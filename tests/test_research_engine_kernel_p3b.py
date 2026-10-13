"""P3b — araştırma çekirdeği birim testleri (docs/ENGINE_BRAIN_V1.md §2.1, §3.3; `research_engine/kernel.py`, `kernel_v2`).

Elle kurulmuş barlar ve betikli bir varyantla: karar bar kapanışında / dolum sonraki açılışta ± kayma; çıkış yolu canlı
defterin `exit_decision`'ı (çağrılan fonksiyonun KENDİSİ; float görünüm Decimal `TickData` ile aynı hükmü verir): açılış
fiyat tiki (boşlukta açılıştan), sonra 1h alt bar OHLC tikleri — stop seviyede, kapanış da ötedeyse kapanıştan
(ihtiyatlı), en kötü uç iki seviyeyi de geçerse likidasyon (ihtiyatlı), aynı tikte stop hedeften önce, TP1 başa-başı
yalnız sonraki tiklerde; 1h alt barlar sinyal barının içindeki sırayı canlı gibi belirler; alt bar eksik/tutarsızsa bar
tek tik + `PATH_1H_GAP`; sinyal dilimi ≤ 1h ise tik barın kendisi; değmeyen barda alt bar açmamak sonucu değiştirmez
(her barı açan koşuyla bayt-aynı); fonlama deponun gerçek uzlaşma satırlarında (nominal dakikaya yuvarlanmış damga, açık
miktar, çıkış barındaki uzlaşmanın ihtiyatlı kuralı — alt barla daralır); `FUNDING_GAP` yerel aralıkla (8h → 4h'e geçen
seri); likidasyon (bracket formülü, izole marj sınırı); sabit 1 USDT risk ve likidasyon mesafesi ≥ 2 × stop; üç maliyet
senaryosunun aynı yol üzerinde aritmetik yeniden maliyetlendirmesi (elle hesap); giriş kapıları (veri boşluğu, maliyet
sonrası R/R dahil); MOVE_STOP doğrulaması; tek giriş; zorlanmış tek niyet yeniden oynatımı; belirlenimcilik. 1d barlı
testlerde alt bar yoktur (bar tek OHLC tiki, işlem `PATH_1H_GAP`). Ağ yok, gerçek veri yok.
"""
from __future__ import annotations

import json
import math

import pytest

from tradingbot import strategy_lib as SL
from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import registry as RG

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


def _markpx(funding):
    """Sentetik markpx 1h (uzlaşma anında başlayan bar, açılışı satırın mark'ı; çekirdeğin mark kaynağı)."""
    from tradingbot.research_engine import synthetic as S
    return S.markpx_from_funding(funding)


def run(bars, var, funding=None, tick=0.0, **kw):
    return KN.run(var, KN.SeriesData(symbol="TST", bars={"1d": bars}, funding=funding, price_tick=tick,
                                     markpx_1h=_markpx(funding)), **kw)


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


def test_one_ohlc_tick_stop_before_target_on_the_same_tick():
    b = flat(6)
    b[3] = bar(3, 100.0, 120.0, 90.0, 110.0)        # aynı tikte stop (95) ve hedef (110): stop önce (defter)
    res = run(b, Scripted(opens={1: {"stop": 95.0, "targets": (SL.Target(SL.TARGET_PRICE, 110.0),)}}))
    (t,) = res.trades
    assert t.exit_kind == KN.EXIT_STOP and t.legs[0].point == "adverse"
    b[3] = bar(3, 100.0, 120.0, 97.0, 90.0)          # ters uç güvenli → lehte uç hedefi alır (kapanış önemsiz)
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0, "targets": (SL.Target(SL.TARGET_PRICE, 110.0),)}})).trades
    assert t.exit_kind == KN.EXIT_TARGET and t.legs[0].point == "favourable" and t.legs[0].ref == 110.0
    assert not t.legs[0].market, "hedef seviyede, kaymasız dolar (defter tiki)"
    assert t.r["base"] == pytest.approx(r_by_hand(100.0, 95.0, [(110.0, 1.0, False)]), abs=1e-12)


