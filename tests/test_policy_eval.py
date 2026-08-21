"""Baseline ↔ candidate walk-forward davranış testleri: fold yalnız geçmişle eğitilir, test fold'u
seçime girmez, metrik farkı gerçekten politika seçiminden gelir, terfi yoktur."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import make_snapshot
from tradingbot.config import BotConfig
from tradingbot.config_v3 import load_v3
from tradingbot.replay.policy_eval import POLICY_EVAL_REPORT, evaluate_policies
from tradingbot.replay.research import ReplaySafetyError, resolve_replay_dir

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
BASE = NOW - timedelta(days=140)
DAY_MS = 86_400_000


def _cfg(tmp_path: Path):
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    cfg.v3 = load_v3({"learning_v3": {"min_samples_train": 20}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    (cfg.state_path / "mode.json").write_text(json.dumps({"mode": "PAPER"}), encoding="utf-8")
    return cfg


def _ms(d: int) -> int:
    return int((BASE + timedelta(days=d)).timestamp() * 1000)


def _bounds(idx, tr_s, tr_e, ts_s, ts_e):
    return {"idx": idx, "train_start_ms": _ms(tr_s), "train_end_ms": _ms(tr_e),
            "purge_start_ms": _ms(tr_e), "purge_end_ms": _ms(tr_e + 1),
            "embargo_start_ms": _ms(tr_e + 1), "embargo_end_ms": _ms(tr_e + 2),
            "test_start_ms": _ms(ts_s), "test_end_ms": _ms(ts_e),
            "purge_bars": 1, "embargo_bars": 1, "bar_ms": DAY_MS}


def _rows(n=120, *, short_r=-0.8, long_r=0.9):
    """SHORT sistematik zararlı, LONG kârlı → doğru aday SHORT'u veto etmeli."""
    out = []
    for i in range(n):
        opened = BASE + timedelta(days=i)
        side = "SHORT" if i % 2 else "LONG"
        sym = "ETH/USDT" if i % 3 else "SOL/USDT"
        snap = make_snapshot(symbol=sym, side=side, decision_ts_ms=int(opened.timestamp() * 1000), seed=3 + i % 5)
        out.append({"trade_id": f"T{i}", "source": "HISTORICAL_REPLAY", "recorded_at": opened.isoformat(),
                    "snapshot": snap,
                    "outcome": {"symbol": sym, "side": side, "r_multiple": short_r if side == "SHORT" else long_r,
                                "opened_at": opened.isoformat(),
                                "closed_at": (opened + timedelta(days=1)).isoformat()},
                    "_open_ms": int(opened.timestamp() * 1000),
                    "_close_ms": int((opened + timedelta(days=1)).timestamp() * 1000)})
    return out


BOUNDS = [_bounds(0, 0, 40, 42, 62), _bounds(1, 0, 62, 64, 84), _bounds(2, 0, 84, 86, 106)]


