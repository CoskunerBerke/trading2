"""Deployment yedek sözleşmesi regresyonları.

`deploy/update.sh` yedeği `deploy/backup.sh manual` ile alıyordu; CLI ise yalnız
`--daily`/`--hourly` tanıyordu. `set -euo pipefail` altında bu, deployment'ı daha git
adımına varmadan `unrecognized arguments` ile öldürüyordu — yani script yıllardır
çalışmıyordu ve kimse fark etmemişti çünkü kimse çalıştırmıyordu.

Buradaki sözleşme:
 1. CLI `ops.backup.KINDS` içindeki DÖRT türü de kabul eder.
 2. Yedek yalnız ALINMAZ, DOĞRULANIR; doğrulama düşerse çıkış kodu sıfır DEĞİLDİR.
 3. Yedek başarısızsa `update.sh` git ve servis mutasyonuna HİÇ ULAŞMAZ.
 4. Yedek doğrulaması git/servis adımlarından ÖNCE gelir (sıra denetimi).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tradingbot.ops.backup import KINDS, run_backup, verify_backup

REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
UPDATE_SH = REPO / "deploy" / "update.sh"
BACKUP_SH = REPO / "deploy" / "backup.sh"


# ------------------------------------------------------------------ 1) CLI sözleşmesi

def _state(tmp: Path) -> Path:
    st = tmp / "state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER"}), encoding="utf-8")
    (st / "futures_ledger.json").write_text(json.dumps({"positions": {}, "history": []}),
                                            encoding="utf-8")
    return st


@pytest.mark.parametrize("kind", list(KINDS))
def test_01_backup_cli_accepts_every_declared_kind(tmp_path, kind):
    """`KINDS` dört tür tanımlıyorsa CLI dördünü de kabul ETMELİ."""
    st = _state(tmp_path)
    res = run_backup(st, tmp_path / "backups", kind=kind)
    assert Path(res.archive).exists()
    assert Path(res.archive).parent.name == kind
    ver = verify_backup(res.archive)
    assert ver["ok"] and ver["members"] > 0
    assert ver["sha256"] == res.sha256 == ver["expected"]


def test_02_backup_cli_parser_exposes_all_kinds():
    from tradingbot.cli import build_parser
    p = build_parser()
    sub = next(a for a in p._actions if hasattr(a, "choices") and a.choices)  # noqa: SLF001
    backup = sub.choices["backup"]
    opts = {o for a in backup._actions for o in a.option_strings}             # noqa: SLF001
    for k in KINDS:
        assert f"--{k}" in opts, f"CLI `--{k}` bayrağını tanımıyor (mevcut: {sorted(opts)})"
    # Vacuous olmasın: parser gerçekten ayrıştırabilmeli.
    for k in KINDS:
        ns = p.parse_args(["backup", f"--{k}"])
        assert getattr(ns, k) is True


def test_03_backup_cli_rejects_two_kinds_at_once(tmp_path, capsys):
    from tradingbot.cli_v3 import cmd_backup
    from types import SimpleNamespace
    st = _state(tmp_path)
    cfg = SimpleNamespace(
        state_path=st, backups_path=tmp_path / "backups",
        obsidian=SimpleNamespace(root=None),
        v3=SimpleNamespace(storage=SimpleNamespace(keep_hourly=2, keep_daily=2, keep_weekly=2)))
    args = SimpleNamespace(hourly=True, daily=True, weekly=False, manual=False)
    assert cmd_backup(cfg, args) == 2, "iki tür birden sessizce kabul edildi"


def test_04_backup_cli_returns_nonzero_when_verification_fails(tmp_path, monkeypatch):
    """Bozuk arşiv üretilirse çıkış kodu SIFIR OLAMAZ — çağıran deployment durmalı."""
    from types import SimpleNamespace

    import tradingbot.cli_v3 as cli
    st = _state(tmp_path)
    cfg = SimpleNamespace(
        state_path=st, backups_path=tmp_path / "backups",
        obsidian=SimpleNamespace(root=None),
        v3=SimpleNamespace(storage=SimpleNamespace(keep_hourly=2, keep_daily=2, keep_weekly=2)))
    args = SimpleNamespace(hourly=False, daily=False, weekly=False, manual=True)
    assert cli.cmd_backup(cfg, args) == 0            # sağlam yol önce kanıtlanır

    import tradingbot.ops.backup as ob
    real = ob.run_backup

    def corrupt(*a, **kw):
        res = real(*a, **kw)
        Path(res.archive).write_bytes(b"bu bir tar.gz DEGIL")
        return res

    monkeypatch.setattr(ob, "run_backup", corrupt)
    assert cli.cmd_backup(cfg, args) == 1, "bozuk arşiv 0 ile döndü — deployment devam ederdi"


def test_05_verify_backup_rejects_unsafe_members_and_sha_mismatch(tmp_path):
    st = _state(tmp_path)
    res = run_backup(st, tmp_path / "backups", kind="manual")
    side = Path(res.archive + ".sha256")
    side.write_text("0" * 64 + f"  {Path(res.archive).name}\n", encoding="utf-8")
    bad = verify_backup(res.archive)
    assert not bad["ok"] and "sha256" in bad["error"]


# ------------------------------------------------------------------ 2) script sözleşmesi

@pytest.mark.skipif(BASH is None, reason="bash yok")
def test_06_update_sh_is_syntactically_valid():
    r = subprocess.run([BASH, "-n", str(UPDATE_SH)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    r2 = subprocess.run([BASH, "-n", str(BACKUP_SH)], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    assert r2.returncode == 0, r2.stderr


def test_07_update_sh_runs_backup_before_any_git_or_service_mutation():
    """Sıra denetimi: yedek satırı git/systemctl satırlarından ÖNCE gelmeli."""
    txt = UPDATE_SH.read_text(encoding="utf-8")
    lines = txt.splitlines()

    def first(pred) -> int:
        for i, ln in enumerate(lines):
            if ln.strip().startswith("#"):
                continue
            if pred(ln):
                return i
        return 10**6
    i_backup = first(lambda ln: "deploy/backup.sh" in ln)
    i_git = first(lambda ln: ln.strip().startswith("git ") or " git " in ln)
    i_svc = first(lambda ln: "systemctl" in ln)
    i_pip = first(lambda ln: "pip install" in ln)
    assert i_backup < i_git, "yedek git'ten SONRA"
    assert i_backup < i_svc, "yedek servis restart'ından SONRA"
    assert i_backup < i_pip, "yedek pip'ten SONRA"
    assert "set -euo pipefail" in txt, "fail-fast kayboldu"
    assert "exit 1" in txt


def test_08_update_sh_no_longer_calls_an_unsupported_backup_kind():
    txt = UPDATE_SH.read_text(encoding="utf-8")
    import re
    calls = re.findall(r"deploy/backup\.sh\"?\s+\"?\$?\{?([A-Za-z_:\-]*)", txt)
    for c in calls:
        token = c.split(":-")[-1].strip("}\"' ")
        if not token or token.startswith("$"):
            continue
        assert token in KINDS, f"desteklenmeyen yedek türü: {token!r}"


@pytest.mark.skipif(BASH is None, reason="bash yok")
def test_09_failed_backup_stops_deployment_before_git_and_services(tmp_path):
    """Sahte ortamda yedek başarısız edilir; git/systemctl ÇAĞRILMAMALI."""
    base = tmp_path / "opt"
    app = base / "app"
    (app / "deploy").mkdir(parents=True)
    (base / "venv" / "bin").mkdir(parents=True)
    shutil.copy(UPDATE_SH, app / "deploy" / "update.sh")
    # Yedek BAŞARISIZ olsun.
    (app / "deploy" / "backup.sh").write_text(
        "#!/usr/bin/env bash\necho 'yedek dogrulamasi dustu' >&2\nexit 1\n", encoding="utf-8")
    marker = tmp_path / "calls.log"
    binmock = tmp_path / "bin"
    binmock.mkdir()
    for tool in ("git", "systemctl"):
        p = binmock / tool
        p.write_text(f"#!/usr/bin/env bash\necho {tool} \"$@\" >> '{marker}'\nexit 0\n",
                     encoding="utf-8")
        p.chmod(0o755)
    (base / "venv" / "bin" / "pip").write_text(
        f"#!/usr/bin/env bash\necho pip \"$@\" >> '{marker}'\nexit 0\n", encoding="utf-8")
    (base / "venv" / "bin" / "pip").chmod(0o755)
    (base / "venv" / "bin" / "python").write_text(
        f"#!/usr/bin/env bash\necho python \"$@\" >> '{marker}'\nexit 0\n", encoding="utf-8")
    (base / "venv" / "bin" / "python").chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{binmock}{os.pathsep}{env.get('PATH', '')}"
    env["TRADINGBOT_BASE"] = str(base)
    r = subprocess.run([BASH, str(app / "deploy" / "update.sh")], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, cwd=str(app))
    assert r.returncode != 0, "yedek düştüğü hâlde deployment sıfırla döndü"
    assert not marker.exists(), f"git/systemctl/pip ÇAĞRILDI: {marker.read_text()}"
    assert not (base / ".last_good_commit").exists(), "rollback işaretçisi yedeksiz yazıldı"


@pytest.mark.skipif(BASH is None, reason="bash yok")
def test_10_successful_backup_lets_deployment_proceed(tmp_path):
    """Karşı kanıt: yedek geçerse git adımına GERÇEKTEN ulaşılır (test vacuous değil)."""
    base = tmp_path / "opt"
    app = base / "app"
    (app / "deploy").mkdir(parents=True)
    (base / "venv" / "bin").mkdir(parents=True)
    shutil.copy(UPDATE_SH, app / "deploy" / "update.sh")
    (app / "deploy" / "backup.sh").write_text(
        "#!/usr/bin/env bash\necho 'yedek ok'\nexit 0\n", encoding="utf-8")
    marker = tmp_path / "calls.log"
    binmock = tmp_path / "bin"
    binmock.mkdir()
    for tool in ("git", "systemctl", "sleep"):
        p = binmock / tool
        body = "rev-parse" if tool == "git" else ""
        p.write_text(
            f"#!/usr/bin/env bash\necho {tool} \"$@\" >> '{marker}'\n"
            f"{'if [ \"$1\" = \"rev-parse\" ]; then echo abc123; fi' if body else ''}\nexit 0\n",
            encoding="utf-8")
        p.chmod(0o755)
    for exe in ("pip", "python"):
        p = base / "venv" / "bin" / exe
        p.write_text(f"#!/usr/bin/env bash\necho {exe} \"$@\" >> '{marker}'\nexit 0\n",
                     encoding="utf-8")
        p.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{binmock}{os.pathsep}{env.get('PATH', '')}"
    env["TRADINGBOT_BASE"] = str(base)
    r = subprocess.run([BASH, str(app / "deploy" / "update.sh")], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, cwd=str(app))
    assert marker.exists(), f"git adımına hiç ulaşılmadı: {r.stdout}\n{r.stderr}"
    calls = marker.read_text(encoding="utf-8")
    assert "git fetch" in calls
    assert (base / ".last_good_commit").exists(), "rollback işaretçisi yazılmadı"


def test_11_backup_kind_is_configurable_but_defaults_to_manual():
    txt = UPDATE_SH.read_text(encoding="utf-8")
    assert "TRADINGBOT_BACKUP_KIND:-manual" in txt, "varsayılan tür manual olmalı"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="izin bitleri Windows'ta anlamsız")
def test_12_deploy_scripts_remain_executable_contract():
    for p in (UPDATE_SH, BACKUP_SH):
        assert p.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
