# -*- coding: utf-8 -*-
"""MUM VARYASYONU DİLİ (DSL v1) — sözlük eşikleri, katı ayrıştırma, mühür, tek dedektörün anlamı ve kayıt kapısının
hiç yükseltmemesi.

Pencereler `candle_variation_examples.pad` ile kurulur: 500 barlık düz önek (ATR ≈ 1, RSI ≈ 50, hacim ortalaması ≈ 1),
dizideki barlar BASE'e göre ATR birimiyle yazılır. Göstergelerin beklenen değerleri laboratuvarın KENDİ fonksiyonundan
(`signal_lab.indicators`) okunur; dedektör ikinci bir formül kullanmamalıdır.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from candle_variation_examples import EXAMPLES, H4, T_LAST, pad  # noqa: E402
from tradingbot import candle_dsl as D  # noqa: E402
from tradingbot import candle_variations as V  # noqa: E402
from tradingbot import signal_lab as L  # noqa: E402
from tradingbot.learn.candle_context import CandleContextConfig  # noqa: E402

CV0 = "CV000_EXAMPLE_BULL3"
W = D.WINDOW


def _var(bars, relations=(), context=None, *, side="LONG", confirm=None, stop=None, exit_=None) -> D.Variation:
    return D.parse_variation({"id": "CV901_TEST", "definition": {
        "side": side, "timeframe": "4h", "bars": bars, "relations": list(relations), "context": context or {},
        "confirm": confirm or {"kind": "pattern_close"}, "stop": stop or {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": exit_ or {"target_r": 2.0, "max_hold_bars": 24}}})


def _win(seq, **kw) -> D.Window:
    w, why = D.window_from_rows(pad(seq, **kw), H4)
    assert why is None, why
    return w


def _ind(win: D.Window) -> dict:
    return L.indicators(pd.DataFrame({"high": win.h, "low": win.l, "close": win.c, "volume": win.v}))


def _clause(explained: dict, name: str, cand: int = 0) -> dict:
    return next(r for r in explained["candidates"][cand]["clauses"] if r["clause"] == name)


def _rw_rows(seed: int, n: int = W, drift: float = 0.0) -> list[dict]:
    """Gerçek fiyat YOLU olan rastgele yürüyüş (mum içi 12 alt adım), 4h adımlı, hacimli."""
    rnd = np.random.default_rng(seed)
    px, rows = 100.0, []
    for k in range(n):
        path = px * np.exp(np.cumsum(rnd.normal(drift / 12, 0.01 / np.sqrt(12), 12)))
        c = float(path[-1])
        rows.append({"timestamp": T_LAST - (n - 1 - k) * H4, "open": px, "high": max(px, float(path.max())),
                     "low": min(px, float(path.min())), "close": c, "volume": float(rnd.uniform(50, 150))})
        px = c
    return rows


# ================================================================== 1) sözlük
def test_lexicon_thresholds_equal_catalog_config():
    cc, lx = CandleContextConfig(), D.LEXICON
    assert lx["doji"]["bar"]["body_range"] == [None, cc.doji_body_ratio] == [None, 0.10]
    assert lx["küçük gövde"]["bar"]["body_range"] == lx["topaç"]["bar"]["body_range"] == [None, cc.spinning_top_body_max] == [None, 0.35]
    assert lx["uzun gövde"]["bar"]["body_range"] == [cc.belt_hold_body_min, None] == [0.60, None]
    assert lx["marubozu"]["bar"]["body_range"] == [cc.marubozu_body_ratio, None] == [0.85, None]
    assert lx["üst fitilsiz"]["bar"] == {"upper_wick_range": [None, cc.belt_hold_wick_max]}
    assert lx["alt fitilsiz"]["bar"] == {"lower_wick_range": [None, cc.belt_hold_wick_max]} and cc.belt_hold_wick_max == 0.05
    hammer = {"lower_wick_range": [cc.hammer_wick_ratio, None], "upper_wick_range": [None, cc.hammer_opposite_wick_max]}
    assert lx["çekiç"]["bar"] == lx["uzun alt fitil"]["bar"] == hammer and (cc.hammer_wick_ratio, cc.hammer_opposite_wick_max) == (0.55, 0.20)
    mirror = {"upper_wick_range": [cc.hammer_wick_ratio, None], "lower_wick_range": [None, cc.hammer_opposite_wick_max]}
    assert lx["ters çekiç"]["bar"] == lx["uzun üst fitil"]["bar"] == mirror
    # önceki hareket = katalogun `detect_trend` ölçüsü (10 bar, 0,5 ATR)
    assert lx["düşüş sonrası"]["context"]["prior_move"] == {"bars": cc.trend_lookback_bars, "max": -cc.trend_min_slope_atr}
    assert lx["yükseliş sonrası"]["context"]["prior_move"] == {"bars": cc.trend_lookback_bars, "min": cc.trend_min_slope_atr}
    assert (cc.trend_lookback_bars, cc.trend_min_slope_atr) == (10, 0.5)
    assert cc.engulf_min_ratio == 1.0, "yutan (gövde) ilişkisi tam kapsama (oran 1,0) varsayar"
    # varsayılan çıkışlar ve risk aralığı = laboratuvarın değerleri
    lab = L.LabConfig()
    assert lx["varsayılan çıkışlar"]["exit"] == {"target_r": lab.default_rr, "max_hold_bars": lab.max_hold_bars}
    assert lx["varsayılan çıkışlar"]["stop"] == {"anchor": "pattern", "atr_buffer": 0.25}
    assert D.DEFAULT_RISK_ATR_BOUNDS == (lab.min_risk_atr, lab.max_risk_atr)
    # hacim ve RSI kelimeleri laboratuvarın dilim sınırlarında
    nan1 = np.array([np.nan])
    base_ind = {"rsi": nan1, "ema50": nan1, "ema200": nan1, "atr_pct": nan1, "atr_med": nan1, "vol_avg": np.array([1.0])}
    bucket = lambda vr: L.context(base_ind, {"close": np.array([1.0]), "volume": np.array([vr])}, 0, "LONG")["hacim"]  # noqa: E731
    assert bucket(lx["hacimli"]["bar"]["volume_ratio"][0] + 0.01).startswith("yüksek")
    assert bucket(lx["hacimsiz"]["bar"]["volume_ratio"][1] - 0.01).startswith("düşük")
    rsi = lambda r: L.context(dict(base_ind, rsi=np.array([r])), {"close": np.array([1.0]), "volume": np.array([1.0])}, 0, "LONG")["rsi"]  # noqa: E731
    assert rsi(lx["aşırı satım"]["context"]["rsi14"][1] - 0.01) == "<30" and rsi(lx["aşırı alım"]["context"]["rsi14"][0] + 0.01) == ">70"
    # sözlüğün her parçası geçerli DSL'dir
    for word, frag in lx.items():
        d = {"side": "LONG", "timeframe": "4h", "bars": [dict(frag.get("bar", {})), {}], "relations": list(frag.get("relations", [])),
             "context": copy.deepcopy(frag.get("context", {})), "confirm": {"kind": "pattern_close"},
             "stop": frag.get("stop", {"anchor": "pattern", "atr_buffer": 0.25}), "exit": frag.get("exit", {"target_r": 2.0, "max_hold_bars": 24})}
        try:
            D.parse_variation({"id": "CV901_LEXICON", "definition": d})
        except ValueError as exc:
            pytest.fail("sözlük parçası geçersiz: %s: %s" % (word, exc))


# ================================================================== 2) katı ayrıştırma
def _set(path, value):
    def f(e):
        d = e
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = value
    return f


def _del(path):
    def f(e):
        d = e
        for k in path[:-1]:
            d = d[k]
        del d[path[-1]]
    return f


DF = "definition"
BAD = [
    ("top_key", _set(("foo",), 1), "bilinmeyen anahtar"),
    ("definition_key", _set((DF, "foo"), 1), "bilinmeyen anahtar"),
    ("bar_key", _set((DF, "bars", 0, "wick"), [0, 1]), "bilinmeyen anahtar"),
    ("context_key", _set((DF, "context", "macd"), [0, 1]), "bilinmeyen anahtar"),
    ("prior_move_key", _set((DF, "context", "prior_move", "slope"), 1), "bilinmeyen anahtar"),
    ("confirm_key", _set((DF, "confirm", "within_bars"), 2), "bilinmeyen anahtar"),
    ("stop_key", _set((DF, "stop", "trail"), 1), "bilinmeyen anahtar"),
    ("exit_key", _set((DF, "exit", "trail"), 1), "bilinmeyen anahtar"),
    ("source_key", _set(("source", "url"), "x"), "bilinmeyen anahtar"),
    ("missing_definition_key", _del((DF, "stop")), "eksik anahtar"),
    ("ratio_above_one", _set((DF, "bars", 0, "body_range"), [0.6, 1.2]), "[0, 1]"),
    ("ratio_below_zero", _set((DF, "bars", 2, "upper_wick_range"), [-0.1, 0.25]), "[0, 1]"),
    ("atr_ratio_negative", _set((DF, "bars", 0, "range_atr"), [-1.0, None]), "sınır"),
    ("min_gt_max", _set((DF, "bars", 0, "body_range"), [0.7, 0.3]), "min > max"),
    ("context_min_gt_max", _set((DF, "context", "rsi14"), [60, 40]), "min > max"),
    ("prior_move_min_gt_max", _set((DF, "context", "prior_move"), {"bars": 10, "min": 1.0, "max": -1.0}), "min > max"),
    ("empty_range", _set((DF, "bars", 1, "range_atr"), [None, None]), "boş aralık"),
    ("range_not_pair", _set((DF, "bars", 1, "range_atr"), [0.1]), "[min, max]"),
    ("bool_bound", _set((DF, "bars", 0, "body_range"), [True, None]), "sayı olmalı"),
    ("string_bound", _set((DF, "bars", 0, "body_range"), ["0.6", None]), "sayı olmalı"),
    ("equals", _set((DF, "relations", 0), "c1.close == c0.close"), "=="),
    ("not_equals", _set((DF, "relations", 0), "c1.close != c0.close"), "=="),
    ("relation_index", _set((DF, "relations", 0), "c3.close > c0.close"), "mum indeksi"),
    ("relation_field", _set((DF, "relations", 0), "c1.wick > c0.close"), "bilinmeyen alan"),
    ("relation_garbage", _set((DF, "relations", 0), "c1.close >> c0.close"), "dilbilgisine"),
    ("relation_negative_k", _set((DF, "relations", 0), "c1.close > -2 * c0.close"), "dilbilgisine"),
    ("relation_not_text", _set((DF, "relations", 0), 5), "metin olmalı"),
    ("aggregate_below_minus_50", _set((DF, "relations", 1), "c1.low <= min_low(-51..-1)"), "toplam aralığı"),
    ("aggregate_reaches_pattern", _set((DF, "relations", 1), "c1.low <= min_low(-10..0)"), "toplam aralığı"),
    ("aggregate_reversed", _set((DF, "relations", 1), "c1.low <= min_low(-1..-10)"), "toplam aralığı"),
    ("aggregate_unknown", _set((DF, "relations", 1), "c1.low <= median_low(-10..-1)"), "bilinmeyen toplam"),
    ("within_bars_0", _set((DF, "confirm"), {"kind": "break", "within_bars": 0}), "1..3"),
    ("within_bars_4", _set((DF, "confirm"), {"kind": "break", "within_bars": 4}), "1..3"),
    ("confirm_kind", _set((DF, "confirm"), {"kind": "retest"}), "pattern_close"),
    ("tf_5m", _set((DF, "timeframe"), "5m"), "COST_TRAP"),
    ("tf_15m", _set((DF, "timeframe"), "15m"), "COST_TRAP"),
    ("tf_1h", _set((DF, "timeframe"), "1h"), "TF_NOT_ENABLED_V1"),
    ("tf_1d", _set((DF, "timeframe"), "1d"), "TF_NOT_ENABLED_V1"),
    ("tf_1w", _set((DF, "timeframe"), "1w"), "yalnız '4h'"),
    ("target_r_low", _set((DF, "exit", "target_r"), 0.2), "0.5..10"),
    ("target_r_high", _set((DF, "exit", "target_r"), 11), "0.5..10"),
    ("max_hold_zero", _set((DF, "exit", "max_hold_bars"), 0), "1..300"),
    ("max_hold_301", _set((DF, "exit", "max_hold_bars"), 301), "1..300"),
    ("max_hold_fraction", _set((DF, "exit", "max_hold_bars"), 2.5), "tam sayı"),
    ("atr_buffer_negative", _set((DF, "stop", "atr_buffer"), -0.1), "0..2"),
    ("atr_buffer_high", _set((DF, "stop", "atr_buffer"), 2.5), "0..2"),
    ("stop_anchor", _set((DF, "stop", "anchor"), "swing"), "pattern_to_confirm"),
    ("risk_bounds_order", _set((DF, "risk_atr_bounds"), [5.0, 1.0]), "lo < hi"),
    ("risk_bounds_high", _set((DF, "risk_atr_bounds"), [0.1, 11.0]), "lo < hi"),
    ("side", _set((DF, "side"), "BUY"), "LONG | SHORT"),
    ("color_doji", _set((DF, "bars", 1, "color"), "doji"), "doji rengi yok"),
    ("no_bars", _set((DF, "bars"), []), "1..5"),
    ("six_bars", _set((DF, "bars"), [{} for _ in range(6)]), "1..5"),
    ("trend_unknown", _set((DF, "context", "trend"), ["up"]), "alt kümesi"),
    ("trend_empty", _set((DF, "context", "trend"), []), "alt kümesi"),
    ("trend_duplicate", _set((DF, "context", "trend"), ["with", "with"]), "alt kümesi"),
    ("ema_side", _set((DF, "context", "ema200_side"), "over"), "above | below"),
    ("prior_move_bars_1", _set((DF, "context", "prior_move"), {"bars": 1, "max": -0.5}), "2..50"),
    ("prior_move_bars_51", _set((DF, "context", "prior_move"), {"bars": 51, "max": -0.5}), "2..50"),
    ("rsi_above_100", _set((DF, "context", "rsi14"), [None, 120]), "[0, 100]"),
    ("bad_id", _set(("id",), "cv001_x"), "biçiminde"),
    ("id_trailing_newline", _set(("id",), "CV001_OK\n"), "biçiminde"),
    ("readback_by", _set(("readback",), {"confirmed_by": "claude", "date": "2026-09-26"}), "'user'"),
    ("readback_date", _set(("readback",), {"confirmed_by": "user", "date": "26.09.2026"}), "YYYY-MM-DD"),
    ("approval_observation", _set(("approval",), {"by": "user", "date": "2026-09-26", "observation": "yes"}), "bool"),
    ("approval_run_id", _set(("approval",), {"by": "user", "date": "2026-09-26", "observation": True, "run_id": True}), "run_id"),
    ("approval_unknown", _set(("approval",), {"by": "user", "date": "2026-09-26", "observation": True, "run": "1"}), "bilinmeyen"),
    ("example_not_bool", _set(("example",), "yes"), "bool"),
    ("supersedes_self", _set(("supersedes",), CV0), "supersedes"),
]


@pytest.mark.parametrize("mutate,match", [(m, s) for _n, m, s in BAD], ids=[n for n, _m, _s in BAD])
def test_parse_rejects_bad_definitions(mutate, match):
    e = copy.deepcopy(V.CV000_EXAMPLE_BULL3)
    D.parse_variation(e)                                  # değişmemiş kayıt geçerli
    mutate(e)
    with pytest.raises(ValueError, match=re.escape(match)):
        D.parse_variation(e)


def test_parse_rejects_non_dict_entries():
    for bad in (None, [], "CV001_X", {"id": "CV001_X"}, {"definition": {}}):
        with pytest.raises(ValueError):
            D.parse_variation(bad)


# ================================================================== 3) mühür
def test_definition_sha_ignores_metadata_changes_with_definition_dsl_version_and_window(monkeypatch):
    base = copy.deepcopy(V.CV000_EXAMPLE_BULL3)
    var = D.parse_variation(base)
    sha = var.definition_sha
    assert re.fullmatch(r"[0-9a-f]{16}", sha)
    manual = hashlib.sha256(json.dumps({"dsl": D.DSL_VERSION, "window": D.WINDOW, "definition": var.definition}, sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()[:16]
    assert sha == manual == D.definition_sha(base) == D.definition_sha(base["definition"]) == D.definition_sha(var)
    # üst bilgi mühre girmez
    meta = copy.deepcopy(base)
    meta.update(id="CV123_OTHER", example=False, title_tr="başka", notes_tr=["x"],
                source={"kind": "text", "received": "2026-10-01", "ref": "metin"},
                readback={"confirmed_by": "user", "date": "2026-10-01"},
                approval={"by": "user", "date": "2026-10-02", "observation": True, "run_id": 123},
                retired={"date": "2026-10-03", "reason_tr": "deneme"}, supersedes=CV0)
    assert D.parse_variation(meta).definition_sha == sha
    assert D.parse_variation(meta).approval["run_id"] == "123", "onayın çalıştırma kimliği metne çevrilir"
    # aynı anlam, farklı yazım → aynı mühür (2 ile 2.0, boşluklar, 1*, +0 atr, varsayılanlar, anahtar sırası)
    same = copy.deepcopy(base)
    d = same["definition"]
    d["exit"].update(target_r=2, max_hold_bars=24.0)
    d["stop"]["atr_buffer"] = 0.25
    d["context"] = {"rsi14": [None, 55.0], "prior_move": {"max": -0.5, "bars": 10.0}}
    d["relations"] = ["c1.body_hi<=c0.body_mid", "  c1.low   <=  min_low( -10 .. -1 ) ", "c2.close>1*c0.body_mid+0 atr"]
    d["bars"][1].pop("color")                             # varsayılan renk: any
    d.pop("risk_atr_bounds")                              # varsayılan: [0.1, 5.0]
    assert D.parse_variation(same).definition_sha == sha
    k2, k2b = copy.deepcopy(base), copy.deepcopy(base)
    k2[DF]["relations"].append("c2.body >= 2 * c1.body")
    k2b[DF]["relations"].append("c2.body>=2.0*c1.body")
    assert D.parse_variation(k2).definition_sha == D.parse_variation(k2b).definition_sha != sha
    assert D.parse_variation(k2).definition["relations"][-1] == "c2.body >= 2.0 * c1.body"
    # tanım değişince mühür değişir
    for mut in (lambda x: x["bars"][0].update(body_range=[0.61, None]), lambda x: x.update(side="SHORT"),
                lambda x: x["stop"].update(atr_buffer=0.3), lambda x: x["exit"].update(max_hold_bars=25),
                lambda x: x["context"].pop("rsi14"), lambda x: x.update(confirm={"kind": "break", "within_bars": 1})):
        x = copy.deepcopy(base)
        mut(x[DF])
        assert D.parse_variation(x).definition_sha != sha
    # DSL sürümü ve pencere mühre girer
    monkeypatch.setattr(D, "DSL_VERSION", "cv_dsl_2")
    assert D.definition_sha(base) != sha
    monkeypatch.setattr(D, "DSL_VERSION", "cv_dsl_1")
    monkeypatch.setattr(D, "WINDOW", 600)
    assert D.definition_sha(base) != sha


# ================================================================== 4) mum anahtarları
def test_each_per_bar_key_on_hand_built_windows():
    win = _win([(0.0, 1.2, -0.2, 1.0, 2.0)])              # boğa: gövde 1,0, aralık 1,4, fitiller 0,2; hacim 2x
    ind = _ind(win)
    # düz önek: ATR ≈ 1, RSI ≈ 50, hacim ortalaması 1 → testler ATR birimiyle yazılır
    assert ind["atr"][-2] == pytest.approx(1.0, abs=1e-9) and 45 < ind["rsi"][-2] < 55 and ind["vol_avg"][-1] == 1.0
    a_ref = float(ind["atr"][-2])
    rng = float(win.h[-1] - win.l[-1])
    expect = {"body_range": float(win.c[-1] - win.o[-1]) / rng, "upper_wick_range": float(win.h[-1] - win.c[-1]) / rng,
              "lower_wick_range": float(win.o[-1] - win.l[-1]) / rng, "close_pos": float(win.c[-1] - win.l[-1]) / rng,
              "range_atr": rng / a_ref, "body_atr": float(win.c[-1] - win.o[-1]) / a_ref, "volume_ratio": 2.0 / float(ind["vol_avg"][-1])}
    assert expect["body_range"] == pytest.approx(1 / 1.4) and expect["close_pos"] == pytest.approx(1.2 / 1.4)
    for key, v in expect.items():
        name = "bars[0].%s" % key
        top = 1.0 if key in D.RATIO_KEYS else v + 1.0
        for inside in ([v - 0.01, min(v + 0.01, top)], [v, v], [v, None], [None, v]):   # iki uç dahil
            var = _var([{key: inside}])
            assert D.detect_last(win, var) is not None, (key, inside)
            row = _clause(D.explain_last(win, var), name)
            assert row["ok"] and row["value"] == v, (key, row)
        for outside in ([min(v + 0.01, top), None], [None, v - 0.01]):
            var = _var([{key: outside}])
            assert D.detect_last(win, var) is None, (key, outside)
            assert D.explain_last(win, var)["first_fail_clause"] == name
    assert D.detect_last(win, _var([{"color": "bull"}])) and D.detect_last(win, _var([{"color": "any"}]))
    bear = _var([{"color": "bear"}])
    assert D.detect_last(win, bear) is None and D.explain_last(win, bear)["first_fail"] == "bars[0].color bull != bear"


def test_zero_range_bar_fails_every_ratio_clause():
    win = _win([(0.0, 0.0, 0.0, 0.0, 1.0)])               # h == l == o == c: okunabilir bar, oran tanımsız
    assert D.detect_last(win, _var([{}])) is not None, "koşulsuz tek mum: bar geçerli, stop koruyucu tarafta"
    for key in D.RATIO_KEYS:
        var = _var([{key: [0.0, 1.0]}])                   # tüm aralık bile NaN'ı kabul etmez
        assert D.detect_last(win, var) is None, key
        e = D.explain_last(win, var)
        assert e["first_fail_clause"] == "bars[0].%s" % key and e["first_fail"].endswith("NaN")
        assert _clause(e, "bars[0].%s" % key)["value"] is None
        json.dumps(e, allow_nan=False)                    # NaN değer None olarak yazılır (altın özet JSON'dan alınır)
    for color in ("bull", "bear"):
        assert D.detect_last(win, _var([{"color": color}])) is None
    # ATR'ye oranlar tanımlıdır: aralık ve gövde 0
    e = D.explain_last(win, _var([{"range_atr": [None, 0.7], "body_atr": [None, 0.1]}]))
    assert e["matched"] and _clause(e, "bars[0].range_atr")["value"] == 0.0 and _clause(e, "bars[0].body_atr")["value"] == 0.0


# ================================================================== 6) ilişkiler
REL_WINDOW = [(0.0, 0.6, -1.0, -0.8, 1.0),     # c0: ayı, gövde 0,8, aralık 1,6
              (-0.7, 0.4, -0.9, 0.3, 2.5)]     # c1: boğa, gövde 1,0, aralık 1,3, c0'ın içinde, hacim 2,5x
REL_CASES = [
    ("c1.high <= c0.high", True), ("c1.low >= c0.low", True), ("c1.high > c0.high", False),
    ("c1.body >= 2 * c0.body", False), ("c1.body >= 1.2 * c0.body", True),
    ("c1.close > c0.open + 0.25 atr", True), ("c1.close > c0.open + 0.35 atr", False),
    ("c1.open > c0.close - 0.05 atr", True), ("c1.open < c0.close - 0.05 atr", False),
    ("c1.low <= min_low(-10..-1)", True), ("c0.low <= min_low(-10..-1)", True), ("c1.high > max_high(-5..-1)", False),
    ("c1.volume >= 2 * avg_volume(-20..-1)", True), ("c1.volume >= 3 * avg_volume(-20..-1)", False),
    ("c0.range > 1.5 * avg_range(-20..-1)", True), ("c0.range > 1.7 * avg_range(-20..-1)", False),
    ("c1.body_mid > c0.body_mid", True), ("c1.mid < c0.mid", True), ("c1.upper_wick < c0.lower_wick", True),
    ("c1.body_lo > c0.body_hi", False), ("c1.body_hi >= c0.body_hi", True),
    ("max_close(-3..-1) < c1.close", True), ("min_close(-3..-1) > c0.close + 0.5 atr", True),
    ("min_close(-3..-1) > c0.close + 0.8 atr", False),
    ("avg_body(-5..-1) <= 0.25 * c1.body", True), ("avg_body(-5..-1) > 0.25 * c1.body", False),
]


@pytest.mark.parametrize("rel,expected", REL_CASES, ids=[r for r, _e in REL_CASES])
def test_relation_forms(rel, expected):
    win = _win(REL_WINDOW)
    var = _var([{}, {}], relations=[rel], stop={"anchor": "pattern", "atr_buffer": 0.0})
    assert (D.detect_last(win, var) is not None) is expected
    row = _clause(D.explain_last(win, var), "relations[0]")
    assert row["ok"] is expected
    assert row["stage"] == ("pattern" if " atr" in rel else "shape"), "ATR terimli ilişki göstergeden sonra değerlendirilir"


def test_relation_text_is_normalised():
    assert D.parse_relation("c2.body>=2*c1.body", 3).text == "c2.body >= 2.0 * c1.body"
    assert D.parse_relation("c1.open>c0.close+0.05atr", 2).text == "c1.open > c0.close + 0.05 atr"
    assert D.parse_relation(" c1.low <= min_low( -10 .. -1 ) ", 2).text == "c1.low <= min_low(-10..-1)"
    assert D.parse_relation("c1.close < 1 * c0.close - 0 atr", 2).text == "c1.close < c0.close"
    assert D.parse_relation("max_high(-3..-1) < c0.close - .5 atr", 1).text == "max_high(-3..-1) < c0.close - 0.5 atr"


# ================================================================== 7) bağlam = laboratuvar göstergeleri
def test_context_filters_equal_signal_lab_indicators_on_the_window():
    seen: set[str] = set()
    for seed, drift in ((1, 0.0), (2, 0.004), (3, -0.004), (4, 0.0), (5, 0.002)):
        rows = _rw_rows(seed, drift=drift)
        win, why = D.window_from_rows(rows, H4)
        assert why is None
        df = pd.DataFrame(rows)
        ind = L.indicators(df)
        arr = {"close": df["close"].to_numpy(dtype=float), "volume": df["volume"].to_numpy(dtype=float)}
        i = W - 1
        values = {"rsi14": float(ind["rsi"][i]), "volume_ratio": arr["volume"][i] / ind["vol_avg"][i],
                  "atr_regime": ind["atr_pct"][i] / ind["atr_med"][i]}
        for key, v in values.items():
            var = _var([{}], context={key: [v - 1e-9, v + 1e-9]})
            assert D.detect_last(win, var) is not None, key
            assert _clause(D.explain_last(win, var), "context." + key)["value"] == v
            for rng in ([v + 1e-9, None], [None, v - 1e-9]):
                var = _var([{}], context={key: rng})
                assert D.detect_last(win, var) is None and D.explain_last(win, var)["first_fail_clause"] == "context." + key
        for side in ("LONG", "SHORT"):
            lab = {"trendle": "with", "trende_karşı": "against", "karışık": "mixed"}[L.context(ind, arr, i, side)["trend"]]
            seen.add(lab)
            for b in D.TRENDS:
                assert (D.detect_last(win, _var([{}], side=side, context={"trend": [b]})) is not None) is (b == lab), (seed, side, b)
        side_ = "above" if arr["close"][i] > ind["ema200"][i] else "below"
        assert D.detect_last(win, _var([{}], context={"ema200_side": side_})) is not None
        assert D.detect_last(win, _var([{}], context={"ema200_side": ({"above", "below"} - {side_}).pop()})) is None
        # prior_move: iki mumlu formasyon → p0 = i-1; (c[p0-1] - c[p0-L]) / ATR14(p0-1)
        for n_back in (2, 10, 50):
            p0 = i - 1
            v = (arr["close"][p0 - 1] - arr["close"][p0 - n_back]) / ind["atr"][p0 - 1]
            var = _var([{}, {}], context={"prior_move": {"bars": n_back, "min": v - 1e-9, "max": v + 1e-9}})
            assert D.detect_last(win, var) is not None and _clause(D.explain_last(win, var), "context.prior_move")["value"] == v
            assert D.detect_last(win, _var([{}, {}], context={"prior_move": {"bars": n_back, "min": v + 1e-9}})) is None
        # context_ok: yalnız şekil DIŞI koşullar + aynı stop kuralı, son n bar üzerinde
        rsi = values["rsi14"]
        shape_never = [{"color": "bull", "body_range": [0.999, 1.0]}, {"color": "bear", "body_range": [0.999, 1.0]}]
        var = _var(shape_never, context={"rsi14": [rsi - 1e-9, rsi + 1e-9]}, stop={"anchor": "pattern", "atr_buffer": 0.5})
        ok, stop, a_i = D.context_ok(win, var)
        assert ok and a_i == float(ind["atr"][i]) and stop == float(df["low"].iloc[-2:].min()) - 0.5 * a_i
        short = _var(shape_never, side="SHORT", context={"rsi14": [rsi - 1e-9, rsi + 1e-9]})
        assert D.context_ok(win, short)[1] == float(df["high"].iloc[-2:].max()) + 0.25 * a_i
        assert D.context_ok(win, _var(shape_never, context={"rsi14": [rsi + 1e-9, None]})) == (False, None, a_i)
        # placebo_context (eşleştirilmiş plasebo): aynı şekil dışı koşullar, stop kuralı YOK; prior_move sözde p'ye çapalı
        assert D.placebo_context(win, var) == (True, a_i) and D.placebo_context(win, short) == (True, a_i)
        assert D.placebo_context(win, _var(shape_never, context={"rsi14": [rsi + 1e-9, None]})) == (False, a_i)
        v1 = (arr["close"][i - 3] - arr["close"][i - 12]) / ind["atr"][i - 3]        # p = i-1 → p0 = i-2
        v0 = (arr["close"][i - 2] - arr["close"][i - 11]) / ind["atr"][i - 2]        # p = i   → p0 = i-1
        pm = _var(shape_never, context={"prior_move": {"bars": 10, "min": v1 - 1e-9, "max": v1 + 1e-9}})
        assert D.placebo_context(win, pm, p=W - 2) == (True, a_i)
        assert D.placebo_context(win, pm)[0] is bool(abs(v0 - v1) <= 1e-9)
    assert seen == set(D.TRENDS), "üç trend dilimi de denendi"


# ================================================================== 8) kırılım teyidi
P = (0.0, 0.2, -0.1, 0.1, 1.0)        # küçük mum: H = 100,2, L = 99,9 (düz önek aralığı 1,0 → formasyon değil)
N = (0.0, 0.5, -0.5, 0.05, 1.0)       # nötr: kapanış [L, H] içinde
B = (0.0, 0.7, -0.3, 0.6, 1.0)        # kırılım: kapanış H'nin üstünde


def _brk(side="LONG", within=3, anchor="pattern"):
    return _var([{"range_atr": [None, 0.5]}], side=side, confirm={"kind": "break", "within_bars": within},
                stop={"anchor": anchor, "atr_buffer": 0.25})


def test_break_mode():
    var = _brk()
    # H'nin ötesindeki ilk kapanış (K bar içinde) teyit eder; en yeni p önce
    win = _win([P, N, B])
    h = D.detect_last(win, var)
    assert h and (h.p, h.p0) == (W - 3, W - 3) and h.pattern_high == pytest.approx(100.2) and h.pattern_low == pytest.approx(99.9)
    assert h.stop == pytest.approx(99.9 - 0.25 * h.atr_i) and h.atr_i == float(_ind(win)["atr"][-1])
    assert h.signal_ts == T_LAST and h.signal_close_ms == T_LAST + H4
    # daha önceki bir kapanış H'yi geçtiyse teyit O bardı: ikinci isabet yok
    assert D.detect_last(_win([P, B]), var).p == W - 2
    win = _win([P, B, (0.6, 1.3, 0.3, 1.2, 1.0)])
    assert D.detect_last(win, var) is None and _clause(D.explain_last(win, var), "break.first", cand=1)["ok"] is False
    # L'nin altındaki kapanış formasyonu geçersiz kılar
    win = _win([P, (0.0, 0.4, -0.6, -0.3, 1.0), B])
    assert D.detect_last(win, var) is None and D.explain_last(win, var)["candidates"][1]["first_fail_clause"] == "break.intact"
    # K bar sonra süre dolar (tam K bar hâlâ geçerli)
    assert D.detect_last(_win([P, N, N, B]), var).p == W - 4
    assert D.detect_last(_win([P, N, N, N, B]), var) is None
    assert D.detect_last(_win([P, N, B]), _brk(within=1)) is None
    # iç içe formasyonlar: en yeni p kazanır
    assert D.detect_last(_win([P, (0.1, 0.25, 0.0, 0.15, 1.0), B]), var).p == W - 2
    # pattern_to_confirm: stop i barı dahil tüm barların en düşüğünden
    seq = [P, N, (0.0, 0.7, -0.8, 0.6, 1.0)]
    a = D.detect_last(_win(seq), var)
    b = D.detect_last(_win(seq), _brk(anchor="pattern_to_confirm"))
    assert a.stop == pytest.approx(99.9 - 0.25 * a.atr_i) and b.stop == pytest.approx(99.2 - 0.25 * b.atr_i)
    # SHORT aynası
    s = _brk(side="SHORT")
    h = D.detect_last(_win([P, N, (0.0, 0.3, -0.7, -0.6, 1.0)]), s)
    assert h and h.p == W - 3 and h.side == "SHORT" and h.stop == pytest.approx(100.2 + 0.25 * h.atr_i)
    assert D.detect_last(_win([P, (0.0, 0.8, -0.2, 0.3, 1.0), (0.0, 0.3, -0.7, -0.6, 1.0)]), s) is None   # H üstü kapanış bozar
    # bekleyen kırılımlar (panel): formasyon, seviye, kalan bar
    for seq, left in (([P], 3), ([P, N], 2), ([P, N, N], 1)):
        e = D.explain_last(_win(seq), var)
        assert not e["matched"] and len(e["pending_breaks"]) == 1, seq
        pb = e["pending_breaks"][0]
        assert pb["p"] == W - len(seq) and pb["bars_left"] == left
        assert pb["level"] == pytest.approx(100.2) and pb["invalid_level"] == pytest.approx(99.9)
    assert D.explain_last(_win([P, N, N, N]), var)["pending_breaks"] == []
    assert D.explain_last(_win([P, B]), var)["pending_breaks"] == [], "kırılmış formasyon bekleyen değildir"
    assert D.explain_last(_win([P]), s)["pending_breaks"][0]["level"] == pytest.approx(99.9)


# ================================================================== 9) stop geçerliliği
def test_stop_validity(monkeypatch):
    var = V.get(CV0)
    win = _win(EXAMPLES[CV0]["match"])
    assert D.detect_last(win, var) is not None
    orig = L.indicators
    for bad in (np.nan, 0.0, np.inf):
        def patched(df, _bad=bad):
            d = dict(orig(df))
            a = d["atr"].copy()
            a[-1] = _bad
            d["atr"] = a
            return d
        monkeypatch.setattr(L, "indicators", patched)
        assert D.detect_last(win, var) is None, bad
        assert D.explain_last(win, var)["first_fail_clause"] == "stop.atr"
    monkeypatch.setattr(L, "indicators", orig)
    # stop <= 0: düşük fiyatlı seri (önek dipleri 0,1), tampon 0,25 ATR stop'u sıfırın altına iter
    low = _win([(0.0, 0.5, -0.5, 0.1, 1.0)], base=0.6)
    assert D.detect_last(low, _var([{}], stop={"anchor": "pattern", "atr_buffer": 0.0})) is not None
    under = _var([{}], stop={"anchor": "pattern", "atr_buffer": 0.25})
    assert D.detect_last(low, under) is None and D.explain_last(low, under)["first_fail_clause"] == "stop.side"
    # yanlış taraf: kapanış formasyonun ucunda, tampon 0 → stop kapanışa eşit (koruyucu değil)
    for side, bar in (("LONG", (0.0, 0.5, -0.5, -0.5, 1.0)), ("SHORT", (0.0, 0.5, -0.5, 0.5, 1.0))):
        w = _win([bar])
        flat_stop = _var([{}], side=side, stop={"anchor": "pattern", "atr_buffer": 0.0})
        assert D.detect_last(w, flat_stop) is None and D.explain_last(w, flat_stop)["first_fail_clause"] == "stop.side"
        assert D.detect_last(w, _var([{}], side=side)) is not None


# ================================================================== 10) pencere okuma (fail-closed)
def test_window_from_rows_fail_closed():
    rows = pad([])
    win, why = D.window_from_rows(rows, H4)
    assert why is None and len(win.c) == W and win.step_ms == H4 and win.v.dtype == float and float(win.v[-1]) == 1.0
    assert D.window_from_rows(rows[1:], H4) == (None, "NOT_ENOUGH_4H_BARS")
    assert D.window_from_rows(rows + [dict(rows[-1], timestamp=rows[-1]["timestamp"] + H4)], H4) == (None, "NOT_ENOUGH_4H_BARS")
    assert D.window_from_rows([], H4) == (None, "NOT_ENOUGH_4H_BARS") and D.window_from_rows(None, H4) == (None, "NOT_ENOUGH_4H_BARS")

    def edit(k, **kw):
        out = [dict(r) for r in rows]
        out[k].update(kw)
        return out
    gap = [dict(r, timestamp=r["timestamp"] + (H4 if k >= 250 else 0)) for k, r in enumerate(rows)]
    assert D.window_from_rows(gap, H4) == (None, "ROWS_NOT_CONTIGUOUS")
    assert D.window_from_rows(edit(10, timestamp=rows[9]["timestamp"]), H4) == (None, "ROWS_NOT_CONTIGUOUS")
    assert D.window_from_rows(rows, 3_600_000) == (None, "ROWS_NOT_CONTIGUOUS")
    assert D.window_from_rows(edit(5, timestamp=None), H4) == (None, "ROWS_UNREADABLE")
    for bad in (dict(close=float("nan")), dict(open=None), dict(high=float("inf")), dict(low="x"), dict(close=True),
                dict(high=99.0), dict(low=101.0)):
        assert D.window_from_rows(edit(123, **bad), H4) == (None, "ROWS_UNREADABLE"), bad
    missing_low = [dict(r) for r in rows]
    del missing_low[7]["low"]
    assert D.window_from_rows(missing_low, H4) == (None, "ROWS_UNREADABLE")
    no_vol = [{k: v for k, v in r.items() if k != "volume"} for r in rows]
    assert D.window_from_rows(no_vol, H4) == (None, "VOLUME_MISSING")
    for bad in (None, float("nan"), float("inf"), -1.0, "x"):
        assert D.window_from_rows(edit(499, volume=bad), H4) == (None, "VOLUME_MISSING"), bad
    with pytest.raises(ValueError):
        D.Window(ts=win.ts[1:], o=win.o[1:], h=win.h[1:], l=win.l[1:], c=win.c[1:], v=win.v[1:], step_ms=H4)


# ================================================================== 11) geleceğe bakış yok
def test_no_lookahead():
    """Laboratuvar pencereyi tam serinin numpy GÖRÜNÜMÜNDEN kurar: k barından sonraki bellek değişse ya da seri uzasa bile
    k'de biten pencerenin kararı ve açıklaması değişmez."""
    rows = _rw_rows(7, n=620)
    full = {"ts": np.array([r["timestamp"] for r in rows], dtype=np.int64)}
    for k, col in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")):
        full[k] = np.array([r[col] for r in rows], dtype=float)

    def at(arr, k):
        s = slice(k - W + 1, k + 1)
        return D.Window(ts=arr["ts"][s], o=arr["o"][s], h=arr["h"][s], l=arr["l"][s], c=arr["c"][s], v=arr["v"][s], step_ms=H4)

    loose = _var([{"color": "bull", "body_range": [0.4, None]}], context={"rsi14": [None, 80]})
    brk = _var([{"range_atr": [None, 0.8]}], confirm={"kind": "break", "within_bars": 2})
    ks = list(range(W - 1, 620 - 1, 4))
    hits = 0
    for var in (loose, brk):
        for k in ks:
            ref_hit, ref_exp = D.detect_last(at(full, k), var), D.explain_last(at(full, k), var)
            hits += ref_hit is not None and var is loose
            fut = {key: a.copy() for key, a in full.items()}
            for key in ("o", "h", "l", "c"):
                fut[key][k + 1:] *= 3.0                    # gelecek tamamen farklı
            fut["v"][k + 1:] = 1e9
            longer = {key: np.concatenate([a, a[-50:] + (50 * H4 if key == "ts" else 0)]) for key, a in fut.items()}
            for other in (fut, longer):
                assert D.detect_last(at(other, k), var) == ref_hit, (k, var.id)
                assert D.explain_last(at(other, k), var) == ref_exp
    assert hits >= 3, "gevşek varyasyon bu seride birkaç kez eşleşmeli (test boş geçmesin)"


