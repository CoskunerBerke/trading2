"""Genişletilmiş göstergeler — saf pandas/numpy, tamamı NEDENSEL (causal): t anındaki değer yalnızca ≤ t verisine bağlıdır.

Kurallar: `ewm(adjust=False)`, `rolling(min_periods=n)`, asla `shift(-k)` / `center=True` yok. Salınım (swing) noktaları
pivot barından k bar SONRA (onay barında) yazılır; böylece "gelecekten haber" sızmaz. `tests/test_market.py`
içindeki perturbasyon testi bu sözleşmeyi (prefix tutarlılığı) doğrular.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from .indicators import atr, donchian, ema, rsi, sma


# ---------------------------------------------------------------- hareketli ortalama setleri
def sma_set(close: pd.Series, lengths: Iterable[int] = (20, 50, 200)) -> pd.DataFrame:
    return pd.DataFrame({f"sma_{n}": sma(close, int(n)) for n in lengths}, index=close.index)


def ema_set(close: pd.Series, lengths: Iterable[int] = (20, 50, 200)) -> pd.DataFrame:
    return pd.DataFrame({f"ema_{n}": ema(close, int(n)) for n in lengths}, index=close.index)


def ema_slope_pct(close: pd.Series, length: int = 20, lookback: int = 3) -> pd.Series:
    """EMA'nın `lookback` bar önceki değerine göre yüzde eğimi."""
    e = ema(close, length)
    prev = e.shift(lookback)
    return (e - prev) / prev.replace(0.0, np.nan) * 100.0


