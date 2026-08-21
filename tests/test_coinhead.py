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


def test_red_team_separates_hard_veto_from_soft_penalty():
    """`review()` → (hard_veto_codes, soft_penalty_codes).

    Ekonomik/istatistiksel zayıflıklar (zayıf OOS edge, korelasyon/yığılma, funding, yeni
    listelenme, rejim uyumsuzluğu, orta seviye spread/derinlik, tercih dışı fakat geçerli stop)
    TEK BAŞINA REDDETMEZ. Sert liste yalnız gerçek güvenlik/geçerlilik/ekonomi ihlalidir.
    """
    from tradingbot.coinhead.redteam import (HARD_VETO_CODES, SOFT_PENALTY_CODES,
                                             assert_classification_matches_registry)
    assert_classification_matches_registry()                       # decision_gates ile birebir
    assert not (set(HARD_VETO_CODES) & set(SOFT_PENALTY_CODES))
    hard, soft = review(RedTeamContext(direction="LONG", data_stale=True, spread_pct=0.5, depth_usdt=10_000,
                                       expected_cost_pct=1.0, expected_return_gross_pct=0.5,
                                       has_edge=False, oos_trades=5, corr_btc=0.9, same_direction_open=3,
                                       btc_regime="TREND_DOWN", stop_pct=10, atr_pct=1,
                                       liq_distance_pct=5, funding_pct=0.08, kill_switch_active=True,
                                       listing_age_days=10, min_order_conflict=True))
    for code in ("STALE_DATA", "COSTS_EXCEED_EDGE", "LIQ_BEFORE_STOP", "KILL_SWITCH_ACTIVE", "MIN_ORDER_CONFLICT"):
        assert code in hard, code                                  # gerçek güvenlik/ekonomi → SERT
    for code in ("WIDE_SPREAD", "LOW_LIQUIDITY", "WEAK_OOS_EDGE", "LOW_TRADE_COUNT", "AGAINST_BTC_REGIME",
                 "HIGH_CORRELATION_EXPOSURE", "CROWDED_SAME_DIRECTION", "STOP_TOO_FAR",
                 "FUNDING_EXTREME", "NEW_LISTING"):
        assert code in soft, code                                  # ekonomik zayıflık → YUMUŞAK
        assert code not in hard, f"{code} tek başına REDDETMEMELİ"
    # Yalnız ekonomik zayıflıklar varsa SERT VETO YOKTUR (eski "10 ayrı engel" davranışı kalktı).
    only_soft_hard, only_soft = review(RedTeamContext(direction="LONG", has_edge=False, oos_trades=5,
                                                      corr_btc=0.9, same_direction_open=3,
                                                      btc_regime="TREND_DOWN", stop_pct=10, atr_pct=1,
                                                      funding_pct=0.08, listing_age_days=10,
                                                      spread_pct=0.5, depth_usdt=10_000))
    assert only_soft_hard == [], only_soft_hard
    assert len(only_soft) >= 8
    # Gerçekten işlem yapılamayacak likidite/spread SERT kalır.
    h2, _ = review(RedTeamContext(direction="LONG", spread_pct=2.0))
    assert "LIQUIDITY_UNTRADEABLE" in h2
    h3, _ = review(RedTeamContext(direction="LONG", depth_usdt=100))
    assert "LIQUIDITY_UNTRADEABLE" in h3
    h4, s4 = review(RedTeamContext(direction="SHORT", funding_pct=0.08, stop_pct=0.5, atr_pct=1.0))
    assert "FUNDING_EXTREME" not in s4 and "STOP_TOO_CLOSE" in s4 and h4 == []
    assert review(RedTeamContext()) == ([], [])


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
    # LLM ADVISORY: ne işlem açtırabilir NE DE tek başına hard veto verebilir (registry'yi atlayamaz).
    d6 = head.decide(_inputs(fr, reports, brief, llm_advice={"veto": True, "veto_reasons": ["contradiction"]}))
    assert not d6.vetoes, "LLM sert veto üretemez"
    if d.is_actionable:
        assert d6.is_actionable, "LLM advisory planı geçersiz YAPAMAZ"
        assert "RED_TEAM_SOFT_PENALTY" in d6.active_plan.soft_flags   # yalnız yumuşak ceza
    llm_rep = next((r for r in d6.specialist_reports if r.agent_name.startswith("red_team_veto")), None)
    assert llm_rep is not None and not llm_rep.veto
    assert (llm_rep.metrics or {}).get("llm_advisory", {}).get("can_hard_veto") is False


