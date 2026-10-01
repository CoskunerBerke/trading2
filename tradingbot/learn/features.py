"""Özellik çıkarımı v2 — deterministik (datetime.now yok), legacy `features_from_brief` sözlükleriyle geriye uyumlu."""
from __future__ import annotations

from typing import Any

FEATURE_VERSION = 2
LEGACY_AGENTS = ("trend", "momentum", "candles", "volume", "levels", "market", "analog", "edge")
REGIMES = ("TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "LOW_VOL", "SQUEEZE", "BREAKOUT", "PANIC", "EUPHORIC", "ILLIQUID")

_BASE = ([f"bias_{a}" for a in LEGACY_AGENTS] + [f"conf_{a}" for a in LEGACY_AGENTS] +
         ["conviction", "rr", "atr_pct", "funding_dir", "ob_dir", "rsi4_dir", "n_warnings", "leverage", "scan_score", "is_breakout", "btc_align"])
_V2 = ["funding_z", "oi_change_pct", "spread_pct", "depth_ratio", "corr_btc", "beta_btc", "data_freshness_s", "expected_r", "expected_cost_pct",
       "p_win_prior", "is_futures", "consensus_score", "consensus_conf", "n_dissent", "n_vetoes"] + [f"regime_{r}" for r in REGIMES]
_TIME = ["hour_sin", "hour_cos"]


def feature_names(version: int = FEATURE_VERSION, include_time_features: bool = False) -> list[str]:
    names = list(_BASE) + (list(_V2) if version >= 2 else [])
    if include_time_features:
        names += _TIME
    return names


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if v == v else default


def build_features(record: dict[str, Any], *, version: int = FEATURE_VERSION, include_time_features: bool = False,
                   as_of_utc: str | None = None) -> dict[str, float]:
    """Zengin giriş anı sözlüğü (legacy anahtarlar + v2 anahtarlar) → düz sayısal özellik sözlüğü.
    Zaman özellikleri yalnız `include_time_features=True` ve `as_of_utc` verilirse (deterministik)."""
    r = record or {}
    out: dict[str, float] = {}
    for a in LEGACY_AGENTS:
        out[f"bias_{a}"] = max(-1.0, min(1.0, _f(r.get(f"bias_{a}"))))
        out[f"conf_{a}"] = max(0.0, min(1.0, _f(r.get(f"conf_{a}"))))
    out["conviction"] = max(0.0, min(1.0, _f(r.get("conviction"))))
    out["rr"] = min(10.0, _f(r.get("rr")))
    out["atr_pct"] = min(5.0, _f(r.get("atr_pct")))
    out["funding_dir"] = max(-5.0, min(5.0, _f(r.get("funding_dir"))))
    out["ob_dir"] = max(-1.0, min(1.0, _f(r.get("ob_dir"))))
    out["rsi4_dir"] = max(-1.0, min(1.0, _f(r.get("rsi4_dir"))))
    out["n_warnings"] = min(20.0, _f(r.get("n_warnings")))
    out["leverage"] = max(1.0, min(125.0, _f(r.get("leverage"), 1.0)))
    out["scan_score"] = max(0.0, min(1.0, _f(r.get("scan_score"))))
    out["is_breakout"] = 1.0 if _f(r.get("is_breakout")) > 0.5 or r.get("setup_type") in ("kırılım", "breakout") else 0.0
    out["btc_align"] = max(-1.0, min(1.0, _f(r.get("btc_align"))))
    if version >= 2:
        out["funding_z"] = max(-5.0, min(5.0, _f(r.get("funding_z"))))
        out["oi_change_pct"] = max(-50.0, min(50.0, _f(r.get("oi_change_pct"))))
        out["spread_pct"] = min(2.0, _f(r.get("spread_pct")))
        out["depth_ratio"] = min(10.0, _f(r.get("depth_ratio")))
        out["corr_btc"] = max(-1.0, min(1.0, _f(r.get("corr_btc"))))
        out["beta_btc"] = max(-5.0, min(5.0, _f(r.get("beta_btc"))))
        out["data_freshness_s"] = min(3600.0, _f(r.get("data_freshness_s")))
        out["expected_r"] = min(20.0, _f(r.get("expected_r")))
        out["expected_cost_pct"] = min(5.0, _f(r.get("expected_cost_pct")))
        out["p_win_prior"] = max(0.0, min(1.0, _f(r.get("p_win_prior"), 0.5)))
        mt = str(r.get("market_type", "")).lower()
        out["is_futures"] = 1.0 if ("fut" in mt or "perp" in mt) else 0.0
        out["consensus_score"] = max(-1.0, min(1.0, _f(r.get("consensus_score"))))
        out["consensus_conf"] = max(0.0, min(1.0, _f(r.get("consensus_conf"))))
        out["n_dissent"] = min(11.0, _f(r.get("n_dissent")))
        out["n_vetoes"] = min(11.0, _f(r.get("n_vetoes")))
        reg = str(r.get("regime", "")).upper()
        for rg in REGIMES:
            out[f"regime_{rg}"] = 1.0 if reg == rg else 0.0
    if include_time_features:
        import math
        from ..core import from_iso
        h = from_iso(as_of_utc).hour if as_of_utc else 0
        out["hour_sin"], out["hour_cos"] = math.sin(2 * math.pi * h / 24), math.cos(2 * math.pi * h / 24)
    return out


def to_vector(features: dict[str, float], names: list[str]) -> list[float]:
    return [float(features.get(n, 0.0)) for n in names]
