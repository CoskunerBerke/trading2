"""P3b-2b — O örtüleri, hız önlemleri ve kapı dışı işaretler (docs/ENGINE_BRAIN_V1.md §2.2 satır O, §5.3, §8.4 P3b-2b).

* Katalog: 35 örtü (taban başına 7) uygulandı; çalıştırılabilir toplam 93 = kaydın 106 − 13'ü; her örtünün kendi
  `spec_sha`'sı ve bildirilmiş modül kümesi (faktör modülü + `o_cover` + `stream` + taban modülü; elle sabitlenmiş).
* Örtüler YALNIZ mühürlü satırdan kurulur (faktör parametreleri + teknik okumalar `LIB_V1.covers.factors.<F>.reading`;
  taban parametreleri `copy_books`'tan); okunuş/parametre değişikliği kapalı hatadır. C4 tabanı = C4 defteri (sekiz
  varyasyon config sırasıyla, ilk eşleşen = canlı `candle_book.decide`).
* Faktörler elle kurulmuş yollarda: F_ER (ER20 eşiği, Box'ta ters ve 1d), F_BTC (canlı `regime_gate.btc_regime`, 4h, < 200
  bar → giriş yok), F_VOL (ATR% sırası), X_TRAIL2 (+1R silahlanma, 2 ATR iz, yalnız sıkılaştırır, delinince CLOSE), X_CHAN3
  (girişten bu yana en iyi uç − 3 × ATR22), X_TIME (yarı tutuş: 150/150/150/12/48), X_TPL (üçte bir, başa-baş). Filtreler
  geleceği görmez (seri karar anında kesilince aynı niyet). Tabanın CLOSE'u önceliklidir.
* Hız önlemleri bayt-aynı: (a) paylaşılan akış — aile koşusu = her örtünün ayrı koşusu = düz yol (`fast=False`); (b) Box
  ön-süzgeci — uzun dünyalarda ve uç durumlarda her karar barında "süzgeç atladı ⇒ canlı kural da OPEN/CLOSE üretmez",
  çekirdek koşusu düz yolla bayt-aynı; D4 ön-süzgeci aynı; (c) hızlı çerçeve — her canlı satır okuyucusunun çıktısı pandas
  çerçevesininkiyle aynı (değer, tür, anahtar sırası, NaN), K kopyalarının koşusu bayt-aynı.
* Kapı dışı işaretler (`WF_PROTOCOL_V1.gate_exclusions`): FUNDING_GAP, PATH_1H_GAP, F8_UNMEASURABLE; `kernel.gate_excluded`.
* Parite (a) örtülerde (başvuru = örtünün kendi kuralı → `apply_action` → `FuturesLedgerV2`; MOVE_STOP ve X_TPL payları);
  çalışma anı G/Ç-saat yoklaması; ≥ 200 işlem ölçümü `TB_P3B_FULL_PARITY=1` ile.
"""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tradingbot import box_theory, candle_book, ema200_trend, paper_rules, regime_gate
from tradingbot import strategy_lib as SL
from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import parity as PA
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import synthetic as S
from tradingbot.strategy_lib import catalog as SLC
from tradingbot.strategy_lib import identity as SLI
from tradingbot.strategy_lib import indicators as G
from tradingbot.strategy_lib import livebars as LB
from tradingbot.strategy_lib import o_cover as OC
from tradingbot.strategy_lib import stream as ST

ROOT = Path(__file__).resolve().parents[1]
BASES = ("T2", "M2", "BOX", "D4", "C4")
FACTORS = ("F_ER", "F_BTC", "F_VOL", "X_TRAIL2", "X_CHAN3", "X_TIME", "X_TPL")
COVERS = [f"O_{b}_{f}" for b in BASES for f in FACTORS]
H4, D1, M5 = S.H4, S.D1, S.M5
WORLD = {"T2": lambda: S.trend_world(22, n=420), "M2": lambda: S.trend_world(21, n=420),
         "BOX": lambda: S.box_world(21, days=6), "D4": lambda: S.d4_world(22, n=700), "C4": lambda: S.c4_world(21, n=700)}


def _dump(res: KN.KernelResult) -> str:
    return json.dumps(res.to_dict(), sort_keys=True)


# ============================================================================ katalog ↔ kayıt
def test_covers_catalog_reconciles_with_the_registry():
    vs = RG.variants_by_id()
    assert sorted(v for v in vs if vs[v]["family"] == "O") == sorted(COVERS)
    assert all(SLC.status(v) == SLC.IMPLEMENTED_S and SLC.FAMILY_OF[v] == "O" for v in COVERS)
    assert not SLC.PENDING_PREFIXES and not [v for v in vs if SLC.status(v) == SLC.PENDING_S]
    assert Counter(KN.covers_of(b)[0].split("_")[1] for b in BASES) == Counter(BASES)
    assert all(len(KN.covers_of(b)) == 7 for b in BASES)
    runnable = RG.unique_variant_count() - RG.blocked_budget()["variants"]
    assert len(SLC.IMPLEMENTED) == runnable == 93 and RG.family_counts()["O"] == 35
    for vid in COVERS:
        b, f = vid.split("_", 2)[1:]
        assert SLC.module_name(vid) == f"tradingbot.strategy_lib.{SLC.COVER_FACTOR_MODULE[f]}"
        assert SLC.extra_modules(vid) == (f"tradingbot.strategy_lib.{SLC.COVER_BASE_MODULE[b]}",)
        assert vs[vid]["cover"]["base"] == b and vs[vid]["cover"]["factor"] == f


def test_covers_are_built_from_the_sealed_row_only():
    vs = RG.variants_by_id()
    for vid in COVERS:
        var = KN.make_variant(vid)
        b = vs[vid]["cover"]["base"]
        assert (var.variant_id, var.family, var.base_name) == (vid, "O", b)
        assert list(var.timeframes) == list(vs[vid]["timeframes"]) and var.signal_tf == vs[vid]["signal_tf"]
        assert dict(var.lookback) == dict(var.shared.base.lookback)
        p, spec = KN.lib_params(vid), KN.variant_spec(vid)
        assert spec["base_params"] == [KN.lib_params(x) for x in vs[vid]["cover"]["base_variants"]]
        assert spec["base_max_hold"] == RG.thaw(RG.LIB_V1["copy_books"][b]["max_hold"])
        with pytest.raises(ValueError):
            SLC.make(vid, {**p, "extra": 1}, spec)
        with pytest.raises(ValueError):
            SLC.make(vid, p, None)
        with pytest.raises(ValueError):
            SLC.make(vid, dict(p, factor_params=dict(p["factor_params"], kind="nope")), spec)
        bad = dict(p["factor_params"]["reading"], timeframe="X") if "timeframe" in p["factor_params"]["reading"] else \
            dict(p["factor_params"]["reading"], target_from="SIGNAL_CLOSE") if vid.endswith("X_TPL") else \
            dict(p["factor_params"]["reading"], window_bars=500) if vid.endswith("F_BTC") else \
            dict(p["factor_params"]["reading"], bars={**p["factor_params"]["reading"]["bars"], b: 7})
        with pytest.raises(ValueError):
            SLC.make(vid, dict(p, factor_params=dict(p["factor_params"], reading=bad)), spec)
    with pytest.raises(ValueError):
        SLC.make("O_T2_F_ER", KN.lib_params("O_T2_F_ER"), dict(KN.variant_spec("O_T2_F_ER"), base="M2"))


