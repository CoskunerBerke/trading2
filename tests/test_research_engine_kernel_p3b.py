"""P3b-1 — araştırma çekirdeği birim testleri (docs/ENGINE_BRAIN_V1.md §2.1, §3.3; `research_engine/kernel.py`).

Elle kurulmuş barlar ve betikli bir varyantla: karar bar kapanışında / dolum sonraki açılışta ± kayma; bar içi sıra
(açılış → ters → lehte → kapanış); stop seviyede, boşlukta açılışta; TP1 kısmi + başa-baş stop yalnız sonraki noktalarda;
fonlama deponun gerçek uzlaşma satırlarında (nominal dakikaya yuvarlanmış damga, açık miktar, çıkış barındaki uzlaşmanın
ihtiyatlı kuralı); `FUNDING_GAP` yerel aralıkla (8h → 4h'e geçen seri); likidasyon (bracket formülü, izole marj sınırı);
sabit 1 USDT risk ve likidasyon mesafesi ≥ 2 × stop; üç maliyet senaryosunun aynı yol üzerinde aritmetik yeniden
maliyetlendirmesi (elle hesap); giriş kapıları (veri boşluğu, maliyet sonrası R/R dahil); MOVE_STOP doğrulaması; tek
giriş; zorlanmış tek niyet yeniden oynatımı; belirlenimcilik. Ağ yok, gerçek veri yok.
"""
from __future__ import annotations

import json
import math

import pytest

from tradingbot import strategy_lib as SL
from tradingbot.research_engine import kernel as KN

D1 = 86_400_000
T0 = 1_577_836_800_000          # 2020-01-01T00:00Z
FEE, SLIP = 0.0005, 0.0003


