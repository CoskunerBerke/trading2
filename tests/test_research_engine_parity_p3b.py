"""P3b — parite (a) ve (b), altın sentetik yollar, belirlenimcilik (docs/ENGINE_BRAIN_V1.md §2.1; ana belge §6.2).

* Altın yollar: her K ailesi için küçük belirlenimci sentetik dünyada (4h/1d'de tutarlı 1h alt barlarla) çekirdeğin
  (`kernel_v2`) işlem listesi (sinyal barı, yön, çıkış türü, üç senaryonun R'si) sabitlenir. Kural/çekirdek değişirse
  test kırılır ve değer bilerek yeniden sabitlenir.
* Parite (a): aynı dünyada çekirdek ↔ canlı kural + `apply_action` + `FuturesLedgerV2.tick`, canlı defterin tikleriyle
  (`live_1h`: açılış fiyat tiki + sinyal barının bütün 1h OHLC barları, kırpma yok; başvuru alt barları çekirdekten
  bağımsız okur): sinyal kimliği %100, medyan |ΔR| ≤ 0,02, p95 ≤ 0,05 (mühürlü eşikler). Sinyal barı başına tek OHLC
  tiki (`ohlc`) raporlanır (kapıya girmez). Testteki dünyalar küçüktür (süre); ≥ 200 işlem/aile ölçümü
  `scripts/engine_p3b_measure.py` ile yapılır (belge §8.4 notları) ve `TB_P3B_FULL_PARITY=1` ile burada da koşar.
* Betikli defter çapraz denetimi (canlı tikler): TP1 + başa-baş, boşluk, likidasyon (açılış likidasyon fiyatının hemen
  altında/üstünde: bracket formülü iki tarafta da aynı fiyatı verir), fonlama, defterin ihtiyatlı dalları
  (`STOP_CLOSE_BEYOND_LEVEL_PRUDENT`, `PRUDENT_LIQUIDATION`) ve 1h alt barlı gün: çekirdek R'si ve dayanağı defterin
  `r_multiple`'ına / `exit_fill.basis`'ine eşit; kırpılmamış likidasyon sonucu (bracket 2, bakım tutarı ≠ 0) defterin
  `liquidation_outcome`'ına eşit.
* Parite (b): dönem görünümünden kurulan kopya, karar barında `signals` + tek niyetin zorlanmış yeniden oynatımı; POLICY
  kohortu, ≥ 20 giriş kapısı, ≥ %95 aynı bar/yön, medyan |ΔR| ≤ 0,05; anahtarsız kayıt EŞLEŞMEDİ; C4 varyasyon süzgeci;
  eşlenemeyen görünüm NOT_COMPARABLE; `PARITY_FAIL` / `PARITY_UNPROVEN` ve lider satırına yansıması.
* Belirlenimcilik: A→B ile B→A sırası (yapı önbelleği temizlenerek ve ayrı süreçte) aynı baytları verir.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tradingbot import strategy_lib as SL
from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import parity as PA
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import synthetic as S

ROOT = Path(__file__).resolve().parents[1]

#: (varyant, dünya, kwargs, tohum)
GOLDEN_CASES = {
    "K_T2": ("T2", {"n": 560}, 22), "K_M2": ("M2", {"n": 560}, 21), "K_D4": ("D4", {"n": 520}, 22),
    "K_BOX": ("BOX", {"days": 5}, 21), "K_C4_CV003": ("C4", {"n": 800}, 21), "K_FM": ("FM", {"n": 700}, 21),
}
_CACHE: dict[str, tuple] = {}


def _world(vid: str) -> KN.SeriesData:
    fam, kw, seed = GOLDEN_CASES[vid]
    return S.WORLDS[fam](seed, **kw)


def _runs(vid: str) -> tuple[KN.KernelResult, list, KN.SeriesData]:
    if vid not in _CACHE:
        data = _world(vid)
        kr = KN.run(KN.make_variant(vid), data)
        refs, _rej = PA.reference_run(vid, data)
        _CACHE[vid] = (kr, refs, data)
    return _CACHE[vid]


def _summary(kr: KN.KernelResult) -> list[tuple]:
    return [(t.signal_ts, t.side, t.exit_kind, round(t.r["base"], 6), round(t.r["ADVERSE"], 6), round(t.r["STRESS"], 6))
            for t in kr.trades]


# ============================================================================ altın yollar (çekirdek)
#: `kernel_v2` (2026-10-12; canlı `exit_decision` + 1h alt barlar). `kernel_v1`'e göre değişen satırlar yalnız stop
#: çıkışlarıdır ve hepsi AŞAĞI (ihtiyatlı dolum): Box 5, Formasyon 2 işlem.
GOLDEN: dict[str, list[tuple]] = {
    "K_BOX": [
        (1578009600000, "LONG", "STOP", -1.188618, -1.223598, -1.307818),
        (1578013800000, "LONG", "STOP", -1.220193, -1.259175, -1.354542),
        (1578015600000, "LONG", "STOP", -1.1767, -1.210047, -1.289859),
        (1578017700000, "LONG", "STOP", -1.196099, -1.23209, -1.319016),
        (1578019500000, "LONG", "STOP", -1.1211, -1.145796, -1.203524),
        (1578024600000, "LONG", "STOP", -1.174093, -1.207103, -1.285931),
        (1578031200000, "LONG", "STOP", -1.129581, -1.154924, -1.21437),
        (1578038400000, "LONG", "STOP", -1.138307, -1.165905, -1.230738),
        (1578042300000, "LONG", "STOP", -1.159019, -1.189855, -1.262913),
        (1578044700000, "LONG", "STOP", -1.291814, -1.32598, -1.415068),
        (1578052200000, "LONG", "STOP", -1.09489, -1.117023, -1.168014),
        (1578078600000, "LONG", "STOP", -1.197759, -1.234054, -1.321563),
        (1578079500000, "LONG", "STOP", -1.142818, -1.171159, -1.237812),
        (1578085200000, "LONG", "STOP", -1.465609, -1.491338, -1.573137),
        (1578090900000, "LONG", "STOP", -1.154165, -1.184291, -1.255449),
        (1578096300000, "LONG", "STOP", -1.191496, -1.226326, -1.310108),
        (1578099300000, "LONG", "TARGET", 4.99656, 4.789363, 4.561497),
        (1578194400000, "SHORT", "STOP", -1.205683, -1.243213, -1.333789),
        (1578203700000, "SHORT", "STOP", -1.203704, -1.240984, -1.330861),
        (1578206400000, "SHORT", "TARGET", 6.116735, 5.754729, 5.380861),
        (1578243600000, "LONG", "STOP", -1.752648, -1.761367, -1.814595),
        (1578248400000, "LONG", "STOP", -1.107388, -1.129764, -1.181635),
        (1578253500000, "LONG", "END", 1.502812, 1.406315, 1.284262),
    ],
    "K_C4_CV003": [
        (1585540800000, "LONG", "STOP", -1.025678, -1.031406, -1.044807),
        (1586635200000, "LONG", "TARGET", 1.957862, 1.941249, 1.917486),
        (1588190400000, "LONG", "STOP", -1.026251, -1.032068, -1.045763),
        (1588420800000, "LONG", "TARGET", 1.909308, 1.883749, 1.847222),
    ],
    "K_D4": [
        (1579104000000, "LONG", "RULE", 1.022846, 1.00299, 0.974872),
        (1580328000000, "LONG", "RULE", 0.28839, 0.274203, 0.252196),
        (1582603200000, "LONG", "RULE", 1.022835, 1.003579, 0.976395),
        (1583769600000, "LONG", "RULE", -0.09332, -0.103086, -0.119326),
        (1584014400000, "LONG", "RULE", 0.841391, 0.827158, 0.806618),
        (1584720000000, "LONG", "STOP", -1.034933, -1.040347, -1.053195),
    ],
    "K_FM": [
        (1579420800000, "LONG", "TARGET", 1.961397, 1.946774, 1.926747),
        (1579852800000, "LONG", "TARGET", 1.970535, 1.95913, 1.943265),
        (1579982400000, "LONG", "STOP", -1.028029, -1.03087, -1.037522),
        (1580400000000, "LONG", "TARGET", 1.948057, 1.93128, 1.907862),
        (1580486400000, "LONG", "TIME", -0.04626, -0.05536, -0.070078),
        (1581004800000, "LONG", "TIME", 1.865439, 1.851765, 1.833787),
        (1581523200000, "LONG", "TARGET", 1.975468, 1.964149, 1.947926),
        (1581652800000, "LONG", "TARGET", 1.945941, 1.926148, 1.898015),
        (1581724800000, "LONG", "TIME", 1.069707, 1.060225, 1.046833),
        (1583337600000, "LONG", "STOP", -1.051227, -1.056, -1.06737),
        (1584072000000, "LONG", "TARGET", 1.976546, 1.965933, 1.950721),
        (1584432000000, "LONG", "TARGET", 1.986821, 1.977072, 1.962965),
        (1584547200000, "LONG", "TIME", -0.167399, -0.172837, -0.182059),
        (1585080000000, "LONG", "TARGET", 1.963063, 1.952581, 1.937324),
        (1585209600000, "LONG", "STOP", -1.098783, -1.101021, -1.106746),
        (1587067200000, "LONG", "TARGET", 1.980356, 1.971062, 1.957509),
        (1587225600000, "LONG", "TIME", -0.074863, -0.081421, -0.092347),
    ],
    "K_M2": [
        (1596153600000, "LONG", "RULE", 0.000984, -0.002759, -0.008925),
        (1603411200000, "LONG", "RULE", -0.411747, -0.414563, -0.419673),
        (1604016000000, "LONG", "RULE", 0.114687, 0.11075, 0.104438),
        (1604361600000, "LONG", "RULE", 0.53298, 0.528006, 0.520513),
        (1606608000000, "LONG", "RULE", 0.02472, 0.021048, 0.015053),
        (1607817600000, "LONG", "RULE", -0.141919, -0.145841, -0.152474),
    ],
    "K_T2": [
        (1608595200000, "LONG", "RULE", -0.402943, -0.405712, -0.410768),
        (1611792000000, "LONG", "RULE", -0.300118, -0.302852, -0.307697),
        (1612915200000, "LONG", "RULE", -0.60779, -0.610439, -0.615627),
        (1613952000000, "LONG", "RULE", -0.32971, -0.332946, -0.338723),
        (1620604800000, "LONG", "RULE", -0.170095, -0.173572, -0.179524),
        (1621900800000, "LONG", "RULE", -0.155126, -0.158587, -0.164494),
        (1625270400000, "LONG", "RULE", -0.584335, -0.586808, -0.59161),
    ],
}


@pytest.mark.parametrize("vid", sorted(GOLDEN_CASES))
def test_golden_synthetic_path_per_k_family(vid):
    kr, _refs, _data = _runs(vid)
    got = _summary(kr)
    assert got, f"{vid}: altın dünya işlem üretmeli"
    want = GOLDEN[vid]
    assert [g[:3] for g in got] == [w[:3] for w in want]
    for g, w in zip(got, want):
        assert g[3:] == pytest.approx(w[3:], abs=1e-6)


def test_kernel_run_is_deterministic_byte_for_byte():
    data = _world("K_C4_CV003")
    a = json.dumps(KN.run(KN.make_variant("K_C4_CV003"), data).to_dict(), sort_keys=True)
    b = json.dumps(KN.run(KN.make_variant("K_C4_CV003"), _world("K_C4_CV003")).to_dict(), sort_keys=True)
    assert a == b
    assert json.dumps(_world("K_FM").bars["4h"][:50]) == json.dumps(S.WORLDS["FM"](21, n=700).bars["4h"][:50])


# ============================================================================ parite (a)
@pytest.mark.parametrize("vid", sorted(GOLDEN_CASES))
def test_parity_a_kernel_vs_live_ledger_on_the_fixture_world(vid):
    kr, refs, data = _runs(vid)
    a = PA.compare_a([(vid, kr, refs)], min_trades=1)
    th = RG.LIB_V1["thresholds"]["parity_kernel"]
    assert a.n_matched >= 1
    assert a.identity_share == 1.0 >= th["min_signal_identity_share"], (a.only_kernel, a.only_ref)
    assert a.median_abs_dr <= th["max_median_abs_dr"] and a.p95_abs_dr <= th["max_p95_abs_dr"], a.to_dict()
    assert a.passed
    # sinyal barı başına tek OHLC tiki (rapor; kapıya girmez): aynı sinyaller, ΔR raporlanır
    ohlc, _rej = PA.reference_run(vid, data, mode=PA.MODE_OHLC)
    b = PA.compare_a([(vid, kr, ohlc)], min_trades=1)
    assert b.n_matched >= 1 and math.isfinite(b.mean_signed_dr), b.to_dict()


def test_parity_a_thresholds_come_from_the_seal_and_fail_closed():
    th = RG.LIB_V1["thresholds"]["parity_kernel"]
    assert (th["min_synthetic_trades_per_family"], th["min_signal_identity_share"], th["max_median_abs_dr"],
            th["max_p95_abs_dr"]) == (200, 1.0, 0.02, 0.05)
    kr, refs, _data = _runs("K_FM")
    assert not PA.compare_a([("K_FM", kr, refs)]).passed or len(kr.trades) >= 200, "≥ 200 işlem şartı varsayılandır"
    assert not PA.compare_a([("K_FM", kr, refs[1:])], min_trades=1).passed, "eksik sinyal kimliği geçemez"


@pytest.mark.skipif(os.environ.get("TB_P3B_FULL_PARITY") != "1", reason="tam ölçüm (≥ 200 işlem/aile) uzun sürer")
def test_parity_a_full_200_trades_per_family():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("p3b_measure", Path(__file__).resolve().parents[1] / "scripts" /
                                                  "engine_p3b_measure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for fam in ("T2", "M2", "BOX", "D4", "C4", "FM"):
        res = mod.measure(fam, min_trades=200, max_worlds=60)
        assert res["parity_a"]["passed"] and res["parity_a"]["n_matched"] >= 200, res


# ============================================================================ betikli defter çapraz denetimi
D1 = 86_400_000
T0 = S.T0


def _bar(i, o, h, lo, c):
    return {"timestamp": T0 + i * D1, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


class _Once(SL.Variant):
    variant_id, family, signal_tf, timeframes = "TEST", "T", "1d", ("1d",)

    def __init__(self, **intent):
        self.intent, self.lookback = intent, {"1d": 3}

    def signals(self, bars, ctx):
        if (int(ctx["now_ms"]) - T0) // D1 - 1 != 1:
            return []
        return [SL.Intent(signal_ts=T0 + D1, entry_ref=SL.EntryRef(price=100.0), **self.intent)]

    def manage(self, pos, bars, ctx):
        return SL.NONE


def _ledger_r(bars, side, stop, targets, funding, *, hours=None, mode=PA.MODE_LIVE):
    """Defter yolu, canlı tiklerle: 2. barın açılışında `apply_action` OPEN (çekirdeğin kaldıracıyla); sonra her barın
    açılışında fiyat tiki (giriş barı hariç) ve barın 1h OHLC barları (`hours` yoksa ya da `ohlc` kipinde bar tek OHLC
    tiki), tikler gözlem barının açılışından 1 ms sonra; veri sonunda son kapanışta fiyat tiki + elle kapanış. Dönen: (R,
    kayıt, açılıştaki likidasyon fiyatı)."""
    from tradingbot.accounting import TickData
    from tradingbot.core import iso
    from tradingbot.risk import build_state
    from tradingbot.strategy_paper import DataVerdict, apply_action
    data = KN.SeriesData(symbol="TST", bars={"1d": bars}, funding=funding, markpx_1h=S.markpx_from_funding(funding))
    led, filt, prof, risk, lookup = PA._ref_ledger(data)
    o = bars[2]["open"]
    ok, _w, lev, _n, _m, _l = KN.size_trade(side, o * (1 + (1 if side == "LONG" else -1) * 0.0003), stop)
    at = datetime.fromtimestamp((T0 + 2 * D1) / 1000, tz=timezone.utc)
    st = build_state(equity=PA.REF_EQUITY, starting_equity=PA.REF_EQUITY, available=PA.REF_EQUITY, used_margin=0.0,
                     positions=[], history=[], now=at)
    act = {"action": "OPEN", "direction": side, "stop": stop, "targets": list(targets), "leverage": lev, "leverage_max": 0,
           "name": "T"}
    res = apply_action(act, symbol="TST", price=o, tick=None, now=at, ledger=led, risk=risk, profile=prof, state=st,
                       filters=filt, run_id="t", reject=lambda s, r: None, on_closed=lambda r: None,
                       data=DataVerdict(ok=True, entry_ok=True, bars={"1d": T0 + D1}))
    assert res == "OPENED"
    liq0 = float(led.positions["TST"].liquidation_price)

    def dec(x):
        return Decimal(repr(float(x)))
    for i in range(2, len(bars)):
        b = bars[i]
        if led.positions.get("TST") is None:
            break
        odt = datetime.fromtimestamp(b["timestamp"] / 1000, tz=timezone.utc)
        if i > 2:
            led.tick({"TST": TickData(last=dec(b["open"]), mark=dec(b["open"]), ts=iso(odt))}, now_utc=odt,
                     funding_rate_lookup=lookup)
        subs = [x for x in (hours or []) if b["timestamp"] <= x["timestamp"] < b["timestamp"] + D1] \
            if mode == PA.MODE_LIVE else []
        for x in subs or [b]:
            if led.positions.get("TST") is None:
                break
            xdt = datetime.fromtimestamp((x["timestamp"] + PA.INTRABAR_TICK_MS) / 1000, tz=timezone.utc)
            td = TickData(last=dec(x["close"]), mark=dec(x["close"]), high=dec(x["high"]), low=dec(x["low"]),
                          open=dec(x["open"]), ts=iso(xdt))
            led.tick({"TST": td}, now_utc=xdt, funding_rate_lookup=lookup)
    if led.positions.get("TST") is not None:
        end = datetime.fromtimestamp((bars[-1]["timestamp"] + D1) / 1000, tz=timezone.utc)
        led.tick({"TST": TickData(last=dec(bars[-1]["close"]), mark=dec(bars[-1]["close"]), ts=iso(end))}, now_utc=end,
                 funding_rate_lookup=lookup)
        led.close_manual("TST", dec(bars[-1]["close"]), now=end)
    (rec,) = led.history
    return float(rec.r_multiple), rec, liq0


def _flat(n, px=100.0):
    return [_bar(i, px, px * 1.0005, px * 0.9995, px) for i in range(n)]


_LIQ = KN.size_trade("LONG", 100.0 * (1 + 0.0003), 99.9)[5]          # stop 99,9 → kaldıraç 4, likidasyon ≈ 75,3
CROSS = {
    "liq_open_just_below": ("LONG", 99.9, (), [_bar(3, _LIQ * 0.9999, _LIQ * 1.01, _LIQ * 0.99, _LIQ)]),
    "liq_slippage_beyond": ("LONG", 99.9, (), [_bar(3, _LIQ * 1.0001, _LIQ * 1.01, _LIQ * 1.00005, _LIQ * 1.001)]),
    "liq_open_just_above": ("LONG", 99.9, (), [_bar(3, _LIQ * 1.001, _LIQ * 1.01, _LIQ * 1.0008, _LIQ * 1.002)]),
    "stop_at_level": ("LONG", 95.0, (), [_bar(3, 101.0, 101.5, 94.0, 95.5)]),
    "gap_stop": ("LONG", 95.0, (), [_bar(3, 93.0, 94.0, 92.0, 93.5)]),
    "target": ("LONG", 95.0, (110.0,), [_bar(3, 101.0, 111.0, 100.5, 110.5)]),
    "tp1_breakeven": ("LONG", 95.0, (105.0, 120.0), [_bar(3, 101.0, 106.0, 100.5, 99.0)]),
    "short_stop": ("SHORT", 105.0, (), [_bar(3, 99.0, 106.0, 98.5, 104.0)]),
    "liquidation_gap": ("LONG", 99.9, (), [_bar(3, 70.0, 71.0, 69.0, 70.5)]),
    "end_of_data": ("LONG", 90.0, (), []),
}


@pytest.mark.parametrize("case", sorted(CROSS))
def test_scripted_paths_match_the_ledger_r_multiple(case):
    side, stop, targets, tail = CROSS[case]
    bars = _flat(3) + tail
    bars += [_bar(i, b["open"], b["high"], b["low"], b["close"]) for i, b in enumerate(_flat(8, bars[-1]["close"]))][len(bars):]
    funding = [(T0 + k * 8 * 3_600_000, 0.0002 * (1 + k % 3), 100.0 + k % 5) for k in range(0, 40)]
    (t,) = KN.run(_Once(side=side, stop=stop, targets=tuple(SL.Target(SL.TARGET_PRICE, x) for x in targets)),
                  KN.SeriesData(symbol="TST", bars={"1d": bars}, funding=funding,
                                markpx_1h=S.markpx_from_funding(funding))).trades
    r_led, rec, liq0 = _ledger_r(bars, side, stop, targets, funding)
    assert t.liq_price == pytest.approx(liq0, rel=1e-6), "bracket likidasyon formülü iki tarafta aynı fiyatı verir"
    assert t.r["base"] == pytest.approx(r_led, abs=1e-9), (case, t.exit_kind, rec.exit_reason)
    risk_k = abs(t.entry_ref * (1 + (1 if side == "LONG" else -1) * 0.0003) - stop) * t.qty
    risk_l = float(rec.features["risk_usdt"])
    assert t.funding_usdt / risk_k == pytest.approx(float(rec.funding) / risk_l, abs=1e-9), "fonlama R cinsinden aynı"
    if case in ("liquidation_gap", "liq_open_just_below", "liq_slippage_beyond"):
        assert t.exit_kind == KN.EXIT_LIQ and rec.exit_reason == "likidasyon"
    if case == "liq_slippage_beyond":
        assert t.exit_reason == "SLIPPAGE_BEYOND_LIQUIDATION" == rec.features["exit_fill"]["basis"]
    if case == "liq_open_just_above":
        assert t.exit_kind == KN.EXIT_STOP and t.exit_reason == "GAP_STOP" and rec.exit_reason != "likidasyon"
        assert t.r["base"] < -100.0, "açılış boşluğu stopu: kayıp likidasyonun hemen berisinde"
    if case == "tp1_breakeven":
        assert [lg.kind for lg in t.legs] == [KN.EXIT_TARGET, KN.EXIT_STOP] and rec.tp1_done


def test_unclamped_liquidation_outcome_equals_the_ledger_arithmetic():
    """Bracket 2'de (mmr ≥ likidasyon ücreti, bakım tutarı ≠ 0) likidasyon kaybı izole marjdan KÜÇÜKTÜR (kırpılmaz): sonuç
    likidasyon fiyatına bağlıdır. Defterin açtığı pozisyonun likidasyon fiyatı bracket formülüne, çekirdeğin likidasyon
    bacağı aritmetiği (`cost_trade`) defterin `liquidation_outcome` sonucuna eşit. (Çekirdeğin kendi büyüklüğünde —
    1 USDT risk, ≤ 200 × 125 notional — bracket hep 1'dir ve likidasyon hep kırpılır; bu yüzden ayrı kurulur.)"""
    from tradingbot.accounting import (AmountType, FeeSchedule, FuturesLedgerV2, LiquidationParams, MarketType, SizeSpec,
                                       SlippageModel, SymbolFilters, TickData, bracket_for, liquidation_price)
    from tradingbot.core import iso
    led = FuturesLedgerV2(1_000_000, max_positions=None, enforce_position_cap=False,
                          fees=FeeSchedule(maker_pct=Decimal("0.02"), taker_pct=Decimal("0.05")),
                          slippage=SlippageModel(fixed_bps=Decimal("3")), liq_params=LiquidationParams())
    filt = SymbolFilters(symbol="TST", market_type=MarketType.USDM_PERP, price_tick=Decimal("1e-8"), qty_step=Decimal("1e-6"),
                         min_qty=Decimal("0"), max_qty=Decimal("1e15"), market_max_qty=Decimal("1e15"),
                         min_notional=Decimal("0"), max_leverage=125, source="test")
    at = datetime(2020, 1, 3, tzinfo=timezone.utc)
    pos = led.open("TST", "LONG", Decimal("100"), SizeSpec(Decimal("100000"), AmountType.NOTIONAL, 10), stop=Decimal("99"),
                   filters=filt, now=at)
    assert pos is not None, led.last_reject_reason
    b = bracket_for(pos.qty * pos.entry_avg)
    assert b.maint_amount > 0 and b.mmr * 100 >= LiquidationParams().liq_fee_pct, "bracket 2: kırpılmayan bölge"
    assert pos.liquidation_price == liquidation_price("LONG", pos.entry_avg, pos.qty, pos.isolated_margin, b.mmr, b.maint_amount)
    lp = float(pos.liquidation_price)
    qty, margin, entry = float(pos.qty), float(pos.isolated_margin), float(pos.entry_avg)
    at2 = datetime(2020, 1, 4, tzinfo=timezone.utc)
    led.tick({"TST": TickData(last=Decimal(repr(lp * 0.99)), mark=Decimal(repr(lp * 0.99)), ts=iso(at2))}, now_utc=at2)
    (rec,) = led.history
    assert rec.exit_reason == "likidasyon" and pos.meta.get("liq_clamped") is False
    t = KN.KernelTrade(symbol="TST", variant_id="T", side="LONG", signal_ts=0, entry_ts=0, entry_ref=entry / 1.0003,
                       initial_stop=99.0, targets=(), leverage=10, qty=qty, notional=qty * entry, margin=margin,
                       liq_price=lp, risk_usdt=0.0)
    t.legs.append(KN.Leg(KN.EXIT_LIQ, lp, 1.0, False, 0, "open", "LIQUIDATION"))
    r, net = KN.cost_trade(t, KN.scenario("base"), 0.0)
    assert -net < margin + entry * qty * 0.0005 - 1.0, "kırpılmadı: kayıp izole marj + giriş ücretinden küçük"
    assert net == pytest.approx(float(rec.pnl), rel=1e-9) and r == pytest.approx(float(rec.r_multiple), rel=1e-9)


def test_the_kernel_takes_the_ledger_prudent_branches():
    """Canlı tikte: (1) ters uç ve kapanış stopun ötesinde → dolum KAPANIŞTAN (`STOP_CLOSE_BEYOND_LEVEL_PRUDENT`); (2) ters
    uç hem stopu hem likidasyonu geçer, stop daha yakın → LİKİDASYON (`PRUDENT_LIQUIDATION`). Çekirdek (`kernel_v2`) iki
    durumda da defterle aynı dalı, aynı R'yi verir; `kernel_v1`'in seviyeden stop dolumu bu yollarda > 0,1 R iyimserdi."""
    funding = [(T0 + k * 8 * 3_600_000, 0.0002, 100.0) for k in range(0, 40)]
    cases = (("LONG", 95.0, [_bar(3, 101.0, 101.5, 93.0, 94.0)], "STOP_CLOSE_BEYOND_LEVEL_PRUDENT", KN.EXIT_STOP),
             ("LONG", 99.9, [_bar(3, 100.0, 100.5, _LIQ * 0.99, 99.95)], "INTRABAR_ORDER_UNOBSERVED", KN.EXIT_LIQ))
    for side, stop, tail, basis, kind in cases:
        bars = _flat(3) + tail
        bars += [_bar(i, b["open"], b["high"], b["low"], b["close"]) for i, b in enumerate(_flat(8, bars[-1]["close"]))][len(bars):]
        (t,) = KN.run(_Once(side=side, stop=stop, targets=()), KN.SeriesData(symbol="TST", bars={"1d": bars}, funding=funding,
                                                                                  markpx_1h=S.markpx_from_funding(funding))).trades
        r_led, rec, _l = _ledger_r(bars, side, stop, (), funding)
        assert rec.features["exit_fill"]["basis"] == basis == t.legs[0].basis and t.exit_kind == kind
        assert t.r["base"] == pytest.approx(r_led, abs=1e-9), (basis, t.r["base"], r_led)
        e = 100.0 * 1.0003
        level_r = ((stop * 0.9997 - e) - 0.0005 * (e + stop * 0.9997)) / (e - stop)       # kernel_v1: seviyeden, fonlamasız
        assert level_r - (t.r["base"] - t.funding_usdt / t.risk_usdt) > 0.1, (basis, level_r, t.r["base"])


def test_one_day_with_1h_bars_matches_the_ledger_ticking_every_hour():
    """1d sinyal barı + 24 tutarlı 1h barı: çekirdek (yalnız değen günde alt barları açar) ↔ defter (her saati tikler):
    hedef önce gelen gün HEDEF, stop 1h barında delinip o saatin kapanışı ötedeyse dolum o kapanıştan; R ve dayanak eşit."""
    H1 = 3_600_000

    def hours_for(days, special):
        out = []
        for d in days:
            i = (d["timestamp"] - T0) // D1
            hrs = special.get(i) or [(d["open"], d["high"], d["low"], d["close"])] + [(d["close"],) * 4] * 23
            out += [{"timestamp": d["timestamp"] + k * H1, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
                    for k, (o, h, lo, c) in enumerate(hrs)]
        return out
    funding = [(T0 + k * 8 * H1, 0.0002 * (1 + k % 3), 100.0 + k % 5) for k in range(0, 40)]
    up = [(100.5, 100.6, 100.4, 100.5)] * 2 + [(100.5, 111.0, 100.4, 104.0)] + [(104.0,) * 4] * 7 + \
        [(104.0, 104.2, 94.0, 96.0)] + [(96.0,) * 4] * 13
    prudent = [(100.5,) * 4] * 5 + [(100.5, 100.6, 94.0, 94.5)] + [(94.5, 100.0, 94.4, 100.0)] + [(100.0,) * 4] * 17
    for special, targets, basis in (({3: up}, (110.0,), None), ({3: prudent}, (), KN.BASIS_PRUDENT_CLOSE)):
        hrs = special[3]
        day3 = _bar(3, hrs[0][0], max(x[1] for x in hrs), min(x[2] for x in hrs), hrs[-1][3])
        bars = _flat(3) + [day3]
        bars += [_bar(i, b["open"], b["high"], b["low"], b["close"]) for i, b in enumerate(_flat(8, day3["close"]))][len(bars):]
        hours = hours_for(bars, special)
        (t,) = KN.run(_Once(side="LONG", stop=95.0, targets=tuple(SL.Target(SL.TARGET_PRICE, x) for x in targets)),
                      KN.SeriesData(symbol="TST", bars={"1d": bars, "1h": hours}, funding=funding,
                                    markpx_1h=S.markpx_from_funding(funding))).trades
        r_led, rec, _l = _ledger_r(bars, "LONG", 95.0, targets, funding, hours=hours)
        assert t.r["base"] == pytest.approx(r_led, abs=1e-9) and KN.FLAG_PATH_GAP not in t.flags
        if basis is None:
            assert t.exit_kind == KN.EXIT_TARGET and rec.exit_reason != "stop"
        else:
            assert t.legs[0].basis == basis == rec.features["exit_fill"]["basis"] and t.legs[0].ref == 94.5


def test_the_reference_reads_the_live_bar_timeframe():
    from tradingbot import strategy_paper
    assert PA.LIVE_BAR_TF == strategy_paper.BAR_TIMEFRAME == KN.PATH_TF
    assert (PA.MODE_LIVE, PA.MODE_OHLC) == ("live_1h", "ohlc")
    with pytest.raises(ValueError):
        PA.reference_run("K_D4", _world("K_D4"), mode="clipped")


# ============================================================================ belirlenimcilik (önbellek, süreç)
def _digest(kr: KN.KernelResult) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(kr.to_dict(), sort_keys=True).encode()).hexdigest()


DET_WORLD = ("T2", {"n": 300}, 23)


def test_determinism_a_then_b_equals_b_then_a_across_a_cleared_structure_cache_and_a_subprocess():
    """K_T2 ve K_M2 aynı 1d seride ortak yapı analizini (süreç içi önbellek, aynı anahtar) paylaşır: A→B ve önbellek
    temizlenip B→A aynı baytları verir; taze bir süreçteki koşu da aynı."""
    from tradingbot.structures.analysis import clear_cache
    fam, kw, seed = DET_WORLD
    data = S.WORLDS[fam](seed, **kw)
    clear_cache()
    a1 = _digest(KN.run(KN.make_variant("K_T2"), data))
    b1 = _digest(KN.run(KN.make_variant("K_M2"), data))
    clear_cache()
    b2 = _digest(KN.run(KN.make_variant("K_M2"), data))
    a2 = _digest(KN.run(KN.make_variant("K_T2"), data))
    assert (a1, b1) == (a2, b2)
    code = ("import sys, json, hashlib; sys.path.insert(0, %r); from tradingbot.research_engine import kernel as KN, "
            "synthetic as S; d = S.WORLDS[%r](%d, n=%d); "
            "print(hashlib.sha256(json.dumps(KN.run(KN.make_variant('K_M2'), d).to_dict(), sort_keys=True).encode()).hexdigest())"
            % (str(ROOT), fam, seed, kw["n"]))
    cp = subprocess.run([sys.executable, "-s", "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    assert cp.returncode == 0, cp.stderr[-2000:]
    assert cp.stdout.strip().splitlines()[-1] == b1


# ============================================================================ çalışma anı G/Ç yoklaması
import random as _random_mod  # noqa: E402
import time as _time_mod  # noqa: E402
from datetime import date as _date  # noqa: E402

_PROBE: dict[str, Any] = {"on": False, "hits": [], "files": set()}
_TB = str(ROOT / "tradingbot") + os.sep
_SAFE_SUFFIX = (".py", ".pyc", ".pyi", ".so", ".pth")


def _audit(event: str, args: tuple) -> None:
    if not _PROBE["on"]:
        return
    if event == "open":
        path = str(args[0]) if args else ""
        if not path.endswith(_SAFE_SUFFIX):
            _PROBE["hits"].append((event, path))
    elif event in ("os.listdir", "os.scandir"):
        path = str(args[0]) if args else ""
        if not any(path.startswith(p) for p in sys.path if p):
            _PROBE["hits"].append((event, path))
    elif event.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "urllib.",
                           "http.", "os.remove", "os.rename", "os.mkdir", "shutil.")):
        _PROBE["hits"].append((event, str(args)[:120]))


sys.addaudithook(_audit)
_CLOCK = {"time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns", "process_time", "sleep",
          "now", "utcnow", "today"}
_RANDOM = {"random", "getrandbits", "randbytes", "urandom", "getrandom"}


def _profile(frame, event, arg):
    """Profil kancası (yalnız yoklama sırasında): çağrılan Python dosyaları ve saat/rastgelelik C çağrıları."""
    if event == "call":
        _PROBE["files"].add(frame.f_code.co_filename)
    elif event == "c_call":
        name = getattr(arg, "__name__", "")
        if name in _CLOCK:
            owner = getattr(arg, "__self__", None)
            if owner is _time_mod or (isinstance(owner, type) and issubclass(owner, _date)):
                _PROBE["hits"].append(("clock:" + name, f"{frame.f_code.co_filename}:{frame.f_lineno}"))
        elif name in _RANDOM:
            owner = getattr(arg, "__self__", None)
            if owner is os or isinstance(owner, _random_mod.Random) or name in ("urandom", "getrandom"):
                _PROBE["hits"].append(("random:" + name, f"{frame.f_code.co_filename}:{frame.f_lineno}"))


PROBE_CASES = {"K_T2": ("T2", {"n": 280}, 22), "K_BOX": ("BOX", {"days": 2}, 21), "K_D4": ("D4", {"n": 260}, 22),
               "K_C4_CV003": ("C4", {"n": 400}, 21), "K_FM": ("FM", {"n": 450}, 21), "K_M2": ("M2", {"n": 280}, 21)}


@pytest.mark.parametrize("vid", sorted(PROBE_CASES))
def test_copies_do_no_io_clock_or_randomness_at_runtime(vid):
    """Altı kopyanın `signals` ve `manage`'i çalışırken: dosya açma (kaynak kodu yükleme hariç), dizin okuma, ağ/süreç
    (denetim kancası) ve saat/rastgelelik çağrısı (profil kancası, C çağrıları: `time.*`, `datetime.now/utcnow/today`,
    `os.urandom`, `random`) YOK. Tembel import'ların kaynak/bayt kodu okuması (.py/.pyc ve `sys.path` dizinleri) G/Ç
    sayılmaz. Aynı iz, kimliğin BİLDİRİLMİŞ dosya kümesinin (`identity.module_files`) çalışan her `tradingbot` dosyasını
    örttüğünü de sınar (m3)."""
    import importlib

    from tradingbot.strategy_lib import identity as SLI
    for m in SLI.declared_modules(vid):
        importlib.import_module(m)                   # içe aktarma anının yan etkileri (ör. multiprocessing anahtarı) yoklama dışı
    fam, kw, seed = PROBE_CASES[vid]
    data = S.WORLDS[fam](seed, **kw)
    var = KN.make_variant(vid)
    calls = {"signals": 0, "manage": 0}
    osig, oman = var.signals, var.manage

    def probed(fn, kind):
        def wrapper(*a):
            calls[kind] += 1
            _PROBE["on"] = True
            sys.setprofile(_profile)
            try:
                return fn(*a)
            finally:
                sys.setprofile(None)
                _PROBE["on"] = False
        return wrapper
    var.signals, var.manage = probed(osig, "signals"), probed(oman, "manage")
    _PROBE["hits"], _PROBE["files"] = [], set()
    res = KN.run(var, data)
    assert _PROBE["hits"] == [], _PROBE["hits"][:5]
    ran = {Path(f).relative_to(ROOT).as_posix() for f in _PROBE["files"] if f.startswith(_TB)}
    assert ran and ran <= set(SLI.module_files(vid)), sorted(ran - set(SLI.module_files(vid)))
    assert calls["signals"] > 50 and (calls["manage"] > 0 or not res.trades)


def test_the_io_probe_catches_a_file_read_and_a_clock_call():
    class Bad(_Once):
        def signals(self, bars, ctx):
            from datetime import datetime as _d
            with open(__file__ + ".nope", "a"):
                pass
            _time_mod.time()
            _d.now()
            _random_mod.random()
            return []
    _PROBE["hits"] = []
    _PROBE["on"] = True
    sys.setprofile(_profile)
    try:
        Bad(side="LONG", stop=95.0).signals({}, {"now_ms": 0})
    finally:
        sys.setprofile(None)
        _PROBE["on"] = False
        try:
            os.remove(__file__ + ".nope")
        except OSError:
            pass
    kinds = {h[0] for h in _PROBE["hits"]}
    assert {"open", "clock:time", "clock:now", "random:random"} <= kinds, kinds


# ============================================================================ parite (b)
def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


_FMB: dict[str, Any] = {}


def _fm_b() -> tuple[KN.SeriesData, KN.KernelResult]:
    if not _FMB:
        data = S.fm_world(21, n=1000)
        _FMB["v"] = (data, KN.run(KN.make_variant("K_FM"), data))
    return _FMB["v"]


def _rec(t: KN.KernelTrade, opened_at: str, *, r: float, codes=(), learning: bool = True, keyless: bool = False,
         variation: str | None = None, setup_type: str | None = None) -> dict:
    feats: dict[str, Any] = {}
    if t.evidence.get("structure"):
        feats["structure"] = dict(t.evidence["structure"])
    if not keyless:
        feats["data_source"] = {"bars": {"4h": int(t.signal_ts)}}
    if learning:
        feats["learning"] = {"learning_unlocked_by": list(codes)}
    if variation:
        feats["candle_variation"] = {"id": variation}
    out = {"symbol": t.symbol, "side": t.side, "opened_at": opened_at, "r_multiple": r, "features": feats}
    if setup_type:
        out["setup_type"] = setup_type
    return out


def _fm_view(**over) -> dict[str, Any]:
    from tradingbot.structures import POLICY_VERSION
    v = {"pattern_trader.protocol": "momentum_4h_v3", "pattern_trader.enabled": True, "pattern_trader.max_open_positions": 3,
         "pattern_trader.cooldown_bars_after_loss": 8, "structures.enabled": True, "structures.pattern_trader": "ENFORCE",
         "structures.policy_version": POLICY_VERSION, "fees.futures_taker_pct": 0.05, "fees.slippage_bps": 3.0,
         "learning_mode.enabled": True}
    v.update(over)
    return {k: x for k, x in v.items() if x is not None}


FAR = "2030-01-01T00:00:00+00:00"


def _mid(a_ms: int, b_ms: int, shift_ms: int = 0) -> str:
    return _iso((int(a_ms) + int(b_ms)) // 2 + shift_ms)


def _research(tmp_path, book: str, epochs: list[tuple], covered_until: str = FAR) -> tuple[Any, dict]:
    """Geçici mühürlü-biçim dönem dosyası: `epochs` = [(id, tür, from, to, görünüm|None)]; dönen (kök, görünümler)."""
    from tradingbot.research_engine import epochs as EP
    views, eps = {}, []
    for eid, kind, a, b, view in epochs:
        e = {"id": eid, "kind": kind, "books": [book], "from": a, "to": b}
        if view is not None:
            e["view_sha"] = EP.view_sha(view)
            views[e["view_sha"]] = view
        eps.append(e)
    (tmp_path / "library").mkdir(exist_ok=True)
    (tmp_path / "library" / "config_epochs.json").write_text(json.dumps({"epochs": eps, "covered_until": covered_until}),
                                                             encoding="utf-8")
    return tmp_path, views


def _fm_research(tmp_path, view_a: dict, view_b: dict, n_a: int = 22) -> tuple[Any, dict]:
    """EA: ilk `n_a` işlem; EB: kalanlar (sınır iki girişin ortası; kayıtların `opened_at`'ı gerçek giriş anı — canlı
    defterin geçmişi bu anlarla kurulur)."""
    _data, kr = _fm_b()
    t = kr.trades
    m = _mid(t[n_a - 1].entry_ts, t[n_a].entry_ts)
    return _research(tmp_path, "pattern_trader", [("EA", "CONFIG", "2019-01-01T00:00:00+00:00", m, view_a),
                                                  ("EB", "CONFIG", m, None, view_b)])


def _fm_records(dr_a: float = 0.01, dr_b: float = -0.02, n_a: int = 22) -> list[dict]:
    _data, kr = _fm_b()
    assert len(kr.trades) >= n_a + 3, len(kr.trades)
    return [_rec(t, _iso(t.entry_ts), r=t.r["base"] + (dr_a if i < n_a else dr_b)) for i, t in enumerate(kr.trades)]


def test_parity_b_passes_with_the_copy_built_from_each_epoch_view_and_forced_replay(tmp_path):
    data, kr = _fm_b()
    research, views = _fm_research(tmp_path, _fm_view(), _fm_view(**{"structures.pattern_trader": "SHADOW"}))
    res = PA.compare_b("K_FM", _fm_records(), data={data.symbol: data}, views=views, research=research,
                       copy_trades=kr.trades)
    assert res.verdict == PA.PASS, res.to_dict()
    eps = {e.epoch: e for e in res.epochs}
    assert set(eps) == {"EA", "EB"}
    a, b = eps["EA"], eps["EB"]
    assert (a.n_policy, a.n_matched, a.share, a.gated, a.passed) == (22, 22, 1.0, True, True)
    assert a.median_abs_dr == pytest.approx(0.01, abs=1e-9), "zorlanmış tek niyet yeniden oynatımı = sürekli koşu (yol yok)"
    assert a.params == KN.lib_params("K_FM")
    assert b.comparable and not b.gated and b.passed is None and b.params == {"protocol": "momentum_4h_v3", "structures": "SHADOW"}, \
        "dönemin değişen (eşlenebilir) parametresiyle kurulan kopya"
    assert b.n_matched == b.n_policy == len(kr.trades) - 22
    assert res.copy_extra == 0 and res.cohorts == {"POLICY": len(kr.trades)}


def test_parity_b_keyless_policy_record_is_unmatched_not_skipped(tmp_path):
    data, _kr = _fm_b()
    research, views = _fm_research(tmp_path, _fm_view(), _fm_view())
    recs = _fm_records()
    for r in recs[:2]:
        r["features"].pop("data_source")
    res = PA.compare_b("K_FM", recs, data={data.symbol: data}, views=views, research=research)
    ea = next(e for e in res.epochs if e.epoch == "EA")
    assert (ea.n_policy, ea.n_matched, ea.unmatched) == (22, 20, {"NO_DATA_SOURCE_BARS": 2})
    assert ea.share == pytest.approx(20 / 22) and ea.passed is False and res.verdict == PA.PARITY_FAIL
    res = PA.compare_b("K_FM", _fm_records(), data={}, views=views, research=research)
    assert res.verdict == PA.PARITY_FAIL and next(e for e in res.epochs if e.epoch == "EA").unmatched == {"NO_SERIES": 22}


def test_parity_b_fails_on_median_dr_in_a_gated_epoch(tmp_path):
    data, _kr = _fm_b()
    research, views = _fm_research(tmp_path, _fm_view(), _fm_view())
    res = PA.compare_b("K_FM", _fm_records(dr_a=0.08), data={data.symbol: data}, views=views, research=research)
    assert res.verdict == PA.PARITY_FAIL and [e.passed for e in res.epochs] == [False, None]


def test_parity_b_epoch_whose_view_cannot_be_mapped_is_not_comparable(tmp_path):
    data, _kr = _fm_b()
    recs = _fm_records()
    cases = {"UNMAPPED_KEY:pattern_trader.new_rule_knob": _fm_view(**{"pattern_trader.new_rule_knob": 1}),
             "COPY_REJECTS_PARAMS": _fm_view(**{"pattern_trader.protocol": "classic"}),
             "FEES_DIFFER:fees.slippage_bps": _fm_view(**{"fees.slippage_bps": 5.0}),
             "STRUCTURES_POLICY_VERSION": _fm_view(**{"structures.policy_version": "structures_v0"}),
             "VIEW_PATH_MISSING:structures.enabled": _fm_view(**{"structures.enabled": None})}
    for why, view_a in cases.items():
        research, views = _fm_research(tmp_path, view_a, _fm_view())
        res = PA.compare_b("K_FM", recs, data={data.symbol: data}, views=views, research=research)
        ea = next(e for e in res.epochs if e.epoch == "EA")
        assert not ea.comparable and not ea.gated and ea.reason.startswith(why), (why, ea.reason)
        assert ea.to_dict()["status"] == PA.NOT_COMPARABLE and ea.unmatched == {PA.NOT_COMPARABLE: 22}
        assert res.verdict == PA.PARITY_UNPROVEN, "kapılı karşılaştırılabilir dönem yok → kanıtlanmadı"
    research, views = _fm_research(tmp_path, _fm_view(), _fm_view())
    assert PA.compare_b("K_FM", recs, data={data.symbol: data}, views=None, research=research).epochs[0].reason == \
        "VIEW_UNAVAILABLE"
    bad = {k: dict(v, extra=1) for k, v in views.items()}
    assert PA.compare_b("K_FM", recs, data={data.symbol: data}, views=bad, research=research).epochs[0].reason == \
        "VIEW_SHA_MISMATCH"


def test_parity_b_counts_only_policy_entries_and_unassigned_epochs(tmp_path):
    from tradingbot.learning_mode import UNLOCK_CAPACITY
    data, kr = _fm_b()
    t = kr.trades
    m24, x_end, cov = _mid(t[24].entry_ts, t[25].entry_ts), _mid(t[25].entry_ts, t[26].entry_ts, -3_600_000), \
        _mid(t[25].entry_ts, t[26].entry_ts)
    research, views = _research(tmp_path, "pattern_trader", [
        ("EA", "CONFIG", "2019-01-01T00:00:00+00:00", m24, _fm_view()), ("XT", "TRANSITION", m24, x_end, None),
        ("EB", "CONFIG", x_end, None, _fm_view())], covered_until=cov)
    cap = sorted(UNLOCK_CAPACITY)[0]
    recs = [_rec(x, _iso(x.entry_ts), r=x.r["base"]) for x in t[:19]]
    recs += [_rec(x, _iso(x.entry_ts), r=-1.0, codes=[cap]) for x in t[19:22]]                       # CAPACITY: dışarıda
    recs += [_rec(x, _iso(x.entry_ts), r=-1.0, codes=["STRUCTURE_WAIT_TRIGGER"]) for x in t[22:24]]  # SELECTIVITY
    recs += [_rec(x, _iso(x.entry_ts), r=-1.0, learning=False) for x in t[24:25]]                    # UNTAGGED
    recs += [_rec(x, _iso(x.entry_ts), r=0.0) for x in t[25:28]]                     # TRANSITION (1), kapsam dışı (2)
    res = PA.compare_b("K_FM", recs, data={data.symbol: data}, views=views, research=research)
    assert res.cohorts == {"POLICY": 22, "CAPACITY": 3, "SELECTIVITY": 2, "UNTAGGED": 1}
    assert res.unassigned_epoch == 3, "TRANSITION penceresi ve mühür kapsamı dışı dönem ATANMAZ"
    (ep,) = res.epochs
    assert (ep.epoch, ep.n_policy, ep.n_matched, ep.gated, ep.passed) == ("EA", 19, 19, False, None)
    assert res.verdict == PA.PARITY_UNPROVEN


def _c4_view(variations) -> dict[str, Any]:
    p = "strategy_paper.extra[name=c4_candle_variations]"
    return {f"{p}.breakeven_at_mfe_r": 0.0, f"{p}.max_entry_drift_pct": 0.0, f"{p}.enabled": True,
            f"{p}.rule_params.entry_window_min": 60, f"{p}.rule_params.leverage": 1,
            f"{p}.rule_params.variations": list(variations), f"{p}.symbols": ["BTC/USDT"]}


def test_parity_b_c4_compares_each_variation_only_with_its_own_records(tmp_path):
    data = S.c4_world(21, n=1500)
    k3 = KN.run(KN.make_variant("K_C4_CV003"), data).trades
    k4 = KN.run(KN.make_variant("K_C4_CV004"), data).trades
    assert len(k3) >= 3 and len(k4) >= 3
    cv3, cv4 = KN.lib_params("K_C4_CV003")["variation"], KN.lib_params("K_C4_CV004")["variation"]
    recs = [_rec(t, _iso(t.entry_ts), r=t.r["base"], variation=cv3) for t in k3[:-1]]
    recs += [_rec(k3[-1], _iso(k3[-1].entry_ts), r=k3[-1].r["base"], setup_type="candle:" + cv3)]   # yalnız setup_type
    recs += [_rec(t, _iso(t.entry_ts), r=t.r["base"], variation=cv4) for t in k4]
    recs += [_rec(k3[0], _iso(k3[0].entry_ts), r=0.0)]                                               # varyasyon yok
    book = "strategy_paper_candle4h"
    research, views = _research(tmp_path, book, [("EA", "CONFIG", "2019-01-01T00:00:00+00:00", None,
                                                  _c4_view(RG.C4_VARIATIONS))])
    r3 = PA.compare_b("K_C4_CV003", recs, data={data.symbol: data}, views=views, research=research)
    (e3,) = r3.epochs
    assert (r3.other_variation, r3.no_variation) == (len(k4), 1)
    assert (e3.n_policy, e3.n_matched, e3.unmatched) == (len(k3) + 1, len(k3), {"NO_VARIATION": 1})
    assert e3.median_abs_dr == pytest.approx(0.0, abs=1e-9)
    r4 = PA.compare_b("K_C4_CV004", recs, data={data.symbol: data}, views=views, research=research)
    (e4,) = r4.epochs
    assert (r4.other_variation, e4.n_policy, e4.n_matched) == (len(k3), len(k4) + 1, len(k4))
    # dönem görünümünde varyasyon etkin değil → karşılaştırılamaz
    research, views = _research(tmp_path, book, [("EA", "CONFIG", "2019-01-01T00:00:00+00:00", None, _c4_view([cv4]))])
    (e,) = PA.compare_b("K_C4_CV003", recs, data={data.symbol: data}, views=views, research=research).epochs
    assert not e.comparable and e.reason == f"VARIATION_NOT_ACTIVE:{cv3}"


def test_params_from_view_maps_epoch_values_and_fails_closed():
    from tradingbot.structures import POLICY_VERSION
    t2 = "strategy_paper[name=t2_trend_regime]"
    v = {f"{t2}.atr_mult": 2.5, f"{t2}.breakeven_at_mfe_r": 0.0, f"{t2}.max_entry_drift_pct": 0.0, f"{t2}.enabled": True,
         f"{t2}.rule_params.leverage": 2, "structures.enabled": True, "structures.t2_trend_regime": "ENFORCE",
         "structures.policy_version": POLICY_VERSION,
         "learning_mode.strategy_overrides.structures_entry_shadow": ["t2_trend_regime"]}
    p, why = PA.params_from_view("K_T2", v)
    assert why is None and p == {"atr_mult": 2.5, "breakeven_at_mfe_r": 0.0, "max_entry_drift_pct": 0.0,
                                 "structures": "ENFORCE"}, "öğrenme kipinin giriş gölgesi ENFORCE kopyasıyla karşılaştırılır"
    assert PA.params_from_view("K_T2", dict(v, **{f"{t2}.rule_params.ema_len": 100}))[1] == \
        f"UNMAPPED_KEY:{t2}.rule_params.ema_len"
    assert PA.params_from_view("K_T2", dict(v, **{f"{t2}.breakeven_at_mfe_r": 0.5}))[1].startswith("COPY_REJECTS_PARAMS")
    assert PA.params_from_view("K_T2", {k: x for k, x in v.items() if not k.endswith("max_entry_drift_pct")})[1] == \
        f"VIEW_PATH_MISSING:{t2}.max_entry_drift_pct"
    assert PA.params_from_view("K_T2", dict(v, **{"structures.enabled": False}))[0]["structures"] == "OFF"
    bx = "strategy_paper.extra[name=b1_box_fade]"
    vb = {f"{bx}.breakeven_at_mfe_r": 0.0, **{f"{bx}.rule_params.{k}": x for k, x in (
        ("near_frac", 0.1), ("trigger", "break_prev"), ("long_stop", "day_low"), ("exit_kind", "box_mid"), ("exit_r", 2.0),
        ("eod_close", True), ("allow_long", True), ("allow_short", True), ("min_stop_pct", 2.22), ("leverage", 3))},
        "structures.enabled": True, "structures.b1_box_fade": "SHADOW", "structures.policy_version": POLICY_VERSION}
    assert PA.params_from_view("K_BOX", vb)[0]["min_stop_pct"] == 2.22, "öğrenme kipi yok → kuralın kendi değeri"
    vl = dict(vb, **{"learning_mode.enabled": True, "learning_mode.books.b1_box_fade.enabled": True,
                     "learning_mode.books.b1_box_fade.min_stop_pct": 0.32})
    assert PA.params_from_view("K_BOX", vl)[0]["min_stop_pct"] == 0.32, "öğrenme kipinde kural öğrenme tabanıyla çağrılır"
    assert PA.params_from_view("K_BOX", dict(vb, **{"structures.b1_box_fade": "ENFORCE"}))[1].startswith("COPY_REJECTS_PARAMS")
    assert PA.params_from_view("K_BOX", dict(vb, **{f"{bx}.max_entry_drift_pct": 1.0}))[1] == "UNMODELLED:max_entry_drift_pct"


def test_sealed_epoch_views_resolve_from_the_release_configs_and_map_to_the_seal():
    """Git geçmişi varsa: mühürlü dönem dosyasındaki her K defteri CONFIG döneminin `view_sha`'sı sürüm config'lerinden
    kurulan görünümlerle çözülür; son dönem (mühür anındaki config) kopyanın mühürlü parametrelerine eşlenir."""
    root = ROOT
    if subprocess.run(["git", "-C", str(root), "cat-file", "-e", "db5db96^{commit}"], capture_output=True).returncode != 0:
        pytest.skip("git geçmişi yok")
    from tradingbot.research_engine import epochs as EP
    blobs = {rel: subprocess.run(["git", "-C", str(root), "cat-file", "blob", f"{rel}:config.yaml"], capture_output=True,
                                 check=True).stdout for rel in EP.releases_needed()}
    views = PA.views_from_configs(blobs)
    doc = json.loads((RG.PKG_DIR / "library" / "config_epochs.json").read_text(encoding="utf-8"))
    for vid in ("K_T2", "K_M2", "K_BOX", "K_D4", "K_C4_CV003", "K_FM"):
        eps = [e for e in doc["epochs"] if PA.BOOK_DIR[vid] in e["books"] and e.get("view_sha")]
        assert all(e["view_sha"] in views for e in eps), vid
        p, why = PA.params_from_view(vid, views[eps[-1]["view_sha"]])
        assert why is None and p == KN.lib_params(vid), (vid, why, p)


def test_parity_fail_marks_copy_rows_ineligible_for_the_leaderboard_and_gate(tmp_path):
    data, _kr = _fm_b()
    research, views = _fm_research(tmp_path, _fm_view(), _fm_view())
    ok = PA.compare_b("K_FM", _fm_records(), data={data.symbol: data}, views=views, research=research)
    bad = PA.compare_b("K_T2", [], data={}, views=views, research=research)
    rows = [{"variant_id": "K_FM", "row_id": "K_FM@POOLED_OWN"}, {"variant_id": "K_T2", "row_id": "K_T2@POOLED_OWN"},
            {"variant_id": "K_M2", "row_id": "K_M2@POOLED_OWN"}, {"variant_id": "TS_28_LS", "row_id": "TS_28_LS@POOLED_P23"}]
    out = PA.mark_parity(rows, {"K_FM": ok, "K_T2": bad})
    by = {r["variant_id"]: r for r in out}
    assert ok.verdict == PA.PASS and by["K_FM"]["eligible"] is True and by["K_FM"]["parity"] == PA.PASS
    assert by["K_T2"]["eligible"] is False and PA.PARITY_UNPROVEN in by["K_T2"]["flags"]
    assert by["K_M2"]["eligible"] is False, "parite ölçülmemiş kopya da uygun değil"
    assert "parity" not in by["TS_28_LS"] and "eligible" not in by["TS_28_LS"]
    failing = PA.compare_b("K_FM", _fm_records(dr_a=9.0), data={data.symbol: data}, views=views, research=research)
    assert PA.mark_parity(rows[:1], {"K_FM": failing})[0]["flags"] == [PA.PARITY_FAIL]
