# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1a — motor sürüm betiği `deploy/releases/tb-engine-<sha7>.sh` (docs/SYSTEM_LEARNING_ENGINE_V1.md
§9.3, §9.4, §10 P1a; depo kabul testi 10'un betik kısmı).

Betik SAHTE bir kökte (`TRADINGBOT_BASE`, `TRADINGBOT_SYSTEMD_DIR`) ve SAHTE bir `systemctl` ile koşulur; gerçek servis,
gerçek systemd ve ağ yoktur. Kaynak depo yerel bir çıplak depodur (`TB_ENGINE_REPO_URL`; içerik yine TAM SHA'ya
bağlıdır), app klonu VPS'te çalışan `f8b05fb`'dedir. Sahte `systemctl`:

* worker ve dashboard için her DEĞİŞTİREN fiilde (start/stop/restart/enable/…/set-property) `FORBIDDEN` yazar ve 99 ile
  düşer — betiğin worker'a hiç dokunmadığı böylece her senaryoda kanıtlanır;
* `start tradingbot-engine-night.service` (elle smoke) DEPLOY EDİLEN engine-app kodunu (`night.run_night`) gerçek
  `git` ata denetimiyle çalıştırır; yalnız çekirdek yalıtım denemeleri yerine-geçendir (`FAKE_SMOKE=fail` soketi açık
  gösterir → `ISOLATION_BROKEN`; `namespace` → 226/NAMESPACE benzetimi);
* `daemon-reload` yüklü birim kümesini kaydeder; `FAKE_RELOAD_DRIFT=1` worker MemoryMax'ını 4G'ye kaydırır.

