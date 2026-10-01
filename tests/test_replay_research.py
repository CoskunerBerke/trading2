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


BASE = NOW - timedelta(days=120)          # replay penceresinin başlangıcı
DAY_MS = 86_400_000


def _ms(dt) -> int:
    return int(dt.timestamp() * 1000)


def _seed_replay_memory(rdir: Path, n: int = 60, *, hold_days: int = 1, extra: list[dict] | None = None) -> None:
    """HISTORICAL_REPLAY namespace'inde n kapanmış işlem: günde bir giriş, `hold_days` sonra kapanış.
    `opened_at`/`closed_at` gerçek olay zamanlarıdır → walk-forward fold'ları bunlara göre bölünür."""
    from conftest import make_snapshot
    rdir.mkdir(parents=True, exist_ok=True)
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    for i in range(n):
        rec = _rec(i, i % 3 != 0)
        opened = BASE + timedelta(days=i)
        closed = opened + timedelta(days=hold_days)
        sym = "ETH/USDT" if i % 2 else "SOL/USDT"
        side = "LONG" if i % 2 else "SHORT"
        rec = rec | {"opened_at": opened.isoformat(), "closed_at": closed.isoformat(), "symbol": sym, "side": side}
        snap = make_snapshot(symbol=sym, side=side, decision_ts_ms=int(opened.timestamp() * 1000), seed=3 + i % 5)
        mem.record_entry({"trade_id": rec["id"], "symbol": sym, "direction": side, "setup_type": "kırılım",
                          "regime": "TREND_UP", "features": rec["features"], "snapshot": snap,
                          "recorded_at": opened.isoformat()})
        mem.record_exit(rec["id"], rec | {"recorded_at": opened.isoformat()}, [], {"lesson_text_tr": ["ders"]})
    for e in extra or []:
        opened, closed = e["opened"], e["closed"]
        rec = _rec(e.get("idx", 900), e.get("won", True)) | {"id": e["id"], "opened_at": opened.isoformat(),
                                                             "closed_at": closed.isoformat(), "side": "LONG"}
        snap = make_snapshot(symbol=rec["symbol"], side="LONG", decision_ts_ms=int(opened.timestamp() * 1000))
        mem.record_entry({"trade_id": e["id"], "symbol": rec["symbol"], "direction": "LONG", "setup_type": "kırılım",
                          "regime": "TREND_UP", "features": rec["features"], "snapshot": snap,
                          "recorded_at": opened.isoformat()})
        mem.record_exit(e["id"], rec | {"recorded_at": opened.isoformat()}, [], {})


def _bounds(idx: int, train_start_d: int, train_end_d: int, purge_d: int, embargo_d: int, test_start_d: int, test_end_d: int) -> dict:
    return {"idx": idx, "train_start_ms": _ms(BASE + timedelta(days=train_start_d)),
            "train_end_ms": _ms(BASE + timedelta(days=train_end_d)),
            "purge_start_ms": _ms(BASE + timedelta(days=train_end_d)),
            "purge_end_ms": _ms(BASE + timedelta(days=train_end_d + purge_d)),
            "embargo_start_ms": _ms(BASE + timedelta(days=train_end_d + purge_d)),
            "embargo_end_ms": _ms(BASE + timedelta(days=train_end_d + purge_d + embargo_d)),
            "test_start_ms": _ms(BASE + timedelta(days=test_start_d)),
            "test_end_ms": _ms(BASE + timedelta(days=test_end_d)),
            "purge_bars": purge_d, "embargo_bars": embargo_d, "bar_ms": DAY_MS}


def _seed_replay_result(rdir: Path, bounds: list[dict] | None = None, *, seed: int = 7,
                        symbols: list[str] | None = None, rejections: dict | None = None) -> list[dict]:
    """Gerçek `historical-replay` çıktısının şeması: pencerelerin KESİN sınırlarıyla (bounds)."""
    b = bounds if bounds is not None else [
        _bounds(0, 0, 30, 1, 1, 32, 47),
        _bounds(1, 0, 45, 1, 1, 47, 62),
        _bounds(2, 0, 60, 1, 1, 62, 77),
    ]
    (rdir / "replay_result.json").write_text(json.dumps({
        "run_id": rdir.name, "seed": seed, "determinism_hash": "deadbeef",
        "symbols": symbols if symbols is not None else ["ETH/USDT", "SOL/USDT"],
        "n_actionable": 100, "n_opened": 90, "point_in_time": False,
        "survivorship_bias": {"present": True},
        "rejections": rejections if rejections is not None else {
            "total": 12, "by_reason": {"STEP_ZERO_QTY": 12},
            "by_symbol": {"SOL/USDT": {"STEP_ZERO_QTY": 12}}},
        "windows": [{"idx": x["idx"], "train": ["", ""], "test": ["", ""], "bounds": x} for x in b]}), encoding="utf-8")
    return b


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
    _seed_replay_result(rdir)
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
    _seed_replay_result(rdir_b)
    m3 = train_replay_challenger(cfg, rdir_b, seed=7)
    assert m3["params_hash"] == m1["params_hash"] and m3["metrics_hash"] == m1["metrics_hash"]


