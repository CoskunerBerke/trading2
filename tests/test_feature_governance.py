"""Feature yönetişimi — geniş ölç, dar karar ver.

Kabul kanıtları (görev §15/11-18): indikatör tek başına veto olamaz, aileler ve karar-düzeyi
yumuşak girdiler tavanlıdır, aynı bilgiyi ölçenler bağımsız tam ağırlık alamaz, research-only
karar değiştiremez, katkılar açıklanabilir ve sonlu, ablation OOS katkıyı ölçer.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from tradingbot.decision_gates import GATES, HARD_SAFETY, SOFT_EVIDENCE, is_hard
from tradingbot.learn.ablation import ablation_report
from tradingbot.learn.feature_registry import (DECISION_LEVEL_SOFT_INPUTS, FAMILIES,
                                               REGISTRY, RESEARCH_ONLY, SOFT_SCORE,
                                               FeatureGovernanceError, active_feature_names,
                                               active_families, family_of,
                                               feature_contributions, redundancy_groups,
                                               validate_registry)
from tradingbot.learn.features import build_features, feature_names, to_vector
from tradingbot.learn.model import LogisticModel


# ================================================================== 11) eksik feature ≠ NO_TRADE

def test_11_missing_optional_feature_never_blocks_alone():
    """Opsiyonel feature eksikliği UNAVAILABLE'dır; tek başına NO_TRADE üretemez."""
    for s in REGISTRY.values():
        if s.cls == SOFT_SCORE:
            assert s.missing_ok, f"{s.name}: SOFT feature eksikliği bloklayıcı OLAMAZ"
    # eksik girdi sıfır uydurmaz → sınırlı varsayılan; vektör her zaman sonlu
    feats = build_features({})                       # HİÇBİR alan yok
    vec = to_vector(feats, feature_names())
    assert all(math.isfinite(v) for v in vec)
    # hiçbir SOFT feature adı sert kapı koduna karşılık gelmez
    for s in REGISTRY.values():
        assert s.name.upper() not in GATES, f"{s.name} bir kapı kodu değil, feature'dır"


# ================================================================== 12) indikatör veto olamaz

_INDICATOR_SOFT_CODES = ("MA_POSITION", "MA_CROSS", "RSI_LEVEL", "MOMENTUM_WEAK",
                         "TREND_MISALIGNED", "VOLUME_WEAK", "VOL_REGIME_HIGH",
                         "FUNDING_ADVERSE", "BTC_CORRELATION", "PATTERN_WEAK",
                         "LOW_CONSENSUS", "HIGH_DISSENT", "LOW_CONFIDENCE")

#: SERT veto YALNIZCA bu kategorilerden gelebilir (görev §5 HARD_GATE listesi).
_ALLOWED_HARD = {
    # veri bütünlüğü / geçerlilik
    "DATA_INVALID", "TIMESTAMP_LEAKAGE", "STALE_DATA", "MISSING_4H_FRAME",
    "CLOCK_OR_API_ISSUE", "SOURCES_CONFLICT", "LLM_SCHEMA_INVALID", "MODEL_DRIFT",
    # uygunluk / trading status
    "MARKET_UNAVAILABLE", "DELIST_RISK",
    # min qty/notional & geometri
    "NO_TRADE_MIN_ORDER_CONFLICT", "STEP_ZERO_QTY", "MIN_ORDER_CONFLICT",
    "STOP_PRESENT", "ZERO_STOP_DISTANCE", "PLAN_GEOMETRY_INVALID", "LIQ_BEFORE_STOP",
    # kritik likidite / yürütme uygunluğu
    "LIQUIDITY_UNTRADEABLE",
    # maliyet sonrası negatif edge
    "NEGATIVE_NET_EDGE", "COSTS_EXCEED_EDGE",
    # duplicate / açık pozisyon
    "DUPLICATE_SIGNAL", "ALREADY_OPEN_SAME_SYMBOL", "OPPOSITE_EXPOSURE_CONFLICT",
    # portföy & aktif RiskEngine sınırları
    "TOTAL_OPEN_RISK", "MARGIN_UTILIZATION", "LIQ_BUFFER_TOO_THIN",
    "RISK_CAPACITY_BLOCKED", "RISK_LIMIT", "MAX_POSITIONS", "MAX_POSITIONS_MARKET",
    "SIZE_MULTIPLIER_ZERO",
    # PAPER/LIVE güvenliği & kill switch & bütünlük
    "MODE_NOT_TRADEABLE", "KILL_SWITCH_ACTIVE", "SHUTDOWN_REQUESTED",
    "RISK_STATE_PERSIST_FAILED", "GAP_RECONCILE_PENDING", "UNKNOWN_GATE_CODE",
    "RED_TEAM_HARD_VETO",
}


