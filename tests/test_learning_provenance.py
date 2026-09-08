"""RELEASE agaci — ogrenme provenansi ve idempotency regresyonlari (arastirma bagimliligi YOK)."""
from __future__ import annotations

import json


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
