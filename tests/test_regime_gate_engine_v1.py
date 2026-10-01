# -*- coding: utf-8 -*-
"""PIYASA REJIMI KAPISI — canli motor kancasi GERCEKTEN calisiyor mu? (V7)

Ilk tur (OFF) adaylarin yonunu verir. Ikinci turda BTC gunluk karesi (motorun okudugu kopya)
DOWN ya da UP olacak sekilde kurulur. Beklenen (ENFORCE, r1): DOWN'da her aday REGIME_VETO;
UP'ta SHORT adaylar REGIME_VETO (R1_SHORT_BLOCKED), LONG adaylar ACILIR. SHADOW kaydeder, engellemez.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_candle_confirmation_engine_v1 import SYMS, _directions  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import (EQUITY, _force_opportunities, _force_triggers,  # noqa: E402
                                          _funnel, _opp, _profile, _risk_log)

BTC = "BTC/USDT"


def _inject_btc(eng, monkeypatch, regime: str):
    """BTC karesi = ETH karesinin kopyasi; 1d kapanislari EMA200'un ustune/altina konur."""
    orig = eng.runner.run_symbol

    def wrapped(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        src = eng.runner.last_frames.get(SYMS[0])
        if src is not None and BTC not in eng.runner.last_frames:
            fr = dict(src)
            d1 = fr["1d"].copy()
            d1["ema200"] = d1["close"] * (1.05 if regime == "DOWN" else 0.95)
            fr["1d"] = d1
            eng.runner.last_frames[BTC] = fr
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", wrapped)


def _two_tours(tmp_path, monkeypatch, mode: str, regime: str):
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=2, equity=EQUITY)
    _force_opportunities(monkeypatch, {s: _opp(0.80) for s in SYMS})
    _force_triggers(monkeypatch, False)
    es = eng.cfg.v3.entry_selectivity
    es.candle_confirmation_mode = "OFF"
    es.chart_confirmation_mode = "OFF"
    es.regime_gate_mode = "OFF"
    eng.tour(do_scan=False, obsidian=False, charts=False)
    dirs = _directions(eng)
    assert set(dirs) == set(SYMS), dirs
    _inject_btc(eng, monkeypatch, regime)
    _force_triggers(monkeypatch, True)
    es.regime_gate_mode = mode
    es.regime_gate_variant = "r1_long_only_uptrend"
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    opened = [x.split(" ")[0] for x in s["opened"]]
    recs = {e["symbol"]: e for e in _risk_log(eng) if e.get("symbol") in SYMS}
    return eng, opened, recs, dirs


def test_enforce_in_downtrend_blocks_every_candidate(tmp_path, monkeypatch):
    eng, opened, recs, dirs = _two_tours(tmp_path, monkeypatch, "ENFORCE", "DOWN")
    assert opened == [], opened
    f = _funnel(eng)["run"]
    assert f["regime_blocked"] == 2 and f["opened"] == 0
    for sym in SYMS:
        r = recs[sym]
        expected = "REGIME_VETO:R1_SHORT_BLOCKED" if dirs[sym] == "SHORT" else "REGIME_VETO:R1_NOT_UPTREND"
        assert str(r.get("block_code", "")) == expected, (sym, r.get("block_code"))
        rg = r["regime_gate"]
        assert rg["regime"] == "DOWN" and rg["blocks"] is True and set(rg["shadow"]) == {
            "r1_long_only_uptrend", "r2_no_trade_downtrend", "r3_follow_regime"}


def test_enforce_in_uptrend_opens_longs_and_blocks_shorts(tmp_path, monkeypatch):
    eng, opened, recs, dirs = _two_tours(tmp_path, monkeypatch, "ENFORCE", "UP")
    longs = sorted(s for s in SYMS if dirs[s] == "LONG")
    shorts = [s for s in SYMS if dirs[s] == "SHORT"]
    assert sorted(opened) == longs, (opened, dirs)
    f = _funnel(eng)["run"]
    assert f["regime_blocked"] == len(shorts) and f["opened"] == len(longs)
    for s in shorts:
        assert str(recs[s].get("block_code", "")) == "REGIME_VETO:R1_SHORT_BLOCKED"
    for s in longs:
        assert recs[s]["regime_gate"]["regime"] == "UP" and recs[s]["regime_gate"]["blocks"] is False


def test_shadow_records_but_never_blocks(tmp_path, monkeypatch):
    eng, opened, recs, dirs = _two_tours(tmp_path, monkeypatch, "SHADOW", "DOWN")
    assert sorted(opened) == sorted(SYMS), opened
    assert _funnel(eng)["run"]["regime_blocked"] == 0
    for s in SYMS:
        rg = recs[s]["regime_gate"]
        assert rg["mode"] == "SHADOW" and rg["blocks"] is False and rg["regime"] == "DOWN"
        assert rg["verdict"]["ok"] is False