def test_12_indicator_codes_are_soft_and_hard_set_is_safety_only():
    for code in _INDICATOR_SOFT_CODES:
        assert GATES[code].cls == SOFT_EVIDENCE, f"{code} indikatör kanıtıdır, SERT OLAMAZ"
        assert not is_hard(code)
    hard = {c for c, g in GATES.items() if g.cls == HARD_SAFETY}
    extra = hard - _ALLOWED_HARD
    assert not extra, f"izinli kategori dışında SERT kapı: {sorted(extra)}"
    # red-team sert kodları da aynı güvenlik kümesinin altında
    from tradingbot.coinhead.redteam import HARD_VETO_CODES
    assert set(HARD_VETO_CODES) <= _ALLOWED_HARD


# ================================================================== 13) yedekler aynı aile

def test_13_same_information_features_share_family_and_group():
    groups = redundancy_groups()
    assert "momentum_oscillator" in groups
    assert {"bias_momentum", "rsi4_dir"} <= set(groups["momentum_oscillator"]), \
        "RSI yönü ile momentum ajanı AYNI bilgiyi ölçer — ayrı bağımsız kanıt olamaz"
    assert {"funding_dir", "funding_z"} <= set(groups.get("funding", []))
    for gname, members in groups.items():
        fams = {family_of(m) for m in members}
        assert len(fams) == 1, f"{gname}: yedek grup TEK ailede olmalı ({fams})"


# ================================================================== 14) tavanlar uygulanır

def test_14_family_and_soft_input_caps_enforced():
    rep = validate_registry(max_families=8, max_soft_inputs=12)
    assert rep["n_families"] <= 8 and rep["n_decision_soft_inputs"] <= 12
    assert set(rep["families"]) <= set(FAMILIES)
    with pytest.raises(FeatureGovernanceError, match="FEATURE_FAMILY_CAP"):
        validate_registry(max_families=7)             # mevcut 8 aile 7 tavanına sığmaz
    with pytest.raises(FeatureGovernanceError, match="SOFT_INPUT_CAP"):
        validate_registry(max_soft_inputs=11)


def test_14b_config_rejects_cap_violation_fail_closed(tmp_path, monkeypatch):
    from tradingbot.config import load_config
    from tradingbot.core import ConfigError
    cfgp = tmp_path / "config.yaml"
    cfgp.write_text("learning_v3:\n  max_active_families: 3\n", encoding="utf-8")
    monkeypatch.setenv("TRADINGBOT_STATE_DIR", str(tmp_path / "st"))
    with pytest.raises(ConfigError, match="FEATURE_GOVERNANCE"):
        load_config(cfgp)


# ================================================================== 15) research-only karara giremez

def test_15_research_only_features_are_not_in_active_vector():
    active = set(active_feature_names())
    for s in REGISTRY.values():
        if s.cls == RESEARCH_ONLY:
            assert s.name not in active, f"{s.name} kanıt olmadan aktif vektöre GİREMEZ"
    # aktif vektör mevcut şemayla BİREBİR aynı — davranış/şema hash'i değişmedi
    assert active == set(feature_names())
    from tradingbot.learn.snapshot import prediction_schema_hash
    assert isinstance(prediction_schema_hash(), str) and len(prediction_schema_hash()) >= 8