def bar(i: int, o: float, h: float, lo: float, c: float) -> dict:
    return {"timestamp": T0 + i * D1, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def flat(n: int, px: float = 100.0) -> list[dict]:
    return [bar(i, px, px * 1.001, px * 0.999, px) for i in range(n)]


class Scripted(SL.Variant):
    """Betikli varyant: `opens[i]` = i. barın kapanışında verilecek niyetin alanları; `acts[i]` = i. barın kapanışında
    açık pozisyona eylem."""

    variant_id, family, signal_tf, timeframes = "TEST", "T", "1d", ("1d",)

    def __init__(self, opens: dict[int, dict] | None = None, acts: dict[int, SL.Action] | None = None):
        self.opens, self.acts = dict(opens or {}), dict(acts or {})
        self.lookback = {"1d": 5}
        self.seen: list[tuple[int, int]] = []

    def _i(self, ctx) -> int:
        return (int(ctx["now_ms"]) - T0) // D1 - 1

    def signals(self, bars, ctx):
        i = self._i(ctx)
        assert all(int(b["timestamp"]) + D1 <= int(ctx["now_ms"]) for b in bars["1d"]), "yalnız kapanmış barlar"
        self.seen.append((i, len(bars["1d"])))
        spec = self.opens.get(i)
        if spec is None:
            return []
        d = {"side": SL.LONG, "targets": (), "one_entry_per_signal": False, "risk_atr_bounds": None,
             "max_hold_bars": None, "entry_ref": SL.EntryRef(price=float(bars["1d"][-1]["close"]))}
        d.update(spec)
        return [SL.Intent(signal_ts=T0 + i * D1, **d)]

    def manage(self, pos, bars, ctx):
        return self.acts.get(self._i(ctx), SL.NONE)


def run(bars, var, funding=None, tick=0.0, **kw):
    return KN.run(var, KN.SeriesData(symbol="TST", bars={"1d": bars}, funding=funding, price_tick=tick), **kw)


def r_by_hand(e_ref: float, stop: float, exits: list[tuple[float, float, bool]], *, fee=FEE, slip=SLIP, spread=0.0,
              side=1.0, funding_r: float = 0.0) -> float:
    """exits: (referans, pay, piyasa mı). Miktar 1 birim; fonlama R cinsinden ayrıca."""
    e = e_ref * (1 + side * slip) + side * spread
    g = f = 0.0
    f += fee * e
    for ref, frac, market in exits:
        x = ref * (1 - side * slip) - side * spread if market else ref - side * spread
        g += (x - e) * frac * side
        f += fee * x * frac
    return (g - f) / abs(e - stop) + funding_r


# ============================================================================ dolum, yol, stop
def test_decision_at_close_fill_at_next_open_and_stop_at_level():
    b = flat(6)
    b[2] = bar(2, 100.0, 104.0, 96.0, 102.0)
    b[3] = bar(3, 101.0, 101.5, 94.0, 95.5)        # ters uç stopun ötesinde → seviyede (95)
    var = Scripted(opens={1: {"stop": 95.0}})
    res = run(b, var)
    (t,) = res.trades
    assert (t.entry_ts, t.entry_ref, t.signal_ts) == (T0 + 2 * D1, 100.0, T0 + D1)
    assert t.exit_kind == KN.EXIT_STOP and t.exit_reason == "STOP" and t.legs[0].ref == 95.0 and t.legs[0].point == "adverse"
    assert t.exit_ts == T0 + 4 * D1 and t.bars_held == 2
    assert t.r["base"] == pytest.approx(r_by_hand(100.0, 95.0, [(95.0, 1.0, True)]), abs=1e-12)
    assert all(n <= 5 for _i, n in var.seen), "pencere varyantın lookback'iyle sınırlı"


def test_gap_through_stop_fills_at_the_open_not_the_level():
    b = flat(6)
    b[3] = bar(3, 93.0, 94.0, 92.0, 93.5)          # açılış stopun ötesinde (boşluk)
    res = run(b, Scripted(opens={1: {"stop": 95.0}}))
    (t,) = res.trades
    assert t.exit_reason == "GAP_STOP" and t.legs[0].ref == 93.0 and t.legs[0].point == "open" and t.exit_ts == T0 + 3 * D1
    assert t.r["base"] == pytest.approx(r_by_hand(100.0, 95.0, [(93.0, 1.0, True)]), abs=1e-12)


def test_entry_on_a_gap_beyond_the_stop_is_rejected_like_apply_action():
    b = flat(6)
    b[2] = bar(2, 94.0, 96.0, 93.0, 95.0)          # sinyalden sonra açılış stopun altında
    res = run(b, Scripted(opens={1: {"stop": 95.0}}))
    assert res.trades == [] and res.rejected == {KN.REJ_BAD_STOP: 1}


def test_intrabar_order_open_adverse_favourable_close_stop_before_target():
    b = flat(6)
    b[3] = bar(3, 100.0, 120.0, 90.0, 110.0)        # aynı barda stop (95) ve hedef (110): ters uç önce
    res = run(b, Scripted(opens={1: {"stop": 95.0, "targets": (SL.Target(SL.TARGET_PRICE, 110.0),)}}))
    (t,) = res.trades
    assert t.exit_kind == KN.EXIT_STOP and t.legs[0].point == "adverse"
    b[3] = bar(3, 100.0, 120.0, 97.0, 90.0)          # ters uç güvenli → lehte uç hedefi alır (kapanış önemsiz)
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0, "targets": (SL.Target(SL.TARGET_PRICE, 110.0),)}})).trades
    assert t.exit_kind == KN.EXIT_TARGET and t.legs[0].point == "favourable" and t.legs[0].ref == 110.0
    assert not t.legs[0].market, "hedef seviyede, kaymasız dolar (defter tiki)"
    assert t.r["base"] == pytest.approx(r_by_hand(100.0, 95.0, [(110.0, 1.0, False)]), abs=1e-12)


def test_tp1_partial_moves_stop_to_breakeven_only_for_later_points():
    tg = (SL.Target(SL.TARGET_PRICE, 105.0), SL.Target(SL.TARGET_PRICE, 120.0))
    b = flat(6)
    b[2] = bar(2, 100.0, 106.0, 99.0, 99.5)          # lehte uç TP1 → stop başa-başa; kapanış başa-başın altında
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0, "targets": tg}})).trades
    be = KN.break_even(True, 100.0 * (1 + SLIP), FEE, SLIP)
    assert [(lg.kind, lg.frac, lg.point) for lg in t.legs] == [(KN.EXIT_TARGET, 0.5, "favourable"), (KN.EXIT_STOP, 0.5, "close")]
    assert t.legs[1].ref == pytest.approx(be) and t.exit_reason == "BREAKEVEN_STOP"
    assert t.r["base"] == pytest.approx(r_by_hand(100.0, 95.0, [(105.0, 0.5, False), (be, 0.5, True)]), abs=1e-12)
    # aynı barın TERS ucu (önce gelir) yeni stopu sınamaz: düşük 99 < başa-baş ama yol "sürer"
    b = flat(2) + [bar(2, 100.0, 106.0, 99.0, 103.0)] + [bar(i, 103.0, 103.1, 102.9, 103.0) for i in range(3, 6)]
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0, "targets": tg}})).trades
    assert t.exit_kind == KN.EXIT_END and t.legs[0].kind == KN.EXIT_TARGET and KN.FLAG_OPEN_AT_END in t.flags


