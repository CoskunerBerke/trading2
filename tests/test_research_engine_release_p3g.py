# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P3g — kod-yalnız motor sürüm betiği `deploy/releases/tb-engine-<sha7>.sh` (S4x/S4w/S6,
MEMORY_GUARD, config dönemleri, S1d LIB_AG_V1'in gece bağlantısı; veri biriminde 794aacf ve XAUUSDT mark katmanı;
docs/ENGINE_BRAIN_V1.md §8.1 P3g, §8.4 P3g notu, §8.6).

Sahte kök, sahte `systemctl` / `journalctl`, kaynak depo ve bugünkü VPS şablonu P2 / P2.1 sürüm testlerinden
(`test_research_engine_release_p2`, `test_research_engine_release_p21`) ALINIR; burada kopyalanmaz. Tek ek şablon adımı
gerçek `tb-engine-b94bedf.sh` dağıtımıdır (VPS'in bugünkü hâli: engine-app b94bedf, P2 gece birimi 768M, veri birimi +
zamanlayıcı, drop-in'ler). O kurulumda b94bedf ağacının bağımsız koşucusu önbellekten "90/90" döner (gerçek koşusu P2.1 sürüm
testinde); hedef (P3g) ağacının koşucusu bu modülde bir kez GERÇEKTEN koşar ve `INV_TESTS` (118) ona karşı denetlenir.

