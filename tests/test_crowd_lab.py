# -*- coding: utf-8 -*-
"""Kalabalık laboratuvarı (fut_v2, docs/CROWD_LAB_FUT_V2.md; 2026-09-30): ön kayıt mührü, 4 kural × 2 yön ve kontrolleri
(tam tümleyen: ayrık, birleşim = tetik ∩ OK), ayna yönler, NaN hiçbir şeyi tetiklemez, plasebolar (belirlenimci, maskeli,
gerçek sinyalden ve seri uzunluğundan bağımsız), nedensellik, katkı etiketleri ve defter adaylığı, takip/karşı satırları,
kör sayım (VERİ AZ tasarım gereği), rastgele yürüyüş kalibrasyonu, uçtan uca (sahte arşiv) ve komut satırı.
Ağ YOK: sahte `fetch`, sentetik zip."""
from __future__ import annotations

import ast
import dataclasses
import gzip
import hashlib
import importlib.util
import io
import json
import re
import threading
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot import crowd_data as CD
from tradingbot import crowd_features as CF
from tradingbot import crowd_lab as CL
from tradingbot import futures_features as FF
from tradingbot import futures_lab as FL
from tradingbot import signal_lab as L

DAY = 86_400_000
HOUR = 3_600_000
H4 = 4 * HOUR
T0 = 1_700_006_400_000                                         # gün başı (UTC)
ROOT = Path(__file__).resolve().parents[1]

#: ön kayıt mührü — bir sabit, kural metni, plasebo tanımı, çıkış/maliyet ayarı, özellik sabiti ya da sütun eşlemesi
#: değişirse bu test KIRILIR (yeni sürüm + belge + yeni deneme sayısı; sonuç görüldükten sonra gevşetme YOK)
PINNED_SHA = "14cbf0523ef25ef1"


# ---------------------------------------------------------------------------- yardımcılar
def neutral(n: int = 300, step: int = H4):
    """Hiçbir kuralın tetiklenmediği düz seri + elle kurulan gösterge/yardımcı/kalabalık dizileri (birim testleri)."""
    ts = T0 + np.arange(n, dtype=np.int64) * step
    df = pd.DataFrame({"timestamp": ts, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0})
    ind = {"atr": np.full(n, 1.0), "ema50": np.full(n, 99.0), "ema200": np.full(n, 98.0), "vol_avg": np.full(n, 50.0)}
    aux = {"hi20": np.full(n, 101.0), "lo20": np.full(n, 99.0)}
    feat = {"flow_ok": np.ones(n, dtype=bool), "pos_ok": np.ones(n, dtype=bool), "flow_side": np.zeros(n), "pos_side": np.zeros(n)}
    return df, ind, aux, feat


def real(evs, *names):
    return sorted((e.family, e.name, e.side, e.i, round(e.stop, 9), e.trigger, json.dumps(e.exit)) for e in evs
                  if e.family != "placebo" and (not names or e.name in names))


def setbar(df, i, **kw):
    for k, v in kw.items():
        df.loc[i, k] = v


BRK = ("CROWD_BRK_FLOW_FOLLOW", "CROWD_BRK_FLOW_CONTRA")
SWEEP = ("CROWD_SWEEP_POS_CONTRA", "CROWD_SWEEP_POS_FOLLOW")
CTRL_BRK = tuple("CTRL_" + r for r in BRK)
EXIT = json.dumps(CL.BRK_EXIT)