def test_train_fail_closed_on_insufficient_samples_and_missing_dir(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "small")
    _seed_replay_memory(rdir, n=5)
    _seed_replay_result(rdir)
    with pytest.raises(ReplaySafetyError, match="yetersiz"):
        train_replay_challenger(cfg, rdir)
    with pytest.raises(ReplaySafetyError):
        train_replay_challenger(cfg, cfg.state_path / "replay" / "yok")


# --------------------------------------------------------------------------- 4) değerlendirme
def test_evaluate_real_multi_fold_walk_forward(tmp_path):
    cfg = _cfg(tmp_path)
    before = _live_state(cfg)
    rdir = resolve_replay_dir(cfg.state_path, "run_e")
    _seed_replay_memory(rdir, n=90)
    _seed_replay_result(rdir)
    train_replay_challenger(cfg, rdir, seed=7)
    rep = evaluate_replay(cfg, rdir, min_samples=10)
    wf = rep["walk_forward"]
    assert wf["scored_folds"] >= 2 and wf["purge_embargo_enforced"] is True
    scored = [f for f in rep["folds"] if "calibration" in f]
    assert len(scored) == wf["scored_folds"]
    for f in scored:                                   # her fold'da gerçek eğitim + test ve tarih aralıkları
        assert f["n_train"] > 0 and f["n_test"] > 0
        assert f["train_range"][0] < f["train_range"][1] <= f["test_range"][0] < f["test_range"][1]
        for k in ("expectancy_r", "win_rate", "max_dd_r", "profit_factor", "n"):
            assert k in f
        for k in ("brier", "ece", "log_loss"):
            assert k in f["calibration"]
    # fold train pencereleri ilerlemeli (anchored-forward)
    assert [f["n_train"] for f in scored] == sorted(f["n_train"] for f in scored)
    agg = rep["out_of_sample"]
    assert agg["n"] == sum(f["n_test"] for f in scored) == rep["samples"]["oos_pooled"]
    assert set(rep["gates"]) == {"enough_oos", "positive_expectancy", "profit_factor_above_one",
                                 "ci95_lower_above_zero", "calibration_ok", "drawdown_ok",
                                 "fold_consistency", "enough_folds", "symbol_coverage", "side_coverage",
                                 "point_in_time", "survivorship_clean"}
    assert rep["shadow_candidate"] == all(rep["gates"].values())
    assert rep["verdict"] in ("SHADOW_CANDIDATE", "RESEARCH_ONLY", "REJECTED")
    assert rep["promotion"]["promote_called"] is False
    assert rep["promotion"]["live_promotion"] is False and rep["model_status"] == "CANDIDATE"
    assert (rdir / EVAL_REPORT).exists() and assert_live_state_untouched(cfg.state_path) == before
    rep2 = evaluate_replay(cfg, rdir, min_samples=10)   # deterministik
    assert rep2["out_of_sample"] == agg and rep2["folds"] == rep["folds"]


def test_evaluate_excludes_purge_and_embargo_records(tmp_path):
    """Etiketi purge/embargo bölgesinde kapanan işlem NE eğitime NE OOS'a girer."""
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "run_purge")
    # 29. günde açılıp purge bölgesinde (30-31) kapanan işlem
    extra = [{"id": "PURGE1", "opened": BASE + timedelta(days=29, hours=12), "closed": BASE + timedelta(days=30, hours=12)}]
    _seed_replay_memory(rdir, n=90, extra=extra)
    _seed_replay_result(rdir, [_bounds(0, 0, 30, 1, 1, 32, 47), _bounds(1, 0, 45, 1, 1, 47, 62)])
    train_replay_challenger(cfg, rdir, seed=7)
    rep = evaluate_replay(cfg, rdir, min_samples=10)
    f0 = [f for f in rep["folds"] if f["idx"] == 0][0]
    assert f0["excluded_purge_embargo"] > 0
    from tradingbot.replay.research import _fold_rows
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    rows = mem.trades(closed_only=True)
    for r in rows:
        o = r.get("outcome") or {}
        r["_open_ms"] = int(datetime.fromisoformat(o["opened_at"]).timestamp() * 1000)
        r["_close_ms"] = int(datetime.fromisoformat(o["closed_at"]).timestamp() * 1000)
    tr, te, ex = _fold_rows(rows, _bounds(0, 0, 30, 1, 1, 32, 47))
    ids_tr = {r["trade_id"] for r in tr}
    ids_te = {r["trade_id"] for r in te}
    assert "PURGE1" not in ids_tr and "PURGE1" not in ids_te
    assert "PURGE1" in {r["trade_id"] for r in ex}


