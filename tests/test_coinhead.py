"""Phase 4 — Coin Head katmanı (uzman şeması, faktör grupları, red team, konsensüs, registry, chief) — ağsız."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from tradingbot import indicators as ind
from tradingbot.agents.base import CoinContext
from tradingbot.agents.manager import CoinManagerAgent
from tradingbot.agents.market import MarketDataAgent
from tradingbot.agents.technical import TECHNICAL_AGENTS
from tradingbot.coinhead import (ChiefPortfolioManager, CoinHead, CoinHeadConfig, CoinHeadInputs, CoinHeadRegistry, RedTeamContext,
                                 SpecialistContext, Verdict, adapt_legacy_reports, aggregate, consensus, review)
from tradingbot.coinhead.specialists import DataIntegrityAgent, MarketRegimeAgent, RiskSizingAgent
from tradingbot.data import prepare


def make_df(n=1200, seed=7, drift=0.0004, vol=0.02):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    high = close * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = close * (1 - np.abs(rng.normal(0, vol / 2, n)))
    open_ = np.roll(close, 1); open_[0] = close[0]
    ts = 1_700_000_000_000 + np.arange(n) * 14_400_000
    return prepare(pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}))


def frames(seed=3, drift=0.0008):
    return {"1d": ind.add_snapshot_indicators(make_df(400, seed, drift * 6, 0.04)),
            "4h": ind.add_snapshot_indicators(make_df(1200, seed + 1, drift, 0.02)),
            "1h": ind.add_snapshot_indicators(make_df(600, seed + 2, drift / 4, 0.01))}


LIVE = {"ticker": {"last": 100.0, "high": 104.0, "low": 96.0, "quoteVolume": 5e8, "percentage": 1.2, "bid": 99.99, "ask": 100.01},
        "orderbook": {"bid_usdt": 300000, "ask_usdt": 100000, "imbalance": 0.75, "spread_pct": 0.01},
        "funding": {"rate": 0.0001, "mark": 100.02}, "open_interest": {"amount": 1000.0}, "long_short": {"ratio": 1.5, "long_pct": 60}, "ts": 1.0}


def live_for(fr, **over):
    """Canlı ticker'ı 4h kapanışıyla tutarlı fiyata çeker (gerçekte de öyledir)."""
    lv = {k: (dict(v) if isinstance(v, dict) else v) for k, v in LIVE.items()}
    px = float(fr["4h"]["close"].iloc[-1]) if "4h" in fr else 100.0
    lv["ticker"].update({"last": px, "high": px * 1.04, "low": px * 0.96, "bid": px * 0.9999, "ask": px * 1.0001})
    lv["funding"]["mark"] = px * 1.0002
    lv.update(over)
    return lv


def legacy(fr, live=None):
    live = live or live_for(fr)
    ctx = CoinContext(symbol="ETH/USDT", frames=fr, live=live, equity_usdt=50, risk_pct=2.0, atr_stop_mult=2.5)
    reports = [a.run(ctx) for a in TECHNICAL_AGENTS + [MarketDataAgent()]]
    brief = CoinManagerAgent().decide(ctx, reports)
    return reports, brief


def test_specialist_schema_and_factor_dedup():
    fr = frames()
    reports, _ = legacy(fr)
    ctx = SpecialistContext(symbol="ETH/USDT", run_id="r1", snapshot_id="s1", frames=fr, live=LIVE)
    specs = adapt_legacy_reports(reports, ctx)
    names = {s.agent_name for s in specs}
    assert {"trend", "momentum", "market:liquidity", "market:derivatives"} <= names       # market ajanı bölündü
    for s in specs:
        d = s.to_dict()
        for k in ("analysis_id", "run_id", "snapshot_id", "symbol", "market_type", "agent_name", "agent_version", "as_of_utc", "stance",
                  "bias", "confidence_raw", "confidence_calibrated", "evidence_for", "evidence_against", "metrics", "levels", "warnings",
                  "veto", "veto_reason", "error", "latency_ms", "factor_group"):
            assert k in d
    groups = aggregate(specs)
    trend = next(g for g in groups if g.group == "trend")
    assert trend.n_independent == 1                       # trend grubunda tek bağımsız kaynak (EMA'lar tek oy)
    sl = next(g for g in groups if g.group == "structure_levels")
    assert sl.n_independent == 2                          # levels + candles
    score, conf, dissent = consensus(groups, "TREND_UP")
    assert -1 <= score <= 1 and 0 <= conf <= 1 and isinstance(dissent, list)


