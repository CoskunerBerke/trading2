# -*- coding: utf-8 -*-
"""KALABALIK LABORATUVARI (fut_v2) — "kalabalığı takip mi, kalabalığa karşı mı?" sorusunun ön kayıtlı geçmiş testi
(2026-09-30). Salt araştırma: defterlere/stratejilere DOKUNMAZ; yalnız `signal_lab` `--futures crowd-probe|crowd|crowd-ctx`
ile çağırır (varsayılan yol ve fut_v1 kipleri bu modülü HİÇ içe aktarmaz).

* Özellikler `crowd_features`'tan (saf, nedensel; canlı flow_v1 ile AYNI kod), veri `crowd_data`'dan (arşiv önbelleği),
  fonlama fut_v1 önbelleğinden.
* ÖN KAYIT: bütün sabitler, kural metinleri, plasebo tanımı, çıkış/maliyet ayarları, istatistik/katkı/defter metinleri,
  hipotez listesi, `CROWD_FEATURE_CONSTANTS` + `FF.FEATURE_CONSTANTS`, sütun eşlemesi ve hizalama kuralı `CROWD_REGISTRY`'de;
  `CROWD_REGISTRY_SHA` testle sabitlenir ve doğrulayıcı koşudan ÖNCE `docs/CROWD_LAB_FUT_V2.md`'ye yazılır. Değişiklik =
  yeni sürüm + belge + yeni deneme sayısı. Kural, sayımlar ya da sonuçlar görüldükten sonra GEVŞETİLMEZ.
* Doğrulayıcı aile tam 8 hipotez: 4h, bağlam HEPSİ, 4 kural × 2 yön. 1h `(bilgi)`. Kontrol (`CTRL_`) ve plasebo hipotez
  değildir; bağlam dilimleri KEŞİF'tir.
* Kontrol = AYNI fiyat tetiği, yön, stop ve çıkış; kalabalık koşulu BİLİNEN ve YANLIŞ (tam tümleyen: kural ∪ kontrol =
  tetik ∩ OK). Koşulun katkısı kural − kontrol farkıyla ölçülür (`katkı`, fut_v1 ile aynı etiketler).
* Fonlama taşıma (`funding_r`) yalnız BİLGİ; hüküm R'sine girmez.

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

from . import crowd_data as CD
from . import crowd_features as CF
from . import futures_data as FD
from . import futures_features as FF
from . import futures_lab as FL
from . import signal_lab as L
from .timeframes import tf_ms

LONG, SHORT = L.LONG, L.SHORT
CROWD_VERSION = "fut_v2"
PRIMARY_TF = "4h"
CROWD_TFS = CD.CROWD_TFS
RULES = ("CROWD_BRK_FLOW_FOLLOW", "CROWD_BRK_FLOW_CONTRA", "CROWD_SWEEP_POS_CONTRA", "CROWD_SWEEP_POS_FOLLOW")
FAMILY, CTRL_FAMILY = "crowd", "control"
START_BAR = FL.START_BAR                # 210: algo_events ile aynı (EMA200 ısınması)

BRK_STOP_ATR = 2.0                      # CROWD_BRK_FLOW_FOLLOW: c ∓ 2 ATR
BRK_EXIT = {"kind": "channel", "max_bars": 300}   # TREND_DONCHIAN_20_10 / OI_BREAKOUT_20 ile aynı
CONTRA_BUF_ATR = 0.25                   # CROWD_BRK_FLOW_CONTRA: kırılım barının ucu ± 0,25 ATR
SWEEP_WICK = FL.SWEEP_WICK              # 0,5: fut_v1 #10 geometrisi
SWEEP_BUF_ATR = FL.SWEEP_STOP_BUF_ATR   # 0,25
PLACEBO_P = FL.PLACEBO_P                # BRK_FOLLOW eşi: barların %1'i; u < 0,005 → LONG (fut_v1 #1)
PLACEBO_LONG_BELOW = FL.PLACEBO_LONG_BELOW
SIM_PLACEBO_P = FL.SWEEP_PLACEBO_P      # simulate() kurallarının eşi: barların %2'si (fut_v1 #10)
SIM_PLACEBO_SIDE = FL.SWEEP_PLACEBO_SIDE

KATKI_KESIN, KATKI_VAR, KATKI_YOK, KATKI_NA = FL.KATKI_KESIN, FL.KATKI_VAR, FL.KATKI_YOK, FL.KATKI_NA
NOTE_PRICE_ONLY = "kenar fiyat tetiğinden; kalabalık koşulu gerekçesiz"
THIN_BY_DESIGN = FL.THIN_BY_DESIGN
HYPOTHESIS_LINE = ("ön kayıtlı hipotez: 8 (4h HEPSİ) · bilgi: 1h · kontrol ve plasebo hipotez değildir · bağlam dilimleri "
                   "KEŞİF · bugüne kadar 2 vadeli aile, 16 hipotez")
TRIALS = {"families": 2, "hypotheses": 16, "tr": "fut_v1 (8 hipotez, aday çıkmadı) + fut_v2 kalabalık (8 hipotez)"}

CTRL = {r: "CTRL_" + r for r in RULES}
PLAC = {r: "PLACEBO_" + r for r in RULES}
CROWD_NAMES = frozenset(RULES) | frozenset(CTRL.values()) | frozenset(PLAC.values())
CTX_KEYS = tuple(CF.CTX_BUCKETS)
#: "takip mi karşı mı" satırları: (tetik, TAKİP kuralı, KARŞI kuralı, tetik yönü d=+1 açıklaması, d=−1 açıklaması)
PAIRS = (("BRK", "CROWD_BRK_FLOW_FOLLOW", "CROWD_BRK_FLOW_CONTRA", "yukarı kırılım, kalabalık long giriyor",
          "aşağı kırılım, kalabalık short giriyor"),
         ("SWEEP", "CROWD_SWEEP_POS_FOLLOW", "CROWD_SWEEP_POS_CONTRA", "tepe süpürmesi, kalabalık long konumlu",
          "dip süpürmesi, kalabalık short konumlu"))

_CFG0 = L.LabConfig()
CROWD_REGISTRY: dict[str, Any] = {
    "version": CROWD_VERSION,
    "primary_tf": PRIMARY_TF,
    "tfs": list(CROWD_TFS),
    "features": CF.CROWD_FEATURE_CONSTANTS,
    "fut_features": FF.FEATURE_CONSTANTS,
    "warmup_days": CD.WARMUP_DAYS,
    "vol_agree_rtol": CD.VOL_AGREE_RTOL,
    "column_map": CD.COLUMN_MAP,
    "alignment_tr": "karar anı T = ts[i] + step; metrics (OI, gls, tpls, tals, tkls): T−15dk ≤ t ≤ T−5dk aralığındaki SON "
                    "satır, bütün sütunlar aynı satırdan, değer sonlu ve > 0 değilse NaN (eskiye düşülmez); fonlama: calc ≤ "
                    "T − 60 sn; mumlar yalnız kapanmış barlar; taker satırı lab mumuna açılış zamanıyla bağlanır, hacim "
                    "uyuşmazsa (göreli > vol_agree_rtol) tb NaN; z pencereleri i'yi HARİÇ tutar",
    "source_tr": "yalnız data.binance.vision arşivi: daily/metrics tam satırlar (sum_open_interest, count_long_short_ratio, "
                 "sum_toptrader_long_short_ratio, count_toptrader_long_short_ratio, sum_taker_long_short_vol_ratio) + "
                 "{monthly|daily}/klines 1h/4h taker_buy_volume + fut_v1 fonlama önbelleği (monthly/fundingRate); REST yok",
    "loop_tr": f"i ∈ [{START_BAR}, n); lab ATR[i] (EWM 14) sonlu ve > 0; karar i kapanışında, giriş i+1 açılışında; "
               "hi20/lo20/hi10/lo10 aux_series'ten (kaydırılmış); maliyet taraf başına %0,05 + 3 bps",
    "triggers": {
        "BRK": "Donchian ilk kapanış kırılımı: c[i] > hi20[i] ve c[i−1] ≤ hi20[i−1] → d = +1; c[i] < lo20[i] ve c[i−1] ≥ "
               "lo20[i−1] → d = −1 (trend/hacim süzgeci YOK: tetik kalabalığa göre nötr)",
        "SWEEP": "fut_v1 #10 geometrisi: h[i] > hi20[i], c[i] < hi20[i], h > l, h − max(o,c) ≥ wick·(h − l) → d = +1; l[i] < "
                 "lo20[i], c[i] > lo20[i], min(o,c) − l ≥ wick·(h − l) → d = −1",
    },
    "rules": {
        "CROWD_BRK_FLOW_FOLLOW": {
            "trigger": "BRK", "ok": "flow_ok", "rule_tr": "flow_side == d (kalabalık kırılım yönünde giriyor)",
            "control_tr": "flow_ok ve flow_side ≠ d", "side_tr": "d", "stop_tr": "c ∓ stop_atr·ATR (LONG c − 2 ATR)",
            "stop_atr": BRK_STOP_ATR, "exit": BRK_EXIT, "sim": "simulate_rule"},
        "CROWD_BRK_FLOW_CONTRA": {
            "trigger": "BRK", "ok": "flow_ok", "rule_tr": "flow_side == d", "control_tr": "flow_ok ve flow_side ≠ d",
            "side_tr": "−d", "stop_tr": "d = +1 (SHORT): h[i] + buf·ATR; d = −1 (LONG): l[i] − buf·ATR",
            "stop_buf_atr": CONTRA_BUF_ATR, "entry_tr": "trigger = c[i] (kovalama kuralı); simulate(): 2R hedef, 24 bar",
            "sim": "simulate"},
        "CROWD_SWEEP_POS_CONTRA": {
            "trigger": "SWEEP", "ok": "pos_ok", "rule_tr": "pos_side == d (kalabalık süpürme yönünde konumlu = tuzakta)",
            "control_tr": "pos_ok ve pos_side ≠ d", "side_tr": "−d",
            "stop_tr": "fut_v1 #10: d = +1 (SHORT) h[i] + buf·ATR; d = −1 (LONG) l[i] − buf·ATR", "stop_buf_atr": SWEEP_BUF_ATR,
            "wick": SWEEP_WICK, "entry_tr": "trigger = c[i]; simulate(): 2R hedef, 24 bar", "sim": "simulate"},
        "CROWD_SWEEP_POS_FOLLOW": {
            "trigger": "SWEEP", "ok": "pos_ok", "rule_tr": "pos_side == d", "control_tr": "pos_ok ve pos_side ≠ d",
            "side_tr": "d", "stop_tr": "karşı bar ucu: d = +1 (LONG) l[i] − buf·ATR; d = −1 (SHORT) h[i] + buf·ATR",
            "stop_buf_atr": SWEEP_BUF_ATR, "wick": SWEEP_WICK, "entry_tr": "trigger = c[i]; simulate(): 2R hedef, 24 bar",
            "sim": "simulate"},
    },
    "placebo": {
        "rate": PLACEBO_P, "long_below": PLACEBO_LONG_BELOW, "sim_rate": SIM_PLACEBO_P, "sim_side_long_below": SIM_PLACEBO_SIDE,
        "rule_tr": "CROWD_BRK_FLOW_FOLLOW (fut_v1 #1 gibi): u = _h(sembol, tf, PLACEBO_<ad>, ts[j]) < rate, flow_ok[j], ATR "
                   "geçerli; u < long_below → LONG; stop c ∓ 2 ATR, kanal çıkışı. Üç simulate() kuralı (fut_v1 #10 gibi): "
                   "u < sim_rate, kuralın OK maskesi (BRK_CONTRA flow_ok, SWEEP_* pos_ok); yön _h2(…, 'side', ts[j]) < 0,5 → "
                   "LONG; stop c[j] ∓ q·ATR[j], q = j'den ÖNCEKİ aynı yönlü gerçek olaydan (kural ∪ kontrol) _h2(…, 'q', "
                   "ts[j]) ile; yoksa PLACEBO_…:NO_REAL_RISK; trigger c[j]. Plasebo kalabalık koşuluyla EŞLEŞTİRİLMEZ",
    },
    "funding_carry_tr": "bilgi (hüküm R'sine girmez): futures_lab.funding_carry(names=kalabalık adları), fut_v1 tanımı aynen",
    "exit_cfg": {"max_hold_bars": _CFG0.max_hold_bars, "default_rr": _CFG0.default_rr, "chase_atr": _CFG0.chase_atr,
                 "min_risk_atr": _CFG0.min_risk_atr, "max_risk_atr": _CFG0.max_risk_atr, "fee_pct": _CFG0.fee_pct,
                 "slippage_bps": _CFG0.slippage_bps, "rule_stop_too_far_atr": 10,
                 "tr": "simulate (BRK_CONTRA, SWEEP_*): max_hold_bars bar, default_rr·R hedef, risk (min_risk_atr, "
                       "max_risk_atr]·ATR, giriş trigger'dan chase_atr·ATR'den uzaksa CHASE; simulate_rule (BRK_FOLLOW): risk "
                       "(min_risk_atr, rule_stop_too_far_atr]·ATR; maliyet taraf başına fee_pct % + slippage_bps bps"},
    "stats": {"split": _CFG0.split, "min_is": _CFG0.min_is, "min_oos": _CFG0.min_oos,
              "bootstrap_iters": _CFG0.bootstrap_iters, "strict_seed": L.STRICT_SEED, "strict_min_days": L.STRICT_MIN_DAYS,
              "placebo_tr": "crowd grubu YALNIZ kendi PLACEBO_<ad> grubuyla (PLACEBO_RANDOM'a düşmez); kontrolün eşi yok",
              "cut_tr": "dilim başına keşif/doğrulama kesimi yalnız kalabalık koşusunun olaylarından (--futures crowd: katalog, "
                        "ek sinyal, algoritma yok)",
              "verdict_tr": "signal_lab.aggregate / verdict / verdict_strict DEĞİŞMEDEN"},
    "katki_tr": {
        KATKI_KESIN: "Δ_IS > 0, Δ_OOS > 0 ve diff_ci(kural_OOS, kontrol_OOS) alt ucu > 0",
        KATKI_VAR: "Δ_IS > 0 ve Δ_OOS > 0",
        KATKI_YOK: "yukarıdakilerin hiçbiri",
        KATKI_NA: "bir dönemde taraflardan birinin n < min_oos ya da ci95_OOS yok",
        "delta": "Δ_P = ort.R(kural, P) − ort.R(CTRL_kural, P); aynı dilim ve yön; gün kümeli bağımsız bootstrap (diff_ci); "
                 "etiket futures_lab._katki ile",
    },
    "book_tr": {
        "standard": "yalnız 4h HEPSİ: verdict == GÜÇLÜ ADAY ve katkı ∈ {VAR, KESİN}",
        "strict": "yalnız 4h HEPSİ: verdict_strict == GÜÇLÜ ADAY ve katkı == KESİN",
        "price_only": "GÜÇLÜ ama katkı YOK → '" + NOTE_PRICE_ONLY + "'",
        "info": "1h yalnız bilgi; hiçbir zaman aday yapmaz",
        "paper_tr": "sıkı aday yalnız PAPER gözlem ÖNERİSİdir (kullanıcı onayı gerekir); canlı flow raporu aynı koşulu ön kayıtlı "
                    "tekrar olarak izler",
    },
    "pairs_tr": "tetik × yön başına TAKİP ve KARŞI satırları yan yana (BRK d: TAKİP d, KARŞI −d; SWEEP d: KARŞI −d, TAKİP d); "
                "cevap: sıkı aday varsa o taraf (sıkı), yoksa standart aday varsa o taraf (standart), yoksa kanıt yok",
    "blind_tr": "4h kuralında öngörülen IS < min_is ya da OOS < min_oos → 'VERİ AZ (tasarım gereği)'; kural/eşik/dilim DEĞİŞMEZ",
    "hypotheses": [f"{PRIMARY_TF} {r} {s} HEPSİ" for r in RULES for s in (LONG, SHORT)],
    "context_tr": "kalabalik_poz / kalabalik_akis / oi_ceyrek / taker_24s dilimleri yalnız KEŞİF (hipotez değil); "
                  "--futures crowd-ctx: mevcut algoritma/ek sinyal olaylarına eklenir, kalabalık kuralı YOK",
    "ctx_buckets": {k: list(v) for k, v in CF.CTX_BUCKETS.items()},
    "trials": TRIALS,
}
CROWD_REGISTRY_SHA = hashlib.sha256(json.dumps(CROWD_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _fin(x: float) -> bool:
    return x == x and not math.isinf(x)


def _sgn(side: str) -> float:
    return 1.0 if side == LONG else -1.0


# ---------------------------------------------------------------------------- özellikler
def features(df: pd.DataFrame, tf: str, raw: dict) -> dict[str, Any]:
    """Lab serisi + önbellek satırları → `crowd_features.bar_features` (canlı ile aynı kod). `_taker`: birleşim sayaçları."""
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    o, h, lo, c, v = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    tb, st = CD.join_taker(ts, v, raw.get("k_t", []), raw.get("k_v", []), raw.get("k_tb", []))
    feat: dict[str, Any] = CF.bar_features(ts, tf_ms(tf), o, h, lo, c, v, tb, raw, raw)
    feat["_taker"] = {**st, "valid": int(np.isfinite(CF.clean_taker(v, tb)).sum())}
    return feat


# ---------------------------------------------------------------------------- olaylar (§4.2)
def events(df: pd.DataFrame, symbol: str, tf: str, ind: dict[str, np.ndarray], aux: dict[str, np.ndarray],
           feat: dict[str, Any], skipped: dict[str, int] | None = None) -> list[L.Event]:
    """Kurallar (`crowd`), kontrolleri (`control`, `CTRL_<ad>`) ve plaseboları (`placebo`, `PLACEBO_<ad>`). Karar i
    barının kapanışında (t_ms = ts[i] + step); simülasyon `process_series`'in ana döngüsünde."""
    o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    step = tf_ms(tf)
    n = len(df)
    atr = ind["atr"]
    hi20, lo20 = aux["hi20"], aux["lo20"]
    flow_ok, pos_ok = np.asarray(feat["flow_ok"], dtype=bool), np.asarray(feat["pos_ok"], dtype=bool)
    flow_side, pos_side = np.asarray(feat["flow_side"], dtype=float), np.asarray(feat["pos_side"], dtype=float)
    out: list[L.Event] = []

    def add(family: str, name: str, side: str, i: int, stop: float, spec: dict, trigger: float | None = None) -> None:
        out.append(L.Event(symbol, tf, family, name, side, i, int(ts[i]) + step, float(stop), trigger=trigger, exit=dict(spec)))

    geo: dict[str, dict[str, list[tuple[int, float]]]] = {r: {LONG: [], SHORT: []} for r in RULES}

    def pair(rule: str, hit: bool, side: str, i: int, stop: float, spec: dict, trigger: float | None = None) -> None:
        """Aynı tetik: koşul doğru → kural, bilinen ve yanlış → kontrol (tam tümleyen)."""
        if hit:
            add(FAMILY, rule, side, i, stop, spec, trigger)
        else:
            add(CTRL_FAMILY, CTRL[rule], side, i, stop, spec, trigger)
        geo[rule][side].append((i, _sgn(side) * (c[i] - stop) / atr[i]))

    for i in range(START_BAR, n):
        a = atr[i]
        if not _fin(a) or a <= 0:
            continue
        # BRK — Donchian ilk kapanış kırılımı; kalabalık akışı (flow_side) kırılım yönünde mi
        d = 1 if (c[i] > hi20[i] and c[i - 1] <= hi20[i - 1]) else (-1 if (c[i] < lo20[i] and c[i - 1] >= lo20[i - 1]) else 0)
        if d and flow_ok[i]:
            hit = flow_side[i] == d
            fs, cs = (LONG, SHORT) if d > 0 else (SHORT, LONG)
            pair("CROWD_BRK_FLOW_FOLLOW", hit, fs, i, c[i] - d * BRK_STOP_ATR * a, BRK_EXIT)
            pair("CROWD_BRK_FLOW_CONTRA", hit, cs, i, (h[i] + CONTRA_BUF_ATR * a) if d > 0 else (lo[i] - CONTRA_BUF_ATR * a), {},
                 trigger=float(c[i]))
        # SWEEP — kanal dışı fitil, kapanış içeride; kalabalık konumu (pos_side) süpürme yönünde mi (tuzak)
        rng = h[i] - lo[i]
        if pos_ok[i] and rng > 0:
            for d, swept in ((1, h[i] > hi20[i] and c[i] < hi20[i] and h[i] - max(o[i], c[i]) >= SWEEP_WICK * rng),
                             (-1, lo[i] < lo20[i] and c[i] > lo20[i] and min(o[i], c[i]) - lo[i] >= SWEEP_WICK * rng)):
                if not swept:
                    continue
                hit = pos_side[i] == d
                up_stop, dn_stop = h[i] + SWEEP_BUF_ATR * a, lo[i] - SWEEP_BUF_ATR * a
                if d > 0:
                    pair("CROWD_SWEEP_POS_CONTRA", hit, SHORT, i, up_stop, {}, trigger=float(c[i]))
                    pair("CROWD_SWEEP_POS_FOLLOW", hit, LONG, i, dn_stop, {}, trigger=float(c[i]))
                else:
                    pair("CROWD_SWEEP_POS_CONTRA", hit, LONG, i, dn_stop, {}, trigger=float(c[i]))
                    pair("CROWD_SWEEP_POS_FOLLOW", hit, SHORT, i, up_stop, {}, trigger=float(c[i]))
    # PLASEBOLAR: seçim yalnız barın zaman damgası + veri maskesi (gerçek sinyale ve seri uzunluğuna bağlı DEĞİL)
    pname = PLAC["CROWD_BRK_FLOW_FOLLOW"]
    for j in range(START_BAR, n):
        u = L._h(symbol, tf, pname, int(ts[j]))
        a = atr[j]
        if u >= PLACEBO_P or not flow_ok[j] or not _fin(a) or a <= 0:
            continue
        side = LONG if u < PLACEBO_LONG_BELOW else SHORT
        add("placebo", pname, side, j, c[j] - _sgn(side) * BRK_STOP_ATR * a, BRK_EXIT)
    for rule, mask in (("CROWD_BRK_FLOW_CONTRA", flow_ok), ("CROWD_SWEEP_POS_CONTRA", pos_ok), ("CROWD_SWEEP_POS_FOLLOW", pos_ok)):
        pname = PLAC[rule]
        ptr = {LONG: 0, SHORT: 0}                            # geo[rule][side][:ptr] = j'den ÖNCEKİ gerçek olaylar
        for j in range(START_BAR, n):
            t_j = int(ts[j])
            a = atr[j]
            if L._h(symbol, tf, pname, t_j) >= SIM_PLACEBO_P or not mask[j] or not _fin(a) or a <= 0:
                continue
            side = LONG if FL._h2(symbol, tf, pname, "side", t_j) < SIM_PLACEBO_SIDE else SHORT
            g = geo[rule][side]
            while ptr[side] < len(g) and g[ptr[side]][0] < j:
                ptr[side] += 1
            if ptr[side] == 0:
                if skipped is not None:
                    key = pname + ":NO_REAL_RISK"
                    skipped[key] = skipped.get(key, 0) + 1
                continue
            q = g[int(FL._h2(symbol, tf, pname, "q", t_j) * ptr[side])][1]
            add("placebo", pname, side, j, c[j] - _sgn(side) * q * a, {}, trigger=float(c[j]))
    return out


