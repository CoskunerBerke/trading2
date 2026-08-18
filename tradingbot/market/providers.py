"""Piyasa verisi sağlayıcıları — resmi Binance REST (spot + USDⓈ-M futures), TradingView/ccxt yedekleri, Mock/Replay.

Ortak sözleşme `MarketDataProvider` (Protocol). Mum DataFrame'i sütunları:
  timestamp(open ms), open, high, low, close, volume, close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, is_closed
`is_closed` = close_time < şimdi (sunucu saati kayması ile düzeltilmiş). Sağlayıcı ağ dışı hiçbir iş yapmaz;
tazelik/kalite kararı `quality.DataQualityGate`, birleştirme `feed.MarketFeed` işidir.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

import pandas as pd

from ..data import TIMEFRAME_MS

log = logging.getLogger(__name__)

SPOT = "spot"
FUTURES = "futures"

KLINE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades",
                 "taker_buy_base", "taker_buy_quote", "is_closed"]
_FLOAT_COLS = ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]

# ccxt tarzı zaman dilimi → Binance interval (aynı gösterim, doğrulama için)
BINANCE_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}


def tf_ms(interval: str) -> int:
    if interval in TIMEFRAME_MS:
        return TIMEFRAME_MS[interval]
    extra = {"3m": 180_000, "8h": 28_800_000, "3d": 259_200_000, "1M": 2_592_000_000}
    if interval in extra:
        return extra[interval]
    raise ValueError(f"bilinmeyen interval: {interval}")


def to_raw(symbol: str) -> str:
    """'BTC/USDT' | 'BTC/USDT:USDT' → 'BTCUSDT'."""
    return symbol.split(":")[0].replace("/", "").upper()


def to_pretty(raw: str, base: str, quote: str) -> str:
    return f"{base}/{quote}"


def now_ms() -> int:
    return int(time.time() * 1000)


def klines_frame(rows: Iterable[Iterable[Any]], now_ms_: int | None = None) -> pd.DataFrame:
    """Binance 12 alanlı kline satırları → standart DataFrame (kapanış bayrağı dahil)."""
    now_ = now_ms_ if now_ms_ is not None else now_ms()
    recs = []
    for r in rows:
        r = list(r)
        if len(r) < 6:
            continue
        rec = {
            "timestamp": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
            "close": float(r[4]), "volume": float(r[5]),
            "close_time": int(r[6]) if len(r) > 6 and r[6] is not None else 0,
            "quote_volume": float(r[7]) if len(r) > 7 and r[7] is not None else float("nan"),
            "trades": int(r[8]) if len(r) > 8 and r[8] is not None else 0,
            "taker_buy_base": float(r[9]) if len(r) > 9 and r[9] is not None else float("nan"),
            "taker_buy_quote": float(r[10]) if len(r) > 10 and r[10] is not None else float("nan"),
        }
        recs.append(rec)
    df = pd.DataFrame(recs, columns=KLINE_COLUMNS[:-1])
    if df.empty:
        df["is_closed"] = pd.Series(dtype=bool)
        return df
    df["is_closed"] = df["close_time"] < now_
    df = df.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    return df


def frame_from_ohlcv(rows: Iterable[Iterable[Any]], interval: str, now_ms_: int | None = None) -> pd.DataFrame:
    """[ts,o,h,l,c,v] satırları (ccxt/TradingView) → standart DataFrame; close_time = ts + tf - 1."""
    step = tf_ms(interval)
    full = []
    for r in rows:
        r = list(r)
        full.append([r[0], r[1], r[2], r[3], r[4], r[5] if len(r) > 5 else 0.0, int(r[0]) + step - 1, None, 0, None, None, 0])
    return klines_frame(full, now_ms_)


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    market_type: str

    def exchange_info(self) -> list[dict]: ...
    def klines(self, symbol: str, interval: str, limit: int = 500, start_ms: int | None = None,
               end_ms: int | None = None) -> pd.DataFrame: ...
    def ticker24h(self, symbols: list[str] | None = None) -> list[dict]: ...
    def book_ticker(self, symbol: str) -> dict: ...
    def depth(self, symbol: str, limit: int = 100) -> dict: ...
    def server_time_ms(self) -> int: ...


# ---------------------------------------------------------------------------- Binance REST
def _kline_weight_spot(limit: int) -> int:
    return 1 if limit <= 100 else 2 if limit <= 500 else 5 if limit <= 1000 else 10


def _kline_weight_fut(limit: int) -> int:
    return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10


def _depth_weight_spot(limit: int) -> int:
    return 5 if limit <= 100 else 25 if limit <= 500 else 50 if limit <= 1000 else 250


def _depth_weight_fut(limit: int) -> int:
    return 2 if limit <= 50 else 5 if limit <= 100 else 10 if limit <= 500 else 20


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class _BinanceBase:
    """Ortak Binance parçaları. `http` = HttpClient benzeri (`get(path, params, weight)`)."""

    name = "binance"
    market_type = SPOT
    prefix = "/api/v3"

    def __init__(self, http: Any, clock_ms: Callable[[], int] = now_ms):
        self.http = http
        self.clock_ms = clock_ms
        self.drift_ms: int = 0        # sunucu - yerel (son server_time çağrısında ölçülür)

    # --- yardımcılar ------------------------------------------------------
    def _now(self) -> int:
        return self.clock_ms() + self.drift_ms

    def _kline_weight(self, limit: int) -> int:
        return _kline_weight_spot(limit)

    def _depth_weight(self, limit: int) -> int:
        return _depth_weight_spot(limit)

    # --- ortak uç noktalar -----------------------------------------------
    def server_time_ms(self) -> int:
        local = self.clock_ms()
        data = self.http.get(f"{self.prefix}/time", weight=1)
        srv = int(data["serverTime"])
        self.drift_ms = srv - local
        return srv

    def exchange_info(self) -> list[dict]:
        data = self.http.get(f"{self.prefix}/exchangeInfo", weight=self.exchange_info_weight)
        return list(data.get("symbols", []))

    def klines(self, symbol: str, interval: str, limit: int = 500, start_ms: int | None = None,
               end_ms: int | None = None) -> pd.DataFrame:
        if interval not in BINANCE_INTERVALS:
            raise ValueError(f"Binance interval değil: {interval}")
        limit = max(1, min(int(limit), self.max_kline_limit))
        params = {"symbol": to_raw(symbol), "interval": interval, "limit": limit, "startTime": start_ms, "endTime": end_ms}
        rows = self.http.get(f"{self.prefix}/klines", params=params, weight=self._kline_weight(limit))
        return klines_frame(rows, self._now())

    def ticker24h(self, symbols: list[str] | None = None) -> list[dict]:
        if symbols and len(symbols) == 1:
            data = self.http.get(f"{self.prefix}/ticker/24hr", params={"symbol": to_raw(symbols[0])}, weight=self.ticker_single_weight)
            return [data] if isinstance(data, dict) else list(data)
        data = self.http.get(f"{self.prefix}/ticker/24hr", weight=self.ticker_all_weight)
        rows = list(data) if isinstance(data, list) else [data]
        if symbols:
            want = {to_raw(s) for s in symbols}
            rows = [r for r in rows if r.get("symbol") in want]
        return rows

    def book_ticker(self, symbol: str) -> dict:
        return self.http.get(f"{self.prefix}/ticker/bookTicker", params={"symbol": to_raw(symbol)}, weight=self.book_weight)

    def book_tickers(self) -> list[dict]:
        """Bütün semboller için en iyi bid/ask (tek istek) — evren spread filtresi için."""
        data = self.http.get(f"{self.prefix}/ticker/bookTicker", weight=self.book_all_weight)
        return list(data) if isinstance(data, list) else [data]

    def depth(self, symbol: str, limit: int = 100) -> dict:
        return self.http.get(f"{self.prefix}/depth", params={"symbol": to_raw(symbol), "limit": int(limit)}, weight=self._depth_weight(limit))


class BinanceSpotProvider(_BinanceBase):
    name = "binance_spot"
    market_type = SPOT
    prefix = "/api/v3"
    base_url = "https://api.binance.com"
    exchange_info_weight = 20
    ticker_single_weight = 2
    ticker_all_weight = 80
    book_weight = 2
    book_all_weight = 4
    max_kline_limit = 1000


class BinanceFuturesProvider(_BinanceBase):
    name = "binance_futures"
    market_type = FUTURES
    prefix = "/fapi/v1"
    base_url = "https://fapi.binance.com"
    exchange_info_weight = 1
    ticker_single_weight = 1
    ticker_all_weight = 40
    book_weight = 2
    book_all_weight = 5
    max_kline_limit = 1500

    def _kline_weight(self, limit: int) -> int:
        return _kline_weight_fut(limit)

    def _depth_weight(self, limit: int) -> int:
        return _depth_weight_fut(limit)

    # --- futures'a özel ----------------------------------------------------
    def mark_price(self, symbol: str) -> dict:
        """premiumIndex → {symbol, mark, index, funding_rate, next_funding_ts, ts}."""
        d = self.http.get(f"{self.prefix}/premiumIndex", params={"symbol": to_raw(symbol)}, weight=1)
        return {
            "symbol": d.get("symbol"), "mark": _f(d.get("markPrice")), "index": _f(d.get("indexPrice")),
            "estimated_settle": _f(d.get("estimatedSettlePrice")), "funding_rate": _f(d.get("lastFundingRate")),
            "interest_rate": _f(d.get("interestRate")), "next_funding_ts": int(d.get("nextFundingTime") or 0),
            "ts": int(d.get("time") or 0),
        }

    def funding_history(self, symbol: str, limit: int = 100, start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
        rows = self.http.get(f"{self.prefix}/fundingRate", params={"symbol": to_raw(symbol), "limit": int(limit), "startTime": start_ms, "endTime": end_ms}, weight=1)
        return [{"symbol": r.get("symbol"), "funding_ts": int(r.get("fundingTime") or 0), "rate": _f(r.get("fundingRate")),
                 "mark": _f(r.get("markPrice"))} for r in rows]

    def open_interest(self, symbol: str) -> dict:
        d = self.http.get(f"{self.prefix}/openInterest", params={"symbol": to_raw(symbol)}, weight=1)
        return {"symbol": d.get("symbol"), "oi": _f(d.get("openInterest")), "ts": int(d.get("time") or 0)}

    def open_interest_hist(self, symbol: str, period: str = "1h", limit: int = 30) -> list[dict]:
        rows = self.http.get("/futures/data/openInterestHist", params={"symbol": to_raw(symbol), "period": period, "limit": int(limit)}, weight=1)
        return [{"symbol": r.get("symbol"), "oi": _f(r.get("sumOpenInterest")), "oi_value": _f(r.get("sumOpenInterestValue")),
                 "ts": int(r.get("timestamp") or 0)} for r in rows]

    def long_short_ratio(self, symbol: str, period: str = "1h", limit: int = 1) -> list[dict]:
        rows = self.http.get("/futures/data/globalLongShortAccountRatio", params={"symbol": to_raw(symbol), "period": period, "limit": int(limit)}, weight=1)
        return [{"symbol": r.get("symbol"), "ratio": _f(r.get("longShortRatio")), "long_pct": _f(r.get("longAccount")) * 100,
                 "short_pct": _f(r.get("shortAccount")) * 100, "ts": int(r.get("timestamp") or 0)} for r in rows]

    def taker_buy_sell_ratio(self, symbol: str, period: str = "1h", limit: int = 1) -> list[dict]:
        rows = self.http.get("/futures/data/takerlongshortRatio", params={"symbol": to_raw(symbol), "period": period, "limit": int(limit)}, weight=1)
        return [{"symbol": to_raw(symbol), "ratio": _f(r.get("buySellRatio")), "buy_vol": _f(r.get("buyVol")),
                 "sell_vol": _f(r.get("sellVol")), "ts": int(r.get("timestamp") or 0)} for r in rows]


# ---------------------------------------------------------------------------- yedekler
class TradingViewProvider:
    """Mevcut `tradingbot.tradingview.TradingViewFeed` etrafında ince sarmalayıcı — yalnızca klines."""

    name = "tradingview"
    market_type = "any"

    def __init__(self, feed: Any = None, exchange: str = "BINANCE", clock_ms: Callable[[], int] = now_ms):
        self._feed = feed
        self.exchange = exchange
        self.clock_ms = clock_ms

    def _tv(self):
        if self._feed is None:
            from ..tradingview import TradingViewFeed  # tembel (websocket-client opsiyonel)

            self._feed = TradingViewFeed()
        return self._feed

    def klines(self, symbol: str, interval: str, limit: int = 500, start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
        from ..tradingview import TV_INTERVAL, tv_symbol

        res = TV_INTERVAL.get(interval)
        if res is None:
            raise ValueError(f"TradingView çözünürlüğü yok: {interval}")
        raw = self._tv().fetch_ohlcv(tv_symbol(self.exchange, symbol.split(":")[0]), res, n_bars=max(300, min(int(limit), 5000)))
        rows = raw[["timestamp", "open", "high", "low", "close", "volume"]].to_numpy().tolist()
        df = frame_from_ohlcv(rows, interval, self.clock_ms())
        if start_ms is not None:
            df = df[df["timestamp"] >= start_ms]
        if end_ms is not None:
            df = df[df["timestamp"] <= end_ms]
        return df.tail(limit).reset_index(drop=True)

    def exchange_info(self) -> list[dict]:
        return []

    def ticker24h(self, symbols=None):
        return []

    def book_ticker(self, symbol):
        return {}

    def depth(self, symbol, limit=100):
        return {"bids": [], "asks": []}

    def server_time_ms(self) -> int:
        return self.clock_ms()


class CcxtFallbackProvider:
    """ccxt (public) yedeği — klines + ticker. exchange_id: 'binance' (spot) | 'binanceusdm' (futures)."""

    name = "ccxt"

    def __init__(self, exchange_id: str = "binance", exchange: Any = None, clock_ms: Callable[[], int] = now_ms):
        self.exchange_id = exchange_id
        self.market_type = FUTURES if exchange_id.endswith("usdm") else SPOT
        self._ex = exchange
        self.clock_ms = clock_ms
        self.name = f"ccxt:{exchange_id}"

    def _exchange(self):
        if self._ex is None:
            import ccxt  # tembel

            self._ex = getattr(ccxt, self.exchange_id)({"enableRateLimit": True})
        return self._ex

    def _sym(self, symbol: str) -> str:
        s = symbol.split(":")[0]
        if "/" not in s:  # 'BTCUSDT' → 'BTC/USDT' (USDT/USDC/BUSD/BTC quote'ları)
            for q in ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB"):
                if s.endswith(q) and len(s) > len(q):
                    s = f"{s[:-len(q)]}/{q}"
                    break
        return f"{s}:USDT" if self.market_type == FUTURES and s.endswith("/USDT") else s

    def klines(self, symbol: str, interval: str, limit: int = 500, start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
        ex = self._exchange()
        rows = ex.fetch_ohlcv(self._sym(symbol), interval, since=start_ms, limit=min(int(limit), 1000))
        df = frame_from_ohlcv(rows, interval, self.clock_ms())
        if end_ms is not None:
            df = df[df["timestamp"] <= end_ms]
        return df.tail(limit).reset_index(drop=True)

    def ticker24h(self, symbols: list[str] | None = None) -> list[dict]:
        ex = self._exchange()
        out = []
        for s in symbols or []:
            t = ex.fetch_ticker(self._sym(s))
            out.append({"symbol": to_raw(s), "lastPrice": t.get("last"), "bidPrice": t.get("bid"), "askPrice": t.get("ask"),
                        "quoteVolume": t.get("quoteVolume"), "volume": t.get("baseVolume"), "priceChangePercent": t.get("percentage"),
                        "highPrice": t.get("high"), "lowPrice": t.get("low"), "closeTime": t.get("timestamp")})
        return out

    def exchange_info(self) -> list[dict]:
        return []

    def book_ticker(self, symbol: str) -> dict:
        t = self._exchange().fetch_ticker(self._sym(symbol))
        return {"symbol": to_raw(symbol), "bidPrice": t.get("bid"), "askPrice": t.get("ask"), "bidQty": t.get("bidVolume"), "askQty": t.get("askVolume")}

    def depth(self, symbol: str, limit: int = 100) -> dict:
        ob = self._exchange().fetch_order_book(self._sym(symbol), limit=limit)
        return {"bids": ob.get("bids", []), "asks": ob.get("asks", [])}

    def server_time_ms(self) -> int:
        return int(self._exchange().fetch_time())


# ---------------------------------------------------------------------------- test / replay
def _key(symbol: str, interval: str) -> tuple[str, str]:
    return (to_raw(symbol), interval)


class MockProvider:
    """Testler için bellek içi sağlayıcı. `candles`: {(symbol, interval) | 'BTCUSDT:4h' | 'BTC/USDT': {'4h': df}} → DataFrame."""

    def __init__(self, candles: dict | None = None, tickers: dict | None = None, books: dict | None = None,
                 depths: dict | None = None, marks: dict | None = None, ois: dict | None = None, lsrs: dict | None = None,
                 takers: dict | None = None, symbols_info: list[dict] | None = None, market_type: str = FUTURES,
                 name: str = "mock", clock_ms: Callable[[], int] = now_ms, fail: set | None = None):
        self.name = name
        self.market_type = market_type
        self.clock_ms = clock_ms
        self._candles: dict[tuple[str, str], pd.DataFrame] = {}
        for k, v in (candles or {}).items():
            if isinstance(v, dict):
                for tf, df in v.items():
                    self._candles[_key(k, tf)] = df
            elif isinstance(k, tuple):
                self._candles[_key(k[0], k[1])] = v
            else:
                sym, tf = str(k).split(":")
                self._candles[_key(sym, tf)] = v
        self.tickers = {to_raw(k): v for k, v in (tickers or {}).items()}
        self.books = {to_raw(k): v for k, v in (books or {}).items()}
        self.depths = {to_raw(k): v for k, v in (depths or {}).items()}
        self.marks = {to_raw(k): v for k, v in (marks or {}).items()}
        self.ois = {to_raw(k): v for k, v in (ois or {}).items()}
        self.lsrs = {to_raw(k): v for k, v in (lsrs or {}).items()}
        self.takers = {to_raw(k): v for k, v in (takers or {}).items()}
        self.symbols_info = symbols_info or []
        self.fail = fail or set()          # {'klines', 'ticker24h', ...} → RuntimeError
        self.calls: list[tuple] = []

    def _maybe_fail(self, what: str) -> None:
        if what in self.fail:
            raise RuntimeError(f"mock failure: {what}")

    def set_candles(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        self._candles[_key(symbol, interval)] = df

    def exchange_info(self) -> list[dict]:
        self._maybe_fail("exchange_info")
        return list(self.symbols_info)

    def klines(self, symbol: str, interval: str, limit: int = 500, start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
        self.calls.append(("klines", to_raw(symbol), interval, limit, start_ms, end_ms))
        self._maybe_fail("klines")
        df = self._candles.get(_key(symbol, interval))
        if df is None:
            raise KeyError(f"mock: {symbol} {interval} yok")
        df = df.copy()
        if "close_time" not in df.columns:
            df["close_time"] = df["timestamp"].astype("int64") + tf_ms(interval) - 1
        for c in ("quote_volume", "taker_buy_base", "taker_buy_quote"):
            if c not in df.columns:
                df[c] = float("nan")
        if "trades" not in df.columns:
            df["trades"] = 0
        if start_ms is not None:
            df = df[df["timestamp"] >= start_ms]
        if end_ms is not None:
            df = df[df["timestamp"] <= end_ms]
        # Binance semantiği: startTime verilirse ondan itibaren ilk `limit` bar; verilmezse son `limit` bar
        df = (df.head(limit) if start_ms is not None else df.tail(limit)).reset_index(drop=True)
        df["is_closed"] = df["close_time"] < self.clock_ms()
        return df[KLINE_COLUMNS]

    def ticker24h(self, symbols: list[str] | None = None) -> list[dict]:
        self._maybe_fail("ticker24h")
        if symbols:
            return [self.tickers[to_raw(s)] for s in symbols if to_raw(s) in self.tickers]
        return list(self.tickers.values())

    def book_ticker(self, symbol: str) -> dict:
        self._maybe_fail("book_ticker")
        return self.books[to_raw(symbol)]

    def book_tickers(self) -> list[dict]:
        self._maybe_fail("book_tickers")
        return [dict(v, symbol=v.get("symbol", k)) for k, v in self.books.items()]

    def depth(self, symbol: str, limit: int = 100) -> dict:
        self._maybe_fail("depth")
        d = self.depths[to_raw(symbol)]
        return {"bids": d["bids"][:limit], "asks": d["asks"][:limit]}

    def server_time_ms(self) -> int:
        return self.clock_ms()

    def mark_price(self, symbol: str) -> dict:
        self._maybe_fail("mark_price")
        return self.marks[to_raw(symbol)]

    def funding_history(self, symbol: str, limit: int = 100, start_ms=None, end_ms=None) -> list[dict]:
        m = self.marks.get(to_raw(symbol))
        return [{"symbol": to_raw(symbol), "funding_ts": 0, "rate": m.get("funding_rate", 0.0), "mark": m.get("mark", 0.0)}] if m else []

    def open_interest(self, symbol: str) -> dict:
        self._maybe_fail("open_interest")
        return self.ois[to_raw(symbol)]

    def open_interest_hist(self, symbol: str, period: str = "1h", limit: int = 30) -> list[dict]:
        return list(self.ois.get(to_raw(symbol), {}).get("hist", []))[-limit:]

    def long_short_ratio(self, symbol: str, period: str = "1h", limit: int = 1) -> list[dict]:
        return [self.lsrs[to_raw(symbol)]] if to_raw(symbol) in self.lsrs else []

    def taker_buy_sell_ratio(self, symbol: str, period: str = "1h", limit: int = 1) -> list[dict]:
        return [self.takers[to_raw(symbol)]] if to_raw(symbol) in self.takers else []


class ReplayProvider:
    """Deterministik yeniden oynatma: `clock()` (ms) anına kadar açılmış barları döndürür; gelecek satır asla sızmaz.

    `store_or_frames`: {(symbol, interval): DataFrame} sözlüğü ya da `.get_klines(symbol, interval)` sunan nesne.
    Ticker/mark, son kapanmış bar kapanışından türetilir (spread 0).
    """

    name = "replay"

    def __init__(self, store_or_frames: Any, clock: Callable[[], int] | int, market_type: str = FUTURES):
        self._store = store_or_frames
        self._clock = clock
        self.market_type = market_type

    def now(self) -> int:
        return int(self._clock() if callable(self._clock) else self._clock)

    def set_clock(self, ms: int) -> None:
        self._clock = int(ms)

    def _frame(self, symbol: str, interval: str) -> pd.DataFrame:
        if isinstance(self._store, dict):
            df = self._store.get(_key(symbol, interval))
            if df is None:
                df = self._store.get((symbol, interval))
        else:
            df = self._store.get_klines(symbol, interval)
        if df is None:
            raise KeyError(f"replay: {symbol} {interval} yok")
        return df

    def klines(self, symbol: str, interval: str, limit: int = 500, start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
        now = self.now()
        df = self._frame(symbol, interval).copy()
        step = tf_ms(interval)
        if "close_time" not in df.columns:
            df["close_time"] = df["timestamp"].astype("int64") + step - 1
        df = df[df["timestamp"] <= now]
        if start_ms is not None:
            df = df[df["timestamp"] >= start_ms]
        if end_ms is not None:
            df = df[df["timestamp"] <= min(end_ms, now)]
        for c in ("quote_volume", "taker_buy_base", "taker_buy_quote"):
            if c not in df.columns:
                df[c] = float("nan")
        if "trades" not in df.columns:
            df["trades"] = 0
        df = (df.head(limit) if start_ms is not None else df.tail(limit)).reset_index(drop=True)
        df["is_closed"] = df["close_time"] < now
        return df[KLINE_COLUMNS]

    def _last_close(self, symbol: str) -> float | None:
        for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
            try:
                df = self.klines(symbol, tf, limit=2)
            except KeyError:
                continue
            closed = df[df["is_closed"]]
            if not closed.empty:
                return float(closed["close"].iloc[-1])
        return None

    def exchange_info(self) -> list[dict]:
        return []

    def ticker24h(self, symbols: list[str] | None = None) -> list[dict]:
        out = []
        for s in symbols or []:
            px = self._last_close(s)
            if px is not None:
                out.append({"symbol": to_raw(s), "lastPrice": px, "bidPrice": px, "askPrice": px, "closeTime": self.now(), "quoteVolume": 0.0, "priceChangePercent": 0.0})
        return out

    def book_ticker(self, symbol: str) -> dict:
        px = self._last_close(symbol)
        return {"symbol": to_raw(symbol), "bidPrice": px, "askPrice": px, "bidQty": 0.0, "askQty": 0.0}

    def depth(self, symbol: str, limit: int = 100) -> dict:
        return {"bids": [], "asks": []}

    def server_time_ms(self) -> int:
        return self.now()

    def mark_price(self, symbol: str) -> dict:
        px = self._last_close(symbol) or 0.0
        return {"symbol": to_raw(symbol), "mark": px, "index": px, "funding_rate": 0.0, "next_funding_ts": 0, "ts": self.now()}


__all__ = ["MarketDataProvider", "BinanceSpotProvider", "BinanceFuturesProvider", "TradingViewProvider", "CcxtFallbackProvider",
           "MockProvider", "ReplayProvider", "klines_frame", "frame_from_ohlcv", "to_raw", "to_pretty", "tf_ms", "now_ms",
           "KLINE_COLUMNS", "SPOT", "FUTURES"]
