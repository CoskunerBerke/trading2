# -*- coding: utf-8 -*-
"""P3g — S1d `LIB_AG_V1`'in gece bağlantısı (docs/ENGINE_BRAIN_V1.md §8.6 S1d EKLEME API'si, §8.4 P3g notu). Yalnız sentetik
veri, ağ yok.

Kabul: her gece önce `seal_s1d` (tutmazsa AG atlanır, `AG_SKIPPED`; LIB_V1 sürer); AG'nin 38 `SPEC` satırı deftere S4x'ten
ÖNCE ve bir kez (sonra `DUPLICATE`); eklenemezse AG o gece koşmaz; S4w'de AG birimleri aynı kuyrukta (sembol × 12 grup,
coin'in LIB birimlerinden sonra, XAUUSDT en sonda, `AG_EVAL@AG_POOLED` planı kapatır) ve aynı pay/kontrol noktası/koruma;
değerlendirme varyant başına `lib_ag.evaluate(vid, satırlar, lib_ag.plan_for([vid], başlangıç, bitiş))` (41 sembolün
birleşimi); XAUUSDT'nin listelenme öncesi geçmişi yoktur (hata değil); S4x AG keşfi `S1D_SEAL_MS`'den ileri, artımlı =
baştan (bayt-aynı); Kapı A BH ailesi LIB + AG = 856, seçim grupları mühürden, N alt sınırı 4.551, AG örtüleri dönem
karşılaştırması ister (daha sıkı).
"""
from __future__ import annotations

import hashlib
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import FakeHost, FakeVps, run_engine_night  # noqa: E402
from research_engine_p3g_fixtures import COINS40, XAU, add_ag, config_text, night_after_seal, p3_world  # noqa: E402

from tradingbot.research_engine import agnight as AGN  # noqa: E402
from tradingbot.research_engine import explore as EX  # noqa: E402
from tradingbot.research_engine import lib_ag as LA  # noqa: E402
from tradingbot.research_engine import night as N  # noqa: E402
from tradingbot.research_engine import nightp3 as NP  # noqa: E402
from tradingbot.research_engine import registry as RG  # noqa: E402
from tradingbot.research_engine import seal_s1d as S1D  # noqa: E402
from tradingbot.research_engine import selfcheck as SC  # noqa: E402
from tradingbot.research_engine import stats as ST  # noqa: E402
from tradingbot.research_engine import summary as SM  # noqa: E402
from tradingbot.research_engine import synthetic as SY  # noqa: E402
from tradingbot.research_engine import trials as TR  # noqa: E402
from tradingbot.research_engine import wf as WF  # noqa: E402
from tradingbot.research_engine import wfqueue as WQ  # noqa: E402

UTC = timezone.utc
D1 = 86_400_000
H1 = 3_600_000
AG_SYMS = LA.universe_symbols()
_BUILD = WQ.build_units
#: hızlı ama her yolu kapsayan AG alt kümesi (5m/15m saf aileler, 1h seviyeli P5, 5m/4h/1d örtüler — BTC 1d dahil)
AG_MIX = ("AG_LS_HTF_QUIET", "AG_TENM_B_ANY", "AG_MSS_5m_2R_ALL", "AG_MSS_15m_3R_KZ", "AG_RB_DIRECT", "AG_MSNR_LEVEL",
          "AGO_BOX_NOREACT", "AGO_D4_KZ", "AGO_T2_CAL")


def _small_folds(monkeypatch):
    """Kısa sentetik geçmişe sığan WF katmanları (yalnız test; mühürlü uzunluklar `WF_PROTOCOL_V1`'de)."""
    monkeypatch.setattr(WF, "FOLDS", {"INTRADAY": {"train_days": 6, "validation_days": 2, "test_days": 2, "step_days": 2},
                                      "H1_PLUS": {"min_train_days": 20, "validation_days": 5, "test_days": 5,
                                                  "step_days": 5},
                                      "GOLD_FUT": {"min_train_days": 6, "validation_days": 2, "test_days": 2,
                                                   "step_days": 2}})


