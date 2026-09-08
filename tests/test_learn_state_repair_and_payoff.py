"""Sayaç onarım paketi + hedefe hizalı ödeme modeli regresyonları.

Somut riskler:
* dönüştürücü yalnız `n`'yi bölerse `s`/`ss` tutarsız kalır,
* yaprak / `exp_r` / `agent_hit` / `lessons` yanlışlıkla değiştirilebilir,
* belirsiz, karışık, bayat ya da ZATEN dönüştürülmüş girdi sessizce kabul edilebilir,
* ödeme modelinde maliyet İKİ KEZ düşülebilir (R zaten net),
* koşullu ortalamalar GELECEK kapanışları görebilir (zamansal sızıntı),
* boş sınıf için uydurma sıfır üretilebilir,
* hedef bölmesi (SCRATCH N sınıfında) bozulabilir,
* provenans alanı idempotency anahtarını değiştirebilir.
"""
from __future__ import annotations

import json

from tradingbot.learn.model import HierarchicalRate
from tradingbot.research.learn_state_repair import (ALREADY_CONVERTED, BLOCKED, MARKER_KEY,
                                                    REPAIRABLE, analyse, convert,
                                                    option_comparison, replay_from_lessons,
                                                    verify_conversion)
from tradingbot.research.payoff_model import (PANEL_BLEND, PANEL_PRIOR, UNAVAILABLE,
                                              accounting_reconciliation,
                                              minus_one_approximation_check,
                                              past_only_conditional_means, payoff_comparison,
                                              r_definition, trace_avg_win_r)


# --------------------------------------------------------------------- yardımcılar
def _lesson(tid, sym, setup, side, regime, r, won):
    return {"id": tid, "symbol": sym, "setup": setup, "side": side, "regime": regime,
            "r": r, "won": won, "codes": [], "why": [], "at": "2026-09-03T00:00:00+00:00"}


def _state(lessons):
    """Eski (iki çağrılı) semantikle kurulmuş gerçekçi bir `learn_v2` belgesi."""
    win, exp_r = HierarchicalRate(10.0, 0.5), HierarchicalRate(10.0, 0.0)
    for ls in lessons:
        w = 1.0 if ls["won"] else 0.0
        win.add(w, regime=ls["regime"], leaf=f"{ls['symbol']}|{ls['setup']}")
        win.add(w, regime=ls["regime"], leaf=ls["symbol"])
        exp_r.add(ls["r"], regime=ls["regime"], leaf=f"{ls['setup']}|{ls['side']}")
    return {"schema_version": 2, "updated_at": "2026-09-07T12:27:43+00:00",
            "win": win.to_dict(), "exp_r": exp_r.to_dict(),
            "agent_hit": HierarchicalRate(10.0, 0.5).to_dict(),
            "n_closed": len(lessons), "lessons": lessons,
            "calibrator": {"kind": "platt", "a": 1.0, "b": 0.0, "xs": [], "ys": [], "n_fit": 0},
            "last_metrics": {"keep": "me"}, "baseline_metrics": {}}


LESSONS = [
    _lesson("T1", "AAA/USDT", "pullback", "LONG", "TREND_UP", 2.0, True),
    _lesson("T2", "AAA/USDT", "pullback", "LONG", "TREND_UP", -1.0, False),
    _lesson("T3", "BBB/USDT", "pullback", "SHORT", "RANGE", -1.1, False),
    _lesson("T4", "CCC/USDT", "breakout", "LONG", "RANGE", 1.5, True),
]


# --------------------------------------------------------------------- dönüştürücü
def test_analyse_accepts_a_uniformly_double_counted_state():
    rep = analyse(_state(LESSONS), canonical_closes=4, canonical_wins=2)
    assert rep["state"] == REPAIRABLE
    assert all(c["passed"] for c in rep["checks"])
    assert rep["n_ancestor_nodes"] == 3          # global + 2 rejim


