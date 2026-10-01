"""Piyasa akışı — sağlayıcı zinciri (öncelik sırası), artımlı önbellek birleştirme, açık bar düşürme, canlı anlık görüntü.

`MarketFeed.get_klines()`:
  1. Uygun sağlayıcıları (market_type eşleşen ya da 'any') sırayla dener; ilk başarılı kaynak kazanır.
  2. Önbellek (`cache_store`: `.read(symbol, tf, since_ms)`, `.write(symbol, tf, df)` — `storage.CandleStore` uyumlu) varsa
     yalnızca son bardan sonrası çekilir (artımlı), birleştirilir, tekilleştirilir.
  3. Kapanmamış son bar düşürülür (sinyal üretimi asla açık bar görmez), boşluklar (gaps) raporlanır.
`MarketFeed.snapshot()`: ticker + emir defteri (spread, derinlik ±%0.5 / ±%1, dengesizlik) + futures ekleri (mark/index/funding/OI/LSR/taker).
Sağlayıcı hataları toplanır (`errors`), akış çökmez; hiçbir sağlayıcı mum veremezse `DataQualityError`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import pandas as pd

from ..core import DataQualityError
from .providers import FUTURES, KLINE_COLUMNS, SPOT, now_ms, tf_ms, to_raw

log = logging.getLogger(__name__)

DEFAULT_FRESHNESS_BARS = 2.0
_MAX_PAGES = 25


# ---------------------------------------------------------------- sonuç tipleri
@dataclass
class KlinesResult:
    df: pd.DataFrame
    source: str
    fetched_at: int
    is_stale: bool
    gaps: list[int] = field(default_factory=list)   # eksik bar açılış zamanları (ms), ilk 50
    symbol: str = ""
    tf: str = ""
    market_type: str = FUTURES
    dropped_unclosed: int = 0
    from_cache: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def last_ts(self) -> int | None:
        return int(self.df["timestamp"].iloc[-1]) if len(self.df) else None

    @property
    def last_close(self) -> float | None:
        return float(self.df["close"].iloc[-1]) if len(self.df) else None

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "tf": self.tf, "market_type": self.market_type, "source": self.source,
                "fetched_at": self.fetched_at, "bars": int(len(self.df)), "last_ts": self.last_ts, "last_close": self.last_close,
                "is_stale": self.is_stale, "gaps": list(self.gaps), "dropped_unclosed": self.dropped_unclosed,
                "from_cache": self.from_cache, "errors": list(self.errors)}


@dataclass
class LiveSnapshot:
    symbol: str
    market_type: str
    ticker: dict = field(default_factory=dict)
    book: dict = field(default_factory=dict)
    last: float | None = None
    spread_pct: float | None = None
    depth_0_5pct: float | None = None       # ±%0.5 içindeki toplam notional (bid+ask, quote)
    depth_1pct: float | None = None
    imbalance: float | None = None          # bid_notional / (bid+ask) ±%1 (0.5 nötr)
    mark: float | None = None
    index: float | None = None
    funding_rate: float | None = None
    next_funding_ts: int | None = None
    oi: float | None = None
    lsr: float | None = None
    taker_ratio: float | None = None
    ts: int = 0
    source: str = ""
    errors: list[str] = field(default_factory=list)
    freshness_seconds: float | None = None

    @property
    def funding_pct(self) -> float | None:
        return None if self.funding_rate is None else self.funding_rate * 100.0

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["ticker"] = dict(self.ticker or {})
        d["book"] = dict(self.book or {})
        d["errors"] = list(self.errors)
        d["funding_pct"] = self.funding_pct
        return d


# ---------------------------------------------------------------- yardımcılar
def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN → None


def _cache_symbol(symbol: str, market_type: str) -> str:
    raw = to_raw(symbol)
    if "/" in symbol:
        base_quote = symbol.split(":")[0]
    else:
        base_quote = raw
        for q in ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB"):
            if raw.endswith(q) and len(raw) > len(q):
                base_quote = f"{raw[:-len(q)]}/{q}"
                break
    if market_type == FUTURES and "/" in base_quote:
        return f"{base_quote}:{base_quote.split('/')[1]}"
    return base_quote


def find_gaps(timestamps: Iterable[int], step: int, limit: int = 50) -> list[int]:
    ts = sorted({int(t) for t in timestamps})
    if len(ts) < 2:
        return []
    out: list[int] = []
    for a, b in zip(ts, ts[1:]):
        if b - a > step:
            t = a + step
            while t < b and len(out) < limit:
                out.append(t)
                t += step
            if len(out) >= limit:
                break
    return out


def normalize_frame(df: pd.DataFrame, tf: str, now_ms_: int) -> pd.DataFrame:
    """Herhangi bir OHLCV DataFrame'i standart KLINE_COLUMNS'a tamamla; tekilleştir; sırala; is_closed hesapla."""
    step = tf_ms(tf)
    out = df.copy()
    if out.empty:
        return pd.DataFrame(columns=KLINE_COLUMNS)
    out["timestamp"] = pd.to_numeric(out["timestamp"], errors="coerce").astype("int64")
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    if "close_time" not in out.columns:
        out["close_time"] = out["timestamp"] + step - 1
    else:
        ct = pd.to_numeric(out["close_time"], errors="coerce")
        out["close_time"] = ct.where(ct > 0, out["timestamp"] + step - 1).astype("int64")
    for c in ("quote_volume", "taker_buy_base", "taker_buy_quote"):
        if c not in out.columns:
            out[c] = float("nan")
    if "trades" not in out.columns:
        out["trades"] = 0
    out["trades"] = pd.to_numeric(out["trades"], errors="coerce").fillna(0).astype("int64")
    out = out.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    out["is_closed"] = out["close_time"] < int(now_ms_)
    return out[KLINE_COLUMNS]


# ---------------------------------------------------------------- akış
class MarketFeed:
    def __init__(self, providers_by_priority: list[Any], cache_store: Any = None,
                 freshness_limits: dict[str, float] | None = None, clock_ms: Callable[[], int] = now_ms,
                 default_freshness_bars: float = DEFAULT_FRESHNESS_BARS, drop_unclosed: bool = True,
                 depth_limit: int = 100):
        if not providers_by_priority:
            raise ValueError("en az bir sağlayıcı gerekli")
        self.providers = list(providers_by_priority)
        self.cache = cache_store
        self.freshness_limits = dict(freshness_limits or {})
        self.default_freshness_bars = float(default_freshness_bars)
        self.clock_ms = clock_ms
        self.drop_unclosed = drop_unclosed
        self.depth_limit = int(depth_limit)
        self.stats = {"klines_calls": 0, "provider_failures": 0, "cache_hits": 0, "snapshots": 0}

    # ---- sağlayıcı seçimi -----------------------------------------------
    def providers_for(self, market_type: str) -> list[Any]:
        exact = [p for p in self.providers if getattr(p, "market_type", "any") in (market_type, "any")]
        return exact or list(self.providers)

    def freshness_bars(self, tf: str) -> float:
        return float(self.freshness_limits.get(tf, self.default_freshness_bars))

    # ---- mumlar -----------------------------------------------------------
    def _fetch_range(self, provider: Any, symbol: str, tf: str, start_ms: int | None, need: int, now: int) -> pd.DataFrame:
        """`start_ms`'ten ileriye sayfalayarak en az `need` bar (ya da şimdiye kadar) çek."""
        step = tf_ms(tf)
        max_limit = int(getattr(provider, "max_kline_limit", 1000))
        frames: list[pd.DataFrame] = []
        if start_ms is None and need > max_limit:      # tek sayfaya sığmıyor → geriden ileriye sayfala
            start_ms = now - need * step
        cursor = start_ms
        got = 0
        for _ in range(_MAX_PAGES):
            limit = max(1, min(max_limit, need - got + 1))
            self.stats["klines_calls"] += 1
            batch = provider.klines(symbol, tf, limit=limit, start_ms=cursor)
            if batch is None or len(batch) == 0:
                break
            frames.append(batch)
            got += len(batch)
            last_ts = int(batch["timestamp"].iloc[-1])
            if start_ms is None or len(batch) < limit or last_ts + step > now or got >= need:
                break
            cursor = last_ts + step
        if not frames:
            return pd.DataFrame(columns=KLINE_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def _read_cache(self, csym: str, tf: str, since_ms: int) -> pd.DataFrame:
        if self.cache is None:
            return pd.DataFrame()
        try:
            df = self.cache.read(csym, tf, since_ms=since_ms)
        except Exception as exc:  # noqa: BLE001 — önbellek hatası akışı durdurmaz
            log.warning("önbellek okunamadı %s %s: %s", csym, tf, exc)
            return pd.DataFrame()
        return df if df is not None else pd.DataFrame()

    def _write_cache(self, csym: str, tf: str, df: pd.DataFrame) -> None:
        if self.cache is None or df is None or len(df) == 0:
            return
        try:
            self.cache.write(csym, tf, df[["timestamp", "open", "high", "low", "close", "volume"]])
        except Exception as exc:  # noqa: BLE001
            log.warning("önbellek yazılamadı %s %s: %s", csym, tf, exc)

    def get_klines(self, symbol: str, tf: str, market_type: str = FUTURES, n: int = 300) -> KlinesResult:
        now = int(self.clock_ms())
        step = tf_ms(tf)
        n = max(1, int(n))
        csym = _cache_symbol(symbol, market_type)
        since = now - (n + 50) * step          # geniş pencere: artımlı yol için önbellekte fazlalık kalsın
        cached = self._read_cache(csym, tf, since)
        cached = normalize_frame(cached, tf, now) if len(cached) else pd.DataFrame(columns=KLINE_COLUMNS)
        cached = cached[cached["is_closed"]] if len(cached) else cached
        errors: list[str] = []
        fetched: pd.DataFrame | None = None
        source = ""
        for p in self.providers_for(market_type):
            pname = getattr(p, "name", type(p).__name__)
            try:
                incremental = False
                if len(cached) and not find_gaps(cached["timestamp"], step, 1):
                    start = int(cached["timestamp"].iloc[-1]) + step
                    need = max(0, (now - start) // step + 1)          # yeni (muhtemelen kapalı) bar sayısı
                    incremental = len(cached) + max(0, need - 1) >= n   # yeni barlarla birlikte n'e ulaşılıyor
                if incremental:
                    fetched = self._fetch_range(p, symbol, tf, start, max(1, need), now) if start <= now else pd.DataFrame(columns=KLINE_COLUMNS)
                    self.stats["cache_hits"] += 1
                else:
                    fetched = self._fetch_range(p, symbol, tf, None, n + 2, now)
                    if fetched is not None and len(fetched) == 0 and len(cached) == 0:
                        raise DataQualityError(f"{pname}: {symbol} {tf} boş yanıt")
                source = pname
                break
            except Exception as exc:  # noqa: BLE001 — bir sonraki sağlayıcıya geç; hepsi toplanır
                self.stats["provider_failures"] += 1
                errors.append(f"{pname}: {type(exc).__name__}: {str(exc)[:120]}")
                log.warning("klines %s %s @%s başarısız: %s", symbol, tf, pname, exc)
                fetched = None
        if fetched is None:
            raise DataQualityError(f"hiçbir sağlayıcı {symbol} {tf} veremedi: {' | '.join(errors)}")
        fetched = normalize_frame(fetched, tf, now) if len(fetched) else pd.DataFrame(columns=KLINE_COLUMNS)
        merged = pd.concat([cached, fetched], ignore_index=True) if len(cached) else fetched
        merged = normalize_frame(merged, tf, now) if len(merged) else merged
        dropped = 0
        if self.drop_unclosed and len(merged):
            closed = merged[merged["is_closed"]]
            dropped = int(len(merged) - len(closed))
            merged = closed.reset_index(drop=True)
        merged = merged.tail(n).reset_index(drop=True)
        new_closed = fetched[fetched["is_closed"]] if len(fetched) else fetched
        self._write_cache(csym, tf, new_closed)
        gaps = find_gaps(merged["timestamp"], step) if len(merged) else []
        is_stale = True
        if len(merged):
            last_close = int(merged["close_time"].iloc[-1]) + 1
            is_stale = (now - last_close) > self.freshness_bars(tf) * step
        from_cache = int(len(cached[cached["timestamp"].isin(merged["timestamp"])])) if len(cached) and len(merged) else 0
        return KlinesResult(df=merged, source=source, fetched_at=now, is_stale=is_stale, gaps=gaps, symbol=symbol, tf=tf,
                            market_type=market_type, dropped_unclosed=dropped, from_cache=from_cache, errors=errors)

    # ---- anlık görüntü ---------------------------------------------------
    @staticmethod
    def _book_metrics(book: dict, depth: dict | None, snap: LiveSnapshot) -> None:
        bid = _f(book.get("bidPrice"))
        ask = _f(book.get("askPrice"))
        mid = None
        if bid and ask and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            snap.spread_pct = (ask - bid) / mid * 100.0
        if depth and mid:
            def _notional(levels: list, lo: float, hi: float) -> float:
                tot = 0.0
                for lv in levels:
                    px, qty = _f(lv[0]), _f(lv[1])
                    if px is None or qty is None:
                        continue
                    if lo <= px <= hi:
                        tot += px * qty
                return tot
            bids, asks = depth.get("bids") or [], depth.get("asks") or []
            b05 = _notional(bids, mid * 0.995, mid)
            a05 = _notional(asks, mid, mid * 1.005)
            b1 = _notional(bids, mid * 0.99, mid)
            a1 = _notional(asks, mid, mid * 1.01)
            snap.depth_0_5pct = b05 + a05
            snap.depth_1pct = b1 + a1
            snap.imbalance = (b1 / (b1 + a1)) if (b1 + a1) > 0 else 0.5

    def snapshot(self, symbol: str, market_type: str = FUTURES) -> LiveSnapshot:
        now = int(self.clock_ms())
        snap = LiveSnapshot(symbol=symbol, market_type=market_type, ts=now)
        self.stats["snapshots"] += 1
        provider = None
        for p in self.providers_for(market_type):
            pname = getattr(p, "name", type(p).__name__)
            try:
                rows = p.ticker24h([symbol])
            except Exception as exc:  # noqa: BLE001 — sonraki sağlayıcı
                snap.errors.append(f"{pname}.ticker24h: {type(exc).__name__}: {str(exc)[:100]}")
                continue
            if rows:
                snap.ticker = dict(rows[0])
                provider, snap.source = p, pname
                break
            snap.errors.append(f"{pname}.ticker24h: boş")
        if provider is None:
            return snap
        t = snap.ticker
        snap.last = _f(t.get("lastPrice")) or _f(t.get("last"))
        ct = _f(t.get("closeTime"))
        if ct:
            snap.freshness_seconds = max(0.0, (now - ct) / 1000.0)
        # emir defteri
        try:
            snap.book = dict(provider.book_ticker(symbol) or {})
        except Exception as exc:  # noqa: BLE001
            snap.errors.append(f"book_ticker: {type(exc).__name__}: {str(exc)[:100]}")
            if t.get("bidPrice") and t.get("askPrice"):
                snap.book = {"bidPrice": t.get("bidPrice"), "askPrice": t.get("askPrice")}
        depth = None
        try:
            depth = provider.depth(symbol, limit=self.depth_limit)
        except Exception as exc:  # noqa: BLE001
            snap.errors.append(f"depth: {type(exc).__name__}: {str(exc)[:100]}")
        if snap.book:
            self._book_metrics(snap.book, depth, snap)
        # futures ekleri
        if market_type == FUTURES:
            self._futures_extras(provider, symbol, snap)
        return snap

    @staticmethod
    def _futures_extras(provider: Any, symbol: str, snap: LiveSnapshot) -> None:
        if hasattr(provider, "mark_price"):
            try:
                m = provider.mark_price(symbol) or {}
                snap.mark = _f(m.get("mark"))
                snap.index = _f(m.get("index"))
                snap.funding_rate = _f(m.get("funding_rate"))
                nft = m.get("next_funding_ts")
                snap.next_funding_ts = int(nft) if nft else None
            except Exception as exc:  # noqa: BLE001
                snap.errors.append(f"mark_price: {type(exc).__name__}: {str(exc)[:100]}")
        if hasattr(provider, "open_interest"):
            try:
                snap.oi = _f((provider.open_interest(symbol) or {}).get("oi"))
            except Exception as exc:  # noqa: BLE001
                snap.errors.append(f"open_interest: {type(exc).__name__}: {str(exc)[:100]}")
        if hasattr(provider, "long_short_ratio"):
            try:
                rows = provider.long_short_ratio(symbol, "1h", 1) or []
                snap.lsr = _f(rows[-1].get("ratio")) if rows else None
            except Exception as exc:  # noqa: BLE001
                snap.errors.append(f"long_short_ratio: {type(exc).__name__}: {str(exc)[:100]}")
        if hasattr(provider, "taker_buy_sell_ratio"):
            try:
                rows = provider.taker_buy_sell_ratio(symbol, "1h", 1) or []
                snap.taker_ratio = _f(rows[-1].get("ratio")) if rows else None
            except Exception as exc:  # noqa: BLE001
                snap.errors.append(f"taker_ratio: {type(exc).__name__}: {str(exc)[:100]}")

    # ---- saat --------------------------------------------------------------
    def clock_drift_ms(self, market_type: str | None = None) -> int:
        """Sunucu − yerel saat farkı (ms). İlk yanıt veren sağlayıcı kullanılır; hiçbiri vermezse DataQualityError."""
        errors = []
        for p in (self.providers_for(market_type) if market_type else self.providers):
            if not hasattr(p, "server_time_ms"):
                continue
            try:
                local = int(self.clock_ms())
                srv = int(p.server_time_ms())
                return srv - local
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{getattr(p, 'name', '?')}: {exc}")
        raise DataQualityError(f"sunucu saati alınamadı: {' | '.join(errors) or 'sağlayıcı yok'}")


__all__ = ["MarketFeed", "KlinesResult", "LiveSnapshot", "normalize_frame", "find_gaps", "SPOT", "FUTURES"]
