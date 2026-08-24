"""Quant Evaluation V1 — üç yollu (train/validation/test) walk-forward testleri.

Kritik ispatlar:
* Validation verisini değiştirmek train fit parametrelerini DEĞİŞTİRMEZ.
* Test verisini değiştirmek seçilen adayı DEĞİŞTİRMEZ (test performansı değişebilir).
* Holdout verisini değiştirmek holdout öncesi hiçbir sonucu DEĞİŞTİRMEZ.
* Fold sınırlarında overlap yoktur; her sınırda purge+embargo uygulanır.
* Yetersiz veri fail-closed (fold skipped; sessiz "nötr sonuç" yok).
* `validation_days=0` eski iki yollu API'yi birebir korur (backward compatibility).
"""
from __future__ import annotations

import json

import pytest

from tradingbot.quant.attribution import group_metrics
from tradingbot.quant.walkforward import (assign_rows, fold_report, is_three_way, leakage_check,
                                          make_folds, run_three_way, validate_folds)

DAY = 86_400_000
T0 = 1_760_000_000_000


def _plan3(**over):
    kw = dict(mode="anchored", train_days=30, validation_days=10, test_days=10,
              purge_bars=6, embargo_bars=6, tf="4h", holdout_days=20)
    kw.update(over)
    return make_folds(T0, T0 + 200 * DAY, **kw)


def _row(ts_ms, r=1.0, symbol="ETH/USDT", regime="trend"):
    return {"ts_ms": ts_ms, "as_of_ms": ts_ms - 3_600_000, "symbol": symbol, "regime": regime,
            "r_multiple": r, "net_pnl": r * 30, "outcome_labeled": True,
            "is_counterfactual": False, "quality_flags": []}


# --------------------------------------------------------------- fold geometrisi

def test_three_way_windows_ordered_with_gaps_on_both_boundaries():
    plan = _plan3()
    assert is_three_way(plan) and plan["layout"] == "three_way"
    assert len(plan["folds"]) >= 3
    for w in plan["folds"]:
        b = w.bounds()
        gap = (6 + 6) * w.bar_ms
        assert b["train_start_ms"] < b["train_end_ms"] < b["val_start_ms"] < b["val_end_ms"] \
            < b["test_start_ms"] < b["test_end_ms"]
        assert b["val_start_ms"] - b["train_end_ms"] == gap      # train→validation boşluğu
        assert b["test_start_ms"] - b["val_end_ms"] == gap       # validation→test boşluğu
    validate_folds(plan)


def test_no_overlap_between_folds_and_holdout_isolated():
    plan = _plan3()
    tests = [(w.test_start, w.test_end) for w in plan["folds"]]
    for (s1, e1), (s2, e2) in zip(tests, tests[1:]):
        assert e1 <= s2                                          # test pencereleri örtüşmüyor
    hold = plan["holdout"]
    assert hold["locked"] is True
    for w in plan["folds"]:
        assert w.test_end <= hold["start_ms"] and w.val_end <= hold["start_ms"]


def test_insufficient_range_fails_closed():
    with pytest.raises(ValueError, match="yetersiz veri aralığı"):
        make_folds(T0, T0 + 25 * DAY, mode="anchored", train_days=30, validation_days=10,
                   test_days=10, tf="4h")
    with pytest.raises(ValueError):
        make_folds(T0, T0 + 200 * DAY, train_days=30, validation_days=-1, test_days=10, tf="4h")
    with pytest.raises(ValueError):                              # tf/bar_ms yok → fail-closed
        make_folds(T0, T0 + 200 * DAY, train_days=30, validation_days=10, test_days=10)


def test_backward_compatible_two_way_unchanged():
    plan2 = make_folds(T0, T0 + 200 * DAY, train_days=30, test_days=10, tf="4h", holdout_days=20)
    assert plan2["layout"] == "two_way" and not is_three_way(plan2)
    assert not hasattr(plan2["folds"][0], "val_start")            # eski WFWindow şeması
    asg = assign_rows([_row(plan2["folds"][0].train_start + DAY)], plan2)
    assert "validation" not in asg["folds"][0]                    # eski atama sözleşmesi korunur
    rep = fold_report(asg, plan2, lambda rs: group_metrics(rs, min_sample=1))
    assert rep["layout"] == "two_way"


# --------------------------------------------------------------- atama ve leakage