def test_no_lookahead_lab_events_equal_on_every_cut_point():
    """`candle_lab.variation_events(df[:m])` = tam serinin i < m olayları (aynı stop, aynı ATR); gelecek değişse de aynı.
    Plasebo barları yalnız zaman damgasından seçilir: seri uzunluğuna bağlı değildir."""
    from tradingbot import candle_lab as CL
    df = pd.DataFrame(_rw_rows(9, n=760))
    loose = _var([{"color": "bull", "body_range": [0.4, None]}], context={"rsi14": [None, 80]})
    brk = _var([{"range_atr": [None, 0.8]}], confirm={"kind": "break", "within_bars": 2},
               context={"prior_move": {"bars": 5, "min": -50.0}})
    key = lambda e: (e.family, e.name, e.side, e.i, e.t_ms, e.stop)  # noqa: E731
    cut = 650
    fut = df.copy()
    fut.loc[cut:, ["open", "high", "low", "close"]] *= 3.0          # gelecek tamamen farklı
    fut.loc[cut:, "volume"] = 1e9
    for var in (loose, brk):
        full, atr_full = CL.variation_events(df, "X/USDT", "4h", var)
        assert sum(e.family == "candle_var" for e in full) >= 3 and sum(e.family == "placebo" for e in full) >= 3, var.id
        assert all(e.t_ms == int(df["timestamp"].iloc[e.i]) + H4 and e.trigger is None and e.target is None for e in full)
        for m in (W, W + 1, W + 57, len(df) - 1):
            part, atr_part = CL.variation_events(df.iloc[:m].reset_index(drop=True), "X/USDT", "4h", var)
            assert sorted(map(key, part)) == sorted(key(e) for e in full if e.i < m), (var.id, m)
            assert all(atr_part[e.i] == atr_full[e.i] for e in part)
        mut, atr_mut = CL.variation_events(fut, "X/USDT", "4h", var)
        assert sorted(key(e) for e in mut if e.i < cut) == sorted(key(e) for e in full if e.i < cut)
        assert all(atr_mut[e.i] == atr_full[e.i] for e in mut if e.i < cut)
        # plasebo: zaman damgasıyla seçilen barlar ∩ bağlam; stop = c[j] − q × ATR14(j), q (ve kırılımda gecikme) j'den
        # ÖNCEKİ gerçek isabetlerden birinin risk/ATR'si (seçim zaman damgasından); aynı yön
        sel = {j for j in range(W - 1, len(df))
               if L._h("X/USDT", "4h", CL.placebo_name(var.id), int(df["timestamp"].iloc[j])) < CL.PLACEBO_P}
        plc = [e for e in full if e.family == "placebo"]
        assert {e.i for e in plc} <= sel and all(e.name == "PLACEBO_" + var.id and e.side == var.side for e in plc)
        close = df["close"].to_numpy(dtype=float)
        real = sorted((e for e in full if e.family == "candle_var"), key=lambda e: e.i)
        for e in plc:
            before = [r for r in real if r.i < e.i]
            assert before, "gerçek isabetten önce plasebo yok"
            r = before[int(L._h("X/USDT", "4h", CL.placebo_name(var.id), "q", int(df["timestamp"].iloc[e.i])) * len(before))]
            q = (close[r.i] - r.stop) / atr_full[r.i]
            assert e.stop == close[e.i] - q * atr_full[e.i] and 0 < e.stop < close[e.i]
            win_j = CL.window_at(CL._arrays(df), e.i, H4)
            lag = r.i - D.detect_last(CL.window_at(CL._arrays(df), r.i, H4), var).p
            assert D.placebo_context(win_j, var, p=W - 1 - lag) == (True, atr_full[e.i])
    # pencerede zaman boşluğu olan bar (canlıda ROWS_NOT_CONTIGUOUS) laboratuvarda da değerlendirilmez
    gap = df.copy()
    gap.loc[600:, "timestamp"] += H4
    evs, _ = CL.variation_events(gap, "X/USDT", "4h", loose)
    assert evs and all(e.i < 600 or e.i >= 600 + W - 1 for e in evs)


