# -*- coding: utf-8 -*-
"""KALABALIK ÖZELLİKLERİ (fut_v2 laboratuvarı + ileride canlı flow_v1 kayıtçısının ORTAK matematiği) — taker (agresif
alış/satış) akışı, açık pozisyon (OI), long/short oranları ve fonlamadan bar başına NEDENSEL özellikler (2026-09-30).

Salt fonksiyonlar: dosya/ağ yok, girdiler değiştirilmez; yalnız stdlib + numpy, `futures_features` ve `timeframes` içe
aktarılır (testle bağlı). Laboratuvar (`crowd_lab`, fut_v2) bugün tam seriler üzerinde vektörel çağırır; canlı kayıtçı
ileride AYNI dizilerin son indeksini okur (lab/canlı eşliği yapı gereği; `futures_features` emsali).

Gösterim fut_v1 ile aynı: bar i `ts[i]`'de açılır, karar anı `T_i = ts[i] + step`. k = 24 saat (4h 6, 1h 24 bar),
W = 20 gün (4h 120, 1h 480 bar) — `FF.k_bars` / `FF.w_bars`.

Nedensellik ve eksik veri (2026-09-30):
* Mumlar yalnız kapanmış barlardır (çağıran verir). Taker `tb` geçersizse (sonlu değil, v < 0, tb < 0, tb > v·(1+1e-9))
  o barın `tb`'si NaN olur; bar ATILMAZ.
* Metrics (OI ve L/S oranları): `T − 15 dk ≤ t ≤ T − 5 dk` aralığındaki SON satır (fut_v1 `align_oi` kuralının aynısı);
  değer sonlu ve > 0 değilse NaN (daha eski satıra düşülmez).
* Fonlama: fut_v1 `align_funding` (uzlaşma yalnız calc ≤ T − 60 sn ise görünür).
* z pencereleri (`zdm`) HER ZAMAN i'nin kendisini hariç tutar ve yalnız pencere içeriğine bağlıdır (önek değişmezliği).
  Toplam/ortalama pencereleri de satır başına aynı indirgemeyle hesaplanır (kümülatif toplam farkı KULLANILMAZ).
* Eksik girdi → NaN. Kategorik alanlar float kod döner: NaN = UNKNOWN (bağlam kovasında `bilinmiyor`). NaN hiçbir zaman
  0'a çevrilmez, ileri taşınmaz.

Sabitler `CROWD_FEATURE_CONSTANTS`'ta; `crowd_lab.CROWD_REGISTRY` mührüne girer. Biri değişirse yeni sürüm (fut_v3 /
flow_v2) gerekir; kural sonuç görüldükten sonra gevşetilmez.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from . import futures_features as FF
from .timeframes import tf_ms

DAY, HOUR = FF.DAY, FF.HOUR

# ---------------------------------------------------------------------------- sabitler (2026-09-30, ön kayıt)
DIV_LOOKBACK = 20             # D: ıraksama penceresi (bar)
Z_FLOOR_SHARE = 0.01          # dshare / cvd_share z paydası tabanı
Z_FLOOR_LOG = 0.02            # ln(oran) z paydası tabanı
Z_FLOOR_FUND = 0.000025       # f8_z paydası tabanı (0,25 bp)
Z_CLIP = 3.0                  # crowd_z bileşenleri ±3'te kırpılır
CROWD_Z = 1.0                 # pos_side eşiği (crowd_z ≥ +1 LONG, ≤ −1 SHORT)
FLOW_OI_Z = 1.0               # flow_side: oi_z ≥ 1 ...
FLOW_CVD_Z = 1.0              # ... ve cvd_share_z ≥ +1 (LONG) / ≤ −1 (SHORT)
TAKER_REL_Z = 1.0             # taker_24s bağlam kovası: cvd_share_z·s ≥ 1 kalabalıkla, ≤ −1 kalabalığa karşı
OI_QUAD_DEAD = 0.5            # oi_quad ölü bölgesi (|oi_z| < 0,5 → FLAT)
ABS_VR = 1.5                  # emilim: v / ort(önceki 20 v) ≥ 1,5
ABS_RANGE_ATR = 0.6           # emilim: h − l ≤ 0,6 · atr14s
ABS_DSHARE = 0.10             # emilim: |dshare| ≥ 0,10
ABS_VOL_N = 20                # emilim hacim ortalaması (önceki bar sayısı)
ATR_S_N = 14                  # atr14s: son 14 TR'nin basit ortalaması (lab'ın EWM ATR'si yalnız kural stopları için)
EXH_SPAN = 10                 # tükenme: son 10 bar içindeki kapanış zirveleri
EXH_LOOKBACK = 20             # tükenme: kapanış zirvesi = son 20 kapanışın (kendisi dahil) en büyüğü
EXH_MEMBERS = 3               # tükenme: en az 3 üye, son 3 üyenin dshare'i kesin azalan (UP) / artan (DOWN)
TB_TOL = 1e-9                 # taker geçerliliği: 0 ≤ tb ≤ v·(1 + TB_TOL)

UNKNOWN = "UNKNOWN"
SIDE_LABELS = {1: "LONG", -1: "SHORT", 0: "NONE"}
DIV_LABELS = {1: "BULL_DIV", -1: "BEAR_DIV", 0: "NONE"}
ABS_LABELS = {1: "SELL_ABSORBED", -1: "BUY_ABSORBED", 0: "NONE"}
EXH_LABELS = {1: "UP_EXHAUST", -1: "DOWN_EXHAUST", 0: "NONE"}
QUAD_LABELS = {0: "FLAT", 1: "LONG_BUILD", 2: "SHORT_BUILD", 3: "SHORT_COVER", 4: "LONG_UNWIND"}
FUND_LABELS = {-1: "NEG", 0: "BASE", 1: "HIGH"}
STATE_LABELS = {0: "CALM", 1: "LONG_CROWDED", 2: "SHORT_CROWDED", 3: "LONGS_ENTERING", 4: "SHORTS_ENTERING"}

#: bar_features çıktısının alanları ve sırası (canlı şema flow_v1 bunları h1_/h4_ önekiyle kullanır)
CROWD_KEYS = ("dshare", "dshare_z", "cvd_share", "cvd_share_z", "cvd_slope", "divergence", "absorption", "exhaustion_t",
              "oi", "doi_1", "doi_k", "oi_z", "oi_pct", "dpx_k", "px_z", "oi_quad",
              "gls", "gls_z", "tpls", "tpls_z", "tals", "tals_z", "smart_retail", "smart_retail_z", "tkls",
              "f8", "f8_hi", "f8_lo", "f8_z", "funding_bucket", "pos_ok", "crowd_z", "pos_side", "flow_ok", "flow_side",
              "crowd_state")
#: metrics sütunları (arşiv `daily/metrics` başlığı → alan; `crowd_data` ayrıştırır)
METRIC_FIELDS = ("oi", "tals", "tpls", "gls", "tkls")

FORMULAS_TR: dict[str, str] = {
    "zdm": "zdm(x, W, taban)[i] = (x_i − μ) / max(σ, taban); μ, σ (ddof 0) x[i−W .. i−1]'in SONLU değerlerinden, en az W//2 "
           "değer gerekir, x_i sonlu olmalı (i'nin kendisi pencerede YOK)",
    "atr14s": "TR_i = max(h−l, |h−c_{i−1}|, |l−c_{i−1}|) (TR_0 NaN); atr14s_i = ort(TR[i−13 .. i]) (14'ü de sonlu)",
    "taker": "tb geçersiz (sonlu değil, v < 0, tb < 0, tb > v·(1+TB_TOL)) → NaN; delta = 2·tb − v; dshare = delta / v (v = 0 → NaN)",
    "dshare_z": "zdm(dshare, W, Z_FLOOR_SHARE)",
    "cvd_share": "Σ_{son k} delta / Σ_{son k} v; penceredeki bir tb NaN ya da Σv ≤ 0 → NaN; cvd_share_z = zdm(cvd_share, W, "
                 "Z_FLOOR_SHARE)",
    "cvd_slope": "C_m = Σ_{j=i−k+1}^{i−k+m} delta_j (m = 1..k) doğrusunun EKK eğimi / ort(v, son k); k < 2 → NaN",
    "divergence": "C'_j = Σ_{m=i−D}^{j} delta_m; BEAR_DIV: c_i > max c[i−D..i−1] ve C'_i ≤ max C'[i−D..i−1]; BULL_DIV ayna "
                  "(c_i < min, C'_i ≥ min); aksi NONE; pencerede NaN → UNKNOWN",
    "absorption": "vr = v_i / ort(v[i−20..i−1]) ≥ ABS_VR ve h−l ≤ ABS_RANGE_ATR·atr14s; SELL_ABSORBED: dshare ≤ −ABS_DSHARE ve "
                  "c ≥ o; BUY_ABSORBED: dshare ≥ +ABS_DSHARE ve c ≤ o; aksi NONE; girdi NaN → UNKNOWN",
    "exhaustion_t": "S = {j ∈ [i−9, i] : c_j ≥ max c[j−19..j]}; UP_EXHAUST: i ∈ S, |S| ≥ 3 ve S'nin son 3 üyesinde dshare "
                    "kesin azalan; DOWN_EXHAUST ayna (kapanış dipleri, kesin artan); önce UP; aksi NONE; c[i−28..i] ya da "
                    "dshare[i−9..i] içinde NaN → UNKNOWN",
    "oi": "fut_v1 bar_features AYNEN: oi = align_oi (T−15dk ≤ t ≤ T−5dk), doi_k = fut_v1 doi, dpx_k = fut_v1 dpx, oi_z, "
          "px_z, oi_pct; doi_1 = ln(oi_i / oi_{i−1})",
    "oi_quad": "FLAT: |oi_z| < OI_QUAD_DEAD ya da dpx_k = 0; LONG_BUILD dpx > 0 ve oi_z ≥ 0,5; SHORT_BUILD dpx < 0 ve oi_z ≥ 0,5; "
               "SHORT_COVER dpx > 0 ve oi_z ≤ −0,5; LONG_UNWIND dpx < 0 ve oi_z ≤ −0,5; NaN → UNKNOWN",
    "ratios": "gls/tpls/tals/tkls: metrics satırı OI ile AYNI hizalama kuralıyla, değer sonlu ve > 0; *_z = zdm(ln x, W, "
              "Z_FLOOR_LOG); smart_retail = ln tpls − ln gls, smart_retail_z = zdm(smart_retail, W, Z_FLOOR_LOG); tals ve tkls "
              "yalnız bilgi",
    "funding": "f8, f8_hi, f8_lo = fut_v1 align_funding; f8_z = (f8 − ort(diğerleri)) / max(sd(diğerleri), Z_FLOOR_FUND), "
               "diğerleri = align_funding'in 20 günlük sıra penceresindeki sonlu f8'ler (≥ FUND_MIN_N); funding_bucket NEG "
               "(f8 < 0) / BASE (≤ FUND_BASE + EPS) / HIGH",
    "pos": "pos_ok = gls_z, tpls_z, f8_z üçü de sonlu; crowd_z = ort(clip±3 gls_z, clip±3 tpls_z, clip±3 f8_z); pos_side LONG "
           "crowd_z ≥ +CROWD_Z, SHORT ≤ −CROWD_Z, aksi NONE, pos_ok değilse UNKNOWN (herkes nerede)",
    "flow": "flow_ok = oi_z ve cvd_share_z sonlu; flow_side LONG: oi_z ≥ FLOW_OI_Z ve cvd_share_z ≥ FLOW_CVD_Z; SHORT: "
            "oi_z ≥ FLOW_OI_Z ve cvd_share_z ≤ −FLOW_CVD_Z; aksi NONE; flow_ok değilse UNKNOWN (şu an kim giriyor)",
    "crowd_state": "pos_side LONG → LONG_CROWDED, SHORT → SHORT_CROWDED (pozisyon önce); pos NONE ise flow LONG → "
                   "LONGS_ENTERING, SHORT → SHORTS_ENTERING, NONE → CALM; aksi UNKNOWN",
    "ctx": "kalabalik_poz / kalabalik_akis: pos_side / flow_side işlem yönüne göre kalabalıkla / kalabalığa_karşı / "
           "kalabalık_yok; taker_24s: cvd_share_z·s ≥ TAKER_REL_Z kalabalıkla, ≤ −TAKER_REL_Z kalabalığa_karşı, aksi "
           "kalabalık_yok; oi_ceyrek = oi_quad etiketi; NaN → bilinmiyor",
}

CROWD_FEATURE_CONSTANTS: dict[str, Any] = {
    "DIV_LOOKBACK": DIV_LOOKBACK, "Z_FLOOR_SHARE": Z_FLOOR_SHARE, "Z_FLOOR_LOG": Z_FLOOR_LOG, "Z_FLOOR_FUND": Z_FLOOR_FUND,
    "Z_CLIP": Z_CLIP, "CROWD_Z": CROWD_Z, "FLOW_OI_Z": FLOW_OI_Z, "FLOW_CVD_Z": FLOW_CVD_Z, "TAKER_REL_Z": TAKER_REL_Z,
    "OI_QUAD_DEAD": OI_QUAD_DEAD, "ABS_VR": ABS_VR, "ABS_RANGE_ATR": ABS_RANGE_ATR, "ABS_DSHARE": ABS_DSHARE,
    "ABS_VOL_N": ABS_VOL_N, "ATR_S_N": ATR_S_N, "EXH_SPAN": EXH_SPAN, "EXH_LOOKBACK": EXH_LOOKBACK,
    "EXH_MEMBERS": EXH_MEMBERS, "TB_TOL": TB_TOL,
    "k_tr": "k = FF.k_bars(step) (24 saat), W = FF.w_bars(step) (20 gün)",
    "keys": list(CROWD_KEYS),
    "labels": {"side": {str(k): v for k, v in SIDE_LABELS.items()}, "divergence": {str(k): v for k, v in DIV_LABELS.items()},
               "absorption": {str(k): v for k, v in ABS_LABELS.items()}, "exhaustion": {str(k): v for k, v in EXH_LABELS.items()},
               "oi_quad": {str(k): v for k, v in QUAD_LABELS.items()}, "funding": {str(k): v for k, v in FUND_LABELS.items()},
               "crowd_state": {str(k): v for k, v in STATE_LABELS.items()}, "unknown": "NaN"},
    "formulas_tr": FORMULAS_TR,
}

# bağlam kovaları (yalnız KEŞİF; `--futures crowd-ctx|crowd`)
UNKNOWN_TR = "bilinmiyor"
REL_WITH, REL_AGAINST, REL_NONE = "kalabalıkla", "kalabalığa_karşı", "kalabalık_yok"
REL_BUCKETS = (REL_WITH, REL_AGAINST, REL_NONE)
CTX_BUCKETS: dict[str, tuple[str, ...]] = {"kalabalik_poz": REL_BUCKETS, "kalabalik_akis": REL_BUCKETS,
                                           "oi_ceyrek": tuple(QUAD_LABELS.values()), "taker_24s": REL_BUCKETS}


# ---------------------------------------------------------------------------- yardımcılar
def _f(x: Any) -> np.ndarray:
    return np.asarray(x if x is not None else [], dtype=float)


def _t(x: Any) -> np.ndarray:
    return np.asarray(x if x is not None else [], dtype=np.int64)


def label(labels: dict[int, str], code: float) -> str:
    """Float kod → etiket; NaN → UNKNOWN."""
    try:
        c = float(code)
    except (TypeError, ValueError):
        return UNKNOWN
    return UNKNOWN if not math.isfinite(c) else labels.get(int(c), UNKNOWN)


def _incl_windows(L: int, fn: Callable[..., np.ndarray], *xs: np.ndarray) -> np.ndarray:
    """Her i için `fn(pencereler...)`, pencere = x[i−L+1 .. i] (i DAHİL; baştaki eksik yer NaN). Parça parça ve satır
    başına aynı indirgeme (FF._prev_windows ile aynı düzen): sonuç yalnız pencere içeriğine bağlıdır."""
    n = xs[0].size
    out = np.full(n, np.nan)
    if n == 0:
        return out
    views = [np.lib.stride_tricks.sliding_window_view(np.concatenate([np.full(L - 1, np.nan), x]), L) for x in xs]
    for a in range(0, n, FF._CHUNK):
        b = min(n, a + FF._CHUNK)
        out[a:b] = fn(*[np.ascontiguousarray(v[a:b]) for v in views])
    return out


def _zdm_fn(W: int, floor: float):
    need = W // 2

    def fn(win: np.ndarray, cur: np.ndarray) -> np.ndarray:
        fin = np.isfinite(win)
        cnt = fin.sum(axis=1)
        v = np.where(fin, win, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = v.sum(axis=1) / cnt
            dev = np.where(fin, win - mean[:, None], 0.0)
            sd = np.sqrt((dev * dev).sum(axis=1) / cnt)          # ddof=0
            z = (cur - mean) / np.maximum(sd, floor)
        ok = (cnt >= need) & np.isfinite(sd) & np.isfinite(cur)
        return np.where(ok, z, np.nan)
    return fn


def zdm(x: Any, W: int, floor: float) -> np.ndarray:
    """Önceki W değere göre ortalaması çıkarılmış, paydası tabanlı z (i hariç; FORMULAS_TR["zdm"])."""
    return FF._prev_windows(_f(x), int(W), _zdm_fn(int(W), float(floor)))


def clean_taker(v: Any, tb: Any) -> np.ndarray:
    """Geçerli taker alış hacmi; geçersiz bar NaN (bar atılmaz)."""
    v, tb = _f(v), _f(tb)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(v) & (v >= 0) & np.isfinite(tb) & (tb >= 0) & (tb <= v * (1.0 + TB_TOL))
    return np.where(ok, tb, np.nan)


def atr14s(h: Any, lo: Any, c: Any) -> np.ndarray:
    """Son 14 TR'nin basit ortalaması (önek değişmez; TR_0 = NaN)."""
    h, lo, c = _f(h), _f(lo), _f(c)
    tr = np.full(h.size, np.nan)
    if h.size > 1:
        pc = c[:-1]
        tr[1:] = np.maximum(h[1:] - lo[1:], np.maximum(np.abs(h[1:] - pc), np.abs(lo[1:] - pc)))
    return _incl_windows(ATR_S_N, lambda w: np.where(np.isfinite(w).all(axis=1), w.mean(axis=1), np.nan), tr)


