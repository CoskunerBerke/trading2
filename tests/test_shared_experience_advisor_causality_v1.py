# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN advisor_v1 — NEDENSELLİK (2026-09-29; SPEC_ADVISOR_V1 §4.1–4.3, §7 T-A2; DANISMAN_V1.md §2.3).

Görünürlük saati `avail = max(rec(değer), rec(bağlam), olay)` ve KESİN `avail < as_of`:
* A1–A14 düşmanca durumların her biri elle kurulmuş bir depo (tur ortası etiket, kilit meşgul gecikmesi, kesinleşmemiş
  revizyon, as_of'tan sonraki revizyon, tembel v1→v3, ertelenmiş giriş, taslak hedef, 80 sa GEÇ, geri doldurma,
  saat anomalisi, hedefin kendi sonucu, `label_ts` tuzağı, saniye eşitlikleri).
* KABA KUVVET KÂHİNİ: 200 rastgele depoda (sabit tohumlar) her hedef için tanımsal hesap (deponun tamamı taranır,
  `fractions.Fraction`) ile katlamanın kanal belgeleri BİREBİR aynı.
* MUTASYON: bir hedefin `as_of`'undan sonra görünen her şey (sonraki toplu yazımlar silinir/eklenir; aynı ve önceki
  toplu yazımlarda `avail ≥ as_of` olan değerler değişir) o hedefin `core_sha`sını DEĞİŞTİRMEZ.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import advisor_synth as X  # noqa: E402
from advisor_synth import A, R  # noqa: E402

MIN, HOUR, DAY, STEP = X.MIN, X.HOUR, X.DAY, X.STEP
TK = X.T0_MS                                   # k. turun başlangıcı (recorded_at, µs = 0)
BOX = "strategy_paper_box"


def rec(ms: int, us: int = 0) -> str:
    return X.iso_us(ms, us)


def base_cell(n_days: int = 5, per_day: int = 6, r: float = -1.0) -> list[dict]:
    """Hedefin S0 hücresini dolduran eski kanıt (geri doldurma toplu yazımı; her şeyi görünür)."""
    return X.cell_rows({d: [r] * per_day for d in range(1, n_days + 1)}, rec=rec(TK - 30 * DAY), prefix="BASE",
                       base_ms=TK - 29 * DAY)


def advice_for(rows: list[dict], key: str) -> dict:
    f, adv = X.fold_all(rows)
    hit = [a for a in adv if a["target"]["key"].split("|")[1] == key]
    assert len(hit) == 1, [a["target"]["key"] for a in adv]
    return hit[0]


def cf_target(cid: str, as_of: int, r_ms: int, **kw) -> dict:
    return X.cf(BOX, cid, as_of, rec=rec(r_ms), setup="box_fade", **kw)


def cf_label(cid: str, created: int, labeled: int, r_ms: int, *, r: float = 1.0, rev: int = 1, lv: str = "cf_label_v3",
             status: str = R.LABELLED) -> dict:
    return X.cf(BOX, cid, created, rec=rec(r_ms), rev=rev, status=status, r=r, labeled_ms=labeled, lv=lv,
                setup="box_fade")


def vis(adv: dict, ch: str = "cf") -> int:
    return adv[ch]["visible_evidence"]


# ============================================================================ A1 / A14 — saniye eşitliği, label_ts
@pytest.mark.parametrize("delta_s,visible", [(-1, 1), (0, 0), (1, 0)])
def test_a1_same_batch_label_one_second_before_equal_after(delta_s, visible):
    as_of = TK + 2 * MIN
    rows = [cf_target("ev", TK - 3 * HOUR, TK - STEP)]                           # bağlam önceki turda
    rows += [cf_label("ev", TK - 3 * HOUR, as_of + delta_s * 1000, TK),           # etiket bu turda (tur ortası)
             cf_target("tgt", as_of, TK)]
    assert vis(advice_for(rows, "tgt")) == visible


