# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1b — motor sürüm betiği `deploy/releases/tb-engine-<sha7>.sh` (docs/SYSTEM_LEARNING_ENGINE_V1.md
§2.2–§2.5, §3.4, §9.3, §9.4, §10 P1b).

Betik SAHTE bir kökte (`TRADINGBOT_BASE`, `TRADINGBOT_SYSTEMD_DIR`), SAHTE `systemctl` ve SAHTE `systemd-run` ile koşulur;
gerçek servis, gerçek systemd ve AĞ yoktur (veri birimi sahte data.binance.vision + sahte REST + sahte günlükle çalışır:
`research_engine_data_fixtures`). Kaynak depo yerel bir çıplak depodur (`TB_ENGINE_REPO_URL`; PR dalı
`claude/gifted-knuth-0ehpcs` = hedef); app klonu VPS'te çalışan `f8b05fb`'dedir. **Ön koşul P1a**, gerçek P1a betiği
(`tb-engine-4962209.sh`) aynı sahte kökte bir kez dağıtılarak kurulur (modül başına bir şablon; her test kopyasını
kullanır).

Sahte `systemctl`: worker ve dashboard için her DEĞİŞTİREN fiilde `FORBIDDEN` yazar ve 99 ile düşer (betiğin worker'a hiç
dokunmadığı her senaryoda kanıtlanır); `start tradingbot-engine-data.service` DEPLOY EDİLEN engine-app kodunu
(`datastore.run_data --update`) sahte ağ ve yalıtım denemeleriyle çalıştırır (`FAKE_DATA_SMOKE=journal` worker günlüğünü
okunamaz yapar → `REST_GUARD_UNKNOWN`); `start tradingbot-engine-night.service` gece birimini (`night.run_night`) çalıştırır;
`daemon-reload` yüklü birim kümesini kaydeder (`FAKE_RELOAD_DRIFT=1` worker MemoryMax'ını 4G'ye kaydırır). Sahte
`systemd-run` bağımsız değişkenlerini kaydeder ve ilk doldurmayı (`--backfill`) aynı sahtelerle çalıştırır
(`FAKE_BF=hold`: süren doldurma; `fail`: hemen düşen doldurma).