def test_spot_preferred_over_futures_when_funding_extreme_without_hard_veto():
    """Aşırı funding artık VETO DEĞİL: futures planı GEÇERLİ kalır, yumuşak kanıt taşır ve
    maliyet sonrası R üzerinden spot tercih edilir. (Funding zaten `expected_cost_pct`e girer.)"""
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    fr = frames(seed=5, drift=0.0015)
    live = live_for(fr, funding={"rate": 0.0009, "mark": float(fr["4h"]["close"].iloc[-1])})   # %0.09 / 8s → long için aşırı
    reports, brief = legacy(fr, live)
    d = CoinHead("ETH/USDT", cfg).decide(_inputs(fr, reports, brief, live=live))
    assert d.direction == "LONG" and d.verdict == Verdict.SPOT_LONG and d.market_type == "spot"
    # SERT VETO YOK: plan geçerli, yalnız yumuşak kanıt (boyut küçültücü) taşıyor.
    assert d.futures_plan is not None and d.futures_plan.valid
    assert "FUNDING_EXTREME" in d.futures_plan.soft_flags
    assert not d.vetoes, d.vetoes
    assert d.spot_plan is not None and d.spot_plan.valid and d.leverage == 1 and d.notional > 0
    # Seçim ekonomiktir: funding maliyeti futures'ın maliyet sonrası R'sini düşürür.
    assert d.futures_plan.expected_r < d.spot_plan.expected_r or d.spot_plan.expected_cost_pct < d.futures_plan.expected_cost_pct
    # aynı veri, normal funding → futures da geçerli; maliyet sonrası R'ye göre seçim yapılır
    d2 = CoinHead("ETH/USDT", cfg).decide(_inputs(fr, reports, brief))
    assert d2.is_actionable and d2.futures_plan is not None and d2.futures_plan.valid
    assert "FUNDING_EXTREME" not in d2.futures_plan.soft_flags


def test_registry_lock_stale_and_save(tmp_path: Path):
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    reg = CoinHeadRegistry(cfg, max_workers=2)
    fr = frames()
    reports, brief = legacy(fr)
    t = _inputs(fr, reports, brief).now_ms
    d1 = reg.run("ETH/USDT", _inputs(fr, reports, brief, snapshot_id="snap0002", snapshot_at_ms=t))
    assert d1 is not None
    stale = reg.run("ETH/USDT", _inputs(fr, reports, brief, snapshot_id="snap0001", snapshot_at_ms=t - 1000))   # olay zamanı daha eski
    assert stale is None and reg.last_decisions["ETH/USDT"].snapshot_id == "snap0002"
    out = reg.run_many({"ETH/USDT": _inputs(fr, reports, brief, snapshot_id="snap0003", snapshot_at_ms=t + 1000),
                        "SOL/USDT": _inputs(fr, reports, brief, snapshot_id="snap0003", snapshot_at_ms=t + 1000)})
    assert set(out) == {"ETH/USDT", "SOL/USDT"}
    p = reg.save(tmp_path, run_id="run1")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data["heads"]) == 2 and data["heads"][0]["specialist_reports"]
    assert data["snapshot_order"]["ETH/USDT"] == {"at_ms": t + 1000, "seq": 0, "snapshot_id": "snap0003"}
    assert reg.get_or_create("ETH/USDT").coin_head_id == reg.get_or_create("ETH/USDT").coin_head_id


