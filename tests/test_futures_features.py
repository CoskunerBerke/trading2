# -*- coding: utf-8 -*-
"""VADELİ ÖZELLİKLER (fut_v1, SPEC §4, §10 test 6–10): OI/fonlama hizalaması, nedensellik ve önek değişmezliği, canlı
REST bütçesi, bağlam kovaları. Çevrimdışı ve sentetik: ağ yok, dosya yok."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tradingbot import futures_features as FF

DAY, HOUR, MIN = FF.DAY, FF.HOUR, 60_000
T0 = 1_700_000_000_000 - 1_700_000_000_000 % DAY


def synth(step: int, n: int, seed: int = 7, *, switch_at: int | None = None, pre_days: int = 30) -> tuple:
    """Fiyat (rastgele yürüyüş), 5 dk'lık OI satırları (başlangıçtan `pre_days` önce başlar) ve 8 saatlik (switch_at'tan sonra
    4 saatlik) fonlama uzlaşmaları; calc_time'da +0..5 ms oynama."""
    rnd = np.random.default_rng(seed)
    ts = T0 + np.arange(n, dtype=np.int64) * step
    close = 100.0 * np.exp(np.cumsum(rnd.normal(0, 0.01, n)))
    start, end = T0 - pre_days * DAY, T0 + (n + 1) * step
    oi_t = np.arange(start, end, 300_000, dtype=np.int64)
    oi = 1e6 * np.exp(np.cumsum(rnd.normal(0, 0.002, oi_t.size)))
    f, t = [], start
    while t < end:
        f.append(t)
        t += 4 * HOUR if switch_at is not None and t >= switch_at else 8 * HOUR
    f_t = np.asarray(f, dtype=np.int64) + rnd.integers(0, 6, len(f))
    f_rate = rnd.normal(1e-4, 2e-4, len(f))
    return ts, close, {"oi_t": oi_t, "oi": oi, "f_t": f_t, "f_rate": f_rate}


def feats_equal(a: dict, b: dict, sl) -> None:
    for key in FF.FEATURE_KEYS:
        np.testing.assert_array_equal(np.asarray(a[key])[sl], np.asarray(b[key])[sl], err_msg=key)


# ---------------------------------------------------------------------------- 6. align_oi
def test_align_oi_uses_only_rows_between_t_minus_15m_and_t_minus_5m():
    T = T0 + 10 * HOUR
    at = lambda rows, vals=None: FF.align_oi([T], rows, vals if vals is not None else [5.0] * len(rows))[0]  # noqa: E731
    assert np.isnan(at([T]))                                  # T anında damgalı satır KULLANILMAZ
    assert at([T - 5 * MIN]) == 5.0                           # T−5dk kullanılır
    assert np.isnan(at([T - 20 * MIN]))                       # yalnız T−20dk → bayat → NaN (ileri taşıma yok)
    assert at([T - 15 * MIN]) == 5.0 and np.isnan(at([T - 15 * MIN - 1]))
    assert np.isnan(at([T - 5 * MIN + 1]))
    assert at([T - 10 * MIN, T - 5 * MIN + 1, T], [1.0, 2.0, 3.0]) == 1.0       # sınırdan sonraki satırlar görünmez
    assert at([T - 5 * MIN, T - 10 * MIN], [2.0, 1.0]) == 2.0                  # sırasız girdi savunması
    assert np.isnan(at([T - 5 * MIN], [0.0])) and np.isnan(at([T - 5 * MIN], [np.nan]))
    assert np.isnan(at([T - 5 * MIN, T - 10 * MIN], [np.nan, 1.0]))            # son satır NaN → NaN (eskiye düşmez)
    assert FF.align_oi([T], [], []).size == 1 and np.isnan(FF.align_oi([T], None, None)[0])


