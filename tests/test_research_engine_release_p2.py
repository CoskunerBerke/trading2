# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2 — motor sürüm betiği `deploy/releases/tb-engine-<sha7>.sh` (docs/SYSTEM_LEARNING_ENGINE_V1.md
§2.2–§2.9, §9.3, §10 P2, P2 uygulama notları 34 ve 37).

Sahte kök, sahte `systemctl` / `systemd-run` / `journalctl` ve kaynak depo P1b sürüm testinden
(`test_research_engine_release_p1b`) alınır; burada yalnız üç küçük ek vardır: sahte `systemctl` gece biriminin yüklü
`MemoryMax`'ını kurulu dosyadan okur (P2 birimi 768M), `FAKE_NIGHT_FAIL=1` gece smoke'unu düşürür, sahte venv python'u
bağımsız koşucunun sonucunu commit başına önbellekte tutar (90 testlik P2 koşucusu bu modülde bir kez GERÇEKTEN koşar;
sonraki dağıtımlar aynı ağacın aynı sonucunu kullanır). Şablon bugünkü VPS durumudur (2026-10-10): P1a gerçek
`tb-engine-4962209.sh` ile, app + engine-app db5db96, sonra gerçek `tb-engine-57cfef1.sh` ve `tb-engine-79b27cf.sh`
(yükseltme yolu) — uyarı birimi VAR ve nproc = 2, yani gece ve veri drop-in'leri vardır.