Kanıtlananlar: ön koşul (birim dosyaları b94bedf'nin = 44d5353'ün baytları ve yüklü, zamanlayıcılar açık, engine-app
b94bedf ⊑) yoksa hiçbir şeye dokunmadan durur; kuru çalışma hiçbir şeyi değiştirmez; yükseltme birim dosyalarına dokunmaz,
daemon-reload / enable / disable YAPMAZ, engine-app'i hedefe sabitler ve iki smoke'la (gece smoke'u S4x/S4w/S6 + config
dönemleri + S1d mührü + AG'nin 38 deneme satırı + Kapı A ailesi 856) "DAĞITILDI N/N" der; gece smoke'u geçmezse engine-app
b94bedf'ye döner; `--check` P3g bölümünü basar; `--rollback` b94bedf durumuna döner ve b94bedf betiği kurulumu tanır.
"""
from __future__ import annotations

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
import test_research_engine_release_p2 as P2  # noqa: E402
import test_research_engine_release_p21 as P21  # noqa: E402
from test_research_engine_release import _git, _has, _tree_digest, own_tmp  # noqa: E402
from test_research_engine_release_p1b import (BASH, DSVC, DTMR, GIT, SVC, TMR, _blob, _data_fixture,  # noqa: E402
                                              _no_forbidden)
# P2 / P2.1 sürüm testlerinin fixture'ları (kaynak depo + bugünkü VPS şablonu) bu modülde AYNEN kullanılır
from test_research_engine_release_p2 import _night_fixture, _sb, _units, _wj, source, vps_template  # noqa: E402, F401
from test_research_engine_release_p21 import V3, vps44  # noqa: E402, F401

SCRIPT = ROOT / "deploy" / "releases" / "tb-engine-a56c8e2.sh"
#: Betiğin kayıtlı sha256'sı (sürüm notu ve sahibe verilen değer; betik değişirse bu da bilinçli değişir).
SCRIPT_SHA256 = "a7e405800a610f544d0ff3e4c2e6c510198ceb628f596f60dc64cac1c337599f"
TEXT = SCRIPT.read_text(encoding="utf-8")
TIP = re.search(r'^TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
PREV_TIP = re.search(r'^PREV_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
P2_TIP = re.search(r'^P2_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
P1B_TIP = re.search(r'^P1B_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
P1A_TIP = re.search(r'^P1A_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
FD_VER = re.search(r'^FD_VER="([^"]+)"', TEXT, re.M).group(1)
S1D = re.search(r'^S1D_SHA="([0-9a-f]{64})"', TEXT, re.M).group(1)
UNIT_VARS = (("SVC_SHA256", SVC), ("TMR_SHA256", TMR), ("DSVC_SHA256", DSVC), ("DTMR_SHA256", DTMR))
NO_RELOAD = ("daemon-reload", "enable", "disable")
UTC = timezone.utc
P3FULL = ["S0", "S1a", "S3", "S1b", "S2", "S4x", "S4w", "S6", "S7", "S7b"]

needs_sandbox = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and BASH and GIT and shutil.which("systemd-analyze") and shutil.which("flock")
         and shutil.which("getent") and all(_has(s) for s in (TIP, PREV_TIP, P2_TIP, P1B_TIP, P1A_TIP, P2.APP_SHA, P2.VPS_APP))),
    reason="sandbox Linux + bash + git + systemd-analyze + flock + getent ve geçmişte hedef/b94bedf/44d5353/79b27cf/P1a/app ister")


def _code() -> str:
    return "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))


# ============================================================================ statik
def test_script_is_syntax_ok_recorded_and_states_the_rules():
    lines = TEXT.splitlines()
    assert len(lines) <= 1560, "küçük, ayrılmış betik (b94bedf'ten; birim kurulumu ve reload'u yok)"
    assert subprocess.run([BASH or "bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert P2._sha(SCRIPT.read_bytes()) == SCRIPT_SHA256
    assert SCRIPT.name == f"tb-engine-{TIP[:7]}.sh"
    for flag in ("--dry-run", "--check", "--ab-report", "--rollback", "--backfill"):
        assert flag in TEXT
    assert re.search(r'^BRANCH_REF="refs/heads/claude/gifted-knuth-0ehpcs"$', TEXT, re.M), "PR dalı"
    head = "\n".join(lines[:60])
    assert "ÖN KOŞUL — P2.1 (b94bedf) KURULU" in head and "EN ERKEN DAĞITIM" in head and "BİTTİKTEN sonra" in head
    steps = [re.search(rf"#\s+{i}\) sudo bash tb-engine-{TIP[:7]}\.sh {flag}", head)
             for i, flag in enumerate(("--dry-run", " *#", "--check", "--ab-report"), 1)]
    assert all(steps) and [m.start() for m in steps] == sorted(m.start() for m in steps), \
        "sahibin adımları sırayla: dry-run → dağıt → --check → --ab-report"
    chain = [head.index(f"tb-engine-{s[:7]}.sh --rollback") for s in (PREV_TIP, P2_TIP, P1B_TIP, P1A_TIP)]
    assert chain == sorted(chain), "geri alma zinciri: b94bedf → 44d5353 → 79b27cf → 4962209"
    assert "daemon-reload YAPMAZ" in head and "enable / disable YOK" in head and "setup_vps_v3.sh ASLA" in head
    for s in ("S4x S4w S6", "MEMORY_GUARD", "LIB_AG_V1", "38 SPEC", "S1B_REBUILD", "794aacf", "XAUUSDT markpx_1h", "m = 856",
              "75 dk", "≥ 50 dk", "≥ 35 dk", "614 MiB", "538 MiB", "HESAPLANMADI"):
        assert s in head, s


@pytest.mark.skipif(not all(_has(s) for s in (TIP, PREV_TIP, P2_TIP, P1B_TIP, P1A_TIP)), reason="commit'ler geçmişte yok (sığ klon)")
def test_script_pins_match_the_code_commit():
    from tradingbot.research_engine import attribution as AT
    from tradingbot.research_engine import cfgrid as CG
    from tradingbot.research_engine import lib_ag as LA
    from tradingbot.research_engine import seal_s1d as SD
    assert _git("merge-base", "--is-ancestor", TIP, "HEAD").returncode == 0
    assert _git("merge-base", "--is-ancestor", PREV_TIP, TIP).returncode == 0, "b94bedf hedefin atası"
    assert _git("merge-base", "--is-ancestor", "794aacf", TIP).returncode == 0, "794aacf (metrics düzeltmesi) hedefte"
    assert (PREV_TIP, P2_TIP, P1B_TIP, P1A_TIP) == (P21.TIP, P21.PREV_TIP, P21.P1B_TIP, P21.P1A_TIP), "zincir = P2.1 betiğininki"
    for var, unit in UNIT_VARS:
        want = P2._sha(_blob(TIP, f"deploy/{unit}"))
        assert want == P2._sha(_blob(PREV_TIP, f"deploy/{unit}")), f"{unit}: birim dosyası b94bedf'ten beri AYNI"
        assert re.search(rf'^{var}="{want}"', TEXT, re.M) and re.search(rf'^{var}="{want}"', P21.TEXT, re.M), var
    runner = _blob(TIP, "tests/standalone/run_engine_invariants.py").decode("utf-8")
    n = len(re.findall(r'^\s+\(\d+, "test_research_engine_\w+",', runner, re.M))
    assert n == 118 and re.search(rf"^INV_TESTS={n}\b", TEXT, re.M), "sandbox kuru çalışması gerçek koşucuyla da denetler"
    acc = " ".join(re.search(r"^ACCEPTANCE = \(([^)]*)\)", runner, re.M).group(1).split())
    assert " ".join(re.search(r"^ACCEPT = \[([^\]]*)\]", TEXT, re.M).group(1).split()) == acc, "betiğin kabul listesi = koşucununki"
    ver = re.search(r'^ENGINE_VERSION = "([^"]+)"', _blob(TIP, "tradingbot/research_engine/__init__.py").decode(), re.M).group(1)
    assert ver == "research_engine_v1_p3g" and re.search(rf'^ENGINE_VER="{ver}"$', TEXT, re.M)
    fdv = re.search(r'^FIDELITY_VERSION = "([^"]+)"', _blob(TIP, "tradingbot/research_engine/fidelity.py").decode(), re.M).group(1)
    assert fdv == FD_VER == "fidelity_v3"
    assert _git("diff", "--quiet", PREV_TIP, TIP, "--", "tradingbot/research_engine/attribution.py",
                "tradingbot/research_engine/cfgrid.py", "tradingbot/research_engine/fidelity.py").returncode == 0, \
        "P2 mühürleri ve fidelity_v3 b94bedf'ten beri DEĞİŞMEDİ"
    for var, val in (("ATTR_SHA", AT.ATTRIBUTION_SHA), ("CFG_SHA", CG.CFGRID_SHA)):
        assert re.search(rf'^{var}="{val}"', TEXT, re.M) and re.search(rf'^{var}="{val}"', P21.TEXT, re.M), var
    assert S1D == SD.PINNED["S1D"] == SD.S1D_SHA and S1D in _blob(TIP, "tradingbot/research_engine/seal_s1d.py").decode()
    assert re.search(rf"^AG_SPECS={len(LA.gate_a_rows())}; GATE_M=856\b", TEXT, re.M) and len(LA.gate_a_rows()) == 38


def test_script_never_writes_units_and_never_reloads_enables_or_disables():
    code = _code()
    for m in re.finditer(r'(?<![\w-])systemctl (start|stop|restart|try-restart|reload-or-restart|reload|kill|enable|'
                         r'disable|mask|set-property|edit|revert|daemon-reexec|daemon-reload|reset-failed)\b([^\n;|&)]*)', code):
        if code[max(0, m.start() - 5):m.start()].endswith("sudo "):      # sahibe basılan talimat metni
            continue
        assert m.group(1) not in ("enable", "disable", "set-property", "edit", "revert", "daemon-reload", "daemon-reexec"), m.group(0)
        assert re.match(r'\s*(--\S+\s+)*"?\$(SVC|DSVC|BF)\b', m.group(2)), f"motor birimi dışında: {m.group(0)}"
    assert not re.search(r'(?<![\w-])(rm|install|cp|mv|tee|sed -i)\b[^\n]*"\$(SD|NDROPIN|DDROPIN)\b', code), "birim/drop-in yazılmaz"
    assert not re.search(r'>\s*"\$(SD|NDROPIN|DDROPIN)', code)
    for gone in ("nu_put", "reload_units", "undo_units", "reload_gate"):
        assert gone not in code, gone
    assert not re.search(r"^\s*(bash|sh)\s+\S*setup_vps", code, re.M)
    assert len(re.findall(r"^\s*systemd-run --unit=\"\$BF\"", code, re.M)) == 1, "yalnız --backfill (reload yapmaz)"
    assert "trap '' PIPE" in code and "exec tee -p -a \"$LOG\"" in code, "kopmuş boru/bağlantı geri dönüşü öldürmez"
    assert re.search(r'mv -- "\$BASELINE" "\$BASELINE\.rolledback-', code), "--rollback taban kaydını kaldırır"
    # saat: başta ≥ 75 dk, engine-app'e dokunmadan önce ≥ 50 dk, gece smoke'undan önce ≥ 35 dk (P3g: S4x/S4w/S6 ile)
    assert "if time_ok 75; then ok" in code and "SMOKE_TIMEOUT=2100" in code
    i50, i_fetch, i_pin = code.index("\ntime_ok 50 || die "), code.index('\nfetch_tip "$ENG" "$TIP" "$W"'), code.index('pin_eng "$TIP"')
    assert i50 < i_fetch < i_pin and "HİÇBİR ŞEYE DOKUNULMADI" in code[i50:code.index("\n", i50 + 1)]
    i35 = code.index("\ntime_ok 35 || { back_out; die ")
    assert code.index('timeout "$DSMOKE_TIMEOUT" systemctl start "$DSVC"') < i35 < code.index('timeout "$SMOKE_TIMEOUT" systemctl start "$SVC"')
    assert re.search(r'CHANGED\+=\("engine-app=\$T7"\); pin_eng "\$TIP"', code), "yarım sabitleme de geri alınır"
    assert re.search(r'CHANGED\+=\("engine-app=\$\{PREV_TIP:0:7\}"\); pin_eng "\$PREV_TIP"', code), "--rollback"
    for step in ('-s -m compileall -q tradingbot scripts', '-s tests/standalone/run_engine_invariants.py',
                 "-s -c 'import resource as r, sys, tradingbot.cli", '-s scripts/bot_scorecard.py'):
        lines = [ln for ln in TEXT.splitlines() if step in ln and "$VENV/bin/python" in ln]
        assert lines and all('"${LOW[@]}" "$VENV/bin/python"' in ln for ln in lines), f"en düşük öncelik: {step}"


def test_shellcheck_clean():
    exe = shutil.which("shellcheck") or str(Path(sys.executable).with_name("shellcheck"))
    if not os.access(exe, os.X_OK):
        pytest.skip("shellcheck yok (betiğin --dry-run'ı VPS'te varsa koşar)")
    cp = subprocess.run([exe, str(SCRIPT)], capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stdout + cp.stderr


def test_isolation_check_is_unchanged_and_passes_on_the_target_tree(tmp_path, tool):
    """Yalıtım değişmezi gevşetilmez: gömülü `c_ast` ve onu çağıran kabuk satırları P2.1 (= 44d5353) betiğininkiyle aynı;
    hedef ağaçta (S1d `lib_ag`/`seal_s1d`, P3g `agnight`/`nightp3`/`wfqueue` dahil) yasak import yoktur."""
    def block(text: str) -> str:
        return text[text.index("def c_ast(tree):"):text.index("def c_lastrun(res):")]

    def shell(text: str) -> list[str]:
        return [ln for ln in text.splitlines() if 'inv "yalıtım-AST"' in ln or 'rpy ast "$W"' in ln]
    assert block(TEXT) == block(P21.TEXT) and shell(TEXT) == shell(P21.TEXT) and len(shell(TEXT)) == 2
    cp = tool(tmp_path, "ast", str(ROOT))
    assert cp.returncode == 0 and cp.stdout.strip() == "yasak import yok", cp.stdout + cp.stderr


# ============================================================================ gömülü araçlar (deterministik, dağıtımsız)
@pytest.fixture
def tool(monkeypatch):
    """P2 testinin gömülü araç koşucusu (`P2._tool`), bu betiğin PYTOOL'uyla."""
    monkeypatch.setattr(P2, "TEXT", TEXT)
    return P2._tool


def _p3_fixture(res: Path, *, s4w_skip=None, ag_run=True, ag_seal="OK", family_m=856, n_specs=38, epochs="OK", trips=(),
                s4x_skip=None, plan=P3FULL, **kw) -> dict:
    """P3g gecesi: P2 gecesinin üstüne S4x/S4w/S6 sonuçları, `p3` (AG, koruma), config dönemleri ve dosyalar."""
    kw.setdefault("fidelity", V3)
    st = _night_fixture(res, plan=plan, engine="research_engine_v1_p3g", **kw)
    stg = st["stages"]
    if "S4x" in plan:
        stg["S4x"] = ({"status": "SKIPPED", "reason": s4x_skip} if s4x_skip else
                      {"status": "OK", "duration_s": 110.0, "result": {"status": {"OK": 3720, "UNSPECIFIED_PARAM": 25},
                                                                       "ag": {"status": {"OK": 1558}, "plan_total": 1558}
                                                                       if ag_run else
                                                                       {"skipped": "AG kapalı"}}})
    if "S4w" in plan:
        stg["S4w"] = ({"status": "SKIPPED", "reason": s4w_skip} if s4w_skip else
                      {"status": "OK", "duration_s": 120.0, "result": {
                          "stop": "DEADLINE", "units_done": 40, "backlog": {"done": 40, "total": 1173,
                                                                            "building_month": "2026-09"},
                          "ag": {"units_planned": 493, "units_built": 12, "run": ag_run}}})
    if "S6" in plan:
        stg["S6"] = {"status": "OK", "duration_s": 3.0, "result": {
            "trials": {"ok": True, "code": "OK", "rows": n_specs}, "gate_a": {"status": "NOT_LOOK_NIGHT",
                                                                           "family": {"lib": 818, "ag": 38, "m": family_m}}}}
    st["p3"] = {"ag": {"ok": ag_seal == "OK", "run": ag_run, "seal": ag_seal, "s1d_sha": S1D, "symbols": 41,
                       "trials": {"ok": True, "status": "APPENDED", "appended": 38} if ag_run else None,
                       "reason": None if ag_run else "AG_SKIPPED: test"},
                "guard": {"limit_bytes": 805306368, "fraction": 0.7, "max_rss_bytes": 280 << 20, "vm_hwm_bytes": 300 << 20,
                          "trips": list(trips)}}
    st["config_epochs"] = {"status": epochs, "night_epochs": 6}
    for p in (res / "runs" / st["run_id"] / "run_status.json", res / "summary" / "run_status.json"):
        if p.exists():
            _wj(p, st)
    for n in ("explore/LIB_v1/_run.json", "explore/LIB_AG_v1/_run.json", "backtests/queue.json"):
        _wj(res / n, {"x": 1})
    rows = [json.dumps({"kind": "SPEC", "lib_version": "LIB_AG_v1", "seq": i + 1}) for i in range(n_specs)]
    (res / "trials").mkdir(parents=True, exist_ok=True)
    (res / "trials" / "trials.jsonl").write_text("".join(r + "\n" for r in rows), encoding="utf-8")
    return st


def test_night_smoke_requires_the_p3_stages_epochs_s1d_and_the_ag_trials(tmp_path, tool):
    """Araç SHA'dan bağımsızdır: P2 fikstürü 44d5353'ün SHA'sını yazar, `tip` olarak o verilir."""
    user = pwd.getpwuid(os.getuid()).pw_name
    t0 = str(time.time() - 5)

    def case(name, **kw):
        res = tmp_path / name / "research"
        _p3_fixture(res, **kw)
        return tool(tmp_path, "smoke", str(res), P2.TIP, "", t0, user, "research_engine_v1_p3g", P2.ATTR, P2.CFG, "805306368",
                    FD_VER, S1D, "38", "856")
    ok = case("ok", mem={"memory_max_bytes": 805306368, "memory_peak_ratio": 0.41, "memory_peak_bytes": 330 << 20})
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "deneme defteri: S1d AG SPEC satırı 38 (beklenen 38; sonuçlardan ÖNCE, bir kez)" in ok.stdout, ok.stdout
    assert re.search(r"S4w OK · 120 sn · WF_BACKLOG 40/1173", ok.stdout) and "aile m = 856" in ok.stdout, ok.stdout
    rebuild = case("rebuild", s4w_skip="S1B_REBUILD: bu gece 600/600 günlük satırı yeniden kuruldu")
    assert rebuild.returncode == 0 and "S4w SKIPPED" in rebuild.stdout, rebuild.stdout + rebuild.stderr
    diff = case("universe_diff", s4x_skip="ENTRY_UNIVERSE_DIFF: eksik 1", s4w_skip="ENTRY_UNIVERSE_DIFF: eksik 1", ag_run=False)
    assert diff.returncode == 0, "evren farkı raporlanır (S4x/S4w atlanır), dağıtım hatası değil: " + diff.stdout
    for name, kw, why in (("odd_skip", {"s4w_skip": "PREFLIGHT_FAILED: x"}, "aşama S4w: SKIPPED"),
                          ("family", {"family_m": 818}, "Kapı A ailesi m = 818 ≠ 856"),
                          ("seal", {"ag_seal": "S1D_SHA_MISMATCH: LIB_AG_V1", "ag_run": False}, "S1d mührü S1D_SHA_MISMATCH"),
                          ("ag_off", {"ag_run": False}, "AG bu gece koşmadı"),
                          ("specs", {"n_specs": 37}, "deneme defterinde AG SPEC 37 ≠ 38"),
                          ("epochs", {"epochs": "FAILED"}, "config dönemleri FAILED"),
                          ("guard", {"trips": [{"stage": "S4w", "rss_bytes": 600 << 20}]}, "MEMORY_GUARD tetiklendi (1)"),
                          ("old_plan", {"plan": P2.FULL}, "planda S4x yok (P3g aşaması çalışmadı)"),
                          ("fidelity", {"fidelity": {**V3, "version": "fidelity_v2"}}, "fidelity sürümü fidelity_v2")):
        cp = case(name, **kw)
        assert cp.returncode == 1 and "KALDI:" in cp.stdout and why in cp.stdout, (name, cp.stdout + cp.stderr)


def test_data_smoke_requires_the_xauusdt_mark_tier_in_the_plan(tmp_path, tool):
    user = pwd.getpwuid(os.getuid()).pw_name
    t0 = str(time.time() - 1)

    def case(name, doc):
        res = tmp_path / name / "research"
        _data_fixture(res, engine="research_engine_v1_p3g")
        day = json.loads((res / "summary" / "data_status.json").read_text(encoding="utf-8"))["last_run"]["started_at"][:10]
        _wj(res / "universe" / f"{day}.json", doc)
        return tool(tmp_path, "dsmoke", str(res), "", t0, user, "research_engine_v1_p3g")
    ag = {"XAUUSDT": ["ag_evreni", "altin"], "PAXGUSDT": ["altin"]}
    ok = case("ok", {"gold_futures": ag, "series": ["futures/BTCUSDT/5m", "futures/XAUUSDT/1h", "futures/XAUUSDT/markpx_1h"]})
    assert ok.returncode == 0 and "XAUUSDT ag_evreni rolüyle planda" in ok.stdout, ok.stdout + ok.stderr
    short = case("short_list", {"gold_futures": ag, "series": ["futures/BTCUSDT/1d"]})
    assert short.returncode == 0, "kısaltılmış seri listesi (sandbox): yalnız rol denetlenir — " + short.stdout
    no_role = case("no_role", {"gold_futures": {"XAUUSDT": ["altin"]}, "series": ["futures/XAUUSDT/1h"]})
    assert no_role.returncode == 1 and "XAUUSDT'nin S1d rolü (ag_evreni) yok" in no_role.stdout, no_role.stdout
    no_tier = case("no_tier", {"gold_futures": ag, "series": ["futures/BTCUSDT/5m", "futures/XAUUSDT/1h"]})
    assert no_tier.returncode == 1 and "futures/XAUUSDT/markpx_1h planda yok (S1d AG katmanı)" in no_tier.stdout, no_tier.stdout


def test_p3_check_rows_report_what_the_engine_wrote_and_never_pass_the_unmeasured(tmp_path, tool):
    from tradingbot.research_engine.summary import claim_word_violations
    empty = tool(tmp_path, "p3", str(tmp_path / "yok"), P2.TIP, S1D, "38", "856", "805306368")
    rows = [ln for ln in empty.stdout.splitlines() if ln.startswith("   [")]
    assert empty.returncode == 0 and len(rows) == 6 and all("[ölçülemedi]" in ln for ln in rows), empty.stdout + empty.stderr
    res = tmp_path / "ok" / "research"
    _p3_fixture(res)
    out = tool(tmp_path, "p3", str(res), P2.TIP, S1D, "38", "856", "805306368")
    assert out.returncode == 0 and not claim_word_violations(out.stdout.splitlines()), out.stdout + out.stderr
    for pat in (r"\[tamam\]\s+P1 son tam gece", r"\[bilgi\]\s+P2 WF_BACKLOG 40/1173 · kurulan ay 2026-09",
                r"\[tamam\]\s+P3 MEMORY_GUARD ihlali 0 · VmHWM en çok 300 MB \(eşik 0,7 × 768 MB = 538 MB",
                r"\[tamam\]\s+P4 S1d AG: mühür OK · koşmayan gece 0/1 · deneme defteri AG SPEC 38 \(beklenen 38\)",
                r"\[tamam\]\s+P5 Kapı A BH ailesi m = 856", r"\[tamam\]\s+P6 P3 bayrakları"):
        assert re.search(pat, out.stdout), (pat, out.stdout)
    bad = tmp_path / "bad" / "research"
    _p3_fixture(bad, trips=[{"stage": "S4w"}], n_specs=37, family_m=818, flags=("WF_UNIT_ERRORS",))
    out = tool(tmp_path, "p3", str(bad), P2.TIP, S1D, "38", "856", "805306368").stdout
    for pat in (r"\[DİKKAT\]\s+P3 MEMORY_GUARD ihlali 1", r"\[DİKKAT\]\s+P4 S1d AG", r"\[DİKKAT\]\s+P5 Kapı A BH ailesi m = 818",
                r"\[DİKKAT\]\s+P6 P3 bayrakları .*WF_UNIT_ERRORS 1"):
        assert re.search(pat, out), (pat, out)


# ============================================================================ sandbox
@pytest.fixture(scope="module")
def vpsb94(tmp_path_factory, source, vps44):
    """VPS'in bugünkü hâli: P2.1 şablonunun (P1a → 57cfef1 → 79b27cf → 44d5353) üstüne gerçek `tb-engine-b94bedf.sh`
    dağıtımı. PR dalının ucu P3g hedefidir (betiklerin yedek getirme yolu). b94bedf ağacının koşucu sonucu önbellekten."""
    assert _git("update-ref", "refs/heads/claude/gifted-knuth-0ehpcs", TIP, cwd=source).returncode == 0
    aux = vps44[1]
    acc = [int(x) for x in re.search(r"^ACCEPT = \[([^\]]*)\]", P21.TEXT, re.M).group(1).replace(",", " ").split()]
    (aux / f"inv.{PREV_TIP}.json").write_text(json.dumps({"passed": 90, "failed": 0, "skipped": 0, "acceptance_passed": acc}),
                                              encoding="utf-8")
    root = tmp_path_factory.mktemp("vpsb94")
    sb = _sb(root, vps44, source)
    cp = sb.run(script=P21.SCRIPT)
    assert cp.returncode == 0 and "DAĞITILDI: 28/28" in cp.out, cp.out[-4000:]
    assert sb.eng_head() == PREV_TIP and (sb.sd / SVC).read_bytes() == _blob(PREV_TIP, f"deploy/{SVC}")
    assert (sb.base / "deploy-logs" / f"engine-{PREV_TIP[:7]}-deploy.json").is_file()
    return root, aux


def _sbb(tmp_path: Path, vpsb94, source, **env):
    return _sb(tmp_path, vpsb94, source, **env)


def _p21_state(sb, units0: dict) -> None:
    """b94bedf durumu: engine-app b94bedf, birim dosyaları (gece + veri + drop-in'ler) şablondakiyle bayt bayt aynı."""
    assert sb.eng_head() == PREV_TIP and _units(sb) == units0
    st = sb.state_json()
    assert st["enabled"] == {TMR: True, DTMR: True} and st["active"] == {TMR: True, DTMR: True}
    assert st["loaded_mem"][SVC] == "805306368"


def _no_reload(log: list[str]) -> None:
    assert not [ln for ln in log if ln.split()[0] in NO_RELOAD], log


@needs_sandbox
def test_dry_run_on_the_p21_vps_runs_the_real_runner_and_changes_nothing(tmp_path, source, vpsb94):
    sb = _sbb(tmp_path, vpsb94, source)
    snap, made0 = sb.snap(), len(own_tmp(sb.fake))
    cp = sb.run("--dry-run", script=SCRIPT)
    assert cp.returncode == 0, cp.out[-6000:]
    m = re.search(r"KURU ÇALIŞMA: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "20", cp.out[-3000:]
    assert "118 geçti · 0 kaldı · 0 atlandı" in cp.out, cp.out[-6000:]
    for name in ("P2.1-kurulu", "yedek-doğrulandı", "1g-pencere-dışı", "kaynak-TIP", "app-SHA-ata", "b94bedf-ata", "birim-sha256",
                 "birim-sözleşmesi", "systemd-analyze-verify", "compileall", "bağımsız-koşucu", "yalıtım-AST", "status-kuru",
                 "worker-MemoryMax=6G", "motor-boşta"):
        assert re.search(rf"\[tamam\] #\d+ {re.escape(name)}", cp.out), name
    assert re.search(r"UYARI\s+b94bedf A/B penceresi \(sürüm günü \d{4}-\d\d-\d\d\): 0/14 gece geçti — P3g motor kodunu", cp.out)
    sb.untouched_since(snap)
    _no_reload(sb.log()[snap["log"]:])
    _no_forbidden(sb)
    made = own_tmp(sb.fake)[made0:]
    assert made and not [p for p in made if p.exists()], "geçici klon silinmeli"


@needs_sandbox
@pytest.mark.parametrize("case", ["engine_app_44d5353", "night_unit_not_loaded"])
def test_missing_p21_refuses_before_any_change(tmp_path, source, vpsb94, case):
    sb = _sbb(tmp_path, vpsb94, source)
    st = sb.state_json()
    if case == "engine_app_44d5353":
        assert _git("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", P2_TIP, cwd=sb.base / "engine-app").returncode == 0
    if case == "night_unit_not_loaded":
        st["loaded"][SVC] = "0" * 64
    (sb.fake / "systemd.json").write_text(json.dumps(st), encoding="utf-8")
    snap = sb.snap()
    cp = sb.run(script=SCRIPT)
    want = {"engine_app_44d5353": "engine-app 44d5353, b94bedf'yi (b94bedf) içermiyor",
            "night_unit_not_loaded": f"{SVC} yüklü değil ya da NeedDaemonReload=yes (bu sürüm daemon-reload YAPMAZ"}[case]
    assert cp.returncode == 1 and "DUR: P2.1-kurulu" in cp.out and want in cp.out, cp.out[-3000:]
    assert "(önce: tb-engine-b94bedf.sh)" in cp.out and "HİÇBİR ŞEYE DOKUNULMADI" in cp.out
    sb.untouched_since(snap)
    _no_forbidden(sb)


@needs_sandbox
def test_upgrade_pins_engine_app_runs_the_p3_stages_check_rerun_and_rollback(tmp_path, source, vpsb94):
    from tradingbot.research_engine.summary import claim_word_violations
    sb = _sbb(tmp_path, vpsb94, source)
    units0, n0 = _units(sb), len(sb.log())
    cp = sb.run(script=SCRIPT)
    assert cp.returncode == 0, cp.out[-8000:]
    m = re.search(r"DAĞITILDI: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "28", cp.out[-3000:]
    log = sb.log()[n0:]
    _no_reload(log)
    assert [ln.split()[:2] for ln in log if ln.split()[0] in ("start", "stop")] == [["start", DSVC], ["start", SVC]], log
    assert _units(sb) == units0, "birim dosyaları ve drop-in'ler bayt bayt aynı"
    assert sb.eng_head() == TIP
    for name in ("birimler-aynı", "engine-app-sabit", "veri-smoke", "gece-smoke", "birimler-zamanlayıcılar", "worker-dokunulmadı"):
        assert re.search(rf"\[tamam\] #\d+ {re.escape(name)}", cp.out), name
    assert "XAUUSDT ag_evreni rolüyle planda" in cp.out, cp.out[-3000:]
    rs = json.loads((sb.res / "summary" / "run_status.json").read_text(encoding="utf-8"))
    assert rs["result"] == "SUCCESS" and rs["shas"]["engine"] == TIP and rs["engine"] == "research_engine_v1_p3g"
    assert {"S1b", "S2", "S4x", "S4w", "S6"} <= set(rs["plan"]) and rs["stages"]["S6"]["status"] == "OK"
    assert rs["config_epochs"]["status"] == "OK" and (sb.res / "library" / "config_epochs.json").is_file()
    assert rs["stages"]["S6"]["result"]["gate_a"]["family"]["m"] == 856
    ag = rs["p3"]["ag"]
    assert ag.get("seal") in (None, "OK"), ag
    if ag.get("run"):
        n_ag = sum(1 for ln in (sb.res / "trials" / "trials.jsonl").read_text(encoding="utf-8").splitlines()
                   if '"LIB_AG_v1"' in ln and json.loads(ln)["kind"] == "SPEC")
        assert n_ag == 38 and "S1d AG SPEC satırı 38" in cp.out
    # --check: araştırma ağacı değişmez; P1a, P1b, P2 ve P3g bölümleri; iddia kelimesi yok
    d0 = _tree_digest(sb.res)
    ck = sb.run("--check", script=SCRIPT)
    assert ck.returncode == 0, ck.out[-4000:]
    for s in (f"engine-app {TIP[:7]}", "kabul ölçütleri — P2", "kabul ölçütleri — P3g", "P1 son tam gece", "P2 WF_BACKLOG",
              "P3 MEMORY_GUARD ihlali", "P4 S1d AG", "P5 Kapı A BH ailesi m = 856", "geri alma (b94bedf durumu)"):
        assert s in ck.out, s
    assert not claim_word_violations(ck.out.splitlines()) and _tree_digest(sb.res) == d0
    k = len(sb.log())
    again = sb.run(script=SCRIPT)
    assert again.returncode == 0 and f"zaten dağıtılmış ({TIP[:7]}" in again.out, again.out[-2000:]
    assert not [ln for ln in sb.log()[k:] if ln.split()[0] in NO_RELOAD + ("start", "stop")]
    # --rollback → b94bedf durumu (birimler aynı, reload YOK); b94bedf betiği kurulumu tanır
    k = len(sb.log())
    rb = sb.run("--rollback", script=SCRIPT)
    assert rb.returncode == 0 and "GERİ ALINDI (b94bedf)" in rb.out, rb.out[-4000:]
    chain = [rb.out.index(f"tb-engine-{s[:7]}.sh --rollback") for s in (PREV_TIP, P2_TIP, P1B_TIP, P1A_TIP)]
    assert chain == sorted(chain), "zincir sırayla"
    assert not [ln for ln in sb.log()[k:] if ln.split()[0] in NO_RELOAD + ("start", "stop")], sb.log()[k:]
    _p21_state(sb, units0)
    assert _tree_digest(sb.res) == d0, "geri alma data/research'e dokunmaz (P3g klasörleri kalır)"
    logs = sb.base / "deploy-logs"
    assert not (logs / f"engine-{TIP[:7]}-deploy.json").exists() and list(logs.glob(f"engine-{TIP[:7]}-deploy.json.rolledback-*"))
    p21 = sb.run(script=P21.SCRIPT)
    assert p21.returncode == 0 and "zaten dağıtılmış (b94bedf" in p21.out, p21.out[-2000:]
    _no_reload(sb.log()[n0:])
    _no_forbidden(sb)


@needs_sandbox
def test_failed_night_smoke_returns_engine_app_to_b94bedf(tmp_path, source, vpsb94):
    sb = _sbb(tmp_path, vpsb94, source)
    units0, n0 = _units(sb), len(sb.log())
    cp = sb.run(script=SCRIPT, FAKE_NIGHT_FAIL="1")
    assert cp.returncode == 2 and "gece smoke'u GEÇMEDİ" in cp.out, cp.out[-4000:]
    assert "engine-app önceki sabitine döndü (b94bedf)" in cp.out and "--rollback GEREKMEZ" in cp.out, cp.out[-3000:]
    assert "birim dosyalarına ve zamanlayıcılara dokunulmadı" in cp.out
    assert any(ln.startswith(f"start {SVC}") for ln in sb.log()[n0:])
    _p21_state(sb, units0)
    assert not (sb.base / "deploy-logs" / f"engine-{TIP[:7]}-deploy.json").exists()
    _no_reload(sb.log()[n0:])
    _no_forbidden(sb)