def test_a14_label_ts_before_as_of_but_labeled_after_is_excluded():
    as_of = TK + 3 * MIN
    created = TK - 30 * HOUR                                                      # label_ts = created + 24 sa < as_of
    rows = [cf_target("ev", created, TK - STEP),
            cf_label("ev", created, as_of + 60_000, TK), cf_target("tgt", as_of, TK)]
    assert rows[1]["label_ts"] < X.iso_s(as_of) and vis(advice_for(rows, "tgt")) == 0


# ============================================================================ A2 / A3 — yazım anı yalnız başına yetmez / geç yazım
def test_a2_mid_tour_label_after_the_target_is_excluded_although_recorded_at_is_earlier():
    rows = [cf_target("ev", TK - 2 * HOUR, TK - STEP),
            cf_label("ev", TK - 2 * HOUR, TK + 4 * MIN, TK), cf_target("tgt", TK + 1 * MIN, TK)]
    assert rows[1]["recorded_at"] < X.iso_s(TK + MIN)                             # yazım anı hedeften önce …
    assert vis(advice_for(rows, "tgt")) == 0                                       # … ama olay sonra: SIZINTI YOK


def test_a3_label_before_as_of_but_written_in_a_later_step_is_excluded():
    as_of = TK + 5 * MIN
    rows = [cf_target("ev", TK - 2 * HOUR, TK - STEP), cf_target("tgt", as_of, TK)]
    # kilit meşgul: etiket as_of − 10 dk'da oldu ama satır k+1. turda yazıldı; ertelenen bir hedef de k+1'de
    rows += [cf_label("ev", TK - 2 * HOUR, as_of - 10 * MIN, TK + STEP), cf_target("tgt2", as_of + MIN, TK + STEP)]
    assert vis(advice_for(rows, "tgt")) == 0 and vis(advice_for(rows, "tgt2")) == 0
    later = rows + [cf_target("tgt3", TK + STEP + MIN, TK + STEP)]
    assert vis(advice_for(later, "tgt3")) == 1


# ============================================================================ A4 / A5 — kesinleşme ve sonraki revizyonlar
def test_a4_non_final_revision_is_not_evidence_until_final():
    opened, closed = TK - 5 * HOUR, TK - 4 * HOUR
    rows = [X.entry(BOX, "T1", opened, rec=rec(TK - 2 * STEP), setup="box_fade"),
            X.outcome(BOX, "T1", opened, closed, -1.0, rec=rec(TK - STEP), rev=0, final=False, setup="box_fade"),
            X.target_row(tid="A", as_of=TK + MIN, rec=rec(TK)),
            X.outcome(BOX, "T1", opened, closed, -1.0, rec=rec(TK + STEP), rev=1, final=True, setup="box_fade"),
            X.target_row(tid="B", as_of=TK + STEP + MIN, rec=rec(TK + STEP))]
    assert vis(advice_for(rows, "A"), "real") == 0 and vis(advice_for(rows, "B"), "real") == 1


def test_a5_first_final_value_is_fixed_later_revisions_ignored():
    base = X.cell_rows({1: [0.0] * 9}, rec=rec(TK - 30 * DAY), prefix="Z", base_ms=TK - 29 * DAY)
    opened = TK - 6 * HOUR
    rows = base + [X.entry(BOX, "T1", opened, rec=rec(TK - STEP), setup="box_fade"),
                   X.outcome(BOX, "T1", opened, opened + HOUR, -1.0, rec=rec(TK - STEP), rev=0, final=True,
                             setup="box_fade"),
                   X.outcome(BOX, "T1", opened, opened + HOUR, 5.0, rec=rec(TK), rev=1, final=True, setup="box_fade"),
                   X.target_row(tid="A", as_of=TK + MIN, rec=rec(TK))]
    real = advice_for(rows, "A")["real"]
    s5 = [lv for lv in real["levels"] if lv[0] == "S5"][0]
    assert s5[1] == 10 and s5[3] == -0.1                                           # ilk kesin değer (−1), 5.0 DEĞİL


