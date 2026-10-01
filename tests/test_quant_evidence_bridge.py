"""Quant Evaluation V1 — policy_eval/walk-forward → champion evidence bridge testleri.

Kritik ispatlar:
* Kanıt paketi gerçek araştırma çıktılarından (walk-forward fold raporu, attribution metrikleri,
  leakage/data-quality/isolation kanıtları, senaryo raporu) normalize edilir; hiçbir alan uydurulmaz.
* Kritik kanıt EKSİK → `KEEP_CHAMPION` (bilinmiyor ≠ geçti).
* Leakage / izolasyon / veri kalitesi / maliyet modeli AÇIKÇA başarısız → `REJECT_CHALLENGER`.
* Challenger daha kârlı olsa bile drawdown/tail/yoğunlaşma/senaryo/coverage kapısı düşerse terfi yok.
* `PROMOTE_CANDIDATE` yalnız araştırma önerisidir; hiçbir config/ledger/worker durumu değişmez.
"""
from __future__ import annotations

import pytest

from tradingbot.quant.attribution import group_metrics
from tradingbot.quant.champion import KEEP_CHAMPION, PROMOTE_CANDIDATE, REJECT_CHALLENGER
from tradingbot.quant.evidence import (CRITICAL_EVIDENCE, build_evidence, evaluate_with_evidence)
from tradingbot.quant.walkforward import assign_rows, fold_report, make_folds

DAY = 86_400_000
T0 = 1_760_000_000_000


def _metrics(n=200, exp=0.2, dd=-3.0, tail=-1.2, sym=0.2, trade=0.1, ci=(0.05, 0.4),
             payoff=1.5, win_rate=0.5):
    return {"n": n, "insufficient_sample": False, "expectancy_r": exp, "max_drawdown_r": dd,
            "tail_loss_r_cvar5": tail, "payoff_ratio": payoff, "win_rate": win_rate,
            "calibration": {"brier": 0.2, "n": n, "state": "ok"},
            "bootstrap_ci_mean_r": {"state": "ok", "low": ci[0], "high": ci[1]},
            "concentration": {"top_symbol_share": sym, "top_trade_share": trade}}


def _wf_real():
    """GERÇEK walk-forward fold raporu (sentetik satırlar, üretim kod yolu)."""
    plan = make_folds(T0, T0 + 200 * DAY, train_days=30, test_days=10, tf="4h", holdout_days=20)
    rows = []
    for w in plan["folds"]:
        for i in range(1, 16):
            rows.append({"ts_ms": w.train_start + i * DAY, "as_of_ms": w.train_start + i * DAY - 1000,
                         "symbol": "ETH/USDT" if i % 2 else "SOL/USDT",
                         "regime": "trend_up" if i % 3 else "range",
                         "r_multiple": 1.0 if i % 3 else -0.8, "net_pnl": 30.0 if i % 3 else -24.0,
                         "outcome_labeled": True, "is_counterfactual": False, "quality_flags": []})
        for i in range(1, 12):
            rows.append({"ts_ms": w.test_start + i * 6 * 3_600_000,
                         "as_of_ms": w.test_start + i * 6 * 3_600_000 - 1000,
                         "symbol": "ETH/USDT" if i % 2 else "SOL/USDT",
                         "regime": "trend_up" if i % 2 else "range",
                         "r_multiple": 0.6 if i % 4 else -0.4, "net_pnl": 18.0 if i % 4 else -12.0,
                         "outcome_labeled": True, "is_counterfactual": False, "quality_flags": []})
    asg = assign_rows(rows, plan)
    return fold_report(asg, plan, lambda rs: group_metrics(rs, min_sample=5, seed=7))


_PROOFS = dict(leakage={"passed": True, "n_violations": 0},
               data_quality={"passed": True, "verdict": "OK"},
               isolation={"passed": True, "detail": "ana ledger/outbox/gateway dokunulmadı"},
               cost_model_equal=True,
               scenarios={"robust_across_scenarios": True, "verdict": "bütün senaryolarda pozitif"},
               coverage={"gates_passed": True, "verdict": "kapsama yeterli"})


def test_bundle_normalizes_real_walk_forward_output():
    wf = _wf_real()
    ev = build_evidence(champion_metrics=_metrics(exp=0.05), challenger_metrics=_metrics(exp=0.2),
                        walk_forward=wf, **_PROOFS)
    assert ev["schema_version"] == "quant_evidence_v1"
    assert ev["complete"] is True and ev["missing_critical"] == []
    assert ev["champion_n"] == 200 and ev["challenger_n"] == 200
    assert ev["expectancy_delta_r"] == pytest.approx(0.15)
    assert ev["confidence_interval"]["state"] == "ok"
    assert ev["fold_sign_consistency"] == wf["oos_sign_consistency"]
    assert ev["regime_coverage"]["n_symbols"] >= 2          # gerçek fold çıktısından türedi
    assert ev["pbo_state"] == wf["pbo_state"]
    assert ev["execution_provenance"] is None               # verilmedi → uydurulmadı


def test_missing_critical_evidence_keeps_champion():
    for drop in ("leakage", "data_quality", "isolation", "cost_model_equal"):
        kw = dict(_PROOFS)
        kw[drop] = None
        ev = build_evidence(champion_metrics=_metrics(exp=0.05),
                            challenger_metrics=_metrics(exp=0.2), walk_forward=_wf_real(), **kw)
        assert ev["complete"] is False
        out = evaluate_with_evidence(ev)
        assert out["decision"] == KEEP_CHAMPION, drop
        assert out["missing_critical"], drop
        assert out["auto_promotion"] is False