def test_data_integrity_veto_and_regime_and_sizing():
    fr = frames()
    ctx = SpecialistContext(symbol="ETH/USDT", run_id="r", snapshot_id="s", frames={"1d": fr["1d"]}, live=LIVE)
    r = DataIntegrityAgent(ctx)
    assert r.veto and "MISSING_4H_FRAME" in r.veto_reason
    ctx2 = SpecialistContext(symbol="ETH/USDT", run_id="r", snapshot_id="s", frames=fr, live=LIVE, quality={"verdict": "DATA_INVALID", "issues": ["STALE_CANDLE"]})
    assert DataIntegrityAgent(ctx2).veto
    ctx3 = SpecialistContext(symbol="ETH/USDT", run_id="r", snapshot_id="s", frames=fr, live=LIVE)
    assert not DataIntegrityAgent(ctx3).veto
    reg = MarketRegimeAgent(ctx3)
    assert reg.metrics["regime"] in ("TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "LOW_VOL", "SQUEEZE", "BREAKOUT", "PANIC", "EUPHORIC")
    rs = RiskSizingAgent(SpecialistContext(symbol="X/USDT", run_id="r", snapshot_id="s", frames=fr, live=LIVE, equity_usdt=50, risk_pct=0.25, stop_pct=10.0))
    assert rs.veto and rs.veto_reason == "NO_TRADE_MIN_ORDER_CONFLICT"     # 0.125 USDT risk / %10 stop = 1.25 notional < 5


def test_red_team_codes():
    v, w = review(RedTeamContext(direction="LONG", data_stale=True, spread_pct=0.5, depth_usdt=1000, expected_cost_pct=1.0, expected_return_gross_pct=0.5,
                                 has_edge=False, oos_trades=5, corr_btc=0.9, same_direction_open=3, btc_regime="TREND_DOWN", stop_pct=10, atr_pct=1,
                                 liq_distance_pct=5, funding_pct=0.08, kill_switch_active=True, listing_age_days=10, min_order_conflict=True))
    for code in ("STALE_DATA", "WIDE_SPREAD", "LOW_LIQUIDITY", "COSTS_EXCEED_EDGE", "WEAK_OOS_EDGE", "HIGH_CORRELATION_EXPOSURE",
                 "CROWDED_SAME_DIRECTION", "STOP_TOO_FAR", "LIQ_BEFORE_STOP", "FUNDING_EXTREME", "KILL_SWITCH_ACTIVE", "NEW_LISTING", "MIN_ORDER_CONFLICT"):
        assert code in v, code
    assert "AGAINST_BTC_REGIME" in w and "LOW_TRADE_COUNT" in w
    v2, _ = review(RedTeamContext(direction="SHORT", funding_pct=0.08, stop_pct=0.5, atr_pct=1.0))
    assert "FUNDING_EXTREME" not in v2 and "STOP_TOO_CLOSE" in v2
    assert review(RedTeamContext())[0] == []


def _inputs(fr, reports, brief, **kw):
    now_ms = int(next(iter(fr.values()))["timestamp"].iloc[-1]) + 14_400_000 + 1000
    live = dict(kw.pop("live", None) or live_for(fr), ts=now_ms / 1000 - 5)      # taze ticker
    base = dict(frames=fr, live=live, legacy_reports=reports, legacy_brief=brief, availability={"spot": True, "futures": True},
                btc_frames=frames(seed=11), eth_frames=frames(seed=12), run_id="run1", snapshot_id="snap0001", now_ms=now_ms,
                edge={"has_edge": True, "oos_sharpe": 0.9, "oos_trades": 40})
    base.update(kw)
    return CoinHeadInputs(**base)


def test_coin_head_decision_paths():
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)   # sentetik veride yön çıkması için düşük eşik
    fr = frames(seed=3, drift=0.0015)
    reports, brief = legacy(fr)
    head = CoinHead("ETH/USDT", cfg)
    d = head.decide(_inputs(fr, reports, brief))
    dd = d.to_dict()
    for k in ("symbol", "market_type", "regime", "verdict", "no_trade_reason", "direction", "confidence_raw", "confidence_calibrated", "p_win",
              "expected_return_gross", "expected_cost", "expected_return_net", "expected_r", "expected_shortfall", "entry_trigger", "entry_zone",
              "invalidation", "stop", "targets", "time_horizon", "position_size", "margin", "notional", "leverage", "consensus", "dissent", "vetoes",
              "evidence", "model_versions", "data_freshness", "expires_at", "spot_plan", "futures_plan", "factor_scores", "specialist_reports"):
        assert k in dd, k
    assert d.expires_at and d.verdict in Verdict and d.regime != "UNKNOWN"
    if d.is_actionable:
        assert d.stop and d.targets and d.notional > 0 and d.expected_r >= cfg.min_expected_r and not d.vetoes
        assert d.net_exposure_after["after"] != 0
    # 4h çerçeve yoksa DATA_INVALID
    d2 = head.decide(_inputs({"1d": fr["1d"], "1h": fr["1h"]}, reports, brief))
    assert d2.verdict == Verdict.DATA_INVALID and d2.no_trade_reason == "NO_TRADE_DATA_INVALID"
    # spot yok + long → futures ya da NO_TRADE; futures yok → SPOT_LONG ya da NO_TRADE (asla FUTURES_*)
    d3 = head.decide(_inputs(fr, reports, brief, availability={"spot": True, "futures": False}))
    assert d3.verdict in (Verdict.SPOT_LONG, Verdict.NO_TRADE)
    assert d3.futures_plan is None
    # kill switch aktifken red team veto → NO_TRADE
    d4 = head.decide(_inputs(fr, reports, brief, portfolio={"kill_switch_active": True}))
    assert d4.verdict == Verdict.NO_TRADE and (d4.no_trade_reason in ("NO_TRADE_RED_TEAM_VETO", "NO_TRADE_LOW_CONSENSUS", "NO_TRADE_NO_VALID_PLAN"))
    if d.is_actionable:
        assert d4.no_trade_reason == "NO_TRADE_RED_TEAM_VETO" and any("KILL_SWITCH_ACTIVE" in v for v in d4.vetoes)
    # açık pozisyon → HOLD/EXIT/REDUCE
    d5 = head.decide(_inputs(fr, reports, brief, portfolio={"open_position": {"side": "LONG"}}))
    assert d5.verdict in (Verdict.HOLD, Verdict.EXIT, Verdict.REDUCE)
    # LLM veto yalnızca veto edebilir, işlem açtıramaz
    d6 = head.decide(_inputs(fr, reports, brief, llm_advice={"veto": True, "veto_reasons": ["contradiction"]}))
    assert not d6.is_actionable