# ============================================================================ A6 — tembel v1 → v3 yeniden etiket
def test_a6_lazy_v3_relabel_counts_from_its_write_time_with_the_v3_value():
    created, lab = TK - 3 * DAY, TK - 2 * DAY
    rows = [cf_target("ev", created, TK - 3 * STEP),
            cf_label("ev", created, lab, TK - 2 * STEP, r=0.7, lv="cf_label_v1", rev=1),
            cf_target("t1", TK - STEP + MIN, TK - STEP),
            cf_label("ev", created, lab, TK, r=-0.4, lv="cf_label_v3", rev=2),         # labeled_at DEĞİŞMEDİ
            cf_target("t2", TK - MIN, TK), cf_target("t3", TK + STEP + MIN, TK + STEP)]
    assert vis(advice_for(rows, "t1")) == 0                                        # v1 kanıt DEĞİL
    assert vis(advice_for(rows, "t2")) == 0                                        # v3 yazımı as_of'tan sonra
    assert vis(advice_for(rows, "t3")) == 1


# ============================================================================ A7 / A8 / A9 / A10
def test_a7_outcome_before_deferred_entry_is_visible_only_from_the_entry_write():
    opened = TK - 5 * HOUR
    rows = [X.outcome(BOX, "T1", opened, opened + HOUR, -1.0, rec=rec(TK), setup="box_fade"),
            X.target_row(tid="A", as_of=TK + MIN, rec=rec(TK)),
            X.entry(BOX, "T1", opened, rec=rec(TK + 3 * STEP), setup="box_fade"),
            X.target_row(tid="B", as_of=TK + 3 * STEP - MIN, rec=rec(TK + 3 * STEP)),
            X.target_row(tid="C", as_of=TK + 3 * STEP + MIN, rec=rec(TK + 3 * STEP))]
    assert vis(advice_for(rows, "A"), "real") == 0
    assert vis(advice_for(rows, "B"), "real") == 0                                 # avail = rec(giriş) > as_of
    assert vis(advice_for(rows, "C"), "real") == 1
    f, _adv = X.fold_all(rows)
    assert f.counters["orphan_values"] == 1 and f.counters["evidence_real"] == 1


def test_a8_draft_target_materialised_late_subtracts_everything_written_after_its_as_of():
    as_of = TK - 30 * HOUR
    rows = []
    for i in range(12):                                                           # 12 kanıt: 6'sı as_of'tan önce
        t = TK - 36 * HOUR + i * HOUR
        rows += X.ev_cf(BOX, "e%d" % i, t - 2 * HOUR, -1.0, rec=rec(t), setup="box_fade", label_after=HOUR)
    rows.append(cf_target("tgt", as_of, TK))
    adv = advice_for(rows, "tgt")
    assert adv["state"] == "OK" and vis(adv) == 6


def test_a9_target_older_than_72h_is_late_with_no_advice():
    rows = [cf_target("old", TK - 80 * HOUR, TK), cf_target("edge", TK - 72 * HOUR, TK),
            cf_target("edge2", TK - 72 * HOUR - 1000, TK)]
    assert advice_for(rows, "old")["state"] == "LATE" and advice_for(rows, "old")["advice"] is None
    assert advice_for(rows, "old")["real"] is None and advice_for(rows, "old")["cf"] is None
    assert advice_for(rows, "edge")["state"] == "OK" and advice_for(rows, "edge2")["state"] == "LATE"


def test_a10_backfill_evidence_is_invisible_to_targets_before_its_write():
    rows = [cf_target("pre", TK - HOUR, TK - 2 * STEP)]
    rows += X.cell_rows({d: [-1.0] * 6 for d in range(1, 6)}, rec=rec(TK - STEP), prefix="BF", kind="cf",
                        base_ms=TK - 20 * DAY)
    rows += [cf_target("draft", TK - STEP - HOUR, TK), cf_target("post", TK + MIN, TK)]
    assert vis(advice_for(rows, "pre")) == 0 and vis(advice_for(rows, "draft")) == 0
    assert vis(advice_for(rows, "post")) == 30 and advice_for(rows, "post")["cf"]["advice"] == "GIRME"


