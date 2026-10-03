# -*- coding: utf-8 -*-
"""VADELİ ÖZELLİKLER (fut_v1) — açık pozisyon (OI) ve fonlama oranından bar başına NEDENSEL özellikler.

Salt fonksiyonlar: dosya/ağ yok, girdiler değiştirilmez, `signal_lab` içe aktarılmaz. Laboratuvar (`futures_lab`) bugün,
canlı PAPER tarafı ileride AYNI fonksiyonları çağırır; pencereler canlı REST bütçesine sığar (`live_requirements`,
testle bağlı: `LIVE_OI_MAX_MS`).

Gösterim: bar `i` `ts[i]`'de açılır; karar anı `T_i = ts[i] + step` (= `Event.t_ms`). `k = max(1, GÜN // step)` 24 saatlik
ufuk (4h → 6, 1h → 24, 1d → 1); `W = max(2, WINDOW_DAYS·GÜN // step)` z/sıra penceresi (4h → 120, 1h → 480, 1d → 20).

Nedensellik sözleşmesi (SPEC fut_v1 §2.2, §4):
* OI: `T_i − OI_STALE_MS ≤ t ≤ T_i − OI_LAG_MS` aralığındaki SON satır (T−15dk … T−5dk). `create_time` pencere başı da
  olsa sonu da olsa güvenli. Aralıkta satır yoksa NaN; ileri taşıma YOK.
* Fonlama: uzlaşma `calc ≤ T_i − FUND_GUARD_MS` ise görünür (60 sn: ms oynamasını emer, canlıda tekrarlanabilir). Aralık
  (1/2/4/8 saat) arşiv ve REST için AYNI yolla, ardışık farktan çıkarılır; ilk satır ve boşluk sonrası NaN.
* Herhangi bir girdide NaN → özellik NaN → bağlam kovası `bilinmiyor`; kural/kontrol/plasebo ATEŞLEMEZ.

Sabitler `futures_lab.FUT_REGISTRY`'ye `FEATURE_CONSTANTS` ile girer; biri değişirse fut_v2 (kural sonuçtan sonra gevşetilmez).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .timeframes import tf_ms

DAY = 86_400_000
HOUR = 3_600_000

WINDOW_DAYS = 20              # bütün kayan z / sıra pencereleri; OI geriye bakışı (W+k)·step + 15dk ≈ 21 gün ≤ LIVE_OI_MAX_MS
LIVE_OI_MAX_MS = 28 * DAY     # REST openInterestHist ~30 gün tutar; testle zorlanır
OI_LAG_MS = 300_000           # OI satırı T−15dk ≤ t ≤ T−5dk olmalı
OI_STALE_MS = 900_000
FUND_GUARD_MS = 60_000        # uzlaşma i barında yalnız calc ≤ T_i − 60 sn ise görünür
FUND_MIN_N = 30               # sıra için 20 günlük pencerede gereken uzlaşma sayısı
FUND_BASE = 0.0001            # 8 saatte 1 bp, Binance tabanı
EPS = 1e-12
INTERVALS_H = (1, 2, 4, 8)    # kabul edilen fonlama aralıkları (saat); başka fark → NaN
OI_PCT_LOW = 0.2              # oi_seviye kova kenarları (≤ düşük, ≥ yüksek)
OI_PCT_HIGH = 0.8

FEATURE_CONSTANTS: dict[str, Any] = {
    "WINDOW_DAYS": WINDOW_DAYS, "LIVE_OI_MAX_MS": LIVE_OI_MAX_MS, "OI_LAG_MS": OI_LAG_MS, "OI_STALE_MS": OI_STALE_MS,
    "FUND_GUARD_MS": FUND_GUARD_MS, "FUND_MIN_N": FUND_MIN_N, "FUND_BASE": FUND_BASE, "EPS": EPS,
    "INTERVALS_H": list(INTERVALS_H), "OI_PCT_LOW": OI_PCT_LOW, "OI_PCT_HIGH": OI_PCT_HIGH,
    "FUND_STALE_RULE": "calc yaşı > aralık_saat·1s + 1s + FUND_GUARD_MS → NaN",
}

FEATURE_KEYS = ("oi", "doi", "dpx", "oi_z", "px_z", "oi_pct", "f8", "f8_hi", "f8_lo", "OI_OK", "FUND_OK")

UNKNOWN = "bilinmiyor"
REJIM_BUCKETS = ("fiyat↑OI↑", "fiyat↑OI↓", "fiyat↓OI↑", "fiyat↓OI↓")
SEVIYE_BUCKETS = ("düşük(≤%20)", "orta", "yüksek(≥%80)")
FONLAMA_BUCKETS = ("negatif(<0)", "taban(0–1bp)", "yüksek(>1bp)")
CTX_BUCKETS: dict[str, tuple[str, ...]] = {"oi_rejim": REJIM_BUCKETS, "oi_seviye": SEVIYE_BUCKETS, "fonlama": FONLAMA_BUCKETS}

_CHUNK = 2048                 # kayan pencere hesabı bu kadar satırlık parçalarla (bellek sınırı; sonuç parçadan bağımsız)


def k_bars(step: int) -> int:
    """24 saatlik ufuk (bar)."""
    return max(1, DAY // int(step))


def w_bars(step: int) -> int:
    """z / sıra penceresi (bar)."""
    return max(2, WINDOW_DAYS * DAY // int(step))


def live_requirements(tf: str) -> dict[str, int]:
    """Canlı tarafın i barı için geriye doğru ihtiyacı (T_i'den ms). `oi_ms`: doi[i−W] için oi[i−W−k] satırı T−15dk'ya kadar
    → (W+k)·step + OI_STALE_MS. `funding_ms`: 20 günlük sıra penceresi + penceredeki ilk uzlaşmanın aralığını çıkarmak için
    bir önceki uzlaşma (≤ 8 saat, yuvarlama payı 8,5 saat → 9 saat)."""
    step = tf_ms(tf)
    return {"oi_ms": (w_bars(step) + k_bars(step)) * step + OI_STALE_MS,
            "funding_ms": WINDOW_DAYS * DAY + (max(INTERVALS_H) + 1) * HOUR}


def _f(x: Any) -> np.ndarray:
    return np.asarray(x if x is not None else [], dtype=float)


def _t(x: Any) -> np.ndarray:
    return np.asarray(x if x is not None else [], dtype=np.int64)


def _sorted(t: np.ndarray, *vals: np.ndarray) -> tuple[np.ndarray, ...]:
    """Zamana göre kararlı sıralama (ayrıştırıcı zaten sıralar; savunma). Aynı zamanlıda son satır searchsorted ile kazanır."""
    if t.size > 1 and np.any(np.diff(t) < 0):
        o = np.argsort(t, kind="stable")
        return (t[o],) + tuple(v[o] for v in vals)
    return (t,) + vals


def _log_ratio(x: np.ndarray, k: int) -> np.ndarray:
    """ln(x[i] / x[i−k]); i < k, NaN ya da ≤ 0 → NaN."""
    out = np.full(x.size, np.nan)
    if x.size > k:
        a, b = x[k:], x[:-k]
        ok = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
        out[k:][ok] = np.log(a[ok] / b[ok])
    return out


def _prev_windows(x: np.ndarray, W: int, fn) -> np.ndarray:
    """Her i için `fn(pencere, x[i])`, pencere = x[i−W .. i−1] (i'nin KENDİSİ hariç; baştaki eksik yer NaN). Parça parça ve
    satır başına aynı indirgeme: sonuç yalnız pencere içeriğine bağlıdır (önek / kırpma değişmezliği birebir)."""
    n = x.size
    out = np.full(n, np.nan)
    if n == 0:
        return out
    xp = np.concatenate([np.full(W, np.nan), x])
    view = np.lib.stride_tricks.sliding_window_view(xp, W)
    for a in range(0, n, _CHUNK):
        b = min(n, a + _CHUNK)
        out[a:b] = fn(np.ascontiguousarray(view[a:b]), x[a:b])
    return out


def _z_fn(W: int):
    need = W // 2

    def fn(win: np.ndarray, cur: np.ndarray) -> np.ndarray:
        fin = np.isfinite(win)
        cnt = fin.sum(axis=1)
        v = np.where(fin, win, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = v.sum(axis=1) / cnt
            dev = np.where(fin, win - mean[:, None], 0.0)
            sd = np.sqrt((dev * dev).sum(axis=1) / cnt)          # ddof=0
            z = cur / sd
        ok = (cnt >= need) & np.isfinite(sd) & (sd > EPS) & np.isfinite(cur)
        return np.where(ok, z, np.nan)
    return fn


def _pct_fn(W: int):
    need = W // 2

    def fn(win: np.ndarray, cur: np.ndarray) -> np.ndarray:
        fin = np.isfinite(win)
        cnt = fin.sum(axis=1)
        below = (fin & (win < cur[:, None])).sum(axis=1)       # KESİN küçük
        with np.errstate(invalid="ignore", divide="ignore"):
            p = below / cnt
        return np.where((cnt >= need) & np.isfinite(cur), p, np.nan)
    return fn


def align_oi(t_close: Any, oi_t: Any, oi: Any) -> np.ndarray:
    """Her karar anı T için `T − OI_STALE_MS ≤ t ≤ T − OI_LAG_MS` olan SON OI satırının değeri; yoksa (ya da değer NaN/≤ 0) NaN."""
    T = _t(t_close)
    ot, ov = _sorted(_t(oi_t), _f(oi))
    out = np.full(T.size, np.nan)
    if ot.size == 0 or T.size == 0:
        return out
    j = np.searchsorted(ot, T - OI_LAG_MS, side="right") - 1
    has = j >= 0
    jj = np.where(has, j, 0)
    ok = has & (ot[jj] >= T - OI_STALE_MS)
    val = ov[jj]
    ok &= np.isfinite(val) & (val > 0)
    out[ok] = val[ok]
    return out


def infer_interval_h(f_t: Any) -> np.ndarray:
    """Uzlaşma başına aralık (saat) = round(fark / 1 saat) ∈ {1,2,4,8}, değilse NaN; ilk satır NaN (arşiv = REST, aynı yol)."""
    t = _t(f_t)
    out = np.full(t.size, np.nan)
    if t.size > 1:
        h = np.round(np.diff(t).astype(float) / HOUR)
        out[1:] = np.where(np.isin(h, INTERVALS_H), h, np.nan)
    return out


def align_funding(t_close: Any, f_t: Any, f_rate: Any) -> dict[str, np.ndarray]:
    """`f8`: görünür (calc ≤ T − FUND_GUARD_MS) son uzlaşmanın 8 saate ölçeklenmiş oranı (oran × 8 / aralık); uzlaşma
    `aralık·1s + 1s + 60 sn`'den eskiyse NaN. `f8_hi` / `f8_lo`: calc ∈ (T − 20 gün, güncel) diğer uzlaşmaların (sonlu f8)
    güncelden KESİN küçük / KESİN büyük olanlarının payı; en az FUND_MIN_N değer gerekir (1 bp'de çakılı oran uç değildir)."""
    T = _t(t_close)
    ft, fr = _sorted(_t(f_t), _f(f_rate))
    n = T.size
    f8 = np.full(n, np.nan)
    hi = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    if ft.size == 0 or n == 0:
        return {"f8": f8, "f8_hi": hi, "f8_lo": lo}
    iv = infer_interval_h(ft)
    with np.errstate(invalid="ignore", divide="ignore"):
        s8 = fr * 8.0 / iv
    j = np.searchsorted(ft, T - FUND_GUARD_MS, side="right") - 1
    lo_idx = np.searchsorted(ft, T - WINDOW_DAYS * DAY, side="right")          # calc > T − 20 gün
    for i in range(n):
        ji = int(j[i])
        if ji < 0:
            continue
        cur = s8[ji]
        if not math.isfinite(cur) or (T[i] - ft[ji]) > iv[ji] * HOUR + HOUR + FUND_GUARD_MS:
            continue
        f8[i] = cur
        a = int(lo_idx[i])
        if ji - a < FUND_MIN_N:
            continue
        others = s8[a:ji]
        others = others[np.isfinite(others)]
        if others.size < FUND_MIN_N:
            continue
        hi[i] = float((others < cur).sum()) / others.size
        lo[i] = float((others > cur).sum()) / others.size
    return {"f8": f8, "f8_hi": hi, "f8_lo": lo}


def bar_features(ts: Any, step: int, close: Any, raw: dict | None) -> dict[str, np.ndarray]:
    """Bar başına bütün vadeli özellikler (`FEATURE_KEYS`). `raw` = {"oi_t","oi","f_t","f_rate"} (önbellek satırları; eksik
    anahtar / None → o kaynak NaN). `OI_OK` / `FUND_OK` bool dizileridir."""
    step = int(step)
    ts_ = _t(ts)
    c = _f(close)
    raw = raw or {}
    T = ts_ + step
    k, W = k_bars(step), w_bars(step)
    oi = align_oi(T, raw.get("oi_t"), raw.get("oi"))
    doi = _log_ratio(oi, k)
    dpx = _log_ratio(c, k)
    oi_z = _prev_windows(doi, W, _z_fn(W))
    px_z = _prev_windows(dpx, W, _z_fn(W))
    oi_pct = _prev_windows(oi, W, _pct_fn(W))
    fund = align_funding(T, raw.get("f_t"), raw.get("f_rate"))
    px_prev = np.concatenate([[np.nan], px_z[:-1]]) if px_z.size else px_z
    oi_ok = np.isfinite(doi) & np.isfinite(oi_z) & np.isfinite(oi_pct) & np.isfinite(px_z) & np.isfinite(px_prev)
    fund_ok = np.isfinite(fund["f8"]) & np.isfinite(fund["f8_hi"]) & np.isfinite(fund["f8_lo"])
    return {"oi": oi, "doi": doi, "dpx": dpx, "oi_z": oi_z, "px_z": px_z, "oi_pct": oi_pct,
            "f8": fund["f8"], "f8_hi": fund["f8_hi"], "f8_lo": fund["f8_lo"], "OI_OK": oi_ok, "FUND_OK": fund_ok}


def _num(feat: dict, key: str, i: int) -> float:
    try:
        v = float(feat[key][i])
    except (KeyError, IndexError, TypeError, ValueError):
        return float("nan")
    return v


def ctx_labels(feat: dict, i: int) -> dict[str, str]:
    """Bağlam kovaları (yalnız KEŞİF; `--futures ctx|rules`). `oi_rejim` eşiksiz, yalnız işaret: ↑ = > 0, ↓ = ≤ 0 (sıfır
    değişim ↓ sayılır; #1 kontrolünün `doi ≤ 0` kenarıyla aynı). NaN → `bilinmiyor`."""
    dpx, doi = _num(feat, "dpx", i), _num(feat, "doi", i)
    pct, f8 = _num(feat, "oi_pct", i), _num(feat, "f8", i)
    if math.isnan(dpx) or math.isnan(doi):
        rejim = UNKNOWN
    else:
        rejim = "fiyat%sOI%s" % ("↑" if dpx > 0 else "↓", "↑" if doi > 0 else "↓")
    if math.isnan(pct):
        seviye = UNKNOWN
    else:
        seviye = SEVIYE_BUCKETS[0] if pct <= OI_PCT_LOW else (SEVIYE_BUCKETS[2] if pct >= OI_PCT_HIGH else SEVIYE_BUCKETS[1])
    if math.isnan(f8):
        fon = UNKNOWN
    else:
        fon = FONLAMA_BUCKETS[0] if f8 < 0 else (FONLAMA_BUCKETS[1] if f8 <= FUND_BASE + EPS else FONLAMA_BUCKETS[2])
    return {"oi_rejim": rejim, "oi_seviye": seviye, "fonlama": fon}