# ---------------------------------------------------------------- snapshot sıralaması: olay zamanı, id sözlük sırası DEĞİL
def _reg_and_inputs():
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    fr = frames()
    reports, brief = legacy(fr)
    t = _inputs(fr, reports, brief).now_ms
    def mk(snap, at, seq=0, **kw):
        return _inputs(fr, reports, brief, snapshot_id=snap, snapshot_at_ms=at, snapshot_seq=seq, **kw)
    return CoinHeadRegistry(cfg, max_workers=1), mk, t


def test_snapshot_newer_time_but_lexically_smaller_id_is_accepted():
    reg, mk, t = _reg_and_inputs()
    assert reg.run("ETH/USDT", mk("ffff-hash", t)) is not None
    d = reg.run("ETH/USDT", mk("0000-hash", t + 60_000))          # id sözlükte küçük, zaman daha yeni → kabul
    assert d is not None and reg.last_decisions["ETH/USDT"].snapshot_id == "0000-hash"
    assert reg.drops["stale_snapshot"] == 0


def test_two_consecutive_tours_both_reach_decision():
    reg, mk, t = _reg_and_inputs()
    ids = ["9a", "1b", "5c"]                                          # hash sırası rastgele; zaman monoton
    for i, s in enumerate(ids):
        out = reg.run_many({"ETH/USDT": mk(s, t + i * 1000, seq=i + 1), "SOL/USDT": mk(s, t + i * 1000, seq=i + 1)})
        assert set(out) == {"ETH/USDT", "SOL/USDT"}, (i, s)
    assert reg.drops == {"stale_snapshot": 0, "duplicate_snapshot": 0}


def test_same_snapshot_resent_is_rejected_idempotently():
    reg, mk, t = _reg_and_inputs()
    d1 = reg.run("ETH/USDT", mk("s1", t))
    assert d1 is not None
    assert reg.run("ETH/USDT", mk("s1", t)) is None
    assert reg.drops["duplicate_snapshot"] == 1 and reg.last_decisions["ETH/USDT"] is d1


def test_truly_older_snapshot_is_stale():
    reg, mk, t = _reg_and_inputs()
    assert reg.run("ETH/USDT", mk("new", t + 5000, seq=2)) is not None
    assert reg.run("ETH/USDT", mk("old", t, seq=1)) is None           # eski zaman
    assert reg.run("ETH/USDT", mk("tie", t + 5000, seq=1)) is None    # aynı zaman, küçük seq → belirsiz → fail-closed
    assert reg.drops["stale_snapshot"] == 2 and reg.last_decisions["ETH/USDT"].snapshot_id == "new"


def test_persisted_registry_keeps_time_order(tmp_path: Path):
    reg, mk, t = _reg_and_inputs()
    assert reg.run("ETH/USDT", mk("zz", t + 9000, seq=3)) is not None
    reg.save(tmp_path, run_id="r")
    reg2 = CoinHeadRegistry(reg.cfg, max_workers=1)
    assert reg2.load(tmp_path) == 1
    assert reg2.run("ETH/USDT", mk("aa", t + 1000, seq=1)) is None    # daha eski → STALE (yeniden açıldıktan sonra da)
    assert reg2.run("ETH/USDT", mk("zz", t + 9000, seq=3)) is None    # aynı → duplicate
    assert reg2.run("ETH/USDT", mk("bb", t + 10_000, seq=4)) is not None
    assert reg2.drops == {"stale_snapshot": 1, "duplicate_snapshot": 1}


