# -*- coding: utf-8 -*-
"""Mühür S1d (`LIB_AG_V1`, `AG_UNIVERSE_V1`, `AG_CALENDAR_V1`; docs/ENGINE_BRAIN_V1.md §8.6 S1d, §8.2 tablosu).

Kabul: sha'lar sabit; her alan değişikliği sha'yı değiştirir; `S1D_SEAL_MS` mühür commit'inden önce değil (git); S1
(registry.py, prior_trials.json, config_epochs.json), S1b ve S1c dosyaları dokunulmadı ve S1d üçünü de genişletir;
asla-gevşetme yönleri her tanım alanını kapsar ve her değişikliği yakalar; varyant sayısı ve deneme bütçesi etkisi (N_raw
+ 38, tekilleştirme, ayrı LIB ailesi); evren (S1b 40 + XAUUSDT) ve XAUUSDT veri katmanı; takvim kuralları; tutmayan mühürde
AG varyantı kurulmaz; ağsız modüller veri birimini çekmez.
"""
from __future__ import annotations

import copy
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from tradingbot.research_engine import lib_ag as A
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import seal_s1b as S1B
from tradingbot.research_engine import seal_s1c as S1C
from tradingbot.research_engine import seal_s1d as S
from tradingbot.research_engine import trials as TR
from tradingbot.research_engine import universe as U
from tradingbot.research_engine.paths import EnginePaths

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_data_fixtures import CONFIG_ONE, utc  # noqa: E402
from research_engine_fixtures import FakeHost, FakeVps  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GIT = ("git", "-c", "log.showSignature=false")
REGISTRY_SHA_S1 = "3e700fa03e455237c5ef45e29b85e5a810a9f8f7bd9dfe88465e543a5620beaf"
S1B_SHA_PINNED = "b3254fe69f72c48dda1ec372e6230d8b4a8106bac61043c4f5b88b4db9d08c31"
S1C_SHA_PINNED = "277922ca4c84b29017e93b05e5e3947bce57b5e890ef8413828f03917a52de95"
S1D_SHAS_PINNED = {
    "LIB_AG_V1": "6421cce57d83d7178a5cfeebf32c6b8141f7977dbfba65e93a9a52a566be82a1",
    "AG_UNIVERSE_V1": "7e856fa946d6f0668df1e44e6a0cddd371cbaee8a5b70b72001fb27ed8c4a3ec",
    "AG_CALENDAR_V1": "1ac8e04cd3158aaaf80b29bd8a4f8b19e94b7bf1124607216c4db60ba140bb9d",
    "S1D_DIRECTIONS_V1": "a69cd988100a7be82efa88f86c2a1596e3e269bc49d141c5b5415ec4b19e1c15",
}
S1D_SEAL_MS_PINNED = 1791943200000
S1D_SHA_PINNED = "54b1447aee5c26d3a7b7bf69c21fcbf88dedc154d5a19804ad44b2b50ecf6b68"
BASE_COMMIT = "80fa144"              # S1d'nin dalı (S1c + P3f sonrası)


