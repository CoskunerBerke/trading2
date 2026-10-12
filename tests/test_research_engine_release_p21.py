# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2.1 — kod-yalnız motor sürüm betiği `deploy/releases/tb-engine-<sha7>.sh` (fidelity_v3,
strategy_paper ≥ 1h gözlem dolumu, ABSENT_UPSTREAM; docs/SYSTEM_LEARNING_ENGINE_V1.md P2 notları 40–44).

Sahte kök, sahte `systemctl` / `journalctl` (P2 ekleriyle: yüklü MemoryMax birim dosyasından, `FAKE_NIGHT_FAIL`), kaynak
depo, bugünkü VPS şablonu (gerçek P1a → 57cfef1 → 79b27cf betikleri) ve gömülü araç yardımcıları P2 sürüm testinden
(`test_research_engine_release_p2`) ALINIR; burada kopyalanmaz. Tek ek şablon adımı gerçek `tb-engine-44d5353.sh`
dağıtımıdır (VPS'in bugünkü hâli: engine-app 44d5353, P2 gece birimi 768M, veri birimi + zamanlayıcı, drop-in'ler). O
kurulum adımında 44d5353 ağacının bağımsız koşucusu önbellekten "90/90" döner (o ağacın GERÇEK koşusu P2 sürüm testinde
yapılır; burada süre için tekrarlanmaz); hedef (P2.1) ağacının koşucusu bu modülde bir kez GERÇEKTEN koşar ve
`INV_TESTS` ona karşı denetlenir.

