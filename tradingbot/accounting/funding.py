"""Funding tahakkuku (USDⓈ-M perpetual) — settlement anlarına hizalı, kaçırılan HER dönem ayrı ayrı uygulanır.

* rate > 0 → LONG öder, SHORT alır; rate < 0 → tersi.
* rate_lookup(symbol, settlement_dt) → Decimal|None. None dönerse dönem BEKLER (`fallback_to_last_known=True` ile
  açıkça istenmedikçe; o yolda olay `estimated=True` işaretlenir).

SETTLEMENT SÖZLEŞMESİ (`FUNDING_SETTLEMENT_CONTRACT`, 2026-09-22) — açık pozisyonun tahakkuku (`FundingSchedule.accrue`)
ile kapanmış işlemin geç uzlaştırması (`FuturesLedgerV2.settle_late_funding`) AYNI ekonomik olay için AYNI tutarı yazar;
verinin ne zaman ulaştığı sonucu değiştirmez:

* Tutar = `qty(t) × mark(t) × oran(t)`, işaret yukarıdaki gibi (`settlement_amount`).
* `qty(t)` = settlement anında GERÇEKTEN açık miktar, dolum geçmişinden (`qty_open_at`): `t`den ÖNCEKİ girişler eksi
  `t`den ÖNCEKİ çıkışlar. Aynı an sırası: `t` anında KAPANAN miktar o dönemi ÖDER (settlement'ta pozisyon açıktı);
  `t` anında AÇILAN miktar ödemez (pencere açılıştan SONRAKİ settlement'ları kapsar: `(opened_at, closed_at]`).
  Kısmi kapanış güncel kalan miktarı değil, settlement anındaki miktarı etkiler.
* `mark(t)` = o settlement satırının KENDİ mark'ı (kaynağın `settlement_mark`i). Kaynak mark veremiyorsa dönem BEKLER
  (tahmin/sıfır yazılmaz). Kaynak bir vekil ilan ederse (ör. replay'de settlement anındaki bar açılışı) olay bunu
  `mark_basis` ile taşır — gerçek satır mark'ı gibi damgalanmaz. Yalnız oran veren (settlement mark'ı OLMAYAN) eski
  bir lookup verilirse tikin fiyatı kullanılır ve olay `mark_basis="TICK_MARK"` taşır (üretim yolu bunu KULLANMAZ).
* Oran (`rate_lookup`) GERÇEKLEŞMİŞ settlement oranıdır; bilinmiyorsa dönem ve sonrakiler BEKLER (kronolojik).
* Yinelenmezlik: işlenen anlar kayda (`features.funding_settled_ts`) ve watermark'a (`last_funding_settlement_utc` /
  `features.funding_settled_until`) yazılır; aynı settlement ikinci kez yazılmaz, yeniden başlatma bunu değiştirmez.
* Settlement saatleri kaynağın BUGÜNKÜ sözleşme aralığıdır (`hours_for_symbol`); geçmişte aralık değiştiyse bu
  DOĞRULANMAMIŞTIR — kayıt aralığın nereden geldiğini taşır (`funding_hours_utc`), kapsama bunu gizlemez.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping

from ..core import D, ZERO, FUNDING_HOURS_UTC, from_iso, funding_settlements_between, iso
from .models import Position, PositionSide, dec_or_none, ser

RateLookup = Callable[[str, datetime], "Decimal | float | str | None"]
MarkLookup = Callable[[str, datetime], "Decimal | float | str | None"]

#: Açık ve kapanmış işlemlerin ORTAK settlement sözleşmesinin adı (kayda `features.funding_contract` olarak yazılır).
FUNDING_SETTLEMENT_CONTRACT = "funding_settlement_v2"

#: `mark_basis` değerleri: satırın kendi mark'ı / kaynağın ilan ettiği vekil / yalnız-oran lookup'ında tik fiyatı.
MARK_SETTLEMENT_ROW = "SETTLEMENT_ROW"
MARK_TICK = "TICK_MARK"


@dataclass
class FundingEvent:
    ts: str
    symbol: str
    side: str
    rate: Decimal
    mark: Decimal
    qty: Decimal
    amount: Decimal          # cüzdana etkisi: + alındı, − ödendi
    estimated: bool = False
    mark_basis: str = ""     # SETTLEMENT_ROW | vekil adı | TICK_MARK
    qty_basis: str = ""      # FILLS | NO_FILLS_CURRENT_QTY

    def to_dict(self) -> dict:
        return ser(self)


def qty_open_at(fills: Iterable[Any] | None, t: datetime, fallback) -> tuple[Decimal | None, str]:
    """Settlement anı `t`de GERÇEKTEN açık miktar (sözleşme: modül başı). Dolum yoksa `fallback` (eski/içe aktarılmış
    pozisyon) ve gerekçe `NO_FILLS_CURRENT_QTY`. Zamanı okunamayan dolum varsa miktar BİLİNMİYOR sayılır (None):
    yanlış miktarla tutar yazmak yerine dönem bekler."""
    fl = list(fills or [])
    if not fl:
        return D(fallback), "NO_FILLS_CURRENT_QTY"
    qty = ZERO
    for f in fl:
        try:
            fts = from_iso(str(getattr(f, "ts", "") or ""))
        except (TypeError, ValueError):
            return None, "FILL_TIME_UNREADABLE"
        if fts >= t:
            continue                                  # t anındaki/sonraki dolum t'deki miktarı DEĞİŞTİRMEZ
        q = D(getattr(f, "qty", 0))
        qty = qty + q if str(getattr(f, "kind", "")) == "entry" else qty - q
    return max(qty, ZERO), "FILLS"


def settlement_amount(side, qty, mark, rate) -> Decimal:
    """Tek settlement'ın cüzdan etkisi (+ alındı / − ödendi) — açık ve kapanmış yolun ORTAK formülü."""
    pay = D(qty) * D(mark) * D(rate)
    long = (side is PositionSide.LONG) if isinstance(side, PositionSide) else str(side).upper() in ("LONG", "BUY")
    return -pay if long else pay