# ---------------------------------------------------------------------------- 7. align_funding
def test_infer_interval_from_diffs():
    t = [0, 8 * HOUR, 16 * HOUR + 3, 20 * HOUR, 21 * HOUR, 23 * HOUR, 40 * HOUR]
    np.testing.assert_array_equal(FF.infer_interval_h(t), [np.nan, 8, 8, 4, 1, 2, np.nan])
    assert FF.infer_interval_h([]).size == 0 and np.isnan(FF.infer_interval_h([5])[0])


def _grid(end: int, n: int, every: int = 8 * HOUR) -> list[int]:
    return [end - (n - 1 - j) * every for j in range(n)]


@pytest.mark.parametrize("late", [3, -30_000])
def test_funding_settlement_near_t_is_invisible_until_next_bar(late):
    T, step = T0 + 40 * DAY, 4 * HOUR
    f_t = _grid(T - 8 * HOUR, 40) + [T + late]
    rate = [1e-4] * 40 + [9e-4]
    out = FF.align_funding([T, T + step], f_t, rate)
    assert out["f8"][0] == pytest.approx(1e-4)                # T+3 ms ve T−30 sn: T'de görünmez
    assert out["f8"][1] == pytest.approx(9e-4)                # sonraki barda görünür


def test_funding_settlement_61s_before_t_is_visible():
    T = T0 + 40 * DAY
    f_t = _grid(T - 61_000, 40)
    rate = [1e-4] * 39 + [7e-4]
    assert FF.align_funding([T], f_t, rate)["f8"][0] == pytest.approx(7e-4)
    f_t[-1] = T - 59_999
    assert FF.align_funding([T], f_t, rate)["f8"][0] == pytest.approx(1e-4)


def test_funding_interval_switch_scales_to_8h():
    T = T0 + 40 * DAY
    f_t = _grid(T - 4 * HOUR - MIN, 40) + [T - MIN]           # son fark 4 saat → aralık 4
    rate = [1e-4] * 40 + [3e-4]
    assert FF.align_funding([T], f_t, rate)["f8"][0] == pytest.approx(6e-4)     # ×8/4 = ×2
    f_t2 = _grid(T - MIN, 40, 4 * HOUR)
    assert FF.align_funding([T], f_t2, [2e-4] * 40)["f8"][0] == pytest.approx(4e-4)
    f_t3 = _grid(T - MIN, 40, HOUR)
    assert FF.align_funding([T], f_t3, [1e-5] * 40)["f8"][0] == pytest.approx(8e-5)


def test_funding_gap_and_staleness_give_nan():
    base = T0 + 40 * DAY
    f_t = _grid(base, 40) + [base + 24 * HOUR, base + 32 * HOUR]      # 16 saat boşluk
    rate = [1e-4] * 42
    T = np.array([base + 9 * HOUR + 60_000, base + 9 * HOUR + 60_001, base + 20 * HOUR,
                  base + 24 * HOUR + 2 * MIN, base + 32 * HOUR + 2 * MIN], dtype=np.int64)
    f8 = FF.align_funding(T, f_t, rate)["f8"]
    assert f8[0] == pytest.approx(1e-4)                      # yaş = 8s + 1s + 60 sn: sınırda hâlâ geçerli
    assert np.isnan(f8[1]) and np.isnan(f8[2])               # bayat → NaN (ileri taşıma yok)
    assert np.isnan(f8[3])                                    # 16 saatlik fark → aralık bilinmiyor → NaN
    assert f8[4] == pytest.approx(1e-4)                      # boşluktan sonraki ikinci uzlaşma normal
    assert np.isnan(FF.align_funding([base + DAY], [base], [1e-4])["f8"][0])     # ilk satırın aralığı yok → NaN


