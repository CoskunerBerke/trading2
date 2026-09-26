# -*- coding: utf-8 -*-
"""VADELİ LABORATUVAR (fut_v1) — açık pozisyon (OI) ve fonlama oranı kuralları, kontrolleri ve plaseboları (SPEC fut_v1 §5–§7).

Salt araştırma: defterlere/stratejilere DOKUNMAZ; yalnız `signal_lab` `--futures` ile çağırır (varsayılan `off` yolu bu
modülü HİÇ içe aktarmaz). Özellikler `futures_features`'tan (saf, nedensel), veri `futures_data`'dan (arşiv önbelleği).

* ÖN KAYIT: bütün sabitler ve kural metinleri `FUT_REGISTRY`'de; `FUT_REGISTRY_SHA` testle sabitlenir ve
  `docs/FUTURES_OI_FUNDING_LAB.md`'de yazılıdır. Değişiklik = fut_v2 + belge + yeni deneme sayısı. Kural, sayımlar ya da
  sonuçlar görüldükten sonra GEVŞETİLMEZ.
* Doğrulayıcı aile tam 8 hipotez: 4h, bağlam HEPSİ, 4 kural × 2 yön. 1h/1d `(bilgi)`. Kontrol (`CTRL_`) ve plasebo
  hipotez değildir; bağlam dilimleri KEŞİF'tir.
* Kontrol = AYNI fiyat tetiği, yön, stop ve çıkış; OI/fonlama koşulu BİLİNEN ve YANLIŞ olan barlar (tam tümleyen).
  Koşulun katkısı kural − kontrol farkıyla ölçülür (`contributions`, `katkı`). Plasebo koşulla EŞLEŞTİRİLMEZ (koşul
  hipotezin kendisi).
* Fonlama taşıma maliyeti (`funding_carry`) yalnız BİLGİ sütunudur; hüküm R'sine girmez.

PAPER/geçmiş testtir; kâr garantisi değildir.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import futures_data as FD
from . import futures_features as FF
from . import signal_lab as L
from .timeframes import tf_ms

LONG, SHORT = L.LONG, L.SHORT
FUT_VERSION = "fut_v1"
PRIMARY_TF = "4h"
FUTURES_TFS = ("1h", "4h", "1d")
RULES = ("OI_BREAKOUT_20", "OI_REGIME_CONT", "OI_REGIME_REVERT", "FUNDING_SWEEP_REVERSAL")
FAMILY, CTRL_FAMILY = "futures", "control"
START_BAR = 210                        # algo_events ile aynı (EMA200 ısınması)

# #1 OI_BREAKOUT_20
BREAKOUT_VOL_MULT = 1.5                # laboratuvarın "yüksek(>1.5x)" hacim kenarı
BREAKOUT_STOP_ATR = 2.0
BREAKOUT_EXIT = {"kind": "channel", "max_bars": 300}   # TREND_DONCHIAN_20_10 ile aynı
# #9 OI_REGIME_CONT / OI_REGIME_REVERT
REGIME_PX_Z = 1.0                      # px_z kesişim eşiği (±1)
REGIME_OI_Z = 1.0                      # CONT: oi_z ≥ 1; REVERT: oi_z ≤ −1
REGIME_STOP_ATR = 2.0
# #10 FUNDING_SWEEP_REVERSAL
SWEEP_WICK = 0.5                       # fitil ≥ %50 bar aralığı
SWEEP_STOP_BUF_ATR = 0.25              # extra_events ile aynı tampon
SWEEP_FUND_RANK = 0.90                 # sayımdan ÖNCE seçildi (0.95 değil: üç süzgeç zaten birleşik)
SWEEP_OI_PCT = 0.80
# plasebolar
PLACEBO_P = 0.01                       # #1, #9: barların %1'i; u < 0.005 → LONG
PLACEBO_LONG_BELOW = 0.005
SWEEP_PLACEBO_P = 0.02                 # #10: barların %2'si; yön ayrı karmayla (< 0.5 → LONG)
SWEEP_PLACEBO_SIDE = 0.5

KATKI_KESIN, KATKI_VAR, KATKI_YOK, KATKI_NA = "KESİN", "VAR", "YOK", "ÖLÇÜLEMEDİ"
NOTE_PRICE_ONLY = "kenar fiyat kuralından; OI/fonlama filtresi gerekçesiz"
THIN_BY_DESIGN = "VERİ AZ (tasarım gereği)"
HYPOTHESIS_LINE = ("ön kayıtlı hipotez: 8 (4h HEPSİ) · bilgi: 1h/1d · kontrol ve plasebo hipotez değildir · "
                   "bağlam dilimleri KEŞİF")

CTRL = {r: "CTRL_" + r for r in RULES}
PLAC = {r: "PLACEBO_" + r for r in RULES}
FUT_NAMES = frozenset(RULES) | frozenset(CTRL.values()) | frozenset(PLAC.values())
CTX_KEYS = tuple(FF.CTX_BUCKETS)

_CFG0 = L.LabConfig()
FUT_REGISTRY: dict[str, Any] = {
    "version": FUT_VERSION,
    "primary_tf": PRIMARY_TF,
    "tfs": list(FUTURES_TFS),
    "features": FF.FEATURE_CONSTANTS,
    "warmup_days": FD.WARMUP_DAYS,
    "horizon_tr": "k = max(1, GÜN // step) (4h 6, 1h 24, 1d 1); W = max(2, WINDOW_DAYS·GÜN // step) (4h 120, 1h 480, 1d 20)",
    "source_tr": "yalnız data.binance.vision arşivi: daily/metrics sum_open_interest (baz birim; _value kullanılmaz) + "
                 "monthly/fundingRate last_funding_rate (bitmiş aylar); REST yedeği yok",
    "loop_tr": f"i ∈ [{START_BAR}, n); atr[i] sonlu ve > 0; karar i kapanışında, giriş i+1 açılışında; hi20/lo20/hi10/lo10 "
               "aux_series'ten (kaydırılmış); EMA50/EMA200/ATR/vol_avg indicators'tan (tam seri)",
    "rules": {
        "OI_BREAKOUT_20": {
            "trigger_tr": "LONG: c[i] > hi20[i] ve c[i−1] ≤ hi20[i−1]; c[i] > ema50[i] > ema200[i]; vol_avg[i] > 0 ve "
                          "v[i] > vol_mult·vol_avg[i]; OI_OK[i]. SHORT: ayna (lo20, c < ema50 < ema200)",
            "rule_tr": "tetik ve doi[i] > 0", "control_tr": "tetik ve doi[i] ≤ 0",
            "vol_mult": BREAKOUT_VOL_MULT, "stop_atr": BREAKOUT_STOP_ATR, "exit": BREAKOUT_EXIT},
        "OI_REGIME_CONT": {
            "trigger_tr": "yukarı kesişim: px_z[i] ≥ 1 ve px_z[i−1] < 1; aşağı: px_z[i] ≤ −1 ve px_z[i−1] > −1; OI_OK[i]; "
                          "aynı yönde son KABUL edilen kesişimden sonra i − son ≤ k olan kesişim yok sayılır (kural/kontrol "
                          "ayrımından ÖNCE; tümleyen tam kalır)",
            "rule_tr": "LONG: yukarı kesişim ve oi_z ≥ 1; SHORT: aşağı kesişim ve oi_z ≥ 1",
            "control_tr": "aynı tetik ve yön, oi_z < 1",
            "px_z": REGIME_PX_Z, "oi_z": REGIME_OI_Z, "stop_atr": REGIME_STOP_ATR, "exit": "hold k (24 saat)",
            "refractory": "k bar"},
        "OI_REGIME_REVERT": {
            "trigger_tr": "OI_REGIME_CONT ile aynı kesişim akışı",
            "rule_tr": "LONG: aşağı kesişim ve oi_z ≤ −1 (long tasfiyesi → sıçrama); SHORT: yukarı kesişim ve oi_z ≤ −1 "
                       "(short kapanışı → sönme)",
            "control_tr": "aynı tetik ve yön, oi_z > −1",
            "px_z": REGIME_PX_Z, "oi_z": -REGIME_OI_Z, "stop_atr": REGIME_STOP_ATR, "exit": "hold k (24 saat)",
            "refractory": "k bar"},
        "FUNDING_SWEEP_REVERSAL": {
            "trigger_tr": "SHORT süpürme: h[i] > hi20[i] ve c[i] < hi20[i]; h > l; h − max(o,c) ≥ wick·(h − l); "
                          "LONG: l[i] < lo20[i] ve c[i] > lo20[i]; min(o,c) − l ≥ wick·(h − l); OI_OK[i] ve FUND_OK[i]",
            "rule_tr": "SHORT: f8 > FUND_BASE + EPS ve f8_hi ≥ fund_rank ve oi_pct ≥ oi_pct_min; LONG: f8 < 0 ve "
                       "f8_lo ≥ fund_rank ve oi_pct ≥ oi_pct_min",
            "control_tr": "süpürme ve kalabalık koşulu yanlış",
            "wick": SWEEP_WICK, "fund_rank": SWEEP_FUND_RANK, "oi_pct_min": SWEEP_OI_PCT,
            "stop_tr": "SHORT h[i] + buf·ATR, LONG l[i] − buf·ATR", "stop_buf_atr": SWEEP_STOP_BUF_ATR,
            "entry_tr": "trigger = c[i] (kovalama kuralı); çıkış simulate(): LabConfig 2R hedef, 24 bar, risk 0,1–5 ATR"},
    },
    "placebo": {
        "rate": PLACEBO_P, "long_below": PLACEBO_LONG_BELOW, "sweep_rate": SWEEP_PLACEBO_P, "sweep_side_long_below": SWEEP_PLACEBO_SIDE,
        "rule_tr": "#1/#9: u = _h(sembol, tf, PLACEBO_<ad>, ts[j]) < rate, OI_OK[j], ATR geçerli; u < long_below → LONG; "
                   "stop c ∓ 2 ATR, kuralın çıkışı. #10: u < sweep_rate, OI_OK[j] ve FUND_OK[j]; yön _h2(…, 'side', ts[j]) "
                   "< 0.5 → LONG; stop c[j] ∓ q·ATR[j], q = j'den ÖNCEKİ aynı yönlü süpürme olayından (kural ∪ kontrol) "
                   "_h2(…, 'q', ts[j]) ile seçilir; yoksa PLACEBO_…:NO_REAL_RISK. _h2 = sha256 (crc32 doğrusal: _h(…, 'side', t) "
                   "= _h(…, t) XOR sabit, u < 0.02 iken yön hep aynı çıkardı). Plasebo OI/fonlama koşuluyla EŞLEŞTİRİLMEZ",
    },
    "funding_carry_tr": "bilgi (hüküm R'sine girmez): ts[j] < t ≤ çıkış_ms uzlaşmaları; çıkış_ms = ts[j+hold−1] "
                        "(STOP/TARGET/RULE), + step (TIME); funding_r = −s·Σ oran·px / risk, px = t'yi içeren barın "
                        "açılışı; penceredeki bir uzlaşma bilinmiyorsa NaN",
    # çıkış/maliyet: #10 (kural, kontrol, plasebo) simulate() ile, #1/#9 simulate_rule() ile LabConfig'ten okur; burada
    # sabitlenmezse LabConfig varsayılanı değişince hipotez mühür değişmeden başka bir teste dönüşür
    "exit_cfg": {"max_hold_bars": _CFG0.max_hold_bars, "default_rr": _CFG0.default_rr, "chase_atr": _CFG0.chase_atr,
                 "min_risk_atr": _CFG0.min_risk_atr, "max_risk_atr": _CFG0.max_risk_atr, "fee_pct": _CFG0.fee_pct,
                 "slippage_bps": _CFG0.slippage_bps, "rule_stop_too_far_atr": 10,
                 "tr": "simulate (#10): max_hold_bars bar, default_rr·R hedef, risk (min_risk_atr, max_risk_atr]·ATR, giriş "
                       "trigger'dan chase_atr·ATR'den uzaksa CHASE; simulate_rule (#1, #9): risk (min_risk_atr, "
                       "rule_stop_too_far_atr]·ATR; maliyet taraf başına fee_pct % + slippage_bps bps (her R'de)"},
    "stats": {"split": _CFG0.split, "min_is": _CFG0.min_is, "min_oos": _CFG0.min_oos,
              "bootstrap_iters": _CFG0.bootstrap_iters, "strict_seed": L.STRICT_SEED, "strict_min_days": L.STRICT_MIN_DAYS,
              "placebo_tr": "futures grubu YALNIZ kendi PLACEBO_<ad> grubuyla (PLACEBO_RANDOM'a düşmez)",
              "cut_tr": "dilim başına keşif/doğrulama kesimi yalnız vadeli koşunun olaylarından (--only-futures)"},
    "katki_tr": {
        KATKI_KESIN: "Δ_IS > 0, Δ_OOS > 0 ve diff_ci(kural_OOS, kontrol_OOS) alt ucu > 0",
        KATKI_VAR: "Δ_IS > 0 ve Δ_OOS > 0",
        KATKI_YOK: "yukarıdakilerin hiçbiri",
        KATKI_NA: "bir dönemde taraflardan birinin n < min_oos ya da ci95_OOS yok",
        "delta": "Δ_P = ort.R(kural, P) − ort.R(CTRL_kural, P); aynı dilim ve yön; gün kümeli bağımsız bootstrap (diff_ci)",
    },
    "book_tr": {
        "standard": "yalnız 4h HEPSİ: verdict == GÜÇLÜ ADAY ve katkı ∈ {VAR, KESİN}",
        "strict": "yalnız 4h HEPSİ: verdict_strict == GÜÇLÜ ADAY ve katkı == KESİN",
        "price_only": "GÜÇLÜ ama katkı YOK → '" + NOTE_PRICE_ONLY + "'; fiyat-yalnız ebeveyn ayrı ön kayıt ister",
        "info": "1h ve 1d yalnız bilgi; hiçbir zaman aday yapmaz",
    },
    "hypotheses": [f"{PRIMARY_TF} {r} {s} HEPSİ" for r in RULES for s in (LONG, SHORT)],
    "context_tr": "oi_rejim / oi_seviye / fonlama dilimleri yalnız KEŞİF (hipotez değil)",
}
FUT_REGISTRY_SHA = hashlib.sha256(json.dumps(FUT_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------- olaylar (§5)
def _h2(*parts: Any) -> float:
    """[0, 1) karma — sha256. İkincil çekilişler (yön, q seçimi) için: crc32 (`L._h`) doğrusal olduğundan aynı zaman
    damgalı iki `_h` değeri birbirinin XOR sabitidir; kabul çekilişi u < 0.02 iken ikinci `_h` hep aynı yarıya düşer."""
    return int.from_bytes(hashlib.sha256("|".join(str(x) for x in parts).encode()).digest()[:8], "big") / 2 ** 64


def _fin(x: float) -> bool:
    return x == x and not math.isinf(x)


def events(df: pd.DataFrame, symbol: str, tf: str, ind: dict[str, np.ndarray], aux: dict[str, np.ndarray],
           feat: dict[str, np.ndarray], skipped: dict[str, int] | None = None) -> list[L.Event]:
    """Kurallar (`futures`), kontrolleri (`control`, `CTRL_<ad>`) ve plaseboları (`placebo`, `PLACEBO_<ad>`). Karar i
    barının kapanışında (t_ms = ts[i] + step); simülasyon `process_series`'in ana döngüsünde."""
    o, h, lo, c, v = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    step = tf_ms(tf)
    k = FF.k_bars(step)
    n = len(df)
    atr, e50, e200, vavg = ind["atr"], ind["ema50"], ind["ema200"], ind["vol_avg"]
    hi20, lo20 = aux["hi20"], aux["lo20"]
    ok_oi, ok_f = np.asarray(feat["OI_OK"], dtype=bool), np.asarray(feat["FUND_OK"], dtype=bool)
    doi, oi_z, px_z, oi_pct = feat["doi"], feat["oi_z"], feat["px_z"], feat["oi_pct"]
    f8, f8_hi, f8_lo = feat["f8"], feat["f8_hi"], feat["f8_lo"]
    hold = {"kind": "hold", "bars": k}
    out: list[L.Event] = []

    def add(family: str, name: str, side: str, i: int, stop: float, spec: dict, trigger: float | None = None) -> None:
        out.append(L.Event(symbol, tf, family, name, side, i, int(ts[i]) + step, float(stop), trigger=trigger, exit=dict(spec)))

    def pair(rule: str, hit: bool, side: str, i: int, stop: float, spec: dict, trigger: float | None = None) -> None:
        """Aynı tetik: koşul doğru → kural, bilinen ve yanlış → kontrol (tam tümleyen)."""
        if hit:
            add(FAMILY, rule, side, i, stop, spec, trigger)
        else:
            add(CTRL_FAMILY, CTRL[rule], side, i, stop, spec, trigger)

    last_cross = {LONG: -10 ** 9, SHORT: -10 ** 9}          # tetik akışının son kabul edilen kesişimi (yön başına)
    geo: dict[str, list[tuple[int, float]]] = {LONG: [], SHORT: []}   # süpürme ebeveynleri: (i, q = s·(c − stop)/ATR)
    for i in range(START_BAR, n):
        a = atr[i]
        if not _fin(a) or a <= 0:
            continue
        # #1 OI_BREAKOUT_20 — Donchian ilk kapanış kırılımı + trend + hacim; kural doi > 0, kontrol doi ≤ 0
        if ok_oi[i] and vavg[i] > 0 and v[i] > BREAKOUT_VOL_MULT * vavg[i]:
            if c[i] > hi20[i] and c[i - 1] <= hi20[i - 1] and c[i] > e50[i] > e200[i]:
                pair("OI_BREAKOUT_20", doi[i] > 0, LONG, i, c[i] - BREAKOUT_STOP_ATR * a, BREAKOUT_EXIT)
            elif c[i] < lo20[i] and c[i - 1] >= lo20[i - 1] and c[i] < e50[i] < e200[i]:
                pair("OI_BREAKOUT_20", doi[i] > 0, SHORT, i, c[i] + BREAKOUT_STOP_ATR * a, BREAKOUT_EXIT)
        # #9 fiyat × OI rejimi — kesişim akışı (bekleme süresi kural/kontrol ayrımından ÖNCE)
        if ok_oi[i]:
            up = px_z[i] >= REGIME_PX_Z and px_z[i - 1] < REGIME_PX_Z
            down = px_z[i] <= -REGIME_PX_Z and px_z[i - 1] > -REGIME_PX_Z
            for cross, flag in ((LONG, up), (SHORT, down)):
                if not flag or i - last_cross[cross] <= k:
                    continue
                last_cross[cross] = i
                other = SHORT if cross == LONG else LONG
                stop_c = c[i] - REGIME_STOP_ATR * a if cross == LONG else c[i] + REGIME_STOP_ATR * a
                stop_r = c[i] - REGIME_STOP_ATR * a if other == LONG else c[i] + REGIME_STOP_ATR * a
                pair("OI_REGIME_CONT", oi_z[i] >= REGIME_OI_Z, cross, i, stop_c, hold)       # yeni pozisyon → devam
                pair("OI_REGIME_REVERT", oi_z[i] <= -REGIME_OI_Z, other, i, stop_r, hold)    # tasfiye → dönüş
        # #10 FUNDING_SWEEP_REVERSAL — kanal dışı fitil, kapanış içeride; kalabalık fonlama + yüksek OI seviyesi
        rng = h[i] - lo[i]
        if ok_oi[i] and ok_f[i] and rng > 0:
            if h[i] > hi20[i] and c[i] < hi20[i] and h[i] - max(o[i], c[i]) >= SWEEP_WICK * rng:
                crowd = f8[i] > FF.FUND_BASE + FF.EPS and f8_hi[i] >= SWEEP_FUND_RANK and oi_pct[i] >= SWEEP_OI_PCT
                stop = h[i] + SWEEP_STOP_BUF_ATR * a
                pair("FUNDING_SWEEP_REVERSAL", crowd, SHORT, i, stop, {}, trigger=float(c[i]))
                geo[SHORT].append((i, -(c[i] - stop) / a))
            if lo[i] < lo20[i] and c[i] > lo20[i] and min(o[i], c[i]) - lo[i] >= SWEEP_WICK * rng:
                crowd = f8[i] < 0 and f8_lo[i] >= SWEEP_FUND_RANK and oi_pct[i] >= SWEEP_OI_PCT
                stop = lo[i] - SWEEP_STOP_BUF_ATR * a
                pair("FUNDING_SWEEP_REVERSAL", crowd, LONG, i, stop, {}, trigger=float(c[i]))
                geo[LONG].append((i, (c[i] - stop) / a))
    # PLASEBOLAR: seçim yalnız barın zaman damgası + veri maskesi (gerçek sinyale ve seri uzunluğuna bağlı DEĞİL)
    for rule, mult, spec in (("OI_BREAKOUT_20", BREAKOUT_STOP_ATR, BREAKOUT_EXIT), ("OI_REGIME_CONT", REGIME_STOP_ATR, hold),
                             ("OI_REGIME_REVERT", REGIME_STOP_ATR, hold)):
        pname = PLAC[rule]
        for j in range(START_BAR, n):
            u = L._h(symbol, tf, pname, int(ts[j]))
            a = atr[j]
            if u >= PLACEBO_P or not ok_oi[j] or not _fin(a) or a <= 0:
                continue
            side = LONG if u < PLACEBO_LONG_BELOW else SHORT
            add("placebo", pname, side, j, c[j] - mult * a if side == LONG else c[j] + mult * a, spec)
    pname = PLAC["FUNDING_SWEEP_REVERSAL"]
    ptr = {LONG: 0, SHORT: 0}                                # geo[side][:ptr] = j'den ÖNCEKİ ebeveynler (önek)
    for j in range(START_BAR, n):
        t_j = int(ts[j])
        a = atr[j]
        if L._h(symbol, tf, pname, t_j) >= SWEEP_PLACEBO_P or not ok_oi[j] or not ok_f[j] or not _fin(a) or a <= 0:
            continue
        side = LONG if _h2(symbol, tf, pname, "side", t_j) < SWEEP_PLACEBO_SIDE else SHORT
        g = geo[side]
        while ptr[side] < len(g) and g[ptr[side]][0] < j:
            ptr[side] += 1
        if ptr[side] == 0:
            if skipped is not None:
                key = pname + ":NO_REAL_RISK"
                skipped[key] = skipped.get(key, 0) + 1
            continue
        q = g[int(_h2(symbol, tf, pname, "q", t_j) * ptr[side])][1]
        s = 1.0 if side == LONG else -1.0
        add("placebo", pname, side, j, c[j] - s * q * a, {}, trigger=float(c[j]))
    return out


