# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — T1: `situation_v1` anlık görüntüsü (2026-09-29).

docs/ortak_deneyim/SPEC_V1.md §4 + §13 T1: sabitlenmiş şema özeti ve altın vektör (bağımsız referans uygulamayla
çapraz denetimli); nedensellik (as_of'tan sonra kapanan barı değiştirmek/eklemek hiçbir şey değiştirmez); sınır (açılış +
dilim == as_of dahil, 1 ms önce hariç); alınma kuralı (çerçeve alındığında oluşan bar dışlanır, pencere eksik kalır);
700/240/200 kırpma aynılığı; kısa geçmiş → PARTIAL + doğru `missing`, sıfır YOK; bozuk satırlar düşer; belirlenimcilik
(süreçler arası, PYTHONHASHSEED); kova eşikleri `signal_lab.context` ile aynı; saflık (G/Ç yok, kitap/defter/risk
içe aktarımı yok). Her test eski kodda (7ec832c) düşer: `tradingbot.shared_experience` paketi yoktur."""
from __future__ import annotations

import ast
import json
import math
import os
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.shared_experience import situation as S  # noqa: E402

H4, H1 = 14_400_000, 3_600_000
#: Pazartesi 12:05 UTC (bir 4h ve 1h sınırından 5 dk sonra) — tur `now`'ı gibi.
AS_OF = (1_790_000_000_000 // H4) * H4 + 5 * 60_000
LAST4 = (AS_OF // H4) * H4 - H4          # 08:00 açılışlı 4h bar: 12:00'de kapandı
LAST1 = (AS_OF // H1) * H1 - H1          # 11:00 açılışlı 1h bar: 12:00'de kapandı
SYM = "SOL/USDT"


# ---------------------------------------------------------------------------- belirlenimci sentetik barlar
def _lcg(seed: int):
    x = seed & 0xFFFFFFFF
    while True:
        x = (1664525 * x + 1013904223) & 0xFFFFFFFF
        yield x / 4294967296.0


def make_bars(n: int, step: int, first_open: int, seed: int, base: float = 100.0, dec: int = 4) -> list[dict]:
    """Borsa biçimli (tick'e yuvarlanmış ondalık) OHLCV satırları; rastgelelik yalnız sabit tohumlu LCG."""
    g = _lcg(seed)
    out = []
    c = round(base, dec)
    for i in range(n):
        o = c
        r = (next(g) - 0.5) * 0.03 + 0.006 * math.sin(i / 23.0 + seed)
        c = round(o * (1 + r), dec)
        hi = round(max(o, c) * (1 + next(g) * 0.008), dec)
        lo = round(min(o, c) * (1 - next(g) * 0.008), dec)
        v = round(1000.0 * (0.3 + next(g)) * (1.2 + math.sin(i / 11.0)), 3)
        out.append({"timestamp": first_open + i * step, "open": o, "high": hi, "low": lo, "close": c, "volume": v})
    return out


def fixture() -> tuple[list[dict], list[dict], list[dict]]:
    """Altın fikstür: 700 4h + 500 1h (SOL) + 700 4h (BTC), son kapanış tam 12:00'de."""
    r4 = make_bars(700, H4, LAST4 - 699 * H4, 11, 150.0)
    r1 = make_bars(500, H1, LAST1 - 499 * H1, 12, 150.0)
    rb = make_bars(700, H4, LAST4 - 699 * H4, 13, 60000.0, 1)
    return r4, r1, rb


def windows(r4, r1, rb, as_of=AS_OF, *, fetch_lb=None):
    return (S.normalize_rows(r4, "4h", as_of, W=S.W4H, fetch_lb_ms=fetch_lb),
            S.normalize_rows(r1, "1h", as_of, W=S.W1H, fetch_lb_ms=fetch_lb),
            S.normalize_rows(rb, "4h", as_of, W=S.WBTC, fetch_lb_ms=fetch_lb))


def snap_of(r4, r1, rb, as_of=AS_OF, *, symbol=SYM, fetch_lb=None) -> dict:
    return S.snapshot(symbol, *windows(r4, r1, rb, as_of, fetch_lb=fetch_lb), as_of)


# ---------------------------------------------------------------------------- sabitler
PINNED_SCHEMA_SHA = "640fd10e5d6f727c"

GOLDEN = {
    "schema_id": "situation_v1", "schema_sha": PINNED_SCHEMA_SHA, "symbol": "SOL/USDT", "as_of_ms": 1789992300000,
    "h4_last_open_ms": 1789977600000, "h1_last_open_ms": 1789988400000, "btc_last_open_ms": 1789977600000,
    "n_h4": 200, "n_h1": 200, "n_btc": 200, "input_sha": "9d63eeaa995e6d69", "status": "OK", "missing": [],
    "h4_ret_6_pct": -2.088838, "h4_ret_42_pct": 1.821954, "h4_ema_state": 0, "h4_ema50_slope_atr": 0.044914,
    "h4_close_vs_ema50_atr": 0.388743, "h4_er20": 0.11627, "h4_structure": "MIXED", "h4_trend": "RANGE",
    "h4_atr_pct": 1.659175, "h4_atr_ratio": 1.007388, "h4_vol_regime": "NORMAL", "h4_range_pos_20": 0.31499,
    "h4_bbw_pctile_100": 0.1, "h4_dist_swing_high_atr": 2.011207, "h4_swing_high_found": True,
    "h4_dist_swing_low_atr": 0.773236, "h4_swing_low_found": True, "h4_rsi14": 46.013667, "h4_rsi_bucket": "30-50",
    "h4_vol_ratio20": 2.51389, "h4_vol_bucket": "HIGH", "h4_effort_result": 4.247227, "h4_exhaustion": "NONE",
    "h1_rsi14": 77.21241, "h1_ret_4_pct": 1.32333, "h1_vol_ratio20": 1.80562, "h1_close_vs_ema20_atr": 2.666002,
    "h1_va_pos": "ABOVE_VA", "h1_poc_dist_atr": 13.239749, "btc_h4_trend": "DOWN", "btc_h4_ret_6_pct": -6.537104,
    "btc_h4_vol_regime": "HIGH", "corr_btc_h4_50": 0.101749, "is_btc": False, "rs_btc_h4_42_pct": 21.017083,
    "hour_utc": 12, "session": "EU", "weekday": 0,
}


def test_schema_sha_pinned_and_spec_consistent():
    canon = json.dumps(S.SCHEMA_SPEC, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    import hashlib
    assert S.SCHEMA_ID == "situation_v1"
    assert S.SCHEMA_SHA == hashlib.sha256(canon).hexdigest()[:16] == PINNED_SCHEMA_SHA
    assert S.W4H == S.W1H == S.WBTC == 200 and S.W_MAX == 240
    from tradingbot.pattern_trader.data import BARS_PER_TF
    assert max(S.W4H, S.W1H, S.WBTC) <= min(BARS_PER_TF["4h"], BARS_PER_TF["1h"]) == S.W_MAX
    assert S.SCHEMA_SPEC["fields"]["provenance"] == list(S.PROVENANCE_FIELDS)
    assert S.SCHEMA_SPEC["fields"]["core"] == list(S.CORE_FIELDS)
    assert set(S.NEEDS) == set(S.CORE_FIELDS) - {"is_btc"}
    assert S.SCHEMA_SPEC["edges"]["numbers"] == [0.8, 1.25, 0.8, 1.5, 30.0, 50.0, 70.0]
    snap = snap_of(*fixture())
    assert list(snap) == list(S.SNAPSHOT_FIELDS)


def test_golden_vector():
    snap = snap_of(*fixture())
    assert snap == GOLDEN
    json.dumps(snap, allow_nan=False)                        # NaN/inf asla yayımlanmaz


# ---------------------------------------------------------------------------- bağımsız referans (numpy/pandas)
def _ref_atr(h, l, c, p=14):
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    out = np.full(len(c), np.nan)
    a = tr[:p].mean()
    out[p] = a
    for i, x in enumerate(tr[p:], start=p + 1):
        a = (a * (p - 1) + x) / p
        out[i] = a
    return out


def _ref_ema(c, p):
    out = np.full(len(c), np.nan)
    e = c[:p].mean()
    out[p - 1] = e
    k = 2 / (p + 1)
    for i in range(p, len(c)):
        e = e + k * (c[i] - e)
        out[i] = e
    return out


def _ref_rsi(c, p=14):
    d = np.diff(c)
    g, ls = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = g[:p].mean(), ls[:p].mean()
    for i in range(p, len(d)):
        ag, al = (ag * (p - 1) + g[i]) / p, (al * (p - 1) + ls[i]) / p
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def _ref_vp(h, l, v, c_last, bins=48):
    lo, hi = l.min(), h.max()
    hist = np.zeros(bins)
    for a, b, vv in zip(l, h, v):
        i0 = min(bins - 1, int(np.floor((a - lo) / (hi - lo) * bins)))
        i1 = min(bins - 1, int(np.floor((b - lo) / (hi - lo) * bins)))
        hist[i0:i1 + 1] += vv / (i1 - i0 + 1)
    poc = int(hist.argmax())
    a = b = poc
    acc = hist[poc]
    while acc < 0.7 * hist.sum():
        below = hist[a - 1] if a > 0 else -1.0
        above = hist[b + 1] if b < bins - 1 else -1.0
        if above >= below:
            b += 1
            acc += above
        else:
            a -= 1
            acc += below
    w = (hi - lo) / bins
    pos = "ABOVE_VA" if c_last > lo + (b + 1) * w else ("BELOW_VA" if c_last < lo + a * w else "IN_VA")
    return pos, lo + (poc + 0.5) * w


def test_golden_matches_independent_reference():
    r4, r1, rb = fixture()
    w4, w1, wb = windows(r4, r1, rb)
    snap = S.snapshot(SYM, w4, w1, wb, AS_OF)
    o, h, l, c, v = (np.asarray(x) for x in (w4.o, w4.h, w4.l, w4.c, w4.v))
    atr = _ref_atr(h, l, c)
    e20, e50 = _ref_ema(c, 20), _ref_ema(c, 50)
    tol = 2e-6
    assert snap["h4_atr_pct"] == pytest.approx(100 * atr[-1] / c[-1], abs=tol)
    assert snap["h4_ema50_slope_atr"] == pytest.approx((e50[-1] - e50[-11]) / (10 * atr[-1]), abs=tol)
    assert snap["h4_close_vs_ema50_atr"] == pytest.approx((c[-1] - e50[-1]) / atr[-1], abs=tol)
    state = 1 if c[-1] > e20[-1] > e50[-1] else (-1 if c[-1] < e20[-1] < e50[-1] else 0)
    assert snap["h4_ema_state"] == state
    assert snap["h4_rsi14"] == pytest.approx(_ref_rsi(c), abs=tol)
    assert snap["h1_rsi14"] == pytest.approx(_ref_rsi(np.asarray(w1.c)), abs=tol)
    assert snap["h4_ret_6_pct"] == pytest.approx((c[-1] / c[-7] - 1) * 100, abs=tol)
    assert snap["h4_ret_42_pct"] == pytest.approx((c[-1] / c[-43] - 1) * 100, abs=tol)
    assert snap["h4_er20"] == pytest.approx(abs(c[-1] - c[-21]) / np.abs(np.diff(c[-21:])).sum(), abs=tol)
    assert snap["h4_range_pos_20"] == pytest.approx((c[-1] - l[-20:].min()) / (h[-20:].max() - l[-20:].min()), abs=tol)
    atr_pct = 100 * atr / c
    assert snap["h4_atr_ratio"] == pytest.approx(atr_pct[-1] / np.median(atr_pct[-101:-1]), abs=tol)
    cs = pd.Series(c)
    bbw = (4 * cs.rolling(20).std(ddof=0) / cs.rolling(20).mean()).to_numpy()
    assert snap["h4_bbw_pctile_100"] == pytest.approx(float((bbw[-101:-1] < bbw[-1]).mean()), abs=1e-12)
    # hacim oranı: `signal_lab.indicators` vol_avg ("ÖNCEKİ 20 bar") ile aynı tanım
    from tradingbot.signal_lab import indicators
    ind = indicators(pd.DataFrame({"high": h, "low": l, "close": c, "volume": v}))
    assert snap["h4_vol_ratio20"] == pytest.approx(v[-1] / ind["vol_avg"][-1], abs=tol)
    assert snap["h4_effort_result"] == pytest.approx(v[-1] / ind["vol_avg"][-1] / max(abs(c[-1] - o[-1]) / atr[-1], 0.1), abs=tol)
    # 1h EMA20/ATR ve hacim profili
    h1h, l1h, c1h, v1h = (np.asarray(x) for x in (w1.h, w1.l, w1.c, w1.v))
    atr1 = _ref_atr(h1h, l1h, c1h)
    assert snap["h1_close_vs_ema20_atr"] == pytest.approx((c1h[-1] - _ref_ema(c1h, 20)[-1]) / atr1[-1], abs=tol)
    pos, poc_mid = _ref_vp(h1h[-120:], l1h[-120:], v1h[-120:], c1h[-1])
    assert snap["h1_va_pos"] == pos
    assert snap["h1_poc_dist_atr"] == pytest.approx((c1h[-1] - poc_mid) / atr1[-1], abs=tol)
    # BTC korelasyonu: aynı zaman damgalı son 50 log getiri
    ra = np.diff(np.log(c))[-50:]
    rbb = np.diff(np.log(np.asarray(wb.c)))[-50:]
    assert snap["corr_btc_h4_50"] == pytest.approx(float(np.corrcoef(ra, rbb)[0, 1]), abs=tol)
    cb = np.asarray(wb.c)
    assert snap["rs_btc_h4_42_pct"] == pytest.approx((c[-1] / c[-43] - cb[-1] / cb[-43]) * 100, abs=tol)
    assert snap["btc_h4_ret_6_pct"] == pytest.approx((cb[-1] / cb[-7] - 1) * 100, abs=tol)


# ---------------------------------------------------------------------------- nedensellik / sınır / alınma kuralı
def _future_rows(rows, step, n, *, crazy=1000.0):
    last = rows[-1]["timestamp"]
    return [{"timestamp": last + (k + 1) * step, "open": crazy, "high": crazy * 2, "low": crazy / 2, "close": crazy * 1.5,
             "volume": 9e9} for k in range(n)]


def test_causality_future_bars_mutated_or_appended_change_nothing():
    r4, r1, rb = fixture()
    base = snap_of(r4, r1, rb)
    # as_of'ta OLUŞAN bar (açılış 12:00, kapanış 16:00 > as_of) + gelecek barlar; BTC için de
    m4 = r4 + _future_rows(r4, H4, 5)
    m1 = r1 + _future_rows(r1, H1, 9, crazy=7.0)
    mb = rb + _future_rows(rb, H4, 5, crazy=1.0)
    assert snap_of(m4, m1, mb) == base
    # oluşan barın değerlerini değiştirmek (kısmi → farklı kısmi) de hiçbir şey değiştirmez
    m4b = [dict(r) for r in m4]
    m4b[700]["close"] = 1.0
    m4b[700]["volume"] = 0.0
    assert snap_of(m4b, m1, mb) == base
    # duyarlılık: as_of'ta KAPANMIŞ son barı değiştirmek her şeyi değiştirir (test kör değil)
    m4c = [dict(r) for r in r4]
    m4c[-1]["close"] = round(m4c[-1]["close"] * 1.01, 4)
    m4c[-1]["high"] = max(m4c[-1]["high"], m4c[-1]["close"])
    changed = snap_of(m4c, r1, rb)
    assert changed["input_sha"] != base["input_sha"] and changed["h4_ret_6_pct"] != base["h4_ret_6_pct"]


def test_boundary_inclusive_at_close_exclusive_one_ms_before():
    r4, r1, rb = fixture()
    close_ms = LAST4 + H4                                    # 08:00 barı 12:00'de kapanır
    w_at = S.normalize_rows(r4, "4h", close_ms, W=200)
    assert w_at.last_open_ms == LAST4 and w_at.complete
    w_before = S.normalize_rows(r4, "4h", close_ms - 1, W=200)
    assert w_before.last_open_ms == LAST4 - H4 and w_before.complete
    assert LAST4 not in set(w_before.ts.tolist())
    # 1 ms önce: sonraki barlar YOKmuş gibi (aynı kırpılmış kaynakla birebir)
    s_before = snap_of(r4, r1, rb, close_ms - 1)
    trimmed = snap_of([r for r in r4 if r["timestamp"] + H4 <= close_ms - 1], [r for r in r1 if r["timestamp"] + H1 <= close_ms - 1],
                      [r for r in rb if r["timestamp"] + H4 <= close_ms - 1], close_ms - 1)
    assert s_before == trimmed and s_before["status"] == "OK"
    assert S.expected_last_closed_open(close_ms, "4h") == LAST4
    assert S.expected_last_closed_open(close_ms - 1, "4h") == LAST4 - H4


def test_fetch_rule_excludes_bar_that_was_forming_when_frame_was_fetched():
    """Box olayı 12:05, çerçeve 11:58'de başlayan turdan: 08:00 4h barı ve 11:00 1h barı çerçevede OLUŞAN (kısmi) satır.
    as_of kapanışlarını geçmiş olsa da kullanılamaz: pencere eksik, durum GAP; kural olmadan kısmi değer sızardı."""
    r4, r1, rb = fixture()
    fetch_lb = LAST4 + H4 - 2 * 60_000                        # 11:58 (sınırdan önce)
    part4 = [dict(r) for r in r4]
    part4[-1] = dict(part4[-1], close=round(part4[-1]["open"] * 0.97, 4), high=part4[-1]["open"], low=round(part4[-1]["open"] * 0.965, 4),
                     volume=12.0)
    part1 = [dict(r) for r in r1]
    part1[-1] = dict(part1[-1], close=part1[-1]["open"], high=part1[-1]["open"], low=part1[-1]["open"], volume=1.0)
    partb = [dict(r) for r in rb]
    partb[-1] = dict(partb[-1], volume=3.0)
    w4 = S.normalize_rows(part4, "4h", AS_OF, W=200, fetch_lb_ms=fetch_lb)
    assert w4.last_open_ms == LAST4 - H4 and not w4.complete           # açılış + dilim <= as_of olsa bile dışlandı
    snap = snap_of(part4, part1, partb, fetch_lb=fetch_lb)
    assert snap["status"] == "GAP"
    assert snap["missing"][:3] == ["h4_window", "h1_window", "btc_window"]
    assert all(snap[f] in (None, "UNKNOWN") for f in S.CORE_FIELDS if f.startswith("h4_"))
    assert all(f in snap["missing"] for f in S.CORE_FIELDS if f.startswith(("h4_", "h1_", "btc_")))
    # kural OLMADAN (yanlış kullanım) kısmi bar tam sayılır ve gerçekten farklı bir "durum" üretir: tuzak gerçektir
    trap = snap_of(part4, part1, partb)
    truth = snap_of(r4, r1, rb)
    assert trap["status"] == "OK" and trap["input_sha"] != truth["input_sha"]
    # çerçeve sınırdan SONRA alınmışsa (12:03 < as_of 12:05) bar kapanmış ve kesindir → tam, gerçekle aynı
    assert snap_of(r4, r1, rb, fetch_lb=LAST4 + H4 + 3 * 60_000) == truth


def test_btc_after_as_of_ignored_and_is_btc_corr_one():
    r4, r1, rb = fixture()
    base = snap_of(r4, r1, rb)
    assert snap_of(r4, r1, rb + _future_rows(rb, H4, 3)) == base
    b4 = S.normalize_rows(rb, "4h", AS_OF, W=200)
    b1 = S.normalize_rows(r1, "1h", AS_OF, W=200)
    for sym in ("BTC/USDT", "BTC/USDT:USDT", "BTCUSDT", "btc-usdt"):
        s = S.snapshot(sym, b4, b1, b4, AS_OF)
        assert s["is_btc"] is True and s["corr_btc_h4_50"] == 1.0 and s["rs_btc_h4_42_pct"] == 0.0
        assert s["btc_h4_trend"] == s["h4_trend"] and s["status"] == "OK"
    assert base["is_btc"] is False and S.is_btc_symbol("BTC/USDC") is False


def test_truncation_700_240_200_identical():
    r4, r1, rb = fixture()
    full = snap_of(r4, r1, rb)
    assert snap_of(r4[-240:], r1[-240:], rb[-240:]) == full
    assert snap_of(r4[-200:], r1[-200:], rb[-200:]) == full
    assert snap_of(r4[-201:], r1[-500:], rb[-333:]) == full


def test_short_history_partial_with_exact_missing_and_no_zeros():
    r4, r1, rb = fixture()
    s = snap_of(r4[-50:], r1[-50:], rb[-50:])
    assert s["status"] == "PARTIAL" and (s["n_h4"], s["n_h1"], s["n_btc"]) == (50, 50, 50)
    assert s["missing"] == ["h4_ema50_slope_atr", "h4_trend", "h4_atr_ratio", "h4_vol_regime", "h4_bbw_pctile_100",
                            "btc_h4_trend", "btc_h4_vol_regime"]
    for f in s["missing"]:
        assert s[f] is None or s[f] == "UNKNOWN"            # ASLA 0
    assert s["h4_ema_state"] is not None and s["corr_btc_h4_50"] is not None and s["h1_va_pos"] is not None
    # çok kısa: 12 bar — hesaplanamayan her alan None ve missing'te; sayısal hiçbir eksik alan 0 değil
    t = snap_of(r4[-12:], r1[-4:], rb[-12:])
    assert t["status"] == "PARTIAL"
    assert t["h4_structure"] == "UNKNOWN" and "h4_structure" not in t["missing"]   # >= 10 bar: hesaplandı, salınım az
    for f in S.CORE_FIELDS:
        need = S.NEEDS.get(f)
        if need is None:
            continue
        n = {"h4": 12, "h1": 4, "bt": 12, "co": 12, "rs": 12}[f[:2]]
        if n < need:
            assert f in t["missing"], f
            assert t[f] in (None, "UNKNOWN"), f
    # 1h penceresi hiç yok (eski geri doldurma): PARTIAL, 1h alanları None, belirteç missing'te
    u = S.snapshot(SYM, S.normalize_rows(r4, "4h", AS_OF, W=200), S.normalize_rows([], "1h", AS_OF, W=200),
                   S.normalize_rows(rb, "4h", AS_OF, W=200), AS_OF)
    assert u["status"] == "PARTIAL" and u["missing"][0] == "h1_window"
    assert all(u[f] is None for f in S.CORE_FIELDS if f.startswith("h1_"))


def test_no_bars_gap_and_error_statuses():
    r4, r1, rb = fixture()
    w4, w1, wb = windows(r4, r1, rb)
    e4 = S.normalize_rows(None, "4h", AS_OF, W=200)
    assert S.snapshot(SYM, e4, w1, wb, AS_OF)["status"] == "NO_BARS"
    nb = S.snapshot(SYM, w4, w1, S.empty_window("4h"), AS_OF)
    assert nb["status"] == "NO_BARS" and nb["corr_btc_h4_50"] is None and nb["missing"][0] == "btc_window"
    # bayat 4h (son bar yok): GAP; 4h alanları bayat barlardan DEĞİL → None; 1h/BTC alanları tamdakinin aynısı
    stale = S.normalize_rows(r4[:-1], "4h", AS_OF, W=200)
    g = S.snapshot(SYM, stale, w1, wb, AS_OF)
    full = S.snapshot(SYM, w4, w1, wb, AS_OF)
    assert g["status"] == "GAP" and g["missing"][0] == "h4_window"
    assert g["h4_ret_6_pct"] is None and g["h4_trend"] == "UNKNOWN" and g["corr_btc_h4_50"] is None
    assert all(g[f] == full[f] for f in S.CORE_FIELDS if f.startswith(("h1_", "btc_")))
    err = S.error_snapshot(SYM, AS_OF, h4=w4, h1=w1, btc=wb)
    assert err["status"] == "ERROR" and err["input_sha"] == full["input_sha"]
    assert all(err[f] is None for f in S.CORE_FIELDS if f != "is_btc") and list(err) == list(S.SNAPSHOT_FIELDS)
    json.dumps(err, allow_nan=False)


def test_invalid_rows_dropped_and_zero_volume_gives_null_ratio():
    r4, r1, rb = fixture()
    base = snap_of(r4, r1, rb)
    bad = [dict(r4[i], close=float("nan")) for i in range(690, 700)] + [dict(r4[-1], open=0.0), dict(r4[-2], low=-1.0),
                                                                          dict(r4[-3], volume=-5.0), dict(r4[-4], volume=float("nan")),
                                                                          dict(r4[-5], high=float("inf"))]
    # aynı zaman damgalı bozuk satırlar SONRA gelse de düşer (önce süzme, sonra tekilleştirme): sonuç değişmez
    assert snap_of(r4 + bad, r1, rb) == base
    # geçerli yinelenen zaman damgası: girdide SONRA gelen kazanır (borsa düzeltmesi/üst üste yazma), sıra fark etmez
    fix = dict(r4[-1], close=round(r4[-1]["close"] * 1.002, 4))
    fix["high"] = max(fix["high"], fix["close"])
    w_last = S.normalize_rows(r4 + [fix], "4h", AS_OF, W=200)
    assert w_last.c.tolist()[-1] == fix["close"] and w_last.n == 200
    w_first = S.normalize_rows([fix] + r4, "4h", AS_OF, W=200)
    assert w_first.c.tolist()[-1] == r4[-1]["close"]
    # -0.0 hacim (CSV "-0.0") +0.0 ile aynı paketlenir: kaynağa göre input_sha ayrışmaz
    nz = [dict(r) for r in r4]
    pz = [dict(r) for r in r4]
    nz[-3]["volume"], pz[-3]["volume"] = -0.0, 0.0
    assert snap_of(nz, r1, rb)["input_sha"] == snap_of(pz, r1, rb)["input_sha"]
    # benzersiz zaman damgalı bozuk satır pencereye girmez
    lone = dict(r4[-1], timestamp=r4[-1]["timestamp"] - H4 // 2, close=-3.0)
    w = S.normalize_rows(r4 + [lone], "4h", AS_OF, W=200)
    assert lone["timestamp"] not in set(w.ts.tolist())
    # önceki 20 barın hacmi 0 → oran hesaplanamaz: None (0 DEĞİL), kova None, missing'te; efor da None
    z4 = [dict(r) for r in r4]
    for r in z4[-21:-1]:
        r["volume"] = 0.0
    z = snap_of(z4, r1, rb)
    assert z["h4_vol_ratio20"] is None and z["h4_vol_bucket"] is None and z["h4_effort_result"] is None
    assert {"h4_vol_ratio20", "h4_vol_bucket", "h4_effort_result"} <= set(z["missing"]) and z["status"] == "PARTIAL"
    assert S.bucket(z, "LONG")["volume"] == "UNKNOWN"
    # son barın hacmi 0 (ortalama > 0): gerçek bir 0 oranıdır → LOW
    z4b = [dict(r) for r in r4]
    z4b[-1]["volume"] = 0.0
    zb = snap_of(z4b, r1, rb)
    assert zb["h4_vol_ratio20"] == 0.0 and zb["h4_vol_bucket"] == "LOW" and zb["status"] == "OK"


def test_deterministic_repeated_runs_and_across_processes():
    r4, r1, rb = fixture()
    a = json.dumps(snap_of(r4, r1, rb), sort_keys=False)
    for _ in range(3):
        assert json.dumps(snap_of(r4, r1, rb), sort_keys=False) == a
    # farklı kapsayıcılar: DataFrame (timestamp sütunu), DataFrame (yalnız dt indeksi), liste-dizi
    df4 = pd.DataFrame(r4)
    dti = df4.set_index(pd.to_datetime(df4["timestamp"], unit="ms", utc=True)).drop(columns=["timestamp"])
    seq1 = [[r["timestamp"], r["open"], r["high"], r["low"], r["close"], r["volume"]] for r in r1]
    assert json.dumps(snap_of(df4, seq1, pd.DataFrame(rb))) == a
    assert json.dumps(snap_of(dti, r1, rb)) == a
    code = ("import sys, json; sys.path.insert(0, %r); sys.path.insert(0, %r); import test_shared_experience_situation_v1 as T; "
            "print(json.dumps(T.snap_of(*T.fixture())))") % (str(ROOT), str(Path(__file__).resolve().parent))
    for seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120, check=True)
        assert out.stdout.strip() == a


def test_rounding_six_decimals_and_negative_zero():
    snap = snap_of(*fixture())
    for f in S.CORE_FIELDS:
        x = snap[f]
        if isinstance(x, float):
            assert round(x, 6) == x and math.isfinite(x)
    assert S._num(-1e-9) == 0.0 and math.copysign(1.0, S._num(-1e-9)) == 1.0
    assert S._num(float("nan")) is None and S._num(float("inf")) is None


# ---------------------------------------------------------------------------- kovalar
def test_bucket_edges_match_signal_lab_context():
    from tradingbot.signal_lab import context
    vol_map = {"düşük(<0.8x)": "LOW", "normal": "NORMAL", "yüksek(>1.5x)": "HIGH"}
    vx_map = {"düşük": "LOW", "normal": "NORMAL", "yüksek": "HIGH"}
    for x in (0.1, 0.7999999, 0.8, 0.8000001, 1.0, 1.2499999, 1.25, 1.2500001, 1.4999999, 1.5, 1.5000001, 3.0):
        for rsi in (5.0, 29.999, 30.0, 49.999, 50.0, 69.999, 70.0, 95.0):
            ind = {"vol_avg": np.array([1.0]), "rsi": np.array([rsi]), "ema50": np.array([np.nan]), "ema200": np.array([np.nan]),
                   "atr_pct": np.array([x]), "atr_med": np.array([1.0])}
            arr = {"close": np.array([1.0]), "volume": np.array([x])}
            ctx = context(ind, arr, 0, "LONG")
            assert S._vol_bucket(x) == vol_map[ctx["hacim"]], x
            assert S._vol_regime(x) == vx_map[ctx["volatilite"]], x
            assert S._rsi_bucket(rsi) == ctx["rsi"], rsi
    assert S._vol_regime(None) == "UNKNOWN" and S._vol_bucket(None) is None and S._rsi_bucket(None) is None


def test_bucket_dims_and_align():
    snap = dict(GOLDEN)
    assert S.bucket(snap, "LONG") == {"trend": "RANGE", "vol": "NORMAL", "btc": "DOWN", "volume": "HIGH", "structure": "MIXED",
                                      "align": "NEUTRAL"}
    for trend, side, want in (("UP", "LONG", "WITH"), ("UP", "short", "AGAINST"), ("DOWN", "SHORT", "WITH"), ("DOWN", "LONG", "AGAINST"),
                              ("RANGE", "SHORT", "NEUTRAL"), ("UNKNOWN", "LONG", "UNKNOWN"), ("UP", "BUY", "UNKNOWN"), (None, "LONG", "UNKNOWN")):
        assert S.bucket(dict(snap, h4_trend=trend), side)["align"] == want, (trend, side)
    assert S.bucket(None, "LONG") == {k: "UNKNOWN" for k in ("trend", "vol", "btc", "volume", "structure", "align")}


# ---------------------------------------------------------------------------- anlamsal senaryolar
def _trend_rows(n, step, last_open, *, up: bool, seed=5):
    g = _lcg(seed)
    rows = []
    c = 100.0
    for i in range(n):
        o = c
        wave = 0.02 * math.sin(i / 3.0)                       # geri çekilmeli dalga: salınımlar oluşsun
        c = round(o * (1 + (0.006 if up else -0.006) + wave * 0.5 + (next(g) - 0.5) * 0.002), 4)
        hi, lo = round(max(o, c) * (1.002 + 0.002 * next(g)), 4), round(min(o, c) * (0.998 - 0.002 * next(g)), 4)
        rows.append({"timestamp": last_open - (n - 1 - i) * step, "open": o, "high": hi, "low": lo, "close": c,
                     "volume": round(500 + 100 * next(g), 3)})
    return rows


def test_trend_structure_semantics_up_and_down():
    _r4, r1, rb = fixture()
    up = snap_of(_trend_rows(700, H4, LAST4, up=True), r1, rb)
    dn = snap_of(_trend_rows(700, H4, LAST4, up=False), r1, rb)
    assert (up["h4_ema_state"], up["h4_trend"], up["h4_structure"]) == (1, "UP", "HH_HL")
    assert (dn["h4_ema_state"], dn["h4_trend"], dn["h4_structure"]) == (-1, "DOWN", "LH_LL")
    assert up["h4_ema50_slope_atr"] > 0 > dn["h4_ema50_slope_atr"]
    assert S.bucket(up, "LONG")["align"] == "WITH" and S.bucket(dn, "LONG")["align"] == "AGAINST"


def test_exhaustion_and_swing_invalidation_semantics():
    n = 60
    c = [100.0 + 0.1 * math.sin(i) for i in range(n)]
    v = [1000.0] * n
    for i, (px, vol) in zip((n - 7, n - 4, n - 1), ((110.0, 3000.0), (111.0, 2000.0), (112.0, 1500.0))):
        c[i], v[i] = px, vol
    for i in range(n - 6, n - 4):
        c[i] = 105.0
    for i in range(n - 3, n - 1):
        c[i] = 106.0
    assert S._exhaustion(c, v) == "UP_EXHAUST"
    v2 = list(v)
    v2[n - 1] = 9000.0                                       # hacim artıyor → tükenme değil
    assert S._exhaustion(c, v2) == "NONE"
    assert S._exhaustion([200.0 - x for x in c], v) == "DOWN_EXHAUST"
    # salınım yükseği (20, i=3, teyit 6) sonradan ÜSTÜNDE KAPANIŞLA geçersizleşir; tepe 25 (i=15) henüz teyitsiz →
    # geçerli direnç yok: bulunamadı (N/A, eksik değil). Yalnız fitil (yüksek) geçerse seviye geçerli kalır.
    h = [10.0, 11, 12, 20, 12, 11, 10, 11, 12, 13, 14, 16, 18, 22, 24, 25, 17, 15]
    l_ = [x - 1 for x in h]
    cl = [x - 0.5 for x in h]                                 # c[13] = 21.5 > 20
    assert S._confirmed_swings(h, l_, 3)[0] == [(3, 6, 20.0)]
    dh, fh, _dl, _fl = S._swing_distances(h, l_, cl, 1.0)
    assert fh is False and dh is None
    cl2 = list(cl)
    cl2[13], cl2[14], cl2[15] = 19.5, 19.8, 19.9              # yüksekler (fitil 22/24/25) 20'yi geçti, kapanışlar geçmedi
    dh2, fh2, _, _ = S._swing_distances(h, l_, cl2, 2.0)
    assert fh2 is True and dh2 == pytest.approx((20 - 14.5) / 2.0)
    core = {f: 0.0 for f in S.CORE_FIELDS}
    core.update(h4_dist_swing_high_atr=None, h4_swing_high_found=False, h4_trend="UP", h4_vol_regime="LOW", btc_h4_trend="UP",
                btc_h4_vol_regime="LOW")
    assert S._missing(core, []) == []


def test_volume_profile_semantics():
    h = [101.0] * 100 + [130.0] * 20
    l_ = [99.0] * 100 + [128.0] * 20
    v = [1000.0] * 100 + [10.0] * 20
    pos, poc = S._volume_profile(h, l_, v, 129.0)
    assert pos == "ABOVE_VA" and 99.0 < poc < 101.0
    assert S._volume_profile(h, l_, v, 100.0)[0] == "IN_VA"
    assert S._volume_profile(h, l_, v, 98.0)[0] == "BELOW_VA"
    assert S._volume_profile([5.0] * 40, [5.0] * 40, [1.0] * 40, 5.0) == (None, None)      # dejenere aralık
    assert S._volume_profile(h, l_, [0.0] * 120, 100.0) == (None, None)                   # sıfır hacim


# ---------------------------------------------------------------------------- sözleşme / parite / saflık
def test_input_sha_packing_matches_struct_loop():
    import hashlib
    w4, w1, wb = windows(*fixture())
    hsh = hashlib.sha256()
    for role, w in (("h4", w4), ("h1", w1), ("btc", wb)):
        hsh.update(("%s|%s|%d|" % (role, w.tf, w.n)).encode("ascii"))
        for row in zip(w.ts.tolist(), w.o.tolist(), w.h.tolist(), w.l.tolist(), w.c.tolist(), w.v.tolist()):
            hsh.update(struct.pack(">q5d", *row))
    assert S.input_sha(w4, w1, wb) == hsh.hexdigest()[:16] == GOLDEN["input_sha"]


def test_vendored_confirmed_swings_equals_multitimeframe_context():
    from tradingbot.learn.multitimeframe_context import confirmed_swings
    g = _lcg(77)
    for trial in range(20):
        n = 30 + trial * 7
        hs = [round(10 + next(g) * 3, 1) for _ in range(n)]        # 1 ondalık: eşit tepeler/dipler bol
        ls = [round(x - 0.2 - next(g), 1) for x in hs]
        bars = [{"timestamp": i, "high": hs[i], "low": ls[i]} for i in range(n)]
        ref = confirmed_swings(bars, lookback=3)
        highs, lows = S._confirmed_swings(hs, ls, 3)
        assert highs == [(s["index"], s["confirmed_at_index"], s["level"]) for s in ref["highs"]]
        assert lows == [(s["index"], s["confirmed_at_index"], s["level"]) for s in ref["lows"]]


def test_closed_rule_equals_strategy_paper_and_candle_confirmation():
    from tradingbot.candle_confirmation import closed_bars
    from tradingbot.strategy_paper import expected_last_closed_open as sp_expected
    r4, r1, _rb = fixture()
    for tf, rows, step in (("4h", r4, H4), ("1h", r1, H1)):
        for as_of in (AS_OF, LAST4 + H4, LAST4 + H4 - 1, LAST1 + 1, AS_OF + 7 * H1 + 1):
            assert S.expected_last_closed_open(as_of, tf) == sp_expected(as_of, tf)
            future = rows + _future_rows(rows, step, 10)
            w = S.normalize_rows(future, tf, as_of, W=240)
            assert w.ts.tolist() == [r["timestamp"] for r in closed_bars(future, now_ms=as_of, tf=tf)][-240:]


def test_window_limits_and_snapshot_guards():
    r4, r1, rb = fixture()
    with pytest.raises(ValueError):
        S.normalize_rows(r4, "4h", AS_OF, W=241)
    with pytest.raises(ValueError):
        S.normalize_rows(r4, "4h", AS_OF, W=0)
    with pytest.raises(ValueError):
        S.normalize_rows(r4, "2h", AS_OF, W=200)
    w240 = S.normalize_rows(r4, "4h", AS_OF, W=240)
    assert w240.n == 240 and not w240.ts.flags.writeable
    w4, w1, wb = windows(r4, r1, rb)
    with pytest.raises(ValueError):                            # şema W=200'den uzun pencere
        S.snapshot(SYM, w240, w1, wb, AS_OF)
    with pytest.raises(ValueError):                            # as_of'tan sonra kapanan bar (nedensellik ihlali)
        S.snapshot(SYM, w4, w1, wb, LAST4 + H4 - 1)
    with pytest.raises(ValueError):                            # rol/dilim karışıklığı
        S.snapshot(SYM, w1, w4, wb, AS_OF)


def test_calendar_fields():
    monday = 1_790_035_200_000 - ((1_790_035_200_000 // 86_400_000 + 3) % 7) * 86_400_000
    monday -= monday % 86_400_000
    assert S.calendar(monday) == {"hour_utc": 0, "session": "ASIA", "weekday": 0}
    assert S.calendar(monday + 8 * H1) == {"hour_utc": 8, "session": "EU", "weekday": 0}
    assert S.calendar(monday + 16 * H1 - 1) == {"hour_utc": 15, "session": "EU", "weekday": 0}
    assert S.calendar(monday + 16 * H1) == {"hour_utc": 16, "session": "US", "weekday": 0}
    assert S.calendar(monday + 7 * 86_400_000 - 1) == {"hour_utc": 23, "session": "US", "weekday": 6}
    import datetime as dt
    assert dt.datetime.fromtimestamp(monday / 1000, dt.timezone.utc).weekday() == 0


_ALLOWED_IMPORTS = {"__future__", "hashlib", "json", "math", "collections", "collections.abc", "dataclasses", "typing", "numpy"}


def test_module_purity_no_io_no_books_imports():
    pkg = ROOT / "tradingbot" / "shared_experience"
    init_tree = ast.parse((pkg / "__init__.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(init_tree) if isinstance(n, (ast.Import, ast.ImportFrom))]   # KAPALI yolda hiçbir şey yüklenmez
    for name, rel_ok in (("situation.py", {("timeframes", 2)}), ("cache.py", {("situation", 1)})):
        tree = ast.parse((pkg / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(a.name in _ALLOWED_IMPORTS for a in node.names), (name, [a.name for a in node.names])
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    assert (node.module, node.level) in rel_ok, (name, node.module, node.level)
                else:
                    assert node.module in _ALLOWED_IMPORTS, (name, node.module)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "print", "exec", "eval", "__import__", "input"}, (name, node.func.id)
            elif isinstance(node, ast.Attribute):
                assert node.attr not in {"write_text", "read_text", "open", "unlink", "mkdir", "system"}, (name, node.attr)
    code = ("import sys; sys.path.insert(0, %r); import tradingbot.shared_experience as P; "
            "a = sorted(m for m in sys.modules if m.startswith('tradingbot')); "
            "import tradingbot.shared_experience.cache; "
            "b = sorted(m for m in sys.modules if m.startswith('tradingbot')); "
            "print(a); print(b); print('pandas' in sys.modules)") % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True).stdout.splitlines()
    assert out[0] == str(["tradingbot", "tradingbot.shared_experience"])
    assert out[1] == str(["tradingbot", "tradingbot.shared_experience", "tradingbot.shared_experience.cache",
                          "tradingbot.shared_experience.situation", "tradingbot.timeframes"])
    assert out[2] == "False"
