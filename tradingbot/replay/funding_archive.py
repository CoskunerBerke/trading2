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
        self.hits = 0
        self.unknown = 0
        self.missing_series: set[str] = set()

    def _load(self, symbol: str) -> tuple[list[int], list[Decimal]]:
        cached = self._series.get(symbol)
        if cached is not None:
            return cached
        ts: list[int] = []
        rates: list[Decimal] = []
        try:
            df = self.store.read(self.market, symbol, "funding")
        except Exception:  # noqa: BLE001 — seri okunamiyorsa "bilinmiyor"dur, sifir DEGIL
            df = None
        if df is None or not len(df):
            self.missing_series.add(symbol)
        else:
            for t, r in zip(df["timestamp"], df["rate"]):
                try:
                    ts.append(int(t))
                    rates.append(Decimal(str(float(r))))
                except (TypeError, ValueError):
                    continue
        out = (ts, rates)
        self._series[symbol] = out
        return out

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
        return {"queries": total, "answered": self.hits, "unknown": self.unknown,
                "complete": self.unknown == 0,
                "answered_fraction": round(self.hits / total, 6) if total else None,
                "missing_series": sorted(self.missing_series),
                "tolerance_ms": self.tolerance_ms,
                "source": "history store: futures/<symbol>/funding (Binance fundingRate arşivi)"}


__all__ = ["ArchiveFundingRates", "DEFAULT_TOLERANCE_MS"]