def feature_meta(ts: np.ndarray, step: int, feat: dict[str, np.ndarray] | None, raw: dict | None) -> dict[str, Any]:
    """`meta["futures"]`: bar sayıları ve kapsama (ilk OI satırından sonraki barların OI'si bulunan payı)."""
    if feat is None or raw is None:
        return {"error": "NO_CACHE"}
    T = np.asarray(ts, dtype=np.int64) + int(step)
    ok = np.asarray(feat["OI_OK"], dtype=bool)
    oi_t = np.asarray(raw.get("oi_t", []), dtype=np.int64)
    since = T >= (int(oi_t.min()) + FF.OI_LAG_MS) if oi_t.size else np.zeros(T.size, dtype=bool)
    n_since, n_oi = int(since.sum()), int((since & np.isfinite(feat["oi"])).sum())
    idx = np.flatnonzero(ok)
    return {"bars": int(T.size), "bars_oi_ok": int(ok.sum()), "bars_fund_ok": int(np.asarray(feat["FUND_OK"], dtype=bool).sum()),
            "first_ok_ms": int(T[idx[0]]) if idx.size else None, "last_ok_ms": int(T[idx[-1]]) if idx.size else None,
            "bars_since_oi": n_since, "bars_oi": n_oi,
            "coverage_pct": round(100.0 * n_oi / n_since, 1) if n_since else None}