def test_tp1_partial_moves_stop_to_breakeven_only_for_later_ticks():
    tg = (SL.Target(SL.TARGET_PRICE, 105.0), SL.Target(SL.TARGET_PRICE, 120.0))
    b = flat(6)
    b[3] = bar(3, 100.0, 106.0, 99.0, 99.5)          # lehte uç TP1 → stop başa-başa; aynı tikin kapanışı onu sınamaz
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0, "targets": tg}})).trades
    be = KN.break_even(True, 100.0 * (1 + SLIP), FEE, SLIP)
    # sonraki barın açılış tiki (100) başa-başın altında: boşluk dolumu açılıştan (defter `GAP_FILL_AT_FIRST_OBSERVATION`)
    assert [(lg.kind, lg.frac, lg.point) for lg in t.legs] == [(KN.EXIT_TARGET, 0.5, "favourable"), (KN.EXIT_STOP, 0.5, "open")]
    assert 100.0 < be and t.legs[1].ref == 100.0 and t.exit_reason == "BREAKEVEN_STOP"
    assert t.legs[1].basis == "GAP_FILL_AT_FIRST_OBSERVATION" and t.exit_ts == T0 + 4 * D1
    assert t.r["base"] == pytest.approx(r_by_hand(100.0, 95.0, [(105.0, 0.5, False), (100.0, 0.5, True)]), abs=1e-12)
    # aynı tikin TERS ucu yeni stopu sınamaz: düşük 99 < başa-baş ama yol "sürer"
    b = flat(3) + [bar(3, 100.0, 106.0, 99.0, 103.0)] + [bar(i, 103.0, 103.1, 102.9, 103.0) for i in range(4, 7)]
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0, "targets": tg}})).trades
    assert t.exit_kind == KN.EXIT_END and t.legs[0].kind == KN.EXIT_TARGET and KN.FLAG_OPEN_AT_END in t.flags


def test_entry_hour_applies_stop_and_liquidation_but_no_target_credit():
    """Mühürlü okunuş `WF_PROTOCOL_V1.kernel.entry_hour` (m-A): girişi içeren 1h saat diliminde yalnız stop/likidasyon;
    hedef kredisi yok (canlıda o saatin 1h barı `apply_closed_bars`'a girmez). Alt barsız 1d giriş barı tek tiktir ve
    giriş saatini içerir → bütün bar hedefsiz; hedef sonraki tikte hâlâ oradaysa alınır; stop giriş saatinde de çalışır."""
    tg = {"stop": 95.0, "targets": (SL.Target(SL.TARGET_PRICE, 110.0),)}
    b = flat(6)
    b[2] = bar(2, 100.0, 111.0, 99.0, 104.0)         # giriş barı (tek tik, giriş saatini içerir): hedef 110 değmiş
    (t,) = run(b, Scripted(opens={1: tg})).trades
    assert t.exit_kind == KN.EXIT_END and not any(lg.kind == KN.EXIT_TARGET for lg in t.legs), "giriş saatinde hedef yok"
    b[3] = bar(3, 104.0, 112.0, 103.0, 108.0)         # sonraki gün hedef yine değer → alınır
    (t,) = run(b, Scripted(opens={1: tg})).trades
    assert (t.exit_kind, t.legs[0].ref, t.exit_ts) == (KN.EXIT_TARGET, 110.0, T0 + 4 * D1)
    b = flat(6)
    b[2] = bar(2, 100.0, 111.0, 94.0, 94.5)           # giriş barında stop: uygulanır (ihtiyatlı kapanış dolumu)
    (t,) = run(b, Scripted(opens={1: tg})).trades
    assert (t.exit_kind, t.legs[0].ref, t.legs[0].basis, t.exit_ts) == (KN.EXIT_STOP, 94.5, KN.BASIS_PRUDENT_CLOSE, T0 + 3 * D1)
    # 1h alt barlarla: giriş saati (00:00) hedefe değer → kredi yok; 01:00 saati yine değer → hedef 01:00'de
    tg2 = {"stop": 95.0, "targets": (SL.Target(SL.TARGET_PRICE, 105.0),)}
    days, hours = hourly_world(6, {2: hours_with(100.0, {0: (100.0, 106.0, 99.9, 104.0), 1: (104.0, 105.5, 103.0, 104.5)})})
    (t,) = run_h(days, hours, Scripted(opens={1: tg2})).trades
    assert (t.exit_kind, t.legs[0].earliest, t.legs[0].ts) == (KN.EXIT_TARGET, T0 + 2 * D1 + H1, T0 + 2 * D1 + 2 * H1)
    days, hours = hourly_world(6, {2: hours_with(100.0, {0: (100.0, 106.0, 99.9, 104.0), 1: (104.0, 104.5, 103.0, 104.5)})})
    (t,) = run_h(days, hours, Scripted(opens={1: tg2})).trades
    assert t.exit_kind == KN.EXIT_END and KN.FLAG_PATH_GAP not in t.flags, "giriş saatinden sonra değmedi → hedef yok"
    days, hours = hourly_world(6, {2: hours_with(100.0, {0: (100.0, 106.0, 94.0, 94.5)})})
    (t,) = run_h(days, hours, Scripted(opens={1: tg2})).trades
    assert (t.exit_kind, t.legs[0].ref, t.legs[0].earliest) == (KN.EXIT_STOP, 94.5, T0 + 2 * D1), "giriş saatinde stop"


