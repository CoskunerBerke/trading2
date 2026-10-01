"""Quant Evaluation V1 — Risk Engine V2 (advisory) testleri.

Kapsam: volatilite ölçekleme, korelasyon kümeleri, aynı yönlü LONG maruziyeti, yönlü toplamlar,
eksik veri fallback'i, kaldıraç 2–5 bandı, advisory-only zorlaması, mevcut dış sınırların
korunması ve kill-switch/dış sınır önceliği (öneri asla sınırı gevşetemez).
"""
from __future__ import annotations

import pytest

from tradingbot.quant.risk_v2 import (ABS_MAX_LEVERAGE, ABS_MIN_LEVERAGE, AdviceContext,
                                      RiskV2Config, advise, cluster_exposure,
                                      correlation_clusters, realized_vol_pct,
                                      rolling_correlation, vol_target_scale)


def test_realized_vol_safe_on_missing_and_nonfinite():
    assert realized_vol_pct([]) is None
    assert realized_vol_pct([1.0, float("nan"), float("inf")]) is None   # sonlu obs < min
    v = realized_vol_pct([1.0, -1.0] * 10, window=20, min_obs=10)
    assert v is not None and v > 0


def test_vol_target_scaling_never_increases_risk():
    scale_hi, why_hi = vol_target_scale(8.0, 2.0)
    assert scale_hi == pytest.approx(0.25) and "SCALED_DOWN" in why_hi
    scale_lo, _ = vol_target_scale(0.5, 2.0)
    assert scale_lo == 1.0                                   # düşük vol risk BÜYÜTMEZ
    scale_na, why_na = vol_target_scale(None, 2.0)
    assert scale_na == 0.5 and why_na == "VOL_UNKNOWN_CONSERVATIVE"


def test_correlated_long_positions_form_single_cluster():
    base = [1.0, -0.5, 2.0, -1.0, 0.5, 1.5, -0.8, 0.9, -0.2, 1.1] * 3
    rets = {"AAVE/USDT": base, "ETH/USDT": [x * 1.1 for x in base],
            "LDO/USDT": [x * 0.9 for x in base],
            "QQQ/USDT": [(-x) for x in base]}                # negatif korelasyon → ayrı
    corr = rolling_correlation(rets, window=30, min_obs=20)
    assert corr[("AAVE/USDT", "ETH/USDT")] == pytest.approx(1.0, abs=1e-6)
    clusters = correlation_clusters(corr, rets, threshold=0.7)
    grouped = next(c for c in clusters if len(c) > 1)
    assert set(grouped) == {"AAVE/USDT", "ETH/USDT", "LDO/USDT"}
    assert ["QQQ/USDT"] in clusters                          # negatif korelasyon kümelenmedi


def test_insufficient_corr_obs_returns_none_not_guess():
    corr = rolling_correlation({"A": [1.0] * 5, "B": [1.0] * 5}, min_obs=20)
    assert corr[("A", "B")] is None


def test_cluster_and_directional_exposure():
    clusters = [["AAVE/USDT", "ETH/USDT", "LDO/USDT"], ["QQQ/USDT"]]
    pos = [{"symbol": "AAVE/USDT", "direction": "LONG", "risk_usdt": 10},
           {"symbol": "ETH/USDT", "direction": "LONG", "risk_usdt": 15},
           {"symbol": "LDO/USDT", "direction": "LONG", "risk_usdt": 5},
           {"symbol": "QQQ/USDT", "direction": "SHORT", "risk_usdt": 10},
           {"symbol": "BOZUK", "direction": "LONG", "risk_usdt": float("nan")}]
    rep = cluster_exposure(pos, clusters)
    c0 = rep["clusters"][0]
    assert c0["long_usdt"] == pytest.approx(30.0)            # üç LONG tek küme = tek bahis
    assert c0["share_of_total"] == pytest.approx(0.75)
    assert rep["total_long_usdt"] == pytest.approx(30.0)
    assert rep["total_short_usdt"] == pytest.approx(10.0)    # NaN pozisyon sessizce sayılmadı


def test_advise_stays_in_2_5_band_and_never_raises_proposal():
    out = advise(AdviceContext("ETH/USDT", "LONG", proposed_leverage=9,
                               symbol_vol_pct=1.0, calibrated_edge=0.1))
    assert ABS_MIN_LEVERAGE <= out["advised_leverage"] <= ABS_MAX_LEVERAGE
    assert out["advised_leverage"] <= ABS_MAX_LEVERAGE < 9   # sınır aşılmadı
    ok = advise(AdviceContext("ETH/USDT", "LONG", proposed_leverage=4,
                              symbol_vol_pct=1.0, calibrated_edge=0.1))
    assert ok["advised_leverage"] <= 4                       # öneri teklifi asla aşamaz
    assert ok["advisory_only"] is True and "outer_limits" in ok


def test_missing_data_and_uncertainty_reduce_never_increase():
    conservative = advise(AdviceContext("SOL/USDT", "LONG", proposed_leverage=5,
                                        symbol_vol_pct=None, model_uncertainty=0.9,
                                        data_quality_ok=False))
    assert conservative["advised_leverage"] < 5
    assert "VOL_UNKNOWN_CONSERVATIVE" in conservative["reasons"]
    assert "DATA_QUALITY_DEGRADED_DERISK" in conservative["reasons"]
    assert conservative["risk_scale"] <= 1.0


def test_drawdown_cluster_and_edge_gates():
    out = advise(AdviceContext("AAVE/USDT", "LONG", proposed_leverage=5,
                               symbol_vol_pct=1.0, portfolio_drawdown_pct=10.0,
                               cluster_share=0.8, calibrated_edge=-0.05))
    assert out["advised_leverage"] == ABS_MIN_LEVERAGE
    assert out["stand_aside"] is True
    assert {"PORTFOLIO_DRAWDOWN_DERISK", "CLUSTER_CONCENTRATION_HIGH",
            "NO_CALIBRATED_EDGE"} <= set(out["reasons"])


def test_advisory_only_is_enforced_fail_closed():
    with pytest.raises(ValueError, match="RISK_V2_ADVISORY_ONLY"):
        advise(AdviceContext("ETH/USDT", "LONG", 3), RiskV2Config(advisory_only=False))
    cfg = RiskV2Config()
    assert cfg.enabled is False                              # default disabled
    with pytest.raises(ValueError):
        RiskV2Config(cluster_threshold=1.5).validate()


def test_advise_is_pure_and_deterministic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                              # dosya yazsaydı burada görünürdü
    ctx = AdviceContext("ETH/USDT", "SHORT", 4, symbol_vol_pct=3.0, calibrated_edge=0.05)
    a, b = advise(ctx), advise(ctx)
    assert a == b
    assert list(tmp_path.iterdir()) == []                    # hiçbir dosya/emir/outbox üretilmedi