def test_candidate_selected_on_past_only_beats_baseline_on_test_folds(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pol1")
    rdir.mkdir(parents=True, exist_ok=True)
    rep = evaluate_policies(cfg, rdir, _rows(), BOUNDS, seed=7, min_test_trades=5)
    assert rep["scored_folds"] >= 2
    # aday SHORT'u eleyince beklenti baseline'ı geçmeli (fark gerçekten politika seçiminden gelir)
    assert rep["candidate"]["expectancy_r"] > rep["baseline"]["expectancy_r"]
    assert rep["candidate"]["n"] < rep["baseline"]["n"]                   # bazı işlemler filtrelendi
    assert rep["delta_expectancy_r"] > 0
    for f in rep["folds"]:
        if "baseline" in f:
            assert f["selected_policy"] and f["validation_n"] > 0
            assert f["blocked_in_test"] > 0                               # test fold'unda gerçekten filtreledi
    assert (rdir / POLICY_EVAL_REPORT).exists()
    assert rep["promotion"]["live_promotion"] is False and rep["promotion"]["promote_called"] is False
    assert rep["verdict"] in ("RESEARCH_ONLY", "REJECTED", "SHADOW_CANDIDATE")


def test_selection_never_uses_its_own_test_fold(tmp_path, monkeypatch):
    """Her fold için seçim YALNIZ o fold'un train_end'inden önceki satırlarla yapılır.
    (Anchored-forward'da sonraki fold'un train'i önceki fold'un test penceresini kapsayabilir — bu meşrudur;
    yasak olan, bir fold'un KENDİ test penceresini seçimde kullanmasıdır.)"""
    import tradingbot.replay.policy_eval as pe
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pol2")
    rdir.mkdir(parents=True, exist_ok=True)
    rows = _rows()
    calls: list[set] = []
    real_score = pe._score

    def spy(rs, policy, **kw):
        calls.append({r["trade_id"] for r in rs})
        return real_score(rs, policy, **kw)

    monkeypatch.setattr(pe, "_score", spy)
    rep = pe.evaluate_policies(cfg, rdir, rows, BOUNDS, seed=7, min_test_trades=5)
    n_cands = rep["n_candidates"]
    scored_folds = [f for f in rep["folds"] if "baseline" in f]
    assert calls and len(calls) == n_cands * len(scored_folds)
    by_id = {r["trade_id"]: r for r in rows}
    for i, fold in enumerate(scored_folds):
        b = next(x for x in BOUNDS if x["idx"] == fold["idx"])
        chunk = calls[i * n_cands:(i + 1) * n_cands]
        used = set().union(*chunk)
        assert used, f"fold {fold['idx']}: seçim boş"
        # kendi test penceresi ve sonrası seçime GİREMEZ
        assert all(by_id[t]["_open_ms"] < b["train_end_ms"] for t in used)
        own_test = {t for t in by_id if b["test_start_ms"] <= by_id[t]["_open_ms"] < b["test_end_ms"]}
        assert not (used & own_test), f"fold {fold['idx']}: kendi test fold'u seçimde kullanıldı"


def test_evaluation_is_deterministic_and_idempotent(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pol3")
    rdir.mkdir(parents=True, exist_ok=True)
    a = evaluate_policies(cfg, rdir, _rows(), BOUNDS, seed=7, min_test_trades=5)
    b = evaluate_policies(cfg, rdir, _rows(), BOUNDS, seed=7, min_test_trades=5)
    for k in ("baseline", "candidate", "selected_policies", "fold_consistency", "gates", "verdict"):
        assert a[k] == b[k], k
    c = evaluate_policies(cfg, rdir, _rows(), BOUNDS, seed=99, min_test_trades=5)
    assert c["seed"] == 99                                                # seed raporlanır (bootstrap'i etkiler)


def test_pit_and_survivorship_gates_cap_verdict(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pol4")
    rdir.mkdir(parents=True, exist_ok=True)
    biased = evaluate_policies(cfg, rdir, _rows(), BOUNDS, seed=7, min_test_trades=5,
                               point_in_time=False, survivorship_present=True)
    assert biased["gates"]["point_in_time"] is False and biased["gates"]["survivorship_clean"] is False
    assert biased["verdict"] != "SHADOW_CANDIDATE"
    clean = evaluate_policies(cfg, rdir, _rows(), BOUNDS, seed=7, min_test_trades=5,
                              point_in_time=True, survivorship_present=False)
    assert clean["gates"]["point_in_time"] is True and clean["gates"]["survivorship_clean"] is True


def test_negative_candidate_is_rejected(tmp_path):
    """Aday baseline'ı geçemiyorsa REJECTED; asla SHADOW_CANDIDATE."""
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pol5")
    rdir.mkdir(parents=True, exist_ok=True)
    rep = evaluate_policies(cfg, rdir, _rows(short_r=-0.9, long_r=-0.9), BOUNDS, seed=7, min_test_trades=5)
    assert rep["candidate"]["expectancy_r"] is None or rep["candidate"]["expectancy_r"] <= 0
    assert rep["verdict"] == "REJECTED" and rep["shadow_candidate"] if "shadow_candidate" in rep else True
    assert not rep["gates"]["candidate_positive"]


def test_requires_at_least_two_folds(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pol6")
    rdir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ReplaySafetyError, match="2 fold"):
        evaluate_policies(cfg, rdir, _rows(), BOUNDS[:1], seed=7, min_test_trades=5)


def test_no_promotion_call_in_policy_path(tmp_path, monkeypatch):
    from tradingbot.learn import LearnerV2
    calls = []
    monkeypatch.setattr(LearnerV2, "maybe_promote", lambda self, *a, **k: calls.append(a) or (True, []))
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pol7")
    rdir.mkdir(parents=True, exist_ok=True)
    evaluate_policies(cfg, rdir, _rows(), BOUNDS, seed=7, min_test_trades=5)
    assert calls == []


def test_report_written_only_inside_run_dir(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pol8")
    rdir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in cfg.state_path.iterdir()}
    evaluate_policies(cfg, rdir, _rows(), BOUNDS, seed=7, min_test_trades=5)
    after = {p.name for p in cfg.state_path.iterdir()}
    assert after == before                                                # canlı state klasörüne yazılmadı
    assert (rdir / POLICY_EVAL_REPORT).is_file()
