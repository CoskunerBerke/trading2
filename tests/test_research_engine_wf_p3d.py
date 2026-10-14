# -*- coding: utf-8 -*-
"""P3d — walk-forward (docs/ENGINE_BRAIN_V1.md §3.1–§3.3, §8.1 P3d; mühür `WF_PROTOCOL_V1`, B1). Sentetik dünyalar
(`research_engine/synthetic.py`), ağ yok.

Kabul: `leakage_check` ve ambargo; `COIN_FIT` üç yollu (`run_three_way` her adımın yalnız kendi verisini görür); maliyet
senaryoları; kapı dışı işlemler; aileler `make_family` + `run_lockstep`; seçim ve bakış satırları deftere yalnız
`trials.py` ile; çekirdekten Kapı A bakışına uçtan uca sentetik koşu (süre ölçülür).
"""
from __future__ import annotations

import dataclasses
import json
import random
import time
import hashlib
from datetime import date, datetime, timezone

import pytest

from tradingbot.quant import walkforward as QW
from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import placebo as PL
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import stats as ST
from tradingbot.research_engine import synthetic as S
from tradingbot.research_engine import trials as TR
from tradingbot.research_engine import wf as WF
from tradingbot.research_engine.paths import EnginePaths

DAY = 86_400_000
T0 = S.T0
AT = "2026-12-06T01:37:00+00:00"


# ============================================================================ katmanlar ve B1
def test_b1_purge_embargo_split_is_the_sealed_reading():
    ex = RG.WF_PROTOCOL_V1["purge_embargo"]["make_folds_split"]["example"]
    assert WF.purge_embargo(ex["max_hold"]) == (ex["purge_bars"], ex["embargo_bars"]) == (150, 151)
    for mh in range(1, 500):
        p, e = WF.purge_embargo(mh)
        assert p + e == 1 + mh and p == (1 + mh) // 2 and e - p in (0, 1)


@pytest.mark.parametrize("vid, mode, train, val, test, gap_bars, bar", [
    ("TS_7_LO", "anchored", 365, 90, 90, 301, DAY),
    ("K_D4", "anchored", 365, 90, 90, 301, 4 * 3_600_000),
    ("KEL_20_2_1h", "anchored", 365, 90, 90, 49, 3_600_000),
    ("K_BOX", "rolling", 120, 30, 14, 289, 300_000),
    ("G_LDN_ORB", "anchored", 180, 30, 30, 301, 3_600_000)])
def test_fold_classes_lengths_and_both_boundary_gaps(vid, mode, train, val, test, gap_bars, bar):
    plan = WF.plan_for([vid], T0, T0 + 2400 * DAY)
    assert plan["layout"] == "three_way" and plan["mode"] == mode and len(plan["folds"]) >= 3
    prev = None
    for w in plan["folds"]:
        assert w.val_start - w.train_end == gap_bars * bar == w.test_start - w.val_end        # her iki sınırda 1 + max_hold
        assert w.val_end - w.val_start == val * DAY and w.test_end - w.test_start == test * DAY
        if mode == "anchored":
            assert w.train_start == T0
        else:
            assert w.train_end - w.train_start == train * DAY
        if prev is not None:
            assert w.test_start == prev.test_end and w.train_end - prev.train_end == test * DAY  # adım = test
        prev = w
    assert plan["folds"][0].train_end - T0 == train * DAY


