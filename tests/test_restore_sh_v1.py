"""deploy/restore.sh davranışı (2026-09-30): geri yükleme root olarak çalışır ve arşiv `tar` "data" süzgeciyle açılır, bu yüzden
açılan state root'a ait olur. Betik sahibi geri yüklemeden ÖNCE belirleyip (worker biriminin User='ı; yoksa eski state'in
gerçek sahibi, sayısal) SONRA yeni state'e vermeli; hata ya da Ctrl+C olursa worker'ı başlatmadan açık bir ileti yazmalı;
çıktı `| head`'e verilse ya da SSH kopsa da (SIGPIPE / SIGHUP) sahiplik verilip worker başlatılmalı; kuru çalıştırma hiçbir
şeye dokunmamalı.

Gerçek sistem yerine taklit `python` (TRADINGBOT_PY), `systemctl`, `chown` ve `stat` sarmalayıcısı kullanılır (root gerekmez).
"""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy" / "restore.sh"
BASH = shutil.which("bash")
REAL_STAT = shutil.which("stat")

pytestmark = pytest.mark.skipif(BASH is None or REAL_STAT is None or shutil.which("id") is None, reason="bash/stat/id yok")

STUB_PY = r"""#!/usr/bin/env bash
# taklit python: "-m tradingbot restore ARCHIVE [--yes]"
echo "py $*" >> "$STUB_LOG"
[[ "${STUB_FAIL:-}" == "1" ]] && { echo "StorageError: yedek doğrulanamadı" >&2; exit 1; }
if [[ " $* " == *" --yes "* ]]; then
  data="$(dirname "$TRADINGBOT_STATE_DIR")"
  if [[ -n "${STUB_SIG:-}" ]]; then
    mkdir -p "$data/tbrestore-x"          # açma sırasında kesinti: geçici klasör kalır
    kill "-$STUB_SIG" 0                   # Ctrl+C / SSH kopması gibi: tüm süreç grubuna
    [[ "$STUB_SIG" == "INT" ]] && { sleep 1; exit 130; }
    rmdir "$data/tbrestore-x"
  fi
  st="$TRADINGBOT_STATE_DIR"; pre="$data/state.pre-restore-T"
  [[ -d "$st" ]] && mv "$st" "$pre"
  mkdir -p "$st"; echo restored > "$st/marker.txt"
  [[ "${STUB_VAULT:-}" == "1" ]] && mkdir -p "$data/vault.restored-T/notes"
  [[ "${STUB_FAIL_AFTER:-}" == "1" ]] && { echo "OSError: yerine koyduktan sonra" >&2; exit 1; }
  echo '{"ok": true, "members": ['
  for i in $(seq 1 3000); do echo "  \"state/f$i.json\","; done
  echo '  "state/"], "xp_segments": {"needed": 0, "present": 0, "copied": 0, "missing": []}}'
else
  echo '{"ok": true, "dry_run": true}'
fi
"""
STUB_SYSTEMCTL = r"""#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_LOG"
[[ "$1" == "show" ]] && echo "${STUB_USER:-}"
exit 0
"""
STUB_CHOWN = r"""#!/usr/bin/env bash
echo "chown $*" >> "$STUB_LOG"
exit "${STUB_CHOWN_RC:-0}"
"""
# stat sarmalayıcısı: vault.restored-* için sahip adı STUB_VAULT_OWNER (root olmayan testte root'a ait dosya yapılamaz;
# root olarak koşan testte de root olmayan sahip taklit edilebilir)
STUB_STAT = r"""#!/usr/bin/env bash
if [[ -n "${STUB_VAULT_OWNER:-}" && "$1" == "-c" && "$2" == "%U" && "$3" == *"/vault.restored-"* ]]; then
  echo "$STUB_VAULT_OWNER"; exit 0
fi
exec "$REAL_STAT" "$@"
"""
ARCHIVE = "/tmp/archive.tar.gz"
START = "systemctl start tradingbot-worker.service"
STOP = "systemctl stop tradingbot-worker.service"
PY_YES = f"py -m tradingbot restore {ARCHIVE} --yes"