def _git(*args: str) -> str:
    return subprocess.run([*GIT, "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120, check=True).stdout


def _git_ok() -> bool:
    try:
        _git("rev-parse", "HEAD")
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _leaves(x, path=()):
    if isinstance(x, dict):
        for k in sorted(x):
            yield from _leaves(x[k], path + (k,))
    else:
        yield path, x


def _set(doc, path, v):
    cur = doc
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = v


def _mutate(v):
    if isinstance(v, bool):
        return not v
    if isinstance(v, (int, float)):
        return v + 1
    if isinstance(v, str):
        return v + "x"
    if isinstance(v, list):
        return v + ["NEW"]
    if isinstance(v, dict):
        return {**v, "NEW": 1}
    return "x"


def test_s1d_shas_are_pinned_and_extend_untouched_s1_s1b_s1c():
    assert dict(S.SHAS) == S1D_SHAS_PINNED == {k: v for k, v in S.PINNED.items() if k != "S1D"}
    assert S.S1D_SEAL_MS == S1D_SEAL_MS_PINNED and S.S1D_SHA == S1D_SHA_PINNED == S.PINNED["S1D"]
    assert RG.thaw(S.S1D_SEAL) == {"seal_id": "S1d", "shas": S1D_SHAS_PINNED, "s1d_seal_ms": S1D_SEAL_MS_PINNED,
                                   "extends_s1_registry_sha": REGISTRY_SHA_S1, "extends_s1b_sha": S1B_SHA_PINNED,
                                   "extends_s1c_sha": S1C_SHA_PINNED}
    assert (RG.REGISTRY_SHA, S1B.S1B_SHA, S1C.S1C_SHA) == (REGISTRY_SHA_S1, S1B_SHA_PINNED, S1C_SHA_PINNED)
    assert S.check() == (True, "OK") and S1C.check() == (True, "OK")
    with pytest.raises(TypeError):
        S.LIB_AG_V1["common"]["buffer_frac"] = 0.0                                # derin donuk
    assert S1B.ENTRY_UNIVERSE_V1_SHA == "f1d5744789fa585e6bc5a059168c9bdd1bb5138902dbacbc90d81139362ee3f6"
    if not _git_ok():
        pytest.skip("git geçmişi yok")
    try:
        _git("cat-file", "-e", f"{BASE_COMMIT}^{{commit}}")
    except subprocess.CalledProcessError:
        pytest.skip(f"{BASE_COMMIT} bu klonda yok")
    for f in ("tradingbot/research_engine/registry.py", "tradingbot/research_engine/seal_s1b.py",
              "tradingbot/research_engine/seal_s1c.py", "tradingbot/research_engine/prior_trials.json",
              "tradingbot/research_engine/library/config_epochs.json", "tradingbot/strategy_lib/contract.py",
              "tradingbot/strategy_lib/indicators.py"):
        assert _git("diff", "--no-textconv", BASE_COMMIT, "--", f) == "", f        # mühürlü dosyalar ve kimlik çekirdeği aynı


def test_s1d_seal_ms_is_not_before_its_commit():
    t = S.S1D_SEAL_MS
    assert t % 60_000 == 0 and t > S1C.S1C_SEAL_MS > S1B.S1B_SEAL_MS > RG.T_SEAL_MS
    if not _git_ok():
        pytest.skip("git geçmişi yok")
    out = _git("log", "-1", "--no-textconv", "--format=%ct %H", "--", "tradingbot/research_engine/seal_s1d.py").split()
    if not out:
        pytest.skip("seal_s1d.py henüz işlenmedi")
    ct, sha = int(out[0]), out[1]
    assert ct * 1000 <= t, f"S1D_SEAL_MS {t} seal_s1d.py'yi değiştiren {sha[:12]} commit'inden ({ct}) önce: yeniden mühürle"
    assert t - ct * 1000 <= 24 * 3600 * 1000


def test_any_field_change_changes_the_sha():
    for name, reg in S.REGISTRIES.items():
        base = RG.thaw(reg)
        sha0 = RG.sha256_canonical(base)
        n = 0
        for path, v in _leaves(base):
            doc = copy.deepcopy(base)
            _set(doc, path, _mutate(v))
            assert RG.sha256_canonical(doc) != sha0, (name, path)
            n += 1
        assert n >= 8, name
    assert RG.sha256_canonical(S.seal_doc(seal_ms=S.S1D_SEAL_MS + 60_000)) != S.S1D_SHA
    for k in ("s1_registry_sha", "s1b_sha", "s1c_sha"):
        assert RG.sha256_canonical(S.seal_doc(**{k: "0" * 64})) != S.S1D_SHA, k


def test_never_loosen_directions_cover_every_definition_field_and_catch_changes():
    pairs = {"LIB_AG": S.LIB_AG_V1, "AG_UNIVERSE": S.AG_UNIVERSE_V1, "AG_CALENDAR": S.AG_CALENDAR_V1}
    assert set(S.S1D_DIRECTIONS_V1) == set(S.S1D_VERSIONS) == set(pairs)
    for name, v1 in pairs.items():
        dirs = S.S1D_DIRECTIONS_V1[name]
        assert set(dirs.values()) == {RG.FIXED}
        for path, _v in _leaves(RG.thaw(v1)):
            if path[0] in S.META_KEYS:
                continue
            dotted = ".".join(path)
            assert any(dotted == d or dotted.startswith(d + ".") for d in dirs), (name, dotted)
        assert RG.looser_than(v1, v1, dirs) == []
        for path, _v in _leaves(RG.thaw(v1)):                                     # her tanım yaprağının değişimi yakalanır
            if path[0] in S.META_KEYS:
                continue
            changed = RG.thaw(v1)
            _set(changed, path, _mutate(_v))
            bad = RG.looser_than(v1, changed, dirs)
            assert len(bad) == 1 and bad[0].startswith(path[0] + ":"), (name, path, bad)


def test_variant_count_by_family_and_the_trial_budget_effect(tmp_path):
    assert len(A.variant_ids()) == 38 and A.budget() == {
        "variants": 38, "family_counts": {"AG_P1": 4, "AG_P2": 4, "AG_P3": 8, "AG_P4": 2, "AG_P5": 2, "AG_O": 18},
        "gate_a_rows": 38, "raw_trials": 38, "explore_series": 38 * 41}
    assert dict(S.LIB_AG_V1["budget"]["family_counts"]) == A.budget()["family_counts"]
    assert RG.n_at_seal() == 4513 and A.n_at_seal_with_ag() == 4551 == S.LIB_AG_V1["budget"]["n_at_seal_with_ag"]
    assert RG.budget_table()["TOTAL"] == (818, 1738) and RG.unique_variant_count() == 106       # LIB_V1 değişmedi
    assert list(S.LIB_AG_V1["budget"]["structurally_empty"]) == ["AGO_T2_KZ", "AGO_M2_KZ"]
    groups = A.selection_groups()
    assert sorted(len(v) for v in groups.values()) == [2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 8]
    assert all(A.selection_group_key(r) == k for k, rows in groups.items() for r in rows)
    assert {A.max_hold_bars(v) for v in A.variant_ids() if not v.startswith("AGO_")} == {48}
    assert (A.max_hold_bars("AGO_BOX_KZ"), A.max_hold_bars("AGO_D4_CAL"), A.max_hold_bars("AGO_FM_NOREACT"),
            A.max_hold_bars("AGO_T2_CAL")) == (288, 300, 24, 300)
    assert A.wf_class("AG_MSS_15m_2R_KZ") == "INTRADAY" and A.wf_class("AGO_D4_KZ") == "H1_PLUS"
    plan = A.plan_for(["AG_LS_NONE_NONE"], 1_700_000_000_000, 1_700_000_000_000 + 400 * 86_400_000)
    assert plan["meta"] == {"wf_class": "INTRADAY", "signal_tf": "5m", "max_hold": 48, "purge_bars": 24, "embargo_bars": 25}
    # deneme defteri: 38 SPEC → N_raw + 38; ikinci kez sayılmaz; LIB_v1'den ayrı aile
    paths = EnginePaths(data=tmp_path / "data", state=tmp_path / "data" / "state")
    led = TR.TrialsLedger(paths)
    led.import_priors(at="2026-10-13T00:00:00+00:00")
    n0 = led.counters["N_raw_cum"]
    fake = {v: "%064x" % i for i, v in enumerate(A.variant_ids(), 1)}
    rows = A.seal_entries(fake)
    assert len(rows) == 38 and {r["lib_version"] for r in rows} == {"LIB_AG_v1"} and len({r["spec_sha"] for r in rows}) == 38
    out = led.append([dict(r, at="2026-10-13T00:00:00+00:00") for r in rows])
    assert {o["status"] for o in out} == {"APPENDED"}
    assert led.counters["N_raw_cum"] == n0 + 38 and led.dsr_n() == n0 + 38
    again = TR.TrialsLedger(paths).append([dict(r, at="2026-10-13T00:00:00+00:00") for r in rows])
    assert {o["status"] for o in again} == {"DUPLICATE"}                           # tekilleştirildi
    led = TR.TrialsLedger(paths)
    assert led.counters["N_raw_cum"] == n0 + 38
    led.append([dict(rows[0], lib_version="LIB_v1", at="2026-10-13T00:00:00+00:00")])
    assert led.counters["N_raw_cum"] == n0 + 39                                  # ayrı aile → ayrı deneme
    for bad in ("LIB_AG", "LIB_AG_v0", "LIB_AG_v1.2026-11", "LIB_ag_v1"):
        with pytest.raises(TR.TrialRowError):
            TR.validate_input(dict(rows[0], lib_version=bad))


def test_spec_sha_uses_the_sealed_params_and_module_identity():
    s1 = A.spec_sha_for("AG_RB_DIRECT", "a" * 64)
    assert s1 != A.spec_sha_for("AG_RB_CONFIRM", "a" * 64) != A.spec_sha_for("AG_RB_DIRECT", "b" * 64)
    sp = A.spec_for("AG_LS_NONE_NONE", "a" * 64)
    assert sp["params"]["common"]["buffer_frac"] == 0.0005 and sp["sizing"] == RG.thaw(RG.LIB_V1["sizing"])
    assert set(sp) == set(RG.SPEC_KEYS)


def test_ag_universe_is_s1b_forty_plus_xauusdt_and_calendar_rules():
    u = S.AG_UNIVERSE_V1
    assert list(u["coins"]) == list(S1B.ENTRY_UNIVERSE_V1["coins"]) + ["XAU"] and u["size"] == 41
    assert A.universe_symbols()[-1] == "XAUUSDT" and len(set(A.universe_symbols())) == 41 and len(A.series_plan()) == 1558
    d = A.universe_diff([c + "/USDT" for c in S1B.ENTRY_UNIVERSE_V1["coins"]])
    assert d["ok"] and d["ag_symbols"] == A.universe_symbols()
    assert not A.universe_diff(["BTC/USDT"])["ok"]
    cal = S.AG_CALENDAR_V1
    for y, ds in cal["fomc_statement_days"].items():
        assert len(ds) == 8 and all(x.startswith(y) for x in ds)
        for x in ds:
            wd = date.fromisoformat(x).weekday()
            assert wd == 2 or x == "2024-11-07", x                                 # Çarşamba; 2024-11-07 Perşembe (seçim)

    def nth(y, m, wd, n):
        d0 = date(y, m, 1)
        return d0 + timedelta(days=(wd - d0.weekday()) % 7 + 7 * (n - 1))

    def last(y, m, wd):
        d0 = date(y, m + 1, 1) - timedelta(days=1)
        return d0 - timedelta(days=(d0.weekday() - wd) % 7)

    def observed(d0):
        return d0 + timedelta(days=1) if d0.weekday() == 6 else (d0 - timedelta(days=1) if d0.weekday() == 5 else d0)

    easter = {2023: date(2023, 4, 9), 2024: date(2024, 3, 31), 2025: date(2025, 4, 20), 2026: date(2026, 4, 5)}
    for y in range(2023, 2027):
        want = {observed(date(y, 1, 1)), nth(y, 1, 0, 3), nth(y, 2, 0, 3), easter[y] - timedelta(days=2), last(y, 5, 0),
                observed(date(y, 6, 19)), observed(date(y, 7, 4)), nth(y, 9, 0, 1), nth(y, 11, 3, 4), observed(date(y, 12, 25))}
        want = {d0 for d0 in want if d0.year == y}
        if y == 2025:
            want.add(date(2025, 1, 9))                                             # Carter ulusal yas günü
        assert {date.fromisoformat(x) for x in cal["nyse_full_holidays"][str(y)]} == want, y
    assert list(cal["blocked_dates"]) == sorted(set(cal["blocked_dates"])) and len(cal["blocked_dates"]) == 41 + 32
    assert set(cal["uncertain"]) <= set(cal["fomc_statement_days"]["2026"]) and cal["source"]["verified_online"] is False


def test_xauusdt_data_tiers_include_the_mark_series_from_listing(tmp_path):
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path, config_text=CONFIG_ONE)
    now = utc(2026, 10, 13, 0, 41)
    doc = U.build_universe(v.paths, now=now, app_dir=host.app)
    assert U.ROLE_AG in doc["gold_futures"]["XAUUSDT"] and U.ROLE_AG not in doc["gold_futures"]["PAXGUSDT"]
    plan = {s.key: s for s in U.plan_series(doc, now=now)}
    for k in ("1m", "5m", "15m", "1h", "4h", "1d", "funding", "markpx_1h"):
        s = plan[f"futures/XAUUSDT/{k}"]
        assert s.from_listing and (s.start_ms == U.day_ms("2025-12-01") or k == "1m"), k
    assert "futures/PAXGUSDT/markpx_1h" not in plan and "futures/XAUUSDT/metrics_5m" not in plan
    assert list(S.AG_UNIVERSE_V1["extra"]["XAU"]["data_tiers"]) == ["1m", "5m", "15m", "1h", "4h", "1d", "funding", "markpx_1h"]


def test_broken_seal_refuses_to_build_ag_variants(monkeypatch):
    monkeypatch.setattr(S, "PINNED", {**S.PINNED, "AG_CALENDAR_V1": "0" * 64})
    assert S.check()[0] is False and S.check()[1].startswith("S1D_SHA_MISMATCH: AG_CALENDAR_V1")
    with pytest.raises(S.S1dSealError):
        A.make_variant("AG_RB_DIRECT")


def test_engine_modules_stay_network_free():
    code = ("import sys; import tradingbot.research_engine.lib_ag, tradingbot.research_engine.seal_s1d; "
            "print('tradingbot.research_engine.datastore' in sys.modules)")
    cp = subprocess.run([sys.executable, "-s", "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert cp.returncode == 0 and cp.stdout.strip().splitlines()[-1] == "False", cp.stderr[-2000:]


def test_ag_rows_run_through_the_sealed_walk_forward_path():
    """AG satırı LIB_V1'in mühürlü WF yolundan geçer: çekirdek → `trade_rows` (MTM) → `plan_for` (INTRADAY, B1) →
    `wf.evaluate_fixed` (sızıntı denetimi, OOS katmanları)."""
    from tradingbot.research_engine import kernel as KN
    from tradingbot.research_engine import synthetic as SY
    from tradingbot.research_engine import wf as WF
    t0 = 1_735_689_600_000                                                         # 2025-01-01
    m5 = SY.walk(200 * 288, seed=5, step=300_000, start=t0, p0=100.0, vol=0.002, trend=0.0004, regime_len=24, gap_p=0.0,
                 gap_vol=0.0, wick=0.9)
    fr = SY.funding_rows(m5, 300_000, seed=5)
    data = KN.SeriesData(symbol="SYN", bars={"5m": m5}, funding=fr, price_tick=0.001, markpx_1h=SY.markpx_from_funding(fr))
    vid = "AG_RB_CONFIRM"
    res, sig = A.run_variants([vid], data)
    rows = A.trade_rows(res.values(), data)
    assert len(rows) > 50 and sig[vid] and all(r["mtm"] for r in rows) and {r["option"] for r in rows} == {vid}
    start = WF.data_start_ms(int(m5[0]["timestamp"]), "5m")
    plan = A.plan_for([vid], start, int(m5[-1]["timestamp"]) + 300_000)
    rr = A.evaluate(vid, rows, plan)
    assert rr.row_id == f"{vid}@AG_POOLED" and rr.row_type == "AG" and rr.scope == "AG_POOLED"
    assert plan["meta"]["wf_class"] == "INTRADAY" and len(plan["folds"]) >= 1


def test_doc_section_8_6_carries_the_same_seal_values():
    text = (ROOT / "docs" / "ENGINE_BRAIN_V1.md").read_text(encoding="utf-8")
    for name, sha in S1D_SHAS_PINNED.items():
        assert f"`{name}` `{sha}`" in text, name
    assert f"`S1D_SEAL_MS` `{S1D_SEAL_MS_PINNED}`" in text and f"`S1D_SHA` `{S1D_SHA_PINNED}`" in text
    assert "| **S1d** |" in text and "### 8.6 S1d — LIB_AG_V1" in text
