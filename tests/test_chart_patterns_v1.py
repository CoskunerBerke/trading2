# -*- coding: utf-8 -*-
"""GRAFIK FORMASYONLARI — sentetik seriler: her formasyon icin teyitli tespit, gelecege
bakmama (kirilis olmadan CONFIRMED yok), tazelik ve varyant davranisi."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.chart_confirmation import VARIANTS, chart_confirmation, evaluate_variant  # noqa: E402
from tradingbot.chart_patterns import (ASCENDING_TRIANGLE, BEAR, BEAR_FLAG, BULL, BULL_FLAG,  # noqa: E402
                                       DESCENDING_TRIANGLE, DOUBLE_BOTTOM, DOUBLE_TOP,
                                       HEAD_AND_SHOULDERS, INVERSE_HEAD_AND_SHOULDERS, TRIPLE_TOP,
                                       ChartPatternConfig, detect_chart_patterns)

H4 = 4 * 3_600_000
CFG = ChartPatternConfig()


def _bars(closes, *, wick=0.3):
    """OHLC tutarli sentetik bar. Fitil KAPANISA gore konur: ardisik barlar ayni ucu
    paylasmaz (fraktal salinim tekil ekstremum ister)."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append({"timestamp": i * H4, "open": o, "high": max(o, c + wick),
                    "low": min(o, c - wick), "close": c})
        prev = c
    return out


def _ramp(a, b, n):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _names(det):
    return {p["pattern"] for p in det["patterns"]}


def _pad(closes, n=30, level=None):
    """Onune notr, hafif dalgali dolgu koyar (yeterli bar + gercekci salinimlar)."""
    base = level if level is not None else closes[0]
    filler = [base * (1 + 0.004 * ((i % 4) - 1.5)) for i in range(n)]
    return filler + list(closes)


# ------------------------------------------------------------------ cift dip / cift tepe
DOUBLE_BOTTOM_PATH = _pad(_ramp(112, 100, 10) + _ramp(100.6, 110, 9) + _ramp(109.4, 100.4, 9)
                          + _ramp(101, 109.5, 8) + [110.8, 111.5])


def test_double_bottom_confirmed_only_after_neckline_break():
    det = detect_chart_patterns(_bars(DOUBLE_BOTTOM_PATH), CFG)
    assert DOUBLE_BOTTOM in _names(det)
    p = [x for x in det["patterns"] if x["pattern"] == DOUBLE_BOTTOM][0]
    assert p["side"] == BULL and p["state"] == "CONFIRMED" and p["bars_since"] <= 1
    # kirilis barlari kesilirse formasyon RAPORLANMAZ (gelecege bakma yok)
    cut = detect_chart_patterns(_bars(DOUBLE_BOTTOM_PATH[:-2]), CFG)
    assert DOUBLE_BOTTOM not in _names(cut)


def test_double_top_is_the_mirror():
    path = _pad([200 - (x - 100) for x in DOUBLE_BOTTOM_PATH[30:]], level=88)
    det = detect_chart_patterns(_bars(path), CFG)
    assert DOUBLE_TOP in _names(det)
    assert all(p["side"] == BEAR for p in det["patterns"] if p["pattern"] == DOUBLE_TOP)


# ------------------------------------------------------------------ uclu tepe
def test_triple_top_needs_three_equal_highs_and_a_break():
    seg = (_ramp(96, 108, 7) + _ramp(107.3, 100, 6) + _ramp(100.6, 108.4, 7) + _ramp(107.5, 100.3, 6)
           + _ramp(101, 107.9, 7) + _ramp(107, 100.5, 6) + [99.2, 98.5])
    det = detect_chart_patterns(_bars(_pad(seg, level=96)), CFG)
    assert TRIPLE_TOP in _names(det)
    assert all(p["side"] == BEAR for p in det["patterns"] if p["pattern"] == TRIPLE_TOP)


# ------------------------------------------------------------------ omuz bas omuz
HS_PATH = _pad(_ramp(96, 105, 8) + _ramp(104.2, 101.5, 5) + _ramp(102.4, 111, 8) + _ramp(110, 102, 7)
               + _ramp(102.9, 105.3, 5) + _ramp(104.4, 102.6, 5) + [101.2, 100.4], level=95)


def test_head_and_shoulders_breaks_the_sloped_neckline():
    det = detect_chart_patterns(_bars(HS_PATH), CFG)
    assert HEAD_AND_SHOULDERS in _names(det)
    p = [x for x in det["patterns"] if x["pattern"] == HEAD_AND_SHOULDERS][0]
    assert p["side"] == BEAR and p["bars_since"] <= 1
    cut = detect_chart_patterns(_bars(HS_PATH[:-2]), CFG)
    assert HEAD_AND_SHOULDERS not in _names(cut)


def test_inverse_head_and_shoulders_is_the_mirror():
    path = _pad([210 - x for x in HS_PATH[30:]], level=115)
    det = detect_chart_patterns(_bars(path), CFG)
    assert INVERSE_HEAD_AND_SHOULDERS in _names(det)
    assert all(p["side"] == BULL for p in det["patterns"] if p["pattern"] == INVERSE_HEAD_AND_SHOULDERS)


# ------------------------------------------------------------------ ucgen: yon KIRILISTAN gelir
def _desc_triangle_body():
    return (_ramp(103, 100, 4) + _ramp(100.7, 108, 6) + _ramp(107.2, 100.2, 6) + _ramp(100.8, 105, 6)
            + _ramp(104.3, 100.3, 6) + _ramp(100.9, 103, 4))