def _env(tmp_path: Path, **extra: str) -> tuple[dict, Path, Path]:
    base = tmp_path / "opt"
    (base / "app").mkdir(parents=True)
    data = base / "data"
    state = data / "state"
    state.mkdir(parents=True)
    (state / "old.txt").write_text("old", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("python", STUB_PY), ("systemctl", STUB_SYSTEMCTL), ("chown", STUB_CHOWN), ("stat", STUB_STAT)):
        p = bindir / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    log = tmp_path / "calls.log"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}", "TRADINGBOT_BASE": str(base),
           "TRADINGBOT_PY": str(bindir / "python"), "TRADINGBOT_DATA": str(data), "TRADINGBOT_STATE_DIR": str(state),
           "STUB_LOG": str(log), "REAL_STAT": str(REAL_STAT), **extra}
    return env, state, log


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, str(SCRIPT), ARCHIVE, *args], env=env, capture_output=True, text=True, timeout=60)


def _calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _num_owner(path: Path) -> str:
    st = path.stat()
    return f"{st.st_uid}:{st.st_gid}"


def test_restore_gives_the_restored_state_to_the_old_owner_then_starts_the_worker(tmp_path):
    env, state, log = _env(tmp_path)
    owner = _num_owner(state)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    calls = _calls(log)
    assert calls[0] == "systemctl show -p User --value tradingbot-worker.service" and calls[1] == STOP, calls
    i_chown = calls.index(f"chown -R {owner} {state}")          # sayısal uid:gid
    i_start = calls.index(START)
    assert calls.index(PY_YES) < i_chown < i_start, calls
    assert [c for c in calls if c.startswith("systemctl start")] == [START]
    assert (state / "marker.txt").exists() and "sahiplik geri veriliyor" in r.stdout and '"ok": true' in r.stdout
    assert "GERİ YÜKLEME BAŞARISIZ" not in r.stderr


def test_the_worker_units_user_is_the_owner_when_systemd_reports_one(tmp_path):
    other = next(p for p in pwd.getpwall() if p.pw_uid != os.getuid())
    env, state, log = _env(tmp_path, STUB_USER=other.pw_name)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert f"chown -R {other.pw_uid}:{other.pw_gid} {state}" in _calls(log)


def test_an_unknown_unit_user_stops_before_touching_the_worker(tmp_path):
    env, state, log = _env(tmp_path, STUB_USER="tbx_no_such_user_x")
    r = _run(env)
    assert r.returncode != 0 and "DUR:" in r.stderr
    assert _calls(log) == ["systemctl show -p User --value tradingbot-worker.service"]
    assert (state / "old.txt").exists() and not (state / "marker.txt").exists()


def test_a_failed_restore_says_so_and_does_not_start_the_worker(tmp_path):
    env, state, log = _env(tmp_path, STUB_FAIL="1")
    r = _run(env)
    assert r.returncode != 0
    assert "GERİ YÜKLEME BAŞARISIZ" in r.stderr and "sudo systemctl start tradingbot-worker.service" in r.stderr
    assert "State'e DOKUNULMADI" in r.stderr and r.stderr.count("GERİ YÜKLEME BAŞARISIZ") == 1
    calls = _calls(log)
    assert STOP in calls
    assert not any(c.startswith("systemctl start") for c in calls) and not any(c.startswith("chown") for c in calls)
    assert (state / "old.txt").exists(), "doğrulama hatasında state'e dokunulmaz"


def test_a_failure_after_the_state_was_replaced_gives_the_chown_remedy(tmp_path):
    env, state, log = _env(tmp_path, STUB_FAIL_AFTER="1")
    owner = _num_owner(state)
    r = _run(env)
    assert r.returncode != 0 and "GERİ YÜKLEME BAŞARISIZ" in r.stderr
    assert "YERİNE KONDU" in r.stderr and f"sudo chown -R {owner} {state}" in r.stderr
    assert not any(c.startswith("systemctl start") for c in _calls(log))