def probe_series(df: pd.DataFrame, symbol: str, tf: str, fut_raw: dict | None) -> tuple[list[dict], dict]:
    """`--futures probe` işçisi: özellikler + olaylar, SİMÜLASYON YOK (kör sayım; R hesaplanmaz)."""
    step = tf_ms(tf)
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    skipped: dict[str, int] = {}
    feat, evs = None, []
    if fut_raw is not None:
        feat = FF.bar_features(ts, step, df["close"].to_numpy(dtype=float), fut_raw)
        evs = events(df, symbol, tf, L.indicators(df), L.aux_series(df), feat, skipped)
    meta = {"symbol": symbol, "tf": tf, "bars": len(df), "signals": len(evs), "trades": 0, "skipped": skipped,
            "first": int(ts[0]) if len(ts) else None, "last": int(ts[-1]) if len(ts) else None,
            "futures": feature_meta(ts, step, feat, fut_raw)}
    return [asdict(e) for e in evs], meta


# ---------------------------------------------------------------------------- fonlama taşıma (bilgi)
def funding_carry(done: list[L.Event], arr: dict[str, np.ndarray], ts: np.ndarray, step: int, raw: dict | None) -> None:
    """Vadeli ailelerin (kural, kontrol, plaseboları) işlemlerine `funding_r` yazar: ts[j] < t ≤ çıkış_ms uzlaşmaları,
    `−s·Σ oran·px / risk`. Penceredeki uzlaşma dizisi kesintisizse (önceki ve sonraki uzlaşma var, bütün aralıklar
    1/2/4/8 saat) değer; değilse NaN (atılmaz, doldurulmaz). Diğer olaylar None kalır."""
    raw = raw or {}
    ft = np.asarray(raw.get("f_t", []), dtype=np.int64)
    fr = np.asarray(raw.get("f_rate", []), dtype=float)
    if ft.size > 1 and np.any(np.diff(ft) < 0):
        o_ = np.argsort(ft, kind="stable")
        ft, fr = ft[o_], fr[o_]
    iv = FF.infer_interval_h(ft)
    ts = np.asarray(ts, dtype=np.int64)
    o, c = arr["open"], arr["close"]
    n = len(o)
    for ev in done:
        if ev.name not in FUT_NAMES:
            continue
        j = ev.i + 1
        e = j + int(ev.hold) - 1
        s = 1.0 if ev.side == LONG else -1.0
        risk = s * (float(o[j]) - float(ev.stop)) if j < n else float("nan")
        if e >= n or not (risk > 0):
            ev.funding_r = float("nan")
            continue
        exit_ms = int(ts[e]) + (int(step) if ev.exit_reason == "TIME" else 0)
        a = int(np.searchsorted(ft, ts[j], side="right"))       # ilk t > ts[j]
        b = int(np.searchsorted(ft, exit_ms, side="right"))     # [a, b): ts[j] < t ≤ exit_ms
        if a == 0 or b >= ft.size or not np.all(np.isfinite(iv[a:b + 1])) or not np.all(np.isfinite(fr[a:b])):
            ev.funding_r = float("nan")
            continue
        bar = np.searchsorted(ts, ft[a:b], side="right") - 1
        px = np.where(bar < n, o[np.minimum(bar, n - 1)], c[n - 1])
        ev.funding_r = float(-s * float(np.sum(fr[a:b] * px)) / risk)