Kanıtlananlar: ön koşul (veri birimi 79b27cf, zamanlayıcılar açık, gece birimi P1a ya da P2 baytları, engine-app 79b27cf
⊑) yoksa hiçbir şeye dokunmadan durur; kuru çalışma hiçbir şeyi değiştirmez; dağıtım veri birimi dosyalarına ve
drop-in'lere dokunmaz, gece .service'ini (önceki baytları deploy-logs'ta) kurar ve TEK kapılı daemon-reload yapar; gece
smoke'u P2'nin çalıştığını (S1b + S2, günlük/atıf/özet, mühürler) kanıtlar; smoke geçmezse ya da sabitlemeden sonra hata
olursa engine-app 79b27cf'ye ve gece birimi P1a baytlarına döner (çıktı borusu erken kapansa da: SIGPIPE betiği öldürmez,
kayıt sürer; geri koyma reload'undan sonra 6G kayması çıkış 3); reload sonrası 6G kayması gece birimini geri alır;
`--rollback` 79b27cf durumuna döner (79b27cf betiği kurulumu "zaten dağıtılmış" tanır); başarıdan sonra yeniden çalıştırma
"zaten dağıtılmış" der; `--check` P1a/P1b satırlarını ve P2 kabul satırlarını basar, araştırma ağacını değiştirmez.
"""
from __future__ import annotations

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
from test_research_engine_release import JOURNALCTL, _git, _has, _tree_digest, own_tmp  # noqa: E402
from test_research_engine_release_p1b import (APP_SHA, BASH, DSVC, DTMR, GIT, OLD_SCRIPT, OLD_TIP,  # noqa: E402
                                              SVC, SYSTEMCTL, TMR, VPS_APP, Sandbox, _at, _blob, _from_template,
                                              _no_forbidden)
from test_research_engine_release_p1b import SCRIPT as P79_SCRIPT  # noqa: E402

SCRIPT = ROOT / "deploy" / "releases" / "tb-engine-44d5353.sh"
#: Betiğin kayıtlı sha256'sı (sürüm notu ve sahibe verilen değer; betik değişirse bu da bilinçli değişir).
SCRIPT_SHA256 = "a4f04e6c26cd9f0709bed5902113b23896e8109ddb161bcbeecd88b6a28cddd9"
TEXT = SCRIPT.read_text(encoding="utf-8")
TIP = re.search(r'^TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
PREV_TIP = re.search(r'^PREV_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
P1A_TIP = re.search(r'^P1A_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
P79_TEXT = P79_SCRIPT.read_text(encoding="utf-8")
DDROP, NDROP = Path(f"{DSVC}.d") / "50-tb-engine.conf", Path(f"{SVC}.d") / "50-tb-engine.conf"
UTC = timezone.utc

needs_sandbox = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and BASH and GIT and shutil.which("systemd-analyze") and shutil.which("flock")
         and shutil.which("getent") and all(_has(s) for s in (TIP, PREV_TIP, P1A_TIP, APP_SHA, VPS_APP, OLD_TIP))),
    reason="sandbox Linux + bash + git + systemd-analyze + flock + getent ve geçmişte hedef/79b27cf/57cfef1/P1a/app commit'leri ister")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _code() -> str:
    return "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))


# ============================================================================ statik
def test_script_is_syntax_ok_recorded_and_states_the_rules():
    lines = TEXT.splitlines()
    assert len(lines) <= 1385, "küçük, ayrılmış betik (79b27cf + P2 gece birimi/smoke/--check/--rollback)"
    assert subprocess.run([BASH or "bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert _sha(SCRIPT.read_bytes()) == SCRIPT_SHA256
    assert SCRIPT.name == f"tb-engine-{TIP[:7]}.sh"
    for flag in ("--dry-run", "--check", "--ab-report", "--rollback", "--backfill"):
        assert flag in TEXT
    assert re.search(r'^BRANCH_REF="refs/heads/claude/gifted-knuth-0ehpcs"$', TEXT, re.M), "PR dalı"
    head = "\n".join(lines[:52])
    assert "ÖN KOŞUL — P1b (79b27cf) KURULU" in head and "EN ERKEN DAĞITIM" in head and "BİTTİKTEN sonra" in head
    assert "en az 1 GÜN" in head and '"öğrenmeyi' in head and "restart-at.txt" in head
    steps = [re.search(rf"#\s+{i}\) sudo bash tb-engine-{TIP[:7]}\.sh {flag}", head)
             for i, flag in enumerate(("--dry-run", " *#", "--check", "--ab-report"), 1)]
    assert all(steps) and [m.start() for m in steps] == sorted(m.start() for m in steps), \
        "sahibin adımları sırayla: dry-run → dağıt → --check → --ab-report"
    assert "tb-engine-79b27cf.sh --rollback" in head and "tb-engine-4962209.sh --rollback" in head, "geri alma zinciri"
    assert "75 dk" in head and "≥ 50 dk" in head and "≥ 30 dk" in head and "MemoryMax 768M" in head and "614 MiB" in head
    assert "setup_vps_v3.sh ASLA" in head


@pytest.mark.skipif(not all(_has(s) for s in (TIP, PREV_TIP, P1A_TIP, APP_SHA)), reason="commit'ler geçmişte yok (sığ klon)")
def test_script_pins_match_the_code_commit():
    from tradingbot.research_engine import attribution as AT
    from tradingbot.research_engine import cfgrid as CG
    assert _git("merge-base", "--is-ancestor", TIP, "HEAD").returncode == 0
    assert _git("merge-base", "--is-ancestor", PREV_TIP, TIP).returncode == 0, "79b27cf hedefin atası"
    assert _git("merge-base", "--is-ancestor", P1A_TIP, PREV_TIP).returncode == 0
    if _has(VPS_APP):
        assert _git("merge-base", "--is-ancestor", VPS_APP, TIP).returncode == 0, "VPS app'i (db5db96) hedefin atası"
    assert re.search(rf'^TIP="{PREV_TIP}"', P79_TEXT, re.M), "PREV_TIP = 79b27cf betiğinin hedefi"
    for var, path in (("SVC_SHA256", f"deploy/{SVC}"), ("TMR_SHA256", f"deploy/{TMR}"), ("DSVC_SHA256", f"deploy/{DSVC}"),
                      ("DTMR_SHA256", f"deploy/{DTMR}")):
        want = _sha(_blob(TIP, path))
        assert re.search(rf'^{var}="{want}"', TEXT, re.M), var
        if var != "SVC_SHA256":                     # zamanlayıcı + veri birimi 79b27cf'den beri aynı (yeniden kurulmaz)
            assert re.search(rf'^{var}="{want}"', P79_TEXT, re.M), var
    p1a = _sha(_blob(PREV_TIP, f"deploy/{SVC}"))
    assert p1a == _sha(_blob(P1A_TIP, f"deploy/{SVC}")) and re.search(rf'^P1A_SVC_SHA256="{p1a}"', TEXT, re.M)
    assert re.search(rf'^SVC_SHA256="{p1a}"', P79_TEXT, re.M), "VPS'te kurulu gece birimi = P1a baytları"
    unit = _blob(TIP, f"deploy/{SVC}").decode()
    mem = int(re.search(r"\bENGINE_MEM=(\d+)", TEXT).group(1))
    assert "\nMemoryMax=768M\n" in unit and "\nMemoryHigh=640M\n" in unit and mem == 768 << 20
    assert f"\nEnvironment=ENGINE_EXPECTED_MEMORY_MAX={mem}\n" in unit
    runner = _blob(TIP, "tests/standalone/run_engine_invariants.py").decode("utf-8")
    n = len(re.findall(r'^\s+\(\d+, "test_research_engine_\w+",', runner, re.M))
    assert n == 90 and re.search(rf"^INV_TESTS={n}\b", TEXT, re.M)
    acc = " ".join(re.search(r"^ACCEPTANCE = \(([^)]*)\)", runner, re.M).group(1).split())
    got = " ".join(re.search(r"^ACCEPT = \[([^\]]*)\]", TEXT, re.M).group(1).split())
    assert got == acc, "betiğin kabul listesi = koşucununki"
    ver = re.search(r'^ENGINE_VERSION = "([^"]+)"', _blob(TIP, "tradingbot/research_engine/__init__.py").decode(), re.M).group(1)
    assert ver == "research_engine_v1_p2" and re.search(rf'^ENGINE_VER="{ver}"$', TEXT, re.M)
    # mühürlü P2 kayıtlarının kaynağı TIP'tekiyle aynı (P3 kodu research_engine'e eklenir; P2 mühürlerine dokunmaz)
    assert _git("diff", "--quiet", TIP, "HEAD", "--", "tradingbot/research_engine/attribution.py",
                "tradingbot/research_engine/cfgrid.py").returncode == 0, "mühürler TIP'in kodundan"
    assert re.search(rf'^ATTR_SHA="{AT.ATTRIBUTION_SHA}"', TEXT, re.M) and re.search(rf'^CFG_SHA="{CG.CFGRID_SHA}"', TEXT, re.M)


def test_script_touches_only_engine_units_and_reloads_only_through_the_gate():
    code = _code()
    for m in re.finditer(r'(?<![\w-])systemctl (start|stop|restart|try-restart|reload-or-restart|reload|kill|enable|'
                         r'disable|mask|set-property|edit|revert|daemon-reexec|reset-failed)\b([^\n;|&)]*)', code):
        if code[max(0, m.start() - 5):m.start()].endswith("sudo "):      # sahibe basılan talimat metni
            continue
        assert m.group(1) not in ("enable", "disable", "set-property", "edit", "revert"), m.group(0)
        assert re.match(r'\s*(--\S+\s+)*"?\$(SVC|DSVC|BF)\b', m.group(2)), f"motor birimi dışında: {m.group(0)}"
    reloads = [i for i, ln in enumerate(code.splitlines()) if re.search(r"(?<![\w-])systemctl daemon-reload", ln)]
    assert len(reloads) == 2, "daemon-reload yalnız reload_units ve undo_units'te"
    assert re.search(r"reload_units\(\) \{[^}]*\n  reload_gate \|\| return 1\n  systemctl daemon-reload; RELOADED=1", code)
    assert re.search(r'elif reload_gate; then systemctl daemon-reload\n    if \[\[ "\$\(sc_show "\$WORKER" MemoryMax\)" == "\$MEM_EXPECT" \]\]; '
                     r'then ok "birim dosyaları önceki[^\n]*\n    else DRIFT=1; UNDO_NOTE=', code), "geri koyma reload'u da 6G denetler"
    assert "trap '' PIPE" in code and "exec tee -p -a \"$LOG\"" in code, "kopmuş boru/bağlantı geri dönüşü öldürmez"
    assert re.search(r'mv -- "\$BASELINE" "\$BASELINE\.rolledback-', code), "--rollback taban kaydını kaldırır"
    assert not re.search(r'(rm|install|cp)\b[^\n]*"\$SD/\$(DSVC|DTMR)"', code), "veri birimi dosyaları değişmez"
    assert re.search(r'^for u in "\$SVC" "\$TMR"; do nu_put "\$SD/\$u" "\$W/deploy/\$u"; done', code, re.M)
    assert not re.search(r"^\s*(bash|sh)\s+\S*setup_vps", code, re.M)
    assert len(re.findall(r"^\s*systemd-run --unit=\"\$BF\"", code, re.M)) == 1


def test_time_is_rechecked_before_the_units_and_before_the_night_smoke_and_rollback_errors_say_what_changed():
    code = _code()
    assert "if time_ok 75; then ok" in code, "başta: koşucu + iki smoke için ≥ 75 dk"
    i50, i_fetch = code.index("\ntime_ok 50 || die "), code.index('\nfetch_tip "$ENG" "$TIP" "$W"')
    assert i50 < i_fetch < code.index('nu_put "$SD/$u" "$W/deploy/$u"'), "koşucudan sonra, hiçbir şeye dokunmadan önce ≥ 50 dk"
    assert "HİÇBİR ŞEYE DOKUNULMADI" in code[i50:code.index("\n", i50 + 1)]
    i30 = code.index("\ntime_ok 30 || { back_out; die ")
    assert code.index('timeout "$DSMOKE_TIMEOUT" systemctl start "$DSVC"') < i30 < code.index(
        'timeout "$SMOKE_TIMEOUT" systemctl start "$SVC"'), "gece smoke'undan (S1b/S2 ≈ 20–25 dk) hemen önce ≥ 30 dk"
    assert re.search(r'CHANGED\+=\("engine-app=\$\{PREV_TIP:0:7\}"\); pin_eng "\$PREV_TIP"', code), \
        "--rollback: sabitleme yarıda düşerse on_err 'değişen: hiçbir şey' demez"
    assert re.search(r'elif \[\[ "\$MODE" == --rollback \]\]; then printf [^\n]*yazılan birim dosyaları[^\n]*--rollback', code)
    assert re.search(r'revert_eng\(\) \{[^}]*\|\| return 1; echo [^\n]*\|\| true; fi\n  return 0', code), \
        "yazılamayan çıktı geri dönüşü 'başarısız' yapmaz"


def test_heavy_steps_run_at_lowest_cpu_and_io_priority():
    assert re.search(r'^LOW=\(nice -n 19\); if command -v ionice .* LOW\+=\(ionice -c3 -t\)', TEXT, re.M)
    for step in ('-s -m compileall -q tradingbot scripts', '-s tests/standalone/run_engine_invariants.py',
                 "-s -c 'import resource as r, sys, tradingbot.cli", '-s scripts/bot_scorecard.py'):
        lines = [ln for ln in TEXT.splitlines() if step in ln and "$VENV/bin/python" in ln]
        assert lines and all('"${LOW[@]}" "$VENV/bin/python"' in ln for ln in lines), step


def test_shellcheck_clean():
    exe = shutil.which("shellcheck") or str(Path(sys.executable).with_name("shellcheck"))
    if not os.access(exe, os.X_OK):
        pytest.skip("shellcheck yok (betiğin --dry-run'ı VPS'te varsa koşar)")
    cp = subprocess.run([exe, str(SCRIPT)], capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stdout + cp.stderr


# ============================================================================ gömülü araçlar (deterministik, dağıtımsız)
def _tool(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    a = TEXT.index("read -r -d '' PYTOOL <<'PY' || true\n") + len("read -r -d '' PYTOOL <<'PY' || true\n")
    stub = tmp / "jbin"
    stub.mkdir(exist_ok=True)
    (stub / "journalctl").write_text(JOURNALCTL.replace("@PY@", sys.executable), encoding="utf-8")
    (stub / "journalctl").chmod(0o755)
    return subprocess.run([sys.executable, "-I", "-c", TEXT[a:TEXT.index("\nPY\nrpy()", a)], *args], capture_output=True,
                          text=True, timeout=120, env={"PATH": f"{stub}:/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": "UTC"})


ATTR = re.search(r'^ATTR_SHA="([0-9a-f]{64})"', TEXT, re.M).group(1)
CFG = re.search(r'^CFG_SHA="([0-9a-f]{64})"', TEXT, re.M).group(1)
FULL = ["S0", "S1a", "S3", "S1b", "S2", "S7", "S7b"]


def _wj(p: Path, d) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")


def _night_fixture(res: Path, *, rid="20261011T101500Z", started=None, finished=None, result="SUCCESS", plan=FULL,
                   ab="AB_RELEASE_DAY", engine="research_engine_v1_p2", attr=ATTR, cfg=CFG, digest=3000, mem=None,
                   p2files=True, write_summary=True, flags=(), journal=None, fidelity=None, rehydrate=None, s2_errors=0,
                   epoch_day=None, sanity=None) -> dict:
    started = started or datetime.now(UTC).isoformat()
    day = started[:10]
    stages = {s: {"status": "OK", "duration_s": 12.0} for s in plan}
    if "S1b" in plan:
        stages["S1b"]["result"] = {"journal": {"rows": 40, "built_rows": 40, "pending": 0},
                                   "reconcile": {"status": "OK", "books_not_ok": []}}
    if "S2" in plan:
        stages["S2"]["result"] = {"rows": 30, "built_rows": 30, "pending": 0, "errors": s2_errors, "attribution_sha": attr,
                                  "cfgrid_sha": cfg}
    st = {"schema": "engine_run_status_v1", "engine": engine, "run_id": rid, "started_at": started,
          "finished_at": finished or started, "result": result, "exit_code": 0, "plan": plan, "stages": stages,
          "flags": list(flags), "data_seal": "s" * 64, "shas": {"engine": TIP, "app": VPS_APP},
          "selfcheck": {"status": "OK", "ab": {"status": ab}, "isolation": {"checks": {}}},
          "resources": {"memory_peak_bytes": None, "memory_max_bytes": None, "memory_peak_ratio": None, **(mem or {})}}
    _wj(res / "runs" / rid / "run_status.json", st)
    if write_summary:
        _wj(res / "summary" / "run_status.json", st)
        _wj(res / "summary" / "data_status.json", {"data_seal": "s" * 64})
        (res / "runs" / "engine_epochs.jsonl").write_text(json.dumps({"engine_sha": TIP, "epoch_day": epoch_day or day}) + "\n",
                                                          encoding="utf-8")
        for n in (f"snapshots/{day}.json.gz", "summary/daily_target.json", "summary/engine_summary.json",
                  f"backup/research-small-{day}.tar.gz", f"backup/research-small-{day}.tar.gz.sha256"):
            (res / n).parent.mkdir(parents=True, exist_ok=True)
            (res / n).write_bytes(b"x")
        (res / "summary" / "digest_tr.md").write_bytes(b"#" * digest)
    if p2files:
        _wj(res / "journal" / "tj_v1" / "_build.json", {
            "rows": 40, "owner_fields_ok": True, "fee_identity_violations": 0, "r_check_failures": 0,
            "fidelity": fidelity or {"eligible": 20, "ok": 19, "rate": 0.95, "gate_pass": True,
                                     "failures_by_reason": {"EXIT_REASON_DIFFERS": 1}, "not_eligible": {"SPOT": 4}},
            "rehydrate": rehydrate or {"strategy_paper_box": {"E2026-10-03": {"n": 30, "ok": 30, "rate": 1.0,
                                                                              "meets_target": True}}},
            **(journal or {})})
        _wj(res / "attribution" / "_build.json", {"attribution_sha": attr, "cfgrid_sha": cfg, "rows": 30,
                                                  "grid": {"OK": 20, "NE:FIDELITY": 10}, "rank_src": {"cf_aux_v1": 20},
                                                  **({"sanity_5_10_box_stop_width": sanity} if sanity else {})})
        for n in ("journal/tj_v1/_index.json.gz", "attribution/_index.json.gz"):
            (res / n).write_bytes(gzip.compress(b"{}"))
    return st


def test_night_smoke_requires_p2_stages_outputs_seals_and_memory(tmp_path):
    user = pwd.getpwuid(os.getuid()).pw_name
    t0 = str(time.time() - 5)

    def case(name, **kw):
        res = tmp_path / name / "research"
        _night_fixture(res, **kw)
        return _tool(tmp_path, "smoke", str(res), TIP, "", t0, user, "research_engine_v1_p2", ATTR, CFG, "805306368")
    ok = case("ok", mem={"memory_max_bytes": 805306368, "memory_peak_ratio": 0.41, "memory_peak_bytes": 330 << 20},
              flags=("S1B_BACKLOG",))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "S1b OK" in ok.stdout and "kaldığı yerden sürer" in ok.stdout
    assert case("ab_off_redeploy", plan=["S0", "S1s"], result="AB_OFF", ab="AB_OFF").returncode == 0, \
        "aynı kodun A/B KAPALI gününde yeniden dağıtım: P2 dosyaları önceki çalıştırmadan"
    for name, kw, why in (("no_p2_plan", {"plan": ["S0", "S1a", "S3", "S7", "S7b"]}, "planda S1b yok"),
                          ("old_code", {"engine": "research_engine_v1_p1b"}, "motor sürümü"),
                          ("attr_seal", {"attr": "a" * 64}, "attribution_v1 mührü"),
                          ("cfg_seal", {"cfg": "c" * 64}, "cfgrid_v1 mührü"),
                          ("digest", {"digest": 8193}, "> 8 KB"),
                          ("peak", {"mem": {"memory_peak_ratio": 0.83}}, "memory.peak 0,83"),
                          ("old_unit", {"mem": {"memory_max_bytes": 536870912}}, "memory.max 536870912"),
                          ("ab_off_no_p2", {"plan": ["S0", "S1s"], "result": "AB_OFF", "ab": "AB_OFF", "p2files": False},
                           "beklenen dosya yok: journal/tj_v1/_build.json"),
                          ("failed", {"result": "FAILED"}, "sonuç FAILED")):
        cp = case(name, **kw)
        assert cp.returncode == 1 and "KALDI:" in cp.stdout and why in cp.stdout, (name, cp.stdout + cp.stderr)


def test_p2_check_rows_measure_what_the_engine_wrote_and_never_pass_the_unmeasured(tmp_path):
    from tradingbot.research_engine.summary import claim_word_violations
    empty = _tool(tmp_path, "p2", str(tmp_path / "yok"), TIP, ATTR, CFG, "", "")
    assert empty.returncode == 0, empty.stderr
    rows = [ln for ln in empty.stdout.splitlines() if ln.startswith("   [")]
    assert len(rows) == 10 and all(ln.lstrip().startswith("[ölçülemedi]") for ln in rows), empty.stdout
    # son 03:40'ı geçmiş takvim gecesi ve öncekiler; sürüm günü 30 gün önce (A/B penceresi geçti: her gece AÇIK)
    last = (datetime.now(UTC) - timedelta(minutes=221)).replace(hour=1, minute=37, second=0, microsecond=0)
    rel = (last - timedelta(days=30)).strftime("%Y-%m-%d")
    box = {"E2026-09-30": {"<0.5": {"n": 12, "mean_r": -0.56, "codes": {"STOP_TOO_TIGHT": 9, "NOISE_LOSS": 2, "X": 1}},
                           "0.5-1": {"n": 8, "mean_r": 0.17, "codes": {}}}}

    def nights(res, days, late=False, **kw):
        for i in days:
            s = last - timedelta(days=i)
            _night_fixture(res, rid=s.strftime("%Y%m%dT%H%M%SZ"), started=s.isoformat(), write_summary=(i == min(days)),
                           finished=(s + timedelta(minutes=130 if late and i == 0 else 40)).isoformat(), epoch_day=rel, **kw)
    good = tmp_path / "good" / "research"
    nights(good, (2, 1, 0), sanity=box)
    _wj(good / "target" / "daily_utc.jsonl", {"day": "2026-10-09", "rev": 0, "books": {"b": {"anchor_check": {"ok": True}}}})
    out = _tool(tmp_path, "p2", str(good), TIP, ATTR, CFG, "41", "12").stdout
    assert not claim_word_violations(out.splitlines())
    for row in ("S1b/S2 son tam gece", "1 günlük = arşiv = ledger", "2 sahip alanları", "5 fidelity 19/20 = %95,0",
                "6 rehydrate eşleşmesi", "8 mühürler", "10 UTC günü MTM: 1 gün", "12 digest_tr.md 2,9 KB",
                "13 son 3 ardışık gece"):
        assert re.search(r"\[tamam\]\s+" + re.escape(row), out), (row, out)
    assert re.search(r"\[bilgi\]\s+11 Box stop genişliği .*E2026-09-30: <0,5 12 işlem ort\. -0,56R \(STOP_TOO_TIGHT 9, "
                     r"NOISE_LOSS 2\) · 0,5–1 8 işlem ort\. 0,17R \(—\)", out), out
    bad = tmp_path / "bad" / "research"
    nights(bad, (2, 1, 0), late=True, attr="a" * 64, digest=9000, journal={"fee_identity_violations": 2},
           fidelity={"eligible": 10, "ok": 8, "rate": 0.8, "gate_pass": False, "failures_by_reason": {"PATH_GAP": 2}},
           rehydrate={"strategy_paper_box": {"E0": {"n": 20, "ok": 18, "rate": 0.9, "meets_target": False}}})
    _wj(bad / "target" / "daily_utc.jsonl", {"day": "2026-10-09", "rev": 0, "books": {"b": {"entries_check": {"ok": False}}}})
    out = _tool(tmp_path, "p2", str(bad), TIP, ATTR, CFG, "", "12").stdout
    for row in ("2 sahip alanları", "5 fidelity 8/10 = %80,0", "6 rehydrate eşleşmesi", "8 mühürler", "10 UTC günü MTM",
                "12 digest_tr.md 8,8 KB", "13 son 3 ardışık gece"):
        assert re.search(r"\[DİKKAT\]\s+" + re.escape(row), out), (row, out)
    assert "why-lost --days 7: HATA satır" in out and "strategy_paper_box E0 %90 (20)" in out
    # kaydı olmayan gece (birim hiç başlamadı) atlanmaz: başarısız sayılır; S2 işlem hatası (int) satırları düşürmez
    gap = tmp_path / "gap" / "research"
    nights(gap, (3, 2, 0), s2_errors=2)
    cp = _tool(tmp_path, "p2", str(gap), TIP, ATTR, CFG, "41", "12")
    assert cp.returncode == 0 and "Traceback" not in cp.stderr, cp.stderr
    assert len([ln for ln in cp.stdout.splitlines() if ln.startswith("   [")]) == 10, cp.stdout
    assert re.search(r"\[DİKKAT\]\s+S1b/S2 son tam gece .*işlem hatası 2", cp.stdout), cp.stdout
    day1 = (last - timedelta(days=1)).strftime("%Y-%m-%d")
    assert re.search(r"\[DİKKAT\]\s+13 son 3 ardışık gece .*; %s kayıt yok; " % day1, cp.stdout), cp.stdout
    # A/B penceresinde (sürüm günü 6 gün önce) gün-yıl sırası tek geceler KAPALI: kayıtları olmasa da sayılmaz
    abw, rel6 = tmp_path / "abw" / "research", (last - timedelta(days=6)).strftime("%Y-%m-%d")
    on = [i for i in range(6) if (last - timedelta(days=i)).timetuple().tm_yday % 2 == 0]
    for i in on:
        s = last - timedelta(days=i)
        _night_fixture(abw, rid=s.strftime("%Y%m%dT%H%M%SZ"), started=s.isoformat(), write_summary=(i == min(on)),
                       finished=(s + timedelta(minutes=40)).isoformat(), epoch_day=rel6)
    out = _tool(tmp_path, "p2", str(abw), TIP, ATTR, CFG, "41", "12").stdout
    assert len(on) == 3 and re.search(r"\[tamam\]\s+13 son 3 ardışık gece", out) and "kayıt yok" not in out, out


# ============================================================================ sandbox
#: P1b sahtesine üç küçük ek (modül başı): yüklü MemoryMax birim dosyasından, gece smoke'u düşürme.
SYSTEMCTL_P2 = SYSTEMCTL.replace(
    '    S["loaded"] = {u: fsha(u) for u in (SVC, TMR, DSVC, DTMR) if (SD / u).exists()}\n',
    '    S["loaded"] = {u: fsha(u) for u in (SVC, TMR, DSVC, DTMR) if (SD / u).exists()}\n'
    '    S["loaded_mem"] = {u: str(int(m.group(1)) << {"M": 20, "G": 30}[m.group(2)]) for u in (SVC, DSVC)\n'
    '                       for m in [re.search(r"^MemoryMax=(\\d+)([MG])$", (SD / u).read_text(), re.M) if (SD / u).exists() else None]\n'
    '                       if m}\n').replace(
    '"MemoryMax": MEM.get(u, "infinity") if u in ld else "infinity"',
    '"MemoryMax": S.get("loaded_mem", {}).get(u) or MEM.get(u, "infinity") if u in ld else "infinity"').replace(
    "import json, os, sys, pathlib, hashlib", "import json, os, re, sys, pathlib, hashlib").replace(
    '    if os.environ.get("FAKE_RELOAD_DRIFT") and DSVC in S["loaded"]:\n',
    '    if os.environ.get("FAKE_DRIFT_ON_SVC_SHA") and os.environ["FAKE_DRIFT_ON_SVC_SHA"] == S["loaded"].get(SVC):\n'
    '        S.setdefault("props", {}).setdefault(W, {})["MemoryMax"] = "4294967296"\n'
    '    if os.environ.get("FAKE_RELOAD_DRIFT") and DSVC in S["loaded"]:\n').replace(
    "    import fakeengine\n",
    '    if os.environ.get("FAKE_NIGHT_FAIL") and u == SVC:\n'
    '        S["result"][u], S["ems"][u] = "exit-code", 1; save(); sys.exit(1)\n'
    "    import fakeengine\n")
assert SYSTEMCTL_P2.count("loaded_mem") == 2 and "FAKE_NIGHT_FAIL" in SYSTEMCTL_P2 and ", re, sys" in SYSTEMCTL_P2 \
    and "FAKE_DRIFT_ON_SVC_SHA" in SYSTEMCTL_P2


def _p2_stubs(sb: Sandbox, cache: Path, fail_flag: Path) -> None:
    """Sahte systemctl (P2 ekleri) + koşucu önbellekli venv python'u (servis ortamı `env -i` ile temizlendiğinden yollar
    dosyaya gömülür; anahtar ağacın HEAD'i). `fail_flag` varsa gömülü aracın `dlast` komutu düşer (sabitlemeden sonra
    beklenmeyen hata)."""
    (sb.stub / "systemctl").write_text(SYSTEMCTL_P2.replace("@PY@", sys.executable), encoding="utf-8")
    py = sb.base / "venv" / "bin" / "python"
    py.write_text(f'#!/usr/bin/env bash\nif [ -e "{fail_flag}" ] && [ "$4" = dlast ]; then exit 7; fi\n'
                  f'if [ "$2" = tests/standalone/run_engine_invariants.py ] && [ "$3" = --tree ] && [ "$6" = --json ]; then\n'
                  f'  c="{cache}.$(git -C "$4" rev-parse HEAD 2>/dev/null).json"\n'
                  f'  if [ -s "$c" ]; then cp "$c" "$7"; echo "(önbellek: bu ağacın gerçek koşucu sonucu)"; exit 0; fi\n'
                  f'  "{sys.executable}" "$@"; rc=$?; [ -s "$7" ] && cp "$7" "$c"; exit $rc\nfi\n'
                  f'exec "{sys.executable}" "$@"\n', encoding="utf-8")


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """Yerel çıplak kaynak depo: claude/gifted-knuth-0ehpcs = P2 hedefi; app-main = f8b05fb, app-db5db96 = VPS app'i."""
    if not all(_has(s) for s in (TIP, PREV_TIP, P1A_TIP, APP_SHA, VPS_APP, OLD_TIP)):
        pytest.skip("commit'ler yok")
    src = tmp_path_factory.mktemp("src") / "src.git"
    assert _git("init", "-q", "--bare", str(src)).returncode == 0
    assert _git("fetch", "-q", "--no-tags", str(ROOT), "+HEAD:refs/heads/scratch", cwd=src).returncode == 0
    for ref, sha in (("claude/gifted-knuth-0ehpcs", TIP), ("app-main", APP_SHA), ("app-db5db96", VPS_APP)):
        assert _git("update-ref", f"refs/heads/{ref}", sha, cwd=src).returncode == 0
    assert _git("update-ref", "-d", "refs/heads/scratch", cwd=src).returncode == 0
    return src


