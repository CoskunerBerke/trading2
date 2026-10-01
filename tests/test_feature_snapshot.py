"""FeatureSnapshotV3 + coverage gate + bounded policy + baseline↔candidate walk-forward davranış testleri."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from conftest import BAR_MS, make_snapshot, sparse_features, synth_bars
from tradingbot.config import BotConfig
from tradingbot.config_v3 import load_v3
from tradingbot.learn import TradeMemory
from tradingbot.learn.attribution import attribution_report
from tradingbot.learn.coverage import COVERAGE_INVALID, coverage_report
from tradingbot.learn.policy import (CandidatePolicy, POLICY_BOUNDS, PolicyBoundsError, baseline_policy,
                                     generate_candidates, validate_policy)
from tradingbot.learn.snapshot import (FIELD_NAMES, LeakageError, MISS_PREFIX, REQUIRED_FIELDS,
                                       SNAPSHOT_VERSION, build_snapshot, snapshot_feature_names)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
BASE = NOW - timedelta(days=140)


def _cfg(tmp_path: Path):
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    cfg.v3 = load_v3({"learning_v3": {"min_samples_train": 20, "holdout_frac": 0.25}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    cfg.cache_path.mkdir(parents=True, exist_ok=True)
    (cfg.state_path / "mode.json").write_text(json.dumps({"mode": "PAPER"}), encoding="utf-8")
    return cfg


def _plan(px: float) -> dict:
    return {"setup_type": "pullback", "expected_r": 1.5, "p_win": 0.55, "entry": px, "stop": px * 0.97,
            "targets": [px * 1.05, px * 1.09], "rr": 2.0, "leverage": 1, "notional": 15.0}


# =========================================================================== 1) feature doğruluğu
def test_features_computed_correctly_from_synthetic_bars():
    """MA/getiri/RSI/ATR/hacim değerleri sentetik mumlardan matematiksel olarak doğru çıkmalı."""
    n, end = 120, int(NOW.timestamp() * 1000)
    close = [100.0 + i for i in range(n)]                      # kesin lineer artış
    bars = pd.DataFrame({"timestamp": [end - (n - 1 - i) * BAR_MS for i in range(n)],
                         "open": close, "high": [c + 1 for c in close], "low": [c - 1 for c in close],
                         "close": close, "volume": [1000.0] * n})
    s = build_snapshot(symbol="ETH/USDT", market_type="USDM_PERP", timeframe="4h", side="LONG",
                       decision_ts_ms=end, bars=bars, plan=_plan(close[-1]))
    v = s.values
    assert v["close"] == pytest.approx(close[-1])
    assert v["ma25"] == pytest.approx(sum(close[-25:]) / 25)
    assert v["ma99"] == pytest.approx(sum(close[-99:]) / 99)
    assert v["ret_1"] == pytest.approx(100.0 * (close[-1] - close[-2]) / abs(close[-2]))
    assert v["ret_4"] == pytest.approx(100.0 * (close[-1] - close[-5]) / abs(close[-5]))
    assert v["ma_cross_dir"] == 1.0 and v["px_vs_ma99_pct"] > 0        # sürekli yükseliş
    assert v["rsi_fast"] == pytest.approx(100.0)                        # hiç düşüş yok
    assert v["momentum_dir"] == 1.0
    assert v["volume_z"] == pytest.approx(0.0)                          # sabit hacim
    assert v["is_long"] == 1.0 and v["is_short"] == 0.0
    assert v["stop_dist_pct"] == pytest.approx(3.0, abs=1e-6)
    assert v["atr"] > 0 and v["atr_pct"] > 0


def test_snapshot_field_inventory_is_versioned_and_stable():
    names = snapshot_feature_names()
    assert len(FIELD_NAMES) > 100 and len(REQUIRED_FIELDS) >= 30
    assert len(names) == len(set(names))                                 # ad çakışması yok
    assert all(n.startswith(MISS_PREFIX) or n in FIELD_NAMES for n in names)
    s = make_snapshot(symbol="ETH/USDT", side="LONG", decision_ts_ms=int(NOW.timestamp() * 1000))
    assert s["feature_version"] == SNAPSHOT_VERSION and s["schema_id"] == "feature_snapshot_v3"
    for key in ("symbol", "market_type", "timeframe", "side", "decision_ts", "last_bar_ts", "run_id",
                "seed", "config_hash", "strategy_version", "model_version", "pattern_version", "snapshot_hash"):
        assert key in s


# =========================================================================== 2) nedensellik / leakage
def test_future_row_mutation_does_not_change_past_snapshot():
    end = int(NOW.timestamp() * 1000)
    bars = synth_bars(end_ms=end)
    a = build_snapshot(symbol="X/USDT", market_type="USDM_PERP", timeframe="4h", side="LONG",
                       decision_ts_ms=end, bars=bars, plan=_plan(100.0))
    future = pd.concat([bars, pd.DataFrame({"timestamp": [end + BAR_MS], "open": [9e5], "high": [9e5],
                                            "low": [9e5], "close": [9e5], "volume": [1.0]})], ignore_index=True)
    b = build_snapshot(symbol="X/USDT", market_type="USDM_PERP", timeframe="4h", side="LONG",
                       decision_ts_ms=end, bars=future[future["timestamp"] <= end], plan=_plan(100.0))
    assert a.hash() == b.hash() and a.values == b.values


def test_bar_after_decision_ts_is_blocked():
    end = int(NOW.timestamp() * 1000)
    bars = synth_bars(end_ms=end)
    with pytest.raises(LeakageError):
        build_snapshot(symbol="X/USDT", market_type="USDM_PERP", timeframe="4h", side="LONG",
                       decision_ts_ms=end - 3 * BAR_MS, bars=bars, plan=_plan(100.0))
    with pytest.raises(LeakageError):                                    # BTC bağlamı da denetlenir
        build_snapshot(symbol="X/USDT", market_type="USDM_PERP", timeframe="4h", side="LONG",
                       decision_ts_ms=end, bars=bars[bars["timestamp"] <= end], btc_bars=synth_bars(end_ms=end + BAR_MS),
                       plan=_plan(100.0))


def test_source_namespace_does_not_change_the_market_view():
    """`source` yalnız namespace'tir: aynı piyasa görüntüsünde vektörü/hash'i DEĞİŞTİRMEZ.

    DİKKAT: bu test replay ve canlı yolların birbiriyle tutarlı olduğunu KANITLAMAZ — aynı fonksiyonu
    aynı argümanlarla iki kez çağırır. İki gerçek çağrı yerinin paritesi
    `tests/test_prediction_parity.py::test_replay_and_live_prediction_vectors_are_identical_on_same_context`
    içinde, metotların gerçek gövdeleri çalıştırılarak doğrulanır.
    """
    end = int(NOW.timestamp() * 1000)
    common = dict(symbol="ETH/USDT", market_type="USDM_PERP", timeframe="4h", side="SHORT",
                  decision_ts_ms=end, bars=synth_bars(end_ms=end), plan=_plan(120.0),
                  decision={"consensus_score": -0.3, "consensus_conf": 0.5, "n_dissent": 0, "n_vetoes": 0,
                            "risk_allowed": True})
    a = build_snapshot(source="HISTORICAL_REPLAY", **common)
    b = build_snapshot(source="LIVE_PAPER", **common)
    assert a.vector() == b.vector()
    assert a.hash() == b.hash()                                          # source hash'e girmez (aynı piyasa görüntüsü)
    assert a.source != b.source


def test_missingness_is_distinguished_from_real_zero():
    end = int(NOW.timestamp() * 1000)
    bars = synth_bars(end_ms=end)
    no_micro = build_snapshot(symbol="X/USDT", market_type="USDM_PERP", timeframe="4h", side="LONG",
                              decision_ts_ms=end, bars=bars, plan=_plan(100.0))
    zero_micro = build_snapshot(symbol="X/USDT", market_type="USDM_PERP", timeframe="4h", side="LONG",
                                decision_ts_ms=end, bars=bars, plan=_plan(100.0),
                                funding={"rate": 0.0}, micro={"spread_pct": 0.0})
    assert "funding_rate" in no_micro.missing and "funding_rate" not in zero_micro.missing
    assert no_micro.vector()[MISS_PREFIX + "funding_rate"] == 1.0
    assert zero_micro.vector()[MISS_PREFIX + "funding_rate"] == 0.0
    assert zero_micro.values["funding_rate"] == 0.0                      # gerçek 0 değeri korunur
    assert no_micro.hash() != zero_micro.hash()


# =========================================================================== 3) coverage gate
def _rows(n=40, *, with_snapshot=True, sides=("LONG", "SHORT"), symbols=("ETH/USDT", "SOL/USDT"),
          source="HISTORICAL_REPLAY", broken_join=False):
    out = []
    for i in range(n):
        opened = BASE + timedelta(days=i)
        side, sym = sides[i % len(sides)], symbols[i % len(symbols)]
        row = {"trade_id": f"T{i}", "source": source, "recorded_at": opened.isoformat(),
               "outcome": ({} if broken_join else {"symbol": sym, "side": side, "r_multiple": 1.0 if i % 3 else -1.0,
                                                   "opened_at": opened.isoformat(),
                                                   "closed_at": (opened + timedelta(days=1)).isoformat()})}
        if with_snapshot:
            row["snapshot"] = make_snapshot(symbol=sym, side=side, decision_ts_ms=int(opened.timestamp() * 1000),
                                            seed=3 + i % 5)
        else:
            row["features"] = sparse_features()
        out.append(row)
    return out


def test_coverage_gate_passes_on_rich_snapshots():
    rep = coverage_report(_rows(40), source="HISTORICAL_REPLAY")
    assert rep["ok"] and rep["code"] == "OK", rep["problems"]
    assert rep["required_available_pct"] >= 90.0 and rep["join"]["broken"] == 0
    assert set(rep["sides"]) == {"LONG", "SHORT"} and len(rep["symbols"]) >= 2
    f = rep["fields"]["ma99"]
    assert f["available_pct"] == 100.0 and not f["constant"] and f["unique"] > 1


def test_old_core4_like_sparse_memory_is_feature_coverage_invalid():
    """Eski Core-4 hafızası (yalnız expected_r dolu, snapshot yok) → eğitim değil, açık BLOCK."""
    rep = coverage_report(_rows(40, with_snapshot=False), source="HISTORICAL_REPLAY")
    assert not rep["ok"] and rep["code"] == COVERAGE_INVALID
    assert any("FeatureSnapshotV3 yok" in p for p in rep["problems"])


def test_coverage_gate_blocks_broken_join_namespace_mix_and_single_side():
    broken = coverage_report(_rows(30, broken_join=True), source="HISTORICAL_REPLAY")
    assert not broken["ok"] and any("join" in p for p in broken["problems"])
    mixed = _rows(30)
    mixed[0]["source"] = "LIVE_PAPER"
    rep = coverage_report(mixed, source="HISTORICAL_REPLAY")
    assert not rep["ok"] and any("namespace" in p for p in rep["problems"])
    one_side = coverage_report(_rows(30, sides=("LONG",)), source="HISTORICAL_REPLAY")
    assert not one_side["ok"] and any("taraf" in p for p in one_side["problems"])
    one_symbol = coverage_report(_rows(30, symbols=("ETH/USDT",)), source="HISTORICAL_REPLAY")
    assert not one_symbol["ok"] and any("sembol" in p for p in one_symbol["problems"])


def test_coverage_gate_blocks_timestamp_leakage():
    rows = _rows(30)
    rows[0]["snapshot"]["last_bar_ts"] = "2099-01-01T00:00:00+00:00"      # karar anından sonra
    rep = coverage_report(rows, source="HISTORICAL_REPLAY")
    assert not rep["ok"] and any("leakage" in p for p in rep["problems"])


def test_training_is_blocked_on_sparse_memory(tmp_path):
    """Uçtan uca: sparse hafızada `replay-train` FEATURE_COVERAGE_INVALID ile durur."""
    from tradingbot.replay.research import ReplaySafetyError, resolve_replay_dir, train_replay_challenger
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "sparse_run")
    rdir.mkdir(parents=True, exist_ok=True)
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    for i in range(40):
        opened = BASE + timedelta(days=i)
        mem.record_entry({"trade_id": f"S{i}", "symbol": "BTC/USDT", "features": sparse_features(),
                          "recorded_at": opened.isoformat()})
        mem.record_exit(f"S{i}", {"symbol": "BTC/USDT", "side": "LONG", "r_multiple": -0.5,
                                  "opened_at": opened.isoformat(),
                                  "closed_at": (opened + timedelta(days=1)).isoformat(),
                                  "recorded_at": opened.isoformat()}, [], {})
    (rdir / "replay_result.json").write_text(json.dumps({"seed": 7, "determinism_hash": "x", "windows": []}),
                                             encoding="utf-8")
    with pytest.raises(ReplaySafetyError, match=COVERAGE_INVALID):
        train_replay_challenger(cfg, rdir, seed=7)
    assert not (rdir / "train_manifest.json").exists()


# =========================================================================== 4) bounded policy
def test_candidate_generation_is_deterministic_and_bounded():
    a = generate_candidates(seed=7)
    b = generate_candidates(seed=7)
    assert [x.policy_id for x in a] == [x.policy_id for x in b]
    assert [x.hash() for x in a] == [x.hash() for x in b]
    assert len({x.policy_id for x in a}) == len(a)
    for c in a:
        assert c.size_multiplier <= 1.0 and c.max_leverage_cap <= 1.0
        d = c.to_dict()
        assert d["capabilities"] == {"can_change_risk_limits": False, "can_enable_live": False,
                                     "can_modify_open_positions": False, "can_modify_source": False,
                                     "can_only_filter_or_shrink": True}
    assert generate_candidates(seed=8)[0].seed == 8


def test_policy_cannot_increase_risk_or_touch_forbidden_keys():
    with pytest.raises(PolicyBoundsError, match="size_multiplier"):
        validate_policy(CandidatePolicy(policy_id="x", seed=1, size_multiplier=1.5))
    with pytest.raises(PolicyBoundsError, match="tavan"):
        validate_policy(CandidatePolicy(policy_id="x", seed=1, max_leverage_cap=3.0), risk_profile_max_leverage=1.0)
    with pytest.raises(PolicyBoundsError):
        validate_policy(CandidatePolicy(policy_id="x", seed=1, min_p_win=0.99))
    with pytest.raises(PolicyBoundsError):
        validate_policy(CandidatePolicy(policy_id="x", seed=1, agent_weights={"trend": 5.0}))
    with pytest.raises(PolicyBoundsError, match="yasak anahtar"):
        validate_policy(CandidatePolicy(policy_id="x", seed=1, notes="allow_live"))
    assert POLICY_BOUNDS["size_multiplier"][1] == 1.0


def test_policy_penalises_negative_short_and_preserves_positive_long():
    """Negatif SHORT fixture'ı veto/penalize edilir; pozitif LONG korunur."""
    p = CandidatePolicy(policy_id="c1", seed=7, side_veto=["SHORT"], min_expected_net_r=0.0)
    validate_policy(p)
    short = p.decide({"consensus_score": 0.5}, side="SHORT", symbol="ETH/USDT", p_win=0.6, expected_net_r=1.0)
    long_ = p.decide({"consensus_score": 0.5}, side="LONG", symbol="ETH/USDT", p_win=0.6, expected_net_r=1.0)
    assert not short["allow"] and "SIDE_VETO:SHORT" in short["reasons"]
    assert long_["allow"] and long_["size_multiplier"] == 1.0
    pen = CandidatePolicy(policy_id="c2", seed=7, side_penalty={"SHORT": 0.5}, min_expected_net_r=0.2)
    assert not pen.decide({}, side="SHORT", symbol="X", p_win=0.9, expected_net_r=0.5)["allow"]
    assert pen.decide({}, side="LONG", symbol="X", p_win=0.9, expected_net_r=0.5)["allow"]