def test_placebo_matches_real_risk_geometry_on_drifting_random_walk(monkeypatch):
    """Mum şekli stop mesafesini de belirler (boğa gövdesi kapanışı dipten uzaklaştırır; kırılım barı formasyon tepesinin
    üstünde kapanır). Plasebo stop kuralını son n bara uygulasaydı stop'u daha dar olurdu: R cinsinden maliyeti yüksek,
    sürüklenmesi farklı; `vs_placebo` şeklin değil stop geometrisinin farkını ölçerdi ve yukarı sürüklenen AVANTAJSIZ veride
    (+0,0005/bar) boğa mumu ve iç mum kırılımı GÜÇLÜ ADAY çıkıyordu (vs ≈ +0,17 / +0,21). Onarım: plasebonun risk/ATR'si
    gerçek isabetlerin dağılımından. Tohumlar SABİT (6 × 3000 bar); ölçü: ortalama risk/ATR %5 içinde (ikisi de); boğa
    mumunda (çok işlem) ayrıca |vs| < 0,08 ve GÜÇLÜ ADAY değil. Kırılımda işlem az, vs gürültülü: yalnız geometri."""
    import dataclasses

    def entry(vid, bars, **kw):
        return {"id": vid, "definition": {"side": "LONG", "timeframe": "4h", "bars": bars, "relations": kw.get("rel", []),
                                          "confirm": kw.get("confirm", {"kind": "pattern_close"}),
                                          "stop": {"anchor": "pattern", "atr_buffer": 0.25}, "exit": {"target_r": 2.0, "max_hold_bars": 24}}}
    bull = entry("CV971_RW_BULL", [{"color": "bull"}])
    brk = entry("CV972_RW_INSIDE_BREAK", [{}], rel=["c0.high <= max_high(-1..-1)", "c0.low >= min_low(-1..-1)"],
                confirm={"kind": "break", "within_bars": 3})
    monkeypatch.setattr(V, "VARIATIONS", (V.CV000_EXAMPLE_BULL3, bull, brk))
    V.reset_cache()
    cfg = L.LabConfig()
    try:
        for e in (bull, brk):
            vid, evs, risk = e["id"], [], {"candle_var": [], "placebo": []}
            for seed in range(6):
                df = pd.DataFrame(_rw_rows(seed, n=3000, drift=0.0005))
                done, meta = L.process_series(df, "S%d/USDT" % seed, "4h", cfg, catalog=False, algos=False, extras=False,
                                              variations=(vid,))
                # tam serinin ATR14'ü = 500 barlık pencerenin ATR14'ü (Wilder başlangıcının etkisi 486 barda sönmüş)
                atr, o = L.indicators(df)["atr"], df["open"].to_numpy(dtype=float)
                for x in done:
                    risk[x.family].append((o[x.i + 1] - x.stop) / atr[x.i])
                evs += [dataclasses.asdict(x) for x in done]
            real, plc = float(np.mean(risk["candle_var"])), float(np.mean(risk["placebo"]))
            assert len(risk["placebo"]) >= 500 and abs(plc / real - 1.0) < 0.05, (vid, real, plc)
            g = next(x for x in L.aggregate(evs, cfg)["groups"] if x["name"] == vid and x["context"] == "HEPSİ")
            vs = g["vs_placebo"]
            assert vs is not None, vid
            if e is bull:
                assert abs(vs["IS"]) < 0.08 and abs(vs["OOS"]) < 0.08, (vid, vs, g["verdict"])
                assert g["verdict"] != L.V_STRONG, (vid, g["verdict"])
    finally:
        V.reset_cache()