def test_r_from_entry_target_is_measured_from_the_real_entry():
    b = flat(6)
    b[2] = bar(2, 102.0, 103.0, 101.0, 102.5)
    (t,) = run(b, Scripted(opens={1: {"stop": 98.0, "targets": (SL.Target(SL.TARGET_R_FROM_ENTRY, 2.0),)}})).trades
    assert t.targets == (102.0 + 2 * 4.0,)


# ============================================================================ kural çıkışı, zaman stopu, MOVE_STOP
def test_rule_close_and_time_stop_execute_at_the_next_open():
    b = flat(8)
    b[4] = bar(4, 101.0, 102.0, 100.5, 101.5)
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}, acts={3: SL.close("RULE_X")})).trades
    assert (t.exit_kind, t.exit_reason, t.exit_ts, t.legs[0].ref, t.legs[0].market) == (KN.EXIT_RULE, "RULE_X", T0 + 4 * D1,
                                                                                        101.0, True)
    (t,) = run(flat(8), Scripted(opens={1: {"stop": 95.0, "max_hold_bars": 3}})).trades
    assert t.exit_kind == KN.EXIT_TIME and t.exit_ts == T0 + 5 * D1 and t.exit_reason == "TIME_STOP_3_BARS"


def test_gap_at_the_open_wins_over_a_pending_rule_close():
    b = flat(8)
    b[4] = bar(4, 90.0, 91.0, 89.0, 90.5)
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}, acts={3: SL.close("RULE_X")})).trades
    assert t.exit_kind == KN.EXIT_STOP and t.exit_reason == "GAP_STOP"


def test_move_stop_applies_from_the_next_observation():
    b = flat(8)
    b[4] = bar(4, 100.0, 100.5, 98.5, 99.0)
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}, acts={3: SL.move_stop(99.0)})).trades
    assert t.exit_kind == KN.EXIT_STOP and t.legs[0].ref == 99.0 and t.exit_ts == T0 + 5 * D1
    assert t.flags == [KN.FLAG_FUNDING_GAP] and t.move_stop_ignored == {}


def test_move_stop_only_tightens_and_never_crosses_the_price():
    b = flat(8)
    b[4] = bar(4, 100.0, 100.5, 93.5, 99.0)          # gevşetilmiş stop (93) burada vururdu; geçerli stop 95'te vurur
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}, acts={3: SL.move_stop(93.0)})).trades
    assert t.legs[0].ref == 95.0 and t.move_stop_ignored == {KN.MOVE_LOOSENS: 1} and KN.FLAG_MOVE_STOP_IGNORED in t.flags
    b = flat(8)                                      # karar fiyatı 100: 101'e stop fiyatı geçer → yok sayılır
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}, acts={3: SL.move_stop(101.0), 4: SL.move_stop(100.0)})).trades
    assert t.move_stop_ignored == {KN.MOVE_CROSSES_PRICE: 2} and t.exit_kind == KN.EXIT_END
    b = flat(8)
    b[4] = bar(4, 100.0, 106.0, 100.0, 101.0)        # SHORT: sıkılaştırma aşağı, fiyatın ÜSTÜNDE kalmalı
    (t,) = run(b, Scripted(opens={1: {"stop": 105.0, "side": SL.SHORT}}, acts={3: SL.move_stop(104.0)})).trades
    assert t.exit_kind == KN.EXIT_STOP and t.legs[0].ref == 104.0 and t.move_stop_ignored == {}
    (t,) = run(flat(8), Scripted(opens={1: {"stop": 105.0, "side": SL.SHORT}},
                                  acts={3: SL.move_stop(106.0), 4: SL.move_stop(99.0)})).trades
    assert t.move_stop_ignored == {KN.MOVE_LOOSENS: 1, KN.MOVE_CROSSES_PRICE: 1}
    assert KN.move_stop_verdict(SL.LONG, 95.0, 95.0, 100.0) is None, "eşit stop gevşetme değildir"