# ============================================================================ A12 / A13
def test_a12_clock_anomalies_are_excluded_and_counted():
    opened = TK - 5 * HOUR
    rows = [X.entry(BOX, "T1", opened, rec=rec(TK - STEP), setup="box_fade"),
            X.outcome(BOX, "T1", opened, opened - 1000, -1.0, rec=rec(TK - STEP), setup="box_fade"),
            cf_target("c1", TK - 4 * HOUR, TK - STEP), cf_label("c1", TK - 4 * HOUR, TK - 5 * HOUR, TK - STEP),
            X.target_row(tid="A", as_of=TK + MIN, rec=rec(TK))]
    adv = advice_for(rows, "A")
    f, _ = X.fold_all(rows)
    assert vis(adv, "real") == 0 and vis(adv, "cf") == 0 and f.counters["clock_anomaly"] == 2


def test_a13_the_targets_own_outcome_is_never_its_evidence():
    as_of = TK + MIN
    rows = [X.cf(BOX, "self", as_of, rec=rec(TK), status=R.LABELLED, r=-1.0, labeled_ms=as_of, setup="box_fade")]
    adv = advice_for(rows, "self")
    f, _ = X.fold_all(rows)
    assert vis(adv) == 0 and f.counters["evidence_cf"] == 1 and f.counters["resolved"] == 1


# ============================================================================ A15 — duvar saati GERİ adımı (2026-09-30)
def _step_rows(y_as_of: int, *, z_as_of: int | None = None, stepped: bool = True) -> list[dict]:
    """Saat gerçek TK'da 1 sa geri atar, ~15 dk yanlış kalır. X gerçek TK+5 dk'da kapanır (damga TK−55 dk), gerçek TK+10
    dk'da TK−50 dk damgalı bir toplu yazımda yazılır. Saat düzeldikten sonra (TK+20 dk) taslak hedef(ler) yazılır.
    `stepped=False`: aynı sonuç doğru saatle (TK+10 dk) yazılır (kontrol)."""
    ox = TK - 3 * HOUR
    rows = [X.entry(BOX, "X", ox, rec=rec(TK - 3 * HOUR), setup="box_fade"),
            X.entry(BOX, "P", TK - 21 * MIN, rec=rec(TK - 20 * MIN), setup="box_fade"),
            X.entry(BOX, "Q", TK - 11 * MIN, rec=rec(TK - 10 * MIN), setup="box_fade")]
    if stepped:
        rows.append(X.outcome(BOX, "X", ox, TK - 55 * MIN, 3.0, rec=rec(TK - 50 * MIN), setup="box_fade"))
    else:
        rows.append(X.outcome(BOX, "X", ox, TK + 5 * MIN, 3.0, rec=rec(TK + 10 * MIN), setup="box_fade"))
    rows.append(X.target_row(tid="Y", as_of=y_as_of, rec=rec(TK + 20 * MIN)))
    if z_as_of is not None:
        rows.append(X.target_row(tid="Z", as_of=z_as_of, rec=rec(TK + 20 * MIN)))
    return rows


