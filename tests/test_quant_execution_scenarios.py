"""Quant Evaluation V1 — execution maliyet senaryoları testleri.

Kritik ispatlar:
* base ≤ adverse ≤ stress maliyetleri MONOTON (hiçbir girdide tersine dönmez).
* Daha kötü execution PnL'i İYİLEŞTİREMEZ.
* Notional/katılım arttıkça modellenen slippage AZALAMAZ; volatilite arttıkça spread AZALAMAZ.
* Model SINIRLIDIR (bounded) — uçuk girdide bileşen tavanı aşılmaz.
* Provenance dürüst: historical bid/ask ve order-book `UNAVAILABLE`; latency `FALLBACK` ve
  milisaniye iddiası yok.
"""
from __future__ import annotations

import pytest

from tradingbot.quant.execution_scenarios import (ADVERSE, BASE, MAX_COMPONENT_BPS, SCENARIO_ORDER,
                                                  SCENARIOS, STRESS, UNAVAILABLE, apply_scenario,
                                                  compare_scenarios, one_way_cost_bps)


def _trade(i: int, gross=40.0, notional=1000.0, risk=20.0, vol=1.0, qv=500_000.0, gap=False):
    return {"symbol": "ETH/USDT" if i % 2 else "SOL/USDT", "gross_pnl": gross,
            "notional": notional, "risk_usdt": risk, "volatility_pct": vol,
            "bar_quote_volume": qv, "fees": 1.0, "funding": -0.5, "gap": gap}


def _mixed(n=20, gross_win=60.0, gross_loss=-25.0):
    return [_trade(i, gross=gross_win if i % 3 else gross_loss) for i in range(n)]


# --------------------------------------------------------------- monotonluk

@pytest.mark.parametrize("notional", [10.0, 1_000.0, 100_000.0])
@pytest.mark.parametrize("vol", [0.0, 1.5, 8.0])
@pytest.mark.parametrize("qv", [None, 1_000.0, 10_000_000.0])
def test_scenario_costs_monotonic_across_severity(notional, vol, qv):
    totals = [one_way_cost_bps(SCENARIOS[n], notional_usdt=notional, bar_quote_volume=qv,
                               volatility_pct=vol)["total_bps"] for n in SCENARIO_ORDER]
    assert totals[0] <= totals[1] <= totals[2], totals


@pytest.mark.parametrize("scenario", [BASE, ADVERSE, STRESS])
def test_larger_notional_never_reduces_modeled_slippage(scenario):
    prev = -1.0
    for notional in (1.0, 100.0, 1_000.0, 50_000.0, 5_000_000.0):
        c = one_way_cost_bps(scenario, notional_usdt=notional, bar_quote_volume=100_000.0,
                             volatility_pct=1.0)
        impact = c["components_bps"]["impact"]
        assert impact >= prev
        prev = impact


@pytest.mark.parametrize("scenario", [BASE, ADVERSE, STRESS])
def test_higher_volatility_never_reduces_spread(scenario):
    prev = -1.0
    for vol in (0.0, 0.5, 2.0, 10.0, 100.0):
        hs = one_way_cost_bps(scenario, notional_usdt=1000.0, bar_quote_volume=100_000.0,
                              volatility_pct=vol)["components_bps"]["half_spread"]
        assert hs >= prev
        prev = hs


def test_cost_model_is_bounded():
    c = one_way_cost_bps(STRESS, notional_usdt=1e12, bar_quote_volume=1.0, volatility_pct=1e9,
                         gap=True)
    for name, v in c["components_bps"].items():
        assert v <= MAX_COMPONENT_BPS + 1e-9, name
    assert c["total_bps"] < MAX_COMPONENT_BPS * 5


def test_missing_volume_is_conservative_not_optimistic():
    known = one_way_cost_bps(BASE, notional_usdt=10.0, bar_quote_volume=10_000_000.0,
                             volatility_pct=1.0)
    unknown = one_way_cost_bps(BASE, notional_usdt=10.0, bar_quote_volume=None, volatility_pct=1.0)
    assert unknown["components_bps"]["impact"] > known["components_bps"]["impact"]
    assert unknown["provenance"]["impact"] == "FALLBACK"
    assert known["provenance"]["impact"] == "MODELED"