def funding_carry(done: list[L.Event], arr: dict[str, np.ndarray], ts: np.ndarray, step: int, raw: dict | None) -> None:
    """Bilgi: kalabalık ailelerinin işlemlerine fut_v1 tanımıyla `funding_r` (fonlama fut_v1 önbelleğinden)."""
    FL.funding_carry(done, arr, ts, step, raw, names=CROWD_NAMES)


# ---------------------------------------------------------------------------- meta, yoklama, kör sayım (P8)
_SIDE_NAMES = (("LONG", 1.0), ("SHORT", -1.0), ("NONE", 0.0))


def _side_counts(x: np.ndarray) -> dict[str, int]:
    x = np.asarray(x, dtype=float)
    out = {k: int((x == v).sum()) for k, v in _SIDE_NAMES}
    out["UNKNOWN"] = int((~np.isfinite(x)).sum())
    return out


def feature_meta(ts: np.ndarray, step: int, feat: dict | None, raw: dict | None) -> dict[str, Any]:
    """`meta["crowd"]`: bar sayıları, pos/flow yön sayıları, metrics kapsaması (ilk metrics satırından sonraki barların OI'si
    bulunan payı) ve taker birleşim sayaçları."""
    if feat is None or raw is None:
        return {"error": "NO_CACHE"}
    T = np.asarray(ts, dtype=np.int64) + int(step)
    pos_ok, flow_ok = np.asarray(feat["pos_ok"], dtype=bool), np.asarray(feat["flow_ok"], dtype=bool)
    m_t = np.asarray(raw.get("m_t", []), dtype=np.int64)
    since = T >= (int(m_t.min()) + FF.OI_LAG_MS) if m_t.size else np.zeros(T.size, dtype=bool)
    n_since, n_m = int(since.sum()), int((since & np.isfinite(np.asarray(feat["oi"], dtype=float))).sum())
    both = np.flatnonzero(pos_ok & flow_ok)
    return {"bars": int(T.size), "bars_pos_ok": int(pos_ok.sum()), "bars_flow_ok": int(flow_ok.sum()),
            "pos_side": _side_counts(feat["pos_side"]), "flow_side": _side_counts(feat["flow_side"]),
            "first_ok_ms": int(T[both[0]]) if both.size else None, "last_ok_ms": int(T[both[-1]]) if both.size else None,
            "bars_since_metrics": n_since, "bars_metrics": n_m,
            "coverage_pct": round(100.0 * n_m / n_since, 1) if n_since else None, "taker": dict(feat.get("_taker") or {})}


