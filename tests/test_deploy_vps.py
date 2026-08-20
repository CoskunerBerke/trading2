"""VPS deployment regresyonları — preflight karar matrisi (tiplenmiş, substring'siz), doctor'ın
makine-okunur heartbeat kodları, systemd unit sözleşmeleri, setup_vps_v3.sh sandbox testleri
(farklı cwd + boşluklu path + kısmi hata + idempotency; gerçek servis/apt/network YOK)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingbot.ops.preflight import decide

REPO_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
IS_LINUX = sys.platform.startswith("linux")


def _chk(name, ok, severity="fail", code="", detail=""):
    return {"name": name, "ok": ok, "severity": severity, "code": code, "detail": detail}


def _rep(checks, ok=None):
    if ok is None:
        ok = all(c["ok"] or c["severity"] != "fail" for c in checks)
    return {"ok": ok, "quick": True, "generated_at": "t", "checks": checks}


# ------------------------------------------------------------------ 1) preflight karar matrisi
def test_preflight_matrix_full_success_allows():
    ok, why = decide(_rep([_chk("config", True), _chk("heartbeat", True, code="HEARTBEAT_OK")]))
    assert ok and why.startswith("ALLOW")


def test_preflight_matrix_only_stale_heartbeat_allows_with_warning():
    ok, why = decide(_rep([_chk("config", True), _chk("heartbeat", False, code="HEARTBEAT_STALE", detail="1200s")]))
    assert ok and why.startswith("WARNING") and "HEARTBEAT_STALE" in why


def test_preflight_matrix_single_other_failure_blocks():
    ok, why = decide(_rep([_chk("state_writable", False, code=""), _chk("heartbeat", True, code="HEARTBEAT_OK")]))
    assert not ok and "state_writable" in why


def test_preflight_matrix_stale_plus_other_failure_blocks():
    ok, _ = decide(_rep([_chk("heartbeat", False, code="HEARTBEAT_STALE"), _chk("deps_required", False)]))
    assert not ok


def test_preflight_matrix_heartbeat_missing_unknown_malformed_block():
    for code in ("HEARTBEAT_MISSING", "HEARTBEAT_MALFORMED", "", "SOMETHING_ELSE"):
        ok, why = decide(_rep([_chk("heartbeat", False, code=code)]))
        assert not ok, code
        assert "BLOCK" in why


def test_preflight_matrix_invalid_or_missing_structured_result_blocks():
    for bad in (None, "text", 42, {}, {"ok": True}, {"ok": True, "checks": []},
                {"ok": True, "checks": "x"}, {"ok": True, "checks": [{"name": 1, "ok": True}]},
                {"ok": True, "checks": [{"name": "a", "ok": "yes"}]}):
        ok, why = decide(bad)
        assert not ok and "BLOCK" in why, bad
    # ok=false ama fail listesi boş → tutarsız → block
    ok, _ = decide(_rep([_chk("x", True)], ok=False))
    assert not ok


def test_preflight_matrix_human_text_mentions_stale_but_typed_check_differs_blocks():
    # detail metni "stale heartbeat" dese bile karar yalnız name+code'a bakar → block
    ok, _ = decide(_rep([_chk("state_json", False, code="", detail="stale heartbeat gibi görünen bozuk dosya")]))
    assert not ok
    ok, _ = decide(_rep([_chk("heartbeat", False, code="HEARTBEAT_MISSING", detail="stale heartbeat 9999s")]))
    assert not ok


def test_preflight_cli_doctor_crash_is_fail_closed(monkeypatch, tmp_path):
    import tradingbot.ops.doctor as doc
    from tradingbot.cli_v3 import cmd_preflight

    def boom(*a, **k):
        raise RuntimeError("çöktü")
    monkeypatch.setattr(doc, "run_doctor", boom)
    cfg = SimpleNamespace(state_path=tmp_path, cache_path=tmp_path, obsidian=SimpleNamespace(root=tmp_path), mode="PAPER")
    assert cmd_preflight(cfg, SimpleNamespace(quick=True)) == 1


# ------------------------------------------------------------------ 2) doctor typed heartbeat kodları
def test_doctor_heartbeat_codes_and_json_contract(tmp_path):
    from tradingbot.core import atomic_write_json, iso, utc_now
    from tradingbot.ops.doctor import run_doctor

    def hb_check(state_dir):
        rep = run_doctor({"mode": "PAPER"}, state_dir, quick=True)
        d = rep.to_dict()
        [hb] = [c for c in d["checks"] if c["name"] == "heartbeat"]
        return rep, hb

    # missing → ok(warn) + HEARTBEAT_MISSING; normal doctor davranışı DEĞİŞMEDİ (fail değil)
    rep, hb = hb_check(tmp_path)
    assert hb["ok"] and hb["code"] == "HEARTBEAT_MISSING"
    # malformed → ok(warn) + HEARTBEAT_MALFORMED
    (tmp_path / "heartbeat.json").write_text("{bozuk", encoding="utf-8")
    rep, hb = hb_check(tmp_path)
    assert hb["ok"] and hb["code"] == "HEARTBEAT_MALFORMED"
    # taze → HEARTBEAT_OK
    atomic_write_json(tmp_path / "heartbeat.json", {"at": iso(utc_now())})
    rep, hb = hb_check(tmp_path)
    assert hb["ok"] and hb["code"] == "HEARTBEAT_OK"
    # bayat → FAIL + HEARTBEAT_STALE; normal doctor'da hata OLMAYA DEVAM eder, preflight ise izin verir
    import datetime as dt
    atomic_write_json(tmp_path / "heartbeat.json", {"at": iso(utc_now() - dt.timedelta(hours=2))})
    rep, hb = hb_check(tmp_path)
    assert not hb["ok"] and hb["severity"] == "fail" and hb["code"] == "HEARTBEAT_STALE"
    if not rep.to_dict()["ok"]:                     # ortamda başka fail yoksa uçtan uca stale-only senaryosu
        fails = [c for c in rep.to_dict()["checks"] if not c["ok"] and c["severity"] == "fail"]
        if all(c["name"] == "heartbeat" for c in fails):
            allow, why = decide(rep.to_dict())
            assert allow and why.startswith("WARNING")


# ------------------------------------------------------------------ 3) unit dosya sözleşmeleri
def test_systemd_unit_contracts():
    worker = (REPO_ROOT / "deploy" / "tradingbot-worker.service").read_text(encoding="utf-8")
    dash = (REPO_ROOT / "deploy" / "tradingbot-dashboard.service").read_text(encoding="utf-8")
    setup = (REPO_ROOT / "deploy" / "setup_vps_v3.sh").read_text(encoding="utf-8")
    # worker: kaynak-kontrollü preflight + MPLCONFIGDIR (dar CacheDirectory; world-writable değil)
    assert "ExecStartPre=/opt/tradingbot/preflight.sh" in worker
    assert "CacheDirectory=tradingbot" in worker and "CacheDirectoryMode=0750" in worker
    assert "MPLCONFIGDIR=/var/cache/tradingbot/matplotlib" in worker
    assert "ProtectSystem=strict" in worker and "NoNewPrivileges=yes" in worker      # hardening korunuyor
    # dashboard: yalnız logs + state yazılabilir; data kökü read-only, app writable DEĞİL
    assert "ReadWritePaths=/opt/tradingbot/data/logs /opt/tradingbot/data/state" in dash
    assert "ReadOnlyPaths=/opt/tradingbot/data" in dash
    rw_lines = [ln for ln in dash.splitlines() if ln.startswith("ReadWritePaths=")]
    assert rw_lines and all("/opt/tradingbot/app" not in ln and ln != "ReadWritePaths=/opt/tradingbot" for ln in rw_lines)
    assert "ProtectSystem=strict" in dash
    # setup: preflight'i kurar, timer'ı enable --now + enabled/active doğrular, fail-fast
    assert 'install -m 0755 "$APP/deploy/preflight.sh" "$BASE/preflight.sh"' in setup
    assert "systemctl enable --now tradingbot-backup.timer" in setup
    assert "is-enabled tradingbot-backup.timer" in setup and "is-active tradingbot-backup.timer" in setup
    assert "set -Eeuo pipefail" in setup and "trap" in setup


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze yok (yalnız Linux/CI)")
def test_systemd_analyze_verify(tmp_path):
    """Unit sözdizimi doğrulaması: Exec yolları bu makinede bulunmadığından mevcut bir binary'ye map edilir;
    直 dizin yapısı değil, unit directive'lerinin geçerliliği test edilir."""
    true_bin = shutil.which("true") or "/bin/true"
    for u in ("tradingbot-worker.service", "tradingbot-dashboard.service", "tradingbot-backup.service", "tradingbot-backup.timer"):
        txt = (REPO_ROOT / "deploy" / u).read_text(encoding="utf-8")
        txt = txt.replace("/opt/tradingbot/preflight.sh", true_bin).replace("/opt/tradingbot/venv/bin/python", true_bin)
        txt = txt.replace("WorkingDirectory=/opt/tradingbot/app", f"WorkingDirectory={tmp_path}")
        (tmp_path / u).write_text(txt, encoding="utf-8")
    r = subprocess.run(["systemd-analyze", "verify", *(str(tmp_path / u) for u in
                        ("tradingbot-worker.service", "tradingbot-dashboard.service", "tradingbot-backup.service"))],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------ 4) setup sandbox (yalnız Linux; servis yok, mock systemctl)