def test_entry_hour_on_sub_hour_signal_bars_spans_the_clock_hour():
    """Sinyal dilimi ≤ 1h (Box 5m) iken tikler barın kendisidir; girişi içeren SAAT diliminin bütün tikleri (açılış
    tikleri dahil) hedefsizdir, sonraki saatin ilk tikinden itibaren hedef alınır."""
    M5 = 300_000

    class Five(SL.Variant):
        variant_id, family, signal_tf, timeframes = "TEST5M", "T", "5m", ("5m",)

        def __init__(self):
            self.lookback = {"5m": 5}

        def signals(self, bars, ctx):
            if int(ctx["now_ms"]) != T0 + 3 * M5:
                return []
            return [SL.Intent(signal_ts=T0 + 2 * M5, side=SL.LONG, entry_ref=SL.EntryRef(price=100.0), stop=95.0,
                              targets=(SL.Target(SL.TARGET_PRICE, 101.0),))]

        def manage(self, pos, bars, ctx):
            return SL.NONE
    rows = [{"timestamp": T0 + k * M5, "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1.0}
            for k in range(30)]
    for k in (5, 9, 11):                               # giriş 00:15; 00:25, 00:45, 00:55 hedefe değer — giriş saati
        rows[k] = dict(rows[k], high=101.5)
    rows[12] = dict(rows[12], open=101.2, high=101.3, close=101.0)   # 01:00 açılış tiki hedefin ötesinde → alınır
    (t,) = KN.run(Five(), KN.SeriesData(symbol="TST", bars={"5m": rows})).trades
    assert (t.exit_kind, t.legs[0].ts, t.legs[0].point) == (KN.EXIT_TARGET, T0 + 12 * M5, "open")


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
    assert t.legs[0].basis == KN.BASIS_PRUDENT_CLOSE, "kapanış (99) stopta → kapanıştan (aynı fiyat)"
    assert t.flags == [KN.FLAG_PATH_GAP, KN.FLAG_FUNDING_GAP] and t.move_stop_ignored == {}


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


# ============================================================================ canlı çıkış sözleşmesi (kernel_v2)
H1 = 3_600_000


def day_from_hours(i: int, hrs: list[tuple[float, float, float, float]]) -> tuple[dict, list[dict]]:
    """i. gün ve onun 24 1h alt barı; gün alt barlardan toplanır (tutarlı)."""
    subs = [{"timestamp": T0 + i * D1 + k * H1, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
            for k, (o, h, lo, c) in enumerate(hrs)]
    return bar(i, hrs[0][0], max(x[1] for x in hrs), min(x[2] for x in hrs), hrs[-1][3]), subs


def hourly_world(n: int, special: dict[int, list[tuple]], px: float = 100.0) -> tuple[list[dict], list[dict]]:
    days, hours = [], []
    for i in range(n):
        d, sb = day_from_hours(i, special.get(i) or [(px, px * 1.0005, px * 0.9995, px)] * 24)
        days.append(d)
        hours += sb
    return days, hours


def hours_with(px: float, at: dict[int, tuple]) -> list[tuple]:
    """24 düz saat (px), `at` saatlerinde verilen (o, h, l, c); düz saatler önceki kapanıştan sürer."""
    out, last = [], px
    for k in range(24):
        if k in at:
            out.append(at[k])
            last = at[k][3]
        else:
            out.append((last, last * 1.0005, last * 0.9995, last))
    return out


def run_h(days, hours, var, funding=None):
    return KN.run(var, KN.SeriesData(symbol="TST", bars={"1d": days, "1h": hours}, funding=funding,
                                     markpx_1h=_markpx(funding)))


def test_the_kernel_calls_the_live_exit_decision_and_the_seal_names_the_rule():
    from tradingbot import strategy_paper
    from tradingbot.accounting import futures_ledger as FL
    assert KN.exit_decision is FL.exit_decision, "canlı saf fonksiyonun kendisi (yeniden yazılmadı)"
    assert KN.EXIT_CONTRACT == FL.EXIT_FILL_CONTRACT == "exit_fill_v1"
    assert KN.PATH_TF == strategy_paper.BAR_TIMEFRAME == "1h", "çıkış tikleri canlı defterin bar dilimiyle"
    assert KN.KERNEL_VERSION == "kernel_v2"
    sealed = RG.WF_PROTOCOL_V1["kernel"]["stop_fill"]
    assert sealed["policy"] == KN.STOP_FILL_POLICY and "exit_decision" in sealed["function"]
    assert "2026-10-12" in sealed["reading_tr"] and "+0,02–0,04 R" in sealed["reading_tr"]
    assert "PATH_1H_GAP" in sealed["path_gap_tr"] == sealed["path_gap_tr"] and KN.FLAG_PATH_GAP == "PATH_1H_GAP"
    assert RG.THRESHOLD_DIRECTIONS["WF_PROTOCOL"]["kernel.stop_fill.policy"] == RG.FIXED, "asla gevşetme: kural sabit"
    eh = RG.WF_PROTOCOL_V1["kernel"]["entry_hour"]
    assert eh["policy"] == KN.ENTRY_HOUR_POLICY and "2026-10-12" in eh["reading_tr"] and "m-A" in eh["decision"]
    assert RG.THRESHOLD_DIRECTIONS["WF_PROTOCOL"]["kernel.entry_hour.policy"] == RG.FIXED


def test_the_float_tick_view_gives_the_same_decision_as_a_decimal_tickdata():
    """Çekirdek `exit_decision`'ı float görünümle (`Obs`) çağırır; defter Decimal `TickData` ile. Eşitlik sınırları
    (stop = kapanış, stop = uç, ilk gözlem = seviye) sık çıksın diye değerler 0,5'lik ızgaradan; seviyelerin %20'si
    pozisyonun asla taşımadığı sırada (likidasyon stoptan yakın) — fonksiyonun bütün dalları."""
    import random
    from decimal import Decimal

    from tradingbot.accounting import PositionSide, TickData
    from tradingbot.accounting.futures_ledger import exit_decision
    rnd = random.Random(20261012)
    g = [90.0 + 0.5 * k for k in range(41)]
    seen = set()
    for _ in range(4000):
        long = rnd.random() < 0.5
        lo, h = sorted(rnd.sample(g, 2))
        o, c = rnd.choice([x for x in g if lo <= x <= h]), rnd.choice([x for x in g if lo <= x <= h])
        stop, liq = sorted(rnd.sample(g, 2), reverse=long) if rnd.random() < 0.8 else rnd.sample(g, 2)
        price_only = rnd.random() < 0.2
        obs = KN.Obs(c) if price_only else KN.Obs(c, open=o, high=h, low=lo)
        dd = (lambda x: Decimal(repr(x)))
        td = TickData(last=dd(c), mark=dd(c)) if price_only else TickData(last=dd(c), mark=dd(c), high=dd(h), low=dd(lo),
                                                                          open=dd(o))
        side = PositionSide.LONG if long else PositionSide.SHORT
        a, b = exit_decision(side, stop, liq, obs), exit_decision(side, dd(stop), dd(liq), td)
        assert (a is None) == (b is None)
        if a is not None:
            assert (a["kind"], a["record"]["basis"]) == (b["kind"], b["record"]["basis"])
            seen.add(a["record"]["basis"])
            if a["kind"] == "stop":
                assert dd(a["ref"]) == b["ref"]
    assert seen >= {"FIRST_OBSERVATION_BEYOND_LIQUIDATION", "GAP_FILL_AT_FIRST_OBSERVATION", "INTRABAR_ORDER_UNOBSERVED",
                    "LIQUIDATION_NEARER", KN.BASIS_PRUDENT_CLOSE, "STOP_AT_LEVEL"}, seen


def test_prudent_close_fill_and_prudent_liquidation_on_a_tick():
    """Defterin iki ihtiyatlı dalı çekirdekte: (1) ters uç ve kapanış stopun ötesinde → dolum KAPANIŞTAN (seviyeden
    değil); (2) ters uç hem stopu hem likidasyonu geçer, stop daha yakın → LİKİDASYON (bar içi sıra gözlenmedi)."""
    b = flat(6)
    b[3] = bar(3, 101.0, 101.5, 93.0, 94.0)
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0}})).trades
    assert (t.exit_kind, t.exit_reason, t.legs[0].ref, t.legs[0].point) == (KN.EXIT_STOP, "STOP", 94.0, "close")
    assert t.legs[0].basis == KN.BASIS_PRUDENT_CLOSE and t.legs[0].market
    level = r_by_hand(100.0, 95.0, [(95.0, 1.0, True)])
    assert t.r["base"] == pytest.approx(r_by_hand(100.0, 95.0, [(94.0, 1.0, True)]), abs=1e-12)
    assert t.r["base"] < level - 0.1, "seviyeden dolum (kernel_v1) iyimserdi"
    liq = KN.size_trade("LONG", 100.0 * (1 + SLIP), 99.9)[5]
    b = [bar(i, 100.0, 100.05, 99.95, 100.0) for i in range(6)]
    b[3] = bar(3, 100.0, 100.5, liq * 0.99, 99.95)
    (t,) = run(b, Scripted(opens={1: {"stop": 99.9}})).trades
    assert (t.exit_kind, t.exit_reason, t.legs[0].basis) == (KN.EXIT_LIQ, "LIQUIDATION", "INTRABAR_ORDER_UNOBSERVED")
    assert t.legs[0].ref == t.liq_price and KN.FLAG_PATH_GAP in t.flags


