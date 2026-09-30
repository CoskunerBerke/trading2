"""deploy/restore.sh davranışı (2026-09-30): geri yükleme root olarak çalışır ve arşiv `tar` "data" süzgeciyle açılır, bu yüzden
açılan state root'a ait olur. Betik geri yüklemeden ÖNCE eski state'in sahibini alıp SONRA yeni state'e vermeli; hata olursa worker'ı
başlatmadan açık bir ileti yazmalı; kuru çalıştırma hiçbir şeye dokunmamalı.

Gerçek sistem yerine taklit `python` (TRADINGBOT_PY), `systemctl` ve `chown` kullanılır (root gerekmez).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy" / "restore.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None or shutil.which("stat") is None, reason="bash/stat yok")

STUB_PY = r"""#!/usr/bin/env bash
# taklit python: "-m tradingbot restore ARCHIVE [--yes]"
echo "py $*" >> "$STUB_LOG"
[[ "${STUB_FAIL:-}" == "1" ]] && { echo "StorageError: yedek doğrulanamadı" >&2; exit 1; }
if [[ " $* " == *" --yes "* ]]; then
  st="$TRADINGBOT_STATE_DIR"; pre="$(dirname "$st")/state.pre-restore-T"
  [[ -d "$st" ]] && mv "$st" "$pre"
  mkdir -p "$st"; echo restored > "$st/marker.txt"
  echo '{"ok": true, "xp_segments": {"needed": 0, "present": 0, "copied": 0, "missing": []}}'
else
  echo '{"ok": true, "dry_run": true}'
fi
"""
STUB_REC = r"""#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
"""


def _env(tmp_path: Path, **extra: str) -> tuple[dict, Path, Path]:
    base = tmp_path / "opt"
    (base / "app").mkdir(parents=True)
    data = base / "data"
    state = data / "state"
    state.mkdir(parents=True)
    (state / "old.txt").write_text("old", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("python", STUB_PY), ("systemctl", STUB_REC), ("chown", STUB_REC)):
        p = bindir / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    log = tmp_path / "calls.log"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}", "TRADINGBOT_BASE": str(base),
           "TRADINGBOT_PY": str(bindir / "python"), "TRADINGBOT_DATA": str(data), "TRADINGBOT_STATE_DIR": str(state),
           "STUB_LOG": str(log), **extra}
    return env, state, log


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, str(SCRIPT), "/tmp/archive.tar.gz", *args], env=env, capture_output=True, text=True,
                          timeout=60)


def _owner(path: Path) -> str:
    return subprocess.run(["stat", "-c", "%U:%G", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def test_restore_gives_the_restored_state_back_to_the_old_owner_then_starts_the_worker(tmp_path):
    env, state, log = _env(tmp_path)
    owner = _owner(state)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "systemctl stop tradingbot-worker.service"
    assert "py -m tradingbot restore /tmp/archive.tar.gz --yes" in calls
    i_chown = calls.index(f"chown -R {owner} {state}")
    i_start = calls.index("systemctl start tradingbot-worker.service")
    assert calls.index("py -m tradingbot restore /tmp/archive.tar.gz --yes") < i_chown < i_start, calls
    assert (state / "marker.txt").exists() and "sahiplik geri veriliyor" in r.stdout


def test_a_failed_restore_says_so_and_does_not_start_the_worker(tmp_path):
    env, state, log = _env(tmp_path, STUB_FAIL="1")
    r = _run(env)
    assert r.returncode != 0
    assert "GERİ YÜKLEME BAŞARISIZ" in r.stderr and "systemctl start tradingbot-worker.service" in r.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert "systemctl stop tradingbot-worker.service" in calls
    assert not any(c.startswith("systemctl start") for c in calls) and not any(c.startswith("chown") for c in calls)
    assert (state / "old.txt").exists(), "doğrulama hatasında state'e dokunulmaz"


def test_dry_run_only_calls_restore_without_yes_and_touches_nothing(tmp_path):
    env, state, log = _env(tmp_path)
    r = _run(env, "--dry-run")
    assert r.returncode == 0, r.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls == ["py -m tradingbot restore /tmp/archive.tar.gz"], calls
    assert (state / "old.txt").exists() and not (state / "marker.txt").exists()


def test_a_missing_state_dir_takes_the_owner_from_the_data_dir(tmp_path):
    env, state, log = _env(tmp_path)
    shutil.rmtree(state)
    owner = _owner(state.parent)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert f"chown -R {owner} {state}" in log.read_text(encoding="utf-8").splitlines()
