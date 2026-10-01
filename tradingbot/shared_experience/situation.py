# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — DURUM ANLIK GÖRÜNTÜSÜ `situation_v1` (2026-09-29).

Katmanın cevapladığı soru: "Bu DURUMDA (trend / oynaklık / BTC / hacim / yapı), bu kurulumla daha önce kazandık mı?".
Buradaki "durum", `(sembol, as_of, KAPANMIŞ 4h / 1h / BTC-4h barları)`nın SAF fonksiyonudur
(docs/ortak_deneyim/SPEC_V1.md §4; KARARLAR.md SPEC_V1'e üstündür).

Sözleşme (2026-09-29):
* SAF modül: yalnız stdlib + numpy + bağımlılıksız `tradingbot.timeframes`. G/Ç YOK; defter / risk / kitap / öğrenici
  içe aktarımı YOK (testte AST ile denetlenir). Hiçbir karar yoluna yazmaz.
* Kapanmış bar kuralı: satır yalnız `ts + tf <= as_of` ise kullanılır (eşitlik dahil, 1 ms önce DEĞİL) —
  `candle_confirmation.closed_bars` ve `strategy_paper.frame_freshness` ile aynı koşul.
* Alınma kuralı (YENİ): `fetch_lb_ms` verilirse ayrıca `ts + tf <= fetch_lb_ms`. Çerçeve alındığında henüz
  kapanmamış (oluşan) bir bar, `as_of` sonradan kapanışını geçse bile KULLANILMAZ; pencere eksik (incomplete) kalır
  ve toplayıcı satırı bir sonraki tura erteler (PENDING). Tur çerçevesi için `fetch_lb_ms = tur başlangıcı`;
  CSV önbelleği yapısı gereği yalnız kapanmış bar tutar → `None`.
* Pencere W <= 240 (Formasyon `DataService` dilim başına 240 bar tutar; bu sayı Formasyon kararını değiştirir, büyütülemez).
  Tüm kaynaklar (tur çerçevesi 700/500, CSV 720, laboratuvar, Formasyon 240) AYNI W'ye kırpılır → AYNI çıktı.
* Hesaplanamayan alan `None` olur ve `missing` listesine girer; ASLA 0 yazılmaz. Kayan noktalar 6 ondalığa yuvarlanır;
  kovalar (trend, rejim, hacim, RSI) YUVARLANMAMIŞ değerlerden hesaplanır.
* `SCHEMA_SPEC` alan listesini/sırasını, W değerlerini, eşikleri, yardımcı algoritma adlarını ve `input_sha` paketlemesini
  sabitler; `SCHEMA_SHA` testle sabitlenmiştir. Herhangi bir değişiklik `situation_v2` gerektirir.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..timeframes import tf_ms as _tf_ms

# ============================================================================ sabitler (2026-09-29)

SCHEMA_ID = "situation_v1"
#: Pencere boyları (kapanmış bar). W <= W_MAX şarttır (Formasyon `BARS_PER_TF` = 240; SPEC_V1 §4.1).
W4H = 200
W1H = 200
WBTC = 200
W_MAX = 240
BTC_SYMBOL = "BTC/USDT"
TF_H4 = "4h"
TF_H1 = "1h"
STATUSES = ("OK", "PARTIAL", "GAP", "NO_BARS", "ERROR")
UNKNOWN = "UNKNOWN"
ROUND_DECIMALS = 6

ATR_P = 14
RSI_P = 14
EMA_FAST = 20
EMA_SLOW = 50
SLOPE_BARS = 10
SWING_K = 3
SWING_LOOKBACK = 120
BBW_P = 20
BBW_HISTORY = 100
ATR_RATIO_HISTORY = 100
ATR_RATIO_MIN_PRIOR = 50
VOL_AVG_P = 20
EXHAUST_SPAN = 10
EXHAUST_HIGH_P = 20
VP_BARS = 120
VP_BINS = 48
VP_VALUE_AREA = 0.70
CORR_MAX = 50
CORR_MIN = 40
#: Kova eşikleri — `signal_lab.context` (hacim 0.8/1.5, oynaklık 0.8/1.25, RSI 30/50/70) ile AYNI (2026-09-29).
VOL_REGIME_EDGES = (0.8, 1.25)
VOL_BUCKET_EDGES = (0.8, 1.5)
RSI_EDGES = (30.0, 50.0, 70.0)
EFFORT_FLOOR = 0.1

#: Pencere rolleri: (rol, dilim, W). `input_sha` bu sırayla paketler.
ROLES: tuple[tuple[str, str, int], ...] = (("h4", TF_H4, W4H), ("h1", TF_H1, W1H), ("btc", TF_H4, WBTC))
#: Eksik (incomplete) pencere `missing` içinde bu belirteçlerle görünür (GAP / boş pencere) (2026-09-29).
WINDOW_TOKENS = {"h4": "h4_window", "h1": "h1_window", "btc": "btc_window"}

PROVENANCE_FIELDS: tuple[str, ...] = (
    "schema_id", "schema_sha", "symbol", "as_of_ms",
    "h4_last_open_ms", "h1_last_open_ms", "btc_last_open_ms",
    "n_h4", "n_h1", "n_btc", "input_sha", "status", "missing",
)
CORE_FIELDS: tuple[str, ...] = (
    "h4_ret_6_pct", "h4_ret_42_pct", "h4_ema_state", "h4_ema50_slope_atr", "h4_close_vs_ema50_atr", "h4_er20",
    "h4_structure", "h4_trend", "h4_atr_pct", "h4_atr_ratio", "h4_vol_regime", "h4_range_pos_20", "h4_bbw_pctile_100",
    "h4_dist_swing_high_atr", "h4_swing_high_found", "h4_dist_swing_low_atr", "h4_swing_low_found",
    "h4_rsi14", "h4_rsi_bucket", "h4_vol_ratio20", "h4_vol_bucket", "h4_effort_result", "h4_exhaustion",
    "h1_rsi14", "h1_ret_4_pct", "h1_vol_ratio20", "h1_close_vs_ema20_atr", "h1_va_pos", "h1_poc_dist_atr",
    "btc_h4_trend", "btc_h4_ret_6_pct", "btc_h4_vol_regime", "corr_btc_h4_50", "is_btc", "rs_btc_h4_42_pct",
)
CALENDAR_FIELDS: tuple[str, ...] = ("hour_utc", "session", "weekday")
SNAPSHOT_FIELDS: tuple[str, ...] = PROVENANCE_FIELDS + CORE_FIELDS + CALENDAR_FIELDS

#: Alan başına gereken asgari KAPANMIŞ bar (kendi penceresinde; corr/rs için hem sembol hem BTC penceresinde).
NEEDS: dict[str, int] = {
    "h4_ret_6_pct": 7, "h4_ret_42_pct": 43, "h4_ema_state": 50, "h4_ema50_slope_atr": 60, "h4_close_vs_ema50_atr": 50,
    "h4_er20": 21, "h4_structure": 10, "h4_trend": 60, "h4_atr_pct": 15, "h4_atr_ratio": 65, "h4_vol_regime": 65,
    "h4_range_pos_20": 20, "h4_bbw_pctile_100": 120, "h4_dist_swing_high_atr": 15, "h4_swing_high_found": 15,
    "h4_dist_swing_low_atr": 15, "h4_swing_low_found": 15, "h4_rsi14": 15, "h4_rsi_bucket": 15, "h4_vol_ratio20": 21,
    "h4_vol_bucket": 21, "h4_effort_result": 21, "h4_exhaustion": 40,
    "h1_rsi14": 15, "h1_ret_4_pct": 5, "h1_vol_ratio20": 21, "h1_close_vs_ema20_atr": 20, "h1_va_pos": 30, "h1_poc_dist_atr": 30,
    "btc_h4_trend": 60, "btc_h4_ret_6_pct": 7, "btc_h4_vol_regime": 65, "corr_btc_h4_50": 41, "rs_btc_h4_42_pct": 43,
}
#: Girdisi `None` olduğunda "UNKNOWN" yazan kategorik alanlar: bu değer `missing` sayılır (hesaplanamadı) (2026-09-29).
UNKNOWN_IS_MISSING: tuple[str, ...] = ("h4_trend", "h4_vol_regime", "btc_h4_trend", "btc_h4_vol_regime")
#: "Uygulanamaz" boşluğu: fiyatın üstünde/altında geçerli salınım YOKSA mesafe `None`, bayrak `False` olur; bu bir veri
#: EKSİĞİ değildir (pencere zirvesindeki kırılım girişleri PARTIAL sayılıp rapordan düşmesin diye) (2026-09-29).
NOT_APPLICABLE_IF: dict[str, str] = {"h4_dist_swing_high_atr": "h4_swing_high_found", "h4_dist_swing_low_atr": "h4_swing_low_found"}

#: situation_v1'in TAM tanımı. Buradaki herhangi bir değişiklik SCHEMA_SHA'yı değiştirir → `situation_v2` gerekir.
SCHEMA_SPEC: dict[str, Any] = {
    "schema_id": SCHEMA_ID,
    "windows": {r: {"tf": tf, "W": w} for r, tf, w in ROLES},
    "btc_symbol": BTC_SYMBOL,
    "w_max": W_MAX,
    "normalize": {
        "ts": "timestamp column (epoch ms, bar OPEN) else datetime index (-> ms)",
        "row_filter": "open,high,low,close finite and > 0; volume finite and >= 0 (-0.0 -> 0.0); invalid ts dropped",
        "dedupe": "on ts, keep last (input order), then sort ascending",
        "closed_rule": "ts + tf_ms <= as_of_ms",
        "fetch_rule": "if fetch_lb_ms: ts + tf_ms <= fetch_lb_ms",
        "truncate": "keep last W rows",
        "complete": "n > 0 and last_open_ms == (as_of_ms // tf_ms) * tf_ms - tf_ms",
    },
    "fields": {"provenance": list(PROVENANCE_FIELDS), "core": list(CORE_FIELDS), "calendar": list(CALENDAR_FIELDS)},
    "needs": dict(NEEDS),
    "unknown_is_missing": list(UNKNOWN_IS_MISSING),
    "not_applicable_if": dict(NOT_APPLICABLE_IF),
    "window_tokens": dict(WINDOW_TOKENS),
    "helpers": {
        "tr": "TR_i = max(h_i-l_i, |h_i-c_{i-1}|, |l_i-c_{i-1}|), i>=1",
        "atr": "wilder14: ATR_14 = mean(TR_1..TR_14); ATR_i = (13*ATR_{i-1}+TR_i)/14",
        "ema": "sma_seed: EMA_{p-1} = mean(c_0..c_{p-1}); EMA_i = EMA_{i-1} + 2/(p+1)*(c_i-EMA_{i-1})",
        "rsi": "wilder14: avg gain/loss seeded by mean of first 14 diffs; avg_loss == 0 -> 100",
        "swings": "confirmed_swings(k=3): unique extreme in 2k+1 window, i in [k, n-k-1], confirmed_at = i+k "
                  "(vendored learn/multitimeframe_context.py:358-378)",
        "median": "sorted; even count -> (a+b)/2",
        "std": "population (ddof 0), two-pass",
        "sums": "left-to-right python float",
        "pearson": "two-pass means, sxy/sqrt(sxx*syy), clamp [-1,1]; sxx==0 or syy==0 -> null",
        "logret": "ln(c_i/c_{i-1}) only where ts_i - ts_{i-1} == tf_ms; keyed by ts_i",
        "volume_profile": "bin(p) = clamp(floor((p-lo)/(hi-lo)*48), 0, 47); bar volume split equally over bins "
                          "bin(l)..bin(h); POC = argmax (lowest index on tie); value area grows from POC to adjacent bin "
                          "with more volume (tie -> upper) until acc >= 0.70*sum(bins); VAL = lo+a*w, VAH = lo+(b+1)*w, "
                          "POC_mid = lo+(poc+0.5)*w, w=(hi-lo)/48",
        "structure": "swings over last 120 bars; last two highs & lows: HH_HL both higher, LH_LL both lower, else MIXED; "
                     "<2 of either -> UNKNOWN",
        "swing_distance": "confirmed swings over whole window; high: level > c and max(c[conf+1:]) <= level; nearest = min "
                          "level; low mirrored (level < c, min(c[conf+1:]) >= level, nearest = max level); /ATR14",
        "exhaustion": "S = {i in [n-10, n-1]: c_i >= max(c_{i-19..i})}; UP_EXHAUST if n-1 in S, |S|>=3 and vol_ratio20 at "
                      "last three members strictly decreasing; DOWN mirrored with closing lows; checked UP first; else NONE",
        "trend": "UP if ema_state == +1 and slope > 0; DOWN if ema_state == -1 and slope < 0; RANGE otherwise; "
                 "UNKNOWN if an input is null",
        "atr_ratio": "atr_pct[-1] / median(atr_pct[-101:-1]) (defined values only, >= 50 needed); median 0 -> null",
        "bbw_pctile": "bbw_i = 4*std20_i/SMA20_i; share of the 100 prior bbw strictly below bbw[-1]",
        "vol_ratio": "v[-1] / mean(v[-21:-1]); mean 0 -> null",
        "effort_result": "vol_ratio20 / max(|c-o|/ATR14, 0.1)",
        "corr": "last <= 50 timestamp-aligned log returns, >= 40 required; is_btc -> 1.0",
    },
    "params": {"atr_p": ATR_P, "rsi_p": RSI_P, "ema_fast": EMA_FAST, "ema_slow": EMA_SLOW, "slope_bars": SLOPE_BARS,
               "swing_k": SWING_K, "swing_lookback": SWING_LOOKBACK, "bbw_p": BBW_P, "bbw_history": BBW_HISTORY,
               "atr_ratio_history": ATR_RATIO_HISTORY, "atr_ratio_min_prior": ATR_RATIO_MIN_PRIOR, "vol_avg_p": VOL_AVG_P,
               "exhaust_span": EXHAUST_SPAN, "exhaust_high_p": EXHAUST_HIGH_P, "vp_bars": VP_BARS, "vp_bins": VP_BINS,
               "vp_value_area": VP_VALUE_AREA, "corr_max": CORR_MAX, "corr_min": CORR_MIN, "effort_floor": EFFORT_FLOOR},
    "edges": {
        "vol_regime": {"LOW": "< 0.8", "HIGH": "> 1.25", "else": "NORMAL", "null": "UNKNOWN"},
        "vol_bucket": {"LOW": "< 0.8", "HIGH": "> 1.5", "else": "NORMAL", "null": "null"},
        "rsi_bucket": {"<30": "< 30", "30-50": "< 50", "50-70": "< 70", ">70": "else", "null": "null"},
        "va_pos": {"ABOVE_VA": "c > VAH", "BELOW_VA": "c < VAL", "else": "IN_VA"},
        "session": {"ASIA": "[0,8)", "EU": "[8,16)", "US": "[16,24)"},
        "weekday": "0 = Monday",
        "numbers": [VOL_REGIME_EDGES[0], VOL_REGIME_EDGES[1], VOL_BUCKET_EDGES[0], VOL_BUCKET_EDGES[1]] + list(RSI_EDGES),
    },
    "bucket": {"dims": ["trend", "vol", "btc", "volume", "structure", "align"],
               "sources": {"trend": "h4_trend", "vol": "h4_vol_regime", "btc": "btc_h4_trend", "volume": "h4_vol_bucket",
                           "structure": "h4_structure"},
               "null": UNKNOWN,
               "align": "WITH (UP&LONG | DOWN&SHORT), AGAINST (UP&SHORT | DOWN&LONG), NEUTRAL (RANGE), else UNKNOWN"},
    "rounding": {"decimals": ROUND_DECIMALS, "negative_zero": "0.0", "non_finite": "null", "buckets_from": "unrounded"},
    "status": {"NO_BARS": "h4.n == 0 or btc.n == 0", "GAP": "a non-empty window is incomplete",
               "PARTIAL": "else if any n < W or missing non-empty (empty 1h window -> PARTIAL)",
               "OK": "else", "ERROR": "exception (error_snapshot)",
               "incomplete_window_fields": "computed from an EMPTY window (null), never from stale bars"},
    "input_sha": {"hash": "sha256", "order": ["h4", "h1", "btc"], "tag": "ascii '<role>|<tf>|<n>|'",
                  "row": "struct '>q5d' (ts, o, h, l, c, v)", "hex_chars": 16},
}
SCHEMA_SHA: str = hashlib.sha256(json.dumps(SCHEMA_SPEC, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                                 .encode("ascii")).hexdigest()[:16]


# ============================================================================ pencere (2026-09-29)

@dataclass(frozen=True, eq=False)
class Window:
    """Bir dilimin normalleştirilmiş KAPANMIŞ bar penceresi (salt okunur numpy dizileri) (2026-09-29).

    `complete`: son bar, `as_of` anında kapanmış olması gereken SON bar mı. `source` yalnız bilgi amaçlıdır
    (tour_frames | csv_cache | …); anlık görüntünün belirlenimcilik sözleşmesine GİRMEZ."""

    tf: str
    ts: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741 — spesifikasyondaki ad
    c: np.ndarray
    v: np.ndarray
    complete: bool
    last_open_ms: int | None
    n: int
    source: str


def expected_last_closed_open(as_of_ms: int, tf: str) -> int:
    """`as_of` anında kapanmış olması gereken SON barın açılış ms'si (açılış + dilim <= as_of, eşitlik dahil).
    `strategy_paper.expected_last_closed_open` ile birebir aynı formül; kitap modülü içe aktarılmasın diye burada
    (testte eşitliği denetlenir) (2026-09-29)."""
    step = _tf_ms(tf)
    return (int(as_of_ms) // step) * step - step


def _ro(a: np.ndarray) -> np.ndarray:
    a = np.array(a, copy=True)
    a.setflags(write=False)
    return a


def empty_window(tf: str, source: str = "?") -> Window:
    """Hiç barı olmayan pencere (kaynak yok / hepsi dışlandı) (2026-09-29)."""
    _tf_ms(tf)
    z = np.zeros(0, dtype=np.float64)
    return Window(tf=str(tf), ts=_ro(np.zeros(0, dtype=np.int64)), o=_ro(z), h=_ro(z), l=_ro(z), c=_ro(z), v=_ro(z),
                  complete=False, last_open_ms=None, n=0, source=str(source))


def _safe_float(x: Any) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return math.nan
    return f


def _floats(col: Any, n: int) -> np.ndarray:
    """Sütun → float64 dizi; dönüştürülemeyen hücre NaN olur (satır sonra düşer) (2026-09-29)."""
    try:
        a = np.asarray(col, dtype=np.float64)
        if a.shape == (n,):
            return a
    except (TypeError, ValueError):
        pass
    return np.array([_safe_float(x) for x in list(col)], dtype=np.float64)


def _ts_ms(col: Any, n: int) -> tuple[np.ndarray, np.ndarray]:
    """`timestamp` sütunu → (int64 ms, geçerli maskesi). Tamsayı olmayan / sonlu olmayan değer geçersizdir."""
    try:
        a = np.asarray(col)
    except (TypeError, ValueError):
        a = np.array([_safe_float(x) for x in list(col)], dtype=np.float64)
    if a.shape == (n,) and a.dtype.kind in "iu":
        return a.astype(np.int64), np.ones(n, dtype=bool)
    if a.shape == (n,) and a.dtype.kind == "M":
        ns = a.astype("datetime64[ns]").astype(np.int64)
        ok = ns != np.iinfo(np.int64).min
        return np.where(ok, ns // 1_000_000, 0).astype(np.int64), ok
    f = _floats(a if a.shape == (n,) else col, n)
    ok = np.isfinite(f)
    ok &= np.where(ok, f == np.floor(np.where(ok, f, 0.0)), False)
    return np.where(ok, f, 0.0).astype(np.int64), ok


def _index_ms(idx: Any, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Tarih-saat indeksi → (int64 ms, geçerli maskesi). pandas 3 indeksi ms/us birimli olabilir: önce ns'ye çevrilir."""
    kind = getattr(getattr(idx, "dtype", None), "kind", "")
    if kind != "M":
        raise ValueError("normalize_rows: 'timestamp' sütunu da tarih-saat indeksi de yok")
    if hasattr(idx, "as_unit"):
        idx = idx.as_unit("ns")
    ns = np.asarray(idx.asi8, dtype=np.int64)
    ok = ns != np.iinfo(np.int64).min
    return np.where(ok, ns // 1_000_000, 0).astype(np.int64), ok


def _coerce(src: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Girdi → (ts, ts_ok, o, h, l, c, v). DataFrame (timestamp sütunu ya da dt indeksi) veya satır listesi
    (sözlük: timestamp/open/high/low/close/volume; ya da [ts, o, h, l, c, v] dizisi) (2026-09-29)."""
    if src is None:
        z = np.zeros(0, dtype=np.float64)
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=bool), z, z, z, z, z
    cols = getattr(src, "columns", None)
    if cols is not None:
        n = len(src)
        names = set(str(k) for k in cols)
        if "timestamp" in names:
            ts, ok = _ts_ms(src["timestamp"], n)
        else:
            ts, ok = _index_ms(getattr(src, "index", None), n)
        vals = [(_floats(src[k], n) if k in names else np.full(n, np.nan)) for k in ("open", "high", "low", "close", "volume")]
        return (ts, ok, *vals)
    rows = list(src)
    n = len(rows)
    raw_t: list[Any] = []
    raw: list[list[float]] = [[], [], [], [], []]
    for r in rows:
        if isinstance(r, Mapping):
            t = r.get("timestamp")
            vs = [r.get(k) for k in ("open", "high", "low", "close", "volume")]
        else:
            seq = list(r) if r is not None else []
            seq = seq + [None] * (6 - len(seq))
            t, vs = seq[0], seq[1:6]
        raw_t.append(_safe_float(t) if not isinstance(t, (int, np.integer)) or isinstance(t, bool) else int(t))
        for k in range(5):
            raw[k].append(_safe_float(vs[k]))
    if n and all(isinstance(t, int) for t in raw_t):
        ts, ok = np.array(raw_t, dtype=np.int64), np.ones(n, dtype=bool)
    else:
        ts, ok = _ts_ms(np.array([float(t) for t in raw_t], dtype=np.float64), n)
    vals = [np.array(raw[k], dtype=np.float64) for k in range(5)]
    return (ts, ok, *vals)


def normalize_rows(src: Any, tf: str, as_of_ms: int, *, W: int, fetch_lb_ms: int | None = None, source: str = "?") -> Window:
    """Herhangi bir bar kaynağını `as_of` anındaki KAPANMIŞ pencereye çevirir (SPEC_V1 §4.1) (2026-09-29).

    1. `(ts_ms, o, h, l, c, v)`: `timestamp` sütunu, yoksa tarih-saat indeksi. 2. Sonlu olmayan / pozitif olmayan OHLC ya da
    sonlu olmayan / negatif hacimli satır düşer; `ts` üzerinde tekilleştirme (sondaki kalır), artan sıralama.
    3. Kapanmış kuralı `ts + tf <= as_of`. 4. Alınma kuralı `ts + tf <= fetch_lb_ms` (verildiyse). 5. Son `W` satır.
    6. `complete = son açılış == expected_last_closed_open(as_of, tf)`."""
    step = _tf_ms(tf)
    w = int(W)
    if w < 1 or w > W_MAX:
        raise ValueError("normalize_rows: W=%d aralık dışı (1..%d; Formasyon DataService 240 bar tutar)" % (w, W_MAX))
    as_of = int(as_of_ms)
    ts, ok, o, h, l, c, v = _coerce(src)
    if len(ts):
        with np.errstate(invalid="ignore"):
            ok = ok & np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & np.isfinite(v)
            ok &= (o > 0) & (h > 0) & (l > 0) & (c > 0) & (v >= 0)
        ts, o, h, l, c, v = ts[ok], o[ok], h[ok], l[ok], c[ok], v[ok]
    if len(ts):
        rev = ts[::-1]
        _, first = np.unique(rev, return_index=True)       # (2026-09-29) tersinde ilk görülen = girdide SONUNCU; sonuç ts'ye göre artan
        pick = len(ts) - 1 - first
        ts, o, h, l, c, v = ts[pick], o[pick], h[pick], l[pick], c[pick], v[pick]
        lim = as_of if fetch_lb_ms is None else min(as_of, int(fetch_lb_ms))
        keep = (ts + step) <= lim
        ts, o, h, l, c, v = ts[keep][-w:], o[keep][-w:], h[keep][-w:], l[keep][-w:], c[keep][-w:], v[keep][-w:]
    if not len(ts):
        return empty_window(tf, source)
    v = v + 0.0                                              # (2026-09-29) -0.0 → +0.0: input_sha paketlemesi kaynaktan bağımsız
    last = int(ts[-1])
    return Window(tf=str(tf), ts=_ro(ts.astype(np.int64)), o=_ro(o), h=_ro(h), l=_ro(l), c=_ro(c), v=_ro(v),
                  complete=bool(last == expected_last_closed_open(as_of, tf)), last_open_ms=last, n=int(len(ts)),
                  source=str(source))


# ============================================================================ yardımcılar (saf python, soldan sağa) (2026-09-29)

def _mean(xs: list[float]) -> float:
    s = 0.0
    for x in xs:
        s += x
    return s / len(xs)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    m = len(s)
    k = m // 2
    return s[k] if m % 2 else (s[k - 1] + s[k]) / 2.0


def _pstd(xs: list[float], mu: float) -> float:
    s = 0.0
    for x in xs:
        d = x - mu
        s += d * d
    return math.sqrt(s / len(xs))


def _tr(h: list[float], l: list[float], c: list[float]) -> list[float | None]:  # noqa: E741
    out: list[float | None] = [None] * len(c)
    for i in range(1, len(c)):
        out[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return out


def _atr_wilder(h: list[float], l: list[float], c: list[float], p: int = ATR_P) -> list[float | None]:  # noqa: E741
    """Wilder ATR, SMA tohumlu: ATR_p = ortalama(TR_1..TR_p); ATR_i = ((p-1)·ATR_{i-1} + TR_i)/p. n >= p+1."""
    n = len(c)
    out: list[float | None] = [None] * n
    if n < p + 1:
        return out
    tr = _tr(h, l, c)
    s = 0.0
    for i in range(1, p + 1):
        s += tr[i]  # type: ignore[operator]
    a = s / p
    out[p] = a
    for i in range(p + 1, n):
        a = (a * (p - 1) + tr[i]) / p  # type: ignore[operator]
        out[i] = a
    return out


def _ema_sma_seed(c: list[float], p: int) -> list[float | None]:
    """EMA_p, tohum p-1 indeksinde ilk p kapanışın ortalaması. n >= p."""
    n = len(c)
    out: list[float | None] = [None] * n
    if n < p:
        return out
    e = _mean(c[:p])
    out[p - 1] = e
    k = 2.0 / (p + 1)
    for i in range(p, n):
        e = e + k * (c[i] - e)
        out[i] = e
    return out


def _rsi_from(ag: float, al: float) -> float:
    return 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)


def _rsi_wilder(c: list[float], p: int = RSI_P) -> list[float | None]:
    """Wilder RSI; ortalama kazanç/kayıp ilk p farkın ortalamasıyla tohumlanır; ortalama kayıp 0 → 100. n >= p+1."""
    n = len(c)
    out: list[float | None] = [None] * n
    if n < p + 1:
        return out
    g = lo = 0.0
    for i in range(1, p + 1):
        d = c[i] - c[i - 1]
        g += d if d > 0 else 0.0
        lo += -d if d < 0 else 0.0
    ag, al = g / p, lo / p
    out[p] = _rsi_from(ag, al)
    for i in range(p + 1, n):
        d = c[i] - c[i - 1]
        ag = (ag * (p - 1) + (d if d > 0 else 0.0)) / p
        al = (al * (p - 1) + (-d if d < 0 else 0.0)) / p
        out[i] = _rsi_from(ag, al)
    return out


def _er(c: list[float], p: int = 20) -> float | None:
    """Verim oranı: |c[-1] − c[-1-p]| / Σ|Δc| (son p bar); toplam 0 → None."""
    n = len(c)
    if n < p + 1:
        return None
    den = 0.0
    for i in range(n - p, n):
        den += abs(c[i] - c[i - 1])
    if den == 0:
        return None
    return abs(c[-1] - c[-1 - p]) / den


def _confirmed_swings(h: list[float], l: list[float], k: int = SWING_K) -> tuple[list[tuple[int, int, float]], list[tuple[int, int, float]]]:  # noqa: E741
    """`learn/multitimeframe_context.confirmed_swings` (358-378) SATICI KOPYASI (2026-09-29): i indeksindeki salınım, 2k+1
    pencerenin TEK uç değeriyse ve sağında k KAPANMIŞ bar varsa teyitlidir; teyit indeksi i+k. Döner: [(i, i+k, seviye)]."""
    highs: list[tuple[int, int, float]] = []
    lows: list[tuple[int, int, float]] = []
    n = len(h)
    for i in range(k, n - k):
        wh = h[i - k:i + k + 1]
        if h[i] == max(wh) and wh.count(h[i]) == 1:
            highs.append((i, i + k, h[i]))
        wl = l[i - k:i + k + 1]
        if l[i] == min(wl) and wl.count(l[i]) == 1:
            lows.append((i, i + k, l[i]))
    return highs, lows


def _structure(h: list[float], l: list[float]) -> str:  # noqa: E741
    highs, lows = _confirmed_swings(h[-SWING_LOOKBACK:], l[-SWING_LOOKBACK:], SWING_K)
    if len(highs) < 2 or len(lows) < 2:
        return UNKNOWN
    h1, h2 = highs[-2][2], highs[-1][2]
    l1, l2 = lows[-2][2], lows[-1][2]
    if h2 > h1 and l2 > l1:
        return "HH_HL"
    if h2 < h1 and l2 < l1:
        return "LH_LL"
    return "MIXED"


def _swing_distances(h: list[float], l: list[float], c: list[float], atr: float | None) -> tuple[float | None, bool, float | None, bool]:  # noqa: E741
    """En yakın GEÇERLİ salınım yükseği (seviye > c, teyitten sonra seviyenin üstünde kapanış yok) ve düşüğü (ayna);
    ATR14 cinsinden mesafe. Döner: (yüksek mesafe, bulundu, düşük mesafe, bulundu)."""
    n = len(c)
    highs, lows = _confirmed_swings(h, l, SWING_K)
    sufmax = [-math.inf] * (n + 1)
    sufmin = [math.inf] * (n + 1)
    for j in range(n - 1, -1, -1):
        sufmax[j] = c[j] if c[j] > sufmax[j + 1] else sufmax[j + 1]
        sufmin[j] = c[j] if c[j] < sufmin[j + 1] else sufmin[j + 1]
    last = c[-1]
    ch = [lv for (_i, conf, lv) in highs if lv > last and sufmax[conf + 1] <= lv]
    cl = [lv for (_i, conf, lv) in lows if lv < last and sufmin[conf + 1] >= lv]
    ok_atr = atr is not None and atr > 0
    dh = (min(ch) - last) / atr if (ch and ok_atr) else None  # type: ignore[operator]
    dl = (last - max(cl)) / atr if (cl and ok_atr) else None  # type: ignore[operator]
    return dh, bool(ch), dl, bool(cl)


def _vol_ratio_at(v: list[float], i: int, p: int = VOL_AVG_P) -> float | None:
    """v[i] / ortalama(v[i-p..i-1]) (ÖNCEKİ p bar, `signal_lab.indicators` vol_avg gibi); ortalama 0 → None."""
    if i < p:
        return None
    m = _mean(v[i - p:i])
    if m == 0:
        return None
    return v[i] / m


def _vol_regime(x: float | None) -> str:
    if x is None:
        return UNKNOWN
    return "LOW" if x < VOL_REGIME_EDGES[0] else ("HIGH" if x > VOL_REGIME_EDGES[1] else "NORMAL")


def _vol_bucket(x: float | None) -> str | None:
    if x is None:
        return None
    return "LOW" if x < VOL_BUCKET_EDGES[0] else ("HIGH" if x > VOL_BUCKET_EDGES[1] else "NORMAL")


def _rsi_bucket(x: float | None) -> str | None:
    if x is None:
        return None
    a, b, cc = RSI_EDGES
    return "<30" if x < a else ("30-50" if x < b else ("50-70" if x < cc else ">70"))


def _trend(ema_state: int | None, slope: float | None) -> str:
    if ema_state is None or slope is None:
        return UNKNOWN
    if ema_state == 1 and slope > 0:
        return "UP"
    if ema_state == -1 and slope < 0:
        return "DOWN"
    return "RANGE"


def _exhaustion(c: list[float], v: list[float]) -> str:
    n = len(c)
    up = [i for i in range(n - EXHAUST_SPAN, n) if c[i] >= max(c[i - EXHAUST_HIGH_P + 1:i + 1])]
    dn = [i for i in range(n - EXHAUST_SPAN, n) if c[i] <= min(c[i - EXHAUST_HIGH_P + 1:i + 1])]
    for s, label in ((up, "UP_EXHAUST"), (dn, "DOWN_EXHAUST")):
        if s and s[-1] == n - 1 and len(s) >= 3:
            r = [_vol_ratio_at(v, i) for i in s[-3:]]
            if all(x is not None for x in r) and r[0] > r[1] > r[2]:  # type: ignore[operator]
                return label
    return "NONE"


def _volume_profile(h: list[float], l: list[float], v: list[float], last: float) -> tuple[str | None, float | None]:  # noqa: E741
    """Son 120 1h bar, 48 eşit kutu (SCHEMA_SPEC helpers.volume_profile). Döner: (va_pos, POC orta fiyatı)."""
    h, l, v = h[-VP_BARS:], l[-VP_BARS:], v[-VP_BARS:]
    lo, hi = min(l), max(h)
    rng = hi - lo
    if not rng > 0:
        return None, None
    bins = [0.0] * VP_BINS

    def _bin(p: float) -> int:
        k = int(math.floor((p - lo) / rng * VP_BINS))
        return 0 if k < 0 else (VP_BINS - 1 if k > VP_BINS - 1 else k)

    for hb, lb, vb in zip(h, l, v):
        i0, i1 = _bin(lb), _bin(hb)
        share = vb / (i1 - i0 + 1)
        for j in range(i0, i1 + 1):
            bins[j] += share
    total = 0.0
    for x in bins:
        total += x
    if not total > 0:
        return None, None
    poc = 0
    for j in range(1, VP_BINS):
        if bins[j] > bins[poc]:
            poc = j
    a = b = poc
    acc = bins[poc]
    target = VP_VALUE_AREA * total
    while acc < target:
        below = bins[a - 1] if a > 0 else None
        above = bins[b + 1] if b < VP_BINS - 1 else None
        if below is None and above is None:
            break
        if above is not None and (below is None or above >= below):
            b += 1
            acc += above
        else:
            a -= 1
            acc += below  # type: ignore[operator]
    w = rng / VP_BINS
    val, vah = lo + a * w, lo + (b + 1) * w
    pos = "ABOVE_VA" if last > vah else ("BELOW_VA" if last < val else "IN_VA")
    return pos, lo + (poc + 0.5) * w


def _logrets(w: Window) -> dict[int, float]:
    step = _tf_ms(w.tf)
    ts, c = w.ts.tolist(), w.c.tolist()
    out: dict[int, float] = {}
    for i in range(1, len(c)):
        if ts[i] - ts[i - 1] == step:
            out[int(ts[i])] = math.log(c[i] / c[i - 1])
    return out


def _pearson_logret(a: Window, b: Window) -> float | None:
    """Zaman damgası hizalı son <= 50 log getirinin Pearson korelasyonu; >= 40 gerekir; sıfır varyans → None."""
    if a.n < NEEDS["corr_btc_h4_50"] or b.n < NEEDS["corr_btc_h4_50"]:
        return None
    ra, rb = _logrets(a), _logrets(b)
    common = sorted(set(ra) & set(rb))[-CORR_MAX:]
    if len(common) < CORR_MIN:
        return None
    x = [ra[t] for t in common]
    y = [rb[t] for t in common]
    mx, my = _mean(x), _mean(y)
    sxx = syy = sxy = 0.0
    for xi, yi in zip(x, y):
        dx, dy = xi - mx, yi - my
        sxx += dx * dx
        syy += dy * dy
        sxy += dx * dy
    if sxx == 0 or syy == 0:
        return None
    r = sxy / math.sqrt(sxx * syy)
    return 1.0 if r > 1.0 else (-1.0 if r < -1.0 else r)


def _num(x: float | None) -> float | None:
    """Yayım biçimi: sonlu değilse None; 6 ondalık; -0.0 → 0.0 (2026-09-29)."""
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    r = round(x, ROUND_DECIMALS)
    return 0.0 if r == 0 else r


def _ret(c: list[float], k: int) -> float:
    return (c[-1] / c[-1 - k] - 1.0) * 100.0


# ============================================================================ çekirdek (2026-09-29)

def _tf_block(w: Window, *, full: bool) -> dict[str, Any]:
    """4h penceresinden türeyen alanlar (YUVARLANMAMIŞ). `full=False`: BTC için yalnız trend/getiri/rejim."""
    n = w.n
    o, h, l, c, v = w.o.tolist(), w.h.tolist(), w.l.tolist(), w.c.tolist(), w.v.tolist()
    atr = _atr_wilder(h, l, c)
    atr_last = atr[-1] if n >= NEEDS["h4_atr_pct"] else None
    ok_atr = atr_last is not None and atr_last > 0
    e20 = _ema_sma_seed(c, EMA_FAST)
    e50 = _ema_sma_seed(c, EMA_SLOW)
    out: dict[str, Any] = {}
    out["ret6"] = _ret(c, 6) if n >= NEEDS["h4_ret_6_pct"] else None
    out["ret42"] = _ret(c, 42) if n >= NEEDS["h4_ret_42_pct"] else None
    ema_state = None
    if n >= NEEDS["h4_ema_state"]:
        last, f, s = c[-1], e20[-1], e50[-1]
        ema_state = 1 if (last > f > s) else (-1 if (last < f < s) else 0)  # type: ignore[operator]
    slope = None
    if n >= NEEDS["h4_ema50_slope_atr"] and ok_atr:
        slope = (e50[-1] - e50[-1 - SLOPE_BARS]) / (SLOPE_BARS * atr_last)  # type: ignore[operator]
    out["ema_state"], out["slope"], out["trend"] = ema_state, slope, _trend(ema_state, slope)
    atr_pct = [None if atr[i] is None else 100.0 * atr[i] / c[i] for i in range(n)]  # type: ignore[operator]
    out["atr_pct"] = atr_pct[-1] if n >= NEEDS["h4_atr_pct"] else None
    ratio = None
    if n >= NEEDS["h4_atr_ratio"]:
        prior = [x for x in atr_pct[max(0, n - 1 - ATR_RATIO_HISTORY):n - 1] if x is not None]
        if len(prior) >= ATR_RATIO_MIN_PRIOR:
            med = _median(prior)
            if med > 0:
                ratio = atr_pct[-1] / med  # type: ignore[operator]
    out["atr_ratio"], out["vol_regime"] = ratio, _vol_regime(ratio)
    if not full:
        return out
    out["close_vs_ema50"] = (c[-1] - e50[-1]) / atr_last if (n >= NEEDS["h4_close_vs_ema50_atr"] and ok_atr) else None  # type: ignore[operator]
    out["er20"] = _er(c, 20) if n >= NEEDS["h4_er20"] else None
    out["structure"] = _structure(h, l) if n >= NEEDS["h4_structure"] else None
    rp = None
    if n >= NEEDS["h4_range_pos_20"]:
        lo20, hi20 = min(l[-20:]), max(h[-20:])
        if hi20 - lo20 > 0:
            rp = (c[-1] - lo20) / (hi20 - lo20)
    out["range_pos"] = rp
    bp = None
    if n >= NEEDS["h4_bbw_pctile_100"]:
        bbw = []
        for i in range(n - 1 - BBW_HISTORY, n):
            seg = c[i - BBW_P + 1:i + 1]
            mu = _mean(seg)
            bbw.append(4.0 * _pstd(seg, mu) / mu)
        cur = bbw[-1]
        bp = sum(1 for x in bbw[:-1] if x < cur) / float(BBW_HISTORY)
    out["bbw_pctile"] = bp
    if n >= NEEDS["h4_dist_swing_high_atr"]:
        dh, fh, dl, fl = _swing_distances(h, l, c, atr_last)
    else:
        dh, fh, dl, fl = None, None, None, None
    out["dist_high"], out["found_high"], out["dist_low"], out["found_low"] = dh, fh, dl, fl
    rsi = _rsi_wilder(c)
    out["rsi"] = rsi[-1] if n >= NEEDS["h4_rsi14"] else None
    vr = _vol_ratio_at(v, n - 1) if n >= NEEDS["h4_vol_ratio20"] else None
    out["vol_ratio"] = vr
    er_ = None
    if n >= NEEDS["h4_effort_result"] and vr is not None and ok_atr:
        er_ = vr / max(abs(c[-1] - o[-1]) / atr_last, EFFORT_FLOOR)  # type: ignore[operator]
    out["effort"] = er_
    out["exhaustion"] = _exhaustion(c, v) if n >= NEEDS["h4_exhaustion"] else None
    return out


def _h1_block(w: Window) -> dict[str, Any]:
    n = w.n
    h, l, c, v = w.h.tolist(), w.l.tolist(), w.c.tolist(), w.v.tolist()
    atr = _atr_wilder(h, l, c)
    atr_last = atr[-1] if n >= ATR_P + 1 else None
    ok_atr = atr_last is not None and atr_last > 0
    out: dict[str, Any] = {}
    out["rsi"] = _rsi_wilder(c)[-1] if n >= NEEDS["h1_rsi14"] else None
    out["ret4"] = _ret(c, 4) if n >= NEEDS["h1_ret_4_pct"] else None
    out["vol_ratio"] = _vol_ratio_at(v, n - 1) if n >= NEEDS["h1_vol_ratio20"] else None
    cve = None
    if n >= NEEDS["h1_close_vs_ema20_atr"] and ok_atr:
        cve = (c[-1] - _ema_sma_seed(c, EMA_FAST)[-1]) / atr_last  # type: ignore[operator]
    out["close_vs_ema20"] = cve
    pos, poc_mid = (None, None)
    if n >= NEEDS["h1_va_pos"]:
        pos, poc_mid = _volume_profile(h, l, v, c[-1])
    out["va_pos"] = pos
    out["poc_dist"] = (c[-1] - poc_mid) / atr_last if (poc_mid is not None and ok_atr) else None  # type: ignore[operator]
    return out


def _check_role(w: Window, role: str, tf: str) -> None:
    if not isinstance(w, Window):
        raise TypeError("situation: %s penceresi Window değil: %r" % (role, type(w).__name__))
    if w.tf != tf:
        raise ValueError("situation: %s penceresinin dilimi %s olmalı (gelen %s)" % (role, tf, w.tf))


def compute_core(h4: Window, h1: Window, btc: Window, *, is_btc: bool) -> dict[str, Any]:
    """Bar türevli, tarafsız (LONG/SHORT'tan bağımsız), önbelleğe alınabilir alanlar — `CORE_FIELDS` sırasıyla,
    YAYIM biçiminde (6 ondalık). Kategorik alanlar yuvarlanmamış değerlerden. Pencereleri OLDUĞU GİBİ kullanır
    (tamlık denetimi `snapshot`'ın işidir) (2026-09-29)."""
    for w, (role, tf, _W) in zip((h4, h1, btc), ROLES):
        _check_role(w, role, tf)
    a = _tf_block(h4, full=True)
    x = _h1_block(h1)
    b = _tf_block(btc, full=False)
    corr = 1.0 if is_btc else _pearson_logret(h4, btc)
    rs = (a["ret42"] - b["ret42"]) if (a["ret42"] is not None and b["ret42"] is not None) else None
    core: dict[str, Any] = {
        "h4_ret_6_pct": _num(a["ret6"]),
        "h4_ret_42_pct": _num(a["ret42"]),
        "h4_ema_state": a["ema_state"],
        "h4_ema50_slope_atr": _num(a["slope"]),
        "h4_close_vs_ema50_atr": _num(a["close_vs_ema50"]),
        "h4_er20": _num(a["er20"]),
        "h4_structure": a["structure"],
        "h4_trend": a["trend"],
        "h4_atr_pct": _num(a["atr_pct"]),
        "h4_atr_ratio": _num(a["atr_ratio"]),
        "h4_vol_regime": a["vol_regime"],
        "h4_range_pos_20": _num(a["range_pos"]),
        "h4_bbw_pctile_100": _num(a["bbw_pctile"]),
        "h4_dist_swing_high_atr": _num(a["dist_high"]),
        "h4_swing_high_found": a["found_high"],
        "h4_dist_swing_low_atr": _num(a["dist_low"]),
        "h4_swing_low_found": a["found_low"],
        "h4_rsi14": _num(a["rsi"]),
        "h4_rsi_bucket": _rsi_bucket(a["rsi"]),
        "h4_vol_ratio20": _num(a["vol_ratio"]),
        "h4_vol_bucket": _vol_bucket(a["vol_ratio"]),
        "h4_effort_result": _num(a["effort"]),
        "h4_exhaustion": a["exhaustion"],
        "h1_rsi14": _num(x["rsi"]),
        "h1_ret_4_pct": _num(x["ret4"]),
        "h1_vol_ratio20": _num(x["vol_ratio"]),
        "h1_close_vs_ema20_atr": _num(x["close_vs_ema20"]),
        "h1_va_pos": x["va_pos"],
        "h1_poc_dist_atr": _num(x["poc_dist"]),
        "btc_h4_trend": b["trend"],
        "btc_h4_ret_6_pct": _num(b["ret6"]),
        "btc_h4_vol_regime": b["vol_regime"],
        "corr_btc_h4_50": _num(corr),
        "is_btc": bool(is_btc),
        "rs_btc_h4_42_pct": _num(rs),
    }
    return core


def calendar(as_of_ms: int) -> dict[str, Any]:
    """`as_of`'tan takvim: saat (UTC), seans (ASIA [0,8) / EU [8,16) / US [16,24)), haftanın günü (0 = Pazartesi).
    Önbelleğe ALINMAZ (her çağrıda hesaplanır) (2026-09-29)."""
    a = int(as_of_ms)
    hour = (a // 3_600_000) % 24
    weekday = (a // 86_400_000 + 3) % 7                      # (2026-09-29) 1970-01-01 Perşembe (3)
    session = "ASIA" if hour < 8 else ("EU" if hour < 16 else "US")
    return {"hour_utc": int(hour), "session": session, "weekday": int(weekday)}


_ROW_DT = np.dtype([("t", ">i8"), ("o", ">f8"), ("h", ">f8"), ("l", ">f8"), ("c", ">f8"), ("v", ">f8")])


def _pack(w: Window) -> bytes:
    a = np.empty(w.n, dtype=_ROW_DT)
    a["t"], a["o"], a["h"], a["l"], a["c"], a["v"] = w.ts, w.o, w.h, w.l, w.c, w.v
    return a.tobytes()                                       # (2026-09-29) satır başına struct.pack(">q5d", …) ile aynı bayt


def input_sha(h4: Window, h1: Window, btc: Window) -> str:
    """sha256: her pencere (4h, 1h, btc sırasıyla) için ASCII `'<rol>|<dilim>|<n>|'` etiketi + her satır
    `struct.pack(">q5d", ts, o, h, l, c, v)`; ilk 16 hex (2026-09-29)."""
    hsh = hashlib.sha256()
    for w, (role, _tf, _W) in zip((h4, h1, btc), ROLES):
        hsh.update(("%s|%s|%d|" % (role, w.tf, w.n)).encode("ascii"))
        hsh.update(_pack(w))
    return hsh.hexdigest()[:16]


def is_btc_symbol(symbol: str) -> bool:
    """BTC/USDT (perp soneki, tire/alt çizgi ya da bitişik yazım dahil) mi (2026-09-29)."""
    core = str(symbol or "").split(":")[0].upper().replace("-", "/").replace("_", "/")
    return core in (BTC_SYMBOL, BTC_SYMBOL.replace("/", ""))


def is_complete(w: Window, as_of_ms: int) -> bool:
    """Pencerenin son barı `as_of` anında kapanmış olması gereken SON bar mı (bayrağa güvenmeden yeniden hesap)."""
    return w.n > 0 and w.last_open_ms == expected_last_closed_open(as_of_ms, w.tf)


def _check_windows(h4: Window, h1: Window, btc: Window, as_of: int) -> None:
    for w, (role, tf, W) in zip((h4, h1, btc), ROLES):
        _check_role(w, role, tf)
        if w.n > W:
            raise ValueError("situation: %s penceresi %d bar > W=%d (normalize_rows(W=%d) ile kırpın)" % (role, w.n, W, W))
        if w.n and int(w.ts[-1]) + _tf_ms(tf) > as_of:
            raise ValueError("situation: %s penceresi as_of'tan SONRA kapanan bar içeriyor (nedensellik)" % role)


def _missing(core: Mapping[str, Any], incomplete_roles: list[str]) -> list[str]:
    out = [WINDOW_TOKENS[r] for r in incomplete_roles]
    for f in CORE_FIELDS:
        val = core.get(f)
        if val is None:
            flag = NOT_APPLICABLE_IF.get(f)
            if flag is not None and core.get(flag) is False:
                continue                                     # (2026-09-29) uygulanamaz (geçerli salınım yok) ≠ eksik
            out.append(f)
        elif f in UNKNOWN_IS_MISSING and val == UNKNOWN:
            out.append(f)
    return out


def _provenance(symbol: str, as_of: int, h4: Window | None, h1: Window | None, btc: Window | None, sha: str | None) -> dict[str, Any]:
    return {
        "schema_id": SCHEMA_ID, "schema_sha": SCHEMA_SHA, "symbol": str(symbol), "as_of_ms": int(as_of),
        "h4_last_open_ms": None if h4 is None else h4.last_open_ms,
        "h1_last_open_ms": None if h1 is None else h1.last_open_ms,
        "btc_last_open_ms": None if btc is None else btc.last_open_ms,
        "n_h4": None if h4 is None else h4.n, "n_h1": None if h1 is None else h1.n, "n_btc": None if btc is None else btc.n,
        "input_sha": sha,
    }


def snapshot(symbol: str, h4: Window, h1: Window, btc: Window, as_of_ms: int, *, core: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`situation_v1` anlık görüntüsü = provenans + çekirdek + takvim + durum/eksikler (SPEC_V1 §4.2-4.3) (2026-09-29).

    * Eksik (incomplete) pencerenin alanları BAYAT barlardan DEĞİL, boş pencereden hesaplanır (hepsi `None`);
      `missing` o pencerenin belirtecini (`h4_window` …) ve boş alanları listeler.
    * Durum: NO_BARS (4h ya da BTC penceresi boş) > GAP (boş olmayan pencere eksik) > PARTIAL (n < W, boş 1h ya da
      `missing` dolu) > OK. GAP satırı toplayıcı yalnız `pending_max_age_h` sonra yazar; öncesinde taslak (PENDING) kalır.
    * `core`: önbellekten gelen çekirdek; YALNIZ üç pencere de tamsa kullanılır (aksi halde yok sayılır).
    * BTC sembolü için `btc` olarak aynı 4h penceresi verilmelidir; `corr = 1.0`, `is_btc = True`."""
    as_of = int(as_of_ms)
    _check_windows(h4, h1, btc, as_of)
    btc_sym = is_btc_symbol(symbol)
    comp = {r: is_complete(w, as_of) for w, (r, _tf, _W) in zip((h4, h1, btc), ROLES)}
    sha = input_sha(h4, h1, btc)
    if all(comp.values()) and core is not None:
        cr = dict(core)
    else:
        use = [w if comp[r] else empty_window(tf, w.source) for w, (r, tf, _W) in zip((h4, h1, btc), ROLES)]
        cr = compute_core(use[0], use[1], use[2], is_btc=btc_sym)
    incomplete = [r for r, _tf, _W in ROLES if not comp[r]]
    missing = _missing(cr, incomplete)
    if h4.n == 0 or btc.n == 0:
        status = "NO_BARS"
    elif any(w.n > 0 and not comp[r] for w, (r, _tf, _W) in zip((h4, h1, btc), ROLES)):
        status = "GAP"
    elif h4.n < W4H or h1.n < W1H or btc.n < WBTC or missing:
        status = "PARTIAL"
    else:
        status = "OK"
    out = _provenance(symbol, as_of, h4, h1, btc, sha)
    out["status"] = status
    out["missing"] = missing
    for f in CORE_FIELDS:
        out[f] = cr.get(f)
    out.update(calendar(as_of))
    return out


def error_snapshot(symbol: str, as_of_ms: int, *, h4: Window | None = None, h1: Window | None = None,
                   btc: Window | None = None) -> dict[str, Any]:
    """Hesap sırasında istisna → status ERROR; çekirdek alanların hepsi `None` (sıfır YOK) ve `missing`te (2026-09-29)."""
    sha = None
    if h4 is not None and h1 is not None and btc is not None:
        try:
            sha = input_sha(h4, h1, btc)
        except Exception:  # noqa: BLE001 — (2026-09-29) hata anlık görüntüsü asla ikinci hata üretmez
            sha = None
    out = _provenance(symbol, int(as_of_ms), h4, h1, btc, sha)
    out["status"] = "ERROR"
    out["missing"] = [f for f in CORE_FIELDS if f != "is_btc"]
    for f in CORE_FIELDS:
        out[f] = None
    out["is_btc"] = is_btc_symbol(symbol)
    out.update(calendar(int(as_of_ms)))
    return out


def bucket(snap: Mapping[str, Any] | None, side: str) -> dict[str, str]:
    """Kullanıcının beş boyutu + hizalanma (SPEC_V1 §4.5). `None` → UNKNOWN. align: WITH (UP&LONG | DOWN&SHORT),
    AGAINST (tersi), NEUTRAL (RANGE), aksi UNKNOWN (2026-09-29)."""
    s = snap or {}

    def _g(k: str) -> str:
        val = s.get(k)
        return UNKNOWN if val is None else str(val)

    trend = _g("h4_trend")
    sd = str(side or "").strip().upper()
    if sd not in ("LONG", "SHORT"):
        align = UNKNOWN
    elif trend == "RANGE":
        align = "NEUTRAL"
    elif trend in ("UP", "DOWN"):
        align = "WITH" if (trend == "UP") == (sd == "LONG") else "AGAINST"
    else:
        align = UNKNOWN
    return {"trend": trend, "vol": _g("h4_vol_regime"), "btc": _g("btc_h4_trend"), "volume": _g("h4_vol_bucket"),
            "structure": _g("h4_structure"), "align": align}


__all__ = [
    "BTC_SYMBOL", "CALENDAR_FIELDS", "CORE_FIELDS", "NEEDS", "PROVENANCE_FIELDS", "ROLES", "SCHEMA_ID", "SCHEMA_SHA",
    "SCHEMA_SPEC", "SNAPSHOT_FIELDS", "STATUSES", "W1H", "W4H", "WBTC", "W_MAX", "WINDOW_TOKENS", "Window", "bucket",
    "calendar", "compute_core", "empty_window", "error_snapshot", "expected_last_closed_open", "input_sha", "is_btc_symbol",
    "is_complete", "normalize_rows", "snapshot",
]
