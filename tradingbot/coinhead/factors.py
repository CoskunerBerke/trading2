"""Faktör grupları — ilişkili göstergelerin aynı kanıtı tekrar tekrar oylamasını engeller.

Grup içinde: bias'lar güvenle ağırlıklı ortalanır (bir grup = tek oy), n_independent = farklı ajan sayısı,
conflict = grup içi bias std sapması. Gruplar arası: rejime bağlı güvenilirlik ağırlıklarıyla birleştirilir.
"""
from __future__ import annotations

import math
from typing import Iterable

from .schema import FactorGroupScore, SpecialistReport

FACTOR_GROUPS: tuple[str, ...] = ("trend", "momentum", "volatility", "volume_flow", "structure_levels", "liquidity",
                                  "derivatives", "correlation", "historical_edge", "catalyst", "risk")

# legacy ajan → grup(lar). market ajanı likidite ve türev olarak ikiye bölünür (metriklerine göre)
LEGACY_GROUP: dict[str, tuple[str, ...]] = {
    "trend": ("trend",), "momentum": ("momentum",), "volatility": ("volatility",), "volume": ("volume_flow",),
    "levels": ("structure_levels",), "candles": ("structure_levels",), "analog": ("historical_edge",),
    "edge": ("historical_edge",), "market": ("liquidity", "derivatives"),
}

# yön veren gruplar için temel ağırlıklar (rejime göre değiştirilir); volatility/liquidity/risk yön vermez
BASE_WEIGHTS: dict[str, float] = {"trend": 0.24, "momentum": 0.16, "volume_flow": 0.10, "structure_levels": 0.16,
                                  "derivatives": 0.10, "correlation": 0.04, "historical_edge": 0.16, "catalyst": 0.04}
REGIME_MULT: dict[str, dict[str, float]] = {
    "TREND_UP": {"trend": 1.3, "momentum": 1.1, "structure_levels": 0.8},
    "TREND_DOWN": {"trend": 1.3, "momentum": 1.1, "structure_levels": 0.8},
    "RANGE": {"trend": 0.6, "structure_levels": 1.4, "momentum": 0.8},
    "SQUEEZE": {"trend": 0.7, "volume_flow": 1.3, "structure_levels": 1.2},
    "HIGH_VOL": {"trend": 0.9, "derivatives": 1.3, "historical_edge": 0.8},
    "PANIC": {"trend": 0.7, "derivatives": 1.4, "structure_levels": 1.2},
    "EUPHORIC": {"derivatives": 1.4, "momentum": 0.8},
    "ILLIQUID": {"volume_flow": 0.5, "structure_levels": 0.7},
}


def split_market_report(rep: SpecialistReport) -> list[SpecialistReport]:
    """Legacy `market` raporunu likidite (ob/spread) ve türev (funding/oi/lsr) parçalarına ayırır."""
    if rep.factor_group not in ("liquidity", "derivatives") and not rep.agent_name.startswith("market"):
        return [rep]
    m = rep.metrics or {}
    out = []
    ob = float(m.get("ob_imbalance", 0.5) or 0.5)
    liq_bias = max(-1.0, min(1.0, (ob - 0.5) * 2.0))
    fr = float(m.get("funding_pct", 0.0) or 0.0)
    lsr = float(m.get("long_short_ratio", 1.0) or 1.0)
    der_bias = 0.0
    if fr > 0.03:
        der_bias -= 0.5
    elif fr < -0.02:
        der_bias += 0.5
    if lsr > 2.5:
        der_bias -= 0.3
    elif lsr < 0.7:
        der_bias += 0.3
    for grp, b in (("liquidity", liq_bias), ("derivatives", der_bias)):
        r = SpecialistReport(**{**rep.__dict__})
        r.factor_group, r.bias, r.agent_name = grp, b, f"market:{grp}"
        r.stance = _stance(b)
        out.append(r)
    return out


def _stance(b: float):
    from .schema import stance_from_bias
    return stance_from_bias(b)


def aggregate(reports: Iterable[SpecialistReport]) -> list[FactorGroupScore]:
    """Raporları faktör gruplarına indirger. Her grup tek skor: güven-ağırlıklı bias ortalaması."""
    by: dict[str, list[SpecialistReport]] = {}
    for r in reports:
        by.setdefault(r.factor_group or "risk", []).append(r)
    out: list[FactorGroupScore] = []
    for grp in FACTOR_GROUPS:
        reps = by.get(grp, [])
        if not reps:
            continue
        ok = [r for r in reps if r.usable]
        dq = len(ok) / len(reps)
        if not ok:
            out.append(FactorGroupScore(grp, 0.0, 0.0, dq, 0, 0.0))
            continue
        w = [max(0.05, r.confidence_calibrated) for r in ok]
        score = sum(wi * r.bias for wi, r in zip(w, ok)) / sum(w)
        conf = sum(w) / len(w)
        n_ind = len({r.agent_name.split(":")[0] for r in ok})
        mean_b = sum(r.bias for r in ok) / len(ok)
        conflict = math.sqrt(sum((r.bias - mean_b) ** 2 for r in ok) / len(ok)) if len(ok) > 1 else 0.0
        # çelişki güveni düşürür, bağımsız kanıt sayısı hafifçe artırır (log, doygun)
        conf = conf * (1.0 - min(0.5, conflict)) * min(1.0, 0.7 + 0.15 * math.log1p(n_ind))
        out.append(FactorGroupScore(grp, round(max(-1.0, min(1.0, score)), 4), round(conf, 4), round(dq, 3), n_ind, round(conflict, 4)))
    return out


def consensus(groups: list[FactorGroupScore], regime: str = "UNKNOWN", weights: dict[str, float] | None = None,
              calibration: dict[str, float] | None = None) -> tuple[float, float, list[str]]:
    """Gruplar → (skor −1..1, güven 0..1, dissent). Yalnız yön veren gruplar sayılır; her grup tek oy."""
    w_base = dict(BASE_WEIGHTS)
    if weights:
        w_base.update({k: v for k, v in weights.items() if k in w_base})
    mult = REGIME_MULT.get(regime, {})
    num = den = 0.0
    used: list[tuple[str, float]] = []
    for g in groups:
        if g.group not in w_base or g.n_independent == 0:
            continue
        w = w_base[g.group] * mult.get(g.group, 1.0) * (calibration or {}).get(g.group, 1.0)
        w *= (0.4 + 0.6 * g.confidence) * (0.5 + 0.5 * g.data_quality)
        num += w * g.score
        den += w
        used.append((g.group, g.score))
    if den == 0:
        return 0.0, 0.0, []
    score = num / den
    dissent = [grp for grp, s in used if abs(s) >= 0.15 and (s > 0) != (score > 0) and abs(score) >= 0.05]
    agree = [grp for grp, s in used if abs(s) >= 0.15 and (s > 0) == (score > 0)]
    conf = min(1.0, abs(score) / 0.6) * (len(agree) / max(1, len(agree) + len(dissent)))
    return round(score, 4), round(conf, 4), dissent
