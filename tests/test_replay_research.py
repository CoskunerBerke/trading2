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
    rdir.mkdir(parents=True, exist_ok=True)
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    for i in range(n):
        rec = _rec(i, i % 3 != 0)
        opened = BASE + timedelta(days=i)
        closed = opened + timedelta(days=hold_days)
        rec = rec | {"opened_at": opened.isoformat(), "closed_at": closed.isoformat()}
        mem.record_entry({"trade_id": rec["id"], "symbol": rec["symbol"], "direction": "LONG", "setup_type": "kırılım",
                          "regime": "TREND_UP", "features": rec["features"], "recorded_at": opened.isoformat()})
        mem.record_exit(rec["id"], rec | {"recorded_at": opened.isoformat()}, [], {"lesson_text_tr": ["ders"]})
    for e in extra or []:
        opened, closed = e["opened"], e["closed"]
        rec = _rec(e.get("idx", 900), e.get("won", True)) | {"id": e["id"], "opened_at": opened.isoformat(), "closed_at": closed.isoformat()}
        mem.record_entry({"trade_id": e["id"], "symbol": rec["symbol"], "direction": "LONG", "setup_type": "kırılım",
                          "regime": "TREND_UP", "features": rec["features"], "recorded_at": opened.isoformat()})
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


def _seed_replay_result(rdir: Path, bounds: list[dict] | None = None) -> list[dict]:
    """Gerçek `historical-replay` çıktısının şeması: pencerelerin KESİN sınırlarıyla (bounds)."""
    b = bounds if bounds is not None else [
        _bounds(0, 0, 30, 1, 1, 32, 47),
        _bounds(1, 0, 45, 1, 1, 47, 62),
        _bounds(2, 0, 60, 1, 1, 62, 77),
    ]
    (rdir / "replay_result.json").write_text(json.dumps({
        "run_id": rdir.name, "seed": 7, "determinism_hash": "deadbeef",
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
    assert set(rep["gates"]) == {"enough_oos", "positive_expectancy", "ci95_lower_above_zero",
                                 "calibration_ok", "fold_consistency", "enough_folds"}
    assert rep["shadow_candidate"] == all(rep["gates"].values())
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
    # eski format (bounds yok) → fail-closed
    (rdir / "replay_result.json").write_text(json.dumps({"windows": [{"idx": 0, "train": ["a", "b"], "test": ["c", "d"]}]}), encoding="utf-8")
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
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    rec = _rec(99, True)
    mem.record_entry({"trade_id": rec["id"], "symbol": rec["symbol"], "features": rec["features"], "recorded_at": NOW.isoformat()})
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


# --------------------------------------------------------------------------- 9) runner sözleşmeleri
RUNNER = (REPO_ROOT / "deploy" / "replay_runner.sh").read_text(encoding="utf-8")


def test_runner_actions_and_full_ordering():
    """Eylemler plan|replay|train|evaluate|full|status; `full` = plan → historical-replay → train → evaluate."""
    assert "plan|replay|train|evaluate|full|status" in RUNNER
    i_plan = RUNNER.index("replay-plan --run-id")
    i_replay = RUNNER.index("tb_unit replay historical-replay")
    i_train = RUNNER.index("tb_unit train replay-train")
    i_eval = RUNNER.index("tb_unit evaluate replay-evaluate")
    assert i_plan < i_replay < i_train < i_eval
    for act in ('== "replay" || "$ACTION" == "full"', '== "train" || "$ACTION" == "full"',
                '== "evaluate" || "$ACTION" == "full"'):
        assert act in RUNNER


def test_runner_heaviest_job_inside_cgroup_and_no_unlimited_fallback():
    """historical-replay cgroup DIŞINDA çalışamaz; systemd-run yoksa BLOCK (sınırsız fallback yok)."""
    assert "tb_unit replay historical-replay" in RUNNER
    assert "tb historical-replay" not in RUNNER
    assert "BLOCK: systemd-run yok" in RUNNER
    assert "nice -n" not in RUNNER and "ionice" not in RUNNER
    for prop in ("MemoryMax=$REPLAY_MEM_MAX", "CPUQuota=$REPLAY_CPU_QUOTA", "Nice=$REPLAY_NICE", "IOWeight=$REPLAY_IO_WEIGHT"):
        assert '--property="' + prop + '"' in RUNNER


def test_runner_transient_service_survives_ssh_and_reports_unit():
    """`--scope` değil transient SERVICE (SSH kopsa da sürer); unit adı ve takip komutu raporlanır."""
    assert "--service-type=exec" in RUNNER and "--scope" not in RUNNER
    assert '--unit="$unit"' in RUNNER
    assert "journalctl -u $unit.service -f" in RUNNER


def test_runner_concurrency_and_resume_force_contract():
    assert 'systemctl is-active --quiet "$unit.service"' in RUNNER
    assert "aynı run-id ikinci kez başlatılamaz" in RUNNER
    assert "--resume" in RUNNER and "--force" in RUNNER and "replay_result.json var" in RUNNER


def test_runner_status_action_reports_units_and_artifacts():
    assert 'if [[ "$ACTION" == "status" ]]' in RUNNER
    for k in ("ActiveState", "Result", "ExecMainStatus", "journalctl -u"):
        assert k in RUNNER
    for f in ("replay_result.json", "train_manifest.json", "evaluation.json"):
        assert f in RUNNER


def test_runner_validates_inputs_against_injection():
    assert "[A-Za-z0-9._-]" in RUNNER and "geçersiz RUN_ID" in RUNNER
    assert "geçersiz REPLAY_MEM_MAX" in RUNNER and "geçersiz REPLAY_CPU_QUOTA" in RUNNER
    assert "--runner-memory-max-mb" in RUNNER and "--runner-safe-pct" in RUNNER


def test_runner_does_not_stop_services_or_read_secrets():
    for forbidden in ("systemctl stop", "systemctl restart", "EnvironmentFile", "ANTHROPIC", "API_KEY"):
        assert forbidden not in RUNNER, forbidden
    assert "env -i" in RUNNER and "set -Eeuo pipefail" in RUNNER


def test_runner_propagates_failure_not_success():
    """`--wait` ile sonuç beklenir; OOM/non-zero `set -e` ile yayılır, REPLAY_RUNNER_OK basılmaz."""
    assert "--wait" in RUNNER
    assert RUNNER.index("tb_unit evaluate replay-evaluate") < RUNNER.index("REPLAY_RUNNER_OK")
    body = RUNNER.split("tb_unit() {")[1].split("\n}")[0]
    assert "|| true" not in body.replace("reset-failed \"$unit.service\" >/dev/null 2>&1 || true", "")


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