# ================================================================== 12-13) kayıt
@pytest.mark.parametrize("vid", [e["id"] for e in V.VARIATIONS])
def test_registry_examples(vid):
    assert vid in EXAMPLES, "her kayıt kimliğinin örneği olmalı (tests/candle_variation_examples.py)"
    var = V.get(vid)
    ex = EXAMPLES[vid]
    rows = pad(ex["match"])
    win, why = D.window_from_rows(rows, H4)
    assert why is None
    hit = D.detect_last(win, var)
    assert hit is not None and hit.i == W - 1 and hit.p == W - 1 - int(ex.get("p_back", 0))
    a_i = float(L.indicators(pd.DataFrame(rows))["atr"][-1])
    end = hit.p if var.stop_anchor == "pattern" else hit.i
    seg = rows[hit.p0:end + 1]
    expected = (min(r["low"] for r in seg) - var.atr_buffer * a_i if var.side == "LONG"
                else max(r["high"] for r in seg) + var.atr_buffer * a_i)
    assert hit.atr_i == a_i and hit.stop == pytest.approx(expected, abs=1e-12)
    e = D.explain_last(win, var)
    assert e["matched"] and e["first_fail"] is None and all(r["ok"] for r in e["candidates"][0]["clauses"])
    assert e["hit"]["stop"] == hit.stop and e["definition_sha"] == var.definition_sha and e["dsl_version"] == D.DSL_VERSION
    json.dumps(e, allow_nan=False)
    assert ex["near_miss"], "en az bir kıl payı kaçan örnek"
    for clause, seq in ex["near_miss"].items():
        w, why = D.window_from_rows(pad(seq), H4)
        assert why is None
        assert D.detect_last(w, var) is None, clause
        assert D.explain_last(w, var)["first_fail_clause"] == clause


