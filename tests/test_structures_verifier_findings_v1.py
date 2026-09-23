# -*- coding: utf-8 -*-
"""BAĞIMSIZ DOĞRULAYICI BULGULARI (2026-09-23, 18 bulgu) — her düzeltmenin gerileme testi.

Bulgu numaraları doğrulayıcı raporundakiyle aynıdır (inceleme belgesinde tablo). Veri SENTETİKTİR, etiketlidir;
karar/defter kodu üretim kodudur. #8 önbellek sözleşmesi `test_structures_analysis_v1.py`'dedir.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from structure_fixtures import _bar, bearish_engulf_confirmed, box_day, neutral_trend  # noqa: E402

from tradingbot.structures import analysis as A  # noqa: E402
from tradingbot.structures import bots as SB  # noqa: E402
from tradingbot.structures import catalog as K  # noqa: E402
from tradingbot.structures import policy as P  # noqa: E402

DAY = 86_400_000
H4 = 14_400_000
H1 = 3_600_000
M5 = 300_000
T0 = 1_780_000_000_000 // DAY * DAY


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


# ============================================================================ #1 SHADOW = OFF (mum ajanı)
def test_1_shadow_candle_agent_is_bit_identical_to_off_and_only_enforce_votes_from_the_catalog():
    from tradingbot.agents.base import CoinContext
    from tradingbot.agents.technical import CandleAgent
    d1 = bearish_engulf_confirmed(neutral_trend(120, start_ms=T0, step=DAY, up=True, scale=0.3), step=DAY)
    h4 = bearish_engulf_confirmed(neutral_trend(120, start_ms=T0, step=H4, up=True, scale=0.3), step=H4)
    frames = {"1d": pd.DataFrame(d1), "4h": pd.DataFrame(h4)}
    reps = {}
    for mode in ("OFF", "SHADOW", "ENFORCE"):
        A.clear_cache()
        ctx = CoinContext(symbol="SYN/USDT", frames=frames, live={"ticker": {"last": d1[-1]["close"]}},
                          frame_market="USDM_PERP", structures_mode=mode)
        reps[mode] = CandleAgent().run(ctx)
    off, sh, en = reps["OFF"], reps["SHADOW"], reps["ENFORCE"]
    assert (sh.bias, sh.confidence, sh.findings, sh.metrics) == (off.bias, off.confidence, off.findings, off.metrics)
    assert "structure_analysis_ids" in en.metrics and "structure_analysis_ids" not in sh.metrics
    assert en.bias != off.bias, "ENFORCE'ta teyitli ayı yapısı TEK katalog oyu olarak girer"


# ============================================================================ #2 eski 1h uçları sonraki turda
def test_2_bar_extremes_from_before_a_structure_tightening_are_not_used_in_later_tours():
    from tradingbot.accounting import TickData
    from tradingbot.engine_v3 import _pre_change_extreme_symbols
    at = T0 + 12 * H1 + 5 * 60_000                       # 12:05 sıkılaştırma
    pos = SimpleNamespace(meta={"structure_stop": {"from": "95", "to": "97", "at": _iso(at)}})
    other = SimpleNamespace(meta={})
    mk = TickData(last=Decimal("97.9"), mark=Decimal("97.9"), high=Decimal("99"), low=Decimal("97.0"), ts=_iso(at + 15 * 60_000))
    frames_old = {"X": {"1h": pd.DataFrame([{"timestamp": T0 + 11 * H1, "high": 99.0, "low": 97.0}])}}   # 11:00-12:00 barı
    frames_new = {"X": {"1h": pd.DataFrame([{"timestamp": T0 + 13 * H1, "high": 99.0, "low": 97.0}])}}   # 13:00 barı (sonra)
    frames_cur = {"X": {"1h": pd.DataFrame([{"timestamp": T0 + 12 * H1, "high": 99.0, "low": 97.0}])}}   # 12:00 açılışlı (12:05 öncesi)
    assert _pre_change_extreme_symbols({"X": pos, "Y": other}, {"X": mk, "Y": mk}, frames_old) == ["X"]
    assert _pre_change_extreme_symbols({"X": pos}, {"X": mk}, frames_cur) == ["X"], "sıkılaştırmadan önce AÇILMIŞ bar"
    assert _pre_change_extreme_symbols({"X": pos}, {"X": mk}, frames_new) == [], "sonra açılan barın uçları geçerli"
    assert _pre_change_extreme_symbols({"X": pos}, {"X": mk}, {"X": {}}) == ["X"], "bar zamanı okunamazsa ihtiyatlı"
    # defter düzeyinde: uçlar düşürülünce 97.9 son fiyat 97 stopunu tetiklemez; eski uçla tetiklerdi
    from tradingbot.accounting.futures_ledger import FuturesLedgerV2
    from tradingbot.accounting.models import AmountType, SizeSpec
    for use_extremes, expect_open in ((True, False), (False, True)):
        led = FuturesLedgerV2(Decimal("1000"))
        led.open("X/USDT", "LONG", Decimal("100"), SizeSpec(Decimal("100"), AmountType.NOTIONAL, 1), stop=Decimal("97"), targets=[],
                 tick=TickData(last=Decimal("100"), mark=Decimal("100"), ts=_iso(T0)), now=datetime.fromtimestamp(T0 / 1000, tz=timezone.utc))
        t = mk if use_extremes else TickData(last=mk.last, mark=mk.mark, ts=mk.ts)
        led.tick({"X/USDT": t}, now_utc=datetime.fromtimestamp((at + 15 * 60_000) / 1000, tz=timezone.utc))
        assert ("X/USDT" in led.positions) is expect_open


# ============================================================================ #3 replay ana modu = canlı mum ajanı
def test_3_replay_legacy_brief_gives_the_agents_the_same_structure_mode_and_frame_market_as_live():
    from tradingbot.replay.engine import HistoricalReplay
    seen = {}

    class Spy:
        def run(self, ctx):
            seen["mode"], seen["market"] = ctx.structures_mode, ctx.frame_market
            return SimpleNamespace(agent="spy")
    rp = object.__new__(HistoricalReplay)
    rp.legacy_agents_on, rp._legacy_agents, rp.market, rp.structures_main = True, [Spy()], "futures", "ENFORCE"
    rp._legacy_manager = SimpleNamespace(decide=lambda ctx, reports: "brief")
    rp.cfg = SimpleNamespace(risk=SimpleNamespace(starting_equity_usdt=100.0, risk_per_trade_pct=2.0, atr_stop_mult=2.5))
    rp.legacy_brief_errors = {}
    assert rp._legacy_brief("X/USDT", {}, 1.0) == "brief"
    assert seen == {"mode": "ENFORCE", "market": "USDM_PERP"}


# ============================================================================ #4 M2 giriş yapısı analizden düşünce
def _pos(opened_ms: int, *, side="LONG", structure=None):
    return SimpleNamespace(opened_at=_iso(opened_ms), side=SimpleNamespace(value=side), features={"structure": structure or {}},
                           fills=[])


def test_4_m2_exits_on_its_frozen_entry_invalidation_when_the_record_is_no_longer_in_the_analysis():
    rows = neutral_trend(260, start_ms=T0, step=DAY, up=True, scale=0.3)
    opened = int(rows[-6]["timestamp"]) + DAY
    inv = rows[-1]["close"] * 1.01                        # son kapanış geçersizliğin ALTINDA
    st = {"action": "ENTER", "pattern_id": "gone00000000000", "name": "COMPRESSION_BREAKOUT", "timeframe": "1d",
          "invalidation": {"rule": "close_below", "level": inv}, "side": "LONG"}
    ctx = SB.StructureContext(mode="ENFORCE", symbol="SYN/USDT", as_of_ms=int(rows[-1]["timestamp"]) + DAY + 60_000)
    act, dec, _ = SB.trend_decide("m2_tsmom28", daily_rows=rows, base=None, position=_pos(opened, structure=st), ctx=ctx)
    assert dec["action"] == P.ACT_EXIT and dec["reason_code"] == "ENTRY_STRUCTURE_FAILED"
    assert dec["detail"]["source"] == "FROZEN_ENTRY_RECORD" and act["action"] == "CLOSE"
    # geçersizlik korunuyorsa çıkış YOK; T2 (yapı çıkışı yok) etkilenmez
    st_ok = dict(st, invalidation={"rule": "close_below", "level": rows[-1]["close"] * 0.5})
    _, dec2, _ = SB.trend_decide("m2_tsmom28", daily_rows=rows, base=None, position=_pos(opened, structure=st_ok), ctx=ctx)
    assert dec2["action"] != P.ACT_EXIT
    _, dec3, _ = SB.trend_decide("t2_trend_regime", daily_rows=rows, base=None, position=_pos(opened, structure=st), ctx=ctx)
    assert dec3["action"] != P.ACT_EXIT


# ============================================================================ #5 gölge/ENTER olmayan referans
def test_5_only_real_enter_references_count_as_entry_structures_or_used_patterns():
    wait = {"action": "WAIT", "pattern_id": "p_wait", "invalidation": {"level": 1.0}}
    shadow = {"action": "ENTER", "shadow": True, "pattern_id": "p_shadow"}
    enter = {"action": "ENTER", "pattern_id": "p_enter"}
    led = SimpleNamespace(positions={"A": SimpleNamespace(features={"structure": wait}),
                                     "B": SimpleNamespace(features={"structure": shadow}),
                                     "C": SimpleNamespace(features={"structure": enter})},
                          history=[SimpleNamespace(features={"structure": dict(wait, pattern_id="h_wait")})])
    assert SB.used_patterns_of(led) == {"p_enter"}
    assert SB._entry_pid(led.positions["A"]) is None and SB._entry_pid(led.positions["B"]) is None
    assert SB._entry_pid(led.positions["C"]) == "p_enter"


# ============================================================================ #6 Box dış kırılım iptali kalıcı
def test_6_a_confirmed_outside_breakout_keeps_cancelling_the_fade_until_a_close_back_inside():
    from tradingbot import box_theory
    day0 = T0 + 300 * DAY
    daily, m5 = box_day(day0=day0, ending="breakout")          # kutu 95-105, tepenin üstünde ardışık 2 kapanış
    t = m5[-1]["timestamp"]
    later = list(m5)
    px = m5[-1]["close"]
    for i in range(1, 9):                                       # 8 bar daha DIŞARIDA (kayıt artık taze değil)
        later.append(_bar(t + i * M5, px, px * 1.002, px * 0.998, px * 1.001))
        px = later[-1]["close"]
    tt = later[-1]["timestamp"]                                 # dışarıda ayı yutan + teyit (eski kodda fade açabilirdi)
    later += [_bar(tt + M5, px * 1.000, px * 1.004, px * 0.999, px * 1.003),
              _bar(tt + 2 * M5, px * 1.004, px * 1.005, px * 0.996, px * 0.997),
              _bar(tt + 3 * M5, px * 0.997, px * 0.998, px * 0.990, px * 0.992)]
    assert min(r["close"] for r in later[len(m5) - 2:]) > 105.0, "hiç içeri kapanış yok"
    params = box_theory.BoxParams()
    ctx = SB.StructureContext(mode="ENFORCE", symbol="SYN/USDT", as_of_ms=int(later[-1]["timestamp"]) + M5, price=later[-1]["close"])
    base = {"action": "OPEN", "direction": "SHORT", "stop": later[-1]["close"] * 1.02, "name": "b1_box_fade"}
    act, dec, _ = SB.box_decide("b1_box_fade", daily_rows=daily, m5_rows=later, base=base, position=None, params=params, ctx=ctx)
    assert dec["action"] == P.ACT_CANCEL and dec["reason_code"] == "BOX_OUTSIDE_BREAKOUT", (dec["action"], dec["reason_code"])
    assert act["action"] == "NONE" and dec["detail"]["outside_since_ms"] is not None
    # içeri kapanış → kırılım başarısız: iptal kalkar
    back = later + [_bar(later[-1]["timestamp"] + M5, 104.0, 104.2, 103.0, 103.5)]
    assert SB._outside_breakout_since(back, day0, 105.0, up=True) is None
    assert SB._outside_breakout_since(later, day0, 105.0, up=True) == int(m5[-2]["timestamp"])


# ============================================================================ #7 / #17 durum makinesi
def _rows(closes):
    return [_bar(T0 + i * H4, c, c * 1.001, c * 0.999, c) for i, c in enumerate(closes)]


def test_7_closes_before_recognition_cannot_confirm_but_can_break():
    trig, inv = 110.0, 90.0
    # tanınma (detected=5) öncesi tetik kesişmesi (k=2) + araya geçersizlik kapanışı (k=3) → BROKEN (önce CONFIRMED)
    r1 = A._run_status(_rows([100, 100, 111, 89, 100, 100, 100, 100]), start=0, detected_idx=5, side=K.LONG,
                       trigger_at=lambda k: trig, invalidation=inv, window=20, fresh_bars=2)
    assert r1["status"] == K.ST_BROKEN and r1["broken_idx"] == 5
    assert r1["reasons"] == ["CLOSE_BEYOND_INVALIDATION_BEFORE_RECOGNITION"]
    # tanınmadan önce kesişip tanınma barında GERİ içeride → teyit YOK (oluşuyor)
    r2 = A._run_status(_rows([100, 100, 111, 105, 104, 103, 104]), start=0, detected_idx=5, side=K.LONG,
                       trigger_at=lambda k: trig, invalidation=inv, window=20, fresh_bars=2)
    assert r2["status"] == K.ST_FORMING and r2["confirm_idx"] is None
    # tanınma barında hâlâ ötede → tanınma barında teyit (teyit barının kapanışı tetiğin ötesinde)
    r3 = A._run_status(_rows([100, 100, 111, 112, 113, 114, 114]), start=0, detected_idx=5, side=K.LONG,
                       trigger_at=lambda k: trig, invalidation=inv, window=20, fresh_bars=2)
    assert r3["status"] == K.ST_CONFIRMED and r3["confirm_idx"] == 5


def test_17_a_trigger_that_became_undefined_after_recognition_expires_the_record():
    r = A._run_status(_rows([100, 100, 100, 100, 100, 100, 100, 100]), start=0, detected_idx=2, side=K.LONG,
                      trigger_at=lambda k: 110.0 if k < 5 else None, invalidation=90.0, window=20, fresh_bars=2)
    assert r["status"] == K.ST_EXPIRED and r["expired_idx"] == 5 and r["reasons"] == ["TRIGGER_UNREACHABLE_APEX_PASSED"]


# ============================================================================ #9 son geçerlilik = bayatlama anı
def test_9_a_confirmed_record_is_valid_exactly_as_long_as_it_is_fresh():
    rows = _rows([100] * 10)
    st = {"status": K.ST_CONFIRMED, "confirm_idx": 5, "broken_idx": None, "expired_idx": None, "reasons": []}
    A._after_confirm(st, n=len(rows), fresh_bars=2, beyond=lambda q: False)
    rec: dict = {}
    A._finish(rec, rows, st, step=H4, n=len(rows), window=3, fresh_bars=2, detected_idx=4)
    assert rec["status"] == K.ST_EXPIRED and rec["expires_at_ms"] == rec["expired_at_ms"] == T0 + 9 * H4
    fresh_rows = rows[:8]                                        # son bar 7 = teyit + 2 → hâlâ taze
    st2 = {"status": K.ST_CONFIRMED, "confirm_idx": 5, "broken_idx": None, "expired_idx": None, "reasons": []}
    A._after_confirm(st2, n=len(fresh_rows), fresh_bars=2, beyond=lambda q: False)
    rec2: dict = {}
    A._finish(rec2, fresh_rows, st2, step=H4, n=len(fresh_rows), window=3, fresh_bars=2, detected_idx=4)
    from tradingbot.pattern_trader.book import PatternBook
    as_of = int(fresh_rows[-1]["timestamp"]) + H4
    assert rec2["status"] == K.ST_CONFIRMED and not PatternBook.is_expired({"expires_at_ms": rec2["expires_at_ms"]}, as_of + 60_000)


# ============================================================================ #10 / #11 / #18 formasyon planı
def _book(tmp_path):
    from test_pattern_trader_v1 import _book_obj, _cfg
    cfg = _cfg(tmp_path)
    cfg.v3.structures.enabled = True
    cfg.v3.structures.pattern_trader = "ENFORCE"
    return _book_obj(cfg)


def _plan(status, pid="p1", tf="15m"):
    return {"version": "pattern_protocol_v2.0.0", "plan_id": "pl_" + pid, "symbol": "LNG/USDT", "family": "D2_CHART_STRUCTURE",
            "side": "LONG", "entry_tf": tf, "pattern_id": pid, "status": status, "status_history": [], "reasons": [],
            "structure": {"pattern_id": pid, "name": "BULL_FLAG", "policy_version": K.POLICY_VERSION}, "expires_at_ms": T0 + 10 * DAY,
            "trigger": {"level": 1.0, "rule": "close_above", "tf": tf}, "invalidation": {"level": 0.9}, "stop": 0.89, "target": 1.2,
            "atr": 0.01}


def test_10_a_triggered_plan_waiting_for_price_ends_when_its_record_breaks_or_goes_stale(tmp_path):
    book = _book(tmp_path)
    now = datetime.fromtimestamp(T0 / 1000, tz=timezone.utc)
    out, cs = {"triggered": 0}, {"triggered": 0}
    for rec_status, want in ((K.ST_BROKEN, "BROKEN"), (K.ST_EXPIRED, "EXPIRED"), (K.ST_CONFIRMED, "TRIGGERED")):
        pl = _plan("TRIGGERED")
        an = {"15m": {"market": "USDM_PERP", "records": [{"pattern_id": "p1", "status": rec_status, "broken_at_ms": T0, "expired_at_ms": T0}]}}
        book._follow_record(pl, an, as_of_ms=T0, dec_ms=T0, now=now, out=out, cs=cs, levels_1h=None)
        assert pl["status"] == want, (rec_status, pl["status"])


def test_11_plans_are_neither_built_nor_triggered_from_a_non_perp_timeframe(tmp_path):
    from tradingbot.pattern_trader.strategy_v2 import build_plans_v2
    plans, skipped = build_plans_v2("LNG/USDT", as_of_ms=T0, analyses={"15m": {"market": "SPOT", "records": []},
                                                                        "1h": {"market": "UNVERIFIED", "records": []}},
                                    bars_by_tf={}, levels_1h=None, trend_4h=None, cost_frac=0.001)
    assert plans == [] and {s["reason"] for s in skipped} == {"ANALYSIS_MARKET_SPOT", "ANALYSIS_MARKET_UNVERIFIED"}
    book = _book(tmp_path)
    pl = _plan("AWAITING_TRIGGER", tf="1h")
    rec = {"pattern_id": "p1", "status": K.ST_CONFIRMED, "confirmed_at_ms": T0, "trigger": {"level": 1.0}, "invalidation": {"level": 0.9},
           "stop": 0.89, "targets": [1.5], "atr": 0.01, "timeframe": "1h"}
    book._follow_record(pl, {"1h": {"market": "SPOT", "records": [rec]}}, as_of_ms=T0, dec_ms=T0,
                        now=datetime.fromtimestamp(T0 / 1000, tz=timezone.utc), out={"triggered": 0}, cs={"triggered": 0}, levels_1h=None)
    assert pl["status"] == "AWAITING_TRIGGER", "spot çerçevedeki teyit vadeli planı TETİKLEMEZ"


def test_18_a_rejected_or_ended_plan_is_recorded_as_such_not_as_an_entry(tmp_path):
    from tradingbot.structures.store import StructureStore
    book = _book(tmp_path)
    pl = _plan("TRIGGERED")
    book.plans[pl["plan_id"]] = pl
    book._set_status(pl, "REJECTED", T0, "MAX_POSITION_PCT")
    row = StructureStore(book.cfg.state_path).latest_decisions()["pattern_trader|USDM_PERP|LNG/USDT"]
    assert row["action"] == "CANCEL" and row["reason_code"] == "PLAN_REJECTED:MAX_POSITION_PCT" and "Girmedi" in row["text_tr"]
    pl2 = dict(_plan("MANAGED", pid="p2"), position_id="F00009")
    book._set_status(pl2, "CLOSED", T0 + 1, "EXIT_HEDEF1")
    row2 = StructureStore(book.cfg.state_path).latest_decisions()["pattern_trader|USDM_PERP|LNG/USDT"]
    assert row2["action"] == "EXIT" and row2["trade_id"] == "F00009"


# ============================================================================ #12 / #15 ana bot
def _engine_stub(tmp_path, *, runtime_mode="PAPER", mode="ENFORCE"):
    from tradingbot.config import BotConfig
    from tradingbot.config_v3 import load_v3
    from tradingbot.engine_v3 import TradingEngineV3
    eng = object.__new__(TradingEngineV3)
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.v3 = load_v3({"structures": {"enabled": True, "main": mode}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    eng.cfg = cfg
    eng.mode_state = SimpleNamespace(mode=SimpleNamespace(value=runtime_mode))
    return eng


def test_15_runtime_live_mode_downgrades_enforce_to_shadow(tmp_path):
    assert _engine_stub(tmp_path, runtime_mode="PAPER")._structure_mode_main() == "ENFORCE"
    assert _engine_stub(tmp_path, runtime_mode="LIVE")._structure_mode_main() == "SHADOW"
    assert _engine_stub(tmp_path, runtime_mode="LIVE_LIMITED")._structure_mode_main() == "SHADOW"
    assert _engine_stub(tmp_path, runtime_mode="LIVE", mode="OFF")._structure_mode_main() == "OFF"


def test_12_the_main_gate_measures_the_chase_from_the_verified_perp_mark(tmp_path, monkeypatch):
    from tradingbot.accounting.futures_ledger import FuturesLedgerV2
    from tradingbot.structures import bots as B
    eng = _engine_stub(tmp_path)
    eng.ledger2 = FuturesLedgerV2(Decimal("100"))
    eng.runner = SimpleNamespace(last_frames={})
    seen = {}

    def fake(**kw):
        seen["price"] = kw["price"]
        return {"bot": "main", "action": "NO_EFFECT", "reason_code": "NO_STRUCTURE", "pattern_ids": [], "primary": None}, {}
    monkeypatch.setattr(B, "main_entry_decision", fake)
    d, plan, b = SimpleNamespace(direction="LONG"), SimpleNamespace(entry_type="pullback"), SimpleNamespace(price=100.0)
    now = datetime.fromtimestamp(T0 / 1000, tz=timezone.utc)
    eng._frame_provenance = {"X/USDT": {"market": "USDM_PERP", "perp_mark": {"price": 101.5, "fresh": True}}}
    dec = eng._structure_gate("X/USDT", d, plan, b, now, "USDM_PERP")
    assert seen["price"] == 101.5 and dec["detail"]["chase_price_source"] == "perp_mark"
    eng._frame_provenance = {"X/USDT": {"market": "USDM_PERP", "perp_mark": {"price": 101.5, "fresh": False}}}
    dec = eng._structure_gate("X/USDT", d, plan, b, now, "USDM_PERP")
    assert seen["price"] == 100.0 and dec["detail"]["chase_price_source"] == "brief_price"


# ============================================================================ #14 geri çekilme + analiz yok
def test_14_a_pullback_plan_waits_when_the_decision_timeframe_analysis_is_unavailable():
    pol = P.POLICIES[P.BOT_MAIN]
    d = P.entry_decision(pol, intended_side="LONG", analyses={"4h": None, "1d": None}, as_of_ms=T0, entry_type="pullback")
    assert d["action"] == P.ACT_WAIT and d["reason_code"] == "PULLBACK_NEEDS_CONFIRMED_STRUCTURE:ANALYSIS_UNAVAILABLE"
    d2 = P.entry_decision(pol, intended_side="LONG", analyses={"4h": None}, as_of_ms=T0, entry_type="breakout")
    assert d2["action"] == P.ACT_NO_EFFECT


# ============================================================================ #16 yapı arızası çıkışı düşürmez
def test_16_a_structure_layer_error_never_drops_the_bots_own_exit_and_blocks_only_new_entries(tmp_path, monkeypatch):
    import test_structures_strategy_books_v1 as S

    from tradingbot import paper_rules
    flag, brk = S._daily_flag()
    btc = S._btc(len(brk))
    book = S._book(S._cfg(tmp_path, "ENFORCE"), "t2_trend_regime")
    S._step(book, {"1d": brk}, as_of_ms=S._asof(brk), price=brk[-1]["close"], btc=btc)
    assert S.SYM in book.ledger.positions

    def boom(*a, **k):
        raise RuntimeError("yapı katmanı arızası")
    monkeypatch.setattr(paper_rules, "decide_with_structures", boom)
    monkeypatch.setattr(paper_rules, "decide_for", lambda *a, **k: {"action": "CLOSE", "reason": "EMA200_CROSS_DOWN", "name": "t2_trend_regime"})
    S._step(book, {"1d": brk}, as_of_ms=S._asof(brk) + 3_600_000, price=brk[-1]["close"], btc=btc)
    assert S.SYM not in book.ledger.positions and book.ledger.history[-1].exit_reason == "EMA200_CROSS_DOWN"
    assert book.rejections.get("STRUCTURE_ERROR:RuntimeError", 0) >= 1
    # ENFORCE: yapı ölçülemezken YENİ giriş yok
    monkeypatch.setattr(paper_rules, "decide_for", lambda *a, **k: {"action": "OPEN", "direction": "LONG", "stop": brk[-1]["close"] * 0.9,
                                                                   "name": "t2_trend_regime", "leverage": 2})
    S._step(book, {"1d": brk}, as_of_ms=S._asof(brk) + 7_200_000, price=brk[-1]["close"], btc=btc)
    assert S.SYM not in book.ledger.positions


def test_16_pattern_book_structure_analysis_error_does_not_stop_the_scan(tmp_path, monkeypatch):
    book = _book(tmp_path)
    monkeypatch.setattr(book, "_structure_analyses", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    rows = neutral_trend(80, start_ms=T0, step=900_000, up=True, scale=0.3)
    out = book.process_symbol("LNG/USDT", bars_by_tf={"15m": rows, "1h": [], "4h": []}, statuses={}, as_of_ms=int(rows[-1]["timestamp"]) + 900_000,
                              universe_entry=None, price=None, liquidity=None, run_id="t")
    assert any(s.get("reason") == "STRUCTURE_ANALYSIS_ERROR:RuntimeError" for s in out["skipped"])


# ============================================================================ #13 geç doğan kayıt (kesişme/seviye)
def test_13_retests_emit_every_level_and_every_cross_so_nothing_is_born_late():
    """Her seviye ve ufuk içindeki HER kesişme kendi kaydıdır (önce yalnız ilk kesişme; düzeltme sırasında bir
    girinti hatası yalnız İLK seviyeyi işletti — gerçek arşiv denetimi yakaladı, bu test onu da yakalar)."""
    closes = [100, 100, 100, 111, 112, 109, 108, 111.5, 112, 100, 98, 111, 112, 113, 113, 113, 113, 113, 113, 113]
    rows = _rows(closes)
    atr = A._atr_series(rows, 14)
    atr = [a if a else 1.0 for a in atr]
    cfg = K.DEFAULT_CONFIG
    levels = [{"name": "SWING_HIGH", "level": 110.0, "kind": "high", "valid_from_idx": 1, "anchor_ts": int(rows[0]["timestamp"])},
              {"name": "SWING_HIGH", "level": 105.0, "kind": "high", "valid_from_idx": 1, "anchor_ts": int(rows[1]["timestamp"])}]
    recs = A._retests(rows, atr, levels, market="USDM_PERP", symbol="SYN/USDT", tf="4h", step=H4, cfg=cfg)
    by_level = {}
    for r in recs:
        by_level.setdefault(r["anchors"][0]["price"], []).append(int(r["anchors"][1]["ts"]))
    assert set(by_level) == {110.0, 105.0}, "iki seviyenin de kaydı var"
    assert sorted(by_level[110.0]) == [int(rows[k]["timestamp"]) for k in (3, 7, 11)], "110'un üç kesişmesi"


# ============================================================================ TUR 3 (düzeltmelerin doğrulaması, 905098d)
def test_r3_1_box_exits_an_open_fade_on_an_outside_breakout_confirmed_after_entry_even_when_the_record_is_stale():
    from tradingbot import box_theory
    day0 = T0 + 300 * DAY
    daily, m5 = box_day(day0=day0, ending="none")
    t = m5[-1]["timestamp"]
    opened = t + M5                                              # fade açılışı
    rows = list(m5) + [_bar(t + M5, 104.0, 104.5, 103.8, 104.2),
                       _bar(t + 2 * M5, 104.2, 105.9, 104.1, 105.6),   # kutu (105) üstünde 1. kapanış
                       _bar(t + 3 * M5, 105.6, 106.0, 105.3, 105.8)]   # 2. kapanış → teyitli dış kırılım
    for i in range(4, 10):                                       # kayıt bayatlayana kadar dışarıda
        rows.append(_bar(t + i * M5, 105.8, 106.1, 105.5, 105.9))
    pos = SimpleNamespace(opened_at=_iso(opened), side=SimpleNamespace(value="SHORT"), features={"structure": {}}, fills=[])
    ctx = SB.StructureContext(mode="ENFORCE", symbol="SYN/USDT", as_of_ms=int(rows[-1]["timestamp"]) + M5, price=rows[-1]["close"])
    act, dec, _ = SB.box_decide("b1_box_fade", daily_rows=daily, m5_rows=rows, base=None, position=pos,
                                params=box_theory.BoxParams(), ctx=ctx)
    assert dec["action"] == P.ACT_EXIT and act["action"] == "CLOSE", (dec["action"], dec["reason_code"])
    assert dec["detail"]["breakout_confirmed_at_ms"] > opened
    late = SimpleNamespace(opened_at=_iso(int(rows[-1]["timestamp"])), side=SimpleNamespace(value="SHORT"),
                           features={"structure": {}}, fills=[])
    _, dec2, _ = SB.box_decide("b1_box_fade", daily_rows=daily, m5_rows=rows, base=None, position=late,
                               params=box_theory.BoxParams(), ctx=ctx)
    assert dec2["action"] != P.ACT_EXIT, "girişten ÖNCE teyit olmuş kırılım açık pozisyonu kapatmaz"


def test_r3_2_levels_are_point_in_time_and_superseded_triangles_end_instead_of_vanishing():
    rows = _rows([100] * 10 + [100.5, 101.2, 100.8, 100.2, 99.8, 100.1, 100.4, 100.2, 100.0, 100.3])
    rows[11] = _bar(rows[11]["timestamp"], 100.5, 101.6, 100.4, 100.8)          # tepe 101'i fitille aşar, içeride kapanır
    atr = [1.0] * len(rows)
    cfg = K.DEFAULT_CONFIG
    lvl = {"name": "SWING_HIGH", "level": 101.0, "kind": "high", "valid_from_idx": 5, "anchor_ts": int(rows[2]["timestamp"])}
    active = A._sweeps_and_breakouts(rows, atr, [dict(lvl, valid_until_idx=None)], market="USDM_PERP", symbol="S", tf="4h", step=H4, cfg=cfg)
    ended = A._sweeps_and_breakouts(rows, atr, [dict(lvl, valid_until_idx=11)], market="USDM_PERP", symbol="S", tf="4h", step=H4, cfg=cfg)
    assert [r["name"] for r in active] == ["SWEEP_RECLAIM"], [(r["name"], r["status"]) for r in active]
    assert ended == [], "seviye kümeden çıktıktan SONRA başlayan olay kayıt üretmez"
    r = A._run_status(_rows([100] * 12), start=0, detected_idx=3, side=K.LONG, trigger_at=lambda k: 110.0,
                      invalidation=90.0, window=20, fresh_bars=2, superseded_idx=6)
    assert r["status"] == K.ST_EXPIRED and r["expired_idx"] == 6 and r["reasons"] == ["SUPERSEDED_BY_NEW_PIVOT"]
    r2 = A._run_status(_rows([100, 100, 100, 100, 111, 111, 111, 111, 111]), start=0, detected_idx=3, side=K.LONG,
                       trigger_at=lambda k: 110.0, invalidation=90.0, window=20, fresh_bars=2, superseded_idx=6)
    assert r2["confirm_idx"] == 4, "yerini almadan ÖNCE teyit olan kayıt yaşamaya devam eder"


def test_r3_3_4_sibling_cancels_do_not_overwrite_the_entry_row_and_watcher_exits_carry_no_foreign_analysis(tmp_path):
    from tradingbot.structures.store import StructureStore
    book = _book(tmp_path)
    st = StructureStore(book.cfg.state_path)
    st.record_decision({"bot": "pattern_trader", "action": "ENTER", "pattern_ids": ["p1"], "primary": {"pattern_id": "p1"}},
                       book_id="pattern_trader", market="USDM_PERP", symbol="LNG/USDT", at_ms=T0, trade_id="F00001")
    sib = _plan("AWAITING_TRIGGER", pid="p2")
    book._set_status(sib, "CANCELLED", T0 + 1, "OTHER_PLAN_FILLED:pl_p1")
    assert st.latest_decisions()["pattern_trader|USDM_PERP|LNG/USDT"]["action"] == "ENTER"
    book._scan_analyses, book._scan_symbol = {"15m": {"analysis_id": "foreign", "records": []}}, "OTH/USDT"
    pl = dict(_plan("MANAGED", pid="p1"), position_id="F00001")
    book._set_status(pl, "CLOSED", T0 + 2, "EXIT_STOP")
    row = st.latest_decisions()["pattern_trader|USDM_PERP|LNG/USDT"]
    assert row["action"] == "EXIT" and not row.get("analysis_ids"), row.get("analysis_ids")


def test_r3_5_a_triggered_v2_plan_opens_only_while_its_record_is_confirmed_in_this_scan():
    from tradingbot.pattern_trader.book import PatternBook
    pl = _plan("TRIGGERED")
    ok = {"15m": {"market": "USDM_PERP", "records": [{"pattern_id": "p1", "status": "CONFIRMED"}]}}
    assert PatternBook._record_confirmed_now(pl, ok)
    assert not PatternBook._record_confirmed_now(pl, {})
    assert not PatternBook._record_confirmed_now(pl, {"15m": {"market": "SPOT", "records": [{"pattern_id": "p1", "status": "CONFIRMED"}]}})
    assert not PatternBook._record_confirmed_now(pl, {"15m": {"market": "USDM_PERP", "records": [{"pattern_id": "p1", "status": "EXPIRED"}]}})


def test_r3_6_the_structure_error_fallback_is_shared_by_live_and_replay(monkeypatch):
    from tradingbot import paper_rules

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(SB, "trend_decide", boom)
    rows = neutral_trend(260, start_ms=T0, step=DAY, up=True, scale=0.3)
    frames = {"1d": pd.DataFrame(rows)}
    now = int(rows[-1]["timestamp"]) + DAY + 60_000
    cases = (({"action": "CLOSE", "reason": "EMA200_CROSS_DOWN"}, "ENFORCE", "CLOSE"),
             ({"action": "OPEN", "direction": "LONG", "stop": 1.0}, "ENFORCE", "NONE"),
             ({"action": "OPEN", "direction": "LONG", "stop": 1.0}, "SHADOW", "OPEN"))
    for base, mode, want in cases:
        monkeypatch.setattr(paper_rules, "decide_from_rows", lambda *a, _b=base, **k: dict(_b))
        ctx = SB.StructureContext(mode=mode, symbol="SYN/USDT", as_of_ms=now)
        act, dec, _ = paper_rules.decide_with_structures("t2_trend_regime", frames=frames, btc_rows=[], now_ms=now, position=None, ctx=ctx)
        assert act["action"] == want and dec["reason_code"] == "STRUCTURE_ERROR:RuntimeError", (mode, act, dec)


def test_r3_7_box_shadow_records_the_same_cancel_as_enforce_after_an_outside_breakout():
    from tradingbot import box_theory
    day0 = T0 + 300 * DAY
    daily, m5 = box_day(day0=day0, ending="breakout")
    t = m5[-1]["timestamp"]
    rows = list(m5) + [_bar(t + i * M5, 105.9, 106.2, 105.6, 105.9) for i in range(1, 6)]
    decs = {}
    for mode in ("ENFORCE", "SHADOW"):
        ctx = SB.StructureContext(mode=mode, symbol="SYN/USDT", as_of_ms=int(rows[-1]["timestamp"]) + M5, price=rows[-1]["close"])
        _, dec, _ = SB.box_decide("b1_box_fade", daily_rows=daily, m5_rows=rows, base={"action": "OPEN", "direction": "SHORT"},
                                  position=None, params=box_theory.BoxParams(), ctx=ctx)
        decs[mode] = (dec["action"], dec["reason_code"])
    assert decs["ENFORCE"] == decs["SHADOW"] == (P.ACT_CANCEL, "BOX_OUTSIDE_BREAKOUT")


def test_r3_8_an_expired_record_publishes_the_moment_it_expired_as_its_validity_end():
    rows = _rows([100] * 12)
    st = A._run_status(rows, start=0, detected_idx=2, side=K.LONG, trigger_at=lambda k: 110.0 if k < 5 else None,
                       invalidation=90.0, window=20, fresh_bars=2)
    rec: dict = {}
    A._finish(rec, rows, st, step=H4, n=len(rows), window=20, fresh_bars=2, detected_idx=2)
    assert rec["status"] == K.ST_EXPIRED and rec["expires_at_ms"] == rec["expired_at_ms"] == T0 + 6 * H4


def test_r3_9_cached_records_carry_the_callers_moment_and_source():
    rows = bearish_engulf_confirmed(neutral_trend(120, start_ms=T0, step=H4, up=True, scale=0.3), step=H4)
    A.clear_cache()
    a = A.analyze(market="USDM_PERP", symbol="SYN/USDT", timeframe="4h", bars=rows, as_of_ms=int(rows[-1]["timestamp"]) + H4)
    b = A.analyze(market="USDM_PERP", symbol="SYN/USDT", timeframe="4h", bars=rows, as_of_ms=int(rows[-1]["timestamp"]) + H4 + 5,
                  data_provenance={"market": "USDM_PERP", "source": "perp_frames", "tour_id": "t9"})
    assert a["records"], "sentetik seride kayıt var"
    assert all(r["as_of_ms"] == b["as_of_ms"] and r["data_provenance"]["source"] == "perp_frames" for r in b["records"])
    assert all(r["data_provenance"]["source"] is None for r in a["records"]), "ilk çağıranın kayıtları değişmedi"


# ============================================================================ TUR 4 (düzeltmelerin doğrulaması, 68e1a14)
def test_r4_1_a_triggered_v2_plan_whose_record_left_the_analysis_ends_at_its_own_expiry(tmp_path, monkeypatch):
    """Kayıt analizden çekilen tetiklenmiş v2 planı süre sınırında biter (önce hiç bitmiyordu: giriş yolu ona hiç
    ulaşmadığı için süre orada dolamıyordu ve plan aynı aile/yöndeki yeni planları engelliyordu)."""
    book = _book(tmp_path)
    rows = neutral_trend(80, start_ms=T0, step=900_000, up=True, scale=0.3)
    as_of = int(rows[-1]["timestamp"]) + 900_000
    pl = dict(_plan("TRIGGERED"), expires_at_ms=as_of + 2 * 900_000, triggered_at_ms=as_of - 900_000)
    book.plans[pl["plan_id"]] = pl
    an = {"15m": {"market": "USDM_PERP", "analysis_id": "a1", "records": []}}
    monkeypatch.setattr(book, "_structure_analyses", lambda *a, **k: an)
    kw = dict(bars_by_tf={"15m": rows, "1h": [], "4h": []}, statuses={}, universe_entry=None, price=None, liquidity=None, run_id="t")
    book.process_symbol("LNG/USDT", as_of_ms=as_of, **kw)
    assert pl["status"] == "TRIGGERED" and not book.ledger.positions, "süre dolmadan: bekler, kayıt yokken AÇILMAZ"
    book.process_symbol("LNG/USDT", as_of_ms=as_of + 3 * 900_000, **kw)
    assert pl["status"] == "EXPIRED" and pl["reasons"][-1] == "EXPIRED_AT_SCAN_RECORD_MISSING", (pl["status"], pl["reasons"])
    assert not book.ledger.positions


def _tri_rows(*, breakout_before_successor: bool = True, tail=None, way=None):
    """Yükselen üçgen: düz tepeler f1/f2 (~110), yükselen dipler q1 < q2 < q3 (q3, f2'den sonra). Kırılım q3'ün
    teyidinden ÖNCE (selef A kırılımı teyit eder) ya da SONRA (A yerini alınca biter, ardıl B teyit eder); `tail`
    verilirse kırılım yolu doğrudan o noktalardır; `way` verilirse bütün yol (pivot indeksleri aynı kalmalı)."""
    lead = [80.0 + 20.0 * i / 59 for i in range(60)]
    if tail is None:
        tail = [(39, 110.6), (40, 110.9), (44, 111.4)] if breakout_before_successor else [(41, 108.0), (44, 108.8), (46, 110.8), (48, 111.3)]
    if way is None:
        way = [(0, 100.0), (10, 110.0), (16, 101.0), (21, 108.0), (26, 103.0), (32, 110.1), (37, 105.0)] + tail
    path = []
    for (i0, p0), (i1, p1) in zip(way, way[1:]):
        path += [p0 + (p1 - p0) * (i - i0) / (i1 - i0) for i in range(i0, i1)]
    closes = lead + path + [way[-1][1]]
    peaks, troughs = {60 + 10, 60 + 21, 60 + 32}, {60 + 16, 60 + 26, 60 + 37}
    rows, prev = [], closes[0] - 0.1
    for k, c in enumerate(closes):
        rows.append(_bar(T0 + k * H4, prev, max(prev, c) + (0.3 if k in peaks else 0.05),
                         min(prev, c) - (0.3 if k in troughs else 0.05), c))
        prev = c
    return rows


def test_r4_2_the_same_triangle_break_is_confirmed_once_per_flat_pair_and_the_successor_is_never_born():
    rows = _tri_rows(breakout_before_successor=True)
    seen: dict[str, int] = {}
    for end in range(len(rows) - 12, len(rows) + 1):              # ileri yürüyüş: her kapanmış barda yeniden hesap
        A.clear_cache()
        an = A.analyze(market="USDM_PERP", symbol="SYN/USDT", timeframe="4h", bars=rows[:end], as_of_ms=int(rows[end - 1]["timestamp"]) + H4)
        for r in an["records"]:
            if r["name"] == "ASCENDING_TRIANGLE" and r["side"] == K.LONG and r["confirmed_at_ms"] is not None:
                seen.setdefault(r["pattern_id"], int(r["confirmed_at_ms"]))
    assert len(seen) == 1, "aynı düz çiftin kırılımı TEK teyit: %s" % seen
    assert any(x.get("reason") == "TRIANGLE_FLAT_PAIR_ALREADY_RESOLVED" for x in an["rejects"]), an["rejects"]
    # selef olaysız yerini alırsa ardıl DOĞAR ve kendi kırılımını teyit eder (aşırı bastırma yok)
    rows2 = _tri_rows(breakout_before_successor=False)
    A.clear_cache()
    an2 = A.analyze(market="USDM_PERP", symbol="SYN/USDT", timeframe="4h", bars=rows2, as_of_ms=int(rows2[-1]["timestamp"]) + H4)
    longs = [r for r in an2["records"] if r["name"] == "ASCENDING_TRIANGLE" and r["side"] == K.LONG]
    assert sorted((r["status"], r["confirmed_at_ms"] is not None) for r in longs) == [(K.ST_CONFIRMED, True), (K.ST_EXPIRED, False)], \
        [(r["status"], r["reason_codes"]) for r in longs]
    assert any("SUPERSEDED_BY_NEW_PIVOT" in r["reason_codes"] for r in longs)


def test_r4_2b_the_walk_forward_audit_counts_a_triangle_break_confirmed_twice(monkeypatch):
    """Denetim ölçütünün kendisi: aynı düz çiftin iki kimlikle teyidi grup başına BİR ihlal (gerçek arşiv ölçümü bununla)."""
    from tradingbot.structures import audit as AU
    rows = _rows([100] * 6)

    def rec(pid, conf):
        return {"pattern_id": pid, "name": "ASCENDING_TRIANGLE", "side": K.LONG, "status": K.ST_CONFIRMED, "confirmed_at_ms": conf,
                "anchors": [{"ts": T0, "price": 110.0, "role": "duz1"}, {"ts": T0 + H4, "price": 110.1, "role": "duz2"},
                            {"ts": T0 + 2 * H4, "price": 101.0, "role": "egik1"}], "detected_at_ms": T0 + 3 * H4}
    monkeypatch.setattr(AU, "analyze", lambda **k: {"records": [rec("a", T0 + 4 * H4), rec("b", T0 + 5 * H4)], "rejects": []})
    r = AU.walk_forward_audit(rows, market="USDM_PERP", symbol="S", timeframe="4h", step_ms=H4, n_eval=3)
    assert r["violations"].get("TRIANGLE_BREAK_CONFIRMED_TWICE") == 1, r["violations"]


def test_r4_3_box_exit_compares_the_breakout_to_the_entry_bar_not_to_the_fill_time():
    from tradingbot import box_theory
    day0 = T0 + 300 * DAY
    daily, m5 = box_day(day0=day0, ending="none")
    t = m5[-1]["timestamp"]                                       # girişin kullandığı son 5m barı (kapanış t+5dk)
    rows = list(m5) + [_bar(t + M5, 104.2, 105.9, 104.1, 105.6),    # kutu (105) üstünde 1. kapanış
                       _bar(t + 2 * M5, 105.6, 106.0, 105.3, 105.8)]  # 2. kapanış → teyit t+3*5dk
    fill = t + 3 * M5 + 120_000                                   # yavaş tur: dolum teyitten SONRA
    ctx = SB.StructureContext(mode="ENFORCE", symbol="SYN/USDT", as_of_ms=int(rows[-1]["timestamp"]) + M5, price=rows[-1]["close"])

    def pos(entry_bar):
        feats = {"structure": {}, "data_source": {"bars": {"5m": entry_bar, "1d": day0 - DAY}}}
        return SimpleNamespace(opened_at=_iso(fill), side=SimpleNamespace(value="SHORT"), features=feats, fills=[])
    act, dec, _ = SB.box_decide("b1_box_fade", daily_rows=daily, m5_rows=rows, base=None, position=pos(t),
                                params=box_theory.BoxParams(), ctx=ctx)
    assert dec["action"] == P.ACT_EXIT and act["action"] == "CLOSE", (dec["action"], dec["reason_code"])
    assert dec["detail"]["entry_reference"] == "entry_bar_close" and dec["detail"]["entry_reference_ms"] == t + M5
    # giriş barı kırılımın teyidinden SONRA ise (bilgi girişte vardı) çıkış yok
    _, dec2, _ = SB.box_decide("b1_box_fade", daily_rows=daily, m5_rows=rows, base=None, position=pos(t + 2 * M5),
                               params=box_theory.BoxParams(), ctx=ctx)
    assert dec2["action"] != P.ACT_EXIT


def test_r4_4_the_box_is_the_day_before_the_evaluated_5m_bar_also_between_00_00_and_00_05_utc():
    from tradingbot import box_theory
    day0 = T0 + 300 * DAY
    daily, _ = box_day(day0=day0, ending="none")                  # son günlük bar = day0-1: kutu (95, 105)
    m5 = neutral_trend(288, start_ms=day0, step=M5, px0=101.0, up=True, scale=0.02)   # day0'ın tüm 5m barları
    d_bar = _bar(day0, m5[0]["open"], max(r["high"] for r in m5), min(r["low"] for r in m5), m5[-1]["close"])
    assert d_bar["high"] != 105.0
    daily_now = daily + [d_bar]                                   # 00:02: day0'ın günlük barı da kapandı
    st = box_theory.rule_state("b1_box_fade", daily_rows=daily_now, m5_rows=m5)
    assert (st["box_high"], st["box_low"]) == (105.0, 95.0), "değerlendirilen bar day0'a ait → kutu day0-1"
    assert box_theory.read_box(daily_now, before_ms=day0) == (105.0, 95.0)
    nxt = m5 + [_bar(day0 + DAY, m5[-1]["close"], m5[-1]["close"] + 0.1, m5[-1]["close"] - 0.1, m5[-1]["close"])]
    st2 = box_theory.rule_state("b1_box_fade", daily_rows=daily_now, m5_rows=nxt)
    assert (st2["box_high"], st2["box_low"]) == (d_bar["high"], d_bar["low"]), "yeni günün ilk barı → kutu day0"
    bad = daily[:-1] + [dict(daily[-1], timestamp=None)]
    assert box_theory.read_box(bad, before_ms=day0) is None, "günü okunamayan bar: fail-closed"


def test_r4_5_a_structure_error_is_counted_once_in_live_and_in_replay(tmp_path, monkeypatch):
    import test_structures_strategy_books_v1 as S

    from tradingbot import paper_rules

    def boom(*a, **k):
        raise RuntimeError("yapı katmanı arızası")
    monkeypatch.setattr(SB, "trend_decide", boom)
    flag, brk = S._daily_flag()
    btc = S._btc(len(brk))
    opened = {"action": "OPEN", "direction": "LONG", "stop": brk[-1]["close"] * 0.9, "name": "t2_trend_regime", "leverage": 2}
    monkeypatch.setattr(paper_rules, "decide_from_rows", lambda *a, **k: dict(opened))
    book = S._book(S._cfg(tmp_path, "ENFORCE"), "t2_trend_regime")
    S._step(book, {"1d": brk}, as_of_ms=S._asof(brk), price=brk[-1]["close"], btc=btc)
    assert S.SYM not in book.ledger.positions, "ENFORCE: yapı ölçülemezken yeni giriş yok"
    assert book.rejections.get("STRUCTURE_ERROR:RuntimeError") == 1 and book.counters["rejected"] == 1, (book.rejections, book.counters)
    # replay: aynı ortak geri düşüş, aynı sayaç
    got: list = []
    eng = SimpleNamespace(tf="1d", frames={}, ledger2=SimpleNamespace(positions={}, history=[]), run_id="r",
                          _strategy_marks_f={S.SYM: brk[-1]["close"]}, _reject=lambda s, why: got.append((s, why)))
    fn = paper_rules.replay_strategy("t2_trend_regime", mode="ENFORCE")
    act = fn(S.SYM, int(brk[-1]["timestamp"]), {"1d": pd.DataFrame(brk)}, None, eng)
    assert act["action"] == "NONE" and got == [(S.SYM, "STRUCTURE_ERROR:RuntimeError")], (act, got)


# ============================================================================ TUR 5 (düzeltmelerin doğrulaması, 5fa4312)
def test_r5_1_a_breakout_closing_on_the_predecessors_superseded_bar_is_confirmed_by_the_successor():
    """Kırılım, selefin yerini aldığı barda kapanır: selefin LONG'u o barda yerini alır, SHORT'u aynı kapanışla bozulur.
    Bozulma düz çifti ÇÖZMEZ; kırılımı ardıl teyit eder (5fa4312'de HİÇBİR kayıt teyit etmiyordu)."""
    rows = _tri_rows(tail=[(39, 109.5), (40, 110.9), (44, 111.4)])        # ilk kapanış > düz tepe: bar 100 = q3 teyidi
    conf: dict = {}
    for end in range(96, len(rows) + 1):
        A.clear_cache()
        an = A.analyze(market="USDM_PERP", symbol="SYN/USDT", timeframe="4h", bars=rows[:end], as_of_ms=int(rows[end - 1]["timestamp"]) + H4)
        for r in an["records"]:
            if r["name"] == "ASCENDING_TRIANGLE" and r.get("confirmed_at_ms") is not None:
                conf.setdefault((r["side"], r["pattern_id"]), (int(r["confirmed_at_ms"]) - T0) // H4 - 1)
    assert sorted((k[0], v) for k, v in conf.items()) == [(K.LONG, 100)], conf


def _chart_rec(pid, *, conf_bar, level, tf="4h", sym="SYN/USDT", name="ASCENDING_TRIANGLE", side=K.LONG, family=K.FAMILY_CHART):
    step = {"4h": H4, "1d": DAY}[tf]
    return {"pattern_id": pid, "family": family, "name": name, "side": side, "symbol": sym, "timeframe": tf,
            "status": K.ST_CONFIRMED, "confirmed_at_ms": T0 + conf_bar * step, "trigger": {"rule": "close_above", "level": level},
            "invalidation": {"level": level * 0.95}, "stop": level * 0.94, "targets": [level * 1.1], "atr": level * 0.01,
            "expires_at_ms": T0 + (conf_bar + 3) * step, "confirm_bar": {"ts": T0 + (conf_bar - 1) * step, "close": level * 1.002,
                                                                       "low": level * 0.99, "high": level * 1.004}}


class _Used(set):
    """Kimlik kümesi + giriş özetleri (üretimde `bots.UsedStructures`; burada sınıfa bağlı olmadan)."""

    def __init__(self, ids, entries):
        super().__init__(ids)
        self.entries = list(entries)


def test_r5_2_a_sibling_identity_of_an_already_traded_break_opens_no_second_trade():
    a = _chart_rec("a", conf_bar=10, level=64082.7)
    b = _chart_rec("b", conf_bar=12, level=64306.3)             # BTC 4h örneği: aynı direnç, 2 bar sonra, tetik %0,35 farklı
    used = _Used({"a"}, [{k: a[k] for k in ("pattern_id", "family", "name", "side", "symbol", "timeframe",
                                            "confirmed_at_ms", "trigger")}])
    an = {"4h": {"market": "USDM_PERP", "records": [b]}, "1d": {"market": "USDM_PERP", "records": []}}
    as_of = int(b["confirmed_at_ms"]) + 60_000
    pol = P.POLICIES[P.BOT_MAIN]
    d_used = P.entry_decision(pol, intended_side=K.LONG, analyses=an, as_of_ms=as_of, used_patterns=used, entry_type="pullback")
    d_new = P.entry_decision(pol, intended_side=K.LONG, analyses=an, as_of_ms=as_of, used_patterns=set(), entry_type="pullback")
    assert d_new["action"] == P.ACT_ENTER and d_new["primary"]["pattern_id"] == "b"
    assert d_used["action"] == P.ACT_WAIT and d_used["reason_code"] == "PULLBACK_NEEDS_CONFIRMED_STRUCTURE", \
        "ana bot geri çekilmesi aynı kırılımın kardeş kimliğiyle İKİNCİ kez girmez"
    assert P.same_break(a, b) and P.same_break(b, a)
    assert not P.same_break(a, _chart_rec("c", conf_bar=13, level=64306.3)), "taze pencere (2 bar) dışı: yeni olay"
    assert not P.same_break(a, _chart_rec("d", conf_bar=11, level=66000.0)), "tolerans (%1,5) dışı seviye"
    assert not P.same_break(a, _chart_rec("e", conf_bar=11, level=64100.0, sym="OTH/USDT"))
    assert not P.same_break(dict(a, family=K.FAMILY_CANDLE), dict(b, family=K.FAMILY_CANDLE)), "yalnız grafik yapıları"
    # defterden okunan kullanılmış yapılar giriş özetini sembolüyle taşır (canlı/replay ve formasyon aynı kaynak)
    from tradingbot.structures.bots import used_patterns_of
    led = SimpleNamespace(positions={"SYN/USDT": SimpleNamespace(symbol="SYN/USDT", features={"structure": dict(
        used.entries[0], action="ENTER")})}, history=[])
    u2 = used_patterns_of(led)
    assert "a" in u2 and P.already_used(b, u2) and not P.already_used(_chart_rec("z", conf_bar=30, level=70000.0), u2)


def test_r5_2b_the_pattern_bot_cancels_a_plan_whose_record_confirms_as_an_already_traded_break(tmp_path):
    book = _book(tmp_path)
    now = datetime.fromtimestamp(T0 / 1000, tz=timezone.utc)
    a = _chart_rec("pa", conf_bar=10, level=50.0, tf="4h", sym="LNG/USDT")
    b = _chart_rec("pb", conf_bar=11, level=50.1, tf="4h", sym="LNG/USDT")
    book.ledger.history.append(SimpleNamespace(symbol="LNG/USDT", features={"structure": {
        "action": "ENTER", "pattern_id": "pa", "family": a["family"], "name": a["name"], "side": a["side"], "timeframe": "4h",
        "confirmed_at_ms": a["confirmed_at_ms"], "trigger": dict(a["trigger"])}}))
    pl = dict(_plan("AWAITING_TRIGGER", pid="pb", tf="4h"), family="D2_CHART_STRUCTURE")
    out, cs = {"triggered": 0}, {"triggered": 0}
    an = {"4h": {"market": "USDM_PERP", "analysis_id": "x", "records": [b]}}
    book._follow_record(pl, an, as_of_ms=int(b["confirmed_at_ms"]), dec_ms=int(b["confirmed_at_ms"]), now=now, out=out, cs=cs,
                        levels_1h=None)
    assert pl["status"] == "CANCELLED" and pl["reasons"][-1] == "SAME_BREAK_ALREADY_USED", (pl["status"], pl.get("reasons"))
    pl2 = dict(_plan("AWAITING_TRIGGER", pid="pb", tf="4h"), family="D2_CHART_STRUCTURE")
    book.ledger.history.pop()
    book._follow_record(pl2, an, as_of_ms=int(b["confirmed_at_ms"]), dec_ms=int(b["confirmed_at_ms"]), now=now, out=out, cs=cs,
                        levels_1h=None)
    assert pl2["status"] == "TRIGGERED", "kullanılmış kırılım yoksa aynı kayıt tetikler"


@pytest.mark.parametrize("mode", ["SHADOW", "OFF"])
def test_r5_3_a_leftover_v2_plan_is_cancelled_outside_enforce_and_opens_no_position(tmp_path, mode):
    from test_pattern_trader_v1 import CLOCK, M15, _set_mark
    from test_trading2_fix_plan_expiry_v1 import _triggered_plan_waiting_for_price
    p, sc, book, plan, rows15 = _triggered_plan_waiting_for_price(tmp_path)
    plan.update(version="pattern_protocol_v2.0.0", pattern_id="deadbeefdeadbeef", entry_tf="15m",
                structure={"pattern_id": "deadbeefdeadbeef", "name": "BULL_FLAG", "policy_version": K.POLICY_VERSION})
    book.structure_mode = mode
    CLOCK[0] = int(plan["expires_at_ms"]) - M15
    _set_mark(p, "LNG/USDT", rows15[40]["close"])
    try:
        sc.scan_cycle(now_ms=CLOCK[0])
    finally:
        CLOCK[0] = T0
    assert plan["status"] == "CANCELLED" and plan["reasons"][-1] == "STRUCTURE_MODE_%s" % mode, (plan["status"], plan.get("reasons"))
    assert "LNG/USDT" not in book.ledger.positions


def test_r5_4_the_box_needs_exactly_yesterdays_daily_bar_and_the_end_of_day_close_does_not_need_the_box():
    from tradingbot import box_theory
    day0 = T0 + 300 * DAY
    daily, m5 = box_day(day0=day0, ending="none")                  # son günlük bar = day0-1 (kutu 95-105)
    lag = daily[:-1]                                               # dünün günlük barı henüz gelmedi (gecikme)
    assert box_theory.read_box(lag, before_ms=day0) is None, "iki gün önceki kutu SESSİZCE kullanılmaz"
    assert box_theory.read_box(daily, before_ms=day0) == (105.0, 95.0)
    assert box_theory.rule_state("b1_box_fade", daily_rows=lag, m5_rows=m5)["reason"] == "BOX_DAY_BAR_MISSING"
    pos = {"opened_ts": day0 - 3_600_000}                          # dün açılmış pozisyon
    for d in (daily, lag, []):
        act = box_theory.decide("b1_box_fade", daily_rows=d, m5_rows=m5, position=pos)
        assert act and act["action"] == "CLOSE" and act["reason"] == "BOX_EOD_FLAT", (len(d), act)


def test_r5_5_a_base_rule_exception_is_counted_once_as_strategy_error_in_live_like_replay(tmp_path, monkeypatch):
    import test_structures_strategy_books_v1 as S

    from tradingbot import paper_rules

    def boom(*a, **k):
        raise RuntimeError("botun kendi kuralı arızalı")
    monkeypatch.setattr(paper_rules, "decide_from_rows", boom)
    flag, brk = S._daily_flag()
    book = S._book(S._cfg(tmp_path, "ENFORCE"), "t2_trend_regime")
    S._step(book, {"1d": brk}, as_of_ms=S._asof(brk), price=brk[-1]["close"], btc=S._btc(len(brk)))
    assert book.rejections == {"STRATEGY_ERROR:RuntimeError": 1} and book.counters["rejected"] == 1, (book.rejections, book.counters)


# ============================================================================ TUR 6 (düzeltmelerin doğrulaması, 41e3489)
def test_r6_1_the_pattern_bot_opens_no_second_trade_on_a_sibling_of_the_break_it_traded(tmp_path):
    """ÜRETİM tarama yolu: gerçek bayrak işlemi → hedefte kapanır → sonraki taramanın analizinde aynı kırılımın kardeş
    yorumu (yeni kimlik, tetik +%0,3, bir bar sonra teyit). 41e3489'da giriş kaydı `side`/`trigger` taşımadığı için
    kardeş kullanılmamış sayılıyor ve İKİNCİ işlem açılıyordu."""
    import copy

    import test_structures_pattern_bot_v2 as V
    from test_pattern_trader_v1 import CLOCK, M15, _exinfo, _provider, _scanner, _set_mark
    from test_pattern_trader_v1 import T0 as PT0
    try:
        CLOCK[0] = PT0
        flag, brk = V._flag15()
        p = _provider({V.SYM: V._frames(brk)}, _exinfo(V.EX))
        sc, book = _scanner(V._enforce_cfg(tmp_path), p)
        CLOCK[0] = int(flag[-1]["timestamp"]) + M15
        _set_mark(p, V.SYM, flag[-1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
        CLOCK[0] = int(brk[-1]["timestamp"]) + M15
        _set_mark(p, V.SYM, brk[-1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
        pos1 = book.ledger.positions[V.SYM]
        st1 = pos1.features["structure"]
        rec1 = copy.deepcopy(next(r for r in book._scan_analyses["15m"]["records"] if r["pattern_id"] == st1["pattern_id"]))
        plan1 = next(pl for pl in book.plans.values() if pl.get("position_id") == pos1.id)
        CLOCK[0] += M15
        _set_mark(p, V.SYM, float(plan1["target"]) * 1.001)
        sc.exit_check()
        assert V.SYM not in book.ledger.positions
        sib = copy.deepcopy(rec1)
        sib.update(pattern_id="sib" + rec1["pattern_id"][3:], confirmed_at_ms=int(rec1["confirmed_at_ms"]) + M15,
                   expires_at_ms=int(rec1["confirmed_at_ms"]) + 4 * M15)
        sib["trigger"] = dict(rec1["trigger"], level=float(rec1["trigger"]["level"]) * 1.003)
        assert P.same_break(rec1, sib)
        orig = book._structure_analyses

        def with_sibling(*a, **k):
            out = orig(*a, **k)
            if out.get("15m"):
                out["15m"] = dict(out["15m"], records=list(out["15m"]["records"]) + [sib])
            return out
        book._structure_analyses = with_sibling
        CLOCK[0] = int(sib["confirmed_at_ms"]) + 60_000
        _set_mark(p, V.SYM, float(sib["trigger"]["level"]) * 1.001)
        sc.scan_cycle(now_ms=CLOCK[0])
        assert V.SYM not in book.ledger.positions and book.counters["opened"] == 1, "aynı kırılım İKİNCİ işlem açmaz"
        assert not any(pl.get("pattern_id") == sib["pattern_id"] and pl.get("status") in ("OPENED", "MANAGED", "CLOSED")
                       for pl in book.plans.values())
        assert st1.get("side") == K.LONG and (st1.get("trigger") or {}).get("level") is not None, \
            "giriş kaydı aynı kırılım alanlarını (taraf, tetik) taşır"
    finally:
        CLOCK[0] = PT0


def test_r6_2_the_opposite_side_confirming_first_does_not_swallow_the_later_flat_break():
    """Eğik taraf (SHORT) önce aşağı kırılır ve teyit olur, fiyat döner, q3 teyidiyle düz tepe yukarı kırılır: bu FARKLI
    kırılımı ardıl teyit eder (41e3489'da çift bütünüyle çözüldüğü için hiçbir kayıt teyit etmiyordu)."""
    way = [(0, 100.0), (10, 110.0), (16, 101.0), (21, 108.0), (26, 103.0), (32, 110.1), (33, 108.5), (34, 107.0),
           (35, 105.5), (36, 104.2), (37, 104.0), (38, 106.0), (39, 108.0), (40, 110.9), (44, 111.4)]
    rows = _tri_rows(way=way)
    conf: dict = {}
    for end in range(90, len(rows) + 1):
        A.clear_cache()
        an = A.analyze(market="USDM_PERP", symbol="SYN/USDT", timeframe="4h", bars=rows[:end], as_of_ms=int(rows[end - 1]["timestamp"]) + H4)
        for r in an["records"]:
            if r["name"] == "ASCENDING_TRIANGLE" and r.get("confirmed_at_ms") is not None:
                conf.setdefault((r["side"], r["pattern_id"]), (int(r["confirmed_at_ms"]) - T0) // H4 - 1)
    got = sorted((k[0], v) for k, v in conf.items())
    assert got == [(K.LONG, 100), (K.SHORT, 96)], got


def test_r6_3_a_leftover_v1_plan_is_cancelled_in_enforce_and_opens_no_position(tmp_path):
    from test_pattern_trader_v1 import CLOCK, M15, _set_mark
    from test_pattern_trader_v1 import T0 as PT0
    from test_trading2_fix_plan_expiry_v1 import _triggered_plan_waiting_for_price
    p, sc, book, plan, rows15 = _triggered_plan_waiting_for_price(tmp_path)
    assert plan.get("version") != "pattern_protocol_v2.0.0"
    book.structure_mode = "ENFORCE"
    CLOCK[0] = int(plan["expires_at_ms"]) - M15
    _set_mark(p, "LNG/USDT", rows15[40]["close"])
    try:
        sc.scan_cycle(now_ms=CLOCK[0])
    finally:
        CLOCK[0] = PT0
    assert plan["status"] == "CANCELLED" and plan["reasons"][-1] == "STRUCTURE_MODE_ENFORCE_V1_PLAN", (plan["status"], plan.get("reasons"))
    assert "LNG/USDT" not in book.ledger.positions