def test_futures_rejected_spot_allowed_when_funding_extreme():
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    fr = frames(seed=5, drift=0.0015)
    live = live_for(fr, funding={"rate": 0.0009, "mark": float(fr["4h"]["close"].iloc[-1])})   # %0.09 / 8s → long için aşırı
    reports, brief = legacy(fr, live)
    d = CoinHead("ETH/USDT", cfg).decide(_inputs(fr, reports, brief, live=live))
    assert d.direction == "LONG" and d.verdict == Verdict.SPOT_LONG and d.market_type == "spot"
    assert d.futures_plan is not None and not d.futures_plan.valid and "FUNDING_EXTREME" in d.futures_plan.invalid_reason
    assert d.spot_plan is not None and d.spot_plan.valid and d.leverage == 1 and d.notional > 0
    # aynı veri, normal funding → futures da geçerli; maliyet sonrası R'ye göre seçim yapılır
    d2 = CoinHead("ETH/USDT", cfg).decide(_inputs(fr, reports, brief))
    assert d2.is_actionable and d2.futures_plan is not None and d2.futures_plan.valid


def test_registry_lock_stale_and_save(tmp_path: Path):
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    reg = CoinHeadRegistry(cfg, max_workers=2)
    fr = frames()
    reports, brief = legacy(fr)
    d1 = reg.run("ETH/USDT", _inputs(fr, reports, brief, snapshot_id="snap0002"))
    assert d1 is not None
    stale = reg.run("ETH/USDT", _inputs(fr, reports, brief, snapshot_id="snap0001"))
    assert stale is None and reg.last_decisions["ETH/USDT"].snapshot_id == "snap0002"
    out = reg.run_many({"ETH/USDT": _inputs(fr, reports, brief, snapshot_id="snap0003"), "SOL/USDT": _inputs(fr, reports, brief, snapshot_id="snap0003")})
    assert set(out) == {"ETH/USDT", "SOL/USDT"}
    p = reg.save(tmp_path, run_id="run1")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data["heads"]) == 2 and data["heads"][0]["specialist_reports"]
    assert reg.get_or_create("ETH/USDT").coin_head_id == reg.get_or_create("ETH/USDT").coin_head_id


def test_chief_ranks_blocks_crowding_and_requires_three_flags():
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    fr = frames(seed=3, drift=0.0015)
    reports, brief = legacy(fr)
    decs = [CoinHead(s, cfg).decide(_inputs(fr, reports, brief)) for s in ("SOL/USDT", "AVAX/USDT", "ADA/USDT", "BTC/USDT")]
    ps = {"equity": 50, "open_positions": [{"symbol": "NEAR/USDT", "side": "LONG", "notional": 10, "market_type": "USDM_PERP"},
                                           {"symbol": "APT/USDT", "side": "LONG", "notional": 10, "market_type": "USDM_PERP"}]}
    ch = ChiefPortfolioManager().decide(decs, ps)
    d = ch.to_dict()
    assert d["market_risk_mode"] in ("RISK-ON", "NÖTR", "RISK-OFF") and len(d["ranking"]) == 4
    assert d["approval_flags_required"] == ["coin_head_valid", "no_red_team_veto", "risk_engine_allowed"]
    for sym, perm in d["permission"].items():
        if perm["allow"]:
            assert perm["requires"] == ["coin_head_valid", "no_red_team_veto", "risk_engine_allowed"]
    actionable = [x for x in decs if x.is_actionable and x.direction == "LONG" and x.symbol != "BTC/USDT"]
    if actionable:   # l1 kümesinde 2 long açıkken üçüncü l1 long'a izin yok
        assert any("küme kalabalık" in p["reason"] or "yığılma" in p["reason"] or "limiti" in p["reason"] for p in d["permission"].values() if not p["allow"])
    assert d["exposure"]["long_notional"] == 20 and d["allocation"]["futures_notional"] == 20
