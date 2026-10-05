# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1a — gece birimi çalışma zamanı (docs/SYSTEM_LEARNING_ENGINE_V1.md §2.4, §2.8, §2.9, §6.1,
§7.7; P1a depo kabul testleri 12, 13, 16'nın A/B kısmı, 17 ve 5'in özet/durum metni kısmı).

* 12: sahte state üzerinde TAM gece (`night.run_night`): bütün yazımlar `data/research` altında (`open`, `io.open`,
  `os.open`, `os.replace`/`rename`, `unlink`/`rmtree`, `mkdir` gözlenir), ledger'lar `"r"` kipinde açılır, state baytları
  değişmez; rastgele state ağaçlarında `find_books` yolları ve `state/spot_ledger.json` hiç yazılmaz (özellik testi).
* 13: S0 öz-denetimi — yazılabilir state, açık soket, yanlış `memory.max` → `ISOLATION_BROKEN` (çıkış 3, yalnız S0);
  SKEW → yalnız S0/S1a/S7; PAPER denetimi; gerçek deneme fonksiyonları ve `read_git_head`.
* 16 (A/B): sürüm günü tam çalışma, KAPALI gece yalnız S0 (`AB_OFF`), rotasyon payı < 3 gün → `AB_OFF_ZORUNLU_ARŞİV`.
* 17: `digest_tr.md` ≤ 8 KB, `engine_summary.json` ≤ 256 KB, `engine-status --brief` ≤ 60 satır.
* 5 (metin): özet ve durum çıktılarında yalnız-gerçekleşmiş satırda `TUTTU`/`HEDEF GÜNÜ` yok.

Bu dosyadaki testler pytest'e özgü bir şey kullanmaz (yalnız `tmp_path` ve `monkeypatch` fixture'ları):
VPS'teki bağımsız koşucu (`tests/standalone/run_engine_invariants.py`) 13'ü pytest olmadan buradan koşar.
"""
from __future__ import annotations

import builtins
import errno
import importlib.util
import io
import json
import os
import random
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import (  # noqa: E402
    MEM_MAX,
    SHA_APP,
    SHA_ENGINE,
    FakeHost,
    FakeVps,
    at,
    fake_repo,
    night_of,
    run_engine_night,
    trade,
)

from tradingbot.research_engine import closes as C  # noqa: E402
from tradingbot.research_engine import lock as LK  # noqa: E402
from tradingbot.research_engine import night as N  # noqa: E402
from tradingbot.research_engine import paths as P  # noqa: E402
from tradingbot.research_engine import selfcheck as SC  # noqa: E402
from tradingbot.research_engine import summary as SM  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _no_ab(v: FakeVps) -> None:
    """A/B penceresini dışarıda bırak (motor SHA'sı çok önce görülmüş): çok geceli testler her gece tam çalışır."""
    SC.register_epoch(v.paths, SHA_ENGINE, at("2020-01-01"), "test")


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()} if root.exists() else {}


def _books(v: FakeVps, day: str, *, spot: bool = True) -> None:
    trade(v.fut(), "ETH/USDT", at(day, 3), 100.0, 101.0)
    trade(v.fut("strategy_paper_box"), "BTC/USDT", at(day, 4), 100.0, 99.5)
    if spot:
        v.spot().market_buy("ETH/USDT", qty=P_D("0.5"), ref_price=P_D("100"), now=at(day, 5))
        v.spot().market_sell("ETH/USDT", qty=P_D("0.2"), ref_price=P_D("101"), now=at(day, 6))


def P_D(x: str):
    from decimal import Decimal
    return Decimal(x)


# ============================================================================ yazım gözlemcisi (kabul 12)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _spy_io(monkeypatch) -> list[tuple]:
    ev: list[tuple] = []
    r_open, r_io_open, r_os_open = builtins.open, io.open, os.open
    r_replace, r_rename, r_unlink, r_remove = os.replace, os.rename, os.unlink, os.remove
    r_mkdir, r_rmtree = os.mkdir, shutil.rmtree

    def _p(x):
        return os.fspath(x) if isinstance(x, (str, bytes, os.PathLike)) else None

    def b_open(file, mode="r", *a, **k):
        if _p(file) is not None:
            ev.append(("open", _p(file), mode))
        return r_open(file, mode, *a, **k)

    def i_open(file, mode="r", *a, **k):
        if _p(file) is not None:
            ev.append(("open", _p(file), mode))
        return r_io_open(file, mode, *a, **k)

    def o_open(path, flags, *a, **k):
        ev.append(("os.open", _p(path), flags))
        return r_os_open(path, flags, *a, **k)

    def rep(src, dst, *a, **k):
        ev.append(("replace", _p(src), _p(dst)))
        return r_replace(src, dst, *a, **k)

    def ren(src, dst, *a, **k):
        ev.append(("replace", _p(src), _p(dst)))
        return r_rename(src, dst, *a, **k)

    def unl(path, *a, **k):
        ev.append(("unlink", _p(path)))
        return r_unlink(path, *a, **k)

    def rem(path, *a, **k):
        ev.append(("unlink", _p(path)))
        return r_remove(path, *a, **k)

    def mkd(path, *a, **k):
        ev.append(("mkdir", _p(path)))
        return r_mkdir(path, *a, **k)

    def rmt(path, *a, **k):
        ev.append(("rmtree", _p(path)))
        return r_rmtree(path, *a, **k)

    monkeypatch.setattr(builtins, "open", b_open)
    monkeypatch.setattr(io, "open", i_open)
    monkeypatch.setattr(os, "open", o_open)
    monkeypatch.setattr(os, "replace", rep)
    monkeypatch.setattr(os, "rename", ren)
    monkeypatch.setattr(os, "unlink", unl)
    monkeypatch.setattr(os, "remove", rem)
    monkeypatch.setattr(os, "mkdir", mkd)
    monkeypatch.setattr(shutil, "rmtree", rmt)
    return ev