# ---------------------------------------------------------------------------- mühür
def test_registry_sha_is_pinned_documented_and_fut_v1_seal_unchanged():
    again = hashlib.sha256(json.dumps(CL.CROWD_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    assert CL.CROWD_REGISTRY_SHA == again == PINNED_SHA, "kayıt değişti: yeni sürüm + belge + yeni deneme sayısı gerekir"
    doc = (ROOT / "docs" / "CROWD_LAB_FUT_V2.md").read_text(encoding="utf-8")
    assert PINNED_SHA in doc and "fut_v2" in doc and CL.CROWD_VERSION == "fut_v2"
    assert FL.FUT_REGISTRY_SHA == "6f08eca51d8b97ee", "fut_v1 mührü DEĞİŞMEZ"
    R = CL.CROWD_REGISTRY
    assert R["hypotheses"] == [f"4h {r} {s} HEPSİ" for r in CL.RULES for s in (L.LONG, L.SHORT)] and len(R["hypotheses"]) == 8
    assert R["features"] == CF.CROWD_FEATURE_CONSTANTS and R["fut_features"] == FF.FEATURE_CONSTANTS
    assert R["column_map"] == CD.COLUMN_MAP and R["warmup_days"] == 22 and R["vol_agree_rtol"] == CD.VOL_AGREE_RTOL
    assert R["trials"] == {"families": 2, "hypotheses": 16, "tr": CL.TRIALS["tr"]} and (R["primary_tf"], R["tfs"]) == ("4h", ["1h", "4h"])
    assert set(R["rules"]) == set(CL.RULES) and R["alignment_tr"] and R["placebo"]["rate"] == 0.01 and R["placebo"]["sim_rate"] == 0.02
    for path, val in ((("rules", "CROWD_BRK_FLOW_FOLLOW", "stop_atr"), 2.5), (("features", "CROWD_Z"), 1.5),
                      (("column_map", "metrics_header", "gls"), "count_toptrader_long_short_ratio"), (("placebo", "rate"), 0.02),
                      (("exit_cfg", "default_rr"), 3.0), (("fut_features", "OI_LAG_MS"), 0)):
        tweak = json.loads(json.dumps(R))
        node = tweak
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = val
        assert hashlib.sha256(json.dumps(tweak, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16] != PINNED_SHA, path


def test_registry_pins_exit_and_cost_config_used_by_simulate():
    ex, cfg = CL.CROWD_REGISTRY["exit_cfg"], dataclasses.asdict(L.LabConfig())
    assert {k: ex[k] for k in ex if k not in ("tr", "rule_stop_too_far_atr")} == {k: cfg[k] for k in ex if k in cfg}
    assert ex["rule_stop_too_far_atr"] == 10 and ex["fee_pct"] == 0.05 and ex["slippage_bps"] == 3.0
    st = CL.CROWD_REGISTRY["stats"]
    assert (st["min_is"], st["min_oos"], st["split"], st["strict_seed"]) == (30, 20, 2 / 3, L.STRICT_SEED)


# ---------------------------------------------------------------------------- kurallar ve kontroller
def test_breakout_follow_contra_rule_control_mirror_and_every_condition():
    def run(side=1.0, ok=True, d=1, atr=1.0):
        df, ind, aux, feat = neutral()
        if d > 0:
            setbar(df, 240, close=102.0, high=102.5)
        else:
            setbar(df, 240, close=98.0, low=97.5)
        feat["flow_side"][240], feat["flow_ok"][240] = side, ok
        ind["atr"][240] = atr
        return real(CL.events(df, "X/USDT", "4h", ind, aux, feat, {}))
    up = [("crowd", "CROWD_BRK_FLOW_CONTRA", L.SHORT, 240, 102.75, 102.0, "{}"),
          ("crowd", "CROWD_BRK_FLOW_FOLLOW", L.LONG, 240, 100.0, None, EXIT)]
    assert run() == up, "yukarı kırılım + kalabalık long giriyor → TAKİP LONG (c − 2 ATR, kanal) ve KARŞI SHORT (h + 0,25 ATR)"
    ctrl = [("control", "CTRL_" + n, s, 240, st, tr, ex) for (_, n, s, _, st, tr, ex) in up]
    assert run(side=0.0) == sorted(ctrl) and run(side=-1.0) == sorted(ctrl), "akış yok / ters: kontrol (aynı tetik, yön, stop)"
    down = [("crowd", "CROWD_BRK_FLOW_CONTRA", L.LONG, 240, 97.25, 98.0, "{}"),
            ("crowd", "CROWD_BRK_FLOW_FOLLOW", L.SHORT, 240, 100.0, None, EXIT)]
    assert run(side=-1.0, d=-1) == down, "ayna: aşağı kırılım + kalabalık short giriyor"
    assert run(side=1.0, d=-1) == sorted(("control", "CTRL_" + n, s, 240, st, tr, ex) for (_, n, s, _, st, tr, ex) in down)
    assert run(ok=False) == [] and run(ok=False, d=-1) == [], "akış bilinmiyor (flow_ok yanlış) → ne kural ne kontrol"
    assert run(atr=np.nan) == [] and run(atr=0.0) == [], "ATR geçersiz"
    df, ind, aux, feat = neutral()
    setbar(df, 239, close=101.5)
    setbar(df, 240, close=102.0, high=102.5)
    feat["flow_side"][240] = 1.0
    got = real(CL.events(df, "X/USDT", "4h", ind, aux, feat, {}), *BRK, *CTRL_BRK)
    assert {x[3] for x in got} == {239}, "240 ilk kapanış değil (c[239] > hi20[239]); 239 kendisi ilk kapanış (kontrol)"
    df, ind, aux, feat = neutral()
    setbar(df, 200, close=102.0, high=102.5)
    feat["flow_side"][200] = 1.0
    assert real(CL.events(df, "X/USDT", "4h", ind, aux, feat, {})) == [], "i < 210"


def test_sweep_contra_follow_rule_control_mirror_and_geometry():
    def run(side=1.0, ok=True, **bar):
        df, ind, aux, feat = neutral()
        setbar(df, 250, **bar)
        feat["pos_side"][250], feat["pos_ok"][250] = side, ok
        return real(CL.events(df, "X/USDT", "4h", ind, aux, feat, {}), *SWEEP, *("CTRL_" + r for r in SWEEP))
    top = dict(high=102.0)                                      # o = c = 100, l = 100: üst fitil 2 = aralık, c < hi20
    want = [("crowd", "CROWD_SWEEP_POS_CONTRA", L.SHORT, 250, 102.25, 100.0, "{}"),
            ("crowd", "CROWD_SWEEP_POS_FOLLOW", L.LONG, 250, 99.75, 100.0, "{}")]
    assert run(**top) == want, "tepe süpürmesi + kalabalık long konumlu (tuzak) → KARŞI SHORT, TAKİP LONG"
    ctrl = sorted(("control", "CTRL_" + n, s, 250, st, tr, ex) for (_, n, s, _, st, tr, ex) in want)
    assert run(side=0.0, **top) == ctrl and run(side=-1.0, **top) == ctrl
    bot = dict(low=98.0)
    want_b = [("crowd", "CROWD_SWEEP_POS_CONTRA", L.LONG, 250, 97.75, 100.0, "{}"),
              ("crowd", "CROWD_SWEEP_POS_FOLLOW", L.SHORT, 250, 100.25, 100.0, "{}")]
    assert run(side=-1.0, **bot) == want_b, "ayna: dip süpürmesi + kalabalık short konumlu"
    assert run(ok=False, **top) == [] and run(side=np.nan, ok=False, **bot) == [], "konum bilinmiyor → hiçbir şey"
    assert run(high=101.2, low=99.5, open=100.0, close=100.9) == [], "üst fitil 0,3 < 0,5·aralık"
    assert run(high=102.0, close=101.5, open=101.5) == [], "kapanış kanalın dışında (c ≥ hi20) → süpürme değil"
    assert run(high=101.0) == [], "h = hi20 (kesin büyük değil)"


def test_nan_features_never_fire_and_unknown_is_not_zero():
    df, ind, aux, feat = neutral()
    setbar(df, 240, close=102.0, high=102.5)
    setbar(df, 250, high=102.0)
    feat["flow_side"][:], feat["pos_side"][:] = np.nan, np.nan
    feat["flow_ok"][:], feat["pos_ok"][:] = False, False
    evs = CL.events(df, "X/USDT", "4h", ind, aux, feat, {})
    assert evs == [], "bilinmeyen kalabalık: kural, kontrol ve plasebo YOK (NaN 0 sayılmaz)"
    aux["hi20"][240] = np.nan
    feat["flow_ok"][:] = True
    feat["flow_side"][:] = 0.0
    assert not real(CL.events(df, "X/USDT", "4h", ind, aux, feat, {}), *BRK, *CTRL_BRK), "kanal NaN → tetik yok"


# ---------------------------------------------------------------------------- sentetik dünya (fiyat + taker + metrics + fonlama)
def rw_prices(n: int, seed: int, step: int = H4, t0: int = T0) -> pd.DataFrame:
    rnd = np.random.default_rng(seed)
    ts = t0 + np.arange(n, dtype=np.int64) * step
    c = 100.0 * np.exp(np.cumsum(rnd.normal(0, 0.012, n)))
    o = np.concatenate([[100.0], c[:-1]])
    wig = np.abs(rnd.normal(0, 0.006, (2, n)))
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) * (1 + wig[0]), "low": np.minimum(o, c) * (1 - wig[1]),
                         "close": c, "volume": np.round(100.0 * np.exp(rnd.normal(0, 0.5, n)), 3), "close_time": ts + step - 1})


def rw_raw(df: pd.DataFrame, t_from: int, t_to: int, seed: int) -> dict:
    """Fiyattan BAĞIMSIZ rastgele taker payı, metrics (OI + dört oran, 5 dk) ve fonlama (8 saat)."""
    rnd = np.random.default_rng(seed + 10_000)
    m_t = np.arange(t_from - t_from % 300_000, t_to, 300_000, dtype=np.int64)
    out = {"m_t": m_t, "oi": 1e6 * np.exp(np.cumsum(rnd.normal(0, 0.003, m_t.size)))}
    for k in ("tals", "tpls", "gls", "tkls"):
        out[k] = np.exp(np.cumsum(rnd.normal(0, 0.004, m_t.size)))
    f_t = np.arange(t_from - t_from % (8 * HOUR), t_to, 8 * HOUR, dtype=np.int64)
    out.update(f_t=f_t + rnd.integers(0, 6, f_t.size), f_rate=rnd.normal(1e-4, 2e-4, f_t.size))
    v = df["volume"].to_numpy(dtype=float)
    out.update(k_t=df["timestamp"].to_numpy(dtype=np.int64), k_v=v, k_tb=np.round(v * rnd.uniform(0.3, 0.7, v.size), 3))
    return out