# ============================================================================ giriş kapıları ve tek giriş
def test_entry_gates_drift_risk_range_chase():
    b = flat(6)
    b[2] = bar(2, 103.0, 104.0, 102.0, 103.0)
    ref = SL.EntryRef(price=100.0, max_drift_pct=1.0)
    assert run(b, Scripted(opens={1: {"stop": 95.0, "entry_ref": ref}})).rejected == {KN.REJ_DRIFT: 1}
    rb = SL.RiskAtrBounds(0.1, 2.0, 1.0)            # |103 − 95| = 8 > 2 ATR
    assert run(b, Scripted(opens={1: {"stop": 95.0, "risk_atr_bounds": rb}})).rejected == {KN.REJ_RISK_RANGE: 1}
    ch = SL.EntryRef(price=100.0, anchor=100.0, max_anchor_dist=2.0)
    assert run(b, Scripted(opens={1: {"stop": 95.0, "entry_ref": ch}})).rejected == {KN.REJ_CHASE: 1}
    ok = SL.RiskAtrBounds(0.1, 10.0, 1.0)
    assert len(run(b, Scripted(opens={1: {"stop": 95.0, "risk_atr_bounds": ok}})).trades) == 1


def test_entry_after_a_data_gap_is_rejected():
    b = flat(8)
    del b[2]                                         # karar 1. barın kapanışında; sonraki satır bir gün sonra açılıyor
    res = run(b, Scripted(opens={1: {"stop": 95.0}}))
    assert res.trades == [] and res.rejected == {KN.REJ_DATA_GAP: 1}
    b = flat(8)
    res = run(b, Scripted(opens={1: {"stop": 95.0}}))
    assert len(res.trades) == 1 and res.trades[0].entry_ts == T0 + 2 * D1


def test_rr_below_min_at_entry_gate_uses_the_live_formula():
    from tradingbot.pattern_trader.strategy import rr_after_cost
    cf = 0.0016
    tg = (SL.Target(SL.TARGET_R_FROM_ENTRY, 2.0),)
    b = flat(6)
    b[2] = bar(2, 100.0, 100.05, 99.95, 100.0)
    for stop, ok in ((99.8, False), (99.5, True)):     # mesafe %0,2 (maliyet tabanın altında) / %0,5
        ref = SL.EntryRef(price=100.0, min_rr_after_cost=1.0, rr_cost_frac=cf)
        res = run(b, Scripted(opens={1: {"stop": stop, "targets": tg, "entry_ref": ref}}))
        net = rr_after_cost(100.0, stop, 100.0 + 2.0 * (100.0 - stop), cost_frac=cf)[1]
        assert (net >= 1.0) == ok
        assert (len(res.trades) == 1) == ok and (res.rejected == {} if ok else res.rejected == {KN.REJ_RR: 1})
    with pytest.raises(ValueError):
        SL.EntryRef(price=1.0, min_rr_after_cost=1.0)


def test_forced_replay_of_one_intent_equals_the_continuous_run():
    b = flat(12)
    b[4] = bar(4, 100.0, 100.5, 94.0, 96.0)
    b[9] = bar(9, 100.0, 111.0, 99.5, 110.0)
    var = Scripted(opens={1: {"stop": 95.0}, 6: {"stop": 96.0, "targets": (SL.Target(SL.TARGET_PRICE, 108.0),)}})
    data = KN.SeriesData(symbol="TST", bars={"1d": b}, funding=[(T0 + k * 8 * 3_600_000, 0.0003, 100.0) for k in range(60)])
    full = KN.run(var, data)
    assert len(full.trades) == 2
    for t in full.trades:
        i = (t.signal_ts - T0) // D1
        (it,) = KN.signals_at(var, data, t.signal_ts + D1)
        assert it.signal_ts == t.signal_ts
        tr, why = KN.replay_intent(var, data, it, decision_ms=t.signal_ts + D1)
        assert why is None and tr.to_dict() == t.to_dict(), i
    tr, why = KN.replay_intent(var, data, it, decision_ms=T0 + 50 * D1)
    assert tr is None and why == "DECISION_BAR_MISSING"
    late = SL.Intent(signal_ts=T0 + 3 * D1, side=SL.LONG, entry_ref=SL.EntryRef(price=100.0), stop=101.0)
    assert KN.replay_intent(var, data, late, decision_ms=T0 + 4 * D1) == (None, KN.REJ_BAD_STOP)