def _record(var_or_entry, **over) -> dict:
    rec = {"record_schema": "candle_lab/1", "id": CV0, "definition_sha": D.definition_sha(var_or_entry), "dsl_version": D.DSL_VERSION,
           "window": D.WINDOW, "verdict": L.V_STRONG, "run": {"github_run_id": "1", "run_url": "https://example.invalid/1",
                                                              "commit": "abc", "completed_at": "2026-09-27T10:00:00Z",
                                                              "mode": {"only_variations": True}},
           "readback": (var_or_entry or {}).get("readback"), "primary_tf": "4h", "by_tf": {"4h": {"OOS": {"n": 0}}}}
    rec.update(over)
    return rec


def test_registry_parses_ids_unique_and_example_never_passes_gate(tmp_path, monkeypatch):
    ids = [e["id"] for e in V.VARIATIONS]
    assert len(ids) == len(set(ids)), "kimlikler tekil"
    for k, e in enumerate(V.VARIATIONS):
        var = D.parse_variation(e)
        assert D.ID_PATTERN.fullmatch(var.id) and var.definition_sha == D.definition_sha(e) and var.timeframe == "4h"
        if e.get("supersedes"):
            assert e["supersedes"] in ids[:k], "yerine geçtiği kimlik daha önce eklenmiş olmalı"
    assert [e["id"] for e in V.VARIATIONS if e.get("example")] == [CV0]
    assert V.gate(CV0) == (None, "EXAMPLE") and V.get(CV0).example is True
    # gerçek kayıttan bağımsız (kullanıcı varyasyonu eklenince de geçer): örnek olmayan her kayıt bir denemedir
    assert V.trials_to_date() == sum(1 for e in V.VARIATIONS if not e.get("example"))
    # tam onaylı, CI kayıtlı ve GÜÇLÜ ADAY olsa bile örnek kapıdan geçmez; örnek olmayan aynısı geçer
    full = copy.deepcopy(V.CV000_EXAMPLE_BULL3)
    full.update(readback={"confirmed_by": "user", "date": "2026-09-26"},
                approval={"by": "user", "date": "2026-09-28", "observation": False, "run_id": "1"})
    (tmp_path / ("%s.json" % CV0)).write_text(json.dumps(_record(full)), encoding="utf-8")
    monkeypatch.setattr(V, "LAB_RECORDS_DIR", tmp_path)
    monkeypatch.setattr(V, "VARIATIONS", (full,))
    assert V.gate(CV0) == (None, "EXAMPLE")
    monkeypatch.setattr(V, "VARIATIONS", (dict(full, example=False),))
    var, why = V.gate(CV0)
    assert why is None and var.id == CV0 and not var.example
    assert V.trials_to_date() == 1


