"""Feature yönetişimi — geniş ölç, DAR karar ver.

İlke: bot parametreye boğulmaz. Her feature TAM OLARAK bir sınıfa aittir:

* ``HARD_GATE``     — yalnız veri bütünlüğü / uygunluk / min-notional / negatif net edge /
  duplicate / risk kapasitesi / PAPER-LIVE güvenliği / kill switch sert veto olabilir.
  Kayıt `decision_gates.GATES`tedir; İNDİKATÖR TEK BAŞINA SERT VETO OLAMAZ (testle sabit).
* ``SOFT_SCORE``    — aktif skor girdisi. Sekiz bağımsız bilgi ailesinden birine aittir;
  aynı bilgiyi ölçen değişkenler (`redundancy_group`) ayrı ayrı tam bağımsız kanıt sayılmaz.
* ``RESEARCH_ONLY`` — yalnız kaydedilir/gölgede ölçülür; OOS ablation kanıtı olmadan aktif
  kararı DEĞİŞTİREMEZ. Aktivasyon otomatik değildir (operatör sözleşmesi).

Aktif model vektörü `active_feature_names()` = mevcut `feature_names()` — bu modül davranışı
DEĞİŞTİRMEZ; sınıflandırır, sınırlar ve açıklanabilir kılar. Şema hash'i aynen korunur.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .features import REGIMES, feature_names

REGISTRY_VERSION = "feature_registry_v1"

HARD_GATE, SOFT_SCORE, RESEARCH_ONLY = "HARD_GATE", "SOFT_SCORE", "RESEARCH_ONLY"

#: Sekiz bağımsız bilgi ailesi — aile sayısı tavanı config'ten (varsayılan 8) doğrulanır.
FAMILIES = ("regime_structure", "trend", "momentum", "volatility",
            "volume_liquidity", "mtf_alignment", "cost_expectancy", "memory_experience")

#: Karar düzeyi yumuşak girdiler — üst sınır config'ten (varsayılan 12) doğrulanır.
#: Bunlar kabul/boyut kararına DOĞRUDAN giren sayılardır; 52 boyutlu model vektörü bu
#: girdilerden yalnız `p_win_calibrated`ın İÇ temsilidir (ayrı ayrı karar girdisi değildir).
DECISION_LEVEL_SOFT_INPUTS = (
    "consensus_score", "consensus_confidence", "p_win_calibrated", "expected_r",
    "conservative_net_edge_r", "opportunity_score", "learning_influence_fraction",
    "regime_code", "atr_pct", "scan_score", "soft_penalty_r", "research_multiplier")


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    family: str
    cls: str = SOFT_SCORE
    redundancy_group: str | None = None       # aynı bilgiyi ölçenler aynı grupta
    source: str = "coin_head_snapshot"        # OBSERVED kaynak sınıfı ayrıca `data_sources`ta
    missing_ok: bool = True                   # eksikse UNAVAILABLE; tek başına NO_TRADE ÜRETMEZ
    note: str = ""


def _spec(name: str, family: str, **kw: Any) -> FeatureSpec:
    return FeatureSpec(name=name, family=family, **kw)


#: 52 aktif model feature'ı + araştırma alanları. Her feature TEK ailede.
_SPECS: tuple[FeatureSpec, ...] = (
    # --- trend (fiyat yapısı dahil) ---
    _spec("bias_trend", "trend"), _spec("conf_trend", "trend"),
    _spec("bias_candles", "trend", redundancy_group="price_action",
          note="mum yapısı fiyat-yapısı bilgisidir; trend ile aynı aile"),
    _spec("conf_candles", "trend", redundancy_group="price_action"),
    # --- momentum (osilatörler TEK grup: ayrı ayrı tam kanıt sayılmaz) ---
    _spec("bias_momentum", "momentum", redundancy_group="momentum_oscillator"),
    _spec("conf_momentum", "momentum", redundancy_group="momentum_oscillator"),
    _spec("rsi4_dir", "momentum", redundancy_group="momentum_oscillator",
          note="RSI yönü momentum ajanıyla AYNI bilgiyi ölçer"),
    # --- rejim / piyasa yapısı ---
    _spec("bias_market", "regime_structure"), _spec("conf_market", "regime_structure"),
    _spec("btc_align", "regime_structure", redundancy_group="btc_link"),
    _spec("corr_btc", "regime_structure", redundancy_group="btc_link"),
    _spec("beta_btc", "regime_structure", redundancy_group="btc_link"),
    _spec("is_breakout", "regime_structure"),
    _spec("data_freshness_s", "regime_structure",
          note="bağlam; SERT tazelik ihlali DATA_INVALID kapısındadır"),
    *[_spec(f"regime_{r}", "regime_structure", redundancy_group="regime_onehot")
      for r in REGIMES],
    # --- volatilite ---
    _spec("atr_pct", "volatility"),
    # --- hacim / likidite / akış ---
    _spec("bias_volume", "volume_liquidity"), _spec("conf_volume", "volume_liquidity"),
    _spec("ob_dir", "volume_liquidity"),
    _spec("spread_pct", "volume_liquidity", redundancy_group="microstructure"),
    _spec("depth_ratio", "volume_liquidity", redundancy_group="microstructure"),
    _spec("funding_dir", "volume_liquidity", redundancy_group="funding"),
    _spec("funding_z", "volume_liquidity", redundancy_group="funding",
          note="funding_dir ile AYNI kaynaktan türev"),
    _spec("oi_change_pct", "volume_liquidity"),
    # --- çoklu zaman dilimi / seviye uyumu ---
    _spec("bias_levels", "mtf_alignment"), _spec("conf_levels", "mtf_alignment"),
    _spec("scan_score", "mtf_alignment",
          note="tier-1 kompozit (1h/4h/1d yapı taraması)"),
    # --- maliyet sonrası beklenti / yürütme bağlamı ---
    _spec("rr", "cost_expectancy"), _spec("expected_r", "cost_expectancy"),
    _spec("expected_cost_pct", "cost_expectancy"),
    _spec("n_warnings", "cost_expectancy"),
    _spec("leverage", "cost_expectancy"), _spec("is_futures", "cost_expectancy"),
    # --- hafıza / geçmiş deneyim / meta-konsensüs ---
    _spec("bias_analog", "memory_experience"), _spec("conf_analog", "memory_experience"),
    _spec("bias_edge", "memory_experience"), _spec("conf_edge", "memory_experience"),
    _spec("p_win_prior", "memory_experience"),
    _spec("conviction", "memory_experience", redundancy_group="consensus_meta",
          note="coin head kanaat gücü — meta toplam, bağımsız kanıt değil"),
    _spec("consensus_score", "memory_experience", redundancy_group="consensus_meta",
          note="diğer ailelerin toplamı — meta; ayrı bağımsız kanıt DEĞİL"),
    _spec("consensus_conf", "memory_experience", redundancy_group="consensus_meta"),
    _spec("n_dissent", "memory_experience", redundancy_group="consensus_meta"),
    _spec("n_vetoes", "memory_experience", redundancy_group="consensus_meta"),
    # --- RESEARCH_ONLY: aktif vektörde YOK; kanıt olmadan karara giremez ---
    _spec("hour_sin", "regime_structure", cls=RESEARCH_ONLY,
          note="saat özelliği — leakage/overfit riski, varsayılan kapalı"),
    _spec("hour_cos", "regime_structure", cls=RESEARCH_ONLY),
    _spec("news_sentiment", "regime_structure", cls=RESEARCH_ONLY, source="UNAVAILABLE",
          note="point-in-time kaynak yok — yalnız kayıt sözleşmesi"),
    _spec("onchain_flow", "volume_liquidity", cls=RESEARCH_ONLY, source="UNAVAILABLE"),
    _spec("social_heat", "regime_structure", cls=RESEARCH_ONLY, source="UNAVAILABLE"),
)

REGISTRY: dict[str, FeatureSpec] = {s.name: s for s in _SPECS}


def active_feature_names() -> list[str]:
    """Aktif model vektörü — `feature_names()` ile BİREBİR aynı (davranış korunur)."""
    return feature_names()


def spec(name: str) -> FeatureSpec | None:
    return REGISTRY.get(str(name))


def family_of(name: str) -> str | None:
    s = REGISTRY.get(str(name))
    return s.family if s else None


def active_families() -> list[str]:
    fams = {s.family for s in REGISTRY.values() if s.cls == SOFT_SCORE}
    return sorted(fams)


def redundancy_groups() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in REGISTRY.values():
        if s.redundancy_group and s.cls == SOFT_SCORE:
            out.setdefault(s.redundancy_group, []).append(s.name)
    return {k: sorted(v) for k, v in sorted(out.items()) if len(v) > 1}


class FeatureGovernanceError(Exception):
    """Yönetişim ihlali — fail-closed: config doğrulaması reddeder."""


def validate_registry(*, max_families: int = 8, max_soft_inputs: int = 12) -> dict[str, Any]:
    """Tavanları ve bütünlüğü doğrular. İhlalde `FeatureGovernanceError` (fail-closed).

    * Aktif aile sayısı ≤ `max_families`.
    * Karar düzeyi yumuşak girdi sayısı ≤ `max_soft_inputs`.
    * Aktif vektördeki HER feature kayıtlı ve SOFT_SCORE olmalı.
    * RESEARCH_ONLY hiçbir feature aktif vektörde olamaz.
    """
    fams = active_families()
    if len(fams) > int(max_families):
        raise FeatureGovernanceError(
            f"FEATURE_FAMILY_CAP: {len(fams)} aile > tavan {max_families}: {fams}")
    if len(DECISION_LEVEL_SOFT_INPUTS) > int(max_soft_inputs):
        raise FeatureGovernanceError(
            f"SOFT_INPUT_CAP: {len(DECISION_LEVEL_SOFT_INPUTS)} girdi > tavan {max_soft_inputs}")
    unknown = [n for n in active_feature_names() if n not in REGISTRY]
    if unknown:
        raise FeatureGovernanceError(f"UNREGISTERED_ACTIVE_FEATURE: {unknown[:5]}")
    research_active = [n for n in active_feature_names()
                       if REGISTRY[n].cls == RESEARCH_ONLY]
    if research_active:
        raise FeatureGovernanceError(f"RESEARCH_ONLY_IN_ACTIVE_VECTOR: {research_active}")
    bad_family = [s.name for s in REGISTRY.values() if s.family not in FAMILIES]
    if bad_family:
        raise FeatureGovernanceError(f"UNKNOWN_FAMILY: {bad_family[:5]}")
    return {"registry_version": REGISTRY_VERSION,
            "n_active_features": len(active_feature_names()),
            "n_families": len(fams), "families": fams,
            "n_decision_soft_inputs": len(DECISION_LEVEL_SOFT_INPUTS),
            "decision_soft_inputs": list(DECISION_LEVEL_SOFT_INPUTS),
            "n_research_only": sum(1 for s in REGISTRY.values() if s.cls == RESEARCH_ONLY),
            "redundancy_groups": redundancy_groups()}


# ------------------------------------------------------------------ katkı açıklaması

def feature_contributions(model: Any, features_vector: list[float] | None,
                          *, top: int = 6) -> dict[str, Any] | None:
    """Lojistik modelin logit katkıları — `w_i * x_scaled_i` (finite, sınırlı, aile toplamıyla).

    Model hazır değilse None (uydurma yok). Toplam = logit kayması (bias hariç);
    açıklama kanıttan türer.
    """
    try:
        names = list(getattr(model, "feature_names", None) or [])
        weights = list(getattr(model, "weights", None) or [])
        scaler = getattr(model, "scaler", None)
        if not names or not weights or features_vector is None:
            return None
        mean = list(getattr(scaler, "mean", None) or [0.0] * len(names))
        std = list(getattr(scaler, "std", None) or [1.0] * len(names))
        contribs: list[tuple[str, float]] = []
        for i, name in enumerate(names):
            x = float(features_vector[i]) if i < len(features_vector) else 0.0
            sd = float(std[i]) if i < len(std) and std[i] else 1.0
            z = (x - float(mean[i]) if i < len(mean) else x) / sd
            c = float(weights[i]) * z
            if math.isfinite(c):
                contribs.append((name, c))
        if not contribs:
            return None
        by_family: dict[str, float] = {}
        for name, c in contribs:
            fam = family_of(name) or "unknown"
            by_family[fam] = by_family.get(fam, 0.0) + c
        contribs.sort(key=lambda t: -abs(t[1]))
        total = sum(c for _, c in contribs)
        return {"registry_version": REGISTRY_VERSION,
                "top_positive": [{"feature": n, "family": family_of(n),
                                  "logit": round(c, 6)}
                                 for n, c in contribs if c > 0][:top],
                "top_negative": [{"feature": n, "family": family_of(n),
                                  "logit": round(c, 6)}
                                 for n, c in contribs if c < 0][:top],
                "by_family": {k: round(v, 6) for k, v in
                              sorted(by_family.items(), key=lambda kv: -abs(kv[1]))},
                "logit_total": round(total, 6)}
    except Exception:  # noqa: BLE001 — açıklama arızası kararı ETKİLEYEMEZ
        return None


def summary() -> dict[str, Any]:
    """Dashboard için salt okunur envanter özeti."""
    by_cls: dict[str, int] = {}
    for s in REGISTRY.values():
        by_cls[s.cls] = by_cls.get(s.cls, 0) + 1
    return {"registry_version": REGISTRY_VERSION,
            "families": active_families(),
            "n_active": sum(1 for s in REGISTRY.values() if s.cls == SOFT_SCORE),
            "by_class": by_cls,
            "redundancy_groups": redundancy_groups(),
            "research_only": sorted(s.name for s in REGISTRY.values()
                                    if s.cls == RESEARCH_ONLY),
            "decision_soft_inputs": list(DECISION_LEVEL_SOFT_INPUTS)}


__all__ = ["DECISION_LEVEL_SOFT_INPUTS", "FAMILIES", "FeatureGovernanceError", "FeatureSpec",
           "HARD_GATE", "REGISTRY", "REGISTRY_VERSION", "RESEARCH_ONLY", "SOFT_SCORE",
           "active_feature_names", "active_families", "family_of", "feature_contributions",
           "redundancy_groups", "spec", "summary", "validate_registry"]
