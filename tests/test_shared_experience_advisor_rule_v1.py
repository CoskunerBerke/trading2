# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN advisor_v1 — KURAL (2026-09-29; SPEC_ADVISOR_V1 §3, §7 T-A1; ön kayıt DANISMAN_V1.md §3, §6).

Mühür (`ADVISOR_SHA` sabit; her anahtar değişimi mührü değiştirir; situation_v1 SCHEMA_SHA gömülü), t tablosu ve arama
kuralı, altın CI vektörleri (her tam sayı ve yuvarlanmış kayan nokta), eşikler (n 29/30, küme 4/5, sınırlar KESİN,
D = 0), geri çekilme (raporun `backoff_levels`i; S önce F; İLK yeterli seviye cevaplar; C4S aile yok; FADE hiçbir
seviyede TREND kanıtı görmez; durum yoksa yalnız S5/F5; UNKNOWN bir kategori; n iç içe seviyelerde monoton), büzülme
vektörleri ve itiraz kuralı, kanallar (olsaydı kanıtı birincili, gerçek kanıt olsaydı kanalını DEĞİŞTİRMEZ; yalnız
cf_label_v3), tek kaynaklar (DIMS, BACKOFF_ORDER, coin_of, bucket).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import advisor_synth as X  # noqa: E402
from advisor_synth import A, R, S, XR  # noqa: E402

#: Mühür (2026-09-29; 2026-09-30 mühür commit'inden ÖNCE saat kuralı düzeltildi — DANISMAN_V1.md §7). Kural değişirse advisor_v2 + yeni ön kayıt gerekir; bu sabit ASLA sessizce güncellenmez.
PINNED_ADVISOR_SHA = "8a89fd7e69a2d33b"
REC0 = X.iso_us(X.T0_MS - 3 * X.STEP, 11)
REC1 = X.iso_us(X.T0_MS, 222)
AS_OF = X.T0_MS + 60_000


def _sha(spec) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                          .encode("ascii")).hexdigest()[:16]


def _advice(rows, key="TGT"):
    _f, adv = X.fold_all(rows)
    hit = [a for a in adv if a["target"]["key"].split("|")[1] == key]
    assert len(hit) == 1, [a["target"]["key"] for a in adv]
    return hit[0]


# ============================================================================ mühür
def test_advisor_sha_is_pinned_and_rebuilt_like_situation_schema_sha():
    assert A.ADVISOR_SHA == PINNED_ADVISOR_SHA
    assert _sha(A.ADVISOR_SPEC) == A.ADVISOR_SHA
    assert A.ADVISOR_SPEC["situation_schema_sha"] == S.SCHEMA_SHA and A.ADVISOR_SPEC["situation_schema_id"] == "situation_v1"
    # aynı kurulum: situation.SCHEMA_SHA = sha256(kanonik JSON)[:16]
    assert _sha(S.SCHEMA_SPEC) == S.SCHEMA_SHA
    assert A.ADVISOR_SPEC["evidence"]["cf_label_versions"] == ["cf_label_v3"]
    assert A.ADVISOR_SPEC["decision"]["min_n"] == 30 and A.ADVISOR_SPEC["decision"]["min_clusters"] == 5


@pytest.mark.parametrize("key", sorted(A.ADVISOR_SPEC))
def test_mutating_any_spec_key_changes_the_seal(key):
    spec = copy.deepcopy(A.ADVISOR_SPEC)
    v = spec[key]
    spec[key] = (v + "_x") if isinstance(v, str) else ({**v, "_mutated": 1} if isinstance(v, dict)
                                                       else [*v, "x"] if isinstance(v, list) else (v or 0) + 1)
    assert _sha(spec) != A.ADVISOR_SHA


def test_situation_schema_change_would_force_a_new_seal():
    spec = copy.deepcopy(A.ADVISOR_SPEC)
    spec["situation_schema_sha"] = "0" * 16
    assert _sha(spec) != A.ADVISOR_SHA