# ---------------------------------------------------------------------------- kör sayım (P8)
def _g(e: Any, k: str) -> Any:
    return e.get(k) if isinstance(e, dict) else getattr(e, k, None)


def blind_counts(events: list, tf: str, cfg: L.LabConfig | None = None) -> dict[str, Any]:
    """Ad, yön ve YAKLAŞIK dönem başına olay sayısı (R YOK). Kesim bu dilimin vadeli olaylarından (split). 4h kuralında
    öngörülen IS < min_is ya da OOS < min_oos → `VERİ AZ (tasarım gereği)`; yerine kural/eşik/dilim DEĞİŞMEZ."""
    cfg = cfg or _CFG0
    evs = [e for e in events if _g(e, "tf") == tf and _g(e, "name") in FUT_NAMES]
    rows: list[dict[str, Any]] = []
    if not evs:
        return {"tf": tf, "cutoff_ms": None, "rows": rows}
    t = np.asarray([int(_g(e, "t_ms")) for e in evs], dtype=np.int64)
    cut = int(t.min()) + cfg.split * (int(t.max()) - int(t.min()))
    cnt: dict[tuple[str, str], list[int]] = {}
    for e, tm in zip(evs, t):
        c = cnt.setdefault((_g(e, "name"), _g(e, "side")), [0, 0])
        c[0 if tm <= cut else 1] += 1
    for rule in RULES:
        for name in (rule, CTRL[rule], PLAC[rule]):
            for side in (LONG, SHORT):
                n_is, n_oos = cnt.get((name, side), [0, 0])
                row = {"tf": tf, "name": name, "side": side, "IS": n_is, "OOS": n_oos}
                if name == rule and tf == PRIMARY_TF and (n_is < cfg.min_is or n_oos < cfg.min_oos):
                    row["note"] = THIN_BY_DESIGN
                rows.append(row)
    return {"tf": tf, "cutoff_ms": int(cut), "rows": rows}