Kanıtlananlar: kuru çalışma hiçbir şey değiştirmez; dağıtımda data/research İLK başlatmadan ÖNCE doğru sahiplik ve 0750
ile vardır, reload → smoke → zamanlayıcı sırası, smoke başarısızsa zamanlayıcı AÇILMAZ, reload sonrası 6G kayması geri
alınır, güvensiz ön koşullar (NeedDaemonReload=yes, saat penceresi, başka sürümün yeniden başlatmasından
3 gün geçmemesi (2026-10-06 sahip kararı; önceden 7 gün), PAPER değil, app ata değil)
hiçbir şeye dokunmadan durdurur, `--check`/`--ab-report` araştırma ağacını değiştirmez ve yalnız-gerçekleşmiş satırda
iddia kelimesi basmaz, geri alma zamanlayıcıyı/servisi ve klonu kaldırır, data/research'ü bayt bayt bırakır.
"""
from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
SCRIPT = ROOT / "deploy" / "releases" / "tb-engine-4962209.sh"
#: Betiğin kayıtlı sha256'sı (sürüm notu ve sahibe verilen değer; betik değişirse bu da bilinçli değişir).
SCRIPT_SHA256 = "6a33bb9b9a0fbf49fb80ffb08162b13b92c4f6711226a2ef0a54ce0b56890da6"
TEXT = SCRIPT.read_text(encoding="utf-8")
TIP = re.search(r'^TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
APP_SHA = "f8b05fb27310238c764ac7dad23221d84f7c0b6d"      # VPS'te çalışan app (hedefin atası)
BASH, GIT = shutil.which("bash"), shutil.which("git")
SVC, TMR = "tradingbot-engine-night.service", "tradingbot-engine-night.timer"


def _git(*a: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run([GIT, *a], cwd=str(cwd), capture_output=True, text=True)


def _has(sha: str) -> bool:
    return GIT is not None and _git("cat-file", "-e", sha + "^{commit}").returncode == 0


needs_sandbox = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and BASH and GIT and shutil.which("systemd-analyze") and shutil.which("flock")
         and _has(TIP) and _has(APP_SHA)),
    reason="sandbox Linux + bash + git + systemd-analyze + flock ve geçmişte hedef/app commit'leri ister")


# ============================================================================ statik
def test_script_is_small_syntax_ok_and_recorded():
    assert len(TEXT.splitlines()) <= 800, "küçük, ayrılmış betik (§9.3: < ~800 satır)"
    assert subprocess.run([BASH or "bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == SCRIPT_SHA256
    assert SCRIPT.name == f"tb-engine-{TIP[:7]}.sh"
    for flag in ("--dry-run", "--check", "--ab-report", "--rollback"):
        assert flag in TEXT


@pytest.mark.skipif(not _has(TIP), reason="hedef commit geçmişte yok (sığ klon)")
def test_script_pins_match_the_code_commit():
    assert _git("merge-base", "--is-ancestor", TIP, "HEAD").returncode == 0
    assert _git("merge-base", "--is-ancestor", APP_SHA, TIP).returncode == 0, "VPS app'i hedefin atası (SKEW yok)"
    for var, path in (("SVC_SHA256", "deploy/tradingbot-engine-night.service"),
                      ("TMR_SHA256", "deploy/tradingbot-engine-night.timer")):
        blob = subprocess.run([GIT, "show", f"{TIP}:{path}"], cwd=str(ROOT), capture_output=True).stdout
        assert re.search(rf'^{var}="{hashlib.sha256(blob).hexdigest()}"', TEXT, re.M), var
    runner = subprocess.run([GIT, "show", f"{TIP}:tests/standalone/run_engine_invariants.py"], cwd=str(ROOT),
                            capture_output=True, text=True).stdout
    n = len(re.findall(r'^\s+\(\d+, "test_research_engine_\w+",', runner, re.M))
    assert re.search(rf"^INV_TESTS={n}\b", TEXT, re.M) and n == 33


def test_script_never_mutates_worker_or_dashboard_statically():
    code = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
    for m in re.finditer(r'(?<![\w-])systemctl (start|stop|restart|try-restart|reload-or-restart|reload|kill|enable|'
                         r'disable|mask|set-property|edit|revert|daemon-reexec)\b([^\n;|&)]*)', code):
        pre = code[max(0, m.start() - 5):m.start()]
        if pre.endswith("sudo "):                       # sahibe basılan talimat metni (çalıştırılmaz)
            continue
        assert re.match(r'\s*(--\S+\s+)*"?\$(SVC|TMR)\b', m.group(2)), f"motor birimi dışında değiştiren systemctl: {m.group(0)}"
    assert "setup_vps_v3.sh ASLA" in TEXT and not re.search(r"^\s*(bash|sh)\s+\S*setup_vps", code, re.M)


def test_shellcheck_clean():
    exe = shutil.which("shellcheck") or str(Path(sys.executable).with_name("shellcheck"))
    if not os.access(exe, os.X_OK):
        pytest.skip("shellcheck yok (betiğin --dry-run'ı VPS'te varsa koşar)")
    cp = subprocess.run([exe, str(SCRIPT)], capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stdout + cp.stderr


# ============================================================================ sandbox
SYSTEMCTL = r'''#!@PY@
import json, os, sys, pathlib, hashlib
if not os.environ.get("FAKE_STATE"):      # motorun S0'ı kısıtlı ortamla `is-active tradingbot-backup.service` sorar
    print("inactive"); sys.exit(3)
F = pathlib.Path(os.environ["FAKE_STATE"]); SD = pathlib.Path(os.environ["TRADINGBOT_SYSTEMD_DIR"])
BASE = pathlib.Path(os.environ["TRADINGBOT_BASE"])
W, DSH, SVC, TMR = "tradingbot-worker.service", "tradingbot-dashboard.service", "tradingbot-engine-night.service", \
    "tradingbot-engine-night.timer"
args = sys.argv[1:]
log = F / "systemctl.log"
def L(s):
    with log.open("a", encoding="utf-8") as fh:
        fh.write(s + "\n")
L(" ".join(args))
sp = F / "systemd.json"
S = json.loads(sp.read_text()) if sp.exists() else {}
def save():
    sp.write_text(json.dumps(S))
MUT = {"start", "stop", "restart", "try-restart", "reload-or-restart", "reload", "kill", "enable", "disable", "mask",
       "set-property", "edit", "revert"}
verb = args[0] if args else ""
tg = [a for a in args[1:] if not a.startswith("-")]
if verb == "daemon-reexec" or (verb in MUT and any(t.startswith(("tradingbot-worker", "tradingbot-dashboard")) for t in tg)):
    L("FORBIDDEN " + " ".join(args)); sys.exit(99)
def fsha(u):
    p = SD / u
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def reload():
    S["loaded"] = {u: fsha(u) for u in (SVC, TMR) if (SD / u).exists()}
    if os.environ.get("FAKE_RELOAD_DRIFT"):
        S.setdefault("props", {}).setdefault(W, {})["MemoryMax"] = "4294967296"
    save()
def prop(u, p):
    v = S.get("props", {}).get(u, {}).get(p)
    if v is not None:
        return v
    if u == W:
        return {"NeedDaemonReload": os.environ.get("FAKE_NDR_WORKER", "no"), "MemoryMax": "6442450944",
                "NRestarts": "0", "MainPID": "4242"}.get(p, "")
    if u == DSH:
        return {"NeedDaemonReload": "no", "MemoryMax": "536870912"}.get(p, "")
    if u in (SVC, TMR):
        ld = S.get("loaded", {})
        if p == "LoadState":
            return "loaded" if u in ld else "not-found"
        if p == "NeedDaemonReload":
            return "no" if ld.get(u) == fsha(u) else "yes"
        if p == "MemoryMax":
            return "536870912" if u == SVC and u in ld else "infinity"
        if p == "Result":
            return S.get("result", "success")
        if p == "ExecMainStatus":
            return str(S.get("ems", 0))
        if p == "NextElapseUSecRealtime":
            return "Tue 2026-10-06 04:37:00 +03" if S.get("enabled") else ""
    return ""
if verb == "show":
    print(prop(args[1], args[args.index("-p") + 1])); sys.exit(0)
if verb == "daemon-reload":
    reload(); sys.exit(0)
if verb == "is-enabled":
    st = ("enabled" if S.get("enabled") else "disabled") if (SD / TMR).exists() else "not-found"
    print(st); sys.exit(0 if st == "enabled" else 1)
if verb == "is-active":
    u = tg[0]
    st = "active" if u == W or (u == TMR and S.get("timer_active")) else "inactive"
    print(st); sys.exit(0 if st == "active" else 3)
if verb == "cat":
    sys.exit(0 if (tg[0] == "tradingbot-alert@.service" and os.environ.get("FAKE_ALERT")) else 1)
if verb in ("enable", "disable"):
    if tg[0] != TMR or (verb == "enable" and TMR not in S.get("loaded", {})):
        sys.exit(1)
    S["enabled"] = verb == "enable"
    if "--now" in args:
        S["timer_active"] = verb == "enable"
    save()
    if "--no-reload" not in args:
        reload()
    sys.exit(0)
if verb == "stop":
    if tg[0] == TMR:
        S["timer_active"] = False; save()
    sys.exit(0)
if verb == "reset-failed":
    sys.exit(0)
if verb == "start" and tg[0] == SVC:
    if SVC not in S.get("loaded", {}):
        print("Unit not found"); sys.exit(5)
    r = BASE / "data" / "research"
    L("START research=%s mode=%s uid=%s" % (r.is_dir(), oct(r.stat().st_mode & 0o777) if r.exists() else None,
                                           r.stat().st_uid if r.exists() else None))
    mode = os.environ.get("FAKE_SMOKE", "ok")
    if mode == "namespace":
        S.update(result="exit-code", ems=226); save(); sys.exit(1)
    sys.dont_write_bytecode = True
    eng = BASE / "engine-app"
    sys.path.insert(0, str(eng)); os.chdir(eng)
    from tradingbot.research_engine import night as N, selfcheck as SC
    from tradingbot.research_engine.paths import EnginePaths
    sock = "CONNECTED" if mode == "fail" else "DENIED"
    pr = SC.Probes(write_denied=lambda d: {"path": str(d), "status": "DENIED", "errno": "EROFS"},
                   socket_denied=lambda: {"status": sock, "errno": "ENETUNREACH"},
                   memory_max=lambda env: {"status": "MATCH", "expected": 536870912, "actual": 536870912},
                   writable=lambda d: {"path": str(d), "status": "WRITABLE_OK"})
    st = N.run_night(EnginePaths(data=BASE / "data", state=BASE / "data" / "state"), app_dir=BASE / "app",
                     engine_dir=eng, env={"ALLOW_LIVE_TRADING": "false"}, probes=pr, backup_wait_s=0,
                     free_bytes=int(os.environ.get("FAKE_FREE_BYTES", "50000000000")))   # disk: sunucudan bağımsız
    rc = int(st.get("exit_code") or 0)
    S.update(result="success" if rc == 0 else "exit-code", ems=rc); save()
    sys.exit(0 if rc == 0 else 1)
sys.exit(0)
'''
#: `journalctl -u U --since @S [--until @E] -o short-unix …`: FAKE_JOURNAL'daki satırları zaman damgasına göre süzer.
JOURNALCTL = r'''#!@PY@
import os, sys
a = sys.argv[1:]
f = os.environ.get("FAKE_JOURNAL")
since = float(a[a.index("--since") + 1].lstrip("@")) if "--since" in a else 0.0
until = float(a[a.index("--until") + 1].lstrip("@")) if "--until" in a else float("inf")
if f and "short-unix" in a:
    for ln in open(f, encoding="utf-8"):
        if since <= float(ln.split(" ", 1)[0]) < until:
            sys.stdout.write(ln)
'''
STUBS = {
    "sudo": '#!/usr/bin/env bash\necho "sudo $*" >> "$FAKE_STATE/sudo.log"\n[ "$1" = "-u" ] && shift 2\nexec "$@"\n',
    # betiğin disk kapısı (`df -B1 --output=avail`): sunucunun gerçek boş alanından bağımsız (FAKE_FREE_BYTES)
    "df": '#!/usr/bin/env bash\nif [ -n "${FAKE_FREE_BYTES:-}" ] && [ "$1" = "-B1" ] && [ "$2" = "--output=avail" ]; then\n'
          '  printf "Avail\\n%s\\n" "$FAKE_FREE_BYTES"; exit 0; fi\nexec @DF@ "$@"\n',
    "nproc": '#!/usr/bin/env bash\necho "${FAKE_NPROC:-4}"\n',
    "date": '#!/usr/bin/env bash\nif [ -n "${FAKE_UTC_HM:-}" ] && [ "$*" = "-u +%H:%M" ]; then echo "$FAKE_UTC_HM"; exit 0; fi\n'
            'exec @DATE@ "$@"\n',
}


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """Yerel çıplak kaynak depo: claude/gifted-knuth-0ehpcs = hedef, app-main = VPS app'i (f8b05fb)."""
    if not (GIT and _has(TIP) and _has(APP_SHA)):
        pytest.skip("hedef/app commit'leri yok")
    d = tmp_path_factory.mktemp("src")
    src = d / "src.git"
    assert _git("init", "-q", "--bare", str(src)).returncode == 0
    assert _git("fetch", "-q", "--no-tags", str(ROOT), "+HEAD:refs/heads/scratch", cwd=src).returncode == 0
    assert _git("update-ref", "refs/heads/claude/gifted-knuth-0ehpcs", TIP, cwd=src).returncode == 0
    assert _git("update-ref", "refs/heads/app-main", APP_SHA, cwd=src).returncode == 0
    assert _git("update-ref", "-d", "refs/heads/scratch", cwd=src).returncode == 0
    return src