# ============================================================================ t tablosu
def test_t975_table_and_lookup_rule():
    table = {4: 2.776445, 5: 2.570582, 6: 2.446912, 7: 2.364624, 8: 2.306004, 9: 2.262157, 10: 2.228139,
             11: 2.200985, 12: 2.178813, 13: 2.160369, 14: 2.144787, 15: 2.131450, 16: 2.119905, 17: 2.109816,
             18: 2.100922, 19: 2.093024, 20: 2.085963, 21: 2.079614, 22: 2.073873, 23: 2.068658, 24: 2.063899,
             25: 2.059539, 26: 2.055529, 27: 2.051831, 28: 2.048407, 29: 2.045230, 30: 2.042272, 40: 2.021075,
             60: 2.000298, 120: 1.979930}
    assert A.T975 == table and A.T975_INF == 1.959964
    assert A.t975(4) == 2.776445 and A.t975(29) == 2.045230 and A.t975(30) == 2.042272
    assert A.t975(31) == 2.042272 and A.t975(39) == 2.042272 and A.t975(45) == 2.021075
    assert A.t975(59) == 2.021075 and A.t975(119) == 2.000298 and A.t975(200) == 1.979930
    assert A.t975(10 ** 9 - 1) == 1.979930 and A.t975(10 ** 9) == A.T975_INF and A.t975(10 ** 12) == A.T975_INF
    assert A.t975(3) is None
    # tablo değerleri gerçekten Student t 0,975 çeyreği (6 ondalık)
    scipy_stats = pytest.importorskip("scipy.stats")
    for df, t in table.items():
        assert round(float(scipy_stats.t.ppf(0.975, df)), 6) == t


def _t_pdf(x: float, v: int) -> float:
    return math.exp(math.lgamma((v + 1) / 2) - math.lgamma(v / 2) - 0.5 * math.log(v * math.pi)
                    - (v + 1) / 2 * math.log1p(x * x / v))


def _t_q975(v: int, n: int = 800) -> float:
    """Student t 0,975 çeyreği — bağımsız saf Python (Simpson ile P(0 ≤ T ≤ t) = 0,475, ikiye bölme)."""
    def half(t: float) -> float:
        h = t / n
        return (_t_pdf(0.0, v) + _t_pdf(t, v) + sum((4 if i % 2 else 2) * _t_pdf(i * h, v) for i in range(1, n))) * h / 3
    lo, hi = 1.0, 4.0
    for _ in range(44):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if half(mid) < 0.475 else (lo, mid)
    return (lo + hi) / 2


def test_t975_table_matches_an_independent_pure_python_quantile():
    """scipy olmadan (CI'da yok) da tablo doğrulanır: her df için bağımsız sayısal çeyrek, 6 ondalık; ∞ → normal."""
    for df, t in A.T975.items():
        assert round(_t_q975(df), 6) == t, df
    lo, hi = 1.0, 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if 0.5 * math.erfc(-mid / math.sqrt(2.0)) < 0.975 else (lo, mid)
    assert round(lo, 6) == A.T975_INF


# ============================================================================ altın vektörler (§5.7)
def test_golden_vector_a_girme_every_integer_and_float():
    st = A.CellStats.from_days(X.GOLDEN_A)
    assert (st.n, st.s, st.q1, st.q2, st.q3, st.c) == (30, -19_100_000, 79_950_000_000_000, -114_600_000, 180, 5)
    d = st.q1 * st.n ** 2 - 2 * st.s * st.n * st.q2 + st.s ** 2 * st.q3
    assert d == 6_289_200_000_000_000
    mean, se, df, t, lo, hi = A.ci_cr1(st)
    assert (round(mean, 6), round(se, 6), df, t, round(lo, 6), round(hi, 6)) == (-0.636667, 0.098517, 4, 2.776445,
                                                                                 -0.910193, -0.36314)
    assert A.decide(st) == (A.GIRME, A.CI_BELOW_0)