def test_baseline_policy_is_passthrough():
    b = baseline_policy()
    d = b.decide({}, side="SHORT", symbol="ANY", p_win=0.0, expected_net_r=-5.0)
    assert d["allow"] and d["size_multiplier"] == 1.0 and d["reasons"] == ["BASELINE_PASSTHROUGH"]


# =========================================================================== 5) attribution
def test_attribution_reports_context_relations_with_disclaimer():
    rows = _rows(60)
    for i, r in enumerate(rows):                                # SHORT'ları sistematik zararlı yap
        if (r["outcome"].get("side") or "") == "SHORT":
            r["outcome"]["r_multiple"] = -0.8
    rep = attribution_report(rows, min_bucket=5)
    assert rep["cuts"]["side"]["SHORT"]["expectancy_r"] < 0
    assert any(f["cut"] == "side" and f["label"] == "SHORT" for f in rep["findings"])
    assert any("side=SHORT" in f["text"] for f in rep["findings"])
    assert "nedensellik" in rep["disclaimer"].lower()
    assert rep["cuts"]["vol_regime"] and rep["cuts"]["side_x_regime"]
    assert isinstance(rep["missing_field_rate"], dict)
    assert rep["schema"] == "loss_attribution_v2" and rep["trades"]         # işlem bazlı yapılandırılmış analiz
