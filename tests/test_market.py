"""Piyasa verisi katmanı testleri — tamamen çevrimdışı (sahte oturum/uyku, satır içi Binance tarzı JSON)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot import indicators_ext as ext
from tradingbot.core import DataQualityError
from tradingbot.market import (BinanceFuturesProvider, BinanceSpotProvider, CandidateFunnel, DataQualityConfig,
                               DataQualityGate, HttpClient, MarketFeed, MockProvider, RateBudget, ReplayProvider,
                               TransientHttpError, UniverseConfig, UniverseSnapshot, build_futures_universe,
                               build_spot_universe, fast_features, klines_frame, merge_universe, tier1_score)
from tradingbot.market.feed import LiveSnapshot, find_gaps
from tradingbot.market.ratelimit import BannedError, RateLimitedError, backoff
from tradingbot.market.scanner_fast import CandidateRow

H4 = 14_400_000
NOW = 1_755_500_000_000  # sabit "şimdi" (ms) — 4h ızgarasına hizalı değil, gerçekçi


# ------------------------------------------------------------------ yardımcılar
class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeResp:
    def __init__(self, status: int, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text or json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Sıradaki yanıtları verir; çağrıları kaydeder."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_ohlcv(n: int, tf: int = H4, start: int | None = None, seed: int = 7, base: float = 100.0, with_close_time=True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = start if start is not None else (NOW // tf) * tf - (n - 1) * tf
    ts = np.arange(n, dtype=np.int64) * tf + start
    close = base * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    open_ = np.roll(close, 1); open_[0] = base
    hi = np.maximum(open_, close) * (1 + rng.uniform(0, 0.005, n))
    lo = np.minimum(open_, close) * (1 - rng.uniform(0, 0.005, n))
    vol = rng.uniform(100, 1000, n)
    df = pd.DataFrame({"timestamp": ts, "open": open_, "high": hi, "low": lo, "close": close, "volume": vol})
    if with_close_time:
        df["close_time"] = df["timestamp"] + tf - 1
    return df


def binance_kline_rows(df: pd.DataFrame, tf: int = H4) -> list[list]:
    return [[int(r.timestamp), f"{r.open:.4f}", f"{r.high:.4f}", f"{r.low:.4f}", f"{r.close:.4f}", f"{r.volume:.3f}",
             int(r.timestamp) + tf - 1, f"{r.volume * r.close:.2f}", 123, f"{r.volume/2:.3f}", f"{r.volume*r.close/2:.2f}", "0"]
            for r in df.itertuples()]


# ------------------------------------------------------------------ rate limit
def test_rate_budget_acquire_and_backoff_and_429_cooldown():
    clk = FakeClock()
    b = RateBudget(weight_per_minute=100, safety=1.0, clock=clk, sleeper=clk.sleep, rng=lambda: 0.0, max_jitter_s=0.0)
    assert b.acquire(60) == 0.0
    assert b.tokens == pytest.approx(40)
    slept = b.acquire(60)                     # 20 token eksik → 20 / (100/60) = 12 s uyku
    assert slept == pytest.approx(12.0, abs=1e-6)
    ra = b.on_429(retry_after=30)
    assert ra == 30 and b.tokens == 0.0
    slept = b.acquire(1)                      # soğuma bitene kadar bekler
    assert slept >= 30.0
    b.on_418(retry_after=120)
    with pytest.raises(BannedError):
        b.acquire(1)
    b.on_response({"X-MBX-USED-WEIGHT-1M": "95"})
    assert b.used_weight_1m == 95 and b.tokens <= 5
    assert backoff(0, base=0.5, jitter=False) == 0.5 and backoff(3, base=0.5, jitter=False) == 4.0
    assert backoff(20, cap=30, jitter=False) == 30.0
    d = backoff(2, base=1.0, jitter=True, rng=lambda: 0.5)
    assert 2.0 <= d <= 4.0
    assert b.to_dict()["host"] == ""


# ------------------------------------------------------------------ http
def _client(responses, max_retries=3):
    clk = FakeClock()
    sess = FakeSession(responses)
    budget = RateBudget(weight_per_minute=1200, clock=clk, sleeper=clk.sleep, rng=lambda: 0.0, max_jitter_s=0.0)
    c = HttpClient("https://api.binance.com", budget=budget, session=sess, sleeper=clk.sleep, max_retries=max_retries, jitter=False)
    return c, sess, clk


def test_http_retry_on_5xx_then_success_and_raise_after_n():
    c, sess, _ = _client([FakeResp(502, {"code": -1}), FakeResp(503, {"code": -1}), FakeResp(200, {"serverTime": 1})])
    assert c.get("/api/v3/time") == {"serverTime": 1}
    assert c.stats["retries"] == 2 and len(sess.calls) == 3
    c2, sess2, _ = _client([FakeResp(500, {}) for _ in range(5)], max_retries=2)
    with pytest.raises(TransientHttpError):
        c2.get("/api/v3/time")
    assert len(sess2.calls) == 3
    c3, _, _ = _client([TimeoutError("t/o"), FakeResp(200, [1, 2])])
    assert c3.get("/x") == [1, 2]


def test_http_429_cooldown_then_success():
    c, sess, clk = _client([FakeResp(429, {"code": -1003}, headers={"Retry-After": "7"}), FakeResp(200, {"ok": 1})])
    t0 = clk.t
    assert c.get("/api/v3/ping") == {"ok": 1}
    assert clk.t - t0 >= 7.0 and c.stats["rate_limited"] == 1
    c4, _, _ = _client([FakeResp(429, {}, headers={"Retry-After": "1"}) for _ in range(6)], max_retries=1)
    with pytest.raises(RateLimitedError):
        c4.get("/y")
    c5, _, _ = _client([FakeResp(418, {}, headers={"Retry-After": "5"})])
    with pytest.raises(BannedError):
        c5.get("/z")


# ------------------------------------------------------------------ providers parse
EXINFO_SPOT = {"symbols": [
    {"symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT", "isSpotTradingAllowed": True,
     "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01000000"}, {"filterType": "LOT_SIZE", "stepSize": "0.00001000", "minQty": "0.00001"},
                 {"filterType": "NOTIONAL", "minNotional": "5.00000000"}]},
    {"symbol": "ETHUSDT", "status": "TRADING", "baseAsset": "ETH", "quoteAsset": "USDT", "filters": []},
    {"symbol": "USDCUSDT", "status": "TRADING", "baseAsset": "USDC", "quoteAsset": "USDT", "filters": []},
    {"symbol": "BTCUPUSDT", "status": "TRADING", "baseAsset": "BTCUP", "quoteAsset": "USDT", "filters": []},
    {"symbol": "XYZUSDT", "status": "BREAK", "baseAsset": "XYZ", "quoteAsset": "USDT", "filters": []},
    {"symbol": "LOWUSDT", "status": "TRADING", "baseAsset": "LOW", "quoteAsset": "USDT", "filters": []},
    {"symbol": "SOLUSDT", "status": "TRADING", "baseAsset": "SOL", "quoteAsset": "USDT", "filters": []},
    {"symbol": "BTCBTC", "status": "TRADING", "baseAsset": "ETH", "quoteAsset": "BTC", "filters": []},
]}
TICKER_SPOT = [
    {"symbol": "BTCUSDT", "lastPrice": "60000.10", "bidPrice": "60000.00", "askPrice": "60000.20", "quoteVolume": "1500000000", "priceChangePercent": "1.5", "closeTime": NOW},
    {"symbol": "ETHUSDT", "lastPrice": "3000", "bidPrice": "2999", "askPrice": "3001", "quoteVolume": "800000000", "priceChangePercent": "-0.5", "closeTime": NOW},
    {"symbol": "USDCUSDT", "lastPrice": "1", "bidPrice": "0.9999", "askPrice": "1.0001", "quoteVolume": "900000000", "closeTime": NOW},
    {"symbol": "BTCUPUSDT", "lastPrice": "10", "bidPrice": "9.9", "askPrice": "10.1", "quoteVolume": "90000000", "closeTime": NOW},
    {"symbol": "XYZUSDT", "lastPrice": "1", "bidPrice": "1", "askPrice": "1", "quoteVolume": "90000000", "closeTime": NOW},
    {"symbol": "LOWUSDT", "lastPrice": "1", "bidPrice": "1", "askPrice": "1.001", "quoteVolume": "5000000", "closeTime": NOW},
    {"symbol": "SOLUSDT", "lastPrice": "150", "bidPrice": "149.9", "askPrice": "150.1", "quoteVolume": "400000000", "closeTime": NOW},
]
EXINFO_FUT = {"symbols": [
    {"symbol": "BTCUSDT", "pair": "BTCUSDT", "contractType": "PERPETUAL", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
     "onboardDate": NOW - 2000 * 86_400_000, "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"}, {"filterType": "LOT_SIZE", "stepSize": "0.001"}]},
    {"symbol": "ETHUSDT", "contractType": "PERPETUAL", "status": "TRADING", "baseAsset": "ETH", "quoteAsset": "USDT", "onboardDate": NOW - 1500 * 86_400_000, "filters": []},
    {"symbol": "NEWUSDT", "contractType": "PERPETUAL", "status": "TRADING", "baseAsset": "NEW", "quoteAsset": "USDT", "onboardDate": NOW - 10 * 86_400_000, "filters": []},
    {"symbol": "BTCUSDT_250926", "contractType": "CURRENT_QUARTER", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT", "onboardDate": NOW - 100 * 86_400_000, "filters": []},
    {"symbol": "DOGEUSDT", "contractType": "PERPETUAL", "status": "TRADING", "baseAsset": "DOGE", "quoteAsset": "USDT", "onboardDate": NOW - 900 * 86_400_000, "filters": []},
    {"symbol": "OLDUSDT", "contractType": "PERPETUAL", "status": "SETTLING", "baseAsset": "OLD", "quoteAsset": "USDT", "onboardDate": NOW - 900 * 86_400_000, "filters": []},
]}
TICKER_FUT = [
    {"symbol": "BTCUSDT", "lastPrice": "60010", "quoteVolume": "20000000000", "priceChangePercent": "1.4", "closeTime": NOW},
    {"symbol": "ETHUSDT", "lastPrice": "3001", "quoteVolume": "9000000000", "priceChangePercent": "-0.4", "closeTime": NOW},
    {"symbol": "NEWUSDT", "lastPrice": "2", "quoteVolume": "300000000", "closeTime": NOW},
    {"symbol": "DOGEUSDT", "lastPrice": "0.1", "quoteVolume": "600000000", "closeTime": NOW},
    {"symbol": "OLDUSDT", "lastPrice": "0.1", "quoteVolume": "600000000", "closeTime": NOW},
]
BOOK_FUT = [{"symbol": "BTCUSDT", "bidPrice": "60000", "askPrice": "60001"}, {"symbol": "ETHUSDT", "bidPrice": "3000", "askPrice": "3000.5"},
            {"symbol": "DOGEUSDT", "bidPrice": "0.1000", "askPrice": "0.1010"}, {"symbol": "NEWUSDT", "bidPrice": "2", "askPrice": "2.001"}]


def test_providers_parse_binance_payloads():
    df = make_ohlcv(5)
    rows = binance_kline_rows(df)
    responses = [
        FakeResp(200, EXINFO_SPOT), FakeResp(200, rows), FakeResp(200, TICKER_SPOT[0]), FakeResp(200, {"bids": [["1", "2"]], "asks": [["1.1", "3"]]}),
        FakeResp(200, {"serverTime": NOW + 250}),
    ]
    c, sess, _ = _client(responses)
    spot = BinanceSpotProvider(c, clock_ms=lambda: NOW)
    info = spot.exchange_info()
    assert info[0]["symbol"] == "BTCUSDT" and len(info) == 8
    k = spot.klines("BTC/USDT", "4h", limit=5)
    assert list(k.columns)[:6] == ["timestamp", "open", "high", "low", "close", "volume"] and len(k) == 5
    assert k["is_closed"].iloc[-1] == (int(k["close_time"].iloc[-1]) < NOW)
    assert k["quote_volume"].notna().all() and k["trades"].iloc[0] == 123
    assert sess.calls[1][1]["symbol"] == "BTCUSDT" and sess.calls[1][1]["interval"] == "4h"
    t = spot.ticker24h(["BTC/USDT"])
    assert t[0]["lastPrice"] == "60000.10"
    d = spot.depth("BTCUSDT", 5)
    assert d["bids"][0][0] == "1"
    assert spot.server_time_ms() == NOW + 250 and spot.drift_ms == 250

    fut_responses = [
        FakeResp(200, {"symbol": "BTCUSDT", "markPrice": "60005.5", "indexPrice": "60004", "lastFundingRate": "0.0001", "nextFundingTime": NOW + 3600_000, "time": NOW}),
        FakeResp(200, [{"symbol": "BTCUSDT", "fundingTime": NOW - 8 * 3600_000, "fundingRate": "0.00012", "markPrice": "59990"}]),
        FakeResp(200, {"symbol": "BTCUSDT", "openInterest": "85000.5", "time": NOW}),
        FakeResp(200, [{"symbol": "BTCUSDT", "longShortRatio": "1.8", "longAccount": "0.64", "shortAccount": "0.36", "timestamp": NOW}]),
        FakeResp(200, [{"buySellRatio": "1.1", "buyVol": "10", "sellVol": "9", "timestamp": NOW}]),
        FakeResp(200, BOOK_FUT),
    ]
    c2, sess2, _ = _client(fut_responses)
    fut = BinanceFuturesProvider(c2, clock_ms=lambda: NOW)
    m = fut.mark_price("BTC/USDT")
    assert m["mark"] == 60005.5 and m["funding_rate"] == 0.0001 and m["next_funding_ts"] == NOW + 3600_000
    fh = fut.funding_history("BTC/USDT", limit=1)
    assert fh[0]["rate"] == 0.00012
    assert fut.open_interest("BTC/USDT")["oi"] == 85000.5
    assert fut.long_short_ratio("BTC/USDT")[0]["ratio"] == 1.8
    assert fut.taker_buy_sell_ratio("BTC/USDT")[0]["ratio"] == 1.1
    assert len(fut.book_tickers()) == 4
    assert sess2.calls[0][0].endswith("/fapi/v1/premiumIndex")
    with pytest.raises(ValueError):
        fut.klines("BTCUSDT", "7h")


def test_klines_frame_dedupes_and_flags_unclosed():
    df = make_ohlcv(3)
    rows = binance_kline_rows(df) + [binance_kline_rows(df)[-1]]
    rows[-1][6] = NOW + 5000  # son bar kapanmamış
    k = klines_frame(rows, NOW)
    assert len(k) == 3 and not bool(k["is_closed"].iloc[-1]) and bool(k["is_closed"].iloc[0])


# ------------------------------------------------------------------ universe
def test_universe_filters_and_marking(tmp_path):
    spot_p = MockProvider(tickers={t["symbol"]: t for t in TICKER_SPOT}, symbols_info=EXINFO_SPOT["symbols"], market_type="spot", name="spot")
    fut_p = MockProvider(tickers={t["symbol"]: t for t in TICKER_FUT}, symbols_info=EXINFO_FUT["symbols"], market_type="futures", name="fut",
                         books={b["symbol"]: b for b in BOOK_FUT})
    cfg = UniverseConfig(max_symbols=200)
    spot = build_spot_universe(spot_p, cfg, now_ms_=NOW)
    reasons = {e.raw: e.excluded_reason for e in spot}
    assert reasons["BTCUSDT"] is None and reasons["ETHUSDT"] is None and reasons["SOLUSDT"] is None
    assert reasons["USDCUSDT"] == "STABLE_BASE"
    assert reasons["BTCUPUSDT"] == "LEVERAGED_TOKEN"
    assert reasons["XYZUSDT"] == "STATUS_BREAK"
    assert reasons["LOWUSDT"] == "LOW_VOLUME"
    assert reasons["BTCBTC"] == "QUOTE_BTC"
    btc = next(e for e in spot if e.raw == "BTCUSDT")
    assert btc.symbol == "BTC/USDT" and btc.filters["_tick_size"] == "0.01000000" and btc.filters["_min_notional"] == "5.00000000"
    assert btc.spread_pct == pytest.approx(0.2 / 60000.1 * 100, rel=1e-3)

    fut = build_futures_universe(fut_p, cfg, now_ms_=NOW)
    fr = {e.raw: e.excluded_reason for e in fut}
    assert fr["BTCUSDT"] is None and fr["ETHUSDT"] is None
    assert fr["NEWUSDT"] == "YOUNG_LISTING"
    assert fr["BTCUSDT_250926"] == "CONTRACT_CURRENT_QUARTER"
    assert fr["DOGEUSDT"] == "WIDE_SPREAD"          # (0.101-0.1)/0.1005 ≈ %1 > 0.2
    assert fr["OLDUSDT"] == "STATUS_SETTLING"
    assert next(e for e in fut if e.raw == "NEWUSDT").listing_age_days == pytest.approx(10.0)
    assert next(e for e in fut if e.raw == "BTCUSDT").perp == "BTC/USDT:USDT"

    merged = merge_universe(spot, fut)
    tags = {e.symbol: e.tags for e in merged}
    assert "both" in tags["BTC/USDT"] and "both" in tags["ETH/USDT"]
    assert "spot_only" in tags["SOL/USDT"]
    assert all("futures_only" not in t for t in tags.values())  # bu fixture'da futures-only yok
    assert merged[0].symbol == "BTC/USDT"                       # hacme göre sıralı
    snap = UniverseSnapshot.build(spot, fut, cfg)
    assert snap.counts["spot_eligible"] == 3 and snap.counts["futures_eligible"] == 2 and snap.counts["both"] == 2
    p = snap.save(tmp_path / "state" / "universe.json")
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    assert data["counts"]["merged"] == 3 and data["merged"][0]["raw"] == "BTCUSDT"
    # max_symbols sınırı
    small = build_spot_universe(spot_p, UniverseConfig(max_symbols=1), now_ms_=NOW)
    assert sum(1 for e in small if e.excluded_reason is None) == 1
    assert next(e for e in small if e.raw == "ETHUSDT").excluded_reason == "MAX_SYMBOLS"


# ------------------------------------------------------------------ quality gate
def test_quality_gate_each_code():
    g = DataQualityGate(DataQualityConfig(min_bars=10))
    good = make_ohlcv(30)
    now = int(good["close_time"].iloc[-1]) + 1000     # son bar 1 s önce kapandı
    r = g.check_klines(good, "4h", now)
    assert r.ok and r.verdict == "OK" and r.issues == []
    # INSUFFICIENT
    assert g.check_klines(good.head(5), "4h", now).has("INSUFFICIENT_BARS")
    # MISSING (son pencere içinde → HIGH)
    gap = good.drop(index=25)
    rep = g.check_klines(gap, "4h", now)
    assert rep.has("MISSING_BARS") and rep.verdict == "DATA_INVALID" and rep.details["missing_ts"] == [int(good["timestamp"].iloc[25])]
    # DUPLICATE
    dup = pd.concat([good, good.tail(1)], ignore_index=True)
    assert g.check_klines(dup, "4h", now).has("DUPLICATE_BARS")
    # UNSORTED
    uns = good.iloc[::-1].reset_index(drop=True)
    assert g.check_klines(uns, "4h", now).has("UNSORTED")
    # UNCLOSED
    assert g.check_klines(good, "4h", now - 5000).has("UNCLOSED_LAST_BAR")
    # ZERO_PRICE
    z = good.copy(); z.loc[3, "low"] = 0.0
    assert g.check_klines(z, "4h", now).has("ZERO_PRICE")
    # STALE
    st = g.check_klines(good, "4h", now + 3 * H4)
    assert st.has("STALE_CANDLE") and not st.ok
    # snapshot
    snap = LiveSnapshot(symbol="BTC/USDT", market_type="futures", last=100.0, spread_pct=0.5, freshness_seconds=300.0, mark=101.0)
    rs = g.check_snapshot(snap, ref_price=99.0)
    assert set(rs.codes) == {"STALE_TICKER", "WIDE_SPREAD", "PRICE_DIVERGENCE", "MARK_LAST_DIVERGENCE"}
    assert rs.verdict == "DATA_INVALID"
    ok_snap = g.check_snapshot({"ticker": {"lastPrice": "100.1"}, "spread_pct": 0.01, "freshness_seconds": 2, "mark": 100.2}, ref_price=100.0)
    assert ok_snap.ok and ok_snap.verdict == "OK"
    only_spread = g.check_snapshot(LiveSnapshot("X", "spot", last=1.0, spread_pct=1.0))
    assert only_spread.verdict == "DATA_DEGRADED" and only_spread.ok
    assert g.check_snapshot(LiveSnapshot("X", "spot")).has("NO_TICKER")
    # clock
    assert g.check_clock(200).ok and g.check_clock(9000).has("CLOCK_DRIFT") and not g.check_clock(-9000).ok
    all_rep = g.check_all(good, "4h", now, snap, 100)
    assert not all_rep.ok and "PRICE_DIVERGENCE" in all_rep.codes
    assert isinstance(all_rep.to_dict()["issues"], list)


# ------------------------------------------------------------------ feed
class DictCache:
    """CandleStore benzeri bellek içi önbellek."""

    def __init__(self):
        self.frames: dict[tuple[str, str], pd.DataFrame] = {}
        self.writes = 0

    def read(self, symbol, tf, since_ms=None, until_ms=None):
        df = self.frames.get((symbol, tf))
        if df is None:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        if since_ms is not None:
            df = df[df["timestamp"] >= since_ms]
        return df.reset_index(drop=True)

    def write(self, symbol, tf, df):
        self.writes += 1
        old = self.frames.get((symbol, tf))
        new = df[["timestamp", "open", "high", "low", "close", "volume"]]
        merged = pd.concat([old, new], ignore_index=True) if old is not None else new
        self.frames[(symbol, tf)] = merged.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def test_feed_incremental_merge_and_unclosed_drop():
    full = make_ohlcv(400)                                    # son bar açık (close_time > NOW)
    assert int(full["close_time"].iloc[-1]) > NOW
    bad = MockProvider(candles={"BTC/USDT": {"4h": full}}, market_type="futures", name="bad", fail={"klines"}, clock_ms=lambda: NOW)
    good = MockProvider(candles={"BTC/USDT": {"4h": full}}, market_type="futures", name="good", clock_ms=lambda: NOW)
    cache = DictCache()
    feed = MarketFeed([bad, good], cache_store=cache, clock_ms=lambda: NOW)
    r = feed.get_klines("BTC/USDT", "4h", "futures", 300)
    assert r.source == "good" and len(r.df) == 300 and r.dropped_unclosed == 1 and r.gaps == [] and not r.is_stale
    assert bool(r.df["is_closed"].all()) and int(r.df["timestamp"].iloc[-1]) == int(full["timestamp"].iloc[-2])
    assert r.errors and r.errors[0].startswith("bad:")
    assert cache.frames[("BTC/USDT:USDT", "4h")]["timestamp"].iloc[-1] == int(full["timestamp"].iloc[-2])
    d = r.to_dict()
    assert d["bars"] == 300 and d["source"] == "good"
    # artımlı: 2 bar sonrası
    later = NOW + 2 * H4
    good.clock_ms = lambda: later
    feed.clock_ms = lambda: later
    good.calls.clear()
    r2 = feed.get_klines("BTC/USDT", "4h", "futures", 300)
    assert len(r2.df) == 300 and r2.from_cache >= 298
    start_arg = good.calls[0][4]
    assert start_arg is not None and start_arg == int(full["timestamp"].iloc[-2]) + H4   # yalnız son bardan sonrası istendi
    assert int(r2.df["timestamp"].iloc[-1]) == int(full["timestamp"].iloc[-1]) and r2.dropped_unclosed == 0
    # önbellekli veri eski kalırsa is_stale
    good.clock_ms = lambda: NOW + 12 * H4
    feed.clock_ms = lambda: NOW + 12 * H4
    r3 = feed.get_klines("BTC/USDT", "4h", "futures", 300)
    assert r3.is_stale
    # gap tespiti
    gap_df = pd.concat([full.iloc[:100], full.iloc[102:]], ignore_index=True)
    gp = MockProvider(candles={"ETH/USDT": {"4h": gap_df}}, market_type="futures", clock_ms=lambda: NOW)
    rg = MarketFeed([gp], clock_ms=lambda: NOW).get_klines("ETH/USDT", "4h", "futures", 400)
    assert rg.gaps == [int(full["timestamp"].iloc[100]), int(full["timestamp"].iloc[101])]
    # hepsi başarısız → DataQualityError
    with pytest.raises(DataQualityError):
        MarketFeed([bad], clock_ms=lambda: NOW).get_klines("BTC/USDT", "4h", "futures", 10)
    assert find_gaps([0, 10, 30], 10) == [20]


def test_feed_paging_when_n_exceeds_provider_limit():
    full = make_ohlcv(1200, tf=3_600_000)
    p = MockProvider(candles={"BTC/USDT": {"1h": full}}, market_type="spot", clock_ms=lambda: NOW)
    p.max_kline_limit = 500
    feed = MarketFeed([p], clock_ms=lambda: NOW)
    r = feed.get_klines("BTC/USDT", "1h", "spot", 1100)
    assert len(r.df) == 1100 and len(p.calls) >= 3 and r.gaps == []


def test_feed_snapshot_and_clock_drift():
    p = MockProvider(
        tickers={"BTCUSDT": {"symbol": "BTCUSDT", "lastPrice": "100", "closeTime": NOW - 30_000, "quoteVolume": "1e9", "priceChangePercent": "2"}},
        books={"BTCUSDT": {"symbol": "BTCUSDT", "bidPrice": "99.9", "askPrice": "100.1"}},
        depths={"BTCUSDT": {"bids": [["99.9", "10"], ["99.6", "10"], ["99.2", "10"], ["98", "100"]], "asks": [["100.1", "10"], ["100.4", "10"], ["100.8", "20"], ["102", "100"]]}},
        marks={"BTCUSDT": {"mark": 100.05, "index": 100.0, "funding_rate": 0.0002, "next_funding_ts": NOW + 1000}},
        ois={"BTCUSDT": {"oi": 12345.0}}, lsrs={"BTCUSDT": {"ratio": 1.4}}, takers={"BTCUSDT": {"ratio": 0.9}},
        market_type="futures", clock_ms=lambda: NOW)
    feed = MarketFeed([p], clock_ms=lambda: NOW)
    s = feed.snapshot("BTC/USDT", "futures")
    assert s.source == "mock" and s.last == 100.0 and s.spread_pct == pytest.approx(0.2, rel=1e-6)
    assert s.freshness_seconds == pytest.approx(30.0)
    assert s.depth_0_5pct == pytest.approx(99.9 * 10 + 99.6 * 10 + 100.1 * 10 + 100.4 * 10)
    assert s.depth_1pct == pytest.approx(99.9 * 10 + 99.6 * 10 + 99.2 * 10 + 100.1 * 10 + 100.4 * 10 + 100.8 * 20)
    assert 0 < s.imbalance < 0.5
    assert s.mark == 100.05 and s.funding_rate == 0.0002 and s.funding_pct == pytest.approx(0.02) and s.oi == 12345.0 and s.lsr == 1.4 and s.taker_ratio == 0.9
    assert s.errors == [] and s.to_dict()["symbol"] == "BTC/USDT"
    assert feed.clock_drift_ms() == 0
    # ticker yoksa hatalar toplanır, çökmez
    p2 = MockProvider(market_type="futures", clock_ms=lambda: NOW, fail={"ticker24h"})
    s2 = MarketFeed([p2], clock_ms=lambda: NOW).snapshot("ETH/USDT", "futures")
    assert s2.source == "" and s2.errors and s2.last is None
    assert DataQualityGate().check_snapshot(s2).has("NO_TICKER")


def test_replay_provider_never_leaks_future():
    full = make_ohlcv(100)
    t_cut = int(full["timestamp"].iloc[50]) + 1000     # 51. bar açık
    rp = ReplayProvider({("BTCUSDT", "4h"): full}, clock=t_cut)
    df = rp.klines("BTC/USDT", "4h", limit=500)
    assert int(df["timestamp"].max()) <= t_cut and len(df) == 51
    assert not bool(df["is_closed"].iloc[-1]) and bool(df["is_closed"].iloc[-2])
    feed = MarketFeed([rp], clock_ms=lambda: t_cut)
    r = feed.get_klines("BTC/USDT", "4h", "futures", 500)
    assert len(r.df) == 50 and int(r.df["timestamp"].iloc[-1]) == int(full["timestamp"].iloc[49])
    rp.set_clock(int(full["timestamp"].iloc[80]) + H4)   # 81 bar kapalı, 82. bar tam açılış anında
    df2 = rp.klines("BTC/USDT", "4h", limit=500)
    assert len(df2) == 82 and bool(df2["is_closed"].iloc[:81].all()) and not bool(df2["is_closed"].iloc[-1])
    px = rp.mark_price("BTC/USDT")["mark"]
    assert px == pytest.approx(float(full["close"].iloc[80]))


# ------------------------------------------------------------------ indicators_ext: look-ahead yok
def _all_ext(df: pd.DataFrame) -> dict[str, pd.Series]:
    h, l, c, v = df["high"], df["low"], df["close"], df["volume"]
    ml, ms, mh = ext.macd(c)
    kl, km, ku = ext.keltner(h, l, c)
    sk, sd = ext.stoch_rsi(c)
    sw = ext.swing_points(h, l, 3)
    return {
        "macd": ml, "macd_sig": ms, "macd_hist": mh, "keltner_lo": kl, "keltner_hi": ku, "vwap": ext.vwap_session(df),
        "avwap": ext.anchored_vwap(df, 10), "swing_high": sw["swing_high"], "swing_low": sw["swing_low"],
        "last_sh": sw["last_swing_high"], "stoch_k": sk, "stoch_d": sd, "roc": ext.roc(c, 12), "cci": ext.cci(h, l, c, 20),
        "obv": ext.obv(c, v), "bbw_rank": ext.bb_width_pct_rank(c, 20, 2.0, 60), "atr_rank": ext.atr_pct_rank(h, l, c, 14, 60),
        "rv": ext.realized_vol(c, 20), "dmid": ext.donchian_mid(h, l, 20), "slope": ext.ema_slope_pct(c, 20, 3),
        "sma20": ext.sma_set(c, (20,))["sma_20"], "ema50": ext.ema_set(c, (50,))["ema_50"],
    }


def test_indicators_ext_no_lookahead_perturbation():
    df = make_ohlcv(250, tf=3_600_000, seed=3)
    base = _all_ext(df)
    # 1) gelecek barları değiştir → ilk 200 bar aynı kalmalı
    pert = df.copy()
    pert.loc[200:, ["open", "high", "low", "close"]] *= 1.5
    pert.loc[200:, "volume"] *= 3
    after = _all_ext(pert)
    for name, s in base.items():
        a, b = s.iloc[:200].to_numpy(float), after[name].iloc[:200].to_numpy(float)
        assert np.allclose(a, b, equal_nan=True), f"{name}: gelecek değişikliği geçmişi etkiledi"
    # 2) prefix tutarlılığı: f(x[:n]) == f(x)[:n]
    trunc = _all_ext(df.iloc[:200].reset_index(drop=True))
    for name, s in base.items():
        assert np.allclose(s.iloc[:200].to_numpy(float), trunc[name].to_numpy(float), equal_nan=True), f"{name}: prefix tutarsız"
    # 3) swing: pivot barında değil, k bar sonra yazılır
    sw = ext.swing_points(df["high"], df["low"], 3)
    idx = sw["swing_high"].dropna().index
    assert len(idx) > 0
    for t in idx[:10]:
        i = int(sw.loc[t, "swing_high_pos"])
        assert t - i == 3 and df["high"].iloc[i] == sw.loc[t, "swing_high"]
        assert df["high"].iloc[i] > df["high"].iloc[i - 3:i].max() and df["high"].iloc[i] > df["high"].iloc[i + 1:i + 4].max()
    # 4) vwap günlük sıfırlanır (UTC)
    day_start = df[df["timestamp"] % 86_400_000 == 0].index
    if len(day_start) > 1:
        t0 = day_start[1]
        tp = (df.loc[t0, "high"] + df.loc[t0, "low"] + df.loc[t0, "close"]) / 3
        assert base["vwap"].loc[t0] == pytest.approx(tp)
    # anchored vwap: öncesi NaN, anchor barında tipik fiyat
    assert np.isnan(base["avwap"].iloc[9]) and base["avwap"].iloc[10] == pytest.approx(((df["high"] + df["low"] + df["close"]) / 3).iloc[10])
    # macd histogram = line - signal
    assert np.allclose((base["macd"] - base["macd_sig"]).dropna(), base["macd_hist"].dropna())


# ------------------------------------------------------------------ fast scanner
def test_fast_features_keys_and_funnel():
    h4 = make_ohlcv(250, seed=11)
    d1 = make_ohlcv(120, tf=86_400_000, seed=12)
    snap = LiveSnapshot("BTC/USDT", "futures", ticker={"priceChangePercent": "3.2", "quoteVolume": "5e8", "lastPrice": "100"},
                        last=100.0, spread_pct=0.02, depth_0_5pct=250_000.0, imbalance=0.55, funding_rate=0.0006, oi=1000.0, lsr=1.2, taker_ratio=1.05)
    f = fast_features(h4, d1, snap)
    for k in ("price", "ema20_4h", "ema50_4h", "ema200_4h", "above_ema200", "rsi_4h", "rsi_1d", "adx_4h", "atr_pct_4h", "roc12_4h",
              "macd_hist_4h", "vol_ratio_1", "vol_ratio_6", "bb_width_rank", "squeeze_release", "breakout_up", "breakout_down",
              "chg24_pct", "quote_volume_24h", "spread_pct", "depth_0_5pct", "imbalance", "funding_pct", "oi", "lsr", "taker_ratio",
              "last_swing_high", "last_swing_low", "up_dn_vol_ratio", "vol_direction", "bars_4h", "bars_1d"):
        assert k in f, k
    assert "error" not in f and f["funding_pct"] == pytest.approx(0.06) and f["chg24_pct"] == 3.2
    sl, ss, tags = tier1_score(f)
    assert 0 <= sl <= 100 and 0 <= ss <= 100 and "funding aşırı +" in tags
    assert fast_features(h4.head(10), d1, snap)["error"] == "insufficient_data"
    assert tier1_score({"error": "insufficient_data"}) == (0, 0, ["insufficient_data"])
    assert fast_features(h4, d1, None)["chg24_pct"] == 0.0     # snapshot opsiyonel
    # deterministik
    assert tier1_score(f) == tier1_score(fast_features(h4, d1, snap))
    # huni
    rows = [CandidateRow(symbol=f"S{i}/USDT", score_long=i * 2, score_short=i) for i in range(50)]
    rows.append(CandidateRow(symbol="ERR/USDT", error="insufficient_data"))
    quality = {"S49/USDT": DataQualityGate().check_clock(99999), "S48/USDT": {"ok": True, "verdict": "OK"}}
    res = CandidateFunnel(tier2_top=30, tier3_top=10).select(rows, quality)
    assert res.counts == {"tier1": 49, "tier2": 30, "tier3": 10, "dropped": 2}
    assert res.tier1[0].symbol == "S48/USDT" and res.tier3[-1].symbol == "S39/USDT"
    assert res.dropped["S49/USDT"].startswith("quality:DATA_INVALID:CLOCK_DRIFT") and res.dropped["ERR/USDT"].startswith("error:")
    d = res.to_dict()
    assert d["counts"]["tier3"] == 10 and d["tier3"][0]["direction"] == "LONG"
    # dict girişleri de kabul edilir
    res2 = CandidateFunnel().select([{"symbol": "A/USDT", "features": f}])
    assert len(res2.tier1) == 1 and res2.tier1[0].score == max(sl, ss)
