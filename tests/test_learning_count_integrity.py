"""Öğrenme sayaç bütünlüğü + düzeltilmiş olasılık hedefi + sunum doğruluğu regresyonları.

Somut riskler:
* bir nihai kapanış AYNI ata düğümü İKİ KEZ güncelliyordu (global + regime),
* meşru FARKLI yaprak düğümü güncellemeleri yanlışlıkla bastırılabilirdi,
* tekrarlı teslim kanıtı çoğaltabilir (dış idempotency otoritesi bunu engellemeli),
* kısmi çıkış nihai kapanış gibi sayılabilir,
* olasılık hedefi (SCRATCH paydada / koşulsuz) yanlış eşleştirilebilir,
* eşleşmiş kıyaslama farklı satır kümelerinde yapılabilir,
* panelde ÖLÇÜLMÜŞ sıfır ile eksik/gizlenmiş küçük değer karışabilir.
"""
from __future__ import annotations

import json
from pathlib import Path

from tradingbot.dashboard.views import (COIN_HEAD_COLUMN_NOTES, COIN_HEAD_COLUMNS,
                                        _cell_pct_signal, coin_head_table)
from tradingbot.learn.labels import label_outcome
from tradingbot.learn.learner_v2 import LearnerV2
from tradingbot.learn.memory import TradeMemory
from tradingbot.learn.model import HierarchicalRate
from tradingbot.learn.reconcile import LearnedIndex, close_event_id, note_learned
from tradingbot.learn.registry import ModelRegistry
from tradingbot.research.learning_replay import NEW, OLD, count_integrity, rebuild
from tradingbot.research.probability_benchmark import (NOT_EVALUABLE, id_set_hash,
                                                       label_target_contract, score_predictor)


def _close(tid="T1", symbol="AAA/USDT", r=-1.0, regime="TREND_UP", setup="pullback",
           side="LONG", closed_at="2026-09-03T00:00:00+00:00"):
    return {"id": tid, "trade_id": tid, "symbol": symbol, "side": side, "setup_type": setup,
            "r_multiple": r, "net_pnl": r, "exit_reason": "stop", "closed_at": closed_at,
            "mae_pct": -1.0, "mfe_pct": 0.5,
            "features": {"regime": regime, "setup_type": setup, "direction": side}}


def _learner(tmp: Path) -> LearnerV2:
    return LearnerV2(TradeMemory(tmp / "trade_memory.jsonl"),
                     ModelRegistry(tmp / "models.json"), state_path=tmp / "learn_v2.json")


# ------------------------------------------------------- 1) tek gözlem, düğüm başına tek sayım
def test_one_final_close_updates_each_node_exactly_once(tmp_path):
    lrn = _learner(tmp_path)
    lrn.on_trade_closed(_close(), {"regime": "TREND_UP"})
    assert lrn.n_closed == 1
    # ATA düğümler: tam olarak 1
    assert lrn.win.stats[""].n == 1
    assert lrn.win.stats["regime:TREND_UP"].n == 1
    assert lrn.exp_r.stats[""].n == 1
    # MEŞRU farklı yapraklar KORUNUR (bastırılmaz)
    assert lrn.win.stats["leaf:AAA/USDT|pullback"].n == 1
    assert lrn.win.stats["leaf:AAA/USDT"].n == 1
    assert lrn.win.stats["regime:TREND_UP|leaf:AAA/USDT|pullback"].n == 1
    assert lrn.win.stats["regime:TREND_UP|leaf:AAA/USDT"].n == 1


def test_two_distinct_closes_stay_distinct(tmp_path):
    lrn = _learner(tmp_path)
    lrn.on_trade_closed(_close("T1", r=-1.0), {"regime": "TREND_UP"})
    lrn.on_trade_closed(_close("T2", r=+2.0, closed_at="2026-09-04T00:00:00+00:00"),
                        {"regime": "TREND_UP"})
    assert lrn.n_closed == 2
    assert lrn.win.stats[""].n == 2
    assert lrn.win.stats[""].s == 1.0            # bir kazanç
    assert lrn.exp_r.stats[""].n == 2


def test_multi_leaf_add_counts_ancestors_once_but_each_leaf_once():
    hr = HierarchicalRate(10.0, 0.5)
    hr.add(1.0, regime="R", leaves=("A|s", "A"))
    assert hr.stats[""].n == 1
    assert hr.stats["regime:R"].n == 1
    assert hr.stats["leaf:A|s"].n == 1
    assert hr.stats["leaf:A"].n == 1
    assert hr.stats["regime:R|leaf:A|s"].n == 1
    assert hr.stats["regime:R|leaf:A"].n == 1


def test_duplicate_leaf_in_one_observation_is_not_double_counted():
    hr = HierarchicalRate(10.0, 0.5)
    hr.add(1.0, regime="R", leaves=("A", "A"))
    assert hr.stats["leaf:A"].n == 1
    assert hr.stats[""].n == 1


