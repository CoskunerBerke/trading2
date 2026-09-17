# -*- coding: utf-8 -*-
"""BOX THEORY V15 — kural sözleşmesi.

Kural videodan birebir çıkarıldı; videoda OLMAYAN her şey (çıkış, "yakın" eşiği, long stop okuması)
parametredir ve burada İKİ okuma da sınanır. Testler kuralın kendi iddiasını ayırt etmeli: bir parametre
değiştiğinde kararın DEĞİŞTİĞİ gösterilir (geri alma sondası mantığı), yoksa test kusuru değil kuralı ölçer.
"""
from __future__ import annotations

import copy

import pytest

from tradingbot.box_theory import (
    DEFAULT_PARAMS,
    MIN_M5_BARS,
    BoxParams,
    decide,
    location,
    read_box,
    read_intraday,
    rule_state,
    sweep_params,
)
from tradingbot.timeframes import DAY_MS, TF_MS

D0 = 1_767_225_600_000        # 2026-01-01T00:00:00Z, gün başlangıcı
M5 = TF_MS["5m"]


def _daily(high: float, low: float, ts: int = D0 - DAY_MS, n: int = 3) -> list[dict]:
    """Son bar = ÖNCEKİ GÜN (kutu). Öncesi dolgu: kural yalnız sonuncuyu okur."""
    rows = [{"timestamp": ts - (n - 1 - i) * DAY_MS, "open": low, "high": high, "low": low, "close": high}
            for i in range(n)]
    return rows


def _bar(ts: int, o: float, h: float, lo: float, c: float) -> dict:
    return {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c}


def _m5(bars: list[tuple], start_ts: int = D0) -> list[dict]:
    return [_bar(start_ts + i * M5, *b) for i, b in enumerate(bars)]


# ------------------------------------------------------------------ kutu okuma
def test_box_is_the_previous_days_high_and_low():
    assert read_box(_daily(110.0, 90.0)) == (110.0, 90.0)


@pytest.mark.parametrize("rows", [
    [],                                                        # bar yok
    [{"timestamp": D0, "high": 110.0, "low": 90.0}],           # tek bar: MIN_DAILY_BARS altı
    _daily(90.0, 110.0),                                       # high <= low
    [{"timestamp": D0, "high": None, "low": 90.0}] * 3,        # okunamaz
    [{"timestamp": D0, "high": 110.0, "low": -1.0}] * 3,       # pozitif değil
])
def test_box_is_fail_closed_on_unusable_daily_data(rows):
    assert read_box(rows) is None


def test_decide_returns_none_when_the_box_cannot_be_read():
    assert decide(daily_rows=[], m5_rows=_m5([(100, 101, 99, 100)] * 3)) is None


def test_decide_returns_none_with_too_few_five_minute_bars():
    bars = _m5([(100, 101, 99, 100)] * (MIN_M5_BARS - 1))
    assert decide(daily_rows=_daily(110.0, 90.0), m5_rows=bars) is None


# ------------------------------------------------------------------ konum
@pytest.mark.parametrize("price,want", [
    (110.0, "TOP"), (109.0, "TOP"), (108.1, "TOP"),     # üst %10 bandı: 108 ve üstü
    (100.0, "MIDDLE"), (104.0, "MIDDLE"), (105.0, "MIDDLE"),
    (91.9, "BOTTOM"), (90.0, "BOTTOM"),
])
def test_location_reads_the_near_band_as_a_fraction_of_box_height(price, want):
    assert location(price, 110.0, 90.0, near_frac=0.10, allow_outside=True) == want


def test_price_beyond_an_edge_counts_as_that_edge_only_when_outside_is_allowed():
    assert location(115.0, 110.0, 90.0, near_frac=0.10, allow_outside=True) == "TOP"
    assert location(115.0, 110.0, 90.0, near_frac=0.10, allow_outside=False) == "OUTSIDE"
    assert location(85.0, 110.0, 90.0, near_frac=0.10, allow_outside=False) == "OUTSIDE"


def test_a_wider_near_band_pulls_more_prices_to_the_edge():
    assert location(105.0, 110.0, 90.0, near_frac=0.10, allow_outside=True) == "MIDDLE"
    assert location(105.0, 110.0, 90.0, near_frac=0.30, allow_outside=True) == "TOP"