def probe_series(df: pd.DataFrame, symbol: str, tf: str, raw: dict | None) -> tuple[list[dict], dict]:
    """`--futures crowd-probe` işçisi: özellikler + olaylar, SİMÜLASYON YOK (kör sayım; R hesaplanmaz)."""
    step = tf_ms(tf)
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    skipped: dict[str, int] = {}
    feat, evs = None, []
    if raw is not None:
        feat = features(df, tf, raw)
        evs = events(df, symbol, tf, L.indicators(df), L.aux_series(df), feat, skipped)
    meta = {"symbol": symbol, "tf": tf, "bars": len(df), "signals": len(evs), "trades": 0, "skipped": skipped,
            "first": int(ts[0]) if len(ts) else None, "last": int(ts[-1]) if len(ts) else None,
            "crowd": feature_meta(ts, step, feat, raw)}
    return [asdict(e) for e in evs], meta


def blind_counts(events: list, tf: str, cfg: L.LabConfig | None = None) -> dict[str, Any]:
    """Ad, yön ve YAKLAŞIK dönem başına olay sayısı (R YOK). Kesim bu dilimin kalabalık olaylarından (split). 4h kuralında
    öngörülen IS < min_is ya da OOS < min_oos → `VERİ AZ (tasarım gereği)`; yerine kural/eşik/dilim DEĞİŞMEZ."""
    cfg = cfg or _CFG0
    evs = [e for e in events if FL._g(e, "tf") == tf and FL._g(e, "name") in CROWD_NAMES]
    rows: list[dict[str, Any]] = []
    if not evs:
        return {"tf": tf, "cutoff_ms": None, "rows": rows}
    t = np.asarray([int(FL._g(e, "t_ms")) for e in evs], dtype=np.int64)
    cut = int(t.min()) + cfg.split * (int(t.max()) - int(t.min()))
    cnt: dict[tuple[str, str], list[int]] = {}
    for e, tm in zip(evs, t):
        c = cnt.setdefault((FL._g(e, "name"), FL._g(e, "side")), [0, 0])
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


