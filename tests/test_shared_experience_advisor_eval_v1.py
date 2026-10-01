# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — WALK-FORWARD DEĞERLENDİRME (2026-09-29; SPEC_ADVISOR_V1 §5, §6.4, §7 T-A5; DANISMAN_V1.md §4, §6).

* Elle hesaplanmış oyuncak (§5.7): her ölçü, yarılar ve bölme günü; boş kümeler (G boş, T∖G boş, Δ tanımsız).
* Bootstrap: deterministik, oyuncak için SABİT aralık (tohum 20261001, B = 10 000); kanonik gün sırası; parça boyu sonucu
  değiştirmez; yüzdelik dizin kuralı.
* Kohort × gruplama × kanal matrisi elle yeniden hesaplamaya eşit.
* Bakışlar: L1 tarihi sonuca KÖR; L2 = L1 + 28 g, L3 = L1 + 56 g; α bakış başına; `kesim + 7 g` yerleşme; 16 haftada
  NOT_REACHED; PASS her koşulu ister (her biri tek tek bozulunca FAIL; bütünlük bilinmiyorsa PENDING).
* Koşan sayaçlar (panel) = CLI'ın defter başına nokta ölçüleri.
* CLI: salt okur (state ağacı öncesi/sonrası aynı), `--clock event` RETRO şeridi, `ask` biçimi, `verify` uyumu ve açıklanan
  uyumsuzluk, `shared-experience-report --advisor`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import advisor_synth as X  # noqa: E402
from advisor_synth import A, R  # noqa: E402
from test_shared_experience_advisor_fold_v1 import run_world  # noqa: E402

from tradingbot.shared_experience import advisor_eval as AE  # noqa: E402
from tradingbot.shared_experience import advisor_live as AL  # noqa: E402

DAY = X.DAY
L = {lab: i for i, lab in enumerate(A.LABELS)}
TOY = [(1, "GIR", 1.0), (1, "NOTR", -0.5), (1, "GIRME", -1.0), (2, "GIR", -1.0), (2, "GIRME", -1.0),
       (2, "VERI_AZ", 0.5), (3, "NOTR", 2.0), (3, "GIRME", 0.5), (4, "GIR", 0.5), (4, "GIRME", -1.0)]


def _toy(rows=TOY):
    day = np.array([t[0] for t in rows], dtype=np.int64)
    lab = np.array([L[t[1]] for t in rows], dtype=np.int64)
    y = np.array([A.r_micro(t[2]) for t in rows], dtype=np.int64)
    return day, lab, y


# ============================================================================ oyuncak (§5.7)
def test_walkforward_toy_every_metric_by_hand():
    m = AE.metrics(*_toy())
    assert (m["n_T"], m["n_G"], m["mean_T"], m["share_G"], m["days"]) == (10, 4, 0.0, 0.4, 4)
    assert m["U_mean"] == 0.416667 and m["U_sum100"] == 25.0 and m["delta"] == 0.791667
    means = {k: v["mean"] for k, v in m["labels"].items()}
    assert means == {"GIR": 0.166667, "NOTR": 0.75, "GIRME": -0.625, "VERI_AZ": 0.5}
    assert {k: v["n"] for k, v in m["labels"].items()} == {"GIR": 3, "NOTR": 2, "GIRME": 4, "VERI_AZ": 1}
    h = m["halves"]
    assert h["split_day"] == 2 and h["h1"]["U_mean"] == 0.333333 and h["h2"]["U_mean"] == 0.75
    assert h["h1"]["n_T"] == 6 and h["h2"]["n_T"] == 4


def test_empty_set_rules():
    day, lab, y = _toy([(1, "GIR", 1.0), (1, "NOTR", -1.0), (2, "GIR", 0.5)])
    m = AE.metrics(day, lab, y, boot=False)
    assert m["n_G"] == 0 and m["U_mean"] == 0.0 and m["U_sum100"] == 0.0 and m["delta"] is None
    day, lab, y = _toy([(1, "GIRME", -1.0), (2, "GIRME", 0.5)])
    m = AE.metrics(day, lab, y, boot=False)
    assert m["U_mean"] == 0.25 and m["U_sum100"] == 25.0 and m["delta"] is None     # T∖G boş → −ort(T)
    # Δ, GİR ya da GİRME olmayan yeniden örneklemlerde düşer; > %5 ise aralık null
    day, lab, y = _toy([(1, "GIR", 1.0), (2, "GIRME", -1.0), (3, "NOTR", 0.0)])
    ci = AE.bootstrap(day, lab, y, b=2000)
    assert ci["delta_dropped"] > 0.05 * 2000 and ci["delta"] is None and ci["U_mean"] is not None