# ================================================================== 16) katkılar sonlu/açıklanabilir

def _fixture_model(n: int = 200, seed: int = 5):
    rng = np.random.default_rng(seed)
    names = feature_names()
    X = rng.normal(0, 1, size=(n, len(names)))
    i_tr = names.index("bias_trend")
    y = (X[:, i_tr] + 0.3 * rng.normal(0, 1, n) > 0).astype(float)   # sinyal: trend
    m = LogisticModel(feature_names=list(names))
    m.fit(X, y)
    return m, X, y, names


def test_16_contribution_sum_is_finite_and_explains_logit():
    m, X, _, names = _fixture_model()
    c = feature_contributions(m, X[0].tolist(), top=6)
    assert c is not None
    for row in c["top_positive"] + c["top_negative"]:
        assert math.isfinite(row["logit"]) and row["family"] in FAMILIES
    assert math.isfinite(c["logit_total"])
    assert abs(sum(c["by_family"].values()) - c["logit_total"]) < 1e-4, \
        "aile katkıları toplamı logit kaymasına eşit olmalı (açıklanabilirlik)"
    assert feature_contributions(m, None) is None      # veri yoksa uydurma yok
    assert feature_contributions(object(), X[0].tolist()) is None   # model hazır değil → None


# ================================================================== 17) ablation OOS katkı

def test_17_ablation_report_shows_incremental_oos_contribution():
    rng = np.random.default_rng(11)
    names = feature_names()
    n = 400
    X = rng.normal(0, 1, size=(n, len(names)))
    i_tr = names.index("bias_trend")
    y = (X[:, i_tr] > 0).astype(float)                 # yalnız TREND ailesi sinyal taşıyor
    rep = ablation_report(X, y, names=names, n_folds=3)
    assert not rep["insufficient_sample"]
    fams = rep["families"]
    assert fams["trend"]["contribution_logloss"] > 0.05, "sinyal ailesi OOS katkı göstermeli"
    assert fams["trend"]["verdict"] == "CARRIES_OOS_SIGNAL"
    noise = fams["volatility"]["contribution_logloss"]
    assert noise is not None and noise < fams["trend"]["contribution_logloss"] / 5, \
        "gürültü ailesi sinyal ailesinden açıkça küçük katkı göstermeli"
    assert "otomatik" in rep["note"].lower() or "RESEARCH" in rep["note"]


def test_17b_ablation_insufficient_sample_is_honest():
    names = feature_names()
    X = np.zeros((10, len(names)))
    y = np.array([0, 1] * 5, dtype=float)
    rep = ablation_report(X, y, names=names)
    assert rep["insufficient_sample"] is True and rep["families"] == {}


# ================================================================== 18) yedek çıkınca davranış korunur

def test_18_dropping_redundant_copy_preserves_decisions():
    """Aynı bilgiyi ölçen kopya feature çıkarıldığında karar İŞARETLERİ değişmez."""
    rng = np.random.default_rng(7)
    names = list(feature_names())
    n = 300
    X = rng.normal(0, 1, size=(n, len(names)))
    i_mom, i_rsi = names.index("bias_momentum"), names.index("rsi4_dir")
    X[:, i_rsi] = X[:, i_mom]                          # rsi4_dir = momentum KOPYASI
    y = (X[:, i_mom] > 0).astype(float)
    full = LogisticModel(feature_names=list(names))
    full.fit(X, y)
    X_drop = X.copy()
    X_drop[:, i_rsi] = 0.0                             # yedek çıkarıldı
    dropped = LogisticModel(feature_names=list(names))
    dropped.fit(X_drop, y)
    p_full = full.predict_proba(X)
    p_drop = dropped.predict_proba(X_drop)
    agree = float(np.mean((p_full > 0.5) == (p_drop > 0.5)))
    assert agree >= 0.98, f"yedek feature çıkınca kararlar korunmalı (uyum {agree:.3f})"
