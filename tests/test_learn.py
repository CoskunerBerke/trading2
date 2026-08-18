"""Phase 6 — öğrenme v2 (ağsız)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot.learn import (Calibrator, HierarchicalRate, LearnConfig, LearnerV2, LogisticModel, ModelRegistry, ShadowBook, StandardScaler,
                              TradeMemory, brier, build_features, ece, feature_names, isotonic_fit, label_outcome, log_loss, promotion_gate,
                              retrieve_similar, structured_postmortem, drift_check)
from tradingbot.learn.shadow import label_with_candles
from tradingbot.learning import Learner

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def test_features_deterministic_backward_compatible():
    legacy = {"bias_trend": 0.6, "conf_trend": 0.7, "conviction": 0.5, "rr": 2.0, "leverage": 3, "n_warnings": 4, "setup_type": "kırılım", "hour_sin": 0.3}
    f1, f2 = build_features(legacy), build_features(legacy)
    assert f1 == f2 and f1["is_breakout"] == 1.0 and "hour_sin" not in f1 and set(feature_names()) == set(f1)
    f3 = build_features({**legacy, "regime": "TREND_UP", "market_type": "futures", "funding_z": 9.0}, include_time_features=True, as_of_utc="2026-08-18T06:00:00+00:00")
    assert f3["regime_TREND_UP"] == 1.0 and f3["is_futures"] == 1.0 and f3["funding_z"] == 5.0 and "hour_sin" in f3


def test_scaler_train_only_and_logistic_recovers_relation():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, 3)) * np.array([1, 10, 100])
    y = (X[:, 0] + 0.1 * X[:, 1] > 0).astype(float)
    sc = StandardScaler().fit(X[:300])
    assert np.allclose(sc.transform(X[:300]).mean(axis=0), 0, atol=1e-9)
    m = LogisticModel(feature_names=["a", "b", "c"], l2=0.01).fit(X[:300], y[:300])
    p = m.predict_proba(X[300:])
    acc = float(np.mean((p > 0.5) == (y[300:] > 0.5)))
    assert acc > 0.9 and 0 < p.min() < p.max() < 1
    m2 = LogisticModel.from_dict(m.to_dict())
    assert np.allclose(m2.predict_proba(X[300:]), p)


def test_calibration_metrics_and_pava_monotone():
    y = np.array([0, 0, 1, 1, 1, 0, 1, 1, 0, 1], float)
    perfect = y.copy()
    assert brier(perfect, y) == 0 and log_loss(perfect, y) < 1e-5 and ece(perfect, y) == 0
    rnd = np.full(10, 0.5)
    assert brier(rnd, y) == pytest.approx(0.25)
    xs, ys = isotonic_fit([0.1, 0.4, 0.35, 0.8, 0.9, 0.2], [0, 1, 0, 1, 1, 0])
    assert all(ys[i] <= ys[i + 1] for i in range(len(ys) - 1))
    c = Calibrator("platt").fit(np.linspace(0.05, 0.95, 50), (np.linspace(0.05, 0.95, 50) > 0.5).astype(float))
    out = c.apply([0.2, 0.8])
    assert out[0] < out[1]


def test_hierarchical_shrinkage_and_blacklist_evidence():
    h = HierarchicalRate(alpha=10, prior_mean=0.5)
    for _ in range(100):
        h.add(1.0, regime="TREND_UP", leaf="BTC")
    for _ in range(3):
        h.add(0.0, regime="TREND_UP", leaf="XYZ")
    m_btc, _ = h.estimate(regime="TREND_UP", leaf="BTC")
    m_xyz, _ = h.estimate(regime="TREND_UP", leaf="XYZ")
    assert m_btc > 0.9 and 0.5 < m_xyz < 0.97          # az verili XYZ ebeveyne (yüksek) çekilir
    e = HierarchicalRate(alpha=10, prior_mean=0.0)
    for _ in range(3):
        e.add(-1.0, leaf="kırılım|LONG")
    assert not e.is_negative_with_evidence(leaf="kırılım|LONG")   # n<5 → kanıt yetersiz
    for _ in range(20):
        e.add(-0.8, leaf="kırılım|LONG")
    assert e.is_negative_with_evidence(leaf="kırılım|LONG")
    e2 = HierarchicalRate.from_dict(e.to_dict())
    assert e2.estimate(leaf="kırılım|LONG") == e.estimate(leaf="kırılım|LONG")


def test_labels_and_postmortem_codes():
    assert label_outcome({"r_multiple": 0.1, "pnl": 0.05, "exit_reason": "stop"})["outcome_class"] == "SCRATCH"
    lab = label_outcome({"r_multiple": -1.0, "pnl": -1.0, "exit_reason": "likidasyon", "fees": 0.3, "mae_pct": -3, "mfe_pct": 0.1})
    assert lab["outcome_class"] == "LOSS" and lab["exit_quality"] == "LIQUIDATION" and lab["fee_drag_r"] == pytest.approx(0.3)
    pm = structured_postmortem({"id": "F1", "symbol": "ETH/USDT", "side": "LONG", "exit_reason": "likidasyon", "pnl": -1.0, "r_multiple": -1.0,
                                "leverage": 5, "features": {"bias_trend": 0.6, "bias_volume": -0.5, "n_warnings": 6, "btc_align": -1, "rr": 1.2, "p_win": 0.6}})
    assert "LIQUIDATION_LEVERAGE" in pm.lesson_codes and "TOO_MANY_WARNINGS" in pm.lesson_codes and "AGAINST_BTC_MODE" in pm.lesson_codes
    assert pm.should_not_have_opened and "volume" in pm.agents_right and "trend" in pm.agents_wrong and pm.lesson_text_tr
    pm2 = structured_postmortem({"id": "F2", "symbol": "ETH/USDT", "side": "LONG", "exit_reason": "stop", "pnl": -1.0, "r_multiple": -1.0, "bars_held": 1,
                                 "mfe_pct": 2.5, "features": {}})
    assert "STOP_TOO_FAST" in pm2.lesson_codes or "PROFIT_NOT_TAKEN" in pm2.lesson_codes


def _candles(start: datetime, n: int, closes: list[float], hi_add=1.0, lo_sub=1.0):
    ts = [int((start + timedelta(hours=4 * i)).timestamp() * 1000) for i in range(n)]
    c = closes[:n] + [closes[-1]] * (n - len(closes))
    return pd.DataFrame({"timestamp": ts, "open": c, "high": [x + hi_add for x in c], "low": [x - lo_sub for x in c], "close": c, "volume": 1.0})


def test_shadow_conservative_and_no_lookahead(tmp_path: Path):
    book = ShadowBook(tmp_path / "shadow.json")
    plan = {"symbol": "ETH/USDT", "market_type": "futures", "direction": "LONG", "entry": 100.0, "stop": 95.0, "targets": [110.0, 120.0], "horizon_bars": 6}
    (sh,) = book.add(plan, ["FUNDING_EXTREME"], now=NOW)
    assert sh.is_counterfactual and book.pending(NOW) == [] and book.pending(NOW + timedelta(hours=25))
    # aynı barda hem stop hem hedef değiyor → stop (muhafazakâr)
    df = _candles(NOW + timedelta(hours=4), 8, [100, 100, 100, 100, 100, 100, 100, 100], hi_add=15, lo_sub=6)
    out = label_with_candles(sh, df)
    assert out["exit_reason"] == "stop" and out["r_multiple"] == pytest.approx(-1.0) and out["veto_was_right"]
    # label_ts sonrası mumlar okunmaz: hedef label_ts'ten SONRA vurulsa bile sayılmaz
    df2 = _candles(NOW + timedelta(hours=4), 12, [100] * 6 + [130] * 6, hi_add=0.5, lo_sub=0.5)
    out2 = label_with_candles(sh, df2)
    assert out2["exit_reason"] == "horizon" and out2["bars"] == 6
    book.label(sh, df2)
    assert ShadowBook(tmp_path / "shadow.json").trades[0].outcome is not None and book.stats()["labeled"] == 1


def test_registry_promotion_rules_and_drift(tmp_path: Path):
    reg = ModelRegistry(tmp_path / "models.json")
    weak = reg.register("p_win_lr", {"n_train": 10}, {"n_holdout": 5, "ece": 0.3, "log_loss": 0.7, "brier": 0.26, "expectancy_r": -0.1})
    ok, reasons = reg.promote(weak, operator="auto", mode="PAPER")
    assert not ok and any("holdout" in r for r in reasons)
    strong = reg.register("p_win_lr", {"n_train": 100}, {"n_holdout": 40, "ece": 0.05, "log_loss": 0.55, "brier": 0.2, "expectancy_r": 0.3})
    assert reg.promote(strong, operator="auto", mode="PAPER")[0] and reg.champion("p_win_lr")["id"] == strong
    better = reg.register("p_win_lr", {"n_train": 200}, {"n_holdout": 60, "ece": 0.04, "log_loss": 0.50, "brier": 0.18, "expectancy_r": 0.35})
    ok, reasons = reg.promote(better, operator="auto", mode="TESTNET")
    assert not ok and "manuel" in reasons[0]
    assert reg.promote(better, operator="berke", mode="TESTNET", manual=True)[0]
    assert ModelRegistry(tmp_path / "models.json").champion("p_win_lr")["id"] == better and reg.get(strong)["status"] == "RETIRED"
    d = drift_check({"log_loss": 0.75, "brier": 0.3, "hit_rate": 0.4}, {"log_loss": 0.5, "brier": 0.2, "hit_rate": 0.6})
    assert d.drifted and len(d.signals) == 3
    assert not drift_check({"log_loss": 0.51}, {"log_loss": 0.5}).drifted
    ok, _ = promotion_gate(None, {"n_holdout": 40, "ece": 0.1})
    assert ok


def test_memory_append_only_and_retrieval(tmp_path: Path):
    mem = TradeMemory(tmp_path / "mem.jsonl")
    for i in range(30):
        tid = mem.record_entry({"trade_id": f"T{i}", "symbol": "ETH/USDT" if i % 2 else "SOL/USDT", "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP",
                                "features": {"bias_trend": 0.5 if i % 2 else -0.5, "conviction": 0.6, "atr_pct": 0.3}, "specialist_reports": [{"agent": "trend"}]})
        mem.record_exit(tid, {"symbol": "ETH/USDT", "r_multiple": 1.0 if i % 2 else -1.0, "exit_reason": "hedef2"}, [], {"lesson_codes": ["X"], "lesson_text_tr": ["ders"]})
    assert mem.count() == 30 and len(mem.trades(closed_only=True)) == 30 and mem.get("T3")["outcome"]["r_multiple"] == 1.0
    size1 = (tmp_path / "mem.jsonl").stat().st_size
    mem.record_entry({"trade_id": "T99", "symbol": "X/USDT"})
    assert (tmp_path / "mem.jsonl").stat().st_size > size1          # asla kesilmez
    sim = retrieve_similar(mem, {"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP", "bias_trend": 0.5, "conviction": 0.6, "atr_pct": 0.3}, k=5)
    assert len(sim) == 5 and all(s["symbol"] == "ETH/USDT" for s in sim) and sim[0]["r_multiple"] == 1.0 and sim[0]["lesson"] == "ders"


def _rec(i, won, symbol="ETH/USDT"):
    return {"id": f"F{i}", "symbol": symbol, "side": "LONG", "entry": 100, "exit_reason": "hedef2" if won else "stop", "closed_at": (NOW - timedelta(days=60 - i)).isoformat(),
            "pnl": 2.0 if won else -1.0, "r_multiple": 2.0 if won else -1.0, "mae_pct": -0.5, "mfe_pct": 3.0 if won else 0.2, "bars_held": 4, "leverage": 2,
            "setup_type": "kırılım", "features": {"bias_trend": 0.6 if won else -0.6, "conf_trend": 0.7, "bias_momentum": 0.4 if won else -0.3, "conviction": 0.6 if won else 0.3,
                                                   "rr": 2.5, "atr_pct": 0.3, "n_warnings": 1 if won else 5, "leverage": 2}}


def test_learner_v2_end_to_end_prior_to_model_and_legacy_bridge(tmp_path: Path):
    mem = TradeMemory(tmp_path / "mem.jsonl")
    from tradingbot.learn import PromotionThresholds
    reg = ModelRegistry(tmp_path / "models.json", thresholds=PromotionThresholds(min_holdout=10))
    lr = LearnerV2(mem, reg, LearnConfig(min_samples_train=40, holdout_frac=0.25), tmp_path / "learn_v2.json")
    p0 = lr.predict({"bias_trend": 0.6}, regime="TREND_UP", symbol="ETH/USDT", setup="kırılım")
    assert not p0.ready and p0.p_win_calibrated == pytest.approx(0.5)
    for i in range(60):
        won = (i % 3 != 0)
        rec = _rec(i, won)
        mem.record_entry({"trade_id": rec["id"], "symbol": rec["symbol"], "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP", "features": rec["features"],
                          "recorded_at": (NOW - timedelta(days=60 - i)).isoformat()})
        lesson = lr.on_trade_closed(rec, {"regime": "TREND_UP", "consensus_score": 0.4, "dissent": []})
        assert lesson["why"]
    assert lr.n_closed == 60 and lr.snapshot()["agents"]["trend"]["hit_rate_shrunk"] > 0.7
    p1 = lr.predict({"bias_trend": 0.6, "conviction": 0.6, "n_warnings": 1}, regime="TREND_UP", symbol="ETH/USDT", setup="kırılım")
    assert not p1.ready and p1.prior_used > 0.55                    # hiyerarşik önsel öğrenmiş, model henüz yok
    out = lr.train_challenger(now=NOW)
    assert out and out["metrics"]["n_holdout"] >= 10 and reg.challenger("p_win_lr")
    ok, reasons = lr.maybe_promote("PAPER")
    assert ok, reasons
    p2 = lr.predict({"bias_trend": 0.6, "conviction": 0.6, "n_warnings": 1}, regime="TREND_UP", symbol="ETH/USDT", setup="kırılım")
    p3 = lr.predict({"bias_trend": -0.6, "conviction": 0.3, "n_warnings": 5}, regime="TREND_UP", symbol="ETH/USDT", setup="kırılım")
    assert p2.ready and p2.model_id and p2.p_win_calibrated > p3.p_win_calibrated
    assert 0.5 * (p2.prior_used) < p2.p_win_calibrated            # harman: model + önsel
    again = LearnerV2(mem, ModelRegistry(tmp_path / "models.json"), LearnConfig(), tmp_path / "learn_v2.json")
    assert again.n_closed == 60 and again.predict({"bias_trend": 0.6}, regime="TREND_UP").ready
    # legacy köprü
    v1 = Learner(tmp_path / "learn_v1.json", min_trades=5)
    for i in range(6):
        v1.learn(_rec(100 + i, i % 2 == 0))
    imported = again.legacy_bridge(v1)
    assert imported["agents"] >= 1 and imported["setups"] >= 1 and imported["lessons"] == 6
    assert again.snapshot()["setups"].get("kırılım|LONG") is not None
