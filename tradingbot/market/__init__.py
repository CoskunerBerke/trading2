"""Piyasa verisi katmanı (v3) — sağlayıcılar, akış, kalite kapısı, evren, hızlı tarayıcı, oran bütçesi, HTTP.

Hızlı başlangıç (paper, yalnızca public uç noktalar):
    from tradingbot.market import (BinanceFuturesProvider, BinanceSpotProvider, HttpClient, BudgetPool, MarketFeed,
                                   DataQualityGate, UniverseConfig, build_universe, fast_features, tier1_score, CandidateFunnel)
    pool = BudgetPool()
    fut = BinanceFuturesProvider(HttpClient(BinanceFuturesProvider.base_url, pool.get("fapi.binance.com")))
    spot = BinanceSpotProvider(HttpClient(BinanceSpotProvider.base_url, pool.get("api.binance.com")))
    feed = MarketFeed([fut, spot])
    res = feed.get_klines("BTC/USDT", "4h", "futures", 300)     # kapalı barlar, gaps, is_stale
    snap = feed.snapshot("BTC/USDT", "futures")
    rep = DataQualityGate().check_all(res.df, "4h", res.fetched_at, snap, feed.clock_drift_ms("futures"))
"""
from .feed import KlinesResult, LiveSnapshot, MarketFeed, find_gaps, normalize_frame
from .http import HttpClient, HttpError, TransientHttpError
from .providers import (
    FUTURES,
    KLINE_COLUMNS,
    SPOT,
    BinanceFuturesProvider,
    BinanceSpotProvider,
    CcxtFallbackProvider,
    MarketDataProvider,
    MockProvider,
    ReplayProvider,
    TradingViewProvider,
    frame_from_ohlcv,
    klines_frame,
    now_ms,
    tf_ms,
    to_raw,
)
from .quality import DataQualityConfig, DataQualityGate, QualityIssue, QualityReport, merge_reports
from .ratelimit import BannedError, BudgetPool, RateBudget, RateLimitedError, backoff
from .scanner_fast import CandidateFunnel, CandidateRow, FunnelResult, fast_features, tier1_score
from .universe import (
    UniverseConfig,
    UniverseEntry,
    UniverseSnapshot,
    build_futures_universe,
    build_spot_universe,
    build_universe,
    eligible,
    merge_universe,
)

__all__ = [
    # providers
    "MarketDataProvider", "BinanceSpotProvider", "BinanceFuturesProvider", "TradingViewProvider", "CcxtFallbackProvider",
    "MockProvider", "ReplayProvider", "klines_frame", "frame_from_ohlcv", "to_raw", "tf_ms", "now_ms", "KLINE_COLUMNS", "SPOT", "FUTURES",
    # feed
    "MarketFeed", "KlinesResult", "LiveSnapshot", "normalize_frame", "find_gaps",
    # quality
    "DataQualityConfig", "DataQualityGate", "QualityReport", "QualityIssue", "merge_reports",
    # universe
    "UniverseConfig", "UniverseEntry", "UniverseSnapshot", "build_spot_universe", "build_futures_universe", "merge_universe",
    "build_universe", "eligible",
    # scanner
    "fast_features", "tier1_score", "CandidateFunnel", "CandidateRow", "FunnelResult",
    # ratelimit / http
    "RateBudget", "BudgetPool", "RateLimitedError", "BannedError", "backoff", "HttpClient", "HttpError", "TransientHttpError",
]
