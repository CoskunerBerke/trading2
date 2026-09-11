# -*- coding: utf-8 -*-
"""Aday C1 kaldiraci: hedef mesafesi = k x stop mesafesi.

Sozlesme: `target_r_multiple is None` iken plan HIC degismez (uretim bit-ayni). Sayi
verildiginde YALNIZ hedefler degisir; giris, stop, yon, kaldirac ve risk DEGISMEZ.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.coinhead.head import CoinHead, CoinHeadConfig      # noqa: E402
from tradingbot.coinhead.schema import PlanSize, TradePlanV3       # noqa: E402


def _plan(direction="LONG", stop=95.0, targets=(110.0, 115.0)):
    return TradePlanV3(market_type="futures", direction=direction, entry_type="pullback",
                       entry_trigger="", entry_zone=(99.0, 101.0), invalidation="",
                       stop=stop, targets=list(targets), time_horizon_bars=24,
                       size=PlanSize(0.0, "NOTIONAL", 3))


def _head(**kw):
    h = CoinHead.__new__(CoinHead)
    h.cfg = CoinHeadConfig(**kw)
    return h


def test_default_leaves_the_plan_untouched():
    p = _plan()
    _head()._retarget(p)
    assert p.targets == [110.0, 115.0] and p.stop == 95.0


def test_targets_are_rebuilt_from_the_stop_distance():
    p = _plan()                                        # giris 100, stop 95 -> mesafe 5
    _head(target_r_multiple=1.0, target2_r_multiple=1.5)._retarget(p)
    assert p.targets == [105.0, 107.5]
    assert p.stop == 95.0 and p.entry == 100.0 and p.size.leverage == 3


def test_short_side_mirrors_the_distance():
    p = _plan(direction="SHORT", stop=105.0, targets=(90.0, 85.0))
    _head(target_r_multiple=2.0, target2_r_multiple=3.0)._retarget(p)
    assert p.targets == [90.0, 85.0]                   # 100 - 2*5, 100 - 3*5
    assert p.stop == 105.0


def test_single_target_when_second_multiple_is_absent():
    p = _plan()
    _head(target_r_multiple=0.75)._retarget(p)
    assert p.targets == [103.75]


def test_break_even_hit_rate_moves_with_k():
    """Kaldiracin AMACI budur: gereken isabet orani k ile belirlenir."""
    loss_r = 1.0
    needed = {k: loss_r / (loss_r + k) for k in (0.75, 1.0, 1.5, 2.0, 3.0)}
    assert needed[2.0] > needed[3.0] and needed[0.75] > needed[2.0]
    assert abs(needed[2.0] - 1 / 3) < 1e-9


def test_zero_stop_distance_is_left_alone():
    p = _plan(stop=100.0)                              # mesafe 0 -> ZERO_STOP_DISTANCE yolu
    _head(target_r_multiple=2.0)._retarget(p)
    assert p.targets == [110.0, 115.0]


def test_cost_and_r_follows_the_new_geometry():
    p = _plan()
    h = _head(target_r_multiple=1.0, fee_taker_pct=0.05, slippage_pct=0.03)
    h._retarget(p)
    h._cost_and_r(p, "futures", None, None)
    assert p.valid
    assert abs(p.expected_r - (5.0 - 0.16) / 5.0) < 1e-9        # ~0.968, 1.96 DEGIL