def test_funding_rank_needs_min_n_and_is_strict():
    T = T0 + 40 * DAY
    few = _grid(T - MIN, FF.FUND_MIN_N + 1)                    # güncel + 30 diğer; ilkinin aralığı yok → 29 sonlu
    out = FF.align_funding([T], few, np.linspace(1e-4, 5e-4, len(few)))
    assert np.isfinite(out["f8"][0]) and np.isnan(out["f8_hi"][0]) and np.isnan(out["f8_lo"][0])
    enough = _grid(T - MIN, FF.FUND_MIN_N + 2)
    out = FF.align_funding([T], enough, np.linspace(1e-4, 5e-4, len(enough)))
    assert out["f8_hi"][0] == 1.0 and out["f8_lo"][0] == 0.0
    pinned = _grid(T - MIN, 55)
    out = FF.align_funding([T], pinned, [1e-4] * 55)
    assert out["f8"][0] == pytest.approx(1e-4)
    assert out["f8_hi"][0] == 0.0 and out["f8_lo"][0] == 0.0    # 1 bp'de çakılı oran uç DEĞİL
    assert FF.ctx_labels({"f8": out["f8"]}, 0)["fonlama"] == "taban(0–1bp)"


def test_funding_rank_window_is_20_days_and_excludes_current():
    T = T0 + 60 * DAY
    f_t = _grid(T - MIN, 100)                                  # ~33 gün
    rate = np.arange(100, dtype=float) * 1e-6 + 1e-4
    rate[-1] = rate[-2] + 0.5e-6                               # güncel: en büyük
    rate[:30] = 1.0                                             # 20 günden eski: sıraya GİRMEZ
    out = FF.align_funding([T], f_t, rate)
    assert out["f8_hi"][0] == 1.0 and out["f8_lo"][0] == 0.0
    edge = _grid(T - 8 * HOUR, 100)                            # edge[40] == T − 20 gün tam
    assert edge[40] == T - FF.WINDOW_DAYS * DAY
    rate2 = np.full(100, 1e-4)
    rate2[-1] = 2e-4
    rate2[40] = 5.0                                            # calc == T − 20 gün → pencere dışı (açık aralık)
    assert FF.align_funding([T], edge, rate2)["f8_lo"][0] == 0.0
    rate2[41] = 5.0                                            # bir sonraki içeride → sayılır (58 diğerinden 1'i)
    assert FF.align_funding([T], edge, rate2)["f8_lo"][0] == pytest.approx(1 / 58)


# ---------------------------------------------------------------------------- bar_features
@pytest.mark.parametrize("tf,k,w", [("4h", 6, 120), ("1h", 24, 480), ("1d", 1, 20)])
def test_horizon_and_window_bars(tf, k, w):
    from tradingbot.timeframes import tf_ms
    assert FF.k_bars(tf_ms(tf)) == k and FF.w_bars(tf_ms(tf)) == w


def test_bar_features_match_the_written_formulas():
    step = 4 * HOUR
    ts, close, raw = synth(step, 400)
    f = FF.bar_features(ts, step, close, raw)
    k, W = 6, 120
    T = ts + step
    np.testing.assert_array_equal(f["oi"], FF.align_oi(T, raw["oi_t"], raw["oi"]))
    ok = np.flatnonzero(f["OI_OK"] & f["FUND_OK"])
    assert ok.size > 100 and f["OI_OK"].dtype == bool and f["FUND_OK"].dtype == bool
    for i in ok[::37]:
        a = max(0, i - W)                                        # pencere başı serinin başından önceyse eksik yer NaN
        assert f["doi"][i] == pytest.approx(np.log(f["oi"][i] / f["oi"][i - k]))
        assert f["dpx"][i] == pytest.approx(np.log(close[i] / close[i - k]))
        win = f["doi"][a:i]
        win = win[np.isfinite(win)]
        assert f["oi_z"][i] == pytest.approx(f["doi"][i] / np.std(win), rel=1e-9)
        pw = f["dpx"][a:i]
        pw = pw[np.isfinite(pw)]
        assert f["px_z"][i] == pytest.approx(f["dpx"][i] / np.std(pw), rel=1e-9)
        ow = f["oi"][a:i]
        ow = ow[np.isfinite(ow)]
        assert f["oi_pct"][i] == pytest.approx(np.mean(ow < f["oi"][i]))
    first_z = np.flatnonzero(np.isfinite(f["oi_z"]))[0]
    assert first_z == k + W // 2                                # doi[k..] sonlu; en az W//2 değer
    assert not f["OI_OK"][first_z]                              # px_z[i−1] de gerekir
    assert f["OI_OK"][first_z + 1]