def test_1h_subbars_order_the_signal_bar_like_the_live_ledger():
    """Günün 1h alt barları sırayı belirler (canlı `apply_closed_bars`): hedef 02:00'de, stop 10:00'da → HEDEF; ters sıra
    → STOP. Alt barsız aynı gün tek tiktir: stop ve hedef aynı tikte → STOP (+ `PATH_1H_GAP`)."""
    tg = {"stop": 95.0, "targets": (SL.Target(SL.TARGET_PRICE, 110.0),)}
    up_then_down = hours_with(100.0, {2: (100.0, 111.0, 99.8, 104.0), 10: (104.0, 104.2, 94.0, 96.0)})
    days, hours = hourly_world(6, {3: up_then_down})
    (t,) = run_h(days, hours, Scripted(opens={1: tg})).trades
    assert (t.exit_kind, t.legs[0].ref, t.legs[0].point) == (KN.EXIT_TARGET, 110.0, "favourable")
    assert (t.legs[0].earliest, t.legs[0].ts, t.exit_ts) == (T0 + 3 * D1 + 2 * H1, T0 + 3 * D1 + 3 * H1, T0 + 3 * D1 + 3 * H1)
    assert KN.FLAG_PATH_GAP not in t.flags and t.bars_held == 2
    (t,) = run(days, Scripted(opens={1: tg})).trades                   # 1h verisi yok: gün tek tik
    assert t.exit_kind == KN.EXIT_STOP and t.legs[0].ref == 95.0 and KN.FLAG_PATH_GAP in t.flags
    down_then_up = hours_with(100.0, {2: (100.0, 100.2, 94.0, 96.0), 10: (96.0, 111.0, 95.5, 104.0)})
    days, hours = hourly_world(6, {3: down_then_up})
    (t,) = run_h(days, hours, Scripted(opens={1: tg})).trades
    assert (t.exit_kind, t.legs[0].ref, t.legs[0].basis, t.exit_ts) == (KN.EXIT_STOP, 95.0, "STOP_AT_LEVEL", T0 + 3 * D1 + 3 * H1)


