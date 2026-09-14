# -*- coding: utf-8 -*-
"""V11 — momentum tanımı varyantları (YALNIZ ARAŞTIRMA; üretime girmez).

T2 ile tek fark sinyal: M1 EMA100, M2 close > close[-28], M3 close > close[-91]. BTC rejim kapısı
(EMA200), stop 3×ATR14(1d), kaldıraç 1 aynı. Replay strateji modu + `apply_action` ile koşar.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:/Users/berke/wt-entry")))

from tradingbot.candle_confirmation import closed_bars  # noqa: E402
from tradingbot.ema200_trend import DEFAULT_ATR_MULT, MIN_DAILY_BARS, daily_rows_from_frame, read_daily  # noqa: E402
from tradingbot.regime_gate import BTC_SYMBOL, UP, btc_regime  # noqa: E402

H4_MS = 4 * 3_600_000


def _ema_last(closes, n):
    if len(closes) < n:
        return None
    k = 2.0 / (n + 1.0)
    e = sum(closes[:n]) / n
    for v in closes[n:]:
        e = v * k + e * (1.0 - k)
    return e


def _signal(variant, rows):
    """(above: bool) — sinyal; veri yetersizse None."""
    if len(rows) < MIN_DAILY_BARS:
        return None
    closes = [float(r["close"]) for r in rows]
    c = closes[-1]
    if variant == "m1_ema100":
        e = _ema_last(closes, 100)
        return None if e is None else c > e
    if variant == "m2_tsmom28":
        return c > closes[-1 - 28]
    if variant == "m3_tsmom91":
        return c > closes[-1 - 91]
    if variant == "m2_tsmom21":                 # V13R komsu
        return c > closes[-1 - 21]
    if variant == "m2_tsmom42":                 # V13R komsu
        return c > closes[-1 - 42]
    if variant in ("t2r_ema200", "t2r_ema150", "t2r_ema250"):   # V13R: kontrol + komsular (arastirma EMA)
        e = _ema_last(closes, int(variant[-3:]))
        return None if e is None else c > e
    raise KeyError(variant)


VARIANTS = ("m1_ema100", "m2_tsmom28", "m3_tsmom91",
            "m2_tsmom21", "m2_tsmom42", "t2r_ema200", "t2r_ema150", "t2r_ema250")   # V13R eklemeleri


def make(variant):
    if variant not in VARIANTS:
        raise KeyError(variant)
    cache = {}

    def strat(sym, t, fr, pos, rp):
        now_ms = t + H4_MS
        _tail = 700 if variant.startswith("t2r_") else 320      # EMA250 icin daha uzun isinma
        rows = closed_bars(daily_rows_from_frame(fr.get("1d"), tail=_tail), now_ms=now_ms, tf="1d")
        above = _signal(variant, rows)
        if above is None:
            return None
        if pos is not None:
            return {"action": "CLOSE", "reason": variant.upper() + "_CROSS_DOWN", "name": variant} if not above else None
        if not above:
            return None
        reg = cache.get(t)
        if reg is None:
            b1d = rp._slice(BTC_SYMBOL, t).get("1d") if BTC_SYMBOL in rp.frames else None
            reg = btc_regime(closed_bars(daily_rows_from_frame(b1d), now_ms=now_ms, tf="1d")) or "NONE"
            cache[t] = reg
        if reg != UP:
            return None
        d = read_daily(rows)                       # (close, ema200, atr14) — ATR için
        if d is None:
            return None
        close, _ema, atr = d
        stop = close - DEFAULT_ATR_MULT * atr
        if stop <= 0:
            return None
        return {"action": "OPEN", "direction": "LONG", "stop": stop, "targets": [], "leverage": 1,
                "reason": variant.upper(), "name": variant, "regime": reg, "setup_type": "trend"}
    return strat


def build(name):
    return make(name)
