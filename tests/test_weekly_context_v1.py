"""WEEKLY_MARKET_STRUCTURE_AND_CONTEXTUAL_PRICE_ACTION_V1 — 40 zorunlu regresyon.

Sözleşme (görev metniyle birebir):
 1-9   Haftalık sınır, rollover, seans, kapanmamış/gelecek bar, eksik veri.
10-15  Süpürme/geri alma sınıflandırması ve LONG/SHORT simetrisi.
16-19  Mum bağlamı: aynı şekil ≠ aynı anlam; doji yön iddiası üretmez.
20-25  Point-in-time, kimlik, tekilleştirme, restart, sıfıra düşürmeme.
26-30  Aktif karar/RiskEngine/fingerprint değişmezliği, izolasyon, mod reddi.
31-32  Maliyet düzeltilmiş R:R matematiği ve maliyet provenance'ı.
33-36  Arşiv-önce rotasyon, çökme, sıcak+arşiv, rotasyon boyunca bağ.
37-40  Bozuk yedek deployment'ı durdurur, fingerprint alan sözleşmesi, panel, API güvenliği.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradingbot.core import ConfigError, utc_now
from tradingbot.learn.candle_context import (BEARISH_ENGULFING_LIKE, BULLISH_ENGULFING_LIKE,
                                             CONFIRMATION_UNKNOWN, CONTEXTUAL_ALIASES,
                                             DIRECTIONAL_CLAIM_NONE, DOJI_LIKE, HAMMER_LIKE,
                                             INVERTED_HAMMER_LIKE, THREE_BLACK_CROWS_LIKE,
                                             THREE_WHITE_SOLDIERS_LIKE, TREND_DOWN, TREND_UP,
                                             CandleContextConfig, build_candle_context,
                                             candle_metrics)
from tradingbot.learn.entry_challenger_v2 import (ABSTAIN, ALLOW, BLOCK, FAM_STRUCT,
                                                  FAM_WEEKLY, UNKNOWN,
                                                  WeeklyChallengerConfig, build_variants,
                                                  candle_confidence_delta, challenger_f,
                                                  challenger_g, cost_adjusted_rr,
                                                  evaluate_all_variants, structural_plan)
from tradingbot.learn.entry_eval_v2 import (GATE_MAX_ABSTAIN_RATE, GATE_MIN_WEEKLY_COVERAGE,
                                            build_report_v2, evaluate_trade_v2, outcome_id_v2)
from tradingbot.learn.weekly_structure import (ACCEPTED_BREAKOUT, BREAKOUT_UNCONFIRMED,
                                               DATA_UNAVAILABLE, DQ_OK, DQ_UNAVAILABLE,
                                               HIGH_SWEEP_RECLAIM, LOW_SWEEP_RECLAIM,
                                               NO_INTERACTION, SESSION_CRYPTO_CONTINUOUS,
                                               SESSION_UNKNOWN, SESSION_WEEKDAY, TOUCH_ONLY,
                                               WeeklyStructureConfig, build_weekly_structure,
                                               classify_level_interaction, infer_session_profile,
                                               iso_week_id, previous_completed_week,
                                               week_start_utc)

WCFG = WeeklyStructureConfig()
CCFG = CandleContextConfig()
XCFG = WeeklyChallengerConfig()
MS = 1000.0


def _d(y, m, d, hh=0):
    return datetime(y, m, d, hh, tzinfo=timezone.utc)


def _daily(start: datetime, n: int, *, step_days: int = 1, base: float = 100.0,
           drift: float = 0.5, weekdays_only: bool = False):
    rows, i = [], 0
    t = start
    while len(rows) < n:
        if not (weekdays_only and t.weekday() >= 5):
            b = base + drift * i
            rows.append({"timestamp": t.timestamp() * MS, "open": b, "high": b + 2,
                         "low": b - 2, "close": b + 0.5})
            i += 1
        t += timedelta(days=step_days)
    return rows


def _bar(ts, o, h, lo, c):
    return {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c}


# ------------------------------------------------------------------ 1-9: hafta sınırı / veri

def test_01_previous_completed_week_boundaries_are_exact():
    rows = _daily(_d(2026, 8, 10), 21)                 # 3 tam hafta, Pazartesi başlangıç
    now = _d(2026, 8, 31, 12)                          # yeni haftanın Pazartesi'si
    w = previous_completed_week(rows, now, cfg=WCFG)
    assert w["previous_week_id"] == "2026-W35"
    assert w["data_quality"] == DQ_OK and w["available_bars"] == 7
    start = week_start_utc(now)
    assert start == _d(2026, 8, 31) and start.weekday() == 0
    # Önceki haftanın barları [24 Ağu, 31 Ağu) aralığında olmalı.
    assert w["source_first_timestamp_ms"] >= _d(2026, 8, 24).timestamp() * MS
    assert w["source_last_timestamp_ms"] < start.timestamp() * MS


def test_02_monday_rollover_moves_the_reference_week():
    rows = _daily(_d(2026, 8, 10), 21)
    sun = previous_completed_week(rows, _d(2026, 8, 30, 23), cfg=WCFG)
    mon = previous_completed_week(rows, _d(2026, 8, 31, 0), cfg=WCFG)
    assert sun["previous_week_id"] == "2026-W34"
    assert mon["previous_week_id"] == "2026-W35", "Pazartesi 00:00 UTC'de hafta DÖNMEDİ"
    assert sun["previous_completed_week_high"] != mon["previous_completed_week_high"]


def test_03_year_rollover_keeps_iso_week_identity():
    rows = _daily(_d(2025, 12, 15), 28)
    w = previous_completed_week(rows, _d(2026, 1, 5, 6), cfg=WCFG)
    assert w["previous_week_id"] == iso_week_id(_d(2025, 12, 29))
    assert w["previous_week_id"].startswith("2026-W01") or \
        w["previous_week_id"].startswith("2025-W")
    assert w["data_quality"] in (DQ_OK, "PARTIAL")


def test_04_session_profile_is_measured_not_assumed():
    crypto = infer_session_profile(_daily(_d(2026, 8, 3), 28))
    assert crypto["profile"] == SESSION_CRYPTO_CONTINUOUS
    assert crypto["expected_bars_per_week"] == 7 and crypto["has_weekend_bars"] is True
    stock = infer_session_profile(_daily(_d(2026, 8, 3), 20, weekdays_only=True))
    assert stock["profile"] == SESSION_WEEKDAY and stock["expected_bars_per_week"] == 5
    assert stock["has_weekend_bars"] is False


def test_05_irregular_feed_fails_to_unknown_not_to_a_guess():
    rows = (_daily(_d(2026, 8, 3), 7) + _daily(_d(2026, 8, 12), 3)
            + _daily(_d(2026, 8, 18), 6) + _daily(_d(2026, 8, 25), 2))
    sess = infer_session_profile(rows)
    assert sess["profile"] == SESSION_UNKNOWN
    w = previous_completed_week(rows, _d(2026, 9, 2), cfg=WCFG)
    assert w["data_quality"] == DQ_UNAVAILABLE
    assert "SESSION_PROFILE_UNKNOWN" in str(w["unavailable_reason"])
    assert w["available"] is False


def test_06_current_incomplete_week_is_excluded():
    rows = _daily(_d(2026, 8, 24), 12)                 # 24-31 Ağu + yeni haftanın günleri
    now = _d(2026, 9, 3, 12)                           # Perşembe, hafta yarım
    w = previous_completed_week(rows, now, cfg=WCFG)
    assert w["previous_week_id"] == "2026-W35"
    cur_start = week_start_utc(now).timestamp() * MS
    assert w["source_last_timestamp_ms"] < cur_start, "mevcut haftanın barı kullanıldı"


def test_07_unclosed_and_future_bars_are_rejected():
    rows = _daily(_d(2026, 8, 24), 7)
    now = _d(2026, 8, 31, 12)
    rows.append(_bar(now.timestamp() * MS, 999, 9999, 1, 999))          # kapanmamış
    rows.append(_bar((now + timedelta(days=3)).timestamp() * MS, 1, 5000, 1, 1))  # gelecek
    w = previous_completed_week(rows, now, cfg=WCFG)
    assert w["excluded_unclosed_bars"] >= 1 and w["excluded_future_bars"] >= 1
    assert w["previous_completed_week_high"] < 500, "gelecek/kapanmamış bar sızdı"


def test_08_missing_weekly_bars_degrade_quality_and_are_reported():
    rows = [r for i, r in enumerate(_daily(_d(2026, 8, 24), 7)) if i not in (2, 4)]
    rows = _daily(_d(2026, 8, 10), 14) + rows
    w = previous_completed_week(rows, _d(2026, 8, 31, 12), cfg=WCFG)
    assert w["available_bars"] == 5 and w["expected_bars"] == 7
    assert w["data_quality"] == "PARTIAL" and "MISSING_BARS" in str(w["unavailable_reason"])
    assert w["gap_count"] == 2


def test_09_no_bars_before_current_week_is_unavailable():
    w = previous_completed_week([], _d(2026, 8, 31), cfg=WCFG)
    assert w["available"] is False and w["data_quality"] == DQ_UNAVAILABLE
    assert w["unavailable_reason"] == "NO_BARS_BEFORE_CURRENT_WEEK"


# ------------------------------------------------------------------ 10-15: süpürme/geri alma

def _inter(level, seq, atr=1.0, side="high"):
    bars = [_bar(1000 + i, *ohlc) for i, ohlc in enumerate(seq)]
    return classify_level_interaction(level=level, bars=bars, side=side, atr=atr, cfg=WCFG)


def test_10_touch_without_meaningful_excess_is_not_a_sweep():
    r = _inter(100.0, [(99, 100.02, 98, 99.5)])
    assert r["classification"] == TOUCH_ONLY
    assert r["touched"] is True and r["swept"] is False


def test_11_sweep_without_reclaim_is_not_a_sweep_reclaim():
    r = _inter(100.0, [(99, 100.3, 98.5, 100.2), (100.2, 100.4, 100.1, 100.35)])
    assert r["classification"] == BREAKOUT_UNCONFIRMED
    assert r["swept"] is True and r["close_returned"] is False
    assert r["reclaim_confirmed"] in (None, False)


def test_12_confirmed_high_sweep_reclaim_is_classified():
    r = _inter(100.0, [(99.5, 99.8, 99, 99.6), (99.6, 100.3, 99.4, 99.7), (99.7, 99.9, 99.2, 99.4)])
    assert r["classification"] == HIGH_SWEEP_RECLAIM
    assert r["swept"] and r["close_returned"] and r["reclaim_confirmed"]
    assert r["sweep_distance_atr"] >= WCFG.min_sweep_atr
    assert r["rejection_wick_ratio"] is not None


def test_13_accepted_breakout_is_never_mislabeled_as_a_sweep():
    r = _inter(100.0, [(99.5, 101.5, 99.4, 101.2), (101.2, 101.9, 101.0, 101.7)])
    assert r["classification"] == ACCEPTED_BREAKOUT
    assert r["classification"] not in (HIGH_SWEEP_RECLAIM, LOW_SWEEP_RECLAIM)
    assert XCFG.accepted_breakout_never_sweep is True
    with pytest.raises(ValueError):
        WeeklyChallengerConfig.from_dict({"accepted_breakout_never_sweep": False})


def test_14_low_sweep_reclaim_mirrors_the_high_case():
    r = _inter(100.0, [(100.5, 101, 100.2, 100.4), (100.4, 100.6, 99.7, 100.3),
                       (100.3, 100.8, 100.1, 100.6)], side="low")
    assert r["classification"] == LOW_SWEEP_RECLAIM
    assert r["swept"] and r["reclaim_confirmed"]


def test_15_long_short_symmetry_of_the_family_decision():
    weekly = {"week_available": True, "data_quality": DQ_OK,
              "high_interaction": {"classification": HIGH_SWEEP_RECLAIM,
                                   "sweep_distance_atr": 0.4, "reclaim_confirmed": True},
              "low_interaction": {"classification": NO_INTERACTION}}
    long_snap = {"candidate_id": "a", "direction": "LONG", "baseline_accepted": True}
    short_snap = {"candidate_id": "b", "direction": "SHORT", "baseline_accepted": True}
    f_long = challenger_f(long_snap, weekly, XCFG)
    f_short = challenger_f(short_snap, weekly, XCFG)
    assert f_long["decision"] == BLOCK and f_short["decision"] == ALLOW
    mirrored = {"week_available": True, "data_quality": DQ_OK,
                "low_interaction": {"classification": LOW_SWEEP_RECLAIM,
                                    "sweep_distance_atr": 0.4, "reclaim_confirmed": True},
                "high_interaction": {"classification": NO_INTERACTION}}
    assert challenger_f(short_snap, mirrored, XCFG)["decision"] == BLOCK
    assert challenger_f(long_snap, mirrored, XCFG)["decision"] == ALLOW


# ------------------------------------------------------------------ 16-19: mum bağlamı

def _trend(down=True, n=10):
    return [_bar(i, 110 - i if down else 90 + i, (110 - i if down else 90 + i) + 1.5,
                 (110 - i if down else 90 + i) - 1.5, (109 - i if down else 91 + i))
            for i in range(n)]


def test_16_hammer_and_hanging_man_share_a_shape_but_not_a_context():
    shape = _bar(99, 100, 100.3, 96.0, 99.8)
    down = build_candle_context(bars=_trend(True) + [shape], atr=1.0, cfg=CCFG)
    up = build_candle_context(bars=_trend(False) + [shape], atr=1.0, cfg=CCFG)
    assert HAMMER_LIKE in down["pattern_shapes"] and HAMMER_LIKE in up["pattern_shapes"]
    assert down["trend_context"] == TREND_DOWN and up["trend_context"] == TREND_UP
    assert down["contextual_aliases"][HAMMER_LIKE] == "HAMMER"
    assert up["contextual_aliases"][HAMMER_LIKE] == "HANGING_MAN"
    assert down["directional_claim"] == up["directional_claim"] == DIRECTIONAL_CLAIM_NONE


def test_17_inverted_hammer_and_shooting_star_need_context_too():
    shape = _bar(99, 100, 104.0, 99.8, 100.2)
    down = build_candle_context(bars=_trend(True) + [shape], atr=1.0, cfg=CCFG)
    up = build_candle_context(bars=_trend(False) + [shape], atr=1.0, cfg=CCFG)
    assert INVERTED_HAMMER_LIKE in down["pattern_shapes"]
    assert down["contextual_aliases"][INVERTED_HAMMER_LIKE] == "INVERTED_HAMMER"
    assert up["contextual_aliases"][INVERTED_HAMMER_LIKE] == "SHOOTING_STAR"
    assert CONTEXTUAL_ALIASES[INVERTED_HAMMER_LIKE]["UPTREND"] == "SHOOTING_STAR"


def test_18_doji_never_produces_a_directional_claim():
    ctx = build_candle_context(bars=_trend(True) + [_bar(99, 100, 102, 98, 100.02)],
                               atr=1.0, cfg=CCFG)
    assert DOJI_LIKE in ctx["pattern_shapes"]
    assert ctx["directional_claim"] == DIRECTIONAL_CLAIM_NONE
    blob = json.dumps(ctx, default=str).upper()
    for banned in ('"BUY"', '"SELL"', '"AL"', '"SAT"', "REVERSAL_CONFIRMED"):
        assert banned not in blob, f"yön etiketi sızdı: {banned}"
    # Tek taraflı olmayan şekil teyit yönü ÜRETEMEZ.
    d = candle_confidence_delta({"confirmed_pattern_shapes": [DOJI_LIKE],
                                 "confirmation_state": "CONFIRMED"}, is_long=True)
    assert d["delta"] == 0.0 and d["reason"] == "SHAPE_HAS_NO_SINGLE_SIDE"


def test_19_engulfing_requires_a_valid_preceding_candle():
    solo = build_candle_context(bars=[_bar(1, 100, 105, 99, 104)], atr=1.0, cfg=CCFG)
    assert BULLISH_ENGULFING_LIKE not in solo["pattern_shapes"]
    pair = build_candle_context(bars=[_bar(1, 102, 102.5, 100.5, 100.8),
                                      _bar(2, 100.3, 103.5, 100.0, 103.0)], atr=1.0, cfg=CCFG)
    assert BULLISH_ENGULFING_LIKE in pair["pattern_shapes"]
    bear = build_candle_context(bars=[_bar(1, 100.5, 102.5, 100.2, 102.2),
                                      _bar(2, 102.5, 102.8, 99.8, 100.0)], atr=1.0, cfg=CCFG)
    assert BEARISH_ENGULFING_LIKE in bear["pattern_shapes"]
    assert "THREE_WHITE_CROWS_LIKE" not in json.dumps(pair), "yanlış formasyon adı"
    assert THREE_WHITE_SOLDIERS_LIKE.endswith("SOLDIERS_LIKE")
    assert THREE_BLACK_CROWS_LIKE.endswith("CROWS_LIKE")


# ------------------------------------------------------------------ 20-25: point-in-time

def _snap(**over):
    base = {"candidate_id": "c1", "decision_id": "d1", "symbol": "ETH/USDT",
            "direction": "LONG", "baseline_accepted": True, "entry_price": 100.0,
            "stop_price": 96.0, "atr_pct": 2.0, "targets": [110.0],
            "expected_cost_pct": 0.2, "est_slippage_pct": 0.03, "funding_rate": 0.0001,
            "link_status": "LINKED", "ts": utc_now().isoformat(),
            "provenance": {"written_at_stage": "RANKING", "sees_outcome": False}}
    base.update(over)
    return base


def _close(tid="T1", r=-1.0, when=None):
    ts = (when or utc_now()).isoformat()
    return {"close_event_id": f"ce-{tid}", "trade_id": tid, "symbol": "ETH/USDT",
            "side": "LONG", "opened_at": ts, "closed_at": ts, "exit_reason": "stop",
            "r_multiple": r, "net_pnl": r * 2.0, "fees": 0.04, "funding": 0.01,
            "raw": {"meta": {"risk_snapshot": {"max_loss_at_stop_usdt": 2.0}}}}


def test_20_weekly_snapshot_contains_no_outcome_fields():
    w = build_weekly_structure(symbol="ETH/USDT", direction="LONG", now=_d(2026, 8, 31, 12),
                               daily_frame=_daily(_d(2026, 8, 10), 21),
                               intraweek_frame=[], current_price=105.0, atr=1.0, cfg=WCFG)
    for k in ("r_multiple", "net_pnl", "closed_at", "exit_reason", "won", "outcome"):
        assert k not in w, f"sonuç alanı sızdı: {k}"
    assert w["provenance"]["sees_outcome"] is False
    assert w["provenance"]["uses_future_bar"] is False
    ctx = build_candle_context(bars=_trend(True), atr=1.0, cfg=CCFG)
    for k in ("r_multiple", "net_pnl", "outcome"):
        assert k not in ctx


def test_21_forward_labels_are_absent_from_the_decision_snapshot():
    """İleriye dönük getiriler karar snapshot'ına GİREMEZ."""
    w = build_weekly_structure(symbol="X", direction="LONG", now=_d(2026, 8, 31),
                               daily_frame=_daily(_d(2026, 8, 10), 21),
                               intraweek_frame=[], current_price=100.0, atr=1.0, cfg=WCFG)
    blob = json.dumps(w, default=str)
    for banned in ("forward_return", "label_available_at", "future_close", "r_1", "r_3", "r_6"):
        assert banned not in blob, f"ileri etiket karar snapshot'ında: {banned}"


