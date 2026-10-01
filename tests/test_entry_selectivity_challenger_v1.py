"""ENTRY_SELECTIVITY_CHALLENGER_V1 — giriş seçiciliği regresyonları.

37 zorunlu senaryo; parametrelendirme ile toplanan test sayısı 56.

Kapsam sözleşmesi (görev metniyle birebir):

 1. Snapshot sonuçtan ÖNCE yazılır ve sonucu GÖRMEZ.
 2. Ölçülemeyen alan `UNKNOWN` kalır — sıfıra düşürülmez.
 3. Deterministik `candidate_id` / `decision_id` (karar günlüğüyle AYNI türetme).
 4. Snapshot deposu `candidate_id` ile tekilleştirir.
 5. Değerlendirme `outcome_id` ile tekilleştirir.
 6. `LEGACY_MEMORY` terfi kanıtı SAYILMAZ.
 7. Challenger/snapshot/eval modülleri emir yoluna BAĞLANAMAZ (AST).
 8. `applied` HER ZAMAN False.
 9-13. Beş ailenin her biri için ACCEPT / VETO / `MISSING_DATA` yolu.
14. Eksik veri VETO GEREKÇESİ DEĞİLDİR.
15. NO-LOOKAHEAD: ödeme oranı yalnız GEÇMİŞ kapanışlardan.
16. Walk-forward katları kronolojik ve ÖRTÜŞMEZ.
17-18. Komisyon/funding rapora girer; ölçülemeyen maliyet SIFIR SAYILMAZ.
19-21. Terfi kapıları fail-closed; kapılar dolmadan `INSUFFICIENT_ENTRY_SAMPLE`.
22. `p_win` ters kalibrasyonu uydurma bir eşikle MASKELENMEZ.
23-24. Config fail-closed; varsayılan `SHADOW`.
25-27. Motor e2e: snapshot yazılır, AKTİF KARAR ve pozisyon fingerprint'i DEĞİŞMEZ.
28-30. Panel bozuk şemada 500 vermez; SHADOW/LEGACY ayrımı ve dürüst LLM durumu görünür.
31-32. Faz 5 replay denetimi fail-closed; sentetik kârlılık ÜRETİLMEZ.
33-37. Bootstrap determinizmi, link satırı, JSON güvenliği, PAPER/live değişmezleri.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import timedelta
from pathlib import Path

import pytest

from tradingbot.core import ConfigError, utc_now
from tradingbot.learn.entry_challenger import (ACCEPT, FAM_DISPERSION, FAM_HEAT, FAM_LIQUIDITY,
                                               FAM_PROB, FAM_REGIME, FAMILIES,
                                               MISSING_MEANS_ACCEPT, R_BELOW_BREAKEVEN,
                                               R_CONCENTRATION, R_COST_TO_RISK, R_HEAT,
                                               R_ILLIQUID, R_LOW_CONSENSUS, R_MISSING, R_OK,
                                               R_REGIME_MISMATCH, R_REGIME_UNKNOWN, VETO,
                                               EntryChallengerConfig, challenger_a,
                                               challenger_b, challenger_c, challenger_d,
                                               challenger_e, evaluate_all)
from tradingbot.learn.entry_eval import (ALLOWED_MODES, ELIGIBLE_FOR_PAPER_BOUNDED,
                                         FORBIDDEN_OUTCOME_FIELDS, GATE_MIN_DAYS,
                                         GATE_MIN_LINKED_CLOSES, INSUFFICIENT_ENTRY_SAMPLE,
                                         KNOWN_MODES, MODE_SHADOW, NO_SNAPSHOT,
                                         bootstrap_ci, build_report, evaluate_closes,
                                         evaluate_trade, expanding_payoff, leakage_report,
                                         outcome_id, walk_forward_folds)
from tradingbot.learn.entry_replay import (NOT_REPLAYABLE, REQUIRED_FIELDS, field_coverage,
                                           journal_to_candidate, memory_to_candidate,
                                           replay_audit)
from tradingbot.learn.entry_snapshot import (DEFAULTED, LEGACY_MEMORY, LINKED, MEASURED,
                                             MISSING, EntrySnapshotStore, build_entry_snapshot,
                                             candidate_id, decision_id,
                                             snapshot_from_memory_entry)

CFG = EntryChallengerConfig()
NOW = utc_now()


# ------------------------------------------------------------------ yardımcılar

def _snap(**over):
    """Dolu, gerçekçi bir giriş snapshot'ı. `over` ile tek tek alan bozulabilir."""
    base = dict(
        run_id="r1", cycle_id="c1", symbol="ETH/USDT", direction="LONG",
        decision={"p_win": 0.55, "consensus_score": 0.42, "consensus_confidence": 0.6,
                  "expected_r": 1.9, "regime": "TREND_UP", "dissent": [1], "vetoes": []},
        plan={"entry": 100.0, "stop": 95.0, "entry_type": "breakout", "notional": 50.0,
              "leverage": 2, "targets": [105.0, 110.0], "expected_cost_pct": 0.2},
        opportunity={"conservative_net_edge_r": 0.5, "net_expectancy_r": 0.8,
                     "gross_expectancy_r": 0.9, "uncertainty_penalty_r": 0.1,
                     "size_multiplier": 1.0, "sample_size": 25, "avg_win_r": 1.6,
                     "avg_loss_r": 1.0, "expectancy_basis": "symbol"},
        chief_permission={"allow": True, "open_positions": 2, "total_open_risk_usdt": 2.0,
                          "same_direction_open": 1},
        risk_decision={"allowed": True, "reasons": []},
        features={"atr_pct": 2.0, "bb_width": 0.3, "spread_pct": 0.05,
                  "est_slippage_pct": 0.02, "depth_ratio": 1.4, "liquidity_ok": True,
                  "funding_rate": 0.0001, "expected_cost_pct": 0.2},
        specialist_scores={"a": 0.4, "b": 0.5, "c": 0.35, "d": 0.45},
        baseline_rank=0, baseline_accepted=True, code_sha="deadbeef", config_hash="cafe",
        policy_version="entry_v1.0.0", now=NOW,
    )
    base.update(over)
    return build_entry_snapshot(**base)


