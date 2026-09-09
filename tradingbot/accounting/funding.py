"""Funding tahakkuku (USDⓈ-M perpetual) — 00/08/16 UTC settlement'larına hizalı, kaçırılan HER dönem ayrı ayrı uygulanır.

* rate > 0 → LONG öder, SHORT alır; rate < 0 → tersi.
* tutar = qty * mark * rate  (settlement anındaki mark; elimizde yoksa verilen mark)
* rate_lookup(symbol, settlement_dt) → oran ya da None. None dönerse son bilinen oran kullanılır ve olay `estimated=True` işaretlenir.

FUNDING SETTLEMENT V1 (2026-09-09). Eski davranış: çağıran TEK bir anlık oran (`static_rates`,
settlement zamanını yok sayar) ve TEK bir anlık mark veriyordu; kaçırılan 15 settlement'ın hepsine
aynı oran ve aynı mark uygulanıyordu. Üretim defterindeki 29 kapanmış işlem gerçek Binance
`fundingRate` geçmişiyle uzlaştırıldığında kayıtlı funding toplamı +0,067382 USDT, gerçek toplam
+0,004014 USDT çıktı (işlem başına MUTLAK hata toplamı 0,209232 USDT; en kötüsü F00015 STX/USDT
LONG, 15 settlement, +0,144644 karşı +0,040753). Birim/işaret dönüşümü DOĞRUYDU, settlement eşlemesi
1:1'di — kusur yalnızca "hangi oran" sorusundaydı.

Onarım `rate_lookup`'un SÖZLEŞMESİNİ genişletir: artık dönüş değeri skaler bir oran YA DA
`FundingQuote(rate, mark)` olabilir. Böylece her settlement kendi oranını ve o andaki mark'ını
kullanır. Skaler dönüşler (mevcut `static_rates`, testler, `ops/gap.py`) BİREBİR eskisi gibi çalışır.
FAIL-CLOSED semantiği değişmedi: oran gerçekten çözülemiyorsa (son bilinen oran da yoksa) o
settlement ve SONRAKİLERİN hepsi BEKLER, watermark ilerlemez, sessiz sıfır YAZILMAZ.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..core import D, ZERO, FUNDING_HOURS_UTC, from_iso, funding_settlements_between, iso
from .models import Position, PositionSide, dec_or_none, ser

log = logging.getLogger(__name__)

RateLookup = Callable[[str, datetime], "Decimal | float | str | FundingQuote | None"]


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

    def to_dict(self) -> dict:
        return ser(self)


@dataclass(frozen=True)
class FundingQuote:
    """TEK bir settlement için çözülmüş oran (+ o settlement anındaki mark, biliniyorsa).

    `mark=None` → çağıranın verdiği anlık mark kullanılır (eski davranış). `source` yalnız izleme
    içindir, muhasebeye girmez.
    """
    rate: Decimal
    mark: Decimal | None = None
    source: str = ""


def _as_quote(raw: Any) -> "FundingQuote | None":
    """`rate_lookup` dönüşünü normalize eder: None | skaler | FundingQuote | (rate, mark) | {"rate":…}.

    Çözülemeyen/bozuk değer None döner — yani fail-closed yolu işler; ASLA sessizce 0 üretmez.
    """
    if raw is None:
        return None
    if isinstance(raw, FundingQuote):
        return raw
    rate_raw: Any = raw
    mark_raw: Any = None
    source = ""
    if isinstance(raw, Mapping):
        rate_raw, mark_raw, source = raw.get("rate"), raw.get("mark"), str(raw.get("source") or "")
    elif isinstance(raw, (tuple, list)):
        if not raw:
            return None
        rate_raw = raw[0]
        mark_raw = raw[1] if len(raw) > 1 else None
    if rate_raw is None:
        return None
    try:
        rate = D(rate_raw)
    except (InvalidOperation, TypeError, ValueError):
        log.warning("funding oranı çözülemedi (%r) — settlement BEKLETİLİYOR", raw)
        return None
    mark = dec_or_none(mark_raw)
    return FundingQuote(rate=rate, mark=mark if (mark is not None and mark > 0) else None, source=source)


def static_rates(rates: Mapping[str, "Decimal | float | str"]) -> RateLookup:
    """Sembol → sabit oran sözlüğünden lookup üretir (engine'in mevcut funding dict'i için).

    Settlement zamanını YOK SAYAR: tek bir anlık oranı bütün dönemlere uygular. Doğru oran kaynağı
    (`market.funding_rates.FundingRateCache`) elde yokken YEDEK olarak kullanılır — bkz. `chained_rates`.
    """
    def _lookup(symbol: str, _when: datetime):
        v = rates.get(symbol)
        return None if v is None else D(v)
    return _lookup


def chained_rates(*lookups: "RateLookup | None") -> RateLookup:
    """Lookup'ları SIRAYLA dener; ilk None-olmayan cevap kazanır.

    Kullanım: `chained_rates(funding_rates.lookup, static_rates(snapshot))` — önce settlement'ın
    GERÇEK oranı, o yoksa bugünkü anlık oran. Venue erişilemezken davranış BUGÜNKÜYLE birebir aynı
    kalır (yedek devreye girer); erişilebilirken her settlement kendi oranını alır. Bir kaynağın
    istisnası zinciri KESMEZ; hiçbiri cevap veremezse None döner ve `accrue` fail-closed davranır.
    """
    chain = [f for f in lookups if f is not None]

    def _lookup(symbol: str, when: datetime):
        for f in chain:
            try:
                v = f(symbol, when)
            except Exception as exc:  # noqa: BLE001 — kaynak arızası sonraki kaynağa düşer
                log.warning("funding oran kaynağı hata verdi (%s %s): %s", symbol, when, exc)
                continue
            if v is not None:
                return v
        return None
    return _lookup


@dataclass
class FundingSchedule:
    hours_utc: tuple[int, ...] = FUNDING_HOURS_UTC
    fallback_to_last_known: bool = True

    def settlements_due(self, position: Position, now_utc: datetime) -> list[datetime]:
        start_s = position.last_funding_settlement_utc or position.opened_at
        if not start_s:
            return []
        return funding_settlements_between(from_iso(start_s), now_utc, self.hours_utc)

    def accrue(self, position: Position, now_utc: datetime, mark_price, rate_lookup: RateLookup | None) -> list[FundingEvent]:
        """(last_settlement, now] aralığındaki bütün settlement'ları uygular; pozisyonun funding_paid/received alanlarını günceller.
        Dönen olayların `amount` toplamı cüzdana eklenmelidir (çağıran ledger yapar)."""
        due = self.settlements_due(position, now_utc)
        if not due:
            return []
        # Çağıranın verdiği ANLIK mark: yalnız settlement'a ait gerçek mark bilinmediğinde kullanılır.
        fallback_mark = D(mark_price)
        last_rate = dec_or_none(position.meta.get("last_funding_rate"))
        events: list[FundingEvent] = []
        settled_until: datetime | None = None
        for t in due:
            quote = _as_quote(rate_lookup(position.symbol, t)) if rate_lookup is not None else None
            estimated = False
            if quote is None:
                if last_rate is None or not self.fallback_to_last_known:
                    break              # oran bilinmiyor → bu ve sonraki dönemler BEKLER (sessiz kayıp yok); watermark ileri sarılmaz
                rate, estimated = last_rate, True
                mark = fallback_mark
            else:
                rate = quote.rate
                # settlement anındaki mark biliniyorsa O kullanılır; yoksa anlık mark (eski davranış).
                mark = quote.mark if quote.mark is not None else fallback_mark
            if rate == ZERO or position.qty <= 0:
                last_rate, settled_until = rate, t
                continue
            pay = position.qty * mark * rate           # >0: long öder
            amount = -pay if position.side is PositionSide.LONG else pay
            if amount < 0:
                position.funding_paid += -amount
            else:
                position.funding_received += amount
            events.append(FundingEvent(ts=iso(t), symbol=position.symbol, side=position.side.value, rate=rate, mark=mark,
                                       qty=position.qty, amount=amount, estimated=estimated))
            last_rate, settled_until = rate, t
        if settled_until is not None:
            position.last_funding_settlement_utc = iso(settled_until)
        if last_rate is not None:
            position.meta["last_funding_rate"] = format(last_rate, "f")
        return events


__all__ = ["FundingEvent", "FundingQuote", "FundingSchedule", "RateLookup", "chained_rates", "static_rates"]