def test_conversion_transforms_n_s_and_ss_together_and_only_ancestors():
    doc = _state(LESSONS)
    conv = convert(doc, canonical_closes=4, canonical_wins=2)
    new = conv["converted"]
    assert new is not None and conv["n_nodes_changed"] == 3
    g_old, g_new = doc["win"]["stats"][""], new["win"]["stats"][""]
    assert (g_old["n"], g_old["s"], g_old["ss"]) == (8.0, 4.0, 4.0)
    assert (g_new["n"], g_new["s"], g_new["ss"]) == (4, 2.0, 2.0)   # n, s, ss BİRLİKTE
    # yapraklar, exp_r, agent_hit, dersler, kalibratör, n_closed DEĞİŞMEZ
    for k, v in doc["win"]["stats"].items():
        if k == "" or (k.startswith("regime:") and "|leaf:" not in k):
            continue
        assert new["win"]["stats"][k] == v
    assert new["exp_r"] == doc["exp_r"]
    assert new["agent_hit"] == doc["agent_hit"]
    assert new["lessons"] == doc["lessons"]
    assert new["calibrator"] == doc["calibrator"]
    assert new["n_closed"] == doc["n_closed"]
    assert new["last_metrics"] == {"keep": "me"}          # ilgisiz alan korunur
    assert new["win"]["alpha"] == doc["win"]["alpha"]     # ÖNSEL kütle ayrı, dokunulmaz
    assert new["win"]["prior_mean"] == doc["win"]["prior_mean"]


def test_conversion_never_mutates_its_input():
    doc = _state(LESSONS)
    before = json.dumps(doc, sort_keys=True)
    convert(doc, canonical_closes=4, canonical_wins=2)
    assert json.dumps(doc, sort_keys=True) == before


def test_converted_state_equals_a_clean_new_semantics_rebuild():
    """İki BAĞIMSIZ yol aynı sonucu vermeli: ata yarılama vs derslerden temiz kurulum."""
    doc = _state(LESSONS)
    conv = convert(doc, canonical_closes=4, canonical_wins=2)
    ver = verify_conversion(doc, conv)
    assert ver["state"] == "VERIFIED"
    assert ver["stored_matches_old_replay"]["identical"] is True
    assert ver["converted_matches_new_replay"]["identical"] is True
    assert ver["exp_r_untouched"]["identical"] is True


def test_replay_covers_more_than_one_regime_and_valid_child_leaves():
    rep = replay_from_lessons(_state(LESSONS), semantics="NEW")
    regimes = {k for k in rep["win"] if k.startswith("regime:") and "|leaf:" not in k}
    leaves = {k for k in rep["win"] if k.startswith("leaf:")}
    assert regimes == {"regime:TREND_UP", "regime:RANGE"}
    assert {"leaf:AAA/USDT", "leaf:AAA/USDT|pullback", "leaf:CCC/USDT|breakout"} <= leaves
    assert rep["win"]["leaf:AAA/USDT"]["n"] == 2          # iki kapanış, tek sayım


def test_already_converted_input_is_refused():
    doc = _state(LESSONS)
    conv = convert(doc, canonical_closes=4, canonical_wins=2)
    again = convert(conv["converted"], canonical_closes=4, canonical_wins=2)
    assert again["converted"] is None
    assert again["analysis"]["state"] in (BLOCKED, ALREADY_CONVERTED)
    assert MARKER_KEY in conv["converted"]


def test_blocked_on_decay_weights_mixed_versions_and_legacy_import():
    decayed = _state(LESSONS)
    decayed["win"]["half_life_days"] = 60.0
    assert analyse(decayed)["state"] == BLOCKED

    odd = _state(LESSONS)
    odd["win"]["stats"][""]["n"] = 7.0                   # tek → tekdüze iki kat değil
    assert analyse(odd)["state"] == BLOCKED

    legacy = _state(LESSONS)
    legacy["win"]["stats"]["leaf:pullback|LONG"] = {"n": 3.0, "s": 1.0, "ss": 1.0}
    assert analyse(legacy)["state"] == BLOCKED

    schema = _state(LESSONS)
    schema["schema_version"] = 3
    assert analyse(schema)["state"] == BLOCKED

    weighted = _state(LESSONS)
    weighted["win"]["stats"][""]["ss"] = 3.0             # s != ss → ağırlıklı/karışık
    assert analyse(weighted)["state"] == BLOCKED


def test_blocked_when_canonical_counts_disagree():
    assert analyse(_state(LESSONS), canonical_closes=9)["state"] == BLOCKED
    assert analyse(_state(LESSONS), canonical_closes=4, canonical_wins=3)["state"] == BLOCKED


def test_option_comparison_is_labelled_a_scenario_and_shows_permanent_excess():
    doc = _state(LESSONS)
    cmp_ = option_comparison(doc, future_closes=(0, 10))
    assert "SENARYO" in cmp_["label"]
    assert cmp_["decay_present"] is False
    assert cmp_["rows"][0]["excess_mass_in_A"] == cmp_["rows"][1]["excess_mass_in_A"] > 0