def test_evaluate_blocks_overlapping_folds_and_missing_bounds(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "run_ov")
    _seed_replay_memory(rdir, n=90)
    # eski format (seed/hash var ama kesin `bounds` YOK) → fail-closed
    (rdir / "replay_result.json").write_text(json.dumps(
        {"seed": 7, "determinism_hash": "deadbeef",
         "windows": [{"idx": 0, "train": ["a", "b"], "test": ["c", "d"]}]}), encoding="utf-8")
    train_replay_challenger(cfg, rdir, seed=7)
    with pytest.raises(ReplaySafetyError, match="bounds"):
        evaluate_replay(cfg, rdir, min_samples=10)
    # örtüşen test pencereleri → çift sayım engeli
    _seed_replay_result(rdir, [_bounds(0, 0, 30, 1, 1, 32, 50), _bounds(1, 0, 45, 1, 1, 47, 62)])
    with pytest.raises(ReplaySafetyError, match="örtüşüyor"):
        evaluate_replay(cfg, rdir, min_samples=10)
    # test, embargo bitmeden başlıyor → sınır ihlali
    _seed_replay_result(rdir, [_bounds(0, 0, 30, 5, 5, 33, 47), _bounds(1, 0, 45, 1, 1, 47, 62)])
    with pytest.raises(ReplaySafetyError, match="sınırları geçersiz"):
        evaluate_replay(cfg, rdir, min_samples=10)
    # tek fold → min_folds karşılanmıyor
    _seed_replay_result(rdir, [_bounds(0, 0, 30, 1, 1, 32, 47)])
    with pytest.raises(ReplaySafetyError, match="fold"):
        evaluate_replay(cfg, rdir, min_samples=10)


def test_evaluate_fail_closed_paths(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "run_f")
    _seed_replay_memory(rdir, n=90)
    _seed_replay_result(rdir)
    with pytest.raises(ReplaySafetyError, match="manifest"):
        evaluate_replay(cfg, rdir)
    train_replay_challenger(cfg, rdir, seed=7)
    with pytest.raises(ReplaySafetyError, match="yetersiz örnek"):
        evaluate_replay(cfg, rdir, min_samples=999)
    from conftest import make_snapshot
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    rec = _rec(99, True)
    mem.record_entry({"trade_id": rec["id"], "symbol": rec["symbol"], "features": rec["features"],
                      "snapshot": make_snapshot(symbol=rec["symbol"], side="LONG",
                                                decision_ts_ms=int(NOW.timestamp() * 1000)),
                      "recorded_at": NOW.isoformat()})
    mem.record_exit(rec["id"], rec | {"recorded_at": NOW.isoformat()}, [], {})
    with pytest.raises(ReplaySafetyError, match="bayat"):
        evaluate_replay(cfg, rdir, min_samples=10)
    (rdir / TRAIN_MANIFEST).write_text("{bozuk", encoding="utf-8")
    with pytest.raises(ReplaySafetyError):
        evaluate_replay(cfg, rdir, min_samples=10)