def test_c4_base_is_the_c4_book_in_config_order_first_match():
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    book = [x for x in cfg["strategy_paper"]["extra"] if x.get("name") == "c4_candle_variations"][0]
    var = KN.make_variant("O_C4_F_ER")
    ids = [v.id for v in var.shared.base.vars]
    assert ids == list(book["rule_params"]["variations"]) == list(RG.C4_VARIATIONS)
    assert list(var.shared.base.rule_params.variations) == ids
    # ilk eşleşen = canlı candle_book.decide (kapısız saf yol) — iki varyasyonun aynı barda eştiği bir dünya
    data = S.c4_world(21, n=900)
    singles = {v: KN.make_variant(v) for v in SLC.C4_VARIATIONS}
    rows = data.bars["4h"]
    seen_multi = seen = 0
    for i in range(520, len(rows)):
        now = int(rows[i]["timestamp"]) + H4
        got = KN.signals_at(var.shared.base, data, now)
        hits = [v for v, s in singles.items() if KN.signals_at(s, data, now)]
        seen_multi += len(hits) > 1
        seen += bool(hits)
        if hits:
            first = KN.signals_at(singles[hits[0]], data, now)
            assert [(x.signal_ts, x.side, x.stop, dict(x.evidence)) for x in got] == \
                [(x.signal_ts, x.side, x.stop, dict(x.evidence)) for x in first]
        else:
            assert got == []
    assert seen >= 3, "dünya C4 sinyali üretmeli"


_COV = ("tradingbot/strategy_lib/contract.py", "tradingbot/strategy_lib/indicators.py", "tradingbot/strategy_lib/o_cover.py",
        "tradingbot/strategy_lib/stream.py", "tradingbot/strategy_lib/livebars.py", "tradingbot/paper_rules.py",
        "tradingbot/candle_confirmation.py", "tradingbot/timeframes.py")
_STRUCT = ("tradingbot/structures/analysis.py", "tradingbot/structures/bots.py", "tradingbot/structures/catalog.py",
           "tradingbot/structures/policy.py", "tradingbot/chart_patterns.py", "tradingbot/learn/candle_context.py",
           "tradingbot/learn/multitimeframe_context.py", "tradingbot/learn/weekly_structure.py", "tradingbot/core/ids.py")
#: bildirilmiş dosya kümeleri, elle sabitlenmiş (faktör modülü + çerçeve + taban kopyanın kümesi)
EXPECTED_FILES = {
    "O_T2_F_BTC": _COV + _STRUCT + ("tradingbot/strategy_lib/o_f_btc.py", "tradingbot/strategy_lib/k_trend.py",
                                    "tradingbot/ema200_trend.py", "tradingbot/regime_gate.py", "tradingbot/indicators.py"),
    "O_BOX_X_TRAIL2": _COV + ("tradingbot/strategy_lib/o_x_trail2.py", "tradingbot/strategy_lib/k_box.py",
                              "tradingbot/box_theory.py", "tradingbot/ema200_trend.py", "tradingbot/indicators.py"),
    "O_D4_F_ER": _COV + ("tradingbot/strategy_lib/o_f_er.py", "tradingbot/strategy_lib/k_d4.py",
                         "tradingbot/donchian_trend.py", "tradingbot/signal_lab.py", "tradingbot/box_theory.py"),
    "O_C4_X_TPL": _COV + ("tradingbot/strategy_lib/o_x_tpl.py", "tradingbot/strategy_lib/k_c4.py", "tradingbot/candle_book.py",
                          "tradingbot/candle_dsl.py", "tradingbot/candle_variations.py", "tradingbot/signal_lab.py"),
}


def test_cover_identity_files_are_factor_plus_base_and_spec_shas_are_distinct():
    for vid, files in EXPECTED_FILES.items():
        assert SLI.module_files(vid) == tuple(sorted(set(files))), vid
    for vid in COVERS:
        b = vid.split("_")[1]
        k = {"T2": "K_T2", "M2": "K_M2", "BOX": "K_BOX", "D4": "K_D4", "C4": "K_C4_CV001"}[b]
        fac = SLI.module_files(vid)
        assert set(SLI.module_files(k)) < set(fac), vid                    # taban kümesi ⊂ örtü kümesi
        assert f"tradingbot/strategy_lib/{SLC.COVER_FACTOR_MODULE[vid.split('_', 2)[2]]}.py" in fac
        others = [f"tradingbot/strategy_lib/{m}.py" for f, m in SLC.COVER_FACTOR_MODULE.items() if f != vid.split("_", 2)[2]]
        assert not set(others) & set(fac), vid                             # yalnız KENDİ faktörü
    assert SLI.module_files("O_T2_X_TIME") == SLI.module_files("O_M2_X_TIME")
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True).returncode != 0:
        pytest.skip("git yok")
    try:
        shas = {v: KN.spec_sha_for(v) for v in COVERS + ["K_T2", "K_M2", "K_BOX", "K_D4", "K_C4_CV001"]}
    except SLI.DirtyTreeError as exc:
        pytest.skip(f"çalışma ağacı kirli (kimlik bilerek hesaplanmaz): {exc}")
    assert len(set(shas.values())) == len(shas)
    for vid in COVERS:
        assert shas[vid] == RG.spec_sha(RG.lib_spec(vid, SLI.module_src_sha(vid)))


# ============================================================================ elle kurulmuş yollar
def mk(closes: list[float], tf: str, *, wick: float = 0.001, t0: int = S.T0) -> list[dict]:
    step = SL.TF_MS[tf]
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"timestamp": t0 + i * step, "open": prev, "high": max(prev, c) * (1 + wick),
                    "low": min(prev, c) * (1 - wick), "close": c, "volume": 1.0})
        prev = c
    return out


def _intent(side: str = "LONG", ts: int = 0, stop: float = 90.0, **kw: Any) -> SL.Intent:
    return SL.Intent(signal_ts=ts, side=side, entry_ref=SL.EntryRef(price=100.0), stop=stop, **kw)


def test_f_er_trend_bases_need_er_at_least_0_3_box_needs_below_0_3_on_1d():
    f = KN.make_variant("O_D4_F_ER").factor
    assert (f.tf, f.lo, f.n, f.box) == ("4h", 0.3, 20, False)
    straight = mk([100.0 + i for i in range(30)], "4h")
    zig = mk([100.0 + (1.0 if i % 2 else -1.0) for i in range(30)], "4h")
    it = _intent()
    assert f.value({"4h": straight}) == pytest.approx(1.0) and f.on_intent(it, {"4h": straight}, {}) is it
    assert f.value({"4h": zig}) < 0.3 and f.on_intent(it, {"4h": zig}, {}) is None
    assert f.on_intent(it, {"4h": straight[:20]}, {}) is None                     # < 21 kapanış → ölçülemez
    flat = mk([100.0] * 30, "4h")
    assert f.value({"4h": flat}) is None and f.on_intent(it, {"4h": flat}, {}) is None
    c = [r["close"] for r in zig[-21:]]
    assert f.value({"4h": zig}) == G.er(c, 20, 20)
    fb = KN.make_variant("O_BOX_F_ER").factor
    assert (fb.tf, fb.hi, fb.box) == ("1d", 0.3, True)
    d_straight, d_zig = mk([100.0 + i for i in range(30)], "1d"), mk([100.0 + (1.0 if i % 2 else -1.0) for i in range(30)], "1d")
    assert fb.on_intent(it, {"1d": d_straight, "5m": []}, {}) is None              # Box: trendde giriş yok
    assert fb.on_intent(it, {"1d": d_zig, "5m": []}, {}) is it
    # tam eşik: ER = 0,3 trend tabanında geçer (≥), Box'ta geçmez (<)
    g = KN.make_variant("O_T2_F_ER").factor
    g.value = lambda bars: 0.3                                                      # noqa: E731 — eşik davranışı
    fb.value = lambda bars: 0.3                                                     # noqa: E731
    assert g.on_intent(it, {}, {}) is it and fb.on_intent(it, {}, {}) is None