def test_a_failed_chown_says_so_gives_the_remedy_and_does_not_start_the_worker(tmp_path):
    env, state, log = _env(tmp_path, STUB_CHOWN_RC="1")
    r = _run(env)
    assert r.returncode != 0 and "GERİ YÜKLEME BAŞARISIZ" in r.stderr and "YERİNE KONDU" in r.stderr
    assert "sudo chown -R" in r.stderr
    assert not any(c.startswith("systemctl start") for c in _calls(log))


def test_dry_run_only_calls_restore_without_yes_and_touches_nothing(tmp_path):
    env, state, log = _env(tmp_path)
    r = _run(env, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert _calls(log) == [f"py -m tradingbot restore {ARCHIVE}"]
    assert (state / "old.txt").exists() and not (state / "marker.txt").exists()


def test_a_missing_state_dir_takes_the_owner_from_the_data_dir(tmp_path):
    env, state, log = _env(tmp_path)
    shutil.rmtree(state)
    owner = _num_owner(state.parent)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert f"chown -R {owner} {state}" in _calls(log)


def test_a_root_owned_restored_vault_copy_is_given_to_the_owner_too(tmp_path):
    env, state, log = _env(tmp_path, STUB_VAULT="1", STUB_VAULT_OWNER="root")
    owner = _num_owner(state)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    calls = _calls(log)
    vault = state.parent / "vault.restored-T"
    assert calls.index(f"chown -R {owner} {vault}") < calls.index(START), calls


def test_a_vault_copy_not_owned_by_root_is_left_alone(tmp_path):
    env, state, log = _env(tmp_path, STUB_VAULT="1", STUB_VAULT_OWNER="tradingbot")
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert not any("vault.restored-T" in c for c in _calls(log))


def test_output_piped_into_a_closed_reader_still_chowns_and_starts_the_worker(tmp_path):
    """`sudo bash restore.sh ARC | head -3`: okuyan taraf kapanınca bash SIGPIPE ile ölmemeli."""
    env, state, log = _env(tmp_path)
    owner = _num_owner(state)
    rfd, wfd = os.pipe()
    os.close(rfd)                      # okuyan taraf yok: ilk yazımda EPIPE / SIGPIPE
    try:
        p = subprocess.run([BASH, str(SCRIPT), ARCHIVE], env=env, stdout=wfd, stderr=subprocess.PIPE, text=True,
                           timeout=60)
    finally:
        os.close(wfd)
    calls = _calls(log)
    assert calls.index(PY_YES) < calls.index(f"chown -R {owner} {state}") < calls.index(START), (calls, p.stderr)
    assert "GERİ YÜKLEME BAŞARISIZ" not in p.stderr


def _run_in_own_session(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, str(SCRIPT), ARCHIVE], env=env, capture_output=True, text=True, timeout=60,
                          start_new_session=True)     # taklit python `kill -SIG 0` yalnız bu grubu vurur


def test_ctrl_c_during_the_restore_says_so_once_and_does_not_start_the_worker(tmp_path):
    env, state, log = _env(tmp_path, STUB_SIG="INT")
    r = _run_in_own_session(env)
    assert r.returncode == 130, (r.returncode, r.stderr)
    assert r.stderr.count("GERİ YÜKLEME BAŞARISIZ") == 1 and "State'e DOKUNULMADI" in r.stderr
    assert "tbrestore-x" in r.stderr                     # yarım kalan geçici klasör söylenir
    calls = _calls(log)
    assert STOP in calls and not any(c.startswith("systemctl start") for c in calls)
    assert not any(c.startswith("chown") for c in calls) and (state / "old.txt").exists()


def test_an_ssh_drop_during_the_restore_does_not_interrupt_it(tmp_path):
    env, state, log = _env(tmp_path, STUB_SIG="HUP")
    owner = _num_owner(state)
    r = _run_in_own_session(env)
    assert r.returncode == 0, (r.returncode, r.stderr)
    calls = _calls(log)
    assert calls.index(PY_YES) < calls.index(f"chown -R {owner} {state}") < calls.index(START), calls
    assert (state / "marker.txt").exists() and "GERİ YÜKLEME BAŞARISIZ" not in r.stderr
