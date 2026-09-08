"""Dual-edge doğrulama katmanının GETİRDİĞİ riskler için testler.

* kaynak/formül kimliği (çapa bulma, kapsam mantığı, üretim değerinin yeniden üretilmesi),
* hedef uyumsuzluğu (uyumsuzsa ekonomik ikame YAPILMAZ),
* gelecekteki model kirlenmesi (bugünkü model geçmişe çalıştırılamaz — yapısal AST testi),
* eksik girdi yönetimi (eksik eksik kalır; sıfır ya da otomatik veto DEĞİL),
* idempotency (aynı girdi → aynı çıktı),
* karışmama (export/üretim dosyalarına yazım yok).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from tradingbot.research.dual_edge import (INCOMPATIBLE, LOSS, PARTIAL_COMPATIBLE, PENDING,
                                           SCRATCH, WIN, availability_table, calibration_v2,
                                           classify_separation, decompose_edge, dual_edge_rows,
                                           dual_gate_counts, frozen_label, past_only_base_rate,
                                           pipeline_trace, prior_recovery_check,
                                           probability_contracts, recover_prior, scoring)
from tradingbot.research.dual_edge_run import f36_block, observer_sufficiency
from tradingbot.research.entry_lab import (REP_FIRST, REP_LINKED_ELSE_FIRST, build_opportunities)

ENGINE_SRC = """
class E:
    def tour(self):
        self._assess_opportunities(decisions, briefs)
        chief = self.chief_mgr.decide(x)
        for b in briefs:
            d.p_win = b.p_win

    def _assess_opportunities(self, decisions, briefs):
        d.opportunity = a.to_dict()

    def _execute_locked(self):
        _order.sort(key=lambda s: 1)
        if _opp.get("tradeable"):
            pass
"""

HEAD_SRC = """
class H:
    def decide(self):
        d.p_win = round(0.5 + 0.25 * conf * (1 if abs(score) >= t else 0), 3)
"""


def _opp(**kw):
    base = {"opportunity_key": "K", "candidate_id": "c1", "decision_id": "d1",
            "symbol": "AAA/USDT", "direction": "LONG", "ts": "2026-09-03T00:00:00+00:00",
            "ts_ms": 1.0, "setup": "pullback", "regime": "TREND_UP",
            "p_win_prior": 0.65, "p_win_model": 0.30,
            "avg_win_r": 2.0, "avg_loss_r": -1.0,
            "gross_expectancy_r": 0.65 * 2.0 - 0.35,
            "net_expectancy_r": 0.65 * 2.0 - 0.35,
            "uncertainty_penalty_r": 0.05, "conservative_net_edge_r": 0.65 * 2.0 - 0.35 - 0.05,
            "size_multiplier": 1.0, "baseline_accepted": False, "reject_reasons": ["X"]}
    base.update(kw)
    return base


# ------------------------------------------------------------------ kaynak / formül kimliği
def test_pipeline_trace_uses_call_sites_not_definition_lines():
    """Callee'nin GÖVDESİ, çağrıldığı satırdan sonra görünse bile ikinci bir çağrı DEĞİLDİR."""
    tr = pipeline_trace(ENGINE_SRC, HEAD_SRC)
    assert tr["all_anchors_found"] is True
    assert tr["same_enclosing_scope"] is True
    assert tr["enclosing_scope"] == "tour"
    assert tr["economic_gate_before_model_assignment"] is True
    # `d.opportunity = ...` satırı model atamasından SONRA gelir ama `_assess_opportunities`
    # TANIMI içindedir -> yeniden hesap SAYILMAZ.
    assert tr["opportunity_assignments"][0]["enclosing_def"] == "_assess_opportunities"
    assert tr["recompute_call_sites_after_model"] == []
    assert tr["alternate_recompute_path"] is False


def test_pipeline_trace_detects_a_real_recompute_call():
    src = ENGINE_SRC.replace("            d.p_win = b.p_win",
                             "            d.p_win = b.p_win\n        self._assess_opportunities(x, y)")
    tr = pipeline_trace(src, HEAD_SRC)
    assert tr["alternate_recompute_path"] is True
    assert tr["recompute_call_sites_after_model"]