def test_evaluate_blocks_champion_and_split_inconsistency(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "run_g")
    _seed_replay_memory(rdir, n=90)
    _seed_replay_result(rdir)
    man = train_replay_challenger(cfg, rdir, seed=7)
    bad = dict(man)
    bad["n_train"] = man["n_train"] + 5
    (rdir / TRAIN_MANIFEST).write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ReplaySafetyError, match="tutarsız"):
        evaluate_replay(cfg, rdir, min_samples=10)
    (rdir / TRAIN_MANIFEST).write_text(json.dumps(man), encoding="utf-8")
    reg = ModelRegistry(rdir / "models.json")
    for m in reg.models:
        m["status"] = "CHAMPION"
    reg.save()
    with pytest.raises(ReplaySafetyError, match="CHAMPION"):
        evaluate_replay(cfg, rdir, min_samples=10)


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
    _seed_replay_memory(rdir, n=90)
    _seed_replay_result(rdir)
    assert cmd_replay_train(cfg, _args(run_id="cli_run", seed=7)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["promotion"]["live_promotion"] is False
    assert cmd_replay_evaluate(cfg, _args(run_id="cli_run", min_samples=10)) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["samples"]["closed"] == 90 and rep["model_status"] == "CANDIDATE"
    assert rep["walk_forward"]["scored_folds"] >= 2
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


# --------------------------------------------------------------------------- 7) historical-replay izolasyonu
def test_historical_replay_cli_rejects_unsafe_run_ids(tmp_path, capsys):
    """`historical-replay` de aynı kanonik doğrulamadan geçer (replay-plan/train/evaluate ile aynı sözleşme)."""
    from tradingbot.cli_v3 import cmd_historical_replay
    cfg = _cfg(tmp_path)
    before = _live_state(cfg)
    for bad in ("../escape", "a/b", "/abs", ".", "..", ".hidden", "-flag"):
        args = SimpleNamespace(symbols=["ETH/USDT"], market="futures", tf="4h", seed=0, run_id=bad, state_dir=None,
                               from_=None, to=None, stride=1, train_days=180, test_days=30, purge=6, embargo=6,
                               no_patterns=True, min_sample=30, horizon=24)
        assert cmd_historical_replay(cfg, args) == 2, bad
        assert "BLOCK" in capsys.readouterr().out
    args = SimpleNamespace(symbols=["ETH/USDT"], market="futures", tf="4h", seed=0, run_id="ok_id",
                           state_dir=str(cfg.state_path), from_=None, to=None, stride=1, train_days=180,
                           test_days=30, purge=6, embargo=6, no_patterns=True, min_sample=30, horizon=24)
    assert cmd_historical_replay(cfg, args) == 2               # canlı state replay kökü olamaz
    assert assert_live_state_untouched(cfg.state_path) == before


def test_historical_replay_engine_defense_in_depth(tmp_path):
    """Motor, CLI atlansa bile kanonik kontrolü kendi uygular; state_dir tam olarak <root>/<run_id>."""
    from tradingbot.replay import HistoricalReplay
    cfg = _cfg(tmp_path)
    for bad in ("../evil", "", "sub/dir"):
        with pytest.raises(ReplaySafetyError):
            HistoricalReplay(cfg, run_id=bad, store=None, symbols=["ETH/USDT"])
    with pytest.raises(ReplaySafetyError):
        HistoricalReplay(cfg, run_id="x", store=None, symbols=["ETH/USDT"], state_root=cfg.state_path)
    rp = HistoricalReplay(cfg, run_id="safe_run", store=None, symbols=["ETH/USDT"])
    assert rp.state_dir == (cfg.state_path / "replay" / "safe_run").resolve()
    assert rp.state_dir.is_dir()
    assert not (cfg.state_path / "replay" / "safe_run" / "safe_run").exists()      # double-run-id yok


def test_historical_replay_cli_passes_resolved_root_without_double_run_id(tmp_path, monkeypatch):
    import tradingbot.cli_v3 as cli
    import tradingbot.replay as rmod
    cfg = _cfg(tmp_path)
    seen = {}

    class _Boom(Exception):
        pass

    class _FakeReplay:
        def __init__(self, cfg_, **kw):
            seen.update(kw)
            raise _Boom()

    monkeypatch.setattr(rmod, "HistoricalReplay", _FakeReplay, raising=False)
    args = SimpleNamespace(symbols=["ETH/USDT"], market="futures", tf="4h", seed=0, run_id="rid1", state_dir=None,
                           from_=None, to=None, stride=1, train_days=180, test_days=30, purge=6, embargo=6,
                           no_patterns=True, min_sample=30, horizon=24)
    with pytest.raises(_Boom):
        cli.cmd_historical_replay(cfg, args)
    assert seen["state_root"] == (cfg.state_path / "replay").resolve()
    assert seen["run_id"] == "rid1"


# --------------------------------------------------------------------------- 8) plan ↔ runner uyumu
def test_plan_blocks_when_estimate_exceeds_runner_memory_limit(tmp_path):
    cfg = _cfg(tmp_path)
    store = _store_4h(["BTC/USDT", "ETH/USDT"], rows=300_000)
    ok = plan_replay(cfg, store, run_id="r1", symbols=["BTC/USDT", "ETH/USDT"], stride=4, available_mb=6600)
    assert ok.ok, ok.blockers
    tight = plan_replay(cfg, store, run_id="r2", symbols=["BTC/USDT", "ETH/USDT"], stride=4, available_mb=6600,
                        runner_memory_max_mb=2048, runner_safe_pct=80)
    assert not tight.ok and any("runner sınırı" in b for b in tight.blockers)
    assert tight.runner_memory_max_mb == 2048 and tight.runner_budget_mb == 1638.4
    roomy = plan_replay(cfg, store, run_id="r3", symbols=["BTC/USDT", "ETH/USDT"], stride=4, available_mb=6600,
                        runner_memory_max_mb=8192, runner_safe_pct=80)
    assert roomy.ok


def test_plan_pattern_stride_parity_with_replay_stride(tmp_path):
    """historical-replay `--stride`'ı pattern index'te de kullanır → plan varsayılanı aynı stride."""
    cfg = _cfg(tmp_path)
    store = _store_4h(["BTC/USDT"], rows=40_000)
    p4 = plan_replay(cfg, store, run_id="s4", symbols=["BTC/USDT"], stride=4, available_mb=6600)
    assert p4.pattern_stride == 4 and p4.pattern_events == 40_000 // 4
    p1 = plan_replay(cfg, store, run_id="s1", symbols=["BTC/USDT"], stride=1, available_mb=6600)
    assert p1.pattern_stride == 1 and p1.pattern_events == 40_000
    assert p1.est_memory_mb > p4.est_memory_mb
    ovr = plan_replay(cfg, store, run_id="s5", symbols=["BTC/USDT"], stride=4, pattern_stride=2, available_mb=6600)
    assert ovr.pattern_stride == 2                                   # bilinçli override


def test_cli_plan_defaults_pattern_stride_to_stride(tmp_path, capsys, monkeypatch):
    import tradingbot.cli_v3 as cli
    import tradingbot.history as hist
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(hist, "HistoryStore", lambda *a, **k: _store_4h(["BTC/USDT"], rows=40_000), raising=False)
    assert cli.cmd_replay_plan(cfg, _args(run_id="cli_par", symbols=["BTC/USDT"], stride=4,
                                          pattern_stride=None, assume_available_mb=6600)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stride"] == 4 and out["pattern_stride"] == 4


# --------------------------------------------------------------------------- 10) semantik operasyonel doğrulama
def test_semantic_snapshot_tolerates_mtm_but_catches_plan_changes(tmp_path):
    """Worker çalışırken MTM/updated_at değişir (byte hash bozulur) — semantik değişmezler korunmalı."""
    from tradingbot.replay.research import compare_semantic, semantic_live_snapshot
    cfg = _cfg(tmp_path)
    st = cfg.state_path
    led = {"schema_version": 2, "wallet_balance": "47.21", "updated_at": "t0",
           "positions": {"BZ/USDT": {"id": "F00004", "side": "LONG", "entry_avg": "90.61", "qty": "0.165",
                                     "stop": "88.34", "targets": ["95.05"], "last_price": "90.1",
                                     "fills": [{"id": "F00004-e"}]}},
           "history": [], "entries": []}
    (st / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}), encoding="utf-8")
    before = semantic_live_snapshot(st)
    assert before["mode"] == "PAPER" and before["duplicate_fills"] is False
    # doğal PAPER faaliyeti: MTM + updated_at değişir → semantik OK
    led["positions"]["BZ/USDT"]["last_price"] = "91.4"
    led["updated_at"] = "t1"
    (st / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    assert compare_semantic(before, semantic_live_snapshot(st))["ok"] is True
    # stop değişirse ihlal
    led["positions"]["BZ/USDT"]["stop"] = "87.00"
    (st / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    r = compare_semantic(before, semantic_live_snapshot(st))
    assert not r["ok"] and any("stop" in d for d in r["diffs"])


def test_semantic_flags_mode_orders_and_duplicates(tmp_path):
    from tradingbot.replay.research import compare_semantic, semantic_live_snapshot
    cfg = _cfg(tmp_path)
    st = cfg.state_path
    base = {"schema_version": 2, "positions": {"X/USDT": {"id": "F1", "side": "LONG", "entry_avg": "1", "qty": "1",
                                                          "stop": "0.9", "targets": ["1.2"], "fills": [{"id": "F1-e"}]}},
            "history": [], "entries": []}
    (st / "futures_ledger.json").write_text(json.dumps(base), encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}), encoding="utf-8")
    before = semantic_live_snapshot(st)
    # LIVE'a geçiş + gerçek emir + duplicate fill → hepsi yakalanır
    (st / "mode.json").write_text(json.dumps({"mode": "LIVE", "live_order_path_enabled": True}), encoding="utf-8")
    bad = json.loads(json.dumps(base))
    bad["real_orders"] = 2
    bad["positions"]["X/USDT"]["fills"].append({"id": "F1-e"})
    (st / "futures_ledger.json").write_text(json.dumps(bad), encoding="utf-8")
    r = compare_semantic(before, semantic_live_snapshot(st))
    assert not r["ok"]
    joined = " ".join(r["diffs"])
    assert "PAPER değil" in joined and "live_order_path_enabled=true" in joined
    assert "gerçek emir" in joined and "duplicate fill" in joined
    # doğal kapanış ihlal DEĞİL (pozisyon history'e geçer)
    closed = {"schema_version": 2, "positions": {},
              "history": [{"id": "F1", "fills": [{"id": "F1-e"}, {"id": "F1-stop-1"}]}], "entries": []}
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}), encoding="utf-8")
    (st / "futures_ledger.json").write_text(json.dumps(closed), encoding="utf-8")
    assert compare_semantic(before, semantic_live_snapshot(st))["ok"] is True


