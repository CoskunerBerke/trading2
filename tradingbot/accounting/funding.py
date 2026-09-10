"""Funding tahakkuku (USDⓈ-M perpetual) — settlement zamanları VENUE'DAN gelir, kaçırılan HER dönem ayrı ayrı uygulanır.

* rate > 0 → LONG öder, SHORT alır; rate < 0 → tersi.
* tutar = qty * mark * rate  (settlement anındaki mark; elimizde yoksa verilen mark)
* rate_lookup(symbol, settlement_dt) → alıntı ya da None. **Yalnız `verified=True` bir alıntı dönemi
  KAPATIR** (V2, `require_verified`); doğrulanmamış değer ya da None → dönem BEKLER, watermark durur.
* Settlement zamanları `settlement_source` ile venue kayıtlarından gelir; `hours_utc` (00/08/16)
  yalnız o kaynak verilmediğinde kullanılan bir YEDEKTİR ve üretim yolunda kullanılmaz.

FUNDING SETTLEMENT V1 (2026-09-09). Eski davranış: çağıran TEK bir anlık oran (`static_rates`,
settlement zamanını yok sayar) ve TEK bir anlık mark veriyordu; kaçırılan 15 settlement'ın hepsine
aynı oran ve aynı mark uygulanıyordu. Üretim defterindeki 29 kapanmış işlem gerçek Binance
`fundingRate` geçmişiyle uzlaştırıldığında kayıtlı funding toplamı +0,067382 USDT, gerçek toplam
+0,004014 USDT çıktı (işlem başına MUTLAK hata toplamı 0,209232 USDT; en kötüsü F00015 STX/USDT
LONG, 15 settlement, +0,144644 karşı +0,040753). Birim/işaret dönüşümü DOĞRUYDU, settlement eşlemesi
1:1'di — kusur yalnızca "hangi oran" sorusundaydı.

Onarım `rate_lookup`'un SÖZLEŞMESİNİ genişletir: dönüş değeri skaler bir oran YA DA
`FundingQuote(rate, mark, verified=...)` olabilir. **V2 uyarısı:** skaler dönüşler artık
doğrulanmamış sayılır ve hiçbir dönemi kapatamaz; `ops/gap.py` ve replay harness'leri bu yüzden
açıkça `verified=True` bildirir. Fail-closed: çözülemeyen settlement ve SONRAKİLERİN hepsi BEKLER,
watermark ilerlemez, sessiz sıfır YAZILMAZ — ama bu garanti pozisyon AÇIKKEN geçerlidir; kapanışta
çözülememiş dönem `TradeRecord.funding_pending_settlements` ile KAYDA GEÇER (uydurma oran yazılmaz).
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
    verified: bool = False   # oran, TAM O settlement için kaynaktan doğrulandı mı
    zero_rate: bool = False  # kaynaktan DOĞRULANMIŞ sıfır oran (eksik veri varsayılanı DEĞİL)

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
    #: Bu oran, TAM O settlement zaman damgası için KAYNAKTAN alındı mı? Yalnız `True` olan bir
    #: alıntı watermark'ı ilerletebilir. Skaler/anlık/tahmini değerler `False` kalır.
    verified: bool = False


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
    verified = False
    if isinstance(raw, Mapping):
        rate_raw, mark_raw, source = raw.get("rate"), raw.get("mark"), str(raw.get("source") or "")
        verified = bool(raw.get("verified", False))
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
    return FundingQuote(rate=rate, mark=mark if (mark is not None and mark > 0) else None, source=source,
                        verified=verified)


def static_rates(rates: Mapping[str, "Decimal | float | str"]) -> RateLookup:
    """Sembol → sabit oran sözlüğünden lookup üretir (engine'in mevcut funding dict'i için).

    Settlement zamanını YOK SAYAR ve DOĞRULANMAMIŞ alıntı üretir; `require_verified=True` altında
    hiçbir dönemi KAPATAMAZ. Üretim yolunda kullanılmaz — doğru kaynak `FundingRateCache`'tir.
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


#: Herhangi bir USDⓈ-M sözleşmesinin GÖRÜLEN en kısa funding aralığı. Yalnız "bu pencerede
#: settlement OLABİLİR mi?" sorusuna cevap verir; settlement zamanlarını BELİRLEMEZ.
MIN_FUNDING_INTERVAL_H = 1

#: `Position.meta` altında son funding değerlendirmesinin durduğu anahtar. Pozisyona yazılır,
#: paylaşılan `FundingSchedule` örneğine DEĞİL: kapanış kaydı başka bir sembolün sayacını
#: devralamasın diye (bkz. `FundingSchedule._record_eval`).
FUNDING_EVAL_KEY = "funding_eval"