def blind_report(events: list, metas: list[dict], tfs: list[str], cfg: L.LabConfig | None = None) -> dict[str, Any]:
    """Dilim başına kör sayım + atlama nedenleri (NO_REAL_RISK) + maske bar sayıları (OI_OK / FUND_OK)."""
    out = {}
    for tf in tfs:
        b = blind_counts(events, tf, cfg)
        ms = [m for m in metas if m.get("tf") == tf]
        sk: dict[str, int] = {}
        for m in ms:
            for key, v in (m.get("skipped") or {}).items():
                sk[key] = sk.get(key, 0) + int(v)
        fm = [m.get("futures") or {} for m in ms]
        b["skipped"] = sk
        b["bars"] = sum(int(f.get("bars") or 0) for f in fm)
        b["bars_oi_ok"] = sum(int(f.get("bars_oi_ok") or 0) for f in fm)
        b["bars_fund_ok"] = sum(int(f.get("bars_fund_ok") or 0) for f in fm)
        b["no_cache"] = sorted(m["symbol"] for m in ms if (m.get("futures") or {}).get("error"))
        out[tf] = b
    return out


def coverage_warnings(metas: list[dict]) -> list[str]:
    """`data_warnings` ekleri: ilk metrics gününden sonraki barların %90'ından azında OI varsa `VADELİ_KAPSAMA %x`;
    önbellek yoksa `VADELİ_VERİ_YOK`."""
    out = []
    for m in metas:
        f = m.get("futures")
        if not isinstance(f, dict):
            continue
        if f.get("error"):
            out.append(f"{m['symbol']} {m['tf']}: VADELİ_VERİ_YOK ({f['error']})")
        elif f.get("coverage_pct") is not None and f["coverage_pct"] < 90.0:
            out.append(f"{m['symbol']} {m['tf']}: VADELİ_KAPSAMA %{f['coverage_pct']:.1f}")
    return out


