"""Storage-aware kapsam katmanları: Tier A (tüm likit evren 1h/4h/1d max) · Tier B (top-N 15m+ max) · Tier C (top-N + açık pozisyon: 1m 90g, 5m 365g)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .collector import CollectSpec


@dataclass
class TierPlan:
    specs: list[CollectSpec] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def build_tier_specs(universe_ranked: list[str], open_position_symbols: list[str], hist_cfg, *, markets=("spot", "futures")) -> TierPlan:
    """`universe_ranked`: hacim/veri kalitesine göre sıralı semboller. Config aralıkları genişletilebilir (tier_c_1m_days ...)."""
    top_b = universe_ranked[: hist_cfg.tier_b_top_n]
    top_c = list(dict.fromkeys(universe_ranked[: hist_cfg.tier_c_top_n] + list(open_position_symbols)))
    common = dict(markets=tuple(markets), max_available=bool(hist_cfg.max_available), days=int(hist_cfg.default_days),
                  include_funding=bool(hist_cfg.include_funding), include_open_interest=bool(hist_cfg.include_open_interest),
                  archive_first=bool(hist_cfg.archive_first))
    a = CollectSpec(symbols=tuple(universe_ranked), timeframes=tuple(hist_cfg.tier_a_timeframes), **common)
    b = CollectSpec(symbols=tuple(top_b), timeframes=tuple(t for t in hist_cfg.tier_b_timeframes if t not in a.timeframes), **common)
    c = CollectSpec(symbols=tuple(top_c), timeframes=("1m", "5m"), per_tf_days={"1m": int(hist_cfg.tier_c_1m_days), "5m": int(hist_cfg.tier_c_5m_days)}, **common)
    return TierPlan([a, b, c], {"tier_a_symbols": len(a.symbols), "tier_b_symbols": len(b.symbols), "tier_c_symbols": len(c.symbols),
                                "tier_a_tfs": a.timeframes, "tier_b_tfs": b.timeframes, "tier_c_tfs": c.timeframes})


__all__ = ["build_tier_specs", "TierPlan"]
