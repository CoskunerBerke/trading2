# -*- coding: utf-8 -*-
"""EKONOMI KAPISI — canli motor ile replay'in ORTAK tek kaynagi.

Neden ayri modul: `engine_v3._assess_opportunities` bu mantigi `self` uzerinden kuruyordu ve
`replay/engine` onu HIC calistirmiyordu. Sonuc, backtest'in uretimden yapisal olarak farkli bir
sistemi olcmesiydi (olculdu 2026-09-12: replay'de `assess` cagrisi SIFIR). Ayni kusur sinifi
cikis politikasinda da yasandi (`breakeven_at_mfe_r`), bu yuzden mantik ikinci kez
KOPYALANMIYOR, TEK yere tasiniyor.

Fonksiyon SAFTIR: `self` yok, dosya/ag yok, girdileri mutasyona ugratmaz.
"""
from __future__ import annotations

from .decision_gates import GateLedger, UnknownGateCode
from .opportunity import OpportunityAssessment, assess, hierarchical_expectancy

#: Yumusak kanit -> R cinsinden ceza. Tek kaynak; `engine_v3` bunu import eder.
SOFT_PENALTY_R = {
    "LOW_CONSENSUS": 0.06, "LOW_CONFIDENCE": 0.06, "HIGH_DISSENT": 0.05,
    "RR_BELOW_PREFERRED": 0.05, "PATTERN_WEAK": 0.05, "SPREAD_WIDE": 0.04,
    "VOL_REGIME_HIGH": 0.04, "FUNDING_ADVERSE": 0.04, "MARKET_REGIME_MISMATCH": 0.10,
    "SAME_DIRECTION_CROWDED": 0.08, "CLUSTER_CROWDED": 0.08,
    "RED_TEAM_SOFT_PENALTY": 0.04, "SMALL_SAMPLE": 0.05,
    # --- RED TEAM'in EKONOMIK/ISTATISTIKSEL kodlari: SERT VETO DEGIL ---
    "WEAK_OOS_EDGE": 0.08, "LOW_TRADE_COUNT": 0.05, "HIGH_CORRELATION_EXPOSURE": 0.08,
    "CROWDED_SAME_DIRECTION": 0.08, "AGAINST_BTC_REGIME": 0.08, "STOP_TOO_FAR": 0.05,
    "STOP_TOO_CLOSE": 0.05, "FUNDING_EXTREME": 0.06, "FUNDING_CROWDED": 0.04,
    "NEW_LISTING": 0.06, "WIDE_SPREAD": 0.05, "LOW_LIQUIDITY": 0.05,
    "LIQ_BUFFER_THIN": 0.05,
}

DEFAULT_RISK_PCT = 2.0


def assess_one(*, symbol: str, direction: str, setup: str, regime: str | None,
               soft_flags, redteam_warnings, stop_pct: float,
               expected_cost_pct: float, expected_r: float, is_spot: bool,
               learner, p_win_override: float | None,
               short_penalty_r: float = 0.0, futures_only_penalty_r: float = 0.0,
               spot_listed: bool | None = None,
               risk_per_trade_pct: float = DEFAULT_RISK_PCT) -> tuple[OpportunityAssessment, list[str]]:
    """Tek adayin ekonomik degerlendirmesi. Doner: (degerlendirme, bilinmeyen kapi kodlari).

    `p_win_override` verilirse (kalibre/ogrenilmis tahmin) hiyerarsik tahminin yerine gecer.
    `None` degeri "tahmin YOK" demektir; `0.0` gecerli bir tahmindir ve DUSMEZ.
    """
    gates = GateLedger()
    unknown: list[str] = []
    for code in list(soft_flags or []):
        try:
            gates.penalise(code, SOFT_PENALTY_R.get(code, 0.05), detail="coin head kanıtı")
        except UnknownGateCode as exc:
            unknown.append(exc.code)
    for w in list(redteam_warnings or [])[:4]:
        gates.penalise("RED_TEAM_SOFT_PENALTY", 0.04, detail=str(w)[:80])
    # KANIT ONARIMI V1.1: olculmus kesit aciklari YUMUSAK kanit; yasak YOK.
    if short_penalty_r > 0 and str(direction or "").upper() == "SHORT":
        gates.penalise("SHORT_SEGMENT_PENALTY", short_penalty_r,
                       detail="olculen SHORT kesiti acigi (2026-09-09, n=13)")
    if futures_only_penalty_r > 0 and not is_spot:
        if spot_listed is None:
            gates.penalise("FUTURES_ONLY_SEGMENT_PENALTY", futures_only_penalty_r,
                           detail="spot listeleme verisi yok — ceza fail-safe uygulandi")
        elif not spot_listed:
            gates.penalise("FUTURES_ONLY_SEGMENT_PENALTY", futures_only_penalty_r,
                           detail="Binance spot'ta listeli degil (olculen acik ~0.37R)")
    if unknown:
        gates.block("UNKNOWN_GATE_CODE", detail=",".join(sorted(set(unknown))[:5]))
    if stop_pct <= 0:
        gates.block("ZERO_STOP_DISTANCE")

    stats = hierarchical_expectancy(learner=learner, symbol=symbol, side=direction,
                                    setup=setup or "-", regime=regime,
                                    fallback_win_r=expected_r)
    # KALIBRE TAHMIN ONCELIKLI. `is not None` sarttir: 0.0 falsy oldugu icin ciplak truthiness
    # "kesin kayip" tahminini sessizce DUSURUR ve kapiyi GEVSETIR (fail-open).
    if p_win_override is not None:
        stats["p_win"] = max(0.05, min(0.95, float(p_win_override)))
    a = assess(symbol=symbol, side=direction, setup=setup or "-", gates=gates,
               p_win=stats["p_win"], avg_win_r=stats["avg_win_r"], avg_loss_r=stats["avg_loss_r"],
               sample_size=stats["sample_size"], cost_pct_notional=expected_cost_pct,
               stop_dist_pct=stop_pct, expectancy_basis=stats["expectancy_basis"],
               risk_per_trade_pct=risk_per_trade_pct,
               provenance=stats["provenance"] | {"expected_r_geometry": expected_r})
    return a, unknown


__all__ = ["DEFAULT_RISK_PCT", "SOFT_PENALTY_R", "assess_one"]
