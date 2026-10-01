# -*- coding: utf-8 -*-
"""PIYASA REJIMI KAPISI — BTC gunluk trendine gore giris izni (V7, 2026-09-13). SAF, tek kaynak.

Olculen mekanizma: sistem 2021'de (BTC yukselis) kazandi, 2022'den beri her pencerede
kaybediyor; SHORT kesiti ayrica negatif. Hipotez: giris kalitesi PIYASA rejimine bagli ve
sistem yalniz yukselis rejiminde, yalniz LONG calismali.

Rejim tanimi (tek deger, izgara yok): son KAPANMIS gunluk BTC barinda close > EMA200 → UP,
degilse DOWN. EMA200 gunluk cercevede zaten hesaplaniyor (`add_snapshot_indicators`);
sutun yoksa 200+ kapanistan hesaplanir; o da yoksa rejim BILINMIYOR ve kapi FAIL-CLOSED.

Not: kirmizi takim zaten `AGAINST_BTC_REGIME` diye YUMUSAK ceza uygular; bu kapi SERT'tir
ve farkli bir politikadir (aday hic acilmaz).

Varyantlar:
  r1_long_only_uptrend   yalniz LONG ve yalniz UP rejiminde; SHORT hic acilmaz
  r2_no_trade_downtrend  DOWN rejiminde hic giris yok; UP'ta iki yon de serbest
  r3_follow_regime       UP'ta yalniz LONG, DOWN'da yalniz SHORT
"""
from __future__ import annotations

import math
from typing import Any

VARIANTS = ("r1_long_only_uptrend", "r2_no_trade_downtrend", "r3_follow_regime")
EMA_LEN = 200
UP, DOWN = "UP", "DOWN"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def ema_last(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    k = 2.0 / (n + 1.0)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1.0 - k)
    return e


def btc_regime(daily_bars: list[dict[str, Any]]) -> str | None:
    """Son KAPANMIS gunluk bardan rejim. Bilinmiyorsa None (uydurulmaz)."""
    if not daily_bars:
        return None
    last = daily_bars[-1]
    c = _f(last.get("close"))
    if c is None:
        return None
    e = _f(last.get("ema200"))
    if e is None:
        closes = [_f(b.get("close")) for b in daily_bars]
        closes = [x for x in closes if x is not None]
        e = ema_last(closes, EMA_LEN)
        if e is None:
            return None
    return UP if c > e else DOWN


def evaluate_variant(variant: str, *, direction: str, regime: str | None) -> tuple[bool, str]:
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen rejim varyanti: %r" % (variant,))
    tag = variant.split("_", 1)[0].upper()
    side = str(direction or "").upper()
    if side not in ("LONG", "SHORT"):
        return False, tag + "_SIDE"
    if regime not in (UP, DOWN):
        return False, tag + "_NO_REGIME"
    if variant == "r1_long_only_uptrend":
        if side == "SHORT":
            return False, "R1_SHORT_BLOCKED"
        return (True, "") if regime == UP else (False, "R1_NOT_UPTREND")
    if variant == "r2_no_trade_downtrend":
        return (True, "") if regime == UP else (False, "R2_DOWNTREND")
    ok = (side == "LONG" and regime == UP) or (side == "SHORT" and regime == DOWN)
    return (True, "") if ok else (False, "R3_AGAINST_REGIME")


MODES = ("OFF", "SHADOW", "ENFORCE")
DEFAULT_VARIANT = "r1_long_only_uptrend"
BTC_SYMBOL = "BTC/USDT"
_REAL_MONEY_MODES = ("LIVE", "LIVE_LIMITED")
_ROW_COLS = ("timestamp", "close", "ema200")


def rows_for_regime(frame, tail: int = 260) -> list[dict[str, Any]]:
    """BTC gunluk DataFrame'inden rejim satirlari (timestamp, close, varsa ema200). Iki motor da
    bunu kullanir; sutun secimi tek yerde durur."""
    if frame is None:
        return []
    try:
        cols = [c for c in _ROW_COLS if c in frame.columns]
        if "close" not in cols:
            return []
        return frame.tail(tail)[cols].to_dict("records")
    except (AttributeError, TypeError, ValueError, KeyError):
        return []


def regime_confirmation(*, mode: str, variant: str, direction: str,
                        btc_daily_bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Karar kaydi: rejim + secili varyantin hukmu + tum varyantlarin golge hukmu."""
    m = str(mode or "OFF").upper()
    out: dict[str, Any] = {"schema_version": "regime_gate_v1", "mode": m, "variant": variant,
                           "regime": None, "btc_close": None, "btc_ema200": None,
                           "verdict": {"ok": True, "reason": ""}, "blocks": False, "shadow": {}}
    if m == "OFF":
        return out
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen rejim varyanti: %r" % (variant,))
    reg = btc_regime(btc_daily_bars)
    out["regime"] = reg
    if btc_daily_bars:
        out["btc_close"] = _f(btc_daily_bars[-1].get("close"))
        out["btc_ema200"] = _f(btc_daily_bars[-1].get("ema200"))
    for v in VARIANTS:
        ok, why = evaluate_variant(v, direction=direction, regime=reg)
        out["shadow"][v] = {"ok": bool(ok), "reason": why}
    sel = out["shadow"][variant]
    out["verdict"] = {"ok": bool(sel["ok"]), "reason": sel["reason"]}
    out["blocks"] = bool(m == "ENFORCE" and not sel["ok"])
    return out


def validate_settings(*, mode: str | None, variant: str | None, app_mode: str | None) -> str:
    """Config dogrulamasi (SAF): normalize edilmis modu dondurur; gecersizse ValueError."""
    m = str(mode or "OFF").upper()
    if m not in MODES:
        raise ValueError("regime_gate_mode gecersiz: %r (gecerli: %s)" % (mode, ", ".join(MODES)))
    if variant not in VARIANTS:
        raise ValueError("regime_gate_variant gecersiz: %r (gecerli: %s)" % (variant, ", ".join(VARIANTS)))
    if m == "ENFORCE" and str(app_mode or "").upper() in _REAL_MONEY_MODES:
        raise ValueError("REGIME_GATE_NOT_VALIDATED_FOR_LIVE: regime_gate_mode=ENFORCE yalniz "
                         "PAPER/TESTNET/OBSERVE/SHADOW_LIVE modda acilabilir (DENEY_V7: kayip azaltici, karli degil)")
    return m


__all__ = ["BTC_SYMBOL", "DEFAULT_VARIANT", "DOWN", "EMA_LEN", "MODES", "UP", "VARIANTS", "btc_regime",
           "ema_last", "evaluate_variant", "regime_confirmation", "rows_for_regime", "validate_settings"]