def _ag_plan(coins=("BTC", XAU)):
    plan = _BUILD(COINS40, AG_SYMS)
    return [u for u in plan if u.lib == WQ.AG_UNIT and u.coin in coins] + [plan[-1]]


def _store_world(tmp_path, *, coins=("BTC",), pre_5m_days=45, xau_days=40):
    from tradingbot.research_engine.paths import EnginePaths
    from tradingbot.research_engine.provider import SealedReader
    p = EnginePaths(data=tmp_path / "a", state=tmp_path / "s")
    p.ensure_tree()
    p3_world(p, p.state, coins=list(coins), fwd_days=3, pre_1d_days=500, pre_1h_days=pre_5m_days + 5,
             pre_5m_days=pre_5m_days, ag=True, xau_days=xau_days)
    return p, SealedReader.from_status(p)


# ============================================================================ plan
def test_ag_units_follow_each_coins_lib_units_xau_is_last_and_the_eval_unit_closes_the_plan():
    lib = _BUILD(COINS40)
    plan = _BUILD(COINS40, AG_SYMS)
    assert [u for u in plan if u.lib == WQ.LIB_UNIT] == lib and len(lib) == 680, "LIB_V1 planı değişmedi"
    ag = [u for u in plan if u.lib == WQ.AG_UNIT]
    assert len(AGN.groups()) == 12 and len(ag) == 41 * 12 and plan[-1].key == AGN.EVAL_UNIT == "AG_EVAL@AG_POOLED"
    assert plan[-1].lib == WQ.AG_EVAL and set(plan[-1].variants) == set(LA.variant_ids())
    assert sorted(v for _g, vs in AGN.groups() for v in vs) == sorted(LA.variant_ids()), "her AG varyantı tam bir grupta"
    assert [u.coin for u in plan[-13:-1]] == [XAU] * 12, "XAUUSDT (giriş evreni dışı) en sonda"
    for coin in COINS40:
        idx = [i for i, u in enumerate(plan) if u.coin == coin]
        kinds = [plan[i].lib for i in idx]
        assert idx == list(range(idx[0], idx[0] + len(idx))), "coin'in birimleri bitişik (geçmiş bir kez yüklenir)"
        assert kinds == sorted(kinds, key=lambda k: k != WQ.LIB_UNIT) and kinds.count(WQ.AG_UNIT) == 12
        tfs = [WQ.TF_ORDER[plan[i].signal_tf] for i in idx if plan[i].lib == WQ.AG_UNIT]
        assert tfs == sorted(tfs), "AG birimleri dilim büyükten küçüğe (5m en sonda)"
    assert {u.key.split("@")[0] for u in ag} == {g for g, _ in AGN.groups()}
    ident = WQ.code_identity()
    assert ident["s1d"] == S1D.S1D_SHA, "S1d değişirse kurulum yeniden başlar"


