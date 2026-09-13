# -*- coding: utf-8 -*-
"""REJIM KAPISI — rejim tespiti (sutun / hesap / bilinmiyor) ve uc varyantin davranisi."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.regime_gate import DOWN, UP, VARIANTS, btc_regime, ema_last, evaluate_variant  # noqa: E402


def test_regime_uses_the_ema200_column_when_present():
    assert btc_regime([{"close": 101.0, "ema200": 100.0}]) == UP
    assert btc_regime([{"close": 99.0, "ema200": 100.0}]) == DOWN
    assert btc_regime([{"close": 100.0, "ema200": 100.0}]) == DOWN      # esitlik UP degil


def test_regime_is_computed_from_closes_when_column_missing_and_unknown_when_short():
    rising = [{"close": 100.0 + i * 0.5} for i in range(260)]
    assert btc_regime(rising) == UP
    falling = [{"close": 300.0 - i * 0.5} for i in range(260)]
    assert btc_regime(falling) == DOWN
    assert btc_regime([{"close": 100.0} for _ in range(150)]) is None
    assert btc_regime([]) is None and btc_regime([{"close": None}]) is None


def test_ema_last_matches_a_direct_computation():
    vals = [float(i) for i in range(1, 260)]
    e = ema_last(vals, 200)
    k = 2 / 201
    ref = sum(vals[:200]) / 200
    for v in vals[200:]:
        ref = v * k + ref * (1 - k)
    assert e == pytest.approx(ref)


def test_variants():
    assert evaluate_variant("r1_long_only_uptrend", direction="LONG", regime=UP) == (True, "")
    assert evaluate_variant("r1_long_only_uptrend", direction="LONG", regime=DOWN) == (False, "R1_NOT_UPTREND")
    assert evaluate_variant("r1_long_only_uptrend", direction="SHORT", regime=UP) == (False, "R1_SHORT_BLOCKED")
    assert evaluate_variant("r2_no_trade_downtrend", direction="SHORT", regime=UP) == (True, "")
    assert evaluate_variant("r2_no_trade_downtrend", direction="LONG", regime=DOWN) == (False, "R2_DOWNTREND")
    assert evaluate_variant("r3_follow_regime", direction="SHORT", regime=DOWN) == (True, "")
    assert evaluate_variant("r3_follow_regime", direction="SHORT", regime=UP) == (False, "R3_AGAINST_REGIME")
    assert evaluate_variant("r3_follow_regime", direction="LONG", regime=None) == (False, "R3_NO_REGIME")
    assert evaluate_variant("r1_long_only_uptrend", direction="", regime=UP) == (False, "R1_SIDE")
    assert len(VARIANTS) == 3
    with pytest.raises(ValueError):
        evaluate_variant("r9", direction="LONG", regime=UP)