def test_max_hold_resolution_from_the_seal():
    assert WF.max_hold_bars("TS_7_LO") == 300 and WF.max_hold_bars("KEL_20_2_1h") == 48
    assert WF.max_hold_bars("FUND_FADE_24h") == 6 and WF.max_hold_bars("FUND_FADE_LOW_72h") == 18
    assert WF.max_hold_bars("K_D4") == 300 and WF.max_hold_bars("DON_20_10_4h_LO") == 300                  # alias → K_D4
    assert WF.max_hold_bars("K_T2") == 300 and WF.max_hold_bars("K_C4_CV005") == 24 and WF.max_hold_bars("K_FM") == 24
    assert WF.max_hold_bars("O_C4_X_TIME") == 24 and WF.max_hold_bars("O_T2_F_BTC") == 300
    assert WF.max_hold_bars("K_BOX") == WF.max_hold_bars("BOX_n20_2r_ms1.0") == 288                       # UTC gün sonu
    assert WF.data_start_ms(T0 - 400 * DAY, "1d") == WF.DATA_START_EARLIEST_MS == T0                     # en erken 2020-01-01
    assert WF.data_start_ms(T0 + 5 * DAY, "4h") == T0 + 5 * DAY + 210 * 4 * 3_600_000
    with pytest.raises(ValueError):
        WF.plan_for(["K_BOX", "TS_7_LO"], T0, T0 + 2000 * DAY)                                           # farklı sınıf


# ============================================================================ sızıntı
def _row(ts: int, *, opt: str = "TS_7_LO", sym: str = "BTC", r: float = 0.1, hold_days: float = 2, as_of=None,
         flags=(), base: float | None = None) -> dict:
    rr = {"base": r + 0.01 if base is None else base, "ADVERSE": r, "STRESS": r - 0.01}
    return {"ts_ms": ts, "as_of_ms": ts if as_of is None else as_of, "exit_ms": ts + int(hold_days * DAY),
            "day": ts // DAY, "symbol": sym, "option": opt, "side": "LONG", "signal_ts": ts - DAY, "bars_held": 2,
            "r": rr, "pnl": dict(rr), "flags": list(flags), "excluded": KN.gate_excluded({"flags": list(flags)}),
            "trade_key": f"{opt}|{sym}:{ts}:LONG|{ts}"}


def _daily_rows(plan, opt="TS_7_LO", sym="BTC", every=3, seed=1, mu=0.05):
    rng = random.Random(seed)
    start, end = plan["folds"][0].train_start, plan["folds"][-1].test_end
    return [_row(t, opt=opt, sym=sym, r=rng.gauss(mu, 1.0)) for t in range(start + DAY, end - DAY, every * DAY)]


def test_leaking_folds_are_dropped_and_counted():
    plan = WF.plan_for(["TS_7_LO"], T0, T0 + 2300 * DAY)
    rows = _daily_rows(plan)
    w1, w3 = plan["folds"][1], plan["folds"][3]
    # (a) eğitim işleminin etiketi doğrulamaya taşıyor (purge/embargo'dan uzun tutuş), (b) gelecekten veri (as_of > ts)
    long_hold = _row(w1.train_end - DAY, hold_days=310)
    future = _row(w3.test_start + 5 * DAY, as_of=w3.test_start + 6 * DAY)
    rows += [long_hold, future]
    a, leaks = WF.assign_and_check(plan, rows)
    # (a) yalnız etiketinin taştığı katmanda; (b) satır hangi katmanın hangi diliminde kullanılıyorsa orada
    used = {w.idx for w in plan["folds"] if w.train_start <= future["ts_ms"] < w.train_end
            or w.val_start <= future["ts_ms"] < w.val_end or w.test_start <= future["ts_ms"] < w.test_end}
    assert 3 in used and sorted(leaks) == sorted({1} | used)
    rr = WF.evaluate_fixed("TS_7_LO@POOLED_P23", "POOLED", "TS_7_LO", "POOLED_P23", rows, plan)
    assert rr.excluded["n_leak_folds"] == len(leaks) and rr.excluded["leak_folds"] == sorted(leaks)
    assert [f["idx"] for f in rr.folds if f["leak"]] == sorted(leaks)
    dropped = [w for w in plan["folds"] if w.idx in leaks]
    for w in dropped:
        assert not any(w.test_start <= t["ts_ms"] < w.test_end for t in rr.trades)            # atılan katman OOS'ta yok
    kept = [w for w in plan["folds"] if w.idx not in leaks]
    assert set(rr.oos_days) == {d for w in kept for d in WF.window_days(w.test_start, w.test_end)}
    clean = WF.evaluate_fixed("TS_7_LO@POOLED_P23", "POOLED", "TS_7_LO", "POOLED_P23", _daily_rows(plan), plan)
    assert clean.excluded["n_leak_folds"] == 0