# ============================================================================ S4w: AG birimleri + değerlendirme
def test_ag_units_and_the_pooled_evaluation_match_lib_ag_evaluate_on_the_cached_rows(tmp_path, monkeypatch):
    _small_folds(monkeypatch)
    p, rd = _store_world(tmp_path)
    units = _ag_plan()
    now = int(night_after_seal(4).timestamp() * 1000)
    r = WQ.run_s4w(p, rd, entry_symbols=COINS40, ticks={"BTC": 1e-4, XAU: 0.01}, now_ms=now, deadline=1e12,
                   units=units, ag_symbols=AG_SYMS)
    assert r["stop"] == WQ.STOP_DONE and not r["errors"] and r["backlog"]["complete_month"] == "2026-09", r["errors"]
    assert r["ag"]["units_built"] == 24 and r["ag"]["units_done"] > 12 and r["ag"]["trades"] > 100
    assert r["ag"]["evaluated"] > 0
    q = WQ.read_queue(WQ.root_of(p))
    c = q["complete"]
    root = WQ.root_of(p)
    # XAUUSDT listelenmeden önce geçmişsiz: uzun pencereli 1d/4h örtüleri NO_DATA (hata değil), 5m birimleri koşar
    assert c["units"]["AGO_T2@XAU"]["status"] == WQ.ST_NO_DATA and c["units"]["AG_P3.5m@XAU"]["status"] == WQ.ST_DONE
    ev = list(WQ.read_cache(WQ.cache_file(root, c["month"], AGN.EVAL_UNIT)))
    head, rows = ev[0], {x["variant"]: x for x in ev[1:]}
    assert head["k"] == "H" and head["variants"] == 38 and set(rows) == set(LA.variant_ids())
    assert head["identity"]["inputs"] == {u.key: c["units"][u.key]["sha256"] for u in units if u.lib == WQ.AG_UNIT}
    # beklenen: önbellekteki satırlardan doğrudan `lib_ag.evaluate` (41 sembolün birleşimi; burada BTC + XAU)
    for vid in ("AG_MSS_5m_2R_ALL", "AG_RB_DIRECT", "AGO_BOX_KZ"):
        g = AGN.group_key(vid)
        trs, starts = [], []
        for coin in ("BTC", XAU):
            key = f"{g}@{coin}"
            if c["units"][key]["status"] != WQ.ST_DONE:
                continue
            for x in WQ.read_cache(WQ.cache_file(root, c["month"], key)):
                k = x.pop("k")
                if k == "H":
                    starts.append(int(x["start_ms"]))
                elif k == "T" and x["option"] == vid:
                    trs.append(x)
        res = LA.evaluate(vid, trs, LA.plan_for([vid], min(starts), int(c["end_ms"])))
        got = rows[vid]
        assert got["status"] == WQ.ST_DONE and got["rows"] == len(trs) > 0 and got["n_oos"] == len(res.trades)
        assert got["mean_r"] == pytest.approx(res.means) and got["oos_days"] == len(res.oos_days)
        assert [f["n"] for f in got["folds"]] == [f["n"] for f in res.folds] and got["start_ms"] == min(starts)
        assert got["row_id"] == f"{vid}@AG_POOLED" and got["cal_flag"] is None and got["wf_class"] == LA.wf_class(vid)
        assert set(got["instrument_net_r"]) <= {"BTC/USDT", "XAU/USDT"}
    assert rows["AGO_T2_KZ"]["symbols_with_trades"] <= 1
    # haftalık doğrulama yolu: aynı önbellekten yeniden değerlendirme bayt-aynı
    lines, meta = WQ.run_ag_eval(units[-1], root, c, units=units)
    assert hashlib.sha256(b"".join(lines)).hexdigest() == c["units"][AGN.EVAL_UNIT]["sha256"]


def test_ag_units_wait_when_ag_is_off_tonight_then_the_build_completes(tmp_path, monkeypatch):
    _small_folds(monkeypatch)
    p, rd = _store_world(tmp_path, pre_5m_days=20)
    lib = [u for u in _BUILD(COINS40) if u.coin == "BTC" and u.signal_tf == "1d"][:2]
    units = lib + _ag_plan()
    now = int(night_after_seal(4).timestamp() * 1000)
    kw = dict(entry_symbols=COINS40, ticks={"BTC": 1e-4, XAU: 0.01}, now_ms=now, deadline=1e12, units=units,
              ag_symbols=AG_SYMS)
    r = WQ.run_s4w(p, rd, ag_run=False, **kw)
    assert r["stop"] == WQ.STOP_DONE and r["units_done"] + r["units_no_data"] == 2, "LIB birimleri sürer"
    assert r["ag"]["run"] is False and r["ag"]["units_built"] == 0 and r["ag"]["waiting"] == 25
    assert r["backlog"]["building_month"] == "2026-09" and r["backlog"]["done"] == 2, "kurulum AG'siz tamamlanmaz"
    r2 = WQ.run_s4w(p, rd, ag_run=True, **kw)
    assert r2["ag"]["units_built"] == 24 and r2["backlog"]["complete_month"] == "2026-09" and r2["completed_month"] == "2026-09"