def _under(p: str, root: Path) -> bool:
    try:
        Path(os.path.abspath(p)).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _write_violations(ev: list[tuple], research: Path) -> list[tuple]:
    bad = []
    for e in ev:
        if e[0] == "open" and any(c in e[2] for c in "wax+") and not _under(e[1], research):
            bad.append(e)
        elif e[0] == "os.open" and e[2] & _WRITE_FLAGS and not _under(e[1], research):
            bad.append(e)
        elif e[0] == "replace" and not (_under(e[1], research) and _under(e[2], research)):
            bad.append(e)
        elif e[0] in ("unlink", "mkdir", "rmtree") and not _under(e[1], research):
            bad.append(e)
    return bad


# ============================================================================ kabul 12: tam gece, yazım sınırı
def test_full_night_writes_only_under_research_and_opens_ledgers_read_only(tmp_path, monkeypatch):
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path)
    _books(v, "2026-09-01")
    v.save(night_of("2026-09-02") - timedelta(minutes=1))
    before = _files(v.state)
    ev = _spy_io(monkeypatch)
    st = N.run_night(v.paths, now=night_of("2026-09-02"), **host.kw())
    _books(v, "2026-09-02", spot=False)
    monkeypatch.undo()
    v.save(night_of("2026-09-03") - timedelta(minutes=1))
    before2 = _files(v.state)
    ev2 = _spy_io(monkeypatch)
    st2 = N.run_night(v.paths, now=night_of("2026-09-03"), **host.kw())
    monkeypatch.undo()
    assert st["result"] == N.R_SUCCESS and st2["result"] == N.R_SUCCESS, (st["stages"], st2["stages"])
    assert list(st2["plan"]) == list(N.PLAN_FULL) and all(st2["stages"][s]["status"] == "OK" for s in N.STAGES)
    research = v.data / "research"
    for e in (ev, ev2):
        assert not _write_violations(e, research), _write_violations(e, research)[:5]
        led = [x for x in e if x[1] and x[1].endswith(("futures_ledger.json", "spot_ledger.json"))]
        assert led and all(x[0] == "open" and x[2] == "r" for x in led), led
    assert _files(v.state) == before2 and set(before) <= set(before2), "state baytları değişmedi"
    assert {p.name for p in v.data.iterdir()} == {"state", "research"}
    # beklenen çıktılar (VPS smoke çalıştırmasının da beklediği dosyalar)
    for rel in ("summary/run_status.json", "summary/digest_tr.md", "summary/engine_summary.json",
                "summary/daily_target.json", "target/daily.jsonl", "snapshots/2026-09-03.json.gz",
                "closes/main_fut", "closes/main_spot", "entries/main_spot", "backup/research-small-2026-09-03.tar.gz",
                "backup/research-small-2026-09-03.tar.gz.sha256", f"runs/{st2['run_id']}/run_status.json"):
        assert (research / rel).exists(), rel
    rs = json.loads((research / "summary" / "run_status.json").read_text(encoding="utf-8"))
    assert rs["result"] == "SUCCESS" and rs["exit_code"] == 0 and rs["selfcheck"]["status"] == "OK"
    assert rs["shas"] == {"app": SHA_APP, "engine": SHA_ENGINE, "relation": "ANCESTOR"}
    assert rs["config"]["sha256"] and rs["data_seal"] is None and rs["schema"] == N.RUN_SCHEMA
    for s in N.STAGES:
        assert rs["stages"][s]["duration_s"] >= 0 and "cpu_s" in rs["stages"][s]
    assert {"cpu_self_s", "cpu_children_s", "maxrss_bytes", "memory_peak_bytes", "memory_max_bytes"} <= set(rs["resources"])
    assert rs["stages"]["S7b"]["result"]["verified"] is True


def _load_scorecard():
    spec = importlib.util.spec_from_file_location("bot_scorecard_night", ROOT / "scripts" / "bot_scorecard.py")
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    return S


