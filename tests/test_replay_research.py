"""Replay araştırma hattı testleri — izolasyon (traversal/symlink/canlı çakışma), read-only plan +
kapasite fail-closed, challenger eğitimi (determinizm/idempotency/manifest), OOS değerlendirmesi
(sızıntı/yetersiz örnek/bozuk artifact) ve canlı state'in bayt düzeyinde değişmediği kanıtı."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingbot.config import BotConfig
from tradingbot.config_v3 import load_v3
from tradingbot.learn import ModelRegistry, TradeMemory
from tradingbot.replay.research import (EVAL_REPORT, TRAIN_MANIFEST, ReplaySafetyError, assert_live_state_untouched,
                                        evaluate_replay, plan_replay, resolve_replay_dir, train_replay_challenger)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- yardımcılar
def _cfg(tmp_path: Path):
    """Gerçek BotConfig; project_root tmp'ye alınır → state/cache tmp altındadır (canlı dosyalara dokunulmaz)."""
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    cfg.v3 = load_v3({"learning_v3": {"min_samples_train": 40, "holdout_frac": 0.25}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    cfg.cache_path.mkdir(parents=True, exist_ok=True)
    return cfg


def _live_state(cfg, *, positions=True):
    """Gerçekçi canlı state dosyaları — testler bunların BAYT DÜZEYİNDE değişmediğini kanıtlar."""
    st = cfg.state_path
    (st / "futures_ledger.json").write_text(json.dumps({
        "schema_version": 2, "wallet_balance": "47.21", "positions": ({"BZ/USDT": {"id": "F00004", "side": "LONG", "stop": "88.34"}} if positions else {}),
        "history": [], "entries": []}), encoding="utf-8")
    (st / "trade_memory.jsonl").write_text('{"kind": "entry", "trade_id": "F00004", "source": "LIVE_PAPER"}\n', encoding="utf-8")
    (st / "learn_v2.json").write_text(json.dumps({"n_closed": 3, "lessons": []}), encoding="utf-8")
    (st / "models.json").write_text(json.dumps({"models": [], "events": []}), encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER"}), encoding="utf-8")
    return assert_live_state_untouched(st)


def _rec(i, won):
    return {"id": f"R{i}", "symbol": "ETH/USDT", "side": "LONG", "entry": 100.0, "exit_price": 102.0 if won else 99.0,
            "exit_reason": "hedef2" if won else "stop", "closed_at": (NOW - timedelta(days=60 - i)).isoformat(),
            "net_pnl": 2.0 if won else -1.0, "r_multiple": 2.0 if won else -1.0, "mae_pct": -0.5,
            "mfe_pct": 3.0 if won else 0.2, "bars_held": 4, "leverage": 2, "setup_type": "kırılım",
            "features": {"bias_trend": 0.6 if won else -0.6, "conf_trend": 0.7, "bias_momentum": 0.4 if won else -0.3,
                         "conviction": 0.6 if won else 0.3, "rr": 2.5, "atr_pct": 0.3, "n_warnings": 1 if won else 5, "leverage": 2}}


def _seed_replay_memory(rdir: Path, n: int = 60) -> None:
    """HISTORICAL_REPLAY namespace'inde n kapanmış işlem (zaman sırasıyla)."""
    rdir.mkdir(parents=True, exist_ok=True)
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    for i in range(n):
        rec = _rec(i, i % 3 != 0)
        ts = (NOW - timedelta(days=n - i)).isoformat()
        mem.record_entry({"trade_id": rec["id"], "symbol": rec["symbol"], "direction": "LONG", "setup_type": "kırılım",
                          "regime": "TREND_UP", "features": rec["features"], "recorded_at": ts})
        mem.record_exit(rec["id"], rec | {"recorded_at": ts}, [], {"lesson_text_tr": ["ders"]})


class _FakeManifest:
    def __init__(self, rows, first, last, gaps=0, bad=None):
        self.row_count, self.first_ts_ms, self.last_ts_ms = rows, first, last
        self.gap_count, self.bad_chunks = gaps, list(bad or [])


class _FakeStore:
    """Yalnız manifest okunur (plan veri OKUMAZ)."""
    def __init__(self, table):
        self.table = table
        self.read_calls = 0

    def manifest(self, market, symbol, tf):
        key = (market, symbol, tf)
        if key not in self.table:
            raise FileNotFoundError(key)
        return self.table[key]

    def read(self, *a, **k):
        self.read_calls += 1
        raise AssertionError("plan veri okumamalı (read-only manifest sözleşmesi)")


def _store_4h(symbols, rows=4000, tf="4h"):
    step, last = 14_400_000, 1_787_000_000_000
    t = {}
    for s in symbols:
        t[("futures", s, tf)] = _FakeManifest(rows, last - rows * step, last)
        for aux in ("1d", "1h"):
            t[("futures", s, aux)] = _FakeManifest(rows, last - rows * step, last)
    return _FakeStore(t)


# --------------------------------------------------------------------------- 1) izolasyon
def test_resolve_replay_dir_rejects_traversal_empty_and_live_collision(tmp_path):
    live = tmp_path / "state"
    live.mkdir()
    for bad in ("", "   ", ".", "..", "../evil", "a/b", "a\\b", "/abs", "-flag", ".hidden"):
        with pytest.raises(ReplaySafetyError):
            resolve_replay_dir(live, bad)
    # replay kökü canlı state olamaz
    with pytest.raises(ReplaySafetyError):
        resolve_replay_dir(live, "run1", state_root=live)
    good = resolve_replay_dir(live, "run1")
    assert good == (live / "replay" / "run1").resolve() and live in good.parents


@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI"), reason="symlink Windows'ta yönetici ister")
def test_resolve_replay_dir_rejects_symlink_escape(tmp_path):
    live = tmp_path / "state"
    (live / "replay").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = live / "replay" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink oluşturulamadı")
    with pytest.raises(ReplaySafetyError):
        resolve_replay_dir(live, "escape")


def test_replay_dir_cannot_be_live_state_subpath(tmp_path):
    live = tmp_path / "state"
    live.mkdir()
    with pytest.raises(ReplaySafetyError):          # state_root = canlı state → çakışma
        resolve_replay_dir(live, "x", state_root=live)


# --------------------------------------------------------------------------- 2) plan (read-only + fail-closed)
def test_plan_is_read_only_and_reports_capacity(tmp_path):
    cfg = _cfg(tmp_path)
    before = _live_state(cfg)
    store = _store_4h(["BTC/USDT", "ETH/USDT"])
    plan = plan_replay(cfg, store, run_id="p1", symbols=["BTC/USDT", "ETH/USDT"], market="futures", tf="4h",
                       stride=4, seed=7, available_mb=6600, pattern_stride=4)
    assert store.read_calls == 0                                     # veri okunmadı
    assert plan.ok and plan.risk_class in ("LOW", "MEDIUM")
    assert plan.total_rows == 8000 and plan.timeline_bars > 0 and plan.pattern_events == 2000
    assert plan.est_memory_mb > 0 and plan.est_cpu_minutes > 0 and plan.budget_mb == 6600 - 1024 - 900
    d = plan.to_dict()
    assert d["survivorship_bias"]["present"] is True and d["point_in_time"] is False
    assert assert_live_state_untouched(cfg.state_path) == before     # canlı state bayt bayt aynı
    assert not (cfg.state_path / "replay").exists()                  # plan hiçbir dizin yaratmaz


def test_plan_fail_closed_on_insufficient_data_and_capacity(tmp_path):
    cfg = _cfg(tmp_path)
    store = _store_4h(["BTC/USDT"], rows=100)                        # min_bars altında
    plan = plan_replay(cfg, store, run_id="p2", symbols=["BTC/USDT"], available_mb=6600)
    assert not plan.ok and plan.risk_class == "BLOCKED" and any("yetersiz veri" in b for b in plan.blockers)
    # kapasite: bütçeyi aşan tahmin → BLOCKED
    big = _store_4h(["BTC/USDT", "ETH/USDT", "SOL/USDT"], rows=400_000)
    plan2 = plan_replay(cfg, big, run_id="p3", symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"], available_mb=6600)
    assert not plan2.ok and plan2.risk_class == "BLOCKED" and any("bütçe" in b for b in plan2.blockers)
    # RAM ölçülemiyor ve açık değer verilmedi → fail-closed
    plan3 = plan_replay(cfg, _store_4h(["BTC/USDT"]), run_id="p4", symbols=["BTC/USDT"], available_mb=None)
    if plan3.available_mb is None:
        assert not plan3.ok and any("RAM" in b for b in plan3.blockers)
    # bozuk parça → fail-closed
    st = _store_4h(["BTC/USDT"])
    st.table[("futures", "BTC/USDT", "4h")].bad_chunks = [{"chunk": "2024/01"}]
    plan4 = plan_replay(cfg, st, run_id="p5", symbols=["BTC/USDT"], available_mb=6600)
    assert not plan4.ok and any("bozuk parça" in b for b in plan4.blockers)


def test_plan_missing_manifest_blocks(tmp_path):
    cfg = _cfg(tmp_path)
    plan = plan_replay(cfg, _FakeStore({}), run_id="p6", symbols=["NOPE/USDT"], available_mb=6600)
    assert not plan.ok and any("manifest" in b for b in plan.blockers)


# --------------------------------------------------------------------------- 3) eğitim
def test_train_isolated_deterministic_idempotent_and_no_live_touch(tmp_path):
    cfg = _cfg(tmp_path)
    before = _live_state(cfg)
    rdir = resolve_replay_dir(cfg.state_path, "run_a")
    _seed_replay_memory(rdir)
    m1 = train_replay_challenger(cfg, rdir, seed=7)
    assert m1["source"] == "HISTORICAL_REPLAY" and m1["model_id"] and m1["idempotent_skip"] is False
    assert m1["n_train"] + m1["n_holdout"] == m1["inputs"]["n_closed"] == 60
    assert m1["promotion"]["live_promotion"] is False
    assert (rdir / TRAIN_MANIFEST).exists() and (rdir / "models.json").exists() and (rdir / "learn_v2.json").exists()
    # canlı state bayt bayt aynı (models.json/learn_v2.json/ledger/trade_memory)
    assert assert_live_state_untouched(cfg.state_path) == before
    assert json.loads((cfg.state_path / "models.json").read_text(encoding="utf-8"))["models"] == []
    # replay registry'sinde CANDIDATE, CHAMPION yok
    reg = ModelRegistry(rdir / "models.json")
    assert reg.challenger("p_win_lr") and reg.champion("p_win_lr") is None
    # idempotent: ikinci çağrı yeniden eğitmez, aynı artifact
    m2 = train_replay_challenger(cfg, rdir, seed=7)
    assert m2["idempotent_skip"] is True and m2["params_hash"] == m1["params_hash"] and m2["model_id"] == m1["model_id"]
    assert len(ModelRegistry(rdir / "models.json").models) == 1
    # determinizm: aynı veriyle bağımsız ikinci run aynı ağırlık hash'ini üretir
    rdir_b = resolve_replay_dir(cfg.state_path, "run_b")
    _seed_replay_memory(rdir_b)
    m3 = train_replay_challenger(cfg, rdir_b, seed=7)
    assert m3["params_hash"] == m1["params_hash"] and m3["metrics_hash"] == m1["metrics_hash"]


def test_train_fail_closed_on_insufficient_samples_and_missing_dir(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "small")
    _seed_replay_memory(rdir, n=5)
    with pytest.raises(ReplaySafetyError, match="yetersiz"):
        train_replay_challenger(cfg, rdir)
    with pytest.raises(ReplaySafetyError):
        train_replay_challenger(cfg, cfg.state_path / "replay" / "yok")


# --------------------------------------------------------------------------- 4) değerlendirme
def test_evaluate_reports_oos_without_promotion(tmp_path):
    cfg = _cfg(tmp_path)
    before = _live_state(cfg)
    rdir = resolve_replay_dir(cfg.state_path, "run_e")
    _seed_replay_memory(rdir)
    train_replay_challenger(cfg, rdir, seed=7)
    rep = evaluate_replay(cfg, rdir)
    s = rep["samples"]
    assert s["closed"] == 60 and s["train"] + s["holdout"] == 60 and s["holdout"] > 0
    for k in ("expectancy_r", "win_rate", "max_dd_r", "profit_factor", "n"):
        assert k in rep["out_of_sample"]
    assert set(rep["calibration"]) & {"brier", "ece", "log_loss"}
    assert rep["promotion"]["live_promotion"] is False and rep["model_status"] == "CANDIDATE"
    assert rep["survivorship_bias"]["present"] is True and rep["data_range"]["first_recorded_at"]
    assert rep["determinism"]["params_hash"] and "verdict" in rep
    assert (rdir / EVAL_REPORT).exists()
    assert assert_live_state_untouched(cfg.state_path) == before
    # aynı girdiyle tekrar → aynı hash'ler (deterministik rapor)
    rep2 = evaluate_replay(cfg, rdir)
    assert rep2["determinism"] == rep["determinism"] and rep2["out_of_sample"] == rep["out_of_sample"]


def test_evaluate_fail_closed_paths(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "run_f")
    _seed_replay_memory(rdir)
    with pytest.raises(ReplaySafetyError, match="manifest"):          # eğitim yapılmadan
        evaluate_replay(cfg, rdir)
    train_replay_challenger(cfg, rdir, seed=7)
    # yetersiz örnek eşiği
    with pytest.raises(ReplaySafetyError, match="yetersiz örnek"):
        evaluate_replay(cfg, rdir, min_samples=999)
    # hafıza eğitimden sonra değişti → bayat artifact
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    rec = _rec(99, True)
    mem.record_entry({"trade_id": rec["id"], "symbol": rec["symbol"], "features": rec["features"], "recorded_at": NOW.isoformat()})
    mem.record_exit(rec["id"], rec | {"recorded_at": NOW.isoformat()}, [], {})
    with pytest.raises(ReplaySafetyError, match="bayat"):
        evaluate_replay(cfg, rdir)
    # bozuk manifest
    (rdir / TRAIN_MANIFEST).write_text("{bozuk", encoding="utf-8")
    with pytest.raises(ReplaySafetyError):
        evaluate_replay(cfg, rdir)


def test_evaluate_blocks_champion_and_split_leakage(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "run_g")
    _seed_replay_memory(rdir)
    man = train_replay_challenger(cfg, rdir, seed=7)
    # train/holdout toplamı tutmuyorsa → sızıntı/tutarsızlık
    bad = dict(man)
    bad["n_train"] = man["n_train"] + 5
    (rdir / TRAIN_MANIFEST).write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ReplaySafetyError, match="tutarsız"):
        evaluate_replay(cfg, rdir)
    (rdir / TRAIN_MANIFEST).write_text(json.dumps(man), encoding="utf-8")
    # replay modeli CHAMPION'a çevrilirse araştırma hattı reddeder (terfi üretmemeli)
    reg = ModelRegistry(rdir / "models.json")
    for m in reg.models:
        m["status"] = "CHAMPION"
    reg.save()
    with pytest.raises(ReplaySafetyError, match="CHAMPION"):
        evaluate_replay(cfg, rdir)


# --------------------------------------------------------------------------- 5) CLI sözleşmeleri
def _args(**kw):
    base = {"symbols": None, "market": "futures", "tf": "4h", "from_": None, "to": None, "stride": 1,
            "pattern_stride": 1, "seed": 0, "run_id": None, "state_dir": None, "no_patterns": False,
            "assume_available_mb": None, "host_reserve_mb": 1024.0, "worker_reserve_mb": 900.0,
            "force": False, "min_samples": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_cli_rejects_unsafe_run_ids(tmp_path, capsys):
    from tradingbot.cli_v3 import cmd_replay_evaluate, cmd_replay_plan, cmd_replay_train
    cfg = _cfg(tmp_path)
    for fn in (cmd_replay_plan, cmd_replay_train, cmd_replay_evaluate):
        rc = fn(cfg, _args(run_id="../escape"))
        assert rc == 2
        assert "BLOCK" in capsys.readouterr().out


def test_cli_train_evaluate_roundtrip_and_live_untouched(tmp_path, capsys):
    from tradingbot.cli_v3 import cmd_replay_evaluate, cmd_replay_train
    cfg = _cfg(tmp_path)
    before = _live_state(cfg)
    rdir = resolve_replay_dir(cfg.state_path, "cli_run")
    _seed_replay_memory(rdir)
    assert cmd_replay_train(cfg, _args(run_id="cli_run", seed=7)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["promotion"]["live_promotion"] is False
    assert cmd_replay_evaluate(cfg, _args(run_id="cli_run")) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["samples"]["closed"] == 60 and rep["model_status"] == "CANDIDATE"
    assert assert_live_state_untouched(cfg.state_path) == before
    # eksik run → non-zero
    assert cmd_replay_evaluate(cfg, _args(run_id="yok_boyle_run")) == 2


def test_cli_plan_exit_codes(tmp_path, capsys, monkeypatch):
    import tradingbot.cli_v3 as cli
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(cli, "HistoryStore", _FakeStore, raising=False)
    import tradingbot.history as hist
    monkeypatch.setattr(hist, "HistoryStore", lambda *a, **k: _store_4h(["BTC/USDT"]), raising=False)
    rc = cli.cmd_replay_plan(cfg, _args(run_id="cli_plan", symbols=["BTC/USDT"], assume_available_mb=6600, stride=4))
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    monkeypatch.setattr(hist, "HistoryStore", lambda *a, **k: _store_4h(["BTC/USDT"], rows=50), raising=False)
    rc2 = cli.cmd_replay_plan(cfg, _args(run_id="cli_plan2", symbols=["BTC/USDT"], assume_available_mb=6600))
    assert rc2 == 1                                                   # yetersiz veri → non-zero


# --------------------------------------------------------------------------- 6) runner script sözleşmesi
def test_replay_runner_contract():
    sh = (REPO_ROOT / "deploy" / "replay_runner.sh").read_text(encoding="utf-8")
    assert "set -Eeuo pipefail" in sh
    assert 'sudo -u "$SVC_USER"' in sh and 'cd "$APP"' in sh                 # service user + APP cwd
    assert "TRADINGBOT_STATE_DIR=" in sh and "TRADINGBOT_DATA=" in sh        # açık DATA/state
    assert "mode-status" in sh and "PAPER" in sh                             # PAPER zorunlu
    assert "replay-plan" in sh and "iş başlatılmadı" in sh                   # kapasite fail-closed
    assert all(k in sh for k in ("MemoryMax=", "CPUQuota=", "Nice=", "IOWeight="))
    assert "env -i" in sh and "EnvironmentFile" not in sh.replace("EnvironmentFile yüklenmez", "")
    for forbidden in ("systemctl stop", "systemctl restart", "ANTHROPIC", "API_KEY"):
        assert forbidden not in sh, forbidden