def _btc(closes: list[float]) -> list[dict]:
    return mk(closes, "4h")


def test_f_btc_uses_the_live_regime_definition_on_btc_4h_and_blocks_unknown():
    f = KN.make_variant("O_T2_F_BTC").factor
    cov = KN.make_variant("O_T2_F_BTC")
    assert cov.needs_btc_4h and cov.btc_4h_lookback == 699 == LB.LIVE_CLOSED_BARS["4h"]
    up = _btc([100.0 * 1.001 ** i for i in range(300)])
    down = _btc([100.0 * 0.999 ** i for i in range(300)])
    lng, sht = _intent("LONG"), _intent("SHORT", stop=110.0)
    assert f.regime({"btc_4h_bars": up}) == regime_gate.UP and f.regime({"btc_4h_bars": down}) == regime_gate.DOWN
    assert f.on_intent(lng, {}, {"btc_4h_bars": up}) is lng and f.on_intent(sht, {}, {"btc_4h_bars": up}) is None
    assert f.on_intent(sht, {}, {"btc_4h_bars": down}) is sht and f.on_intent(lng, {}, {"btc_4h_bars": down}) is None
    assert f.regime({"btc_4h_bars": up[:199]}) is None and f.on_intent(lng, {}, {"btc_4h_bars": up[:199]}) is None
    assert f.regime({"btc_4h_bars": up[:200]}) == regime_gate.UP
    assert f.on_intent(lng, {}, {}) is None                                         # veri yok → giriş yok
    # pencere 699 bar: daha uzun seride EMA tohumu son 699 barın başıdır (canlı çerçeve), ema200 sütunu okunmaz
    rnd = random.Random(3)
    long_ = _btc([100.0 + rnd.gauss(0, 2) for _ in range(900)])
    for r in long_:
        r["ema200"] = 1e9                                                           # varsa bile okunmaz (okunuş)
    closes = [r["close"] for r in long_[-699:]]
    e = regime_gate.ema_last(closes, 200)
    assert f.regime({"btc_4h_bars": long_}) == (regime_gate.UP if closes[-1] > e else regime_gate.DOWN)


def test_f_vol_is_the_atr_pct_rank_among_the_last_120_including_the_current():
    f = KN.make_variant("O_D4_F_VOL").factor
    assert (f.look, f.cap, f.tf, f.n_atr) == (120, 0.8, "4h", 14)
    rnd = random.Random(5)
    closes = [100.0]
    for _ in range(200):
        closes.append(closes[-1] * (1 + rnd.gauss(0, 0.01)))
    calm = mk(closes, "4h", wick=0.002)
    spike = calm[:-1] + [dict(calm[-1], high=calm[-1]["close"] * 1.08, low=calm[-1]["close"] * 0.92)]
    it = _intent()
    for rows in (calm, spike):
        h, lo, c = [r["high"] for r in rows], [r["low"] for r in rows], [r["close"] for r in rows]
        a = G.atr(h, lo, c, 14)
        pct = [a[k] / c[k] * 100.0 for k in range(len(c) - 120, len(c))]
        assert f.rank({"4h": rows}) == G.pct_rank(pct, pct[-1])
    assert f.rank({"4h": spike}) > 0.8 and f.on_intent(it, {"4h": spike}, {}) is None
    calm_last = calm[:-1] + [dict(calm[-1], high=calm[-1]["close"] * 1.0001, low=calm[-1]["close"] * 0.9999)]
    assert f.rank({"4h": calm_last}) <= 0.8 and f.on_intent(it, {"4h": calm_last}, {}) is it
    assert f.on_intent(it, {"4h": calm[:119]}, {}) is None                         # < 120 değer → ölçülemez
    assert KN.make_variant("O_BOX_F_VOL").factor.tf == "1d"


class _Stub(SL.Variant):
    """Örtü çerçevesi testleri için taban: `signals`/`manage` sabit cevap verir."""

    family = "K"

    def __init__(self, tf: str, timeframes: tuple, act: SL.Action = SL.NONE, its: list | None = None):
        self.variant_id, self.signal_tf, self.timeframes = "STUB", tf, timeframes
        self.lookback = {t: 400 for t in timeframes}
        self.act, self.its, self.calls = act, list(its or []), Counter()

    def signal_key(self, ctx):
        return None

    def manage_key(self, pos, ctx):
        return None

    def signals(self, bars, ctx):
        self.calls["signals"] += 1
        return list(self.its)

    def manage(self, pos, bars, ctx):
        self.calls["manage"] += 1
        return self.act


def _cover(vid: str, base: SL.Variant) -> OC.Cover:
    mod = __import__(SLC.module_name(vid), fromlist=["make"])
    return mod.make(vid, KN.lib_params(vid), KN.variant_spec(vid), base)


def _pos(side: str, entry: float, stop: float, entry_ts: int, *, initial_stop: float | None = None) -> SL.PosView:
    it = _intent(side, entry_ts, initial_stop or stop)
    return SL.PosView(side=side, entry=entry, stop=stop, initial_stop=initial_stop or stop, opened_ms=entry_ts,
                      signal_ts=entry_ts - H4, entry_bar_ts=entry_ts, bars_held=1, intent=it)


def test_x_trail2_arms_after_one_r_trails_two_atr_only_tightens_and_closes_when_breached():
    cov = _cover("O_D4_X_TRAIL2", _Stub("4h", ("4h",)))
    rows = mk([100.0 + 0.2 * (i % 3) for i in range(60)], "4h", wick=0.01)
    t_e = int(rows[40]["timestamp"])
    h, lo, c = [r["high"] for r in rows], [r["low"] for r in rows], [r["close"] for r in rows]
    a = G.atr(h, lo, c, 14)[-1]
    best = max(r["high"] for r in rows[40:])
    # silahlanmadı: en iyi uç < giriş + 1R
    p = _pos("LONG", entry=100.0, stop=best - 50.0, entry_ts=t_e, initial_stop=100.0 - (best - 100.0) * 1.01)
    assert cov.manage(p, {"4h": rows}, {"now_ms": 0}) is SL.NONE
    # silahlı: aday = en iyi uç − 2 × ATR14; sıkılaştırıyorsa MOVE_STOP
    p = _pos("LONG", entry=100.0, stop=90.0, entry_ts=t_e, initial_stop=99.0)
    act = cov.manage(p, {"4h": rows}, {"now_ms": 0})
    assert act.kind == SL.ACT_MOVE_STOP and act.price == pytest.approx(best - 2.0 * a) and act.price < c[-1]
    assert cov.manage(replace(p, stop=act.price + 1e-6), {"4h": rows}, {"now_ms": 0}) is SL.NONE    # gevşetmez
    # delinmiş: aday karar kapanışının ötesinde → CLOSE (sonraki açılış)
    crash = rows[:-1] + [dict(rows[-1], close=best - 3.0 * a, low=best - 3.2 * a)]
    act = cov.manage(p, {"4h": crash}, {"now_ms": 0})
    assert act.kind == SL.ACT_CLOSE and act.reason == "X_TRAIL2_BREACHED"
    # SHORT simetrik
    sp = _pos("SHORT", entry=101.0, stop=110.0, entry_ts=t_e, initial_stop=101.5)
    act = cov.manage(sp, {"4h": rows}, {"now_ms": 0})
    worst = min(r["low"] for r in rows[40:])
    assert act.kind == SL.ACT_MOVE_STOP and act.price == pytest.approx(worst + 2.0 * a)
    # tabanın CLOSE'u önceliklidir (örtü hiç hesaplanmaz) ve tabanın işaretleri korunur
    cb = _cover("O_D4_X_TRAIL2", _Stub("4h", ("4h",), act=SL.close("BASE")))
    assert cb.manage(p, {"4h": rows}, {"now_ms": 0}).reason == "BASE"
    fl = _cover("O_D4_X_TRAIL2", _Stub("4h", ("4h",), act=SL.hold("F8_UNMEASURABLE")))
    got = fl.manage(p, {"4h": rows}, {"now_ms": 0})
    assert got.kind == SL.ACT_MOVE_STOP and got.flags == ("F8_UNMEASURABLE",)