def test_replay_pipeline_never_opens_live_state_files(tmp_path, monkeypatch):
    """Kanıt: train + evaluate sırasında canlı state dosyalarından HİÇBİRİ açılmaz (dosya erişimi izlenir)."""
    import builtins
    import io
    cfg = _cfg(tmp_path)
    _live_state(cfg)
    rdir = resolve_replay_dir(cfg.state_path, "no_touch")
    _seed_replay_memory(rdir, n=90)
    _seed_replay_result(rdir)
    live_dir = Path(cfg.state_path).resolve()
    opened: list[str] = []
    real_open = builtins.open
    real_path_open = Path.open

    def _record(path):
        try:
            p = Path(path).resolve()
        except (OSError, TypeError, ValueError):
            return
        if p.parent == live_dir:                      # canlı state klasöründeki dosyalar (replay alt dizini hariç)
            opened.append(p.name)

    def _open(file, *a, **k):
        _record(file)
        return real_open(file, *a, **k)

    def _popen(self, *a, **k):
        _record(self)
        return real_path_open(self, *a, **k)

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(Path, "open", _popen)
    train_replay_challenger(cfg, rdir, seed=7)
    evaluate_replay(cfg, rdir, min_samples=10)
    monkeypatch.undo()
    forbidden = {"futures_ledger.json", "spot_ledger.json", "trade_memory.jsonl", "learn_v2.json",
                 "models.json", "risk.json", "portfolio.json", "mode.json"}
    assert not (set(opened) & forbidden), sorted(set(opened) & forbidden)
    # artifact'ler yalnız state/replay/<run-id> altında
    produced = {p.name for p in rdir.iterdir()}
    assert {"train_manifest.json", "evaluation.json", "models.json", "learn_v2.json"} <= produced
    assert not (live_dir / "train_manifest.json").exists() and not (live_dir / "evaluation.json").exists()


