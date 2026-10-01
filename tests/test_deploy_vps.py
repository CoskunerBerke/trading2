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


def test_preflight_real_world_gap_missing_malformed_block_even_when_doctor_ok():
    """GERÇEK run_doctor sözleşmesi: missing/malformed heartbeat ok=true+warn üretir ve report.ok=true olur —
    preflight yine de BLOCK etmelidir (eksik/bozuk heartbeat 'stale' sayılmaz)."""
    for code in ("HEARTBEAT_MISSING", "HEARTBEAT_MALFORMED"):
        rep = _rep([_chk("config", True), _chk("heartbeat", True, severity="warn", code=code)])
        assert rep["ok"] is True
        ok, why = decide(rep)
        assert not ok and "BLOCK" in why, code


def test_preflight_heartbeat_cardinality_and_consistency():
    # heartbeat check yok → block
    ok, why = decide(_rep([_chk("config", True)]))
    assert not ok and "heartbeat check sayısı 0" in why
    # birden fazla heartbeat check → block
    ok, why = decide(_rep([_chk("heartbeat", True, code="HEARTBEAT_OK"), _chk("heartbeat", True, code="HEARTBEAT_OK")]))
    assert not ok and "heartbeat check sayısı 2" in why
    # report.ok=true ama fail var → tutarsız → block
    ok, why = decide(_rep([_chk("state_writable", False), _chk("heartbeat", True, code="HEARTBEAT_OK")], ok=True))
    assert not ok and "tutarsız" in why
    # code=OK ama ok=false (uyumsuz heartbeat kaydı) → block
    ok, _ = decide(_rep([_chk("heartbeat", False, code="HEARTBEAT_OK")]))
    assert not ok


def test_preflight_cli_doctor_crash_is_fail_closed(monkeypatch, tmp_path):
    import tradingbot.ops.doctor as doc
    from tradingbot.cli_v3 import cmd_preflight

    def boom(*a, **k):
        raise RuntimeError("çöktü")
    monkeypatch.setattr(doc, "run_doctor", boom)
    cfg = SimpleNamespace(state_path=tmp_path, cache_path=tmp_path, obsidian=SimpleNamespace(root=tmp_path), mode="PAPER")
    assert cmd_preflight(cfg, SimpleNamespace(quick=True)) == 1


def _pf_cfg(tmp_path):
    return SimpleNamespace(state_path=tmp_path, cache_path=tmp_path / "cache", obsidian=SimpleNamespace(root=tmp_path / "vault"), mode="PAPER")


def _real_preflight_rc(tmp_path, capsys=None):
    from tradingbot.cli_v3 import cmd_preflight
    rc = cmd_preflight(_pf_cfg(tmp_path), SimpleNamespace(quick=True))
    out = capsys.readouterr().out if capsys is not None else ""
    return rc, out


def test_e2e_preflight_missing_heartbeat_blocks(tmp_path, capsys):
    rc, out = _real_preflight_rc(tmp_path, capsys)
    assert rc == 1 and "BLOCK" in out


def test_e2e_preflight_malformed_heartbeat_blocks(tmp_path, capsys):
    (tmp_path / "heartbeat.json").write_text("{bozuk json", encoding="utf-8")
    rc, out = _real_preflight_rc(tmp_path, capsys)
    assert rc == 1 and "BLOCK" in out


def test_e2e_preflight_fresh_heartbeat_allows(tmp_path, capsys):
    from tradingbot.core import atomic_write_json, iso, utc_now
    atomic_write_json(tmp_path / "heartbeat.json", {"at": iso(utc_now())})
    rc, out = _real_preflight_rc(tmp_path, capsys)
    assert rc == 0 and "ALLOW" in out


def test_e2e_preflight_stale_only_allows_with_warning(tmp_path, capsys):
    import datetime as dt
    from tradingbot.core import atomic_write_json, iso, utc_now
    atomic_write_json(tmp_path / "heartbeat.json", {"at": iso(utc_now() - dt.timedelta(hours=2))})
    rc, out = _real_preflight_rc(tmp_path, capsys)
    assert rc == 0 and out.startswith("WARNING") and "HEARTBEAT_STALE" in out


def test_e2e_preflight_stale_plus_other_failure_blocks(tmp_path, capsys):
    import datetime as dt
    from tradingbot.core import atomic_write_json, iso, utc_now
    atomic_write_json(tmp_path / "heartbeat.json", {"at": iso(utc_now() - dt.timedelta(hours=2))})
    (tmp_path / "futures_ledger.json").write_text('{"schema_version": "x"}', encoding="utf-8")   # state_json FAIL
    rc, out = _real_preflight_rc(tmp_path, capsys)
    assert rc == 1 and "BLOCK" in out and "state_json" in out