def test_22_deterministic_identifiers():
    a = outcome_id_v2("ce1", "cand1", FAM_WEEKLY, "base")
    b = outcome_id_v2("ce1", "cand1", FAM_WEEKLY, "base")
    assert a == b
    assert a != outcome_id_v2("ce1", "cand1", FAM_WEEKLY, "strict_sweep")
    assert a != outcome_id_v2("ce1", "cand1", FAM_STRUCT, "base")
    w1 = build_weekly_structure(symbol="X", direction="LONG", now=_d(2026, 8, 31),
                                daily_frame=_daily(_d(2026, 8, 10), 21), intraweek_frame=[],
                                current_price=100.0, atr=1.0, cfg=WCFG)
    w2 = build_weekly_structure(symbol="X", direction="LONG", now=_d(2026, 8, 31),
                                daily_frame=_daily(_d(2026, 8, 10), 21), intraweek_frame=[],
                                current_price=100.0, atr=1.0, cfg=WCFG)
    assert w1["config_id"] == w2["config_id"]
    assert json.dumps(w1, sort_keys=True) == json.dumps(w2, sort_keys=True)


def test_23_restart_idempotency_of_the_v2_report():
    snaps = {"c1": _snap()}
    links = {"T1": "c1"}
    closes = [_close()]
    a = build_report_v2(closes=closes, snapshots=snaps, links=links)
    b = build_report_v2(closes=closes, snapshots=snaps, links=links)
    del a["generated_at"], b["generated_at"]
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True,
                                                                   default=str)


