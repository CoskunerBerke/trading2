"""Ağ gerektirmeyen regresyon testleri — sentetik OHLCV ile."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot import indicators as ind
from tradingbot.analyzer import analyze_symbol, classify_regime
from tradingbot.backtest import run_backtest
from tradingbot.config import BotConfig
from tradingbot.data import drop_unclosed_last_bar, prepare
from tradingbot.decision import decide
from tradingbot.obsidian import ObsidianWriter
from tradingbot.portfolio import Portfolio
from tradingbot.signals import apply_paper
from tradingbot.strategies import StrategySpec, generate_signals, iter_specs


def make_df(n: int = 1200, seed: int = 7, drift: float = 0.0004, vol: float = 0.02) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = close * (1 - np.abs(rng.normal(0, vol / 2, n)))
    open_ = np.roll(close, 1); open_[0] = close[0]
    ts = 1_700_000_000_000 + np.arange(n) * 14_400_000
    df = pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": 1000.0})
    return prepare(df)


# ---------------------------------------------------------------- göstergeler
def test_rsi_bounds_and_ema_shape():
    df = make_df(400)
    r = ind.rsi(df["close"], 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    e = ind.ema(df["close"], 20)
    assert e.isna().sum() == 19 and len(e) == 400


def test_donchian_has_no_lookahead():
    df = make_df(200)
    lower, upper = ind.donchian(df["high"], df["low"], 20)
    # bar i'nin kanalı, bar i'nin kendi high'ını içermemeli
    i = 150
    assert upper.iloc[i] == df["high"].iloc[i - 20:i].max()


# ---------------------------------------------------------------- backtest
def test_backtest_executes_next_bar_open_and_applies_stop():
    df = make_df(300, seed=1)
    sig = pd.DataFrame({"entries": False, "exits": False}, index=df.index)
    sig.iloc[50, 0] = True     # bar 50 kapanışında giriş sinyali
    sig.iloc[80, 1] = True     # bar 80'de çıkış
    res = run_backtest(df, sig, bars_per_year=2190, fee_pct=0.1, slippage_pct=0.05, atr_stop_mult=50)  # stop devre dışı
    assert res.metrics.trades == 1
    t = res.trades[0]
    assert t.entry_price == pytest.approx(float(df["open"].iloc[51]))   # bir sonraki barın açılışı
    assert t.exit_price == pytest.approx(float(df["open"].iloc[81]))
    assert t.reason == "signal"


def test_backtest_stop_triggers_on_crash():
    df = make_df(300, seed=3)
    # giriş bar 101 açılışında; bar 103'te sert çöküş → stop zararla tetiklenmeli
    df.iloc[103:, df.columns.get_loc("low")] *= 0.5
    df.iloc[103:, df.columns.get_loc("close")] *= 0.6
    df.iloc[103:, df.columns.get_loc("open")] *= 0.6
    df.iloc[103:, df.columns.get_loc("high")] *= 0.6
    sig = pd.DataFrame({"entries": False, "exits": False}, index=df.index)
    sig.iloc[100, 0] = True
    res = run_backtest(df, sig, bars_per_year=2190, atr_stop_mult=2.0)
    assert res.metrics.trades == 1 and res.trades[0].reason == "stop"
    assert res.trades[0].pnl < 0
    assert res.trades[0].exit_time == str(df.index[103])
    assert res.metrics.max_drawdown_pct < 0


def test_backtest_flat_signals_zero_trades_and_equity_constant():
    df = make_df(100)
    sig = pd.DataFrame({"entries": False, "exits": False}, index=df.index)
    res = run_backtest(df, sig, bars_per_year=2190, starting_equity=5000)
    assert res.metrics.trades == 0
    assert res.equity.iloc[-1] == pytest.approx(5000)


# ---------------------------------------------------------------- stratejiler
def test_all_specs_generate_bool_signals():
    df = make_df(600)
    specs = list(iter_specs())
    assert len(specs) > 20
    for spec in specs:
        s = generate_signals(df, spec)
        assert s["entries"].dtype == bool and s["exits"].dtype == bool
        assert len(s) == len(df)


# ---------------------------------------------------------------- analiz + karar
def test_regime_classification():
    assert classify_regime(110, 105, 100, 90, 30)[0] == "YÜKSELİŞ"
    assert classify_regime(80, 85, 90, 100, 30)[0] == "DÜŞÜŞ"
    assert classify_regime(100, 101, 100, 99, 10)[0] == "YATAY"


def test_analyze_and_decide_pipeline(tmp_path: Path):
    cfg = BotConfig()
    cfg.coins = ["BTC/USDT", "ETH/USDT"]
    cfg.project_root = tmp_path
    now_ms = 1_700_000_000_000 + 1199 * 14_400_000 + 5 * 60_000  # son bar (idx 1199) henüz açık → düşürülmeli
    a1 = analyze_symbol("BTC/USDT", make_df(1200, seed=11, drift=0.0006), cfg, now_ms=now_ms, families=["rsi_mr", "ema_trend"])
    a2 = analyze_symbol("ETH/USDT", make_df(1200, seed=12, drift=-0.0002), cfg, now_ms=now_ms, families=["rsi_mr", "ema_trend"])
    assert a1.bars == 1199 and not a1.error
    assert 0 <= a1.score <= 100 and a1.signal in {"BUY", "SELL", "HOLD", "WATCH"}
    assert a1.best_strategy and a1.top_configs

    pf = Portfolio.load(tmp_path / "pf.json", 10_000)
    decisions, summary = decide([a1, a2], pf, cfg.risk)
    assert len(decisions) == 2 and summary.equity == pytest.approx(10_000)
    for d in decisions:
        assert d.action in {"BUY", "SELL", "HOLD", "WATCH"} and d.reasons
        if d.action == "BUY":
            assert d.units > 0 and d.notional_usdt <= 10_000 * cfg.risk.max_position_pct / 100 + 1e-6 and d.stop < d.price


def test_decide_sells_when_stop_hit_and_holds_otherwise():
    from tradingbot.analyzer import CoinAnalysis
    cfg = BotConfig()
    a = CoinAnalysis(symbol="SOL/USDT", price=90.0, atr14=3.0, has_edge=True, strategy_position="LONG",
                     signal="HOLD", score=70, best_strategy="x", regime="YÜKSELİŞ")
    pf = Portfolio(cash=5000, starting_equity=10000)
    pf.paper_buy("SOL/USDT", units=10, price=100.0, stop=95.0, strategy="x", fee_pct=0.1, when="t0")
    decisions, _ = decide([a], pf, cfg.risk)
    assert decisions[0].action == "SELL" and "Stop" in decisions[0].reasons[0]

    a.price = 120.0
    decisions, _ = decide([a], pf, cfg.risk)
    assert decisions[0].action == "HOLD"
    assert pf.positions["SOL/USDT"].stop > 95.0  # iz süren stop yukarı çekildi


def test_apply_paper_and_persistence(tmp_path: Path):
    from tradingbot.decision import Decision
    pf = Portfolio(cash=10_000, starting_equity=10_000)
    buy = Decision(symbol="BTC/USDT", action="BUY", price=100.0, units=5, notional_usdt=500, stop=90, strategy="s")
    ex = apply_paper([buy], pf, 0.1, "t1")
    assert ex and pf.positions["BTC/USDT"].units == 5 and pf.cash < 10_000
    sell = Decision(symbol="BTC/USDT", action="SELL", price=110.0, reasons=["test"])
    ex = apply_paper([sell], pf, 0.1, "t2")
    assert ex[0]["pnl"] > 0 and not pf.positions and pf.history
    p = tmp_path / "pf.json"
    pf.save(p)
    again = Portfolio.load(p, 10_000)
    assert again.cash == pytest.approx(pf.cash) and len(again.history) == 1


# ---------------------------------------------------------------- Obsidian
def test_obsidian_writer_produces_valid_canvas(tmp_path: Path):
    from tradingbot.analyzer import CoinAnalysis
    from tradingbot.decision import Decision, DecisionSummary
    cfg = BotConfig()
    analyses = [CoinAnalysis(symbol=f"{b}/USDT", price=10.0 * (i + 1), atr14=0.5, score=60 + i, has_edge=(i % 2 == 0),
                             best_strategy="rsi_mr(length=14)", best_family_title="RSI Ortalamaya Dönüş", regime="YATAY",
                             test_metrics={"sharpe": 1.1, "profit_factor": 1.5}, train_metrics={"sharpe": 1.4})
                for i, b in enumerate(["BTC", "ETH", "SOL"])]
    decisions = [Decision(symbol="BTC/USDT", action="BUY", confidence=70, price=10.0, units=3, notional_usdt=30, stop=9.0, reasons=["r"]),
                 Decision(symbol="ETH/USDT", action="WATCH", confidence=40, price=20.0, reasons=["r"]),
                 Decision(symbol="SOL/USDT", action="HOLD", confidence=60, price=30.0, units=1, notional_usdt=30, stop=27, reasons=["r"], holding=True)]
    summary = DecisionSummary(equity=10_000, cash=9_000, open_positions=1, market_regime="YATAY", btc_filter_active=False,
                              n_buy=1, n_sell=0, n_hold=1, n_watch=1)
    portfolio = {"cash": 9000, "equity": 10000, "starting_equity": 10000, "positions": {"SOL/USDT": {"units": 1, "entry_price": 28, "stop": 27, "strategy": "s", "entry_time": "t"}},
                 "open_pnl": {"SOL/USDT": 2.0}, "closed_trades": 0, "realized_pnl": 0.0, "history": []}
    w = ObsidianWriter(tmp_path / "vault", cfg.obsidian.canvas_name)
    paths = w.write_all(analyses, decisions, summary, portfolio, [], exchange="binance", timeframe="4h", run_time="2026-01-01T00:00:00+00:00")
    canvas = json.loads(Path(paths["canvas"]).read_text(encoding="utf-8"))
    ids = {n["id"] for n in canvas["nodes"]}
    assert {"coin_BTC", "coin_ETH", "coin_SOL", "decision", "signal", "portfolio", "regime", "backtests"} <= ids
    for e in canvas["edges"]:
        assert e["fromNode"] in ids and e["toNode"] in ids
    btc_node = next(n for n in canvas["nodes"] if n["id"] == "coin_BTC")
    assert btc_node["color"] == "4"  # BUY = yeşil
    assert (tmp_path / "vault" / "Coins" / "BTC.md").exists()
    assert (tmp_path / "vault" / "Backtests" / "Sweep.md").exists()
    dash = (tmp_path / "vault" / "Dashboard.md").read_text(encoding="utf-8")
    assert "```mermaid" in dash and 'BTC(("BTC' in dash


def test_drop_unclosed_last_bar():
    df = make_df(10)
    last_open = int(df["timestamp"].iloc[-1])
    assert len(drop_unclosed_last_bar(df, "4h", now_ms=last_open + 1000)) == 9
    assert len(drop_unclosed_last_bar(df, "4h", now_ms=last_open + 14_400_000 + 1)) == 10


# ---------------------------------------------------------------- TradingView / borsa kuralları / WFO
def test_tradingview_frame_parsing_and_symbol():
    from tradingbot.tradingview import _frame, _split_frames, tv_symbol
    f = _frame("set_auth_token", ["unauthorized_user_token"])
    assert f.startswith("~m~") and '"m":"set_auth_token"' in f
    raw = f + "~m~4~m~~h~1"
    parts = _split_frames(raw)
    assert len(parts) == 2 and parts[1] == "~h~1"
    assert tv_symbol("binance", "BTC/USDT") == "BINANCE:BTCUSDT"


def test_exchange_rules_rounding_and_validation():
    from tradingbot.exchange_rules import SymbolRule
    r = SymbolRule("DOGE/USDT", min_cost=1.0, amount_step=1.0, price_tick=1e-5, taker_fee_pct=0.1, min_amount=1.0)
    assert r.round_amount(71.9) == 71.0
    assert r.validate(71.0, 0.07)[0] is True
    assert r.validate(5.0, 0.07)[0] is False  # 0.35 USDT < min 1
    b = SymbolRule("BTC/USDT", min_cost=5.0, amount_step=1e-5, price_tick=0.01, taker_fee_pct=0.1, min_amount=1e-5)
    assert b.round_amount(0.000123456) == pytest.approx(0.00012)


def test_backtest_respects_min_notional_for_small_account():
    df = make_df(300, seed=5)
    sig = pd.DataFrame({"entries": False, "exits": False}, index=df.index)
    sig.iloc[50, 0] = True
    sig.iloc[80, 1] = True
    # 50 USDT, %2 risk → risk boyutu ~1 USDT; min 5 USDT'ye çekilmeli (cap %30 = 15 USDT izin veriyor)
    res = run_backtest(df, sig, bars_per_year=2190, starting_equity=50, risk_per_trade_pct=2.0,
                       max_position_pct=30, atr_stop_mult=50, min_notional=5.0, amount_step=1e-5)
    assert res.metrics.trades == 1
    t = res.trades[0]
    assert t.units * t.entry_price >= 5.0 * 0.999
    # cap 3 USDT (%6) → minimum sağlanamaz → işlem yok
    res2 = run_backtest(df, sig, bars_per_year=2190, starting_equity=50, risk_per_trade_pct=2.0,
                        max_position_pct=6, atr_stop_mult=50, min_notional=5.0)
    assert res2.metrics.trades == 0


def test_walk_forward_sweep_produces_folds():
    from tradingbot.sweep import sweep_symbol
    cfg = BotConfig()
    df = make_df(1500, seed=21, drift=0.0005)
    res = sweep_symbol("X/USDT", df, bars_per_year=2190, bt_cfg=cfg.backtest, risk_cfg=cfg.risk,
                       families=["ema_trend", "donchian"], min_notional=5.0, amount_step=1e-5)
    assert len(res.folds) == cfg.backtest.wfo_folds
    assert res.wfo is not None and res.best is not None
    for f in res.folds:
        assert f.spec and f.train_end < f.test_end
    assert res.edge_note.startswith(("✅", "❌"))


# ---------------------------------------------------------------- Çoklu ajan katmanı (ağsız)
def _frames(seed=3, drift=0.0005):
    from tradingbot import indicators as ind
    return {"1d": ind.add_snapshot_indicators(make_df(400, seed=seed, drift=drift * 6, vol=0.04)),
            "4h": ind.add_snapshot_indicators(make_df(1200, seed=seed + 1, drift=drift, vol=0.02)),
            "1h": ind.add_snapshot_indicators(make_df(600, seed=seed + 2, drift=drift / 4, vol=0.01))}


def test_agents_run_and_manager_produces_brief():
    from tradingbot.agents.base import CoinContext
    from tradingbot.agents.technical import TECHNICAL_AGENTS
    from tradingbot.agents.market import MarketDataAgent
    from tradingbot.agents.manager import ChiefAgent, CoinManagerAgent
    live = {"ticker": {"last": 100.0, "high": 104.0, "low": 96.0, "quoteVolume": 5e8, "percentage": 1.2},
            "orderbook": {"bid_usdt": 300000, "ask_usdt": 100000, "imbalance": 0.75, "spread_pct": 0.01},
            "funding": {"rate": 0.0001}, "open_interest": {"amount": 1000.0}, "long_short": {"ratio": 1.5, "long_pct": 60}}
    ctx = CoinContext(symbol="ETH/USDT", frames=_frames(), live=live, equity_usdt=50, risk_pct=2.0, atr_stop_mult=2.5)
    reports = [a.run(ctx) for a in TECHNICAL_AGENTS + [MarketDataAgent()]]
    assert len(reports) == 8
    for r in reports:
        assert -1.0 <= r.bias <= 1.0 and 0 <= r.confidence <= 100
        if r.agent != "edge":
            assert not r.error, f"{r.agent}: {r.error}"
    brief = CoinManagerAgent().decide(ctx, reports)
    assert brief.verdict in {"LONG", "SHORT", "BEKLE"} and 0 <= brief.conviction <= 100
    assert brief.do_list and brief.headline
    p = brief.plan
    if p.direction != "BEKLE":
        assert p.stop and p.entry and p.target1
        assert p.max_leverage >= 1 and p.suggested_leverage <= p.max_leverage
        if p.valid:
            assert p.rr >= 1.5 and p.notional_usdt >= 5
    chief = ChiefAgent(3).decide([brief])
    assert chief.risk_mode in {"RISK-ON", "RISK-OFF", "NÖTR"} and chief.ranking


def test_agent_error_is_contained():
    from tradingbot.agents.base import CoinContext
    from tradingbot.agents.technical import VolatilityAgent
    ctx = CoinContext(symbol="X/USDT", frames={})
    r = VolatilityAgent().run(ctx)
    assert r.error and r.bias == 0.0 and r.confidence == 0


def test_obsidian_agent_writer(tmp_path: Path):
    from tradingbot.agents.base import CoinContext
    from tradingbot.agents.technical import TECHNICAL_AGENTS
    from tradingbot.agents.manager import ChiefAgent, CoinManagerAgent
    from tradingbot.obsidian_agents import ObsidianAgentWriter
    ctx = CoinContext(symbol="SOL/USDT", frames=_frames(seed=9))
    reports = [a.run(ctx) for a in TECHNICAL_AGENTS]
    b = CoinManagerAgent().decide(ctx, reports)
    chief = ChiefAgent().decide([b])
    out = ObsidianAgentWriter(tmp_path).write_all([b], chief, ["SOL/USDT: BEKLE → LONG"])
    canvas = json.loads((tmp_path / "Agents" / "SOL.canvas").read_text(encoding="utf-8"))
    ids = {n["id"] for n in canvas["nodes"]}
    assert {"manager", "plan", "dont", "ifthen", "levels"} <= ids and any(i.startswith("ag_") for i in ids)
    for e in canvas["edges"]:
        assert e["fromNode"] in ids and e["toNode"] in ids
    assert (tmp_path / "Agents" / "SOL.md").exists() and (tmp_path / "Agents" / "Baş Yönetici.md").exists()
    assert "SOL/USDT: BEKLE → LONG" in (tmp_path / "Agents" / "Alarmlar.md").read_text(encoding="utf-8")