# ============================================================================ S4x: AG keşfi
def _ag_inputs(world, now):
    ctx = EX.ag_ctx()
    out = {}
    for c, w in world.items():
        fr = [(t, r, m, "archive") for t, r, m in w["funding"] or ()]
        out[c] = EX.inputs_from_rows(c, w["bars"], fr, 0.001, now_ms=now, t_seal_ms=ctx.t_seal_ms, markpx=w["markpx_1h"],
                                     ctx=ctx)
    return out


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and p.name != "_run.json"}


def test_ag_explore_is_forward_from_the_s1d_seal_and_incremental_equals_scratch(tmp_path):
    seal = int(S1D.S1D_SEAL_MS)
    day0 = seal // D1 * D1
    w = SY.explore_world(["BTC", XAU], seed=7, seal_ms=seal, fwd_days=8, pre_1d_days=420, pre_1h_days=40, pre_5m_days=36)
    add_ag(w)
    rnd = random.Random(3)
    nights = sorted(rnd.sample(range(2, 8), 3)) + [8]
    inc = tmp_path / "inc"
    for k in nights:
        rep = EX.explore_step_ag(inc, _ag_inputs(w, day0 + k * D1 + rnd.randrange(24) * H1), ag_symbols=AG_SYMS,
                                 only=AG_MIX)
        assert not rep["errors"], rep["errors"]
    last = day0 + 8 * D1 + 5 * H1
    rep = EX.explore_step_ag(inc, _ag_inputs(w, last), ag_symbols=AG_SYMS, only=AG_MIX)
    scr = tmp_path / "scr"
    rep2 = EX.explore_step_ag(scr, _ag_inputs(w, last), ag_symbols=AG_SYMS, only=AG_MIX)
    assert rep["plan"]["total"] == 38 * 41 == 1558 and rep["lib"] == "LIB_AG_v1" and rep["t_seal_ms"] == seal
    a, b = _files(inc), _files(scr)
    assert a.keys() == b.keys() and len(a) >= 2 * len(AG_MIX) and [k for k in a if a[k] != b[k]] == []
    sts = EX.read_states(scr)
    assert set(sts) == {f"{v}@{c}" for v in AG_MIX for c in ("BTC", XAU)}
    assert all(st["t_seal_ms"] == seal and st["s1d_sha"] == S1D.S1D_SHA and st["lib"] == "LIB_AG_v1" for st in sts.values())
    assert sum(st["summary"]["n"] for st in sts.values()) > 10, "gerçekten işlem var"
    for p in sorted(scr.rglob("*.jsonl")):
        for ln in p.read_text(encoding="utf-8").splitlines():
            row = EX.json.loads(ln)
            if row["k"] == "T":
                assert int(row["entry_ts"]) >= seal + WF.TF_MS[EX.meta(row["variant_id"]).signal_tf], "mühürden önce karar yok"
    # aynı gece yeniden: hiçbir şey değişmez
    rep3 = EX.explore_step_ag(inc, _ag_inputs(w, last), ag_symbols=AG_SYMS, only=AG_MIX)
    assert rep3["status"].get(EX.ST_UNCHANGED) == len(AG_MIX) * 2 and _files(inc) == a
    # AG satırının ileri okuması 41 sembolün birleşimidir (biri eksikse okuma yok → kapı FWD_MISSING)
    row = next(r for r in LA.gate_a_rows() if r["unit"] == "AG_RB_DIRECT")
    assert EX._row_members(row, None, None) == [f"AG_RB_DIRECT@{c}" for c in AGN.coins()]
    assert EX.fwd_reads(scr, [row]) == {}
    full = {f"AG_RB_DIRECT@{c}": sts["AG_RB_DIRECT@BTC"] for c in AGN.coins()}
    assert EX.fwd_reads(scr, [row], states=full)[row["row_id"]]["members"] == 41