def has_settlement_marks(source: Any) -> bool:
    """Kaynak oranla BİRLİKTE settlement mark'ı da veriyor mu (`settlement_mark` özniteliği)?"""
    return source is not None and callable(getattr(source, "settlement_mark", None))


def settlement_mark(source: Any, symbol: str, t: datetime, mark_for: MarkLookup | None = None) -> tuple[Decimal | None, str]:
    """Settlement `t`nin mark'ı ve dayanağı. `mark_for` verilmişse o, yoksa kaynağın `settlement_mark`i. Bilinmiyorsa
    (None, ""). Kaynak `mark_basis(symbol, t)` veriyorsa dayanak oradan (vekil ilanı), yoksa `SETTLEMENT_ROW`."""
    fn = mark_for if mark_for is not None else getattr(source, "settlement_mark", None)
    if fn is None:
        return None, ""
    try:
        m = dec_or_none(fn(symbol, t))
    except Exception:  # noqa: BLE001 — mark okunamazsa dönem BEKLER
        return None, ""
    if m is None or m <= 0:
        return None, ""
    basis_fn = getattr(source, "mark_basis", None)
    try:
        basis = str(basis_fn(symbol, t)) if callable(basis_fn) else MARK_SETTLEMENT_ROW
    except Exception:  # noqa: BLE001
        basis = MARK_SETTLEMENT_ROW
    return m, basis or MARK_SETTLEMENT_ROW


def static_rates(rates: Mapping[str, "Decimal | float | str"]) -> RateLookup:
    """Sembol → sabit oran sözlüğünden YALNIZ-ORAN lookup (settlement mark'ı yok). Anlık oranı her geçmiş settlement'a
    aynen uygular — GERÇEKLEŞMİŞ oran DEĞİLDİR. Üretim defterleri bunu kullanmaz (2026-09-22: gerçekleşmiş oran/mark
    kaynağına bağlandılar); testler ve açık senaryolar içindir."""
    def _lookup(symbol: str, _when: datetime):
        v = rates.get(symbol)
        return None if v is None else D(v)
    return _lookup


