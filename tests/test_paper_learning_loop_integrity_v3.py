"""PAPER LEARNING LOOP INTEGRITY V3 — kapanan her PAPER işleminin öğrenilmesinin regresyonları.

Kapsam sözleşmesi (18 zorunlu regresyon, görev metniyle birebir):

 1. Her final close TAM BİR outcome üretir.
 2. Her outcome TAM BİR ders üretir.
 3. Restart/retry duplicate ÜRETMEZ.
 4. Outcome yazılıp ders yazılmadan çökme olursa sonraki tur dersi TAMAMLAR.
 5. Kısmi TP final ders DEĞİLDİR.
 6. Legacy işlem UYDURMA decision ID almaz.
 7. Yeni işlem entry decision ID ile kapanır.
 8. Reconcile ikinci çalıştırmada SIFIR değişiklik.
 9. Fee/funding ders teşhisine ULAŞIR.
10. Ajan ağırlık delta sınırı korunur.
11. Düşük örnekte promotion YOK.
12. NaN/Infinity yayımlanmaz.
13. Öğrenme risk/size/leverage/stop/TP DEĞİŞTİREMEZ.
14. Değerlendirilmemiş ekonomi `UNKNOWN` gösterilir (0.00/0.50 değil).
15. `REDUCE/EXIT` aktif değilse `ADVISORY_ONLY`.
16. Dashboard eksik/bozuk/eski şemada 500 VERMEZ.
17. Quant örneklem sayısı kanonik geçmişle karşılaştırılır.
18. Ana defter reconcile sırasında BYTE-IDENTICAL kalır.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.learn.close_chain import (canonical_closes, chain_report, close_event_id,
                                          cost_drags, legacy_view, r_normalized)
from tradingbot.learn.memory import TradeMemory
from tradingbot.learn.position_mgmt import (ADVISORY_ONLY, EXECUTABLE, UNKNOWN,
                                            ManagementExecutor, build_snapshot_doc,
                                            economics_available, management_snapshot,
                                            proposed_action, r_metrics)
from tradingbot.learn.provenance import (LEGACY_UNLINKED, LINKED, PROVENANCE_FIELDS,
                                         ProvenanceStore, build_entry_provenance,
                                         legacy_provenance, link_summary)
from tradingbot.learn.reconcile import (LearnedIndex, apply_plan, bootstrap_index, build_plan,
                                        complete_missing_chain, note_learned)
from tradingbot.learning import Learner

# --------------------------------------------------------------------------- fixture'lar


def _close(tid: str, *, symbol="ETH/USDT", side="LONG", pnl=-1.0, r=-1.0, entry=100.0,
           stop=98.0, fees=0.02, funding=-0.01, mfe=0.5, mae=-2.0, exit_reason="stop",
           tp1=False, opened="2026-08-20T10:00:00+00:00",
           closed="2026-08-21T10:00:00+00:00") -> dict:
    """Gerçek `TradeRecord.to_legacy_dict()` şekline uygun kapanış kaydı (float değerler)."""
    return {"id": tid, "symbol": symbol, "side": side, "entry": entry, "exit_reason": exit_reason,
            "closed_at": closed, "opened_at": opened, "pnl": pnl, "net_pnl": pnl,
            "gross_pnl": pnl + fees, "fees": fees, "funding": funding, "r_multiple": r,
            "mae_pct": mae, "mfe_pct": mfe, "bars_held": 6, "leverage": 2,
            "setup_type": "pullback", "trigger_text": "t", "tp1_done": tp1,
            "quantity": 1.0, "exit_price": entry + (pnl / 1.0),
            "features": {"setup_type": "pullback", "initial_stop": stop, "rr": 2.0,
                         "p_win": 0.45, "bias_trend": 0.4, "conf_trend": 0.8,
                         "bias_momentum": -0.3, "conf_momentum": 0.6}}


@pytest.fixture()
def env(tmp_path: Path):
    """İzole öğrenme ortamı: defter geçmişi + hafıza + öğrenen + indeks + provenance."""
    mem = TradeMemory(tmp_path / "trade_memory.jsonl", source="LIVE_PAPER")
    learner = Learner(tmp_path / "learning.json", min_trades=2)
    idx = LearnedIndex(tmp_path / "learned_closes.jsonl")
    prov = ProvenanceStore(tmp_path / "entry_provenance.jsonl")
    return {"dir": tmp_path, "memory": mem, "learner": learner, "index": idx, "prov": prov}


def _run_chain(env, history, **kw):
    return complete_missing_chain(history=history, memory=env["memory"],
                                  learner=env["learner"], index=env["index"],
                                  provenance_store=env["prov"], **kw)


def _lesson_ids(env) -> list[str]:
    return [x["id"] for x in env["learner"].state.lessons]


# ------------------------------------------------------------------ 1-2: her kapanış öğrenilir


def test_01_every_final_close_produces_exactly_one_outcome(env):
    hist = [_close("F1"), _close("F2", symbol="SOL/USDT"), _close("F3", symbol="BTC/USDT")]
    res = _run_chain(env, hist)
    assert res["outcomes_added"] == 3, res
    exits = [r for r in env["memory"].iter_rows() if r.get("kind") == "exit"]
    assert len(exits) == 3
    assert sorted(r["trade_id"] for r in exits) == ["F1", "F2", "F3"]
    # TAM BİR tane: aynı trade_id iki exit satırı üretmemeli
    assert len({r["trade_id"] for r in exits}) == 3


def test_02_every_outcome_produces_exactly_one_lesson(env):
    hist = [_close("F1"), _close("F2", symbol="SOL/USDT", pnl=1.5, r=1.5, exit_reason="hedef2")]
    _run_chain(env, hist)
    assert sorted(_lesson_ids(env)) == ["F1", "F2"]
    rep = build_plan(history=hist, memory=env["memory"], learner=env["learner"],
                     index=env["index"], provenance_store=env["prov"])["report"]
    assert rep["canonical_final_closes"] == 2
    assert rep["outcomes"] == 2 and rep["lessons"] == 2
    assert rep["missing_outcome"] == 0 and rep["missing_lesson"] == 0
    assert rep["duplicate_lesson_count"] == 0


# ------------------------------------------------------------------ 3-4: crash / retry


def test_03_restart_and_retry_produce_no_duplicates(env):
    hist = [_close("F1"), _close("F2", symbol="SOL/USDT")]
    _run_chain(env, hist)
    before_les, before_out = len(_lesson_ids(env)), len(list(env["memory"].iter_rows()))
    for _ in range(4):                                   # dört kez yeniden çalıştır
        r = _run_chain(env, hist)
        assert r["outcomes_added"] == 0 and r["lessons_added"] == 0, r
    assert len(_lesson_ids(env)) == before_les
    assert len(list(env["memory"].iter_rows())) == before_out
    assert sorted(_lesson_ids(env)) == ["F1", "F2"]


def test_04_crash_after_outcome_completes_the_lesson_next_round(env):
    """Outcome yazıldı, ders yazılmadan süreç öldü. Sonraki tur EKSİK ADIMI tamamlamalı."""
    hist = [_close("F1", pnl=-0.9, r=-0.95)]
    env["memory"].record_exit("F1", hist[0], price_path=[], postmortem={})   # yalnız outcome
    assert _lesson_ids(env) == []
    res = _run_chain(env, hist)
    assert res["outcomes_added"] == 0, "mevcut outcome TEKRAR yazılmamalı"
    assert res["lessons_added"] == 1, res
    assert _lesson_ids(env) == ["F1"]
    # ikinci tur sıfır değişiklik
    assert _run_chain(env, hist)["lessons_added"] == 0


def test_04b_crash_before_any_step_completes_both(env):
    """Defter kapanışı kalıcı, hiçbir öğrenme adımı yok — ikisi de tamamlanmalı."""
    hist = [_close("F9", symbol="AVAX/USDT")]
    res = _run_chain(env, hist)
    assert (res["outcomes_added"], res["lessons_added"]) == (1, 1)


# ------------------------------------------------------------------ 5: kısmi TP


def test_05_partial_tp_is_not_a_final_close(env):
    """`tp1_done=True` bir kapanış KISMİ AZALTMA değil, TP1'den sonra gelen FİNAL kapanıştır.

    Kanonik kaynak `_finalize`dir: `_close_part` history'ye satır EKLEMEZ. Bu yüzden TP1
    azaltması ayrı bir ders üretemez; tek kapanış tek ders verir.
    """
    hist = [_close("F1", tp1=True, pnl=0.4, r=0.6, exit_reason="başa-baş stop")]
    _run_chain(env, hist)
    assert _lesson_ids(env) == ["F1"], "kısmi TP ikinci bir ders üretmemeli"
    closes = canonical_closes(hist)
    assert len(closes) == 1 and closes[0]["tp1_done"] is True
    # aynı trade_id'nin iki farklı history satırı olsa bile kimlik kapanış anına bağlıdır
    ev1 = close_event_id("F1", "2026-08-21T10:00:00+00:00", "başa-baş stop")
    ev2 = close_event_id("F1", "2026-08-21T10:00:00+00:00", "hedef1")
    assert ev1 != ev2, "farklı çıkış nedeni farklı kapanış olayıdır"


def test_05b_canonical_closes_dedupes_identical_close_events():
    hist = [_close("F1"), _close("F1")]                   # aynı kapanış iki kez listelendi
    assert len(canonical_closes(hist)) == 1


# ------------------------------------------------------------------ 6-7: entry link


def test_06_legacy_trade_never_gets_a_fabricated_decision_id(env):
    hist = [_close("F1"), _close("F2", symbol="SOL/USDT")]
    _run_chain(env, hist)
    loaded = env["prov"].load()
    assert set(loaded) == {"F1", "F2"}
    for tid, rec in loaded.items():
        assert rec["link_status"] == LEGACY_UNLINKED
        assert rec["entry_decision_id"] is None, "kimlik UYDURULAMAZ"
        assert rec["legacy_reason"] == "NO_ENTRY_DECISION_RECORD"
        for f in PROVENANCE_FIELDS:                       # sözleşme: anahtar DAİMA var
            assert f in rec
    # ...ama ekonomi defterden okunabildiği için ders yine üretilmiş olmalı
    assert sorted(_lesson_ids(env)) == ["F1", "F2"]


def test_07_new_trade_closes_with_its_entry_decision_id(env):
    """Açılışta yazılan provenance, kapanışta kaybolmadan bağlantıyı taşımalı."""
    env["prov"].record(build_entry_provenance(
        trade_id="F1", symbol="ETH/USDT", direction="LONG",
        decision_id="dec_abc123", journal_id="snap_1", run_id="run_1", cycle_id=7,
        code_sha="deadbeef", config_hash="cfg1", policy_id="champion",
        p_win=0.55, expected_r=1.8, expected_net_return=0.42,
        features={"atr_pct": 1.2}, specialist_scores={"trend": 0.5},
        regime="TREND_UP", risk_decision={"risk_usdt": 2.0},
        stop=98.0, targets=[104.0, 108.0], size_usdt=50.0, leverage=2))
    hist = [_close("F1")]
    _run_chain(env, hist)
    rec = env["prov"].get("F1")
    assert rec["link_status"] == LINKED
    assert rec["entry_decision_id"] == "dec_abc123"
    assert rec["entry_code_sha"] == "deadbeef" and rec["entry_config_hash"] == "cfg1"
    assert rec["entry_p_win"] == 0.55 and rec["entry_expected_r"] == 1.8
    assert rec["entry_stop"] == 98.0 and rec["entry_targets"] == [104.0, 108.0]
    assert rec["entry_leverage"] == 2.0 and rec["entry_size_usdt"] == 50.0
    rep = build_plan(history=hist, memory=env["memory"], learner=env["learner"],
                     index=env["index"], provenance_store=env["prov"])["report"]
    assert rep["entry_linked"] == 1 and rep["legacy_unlinked"] == 0
    assert rep["rows"][0]["entry_decision_id"] == "dec_abc123"


def test_07b_provenance_first_write_wins_and_is_idempotent(env):
    a = build_entry_provenance(trade_id="F1", symbol="ETH/USDT", direction="LONG",
                               decision_id="first")
    b = build_entry_provenance(trade_id="F1", symbol="ETH/USDT", direction="LONG",
                               decision_id="second")
    assert env["prov"].record(a) is True
    assert env["prov"].record(b) is False, "giriş kararı sonradan DEĞİŞTİRİLEMEZ"
    assert env["prov"].get("F1")["entry_decision_id"] == "first"


def test_07c_link_summary_counts_are_measured_not_assumed():
    prov = {"A": {"link_status": LINKED}, "B": {"link_status": LEGACY_UNLINKED}}
    s = link_summary(prov, ["A", "B", "C"])
    assert (s["linked"], s["legacy_unlinked"], s["missing"]) == (1, 1, 1)
    assert s["missing_ids"] == ["C"]


# ------------------------------------------------------------------ 8: idempotency


def test_08_reconcile_second_apply_changes_nothing(env):
    hist = [_close(f"F{i}", symbol=f"S{i}/USDT") for i in range(1, 6)]
    plan1 = build_plan(history=hist, memory=env["memory"], learner=env["learner"],
                       index=env["index"], provenance_store=env["prov"])
    assert plan1["will_add_lessons"] == 5
    apply_plan(plan1, history=hist, memory=env["memory"], learner=env["learner"],
               index=env["index"], provenance_store=env["prov"])
    plan2 = build_plan(history=hist, memory=env["memory"], learner=env["learner"],
                       index=env["index"], provenance_store=env["prov"])
    assert plan2["pending"] == [] and plan2["will_add_lessons"] == 0
    assert plan2["will_mark_legacy"] == 0 and plan2["files_to_change"] == []
    res2 = apply_plan(plan2, history=hist, memory=env["memory"], learner=env["learner"],
                      index=env["index"], provenance_store=env["prov"])
    assert (res2["outcomes_added"], res2["lessons_added"]) == (0, 0)
    assert len(_lesson_ids(env)) == 5


def test_08b_bootstrap_marks_existing_lessons_without_relearning(env):
    """İndeks yokken mevcut dersler YENİDEN ÖĞRENİLMEZ; yalnız indekse taşınır."""
    hist = [_close("F1"), _close("F2", symbol="SOL/USDT")]
    for h in hist:                                        # "eski" öğrenme: yalnız ders var
        env["learner"].learn(dict(h))
    assert len(_lesson_ids(env)) == 2
    n = bootstrap_index(history=hist, learner=env["learner"], index=env["index"])
    assert n == 2
    res = _run_chain(env, hist)
    assert res["lessons_added"] == 0, "bootstrap sonrası yeniden öğrenme OLMAMALI"
    assert len(_lesson_ids(env)) == 2


def test_08c_archived_lesson_is_not_relearned(env):
    """Ders sıcak pencereden çıkarsa (arşiv) tekrar üretilmemeli — indeks otoritedir."""
    hist = [_close("F1")]
    _run_chain(env, hist)
    env["learner"].state.lessons = []                     # sıcak pencereden düştü
    env["learner"].save()
    res = _run_chain(env, hist)
    assert res["lessons_added"] == 0, "arşivlenmiş ders İKİNCİ kez üretilemez"


def test_08d_note_learned_is_deterministic_and_single_shot(env):
    c = _close("F1")
    assert note_learned(env["index"], c, {"id": "F1"}) is True
    assert note_learned(env["index"], c, {"id": "F1"}) is False
    assert env["index"].trade_ids() == {"F1"}


# ------------------------------------------------------------------ 9: maliyet teşhisi


def test_09_fee_and_funding_reach_the_lesson_diagnosis(env):
    """Maliyet alanları defterden derse ULAŞMALI — `None` kalmamalı."""
    hist = [_close("F1", pnl=-0.5, r=-1.1, fees=0.05, funding=-0.02)]
    _run_chain(env, hist)
    obs = env["learner"].state.lessons[-1]["observation"]
    assert obs["fee_drag_r"] is not None and obs["fee_drag_r"] > 0
    assert obs["funding_drag_r"] is not None
    assert obs["cost_drag_total_r"] is not None
    # zincir raporu da aynı büyüklüğü bağımsız hesaplar
    d = cost_drags(canonical_closes(hist)[0])
    assert d["fee_drag_r"] is not None and d["funding_drag_r"] is not None


def test_09b_cost_dominated_is_reachable_in_production_path(env):
    from tradingbot.learn.edge_execution import COST_DOMINATED
    hist = [_close("F1", pnl=-0.1, r=-0.2, fees=0.30, funding=-0.10, mfe=2.0, mae=-0.5)]
    _run_chain(env, hist)
    obs = env["learner"].state.lessons[-1]["observation"]
    assert COST_DOMINATED in obs["observation_codes"], obs["observation_codes"]


def test_09c_r_normalization_separates_entry_from_exit_problems():
    """Yüzde bazlı MFE yanıltır; R'ye bölünce sınıf değişir (KORU/BZ dersi)."""
    wide = canonical_closes([_close("F1", entry=100.0, stop=85.0, mfe=3.7, mae=-15.0)])[0]
    tight = canonical_closes([_close("F2", entry=100.0, stop=98.0, mfe=3.7, mae=-2.0)])[0]
    assert r_normalized(wide)["mfe_r"] < 0.3, "geniş stop: giriş kalitesi sorunu"
    assert r_normalized(tight)["mfe_r"] > 1.5, "dar stop: çıkış politikası sorunu"


