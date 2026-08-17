"""Saf pandas/numpy teknik göstergeler (harici TA kütüphanesi gerekmez)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0.0, 100.0)
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = true_range(high, low, close)
    atr_ = tr.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean() / atr_
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean() / atr_
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0):
    mid = sma(close, length)
    std = close.rolling(length, min_periods=length).std(ddof=0)
    return mid - mult * std, mid, mid + mult * std


def donchian(high: pd.Series, low: pd.Series, length: int = 20):
    """Önceki barlara göre kanal (mevcut bar dahil edilmez → look-ahead yok)."""
    upper = high.rolling(length, min_periods=length).max().shift(1)
    lower = low.rolling(length, min_periods=length).min().shift(1)
    return lower, upper


def add_snapshot_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Analiz düğümünün kullandığı standart gösterge seti."""
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["rsi14"] = rsi(out["close"], 14)
    out["atr14"] = atr(out["high"], out["low"], out["close"], 14)
    out["adx14"] = adx(out["high"], out["low"], out["close"], 14)
    out["atr_pct"] = 100.0 * out["atr14"] / out["close"]
    return out