def _tree_digest(p: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(x for x in p.rglob("*")):
        st = f.lstat()
        h.update(f"{f.relative_to(p)}|{oct(st.st_mode)}|{st.st_uid}".encode())
        if f.is_file():
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


class Sandbox:
    def __init__(self, tmp: Path, src: Path, **env):
        from research_engine_fixtures import FakeVps, at, trade
        self.tmp, self.base, self.sd, self.fake = tmp, tmp / "tb base", tmp / "systemd", tmp / "fake"
        stub = tmp / "bin"
        for d in (self.base / "venv" / "bin", self.sd, self.fake, stub):
            d.mkdir(parents=True)
        py = self.base / "venv" / "bin" / "python"
        py.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        (stub / "systemctl").write_text(SYSTEMCTL.replace("@PY@", sys.executable), encoding="utf-8")
        (stub / "journalctl").write_text(JOURNALCTL.replace("@PY@", sys.executable), encoding="utf-8")
        for name, body in STUBS.items():
            (stub / name).write_text(body.replace("@DATE@", shutil.which("date") or "/bin/date")
                                     .replace("@DF@", shutil.which("df") or "/bin/df"), encoding="utf-8")
        for p in [py, *stub.iterdir()]:
            p.chmod(0o755)
        assert _git("clone", "-q", "--no-local", "--single-branch", "--branch", "app-main", str(src),
                    str(self.base / "app")).returncode == 0
        v = FakeVps(self.base)
        led, book = v.fut(""), v.fut("b1_box_fade")
        trade(led, "BTCUSDT", at("2026-09-01", 4), 100.0, 102.0)
        trade(book, "ETHUSDT", at("2026-09-01", 6), 50.0, 49.5, side="SHORT")
        sp = v.spot()
        sp.market_buy("ETH/USDT", qty=D("1"), ref_price=D("100"), now=at("2026-09-01", 4))
        sp.market_sell("ETH/USDT", qty=D("0.4"), ref_price=D("103"), now=at("2026-09-01", 8))
        v.save(at("2026-09-02", 1))
        self.state = v.state
        (self.state / "mode.json").write_text('{"mode": "PAPER", "live_order_path_enabled": false}', encoding="utf-8")
        (self.state / "box_timer.json").write_text('{"missed_bars": 3, "evaluations": 900, "lag_p50_s": 9.0}', encoding="utf-8")
        (self.state / "protective_monitor.json").write_text('{"runs": 50, "duration_p50_s": 1.5}', encoding="utf-8")
        (self.state / "strategy_paper.json").write_text(
            '{"counters": {"tours": 40, "data_rejected": 2}, "rejections": {"DATA_VERDICT_MISSING": 1}}', encoding="utf-8")
        self.res = self.base / "data" / "research"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("TRADINGBOT_", "ENGINE_", "FAKE_", "TB_"))}
        self.env.update({"PATH": f"{stub}:{os.environ.get('PATH', '/usr/bin:/bin')}", "TRADINGBOT_BASE": str(self.base),
                         "TRADINGBOT_SYSTEMD_DIR": str(self.sd), "TRADINGBOT_USER": pwd.getpwuid(os.getuid()).pw_name,
                         "TRADINGBOT_GROUP": grp.getgrgid(os.getgid()).gr_name, "TB_ENGINE_ALLOW_NON_ROOT": "1",
                         "TB_ENGINE_REPO_URL": str(src), "FAKE_STATE": str(self.fake), "FAKE_UTC_HM": "10:00",
                         "FAKE_NPROC": "4", "FAKE_FREE_BYTES": "50000000000", **env})

    def run(self, *args: str, **env) -> subprocess.CompletedProcess:
        cp = subprocess.run([BASH, str(SCRIPT), *args], env={**self.env, **env}, cwd=str(self.tmp), capture_output=True,
                            text=True, timeout=900)
        cp.out = cp.stdout + cp.stderr
        return cp

    def log(self) -> list[str]:
        p = self.fake / "systemctl.log"
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def unit_files(self) -> list[str]:
        return sorted(p.name for p in self.sd.rglob("*") if p.is_file())

    def untouched(self) -> None:
        assert not (self.base / "engine-app").exists() and not self.res.exists() and not self.unit_files()
        assert not [ln for ln in self.log() if ln.split()[0] in ("daemon-reload", "start", "enable", "disable", "stop")]