def pipeline(df, raw, symbol="X/USDT", tf="4h", skipped=None):
    feat = CL.features(df, tf, raw)
    return feat, CL.events(df, symbol, tf, L.indicators(df), L.aux_series(df), feat, skipped if skipped is not None else {})


def test_rule_and_control_are_an_exact_disjoint_complement_of_trigger_and_ok():
    seen = {r: [0, 0] for r in CL.RULES}
    for seed in range(1, 7):
        df = rw_prices(1500, seed)
        raw = rw_raw(df, T0 - 40 * DAY, T0 + 1510 * H4, seed)
        feat, evs = pipeline(df, raw)
        o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        atr, aux = L.indicators(df)["atr"], L.aux_series(df)
        hi20, lo20 = aux["hi20"], aux["lo20"]
        valid = np.zeros(len(df), dtype=bool)
        valid[CL.START_BAR:] = True
        valid &= np.isfinite(atr) & (atr > 0)
        with np.errstate(invalid="ignore"):
            up = (c > hi20) & (np.roll(c, 1) <= np.roll(hi20, 1))
            dn = (c < lo20) & (np.roll(c, 1) >= np.roll(lo20, 1))
            rng = h - lo
            s_up = (h > hi20) & (c < hi20) & (h - np.maximum(o, c) >= 0.5 * rng) & (rng > 0)
            s_dn = (lo < lo20) & (c > lo20) & (np.minimum(o, c) - lo >= 0.5 * rng) & (rng > 0)
        brk = valid & (up | dn) & feat["flow_ok"]
        swp = valid & (s_up | s_dn) & feat["pos_ok"]
        assert brk.sum() > 20 and swp.sum() > 20, "sentetik veride her tetik var"
        for rule, trig, key, t_up, t_dn in (("CROWD_BRK_FLOW_FOLLOW", brk, "flow_side", up, dn),
                                            ("CROWD_BRK_FLOW_CONTRA", brk, "flow_side", up, dn),
                                            ("CROWD_SWEEP_POS_CONTRA", swp, "pos_side", s_up, s_dn),
                                            ("CROWD_SWEEP_POS_FOLLOW", swp, "pos_side", s_up, s_dn)):
            follow = rule.endswith("FOLLOW")
            d_of = lambda side: (1.0 if side == L.LONG else -1.0) * (1.0 if follow else -1.0)  # noqa: E731 — TAKİP d, KARŞI −d
            mine = {(e.i, d_of(e.side)) for e in evs if e.name == rule}
            ctrl = {(e.i, d_of(e.side)) for e in evs if e.name == "CTRL_" + rule}
            assert not mine & ctrl, rule
            seen[rule][0] += len(mine)
            seen[rule][1] += len(ctrl)
            assert {i for i, _ in mine | ctrl} == set(np.flatnonzero(trig)), "kural ∪ kontrol = tetik ∩ OK"
            assert all((t_up if d > 0 else t_dn)[i] for i, d in mine | ctrl), "yön tetikten"
            assert all(feat[key][i] == d for i, d in mine) and all(feat[key][i] != d for i, d in ctrl)
            assert all(np.isfinite(feat[key][i]) for i, _ in mine | ctrl), "kontrol koşulu BİLİNEN ve yanlış"
        assert all(e.family in ("crowd", "control", "placebo") for e in evs)
    assert all(a > 0 and b > 0 for a, b in seen.values()), seen


def test_events_are_causal_and_prefix_invariant():
    n, cut = 1500, 1100
    df = rw_prices(n, 3)
    raw = rw_raw(df, T0 - 40 * DAY, T0 + (n + 2) * H4, 3)
    key = lambda es: [(e.family, e.name, e.side, e.i, e.t_ms, round(e.stop, 9), e.trigger, json.dumps(e.exit)) for e in es if e.i <= cut]  # noqa: E731
    full = key(pipeline(df, raw)[1])
    assert {x[0] for x in full} >= {"crowd", "control", "placebo"}
    t_cut = int(df["timestamp"][cut]) + H4
    pert = df.copy()
    pert.loc[cut + 1:, ["open", "high", "low", "close"]] *= 1.7
    pert.loc[cut + 1:, "volume"] *= 9.0
    r2 = {k: np.array(v, copy=True) for k, v in raw.items()}
    late = r2["m_t"] > t_cut - FF.OI_LAG_MS
    for k in CF.METRIC_FIELDS:
        r2[k][late] *= 3.0
    r2["f_rate"][r2["f_t"] > t_cut - FF.FUND_GUARD_MS] = 0.01
    r2["k_v"] = pert["volume"].to_numpy(dtype=float)
    r2["k_tb"][cut + 1:] = r2["k_v"][cut + 1:] * 0.99
    assert key(pipeline(pert, r2)[1]) == full, "gelecek fiyat/taker/metrics/fonlama i ≤ kesim olaylarını değiştirmez"
    short = df.iloc[:cut + 1].reset_index(drop=True)
    r3 = {k: v for k, v in raw.items()}
    for tk, cols in (("m_t", CF.METRIC_FIELDS), ("f_t", ("f_rate",)), ("k_t", ("k_v", "k_tb"))):
        m = raw[tk] <= t_cut
        r3[tk] = raw[tk][m]
        for c_ in cols:
            r3[c_] = raw[c_][m]
    assert key(pipeline(short, r3)[1]) == full, "kesilmiş seri aynı olayları verir"


# ---------------------------------------------------------------------------- plasebolar
def _plac(evs, name=None):
    return [(e.name, e.side, e.i, round(e.stop, 9), e.trigger, json.dumps(e.exit)) for e in evs
            if e.family == "placebo" and (name is None or e.name == name)]


