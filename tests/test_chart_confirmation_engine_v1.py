# -*- coding: utf-8 -*-
"""GRAFIK FORMASYONU ONAYI — canli motor kancasi GERCEKTEN calisiyor mu? (V5)

Ilk turda (OFF) adaylarin yonu okunur; ikinci turda ilk adayin son 120 4h bari, adayin
yonune KARSI taze kirilisli bir cift dip/tepe ile degistirilir (yalniz motorun okudugu kopya).
Beklenen: ENFORCE'ta `CHART_VETO`, SHADOW'da kayit ama acilis, OFF'ta kayit yok.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_candle_confirmation_engine_v1 import SYMS, _directions, _set_last_bars  # noqa: E402
from test_chart_patterns_v1 import DOUBLE_BOTTOM_PATH  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import (EQUITY, _force_opportunities, _force_triggers,  # noqa: E402
                                          _funnel, _opp, _profile, _risk_log)

BOTTOM = DOUBLE_BOTTOM_PATH[30:]                     # 32 bar, taze BOGA kirilisiyla biter
TOP = [212.0 - x for x in BOTTOM]                    # ayna: taze AYI kirilisi
FILL = 88                                            # tarama penceresi (120) tamamen bizim olsun


def _rows(closes, scale):
    out, prev = [], closes[0] * scale
    for c in closes:
        c *= scale
        out.append((prev, max(prev, c * 1.003), min(prev, c * 0.997), c))
        prev = c
    return out


def _path_for(direction: str, last_close: float):
    body = BOTTOM if direction == "SHORT" else TOP          # adayin yonune KARSI kirilis
    base = body[0]
    filler = [base * (1 + 0.003 * ((i % 4) - 1.5)) for i in range(FILL)]
    closes = filler + body
    return _rows(closes, last_close / body[-1])


def _flat(last_close: float):
    closes = [100.0 * (1 + 0.002 * ((i % 3) - 1)) for i in range(FILL + 32)]
    return _rows(closes, last_close / 100.0)


def _install(eng, monkeypatch, makers):
    orig = eng.runner.run_symbol

    def wrapped(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        mk = makers.get(symbol)
        if mk is not None:
            fr = dict(eng.runner.last_frames[symbol])
            h4 = fr["4h"].copy()
            assert len(h4) >= FILL + 40, "sentetik cerceve tarama penceresinden kisa"
            _set_last_bars(h4, mk(float(h4["close"].iloc[-1])))
            fr["4h"] = h4
            eng.runner.last_frames[symbol] = fr
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", wrapped)


def _two_tours(tmp_path, monkeypatch, mode: str):
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=2, equity=EQUITY)
    _force_opportunities(monkeypatch, {s: _opp(0.80) for s in SYMS})
    _force_triggers(monkeypatch, False)
    es = eng.cfg.v3.entry_selectivity
    es.candle_confirmation_mode = "OFF"                 # yalniz grafik kancasi olculsun
    es.chart_confirmation_mode = "OFF"
    eng.tour(do_scan=False, obsidian=False, charts=False)
    dirs = _directions(eng)
    assert set(dirs) == set(SYMS), dirs
    first, second = SYMS
    _install(eng, monkeypatch, {first: lambda c, d=dirs[first]: _path_for(d, c), second: _flat})
    _force_triggers(monkeypatch, True)
    es.chart_confirmation_mode = mode
    es.chart_confirmation_variant = "p2_4h_veto"
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    opened = [x.split(" ")[0] for x in s["opened"]]
    recs = {e["symbol"]: e for e in _risk_log(eng) if e.get("symbol") in SYMS}
    return eng, opened, recs, first, second


def test_enforce_blocks_the_candidate_facing_an_opposite_breakout(tmp_path, monkeypatch):
    eng, opened, recs, first, second = _two_tours(tmp_path, monkeypatch, "ENFORCE")
    assert opened == [second], opened
    f = _funnel(eng)["run"]
    assert f["chart_blocked"] == 1 and f["trigger_fired"] == 2 and f["opened"] == 1
    r1 = recs[first]
    assert str(r1.get("block_code", "")).startswith("CHART_VETO:P2_OPPOSITE_PATTERN"), r1.get("block_code")
    ch = r1["chart_confirmation"]
    assert ch["mode"] == "ENFORCE" and ch["blocks"] is True and ch["policy_version"] == "chart_v1.0.0"
    assert set(ch["shadow"]) == {"p1_4h_confirm", "p2_4h_veto", "p3_1d_confirm", "p4_1d_veto"}
    assert ch["fresh_4h"] and ch["fresh_4h"][0]["pattern"] in ("DOUBLE_BOTTOM", "DOUBLE_TOP")
    r2 = recs[second]
    assert r2["chart_confirmation"]["blocks"] is False and r2["chart_confirmation"]["fresh_4h"] == []


def test_shadow_records_but_never_blocks(tmp_path, monkeypatch):
    eng, opened, recs, first, second = _two_tours(tmp_path, monkeypatch, "SHADOW")
    assert sorted(opened) == sorted(SYMS), opened
    assert _funnel(eng)["run"]["chart_blocked"] == 0
    ch = recs[first]["chart_confirmation"]
    assert ch["mode"] == "SHADOW" and ch["blocks"] is False
    assert ch["verdict"] == {"ok": False, "reason": "P2_OPPOSITE_PATTERN"}


def test_off_leaves_the_decision_path_untouched(tmp_path, monkeypatch):
    eng, opened, recs, first, second = _two_tours(tmp_path, monkeypatch, "OFF")
    assert sorted(opened) == sorted(SYMS), opened
    assert "chart_confirmation" not in recs[first] and "chart_confirmation" not in recs[second]