# --------------------------------------------------------------- PnL etkisi

def test_worse_execution_cannot_improve_pnl():
    trades = _mixed()
    nets, exps, dds = [], [], []
    for n in SCENARIO_ORDER:
        r = apply_scenario(trades, SCENARIOS[n])
        nets.append(r["net_pnl_usdt"])
        exps.append(r["expectancy_r"])
        dds.append(r["max_drawdown_r"])
    assert nets[0] > nets[1] > nets[2]                     # daha kötü execution → daha düşük net
    assert exps[0] > exps[1] > exps[2]
    assert dds[0] >= dds[1] >= dds[2]                      # drawdown daha kötüye gider (daha negatif)


def test_gap_costs_more_in_adverse_and_stress():
    normal = apply_scenario([_trade(0)], ADVERSE)["net_pnl_usdt"]
    gapped = apply_scenario([_trade(0, gap=True)], ADVERSE)["net_pnl_usdt"]
    assert gapped < normal
    assert apply_scenario([_trade(0, gap=True)], BASE)["net_pnl_usdt"] == \
        apply_scenario([_trade(0)], BASE)["net_pnl_usdt"]   # base'te gap ek maliyeti 0


def test_funding_multiplier_scales_with_severity():
    f = [apply_scenario(_mixed(), SCENARIOS[n])["total_funding_usdt"] for n in SCENARIO_ORDER]
    assert f[0] > f[1] > f[2]                              # funding negatif → daha da negatif


def test_unusable_trades_skipped_not_guessed():
    bad = [{"gross_pnl": float("nan"), "notional": 100.0},
           {"gross_pnl": 10.0, "notional": 0.0},
           {"gross_pnl": 10.0}]
    r = apply_scenario(bad, BASE)
    assert r["n"] == 0 and r["n_skipped"] == 3
    assert r["expectancy_r"] is None and r["net_pnl_usdt"] is None


# --------------------------------------------------------------- karşılaştırma raporu

def test_compare_scenarios_robust_case():
    rep = compare_scenarios(_mixed(gross_win=300.0, gross_loss=-5.0))
    assert rep["robust_across_scenarios"] is True
    assert rep["advantage_lost_in"] == []
    assert rep["cost_sensitivity_base_to_stress"] is not None
    assert "pozitif expectancy" in rep["verdict"]


def test_compare_scenarios_advantage_lost_under_stress():
    # base'te ince kâr; stress maliyetleri avantajı silmeli
    rep = compare_scenarios([_trade(i, gross=3.0, notional=20_000.0, risk=20.0, qv=50_000.0)
                             for i in range(15)])
    assert rep["robust_across_scenarios"] is False
    assert "stress" in rep["advantage_lost_in"]
    assert rep["expectancy_r_by_scenario"]["base"] > rep["expectancy_r_by_scenario"]["stress"]
    assert "kayboluyor" in rep["verdict"]


def test_provenance_is_honest_about_missing_market_microstructure():
    rep = compare_scenarios(_mixed())
    prov = rep["results"]["base"]["provenance"]
    assert prov["historical_bid_ask"] == UNAVAILABLE
    assert prov["order_book"] == UNAVAILABLE
    assert prov["fee"] == "OBSERVED" and prov["funding"] == "OBSERVED"
    assert prov["latency"] == "FALLBACK"
    assert "milisaniye" in rep["latency_model"] or "milisaniyelik" in rep["latency_model"]
    assert "TEST DATA" in rep["label"]


def test_compare_scenarios_deterministic():
    trades = _mixed()
    assert compare_scenarios(trades) == compare_scenarios(trades)


def test_empty_input_is_unknown_not_neutral():
    rep = compare_scenarios([])
    assert rep["robust_across_scenarios"] is None
    assert rep["advantage_lost_in"] is None
    assert "hesaplanamadı" in rep["verdict"]
