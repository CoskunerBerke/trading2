"""RED TEAM / VETO ajanı — görevi işlem bulmak değil, işlemi REDDETMEK için en güçlü nedeni bulmaktır."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .schema import SpecialistReport
from .specialists import SpecialistContext, _base, _finish

VETO_CODES = ("STALE_DATA", "LOW_LIQUIDITY", "WIDE_SPREAD", "COSTS_EXCEED_EDGE", "WEAK_OOS_EDGE", "LOW_TRADE_COUNT",
              "HIGH_CORRELATION_EXPOSURE", "AGAINST_BTC_REGIME", "STOP_TOO_FAR", "STOP_TOO_CLOSE", "LIQ_BEFORE_STOP",
              "FUNDING_EXTREME", "SOURCES_CONFLICT", "LLM_SCHEMA_INVALID", "MIN_ORDER_CONFLICT", "RISK_LIMIT",
              "KILL_SWITCH_ACTIVE", "MODEL_DRIFT", "NEW_LISTING", "DELIST_RISK", "CROWDED_SAME_DIRECTION",
              "CLOCK_OR_API_ISSUE", "MISSING_4H_FRAME")


@dataclass
class RedTeamContext:
    """Her alan None = bilinmiyor → değerlendirilmez. Değerler yüzde/oran olarak verilir."""
    direction: str = ""                         # LONG | SHORT
    data_stale: bool | None = None
    missing_4h: bool | None = None
    spread_pct: float | None = None
    depth_usdt: float | None = None
    expected_cost_pct: float | None = None      # gidiş-dönüş komisyon+kayma+spread+funding (% notional)
    expected_return_gross_pct: float | None = None
    oos_sharpe: float | None = None
    oos_trades: int | None = None
    has_edge: bool | None = None
    corr_btc: float | None = None
    same_direction_open: int | None = None      # portföyde aynı yönde açık pozisyon sayısı
    btc_regime: str | None = None               # TREND_UP/TREND_DOWN/... veya RISK-ON/RISK-OFF
    stop_pct: float | None = None
    atr_pct: float | None = None
    liq_distance_pct: float | None = None
    funding_pct: float | None = None            # 8 saatlik %
    funding_z: float | None = None
    sources_conflict: bool | None = None
    llm_schema_invalid: bool | None = None
    min_order_conflict: bool | None = None
    risk_limit_hit: bool | None = None
    kill_switch_active: bool | None = None
    model_drift: bool | None = None
    listing_age_days: float | None = None
    delist_flag: bool | None = None
    clock_or_api_issue: bool | None = None
    max_spread_pct: float = 0.3
    min_depth_usdt: float = 50_000
    min_oos_trades: int = 30
    min_oos_sharpe: float = 0.5
    max_corr_same_dir: float = 0.85
    max_stop_atr_mult: float = 4.0
    min_stop_atr_mult: float = 1.0
    min_liq_stop_ratio: float = 3.0
    funding_extreme_pct: float = 0.05
    min_listing_days: float = 60
    extra: dict[str, Any] = field(default_factory=dict)


def review(rt: RedTeamContext) -> tuple[list[str], list[str]]:
    """→ (veto kodları, uyarılar)."""
    v: list[str] = []
    w: list[str] = []
    if rt.data_stale:
        v.append("STALE_DATA")
    if rt.missing_4h:
        v.append("MISSING_4H_FRAME")
    if rt.spread_pct is not None and rt.spread_pct > rt.max_spread_pct:
        v.append("WIDE_SPREAD")
    if rt.depth_usdt is not None and rt.depth_usdt < rt.min_depth_usdt:
        v.append("LOW_LIQUIDITY")
    if rt.expected_cost_pct is not None and rt.expected_return_gross_pct is not None and rt.expected_cost_pct >= rt.expected_return_gross_pct:
        v.append("COSTS_EXCEED_EDGE")
    if rt.has_edge is False or (rt.oos_sharpe is not None and rt.oos_sharpe < rt.min_oos_sharpe):
        (v if rt.has_edge is False else w).append("WEAK_OOS_EDGE")
    if rt.oos_trades is not None and rt.oos_trades < rt.min_oos_trades:
        w.append("LOW_TRADE_COUNT")
    if rt.corr_btc is not None and rt.same_direction_open and abs(rt.corr_btc) > rt.max_corr_same_dir and rt.same_direction_open >= 2:
        v.append("HIGH_CORRELATION_EXPOSURE")
    if rt.same_direction_open is not None and rt.same_direction_open >= 3:
        v.append("CROWDED_SAME_DIRECTION")
    if rt.btc_regime and rt.direction:
        bearish = rt.btc_regime in ("TREND_DOWN", "PANIC", "RISK-OFF")
        bullish = rt.btc_regime in ("TREND_UP", "EUPHORIC", "RISK-ON")
        if (bearish and rt.direction == "LONG") or (bullish and rt.direction == "SHORT"):
            w.append("AGAINST_BTC_REGIME")
    if rt.stop_pct is not None and rt.atr_pct:
        k = rt.stop_pct / rt.atr_pct
        if k > rt.max_stop_atr_mult:
            v.append("STOP_TOO_FAR")
        elif k < rt.min_stop_atr_mult:
            v.append("STOP_TOO_CLOSE")
    if rt.liq_distance_pct is not None and rt.stop_pct:
        if rt.liq_distance_pct <= rt.stop_pct:
            v.append("LIQ_BEFORE_STOP")
        elif rt.liq_distance_pct < rt.min_liq_stop_ratio * rt.stop_pct:
            w.append("LIQ_BUFFER_THIN")
    if rt.funding_pct is not None and rt.direction:
        if (rt.direction == "LONG" and rt.funding_pct > rt.funding_extreme_pct) or (rt.direction == "SHORT" and rt.funding_pct < -rt.funding_extreme_pct):
            v.append("FUNDING_EXTREME")
    if rt.funding_z is not None and rt.direction and ((rt.direction == "LONG" and rt.funding_z > 2.5) or (rt.direction == "SHORT" and rt.funding_z < -2.5)):
        w.append("FUNDING_CROWDED")
    for flag, code in ((rt.sources_conflict, "SOURCES_CONFLICT"), (rt.llm_schema_invalid, "LLM_SCHEMA_INVALID"),
                       (rt.min_order_conflict, "MIN_ORDER_CONFLICT"), (rt.risk_limit_hit, "RISK_LIMIT"),
                       (rt.kill_switch_active, "KILL_SWITCH_ACTIVE"), (rt.model_drift, "MODEL_DRIFT"),
                       (rt.delist_flag, "DELIST_RISK"), (rt.clock_or_api_issue, "CLOCK_OR_API_ISSUE")):
        if flag:
            v.append(code)
    if rt.listing_age_days is not None and rt.listing_age_days < rt.min_listing_days:
        v.append("NEW_LISTING")
    return list(dict.fromkeys(v)), list(dict.fromkeys(w))


def RedTeamVetoAgent(ctx: SpecialistContext, rt: RedTeamContext) -> SpecialistReport:
    t0 = time.time()
    rep = _base(ctx, "red_team_veto", "risk")
    vetoes, warns = review(rt)
    rep.metrics = {"vetoes": vetoes, "warnings": warns, "direction": rt.direction}
    rep.warnings = warns
    rep.bias = 0.0
    rep.confidence_raw = 90 if vetoes else 60
    if vetoes:
        rep.veto, rep.veto_reason = True, "NO_TRADE_RED_TEAM_VETO: " + ", ".join(vetoes)
        rep.evidence_against = [f"VETO {c}" for c in vetoes]
    else:
        rep.evidence_for = ["Red team veto bulamadı"] + [f"uyarı: {x}" for x in warns]
    return _finish(rep, t0)