def test_prudent_close_is_the_1h_close_not_the_day_close():
    """Stop 05:00 alt barında delinir ve o saatin kapanışı stopun ötesinde (94,5) → dolum 94,5 (canlı 1h barı). Gün
    sonra toparlanır (kapanış 100): alt barsız tek gün tiki seviyeden (95) dolardı — 1h ayrıntısı daha ihtiyatlı."""
    hrs = hours_with(100.0, {5: (99.0, 99.2, 94.0, 94.5), 6: (94.5, 100.0, 94.4, 100.0)})
    days, hours = hourly_world(6, {3: hrs})
    (t,) = run_h(days, hours, Scripted(opens={1: {"stop": 95.0}})).trades
    assert (t.legs[0].ref, t.legs[0].basis, t.legs[0].point) == (94.5, KN.BASIS_PRUDENT_CLOSE, "close")
    (t0,) = run(days, Scripted(opens={1: {"stop": 95.0}})).trades
    assert (t0.legs[0].ref, t0.legs[0].basis) == (95.0, "STOP_AT_LEVEL") and t.r["base"] < t0.r["base"]


def test_tp1_in_an_early_hour_then_the_breakeven_stop_later_the_same_day():
    tg = {"stop": 95.0, "targets": (SL.Target(SL.TARGET_PRICE, 105.0), SL.Target(SL.TARGET_PRICE, 120.0))}
    hrs = hours_with(100.0, {1: (100.0, 106.0, 99.9, 104.0), 6: (104.0, 104.1, 99.5, 100.5)})
    days, hours = hourly_world(6, {2: hrs})                          # 2. gün giriş günü (açılış 100)
    (t,) = run_h(days, hours, Scripted(opens={1: tg})).trades
    be = KN.break_even(True, 100.0 * (1 + SLIP), FEE, SLIP)
    assert 99.5 < be < 100.5
    assert [(lg.kind, lg.frac, lg.ts) for lg in t.legs] == [(KN.EXIT_TARGET, 0.5, T0 + 2 * D1 + 2 * H1),
                                                           (KN.EXIT_STOP, 0.5, T0 + 2 * D1 + 7 * H1)]
    assert t.legs[1].ref == pytest.approx(be) and t.exit_reason == "BREAKEVEN_STOP" and t.bars_held == 1