# ================================================================== 14) bozuk kayıt hiçbir şeyi durdurmaz
def test_broken_entry_never_raises(tmp_path, monkeypatch):
    broken = dict(copy.deepcopy(V.CV000_EXAMPLE_BULL3), id="CV998_BROKEN", example=False)
    broken[DF]["bars"][0]["wick"] = [0, 1]
    typo = {"id": "CV997_TYPO", DF: {"side": "LONG"}}
    wrong_type = dict(copy.deepcopy(V.CV000_EXAMPLE_BULL3), id="CV996_TYPE", example=False)
    wrong_type[DF]["context"]["trend"] = [["with"]]
    dup = dict(copy.deepcopy(V.CV000_EXAMPLE_BULL3), id="CV995_DUP", example=False)
    # kayıt SABİTLENİR (gerçek kayda kullanıcı varyasyonu eklense de sayılar değişmez)
    monkeypatch.setattr(V, "VARIATIONS", (V.CV000_EXAMPLE_BULL3, broken, typo, wrong_type, dup, copy.deepcopy(dup), "x", None,
                                          {"no_id": 1}, {"id": 5}))
    for vid in ("CV998_BROKEN", "CV997_TYPO", "CV996_TYPE", "CV995_DUP"):
        assert V.gate(vid) == (None, "DEFINITION_INVALID"), vid
        with pytest.raises(ValueError, match="DEFINITION_INVALID"):
            V.get(vid)
    for vid in ("CV999_MISSING", None, 5, ["x"], {"id": CV0}):
        assert V.gate(vid) == (None, "UNKNOWN_ID"), vid
    assert V.gate(CV0) == (None, "EXAMPLE"), "diğer kayıtlar etkilenmez"
    assert V.trials_to_date() == 5, "bozuk ve tekrarlanan kayıtlar da deneme sayılır; kimliksiz çöp sayılmaz"
    # bozuk kayıt DOSYASI da yalnız o varyasyonu kapatır
    ok_entry = dict(copy.deepcopy(V.CV000_EXAMPLE_BULL3), example=False, readback={"confirmed_by": "user", "date": "2026-09-26"})
    monkeypatch.setattr(V, "VARIATIONS", (ok_entry,))
    monkeypatch.setattr(V, "LAB_RECORDS_DIR", tmp_path)
    for content in ("{bozuk", "[]", json.dumps({"id": "CV111_OTHER"})):
        (tmp_path / ("%s.json" % CV0)).write_text(content, encoding="utf-8")
        assert V.gate(CV0) == (None, "NO_LAB_RECORD"), content
    monkeypatch.setattr(V, "LAB_RECORDS_DIR", tmp_path / "yok" / "\0geçersiz")
    assert V.gate(CV0) == (None, "NO_LAB_RECORD")

    # içe aktarma hiçbir şeyi ayrıştırmaz: ayrıştırıcı patlasa bile modül yüklenir, kapı yalnız kod döner
    def boom(_d):
        raise RuntimeError("boom")
    monkeypatch.setattr(D, "parse_variation", boom)
    spec = importlib.util.spec_from_file_location("tradingbot._candle_variations_probe", V.__file__)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    # deneme sayısı ayrıştırmadan sayılır: ayrıştırıcı patlasa da gerçek kaydın örnek olmayan kayıtları
    assert probe.gate(CV0) == (None, "DEFINITION_INVALID")
    assert probe.trials_to_date() == sum(1 for e in probe.VARIATIONS if isinstance(e, dict) and e.get("example") is not True)
    # ... ve kâğıt defter kaydı ile D4 kararı çalışmaya devam eder
    paper_rules = importlib.import_module("tradingbot.paper_rules")
    closes = [100.0 + (0.5 if k % 2 else -0.5) for k in range(79)] + [104.0]
    rows, prev = [], closes[0]
    for k, c in enumerate(closes):
        rows.append({"timestamp": T_LAST - (79 - k) * H4, "open": prev, "high": max(prev, c) + 1.0, "low": min(prev, c) - 1.0,
                     "close": c, "volume": 1.0})
        prev = c
    act = paper_rules.decide_from_rows("d4_donchian_20_10", daily=[], intraday=rows, btc_rows=None, now_ms=T_LAST + H4 + 60_000)
    assert act and act["action"] == "OPEN" and act["setup_type"] == "trend_donchian"


