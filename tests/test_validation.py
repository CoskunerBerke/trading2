"""Doğrulama araçları + vadeli backtester (ağsız)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.futures_backtest import run_futures_backtest
from tradingbot.validation import (ValidationReport, anchored_wfo, block_bootstrap_ci, champion_challenger_gate,
                                   deflated_sharpe, metrics_extended, monte_carlo_drawdown, neighbourhood_stability,
                                   probabilistic_sharpe, purged_kfold, rolling_wfo)


# ------------------------------------------------------------------ statistics
def test_bootstrap_ci_contains_mean():
    rng = np.random.default_rng(1)
    x = rng.normal(0.3, 1.0, size=200)
    ci = block_bootstrap_ci(x, n=500, block=5, seed=0)
    assert ci["lo"] <= ci["stat"] <= ci["hi"] and abs(ci["stat"] - x.mean()) < 1e-12
    assert 0 <= ci["p_le_zero"] <= 1


def test_monte_carlo_dd_percentiles_ordered():
    rng = np.random.default_rng(2)
    pnls = rng.normal(0.2, 1.0, size=100)
    mc = monte_carlo_drawdown(pnls, n=300, seed=0, starting_equity=100.0)
    assert mc["p99"] >= mc["p95"] >= mc["p50"] >= 0
    assert 0 <= mc["p_dd_gt"](mc["p50"]) <= 1 and mc["p_dd_gt"](0) == 1.0 and 25 in mc["p_dd_gt_table"]


def test_dsr_decreases_with_trials_and_psr_monotone():
    sr = 0.15   # dönem başına
    d1, d10, d100 = (deflated_sharpe(sr, k, T=250) for k in (1, 10, 100))
    assert d1 > d10 > d100
    assert probabilistic_sharpe(0.2, 0.0, 250) > probabilistic_sharpe(0.1, 0.0, 250)
    assert probabilistic_sharpe(0.2, 0.0, 500) > probabilistic_sharpe(0.2, 0.0, 50)


def test_purged_kfold_no_overlap_and_embargo():
    folds = purged_kfold(100, 5, embargo=3)
    assert len(folds) == 5
    for tr, te in folds:
        assert len(np.intersect1d(tr, te)) == 0
        assert te.min() - 3 not in tr and te.max() + 3 not in tr    # ambargo bölgesi eğitimde yok
        assert (te.min() - 4 in tr) or te.min() == 0
        assert (te.max() + 4 in tr) or te.max() >= 96
    all_test = np.concatenate([te for _, te in folds])
    assert sorted(all_test.tolist()) == list(range(100))


def test_neighbourhood_stability():
    grid = {(a, b): 1.0 for a in (1, 2, 3) for b in (10, 20, 30)}
    grid[(2, 20)] = 2.0
    ns = neighbourhood_stability(grid, (2, 20), radius=1)
    assert ns["n_neighbours"] == 8 and abs(ns["ratio"] - 0.5) < 1e-12
    grid[(1, 10)] = 3.0
    assert neighbourhood_stability(grid, (1, 10))["n_neighbours"] == 3


# ------------------------------------------------------------------ walk-forward
def _df(n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 0.5, size=n))
    o = np.roll(close, 1); o[0] = close[0]
    h = np.maximum(o, close) + 0.3
    lo = np.minimum(o, close) - 0.3
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": close, "volume": 1.0}, index=idx)


def test_anchored_and_rolling_wfo_windows():
    df = _df(400)
    seen = []

    def run_fn(tr, te):
        seen.append((len(tr), len(te), tr.index[-1], te.index[0]))
        return {"sharpe": 0.5, "trades": 10, "return_pct": 1.0}

    res = anchored_wfo(df, run_fn, folds=4, initial_train_ratio=0.5, purge=2, embargo=1)
    assert res["n_folds"] == 4 and res["trades"] == 40 and res["pct_positive"] == 1.0
    lens = [s[0] for s in seen]
    assert lens == sorted(lens) and lens[0] < lens[-1]              # büyüyen eğitim
    for tr_len, te_len, tr_last, te_first in seen:
        assert te_first > tr_last                                    # purge boşluğu var
    seen.clear()
    res2 = rolling_wfo(df, run_fn, train_bars=100, test_bars=50, purge=1)
    assert res2["n_folds"] > 0 and all(s[0] == 100 and s[1] == 50 for s in seen)   # sabit pencere


def test_metrics_extended_basic():
    eq = [100, 101, 99, 103, 104]
    m = metrics_extended(eq, [1.0, -1.0, 2.0, -0.5], bars_per_year=8760)
    assert m["trades"] == 4 and abs(m["win_rate"] - 0.5) < 1e-12 and m["max_consec_losses"] == 1
    assert m["profit_factor"] == 2.0 and m["expectancy_r"] == 0.375 and m["max_dd_pct"] > 0


# ------------------------------------------------------------------ gate
def test_gate_rejects_weak_accepts_strong():
    weak = ValidationReport(label="w", dsr=0.6, oos_sharpe=0.4, oos_trades=12, oos_profit_factor=1.1, is_max_dd_pct=10,
                            mc_dd_p95=30, bootstrap_expectancy_lo=-0.1, neighbourhood_ratio=0.3, folds_pct_positive=0.4)
    ok, reasons = champion_challenger_gate(None, weak)
    assert not ok and len(reasons) >= 6
    strong = ValidationReport(label="s", dsr=0.97, oos_sharpe=1.4, oos_trades=60, oos_profit_factor=1.6, is_max_dd_pct=10,
                              mc_dd_p95=12, bootstrap_expectancy_lo=0.05, neighbourhood_ratio=0.8, folds_pct_positive=0.75)
    assert champion_challenger_gate(None, strong) == (True, [])
    ok2, r2 = champion_challenger_gate({"oos_sharpe": 1.3}, strong)
    assert not ok2 and any("champion" in r for r in r2)
    assert champion_challenger_gate({"oos_sharpe": 1.0}, strong)[0]


# ------------------------------------------------------------------ futures backtest
def _flat_df(n=60, px=100.0, start="2026-01-01 00:00", freq="1h"):
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": px, "high": px + 1.0, "low": px - 1.0, "close": px, "volume": 1.0}, index=idx, dtype=float)


def _sig(df, spec):
    """Hedef pozisyon serisi: {bar: side} → o bardan itibaren geçerli (ileri doldurma)."""
    s = pd.Series(np.nan, index=df.index)
    for i, v in spec.items():
        s.iloc[i] = v
    return s.ffill().fillna(0.0)


def test_futures_long_win_and_short_win():
    df = _flat_df()
    # ATR ~ 2 (range 2, close-close 0). Long at bar 21 open, ride to +10 by bar 30, exit signal at 30 → fill 31 open
    df.iloc[25:, df.columns.get_loc("open")] = 110.0
    df.iloc[25:, df.columns.get_loc("high")] = 111.0
    df.iloc[25:, df.columns.get_loc("low")] = 109.0
    df.iloc[25:, df.columns.get_loc("close")] = 110.0
    sig = _sig(df, {20: 1, 30: 0})
    res = run_futures_backtest(df, sig, bars_per_year=8760, leverage=2, atr_stop_mult=2.5, fee_taker_pct=0.05,
                               slippage_pct=0.0, starting_equity=1000.0, min_notional=0.0)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.side == 1 and t.pnl > 0 and t.reason == "signal" and t.entry == 100.0 and t.exit == 110.0
    assert t.r > 0 and t.mfe_pct > 0 and t.bars_held == 10 and t.leverage == 2
    assert res.metrics["long_trades"] == 1 and res.metrics["final_equity"] > 1000.0
    # short: price falls
    df2 = _flat_df()
    for col, v in (("open", 90.0), ("high", 91.0), ("low", 89.0), ("close", 90.0)):
        df2.iloc[25:, df2.columns.get_loc(col)] = v
    res2 = run_futures_backtest(df2, _sig(df2, {20: -1, 30: 0}), bars_per_year=8760, leverage=1, slippage_pct=0.0,
                                starting_equity=1000.0, min_notional=0.0)
    t2 = res2.trades[0]
    assert t2.side == -1 and t2.pnl > 0 and t2.exit == 90.0 and res2.metrics["short_trades"] == 1


def test_futures_liquidation_clamps_loss():
    df = _flat_df()
    # 10x kaldıraç: liq ≈ entry*(1-(0.1-0.004)) = 90.4; bar 23 çöküş 80
    for col, v in (("open", 80.0), ("high", 81.0), ("low", 79.0), ("close", 80.0)):
        df.iloc[23:, df.columns.get_loc(col)] = v
    res = run_futures_backtest(df, _sig(df, {20: 1}), bars_per_year=8760, leverage=10, atr_stop_mult=100.0,
                               slippage_pct=0.0, fee_taker_pct=0.05, liq_fee_pct=0.5, starting_equity=1000.0,
                               min_notional=0.0, risk_per_trade_pct=5.0)
    t = res.trades[0]
    assert t.reason == "liq" and res.metrics["liq_count"] == 1
    entry_fee = t.notional * 0.0005
    liq_fee = t.notional * 0.005
    assert abs(t.pnl - (-(t.margin + liq_fee + entry_fee))) < 1e-6
    assert t.pnl > -(t.qty * (100.0 - 80.0)) - entry_fee          # gerçek fiyat kaybından çok daha az


def test_futures_funding_sign():
    df = _flat_df(n=60, start="2026-01-01 00:00")
    fr = pd.Series(0.0, index=df.index)
    fr[:] = 0.001                                                    # her settlement'ta +0.1%
    common = dict(bars_per_year=8760, leverage=1, slippage_pct=0.0, fee_taker_pct=0.0, atr_stop_mult=100.0,
                  starting_equity=1000.0, min_notional=0.0, funding_rates=fr)
    long_res = run_futures_backtest(df, _sig(df, {20: 1, 50: 0}), **common)
    short_res = run_futures_backtest(df, _sig(df, {20: -1, 50: 0}), **common)
    tl, ts = long_res.trades[0], short_res.trades[0]
    # bar 21..50 açık: settlement saatleri 0/8/16 → 24, 32, 40, 48 → 4 kez
    assert tl.funding > 0 and ts.funding < 0 and abs(tl.funding + ts.funding) < 1e-9
    assert abs(tl.funding - 4 * tl.qty * 100.0 * 0.001) < 1e-9
    assert tl.pnl < 0 < ts.pnl and long_res.metrics["funding_paid"] > 0


def test_futures_same_bar_stop_and_tp_is_stop():
    df = _flat_df()
    # ATR≈2, stop mult 1 → stop=98, tp1_r=1 → tp=102. Bar 22: low 97, high 103 → stop kazanır
    df.iloc[22, df.columns.get_loc("low")] = 97.0
    df.iloc[22, df.columns.get_loc("high")] = 103.0
    res = run_futures_backtest(df, _sig(df, {20: 1}), bars_per_year=8760, leverage=1, atr_stop_mult=1.0, tp1_r=1.0,
                               slippage_pct=0.0, starting_equity=1000.0, min_notional=0.0)
    t = res.trades[0]
    assert t.reason == "stop" and t.pnl < 0 and t.bars_held == 1
    # yalnız tp: bar 22 high 103 → tp1
    df2 = _flat_df()
    df2.iloc[22, df2.columns.get_loc("high")] = 103.0
    res2 = run_futures_backtest(df2, _sig(df2, {20: 1}), bars_per_year=8760, leverage=1, atr_stop_mult=1.0, tp1_r=1.0,
                                slippage_pct=0.0, starting_equity=1000.0, min_notional=0.0)
    assert res2.trades[0].reason == "tp1" and res2.trades[0].pnl > 0


def test_futures_reverse_and_min_notional_skip():
    df = _flat_df()
    res = run_futures_backtest(df, _sig(df, {20: 1, 30: -1, 40: 0}), bars_per_year=8760, leverage=1, slippage_pct=0.0,
                               atr_stop_mult=100.0, starting_equity=1000.0, min_notional=0.0)
    assert [t.reason for t in res.trades] == ["reverse", "signal"] and [t.side for t in res.trades] == [1, -1]
    # 50 USDT hesap, 1% risk, ATR 2*2.5=5 → qty 0.1 → notional 10 ≥ min 5 → açılır; min_notional 100 → açılmaz
    small = run_futures_backtest(df, _sig(df, {20: 1, 30: 0}), bars_per_year=8760, leverage=1, starting_equity=50.0,
                                 min_notional=100.0)
    assert small.trades == [] and small.metrics["trades"] == 0
    ok = run_futures_backtest(df, _sig(df, {20: 1, 30: 0}), bars_per_year=8760, leverage=1, starting_equity=50.0,
                              min_notional=5.0, qty_step=0.001)
    assert len(ok.trades) == 1 and ok.trades[0].notional >= 5.0 and abs(ok.trades[0].qty * 1000 - round(ok.trades[0].qty * 1000)) < 1e-6