def _code(cond_pairs: list[tuple[np.ndarray, float]], known: np.ndarray, default: float = 0.0) -> np.ndarray:
    """Sıralı koşullar → kod (ilk doğru kazanır); bilinmeyen → NaN."""
    out = np.full(known.size, default)
    done = np.zeros(known.size, dtype=bool)
    for cond, val in cond_pairs:
        m = cond & ~done
        out[m] = val
        done |= m
    return np.where(known, out, np.nan)


# ---------------------------------------------------------------------------- taker bloğu
def taker_block(o: Any, h: Any, lo: Any, c: Any, v: Any, tb: Any, step: int) -> dict[str, np.ndarray]:
    """dshare, dshare_z, cvd_share, cvd_share_z, cvd_slope, divergence, absorption, exhaustion_t."""
    o, h, lo, c, v = _f(o), _f(h), _f(lo), _f(c), _f(v)
    n = c.size
    k, W = FF.k_bars(step), FF.w_bars(step)
    tbc = clean_taker(v, tb)
    delta = 2.0 * tbc - v
    with np.errstate(invalid="ignore", divide="ignore"):
        dshare = np.where(v > 0, delta / v, np.nan)
    dshare_z = zdm(dshare, W, Z_FLOOR_SHARE)

    def cvd(wd: np.ndarray, wv: np.ndarray) -> np.ndarray:
        sd, sv = wd.sum(axis=1), wv.sum(axis=1)
        ok = np.isfinite(wd).all(axis=1) & np.isfinite(wv).all(axis=1) & (sv > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(ok, sd / sv, np.nan)
    cvd_share = _incl_windows(k, cvd, delta, v)
    cvd_share_z = zdm(cvd_share, W, Z_FLOOR_SHARE)

    if k >= 2:
        m = np.arange(1, k + 1, dtype=float)
        mc = m - m.mean()
        den = float((mc * mc).sum())

        def slope(wd: np.ndarray, wv: np.ndarray) -> np.ndarray:
            s = (np.cumsum(wd, axis=1) * mc).sum(axis=1) / den
            mv = wv.mean(axis=1)
            ok = np.isfinite(wd).all(axis=1) & np.isfinite(wv).all(axis=1) & (mv > 0)
            with np.errstate(invalid="ignore", divide="ignore"):
                return np.where(ok, s / mv, np.nan)
        cvd_slope = _incl_windows(k, slope, delta, v)
    else:
        cvd_slope = np.full(n, np.nan)

    def div(wc: np.ndarray, wd: np.ndarray) -> np.ndarray:
        cp = np.cumsum(wd, axis=1)
        ok = np.isfinite(wc).all(axis=1) & np.isfinite(wd).all(axis=1)
        with np.errstate(invalid="ignore"):
            bear = (wc[:, -1] > wc[:, :-1].max(axis=1)) & (cp[:, -1] <= cp[:, :-1].max(axis=1))
            bull = (wc[:, -1] < wc[:, :-1].min(axis=1)) & (cp[:, -1] >= cp[:, :-1].min(axis=1))
        return _code([(bear, -1.0), (bull, 1.0)], ok)
    divergence = _incl_windows(DIV_LOOKBACK + 1, div, c, delta)

    vavg = FF._prev_windows(v, ABS_VOL_N, lambda win, cur: np.where(np.isfinite(win).all(axis=1), win.mean(axis=1), np.nan))
    with np.errstate(invalid="ignore", divide="ignore"):
        vr = np.where(vavg > 0, v / vavg, np.nan)
    atrs = atr14s(h, lo, c)
    known = np.isfinite(vr) & np.isfinite(atrs) & np.isfinite(dshare) & np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) \
        & np.isfinite(c)
    with np.errstate(invalid="ignore"):
        gate = (vr >= ABS_VR) & ((h - lo) <= ABS_RANGE_ATR * atrs)
        absorption = _code([(gate & (dshare <= -ABS_DSHARE) & (c >= o), 1.0), (gate & (dshare >= ABS_DSHARE) & (c <= o), -1.0)],
                           known)

    exhaustion = _exhaustion(c, dshare)
    return {"dshare": dshare, "dshare_z": dshare_z, "cvd_share": cvd_share, "cvd_share_z": cvd_share_z,
            "cvd_slope": cvd_slope, "divergence": divergence, "absorption": absorption, "exhaustion_t": exhaustion}


