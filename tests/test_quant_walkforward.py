"""Quant Evaluation V1 — walk-forward ve leakage koruması testleri.

Kapsam: kronolojik fold'lar, anchored/rolling ayrımı, purge/embargo boşlukları, kilitli holdout,
satırların yalnız zamana göre atanması, train-only sınırı, gelecek-verisi (as_of) denetimi,
yetersiz fold/aday → PBO hesaplanamadı, deterministik rapor.
"""
from __future__ import annotations

import pytest

from tradingbot.quant.attribution import group_metrics
from tradingbot.quant.walkforward import (SCHEMA_VERSION, assign_rows, fold_report,
                                          leakage_check, make_folds, validate_folds)

DAY = 86_400_000
T0 = 1_760_000_000_000                     # sabit epoch ms — determinizm


def _plan(**over):
    kw = dict(mode="anchored", train_days=30, test_days=10, purge_bars=6, embargo_bars=6,
              tf="4h", holdout_days=15)
    kw.update(over)
    return make_folds(T0, T0 + 120 * DAY, **kw)


def _row(ts_ms, r=1.0, symbol="ETH/USDT", regime="trend", as_of=None):
    return {"ts_ms": ts_ms, "as_of_ms": as_of if as_of is not None else ts_ms - 3_600_000,
            "symbol": symbol, "regime": regime, "r_multiple": r, "net_pnl": r * 30,
            "outcome_labeled": True, "is_counterfactual": False, "quality_flags": []}


def test_chronological_folds_and_gap():
    plan = _plan()
    assert plan["schema_version"] == SCHEMA_VERSION and len(plan["folds"]) >= 4
    for w in plan["folds"]:
        b = w.bounds()
        assert b["train_start_ms"] < b["train_end_ms"] < b["test_start_ms"] < b["test_end_ms"]
        assert b["test_start_ms"] - b["train_end_ms"] == (6 + 6) * w.bar_ms
    # anchored: bütün train'ler aynı başlangıçta
    assert len({w.train_start for w in plan["folds"]}) == 1


def test_rolling_mode_slides_train_start():
    plan = _plan(mode="rolling")
    starts = [w.train_start for w in plan["folds"]]
    assert starts == sorted(starts) and len(set(starts)) > 1
    for w in plan["folds"]:
        assert w.train_end - w.train_start <= 30 * DAY
    validate_folds(plan)


def test_locked_holdout_is_disjoint_and_marked():
    plan = _plan()
    hold = plan["holdout"]
    assert hold["locked"] is True
    for w in plan["folds"]:
        assert w.test_end <= hold["start_ms"]
    with pytest.raises(ValueError):
        make_folds(T0, T0 + 10 * DAY, train_days=30, test_days=10, tf="4h", holdout_days=20)


def test_invalid_mode_and_negative_holdout_fail_closed():
    with pytest.raises(ValueError):
        _plan(mode="shuffle")
    with pytest.raises(ValueError):
        _plan(holdout_days=-1)
    with pytest.raises(ValueError):
        make_folds(T0, T0 + 120 * DAY, train_days=30, test_days=10)   # tf/bar_ms yok → fail


def test_assignment_by_time_only_purge_and_holdout():
    plan = _plan()
    w0 = plan["folds"][0]
    rows = [_row(w0.train_start + DAY),                       # train
            _row(w0.train_end + w0.bar_ms),                   # purge/embargo bölgesi
            _row(w0.test_start + DAY),                        # test
            _row(plan["holdout"]["start_ms"] + DAY),          # kilitli holdout
            {"symbol": "X", "r_multiple": 1.0}]               # zamansız
    asg = assign_rows(rows, plan)
    assert len(asg["folds"][0]["train"]) == 1
    assert len(asg["folds"][0]["test"]) == 1
    assert asg["purged"] >= 1 and len(asg["holdout"]) == 1
    assert asg["unassigned"] == 1                             # zamansız satır train'e SIZMADI


def test_leakage_check_flags_future_as_of_and_holdout_mixing():
    plan = _plan()
    w0 = plan["folds"][0]
    good = _row(w0.train_start + DAY)
    future = _row(w0.test_start + DAY, as_of=w0.test_start + 2 * DAY)  # as_of karar SONRASI
    asg = assign_rows([good, future], plan)
    rep = leakage_check(asg, plan)
    assert rep["passed"] is False
    assert any("gelecek verisi" in v for v in rep["violations"])
    clean = assign_rows([good, _row(w0.test_start + DAY)], plan)
    assert leakage_check(clean, plan)["passed"] is True
    # holdout satırı el ile fold'a sokulursa yakalanır
    bad = assign_rows([_row(plan["holdout"]["start_ms"] + DAY)], plan)
    bad["folds"][0]["test"].append(bad["holdout"][0])
    assert leakage_check(bad, plan)["passed"] is False


def test_fold_report_parameters_stability_and_pbo_unavailable():
    plan = _plan()
    rows = []
    for w in plan["folds"]:
        rows += [_row(w.train_start + i * DAY, r=1.0 if i % 3 else -0.8) for i in range(1, 25)]
        rows += [_row(w.test_start + i * 6 * 3_600_000, r=0.6 if i % 4 else -0.5) for i in range(1, 30)]
    asg = assign_rows(rows, plan)
    rep = fold_report(asg, plan, lambda rs: group_metrics(rs, min_sample=5, seed=7),
                      params_tried=3, n_candidates=1)
    assert rep["n_folds"] == len(plan["folds"]) and rep["params_tried"] == 3
    assert rep["holdout_locked"] is True
    assert rep["oos_sign_consistency"] is not None
    assert rep["pbo"] is None and rep["pbo_state"] in ("not_computable", "requires_candidate_matrix")
    rep2 = fold_report(asg, plan, lambda rs: group_metrics(rs, min_sample=5, seed=7),
                       params_tried=3, n_candidates=1)
    assert rep == rep2                                        # determinizm
