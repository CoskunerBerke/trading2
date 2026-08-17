"""OHLCV veri katmanı: ccxt (public, API key gerekmez) + CSV önbellek."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

TIMEFRAME_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000,
}
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def bars_per_year(timeframe: str) -> float:
    return 365.0 * 86_400_000 / TIMEFRAME_MS[timeframe]


def _cache_file(cache_dir: Path, exchange: str, symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("/", "-")
    return cache_dir / f"{exchange}_{safe}_{timeframe}.csv"


class MarketData:
    """Sırayla borsa dener; ilk çalışan borsayı kilitler; sonuçları CSV'de tutar."""

    def __init__(self, candidates: list[str], timeframe: str, history_days: int, cache_dir: Path):
        self.candidates = candidates
        self.timeframe = timeframe
        self.history_days = history_days
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._exchange = None
        self.exchange_id: str | None = None

    # ---- borsa bağlantısı -------------------------------------------------
    def _connect(self):
        if self._exchange is not None:
            return self._exchange
        import ccxt  # lazy import: testler ağ olmadan çalışabilsin

        last_err: Exception | None = None
        for name in self.candidates:
            try:
                ex = getattr(ccxt, name)({"enableRateLimit": True})
                ex.load_markets()
                self._exchange = ex
                self.exchange_id = name
                log.info("Borsa bağlandı: %s", name)
                return ex
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                log.warning("Borsa %s başarısız: %s", name, exc)
        raise RuntimeError(f"Hiçbir borsaya bağlanılamadı: {last_err}")

    # ---- veri çekme --------------------------------------------------------
    def fetch(self, symbol: str, force_refresh: bool = False) -> pd.DataFrame:
        ex = self._connect()
        cache = _cache_file(self.cache_dir, self.exchange_id or "x", symbol, self.timeframe)
        tf_ms = TIMEFRAME_MS[self.timeframe]
        now_ms = int(time.time() * 1000)
        since = now_ms - self.history_days * 86_400_000

        cached = pd.DataFrame(columns=COLUMNS)
        if cache.exists() and not force_refresh:
            try:
                cached = pd.read_csv(cache)
                if not cached.empty:
                    since = int(cached["timestamp"].iloc[-1])  # son barı yenile
            except Exception:  # noqa: BLE001
                cached = pd.DataFrame(columns=COLUMNS)

        rows: list[list] = []
        cursor = since
        while True:
            batch = ex.fetch_ohlcv(symbol, self.timeframe, since=cursor, limit=1000)
            if not batch:
                break
            rows.extend(batch)
            last_ts = batch[-1][0]
            if len(batch) < 2 or last_ts + tf_ms >= now_ms:
                break
            cursor = last_ts + tf_ms
        new = pd.DataFrame(rows, columns=COLUMNS)
        df = pd.concat([cached, new], ignore_index=True) if not cached.empty else new
        df = df.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
        df = df[df["timestamp"] >= now_ms - self.history_days * 86_400_000]
        for c in COLUMNS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna().reset_index(drop=True)
        df.to_csv(cache, index=False)
        return prepare(df)

    def fetch_many(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for s in symbols:
            try:
                out[s] = self.fetch(s)
                log.info("%s: %d bar", s, len(out[s]))
            except Exception as exc:  # noqa: BLE001
                log.error("%s verisi alınamadı: %s", s, exc)
        return out


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """timestamp -> UTC datetime index; son bar KAPANMAMIŞ olabilir, düşürülür."""
    df = df.copy()
    df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("dt")
    return df


def drop_unclosed_last_bar(df: pd.DataFrame, timeframe: str, now_ms: int | None = None) -> pd.DataFrame:
    """Sinyal üretirken açık (henüz kapanmamış) barı kullanma."""
    if df.empty:
        return df
    now_ms = now_ms or int(time.time() * 1000)
    tf_ms = TIMEFRAME_MS[timeframe]
    last_open = int(df["timestamp"].iloc[-1])
    if last_open + tf_ms > now_ms:
        return df.iloc[:-1]
    return df
