# -*- coding: utf-8 -*-
"""P3d — istatistik ve Kapı A bakışı (docs/ENGINE_BRAIN_V1.md §3.6, §3.7, §8.1 P3d; mühür `STATS_V1`). Sentetik dünyalar
(`research_engine/synthetic.py`), ağ yok.

Kabul: `p_day` başvuru uygulamasıyla eşit; BH; DSR ampirik `sr_var`; dikilmiş avantaj Kapı A'yı geçer, 200 boş dünyada
geçme ≤ q; Kapı A yalnız bakış gecesinde (özellik); kapı dışı işlemler çıkar ve sayılır; deneme defteri yalnız
`trials.py` ile büyür.
"""
from __future__ import annotations

import ast
import hashlib
import random
import time
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from tradingbot import validation as VAL
from tradingbot.research_engine import placebo as PL
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import stats as ST
from tradingbot.research_engine import synthetic as S
from tradingbot.research_engine import trials as TR
from tradingbot.research_engine import wf as WF
from tradingbot.research_engine.paths import EnginePaths

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "tradingbot" / "research_engine"
AT = "2026-12-06T01:37:00+00:00"
PLANTED = "DON_55_20_1d_LS@POOLED_P23"


# ============================================================================ p_day — başvuru uygulaması
def ref_p_day(trades, row_id: str, look_no: int, b: int = 10_000):
    """Tanımın düz okunuşu (testte yazıldı; modülden bağımsız): R mikro-R'ye yuvarlanır, günler `random.Random(B6 tohumu)
    .choices` ile yeniden örneklenir, kaydırılmış tekrar ortalaması (kesirli, tam) gözlenen ortalamayla karşılaştırılır."""
    acc: dict[int, list[int]] = {}
    for d, r in trades:
        a = acc.setdefault(int(d), [0, 0])
        a[0] += int(round(float(r) * 1_000_000))
        a[1] += 1
    days = sorted(acc)
    if len(days) < 2:
        return None
    s = [acc[d][0] for d in days]
    n = [acc[d][1] for d in days]
    m = Fraction(sum(s), sum(n))
    rng = random.Random(int(hashlib.sha256(f"{row_id}|{look_no}".encode("utf-8")).hexdigest()[:16], 16))
    cnt = 0
    for _ in range(b):
        idx = rng.choices(range(len(days)), k=len(days))
        mean = Fraction(sum(s[i] for i in idx), sum(n[i] for i in idx))
        cnt += (mean - m) >= m
    return (1 + cnt) / (b + 1)


def _trades(seed: int, n_days: int, mu: float, sd: float = 1.0) -> list[tuple[int, float]]:
    rng = random.Random(seed)
    out = []
    for d in range(n_days):
        for _ in range(rng.randint(1, 3)):
            out.append((20000 + d, rng.gauss(mu, sd)))
    return out


@pytest.mark.parametrize("seed, n_days, mu, look_no", [(1, 40, 0.25, 1), (2, 35, 0.0, 1), (3, 30, -0.2, 2), (4, 50, 0.1, 7),
                                                        (5, 2, 0.5, 1), (6, 25, 0.05, 3)])
def test_p_day_equals_the_reference_implementation(seed, n_days, mu, look_no):
    tr = _trades(seed, n_days, mu)
    rid = f"ROW_{seed}@POOLED_P23"
    assert ST.p_day(tr, row_id=rid, look_no=look_no) == ref_p_day(tr, rid, look_no)


