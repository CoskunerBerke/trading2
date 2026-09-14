# -*- coding: utf-8 -*-
"""Kural sözleşmesi: sentetik çerçevelerle deterministik davranış (pytest, bu dizinden)."""
import types

import pandas as pd

import candle_rules as cr


def _df(rows):
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _d(direction):
    return types.SimpleNamespace(direction=direction, regime="TREND_UP")


# C1/C3 en az 3, C2 en az 4 kapanmış bar ister; öndeki bar nötr dolgudur (şekil üretmez).
BEAR_THEN_PIERCING = [(0, 105, 105.5, 103.8, 104, 1),
                      (1, 104, 104.5, 99.5, 100, 1), (2, 100, 103.4, 99.2, 103, 1)]
CONFIRM_BAR = [(3, 103, 104.2, 102.5, 103.8, 1)]
# Üç boğa gövdeli ama kapanışları TEK YÖNLÜ OLMAYAN bar: iki barlı şekil (renk aynı) ve üç
# beyaz asker (kapanışlar yükselmiyor) oluşmaz; son bar topaçtır (tarafsız).
FLAT = [(1, 100.0, 100.8, 99.8, 100.5, 1), (2, 100.0, 100.6, 99.7, 100.3, 1),
        (3, 100.0, 100.9, 99.5, 100.4, 1)]


def test_c1_allows_aligned_and_rejects_opposite_or_none():
    r = cr.build("c1_4h")
    fr = {"4h": _df(BEAR_THEN_PIERCING)}
    assert r("X", _d("LONG"), None, fr) == (True, "")
    assert r("X", _d("SHORT"), None, fr) == (False, "C1_OPPOSITE_PATTERN")
    assert r("X", _d("LONG"), None, {"4h": _df(FLAT)}) == (False, "C1_NO_PATTERN")
    assert r("X", _d("LONG"), None, {}) == (False, "C1_MISSING_BARS")


def test_c2_requires_confirmation_bar_after_pattern():
    r = cr.build("c2_4h_confirm")
    ok = {"4h": _df(BEAR_THEN_PIERCING + CONFIRM_BAR)}
    assert r("X", _d("LONG"), None, ok) == (True, "")
    bad = BEAR_THEN_PIERCING + [(3, 103, 103.2, 101.0, 101.5, 1)]     # teyit barı aşağıda
    assert r("X", _d("LONG"), None, {"4h": _df(bad)}) == (False, "C2_NOT_CONFIRMED")
    assert r("X", _d("SHORT"), None, ok) == (False, "C2_OPPOSITE_PATTERN")


def test_c3_only_vetoes_opposite_shape():
    r = cr.build("c3_4h_veto")
    fr = {"4h": _df(BEAR_THEN_PIERCING)}
    assert r("X", _d("LONG"), None, fr) == (True, "")
    assert r("X", _d("SHORT"), None, fr) == (False, "C3_OPPOSITE_PATTERN")
    assert r("X", _d("SHORT"), None, {"4h": _df(FLAT)}) == (True, "")
    assert r("X", _d("SHORT"), None, {}) == (True, "")


def test_c4_reads_daily_frame_only():
    r = cr.build("c4_1d")
    assert r("X", _d("LONG"), None, {"4h": _df(BEAR_THEN_PIERCING)}) == (False, "C4_MISSING_BARS")
    assert r("X", _d("LONG"), None, {"1d": _df(BEAR_THEN_PIERCING)}) == (True, "")


def test_registry_is_exactly_four_arms():
    assert cr.all_names() == ["c1_4h", "c2_4h_confirm", "c3_4h_veto", "c4_1d"]
