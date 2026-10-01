# -*- coding: utf-8 -*-
"""Uc giris hipotezi — ACIK, DETERMINISTIK, yalniz KAPANMIS barlardan.

Uretim agacinda DEGIL: arastirma hipotezleridir. Motor yalniz `entry_rule` kancasini cagirir;
cikis geometrisi, basa-bas kurali, maliyet modeli, sermaye ve risk politikasi DEGISMEZ.

Imza: `(symbol, decision, plan, frames) -> (allow: bool, reason: str)`.
`frames` = {"1d": df, "4h": df, "1h": df}; `HistoricalReplay._slice` yalniz karar aninda
KAPANMIS barlari verir, bu yuzden kural gelecege bakamaz.

NEDEN AJAN METRIKLERI KULLANILMIYOR: replay'de yalniz 10 uzman calisiyor (olculdu) ve
momentum/candles/levels/volume ajanlari YOK — bunlar canli yolda `legacy_brief` uzerinden
geliyor, replay ise onu vermiyor. Bu yuzden kurallar gostergelerini DOGRUDAN cerceveden
hesaplar; boylece ayni kural iki motorda da ayni girdiyle calisabilir.
"""
from __future__ import annotations

import math


def _last(df, col):
    if df is None or col not in df or len(df) == 0:
        return None
    try:
        v = float(df[col].iloc[-1])
    except (TypeError, ValueError, IndexError):
        return None
    return v if v == v and math.isfinite(v) else None