def test_e2e_preflight_matches_synthetic_decide_matrix(tmp_path):
    """Sentetik decide() matrisi ile gerçek run_doctor→cmd_preflight akışı AYNI kararı vermeli."""
    import datetime as dt
    from tradingbot.cli_v3 import cmd_preflight
    from tradingbot.core import atomic_write_json, iso, utc_now
    from tradingbot.ops.doctor import run_doctor

    def scenario(setup_fn, name):
        st = tmp_path / name
        st.mkdir()
        setup_fn(st)
        cfg = SimpleNamespace(state_path=st, cache_path=st / "cache", obsidian=SimpleNamespace(root=st / "vault"), mode="PAPER")
        rep = run_doctor(cfg, st, st / "cache", st / "vault", quick=True).to_dict()
        allow, _ = decide(rep)
        rc = cmd_preflight(cfg, SimpleNamespace(quick=True))
        assert (rc == 0) == allow, f"{name}: decide={allow} cmd_preflight rc={rc}"
        return allow

    assert scenario(lambda st: None, "missing") is False
    assert scenario(lambda st: (st / "heartbeat.json").write_text("{bozuk", encoding="utf-8"), "malformed") is False
    assert scenario(lambda st: atomic_write_json(st / "heartbeat.json", {"at": iso(utc_now())}), "fresh") is True
    assert scenario(lambda st: atomic_write_json(st / "heartbeat.json", {"at": iso(utc_now() - dt.timedelta(hours=2))}), "stale") is True


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
    # bellek sınırı: tam Tier A pattern index ≈3.1 GB ölçüldü → 4G; eski 1500M sınırı OOM-kill üretiyordu
    mem_lines = [ln for ln in worker.splitlines() if ln.startswith("MemoryMax=")]
    assert mem_lines == ["MemoryMax=4G"], mem_lines
    assert "1500M" not in worker
    assert "CPUQuota=150%" in worker and "Restart=on-failure" in worker              # diğer kaynak/restart ayarları aynı
    # dashboard: yalnız logs + state yazılabilir; data kökü read-only, app writable DEĞİL
    assert "ReadWritePaths=/opt/tradingbot/data/logs /opt/tradingbot/data/state" in dash
    assert "ReadOnlyPaths=/opt/tradingbot/data" in dash
    rw_lines = [ln for ln in dash.splitlines() if ln.startswith("ReadWritePaths=")]
    assert rw_lines and all("/opt/tradingbot/app" not in ln and ln != "ReadWritePaths=/opt/tradingbot" for ln in rw_lines)
    assert "ProtectSystem=strict" in dash
    # setup: ATOMİK preflight kurulumu (mktemp + doğrulama + mv), timer enable --now + enabled/active, fail-fast
    assert 'PF_TMP="$(mktemp "$BASE/.preflight.sh.XXXXXX")"' in setup
    assert 'mv -f "$PF_TMP" "$BASE/preflight.sh"' in setup and 'install -m 0755 "$APP/deploy/preflight.sh"' not in setup
    assert "systemctl enable --now tradingbot-backup.timer" in setup
    assert "is-enabled tradingbot-backup.timer" in setup and "is-active tradingbot-backup.timer" in setup
    assert "set -Eeuo pipefail" in setup and "trap" in setup
    # branch güvenliği: sessiz main yok; detached HEAD açık hata; git işlemleri service user ile
    assert 'BRANCH="${2:-}"' in setup and '"${2:-main}"' not in setup
    assert "detached HEAD" in setup and 'TARGET_BRANCH="${BRANCH:-$CUR_BRANCH}"' in setup
    assert 'git_svc() { sudo -u "$SVC_USER" git "$@"; }' in setup
    assert "rollback paketi" in setup and 'cp -p "$BASE/preflight.sh" "$ROLLBACK_DIR/preflight.sh"' in setup


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
echo "sudo $*" >> "${SUDO_LOG:-/dev/null}"
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
                "SUDO_LOG": str(tmp_path / name / "sudo.log"), "PATH": f"{stub}:{env['PATH']}"})
    return {"base": base, "sd": sd, "env": env, "log": tmp_path / name / "systemctl.log",
            "sudo_log": tmp_path / name / "sudo.log"}


def _repo_branch() -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True).stdout.strip() or "HEAD"