def test_one_entry_per_signal_blocks_a_second_entry_on_the_same_signal_bar():
    class Same(Scripted):
        def signals(self, bars, ctx):
            i = self._i(ctx)
            if i < 1:
                return []
            return [SL.Intent(signal_ts=T0 + D1, side=SL.LONG, entry_ref=SL.EntryRef(price=100.0), stop=95.0,
                              one_entry_per_signal=True)]
    b = flat(8)
    b[3] = bar(3, 100.0, 100.5, 94.0, 96.0)          # ilk işlem stoplanır
    res = run(b, Same())
    assert len(res.trades) == 1 and res.rejected.get("SIGNAL_ALREADY_USED", 0) >= 1


# ============================================================================ büyüklük ve likidasyon
@pytest.mark.parametrize("side,stop", [("LONG", 99.9), ("LONG", 99.0), ("LONG", 90.0), ("SHORT", 100.1), ("SHORT", 103.0)])
def test_sizing_is_one_usdt_risk_and_liquidation_is_at_least_twice_the_stop(side, stop):
    ok, why, lev, notional, margin, liq = KN.size_trade(side, 100.0, stop)
    assert ok, why
    s = abs(100.0 - stop) / 100.0
    assert notional * s == pytest.approx(1.0) or notional == pytest.approx(200.0 * lev)
    assert abs(100.0 - liq) >= 2.0 * abs(100.0 - stop)
    assert margin == pytest.approx(notional / lev) and margin <= 200.0 + 1e-9
    assert lev == max(1, math.ceil(notional / 200.0 - 1e-12)), "kaldıraç gerektiği kadar"


def test_liquidation_on_an_open_gap_beyond_the_liquidation_price():
    b = [bar(i, 100.0, 100.05, 99.95, 100.0) for i in range(3)] + [bar(3, 70.0, 71.0, 69.0, 70.5)] + flat(6)[4:]
    # stop 99,9 ve likidasyonun ötesinde açılış (3. bar)
    (t,) = run(b, Scripted(opens={1: {"stop": 99.9}})).trades
    assert t.leverage > 1 and t.exit_kind == KN.EXIT_LIQ and t.legs[0].ref == t.liq_price
    q, e = t.qty, 100.0 * (1 + SLIP)
    fee = min(q * t.liq_price * KN.LIQ_FEE_PCT / 100.0, t.margin)
    gross = max((t.liq_price - e) * q, -(t.margin - fee))
    assert t.r["base"] == pytest.approx((gross - fee - FEE * e * q + t.funding_usdt) / (abs(e - 99.9) * q), rel=1e-9)
    assert -t.pnl_usdt["base"] <= t.margin + FEE * e * q + 1e-9, "kayıp izole marj (+ giriş ücreti) ile sınırlı"


# ============================================================================ fonlama
def _fund(rows):
    return [(int(t), r, m) for t, r, m in rows]