def test_24_duplicate_closes_are_suppressed():
    snaps, links = {"c1": _snap()}, {"T1": "c1"}
    doc = build_report_v2(closes=[_close(), _close()], snapshots=snaps, links=links)
    base = doc["variants"]["base"]
    assert base["n_evaluated"] == 1, "aynı kapanış iki kez sayıldı"


def test_25_missing_fields_are_never_coerced_to_zero():
    bare = _snap(entry_price=None, stop_price=None, atr_pct=None, targets=[],
                 expected_cost_pct=None, est_slippage_pct=None, funding_rate=None)
    plan = structural_plan(bare, None, XCFG)
    assert plan["stop_distance"] is None and plan["gross_reward_to_risk"] is None
    assert plan["structure_quality"] == "UNAVAILABLE"
    cost = cost_adjusted_rr(plan, bare)
    assert cost["cost_adjusted_reward_to_risk"] is None and cost["measured"] is False
    g = challenger_g(bare, None, XCFG)
    assert g["decision"] == UNKNOWN and g["decision"] != BLOCK
    m = candle_metrics({"open": None, "high": 1, "low": 1, "close": 1})
    assert m["body_to_range_ratio"] is None and m["data_quality"] == "UNAVAILABLE"


# ------------------------------------------------------------------ 26-30: izolasyon