def test_golden_vector_b_gir():
    st = A.CellStats.from_days(X.GOLDEN_B)
    assert (st.n, st.s, st.q1, st.q2, st.q3, st.c) == (30, 5_500_000, 8_790_000_000_000, 33_000_000, 180, 5)
    assert st.q1 * st.n ** 2 - 2 * st.s * st.n * st.q2 + st.s ** 2 * st.q3 == 2_466_000_000_000_000
    mean, se, df, t, lo, hi = A.ci_cr1(st)
    assert (round(mean, 6), round(se, 6), round(lo, 6), round(hi, 6)) == (0.183333, 0.061689, 0.012057, 0.35461)
    assert A.decide(st) == (A.GIR, A.CI_ABOVE_0)


def test_golden_vectors_c_and_d_are_veri_az():
    c = {**X.GOLDEN_B, 5: [-1, 1.1, 0.9, -1]}
    st = A.CellStats.from_days(c)
    assert st.n == 28 and A.decide(st) == (A.VERI_AZ, A.NO_SUFFICIENT_LEVEL)
    d = {k: v for k, v in X.GOLDEN_A.items() if k != 5}
    d[4] = d[4] + X.GOLDEN_A[5]
    st = A.CellStats.from_days(d)
    assert (st.n, st.c) == (30, 4) and A.decide(st) == (A.VERI_AZ, A.NO_SUFFICIENT_LEVEL)


def test_golden_vectors_through_the_fold_give_the_same_answer_and_rounded_fields():
    rows = X.cell_rows(X.GOLDEN_A, rec=REC0) + [X.target_row(as_of=AS_OF, rec=REC1)]
    real = _advice(rows)["real"]
    assert real["advice"] == "GIRME" and real["advice_tr"] == "GİRME" and real["reason"] == "CI_BELOW_0"
    assert real["level"] == "S0" and real["n"] == 30 and real["clusters"] == 5 and real["df"] == 4
    assert real["mean_r"] == -0.636667 and real["se_r"] == 0.098517 and real["ci95"] == [-0.910193, -0.36314]
    assert real["sum_r"] == -19.1 and real["t_crit"] == 2.776445
    rows = X.cell_rows(X.GOLDEN_B, rec=REC0) + [X.target_row(as_of=AS_OF, rec=REC1)]
    real = _advice(rows)["real"]
    assert (real["advice"], real["mean_r"], real["ci95"]) == ("GIR", 0.183333, [0.012057, 0.35461])


# ============================================================================ eşikler
def test_sufficiency_thresholds_n_29_30_and_clusters_4_5():
    assert not A.sufficient(29, 5) and A.sufficient(30, 5) and not A.sufficient(30, 4) and A.sufficient(31, 6)
    b29 = {**X.GOLDEN_B, 5: X.GOLDEN_B[5][:5]}
    rows = X.cell_rows(b29, rec=REC0) + [X.target_row(as_of=AS_OF, rec=REC1)]
    real = _advice(rows)["real"]
    assert real["advice"] == "VERI_AZ" and real["levels"][0][1] == 29 and real["level"] is None
    rows = X.cell_rows(X.GOLDEN_B, rec=REC0) + [X.target_row(as_of=AS_OF, rec=REC1)]
    assert _advice(rows)["real"]["level"] == "S0"


def test_decision_boundaries_are_strict():
    assert A.label_for_ci(-1.0, 0.0) == (A.NOTR, A.CI_SPANS_0)          # üst uç = 0 → NÖTR
    assert A.label_for_ci(0.0, 1.0) == (A.NOTR, A.CI_SPANS_0)           # alt uç = 0 → NÖTR
    assert A.label_for_ci(-1.0, -1e-12) == (A.GIRME, A.CI_BELOW_0)
    assert A.label_for_ci(1e-12, 1.0) == (A.GIR, A.CI_ABOVE_0)
    assert A.label_for_ci(-0.5, 0.5) == (A.NOTR, A.CI_SPANS_0)