def test_x_chan3_hangs_three_atr22_from_the_best_extreme_since_entry():
    cov = _cover("O_D4_X_CHAN3", _Stub("4h", ("4h",)))
    rnd = random.Random(9)
    closes = [100.0]
    for _ in range(80):
        closes.append(closes[-1] * (1 + rnd.gauss(0.002, 0.01)))
    rows = mk(closes, "4h", wick=0.004)
    t_e = int(rows[60]["timestamp"])
    h, lo, c = [r["high"] for r in rows], [r["low"] for r in rows], [r["close"] for r in rows]
    a22 = G.atr(h, lo, c, 22)[-1]
    best = max(h[60:])                                         # girişten bu yana (önceki tepeler sayılmaz)
    p = _pos("LONG", entry=c[60], stop=c[60] * 0.5, entry_ts=t_e)
    act = cov.manage(p, {"4h": rows}, {"now_ms": 0})
    assert cov.factor.candidate(p, rows) == pytest.approx(best - 3.0 * a22)
    if best - 3.0 * a22 < c[-1]:
        assert act.kind == SL.ACT_MOVE_STOP and act.price == pytest.approx(best - 3.0 * a22)
    pre = list(rows)
    pre[30] = dict(pre[30], high=10 * best)                    # giriş öncesi uç "en iyi uca" girmez (LeBeau: girişten beri)
    assert OC.best_since_entry(p, pre) == best and max(r["high"] for r in pre) == 10 * best


def test_x_time_is_half_the_base_max_hold_and_box_48_five_minute_bars():
    want = {"T2": 150, "M2": 150, "D4": 150, "C4": 12, "BOX": 48}
    for b, n in want.items():
        cov = KN.make_variant(f"O_{b}_X_TIME")
        assert cov.factor.bars == n, b
        assert cov.factor.on_intent(_intent(), {}, {}).max_hold_bars == n
        assert cov.factor.on_intent(_intent(max_hold_bars=5), {}, {}).max_hold_bars == 5     # daha kısa taban kalır
        assert cov.factor.on_intent(_intent(max_hold_bars=300), {}, {}).max_hold_bars == n
    mh = RG.LIB_V1["copy_books"]
    assert mh["T2"]["max_hold"]["x_time_bars"] == mh["M2"]["max_hold"]["x_time_bars"] == 150
    assert mh["D4"]["max_hold"]["bars"] // 2 == 150 and mh["C4"]["max_hold"]["bars"] // 2 == 12
    assert RG.LIB_V1["covers"]["factors"]["X_TIME"]["box_bars_5m"] == 48


def test_x_tpl_thirds_from_the_real_entry_with_breakeven_after_the_first_target():
    cov = KN.make_variant("O_D4_X_TPL")
    it = cov.factor.on_intent(_intent(targets=(SL.Target(SL.TARGET_PRICE, 120.0),)), {}, {})
    assert [(t.kind, t.value, t.frac) for t in it.targets] == [(SL.TARGET_R_FROM_ENTRY, 1.0, pytest.approx(1 / 3)),
                                                                (SL.TARGET_R_FROM_ENTRY, 2.0, pytest.approx(1 / 3)),
                                                                (SL.TARGET_R_FROM_ENTRY, 3.0, None)]
    with pytest.raises(ValueError):
        SL.Target(SL.TARGET_R_FROM_ENTRY, 1.0, 1.0)
    res = KN.run(cov, S.d4_world(22, n=1400))
    full = [t for t in res.trades if t.exit_reason == "TARGET3"]
    be = [t for t in res.trades if any(lg.reason == "BREAKEVEN_STOP" for lg in t.legs)]
    assert full and be, "dünya üç hedefi alan ve TP1 sonrası başa-başta çıkan işlem üretmeli"
    for t in full:
        assert [lg.reason for lg in t.legs] == ["TARGET1", "TARGET2", "TARGET3"]
        assert [lg.frac for lg in t.legs] == pytest.approx([1 / 3, 1 / 3, 1 / 3])
    for t in be:
        assert t.legs[0].reason == "TARGET1" and t.legs[0].frac == pytest.approx(1 / 3)
        assert sum(lg.frac for lg in t.legs) == pytest.approx(1.0)
        fill = t.entry_ref * (1.0 + KN.scenario("base").slip)
        bel = [lg for lg in t.legs if lg.reason == "BREAKEVEN_STOP"][0]
        assert bel.ref >= KN.break_even(True, fill, KN.scenario("base").fee, KN.scenario("base").slip) - 1e-9 or \
            bel.point in ("open", "close")
    for t in res.trades:                                       # hedefler GERÇEK girişten (açılış) R katları
        r = abs(t.entry_ref - t.initial_stop)
        assert t.targets == pytest.approx(tuple(t.entry_ref + k * r for k in (1, 2, 3)))


# ============================================================================ geleceği görmeme
def _cut(data: KN.SeriesData, now: int) -> KN.SeriesData:
    """Karar anında kapanmış barlar dışındaki her şey silinir (bütün diziler)."""
    def keep(rows, tf):
        return [r for r in rows if int(r["timestamp"]) + SL.TF_MS[tf] <= now]
    return KN.SeriesData(symbol=data.symbol, bars={tf: keep(rows, tf) for tf, rows in data.bars.items()},
                         btc_1d=keep(data.btc_1d or [], "1d") or None, funding=data.funding, price_tick=data.price_tick,
                         btc_4h=keep(data.btc_4h or [], "4h") or None)