def test_funding_at_real_settlement_rows_snapped_to_the_nominal_minute():
    b = flat(8)
    b[4] = bar(4, 93.0, 94.0, 92.0, 93.5)            # 4. barın açılışında boşluk stopu (çıkış anı = açılış)
    h = 3_600_000
    rows = _fund([(T0 + k * 8 * h + 1_017, 0.001, 100.0 + k) for k in range(0, 30)])   # gerçek (ms kaymalı) damgalar
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), funding=rows).trades
    assert KN.snap_ms(T0 + 2 * D1 + 1_017) == T0 + 2 * D1 and KN.snap_ms(T0 + 2 * D1 - 29_000) == T0 + 2 * D1
    # giriş barın AÇILIŞINDA doldu: o anın (kaymalı damgalı) uzlaşması yazılmaz; çıkış anınınki (açılışta kapanan) yazılır
    inside = [(KN.snap_ms(ts), r, m) for ts, r, m in rows if T0 + 2 * D1 < KN.snap_ms(ts) <= T0 + 4 * D1]
    assert [x[0] for x in inside] == [T0 + 2 * D1 + k * 8 * h for k in range(1, 7)]
    assert t.funding_events == len(inside) == 6 and t.funding_ambiguous_events == 0
    assert t.funding_usdt == pytest.approx(sum(-t.qty * m * r for _ts, r, m in inside), rel=1e-12)
    assert KN.FLAG_FUNDING_GAP not in t.flags and KN.FLAG_FUNDING_EXIT_BAR not in t.flags
    base_r = r_by_hand(100.0, 95.0, [(93.0, 1.0, True)])
    assert t.r["base"] == pytest.approx(base_r + t.funding_usdt / (abs(100.0 * (1 + SLIP) - 95.0) * t.qty), abs=1e-12)
    for sc in ("ADVERSE", "STRESS"):
        assert t.pnl_usdt[sc] - t.pnl_usdt["base"] == pytest.approx(
            KN.cost_trade(t, KN.scenario(sc), 0.0)[1] - KN.cost_trade(t, KN.scenario("base"), 0.0)[1])
    conflict = rows + [(T0 + 3 * D1 + 2_000, 0.5, 1.0)]       # aynı dakikaya düşen çelişen satır → okunamaz
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), funding=conflict).trades
    assert KN.FLAG_FUNDING_GAP in t.flags


def test_funding_in_the_exit_bar_is_charged_only_when_the_exit_could_not_precede_it():
    """İhtiyatlı kural: bar içi çıkış (ters uçta stop) barın içinde olur. Barın AÇILIŞINDAKİ uzlaşma yazılır (çıkış ondan
    önce olamaz); barın İÇİNDEKİLER yazılmaz ve işlem `FUNDING_EXIT_BAR` işaretlenir (tutar ayrıca); barın KAPANIŞINDAKİ
    yazılmaz ve belirsiz de değildir (çıkış kesin önce)."""
    b = flat(8)
    b[3] = bar(3, 100.0, 100.5, 94.0, 96.0)          # 3. barın ters ucunda stop (95)
    h8 = 8 * 3_600_000
    rows = [(T0 + k * h8, 0.001, 100.0) for k in range(0, 40)]
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), funding=rows).trades
    assert t.legs[0].point == "adverse" and t.legs[0].ts == T0 + 4 * D1 and t.legs[0].earliest == T0 + 3 * D1
    charged = [ts for ts, _r, _m in rows if T0 + 2 * D1 < ts <= T0 + 3 * D1]          # giriş sonrası … çıkış barı açılışı
    assert t.funding_events == len(charged) == 3
    assert t.funding_usdt == pytest.approx(-t.qty * 100.0 * 0.001 * 3, rel=1e-12)
    assert t.funding_ambiguous_events == 2 and KN.FLAG_FUNDING_EXIT_BAR in t.flags      # 08:00 ve 16:00 (bar içi)
    assert t.funding_ambiguous_usdt == pytest.approx(-t.qty * 100.0 * 0.001 * 2, rel=1e-12)
    # 4h barlarda uzlaşma bar sınırındadır: çıkış barının KAPANIŞINDAKİ uzlaşma yazılmaz ve belirsiz değildir
    H4 = 14_400_000
    b4 = [{"timestamp": T0 + i * H4, "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1.0}
          for i in range(10)]
    b4[5] = dict(b4[5], low=94.0, close=96.0)        # 5. bar 20:00–24:00, ters uçta stop; 00:00 uzlaşması kapanışta

    class S4(Scripted):
        signal_tf, timeframes = "4h", ("4h",)

        def __init__(self):
            super().__init__()
            self.lookback = {"4h": 5}

        def signals(self, bars, ctx):
            if int(ctx["now_ms"]) != T0 + 2 * H4:
                return []
            return [SL.Intent(signal_ts=T0 + H4, side=SL.LONG, entry_ref=SL.EntryRef(price=100.0), stop=95.0)]
    rows4 = [(T0 + k * h8, 0.001, 100.0) for k in range(0, 6)]
    (t,) = KN.run(S4(), KN.SeriesData(symbol="TST", bars={"4h": b4}, funding=rows4)).trades
    assert (t.entry_ts, t.legs[0].ts) == (T0 + 2 * H4, T0 + 6 * H4)
    assert t.funding_events == 1 and t.funding_ambiguous_events == 0, \
        "08:00 (giriş anı) yazılmaz, 16:00 yazılır, 00:00 (çıkış barının kapanışı) yazılmaz ve belirsiz değil"


def test_funding_after_a_partial_exit_uses_the_remaining_quantity():
    tg = (SL.Target(SL.TARGET_PRICE, 105.0), SL.Target(SL.TARGET_PRICE, 130.0))
    b = flat(2) + [bar(2, 100.0, 106.0, 99.5, 104.0)] + [bar(i, 104.0, 104.1, 103.9, 104.0) for i in range(3, 8)]
    h12 = 12 * 3_600_000
    paid = {T0 + 2 * D1 + h12, T0 + 3 * D1, T0 + 3 * D1 + h12}
    rows = [(T0 + k * h12, (0.001 if T0 + k * h12 in paid else 0.0), 100.0) for k in range(0, 30)]
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0, "targets": tg}}), funding=rows).trades
    assert t.legs[0].ts == T0 + 3 * D1 and t.legs[0].earliest == T0 + 2 * D1 and t.exit_kind == KN.EXIT_END
    # TP1 giriş barının İÇİNDE (lehte uç) doldu: barın içindeki uzlaşma yalnız kalan yarıya (TP1 yarısı belirsiz →
    # yazılmaz, işaretli); barın kapanışındaki ve sonrakiler kalan yarıyla
    assert t.funding_usdt == pytest.approx(-t.qty * 100.0 * 0.001 * (0.5 + 0.5 + 0.5), rel=1e-12)
    assert t.funding_ambiguous_events == 1 and KN.FLAG_FUNDING_EXIT_BAR in t.flags
    assert t.funding_ambiguous_usdt == pytest.approx(-t.qty * 100.0 * 0.001 * 0.5, rel=1e-12)