def cache_schema_lines(symbols, cache_dir) -> list[str]:
    """`ctx`/`rules` koşusunun başında P1–P3'ün önbellekten kısmı (ağ YOK): sembol başına metrics ve fonlama satır
    istatistikleri (`FUT_PROBE`). P1 = `ensure_futures`'ın `FUT_COV` satırları."""
    lines = []
    for s in symbols:
        p = FD._paths(cache_dir, FD._sym(s))
        if p["oi"].exists():
            lines.append(FD.log_line("FUT_PROBE", {"item": "P2", "sym": FD._sym(s), **FD._metrics_stats(pd.read_csv(p["oi"]))}))
        if p["f"].exists():
            lines.append(FD.log_line("FUT_PROBE", {"item": "P3", "sym": FD._sym(s), **FD._funding_stats(pd.read_csv(p["f"]))}))
    return lines


# ---------------------------------------------------------------------------- katkı ve defter adaylığı (§6)
def _r4(x: Any) -> float | None:
    return round(float(x), 4) if x is not None and _fin(float(x)) else None


def _katki(d_is: float | None, d_oos: float | None, ci_oos: list | None, ns: list[int], cfg: L.LabConfig) -> str:
    if min(ns) < cfg.min_oos or ci_oos is None or d_is is None or d_oos is None:
        return KATKI_NA
    if d_is > 0 and d_oos > 0:
        return KATKI_KESIN if ci_oos[0] > 0 else KATKI_VAR
    return KATKI_YOK