def test_full_night_never_writes_find_books_or_spot_paths_property(tmp_path, monkeypatch):
    S = _load_scorecard()
    rng = random.Random(11)
    dirs = list(S.BOOKS) + ["yeni_defter"]
    for trial in range(4):
        v = FakeVps(tmp_path / f"t{trial}")
        host = FakeHost(tmp_path / f"t{trial}")
        chosen = rng.sample(dirs, rng.randint(1, 4))
        for b in chosen:
            for i in range(rng.randint(0, 3)):
                trade(v.fut(b), rng.choice(["ETH/USDT", "BTC/USDT"]), at("2026-09-01", 2 + i), 100.0,
                      100.0 + rng.uniform(-2, 2))
        if rng.random() < 0.6:
            v.spot().market_buy("ETH/USDT", qty=P_D("0.2"), ref_price=P_D("100"), now=at("2026-09-01", 6))
        v.save(night_of("2026-09-02") - timedelta(minutes=1))
        protected = {os.path.abspath(p) for p in S.find_books(v.state).values()}
        protected.add(os.path.abspath(v.state / "spot_ledger.json"))
        before = _files(v.state)
        ev = _spy_io(monkeypatch)
        st = N.run_night(v.paths, now=night_of("2026-09-02"), **host.kw())
        monkeypatch.undo()
        assert st["result"] == N.R_SUCCESS
        touched = [e for e in ev if e[0] != "open" or any(c in e[2] for c in "wax+")]
        for e in touched:
            for p in e[1:3]:
                if isinstance(p, str):
                    assert os.path.abspath(p) not in protected, e
        assert _files(v.state) == before
        assert not _write_violations(ev, v.data / "research")


# ============================================================================ kabul 13: S0 öz-denetimi
def _broken_run(tmp_path, **host_kw) -> tuple[FakeVps, dict, dict]:
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path, **host_kw)
    _books(v, "2026-09-01")
    v.save(night_of("2026-09-02") - timedelta(minutes=1))
    before = _files(v.state)
    st = N.run_night(v.paths, now=night_of("2026-09-02"), **host.kw())
    return v, st, before


def _assert_stopped(v: FakeVps, st: dict, before: dict, broken: list[str]) -> None:
    assert st["result"] == N.R_ISOLATION_BROKEN and st["exit_code"] == 3
    assert st["selfcheck"]["isolation"]["broken"] == broken
    assert set(st["stages"]) == {"S0"} and st["stages"]["S0"]["status"] == N.R_ISOLATION_BROKEN
    r = v.data / "research"
    for sub in ("closes", "entries", "snapshots", "target", "backup"):
        assert not (r / sub).exists() or not any((r / sub).iterdir()), f"{sub} yazılmamalıydı"
    assert not (r / "summary" / "digest_tr.md").exists()
    rs = json.loads((r / "summary" / "run_status.json").read_text(encoding="utf-8"))
    assert rs["result"] == "ISOLATION_BROKEN", "durma sebebi kayda geçer (--check görür)"
    assert _files(v.state) == before


def test_s0_writable_state_is_isolation_broken_and_probe_file_is_removed(tmp_path):
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path)
    _books(v, "2026-09-01")
    v.save(night_of("2026-09-02") - timedelta(minutes=1))
    (v.data / "market").mkdir()
    before = _files(v.state)
    pr = host.probes()
    pr.write_denied = SC.probe_write_denied                  # GERÇEK deneme: test dizinleri yazılabilir
    st = N.run_night(v.paths, now=night_of("2026-09-02"), **{**host.kw(), "probes": pr})
    _assert_stopped(v, st, before, ["write_denied_state", "write_denied_market", "write_denied_app"])
    for k in ("state", "market", "app"):
        c = st["selfcheck"]["isolation"]["checks"][f"write_denied_{k}"]
        assert c["status"] == SC.P_WRITABLE and c["probe_removed"] is True
    leftovers = [p for d in (v.state, v.data / "market", host.app) for p in d.iterdir()
                 if p.name.startswith(".engine-isolation-probe")]
    assert not leftovers, "beklenmedik biçimde açılan deneme dosyası hemen silinir"


def test_s0_open_socket_or_timeout_is_isolation_broken(tmp_path):
    v, st, before = _broken_run(tmp_path / "c", socket_status=SC.P_CONNECTED)
    _assert_stopped(v, st, before, ["socket_denied"])
    v, st, before = _broken_run(tmp_path / "t", socket_status=SC.P_TIMEOUT)
    _assert_stopped(v, st, before, ["socket_denied"])


def test_s0_wrong_memory_max_is_isolation_broken(tmp_path):
    v, st, before = _broken_run(tmp_path / "m", memory_status=SC.P_MISMATCH)
    _assert_stopped(v, st, before, ["memory_max"])
    v, st, before = _broken_run(tmp_path / "n", memory_status=SC.P_NO_ENV)
    _assert_stopped(v, st, before, ["memory_max"])