# --------------------------------------------------------------------------- 11) tf-duyarlı bounds
@pytest.mark.parametrize("tf,bar_ms", [("15m", 900_000), ("1h", 3_600_000), ("4h", 14_400_000), ("1d", 86_400_000)])
def test_walk_forward_bounds_follow_timeframe(tf, bar_ms):
    """purge/embargo BAR cinsindendir: sınırlar `tf_ms(tf)` ile hesaplanır (sabit 4h varsayımı yok)."""
    from tradingbot.replay import walk_forward_windows
    ws = walk_forward_windows(0, 400 * 86_400_000, train_days=180, test_days=30, purge_bars=6, embargo_bars=6, tf=tf)
    assert ws
    b = ws[0].bounds()
    assert b["bar_ms"] == bar_ms
    assert b["purge_end_ms"] - b["purge_start_ms"] == 6 * bar_ms
    assert b["embargo_end_ms"] - b["embargo_start_ms"] == 6 * bar_ms
    assert b["test_start_ms"] == b["embargo_end_ms"] and b["train_end_ms"] == b["purge_start_ms"]


def test_walk_forward_requires_explicit_timeframe():
    from tradingbot.replay import walk_forward_windows
    with pytest.raises(ValueError, match="tf"):
        walk_forward_windows(0, 400 * 86_400_000, train_days=180, test_days=30)


def test_evaluate_rejects_bounds_inconsistent_with_timeframe(tmp_path):
    """Bounds içindeki purge/embargo genişliği bar_ms ile uyumsuzsa fail-closed."""
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "tf_bad")
    _seed_replay_memory(rdir, n=90)
    b0 = _bounds(0, 0, 30, 1, 1, 32, 47)
    b0["bar_ms"] = 3_600_000                      # 1h iddiası ama sınırlar gün cinsinden
    _seed_replay_result(rdir, [b0, _bounds(1, 0, 45, 1, 1, 47, 62)])
    train_replay_challenger(cfg, rdir, seed=7)
    with pytest.raises(ReplaySafetyError, match="bar_ms|tutars|geçersiz"):
        evaluate_replay(cfg, rdir, min_samples=10)


# --------------------------------------------------------------------------- 12) root/parent symlink
@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI"), reason="symlink Windows'ta yönetici ister")
def test_resolve_rejects_symlinked_replay_root_and_parent(tmp_path):
    live = tmp_path / "state"
    live.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (live / "replay").symlink_to(outside, target_is_directory=True)      # varsayılan kök symlink
    except (OSError, NotImplementedError):
        pytest.skip("symlink oluşturulamadı")
    with pytest.raises(ReplaySafetyError, match="symlink"):
        resolve_replay_dir(live, "run1")
    # özel kök: üst bileşeni symlink olan yol
    real = tmp_path / "real_root"
    (real / "deep").mkdir(parents=True)
    link_parent = tmp_path / "link_parent"
    try:
        link_parent.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink oluşturulamadı")
    with pytest.raises(ReplaySafetyError, match="symlink"):
        resolve_replay_dir(live, "run1", state_root=link_parent / "deep")