def test_purge_and_embargo_rows_enter_no_set():
    plan = WF.plan_for(["TS_7_LO"], T0, T0 + 2000 * DAY)
    w = plan["folds"][0]
    gap_rows = [_row(w.train_end + k * DAY) for k in range(0, 301, 50)] + [_row(w.val_end + k * DAY) for k in range(0, 301, 60)]
    a, leaks = WF.assign_and_check(plan, gap_rows)
    slot = a["folds"][0]
    assert not leaks and not slot["train"] and not slot["validation"] and not slot["test"]
    assert QW.assign_rows(gap_rows, plan, ts_key="ts_ms")["purged"] >= len(gap_rows)


def test_kernel_trades_with_time_stops_never_leak():
    d = dataclasses.replace(S.d4_world(52, n=5600), symbol="BTC")
    start = WF.data_start_ms(d.bars["4h"][0]["timestamp"], "4h")
    res, _sig = WF.run_variants(["DON_20_10_4h_LS", "MAX_20_50_4h_LS"], d, start_ms=start, record_signals=False)
    rows = WF.trade_rows(res.values())
    plan = WF.plan_for(["DON_20_10_4h_LS", "MAX_20_50_4h_LS"], start, int(d.bars["4h"][-1]["timestamp"]) + 4 * 3_600_000)
    assert len(rows) > 50 and len(plan["folds"]) >= 2
    _a, leaks = WF.assign_and_check(plan, rows)
    assert leaks == {}
    assert all(r["exit_ms"] - r["ts_ms"] <= (WF.max_hold_bars(r["option"]) + 1) * 4 * 3_600_000 for r in rows)


# ============================================================================ COIN_FIT üç yollu
COINS = ("BTC", "ETH", "SOL", "BNB")


def _coin_fit_world(plan, seed=3, best=None):
    """Izgara (B) × 4 coin; `best` = {coin: (seçenek, ortalama)} coin'e özel avantaj."""
    best = best or {"BTC": ("TS_28_LO", 0.6)}
    rng = random.Random(seed)
    start, end = plan["folds"][0].train_start, plan["folds"][-1].test_end
    rows = []
    for o in WF.grid("B"):
        for c in COINS:
            t = start + DAY
            while t < end - 3 * DAY:
                mu = best[c][1] if c in best and best[c][0] == o else -0.02
                rows.append(_row(t, opt=o, sym=c, r=rng.gauss(mu, 1.0)))
                t += rng.randint(2, 5) * DAY
    return rows


@pytest.fixture(scope="module")
def cf_plan():
    return WF.plan_for(WF.grid("B"), T0, T0 + 2100 * DAY)


def test_run_three_way_steps_see_only_their_own_slices(cf_plan, monkeypatch):
    rows = _coin_fit_world(cf_plan)
    seen: list[tuple[str, list[int]]] = []
    orig = QW.run_three_way

    def spy(plan, assignment, *, fit_fn, candidates_fn, select_fn, evaluate_fn, **kw):
        def wrap(name, fn, rows_pos):
            def inner(*args):
                seen.append((name, [r["ts_ms"] for r in args[rows_pos]]))
                return fn(*args)
            return inner
        return orig(plan, assignment, fit_fn=wrap("fit", fit_fn, 0), candidates_fn=wrap("cand", candidates_fn, 1),
                    select_fn=wrap("select", select_fn, 1), evaluate_fn=wrap("eval", evaluate_fn, 1), **kw)
    monkeypatch.setattr(QW, "run_three_way", spy)
    rr = WF.evaluate_coin_fit("B", "BTC", rows, cf_plan)
    folds = [w for w in cf_plan["folds"]]
    assert [n for n, _ in seen] == ["fit", "cand", "select", "eval"] * len(folds)
    for k, w in enumerate(folds):
        (_f, fit_ts), (_c, cand_ts), (_s, sel_ts), (_e, ev_ts) = seen[4 * k: 4 * k + 4]
        assert fit_ts and all(w.train_start <= t < w.train_end for t in fit_ts + cand_ts)
        assert sel_ts and all(w.val_start <= t < w.val_end for t in sel_ts)
        assert ev_ts and all(w.test_start <= t < w.test_end for t in ev_ts)
    assert len(rr.selections) == len(folds)


