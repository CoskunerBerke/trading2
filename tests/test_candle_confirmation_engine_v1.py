# -*- coding: utf-8 -*-
"""MUM ONAYI — canli motor kancasi GERCEKTEN calisiyor mu? (V4, 2026-09-12)

Statik string aramasi DEGIL: gercek `TradingEngineV3.tour` surulur. Ilk turda (OFF, tetiksiz)
adaylarin yonu okunur; ikinci turda ilk adayin son iki 4h bari adayin yonune KARSI bir yutan
formasyona cevrilir. Beklenen: ENFORCE'ta o aday `CANDLE_VETO` ile acilmaz ve huni sayaci
artar; SHADOW'da kayit yazilir ama aday ACILIR; OFF'ta kayit bile yoktur.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import (EQUITY, _force_opportunities, _force_triggers,  # noqa: E402
                                          _funnel, _opp, _profile, _risk_log)

SYMS = ("ETH/USDT", "SOL/USDT")


def _directions(eng) -> dict[str, str]:
    out = {}
    for e in _risk_log(eng):
        v = str(e.get("verdict") or "")
        if e.get("symbol") in SYMS and v:
            out[e["symbol"]] = "SHORT" if "SHORT" in v else "LONG"
    return out


def _set_last_bars(h4, rows):
    """Son len(rows) barin OHLC'sini yazar; zaman damgasi ve hacim DEGISMEZ (kapali bar kalir)."""
    n = len(rows)
    for i, (o, h, lo, c) in enumerate(rows):
        idx = len(h4) - n + i
        h4.iloc[idx, h4.columns.get_loc("open")] = o
        h4.iloc[idx, h4.columns.get_loc("high")] = h
        h4.iloc[idx, h4.columns.get_loc("low")] = lo
        h4.iloc[idx, h4.columns.get_loc("close")] = c


def _opposite_engulfing(c: float, direction: str):
    if direction == "LONG":       # ayi tarafli yutan
        return [(c * 0.990, c * 1.002, c * 0.988, c * 1.000),
                (c * 1.003, c * 1.004, c * 0.984, c * 0.985)]
    return [(c * 1.010, c * 1.012, c * 0.998, c * 1.000),   # boga tarafli yutan
            (c * 0.997, c * 1.016, c * 0.996, c * 1.015)]


def _neutral(c: float):
    return [(c * 1.000, c * 1.008, c * 0.998, c * 1.005),
            (c * 1.000, c * 1.006, c * 0.997, c * 1.003),
            (c * 1.000, c * 1.009, c * 0.995, c * 1.004)]


def _install_shapes(eng, monkeypatch, shapes_by_symbol):
    """`run_symbol` sonrasi motorun okudugu kareyi KOPYA uzerinde degistirir; ajanlarin
    gordugu orijinal DataFrame'e DOKUNMAZ (karar yonu degismesin)."""
    orig = eng.runner.run_symbol

    def wrapped(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        maker = shapes_by_symbol.get(symbol)
        if maker is not None:
            fr = dict(eng.runner.last_frames[symbol])
            h4 = fr["4h"].copy()
            _set_last_bars(h4, maker(float(h4["close"].iloc[-1])))
            fr["4h"] = h4
            eng.runner.last_frames[symbol] = fr
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", wrapped)


def _two_tours(tmp_path, monkeypatch, mode: str):
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=2, equity=EQUITY)
    _force_opportunities(monkeypatch, {s: _opp(0.80) for s in SYMS})
    _force_triggers(monkeypatch, False)
    eng.cfg.v3.entry_selectivity.candle_confirmation_mode = "OFF"
    eng.tour(do_scan=False, obsidian=False, charts=False)
    dirs = _directions(eng)
    assert set(dirs) == set(SYMS), dirs
    first, second = SYMS
    _install_shapes(eng, monkeypatch, {
        first: lambda c: _opposite_engulfing(c, dirs[first]),
        second: _neutral})
    _force_triggers(monkeypatch, True)
    eng.cfg.v3.entry_selectivity.candle_confirmation_mode = mode
    eng.cfg.v3.entry_selectivity.candle_confirmation_variant = "c3_4h_veto"
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    opened = [x.split(" ")[0] for x in s["opened"]]
    recs = {e["symbol"]: e for e in _risk_log(eng) if e.get("symbol") in SYMS}
    return eng, opened, recs, first, second


def test_enforce_blocks_the_candidate_facing_an_opposite_shape(tmp_path, monkeypatch):
    eng, opened, recs, first, second = _two_tours(tmp_path, monkeypatch, "ENFORCE")
    assert opened == [second], opened
    f = _funnel(eng)["run"]
    assert f["candle_blocked"] == 1 and f["trigger_fired"] == 2 and f["opened"] == 1
    r1 = recs[first]
    assert str(r1.get("block_code", "")).startswith("CANDLE_VETO:C3_OPPOSITE_PATTERN"), r1.get("block_code")
    cc = r1["candle_confirmation"]
    assert cc["mode"] == "ENFORCE" and cc["blocks"] is True and cc["policy_version"] >= "candle_v1.1.0"
    assert set(cc["shadow"]) == {"c1_4h", "c2_4h_confirm", "c3_4h_veto", "c4_1d"}
    r2 = recs[second]
    assert r2["candle_confirmation"]["blocks"] is False
    assert not str(r2.get("block_code", "")).startswith("CANDLE")


def test_shadow_records_the_verdict_but_never_blocks(tmp_path, monkeypatch):
    eng, opened, recs, first, second = _two_tours(tmp_path, monkeypatch, "SHADOW")
    assert sorted(opened) == sorted(SYMS), opened
    assert _funnel(eng)["run"]["candle_blocked"] == 0
    cc = recs[first]["candle_confirmation"]
    assert cc["mode"] == "SHADOW" and cc["blocks"] is False
    assert cc["verdict"] == {"ok": False, "reason": "C3_OPPOSITE_PATTERN"}


def test_off_leaves_the_decision_path_untouched(tmp_path, monkeypatch):
    eng, opened, recs, first, second = _two_tours(tmp_path, monkeypatch, "OFF")
    assert sorted(opened) == sorted(SYMS), opened
    assert "candle_confirmation" not in recs[first] and "candle_confirmation" not in recs[second]
    assert _funnel(eng)["run"]["candle_blocked"] == 0
