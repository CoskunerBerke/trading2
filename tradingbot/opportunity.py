"""Fırsat değerlendirmesi — maliyet sonrası, belirsizlik ayarlı beklenti ve DİNAMİK pozisyon boyutu.

Karar artık "sabit R/R ≥ 1.5 VE konsensüs ≥ 0.22 VE güven ≥ 0.25 VE rejim uyumlu VE …" zinciriyle
değil, tek bir ekonomik büyüklükle verilir: **conservative_net_edge_r**.

    gross_expectancy_r      = p_win × avg_win_r − (1 − p_win) × |avg_loss_r|
    net_expectancy_r        = gross − cost_r            (yalnız maliyet HENÜZ düşülmediyse)
    conservative_net_edge_r = net − uncertainty_penalty_r − soft_penalty_r

MALİYETİN ÇİFT SAYILMAMASI: geçmiş `outcome.r_multiple` değerleri zaten fee/slippage/funding sonrası
NET ise `expectancy_basis = NET_OUTCOME` olur ve `cost_r = 0` alınır. Maliyet yalnız beklenti brüt
plan geometrisinden türetildiğinde (`GROSS_MINUS_COSTS`) düşülür. Bu ayrım testle korunur.

Boyut: güçlü ve belirsizliği düşük edge → tavana kadar; orta edge → küçük; point-estimate pozitif ama
belirsiz → araştırma boyutu/gölge; negatif → gerçek giriş yok. Boyut hiçbir koşulda risk profili
tavanını, toplam açık riski ya da margin kapasitesini aşamaz (bunlar risk motorunun HARD kapılarıdır).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .decision_gates import GateLedger, SoftSignal

NET_OUTCOME = "NET_OUTCOME"                    # avg_win/avg_loss zaten maliyet sonrası NET
GROSS_MINUS_COSTS = "GROSS_MINUS_COSTS"        # brüt geometri → maliyet burada düşülür
BASES = (NET_OUTCOME, GROSS_MINUS_COSTS)

# Boyutlandırma referansı: bu kadar muhafazakâr edge tam boyut sayılır (R cinsinden).
FULL_SIZE_EDGE_R = 0.35
MIN_TRADE_MULTIPLIER = 0.20                    # gerçek giriş için taban çarpan
RESEARCH_MULTIPLIER = 0.25                     # point-estimate pozitif ama belirsiz
UNCERTAINTY_K = 0.25                           # belirsizlik cezası ölçeği (SINIRLI)
BLEND_N = 20.0                                 # gerçekleşmiş istatistiğe kayış hızı


def uncertainty_penalty_r(sample_size: float, *, k: float = UNCERTAINTY_K) -> float:
    """Örnek azaldıkça büyüyen ceza: k / sqrt(n + 1). n→∞ iken 0'a gider, n=0'da k."""
    n = max(0.0, float(sample_size or 0.0))
    return round(k / math.sqrt(n + 1.0), 6)


@dataclass
class OpportunityAssessment:
    symbol: str
    side: str
    setup: str
    hard_block_codes: list[str] = field(default_factory=list)
    soft_evidence: list[dict] = field(default_factory=list)
    p_win_calibrated: float = 0.5
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    gross_expectancy_r: float = 0.0
    cost_r: float = 0.0
    net_expectancy_r: float = 0.0
    uncertainty_penalty_r: float = 0.0
    conservative_net_edge_r: float = 0.0
    opportunity_score: float = 0.0             # [0, 1] — sıralama/gösterim için sınırlı ölçek
    size_multiplier: float = 0.0
    risk_pct_requested: float = 0.0
    expectancy_basis: str = NET_OUTCOME
    sample_size: int = 0
    provenance: dict = field(default_factory=dict)

    # ------------------------------------------------------------ kararlar
    @property
    def blocked(self) -> bool:
        return bool(self.hard_block_codes)

    @property
    def tradeable(self) -> bool:
        """Gerçek giriş: sert engel yok VE muhafazakâr edge pozitif VE boyut anlamlı."""
        return (not self.blocked) and self.conservative_net_edge_r > 0 and self.size_multiplier > 0

    @property
    def research_only(self) -> bool:
        """Point-estimate pozitif ama belirsizlik yutuyor → küçük araştırma boyutu / gölge."""
        return (not self.blocked) and self.net_expectancy_r > 0 and self.conservative_net_edge_r <= 0

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "side": self.side, "setup": self.setup,
                "hard_block_codes": list(self.hard_block_codes), "soft_evidence": list(self.soft_evidence),
                "p_win_calibrated": round(self.p_win_calibrated, 6), "avg_win_r": round(self.avg_win_r, 6),
                "avg_loss_r": round(self.avg_loss_r, 6),
                "gross_expectancy_r": round(self.gross_expectancy_r, 6), "cost_r": round(self.cost_r, 6),
                "net_expectancy_r": round(self.net_expectancy_r, 6),
                "uncertainty_penalty_r": round(self.uncertainty_penalty_r, 6),
                "conservative_net_edge_r": round(self.conservative_net_edge_r, 6),
                "opportunity_score": round(self.opportunity_score, 6),
                "size_multiplier": round(self.size_multiplier, 6),
                "risk_pct_requested": round(self.risk_pct_requested, 6),
                "expectancy_basis": self.expectancy_basis, "sample_size": int(self.sample_size),
                "tradeable": self.tradeable, "research_only": self.research_only,
                "provenance": dict(self.provenance)}


def cost_in_r(cost_pct_notional: float | None, stop_dist_pct: float | None) -> float:
    """Maliyeti R cinsine çevirir: (% notional maliyet) / (% notional risk).

    `stop_dist_pct` yoksa ya da sıfırsa maliyet R'ye çevrilemez → çağıran taraf
    `ZERO_STOP_DISTANCE` sert engelini üretmelidir.
    """
    c, s = float(cost_pct_notional or 0.0), float(stop_dist_pct or 0.0)
    if s <= 0:
        return 0.0
    return round(max(0.0, c) / s, 6)


def assess(*, symbol: str, side: str, setup: str, gates: GateLedger,
           p_win: float, avg_win_r: float, avg_loss_r: float, sample_size: float,
           cost_pct_notional: float | None, stop_dist_pct: float | None,
           expectancy_basis: str = NET_OUTCOME, risk_per_trade_pct: float = 2.0,
           provenance: dict | None = None,
           full_size_edge_r: float = FULL_SIZE_EDGE_R) -> OpportunityAssessment:
    """Tek fırsatın ekonomik değerlendirmesi + dinamik boyutu.

    `expectancy_basis`:
      * ``NET_OUTCOME``        — `avg_win_r`/`avg_loss_r` gerçekleşmiş NET sonuçlardan geldi →
                                  maliyet ZATEN içinde, tekrar DÜŞÜLMEZ (`cost_r = 0`).
      * ``GROSS_MINUS_COSTS``  — brüt plan geometrisinden geldi → maliyet burada bir kez düşülür.
    """
    if expectancy_basis not in BASES:
        raise ValueError(f"bilinmeyen expectancy_basis: {expectancy_basis}")
    p = max(0.0, min(1.0, float(p_win)))
    win, loss = abs(float(avg_win_r)), abs(float(avg_loss_r))
    gross = p * win - (1.0 - p) * loss
    # ÇİFT SAYIM KORUMASI — maliyet yalnız brüt tabanda düşülür.
    cost_r = cost_in_r(cost_pct_notional, stop_dist_pct) if expectancy_basis == GROSS_MINUS_COSTS else 0.0
    net = gross - cost_r
    unc = uncertainty_penalty_r(sample_size)
    soft = gates.soft_penalty_r()
    conservative = net - unc - soft

    score = max(0.0, min(1.0, conservative / float(full_size_edge_r))) if full_size_edge_r > 0 else 0.0
    if gates.blocked:
        mult = 0.0
    elif conservative > 0:
        mult = round(max(MIN_TRADE_MULTIPLIER, min(1.0, MIN_TRADE_MULTIPLIER + (1.0 - MIN_TRADE_MULTIPLIER) * score)), 6)
    elif net > 0:
        mult = RESEARCH_MULTIPLIER                   # araştırma boyutu (küçük, sınırlandırılmış)
    else:
        mult = 0.0
    risk_pct = round(max(0.0, min(float(risk_per_trade_pct), float(risk_per_trade_pct) * mult)), 6)

    return OpportunityAssessment(
        symbol=symbol, side=side, setup=setup,
        hard_block_codes=list(gates.hard), soft_evidence=[s.to_dict() for s in gates.soft],
        p_win_calibrated=round(p, 6), avg_win_r=round(win, 6), avg_loss_r=round(-loss, 6),
        gross_expectancy_r=round(gross, 6), cost_r=cost_r, net_expectancy_r=round(net, 6),
        uncertainty_penalty_r=unc, conservative_net_edge_r=round(conservative, 6),
        opportunity_score=round(score, 6), size_multiplier=mult, risk_pct_requested=risk_pct,
        expectancy_basis=expectancy_basis, sample_size=int(sample_size or 0),
        provenance=dict(provenance or {}) | {"soft_penalty_r": round(soft, 6),
                                             "full_size_edge_r": float(full_size_edge_r)})


def rank(assessments: list[OpportunityAssessment]) -> list[OpportunityAssessment]:
    """Deterministik sıralama: muhafazakâr edge büyükten küçüğe, eşitlikte sembol/taraf.

    Adayların TAMAMI işlenmeden önce sıralanır — böylece daha güçlü üçüncü fırsat, daha zayıf iki
    fırsat yüzünden keyfi biçimde dışarıda kalmaz.
    """
    return sorted(assessments, key=lambda a: (-a.conservative_net_edge_r, -a.opportunity_score,
                                              a.symbol, a.side))


def hierarchical_expectancy(*, learner, symbol: str, side: str, setup: str, regime: str | None,
                            default_win_r: float = 1.6, default_loss_r: float = 1.0,
                            fallback_win_r: float | None = None, blend_n: float = BLEND_N) -> dict:
    """LearnerV2 hiyerarşik istatistiklerinden kalibre p_win + kazanç/kayıp dağılımı.

    Sembol/taraf/rejim/setup yaprağından başlar; örnek azsa kütüphanenin shrinkage'ı global prior'a
    doğru küçültür. Eksik değer gerçek 0 sayılmaz — örnek yoksa `n_eff` küçük kalır ve belirsizlik
    cezası büyür (bkz. `uncertainty_penalty_r`).
    """
    leaf = f"{symbol}|{setup}"
    try:
        p_win, n_eff = learner.win.estimate(regime=regime or None, leaf=leaf)
    except Exception:  # noqa: BLE001 — istatistik yoksa nötr prior
        p_win, n_eff = 0.5, 0.0
    try:
        exp_r, exp_n = learner.exp_r.estimate(regime=regime or None, leaf=f"{setup}|{side}")
    except Exception:  # noqa: BLE001
        exp_r, exp_n = 0.0, 0.0
    p = max(0.05, min(0.95, float(p_win)))
    # Gerçekleşmiş beklentiyi kazanç/kayıp büyüklüğüne çevir: p·W − (1−p)·L = exp_r, L sabit kabul.
    realised_win_r = max(0.1, (float(exp_r) + (1.0 - p) * default_loss_r) / p) if p > 0 else default_win_r
    # SOĞUK BAŞLANGIÇ: geçmiş yokken plan GEOMETRİSİ esas alınır; veri biriktikçe gerçekleşmiş
    # dağılıma kayılır. Aksi halde taze bir bot, yalnız "veri yok" diye hiç işlem açamazdı (starvation).
    n = max(float(n_eff or 0), float(exp_n or 0))
    w = n / (n + max(1e-9, float(blend_n)))
    base_win_r = float(fallback_win_r) if fallback_win_r else default_win_r
    win_r = w * realised_win_r + (1.0 - w) * max(0.1, base_win_r)
    return {"p_win": p, "avg_win_r": round(win_r, 6), "avg_loss_r": default_loss_r,
            "sample_size": int(n),
            "expectancy_basis": NET_OUTCOME,
            "provenance": {"leaf": leaf, "regime": regime, "n_eff_win": float(n_eff or 0),
                           "n_eff_exp_r": float(exp_n or 0), "realised_expectancy_r": round(float(exp_r), 6),
                           "geometry_blend_w": round(w, 4), "fallback_win_r": base_win_r}}


__all__ = ["BASES", "BLEND_N", "FULL_SIZE_EDGE_R", "GROSS_MINUS_COSTS", "MIN_TRADE_MULTIPLIER", "NET_OUTCOME",
           "OpportunityAssessment", "RESEARCH_MULTIPLIER", "SoftSignal", "assess", "cost_in_r",
           "hierarchical_expectancy", "rank", "uncertainty_penalty_r"]