def test_09d_cost_drag_is_none_when_risk_is_not_measurable():
    c = canonical_closes([_close("F1", r=0.0, pnl=0.0)])[0]
    d = cost_drags(c)
    assert d["fee_drag_r"] is None and d["funding_drag_r"] is None, "sessiz 0 YASAK"


# ------------------------------------------------------------------ 10-11: sınırlı öğrenme


def test_10_agent_weight_delta_stays_bounded_per_trade(env):
    """Tek işlem stratejiyi sert değiştiremez: |delta| < 0.05 ve before/delta/after kaydedilir."""
    hist = [_close(f"F{i}", symbol=f"S{i}/USDT", pnl=-1.0, r=-1.0) for i in range(1, 9)]
    _run_chain(env, hist)
    for les in env["learner"].state.lessons:
        ac = les.get("agent_contributions") or []
        assert ac, "ajan katkısı kaydedilmeli"
        for a in ac:
            assert {"weight_before", "applied_delta", "weight_after"} <= set(a)
            assert abs(a["applied_delta"]) < 0.05, (a["agent"], a["applied_delta"])
            assert abs((a["weight_before"] + a["applied_delta"]) - a["weight_after"]) < 1e-6


def test_11_low_sample_is_flagged_and_no_policy_promotion(env):
    hist = [_close("F1")]
    _run_chain(env, hist)
    les = env["learner"].state.lessons[-1]
    assert les["evidence_level"] == "OBSERVATION"
    assert les["policy_status"] == "OBSERVATION", "tek işlem POLİTİKA olamaz"
    for a in les["agent_contributions"]:
        assert a["evidence_quality"] == "LOW_SAMPLE"
    for h in (les.get("hypotheses") or []):
        assert h.get("evidence_level", "OBSERVATION") == "OBSERVATION"


