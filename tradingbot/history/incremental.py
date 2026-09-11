"""ARTIMLI ARSIV GUNCELLEMESI — yalniz YENI KAPANMIS barlar.

`HistoryCollector` tam kapsama icindir: aylik arsiv zip'lerini tarar, listelemeden bugune
her boslugu doldurur ve on sembolde ~2,5 saat surer. Calisan bir worker'in ihtiyaci bu
degildir: elinde zaten bir arsiv vardir ve yalnizca son bardan SONRA kapanmis barlari
eklemesi gerekir. Bu modul o dar isi yapar.

Kurallar:

* **Baslangic manifestten gelir.** `last_ts_ms + step`ten once hicbir sey istenmez; butun
  gecmis yeniden indirilmez. Tabani olmayan seri ATLANIR — ilk kapsama `history-collect`in
  isidir ve sessizce burada yapilmaz.
* **Yalnizca KAPANMIS bar yazilir.** Acilis damgasi `t` olan bar `t + step`te kapanir;
  `now_ms < t + step` ise satir DUSURULUR. Acik bar yazmak, karar yolunda yariya kadar
  olusmus bir mumu tamamlanmis gibi gostermek demektir.
* **Zaman UYDURULMAZ.** Yalnizca venue'nun dondurdugu satirlar yazilir. Cevap bossa seri
  "degismedi" sayilir; damga ileri TASINMAZ. "Guncel gorunsun" diye bar uretmek yoktur.
* **Hata seriyi bozmaz.** Bir sembolun istegi patlarsa o seri `error` ile isaretlenir,
  arsiv DOKUNULMAZ kalir ve digerleri devam eder. Kaynak duzeldiginde bir sonraki tur
  ayni yerden devam eder (durum manifestte, bellekte degil).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..market.providers import tf_ms

log = logging.getLogger(__name__)

#: Tek istekte istenecek azami bar. Binance klines tavani 1500; 1000 guvenli ve yeterli
#: (4h seride 166 gun, 15m seride 10 gun geriye tek istekle yetisir).
DEFAULT_LIMIT = 1000

#: Bir guncelleme turunda yapilacak azami istek. Uretimde tur basina calistigi icin
#: sinir bilincli olarak dardir: gecikmis bir arsiv birkac turda yakalanir, tek turda
#: rate-limit yakmaz.
DEFAULT_MAX_REQUESTS = 24


@dataclass
class SeriesResult:
    symbol: str
    timeframe: str
    status: str                      # advanced | unchanged | skipped_no_baseline | error
    rows_new: int = 0
    last_ts_before: int | None = None
    last_ts_after: int | None = None
    error: str = ""

    @property
    def advanced(self) -> bool:
        return self.status == "advanced" and self.rows_new > 0

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "timeframe": self.timeframe, "status": self.status,
                "rows_new": self.rows_new, "last_ts_before": self.last_ts_before,
                "last_ts_after": self.last_ts_after, "error": self.error}


@dataclass
class UpdateReport:
    results: list[SeriesResult] = field(default_factory=list)
    requests: int = 0
    truncated: bool = False          # istek tavanina takildi -> kalan seriler bir sonraki turda

    @property
    def advanced(self) -> bool:
        return any(r.advanced for r in self.results)

    @property
    def rows_new(self) -> int:
        return sum(r.rows_new for r in self.results)

    @property
    def errors(self) -> list[SeriesResult]:
        return [r for r in self.results if r.status == "error"]

    def to_dict(self) -> dict:
        return {"advanced": self.advanced, "rows_new": self.rows_new, "requests": self.requests,
                "truncated": self.truncated, "series": len(self.results),
                "errors": {f"{r.symbol} {r.timeframe}": r.error for r in self.errors},
                "results": [r.to_dict() for r in self.results]}


def closed_only(df, timeframe: str, now_ms: int):
    """Yalniz KAPANMIS barlar. Acilisi `t` olan bar `t + step`te kapanir."""
    if df is None or not len(df):
        return df
    step = tf_ms(timeframe)
    return df[df["timestamp"].astype("int64") + step <= int(now_ms)]


class IncrementalUpdater:
    """Arsivi yalnizca yeni kapanmis barlarla ilerletir. Durum manifestte tutulur."""

    def __init__(self, store: Any, provider: Any, *, market: str = "futures",
                 limit: int = DEFAULT_LIMIT, max_requests: int = DEFAULT_MAX_REQUESTS) -> None:
        self.store = store
        self.provider = provider
        self.market = market
        self.limit = int(limit)
        self.max_requests = int(max_requests)

    def update_series(self, symbol: str, timeframe: str, *, now_ms: int) -> SeriesResult:
        m = self.store.manifest(self.market, symbol, timeframe)
        before = int(m.last_ts_ms) if m.last_ts_ms else None
        if not before:
            # Taban yok: ilk kapsama `history-collect`in isidir. Burada sessizce yillar
            # indirilmez — tur icinde saatler suren bir is baslatmak kabul edilemez.
            return SeriesResult(symbol, timeframe, "skipped_no_baseline")
        step = tf_ms(timeframe)
        start = before + step
        if start + step > int(now_ms):
            return SeriesResult(symbol, timeframe, "unchanged", last_ts_before=before, last_ts_after=before)
        try:
            df = self.provider.klines(symbol, timeframe, limit=self.limit, start_ms=start)
        except Exception as exc:  # noqa: BLE001 — ag/limit hatasi arsivi BOZMAZ
            return SeriesResult(symbol, timeframe, "error", last_ts_before=before,
                                last_ts_after=before, error=f"{type(exc).__name__}: {exc}"[:200])
        df = closed_only(df, timeframe, now_ms)
        if df is None or not len(df):
            # Cevap bos: "degismedi". Damga ILERLETILMEZ.
            return SeriesResult(symbol, timeframe, "unchanged", last_ts_before=before, last_ts_after=before)
        res = self.store.write(self.market, symbol, timeframe, df,
                               source="binance_rest_incremental", chunk_id=f"inc:{start}")
        after = int(self.store.manifest(self.market, symbol, timeframe).last_ts_ms or before)
        return SeriesResult(symbol, timeframe, "advanced", rows_new=int(res.get("rows_new") or 0),
                            last_ts_before=before, last_ts_after=after)

    def update(self, symbols: Iterable[str], timeframes: Iterable[str], *, now_ms: int) -> UpdateReport:
        """Butun (sembol, tf) ciftlerini sirayla ilerletir; istek tavaninda durur."""
        rep = UpdateReport()
        for sym in symbols:
            for tf in timeframes:
                if rep.requests >= self.max_requests:
                    rep.truncated = True
                    return rep
                r = self.update_series(sym, tf, now_ms=now_ms)
                if r.status in ("advanced", "error"):
                    rep.requests += 1
                rep.results.append(r)
                if r.status == "advanced":
                    log.info("arşiv ilerledi: %s %s +%d satır", sym, tf, r.rows_new)
                elif r.status == "error":
                    log.warning("arşiv güncellenemedi: %s %s — %s (arşiv dokunulmadı)", sym, tf, r.error)
        return rep


__all__ = ["DEFAULT_LIMIT", "DEFAULT_MAX_REQUESTS", "IncrementalUpdater", "SeriesResult",
           "UpdateReport", "closed_only"]