def contributions(events: list[dict], agg: dict, cfg: L.LabConfig, tfs: list[str] | None = None) -> list[dict]:
    """Her (dilim, kural, yön) HEPSİ satırı: IS/OOS, plaseboya göre fark + gün kümeli aralık (bütün satırlar; sıkı hükme
    GİRMEZ), kontrole göre fark + aralık + `katkı`, bilgi amaçlı `funding_r`, hüküm, sıkı hüküm ve defter adaylığı.
    Dönem `agg["cutoff_ms"]`'ten, gün `t_ms // GÜN` (aggregate ile aynı)."""
    groups = {(g["tf"], g["family"], g["name"], g["side"]): g for g in agg.get("groups") or [] if g["context"] == "HEPSİ"}
    cut = agg.get("cutoff_ms") or {}
    rows = [e for e in events if e.get("name") in FUT_NAMES and e.get("r") is not None]
    ev = pd.DataFrame(rows, columns=["tf", "family", "name", "side", "t_ms", "r", "funding_r", "symbol"])
    if len(ev):
        ev["period"] = ["IS" if t <= cut.get(tf, float("inf")) else "OOS" for t, tf in zip(ev["t_ms"], ev["tf"])]
        ev["day"] = ev["t_ms"] // L._DAY
    else:
        ev["period"], ev["day"] = [], []
    order = list(tfs) if tfs else sorted(set(ev["tf"]))
    out = []
    for tf in order:
        for rule in RULES:
            for side in (LONG, SHORT):
                sel = lambda fam, name: ev[(ev["tf"] == tf) & (ev["family"] == fam) & (ev["name"] == name) & (ev["side"] == side)]  # noqa: E731
                mine, ctrl, plac = sel(FAMILY, rule), sel(CTRL_FAMILY, CTRL[rule]), sel("placebo", PLAC[rule])
                per = lambda d, p: d[d["period"] == p]  # noqa: E731
                st = {p: L.r_stats(per(mine, p)["r"].to_numpy(dtype=float), cfg.bootstrap_iters, days=per(mine, p)["day"].to_numpy())
                      for p in ("IS", "OOS")}

                def vs(other: pd.DataFrame) -> dict[str, Any]:
                    d, ci, n_o = {}, {}, []
                    for p in ("IS", "OOS"):
                        a, b = per(mine, p), per(other, p)
                        n_o.append(int(len(b)))
                        d[p] = _r4(a["r"].mean() - b["r"].mean()) if len(a) and len(b) else None
                        ci[p] = L.diff_ci(a["r"].to_numpy(dtype=float), a["day"].to_numpy(), b["r"].to_numpy(dtype=float),
                                          b["day"].to_numpy(), cfg.bootstrap_iters) if len(a) and len(b) else None
                    return {"IS": d["IS"], "OOS": d["OOS"], "ci95_IS": ci["IS"], "ci95_OOS": ci["OOS"], "n_other": n_o}
                vp, vc = vs(plac), vs(ctrl)
                ns = [st["IS"].get("n", 0), st["OOS"].get("n", 0)] + vc["n_other"]
                katki = _katki(vc["IS"], vc["OOS"], vc["ci95_OOS"], ns, cfg)
                g = groups.get((tf, FAMILY, rule, side))
                verdict = g["verdict"] if g else L.V_THIN
                strict = g.get("verdict_strict", verdict) if g else L.V_THIN
                primary = tf == PRIMARY_TF
                book = {"standard": primary and verdict == L.V_STRONG and katki in (KATKI_VAR, KATKI_KESIN),
                        "strict": primary and strict == L.V_STRONG and katki == KATKI_KESIN}
                fr = pd.to_numeric(mine["funding_r"], errors="coerce") if len(mine) else pd.Series([], dtype=float)
                fr_p = {p: _r4(fr[mine["period"] == p].dropna().mean()) if len(mine) and fr[mine["period"] == p].notna().any() else None
                        for p in ("IS", "OOS")}
                out.append({"tf": tf, "name": rule, "side": side, "primary": primary, "IS": st["IS"], "OOS": st["OOS"],
                            "vs_placebo": {k: vp[k] for k in ("IS", "OOS", "ci95_IS", "ci95_OOS")} | {"n_placebo": vp["n_other"]},
                            "vs_control": {k: vc[k] for k in ("IS", "OOS", "ci95_IS", "ci95_OOS")} | {"n_ctrl": vc["n_other"]},
                            "katki": katki,
                            "funding_r": {**fr_p, "unknown_share": _r4(float(fr.isna().mean())) if len(mine) else None},
                            "verdict": verdict, "verdict_strict": strict, "book": book,
                            "note": NOTE_PRICE_ONLY if verdict == L.V_STRONG and katki == KATKI_YOK else "",
                            "symbols": int(g["symbols"]) if g else int(mine["symbol"].nunique())})
    return out


# ---------------------------------------------------------------------------- rapor ve günlük
def _ci(ci: list | None) -> str:
    return f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci else "[—]"


def _f2(x: Any) -> str:
    return "—" if x is None else f"{x:+.2f}"