@pytest.mark.parametrize("base", BASES)
def test_cover_signals_use_only_data_closed_at_the_decision(base):
    data = WORLD[base]()
    stf = KN.make_variant(f"O_{base}_F_ER").signal_tf
    rows = data.bars[stf]
    k = KN.make_variant(f"O_{base}_X_TIME").shared.base
    emit = []
    for r in rows[len(rows) // 3:]:
        t = int(r["timestamp"]) + SL.TF_MS[stf]
        if KN.signals_at(k, data, t):
            emit.append(t)
            if len(emit) == 6:
                break
    assert emit, f"{base}: taban dünyada sinyal üretmeli"
    for vid in KN.covers_of(base):
        var = KN.make_variant(vid)
        for t in emit:
            a = [(x.signal_ts, x.side, x.stop, x.targets, x.max_hold_bars) for x in KN.signals_at(var, data, t)]
            b = [(x.signal_ts, x.side, x.stop, x.targets, x.max_hold_bars) for x in KN.signals_at(var, _cut(data, t), t)]
            assert a == b, (vid, t)


# ============================================================================ (a) paylaşılan akış — bayt-aynı
@pytest.mark.parametrize("base", BASES)
def test_shared_stream_family_runs_equal_separate_runs_byte_for_byte(base):
    data = WORLD[base]()
    shared, fam = KN.make_family(base)
    if base != "C4":
        k_vid = {"T2": "K_T2", "M2": "K_M2", "BOX": "K_BOX", "D4": "K_D4"}[base]
        k_shared = KN.run(shared, data)
        assert _dump(k_shared) == _dump(KN.run(KN.make_variant(k_vid), data))
    got = {vid: _dump(KN.run(v, data)) for vid, v in fam.items()}
    st = dict(shared.stats)
    assert st["signals_hit"] > st["signals_miss"] > 0, st                       # gerçekten paylaşıldı
    for vid in fam:
        assert got[vid] == _dump(KN.run(KN.make_variant(vid), data)), vid
    # aynı akış başka bir seri nesnesine bağlanınca sıfırlanır (aynı sembol adı da olsa)
    other = WORLD[base]()
    assert other is not data
    again = {vid: _dump(KN.run(v, other)) for vid, v in fam.items()}
    assert again == got


@pytest.mark.parametrize("base", ["M2", "BOX", "C4"])
def test_lockstep_family_run_equals_sequential_runs_byte_for_byte(base):
    """`kernel.run_lockstep`: ailenin koşuları bar bar sırayla (aynı çağrılar) → her sonuç ayrı `run`'ınkiyle bayt-aynı;
    T2/M2'de canlı yapı analizinin içerik adresli önbelleği aynı bar için sıcaktır (analiz bar başına bir kez)."""
    from tradingbot.structures import analysis as SA
    data = WORLD[base]()
    SA.clear_cache()
    shared, fam = KN.make_family(base)
    runners = ([shared] if base != "C4" else []) + list(fam.values())
    lock = [_dump(r) for r in KN.run_lockstep(runners, data)]
    SA.clear_cache()
    shared2, fam2 = KN.make_family(base)
    seq = [_dump(KN.run(v, data)) for v in ([shared2] if base != "C4" else []) + list(fam2.values())]
    assert lock == seq
    mixed = [KN.make_variant("O_D4_F_ER"), KN.make_variant("K_D4")]
    d4 = S.d4_world(22, n=500)
    assert [_dump(r) for r in KN.run_lockstep(mixed, d4)] == [_dump(KN.run(v, d4)) for v in
                                                             (KN.make_variant("O_D4_F_ER"), KN.make_variant("K_D4"))]
    assert KN.run_lockstep([], data) == []


@pytest.mark.parametrize("vid", ["O_T2_F_BTC", "O_M2_X_CHAN3", "O_BOX_X_TPL", "O_BOX_F_BTC", "O_D4_X_TRAIL2", "O_C4_F_VOL"])
def test_cover_fast_path_equals_the_straightforward_path(vid):
    """Örtünün hızlı yolu (paylaşılan akış + hızlı çerçeve + ön-süzgeç) = düz yol (`fast=False`: pandas çerçevesi,
    süzgeçsiz, paylaşımsız)."""
    base = vid.split("_")[1]
    data = S.box_world(22, days=3) if base == "BOX" else {"T2": lambda: S.trend_world(23, n=300),
                                                        "M2": lambda: S.trend_world(24, n=300)}.get(base, WORLD[base])()
    shared, fam = KN.make_family(base)
    if base != "C4":
        KN.run(shared, data)
    fast = _dump(KN.run(fam[vid], data))
    slow = KN.make_variant(vid)
    slow.fast = False
    assert slow.shared.base.fast is False
    assert _dump(KN.run(slow, data)) == fast


def test_stream_unbound_does_not_memoize_and_keys_follow_the_history_content():
    base = _Stub("1d", ("1d",), its=[_intent()])
    sh = ST.SharedBase(base)
    ctx = {"now_ms": D1}
    sh.signals({}, ctx)
    sh.signals({}, ctx)
    assert base.calls["signals"] == 2 and sum(sh.stats.values()) == 0           # bağlanmamış: düz çağrı
    sh.bind_series(object())
    sh.signals({}, ctx)
    sh.signals({}, ctx)
    assert base.calls["signals"] == 3 and sh.stats["signals_hit"] == 1
    sh.bind_series(object())                                                       # başka seri → sıfırlanır
    sh.signals({}, ctx)
    assert base.calls["signals"] == 4
    with pytest.raises(ValueError):
        ST.SharedBase(sh)
    from tradingbot.strategy_lib import k_trend
    e1 = SL.BookEntry("X", "LONG", 1, 2, {"structure": {"pattern_id": "a", "x": [1, 2.0]}})
    e2 = SL.BookEntry("X", "LONG", 1, 2, {"structure": {"x": [1, 2.0], "pattern_id": "a"}})
    e3 = SL.BookEntry("X", "LONG", 1, 2, {"structure": {"pattern_id": "b", "x": [1, 2.0]}})
    assert k_trend.history_key([e1]) == k_trend.history_key([e2]) != k_trend.history_key([e3])
    assert k_trend.history_key([SL.BookEntry("X", "LONG", 1, 2, {})]) == ()
    assert SL.canon(1) != SL.canon(1.0) != SL.canon(True)


# ============================================================================ (b) Box ön-süzgeci
_BOX_PARAMS = ["K_BOX"] + [v for v in SLC.IMPLEMENTED if SLC.FAMILY_OF[v] == "F2"]


def _edge_box_world(seed: int, days: int) -> KN.SeriesData:
    """Uç durumlar: eksik günlük bar (kutu yok), eksik 5m dilimi, tetik eşitlikleri (kapanış = önceki düşük/tepe,
    kapanış = açılış), kutu kenarına tam değen uç, iki kenara değen geniş mum, kutunun çok dışında boşluk."""
    d = S.box_world(seed, days=days)
    m5 = [dict(r) for r in d.bars["5m"]]
    rnd = random.Random(seed)
    d1 = S.aggregate(m5, D1)
    byday = {int(r["timestamp"]): r for r in d1}
    for i in range(3, len(m5)):
        u = rnd.random()
        cur, prev = m5[i], m5[i - 1]
        if u < 0.02:
            cur["close"] = prev["low"]
            cur["low"], cur["high"] = min(cur["low"], cur["close"]), max(cur["high"], cur["close"])
        elif u < 0.04:
            cur["close"] = prev["high"]
            cur["low"], cur["high"] = min(cur["low"], cur["close"]), max(cur["high"], cur["close"])
        elif u < 0.05:
            cur["close"] = cur["open"]
        elif u < 0.07:
            day0 = int(cur["timestamp"]) // D1 * D1
            box = byday.get(day0 - D1)
            if box:
                band = (box["high"] - box["low"]) * 0.1
                lvl = box["high"] - band if rnd.random() < 0.5 else box["low"] + band
                if lvl >= cur["low"]:
                    cur["high"] = max(cur["high"], lvl, cur["open"], cur["close"])
                    cur["high"] = lvl if lvl >= max(cur["open"], cur["close"]) else cur["high"]
        elif u < 0.075:
            day0 = int(cur["timestamp"]) // D1 * D1
            box = byday.get(day0 - D1)
            if box:
                cur["high"], cur["low"] = max(cur["high"], box["high"]), min(cur["low"], box["low"])
    drop_day = int(m5[0]["timestamp"]) // D1 * D1 + 3 * D1
    m5 = [r for k, r in enumerate(m5) if not (drop_day + 2 * 3_600_000 <= int(r["timestamp"]) < drop_day + 3 * 3_600_000)]
    d1 = [r for r in S.aggregate([dict(r) for r in d.bars["5m"]], D1) if int(r["timestamp"]) != drop_day + D1]
    return KN.SeriesData(symbol=d.symbol, bars={"1d": d1, "5m": m5}, funding=d.funding, price_tick=d.price_tick,
                         btc_4h=d.btc_4h)


def _box_rule(var, d1_rows, m5_rows, now, position=None):
    return paper_rules.decide_from_rows("b1_box_fade", daily=d1_rows, intraday=m5_rows, btc_rows=None, position=position,
                                        params=var.rule_params, now_ms=now)


@pytest.mark.parametrize("world", ["s41", "s42", "edge"])
def test_box_prefilter_skips_only_bars_where_the_live_rule_cannot_emit(world):
    """Her 5m karar barında, sekiz Box parametre kümesinde (K_BOX + yedi F2): ön-süzgeç atladıysa canlı kural (aynı
    satırlarla — hızlı çerçevenin eşitliği ayrıca testli) OPEN üretmez; atlamadıysa kopyanın niyeti kuralınkidir. Açık
    pozisyon: süzgeç atladıysa kural CLOSE üretmez."""
    data = _edge_box_world(43, 14) if world == "edge" else S.box_world(int(world[1:]), days=14)
    vars_ = [KN.make_variant(v) for v in _BOX_PARAMS]
    m5, d1 = data.bars["5m"], data.bars["1d"]
    sim = KN._Sim(vars_[0], data)
    day_rows: dict = {}
    skipped = called = emitted = 0
    for i in range(2, len(m5)):
        now = int(m5[i]["timestamp"]) + M5
        bars = sim.bars_for(now)
        dkey = int(bars["1d"][-1]["timestamp"]) if bars["1d"] else None
        if dkey not in day_rows:
            day_rows[dkey] = LB.daily_rows(bars["1d"], now, fast=True)
        m5_rows = LB.intraday_rows(bars["5m"], "5m", now, fast=True)
        for var in vars_:
            act = _box_rule(var, day_rows[dkey], m5_rows, now)
            opened = bool(act) and str(act.get("action")).upper() == "OPEN"
            if var.cannot_open(bars, now):
                skipped += 1
                assert not opened, (var.variant_id, i)
            else:
                called += 1
                emitted += opened
                its = var.signals(bars, {"now_ms": now})
                assert [(x.signal_ts, x.side, x.stop) for x in its] == \
                    ([(int(act["signal_ts"]), act["direction"], float(act["stop"]))] if opened else [])
        if i % 37 == 0:                                       # açık pozisyon: dün / bugün / 23:55 sinyalli
            for back in (1, 60, 288, 400):
                if i - back < 0:
                    continue
                sig = int(m5[i - back]["timestamp"])
                p = SL.PosView("LONG", 100.0, 90.0, 90.0, sig + M5, sig, sig + M5, back, _intent())
                pos = {"opened_at": sig + M5, "features": {"data_source": {"bars": {"5m": sig}}}}
                act = _box_rule(vars_[0], day_rows[dkey], m5_rows, now, pos)
                closes = bool(act) and str(act.get("action")).upper() == "CLOSE"
                if vars_[0].cannot_close(p, bars, now):
                    assert not closes, (i, back)
    assert emitted >= 1 and skipped > 4 * called, (skipped, called, emitted)


def test_box_prefilter_defers_on_unclosed_or_unordered_bars_and_reads_the_previous_day_box():
    var = KN.make_variant("K_BOX")
    d = S.box_world(21, days=3)
    m5 = d.bars["5m"]
    i = 400
    now = int(m5[i]["timestamp"]) + M5
    bars = {"1d": [r for r in d.bars["1d"] if int(r["timestamp"]) + D1 <= now], "5m": m5[max(0, i - 498):i + 1]}
    assert var.cannot_open(dict(bars, **{"5m": bars["5m"] + [m5[i + 1]]}), now) is False   # kapanmamış bar → kural
    swapped = bars["5m"][:-2] + [bars["5m"][-1], bars["5m"][-2]]
    assert var.cannot_open(dict(bars, **{"5m": swapped}), now) is False
    assert var.cannot_open(dict(bars, **{"5m": bars["5m"][:2]}), now) is True             # < 3 bar
    assert var.cannot_open(dict(bars, **{"1d": []}), now) is True or not KN.signals_at(var, d, now)
    last_of_day = [k for k, r in enumerate(m5) if (int(r["timestamp"]) + M5) % D1 == 0][1]
    now2 = int(m5[last_of_day]["timestamp"]) + M5                                        # 23:55 barı: gün bitti
    b2 = {"1d": [r for r in d.bars["1d"] if int(r["timestamp"]) + D1 <= now2], "5m": m5[last_of_day - 498:last_of_day + 1]}
    assert var.cannot_open(b2, now2) is True


@pytest.mark.parametrize("vid, seed, days", [("K_BOX", 21, 4), ("BOX_n20_2r_ms1.0", 22, 4), ("BOX_n10_2r_ms0.5", 23, 3)])
def test_box_kernel_run_with_the_prefilter_equals_the_straightforward_path(vid, seed, days):
    data = S.box_world(seed, days=days)
    fast = KN.make_variant(vid)
    slow = KN.make_variant(vid)
    slow.fast = False
    a, b = KN.run(fast, data), KN.run(slow, data)
    assert a.trades and _dump(a) == _dump(b)


def test_box_kernel_run_on_the_edge_world_equals_the_straightforward_path():
    data = _edge_box_world(44, 5)
    for vid in ("K_BOX", "BOX_n20_mid_ms0.5"):
        fast, slow = KN.make_variant(vid), KN.make_variant(vid)
        slow.fast = False
        assert _dump(KN.run(fast, data)) == _dump(KN.run(slow, data)), vid


# ============================================================================ D4 ön-süzgeci
def test_d4_prefilter_skips_only_bars_where_the_live_rule_cannot_emit():
    var = KN.make_variant("K_D4")
    data = S.d4_world(31, n=760)
    rows = data.bars["4h"]
    sim = KN._Sim(var, data)
    skipped = called = 0
    for i in range(60, len(rows)):
        now = int(rows[i]["timestamp"]) + H4
        bars = sim.bars_for(now)
        rr = LB.intraday_rows(bars["4h"], "4h", now, fast=True)
        act = paper_rules.decide_from_rows("d4_donchian_20_10", daily=[], intraday=rr, btc_rows=None, position=None,
                                           params=var.rule_params, now_ms=now)
        opened = bool(act) and act.get("action") == "OPEN"
        if var.cannot_open(bars, now):
            skipped += 1
            assert not opened, i
        else:
            called += 1
        if i % 9 == 0:
            for back in (0, 3, 40, 299, 320):
                opened_ms = int(rows[max(0, i - back)]["timestamp"])
                p = SL.PosView("LONG", 100.0, 90.0, 90.0, opened_ms, opened_ms - H4, opened_ms, back + 1, _intent())
                act = paper_rules.decide_from_rows("d4_donchian_20_10", daily=[], intraday=rr, btc_rows=None,
                                                   position={"opened_at": opened_ms}, params=var.rule_params, now_ms=now)
                if var.cannot_close(p, bars, now):
                    assert not (act and act.get("action") == "CLOSE"), (i, back)
    assert skipped > 5 * called, (skipped, called)


def test_d4_kernel_run_with_the_prefilter_equals_the_straightforward_path():
    data = S.d4_world(32, n=650)
    fast, slow = KN.make_variant("K_D4"), KN.make_variant("K_D4")
    slow.fast = False
    a, b = KN.run(fast, data), KN.run(slow, data)
    assert a.trades and _dump(a) == _dump(b)


# ============================================================================ (c) hızlı çerçeve
def _canon_rows(rows: list[dict]) -> str:
    return json.dumps([[(k, type(v).__name__, repr(v)) for k, v in r.items()] for r in rows])


def _rand_bars(n: int, tf: str, seed: int) -> list[dict]:
    rnd = random.Random(seed)
    rows = S.walk(n, seed=seed, step=SL.TF_MS[tf], p0=50.0, vol=0.02)
    for r in rows:
        u = rnd.random()
        if u < 0.05:
            r["volume"] = None
        elif u < 0.08:
            del r["volume"]
        elif u < 0.1:
            r["timestamp"] = float(r["timestamp"])
            r["open"] = int(round(r["open"])) or 1
            r["high"] = max(r["high"], r["open"])
            r["low"] = min(r["low"], r["open"])
    return rows


@pytest.mark.parametrize("tf, n", [("1d", 1), ("1d", 5), ("1d", 199), ("1d", 200), ("1d", 450), ("4h", 60), ("4h", 760),
                                   ("5m", 3), ("5m", 650)])
def test_fast_frame_equals_the_pandas_frame_for_every_live_reader(tf, n):
    bars = _rand_bars(n, tf, seed=n * 7 + len(tf))
    now = int(bars[-1]["timestamp"]) + SL.TF_MS[tf]
    for di in (False, True):
        pf, ff = LB.frame(bars, tf, daily_indicators=di), LB.frame(bars, tf, daily_indicators=di, fast=True)
        assert list(pf.columns) == ff.columns and len(pf) == len(ff)
        for t in (0, 1, 5, 260, 320, 400, 502, 10_000, -1, -3):
            for cols in (list(pf.columns), ["timestamp", "close"], ["close", "timestamp", "high"]):
                assert _canon_rows(pf.tail(t)[cols].to_dict("records")) == _canon_rows(ff.tail(t)[cols].to_dict("records"))
        with pytest.raises(KeyError):
            ff[["nope"]]
        with pytest.raises(ValueError):
            ff.to_dict()
    for fn, args in ((LB.daily_rows, (bars, now)), (LB.btc_daily_rows, (bars, now)), (LB.intraday_rows, (bars, tf, now)),
                     (LB.candle_rows, (bars, now))):
        assert _canon_rows(fn(*args)) == _canon_rows(fn(*args, fast=True)), fn.__name__
    for reader in (ema200_trend.daily_rows_from_frame, box_theory.rows_from_frame, candle_book.rows_from_frame,
                   regime_gate.rows_for_regime):
        pf, ff = LB.frame(bars, tf, daily_indicators=True), LB.frame(bars, tf, daily_indicators=True, fast=True)
        assert _canon_rows(reader(pf)) == _canon_rows(reader(ff)), reader.__name__
    assert LB.frame([], tf, fast=True) is None and LB.daily_rows([], now, fast=True) == LB.daily_rows([], now) == []


def _same_window(a, b) -> bool:
    import numpy as np
    (wa, ra), (wb, rb) = a, b
    if ra != rb or (wa is None) != (wb is None):
        return False
    return wa is None or (wa.step_ms == wb.step_ms and all(
        np.array_equal(getattr(wa, k), getattr(wb, k)) and getattr(wa, k).dtype == getattr(wb, k).dtype
        for k in ("ts", "o", "h", "l", "c", "v")))


def test_fast_candle_window_equals_window_from_rows_including_every_refusal():
    """C4'ün penceresi: `livebars.candle_window(fast=True)` = `candle_dsl.window_from_rows(candle_rows(…))` — diziler
    (değer ve tür) ve ret nedeni; kısa seri, kapanmamış son bar, eksik bar (bitişik değil), NaN/∞ OHLC, h < max(o, c),
    hacim yok / negatif / NaN."""
    d = S.c4_world(27, n=760)
    b = d.bars["4h"]
    n_ok = 0
    for i in range(1, len(b), 5):
        now = int(b[i - 1]["timestamp"]) + H4
        for w in (b[:i], b[:i] + [b[i]], b[max(0, i - 520):i]):
            live = LB.candle_window(w, now)
            assert _same_window(live, LB.candle_window(w, now, fast=True)), i
            n_ok += live[0] is not None
    assert n_ok > 50
    now = int(b[-1]["timestamp"]) + H4
    base = [dict(r) for r in b]
    cases = {"gap": base[:600] + base[601:], "nan": base[:-3] + [dict(base[-3], close=float("nan"))] + base[-2:],
             "inf": base[:-3] + [dict(base[-3], high=float("inf"))] + base[-2:],
             "hl": base[:-3] + [dict(base[-3], high=min(base[-3]["open"], base[-3]["close"]) * 0.99)] + base[-2:],
             "vol_none": base[:-3] + [dict(base[-3], volume=None)] + base[-2:],
             "vol_neg": base[:-3] + [dict(base[-3], volume=-1.0)] + base[-2:],
             "vol_nan": base[:-3] + [dict(base[-3], volume=float("nan"))] + base[-2:], "short": base[-499:]}
    whys = {}
    for name, rows in cases.items():
        live = LB.candle_window(rows, now)
        assert _same_window(live, LB.candle_window(rows, now, fast=True)), name
        whys[name] = live[1]
    assert whys["gap"] == "ROWS_NOT_CONTIGUOUS" and whys["nan"] == whys["inf"] == whys["hl"] == "ROWS_UNREADABLE"
    assert whys["vol_neg"] == whys["vol_nan"] == "VOLUME_MISSING" and whys["short"] == "NOT_ENOUGH_4H_BARS"
    assert whys["vol_none"] is None                            # canlı çerçeve hacmi `or 0.0` ile okur
    assert _same_window(LB.candle_window([], now), LB.candle_window([], now, fast=True))


@pytest.mark.parametrize("vid, world", [("K_T2", lambda: S.trend_world(25, n=330)), ("K_M2", lambda: S.trend_world(26, n=330)),
                                        ("K_C4_CV005", lambda: S.c4_world(25, n=620)), ("K_FM", lambda: S.fm_world(25, n=420))])
def test_k_copies_fast_frame_runs_equal_the_pandas_runs(vid, world):
    data = world()
    fast, slow = KN.make_variant(vid), KN.make_variant(vid)
    if hasattr(slow, "fast"):
        slow.fast = False
    assert _dump(KN.run(fast, data)) == _dump(KN.run(slow, data))


# ============================================================================ kapı dışı işaretler
def test_gate_exclusion_flags_are_sealed_and_the_helper_applies_them():
    from tradingbot.strategy_lib import h_funding
    ge = RG.WF_PROTOCOL_V1["gate_exclusions"]
    assert tuple(ge["flags"]) == RG.GATE_EXCLUDED_FLAGS == KN.GATE_EXCLUDED_FLAGS == (
        "FUNDING_GAP", "PATH_1H_GAP", "F8_UNMEASURABLE")
    assert (KN.FLAG_FUNDING_GAP, KN.FLAG_PATH_GAP, KN.FLAG_F8_UNMEASURABLE) == tuple(ge["flags"])
    assert h_funding.FLAG_F8_UNMEASURABLE == KN.FLAG_F8_UNMEASURABLE
    assert ge["decision"].startswith("P3b-2b") and "F8_UNMEASURABLE" in ge["reading_tr"]
    assert RG.THRESHOLD_DIRECTIONS_V1["WF_PROTOCOL"]["gate_exclusions.flags"] == RG.FIXED
    for f in ge["flags"]:
        t = KN.KernelTrade(symbol="X", variant_id="V", side="LONG", signal_ts=0, entry_ts=1, entry_ref=1.0, initial_stop=0.9,
                           targets=(), leverage=1, qty=1.0, notional=1.0, margin=1.0, liq_price=0.1, risk_usdt=0.1,
                           flags=["OPEN_AT_END", f])
        assert KN.gate_excluded(t) and KN.gate_excluded(t.to_dict())
        assert KN.gate_excluded(PA.RefTrade("X", "LONG", 0, 0.0, "x", "", "", 0.0, flags=(f,)))
    for f in ("OPEN_AT_END", "MOVE_STOP_IGNORED", "FUNDING_EXIT_BAR", "STRUCTURE", ""):
        assert not KN.gate_excluded({"flags": [f]})
    assert not KN.gate_excluded({"flags": []}) and not KN.gate_excluded({}) and not KN.gate_excluded(object())
    # asla gevşetme: listeden bir işaret çıkarılırsa da eklenirse de yakalanır (`fixed`)
    v2 = RG.thaw(RG.WF_PROTOCOL_V1)
    v2["gate_exclusions"]["flags"] = ["FUNDING_GAP", "PATH_1H_GAP"]
    assert RG.looser_than(RG.WF_PROTOCOL_V1, RG.freeze(v2), RG.THRESHOLD_DIRECTIONS_V1["WF_PROTOCOL"])
    # gerçek koşuda: H dünyasında F8_UNMEASURABLE işaretli işlemler kapıdan çıkar
    res = KN.run(KN.make_variant("FUND_CARRY"), S.fund_world(11))
    f8 = [t for t in res.trades if KN.FLAG_F8_UNMEASURABLE in t.flags]
    assert f8 and all(KN.gate_excluded(t) for t in f8)


# ============================================================================ parite (a) örtülerde
@pytest.mark.parametrize("vid, world", [
    ("O_T2_F_BTC", lambda: S.trend_world(22, n=420)), ("O_M2_X_TRAIL2", lambda: S.trend_world(21, n=420)),
    ("O_BOX_X_CHAN3", lambda: S.box_world(21, days=6)), ("O_BOX_X_TPL", lambda: S.box_world(22, days=6)),
    ("O_D4_X_TPL", lambda: S.d4_world(22, n=900)), ("O_D4_X_TIME", lambda: S.d4_world(23, n=900)),
    ("O_C4_F_VOL", lambda: S.c4_world(21, n=900)), ("O_C4_X_TRAIL2", lambda: S.c4_world(22, n=900))])
def test_parity_a_cover_kernel_vs_ledger(vid, world):
    data = world()
    kr = KN.run(KN.make_variant(vid), data)
    refs, rej = PA.reference_run(vid, data)
    a = PA.compare_a([(vid, kr, refs)], min_trades=1)
    th = RG.LIB_V1["thresholds"]["parity_kernel"]
    assert a.n_matched >= 3 and a.identity_share == 1.0, (a.only_kernel[:3], a.only_ref[:3])
    assert a.median_abs_dr <= th["max_median_abs_dr"] and a.p95_abs_dr <= th["max_p95_abs_dr"] and a.passed, a.to_dict()
    assert not any(k.startswith("MOVE_STOP_IGNORED") for k in rej)
    assert not any(KN.FLAG_MOVE_STOP_IGNORED in t.flags for t in kr.trades), "örtü yalnız geçerli sıkılaştırma ister"


@pytest.mark.skipif(os.environ.get("TB_P3B_FULL_PARITY") != "1", reason="tam ölçüm (≥ 200 işlem/aile) uzun sürer")
def test_parity_a_full_200_trades_per_cover_family():
    import importlib.util
    spec = importlib.util.spec_from_file_location("p3b_measure", ROOT / "scripts" / "engine_p3b_measure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for fam in mod.COVER_FAMILIES:
        out = mod.measure_covers(fam, min_trades=200, max_worlds=40)
        assert out["parity_a"]["passed"], (fam, out["parity_a"])


# ============================================================================ çalışma anı G/Ç yoklaması
import time as _time_mod  # noqa: E402

_PROBE: dict[str, Any] = {"on": False, "hits": [], "files": set()}
_TB = str(ROOT / "tradingbot") + os.sep


def _audit(event: str, args: tuple) -> None:
    if not _PROBE["on"]:
        return
    if event == "open":
        path = str(args[0]) if args else ""
        if not path.endswith((".py", ".pyc", ".pyi", ".so", ".pth")):
            _PROBE["hits"].append((event, path))
    elif event.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "urllib.", "http.")):
        _PROBE["hits"].append((event, str(args)[:120]))


