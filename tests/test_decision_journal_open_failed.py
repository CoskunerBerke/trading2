"""P0 (2026-09-28, öğrenme modu): karar günlüğü ACCEPTED'ı yalnız BAŞARILI dolumdan sonra yazar.

Motor `entry["executed_notional"]`ı defter açılışından ÖNCE yazıyor; eski `classify_outcome` bu alanı gördüğü an
ACCEPTED dönüyordu → her EXCHANGE_REJECTED satırı "kabul" sayılıyordu. Ayrıca defterin ret nedeni (`exec_reject`)
günlüğe hiç yazılmıyordu. Bu dosya hem saf sınıflandırmayı hem de gerçek motor turunu sınar.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tradingbot.learn.decision_journal import (  # noqa: E402
    ACCEPTED,
    OPEN_FAILED,
    OUTCOME_CLASSES,
    DecisionJournal,
    build_decision_record,
    classify_outcome,
    why_summary_tr,
)


def _cls(e):
    return classify_outcome(e, is_actionable=True, has_valid_plan=True)


def test_exchange_rejected_after_executed_notional_is_open_failed():
    e = {"executed_notional": 12.5, "block_code": "EXCHANGE_REJECTED", "exec_reject": "INSUFFICIENT_MARGIN"}
    assert _cls(e) == (OPEN_FAILED, "ledger_open", "EXCHANGE_REJECTED")


def test_exec_reject_alone_is_open_failed():
    assert _cls({"executed_notional": 12.5, "exec_reject": "MIN_NOTIONAL"})[0] == OPEN_FAILED


def test_successful_fill_is_still_accepted_and_plain_reject_unchanged():
    assert _cls({"executed_notional": 10.0}) == (ACCEPTED, "ledger_open", None)
    assert _cls({"executed_notional": 10.0, "trade_id": "F00001"}) == (ACCEPTED, "ledger_open", None)
    assert _cls({"block_code": "EXCHANGE_REJECTED"}) == (OPEN_FAILED, "ledger_open", "EXCHANGE_REJECTED")
    assert OPEN_FAILED in OUTCOME_CLASSES


def test_exec_reject_is_persisted_in_the_record_and_summary(tmp_path):
    e = {"executed_notional": 12.5, "block_code": "EXCHANGE_REJECTED", "exec_reject": "INSUFFICIENT_MARGIN",
         "market_type": "USDM_PERP"}
    kind, stage, reason = _cls(e)
    rec = build_decision_record(run_id="r1", cycle_id=1, symbol="ETH/USDT", direction="LONG", entry=e,
                                outcome_kind=kind)
    assert rec["exec_reject"] == "INSUFFICIENT_MARGIN" and rec["outcome_kind"] == OPEN_FAILED
    rec.update({"outcome_stage": stage, "outcome_reason": reason})
    assert "INSUFFICIENT_MARGIN" in why_summary_tr(rec)
    j = DecisionJournal(tmp_path / "decision_journal.jsonl")
    assert j.append_decision(rec)
    rows = list(j.iter_rows())
    assert rows[-1]["exec_reject"] == "INSUFFICIENT_MARGIN" and rows[-1]["outcome_kind"] == OPEN_FAILED
    ok = build_decision_record(run_id="r1", cycle_id=1, symbol="SOL/USDT", direction="LONG",
                               entry={"executed_notional": 10.0}, outcome_kind=ACCEPTED)
    assert ok["exec_reject"] is None                          # yoksa null (uydurulmaz)
    long_reason = build_decision_record(run_id="r", cycle_id=1, symbol="X/USDT", direction="LONG",
                                        entry={"exec_reject": "X" * 500})
    assert len(long_reason["exec_reject"]) == 80              # kayıt sınırlı


def test_engine_tour_journals_ledger_rejection_as_open_failed(tmp_path, monkeypatch):
    """GERÇEK motor turu: risk onayından sonra defter reddederse günlük satırı OPEN_FAILED + `exec_reject` taşır."""
    from test_engine_v3 import _engine
    from test_risk_capacity_and_gates import _force_final_risk_pct, _force_triggers

    import tradingbot.engine_v3 as E

    eng = _engine(tmp_path, monkeypatch, symbols=2, equity=5_000.0)
    assert eng.decision_journal is not None
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 - i * 0.1 for i, s in enumerate(eng.cfg.coins)})
    _force_triggers(monkeypatch, True)

    def _reject(self, **kw):
        self.ledger2.last_reject_reason = "INSUFFICIENT_MARGIN"
        return None
    monkeypatch.setattr(E.TradingEngineV3, "_execute_futures_entry", _reject)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    rows = [r for r in eng.decision_journal.iter_rows() if r.get("kind") == "decision"]
    failed = [r for r in rows if r.get("block_code") == "EXCHANGE_REJECTED"]
    if not failed:
        pytest.skip("fikstür bu turda futures adayı üretmedi")
    for r in failed:
        assert r["outcome_kind"] == OPEN_FAILED, r
        assert r["exec_reject"] == "INSUFFICIENT_MARGIN"
        assert r["outcome_stage"] == "ledger_open"
    assert not any(r.get("outcome_kind") == ACCEPTED for r in rows)
    assert not eng.ledger2.positions


# ---------------------------------------------------------------------------- SPOT istisnası (2026-09-28, öğrenme modu)
def test_spot_fill_misread_by_baseline_status_compare_stays_accepted():
    """Baseline spot dalı DOLAN emri `str(OrderStatus.FILLED)` yüzünden EXCHANGE_REJECTED yazar; defterin ret nedeni
    BOŞTUR. P0 bu satırı OPEN_FAILED yapmamalı (P0 öncesi gibi ACCEPTED); gerçek spot/futures reddi OPEN_FAILED kalır."""
    from decimal import Decimal

    from tradingbot.accounting import SpotLedger

    spot = SpotLedger(1000)
    order = spot.market_buy("ETH/USDT", quote_amount=Decimal("50"), ref_price=Decimal("2000"))
    assert getattr(order.status, "value", order.status) == "FILLED" and spot.last_reject_reason == ""
    assert str(order.status).upper() not in ("FILLED", "PARTIALLY_FILLED")     # baseline karşılaştırması (değişmedi)
    filled = {"executed_notional": 50.0, "block_code": "EXCHANGE_REJECTED", "exec_reject": spot.last_reject_reason}
    assert classify_outcome(filled, is_actionable=True, has_valid_plan=True, verdict="SPOT_LONG") == \
        (ACCEPTED, "ledger_open", None)
    # gerçek spot reddi (nakit yetersiz) → OPEN_FAILED
    bad = spot.market_buy("ETH/USDT", quote_amount=Decimal("5000"), ref_price=Decimal("2000"))
    assert bad.status.value == "REJECTED" and spot.last_reject_reason
    rej = {"executed_notional": 5000.0, "block_code": "EXCHANGE_REJECTED", "exec_reject": spot.last_reject_reason}
    assert classify_outcome(rej, is_actionable=True, has_valid_plan=True, verdict="SPOT_LONG")[0] == OPEN_FAILED
    # istisna YALNIZ spot: futures satırında boş neden yine OPEN_FAILED; `exec_reject` hiç yoksa da OPEN_FAILED
    fut = dict(filled)
    assert classify_outcome(fut, is_actionable=True, has_valid_plan=True, verdict="FUTURES_LONG")[0] == OPEN_FAILED
    no_key = {"executed_notional": 50.0, "block_code": "EXCHANGE_REJECTED"}
    assert classify_outcome(no_key, is_actionable=True, has_valid_plan=True, verdict="SPOT_LONG")[0] == OPEN_FAILED


def test_engine_off_spot_fill_is_journalled_accepted(tmp_path, monkeypatch):
    """GERÇEK motor (öğrenme KAPALI): spot defteri emri doldurur; baseline dalı (bit-aynı) EXCHANGE_REJECTED yazar,
    günlük satırı ise ACCEPTED'dır (P0 öncesiyle aynı; eski kodda da ACCEPTED'dı)."""
    from test_learning_mode_main import SYMS, _cands, _eng

    from tradingbot.coinhead.schema import Verdict
    from tradingbot.core import utc_now

    eng = _eng(tmp_path, monkeypatch, None)
    assert eng.decision_journal is not None
    now = utc_now().replace(microsecond=0)
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1], direction="LONG", notional=20.0, lev=1,
                                             verdict=Verdict.SPOT_LONG)
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
    e = [x for x in risk_log if x.get("symbol") == SYMS[0]][-1]
    assert SYMS[0] in eng.spot2.positions(), "spot defteri emri doldurmalı"
    assert opened == [] and e["block_code"] == "EXCHANGE_REJECTED" and e["exec_reject"] == ""   # baseline aynen
    eng._journal_decisions(risk_log, decisions, now)
    rows = [r for r in eng.decision_journal.iter_rows() if r.get("kind") == "decision" and r.get("symbol") == SYMS[0]]
    assert rows and rows[-1]["outcome_kind"] == ACCEPTED, rows[-1]