def _shares(counts: dict[str, int]) -> dict[str, float | None]:
    known = sum(counts.get(k, 0) for k, _ in _SIDE_NAMES)
    return {k: (round(counts.get(k, 0) / known, 4) if known else None) for k, _ in _SIDE_NAMES}


def blind_report(events: list, metas: list[dict], tfs: list[str], cfg: L.LabConfig | None = None) -> dict[str, Any]:
    """Dilim başına kör sayım + atlama nedenleri (NO_REAL_RISK) + maske barları + pos_side / flow_side payları (P8)."""
    out = {}
    for tf in tfs:
        b = blind_counts(events, tf, cfg)
        ms = [m for m in metas if m.get("tf") == tf]
        sk: dict[str, int] = {}
        for m in ms:
            for key, v in (m.get("skipped") or {}).items():
                sk[key] = sk.get(key, 0) + int(v)
        cm = [m.get("crowd") or {} for m in ms]
        b["skipped"] = sk
        b["bars"] = sum(int(f.get("bars") or 0) for f in cm)
        b["bars_pos_ok"] = sum(int(f.get("bars_pos_ok") or 0) for f in cm)
        b["bars_flow_ok"] = sum(int(f.get("bars_flow_ok") or 0) for f in cm)
        for key in ("pos_side", "flow_side"):
            tot = {k: sum(int((f.get(key) or {}).get(k) or 0) for f in cm) for k in ("LONG", "SHORT", "NONE", "UNKNOWN")}
            b[key] = {**tot, "share": _shares(tot)}
        b["taker"] = {k: sum(int((f.get("taker") or {}).get(k) or 0) for f in cm) for k in ("bars", "found", "vol_mismatch", "valid")}
        b["no_cache"] = sorted(m["symbol"] for m in ms if (m.get("crowd") or {}).get("error"))
        out[tf] = b
    return out