# --------------------------------------------------------------------- ödeme modeli
def _closes():
    return [
        {"trade_id": "C1", "closed_at_ms": 100.0, "r_multiple": 2.0,
         "net_pnl": 2.0, "gross_pnl": 2.1, "fees": 0.1, "funding": 0.0},
        {"trade_id": "C2", "closed_at_ms": 200.0, "r_multiple": -1.2,
         "net_pnl": -1.2, "gross_pnl": -1.1, "fees": 0.1, "funding": 0.0},
        {"trade_id": "C3", "closed_at_ms": 300.0, "r_multiple": 0.1,
         "net_pnl": 0.1, "gross_pnl": 0.2, "fees": 0.1, "funding": 0.0},
    ]


def test_conditional_means_use_only_earlier_closes():
    """Zamansal sızıntı yok: `as_of`tan sonraki kapanış tahmine GİREMEZ."""
    cm = past_only_conditional_means(_closes(), 250.0)
    assert cm["n_available"] == 2                      # C3 (t=300) GÖRÜLMEZ
    assert cm["n_W"] == 1 and cm["n_N"] == 1
    assert cm["mu_W"] == 2.0 and cm["mu_N"] == -1.2
    later = past_only_conditional_means(_closes(), 400.0)
    assert later["n_available"] == 3 and later["n_N"] == 2
    assert abs(later["mu_N"] - (-0.55)) < 1e-9         # SCRATCH N sınıfında


def test_empty_class_reports_unavailable_not_a_fabricated_zero():
    cm = past_only_conditional_means(_closes(), 150.0)
    assert cm["n_W"] == 1 and cm["n_N"] == 0
    assert cm["mu_N"] is None and cm["mu_N_state"] == UNAVAILABLE
    assert cm["mu_W"] == 2.0


def test_scratch_belongs_to_the_non_win_class():
    cm = past_only_conditional_means(_closes(), 400.0)
    assert cm["n_W"] == 1 and cm["n_N"] == 2           # 0.1R kazanç DEĞİL
    assert r_definition()["scratch"].startswith("SCRATCH")


def test_r_is_already_net_so_costs_are_not_subtracted_twice():
    rec = accounting_reconciliation(_closes())
    assert rec["reconciled"] is True and rec["residual"] == 0.0
    assert "TEKRAR düşülmez" in rec["implication"]
    d = r_definition()
    assert "initial_qty" in d["initial_risk_denominator"]
    assert "NET" in d["costs"]


def test_avg_win_r_is_traced_as_a_blend_not_an_empirical_conditional_mean():
    t = trace_avg_win_r()
    assert t["verdict"] == "BLEND"
    assert "E[R | R > 0.25] DEĞİLDİR" in t["why_not_an_empirical_conditional_mean"]


def test_minus_one_direction_is_measured_from_the_cohort():
    only_losses = [{"trade_id": "x", "closed_at_ms": 1.0, "r_multiple": -1.3}]
    assert "UNDERSTATES" in minus_one_approximation_check(only_losses)["direction"]
    with_scratch = only_losses + [{"trade_id": "y", "closed_at_ms": 2.0, "r_multiple": 0.0}]
    assert "OVERSTATES" in minus_one_approximation_check(with_scratch)["direction"]


def _opp(cid="c1", ts=250.0, p_prior=0.65, p_model=0.30):
    gross = p_prior * 2.0 - (1 - p_prior) * 1.0
    return {"candidate_id": cid, "decision_id": "d" + cid, "ts": "2026-09-03T00:00:00+00:00",
            "ts_ms": ts, "symbol": "AAA/USDT", "p_win_prior": p_prior, "p_win_model": p_model,
            "avg_win_r": 2.0, "avg_loss_r": -1.0, "gross_expectancy_r": gross,
            "net_expectancy_r": gross, "uncertainty_penalty_r": 0.05,
            "conservative_net_edge_r": gross - 0.05, "size_multiplier": 1.0}


def test_payoff_comparison_keeps_penalties_and_probability_source_fixed():
    out = payoff_comparison([_opp()], _closes(), panel=PANEL_PRIOR)
    assert out["probability_source_fixed"] is True and out["penalties_unchanged"] is True
    row = out["rows"][0]
    assert row["p"] == 0.65 and row["panel"] == PANEL_PRIOR
    assert row["uncertainty_penalty_r"] == 0.05 and row["soft_penalty_r"] == 0.0
    # gross_new = p*mu_W + (1-p)*mu_N ; mu değerleri as_of'tan ÖNCEKİ kapanışlardan
    assert abs(row["gross_new"] - (0.65 * 2.0 + 0.35 * -1.2)) < 1e-6
    assert abs(row["conservative_new"] - (row["gross_new"] - 0.05)) < 1e-6


