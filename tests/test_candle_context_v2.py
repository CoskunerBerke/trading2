# -*- coding: utf-8 -*-
"""candle_v1.1.0 — harami / delici / kara bulut / cımbız şekilleri ve TEK KAYNAK taraf kümeleri."""
import pytest

from tradingbot.learn.candle_context import (
    BEAR_SIDE_SHAPES, BEARISH_ENGULFING_LIKE, BEARISH_HARAMI_LIKE, BULL_SIDE_SHAPES,
    BULLISH_ENGULFING_LIKE, BULLISH_HARAMI_LIKE, CONFIRMED, DARK_CLOUD_COVER_LIKE,
    PIERCING_LINE_LIKE, TWEEZER_BOTTOM_LIKE, TWEEZER_TOP_LIKE, CandleContextConfig,
    build_candle_context)
from tradingbot.learn.entry_challenger_v2 import candle_confidence_delta

CFG = CandleContextConfig()


def _bar(ts, o, h, lo, c):
    return {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def _shapes(bars):
    return build_candle_context(bars=bars, atr=1.0, cfg=CFG)["pattern_shapes"]


def test_policy_version_bumped_and_config_validates():
    assert CFG.policy_version == "candle_v1.1.0"
    with pytest.raises(ValueError):
        CandleContextConfig(tweezer_tolerance_ratio=0.0).validate()
    with pytest.raises(ValueError):
        CandleContextConfig(tweezer_tolerance_ratio=0.5).validate()


def test_bullish_harami_inside_previous_bearish_body():
    s = _shapes([_bar(1, 104, 104.5, 99.5, 100), _bar(2, 101, 102.6, 100.4, 102)])
    assert BULLISH_HARAMI_LIKE in s and BEARISH_HARAMI_LIKE not in s
    assert BULLISH_ENGULFING_LIKE not in s


def test_bearish_harami_inside_previous_bullish_body():
    s = _shapes([_bar(1, 100, 104.5, 99.5, 104), _bar(2, 103, 103.4, 101.5, 102)])
    assert BEARISH_HARAMI_LIKE in s and BULLISH_HARAMI_LIKE not in s


def test_harami_requires_smaller_body_strictly_inside():
    # Gövde önceki gövdeden büyük → içeri bar değil (bu bir yutan formasyondur).
    s = _shapes([_bar(1, 102, 102.5, 100.5, 100.8), _bar(2, 100.3, 103.5, 100.0, 103.0)])
    assert BULLISH_HARAMI_LIKE not in s and BULLISH_ENGULFING_LIKE in s


def test_piercing_line_closes_above_midpoint_below_previous_open():
    # önceki: 104→100 (orta 102). şimdiki: 100'den açılır, 103'te kapanır.
    s = _shapes([_bar(1, 104, 104.5, 99.5, 100), _bar(2, 100, 103.4, 99.2, 103)])
    assert PIERCING_LINE_LIKE in s and BULLISH_ENGULFING_LIKE not in s
    # orta noktanın altında kapanış → delici değil
    s2 = _shapes([_bar(1, 104, 104.5, 99.5, 100), _bar(2, 100, 102.4, 99.2, 101.5)])
    assert PIERCING_LINE_LIKE not in s2


def test_dark_cloud_cover_closes_below_midpoint_above_previous_open():
    s = _shapes([_bar(1, 100, 104.5, 99.5, 104), _bar(2, 104, 104.8, 100.6, 101)])
    assert DARK_CLOUD_COVER_LIKE in s and BEARISH_ENGULFING_LIKE not in s
    s2 = _shapes([_bar(1, 100, 104.5, 99.5, 104), _bar(2, 104, 104.8, 100.6, 103)])
    assert DARK_CLOUD_COVER_LIKE not in s2


def test_tweezer_bottom_and_top_within_tolerance():
    # eş dip: 99.50 vs 99.52, aralık ~5 → tolerans 0.5
    s = _shapes([_bar(1, 104, 104.5, 99.50, 100.5), _bar(2, 100.5, 104.0, 99.52, 103.5)])
    assert TWEEZER_BOTTOM_LIKE in s
    s2 = _shapes([_bar(1, 100, 104.50, 99.5, 103.5), _bar(2, 103.5, 104.45, 100.0, 100.5)])
    assert TWEEZER_TOP_LIKE in s2
    # dipler bir aralık kadar uzak → cımbız değil
    s3 = _shapes([_bar(1, 104, 104.5, 99.5, 100.5), _bar(2, 100.5, 104.0, 101.5, 103.5)])
    assert TWEEZER_BOTTOM_LIKE not in s3


def test_side_sets_are_single_source_and_disjoint():
    assert not (BULL_SIDE_SHAPES & BEAR_SIDE_SHAPES)
    assert {BULLISH_HARAMI_LIKE, PIERCING_LINE_LIKE, TWEEZER_BOTTOM_LIKE} <= BULL_SIDE_SHAPES
    assert {BEARISH_HARAMI_LIKE, DARK_CLOUD_COVER_LIKE, TWEEZER_TOP_LIKE} <= BEAR_SIDE_SHAPES


def test_new_shapes_flow_into_confirmation_and_challenger_delta():
    # Delici çizgi 2. barda oluşur, 3. bar (kapanmış) formasyon kapanışının üstünde → CONFIRMED.
    bars = [_bar(1, 104, 104.5, 99.5, 100), _bar(2, 100, 103.4, 99.2, 103),
            _bar(3, 103, 104.2, 102.5, 103.8)]
    ctx = build_candle_context(bars=bars, atr=1.0, cfg=CFG)
    assert PIERCING_LINE_LIKE in ctx["confirmed_pattern_shapes"]
    assert ctx["confirmation_state"] == CONFIRMED
    d = candle_confidence_delta(ctx, is_long=True)
    assert d["applied"] and d["delta"] == pytest.approx(0.10)
    d2 = candle_confidence_delta(ctx, is_long=False)
    assert d2["applied"] and d2["delta"] == pytest.approx(-0.10)


def test_missing_ohlc_never_raises_and_yields_no_two_bar_shape():
    s = _shapes([{"timestamp": 1, "open": None, "high": 1, "low": 1, "close": 1},
                 _bar(2, 100, 103.4, 99.2, 103)])
    assert not ({BULLISH_HARAMI_LIKE, PIERCING_LINE_LIKE, TWEEZER_BOTTOM_LIKE} & set(s))