def test_ag_explore_refuses_on_a_broken_s1d_seal(tmp_path, monkeypatch):
    monkeypatch.setattr(S1D, "check", lambda: (False, "S1D_SHA_MISMATCH: test"))
    rep = EX.explore_step_ag(tmp_path / "x", {}, ag_symbols=AG_SYMS)
    assert rep["status"] == {"REFUSED": 1} and rep["refused"]["reason"] == "S1D_SEAL" and not list((tmp_path / "x").glob("*/*"))
    with pytest.raises(S1D.S1dSealError):
        LA.make_variant("AG_RB_DIRECT")


# ============================================================================ gece: mühür, deneme kaydı, özet
def _night_world(tmp_path, monkeypatch, *, ag_coins=("BTC", XAU)):
    _small_folds(monkeypatch)
    lib = [u for u in _BUILD(COINS40) if u.coin == "BTC" and u.signal_tf == "1d"][:2]

    def build(syms, ag_symbols=None):
        return lib + (_ag_plan(ag_coins) if ag_symbols else [])
    monkeypatch.setattr(WQ, "build_units", build)
    v = FakeVps(tmp_path / "w")
    host = FakeHost(tmp_path / "host", config_text=config_text())
    p3_world(v.paths, v.state, coins=["BTC"], fwd_days=3, pre_1d_days=500, pre_1h_days=30, pre_5m_days=25, ag=True,
             xau_days=40)
    SC.register_epoch(v.paths, "b" * 40, datetime(2020, 1, 1, tzinfo=UTC), "t", code_hash=SC.engine_code_hash())
    v.fut("strategy_paper_box")
    return v, host


def _ag_specs(paths) -> list[dict]:
    return [r for r in TR.iter_rows(paths.trials_file) if r["kind"] == "SPEC" and r["lib_version"] == "LIB_AG_v1"]


def test_ag_night_registers_trials_before_any_ag_result_once_and_reports_the_856_family(tmp_path, monkeypatch):
    v, host = _night_world(tmp_path, monkeypatch)
    seen: dict[str, int] = {}
    orig = EX.run_s4x_ag

    def spy(paths, *a, **k):
        seen["specs_before_s4x_ag"] = len(_ag_specs(paths))                        # deneme satırları sonuçtan ÖNCE
        return orig(paths, *a, **k)
    monkeypatch.setattr(EX, "run_s4x_ag", spy)
    st = run_engine_night(v, host, night_after_seal(4))
    assert st["result"] == N.R_SUCCESS, {k: (x.get("status"), x.get("reason"), x.get("error")) for k, x in st["stages"].items()}
    ag = st["p3"]["ag"]
    assert ag["ok"] and ag["run"] and ag["seal"] == "OK" and ag["symbols"] == 41 and ag["s1d_sha"] == S1D.S1D_SHA
    assert ag["trials"] == {"ok": True, "status": "APPENDED", "appended": 38, "duplicate": 0, "rows": 38}
    assert seen["specs_before_s4x_ag"] == 38
    specs = _ag_specs(v.paths)
    assert sorted(r["row_id"] for r in specs) == sorted(r["row_id"] for r in LA.gate_a_rows())
    assert all(r["scope"] == "AG_POOLED" and r["params"]["row_type"] == "AG" for r in specs)
    ms = dict(NP.ag_module_src_shas())
    assert {r["row_id"]: r["spec_sha"] for r in specs} == {LA.row_id(vid): LA.spec_sha_for(vid, ms[vid])
                                                          for vid in LA.variant_ids()}
    x = st["stages"]["S4x"]["result"]
    assert x["ag"]["plan_total"] == 1558 and x["ag"]["errors"] == 0 and x["ag"]["status"].get("OK", 0) > 0
    w = st["stages"]["S4w"]["result"]
    assert w["ag"]["run"] and w["ag"]["units_built"] == 24 and w["ag"]["evaluated"] > 0
    assert w["backlog"]["complete_month"] == "2026-09"
    ga = st["stages"]["S6"]["result"]["gate_a"]
    assert ga["family"] == {"lib": 818, "ag": 38, "m": 856} == AGN.family_size()
    lines = SM.status_lines(v.paths, brief=True, now=night_after_seal(4) + timedelta(hours=2))
    assert any(ln.startswith("P3 AG (S1d LIB_AG_V1") and "bu gece koştu" in ln for ln in lines)
    assert any("BH ailesi m = 856" in ln for ln in lines) and any("AG keşfi" in ln for ln in lines)
    digest = (v.paths.research / "summary" / "digest_tr.md").read_text(encoding="utf-8")
    assert "P3 AG (S1d LIB_AG_V1" in digest and not SM.claim_word_violations(digest.splitlines())
    n1 = TR.TrialsLedger(v.paths).dsr_n()
    # ikinci gece: aynı spec'ler yeniden sayılmaz
    st2 = run_engine_night(v, host, night_after_seal(4) + timedelta(days=1))
    assert st2["p3"]["ag"]["trials"]["status"] == "PRESENT" and st2["p3"]["ag"]["trials"]["duplicate"] == 38
    assert len(_ag_specs(v.paths)) == 38 and TR.TrialsLedger(v.paths).dsr_n() >= n1
    assert sum(r["kind"] == "SPEC" and r["lib_version"] == "LIB_AG_v1" for r in TR.iter_rows(v.paths.trials_file)) == 38