def test_placebos_deterministic_masked_and_independent_of_real_signals_and_series_length():
    df = rw_prices(1500, 7)
    raw = rw_raw(df, T0 - 40 * DAY, T0 + 1510 * H4, 7)
    feat, evs = pipeline(df, raw)
    p = _plac(evs)
    assert p == _plac(pipeline(df, raw)[1]), "belirlenimci"
    names = {x[0] for x in p}
    assert names == {"PLACEBO_" + r for r in CL.RULES}
    atr = L.indicators(df)["atr"]
    for name, i in ((x[0], x[2]) for x in p):
        mask = feat["flow_ok"] if name in ("PLACEBO_CROWD_BRK_FLOW_FOLLOW", "PLACEBO_CROWD_BRK_FLOW_CONTRA") else feat["pos_ok"]
        assert mask[i] and i >= CL.START_BAR and atr[i] > 0, "yalnız OK maskesi ve geçerli ATR"
    brk_f = [x for x in p if x[0] == "PLACEBO_CROWD_BRK_FLOW_FOLLOW"]
    assert all(x[5] == EXIT for x in brk_f) and all(abs(abs(df["close"][x[2]] - x[3]) - 2 * atr[x[2]]) < 1e-9 for x in brk_f)
    assert 0.3 < len(brk_f) / feat["flow_ok"][CL.START_BAR:].sum() / 0.01 < 2.5, "≈ barların %1'i"
    # gerçek sinyaller değişse de (yön kodları, maske aynı) BRK_FOLLOW eşinin seçimi AYNI; simulate() eşlerinin seçimi (bar, yön)
    flip = {**feat, "flow_side": -feat["flow_side"], "pos_side": -feat["pos_side"]}
    evs2 = CL.events(df, "X/USDT", "4h", L.indicators(df), L.aux_series(df), flip, {})
    assert _plac(evs2, "PLACEBO_CROWD_BRK_FLOW_FOLLOW") == brk_f
    for r in CL.RULES[1:]:
        assert [(x[1], x[2]) for x in _plac(evs2, "PLACEBO_" + r)] == [(x[1], x[2]) for x in _plac(evs, "PLACEBO_" + r)], r
    # seri uzunluğundan bağımsız: kesilmiş seride j ≤ kesim eşleri aynı (risk yalnız ÖNCEKİ gerçek olaylardan)
    cut = 1000
    t_cut = int(df["timestamp"][cut]) + H4
    short = df.iloc[:cut + 1].reset_index(drop=True)
    r3 = dict(raw)
    for tk, cols in (("m_t", CF.METRIC_FIELDS), ("f_t", ("f_rate",)), ("k_t", ("k_v", "k_tb"))):
        m = raw[tk] <= t_cut
        r3[tk] = raw[tk][m]
        for c_ in cols:
            r3[c_] = raw[c_][m]
    assert _plac(pipeline(short, r3)[1]) == [x for x in p if x[2] <= cut]
    # başka sembol → başka seçim (karma sembole bağlı)
    assert _plac(pipeline(df, raw, symbol="Y/USDT")[1]) != p


def test_simulate_placebo_risk_comes_only_from_earlier_parents_else_no_real_risk():
    df, ind, aux, feat = neutral(600)
    skipped: dict[str, int] = {}
    evs = CL.events(df, "X/USDT", "4h", ind, aux, feat, skipped)
    assert not _plac(evs, "PLACEBO_CROWD_SWEEP_POS_CONTRA") and skipped.get("PLACEBO_CROWD_SWEEP_POS_CONTRA:NO_REAL_RISK", 0) > 0
    assert _plac(evs, "PLACEBO_CROWD_BRK_FLOW_FOLLOW"), "BRK_FOLLOW eşi riskini gerçek olaydan almaz (2 ATR)"
    setbar(df, 250, high=102.0)                                          # tek ebeveyn: tepe süpürmesi (q = 2,25 / 0,25)
    evs = CL.events(df, "X/USDT", "4h", ind, aux, feat, {})
    pc = [x for x in _plac(evs, "PLACEBO_CROWD_SWEEP_POS_CONTRA")]
    assert pc and all(x[2] > 250 for x in pc), "ebeveynden ÖNCEKİ bar eş almaz"
    for x in pc:
        want = 100.0 + 2.25 if x[1] == L.SHORT else 100.0 - 0.25
        assert x[3] == pytest.approx(want) and x[4] == 100.0, "q = aynı yönlü önceki gerçek olayın riski"


# ---------------------------------------------------------------------------- fonlama taşıma (bilgi)
def test_funding_carry_writes_only_crowd_names_and_default_stays_fut_v1():
    n = 60
    ts = T0 + np.arange(n, dtype=np.int64) * H4
    arr = {"open": np.full(n, 100.0), "close": np.full(n, 100.0)}
    f_t = T0 + np.arange(0, 40) * 8 * HOUR
    raw = {"f_t": f_t, "f_rate": np.full(f_t.size, 1e-4)}

    def ev(name):
        e = L.Event("X", "4h", "crowd", name, L.LONG, 10, 0, 99.0)
        e.hold, e.exit_reason = 6, "TIME"
        return e
    a, b, c = ev("CROWD_BRK_FLOW_CONTRA"), ev("OI_BREAKOUT_20"), ev("PLACEBO_CROWD_SWEEP_POS_FOLLOW")
    CL.funding_carry([a, b, c], arr, ts, H4, raw)
    assert a.funding_r == pytest.approx(-3 * 1e-4 * 100.0 / 1.0) and c.funding_r == a.funding_r and b.funding_r is None
    d, e = ev("CROWD_BRK_FLOW_CONTRA"), ev("OI_BREAKOUT_20")
    FL.funding_carry([d, e], arr, ts, H4, raw)
    assert d.funding_r is None and e.funding_r == pytest.approx(a.funding_r), "varsayılan names = fut_v1 adları (davranış aynı)"
    assert CL.CROWD_NAMES.isdisjoint(FL.FUT_NAMES) and len(CL.CROWD_NAMES) == 12


# ---------------------------------------------------------------------------- katkı, defter, takip/karşı, kör sayım
def _evs(name: str, family: str, rs, days, side: str = L.LONG, tf: str = "4h", fr=None) -> list[dict]:
    return [{"symbol": f"S{k % 3}", "tf": tf, "family": family, "name": name, "side": side,
             "t_ms": T0 + int(d) * DAY + (k % 6) * HOUR, "r": float(r), "cost_r": 0.05, "ctx": {"hacim": "normal"},
             "funding_r": (None if fr is None else fr[k % len(fr)])} for k, (r, d) in enumerate(zip(rs, days))]


def _span(rng, n: int, d0: int = 0, d1: int = 200) -> np.ndarray:
    days = rng.integers(d0, d1, n)
    days[0], days[-1] = d0, d1 - 1
    return days