def test_selection_never_reads_the_test_slice(cf_plan):
    rows = _coin_fit_world(cf_plan)
    rr = WF.evaluate_coin_fit("B", "BTC", rows, cf_plan)
    poisoned = []
    for r in rows:
        if any(w.test_start <= r["ts_ms"] < w.test_end for w in cf_plan["folds"]) and r["option"] == "TS_7_LS":
            r = dict(r, r={k: 25.0 for k in r["r"]}, pnl={k: 25.0 for k in r["r"]})
        poisoned.append(r)
    rp = WF.evaluate_coin_fit("B", "BTC", poisoned, cf_plan)
    first_test = cf_plan["folds"][0].test_start
    # test dilimi sonraki katmanların EĞİTİMİdir (çapalı); seçimin kendi katmanı dışında gördüğü veri yalnız geçmiş
    assert rp.selections[0] == rr.selections[0]
    assert all(r["ts_ms"] < first_test for r in rows if r["ts_ms"] < cf_plan["folds"][0].val_end)
    assert rr.selections[0]["selected"] == "TS_28_LO"                                      # coin'e özel avantaj seçilir


def test_shrinkage_score_pool_tie_and_adverse_scenario():
    k = WF.K_SHRINK
    assert k == RG.LIB_V1["thresholds"]["shrinkage"]["k_trades"] == 50 and WF.POOL_TIE_MARGIN_R == 0.02 and WF.TOP_K == 3
    st = {"c": [100, 30.0], "K": [200, 20.0], "P": [1000, 50.0]}
    r_p = 50.0 / 1000
    r_k = (20.0 + k * r_p) / (200 + k)
    assert WF.shrunk_score(st) == pytest.approx((30.0 + k * r_k) / (100 + k))
    assert WF.shrunk_score({"c": [0, 0.0], "K": [0, 0.0], "P": [0, 0.0]}) == 0.0
    # seçim ADVERSE ile: base'de iyi, ADVERSE'te kötü seçenek seçilmez
    plan = WF.plan_for(WF.grid("B"), T0, T0 + 2100 * DAY)
    rows = []
    rng = random.Random(4)
    for o, (base, adv) in {"TS_7_LO": (0.9, -0.3), "TS_14_LO": (0.3, 0.25)}.items():
        for c in COINS:
            for t in range(T0 + DAY, T0 + 2090 * DAY, 3 * DAY):
                e = rng.gauss(0, 0.05)
                rows.append(_row(t, opt=o, sym=c, r=adv + e, base=base + e))
    rr = WF.evaluate_coin_fit("B", "ETH", rows, plan)
    assert {s["selected"] for s in rr.selections} == {"TS_14_LO"}
    assert rr.means["base"] > rr.means["ADVERSE"] > rr.means["STRESS"]
    # havuzun seçimi: coin farkı < 0,02R ise havuzunki
    tie = []
    for o, coin_mu, pool_mu in (("TS_7_LO", 0.40, 0.20), ("TS_14_LO", 0.29, 0.30)):
        for c in COINS:
            for t in range(T0 + DAY, T0 + 2090 * DAY, 2 * DAY):
                tie.append(_row(t, opt=o, sym=c, r=coin_mu if c == "ETH" else pool_mu))
    sel = WF.evaluate_coin_fit("B", "ETH", tie, plan).selections
    assert all(s["best"] == "TS_7_LO" and s["pool_choice"] == "TS_14_LO" and s["selected"] == "TS_14_LO" for s in sel)