def test_missing_metrics_entirely_keeps_champion():
    ev = build_evidence(**_PROOFS)
    assert set(ev["missing_critical"]) >= {"champion_metrics", "challenger_metrics"}
    out = evaluate_with_evidence(ev)
    assert out["decision"] == KEEP_CHAMPION
    assert set(CRITICAL_EVIDENCE) >= {"leakage_passed", "isolation_verified"}


@pytest.mark.parametrize("bad", ["leakage", "data_quality", "isolation", "cost_model_equal"])
def test_explicit_failure_rejects_challenger(bad):
    kw = dict(_PROOFS)
    kw[bad] = False if bad == "cost_model_equal" else {"passed": False, "verdict": "FAIL"}
    ev = build_evidence(champion_metrics=_metrics(exp=0.05), challenger_metrics=_metrics(exp=0.9),
                        walk_forward=_wf_real(), **kw)
    out = evaluate_with_evidence(ev)
    assert out["decision"] == REJECT_CHALLENGER            # daha kârlı olsa bile reddedilir
    assert out["applies_changes"] is False


def test_all_gates_pass_gives_research_recommendation_only():
    ev = build_evidence(champion_metrics=_metrics(exp=0.05), challenger_metrics=_metrics(exp=0.25),
                        walk_forward=_wf_real(), **_PROOFS)
    out = evaluate_with_evidence(ev)
    assert out["decision"] == PROMOTE_CANDIDATE
    assert out["auto_promotion"] is False and out["applies_changes"] is False
    assert "TEST DATA" in out["label"]
    assert out["evidence_summary"]["expectancy_delta_r"] == pytest.approx(0.2)


def test_more_profitable_but_worse_risk_is_not_promoted():
    wf = _wf_real()
    champ = _metrics(exp=0.05, dd=-3.0, tail=-1.2)
    cases = {
        "drawdown": _metrics(exp=0.9, dd=-30.0),
        "tail": _metrics(exp=0.9, tail=-9.0),
        "symbol_concentration": _metrics(exp=0.9, sym=0.95),
        "trade_concentration": _metrics(exp=0.9, trade=0.8),
    }
    for name, ch in cases.items():
        out = evaluate_with_evidence(build_evidence(champion_metrics=champ, challenger_metrics=ch,
                                                    walk_forward=wf, **_PROOFS))
        assert out["decision"] == KEEP_CHAMPION, name


def test_scenario_fragility_blocks_promotion():
    kw = dict(_PROOFS)
    kw["scenarios"] = {"robust_across_scenarios": False,
                       "verdict": "avantaj şu senaryolarda kayboluyor: stress"}
    out = evaluate_with_evidence(build_evidence(champion_metrics=_metrics(exp=0.05),
                                                challenger_metrics=_metrics(exp=0.9),
                                                walk_forward=_wf_real(), **kw))
    assert out["decision"] == KEEP_CHAMPION
    assert any(c["code"] == "SCENARIO_ROBUSTNESS" and not c["passed"] for c in out["checks"])


def test_low_journal_coverage_blocks_promotion():
    kw = dict(_PROOFS)
    kw["coverage"] = {"gates_passed": False, "verdict": "feature snapshot kapsaması %10"}
    out = evaluate_with_evidence(build_evidence(champion_metrics=_metrics(exp=0.05),
                                                challenger_metrics=_metrics(exp=0.9),
                                                walk_forward=_wf_real(), **kw))
    assert out["decision"] == KEEP_CHAMPION
    assert any(c["code"] == "JOURNAL_COVERAGE" and not c["passed"] for c in out["checks"])


def test_narrow_regime_coverage_blocks_promotion():
    thin_wf = {"folds": [{"regimes_test": ["trend_up"], "symbols_test": ["ETH/USDT"]}],
               "oos_sign_consistency": 0.9, "pbo_state": "not_computable"}
    out = evaluate_with_evidence(build_evidence(champion_metrics=_metrics(exp=0.05),
                                                challenger_metrics=_metrics(exp=0.9),
                                                walk_forward=thin_wf, **_PROOFS))
    assert out["decision"] == KEEP_CHAMPION
    assert any(c["code"] == "REGIME_COVERAGE" and not c["passed"] for c in out["checks"])


def test_bridge_is_deterministic_and_pure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ev = build_evidence(champion_metrics=_metrics(exp=0.05), challenger_metrics=_metrics(exp=0.25),
                        walk_forward=_wf_real(), **_PROOFS)
    a, b = evaluate_with_evidence(ev), evaluate_with_evidence(ev)
    assert a == b
    assert list(tmp_path.iterdir()) == []                  # hiçbir dosya/config/ledger yazımı yok


def test_legacy_evaluate_challenger_unchanged_without_extra_gates():
    """Köprü eklenmeden önceki doğrudan çağrı sözleşmesi bozulmamalı."""
    from tradingbot.quant.champion import evaluate_challenger
    out = evaluate_challenger(_metrics(exp=0.05), _metrics(exp=0.2), leakage_passed=True,
                              data_quality_passed=True, isolation_verified=True,
                              same_cost_model=True, fold_consistency=0.8)
    assert out["decision"] == PROMOTE_CANDIDATE
    assert all(c["code"] not in ("SCENARIO_ROBUSTNESS", "JOURNAL_COVERAGE", "REGIME_COVERAGE")
               for c in out["checks"])