def test_payoff_panels_use_different_fixed_probability_sources():
    a = payoff_comparison([_opp()], _closes(), panel=PANEL_PRIOR)["rows"][0]
    b = payoff_comparison([_opp()], _closes(), panel=PANEL_BLEND)["rows"][0]
    assert a["p"] == 0.65 and b["p"] == 0.30
    assert a["avg_win_r_old"] == b["avg_win_r_old"]     # ödeme terimi AYNI, olasılık farklı


def test_payoff_row_is_unavailable_when_a_class_has_no_observations():
    out = payoff_comparison([_opp(ts=150.0)], _closes(), panel=PANEL_PRIOR)
    row = out["rows"][0]
    assert row["gross_new"] is None and row["tradeable_new"] is None
    assert row["mu_N_state"] == UNAVAILABLE
    assert out["n_with_new_estimate"] == 0


def test_payoff_candidate_order_is_stable():
    opps = [_opp("c2", ts=260.0), _opp("c1", ts=260.0), _opp("c3", ts=255.0)]
    ids = [r["candidate_id"] for r in payoff_comparison(opps, _closes())["rows"]]
    assert ids == ["c3", "c1", "c2"]                   # (as_of, candidate_id)


# --------------------------------------------------------------------- provenans
def test_learning_keys_are_recorded_without_a_second_ledger(tmp_path):
    from tradingbot.learn.learner_v2 import LEARNING_SEMANTICS, LearnerV2
    from tradingbot.learn.memory import TradeMemory
    from tradingbot.learn.reconcile import LearnedIndex, note_learned
    from tradingbot.learn.registry import ModelRegistry
    lrn = LearnerV2(TradeMemory(tmp_path / "m.jsonl"), ModelRegistry(tmp_path / "md.json"),
                    state_path=tmp_path / "l.json")
    rec = {"id": "T1", "symbol": "AAA/USDT", "side": "LONG", "setup_type": "pullback",
           "r_multiple": -1.0, "net_pnl": -1.0, "exit_reason": "stop",
           "closed_at": "2026-09-03T00:00:00+00:00", "mae_pct": -1.0, "mfe_pct": 0.5,
           "features": {"regime": "ENTRY_REGIME", "setup_type": "pullback", "direction": "LONG"}}
    lesson = lrn.on_trade_closed(rec, {"regime": "CLOSE_REGIME"})
    keys = lesson["learning_keys"]
    assert keys["regime_at_close"] == "CLOSE_REGIME"      # GİRİŞ rejimi DEĞİL
    assert keys["win_leaves"] == ["AAA/USDT|pullback", "AAA/USDT"]
    assert keys["exp_r_leaf"] == "pullback|LONG"
    assert keys["weight"] == 1.0 and keys["semantics"] == LEARNING_SEMANTICS
    # learner GERÇEKTEN kapanış anı rejimini kullanır
    assert "regime:CLOSE_REGIME" in lrn.win.stats
    assert "regime:ENTRY_REGIME" not in lrn.win.stats

    idx = LearnedIndex(tmp_path / "learned_closes.jsonl")
    assert note_learned(idx, rec, lesson, source="TEST") is True
    rows = [json.loads(x) for x in
            (tmp_path / "learned_closes.jsonl").read_text(encoding="utf-8").splitlines() if x]
    assert len(rows) == 1 and rows[0]["learning_keys"] == keys
    # İDEMPOTENCY anahtarı DEĞİŞMEDİ: ikinci teslim yine reddedilir, ikinci defter YOK
    assert note_learned(idx, rec, lesson, source="TEST") is False
    assert len(list(tmp_path.glob("*.jsonl"))) == 2       # memory + learned index (yalnız)


def test_learning_keys_are_optional_and_backward_compatible(tmp_path):
    from tradingbot.learn.reconcile import LearnedIndex
    idx = LearnedIndex(tmp_path / "idx.jsonl")
    assert idx.record(close_ev="e1", trade_id="T1", steps=["outcome"], source="OLD") is True
    row = json.loads((tmp_path / "idx.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "learning_keys" not in row                    # alan zorunlu DEĞİL