def test_single_leaf_api_is_backward_compatible():
    old_style, new_style = HierarchicalRate(10.0, 0.5), HierarchicalRate(10.0, 0.5)
    old_style.add(1.0, regime="R", leaf="A")
    new_style.add(1.0, regime="R", leaves=("A",))
    assert {k: v.to_dict() for k, v in old_style.stats.items()} == \
           {k: v.to_dict() for k, v in new_style.stats.items()}


def test_weighted_mass_is_preserved_with_recency_weights():
    """Ağırlıklı kütle semantiği korunur: n = Σw, tek gözlem için tek w."""
    hr = HierarchicalRate(10.0, 0.5, half_life_days=10.0)
    hr.add(1.0, regime="R", leaves=("A|s", "A"), age_days=10.0)
    assert abs(hr.stats[""].n - 0.5) < 1e-9      # 0.5^(10/10)
    assert abs(hr.stats["leaf:A"].n - 0.5) < 1e-9


# ------------------------------------------------------- 2) kalıcılık / tekrarlı teslim
def test_save_load_roundtrip_preserves_counts(tmp_path):
    lrn = _learner(tmp_path)
    lrn.on_trade_closed(_close(), {"regime": "TREND_UP"})
    before = {k: v.to_dict() for k, v in lrn.win.stats.items()}
    again = _learner(tmp_path)                    # diskten yükler
    after = {k: v.to_dict() for k, v in again.win.stats.items()}
    assert before == after
    assert again.n_closed == 1


def test_repeated_delivery_is_blocked_by_the_existing_learned_index(tmp_path):
    """İdempotency otoritesi MEVCUT `LearnedIndex`tir — ikinci bir öğrenme defteri EKLENMEZ."""
    idx = LearnedIndex(tmp_path / "learned_closes.jsonl")
    rec = _close()
    ev = close_event_id(rec["id"], rec["closed_at"], rec["exit_reason"])
    assert note_learned(idx, rec, {"id": "L1"}, source="TEST") is True
    assert note_learned(idx, rec, {"id": "L1"}, source="TEST") is False   # aynı olay
    assert ev in idx.event_ids()
    other = _close("T2", closed_at="2026-09-04T00:00:00+00:00")
    assert note_learned(idx, other, {"id": "L2"}, source="TEST") is True


def test_partial_exit_is_not_a_final_close(tmp_path):
    """Kısmi çıkış (`tp1_done`, kapanış zamanı yok) nihai kapanış gibi sayılamaz."""
    idx = LearnedIndex(tmp_path / "learned_closes.jsonl")
    partial = {"id": "T1", "trade_id": "T1", "tp1_done": True, "closed_at": None,
               "exit_reason": None, "r_multiple": None}
    assert close_event_id("T1", None, None) != close_event_id("T1", "2026-09-03T00:00:00+00:00",
                                                              "stop")
    note_learned(idx, partial, None, source="TEST")
    final = _close("T1")
    assert note_learned(idx, final, {"id": "L"}, source="TEST") is True   # AYRI olay


# ------------------------------------------------------- 3) yalıtılmış replay / etki
def test_isolated_replay_reproduces_the_duplication_and_the_fix():
    closes = [_close(f"T{i}", r=(2.0 if i % 4 == 0 else -1.0),
                     closed_at=f"2026-09-0{1 + i % 5}T00:00:00+00:00") for i in range(8)]
    old = rebuild(closes, semantics=OLD)
    new = rebuild(closes, semantics=NEW)
    assert old["win_stats"][""]["n"] == 16          # 8 kapanış × 2
    assert new["win_stats"][""]["n"] == 8
    assert old["exp_r_stats"][""]["n"] == new["exp_r_stats"][""]["n"] == 8
    ci = count_integrity(closes)
    assert ci["verdict"] == "DUPLICATE_ANCESTOR_UPDATES"
    assert ci["unique_observations"] == 8
    assert ci["update_calls_win_old"] == 16 and ci["update_calls_win_new"] == 8
    kinds = {n["kind"] for n in ci["nodes"] if n["duplicated"]}
    assert kinds <= {"GLOBAL", "REGIME"}, "yaprak düğümler yinelenmiş SAYILMAMALI"
    assert all(n["unchanged"] for n in ci["nodes"] if n["kind"].endswith("LEAF"))


def test_replay_does_not_touch_production_state(tmp_path):
    """Yalıtılmış replay hiçbir dosya yazmaz."""
    closes = [_close()]
    before = sorted(p.name for p in tmp_path.iterdir())
    rebuild(closes, semantics=NEW)
    count_integrity(closes)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# ------------------------------------------------------- 4) hedef sözleşmesi