def test_contributions_label_katki_books_pairs_render_and_log_lines():
    rng = np.random.default_rng(7)
    cfg = L.LabConfig()
    ev = []

    def case(name, side, rule_mean, ctrl, tf="4h", n_ctrl=300):
        rs, d = rng.normal(rule_mean, 1.0, 300), _span(rng, 300)
        ev.extend(_evs(name, "crowd", rs, d, side, tf, fr=[-0.01, float("nan")]))
        if ctrl == "drift":
            ev.extend(_evs("CTRL_" + name, "control", rs - 0.02, d, side, tf))
        else:
            ev.extend(_evs("CTRL_" + name, "control", rng.normal(ctrl, 1.0, n_ctrl), _span(rng, n_ctrl), side, tf))
        ev.extend(_evs("PLACEBO_" + name, "placebo", rng.normal(-0.3, 1.0, 300), _span(rng, 300), side, tf))
    case("CROWD_BRK_FLOW_FOLLOW", L.LONG, 0.5, 0.0)                 # KESİN → standart + sıkı
    case("CROWD_BRK_FLOW_CONTRA", L.SHORT, 0.5, 0.9)                # YOK (kontrol daha iyi) → fiyat-yalnız notu
    case("CROWD_SWEEP_POS_CONTRA", L.SHORT, 0.5, "drift")           # VAR → yalnız standart
    case("CROWD_SWEEP_POS_FOLLOW", L.LONG, 0.5, 0.0, n_ctrl=12)     # ÖLÇÜLEMEDİ
    case("CROWD_BRK_FLOW_FOLLOW", L.LONG, 0.5, 0.0, tf="1h")        # bilgi: aday olamaz
    ev += _evs("PLACEBO_RANDOM", "placebo", rng.normal(-2.0, 1.0, 300), _span(rng, 300), L.SHORT)
    ev += _evs("CROWD_SWEEP_POS_CONTRA", "crowd", rng.normal(0.5, 1.0, 300), _span(rng, 300), L.LONG)   # eşi yok
    agg = L.aggregate(ev, cfg)
    no_ctrl = L.aggregate([e for e in ev if e["family"] != "control"], cfg)
    assert agg["tested"] == no_ctrl["tested"] and agg["tf_summary"] == no_ctrl["tf_summary"], "kontrol hipotez değildir"
    g = lambda name, side, tf="4h": next(x for x in agg["groups"] if (x["name"], x["side"], x["tf"], x["context"]) == (name, side, tf, "HEPSİ"))  # noqa: E731
    assert g("CROWD_SWEEP_POS_CONTRA", L.LONG)["vs_placebo"] is None, "kalabalık kuralı PLACEBO_RANDOM'a DÜŞMEZ"
    assert g("CROWD_SWEEP_POS_CONTRA", L.LONG)["verdict"] != L.V_STRONG
    rows = {(r["tf"], r["name"], r["side"]): r for r in CL.contributions(ev, agg, cfg, ["4h", "1h"])}
    assert len(rows) == 16 and sum(1 for k in rows if k[0] == "4h") == 8, "4h'te her zaman 8 hipotez satırı"
    a, b = rows[("4h", "CROWD_BRK_FLOW_FOLLOW", L.LONG)], rows[("4h", "CROWD_BRK_FLOW_CONTRA", L.SHORT)]
    c, d = rows[("4h", "CROWD_SWEEP_POS_CONTRA", L.SHORT)], rows[("4h", "CROWD_SWEEP_POS_FOLLOW", L.LONG)]
    assert (a["katki"], b["katki"], c["katki"], d["katki"]) == (CL.KATKI_KESIN, CL.KATKI_YOK, CL.KATKI_VAR, CL.KATKI_NA)
    for r in (a, b, c):
        assert r["verdict"] == L.V_STRONG and r["vs_placebo"]["ci95_OOS"] is not None
    assert a["verdict_strict"] == L.V_STRONG and a["book"] == {"standard": True, "strict": True}
    assert b["book"] == {"standard": False, "strict": False} and b["note"] == CL.NOTE_PRICE_ONLY
    assert c["book"] == {"standard": True, "strict": False} and d["book"] == {"standard": False, "strict": False}
    info = rows[("1h", "CROWD_BRK_FLOW_FOLLOW", L.LONG)]
    assert info["katki"] == CL.KATKI_KESIN and not info["primary"] and info["book"] == {"standard": False, "strict": False}
    assert a["funding_r"] == {"IS": -0.01, "OOS": -0.01, "unknown_share": 0.5}
    # takip mi karşı mı: tetik × yön başına TAKİP ve KARŞI yan yana
    prs = CL.pairs(list(rows.values()))
    p4 = [p for p in prs if p["tf"] == "4h"]
    assert len(p4) == 4 and all(p["primary"] for p in p4) and all(p["answer"] == "bilgi" for p in prs if p["tf"] == "1h")
    up = next(p for p in p4 if p["trigger"] == "BRK" and p["d"] == 1)
    assert (up["follow"]["name"], up["follow"]["side"], up["contra"]["name"], up["contra"]["side"]) == (
        "CROWD_BRK_FLOW_FOLLOW", L.LONG, "CROWD_BRK_FLOW_CONTRA", L.SHORT)
    assert up["answer"] == "TAKİP (sıkı)"
    top = next(p for p in p4 if p["trigger"] == "SWEEP" and p["d"] == 1)
    assert (top["follow"]["side"], top["contra"]["side"]) == (L.LONG, L.SHORT) and top["answer"] == "KARŞI (standart)"
    dn = next(p for p in p4 if p["trigger"] == "BRK" and p["d"] == -1)
    assert dn["answer"] == "kanıt yok", "iki tarafta da aday yok"
    bot = next(p for p in p4 if p["trigger"] == "SWEEP" and p["d"] == -1)
    assert (bot["follow"]["name"], bot["follow"]["side"], bot["contra"]["side"]) == ("CROWD_SWEEP_POS_FOLLOW", L.SHORT, L.LONG)
    assert CL._answer({"book": {"strict": False, "standard": True}}, {"book": {"strict": False, "standard": False}}) == "TAKİP (standart)"
    assert CL._answer({"book": {"strict": False, "standard": False}}, {"book": {"strict": True, "standard": True}}) == "KARŞI (sıkı)"
    assert CL._answer({"book": {"strict": True, "standard": True}}, {"book": {"strict": True, "standard": True}}) == "ikisi de (çelişki)"
    # render: genel listelerde kontrol yok; kalabalık bölümü ayrı
    rep = {**agg, "symbols": ["S0", "S1", "S2"], "timeframes": ["4h", "1h"], "seconds": 0}
    txt = L.render(rep)
    assert "CTRL_" not in txt and "KALABALIK" not in txt
    rep["crowd"] = {"mode": "crowd", "version": CL.CROWD_VERSION, "registry_sha": CL.CROWD_REGISTRY_SHA, "coverage": {},
                    "contributions": list(rows.values()), "pairs": prs, "hypotheses": 8, "primary_tf": "4h", "trials": CL.TRIALS}
    txt = L.render(rep)
    assert "== KALABALIK (TAKİP / KARŞI) — ön kayıtlı 8 hipotez · fut_v2 · sha " + CL.CROWD_REGISTRY_SHA in txt
    assert CL.HYPOTHESIS_LINE in txt and "16 hipotez" in txt and "katkı KESİN" in txt and CL.NOTE_PRICE_ONLY in txt
    assert "takip mi karşı mı" in txt and "→ TAKİP (sıkı)" in txt and "(bilgi)" in txt and "VADELİ VERİ" not in txt
    lines = CL.log_lines(rep)
    assert lines[0].startswith("CROWD_REGISTRY ") and json.loads(lines[0][15:])["sha"] == PINNED_SHA
    assert sum(1 for x in lines if x.startswith("CROWD_RESULT ")) == 16 and sum(1 for x in lines if x.startswith("CROWD_PAIR ")) == 8
    assert all(len(x.encode("utf-8")) <= 4000 for x in lines)