def test_p_day_edge_cases_and_seal():
    assert ST.RESAMPLES == RG.STATS_V1["thresholds"]["p_day"]["resamples"] == 10_000
    assert ST.CHUNK == 1000 and ST.MEMORY_MB_MAX == 16 and ST.GATE_COST == "ADVERSE"
    assert ST.p_day([], row_id="x", look_no=1) is None
    assert ST.p_day([(5, 1.0), (5, 2.0)], row_id="x", look_no=1) is None              # tek gün
    same = [(d, 0.3) for d in range(10)]
    assert ST.p_day(same, row_id="x", look_no=1) == 1 / 10_001                         # sabit pozitif: hiç aşım yok
    assert ST.p_day([(d, 0.0) for d in range(10)], row_id="x", look_no=1) == 1.0       # sıfır ortalama: hep eşit
    tr = _trades(9, 30, 0.1)
    assert ST.p_day(tr, row_id="r", look_no=1) != ST.p_day(tr, row_id="r", look_no=2)  # tohum = row_id|look_no
    assert ST.p_day(tr, row_id="r", look_no=1) == ST.p_day(list(reversed(tr)), row_id="r", look_no=1)
    with pytest.raises(ValueError):
        ST.p_day([(1, float("nan")), (2, 1.0)], row_id="r", look_no=1)


def test_seed_and_stream_are_random_random_draw_for_draw():
    for seed in (0, 1, 2 ** 32 - 1, 2 ** 32, ST.p_day_seed("K_D4@POOLED_OWN", 3)):
        for d in (1, 2, 37, 1000):
            rng = random.Random(seed)
            want = [rng.choices(range(d), k=d) for _ in range(23)]
            got = np.concatenate(list(ST.choice_stream(seed, d, 23, rows=5))).tolist()
            assert got == want, (seed, d)
    assert ST.p_day_seed("a", 1) == int(hashlib.sha256(b"a|1").hexdigest()[:16], 16)


def test_p_day_chunking_memory_and_overflow_path(monkeypatch):
    for d in (10, 300, 5000, 50_000):
        rows = ST._block_rows(d, 3)
        assert 1 <= rows <= ST.CHUNK and rows * d * 8 * 3 <= ST.MEMORY_MB_MAX * 1024 * 1024 // 2 or rows == 1
    tr = _trades(12, 45, 0.15)
    fast = ST.p_day(tr, row_id="ov", look_no=1)
    monkeypatch.setattr(ST, "_I62", 0)                                                  # Python tam sayı yolu
    assert ST.p_day(tr, row_id="ov", look_no=1) == fast


def test_p_day_stop_above_is_exact_about_the_bound():
    tr = _trades(13, 40, 0.02)
    p = ST.p_day(tr, row_id="cap", look_no=1)
    assert ST.p_day(tr, row_id="cap", look_no=1, stop_above=p) == p                   # sınırda durmaz
    assert ST.p_day(tr, row_id="cap", look_no=1, stop_above=p * 0.5) == float("inf")


def test_p_day_is_calibrated_under_the_null():
    """Sıfır ortalamalı işlemlerde p ≈ düzgün: p ≤ 0,05 oranı 200 dünyada [0,01, 0,10]."""
    ps = [ST.p_day(_trades(100 + s, 40, 0.0), row_id=f"N{s}", look_no=1) for s in range(200)]
    rate = sum(p <= 0.05 for p in ps) / len(ps)
    assert 0.01 <= rate <= 0.10, rate