def test_skipping_untouched_bars_is_exact(monkeypatch):
    """Çekirdek yalnız stop/likidasyon/sıradaki hedef aralığına değen barda alt barları açar. 1h verisi TAMKEN her barı
    açan koşu (aynı canlı fonksiyon, bütün tikler) bayt-aynı sonuç verir (1d × 24 ve 4h × 4 alt bar, Box 5m)."""
    from tradingbot.research_engine import synthetic as S
    cases = (("K_D4", S.d4_world(22, n=420)), ("K_FM", S.fm_world(21, n=500)), ("K_T2", S.trend_world(23, n=300)),
             ("K_BOX", S.box_world(21, days=2)))
    lazy = {v: json.dumps(KN.run(KN.make_variant(v), d).to_dict(), sort_keys=True) for v, d in cases}
    monkeypatch.setattr(KN, "_touches", lambda *a, **k: True)
    for v, d in cases:
        assert json.dumps(KN.run(KN.make_variant(v), d).to_dict(), sort_keys=True) == lazy[v], v
    assert any(json.loads(x)["trades"] for x in lazy.values())


def test_skipping_with_an_untouched_1h_gap_differs_only_by_the_flag(monkeypatch):
    """m-B: kısaltmanın "bayt-aynı"lığı yalnız 1h verisi tamken geçerlidir. Değmeyen bir barın 1h verisi eksikse işlem,
    bacaklar ve R aynıdır; yalnız her barı açan koşu o bar için `PATH_1H_GAP` koyar (mühürlü kural: işaret yalnız çıkış
    kararı gereken barda)."""
    hrs = hours_with(100.0, {5: (99.0, 99.2, 94.0, 94.5), 6: (94.5, 100.0, 94.4, 100.0)})
    days, hours = hourly_world(6, {3: hrs})
    hole = [x for x in hours if x["timestamp"] != T0 + 2 * D1 + 9 * H1]                   # giriş günü (değmez) eksik saat
    (lazy,) = run_h(days, hole, Scripted(opens={1: {"stop": 95.0}})).trades
    monkeypatch.setattr(KN, "_touches", lambda *a, **k: True)
    (full,) = run_h(days, hole, Scripted(opens={1: {"stop": 95.0}})).trades
    a, b = lazy.to_dict(), full.to_dict()
    assert KN.FLAG_PATH_GAP not in a["flags"] and KN.FLAG_PATH_GAP in b["flags"]
    assert {k: v for k, v in a.items() if k != "flags"} == {k: v for k, v in b.items() if k != "flags"}