def ema_slope_pct(df, col: str = "ema50", bars: int = 5):
    """Son `bars` barda EMA'nin yuzde egimi. Yon isareti icin yeterli, olcek icin degil."""
    if df is None or col not in df or len(df) <= bars:
        return None
    try:
        a, b = float(df[col].iloc[-bars - 1]), float(df[col].iloc[-1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (a == a and b == b) or a == 0:
        return None
    return (b / a - 1.0) * 100.0


def clv(df):
    """Close Location Value: mumun govdesinde kapanis nerede? -1 (dip) .. +1 (tepe)."""
    if df is None or len(df) == 0:
        return None
    r = df.iloc[-1]
    try:
        h, l, c = float(r["high"]), float(r["low"]), float(r["close"])
    except (TypeError, ValueError, KeyError):
        return None
    if not (h > l):
        return 0.0
    return ((c - l) - (h - c)) / (h - l)


def bb_width_rank(df, length: int = 20, window: int = 120):
    """Bollinger genisliginin son `window` bardaki YUZDELIK SIRASI (0-100). Dusuk = sikisma."""
    if df is None or len(df) < max(length, window) + 2:
        return None
    c = df["close"]
    ma = c.rolling(length).mean()
    sd = c.rolling(length).std()
    width = (4.0 * sd / ma) * 100.0
    tail = width.tail(window).dropna()
    if len(tail) < 20:
        return None
    cur = tail.iloc[-1]
    if cur != cur:
        return None
    return float((tail <= cur).sum()) / float(len(tail)) * 100.0


def range_position(df, length: int = 20):
    """Donchian aralik konumu: 0 = alt uc, 1 = ust uc."""
    if df is None or len(df) < length + 1:
        return None
    hi = float(df["high"].tail(length).max())
    lo = float(df["low"].tail(length).min())
    c = float(df["close"].iloc[-1])
    if not (hi > lo):
        return None
    return (c - lo) / (hi - lo)


def vol_ratio(df, length: int = 20):
    """Son barin hacmi / son `length` barin ortalama hacmi."""
    if df is None or "volume" not in df or len(df) < length + 1:
        return None
    m = float(df["volume"].tail(length).mean())
    v = float(df["volume"].iloc[-1])
    if not (m > 0):
        return None
    return v / m


# --------------------------------------------------------------------------- H1
def h1_trend_pullback(rsi_h1_long: float = 45.0, clv_min: float = 0.20):
    """4h trendiyle uyumlu 1h geri cekilme + kapanmis 4h mumunda donus teyidi.

    LONG : rejim ∈ {TREND_UP, BREAKOUT}, EMA50(1d) egimi > 0, RSI(1h) ≤ rsi, CLV(4h) ≥ clv.
    SHORT: rejim ∈ {TREND_DOWN, BREAKOUT}, EMA50(1d) egimi < 0, RSI(1h) ≥ 100-rsi, CLV(4h) ≤ -clv.
    """
    def rule(symbol, d, plan, frames):
        side = str(getattr(d, "direction", "") or "").upper()
        reg = str(getattr(d, "regime", "") or "")
        slope = ema_slope_pct(frames.get("1d"))
        rsi1h = _last(frames.get("1h"), "rsi14")
        c4 = clv(frames.get("4h"))
        if slope is None or rsi1h is None or c4 is None:
            return False, "H1_MISSING_FEATURE"
        if side == "LONG":
            if reg not in ("TREND_UP", "BREAKOUT"):
                return False, "H1_REGIME"
            if slope <= 0:
                return False, "H1_SLOPE"
            if rsi1h > rsi_h1_long:
                return False, "H1_NO_PULLBACK"
            if c4 < clv_min:
                return False, "H1_NO_REVERSAL"
            return True, ""
        if side == "SHORT":
            if reg not in ("TREND_DOWN", "BREAKOUT"):
                return False, "H1_REGIME"
            if slope >= 0:
                return False, "H1_SLOPE"
            if rsi1h < (100.0 - rsi_h1_long):
                return False, "H1_NO_PULLBACK"
            if c4 > -clv_min:
                return False, "H1_NO_REVERSAL"
            return True, ""
        return False, "H1_SIDE"
    return rule


# --------------------------------------------------------------------------- H2
def h2_squeeze_breakout(bb_rank_max: float = 35.0, vol_ratio_min: float = 1.30):
    """Sikisma sonrasi seviye kirilimi + goreli hacim teyidi (hepsi 4h)."""
    def rule(symbol, d, plan, frames):
        side = str(getattr(d, "direction", "") or "").upper()
        f4 = frames.get("4h")
        bb, pos, vol = bb_width_rank(f4), range_position(f4), vol_ratio(f4)
        if bb is None or pos is None or vol is None:
            return False, "H2_MISSING_FEATURE"
        if bb > bb_rank_max:
            return False, "H2_NO_SQUEEZE"
        if vol < vol_ratio_min:
            return False, "H2_NO_VOLUME"
        if side == "LONG":
            return (True, "") if pos >= 0.80 else (False, "H2_NOT_AT_HIGH")
        if side == "SHORT":
            return (True, "") if pos <= 0.20 else (False, "H2_NOT_AT_LOW")
        return False, "H2_SIDE"
    return rule


# --------------------------------------------------------------------------- H3
def h3_range_reversion(pos_edge: float = 0.20, rsi_edge: float = 35.0):
    """YALNIZ yatay rejimde ortalamaya donus; trend rejiminde HIC islem uretmez."""
    def rule(symbol, d, plan, frames):
        side = str(getattr(d, "direction", "") or "").upper()
        if str(getattr(d, "regime", "") or "") != "RANGE":
            return False, "H3_NOT_RANGE"
        f4 = frames.get("4h")
        pos, rsi4 = range_position(f4), _last(f4, "rsi14")
        if pos is None or rsi4 is None:
            return False, "H3_MISSING_FEATURE"
        if side == "LONG":
            return (True, "") if (pos <= pos_edge and rsi4 <= rsi_edge) else (False, "H3_NOT_OVERSOLD")
        if side == "SHORT":
            return (True, "") if (pos >= 1 - pos_edge and rsi4 >= 100 - rsi_edge) else (False, "H3_NOT_OVERBOUGHT")
        return False, "H3_SIDE"
    return rule


#: ON KAYITLI IZGARA — toplam 18 yapilandirma (hipotez basina 6: 3 x 2).
GRID = {
    "H1": [("h1_r%g_c%g" % (r, c), h1_trend_pullback, {"rsi_h1_long": r, "clv_min": c})
           for r in (40.0, 45.0, 50.0) for c in (0.10, 0.20)],
    "H2": [("h2_b%g_v%g" % (b, v), h2_squeeze_breakout, {"bb_rank_max": b, "vol_ratio_min": v})
           for b in (25.0, 35.0, 45.0) for v in (1.15, 1.30)],
    "H3": [("h3_p%g_r%g" % (p, r), h3_range_reversion, {"pos_edge": p, "rsi_edge": r})
           for p in (0.15, 0.20, 0.25) for r in (30.0, 35.0)],
}


def build(name: str):
    for fams in GRID.values():
        for nm, fn, kw in fams:
            if nm == name:
                return fn(**kw)
    raise KeyError(name)


def all_names() -> list[str]:
    return [nm for fams in GRID.values() for nm, _, _ in fams]
