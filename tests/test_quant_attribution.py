"""Quant Evaluation V1 — attribution testleri.

Kapsam: net/gross PnL ve maliyet toplamları, expectancy, R, profit factor sıfır-kayıp durumu,
drawdown, MAE/MFE, gruplama, minimum örnek, deterministik bootstrap, kalibrasyon (Brier) ve
RFC-safe çıktı. Sentetik sabit fixture; rastgelelik yalnız sabit seed'li bootstrap'ta.
"""
from __future__ import annotations

import json

import pytest

from tradingbot.quant.attribution import (DIMENSIONS, attribution_v1, group_metrics, render_text)


def _row(i: int, *, symbol="ETH/USDT", direction="LONG", r=1.0, net=30.0, gross=None,
         fees=1.5, funding=0.5, p_win=None, regime="trend_up", lev=3, cf=False, exit_reason="hedef2"):
    return {"outcome_labeled": True, "is_counterfactual": cf, "symbol": symbol,
            "market_type": "futures", "direction": direction, "regime": regime,
            "timeframe": "4h", "setup_id": "core", "policy_id": "champion",
            "planned_leverage": lev, "exit_reason": exit_reason,
            "event_ts_utc": f"2026-01-{(i % 27) + 1:02d}T{(i * 5) % 24:02d}:00:00+00:00",
            "quality_flags": [], "specialist_scores": {"trend": 0.8, "meanrev": -0.1},
            "r_multiple": r, "net_pnl": net, "gross_pnl": gross if gross is not None else net + fees + funding,
            "fees": fees, "funding": funding, "mae_pct": -1.0, "mfe_pct": 4.0, "bars_held": 6,
            "p_win": p_win, "outcome_class": "WIN" if r > 0.25 else ("LOSS" if r < -0.25 else "SCRATCH"),
            "cost_estimate": {"slippage_usdt": 0.3}}


def _mixed_rows(n=40):
    rows = []
    for i in range(n):
        win = i % 3 != 0                       # 2/3 kazanan, deterministik
        rows.append(_row(i, symbol="ETH/USDT" if i % 2 else "SOL/USDT",
                         direction="LONG" if i % 4 else "SHORT",
                         r=1.2 if win else -1.0, net=36.0 if win else -30.0,
                         p_win=0.62 if win else 0.45))
    return rows


def test_pnl_cost_expectancy_and_r():
    m = group_metrics(_mixed_rows(), min_sample=10, seed=7)
    assert m["insufficient_sample"] is False
    assert m["n"] == 40 and m["wins"] + m["losses"] + m["breakeven"] == 40
    assert m["fees_usdt"] == pytest.approx(1.5 * 40)
    assert m["funding_usdt"] == pytest.approx(0.5 * 40)
    assert m["slippage_usdt"] == pytest.approx(0.3 * 40)
    assert m["net_pnl_usdt"] == pytest.approx(sum(36.0 if i % 3 else -30.0 for i in range(40)))
    assert m["gross_pnl_usdt"] == pytest.approx(m["net_pnl_usdt"] + 40 * 2.0)
    assert m["expectancy_usdt"] == pytest.approx(m["net_pnl_usdt"] / 40)
    assert m["expectancy_r"] == pytest.approx(m["mean_r"])
    assert m["basis"] == "trade" and "annual" not in json.dumps(m)


def test_profit_factor_zero_loss_is_null_not_infinity():
    rows = [_row(i, r=1.0, net=30.0) for i in range(12)]
    m = group_metrics(rows)
    assert m["profit_factor"] is None and m["profit_factor_state"] == "no_losses"
    dumped = json.dumps(m, allow_nan=False)
    assert "Infinity" not in dumped


def test_drawdown_and_mae_mfe():
    rows = [_row(i, r=r, net=r * 30) for i, r in enumerate([1.0, -1.0, -1.0, 2.0, -0.5] * 4)]
    m = group_metrics(rows)
    assert m["max_drawdown_r"] == pytest.approx(-2.0)      # 1 → -1 (iki ardışık -1)
    assert m["mae_pct_mean"] == pytest.approx(-1.0)
    assert m["mfe_pct_mean"] == pytest.approx(4.0)
    assert m["tail_loss_r_cvar5"] is not None and m["tail_loss_r_cvar5"] <= -1.0


def test_minimum_sample_gate():
    m = group_metrics([_row(i) for i in range(5)], min_sample=10)
    assert m["insufficient_sample"] is True
    assert "expectancy_r" not in m                          # kesin sonuç ÜRETİLMEDİ
    ci = group_metrics([_row(i) for i in range(12)], min_sample=10)["bootstrap_ci_mean_r"]
    assert ci["state"] == "insufficient_sample" and ci["low"] is None   # bootstrap ayrı eşik (20)


def test_deterministic_bootstrap_and_report():
    rows = _mixed_rows()
    a = attribution_v1(rows, seed=7)
    b = attribution_v1(rows, seed=7)
    assert a == b                                           # tam determinizm
    c = attribution_v1(rows, seed=8)
    assert c["overall_real"]["bootstrap_ci_mean_r"] != a["overall_real"]["bootstrap_ci_mean_r"]
    ci = a["overall_real"]["bootstrap_ci_mean_r"]
    assert ci["state"] == "ok" and ci["low"] < ci["high"]


def test_grouping_dimensions_and_unknown_dim():
    rows = _mixed_rows()
    rep = attribution_v1(rows, dims=["symbol", "direction", "leverage", "hour_bucket"], min_sample=5)
    by_sym = rep["by_dimension_real"]["symbol"]
    assert set(by_sym) == {"ETH/USDT", "SOL/USDT"}
    assert sum(g["n"] for g in by_sym.values()) == 40
    assert set(rep["by_dimension_real"]["leverage"]) == {"3x"}
    with pytest.raises(ValueError):
        attribution_v1(rows, dims=["olmayan_boyut"])
    assert set(DIMENSIONS) >= {"symbol", "regime", "exit_reason", "policy", "data_quality"}


def test_calibration_brier():
    rows = _mixed_rows()
    cal = attribution_v1(rows)["overall_real"]["calibration"]
    assert cal["state"] == "ok" and 0.0 <= cal["brier"] <= 1.0
    no_p = [_row(i, p_win=None) for i in range(15)]
    assert group_metrics(no_p)["calibration"]["state"] == "insufficient_sample"


def test_counterfactual_isolated_from_real():
    rows = _mixed_rows() + [_row(100 + i, cf=True, r=5.0, net=150.0) for i in range(15)]
    rep = attribution_v1(rows)
    assert rep["n_real"] == 40 and rep["n_counterfactual"] == 15
    assert rep["overall_real"]["n"] == 40                  # shadow kârı gerçek havuza sızmadı
    assert rep["overall_counterfactual"]["n"] == 15
    txt = render_text(rep)
    assert "counterfactual" in txt and "TEST" not in txt.upper().replace("TEST DATA", "")


def test_concentration_flags_single_symbol_profit():
    rows = ([_row(i, symbol="AAA/USDT", r=2.0, net=200.0) for i in range(6)] +
            [_row(20 + i, symbol="BBB/USDT", r=0.3, net=2.0) for i in range(6)])
    m = group_metrics(rows, min_sample=5)
    assert m["concentration"]["top_symbol_share"] == pytest.approx(1200.0 / 1212.0, rel=1e-3)
    assert m["concentration"]["top_trade_share"] == pytest.approx(200.0 / 1212.0, rel=1e-3)