Kanıtlananlar: ön koşul (birim dosyaları 44d5353'ün baytları ve yüklü, zamanlayıcılar açık, engine-app 44d5353 ⊑) yoksa
hiçbir şeye dokunmadan durur; kuru çalışma hiçbir şeyi değiştirmez; yükseltme birim dosyalarına (drop-in'ler dahil)
dokunmaz, daemon-reload / enable / disable YAPMAZ, engine-app'i hedefe sabitler ve iki smoke'la (gece smoke'u
`_build.json`'da fidelity_v3 + değişmeyen P2 mühürleri) "DAĞITILDI N/N" der; günlük satırları bir kez yeniden kurulur;
gece smoke'u geçmezse ya da sabitlemeden sonra hata olursa engine-app 44d5353'e döner; başarıdan sonra yeniden çalıştırma
"zaten dağıtılmış" der; `--check` bütün satırları basar (P2 fidelity satırı fidelity_v3'e göre, V2 tazelik
ABSENT_UPSTREAM serilerini bayat saymadan ayrı listeler); `--rollback` 44d5353 durumuna döner ve 44d5353 betiği kurulumu
tanır.
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
from test_research_engine_release import _git, _has, _tree_digest, own_tmp  # noqa: E402
from test_research_engine_release_p1b import (BASH, DSVC, DTMR, GIT, SVC, TMR, _blob, _data_fixture,  # noqa: E402
                                              _no_forbidden)
# P2 sürüm testinin fixture'ları (kaynak depo + bugünkü VPS şablonu) bu modülde AYNEN kullanılır (pytest ad ile bağlar)
from test_research_engine_release_p2 import _night_fixture, _sb, _units, source, vps_template  # noqa: E402, F401

SCRIPT = ROOT / "deploy" / "releases" / "tb-engine-b94bedf.sh"
#: Betiğin kayıtlı sha256'sı (sürüm notu ve sahibe verilen değer; betik değişirse bu da bilinçli değişir).
SCRIPT_SHA256 = "746970b0211bccf95a0a03e031fc9b9aca9edb3598fef6889f8927d1c674af48"
TEXT = SCRIPT.read_text(encoding="utf-8")
TIP = re.search(r'^TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
PREV_TIP = re.search(r'^PREV_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
P1B_TIP = re.search(r'^P1B_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
P1A_TIP = re.search(r'^P1A_TIP="([0-9a-f]{40})"', TEXT, re.M).group(1)
FD_VER = re.search(r'^FD_VER="([^"]+)"', TEXT, re.M).group(1)
UNIT_VARS = (("SVC_SHA256", SVC), ("TMR_SHA256", TMR), ("DSVC_SHA256", DSVC), ("DTMR_SHA256", DTMR))
NO_RELOAD = ("daemon-reload", "enable", "disable")
UTC = timezone.utc

needs_sandbox = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and BASH and GIT and shutil.which("systemd-analyze") and shutil.which("flock")
         and shutil.which("getent") and all(_has(s) for s in (TIP, PREV_TIP, P1B_TIP, P1A_TIP, P2.APP_SHA, P2.VPS_APP))),
    reason="sandbox Linux + bash + git + systemd-analyze + flock + getent ve geçmişte hedef/44d5353/79b27cf/P1a/app commit'leri ister")


def _code() -> str:
    return "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))


# ============================================================================ statik
def test_script_is_syntax_ok_recorded_and_states_the_rules():
    lines = TEXT.splitlines()
    assert len(lines) <= 1390, "küçük, ayrılmış betik (44d5353'ten; birim kurulumu ve reload'u çıkarıldı)"
    assert subprocess.run([BASH or "bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert P2._sha(SCRIPT.read_bytes()) == SCRIPT_SHA256
    assert SCRIPT.name == f"tb-engine-{TIP[:7]}.sh"
    for flag in ("--dry-run", "--check", "--ab-report", "--rollback", "--backfill"):
        assert flag in TEXT
    assert re.search(r'^BRANCH_REF="refs/heads/claude/gifted-knuth-0ehpcs"$', TEXT, re.M), "PR dalı"
    head = "\n".join(lines[:50])
    assert "ÖN KOŞUL — P2 (44d5353) KURULU" in head and "EN ERKEN DAĞITIM" in head and "BİTTİKTEN sonra" in head
    steps = [re.search(rf"#\s+{i}\) sudo bash tb-engine-{TIP[:7]}\.sh {flag}", head)
             for i, flag in enumerate(("--dry-run", " *#", "--check", "--ab-report"), 1)]
    assert all(steps) and [m.start() for m in steps] == sorted(m.start() for m in steps), \
        "sahibin adımları sırayla: dry-run → dağıt → --check → --ab-report"
    chain = [head.index(f"tb-engine-{s[:7]}.sh --rollback") for s in (PREV_TIP, P1B_TIP, P1A_TIP)]
    assert chain == sorted(chain), "geri alma zinciri: 44d5353 → 79b27cf → 4962209"
    assert "daemon-reload YAPMAZ" in head and "enable / disable YOK" in head and "setup_vps_v3.sh ASLA" in head
    assert "fidelity_v3" in head and "ABSENT_UPSTREAM" in head and "%93–95" in head and "BİR KEZ yeniden kurulur" in head
    assert "75 dk" in head and "≥ 50 dk" in head and "≥ 30 dk" in head and "614 MiB" in head


@pytest.mark.skipif(not all(_has(s) for s in (TIP, PREV_TIP, P1B_TIP, P1A_TIP)), reason="commit'ler geçmişte yok (sığ klon)")
def test_script_pins_match_the_code_commit():
    from tradingbot.research_engine import attribution as AT
    from tradingbot.research_engine import cfgrid as CG
    assert _git("merge-base", "--is-ancestor", TIP, "HEAD").returncode == 0
    assert _git("merge-base", "--is-ancestor", PREV_TIP, TIP).returncode == 0, "44d5353 hedefin atası"
    assert (PREV_TIP, P1B_TIP, P1A_TIP) == (P2.TIP, P2.PREV_TIP, P2.P1A_TIP), "zincir = P2 betiğinin hedefi ve tabanları"
    for var, unit in UNIT_VARS:
        want = P2._sha(_blob(TIP, f"deploy/{unit}"))
        assert want == P2._sha(_blob(PREV_TIP, f"deploy/{unit}")), f"{unit}: birim dosyası 44d5353'ten beri AYNI"
        assert re.search(rf'^{var}="{want}"', TEXT, re.M) and re.search(rf'^{var}="{want}"', P2.TEXT, re.M), var
    runner = _blob(TIP, "tests/standalone/run_engine_invariants.py").decode("utf-8")
    n = len(re.findall(r'^\s+\(\d+, "test_research_engine_\w+",', runner, re.M))
    assert n == 90 and re.search(rf"^INV_TESTS={n}\b", TEXT, re.M), "sandbox kuru çalışması gerçek koşucuyla da denetler"
    acc = " ".join(re.search(r"^ACCEPTANCE = \(([^)]*)\)", runner, re.M).group(1).split())
    assert " ".join(re.search(r"^ACCEPT = \[([^\]]*)\]", TEXT, re.M).group(1).split()) == acc, "betiğin kabul listesi = koşucununki"
    ver = re.search(r'^ENGINE_VERSION = "([^"]+)"', _blob(TIP, "tradingbot/research_engine/__init__.py").decode(), re.M).group(1)
    assert ver == "research_engine_v1_p2" and re.search(rf'^ENGINE_VER="{ver}"$', TEXT, re.M)
    fdv = re.search(r'^FIDELITY_VERSION = "([^"]+)"', _blob(TIP, "tradingbot/research_engine/fidelity.py").decode(), re.M).group(1)
    assert fdv == FD_VER == "fidelity_v3"
    # P2 mühürleri DEĞİŞMEDİ: kaynakları 44d5353'le bayt bayt aynı, sabitler P2 betiğininkiyle ve koddakiyle aynı
    assert _git("diff", "--quiet", PREV_TIP, TIP, "--", "tradingbot/research_engine/attribution.py",
                "tradingbot/research_engine/cfgrid.py").returncode == 0
    assert _git("diff", "--quiet", TIP, "HEAD", "--", "tradingbot/research_engine/attribution.py",
                "tradingbot/research_engine/cfgrid.py").returncode == 0
    for var, val in (("ATTR_SHA", AT.ATTRIBUTION_SHA), ("CFG_SHA", CG.CFGRID_SHA)):
        assert re.search(rf'^{var}="{val}"', TEXT, re.M) and re.search(rf'^{var}="{val}"', P2.TEXT, re.M), var
    assert AT.ATTRIBUTION_SHA.startswith("914875a1") and CG.CFGRID_SHA.startswith("7c299ae0")


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
    # saat: başta ≥ 75 dk, engine-app'e dokunmadan önce ≥ 50 dk, gece smoke'undan önce ≥ 30 dk
    assert "if time_ok 75; then ok" in code
    i50, i_fetch, i_pin = code.index("\ntime_ok 50 || die "), code.index('\nfetch_tip "$ENG" "$TIP" "$W"'), code.index('pin_eng "$TIP"')
    assert i50 < i_fetch < i_pin and "HİÇBİR ŞEYE DOKUNULMADI" in code[i50:code.index("\n", i50 + 1)]
    i30 = code.index("\ntime_ok 30 || { back_out; die ")
    assert code.index('timeout "$DSMOKE_TIMEOUT" systemctl start "$DSVC"') < i30 < code.index('timeout "$SMOKE_TIMEOUT" systemctl start "$SVC"')
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


# ============================================================================ gömülü araçlar (deterministik, dağıtımsız)
@pytest.fixture
def tool(monkeypatch):
    """P2 testinin gömülü araç koşucusu (`P2._tool`), bu betiğin PYTOOL'uyla."""
    monkeypatch.setattr(P2, "TEXT", TEXT)
    return P2._tool


V3 = {"version": "fidelity_v3", "eligible": 20, "ok": 19, "rate": 0.95, "gate_pass": True,
      "failures_by_reason": {"EXIT_REASON_DIFFERS": 1}, "not_eligible": {"SPOT": 4, "NO_PRE_EXIT_BAR": 1}}


def _set_pending(res: Path, rid: str, pending: int) -> None:
    for p in (res / "runs" / rid / "run_status.json", res / "summary" / "run_status.json"):
        if p.exists():
            st = json.loads(p.read_text(encoding="utf-8"))
            if st.get("run_id") == rid:
                st["stages"]["S1b"]["result"]["journal"]["pending"] = pending
                p.write_text(json.dumps(st), encoding="utf-8")


def test_night_smoke_requires_fidelity_v3_when_s1b_ran_and_keeps_the_p2_rules(tmp_path, tool):
    """Araç SHA'dan bağımsızdır: P2 fikstürü 44d5353'ün SHA'sını yazar, `tip` olarak o verilir."""
    user = pwd.getpwuid(os.getuid()).pw_name
    t0 = str(time.time() - 5)

    def case(name, **kw):
        res = tmp_path / name / "research"
        _night_fixture(res, **kw)
        return tool(tmp_path, "smoke", str(res), P2.TIP, "", t0, user, "research_engine_v1_p2", P2.ATTR, P2.CFG, "805306368", FD_VER)
    ok = case("ok", fidelity=V3, flags=("S1B_BACKLOG",),
              mem={"memory_max_bytes": 805306368, "memory_peak_ratio": 0.41, "memory_peak_bytes": 330 << 20})
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert re.search(r"fidelity: journal/tj_v1/_build\.json fidelity_v3 · 19/20 = %95,0 · bu çalıştırmada 40 satır yeniden "
                     r"kuruldu, bekleyen 0", ok.stdout), ok.stdout
    off = case("ab_off_redeploy", plan=["S0", "S1s"], result="AB_OFF", ab="AB_OFF")
    assert off.returncode == 0 and "bu çalıştırmada S1b yok (AB_OFF)" in off.stdout and "ölçülemedi" in off.stdout, off.stdout
    for name, kw, why in (("v2_build", {}, "fidelity sürümü None ≠ fidelity_v3"),
                          ("old_version", {"fidelity": {**V3, "version": "fidelity_v2"}}, "fidelity sürümü fidelity_v2 ≠ fidelity_v3"),
                          ("attr_seal", {"fidelity": V3, "attr": "a" * 64}, "attribution_v1 mührü"),
                          ("cfg_seal", {"fidelity": V3, "cfg": "c" * 64}, "cfgrid_v1 mührü"),
                          ("no_p2_plan", {"fidelity": V3, "plan": ["S0", "S1a", "S3", "S7", "S7b"]}, "planda S1b yok"),
                          ("peak", {"fidelity": V3, "mem": {"memory_peak_ratio": 0.83}}, "memory.peak 0,83")):
        cp = case(name, **kw)
        assert cp.returncode == 1 and "KALDI:" in cp.stdout and why in cp.stdout, (name, cp.stdout + cp.stderr)


def test_isolation_check_is_the_44d5353_check_unchanged_and_passes_on_the_target_tree(tmp_path, tool):
    """Yalıtım değişmezi gevşetilmez: gömülü `c_ast` ve onu çağıran kabuk satırları 44d5353 betiğininkiyle bayt bayt aynı;
    hedef ağaçta (P3b `parity.py` dahil, artık canlı config'i import etmez) yasak import yoktur."""
    def block(text: str) -> str:
        return text[text.index("def c_ast(tree):"):text.index("def c_lastrun(res):")]

    def shell(text: str) -> list[str]:
        return [ln for ln in text.splitlines() if 'inv "yalıtım-AST"' in ln or 'rpy ast "$W"' in ln]
    assert block(TEXT) == block(P2.TEXT) and shell(TEXT) == shell(P2.TEXT) and len(shell(TEXT)) == 2
    cp = tool(tmp_path, "ast", str(ROOT))
    assert cp.returncode == 0 and cp.stdout.strip() == "yasak import yok", cp.stdout + cp.stderr
    src = (ROOT / "tradingbot" / "research_engine" / "parity.py").read_text(encoding="utf-8")
    assert "from ..config_v3" not in src and "import config_v3" not in src


def test_check_counts_fidelity_only_for_a_finished_v3_build_and_lists_absent_upstream_apart(tmp_path, tool):
    from tradingbot.research_engine.summary import claim_word_violations
    empty = tool(tmp_path, "p2", str(tmp_path / "yok"), TIP, P2.ATTR, P2.CFG, FD_VER, "", "")
    rows = [ln for ln in empty.stdout.splitlines() if ln.startswith("   [")]
    assert empty.returncode == 0 and len(rows) == 10 and all("[ölçülemedi]" in ln for ln in rows), empty.stdout + empty.stderr
    started = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

    def p2(name, pending=0, **kw):
        res = tmp_path / name / "research"
        st = _night_fixture(res, started=started, **kw)
        _set_pending(res, st["run_id"], pending)
        cp = tool(tmp_path, "p2", str(res), P2.TIP, P2.ATTR, P2.CFG, FD_VER, "41", "12")
        assert cp.returncode == 0 and not claim_word_violations(cp.stdout.splitlines()), cp.stdout + cp.stderr
        return cp.stdout
    out = p2("v3", fidelity=V3)
    assert re.search(r"\[tamam\]\s+5 fidelity fidelity_v3 19/20 = %95,0 \(ölçüt ≥ %90; beklenen ≈ %93–95\) · nedenler "
                     r"EXIT_REASON_DIFFERS 1 · uygun değil SPOT 4, NO_PRE_EXIT_BAR 1$", out, re.M), out
    out = p2("v2", fidelity={**V3, "version": "fidelity_v2", "ok": 16, "rate": 0.8, "gate_pass": False})
    assert re.search(r"\[ölçülemedi\]\s+5 fidelity fidelity_v2 16/20 = %80,0 .* — özet fidelity_v2: fidelity_v3 satırları "
                     r"henüz kurulmadı \(ilk tam gecede\)", out), "eski sürümün oranı v3 ölçütüne sayılmaz"
    out = p2("rebuilding", pending=7, fidelity=V3)
    assert re.search(r"\[ölçülemedi\]\s+5 fidelity fidelity_v3 19/20 .* — yeniden kurulum sürüyor \(bekleyen 7 satır; oran "
                     r"v2/v3 karışık", out), out
    out = p2("low", fidelity={**V3, "ok": 17, "rate": 0.85, "gate_pass": False})
    assert re.search(r"\[DİKKAT\]\s+5 fidelity fidelity_v3 17/20 = %85,0", out), out
    assert re.search(r"\[tamam\]\s+8 mühürler attribution_v1 914875a1f663", out), "P2 satırları korunur"
    # V2: ABSENT_UPSTREAM bayat SAYILMAZ, taze de sayılmaz — ayrı listelenir; gerçek eski seri yine [DİKKAT]
    res = tmp_path / "data" / "research"
    now = datetime.now(UTC)
    base = tmp_path / "deploy.json"
    base.write_text(json.dumps({"tip": TIP, "started_at": int((now - timedelta(days=1)).timestamp())}), encoding="utf-8")
    _data_fixture(res, rid="20261011T080000Z", mode="backfill", started=(now - timedelta(hours=20)).isoformat())
    _data_fixture(res, rid="20261012T004100Z", started=(now - timedelta(hours=2)).isoformat())
    ds = json.loads((res / "summary" / "data_status.json").read_text(encoding="utf-8"))
    au = {"probe_day": "2026-10-10", "http": 404, "reference": "futures/牛来USDT/1h"}
    ser = {f"{g}/1d": {"planned": True, "stale": False, "age_h": 3.0, "gaps": 0}
           for g in ("futures/BTCUSDT", "futures/XAUUSDT", "futures/PAXGUSDT", "spot/PAXGUSDT")}
    for k in ("futures/牛来USDT/markpx_1h", "futures/牛来USDT/premium_1h", "futures/龙虾USDT/markpx_1h"):
        ser[k] = {"planned": False, "status": "ABSENT_UPSTREAM", "stale": False, "age_h": None, "rows": 0, "absent_upstream": au}
    ser["futures/龙虾USDT/premium_1h"] = {"planned": True, "status": "ABSENT_UPSTREAM", "stale": False, "age_h": None}  # eski biçim
    ds.update(series=ser, totals={"absent_upstream": 4}, dukascopy={"status": "YAPILAMADI"})
    (res / "summary" / "data_status.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")

    def dcheck():
        cp = tool(tmp_path, "dcheck", str(res), TIP, str(base), "1000", "1073741824", "inactive", "0", "5400000000",
                  "40000000000", SCRIPT.name)
        assert cp.returncode == 0 and not claim_word_violations(cp.stdout.splitlines()), cp.stdout + cp.stderr
        return cp.stdout
    out = dcheck()
    assert re.search(r"\[tamam\]\s+V2 tazelik: planlı 4 seri, last_ts ≤ 26 sa 4 · üst kaynakta yok 4 \(arşiv yayımlamıyor, "
                     r"kanıtlı; bayat da taze de sayılmaz, ayrı: futures/牛来USDT/markpx_1h, futures/牛来USDT/premium_1h, "
                     r"futures/龙虾USDT/markpx_1h, futures/龙虾USDT/premium_1h\) · altın tamam", out), out
    ser["futures/ETHUSDT/markpx_1h"] = {"planned": True, "status": "NO_BASELINE", "stale": True, "age_h": None}
    (res / "summary" / "data_status.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")
    out = dcheck()
    assert re.search(r"\[DİKKAT\]\s+V2 tazelik: planlı 5 seri, last_ts ≤ 26 sa 4 · üst kaynakta yok 4 .* — eski: "
                     r"futures/ETHUSDT/markpx_1h NO_BASELINE", out), "kanıtsız tabansız seri bayat kalır"


# ============================================================================ sandbox
@pytest.fixture(scope="module")
def vps44(tmp_path_factory, source, vps_template):
    """VPS'in bugünkü hâli: P2 şablonunun (P1a → 57cfef1 → 79b27cf) üstüne gerçek `tb-engine-44d5353.sh` dağıtımı.
    PR dalının ucu artık P2.1 hedefidir (betiklerin yedek getirme yolu). 44d5353 ağacının koşucu sonucu önbellekten."""
    assert _git("update-ref", "refs/heads/claude/gifted-knuth-0ehpcs", TIP, cwd=source).returncode == 0
    aux = vps_template[1]
    acc = [int(x) for x in re.search(r"^ACCEPT = \[([^\]]*)\]", P2.TEXT, re.M).group(1).replace(",", " ").split()]
    (aux / f"inv.{PREV_TIP}.json").write_text(json.dumps({"passed": 90, "failed": 0, "skipped": 0, "acceptance_passed": acc}),
                                              encoding="utf-8")
    root = tmp_path_factory.mktemp("vps44")
    sb = _sb(root, vps_template, source)
    cp = sb.run(script=P2.SCRIPT)
    assert cp.returncode == 0 and "DAĞITILDI: 33/33" in cp.out, cp.out[-4000:]
    assert sb.eng_head() == PREV_TIP and (sb.sd / SVC).read_bytes() == _blob(PREV_TIP, f"deploy/{SVC}")
    assert (sb.sd / P2.NDROP).read_text(encoding="utf-8").startswith(f"# tb-engine-{P1A_TIP[:7]}:")
    assert (sb.base / "deploy-logs" / f"engine-{PREV_TIP[:7]}-deploy.json").is_file()
    jb = json.loads((sb.res / "journal" / "tj_v1" / "_build.json").read_text(encoding="utf-8"))
    assert jb["fidelity"]["version"] == "fidelity_v2" and jb["rows"] > 0, "44d5353'ün günlüğü v2 ile kurulu"
    return root, aux


def _sb44(tmp_path: Path, vps44, source, **env):
    return _sb(tmp_path, vps44, source, **env)


def _p2_state(sb, units0: dict) -> None:
    """44d5353 durumu: engine-app 44d5353, birim dosyaları (gece + veri + drop-in'ler) şablondakiyle bayt bayt aynı."""
    assert sb.eng_head() == PREV_TIP and _units(sb) == units0
    st = sb.state_json()
    assert st["enabled"] == {TMR: True, DTMR: True} and st["active"] == {TMR: True, DTMR: True}
    assert st["loaded_mem"][SVC] == "805306368"


def _no_reload(log: list[str]) -> None:
    assert not [ln for ln in log if ln.split()[0] in NO_RELOAD], log


@needs_sandbox
def test_dry_run_on_the_p2_vps_runs_the_real_runner_and_changes_nothing(tmp_path, source, vps44):
    sb = _sb44(tmp_path, vps44, source)
    snap, made0 = sb.snap(), len(own_tmp(sb.fake))
    cp = sb.run("--dry-run", script=SCRIPT)
    assert cp.returncode == 0, cp.out[-6000:]
    m = re.search(r"KURU ÇALIŞMA: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "20", cp.out[-3000:]
    # hedef ağacın GERÇEK koşucu sonucu (bu modülde ilk sandbox testi onu koşar; sonrakiler aynı ağacın sonucunu önbellekten alır)
    assert "90 geçti · 0 kaldı · 0 atlandı" in cp.out
    for name in ("P2-kurulu", "yedek-doğrulandı", "1g-pencere-dışı", "kaynak-TIP", "app-SHA-ata", "44d5353-ata", "birim-sha256",
                 "birim-sözleşmesi", "systemd-analyze-verify", "compileall", "bağımsız-koşucu", "yalıtım-AST", "status-kuru",
                 "worker-MemoryMax=6G", "motor-boşta"):
        assert re.search(rf"\[tamam\] #\d+ {re.escape(name)}", cp.out), name
    assert re.search(r"\[tamam\] #\d+ yalıtım-AST\s+yasak import yok; cli import RSS", cp.out), cp.out[-3000:]
    assert re.search(r"UYARI\s+44d5353 A/B penceresi \(sürüm günü \d{4}-\d\d-\d\d\): 0/14 gece geçti — P2\.1 motor kodunu", cp.out)
    assert re.search(rf"OK\s+drop-in {re.escape(str(P2.NDROP))}: # tb-engine-{P1A_TIP[:7]}: .*\(içerik beklenenle aynı\) "
                     r"\(dokunulmaz\)", cp.out), cp.out[-3000:]
    sb.untouched_since(snap)
    _no_reload(sb.log()[snap["log"]:])
    _no_forbidden(sb)
    made = own_tmp(sb.fake)[made0:]
    assert made and not [p for p in made if p.exists()], "geçici klon silinmeli"


@needs_sandbox
@pytest.mark.parametrize("case", ["p1a_night_unit", "engine_app_79b27cf", "night_unit_not_loaded", "data_timer_off"])
def test_missing_p2_refuses_before_any_change(tmp_path, source, vps44, case):
    sb = _sb44(tmp_path, vps44, source)
    st = sb.state_json()
    if case == "p1a_night_unit":
        (sb.sd / SVC).write_bytes(_blob(P1B_TIP, f"deploy/{SVC}"))
    if case == "engine_app_79b27cf":
        assert _git("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", P1B_TIP, cwd=sb.base / "engine-app").returncode == 0
    if case == "night_unit_not_loaded":
        st["loaded"][SVC] = "0" * 64           # dosya doğru baytlarda ama systemd eskisini tutuyor (NeedDaemonReload=yes)
    if case == "data_timer_off":
        st["active"][DTMR] = False
    (sb.fake / "systemd.json").write_text(json.dumps(st), encoding="utf-8")
    snap = sb.snap()
    cp = sb.run(script=SCRIPT)
    want = {"p1a_night_unit": f"{SVC} baytları 44d5353'ün (= hedefin) değil (sha256 0db7a26a2a2c…)",
            "engine_app_79b27cf": "engine-app 79b27cf, 44d5353'ü (44d5353) içermiyor",
            "night_unit_not_loaded": f"{SVC} yüklü değil ya da NeedDaemonReload=yes (bu sürüm daemon-reload YAPMAZ",
            "data_timer_off": "veri zamanlayıcısı açık/aktif değil"}[case]
    assert cp.returncode == 1 and "DUR: P2-kurulu" in cp.out and want in cp.out, cp.out[-3000:]
    assert "(önce: tb-engine-44d5353.sh)" in cp.out and "HİÇBİR ŞEYE DOKUNULMADI" in cp.out
    sb.untouched_since(snap)
    _no_forbidden(sb)


@needs_sandbox
def test_upgrade_pins_engine_app_only_rebuilds_the_journal_once_check_rerun_and_rollback(tmp_path, source, vps44):
    from tradingbot.research_engine.summary import claim_word_violations
    sb = _sb44(tmp_path, vps44, source)
    units0, n0 = _units(sb), len(sb.log())
    rows0 = json.loads((sb.res / "journal" / "tj_v1" / "_build.json").read_text(encoding="utf-8"))["rows"]
    cp = sb.run(script=SCRIPT)
    assert cp.returncode == 0, cp.out[-6000:]
    m = re.search(r"DAĞITILDI: (\d+)/(\d+) değişmez geçti", cp.out)
    assert m and m.group(1) == m.group(2) == "28", cp.out[-3000:]
    log = sb.log()[n0:]
    _no_reload(log)
    assert [ln.split()[:2] for ln in log if ln.split()[0] in ("start", "stop")] == [["start", DSVC], ["start", SVC]], log
    assert _units(sb) == units0, "birim dosyaları ve drop-in'ler bayt bayt aynı"
    assert not list((sb.base / "deploy-logs").glob(f"engine-{TIP[:7]}-prev-units-*")), "birim yedeği yok: hiçbir birim yazılmadı"
    assert sb.eng_head() == TIP
    for name in ("birimler-aynı", "engine-app-sabit", "veri-smoke", "gece-smoke", "birimler-zamanlayıcılar", "worker-dokunulmadı"):
        assert re.search(rf"\[tamam\] #\d+ {re.escape(name)}", cp.out), name
    rs = json.loads((sb.res / "summary" / "run_status.json").read_text(encoding="utf-8"))
    assert rs["result"] == "SUCCESS" and rs["shas"]["engine"] == TIP and {"S1b", "S2"} <= set(rs["plan"])
    assert rs["stages"]["S2"]["result"]["attribution_sha"] == P2.ATTR and rs["stages"]["S2"]["result"]["cfgrid_sha"] == P2.CFG
    j = rs["stages"]["S1b"]["result"]["journal"]
    jb = json.loads((sb.res / "journal" / "tj_v1" / "_build.json").read_text(encoding="utf-8"))
    assert jb["fidelity"]["version"] == "fidelity_v3" and jb["rows"] == rows0
    assert j["built_rows"] == rows0 and j["pending"] == 0, "fidelity sürümü girdi özetinde: bütün satırlar bir kez yeniden"
    assert re.search(rf"fidelity: journal/tj_v1/_build\.json fidelity_v3 · .* bu çalıştırmada {rows0} satır yeniden kuruldu, "
                     r"bekleyen 0", cp.out), cp.out[-3000:]
    st = sb.state_json()
    assert st["enabled"] == {TMR: True, DTMR: True} and st["active"] == {TMR: True, DTMR: True}
    assert st["loaded_mem"][SVC] == "805306368"
    # ikinci gece: aynı kod, aynı girdiler → yeniden kurulan satır yok
    k = len(sb.log())
    assert subprocess.run([str(sb.stub / "systemctl"), "start", SVC], env=sb.env, capture_output=True, timeout=1200).returncode == 0
    rs2 = json.loads((sb.res / "summary" / "run_status.json").read_text(encoding="utf-8"))
    assert rs2["run_id"] != rs["run_id"] and rs2["stages"]["S1b"]["result"]["journal"]["built_rows"] == 0
    assert sb.log()[k:][0].startswith(f"start {SVC}")
    # --check: araştırma ağacı değişmez; P1a, P1b ve P2 satırları (fidelity_v3); iddia kelimesi yok
    d0 = _tree_digest(sb.res)
    ck = sb.run("--check", script=SCRIPT)
    assert ck.returncode == 0, ck.out[-4000:]
    for s in (f"engine-app {TIP[:7]}", "gece birimi dosyası: P2 baytları (640M/768M; bu sürümde değişmedi)",
              "veri birimi dosyaları: hedefle (= 44d5353) aynı", "K1 elle smoke (gece)", "K5 gece memory.peak", "V1 ilk doldurma",
              "V2 tazelik", "V6 veri/doldurma", "kabul ölçütleri — P2", "1 günlük = arşiv = ledger", "6 rehydrate eşleşmesi",
              "8 mühürler", "12 digest_tr.md", "13 son 3 ardışık gece", "fidelity (fidelity_v3)"):
        assert s in ck.out, s
    assert re.search(r"\]\s+5 fidelity fidelity_v3 ", ck.out), ck.out[-3000:]
    assert re.search(r"\[tamam\]\s+8 mühürler attribution_v1 914875a1f663 · cfgrid_v1 7c299ae001ae", ck.out), ck.out[-3000:]
    assert re.search(r"\[ölçülemedi\]\s+13 son 3 ardışık gece", ck.out), "tek gün: 3 gece yok, geçti sayılmaz"
    assert "geri alma (44d5353 durumu)" in ck.out and not claim_word_violations(ck.out.splitlines())
    assert _tree_digest(sb.res) == d0
    ab = sb.run("--ab-report", script=SCRIPT)
    assert ab.returncode == 0 and "A/B geceleri" in ab.out, ab.out[-2000:]
    k = len(sb.log())
    again = sb.run(script=SCRIPT)
    assert again.returncode == 0 and f"zaten dağıtılmış ({TIP[:7]}" in again.out, again.out[-2000:]
    assert not [ln for ln in sb.log()[k:] if ln.split()[0] in NO_RELOAD + ("start", "stop")]
    # --rollback → 44d5353 durumu (birimler aynı, reload YOK); 44d5353 betiği kurulumu tanır
    k = len(sb.log())
    rb = sb.run("--rollback", script=SCRIPT)
    assert rb.returncode == 0 and "GERİ ALINDI (44d5353)" in rb.out, rb.out[-4000:]
    chain = [rb.out.index(f"tb-engine-{s[:7]}.sh --rollback") for s in (PREV_TIP, P1B_TIP, P1A_TIP)]
    assert chain == sorted(chain), "zincir sırayla"
    assert not [ln for ln in sb.log()[k:] if ln.split()[0] in NO_RELOAD + ("start", "stop")], sb.log()[k:]
    _p2_state(sb, units0)
    assert _tree_digest(sb.res) == d0
    logs = sb.base / "deploy-logs"
    assert not (logs / f"engine-{TIP[:7]}-deploy.json").exists() and list(logs.glob(f"engine-{TIP[:7]}-deploy.json.rolledback-*"))
    again = sb.run("--rollback", script=SCRIPT)
    assert again.returncode == 0 and "zaten 44d5353'de" in again.out and "reload gerekmez, yapılmadı" in again.out, again.out[-2000:]
    p2 = sb.run(script=P2.SCRIPT)
    assert p2.returncode == 0 and "zaten dağıtılmış (44d5353" in p2.out, p2.out[-2000:]
    _no_reload(sb.log()[n0:])
    _no_forbidden(sb)


@needs_sandbox
def test_failed_night_smoke_and_an_error_after_the_pin_return_engine_app_to_44d5353(tmp_path, source, vps44):
    sb = _sb44(tmp_path, vps44, source)
    units0, n0 = _units(sb), len(sb.log())
    cp = sb.run(script=SCRIPT, FAKE_NIGHT_FAIL="1")
    assert cp.returncode == 2 and "gece smoke'u GEÇMEDİ" in cp.out, cp.out[-4000:]
    assert "engine-app önceki sabitine döndü (44d5353)" in cp.out and "--rollback GEREKMEZ" in cp.out, cp.out[-3000:]
    assert "birim dosyalarına ve zamanlayıcılara dokunulmadı" in cp.out
    assert any(ln.startswith(f"start {SVC}") for ln in sb.log()[n0:])
    _p2_state(sb, units0)
    assert not (sb.base / "deploy-logs" / f"engine-{TIP[:7]}-deploy.json").exists()
    # sabitlemeden sonra beklenmeyen hata (adım 8'in ilk gömülü araç çağrısı): on_err aynı geri dönüşü yapar
    flag = vps44[1] / "fail_dlast"
    flag.write_text("1", encoding="utf-8")
    try:
        cp = sb.run(script=SCRIPT)
    finally:
        flag.unlink()
    assert cp.returncode == 1 and cp.out.count("beklenmeyen hata") == 1, cp.out[-4000:]
    assert "engine-app önceki sabitine döndü (44d5353)" in cp.out and "--rollback GEREKMEZ" in cp.out, cp.out[-3000:]
    _p2_state(sb, units0)
    _no_reload(sb.log()[n0:])
    _no_forbidden(sb)
    # yeniden: bu kez geçer (geri dönüş bir şey bozmadı)
    ok = sb.run(script=SCRIPT)
    assert ok.returncode == 0 and "DAĞITILDI: 28/28" in ok.out and sb.eng_head() == TIP, ok.out[-3000:]
    assert _units(sb) == units0
    _no_reload(sb.log()[n0:])