def test_classification_depends_on_evidence_not_only_order():
    tr = pipeline_trace(ENGINE_SRC, HEAD_SRC)
    shadow = classify_separation(tr, influence_mode="SHADOW", champion_model_present=True,
                                 calibrator_fitted=True, snapshot_reports_model_p_win=False)
    assert shadow["classification"] == "INTENTIONAL_SHADOW"
    bounded = classify_separation(tr, influence_mode="PAPER_BOUNDED",
                                  champion_model_present=False, calibrator_fitted=False,
                                  snapshot_reports_model_p_win=True)
    assert bounded["classification"] == "MISSING_INTEGRATION"
    assert any("kalibre" in r for r in bounded["reasons"])


def test_edge_decomposition_and_exact_reproduction():
    o = _opp()
    dec = decompose_edge(o)
    assert dec["cost_r"] == 0.0
    assert abs(dec["soft_penalty_r"]) < 1e-9
    assert dec["identity_holds"] is True
    assert dec["gates_blocked"] is False
    rows = dual_edge_rows([o], links={}, canonical_by_trade={})
    assert rows[0]["reproduces_production"] is True


def test_gross_and_conservative_are_not_the_same_label():
    """Basit ağırlıklı beklenti, iki kesintiden SONRAKİ 'conservative net edge' DEĞİLDİR."""
    o = _opp(uncertainty_penalty_r=0.041703,
             conservative_net_edge_r=0.65 * 2.0 - 0.35 - 0.041703 - 0.16)
    dec = decompose_edge(o)
    assert abs(dec["soft_penalty_r"] - 0.16) < 1e-9
    gap = o["gross_expectancy_r"] - o["conservative_net_edge_r"]
    assert abs(gap - (0.041703 + 0.16)) < 1e-9


def test_f36_block_resolves_the_gap_as_two_haircuts():
    o = _opp(candidate_id="4c7bed8f3bb17655", p_win_prior=0.659, avg_win_r=1.851148,
             gross_expectancy_r=0.878907, net_expectancy_r=0.878907,
             uncertainty_penalty_r=0.041703, conservative_net_edge_r=0.677204)
    row = dual_edge_rows([o], links={"4c7bed8f3bb17655": "F00036"},
                         canonical_by_trade={"F00036": {}})[0]
    blk = f36_block(row)
    assert blk["gap_resolution"]["state"] == "RESOLVED"
    assert abs(blk["gap_resolution"]["reported_gap"] - 0.201703) < 1e-6
    assert abs(blk["gap_resolution"]["residual"]) < 1e-6
    assert blk["dual_gate"]["tradeable_prior"] is True
    assert blk["dual_gate"]["tradeable_model"] is False


# ------------------------------------------------------------------ hedef uyumsuzluğu
def test_incompatible_target_blocks_the_substitution():
    rows = dual_edge_rows([_opp()], links={}, canonical_by_trade={},
                          compatibility=INCOMPATIBLE)
    assert rows[0]["model_edge"] is None
    assert rows[0]["model_edge_missing_reason"] == INCOMPATIBLE
    assert rows[0]["tradeable_model"] is None


def test_probability_contracts_declare_targets_and_compatibility():
    c = probability_contracts(champion_model_present=False, calibrator={"n_fit": 0},
                              n_closed=25, learn_updated_at="2026-09-07T12:27:43+00:00")
    assert c["p_win_prior"]["is_calibrated"] is False
    assert c["p_win_model"]["is_fitted_model"] is False
    assert "0.25R" in c["p_win_model"]["predicted_event"]
    assert c["target_compatibility"]["prior_vs_payoff"] == INCOMPATIBLE
    assert c["target_compatibility"]["model_vs_payoff"] == PARTIAL_COMPATIBLE