# ---------------------------------------------------------------- momentum
def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(macd_line, signal_line, histogram)."""
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def stoch_rsi(close: pd.Series, rsi_len: int = 14, stoch_len: int = 14, k: int = 3, d: int = 3) -> tuple[pd.Series, pd.Series]:
    """Stochastic RSI (0-100). Dönen: (%K, %D)."""
    r = rsi(close, rsi_len)
    lo = r.rolling(stoch_len, min_periods=stoch_len).min()
    hi = r.rolling(stoch_len, min_periods=stoch_len).max()
    raw = (r - lo) / (hi - lo).replace(0.0, np.nan) * 100.0
    kk = raw.rolling(k, min_periods=k).mean()
    dd = kk.rolling(d, min_periods=d).mean()
    return kk, dd


def roc(close: pd.Series, length: int = 12) -> pd.Series:
    """Yüzde değişim (rate of change) `length` bar."""
    prev = close.shift(length)
    return (close - prev) / prev.replace(0.0, np.nan) * 100.0


def cci(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    m = tp.rolling(length, min_periods=length).mean()
    md = tp.rolling(length, min_periods=length).apply(lambda x: float(np.abs(x - x.mean()).mean()), raw=True)
    return (tp - m) / (0.015 * md.replace(0.0, np.nan))


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume.fillna(0.0)).cumsum()


# ---------------------------------------------------------------- VWAP
def _typical(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"] + df["close"]) / 3.0


def _day_key(df: pd.DataFrame) -> pd.Series:
    """UTC gün anahtarı; `timestamp` (ms) sütunu varsa ondan, yoksa DatetimeIndex'ten."""
    if "timestamp" in df.columns:
        return (df["timestamp"].astype("int64") // 86_400_000)
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index.tz_convert("UTC") if df.index.tz is not None else df.index.tz_localize("UTC")
        return pd.Series(idx.floor("D").asi8 // 86_400_000_000_000, index=df.index)
    raise ValueError("vwap_session: 'timestamp' sütunu ya da DatetimeIndex gerekli")


def vwap_session(df: pd.DataFrame) -> pd.Series:
    """UTC gün başına sıfırlanan hacim ağırlıklı ortalama fiyat (tipik fiyat × hacim kümülatif)."""
    tp = _typical(df)
    vol = df["volume"].astype(float).fillna(0.0)
    key = _day_key(df)
    pv = (tp * vol).groupby(key.values).cumsum()
    vv = vol.groupby(key.values).cumsum()
    out = pv / vv.replace(0.0, np.nan)
    out.index = df.index
    return out


def anchored_vwap(df: pd.DataFrame, anchor_idx: int) -> pd.Series:
    """`anchor_idx` (konumsal) barından itibaren kümülatif VWAP; öncesi NaN."""
    n = len(df)
    out = pd.Series(np.nan, index=df.index, dtype=float)
    if n == 0:
        return out
    a = int(anchor_idx)
    if a < 0:
        a = max(0, n + a)
    if a >= n:
        return out
    tp = _typical(df).to_numpy(float)[a:]
    vol = df["volume"].astype(float).fillna(0.0).to_numpy()[a:]
    pv = np.cumsum(tp * vol)
    vv = np.cumsum(vol)
    with np.errstate(divide="ignore", invalid="ignore"):
        vals = np.where(vv > 0, pv / vv, np.nan)
    out.iloc[a:] = vals
    return out


# ---------------------------------------------------------------- volatilite / kanallar
def keltner(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20, mult: float = 1.5,
            atr_len: int = 10) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(lower, mid, upper) — EMA orta bant ± mult × ATR."""
    mid = ema(close, length)
    a = atr(high, low, close, atr_len)
    return mid - mult * a, mid, mid + mult * a


def bb_width_pct_rank(close: pd.Series, length: int = 20, mult: float = 2.0, window: int = 120) -> pd.Series:
    """Bollinger bant genişliğinin (upper-lower)/mid son `window` bar içindeki yüzdelik sırası (0-1)."""
    mid = sma(close, length)
    std = close.rolling(length, min_periods=length).std(ddof=0)
    bbw = (2.0 * mult * std) / mid.replace(0.0, np.nan)
    return bbw.rolling(window, min_periods=max(5, window // 4)).rank(pct=True)


def atr_pct_rank(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14, window: int = 120) -> pd.Series:
    """ATR% (ATR/close×100) değerinin son `window` bardaki yüzdelik sırası (0-1)."""
    ap = atr(high, low, close, length) / close.replace(0.0, np.nan) * 100.0
    return ap.rolling(window, min_periods=max(5, window // 4)).rank(pct=True)


def realized_vol(close: pd.Series, window: int = 20, periods_per_year: float | None = None) -> pd.Series:
    """Log getiri standart sapması (bar bazlı); `periods_per_year` verilirse yıllıklandırılır."""
    lr = np.log(close / close.shift(1))
    rv = lr.rolling(window, min_periods=window).std(ddof=1)
    if periods_per_year:
        rv = rv * float(np.sqrt(periods_per_year))
    return rv


def donchian_mid(high: pd.Series, low: pd.Series, length: int = 20) -> pd.Series:
    lower, upper = donchian(high, low, length)
    return (lower + upper) / 2.0


# ---------------------------------------------------------------- salınım noktaları
def swing_points(high: pd.Series, low: pd.Series, k: int = 3) -> pd.DataFrame:
    """Onaylı pivotlar. Bir pivot yüksek `i` barında oluşur, ancak `i+k` barında ONAYLANIR ve o barda yazılır.

    Sütunlar (index = giriş indexi):
      swing_high, swing_low       : onay barındaki pivot fiyatı (yoksa NaN)
      swing_high_pos, swing_low_pos: pivot barının konumsal indeksi (yoksa NaN)
      last_swing_high, last_swing_low: en son onaylanmış pivot seviyeleri (ileri doldurma; nedensel)
    """
    k = max(1, int(k))
    h = high.to_numpy(float)
    lo = low.to_numpy(float)
    n = len(h)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    shp = np.full(n, np.nan)
    slp = np.full(n, np.nan)
    for t in range(2 * k, n):
        i = t - k
        seg_h = h[i - k:i + k + 1]
        seg_l = lo[i - k:i + k + 1]
        if np.isnan(seg_h).any() or np.isnan(seg_l).any():
            continue
        c_h = h[i]
        if c_h > np.max(seg_h[:k]) and c_h > np.max(seg_h[k + 1:]):
            sh[t] = c_h
            shp[t] = i
        c_l = lo[i]
        if c_l < np.min(seg_l[:k]) and c_l < np.min(seg_l[k + 1:]):
            sl[t] = c_l
            slp[t] = i
    out = pd.DataFrame({"swing_high": sh, "swing_low": sl, "swing_high_pos": shp, "swing_low_pos": slp}, index=high.index)
    out["last_swing_high"] = out["swing_high"].ffill()
    out["last_swing_low"] = out["swing_low"].ffill()
    return out


__all__ = ["sma_set", "ema_set", "ema_slope_pct", "macd", "stoch_rsi", "roc", "cci", "obv", "vwap_session", "anchored_vwap",
           "keltner", "bb_width_pct_rank", "atr_pct_rank", "realized_vol", "donchian_mid", "swing_points"]