STUBS = {
    "apt-get": "#!/usr/bin/env bash\nexit 0\n",
    "timedatectl": "#!/usr/bin/env bash\nexit 0\n",
    "useradd": "#!/usr/bin/env bash\nexit 0\n",
    "id": "#!/usr/bin/env bash\nexit 0\n",
    "chown": "#!/usr/bin/env bash\nexit 0\n",
    "ufw": "#!/usr/bin/env bash\nexit 0\n",
    "sudo": """#!/usr/bin/env bash
# test stub: `sudo -u USER env K=V... CMD...` → K=V export edip CMD'yi mevcut cwd'de çalıştırır
[ "$1" = "-u" ] && shift 2
if [ "$1" = "env" ]; then shift; while [[ "${1:-}" == *=* ]]; do export "$1"; shift; done; fi
exec "$@"
""",
    "systemctl": """#!/usr/bin/env bash
echo "systemctl $*" >> "$SYSTEMCTL_LOG"
if [ -n "${SYSTEMCTL_FAIL_MATCH:-}" ] && [[ "$*" == *"$SYSTEMCTL_FAIL_MATCH"* ]]; then exit 1; fi
case "$1" in
  is-enabled) echo "${SYSTEMCTL_ENABLED_SAYS:-enabled}";;
  is-active)  echo "${SYSTEMCTL_ACTIVE_SAYS:-active}";;
esac
exit 0
""",
}