def test_zero_dispersion_gives_a_point_ci_and_girme():
    st = A.CellStats.from_days({d: [-1.0] * 6 for d in range(1, 6)})
    d = st.q1 * st.n ** 2 - 2 * st.s * st.n * st.q2 + st.s ** 2 * st.q3
    assert d == 0
    mean, se, _df, _t, lo, hi = A.ci_cr1(st)
    assert (mean, se, lo, hi) == (-1.0, 0.0, -1.0, -1.0) and A.decide(st)[0] == A.GIRME
    rows = X.cell_rows({d: [-1.0] * 6 for d in range(1, 6)}, rec=REC0) + [X.target_row(as_of=AS_OF, rec=REC1)]
    real = _advice(rows)["real"]
    assert real["ci95"] == [-1.0, -1.0] and real["se_r"] == 0.0 and real["advice"] == "GIRME"


def test_no_sufficient_level_anywhere_is_veri_az_with_every_level_listed():
    rows = X.cell_rows({1: [1.0] * 3, 2: [-1.0] * 3}, rec=REC0) + [X.target_row(as_of=AS_OF, rec=REC1)]
    real = _advice(rows)["real"]
    assert real["advice"] == "VERI_AZ" and real["reason"] == "NO_SUFFICIENT_LEVEL"
    assert [lv[0] for lv in real["levels"]] == list(A.LEVELS_S) + list(A.LEVELS_F)
    assert all(real[k] is None for k in ("level", "n", "ci95", "coin", "origin_mix", "group", "dims_used"))


# ============================================================================ geri çekilme
def test_levels_come_from_report_backoff_levels_setup_before_family():
    dims = {"trend": "UP", "vol": "HIGH", "btc": "DOWN", "volume": "NORMAL", "structure": "MIXED"}
    lv = A.levels_for(dims, setup_key="strategy_paper_box|box_fade|LONG", family_key="FADE|LONG")
    assert [x[0] for x in lv] == list(A.LEVELS_S) + list(A.LEVELS_F)
    bl = XR.backoff_levels(dims)
    assert [(x[2], x[3]) for x in lv[:6]] == [(d, dr) for d, dr in bl] == [(x[2], x[3]) for x in lv[6:]]
    assert A.BACKOFF_ORDER == XR.BACKOFF_ORDER == ("structure", "volume", "btc", "vol", "trend")
    assert A.DIMS is XR.DIMS and A.BACKOFF_ORDER is XR.BACKOFF_ORDER
    assert [set(x[2]) for x in lv[:6]] == [set(A.DIMS[i] for i in k) for k in A.LEVEL_KEPT]
    # durum kullanılamıyorsa yalnız durumsuz seviyeler
    assert [x[0] for x in A.levels_for(None, setup_key="a|b|LONG", family_key="FADE|LONG")] == ["S5", "F5"]
    assert [x[0] for x in A.levels_for(dims, setup_key="a|b|LONG", family_key=None)] == list(A.LEVELS_S)
    assert [x[0] for x in A.levels_for(dims, setup_key=None, family_key="FADE|LONG")] == list(A.LEVELS_F)


