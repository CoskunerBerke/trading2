# -*- coding: utf-8 -*-
"""P3d — plasebo (docs/ENGINE_BRAIN_V1.md §3.6, §8.1 P3d "plasebo tohum belirlenimciliği"; mühür `STATS_V1.placebo`, B6).
Sentetik dünyalar, ağ yok."""
from __future__ import annotations

import bisect
import dataclasses
import hashlib
import json
import random

import pytest

from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import placebo as PL
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import synthetic as S
from tradingbot.research_engine import wf as WF

DAY = 86_400_000


def test_placebo_seed_is_the_sealed_b6_format():
    sealed = RG.STATS_V1["placebo"]["seed_int"]
    assert sealed["seed_int"] == "int(sha256(f'{row_id}|{trade_key}|{k}'.encode('utf-8')).hexdigest()[:16], 16)"
    assert sealed["rng"] == "random.Random(seed_int)" and PL.K_PER_TRADE == 5
    for rid, key, k in (("TS_7_LO@POOLED_P23", "TS_7_LO|BTC:1:LONG|2024-01-01T00:00:00+00:00", 0), ("x", "y", 4)):
        assert PL.placebo_seed(rid, key, k) == int(hashlib.sha256(f"{rid}|{key}|{k}".encode("utf-8")).hexdigest()[:16], 16)