def test_descending_triangle_breaking_down_is_bearish():
    path = _pad(_desc_triangle_body() + [101.5, 99.0, 98.2], level=103)
    det = detect_chart_patterns(_bars(path), CFG)
    hits = [p for p in det["patterns"] if p["pattern"] == DESCENDING_TRIANGLE]
    assert hits and hits[0]["side"] == BEAR


def test_descending_triangle_breaking_up_is_bullish():
    path = _pad(_desc_triangle_body() + [103.6, 105.2, 106.5], level=103)
    det = detect_chart_patterns(_bars(path), CFG)
    hits = [p for p in det["patterns"] if p["pattern"] == DESCENDING_TRIANGLE]
    assert hits and hits[0]["side"] == BULL


def test_ascending_triangle_is_the_mirror():
    body = [206 - x for x in _desc_triangle_body()]
    path = _pad(body + [104.6, 107.0, 108.1], level=103)
    det = detect_chart_patterns(_bars(path), CFG)
    hits = [p for p in det["patterns"] if p["pattern"] == ASCENDING_TRIANGLE]
    assert hits and hits[0]["side"] == BULL


# ------------------------------------------------------------------ bayrak
BULL_FLAG_PATH = _pad(_ramp(100, 110, 9) + [109.2, 108.6, 108.9, 108.1, 107.8, 108.0] + [110.3, 111.0], level=100)


def test_bull_flag_pole_consolidation_break():
    det = detect_chart_patterns(_bars(BULL_FLAG_PATH), CFG)
    hits = [p for p in det["patterns"] if p["pattern"] == BULL_FLAG]
    assert hits and hits[0]["side"] == BULL and hits[0]["bars_since"] <= 1
    cut = detect_chart_patterns(_bars(BULL_FLAG_PATH[:-2]), CFG)
    assert BULL_FLAG not in _names(cut)


def test_bear_flag_is_the_mirror():
    path = _pad([220 - x for x in BULL_FLAG_PATH[30:]], level=120)
    det = detect_chart_patterns(_bars(path), CFG)
    hits = [p for p in det["patterns"] if p["pattern"] == BEAR_FLAG]
    assert hits and hits[0]["side"] == BEAR


# ------------------------------------------------------------------ genel sozlesmeler
def test_flat_series_has_no_pattern_and_short_series_says_so():
    flat = _bars([100 + 0.2 * ((i % 3) - 1) for i in range(80)])
    assert detect_chart_patterns(flat, CFG)["patterns"] == []
    short = detect_chart_patterns(_bars([100, 101, 102]), CFG)
    assert short["patterns"] == [] and short.get("reason") == "NOT_ENOUGH_BARS"


def test_config_validates_and_is_versioned():
    assert CFG.policy_version == "chart_v1.0.0" and CFG.config_id
    with pytest.raises(ValueError):
        ChartPatternConfig(flag_max_retrace=1.5).validate()
    with pytest.raises(ValueError):
        ChartPatternConfig(max_pattern_bars=3).validate()


# ------------------------------------------------------------------ varyantlar
def test_variants_confirm_veto_and_freshness():
    bars = _bars(DOUBLE_BOTTOM_PATH)                       # taze BOGA kirilisi
    assert evaluate_variant("p1_4h_confirm", direction="LONG", bars_4h=bars, bars_1d=None) == (True, "")
    assert evaluate_variant("p1_4h_confirm", direction="SHORT", bars_4h=bars, bars_1d=None) == (False, "P1_OPPOSITE_PATTERN")
    assert evaluate_variant("p2_4h_veto", direction="SHORT", bars_4h=bars, bars_1d=None) == (False, "P2_OPPOSITE_PATTERN")
    assert evaluate_variant("p2_4h_veto", direction="LONG", bars_4h=bars, bars_1d=None) == (True, "")
    stale = _bars(DOUBLE_BOTTOM_PATH + [111.2 + 0.1 * (i % 2) for i in range(8)])   # kirilis 8 bar eski
    assert evaluate_variant("p1_4h_confirm", direction="LONG", bars_4h=stale, bars_1d=None) == (False, "P1_NO_PATTERN")
    assert evaluate_variant("p2_4h_veto", direction="SHORT", bars_4h=stale, bars_1d=None) == (True, "")
    assert evaluate_variant("p3_1d_confirm", direction="LONG", bars_4h=bars, bars_1d=None) == (False, "P3_MISSING_BARS")
    assert evaluate_variant("p4_1d_veto", direction="LONG", bars_4h=bars, bars_1d=None) == (True, "")
    assert evaluate_variant("p1_4h_confirm", direction="", bars_4h=bars, bars_1d=None) == (False, "P1_SIDE")
    with pytest.raises(ValueError):
        evaluate_variant("p9", direction="LONG", bars_4h=bars, bars_1d=None)


def test_record_blocks_only_in_enforce_and_lists_fresh_patterns():
    bars = _bars(DOUBLE_BOTTOM_PATH)
    r = chart_confirmation(mode="ENFORCE", variant="p2_4h_veto", direction="SHORT", bars_4h=bars, bars_1d=None)
    assert r["blocks"] and r["verdict"]["reason"] == "P2_OPPOSITE_PATTERN"
    assert set(r["shadow"]) == set(VARIANTS)
    assert any(p["pattern"] == DOUBLE_BOTTOM for p in r["fresh_4h"])
    s = chart_confirmation(mode="SHADOW", variant="p2_4h_veto", direction="SHORT", bars_4h=bars, bars_1d=None)
    assert not s["blocks"] and s["verdict"]["ok"] is False
    o = chart_confirmation(mode="OFF", variant="p2_4h_veto", direction="SHORT", bars_4h=bars, bars_1d=None)
    assert not o["blocks"] and o["shadow"] == {}
