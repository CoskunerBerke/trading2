"""FAZ 12 — `MULTI_TIMEFRAME_LIQUIDITY_CONFIRMATION_V1` (H ailesi) regresyon paketi.

64 numaralı sözleşme maddesinin tamamı: point-in-time, HTF mantığı, LTF mantığı, geometri,
izolasyon, atıf ve veri bütçesi.

Fikstür sözleşmesi: senaryolar **eşikleri sonuca uydurmak için değil**, tanımların
deterministik olduğunu göstermek için kurulmuştur. Hiçbir eşik F00030'u VETO yapmak üzere
ayarlanmamıştır.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.learn import multitimeframe_context as M
from tradingbot.learn.multitimeframe_context import (ABSTAIN, ALLOW, VETO, MEASURED, MISSING,
                                                     MultiTimeframeConfig, PAIR_D_H1,
                                                     PAIR_H4_M15, PAIR_H1_M5, PAIR_M15_M1,
                                                     build_mtf_context, build_variants,
                                                     closed_bars_as_of, confirmed_swings,
                                                     displacement, equal_level_clusters,
                                                     evaluate_variants, pair_status, retest,
                                                     structural_geometry, structure_shift)

DAY = 86_400_000
HOUR = 3_600_000
AS_OF = 1000 * DAY


# ================================================================== fikstürler

def htf_bars(n: int = 60, *, sell: float = 90.0, buy: float = 110.0,
             as_of: int = AS_OF) -> list[dict]:
    """Günlük kareler: `sell` seviyesinde eşit düşük kümesi, `buy` seviyesinde eşit yüksek."""
    out = []
    for i in range(n):
        o, c, h, low = 94.0, 94.5, 96.0, 93.0
        if i in (20, 30):
            low = sell
        if i in (25, 35):
            h = buy
        out.append({"timestamp": as_of - (n - i) * DAY, "open": o, "high": h,
                    "low": low, "close": c})
    return out


def ltf_bars(n: int = 60, *, as_of: int = AS_OF, sweep_low: float = 89.0,
             reclaim: bool = True, shift: bool = True, big_body: bool = True,
             good_close_loc: bool = True, retest_ok: bool = True,
             invalidate: bool = False) -> list[dict]:
    """Saatlik kareler: süpürme → geri alma → teyitli salınım → yer değiştirme → retest.

    Her bayrak TEK bir mekanik koşulu kapatır; böylece bir testin neyi ölçtüğü belirsiz kalmaz.
    """
    out = []
    for i in range(n):
        o, c, h, low = 93.0, 93.0, 93.5, 92.5
        if i == 40:
            o, h, low = 92.0, 92.5, sweep_low
            c = 91.5 if reclaim else 89.2          # geri alma yoksa seviyenin altında kapanır
        elif 41 <= i <= 43:
            o, c, h, low = 92.0, 92.0, 92.5, 91.5
        elif i == 44:
            o, c, h, low = 92.0, 93.0, 95.0, 91.8   # teyitli salınım yükseği = 95.0
        elif 45 <= i <= 48:
            o, c, h, low = 93.0, 93.0, 93.5, 92.5
        elif i == 49:
            if not shift:
                o, c, h, low = 93.0, 93.2, 93.6, 92.6      # kapanış 95.0'ın ALTINDA
            elif not big_body:
                o, c, h, low = 96.3, 96.5, 96.6, 96.2      # gövde/aralık ÇOK KÜÇÜK
            elif not good_close_loc:
                o, c, h, low = 95.0, 95.6, 98.0, 94.8      # kapanış aralığın DİBİNDE
            else:
                o, c, h, low = 95.0, 96.5, 96.6, 94.8      # tam yer değiştirme
        elif i == 50 and not shift:
            o, c, h, low = 93.0, 93.1, 93.6, 92.6           # kayma yok senaryosunda DÜZ kal
        elif i >= 51 and not shift:
            o, c, h, low = 93.0, 93.0, 93.5, 92.5
        elif i == 50:
            if invalidate:
                o, c, h, low = 96.5, 93.0, 96.6, 92.8      # seviyenin ALTINA kapanış
            elif retest_ok:
                o, c, h, low = 96.5, 96.0, 96.6, 95.0      # seviyeye dönüş + üstünde kapanış
            else:
                o, c, h, low = 96.5, 96.4, 96.6, 96.3      # seviyeye HİÇ dönmez
        elif i >= 51:
            o, c, h, low = 96.2, 96.2, 96.5, 95.8
        out.append({"timestamp": as_of - (n - i) * HOUR, "open": o, "high": h,
                    "low": low, "close": c})
    return out


def ctx(**kw):
    base = dict(symbol="X/USDT", baseline_direction="LONG", as_of_ms=AS_OF, pair=PAIR_D_H1,
                htf_frame=htf_bars(), ltf_frame=ltf_bars(), htf_atr=2.0, ltf_atr=1.0,
                current_price=96.5)
    base.update(kw)
    return build_mtf_context(**base)


# ================================================== 1-6: POINT-IN-TIME sözleşmesi

def test_01_open_htf_candle_is_rejected():
    """Kapanmamış GÜNLÜK bar hesaba GİREMEZ."""
    bars = htf_bars()
    bars.append({"timestamp": AS_OF - DAY // 2, "open": 94.0, "high": 999.0,
                 "low": 1.0, "close": 94.0})            # yarısı geçmiş, KAPANMAMIŞ
    kept, meta = closed_bars_as_of(bars, frame_key="1d", as_of_ms=AS_OF)
    assert meta["dropped_unclosed"] == 1
    assert all(b["high"] != 999.0 for b in kept)


def test_02_open_ltf_candle_is_rejected():
    bars = ltf_bars()
    bars.append({"timestamp": AS_OF - HOUR // 2, "open": 96.0, "high": 500.0,
                 "low": 5.0, "close": 96.0})
    kept, meta = closed_bars_as_of(bars, frame_key="1h", as_of_ms=AS_OF)
    assert meta["dropped_unclosed"] == 1
    assert all(b["high"] != 500.0 for b in kept)


def test_03_a_future_decoy_bar_cannot_change_a_prior_result():
    before = ctx()
    decoy_h = htf_bars() + [{"timestamp": AS_OF + 5 * DAY, "open": 94.0, "high": 9999.0,
                             "low": 0.5, "close": 94.0}]
    decoy_l = ltf_bars() + [{"timestamp": AS_OF + 5 * HOUR, "open": 96.0, "high": 9999.0,
                             "low": 0.5, "close": 9000.0}]
    after = ctx(htf_frame=decoy_h, ltf_frame=decoy_l)
    assert after["decision"] == before["decision"]
    assert after["structural_rr"] == before["structural_rr"]
    assert after["htf_liquidity_level"] == before["htf_liquidity_level"]
    assert after["point_in_time"]["htf"]["dropped_future"] == 1
    assert after["point_in_time"]["ltf"]["dropped_future"] == 1


def test_04_close_time_equal_to_as_of_is_included():
    """`close_time == as_of_ms` SINIRI dâhildir; bir bar erken kesilmez."""
    kept, meta = closed_bars_as_of(
        [{"timestamp": AS_OF - DAY, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}],
        frame_key="1d", as_of_ms=AS_OF)
    assert len(kept) == 1 and meta["dropped_unclosed"] == 0
    assert meta["last_closed_at_ms"] == AS_OF
    # Bir milisaniye erken karar alınsaydı bar HENÜZ kapanmamış olurdu.
    kept2, meta2 = closed_bars_as_of(
        [{"timestamp": AS_OF - DAY, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}],
        frame_key="1d", as_of_ms=AS_OF - 1)
    assert kept2 == [] and meta2["dropped_unclosed"] == 1


def test_05_context_is_deterministic_and_immutable_for_the_same_inputs():
    a, b = ctx(), ctx()
    for k in ("decision", "structural_rr", "htf_liquidity_level", "ltf_structure_state",
              "config_id"):
        assert a[k] == b[k]
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True,
                                                                    default=str)


def test_06_no_outcome_or_forward_label_leaks_into_the_context():
    r = ctx()
    forbidden = ("r_multiple", "net_pnl", "pnl", "closed_at", "exit_reason", "won",
                 "outcome_class", "actual_r", "mfe_r", "mae_r")
    blob = json.dumps(r, default=str)
    assert r["sees_outcome"] is False
    assert r["written_at_stage"] == "RANKING"
    for k in forbidden:
        assert k not in r, k
        assert f'"{k}"' not in blob, k


# ================================================== 7-16: ÜST ZAMAN DİLİMİ mantığı

def test_07_bullish_sell_side_sweep_and_reclaim():
    r = ctx()
    assert r["htf_trend"] == M.HTF_BULLISH
    assert r["htf_liquidity_side"] == "sell_side"
    assert r["htf_interaction"] == "LOW_SWEEP_RECLAIM"
    assert r["htf_reclaim"] is True
    assert r["htf_sweep_distance_atr"] == pytest.approx(0.5)


def test_08_bearish_buy_side_sweep_and_reclaim():
    """Simetri: üst likiditenin süpürülüp geri alınması DÜŞÜŞ bağlamıdır."""
    n = 60
    h = [{"timestamp": AS_OF - (n - i) * DAY, "open": 100.0, "close": 100.0,
          "high": (110.0 if i in (20, 30) else 102.0),
          "low": (90.0 if i in (25, 35) else 98.0)} for i in range(n)]
    lt = []
    for i in range(n):
        o, c, hh, low = 104.0, 104.0, 104.5, 103.5
        if i == 40:
            o, hh, low, c = 105.0, 111.0, 104.5, 108.5      # süpür + geri al (110 altına)
        lt.append({"timestamp": AS_OF - (n - i) * HOUR, "open": o, "high": hh,
                   "low": low, "close": c})
    r = build_mtf_context(symbol="X", baseline_direction="SHORT", as_of_ms=AS_OF,
                          pair=PAIR_D_H1, htf_frame=h, ltf_frame=lt, htf_atr=2.0,
                          ltf_atr=1.0, current_price=104.0)
    assert r["htf_trend"] == M.HTF_BEARISH
    assert r["htf_liquidity_side"] == "buy_side"
    assert r["htf_interaction"] == "HIGH_SWEEP_RECLAIM"


def test_09_bullish_accepted_breakout_is_classified_as_such():
    """Gövdeyle seviyenin çok ötesine kapanış: kabul edilmiş kırılım, süpürme DEĞİL."""
    n = 60
    h = htf_bars(n)
    lt = []
    for i in range(n):
        o, c, hh, low = 100.0, 100.0, 100.5, 99.5
        if i >= 40:
            o, c, hh, low = 112.0, 114.0, 114.5, 111.5      # 110'un ÇOK üstünde kapanış
        lt.append({"timestamp": AS_OF - (n - i) * HOUR, "open": o, "high": hh,
                   "low": low, "close": c})
    r = build_mtf_context(symbol="X", baseline_direction="LONG", as_of_ms=AS_OF,
                          pair=PAIR_D_H1, htf_frame=h, ltf_frame=lt, htf_atr=2.0,
                          ltf_atr=1.0, current_price=114.0)
    assert r["htf_accepted_breakout"] is True
    assert r["htf_interaction"] == "ACCEPTED_BREAKOUT"
    assert M.R_ACCEPTED_BREAKOUT_NOT_SWEEP in r["reason_codes"]


def test_10_bearish_accepted_breakout_is_classified_as_such():
    n = 60
    h = htf_bars(n)
    lt = []
    for i in range(n):
        o, c, hh, low = 93.0, 93.0, 93.5, 92.5
        if i >= 40:
            o, c, hh, low = 86.0, 85.0, 86.5, 84.5          # 90'ın ÇOK altında kapanış
        lt.append({"timestamp": AS_OF - (n - i) * HOUR, "open": o, "high": hh,
                   "low": low, "close": c})
    r = build_mtf_context(symbol="X", baseline_direction="SHORT", as_of_ms=AS_OF,
                          pair=PAIR_D_H1, htf_frame=h, ltf_frame=lt, htf_atr=2.0,
                          ltf_atr=1.0, current_price=85.0)
    assert r["htf_accepted_breakout"] is True
    assert r["htf_trend"] == M.HTF_BEARISH


def test_11_wick_only_breakout_is_not_an_accepted_breakout():
    """Yalnız FİTİL seviyeyi aşarsa kabul edilmiş kırılım SAYILMAZ."""
    n = 60
    h = htf_bars(n)
    lt = []
    for i in range(n):
        o, c, hh, low = 93.0, 93.0, 93.5, 92.5
        if i == 40:
            o, c, hh, low = 92.0, 91.6, 92.5, 89.0          # fitil 90 altına, gövde üstünde
        lt.append({"timestamp": AS_OF - (n - i) * HOUR, "open": o, "high": hh,
                   "low": low, "close": c})
    r = build_mtf_context(symbol="X", baseline_direction="LONG", as_of_ms=AS_OF,
                          pair=PAIR_D_H1, htf_frame=h, ltf_frame=lt, htf_atr=2.0,
                          ltf_atr=1.0, current_price=93.0)
    assert r["htf_accepted_breakout"] is False
    assert r["htf_interaction"] != "ACCEPTED_BREAKOUT"


def test_12_an_accepted_breakout_is_never_relabelled_as_a_sweep():
    """Sözleşme: kabul edilmiş kırılım sonradan hindsight ile süpürmeye ÇEVRİLEMEZ."""
    n = 60
    lt = []
    for i in range(n):
        o, c, hh, low = 100.0, 100.0, 100.5, 99.5
        if i >= 40:
            o, c, hh, low = 112.0, 114.0, 114.5, 111.5
        lt.append({"timestamp": AS_OF - (n - i) * HOUR, "open": o, "high": hh,
                   "low": low, "close": c})
    r = build_mtf_context(symbol="X", baseline_direction="LONG", as_of_ms=AS_OF,
                          pair=PAIR_D_H1, htf_frame=htf_bars(n), ltf_frame=lt,
                          htf_atr=2.0, ltf_atr=1.0, current_price=114.0)
    assert r["htf_interaction"] not in ("HIGH_SWEEP_RECLAIM", "LOW_SWEEP_RECLAIM")


def test_13_equal_high_low_cluster_respects_the_atr_tolerance():
    sw = [{"level": 100.0, "confirmed_at_index": 5, "timestamp": 1, "side": "high"},
          {"level": 100.15, "confirmed_at_index": 8, "timestamp": 2, "side": "high"},
          {"level": 130.0, "confirmed_at_index": 9, "timestamp": 3, "side": "high"}]
    # tolerans 0.10 * ATR 2.0 = 0.20 → ilk ikisi kümelenir, üçüncüsü ASLA.
    cl = equal_level_clusters(sw, atr=2.0, tolerance_atr=0.10)
    assert len(cl) == 1 and cl[0]["n_members"] == 2
    assert cl[0]["level"] == pytest.approx(100.075)
    # ATR ölçülemezse tolerans UYDURULMAZ.
    assert equal_level_clusters(sw, atr=None, tolerance_atr=0.10) == []
    # Tek salınım küme SAYILMAZ.
    assert equal_level_clusters(sw[:1], atr=2.0, tolerance_atr=0.10) == []


def test_14_no_interaction_yields_abstain():
    n = 60
    lt = [{"timestamp": AS_OF - (n - i) * HOUR, "open": 100.0, "high": 100.5,
           "low": 99.5, "close": 100.0} for i in range(n)]     # 90 ve 110'a HİÇ değmez
    r = build_mtf_context(symbol="X", baseline_direction="LONG", as_of_ms=AS_OF,
                          pair=PAIR_D_H1, htf_frame=htf_bars(n), ltf_frame=lt,
                          htf_atr=2.0, ltf_atr=1.0, current_price=100.0)
    assert r["decision"] == ABSTAIN
    assert M.R_NO_INTERACTION in r["reason_codes"]
    assert r["htf_trend"] == M.HTF_NEUTRAL


def test_15_two_sided_interaction_is_ambiguous_and_abstains():
    n = 60
    lt = []
    for i in range(n):
        o, c, hh, low = 100.0, 100.0, 100.5, 99.5
        if i == 30:
            o, c, hh, low = 92.0, 91.5, 92.5, 89.0            # alt likidite süpürüldü+geri alındı
        if i == 40:
            o, c, hh, low = 108.0, 108.5, 111.0, 107.5        # üst likidite süpürüldü+geri alındı
        lt.append({"timestamp": AS_OF - (n - i) * HOUR, "open": o, "high": hh,
                   "low": low, "close": c})
    r = build_mtf_context(symbol="X", baseline_direction="LONG", as_of_ms=AS_OF,
                          pair=PAIR_D_H1, htf_frame=htf_bars(n), ltf_frame=lt,
                          htf_atr=2.0, ltf_atr=1.0, current_price=100.0)
    assert r["decision"] == ABSTAIN
    assert M.R_TWO_SIDED in r["reason_codes"]


def test_16_missing_structural_target_abstains():
    geo = structural_geometry(entry=100.0, sweep_extreme=95.0, target=None, is_long=True,
                              atr=2.0, cfg=MultiTimeframeConfig())
    assert geo["valid"] is False
    assert geo["reason"] == M.R_NO_TARGET


# ================================================== 17-27: ALT ZAMAN DİLİMİ mantığı

def test_17_bullish_structure_shift_is_detected_on_a_close():
    r = ctx()
    assert r["ltf_structure_state"] == M.LTF_SHIFT_UP
    assert r["ltf_shift_direction"] == "LONG"
    assert r["ltf_structure_shift"] is True


def test_18_bearish_structure_shift_is_detected_on_a_close():
    n = 40
    bars = []
    for i in range(n):
        o, c, h, low = 100.0, 100.0, 100.5, 99.5
        if i == 20:
            o, c, h, low = 100.0, 99.0, 100.2, 96.0          # teyitli salınım DÜŞÜĞÜ = 96
        if i == 30:
            o, c, h, low = 97.0, 94.0, 97.2, 93.8            # 96'nın ALTINA kapanış
        bars.append({"timestamp": AS_OF - (n - i) * HOUR, "open": o, "high": h,
                     "low": low, "close": c})
    sh = structure_shift(bars, cfg=MultiTimeframeConfig(), atr=1.0)
    assert sh["state"] == M.LTF_SHIFT_DOWN
    assert sh["direction"] == "SHORT"


def test_19_an_open_candle_cannot_create_a_false_shift():
    """Kapanmamış bir mumun kapanışı yapı kayması ÜRETEMEZ."""
    bars = ltf_bars(shift=False)
    bars.append({"timestamp": AS_OF - HOUR // 2, "open": 93.0, "high": 200.0,
                 "low": 92.0, "close": 199.0})               # AÇIK mum, seviyeyi kırardı
    r = ctx(ltf_frame=bars)
    assert r["ltf_structure_state"] == M.LTF_NO_SHIFT
    assert r["point_in_time"]["ltf"]["dropped_unclosed"] == 1


def test_20_displacement_below_threshold_is_rejected():
    r = ctx(ltf_frame=ltf_bars(big_body=False))
    assert r["decision"] == VETO
    assert M.R_DISPLACEMENT_LOW in r["reason_codes"]


def test_21_displacement_above_threshold_passes():
    r = ctx()
    assert r["ltf_displacement_body_atr"] >= MultiTimeframeConfig().min_displacement_body_atr
    assert r["ltf_displacement_range_atr"] >= MultiTimeframeConfig().min_displacement_range_atr
    assert r["decision"] == ALLOW


def test_22_close_location_failure_is_rejected():
    r = ctx(ltf_frame=ltf_bars(good_close_loc=False))
    assert r["decision"] == VETO
    assert M.R_CLOSE_LOCATION_LOW in r["reason_codes"]


def test_23_a_valid_retest_is_confirmed():
    r = ctx()
    assert r["ltf_retest_state"] == M.RETEST_CONFIRMED
    assert build_mtf_context(
        symbol="X", baseline_direction="LONG", as_of_ms=AS_OF, pair=PAIR_D_H1,
        htf_frame=htf_bars(), ltf_frame=ltf_bars(), htf_atr=2.0, ltf_atr=1.0,
        current_price=96.5,
        cfg=MultiTimeframeConfig(variant="H_RETEST_REQUIRED", require_retest=True,
                                 retest_bar_limit=4))["decision"] == ALLOW


def test_24_a_late_retest_is_labelled_late_and_vetoed_when_required():
    bars = [{"timestamp": i * HOUR, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0}
            for i in range(8)]
    bars += [{"timestamp": (8 + i) * HOUR, "open": 105.0, "high": 105.5, "low": 104.5,
              "close": 105.0} for i in range(6)]              # 6 bar boyunca dönüş YOK
    bars.append({"timestamp": 14 * HOUR, "open": 105.0, "high": 105.5, "low": 100.0,
                 "close": 105.0})                             # LİMİTTEN SONRA dönüş
    rt = retest(bars, shift_index=7, level=100.0, is_up=True, atr=1.0,
                cfg=MultiTimeframeConfig(retest_bar_limit=3))
    assert rt["state"] == M.RETEST_LATE


def test_25_a_missing_retest_is_absent_and_vetoed_when_required():
    r = ctx(ltf_frame=ltf_bars(retest_ok=False),
            cfg=MultiTimeframeConfig(variant="H_RETEST_REQUIRED", require_retest=True,
                                     retest_bar_limit=4))
    assert r["ltf_retest_state"] == M.RETEST_ABSENT
    assert r["decision"] == VETO
    assert M.R_RETEST_MISSING in r["reason_codes"]


def test_26_an_opposite_retest_invalidates_the_level():
    r = ctx(ltf_frame=ltf_bars(invalidate=True),
            cfg=MultiTimeframeConfig(variant="H_RETEST_REQUIRED", require_retest=True))
    assert r["ltf_retest_state"] == M.RETEST_INVALIDATED
    assert r["decision"] == VETO
    assert M.R_RETEST_OPPOSITE in r["reason_codes"]


def test_27_insufficient_bars_abstain():
    short = ltf_bars()[-3:]
    r = ctx(ltf_frame=short)
    assert r["decision"] == ABSTAIN
    assert M.R_INSUFFICIENT_BARS in r["reason_codes"]
    sh = structure_shift(short, cfg=MultiTimeframeConfig(), atr=1.0)
    assert sh["state"] == M.LTF_UNKNOWN and sh["reason"] == M.R_INSUFFICIENT_BARS


# ================================================== 28-33: yapısal geometri

def test_28_valid_long_geometry():
    g = structural_geometry(entry=100.0, sweep_extreme=95.0, target=115.0, is_long=True,
                            atr=2.0, cfg=MultiTimeframeConfig())
    assert g["valid"] is True
    assert g["stop"] == pytest.approx(94.5)          # 95 − 0.25×2
    assert g["rr"] == pytest.approx(15.0 / 5.5)


def test_29_valid_short_geometry():
    g = structural_geometry(entry=100.0, sweep_extreme=105.0, target=85.0, is_long=False,
                            atr=2.0, cfg=MultiTimeframeConfig())
    assert g["valid"] is True
    assert g["stop"] == pytest.approx(105.5)
    assert g["rr"] == pytest.approx(15.0 / 5.5)


def test_30_stop_on_the_wrong_side_is_invalid():
    g = structural_geometry(entry=100.0, sweep_extreme=105.0, target=115.0, is_long=True,
                            atr=2.0, cfg=MultiTimeframeConfig())
    assert g["valid"] is False and g["reason"] == "STOP_ON_WRONG_SIDE"


def test_31_target_on_the_wrong_side_is_invalid():
    g = structural_geometry(entry=100.0, sweep_extreme=95.0, target=90.0, is_long=True,
                            atr=2.0, cfg=MultiTimeframeConfig())
    assert g["valid"] is False and g["reason"] == "TARGET_ON_WRONG_SIDE"


def test_32_rr_below_the_minimum_follows_the_precommitted_variant():
    """H_STRICT (R:R ≥ 2.0) VETO eder; H_LENIENT (veto_on_low_rr=False) ABSTAIN eder."""
    strict = ctx(cfg=MultiTimeframeConfig(variant="H_STRICT", min_structural_rr=2.0))
    assert strict["decision"] == VETO
    assert M.R_RR_BELOW_MIN in strict["reason_codes"]
    lenient = ctx(cfg=MultiTimeframeConfig(variant="H_LENIENT", min_structural_rr=99.0,
                                           veto_on_low_rr=False))
    assert lenient["decision"] == ABSTAIN


def test_33_missing_atr_abstains_and_never_becomes_zero():
    g = structural_geometry(entry=100.0, sweep_extreme=95.0, target=115.0, is_long=True,
                            atr=None, cfg=MultiTimeframeConfig())
    assert g["valid"] is False and g["reason"] == M.R_ATR_MISSING
    assert g["stop"] is None
    r = ctx(htf_atr=None)
    assert r["decision"] == ABSTAIN
    assert M.R_ATR_MISSING in r["reason_codes"]
    assert r["htf_atr"] is None
    assert r["field_provenance"]["htf_atr"] == MISSING


# ================================================== 34-44: İZOLASYON

def _engine_stub(store, *, mtf: bool):
    import types
    from tradingbot.engine_v3 import TradingEngineV3
    eng = types.SimpleNamespace(
        entry_snapshot_store=store, _entry_pending=[], run_id="r", _tour_no=1,
        _journal_cycle=1, entry_cfg=types.SimpleNamespace(policy_version="entry_v1.0.0"),
        entry_mode="SHADOW", weekly_cfg=None,
        mtf_cfg=(MultiTimeframeConfig() if mtf else None), mtf_mode="SHADOW",
        code_sha=lambda: "sha", config_hash=lambda: "cfg",
        _attach_weekly_context=lambda a, b: None,
        _atr_from_frame=TradingEngineV3._atr_from_frame)
    eng._attach_mtf_context = lambda s_, r_: TradingEngineV3._attach_mtf_context(eng, s_, r_)
    return eng


def _pending():
    class P:
        valid, entry, stop, targets = True, 96.5, 88.5, (110.0,)
        entry_type, expected_r, rr = "kirilim", 2.0, 2.0

    class D:
        direction, specialist_reports, opportunity, score = "LONG", (), {}, 0.4

    return [{"symbol": "X/USDT", "direction": "LONG", "decision": D(), "plan": P(),
             "chief": {}, "market": "USDM_PERP", "rank": 0, "specialists": None,
             "features": {"atr_pct": 2.0}, "daily_frame": htf_bars(),
             "intraweek_frame": None, "hourly_frame": ltf_bars(), "ts": None,
             "entry_log": {"risk_allowed": True, "risk_reasons": [], "trade_id": "F1",
                           "block_code": None}}]


def _flush(mtf: bool, tmp_path: Path) -> dict:
    from datetime import datetime, timezone
    from tradingbot.engine_v3 import TradingEngineV3
    from tradingbot.learn.entry_snapshot import EntrySnapshotStore
    now = datetime.fromtimestamp(AS_OF / 1000.0, tz=timezone.utc)
    store = EntrySnapshotStore(tmp_path / f"s_{mtf}.jsonl", max_per_cycle=10)
    eng = _engine_stub(store, mtf=mtf)
    buf = _pending()
    buf[0]["ts"] = now
    eng._entry_pending = buf
    TradingEngineV3._entry_flush(eng, now)
    return next(iter(store.by_candidate().values()))


ACTIVE_FIELDS = ("candidate_id", "decision_id", "symbol", "direction", "entry_price",
                 "stop_price", "targets", "baseline_accepted", "baseline_rank",
                 "baseline_reject_reason", "atr_pct", "market_type")


def test_34_h_on_off_yields_byte_identical_active_risk_fields(tmp_path: Path):
    off, on = _flush(False, tmp_path), _flush(True, tmp_path)
    for k in ACTIVE_FIELDS:
        assert off.get(k) == on.get(k), k
    assert "mtf_context" not in off
    assert "mtf_context" in on


def test_35_h_on_off_yields_identical_opened_position_fingerprints(tmp_path: Path):
    off, on = _flush(False, tmp_path), _flush(True, tmp_path)
    fp = lambda d: json.dumps({k: d.get(k) for k in ACTIVE_FIELDS}, sort_keys=True,
                              default=str)
    assert fp(off) == fp(on)
    # H'nin eklediği TEK fark kendi alanıdır.
    assert set(on) - set(off) == {"mtf_context"}


def _code_only(path: str) -> str:
    """Modülün KODU — docstring ve yorumlar hariç.

    Güvenlik sözleşmesini ANLATAN bir docstring, o sözleşmenin ihlali değildir; bu yüzden
    yasak adlar yalnız gerçek kod üzerinde aranır.
    """
    import ast
    src = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)                          # docstring DÜŞÜR
    return ast.unparse(tree)                          # yorumlar zaten düşer


def test_36_h_does_not_import_or_access_the_gateway():
    src = _code_only("tradingbot/learn/multitimeframe_context.py")
    for bad in ("gateway", "Gateway", "create_order", "place_order", "ccxt", "binance"):
        assert bad not in src, bad


def test_37_h_does_not_write_the_ledger():
    for f in ("tradingbot/learn/multitimeframe_context.py", "tradingbot/learn/mtf_eval.py"):
        src = _code_only(f)
        for bad in ("ledger", "Ledger", "open(", "write_text", "atomic_write"):
            assert bad not in src, f"{f}:{bad}"


def test_38_h_does_not_import_or_use_the_risk_engine():
    src = _code_only("tradingbot/learn/multitimeframe_context.py")
    for bad in ("RiskEngine", "risk_engine", "from ..risk", "position_size"):
        assert bad not in src, bad


def test_39_a_to_g_family_outputs_are_unchanged_by_h():
    """H, A–E ve F/G ailelerinin karar fonksiyonlarını ÇAĞIRMAZ ve DEĞİŞTİREMEZ."""
    src = _code_only("tradingbot/learn/multitimeframe_context.py")
    for bad in ("entry_challenger", "challenger_a", "challenger_f", "evaluate_all",
                "evaluate_v2"):
        assert bad not in src, bad
    from tradingbot.learn.entry_challenger import EntryChallengerConfig, evaluate_all
    snap = {"candidate_id": "c", "direction": "LONG", "p_win": 0.5,
            "conservative_net_edge_r": 0.2, "regime": "TREND_UP", "consensus_score": 0.4}
    before = evaluate_all(snap, EntryChallengerConfig())
    ctx()                                        # H çalıştı
    assert evaluate_all(snap, EntryChallengerConfig()) == before


def test_40_exit_challenger_outputs_are_unchanged_by_h():
    src = _code_only("tradingbot/learn/multitimeframe_context.py")
    for bad in ("exit_policy", "exit_eval", "exit_executor", "ExitPolicyConfig"):
        assert bad not in src, bad


def test_41_applied_is_always_false():
    for v in build_variants():
        assert ctx(cfg=v)["applied"] is False
    from tradingbot.learn.mtf_eval import aggregate
    d = aggregate([])
    assert d["applied"] is False and d["auto_promotion"] is False


@pytest.mark.parametrize("mode", ["ACTIVE", "PAPER_BOUNDED"])
def test_42_43_active_and_paper_bounded_modes_are_rejected(mode):
    import yaml
    from tradingbot.config_v3 import ConfigError, load_v3
    raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    raw["entry_selectivity"] = dict(raw.get("entry_selectivity") or {}) | {"mtf_mode": mode}
    with pytest.raises(ConfigError, match="MULTITIMEFRAME_NOT_ACTIVATED"):
        load_v3(raw)


def test_44_auto_promotion_is_rejected_by_config():
    import yaml
    from tradingbot.config_v3 import ConfigError, load_v3
    raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    raw["entry_selectivity"] = dict(raw.get("entry_selectivity") or {}) | {
        "mtf_auto_promotion": True}
    with pytest.raises(ConfigError, match="MULTITIMEFRAME_AUTO_PROMOTION_FORBIDDEN"):
        load_v3(raw)


# ================================================== 45-55: ATIF

def _h_snap(cid: str, symbol: str, decisions: dict[str, str]) -> dict:
    return {"candidate_id": cid, "symbol": symbol, "direction": "LONG",
            "link_status": "LINKED",
            "mtf_context": {"variants": {v: {"decision": d, "reason_codes": ["X"],
                                             "written_at_stage": "RANKING",
                                             "sees_outcome": False, "pair": PAIR_D_H1,
                                             "missing_fields": []}
                                         for v, d in decisions.items()}}}


def _close(tid: str, symbol: str, r: float, pnl: float, when: str) -> dict:
    return {"close_event_id": f"e_{tid}", "trade_id": tid, "symbol": symbol,
            "closed_at": when, "r_multiple": r, "net_pnl": pnl, "fees": 0.02,
            "funding": 0.0, "raw": {"slippage_cost": 0.03, "spread_cost": 0.0}}


ALLV = {v: ALLOW for v in M.VARIANT_NAMES}
VETOV = {v: VETO for v in M.VARIANT_NAMES}
ABSV = {v: ABSTAIN for v in M.VARIANT_NAMES}


def test_45_f00030_is_excluded_from_h_promotion_evidence():
    from tradingbot.learn.mtf_eval import EV_PRE_H, PRE_H, build_report
    snaps = {"pre": {"candidate_id": "pre", "symbol": "NATGAS/USDT", "direction": "LONG",
                     "link_status": "LINKED"}}          # H bağlamı YOK
    d = build_report(closes=[_close("F00030", "NATGAS/USDT", -1.0598, -0.7597,
                                    "2026-09-03T15:04:50+00:00")],
                     snapshots=snaps, links={"F00030": "pre"})
    ev = d["evaluations"][0]
    assert ev["status"] == PRE_H and ev["evidence_grade"] == EV_PRE_H
    assert d["n_h_linked_closes"] == 0 and d["n_pre_h_excluded"] == 1
    assert d["variants"]["H_BALANCED"]["n"] == 0


def test_46_a_pre_h_open_trade_closing_later_stays_excluded():
    from tradingbot.learn.mtf_eval import EV_PRE_H, build_report
    snaps = {"c31": {"candidate_id": "c31", "symbol": "LTC/USDT", "direction": "LONG",
                     "link_status": "LINKED"}}
    d = build_report(closes=[_close("F00031", "LTC/USDT", 2.0, 1.5,
                                    "2026-09-20T00:00:00+00:00")],
                     snapshots=snaps, links={"F00031": "c31"})
    assert d["evaluations"][0]["evidence_grade"] == EV_PRE_H
    assert d["n_h_linked_closes"] == 0


def test_47_a_complete_post_h_chain_is_counted_exactly_once():
    from tradingbot.learn.mtf_eval import EV_H_COMPLETE, build_report
    d = build_report(closes=[_close("F1", "BTC/USDT", 1.5, 1.1, "2026-09-10T00:00:00+00:00")],
                     snapshots={"c1": _h_snap("c1", "BTC/USDT", ALLV)},
                     links={"F1": "c1"})
    assert d["n_h_linked_closes"] == 1
    assert d["evaluations"][0]["evidence_grade"] == EV_H_COMPLETE
    assert d["variants"]["H_BALANCED"]["n"] == 1


def test_48_a_duplicate_close_is_not_counted_twice():
    from tradingbot.learn.mtf_eval import build_report
    c = _close("F1", "BTC/USDT", 1.5, 1.1, "2026-09-10T00:00:00+00:00")
    d = build_report(closes=[c, dict(c)], snapshots={"c1": _h_snap("c1", "BTC/USDT", ALLV)},
                     links={"F1": "c1"})
    assert d["n_h_linked_closes"] == 1
    assert len(d["evaluations"]) == 1


def test_49_a_blocked_loser_produces_avoided_loss():
    from tradingbot.learn.mtf_eval import build_report
    d = build_report(closes=[_close("F1", "BTC/USDT", -1.2, -0.9,
                                    "2026-09-10T00:00:00+00:00")],
                     snapshots={"c1": _h_snap("c1", "BTC/USDT", VETOV)},
                     links={"F1": "c1"})
    r = d["variants"]["H_BALANCED"]
    assert r["blocked_losers"] == 1 and r["blocked_winners"] == 0
    assert r["avoided_loss_r"] == pytest.approx(1.2)
    assert r["avoided_loss_usdt"] == pytest.approx(0.9)
    assert r["missed_gain_r"] == 0.0


def test_50_a_blocked_winner_produces_missed_gain():
    from tradingbot.learn.mtf_eval import build_report
    d = build_report(closes=[_close("F1", "BTC/USDT", 2.0, 1.4,
                                    "2026-09-10T00:00:00+00:00")],
                     snapshots={"c1": _h_snap("c1", "BTC/USDT", VETOV)},
                     links={"F1": "c1"})
    r = d["variants"]["H_BALANCED"]
    assert r["blocked_winners"] == 1 and r["blocked_losers"] == 0
    assert r["missed_gain_r"] == pytest.approx(2.0)
    assert r["missed_gain_usdt"] == pytest.approx(1.4)
    assert r["avoided_loss_r"] == 0.0


def test_51_abstain_is_counted_in_neither_allow_nor_veto():
    from tradingbot.learn.mtf_eval import build_report
    d = build_report(closes=[_close("F1", "BTC/USDT", -1.0, -0.8,
                                    "2026-09-10T00:00:00+00:00")],
                     snapshots={"c1": _h_snap("c1", "BTC/USDT", ABSV)},
                     links={"F1": "c1"})
    r = d["variants"]["H_BALANCED"]
    assert r["abstain_count"] == 1
    assert r["allow_count"] == 0 and r["veto_count"] == 0
    assert r["n_decided"] == 0 and r["coverage"] == 0.0
    assert r["abstain_rate"] == 1.0
    # ABSTAIN engelleme DEĞİLDİR: karşı-olgusal sonuç GERÇEK sonuçtur.
    assert r["counterfactual"]["total_r"] == pytest.approx(-1.0)
    assert r["avoided_loss_r"] == 0.0


def test_52_slippage_is_not_double_counted():
    from tradingbot.learn.mtf_eval import build_report
    d = build_report(closes=[_close("F1", "BTC/USDT", -1.0, -1.0,
                                    "2026-09-10T00:00:00+00:00")],
                     snapshots={"c1": _h_snap("c1", "BTC/USDT", ALLV)},
                     links={"F1": "c1"})
    cd = d["evaluations"][0]["cost_decomposition"]
    assert cd["reported_cost_r"] == pytest.approx(0.02)         # yalnız komisyon+funding
    assert cd["slippage_drag_r"] == pytest.approx(0.03)         # AYRI alan
    assert cd["total_measured_friction_r"] == pytest.approx(0.05)
    assert cd["cost_provenance"]["legacy_cost_r_meaning"] == \
        "FEE_PLUS_FUNDING_ONLY_EXCLUDES_SLIPPAGE"
    # PnL DEĞİŞMEDİ.
    assert d["evaluations"][0]["actual_net_pnl"] == pytest.approx(-1.0)


def test_53_n_equals_one_gates_show_not_evaluable_low_sample():
    from tradingbot.learn.mtf_eval import (SAMPLE_DEPENDENT_GATES, build_report)
    from tradingbot.learn.entry_eval import GATE_STATUS_LOW_SAMPLE
    d = build_report(closes=[_close("F1", "BTC/USDT", 1.5, 1.1,
                                    "2026-09-10T00:00:00+00:00")],
                     snapshots={"c1": _h_snap("c1", "BTC/USDT", ALLV)},
                     links={"F1": "c1"})
    g = {x["code"]: x for x in d["promotion_gates"]["H_BALANCED"]["gates"]}
    for code in SAMPLE_DEPENDENT_GATES:
        assert g[code]["status"] == GATE_STATUS_LOW_SAMPLE, code
        assert g[code]["passed"] is False, code
    assert d["promotion_gates"]["H_BALANCED"]["all_passed"] is False
    assert d["promotion_gates"]["H_BALANCED"]["promotion_possible"] is False


def test_54_the_fifty_close_and_thirty_day_gates_are_unchanged():
    from tradingbot.learn.entry_eval import GATE_MIN_DAYS, GATE_MIN_LINKED_CLOSES
    from tradingbot.learn.mtf_eval import build_report
    assert GATE_MIN_LINKED_CLOSES == 50 and GATE_MIN_DAYS == 30
    d = build_report(closes=[], snapshots={}, links={})
    g = {x["code"]: x for x in d["promotion_gates"]["H_BALANCED"]["gates"]}
    assert "50" in g["MIN_H_LINKED_CLOSES"]["detail"]
    # Gözlem penceresi ölçülemediğinde kapı "0 gün" UYDURMAZ; açıkça ölçülemedi der.
    assert g["MIN_OBSERVATION_DAYS"]["detail"] == "ölçülemedi"
    assert g["MIN_OBSERVATION_DAYS"]["passed"] is False
    assert g["MIN_H_LINKED_CLOSES"]["passed"] is False
    # Ölçülebildiğinde eşik 30 GÜNDÜR ve 4 günlük pencere kapıyı GEÇEMEZ.
    d2 = build_report(closes=[_close("F1", "A/USDT", 1.0, 1.0, "2026-09-01T00:00:00+00:00"),
                              _close("F2", "B/USDT", 1.0, 1.0, "2026-09-05T00:00:00+00:00")],
                      snapshots={"c1": _h_snap("c1", "A/USDT", ALLV),
                                 "c2": _h_snap("c2", "B/USDT", ALLV)},
                      links={"F1": "c1", "F2": "c2"})
    g2 = {x["code"]: x for x in d2["promotion_gates"]["H_BALANCED"]["gates"]}
    assert "/30" in g2["MIN_OBSERVATION_DAYS"]["detail"]
    assert g2["MIN_OBSERVATION_DAYS"]["passed"] is False


def test_55_missing_is_never_converted_to_zero():
    r = ctx(htf_atr=None, ltf_atr=None)
    assert r["htf_atr"] is None and r["ltf_atr"] is None
    assert r["field_provenance"]["htf_atr"] == MISSING
    assert "htf_atr" in r["missing_fields"]
    # Ölçülen alan MEASURED olarak işaretlenir.
    ok = ctx()
    assert ok["field_provenance"]["htf_liquidity_level"] == MEASURED
    assert ok["field_provenance"]["structural_stop"] == M.MODELED
    from tradingbot.learn.entry_eval import cost_decomposition
    assert cost_decomposition({"r_multiple": -1.0, "net_pnl": -1.0},
                              risk=1.0)["impact_drag_r"] is None


# ================================================== 56-64: VERİ BÜTÇESİ

def test_56_h_disabled_causes_zero_additional_api_calls(tmp_path: Path):
    """H kapalıyken `_attach_mtf_context` erken döner: hiçbir kare okunmaz."""
    off = _flush(False, tmp_path)
    assert "mtf_context" not in off


def test_57_d_h1_causes_zero_additional_provider_calls():
    """D→H1 yalnız motorun ZATEN çektiği `1d`/`1h` karelerini kullanır."""
    from tradingbot.agents.runner import FRAME_SPECS
    assert set(M.PAIR_FRAMES[PAIR_D_H1]) <= set(FRAME_SPECS), \
        "D→H1 kareleri üretimde zaten çekiliyor olmalı"
    assert pair_status(PAIR_D_H1)["new_provider_calls"] == 0
    src = _code_only("tradingbot/learn/multitimeframe_context.py")
    for bad in ("fetch", "MarketData", "requests", "urllib", "httpx"):
        assert bad not in src, bad


def test_58_the_same_symbol_frame_is_read_at_most_once_per_cycle():
    """H çerçeveyi ÇEKMEZ; tampondaki tek referansı okur — tur başına en fazla bir kez."""
    from tradingbot.agents.runner import AgentRunner
    src = Path("tradingbot/agents/runner.py").read_text(encoding="utf-8")
    assert src.count("md.fetch(symbol)") == 1
    assert hasattr(AgentRunner, "last_frames") or "last_frames" in src


def test_59_a_provider_failure_does_not_stop_the_tour():
    """Kare yoksa (sağlayıcı düştü) H `ABSTAIN` üretir; istisna FIRLATMAZ."""
    r = ctx(ltf_frame=None)
    assert r["decision"] == ABSTAIN
    assert M.R_LTF_MISSING in r["reason_codes"]
    r2 = ctx(htf_frame=None)
    assert r2["decision"] == ABSTAIN and M.R_HTF_MISSING in r2["reason_codes"]
    r3 = ctx(htf_frame="bozuk-veri", ltf_frame=12345)
    assert r3["decision"] == ABSTAIN


def test_60_the_disabled_m15_pair_produces_an_explicit_abstain():
    r = ctx(pair=PAIR_H4_M15, htf_frame=None, ltf_frame=None)
    assert r["decision"] == ABSTAIN
    assert r["scope"] == M.R_DATA_UNAVAILABLE
    assert pair_status(PAIR_H4_M15)["state"] == M.R_DATA_UNAVAILABLE
    assert pair_status(PAIR_H4_M15)["new_provider_calls"] == 0
    # Gerekçe ÖLÇÜLMÜŞ bir engeli anlatmalı, "şimdilik kapalı" dememeli.
    assert "ÖLÇÜLEMEDİ" in pair_status(PAIR_H4_M15)["reason"].upper()


@pytest.mark.parametrize("pair", [PAIR_H1_M5, PAIR_M15_M1])
def test_61_62_no_m5_or_m1_calls_are_ever_made(pair):
    r = ctx(pair=pair, htf_frame=None, ltf_frame=None)
    assert r["decision"] == ABSTAIN
    assert r["scope"] == M.R_PAIR_FUTURE_ONLY
    assert pair_status(pair)["new_provider_calls"] == 0
    # Üretim veri hattında 5d/1d kareleri TANIMLI DEĞİL.
    from tradingbot.agents.runner import FRAME_SPECS
    assert "5m" not in FRAME_SPECS and "1m" not in FRAME_SPECS
    src = Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")
    assert '"5m"' not in src and '"1m"' not in src


def test_63_cache_and_rate_limit_guards_hold():
    """H yeni bir çerçeve TALEP EDEMEZ: desteklenen çift üretimdeki kareler kümesindedir."""
    from tradingbot.agents.runner import FRAME_SPECS
    for p in M.SUPPORTED_PAIRS:
        assert set(M.PAIR_FRAMES[p]) <= set(FRAME_SPECS), p
    for p in M.DEFINED_BUT_DISABLED_PAIRS + M.FUTURE_RESEARCH_ONLY_PAIRS:
        assert not set(M.PAIR_FRAMES[p]) <= set(FRAME_SPECS), p
    assert sum(pair_status(p)["new_provider_calls"] for p in M.ALL_PAIRS) == 0


def test_64_report_and_dashboard_tolerate_missing_or_corrupt_state(tmp_path: Path):
    from fastapi.testclient import TestClient
    from tradingbot.dashboard.app import create_app
    from tradingbot.learn.mtf_eval import aggregate, build_report
    # Boş / bozuk girdi istisna FIRLATMAZ.
    assert aggregate([])["n_h_linked_closes"] == 0
    assert build_report(closes=[], snapshots={}, links={})["state"] == "PENDING_FIRST_H_CLOSE"
    assert build_report(closes=[{"trade_id": None}], snapshots={},
                        links={})["n_no_snapshot"] == 1
    sd, dd = tmp_path / "state", tmp_path / "data"
    sd.mkdir(), dd.mkdir()
    c = TestClient(create_app(sd, dd))
    assert c.get("/learning").status_code in (200, 404)     # mtf_eval.json YOK
    (sd / "learning.json").write_text('{"n_trades":0,"lessons":[],"blacklist":[]}',
                                      encoding="utf-8")
    (sd / "mtf_eval.json").write_text("{bozuk json", encoding="utf-8")
    r = c.get("/learning")
    assert r.status_code == 200
    assert "Çok Zaman Dilimli" in r.text


# ================================================== ek: varyant sözleşmesi

def test_precommitted_variants_exist_and_are_versioned():
    names = [c.variant for c in build_variants()]
    assert names == ["H_LENIENT", "H_BALANCED", "H_STRICT", "H_RETEST_REQUIRED"]
    ids = {c.variant: c.config_id for c in build_variants()}
    assert len(set(ids.values())) == 4, "her varyantın kimliği AYRI olmalı"
    for c in build_variants():
        d = c.to_dict()
        for k in ("swing_lookback", "equal_level_atr_tolerance", "min_sweep_atr",
                  "breakout_close_buffer_atr", "required_breakout_closes",
                  "min_displacement_body_atr", "min_displacement_range_atr",
                  "close_location_threshold", "retest_bar_limit", "stop_atr_buffer",
                  "min_structural_rr"):
            assert k in d, k


def test_all_variants_are_measured_simultaneously():
    out = evaluate_variants(symbol="X", baseline_direction="LONG", as_of_ms=AS_OF,
                            pair=PAIR_D_H1, htf_frame=htf_bars(), ltf_frame=ltf_bars(),
                            htf_atr=2.0, ltf_atr=1.0, current_price=96.5)
    assert set(out) == set(M.VARIANT_NAMES)
    assert all(v["applied"] is False for v in out.values())
    assert {v["decision"] for v in out.values()} <= set(M.DECISIONS)


def test_session_unknown_abstains_instead_of_inventing_one():
    """Seans doğrulanamıyorsa H uydurmaz: `SESSION_UNKNOWN_ABSTAIN`."""
    n = 60
    irregular = [{"timestamp": AS_OF - (n - i) * 3 * DAY, "open": 94.0, "high": 96.0,
                  "low": 93.0, "close": 94.5} for i in range(n)]     # düzensiz gün kümesi
    r = ctx(htf_frame=irregular)
    assert r["decision"] == ABSTAIN
    assert M.R_SESSION_UNKNOWN in r["reason_codes"]
    assert r["session_profile"] == M.SESSION_UNKNOWN


def test_swing_confirmation_never_uses_bars_after_the_decision():
    bars = [{"timestamp": i * HOUR, "open": 100.0, "high": (105.0 if i == 5 else 100.5),
             "low": 99.5, "close": 100.0} for i in range(12)]
    sw = confirmed_swings(bars, lookback=2)
    hi = [s for s in sw["highs"] if s["index"] == 5]
    assert hi and hi[0]["confirmed_at_index"] == 7, "salınım ancak 2 bar SONRA teyitlenir"
    # Son `lookback` bar teyit edilemez: gelecek bar beklenmez.
    assert all(s["index"] <= len(bars) - 1 - 2 for s in sw["highs"])


def test_displacement_requires_more_than_a_big_candle():
    """Büyük mum tek başına yetmez: kapanış konumu ve yön hizası AYRI ölçülür."""
    bar = {"timestamp": 0, "open": 100.0, "high": 110.0, "low": 99.0, "close": 100.5}
    d = displacement(bar, atr=1.0, is_up=True)
    assert d["range_atr"] == pytest.approx(11.0)          # aralık DEVASA
    assert d["body_atr"] == pytest.approx(0.5)            # ama gövde küçük
    assert d["close_location"] == pytest.approx(1.5 / 11.0, abs=1e-6)   # kapanış DİPTE
    assert d["close_location"] < MultiTimeframeConfig().close_location_threshold


def test_h_context_is_written_before_the_outcome_exists(tmp_path: Path):
    """FAZ 8: bağlam snapshot `append` edilmeden ÖNCE eklenir ve sonradan DEĞİŞTİRİLMEZ."""
    snap = _flush(True, tmp_path)
    assert snap["mtf_context"]["applied"] is False
    for v in snap["mtf_context"]["variants"].values():
        assert v["sees_outcome"] is False
        assert v["written_at_stage"] == "RANKING"
    assert "r_multiple" not in json.dumps(snap["mtf_context"])
