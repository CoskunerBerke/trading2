"""REPLAY FUNDING — arsivden okunan GERCEK settlement oranlari, uydurma yok.

Kusur: `replay/engine.py` `ledger2.tick(...)` cagrisina `funding_rate_lookup` HIC vermiyordu.
`FuturesLedgerV2.tick` icin bu parametre istege baglidir; verilmedigi icin funding maliyeti
her replay kapanisinda YAPISAL olarak 0 cikiyordu. Sifir "funding olcuIdu ve sifirdi" degil
"funding hic sorulmadi" demekti ve bu, kapanislari maliyet-sonrasi gibi gosteriyordu. 23
islemlik SOL replay'inde 23 kapanisin 23'u funding=0.0 tasiyordu.

Bu modul bosluğu DAR kapsamda kapatir. Tasarim kurallari:

* Oran YALNIZ arsivlenmis `futures/<sembol>/funding` serisinden gelir. Seri, Binance resmi
  `fundingRate` gecmisidir ve point-in-time'dir — canli bir uc noktaya, yayim gecikmesine
  ya da "su ana kadar her sey yayimlandi" varsayimina BAGLI DEGILDIR.
* Settlement anina `tolerance_ms` icinde satir yoksa lookup `None` doner. `None`, `accrue`
  icin "bilinmiyor"dur; en son bilinen orana DUSMEK bu sinifta KAPALIDIR
  (`FundingSchedule(fallback_to_last_known=False)` ile kullanilmalidir), cunku bir gunluk
  bosluğu son bilinen oranla doldurmak sessiz bir tahmin uretir.
* Cevaplanamayan her sorgu SAYILIR (`unknown`). Cagiran taraf bu sayiyi rapora yazar;
  "funding tam" iddiasi ancak `unknown == 0` ise kurulabilir.

Bilincli sinir: bu modul canli PAPER yolunu DEGISTIRMEZ. Canli funding hattinin kendi
tamamlanma sozlesmesi vardir ve ayri bir surumun konusudur.

SETTLEMENT MARK'I (2026-09-22, `funding_settlement_v2`): defter tutari `qty(t) x mark(t) x oran(t)` ile yazar; mark
settlement satirinin KENDI mark'idir (`settlement_mark`). Arsivde Binance `markPrice` 2023 sonundan once BOSTUR
(olculdu: 2020-2022 satirlarinin 0'inda, 2023'un 6105/34489'unda, 2024+ hepsinde mark var). Mark'i olmayan satir
icin replay motoru ILAN EDILMIS bir vekil verebilir (`mark_proxy`: settlement anindaki arsiv bar acilisi); olay bunu
`mark_basis="BAR_OPEN_PROXY"` ile tasir ve kapsama raporu vekil sayisini AYRI verir. Vekil de yoksa donem BEKLER.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

#: Settlement zamani ile arsiv satirinin damgasi arasinda kabul edilen azami sapma.
#: Binance settlement damgalari saniye altinda kayabilir (ornekte 1600041600003, +3 ms);
#: iki dakika, gercek bir settlement'i yakalamaya yeter ve komsu saati YAKALAMAZ.
DEFAULT_TOLERANCE_MS = 120_000


class ArchiveFundingRates:
    """`(symbol, when) -> Decimal | None` — arsivden okunan settlement orani.

    Seriler tembel yuklenir ve sembol basina bir kez okunur. Sorgu ikili aramadir;
    uzun replay'lerde satir sayisi degil log(n) maliyeti vardir.
    """

    def __init__(self, store: Any, *, market: str = "futures",
                 tolerance_ms: int = DEFAULT_TOLERANCE_MS) -> None:
        self.store = store
        self.market = market
        self.tolerance_ms = int(tolerance_ms)
        self._series: dict[str, tuple[list[int], list[Decimal]]] = {}
        self._marks: dict[str, list[Decimal | None]] = {}
        self.hits = 0
        self.unknown = 0
        self.missing_series: set[str] = set()
        #: Satir mark'i yoksa kullanilan ILAN EDILMIS vekil `(symbol, when) -> Decimal | None` (replay motoru verir).
        self.mark_proxy = None
        self.mark_rows = 0
        self.mark_proxied = 0
        self.mark_missing = 0
        #: settlement mark'ı (satır ya da ilan edilmiş vekil) HİÇ bulunamayan TEKİL settlement'lar (yeniden denemeler sayılmaz)
        self.mark_missing_keys: set[tuple[str, int]] = set()

    def _load(self, symbol: str) -> tuple[list[int], list[Decimal]]:
        cached = self._series.get(symbol)
        if cached is not None:
            return cached
        ts: list[int] = []
        rates: list[Decimal] = []
        marks: list[Decimal | None] = []
        try:
            df = self.store.read(self.market, symbol, "funding")
        except Exception:  # noqa: BLE001 — seri okunamiyorsa "bilinmiyor"dur, sifir DEGIL
            df = None
        if df is None or not len(df):
            self.missing_series.add(symbol)
        else:
            mcol = df["mark"] if "mark" in df.columns else [None] * len(df)
            for t, r, m in zip(df["timestamp"], df["rate"], mcol):
                try:
                    tt, rr = int(t), Decimal(str(float(r)))
                except (TypeError, ValueError):
                    continue
                try:
                    mm = Decimal(str(float(m))) if m is not None and float(m) == float(m) and float(m) > 0 else None
                except (TypeError, ValueError):
                    mm = None
                ts.append(tt)
                rates.append(rr)
                marks.append(mm)
        out = (ts, rates)
        self._series[symbol] = out
        self._marks[symbol] = marks
        return out

    def _index(self, symbol: str, when: datetime) -> int | None:
        """Settlement anina tolerans icinde en yakin satirin indeksi; yoksa None."""
        ts, _ = self._load(symbol)
        if not ts:
            return None
        target = int(when.timestamp() * 1000)
        lo, hi = 0, len(ts) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if ts[mid] < target:
                lo = mid + 1
            else:
                hi = mid
        best = lo
        if lo > 0 and abs(ts[lo - 1] - target) < abs(ts[lo] - target):
            best = lo - 1
        return None if abs(ts[best] - target) > self.tolerance_ms else best

    def _row_mark(self, symbol: str, when: datetime) -> Decimal | None:
        i = self._index(symbol, when)
        if i is None:
            return None
        marks = self._marks.get(symbol) or []
        return marks[i] if i < len(marks) else None

    def settlement_mark(self, symbol: str, when: datetime) -> Decimal | None:
        """Satirin KENDI mark'i; yoksa ilan edilmis vekil (`mark_proxy`); ikisi de yoksa None (donem BEKLER)."""
        m = self._row_mark(symbol, when)
        if m is not None:
            self.mark_rows += 1
            return m
        if self.mark_proxy is not None:
            try:
                px = self.mark_proxy(symbol, when)
            except Exception:  # noqa: BLE001 — vekil okunamazsa donem BEKLER
                px = None
            if px is not None and Decimal(str(px)) > 0:
                self.mark_proxied += 1
                return Decimal(str(px))
        self.mark_missing += 1
        self.mark_missing_keys.add((symbol, int(when.timestamp() * 1000)))
        return None

    def mark_basis(self, symbol: str, when: datetime) -> str:
        return "SETTLEMENT_ROW" if self._row_mark(symbol, when) is not None else "BAR_OPEN_PROXY"

    def rate_at(self, symbol: str, when: datetime) -> Decimal | None:
        """Verilen settlement anindaki oran; tolerans disindaysa `None` (uydurma YOK)."""
        ts, rates = self._load(symbol)
        if not ts:
            self.unknown += 1
            return None
        target = int(when.timestamp() * 1000)
        # ikili arama: `target`a en yakin damga
        lo, hi = 0, len(ts) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if ts[mid] < target:
                lo = mid + 1
            else:
                hi = mid
        best = lo
        if lo > 0 and abs(ts[lo - 1] - target) < abs(ts[lo] - target):
            best = lo - 1
        if abs(ts[best] - target) > self.tolerance_ms:
            self.unknown += 1
            return None
        self.hits += 1
        return rates[best]

    def __call__(self, symbol: str, when: datetime) -> Decimal | None:
        return self.rate_at(symbol, when)

    def coverage(self) -> dict:
        """Kac settlement cevaplandi, kac tanesi bilinmiyordu, hangi seriler eksikti."""
        total = self.hits + self.unknown
        # TAM ancak oran bilinmeyen sorgu YOKSA ve settlement mark'ı bulunamayan (dönemi uygulanamayan) settlement da
        # yoksa: oranı bilinen ama mark'ı olmayan dönem funding'e GİRMEZ ve sonraki dönemleri bekletir (doğrulayıcı bulgusu).
        return {"queries": total, "answered": self.hits, "unknown": self.unknown,
                "complete": self.unknown == 0 and not self.mark_missing_keys,
                "settlements_without_mark": len(self.mark_missing_keys),
                "answered_fraction": round(self.hits / total, 6) if total else None,
                "missing_series": sorted(self.missing_series),
                # settlement mark'i dayanaklari (funding_settlement_v2): satirin kendi mark'i / vekil / yok (bekledi)
                "marks": {"settlement_row": self.mark_rows, "bar_open_proxy": self.mark_proxied, "missing_lookups": self.mark_missing,
                          "missing_settlements": len(self.mark_missing_keys)},
                "tolerance_ms": self.tolerance_ms,
                "source": "history store: futures/<symbol>/funding (Binance fundingRate arşivi)"}


__all__ = ["ArchiveFundingRates", "DEFAULT_TOLERANCE_MS"]
