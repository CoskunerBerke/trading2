"""P3b — kopyaların (K_*) girdisi: canlı defterin kural fonksiyonuna verdiği satırların AYNISI (SAF; G/Ç yok).

Canlı motor kuralı, perp sağlayıcısının çerçevesinden okur: `engine.TradingEngine.PERP_FRAME_LIMITS` kadar satır (oluşan
bar dahil) → oluşan bar düşer → günlük çerçeveye `indicators.ema`/`atr` ile `ema200`/`atr14` → kuralın kendi satır
okuyucusu (`paper_rules.daily_rows`/`intraday_rows`, `candle_book.window_rows`). EMA200 değeri pencerenin başına bağlıdır
(`adjust=False` tohumu), bu yüzden kopya pencereyi kısaltmaz/uzatmaz. Sayılar motorun yeniden kurma modülüyle aynıdır
(`LIVE_CLOSED_BARS`; testte `engine.TradingEngine.PERP_FRAME_LIMITS − 1`'e eşitlik). Formasyon defteri (pattern_trader)
4h analizini kendi veri servisinin penceresiyle yapar (`pattern_trader.data.BARS_PER_TF["4h"]`; testte eşitlik).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import pandas as pd

from .. import candle_book, ema200_trend, indicators, paper_rules
from ..candle_confirmation import closed_bars

#: canlı perp çerçevesinin KAPANMIŞ bar sayısı (PERP_FRAME_LIMITS − oluşan bar)
LIVE_CLOSED_BARS: Mapping[str, int] = {"1d": 399, "4h": 699, "1h": 499, "5m": 499, "15m": 499}
#: Formasyon defterinin 4h analiz penceresi (pattern_trader veri servisi)
FM_BARS_4H = 240
_COLS = ("timestamp", "open", "high", "low", "close", "volume")


def last_closed(bars: Sequence[Mapping[str, Any]] | None, n: int) -> list[Mapping[str, Any]]:
    seq = list(bars or ())
    return seq[-int(n):] if n > 0 else []


def frame(bars: Sequence[Mapping[str, Any]] | None, tf: str, *, daily_indicators: bool = False) -> pd.DataFrame | None:
    """Canlı çerçevenin kapanmış kısmı (son `LIVE_CLOSED_BARS[tf]` bar) DataFrame olarak; günlükte `ema200`/`atr14`."""
    rows = last_closed(bars, LIVE_CLOSED_BARS[tf])
    if not rows:
        return None
    df = pd.DataFrame([{k: (int(r[k]) if k == "timestamp" else float(r.get(k) or 0.0) if k == "volume" else float(r[k]))
                        for k in _COLS} for r in rows])
    if daily_indicators:
        df["ema200"] = indicators.ema(df["close"], 200)
        df["atr14"] = indicators.atr(df["high"], df["low"], df["close"], 14)
    return df


def daily_rows(bars_1d: Sequence[Mapping[str, Any]] | None, now_ms: int) -> list[dict[str, Any]]:
    """`paper_rules.daily_rows` — canlı `step` ve gösterimle AYNI okuma."""
    return paper_rules.daily_rows({"1d": frame(bars_1d, "1d", daily_indicators=True)}, now_ms=int(now_ms))


def intraday_rows(bars_tf: Sequence[Mapping[str, Any]] | None, tf: str, now_ms: int) -> list[dict[str, Any]]:
    return paper_rules.intraday_rows({tf: frame(bars_tf, tf)}, tf=tf, now_ms=int(now_ms))


def candle_rows(bars_4h: Sequence[Mapping[str, Any]] | None, now_ms: int) -> list[dict[str, Any]]:
    """Mum varyasyonlarının penceresi: `candle_book.window_rows` (son 500 kapanmış 4h bar, hacim dahil)."""
    return candle_book.window_rows({candle_book.TIMEFRAME: frame(bars_4h, candle_book.TIMEFRAME)}, now_ms=int(now_ms))


def btc_daily_rows(btc_1d: Sequence[Mapping[str, Any]] | None, now_ms: int) -> list[dict[str, Any]]:
    """BTC rejim satırları: canlı `step` ile aynı — `closed_bars(ema200_trend.daily_rows_from_frame(btc_1d))`."""
    fr = frame(btc_1d, "1d", daily_indicators=True)
    return closed_bars(ema200_trend.daily_rows_from_frame(fr), now_ms=int(now_ms), tf="1d")


def fm_rows(bars_4h: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Formasyon v3 penceresi: son `FM_BARS_4H` kapanmış 4h bar (sözlük; analiz ve RSI aynı satırları okur)."""
    return [{k: (int(r[k]) if k == "timestamp" else float(r.get(k) or 0.0) if k == "volume" else float(r[k])) for k in _COLS}
            for r in last_closed(bars_4h, FM_BARS_4H)]


__all__ = ["FM_BARS_4H", "LIVE_CLOSED_BARS", "btc_daily_rows", "candle_rows", "daily_rows", "fm_rows", "frame",
           "intraday_rows", "last_closed"]