def test_assignment_separates_three_sets_and_purges_both_gaps():
    plan = _plan3()
    w = plan["folds"][0]
    rows = [_row(w.train_start + DAY), _row(w.train_end + w.bar_ms),        # train, purge1
            _row(w.val_start + DAY), _row(w.val_end + w.bar_ms),            # validation, purge2
            _row(w.test_start + DAY), _row(plan["holdout"]["start_ms"] + DAY)]
    asg = assign_rows(rows, plan)
    slot = asg["folds"][0]
    assert len(slot["train"]) == 1 and len(slot["validation"]) == 1 and len(slot["test"]) == 1
    assert asg["purged"] >= 2                                     # HER İKİ boşluk da purge edildi
    assert len(asg["holdout"]) == 1
    assert leakage_check(asg, plan)["passed"] is True


def test_leakage_check_flags_validation_and_test_violations():
    plan = _plan3()
    w = plan["folds"][0]
    asg = assign_rows([_row(w.val_start + DAY)], plan)
    asg["folds"][0]["test"].append(asg["folds"][0]["validation"][0])   # validation satırı test'e
    rep = leakage_check(asg, plan)
    assert rep["passed"] is False
    assert any("validation penceresine taşıyor" in v for v in rep["violations"])


# --------------------------------------------------------------- akış izolasyonu (kritik)

def _fit(rows):
    """Train-only fit: ortalama R ve örnek sayısı."""
    rs = [r["r_multiple"] for r in rows]
    return {"mean_r": round(sum(rs) / len(rs), 6), "n": len(rs)}


def _candidates(fit, rows):
    """Adaylar train'den doğar: eşik süzgeçleri."""
    return [{"id": "loose", "min_r": -99.0}, {"id": "strict", "min_r": 0.0},
            {"id": "very_strict", "min_r": 0.5, "train_mean": fit["mean_r"]}]


def _select(cands, val_rows):
    """Seçim YALNIZ validation'da: en yüksek ortalama R'yi veren aday (deterministik tie-break)."""
    def score(c):
        kept = [r["r_multiple"] for r in val_rows if r["r_multiple"] >= c["min_r"]]
        return (sum(kept) / len(kept)) if kept else -99.0
    return sorted(cands, key=lambda c: (-score(c), c["id"]))[0]


def _evaluate(chosen, test_rows):
    kept = [r for r in test_rows if r["r_multiple"] >= chosen["min_r"]]
    return group_metrics(kept, min_sample=1, seed=7)


def _dataset(plan, *, val_r=None, test_r=None, hold_r=None, only_last_fold=True):
    """Her fold için train/validation/test/holdout satırları üretir (deterministik).

    `val_r`/`test_r` varsayılan olarak YALNIZ SON fold'un penceresine uygulanır. Nedeni gerçek
    walk-forward semantiğidir: anchored modda fold i+1'in TRAIN penceresi, fold i'nin validation/
    test dönemini MEŞRU olarak kapsar (o tarihler artık geçmiştir). Dolayısıyla "validation train'i
    etkilemez" değişmezi FOLD İÇİ bir değişmezdir; son fold'da hiçbir sonraki fold kalmadığı için
    izolasyon saf biçimde ölçülebilir.
    """
    last = plan["folds"][-1].idx
    rows = []
    for w in plan["folds"]:
        target = (not only_last_fold) or (w.idx == last)
        for i in range(1, 13):
            rows.append(_row(w.train_start + i * 2 * DAY, r=1.0 if i % 3 else -0.9))
        for i in range(1, 9):
            base_val = 0.8 if i % 2 else -0.6
            rows.append(_row(w.val_start + i * DAY,
                             r=(val_r if (val_r is not None and target) else base_val)))
        for i in range(1, 9):
            base_test = 0.5 if i % 2 else -0.4
            rows.append(_row(w.test_start + i * DAY,
                             r=(test_r if (test_r is not None and target) else base_test)))
    h = plan["holdout"]
    for i in range(1, 9):
        rows.append(_row(h["start_ms"] + i * DAY, r=(hold_r if hold_r is not None else 0.3)))
    return rows


def _run(plan, rows):
    asg = assign_rows(rows, plan)
    return run_three_way(asg, plan) if False else run_three_way(
        plan, asg, fit_fn=_fit, candidates_fn=_candidates, select_fn=_select,
        evaluate_fn=_evaluate)


def test_no_train_row_falls_in_own_validation_or_test_window():
    """Yapısal değişmez: bir fold'un train kümesi kendi validation/test dönemini ASLA görmez."""
    plan = _plan3()
    asg = assign_rows(_dataset(plan), plan)
    for w, slot in zip(plan["folds"], asg["folds"]):
        for r in slot["train"]:
            assert r["ts_ms"] < w.train_end
            assert not (w.val_start <= r["ts_ms"] < w.val_end)
            assert not (w.test_start <= r["ts_ms"] < w.test_end)
        for r in slot["validation"]:
            assert r["ts_ms"] < w.test_start                       # seçim test'i göremez