def coverage_warnings(metas: list[dict]) -> list[str]:
    """`data_warnings` ekleri: metrics kapsaması %90'dan azsa `KALABALIK_KAPSAMA %x`, geçerli taker payı %90'dan azsa
    `TAKER_KAPSAMA %x`, önbellek yoksa `KALABALIK_VERİ_YOK`."""
    out = []
    for m in metas:
        f = m.get("crowd")
        if not isinstance(f, dict):
            continue
        if f.get("error"):
            out.append(f"{m['symbol']} {m['tf']}: KALABALIK_VERİ_YOK ({f['error']})")
            continue
        if f.get("coverage_pct") is not None and f["coverage_pct"] < 90.0:
            out.append(f"{m['symbol']} {m['tf']}: KALABALIK_KAPSAMA %{f['coverage_pct']:.1f}")
        tk = f.get("taker") or {}
        if tk.get("bars") and tk.get("valid", 0) < 0.9 * tk["bars"]:
            out.append(f"{m['symbol']} {m['tf']}: TAKER_KAPSAMA %{100.0 * tk.get('valid', 0) / tk['bars']:.1f}")
    return out


# ---------------------------------------------------------------------------- katkı, defter adaylığı, takip/karşı
def contributions(events: list[dict], agg: dict, cfg: L.LabConfig, tfs: list[str] | None = None) -> list[dict]:
    """Her (dilim, kural, yön) HEPSİ satırı: IS/OOS, plaseboya göre fark + gün kümeli aralık, kontrole göre fark + aralık +
    `katkı` (futures_lab._katki), bilgi amaçlı `funding_r`, hüküm, sıkı hüküm ve defter adaylığı (fut_v1 ile aynı düzen)."""
    groups = {(g["tf"], g["family"], g["name"], g["side"]): g for g in agg.get("groups") or [] if g["context"] == "HEPSİ"}
    cut = agg.get("cutoff_ms") or {}
    rows = [e for e in events if e.get("name") in CROWD_NAMES and e.get("r") is not None]
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
                        d[p] = FL._r4(a["r"].mean() - b["r"].mean()) if len(a) and len(b) else None
                        ci[p] = L.diff_ci(a["r"].to_numpy(dtype=float), a["day"].to_numpy(), b["r"].to_numpy(dtype=float),
                                          b["day"].to_numpy(), cfg.bootstrap_iters) if len(a) and len(b) else None
                    return {"IS": d["IS"], "OOS": d["OOS"], "ci95_IS": ci["IS"], "ci95_OOS": ci["OOS"], "n_other": n_o}
                vp, vc = vs(plac), vs(ctrl)
                ns = [st["IS"].get("n", 0), st["OOS"].get("n", 0)] + vc["n_other"]
                katki = FL._katki(vc["IS"], vc["OOS"], vc["ci95_OOS"], ns, cfg)
                g = groups.get((tf, FAMILY, rule, side))
                verdict = g["verdict"] if g else L.V_THIN
                strict = g.get("verdict_strict", verdict) if g else L.V_THIN
                primary = tf == PRIMARY_TF
                book = {"standard": primary and verdict == L.V_STRONG and katki in (KATKI_VAR, KATKI_KESIN),
                        "strict": primary and strict == L.V_STRONG and katki == KATKI_KESIN}
                fr = pd.to_numeric(mine["funding_r"], errors="coerce") if len(mine) else pd.Series([], dtype=float)
                fr_p = {p: FL._r4(fr[mine["period"] == p].dropna().mean()) if len(mine) and fr[mine["period"] == p].notna().any() else None
                        for p in ("IS", "OOS")}
                out.append({"tf": tf, "name": rule, "side": side, "primary": primary, "IS": st["IS"], "OOS": st["OOS"],
                            "vs_placebo": {k: vp[k] for k in ("IS", "OOS", "ci95_IS", "ci95_OOS")} | {"n_placebo": vp["n_other"]},
                            "vs_control": {k: vc[k] for k in ("IS", "OOS", "ci95_IS", "ci95_OOS")} | {"n_ctrl": vc["n_other"]},
                            "katki": katki,
                            "funding_r": {**fr_p, "unknown_share": FL._r4(float(fr.isna().mean())) if len(mine) else None},
                            "verdict": verdict, "verdict_strict": strict, "book": book,
                            "note": NOTE_PRICE_ONLY if verdict == L.V_STRONG and katki == KATKI_YOK else "",
                            "symbols": int(g["symbols"]) if g else int(mine["symbol"].nunique())})
    return out