import hashlib  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as E  # noqa: E402


def _fp(led) -> str:
    from tradingbot.ops.fingerprint import futures_fingerprint
    return futures_fingerprint(led.positions)["fingerprint"]


def test_26_active_decisions_identical_with_layer_enabled_and_disabled(tmp_path, monkeypatch):
    a = E._engine(tmp_path / "a", monkeypatch, symbols=4)
    sa = a.tour(do_scan=False, obsidian=False, charts=False)
    ra = json.loads((a.cfg.state_path / "risk.json").read_text(encoding="utf-8"))
    b = E._engine(tmp_path / "b", monkeypatch, symbols=4)
    b.weekly_cfg = None                     # haftalık bağlam KAPALI
    b.candle_cfg = None
    b.weekly_challenger_cfg = None
    sb = b.tour(do_scan=False, obsidian=False, charts=False)
    rb = json.loads((b.cfg.state_path / "risk.json").read_text(encoding="utf-8"))

    def _norm(rows):
        return [{k: v for k, v in r.items() if k not in ("at", "trade_id")} for r in rows]
    assert _norm(ra["last_decisions"]) == _norm(rb["last_decisions"])
    assert sa["opened"] == sb["opened"] and sa["ledger"]["equity"] == sb["ledger"]["equity"]


def test_27_risk_engine_results_are_identical(tmp_path, monkeypatch):
    a = E._engine(tmp_path / "a", monkeypatch, symbols=4)
    a.tour(do_scan=False, obsidian=False, charts=False)
    ra = json.loads((a.cfg.state_path / "risk.json").read_text(encoding="utf-8"))
    b = E._engine(tmp_path / "b", monkeypatch, symbols=4)
    b.weekly_cfg = None
    b.tour(do_scan=False, obsidian=False, charts=False)
    rb = json.loads((b.cfg.state_path / "risk.json").read_text(encoding="utf-8"))
    ka = [{k: v for k, v in d.items() if k in ("symbol", "risk_allowed", "risk_reasons",
                                               "adjusted_notional", "adjusted_leverage")}
          for d in ra["last_decisions"]]
    kb = [{k: v for k, v in d.items() if k in ("symbol", "risk_allowed", "risk_reasons",
                                               "adjusted_notional", "adjusted_leverage")}
          for d in rb["last_decisions"]]
    assert ka == kb


