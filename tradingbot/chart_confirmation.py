# -*- coding: utf-8 -*-
"""GRAFIK FORMASYONU ONAYI — canli motor ile replay'in ORTAK tek kaynagi (V5, 2026-09-12).

`candle_confirmation.py` ile ayni sozlesme: OFF / SHADOW / ENFORCE, dort varyant, tum
varyantlarin golge hukmu karar kaydina yazilir. Sekil tespiti `chart_patterns.py`.

Varyantlar:
  p1_4h_confirm  son `confirm_within_bars` 4h bar icinde adayin yonuyle AYNI tarafli TEYITLI
                 formasyon kirilisi varsa gir; yoksa/karsiysa/iki tarafliysa girme
  p2_4h_veto     yalniz VETO: taze KARSI tarafli kirilis varsa girme
  p3_1d_confirm  p1'in gunluk bar surumu
  p4_1d_veto     p2'nin gunluk bar surumu

Olcum: PROTOCOL_V5 / DENEY_V5. `ENFORCE` acmak operator kararidir.
"""
from __future__ import annotations

from typing import Any

from .chart_patterns import ChartPatternConfig, detect_chart_patterns, fresh_patterns, fresh_side

MODES = ("OFF", "SHADOW", "ENFORCE")
VARIANTS = ("p1_4h_confirm", "p2_4h_veto", "p3_1d_confirm", "p4_1d_veto")
DEFAULT_VARIANT = "p2_4h_veto"
_TF = {"p1_4h_confirm": "4h", "p2_4h_veto": "4h", "p3_1d_confirm": "1d", "p4_1d_veto": "1d"}
_VETO = {"p2_4h_veto", "p4_1d_veto"}
_REAL_MONEY_MODES = ("LIVE", "LIVE_LIMITED")
MIN_BARS = 30


def evaluate_variant(variant: str, *, direction: str, bars_4h, bars_1d,
                     cfg: ChartPatternConfig | None = None,
                     det_4h: dict[str, Any] | None = None,
                     det_1d: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Tek varyantin karari. `det_*` verilirse tespit yeniden YAPILMAZ (ayni bar seti)."""
    if variant not in _TF:
        raise ValueError("bilinmeyen grafik onayi varyanti: %r" % (variant,))
    cfg = cfg or ChartPatternConfig()
    tag = variant.split("_", 1)[0].upper()
    side = str(direction or "").upper()
    veto = variant in _VETO
    if side not in ("LONG", "SHORT"):
        return False, tag + "_SIDE"
    bars = bars_4h if _TF[variant] == "4h" else bars_1d
    det = det_4h if _TF[variant] == "4h" else det_1d
    if det is None:
        rows = list(bars or [])
        if len(rows) < MIN_BARS:
            return (True, "") if veto else (False, tag + "_MISSING_BARS")
        det = detect_chart_patterns(rows, cfg)
    elif int(det.get("n_bars", 0)) < MIN_BARS:
        return (True, "") if veto else (False, tag + "_MISSING_BARS")
    fresh = fresh_patterns(det, cfg.confirm_within_bars)
    ps = fresh_side(fresh)
    if veto:
        if ps is not None and ps != side:
            return False, tag + "_OPPOSITE_PATTERN"
        return True, ""
    if not fresh:
        return False, tag + "_NO_PATTERN"
    if ps is None:
        return False, tag + "_AMBIGUOUS"
    return (True, "") if ps == side else (False, tag + "_OPPOSITE_PATTERN")


def chart_confirmation(*, mode: str, variant: str, direction: str, bars_4h, bars_1d,
                       cfg: ChartPatternConfig | None = None) -> dict[str, Any]:
    """Karar kaydi: secili varyantin hukmu + TUM varyantlarin golge hukmu + tespit ozeti."""
    cfg = cfg or ChartPatternConfig()
    m = str(mode or "OFF").upper()
    out: dict[str, Any] = {"schema_version": "chart_confirmation_v1", "mode": m, "variant": variant,
                           "policy_version": cfg.policy_version,
                           "verdict": {"ok": True, "reason": ""}, "blocks": False, "shadow": {},
                           "fresh_4h": [], "fresh_1d": []}
    if m == "OFF":
        return out
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen grafik onayi varyanti: %r" % (variant,))
    d4 = detect_chart_patterns(list(bars_4h or []), cfg) if len(list(bars_4h or [])) >= MIN_BARS else None
    d1 = detect_chart_patterns(list(bars_1d or []), cfg) if len(list(bars_1d or [])) >= MIN_BARS else None
    for key, det in (("fresh_4h", d4), ("fresh_1d", d1)):
        if det is not None:
            out[key] = [{"pattern": p["pattern"], "side": p["side"], "bars_since": p["bars_since"],
                         "level": p["level"]} for p in fresh_patterns(det, cfg.confirm_within_bars)]
    for v in VARIANTS:
        ok, why = evaluate_variant(v, direction=direction, bars_4h=bars_4h, bars_1d=bars_1d, cfg=cfg,
                                   det_4h=d4, det_1d=d1)
        out["shadow"][v] = {"ok": bool(ok), "reason": why}
    sel = out["shadow"][variant]
    out["verdict"] = {"ok": bool(sel["ok"]), "reason": sel["reason"]}
    out["blocks"] = bool(m == "ENFORCE" and not sel["ok"])
    return out


def validate_settings(*, mode: str | None, variant: str | None, app_mode: str | None) -> str:
    """Config dogrulamasi (SAF): normalize edilmis modu dondurur; gecersizse ValueError."""
    m = str(mode or "OFF").upper()
    if m not in MODES:
        raise ValueError("chart_confirmation_mode gecersiz: %r (gecerli: %s)" % (mode, ", ".join(MODES)))
    if variant not in VARIANTS:
        raise ValueError("chart_confirmation_variant gecersiz: %r (gecerli: %s)" % (variant, ", ".join(VARIANTS)))
    if m == "ENFORCE" and str(app_mode or "").upper() in _REAL_MONEY_MODES:
        raise ValueError("CHART_CONFIRMATION_NOT_VALIDATED_FOR_LIVE: chart_confirmation_mode=ENFORCE "
                         "yalniz PAPER/TESTNET/OBSERVE/SHADOW_LIVE modda acilabilir (DENEY_V5)")
    return m


__all__ = ["DEFAULT_VARIANT", "MODES", "VARIANTS", "MIN_BARS", "chart_confirmation",
           "evaluate_variant", "validate_settings"]