def test_blind_counts_mark_thin_primary_rules_by_design_and_never_loosen():
    rng = np.random.default_rng(3)
    ev = []
    for name, n in (("CROWD_BRK_FLOW_FOLLOW", 200), ("CROWD_SWEEP_POS_CONTRA", 40), ("CTRL_CROWD_SWEEP_POS_CONTRA", 5),
                    ("PLACEBO_CROWD_SWEEP_POS_CONTRA", 3)):
        for side in (L.LONG, L.SHORT):
            ev += [{"tf": "4h", "name": name, "side": side, "t_ms": int(T0 + d * DAY)} for d in _span(rng, n)]
    ev += [{"tf": "1h", "name": "CROWD_SWEEP_POS_CONTRA", "side": L.LONG, "t_ms": T0 + k * DAY} for k in range(3)]
    ev += [{"tf": "4h", "name": "TREND_DONCHIAN_20_10", "side": L.LONG, "t_ms": T0 + 500 * DAY}]   # başka aile kesimi kaydırmaz
    b = CL.blind_counts(ev, "4h")
    assert b["cutoff_ms"] == int(T0 + L.LabConfig().split * 199 * DAY)
    rows = {(r["name"], r["side"]): r for r in b["rows"]}
    assert len(b["rows"]) == 24
    assert "note" not in rows[("CROWD_BRK_FLOW_FOLLOW", L.LONG)]
    thin = rows[("CROWD_SWEEP_POS_CONTRA", L.SHORT)]
    assert thin["IS"] + thin["OOS"] == 40 and thin["note"] == "VERİ AZ (tasarım gereği)"
    assert rows[("CROWD_BRK_FLOW_CONTRA", L.LONG)] == {"tf": "4h", "name": "CROWD_BRK_FLOW_CONTRA", "side": L.LONG, "IS": 0, "OOS": 0,
                                                       "note": CL.THIN_BY_DESIGN}
    assert "note" not in rows[("CTRL_CROWD_SWEEP_POS_CONTRA", L.LONG)] and "note" not in rows[("PLACEBO_CROWD_SWEEP_POS_CONTRA", L.LONG)]
    assert all("note" not in r for r in CL.blind_counts(ev, "1h")["rows"]), "1h bilgi: tasarım notu yalnız 4h kurallarında"
    loose = CL.blind_counts(ev, "4h", L.LabConfig(min_is=1, min_oos=1))
    assert "note" not in {(r["name"], r["side"]): r for r in loose["rows"]}[("CROWD_SWEEP_POS_CONTRA", L.SHORT)]
    assert CL.CROWD_REGISTRY["stats"]["min_is"] == 30 and CL.CROWD_REGISTRY["stats"]["min_oos"] == 20, "eşik mühürde"


# ---------------------------------------------------------------------------- rastgele yürüyüş kalibrasyonu
def test_random_walk_with_independent_crowd_data_gives_no_strong_candidate():
    evs = []
    for k in range(24):
        df = rw_prices(1300, 500 + k)
        raw = rw_raw(df, T0 - 40 * DAY, T0 + 1310 * H4, 500 + k)
        e, meta = L.process_series(df, f"R{k}/USDT", "4h", L.LabConfig(), catalog=False, algos=False, extras=False,
                                   futures="crowd", crowd_raw=raw)
        assert meta["crowd"]["bars_flow_ok"] > 900 and meta["crowd"]["bars_pos_ok"] > 900
        assert meta["crowd"]["taker"]["vol_mismatch"] == 0 and meta["crowd"]["taker"]["valid"] == 1300
        evs += [dataclasses.asdict(x) for x in e]
    assert {e["family"] for e in evs} == {"crowd", "control", "placebo"}
    assert all(set(e["ctx"]) == {"hacim", "rsi", "trend", "volatilite", "kalabalik_poz", "kalabalik_akis", "oi_ceyrek", "taker_24s"}
               for e in evs)
    agg = L.aggregate(evs, L.LabConfig())
    crowd = [g for g in agg["groups"] if g["family"] == "crowd"]
    assert crowd and not [g for g in crowd if g["verdict"] == L.V_STRONG and g["context"] == "HEPSİ"]
    assert not [g for g in crowd if g["verdict_strict"] == L.V_STRONG]
    rows = CL.contributions(evs, agg, L.LabConfig(), ["4h"])
    assert len(rows) == 8 and not [r for r in rows if r["book"]["standard"] or r["book"]["strict"]]
    assert all(e["funding_r"] is not None for e in evs), "kalabalık ailelerinin hepsine funding_r (bilinmeyen NaN)"