def _close(tid="F1", *, r=-1.0, sym="ETH/USDT", side="LONG", when=None, fees=0.04,
           funding=0.01, risk=2.0):
    ts = (when or NOW).isoformat()
    return {"close_event_id": f"ce-{tid}", "trade_id": tid, "symbol": sym, "side": side,
            "opened_at": ts, "closed_at": ts, "exit_reason": "stop",
            "net_pnl": round(r * risk, 6), "r_multiple": r, "fees": fees, "funding": funding,
            "raw": {"meta": {"risk_snapshot": {"max_loss_at_stop_usdt": risk}}}}


def _corpus(n=60, *, inverse_p_win=False, block_frac=0.0):
    """n kapanışlık bağlı (LINKED) bir kurgu: snapshot + link + kapanış."""
    snaps, links, closes = {}, {}, []
    for i in range(n):
        side = "LONG" if i % 2 == 0 else "SHORT"
        regime = "TREND_UP" if i % 3 else "RANGE"
        win = (i % 4 == 0)
        p = (0.30 if win else 0.46) if inverse_p_win else (0.60 if win else 0.30)
        edge = 0.6 if (block_frac and i < int(n * block_frac)) else 0.5
        s = _snap(run_id="R", cycle_id=f"c{i}", symbol=f"S{i % 5}/USDT", direction=side,
                  decision={"p_win": p, "consensus_score": 0.42, "consensus_confidence": 0.6,
                            "expected_r": 1.9, "regime": regime, "dissent": [], "vetoes": []},
                  opportunity={"conservative_net_edge_r": edge, "net_expectancy_r": 0.8,
                               "sample_size": 25, "avg_win_r": 1.6, "avg_loss_r": 1.0},
                  now=NOW - timedelta(days=60 - i))
        snaps[s["candidate_id"]] = s
        tid = f"T{i:03d}"
        links[tid] = s["candidate_id"]
        closes.append(_close(tid, r=(2.0 if win else -1.0), sym=f"S{i % 5}/USDT", side=side,
                             when=NOW - timedelta(days=45 - i * 0.7)))
    return snaps, links, closes


# ------------------------------------------------------------------ 1-5: snapshot sözleşmesi

def test_01_snapshot_is_written_before_outcome_and_never_sees_it():
    s = _snap()
    assert s["provenance"]["sees_outcome"] is False
    assert s["provenance"]["written_at_stage"] == "RANKING"
    for k in FORBIDDEN_OUTCOME_FIELDS:
        assert k not in s, f"sonuç alanı snapshot'a sızdı: {k}"
    assert s["link_status"] == LINKED
    # Değerlendirme snapshot'ı DEĞİŞTİRMEZ.
    before = json.dumps(s, sort_keys=True, default=str)
    evaluate_trade(snapshot=s, close=_close(), cfg=CFG)
    assert json.dumps(s, sort_keys=True, default=str) == before


def test_02_missing_field_stays_unknown_and_is_never_zero():
    s = _snap(features={"atr_pct": 2.0}, opportunity={})
    for f in ("spread_pct", "est_slippage_pct", "depth_ratio", "conservative_net_edge_r"):
        assert s[f] is None, f"{f} sıfıra düşürülmüş"
        assert s["sources"][f] == MISSING
        assert f in s["missing_fields"]
    assert s["liquidity_ok"] is None and s["sources"]["liquidity_ok"] == MISSING
    assert s["sources"]["atr_pct"] == MEASURED
    assert s["n_missing"] == len(s["missing_fields"]) > 0


def test_03_identifiers_are_deterministic_and_match_the_decision_journal():
    from tradingbot.learn.decision_journal import decision_id_for
    a, b = _snap(), _snap()
    assert a["candidate_id"] == b["candidate_id"] == candidate_id("r1", "c1", "ETH/USDT", "LONG")
    assert a["decision_id"] == decision_id("r1", "c1", "ETH/USDT", "LONG")
    assert a["decision_id"] == decision_id_for("r1", "c1", "ETH/USDT", "LONG")
    assert _snap(symbol="BTC/USDT")["candidate_id"] != a["candidate_id"]


def test_04_snapshot_store_deduplicates_by_candidate_id(tmp_path):
    st = EntrySnapshotStore(tmp_path / "entry_snapshot.jsonl")
    s = _snap()
    assert st.append(s) is True
    assert st.append(dict(s)) is False           # aynı aday ikinci kez yazılmaz
    assert st.appended == 1 and st.duplicates == 1
    assert len(st.by_candidate()) == 1
    st2 = EntrySnapshotStore(tmp_path / "entry_snapshot.jsonl")
    assert st2.append(dict(s)) is False, "restart sonrası da tekilleştirmeli"


def test_05_evaluation_identity_is_deterministic_and_dedupes():
    snaps, links, closes = _corpus(6)
    assert outcome_id("ce-T000", "x") == outcome_id("ce-T000", "x")
    assert outcome_id("ce-T000", "x") != outcome_id("ce-T001", "x")
    evs = evaluate_closes(closes=closes + closes, snapshots=snaps, links=links, cfg=CFG)
    assert len(evs) == 6, "aynı kapanış iki kez sayıldı"
    assert len({e["outcome_id"] for e in evs}) == 6


# ------------------------------------------------------------------ 6: legacy