# ------------------------------------------------------------------ gelecek-model kirlenmesi
def test_dual_edge_never_runs_a_model():
    """Yapısal güvence: modül hiçbir tahmin edici import etmez / çağırmaz."""
    for name in ("dual_edge.py", "dual_edge_run.py"):
        src = (Path("tradingbot/research") / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", "") or ""
                names = " ".join(a.name for a in node.names)
                assert "learner" not in (mod + names).lower(), f"{name}: learner import"
                assert "registry" not in (mod + names).lower(), f"{name}: registry import"
            if isinstance(node, ast.Attribute) and node.attr in ("predict", "predict_proba",
                                                                 "train_challenger"):
                raise AssertionError(f"{name}: model çağrısı bulundu ({node.attr})")


def test_model_probability_is_only_read_from_the_record():
    """Kayıtta model olasılığı yoksa BUGÜNKÜ model çalıştırılmaz — satır eksik kalır."""
    rows = dual_edge_rows([_opp(p_win_model=None)], links={}, canonical_by_trade={})
    assert rows[0]["model_edge"] is None
    assert rows[0]["tradeable_model"] is None
    assert "YOK" in rows[0]["model_edge_missing_reason"]


# ------------------------------------------------------------------ eksik girdi yönetimi
def test_missing_stays_missing_never_zero_or_veto():
    rows = dual_edge_rows([_opp(avg_win_r=None)], links={}, canonical_by_trade={})
    assert rows[0]["state"] == "MISSING_INPUTS"
    assert "payoff" in rows[0]["missing"]
    counts = dual_gate_counts(rows)
    assert counts["n_both_gates_computable"] == 0
    assert counts["missing"]["tradeable_prior_missing"] == 1
    assert counts["counts"]["prior_NO_model_NO"] == 0     # eksik, "hayır" sayılmaz


def test_blocked_gate_unknown_is_not_assumed():
    """`size_multiplier==0` ve `net<=0` iken bloklama BİLİNMEZ; kenar<=0 ise verdikt yine False."""
    o = _opp(p_win_prior=0.20, gross_expectancy_r=0.20 * 2.0 - 0.80,
             net_expectancy_r=0.20 * 2.0 - 0.80, conservative_net_edge_r=-0.45,
             size_multiplier=0.0)
    dec = decompose_edge(o)
    assert dec["gates_blocked"] is None
    rows = dual_edge_rows([o], links={}, canonical_by_trade={})
    assert rows[0]["tradeable_prior"] is False       # kenar <= 0 -> kesin False


def test_availability_table_uses_one_denominator():
    opps = [_opp(), _opp(candidate_id="c2", p_win_prior=None)]
    t = availability_table(opps)
    assert t["denominator"] == 2
    assert t["counts"]["p_win_prior_available"] == 1
    assert t["counts"]["p_win_model_available"] == 2
    assert t["missing"]["p_win_prior_missing"] == 1


def test_prior_is_recoverable_from_the_identity():
    o = _opp(p_win_prior=None)
    rec = recover_prior(o)
    assert rec["state"] == "OK"
    assert abs(rec["p_gate"] - 0.65) < 1e-6
    chk = prior_recovery_check([_opp()])
    assert chk["n_validated"] == 1
    assert chk["max_abs_error"] is not None and chk["max_abs_error"] < 1e-6


# ------------------------------------------------------------------ etiket / taban çizgisi
def test_frozen_label_matches_the_production_scratch_rule():
    assert frozen_label({"state": "RESOLVED", "net_r": 1.2})["label"] == WIN
    assert frozen_label({"state": "RESOLVED", "net_r": -1.0})["label"] == LOSS
    assert frozen_label({"state": "RESOLVED", "net_r": 0.1})["label"] == SCRATCH
    assert frozen_label({"state": "HORIZON_CLOSE", "net_r": 0.9})["label"] == PENDING
    assert frozen_label(None)["label"] == PENDING


def test_base_rate_uses_only_past_closes():
    closed = [{"closed_at_ms": 10.0, "r_multiple": 2.0},
              {"closed_at_ms": 20.0, "r_multiple": -1.0},
              {"closed_at_ms": 99.0, "r_multiple": 3.0}]
    br = past_only_base_rate(closed, 50.0)
    assert br["n"] == 2 and abs(br["base_rate"] - 0.5) < 1e-9   # 99.0 GÖRÜLMEZ
    assert past_only_base_rate(closed, 1.0)["state"] == "NO_PRIOR_CLOSES"


def test_calibration_uses_only_labelled_rows_and_reports_censoring():
    opps = [_opp(opportunity_key=f"k{i}", candidate_id=f"c{i}") for i in range(6)]
    sims = ([{"opportunity_key": f"k{i}", "state": "RESOLVED", "net_r": 1.0} for i in range(3)]
            + [{"opportunity_key": f"k{i}", "state": "HORIZON_CLOSE", "net_r": 0.4}
               for i in range(3, 6)])
    c = calibration_v2(opps, sims, canonical_closed=[{"closed_at_ms": 0.0, "r_multiple": 1.0}])
    assert c["label_distribution"][WIN] == 3
    assert c["label_distribution"][PENDING] == 3
    assert c["censoring_rate"] == 0.5
    assert c["n_labelled"] == 3


def test_scoring_reports_auc_and_dispersion():
    s = scoring([(0.8, 1.0), (0.7, 1.0), (0.2, 0.0), (0.1, 0.0), (0.3, 0.0), (0.9, 1.0)])
    assert s["state"] == "RUN" and s["auc"] == 1.0
    assert s["dispersion_sd"] > 0
    assert scoring([(0.5, 1.0)])["state"] == "INSUFFICIENT_SAMPLE"


# ------------------------------------------------------------------ temsilci / idempotency
def test_representative_policy_keeps_the_default_cohort_and_finds_linked_rows():
    rows = [{"symbol": "A/USDT", "direction": "LONG", "timeframe": "4h", "setup": "pullback",
             "ts_ms": 14_400_000 * 10 + i * 60_000, "candidate_id": f"c{i}",
             "baseline_accepted": i == 2} for i in range(4)]
    first = build_opportunities(rows, {}, representative=REP_FIRST)["opportunities"][0]
    linked = build_opportunities(rows, {"c2": "F1"},
                                 representative=REP_LINKED_ELSE_FIRST)["opportunities"][0]
    assert first["candidate_id"] == "c0"
    assert linked["candidate_id"] == "c2"
    assert linked["linked_trade_id"] == "F1"


def test_dual_edge_rows_are_deterministic():
    opps = [_opp(opportunity_key=f"k{i}", candidate_id=f"c{i}") for i in range(5)]
    a = dual_edge_rows(opps, links={}, canonical_by_trade={})
    b = dual_edge_rows(opps, links={}, canonical_by_trade={})
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_observer_sufficiency_requires_full_coverage():
    avail = {"denominator": 10}
    ok = observer_sufficiency(avail, {"n_outside_unit_interval": 0, "max_abs_error": 0.0},
                              {"n_both_gates_computable": 10})
    assert ok["sufficient_without_new_instrumentation"] is True
    assert ok["instrumentation_patch_required"] is False
    bad = observer_sufficiency(avail, {"n_outside_unit_interval": 2, "max_abs_error": 0.0},
                               {"n_both_gates_computable": 8})
    assert bad["sufficient_without_new_instrumentation"] is False
    assert bad["instrumentation_patch_required"] is True


def test_dual_edge_stage_writes_only_under_out_dir(tmp_path):
    """Karışmama: export dizini ve üretim dosyaları DEĞİŞMEZ."""
    from tradingbot.research.dual_edge_run import build_dual_edge_report
    root = tmp_path / "exp"
    (root / "state").mkdir(parents=True)
    (root / "MANIFEST.txt").write_text("cutoff_utc=2026-09-07T21:16:41Z\napp_head=abc\n",
                                       encoding="utf-8")
    (root / "state" / "futures_ledger.json").write_text(
        json.dumps({"starting_equity": 100.0, "wallet_balance": 100.0, "positions": {},
                    "history": [], "entries": []}), encoding="utf-8")
    for f in ("entry_snapshot.jsonl", "position_path.jsonl", "trade_memory.jsonl"):
        (root / "state" / f).write_text("", encoding="utf-8")
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    rep = build_dual_edge_report(root, tmp_path / "out", offline=True)
    after = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    assert before == after
    wb = rep["write_boundary"]
    assert wb["pipeline_reordered"] is False
    assert wb["probability_source_switched"] is False
    assert wb["pfexp_touched"] is False
    assert wb["learned_index_touched"] is False