def test_touching_both_edges_in_the_location_window_yields_no_trade():
    """Aynı pencerede hem tepeye hem dibe değen fiyatın YÖNÜ yoktur; kural işlem üretmemeli."""
    bars = _m5([(100, 101, 99, 100), (100, 110.0, 90.0, 100), (100, 100.5, 99.5, 99.0)])
    assert decide(daily_rows=_daily(110.0, 90.0), m5_rows=bars,
                  params=BoxParams(loc_lookback=2)) is None


# ------------------------------------------------------------------ tetik + yön
def _short_setup(trigger_close: float = 107.0) -> list[dict]:
    """Tepeye değmiş fiyat, ardından önceki mumun altına inen KIRMIZI mum."""
    return _m5([(100, 101, 99, 100),
                (108, 110, 107.5, 109.0),                       # tepeye değdi (high 110 = kutu tepesi)
                (109.0, 109.2, trigger_close, trigger_close)])  # kırmızı, prev.low 107.5'in altına kapandı


def test_price_at_the_top_with_a_red_candle_breaking_the_previous_low_opens_a_short():
    act = decide(daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup())
    assert act is not None and act["action"] == "OPEN" and act["direction"] == "SHORT"
    assert act["location"] == "TOP" and act["reason"] == "BOX_FADE_TOP"
    assert act["stop"] == pytest.approx(110.0), "stop TETİK mumunun değil, ÖNCEKİ mumun tepesi olmalı"
    assert act["signal_close"] == pytest.approx(107.0)


def test_a_red_candle_that_does_not_break_the_previous_low_does_not_trigger_break_prev():
    """Tetik ayırt edici olmalı: yalnız 'kırmızı' yetmez, önceki mumun altına İNMELİ."""
    bars = _m5([(100, 101, 99, 100), (108, 110, 107.5, 109.0), (109.0, 109.2, 108.0, 108.2)])
    assert decide(daily_rows=_daily(110.0, 90.0), m5_rows=bars) is None
    act = decide(daily_rows=_daily(110.0, 90.0), m5_rows=bars, params=BoxParams(trigger="color_only"))
    assert act is not None and act["direction"] == "SHORT", "color_only okumasında aynı mum TETİKLER"


def test_a_green_candle_at_the_top_never_opens_a_short():
    bars = _m5([(100, 101, 99, 100), (108, 110, 107.5, 109.0), (107.0, 109.5, 107.0, 109.4)])
    assert decide(daily_rows=_daily(110.0, 90.0), m5_rows=bars) is None


def _long_setup() -> list[dict]:
    return _m5([(100, 101, 99, 100),
                (92, 92.5, 90.0, 90.5),                 # dibe değdi (low 90 = kutu dibi)
                (90.5, 93.0, 90.4, 92.8)])              # yeşil, prev.high 92.5'in üstüne kapandı


def test_price_at_the_bottom_with_a_green_candle_opens_a_long_and_stops_below_the_day_low():
    act = decide(daily_rows=_daily(110.0, 90.0), m5_rows=_long_setup())
    assert act is not None and act["direction"] == "LONG" and act["location"] == "BOTTOM"
    assert act["stop"] == pytest.approx(90.0), "video birebir: stop GÜNÜN en düşüğünün altı"
    assert act["day_low"] == pytest.approx(90.0)


def test_the_symmetric_long_stop_reading_is_a_different_measurable_arm():
    """Videodaki asimetri (short: bir mum, long: günün dibi) VARSAYILMAZ; iki okuma da ölçülür."""
    kw = dict(daily_rows=_daily(110.0, 90.0), m5_rows=_long_setup())
    assert decide(**kw, params=BoxParams(long_stop="day_low"))["stop"] == pytest.approx(90.0)
    assert decide(**kw, params=BoxParams(long_stop="prev_candle"))["stop"] == pytest.approx(90.0)
    # dibi gün içinde DAHA aşağıda olan bir seride iki okuma ayrışır:
    bars = _m5([(95, 95.5, 88.0, 89.0), (92, 92.5, 90.0, 90.5), (90.5, 93.0, 90.4, 92.8)])
    a = decide(daily_rows=_daily(110.0, 90.0), m5_rows=bars, params=BoxParams(long_stop="day_low"))
    b = decide(daily_rows=_daily(110.0, 90.0), m5_rows=bars, params=BoxParams(long_stop="prev_candle"))
    assert a["stop"] == pytest.approx(88.0) and b["stop"] == pytest.approx(90.0)
    assert a["stop"] < b["stop"], "günün dibi okuması DAHA GENİŞ stop verir (risk farkı ölçülebilir)"