def test_11b_single_trade_never_claims_the_model_was_right_or_wrong(env):
    hist = [_close("F1", pnl=-1.0, r=-1.0)]
    _run_chain(env, hist)
    les = env["learner"].state.lessons[-1]
    blob = json.dumps(les, ensure_ascii=False).upper()
    for banned in ("MODEL_WAS_RIGHT", "MODEL_WAS_WRONG", "MODEL HAKLIYDI", "MODEL YANILDI"):
        assert banned not in blob, banned
    # Nedensellik iddiası tek işlemden KURULAMAZ: bayrak False olmalı, hipotezler OBSERVATION.
    assert les["causal_claim"] is False
    assert les["observation"]["causal_claim"] is False
    hyps = les.get("hypotheses") or []
    assert hyps, "hipotez üretilmeli (gözlem seviyesinde)"
    for h in hyps:
        assert h["causal_claim"] is False, h["code"]
        assert h["evidence_level"] == "OBSERVATION", h["code"]


# ------------------------------------------------------------------ 12: sonlu sayı


def _assert_finite(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_finite(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_finite(v, f"{path}[{i}]")
    elif isinstance(obj, float):
        assert math.isfinite(obj), f"sonlu olmayan sayı: {path}={obj}"


def test_12_no_nan_or_infinity_is_ever_published(env):
    hist = [_close("F1", pnl=float("nan"), r=float("inf")),
            _close("F2", symbol="SOL/USDT", fees=float("-inf"), mfe=float("nan"))]
    closes = canonical_closes(hist)
    for c in closes:
        _assert_finite({k: v for k, v in c.items() if k != "raw"})
    rep = chain_report(closes, outcome_ids=[], lesson_ids=[], provenance={})
    _assert_finite({k: v for k, v in rep.items() if k != "rows"})
    for row in rep["rows"]:
        _assert_finite(row)
    # RFC-JSON: bare NaN/Infinity serileşmemeli
    json.dumps(rep["rows"], allow_nan=False)


def test_12b_provenance_rejects_non_finite_numbers():
    rec = build_entry_provenance(trade_id="F1", symbol="E/U", direction="LONG",
                                 p_win=float("nan"), expected_r=float("inf"),
                                 features={"a": float("nan"), "b": 1.5}, stop=float("-inf"))
    assert rec["entry_p_win"] is None and rec["entry_expected_r"] is None
    assert rec["entry_stop"] is None
    assert rec["entry_features"] == {"b": 1.5}
    json.dumps(rec, allow_nan=False)


# ------------------------------------------------------------------ 13: öğrenme risk üretemez


def test_13_learning_cannot_produce_risk_size_leverage_stop_or_tp(env):
    """Ders sözlüğü bir risk/emir parametresi TAŞIYAMAZ."""
    hist = [_close("F1"), _close("F2", symbol="SOL/USDT", pnl=2.0, r=2.0, exit_reason="hedef2")]
    _run_chain(env, hist)
    forbidden = {"leverage_override", "position_size", "size_usdt", "notional_override",
                 "stop_override", "take_profit_override", "risk_budget", "risk_per_trade_pct",
                 "max_leverage", "new_stop", "new_tp"}
    for les in env["learner"].state.lessons:
        keys = set(les) | set(les.get("observation") or {})
        assert not (keys & forbidden), keys & forbidden

    def _walk(o):
        if isinstance(o, dict):
            assert not (set(o) & forbidden), set(o) & forbidden
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
    _walk(env["learner"].state.lessons)


def test_13b_reconcile_declares_and_keeps_ledger_read_only(env, tmp_path):
    hist = [_close("F1")]
    plan = build_plan(history=hist, memory=env["memory"], learner=env["learner"],
                      index=env["index"], provenance_store=env["prov"])
    assert plan["ledger_written"] is False
    res = apply_plan(plan, history=hist, memory=env["memory"], learner=env["learner"],
                     index=env["index"], provenance_store=env["prov"])
    assert res["ledger_written"] is False
    assert not (tmp_path / "futures_ledger.json").exists(), "defter YAZILMAMALI"


# ------------------------------------------------------------------ 14-15: pozisyon yönetimi


class _Pos:
    """Açık pozisyon kılığı (`accounting.models.Position` alan adlarıyla)."""

    def __init__(self, **kw):
        self.id = kw.get("id", "F1")
        self.symbol = kw.get("symbol", "ETH/USDT")
        self.side = kw.get("side", "LONG")
        self.qty = Decimal("1")
        self.entry_avg = Decimal(str(kw.get("entry", 100.0)))
        self.initial_stop = Decimal(str(kw.get("stop", 98.0)))
        self.stop = self.initial_stop
        self.targets = [Decimal("104"), Decimal("108")]
        self.targets_hit = kw.get("targets_hit", 0)
        self.tp1_done = kw.get("tp1_done", False)
        self.leverage = 2
        self.opened_at = kw.get("opened_at", "2026-08-20T10:00:00+00:00")
        self.bars_held = 6
        self.mfe_pct = Decimal(str(kw.get("mfe", 4.0)))
        self.mae_pct = Decimal(str(kw.get("mae", -1.0)))
        self.fees_paid = Decimal("0.05")
        self.funding_paid = Decimal("0.02")
        self.funding_received = Decimal("0.0")


class _Dec:
    def __init__(self, verdict="HOLD", opportunity=None, p_win=0.382, regime="TREND_UP"):
        self.verdict = verdict
        self.opportunity = opportunity
        self.p_win = p_win
        self.regime = regime
        self.consensus_score = 0.2
        self.consensus_confidence = 0.6
        self.no_trade_reason = "konsensüs zayıfladı" if verdict == "REDUCE" else ""


def test_14_unevaluated_economics_is_unknown_not_zero_or_half():
    """Açık pozisyonda ekonomi değerlendirilmez; `0.00`/`0.50` ÜRETİLEMEZ."""
    d = _Dec(verdict="HOLD", opportunity=None, p_win=0.382)
    assert economics_available(d) is False
    row = management_snapshot(position=_Pos(), mark=101.0, decision=d, trade_id="F1")
    assert row["economics_evaluated"] is False
    for f in ("p_win", "expected_net_return", "remaining_edge"):
        assert row[f] == UNKNOWN, f
        assert row[f] not in (0, 0.0, 0.5), f
    assert row["economics_reason"] == "OPEN_POSITION_NOT_ECONOMICALLY_EVALUATED"


def test_14b_evaluated_economics_is_reported_as_numbers():
    d = _Dec(verdict="HOLD", opportunity={"conservative_net_edge_r": 0.33, "net_edge_r": 0.51},
             p_win=0.58)
    assert economics_available(d) is True
    row = management_snapshot(position=_Pos(), mark=101.0, decision=d, trade_id="F1")
    assert row["economics_evaluated"] is True
    assert row["p_win"] == 0.58
    assert row["expected_net_return"] == 0.33 and row["remaining_edge"] == 0.51


def test_14c_missing_decision_defaults_to_hold_not_exit():
    action, reason = proposed_action(None, _Pos())
    assert action == "HOLD" and reason == "NO_DECISION", "veri yokluğu ÇIK demek değildir"


def test_14d_r_metrics_never_fabricate_capture_ratio():
    m = r_metrics(_Pos(mfe=0.0), mark=99.0)
    assert m["capture_ratio"] is None
    assert m["capture_ratio_state"] == "NO_FAVORABLE_EXCURSION"
    m2 = r_metrics(_Pos(entry=100.0, stop=98.0, mfe=4.0), mark=101.0)
    assert m2["current_net_r"] == pytest.approx(0.5, abs=1e-6)
    assert m2["mfe_r"] == pytest.approx(2.0, abs=1e-6)
    assert m2["giveback_r"] == pytest.approx(1.5, abs=1e-6)


def test_15_reduce_and_exit_are_advisory_only_and_never_executed():
    rows = [management_snapshot(position=_Pos(id="F1"), mark=101.0,
                                decision=_Dec(verdict="REDUCE"), trade_id="F1"),
            management_snapshot(position=_Pos(id="F2"), mark=99.0,
                                decision=_Dec(verdict="EXIT"), trade_id="F2")]
    for r in rows:
        assert r["action_mode"] == ADVISORY_ONLY
        assert r["executable"] is False
    doc = build_snapshot_doc(rows, run_id="r1")
    assert doc["action_mode"] == ADVISORY_ONLY and doc["executable"] is False
    assert doc["by_action"] == {"REDUCE": 1, "EXIT": 1}
    ex = ManagementExecutor()
    intents = ex.plan(rows)
    assert len(intents) == 2
    assert all(i["applied"] is False and i["blocker"] == "EXIT_POLICY_NOT_ACTIVATED"
               for i in intents)
    out = ex.execute(intents)
    assert out["applied"] == 0 and out["blocked"] == 2


def test_15b_executor_is_shadow_only_and_fails_closed():
    with pytest.raises(ValueError):
        ManagementExecutor(mode=EXECUTABLE)
    with pytest.raises(ValueError):
        ManagementExecutor(mode="LIVE")
    assert ManagementExecutor().mode == "SHADOW"


def test_15c_executor_cannot_reach_the_execution_gateway():
    """Yapısal izolasyon: yönetim modülü emir yoluna BAĞLANAMAZ."""
    import ast
    src = Path("tradingbot/learn/position_mgmt.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    joined = " ".join(imported)
    for banned in ("execution", "gateway", "accounting", "risk", "outbox", "notify"):
        assert banned not in joined, f"yasak bağımlılık: {banned} ({imported})"
    sig = ManagementExecutor.__init__.__code__.co_varnames
    assert "gateway" not in sig and "ledger" not in sig


# ------------------------------------------------------------------ 16: dashboard dayanıklılığı


httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from tradingbot.dashboard.app import DashboardConfig, create_app  # noqa: E402


def _dirs(tmp: Path) -> tuple[Path, Path]:
    st, data = tmp / "state", tmp / "data"
    st.mkdir(), data.mkdir()
    (st / "futures_ledger.json").write_text(json.dumps(
        {"schema_version": 2, "wallet_balance": "100", "starting_equity": "100",
         "positions": {}, "history": [], "total_fees": "0"}), encoding="utf-8")
    (st / "portfolio.json").write_text(json.dumps(
        {"cash": 100.0, "starting_equity": 100.0, "positions": {}, "history": []}),
        encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")
    (st / "learning.json").write_text(json.dumps(
        {"n_trades": 2, "n_wins": 1, "sum_r": 0.4, "lessons": [], "weights": {},
         "agent_weights": {}}), encoding="utf-8")
    return st, data


@pytest.mark.parametrize("doc", [
    None,                                                      # dosya yok
    {},                                                        # boş
    {"schema_version": "unknown_v99"},                         # bilinmeyen şema
    {"canonical_final_closes": "cok", "rows": "liste-degil"},   # bozuk tipler
    {"canonical_final_closes": 3, "rows": [{"trade_id": "F1"}]},  # eksik alanlar
    {"canonical_final_closes": 2, "outcomes": float("nan"),
     "rows": [{"trade_id": "F1", "net_pnl": float("inf"), "r_multiple": float("nan"),
               "chain_state": "COMPLETE", "link_status": "LINKED"}]},   # sonlu olmayan
])
def test_16_dashboard_never_returns_500_on_missing_or_broken_chain(tmp_path, doc):
    st, data = _dirs(tmp_path)
    if doc is not None:
        st.joinpath("learning_chain.json").write_text(
            json.dumps(doc, allow_nan=True), encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    for path in ("/learning", "/api/learning-chain", "/", "/health/live"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
    body = c.get("/api/learning-chain").text
    assert "NaN" not in body and "Infinity" not in body, "RFC dışı JSON yayımlandı"


def test_16b_dashboard_shows_advisory_only_and_unknown(tmp_path):
    st, data = _dirs(tmp_path)
    st.joinpath("learning_chain.json").write_text(json.dumps(
        {"canonical_final_closes": 2, "outcomes": 2, "lessons": 2, "entry_linked": 1,
         "legacy_unlinked": 1, "missing_outcome": 0, "missing_lesson": 0,
         "duplicate_lessons": 0, "quant_sample_count": 1, "quant_sample_gap": 1,
         "rows": [{"trade_id": "F1", "symbol": "ETH/USDT", "side": "LONG",
                   "closed_at": "2026-08-21T10:00:00+00:00", "exit_reason": "stop",
                   "net_pnl": -1.0, "r_multiple": -1.0, "mfe_r": 0.5, "mae_r": -1.0,
                   "fee_drag_r": 0.02, "funding_drag_r": 0.01,
                   "chain_state": "COMPLETE", "link_status": "LEGACY_UNLINKED"}]}),
        encoding="utf-8")
    st.joinpath("position_management.json").write_text(json.dumps(
        {"action_mode": "ADVISORY_ONLY", "executable": False, "n_positions": 1,
         "n_economics_unknown": 1, "by_action": {"REDUCE": 1},
         "positions": [{"trade_id": "F1", "symbol": "ETH/USDT", "side": "LONG",
                        "mark": 101.0, "current_net_r": 0.5, "mfe_r": 2.0, "mae_r": -0.5,
                        "giveback_r": 1.5, "capture_ratio": 0.25,
                        "capture_ratio_state": "OK", "position_age_hours": 24.0,
                        "stop": 98.0, "targets_hit": 0, "proposed_action": "REDUCE",
                        "p_win": "UNKNOWN", "expected_net_return": "UNKNOWN"}]}),
        encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    r = c.get("/learning")
    assert r.status_code == 200
    html = r.text
    assert "ADVISORY_ONLY" in html or "YALNIZ TAVSİYE" in html
    assert "UNKNOWN" in html, "ölçülmemiş ekonomi UNKNOWN olarak görünmeli"
    assert "LEGACY" in html
    # Sahte sayı yasağı: yönetim tablosunda ekonomi hücresi 0.00/0.50 GÖSTERMEMELİ.
    mgmt = html.split("Açık pozisyon yönetimi", 1)[1]
    assert ">0.0000<" not in mgmt and ">0.5000<" not in mgmt
    js = c.get("/api/learning-chain").json()
    assert js["available"] is True
    assert js["position_management"]["executable"] is False
    assert js["position_management"]["positions"][0]["p_win"] == "UNKNOWN"


# ------------------------------------------------------------------ 17: quant örneklemi


def test_17_quant_sample_count_is_compared_against_canonical_history(tmp_path):
    """Kanonik kapanış 18 iken n=9 rapor «güncel» görünemez."""
    st, data = _dirs(tmp_path)
    st.joinpath("learning_chain.json").write_text(json.dumps(
        {"canonical_final_closes": 18, "outcomes": 18, "lessons": 18, "entry_linked": 2,
         "legacy_unlinked": 16, "missing_outcome": 0, "missing_lesson": 0,
         "duplicate_lessons": 0, "quant_sample_count": 9, "quant_sample_gap": 9,
         "quant_covers_all_closes": False, "quant_run_id": "eski-rapor", "rows": []}),
        encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    html = c.get("/learning").text
    assert "ESKİ" in html and "n=9/18" in html
    assert "GÜNCEL" not in html.split("Quant örneklemi")[-1][:200]
    js = c.get("/api/learning-chain").json()
    assert js["quant_covers_all_closes"] is False and js["quant_sample_gap"] == 9


def test_17b_matching_quant_sample_is_reported_as_current(tmp_path):
    st, data = _dirs(tmp_path)
    st.joinpath("learning_chain.json").write_text(json.dumps(
        {"canonical_final_closes": 18, "outcomes": 18, "lessons": 18, "entry_linked": 18,
         "legacy_unlinked": 0, "missing_outcome": 0, "missing_lesson": 0,
         "duplicate_lessons": 0, "quant_sample_count": 18, "quant_sample_gap": 0,
         "quant_covers_all_closes": True, "rows": []}), encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    assert "GÜNCEL (n=18)" in c.get("/learning").text


# ------------------------------------------------------------------ 18: defter dokunulmazlığı


def test_18_main_ledger_is_byte_identical_across_reconcile(tmp_path):
    """Uzlaştırma ana defteri BYTE düzeyinde değiştirmemeli."""
    from tradingbot.accounting import FuturesLedgerV2

    st = tmp_path / "state"
    st.mkdir()
    led = FuturesLedgerV2(Decimal("100"))
    path = st / "futures_ledger.json"
    led.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["history"] = [_close("F1"), _close("F2", symbol="SOL/USDT", pnl=1.2, r=1.4,
                                           exit_reason="hedef2")]
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    led2 = FuturesLedgerV2.load(path)
    mem = TradeMemory(st / "trade_memory.jsonl", source="LIVE_PAPER")
    learner = Learner(st / "learning.json", min_trades=2)
    idx = LearnedIndex(st / "learned_closes.jsonl")
    prov = ProvenanceStore(st / "entry_provenance.jsonl")
    res = complete_missing_chain(history=led2.history, memory=mem, learner=learner,
                                 index=idx, provenance_store=prov)
    assert res["lessons_added"] == 2 and res["outcomes_added"] == 2
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after, "defter DEĞİŞTİ — uzlaştırma salt okunur olmalı"
    # ikinci geçiş de defteri değiştirmemeli ve sıfır değişiklik üretmeli
    res2 = complete_missing_chain(history=led2.history, memory=mem, learner=learner,
                                  index=idx, provenance_store=prov)
    assert (res2["lessons_added"], res2["outcomes_added"]) == (0, 0)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_18b_legacy_view_matches_the_live_learning_input_shape(tmp_path):
    """Onarım yolu `learn()`e canlı turla AYNI biçimi vermeli (Decimal dize DEĞİL)."""
    from tradingbot.accounting import FuturesLedgerV2

    st = tmp_path / "state"
    st.mkdir()
    led = FuturesLedgerV2(Decimal("100"))
    path = st / "futures_ledger.json"
    led.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["history"] = [_close("F1")]
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    rec = FuturesLedgerV2.load(path).history[0]
    live = rec.to_legacy_dict()
    view = legacy_view(rec)
    assert view == live
    assert isinstance(view["pnl"], float) and isinstance(view["r_multiple"], float)
    assert view["pnl"] > 0 or view["pnl"] <= 0        # karşılaştırma TypeError vermemeli


# ------------------------------------------------------------------ zincir sözleşmesi


def test_chain_report_flags_orphan_lessons_without_a_ledger_close():
    closes = canonical_closes([_close("F1")])
    rep = chain_report(closes, outcome_ids=["F1", "GHOST"], lesson_ids=["F1", "GHOST"],
                       provenance={})
    assert rep["orphan_lessons"] == ["GHOST"] and rep["orphan_outcomes"] == ["GHOST"]
    assert rep["canonical_final_closes"] == 1


def test_chain_report_counts_duplicates_explicitly():
    closes = canonical_closes([_close("F1")])
    rep = chain_report(closes, outcome_ids=["F1"], lesson_ids=["F1"], provenance={},
                       lesson_id_counts={"F1": 3})
    assert rep["duplicate_lessons"] == {"F1": 3} and rep["duplicate_lesson_count"] == 2


def test_close_event_id_is_deterministic_and_order_independent():
    a = close_event_id("F1", "2026-08-21T10:00:00+00:00", "stop")
    b = close_event_id("F1", "2026-08-21T10:00:00+00:00", "stop")
    c = close_event_id("F1", "2026-08-21T10:00:01+00:00", "stop")
    assert a == b and a != c and len(a) == 16


# ------------------------------------------------------------------ uçtan uca motor kanıtı
#
# "Dosya var" öğrenildi demek DEĞİLDİR. Aşağıdaki testler gerçek bir motor turu çalıştırır ve
# zinciri KİMLİKLERLE doğrular: açılışta yazılan `entry_decision_id`, kapanışta aynı kimlikle
# bağlanan outcome, tam bir ders ve indekste tek bir kapanış olayı.

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as E  # noqa: E402


def test_e2e_open_writes_entry_provenance_with_a_real_decision_id(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    summ = eng.tour(do_scan=False, obsidian=False, charts=False)
    st = eng.cfg.state_path
    assert summ["opened"] and eng.ledger2.positions, "provenance kanıtı için giriş gerekli"
    prov = ProvenanceStore(st / "entry_provenance.jsonl").load()
    assert prov, "açılan pozisyon için provenance YAZILMALI"
    for tid, pos in eng.ledger2.positions.items():
        rec = prov.get(str(pos.id))
        assert rec is not None, f"{pos.id} için provenance yok"
        assert rec["link_status"] == LINKED
        assert rec["entry_decision_id"], "gerçek karar kimliği yazılmalı"
        assert len(rec["entry_decision_id"]) == 16, rec["entry_decision_id"]
        assert rec["entry_run_id"] == eng.run_id
        assert rec["entry_code_sha"] is None or isinstance(rec["entry_code_sha"], str)
        assert rec["entry_config_hash"], "config_hash ölçülebilir olmalı"
        assert rec["entry_stop"] is not None and rec["entry_leverage"] is not None
        json.dumps(rec, allow_nan=False)


def test_e2e_tour_writes_chain_and_management_documents(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, symbols=4)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    st = eng.cfg.state_path
    ch = json.loads((st / "learning_chain.json").read_text(encoding="utf-8"))
    assert ch["canonical_final_closes"] == len(eng.ledger2.history)
    assert ch["missing_outcome"] == 0 and ch["missing_lesson"] == 0
    assert ch["duplicate_lessons"] == 0
    assert ch["influence_mode"] in ("OFF", "SHADOW", "PAPER_BOUNDED")
    json.dumps(ch, allow_nan=False)
    pm = json.loads((st / "position_management.json").read_text(encoding="utf-8"))
    assert pm["action_mode"] == ADVISORY_ONLY and pm["executable"] is False
    assert pm["n_positions"] == len(eng.ledger2.positions)
    for row in pm["positions"]:
        # Açık pozisyonda ekonomi DEĞERLENDİRİLMEZ (head erken döner) -> UNKNOWN olmalı.
        if not row["economics_evaluated"]:
            assert row["p_win"] == UNKNOWN and row["expected_net_return"] == UNKNOWN
        assert row["action_mode"] == ADVISORY_ONLY
    json.dumps(pm, allow_nan=False)


def test_e2e_close_is_learned_exactly_once_across_repeated_tours(tmp_path, monkeypatch):
    """Aynı kapanış tekrar tekrar tur atılsa da BİR KEZ öğrenilir (kimlikle kanıtlanır).

    Kapanış DETERMİNİSTİK olarak zorlanır: pozisyon açıldıktan sonra canlı fiyat stop'un
    ötesine taşınır. "Bu turda kapanış olmadı" diye atlanan bir test kanıt DEĞİLDİR.
    """
    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    st = eng.cfg.state_path
    s1 = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s1["opened"], "ilk turda giriş bekleniyor (zincir kanıtı buna dayanıyor)"
    for sym, pos in list(eng.ledger2.positions.items()):        # stop'u tetikle
        eng._fake_live.price[sym] = float(pos.stop) * (0.98 if pos.side.value == "LONG" else 1.02)
    eng.tour(do_scan=False, obsidian=False, charts=False)       # kapanış turu
    eng._fake_live.price.clear()
    for _ in range(2):                                          # tekrar turlar: yeni ders YOK
        eng.tour(do_scan=False, obsidian=False, charts=False)
    hist = eng.ledger2.history
    assert hist, "stop tetiklendiği halde kapanış oluşmadı"
    idx = LearnedIndex(st / "learned_closes.jsonl").load()
    closes = canonical_closes(hist)
    for c in closes:
        assert c["close_event_id"] in idx, f"{c['trade_id']} indekste yok"
        assert idx[c["close_event_id"]]["trade_id"] == c["trade_id"]
    assert len(idx) == len(closes), "indekste fazladan kapanış olayı var"
    ids = [x["id"] for x in eng.learner.state.lessons]
    for c in closes:
        assert ids.count(c["trade_id"]) == 1, f"{c['trade_id']} için ders sayısı != 1"
    exits = [r for r in eng.memory.iter_rows() if r.get("kind") == "exit"]
    for c in closes:
        assert sum(1 for r in exits if r["trade_id"] == c["trade_id"]) == 1


def test_e2e_risk_decision_is_identical_with_chain_layer_disabled(tmp_path, monkeypatch):
    """Zincir katmanı AKTİF KARARI değiştirmemeli: risk kararları birebir aynı olmalı."""
    eng_a = E._engine(tmp_path / "a", monkeypatch, symbols=4)
    sa = eng_a.tour(do_scan=False, obsidian=False, charts=False)
    risk_a = json.loads((eng_a.cfg.state_path / "risk.json").read_text(encoding="utf-8"))

    eng_b = E._engine(tmp_path / "b", monkeypatch, symbols=4)
    eng_b.provenance = None            # zincir katmanı KAPALI
    eng_b.learned_index = None
    eng_b.mgmt_executor = None
    sb = eng_b.tour(do_scan=False, obsidian=False, charts=False)
    risk_b = json.loads((eng_b.cfg.state_path / "risk.json").read_text(encoding="utf-8"))

    def _norm(rows):
        return [{k: v for k, v in r.items() if k not in ("at", "trade_id")}
                for r in rows]
    assert _norm(risk_a["last_decisions"]) == _norm(risk_b["last_decisions"])
    assert sa["opened"] == sb["opened"]
    assert sa["ledger"]["equity"] == sb["ledger"]["equity"]
    assert risk_a["profile"] == risk_b["profile"]


def test_e2e_chain_layer_failure_does_not_stop_the_tour(tmp_path, monkeypatch):
    """Zincir katmanı patlarsa tur SÜRMELİ ve defter/risk etkilenmemeli (fail-safe)."""
    eng = E._engine(tmp_path, monkeypatch, symbols=3)

    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("zincir katmanı arızası")
    eng.provenance = _Boom()
    eng.learned_index = _Boom()
    summ = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert summ["run_id"] and summ["risk"]["killswitch"] == "ARMED"
    assert (eng.cfg.state_path / "health.json").exists()
    h = json.loads((eng.cfg.state_path / "health.json").read_text(encoding="utf-8"))
    assert h["state"] in ("HEALTHY", "KILL_SWITCH")


def test_08e_complete_but_unindexed_close_is_backfilled(env):
    """Zinciri TAM ama indekste olmayan kapanış GERİ DOLDURULUR.

    Üretimde ölçüldü (2026-09-02): F00014 `exit_check` üzerinden kapandı, o yol o sürümde
    `note_learned` çağırmıyordu ve 19 kapanışın 18'i indeksliydi. Ders sıcak pencereden
    (200) arşive döndüğü an o kapanış "eksik" görünüp İKİNCİ kez öğrenilirdi.
    """
    from tradingbot.learn.reconcile import BACKFILL
    hist = [_close("F1"), _close("F2", symbol="SOL/USDT")]
    _run_chain(env, hist)                                   # ikisi de tam + indeksli
    assert len(env["index"].load()) == 2
    # F2'nin indeks kaydını sil (exit_check yolunun bıraktığı durumu taklit et)
    rows = [r for r in env["index"].load().values() if r["trade_id"] != "F2"]
    env["index"].path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert set(env["index"].trade_ids()) == {"F1"}
    plan = build_plan(history=hist, memory=env["memory"], learner=env["learner"],
                      index=env["index"], provenance_store=env["prov"])
    assert plan["pending"] == [], "zincir TAM, eksik adım yok"
    assert plan["will_index"] == 1, "indekssiz kapanış görülmeli"
    res = _run_chain(env, hist)
    assert res["ran"] is True and res["indexed"] == 1
    assert res["lessons_added"] == 0 and res["outcomes_added"] == 0, "yeniden ÖĞRENME olmamalı"
    idx = env["index"].load()
    assert set(env["index"].trade_ids()) == {"F1", "F2"}
    assert [v for v in idx.values() if v["trade_id"] == "F2"][0]["source"] == BACKFILL
    assert _lesson_ids(env).count("F2") == 1
    # üçüncü geçiş sıfır değişiklik
    assert _run_chain(env, hist)["ran"] is False