def test_funding_gap_is_judged_against_the_local_interval_8h_then_4h():
    """Seri 300 gün 8h, sonra 500 gün 4h: hiçbir aralık boşluk değildir (tüm serinin medyanı 4h olsa da 8h dönemi);
    iki dönemin içinde ve geçişten iki uzlaşma sonra eksik tek uzlaşma (ve bir satır arayla iki eksik) yine yakalanır.
    Geçişin tam sınırındaki tek eksik (son 8h ya da ilk 4h satırı) ayırt edilemez: seri geçişi bir adım önce/sonra
    yapmış gibi görünür."""
    h = 3_600_000
    t8 = [T0 + k * 8 * h for k in range(0, 300 * 3)]
    t4 = [t8[-1] + 8 * h + k * 4 * h for k in range(0, 500 * 6)]
    ts = t8 + t4
    fs = KN.FundingSeries([(t, 0.0001, 100.0) for t in ts])
    assert not any(fs.gap_after), "8h → 4h geçişi boşluk değildir"
    assert fs.window(T0 + 10 * D1, T0 + 700 * D1)[1] is False
    for drop in ([t8[100]], [t4[200]], [t4[2]], [t8[200], t8[202]], [t4[300], t4[302]]):
        holed = KN.FundingSeries([(t, 0.0001, 100.0) for t in ts if t not in drop])
        sel, gap = holed.window(min(drop) - 3 * D1, max(drop) + 3 * D1)
        assert gap, drop
        assert not holed.window(max(drop) + 20 * D1, max(drop) + 40 * D1)[1], "boşluk yalnız onu kuşatan pencerede"
    # bir işlem üzerinden: 8h döneminde tutan bir işlem boşluk işaretlemez
    b = flat(8)
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), funding=[(x, 0.0001, 100.0) for x in ts]).trades
    assert KN.FLAG_FUNDING_GAP not in t.flags and t.funding_events > 0