def _sandbox(tmp_path: Path, name: str) -> dict:
    base = tmp_path / name / "tb base"          # boşluklu path → quoting regresyonu
    sd = tmp_path / name / "systemd"
    stub = tmp_path / name / "bin"
    for d in (base, sd, stub):
        d.mkdir(parents=True)
    for fname, body in STUBS.items():
        p = stub / fname
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    venv_bin = base / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    py = venv_bin / "python"
    py.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    py.chmod(0o755)
    pip = venv_bin / "pip"
    pip.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    pip.chmod(0o755)
    env = dict(os.environ)
    env.update({"TRADINGBOT_BASE": str(base), "TRADINGBOT_SYSTEMD_DIR": str(sd),
                "TRADINGBOT_SETUP_ALLOW_NON_ROOT": "1", "SYSTEMCTL_LOG": str(tmp_path / name / "systemctl.log"),
                "PATH": f"{stub}:{env['PATH']}"})
    return {"base": base, "sd": sd, "env": env, "log": tmp_path / name / "systemctl.log"}


def _run_setup(sb: dict, cwd: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(sb["env"])
    env.update(extra_env or {})
    branch = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "HEAD"
    return subprocess.run([BASH, str(REPO_ROOT / "deploy" / "setup_vps_v3.sh"), str(REPO_ROOT), branch],
                          cwd=str(cwd), env=env, capture_output=True, text=True, timeout=600)


needs_linux = pytest.mark.skipif(not (IS_LINUX and BASH), reason="setup sandbox testi Linux+bash ister (CI'da çalışır)")


@needs_linux
def test_setup_from_foreign_cwd_succeeds_and_is_idempotent(tmp_path):
    sb = _sandbox(tmp_path, "ok")
    foreign = tmp_path / "ok" / "foreign cwd"    # /root benzeri yabancı dizin
    foreign.mkdir()
    r1 = _run_setup(sb, foreign)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert "Kuruldu (bütün aşamalar tamamlandı)" in r1.stdout
    # unit'ler + preflight kuruldu
    for u in ("tradingbot-worker.service", "tradingbot-dashboard.service", "tradingbot-backup.service", "tradingbot-backup.timer"):
        assert (sb["sd"] / u).exists(), u
    pf = sb["base"] / "preflight.sh"
    assert pf.exists() and os.access(pf, os.X_OK)
    # authority claim yabancı cwd'den ÇALIŞTI (import hatası regresyonu) → marker state'te
    auth = sb["base"] / "data" / "state" / "worker_authority.json"
    assert auth.exists() and json.loads(auth.read_text(encoding="utf-8"))["host"]
    # timer enable --now + doğrulama systemctl'e gitti
    log = sb["log"].read_text(encoding="utf-8")
    assert "daemon-reload" in log and "enable --now tradingbot-backup.timer" in log
    assert "is-enabled tradingbot-backup.timer" in log and "is-active tradingbot-backup.timer" in log
    # idempotent ikinci koşu
    r2 = _run_setup(sb, foreign)
    assert r2.returncode == 0 and "Kuruldu (bütün aşamalar tamamlandı)" in r2.stdout


@needs_linux
def test_setup_daemon_reload_failure_is_fatal_and_honest(tmp_path):
    sb = _sandbox(tmp_path, "dr")
    r = _run_setup(sb, tmp_path / "dr", {"SYSTEMCTL_FAIL_MATCH": "daemon-reload"})
    assert r.returncode != 0
    assert "KURULUM BAŞARISIZ" in r.stderr and "systemd unit kurulumu" in r.stderr
    assert "Kuruldu (bütün aşamalar tamamlandı)" not in r.stdout


@needs_linux
def test_setup_inactive_timer_fails_without_success_message(tmp_path):
    sb = _sandbox(tmp_path, "ti")
    r = _run_setup(sb, tmp_path / "ti", {"SYSTEMCTL_ACTIVE_SAYS": "inactive"})
    assert r.returncode != 0
    assert "kurulum başarılı SAYILMAZ" in r.stdout + r.stderr
    assert "Kuruldu (bütün aşamalar tamamlandı)" not in r.stdout


# ------------------------------------------------------------------ 5) shell sözdizimi (her platformda bash varsa)
@pytest.mark.skipif(BASH is None, reason="bash yok")
def test_deploy_shell_syntax():
    for f in ("setup_vps_v3.sh", "preflight.sh", "backup.sh", "update.sh", "rollback.sh", "restore.sh"):
        p = REPO_ROOT / "deploy" / f
        if p.exists():
            r = subprocess.run([BASH, "-n", str(p)], capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, f"{f}: {r.stderr}"