def _exhaustion(c: np.ndarray, dshare: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        hh = _incl_windows(EXH_LOOKBACK, lambda w: np.where(np.isfinite(w).all(axis=1), (w[:, -1] >= w.max(axis=1)).astype(float), np.nan), c)
        ll = _incl_windows(EXH_LOOKBACK, lambda w: np.where(np.isfinite(w).all(axis=1), (w[:, -1] <= w.min(axis=1)).astype(float), np.nan), c)
    span_ok = _incl_windows(EXH_SPAN, lambda a, b, d: (np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
                                                       & np.isfinite(d).all(axis=1)).astype(float), hh, ll, dshare) == 1.0
    out = np.where(span_ok, 0.0, np.nan)
    for i in np.flatnonzero(span_ok & ((hh == 1.0) | (ll == 1.0))):
        res = 0.0
        for sign, mem in ((1.0, hh), (-1.0, ll)):                       # önce UP
            if mem[i] != 1.0:
                continue
            idx = [j for j in range(i - EXH_SPAN + 1, i + 1) if mem[j] == 1.0]
            if len(idx) < EXH_MEMBERS:
                continue
            last = dshare[idx[-EXH_MEMBERS:]]
            if not np.isfinite(last).all():
                res = float("nan")
                break
            if (sign > 0 and np.all(np.diff(last) < 0)) or (sign < 0 and np.all(np.diff(last) > 0)):
                res = sign
                break
        out[i] = res
    return out


# ---------------------------------------------------------------------------- metrics / pozisyon bloğu
def align_metrics(t_close: Any, m_t: Any, cols: dict[str, Any]) -> dict[str, np.ndarray]:
    """Her karar anı T için `T − OI_STALE_MS ≤ t ≤ T − OI_LAG_MS` olan SON metrics satırı (bütün sütunlar AYNI satırdan);
    sütun değeri sonlu ve > 0 değilse o sütun NaN (eskiye düşülmez). `cols["oi"]` için sonuç `FF.align_oi` ile birebir."""
    T = _t(t_close)
    t0 = _t(m_t)
    out = {k: np.full(T.size, np.nan) for k in cols}
    names = [k for k in cols if _f(cols[k]).size == t0.size]           # sütun yok / boyu tutmuyor → NaN
    srt = FF._sorted(t0, *[_f(cols[k]) for k in names])
    mt, vals = srt[0], dict(zip(names, srt[1:]))
    if mt.size == 0 or T.size == 0:
        return out
    j = np.searchsorted(mt, T - FF.OI_LAG_MS, side="right") - 1
    has = j >= 0
    jj = np.where(has, j, 0)
    ok = has & (mt[jj] >= T - FF.OI_STALE_MS)
    for k in names:
        val = vals[k][jj]
        good = ok & np.isfinite(val) & (val > 0)
        out[k][good] = val[good]
    return out


def _ln(x: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(np.isfinite(x) & (x > 0), np.log(np.where(x > 0, x, 1.0)), np.nan)


def positioning_block(t_close: Any, step: int, raw_metrics: dict | None) -> dict[str, np.ndarray]:
    """gls, tpls, tals, tkls (hizalı) + ln-z'leri + smart_retail."""
    W = FF.w_bars(step)
    rm = raw_metrics or {}
    al = align_metrics(t_close, rm.get("m_t"), {k: rm.get(k) for k in ("gls", "tpls", "tals", "tkls")})
    lg, lt = _ln(al["gls"]), _ln(al["tpls"])
    sr = lt - lg
    return {"gls": al["gls"], "gls_z": zdm(lg, W, Z_FLOOR_LOG), "tpls": al["tpls"], "tpls_z": zdm(lt, W, Z_FLOOR_LOG),
            "tals": al["tals"], "tals_z": zdm(_ln(al["tals"]), W, Z_FLOOR_LOG), "smart_retail": sr,
            "smart_retail_z": zdm(sr, W, Z_FLOOR_LOG), "tkls": al["tkls"]}


def funding_z(t_close: Any, f_t: Any, f_rate: Any) -> np.ndarray:
    """f8_z: fut_v1 `align_funding`'in AYNI görünürlük/bayatlık kuralları ve AYNI 20 günlük 'diğer uzlaşmalar' penceresi
    (≥ FUND_MIN_N sonlu değer); (f8 − ort) / max(sd, Z_FLOOR_FUND), ddof 0."""
    T = _t(t_close)
    ft, fr = FF._sorted(_t(f_t), _f(f_rate))
    out = np.full(T.size, np.nan)
    if ft.size == 0 or T.size == 0:
        return out
    iv = FF.infer_interval_h(ft)
    with np.errstate(invalid="ignore", divide="ignore"):
        s8 = fr * 8.0 / iv
    j = np.searchsorted(ft, T - FF.FUND_GUARD_MS, side="right") - 1
    lo_idx = np.searchsorted(ft, T - FF.WINDOW_DAYS * DAY, side="right")
    memo: dict[tuple[int, int], float] = {}
    for i in range(T.size):
        ji = int(j[i])
        if ji < 0:
            continue
        cur = s8[ji]
        if not math.isfinite(cur) or (T[i] - ft[ji]) > iv[ji] * HOUR + HOUR + FF.FUND_GUARD_MS:
            continue
        a = int(lo_idx[i])
        if ji - a < FF.FUND_MIN_N:
            continue
        key = (a, ji)
        if key not in memo:
            others = s8[a:ji]
            others = others[np.isfinite(others)]
            if others.size < FF.FUND_MIN_N:
                memo[key] = float("nan")
            else:
                memo[key] = float((cur - others.mean()) / max(float(others.std()), Z_FLOOR_FUND))
        out[i] = memo[key]
    return out


def funding_bucket(f8: Any) -> np.ndarray:
    f8 = _f(f8)
    with np.errstate(invalid="ignore"):
        return _code([(f8 < 0, -1.0), (f8 <= FF.FUND_BASE + FF.EPS, 0.0)], np.isfinite(f8), default=1.0)


def oi_quad(oi_z: Any, dpx: Any) -> np.ndarray:
    oz, dp = _f(oi_z), _f(dpx)
    with np.errstate(invalid="ignore"):
        flat = (np.abs(oz) < OI_QUAD_DEAD) | (dp == 0)
        up = oz >= OI_QUAD_DEAD
        return _code([(flat, 0.0), (up & (dp > 0), 1.0), (up & (dp < 0), 2.0), (dp > 0, 3.0), (dp < 0, 4.0)],
                     np.isfinite(oz) & np.isfinite(dp))


def crowd_sides(gls_z: Any, tpls_z: Any, f8_z: Any, oi_z: Any, cvd_share_z: Any) -> dict[str, np.ndarray]:
    """pos_ok, crowd_z, pos_side (herkes nerede) ve flow_ok, flow_side (şu an kim giriyor); crowd_state."""
    g, t, f = _f(gls_z), _f(tpls_z), _f(f8_z)
    oz, cz = _f(oi_z), _f(cvd_share_z)
    pos_ok = np.isfinite(g) & np.isfinite(t) & np.isfinite(f)
    clip = lambda z: np.clip(z, -Z_CLIP, Z_CLIP)  # noqa: E731
    with np.errstate(invalid="ignore"):
        crowd_z = np.where(pos_ok, (clip(g) + clip(t) + clip(f)) / 3.0, np.nan)
        pos_side = _code([(crowd_z >= CROWD_Z, 1.0), (crowd_z <= -CROWD_Z, -1.0)], pos_ok)
        flow_ok = np.isfinite(oz) & np.isfinite(cz)
        flow_side = _code([((oz >= FLOW_OI_Z) & (cz >= FLOW_CVD_Z), 1.0), ((oz >= FLOW_OI_Z) & (cz <= -FLOW_CVD_Z), -1.0)], flow_ok)
    state = np.full(pos_side.size, np.nan)
    state[pos_side == 1.0] = 1.0
    state[pos_side == -1.0] = 2.0
    calm_pos = pos_side == 0.0
    state[calm_pos & (flow_side == 1.0)] = 3.0
    state[calm_pos & (flow_side == -1.0)] = 4.0
    state[calm_pos & (flow_side == 0.0)] = 0.0
    return {"pos_ok": pos_ok, "crowd_z": crowd_z, "pos_side": pos_side, "flow_ok": flow_ok, "flow_side": flow_side,
            "crowd_state": state}


# ---------------------------------------------------------------------------- bütün alanlar
def bar_features(ts: Any, step: int, o: Any, h: Any, lo: Any, c: Any, v: Any, tb: Any, raw_metrics: dict | None = None,
                 raw_funding: dict | None = None) -> dict[str, np.ndarray]:
    """Bar başına bütün kalabalık alanları (`CROWD_KEYS`). `raw_metrics` = {"m_t", "oi", "tals", "tpls", "gls", "tkls"}
    (5 dk ham satırlar), `raw_funding` = {"f_t", "f_rate"} (uzlaşmalar); eksik anahtar / None → o kaynak NaN.
    `pos_ok` / `flow_ok` bool, kategorik alanlar float kod (NaN = UNKNOWN)."""
    step = int(step)
    ts_ = _t(ts)
    T = ts_ + step
    c_ = _f(c)
    rm, rf = raw_metrics or {}, raw_funding or {}
    tk = taker_block(o, h, lo, c_, v, tb, step)
    ff = FF.bar_features(ts_, step, c_, {"oi_t": rm.get("m_t"), "oi": rm.get("oi"), "f_t": rf.get("f_t"),
                                          "f_rate": rf.get("f_rate")})
    pos = positioning_block(T, step, rm)
    f8z = funding_z(T, rf.get("f_t"), rf.get("f_rate"))
    sides = crowd_sides(pos["gls_z"], pos["tpls_z"], f8z, ff["oi_z"], tk["cvd_share_z"])
    out = {**tk, "oi": ff["oi"], "doi_1": FF._log_ratio(ff["oi"], 1), "doi_k": ff["doi"], "oi_z": ff["oi_z"],
           "oi_pct": ff["oi_pct"], "dpx_k": ff["dpx"], "px_z": ff["px_z"], "oi_quad": oi_quad(ff["oi_z"], ff["dpx"]),
           **pos, "f8": ff["f8"], "f8_hi": ff["f8_hi"], "f8_lo": ff["f8_lo"], "f8_z": f8z,
           "funding_bucket": funding_bucket(ff["f8"]), **sides}
    return {k: out[k] for k in CROWD_KEYS}


def live_requirements(tf: str) -> dict[str, int]:
    """Canlı tarafın bar i için geriye ihtiyacı. `klines_bars`: cvd_share_z / px_z için W + k + 1 bar (ıraksama, tükenme,
    emilim daha kısa); `metrics_ms`: oi_z için (W+k)·step + OI_STALE_MS; `funding_ms`: fut_v1 ile aynı."""
    step = tf_ms(tf)
    k, W = FF.k_bars(step), FF.w_bars(step)
    bars = max(W + k + 1, DIV_LOOKBACK + 1, EXH_SPAN + EXH_LOOKBACK - 1, ABS_VOL_N + 1, ATR_S_N + 1)
    fut = FF.live_requirements(tf)
    return {"klines_bars": bars, "klines_ms": bars * step, "metrics_ms": fut["oi_ms"], "funding_ms": fut["funding_ms"]}


# ---------------------------------------------------------------------------- bağlam kovaları (KEŞİF)
def _num(feat: dict, key: str, i: int) -> float:
    try:
        return float(feat[key][i])
    except (KeyError, IndexError, TypeError, ValueError):
        return float("nan")


def rel(code: float, side_sign: float) -> str:
    """Kalabalık yönü koduna göre işlem yönünün ilişkisi (kalabalıkla / kalabalığa_karşı / kalabalık_yok / bilinmiyor)."""
    if not math.isfinite(code):
        return UNKNOWN_TR
    if code == 0:
        return REL_NONE
    return REL_WITH if code == side_sign else REL_AGAINST


def ctx_labels(feat: dict, i: int, side: str) -> dict[str, str]:
    """Bağlam kovaları (yalnız KEŞİF): kalabalik_poz, kalabalik_akis, oi_ceyrek, taker_24s. NaN → `bilinmiyor`."""
    s = 1.0 if side == "LONG" else -1.0
    z = _num(feat, "cvd_share_z", i)
    if not math.isfinite(z):
        tk = UNKNOWN_TR
    else:
        tk = REL_WITH if z * s >= TAKER_REL_Z else (REL_AGAINST if z * s <= -TAKER_REL_Z else REL_NONE)
    q = _num(feat, "oi_quad", i)
    return {"kalabalik_poz": rel(_num(feat, "pos_side", i), s), "kalabalik_akis": rel(_num(feat, "flow_side", i), s),
            "oi_ceyrek": UNKNOWN_TR if not math.isfinite(q) else QUAD_LABELS[int(q)], "taker_24s": tk}
