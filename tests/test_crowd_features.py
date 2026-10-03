# -*- coding: utf-8 -*-
"""KALABALIK ÖZELLİKLERİ (fut_v2 / flow_v1 ortak matematiği, 2026-09-30): metrics hizalaması, zdm, taker blokları ve
doğruluk tabloları, bileşik kalabalık puanı ve yönler, nedensellik ve önek değişmezliği, canlı REST bütçesi, saflık.
Çevrimdışı ve sentetik: ağ yok, dosya yok."""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tradingbot import crowd_features as CF
from tradingbot import futures_features as FF

DAY, HOUR, MIN = FF.DAY, FF.HOUR, 60_000
H4 = 4 * HOUR
T0 = 1_700_000_000_000 - 1_700_000_000_000 % DAY


def world(step: int, n: int, seed: int = 11, pre_days: int = 30) -> tuple:
    """Fiyat (rastgele yürüyüş) + taker + 5 dk metrics (OI ve dört oran) + 8 saatlik fonlama; metrics/fonlama başlangıçtan
    `pre_days` önce başlar."""
    rnd = np.random.default_rng(seed)
    ts = T0 + np.arange(n, dtype=np.int64) * step
    c = 100.0 * np.exp(np.cumsum(rnd.normal(0, 0.01, n)))
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) * (1 + np.abs(rnd.normal(0, 0.004, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rnd.normal(0, 0.004, n)))
    v = 100.0 * np.exp(rnd.normal(0, 0.5, n))
    tb = v * rnd.uniform(0.3, 0.7, n)
    start, end = T0 - pre_days * DAY, T0 + (n + 1) * step
    m_t = np.arange(start, end, 300_000, dtype=np.int64)
    m = {"m_t": m_t, "oi": 1e6 * np.exp(np.cumsum(rnd.normal(0, 0.002, m_t.size)))}
    for k in ("gls", "tpls", "tals", "tkls"):
        m[k] = np.exp(np.cumsum(rnd.normal(0, 0.01, m_t.size)))
    f_t = np.arange(start, end, 8 * HOUR, dtype=np.int64) + rnd.integers(0, 6, int(np.ceil((end - start) / (8 * HOUR))))
    f = {"f_t": f_t, "f_rate": rnd.normal(1e-4, 2e-4, f_t.size)}
    return ts, o, h, lo, c, v, tb, m, f


def feats(step, ts, o, h, lo, c, v, tb, m, f):
    return CF.bar_features(ts, step, o, h, lo, c, v, tb, m, f)


def same(a: dict, b: dict, sa, sb=None) -> None:
    sb = sa if sb is None else sb
    for key in CF.CROWD_KEYS:
        np.testing.assert_array_equal(np.asarray(a[key], dtype=float)[sa], np.asarray(b[key], dtype=float)[sb], err_msg=key)


# ---------------------------------------------------------------------------- hizalama
def test_align_metrics_uses_last_row_between_t_minus_15m_and_t_minus_5m_for_every_column():
    T = T0 + 10 * HOUR

    def at(rows, **cols):
        return {k: v[0] for k, v in CF.align_metrics([T], rows, cols).items()}
    assert math.isnan(at([T], gls=[2.0])["gls"]), "T anında damgalı satır KULLANILMAZ"
    assert at([T - 5 * MIN], gls=[2.0])["gls"] == 2.0
    assert math.isnan(at([T - 5 * MIN + 1], gls=[2.0])["gls"]) and math.isnan(at([T - 15 * MIN - 1], gls=[2.0])["gls"])
    assert at([T - 15 * MIN], gls=[2.0])["gls"] == 2.0
    got = at([T - 10 * MIN, T - 5 * MIN + 1, T], gls=[1.0, 2.0, 3.0], tpls=[4.0, 5.0, 6.0])
    assert got == {"gls": 1.0, "tpls": 4.0}, "bütün sütunlar AYNI (son geçerli zamanlı) satırdan"
    got = at([T - 10 * MIN, T - 5 * MIN], gls=[1.0, 0.0], tpls=[4.0, 5.0])
    assert math.isnan(got["gls"]) and got["tpls"] == 5.0, "≤ 0 değer yalnız o sütunda NaN; eskiye DÜŞÜLMEZ"
    got = at([T - 10 * MIN, T - 5 * MIN], gls=[1.0, np.nan], tpls=None)
    assert math.isnan(got["gls"]) and math.isnan(got["tpls"]), "NaN son satır ve eksik sütun → NaN"
    assert at([T - 5 * MIN, T - 10 * MIN], gls=[2.0, 1.0])["gls"] == 2.0, "sırasız girdi savunması"
    rnd = np.random.default_rng(3)
    t = np.sort(rnd.integers(T0, T0 + 3 * DAY, 800)).astype(np.int64)
    oi = np.where(rnd.random(800) < 0.1, np.nan, rnd.uniform(-1, 5, 800))
    TT = T0 + np.arange(0, 3 * DAY, HOUR, dtype=np.int64)
    np.testing.assert_array_equal(CF.align_metrics(TT, t, {"oi": oi})["oi"], FF.align_oi(TT, t, oi))


# ---------------------------------------------------------------------------- zdm
def test_zdm_excludes_current_bar_needs_half_window_and_uses_floor():
    rnd = np.random.default_rng(5)
    x = rnd.normal(0, 1, 300)
    z = CF.zdm(x, 40, 0.01)
    for i in (40, 77, 299):
        w = x[i - 40:i]
        assert z[i] == pytest.approx((x[i] - w.mean()) / w.std()), "ddof 0, i pencerede YOK"
    assert np.isnan(z[:20]).all() and np.isfinite(z[20]), "en az W//2 değer"
    y = x.copy()
    y[100] += 50.0
    z2 = CF.zdm(y, 40, 0.01)
    np.testing.assert_array_equal(z2[:100], z[:100])
    assert z2[100] == pytest.approx(z[100] + 50.0 / x[60:100].std()), "i'nin kendi değeri yalnız payı değiştirir"
    flat = np.full(60, 0.3)
    flat[59] = 0.35
    assert CF.zdm(flat, 40, 0.01)[59] == pytest.approx(0.05 / 0.01), "σ = 0 → taban"
    x[150] = np.nan
    zz = CF.zdm(x, 40, 0.01)
    assert np.isnan(zz[150]) and np.isfinite(zz[151]), "güncel NaN → NaN; pencerede NaN atlanır"
    sparse = np.full(100, np.nan)
    sparse[::3] = 1.0 + np.arange(34) * 0.01
    assert np.isnan(CF.zdm(sparse, 40, 0.01)[99]), "pencerede W//2'den az sonlu değer → NaN"


# ---------------------------------------------------------------------------- taker bloğu
def test_taker_validity_dshare_cvd_share_and_slope_formulas_and_nan_is_never_zero():
    v = np.array([100.0, 0.0, 50.0, 80.0, 10.0, 10.0])
    tb = np.array([60.0, 0.0, -1.0, 80.0 * (1 + 2e-9), 10.0, np.nan])
    np.testing.assert_array_equal(CF.clean_taker(v, tb), [60.0, 0.0, np.nan, np.nan, 10.0, np.nan])
    n = 200
    rnd = np.random.default_rng(9)
    v = rnd.uniform(50, 150, n)
    tb = v * rnd.uniform(0.2, 0.8, n)
    c = 100 + np.cumsum(rnd.normal(0, 1, n))
    tb[150] = np.nan
    out = CF.taker_block(c, c + 1, c - 1, c, v, tb, H4)
    k = 6
    delta = 2 * tb - v
    i = 120
    assert out["dshare"][i] == pytest.approx(delta[i] / v[i])
    assert out["cvd_share"][i] == pytest.approx(delta[i - k + 1:i + 1].sum() / v[i - k + 1:i + 1].sum())
    cm = np.cumsum(delta[i - k + 1:i + 1])
    slope = np.polyfit(np.arange(1, k + 1), cm, 1)[0] / v[i - k + 1:i + 1].mean()
    assert out["cvd_slope"][i] == pytest.approx(slope)
    for key in ("dshare", "cvd_share", "cvd_slope"):
        a = out[key]
        assert np.isnan(a[150]) and not (a[~np.isfinite(a)] == 0).any()
    assert np.isnan(out["cvd_share"][150:156]).all() and np.isfinite(out["cvd_share"][156]), "penceresinde NaN → NaN"
    one_d = CF.taker_block(c, c + 1, c - 1, c, v, tb, DAY)
    assert np.isnan(one_d["cvd_slope"]).all(), "k = 1 → eğim yok"
    zero = CF.taker_block(c, c + 1, c - 1, c, np.where(np.arange(n) == 50, 0.0, v), np.where(np.arange(n) == 50, 0.0, tb), H4)
    assert np.isnan(zero["dshare"][50]), "v = 0 → dshare NaN (0 değil)"


def _flat_block(n: int = 40):
    c = np.full(n, 100.0)
    return c.copy(), c + 1.0, c - 1.0, c, np.full(n, 100.0), np.full(n, 50.0)


def test_divergence_truth_table():
    def run(new: str, cvd_last: float, nan_at: int | None = None):
        o, h, lo, c, v, tb = _flat_block()
        i = 30
        tb[i - 20:i] = 55.0                                  # C' yükseliyor (delta +10)
        if new == "high":
            c[i] = 101.0
            tb[i] = 50.0 + cvd_last / 2
        else:
            tb[i - 20:i] = 45.0                              # C' düşüyor (delta −10)
            c[i] = 99.0
            tb[i] = 50.0 + cvd_last / 2
        if nan_at is not None:
            tb[nan_at] = np.nan
        return CF.taker_block(o, h, lo, c, v, tb, H4)["divergence"][i]
    assert run("high", -10.0) == -1.0, "yeni kapanış zirvesi, CVD önceki zirvesinin altında → BEAR_DIV"
    assert run("high", +10.0) == 0.0, "CVD de yeni zirve → NONE"
    assert run("high", 0.0) == -1.0, "C'_i = max önceki (≤) → BEAR_DIV"
    assert run("low", +10.0) == 1.0, "yeni kapanış dibi, CVD önceki dibinin üstünde → BULL_DIV"
    assert run("low", -10.0) == 0.0
    assert np.isnan(run("high", -10.0, nan_at=15)), "pencerede taker NaN → UNKNOWN"
    assert run("high", -10.0, nan_at=5) == -1.0, "pencere dışı NaN etkisiz"
    o, h, lo, c, v, tb = _flat_block()
    assert CF.taker_block(o, h, lo, c, v, tb, H4)["divergence"][30] == 0.0, "yeni uç yok → NONE"
    assert np.isnan(CF.taker_block(o, h, lo, c, v, tb, H4)["divergence"][19]), "21 bar gerekir"


def test_absorption_truth_table():
    def run(vol=200.0, rng=1.0, tbuy=80.0, up=True):
        o, h, lo, c, v, tb = _flat_block()
        h[:] = 101.0
        lo[:] = 99.0                                         # önceki TR = 2
        i = 30
        v[i] = vol
        tb[i] = tbuy
        h[i], lo[i] = 100.0 + rng / 2, 100.0 - rng / 2
        o[i], c[i] = (99.9, 100.1) if up else (100.1, 99.9)
        return CF.taker_block(o, h, lo, c, v, tb, H4)["absorption"][i]
    assert run() == 1.0, "yüksek hacim, dar bar, satıcı baskın ama kapanış ≥ açılış → SELL_ABSORBED"
    assert run(up=False) == 0.0
    assert run(tbuy=120.0, up=False) == -1.0, "alıcı baskın, kapanış ≤ açılış → BUY_ABSORBED"
    assert run(tbuy=120.0) == 0.0
    assert run(vol=140.0, tbuy=56.0) == 0.0, "vr 1.4 < 1.5"
    assert run(vol=150.0, tbuy=60.0) == 1.0, "vr = 1.5 (≥)"
    assert run(rng=1.2) == 0.0, "h − l > 0.6·atr14s"
    assert run(tbuy=95.0) == 0.0, "|dshare| < 0.10"
    assert np.isnan(run(tbuy=np.nan)), "taker bilinmiyor → UNKNOWN"


def test_exhaustion_truth_table():
    def run(d3, down=False, last_new=True, nan_at=None):
        n = 40
        c = 100.0 + np.arange(n, dtype=float) * (-1.0 if down else 1.0)
        if not last_new:
            c[-1] = c[-2] - (-1.0 if down else 1.0) * 0.5
        v = np.full(n, 100.0)
        tb = np.full(n, 50.0)
        for j, ds in zip((n - 3, n - 2, n - 1), d3):
            tb[j] = 50.0 * (1 + ds)
        if nan_at is not None:
            tb[nan_at] = np.nan
        return CF.taker_block(c, c + 1, c - 1, c, v, tb, H4)["exhaustion_t"]
    ex = run((0.3, 0.2, 0.1))
    assert ex[-1] == 1.0, "zirve üyelerinde dshare kesin azalan → UP_EXHAUST"
    assert run((0.3, 0.3, 0.1))[-1] == 0.0, "kesin değil → NONE"
    assert run((0.1, 0.2, 0.3), down=True)[-1] == -1.0, "dip üyelerinde dshare kesin artan → DOWN_EXHAUST"
    assert run((0.3, 0.2, 0.1), down=True)[-1] == 0.0
    assert run((0.3, 0.2, 0.1), last_new=False)[-1] == 0.0, "i ∈ S değil → NONE"
    assert np.isnan(run((0.3, 0.2, 0.1), nan_at=38)[-1]), "son 10 barın dshare'inde NaN → UNKNOWN"
    assert np.isnan(run((0.3, 0.2, 0.1), last_new=False, nan_at=33)[-1]), "aday olmasa da pencerede NaN → UNKNOWN"
    assert run((0.3, 0.2, 0.1), nan_at=29)[-1] == 1.0, "10 barlık pencere dışı NaN etkisiz"
    assert np.isnan(ex[:28]).all() and np.isfinite(ex[28]), "29 bar gerekir (10 + 20 − 1)"


def test_oi_quad_and_funding_bucket_truth_tables():
    oz = np.array([0.2, -0.49, 0.6, 0.6, -0.5, -0.9, 0.6, np.nan, 1.0])
    dp = np.array([0.1, -0.1, 0.1, -0.1, 0.1, -0.1, 0.0, 0.1, np.nan])
    got = [CF.label(CF.QUAD_LABELS, x) for x in CF.oi_quad(oz, dp)]
    assert got == ["FLAT", "FLAT", "LONG_BUILD", "SHORT_BUILD", "SHORT_COVER", "LONG_UNWIND", "FLAT", "UNKNOWN", "UNKNOWN"]
    fb = CF.funding_bucket([-1e-6, 0.0, 1e-4, 1e-4 + 1e-12, 1.1e-4, np.nan])
    assert [CF.label(CF.FUND_LABELS, x) for x in fb] == ["NEG", "BASE", "BASE", "BASE", "HIGH", "UNKNOWN"]


# ---------------------------------------------------------------------------- bileşik puan ve yönler
def test_crowd_z_composite_sides_and_state():
    g = np.array([1.0, 10.0, 1.0, -2.0, 0.5, np.nan, 3.0, 0.0])
    t = np.array([1.0, 0.0, 1.0, -1.0, 0.5, 1.0, 3.0, 0.0])
    f = np.array([1.0, 0.0, 0.97, -0.1, 0.5, 1.0, 3.0, 0.0])
    oz = np.array([1.0, 0.0, 2.0, 0.0, 1.0, 1.5, 0.0, np.nan])
    cz = np.array([1.0, 0.0, -1.0, 0.0, -1.0, 1.0, 0.0, 2.0])
    s = CF.crowd_sides(g, t, f, oz, cz)
    np.testing.assert_allclose(s["crowd_z"][:5], [1.0, 1.0, 2.97 / 3, -3.1 / 3, 0.5])
    assert s["crowd_z"][1] == 1.0, "bileşenler ±3'te kırpılır (10 → 3)"
    assert list(s["pos_ok"]) == [True, True, True, True, True, False, True, True]
    assert [CF.label(CF.SIDE_LABELS, x) for x in s["pos_side"]] == ["LONG", "LONG", "NONE", "SHORT", "NONE", "UNKNOWN", "LONG", "NONE"]
    assert [CF.label(CF.SIDE_LABELS, x) for x in s["flow_side"]] == ["LONG", "NONE", "SHORT", "NONE", "SHORT", "LONG", "NONE", "UNKNOWN"]
    assert [CF.label(CF.STATE_LABELS, x) for x in s["crowd_state"]] == [
        "LONG_CROWDED", "LONG_CROWDED", "SHORTS_ENTERING", "SHORT_CROWDED", "SHORTS_ENTERING", "UNKNOWN", "LONG_CROWDED", "UNKNOWN"]
    neg = CF.crowd_sides(-g, -t, -f, oz, -cz)
    np.testing.assert_array_equal(neg["pos_side"], -s["pos_side"])
    np.testing.assert_array_equal(neg["flow_side"], -s["flow_side"])


def test_funding_z_uses_align_funding_window_and_floor():
    ts, o, h, lo, c, v, tb, m, f = world(H4, 300, seed=4)
    T = ts + H4
    z = CF.funding_z(T, f["f_t"], f["f_rate"])
    fund = FF.align_funding(T, f["f_t"], f["f_rate"])
    np.testing.assert_array_equal(np.isfinite(z), np.isfinite(fund["f8_hi"]), "aynı pencere ve ≥ FUND_MIN_N kuralı")
    ft, fr = f["f_t"], f["f_rate"]
    for i in (150, 299):
        ji = int(np.searchsorted(ft, T[i] - FF.FUND_GUARD_MS, side="right") - 1)
        a = int(np.searchsorted(ft, T[i] - FF.WINDOW_DAYS * DAY, side="right"))
        s8 = fr * 8.0 / FF.infer_interval_h(ft)
        others = s8[a:ji][np.isfinite(s8[a:ji])]
        assert z[i] == pytest.approx((s8[ji] - others.mean()) / max(others.std(), CF.Z_FLOOR_FUND))
    const = {"f_t": f["f_t"], "f_rate": np.full(f["f_t"].size, 1e-4)}
    zc = CF.funding_z(T, const["f_t"], const["f_rate"])
    assert np.nanmax(np.abs(zc)) < 1e-6, "sabit oran: sd 0 → taban, z ≈ 0 (sonsuz değil)"


# ---------------------------------------------------------------------------- nedensellik ve önek değişmezliği
@pytest.mark.parametrize("step,n", [(H4, 1500), (HOUR, 2400)])
def test_bar_features_are_causal_and_prefix_invariant(step, n):
    ts, o, h, lo, c, v, tb, m, f = world(step, n, seed=21, pre_days=30)
    full = feats(step, ts, o, h, lo, c, v, tb, m, f)
    cut = n - 150
    t_cut = int(ts[cut]) + step
    # 1) gelecek barlar / satırlar i ≤ kesim değerlerini DEĞİŞTİRMEZ
    o2, h2, lo2, c2, v2, tb2 = (x.copy() for x in (o, h, lo, c, v, tb))
    for x in (o2, h2, lo2, c2):
        x[cut + 1:] *= 1.7
    v2[cut + 1:] *= 9.0
    tb2[cut + 1:] = v2[cut + 1:] * 0.99
    m2 = {k: np.array(x, copy=True) for k, x in m.items()}
    late = m2["m_t"] > t_cut - FF.OI_LAG_MS
    for k in CF.METRIC_FIELDS:
        m2[k][late] *= 3.0
    f2 = {"f_t": f["f_t"].copy(), "f_rate": f["f_rate"].copy()}
    f2["f_rate"][f2["f_t"] > t_cut - FF.FUND_GUARD_MS] = 0.01
    pert = feats(step, ts, o2, h2, lo2, c2, v2, tb2, m2, f2)
    same(full, pert, slice(0, cut + 1))
    # 2) canlı bütçesi kadar (25 gün) kırpılmış geçmiş son barlarda AYNI değerleri verir
    req = CF.live_requirements("4h" if step == H4 else "1h")
    keep = int(25 * DAY // step)
    a = cut + 1 - keep
    mm = (m["m_t"] >= t_cut - 25 * DAY) & (m["m_t"] <= t_cut)
    ff = (f["f_t"] >= t_cut - 25 * DAY) & (f["f_t"] <= t_cut)
    short = feats(step, ts[a:cut + 1], o[a:cut + 1], h[a:cut + 1], lo[a:cut + 1], c[a:cut + 1], v[a:cut + 1], tb[a:cut + 1],
                  {k: x[mm] for k, x in m.items()}, {k: x[ff] for k, x in f.items()})
    tail = keep - req["klines_bars"] - 1
    assert tail > 5
    same(full, short, slice(cut + 1 - tail, cut + 1), slice(keep - tail, keep))
    for key in CF.CROWD_KEYS:
        assert np.isfinite(np.asarray(short[key], dtype=float)[-1]) or key in ("pos_side", "flow_side", "crowd_state"), key
    assert bool(short["pos_ok"][-1]) and bool(short["flow_ok"][-1])
    # 3) NaN hiçbir zaman 0'a çevrilmez: taker yok → taker alanları ve flow NaN, pozisyon alanları etkilenmez
    no_tb = feats(step, ts, o, h, lo, c, v, np.full(n, np.nan), m, f)
    for key in ("dshare", "dshare_z", "cvd_share", "cvd_share_z", "cvd_slope", "divergence", "absorption", "exhaustion_t", "flow_side"):
        assert np.isnan(np.asarray(no_tb[key], dtype=float)).all(), key
    assert not no_tb["flow_ok"].any()
    np.testing.assert_array_equal(no_tb["pos_side"], full["pos_side"])
    no_m = feats(step, ts, o, h, lo, c, v, tb, None, None)
    for key in ("oi", "oi_z", "gls", "gls_z", "tpls_z", "f8", "f8_z", "crowd_z", "pos_side", "flow_side", "oi_quad", "funding_bucket"):
        assert np.isnan(np.asarray(no_m[key], dtype=float)).all(), key


def test_live_requirements_fit_rest_retention():
    for tf, bars in (("4h", 127), ("1h", 505)):
        req = CF.live_requirements(tf)
        assert req["klines_bars"] == bars, tf
        assert max(req["klines_ms"], req["metrics_ms"], req["funding_ms"]) <= FF.LIVE_OI_MAX_MS == 28 * DAY
        assert req["metrics_ms"] == FF.live_requirements(tf)["oi_ms"]


# ---------------------------------------------------------------------------- bağlam kovaları
def test_ctx_labels_are_side_relative_and_unknown_is_bilinmiyor():
    feat = {"pos_side": np.array([1.0, -1.0, 0.0, np.nan]), "flow_side": np.array([-1.0, np.nan, 1.0, 0.0]),
            "oi_quad": np.array([1.0, np.nan, 0.0, 4.0]), "cvd_share_z": np.array([1.2, -1.0, 0.3, np.nan])}
    assert CF.ctx_labels(feat, 0, "LONG") == {"kalabalik_poz": "kalabalıkla", "kalabalik_akis": "kalabalığa_karşı",
                                              "oi_ceyrek": "LONG_BUILD", "taker_24s": "kalabalıkla"}
    assert CF.ctx_labels(feat, 0, "SHORT") == {"kalabalik_poz": "kalabalığa_karşı", "kalabalik_akis": "kalabalıkla",
                                               "oi_ceyrek": "LONG_BUILD", "taker_24s": "kalabalığa_karşı"}
    assert CF.ctx_labels(feat, 1, "SHORT") == {"kalabalik_poz": "kalabalıkla", "kalabalik_akis": "bilinmiyor",
                                               "oi_ceyrek": "bilinmiyor", "taker_24s": "kalabalıkla"}
    assert CF.ctx_labels(feat, 2, "LONG")["taker_24s"] == "kalabalık_yok" and CF.ctx_labels(feat, 2, "LONG")["kalabalik_poz"] == "kalabalık_yok"
    assert CF.ctx_labels(feat, 3, "LONG") == {"kalabalik_poz": "bilinmiyor", "kalabalik_akis": "kalabalık_yok",
                                              "oi_ceyrek": "LONG_UNWIND", "taker_24s": "bilinmiyor"}
    for key, buckets in CF.CTX_BUCKETS.items():
        assert "bilinmiyor" not in buckets, key


# ---------------------------------------------------------------------------- saflık ve sabitler
def test_module_is_pure_and_imports_only_futures_features_and_timeframes():
    code = ("import sys, tradingbot.crowd_features as C; "
            "ok = ('tradingbot.crowd_features', 'tradingbot.futures_features', 'tradingbot.timeframes'); "
            "bad = [m for m in sys.modules if m.startswith('tradingbot.') and m not in ok]; "
            "bad += [m for m in ('urllib.request', 'requests', 'pandas') if m in sys.modules]; print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=str(Path(__file__).resolve().parents[1])).stdout.strip()
    assert out == ""
    src = (Path(__file__).resolve().parents[1] / "tradingbot" / "crowd_features.py").read_text(encoding="utf-8")
    imports = sorted({ln.split()[1] for ln in src.splitlines() if ln.startswith(("import ", "from "))})
    assert imports == [".", ".timeframes", "__future__", "math", "numpy", "typing"], imports


def test_constants_match_plan():
    C = CF.CROWD_FEATURE_CONSTANTS
    assert (C["DIV_LOOKBACK"], C["Z_FLOOR_SHARE"], C["Z_FLOOR_LOG"], C["Z_FLOOR_FUND"], C["Z_CLIP"]) == (20, 0.01, 0.02, 0.000025, 3.0)
    assert (C["CROWD_Z"], C["FLOW_OI_Z"], C["FLOW_CVD_Z"], C["OI_QUAD_DEAD"]) == (1.0, 1.0, 1.0, 0.5)
    assert (C["ABS_VR"], C["ABS_RANGE_ATR"], C["ABS_DSHARE"], C["EXH_SPAN"], C["EXH_LOOKBACK"], C["EXH_MEMBERS"]) == (1.5, 0.6, 0.10, 10, 20, 3)
    assert C["keys"] == list(CF.CROWD_KEYS) and len(CF.CROWD_KEYS) == 36
    assert set(C["formulas_tr"]) >= {"zdm", "taker", "cvd_share", "pos", "flow", "ratios", "funding", "oi"}
