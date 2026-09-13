# -*- coding: utf-8 -*-
"""candle_v1.2.0 — Türkçe tablodaki ek varyantlar. Canlı veto kümeleri DEĞİŞMEDİ (ayrıca sınanır)."""
import pytest

from tradingbot.learn.candle_context import (
    BEAR_SIDE_SHAPES, BEAR_SIDE_SHAPES_EXT, BEARISH_ABANDONED_BABY_LIKE, BEARISH_BELT_HOLD_LIKE,
    BEARISH_DOJI_STAR_LIKE, BEARISH_HARAMI_CROSS_LIKE, BEARISH_KICKER_LIKE,
    BEARISH_MEETING_LINES_LIKE, BULL_SIDE_SHAPES, BULL_SIDE_SHAPES_EXT, BULLISH_ABANDONED_BABY_LIKE,
    BULLISH_BELT_HOLD_LIKE, BULLISH_DOJI_STAR_LIKE, BULLISH_HARAMI_CROSS_LIKE, BULLISH_KICKER_LIKE,
    BULLISH_MEETING_LINES_LIKE, DESCENDING_HAWK_LIKE, EVENING_DOJI_STAR_LIKE, HOMING_PIGEON_LIKE,
    MORNING_DOJI_STAR_LIKE, TRI_STAR_LIKE, CandleContextConfig, build_candle_context)

CFG = CandleContextConfig()


def _bar(ts, o, h, lo, c):
    return {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def _shapes(bars):
    return build_candle_context(bars=bars, atr=1.0, cfg=CFG)["pattern_shapes"]


def test_version_and_live_veto_sets_unchanged():
    assert CFG.policy_version == "candle_v1.2.0"
    assert len(BULL_SIDE_SHAPES) == 8 and len(BEAR_SIDE_SHAPES) == 6          # v1.1.0 ile aynı
    assert BULL_SIDE_SHAPES < BULL_SIDE_SHAPES_EXT and BEAR_SIDE_SHAPES < BEAR_SIDE_SHAPES_EXT
    assert not (BULL_SIDE_SHAPES_EXT & BEAR_SIDE_SHAPES_EXT)
    assert TRI_STAR_LIKE not in BULL_SIDE_SHAPES_EXT | BEAR_SIDE_SHAPES_EXT


def test_belt_hold_is_a_wickless_long_body():
    assert BULLISH_BELT_HOLD_LIKE in _shapes([_bar(1, 100.0, 104.2, 100.0, 104.0)])      # alt fitil yok
    assert BEARISH_BELT_HOLD_LIKE in _shapes([_bar(1, 104.0, 104.0, 99.8, 100.0)])       # üst fitil yok
    assert BULLISH_BELT_HOLD_LIKE not in _shapes([_bar(1, 100.0, 104.2, 98.0, 104.0)])   # uzun alt fitil


def test_harami_cross_homing_pigeon_descending_hawk():
    # kros hamile boğa: ayı gövde, içeride doji
    s = _shapes([_bar(1, 104, 104.5, 99.5, 100), _bar(2, 102, 102.8, 101.2, 102.05)])
    assert BULLISH_HARAMI_CROSS_LIKE in s
    s = _shapes([_bar(1, 100, 104.5, 99.5, 104), _bar(2, 102, 102.8, 101.2, 101.95)])
    assert BEARISH_HARAMI_CROSS_LIKE in s
    # güvercin yuvası: iki ayı, ikincisi içeride
    s = _shapes([_bar(1, 104, 104.5, 99.5, 100), _bar(2, 102.5, 102.8, 100.9, 101.2)])
    assert HOMING_PIGEON_LIKE in s and DESCENDING_HAWK_LIKE not in s
    # inen şahin: iki boğa, ikincisi içeride
    s = _shapes([_bar(1, 100, 104.5, 99.5, 104), _bar(2, 101.2, 103.2, 100.9, 102.5)])
    assert DESCENDING_HAWK_LIKE in s and HOMING_PIGEON_LIKE not in s


def test_meeting_lines_share_the_close():
    s = _shapes([_bar(1, 104, 104.5, 99.5, 100), _bar(2, 97.0, 100.3, 96.5, 100.05)])
    assert BULLISH_MEETING_LINES_LIKE in s
    s = _shapes([_bar(1, 100, 104.5, 99.5, 104), _bar(2, 107.0, 107.5, 103.7, 103.95)])
    assert BEARISH_MEETING_LINES_LIKE in s


def test_kicker_needs_two_marubozu_in_opposite_directions():
    s = _shapes([_bar(1, 104, 104.1, 99.9, 100), _bar(2, 104.2, 108.5, 104.1, 108.4)])
    assert BULLISH_KICKER_LIKE in s
    s = _shapes([_bar(1, 100, 104.1, 99.9, 104), _bar(2, 99.8, 99.9, 95.5, 95.6)])
    assert BEARISH_KICKER_LIKE in s
    s = _shapes([_bar(1, 104, 106.0, 98.0, 100), _bar(2, 104.2, 108.5, 104.1, 108.4)])   # ilk bar marubozu değil
    assert BULLISH_KICKER_LIKE not in s


def test_doji_star_and_doji_star_variants_of_the_star_patterns():
    s = _shapes([_bar(1, 105, 105.2, 99.8, 100), _bar(2, 99.5, 100.4, 98.6, 99.52)])
    assert BULLISH_DOJI_STAR_LIKE in s
    s = _shapes([_bar(1, 100, 105.2, 99.8, 105), _bar(2, 105.5, 106.4, 104.6, 105.48)])
    assert BEARISH_DOJI_STAR_LIKE in s
    morning = [_bar(1, 105, 105.2, 99.8, 100), _bar(2, 99.5, 100.4, 98.6, 99.52), _bar(3, 100, 104.3, 99.7, 104)]
    s = _shapes(morning)
    assert MORNING_DOJI_STAR_LIKE in s
    evening = [_bar(1, 100, 105.2, 99.8, 105), _bar(2, 105.5, 106.4, 104.6, 105.48), _bar(3, 105, 105.3, 100.7, 101)]
    assert EVENING_DOJI_STAR_LIKE in _shapes(evening)


def test_abandoned_baby_requires_the_doji_fully_outside_and_tri_star_three_dojis():
    baby = [_bar(1, 105, 105.2, 99.8, 100), _bar(2, 98.5, 99.4, 97.6, 98.52), _bar(3, 100.2, 104.3, 99.9, 104)]
    assert BULLISH_ABANDONED_BABY_LIKE in _shapes(baby)
    baby_b = [_bar(1, 100, 105.2, 99.8, 105), _bar(2, 106.5, 107.4, 105.6, 106.48), _bar(3, 104.8, 105.1, 100.7, 101)]
    assert BEARISH_ABANDONED_BABY_LIKE in _shapes(baby_b)
    not_baby = [_bar(1, 105, 105.2, 99.8, 100), _bar(2, 99.9, 100.8, 99.0, 99.92), _bar(3, 100.2, 104.3, 99.9, 104)]
    assert BULLISH_ABANDONED_BABY_LIKE not in _shapes(not_baby)
    tri = [_bar(1, 100, 100.6, 99.4, 100.02), _bar(2, 100.1, 100.7, 99.5, 100.08), _bar(3, 100.0, 100.5, 99.6, 100.03)]
    assert TRI_STAR_LIKE in _shapes(tri)


def test_config_validation_still_holds():
    with pytest.raises(ValueError):
        CandleContextConfig(tweezer_tolerance_ratio=0.0).validate()
    CandleContextConfig().validate()