def test_broken_s1d_seal_skips_ag_for_the_night_and_lib_v1_still_runs(tmp_path, monkeypatch):
    v, host = _night_world(tmp_path, monkeypatch)
    monkeypatch.setattr(S1D, "check", lambda: (False, "S1D_SHA_MISMATCH: LIB_AG_V1 test"))
    st = run_engine_night(v, host, night_after_seal(4))
    assert st["result"] == N.R_SUCCESS and NP.F_AG_SKIPPED in st["flags"]
    ag = st["p3"]["ag"]
    assert ag["ok"] is False and ag["run"] is False and "S1D_SEAL" in ag["reason"] and "trials" not in ag
    assert not v.paths.trials_file.exists() or not _ag_specs(v.paths), "mühür tutmuyorsa AG denemesi de yazılmaz"
    x, w = st["stages"]["S4x"], st["stages"]["S4w"]
    assert x["status"] == "OK" and "skipped" in x["result"]["ag"] and x["result"]["status"].get("OK", 0) > 0
    assert w["status"] == "OK" and w["result"]["ag"]["run"] is False and w["result"]["ag"]["units_built"] == 0
    assert w["result"]["units_done"] + w["result"]["units_no_data"] == 2, "LIB_V1 birimleri sürer"
    assert not (v.paths.research / "explore" / "LIB_AG_v1").exists()
    lines = SM.status_lines(v.paths, brief=True, now=night_after_seal(4) + timedelta(hours=2))
    assert any(ln.startswith("P3 AG") and "UYUŞMAZLIK" in ln and "koşmadı" in ln for ln in lines)


def test_unregistered_ag_trials_keep_ag_off_for_the_night(tmp_path, monkeypatch):
    v, host = _night_world(tmp_path, monkeypatch)
    monkeypatch.setattr(NP, "ag_module_src_shas", lambda: None)                  # git kimliği yok
    st = run_engine_night(v, host, night_after_seal(4))
    assert st["result"] == N.R_SUCCESS and NP.F_AG_TRIALS in st["flags"] and NP.F_AG_SKIPPED not in st["flags"]
    ag = st["p3"]["ag"]
    assert ag["ok"] is True and ag["run"] is False and ag["trials"]["status"] == "FAILED"
    assert st["stages"]["S4w"]["result"]["ag"]["units_built"] == 0 and "skipped" in st["stages"]["S4x"]["result"]["ag"]
    assert not v.paths.trials_file.exists() or not _ag_specs(v.paths)
    assert not (v.paths.research / "explore" / "LIB_AG_v1").exists(), "denemesi sayılmayan AG sonucu üretilmez"