def test_middle_of_the_box_produces_no_trade_in_either_direction():
    bars = _m5([(100, 101, 99, 100), (100, 100.5, 99.5, 100.2), (100.2, 100.3, 99.0, 99.1)])
    assert decide(daily_rows=_daily(110.0, 90.0), m5_rows=bars) is None
    st = rule_state(daily_rows=_daily(110.0, 90.0), m5_rows=bars)
    assert st["ok"] and st["location"] == "MIDDLE" and st["reason"] == "MIDDLE"


def test_disabling_a_side_removes_exactly_that_side():
    kw = dict(daily_rows=_daily(110.0, 90.0))
    assert decide(**kw, m5_rows=_short_setup(), params=BoxParams(allow_short=False)) is None
    assert decide(**kw, m5_rows=_short_setup(), params=BoxParams(allow_long=False))["direction"] == "SHORT"
    assert decide(**kw, m5_rows=_long_setup(), params=BoxParams(allow_long=False)) is None


# ------------------------------------------------------------------ çıkış (videoda YOK → süpürme)
def test_exit_kinds_produce_different_targets_from_the_same_entry():
    kw = dict(daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup())
    opp = decide(**kw, params=BoxParams(exit_kind="box_opposite"))
    mid = decide(**kw, params=BoxParams(exit_kind="box_mid"))
    r2 = decide(**kw, params=BoxParams(exit_kind="r_multiple", exit_r=2.0))
    none = decide(**kw, params=BoxParams(exit_kind="none"))
    assert opp["targets"] == [pytest.approx(90.0)]
    assert mid["targets"] == [pytest.approx(100.0)]
    # R = |giriş 107.0 − stop 110.0| = 3.0 → hedef 107.0 − 6.0 = 101.0
    assert r2["targets"] == [pytest.approx(101.0)]
    assert none["targets"] == []
    assert opp["stop"] == mid["stop"] == r2["stop"] == none["stop"], "çıkış kolu GİRİŞİ ve STOPU değiştirmez"


def test_a_target_on_the_wrong_side_of_the_entry_is_dropped_not_clamped():
    """Kenarı aşmış girişte kutunun karşı kenarı girişin YANLIŞ tarafında kalabilir; uydurulmuş hedef yok."""
    bars = _m5([(100, 101, 99, 100), (88, 89.0, 85.0, 86.0), (86.0, 86.2, 84.0, 84.5)])
    act = decide(daily_rows=_daily(110.0, 90.0), m5_rows=bars,
                 params=BoxParams(exit_kind="box_mid", long_stop="prev_candle", allow_short=False))
    assert act is None or act["targets"] == []


def test_r_multiple_scales_the_target_with_the_ratio():
    kw = dict(daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup())
    t1 = decide(**kw, params=BoxParams(exit_kind="r_multiple", exit_r=1.0))["targets"][0]
    t3 = decide(**kw, params=BoxParams(exit_kind="r_multiple", exit_r=3.0))["targets"][0]
    entry = 107.0
    assert (entry - t3) == pytest.approx(3.0 * (entry - t1))


# ------------------------------------------------------------------ gün sonu düzleşme
def test_a_position_opened_on_an_earlier_day_is_closed_at_the_day_boundary():
    act = decide(daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup(),
                 position={"side": "SHORT", "opened_ts": D0 - 3 * 3_600_000})
    assert act is not None and act["action"] == "CLOSE" and act["reason"] == "BOX_EOD_FLAT"


def test_a_position_opened_the_same_day_is_left_alone():
    assert decide(daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup(),
                  position={"side": "SHORT", "opened_ts": D0 + 60_000}) is None


def test_with_eod_disabled_the_rule_never_closes_a_position_itself():
    assert decide(daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup(),
                  position={"side": "SHORT", "opened_ts": D0 - 3 * 3_600_000},
                  params=BoxParams(eod_close=False)) is None


# ------------------------------------------------------------------ saflık ve sözleşme
def test_decide_does_not_mutate_its_inputs():
    d, m = _daily(110.0, 90.0), _short_setup()
    d_before, m_before = copy.deepcopy(d), copy.deepcopy(m)
    decide(daily_rows=d, m5_rows=m)
    assert d == d_before and m == m_before


def test_an_unknown_variant_is_rejected_loudly():
    with pytest.raises(ValueError):
        decide("b9_nope", daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup())
    with pytest.raises(ValueError):
        rule_state("b9_nope", daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup())