def _answer(follow: dict, contra: dict) -> str:
    fs, cs = follow["book"]["strict"], contra["book"]["strict"]
    if fs and cs:
        return "ikisi de (çelişki)"
    if fs or cs:
        return ("TAKİP" if fs else "KARŞI") + " (sıkı)"
    fd, cd = follow["book"]["standard"], contra["book"]["standard"]
    if fd and cd:
        return "ikisi de (çelişki)"
    if fd or cd:
        return ("TAKİP" if fd else "KARŞI") + " (standart)"
    return "kanıt yok"


def pairs(contrib: list[dict]) -> list[dict]:
    """Tetik × yön başına TAKİP ve KARŞI satırları yan yana ("takip mi karşı mı" doğrudan cevabı; 1h bilgi)."""
    rows = {(r["tf"], r["name"], r["side"]): r for r in contrib}
    out = []
    for tf in dict.fromkeys(r["tf"] for r in contrib):
        for trig, fr, cr, up_tr, dn_tr in PAIRS:
            for d, txt in ((1, up_tr), (-1, dn_tr)):
                fs, cs = (LONG, SHORT) if d > 0 else (SHORT, LONG)
                f, c = rows.get((tf, fr, fs)), rows.get((tf, cr, cs))
                if f is None or c is None:
                    continue
                brief = lambda r: {"name": r["name"], "side": r["side"], "IS": r["IS"].get("mean_r"), "OOS": r["OOS"].get("mean_r"),  # noqa: E731
                                   "n": [r["IS"].get("n", 0), r["OOS"].get("n", 0)], "verdict": r["verdict"],
                                   "verdict_strict": r["verdict_strict"], "katki": r["katki"], "book": r["book"]}
                out.append({"tf": tf, "primary": tf == PRIMARY_TF, "trigger": trig, "d": d, "crowd_tr": txt,
                            "follow": brief(f), "contra": brief(c), "answer": _answer(f, c) if tf == PRIMARY_TF else "bilgi"})
    return out