Kanıtlananlar: P1a yoksa (ya da gece zamanlayıcısı kapalı, engine-app P1a'yı içermiyor, yedek doğrulanmıyor) betik
hiçbir şeye dokunmadan durur; kuru çalışma hiçbir şeyi değiştirmez; dağıtım veri birimini kurar, gece birimine dokunmaz,
sıra reload → veri smoke → gece smoke → veri zamanlayıcısıdır; veri smoke'u (günlük okunamadı) ya da reload sonrası 6G
kayması veri zamanlayıcısını AÇMAZ ve engine-app'i önceki sabitine döndürür; `--backfill` systemd-run'a veri biriminin
[Service] satırlarını AYNEN verir (Type=exec, TimeoutStartSec yok), sürerken ikinci kez başlatmaz, ilerleme/ETA'yı
gösterir, düşmüşse sıfırlayıp sürdürür; `--check`/`--ab-report` araştırma ağacını değiştirmez; `--rollback` veri birimini
kaldırır, engine-app'i P1a'ya döndürür, gece birimini ve data/research'ü bayt bayt bırakır.
"""
from __future__ import annotations

import grp
import gzip
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_research_engine_contract import parse_unit  # noqa: E402
from test_research_engine_release import JOURNALCTL, STUBS, _git, _has, _tree_digest  # noqa: E402

SCRIPT = ROOT / "deploy" / "releases" / "tb-engine-8d0a531.sh"
#: Betiğin kayıtlı sha256'sı (sürüm notu ve sahibe verilen değer; betik değişirse bu da bilinçli değişir).
SCRIPT_SHA256 = "0ffabdd7c8449c16a7a7aeb6925665735c818d6824c7ab35589f2756961e825f"
P1A_SCRIPT = ROOT / "deploy" / "releases" / "tb-engine-4962209.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
TIP = re.search(r'^TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
P1A_TIP = re.search(r'^P1A_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
APP_SHA = "f8b05fb27310238c764ac7dad23221d84f7c0b6d"      # VPS'te çalışan app (hedefin ve P1a'nın atası)
BASH, GIT = shutil.which("bash"), shutil.which("git")
SVC, TMR = "tradingbot-engine-night.service", "tradingbot-engine-night.timer"
DSVC, DTMR, BF = "tradingbot-engine-data.service", "tradingbot-engine-data.timer", "tb-engine-backfill.service"
UTC = timezone.utc


def _blob(sha: str, path: str) -> bytes:
    return subprocess.run([GIT, "show", f"{sha}:{path}"], cwd=str(ROOT), capture_output=True).stdout


needs_sandbox = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and BASH and GIT and shutil.which("systemd-analyze") and shutil.which("flock")
         and shutil.which("getent") and _has(TIP) and _has(P1A_TIP) and _has(APP_SHA)),
    reason="sandbox Linux + bash + git + systemd-analyze + flock + getent ve geçmişte hedef/P1a/app commit'leri ister")


# ============================================================================ statik
def test_script_is_syntax_ok_recorded_and_states_the_rules():
    lines = TEXT.splitlines()
    # §9.3 "hedef < ~800 satır": P1b betiği P1a'nın gece --check/--ab-report'unu + veri bölümünü taşır (uygulama notu 20)
    assert len(lines) <= 1200, "küçük, ayrılmış betik (tb-deploy-*'nin dörtte birinden az)"
    assert subprocess.run([BASH or "bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == SCRIPT_SHA256
    assert SCRIPT.name == f"tb-engine-{TIP[:7]}.sh"
    for flag in ("--dry-run", "--check", "--ab-report", "--rollback", "--backfill"):
        assert flag in TEXT
    assert re.search(r'^BRANCH_REF="refs/heads/claude/gifted-knuth-0ehpcs"$', TEXT, re.M), "PR dalı (impl/* yalnız yedek)"
    head = "\n".join(lines[:45])
    assert "EN ERKEN DAĞITIM" in head and "en az 3 GÜN" in head and "restart-at.txt" in head
    steps = [re.search(rf"#\s+{i}\) sudo bash tb-engine-{TIP[:7]}\.sh {flag}", head)
             for i, flag in enumerate(("--dry-run", " *#", "--backfill", "--check", "--ab-report"), 1)]
    assert all(steps) and [m.start() for m in steps] == sorted(m.start() for m in steps), \
        "sahibin adımları sırayla: dry-run → dağıt → --backfill → --check → --ab-report"
    assert "ÖN KOŞUL — P1a KURULU" in head


@pytest.mark.skipif(not (_has(TIP) and _has(P1A_TIP)), reason="hedef/P1a commit'i geçmişte yok (sığ klon)")
def test_script_pins_match_the_code_commit():
    assert _git("merge-base", "--is-ancestor", TIP, "HEAD").returncode == 0
    assert _git("merge-base", "--is-ancestor", P1A_TIP, TIP).returncode == 0, "P1a (4962209) hedefin atası (eski P1a testi geçer)"
    assert _git("merge-base", "--is-ancestor", APP_SHA, TIP).returncode == 0, "VPS app'i hedefin atası (SKEW yok)"
    p1a = P1A_SCRIPT.read_text(encoding="utf-8")
    assert re.search(rf'^TIP="{P1A_TIP}"', p1a, re.M), "P1A_TIP = P1a betiğinin hedefi"
    for var, path in (("SVC_SHA256", "deploy/tradingbot-engine-night.service"), ("TMR_SHA256", "deploy/tradingbot-engine-night.timer"),
                      ("DSVC_SHA256", "deploy/tradingbot-engine-data.service"), ("DTMR_SHA256", "deploy/tradingbot-engine-data.timer")):
        want = hashlib.sha256(_blob(TIP, path)).hexdigest()
        assert re.search(rf'^{var}="{want}"', TEXT, re.M), var
        if var in ("SVC_SHA256", "TMR_SHA256"):
            assert re.search(rf'^{var}="{want}"', p1a, re.M), f"{var}: gece birimi P1a'dakiyle aynı (yeniden kurulmaz)"
    runner = _blob(TIP, "tests/standalone/run_engine_invariants.py").decode("utf-8")
    n = len(re.findall(r'^\s+\(\d+, "test_research_engine_\w+",', runner, re.M))
    assert re.search(rf"^INV_TESTS={n}\b", TEXT, re.M) and n == 53
    acc = re.search(r"^ACCEPTANCE = \(([^)]*)\)", runner, re.M).group(1)
    assert re.search(r"^ACCEPT = \[" + re.escape(acc) + r"\]$", TEXT, re.M), "betiğin kabul listesi = koşucununki"
    ver = re.search(r'^ENGINE_VERSION = "([^"]+)"', _blob(TIP, "tradingbot/research_engine/__init__.py").decode(), re.M).group(1)
    assert re.search(rf'^ENGINE_VER="{ver}"$', TEXT, re.M)


def _code() -> str:
    return "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))


def test_script_never_mutates_worker_dashboard_or_the_night_unit_statically():
    code = _code()
    for m in re.finditer(r'(?<![\w-])systemctl (start|stop|restart|try-restart|reload-or-restart|reload|kill|enable|'
                         r'disable|mask|set-property|edit|revert|daemon-reexec|reset-failed)\b([^\n;|&)]*)', code):
        pre = code[max(0, m.start() - 5):m.start()]
        if pre.endswith("sudo "):                       # sahibe basılan talimat metni (çalıştırılmaz)
            continue
        assert re.match(r'\s*(--\S+\s+)*"?\$(SVC|TMR|DSVC|DTMR|BF)\b', m.group(2)), f"motor birimi dışında: {m.group(0)}"
        if re.match(r'\s*(--\S+\s+)*"?\$(SVC|TMR)\b', m.group(2)):
            assert m.group(1) in ("start", "stop") and "$SVC" in m.group(2), f"gece birimine yalnız smoke: {m.group(0)}"
    assert not re.search(r'(rm|install|cp)\b[^\n]*"\$SD/\$(SVC|TMR)"', code), "gece birimi dosyaları değişmez"
    assert "setup_vps_v3.sh ASLA" in TEXT and not re.search(r"^\s*(bash|sh)\s+\S*setup_vps", code, re.M)
    assert len(re.findall(r"^\s*systemd-run --unit=\"\$BF\"", code, re.M)) == 1


def test_shellcheck_clean():
    exe = shutil.which("shellcheck") or str(Path(sys.executable).with_name("shellcheck"))
    if not os.access(exe, os.X_OK):
        pytest.skip("shellcheck yok (betiğin --dry-run'ı VPS'te varsa koşar)")
    cp = subprocess.run([exe, str(SCRIPT)], capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stdout + cp.stderr


def test_heavy_steps_run_at_lowest_cpu_and_io_priority():
    assert re.search(r'^LOW=\(nice -n 19\); if command -v ionice .* LOW\+=\(ionice -c3 -t\)', TEXT, re.M)
    for step in ('-s -m compileall -q tradingbot scripts', '-s tests/standalone/run_engine_invariants.py',
                 "-s -c 'import resource as r, sys, tradingbot.cli", '-s scripts/bot_scorecard.py'):
        lines = [ln for ln in TEXT.splitlines() if step in ln and "$VENV/bin/python" in ln]
        assert lines and all('"${LOW[@]}" "$VENV/bin/python"' in ln for ln in lines), step


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


def _service_props(path: Path, dropin: dict[str, str] | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, vs in parse_unit(path)["Service"].items():
        if k in ("Type", "ExecStart", "TimeoutStartSec"):
            continue
        out += [(k, v) for v in vs]
    for k, v in (dropin or {}).items():
        out = [p for p in out if p[0] != k] + [(k, v)]
    return [("Type", "exec")] + out


def test_backfill_properties_are_the_data_unit_service_lines_verbatim(tmp_path):
    unit = ROOT / "deploy" / DSVC
    cp = _tool(tmp_path, "bfprops", str(unit), str(tmp_path / "yok.conf"))
    assert cp.returncode == 0, cp.stderr
    got = [tuple(ln[len("--property="):].split("=", 1)) for ln in cp.stdout.splitlines()]
    assert all(ln.startswith("--property=") for ln in cp.stdout.splitlines())
    assert sorted(got) == sorted(_service_props(unit))
    keys = {k for k, _ in got}
    assert {"ProtectSystem", "PrivateTmp", "ReadWritePaths", "ReadOnlyPaths", "SupplementaryGroups", "Nice", "CPUWeight",
            "IOSchedulingClass", "OOMScoreAdjust", "MemoryMax", "User", "WorkingDirectory", "Environment"} <= keys
    assert "TimeoutStartSec" not in keys and "ExecStart" not in keys and ("Type", "exec") in got, "süre sınırı yok, arka plan"
    assert ("ENGINE_EXPECTED_MEMORY_MAX=1073741824" in {v for k, v in got if k == "Environment"}
            and ("MemoryMax", "1G") in got), "öz-denetim: memory.max = beklenen"
    drop = tmp_path / "50-tb-engine.conf"
    drop.write_text("# x\n[Unit]\nOnFailure=tradingbot-alert@%n.service\n[Service]\nCPUQuota=60%\n", encoding="utf-8")
    got2 = [tuple(ln[len("--property="):].split("=", 1)) for ln in _tool(tmp_path, "bfprops", str(unit), str(drop)).stdout.splitlines()]
    assert sorted(got2) == sorted(_service_props(unit, {"CPUQuota": "60%"})) and ("CPUQuota", "100%") not in got2
    assert _tool(tmp_path, "bfprops", str(tmp_path / "yok.service"), str(drop)).returncode == 1


def _data_fixture(res: Path, *, rid="20261006T104100Z", mode="update", result="SUCCESS", guard="REST_GUARD_OK",
                  engine="research_engine_v1_p1b", iso="OK", sealed=None, files=True, started=None) -> str:
    started = started or datetime.now(UTC).isoformat()
    day = started[:10]
    last = {"run_id": rid, "mode": mode, "result": result, "exit_code": 0 if result == "SUCCESS" else 1,
            "started_at": started, "finished_at": started, "rest": {"guard": guard, "requests": 2,
                                                                   "journal": {"error": "Permission denied"}}}
    (res / "summary").mkdir(parents=True, exist_ok=True)
    (res / "summary" / "data_status.json").write_text(json.dumps(
        {"schema": "engine_data_status_v1", "last_run": last, "data_seal": "s" * 64, "sealed_run_id": sealed or rid}),
        encoding="utf-8")
    dr = {"engine": engine, "run_id": rid, "mode": mode, "result": result, "started_at": started, "finished_at": started,
          "selfcheck": {"isolation": {"status": iso, "checks": {"socket_denied": {"status": "NOT_APPLICABLE"},
                                                                "memory_max": {"status": "MATCH"}}}},
          "resources": {"memory_peak_bytes": 238 << 20, "memory_peak_ratio": 0.2324}, "rest": last["rest"]}
    (res / "runs" / "data" / rid).mkdir(parents=True, exist_ok=True)
    (res / "runs" / "data" / rid / "data_run.json").write_text(json.dumps(dr), encoding="utf-8")
    if files:
        (res / "universe").mkdir(exist_ok=True)
        (res / "universe" / f"{day}.json").write_text("{}", encoding="utf-8")
        (res / "exchangeinfo").mkdir(exist_ok=True)
        (res / "exchangeinfo" / f"{day}.json.gz").write_bytes(gzip.compress(b"{}"))
    return rid


def test_data_smoke_acceptance_rules(tmp_path):
    user = pwd.getpwuid(os.getuid()).pw_name
    t0 = str(time.time() - 1)

    def case(name, pre="", **kw):
        res = tmp_path / name / "research"
        _data_fixture(res, **kw)
        return _tool(tmp_path, "dsmoke", str(res), pre, t0, user, "research_engine_v1_p1b")
    assert case("ok").returncode == 0
    assert case("skip_guard", guard="REST_GUARD_SKIP").returncode == 0, "günlük OKUNDU, worker 429 aldı: REST atlandı (güvenli)"
    for name, kw, why in (("unknown", {"guard": "REST_GUARD_UNKNOWN"}, "SupplementaryGroups"),
                          ("old_code", {"engine": "research_engine_v1"}, "motor sürümü"),
                          ("iso", {"iso": "ISOLATION_BROKEN"}, "öz-denetim"),
                          ("seal", {"sealed": "20261005T004100Z"}, "mühür"),
                          ("partial", {"result": "PARTIAL"}, "sonuç"),
                          ("backfill", {"mode": "backfill"}, "sonuç"),
                          ("nofiles", {"files": False}, "universe/"),
                          ("stale", {"pre": "20261006T104100Z"}, "yeni data_status")):
        pre = kw.pop("pre", "")
        cp = case(name, pre=pre, **kw)
        assert cp.returncode == 1 and "KALDI:" in cp.stdout and why in cp.stdout, (name, cp.stdout + cp.stderr)


def test_data_check_reports_backfill_progress_freshness_gold_and_disk(tmp_path):
    from tradingbot.research_engine.summary import claim_word_violations
    res = tmp_path / "research"
    now = datetime.now(UTC)
    t_dep = int((now - timedelta(days=2)).timestamp())
    base = tmp_path / "deploy.json"
    base.write_text(json.dumps({"tip": TIP, "deployed_at": t_dep + 60, "started_at": t_dep, "data_smoke_run_id": "S"}),
                    encoding="utf-8")
    _data_fixture(res, rid="20261004T110000Z", started=(now - timedelta(days=2) + timedelta(minutes=1)).isoformat())
    bf_start = (now - timedelta(hours=3)).isoformat()
    _data_fixture(res, rid="20261006T080000Z", mode="backfill", result="RUNNING", started=bf_start)
    ds = json.loads((res / "summary" / "data_status.json").read_text(encoding="utf-8"))
    ds["running"] = {"run_id": "20261006T080000Z", "mode": "backfill",
                     "progress": {"phase": "archive", "done": 1200, "total": 170000, "rate_per_h_1h": 9000.0,
                                  "eta": "2026-10-07T03:10:00+00:00", "paused": True, "resume_at": "2026-10-06T12:36:00+00:00"}}
    (res / "summary" / "data_status.json").write_text(json.dumps(ds), encoding="utf-8")

    def run(bf_state, res_b="1000", peak="0"):
        cp = _tool(tmp_path, "dcheck", str(res), TIP, str(base), res_b, "1073741824", bf_state, peak, "5400000000")
        assert cp.returncode == 0, cp.stderr
        assert not claim_word_violations(cp.stdout.splitlines())
        return cp.stdout
    out = run("active", peak=str(300 << 20))
    assert re.search(r"\[ölçülemedi\]\s+V1 ilk doldurma: SÜRÜYOR \(20261006T080000Z\) · aşama archive · 1200/170000 görev · "
                     r"son 1 sa hızı 9000\.0/sa · tahmini bitiş 2026-10-07T03:10 · DURAKLADI \(4h penceresi, 12:36'e kadar\)", out), out
    assert re.search(r"\[ölçülemedi\]\s+V7 disk: .*ilk doldurma bitince", out), out
    assert re.search(r"\[tamam\]\s+V6 veri/doldurma memory.peak en çok 0,29 × MemoryMax", out), out
    out = run("inactive")
    assert re.search(r"\[DİKKAT\]\s+V1 ilk doldurma: 20261006T080000Z DURDU \(süreç yok\) — bitmedi; kaldığı yerden", out), out
    # tamamlanmış doldurma: tazelik, altın serileri, disk ±%30
    dr = res / "runs" / "data" / "20261006T080000Z" / "data_run.json"
    d = json.loads(dr.read_text(encoding="utf-8"))
    d.update(result="SUCCESS", finished_at=now.isoformat())
    dr.write_text(json.dumps(d), encoding="utf-8")
    ds.pop("running")
    ser = {f"futures/{s}/1d": {"planned": True, "stale": False, "age_h": 10.0, "gaps": 0}
           for s in ("BTCUSDT", "XAUUSDT", "PAXGUSDT")}
    ser["spot/PAXGUSDT/1d"] = {"planned": True, "stale": False, "age_h": 30.5, "gaps": 2}
    ser["futures/OLDUSDT/1d"] = {"planned": False, "stale": True, "age_h": 900.0}
    ds.update(series=ser, totals={"unverified_rows": 17, "rest_rows": 5, "seed_rows": 0},
              dukascopy={"status": "YAPILAMADI", "reason": "hazır ayna içe alınmadı"})
    (res / "summary" / "data_status.json").write_text(json.dumps(ds), encoding="utf-8")
    out = run("inactive", res_b=str(int(5.0e9)))
    assert re.search(r"\[tamam\]\s+V1 ilk doldurma: TAMAMLANDI 20261006T080000Z", out), out
    assert re.search(r"\[DİKKAT\]\s+V2 tazelik: planlı 4 seri, last_ts ≤ 26 sa 3 — eski: spot/PAXGUSDT/1d 30 sa · altın tamam · "
                     r"Dukascopy YAPILAMADI", out), out
    assert re.search(r"\[bilgi\]\s+V3 boşluk 2 \(1 seride; .*archive_unverified satır 17", out), out
    assert re.search(r"\[tamam\]\s+V7 disk: data/research 5,00 GB · tahmin 5,4 GB ±%30", out), out
    assert re.search(r"\[tamam\]\s+V4 arşiv uzlaştırma farkı ilk 1/3 gecede 0", out), out
    out = run("inactive", res_b=str(int(9e9)))
    assert re.search(r"\[DİKKAT\]\s+V7 disk", out), out
    del ser["futures/XAUUSDT/1d"]
    (res / "summary" / "data_status.json").write_text(json.dumps(ds), encoding="utf-8")
    assert "altın EKSİK futures/XAUUSDT" in run("inactive")


def test_p1a_ab_window_warning(tmp_path):
    res = tmp_path / "research"
    (res / "runs").mkdir(parents=True)
    ep = res / "runs" / "engine_epochs.jsonl"

    def at(days):
        day = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        ep.write_text(json.dumps({"engine_sha": P1A_TIP, "epoch_day": day}) + "\n", encoding="utf-8")
        return _tool(tmp_path, "p1aab", str(res), P1A_TIP)
    assert at(3).returncode == 3 and "/14 gece geçti" in at(3).stdout
    assert at(16).returncode == 0 and "14/14" in at(16).stdout
    ep.unlink()
    assert _tool(tmp_path, "p1aab", str(res), P1A_TIP).returncode == 3


# ============================================================================ sandbox
#: Sahte motor çalıştırmaları (systemctl start + systemd-run ortak): ağ YOK, deploy edilen engine-app kodu.
FAKEENGINE = r'''
import os, sys, pathlib
from datetime import datetime, timezone
BASE = pathlib.Path(os.environ["TRADINGBOT_BASE"])
DATA_MEM = 1073741824
def _eng():
    sys.dont_write_bytecode = True
    eng = BASE / "engine-app"
    sys.path[:0] = [str(eng), str(eng / "tests")]
    os.chdir(eng)
    return eng
def run_data(kind):
    """`engine-data --update|--backfill`: sahte Binance + sahte günlük + birim yalıtımının yerine-geçenleri."""
    _eng()
    from tradingbot.research_engine import datastore as DS, selfcheck as SC
    from tradingbot.research_engine.paths import EnginePaths
    from research_engine_data_fixtures import FakeBinance, FakeClock, FakeJournal, ms
    clock = FakeClock(datetime.now(timezone.utc))
    fb = FakeBinance(clock, listing={"futures/BTCUSDT": ms(clock.now()) - 20 * 86_400_000})
    bad_journal = os.environ.get("FAKE_DATA_SMOKE") == "journal"
    pr = SC.Probes(write_denied=lambda d: {"path": str(d), "status": "DENIED", "errno": "EROFS"},
                   socket_denied=lambda: {"status": "CONNECTED"},
                   memory_max=lambda env: {"status": "MATCH", "expected": DATA_MEM, "actual": DATA_MEM},
                   writable=lambda d: {"path": str(d), "status": "WRITABLE_OK"})
    keys = {"futures/BTCUSDT/1d", "futures/BTCUSDT/funding"}
    st = DS.run_data(EnginePaths(data=BASE / "data", state=BASE / "data" / "state"), mode=kind, clock=clock.now,
                     sleep=clock.sleep, http=fb, runner=FakeJournal(rc=1 if bad_journal else 0,
                                                                     err="Permission denied" if bad_journal else ""),
                     probes=pr, env={"ALLOW_LIVE_TRADING": "false"}, app_dir=BASE / "app",
                     free_bytes=int(os.environ.get("FAKE_FREE_BYTES", "50000000000")), plan_filter=lambda s: s.key in keys)
    return int(st.get("exit_code") or 0)
def run_night():
    eng = _eng()
    from tradingbot.research_engine import night as N, selfcheck as SC
    from tradingbot.research_engine.paths import EnginePaths
    pr = SC.Probes(write_denied=lambda d: {"path": str(d), "status": "DENIED", "errno": "EROFS"},
                   socket_denied=lambda: {"status": "DENIED", "errno": "ENETUNREACH"},
                   memory_max=lambda env: {"status": "MATCH", "expected": 536870912, "actual": 536870912},
                   writable=lambda d: {"path": str(d), "status": "WRITABLE_OK"})
    st = N.run_night(EnginePaths(data=BASE / "data", state=BASE / "data" / "state"), app_dir=BASE / "app",
                     engine_dir=eng, env={"ALLOW_LIVE_TRADING": "false"}, probes=pr, backup_wait_s=0,
                     free_bytes=int(os.environ.get("FAKE_FREE_BYTES", "50000000000")))
    return int(st.get("exit_code") or 0)
'''
SYSTEMCTL = r'''#!@PY@
import json, os, sys, pathlib, hashlib
if not os.environ.get("FAKE_STATE"):      # motorun S0'ı kısıtlı ortamla `is-active tradingbot-backup.service` sorar
    print("inactive"); sys.exit(3)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
F = pathlib.Path(os.environ["FAKE_STATE"]); SD = pathlib.Path(os.environ["TRADINGBOT_SYSTEMD_DIR"])
W, DSH = "tradingbot-worker.service", "tradingbot-dashboard.service"
SVC, TMR, DSVC, DTMR, BF = ("tradingbot-engine-night.service", "tradingbot-engine-night.timer",
                            "tradingbot-engine-data.service", "tradingbot-engine-data.timer", "tb-engine-backfill.service")
TIMERS, MEM = (TMR, DTMR), {SVC: "536870912", DSVC: "1073741824"}
args = sys.argv[1:]
with (F / "systemctl.log").open("a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\n")
sp = F / "systemd.json"
S = json.loads(sp.read_text()) if sp.exists() else {}
for k in ("enabled", "active", "result", "ems"):
    S.setdefault(k, {})
def save():
    sp.write_text(json.dumps(S))
def L(s):
    with (F / "systemctl.log").open("a", encoding="utf-8") as fh:
        fh.write(s + "\n")
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
    S["loaded"] = {u: fsha(u) for u in (SVC, TMR, DSVC, DTMR) if (SD / u).exists()}
    if os.environ.get("FAKE_RELOAD_DRIFT") and DSVC in S["loaded"]:
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
    if u == BF:
        return {"Result": S.get("bf_result", "success"), "ExecMainStatus": str(S.get("bf_ems", 0))}.get(p, "")
    ld = S.get("loaded", {})
    return {"LoadState": "loaded" if u in ld else "not-found", "NeedDaemonReload": "no" if ld.get(u) == fsha(u) else "yes",
            "MemoryMax": MEM.get(u, "infinity") if u in ld else "infinity", "Result": S["result"].get(u, "success"),
            "ExecMainStatus": str(S["ems"].get(u, 0)),
            "NextElapseUSecRealtime": "Tue 2026-10-06 03:41:00 +03" if S["enabled"].get(u) else ""}.get(p, "")
if verb == "show":
    print(prop(args[1], args[args.index("-p") + 1])); sys.exit(0)
if verb == "daemon-reload":
    reload(); sys.exit(0)
if verb == "is-enabled":
    u = tg[0]
    st = ("enabled" if S["enabled"].get(u) else "disabled") if (SD / u).exists() else "not-found"
    print(st); sys.exit(0 if st == "enabled" else 1)
if verb == "is-active":
    u = tg[0]
    st = "active" if u == W or (u in TIMERS and S["active"].get(u)) else (S.get("bf", "inactive") if u == BF else "inactive")
    print(st); sys.exit(0 if st == "active" else 3)
if verb == "cat":
    sys.exit(0 if (tg[0] == "tradingbot-alert@.service" and os.environ.get("FAKE_ALERT")) else 1)
if verb in ("enable", "disable"):
    u = tg[0]
    if u not in TIMERS or (verb == "enable" and u not in S.get("loaded", {})):
        sys.exit(1)
    S["enabled"][u] = verb == "enable"
    if "--now" in args:
        S["active"][u] = verb == "enable"
    save()
    if "--no-reload" not in args:
        reload()
    sys.exit(0)
if verb == "stop":
    if tg[0] in TIMERS:
        S["active"][tg[0]] = False
    if tg[0] == BF:
        S["bf"] = "inactive"
    save(); sys.exit(0)
if verb == "reset-failed":
    if tg and tg[0] == BF and S.get("bf") == "failed":
        S["bf"] = "inactive"; save()
    sys.exit(0)
if verb == "start" and tg[0] in (SVC, DSVC):
    u = tg[0]
    if u not in S.get("loaded", {}):
        print("Unit not found"); sys.exit(5)
    r = pathlib.Path(os.environ["TRADINGBOT_BASE"]) / "data" / "research"
    L("START %s research=%s mode=%s uid=%s" % (u, r.is_dir(), oct(r.stat().st_mode & 0o777) if r.exists() else None,
                                              r.stat().st_uid if r.exists() else None))
    if os.environ.get("FAKE_SMOKE") == "namespace":
        S["result"][u], S["ems"][u] = "exit-code", 226; save(); sys.exit(1)
    import fakeengine
    rc = fakeengine.run_data("update") if u == DSVC else fakeengine.run_night()
    S = json.loads(sp.read_text()) if sp.exists() else S
    S["result"][u], S["ems"][u] = ("success" if rc == 0 else "exit-code"), rc
    save()
    sys.exit(0 if rc == 0 else 1)
sys.exit(0)
'''
SYSTEMD_RUN = r'''#!@PY@
import json, os, sys, pathlib
a = sys.argv[1:]
if a[:1] == ["--version"]:
    print("systemd 255 (255.4-sahte)"); sys.exit(0)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
F = pathlib.Path(os.environ["FAKE_STATE"])
with (F / "systemd-run.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(a) + "\n")
sp = F / "systemd.json"
S = json.loads(sp.read_text()) if sp.exists() else {}
if S.get("bf") in ("active", "failed"):
    print("Failed to start transient service unit: Unit tb-engine-backfill.service was already loaded", file=sys.stderr)
    sys.exit(1)
mode = os.environ.get("FAKE_BF", "run")
if mode == "hold":
    S["bf"] = "active"
elif mode == "fail":
    S.update(bf="failed", bf_result="exit-code", bf_ems=1)
else:
    import fakeengine
    rc = fakeengine.run_data("backfill")
    S = json.loads(sp.read_text()) if sp.exists() else {}
    S.update(bf="inactive" if rc == 0 else "failed", bf_result="success" if rc == 0 else "exit-code", bf_ems=rc)
sp.write_text(json.dumps(S))
sys.exit(0)
'''


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """Yerel çıplak kaynak depo: claude/gifted-knuth-0ehpcs = P1b hedefi (P1a'yı içerir), app-main = VPS app'i (f8b05fb)."""
    if not (GIT and _has(TIP) and _has(P1A_TIP) and _has(APP_SHA)):
        pytest.skip("hedef/P1a/app commit'leri yok")
    d = tmp_path_factory.mktemp("src")
    src = d / "src.git"
    assert _git("init", "-q", "--bare", str(src)).returncode == 0
    assert _git("fetch", "-q", "--no-tags", str(ROOT), "+HEAD:refs/heads/scratch", cwd=src).returncode == 0
    assert _git("update-ref", "refs/heads/claude/gifted-knuth-0ehpcs", TIP, cwd=src).returncode == 0
    assert _git("update-ref", "refs/heads/app-main", APP_SHA, cwd=src).returncode == 0
    assert _git("update-ref", "-d", "refs/heads/scratch", cwd=src).returncode == 0
    return src


class Sandbox:
    """Sahte VPS kökü (P1a testinin düzeni) + P1b sahteleri. `p1a()` gerçek P1a betiğini dağıtır."""

    def __init__(self, tmp: Path, src: Path, **env):
        from decimal import Decimal as D

        from research_engine_fixtures import FakeVps, at, trade
        self.tmp, self.src = tmp, src
        self.base, self.sd, self.fake, self.stub = tmp / "tb base", tmp / "systemd", tmp / "fake", tmp / "bin"
        for d in (self.base / "venv" / "bin", self.sd, self.fake, self.stub):
            d.mkdir(parents=True)
        py = self.base / "venv" / "bin" / "python"
        py.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        (self.stub / "systemctl").write_text(SYSTEMCTL.replace("@PY@", sys.executable), encoding="utf-8")
        (self.stub / "systemd-run").write_text(SYSTEMD_RUN.replace("@PY@", sys.executable), encoding="utf-8")
        (self.stub / "fakeengine.py").write_text(FAKEENGINE, encoding="utf-8")
        (self.stub / "journalctl").write_text(JOURNALCTL.replace("@PY@", sys.executable), encoding="utf-8")
        for name, body in STUBS.items():
            (self.stub / name).write_text(body.replace("@DATE@", shutil.which("date") or "/bin/date")
                                          .replace("@DF@", shutil.which("df") or "/bin/df"), encoding="utf-8")
        for p in [py, *self.stub.iterdir()]:
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
        self.env.update({"PATH": f"{self.stub}:{os.environ.get('PATH', '/usr/bin:/bin')}", "TRADINGBOT_BASE": str(self.base),
                         "TRADINGBOT_SYSTEMD_DIR": str(self.sd), "TRADINGBOT_USER": pwd.getpwuid(os.getuid()).pw_name,
                         "TRADINGBOT_GROUP": grp.getgrgid(os.getgid()).gr_name, "TB_ENGINE_ALLOW_NON_ROOT": "1",
                         "TB_ENGINE_REPO_URL": str(src), "FAKE_STATE": str(self.fake), "FAKE_UTC_HM": "10:00",
                         "FAKE_NPROC": "4", "FAKE_FREE_BYTES": "50000000000", **env})

    def run(self, *args: str, script: Path = SCRIPT, **env) -> subprocess.CompletedProcess:
        cp = subprocess.run([BASH, str(script), *args], env={**self.env, **env}, cwd=str(self.tmp), capture_output=True,
                            text=True, timeout=1200)
        cp.out = cp.stdout + cp.stderr
        return cp

    def p1a(self) -> None:
        cp = self.run(script=P1A_SCRIPT)
        assert cp.returncode == 0 and "DAĞITILDI" in cp.out, cp.out[-4000:]

    def log(self) -> list[str]:
        p = self.fake / "systemctl.log"
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def runs(self) -> list[list[str]]:
        p = self.fake / "systemd-run.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def unit_files(self) -> list[str]:
        return sorted(p.name for p in self.sd.rglob("*") if p.is_file())

    def eng_head(self) -> str:
        return _git("rev-parse", "HEAD", cwd=self.base / "engine-app").stdout.strip()

    def state_json(self) -> dict:
        return json.loads((self.fake / "systemd.json").read_text(encoding="utf-8"))

    def untouched_since(self, snap: dict) -> None:
        assert self.unit_files() == snap["units"] and self.eng_head() == snap["eng"]
        assert _tree_digest(self.res) == snap["res"]
        new = self.log()[snap["log"]:]
        assert not [ln for ln in new if ln.split()[0] in ("daemon-reload", "start", "enable", "disable", "stop")], new
        assert not self.runs()

    def snap(self) -> dict:
        return {"units": self.unit_files(), "eng": self.eng_head(), "res": _tree_digest(self.res), "log": len(self.log())}


def _no_forbidden(sb: Sandbox) -> None:
    assert not [ln for ln in sb.log() if ln.startswith("FORBIDDEN")], "betik worker/dashboard'a dokundu"


def _at(log: list[str], prefix: str) -> int:
    return next(i for i, ln in enumerate(log) if ln.startswith(prefix))


@pytest.fixture(scope="module")
def p1a_template(tmp_path_factory, source):
    """Gerçek P1a betiğiyle (tb-engine-4962209.sh) bir kez kurulmuş sahte VPS: engine-app 4962209, gece birimi açık."""
    root = tmp_path_factory.mktemp("p1a")
    sb = Sandbox(root, source)
    sb.p1a()
    assert sb.eng_head() == P1A_TIP and sb.unit_files() == sorted([SVC, TMR])
    return root


def _from_template(tmp_path: Path, template: Path, source: Path, **env) -> Sandbox:
    sb = Sandbox.__new__(Sandbox)
    for name in ("tb base", "systemd", "fake", "bin"):
        shutil.copytree(template / name, tmp_path / name, symlinks=True)
    sb.tmp, sb.src = tmp_path, source
    sb.base, sb.sd, sb.fake, sb.stub = tmp_path / "tb base", tmp_path / "systemd", tmp_path / "fake", tmp_path / "bin"
    sb.state, sb.res = sb.base / "data" / "state", sb.base / "data" / "research"
    sb.env = {k: v for k, v in os.environ.items() if not k.startswith(("TRADINGBOT_", "ENGINE_", "FAKE_", "TB_"))}
    sb.env.update({"PATH": f"{sb.stub}:{os.environ.get('PATH', '/usr/bin:/bin')}", "TRADINGBOT_BASE": str(sb.base),
                   "TRADINGBOT_SYSTEMD_DIR": str(sb.sd), "TRADINGBOT_USER": pwd.getpwuid(os.getuid()).pw_name,
                   "TRADINGBOT_GROUP": grp.getgrgid(os.getgid()).gr_name, "TB_ENGINE_ALLOW_NON_ROOT": "1",
                   "TB_ENGINE_REPO_URL": str(source), "FAKE_STATE": str(sb.fake), "FAKE_UTC_HM": "10:00",
                   "FAKE_NPROC": "4", "FAKE_FREE_BYTES": "50000000000", **env})
    return sb


@needs_sandbox
def test_without_p1a_the_script_refuses_before_any_change(tmp_path, source):
    sb = Sandbox(tmp_path, source)
    cp = sb.run()
    assert cp.returncode == 1 and "DUR: P1a-kurulu" in cp.out and "HİÇBİR ŞEYE DOKUNULMADI" in cp.out, cp.out[-3000:]
    assert "tb-engine-4962209.sh" in cp.out, "sahibe önce P1a'yı söyler"
    assert not (sb.base / "engine-app").exists() and not sb.res.exists() and not sb.unit_files() and not sb.runs()
    assert not [ln for ln in sb.log() if ln.split()[0] in ("daemon-reload", "start", "enable", "disable", "stop")]
    bf = sb.run("--backfill")
    assert bf.returncode == 1 and "P1b-kurulu" in bf.out and not sb.runs(), bf.out[-2000:]
    rb = sb.run("--rollback")                      # kurulu veri birimi yok: reload da yok
    assert rb.returncode == 0 and "veri birimi kurulu değil" in rb.out, rb.out[-2000:]
    assert not [ln for ln in sb.log() if ln.split()[0] in ("daemon-reload", "start", "enable", "disable")]
    _no_forbidden(sb)


@needs_sandbox
def test_dry_run_checks_everything_and_changes_nothing(tmp_path, source, p1a_template):
    sb = _from_template(tmp_path, p1a_template, source)
    (sb.base / "deploy-logs" / "8db1faf-restart-at.txt").write_text(f"{int(time.time()) - 3 * 86400 - 600}\nx\n")
    snap = sb.snap()
    before = len(list(Path("/tmp").glob("tb-engine-*")))
    cp = sb.run("--dry-run")
    assert cp.returncode == 0, cp.out[-6000:]
    m = re.search(r"KURU ÇALIŞMA: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "22", cp.out[-3000:]
    for name in ("P1a-kurulu", "günlük-grubu", "yedek-doğrulandı", "3g-pencere-dışı", "kaynak-TIP", "app-SHA-ata", "P1a-ata",
                 "birim-sha256", "birim-sözleşmesi", "systemd-analyze-verify", "systemd-run", "compileall", "bağımsız-koşucu",
                 "yalıtım-AST", "status-kuru", "worker-MemoryMax=6G", "tradingbot-worker-NDR=no", "motor-boşta"):
        assert re.search(rf"\[tamam\] #\d+ {re.escape(name)}", cp.out), name
    assert "53 geçti · 0 kaldı · 0 atlandı" in cp.out
    assert "8db1faf-restart-at.txt 3 gün önce (≥ 3;" in cp.out
    assert re.search(r"UYARI\s+P1a A/B penceresi \(sürüm günü \d{4}-\d\d-\d\d\): 0/14 gece geçti — P1b yeni bir A/B", cp.out)
    sb.untouched_since(snap)
    _no_forbidden(sb)
    assert len(list(Path("/tmp").glob("tb-engine-*"))) == before, "geçici klon silinmeli"


@needs_sandbox
def test_deploy_backfill_check_ab_report_and_rollback_to_p1a(tmp_path, source, p1a_template):
    from tradingbot.research_engine.summary import claim_word_violations
    sb = _from_template(tmp_path, p1a_template, source)
    n0 = len(sb.log())
    night_units = {u: (sb.sd / u).read_bytes() for u in (SVC, TMR)}
    cp = sb.run()
    assert cp.returncode == 0, cp.out[-6000:]
    m = re.search(r"DAĞITILDI: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "32", cp.out[-3000:]
    log = sb.log()[n0:]
    _no_forbidden(sb)
    # §9.3 sırası: veri birimi dosyaları → kapılı reload → veri smoke → gece smoke (yeni kod) → ancak sonra enable --now
    i_reload, i_dstart = _at(log, "daemon-reload"), _at(log, f"start {DSVC}")
    i_nstart, i_enable = _at(log, f"start {SVC}"), _at(log, f"enable --now {DTMR}")
    assert i_reload < i_dstart < i_nstart < i_enable
    assert not [ln for ln in log if ln.startswith(("enable", "disable")) and TMR in ln and DTMR not in ln], "gece zamanlayıcısına dokunulmaz"
    pre = next(ln for ln in log if ln.startswith(f"START {DSVC}"))
    assert f"research=True mode=0o750 uid={os.getuid()}" in pre
    assert sb.unit_files() == sorted([SVC, TMR, DSVC, DTMR])
    for u in (DSVC, DTMR):
        assert (sb.sd / u).read_bytes() == _blob(TIP, f"deploy/{u}")
    assert {u: (sb.sd / u).read_bytes() for u in (SVC, TMR)} == night_units, "gece birimi dosyaları bayt bayt aynı"
    assert sb.eng_head() == TIP
    st = sb.state_json()
    assert st["enabled"] == {TMR: True, DTMR: True} and st["active"] == {TMR: True, DTMR: True}
    ds = json.loads((sb.res / "summary" / "data_status.json").read_text(encoding="utf-8"))
    lr = ds["last_run"]
    assert lr["mode"] == "update" and lr["result"] == "SUCCESS" and lr["rest"]["guard"] == "REST_GUARD_OK"
    day = lr["started_at"][:10]
    assert (sb.res / "universe" / f"{day}.json").is_file() and (sb.res / "exchangeinfo" / f"{day}.json.gz").is_file()
    rs = json.loads((sb.res / "summary" / "run_status.json").read_text(encoding="utf-8"))
    assert rs["result"] == "SUCCESS" and rs["shas"]["engine"] == TIP and rs["data_seal"] == ds["data_seal"]
    assert rs["selfcheck"]["data"]["status"] == "OK" and rs["selfcheck"]["ab"]["status"] == "AB_RELEASE_DAY", "yeni A/B dönemi"
    assert all(p.lstat().st_uid == os.getuid() for p in sb.res.rglob("*"))
    base = json.loads((sb.base / "deploy-logs" / f"engine-{TIP[:7]}-deploy.json").read_text(encoding="utf-8"))
    assert base["tip"] == TIP and base["smoke_run_id"] == rs["run_id"] and base["data_smoke_run_id"] == lr["run_id"]
    assert base["started_at"] <= base["deployed_at"]

    # --check: araştırma ağacı değişmez, yalnız örnek eklenir; iddia kelimesi yok
    samples = sb.base / "deploy-logs" / f"engine-{TIP[:7]}-samples.jsonl"
    d0, k0 = _tree_digest(sb.res), len(samples.read_text(encoding="utf-8").splitlines())
    ck = sb.run("--check")
    assert ck.returncode == 0, ck.out[-4000:]
    for s in (f"iki SHA: app f8b05fb · engine-app {TIP[:7]}", "SKEW yok", "GÜNLÜK HEDEF", "VERİ BİRİMİ (research_engine_v1_p1b)",
              "K1 elle smoke (gece)", "K8 rotasyon payı", "V2 tazelik", "V5 worker 418/429", "V6 veri/doldurma memory.peak",
              f"{DTMR}: enabled / active"):
        assert s in ck.out, s
    assert re.search(r"\[ölçülemedi\]\s+V1 ilk doldurma: başlatılmadı", ck.out), ck.out[-3000:]
    assert re.search(r"\[tamam\]\s+K6 scorecard --daily = engine-status --daily", ck.out), ck.out[-3000:]
    assert not claim_word_violations(ck.out.splitlines()), claim_word_violations(ck.out.splitlines())
    assert _tree_digest(sb.res) == d0 and len(samples.read_text(encoding="utf-8").splitlines()) == k0 + 1

    # --backfill: veri biriminin [Service] satırları AYNEN, Type=exec, süre yok; sahte doldurma eşzamanlı biter
    bf = sb.run("--backfill")
    assert bf.returncode == 0, bf.out[-4000:]
    assert not [ln for ln in sb.log()[n0:] if ln.startswith("daemon-reload")][1:], "systemd-run reload yapmaz (tek reload: dağıtım)"
    calls = sb.runs()
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == f"--unit={BF}" and argv[1].startswith("--description=")
    props = [tuple(a[len("--property="):].split("=", 1)) for a in argv if a.startswith("--property=")]
    assert sorted(props) == sorted(_service_props(sb.sd / DSVC)), "birimle AYNI özellikler"
    assert argv[-6:] == [str(sb.base / "venv" / "bin" / "python"), "-s", "-m", "tradingbot", "engine-data", "--backfill"]
    assert "çalıştı ve bitti" in bf.out and "TAMAMLANDI" in bf.out, bf.out[-2000:]
    ds = json.loads((sb.res / "summary" / "data_status.json").read_text(encoding="utf-8"))
    assert ds["last_run"]["mode"] == "backfill" and ds["last_run"]["result"] == "SUCCESS"
    assert ds["series"]["futures/BTCUSDT/1d"]["rows"] > 0
    ck2 = sb.run("--check")
    assert re.search(r"\[tamam\]\s+V1 ilk doldurma: TAMAMLANDI", ck2.out), ck2.out[-3000:]
    assert re.search(r"\[DİKKAT\]\s+V7 disk", ck2.out), "sahte doldurma tahminin çok altında: ±%30 dışı"
    assert re.search(r"V6 .* gece SKIPPED_LOCKED 0", ck2.out)

    # süren doldurma: ikinci --backfill yeniden BAŞLATMAZ, ilerleme/ETA gösterir; düşmüşse sıfırlayıp sürdürür
    held = sb.run("--backfill", FAKE_BF="hold")
    assert held.returncode == 0 and "İLK DOLDURMA BAŞLADI" in held.out, held.out[-2000:]
    ds = json.loads((sb.res / "summary" / "data_status.json").read_text(encoding="utf-8"))
    ds["running"] = {"run_id": "R", "mode": "backfill", "progress": {"phase": "archive", "done": 10, "total": 40,
                                                                    "rate_per_h_1h": 20.0, "eta": "2026-10-07T01:00:00+00:00"}}
    (sb.res / "summary" / "data_status.json").write_text(json.dumps(ds), encoding="utf-8")
    again = sb.run("--backfill")
    assert again.returncode == 0 and "SÜRÜYOR (R) · aşama archive · 10/40 görev" in again.out and len(sb.runs()) == 2
    assert "tahmini bitiş 2026-10-07T01:00" in again.out
    st = sb.state_json()
    st.update(bf="failed", bf_result="exit-code", bf_ems=1)
    (sb.fake / "systemd.json").write_text(json.dumps(st), encoding="utf-8")
    ds.pop("running")
    (sb.res / "summary" / "data_status.json").write_text(json.dumps(ds), encoding="utf-8")
    resumed = sb.run("--backfill")
    assert resumed.returncode == 0 and "sıfırlanıp sürdürülür" in resumed.out and len(sb.runs()) == 3, resumed.out[-2000:]
    assert f"reset-failed {BF}" in sb.log()

    ab = sb.run("--ab-report")
    assert ab.returncode == 0 and "A/B geceleri" in ab.out and "ARA GÖRÜNÜM" in ab.out and "veri/doldurma 418/429" in ab.out
    assert not claim_word_violations(ab.out.splitlines())
    again = sb.run()
    assert again.returncode == 0 and "zaten dağıtılmış" in again.out, again.out[-3000:]

    # --rollback (P1a'ya dönüş): süren doldurma durur, veri birimi kalkar, engine-app 4962209, gece birimi + veri yerinde
    sb.run("--backfill", FAKE_BF="hold")
    assert sb.state_json()["bf"] == "active"
    d1 = _tree_digest(sb.res)
    k = len(sb.log())
    rb = sb.run("--rollback")
    assert rb.returncode == 0 and "GERİ ALINDI (P1a)" in rb.out, rb.out[-4000:]
    _no_forbidden(sb)
    tail = sb.log()[k:]
    assert f"stop {BF}" in tail and f"stop {DTMR}" in tail and f"disable --no-reload {DTMR}" in tail
    assert tail.index(f"disable --no-reload {DTMR}") < tail.index("daemon-reload")
    assert not [ln for ln in tail if TMR in ln and DTMR not in ln and ln.split()[0] in ("stop", "disable")]
    assert sb.unit_files() == sorted([SVC, TMR]) and sb.eng_head() == P1A_TIP
    assert sb.state_json()["enabled"][TMR] is True and sb.state_json()["active"][TMR] is True
    assert _tree_digest(sb.res) == d1, "geri alma data/research'e dokunmamalı"
    assert "tb-engine-4962209.sh --rollback" in rb.out
    # P1a betiği geri alınmış kurulumu hâlâ tanır (aynı gece birimi, aynı SHA)
    p1 = sb.run("--check", script=P1A_SCRIPT)
    assert p1.returncode == 0 and "engine-app 4962209" in p1.out, p1.out[-2000:]


@needs_sandbox
@pytest.mark.parametrize("case", ["ndr", "night_window", "4h_window", "too_late", "release_window", "not_paper",
                                  "night_timer_off", "engine_app_old", "engine_app_newer", "no_backup", "bad_backup",
                                  "night_unit_changed"])
def test_unsafe_preconditions_refuse_before_any_change(tmp_path, source, p1a_template, case):
    env = {"ndr": {"FAKE_NDR_WORKER": "yes"}, "night_window": {"FAKE_UTC_HM": "02:10"}, "4h_window": {"FAKE_UTC_HM": "16:20"},
           "too_late": {"FAKE_UTC_HM": "19:30"}}.get(case, {})
    sb = _from_template(tmp_path, p1a_template, source, **env)
    if case == "release_window":
        # 3 günden 10 dk eksik: hâlâ pencere içinde (2026-10-06 sahip kararı)
        (sb.base / "deploy-logs" / "34ae8d2-restart-at.txt").write_text(f"{int(time.time()) - 3 * 86400 + 600}\nx\n")
    if case == "not_paper":
        (sb.state / "mode.json").write_text('{"mode": "LIVE"}', encoding="utf-8")
    if case == "night_timer_off":
        st = sb.state_json()
        st["active"][TMR] = False
        (sb.fake / "systemd.json").write_text(json.dumps(st), encoding="utf-8")
    if case == "engine_app_old":
        assert _git("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", APP_SHA, cwd=sb.base / "engine-app").returncode == 0
    if case == "engine_app_newer":                 # daha yeni bir motor sürümü kurulu: betik onu geri sarmaz
        eng = sb.base / "engine-app"
        assert _git("fetch", "-q", str(source), "refs/heads/claude/gifted-knuth-0ehpcs", cwd=eng).returncode == 0
        assert _git("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", TIP, cwd=eng).returncode == 0
        assert _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "yeni",
                    cwd=eng).returncode == 0
    if case in ("no_backup", "bad_backup"):
        for p in sorted((sb.res / "backup").glob("research-small-*.tar.gz")):
            if case == "no_backup":
                p.unlink()
            else:
                p.write_bytes(p.read_bytes()[:-10] + b"0123456789")
    if case == "night_unit_changed":
        p = sb.sd / SVC
        p.write_text(p.read_text(encoding="utf-8").replace("MemoryMax=512M", "MemoryMax=600M"), encoding="utf-8")
    snap = sb.snap()
    cp = sb.run()
    assert cp.returncode == 1 and "HİÇBİR ŞEYE DOKUNULMADI" in cp.out, cp.out[-3000:]
    want = {"release_window": "DUR: 3g-pencere-dışı", "night_timer_off": "DUR: P1a-kurulu", "engine_app_old": "DUR: P1a-kurulu",
            "no_backup": "DUR: yedek-doğrulandı", "bad_backup": "TUTMADI", "night_unit_changed": "P1a'nınki değil",
            "not_paper": "DUR: PAPER", "ndr": "NDR=no", "too_late": "sonraki 4h penceresine",
            "engine_app_newer": "daha yeni bir motor sürümü"}.get(case)
    if want:
        assert want in cp.out, (case, cp.out[-3000:])
    sb.untouched_since(snap)
    _no_forbidden(sb)


@needs_sandbox
def test_failed_data_smoke_keeps_the_data_timer_off_and_reverts_engine_app(tmp_path, source, p1a_template):
    """Veri birimi worker günlüğünü okuyamazsa (SupplementaryGroups / yetki) REST hiç çağrılmaz — güvenli, ama smoke bunu
    kabul ETMEZ: veri zamanlayıcısı açılmaz, gece birimi P1a kodunda kalır."""
    sb = _from_template(tmp_path, p1a_template, source, FAKE_DATA_SMOKE="journal", FAKE_ALERT="1", FAKE_NPROC="2")
    n0 = len(sb.log())
    cp = sb.run()
    assert cp.returncode == 2, cp.out[-4000:]
    assert "veri smoke'u GEÇMEDİ" in cp.out and "REST_GUARD_UNKNOWN" in cp.out and "SupplementaryGroups" in cp.out
    log = sb.log()[n0:]
    assert any(ln.startswith(f"start {DSVC}") for ln in log) and not any(ln.startswith(f"start {SVC}") for ln in log)
    assert not [ln for ln in log if ln.startswith("enable")], "smoke geçmeden veri zamanlayıcısı açılmaz"
    assert sb.eng_head() == P1A_TIP, "engine-app önceki sabitine döndü"
    st = sb.state_json()
    assert st["enabled"][TMR] is True and not st["enabled"].get(DTMR)
    # uyarı birimi VAR ve nproc < 4 → veri birimi drop-in'i: OnFailure + CPUQuota=60% (§2.5)
    assert sb.unit_files() == sorted([SVC, TMR, DSVC, DTMR, "50-tb-engine.conf"])
    dropin = (sb.sd / f"{DSVC}.d" / "50-tb-engine.conf").read_text(encoding="utf-8")
    assert "OnFailure=tradingbot-alert@%n.service" in dropin and "CPUQuota=60%" in dropin
    _no_forbidden(sb)


@needs_sandbox
def test_memorymax_drift_after_reload_rolls_the_data_units_back(tmp_path, source, p1a_template):
    sb = _from_template(tmp_path, p1a_template, source, FAKE_RELOAD_DRIFT="1")
    n0 = len(sb.log())
    cp = sb.run()
    assert cp.returncode == 3, cp.out[-4000:]
    assert "GERİ ALINDI" in cp.out and "set-property" in cp.out
    log = sb.log()[n0:]
    assert not [ln for ln in log if ln.startswith(("start", "enable"))]
    assert sb.unit_files() == sorted([SVC, TMR]) and sb.eng_head() == P1A_TIP, "reload engine-app'ten önce: kod değişmedi"
    _no_forbidden(sb)