ENV = {"FAKE_ALERT": "1", "FAKE_NPROC": "2"}


@pytest.fixture(scope="module")
def vps_template(tmp_path_factory, source):
    """Bugünkü VPS: gerçek P1a betiği, app + engine-app db5db96, gerçek 57cfef1 ve 79b27cf betikleri (drop-in'ler var)."""
    root = tmp_path_factory.mktemp("vps")
    aux = tmp_path_factory.mktemp("aux")
    sb = Sandbox(root, source, **ENV)
    _p2_stubs(sb, aux / "inv", aux / "fail_dlast")
    sb.p1a()
    for d in ("app", "engine-app"):
        repo = sb.base / d
        assert _git("fetch", "-q", str(source), "refs/heads/app-db5db96", cwd=repo).returncode == 0
        assert _git("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", VPS_APP, cwd=repo).returncode == 0
    (sb.base / "deploy-logs" / "db5db96-restart-at.txt").write_text(f"{int(time.time()) - 86400 - 600}\nx\n")
    for script in (OLD_SCRIPT, P79_SCRIPT):
        cp = sb.run(script=script)
        assert cp.returncode == 0 and "DAĞITILDI: 33/33" in cp.out, cp.out[-4000:]
    assert sb.eng_head() == PREV_TIP and _sha((sb.sd / SVC).read_bytes()) == _sha(_blob(PREV_TIP, f"deploy/{SVC}"))
    assert (sb.sd / NDROP).read_text(encoding="utf-8").startswith(f"# tb-engine-{P1A_TIP[:7]}:")
    assert (sb.sd / DDROP).read_text(encoding="utf-8").startswith(f"# tb-engine-{OLD_TIP[:7]}:")
    return root, aux


