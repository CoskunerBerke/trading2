# -*- coding: utf-8 -*-
"""ORTAK ANALİZ (structures_v1) — zaman, kimlik ve adlandırma sözleşmeleri. Veri SENTETİK ve etiketlidir."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from structure_fixtures import (DAY, H4, M5, _bar, bearish_engulf_confirmed, box_day, breakout_bar,  # noqa: E402
                                neutral_trend, pole_and_consolidation)

from tradingbot.chart_patterns import ChartPatternConfig, detect_chart_patterns, structure_candidates  # noqa: E402
from tradingbot.structures import analyze  # noqa: E402
from tradingbot.structures import catalog as K  # noqa: E402
from tradingbot.structures.analysis import clear_cache, closed_rows  # noqa: E402

T0 = 1_700_000_000_000 - 1_700_000_000_000 % DAY


def _an(rows, tf="1d", as_of=None, **kw):
    step = {"1d": DAY, "4h": H4, "5m": M5}[tf]
    return analyze(market=kw.pop("market", "USDM_PERP"), symbol=kw.pop("symbol", "X/USDT"), timeframe=tf, bars=rows,
                   as_of_ms=as_of if as_of is not None else rows[-1]["timestamp"] + step, **kw)


def _flag_rows():
    return pole_and_consolidation(neutral_trend(230, start_ms=T0, step=DAY, up=True, scale=0.3), step=DAY)


def _canon(an):
    return json.dumps([{k: r[k] for k in ("pattern_id", "name", "side", "status", "detected_at_ms", "confirmed_at_ms",
                                            "broken_at_ms", "trigger", "invalidation", "stop")} for r in an["records"]],
                      sort_keys=True, default=str)


# ---------------------------------------------------------------------------- zaman / yeniden boyama
def test_future_bars_do_not_change_a_past_as_of_result_and_ids_are_deterministic():
    flag = _flag_rows()
    brk = breakout_bar(flag, step=DAY, above=max(r["high"] for r in flag[-5:]))
    as_of = flag[-1]["timestamp"] + DAY
    clear_cache()
    a1 = _an(flag, as_of=as_of)
    clear_cache()
    a2 = _an(brk + neutral_trend(10, start_ms=brk[-1]["timestamp"] + DAY, step=DAY, px0=brk[-1]["close"]), as_of=as_of)
    assert a1["analysis_id"] == a2["analysis_id"] and _canon(a1) == _canon(a2), "gelecek barlar geçmiş as_of sonucunu DEĞİŞTİRDİ"
    f1 = next(r for r in a1["records"] if r["name"] == "BULL_FLAG")
    a3 = _an(brk)                                               # bir bar sonra: aynı yapı (aynı kimlik) teyitli
    f3 = next(r for r in a3["records"] if r["pattern_id"] == f1["pattern_id"])
    assert (f1["status"], f3["status"]) == ("FORMING", "CONFIRMED")
    assert f3["detected_at_ms"] == f1["detected_at_ms"], "tanınma anı sonradan değişmez"


def test_partial_bar_is_ignored_and_bar_close_boundary_is_inclusive():
    rows = neutral_trend(40, start_ms=T0, step=H4)
    last = rows[-1]["timestamp"]
    assert len(closed_rows(rows, timeframe="4h", as_of_ms=last + H4 - 1)) == 39, "1 ms önce: son bar KAPANMAMIŞ"
    assert len(closed_rows(rows, timeframe="4h", as_of_ms=last + H4)) == 40, "kapanış anında (eşitlik) kapanmış"
    partial = rows[:-1] + [dict(rows[-1], high=rows[-1]["high"] * 1.5)]      # kapanmamış barda uç sıçraması
    a_part = _an(partial, tf="4h", as_of=last + H4 - 1)
    a_ref = _an(rows[:-1], tf="4h", as_of=last + H4 - 1)
    assert _canon(a_part) == _canon(a_ref) and a_part["last_closed_bar_ts"] == rows[-2]["timestamp"]


def test_pivot_confirmed_structure_is_not_known_before_its_right_side_bars_close():
    """Çift tepe: ikinci tepe pivotu sağında k=3 kapanmış bar olmadan teyitli değildir → yapı o anda BİLİNMEZ."""
    base = neutral_trend(60, start_ms=T0, step=DAY, up=True, scale=0.3)
    p = base[-1]["close"]
    seq = [(1.00, 1.03, 0.995, 1.02), (1.02, 1.08, 1.015, 1.07), (1.07, 1.075, 1.03, 1.04), (1.04, 1.045, 1.00, 1.01),
           (1.01, 1.02, 0.985, 0.99), (0.99, 1.03, 0.985, 1.02), (1.02, 1.079, 1.015, 1.06), (1.06, 1.062, 1.03, 1.035),
           (1.035, 1.04, 1.005, 1.01), (1.01, 1.02, 0.99, 1.00)]
    rows = list(base)
    for i, (o, h, lo, c) in enumerate(seq):
        rows.append(_bar(base[-1]["timestamp"] + (i + 1) * DAY, p * o, p * h, p * lo, p * c))
    top2 = base[-1]["timestamp"] + 7 * DAY                       # ikinci tepe barı
    known_at = top2 + 3 * DAY + DAY                              # sağında 3 bar KAPANDIĞINDA (kapanış = açılış + 1d)
    early = _an(rows, as_of=known_at - 1)
    late = _an(rows, as_of=known_at)
    assert not any(r["name"] == "DOUBLE_TOP" for r in early["records"])
    dt = [r for r in late["records"] if r["name"] == "DOUBLE_TOP"]
    assert dt and all(r["detected_at_ms"] == known_at for r in dt)
    assert all(r["confirmed_at_ms"] is None or r["confirmed_at_ms"] >= r["detected_at_ms"] for r in late["records"])


# ---------------------------------------------------------------------------- kimlik ayrımı
def test_market_symbol_and_timeframe_identities_never_mix():
    rows = _flag_rows()
    a = _an(rows, market="USDM_PERP", symbol="ETH/USDT")
    b = _an(rows, market="SPOT", symbol="ETH/USDT")
    c = _an(rows, market="USDM_PERP", symbol="SOL/USDT")
    ids = {a["analysis_id"], b["analysis_id"], c["analysis_id"]}
    assert len(ids) == 3
    pids = [{r["pattern_id"] for r in x["records"]} for x in (a, b, c)]
    assert not (pids[0] & pids[1]) and not (pids[0] & pids[2])
    assert all(r["market"] == "SPOT" for r in b["records"]) and all(r["symbol"] == "SOL/USDT" for r in c["records"])
    rows4h = neutral_trend(240, start_ms=T0, step=H4)
    assert _an(rows4h, tf="4h")["timeframe"] == "4h" and all(r["timeframe"] == "4h" for r in _an(rows4h, tf="4h")["records"])


def test_same_inputs_return_the_same_versioned_result_for_every_caller():
    rows = _flag_rows()
    a, b = _an(rows), _an(list(rows))
    assert a is b, "aynı market+sembol+dilim+as_of+veri → AYNI sürümlü sonuç nesnesi (önbellek)"
    assert a["policy_version"] == K.POLICY_VERSION and a["schema_version"] == K.SCHEMA_VERSION


def test_short_history_timeframe_is_rejected_with_reasons_without_blocking_other_detectors():
    rows = neutral_trend(18, start_ms=T0, step=H4)
    an = _an(rows, tf="4h")
    rej = {r["detector"]: r for r in an["rejects"]}
    assert rej["compression"] == {"detector": "compression", "reason": "NOT_ENOUGH_BARS", "have": 18,
                                  "need": K.REQUIREMENTS["compression"]}, "eksik geçmiş GEREKÇELİ reddedilir"
    assert "chart" not in rej and "candle" not in rej, "bir dedektörün eksikliği ondan bağımsız dedektörleri ENGELLEMEZ"
    short = _an(neutral_trend(10, start_ms=T0, step=H4), tf="4h")
    assert {r["detector"] for r in short["rejects"]} >= {"chart", "compression"}


# ---------------------------------------------------------------------------- adlandırma
@pytest.mark.parametrize("shape,trend,name,side", [
    ("HAMMER_LIKE", "DOWNTREND", "HAMMER", "LONG"), ("HAMMER_LIKE", "UPTREND", "HANGING_MAN", "SHORT"),
    ("INVERTED_HAMMER_LIKE", "DOWNTREND", "INVERTED_HAMMER", "LONG"), ("INVERTED_HAMMER_LIKE", "UPTREND", "SHOOTING_STAR", "SHORT"),
    ("HAMMER_LIKE", "RANGE", "HAMMER_SHAPE_NO_TREND", None), ("DOJI_LIKE", "UPTREND", "DOJI", None)])
def test_candle_names_are_resolved_by_context_and_contextless_shapes_have_no_side(shape, trend, name, side):
    assert K.candle_name(shape, trend) == (name, side)


def test_evening_star_uses_the_canonical_colour_order():
    """Kanonik akşam yıldızı: 1. uzun BOĞA, 2. küçük gövde, 3. 1. gövdenin ortasının ALTINDA kapanan AYI."""
    from tradingbot.learn.candle_context import CandleContextConfig, _shapes
    base = neutral_trend(20, start_ms=T0, step=H4)
    p = base[-1]["close"]
    t = base[-1]["timestamp"]
    good = base + [_bar(t + H4, p, p * 1.03, p * 0.999, p * 1.028), _bar(t + 2 * H4, p * 1.03, p * 1.034, p * 1.026, p * 1.031),
                   _bar(t + 3 * H4, p * 1.029, p * 1.03, p * 1.005, p * 1.008)]
    swapped = base + [_bar(t + H4, p * 1.028, p * 1.03, p * 0.999, p), _bar(t + 2 * H4, p * 1.03, p * 1.034, p * 1.026, p * 1.031),
                      _bar(t + 3 * H4, p * 1.008, p * 1.03, p * 1.005, p * 1.029)]
    assert "EVENING_STAR_LIKE" in _shapes(good, CandleContextConfig())
    assert "EVENING_STAR_LIKE" not in _shapes(swapped, CandleContextConfig()), "ters renk sırası akşam yıldızı DEĞİLDİR"


def test_flag_and_pennant_are_separate_names():
    base = neutral_trend(230, start_ms=T0, step=DAY, up=True, scale=0.3)
    flag = _an(pole_and_consolidation(base, step=DAY, shape="parallel"))
    pen = _an(pole_and_consolidation(base, step=DAY, shape="converging"))
    assert {r["name"] for r in flag["records"] if r["family"] == "chart"} == {"BULL_FLAG"}
    assert {r["name"] for r in pen["records"] if r["family"] == "chart"} == {"BULL_PENNANT"}


def test_confirmed_opposing_candle_has_trigger_invalidation_stop_and_no_probability():
    rows = bearish_engulf_confirmed(neutral_trend(230, start_ms=T0, step=DAY, up=True, scale=0.3), step=DAY)
    an = _an(rows)
    eng = next(r for r in an["records"] if r["name"] == "BEARISH_ENGULFING")
    assert eng["status"] == "CONFIRMED" and eng["side"] == "SHORT"
    assert eng["trigger"]["rule"] == "close_below" and eng["invalidation"]["rule"] == "close_above"
    assert eng["stop"] > eng["invalidation"]["level"], "SHORT stop = geçersizlik + tampon"
    for r in an["records"]:
        assert "p_win" not in r and "expected_return" not in r and 0.0 <= float(r["geometry_quality"]) <= 1.0
        for k in ("pattern_id", "family", "side", "market", "symbol", "timeframe", "policy_version", "anchors", "detected_at_ms",
                  "confirmed_at_ms", "as_of_ms", "status", "trigger", "invalidation", "stop", "targets", "expires_at_ms",
                  "data_provenance", "geometry_quality", "reason_codes"):
            assert k in r, k


def test_sweep_reclaim_and_range_breakout_are_measured_without_intent_claims():
    day0 = T0 + 300 * DAY
    refs = [{"name": "BOX_HIGH", "level": 105.0, "kind": "high", "valid_from_ms": day0},
            {"name": "BOX_LOW", "level": 95.0, "kind": "low", "valid_from_ms": day0}]
    _, sw = box_day(day0=day0, ending="sweep")
    _, bo = box_day(day0=day0, ending="breakout")
    s = next(r for r in _an(sw, tf="5m", reference_levels=refs)["records"] if r["name"] == "SWEEP_RECLAIM")
    b = next(r for r in _an(bo, tf="5m", reference_levels=refs)["records"] if r["name"] == "RANGE_BREAKOUT")
    assert (s["side"], s["status"], s["reference"]) == ("SHORT", "CONFIRMED", "BOX_HIGH") and "NO_INTENT_CLAIM" in s["reason_codes"]
    assert (b["side"], b["status"], b["reference"]) == ("LONG", "CONFIRMED", "BOX_HIGH") and b["overshoot_closes"] >= 2


def test_legacy_detector_output_is_unchanged_by_the_candidate_refactor():
    """`detect_chart_patterns` (ana bot p2 vetosu, panel) aday üreteçlerinden AYNI çıktıyı üretir; adaylar kırılmamış
    yapıları da içerir. Gerçek arşivde 5 170 pencerelik eşdeğerlik: docs/review/evidence-2026-09-22-shared/."""
    rows = _flag_rows()
    brk = breakout_bar(rows, step=DAY, above=max(r["high"] for r in rows[-5:]))
    det = detect_chart_patterns(brk, ChartPatternConfig())
    assert [p["pattern"] for p in det["patterns"]] == ["BULL_FLAG"]
    cands = structure_candidates(rows, ChartPatternConfig())["candidates"]
    assert any(c["pattern"] == "BULL_FLAG" and c.get("break_index") is None for c in cands)