def test_first_sufficient_level_answers_even_if_a_later_level_would_be_decisive():
    # S0 (tam durum) hücresi: 30 gözlem / 5 gün, ortalama ≈ 0 → NÖTR. Yapısı farklı 150 gözlem çok zararlı: S1+ GİRME olurdu.
    s0 = X.cell_rows({d: [1.0, -1.0, 0.5, -0.5, 0.2, -0.2] for d in range(1, 6)}, rec=REC0, prefix="A")
    other = X.cell_rows({d: [-1.0] * 30 for d in range(1, 6)}, rec=REC0, prefix="B", sn=X.snap(structure="LH_LL"))
    adv = _advice(s0 + other + [X.target_row(as_of=AS_OF, rec=REC1)])
    assert adv["real"]["level"] == "S0" and adv["real"]["advice"] == "NOTR"
    assert [lv[0] for lv in adv["real"]["levels"]] == ["S0"]
    # aynı kanıt, yapısı LH_LL olan hedef için S0 GİRME (kontrol: ayırt edici veri gerçekten var)
    adv2 = _advice(s0 + other + [X.target_row(as_of=AS_OF, rec=REC1, sn=X.snap(structure="LH_LL"))])
    assert adv2["real"]["advice"] == "GIRME"


def test_backoff_drops_structure_first_when_the_full_cell_is_thin():
    thin = X.cell_rows({1: [-1.0] * 3}, rec=REC0, prefix="A")                      # S0: 3 gözlem
    wide = X.cell_rows({d: [-1.0, -0.8] * 4 for d in range(1, 6)}, rec=REC0, prefix="B", sn=X.snap(structure="MIXED"))
    real = _advice(thin + wide + [X.target_row(as_of=AS_OF, rec=REC1)])["real"]
    assert real["level"] == "S1" and real["dropped"] == ["structure"] and "structure" not in real["dims_used"]
    assert real["n"] == 43 and [lv[:3] for lv in real["levels"]] == [["S0", 3, 1], ["S1", 43, 5]]


def test_c4s_has_no_family_levels():
    rows = [X.target_row("strategy_paper_candle4h_strict", setup="candle:CV001", as_of=AS_OF, rec=REC1)]
    real = _advice(rows)["real"]
    assert [lv[0] for lv in real["levels"]] == list(A.LEVELS_S)
    assert R.family_of("strategy_paper_candle4h_strict", "candle:CV001") is None


def test_fade_target_never_sees_trend_evidence_at_any_level():
    trend = X.cell_rows({d: [-1.0] * 12 for d in range(1, 6)}, rec=REC0, book="strategy_paper", setup="trend")
    adv = _advice(trend + [X.target_row(as_of=AS_OF, rec=REC1)])
    assert adv["target"]["family_key"] == "FADE|LONG"
    assert adv["real"]["advice"] == "VERI_AZ" and all(lv[1] == 0 for lv in adv["real"]["levels"])
    t2 = _advice(trend + [X.target_row("strategy_paper", setup="trend", as_of=AS_OF, rec=REC1)])
    assert t2["real"]["advice"] == "GIRME" and t2["target"]["family_key"] == "TREND|LONG"


def test_families_and_sides_never_merge_across_random_stores():
    for seed in range(0, 60, 3):
        rows = X.random_store(seed + 1)
        _f, adv = X.fold_all(rows)
        for a in adv:
            for ch in ("real", "cf"):
                d = a.get(ch)
                if not d or not d["group"]:
                    continue
                want = a["target"]["setup_key"] if d["group"] == "setup" else a["target"]["family_key"]
                assert d["group_key"] == want, (seed, a["target"]["key"], d["group_key"], want)


def test_situation_unusable_uses_only_s5_and_f5():
    rows = X.cell_rows({d: [-1.0] * 7 for d in range(1, 6)}, rec=REC0)
    for sn, st in ((None, "OK"), (X.snap(), "GAP"), (X.snap(schema_sha="0" * 16), "OK")):
        adv = _advice(rows + [X.target_row(as_of=AS_OF, rec=REC1, sn=sn, status=st)])
        assert adv["target"]["situation_used"] is False and adv["target"]["dims"] is None
        assert [lv[0] for lv in adv["real"]["levels"]] == ["S5"] and adv["real"]["level"] == "S5"
        assert adv["real"]["dropped"] == list(A.BACKOFF_ORDER) and adv["real"]["n"] == 35


