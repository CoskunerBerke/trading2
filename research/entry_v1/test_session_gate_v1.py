# -*- coding: utf-8 -*-
"""SEANS KAPISI — saat/gun sinirlari ve fail-closed davranisi."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from session_gate import VARIANTS, evaluate_variant, in_us_session, is_weekend  # noqa: E402


def _ms(y, m, d, h):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def test_us_session_bounds_are_half_open():
    assert in_us_session(_ms(2026, 9, 9, 12)) is True        # Carsamba 12:00 UTC
    assert in_us_session(_ms(2026, 9, 9, 16)) is True
    assert in_us_session(_ms(2026, 9, 9, 20)) is False       # 20:00 disarida
    assert in_us_session(_ms(2026, 9, 9, 8)) is False


def test_weekend_is_saturday_and_sunday_utc():
    assert is_weekend(_ms(2026, 9, 12, 12)) is True          # Cumartesi
    assert is_weekend(_ms(2026, 9, 13, 0)) is True           # Pazar
    assert is_weekend(_ms(2026, 9, 14, 0)) is False          # Pazartesi


def test_variants():
    wed_noon, wed_night, sat_noon = _ms(2026, 9, 9, 12), _ms(2026, 9, 9, 0), _ms(2026, 9, 12, 12)
    assert evaluate_variant("s1_us_session", decision_ms=wed_noon) == (True, "")
    assert evaluate_variant("s1_us_session", decision_ms=wed_night) == (False, "S1_OUTSIDE_US_SESSION")
    assert evaluate_variant("s1_us_session", decision_ms=sat_noon) == (True, "")
    assert evaluate_variant("s2_no_weekend", decision_ms=sat_noon) == (False, "S2_WEEKEND")
    assert evaluate_variant("s2_no_weekend", decision_ms=wed_night) == (True, "")
    assert evaluate_variant("s3_both", decision_ms=wed_noon) == (True, "")
    assert evaluate_variant("s3_both", decision_ms=sat_noon) == (False, "S3_WEEKEND")
    assert evaluate_variant("s3_both", decision_ms=wed_night) == (False, "S3_OUTSIDE_US_SESSION")
    assert set(VARIANTS) == {"s1_us_session", "s2_no_weekend", "s3_both"}


def test_unreadable_time_is_fail_closed_and_unknown_variant_raises():
    assert evaluate_variant("s1_us_session", decision_ms=None) == (False, "S1_NO_TIME")
    assert evaluate_variant("s2_no_weekend", decision_ms="x") == (False, "S2_NO_TIME")
    with pytest.raises(ValueError):
        evaluate_variant("s9", decision_ms=0)