def test_bar_features_nan_inputs_and_missing_sources():
    step = 4 * HOUR
    ts, close, raw = synth(step, 300)
    f = FF.bar_features(ts, step, close, None)
    for key in ("oi", "doi", "oi_z", "oi_pct", "f8", "f8_hi", "f8_lo"):
        assert np.isnan(f[key]).all(), key
    assert not f["OI_OK"].any() and not f["FUND_OK"].any() and np.isfinite(f["px_z"]).any()
    assert FF.bar_features([], step, [], raw)["OI_OK"].size == 0
    # OI'de 2 saatlik delik: o barlarda oi NaN, doi/oi_z NaN, OI_OK False; ileri taşıma yok
    T = ts + step
    hole = (raw["oi_t"] > T[200] - 2 * HOUR) & (raw["oi_t"] <= T[200])
    raw2 = {**raw, "oi_t": raw["oi_t"][~hole], "oi": raw["oi"][~hole]}
    g = FF.bar_features(ts, step, close, raw2)
    assert np.isnan(g["oi"][200]) and np.isnan(g["doi"][200]) and not g["OI_OK"][200]
    assert np.isnan(g["doi"][206])                              # oi[i−k] NaN → doi NaN
    # sabit fiyat: std = 0 → z NaN
    h = FF.bar_features(ts, step, np.full(ts.size, 100.0), raw)
    assert np.isnan(h["px_z"]).all() and not h["OI_OK"].any()