@pytest.mark.parametrize("y_min", [-30, -5])
def test_a15_backward_wall_clock_step_cannot_leak_a_later_outcome_into_a_pre_step_target(y_min):
    """İnceleme bulgusu (E1b): Y adımdan ÖNCE (gerçek TK−30 dk, ya da son normal toplu yazımla adım arasında TK−5 dk)
    açıldı; X ondan SONRA kapandı → Y'ye kanıt OLAMAZ. Eskiden yazım anı toplu yazımın kendi (geri) `recorded_at`'iydi
    (avail_X = TK−50 dk < as_of_Y → sızıntı; Y anomali bayrağı taşımıyordu, H1'e uygundu). Yalnız monoton saat de
    yetmezdi: avail_X = TK−10 dk < TK−5 dk. Şimdi saati ilerletmeyen toplu yazımdaki kanıt saati ilerleten sonraki
    toplu yazıma (TK+20 dk) kadar bekler → iki durumda da görünmez; o toplu yazımın tur ortası hedefi (TK+25 dk) görür."""
    rows = _step_rows(TK + y_min * MIN, z_as_of=TK + 25 * MIN)
    f, adv = X.fold_all(rows, born_ms=TK - HOUR)
    by = {a["target"]["key"].split("|")[1]: a for a in adv}
    y, z = by["Y"], by["Z"]
    assert f.counters["clock_nonincreasing"] == 1 and f.counters["clock_held_evidence"] == 1
    assert f.counters["evidence_real"] == 1 and f.stats()["held_evidence"] == 0
    assert y["state"] == "OK" and y["clock_anomaly"] is False and y["prospective"] is True     # H1'e uygun hedef …
    assert vis(y, "real") == 0                                                                  # … ama SIZINTI YOK
    assert vis(z, "real") == 1                             # bekleyen kanıt saati ilerleten toplu yazımdan sonra görünür
    orc = X.oracle(rows)
    assert X.channel_view(y["real"]) == orc[y["target"]["key"]]["real"]
    assert X.channel_view(z["real"]) == orc[z["target"]["key"]]["real"]
    # kontrol: aynı sonuç doğru saatle (gerçek TK+10 dk) yazılsaydı Y yine görmezdi → Y'nin tavsiyesi AYNI
    _g, adv2 = X.fold_all(_step_rows(TK + y_min * MIN, z_as_of=TK + 25 * MIN, stepped=False), born_ms=TK - HOUR)
    y2 = [a for a in adv2 if a["target"]["key"] == y["target"]["key"]][0]
    assert y2["real"] == y["real"]


def test_a15b_ten_hour_step_evidence_waits_for_the_next_advancing_batch_and_survives_a_snapshot():
    """Geriye 10 sa damgalı toplu yazımdaki kesin sonuç (+ bir hedef) → bekler; anlık görüntüden geri yükleme bekleyeni
    korur; saati ilerleten sonraki toplu yazımın 5 sa önceki taslak hedefi görmez, tur ortası hedefi görür."""
    ox = TK - 40 * HOUR
    head = [X.entry(BOX, "X", ox, rec=rec(TK - 30 * HOUR), setup="box_fade"),
            X.entry(BOX, "A", TK - MIN, rec=rec(TK), setup="box_fade"),
            X.entry(BOX, "B", TK + STEP - MIN, rec=rec(TK + STEP), setup="box_fade"),
            X.outcome(BOX, "X", ox, TK - 11 * HOUR, 5.0, rec=rec(TK - 10 * HOUR), setup="box_fade"),
            X.target_row(tid="V", as_of=TK - 10 * HOUR + MIN, rec=rec(TK - 10 * HOUR))]
    tail = [X.target_row(tid="Y", as_of=TK - 5 * HOUR, rec=rec(TK + 2 * STEP)),
            X.target_row(tid="W", as_of=TK + 2 * STEP + MIN, rec=rec(TK + 2 * STEP))]
    f = A.AdvisorFold()
    for b in X.batches(head):
        f.fold_batch(b)
    assert f.stats()["held_evidence"] == 1 and f.counters["clock_held_evidence"] == 1
    g = A.AdvisorFold.from_state(f.state_bytes())
    assert g.stats()["held_evidence"] == 1 and g.state_bytes() == f.state_bytes()
    outs = []
    for fold in (f, g):
        adv = []
        for b in X.batches(tail):
            adv += fold.fold_batch(b).advice_rows
        outs.append({a["target"]["key"].split("|")[1]: a for a in adv})
    assert outs[0] == outs[1]
    assert vis(outs[0]["Y"], "real") == 0 and outs[0]["Y"]["clock_anomaly"] is False
    assert vis(outs[0]["W"], "real") == 1 and f.stats()["held_evidence"] == 0
    whole = {a["target"]["key"].split("|")[1]: a for a in X.fold_all(head + tail)[1]}
    assert whole["V"]["clock_anomaly"] is True and vis(whole["V"], "real") == 0