def test_register_trials_fails_closed_on_a_broken_chain(tmp_path):
    from tradingbot.research_engine.paths import EnginePaths
    p = EnginePaths(data=tmp_path / "data", state=tmp_path / "state")
    ms = {v: hashlib.sha256(v.encode()).hexdigest() for v in LA.variant_ids()}
    r = AGN.register_trials(p, module_src_sha=ms)
    assert r == {"ok": True, "status": "APPENDED", "appended": 38, "duplicate": 0, "rows": 38}
    assert AGN.register_trials(p, module_src_sha=ms)["status"] == "PRESENT"
    raw = p.trials_file.read_bytes()
    p.trials_file.write_bytes(raw[:-30])
    bad = AGN.register_trials(p, module_src_sha=ms)
    assert bad["ok"] is False and bad["status"] == "FAILED" and "TRIALS_CHAIN_BROKEN" in bad["reason"]


# ============================================================================ Kapı A ailesi
def test_gate_a_family_is_lib_plus_ag_with_sealed_groups_and_the_ag_n_floor():
    fam = ST.gate_a_family_rows()
    assert fam == list(RG.gate_a_rows()) + LA.gate_a_rows() == AGN.gate_a_rows() and len(fam) == 856
    assert len({r["row_id"] for r in fam}) == 856, "satır kimlikleri çakışmaz"
    assert ST.gate_a_n_min() == LA.n_at_seal_with_ag() == RG.n_at_seal() + 38 == 4551
    exp = ST.expected_group_members()
    for g, members in LA.selection_groups().items():
        assert exp[g] == frozenset(members), g
    for r in LA.gate_a_rows():
        assert ST.selection_group_key(r["row_type"], r["unit"], r["scope"]) == LA.selection_group_key(r["row_id"])
    ev = ST.gate_a_evaluate({}, look_no=1, n_trials=4551)
    assert ev["family_m"] == 856


def test_ag_cover_rows_need_the_epoch_comparison_and_join_their_sealed_pbo_group():
    from test_research_engine_stats_p3d import _tiny_rows
    _w, rows, _pl, _fwd = _tiny_rows()
    base = dict(next(iter(rows.values())))
    rid_c, rid_p = "AGO_T2_KZ@AG_POOLED", "AG_RB_DIRECT@AG_POOLED"
    ag_rows = {rid_c: dict(base, row_id=rid_c, row_type="AG", unit="AGO_T2_KZ", scope="AG_POOLED"),
               rid_p: dict(base, row_id=rid_p, row_type="AG", unit="AG_RB_DIRECT", scope="AG_POOLED")}
    ev = ST.gate_a_evaluate(ag_rows, look_no=1, n_trials=4551)
    assert ev["family_m"] == 856
    assert "EPOCH_COMPARISON_MISSING" in ev["rows"][rid_c]["fails"], "AG örtüsü canlı defter kopyasıdır: daha sıkı"
    assert "EPOCH_COMPARISON_MISSING" not in ev["rows"][rid_p]["fails"]
    assert ev["rows"][rid_c]["pbo_group"] == "AG_COVER:T2@AG_POOLED" and ev["rows"][rid_c]["pbo_state"] == ST.GROUP_INCOMPLETE
    assert ev["rows"][rid_p]["pbo_group"] == "AG_FAM:AG_P4@AG_POOLED"
    ev2 = ST.gate_a_evaluate(ag_rows, look_no=1, n_trials=4551, epoch_ok={rid_c: True})
    assert "EPOCH_COMPARISON_MISSING" not in ev2["rows"][rid_c]["fails"]


def test_ag_placebo_uses_the_same_kernel_placebo_with_the_ag_variant():
    from tradingbot.research_engine import placebo as PL
    d = SY.box_world(3, days=6)
    kp = AGN.placebo_for("AGO_BOX_KZ", d)
    assert isinstance(kp, PL.KernelPlacebo) and kp.base.variant_id == "AGO_BOX_KZ" and kp.tf == "5m"