@pytest.mark.parametrize("bad", [
    {"trigger": "nope"}, {"long_stop": "nope"}, {"exit_kind": "nope"},
    {"near_frac": 0.0}, {"near_frac": 0.9}, {"loc_lookback": 0},
    {"exit_kind": "r_multiple", "exit_r": 0.0}, {"stop_buffer_frac": -0.1},
])
def test_invalid_parameters_raise_instead_of_falling_back_to_a_default(bad):
    with pytest.raises(ValueError):
        decide(daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup(), params=BoxParams(**bad))


def test_rule_state_reports_the_same_numbers_the_decision_used():
    kw = dict(daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup())
    act, st = decide(**kw), rule_state(**kw)
    assert st["ok"] and st["side"] == act["direction"] and st["triggered"] is True
    assert st["stop_if_open"] == pytest.approx(act["stop"])
    assert st["targets_if_open"] == [pytest.approx(act["targets"][0])]
    assert st["box_high"] == act["box_high"] and st["box_low"] == act["box_low"]
    assert st["close"] == pytest.approx(act["signal_close"])


def test_rule_state_names_the_reason_when_data_is_missing():
    assert rule_state(daily_rows=[], m5_rows=[])["reason"] == "NOT_ENOUGH_DAILY_BARS"
    st = rule_state(daily_rows=_daily(110.0, 90.0), m5_rows=[])
    assert st["ok"] is False and st["reason"] == "NOT_ENOUGH_M5_BARS"


def test_intraday_reader_rejects_bars_whose_extremes_contradict_the_body():
    bad = _m5([(100, 101, 99, 100), (100, 101, 99, 100), (100, 99.0, 101.0, 100)])   # high < low
    assert read_intraday(bad) is None


def test_intraday_day_extremes_only_use_bars_from_the_current_utc_day():
    """Dünün barları BUGÜNÜN dibine karışmamalı — long stopu doğrudan buradan okuyor."""
    prev_day = _m5([(50, 51, 40.0, 50)], start_ts=D0 - 2 * M5)
    today = _m5([(100, 101, 95.0, 100), (100, 101, 96.0, 100), (100, 101, 97.0, 100)], start_ts=D0)
    intr = read_intraday(prev_day + today)
    assert intr["day_low"] == pytest.approx(95.0) and intr["n_day_bars"] == 3


# ------------------------------------------------------------------ süpürme
def test_sweep_builds_the_cartesian_product_and_labels_every_arm_uniquely():
    arms = sweep_params(near_frac=[0.05, 0.10], exit_kind=["box_mid", "box_opposite"])
    assert len(arms) == 4
    assert len({a.label() for a in arms}) == 4


def test_sweep_refuses_arms_that_collapse_to_the_same_label():
    """İki kol aynı etikete düşerse koşu sessizce üst üste yazar; bu yüzden hata verilir."""
    with pytest.raises(ValueError):
        sweep_params(exit_r=[2.0, 2.0], exit_kind=["r_multiple"])


def test_default_params_are_the_video_reading_except_the_exit_which_the_video_never_states():
    p = DEFAULT_PARAMS
    assert p.trigger == "break_prev" and p.long_stop == "day_low"
    assert p.allow_long and p.allow_short and p.leverage == 1
    assert p.exit_kind in ("box_opposite", "box_mid", "r_multiple", "none")


# ------------------------------------------------------------------ min_stop_pct (BİZİM eşiğimiz)
def test_a_stop_tighter_than_the_threshold_produces_no_trade():
    """Ölçüldü: kuralın stopu medyanda fiyatın %0,25'i ve gidiş-dönüş maliyet (%0,16) riskin %80'ini
    yiyor. Eşik videoda YOKTUR; bu yüzden varsayılanı 0'dır ve etkisi ayrı bir kol olarak ölçülür."""
    kw = dict(daily_rows=_daily(110.0, 90.0), m5_rows=_short_setup())
    # giriş 107.0, stop 110.0 → stop mesafesi %2,80
    assert decide(**kw, params=BoxParams(min_stop_pct=0.0)) is not None
    assert decide(**kw, params=BoxParams(min_stop_pct=2.0)) is not None
    assert decide(**kw, params=BoxParams(min_stop_pct=3.0)) is None


def test_the_threshold_is_off_by_default_so_the_video_reading_stays_measurable():
    assert DEFAULT_PARAMS.min_stop_pct == 0.0


def test_the_threshold_appears_in_the_arm_label_only_when_it_is_on():
    assert "_ms" not in BoxParams(min_stop_pct=0.0).label()
    assert "_ms0.5" in BoxParams(min_stop_pct=0.5).label()
    arms = sweep_params(min_stop_pct=[0.0, 0.5])
    assert len({a.label() for a in arms}) == 2
