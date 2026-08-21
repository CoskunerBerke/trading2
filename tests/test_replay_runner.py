"""Replay runner + durable pipeline DAVRANIŞ testleri (string assertion değil).

Sandbox: sahte `systemd-run` / `systemctl` / `sudo` + gerçek Python; komutlar fiilen çalışır, çağrılar
kaydedilir. Böylece tek-unit pipeline, aşama sırası, fail-closed kapılar, concurrency kilidi, resume/force
sözleşmesi ve kalıcı durum makinesi gerçek davranışla doğrulanır. Hiçbir gerçek servis başlatılmaz.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingbot.config import BotConfig
from tradingbot.config_v3 import load_v3
from tradingbot.learn import TradeMemory
from tradingbot.replay.pipeline import (BLOCKED, FAILED, NOT_STARTED, RUNNING, RUN_STATUS, SUCCESS,
                                        read_run_status, run_pipeline, status_verdict, verify_existing_replay)
from tradingbot.replay.research import ReplaySafetyError, resolve_replay_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "deploy" / "replay_runner.sh"
BASH = shutil.which("bash")
IS_LINUX = sys.platform.startswith("linux")
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
BASE_T = NOW - timedelta(days=120)


# --------------------------------------------------------------------------- ortak fixture'lar
def _cfg(tmp_path: Path):
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    cfg.v3 = load_v3({"learning_v3": {"min_samples_train": 20, "holdout_frac": 0.25}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    cfg.cache_path.mkdir(parents=True, exist_ok=True)
    (cfg.state_path / "mode.json").write_text(json.dumps({"mode": "PAPER"}), encoding="utf-8")
    return cfg


def _rec(i, won):
    return {"id": f"R{i}", "symbol": "ETH/USDT" if i % 2 else "SOL/USDT", "side": "LONG" if i % 2 else "SHORT",
            "entry": 100.0, "exit_price": 102.0 if won else 99.0, "exit_reason": "hedef2" if won else "stop",
            "net_pnl": 2.0 if won else -1.0, "r_multiple": 2.0 if won else -1.0, "mae_pct": -0.5,
            "mfe_pct": 3.0 if won else 0.2, "bars_held": 4, "leverage": 2, "setup_type": "kırılım",
            "features": {"bias_trend": 0.6 if won else -0.6, "conf_trend": 0.7, "conviction": 0.6 if won else 0.3,
                         "rr": 2.5, "atr_pct": 0.3, "n_warnings": 1 if won else 5, "leverage": 2}}


def _seed_run(rdir: Path, n: int = 90, *, seed: int = 7, with_result: bool = True) -> None:
    rdir.mkdir(parents=True, exist_ok=True)
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    for i in range(n):
        opened = BASE_T + timedelta(days=i)
        rec = _rec(i, i % 3 != 0) | {"opened_at": opened.isoformat(), "closed_at": (opened + timedelta(days=1)).isoformat()}
        mem.record_entry({"trade_id": rec["id"], "symbol": rec["symbol"], "direction": rec["side"],
                          "setup_type": "kırılım", "regime": "TREND_UP", "features": rec["features"],
                          "recorded_at": opened.isoformat()})
        mem.record_exit(rec["id"], rec | {"recorded_at": opened.isoformat()}, [], {})
    if with_result:
        def _b(idx, tr_s, tr_e, ts_s, ts_e):
            ms = lambda d: int((BASE_T + timedelta(days=d)).timestamp() * 1000)  # noqa: E731
            return {"idx": idx, "train_start_ms": ms(tr_s), "train_end_ms": ms(tr_e),
                    "purge_start_ms": ms(tr_e), "purge_end_ms": ms(tr_e + 1),
                    "embargo_start_ms": ms(tr_e + 1), "embargo_end_ms": ms(tr_e + 2),
                    "test_start_ms": ms(ts_s), "test_end_ms": ms(ts_e),
                    "purge_bars": 1, "embargo_bars": 1, "bar_ms": 86_400_000}
        bounds = [_b(0, 0, 30, 32, 47), _b(1, 0, 45, 47, 62), _b(2, 0, 60, 62, 77)]
        (rdir / "replay_result.json").write_text(json.dumps({
            "run_id": rdir.name, "seed": seed, "determinism_hash": "deadbeef",
            "symbols": ["ETH/USDT", "SOL/USDT"], "n_actionable": 100, "n_opened": 90,
            "point_in_time": False, "survivorship_bias": {"present": True},
            "rejections": {"total": 5, "by_reason": {"STEP_ZERO_QTY": 5}, "by_symbol": {"BTC/USDT": {"STEP_ZERO_QTY": 5}}},
            "windows": [{"idx": b["idx"], "bounds": b} for b in bounds]}), encoding="utf-8")


# =========================================================================== A) pipeline durum makinesi
def test_pipeline_writes_durable_status_and_stops_after_failed_stage(tmp_path):
    """Aşama hatası sonraki aşamaları BAŞLATMAZ; manifest FAILED + NOT_STARTED ile kalıcı kalır."""
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pipe_fail")
    _seed_run(rdir, n=5)                                   # yetersiz örnek → train FAILED
    rc = run_pipeline(cfg, "pipe_fail", ["train", "evaluate"], unit="u1",
                      limits={"memory_max": "2G", "cpu_quota": "60%"})
    assert rc == 1
    st = read_run_status(rdir)
    assert st["state"] == FAILED and st["exit_code"] == 1
    assert st["stage_states"]["train"]["state"] == FAILED and st["stage_states"]["train"]["exit_code"] == 1
    assert st["stage_states"]["evaluate"]["state"] == NOT_STARTED       # sonraki aşama hiç başlamadı
    assert st["stage_states"]["train"]["error_code"] == "ReplaySafetyError"
    assert not (rdir / "evaluation.json").exists()
    assert st["resource_limits"] == {"memory_max": "2G", "cpu_quota": "60%"}
    assert "telemetry" in st and st["telemetry"]["wall_seconds"] >= 0
    dumped = json.dumps(st)
    for secret in ("ANTHROPIC", "API_KEY", "password", "token"):
        assert secret not in dumped


def test_pipeline_success_manifest_and_stage_order(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pipe_ok")
    _seed_run(rdir, n=90)
    rc = run_pipeline(cfg, "pipe_ok", ["train", "evaluate"], unit="u2")
    assert rc == 0
    st = read_run_status(rdir)
    assert st["state"] == SUCCESS and st["exit_code"] == 0 and st["current_stage"] is None
    assert [st["stage_states"][s]["state"] for s in ("train", "evaluate")] == [SUCCESS, SUCCESS]
    t_end = st["stage_states"]["train"]["finished_at"]
    e_start = st["stage_states"]["evaluate"]["started_at"]
    assert t_end <= e_start                                            # sıra korunmuş
    assert st["artifacts"]["train_manifest.json"]["exists"] and st["artifacts"]["evaluation.json"]["exists"]
    assert st["schema_version"] >= 1 and st["run_id"] == "pipe_ok" and st["unit"] == "u2"
    assert st["input_fingerprint"] and st["telemetry"]["cpu_seconds"] >= 0


def test_pipeline_blocks_when_mode_not_paper(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pipe_live")
    _seed_run(rdir, n=90)
    (cfg.state_path / "mode.json").write_text(json.dumps({"mode": "LIVE"}), encoding="utf-8")
    rc = run_pipeline(cfg, "pipe_live", ["train"])
    assert rc == 2                                                     # PAPER guard → BLOCKED
    st = read_run_status(rdir)
    assert st["state"] == BLOCKED and st["stage_states"]["train"]["state"] == BLOCKED
    assert st["stage_states"]["train"]["error_code"] == "PAPER_GUARD"
    assert not (rdir / "train_manifest.json").exists()


def test_status_verdict_never_reports_success_while_unit_active(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pipe_act")
    _seed_run(rdir, n=90)
    run_pipeline(cfg, "pipe_act", ["train"])
    assert status_verdict(rdir)["state"] == SUCCESS
    assert status_verdict(rdir, unit_active=True)["state"] == RUNNING      # aktif unit → SUCCESS değil


def test_status_verdict_not_started_and_durable_after_unit_collected(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pipe_ns")
    rdir.mkdir(parents=True, exist_ok=True)
    v = status_verdict(rdir)
    assert v["state"] == NOT_STARTED                                       # hiç unit yok, artifact yok
    _seed_run(rdir, n=90)
    run_pipeline(cfg, "pipe_ns", ["train", "evaluate"])
    # unit `--collect` ile silinmiş olsa bile (systemctl bilgisi yok) manifest gerçek sonucu verir
    v2 = status_verdict(rdir)
    assert v2["state"] == SUCCESS and v2["source"] == "manifest"
    assert v2["stage_states"]["evaluate"]["state"] == SUCCESS


def test_status_verdict_flags_manifest_artifact_inconsistency(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pipe_inc")
    _seed_run(rdir, n=90)
    run_pipeline(cfg, "pipe_inc", ["train"])
    (rdir / "train_manifest.json").unlink()                                # artifact kayboldu
    v = status_verdict(rdir)
    assert v["state"] == "INCONSISTENT" and v["problems"]


def test_status_verdict_blocks_when_artifacts_without_manifest(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "pipe_orphan")
    _seed_run(rdir, n=90)                                                  # artifact var, manifest yok
    v = status_verdict(rdir)
    assert v["state"] == BLOCKED and "doğrulanamıyor" in v["note"]


def test_pipeline_rejects_fake_resume_via_cli(tmp_path, capsys):
    from tradingbot.cli_v3 import cmd_replay_pipeline
    cfg = _cfg(tmp_path)
    args = SimpleNamespace(run_id="r_res", state_dir=None, stages="train", resume=True, symbols=None,
                           market="futures", tf="4h", seed=None, from_=None, to=None, stride=1,
                           train_days=180, test_days=30, purge=6, embargo=6, no_patterns=True,
                           min_sample=30, horizon=24, min_samples=None, unit="")
    assert cmd_replay_pipeline(cfg, args) == 2
    assert "UNSUPPORTED" in capsys.readouterr().out


def test_verify_existing_replay_does_not_modify_artifacts(tmp_path):
    cfg = _cfg(tmp_path)
    rdir = resolve_replay_dir(cfg.state_path, "verif")
    _seed_run(rdir, n=90)
    before = {p.name: p.read_bytes() for p in rdir.iterdir() if p.is_file()}
    info = verify_existing_replay(cfg, rdir, expect_seed=7)
    assert info["seed"] == 7 and info["seed_source"] == "replay_result" and info["windows"] == 3
    assert info["memory_rows"] == 180 and info["determinism_hash"] == "deadbeef"
    after = {p.name: p.read_bytes() for p in rdir.iterdir() if p.is_file()}
    assert after == before                                                 # hiçbir artifact değişmedi
    with pytest.raises(ReplaySafetyError):                                 # seed uyuşmazlığı
        verify_existing_replay(cfg, rdir, expect_seed=9)


# =========================================================================== B) runner sandbox (Linux)
STUBS = {
    "sudo": '''#!/usr/bin/env bash
[ "$1" = "-u" ] && shift 2
if [ "$1" = "env" ]; then shift; [ "$1" = "-i" ] && shift; while [[ "${1:-}" == *=* ]]; do export "$1"; shift; done; fi
exec "$@"
''',
    "systemctl": '''#!/usr/bin/env bash
echo "systemctl $*" >> "$SYSTEMCTL_LOG"
case "$1" in
  is-active) [ "${UNIT_ACTIVE:-0}" = "1" ] && exit 0 || exit 3 ;;
  show) echo "${SHOW_VALUE:-}" ;;
esac
exit 0
''',
    "systemd-run": '''#!/usr/bin/env bash
echo "systemd-run $*" >> "$SYSTEMDRUN_LOG"
[ "${SYSTEMD_RUN_FAIL:-0}" = "1" ] && exit 1
# properties'i atla, `--` sonrasını gerçekten çalıştır (pipeline fiilen koşsun)
while [ $# -gt 0 ]; do [ "$1" = "--" ] && { shift; break; }; shift; done
exec "$@"
''',
    "journalctl": '#!/usr/bin/env bash\nexit 0\n',
}


def _sandbox(tmp_path: Path, name: str, *, with_systemd_run: bool = True) -> dict:
    root = tmp_path / name
    base, stub = root / "tb base", root / "bin"
    (base / "venv" / "bin").mkdir(parents=True)
    stub.mkdir(parents=True)
    for fname, body in STUBS.items():
        if fname == "systemd-run" and not with_systemd_run:
            continue
        f = stub / fname
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)
    app = base / "app"
    app.mkdir(parents=True)
    py = base / "venv" / "bin" / "python"
    py.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    py.chmod(0o755)
    data = base / "data"
    (data / "state").mkdir(parents=True)
    env = dict(os.environ)
    env.update({"TRADINGBOT_BASE": str(base), "TRADINGBOT_APP": str(app), "TRADINGBOT_VENV_PY": str(py),
                "TRADINGBOT_DATA": str(data), "TRADINGBOT_STATE_DIR": str(data / "state"),
                "TRADINGBOT_SVC_USER": os.environ.get("USER", "runner"),
                "SYSTEMCTL_LOG": str(root / "systemctl.log"), "SYSTEMDRUN_LOG": str(root / "systemdrun.log"),
                "PATH": f"{stub}:{env['PATH']}", "PYTHONPATH": str(REPO_ROOT)})
    return {"root": root, "base": base, "app": app, "state": data / "state", "env": env,
            "srun_log": root / "systemdrun.log", "sctl_log": root / "systemctl.log"}


def _run(sb: dict, *argv: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(sb["env"])
    env.update(extra_env or {})
    return subprocess.run([BASH, str(RUNNER), *argv], cwd=str(sb["root"]), env=env,
                          capture_output=True, text=True, timeout=300)


needs_linux = pytest.mark.skipif(not (IS_LINUX and BASH), reason="runner sandbox Linux+bash ister (CI'da çalışır)")


@needs_linux
def test_runner_blocks_without_systemd_run(tmp_path):
    """systemd-run yoksa hiçbir iş başlamaz (sınırsız fallback yok)."""
    sb = _sandbox(tmp_path, "nosd", with_systemd_run=False)
    r = _run(sb, "train", "run1")
    assert r.returncode != 0 and "systemd-run yok" in r.stdout + r.stderr
    assert not sb["srun_log"].exists()


@needs_linux
def test_runner_resume_and_force_are_fail_closed(tmp_path):
    sb = _sandbox(tmp_path, "rf")
    rdir = sb["state"] / "replay" / "run1"
    rdir.mkdir(parents=True)
    (rdir / "replay_result.json").write_text('{"seed": 7, "windows": []}', encoding="utf-8")
    before = (rdir / "replay_result.json").read_bytes()
    r1 = _run(sb, "full", "run1", "--resume")
    assert r1.returncode == 2 and "UNSUPPORTED" in r1.stdout + r1.stderr
    r2 = _run(sb, "full", "run1", "--force")
    assert r2.returncode == 2 and "YENİ bir RUN_ID" in r2.stdout + r2.stderr
    assert (rdir / "replay_result.json").read_bytes() == before          # dosya silinmedi/bozulmadı
    assert not sb["srun_log"].exists()                                   # hiçbir unit başlatılmadı


@needs_linux
def test_runner_refuses_to_rerun_completed_replay(tmp_path):
    sb = _sandbox(tmp_path, "done")
    rdir = sb["state"] / "replay" / "run1"
    rdir.mkdir(parents=True)
    (rdir / "replay_result.json").write_text('{"seed": 7, "windows": []}', encoding="utf-8")
    r = _run(sb, "full", "run1")
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "zaten tamamlanmış" in out and "train" in out                 # train/evaluate yolunu önerir
    assert not sb["srun_log"].exists()


@needs_linux
def test_runner_concurrency_lock_blocks_second_pipeline(tmp_path):
    sb = _sandbox(tmp_path, "lock")
    r = _run(sb, "train", "run1", extra_env={"UNIT_ACTIVE": "1"})
    assert r.returncode != 0 and "zaten çalışıyor" in r.stdout + r.stderr
    assert not sb["srun_log"].exists()


@needs_linux
def test_runner_starts_exactly_one_pipeline_unit_with_all_stages(tmp_path, monkeypatch):
    """`full` TEK systemd-run çağrısıyla `replay-pipeline --stages replay,train,evaluate` başlatır
    (aşamaları parent shell yönetmez → SSH kopsa da devam eder) ve `--wait` KULLANMAZ."""
    sb = _sandbox(tmp_path, "one")
    # plan/mode aşamalarını geçmek için sahte tradingbot modülü: gerçek CLI ağ/veri ister
    fake = sb["app"] / "tradingbot"
    fake.mkdir()
    (fake / "__init__.py").write_text("", encoding="utf-8")
    (fake / "__main__.py").write_text(
        'import json, sys\n'
        'a = sys.argv[1:]\n'
        'if a and a[0] == "mode-status":\n'
        '    print(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}))\n'
        'elif a and a[0] == "replay-plan":\n'
        '    print(json.dumps({"ok": True, "risk_class": "LOW"}))\n'
        'else:\n'
        '    print(json.dumps({"cmd": a}))\n',
        encoding="utf-8")
    r = _run(sb, "full", "run1", extra_env={"PYTHONPATH": str(sb["app"])})
    assert r.returncode == 0, r.stdout + r.stderr
    log = sb["srun_log"].read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 1, log                                            # TEK unit
    line = log[0]
    assert "replay-pipeline" in line and "--stages replay,train,evaluate" in line
    assert "--service-type=exec" in line and "--wait" not in line        # bekleyip shell'e bağlanmaz
    assert "--scope" not in line
    for prop in ("MemoryMax=", "CPUQuota=", "Nice=", "IOWeight="):
        assert prop in line
    assert "REPLAY_PIPELINE_STARTED" in r.stdout and "status run1" in r.stdout


@needs_linux
def test_runner_train_only_flow_verifies_existing_replay_first(tmp_path):
    """Tamamlanmış replay üzerinde `train` yalnız train aşamasını başlatır; önce artifact doğrular."""
    sb = _sandbox(tmp_path, "trainonly")
    fake = sb["app"] / "tradingbot"
    fake.mkdir()
    (fake / "__init__.py").write_text("", encoding="utf-8")
    (fake / "__main__.py").write_text(
        'import json, sys\n'
        'a = sys.argv[1:]\n'
        'open("%s", "a").write(" ".join(a) + "\\n")\n'
        'if a and a[0] == "mode-status":\n'
        '    print(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}))\n'
        'elif a and a[0] == "replay-plan":\n'
        '    print(json.dumps({"ok": True}))\n'
        'elif a and a[0] == "replay-verify":\n'
        '    print(json.dumps({"seed": 7}))\n'
        'else:\n'
        '    print(json.dumps({"cmd": a}))\n' % (sb["root"] / "calls.log").as_posix(),
        encoding="utf-8")
    rdir = sb["state"] / "replay" / "run1"
    rdir.mkdir(parents=True)
    (rdir / "replay_result.json").write_text('{"seed": 7, "windows": []}', encoding="utf-8")
    r = _run(sb, "train", "run1", extra_env={"PYTHONPATH": str(sb["app"])})
    assert r.returncode == 0, r.stdout + r.stderr
    calls = (sb["root"] / "calls.log").read_text(encoding="utf-8")
    assert "replay-verify --run-id run1" in calls                        # önce doğrulama
    line = sb["srun_log"].read_text(encoding="utf-8").strip()
    assert "--stages train" in line and "replay,train" not in line       # yalnız train aşaması
    assert (rdir / "replay_result.json").read_text(encoding="utf-8") == '{"seed": 7, "windows": []}'


@needs_linux
def test_runner_does_not_touch_worker_or_dashboard(tmp_path):
    sb = _sandbox(tmp_path, "svc")
    fake = sb["app"] / "tradingbot"
    fake.mkdir()
    (fake / "__init__.py").write_text("", encoding="utf-8")
    (fake / "__main__.py").write_text(
        'import json, sys\n'
        'a = sys.argv[1:]\n'
        'print(json.dumps({"mode": "PAPER", "live_order_path_enabled": False} if a[:1] == ["mode-status"] else {"ok": True}))\n',
        encoding="utf-8")
    _run(sb, "full", "run1", extra_env={"PYTHONPATH": str(sb["app"])})
    sctl = sb["sctl_log"].read_text(encoding="utf-8") if sb["sctl_log"].exists() else ""
    for forbidden in ("stop tradingbot-worker", "restart tradingbot-worker", "stop tradingbot-dashboard",
                      "restart tradingbot-dashboard"):
        assert forbidden not in sctl, forbidden


@needs_linux
def test_runner_blocks_when_mode_is_not_paper(tmp_path):
    sb = _sandbox(tmp_path, "live")
    fake = sb["app"] / "tradingbot"
    fake.mkdir()
    (fake / "__init__.py").write_text("", encoding="utf-8")
    (fake / "__main__.py").write_text(
        'import json, sys\nprint(json.dumps({"mode": "LIVE", "live_order_path_enabled": True}))\n', encoding="utf-8")
    r = _run(sb, "full", "run1", extra_env={"PYTHONPATH": str(sb["app"])})
    assert r.returncode != 0 and "BLOCK" in r.stdout + r.stderr
    assert not sb["srun_log"].exists()


@needs_linux
def test_runner_passes_no_secrets_and_uses_clean_env(tmp_path):
    """`env -i` ile temiz ortam: sızdırılan secret systemd-run satırına ya da çıktıya girmez."""
    sb = _sandbox(tmp_path, "secret")
    fake = sb["app"] / "tradingbot"
    fake.mkdir()
    (fake / "__init__.py").write_text("", encoding="utf-8")
    (fake / "__main__.py").write_text(
        'import json, os, sys\n'
        'a = sys.argv[1:]\n'
        'print(json.dumps({"mode": "PAPER", "live_order_path_enabled": False} if a[:1] == ["mode-status"]\n'
        '                 else {"ok": True, "leaked": "ANTHROPIC_API_KEY" in os.environ}))\n', encoding="utf-8")
    r = _run(sb, "full", "run1", extra_env={"PYTHONPATH": str(sb["app"]), "ANTHROPIC_API_KEY": "sk-test-SECRET"})
    combined = r.stdout + r.stderr + (sb["srun_log"].read_text(encoding="utf-8") if sb["srun_log"].exists() else "")
    assert "sk-test-SECRET" not in combined
    assert '"leaked": false' in r.stdout.replace("False", "false").lower() or '"leaked": false' in combined.lower()
