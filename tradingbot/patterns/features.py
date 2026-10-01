"""Causal feature store — her satır yalnız o barın KAPANIŞINA kadar bilinen veriden hesaplanır (rolling/shift; ileri bakış yok).

Satır alanları: event_ts (bar açılış ms), cutoff_ts (bar kapanış ms = bilgi kesimi), schema_version, source, quality (dolu özellik oranı),
miss_<grup> maskeleri. Gelecek mumlar değişince/eklenince geçmiş satırlar DEĞİŞMEZ (test: future-mutation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import adx, atr, bollinger, ema, rsi, sma
from ..indicators_ext import macd, realized_vol, roc, stoch_rsi
from ..market.providers import tf_ms

FEATURE_SCHEMA_VERSION = 1
GROUPS = ("trend", "momentum", "volatility", "volume", "candle", "futures", "context")
MA_LENS = (9, 25, 50, 99, 200)
EMA_LENS = (9, 21, 25, 50, 99, 200)


def _pct(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a / b - 1.0) * 100.0


def _asof_join(base_ts: pd.Series, other: pd.DataFrame, col: str, ts_col: str = "timestamp") -> pd.Series:
    """`other[col]` değerini base her satırı için ≤ cutoff bilinen SON değerle eşle (ileri bakış yok)."""
    if other is None or other.empty or col not in other.columns:
        return pd.Series(np.nan, index=base_ts.index)
    o = other[[ts_col, col]].dropna().sort_values(ts_col)
    idx = np.searchsorted(o[ts_col].values, base_ts.values, side="right") - 1
    vals = np.where(idx >= 0, o[col].values[np.clip(idx, 0, len(o) - 1)], np.nan)
    return pd.Series(vals, index=base_ts.index, dtype="float64")


def build_feature_frame(df: pd.DataFrame, tf: str, *, btc_df: pd.DataFrame | None = None, funding_df: pd.DataFrame | None = None,
                        oi_df: pd.DataFrame | None = None, source: str = "history") -> pd.DataFrame:
    """df: timestamp/open/high/low/close/volume [+quote_volume,trades,taker_buy_base]. Dönen frame df ile aynı uzunlukta."""
    d = df.sort_values("timestamp").reset_index(drop=True)
    o, h, l, c, v = (d[k].astype(float) for k in ("open", "high", "low", "close", "volume"))
    step = tf_ms(tf)
    out = pd.DataFrame({"event_ts": d["timestamp"].astype("int64"), "cutoff_ts": d["timestamp"].astype("int64") + step - 1})
    ret = c.pct_change() * 100
    # --- trend
    for n in MA_LENS:
        m = sma(c, n)
        out[f"sma{n}_dist"] = _pct(c, m)
        out[f"sma{n}_slope"] = m.pct_change(3) * 100
    for n in EMA_LENS:
        e = ema(c, n)
        out[f"ema{n}_dist"] = _pct(c, e)
        out[f"ema{n}_slope"] = e.pct_change(3) * 100
    out["ema9_21_cross"] = np.sign(ema(c, 9) - ema(c, 21))
    out["sma25_99_cross"] = np.sign(sma(c, 25) - sma(c, 99))
    above = (c > ema(c, 25)).astype(int)
    out["trend_persist"] = above.groupby((above != above.shift()).cumsum()).cumcount() + 1
    out["trend_persist"] = out["trend_persist"] * np.where(above == 1, 1, -1)
    out["hh20"] = (h > h.shift(1).rolling(20).max()).astype(float)
    out["ll20"] = (l < l.shift(1).rolling(20).min()).astype(float)
    out["sup_dist"] = _pct(c, l.rolling(50).min())
    out["res_dist"] = _pct(h.rolling(50).max(), c)
    # --- momentum
    out["rsi14"] = rsi(c, 14)
    ml, ms, mh = macd(c)
    out["macd_hist"] = mh / c * 100
    k, dd = stoch_rsi(c)
    out["stoch_k"] = k
    out["roc12"] = roc(c, 12)
    out["adx14"] = adx(h, l, c, 14)
    out["rsi_div"] = np.sign(out["rsi14"].diff(5)) - np.sign(c.diff(5))          # sayısal diverjans: fiyat↑ rsi↓ → -2
    # --- volatility
    a = atr(h, l, c, 14)
    out["atr_pct"] = a / c * 100
    rv20 = realized_vol(c, 20)
    out["rv20"] = rv20
    out["rv_ratio"] = rv20 / realized_vol(c, 60)
    bl, bm, bu = bollinger(c, 20, 2.0)
    out["bb_width"] = (bu - bl) / bm * 100
    out["vol_pctile"] = rv20.rolling(120, min_periods=30).rank(pct=True) * 100
    out["down_vol"] = ret.where(ret < 0, 0.0).rolling(20).std()
    out["dd50"] = _pct(c, c.rolling(50).max())
    # --- volume/likidite
    vz = (v - v.rolling(20).mean()) / v.rolling(20).std().replace(0, np.nan)
    out["vol_z"] = vz
    out["quote_vol"] = d["quote_volume"].astype(float) if "quote_volume" in d.columns else v * c
    out["vol_trend"] = v.rolling(5).mean() / v.rolling(20).mean()
    out["rel_vol"] = v / v.rolling(50).mean()
    out["candle_impact"] = ret.abs() / (out["rel_vol"].replace(0, np.nan))
    out["taker_buy_ratio"] = (d["taker_buy_base"].astype(float) / v.replace(0, np.nan)) if "taker_buy_base" in d.columns else np.nan
    # --- candle
    rng = (h - l).replace(0, np.nan)
    body = (c - o)
    out["body_range"] = body.abs() / rng
    out["upper_wick"] = (h - np.maximum(o, c)) / rng
    out["lower_wick"] = (np.minimum(o, c) - l) / rng
    out["close_loc"] = (c - l) / rng
    dirn = np.sign(body)
    out["consec_dir"] = dirn.groupby((dirn != dirn.shift()).cumsum()).cumcount() + 1
    out["consec_dir"] = out["consec_dir"] * dirn
    out["breakout20"] = (c > h.shift(1).rolling(20).max()).astype(float) - (c < l.shift(1).rolling(20).min()).astype(float)
    out["rejection"] = ((out["upper_wick"] > 2 * out["body_range"]) | (out["lower_wick"] > 2 * out["body_range"])).astype(float)
    out["compression"] = (rng / a).rolling(5).mean()
    out["failed_breakout"] = ((h > h.shift(1).rolling(20).max()) & (c < h.shift(1).rolling(20).max())).astype(float)
    # --- futures
    fr = _asof_join(out["cutoff_ts"], funding_df, "rate") if funding_df is not None else pd.Series(np.nan, index=out.index)
    out["funding"] = fr * 100
    out["funding_pctile"] = fr.rolling(90, min_periods=10).rank(pct=True) * 100 if funding_df is not None else np.nan
    if oi_df is not None:
        oi = _asof_join(out["cutoff_ts"], oi_df, "oi")
        out["oi_chg"] = oi.pct_change(6) * 100
    else:
        out["oi_chg"] = np.nan
    out["squeeze_proxy"] = (out["funding_pctile"] - 50).abs() / 50 * (out["rv_ratio"].fillna(1))
    # --- bağlam (BTC)
    if btc_df is not None and not btc_df.empty:
        b = btc_df.sort_values("timestamp")
        bc = _asof_join(out["cutoff_ts"], b.assign(cl=b["close"].astype(float)), "cl")
        out["btc_ret20"] = bc.pct_change(20) * 100
        be = ema(bc, 50)
        out["btc_regime"] = np.sign(be.diff(5))
        cr = c.pct_change()
        br = bc.pct_change()
        out["btc_corr30"] = cr.rolling(30).corr(br)
        out["btc_beta30"] = cr.rolling(30).cov(br) / br.rolling(30).var().replace(0, np.nan)
    else:
        for k_ in ("btc_ret20", "btc_regime", "btc_corr30", "btc_beta30"):
            out[k_] = np.nan
    out["ret1"] = ret
    # --- şema/maskeler
    grp_cols = {"trend": [k for k in out.columns if k.startswith(("sma", "ema", "trend_", "hh20", "ll20", "sup_", "res_"))],
                "momentum": ["rsi14", "macd_hist", "stoch_k", "roc12", "adx14", "rsi_div"],
                "volatility": ["atr_pct", "rv20", "rv_ratio", "bb_width", "vol_pctile", "down_vol", "dd50"],
                "volume": ["vol_z", "quote_vol", "vol_trend", "rel_vol", "candle_impact", "taker_buy_ratio"],
                "candle": ["body_range", "upper_wick", "lower_wick", "close_loc", "consec_dir", "breakout20", "rejection", "compression", "failed_breakout"],
                "futures": ["funding", "funding_pctile", "oi_chg", "squeeze_proxy"],
                "context": ["btc_ret20", "btc_regime", "btc_corr30", "btc_beta30"]}
    for g, cols in grp_cols.items():
        out[f"miss_{g}"] = out[cols].isna().mean(axis=1) if cols else 1.0
    feat_cols = [c_ for cols in grp_cols.values() for c_ in cols]
    out["quality"] = 1.0 - out[feat_cols].isna().mean(axis=1)
    out["schema_version"] = FEATURE_SCHEMA_VERSION
    out["source"] = source
    return out


def feature_columns(frame: pd.DataFrame) -> list[str]:
    skip = {"event_ts", "cutoff_ts", "schema_version", "source", "quality"}
    return [c for c in frame.columns if c not in skip and not c.startswith("miss_")]


__all__ = ["build_feature_frame", "feature_columns", "FEATURE_SCHEMA_VERSION", "GROUPS"]