def test_missing_or_inconsistent_1h_subbars_fall_back_to_one_tick_and_flag():
    hrs = hours_with(100.0, {5: (99.0, 99.2, 94.0, 94.5), 6: (94.5, 100.0, 94.4, 100.0)})
    days, hours = hourly_world(6, {3: hrs})
    (t_ok,) = run_h(days, hours, Scripted(opens={1: {"stop": 95.0}})).trades
    (t_none,) = run(days, Scripted(opens={1: {"stop": 95.0}})).trades
    assert KN.FLAG_PATH_GAP not in t_ok.flags and KN.FLAG_PATH_GAP in t_none.flags
    hole = [x for x in hours if x["timestamp"] != T0 + 3 * D1 + 9 * H1]                         # eksik saat
    above = [dict(x, high=x["high"] * 1.5) if x["timestamp"] == T0 + 3 * D1 + 9 * H1 else x for x in hours]   # aralık dışı
    for hs in (hole, above, [x for x in hours if x["timestamp"] < T0 + 3 * D1]):
        (t,) = run_h(days, hs, Scripted(opens={1: {"stop": 95.0}})).trades
        assert KN.FLAG_PATH_GAP in t.flags and t.legs[0].ref == t_none.legs[0].ref == 95.0
    # yalnız DEĞEN barın alt barları gerekir: giriş günü (değmez) eksik olsa da işaret yok
    no_entry_day = [x for x in hours if not (T0 + 2 * D1 <= x["timestamp"] < T0 + 3 * D1)]
    (t,) = run_h(days, no_entry_day, Scripted(opens={1: {"stop": 95.0}})).trades
    assert KN.FLAG_PATH_GAP not in t.flags and t.to_dict()["legs"] == t_ok.to_dict()["legs"]
    # alt barsız sentetik dünya (D4: hedef yok → değen bar = bar içi stop/likidasyon çıkışı): işaret tam o işlemlerde
    from tradingbot.research_engine import synthetic as S
    d = S.d4_world(22, n=520, sub=False)
    assert "1h" not in d.bars
    kr = KN.run(KN.make_variant("K_D4"), d)
    intrabar = [t.exit_kind in (KN.EXIT_STOP, KN.EXIT_LIQ) and t.legs[-1].point != "open" for t in kr.trades]
    assert any(intrabar) and [KN.FLAG_PATH_GAP in t.flags for t in kr.trades] == intrabar