def _run_setup(sb: dict, cwd: Path, extra_env: dict | None = None, args: list[str] | None = None) -> subprocess.CompletedProcess:
    env = dict(sb["env"])
    env.update(extra_env or {})
    if args is None:
        args = [str(REPO_ROOT), _repo_branch()]
    return subprocess.run([BASH, str(REPO_ROOT / "deploy" / "setup_vps_v3.sh"), *args],
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
    # unit'ler + preflight kuruldu; kurulan içerik repo'daki dosyayla birebir (MemoryMax=4G dahil)
    for u in ("tradingbot-worker.service", "tradingbot-dashboard.service", "tradingbot-backup.service", "tradingbot-backup.timer"):
        assert (sb["sd"] / u).exists(), u
        assert (sb["sd"] / u).read_text(encoding="utf-8") == (REPO_ROOT / "deploy" / u).read_text(encoding="utf-8"), u
    assert "MemoryMax=4G" in (sb["sd"] / "tradingbot-worker.service").read_text(encoding="utf-8")
    pf = sb["base"] / "preflight.sh"
    assert pf.exists() and os.access(pf, os.X_OK)
    # authority claim yabancı cwd'den ÇALIŞTI (import hatası regresyonu) → marker state'te
    auth = sb["base"] / "data" / "state" / "worker_authority.json"
    assert auth.exists() and json.loads(auth.read_text(encoding="utf-8"))["host"]
    # timer enable --now + doğrulama systemctl'e gitti
    log = sb["log"].read_text(encoding="utf-8")
    assert "daemon-reload" in log and "enable --now tradingbot-backup.timer" in log
    assert "is-enabled tradingbot-backup.timer" in log and "is-active tradingbot-backup.timer" in log
    # git işlemleri service user yolundan (sudo -u ... git) geçti
    slog = sb["sudo_log"].read_text(encoding="utf-8")
    assert "git clone" in slog
    # atomik preflight: yarım/geçici dosya kalmadı
    assert not list(sb["base"].glob(".preflight.sh.*"))
    branch = _repo_branch()
    # ARGÜMANSIZ ikinci koşu: idempotent VE mevcut feature branch'te KALIR (sessizce main'e geçmez)
    r2 = _run_setup(sb, foreign, args=[])
    assert r2.returncode == 0 and "Kuruldu (bütün aşamalar tamamlandı)" in r2.stdout, r2.stdout + r2.stderr
    cur = subprocess.run(["git", "-C", str(sb["base"] / "app"), "symbolic-ref", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    assert cur == branch, f"branch değişti: {cur} != {branch}"
    slog2 = sb["sudo_log"].read_text(encoding="utf-8")
    assert "git -C" in slog2 and "fetch" in slog2 and "pull --ff-only" in slog2
    # rollback paketi ikinci koşuda mevcut preflight'i sakladı
    rb = list((sb["base"] / "rollback").glob("units-*/preflight.sh"))
    assert rb, "rollback paketi preflight kopyası yok"
    # EXPLICIT branch argümanıyla üçüncü koşu: aynı branch'e yalnız fast-forward, yine idempotent
    r3 = _run_setup(sb, foreign, args=[str(REPO_ROOT), branch])
    assert r3.returncode == 0 and "Kuruldu (bütün aşamalar tamamlandı)" in r3.stdout
    cur3 = subprocess.run(["git", "-C", str(sb["base"] / "app"), "symbolic-ref", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert cur3 == branch
    assert not list(sb["base"].glob(".preflight.sh.*"))
    # idempotent kurulum: unit içeriği tekrarlı koşulardan sonra da repo ile birebir aynı
    assert (sb["sd"] / "tradingbot-worker.service").read_text(encoding="utf-8") == \
           (REPO_ROOT / "deploy" / "tradingbot-worker.service").read_text(encoding="utf-8")


@needs_linux
def test_setup_detached_head_without_branch_arg_fails_clearly(tmp_path):
    sb = _sandbox(tmp_path, "det")
    cwd = tmp_path / "det"
    r1 = _run_setup(sb, cwd)
    assert r1.returncode == 0
    app = sb["base"] / "app"
    subprocess.run(["git", "-C", str(app), "checkout", "--detach", "-q"], check=True, capture_output=True)
    r2 = _run_setup(sb, cwd, args=[])
    assert r2.returncode != 0
    assert "detached HEAD" in r2.stdout + r2.stderr
    assert "Kuruldu (bütün aşamalar tamamlandı)" not in r2.stdout


@needs_linux
def test_setup_new_clone_requires_repo_and_branch(tmp_path):
    sb = _sandbox(tmp_path, "req")
    r = _run_setup(sb, tmp_path / "req", args=[str(REPO_ROOT)])      # branch yok, APP/.git yok
    assert r.returncode != 0 and "zorunlu" in r.stdout + r.stderr


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