def render_section(report: dict[str, Any], fmt: Callable[[dict], str]) -> list[str]:
    """`== VADELİ VERİ (OI/FONLAMA) ... ==` bölümü: kapsama, ön kayıtlı 8 hipotez (4h) + bilgi satırları, KEŞİF dilimleri;
    yoklama kipinde kör sayımlar."""
    fu = report.get("futures") or {}
    lines = [f"\n== VADELİ VERİ (OI/FONLAMA) — ön kayıtlı 8 hipotez · {fu.get('version', FUT_VERSION)} · sha "
             f"{fu.get('registry_sha', FUT_REGISTRY_SHA)} · kip {fu.get('mode')} ==", HYPOTHESIS_LINE]
    cov = fu.get("coverage") or {}
    if cov:
        st: dict[str, int] = {}
        for c in cov.values():
            st[c.get("status") or "?"] = st.get(c.get("status") or "?", 0) + 1
        firsts = sorted(c["first_day"] for c in cov.values() if c.get("first_day"))
        lines.append("kapsama: " + " · ".join(f"{k} {v}" for k, v in sorted(st.items()))
                     + (f" · ilk OI günü {firsts[0]} … {firsts[-1]}" if firsts else ""))
        bad = [f"{c.get('sym')}={c.get('status')}" for c in cov.values() if c.get("status") not in ("OK", None)]
        if bad:
            lines.append("  sorunlu: " + ", ".join(bad[:15]))
    if fu.get("mode") == "probe":
        for tf, b in (fu.get("blind") or {}).items():
            lines.append(f"\n-- kör sayım {tf} (R YOK; yaklaşık kesim) · bar {b.get('bars', 0)} · OI_OK {b.get('bars_oi_ok', 0)} "
                         f"· FUND_OK {b.get('bars_fund_ok', 0)} · atlanan {b.get('skipped') or {}}")
            for r in b.get("rows") or []:
                lines.append(f"  {r['name']:<34}{r['side']:<6} keşif {r['IS']:>5} | doğrulama {r['OOS']:>5}"
                             + (f" · {r['note']}" if r.get("note") else ""))
        return lines
    for r in fu.get("contributions") or []:
        tag = "" if r["primary"] else " (bilgi)"
        vp, vc, fr = r["vs_placebo"], r["vs_control"], r["funding_r"]
        unk = "—" if fr["unknown_share"] is None else "%" + format(fr["unknown_share"] * 100, ".0f")
        book = "standart+sıkı" if r["book"]["strict"] and r["book"]["standard"] else (
            "standart" if r["book"]["standard"] else ("sıkı" if r["book"]["strict"] else "yok"))
        lines.append(f"{r['tf']:>4}{tag} {r['name']:<24}{r['side']:<6} keşif {fmt(r['IS'])} | doğrulama {fmt(r['OOS'])}")
        lines.append(f"      eşine göre {_f2(vp['IS'])}/{_f2(vp['OOS'])} %95 doğr. {_ci(vp['ci95_OOS'])} · kontrole göre "
                     f"{_f2(vc['IS'])}/{_f2(vc['OOS'])} %95 doğr. {_ci(vc['ci95_OOS'])} (kontrol n {vc['n_ctrl'][0]}/{vc['n_ctrl'][1]}) "
                     f"· katkı {r['katki']} · fonlama R {_f2(fr['IS'])}/{_f2(fr['OOS'])} bilinmeyen {unk}")
        lines.append(f"      hüküm {r['verdict']} · sıkı {r['verdict_strict']} · defter adayı: {book if r['primary'] else 'yok (bilgi)'}"
                     + (f" · {r['note']}" if r.get("note") else "") + f" · {r['symbols']} coin")
    kesif = [g for g in report.get("groups") or [] if g.get("context") in CTX_KEYS
             and g.get("family") not in ("placebo", "control") and g.get("verdict") in (L.V_STRONG, L.V_WEAK)]
    if kesif:
        lines.append(f"\n-- KEŞİF dilimleri (hipotez değil; {', '.join(CTX_KEYS)}) · {L.V_STRONG} "
                     f"{sum(1 for g in kesif if g['verdict'] == L.V_STRONG)} · {L.V_WEAK} {sum(1 for g in kesif if g['verdict'] == L.V_WEAK)}")
        for g in sorted(kesif, key=lambda x: -x["OOS"]["mean_r"])[:15]:
            lines.append(f"  {g['tf']:>4} {g['name']:<26}{g['side']:<6}{g['context'] + '=' + str(g['bucket']):<28} "
                         f"doğrulama {fmt(g['OOS'])} · {g['verdict']}")
    return lines


def log_lines(report: dict[str, Any]) -> list[str]:
    """İş günlüğü satırları (her biri ≤ 4 KB): `FUT_REGISTRY`, yoklamada `FUT_COUNT`, kural koşusunda `FUT_RESULT`
    (`FUT_COV` / `FUT_PROBE` indirme ve yoklama sırasında yazılır)."""
    fu = report.get("futures") or {}
    out = [FD.log_line("FUT_REGISTRY", {"version": fu.get("version", FUT_VERSION), "sha": fu.get("registry_sha", FUT_REGISTRY_SHA),
                                        "mode": fu.get("mode")})]
    for b in (fu.get("blind") or {}).values():
        for r in b.get("rows") or []:
            out.append(FD.log_line("FUT_COUNT", r))
    short = lambda st: {"n": st.get("n", 0), "mean_r": st.get("mean_r"), "ci95": st.get("ci95")}  # noqa: E731
    for r in fu.get("contributions") or []:
        vp, vc = r["vs_placebo"], r["vs_control"]
        out.append(FD.log_line("FUT_RESULT", {
            "tf": r["tf"], "name": r["name"], "side": r["side"], "IS": short(r["IS"]), "OOS": short(r["OOS"]),
            "vs_placebo": {"IS": vp["IS"], "OOS": vp["OOS"], "ci95_OOS": vp["ci95_OOS"]},
            "vs_control": {"IS": vc["IS"], "OOS": vc["OOS"], "ci95_OOS": vc["ci95_OOS"], "n_ctrl": vc["n_ctrl"]},
            "katki": r["katki"], "funding_r": r["funding_r"], "verdict": r["verdict"], "verdict_strict": r["verdict_strict"],
            "book": r["book"], "symbols": r["symbols"]}))
    return out