# ---------------------------------------------------------------------------- rapor ve günlük
def _ci(ci: list | None) -> str:
    return f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci else "[—]"


def _f2(x: Any) -> str:
    return "—" if x is None else f"{x:+.2f}"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"%{100 * x:.1f}"


def render_section(report: dict[str, Any], fmt: Callable[[dict], str]) -> list[str]:
    """`== KALABALIK (fut_v2) ... ==` bölümü: kapsama, (yoklamada) kör sayımlar ve pos/flow payları; (koşuda) 8 hipotez +
    bilgi satırları, takip/karşı satırları, KEŞİF dilimleri."""
    cr = report.get("crowd") or {}
    lines = [f"\n== KALABALIK (TAKİP / KARŞI) — ön kayıtlı 8 hipotez · {cr.get('version', CROWD_VERSION)} · sha "
             f"{cr.get('registry_sha', CROWD_REGISTRY_SHA)} · kip {cr.get('mode')} ==", HYPOTHESIS_LINE]
    cov = cr.get("coverage") or {}
    if cov:
        st: dict[str, int] = {}
        for c in cov.values():
            st[c.get("status") or "?"] = st.get(c.get("status") or "?", 0) + 1
        firsts = sorted(c["first_day"] for c in cov.values() if c.get("first_day"))
        lines.append("kapsama: " + " · ".join(f"{k} {v}" for k, v in sorted(st.items()))
                     + (f" · ilk metrics günü {firsts[0]} … {firsts[-1]}" if firsts else ""))
        bad = [f"{c.get('sym')}={c.get('status')}" for c in cov.values() if c.get("status") not in ("OK", None)]
        if bad:
            lines.append("  sorunlu: " + ", ".join(bad[:15]))
    if cr.get("mode") == "crowd-probe":
        for tf, b in (cr.get("blind") or {}).items():
            ps, fs = (b.get("pos_side") or {}).get("share") or {}, (b.get("flow_side") or {}).get("share") or {}
            lines.append(f"\n-- kör sayım {tf} (R YOK; yaklaşık kesim) · bar {b.get('bars', 0)} · pos_ok {b.get('bars_pos_ok', 0)} "
                         f"· flow_ok {b.get('bars_flow_ok', 0)} · atlanan {b.get('skipped') or {}}")
            lines.append(f"   pos_side LONG {_pct(ps.get('LONG'))} SHORT {_pct(ps.get('SHORT'))} NONE {_pct(ps.get('NONE'))} · "
                         f"flow_side LONG {_pct(fs.get('LONG'))} SHORT {_pct(fs.get('SHORT'))} NONE {_pct(fs.get('NONE'))} · "
                         f"taker {b.get('taker') or {}}")
            for r in b.get("rows") or []:
                lines.append(f"  {r['name']:<40}{r['side']:<6} keşif {r['IS']:>5} | doğrulama {r['OOS']:>5}"
                             + (f" · {r['note']}" if r.get("note") else ""))
        return lines
    for r in cr.get("contributions") or []:
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
    prs = cr.get("pairs") or []
    if prs:
        lines.append("\n-- takip mi karşı mı (doğrulama ort.R; hüküm / sıkı) --")
        for p in prs:
            f, c = p["follow"], p["contra"]
            lines.append(f"{p['tf']:>4}{'' if p['primary'] else ' (bilgi)'} {p['crowd_tr']}: TAKİP {f['side']} {_f2(f['OOS'])} "
                         f"({f['verdict']}/{f['verdict_strict']}, katkı {f['katki']}) | KARŞI {c['side']} {_f2(c['OOS'])} "
                         f"({c['verdict']}/{c['verdict_strict']}, katkı {c['katki']}) → {p['answer']}")
    kesif = [g for g in report.get("groups") or [] if g.get("context") in CTX_KEYS
             and g.get("family") not in ("placebo", "control") and g.get("verdict") in (L.V_STRONG, L.V_WEAK)]
    if kesif:
        lines.append(f"\n-- KEŞİF dilimleri (hipotez değil; {', '.join(CTX_KEYS)}) · {L.V_STRONG} "
                     f"{sum(1 for g in kesif if g['verdict'] == L.V_STRONG)} · {L.V_WEAK} {sum(1 for g in kesif if g['verdict'] == L.V_WEAK)}")
        for g in sorted(kesif, key=lambda x: -x["OOS"]["mean_r"])[:15]:
            lines.append(f"  {g['tf']:>4} {g['name']:<26}{g['side']:<6}{g['context'] + '=' + str(g['bucket']):<32} "
                         f"doğrulama {fmt(g['OOS'])} · {g['verdict']}")
    return lines