def test_legacy_hash_only_state_migrates_safely(tmp_path: Path):
    reg, mk, t = _reg_and_inputs()
    legacy_state = {"generated_at": "x", "run_id": "r0", "chief": None,
                    "heads": [{"symbol": "ETH/USDT", "snapshot_id": "ffffffffffffffff", "verdict": "NO_TRADE"}]}   # snapshot_order yok
    (tmp_path / "coin_heads.json").write_text(json.dumps(legacy_state), encoding="utf-8")
    assert reg.load(tmp_path) == 1
    assert reg.run("ETH/USDT", mk("ffffffffffffffff", t)) is None                 # aynı opak id → duplicate
    assert reg.run("ETH/USDT", mk("0000000000000000", t)) is not None            # sözlükte küçük ama yeni format ilk geçerli → kabul
    assert reg.drops == {"stale_snapshot": 0, "duplicate_snapshot": 1}
    reg.save(tmp_path)
    assert json.loads((tmp_path / "coin_heads.json").read_text(encoding="utf-8"))["snapshot_order"]["ETH/USDT"]["at_ms"] == t
    # bozuk dosya → boş, ilk snapshot kabul
    (tmp_path / "coin_heads.json").write_text("{not json", encoding="utf-8")
    reg3 = CoinHeadRegistry(reg.cfg, max_workers=1)
    assert reg3.load(tmp_path) == 0 and reg3.run("ETH/USDT", mk("x", t)) is not None


def test_replay_determinism_unchanged_by_ordering():
    reg_a, mk, t = _reg_and_inputs()
    reg_b = CoinHeadRegistry(reg_a.cfg, max_workers=1)
    da = reg_a.run("ETH/USDT", mk("s", t, seq=1)).to_dict(include_reports=False)
    db = reg_b.run("ETH/USDT", mk("s", t, seq=1)).to_dict(include_reports=False)
    for k in ("expires_at", "generated_at", "latency_ms", "coin_head_id"):
        da.pop(k, None); db.pop(k, None)
    assert da == db


def test_spot_and_futures_paths_share_ordering_rule():
    reg, mk, t = _reg_and_inputs()
    for avail in ({"spot": True, "futures": False}, {"spot": False, "futures": True}):
        sym = "SPOT/USDT" if avail["spot"] else "FUT/USDT"
        assert reg.run(sym, mk("h9", t, availability=avail)) is not None
        assert reg.run(sym, mk("h1", t + 1000, availability=avail)) is not None      # yeni zaman, küçük id → kabul
        assert reg.run(sym, mk("h5", t - 1000, availability=avail)) is None          # eski → STALE
        assert reg.last_decisions[sym].snapshot_id == "h1"
    assert reg.drops["stale_snapshot"] == 2


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
    if actionable:
        # YIĞILMA ARTIK VETO DEĞİL: aynı yön / aynı küme kalabalıksa boyut KÜÇÜLTÜLÜR, işlem
        # reddedilmez. Sert engel yalnız gerçek güvenlik/kapasite kodlarından gelebilir.
        crowded = [p for p in d["permission"].values() if p.get("soft_codes")]
        assert crowded, "yığılma yumuşak kanıt olarak kaydedilmeli"
        assert all(p["size_penalty_r"] > 0 for p in crowded)
        for p in d["permission"].values():
            if not p["allow"]:
                # Chief RISK REZERVE ETMEZ: kapasite kodu artık burada üretilemez.
                assert p.get("block_code") in ("NOT_ACTIONABLE", "RED_TEAM_HARD_VETO"), p
    # SABİT İŞLEM SAYISI KOTASI YOK — raporlama sözleşmesi her zaman null
    assert d["exposure"]["daily_trade_cap"] is None and d["exposure"]["per_run_trade_cap"] is None
    assert "risk_budget_usdt" in d["exposure"] and "risk_capacity_left_usdt" in d["exposure"]
    assert d["exposure"]["long_notional"] == 20 and d["allocation"]["futures_notional"] == 20


def test_chief_has_no_fixed_trade_count_cap():
    """Tur başına / günlük sabit yeni pozisyon tavanı KALDIRILDI (gerçek sorunun kaynağıydı)."""
    from tradingbot.coinhead.chief import ChiefConfig
    c = ChiefConfig()
    assert c.max_new_positions_per_run is None and c.daily_trade_cap is None
    src = Path(__file__).resolve().parents[1] / "tradingbot" / "coinhead" / "chief.py"
    text = src.read_text(encoding="utf-8")
    assert "granted >= cfg.max_new_positions_per_run" not in text
    assert "tur başına yeni pozisyon limiti" not in text