def test_s0_skew_runs_only_s0_s1a_s7(tmp_path):
    for i, rc in enumerate((1, 128)):
        v = FakeVps(tmp_path / f"k{i}")
        host = FakeHost(tmp_path / f"k{i}", git_rc=rc)
        _books(v, "2026-09-01")
        st = run_engine_night(v, host, night_of("2026-09-02"))
        assert st["result"] == N.R_SKEW and st["exit_code"] == 0 and SC.SKEW in st["flags"]
        assert tuple(st["plan"]) == N.PLAN_SKEW == ("S0", "S1a", "S7")
        assert {k: s["status"] for k, s in st["stages"].items()} == {"S0": "OK", "S1a": "OK", "S3": "NOT_PLANNED",
                                                                     "S7": "OK", "S7b": "NOT_PLANNED"}
        assert st["selfcheck"]["skew"]["relation"] == ("NOT_ANCESTOR" if rc == 1 else "UNKNOWN")
        r = v.data / "research"
        assert C.latest_closes(v.paths, "main_fut") and (r / "snapshots" / "2026-09-02.json.gz").exists(), "arşiv çalıştı"
        assert not (r / "target" / "daily.jsonl").exists() and not list((r / "backup").glob("*.tar.gz"))
        assert (r / "summary" / "digest_tr.md").exists()
        git = [c for c in host.calls if c[0] == "git"]
        assert git and git[0][-3:] == ["--is-ancestor", SHA_APP, SHA_ENGINE] and "safe.directory=" + str(host.engine) in git[0]
    v = FakeVps(tmp_path / "same")
    host = FakeHost(tmp_path / "same", engine_sha=SHA_APP)
    st = run_engine_night(v, host, night_of("2026-09-02"))
    assert st["result"] == N.R_SUCCESS and st["selfcheck"]["skew"]["relation"] == "SAME"
    assert not [c for c in host.calls if c[0] == "git"], "aynı SHA'da git çağrılmaz"


def test_s0_paper_check_variants(tmp_path):
    st_dir = tmp_path / "state"
    st_dir.mkdir()
    assert SC.check_paper(st_dir, {})["status"] == "OK" and SC.check_paper(st_dir, {})["source"] == "default_missing"
    (st_dir / "mode.json").write_text('{"mode": "PAPER", "live_order_path_enabled": false}', encoding="utf-8")
    assert SC.check_paper(st_dir, {})["status"] == "OK"
    assert SC.check_paper(st_dir, {"ALLOW_LIVE_TRADING": "true"})["status"] == SC.NOT_PAPER
    for bad in ('{"mode": "LIVE"}', '{"mode": "PAPER", "live_order_path_enabled": true}', "{bozuk", "[1]"):
        (st_dir / "mode.json").write_text(bad, encoding="utf-8")
        assert SC.check_paper(st_dir, {})["status"] == SC.NOT_PAPER, bad
    v = FakeVps(tmp_path / "v")
    host = FakeHost(tmp_path / "v")
    (v.state / "mode.json").write_text('{"mode": "TESTNET"}', encoding="utf-8")
    st = run_engine_night(v, host, night_of("2026-09-02"))
    assert st["result"] == N.R_NOT_PAPER and st["exit_code"] == 4 and set(st["stages"]) == {"S0"}
    assert not (v.data / "research" / "closes").exists()
    assert not (v.data / "research" / "locks" / "analysis.lock").exists(), "PAPER değilse kilit bile alınmaz"


def test_real_probe_functions(tmp_path, monkeypatch):
    d = tmp_path / "w"
    d.mkdir()
    r = SC.probe_write_denied(d)
    assert r["status"] == SC.P_WRITABLE and r["probe_removed"] and not list(d.iterdir())
    assert SC.probe_write_denied(tmp_path / "yok")["status"] == SC.P_ABSENT
    real = os.open
    for code, want in ((errno.EROFS, SC.P_DENIED), (errno.EACCES, SC.P_DENIED), (errno.ENOSPC, SC.P_ERROR),
                       (errno.EPERM, SC.P_ERROR)):
        def boom(path, flags, *a, _c=code, **k):
            if ".engine-isolation-probe" in str(path):
                raise OSError(_c, os.strerror(_c))
            return real(path, flags, *a, **k)
        monkeypatch.setattr(os, "open", boom)
        assert SC.probe_write_denied(d)["status"] == want, code
        monkeypatch.undo()
    assert SC.probe_writable(d)["status"] == SC.P_WRITABLE_OK
    assert SC.probe_writable(tmp_path / "yok" / "x")["status"] == SC.P_NOT_WRITABLE

    import socket as _socket

    class Sock:
        mode = "unreach"

        def __init__(self, *a, **k):
            pass

        def settimeout(self, t):
            assert t == SC.SOCKET_PROBE_TIMEOUT_S

        def connect(self, addr):
            assert addr == ("1.1.1.1", 443), "DNS'siz genel IP"
            if Sock.mode == "unreach":
                raise OSError(errno.ENETUNREACH, "Network is unreachable")
            if Sock.mode == "timeout":
                raise _socket.timeout("timed out")

        def close(self):
            pass
    monkeypatch.setattr(_socket, "socket", Sock)
    assert SC.probe_socket_denied()["status"] == SC.P_DENIED and SC.probe_socket_denied()["errno"] == "ENETUNREACH"
    Sock.mode = "timeout"
    assert SC.probe_socket_denied()["status"] == SC.P_TIMEOUT
    Sock.mode = "ok"
    assert SC.probe_socket_denied()["status"] == SC.P_CONNECTED
    monkeypatch.undo()
    # cgroup v2 / v1 / sınırsız / ortam yok
    cg = tmp_path / "cg"
    (cg / "system.slice" / "tradingbot-engine-night.service").mkdir(parents=True)
    (cg / "system.slice" / "tradingbot-engine-night.service" / "memory.max").write_text(f"{MEM_MAX}\n")
    proc = tmp_path / "proc_cgroup"
    proc.write_text("0::/system.slice/tradingbot-engine-night.service\n")
    env = {SC.ENV_EXPECTED_MEMORY_MAX: str(MEM_MAX)}
    assert SC.probe_memory_max(env, proc_cgroup=proc, cgroup_root=cg)["status"] == SC.P_MATCH
    wrong = {SC.ENV_EXPECTED_MEMORY_MAX: "1073741824"}
    assert SC.probe_memory_max(wrong, proc_cgroup=proc, cgroup_root=cg)["status"] == SC.P_MISMATCH
    assert SC.probe_memory_max({}, proc_cgroup=proc, cgroup_root=cg)["status"] == SC.P_NO_ENV
    (cg / "system.slice" / "tradingbot-engine-night.service" / "memory.max").write_text("max\n")
    assert SC.probe_memory_max(env, proc_cgroup=proc, cgroup_root=cg)["status"] == SC.P_MISMATCH, "sınırsız ≠ beklenen"
    (cg / "memory" / "x").mkdir(parents=True)
    (cg / "memory" / "x" / "memory.limit_in_bytes").write_text(f"{MEM_MAX}\n")
    proc.write_text("4:memory:/x\n1:name=systemd:/x\n")
    assert SC.probe_memory_max(env, proc_cgroup=proc, cgroup_root=cg)["status"] == SC.P_MATCH, "cgroup v1"
    assert SC.probe_memory_max(env, proc_cgroup=tmp_path / "yok", cgroup_root=cg)["status"] == SC.P_UNREADABLE