# ---------------------------------------------------------------------------- 8. nedensellik / önek değişmezliği
@pytest.mark.parametrize("tf,n", [("4h", 420), ("1h", 900), ("1d", 70)])
def test_features_are_causal_and_prefix_invariant(tf, n):
    from tradingbot.timeframes import tf_ms
    step = tf_ms(tf)
    ts, close, raw = synth(step, n, seed=11, switch_at=int(T0 + (n // 2) * step))
    full = FF.bar_features(ts, step, close, raw)
    good = np.flatnonzero(full["OI_OK"] & full["FUND_OK"])
    assert good.size > 10
    rnd = np.random.default_rng(3)
    for i in list(good[[0, good.size // 2, -2]]) + [n - 2]:
        Ti = int(ts[i] + step)
        # i'den sonra damgalı her şeyi boz: fiyat, T−5dk sonrası OI, T−60 sn sonrası fonlama (değer + zaman + ek satır)
        c2 = close.copy()
        c2[i + 1:] *= rnd.uniform(0.5, 2.0, n - i - 1)
        fut_oi = raw["oi_t"] > Ti - FF.OI_LAG_MS
        oi2 = raw["oi"].copy()
        oi2[fut_oi] *= rnd.uniform(0.1, 10.0, int(fut_oi.sum()))
        fut_f = raw["f_t"] > Ti - FF.FUND_GUARD_MS
        fr2 = raw["f_rate"].copy()
        fr2[fut_f] = rnd.normal(0, 1e-2, int(fut_f.sum()))
        ft2 = raw["f_t"].copy()
        ft2[fut_f] += rnd.integers(0, 3 * HOUR, int(fut_f.sum()))
        ft2 = np.maximum.accumulate(ft2)
        pos = int(np.searchsorted(raw["oi_t"], Ti - FF.OI_LAG_MS + 1))
        oi_t3 = np.insert(raw["oi_t"], pos, Ti - FF.OI_LAG_MS + 1)
        oi3 = np.insert(oi2, pos, 1e12)
        ft3 = np.concatenate([ft2, [Ti - FF.FUND_GUARD_MS + 1]])
        fr3 = np.concatenate([fr2, [5.0]])
        o = np.argsort(ft3, kind="stable")
        pert = FF.bar_features(ts, step, c2, {"oi_t": oi_t3, "oi": oi3, "f_t": ft3[o], "f_rate": fr3[o]})
        feats_equal(full, pert, slice(0, i + 1))
        # i+1'de kesilmiş seri + yalnız o ana kadar görünür satırlar → aynı özellikler
        keep_oi = raw["oi_t"] <= Ti - FF.OI_LAG_MS
        keep_f = raw["f_t"] <= Ti - FF.FUND_GUARD_MS
        trunc = FF.bar_features(ts[:i + 1], step, close[:i + 1],
                                {"oi_t": raw["oi_t"][keep_oi], "oi": raw["oi"][keep_oi],
                                 "f_t": raw["f_t"][keep_f], "f_rate": raw["f_rate"][keep_f]})
        feats_equal(full, trunc, slice(0, i + 1))
        for dim, lab in FF.ctx_labels(full, i).items():
            assert FF.ctx_labels(trunc, i)[dim] == lab and FF.ctx_labels(pert, i)[dim] == lab


# ---------------------------------------------------------------------------- 9. canlı bütçe
@pytest.mark.parametrize("tf,n", [("1h", 1300), ("4h", 360), ("1d", 70)])
def test_live_budget_fits_rest_window_and_truncated_history_gives_same_features(tf, n):
    from tradingbot.timeframes import tf_ms
    step = tf_ms(tf)
    req = FF.live_requirements(tf)
    assert set(req) == {"oi_ms", "funding_ms"}
    assert req["oi_ms"] == (FF.w_bars(step) + FF.k_bars(step)) * step + FF.OI_STALE_MS
    assert req["oi_ms"] <= FF.LIVE_OI_MAX_MS and req["funding_ms"] <= FF.LIVE_OI_MAX_MS
    ts, close, raw = synth(step, n, seed=5, pre_days=45)
    full = FF.bar_features(ts, step, close, raw)
    good = np.flatnonzero(full["OI_OK"] & full["FUND_OK"])
    assert good.size > 10
    checked = 0
    for i in good[[0, good.size // 2, -1]]:
        Ti = int(ts[i] + step)
        for oi_span, f_span in ((FF.LIVE_OI_MAX_MS, FF.LIVE_OI_MAX_MS), (req["oi_ms"], req["funding_ms"])):
            ko = (raw["oi_t"] >= Ti - oi_span) & (raw["oi_t"] <= Ti)
            kf = (raw["f_t"] >= Ti - f_span) & (raw["f_t"] <= Ti)
            lim = FF.bar_features(ts, step, close, {"oi_t": raw["oi_t"][ko], "oi": raw["oi"][ko],
                                                   "f_t": raw["f_t"][kf], "f_rate": raw["f_rate"][kf]})
            feats_equal(full, lim, slice(i, i + 1))
        # duyarlılık: bütçenin 11 dk altı oi[i−W−k] satırlarını düşürür → oi_z değişir (test boş geçmiyor)
        if i < FF.w_bars(step) or not np.isfinite(full["doi"][i - FF.w_bars(step)]):
            continue
        checked += 1
        ko = (raw["oi_t"] >= Ti - req["oi_ms"] + 11 * MIN) & (raw["oi_t"] <= Ti)
        short = FF.bar_features(ts, step, close, {**raw, "oi_t": raw["oi_t"][ko], "oi": raw["oi"][ko]})
        assert short["oi_z"][i] != full["oi_z"][i]
    assert checked >= 2


# ---------------------------------------------------------------------------- 10. ctx_labels
def test_ctx_labels_bucket_edges_and_unknown():
    def lab(dpx=np.nan, doi=np.nan, pct=np.nan, f8=np.nan):
        return FF.ctx_labels({"dpx": np.array([dpx]), "doi": np.array([doi]), "oi_pct": np.array([pct]), "f8": np.array([f8])}, 0)
    assert lab() == {"oi_rejim": "bilinmiyor", "oi_seviye": "bilinmiyor", "fonlama": "bilinmiyor"}
    assert list(lab()) == ["oi_rejim", "oi_seviye", "fonlama"]
    assert lab(0.01, 0.02)["oi_rejim"] == "fiyat↑OI↑"
    assert lab(0.01, -0.02)["oi_rejim"] == "fiyat↑OI↓"
    assert lab(-0.01, 0.02)["oi_rejim"] == "fiyat↓OI↑"
    assert lab(-0.01, -0.02)["oi_rejim"] == "fiyat↓OI↓"
    assert lab(0.0, 0.0)["oi_rejim"] == "fiyat↓OI↓"             # yalnız işaret: 0 → ↓ (doi ≤ 0 kontrol kenarı)
    assert lab(np.nan, 0.1)["oi_rejim"] == "bilinmiyor" and lab(0.1, np.nan)["oi_rejim"] == "bilinmiyor"
    assert lab(pct=0.0)["oi_seviye"] == "düşük(≤%20)" and lab(pct=0.2)["oi_seviye"] == "düşük(≤%20)"
    assert lab(pct=0.2000001)["oi_seviye"] == "orta" and lab(pct=0.7999999)["oi_seviye"] == "orta"
    assert lab(pct=0.8)["oi_seviye"] == "yüksek(≥%80)" and lab(pct=1.0)["oi_seviye"] == "yüksek(≥%80)"
    assert lab(f8=-1e-9)["fonlama"] == "negatif(<0)"
    assert lab(f8=0.0)["fonlama"] == "taban(0–1bp)" and lab(f8=1e-4)["fonlama"] == "taban(0–1bp)"
    assert lab(f8=FF.FUND_BASE + FF.EPS)["fonlama"] == "taban(0–1bp)"
    assert lab(f8=1.0001e-4)["fonlama"] == "yüksek(>1bp)"
    assert FF.ctx_labels({}, 0) == lab()                          # eksik anahtar → bilinmiyor
    seen = {d: set() for d in FF.CTX_BUCKETS}
    for a in (-1, 0.5):
        for b in (-1, 0.5):
            for p in (0.1, 0.5, 0.9):
                for f in (-1e-4, 1e-4, 1e-3):
                    for d, v in lab(a, b, p, f).items():
                        seen[d].add(v)
    assert {d: tuple(sorted(v)) for d, v in seen.items()} == {d: tuple(sorted(b)) for d, b in FF.CTX_BUCKETS.items()}
    assert sum(len(b) for b in FF.CTX_BUCKETS.values()) == 10


# ---------------------------------------------------------------------------- saflık
def test_module_is_pure_and_does_not_import_signal_lab():
    code = ("import sys, tradingbot.futures_features as F; "
            "bad = [m for m in sys.modules if m.startswith('tradingbot.') and m not in ('tradingbot.futures_features', 'tradingbot.timeframes')]; "
            "bad += [m for m in ('urllib.request', 'requests', 'pandas') if m in sys.modules]; print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=str(Path(__file__).resolve().parents[1])).stdout.strip()
    assert out == ""


def test_constants_match_spec():
    assert (FF.WINDOW_DAYS, FF.LIVE_OI_MAX_MS, FF.OI_LAG_MS, FF.OI_STALE_MS, FF.FUND_GUARD_MS, FF.FUND_MIN_N, FF.FUND_BASE, FF.EPS) == \
        (20, 28 * DAY, 300_000, 900_000, 60_000, 30, 0.0001, 1e-12)
    assert FF.FEATURE_CONSTANTS["INTERVALS_H"] == [1, 2, 4, 8]
