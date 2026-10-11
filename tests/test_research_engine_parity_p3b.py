"""P3b-1 — parite (a) ve (b), altın sentetik yollar, belirlenimcilik (docs/ENGINE_BRAIN_V1.md §2.1; ana belge §6.2).

* Altın yollar: her K ailesi için küçük belirlenimci sentetik dünyada çekirdeğin işlem listesi (sinyal barı, yön, çıkış
  türü, üç senaryonun R'si) sabitlenir. Kural/çekirdek değişirse test kırılır ve değer bilerek yeniden sabitlenir.
* Parite (a): aynı dünyada çekirdek ↔ canlı kural + `apply_action` + `FuturesLedgerV2.tick`: sinyal kimliği %100,
  medyan |ΔR| ≤ 0,02, p95 ≤ 0,05 (mühürlü eşikler). Testteki dünyalar küçüktür (süre); ≥ 200 işlem/aile ölçümü
  `scripts/engine_p3b_measure.py` ile yapılır (belge §8.4 P3b-1 notu) ve `TB_P3B_FULL_PARITY=1` ile burada da koşar.
* Betikli defter çapraz denetimi: TP1 + başa-baş, boşluk, likidasyon ve fonlama elle kurulmuş yollarda çekirdek R'si
  defterin `r_multiple`'ına eşit.
* Parite (b): fixture defterleriyle dönem başına POLICY kohortu, ≥ 20 giriş kapısı, ≥ %95 aynı bar/yön, medyan
  |ΔR| ≤ 0,05; `PARITY_FAIL` / `PARITY_UNPROVEN` işaretleri ve lider satırına yansıması.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradingbot import strategy_lib as SL
from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import parity as PA
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import synthetic as S

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
GOLDEN: dict[str, list[tuple]] = {
    "K_BOX": [
        (1578009600000, "LONG", "STOP", -1.188618, -1.223598, -1.307818),
        (1578013800000, "LONG", "STOP", -1.220193, -1.259175, -1.354542),
        (1578015600000, "LONG", "STOP", -1.1767, -1.210047, -1.289859),
        (1578017700000, "LONG", "STOP", -1.196099, -1.23209, -1.319016),
        (1578019500000, "LONG", "STOP", -1.1211, -1.145796, -1.203524),
        (1578024600000, "LONG", "STOP", -1.174093, -1.207103, -1.285931),
        (1578031200000, "LONG", "STOP", -1.125475, -1.15094, -1.210497),
        (1578038400000, "LONG", "STOP", -1.138307, -1.165905, -1.230738),
        (1578042300000, "LONG", "STOP", -1.159019, -1.189855, -1.262913),
        (1578044700000, "LONG", "STOP", -1.211945, -1.25002, -1.342533),
        (1578052200000, "LONG", "STOP", -1.09489, -1.117023, -1.168014),
        (1578078600000, "LONG", "STOP", -1.197759, -1.234054, -1.321563),
        (1578079500000, "LONG", "STOP", -1.142818, -1.171159, -1.237812),
        (1578085200000, "LONG", "STOP", -1.212424, -1.250579, -1.343255),
        (1578090900000, "LONG", "STOP", -1.154165, -1.184291, -1.255449),
        (1578096300000, "LONG", "STOP", -1.187851, -1.222841, -1.306764),
        (1578099300000, "LONG", "TARGET", 4.99656, 4.789363, 4.561497),
        (1578194400000, "SHORT", "STOP", -1.205683, -1.243213, -1.333789),
        (1578203700000, "SHORT", "STOP", -1.203704, -1.240984, -1.330861),
        (1578206400000, "SHORT", "TARGET", 6.116735, 5.754729, 5.380861),
        (1578243600000, "LONG", "STOP", -1.159168, -1.190056, -1.263168),
        (1578248400000, "LONG", "STOP", -1.107388, -1.129764, -1.181635),
        (1578253500000, "LONG", "END", 1.502812, 1.406315, 1.284262),
    ],
    "K_C4_CV003": [
        (1585540800000, "LONG", "STOP", -1.025678, -1.031406, -1.044807),
        (1586635200000, "LONG", "TARGET", 1.966953, 1.950289, 1.926476),
        (1588190400000, "LONG", "STOP", -1.026251, -1.032068, -1.045763),
        (1588420800000, "LONG", "TARGET", 1.913902, 1.888303, 1.851737),
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
        (1579852800000, "LONG", "TARGET", 1.974777, 1.963356, 1.947476),
        (1579982400000, "LONG", "STOP", -1.028029, -1.03087, -1.037522),
        (1580400000000, "LONG", "TARGET", 1.948057, 1.93128, 1.907862),
        (1580486400000, "LONG", "TIME", -0.04626, -0.05536, -0.070078),
        (1581004800000, "LONG", "TIME", 1.865439, 1.851765, 1.833787),
        (1581523200000, "LONG", "TARGET", 1.983917, 1.972566, 1.956312),
        (1581652800000, "LONG", "TARGET", 1.942103, 1.922335, 1.894228),
        (1581724800000, "LONG", "TIME", 1.069707, 1.060225, 1.046833),
        (1583337600000, "LONG", "STOP", -1.046033, -1.050835, -1.062235),
        (1584072000000, "LONG", "TARGET", 1.981685, 1.971053, 1.955824),
        (1584432000000, "LONG", "TARGET", 1.992191, 1.982424, 1.9683),
        (1584547200000, "LONG", "TIME", -0.167399, -0.172837, -0.182059),
        (1585080000000, "LONG", "TARGET", 1.963063, 1.952581, 1.937324),
        (1585209600000, "LONG", "STOP", -1.02961, -1.032066, -1.038018),
        (1587067200000, "LONG", "TARGET", 1.982306, 1.973005, 1.959446),
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
    kr, refs, _data = _runs(vid)
    a = PA.compare_a([(vid, kr, refs)], min_trades=1)
    th = RG.LIB_V1["thresholds"]["parity_kernel"]
    assert a.n_matched >= 1
    assert a.identity_share == 1.0 >= th["min_signal_identity_share"], (a.only_kernel, a.only_ref)
    assert a.median_abs_dr <= th["max_median_abs_dr"] and a.p95_abs_dr <= th["max_p95_abs_dr"], a.to_dict()
    assert a.passed


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


def _ledger_r(bars, side, stop, targets, funding):
    """Defter yolu: 2. barın açılışında `apply_action` OPEN (çekirdeğin kaldıracıyla), sonra `learning_cf` sırasıyla tik."""
    from tradingbot.accounting import TickData
    from tradingbot.core import iso
    from tradingbot.learning_cf import _bar_path
    from tradingbot.risk import build_state
    from tradingbot.strategy_paper import DataVerdict, apply_action
    data = KN.SeriesData(symbol="TST", bars={"1d": bars}, funding=funding)
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
    long = side == "LONG"
    for i in range(2, len(bars)):
        b = bars[i]
        odt = datetime.fromtimestamp(b["timestamp"] / 1000, tz=timezone.utc)
        cdt = datetime.fromtimestamp((b["timestamp"] + D1) / 1000, tz=timezone.utc)
        pts = _bar_path(long, None if i == 2 else b["open"], b["high"], b["low"], b["close"])
        for point, px in pts:
            p = led.positions.get("TST")
            if p is None:
                break
            if point != "open" and ((px <= float(p.stop)) if long else (px >= float(p.stop))):
                px = float(p.stop)
            at2 = odt if point == "open" else cdt
            led.tick({"TST": TickData(last=Decimal(repr(px)), mark=Decimal(repr(px)), ts=iso(at2))}, now_utc=at2,
                     funding_rate_lookup=lookup)
    if led.positions.get("TST") is not None:
        led.close_manual("TST", Decimal(repr(bars[-1]["close"])), now=datetime.fromtimestamp(
            (bars[-1]["timestamp"] + D1) / 1000, tz=timezone.utc))
    (rec,) = led.history
    return float(rec.r_multiple), rec


def _flat(n, px=100.0):
    return [_bar(i, px, px * 1.0005, px * 0.9995, px) for i in range(n)]


CROSS = {
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
                  KN.SeriesData(symbol="TST", bars={"1d": bars}, funding=funding)).trades
    r_led, rec = _ledger_r(bars, side, stop, targets, funding)
    assert t.r["base"] == pytest.approx(r_led, abs=1e-9), (case, t.exit_kind, rec.exit_reason)
    risk_k = abs(t.entry_ref * (1 + (1 if side == "LONG" else -1) * 0.0003) - stop) * t.qty
    risk_l = float(rec.features["risk_usdt"])
    assert t.funding_usdt / risk_k == pytest.approx(float(rec.funding) / risk_l, abs=1e-9), "fonlama R cinsinden aynı"
    if case == "liquidation_gap":
        assert t.exit_kind == KN.EXIT_LIQ and rec.exit_reason == "likidasyon"
    if case == "tp1_breakeven":
        assert [lg.kind for lg in t.legs] == [KN.EXIT_TARGET, KN.EXIT_STOP] and rec.tp1_done


# ============================================================================ parite (b)
BOOK = "strategy_paper_trend4h"
EP_A, EP_B = "2026-10-01T12:00:00+00:00", "2026-10-07T12:00:00+00:00"       # E2026-09-28T2120Z, E2026-10-05T0455Z
TRANSITION_AT, UNCOVERED_AT = "2026-10-04T20:00:00+00:00", "2026-10-12T12:00:00+00:00"
H4 = 14_400_000


def _copy(n: int, r: float = 0.5) -> list[KN.KernelTrade]:
    out = []
    for i in range(n):
        t = KN.KernelTrade(symbol="SOLUSDT", variant_id="K_D4", side="LONG", signal_ts=T0 + i * 10 * H4, entry_ts=T0 + (i * 10 + 1) * H4,
                           entry_ref=100.0, initial_stop=95.0, targets=(), leverage=1, qty=0.2, notional=20.0, margin=20.0,
                           liq_price=0.0, risk_usdt=1.0)
        t.r = {"base": r + 0.001 * i, "ADVERSE": r, "STRESS": r}
        out.append(t)
    return out


def _live(i: int, opened_at: str, *, r: float, codes=(), learning: bool = True, side: str = "LONG") -> dict:
    feats = {"data_source": {"bars": {"4h": T0 + i * 10 * H4}}}
    if learning:
        feats["learning"] = {"learning_unlocked_by": list(codes)}
    return {"symbol": "SOL/USDT", "side": side, "opened_at": opened_at, "r_multiple": r, "features": feats}


def test_parity_b_passes_when_every_gated_epoch_reproduces_the_policy_entries():
    copy = _copy(60)
    live = [_live(i, EP_A, r=copy[i].r["base"] + 0.01) for i in range(25)]
    live += [_live(25 + i, EP_B, r=copy[25 + i].r["base"] - 0.02) for i in range(22)]
    res = PA.compare_b("K_D4", copy, live)
    assert res.verdict == PA.PASS
    eps = {e.epoch: e for e in res.epochs}
    assert set(eps) == {"E2026-09-28T2120Z", "E2026-10-05T0455Z"}
    assert all(e.gated and e.passed and e.share == 1.0 for e in eps.values())
    assert eps["E2026-09-28T2120Z"].median_abs_dr == pytest.approx(0.01)
    assert res.copy_extra == 60 - 47


def test_parity_b_fails_on_share_or_on_median_dr_in_any_gated_epoch():
    copy = _copy(60)
    live = [_live(i, EP_A, r=copy[i].r["base"]) for i in range(25)]
    live += [_live(100 + i, EP_B, r=0.0) for i in range(2)] + [_live(25 + i, EP_B, r=copy[25 + i].r["base"]) for i in range(20)]
    res = PA.compare_b("K_D4", copy, live)                 # B: 20/22 = %90,9 < %95
    assert res.verdict == PA.PARITY_FAIL
    assert [e.passed for e in res.epochs] == [True, False]
    live = [_live(i, EP_A, r=copy[i].r["base"] + 0.08) for i in range(25)]
    assert PA.compare_b("K_D4", copy, live).verdict == PA.PARITY_FAIL       # medyan |ΔR| 0,08 > 0,05


def test_parity_b_counts_only_policy_entries_and_small_epochs_are_reported_not_gated():
    from tradingbot.learning_mode import UNLOCK_CAPACITY
    cap = sorted(UNLOCK_CAPACITY)[0]
    copy = _copy(60)
    live = [_live(i, EP_A, r=copy[i].r["base"]) for i in range(19)]
    live += [_live(100 + i, EP_A, r=-1.0, codes=[cap]) for i in range(10)]                 # CAPACITY: dışarıda
    live += [_live(200 + i, EP_A, r=-1.0, codes=["STRUCTURE_SHADOW_ENTRY"]) for i in range(10)]   # SELECTIVITY
    live += [_live(300 + i, EP_A, r=-1.0, learning=False) for i in range(10)]               # UNTAGGED
    live += [_live(19 + i, TRANSITION_AT, r=0.0) for i in range(5)] + [_live(24 + i, UNCOVERED_AT, r=0.0) for i in range(5)]
    res = PA.compare_b("K_D4", copy, live)
    assert res.cohorts == {"POLICY": 29, "CAPACITY": 10, "SELECTIVITY": 10, "UNTAGGED": 10}
    assert res.unassigned_epoch == 10, "TRANSITION penceresi ve mühür kapsamı dışı dönem ATANMAZ"
    (ep,) = res.epochs
    assert (ep.n_policy, ep.gated, ep.passed) == (19, False, None)
    assert res.verdict == PA.PARITY_UNPROVEN


def test_parity_fail_marks_copy_rows_ineligible_for_the_leaderboard_and_gate():
    copy = _copy(30)
    ok = PA.compare_b("K_D4", copy, [_live(i, EP_A, r=copy[i].r["base"]) for i in range(25)])
    bad = PA.compare_b("K_T2", copy, [])
    rows = [{"variant_id": "K_D4", "row_id": "K_D4@POOLED_OWN"}, {"variant_id": "K_T2", "row_id": "K_T2@POOLED_OWN"},
            {"variant_id": "K_M2", "row_id": "K_M2@POOLED_OWN"}, {"variant_id": "TS_28_LS", "row_id": "TS_28_LS@POOLED_P23"}]
    out = PA.mark_parity(rows, {"K_D4": ok, "K_T2": bad})
    by = {r["variant_id"]: r for r in out}
    assert ok.verdict == PA.PASS and by["K_D4"]["eligible"] is True and by["K_D4"]["parity"] == PA.PASS
    assert by["K_T2"]["eligible"] is False and PA.PARITY_UNPROVEN in by["K_T2"]["flags"]
    assert by["K_M2"]["eligible"] is False, "parite ölçülmemiş kopya da uygun değil"
    assert "parity" not in by["TS_28_LS"] and "eligible" not in by["TS_28_LS"]
    failing = PA.compare_b("K_D4", copy, [_live(i, EP_A, r=9.0) for i in range(25)])
    assert PA.mark_parity(rows[:1], {"K_D4": failing})[0]["flags"] == [PA.PARITY_FAIL]