@dataclass
class FundingSchedule:
    """Settlement takvimi. `hours_utc` VARSAYILANDIR (Binance USDⓈ-M: 8 saat); sözleşmesi sapan sembol için
    `hours_for_symbol(symbol)` gerçek aralığı döndürür — sekiz saat varsayımı doğrulanmadan yayılmaz
    (`/fapi/v1/fundingInfo` yalnız sapan sembolleri yayımlar; listede olmayan sembol varsayılandadır).
    `hours_for_symbol` None dönerse varsayılan kullanılır; boş demet (`()`) dönerse aralık BİLİNMİYOR demektir
    ve o sembol için hiçbir settlement üretilmez (uydurulmuş dönem yok)."""

    hours_utc: tuple[int, ...] = FUNDING_HOURS_UTC
    fallback_to_last_known: bool = True
    hours_for_symbol: Callable[[str], "tuple[int, ...] | None"] | None = None

    def bind_source(self, source: Any) -> None:
        """Gerçekleşmiş oran/mark kaynağını takvime BAĞLAR: sözleşmenin kendi aralığı (`hours_for`) kullanılır ve
        bilinmeyen oran TAHMİNLE doldurulmaz — dönem BEKLER (bekleyen maliyet olarak görünür, sıfır sayılmaz)."""
        self.hours_for_symbol = source.hours_for if hasattr(source, "hours_for") else None
        self.fallback_to_last_known = False

    def hours_for(self, symbol: str) -> tuple[int, ...]:
        if self.hours_for_symbol is None:
            return self.hours_utc
        try:
            h = self.hours_for_symbol(symbol)
        except Exception:  # noqa: BLE001 — aralık servisi arızası tahakkuku ÇÖKERTMEZ: varsayılana düşülür
            return self.hours_utc
        return self.hours_utc if h is None else tuple(h)

    def settlements_due(self, position: Position, now_utc: datetime) -> list[datetime]:
        start_s = position.last_funding_settlement_utc or position.opened_at
        if not start_s:
            return []
        hours = self.hours_for(position.symbol)
        if not hours:
            return []                      # aralık bilinmiyor: dönem UYDURULMAZ (kapsama eksikliği ayrıca raporlanır)
        return funding_settlements_between(from_iso(start_s), now_utc, hours)

    def accrue(self, position: Position, now_utc: datetime, mark_price, rate_lookup: RateLookup | None) -> list[FundingEvent]:
        """(last_settlement, now] aralığındaki settlement'ları SÖZLEŞMEYE göre uygular (modül başı); pozisyonun
        funding_paid/received alanlarını günceller. Dönen olayların `amount` toplamı cüzdana eklenmelidir (ledger yapar).

        Bir dönem beklerse (oran ya da settlement mark'ı bilinmiyor, miktar okunamıyor) o ve sonrakiler BEKLER,
        watermark ileri sarılmaz ve neden `position.features["funding_pending"]`e yazılır."""
        due = self.settlements_due(position, now_utc)
        if not due:
            return []
        tick_mark = D(mark_price)
        use_source_mark = has_settlement_marks(rate_lookup)
        last_rate = dec_or_none(position.meta.get("last_funding_rate"))
        events: list[FundingEvent] = []
        settled_until: datetime | None = None
        pending: dict | None = None
        for t in due:
            raw = rate_lookup(position.symbol, t) if rate_lookup is not None else None
            estimated = False
            if raw is None:
                if last_rate is None or not self.fallback_to_last_known:
                    pending = {"since": iso(t), "reason": "RATE_UNKNOWN"}
                    break              # oran bilinmiyor → bu ve sonraki dönemler BEKLER (sessiz kayıp yok); watermark ileri sarılmaz
                rate, estimated = last_rate, True
            else:
                rate = D(raw)
            if rate == ZERO:
                last_rate, settled_until = rate, t
                continue
            qty_t, qty_basis = qty_open_at(position.fills, t, position.qty)
            if qty_t is None:
                pending = {"since": iso(t), "reason": qty_basis}
                break
            if qty_t <= 0:
                last_rate, settled_until = rate, t
                continue
            if use_source_mark:
                mark_t, mark_basis = settlement_mark(rate_lookup, position.symbol, t)
                if mark_t is None:
                    pending = {"since": iso(t), "reason": "SETTLEMENT_MARK_MISSING"}
                    break          # settlement mark'ı yok → dönem BEKLER (güncel fiyat vekil YAPILMAZ)
            else:
                mark_t, mark_basis = tick_mark, MARK_TICK
            amount = settlement_amount(position.side, qty_t, mark_t, rate)
            if amount < 0:
                position.funding_paid += -amount
            else:
                position.funding_received += amount
            events.append(FundingEvent(ts=iso(t), symbol=position.symbol, side=position.side.value, rate=rate, mark=mark_t,
                                       qty=qty_t, amount=amount, estimated=estimated, mark_basis=mark_basis,
                                       qty_basis=qty_basis))
            last_rate, settled_until = rate, t
        if settled_until is not None:
            position.last_funding_settlement_utc = iso(settled_until)
        if last_rate is not None:
            position.meta["last_funding_rate"] = format(last_rate, "f")
        if pending is not None:
            position.features["funding_pending"] = pending
        else:
            position.features.pop("funding_pending", None)
        return events


