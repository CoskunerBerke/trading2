"""Giriş seçiciliği challenger'ları (`entry_challenger_v1`) — SHADOW, saf, karar UYGULAMAZ.

Beş bağımsız aile. Her biri bir giriş snapshot'ı alır ve `ACCEPT` / `VETO` döndürür; hiçbiri
emir üretmez, defter/gateway/RiskEngine'e dokunmaz ve `applied` DAİMA `False`tur.

**Eşikler bu 19 işleme uydurulmamıştır.** Değerler ekonomik gerekçeden gelir:

* Kırılma noktası kazanma oranı, gerçekleşmiş ödeme oranından türetilir:
  `p* = 1 / (1 + payoff)`. Bir aday bunun altındaysa beklenti negatiftir.
* Maliyet/risk oranı tavanı, maliyetin R cinsinden anlamlı bir payı aşmaması ilkesinden gelir.
* Yoğunlaşma tavanları risk profilinin KENDİ açık risk bütçesinden türetilir.

**Üretimde ölçülen ve tasarımı belirleyen gerçek (2026-09-02, 19 kapanış):**

* `p_win` TERS ayrım yapıyor: kazananların ortalaması 0,343, kaybedenlerin 0,434. En büyük üç
  kazanç en düşük p_win'lerdeydi (0,243 / 0,272 / 0,390).
* `conservative_net_edge_r` de ters: kazanan 0,488, kaybeden 0,579.
* `expected_r` hiç ayırmıyor (1,936 vs 1,930) — plan geometrisi artefaktı.
* Ayrım gösteren tek alanlar: `consensus_score` (0,383 vs 0,195) ve `atr_pct` (1,97 vs 2,66).

Bu yüzden A ailesi (olasılık/edge) **bu örneklemde kazananları elerdi**. Challenger'lar yine de
tanımlanır ve ölçülür; hangisinin işe yaradığına 19 işlemle KARAR VERİLMEZ. n=5 kazanan
istatistiksel sonuç için çok azdır ve bu, raporun açık uyarısıdır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from typing import Any

from ..core import stable_id

SCHEMA_VERSION = "entry_challenger_v1"

ACCEPT, VETO = "ACCEPT", "VETO"

#: Challenger aileleri.
FAM_PROB = "A_calibrated_probability_edge"
FAM_REGIME = "B_regime_direction"
FAM_DISPERSION = "C_consensus_dispersion"
FAM_LIQUIDITY = "D_liquidity_cost_to_risk"
FAM_HEAT = "E_portfolio_heat_concentration"
FAMILIES = (FAM_PROB, FAM_REGIME, FAM_DISPERSION, FAM_LIQUIDITY, FAM_HEAT)

#: Gerekçe kodları.
R_OK = "PASSES_FILTER"
R_MISSING = "MISSING_DATA"
R_BELOW_BREAKEVEN = "P_WIN_BELOW_BREAKEVEN"
R_EDGE_NEGATIVE = "NET_EDGE_NOT_POSITIVE"
R_EDGE_UNCERTAIN = "EDGE_WITHIN_UNCERTAINTY"
R_REGIME_MISMATCH = "DIRECTION_AGAINST_REGIME"
R_REGIME_UNKNOWN = "REGIME_UNKNOWN"
R_LOW_CONSENSUS = "CONSENSUS_TOO_WEAK"
R_HIGH_DISPERSION = "SPECIALIST_DISPERSION_HIGH"
R_COST_TO_RISK = "COST_TO_RISK_TOO_HIGH"
R_ILLIQUID = "LIQUIDITY_INSUFFICIENT"
R_VOL_HIGH = "VOLATILITY_ABOVE_CAP"
R_HEAT = "PORTFOLIO_HEAT_HIGH"
R_CONCENTRATION = "DIRECTIONAL_CONCENTRATION"

#: Eksik veri bir VETO GEREKÇESİ DEĞİLDİR. Ölçemediğimiz için reddetmek, ölçtüğümüzü iddia
#: etmenin başka bir biçimidir; challenger `ACCEPT` döner ve `blockers` ile ölçemediğini söyler.
MISSING_MEANS_ACCEPT = True


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass
class EntryChallengerConfig:
    """Versiyonlu eşikler. Koda gömülü DEĞİL; `config.yaml` üzerinden gelir."""
    policy_version: str = "entry_v1.0.0"

    # --- A: olasılık / asgari net edge -------------------------------------------------
    #: Kırılma noktası bu ödeme oranından türetilir: p* = 1/(1+payoff). Gerçekleşmiş ödeme
    #: oranı verilirse o kullanılır; verilmezse bu muhafazakâr varsayılan.
    assumed_payoff_ratio: float = 1.5
    #: Kırılma noktasının üstünde istenen asgari emniyet payı (olasılık puanı).
    prob_safety_margin: float = 0.02
    #: Muhafazakâr net edge bu değerin altındaysa VETO.
    min_conservative_edge_r: float = 0.0

    # --- B: rejim / yön ------------------------------------------------------------------
    #: Yönün AÇIKÇA karşı olduğu rejimler (LONG için düşüş, SHORT için yükseliş).
    block_long_regimes: tuple[str, ...] = ("TREND_DOWN",)
    block_short_regimes: tuple[str, ...] = ("TREND_UP", "EUPHORIC")

    # --- C: konsensüs / dağılım ----------------------------------------------------------
    min_consensus_abs: float = 0.20
    #: Uzman skorlarının standart sapması bu değeri aşarsa (yön belirsiz) VETO.
    max_specialist_dispersion: float = 0.60
    max_dissent_ratio: float = 0.60

    # --- D: likidite / maliyet-risk ------------------------------------------------------
    #: Beklenen maliyetin stop mesafesine oranı tavanı (R cinsinden maliyet).
    max_cost_to_risk_r: float = 0.15
    max_spread_pct: float = 0.30
    max_atr_pct: float = 6.0

    # --- E: portföy ısısı / yoğunlaşma ---------------------------------------------------
    #: Açık riskin risk bütçesine oranı tavanı.
    max_open_risk_fraction: float = 0.80
    #: Aynı yönde azami eşzamanlı pozisyon.
    max_same_direction: int = 6

    def validate(self) -> None:
        if self.assumed_payoff_ratio <= 0:
            raise ValueError("assumed_payoff_ratio pozitif olmalı")
        if not (0.0 <= self.prob_safety_margin < 0.5):
            raise ValueError("prob_safety_margin [0, 0.5) aralığında olmalı")
        if self.max_cost_to_risk_r <= 0:
            raise ValueError("max_cost_to_risk_r pozitif olmalı")
        if not (0.0 < self.max_open_risk_fraction <= 1.0):
            raise ValueError("max_open_risk_fraction (0, 1] aralığında olmalı")
        if self.max_same_direction < 1:
            raise ValueError("max_same_direction >= 1 olmalı")
        if self.max_specialist_dispersion <= 0:
            raise ValueError("max_specialist_dispersion pozitif olmalı")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = list(v) if isinstance(v, tuple) else v
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "EntryChallengerConfig":
        d = dict(d or {})
        allowed = {f.name for f in fields(cls)}
        kw = {}
        for k, v in d.items():
            if k not in allowed:
                continue
            kw[k] = tuple(v) if k.startswith("block_") and isinstance(v, list) else v
        cfg = cls(**kw)
        cfg.validate()
        return cfg

    @property
    def config_id(self) -> str:
        return stable_id("entrycfg", self.policy_version, self.to_dict())

    def breakeven_p(self, payoff: float | None = None) -> float:
        """Kırılma noktası kazanma oranı: p* = 1 / (1 + payoff)."""
        r = _f(payoff) or self.assumed_payoff_ratio
        r = max(1e-6, r)
        return 1.0 / (1.0 + r)


def _verdict(family: str, decision: str, snap: dict[str, Any], cfg: EntryChallengerConfig, *,
             reasons: list[str], evidence: dict[str, Any],
             blockers: list[str] | None = None) -> dict[str, Any]:
    """Ortak sonuç zarfı. `applied` DAİMA False; bu modül hiçbir şey uygulamaz."""
    base_acc = snap.get("baseline_accepted")
    return {
        "schema_version": SCHEMA_VERSION,
        "family": family,
        "policy_version": cfg.policy_version,
        "config_id": cfg.config_id,
        "candidate_id": snap.get("candidate_id"),
        "decision_id": snap.get("decision_id"),
        "symbol": snap.get("symbol"),
        "direction": snap.get("direction"),
        "ts": snap.get("ts"),
        "decision": decision,
        "reason_codes": list(reasons),
        "evidence": evidence,
        "blockers": list(blockers or []),
        "baseline_decision": (None if base_acc is None else (ACCEPT if base_acc else VETO)),
        "counterfactual_decision": decision,
        "changes_baseline": (None if base_acc is None
                             else bool(base_acc and decision == VETO)),
        "applied": False,
        "note_tr": "SHADOW: karşı-olgusal karar; aktif emir yolunu ETKİLEMEZ.",
    }


def challenger_a(snap: dict[str, Any], cfg: EntryChallengerConfig, *,
                 realized_payoff: float | None = None) -> dict[str, Any]:
    """Kalibre olasılık ve asgari net edge.

    Kırılma noktası GERÇEKLEŞMİŞ ödeme oranından türetilir; sabit bir kazanma oranı eşiği
    dayatılmaz. `p_win` ölçülemezse VETO ÜRETİLMEZ (bkz. `MISSING_MEANS_ACCEPT`).
    """
    p = _f(snap.get("p_win"))
    edge = _f(snap.get("conservative_net_edge_r"))
    payoff = realized_payoff
    if payoff is None:
        w, l = _f(snap.get("avg_win_r")), _f(snap.get("avg_loss_r"))
        if w is not None and l:
            payoff = abs(w) / abs(l)
    p_star = cfg.breakeven_p(payoff) + cfg.prob_safety_margin
    ev = {"p_win": p, "breakeven_p": round(p_star, 6),
          "payoff_used": (round(payoff, 6) if payoff is not None else None),
          "conservative_net_edge_r": edge,
          "min_edge": cfg.min_conservative_edge_r}
    blockers = [f"{R_MISSING}:{k}" for k, v in (("p_win", p), ("conservative_net_edge_r", edge))
                if v is None]
    if blockers and MISSING_MEANS_ACCEPT:
        return _verdict(FAM_PROB, ACCEPT, snap, cfg, reasons=[R_MISSING], evidence=ev,
                        blockers=blockers)
    reasons = []
    if p is not None and p < p_star:
        reasons.append(R_BELOW_BREAKEVEN)
    if edge is not None and edge <= cfg.min_conservative_edge_r:
        reasons.append(R_EDGE_NEGATIVE)
    return _verdict(FAM_PROB, VETO if reasons else ACCEPT, snap, cfg,
                    reasons=reasons or [R_OK], evidence=ev, blockers=blockers)


def challenger_b(snap: dict[str, Any], cfg: EntryChallengerConfig) -> dict[str, Any]:
    """Rejim ve yön uyumu. Rejim bilinmiyorsa VETO ÜRETİLMEZ."""
    reg = str(snap.get("regime") or "").upper()
    d = str(snap.get("direction") or "").upper()
    ev = {"regime": reg or None, "direction": d or None,
          "block_long": list(cfg.block_long_regimes), "block_short": list(cfg.block_short_regimes)}
    if not reg or reg == "NONE":
        return _verdict(FAM_REGIME, ACCEPT, snap, cfg, reasons=[R_REGIME_UNKNOWN], evidence=ev,
                        blockers=[f"{R_MISSING}:regime"])
    bad = (d.endswith("LONG") and reg in cfg.block_long_regimes) or \
          (d.endswith("SHORT") and reg in cfg.block_short_regimes)
    return _verdict(FAM_REGIME, VETO if bad else ACCEPT, snap, cfg,
                    reasons=[R_REGIME_MISMATCH] if bad else [R_OK], evidence=ev)


def challenger_c(snap: dict[str, Any], cfg: EntryChallengerConfig) -> dict[str, Any]:
    """Uzman konsensüsü ve dağılımı.

    Üretimde ölçülen tek gerçek ayırt edici alan buydu (kazanan 0,383 / kaybeden 0,195), fakat
    eşik o farka UYDURULMADI: `min_consensus_abs` coin head'in kendi tarihsel yön eşiğidir.
    """
    cons = _f(snap.get("consensus_score"))
    spec = snap.get("specialist_scores") if isinstance(snap.get("specialist_scores"), dict) else None
    disp = None
    if spec and len(spec) >= 3:
        vals = [v for v in spec.values() if isinstance(v, (int, float))]
        if len(vals) >= 3:
            mu = sum(vals) / len(vals)
            disp = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))
    nd, nv = _f(snap.get("n_dissent")), _f(snap.get("n_vetoes"))
    n_spec = len(spec) if spec else None
    diss_ratio = (nd / n_spec) if (nd is not None and n_spec) else None
    ev = {"consensus_score": cons, "abs_consensus": (abs(cons) if cons is not None else None),
          "min_consensus_abs": cfg.min_consensus_abs, "specialist_dispersion": (
              round(disp, 6) if disp is not None else None),
          "max_dispersion": cfg.max_specialist_dispersion,
          "n_dissent": nd, "n_vetoes": nv, "dissent_ratio": (
              round(diss_ratio, 6) if diss_ratio is not None else None)}
    blockers = []
    if cons is None:
        blockers.append(f"{R_MISSING}:consensus_score")
    if disp is None:
        blockers.append(f"{R_MISSING}:specialist_dispersion")
    if cons is None and MISSING_MEANS_ACCEPT:
        return _verdict(FAM_DISPERSION, ACCEPT, snap, cfg, reasons=[R_MISSING], evidence=ev,
                        blockers=blockers)
    reasons = []
    if cons is not None and abs(cons) < cfg.min_consensus_abs:
        reasons.append(R_LOW_CONSENSUS)
    if disp is not None and disp > cfg.max_specialist_dispersion:
        reasons.append(R_HIGH_DISPERSION)
    if diss_ratio is not None and diss_ratio > cfg.max_dissent_ratio:
        reasons.append(R_HIGH_DISPERSION)
    return _verdict(FAM_DISPERSION, VETO if reasons else ACCEPT, snap, cfg,
                    reasons=reasons or [R_OK], evidence=ev, blockers=blockers)


def challenger_d(snap: dict[str, Any], cfg: EntryChallengerConfig) -> dict[str, Any]:
    """Likidite ve maliyet/risk oranı.

    ÜRETİMDE BU AİLE BUGÜN HİÇ KARAR VEREMEZ: `spread_pct`, `est_slippage_pct`, `depth_ratio` ve
    `liquidity_ok` alanlarının tamamı 52 ACCEPTED kaydın 0'ında doluydu. Bu yüzden aile
    `MISSING_DATA` ile ACCEPT döner ve eksik alanları açıkça listeler; likidite ölçülmeden
    "likidite yetersiz" demek uydurma olurdu.
    """
    cost = _f(snap.get("expected_cost_pct"))
    stop_pct = _f(snap.get("stop_distance_pct"))
    spread = _f(snap.get("spread_pct"))
    atr = _f(snap.get("atr_pct"))
    liq_ok = snap.get("liquidity_ok")
    ctr = (cost / stop_pct) if (cost is not None and stop_pct) else None
    ev = {"expected_cost_pct": cost, "stop_distance_pct": stop_pct,
          "cost_to_risk_r": (round(ctr, 6) if ctr is not None else None),
          "max_cost_to_risk_r": cfg.max_cost_to_risk_r,
          "spread_pct": spread, "max_spread_pct": cfg.max_spread_pct,
          "atr_pct": atr, "max_atr_pct": cfg.max_atr_pct, "liquidity_ok": liq_ok}
    blockers = [f"{R_MISSING}:{k}" for k, v in
                (("spread_pct", spread), ("liquidity_ok", liq_ok),
                 ("est_slippage_pct", _f(snap.get("est_slippage_pct"))),
                 ("depth_ratio", _f(snap.get("depth_ratio")))) if v is None]
    reasons = []
    if ctr is not None and ctr > cfg.max_cost_to_risk_r:
        reasons.append(R_COST_TO_RISK)
    if spread is not None and spread > cfg.max_spread_pct:
        reasons.append(R_ILLIQUID)
    if liq_ok is False:
        reasons.append(R_ILLIQUID)
    if atr is not None and atr > cfg.max_atr_pct:
        reasons.append(R_VOL_HIGH)
    if not reasons and blockers and ctr is None:
        return _verdict(FAM_LIQUIDITY, ACCEPT, snap, cfg, reasons=[R_MISSING], evidence=ev,
                        blockers=blockers)
    return _verdict(FAM_LIQUIDITY, VETO if reasons else ACCEPT, snap, cfg,
                    reasons=reasons or [R_OK], evidence=ev, blockers=blockers)


def challenger_e(snap: dict[str, Any], cfg: EntryChallengerConfig, *,
                 risk_budget_usdt: float | None = None) -> dict[str, Any]:
    """Portföy ısısı ve yön yoğunlaşması.

    Tavan risk profilinin KENDİ bütçesinden türetilir; ayrı bir sayı uydurulmaz.
    """
    open_risk = _f(snap.get("portfolio_open_risk_usdt"))
    same_dir = _f(snap.get("same_direction_open"))
    frac = (open_risk / risk_budget_usdt) if (open_risk is not None and risk_budget_usdt) else None
    ev = {"portfolio_open_risk_usdt": open_risk, "risk_budget_usdt": risk_budget_usdt,
          "open_risk_fraction": (round(frac, 6) if frac is not None else None),
          "max_open_risk_fraction": cfg.max_open_risk_fraction,
          "same_direction_open": same_dir, "max_same_direction": cfg.max_same_direction,
          "open_positions": _f(snap.get("portfolio_open_positions"))}
    blockers = [f"{R_MISSING}:{k}" for k, v in
                (("portfolio_open_risk_usdt", open_risk),
                 ("same_direction_open", same_dir)) if v is None]
    if frac is None and same_dir is None and MISSING_MEANS_ACCEPT:
        return _verdict(FAM_HEAT, ACCEPT, snap, cfg, reasons=[R_MISSING], evidence=ev,
                        blockers=blockers)
    reasons = []
    if frac is not None and frac > cfg.max_open_risk_fraction:
        reasons.append(R_HEAT)
    if same_dir is not None and same_dir >= cfg.max_same_direction:
        reasons.append(R_CONCENTRATION)
    return _verdict(FAM_HEAT, VETO if reasons else ACCEPT, snap, cfg,
                    reasons=reasons or [R_OK], evidence=ev, blockers=blockers)


def evaluate_all(snap: dict[str, Any], cfg: EntryChallengerConfig, *,
                 realized_payoff: float | None = None,
                 risk_budget_usdt: float | None = None) -> dict[str, dict[str, Any]]:
    """Beş ailenin kararı — AYRI AYRI. Birleşik bir "süper filtre" bilinçli olarak ÜRETİLMEZ:
    aileleri birleştirmek, hangi gerekçenin işe yaradığını ölçülemez hâle getirir."""
    return {
        FAM_PROB: challenger_a(snap, cfg, realized_payoff=realized_payoff),
        FAM_REGIME: challenger_b(snap, cfg),
        FAM_DISPERSION: challenger_c(snap, cfg),
        FAM_LIQUIDITY: challenger_d(snap, cfg),
        FAM_HEAT: challenger_e(snap, cfg, risk_budget_usdt=risk_budget_usdt),
    }


__all__ = ["SCHEMA_VERSION", "ACCEPT", "VETO", "FAMILIES", "FAM_PROB", "FAM_REGIME",
           "FAM_DISPERSION", "FAM_LIQUIDITY", "FAM_HEAT", "MISSING_MEANS_ACCEPT",
           "EntryChallengerConfig", "challenger_a", "challenger_b", "challenger_c",
           "challenger_d", "challenger_e", "evaluate_all",
           "R_OK", "R_MISSING", "R_BELOW_BREAKEVEN", "R_EDGE_NEGATIVE", "R_EDGE_UNCERTAIN",
           "R_REGIME_MISMATCH", "R_REGIME_UNKNOWN", "R_LOW_CONSENSUS", "R_HIGH_DISPERSION",
           "R_COST_TO_RISK", "R_ILLIQUID", "R_VOL_HIGH", "R_HEAT", "R_CONCENTRATION"]