@dataclass
class FundingSchedule:
    """Settlement takvimi + tahakkuk.

    FUNDING SETTLEMENT V2 (2026-09-10) — bağımsız incelemenin D1/D2/D4 bulguları:

    * **D2 — sabit 8 saat varsayımı KALDIRILDI.** `hours_utc` artık yalnız `settlement_source`
      verilmediğinde kullanılan bir yedektir. Üretimde `settlement_source`, venue'nun GERÇEKTEN
      yayımladığı settlement zamanlarını döndürür (`FundingRateCache.settlements_in`). Bu botun
      işlem gördüğü 34 sembolün 10'u 8 saatlik takvimde değildir (9'u 4 saat, NVDA 1 saat);
      grid varsayımı o sembollerde dönemlerin yarısını hiç sormuyordu.
    * **D1 — DOĞRULANMAMIŞ oran artık watermark'ı İLERLETEMEZ.** Eskiden `rate_lookup` None
      dönünce `meta.last_funding_rate` ile tahmini tahakkuk yapılıyor ve dönem KAPATILIYORDU.
      60 sn'lik çıkış monitörü settlement sınırına 15 dk'lık turdan önce vardığı için gerçek oran
      hiç sorulmadan olay tüketiliyordu (ölçüldü: 36,1× hata). Artık yalnız `verified=True` bir
      alıntı dönemi kapatır; aksi hâlde dönem BEKLER ve bir sonraki tur gerçek oranla kapatır.
      Çıkış monitörü ile tur böylece AYNI kuralı uygular.
    * **D4 — kaynaktan doğrulanmış SIFIR oran ile eksik verinin varsayılan sıfırı AYRIDIR.**
      Doğrulanmış sıfır artık `amount=0, zero_rate=True, verified=True` bir OLAY üretir: dönem
      kapanır ve kayda geçer. Doğrulanmamış hiçbir şey — `meta.last_funding_rate == "0.0"` dahil —
      dönemi kapatamaz.
    """

    hours_utc: tuple[int, ...] = FUNDING_HOURS_UTC
    fallback_to_last_known: bool = True
    #: `(symbol, start, end) -> [settlement zamanları]`. Verilirse GERÇEK venue settlement'ları
    #: kullanılır ve `hours_utc` hiç okunmaz.
    settlement_source: "Callable[[str, datetime, datetime], list[datetime]] | None" = None
    #: `True` (varsayılan): yalnız doğrulanmış alıntı dönemi kapatır. Yalnız tarihsel/deterministik
    #: yeniden oynatma yollarında kapatılabilir.
    require_verified: bool = True
    #: `(symbol, start, end) -> bool`. Verilirse "bu pencerenin BÜTÜN settlement'ları biliniyor mu"
    #: sorusu KAYNAĞA sorulur. Verilmezse takvim `hours_utc`'ten HESAPLANIR, yani kapsama yapısı
    #: gereği tamdır ve boşluk raporlanmaz.
    coverage_source: "Callable[[str, datetime, datetime], bool] | None" = None
    #: Son `accrue()` çağrısında oran doğrulanamadığı için BEKLEYEN dönem sayısı (yalnız gözlem).
    #: Kapanış kaydı bunu POZİSYONDAN okur, buradan DEĞİL — bkz. `_record_eval`.
    pending_settlements: int = 0

    def window_start(self, position: Position) -> "datetime | None":
        start_s = position.last_funding_settlement_utc or position.opened_at
        return from_iso(start_s) if start_s else None

    def coverage_complete(self, position: Position, now_utc: datetime) -> bool:
        """Bu pozisyonun kalan funding penceresindeki BÜTÜN settlement'lar biliniyor mu?

        `settlements_due`'nun boş dönmesi tek başına "settlement yok" DEMEK DEĞİLDİR: kaynak
        kapsamayı bilmiyorsa da boş döner. Bu ayrımı yapan tek yer burasıdır.
        """
        if self.coverage_source is None:
            return True                  # grid: takvim hesaplanır, kapsama yapısı gereği tamdır
        start = self.window_start(position)
        if start is None or start >= now_utc:
            return True                  # örtülecek pencere yok
        try:
            return bool(self.coverage_source(position.symbol, start, now_utc))
        except Exception as exc:  # noqa: BLE001 — kaynak arızası FAIL-CLOSED: kapsama BİLİNMİYOR
            log.warning("funding kapsama kaynağı hata verdi (%s): %s — kapsama EKSİK sayılıyor",
                        position.symbol, exc)
            return False

    def _record_eval(self, position: Position, now_utc: datetime, pending: int) -> None:
        """Bu tahakkuk denemesinin sonucunu POZİSYONA yazar.

        Durum paylaşılan `FundingSchedule` örneğinde DEĞİL pozisyonda durur: `close_manual` /
        `close_partial` gibi tahakkuk ÇAĞIRMAYAN kapanış yolları başka bir sembolün sayacını
        devralamaz. `pending` BİLİNEN ama çözülemeyen dönem sayısıdır; `coverage_gap` ise
        sayının BİLİNMEDİĞİNİ söyler — bu iki durum birbirinin yerine geçmez.
        """
        position.meta[FUNDING_EVAL_KEY] = {
            "at": iso(now_utc), "pending": int(pending),
            "coverage_gap": not self.coverage_complete(position, now_utc)}

    def settlements_due(self, position: Position, now_utc: datetime) -> list[datetime]:
        start = self.window_start(position)
        if start is None:
            return []
        if self.settlement_source is not None:
            try:
                got = list(self.settlement_source(position.symbol, start, now_utc))
            except Exception as exc:  # noqa: BLE001 — kaynak arızası grid'e DÜŞMEZ; fail-closed
                log.warning("settlement kaynağı hata verdi (%s): %s — dönem BEKLETİLİYOR",
                            position.symbol, exc)
                return []
            return sorted(t for t in got if start < t <= now_utc)
        return funding_settlements_between(start, now_utc, self.hours_utc)

    def accrue(self, position: Position, now_utc: datetime, mark_price, rate_lookup: RateLookup | None) -> list[FundingEvent]:
        """(last_settlement, now] aralığındaki bütün settlement'ları uygular; pozisyonun funding_paid/received alanlarını günceller.
        Dönen olayların `amount` toplamı cüzdana eklenmelidir (çağıran ledger yapar)."""
        due = self.settlements_due(position, now_utc)
        self.pending_settlements = 0
        if not due:
            # "Kontrol edildi, settlement yok" ile "kapsama bilinmiyor" AYNI boş listeyi üretir;
            # ikisini `_record_eval` ayırır ve kapanış kaydı o ayrımı taşır.
            self._record_eval(position, now_utc, 0)
            return []
        # Çağıranın verdiği ANLIK mark: yalnız settlement'a ait gerçek mark bilinmediğinde kullanılır.
        fallback_mark = D(mark_price)
        last_rate = dec_or_none(position.meta.get("last_funding_rate"))
        events: list[FundingEvent] = []
        settled_until: datetime | None = None
        self.pending_settlements = 0
        for _i, t in enumerate(due):
            quote = _as_quote(rate_lookup(position.symbol, t)) if rate_lookup is not None else None
            estimated = False
            if quote is None:
                if self.require_verified or last_rate is None or not self.fallback_to_last_known:
                    # Oran bilinmiyor → bu ve SONRAKİ dönemler BEKLER. Watermark ilerlemez, sessiz
                    # sıfır yazılmaz, olay KAYBOLMAZ; bir sonraki tur gerçek oranla kapatır.
                    self.pending_settlements = len(due) - _i     # bu ve SONRAKI donemler bekliyor
                    log.warning("%s %s icin funding orani cozulemedi — bu ve sonraki %d donem BEKLIYOR "
                                "(watermark %s)", position.symbol, iso(t), self.pending_settlements,
                                position.last_funding_settlement_utc)
                    break
                rate, estimated = last_rate, True
                mark = fallback_mark
            elif self.require_verified and not quote.verified:
                # D1: doğrulanmamış (anlık/tahmini) oran dönemi KAPATAMAZ. Çıkış monitörünün
                # 60 sn'lik döngüsü, turun 15 dk'da bir aldığı gerçek oranı çalamaz.
                self.pending_settlements = len(due) - _i     # bu ve SONRAKI donemler bekliyor
                log.warning("%s %s icin oran DOGRULANMAMIS (kaynak=%r) — bu ve sonraki %d donem "
                            "BEKLIYOR (watermark %s)", position.symbol, iso(t), quote.source,
                            self.pending_settlements, position.last_funding_settlement_utc)
                break
            else:
                rate = quote.rate
                # settlement anındaki mark biliniyorsa O kullanılır; yoksa anlık mark (eski davranış).
                mark = quote.mark if quote.mark is not None else fallback_mark
            verified = bool(quote is not None and quote.verified)
            if position.qty <= 0:
                last_rate, settled_until = rate, t
                continue
            if rate == ZERO:
                # D4: DOĞRULANMIŞ sıfır oran gerçek bir olaydır — dönem kapanır ve KAYDA GEÇER.
                # Doğrulanmamış sıfır buraya asla ulaşamaz (yukarıda break edilir).
                events.append(FundingEvent(ts=iso(t), symbol=position.symbol, side=position.side.value,
                                           rate=ZERO, mark=(quote.mark if (quote and quote.mark is not None) else fallback_mark),
                                           qty=position.qty, amount=ZERO, estimated=estimated,
                                           verified=verified, zero_rate=True))
                last_rate, settled_until = rate, t
                continue
            pay = position.qty * mark * rate           # >0: long öder
            amount = -pay if position.side is PositionSide.LONG else pay
            if amount < 0:
                position.funding_paid += -amount
            else:
                position.funding_received += amount
            events.append(FundingEvent(ts=iso(t), symbol=position.symbol, side=position.side.value, rate=rate, mark=mark,
                                       qty=position.qty, amount=amount, estimated=estimated, verified=verified))
            last_rate, settled_until = rate, t
        if settled_until is not None:
            position.last_funding_settlement_utc = iso(settled_until)
        if last_rate is not None:
            position.meta["last_funding_rate"] = format(last_rate, "f")
        # Watermark ilerledikten SONRA değerlendirilir: kalan pencere (settled_until, now]'dur.
        self._record_eval(position, now_utc, self.pending_settlements)
        return events


__all__ = ["FUNDING_EVAL_KEY", "MIN_FUNDING_INTERVAL_H", "FundingEvent", "FundingQuote",
           "FundingSchedule", "RateLookup", "chained_rates", "static_rates"]