def _no_forbidden(sb: Sandbox) -> None:
    assert not [ln for ln in sb.log() if ln.startswith("FORBIDDEN")], "betik worker/dashboard'a dokundu"


def _at(log: list[str], prefix: str) -> int:
    return next(i for i, ln in enumerate(log) if ln.startswith(prefix))


@needs_sandbox
def test_dry_run_checks_everything_and_changes_nothing(tmp_path, source):
    sb = Sandbox(tmp_path, source)
    # başka sürümün yeniden başlatması 3 gün + 10 dk önce: pencere dışı (2026-10-06 sahip kararı; önceden 7 gün)
    (sb.base / "deploy-logs").mkdir()
    (sb.base / "deploy-logs" / "8db1faf-restart-at.txt").write_text(f"{int(time.time()) - 3 * 86400 - 600}\nx\n")
    before = len(list(Path("/tmp").glob("tb-engine-*")))
    cp = sb.run("--dry-run")
    assert cp.returncode == 0, cp.out[-6000:]
    m = re.search(r"KURU ÇALIŞMA: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "17", cp.out[-3000:]
    for name in ("kaynak-TIP", "app-SHA-ata", "birim-sha256", "birim-sözleşmesi", "systemd-analyze-verify", "compileall",
                 "bağımsız-koşucu", "yalıtım-AST", "engine-status-kuru", "worker-MemoryMax=6G", "tradingbot-worker-NDR=no",
                 "1g-pencere-dışı"):
        assert re.search(rf"\[tamam\] #\d+ {re.escape(name)}", cp.out), name
    assert "8db1faf-restart-at.txt 3 gün önce (≥ 3;" in cp.out, cp.out[-3000:]
    assert "33 geçti · 0 kaldı · 0 atlandı" in cp.out
    sb.untouched()
    _no_forbidden(sb)
    assert len(list(Path("/tmp").glob("tb-engine-*"))) == before, "geçici klon silinmeli"


@needs_sandbox
def test_deploy_smoke_then_timer_check_ab_report_and_rollback_keeps_research(tmp_path, source):
    sb = Sandbox(tmp_path, source)
    cp = sb.run()
    assert cp.returncode == 0, cp.out[-6000:]
    m = re.search(r"DAĞITILDI: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "25", cp.out[-3000:]
    log = sb.log()
    _no_forbidden(sb)
    # §9.3 sırası: birimler → kapılı reload → elle smoke → ancak sonra enable --now
    i_reload, i_start, i_enable = _at(log, "daemon-reload"), _at(log, f"start {SVC}"), _at(log, f"enable --now {TMR}")
    assert i_reload < i_start < i_enable
    # data/research İLK başlatmadan ÖNCE vardı: servis kullanıcısına ait, 0750
    pre = next(ln for ln in log if ln.startswith("START "))
    assert f"research=True mode=0o750 uid={os.getuid()}" in pre
    for d in ("locks", "backup"):
        assert (sb.res / d).is_dir() and (sb.res / d).stat().st_mode & 0o777 == 0o750
    # kurulan birimler hedef commit'teki dosyalarla bayt bayt aynı; nproc ≥ 4 ve uyarı birimi yok → drop-in yok
    for u in (SVC, TMR):
        assert (sb.sd / u).read_bytes() == subprocess.run([GIT, "show", f"{TIP}:deploy/{u}"], cwd=str(ROOT),
                                                           capture_output=True).stdout
    assert sb.unit_files() == sorted([SVC, TMR])
    assert _git("rev-parse", "HEAD", cwd=sb.base / "engine-app").stdout.strip() == TIP
    st = json.loads((sb.res / "summary" / "run_status.json").read_text(encoding="utf-8"))
    assert st["result"] == "SUCCESS" and st["selfcheck"]["status"] == "OK" and st["shas"]["engine"] == TIP
    assert st["shas"]["relation"] == "ANCESTOR"
    assert list((sb.res / "backup").glob("research-small-*.tar.gz"))
    assert all(p.lstat().st_uid == os.getuid() for p in sb.res.rglob("*"))
    base = json.loads((sb.base / "deploy-logs" / f"engine-{TIP[:7]}-deploy.json").read_text(encoding="utf-8"))
    assert base["tip"] == TIP and base["smoke_run_id"] == st["run_id"] and base["worker"]["nrestarts"] == 0

    # --check / --ab-report: araştırma ağacı değişmez, yalnız örnek eklenir; yalnız-gerçekleşmiş satırda iddia kelimesi yok
    from tradingbot.research_engine.summary import claim_word_violations
    samples = sb.base / "deploy-logs" / f"engine-{TIP[:7]}-samples.jsonl"
    d0, n0 = _tree_digest(sb.res), len(samples.read_text(encoding="utf-8").splitlines())
    ck = sb.run("--check")
    assert ck.returncode == 0, ck.out[-4000:]
    for s in ("iki SHA: app f8b05fb · engine-app 4962209", "SKEW yok", "GECE ÖĞRENME MOTORU", "GÜNLÜK HEDEF",
              "K1 elle smoke", "K2 ilk arşiv", "K8 rotasyon payı", "--ab-report", "defter okuma", "Aylık"):
        assert s in ck.out, s
    assert not claim_word_violations(ck.out.splitlines()), claim_word_violations(ck.out.splitlines())
    assert "[tamam]       K1" in ck.out and "[tamam]       K2" in ck.out
    assert re.search(r"\[tamam\]\s+K6 scorecard --daily = engine-status --daily: 3 defter × \d+ gün", ck.out), ck.out[-3000:]
    assert re.search(r"\[DİKKAT\]\s+defter okuma: LEDGER_STALE", ck.out), "sahte state'in ledger'ları eski (09-02)"
    assert not list(Path("/tmp").glob("tb-engine-k6.*")), "K6 geçici klasörü silinir"
    ab = sb.run("--ab-report")
    assert ab.returncode == 0 and "A/B geceleri" in ab.out and "ARA GÖRÜNÜM" in ab.out, ab.out[-3000:]
    assert not claim_word_violations(ab.out.splitlines())
    assert _tree_digest(sb.res) == d0
    assert len(samples.read_text(encoding="utf-8").splitlines()) == n0 + 1
    assert json.loads(samples.read_text(encoding="utf-8").splitlines()[-1])["box_missed"] == 3

    again = sb.run()
    assert again.returncode == 0 and "zaten dağıtılmış" in again.out, again.out[-3000:]

    rb = sb.run("--rollback")
    assert rb.returncode == 0, rb.out[-4000:]
    _no_forbidden(sb)
    assert not sb.unit_files() and not (sb.base / "engine-app").exists()
    assert _tree_digest(sb.res) == d0, "geri alma data/research'e dokunmamalı"
    tail = sb.log()[len(log):]
    assert f"stop {TMR}" in tail and f"disable --no-reload {TMR}" in tail
    assert tail.index(f"disable --no-reload {TMR}") < tail.index("daemon-reload")

    # aynı SHA'nın A/B penceresinde YENİDEN dağıtım (sürüm günü dün): bugün KAPALI gün ise smoke yalnız S0'dır ve kabul
    # edilir (AB_OFF), AÇIK gün ise tam plan; ikisinde de zamanlayıcı açılır
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    ep = sb.res / "runs" / "engine_epochs.jsonl"
    rows = [json.loads(x) for x in ep.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows[0]["first_seen_day"] = rows[0]["epoch_day"] = (today - timedelta(days=1)).isoformat()
    ep.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    re2 = sb.run()
    assert re2.returncode == 0 and "DAĞITILDI" in re2.out, re2.out[-4000:]
    st2 = json.loads((sb.res / "summary" / "run_status.json").read_text(encoding="utf-8"))
    if today.timetuple().tm_yday % 2 == 0:
        assert st2["result"] == "SUCCESS" and st2["selfcheck"]["ab"]["status"] == "AB_ON"
    else:
        assert st2["result"] == st2["selfcheck"]["ab"]["status"] in ("AB_OFF", "AB_OFF_ZORUNLU_ARŞİV")
    _no_forbidden(sb)


@needs_sandbox
@pytest.mark.parametrize("hm", ["02:10", "16:20"])
def test_dry_run_is_refused_inside_the_night_and_4h_windows(tmp_path, source, hm):
    """Kuru çalışma derler ve 33 testlik koşucuyu koşar: gece birimi penceresinde (00:00–04:00 UTC) ve 4h yayın
    pencerelerinde worker turlarıyla çekişmesin diye başlamaz (§2.9); ağır adımlar her durumda nice 19 + ionice idle."""
    sb = Sandbox(tmp_path, source, FAKE_UTC_HM=hm)
    cp = sb.run("--dry-run")
    assert cp.returncode == 1 and "kuru çalışma da şimdi yapılmaz" in cp.out and "HİÇBİR ŞEYE DOKUNULMADI" in cp.out, cp.out[-2000:]
    assert "bağımsız-koşucu" not in cp.out, "koşucu hiç başlamadı"
    sb.untouched()
    _no_forbidden(sb)


def test_heavy_steps_run_at_lowest_cpu_and_io_priority():
    assert re.search(r'^LOW=\(nice -n 19\); if command -v ionice .* LOW\+=\(ionice -c3 -t\)', TEXT, re.M)
    for step in ('-s -m compileall -q tradingbot scripts', '-s tests/standalone/run_engine_invariants.py',
                 "-s -c 'import resource as r, sys, tradingbot.cli", '-s scripts/bot_scorecard.py'):
        lines = [ln for ln in TEXT.splitlines() if step in ln and "$VENV/bin/python" in ln]
        assert lines and all('"${LOW[@]}" "$VENV/bin/python"' in ln for ln in lines), step


def test_k6_compares_record_and_wallet_views_up_to_archive_day_minus_two(tmp_path):
    days = [f"2026-10-0{d}" for d in range(1, 6)]

    def book(net2: float, wal4: float = 0.5) -> dict:
        return {"days": {d: {"rec": {"n": 1, "net": net2 if d == "2026-10-02" else 0.25, "fees": 0.1, "funding": 0.0,
                                     "slippage": 0.0},
                             "wal_ts": {"net": wal4 if d == "2026-10-04" else 0.5, "transfer": 0.0, "complete": True}}
                         for d in days}}

    def run(eb: dict, sb: dict, *, upto: str = "2026-10-05") -> str:
        e, s_ = tmp_path / "e.json", tmp_path / "s.json"
        e.write_text(json.dumps({"days": days, "archived_through": upto, "books": eb}), encoding="utf-8")
        s_.write_text(json.dumps({"daily_target": {"days": days, "books": sb}}), encoding="utf-8")
        cp = _tool(tmp_path, "k6", str(e), str(s_))
        assert cp.returncode == 0, cp.stderr
        return cp.stdout
    out = run({"main_fut": book(1.0)}, {"main_fut": book(1.0)})
    assert "[tamam]" in out and "1 defter × 3 gün (≤ 2026-10-03; arşiv 2026-10-05'e kadar)" in out, out
    out = run({"main_fut": book(1.0)}, {"main_fut": book(1.5)})
    assert "[DİKKAT]" in out and "main_fut 2026-10-02 kayıt.net 1.0≠1.5" in out, out
    out = run({"main_fut": book(1.0)}, {"main_fut": book(1.0, wal4=9.0)})
    assert "[tamam]" in out, "arşiv günü − 2'den sonraki fark (geç fonlama/bu geceki kapanış) karşılaştırılmaz"
    out = run({"main_fut": book(1.0), "main_spot": book(0.0)}, {"main_fut": book(1.0)})
    assert "[DİKKAT]" in out and "main_spot: betikte yok" in out
    out = run({"main_fut": book(1.0)}, {"main_fut": book(1.0), "strategy_paper": book(0.0)})
    assert "[DİKKAT]" in out and "strategy_paper: motorda yok" in out
    assert "[ölçülemedi]" in run({}, {"main_fut": book(1.0)}) and "[ölçülemedi]" in run({"main_fut": book(1.0)}, {}, upto=None)


@needs_sandbox
@pytest.mark.parametrize("smoke", ["fail", "namespace"])
def test_failed_smoke_never_enables_the_timer(tmp_path, source, smoke):
    extra = {"FAKE_ALERT": "1", "FAKE_NPROC": "2"} if smoke == "namespace" else {}
    sb = Sandbox(tmp_path, source, FAKE_SMOKE=smoke, **extra)
    cp = sb.run()
    assert cp.returncode == 2, cp.out[-4000:]
    assert "zamanlayıcı ETKİNLEŞTİRİLMEDİ" in cp.out and "[KALDI]" in cp.out
    log = sb.log()
    assert any(ln.startswith(f"start {SVC}") for ln in log)
    assert not [ln for ln in log if ln.startswith("enable")], "smoke geçmeden zamanlayıcı açılmaz"
    assert sb.res.is_dir()
    _no_forbidden(sb)
    if smoke == "namespace":
        assert "226/NAMESPACE" in cp.out
        # uyarı birimi VAR ve nproc < 4 → drop-in: OnFailure + CPUQuota=60% (§2.5)
        assert sb.unit_files() == sorted([SVC, TMR, "50-tb-engine.conf"])
        dropin = (sb.sd / f"{SVC}.d" / "50-tb-engine.conf").read_text(encoding="utf-8")
        assert "OnFailure=tradingbot-alert@%n.service" in dropin and "CPUQuota=60%" in dropin
    else:
        assert sb.unit_files() == sorted([SVC, TMR])
        st = json.loads((sb.res / "summary" / "run_status.json").read_text(encoding="utf-8"))
        assert st["result"] == "ISOLATION_BROKEN" and "socket_denied" in st["selfcheck"]["isolation"]["broken"]


@needs_sandbox
def test_memorymax_drift_after_reload_rolls_units_back_and_stops(tmp_path, source):
    sb = Sandbox(tmp_path, source, FAKE_RELOAD_DRIFT="1")
    cp = sb.run()
    assert cp.returncode == 3, cp.out[-4000:]
    assert "GERİ ALINDI" in cp.out and "set-property" in cp.out
    log = sb.log()
    assert not [ln for ln in log if ln.startswith(("start", "enable"))]
    assert not sb.unit_files()
    _no_forbidden(sb)


@needs_sandbox
@pytest.mark.parametrize("case", ["ndr", "night_window", "4h_window", "release_window", "not_paper"])
def test_unsafe_preconditions_refuse_before_any_change(tmp_path, source, case):
    env = {"ndr": {"FAKE_NDR_WORKER": "yes"}, "night_window": {"FAKE_UTC_HM": "02:10"},
           "4h_window": {"FAKE_UTC_HM": "16:20"}}.get(case, {})
    sb = Sandbox(tmp_path, source, **env)
    if case == "release_window":
        (sb.base / "deploy-logs").mkdir()
        # 3 günden 10 dk eksik: hâlâ pencere içinde (2026-10-06 sahip kararı: 3 gün; önceden 7 gün)
        (sb.base / "deploy-logs" / "34ae8d2-restart-at.txt").write_text(f"{int(time.time()) - 1 * 86400 + 600}\nx\n")
    if case == "not_paper":
        (sb.state / "mode.json").write_text('{"mode": "LIVE"}', encoding="utf-8")
    cp = sb.run()
    assert cp.returncode == 1 and "HİÇBİR ŞEYE DOKUNULMADI" in cp.out, cp.out[-3000:]
    if case == "release_window":
        assert "DUR: 1g-pencere-dışı" in cp.out and "(≥ 1;" in cp.out, cp.out[-3000:]
    sb.untouched()
    _no_forbidden(sb)


@needs_sandbox
def test_app_not_ancestor_of_target_refuses(tmp_path, source):
    sb = Sandbox(tmp_path, source)
    app = sb.base / "app"
    (app / "yerel.txt").write_text("x", encoding="utf-8")
    assert _git("add", "yerel.txt", cwd=app).returncode == 0
    assert _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "yerel", cwd=app).returncode == 0
    cp = sb.run()
    assert cp.returncode == 1 and "app-SHA-ata" in cp.out and "HİÇBİR ŞEYE DOKUNULMADI" in cp.out, cp.out[-3000:]
    sb.untouched()


# ============================================================================ gömülü araçlar (deterministik, dağıtımsız)
def _pytool() -> str:
    a = TEXT.index("read -r -d '' PYTOOL <<'PY' || true\n") + len("read -r -d '' PYTOOL <<'PY' || true\n")
    return TEXT[a:TEXT.index("\nPY\nrpy()", a)]


def _tool(tmp: Path, *args: str, journal: Path | None = None) -> subprocess.CompletedProcess:
    stub = tmp / "jbin"
    stub.mkdir(exist_ok=True)
    (stub / "journalctl").write_text(JOURNALCTL.replace("@PY@", sys.executable), encoding="utf-8")
    (stub / "journalctl").chmod(0o755)
    env = {"PATH": f"{stub}:/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": "UTC"}
    if journal is not None:
        env["FAKE_JOURNAL"] = str(journal)
    return subprocess.run([sys.executable, "-I", "-c", _pytool(), *args], env=env, capture_output=True, text=True,
                          timeout=120)


def _run_status(res: Path, rid: str, started: str, *, result="SUCCESS", plan=("S0", "S1a", "S3", "S7", "S7b"),
                ab="AB_RELEASE_DAY", engine=TIP, sc_status="OK", peak=0.3) -> dict:
    st = {"schema": "engine_run_status_v1", "run_id": rid, "started_at": started, "result": result,
          "exit_code": 0 if result in ("SUCCESS", "AB_OFF", "AB_OFF_ZORUNLU_ARŞİV", "SKEW") else 1, "plan": list(plan),
          "stages": {s: {"status": "OK"} for s in plan}, "flags": [], "shas": {"engine": engine, "relation": "ANCESTOR"},
          "selfcheck": {"status": sc_status, "ab": {"status": ab}}, "resources": {"memory_peak_ratio": peak}}
    (res / "runs" / rid).mkdir(parents=True, exist_ok=True)
    (res / "runs" / rid / "run_status.json").write_text(json.dumps(st), encoding="utf-8")
    (res / "summary").mkdir(parents=True, exist_ok=True)
    (res / "summary" / "run_status.json").write_text(json.dumps(st), encoding="utf-8")
    return st


def _epoch(res: Path, day: str) -> None:
    (res / "runs").mkdir(parents=True, exist_ok=True)
    (res / "runs" / "engine_epochs.jsonl").write_text(json.dumps({"engine_sha": TIP, "first_seen_day": day}) + "\n",
                                                       encoding="utf-8")


def _files(res: Path, day: str, plan) -> None:
    names = {"S1s": [f"snapshots/{day}.json.gz"], "S1a": [f"snapshots/{day}.json.gz"], "S3": ["summary/daily_target.json"],
             "S7": ["summary/digest_tr.md", "summary/engine_summary.json"],
             "S7b": [f"backup/research-small-{day}.tar.gz", f"backup/research-small-{day}.tar.gz.sha256"]}
    for s in plan:
        for n in names.get(s, []):
            (res / n).parent.mkdir(parents=True, exist_ok=True)
            (res / n).write_text("x", encoding="utf-8")


def test_smoke_acceptance_rules(tmp_path):
    user = pwd.getpwuid(os.getuid()).pw_name
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    full = ("S0", "S1a", "S3", "S7", "S7b")

    def case(name, *, plan=full, result="SUCCESS", ab="AB_RELEASE_DAY", files=full, sc_status="OK", pre="",
             t0=now - 1, engine=TIP):
        res = tmp_path / name / "research"
        _epoch(res, day)
        _run_status(res, "R1", started, result=result, plan=plan, ab=ab, sc_status=sc_status, engine=engine)
        _files(res, day, files)
        return _tool(tmp_path, "smoke", str(res), TIP, pre, str(t0), user)

    assert case("ok").returncode == 0
    # A/B penceresinde yeniden dağıtım, KAPALI gün: S0 + S1s planı ve sonucu AB_OFF → kabul (anlık görüntü şart)
    assert case("ab_off", plan=("S0", "S1s"), result="AB_OFF", ab="AB_OFF", files=("S0", "S1s")).returncode == 0
    assert case("ab_forced", plan=("S0", "S1s", "S1a"), result="AB_OFF_ZORUNLU_ARŞİV", ab="AB_OFF_ZORUNLU_ARŞİV",
                files=("S0", "S1s", "S1a")).returncode == 0
    for name, kw in (("skew", {"plan": ("S0", "S1a", "S7"), "result": "SKEW", "files": ("S0", "S1a", "S7")}),
                     ("no_backup", {"files": ("S0", "S1a", "S3", "S7")}),
                     ("iso", {"result": "ISOLATION_BROKEN", "sc_status": "ISOLATION_BROKEN"}),
                     ("stale", {"pre": "R1"}), ("old", {"t0": now + 600}),
                     ("other_sha", {"engine": "c" * 40}),
                     ("ab_off_mismatch", {"plan": ("S0", "S1s"), "result": "AB_OFF", "ab": "AB_ON", "files": ("S0", "S1s")}),
                     ("ab_off_no_snapshot", {"plan": ("S0", "S1s"), "result": "AB_OFF", "ab": "AB_OFF", "files": ("S0",)})):
        cp = case(name, **kw)
        assert cp.returncode == 1 and "KALDI:" in cp.stdout, (name, cp.stdout + cp.stderr)


def _ab_fixture(tmp_path: Path, on_tour_s: float, *, rest_on: int = 0) -> tuple[Path, Path, Path, Path]:
    """Sürümden 16 gün önce; 14 gece: gün-yıl sırası çiftse AÇIK (tam plan), tekse KAPALI (yalnız S0)."""
    from datetime import datetime, timedelta, timezone
    res = tmp_path / "research"
    rel = (datetime.now(timezone.utc) - timedelta(days=16)).replace(hour=0, minute=0, second=0, microsecond=0)
    _epoch(res, rel.strftime("%Y-%m-%d"))
    jl, samples = [], []
    t_prev = rel + timedelta(hours=9)
    samples.append({"t": int(t_prev.timestamp()), "box_missed": 0, "tours": 0, "data_rejected": 0, "pm_p50": 1.0,
                    "worker_pid": 7})
    missed = tours = drej = 0
    for i in range(1, 15):
        d0 = rel + timedelta(days=i)
        on = d0.timetuple().tm_yday % 2 == 0
        _run_status(res, d0.strftime("%Y%m%dT013700Z"), d0.strftime("%Y-%m-%dT01:37:00Z"),
                    result="SUCCESS" if on else "AB_OFF", plan=("S0", "S1a", "S3", "S7", "S7b") if on else ("S0", "S1s"),
                    ab="AB_ON" if on else "AB_OFF", peak=0.35 if on else 0.05)
        for k in range(6):
            t = d0 + timedelta(hours=1, minutes=45 + 20 * k)
            jl.append(f"{t.timestamp():.6f} vps python3[1]: 🎓 ÖĞRENME: 5 işlem · kazanma %50 · "
                      f"{(on_tour_s if on else 320.0) + k:.1f}s")
        jl.append(f"{(d0 + timedelta(hours=2)).timestamp():.6f} vps python3[1]: 12:00:00 INFO tradingbot.engine_v3: "
                  "araştırma adayı PAPER_RESEARCH_ACTIVE: ['x']")
        if on and rest_on:
            jl.append(f"{(d0 + timedelta(hours=2, minutes=5)).timestamp():.6f} vps python3[1]: 12:00:00 WARNING "
                      "tradingbot.market.http: 429 https://fapi.binance.com/fapi/v1/klines")
        jl.append(f"{(d0 + timedelta(hours=12)).timestamp():.6f} vps python3[1]: 🎓 ÖĞRENME: gündüz turu · 999.0s")
        missed, tours, drej = missed + (1 if not on else 1), tours + 72, drej + 2
        samples.append({"t": int((d0 + timedelta(hours=9)).timestamp()), "box_missed": missed, "tours": tours,
                        "data_rejected": drej, "pm_p50": 1.0, "worker_pid": 7})
    jf = tmp_path / "journal.txt"
    jf.write_text("\n".join(jl) + "\n", encoding="utf-8")
    sf = tmp_path / "samples.jsonl"
    sf.write_text("".join(json.dumps(x) + "\n" for x in samples), encoding="utf-8")
    base = tmp_path / "deploy.json"
    base.write_text(json.dumps({"tip": TIP, "worker": {"nrestarts": 0, "pid": 7, "memmax": 6442450944}}), encoding="utf-8")
    return res, jf, sf, base


def test_ab_report_groups_nights_in_the_same_window_and_applies_the_criteria(tmp_path):
    from tradingbot.research_engine.summary import claim_word_violations
    res, jf, sf, base = _ab_fixture(tmp_path / "ok", 330.0)
    cp = _tool(tmp_path, "ab", str(res), TIP, str(base), str(sf), "6442450944", "0", "536870912", journal=jf)
    out = cp.stdout
    assert cp.returncode == 0, out + cp.stderr
    assert "14/14 gece geçti" in out and "ARA GÖRÜNÜM" not in out
    assert "AÇIK 7 gece · KAPALI 7 gece" in out
    # gündüz turları (999 sn) pencere dışında: p95 AÇIK 335 ≤ KAPALI 325 × 1,05
    assert re.search(r"\[GEÇTİ\]\s+tur p50/p95 \(sn\)\s+AÇIK 332/335 \(42 tur\) · KAPALI 322/325 \(42 tur\)", out), out
    assert re.search(r"\[GEÇTİ\]\s+418/429", out) and re.search(r"\[bilgi\]\s+PAPER_RESEARCH_ACTIVE\s+AÇIK 7 · KAPALI 7", out)
    assert re.search(r"\[GEÇTİ\]\s+motor memory.peak\s+AÇIK gecelerde en çok 0,35", out)
    assert re.search(r"\[GEÇTİ\]\s+Box kaçan bar/saat\s+KABA", out) and "(7 aralık)" in out
    assert ">>> en az bir ölçüt KALDI" not in out and not claim_word_violations(out.splitlines())
    res2, jf2, sf2, base2 = _ab_fixture(tmp_path / "slow", 400.0, rest_on=1)
    cp2 = _tool(tmp_path, "ab", str(res2), TIP, str(base2), str(sf2), "4294967296", "1", "536870912", journal=jf2)
    out2 = cp2.stdout
    for name in ("tur p50/p95", "418/429", "worker NRestarts", "worker MemoryMax"):
        assert re.search(rf"\[KALDI\]\s+{re.escape(name)}", out2), (name, out2)
    assert ">>> en az bir ölçüt KALDI" in out2