def test_06_legacy_memory_is_observation_only_and_never_promotion_evidence():
    row = {"trade_id": "L1", "symbol": "SOL/USDT", "direction": "LONG", "run_id": "r",
           "setup_type": "pullback",
           "decision": {"p_win": 0.5, "opportunity": {"conservative_net_edge_r": 0.4}},
           "features": {"entry": 10.0, "initial_stop": 9.0, "atr_pct": 3.0}}
    ls = snapshot_from_memory_entry(row)
    assert ls["link_status"] == LEGACY_MEMORY and ls["decision_id"] is None
    ev = evaluate_trade(snapshot=ls, close=_close("L1", r=-1.0), cfg=CFG)
    assert ev["evidence_grade"] == "OBSERVATION_ONLY"
    snaps, links, closes = _corpus(4)
    snaps[ls["candidate_id"]] = ls
    links["L1"] = ls["candidate_id"]
    closes.append(_close("L1", r=-1.0))
    doc = build_report(closes=closes, snapshots=snaps, links=links, cfg=CFG)
    assert doc["n_legacy_memory"] == 1 and doc["n_linked"] == 4
    for fam in FAMILIES:
        rep = doc["families"][fam]
        assert rep["n_evaluated"] == 4, "legacy kayıt kapı hesabına girdi"
        assert rep["observation_only"]["n_evaluated"] == 1
    detail = next(g["detail"] for g in doc["promotion_gates"][FAM_PROB]
                  if g["code"] == "MIN_LINKED_CLOSES")
    assert detail.startswith("4/")


# ------------------------------------------------------------------ 7: yapısal izolasyon

@pytest.mark.parametrize("mod", ["entry_challenger.py", "entry_snapshot.py", "entry_eval.py",
                                 "entry_replay.py"])
def test_07_entry_modules_cannot_import_the_order_path(mod):
    import ast
    src = Path("tradingbot/learn") .joinpath(mod).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    joined = " ".join(imported)
    for banned in ("execution", "gateway", "accounting", "outbox", "notify", "risk.engine",
                   "paper_futures", "ledger"):
        assert banned not in joined, f"yasak bağımlılık: {banned} ({imported})"


# ------------------------------------------------------------------ 8: applied

def test_08_applied_is_always_false_everywhere():
    s = _snap()
    for v in evaluate_all(s, CFG, risk_budget_usdt=6.0).values():
        assert v["applied"] is False
    ev = evaluate_trade(snapshot=s, close=_close(), cfg=CFG)
    assert ev["applied"] is False
    assert all(f["applied"] is False for f in ev["families"].values())
    snaps, links, closes = _corpus(8)
    doc = build_report(closes=closes, snapshots=snaps, links=links, cfg=CFG)
    assert doc["applied_total"] == 0 and doc["auto_promotion"] is False
    assert all(f["applied"] is False for f in doc["families"].values())


# ------------------------------------------------------------------ 9-13: aileler

def test_09_family_a_accept_veto_and_missing_paths():
    ok = challenger_a(_snap(), CFG)
    assert ok["decision"] == ACCEPT and ok["reason_codes"] == [R_OK]
    low = challenger_a(_snap(decision={"p_win": 0.05, "regime": "TREND_UP"}), CFG)
    assert low["decision"] == VETO and R_BELOW_BREAKEVEN in low["reason_codes"]
    miss = challenger_a(_snap(decision={"regime": "TREND_UP"}, opportunity={}), CFG)
    assert miss["decision"] == ACCEPT and miss["reason_codes"] == [R_MISSING]
    assert any(b.startswith(R_MISSING) for b in miss["blockers"])
    # Kırılma noktası ÖDEME ORANINDAN gelir, sabit bir kazanma oranı dayatılmaz.
    assert challenger_a(_snap(), CFG, realized_payoff=4.0)["evidence"]["breakeven_p"] < \
        challenger_a(_snap(), CFG, realized_payoff=1.0)["evidence"]["breakeven_p"]


def test_10_family_b_accept_veto_and_missing_paths():
    assert challenger_b(_snap(), CFG)["decision"] == ACCEPT
    bad = challenger_b(_snap(decision={"regime": "TREND_DOWN"}, direction="LONG"), CFG)
    assert bad["decision"] == VETO and R_REGIME_MISMATCH in bad["reason_codes"]
    unk = challenger_b(_snap(decision={"regime": None}), CFG)
    assert unk["decision"] == ACCEPT and R_REGIME_UNKNOWN in unk["reason_codes"]
    assert challenger_b(_snap(direction="SHORT", decision={"regime": "TREND_UP"}),
                        CFG)["decision"] == VETO


def test_11_family_c_accept_veto_and_missing_paths():
    assert challenger_c(_snap(), CFG)["decision"] == ACCEPT
    weak = challenger_c(_snap(decision={"consensus_score": 0.01, "regime": "TREND_UP"}), CFG)
    assert weak["decision"] == VETO and R_LOW_CONSENSUS in weak["reason_codes"]
    miss = challenger_c(_snap(decision={"regime": "TREND_UP"}), CFG)
    assert miss["decision"] == ACCEPT and miss["reason_codes"] == [R_MISSING]
    wide = challenger_c(_snap(specialist_scores={"a": -1.0, "b": 1.0, "c": 0.0, "d": 1.0}), CFG)
    assert wide["evidence"]["specialist_dispersion"] > CFG.max_specialist_dispersion
    assert wide["decision"] == VETO


def test_12_family_d_reports_missing_liquidity_instead_of_inventing_a_veto():
    """Üretimde likidite alanlarının tamamı boştu — bu aile karar VEREMEZ ve bunu söyler."""
    prod = _snap(features={"atr_pct": 2.6})
    d = challenger_d(prod, CFG)
    # Likidite ölçülemedi: aile VETO UYDURMAZ, ölçemediğini `blockers` ile bildirir.
    assert d["decision"] == ACCEPT
    assert {f"{R_MISSING}:spread_pct", f"{R_MISSING}:liquidity_ok",
            f"{R_MISSING}:est_slippage_pct", f"{R_MISSING}:depth_ratio"} <= set(d["blockers"])
    assert d["evidence"]["spread_pct"] is None and d["evidence"]["liquidity_ok"] is None
    # Maliyet de ölçülemiyorsa aile HİÇ karar veremez ve bunu açıkça söyler.
    blind = challenger_d(_snap(features={}, plan={"entry": None, "stop": None,
                                                  "entry_type": "b"}), CFG)
    assert blind["decision"] == ACCEPT and blind["reason_codes"] == [R_MISSING]
    assert blind["evidence"]["cost_to_risk_r"] is None
    assert challenger_d(_snap(), CFG)["decision"] == ACCEPT
    wide = challenger_d(_snap(features={"spread_pct": 9.0, "liquidity_ok": False}), CFG)
    assert wide["decision"] == VETO and R_ILLIQUID in wide["reason_codes"]
    costly = challenger_d(_snap(plan={"entry": 100.0, "stop": 99.9, "entry_type": "b",
                                      "expected_cost_pct": 5.0}), CFG)
    assert costly["decision"] == VETO and R_COST_TO_RISK in costly["reason_codes"]