def test_unknown_structure_is_its_own_category():
    unk = X.cell_rows({d: [-1.0] * 6 for d in range(1, 6)}, rec=REC0, prefix="U", sn=X.snap(structure=None))
    hh = X.cell_rows({d: [1.0] * 6 for d in range(1, 6)}, rec=REC0, prefix="H")
    adv = _advice(unk + hh + [X.target_row(as_of=AS_OF, rec=REC1, sn=X.snap(structure=None))])
    assert adv["target"]["dims"]["structure"] == "UNKNOWN"
    assert adv["real"]["level"] == "S0" and adv["real"]["dims_used"]["structure"] == "UNKNOWN"
    assert adv["real"]["n"] == 30 and adv["real"]["advice"] == "GIRME"


def test_n_is_monotone_over_nested_levels():
    seen = 0
    for seed in range(12):
        _f, adv = X.fold_all(X.random_store(seed))
        for a in adv:
            for ch in ("real", "cf"):
                d = a.get(ch)
                if not d or d["advice"] != "VERI_AZ":
                    continue
                for grp in ("S", "F"):
                    ns = [lv[1] for lv in d["levels"] if lv[0].startswith(grp)]
                    assert ns == sorted(ns), (seed, a["target"]["key"], d["levels"])
                    seen += len(ns) > 1
    assert seen > 50


# ============================================================================ büzülme
def test_shrinkage_vectors_and_dissent():
    sh, sign = A.shrunk(10, -10_000_000, 100, 20_000_000)
    assert round(sh, 6) == -0.2 and sign == -1
    assert round(A.shrunk(0, 0, 100, 20_000_000)[0], 6) == 0.2
    assert round(A.shrunk(40, 20_000_000, 100, -30_000_000)[0], 6) == 0.233333
    # itiraz: n_c ≥ 10 ve işaret farklı (0 kendi işaretidir) — kanal belgesinde
    good = X.cell_rows({d: [1.0] * 6 for d in range(1, 6)}, rec=REC0, prefix="G", symbol="ETH/USDT:USDT")
    bad = X.cell_rows({d: [-1.0] * 2 for d in range(1, 6)}, rec=REC0, prefix="B")
    adv = _advice(good + bad + [X.target_row(as_of=AS_OF, rec=REC1)])
    coin = adv["real"]["coin"]
    assert coin["coin"] == "SOL/USDT" and coin["n"] == 10 and coin["mean_r"] == -1.0 and coin["k"] == 20
    assert adv["real"]["mean_r"] == 0.5 and coin["shrunk_mean_r"] == 0.0 and coin["dissent"] is True
    bad9 = X.cell_rows({1: [-1.0] * 1, 2: [-1.0] * 2, 3: [-1.0] * 2, 4: [-1.0] * 2, 5: [-1.0] * 2}, rec=REC0, prefix="B")
    adv = _advice(good + bad9 + [X.target_row(as_of=AS_OF, rec=REC1)])
    assert adv["real"]["coin"]["n"] == 9 and adv["real"]["coin"]["dissent"] is False
    # büzülme etiketi DEĞİŞTİRMEZ: coin kötü (itiraz) ama etiket yalnız havuz aralığından gelir
    adv = _advice(good + bad + [X.target_row(as_of=AS_OF, rec=REC1)])
    assert adv["real"]["coin"]["dissent"] is True
    assert (adv["real"]["advice"], adv["real"]["reason"]) == A.label_for_ci(*adv["real"]["ci95"]) == ("GIR", "CI_ABOVE_0")