def test_bootstrap_is_deterministic_pinned_and_chunk_and_order_invariant():
    day, lab, y = _toy()
    ci = AE.bootstrap(day, lab, y)
    assert ci == {"alpha": 0.05, "B": 10000, "seed": 20261001, "days": 4, "U_mean": [0.291667, 0.75],
                  "U_sum100": [-5.555556, 44.444444], "delta": [-0.75, 1.75], "delta_dropped": 39}
    assert AE.bootstrap(day, lab, y, chunk_bytes=64) == ci == AE.bootstrap(day, lab, y, chunk_bytes=10 ** 9)
    perm = np.random.default_rng(3).permutation(day.size)
    assert AE.bootstrap(day[perm], lab[perm], y[perm]) == ci                      # kanonik (artan) gün sırası


def test_bootstrap_percentile_index_rule():
    day, lab, y = _toy()
    b, alpha = 1000, 0.1
    ci = AE.bootstrap(day, lab, y, b=b, alpha=alpha)
    rng = np.random.default_rng(AE.BOOT_SEED)
    idx = rng.integers(0, 4, size=(b, 4))
    days = np.array([1, 2, 3, 4])
    us = []
    for row in idx:
        sel = np.concatenate([np.flatnonzero(day == days[i]) for i in row])
        us.append(AE.point_metrics(np.bincount(lab[sel], minlength=4), [int(y[sel][lab[sel] == k].sum())
                                                                       for k in range(4)])["U_mean"])
    us = np.sort(np.array(us))
    assert ci["U_mean"] == [round(float(us[int(alpha / 2 * b)]), 6), round(float(us[int((1 - alpha / 2) * b)]), 6)]


def test_wf_sha_is_sealed_separately():
    import copy
    import hashlib
    assert AE.WF_SHA == hashlib.sha256(json.dumps(AE.WF_SPEC, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                                       .encode("ascii")).hexdigest()[:16]
    spec = copy.deepcopy(AE.WF_SPEC)
    spec["bootstrap"]["B"] = 9999
    assert hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16] != AE.WF_SHA
    assert AE.WF_SPEC["looks"]["alpha"] == [0.01, 0.01, 0.03] and sum(AE.LOOK_ALPHAS) == pytest.approx(0.05)
    assert "wf_" not in A.ADVISOR_SPEC and AE.WF_SHA != A.ADVISOR_SHA


# ============================================================================ matris = elle hesap
def _hand(a: dict, mask: np.ndarray, labk: str) -> dict:
    n, s = [0] * 4, [0] * 4
    for j in np.flatnonzero(mask):
        lc = int(a[labk][j])
        if lc < 0:
            continue
        n[lc] += 1
        s[lc] += int(a["y"][j])
    return AE.point_metrics(n, s)