def test_13_family_e_accept_veto_and_missing_paths():
    assert challenger_e(_snap(), CFG, risk_budget_usdt=6.0)["decision"] == ACCEPT
    hot = challenger_e(_snap(chief_permission={"allow": True, "open_positions": 9,
                                               "total_open_risk_usdt": 5.9,
                                               "same_direction_open": 1}),
                       CFG, risk_budget_usdt=6.0)
    assert hot["decision"] == VETO and R_HEAT in hot["reason_codes"]
    conc = challenger_e(_snap(chief_permission={"allow": True, "open_positions": 9,
                                                "total_open_risk_usdt": 1.0,
                                                "same_direction_open": 9}),
                        CFG, risk_budget_usdt=6.0)
    assert conc["decision"] == VETO and R_CONCENTRATION in conc["reason_codes"]
    miss = challenger_e(_snap(chief_permission={"allow": True}), CFG, risk_budget_usdt=6.0)
    assert miss["decision"] == ACCEPT and miss["reason_codes"] == [R_MISSING]


def test_14_missing_data_is_never_a_veto_reason():
    """Ölçemediğimiz için reddetmek, ölçtüğümüzü iddia etmenin başka biçimidir."""
    assert MISSING_MEANS_ACCEPT is True
    bare = build_entry_snapshot(run_id="r", cycle_id="c", symbol="X/USDT", direction="LONG")
    for fam, v in evaluate_all(bare, CFG).items():
        assert v["decision"] == ACCEPT, f"{fam} eksik veriyle VETO üretti"
        assert v["blockers"], f"{fam} ölçemediğini bildirmedi"


# ------------------------------------------------------------------ 15-16: sızıntı / WFO

def test_15_payoff_threshold_uses_only_past_closes_no_lookahead():
    assert expanding_payoff([]) is None
    assert expanding_payoff([2.0, 1.0]) is None          # kayıp örneği yok
    assert expanding_payoff([2.0, -1.0]) == pytest.approx(2.0)
    snaps, links, closes = _corpus(10)
    evs = evaluate_closes(closes=closes, snapshots=snaps, links=links, cfg=CFG)
    assert evs[0]["payoff_used"] is None, "ilk kapanış geleceği görmüş"
    seen: list[float] = []
    for e, c in zip(evs, sorted(closes, key=lambda x: str(x["closed_at"]))):
        assert e["payoff_used"] == (None if expanding_payoff(seen) is None
                                    else pytest.approx(expanding_payoff(seen), abs=1e-6))
        seen.append(c["r_multiple"])


def test_16_walk_forward_folds_are_chronological_and_disjoint():
    snaps, links, closes = _corpus(30)
    evs = evaluate_closes(closes=closes, snapshots=snaps, links=links, cfg=CFG)
    wf = walk_forward_folds(evs, FAM_PROB, k=3)
    assert wf["state"] == "ok" and len(wf["folds"]) == 3
    assert sum(f["n"] for f in wf["folds"]) == len(evs)
    bounds = [(f["from"], f["to"]) for f in wf["folds"]]
    for (_, end), (start, _) in zip(bounds, bounds[1:]):
        assert start >= end, "katlar zaman içinde örtüşüyor"
    assert walk_forward_folds(evs[:3], FAM_PROB, k=3)["state"] == "insufficient_sample"


# ------------------------------------------------------------------ 17-18: maliyet

def test_17_fees_and_funding_enter_the_counterfactual_report():
    s = _snap(decision={"p_win": 0.05, "regime": "TREND_UP"})     # A ailesi VETO verecek
    ev = evaluate_trade(snapshot=s, close=_close(fees=0.06, funding=0.04, risk=2.0), cfg=CFG)
    assert ev["cost_r"] == pytest.approx((0.06 + 0.04) / 2.0)
    assert ev["families"][FAM_PROB]["avoided_cost_r"] == pytest.approx(0.05)
    doc = build_report(closes=[_close(fees=0.06, funding=0.04)],
                       snapshots={s["candidate_id"]: s},
                       links={"F1": s["candidate_id"]}, cfg=CFG)
    sens = doc["families"][FAM_PROB]["cost_sensitivity"]
    assert [x["cost_multiplier"] for x in sens] == [0.5, 1.0, 2.0]
    assert sens[2]["avoided_cost_r"] == pytest.approx(2 * sens[1]["avoided_cost_r"])


def test_18_unmeasurable_cost_is_not_counted_as_zero():
    s = _snap(decision={"p_win": 0.05, "regime": "TREND_UP"})
    c = _close(fees=None, funding=None)
    c["raw"] = {}
    c["net_pnl"] = None
    ev = evaluate_trade(snapshot=s, close=c, cfg=CFG)
    assert ev["cost_r"] is None and ev["initial_risk_usdt"] is None
    doc = build_report(closes=[c], snapshots={s["candidate_id"]: s},
                       links={"F1": s["candidate_id"]}, cfg=CFG)
    assert doc["families"][FAM_PROB]["cost_sensitivity"][1]["n_cost_unknown"] == 1


# ------------------------------------------------------------------ 19-22: kapılar

def test_19_gates_are_fail_closed_when_a_quantity_cannot_be_measured():
    doc = build_report(closes=[], snapshots={}, links={}, cfg=CFG)
    assert doc["verdict"] == INSUFFICIENT_ENTRY_SAMPLE
    for fam in FAMILIES:
        gates = doc["promotion_gates"][fam]
        assert gates and not any(g["passed"] for g in gates), "ölçülemeyen kapı GEÇTİ sayıldı"
        assert any(g["code"] == "NO_LEAKAGE_POINT_IN_TIME" and not g["passed"] for g in gates)


