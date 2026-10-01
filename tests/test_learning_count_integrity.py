"""RELEASE agaci regresyonlari — sayac butunlugu, provenans ve sunum dogrulugu.

Bu dosya SURUM adayi agacindadir ve arastirma modullerine BAGIMLI DEGILDIR.

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

from tradingbot.dashboard.views import (CALIBRATION_UNKNOWN, COIN_HEAD_COLUMN_NOTES_BASE,
                                        COIN_HEAD_COLUMNS, NOT_APPLICABLE, _cell_pct_signal,
                                        calibration_note, coin_head_column_notes,
                                        coin_head_table)
from tradingbot.learn.labels import label_outcome
from tradingbot.learn.learner_v2 import LearnerV2
from tradingbot.learn.memory import TradeMemory
from tradingbot.learn.model import HierarchicalRate
from tradingbot.learn.reconcile import LearnedIndex, close_event_id, note_learned
from tradingbot.learn.registry import ModelRegistry
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
# ------------------------------------------------------- 4) hedef sözleşmesi
def test_scratch_is_in_the_denominator_and_is_not_a_win():
    lab = label_outcome({"r_multiple": 0.1, "net_pnl": 0.1, "exit_reason": "stop",
                         "mae_pct": -0.1, "mfe_pct": 0.2})
    assert lab["outcome_class"] == "SCRATCH"
    assert lab["won"] is False
    hr = HierarchicalRate(10.0, 0.5)
    hr.add(1.0 if lab["won"] else 0.0, leaves=("A",))
    assert hr.stats[""].n == 1 and hr.stats[""].s == 0.0     # paydada, kazanç değil


# ------------------------------------------------------- 5) eşleşmiş kıyaslama
# ------------------------------------------------------- 6) panel sunumu
def test_measured_zero_is_preserved_and_small_nonzero_is_not_hidden():
    assert _cell_pct_signal(0.0, 0) == "%0"          # ÖLÇÜLMÜŞ sıfır
    assert _cell_pct_signal(None, 0) == "—"          # eksik
    assert _cell_pct_signal(0.0019, 0) == "%0.2"     # eskiden "%0" görünüyordu
    assert _cell_pct_signal(0.329, 0) == "%33"       # normal değer değişmedi
    assert _cell_pct_signal(0.5, 0) == "%50"


def test_coin_head_columns_distinguish_confidence_from_probability():
    assert "Konsensüs gücü" in COIN_HEAD_COLUMNS
    assert "P(kazanç) — istatistiksel tahmin" in COIN_HEAD_COLUMNS
    assert "Güven" not in COIN_HEAD_COLUMNS
    base = COIN_HEAD_COLUMN_NOTES_BASE["P(kazanç) — istatistiksel tahmin"]
    assert "0.25R" in base and "ÖMRÜ" in base
    assert "olasılık DEĞİLDİR" in COIN_HEAD_COLUMN_NOTES_BASE["Konsensüs gücü"]


def test_calibration_status_comes_from_evidence_not_a_fixed_claim():
    """Kalibrasyon iddiası KANITA bağlı olmalı; kalıcı sabit bir cümle YANLIŞ olabilir."""
    assert calibration_note(None) == CALIBRATION_UNKNOWN
    unfit = calibration_note({"calibrator_n_fit": 0, "champion_model": None})
    assert "FIT EDİLMEMİŞ" in unfit and "YOK" in unfit
    fitted = calibration_note({"calibrator_n_fit": 40, "champion_model": "m1"})
    assert "fit edilmiş" in fitted and "m1" in fitted
    assert "FIT EDİLMEMİŞ" not in fitted          # durum değişince metin de değişir
    notes = coin_head_column_notes({"calibrator_n_fit": 40, "champion_model": "m1"})
    assert "fit edilmiş" in notes["P(kazanç) — istatistiksel tahmin"]


def test_expectancy_is_not_applicable_when_no_entry_plan_was_produced():
    """Giriş planı üretilmeyen satırdaki 0.0 dataclass VARSAYILANIdır, ölçülmüş sıfır DEĞİL."""
    reduce_row = {"symbol": "SOL/USDT", "verdict": "REDUCE", "direction": "LONG",
                  "confidence_calibrated": 0.0019, "p_win": 0.329,
                  "expected_return_net": 0.0, "expected_r": 0.0, "regime": "RANGE"}
    row = coin_head_table([reduce_row], [], [])["rows"][0]
    assert row[4] == "%0.2"                 # konsensüs gücü: küçük ama ölçülmüş
    assert row[5] == "%33"                  # olasılık tahmini
    assert row[6] == NOT_APPLICABLE         # beklenti HİÇ hesaplanmadı
    assert row[7] == NOT_APPLICABLE


def test_measured_zero_is_preserved_when_a_plan_exists():
    """Plan varken hesaplanmış 0.0 ÖLÇÜLMÜŞ sıfırdır ve aynen gösterilir."""
    planned = {"symbol": "BTC/USDT", "verdict": "FUTURES_LONG", "direction": "LONG",
               "confidence_calibrated": 0.6, "p_win": 0.3,
               "expected_return_net": 0.0, "expected_r": 0.0,
               "futures_plan": {"valid": True}}
    row = coin_head_table([planned], [], [])["rows"][0]
    assert row[6] == "%0.00"
    assert row[7] == "0.00"


def test_missing_head_fields_render_as_unavailable():
    heads = [{"symbol": "X/USDT", "verdict": "NO_TRADE", "confidence_calibrated": None,
              "p_win": None, "expected_return_net": None, "expected_r": None,
              "futures_plan": {"valid": True}}]
    row = coin_head_table(heads, [], [])["rows"][0]
    assert row[4] == "—" and row[5] == "—" and row[6] == "—" and row[7] == "—"


def test_coin_head_table_carries_notes_and_preserves_values():
    out = coin_head_table([{"symbol": "SOL/USDT", "verdict": "REDUCE",
                            "confidence_calibrated": 0.0019, "p_win": 0.329}], [], [],
                          learning_status={"calibrator_n_fit": 0, "champion_model": None})
    assert set(out["column_notes"]) == set(COIN_HEAD_COLUMN_NOTES_BASE)
    assert "FIT EDİLMEMİŞ" in out["column_notes"]["P(kazanç) — istatistiksel tahmin"]


# ------------------------------------------------------- 7) aşama yazma sınırı