sys.addaudithook(_audit)


_CLOCK = {"time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns", "process_time", "sleep",
          "now", "utcnow", "today"}
_RANDOM = {"random", "getrandbits", "randbytes", "urandom", "getrandom"}


def _profile(frame, event, arg):
    """Profil kancası (yalnız yoklama sırasında): çağrılan Python dosyaları ve saat/rastgelelik C çağrıları."""
    from datetime import date as _date
    if event == "call":
        _PROBE["files"].add(frame.f_code.co_filename)
    elif event == "c_call":
        name = getattr(arg, "__name__", "")
        owner = getattr(arg, "__self__", None)
        if name in _CLOCK and (owner is _time_mod or (isinstance(owner, type) and issubclass(owner, _date))):
            _PROBE["hits"].append(("clock:" + name, f"{frame.f_code.co_filename}:{frame.f_lineno}"))
        elif name in _RANDOM and (owner is os or isinstance(owner, random.Random) or name in ("urandom", "getrandom")):
            _PROBE["hits"].append(("random:" + name, f"{frame.f_code.co_filename}:{frame.f_lineno}"))


@pytest.mark.parametrize("vid, world", [("O_T2_F_BTC", lambda: S.trend_world(22, n=300)),
                                        ("O_BOX_X_TRAIL2", lambda: S.box_world(21, days=2)),
                                        ("O_D4_X_TPL", lambda: S.d4_world(22, n=500)),
                                        ("O_C4_F_ER", lambda: S.c4_world(21, n=620)),
                                        ("O_M2_X_CHAN3", lambda: S.trend_world(21, n=300)),
                                        ("O_BOX_F_VOL", lambda: S.box_world(22, days=2))])