def test_cohort_grouping_channel_matrix_equals_hand_computation(tmp_path):
    rows = X.random_store(2) + X.random_store(4)[-150:]
    root = tmp_path / "xp"
    X.write_hot(root, rows)
    born = X.T0_MS - 1
    (root / "advice").mkdir()
    (root / "advice" / AL.META_FILE).write_text(json.dumps({"advisors": {A.ADVISOR_SHA: {"advisor_born_ms": born}}}))
    doc = AE.run_walkforward(root, eligibility="all", boot=False)
    fold = AE.fold_store(root, born_ms=born)
    a = fold.resolved_arrays()
    base = AE.eligible_mask(a, "all")
    for coh in AE.COHORTS:
        m = base & AE._cohort_mask(a, coh)
        for chn, labk in (("advice", "lab_r"), ("advice_cf", "lab_c")):
            got = doc["matrix"][coh][chn]
            want = _hand(a, m, labk)
            for k in ("n_T", "n_G", "U_mean", "U_sum100", "delta"):
                assert got[k] == want[k], (coh, chn, k)
            for bi, bk in enumerate(A.BOOK_KEYS):
                sub = _hand(a, m & (a["book"] == bi), labk)
                if sub["n_T"]:
                    assert got["by_book"][bk]["n_T"] == sub["n_T"] and got["by_book"][bk]["U_mean"] == sub["U_mean"]
            lv = a["lvl_r" if chn == "advice" else "lvl_c"]
            base_codes = [A.LEVEL_NAMES.index("S5"), A.LEVEL_NAMES.index("F5")]
            assert got["by_level"]["BASE"]["n_T"] == _hand(a, m & np.isin(lv, base_codes), labk)["n_T"]
    assert doc["matrix"]["ALL_REAL"]["advice"]["n_T"] > 5 and doc["matrix"]["CF"]["advice_cf"]["n_T"] > 20
    assert AE.render_tr(doc).startswith("GÖLGE DANIŞMAN advisor_v1")