# ============================================================================ BH
def _step_up(pvals: dict[str, float], alpha: float) -> set[str]:
    items = sorted(pvals.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(items)
    k = max([i + 1 for i, (_k, p) in enumerate(items) if p <= alpha * (i + 1) / m], default=0)
    return {kk for kk, _p in items[:k]}


def test_bh_qvalues_equal_the_step_up_procedure():
    rng = random.Random(5)
    for trial in range(200):
        m = rng.randint(1, 40)
        ps = {f"r{i}": (rng.random() ** 3 if rng.random() < 0.7 else 1.0) for i in range(m)}
        q = ST.bh_qvalues(ps)
        for alpha in (0.01, 0.05, 0.1, 0.2):
            assert {k for k, v in q.items() if v <= alpha} == _step_up(ps, alpha), (trial, alpha)
    assert ST.bh_qvalues({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.005}) == pytest.approx(
        {"d": 0.02, "a": 0.02, "c": 0.04, "b": 0.04})


def test_bh_missing_p_enters_as_one_and_m_never_shrinks():
    q_small = ST.bh_qvalues({"a": 0.001, "b": 0.5})
    q_big = ST.bh_qvalues({"a": 0.001, "b": 0.5, "c": None, "d": None})
    assert q_big["c"] == q_big["d"] == 1.0 and q_big["a"] == pytest.approx(0.004) and q_big["a"] > q_small["a"]
    assert ST.BH_Q == RG.STATS_V1["thresholds"]["bh_q"] == 0.1
    ev = ST.gate_a_evaluate({}, look_no=1, n_trials=4513)
    assert ev["family_m"] == len(RG.gate_a_rows()) == 818                                # aile = bütün LIB satırları


# ============================================================================ DSR
def test_dsr_uses_the_family_sr_variance_with_the_1_over_t_minus_1_floor():
    t = 900
    srs = [0.01, -0.02, 0.03, 0.15, 0.0, -0.01]
    var = ST.family_sr_var(srs)
    assert var == pytest.approx(float(np.var(srs, ddof=1)))
    assert ST.sr_var_floored(var, t) == var > 1 / (t - 1)
    assert ST.sr_var_floored(1e-6, t) == 1 / (t - 1)                                    # taban
    assert ST.sr_var_floored(None, t) == 1 / (t - 1)
    assert ST.family_sr_var([0.2, None]) is None
    for v in (var, 1 / (t - 1)):
        d = ST.dsr(0.2, n_trials=4513, t=t, skew=0.3, kurt=5.0, sr_var=v)
        assert d == VAL.deflated_sharpe(0.2, n_trials=4513, T=t, skew=0.3, kurt=5.0, sr_var=v)
    # fonksiyonun 0,01 varsayılan tabanı KULLANILMAZ (günlük SR için yıllık ≈ 7 Sharpe eşiği demekti)
    floor_only = ST.dsr(0.2, n_trials=4513, t=t, skew=0.0, kurt=3.0, sr_var=1 / (t - 1))
    assert floor_only > VAL.deflated_sharpe(0.2, n_trials=4513, T=t, skew=0.0, kurt=3.0) + 0.5
    assert ST.dsr(0.2, n_trials=10, t=t, skew=0, kurt=3, sr_var=var) > ST.dsr(0.2, n_trials=4513, t=t, skew=0, kurt=3,
                                                                               sr_var=var)
    assert ST.dsr(None, n_trials=10, t=t, skew=0, kurt=3, sr_var=var) is None


def test_daily_returns_sharpe_and_moments():
    dr = ST.daily_returns([(10, 2.0), (10, -1.0), (12, 4.0)], [10, 11, 12, 13])
    assert dr == {10: 1.0 / 200, 11: 0.0, 12: 4.0 / 200, 13: 0.0}                       # 200 USDT taban, işlemsiz gün 0
    with pytest.raises(ValueError):
        ST.daily_returns([(99, 1.0)], [10, 11])
    x = [0.01, -0.02, 0.03, 0.0, 0.0]
    assert ST.sharpe(x) == pytest.approx(np.mean(x) / np.std(x, ddof=1))
    assert ST.sharpe([0.0, 0.0]) is None and ST.sharpe([1.0]) is None
    sk, ku = ST.moments([0.0] * 20 + [1.0])
    assert sk > 0 and ku > 3 and ST.moments([0.0, 0.0]) == (0.0, 3.0)


# ============================================================================ Kapı A dünyaları
def gate_world_rows(world: dict, *, full: bool = True):
    vs = RG.variants_by_id()
    rows, pl, fwd = {}, {}, {}
    for r in world["rows"]:
        tf = vs[r["unit"]]["signal_tf"]
        plan = WF.plan_for([r["unit"]], WF.data_start_ms(world["first_ms"], tf), world["end_ms"])
        rr = WF.evaluate_fixed(r["row_id"], "POOLED", r["unit"], r["scope"], world["trades"][r["row_id"]], plan)
        rows[r["row_id"]] = rr.to_gate()
        pl[r["row_id"]] = (lambda rid=r["row_id"], tr=rr.trades: PL.gate_pairs(S.gate_world_placebos(world, rid, tr)))
        fwd[r["row_id"]] = S.gate_world_fwd(world, r["row_id"])
    return rows, pl, fwd


def evaluate_world(world: dict, *, full: bool, n_trials: int = 4513):
    rows, pl, fwd = gate_world_rows(world)
    return ST.gate_a_evaluate(rows, look_no=1, n_trials=n_trials, placebo=pl, fwd=fwd, full=full)


@pytest.fixture(scope="module")
def sealed_ledger(tmp_path_factory):
    """Öncüller (2.775) + LIB_v1 mühür satırları (1.738) → DSR N = 4.513 (sahte modül sha'larıyla; trials testi gibi)."""
    p = EnginePaths(data=tmp_path_factory.mktemp("p3d") / "data", state=tmp_path_factory.mktemp("p3d_state"))
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    mod = {v["variant_id"]: hashlib.sha256(v["variant_id"].encode()).hexdigest() for v in RG.LIB_V1["variants"]}
    led.append([dict(e, at=AT) for e in TR.seal_entries(mod)])
    assert led.dsr_n() == 4513
    return p


def test_planted_edge_passes_gate_a_on_the_look_night(sealed_ledger):
    """Dikilmiş avantaj: 29 gerçek havuz satırının (B, C, D, E) birinde geleceği gören yön → BH (m = 818), DSR (N = 4.513,
    ampirik sr_var), PBO, plasebo, katman payı, OOS büyüklüğü, ileri okuma, tek enstrüman payı — hepsi geçer; diğerleri
    geçmez. Bakış deftere `LOOK_A` (+ `FWD_READ`) olarak yalnız `TrialsLedger.append` ile yazılır."""
    world = S.gate_world(1, planted=PLANTED)
    rows, pl, fwd = gate_world_rows(world)
    led = TR.TrialsLedger(sealed_ledger)
    t0 = time.perf_counter()
    res = ST.gate_a_look(led, date(2026, 12, 6), rows, placebo=pl, fwd=fwd, at=AT)
    elapsed = time.perf_counter() - t0
    assert res["status"] == "LOOK" and res["look_no"] == 1 and res["N"] == 4513
    ev = res["evaluation"]["rows"]
    passed = sorted(k for k, v in ev.items() if v["verdict"] == ST.PASS_A)
    assert passed == [PLANTED], {k: v["fails"] for k, v in ev.items() if k == PLANTED}
    s = ev[PLANTED]
    assert s["q_bh"] <= 0.1 and s["dsr"] >= 0.95 and s["pbo"] < 0.25 and s["placebo_diff_ci"][0] > 0
    assert s["n"] >= 100 and s["n_days"] >= 730 and s["pos_fold_share"] >= 0.6 and s["top_share"] <= 0.5
    rows_written = [r for r in TR.iter_rows(sealed_ledger.trials_file) if r["kind"] in ("LOOK_A", "FWD_READ")]
    look = {r["row_id"]: r for r in rows_written if r["kind"] == "LOOK_A"}
    assert len(look) == 818 and look[PLANTED]["verdict"] == "PASS_A" and look[PLANTED]["look_no"] == 1
    assert look[PLANTED]["params"]["N"] == 4513
    assert look[PLANTED]["params"]["thresholds_source"].startswith("PROVISIONAL_UNSEALED")
    assert sum(r["kind"] == "FWD_READ" for r in rows_written) == 29
    assert look["K_D4@POOLED_OWN"]["params"]["fails"] == ["NOT_EVALUATED"] and look["K_D4@POOLED_OWN"]["q_bh"] == 1.0
    assert TR.verify_chain(sealed_ledger.trials_file, expect=TR.read_hwm(sealed_ledger.trials_hwm_file)[0])["ok"]
    assert led.dsr_n() == 4513                                                          # bakışlar N'i büyütmez
    print(f"\n[P3d] tam sentetik Kapı A bakışı (29 satır, aile 818, plasebo + CSCV + DSR + defter): {elapsed:.1f} sn")
    again = ST.gate_a_look(TR.TrialsLedger(sealed_ledger), date(2026, 12, 7), rows, placebo=pl, fwd=fwd, at=AT)
    assert again["status"] == "INTERIM" and "verdict" not in str(again)                # aynı ayda ikinci bakış yok


@pytest.mark.slow
def test_200_null_worlds_pass_at_most_q():
    """Boş dünya (aynı satır yapısı, rastgele yürüyüş, yazı-tura yön): Kapı A'yı geçen DÜNYA oranı ≤ q (satır oranı
    raporlanır). Hızlı kip (`full=False`) hüküm-eşdeğerdir (ayrı test)."""
    n = ST.NULL_WORLDS
    assert n == 200
    world_pass = row_pass = rows_total = 0
    t0 = time.perf_counter()
    for s in range(n):
        ev = evaluate_world(S.gate_world(10_000 + s), full=False)
        k = sum(v["verdict"] == ST.PASS_A for v in ev["rows"].values())
        world_pass += k > 0
        row_pass += k
        rows_total += len(ev["rows"])
    rate = world_pass / n
    print(f"\n[P3d] boş dünya: {world_pass}/{n} dünya ({rate:.3f}), {row_pass}/{rows_total} satır geçti; "
          f"{time.perf_counter() - t0:.0f} sn")
    assert rate <= ST.BH_Q


def test_fast_mode_gives_the_same_verdicts():
    for world in (S.gate_world(1, planted=PLANTED), S.gate_world(78, planted="TS_7_LO@POOLED_P23", edge=0.6)):
        full = evaluate_world(world, full=True)["rows"]
        fast = evaluate_world(world, full=False)["rows"]
        assert {k: v["verdict"] for k, v in full.items()} == {k: v["verdict"] for k, v in fast.items()}
        for k, v in full.items():
            if v["verdict"] == ST.PASS_A:
                assert fast[k]["p_day"] == v["p_day"] and fast[k]["placebo_diff_ci"] == v["placebo_diff_ci"]


def test_gate_excluded_trades_are_dropped_from_statistics_and_counted():
    world = S.gate_world(5, planted=PLANTED, flag_rate=0.05)
    rows, _pl, _fwd = gate_world_rows(world)
    r = rows[PLANTED]
    assert r["excluded"]["n"] > 0 and r["excluded"]["flags"] == {"FUNDING_GAP": r["excluded"]["n"]}
    assert all(not t["excluded"] for t in r["trades"])
    base = ST.row_stats(r, look_no=1)
    # kapı dışı işlemlerin R'ı ne olursa olsun istatistik aynı
    for t in world["trades"][PLANTED]:
        if t["excluded"]:
            t["r"] = {k: 50.0 for k in t["r"]}
            t["pnl"] = dict(t["r"])
    rows2, _p, _f = gate_world_rows(world)
    again = ST.row_stats(rows2[PLANTED], look_no=1)
    assert again["p_day"] == base["p_day"] and again["mean_r_adverse"] == base["mean_r_adverse"]
    assert again["sr"] == base["sr"] and rows2[PLANTED]["excluded"] == r["excluded"]


# ============================================================================ bakış takvimi (özellik)
def test_look_night_rule():
    assert not ST.is_look_night(date(2026, 12, 5), [])
    assert ST.is_look_night(date(2026, 12, 6), [])
    assert not ST.is_look_night(date(2026, 12, 6), ["2026-12"])
    assert ST.is_look_night(date(2026, 12, 9), ["2026-11"])                            # 6'sı kaçtıysa ilk gece
    assert RG.STATS_V1["looks_tr"].startswith("Kapı A yalnız aylık kayıtlı bakışta (her ayın 5'inden sonraki ilk gece)")


def _tiny_rows():
    world = S.gate_world(3, planted=PLANTED, rows=[r for r in S.gate_rows(("B",)) if r["unit"] in ("TS_28_LS", "TS_7_LO")]
                           + [r for r in S.gate_rows(("D",)) if r["unit"] == "DON_55_20_1d_LS"])
    rows, pl, fwd = gate_world_rows(world)
    return world, rows, {k: v() for k, v in pl.items()}, fwd


def test_gate_a_is_evaluated_only_on_look_nights_property(tmp_path):
    """Özellik: rastgele gece dizilerinde hüküm (`PASS_A`/`FAIL_A`) ve `LOOK_A` yalnız bakış gecesinde; ara gecede defter
    bayt-aynı, sonuç "ara görünüm"; her ay en çok bir bakış; `look_no` ardışık."""
    _world, rows, pl, fwd = _tiny_rows()
    fam = sorted(rows)
    rng = random.Random(42)
    for trial in range(2):
        p = EnginePaths(data=tmp_path / f"d{trial}" / "data", state=tmp_path / f"d{trial}" / "state")
        led = TR.TrialsLedger(p)
        led.import_priors(at=AT)
        nights = sorted({date(2026, 1, 1) + timedelta(days=rng.randrange(0, 400)) for _ in range(40)})
        looked: list[str] = []
        for night in nights:
            before = p.trials_file.read_bytes()
            expect = ST.is_look_night(night, looked)
            res = ST.gate_a_look(TR.TrialsLedger(p), night, rows, placebo=pl, fwd=fwd, family=fam, at=AT)
            if expect:
                looked.append(ST.look_month(night))
                assert res["status"] == "LOOK" and res["look_no"] == len(looked)
                new = [r for r in TR.iter_rows(p.trials_file) if r["kind"] == "LOOK_A" and r["look_no"] == len(looked)]
                assert {r["row_id"] for r in new} == set(fam) and all(r["verdict"] in ("PASS_A", "FAIL_A") for r in new)
                assert all(r["window"]["look_month"] == ST.look_month(night) for r in new)
            else:
                assert res["status"] == "INTERIM" and res["label"] == "ara görünüm"
                assert "PASS_A" not in str(res) and "FAIL_A" not in str(res)
                assert p.trials_file.read_bytes() == before
        months = [r["window"]["look_month"] for r in TR.iter_rows(p.trials_file) if r["kind"] == "LOOK_A"]
        assert len(months) == len(fam) * len(set(months)) and len(set(months)) == len(looked)
        assert all(int(m[-2:]) >= 1 for m in months)


def test_broken_chain_or_seal_closes_the_gate_without_writing(tmp_path, monkeypatch):
    _world, rows, pl, fwd = _tiny_rows()
    p = EnginePaths(data=tmp_path / "data", state=tmp_path / "state")
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    raw = p.trials_file.read_bytes()
    p.trials_file.write_bytes(raw[:-40])                                                 # yarım satır
    res = ST.gate_a_look(TR.TrialsLedger(p), date(2026, 12, 6), rows, placebo=pl, fwd=fwd, family=sorted(rows))
    assert res["status"] == "CLOSED" and res["reason"] == "TRIALS_CHAIN_BROKEN"
    p.trials_file.write_bytes(raw)
    monkeypatch.setattr(RG, "REGISTRY_SHA", "0" * 64)
    res = ST.gate_a_look(TR.TrialsLedger(p), date(2026, 12, 6), rows, placebo=pl, fwd=fwd, family=sorted(rows))
    assert res["status"] == "CLOSED" and res["reason"] == "SEAL_MISMATCH"
    assert p.trials_file.read_bytes() == raw


def test_provisional_thresholds_are_labelled_and_s1_parts_come_from_the_seal():
    th = ST.GATE_A_PROVISIONAL
    assert th["sealed"] is False and th["source"].startswith("PROVISIONAL_UNSEALED")
    assert th["q_bh_max"] == RG.STATS_V1["thresholds"]["bh_q"]
    assert th["placebo_ci_lower_min_exclusive"] == RG.STATS_V1["thresholds"]["placebo"]["min_ci_lower"]
    assert th["max_single_instrument_share"] == RG.LIB_V1["thresholds"]["single_instrument"]["max_oos_profit_share"]
    assert th["min_cluster_coherence_r"] == RG.LIB_V1["thresholds"]["cluster_coherence"]["min_ex_coin_oos_adverse_mean_r"]
    # §3.7 metninin değerleri (S2'de mühürlenecek)
    assert (th["min_oos_trades"], th["min_oos_days"], th["min_positive_fold_share"], th["pbo_max_exclusive"],
            th["dsr_min"], th["fwd_min_days"], th["fwd_min_days_intraday"], th["fwd_min_trades"]) == \
        (100, 730, 0.6, 0.25, 0.95, 60, 30, 20)


def test_selection_groups_follow_the_seal():
    assert ST.selection_group_key("POOLED", "TS_7_LO", "POOLED_P23") == "FAM:B@POOLED_P23"
    assert ST.selection_group_key("COIN_FIT", "D", "COIN_FIT:SOL") == "GRID:D@COIN_FIT:SOL"
    assert ST.selection_group_key("COPY", "K_D4", "COIN:BTC") == "COINSEL:K_D4"
    assert ST.selection_group_key("COPY", "K_D4", "POOLED_OWN") == ST.selection_group_key("COVER", "O_D4_F_ER", "POOLED_OWN")
    assert ST.selection_group_key("COPY", "K_C4_CV003", "POOLED_OWN") == "COVER:C4@POOLED_OWN"
    assert ST.selection_group_key("OTHER", "G_NY_ORB", "XAU") == "FAM:G@XAU"
    keys = {ST.selection_group_key(r["row_type"], r["unit"], r["scope"]) for r in RG.gate_a_rows()}
    sizes = {}
    for r in RG.gate_a_rows():
        k = ST.selection_group_key(r["row_type"], r["unit"], r["scope"])
        sizes[k] = sizes.get(k, 0) + (r["raw_trials"] if r["row_type"] == "COIN_FIT" else 1)
    assert sizes["COINSEL:K_D4"] == 23 and sizes["COVER:D4@POOLED_OWN"] == 8 and sizes["FAM:J@PORT_XSEC"] == 5
    assert sizes["FAM:I@PAIR_BTCETH"] == 2 and sizes["FAM:G@XAU"] == 6 and sizes["GRID:B@COIN_FIT:BTC"] == 10
    assert len(keys) > 100


# ============================================================================ yazım yolu
NEW_MODULES = ("wf.py", "stats.py", "cscv.py", "placebo.py")


def test_new_modules_have_no_file_write_path_and_append_only_through_trials():
    """Deneme defteri yalnız `trials.TrialsLedger.append` ile büyür: yeni modüllerde dosya açma/yazma/yeniden adlandırma/
    silme yok; deftere dokunan tek çağrı `.append(`."""
    banned_attrs = {"replace", "rename", "unlink", "remove", "write_text", "write_bytes", "rmtree", "truncate", "mkdir"}
    for name in NEW_MODULES:
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    assert f.id not in ("open", "exec", "eval"), (name, node.lineno)
                if isinstance(f, ast.Attribute):
                    assert f.attr not in banned_attrs, (name, f.attr, node.lineno)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                assert not {"shutil", "tempfile", "os", "subprocess", "socket", "urllib"} & {m.split(".")[0] for m in mods}, name
    src = (PKG / "stats.py").read_text(encoding="utf-8") + (PKG / "wf.py").read_text(encoding="utf-8")
    assert src.count("ledger.append(") == 2                                              # LOOK_A/FWD_READ ve SELECT