# ---------------------------------------------------------------------------- uçtan uca (sahte arşiv)
def _zip(text: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("x.csv", text)
    return out.getvalue()


class Archive:
    """data.binance.vision taklidi: metrics gün dosyaları (tam sütunlar), fonlama ayları ve taker'lı mum ay/gün dosyaları
    `raw` satırlarından ve fiyat çerçevelerinden üretilir; istekler kaydedilir; `dead` → her istek ConnectionError."""

    def __init__(self, raws: dict[str, dict], frames: dict[str, pd.DataFrame], dead: bool = False):
        self.raws, self.frames, self.dead, self.asked = raws, frames, dead, []
        self.lock = threading.Lock()

    def __call__(self, url: str):
        with self.lock:
            self.asked.append(url)
        if self.dead:
            raise ConnectionError(f"arşiv indirilemedi: {url}")
        name = url.rsplit("/", 1)[-1][:-4]
        sym = name.split("-")[0]
        raw = self.raws.get(sym)
        if raw is None or url.endswith(".CHECKSUM"):
            return None
        if "/daily/metrics/" in url:
            d = int(pd.Timestamp(name[-10:], tz="UTC").timestamp() * 1000)
            m = (raw["m_t"] >= d) & (raw["m_t"] < d + DAY)
            if not m.any():
                return None
            tt = pd.to_datetime(raw["m_t"][m], unit="ms").strftime("%Y-%m-%d %H:%M:%S")
            rows = [f"{t},{sym},{v:.4f},{v * 50:.2f},{a:.5f},{b:.5f},{g:.5f},{k:.5f}" for t, v, a, b, g, k in
                    zip(tt, raw["oi"][m], raw["tals"][m], raw["tpls"][m], raw["gls"][m], raw["tkls"][m])]
            return _zip("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
                        "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n" + "\n".join(rows) + "\n")
        if "/monthly/fundingRate/" in url:
            a = int(pd.Timestamp(name[-7:] + "-01", tz="UTC").timestamp() * 1000)
            m = (raw["f_t"] >= a) & (raw["f_t"] < L._next_month(a))
            if not m.any():
                return None
            rows = [f"{t},8,{r:.8f}" for t, r in zip(raw["f_t"][m], raw["f_rate"][m])]
            return _zip("calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(rows) + "\n")
        if "/klines/" in url:
            tf = url.split("/")[-2]
            df = self.frames.get(sym)
            if df is None or tf != "4h":
                return None
            stamp = name[len(sym) + len(tf) + 2:]
            a = int(pd.Timestamp(stamp + ("-01" if "/monthly/" in url else ""), tz="UTC").timestamp() * 1000)
            b = L._next_month(a) if "/monthly/" in url else a + DAY
            m = (df["timestamp"] >= a) & (df["timestamp"] < b)
            if not m.any():
                return None
            d = df[m]
            tb = raw["k_tb"][m.to_numpy()]
            rows = [f"{int(t)},{o},{h},{lo},{c},{v},{int(ct)},{v * c},10,{x},{x * c},0" for t, o, h, lo, c, v, ct, x in
                    zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"], d["volume"], d["close_time"], tb.tolist())]
            return _zip("open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,"
                        "taker_buy_quote_volume,ignore\n" + "\n".join(rows) + "\n")
        return None


class Klines:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames

    def klines(self, symbol, tf, limit=1500, start_ms=None, end_ms=None):
        df = self.frames[symbol.replace("/", "")]
        d = df[(df["timestamp"] >= start_ms) & (df["timestamp"] <= end_ms)].head(limit).copy()
        d["is_closed"] = True
        return d


def lab_world(n: int = 900, syms=("AAA/USDT", "BBB/USDT")):
    frames = {s.replace("/", ""): rw_prices(n, 50 + k) for k, s in enumerate(syms)}
    now = int(frames["AAAUSDT"]["timestamp"].iloc[-1]) + 2 * H4
    raws = {s: rw_raw(df, now - 400 * DAY, now, 50 + k) for k, (s, df) in enumerate(frames.items())}
    return frames, raws, now


def _strict_json(path: Path) -> dict:
    def bad(s):
        raise ValueError(f"JSON'da sonlu olmayan sayı: {s}")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=bad)


def test_end_to_end_crowd_probe_crowd_ctx_offline_and_abort(tmp_path, monkeypatch):
    frames, raws, now = lab_world()
    arc = Archive(raws, frames)
    logs: list[str] = []
    kw = dict(symbols=["AAA/USDT", "BBB/USDT"], tfs=["4h"], cache_dir=tmp_path / "c",
              cfg=L.LabConfig(min_is=5, min_oos=5, bootstrap_iters=200), days={"4h": 140}, jobs=1, catalog=False, algos=False,
              extras=False, now_ms=now, log=logs.append)
    # 1) yoklama: P1–P7 + kör sayım (P8) ve pos/flow payları; R YOK, olay CSV'si YOK
    rp = L.run(out_dir=tmp_path / "p", provider_factory=lambda: Klines(frames), futures="crowd-probe", futures_fetch=arc, **kw)
    assert any("/daily/metrics/AAAUSDT/" in u for u in arc.asked) and any("/monthly/klines/BBBUSDT/4h/" in u for u in arc.asked)
    assert any("/monthly/fundingRate/AAAUSDT/" in u for u in arc.asked), "fonlama fut_v1 önbelleğine (ensure_futures)"
    assert sum(1 for x in logs if x.startswith("CROWD_COV ")) == 2 and sum(1 for x in logs if x.startswith("FUT_COV ")) == 2
    items = {json.loads(x[len("CROWD_PROBE "):])["item"] for x in logs if x.startswith("CROWD_PROBE ")}
    assert items == {f"P{i}" for i in range(1, 8)}
    cr = rp["crowd"]
    assert (cr["mode"], cr["version"], cr["registry_sha"], cr["hypotheses"], cr["primary_tf"]) == ("crowd-probe", "fut_v2", PINNED_SHA, 8, "4h")
    assert set(cr["probe"]) == {f"P{i}" for i in range(1, 8)} and "groups" not in rp and "futures" not in rp
    b = cr["blind"]["4h"]
    assert len(b["rows"]) == 24 and b["bars_pos_ok"] > 800 and b["bars_flow_ok"] > 1000 and sum(r["IS"] + r["OOS"] for r in b["rows"]) > 30
    assert b["taker"]["vol_mismatch"] == 0 and b["taker"]["valid"] >= b["taker"]["bars"] - 10
    assert set(b["pos_side"]["share"]) == {"LONG", "SHORT", "NONE"} and b["flow_side"]["UNKNOWN"] < b["bars"]
    assert all(r.get("note") == CL.THIN_BY_DESIGN for r in b["rows"] if r["name"] in CL.RULES and (r["IS"] < 30 or r["OOS"] < 20))
    assert not (tmp_path / "p" / "signal_lab_events.csv.gz").exists() and _strict_json(tmp_path / "p" / "signal_lab_report.json")
    ptxt = L.render(rp)
    assert "kör sayım 4h" in ptxt and "pos_side LONG" in ptxt and "KALABALIK (TAKİP / KARŞI)" in ptxt
    plines = CL.log_lines(rp)
    assert sum(1 for x in plines if x.startswith("CROWD_COUNT ")) == 24 and sum(1 for x in plines if x.startswith("CROWD_SHARE ")) == 1
    assert all(len(x.encode()) <= 4000 for x in plines + logs)
    # 2) doğrulayıcı koşu: yalnız kalabalık aileleri; 8 hipotez satırı + takip/karşı; funding_r sütunu
    logs.clear()
    arc.asked.clear()
    rep = L.run(out_dir=tmp_path / "o", provider_factory=lambda: Klines(frames), futures="crowd", futures_fetch=arc, **kw)
    assert not [u for u in arc.asked if "/metrics/" in u or "/klines/" in u], "önbellek dolu: yeniden indirme yok"
    assert any(x.startswith("CROWD_PROBE ") for x in logs), "koşu başında önbellekten P2/P3/P6"
    doc = _strict_json(tmp_path / "o" / "signal_lab_report.json")
    cr = doc["crowd"]
    assert (cr["mode"], cr["registry_sha"], cr["trials"]["hypotheses"]) == ("crowd", PINNED_SHA, 16)
    assert [(r["name"], r["side"]) for r in cr["contributions"]] == [(n, s) for n in CL.RULES for s in (L.LONG, L.SHORT)]
    assert len(cr["pairs"]) == 4 and {p["trigger"] for p in cr["pairs"]} == {"BRK", "SWEEP"}
    assert {g["family"] for g in doc["groups"]} <= {"crowd", "control", "placebo"} and doc["events"] > 30
    assert all(m["crowd"]["bars_pos_ok"] > 400 for m in doc["series"]) and "futures" not in doc
    with gzip.open(tmp_path / "o" / "signal_lab_events.csv.gz", "rt", encoding="utf-8") as fh:
        head = fh.readline().strip().split(",")
    assert head[-1] == "funding_r"
    txt = L.render(rep)
    assert "KALABALIK (TAKİP / KARŞI)" in txt and "kapsama: OK 2" in txt and "takip mi karşı mı" in txt
    # 3) çevrimdışı ikinci koşu: yalnız önbellek (ağ YOK), aynı sonuç
    monkeypatch.setattr(L, "_http_get", lambda url, timeout=60.0: pytest.fail(f"ağ çağrısı: {url}"))
    rep2 = L.run(out_dir=tmp_path / "o2", provider_factory=None, futures="crowd", **kw)
    assert rep2["crowd"]["contributions"] == rep["crowd"]["contributions"] and rep2["crowd"]["pairs"] == rep["crowd"]["pairs"]
    # 4) bağlam dilimleri: algoritma + ek sinyal olaylarına dört kalabalık kovası (KEŞİF), kalabalık kuralı YOK
    rc = L.run(out_dir=tmp_path / "x", provider_factory=None, futures="crowd-ctx", **{**kw, "algos": True, "extras": True})
    assert {g["context"] for g in rc["groups"]} >= {"HEPSİ", "kalabalik_poz", "kalabalik_akis", "oi_ceyrek", "taker_24s"}
    assert not {g["family"] for g in rc["groups"]} & {"crowd", "control"} and rc["crowd"]["contributions"] == []
    assert "KEŞİF dilimleri" in L.render(rc) or not [g for g in rc["groups"] if g["context"] in CL.CTX_KEYS
                                                     and g["verdict"] in (L.V_STRONG, L.V_WEAK)]
    # 5) bağlantı yok: kalabalık/vadeli arşiv indirilemez → rapor YAZILMAZ
    with pytest.raises(L.DownloadAborted, match="ÇALIŞTIRILMADI"):
        L.run(out_dir=tmp_path / "dead", provider_factory=lambda: Klines(frames), futures="crowd",
              futures_fetch=Archive(raws, frames, dead=True), **{**kw, "cache_dir": tmp_path / "c2"})
    assert not (tmp_path / "dead" / "signal_lab_report.json").exists()
    with pytest.raises(ValueError, match="1h, 4h"):
        L.run(out_dir=tmp_path / "y", provider_factory=None, futures="crowd", **{**kw, "tfs": ["1d"]})
    with pytest.raises(ValueError, match="kipi"):
        L.run(out_dir=tmp_path / "y", provider_factory=None, futures="kalabalik", **kw)


# ---------------------------------------------------------------------------- komut satırı
def _cli():
    spec = importlib.util.spec_from_file_location("_signal_lab_cli_crowd", ROOT / "scripts" / "signal_lab.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_crowd_guards_modes_and_log_lines(monkeypatch, tmp_path, capsys):
    cli = _cli()
    seen: dict = {}

    def fake_run(**kw):
        seen.clear()
        seen.update(kw)
        return {"groups": [], "symbols": kw["symbols"], "timeframes": kw["tfs"]}
    monkeypatch.setattr(L, "run", fake_run)
    out = ["--out", str(tmp_path / "o")]
    arch = ["--source", "archive", "--tfs", "4h,1h"] + out
    assert cli.main(["--futures", "crowd-probe", "--tfs", "4h"] + out) == 2 and not seen, "--source api (varsayılan)"
    assert "yalnız arşivden: --source archive" in capsys.readouterr().err
    assert cli.main(["--futures", "crowd", "--source", "archive", "--tfs", "4h,1d"] + out) == 2 and not seen
    assert "--futures crowd yalnız 1h, 4h dilimlerinde (verilen: 1d)" in capsys.readouterr().err
    assert cli.main(["--futures", "crowd-ctx", "--source", "archive", "--tfs", "15m"] + out) == 2 and not seen
    assert cli.main(["--futures", "crowd", "--only-variations", "--variations", "CV000_EXAMPLE_BULL3"] + arch) == 2 and not seen
    assert cli.main(["--only-futures", "--futures", "crowd-ctx"] + arch) == 2 and not seen
    capsys.readouterr()
    assert cli.main(["--futures", "crowd"] + arch) == 0
    assert (seen["catalog"], seen["algos"], seen["extras"], seen["futures"], seen["variations"]) == (False, False, False, "crowd", [])
    printed = capsys.readouterr().out
    assert "CROWD_REGISTRY" in printed and CL.CROWD_REGISTRY_SHA in printed and "FUT_REGISTRY" not in printed
    assert cli.main(["--futures", "crowd-ctx", "--no-catalog"] + arch) == 0
    assert (seen["catalog"], seen["algos"], seen["extras"], seen["futures"]) == (False, True, True, "crowd-ctx")
    assert cli.main(["--futures", "crowd-probe"] + arch) == 0 and seen["futures"] == "crowd-probe"
    assert cli.main(["--futures", "crowd-probe", "--offline", "--tfs", "4h"] + out) == 0 and seen["provider_factory"] is None
    capsys.readouterr()
    assert cli.main(["--futures", "rules", "--source", "archive", "--tfs", "4h,1d"] + out) == 0 and seen["futures"] == "rules"
    printed = capsys.readouterr().out
    assert "FUT_REGISTRY" in printed and "CROWD_" not in printed, "fut_v1 kipi kalabalık satırı basmaz"
    assert cli.main(["--futures", "rules", "--source", "archive", "--tfs", "15m"] + out) == 2
    assert "--futures yalnız 1h, 4h, 1d dilimlerinde (verilen: 15m)" in capsys.readouterr().err, "fut_v1 iletisi aynen"


# ---------------------------------------------------------------------------- içe aktarma sınırları
def _imports(p: Path) -> set[str]:
    mods: set[str] = set()
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            base = ("." * n.level) + (n.module or "")
            mods.add(base)
            mods.update(base + "." + a.name for a in n.names)
    return mods


def test_only_the_lab_imports_crowd_modules_and_crowd_data_never_writes_fut_v1_cache():
    importers = []
    for p in sorted((ROOT / "tradingbot").rglob("*.py")):
        if any("crowd_" in m for m in _imports(p)):
            importers.append(p.relative_to(ROOT).as_posix())
    assert set(importers) <= {"tradingbot/signal_lab.py", "tradingbot/crowd_data.py", "tradingbot/crowd_lab.py"}, importers
    assert "tradingbot/signal_lab.py" in importers
    lab = (ROOT / "tradingbot" / "signal_lab.py").read_text(encoding="utf-8")
    top = ast.parse(lab).body
    assert not [n for n in top if isinstance(n, (ast.Import, ast.ImportFrom)) and "crowd" in ast.dump(n)], "yalnız tembel içe aktarma"
    src = (ROOT / "tradingbot" / "crowd_data.py").read_text(encoding="utf-8")
    used = set(re.findall(r'FD\._paths\([^)]*\)\["(\w+)"\]', src))
    assert used == {"oi_idx", "f"}, "fut_v1 önbelleğinden yalnız ilk gün dizini ve fonlama OKUNUR"
    assert not re.search(r"(_write_atomic|_merge_csv)\(\s*FD\._paths", src), "fut_v1 önbelleğine yazılmaz"