def test_read_git_head_variants_and_this_checkout(tmp_path):
    sha = "c" * 40
    det = tmp_path / "det"
    (det / ".git").mkdir(parents=True)
    (det / ".git" / "HEAD").write_text(sha + "\n")
    assert SC.read_git_head(det) == (sha, None)
    assert SC.read_git_head(fake_repo(tmp_path / "sym", sha))[0] == sha
    pk = tmp_path / "pk"
    (pk / ".git").mkdir(parents=True)
    (pk / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (pk / ".git" / "packed-refs").write_text(f"# pack-refs with: peeled\n{'d' * 40} refs/heads/other\n{sha} refs/heads/main\n")
    assert SC.read_git_head(pk)[0] == sha
    wt = tmp_path / "wt"
    wt.mkdir()
    gd = tmp_path / "common" / "worktrees" / "wt"
    gd.mkdir(parents=True)
    (gd / "HEAD").write_text("ref: refs/heads/feat\n")
    (gd / "commondir").write_text("../..\n")
    (tmp_path / "common" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / "common" / "refs" / "heads" / "feat").write_text(sha + "\n")
    (wt / ".git").write_text(f"gitdir: {gd}\n")
    assert SC.read_git_head(wt)[0] == sha, "worktree: .git dosyası + commondir"
    assert SC.read_git_head(tmp_path / "yok")[0] is None
    if shutil.which("git") and (ROOT / ".git").exists():
        cp = subprocess.run(["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
        if cp.returncode == 0:
            assert SC.read_git_head(ROOT)[0] == cp.stdout.strip(), "bu checkout'un HEAD'i (git çağırmadan okundu)"


def test_skew_ancestor_check_with_real_git_on_this_checkout(tmp_path):
    """SKEW'in gerçek `git merge-base --is-ancestor` çağrısı (safe.directory + en küçük ortam) bu checkout'ta; git yoksa
    veya geçmiş sığsa (HEAD~1 yok) denetlenecek bir şey yoktur."""
    seen: list[dict] = []

    def spy(cmd, **kw):
        seen.append(kw.get("env") or {})
        return subprocess.run(cmd, **kw)
    if not (shutil.which("git") and (ROOT / ".git").exists()):
        return
    head, err = SC.read_git_head(ROOT)
    cp = subprocess.run(["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), "rev-parse", "HEAD~1"],
                        capture_output=True, text=True)
    if head is None or cp.returncode != 0:
        return
    parent = cp.stdout.strip()
    assert SC.is_ancestor(ROOT, parent, head, runner=spy)[0] is True
    assert SC.is_ancestor(ROOT, head, parent, runner=spy)[0] is False
    assert SC.is_ancestor(ROOT, "e" * 40, head, runner=spy)[0] is None, "engine klonunda bilinmeyen app SHA'sı"
    assert seen and all(e.get("HOME") == "/nonexistent" and e.get("GIT_CONFIG_NOSYSTEM") == "1" for e in seen), \
        "git küresel/sistem config'ini okumaz (EACCES'li bir HOME her geceyi SKEW yapardı)"
    app = fake_repo(tmp_path / "app", parent)
    sk = SC.check_skew(app, ROOT)
    assert sk["status"] == SC.OK and sk["relation"] == "ANCESTOR" and sk["engine_sha"] == head
    sk = SC.check_skew(fake_repo(tmp_path / "ileri", head), fake_repo(tmp_path / "geri", parent))
    assert sk["status"] == SC.SKEW, "engine-app, app'in gerisindeyse SKEW (bilinmeyen depo → fail-closed)"


def test_run_id_directory_is_claimed_atomically(tmp_path):
    pth = P.EnginePaths.for_state(tmp_path / "data" / "state")
    pth.runs.mkdir(parents=True)
    start = at("2026-09-02", 1, 37)
    ids = [N._new_run_id(pth, start) for _ in range(3)]
    assert ids == ["20260902T013700Z", "20260902T013700Z-2", "20260902T013700Z-3"]
    assert all((pth.runs / i).is_dir() for i in ids) and all(N.RUN_ID_RE.match(i) for i in ids)


def test_backup_unit_overlap_waits_then_continues(tmp_path):
    slept: list[float] = []
    h = FakeHost(tmp_path, backup_states=["activating", "activating", "inactive"])
    r = SC.check_backup_overlap(runner=h.runner, sleep=slept.append)
    assert r["status"] == "WAITED" and r["polls"] == 2 and r["waited_s"] == 60.0 and slept == [30, 30]
    slept.clear()
    h = FakeHost(tmp_path / "b", backup_states=["active"])
    r = SC.check_backup_overlap(runner=h.runner, sleep=slept.append)
    assert r["status"] == SC.BACKUP_OVERLAP and r["waited_s"] == 15 * 60 and r["polls"] == 30 and len(slept) == 30
    assert SC.check_backup_overlap(runner=FakeHost(tmp_path / "i").runner, sleep=slept.append)["status"] == "IDLE"

    def missing(cmd, **kw):
        raise FileNotFoundError("systemctl")
    assert SC.check_backup_overlap(runner=missing, sleep=slept.append)["status"] == SC.BACKUP_STATE_UNKNOWN
    v = FakeVps(tmp_path / "v")
    host = FakeHost(tmp_path / "v", backup_states=["activating"])
    st = run_engine_night(v, host, night_of("2026-09-02"), backup_wait_s=90)
    assert st["result"] == N.R_SUCCESS and SC.BACKUP_OVERLAP in st["flags"] and host.slept == [30, 30, 30]


# ============================================================================ kilit, son tarih, aşama hatası, disk
def test_lock_held_is_skipped_locked_exit_zero_and_keeps_last_status(tmp_path):
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path)
    _books(v, "2026-09-01")
    first = run_engine_night(v, host, night_of("2026-09-02"))
    lk = LK.analysis_lock(v.paths)
    assert lk.try_acquire()
    try:
        st = run_engine_night(v, host, night_of("2026-09-03"))
    finally:
        lk.release()
    assert st["result"] == LK.SKIPPED_LOCKED and st["exit_code"] == 0 and set(st["stages"]) == {"S0"}
    last = json.loads((v.paths.summary / "run_status.json").read_text(encoding="utf-8"))
    assert last["run_id"] == first["run_id"], "atlanan deneme son tamamlanmış çalıştırmanın kaydını ezmez"
    assert json.loads((v.paths.runs / st["run_id"] / "run_status.json").read_text())["result"] == "SKIPPED_LOCKED"
    assert not (v.paths.snapshots / "2026-09-03.json.gz").exists()
    assert any("SKIPPED_LOCKED" in ln for ln in SM.status_lines(v.paths, brief=True)), "durum en yeni denemeyi gösterir"


def test_deadline_and_stage_failure_and_disk_refuse(tmp_path, monkeypatch):
    s = at("2026-09-02", 1, 37)
    assert N.compute_deadline(s) == at("2026-09-02", 3, 40)
    assert N.compute_deadline(at("2026-09-02", 14, 0)) == at("2026-09-02", 16, 3), "gündüz smoke: başlangıç + 2 sa 3 dk"
    assert N.compute_deadline(at("2026-09-02", 2, 30)) == at("2026-09-02", 3, 40)
    v = FakeVps(tmp_path / "d")
    host = FakeHost(tmp_path / "d")
    monkeypatch.setattr(N, "compute_deadline", lambda start: start - timedelta(seconds=1))
    st = run_engine_night(v, host, night_of("2026-09-02"))
    monkeypatch.undo()
    assert st["result"] == N.R_DEADLINE and st["exit_code"] == 0 and N.F_DEADLINE in st["flags"]
    assert all(st["stages"][x]["status"] == "SKIPPED_DEADLINE" for x in ("S1a", "S3", "S7", "S7b"))
    v = FakeVps(tmp_path / "f")
    host = FakeHost(tmp_path / "f")

    def boom(*a, **k):
        raise RuntimeError("yapay S1a hatası")
    monkeypatch.setattr(C, "run_s1a", boom)
    st = run_engine_night(v, host, night_of("2026-09-02"))
    monkeypatch.undo()
    assert st["result"] == N.R_FAILED and st["exit_code"] == 1 and N.F_STAGE_FAILED in st["flags"]
    assert st["stages"]["S1a"]["status"] == "FAILED" and "yapay S1a hatası" in st["stages"]["S1a"]["error"]
    assert st["stages"]["S3"] == {"status": "SKIPPED", "reason": "S1a başarısız"}
    assert st["stages"]["S7"]["status"] == "OK" and st["stages"]["S7b"]["status"] == "OK", "özet ve yedek yine yazılır"
    v = FakeVps(tmp_path / "x")
    host = FakeHost(tmp_path / "x")
    monkeypatch.setattr(N, "disk_guard", lambda paths: {"status": P.DISK_REFUSE, "reasons": ["test"]})
    st = run_engine_night(v, host, night_of("2026-09-02"))
    monkeypatch.undo()
    assert st["result"] == N.R_DISK_REFUSE and st["exit_code"] == 5 and set(st["stages"]) == {"S0"}
    assert not (v.paths.closes).exists()


def test_config_change_flag_and_runs_retention(tmp_path):
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path)
    _no_ab(v)
    st = run_engine_night(v, host, night_of("2026-09-02"))
    assert N.F_CONFIG_CHANGED not in st["flags"] and st["config"]["extra_entries"] == "record_selectivity"
    (host.app / "config.yaml").write_text("risk: {starting_equity_usdt: 1000}\nlearning_mode: {extra_entries: open, "
                                          "yeni_anahtar: 1}\nshared_experience: {bilinmeyen: true}\n", encoding="utf-8")
    st = run_engine_night(v, host, night_of("2026-09-03"))
    assert st["result"] == N.R_SUCCESS and N.F_CONFIG_CHANGED in st["flags"] and st["config"]["extra_entries"] == "open"
    assert any("DEĞİŞTİ" in ln for ln in SM.status_lines(v.paths))
    for i in range(33):
        (v.paths.runs / f"20260801T0000{i:02d}Z").mkdir(parents=True)
    gone = N.prune_runs(v.paths)
    left = sorted(p.name for p in v.paths.runs.iterdir() if p.is_dir())
    assert len(left) == N.KEEP_RUNS and len(gone) == 35 - N.KEEP_RUNS and (v.paths.runs / SC.EPOCHS_FILE).exists()


# ============================================================================ kabul 16 (A/B takvimi)
def test_ab_calendar_decisions():
    assert SC.ab_parity_on("2026-09-01") and not SC.ab_parity_on("2026-09-02"), "gün-yıl 244 çift, 245 tek"
    d = SC.ab_decision("2026-09-01", "2026-09-01", rotation_min_days=None)
    assert d["status"] == SC.AB_RELEASE_DAY and d["window"] == ["2026-09-02", "2026-09-15"]
    assert SC.ab_decision("2026-09-02", "2026-09-01", rotation_min_days=10.0)["status"] == SC.AB_OFF
    assert SC.ab_decision("2026-09-02", "2026-09-01", rotation_min_days=None)["status"] == SC.AB_OFF
    assert SC.ab_decision("2026-09-02", "2026-09-01", rotation_min_days=2.9)["status"] == SC.AB_OFF_FORCED
    assert SC.ab_decision("2026-09-03", "2026-09-01", rotation_min_days=0.5)["status"] == SC.AB_ON
    assert SC.ab_decision("2026-09-15", "2026-09-01", rotation_min_days=None)["status"] in (SC.AB_ON, SC.AB_OFF)
    assert SC.ab_decision("2026-09-16", "2026-09-01", rotation_min_days=None)["status"] == SC.AB_OUTSIDE
    assert SC.ab_decision("2026-09-02", None, rotation_min_days=None)["status"] == SC.AB_OUTSIDE
    nights = [SC.ab_decision(f"2026-09-{d:02d}", "2026-09-01", rotation_min_days=None)["status"] for d in range(2, 16)]
    assert nights.count(SC.AB_ON) == 7 and nights.count(SC.AB_OFF) == 7, "14 gece: 7 AÇIK + 7 KAPALI"


def test_ab_off_night_runs_only_s0_and_forced_archive_when_margin_low(tmp_path):
    v = FakeVps(tmp_path / "a")
    host = FakeHost(tmp_path / "a")
    _books(v, "2026-08-31")
    st0 = run_engine_night(v, host, night_of("2026-09-01"))
    assert st0["selfcheck"]["ab"]["status"] == SC.AB_RELEASE_DAY and st0["result"] == N.R_SUCCESS, "sürüm günü tam"
    digest0 = (v.paths.summary / "digest_tr.md").read_bytes()
    closes0 = _files(v.paths.closes)
    _books(v, "2026-09-01", spot=False)
    st1 = run_engine_night(v, host, night_of("2026-09-02"))
    assert st1["result"] == N.R_AB_OFF and tuple(st1["plan"]) == N.PLAN_AB_OFF and st1["exit_code"] == 0
    assert not (v.paths.snapshots / "2026-09-02.json.gz").exists() and _files(v.paths.closes) == closes0
    assert (v.paths.summary / "digest_tr.md").read_bytes() == digest0
    assert json.loads((v.paths.summary / "run_status.json").read_text())["result"] == "AB_OFF"
    st2 = run_engine_night(v, host, night_of("2026-09-03"))
    assert st2["selfcheck"]["ab"]["status"] == SC.AB_ON and st2["result"] == N.R_SUCCESS
    assert len(C.latest_closes(v.paths, "main_fut")) == 2, "KAPALI gecenin kapanışı AÇIK gecede arşivlendi"
    # rotasyon payı < 3 gün: KAPALI gecede de S1a
    v = FakeVps(tmp_path / "b")
    host = FakeHost(tmp_path / "b")
    run_engine_night(v, host, night_of("2026-09-01"))
    led = v.fut(equity="100000")
    t0 = night_of("2026-09-01") + timedelta(hours=1)
    for i in range(700):
        trade(led, "ETH/USDT", t0 + timedelta(seconds=90 * i), 100.0, 100.3, hold=timedelta(seconds=20))
    st = run_engine_night(v, host, night_of("2026-09-02"))
    assert st["result"] == N.R_AB_OFF_FORCED and tuple(st["plan"]) == N.PLAN_AB_OFF_FORCED
    assert st["selfcheck"]["rotation_for_ab"]["min_days"] < 3
    assert st["stages"]["S1a"]["status"] == "OK" and st["stages"]["S3"]["status"] == "NOT_PLANNED"
    assert len(C.latest_closes(v.paths, "main_fut")) == 700


# ============================================================================ kabul 5 (metin) + 17 (boyut)
def test_summary_and_status_bounded_and_no_claim_words_on_realized_only_rows(tmp_path):
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path)
    _no_ab(v)
    books = ["", "strategy_paper", "strategy_paper_m2", "strategy_paper_box", "pattern_trader", "strategy_paper_trend4h",
             "strategy_paper_candle4h", "strategy_paper_candle4h_strict"]
    run_engine_night(v, host, night_of("2026-10-01"))
    for d in range(1, 11):
        for i, b in enumerate(books):
            exit_px = 300.0 if (d == 8 and b == "") else 100.5 + i * 0.1      # 10-08: ana bot +200 USDT → isabet günü
            trade(v.fut(b), ["ETH/USDT", "BTC/USDT", "SOL/USDT"][i % 3], at(f"2026-10-{d:02d}", 3 + i), 100.0, exit_px)
        v.spot().market_buy("ETH/USDT", qty=P_D("0.1"), ref_price=P_D("100"), now=at(f"2026-10-{d:02d}", 12))
        v.spot().market_sell("ETH/USDT", qty=P_D("0.1"), ref_price=P_D("100.4"), now=at(f"2026-10-{d:02d}", 13))
        if d == 9:
            continue                                                  # 10-10 gecesi yok → son gün EKSİK (yalnız gerçekleşmiş)
        st = run_engine_night(v, host, night_of(f"2026-10-{d + 1:02d}"))
        assert st["result"] == N.R_SUCCESS, st["stages"]
    digest = (v.paths.summary / "digest_tr.md").read_text(encoding="utf-8")
    assert len(digest.encode("utf-8")) <= SM.DIGEST_MAX_BYTES
    assert (v.paths.summary / "engine_summary.json").stat().st_size <= SM.SUMMARY_MAX_BYTES
    brief = SM.status_lines(v.paths, brief=True, now=night_of("2026-10-11"))
    full = SM.status_lines(v.paths, brief=False, now=night_of("2026-10-11"))
    assert len(brief) <= SM.STATUS_MAX_LINES, len(brief)
    for text in (digest.splitlines(), brief, full):
        assert SM.claim_word_violations(text) == [], SM.claim_word_violations(text)
        assert not any("TUTTU" in ln for ln in text)
        assert not any("k*" in ln or "kaldıracı artır" in ln for ln in text), "k* yalnız lider tablosu/önerilerde (§7.6)"
    realized_only = [ln for ln in digest.splitlines() + brief if "yalnız gerçekleşmiş" in ln]
    assert realized_only, "son gün EKSİK: yalnız-gerçekleşmiş başlık satırı gerçekten üretildi"
    assert any(ln.strip().startswith("Son kesin gün") and "evet" in ln and "başarı değildir" in ln
               for ln in digest.splitlines()), "KESİN isabet günü yalnız kendi satırında, 'başarı değildir' notuyla"
    doc = json.loads((v.paths.summary / "engine_summary.json").read_text(encoding="utf-8"))
    assert doc["schema"] == SM.SUMMARY_SCHEMA and doc["run"]["result"] == "SUCCESS" and doc["daily_target"]["rows"]
    for key in ("Öz-denetim: OK", "SKEW: yok", "A/B:", "Arşiv ve rotasyon payı", "GÜNLÜK HEDEF", "Araştırma yedeği"):
        assert any(key in ln for ln in brief), key


def test_digest_and_summary_json_size_guards(tmp_path):
    pth = P.EnginePaths.for_state(tmp_path / "data" / "state")
    pth.ensure_tree()
    books = {f"defter_{i:03d}": {"status": "OK", "new_closes": i, "new_entries": i, "recon_record": "OK", "recon_wallet": "OK",
                                 "rotation_days": {"history": 99.0, "entries": 9.0}, "flags": ["X" * 40]} for i in range(400)}
    st = {"run_id": "20261001T013700Z", "result": "SUCCESS", "plan": list(N.PLAN_FULL), "flags": ["Y"] * 50,
          "stages": {"S1a": {"status": "OK", "result": {"books": books}}}, "selfcheck": {}}
    txt = SM.digest_text(pth, st)
    assert len(txt.encode("utf-8")) <= SM.DIGEST_MAX_BYTES and "kısaltıldı" in txt
    doc = SM.summary_doc(pth, st)
    doc["daily_target"]["rows"] = [{"day": f"2026-09-{i % 28 + 1:02d}", "pad": "z" * 30000} for i in range(30)]
    raw = SM._bounded_json(doc)
    assert len(raw) <= SM.SUMMARY_MAX_BYTES and json.loads(raw)["daily_target"]["rows_truncated"] is True
    pth.write_json(pth.summary / "run_status.json", st)
    lines = SM.status_lines(pth, brief=True)
    assert len(lines) <= SM.STATUS_MAX_LINES and "kısaltıldı" in lines[-1]