def test_20_verdict_stays_insufficient_until_every_gate_passes():
    snaps, links, closes = _corpus(20)
    doc = build_report(closes=closes, snapshots=snaps, links=links, cfg=CFG)
    assert doc["verdict"] == INSUFFICIENT_ENTRY_SAMPLE
    assert doc["eligible_families"] == []
    for fam in FAMILIES:
        rep = doc["families"][fam]
        assert rep["verdict"] == INSUFFICIENT_ENTRY_SAMPLE
        assert rep["gates_passed"] < rep["gates_total"]
    failed = {g["code"] for g in doc["promotion_gates"][FAM_PROB] if not g["passed"]}
    assert "MIN_LINKED_CLOSES" in failed


def test_21_promotion_requires_linked_closes_and_calendar_days():
    """Kapılar ULAŞILABİLİR olmalı; aksi hâlde 'geçilemez kapı' bir güvenlik tiyatrosudur."""
    assert GATE_MIN_LINKED_CLOSES == 50 and GATE_MIN_DAYS == 30
    snaps, links, closes = {}, {}, []
    # Kaybedenlerin tamamı düşük p_win, kazananların tamamı yüksek → A ailesi GERÇEKTEN ayırır.
    for i in range(60):
        side = "LONG" if i % 2 == 0 else "SHORT"
        regime = "TREND_UP" if i % 2 == 0 else "RANGE"
        win = (i % 3 == 0)
        s = _snap(run_id="R", cycle_id=f"c{i}", symbol=f"S{i % 6}/USDT", direction=side,
                  decision={"p_win": 0.62 if win else 0.20, "consensus_score": 0.42,
                            "consensus_confidence": 0.6, "expected_r": 1.9, "regime": regime,
                            "dissent": [], "vetoes": []},
                  now=NOW - timedelta(days=70 - i))
        snaps[s["candidate_id"]] = s
        tid = f"P{i:03d}"
        links[tid] = s["candidate_id"]
        closes.append(_close(tid, r=(2.0 if win else -1.0), sym=f"S{i % 6}/USDT", side=side,
                             when=NOW - timedelta(days=60 - i)))
    doc = build_report(closes=closes, snapshots=snaps, links=links, cfg=CFG)
    a = doc["families"][FAM_PROB]
    assert a["n_blocked_loser"] > a["n_blocked_winner"]
    assert a["discrimination_youden_j"] > 0
    assert doc["verdict"] == ELIGIBLE_FOR_PAPER_BOUNDED, [
        g["code"] for g in doc["promotion_gates"][FAM_PROB] if not g["passed"]]
    # Terfiye UYGUN olmak, terfi ETMEK değildir.
    assert doc["auto_promotion"] is False and doc["applied_total"] == 0


def test_22_inverse_p_win_calibration_is_not_masked_by_a_fitted_threshold():
    """Üretimde `p_win` TERS ayrım yapıyordu (kazanan 0,343 / kaybeden 0,421).

    Challenger A bu örneklemde kazananları eler; rapor bunu POZİTİF bir iyileşme gibi
    göstermemeli ve kapı DÜŞMELİDİR.
    """
    snaps, links, closes = _corpus(60, inverse_p_win=True)
    doc = build_report(closes=closes, snapshots=snaps, links=links, cfg=CFG)
    a = doc["families"][FAM_PROB]
    assert a["n_blocked"] > 0, "eşik hiç ısırmıyorsa test bir şey kanıtlamaz"
    assert a["n_blocked_winner"] >= a["n_blocked_loser"], "kurgu ters kalibrasyonu taşımıyor"
    assert (a["discrimination_youden_j"] or 0.0) <= 0.0
    assert (a["delta_expectancy_r"] or 0.0) <= 0.0
    codes = {g["code"] for g in doc["promotion_gates"][FAM_PROB] if not g["passed"]}
    assert {"DISCRIMINATION_POSITIVE", "POSITIVE_EXPECTANCY_IMPROVEMENT"} <= codes
    assert doc["families"][FAM_PROB]["verdict"] == INSUFFICIENT_ENTRY_SAMPLE


# ------------------------------------------------------------------ 23-24: config

@pytest.mark.parametrize("bad", [
    {"mode": "PAPER_BOUNDED"}, {"mode": "ACTIVE"}, {"mode": "LIVE"}, {"mode": ""},
    {"auto_promotion": True}, {"max_snapshots_per_cycle": 0},
    {"policy": {"assumed_payoff_ratio": 0}}, {"policy": {"max_open_risk_fraction": 2.0}},
    {"policy": {"max_same_direction": 0}}, {"policy": {"prob_safety_margin": 0.9}},
])
def test_23_config_is_fail_closed(bad):
    from tradingbot.config_v3 import load_v3, validate_v3
    with pytest.raises(ConfigError):
        validate_v3(load_v3({"entry_selectivity": bad}))


def test_24_config_default_is_shadow_and_observation_only():
    from tradingbot.config_v3 import load_v3, validate_v3
    cfg = load_v3({})
    validate_v3(cfg)
    en = cfg.entry_selectivity
    assert en.mode == MODE_SHADOW and en.auto_promotion is False
    assert en.snapshot_enabled is True and en.max_snapshots_per_cycle >= 1
    assert ALLOWED_MODES == (MODE_SHADOW,) and MODE_SHADOW in KNOWN_MODES
    assert load_v3({"entry_selectivity": {"mode": "shadow"}}).entry_selectivity.mode == "SHADOW"


# ------------------------------------------------------------------ 25-27: motor e2e

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as E  # noqa: E402