def test_signal_bars_at_or_below_1h_are_their_own_ticks():
    class Hourly(SL.Variant):
        variant_id, family, signal_tf, timeframes = "TEST1H", "T", "1h", ("1h",)

        def __init__(self):
            self.lookback = {"1h": 5}

        def signals(self, bars, ctx):
            if int(ctx["now_ms"]) != T0 + 2 * H1:
                return []
            return [SL.Intent(signal_ts=T0 + H1, side=SL.LONG, entry_ref=SL.EntryRef(price=100.0), stop=95.0)]

        def manage(self, pos, bars, ctx):
            return SL.NONE
    rows = [{"timestamp": T0 + k * H1, "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1.0}
            for k in range(8)]
    rows[4] = dict(rows[4], low=93.0, close=94.0)
    (t,) = KN.run(Hourly(), KN.SeriesData(symbol="TST", bars={"1h": rows})).trades
    assert (t.legs[0].ref, t.legs[0].basis, t.exit_ts) == (94.0, KN.BASIS_PRUDENT_CLOSE, T0 + 5 * H1)
    assert KN.FLAG_PATH_GAP not in t.flags


def test_funding_with_1h_subbars_dates_the_exit_to_its_hour():
    """Alt barla çıkışın en erken anı saatine daralır: 10:00 alt barında stop → 08:00 uzlaşması (çıkıştan önce) tam
    miktara yazılır, 16:00 yazılmaz ve belirsiz değildir (`FUNDING_EXIT_BAR` yok). Alt barsız aynı yol: 08:00 ve 16:00
    belirsiz (bar içi), yazılmaz."""
    hrs = hours_with(100.0, {10: (100.0, 100.2, 94.0, 96.0)})
    days, hours = hourly_world(8, {3: hrs})
    h8 = 8 * H1
    rows = [(T0 + k * h8, 0.001, 100.0) for k in range(0, 40)]
    (t,) = run_h(days, hours, Scripted(opens={1: {"stop": 95.0}}), funding=rows).trades
    assert (t.legs[0].earliest, t.legs[0].ts) == (T0 + 3 * D1 + 10 * H1, T0 + 3 * D1 + 11 * H1)
    charged = [ts for ts, _r, _m in rows if T0 + 2 * D1 < ts <= T0 + 3 * D1 + 10 * H1]
    assert t.funding_events == len(charged) == 4 and t.funding_ambiguous_events == 0     # 2. gün 08/16, 3. gün 00/08
    assert KN.FLAG_FUNDING_EXIT_BAR not in t.flags and KN.FLAG_FUNDING_GAP not in t.flags
    (t0,) = run(days, Scripted(opens={1: {"stop": 95.0}}), funding=rows).trades
    assert t0.funding_events == 3 and t0.funding_ambiguous_events == 2 and KN.FLAG_FUNDING_EXIT_BAR in t0.flags


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
    fr = [(T0 + k * 8 * 3_600_000, 0.0003, 100.0) for k in range(60)]
    data = KN.SeriesData(symbol="TST", bars={"1d": b}, funding=fr, markpx_1h=_markpx(fr))
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
    (t,) = KN.run(S4(), KN.SeriesData(symbol="TST", bars={"4h": b4}, funding=rows4, markpx_1h=_markpx(rows4))).trades
    assert (t.entry_ts, t.legs[0].ts) == (T0 + 2 * H4, T0 + 6 * H4)
    assert t.funding_events == 1 and t.funding_ambiguous_events == 0, \
        "08:00 (giriş anı) yazılmaz, 16:00 yazılır, 00:00 (çıkış barının kapanışı) yazılmaz ve belirsiz değil"


def test_funding_after_a_partial_exit_uses_the_remaining_quantity():
    tg = (SL.Target(SL.TARGET_PRICE, 105.0), SL.Target(SL.TARGET_PRICE, 130.0))
    b = flat(3) + [bar(3, 100.0, 106.0, 99.5, 104.0)] + [bar(i, 104.0, 104.1, 103.9, 104.0) for i in range(4, 9)]
    h12 = 12 * 3_600_000
    paid = {T0 + 3 * D1 + h12, T0 + 4 * D1, T0 + 4 * D1 + h12}
    rows = [(T0 + k * h12, (0.001 if T0 + k * h12 in paid else 0.0), 100.0) for k in range(0, 30)]
    (t,) = run(b, Scripted(opens={1: {"stop": 95.0, "targets": tg}}), funding=rows).trades
    assert t.legs[0].ts == T0 + 4 * D1 and t.legs[0].earliest == T0 + 3 * D1 and t.exit_kind == KN.EXIT_END
    # TP1 girişten sonraki barın İÇİNDE (lehte uç) doldu: barın içindeki uzlaşma yalnız kalan yarıya (TP1 yarısı
    # belirsiz → yazılmaz, işaretli); barın kapanışındaki ve sonrakiler kalan yarıyla
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


def test_funding_gap_blind_spot_single_missing_settlement_at_an_interval_switch():
    """m-D (belge §8.4 P3b-2a): yerel aralık ölçüsünün BİLİNEN kör noktası — aralık değişiminin (8h → 4h ya da 4h → 8h)
    hemen yanındaki TEK eksik uzlaşma, oluşan aralık büyük komşunun 1,5 katını aşmadığı için `FUNDING_GAP` sayılmaz
    (8h → 4h'te ilk 4h uzlaşmasının yokluğu, değişimin bir uzlaşma sonra olmasından ayırt edilemez). Kararlı seride
    aynı tek eksik yakalanır. Davranış bilerek sabitlenir: düzeltilirse bu test ve belge birlikte güncellenir."""
    h = 3_600_000

    def fs(ints):
        t, rows = 0, [(0, 0.0001, 100.0)]
        for x in ints:
            t += x * h
            rows.append((t, 0.0001, 100.0))
        return KN.FundingSeries(rows)
    for ints in ([8] * 10 + [8] + [4] * 9, [8] * 9 + [12] + [4] * 10, [4] * 10 + [12] + [8] * 9, [4] * 9 + [8] * 11):
        assert not any(fs(ints).gap_after), ints
    assert fs([8] * 10 + [16] + [8] * 10).gap_after[10] and fs([4] * 10 + [8] + [4] * 10).gap_after[10]


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