def funding_coverage(*, opened_at: str, until: str, settled_until: str | None, hours_utc: Any,
                     settled_ts: Any = None, rate_source: str = "") -> dict[str, Any]:
    """Bir pozisyon/işlem için funding KAPSAMASI — AĞ YOK, yalnız kayıttaki bilgiler (`funding_hours_utc`,
    `funding_settled_ts`, watermark). `complete=False` ise o işlemin funding maliyeti ÖLÇÜLMEMİŞTİR (bekleyen maliyet);
    sıfır sanılmamalıdır. `settled` GERÇEKTEN uygulanan dönemlerden sayılır; o liste yoksa watermark'tan (eski kayıt)."""
    hours = tuple(int(h) for h in (hours_utc or ()))
    out: dict[str, Any] = {"interval_known": bool(hours), "hours_utc": list(hours), "settled_until": settled_until,
                           "rate_source": rate_source or "none", "contract": FUNDING_SETTLEMENT_CONTRACT}
    if not hours:
        return {**out, "due": None, "settled": None, "missing": None, "complete": False, "reason": "INTERVAL_UNKNOWN"}
    try:
        o, u = from_iso(str(opened_at)), from_iso(str(until))
    except (ValueError, TypeError):
        return {**out, "due": None, "settled": None, "missing": None, "complete": False, "reason": "TIME_UNREADABLE"}
    due_ts = funding_settlements_between(o, u, hours)
    if isinstance(settled_ts, list):
        applied = {str(t) for t in settled_ts}
        try:
            w = from_iso(str(settled_until)) if settled_until else o
        except (ValueError, TypeError):
            w = o
        # Watermark'a kadar olan dönemler de mutabıktır (oranı SIFIR ya da miktarı 0 olan dönem olay üretmez).
        settled = sum(1 for t in due_ts if iso(t) in applied or t <= w)
        out["source"] = "applied_settlements"
    else:                                             # eski kayıt: yalnız watermark var (alt sınır)
        try:
            w = from_iso(str(settled_until)) if settled_until else o
        except (ValueError, TypeError):
            return {**out, "due": len(due_ts), "settled": None, "missing": None, "complete": False, "reason": "TIME_UNREADABLE"}
        settled = len(funding_settlements_between(o, w, hours))
        out["source"] = "watermark"
    missing = max(0, len(due_ts) - settled)
    return {**out, "due": len(due_ts), "settled": settled, "missing": missing, "complete": missing == 0,
            "reason": "" if missing == 0 else "RATES_MISSING"}


__all__ = ["FUNDING_SETTLEMENT_CONTRACT", "MARK_SETTLEMENT_ROW", "MARK_TICK", "FundingEvent", "FundingSchedule",
           "MarkLookup", "RateLookup", "funding_coverage", "has_settlement_marks", "qty_open_at", "settlement_amount",
           "settlement_mark", "static_rates"]