# ================================================================== 15) altın özet
def test_golden_sha_matches_every_lab_record(monkeypatch):
    """Her laboratuvar kaydı (bu DSL sürümünün) aynı tanım mührünü ve aynı altın özeti taşır: tanım elle düzenlenmişse ya da
    dedektörün anlamı DSL_VERSION artırılmadan değişmişse CI düşer. Kayıt yokken boş geçer; özet belirlenimci ve anlama duyarlı."""
    from tradingbot import candle_lab as CL
    for p in sorted(Path(V.LAB_RECORDS_DIR).glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        assert rec.get("record_schema") == CL.RECORD_SCHEMA and rec.get("id") == p.stem, p.name
        var = V.get(rec["id"])
        if rec.get("dsl_version") != D.DSL_VERSION or rec.get("window") != D.WINDOW:
            continue                                      # eski sürümün kaydı: kapı zaten reddeder (DSL_VERSION_MISMATCH)
        assert rec["definition_sha"] == var.definition_sha, "%s: tanım değişmiş — yeni kimlik gerekir" % p.name
        assert rec["golden_sha"] == CL.golden_sha(var), "%s: dedektör anlamı DSL_VERSION artırılmadan değişti" % p.name
    var = V.get(CV0)
    g = CL.golden_sha(var)
    assert re.fullmatch(r"[0-9a-f]{16}", g) and CL.golden_sha(V.get(CV0)) == g
    corpus = CL.golden_corpus()
    assert len(corpus) == 3 and all(len(a["ts"]) == 1200 and bool(np.all(np.diff(a["ts"]) == H4)) for a in corpus)
    assert all(CL.valid_ends(a, H4)[W - 1:].all() for a in corpus), "derlem canlı pencere denetimlerinden geçer"
    orig = D._vol_ratio
    monkeypatch.setattr(D, "_vol_ratio", lambda win, ind, b: orig(win, ind, b) * 1.01)
    assert CL.golden_sha(var) != g, "koşul anlamı değişince özet değişir"