def test_chief_grants_all_affordable_opportunities_in_one_run():
    """Aynı turda 3 güçlü ve farklı sembollü fırsat: risk bütçesi yetiyorsa ÜÇÜ DE izin almalı."""
    from tradingbot.coinhead.chief import ChiefConfig
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    fr = frames(seed=3, drift=0.0015)
    reports, brief = legacy(fr)
    syms = ("SOL/USDT", "AVAX/USDT", "ADA/USDT")
    decs = [CoinHead(s, cfg).decide(_inputs(fr, reports, brief)) for s in syms]
    for d in decs:                                   # her biri güçlü, ucuz ve maliyet sonrası pozitif
        d.opportunity = {"conservative_net_edge_r": 0.5, "opportunity_score": 1.0,
                         "risk_pct_requested": 1.0, "size_multiplier": 1.0}
    ch = ChiefPortfolioManager(ChiefConfig(max_total_open_risk_pct=6.0)).decide(
        decs, {"equity": 1000.0, "open_positions": [], "total_open_risk_usdt": 0.0})
    allowed = [s for s in syms if ch.permission.get(s, {}).get("allow")]
    actionable = [d.symbol for d in decs if d.is_actionable]
    assert set(allowed) == set(actionable), (allowed, actionable)
    assert len(allowed) >= 3 or len(actionable) < 3
    assert ch.exposure["ranked"] == len(allowed) == ch.exposure["granted_this_run"]


def test_chief_does_not_reserve_risk_and_reports_capacity_as_advisory_only():
    """Chief kapasite REZERVE ETMEZ: projeksiyon yalnız raporlamadır, hiçbir adayı engellemez.

    Yetkili kapasite kararı `RiskEngine.evaluate()` içinde, NİHAİ boyut üzerinden ve yalnız
    gerçekten açılmış pozisyonların riskine karşı verilir.
    """
    from tradingbot.coinhead.chief import ChiefConfig
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    fr = frames(seed=3, drift=0.0015)
    reports, brief = legacy(fr)
    decs = [CoinHead(s, cfg).decide(_inputs(fr, reports, brief)) for s in ("SOL/USDT", "AVAX/USDT", "ADA/USDT")]
    for d in decs:
        d.opportunity = {"conservative_net_edge_r": 0.5, "risk_pct_requested": 2.0, "size_multiplier": 1.0}
    ch = ChiefPortfolioManager(ChiefConfig(max_total_open_risk_pct=2.0)).decide(   # projeksiyon 1 işleme yeter
        decs, {"equity": 1000.0, "open_positions": [], "total_open_risk_usdt": 0.0})
    actionable = [d.symbol for d in decs if d.is_actionable]
    # Kapasite projeksiyonu dolsa bile HİÇBİR aday chief tarafından engellenmez.
    assert all(ch.permission[s]["allow"] for s in actionable), ch.permission
    assert not any(p.get("block_code") == "RISK_CAPACITY_BLOCKED" for p in ch.permission.values())
    for p in ch.permission.values():
        assert p.get("block_code") not in ("DAILY_LIMIT", "PER_RUN_LIMIT")
    assert ch.exposure["authoritative_risk_reservation"] is False
    # `risk_used_usdt` GERÇEK açık risktir (rezervasyon değil): açık pozisyon yok → 0.
    assert ch.exposure["risk_used_usdt"] == 0.0
    assert ch.exposure["risk_capacity_left_usdt"] == ch.exposure["risk_budget_usdt"]
    if len(actionable) > 1:                        # projeksiyon bunu "sığmaz" olarak RAPORLAR
        fits = [ch.permission[s]["capacity_projection"]["would_fit"] for s in actionable]
        assert fits.count(False) >= 1 and all(
            ch.permission[s]["capacity_projection"]["advisory"] for s in actionable)