def test_cluster_coherence_and_option_matrix(cf_plan):
    rr = WF.evaluate_coin_fit("B", "BTC", _coin_fit_world(cf_plan), cf_plan)
    assert WF.cluster_of("BTC") == ["BTC", "ETH"] and rr.cluster_coherence_r is not None
    assert sorted(rr.option_daily) == sorted(WF.grid("B")) and len(WF.grid("B")) == 10
    assert all(sorted(v) == rr.oos_days for v in rr.option_daily.values())
    g = rr.to_gate()
    assert g["row_type"] == "COIN_FIT" and g["unit"] == "B" and g["scope"] == "COIN_FIT:BTC"


def test_select_rows_are_appended_once_through_trials(cf_plan, tmp_path):
    p = EnginePaths(data=tmp_path / "data", state=tmp_path / "state")
    led = TR.TrialsLedger(p)
    rows = _coin_fit_world(cf_plan)
    rr = WF.evaluate_coin_fit("B", "BTC", rows, cf_plan, ledger=led, at=AT)
    sel = [r for r in TR.iter_rows(p.trials_file) if r["kind"] == "SELECT"]
    assert len(sel) == len(rr.selections) == len(cf_plan["folds"])
    assert {r["row_id"] for r in sel} == {"B@COIN_FIT:BTC"} and sel[0]["params"]["selected"] == rr.selections[0]["selected"]
    assert led.counters["N_raw_cum"] == 0                                                  # seçim N'i büyütmez
    size = p.trials_file.stat().st_size
    WF.evaluate_coin_fit("B", "BTC", rows, cf_plan, ledger=TR.TrialsLedger(p), at=AT)       # aynı katman → DUPLICATE
    assert p.trials_file.stat().st_size == size
    assert TR.verify_chain(p.trials_file, expect=TR.read_hwm(p.trials_hwm_file)[0])["ok"]


# ============================================================================ maliyetler ve kapı dışı işlemler
def test_cost_scenarios_are_the_sealed_table():
    c = RG.WF_PROTOCOL_V1["thresholds"]["costs"]
    assert WF.SCENARIOS == ("base", "ADVERSE", "STRESS") == KN.SCENARIOS and WF.GATE_COST == "ADVERSE"
    for name in WF.SCENARIOS:
        sc = KN.scenario(name)
        assert (sc.taker_fee_pct, sc.slippage_bps, sc.spread_ticks) == (c[name]["taker_fee_pct"], c[name]["slippage_bps"],
                                                                         c[name]["spread_ticks"])
    assert RG.WF_PROTOCOL_V1["costs"]["scenarios"]["ADVERSE"]["gate"] is True


def test_gate_excluded_rows_never_reach_selection_or_statistics(cf_plan):
    rows = _coin_fit_world(cf_plan)
    base = WF.evaluate_coin_fit("B", "BTC", rows, cf_plan)
    flagged = [dict(r, r={k: 40.0 for k in r["r"]}, pnl={k: 40.0 for k in r["r"]}, flags=["PATH_1H_GAP"], excluded=True)
               for r in rows if r["option"] == "TS_84_LS"]
    other = [_row(r["ts_ms"] + 3_600_000, opt="TS_84_LS", sym=r["symbol"], r=40.0, flags=["F8_UNMEASURABLE"]) for r in
             rows[:200] if r["option"] == "TS_7_LO"]
    rr = WF.evaluate_coin_fit("B", "BTC", rows + other + flagged, cf_plan)
    assert rr.selections == base.selections and rr.trades == base.trades
    fx = WF.evaluate_fixed("TS_84_LS@POOLED_P23", "POOLED", "TS_84_LS", "POOLED_P23",
                           [r for r in rows + flagged if r["option"] == "TS_84_LS"], cf_plan)
    clean = WF.evaluate_fixed("TS_84_LS@POOLED_P23", "POOLED", "TS_84_LS", "POOLED_P23",
                              [r for r in rows if r["option"] == "TS_84_LS"], cf_plan)
    assert fx.trades == clean.trades and fx.means == clean.means
    assert fx.excluded["n"] == len(clean.trades) > 0 and fx.excluded["flags"] == {"PATH_1H_GAP": len(clean.trades)}
    assert fx.excluded["share"] == pytest.approx(0.5) and clean.excluded["n"] == 0
    assert set(KN.GATE_EXCLUDED_FLAGS) == {"FUNDING_GAP", "PATH_1H_GAP", "F8_UNMEASURABLE"}


