"""`StoreProvider` — araştırma deposundan AĞSIZ bar ve fonlama (§3.5; P1b) + mühürlü okuma (§2.4, `DATA_MOVING`).

* `.klines(symbol, interval, limit, start_ms, end_ms)`: `signal_lab.ArchiveProvider` ile aynı sözleşme — `start ≤
  açılış ≤ end` satırları, sıralı, en çok `limit` satır; 7 sütun (`timestamp, open, high, low, close, volume,
  close_time`) + `is_closed` (`close_time < şimdi`). `wide=True` taker alanlarını da verir (`quote_volume, trades,
  taker_buy_base, taker_buy_quote`). Okuma ay parçası ay parçası ilerler, `limit` dolunca durur (bütün seri belleğe
  alınmaz).
* `.funding_frame(symbol, start_ms, end_ms)`: `[timestamp, rate, mark]` — serideki GERÇEK uzlaşma zaman damgaları
  (sabit 8 saatlik ızgara yok; §3.2 madde 5).
* **Parite kapısı** (§3.5): `archive_unverified` satırlar varsayılan olarak VERİLMEZ (`include_unverified=False`);
  REST kaynaklı kuyruk barları verilir. Mühürlü laboratuvarlar bu aşamada `StoreProvider`'a GEÇİRİLMEZ (fixture
  paritesi testtedir); o zamana kadar `--cache …/research/archive_cache --offline` ile çalışırlar.
* **Mühürlü okuma** (`SealedReader`): gece birimi depoyu yalnız son TAMAMLANMIŞ veri çalıştırmasının `data_seal`'ında
  listelenen ay parçalarıyla okur. Mühür dosyası (`store/_seal/<seal>.json.gz`) `data_status.json`'daki mühürle
  tutmalıdır; okunan parçanın baytlarının sha256'sı mühürdekinden farklıysa (o sırada ilk doldurma yazıyorsa) o seri
  `DataMoving` ile atlanır (çağıran `DATA_MOVING` yazar; çalıştırma durmaz). Mühürden sonra eklenen aylar görünmez.

Ağ yoktur; bu modül `datastore`'u import etmez.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Iterator

import pandas as pd

from .paths import EnginePaths, read_json_gz
from .store import FUNDING, SRC_COL, SRC_UNVERIFIED, ResearchStore, cols_for, month_bounds_ym

BASE_COLS = ["timestamp", "open", "high", "low", "close", "volume", "close_time"]
WIDE_COLS = BASE_COLS + ["quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]
DATA_MOVING = "DATA_MOVING"


class DataMoving(RuntimeError):
    """Okunan parça mühürdekiyle aynı değil (ya da mühür tutarsız): seri bu gece atlanır."""

    def __init__(self, series: str, ym: str, reason: str):
        super().__init__(f"{DATA_MOVING}: {series} {ym}: {reason}")
        self.series, self.ym, self.reason = series, ym, reason


def raw_symbol(symbol: str) -> str:
    """'BTC/USDT' | 'BTC/USDT:USDT' | 'BTCUSDT' → 'BTCUSDT'."""
    return str(symbol).split(":")[0].replace("/", "").upper()


class SealedReader:
    """Yalnız mühürde listelenen parçaları, sha256 denetimiyle okuyan görünüm."""

    def __init__(self, store: ResearchStore, listing: dict[str, dict[str, str]], seal: str) -> None:
        self.store, self.listing, self.seal = store, listing, seal
        self.reads = 0

    @classmethod
    def from_status(cls, paths: EnginePaths, store: ResearchStore | None = None) -> "SealedReader":
        store = store or ResearchStore.for_paths(paths)
        try:
            with open(paths.data_status, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as exc:
            raise DataMoving("*", "", f"data_status okunamadı: {exc}") from exc
        seal, rel = (d or {}).get("data_seal"), (d or {}).get("seal_file")
        if not seal or not rel:
            raise DataMoving("*", "", "tamamlanmış veri çalıştırması yok (mühür yok)")
        try:
            doc = read_json_gz(paths.research / rel)
        except (OSError, ValueError) as exc:
            raise DataMoving("*", "", f"mühür dosyası okunamadı: {exc}") from exc
        parts = [tuple(x) for x in doc.get("parts") or []]
        if ResearchStore.seal_of(parts) != seal:
            raise DataMoving("*", "", "mühür dosyası data_status'taki mühürle tutmuyor")
        listing: dict[str, dict[str, str]] = {}
        for s, ym, sha in parts:
            listing.setdefault(str(s), {})[str(ym)] = str(sha)
        return cls(store, listing, seal)

    def iter_parts(self, market: str, symbol: str, timeframe: str, since_ms: int | None = None,
                   until_ms: int | None = None, *, keep_src: bool = False) -> Iterator[tuple[str, pd.DataFrame]]:
        key = self.store.series_key(market, symbol, timeframe)
        cols = cols_for(timeframe)
        for ym, sha in sorted((self.listing.get(key) or {}).items()):
            a, b = month_bounds_ym(ym)
            if (until_ms is not None and a > until_ms) or (since_ms is not None and b <= since_ms):
                continue
            y, mo = (int(x) for x in ym.split("/"))
            p = self.store.part_path(market, symbol, timeframe, y, mo)
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                raise DataMoving(key, ym, f"parça okunamadı: {exc}") from exc
            self.reads += 1
            if hashlib.sha256(data).hexdigest() != sha:
                raise DataMoving(key, ym, "parça mühürden sonra değişti")
            df = self.store._shape(self.store._parse_part(data, p.name), cols, keep_src)
            if since_ms is not None:
                df = df[df["timestamp"] >= since_ms]
            if until_ms is not None:
                df = df[df["timestamp"] <= until_ms]
            yield ym, df.reset_index(drop=True)


class StoreProvider:
    """Ağsız bar sağlayıcısı. `source` bir `ResearchStore` ya da `SealedReader`'dır."""

    name = "research_store"
    max_kline_limit = 1500

    def __init__(self, source: ResearchStore | SealedReader, *, market: str = "futures", wide: bool = False,
                 include_unverified: bool = False, clock_ms: Callable[[], int] | None = None) -> None:
        self.source = source
        self.market = market
        self.market_type = market
        self.wide = bool(wide)
        self.include_unverified = bool(include_unverified)
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def _parts(self, symbol: str, kind: str, start: int | None, end: int | None) -> Iterator[pd.DataFrame]:
        for _ym, df in self.source.iter_parts(self.market, raw_symbol(symbol), kind, start, end, keep_src=True):
            if not self.include_unverified:
                df = df[df[SRC_COL] != SRC_UNVERIFIED]
            if len(df):
                yield df

    def klines(self, symbol: str, interval: str, limit: int = 1500, start_ms: int | None = None,
               end_ms: int | None = None) -> pd.DataFrame:
        cols = WIDE_COLS if self.wide else BASE_COLS
        now = int(self.clock_ms())
        start = int(start_ms or 0)
        end = int(end_ms if end_ms is not None else now)
        got: list[pd.DataFrame] = []
        n = 0
        for df in self._parts(symbol, interval, start, end):
            got.append(df[cols])
            n += len(df)
            if n >= limit:
                break
        if not got:
            return pd.DataFrame(columns=cols + ["is_closed"])
        out = pd.concat(got, ignore_index=True).drop_duplicates("timestamp", keep="last")
        out = out.sort_values("timestamp", kind="mergesort").head(int(limit)).reset_index(drop=True)
        out["is_closed"] = out["close_time"] < now
        return out

    def funding_frame(self, symbol: str, start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
        cols = cols_for(FUNDING)
        got = [df[cols] for df in self._parts(symbol, FUNDING, start_ms, end_ms)]
        if not got:
            return pd.DataFrame(columns=cols)
        out = pd.concat(got, ignore_index=True).drop_duplicates("timestamp", keep="last")
        return out.sort_values("timestamp", kind="mergesort").reset_index(drop=True)


def read_sealed(reader: SealedReader, market: str, symbol: str, timeframe: str, since_ms: int | None = None,
                until_ms: int | None = None) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Gece birimi yardımcısı: (çerçeve, durum). Parça değişmişse (None, {"status": "DATA_MOVING", ...})."""
    try:
        frames = [df for _, df in reader.iter_parts(market, symbol, timeframe, since_ms, until_ms) if len(df)]
    except DataMoving as exc:
        return None, {"status": DATA_MOVING, "series": exc.series, "ym": exc.ym, "reason": exc.reason}
    cols = cols_for(timeframe)
    df = (pd.concat(frames, ignore_index=True).sort_values("timestamp", kind="mergesort").reset_index(drop=True)
          if frames else pd.DataFrame(columns=cols))
    return df, {"status": "OK", "rows": int(len(df)), "seal": reader.seal}


__all__ = ["BASE_COLS", "DATA_MOVING", "DataMoving", "SealedReader", "StoreProvider", "WIDE_COLS", "raw_symbol",
           "read_sealed"]