def log_lines(report: dict[str, Any]) -> list[str]:
    """İş günlüğü satırları (her biri ≤ 4 KB): `CROWD_REGISTRY`; yoklamada `CROWD_COUNT` + `CROWD_SHARE`; koşuda
    `CROWD_RESULT` + `CROWD_PAIR` (`CROWD_COV` / `CROWD_PROBE` indirme ve yoklama sırasında yazılır)."""
    cr = report.get("crowd") or {}
    out = [FD.log_line("CROWD_REGISTRY", {"version": cr.get("version", CROWD_VERSION), "sha": cr.get("registry_sha", CROWD_REGISTRY_SHA),
                                          "mode": cr.get("mode"), "trials": TRIALS["hypotheses"]})]
    for tf, b in (cr.get("blind") or {}).items():
        for r in b.get("rows") or []:
            out.append(FD.log_line("CROWD_COUNT", r))
        out.append(FD.log_line("CROWD_SHARE", {"tf": tf, "bars": b.get("bars"), "bars_pos_ok": b.get("bars_pos_ok"),
                                               "bars_flow_ok": b.get("bars_flow_ok"), "pos_side": b.get("pos_side"),
                                               "flow_side": b.get("flow_side"), "taker": b.get("taker"), "skipped": b.get("skipped"),
                                               "no_cache": b.get("no_cache")}))
    short = lambda st: {"n": st.get("n", 0), "mean_r": st.get("mean_r"), "ci95": st.get("ci95")}  # noqa: E731
    for r in cr.get("contributions") or []:
        vp, vc = r["vs_placebo"], r["vs_control"]
        out.append(FD.log_line("CROWD_RESULT", {
            "tf": r["tf"], "name": r["name"], "side": r["side"], "IS": short(r["IS"]), "OOS": short(r["OOS"]),
            "vs_placebo": {"IS": vp["IS"], "OOS": vp["OOS"], "ci95_OOS": vp["ci95_OOS"]},
            "vs_control": {"IS": vc["IS"], "OOS": vc["OOS"], "ci95_OOS": vc["ci95_OOS"], "n_ctrl": vc["n_ctrl"]},
            "katki": r["katki"], "funding_r": r["funding_r"], "verdict": r["verdict"], "verdict_strict": r["verdict_strict"],
            "book": r["book"], "symbols": r["symbols"]}))
    for p in cr.get("pairs") or []:
        out.append(FD.log_line("CROWD_PAIR", p))
    return out