# ============================================================================ aileler
def test_cover_family_uses_make_family_and_lockstep_and_equals_separate_runs(monkeypatch):
    calls = {"make_family": 0, "run_lockstep": 0}
    mf, rl = KN.make_family, KN.run_lockstep

    def spy_mf(base):
        calls["make_family"] += 1
        return mf(base)

    def spy_rl(*a, **k):
        calls["run_lockstep"] += 1
        return rl(*a, **k)
    monkeypatch.setattr(KN, "make_family", spy_mf)
    monkeypatch.setattr(KN, "run_lockstep", spy_rl)
    d = S.d4_world(22, n=500)
    res, sig = WF.run_cover_family("D4", d)
    assert calls == {"make_family": 1, "run_lockstep": 1}
    assert sorted(res) == sorted(["K_D4", *KN.covers_of("D4")])
    for vid, r in res.items():
        assert json.dumps(r.to_dict(), sort_keys=True) == json.dumps(KN.run(KN.make_variant(vid), d).to_dict(),
                                                                     sort_keys=True), vid
        assert {t.entry_ts for t in r.trades} <= sig[vid]
    res2, _ = WF.run_variants(["DON_20_10_4h_LS", "MAX_20_50_4h_LO"], d)
    assert calls["run_lockstep"] == 2
    assert json.dumps(res2["DON_20_10_4h_LS"].to_dict(), sort_keys=True) == json.dumps(
        KN.run(KN.make_variant("DON_20_10_4h_LS"), d).to_dict(), sort_keys=True)


