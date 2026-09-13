# -*- coding: utf-8 -*-
"""EMA200 TREND — tek kurallı strateji, canlı motor ile replay'in ORTAK tek kaynağı (V10).

Kural (DENEY_V9, T1/T2): son KAPANMIŞ günlük bar close > EMA200 → LONG (piyasa); close < EMA200
→ kapat. Felaket stopu: close − `atr_mult` × ATR14(1d). Hedef yok. Kaldıraç 1. T2: ayrıca BTC
rejimi UP (regime_gate ile aynı tanım) olmalı.

Ölçüm (research/entry_v1/out/DENEY_V9.md, botun kendi defterinde, funding dahil):
T2 +107% / +34% / +10% (P1/P2/P3), maksDD %22; şu anki üretim +141% / −28% / −40%. İşlem sayısı az
(21–76 / pencere), aralıklar geniş; bu KÂĞIT İLERİ TEST içindir, gerçek para kararı değildir.

Fonksiyonlar SAFTIR: `self` yok, dosya/ağ yok, girdiler değiştirilmez. Barlar KRONOLOJİK ve KAPANMIŞ
olmalı (çağıran `closed_bars` uygular).
"""
from __future__ import annotations

import math
from typing import Any

from .regime_gate import UP, btc_regime

VARIANTS = ("t1_trend", "t2_trend_regime", "m2_tsmom28")
TSMOM_LOOKBACK_DAYS = 28      # m2: close > close[-28] (Liu-Tsyvinski 1-4 haftalik zaman serisi momentumu)
DEFAULT_ATR_MULT = 3.0
MIN_DAILY_BARS = 210
_COLS = ("timestamp", "open", "high", "low", "close", "ema200", "atr14")


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def daily_rows_from_frame(frame, tail: int = 260) -> list[dict[str, Any]]:
    """Günlük DataFrame'den kural satırları (timestamp, OHLC, varsa ema200/atr14). Sütun seçimi tek yerde."""
    if frame is None:
        return []
    try:
        cols = [c for c in _COLS if c in frame.columns]
        if "close" not in cols or "timestamp" not in cols:
            return []
        return frame.tail(tail)[cols].to_dict("records")
    except (AttributeError, TypeError, ValueError, KeyError):
        return []


def _ema_last(closes: list[float], n: int = 200) -> float | None:
    if len(closes) < n:
        return None
    k = 2.0 / (n + 1.0)
    e = sum(closes[:n]) / n
    for v in closes[n:]:
        e = v * k + e * (1.0 - k)
    return e


def _atr_last(rows: list[dict[str, Any]], n: int = 14) -> float | None:
    if len(rows) < n + 1:
        return None
    trs = []
    for i in range(len(rows) - n, len(rows)):
        h, lo, pc = _f(rows[i].get("high")), _f(rows[i].get("low")), _f(rows[i - 1].get("close"))
        if None in (h, lo, pc):
            return None
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / n


def read_daily(rows: list[dict[str, Any]]) -> tuple[float, float, float] | None:
    """(close, ema200, atr14) — sütun varsa oradan, yoksa KAPANIŞLARDAN hesaplanır; ölçülemezse None."""
    if len(rows) < MIN_DAILY_BARS:
        return None
    last = rows[-1]
    close = _f(last.get("close"))
    if close is None:
        return None
    ema = _f(last.get("ema200"))
    if ema is None:
        closes = [_f(r.get("close")) for r in rows]
        if any(c is None for c in closes):
            return None
        ema = _ema_last(closes)
    atr = _f(last.get("atr14"))
    if atr is None:
        atr = _atr_last(rows)
    if ema is None or atr is None or atr <= 0:
        return None
    return close, ema, atr


def decide(variant: str, *, daily_rows: list[dict[str, Any]], btc_daily_rows: list[dict[str, Any]] | None,
           position_open: bool, atr_mult: float = DEFAULT_ATR_MULT) -> dict[str, Any] | None:
    """Tek karar: {"action": "OPEN"|"CLOSE", ...} ya da None. Bilinmeyen veri → None (fail-closed)."""
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen strateji varyanti: %r" % (variant,))
    d = read_daily(daily_rows)
    if d is None:
        return None
    close, ema, atr = d
    if variant == "m2_tsmom28":
        # DENEY_V11: 28 gunluk zaman serisi momentumu; rejim kapisi ve stop T2 ile ayni.
        ref = _f(daily_rows[-1 - TSMOM_LOOKBACK_DAYS].get("close")) if len(daily_rows) > TSMOM_LOOKBACK_DAYS else None
        if ref is None:
            return None
        above = close > ref
    else:
        above = close > ema
    if position_open:
        why = "M2_TSMOM28_CROSS_DOWN" if variant == "m2_tsmom28" else "EMA200_CROSS_DOWN"
        return {"action": "CLOSE", "reason": why, "name": variant} if not above else None
    if not above:
        return None
    regime = None
    if variant in ("t2_trend_regime", "m2_tsmom28"):
        regime = btc_regime(btc_daily_rows or [])
        if regime != UP:
            return None
    stop = close - atr_mult * atr
    if stop <= 0:
        return None
    return {"action": "OPEN", "direction": "LONG", "stop": stop, "targets": [], "leverage": 1,
            "reason": "M2_TSMOM28" if variant == "m2_tsmom28" else "EMA200_TREND", "name": variant,
            "regime": regime, "setup_type": "trend",
            "signal_close": close, "ema200": ema, "atr14": atr}


__all__ = ["DEFAULT_ATR_MULT", "MIN_DAILY_BARS", "TSMOM_LOOKBACK_DAYS", "VARIANTS", "daily_rows_from_frame", "decide", "read_daily"]