# ============================================================================ bakışlar
def _look_arrays(*, born: int, days: int = 70, per_day: int = 30, gshare: float = 0.2, y_fn=None, seed: int = 1):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(days):
        for j in range(per_day):
            as_of = born + d * DAY + 3600_000 + j * 60_000
            u = (j % 10) / 10.0                                   # deterministik etiket payları (gün başına sabit)
            lab = "GIRME" if u < gshare else ("GIR" if u < gshare + 0.3 else "NOTR")
            y = y_fn(lab, d, rng) if y_fn else (-1.0 if lab == "GIRME" else (0.6 if lab == "GIR" else 0.3))
            rows.append((as_of, lab, y))
    n = len(rows)
    return {"kh": np.arange(n, dtype=np.uint64), "as_of": np.array([r[0] for r in rows], dtype=np.int64),
            "day": np.array([r[0] // DAY for r in rows], dtype=np.int64),
            "res_clock": np.array([r[0] + 3600_000 for r in rows], dtype=np.int64),
            "t_clock": np.array([r[0] for r in rows], dtype=np.int64), "book": np.zeros(n, np.int8),
            "fam": np.zeros(n, np.int64), "coh": np.ones(n, np.int8), "kind": np.zeros(n, np.int8),
            "lab_r": np.array([L[r[1]] for r in rows], dtype=np.int8), "lab_c": np.full(n, L["NOTR"], np.int8),
            "lvl_r": np.zeros(n, np.int8), "lvl_c": np.zeros(n, np.int8),
            "y": np.array([A.r_micro(r[2]) for r in rows], dtype=np.int64),
            "flags": np.full(n, A.RF_LIVE | A.RF_PROSP | A.RF_OK, np.int8), "rfam": np.full(n, -1, np.int8)}


BORN = (X.T0_MS // DAY) * DAY + 5 * 3600_000
NOW = BORN + 400 * DAY
GOOD = {"agreement_eligible": 1.0, "unexplained": 0, "invariants_green": True}


def _looks(a, integ=GOOD, now=NOW, b=2000):
    base = AE.eligible_mask(a, "prospective") & (a["kind"] == 0)
    return AE.looks(a, base, BORN, now, integrity=integ, b=b)


def test_passing_data_passes_at_l1_with_its_own_alpha():
    res = _looks(_look_arrays(born=BORN))
    assert res["verdict"] == "PASS(L1)" and len(res["looks"]) == 1
    lk = res["looks"][0]
    assert lk["alpha"] == 0.01 and all(lk["conditions"].values()) and lk["integrity_ok"] is True
    l1 = AE._midnight_ceil(BORN + 28 * DAY)
    assert res["L1"] == X.iso_s(l1) and lk["cutoff_ms"] == l1


def test_l1_date_is_outcome_blind_and_l2_l3_follow_by_28_and_56_days():
    a = _look_arrays(born=BORN, per_day=12)                      # N_T ≥ 500 ancak ~42. günde
    base = AE.eligible_mask(a, "prospective")
    l1, st = AE.find_l1(a, base, BORN, NOW)
    b2 = dict(a, y=-a["y"] * 3 + 17)                              # sonuçlar tamamen değişti
    assert AE.find_l1(b2, base, BORN, NOW) == (l1, st) and st == "FOUND" and l1 > AE._midnight_ceil(BORN + 28 * DAY)
    res = _looks(dict(a, y=np.zeros_like(a["y"])))               # hiçbir bakış geçmez → üç bakış
    assert res["verdict"] == "FAIL" and [x["look"] for x in res["looks"]] == ["L1", "L2", "L3"]
    assert [x["alpha"] for x in res["looks"]] == [0.01, 0.01, 0.03]
    assert [x["cutoff_ms"] - l1 for x in res["looks"]] == [0, 28 * DAY, 56 * DAY]


def test_look_data_uses_cutoff_and_seven_day_settling():
    a = _look_arrays(born=BORN, days=40)
    c = AE._midnight_ceil(BORN + 28 * DAY)
    a["res_clock"][a["as_of"] < BORN + 2 * DAY] = c + 7 * DAY + 1  # ilk iki günün sonuçları geç kesinleşti
    m = AE._look_data(a, np.ones(a["day"].size, bool), c)
    assert not m[a["as_of"] < BORN + 2 * DAY].any() and not m[a["as_of"] >= c].any() and m.sum() > 700


def test_not_reached_by_16_weeks_and_pending_before_the_first_settle():
    thin = _look_arrays(born=BORN, per_day=3, days=130)
    assert _looks(thin)["verdict"] == "NOT_REACHED"
    early = _looks(_look_arrays(born=BORN), now=BORN + 30 * DAY)
    assert early["verdict"] == "PENDING"
    assert AE.looks(thin, np.ones(1, bool), None, NOW)["verdict"] == "PENDING"


def _y_day_noise(lab, d, rng):
    """GİRME günden güne −1 / +0,8 (ortalama −0,1): nokta ölçüler ve yarılar olumlu, gün-kümeli aralık 0'ı kapsar."""
    return (-1.0 if d % 2 else 0.8) if lab == "GIRME" else 0.0


def _y_first_half_bad(lab, d, rng):
    """L1 verisinin ilk yarısında GİRME biraz daha iyi, ikinci yarısında çok kötü: yalnız yarı koşulu bozulur."""
    if d < 15:
        return 0.1 if lab == "GIRME" else 0.0
    return -3.0 if lab == "GIRME" else 0.5


@pytest.mark.parametrize("case,y_fn,integ,want", [
    ("ci", _y_day_noise, GOOD, "FAIL"),
    ("halves", _y_first_half_bad, GOOD, "FAIL"),
    ("u_sum", lambda lab, d, rng: 0.1 if lab == "GIRME" else 1.0, GOOD, "FAIL"),
    ("delta", lambda lab, d, rng: -1.0 if lab == "GIRME" else (-2.0 if lab == "GIR" else 1.0), GOOD, "FAIL"),
    ("agreement", None, {**GOOD, "agreement_eligible": 0.99}, "FAIL"),
    ("unexplained", None, {**GOOD, "unexplained": 1}, "FAIL"),
    ("invariants", None, {**GOOD, "invariants_green": False}, "FAIL"),
    ("invariants_undeclared", None, {**GOOD, "invariants_green": None}, "PENDING"),
    ("integrity_unknown", None, None, "PENDING"),
    ("all_good", None, GOOD, "PASS")])
def test_pass_needs_every_condition(case, y_fn, integ, want):
    """Tek bakış (L1): koşullardan TAM OLARAK biri bozulunca sonuç FAIL; bütünlük bilinmiyorsa PENDING."""
    a = _look_arrays(born=BORN, y_fn=y_fn)
    base = AE.eligible_mask(a, "prospective") & (a["kind"] == 0)
    l1, st = AE.find_l1(a, base, BORN, NOW)
    assert st == "FOUND"
    lk = AE.evaluate_look(a, base, l1, 0.01, integrity=integ, b=2000)
    assert lk["status"] == want, (case, lk)
    cond = lk["conditions"]
    key = {"ci": "ci_lower_gt_0", "halves": "halves_positive", "u_sum": "u_sum_positive", "delta": "delta_positive"}
    if case in key:
        assert cond[key[case]] is False and sum(not v for v in cond.values()) == 1, cond
    else:
        assert all(cond.values())
        assert lk["integrity_ok"] is ({"integrity_unknown": None, "invariants_undeclared": None,
                                        "all_good": True}.get(case, False))


def test_undeclared_invariants_give_pending_not_a_terminal_fail():
    """İnceleme bulgusu (2026-09-30): CLI `--invariants-green` verilmezse `invariants_green=None` geçer. İstatistiği
    geçen bir bakış eskiden FAIL / NO_LOOK_PASSED (terminal) oluyordu; şimdi PENDING / INVARIANTS_UNDECLARED ve bakış
    zinciri L1'de DURUR (sonraki bakışlara geçilmez). Yalnız açık False terminaldir."""
    a = _look_arrays(born=BORN)
    res = _looks(a, integ={**GOOD, "invariants_green": None})
    assert res["verdict"] == "PENDING" and res["reason"] == "INVARIANTS_UNDECLARED"
    assert [(x["look"], x["status"], x["integrity_ok"]) for x in res["looks"]] == [("L1", "PENDING", None)]
    assert _looks(a, integ={**GOOD, "invariants_green": True})["verdict"] == "PASS(L1)"
    red = _looks(a, integ={**GOOD, "invariants_green": False})
    assert red["verdict"] == "FAIL" and red["reason"] == "NO_LOOK_PASSED"
    assert [x["integrity_reason"] for x in red["looks"]] == ["INVARIANTS_RED"] * 3
    assert _looks(a, integ=None)["reason"] == "INTEGRITY_UNKNOWN"
    assert AE.integrity_verdict({**GOOD, "agreement_eligible": 0.99, "invariants_green": None}) == \
        (False, "AGREEMENT_BELOW_MIN")                          # veri kusuru beyan beklemez: terminal


def _h2_arrays(cf_girme_days):
    """600 gerçek hedef, 30 gün. Birincil kanal: 60 GİRME / 20 gün (asgariler sağlanır). Olsaydı kanalı: yalnız
    `cf_girme_days` günlerde günde 3 GİRME (her biri −1 R; diğerleri +0,3) — istatistik olarak geçer."""
    born = 20_000 * DAY
    rows = []
    for d in range(30):
        for j in range(20):
            day = 20_001 + d
            lab_r = L["GIRME"] if (d < 20 and j < 3) else L["NOTR"]
            cf_g = d in cf_girme_days and j < 3
            lab_c = L["GIRME"] if cf_g else (L["GIR"] if j in (3, 4) else L["NOTR"])
            rows.append((day * DAY + 3_600_000, day, lab_r, lab_c, -1_000_000 if cf_g else 300_000))
    n = len(rows)
    a = {"as_of": np.array([r[0] for r in rows], np.int64), "day": np.array([r[1] for r in rows], np.int64),
         "lab_r": np.array([r[2] for r in rows], np.int8), "lab_c": np.array([r[3] for r in rows], np.int8),
         "y": np.array([r[4] for r in rows], np.int64), "res_clock": np.array([r[0] + 3_600_000 for r in rows], np.int64),
         "kind": np.zeros(n, np.int8), "flags": np.full(n, A.RF_OK | A.RF_LIVE | A.RF_PROSP, np.int8),
         "lvl_r": np.zeros(n, np.int8), "lvl_c": np.zeros(n, np.int8), "coh": np.zeros(n, np.int8),
         "book": np.zeros(n, np.int8), "fam": np.zeros(n, np.int64)}
    return a, born


@pytest.mark.parametrize("days,want_ok", [((1, 5, 9, 13, 17, 21, 25, 28), False),        # 24 GİRME / 8 gün
                                          (tuple(range(0, 30, 3))[:9], False),           # 27 GİRME / 9 gün
                                          (tuple(range(0, 30, 2))[:9], False),           # 27 GİRME / 9 gün
                                          (tuple(range(0, 20)), True)])                 # 60 GİRME / 20 gün
def test_h2_minimums_count_the_cf_channels_own_girme(days, want_ok):
    """İnceleme bulgusu (2026-09-30): H2'nin asgarileri eskiden BİRİNCİL kanalın GİRME'lerini sayıyordu (N_G=60 / 20
    gün) → birkaç olsaydı-GİRME'siyle H2 PASS(L1) olabiliyordu. Şimdi asgariler değerlendirilen kanaldan; L1 TARİHİ
    birincil kanaldan (aynı bakış tarihleri)."""
    a, born = _h2_arrays(set(days))
    prim = AE.eligible_mask(a, "prospective") & (a["kind"] == 0)
    now = born + 200 * DAY
    h1 = AE.looks(a, prim, born, now, channel=A.CH_REAL, integrity=GOOD, b=2000)
    h2 = AE.looks(a, prim, born, now, channel=A.CH_CF, integrity=GOOD, b=2000)
    assert h1["L1"] == h2["L1"] and h1["L1"] is not None                  # aynı tarihler
    for x in h2["looks"]:
        mins = x["minimums"]
        assert mins["N_G"] == x["point"]["n_G"], "asgariler ile nokta ölçü AYNI kanalı sayar"
        assert mins["ok"] is want_ok
    if want_ok:
        assert h2["verdict"] == "PASS(L1)"
    else:
        assert h2["verdict"] == "FAIL" and all(x["conditions"]["minimums"] is False for x in h2["looks"])
    assert AE.minimums(a, prim)["N_G"] == 60 and AE.minimums(a, prim, "lab_c")["N_G"] == 3 * len(days)


def test_each_look_uses_its_own_window_integrity_and_a_later_outage_changes_no_past_verdict():
    """İnceleme bulgusu (2026-09-30): bütünlük eskiden deponun ÇALIŞTIRMA anındaki tamamından (bütün bakışlara aynı)
    hesaplanıyordu; L1+7 günden sonraki bir kesinti L1'in PASS hükmünü sonradan FAIL'e çeviriyordu. Şimdi her bakış
    `integrity_at(kesim)` ile KENDİ penceresinin bütünlüğünü kullanır."""
    a = _look_arrays(born=BORN)
    calls: list[int] = []

    def at(c):
        calls.append(c)
        return GOOD
    bad_whole = {**GOOD, "agreement_eligible": 0.42}
    base = AE.eligible_mask(a, "prospective") & (a["kind"] == 0)
    res = AE.looks(a, base, BORN, NOW, integrity=bad_whole, integrity_at=at, b=2000)
    l1 = AE._midnight_ceil(BORN + 28 * DAY)
    assert res["verdict"] == "PASS(L1)" and calls == [l1]
    assert res["looks"][0]["integrity"]["agreement_eligible"] == 1.0
    assert AE.looks(a, base, BORN, NOW, integrity=bad_whole, b=2000)["verdict"] == "FAIL"


def test_store_scan_window_counts_only_the_looks_targets_and_rows_stored_by_its_settle_time(tmp_path):
    root = tmp_path / "xp"
    X.write_hot(root, X.random_store(4, n_batches=30))
    cc = AE._CoreCollector()
    advs: list[dict] = []
    AE.fold_store(root, born_ms=X.T0_MS, collect=False, on_batch=lambda res: (cc(res), advs.extend(res.advice_rows)))
    for r in advs:
        r["recorded_at"] = X.iso_us(r["batch_clock_ms"] + 1000)
        r["meta"] = {}
    adv_dir = root / "advice"
    adv_dir.mkdir(parents=True, exist_ok=True)

    def store(sel):
        (adv_dir / "advice.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sel), encoding="utf-8")
        return AE._StoreScan(root, cc)
    cut = X.T0_MS + 15 * X.STEP
    in_win = [r for r in advs if r["target"]["as_of_ms"] is not None and r["target"]["as_of_ms"] < cut]
    full = store(advs)
    w_full = full.integrity(cut)
    assert full.integrity()["agreement_eligible"] == 1.0 and w_full["agreement_eligible"] == 1.0
    assert w_full["eligible_targets"] >= 20 and w_full["fold_targets"] == len(in_win)
    # sonraki kesinti: pencere DIŞI hedeflerin satırları yok → deponun tamamı düşer, pencere AYNI
    later = store(in_win)
    assert later.integrity()["agreement_eligible"] < 0.9
    w_later = later.integrity(cut)
    assert {k: w_later[k] for k in ("agree", "eligible_in_store", "agreement_eligible", "missing_eligible")} == \
        {k: w_full[k] for k in ("agree", "eligible_in_store", "agreement_eligible", "missing_eligible")}
    # pencere hedefinin satırı ancak kesim + 7 günden SONRA yazıldıysa bakış günü eksikti → eksik sayılır
    late = [dict(r, recorded_at=X.iso_us(cut + 7 * DAY + 1)) if i % 4 == 0 else r for i, r in enumerate(advs)]
    w_late = store(late).integrity(cut)
    assert w_late["missing_eligible"] > 0 and w_late["agreement_eligible"] < 1.0


# ============================================================================ koşan sayaçlar = CLI
def test_running_tallies_equal_the_cli_point_metrics(tmp_path):
    w = run_world(tmp_path, 40)
    st = json.loads((w.root / "advice" / AL.STATUS_FILE).read_text(encoding="utf-8"))
    doc = AE.run_walkforward(w.root, boot=False)
    by_book = doc["matrix"]["ALL_REAL"]["advice"]["by_book"]
    assert st["running"], "koşan sayaç boş olmamalı"
    for bk, run in st["running"].items():
        m = doc["matrix"]["ALL_REAL"]["advice"] if bk == "ALL" else by_book[bk]
        assert (run["N_T"], run["N_G"], run["U_mean"], run["delta"], run["U_sum100"]) == (
            m["n_T"], m["n_G"], m["U_mean"], m["delta"], m["U_sum100"]), bk


# ============================================================================ doğrulama
def test_verify_counts_agreement_and_explains_clock_anomaly_mismatches(tmp_path):
    w = run_world(tmp_path, 16)
    v = AE.verify(w.root)
    assert v["agreement"] == 1.0 and v["unexplained"] == 0 and v["schema"] == AE.VERIFY_SCHEMA
    hot = w.root / "advice" / "advice.jsonl"
    lines = hot.read_text(encoding="utf-8").splitlines()
    r = json.loads(lines[-1])
    r["core_sha"] = "0" * 16
    hot.write_text("\n".join(lines[:-1] + [json.dumps(r)]) + "\n", encoding="utf-8")
    v = AE.verify(w.root)
    assert v["mismatch"] == 1 and v["unexplained"] == 1 and v["agreement"] < 1.0
    r["clock_anomaly"] = True
    hot.write_text("\n".join(lines[:-1] + [json.dumps(r)]) + "\n", encoding="utf-8")
    v = AE.verify(w.root)
    assert v["mismatch"] == 1 and v["mismatch_explained"] == 1 and v["unexplained"] == 0
    hot.write_text("\n".join(lines[:-3]) + "\n", encoding="utf-8")
    v = AE.verify(w.root)
    assert v["missing_in_store"] == 3


# ============================================================================ CLI
def _tree(p: Path) -> dict[str, bytes]:
    return {x.relative_to(p).as_posix(): x.read_bytes() for x in sorted(p.rglob("*")) if x.is_file()}


def test_cli_is_read_only_prints_banners_and_answers_ask_verify_replay(tmp_path):
    w = run_world(tmp_path / "w", 18)
    st_root = w.eng.cfg.state_path
    before = _tree(st_root)
    outd = tmp_path / "out"
    outd.mkdir()
    ROOT = X.ROOT
    script = textwrap.dedent("""
        import json, socket, sys
        def _no_net(*a, **k):
            raise RuntimeError("AĞ YASAK")
        socket.socket.connect = _no_net
        socket.create_connection = _no_net
        sys.path.insert(0, %(root)r)
        from tradingbot.cli import main
        c = %(cfg)r; xp = %(xp)r; o = %(out)r
        codes = [main(["--config", c, "shared-experience-advisor", "--root", xp, "--json", "--out", o + "/wf.json",
                       "--looks", "--summary-out", o + "/summary.json"]),
                 main(["--config", c, "shared-experience-advisor", "--root", xp, "--clock", "event", "--no-boot"]),
                 main(["--config", c, "shared-experience-advisor", "--root", xp, "--mode", "verify", "--json",
                       "--out", o + "/verify.json"]),
                 main(["--config", c, "shared-experience-advisor", "--root", xp, "--mode", "ask", "--book",
                       "strategy_paper_box", "--for-symbol", "SOL/USDT", "--setup", "trend", "--side", "LONG",
                       "--json", "--out", o + "/ask.json"]),
                 main(["--config", c, "shared-experience-advisor", "--root", xp, "--mode", "replay",
                       "--replay-out", o + "/replay.jsonl"]),
                 main(["--config", c, "shared-experience-advisor", "--root", xp, "--mode", "ask"]),
                 main(["--config", c, "shared-experience-report", "--root", xp, "--advisor"]),
                 main(["--config", c, "shared-experience-advisor", "--root", xp + "/yok"])]
        heavy = sorted(m for m in sys.modules if m in ("tradingbot.engine_v3", "tradingbot.engine", "ccxt",
                                                        "tradingbot.box_timer"))
        print("RESULT" + json.dumps({"codes": codes, "heavy": heavy}))
    """) % {"root": str(ROOT), "cfg": str(ROOT / "config.yaml"), "xp": str(w.root), "out": str(outd)}
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRADINGBOT_")}
    p = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, cwd=str(tmp_path),
                       timeout=600)
    assert p.returncode == 0, p.stderr[-3000:]
    res = json.loads(p.stdout.split("RESULT", 1)[1])
    assert res == {"codes": [0, 0, 0, 0, 0, 2, 0, 2], "heavy": []}, res
    assert _tree(st_root) == before, "CLI state ağacına HİÇBİR ŞEY yazmaz"
    assert sorted(x.name for x in outd.iterdir()) == ["ask.json", "replay.jsonl", "summary.json", "verify.json",
                                                      "wf.json"]
    wf = json.loads((outd / "wf.json").read_text(encoding="utf-8"))
    assert wf["banner"] == AE.BANNER_TR and wf["wf_sha"] == AE.WF_SHA and "looks" in wf and "integrity" in wf
    assert wf["integrity"]["agreement"] == 1.0
    sm = json.loads((outd / "summary.json").read_text(encoding="utf-8"))
    assert sm["schema"] == AE.SUMMARY_SCHEMA and sm["looks"]["H1"]["verdict"] in ("PENDING", "NOT_REACHED")
    assert AE.RETRO_BANNER_TR in p.stdout and AE.BANNER_TR in p.stdout
    ask = json.loads((outd / "ask.json").read_text(encoding="utf-8"))
    assert ask["schema"] == AE.ASK_SCHEMA and ask["effect"] == "NONE" and ask["query"]["setup_key"] == \
        "strategy_paper_box|trend|LONG"
    assert ask["real"]["levels"] and ask["cf"]["levels"] and ask["advice"] in A.LABELS
    assert ask["cf"]["advice"] in ("GIR", "NOTR", "GIRME"), "tohum kanıtı S5'te yeterli"
    ver = json.loads((outd / "verify.json").read_text(encoding="utf-8"))
    assert ver["agreement"] == 1.0
    rep = [json.loads(x) for x in (outd / "replay.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["core_sha"] for r in rep} == {r["core_sha"] for r in w.advice_rows()}
    assert {r["meta"]["fold_mode"] for r in rep} == {"replay"}
    assert "GÖLGE DANIŞMAN advisor_v1" in p.stdout and "ORTAK DENEYİM" in p.stdout


def test_event_clock_mode_is_labelled_retro_and_admits_every_origin(tmp_path):
    root = tmp_path / "xp"
    X.write_hot(root, X.random_store(9))
    doc = AE.run_walkforward(root, clock="event", boot=False)
    assert doc["retro_banner"] == AE.RETRO_BANNER_TR and doc["eligibility"] == "all" and doc["clock"] == "event"
    assert doc["matrix"]["ALL_REAL"]["advice"]["n_T"] > 0
    assert AE.RETRO_BANNER_TR in AE.render_tr(doc)
    ref = AE.run_walkforward(root, boot=False)
    assert "retro_banner" not in ref
    with pytest.raises(Exception):
        AE.run_walkforward(root, clock="wall")