def test_funding_gap_is_flagged():
    b = flat(8)
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), funding=None).trades
    assert KN.FLAG_FUNDING_GAP in t.flags and t.funding_usdt == 0.0
    h8 = 8 * 3_600_000
    full = [(T0 + k * h8, 0.0001, 100.0) for k in range(0, 40)]
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), funding=full).trades
    assert KN.FLAG_FUNDING_GAP not in t.flags
    holed = [x for x in full if not (T0 + 3 * D1 < x[0] < T0 + 4 * D1)]
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), funding=holed).trades
    assert KN.FLAG_FUNDING_GAP in t.flags
    bad = [(ts, (float("nan") if ts == T0 + 3 * D1 else r), m) for ts, r, m in full]
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), funding=bad).trades
    assert KN.FLAG_FUNDING_GAP in t.flags
    short = [x for x in full if x[0] < T0 + 3 * D1]
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), funding=short).trades
    assert KN.FLAG_FUNDING_GAP in t.flags, "seri pencereyi örtmüyor"


# ============================================================================ maliyet senaryoları
def test_three_cost_scenarios_recost_the_same_path_arithmetically():
    b = flat(6)
    b[3] = bar(3, 101.0, 101.5, 94.0, 95.5)
    tick = 0.01
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}}), tick=tick).trades
    for name in KN.SCENARIOS:
        sc = KN.scenario(name)
        want = r_by_hand(100.0, 95.0, [(95.0, 1.0, True)], fee=sc.fee, slip=sc.slip, spread=sc.spread_ticks * tick)
        assert t.r[name] == pytest.approx(want, abs=1e-12), name
    assert (KN.scenario("base").slippage_bps, KN.scenario("ADVERSE").slippage_bps, KN.scenario("STRESS").slippage_bps) == (3, 6, 9)
    assert (KN.scenario("ADVERSE").spread_ticks, KN.scenario("STRESS").taker_fee_pct) == (1, 0.07)
    assert t.r["base"] > t.r["ADVERSE"] > t.r["STRESS"]
    b[3] = bar(3, 101.0, 112.0, 100.5, 111.0)
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0, "targets": (SL.Target(SL.TARGET_PRICE, 110.0),)}}), tick=tick).trades
    for name in KN.SCENARIOS:
        sc = KN.scenario(name)
        want = r_by_hand(100.0, 95.0, [(110.0, 1.0, False)], fee=sc.fee, slip=sc.slip, spread=sc.spread_ticks * tick)
        assert t.r[name] == pytest.approx(want, abs=1e-12), name


def test_short_side_is_symmetric():
    b = flat(6)
    b[3] = bar(3, 99.0, 106.0, 98.5, 104.0)
    (t,) = run(b, Scripted(opens={1: {"stop": 105.0, "side": SL.SHORT}})).trades
    assert t.exit_kind == KN.EXIT_STOP and t.legs[0].ref == 105.0
    assert t.r["base"] == pytest.approx(r_by_hand(100.0, 105.0, [(105.0, 1.0, True)], side=-1.0), abs=1e-12)


# ============================================================================ belirlenimcilik, pencere
def test_kernel_is_deterministic_and_the_entry_window_limits_only_new_entries():
    b = flat(12)
    b[6] = bar(6, 100.0, 100.2, 94.0, 96.0)
    var = Scripted(opens={1: {"stop": 95.0}, 8: {"stop": 95.0}}, acts={})
    a = json.dumps(run(b, var).to_dict(), sort_keys=True)
    assert a == json.dumps(run(b, var).to_dict(), sort_keys=True)
    res = run(b, var, end_ms=T0 + 3 * D1)
    assert len(res.trades) == 1 and res.trades[0].exit_kind == KN.EXIT_STOP, "pencere sonrası açık pozisyon yönetilir"
    res = run(b, var, start_ms=T0 + 5 * D1)
    assert [t.signal_ts for t in res.trades] == [T0 + 8 * D1]


def test_contract_types_validate():
    with pytest.raises(ValueError):
        SL.Intent(signal_ts=1, side="UP", entry_ref=SL.EntryRef(price=1.0), stop=0.5)
    with pytest.raises(ValueError):
        SL.Intent(signal_ts=1, side=SL.LONG, entry_ref=SL.EntryRef(price=1.0), stop=float("nan"))
    with pytest.raises(ValueError):
        SL.Target("ATR", 1.0)
    with pytest.raises(ValueError):
        SL.Action("OPEN")
    with pytest.raises(ValueError):
        SL.move_stop(-1.0)
    it = SL.Intent(signal_ts=1, side=SL.LONG, entry_ref=SL.EntryRef(price=1.0), stop=0.5, evidence={"a": 1})
    with pytest.raises(TypeError):
        it.evidence["b"] = 2                          # salt-okunur