def test_draws_are_deterministic_per_k_and_rejections_redraw_from_the_same_stream():
    elig = list(range(1000, 2000, 10))
    calls: list[int] = []

    def sim(t: int):
        calls.append(t)
        return None if t % 30 == 0 else {"day": t // DAY, "r": {"ADVERSE": 0.0}}
    a = PL.draw_placebos("R@S", "key", elig, sim)
    first_calls = list(calls)
    b = PL.draw_placebos("R@S", "key", elig, sim)
    assert a == b and calls == first_calls * 2
    # başvuru: k başına random.Random(B6), ret → aynı akıştan yeniden çekim
    for k, p in enumerate(a):
        rng = random.Random(PL.placebo_seed("R@S", "key", k))
        while True:
            t = elig[rng.randrange(len(elig))]
            if t % 30:
                break
        assert p["decision_ms"] == t and p["k"] == k and not p["missing"]
    assert len({p["decision_ms"] for p in a}) > 1                                       # k akışları ayrı
    assert PL.draw_placebos("R@S", "other", elig, sim) != a
    miss = PL.draw_placebos("R@S", "key", elig, lambda t: None)
    assert all(p["missing"] and p["tries"] == PL.MAX_TRIES for p in miss) and PL.gate_pairs(miss) == []
    assert all(p["missing"] for p in PL.draw_placebos("R@S", "key", [], sim))


def test_eligible_times_exclude_signals_real_holds_and_other_windows():
    dec = list(range(0, 100))
    out = PL.eligible_times(dec, 10, 60, signal_times={12, 13, 40}, real_spans=[(30, 3), (55, 1)])
    assert all(10 <= t < 60 for t in out)
    assert not {12, 13, 40} & set(out)
    assert not set(range(27, 34)) & set(out) and not {54, 55, 56} & set(out)
    assert {11, 14, 26, 34, 39, 41, 53, 57} <= set(out)


@pytest.fixture(scope="module")
def ts_world():
    d = dataclasses.replace(S.trend_world(31, n=1000), symbol="BTC")
    start = WF.data_start_ms(d.bars["1d"][0]["timestamp"], "1d")
    res, sig = WF.run_variants(["TS_7_LO", "TS_14_LS", "DON_20_10_1d_LS"], d, start_ms=start)
    return d, start, res, sig


def test_signal_recorder_does_not_change_kernel_results(ts_world):
    d, start, res, sig = ts_world
    for vid in ("TS_7_LO", "TS_14_LS", "DON_20_10_1d_LS"):
        plain = KN.run(KN.make_variant(vid), d, start_ms=start)
        assert json.dumps(res[vid].to_dict(), sort_keys=True) == json.dumps(plain.to_dict(), sort_keys=True)
        # her gerçek girişin karar anı bir sinyal anıdır (giriş = sinyal barının kapanışı + bir sonraki açılış)
        assert {t.entry_ts for t in res[vid].trades} <= sig[vid]
        assert len(sig[vid]) >= len(res[vid].trades)


def test_kernel_placebos_are_deterministic_and_match_side_hold_and_atr_stop(ts_world):
    d, start, res, sig = ts_world
    vid = "DON_20_10_1d_LS"
    rows = WF.trade_rows([res[vid]])
    end = int(d.bars["1d"][-1]["timestamp"])
    win = [start + 200 * DAY, end]
    real = [dict(r, test_window=win) for r in rows if win[0] <= r["ts_ms"] < win[1]][:12]
    assert {r["side"] for r in real} == {"LONG", "SHORT"}
    assert len(real) >= 6
    kp = PL.KernelPlacebo(KN.make_variant(vid), d)
    a = kp.for_row(f"{vid}@POOLED_P23", real, rows, sig[vid])
    b = PL.KernelPlacebo(KN.make_variant(vid), d).for_row(f"{vid}@POOLED_P23", real, rows, sig[vid])
    assert a == b and len(a) == 5 * len(real)
    by_key = {r["trade_key"]: r for r in real}
    spans = [(r["ts_ms"], max(r["exit_ms"] - r["ts_ms"], DAY)) for r in rows]
    for p in a:
        assert not p["missing"]
        tr = by_key[p["trade_key"]]
        assert p["side"] == tr["side"]                                                   # aynı yön
        assert p["max_hold_bars"] == max(1, tr["bars_held"] - 1)                         # aynı tutuş (zaman stopu)
        # R5 maruziyet eşleşmesi: plasebo yalnız zamanla, gerçek işlemin tutuşunda çıkar; ATR katı stop R birimidir
        assert p["exit_kind"] == "TIME" and p["bars_held"] == tr["bars_held"]          # tutuş veri sonuna sığar
        assert p["stop_mult"] == pytest.approx(kp.stop_mult(tr))                         # aynı ATR katı
        long = p["side"] == "LONG"
        assert (p["guard_stop"] < p["unit_stop"]) if long else (p["guard_stop"] > p["unit_stop"])
        assert set(p["r"]) == {"base", "ADVERSE", "STRESS", PL.GROSS}
        assert p["r"]["GROSS"] >= p["r"]["base"] >= p["r"]["ADVERSE"] >= p["r"]["STRESS"]
        t = p["decision_ms"]
        assert win[0] <= t < win[1] and t not in sig[vid]                                # aynı katman, sinyalsiz bar
        assert all(abs(t - e) > h for e, h in spans)                                     # gerçek işlemlerin ± tutuşu hariç
    # plasebo girişinin stopu = kendi sinyal kapanışı ∓ aynı kat × kendi ATR14'ü
    p = a[0]
    i = bisect.bisect_left(kp.ts, p["decision_ms"] - kp.step)
    assert kp.ts[i] + kp.step == p["decision_ms"]
    # tohum değişince çekimler değişir (satır kimliği tohumdadır)
    c = kp.for_row(f"{vid}@CL_MAJ", real, rows, sig[vid])
    assert [x["decision_ms"] for x in c] != [x["decision_ms"] for x in a]


def test_diff_ci_is_deterministic_and_day_clustered():
    rng = random.Random(1)
    real = [(d, rng.gauss(0.5, 1.0)) for d in range(60) for _ in range(2)]
    plac = [(d, rng.gauss(0.0, 1.0)) for d in range(60) for _ in range(10)]
    ci = PL.diff_ci(real, plac, row_id="R", look_no=1)
    assert ci == PL.diff_ci(list(reversed(real)), plac, row_id="R", look_no=1)
    assert ci[0] > 0 and ci[0] < 0.5 < ci[1]
    assert PL.diff_ci(real, plac, row_id="R", look_no=2) != ci                          # tohum row_id|look_no
    same = PL.diff_ci(plac, plac, row_id="R", look_no=1)
    assert same[0] < 0 < same[1]
    assert PL.diff_ci(real[:8], plac, row_id="R", look_no=1) is None                    # < 5 gün → CI yok
    assert RG.STATS_V1["thresholds"]["placebo"]["ci_level"] == 0.95


def test_always_in_market_rule_has_no_eligible_placebo_bar_and_fails_closed(ts_world):
    """Mühürlü tanımın sonucu (rapor edilir): ters dönen, hep piyasada olan kuralda (TS_*_LS) sinyalsiz ve gerçek
    işlemlerin ± tutuşu dışında bar kalmaz → plasebo yok → kapı geçmez (kapalı hata; `stats` PLACEBO)."""
    d, start, res, sig = ts_world
    vid = "TS_14_LS"
    rows = WF.trade_rows([res[vid]])
    kp = PL.KernelPlacebo(KN.make_variant(vid), d)
    end = int(d.bars["1d"][-1]["timestamp"])
    spans = [(r["ts_ms"], max(r["exit_ms"] - r["ts_ms"], DAY)) for r in rows]
    assert PL.eligible_times(kp.decisions, start, end, sig[vid], spans) == []
    real = [dict(r, test_window=[start, end]) for r in rows[:3]]
    out = kp.for_row(f"{vid}@POOLED_P23", real, rows, sig[vid])
    assert all(p["missing"] for p in out) and PL.gate_pairs(out) == []


def test_gross_r_coverage_and_r_unit_rescaling(ts_world):
    """R5: brüt R = aynı yol, ücret/kayma/spread sıfır (fonlama gerçek); kapsama = en az bir kapı-içi plasebosu olan
    gerçek işlemlerin payı; R'ın ATR katı stopa göre yeniden ölçülmesi aynı bacaklarla kesindir."""
    d, _start, res, _sig = ts_world
    t = res["DON_20_10_1d_LS"].trades[0]
    g = PL.gross_r(t)
    assert g >= t.r["base"] >= t.r["ADVERSE"] and g == KN.cost_trade(t, KN.CostScenario("G", 0, 0, 0), 0.0)[0]
    far = dataclasses.replace(t, initial_stop=t.initial_stop * (0.5 if t.side == "LONG" else 1.5))
    for sc in ("base", "ADVERSE", "STRESS"):
        r_near = KN.cost_trade(t, KN.scenario(sc), d.price_tick)[0]
        r_far = KN.cost_trade(far, KN.scenario(sc), d.price_tick)[0]
        assert r_near == pytest.approx(KN.cost_trade(dataclasses.replace(far, initial_stop=t.initial_stop),
                                                     KN.scenario(sc), d.price_tick)[0])
        assert abs(r_far) <= abs(r_near) + 1e-12
    real = [{"trade_key": k} for k in ("a", "b", "c", "d", "e")]
    pls = [{"trade_key": "a", "day": 1, "r": {}}, {"trade_key": "b", "missing": True},
           {"trade_key": "c", "day": 1, "r": {}, "excluded": True}, {"trade_key": "d", "day": 2, "r": {}}]
    cov, share = PL.coverage(real, pls)
    assert [x["trade_key"] for x in cov] == ["a", "d"] and share == 0.4 and PL.MIN_COVERAGE == 0.8
    assert PL.coverage([], pls) == ([], 0.0)