def test_28_position_fingerprint_unchanged_by_the_research_layer(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert eng.ledger2.positions
    before = _fp(eng.ledger2)
    raw = (eng.cfg.state_path / "futures_ledger.json").read_bytes()
    for _ in range(3):
        eng._write_entry_eval(utc_now())
    assert _fp(eng.ledger2) == before
    assert (eng.cfg.state_path / "futures_ledger.json").read_bytes() == raw


@pytest.mark.parametrize("mod", ["weekly_structure.py", "candle_context.py",
                                 "entry_challenger_v2.py", "entry_eval_v2.py"])
def test_29_research_modules_cannot_import_the_order_path(mod):
    import ast
    src = Path("tradingbot/learn").joinpath(mod).read_text(encoding="utf-8")
    names: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    joined = " ".join(names)
    for banned in ("execution", "gateway", "accounting", "outbox", "notify", "risk.engine",
                   "paper_futures", "ledger", "engine_v3"):
        assert banned not in joined, f"yasak bağımlılık: {banned} ({names})"


@pytest.mark.parametrize("bad", [
    {"mode": "PAPER_BOUNDED"}, {"mode": "ACTIVE"}, {"auto_promotion": True},
    {"weekly_challenger_policy": {"accepted_breakout_never_sweep": False}},
    {"weekly_challenger_policy": {"min_cost_adjusted_rr": 0}},
    {"weekly_structure_policy": {"min_sweep_atr": 0}},
    {"candle_policy": {"doji_body_ratio": 0.9}},
])
def test_30_active_modes_and_invalid_policies_are_rejected(bad):
    from tradingbot.config_v3 import load_v3, validate_v3
    with pytest.raises(ConfigError):
        validate_v3(load_v3({"entry_selectivity": bad}))


# ------------------------------------------------------------------ 31-32: maliyet matematiği

def test_31_cost_adjusted_rr_math_is_exact():
    snap = _snap(entry_price=100.0, stop_price=96.0, targets=[110.0],
                 expected_cost_pct=0.2, est_slippage_pct=0.05, funding_rate=0.001)
    plan = structural_plan(snap, None, XCFG)
    assert plan["stop_distance"] == pytest.approx(4.0)
    assert plan["target_distance"] == pytest.approx(10.0)
    assert plan["gross_reward_to_risk"] == pytest.approx(2.5)
    cost = cost_adjusted_rr(plan, snap)
    assert cost["fee_drag_r"] == pytest.approx(0.2 / 100 * 100 / 4.0)      # 0.05
    assert cost["slippage_drag_r"] == pytest.approx(0.05 / 100 * 100 / 4.0)  # 0.0125
    assert cost["funding_drag_r"] == pytest.approx(0.001 * 100 / 4.0)      # 0.025
    assert cost["total_drag_r"] == pytest.approx(0.0875)
    assert cost["cost_adjusted_reward_to_risk"] == pytest.approx(2.5 - 0.0875)
    assert cost["measured"] is True


def test_32_cost_provenance_is_recorded_and_partial_costs_do_not_fake_completeness():
    snap = _snap(est_slippage_pct=None, funding_rate=None)
    plan = structural_plan(snap, None, XCFG)
    cost = cost_adjusted_rr(plan, snap)
    assert cost["cost_provenance"]["est_slippage_pct"] is None
    assert cost["slippage_drag_r"] is None and cost["funding_drag_r"] is None
    assert cost["fee_drag_r"] is not None
    nocost = cost_adjusted_rr(plan, _snap(expected_cost_pct=None, est_slippage_pct=None,
                                          funding_rate=None))
    assert nocost["total_drag_r"] is None and nocost["reason"] == "NO_COST_INPUT_MEASURED"
    g = challenger_g(_snap(expected_cost_pct=None, est_slippage_pct=None, funding_rate=None),
                     None, XCFG)
    assert g["decision"] in (ABSTAIN, UNKNOWN) and g["decision"] != BLOCK


# ------------------------------------------------------------------ 33-36: rotasyon (bkz. ayrı dosya)

def test_33_archive_first_rotation_contract_is_enforced(tmp_path):
    from tradingbot.learn.entry_snapshot import (ENTRY_ARCHIVE_STREAM_ID, EntrySnapshotStore,
                                                 build_entry_snapshot)
    from tradingbot.learn.journal_archive import SegmentArchive
    arc = SegmentArchive(tmp_path / "arc", stream_id=ENTRY_ARCHIVE_STREAM_ID)
    st = EntrySnapshotStore(tmp_path / "s.jsonl", archive=arc, max_lines=3)
    ids = []
    for i in range(9):
        s = build_entry_snapshot(run_id="R", cycle_id=f"c{i}", symbol="X/USDT",
                                 direction="LONG", now=utc_now())
        st.append(s)
        ids.append(s["candidate_id"])
    res = st.rotate()
    assert res["archived"] == 6 and res["health"] == "OK"
    assert set(st.by_candidate(include_archive=True)) == set(ids)
    assert st.retention_stats()["silent_deletion"] is False


def test_34_crash_during_rotation_neither_loses_nor_duplicates(tmp_path):
    from tradingbot.learn.entry_snapshot import (ENTRY_ARCHIVE_STREAM_ID, EntrySnapshotStore,
                                                 build_entry_snapshot)
    from tradingbot.learn.journal_archive import SegmentArchive

    def store():
        return EntrySnapshotStore(tmp_path / "s.jsonl", max_lines=3,
                                  archive=SegmentArchive(tmp_path / "arc",
                                                         stream_id=ENTRY_ARCHIVE_STREAM_ID))
    st = store()
    ids = []
    for i in range(9):
        s = build_entry_snapshot(run_id="R", cycle_id=f"c{i}", symbol="X/USDT",
                                 direction="LONG", now=utc_now())
        st.append(s)
        ids.append(s["candidate_id"])
    lines = st._hot_lines()                                      # noqa: SLF001
    meta = st.archive.seal(lines[:6])
    st.archive.commit(meta, pending_trim={"segment_id": meta["segment_id"], "n_lines": 6,
                                          "block_sha256": meta["block_sha256"]})
    st2 = store()                                                # ÇÖKME sonrası yeniden aç
    st2.rotate()
    assert set(st2.by_candidate(include_archive=True)) == set(ids)
    assert len(st2._hot_lines()) == 3                            # noqa: SLF001


def test_35_hot_plus_archive_retrieval_is_complete(tmp_path):
    from tradingbot.learn.entry_snapshot import (ENTRY_ARCHIVE_STREAM_ID, EntrySnapshotStore,
                                                 build_entry_snapshot)
    from tradingbot.learn.journal_archive import SegmentArchive
    st = EntrySnapshotStore(tmp_path / "s.jsonl", max_lines=2,
                            archive=SegmentArchive(tmp_path / "arc",
                                                   stream_id=ENTRY_ARCHIVE_STREAM_ID))
    ids = []
    for i in range(8):
        s = build_entry_snapshot(run_id="R", cycle_id=f"c{i}", symbol="X/USDT",
                                 direction="LONG", now=utc_now())
        st.append(s)
        ids.append(s["candidate_id"])
    st.rotate()
    assert len(st.by_candidate()) == 2, "sıcak yol arşivi taramamalı"
    assert len(st.by_candidate(include_archive=True)) == 8


def test_36_open_position_link_survives_rotation(tmp_path):
    from tradingbot.learn.entry_snapshot import (ENTRY_ARCHIVE_STREAM_ID, EntrySnapshotStore,
                                                 build_entry_snapshot)
    from tradingbot.learn.journal_archive import SegmentArchive
    st = EntrySnapshotStore(tmp_path / "s.jsonl", max_lines=2,
                            archive=SegmentArchive(tmp_path / "arc",
                                                   stream_id=ENTRY_ARCHIVE_STREAM_ID))
    first = build_entry_snapshot(run_id="R", cycle_id="c0", symbol="SOL/USDT",
                                 direction="LONG", now=utc_now())
    st.append(first)
    st.link_trade(first["candidate_id"], "F00099")
    for i in range(1, 10):
        st.append(build_entry_snapshot(run_id="R", cycle_id=f"c{i}", symbol="X/USDT",
                                       direction="LONG", now=utc_now()))
    st.rotate()
    assert st.trade_links(include_archive=True).get("F00099") == first["candidate_id"]
    assert first["candidate_id"] in st.by_candidate(include_archive=True)


# ------------------------------------------------------------------ 37-40: deploy / panel / API

def test_37_broken_backup_prevents_deployment():
    from types import SimpleNamespace

    import tradingbot.cli_v3 as cli
    import tradingbot.ops.backup as ob
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        st = Path(td) / "state"
        st.mkdir()
        (st / "mode.json").write_text('{"mode": "PAPER"}', encoding="utf-8")
        cfg = SimpleNamespace(state_path=st, backups_path=Path(td) / "b",
                              obsidian=SimpleNamespace(root=None),
                              v3=SimpleNamespace(storage=SimpleNamespace(
                                  keep_hourly=2, keep_daily=2, keep_weekly=2)))
        args = SimpleNamespace(hourly=False, daily=False, weekly=False, manual=True)
        assert cli.cmd_backup(cfg, args) == 0
        real = ob.run_backup
        try:
            ob.run_backup = lambda *a, **k: (lambda r: (Path(r.archive).write_bytes(b"x"), r)[1])(real(*a, **k))
            assert cli.cmd_backup(cfg, args) == 1
        finally:
            ob.run_backup = real
    txt = Path("deploy/update.sh").read_text(encoding="utf-8")
    assert txt.index("deploy/backup.sh") < txt.index("systemctl")


def test_38_fingerprint_field_contract_excludes_phantom_fields():
    from tradingbot.ops.fingerprint import (FUTURES_POSITION_FIELDS, SPOT_LEDGER_FIELDS,
                                            assert_schema)
    assert "take_profit" not in FUTURES_POSITION_FIELDS
    assert "positions" not in SPOT_LEDGER_FIELDS
    assert assert_schema()["verified"] is True


@pytest.mark.parametrize("doc", [
    None, {}, {"weekly_context": "bozuk"}, {"weekly_context": {"enabled": True, "variants": None}},
    {"weekly_context": {"enabled": False, "reason": "X"}},
    {"weekly_context": {"enabled": True, "error": "Boom"}},
    {"weekly_context": {"enabled": True, "n_linked": float("nan"),
                        "variants": {"base": {"families": {FAM_WEEKLY: None}}}}},
])
def test_39_dashboard_never_500s_on_absent_or_corrupt_weekly_state(tmp_path, doc):
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    st, data = tmp_path / "state", tmp_path / "data"
    st.mkdir(), data.mkdir()
    (st / "mode.json").write_text('{"mode": "PAPER", "history": []}', encoding="utf-8")
    (st / "futures_ledger.json").write_text(
        '{"schema_version": 2, "wallet_balance": "100", "starting_equity": "100",'
        ' "positions": {}, "history": [], "total_fees": "0"}', encoding="utf-8")
    if doc is not None:
        (st / "entry_selectivity.json").write_text(json.dumps(doc, allow_nan=True),
                                                   encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    for path in ("/learning", "/api/entry-selectivity", "/llm", "/api/llm-status", "/"):
        assert c.get(path).status_code == 200, path


def test_40_apis_carry_no_nan_infinity_or_secrets(tmp_path):
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    st, data = tmp_path / "state", tmp_path / "data"
    st.mkdir(), data.mkdir()
    (st / "mode.json").write_text('{"mode": "PAPER", "history": []}', encoding="utf-8")
    (st / "futures_ledger.json").write_text(
        '{"schema_version": 2, "wallet_balance": "100", "starting_equity": "100",'
        ' "positions": {}, "history": [], "total_fees": "0"}', encoding="utf-8")
    (st / "learning.json").write_text(
        '{"n_trades": 1, "n_wins": 1, "sum_r": 0.4, "lessons": [], "weights": {},'
        ' "agent_weights": {}}', encoding="utf-8")
    snaps, links = {"c1": _snap()}, {"T1": "c1"}
    doc = build_report_v2(closes=[_close()], snapshots=snaps, links=links)
    (st / "entry_selectivity.json").write_text(
        json.dumps({"schema_version": "entry_eval_v1", "entry_mode": "SHADOW",
                    "applied_total": 0, "auto_promotion": False,
                    "verdict": "INSUFFICIENT_ENTRY_SAMPLE", "weekly_context": doc},
                   allow_nan=True), encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    body = c.get("/api/entry-selectivity").text
    assert "NaN" not in body and "Infinity" not in body
    import re
    assert not re.search(r"sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}", body)
    html = c.get("/learning").text
    assert "Haftalık yapı ve bağlamsal fiyat hareketi" in html
    assert "Yalnız SHADOW gözlem" in html and "aktif karara etkisi yok" in html
    assert "bağlamsal" in html
    js = c.get("/api/entry-selectivity").json()
    assert js["weekly_context"]["applied_total"] == 0
    assert js["weekly_context"]["auto_promotion"] is False
    assert js["weekly_context"]["verdict"] == "INSUFFICIENT_ENTRY_SAMPLE"
    assert js["weekly_context"]["extra_gates"]["WEEKLY_DATA_COVERAGE"] == GATE_MIN_WEEKLY_COVERAGE
    assert js["weekly_context"]["extra_gates"]["ABSTAIN_RATE_ACCEPTABLE"] == GATE_MAX_ABSTAIN_RATE