def test_covers_do_no_io_clock_or_randomness_and_run_only_declared_files(vid, world):
    import importlib
    for m in SLI.declared_modules(vid):
        importlib.import_module(m)
    data = world()
    var = KN.make_variant(vid)
    calls = Counter()
    osig, oman = var.signals, var.manage

    def probed(f, kind):
        def wrapper(*a):
            calls[kind] += 1
            _PROBE["on"] = True
            sys.setprofile(_profile)
            try:
                return f(*a)
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


def test_cover_runs_are_deterministic():
    data = S.d4_world(22, n=700)
    a = {v: _dump(KN.run(KN.make_variant(v), data)) for v in KN.covers_of("D4")}
    b = {v: _dump(KN.run(KN.make_variant(v), S.d4_world(22, n=700))) for v in KN.covers_of("D4")}
    assert a == b
    paths = {json.dumps([(t["signal_ts"], t["exit_kind"], t["r"]["base"]) for t in json.loads(v)["trades"]]) for v in a.values()}
    base = json.dumps([(t.signal_ts, t.exit_kind, t.r["base"]) for t in KN.run(KN.make_variant("K_D4"), data).trades])
    assert len(paths) >= 5 and base in paths | {base}, "faktörler davranışı gerçekten değiştirir"
    assert math.isfinite(sum(t["r"]["base"] for v in a.values() for t in json.loads(v)["trades"]))


def test_fast_frame_falls_back_to_pandas_on_non_finite_ohlc():
    """İnceleme P3b-2b bulgu 1: NaN yüksek/alçak → saf atr NaN'ı yayar, pandas max(axis=1) atlar; hızlı çerçeve pandas
    yoluna döner ve günlük atr14/ema200 pandas'la aynı kalır."""
    import math

    import pandas as pd

    from tradingbot.strategy_lib import livebars as LB
    day = 86_400_000
    rows = [{"timestamp": i * day, "open": 100 + i % 7, "high": 103 + i % 5, "low": 97 - i % 3, "close": 100 + i % 4,
             "volume": 10.0} for i in range(450)]
    rows[300] = {**rows[300], "high": float("nan")}
    ff = LB.fast_frame(rows, "1d", daily_indicators=True)
    pf = LB.frame(rows, "1d", daily_indicators=True)
    assert isinstance(ff, pd.DataFrame) and ff.equals(pf)
    assert not math.isnan(float(ff["atr14"].iloc[-1]))