def test_scratch_is_in_the_denominator_and_is_not_a_win():
    lab = label_outcome({"r_multiple": 0.1, "net_pnl": 0.1, "exit_reason": "stop",
                         "mae_pct": -0.1, "mfe_pct": 0.2})
    assert lab["outcome_class"] == "SCRATCH"
    assert lab["won"] is False
    hr = HierarchicalRate(10.0, 0.5)
    hr.add(1.0 if lab["won"] else 0.0, leaves=("A",))
    assert hr.stats[""].n == 1 and hr.stats[""].s == 0.0     # paydada, kazanç değil


def test_label_target_contract_states_unconditional_and_lifetime():
    c = label_target_contract()
    assert "PAYDADA" in c["scratch_treatment"]
    assert c["conditionality"].startswith("KOŞULSUZ")
    assert "ÖMRÜ" in c["horizon"]
    assert "0.25" in c["classifier_target"]
    assert "ABARTIR" in c["payoff_mismatch"]


# ------------------------------------------------------- 5) eşleşmiş kıyaslama
def test_id_set_hash_is_order_independent_and_detects_difference():
    assert id_set_hash(["b", "a"]) == id_set_hash(["a", "b"])
    assert id_set_hash(["a"]) != id_set_hash(["a", "b"])


def test_auc_is_not_evaluable_on_constant_or_single_class_scores():
    const = score_predictor("c", [(0.3, 1.0)] * 6 + [(0.3, 0.0)] * 6)
    assert const["auc"] is None and "constant_score" in const["auc_state"]
    single = score_predictor("s", [(0.1 * i, 1.0) for i in range(1, 13)])
    assert single["auc"] is None and "single_class" in single["auc_state"]
    good = score_predictor("g", [(0.9, 1.0)] * 6 + [(0.1, 0.0)] * 6)
    assert good["auc"] == 1.0 and good["auc_state"] == "RUN"


def test_scoring_returns_not_evaluable_on_tiny_samples():
    assert score_predictor("x", [(0.5, 1.0)] * 3)["state"] == NOT_EVALUABLE


# ------------------------------------------------------- 6) panel sunumu
def test_measured_zero_is_preserved_and_small_nonzero_is_not_hidden():
    assert _cell_pct_signal(0.0, 0) == "%0"          # ÖLÇÜLMÜŞ sıfır
    assert _cell_pct_signal(None, 0) == "—"          # eksik
    assert _cell_pct_signal(0.0019, 0) == "%0.2"     # eskiden "%0" görünüyordu
    assert _cell_pct_signal(0.329, 0) == "%33"       # normal değer değişmedi
    assert _cell_pct_signal(0.5, 0) == "%50"


def test_coin_head_columns_distinguish_confidence_from_probability():
    assert "Konsensüs gücü" in COIN_HEAD_COLUMNS
    assert "P(kazanç) — model" in COIN_HEAD_COLUMNS
    assert "Güven" not in COIN_HEAD_COLUMNS
    note = COIN_HEAD_COLUMN_NOTES["P(kazanç) — model"]
    assert "0.25R" in note and "FIT EDİLMEMİŞ" in note
    assert "olasılık DEĞİLDİR" in COIN_HEAD_COLUMN_NOTES["Konsensüs gücü"]


def test_coin_head_table_carries_notes_and_preserves_values():
    heads = [{"symbol": "SOL/USDT", "verdict": "REDUCE", "direction": "LONG",
              "confidence_calibrated": 0.0019, "p_win": 0.329,
              "expected_return_net": 0.0, "expected_r": 0.0, "regime": "RANGE"}]
    out = coin_head_table(heads, [], [])
    assert out["column_notes"] == COIN_HEAD_COLUMN_NOTES
    row = out["rows"][0]
    assert row[4] == "%0.2"      # konsensüs gücü — gizlenmedi
    assert row[5] == "%33"       # p_win
    assert row[6] == "%0.00"     # ÖLÇÜLMÜŞ sıfır beklenen net getiri korunur
    assert row[7] == "0.00"      # ÖLÇÜLMÜŞ sıfır E[R]


def test_missing_head_fields_render_as_unavailable():
    heads = [{"symbol": "X/USDT", "verdict": "NO_TRADE", "confidence_calibrated": None,
              "p_win": None, "expected_return_net": None, "expected_r": None}]
    row = coin_head_table(heads, [], [])["rows"][0]
    assert row[4] == "—" and row[5] == "—" and row[6] == "—" and row[7] == "—"


# ------------------------------------------------------- 7) aşama yazma sınırı
def test_integrity_stage_writes_only_under_out_dir(tmp_path):
    from tradingbot.research.integrity_run import build_integrity_report
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
    rep = build_integrity_report(root, tmp_path / "out", offline=True)
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()} == before
    wb = rep["write_boundary"]
    assert wb["production_learned_state_written"] is False
    assert wb["model_fitted"] is False and wb["state_migrated"] is False
    assert rep["state_repair_plan"]["execution_authorized"] is False
    assert rep["state_repair_plan"]["status"] == "PREPARED_NOT_EXECUTED"