@pytest.mark.parametrize("chunk", range(2))
def test_brute_force_oracle_matches_the_fold_with_backward_clock_steps(chunk):
    """Kâhin (yazım anı = monoton toplu yazım saati) ↔ katlama, turların ~%20'sinde duvar saati geri adımıyla."""
    n_ok = n_steps = 0
    for seed in range(1000 + chunk * 40, 1000 + (chunk + 1) * 40):
        rows = X.random_store(seed, clock_steps=True)
        f, adv = X.fold_all(rows)
        n_steps += f.counters["clock_nonincreasing"]
        orc = X.oracle(rows)
        assert len(adv) == len(orc), seed
        for a in adv:
            o = orc[a["target"]["key"]]
            assert a["state"] == o["state"], (seed, a["target"]["key"])
            if a["state"] != "OK":
                continue
            n_ok += 1
            for ch in ("real", "cf"):
                assert X.channel_view(a[ch]) == o[ch], (seed, a["target"]["key"], ch)
    assert n_ok > 200 and n_steps > 60


# ============================================================================ A11 — kâhin ve mutasyon
@pytest.mark.parametrize("chunk", range(4))
def test_brute_force_oracle_matches_the_fold_on_200_random_stores(chunk):
    n_suff = n_ok = 0
    for seed in range(chunk * 50, (chunk + 1) * 50):
        rows = X.random_store(seed)
        _f, adv = X.fold_all(rows)
        orc = X.oracle(rows)
        assert len(adv) == len(orc), seed
        for a in adv:
            o = orc[a["target"]["key"]]
            assert a["state"] == o["state"], (seed, a["target"]["key"])
            if a["state"] != "OK":
                assert a["real"] is None and a["cf"] is None
                continue
            n_ok += 1
            for ch in ("real", "cf"):
                assert X.channel_view(a[ch]) == o[ch], (seed, a["target"]["key"], ch)
                n_suff += a[ch]["level"] is not None
    assert n_ok > 500 and n_suff > 300


def _mutate_after(rows: list[dict], target_key: str, rng: random.Random) -> list[dict]:
    """Hedefin toplu yazımından sonrasını sil/ekle; aynı ve önceki toplu yazımlarda avail ≥ as_of olan değerleri boz."""
    bs = X.batches(rows)
    bi = next(i for i, b in enumerate(bs) for r in b
              if (r.get("cf_key") or r.get("trade_key")) == target_key and (r["kind"] == "xp_entry" or r["rev"] == 0))
    tgt = next(r for r in bs[bi] if (r.get("cf_key") or r.get("trade_key")) == target_key)
    as_of = A.ms_exact(tgt.get("created_at") or tgt.get("opened_at"))
    out: list[dict] = []
    for b in bs[:bi + 1]:
        for r in b:
            r = dict(r)
            ev = A.ms_exact(r.get("labeled_at") if r["kind"] == "xp_cf" else r.get("closed_at"))
            if (r["kind"] != "xp_entry" and isinstance(r.get("r_net"), float) and ev is not None
                    and max(ev, A.ms_exact(r["recorded_at"])) >= as_of):
                r["r_net"] = rng.choice((-7.5, 3.25, 0.0))                       # görünmez değer: sonuç değişmemeli
            out.append(r)
    extra = X.random_store(rng.randrange(10_000, 20_000))
    shift = A.ms_exact(bs[bi][0]["recorded_at"]) + STEP
    for r in extra[-60:]:
        r = dict(r)
        r["recorded_at"] = X.iso_us(shift + 7, 5)
        out.append(r)
    return out


def test_mutations_after_as_of_leave_the_target_core_unchanged():
    rng = random.Random(4242)
    checked = 0
    for seed in range(0, 90, 3):
        rows = X.random_store(seed)
        _f, adv = X.fold_all(rows)
        ok = [a for a in adv if a["state"] == "OK"]
        for a in rng.sample(ok, min(3, len(ok))):
            mut = _mutate_after(rows, a["target"]["key"], rng)
            _g, adv2 = X.fold_all(mut)
            b = [x for x in adv2 if x["target"]["key"] == a["target"]["key"]]
            assert len(b) == 1 and b[0]["core_sha"] == a["core_sha"], (seed, a["target"]["key"])
            checked += 1
    assert checked >= 60
