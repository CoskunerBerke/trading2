"""Sonuç etiketleme — R bazlı sınıf (WIN/LOSS/SCRATCH), çıkış kalitesi, giriş zamanlaması, maliyet sürüklemeleri (R cinsinden)."""
from __future__ import annotations

from typing import Any


def _f(x, d=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def label_outcome(rec: dict[str, Any], *, scratch_r: float = 0.25) -> dict[str, Any]:
    r = _f(rec.get("r_multiple"))
    pnl = _f(rec.get("net_pnl", rec.get("pnl")))
    reason = str(rec.get("exit_reason", "") or "").lower()
    if abs(r) < scratch_r:
        cls = "SCRATCH"
    else:
        cls = "WIN" if r > 0 else "LOSS"
    if "likid" in reason or "liq" in reason:
        exit_q = "LIQUIDATION"
    elif "hedef" in reason or "target" in reason or "tp" in reason:
        exit_q = "TARGET"
    elif "başa" in reason or "breakeven" in reason:
        exit_q = "TP1_THEN_BE"
    elif "gap" in reason:
        exit_q = "GAP_THROUGH"
    elif "stop" in reason:
        exit_q = "STOP"
    elif "manual" in reason or "manuel" in reason:
        exit_q = "MANUAL"
    else:
        exit_q = "OTHER"
    mae, mfe = _f(rec.get("mae_pct")), _f(rec.get("mfe_pct"))
    if mfe > 0 and abs(mae) > mfe * 0.8:
        timing = "MAE_BEFORE_MFE"
    elif mfe > 0 and abs(mae) < mfe * 0.2:
        timing = "CLEAN_ENTRY"
    else:
        timing = "MIXED"
    # risk_usdt = |pnl| / |r| tahmini (r sıfırsa notional stop% üzerinden)
    risk_usdt = abs(pnl / r) if r else _f(rec.get("risk_usdt"), 0.0)
    fees = _f(rec.get("fees")) + _f(rec.get("entry_fee")) + _f(rec.get("exit_fee")) if rec.get("entry_fee") is not None else _f(rec.get("fees"))
    funding = _f(rec.get("funding_paid")) - _f(rec.get("funding_received")) if rec.get("funding_paid") is not None else -_f(rec.get("funding"))
    slip = _f(rec.get("slippage_cost")) + _f(rec.get("spread_cost"))
    return {"outcome_class": cls, "r_multiple": round(r, 4), "won": cls == "WIN", "exit_quality": exit_q, "entry_timing": timing,
            "fee_drag_r": round(fees / risk_usdt, 4) if risk_usdt else None, "funding_drag_r": round(funding / risk_usdt, 4) if risk_usdt else None,
            "slippage_drag_r": round(slip / risk_usdt, 4) if risk_usdt else None, "mae_pct": mae, "mfe_pct": mfe,
            "bars_held": int(_f(rec.get("bars_held")))}