# ============================================================================ kanallar
def test_cf_evidence_never_changes_the_primary_advice_and_vice_versa():
    real_ev = X.cell_rows(X.GOLDEN_B, rec=REC0, prefix="R")
    cf_ev = X.cell_rows({d: [-1.0] * 10 for d in range(1, 6)}, rec=REC0, prefix="C", kind="cf")
    tgt = [X.target_row(as_of=AS_OF, rec=REC1)]
    only_real = _advice(real_ev + tgt)
    both = _advice(real_ev + cf_ev + tgt)
    assert both["real"] == only_real["real"] and both["advice"] == "GIR"
    assert both["cf"]["advice"] == "GIRME" and only_real["cf"]["advice"] == "VERI_AZ"
    only_cf = _advice(cf_ev + tgt)
    assert only_cf["cf"] == both["cf"]
    assert A.PRIMARY_CHANNEL == "real" and A.CF_WEIGHT_IN_PRIMARY == 0


@pytest.mark.parametrize("lv,counted", [("cf_label_v3", True), ("cf_label_v2", False), ("cf_label_v1", False),
                                        ("cf_label_v1c", False), (None, False), ("cf_label_v4", False)])
def test_only_cf_label_v3_is_cf_evidence(lv, counted):
    assert A.CF_LABEL_VERSIONS == ("cf_label_v3",)
    rows = []
    for d in range(1, 6):
        for j in range(6):
            as_of = (X.T0_MS // X.DAY) * X.DAY - 20 * X.DAY + d * X.DAY + j * 600_000
            rows.append(X.cf("strategy_paper_box", "c%d_%d" % (d, j), as_of, rec=REC0, status=R.LABELLED, r=-1.0,
                             labeled_ms=as_of + X.HOUR, lv=lv, setup="box_fade"))
    adv = _advice(rows + [X.target_row(as_of=AS_OF, rec=REC1)])
    assert (adv["cf"]["advice"] == "GIRME") is counted
    assert adv["cf"]["visible_evidence"] == (30 if counted else 0)


# ============================================================================ tek kaynaklar
def test_single_sources_are_the_report_and_situation_functions(monkeypatch):
    assert A.MIN_N == XR.MIN_N_VERDICT and A.MIN_CLUSTERS == XR.MIN_CLUSTERS and A.SHRINK_K == XR.SHRINK_K
    rows = X.cell_rows(X.GOLDEN_A, rec=REC0) + [X.target_row(as_of=AS_OF, rec=REC1)]
    assert _advice(rows)["target"]["coin"] == "SOL/USDT"
    monkeypatch.setattr(XR, "coin_of", lambda s: "PATCHED")
    assert _advice(rows)["target"]["coin"] == "PATCHED"
    monkeypatch.undo()
    real = dict(S.bucket(X.snap(), "LONG"))
    monkeypatch.setattr(S, "bucket", lambda snap, side: {**real, "trend": "RANGE"})
    assert _advice(rows)["target"]["dims"]["trend"] == "RANGE"
    assert A.DIMS is XR.DIMS and A.BACKOFF_ORDER is XR.BACKOFF_ORDER


# ============================================================================ ön kayıttaki mühür (dağıtım değişmezi 1)
def test_preregistration_seal_table_equals_the_code():
    """DANISMAN_V1.md §7 "Mühür": ADVISOR_SHA, WF_SHA ve situation_v1 SCHEMA_SHA koddakilerle AYNI (belge ile kod
    ayrışırsa danışman canlıya alınamaz; kural değişikliği advisor_v2 + yeni ön kayıt ister)."""
    import re

    from tradingbot.shared_experience import advisor_eval as AE
    doc = (Path(__file__).resolve().parents[1] / "docs" / "ortak_deneyim" / "DANISMAN_V1.md").read_text(encoding="utf-8")
    seal = doc[doc.index("## 7. Mühür"):]

    def val(name: str) -> str:
        m = re.search(r"^\| `%s` \| `([0-9a-f]{16})` \|$" % re.escape(name), seal, re.M)
        assert m, name
        return m.group(1)
    assert val("ADVISOR_SHA") == A.ADVISOR_SHA == PINNED_ADVISOR_SHA
    assert val("WF_SHA") == AE.WF_SHA
    assert val("situation_v1 SCHEMA_SHA") == S.SCHEMA_SHA