# ============================================================================ uçtan uca (çekirdek → WF → plasebo → Kapı A)
def test_end_to_end_kernel_to_gate_a_look(tmp_path):
    """Sentetik dünyalarda B ailesi: 3 coin × 10 seçenek çekirdekte (`run_lockstep`), `COIN_FIT:BTC` üç yollu seçim
    (`SELECT` satırları), sabit `DON_20_10_1d_LS@POOLED_P23` satırı, çekirdek plaseboları, Kapı A bakışı (`LOOK_A` 856
    satır: LIB 818 + S1d AG 38, P3g). Bu dünyalarda dikilmiş avantaj yok → hüküm FAIL_A beklenir; ölçülen süre raporlanır."""
    t0 = time.perf_counter()
    worlds = {c: dataclasses.replace(S.trend_world(60 + i, n=1600), symbol=c) for i, c in enumerate(("BTC", "ETH", "SOL"))}
    opts = WF.grid("B")
    rows, sig = [], {}
    don = []
    for c, d in worlds.items():
        start = WF.data_start_ms(d.bars["1d"][0]["timestamp"], "1d")
        res, s = WF.run_variants(opts + ["DON_20_10_1d_LS"], d, start_ms=start)
        sig[c] = s
        tr = WF.trade_rows(res.values(), d)                                             # + günlük piyasa değerlemesi
        assert all(r["mtm_method"] == ST.MTM_PRICE_PATH for r in tr)
        assert all(abs(sum(r["mtm"].values()) - r["pnl"]["ADVERSE"]) < 1e-9 for r in tr)
        rows += [r for r in tr if r["option"] in opts]
        don += [r for r in tr if r["option"] == "DON_20_10_1d_LS"]
    t_kernel = time.perf_counter() - t0
    end = max(int(d.bars["1d"][-1]["timestamp"]) for d in worlds.values()) + DAY
    p = EnginePaths(data=tmp_path / "data", state=tmp_path / "state")
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    mod = {v["variant_id"]: hashlib.sha256(v["variant_id"].encode()).hexdigest() for v in RG.LIB_V1["variants"]}
    led.append([dict(e, at=AT) for e in TR.seal_entries(mod)])                         # N = 4.513
    from tradingbot.research_engine import lib_ag as LA
    led.append([dict(e, at=AT) for e in LA.seal_entries({v: hashlib.sha256(v.encode()).hexdigest()
                                                         for v in LA.variant_ids()})])  # + AG 38 → 4.551 (kapı şartı)
    plan_b = WF.plan_for(opts, WF.data_start_ms(S.T0, "1d"), end)
    cf = WF.evaluate_coin_fit("B", "BTC", rows, plan_b, ledger=led, at=AT)
    assert cf.selections and all(s["selected"] in opts for s in cf.selections)
    plan_d = WF.plan_for(["DON_20_10_1d_LS"], WF.data_start_ms(S.T0, "1d"), end)
    fx = WF.evaluate_fixed("DON_20_10_1d_LS@POOLED_P23", "POOLED", "DON_20_10_1d_LS", "POOLED_P23", don, plan_d)
    assert fx.excluded["n_leak_folds"] == 0 and all(t["r"]["base"] >= t["r"]["ADVERSE"] >= t["r"]["STRESS"] for t in don)
    placebos = []
    for c, d in worlds.items():
        kp = PL.KernelPlacebo(KN.make_variant("DON_20_10_1d_LS"), d)
        placebos += kp.for_row(fx.row_id, [t for t in fx.trades if t["symbol"] == c],
                               [t for t in don if t["symbol"] == c], sig[c]["DON_20_10_1d_LS"])
    assert len(placebos) == 5 * len(fx.trades)
    rows_gate = {cf.row_id: cf.to_gate(), fx.row_id: fx.to_gate()}
    res = ST.gate_a_look(led, date(2026, 12, 6), rows_gate, placebo={fx.row_id: placebos},
                         fwd={fx.row_id: {"days": 61, "n": 25, "mean_adverse": 0.1}}, at=AT,
                         clock=lambda: datetime(2026, 12, 6, 1, 37, tzinfo=timezone.utc))
    elapsed = time.perf_counter() - t0
    assert res["status"] == "LOOK" and res["N"] == 4551
    ev = res["evaluation"]["rows"]
    assert ev[fx.row_id]["verdict"] == ev[cf.row_id]["verdict"] == "FAIL_A"
    assert ev[fx.row_id]["placebo_diff_ci"] is not None and ev[fx.row_id]["placebo_diff_ci_gross"] is not None
    assert ev[fx.row_id]["pbo_state"] == ST.GROUP_INCOMPLETE and "PBO_GROUP_INCOMPLETE" in ev[fx.row_id]["fails"]
    assert ev[cf.row_id]["pbo_group"] == "GRID:B@COIN_FIT:BTC" and ev[cf.row_id]["pbo_state"] != ST.GROUP_INCOMPLETE
    assert all("GROSS" in t["r"] for t in don) and all(t["r"]["GROSS"] >= t["r"]["base"] - 1e-9 or t["r"]["GROSS"] < 0
                                                       for t in don)
    kinds = [r["kind"] for r in TR.iter_rows(p.trials_file)]
    assert kinds.count("SELECT") == len(cf.selections) and kinds.count("LOOK_A") == 856 and kinds.count("FWD_READ") == 1
    print(f"\n[P3d] uçtan uca sentetik Kapı A (3 coin × 11 varyant çekirdek {t_kernel:.1f} sn; toplam {elapsed:.1f} sn)")