def _sb(tmp_path: Path, vps_template, source, **env) -> Sandbox:
    return _from_template(tmp_path, vps_template[0], source, **ENV, **env)


def _units(sb: Sandbox) -> dict:
    return {str(p.relative_to(sb.sd)): p.read_bytes() for p in sb.sd.rglob("*") if p.is_file()}


def _p79_state(sb: Sandbox, units0: dict) -> None:
    """79b27cf durumu: engine-app 79b27cf, birim dosyaları (gece P1a baytları + drop-in'ler + veri) şablondakiyle aynı."""
    assert sb.eng_head() == PREV_TIP and _units(sb) == units0
    st = sb.state_json()
    assert st["enabled"] == {TMR: True, DTMR: True} and st["active"] == {TMR: True, DTMR: True}


@needs_sandbox
def test_dry_run_checks_everything_and_changes_nothing(tmp_path, source, vps_template):
    sb = _sb(tmp_path, vps_template, source)
    snap, made0 = sb.snap(), len(own_tmp(sb.fake))
    cp = sb.run("--dry-run", script=SCRIPT)
    assert cp.returncode == 0, cp.out[-6000:]
    m = re.search(r"KURU ÇALIŞMA: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "22", cp.out[-3000:]
    for name in ("P1b-kurulu", "yedek-doğrulandı", "1g-pencere-dışı", "kaynak-TIP", "app-SHA-ata", "79b27cf-ata",
                 "birim-sha256", "birim-sözleşmesi", "systemd-analyze-verify", "compileall", "bağımsız-koşucu", "yalıtım-AST",
                 "status-kuru", "worker-MemoryMax=6G", "motor-boşta"):
        assert re.search(rf"\[tamam\] #\d+ {re.escape(name)}", cp.out), name
    assert "90 geçti · 0 kaldı · 0 atlandı" in cp.out
    assert re.search(r"P1b-kurulu .*gece birimi P1a baytları \+ açık, engine-app 79b27cf ⊒ 79b27cf", cp.out), cp.out[-3000:]
    assert re.search(r"UYARI\s+79b27cf A/B penceresi \(sürüm günü \d{4}-\d\d-\d\d\): 0/14 gece geçti — P2 yeni bir A/B", cp.out)
    sb.untouched_since(snap)
    _no_forbidden(sb)
    made = own_tmp(sb.fake)[made0:]
    assert made and not [p for p in made if p.exists()], "geçici klon silinmeli"


@needs_sandbox
@pytest.mark.parametrize("case", ["night_unit_changed", "engine_app_db5db96", "data_timer_off", "backfill_running", "too_late"])
def test_missing_p1b_or_a_busy_engine_refuses_before_any_change(tmp_path, source, vps_template, case):
    sb = _sb(tmp_path, vps_template, source)
    if case == "night_unit_changed":
        p = sb.sd / SVC
        p.write_text(p.read_text(encoding="utf-8").replace("MemoryMax=512M", "MemoryMax=600M"), encoding="utf-8")
    if case == "engine_app_db5db96":
        assert _git("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", VPS_APP, cwd=sb.base / "engine-app").returncode == 0
    st = sb.state_json()
    if case == "data_timer_off":
        st["active"][DTMR] = False
    if case == "backfill_running":
        st["bf"] = "active"
    (sb.fake / "systemd.json").write_text(json.dumps(st), encoding="utf-8")
    snap = sb.snap()
    cp = sb.run(script=SCRIPT, **({"FAKE_UTC_HM": "06:50"} if case == "too_late" else {}))
    want = {"night_unit_changed": "ne P1a'nın ne P2'nin baytları", "engine_app_db5db96": "79b27cf'yi (79b27cf) içermiyor",
            "data_timer_off": "veri zamanlayıcısı açık/aktif değil", "backfill_running": "DUR: motor-boşta",
            "too_late": "sonraki 4h penceresine 70 dk (en az 75 dk gerekir)"}[case]
    assert cp.returncode == 1 and want in cp.out and "HİÇBİR ŞEYE DOKUNULMADI" in cp.out, cp.out[-3000:]
    sb.untouched_since(snap)
    _no_forbidden(sb)


@needs_sandbox
def test_deploy_installs_the_p2_night_unit_with_one_gated_reload_check_rerun_and_rollback(tmp_path, source, vps_template):
    from tradingbot.research_engine.summary import claim_word_violations
    sb = _sb(tmp_path, vps_template, source)
    units0, n0 = _units(sb), len(sb.log())
    cp = sb.run(script=SCRIPT)
    assert cp.returncode == 0, cp.out[-6000:]
    m = re.search(r"DAĞITILDI: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "33", cp.out[-3000:]
    log = sb.log()[n0:]
    _no_forbidden(sb)
    assert [ln for ln in log if ln.startswith("daemon-reload")] == ["daemon-reload"], "TEK kapılı reload"
    assert _at(log, "daemon-reload") < _at(log, f"start {DSVC}") < _at(log, f"start {SVC}")
    assert not [ln for ln in log if ln.split()[0] in ("enable", "disable", "stop")], "zamanlayıcılara dokunulmaz"
    units = _units(sb)
    assert units[SVC] == _blob(TIP, f"deploy/{SVC}") and units[SVC] != units0[SVC], "gece .service = P2 baytları"
    assert {k: v for k, v in units.items() if k != SVC} == {k: v for k, v in units0.items() if k != SVC}, \
        "zamanlayıcı, drop-in'ler ve veri birimi dosyaları bayt bayt aynı"
    bak = list((sb.base / "deploy-logs").glob(f"engine-{TIP[:7]}-prev-units-*/{SVC}"))
    assert len(bak) == 1 and bak[0].read_bytes() == units0[SVC], "önceki gece birimi baytları deploy-logs'ta"
    assert sb.eng_head() == TIP
    rs = json.loads((sb.res / "summary" / "run_status.json").read_text(encoding="utf-8"))
    assert rs["result"] == "SUCCESS" and rs["engine"] == "research_engine_v1_p2" and rs["shas"]["engine"] == TIP
    assert {"S1b", "S2"} <= set(rs["plan"]) and rs["stages"]["S2"]["result"]["attribution_sha"] == ATTR
    assert (sb.res / "summary" / "digest_tr.md").stat().st_size <= 8192 and (sb.res / "journal" / "tj_v1" / "_build.json").is_file()
    assert "S1b OK" in cp.out and re.search(r"\[tamam\] #\d+ gece-smoke", cp.out)
    st = sb.state_json()
    assert st["enabled"] == {TMR: True, DTMR: True} and st["active"] == {TMR: True, DTMR: True}
    assert st["loaded_mem"][SVC] == "805306368"
    # --check: araştırma ağacı değişmez; P1a, P1b ve P2 satırları; iddia kelimesi yok
    d0 = _tree_digest(sb.res)
    ck = sb.run("--check", script=SCRIPT)
    assert ck.returncode == 0, ck.out[-4000:]
    for s in (f"engine-app {TIP[:7]}", "gece birimi dosyası: P2 baytları (640M/768M)", "K1 elle smoke (gece)", "V2 tazelik",
              "kabul ölçütleri — P2", "1 günlük = arşiv = ledger", "5 fidelity", "8 mühürler", "13 son 3 ardışık gece"):
        assert s in ck.out, s
    assert re.search(r"\[tamam\]\s+8 mühürler attribution_v1 914875a1f663", ck.out), ck.out[-3000:]
    assert re.search(r"\[tamam\]\s+12 digest_tr\.md [\d,]+ KB \(≤ 8 KB\) · engine-query why-lost --days 7: \d+ satır", ck.out)
    assert re.search(r"\[ölçülemedi\]\s+13 son 3 ardışık gece", ck.out), "tek (smoke) çalıştırma: 3 gece yok, geçti sayılmaz"
    assert not claim_word_violations(ck.out.splitlines())
    assert _tree_digest(sb.res) == d0
    ab = sb.run("--ab-report", script=SCRIPT)
    assert ab.returncode == 0 and "A/B geceleri" in ab.out, ab.out[-2000:]
    again = sb.run(script=SCRIPT)
    assert again.returncode == 0 and "zaten dağıtılmış" in again.out, again.out[-2000:]
    # --rollback → 79b27cf durumu (veri birimleri ve data/research yerinde); 79b27cf betiği kurulumu tanır
    k = len(sb.log())
    rb = sb.run("--rollback", script=SCRIPT)
    assert rb.returncode == 0 and "GERİ ALINDI (79b27cf)" in rb.out, rb.out[-4000:]
    assert "tb-engine-79b27cf.sh --rollback" in rb.out and "tb-engine-4962209.sh --rollback" in rb.out
    tail = sb.log()[k:]
    assert [ln for ln in tail if ln.startswith("daemon-reload")] == ["daemon-reload"]
    assert not [ln for ln in tail if ln.split()[0] in ("enable", "disable", "stop", "start")], tail
    _p79_state(sb, units0)
    assert sb.state_json()["loaded_mem"][SVC] == "536870912" and _tree_digest(sb.res) == d0
    logs = sb.base / "deploy-logs"
    assert not (logs / f"engine-{TIP[:7]}-deploy.json").exists() and list(logs.glob(f"engine-{TIP[:7]}-deploy.json.rolledback-*")), \
        "geri alınan kurulumun taban kaydı kalkar: yarım kalan yeniden dağıtım smoke'suz 'zaten dağıtılmış' demez"
    again = sb.run("--rollback", script=SCRIPT)
    assert again.returncode == 0 and "zaten 79b27cf'de" in again.out and "reload gerekmez" in again.out, again.out[-2000:]
    p79 = sb.run(script=P79_SCRIPT)
    assert p79.returncode == 0 and "zaten dağıtılmış" in p79.out, p79.out[-2000:]
    _no_forbidden(sb)


@needs_sandbox
def test_failed_night_smoke_and_an_error_after_the_pin_restore_79b27cf_and_the_p1a_night_unit(tmp_path, source, vps_template):
    sb = _sb(tmp_path, vps_template, source)
    units0, n0 = _units(sb), len(sb.log())
    cp = sb.run(script=SCRIPT, FAKE_NIGHT_FAIL="1")
    assert cp.returncode == 2 and "gece smoke'u GEÇMEDİ" in cp.out, cp.out[-4000:]
    assert "engine-app önceki sabitine döndü (79b27cf)" in cp.out and "--rollback GEREKMEZ" in cp.out, cp.out[-3000:]
    log = sb.log()[n0:]
    assert [ln for ln in log if ln.startswith("daemon-reload")] == ["daemon-reload"] * 2, "kur + geri koy, ikisi de kapıdan"
    assert any(ln.startswith(f"start {SVC}") for ln in log) and not [ln for ln in log if ln.split()[0] in ("enable", "disable")]
    _p79_state(sb, units0)
    assert sb.state_json()["loaded_mem"][SVC] == "536870912"
    assert not (sb.base / "deploy-logs" / f"engine-{TIP[:7]}-deploy.json").exists()
    # sabitlemeden sonra beklenmeyen hata (adım 8'in ilk gömülü araç çağrısı): on_err aynı geri dönüşü yapar
    flag = vps_template[1] / "fail_dlast"
    flag.write_text("1", encoding="utf-8")
    try:
        cp = sb.run(script=SCRIPT)
    finally:
        flag.unlink()
    assert cp.returncode == 1 and cp.out.count("beklenmeyen hata") == 1, cp.out[-4000:]
    assert "engine-app önceki sabitine döndü (79b27cf)" in cp.out and "--rollback GEREKMEZ" in cp.out, cp.out[-3000:]
    _p79_state(sb, units0)
    # çıktı erken kapanan boruya (`| tail` + SSH kopması gibi): betik SIGPIPE ile ölmez, kayıt dosyası sürer, geri dönüş biter
    logs0 = set((sb.base / "deploy-logs").glob(f"engine-{TIP[:7]}-deploy-*.log"))
    pp = subprocess.run([BASH, "-c", '"$1" "$0" 2>&1 | head -c 300 >/dev/null; echo "rc=${PIPESTATUS[0]}"', str(SCRIPT), BASH],
                        env={**sb.env, "FAKE_NIGHT_FAIL": "1"}, cwd=str(sb.tmp), capture_output=True, text=True, timeout=1200)
    new = sorted(set((sb.base / "deploy-logs").glob(f"engine-{TIP[:7]}-deploy-*.log")) - logs0)
    txt = new[-1].read_text(encoding="utf-8") if new else ""
    assert pp.stdout.strip().endswith("rc=2"), (pp.stdout, pp.stderr, txt[-3000:])
    assert "gece smoke'u GEÇMEDİ" in txt and "--rollback GEREKMEZ" in txt and "beklenmeyen hata" not in txt, txt[-3000:]
    _p79_state(sb, units0)
    # geri koyma reload'undan sonra worker 6G'den kayarsa: "--rollback GEREKMEZ" denmez, çıkış 3 + set-property ipucu
    cp = sb.run(script=SCRIPT, FAKE_NIGHT_FAIL="1", FAKE_DRIFT_ON_SVC_SHA=_sha(units0[SVC]))
    assert cp.returncode == 3 and "gece smoke'u GEÇMEDİ" in cp.out and "set-property" in cp.out, cp.out[-4000:]
    assert "--rollback GEREKMEZ" not in cp.out and "6G'ye döndürür" in cp.out, cp.out[-3000:]
    assert sb.eng_head() == PREV_TIP and _units(sb) == units0
    _no_forbidden(sb)


@needs_sandbox
def test_memorymax_drift_after_reload_restores_the_previous_night_unit(tmp_path, source, vps_template):
    sb = _sb(tmp_path, vps_template, source)
    units0, n0 = _units(sb), len(sb.log())
    cp = sb.run(script=SCRIPT, FAKE_RELOAD_DRIFT="1")
    assert cp.returncode == 3 and "GERİ ALINDI" in cp.out and "set-property" in cp.out, cp.out[-4000:]
    assert "daemon-reload ATLANDI" in cp.out, "kayma sonrası geri koyma reload'u kapıdan geçemez: dürüstçe söylenir"
    log = sb.log()[n0:]
    assert not [ln for ln in log if ln.split()[0] in ("start", "enable", "disable")], log
    assert sb.eng_head() == PREV_TIP and _units(sb) == units0, "reload engine-app'ten önce: kod değişmedi, baytlar geri"
    _no_forbidden(sb)