# --------------------------------------------------------------------------- 13) duplikasyon temizliği
def test_no_duplicate_helper_definitions_in_research_module():
    """Yinelenen yardımcı/fold fonksiyonu KALMAMALI (tek canonical implementasyon)."""
    import ast
    src = (REPO_ROOT / "tradingbot" / "replay" / "research.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"yinelenen tanımlar: {dupes}"
    for fn in ("_ms_or_none", "iso_ms", "_fold_rows", "_fit_fold", "_r_metrics", "evaluate_replay"):
        assert names.count(fn) == 1, fn


# --------------------------------------------------------------------------- 14) negatif OOS kapısı
def _negative_oos_dir(tmp_path, cfg, name="neg"):
    rdir = resolve_replay_dir(cfg.state_path, name)
    rdir.mkdir(parents=True, exist_ok=True)
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    from conftest import make_snapshot
    for i in range(90):
        won = i % 5 == 0                                   # çoğunluk zarar → negatif OOS
        opened = BASE + timedelta(days=i)
        sym = "ETH/USDT" if i % 2 else "SOL/USDT"
        side = "LONG" if i % 2 else "SHORT"
        rec = _rec(i, won) | {"opened_at": opened.isoformat(), "closed_at": (opened + timedelta(days=1)).isoformat(),
                              "r_multiple": 1.0 if won else -0.6, "symbol": sym, "side": side}
        snap = make_snapshot(symbol=sym, side=side, decision_ts_ms=int(opened.timestamp() * 1000), seed=3 + i % 5)
        mem.record_entry({"trade_id": rec["id"], "symbol": sym, "direction": side,
                          "setup_type": "kırılım", "regime": "TREND_UP", "features": rec["features"],
                          "snapshot": snap, "recorded_at": opened.isoformat()})
        mem.record_exit(rec["id"], rec | {"recorded_at": opened.isoformat()}, [], {})
    _seed_replay_result(rdir)
    return rdir


def test_negative_oos_never_yields_shadow_candidate(tmp_path):
    """Negatif expectancy / PF ≤ 1 / CI alt sınırı ≤ 0 → en fazla REJECTED; SHADOW_CANDIDATE ASLA."""
    cfg = _cfg(tmp_path)
    rdir = _negative_oos_dir(tmp_path, cfg)
    train_replay_challenger(cfg, rdir, seed=7)
    rep = evaluate_replay(cfg, rdir, min_samples=10)
    assert rep["out_of_sample"]["expectancy_r"] < 0
    assert rep["shadow_candidate"] is False
    assert rep["verdict"] == "REJECTED"
    assert rep["gates"]["positive_expectancy"] is False and rep["gates"]["profit_factor_above_one"] is False
    assert rep["failed_gates"] and rep["promotion"]["live_promotion"] is False
    assert "SHADOW" not in rep["verdict"] or rep["verdict"] == "SHADOW_CANDIDATE" and False


def test_point_in_time_and_survivorship_gates_block_promotion_claim(tmp_path):
    """PIT false / survivorship present iken (pozitif OOS olsa bile) SHADOW_CANDIDATE üretilemez."""
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pit")
    _seed_replay_memory(rdir, n=90)                        # bu fixture kazançlı (2R/-1R, 2/3 kazanç)
    _seed_replay_result(rdir)                              # point_in_time=False, survivorship present
    train_replay_challenger(cfg, rdir, seed=7)
    rep = evaluate_replay(cfg, rdir, min_samples=10)
    assert rep["gates"]["point_in_time"] is False and rep["gates"]["survivorship_clean"] is False
    assert rep["shadow_candidate"] is False
    assert rep["verdict"] in ("RESEARCH_ONLY", "REJECTED")
    assert "point_in_time" in rep["failed_gates"] and "survivorship_clean" in rep["failed_gates"]


def test_evaluate_never_calls_maybe_promote(tmp_path, monkeypatch):
    """Davranış kanıtı: hiçbir araştırma yolunda LearnerV2.maybe_promote çağrılmaz."""
    from tradingbot.learn import LearnerV2
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "nopromo")
    _seed_replay_memory(rdir, n=90)
    _seed_replay_result(rdir)
    calls = []
    monkeypatch.setattr(LearnerV2, "maybe_promote", lambda self, *a, **k: calls.append(a) or (True, []))
    train_replay_challenger(cfg, rdir, seed=7)
    rep = evaluate_replay(cfg, rdir, min_samples=10)
    assert calls == [] and rep["promotion"]["promote_called"] is False