def _fut_fingerprint(led) -> str:
    """Kanonik parmak izi — alan kümesi `ops.fingerprint` OTORİTESİNDEN gelir.

    Elle kopyalanan liste `take_profit` içeriyordu; öyle bir alan yok, dolayısıyla her
    pozisyon için sabit `None` hash'leniyordu (vacuous kanıt).
    """
    from tradingbot.ops.fingerprint import futures_fingerprint
    out = futures_fingerprint(led.positions)
    assert "take_profit" not in out["fields_used"]
    return out["fingerprint"][:16]


def test_25_engine_writes_entry_snapshots_and_a_shadow_report(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    summ = eng.tour(do_scan=False, obsidian=False, charts=False)
    st = eng.cfg.state_path
    p = st / "entry_snapshot.jsonl"
    assert p.exists(), "entry_snapshot.jsonl yazılmalı"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    snaps = [r for r in rows if r.get("kind") != "link"]
    assert snaps, "sıralamaya giren aday snapshot'ı yok"
    for r in snaps:
        assert r["provenance"]["sees_outcome"] is False
        assert r["link_status"] == LINKED and r["config_hash"]
        assert r["baseline_accepted"] in (True, False)
        for k in FORBIDDEN_OUTCOME_FIELDS:
            assert k not in r
        json.dumps(r, allow_nan=False)
    if summ["opened"]:
        links = {r["trade_id"] for r in rows if r.get("kind") == "link"}
        assert links == {str(x.id) for x in eng.ledger2.positions.values()}
    doc = json.loads((st / "entry_selectivity.json").read_text(encoding="utf-8"))
    assert doc["entry_mode"] == "SHADOW" and doc["applied_total"] == 0
    assert doc["auto_promotion"] is False
    assert doc["verdict"] == INSUFFICIENT_ENTRY_SAMPLE
    assert doc["replay_audit"]["synthetic_profitability"] is None


def test_26_active_decision_is_identical_with_the_entry_layer_disabled(tmp_path, monkeypatch):
    eng_a = E._engine(tmp_path / "a", monkeypatch, symbols=4)
    sa = eng_a.tour(do_scan=False, obsidian=False, charts=False)
    risk_a = json.loads((eng_a.cfg.state_path / "risk.json").read_text(encoding="utf-8"))

    eng_b = E._engine(tmp_path / "b", monkeypatch, symbols=4)
    eng_b.entry_snapshot_store = None          # giriş gözlemi KAPALI
    eng_b.entry_cfg = None
    sb = eng_b.tour(do_scan=False, obsidian=False, charts=False)
    risk_b = json.loads((eng_b.cfg.state_path / "risk.json").read_text(encoding="utf-8"))

    def _norm(rows):
        return [{k: v for k, v in r.items() if k not in ("at", "trade_id")} for r in rows]
    assert _norm(risk_a["last_decisions"]) == _norm(risk_b["last_decisions"])
    assert sa["opened"] == sb["opened"]
    assert sa["ledger"]["equity"] == sb["ledger"]["equity"]
    assert _fut_fingerprint(eng_a.ledger2) == _fut_fingerprint(eng_b.ledger2)
    assert not (eng_b.cfg.state_path / "entry_snapshot.jsonl").exists()


def test_27_open_position_fingerprint_is_unchanged_by_the_shadow_entry_layer(tmp_path,
                                                                            monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert eng.ledger2.positions
    before = _fut_fingerprint(eng.ledger2)
    ledger_bytes = (eng.cfg.state_path / "futures_ledger.json").read_bytes()
    for _ in range(3):
        eng._write_entry_eval(utc_now())
    assert _fut_fingerprint(eng.ledger2) == before, "pozisyon alanları DEĞİŞTİ"
    assert (eng.cfg.state_path / "futures_ledger.json").read_bytes() == ledger_bytes


# ------------------------------------------------------------------ 28-30: panel

from fastapi.testclient import TestClient  # noqa: E402

from tradingbot.dashboard.app import DashboardConfig, create_app  # noqa: E402


def _dirs(tmp: Path) -> tuple[Path, Path]:
    st, data = tmp / "state", tmp / "data"
    st.mkdir(), data.mkdir()
    (st / "futures_ledger.json").write_text(json.dumps(
        {"schema_version": 2, "wallet_balance": "100", "starting_equity": "100",
         "positions": {}, "history": [], "total_fees": "0"}), encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")
    (st / "learning.json").write_text(json.dumps(
        {"n_trades": 1, "n_wins": 1, "sum_r": 0.4, "lessons": [], "weights": {},
         "agent_weights": {}}), encoding="utf-8")
    return st, data


@pytest.mark.parametrize("doc", [
    None, {}, {"families": "bozuk"}, {"n_linked": float("nan")},
    {"families": {FAM_PROB: {"baseline": {"expectancy_r": float("inf")}}},
     "promotion_gates": {FAM_PROB: "liste değil"}, "trades": [{"families": None}]},
    {"replay_audit": {"sources": {"x": None}, "empty_in_every_source": ["a"]}},
])
def test_28_dashboard_never_returns_500_on_missing_or_broken_entry_report(tmp_path, doc):
    st, data = _dirs(tmp_path)
    if doc is not None:
        st.joinpath("entry_selectivity.json").write_text(json.dumps(doc, allow_nan=True),
                                                         encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    for path in ("/learning", "/api/entry-selectivity", "/llm", "/api/llm-status", "/",
                 "/health/live"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:400])
    body = c.get("/api/entry-selectivity").text
    assert "NaN" not in body and "Infinity" not in body


def test_29_dashboard_separates_promotion_evidence_from_observation(tmp_path):
    st, data = _dirs(tmp_path)
    st.joinpath("entry_selectivity.json").write_text(json.dumps({
        "schema_version": "entry_eval_v1", "entry_mode": "SHADOW", "applied_total": 0,
        "auto_promotion": False, "n_evaluated": 5, "n_linked": 1, "n_legacy_memory": 4,
        "observation_days": 1.5, "verdict": "INSUFFICIENT_ENTRY_SAMPLE",
        "leakage": {"clean": True, "checked": 1, "state": "ok"},
        "snapshot_store": {"snapshots": 12, "links": 1},
        "snapshot_cycle": {"candidates": 12, "written": 12, "errors": 0},
        "families": {FAM_PROB: {"n_evaluated": 1, "n_blocked": 1, "n_blocked_loser": 1,
                                "n_blocked_winner": 0, "avoided_loss_r": 1.0,
                                "missed_gain_r": 0.0, "discrimination_youden_j": 1.0,
                                "baseline": {"expectancy_r": -1.0},
                                "counterfactual": {"expectancy_r": 0.0},
                                "delta_expectancy_r": 1.0, "gates_passed": 3,
                                "gates_total": 14}},
        "promotion_gates": {FAM_PROB: [{"code": "MIN_LINKED_CLOSES", "passed": False,
                                        "detail": "1/50"}]},
        "replay_audit": {"verdict": "NOT_REPLAYABLE", "reason_tr": "eksik alanlar",
                         "sources": {"decision_journal": {"n_rows": 50, "complete": False,
                                                          "missing_fields": ["a", "b"],
                                                          "completely_empty_fields": ["spread_pct"]}},
                         "closes": {"total": 20, "linked_to_decision": 0},
                         "empty_in_every_source": ["spread_pct", "code_sha"],
                         "synthetic_profitability": None},
        "trades": [{"trade_id": "T1", "symbol": "ETH/USDT", "direction": "LONG",
                    "actual_r": -1.0, "evidence_grade": "PROMOTION",
                    "families": {FAM_PROB: {"decision": "VETO"}}},
                   {"trade_id": "L1", "symbol": "SOL/USDT", "direction": "LONG",
                    "actual_r": 2.0, "evidence_grade": "OBSERVATION_ONLY",
                    "families": {FAM_PROB: {"decision": "ACCEPT"}}}],
    }), encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    html = c.get("/learning").text
    assert "Giriş seçiciliği" in html and "SHADOW" in html
    assert "YETERSİZ ÖRNEK" in html and "TERFİ" in html and "GÖZLEM" in html
    assert "NOT_REPLAYABLE" in html and "spread_pct" in html
    js = c.get("/api/entry-selectivity").json()
    assert js["available"] is True and js["applied_total"] == 0
    assert js["auto_promotion"] is False
    assert js["verdict"] == "INSUFFICIENT_ENTRY_SAMPLE"
    assert js["snapshot_coverage"]["rows_tail"] == 0


def test_29b_dashboard_and_report_carry_policy_config_code_identity(tmp_path):
    """Kimliğini söylemeyen bir kanıt belgesi denetlenemez."""
    st, data = _dirs(tmp_path)
    st.joinpath("entry_selectivity.json").write_text(json.dumps({
        "schema_version": "entry_eval_v1", "entry_mode": "SHADOW", "applied_total": 0,
        "auto_promotion": False, "verdict": "INSUFFICIENT_ENTRY_SAMPLE",
        "policy_version": "entry_v1.0.0", "config_id": "09a3f31c837e3012",
        "code_sha": "619386994ec548870e55a9849ea36c26f0e25ab8",
        "config_hash": "3114444fb6cd2cda6ad54105aaaaaaaa",
        "run_id": "run_TEST", "generated_at": "2026-09-02T19:06:26+00:00",
        "n_linked": 0, "n_legacy_memory": 0, "families": {}, "promotion_gates": {},
    }), encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    html = c.get("/learning").text
    assert "Politika / config / kod kimliği" in html
    for token in ("entry_v1.0.0", "09a3f31c837e3012", "619386994ec5", "run_TEST"):
        assert token in html, token
    js = c.get("/api/entry-selectivity").json()
    assert js["policy_version"] == "entry_v1.0.0"
    assert js["config_id"] == "09a3f31c837e3012"
    assert js["code_sha"].startswith("6193869")
    assert js["config_hash"]


@pytest.mark.parametrize("status,expect", [
    ("DISABLED", "DISABLED"), ("NOT_CONFIGURED", "NOT_CONFIGURED"), ("NO_CALLS", "NO_CALLS"),
])
def test_30_llm_page_reports_the_real_state_and_never_prints_a_secret(tmp_path, status, expect):
    st, data = _dirs(tmp_path)
    st.joinpath("llm_status.json").write_text(json.dumps({
        "schema_version": "llm_status_v1", "status": status, "reason_tr": "ölçüldü",
        "mode": "POSTMORTEM_ONLY", "provider": "noop", "service_wired": False,
        "api_key_env": "ANTHROPIC_API_KEY", "api_key_present": False, "calls_recorded": 0,
        "cannot_execute": True}), encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    html = c.get("/llm").text
    assert expect in html and "ANTHROPIC_API_KEY" in html
    assert "sk-" not in html
    js = c.get("/api/llm-status").json()
    assert js["status"] == status and js["api_key_present"] is False
    assert "api_key_value" not in js and js["cannot_execute"] is True
    # Durum dosyası hiç yoksa panel "bilinmiyor" der, "kullanılmıyor" DEMEZ.
    st.joinpath("llm_status.json").unlink()
    c2 = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    assert c2.get("/api/llm-status").json()["status"] == "UNKNOWN"


# ------------------------------------------------------------------ 31-32: Faz 5 replay

def test_31_replay_audit_is_fail_closed_on_production_shaped_history():
    """2026-09-02 üretim şekli: `opportunity` yok, likidite boş, `code_sha` boş."""
    journal = [{"outcome_kind": "ACCEPTED", "p_win": 0.42, "confidence": 0.6,
                "expected_r": 1.9, "regime": "TREND_UP", "leverage": 2,
                "features": {"atr_pct": 2.6}, "code_sha": None, "config_hash": None,
                "policy_id": None, "market_type": None, "setup": None, "price": None}
               for _ in range(50)]
    out = replay_audit(journal_rows=journal, memory_rows=[], snapshots=[],
                       closes=[_close("T1"), _close("T2")], links={})
    assert out["verdict"] == NOT_REPLAYABLE
    jr = out["sources"]["decision_journal"]
    assert jr["n_rows"] == 50 and jr["complete"] is False
    for f in ("conservative_net_edge_r", "spread_pct", "est_slippage_pct", "depth_ratio",
              "liquidity_ok", "code_sha", "config_hash", "market_type", "setup"):
        assert f in jr["completely_empty_fields"], f
    assert out["closes"]["linked_to_decision"] == 0 and out["closes"]["total"] == 2
    assert set(out["empty_in_every_source"]) >= {"spread_pct", "code_sha", "liquidity_ok"}


def test_32_replay_audit_never_fabricates_profitability():
    out = replay_audit(journal_rows=[], memory_rows=[], snapshots=[], closes=[], links={})
    assert out["synthetic_profitability"] is None

    # Denetim yalnız ALAN BULUNABİLİRLİĞİ ölçer: çıktıda hiçbir hesaplanmış kârlılık ANAHTARI
    # olmamalı. (Alan ADI olarak geçen `net_expectancy_r` bir sonuç değil, aranan alandır.)
    banned_keys = {"expectancy_r", "profit_factor", "net_r", "total_r", "win_rate", "pnl",
                   "max_drawdown_r", "tail_loss_r_cvar5", "delta_expectancy_r"}

    def _keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield str(k)
                yield from _keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from _keys(v)
    assert banned_keys.isdisjoint(set(_keys(out))), "replay denetimi kârlılık üretti"
    # Alan sayımı `0.0` ile "hiç yok"u ayırır.
    cov = field_coverage([{"p_win": 0.0, "spread_pct": None}], ("p_win", "spread_pct"))
    assert cov["per_field"]["p_win"]["filled"] == 1
    assert cov["per_field"]["spread_pct"]["filled"] == 0
    assert set(REQUIRED_FIELDS) >= {"conservative_net_edge_r", "code_sha", "liquidity_ok"}
    assert journal_to_candidate({})["p_win"] is None
    assert memory_to_candidate({})["entry_price"] is None


# ------------------------------------------------------------------ 33-37: bütünlük

def test_33_bootstrap_confidence_interval_is_deterministic():
    xs = [0.4, -1.2, 2.0, -0.3, 0.9, -1.5, 0.1, 0.7]
    a, b = bootstrap_ci(xs), bootstrap_ci(list(xs))
    assert a == b and a["state"] == "ok" and a["lo"] <= a["mean"] <= a["hi"]
    assert bootstrap_ci([])["state"] == "insufficient_sample"
    assert bootstrap_ci([0.5])["excludes_zero"] is False
    assert bootstrap_ci([0.0, 0.0, 0.0])["state"] == "degenerate"
    assert bootstrap_ci([1.0, 1.0, 1.0])["excludes_zero"] is True


def test_34_trade_link_is_a_separate_row_and_never_rewrites_the_snapshot(tmp_path):
    st = EntrySnapshotStore(tmp_path / "s.jsonl")
    s = _snap()
    st.append(s)
    body_before = (tmp_path / "s.jsonl").read_text(encoding="utf-8")
    assert st.link_trade(s["candidate_id"], "F42") is True
    body_after = (tmp_path / "s.jsonl").read_text(encoding="utf-8")
    assert body_after.startswith(body_before), "snapshot satırı YENİDEN YAZILDI"
    assert st.trade_links() == {"F42": s["candidate_id"]}
    stored = st.by_candidate()[s["candidate_id"]]
    for k in FORBIDDEN_OUTCOME_FIELDS:
        assert k not in stored


def test_35_report_is_json_safe_and_carries_no_nan():
    snaps, links, closes = _corpus(12)
    closes[0]["r_multiple"] = None                # ölçülemeyen sonuç
    doc = build_report(closes=closes, snapshots=snaps, links=links, cfg=CFG,
                       risk_budget_usdt=6.0)
    blob = json.dumps(doc, allow_nan=False, default=str)
    assert "NaN" not in blob and "Infinity" not in blob
    assert doc["n_no_outcome"] == 1
    ns = evaluate_trade(snapshot={}, close=_close("X"), cfg=CFG)
    assert ns["status"] == NO_SNAPSHOT and ns["families"] == {}


def test_36_leakage_report_flags_a_snapshot_that_saw_the_outcome():
    s = _snap()
    ev = evaluate_trade(snapshot=s, close=_close(when=NOW + timedelta(hours=1)), cfg=CFG)
    assert leakage_report([ev], {s["candidate_id"]: s})["clean"] is True
    dirty = dict(s)
    dirty["provenance"] = dict(s["provenance"]) | {"sees_outcome": True}
    rep = leakage_report([ev], {s["candidate_id"]: dirty})
    assert rep["clean"] is False and rep["state"] == "violation"
    leaked = dict(s) | {"r_multiple": -1.0}
    assert leakage_report([ev], {s["candidate_id"]: leaked})["forbidden_fields"]
    past = dict(s) | {"ts": (NOW + timedelta(days=9)).isoformat()}
    assert leakage_report([ev], {s["candidate_id"]: past})["snapshot_after_close"]


def test_37_paper_and_live_safety_invariants_hold():
    """Bu katman hiçbir modda emir yolunu açamaz."""
    assert ALLOWED_MODES == ("SHADOW",)
    from tradingbot.config_v3 import load_v3, validate_v3
    for mode in ("PAPER", "TESTNET", "OBSERVE"):
        cfg = load_v3({"mode": {"mode": mode}})
        validate_v3(cfg)
        assert cfg.entry_selectivity.mode == "SHADOW"
    # Ölçüm kaynağı `DEFAULTED` işaretlenir: varsayılan bir istatistik ÖLÇÜM DEĞİLDİR.
    s = _snap(opportunity={"conservative_net_edge_r": 0.5, "avg_win_r": 1.6, "avg_loss_r": 1.0,
                           "sample_size": 0})
    assert s["sources"]["avg_win_r"] == DEFAULTED and s["sources"]["avg_loss_r"] == DEFAULTED
    assert math.isfinite(s["avg_win_r"])
