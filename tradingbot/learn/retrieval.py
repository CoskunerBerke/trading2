"""LLM/analog için hafıza getirme — yapılandırılmış filtreler + standardize özellik vektörlerinde kosinüs benzerliği. Ucuz; vektör DB gerekmez."""
from __future__ import annotations

from typing import Any

import numpy as np

from .features import build_features, feature_names, to_vector


def _bucket(x: float | None, edges: tuple[float, ...]) -> int:
    if x is None:
        return -1
    for i, e in enumerate(edges):
        if x < e:
            return i
    return len(edges)


def retrieve_similar(memory: Any, query: dict[str, Any], k: int = 5, *, same_symbol_bonus: float = 0.05,
                     names: list[str] | None = None) -> list[dict]:
    """memory: TradeMemory (trades(closed_only=True)). query: giriş anı özellik sözlüğü (+ symbol/direction/setup/regime)."""
    names = names or feature_names()
    q = np.array(to_vector(build_features(query), names), float)
    cands = memory.trades(closed_only=True)
    if not cands:
        return []
    X = np.array([to_vector(build_features(c.get("features") or c), names) for c in cands], float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Xs, qs = (X - mu) / sd, (q - mu) / sd
    denom = (np.linalg.norm(Xs, axis=1) * (np.linalg.norm(qs) + 1e-9)) + 1e-9
    sims = Xs @ qs / denom
    q_dir, q_setup, q_reg, q_sym = query.get("direction"), query.get("setup_type"), query.get("regime"), query.get("symbol")
    q_vol = _bucket(query.get("atr_pct"), (0.15, 0.3, 0.6))
    q_fund = _bucket(query.get("funding_dir"), (-0.3, 0.3))
    scored = []
    for c, s in zip(cands, sims):
        bonus = 0.0
        if q_dir and c.get("direction") == q_dir:
            bonus += 0.05
        if q_setup and c.get("setup_type") == q_setup:
            bonus += 0.05
        if q_reg and c.get("regime") == q_reg:
            bonus += 0.05
        if q_sym and c.get("symbol") == q_sym:
            bonus += same_symbol_bonus
        f = c.get("features") or c
        if _bucket(f.get("atr_pct"), (0.15, 0.3, 0.6)) == q_vol:
            bonus += 0.02
        if _bucket(f.get("funding_dir"), (-0.3, 0.3)) == q_fund:
            bonus += 0.02
        scored.append((float(s) + bonus, c))
    scored.sort(key=lambda t: t[0], reverse=True)
    out = []
    for score, c in scored[:k]:
        o = c.get("outcome") or {}
        pm = c.get("postmortem") or {}
        out.append({"id": c.get("trade_id"), "symbol": c.get("symbol"), "direction": c.get("direction"), "setup": c.get("setup_type"),
                    "regime": c.get("regime"), "similarity": round(score, 4), "r_multiple": o.get("r_multiple"), "exit_reason": o.get("exit_reason"),
                    "lesson_codes": pm.get("lesson_codes", []), "lesson": (pm.get("lesson_text_tr") or [""])[0][:200]})
    return out