# --------------------------------------------------------------------------- 15) kapsam raporu
def test_coverage_reports_symbols_sides_and_rejections(tmp_path):
    """BTC gibi STEP_ZERO_QTY yüzünden işlem üretemeyen sembol raporda AÇIKÇA görünür."""
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "cov")
    _seed_replay_memory(rdir, n=90)
    _seed_replay_result(rdir, symbols=["ETH/USDT", "BTC/USDT"],
                        rejections={"total": 41, "by_reason": {"STEP_ZERO_QTY": 41},
                                    "by_symbol": {"BTC/USDT": {"STEP_ZERO_QTY": 41}}})
    train_replay_challenger(cfg, rdir, seed=7)
    rep = evaluate_replay(cfg, rdir, min_samples=10)
    cov = rep["coverage"]
    assert "BTC/USDT" in cov["zero_trade_symbols"]
    assert any("BTC/USDT" in w and "STEP_ZERO_QTY" in w for w in cov["warnings"])
    assert cov["rejections"]["by_reason"]["STEP_ZERO_QTY"] == 41
    assert cov["rejections"]["by_symbol"]["BTC/USDT"]["STEP_ZERO_QTY"] == 41
    assert rep["gates"]["symbol_coverage"] is False           # sıfır-işlem sembolü kapıyı düşürür
    assert set(rep["oos_by_symbol"]) and set(rep["oos_by_side"])
    for m in rep["oos_by_symbol"].values():
        assert {"n", "expectancy_r", "profit_factor", "max_dd_r", "win_rate"} <= set(m)


# --------------------------------------------------------------------------- 16) CPU modeli + telemetri
def test_cpu_model_matches_core4_measurement(tmp_path):
    """Yeni model Core-4 ölçümüne (10.032 karar → 12 sa 50 dk CPU) makul yakın olmalı; eski 3 dk değil."""
    from tradingbot.replay.research import CPU_SECONDS_PER_DECISION, CPU_MODEL_PROVENANCE
    measured_hours = 46_200 / 3600.0
    est_hours = 10_032 * CPU_SECONDS_PER_DECISION / 3600.0
    assert 0.7 * measured_hours <= est_hours <= 1.3 * measured_hours
    assert "Core-4" in CPU_MODEL_PROVENANCE and "CPU-s/karar" in CPU_MODEL_PROVENANCE


def test_plan_reports_duration_risk_and_combined_class(tmp_path):
    cfg = _cfg(tmp_path)
    big = _store_4h(["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"], rows=10_000)
    plan = plan_replay(cfg, big, run_id="core4like", symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
                       stride=4, seed=7, available_mb=6600)
    d = plan.to_dict()
    assert d["est_cpu_minutes_low"] < d["est_cpu_minutes"] < d["est_cpu_minutes_high"]
    assert d["cpu_model"]["seconds_per_decision"] > 1.0 and d["cpu_model"]["provenance"]
    assert d["duration_risk"] in ("MEDIUM", "HIGH")            # saatler süren iş "LOW" görünmez
    assert d["risk_class"] == d["duration_risk"] or d["risk_class"] == d["memory_risk"]
    assert d["risk_class"] != "LOW"
    assert any("uzun koşu" in w for w in d["warnings"])


def test_telemetry_does_not_affect_determinism_hashes(tmp_path):
    """Ölçülen wall/CPU/bellek run_status'a yazılır ama determinism hash'lerini DEĞİŞTİRMEZ."""
    from tradingbot.replay.pipeline import read_run_status, run_pipeline
    cfg = _cfg(tmp_path)
    a = resolve_replay_dir(cfg.state_path, "tel_a")
    b = resolve_replay_dir(cfg.state_path, "tel_b")
    for d in (a, b):
        _seed_replay_memory(d, n=90)
        _seed_replay_result(d)
    (cfg.state_path / "mode.json").write_text(json.dumps({"mode": "PAPER"}), encoding="utf-8")
    assert run_pipeline(cfg, "tel_a", ["train", "evaluate"], min_samples=10) == 0
    assert run_pipeline(cfg, "tel_b", ["train", "evaluate"], min_samples=10) == 0
    ma = json.loads((a / TRAIN_MANIFEST).read_text(encoding="utf-8"))
    mb = json.loads((b / TRAIN_MANIFEST).read_text(encoding="utf-8"))
    assert ma["params_hash"] == mb["params_hash"] and ma["metrics_hash"] == mb["metrics_hash"]
    ea = json.loads((a / EVAL_REPORT).read_text(encoding="utf-8"))
    eb = json.loads((b / EVAL_REPORT).read_text(encoding="utf-8"))
    assert ea["determinism"] == eb["determinism"] and ea["out_of_sample"] == eb["out_of_sample"]
    ta, tb = read_run_status(a)["telemetry"], read_run_status(b)["telemetry"]
    assert "wall_seconds" in ta and "wall_seconds" in tb
    assert "telemetry" not in ma and "telemetry" not in ea      # hash girdilerine karışmaz
