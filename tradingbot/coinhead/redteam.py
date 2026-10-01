"""RED TEAM ajanı — işlemi reddetmek için en güçlü nedeni arar, fakat SERT ile YUMUŞAK'ı AYIRIR.

İki ayrı çıktı üretir (bu ayrım davranışın kaynağıdır):

* ``hard_veto_codes``    — GERÇEK güvenlik/geçerlilik/ekonomi ihlalleri. Tek başına REDDEDER.
                           (bayat/bozuk veri, kill switch, delist, min-emir uyumsuzluğu, likidasyon
                           stop'tan önce, işlem yapılamayacak likidite, maliyet sonrası negatif edge)
* ``soft_penalty_codes`` — ekonomik/istatistiksel zayıflık. Tek başına ASLA reddetmez; fırsat
                           puanını ve pozisyon boyutunu düşürür.
                           (zayıf OOS edge, korelasyon/yığılma, funding, yeni listelenme, rejim
                           uyumsuzluğu, orta seviye spread/derinlik, tercih dışı fakat GEÇERLİ stop)

Neden: bu kodların çoğu eskiden `veto` listesine yazılıyor, `head.py` de her veto için
`plan.valid=False` yapıyordu. Sonuç, kullanıcının istemediği "10 ayrı engelden geçemezse hiç işlem
açma" davranışıydı. Sert liste bilinçli olarak kısadır; sınıflandırma `decision_gates.GATES`
kaydıyla birebir uyumludur (`tests` bunu zorlar).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..decision_gates import HARD_SAFETY, SOFT_EVIDENCE, gate_class
from .schema import SpecialistReport
from .specialists import SpecialistContext, _base, _finish

# --- SERT: tek başına reddeder (gerçek güvenlik / veri bütünlüğü / ekonomi / uygulanabilirlik) ---
HARD_VETO_CODES = ("STALE_DATA", "MISSING_4H_FRAME", "CLOCK_OR_API_ISSUE", "SOURCES_CONFLICT",
                   "LLM_SCHEMA_INVALID", "COSTS_EXCEED_EDGE", "LIQUIDITY_UNTRADEABLE",
                   "LIQ_BEFORE_STOP", "MIN_ORDER_CONFLICT", "RISK_LIMIT", "KILL_SWITCH_ACTIVE",
                   "MODEL_DRIFT", "DELIST_RISK")

# --- YUMUŞAK: boyut küçültür, ASLA tek başına reddetmez ---
SOFT_PENALTY_CODES = ("WEAK_OOS_EDGE", "LOW_TRADE_COUNT", "HIGH_CORRELATION_EXPOSURE",
                      "CROWDED_SAME_DIRECTION", "AGAINST_BTC_REGIME", "STOP_TOO_FAR",
                      "STOP_TOO_CLOSE", "FUNDING_EXTREME", "FUNDING_CROWDED", "NEW_LISTING",
                      "WIDE_SPREAD", "LOW_LIQUIDITY", "LIQ_BUFFER_THIN")

# Geriye uyum: eski tek listeli isim. Karar yolunda KULLANILMAZ (sert/yumuşak ayrımı yapmaz).
VETO_CODES = HARD_VETO_CODES


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
    max_spread_pct: float = 0.3                 # TERCIH esigi -> asilirsa WIDE_SPREAD (YUMUSAK)
    min_depth_usdt: float = 50_000              # TERCIH esigi -> altinda LOW_LIQUIDITY (YUMUSAK)
    untradeable_spread_pct: float = 1.5         # bu spread ile emir gercekten uygulanabilir degil (SERT)
    untradeable_depth_usdt: float = 5_000       # bu derinlikte emir gercekten uygulanabilir degil (SERT)
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
    """→ ``(hard_veto_codes, soft_penalty_codes)``.

    SERT liste yalnız gerçekten işlem yapılamayacak/ölçülemeyecek durumları içerir. Ekonomik ve
    istatistiksel zayıflıklar YUMUŞAK listeye gider: boyutu küçültür, işlemi reddetmez.
    """
    hard: list[str] = []
    soft: list[str] = []
    # --- veri bütünlüğü (SERT) ---
    if rt.data_stale:
        hard.append("STALE_DATA")
    if rt.missing_4h:
        hard.append("MISSING_4H_FRAME")
    # --- spread / derinlik: "işlem yapılamaz" SERT, "ideal değil" YUMUŞAK ---
    if rt.spread_pct is not None:
        if rt.spread_pct > rt.untradeable_spread_pct:
            hard.append("LIQUIDITY_UNTRADEABLE")
        elif rt.spread_pct > rt.max_spread_pct:
            soft.append("WIDE_SPREAD")
    if rt.depth_usdt is not None:
        if rt.depth_usdt < rt.untradeable_depth_usdt:
            hard.append("LIQUIDITY_UNTRADEABLE")
        elif rt.depth_usdt < rt.min_depth_usdt:
            soft.append("LOW_LIQUIDITY")
    # --- maliyet sonrası negatif ekonomi (SERT) ---
    if rt.expected_cost_pct is not None and rt.expected_return_gross_pct is not None and rt.expected_cost_pct >= rt.expected_return_gross_pct:
        hard.append("COSTS_EXCEED_EDGE")
    # --- istatistiksel kanıt zayıflığı (YUMUŞAK: tek başına reddetmez) ---
    if rt.has_edge is False or (rt.oos_sharpe is not None and rt.oos_sharpe < rt.min_oos_sharpe):
        soft.append("WEAK_OOS_EDGE")
    if rt.oos_trades is not None and rt.oos_trades < rt.min_oos_trades:
        soft.append("LOW_TRADE_COUNT")
    # --- korelasyon / yığılma (YUMUŞAK) ---
    if rt.corr_btc is not None and rt.same_direction_open and abs(rt.corr_btc) > rt.max_corr_same_dir and rt.same_direction_open >= 2:
        soft.append("HIGH_CORRELATION_EXPOSURE")
    if rt.same_direction_open is not None and rt.same_direction_open >= 3:
        soft.append("CROWDED_SAME_DIRECTION")
    # --- rejim uyumsuzluğu (YUMUŞAK) ---
    if rt.btc_regime and rt.direction:
        bearish = rt.btc_regime in ("TREND_DOWN", "PANIC", "RISK-OFF")
        bullish = rt.btc_regime in ("TREND_UP", "EUPHORIC", "RISK-ON")
        if (bearish and rt.direction == "LONG") or (bullish and rt.direction == "SHORT"):
            soft.append("AGAINST_BTC_REGIME")
    # --- stop mesafesi: tercih dışı fakat GEÇERLİ → YUMUŞAK (geçersiz geometri head.py'de SERT) ---
    if rt.stop_pct is not None and rt.atr_pct:
        k = rt.stop_pct / rt.atr_pct
        if k > rt.max_stop_atr_mult:
            soft.append("STOP_TOO_FAR")
        elif k < rt.min_stop_atr_mult:
            soft.append("STOP_TOO_CLOSE")
    # --- likidasyon geometrisi: stop'tan ÖNCE likidasyon gerçekten geçersizdir (SERT) ---
    if rt.liq_distance_pct is not None and rt.stop_pct:
        if rt.liq_distance_pct <= rt.stop_pct:
            hard.append("LIQ_BEFORE_STOP")
        elif rt.liq_distance_pct < rt.min_liq_stop_ratio * rt.stop_pct:
            soft.append("LIQ_BUFFER_THIN")
    # --- funding (YUMUŞAK: maliyettir, yasak değil; expected_cost_pct'e zaten girer) ---
    if rt.funding_pct is not None and rt.direction:
        if (rt.direction == "LONG" and rt.funding_pct > rt.funding_extreme_pct) or (rt.direction == "SHORT" and rt.funding_pct < -rt.funding_extreme_pct):
            soft.append("FUNDING_EXTREME")
    if rt.funding_z is not None and rt.direction and ((rt.direction == "LONG" and rt.funding_z > 2.5) or (rt.direction == "SHORT" and rt.funding_z < -2.5)):
        soft.append("FUNDING_CROWDED")
    # --- gerçek güvenlik/uygulanabilirlik bayrakları (SERT) ---
    for flag, code in ((rt.sources_conflict, "SOURCES_CONFLICT"), (rt.llm_schema_invalid, "LLM_SCHEMA_INVALID"),
                       (rt.min_order_conflict, "MIN_ORDER_CONFLICT"), (rt.risk_limit_hit, "RISK_LIMIT"),
                       (rt.kill_switch_active, "KILL_SWITCH_ACTIVE"), (rt.model_drift, "MODEL_DRIFT"),
                       (rt.delist_flag, "DELIST_RISK"), (rt.clock_or_api_issue, "CLOCK_OR_API_ISSUE")):
        if flag:
            hard.append(code)
    # --- yeni listelenme (YUMUŞAK: geçmiş kısa → belirsizlik cezası, yasak değil) ---
    if rt.listing_age_days is not None and rt.listing_age_days < rt.min_listing_days:
        soft.append("NEW_LISTING")
    return list(dict.fromkeys(hard)), list(dict.fromkeys(soft))


def RedTeamVetoAgent(ctx: SpecialistContext, rt: RedTeamContext) -> SpecialistReport:
    """`rep.veto` YALNIZ sert kodlarda True olur. Yumuşak kodlar `soft_penalty_codes` ile taşınır."""
    t0 = time.time()
    rep = _base(ctx, "red_team_veto", "risk")
    hard, soft = review(rt)
    rep.metrics = {"hard_veto_codes": hard, "soft_penalty_codes": soft, "direction": rt.direction,
                   "vetoes": hard, "warnings": soft}          # geriye uyumlu anahtarlar
    rep.warnings = soft
    rep.bias = 0.0
    rep.confidence_raw = 90 if hard else 60
    if hard:
        rep.veto, rep.veto_reason = True, "NO_TRADE_RED_TEAM_VETO: " + ", ".join(hard)
        rep.evidence_against = [f"VETO {c}" for c in hard]
    else:
        rep.evidence_for = ["Red team sert veto bulamadı"] + [f"yumuşak: {x}" for x in soft]
    return _finish(rep, t0)


def assert_classification_matches_registry() -> None:
    """Kaynak sözleşmesi: red-team sınıflandırması `decision_gates.GATES` ile birebir aynı olmalı."""
    for code in HARD_VETO_CODES:
        if gate_class(code) != HARD_SAFETY:
            raise ValueError(f"{code} red-team'de SERT ama kayıtta {gate_class(code)}")
    for code in SOFT_PENALTY_CODES:
        if gate_class(code) != SOFT_EVIDENCE:
            raise ValueError(f"{code} red-team'de YUMUŞAK ama kayıtta {gate_class(code)}")