def test_validation_change_cannot_alter_train_fit():
    plan = _plan3()
    base = _run(plan, _dataset(plan))
    # 0.4: son fold'un validation'ında "very_strict" adayı öne geçer → mutasyon gerçekten etkili.
    # (Son fold'un validation penceresi hiçbir fold'un TRAIN penceresine düşmez: bütün train'ler
    # en geç son fold'un train_end'inde biter, val_start = train_end + purge/embargo.)
    changed = _run(plan, _dataset(plan, val_r=0.4))
    fits_base = [f["fit"] for f in base["folds"]]
    fits_changed = [f["fit"] for f in changed["folds"]]
    assert fits_base == fits_changed                              # train fit AYNI
    assert json.dumps(fits_base, sort_keys=True) == json.dumps(fits_changed, sort_keys=True)
    assert base["folds"][-1]["selected"] != changed["folds"][-1]["selected"]


def test_test_change_cannot_alter_candidate_selection():
    plan = _plan3()
    base = _run(plan, _dataset(plan))
    changed = _run(plan, _dataset(plan, test_r=9.0))              # son fold test'i çok farklı
    assert [f["selected"] for f in base["folds"]] == [f["selected"] for f in changed["folds"]]
    assert [f["fit"] for f in base["folds"]] == [f["fit"] for f in changed["folds"]]
    # ama test performansı DEĞİŞEBİLİR — beklenen davranış
    m_base = [(f["test_metrics"] or {}).get("expectancy_r") for f in base["folds"]]
    m_changed = [(f["test_metrics"] or {}).get("expectancy_r") for f in changed["folds"]]
    assert m_base != m_changed


def test_holdout_change_cannot_alter_any_pre_holdout_result():
    plan = _plan3()
    base = _run(plan, _dataset(plan))
    changed = _run(plan, _dataset(plan, hold_r=-50.0))            # holdout felaket senaryosu
    strip = lambda rep: json.dumps([{k: v for k, v in f.items()} for f in rep["folds"]],  # noqa: E731
                                   sort_keys=True, default=str)
    assert strip(base) == strip(changed)                          # holdout öncesi HİÇBİR sonuç değişmedi
    assert base["oos_expectancy_r_by_fold"] == changed["oos_expectancy_r_by_fold"]
    assert base["holdout_used_in_selection"] is False


def test_flow_is_deterministic_and_reports_separate_dates():
    plan = _plan3()
    rows = _dataset(plan)
    a, b = _run(plan, rows), _run(plan, rows)
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    f0 = a["folds"][0]
    assert f0["windows"]["train"][1] < f0["windows"]["validation"][0]
    assert f0["windows"]["validation"][1] < f0["windows"]["test"][0]
    assert a["n_evaluated"] >= 1 and a["holdout_locked"] is True


def test_insufficient_fold_data_is_skipped_not_neutral():
    plan = _plan3()
    sparse = [_row(plan["folds"][0].train_start + DAY)]           # yalnız 1 train satırı
    asg = assign_rows(sparse, plan)
    rep = run_three_way(plan, asg, fit_fn=_fit, candidates_fn=_candidates,
                        select_fn=_select, evaluate_fn=_evaluate,
                        min_train=5, min_validation=3, min_test=3)
    assert rep["n_evaluated"] == 0 and rep["n_skipped"] == len(plan["folds"])
    assert all(f["reason"] == "insufficient_sample" for f in rep["folds"])
    assert all(f["test_metrics"] is None for f in rep["folds"])   # uydurma metrik YOK
    assert rep["oos_sign_consistency"] is None


def test_run_three_way_rejects_two_way_plan():
    plan2 = make_folds(T0, T0 + 200 * DAY, train_days=30, test_days=10, tf="4h")
    asg = assign_rows([], plan2)
    with pytest.raises(ValueError, match="three_way"):
        run_three_way(plan2, asg, fit_fn=_fit, candidates_fn=_candidates,
                      select_fn=_select, evaluate_fn=_evaluate)


def test_fold_report_shows_three_sets():
    plan = _plan3()
    asg = assign_rows(_dataset(plan), plan)
    rep = fold_report(asg, plan, lambda rs: group_metrics(rs, min_sample=1, seed=7))
    assert rep["layout"] == "three_way"
    f0 = rep["folds"][0]
    assert f0["n_train"] > 0 and f0["n_validation"] > 0 and f0["n_test"] > 0
    assert "validation" in f0 and "windows" in f0
