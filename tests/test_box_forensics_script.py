# -*- coding: utf-8 -*-
"""Box forensics script (scripts/box_forensics.py): standalone, read-only, classifies closed Box trades and aggregates them.

The synthetic state is written with the REAL `FuturesLedgerV2` (so the script is tested against the true TradeRecord /
Position field names) plus a hand-written summary, trade memory, box timer status and monitoring gap log."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec, TickData

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "box_forensics.py"
_spec = importlib.util.spec_from_file_location("box_forensics", SCRIPT)
BF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BF)

BOOK = "strategy_paper_box"
NOW = datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)
SECRET = "signature=0123456789abcdef0123456789abcdef0123456789abcdef&apiKey=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCd"


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _open(led, mem, sym, side, price, stop, at, *, targets=(), signal_close=None, lag_min=12.0):
    pos = led.open(sym, side, D(str(price)), SizeSpec(D("30"), AmountType.NOTIONAL, 3), stop=D(str(stop)),
                   targets=[D(str(x)) for x in targets], setup_type="box_fade", trigger_text="BOX_FADE_TOP",
                   features={"strategy": "b1_box_fade"}, now=at)
    assert pos is not None, led.last_reject_reason
    sig_open = at - timedelta(minutes=lag_min) - timedelta(minutes=5)          # 5m signal bar OPEN
    mem.append({"kind": "entry", "trade_id": pos.id, "recorded_at": at.isoformat(), "source": "STRATEGY_PAPER", "symbol": sym,
                "features": {"strategy": "b1_box_fade", "signal_close": signal_close if signal_close is not None else price,
                             "signal_ts": _ms(sig_open), "stop_at_entry": stop}})
    return pos


def _close(led, sym, at, reason, price):
    assert led.close_manual(sym, D(str(price)), reason=reason, now=at) is not None


def _tick(led, sym, at, price):
    recs = led.tick({sym: TickData(last=D(str(price)))}, now_utc=at)
    assert recs and recs[0].symbol == sym


def build_state(tmp_path: Path, *, eod_close=True, summary=True, memory=True, timer=True, gaps=True) -> Path:
    state = tmp_path / "state"
    book = state / BOOK
    book.mkdir(parents=True)
    led = FuturesLedgerV2(D("100"))
    mem: list = []
    # 1 ARB LONG target the same day (the rule planned from 0.99; the fill came after the favorable move)
    _open(led, mem, "ARB/USDT", "LONG", 1.00, 0.97, _t("2026-09-20T01:30:00"), targets=[1.03], signal_close=0.99)
    _tick(led, "ARB/USDT", _t("2026-09-20T04:00:00"), 1.04)
    # 2 AVAX SHORT opened 09-20, stopped 09-25: stuck across five UTC midnights although eod_close is on
    _open(led, mem, "AVAX/USDT", "SHORT", 25.0, 25.6, _t("2026-09-20T01:06:00"), signal_close=24.9)
    _tick(led, "AVAX/USDT", _t("2026-09-25T20:23:00"), 26.0)
    # 3 SOL LONG: legitimate end-of-day flat 7 minutes after midnight
    _open(led, mem, "SOL/USDT", "LONG", 100.0, 97.0, _t("2026-09-21T22:00:00"))
    _close(led, "SOL/USDT", _t("2026-09-22T00:07:00"), "BOX_EOD_FLAT", 99.0)
    # 4 DOGE LONG: EOD flat 3.5 h after midnight -> stuck
    _open(led, mem, "DOGE/USDT", "LONG", 0.20, 0.194, _t("2026-09-22T10:00:00"))
    _close(led, "DOGE/USDT", _t("2026-09-23T03:30:00"), "BOX_EOD_FLAT", 0.199)
    # 5 LINK SHORT: structure exit -> OTHER
    _open(led, mem, "LINK/USDT", "SHORT", 12.0, 12.4, _t("2026-09-23T12:00:00"))
    _close(led, "LINK/USDT", _t("2026-09-23T13:00:00"), "BOX_OUTSIDE_BREAKOUT_EXIT", 12.1)
    # 6 XRP LONG: stop hit by a closed 1h bar 20 minutes after midnight (inside the grace window) -> STOP
    _open(led, mem, "XRP/USDT", "LONG", 1.0, 0.97, _t("2026-09-23T23:30:00"))
    recs = led.tick({"XRP/USDT": TickData(last=D("0.975"), mark=D("0.975"), high=D("1.0"), low=D("0.96"), open=D("0.99"))},
                    now_utc=_t("2026-09-24T00:20:00"))
    assert recs and recs[0].exit_reason == "stop"
    # 7 ENA SHORT stop the same day
    _open(led, mem, "ENA/USDT", "SHORT", 0.50, 0.512, _t("2026-09-26T01:15:00"))
    _tick(led, "ENA/USDT", _t("2026-09-26T07:30:00"), 0.52)
    # open: FET crossed midnight (overdue), REZ same day
    _open(led, mem, "FET/USDT", "LONG", 1.5, 1.45, _t("2026-09-25T23:00:00"))
    _open(led, mem, "REZ/USDT", "SHORT", 0.03, 0.031, _t("2026-09-26T06:00:00"))
    led.save(book / "futures_ledger.json")
    if memory:
        lines = [json.dumps(r) for r in mem] + ["{not json", "", json.dumps({"kind": "exit", "trade_id": mem[0]["trade_id"]})]
        (book / "trade_memory.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if summary:
        doc = {"key": BOOK, "name": "b1_box_fade", "generated_at": "2026-09-26T07:59:00+00:00",
               "rule_evaluated_at": "2026-09-26T07:55:00+00:00", "rule_stale_s": 240.0, "run_id": "run-should-not-matter",
               "rule_params": {"near_frac": 0.1, "exit_kind": "box_mid", "min_stop_pct": 2.22, "eod_close": eod_close,
                               "leverage": 3, "leverage_max": 4},
               "counters": {"opened": 9, "closed": 7, "rejected": 10, "tours": 50, "data_rejected": 120},
               "rejections": {"DATA_FRAME_MISSING_5M": 120, "MAX_POSITION_PCT": 7, "STRUCTURE_BOX_NO_CONFIRMED_EDGE_STRUCTURE": 3},
               "structures": {"mode": "ENFORCE"},
               "funding": {"incomplete_closed": [mem[1]["trade_id"]], "pending_positions": {}},
               "last_actions": {"FET/USDT": {"action": "DATA_REJECTED", "reason": "DATA_FRAME_MISSING_5M", "at": "2026-09-26T07:55:00+00:00",
                                             "stage": "SIGNAL", "would_act": "CLOSE"}},
               "data_events_recent": [
                   {"symbol": "FET/USDT", "kind": "REJECT", "reason": "DATA_FRAME_MISSING_5M", "at": "2026-09-26T00:10:00+00:00",
                    "stage": "SIGNAL", "would_act": "CLOSE", "detail": {"secret_like": SECRET}},
                   {"symbol": "OP/USDT", "kind": "REJECT", "reason": "DATA_FRAME_MISSING_5M", "at": "2026-09-26T00:11:00+00:00",
                    "stage": "SIGNAL", "would_act": "NONE"},
                   {"symbol": "OP/USDT", "kind": "PRICE_GAP", "reason": "NO_VERIFIED_FUTURES_PRICE", "at": "2026-09-26T01:00:00+00:00"},
                   "not-a-dict"],
               "data_checks": {"FET/USDT": {"ok": False, "reason": "DATA_FRAME_MISSING_5M"}, "REZ/USDT": {"ok": True, "reason": ""}}}
        (state / ("%s.json" % BOOK)).write_text(json.dumps(doc), encoding="utf-8")
    if timer:
        (state / "box_timer.json").write_text(json.dumps({
            "generated_at": "2026-09-26T07:59:30+00:00", "alive": True, "evaluations": 80, "missed_bars": 2,
            "duplicates_blocked": 0, "errors": 1, "last_error": "HTTPError: GET https://fapi.example/x?" + SECRET,
            "lag_p50_s": 4.2, "lag_max_s": 30.0, "last_eval": {"bar_open": "2026-09-26T07:50:00+00:00", "symbols": 40}}), encoding="utf-8")
    if gaps:
        rows = [{"kind": "MONITORING_GAP", "book": BOOK, "from": "2026-09-25T20:00:00+00:00", "to": "2026-09-26T07:10:00+00:00",
                 "gap_s": 40200.0, "positions": ["FET/USDT"]},
                {"kind": "MONITORING_GAP", "book": "strategy_paper_m2", "from": "x", "to": "y", "gap_s": 9000.0, "positions": []}]
        (state / "monitoring_gaps.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nbroken\n", encoding="utf-8")
    return state


def _by_symbol(rep):
    return {t["symbol"]: t for t in rep["trades"]}


def _snapshot(root: Path):
    return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


# ----------------------------------------------------------------------------- classification
def test_classification_from_a_real_ledger(tmp_path):
    rep = BF.build_report(build_state(tmp_path), now=NOW)
    t = _by_symbol(rep)
    assert {s: r["class"] for s, r in t.items()} == {
        "ARB/USDT": "TARGET", "AVAX/USDT": "STUCK_MULTI_DAY", "SOL/USDT": "EOD", "DOGE/USDT": "STUCK_MULTI_DAY",
        "LINK/USDT": "OTHER", "XRP/USDT": "STOP", "ENA/USDT": "STOP"}
    assert t["AVAX/USDT"]["exit_reason"] == "stop" and t["AVAX/USDT"]["reason_class"] == "STOP"
    assert t["AVAX/USDT"]["crossed_utc_day"] is True and t["AVAX/USDT"]["utc_midnights_crossed"] == 5
    assert t["AVAX/USDT"]["hold_h"] == pytest.approx((_t("2026-09-25T20:23:00") - _t("2026-09-20T01:06:00")).total_seconds() / 3600, abs=0.01)
    assert t["DOGE/USDT"]["reason_class"] == "EOD" and t["DOGE/USDT"]["open_min_after_first_midnight"] == pytest.approx(210.0)
    assert t["SOL/USDT"]["crossed_utc_day"] is True and t["SOL/USDT"]["open_min_after_first_midnight"] == pytest.approx(7.0)
    assert t["XRP/USDT"]["crossed_utc_day"] is True, "a stop inside the grace window is a STOP, not STUCK"
    assert t["ENA/USDT"]["crossed_utc_day"] is False and t["ENA/USDT"]["utc_midnights_crossed"] == 0
    assert [r["closed_at"] for r in rep["trades"]] == sorted(r["closed_at"] for r in rep["trades"])


def test_trade_fields_use_the_real_record_names(tmp_path):
    state = build_state(tmp_path)
    hist = {h["symbol"]: h for h in json.loads((state / BOOK / "futures_ledger.json").read_text(encoding="utf-8"))["history"]}
    t = _by_symbol(BF.build_report(state, now=NOW))
    arb, ena, xrp = t["ARB/USDT"], t["ENA/USDT"], t["XRP/USDT"]
    for sym, row in t.items():
        h = hist[sym]
        assert row["pnl"] == pytest.approx(float(h["pnl"]), abs=1e-4)
        assert row["r_multiple"] == pytest.approx(float(h["r_multiple"]), abs=1e-4)
        assert row["fees"] == pytest.approx(float(h["fees"]), abs=1e-4) and row["fees"] > 0
        assert row["exit_price"] == pytest.approx(float(h["exit_price"])) and row["entry"] == pytest.approx(float(h["entry"]))
        assert row["leverage"] == 3 and row["memory_joined"] is True
        # stop distance from the ledger's own risk (|entry - initial_stop| x qty), equal to the memory stop at the fill
        stop = {"ARB/USDT": 0.97, "AVAX/USDT": 25.6, "SOL/USDT": 97.0, "DOGE/USDT": 0.194, "LINK/USDT": 12.4,
                "XRP/USDT": 0.97, "ENA/USDT": 0.512}[sym]
        assert row["stop_pct_fill"] == pytest.approx(abs(row["entry"] - stop) / row["entry"] * 100, abs=2e-3)
        assert row["entry_lag_min"] == pytest.approx(12.0) and row["entry_lag_basis"] == "signal_ts"
    assert arb["entry_drift_pct"] == pytest.approx((arb["entry"] - 0.99) / 0.99 * 100, abs=1e-3)
    assert arb["entry_drift_dir_pct"] > 0, "LONG filled above the signal close = after the favorable move"
    assert t["AVAX/USDT"]["entry_drift_dir_pct"] < 0, "SHORT filled above the signal close = closer to the stop"
    assert arb["stop_pct_plan"] == pytest.approx(abs(0.99 - 0.97) / 0.99 * 100, abs=1e-3)
    assert ena["exit_basis"] == "GAP_FILL_AT_FIRST_OBSERVATION" and ena["exit_via"] == "LIVE_MARK" and ena["stop_triggered"] is True
    assert xrp["exit_basis"] == "STOP_AT_LEVEL" and xrp["exit_via"] == "CLOSED_BAR" and xrp["exit_first_source"] == "BAR_OPEN"
    assert t["SOL/USDT"]["exit_via"] == "RULE" and arb["exit_via"] == "TARGET_LEVEL" and t["LINK/USDT"]["exit_via"] == "RULE_OR_MANUAL"
    assert t["SOL/USDT"]["path_unverified"] is False and t["SOL/USDT"]["funding_complete"] in (True, False, None)


# ----------------------------------------------------------------------------- aggregation
def test_aggregation_per_class_and_overall(tmp_path):
    state = build_state(tmp_path)
    rep = BF.build_report(state, now=NOW)
    rows, agg = rep["trades"], rep["aggregates"]
    for c in BF.CLASSES:
        sub = [r for r in rows if r["class"] == c]
        a = agg["by_class"][c]
        assert a["n"] == len(sub)
        assert a["net_pnl"] == pytest.approx(sum(r["pnl"] for r in sub), abs=1e-3)
        if sub:
            assert a["mean_r"] == pytest.approx(sum(r["r_multiple"] for r in sub) / len(sub), abs=1e-3)
        else:
            assert a["mean_r"] is None and a["win_rate"] is None
    ov = agg["overall"]
    led = json.loads((state / BOOK / "futures_ledger.json").read_text(encoding="utf-8"))
    assert ov["n"] == 7 and sum(agg["by_class"][c]["n"] for c in BF.CLASSES) == 7
    open_part = sum(float(p["meta"]["gross_realized"]) - float(p["fees_paid"]) + float(p["funding_received"]) - float(p["funding_paid"])
                    for p in led["positions"].values())
    assert open_part < 0, "the two open positions have paid their entry fee"
    assert ov["net_pnl"] + open_part == pytest.approx(float(led["wallet_balance"]) - float(led["starting_equity"]), abs=1e-3), \
        "closed pnl (fees + funding included) plus open positions' entry fees explain the whole wallet change"
    rc = rep["ledger"]["reconciliation"]
    assert rc["unexplained"] == pytest.approx(0.0, abs=1e-3) and rc["open_realized"] == pytest.approx(open_part, abs=1e-4)
    assert ov["wins"] == sum(1 for r in rows if r["pnl"] > 0) and ov["max_drawdown_r"] <= 0
    assert agg["excluding_stuck"]["n"] == 5
    assert agg["excluding_stuck"]["net_pnl"] == pytest.approx(ov["net_pnl"] - agg["by_class"]["STUCK_MULTI_DAY"]["net_pnl"], abs=1e-3)
    assert agg["by_exit_reason"]["BOX_EOD_FLAT"]["n"] == 2 and agg["by_exit_reason"]["stop"]["n"] == 3
    assert set(agg["by_side"]) == {"LONG", "SHORT"}
    assert rep["ledger"]["closed_total"] == 7 and rep["ledger"]["open_total"] == 2 and rep["ledger"]["memory_rows_joined"] == 7
    eq = rep["entry_quality"]
    assert eq["n_with_signal_close"] == 7 and eq["n_filled_after_favorable_move"] >= 1 and eq["n_filled_closer_to_stop"] >= 1
    assert any(h.startswith("STUCK_MULTI_DAY: 2 trades") for h in rep["highlights"])


def test_split_and_since_filters(tmp_path):
    state = build_state(tmp_path)
    rep = BF.build_report(state, now=NOW, split_at=_t("2026-09-23T00:00:00"))
    sp = rep["aggregates"]["split"]
    assert sp["opened_before"]["n"] == 4 and sp["opened_at_or_after"]["n"] == 3
    rep2 = BF.build_report(state, now=NOW, since="2026-09-24T00:00:00Z")
    assert {r["symbol"] for r in rep2["trades"]} == {"XRP/USDT", "AVAX/USDT", "ENA/USDT"}
    assert rep2["ledger"]["closed_total"] == 7 and rep2["ledger"]["closed_in_report"] == 3


def test_eod_setting_and_grace_window(tmp_path):
    off = BF.build_report(build_state(tmp_path / "a", eod_close=False), now=NOW)
    t = _by_symbol(off)
    assert off["settings"]["eod_close"] is False and off["settings"]["eod_close_source"] == "summary rule_params"
    assert t["AVAX/USDT"]["class"] == "STOP" and t["DOGE/USDT"]["class"] == "EOD", "no STUCK when eod_close is off"
    assert not any(o["overdue_eod"] for o in off["open_positions"])
    forced = BF.build_report(build_state(tmp_path / "b", eod_close=False), now=NOW, eod_mode="on")
    assert _by_symbol(forced)["AVAX/USDT"]["class"] == "STUCK_MULTI_DAY"
    tight = _by_symbol(BF.build_report(build_state(tmp_path / "c"), now=NOW, grace_min=5.0))
    assert tight["SOL/USDT"]["class"] == "STUCK_MULTI_DAY" and tight["XRP/USDT"]["class"] == "STUCK_MULTI_DAY"
    assert tight["ENA/USDT"]["class"] == "STOP"


# ----------------------------------------------------------------------------- open positions / summary
def test_open_positions_rejections_and_events(tmp_path):
    rep = BF.build_report(build_state(tmp_path), now=NOW)
    op = {o["symbol"]: o for o in rep["open_positions"]}
    assert set(op) == {"FET/USDT", "REZ/USDT"}
    assert op["FET/USDT"]["age_h"] == pytest.approx(9.0) and op["FET/USDT"]["overdue_eod"] is True
    assert op["FET/USDT"]["last_action"]["would_act"] == "CLOSE"
    assert op["REZ/USDT"]["age_h"] == pytest.approx(2.0) and op["REZ/USDT"]["overdue_eod"] is False
    assert op["REZ/USDT"]["crossed_utc_day"] is False and op["FET/USDT"]["stop_pct"] > 0
    sm = rep["summary"]
    assert sm["rejections_total"] == 130 and sm["rejections_data_total"] == 120
    assert list(sm["rejections"])[0] == "DATA_FRAME_MISSING_5M"
    assert sm["counters"]["data_rejected"] == 120 and sm["funding_incomplete_closed_ids"] == 1
    de = rep["data_events"]
    assert de["n"] == 3 and de["by_kind"] == {"REJECT": 2, "PRICE_GAP": 1}
    assert de["reject_by_stage"] == {"SIGNAL": 2} and de["reject_would_act"] == {"CLOSE": 1, "NONE": 1}
    assert de["blocked_rule_closes"] == [{"symbol": "FET/USDT", "at": "2026-09-26T00:10:00+00:00", "reason": "DATA_FRAME_MISSING_5M"}]
    assert rep["data_checks"] == {"n_symbols": 2, "ok": 1, "by_reason": {"DATA_FRAME_MISSING_5M": 1, "OK": 1}}
    assert rep["box_timer"]["missed_bars"] == 2 and "0123456789abcdef" not in rep["box_timer"]["last_error"]
    assert rep["monitoring_gaps"]["n"] == 1 and rep["monitoring_gaps"]["total_hours"] == pytest.approx(11.17)
    assert any("OPEN and past EOD grace" in h and "FET/USDT" in h for h in rep["highlights"])


# ----------------------------------------------------------------------------- missing / malformed inputs
def test_missing_optional_files_are_reported_not_fatal(tmp_path, capsys):
    state = build_state(tmp_path, summary=False, memory=False, timer=False, gaps=False)
    assert sorted(p.name for p in state.rglob("*") if p.is_file() and not p.name.endswith(".bak")) == ["futures_ledger.json"]
    rep = BF.build_report(state, now=NOW)
    assert rep["summary"] is None and rep["data_events"] is None and rep["data_checks"] is None
    assert rep["box_timer"] is None and rep["monitoring_gaps"] is None and rep["files"]["trade_memory"] is None
    assert rep["settings"]["eod_close"] is True and rep["settings"]["eod_close_source"].startswith("default")
    t = _by_symbol(rep)
    assert all(r["entry_drift_pct"] is None and r["signal_close"] is None and r["memory_joined"] is False for r in t.values())
    assert t["AVAX/USDT"]["class"] == "STUCK_MULTI_DAY" and t["AVAX/USDT"]["stop_pct_fill"] is not None, "stop % from ledger risk"
    assert all(r["entry_lag_min"] is None for r in t.values())
    assert BF.main(["--state", str(state), "--now", "2026-09-26T08:00:00Z"]) == 0
    out = capsys.readouterr().out
    assert "SUMMARY:" in out and "not found" in out and "STUCK_MULTI_DAY" in out


def test_missing_or_broken_ledger_exits_2(tmp_path, capsys):
    state = tmp_path / "state"
    state.mkdir()
    assert "error" in BF.build_report(state)
    assert BF.main(["--state", str(state)]) == 2
    capsys.readouterr()
    assert BF.main(["--state", str(state), "--json"]) == 2
    assert "ledger not found" in json.loads(capsys.readouterr().out)["error"]
    (state / BOOK).mkdir()
    (state / BOOK / "futures_ledger.json").write_text("{broken", encoding="utf-8")
    assert BF.main(["--state", str(state)]) == 2
    assert BF.main(["--state", str(tmp_path / "nope")]) == 2


def test_sparse_and_legacy_rows_do_not_crash(tmp_path, capsys):
    state = tmp_path / "state"
    (state / BOOK).mkdir(parents=True)
    led = {"wallet_balance": "83.99", "positions": {"OLD/USDT": {"side": "LONG", "entry": 2.0, "units": 5, "stop": 1.9,
                                                                 "opened_at": "2026-09-24T10:00:00Z", "last_price": 2.1}},
           "history": [{"symbol": "AVAX/USDT", "opened_at": "2026-09-25T10:00:00+00:00", "closed_at": "2026-09-25T20:24:10+00:00",
                        "exit_reason": "BOX_EOD_FLAT", "pnl": "-3.1"},
                       {"symbol": "X/USDT", "exit_reason": "likidasyon", "pnl": "garbage", "opened_at": "not-a-time"},
                       {"symbol": "Y/USDT", "exit_reason": "hedef2", "net_pnl": 1.5, "r_multiple": 0.75,
                        "opened_at": _ms(_t("2026-09-22T08:00:00")), "closed_at": "2026-09-22T10:00:00.123Z"},
                       "not-a-dict"]}
    (state / BOOK / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    rep = BF.build_report(state, now=NOW)
    t = _by_symbol(rep)
    assert t["AVAX/USDT"]["class"] == "EOD" and t["X/USDT"]["class"] == "OTHER" and t["Y/USDT"]["class"] == "TARGET"
    assert t["X/USDT"]["pnl"] is None and t["X/USDT"]["crossed_utc_day"] is None
    assert t["Y/USDT"]["pnl"] == 1.5 and t["Y/USDT"]["closed_at"] == "2026-09-22T10:00:00Z" and t["Y/USDT"]["hold_h"] == pytest.approx(2.0)
    assert rep["ledger"]["starting_equity"] is None and rep["ledger"]["reconciliation"] is None
    assert rep["aggregates"]["overall"]["n"] == 3 and rep["aggregates"]["overall"]["net_pnl"] == pytest.approx(-1.6)
    assert any("not objects" in w for w in rep["warnings"]) and any("opened_at/closed_at" in w for w in rep["warnings"])
    o = rep["open_positions"][0]
    assert o["entry"] == 2.0 and o["qty"] == 5 and o["unrealized_pnl"] == pytest.approx(0.5) and o["overdue_eod"] is True
    assert BF.main(["--state", str(state), "--now", "2026-09-26T08:00:00Z"]) == 0
    assert BF.main(["--state", str(state), "--json"]) == 0
    assert "likidasyon" in capsys.readouterr().out


# ----------------------------------------------------------------------------- CLI / JSON / read-only / standalone
def test_json_output_is_complete_and_secret_free(tmp_path, capsys):
    state = build_state(tmp_path)
    assert BF.main(["--state", str(state), "--json", "--now", "2026-09-26T08:00:00Z", "--split-at", "2026-09-23"]) == 0
    raw = capsys.readouterr().out
    doc = json.loads(raw)
    assert doc["kind"] == "BOX_FORENSICS" and doc["schema"] == BF.SCHEMA and doc["read_only"] is True
    assert {"settings", "ledger", "trades", "aggregates", "entry_quality", "open_positions", "summary", "data_events",
            "data_checks", "box_timer", "monitoring_gaps", "highlights", "warnings"} <= set(doc)
    assert len(doc["trades"]) == 7 and set(doc["aggregates"]["by_class"]) == set(BF.CLASSES)
    assert doc["aggregates"]["split"]["at"] == "2026-09-23T00:00:00Z"
    assert "0123456789abcdef" not in raw and "AbCdEfGhIj" not in raw and "run-should-not-matter" not in raw
    assert BF.main(["--state", str(state), "--now", "2026-09-26T08:00:00Z"]) == 0
    txt = capsys.readouterr().out
    assert "0123456789abcdef" not in txt and "AbCdEfGhIj" not in txt
    for needle in ("CLOSED TRADES", "BY CLASS", "STUCK_MULTI_DAY", "OPEN POSITIONS", "OVERDUE_EOD", "rejections total 130",
                   "blocked rule CLOSE: FET/USDT", "BOX TIMER", "MONITORING GAPS", "HIGHLIGHTS"):
        assert needle in txt, needle


def test_never_writes_anything(tmp_path, capsys):
    state = build_state(tmp_path)
    before = _snapshot(tmp_path)
    for argv in (["--json"], [], ["--since", "2026-09-22", "--split-at", "2026-09-23T00:00:00Z", "--eod-close", "off"]):
        assert BF.main(["--state", str(state)] + argv) == 0
    capsys.readouterr()
    assert _snapshot(tmp_path) == before, "no file is created, modified or touched"


def test_runs_standalone_without_the_repo(tmp_path):
    state = build_state(tmp_path)
    copy = tmp_path / "box_forensics.py"                    # like /tmp/box_forensics.py on the VPS
    copy.write_bytes(SCRIPT.read_bytes())
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    before = _snapshot(state)
    res = subprocess.run([sys.executable, "-I", str(copy), "--state", str(state), "--json", "--now", "2026-09-26T08:00:00Z"],
                         cwd=str(tmp_path), env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert res.returncode == 0, res.stderr
    assert len(json.loads(res.stdout)["trades"]) == 7
    assert not (tmp_path / "__pycache__").exists() and _snapshot(state) == before
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}, mods


def test_redaction_keeps_reason_codes():
    r = BF.redact("GET /x?" + SECRET + " Bearer abc.def-123 token: s3cr3tvalue")
    assert "0123456789abcdef" not in r and "AbCdEfGhIj" not in r and "abc.def-123" not in r and "s3cr3tvalue" not in r
    for code in ("STRUCTURE_BOX_NO_CONFIRMED_EDGE_STRUCTURE", "DATA_FRAME_MISSING_5M", "BOX_UNMEASURABLE:NOT_ENOUGH_DAILY_BARS",
                 "başa-baş stop"):
        assert BF.redact(code) == code
    assert BF.redact("f" * 64) == "***" and BF.redact(None) is None
