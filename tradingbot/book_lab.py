# -*- coding: utf-8 -*-
"""DEFTER ARAŞTIRMASI — book_v1 (docs/BOOK_RESEARCH_V1.md; 2026-10-05): D4 (4h Donchian 20/10 trend), C4 (4h mum
varyasyonları) ve Formasyon (4h üç beyaz asker + RSI14 > 70) defterlerinin bugünkü kuralı ya da önceden yazılmış, gerekçeli,
az sayıda değişikliği maliyet sonrası ve defterin gerçek kapasite sınırları içinde ayda en az net +%1 veriyor mu? Salt
araştırma: defterlere, stratejilere, ajanlara, config'e ve parametrelere DOKUNMAZ; yalnız geçmiş mumları ve fonlamayı okur,
rapor yazar. İçe aktarılırken ağa çıkmaz.

* ÖN KAYIT: bütün varyant tanımları, süzgeçler, plasebo, istatistik, çoklu test, kapasite, aylık ölçü, sabit veri
  pencereleri, evrenler, PIT kipi, ana bot kapsamı, sonuç metinleri ve belgenin belirsiz yerlerinin okunuşu (`READINGS_TR`)
  `BOOK_REGISTRY`'dedir; `BOOK_REGISTRY_SHA` testle sabitlenir ve belgeye yazılır. Değişiklik = book_v2 + yeni deneme.
* Kural kodu defterlerin KENDİ fonksiyonlarıdır: D4 kırılımı `donchian_trend._fresh_breakout` (laboratuvar göstergeleri
  `signal_lab.indicators` / `aux_series`); C4 tespiti ve eşleştirilmiş plasebosu `candle_lab.variation_events` (canlı defterin
  `candle_dsl.detect_last`'ı, 500 barlık pencere); Formasyon kaydı `signal_lab.catalog_events` adım 1 (canlı v3'ün
  `structures.analyze`'ı); bağlam kovaları (VOL_OK, VOL_CONFIRM) `signal_lab.context`; fonlama `futures_lab.funding_carry`;
  boyut `learning_mode.fit_size`; istatistik `signal_lab.r_stats` / `verdict` / `diff_ci` / `verdict_strict`. Yeniden yazılan
  küçük parçalar (EMA200 rejiminin önek dizisi, temiz pencere, 55 barlık kanal) eşdeğerlik testleriyle bağlanır.
* Simülatör veri sonuna değmeyen her işlemde `signal_lab.simulate` / `simulate_rule` ile birebir aynıdır (parite testi);
  farkları `DATA_END` tamamlaması ve kanal çıkışının referans serisi.

PAPER/geçmiş testtir; kâr garantisi değildir.
"""
from __future__ import annotations

import copy
import csv
import dataclasses
import gzip
import hashlib
import io
import json
import math
import re
import subprocess
import time
import urllib.parse
import zipfile
from bisect import bisect_left
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd

from . import signal_lab as L
from .timeframes import tf_ms

LONG, SHORT = L.LONG, L.SHORT
SIDES = (LONG, SHORT)
VERSION = "book_v1"
DOC = "docs/BOOK_RESEARCH_V1.md"
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
TF4, TF1 = "4h", "1d"

# ---------------------------------------------------------------------------- sabit veri pencereleri (§4.1; UTC ms)
START_4H_MS = 1_640_995_200_000          # 2022-01-01 00:00 — her coinin 4h serisi (ya da listeleme)
START_1D_MS = 1_577_836_800_000          # 2020-01-01 00:00 — her coinin ve BTC'nin 1d serisi (ya da listeleme)
PRICE_END_MS = 1_790_812_800_000         # 2026-10-01 00:00 — bütün çıkışlar, süre sınırları ve DATA_END
DECISION_START_MS = 1_672_531_200_000    # 2023-01-01 00:00 — karar penceresi başı
IS_END_MS = 1_735_689_600_000            # 2025-01-01 00:00 — keşif / doğrulama sınırı
FUNDING_START_MS = 1_669_852_800_000     # 2022-12-01 00:00
FUNDING_END_MS = 1_790_899_200_000       # 2026-10-02 00:00 (PRICE_END'den sonra en az bir uzlaşma)

# ---------------------------------------------------------------------------- ön kayıtlı sabitler
COST_STOP_MIN = 0.016                    # COST_STOP: |giriş − stop| / giriş ≥ %1,6
RISK_PCT, RISK_PCT_INFO = 0.5, 1.0       # learning_mode.risk_per_trade_pct; %1 yalnız bilgi
TARGET_PCT_MONTH = 1.0
MONTH_SEED, MONTH_ITERS = 20261005, 10_000
MT_SEED_REAL, MT_SEED_PLACEBO, MT_ITERS = 20261006, 20261007, 20_000
HOLM_ALPHA = 0.05
CENSOR_MAX, BAD_MAX, NAN_FUND_MAX = 0.10, 0.01, 0.05
FUND_FILL_RATE, FUND_FILL_HOURS = 0.0001, 8
D4_MIN_I, CLEAN_D4, CLEAN_FM, CLEAN_REGIME = 210, 210, 300, 200
EMA_REGIME = 200
PIT_TOP, PIT_DAYS, PIT_MIN_BARS, PIT_MISSING_MAX, PIT_PAIR_MIN_COVER = 40, 30, 30, 0.05, 0.90
MIN_PLACEBO_N = L.LabConfig().min_oos   # plasebo IS ve OOS n ≥ 20 (aggregate kuralı)

V_STRONG, V_WEAK, V_LOSS, V_NONE, V_THIN = L.V_STRONG, L.V_WEAK, L.V_LOSS, L.V_NONE, L.V_THIN
ST_MEETS = "HEDEFİ KARŞILIYOR"
ST_NOT_MEETS = "HEDEFİ KARŞILAMIYOR"
ST_FILTER_WAIT = "FİLTRE BEKLİYOR"
ST_FUND_MISSING = "FONLAMA EKSİK"
ST_CENSOR = "SANSÜR YÜKSEK"
ST_BAD = "VERİ BOZUK"
ST_MT_PASS = "ÇOKLU TESTİ GEÇTİ"
ST_MT_FAIL = "ÇOKLU TESTTE ELENDİ"
ST_CHANCE = "TESADÜFLE AÇIKLANABİLİR"
ST_BASE = "TABAN YA DA VERİ GÖZETLEMELİ (tek başına öneri olamaz)"
ST_PIT_WAIT = "PIT TEYİDİ BEKLİYOR"
ST_PIT_FAIL = "PIT TEYİDİ GEÇMEDİ"
ST_PIT_UNVERIFIED = "PIT DOĞRULANAMADI"
ST_PIT_PASS = "PIT TEYİDİ GEÇTİ"
ST_CANDIDATE = "ÖNERİ ADAYI"
NO_TARGET_TR = "bu defterler ve önceden yazılmış varyantları maliyet sonrası, kapasite içinde +%1/ay vermedi"

# ---------------------------------------------------------------------------- evrenler (§4.3)
PRIMARY_UNIVERSE: tuple[str, ...] = (
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT",
    "DOT/USDT", "NEAR/USDT", "TRX/USDT", "BCH/USDT", "UNI/USDT", "FIL/USDT", "XLM/USDT", "AAVE/USDT", "APT/USDT", "ARB/USDT",
    "OP/USDT", "INJ/USDT", "SUI/USDT")
#: config.yaml → entry_universe.symbols (2026-10-05, commit 3e42da4'teki liste; kayda kopyalandı)
INFO_UNIVERSE: tuple[str, ...] = (
    "BTC/USDT", "ETH/USDT", "ZEC/USDT", "SOL/USDT", "NEAR/USDT", "UNI/USDT", "XRP/USDT", "ONE/USDT", "BNB/USDT", "ARB/USDT",
    "DOGE/USDT", "LSK/USDT", "SUI/USDT", "ENA/USDT", "G/USDT", "WLD/USDT", "ADA/USDT", "ONDO/USDT", "1000PEPE/USDT", "COTI/USDT",
    "LINK/USDT", "REZ/USDT", "DASH/USDT", "AAVE/USDT", "TAO/USDT", "BCH/USDT", "AVAX/USDT", "DOT/USDT", "FIL/USDT", "APT/USDT",
    "SYN/USDT", "LTC/USDT", "XLM/USDT", "INJ/USDT", "FET/USDT", "TRX/USDT", "XMR/USDT", "1000SHIB/USDT", "OP/USDT", "KSM/USDT")
BTC_SYMBOL = "BTC/USDT"
UNIVERSES = ("primary", "info", "pit")

#: PIT sembol havuzu dışlamaları (§4.3; koşudan ÖNCE, 2026-10-05 arşiv listesindeki adlardan). Kurallar: alt çizgili vadeli
#: sözleşme; USDT ile bitmeyen (USDC/BUSD kotalı, ...SETTLED); aşağıdaki tabanlar.
PIT_EXCLUDE = {
    "endeks": ["BTCDOM", "DEFI", "FOOTBALL", "BLUEBIRD"],
    "stabil": ["USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "USDE", "PYUSD", "RLUSD", "USD1", "EUR", "AEUR"],
    "emtia_doviz": ["XAU", "XAG", "XPT", "XPD", "XAUT", "PAXG", "COPPER", "NATGAS", "CL", "BZ", "USDBRL"],
    "hisse_etf_halka_arz_oncesi": [
        "AAOI", "AAPL", "ACN", "ADBE", "ALAB", "AMAT", "AMD", "AMZN", "ANET", "ANTHROPIC", "APLD", "APP", "ARM", "ASML", "ASTS",
        "AVGO", "AXTI", "BABA", "BITO", "BMNR", "BRKB", "BYD", "CBRS", "CIEN", "COHR", "COIN", "COST", "CRCL", "CRDO", "CRM",
        "CRWD", "CRWV", "CSCO", "CSOPSAMSUNG2L", "CSOPSKHYNIX2L", "CVNA", "CXMT", "DDOG", "DELL", "DIS", "DJT", "DKNG", "DRAM",
        "EBAY", "EWJ", "EWY", "EWZ", "FLNC", "GDX", "GEV", "GLW", "GME", "GOOGL", "GTLB", "HANA", "HANMI", "HIMS", "HK0625",
        "HK0700", "HK0992", "HK1810", "HOOD", "HPE", "HUT", "HYUNDAI", "IBM", "INTC", "IONQ", "IREN", "IWM", "JPM", "KLAC",
        "KODEX200", "KUAISHOU", "LGELECTRONICS", "LITE", "LLY", "LRCX", "MARA", "MDB", "MEITUAN", "META", "MINIMAX", "MRK",
        "MRNA", "MRVL", "MSFT", "MSTR", "MU", "MUU", "NAVER", "NBIS", "NFLX", "NKE", "NOK", "NOW", "NVDA", "NVDL", "NVO", "OKLO",
        "OPENAI", "ORCL", "PANW", "PDD", "PLTR", "POPMART", "PYPL", "QCOM", "QQQ", "RDDT", "RIVN", "RKLB", "SAMSUNG", "SAMSUNGEM",
        "SHOP", "SKHY", "SKHYNIX", "SLX", "SMCI", "SMH", "SNDK", "SNOW", "SOFI", "SONY", "SOXL", "SOXS", "SPCX", "SPY", "SQQQ",
        "STRC", "TEM", "TENCENT", "TQQQ", "TSLA", "TSLL", "TSM", "TTWO", "TXN", "TZA", "UBER", "UNH", "UNITREE", "URNM", "UVXY",
        "WDC", "WMT", "XBI", "XLE", "XOM", "ZHIPU", "ZHONGJI"],
}
PIT_LIST_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/monthly/klines/"

# ---------------------------------------------------------------------------- defterler ve kapasite (§8.2)
BOOKS = ("D4", "C4", "FM")
BOOK_LIVE = {"D4": "d4_donchian_20_10 (strategy_paper_trend4h)", "C4": "c4_candle_variations (strategy_paper_candle4h)",
             "FM": "pattern_trader, protocol momentum_4h_v3"}
CAPACITY = {"D4": {"equity": 200.0, "slots": 20, "leverage_max": 3},
            "C4": {"equity": 200.0, "slots": 20, "leverage_max": 3},
            "FM": {"equity": 100.0, "slots": 30, "leverage_max": 3}}
CAP_COMMON = {"risk_pct": RISK_PCT, "reserve_pct": 5.0, "liq_buffer_mult": 2.0, "mmr": 0.004, "hard_cap_pct": 2.0,
              "max_position_pct": 30.0, "slots_info": 3}
CAP_MODES = ("BUMP", "NO_BUMP")

# ---------------------------------------------------------------------------- varyantlar (§6)
_D4_EXIT = {"kind": "channel", "ref": {LONG: "lo10", SHORT: "hi10"}, "max_bars": 300}


def _d4(**kw: Any) -> dict[str, Any]:
    base = {"book": "D4", "tf": TF4, "sides": [LONG], "entry_ref": {LONG: "hi20", SHORT: "lo20"}, "stop_atr": 2.0,
            "exit": copy.deepcopy(_D4_EXIT), "risk_atr": [0.1, 10.0], "chase_atr": None, "min_i": D4_MIN_I,
            "clean_bars": CLEAN_D4, "filters": [], "placebo_filters": [], "cells": ["LONG"], "control": False}
    base.update(kw)
    return base


D4_VARIANTS: dict[str, dict[str, Any]] = {
    "D4_00_BASE": _d4(change_tr="yok (bugünkü kural)"),
    "D4_01_BTC_UP": _d4(change_tr="+ BTC_UP", filters=["BTC_UP"], placebo_filters=["BTC_UP"]),
    "D4_02_COIN_UP": _d4(change_tr="+ COIN_UP", filters=["COIN_UP"], placebo_filters=["COIN_UP"]),
    "D4_03_VOL_OK": _d4(change_tr="+ VOL_OK", filters=["VOL_OK"], placebo_filters=["VOL_OK"]),
    "D4_04_VOL_CONFIRM": _d4(change_tr="+ VOL_CONFIRM", filters=["VOL_CONFIRM"]),
    "D4_05_STOP3ATR": _d4(change_tr="ilk stop c[i] − 3 × ATR14", stop_atr=3.0),
    "D4_06_EXIT_LO20": _d4(change_tr="çıkış c[k] < lo20[k]", exit={"kind": "channel", "ref": {LONG: "lo20"}, "max_bars": 300}),
    "D4_07_1D": _d4(change_tr="taban kuralın 1d hâli (en çok 300 günlük bar)", tf=TF1),
    "D4_08_1D_55_20": _d4(change_tr="1d; giriş c[i] > hi55[i] ve c[i−1] ≤ hi55[i−1]; çıkış c[k] < lo20[k]", tf=TF1,
                          entry_ref={LONG: "hi55"}, exit={"kind": "channel", "ref": {LONG: "lo20"}, "max_bars": 300}),
    "D4_09_1D_BTC_UP": _d4(change_tr="D4_07 + BTC_UP", tf=TF1, filters=["BTC_UP"], placebo_filters=["BTC_UP"]),
    "D4_10_1D_REGIME_BOTH": _d4(change_tr="D4_07; LONG yalnız BTC_UP, SHORT yalnız BTC_DOWN", tf=TF1, sides=[LONG, SHORT],
                                filters=["REGIME_FOLLOW"], placebo_filters=["REGIME_FOLLOW"], cells=["BOTH", "LONG", "SHORT"]),
    "D4_11_BTC_COIN_UP": _d4(change_tr="+ BTC_UP + COIN_UP", filters=["BTC_UP", "COIN_UP"], placebo_filters=["BTC_UP", "COIN_UP"]),
    "D4_12_COST_STOP": _d4(change_tr="+ COST_STOP", filters=["COST_STOP"], placebo_filters=["COST_STOP"]),
}

CV_ORDER = ("CV001_BREAKOUT20_TREND_VOL_L", "CV002_BREAKOUT20_TREND_VOL_S", "CV003_PULLBACK_ENGULF_L", "CV004_PULLBACK_ENGULF_S",
            "CV005_SUPPORT_HARAMI_L", "CV006_RESIST_HARAMI_S", "CV007_SWEEP_REJECT_ENGULF_L", "CV008_SWEEP_REJECT_ENGULF_S")
#: belgedeki tablo (§6.2); kayıttaki tanımlarla eşitliği testle denetlenir
CV_BASE_SHA = {"CV001_BREAKOUT20_TREND_VOL_L": "568292b548308023", "CV002_BREAKOUT20_TREND_VOL_S": "d2a63f46ff2a72f5",
               "CV003_PULLBACK_ENGULF_L": "6a51dc3b125a6dea", "CV004_PULLBACK_ENGULF_S": "904dcdb6954657b4",
               "CV005_SUPPORT_HARAMI_L": "e681cff403c840b3", "CV006_RESIST_HARAMI_S": "e7dafeddb50d7f62",
               "CV007_SWEEP_REJECT_ENGULF_L": "07fc988844ebe90f", "CV008_SWEEP_REJECT_ENGULF_S": "fd7a16f7a2274991"}


def _c4(**kw: Any) -> dict[str, Any]:
    base = {"book": "C4", "tf": TF4, "sides": [LONG, SHORT], "cvs": list(CV_ORDER), "dsl_change": None, "detection": "base",
            "filters": [], "placebo_filters": [], "cells": ["BOTH", "LONG", "SHORT"], "control": False, "snooped": False}
    base.update(kw)
    return base


C4_VARIANTS: dict[str, dict[str, Any]] = {
    "C4_00_BASE": _c4(change_tr="yok (bugünkü defter)"),
    "C4_01_REGIME": _c4(change_tr="LONG CV'ler yalnız BTC_UP, SHORT CV'ler yalnız BTC_DOWN (tespit anında)",
                        filters=["REGIME_FOLLOW"], placebo_filters=["REGIME_FOLLOW"]),
    "C4_02_EXIT_1R": _c4(change_tr="exit.target_r 1.0, max_hold_bars 24", dsl_change={"exit": {"target_r": 1.0, "max_hold_bars": 24}}),
    "C4_03_EXIT_3R": _c4(change_tr="exit.target_r 3.0, max_hold_bars 60", dsl_change={"exit": {"target_r": 3.0, "max_hold_bars": 60}}),
    "C4_04_WIDE_STOP": _c4(change_tr="stop.atr_buffer 1.0", dsl_change={"stop": {"atr_buffer": 1.0}}, detection="stop1"),
    "C4_05_COST_STOP": _c4(change_tr="+ COST_STOP (girişte)", filters=["COST_STOP"], placebo_filters=["COST_STOP"]),
    "C4_06_VOL_OK": _c4(change_tr="context.atr_regime [None, 1.25] (VOL_OK_DSL)", dsl_change={"context": {"atr_regime": [None, 1.25]}},
                        detection="volok"),
    "C4_07_TREND_ONLY": _c4(change_tr="yalnız CV001–CV004", cvs=list(CV_ORDER[:4]), snooped=True),
    "C4_08_REGIME_COST": _c4(change_tr="C4_01 + C4_05", filters=["REGIME_FOLLOW", "COST_STOP"],
                             placebo_filters=["REGIME_FOLLOW", "COST_STOP"]),
}
#: tespit kümeleri: çıkış dışında aynı tanımlar tabanın tespitini paylaşır (tespit ve plasebo seçimi çıkışı okumaz)
C4_DETECTION_CHANGES = {"base": None, "stop1": {"stop": {"atr_buffer": 1.0}}, "volok": {"context": {"atr_regime": [None, 1.25]}}}

_FM_SIGNALS = {LONG: {"name": "THREE_WHITE_SOLDIERS", "rsi_op": ">", "rsi": 70.0},
               SHORT: {"name": "THREE_BLACK_CROWS", "rsi_op": "<", "rsi": 30.0}}
_FM_TARGET = {"kind": "target", "rr": 2.0, "max_bars": 24}
_FM_TREND = {"kind": "channel", "ref": {LONG: "lo10"}, "max_bars": 300}


def _fm(**kw: Any) -> dict[str, Any]:
    base = {"book": "FM", "tf": TF4, "sides": [LONG], "signals": copy.deepcopy(_FM_SIGNALS), "rsi_filter": True,
            "exit": copy.deepcopy(_FM_TARGET), "risk_atr": [0.1, 5.0], "chase_atr": 1.0, "clean_bars": CLEAN_FM,
            "stop_tr": "katalog kaydının stop'u, DEĞİŞTİRİLMEDEN", "filters": [], "placebo_filters": [], "cells": ["LONG"],
            "control": False}
    base.update(kw)
    return base


FM_VARIANTS: dict[str, dict[str, Any]] = {
    "FM_00_BASE": _fm(change_tr="yok (bugünkü kural)"),
    "FM_01_BTC_UP": _fm(change_tr="+ BTC_UP", filters=["BTC_UP"], placebo_filters=["BTC_UP"]),
    "FM_02_COIN_UP": _fm(change_tr="+ COIN_UP", filters=["COIN_UP"], placebo_filters=["COIN_UP"]),
    "FM_03_VOL_OK": _fm(change_tr="+ VOL_OK", filters=["VOL_OK"], placebo_filters=["VOL_OK"]),
    "FM_04_VOL_CONFIRM": _fm(change_tr="+ VOL_CONFIRM (teyit barında)", filters=["VOL_CONFIRM"]),
    "FM_05_COST_STOP": _fm(change_tr="+ COST_STOP", filters=["COST_STOP"], placebo_filters=["COST_STOP"]),
    "FM_07_EXIT_TREND": _fm(change_tr="hedef yok; çıkış c[k] < lo10[k] → k+1 açılışı; en çok 300 bar; kovalama ve 0,1–5 ATR girişte",
                            exit=copy.deepcopy(_FM_TREND)),
    "FM_08_EXIT_3R": _fm(change_tr="hedef 3R, en çok 60 bar", exit={"kind": "target", "rr": 3.0, "max_bars": 60}),
    "FM_09_REGIME_BOTH": _fm(change_tr="LONG taban sinyal yalnız BTC_UP; SHORT üç kara karga + RSI14 < 30 yalnız BTC_DOWN",
                             sides=[LONG, SHORT], filters=["REGIME_FOLLOW"], placebo_filters=["REGIME_FOLLOW"],
                             cells=["BOTH", "LONG", "SHORT"]),
    "FM_10_BTC_UP_EXIT_TREND": _fm(change_tr="FM_01 + FM_07", filters=["BTC_UP"], placebo_filters=["BTC_UP"],
                                   exit=copy.deepcopy(_FM_TREND)),
    "FM_CTRL_NO_RSI": _fm(change_tr="KONTROL: RSI süzgeci yok (üç beyaz asker tek başına)", rsi_filter=False, control=True),
}
VARIANTS: dict[str, dict[str, Any]] = {**D4_VARIANTS, **C4_VARIANTS, **FM_VARIANTS}
BASE_VARIANTS = ("D4_00_BASE", "C4_00_BASE", "FM_00_BASE")
SNOOPED_VARIANTS = ("C4_07_TREND_ONLY",)
SIGNAL_FILTERS = ("VOL_CONFIRM",)             # sinyalin kendisi: plaseboya uygulanmaz
ENTRY_FILTERS = ("COST_STOP",)                # giriş fiyatına bağlı: girişte denetlenir


def cell_id(vid: str, scope: str) -> str:
    return f"{vid}|{scope}"


PRIMARY_CELLS: tuple[str, ...] = tuple(cell_id(v, s) for v, d in VARIANTS.items() if not d.get("control") for s in d["cells"])
CONTROL_CELLS: tuple[str, ...] = tuple(cell_id(v, s) for v, d in VARIANTS.items() if d.get("control") for s in d["cells"])
ALL_CELLS = PRIMARY_CELLS + CONTROL_CELLS


def _month_range(a: str, b: str) -> list[str]:
    y, m = int(a[:4]), int(a[5:])
    out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


IS_MONTHS = _month_range("2023-01", "2024-12")
OOS_MONTHS = _month_range("2025-01", "2026-09")
PIT_MONTHS = _month_range("2023-01", "2026-09")

# ---------------------------------------------------------------------------- C4 tanımları (DSL değişiklikleri)
_C4_VARS_CACHE: dict[str, Any] = {}


def _apply_dsl_change(defn: Mapping[str, Any], change: Mapping[str, Any] | None) -> dict[str, Any]:
    """Tanıma ön kayıtlı değişikliği uygular: `exit` yerine konur; `stop` ve `context` anahtar anahtar güncellenir."""
    out = copy.deepcopy(dict(defn))
    for k, v in (change or {}).items():
        if k == "exit":
            out["exit"] = copy.deepcopy(v)
        else:
            out[k] = {**copy.deepcopy(out.get(k) or {}), **copy.deepcopy(v)}
    return out


def c4_variation(cv: str, change: Mapping[str, Any] | None):
    """CV kaydının (`candle_variations`) değişiklik uygulanmış ayrıştırılmış hâli. Kimlik AYNI kalır (defter kaydı ve
    `candle_variations.VARIATIONS` değişmez); yeni tanım yeni `definition_sha` alır."""
    key = json.dumps([cv, change], sort_keys=True)
    if key not in _C4_VARS_CACHE:
        from . import candle_dsl as D
        from . import candle_variations as CV
        entry = next(e for e in CV.VARIATIONS if isinstance(e, dict) and e.get("id") == cv)
        _C4_VARS_CACHE[key] = D.parse_variation({**entry, "definition": _apply_dsl_change(entry["definition"], change)})
    return _C4_VARS_CACHE[key]


def _c4_registry_shas() -> dict[str, dict[str, str]]:
    return {vid: {cv: c4_variation(cv, d["dsl_change"]).definition_sha for cv in d["cvs"]} for vid, d in C4_VARIANTS.items()}


# ---------------------------------------------------------------------------- okunuşlar (mühre girer)
READINGS_TR = (
    "anahtarlar: signal_lab._h ile; <sembol> 'BTC/USDT' biçimi (laboratuvar ve evren listesi yazımı), <dilim> '4h'/'1d', <kimlik> "
    "varyant kimliği (ör. D4_00_BASE; PLACEBO_ öneki YOK), <yön> LONG/SHORT, zaman damgası = barın AÇILIŞ zamanı (ms tamsayı)",
    "karar anı t = sinyal barının open_time + dilim (laboratuvarın t_ms'i); dönem t ile: keşif 2023-01-01 ≤ t < 2025-01-01, "
    "doğrulama 2025-01-01 ≤ t < 2026-10-01; karar penceresi dışındaki sinyal (ısınma) işlem ve plasebo üretmez",
    "pencere kırpması: barın open_time'ı [ilk bar sınırı, PRICE_END) aralığında; 4h 2022-01-01, 1d 2020-01-01 (coinler ve BTC); "
    "fonlama uzlaşma zamanı [2022-12-01, 2026-10-02); fonlama yalnız aylık fundingRate dosyalarından, 2022-12 … 2026-10 (arşivde "
    "günlük fundingRate klasörü yok, 2026-10-05 S3 listesi); PRICE_END sonrası uzlaşmalar 2026-10 dosyasındandır (ay dosyası "
    "ay bitince yayımlanır)",
    "temiz pencere: bozuk bar = candle_lab.valid_ends tanımı (sonlu olmayan OHLC, h < max(o,c), l > min(o,c), sonlu olmayan ya da "
    "negatif hacim); boşluk = ardışık iki açılış farkı ≠ dilim; i'de biten w barlık pencere temiz = i−w+1..i barlarının hiçbiri "
    "bozuk değil VE pencere içindeki her ardışık çift boşluksuz (pencerenin ilk barından önceki boşluk sayılmaz; valid_ends ile "
    "aynı); D4 w = 210 (4h ve 1d), Formasyon w = 300, C4 valid_ends (500), BTC_UP/COIN_UP için 1d w = 200",
    "BTC_UP/BTC_DOWN/COIN_UP: kullanılan 1d bar k = open_time + 1 gün ≤ t olan son bar; EMA200 = regime_gate.btc_regime ile "
    "aynı: serinin ilk barından k'ye kadar SONLU kapanışlar üzerinde regime_gate.ema_last (ilk 200 kapanışın SMA'sıyla tohum); "
    "close[k] > EMA → UP, değilse DOWN; close[k] sonlu değil, sonlu kapanış < 200, k'de biten 200 barlık 1d pencere temiz "
    "değil ya da k barı t'den önceki son 24 saatte kapanmamış (1d seride t'ye değen boşluk; bayat değer kullanılmaz) ise "
    "BİLİNMİYOR → giriş yok, plasebo yok; REGIME_FOLLOW = LONG için BTC_UP, SHORT için BTC_DOWN",
    "VOL_OK = signal_lab.context'in volatilite kovası 'düşük' ya da 'normal' (ATR%/atr_med ≤ 1,25; tam seri); VOL_CONFIRM = hacim "
    "kovası 'yüksek(>1.5x)' (v[i] / önceki 20 bar ortalaması > 1,5); kova 'bilinmiyor' → geçmez",
    "COST_STOP: s = |giriş − stop| / giriş, giriş = sonraki barın açılışı (kaymasız laboratuvar girişi); s ≥ 0,016 geçer (eşitlik "
    "geçer); geçmezse STOP_TOO_TIGHT_FOR_COST (gerçek ve plasebo aynı)",
    "atlama sırası: NO_ENTRY_BAR (giriş barı yok), NO_ATR, STOP_TOO_CLOSE (risk ≤ alt × ATR), STOP_TOO_FAR (risk > üst × ATR), "
    "CHASE (yalnız tetikli olaylarda: s × (giriş − tetik) > 1 ATR), STOP_TOO_TIGHT_FOR_COST, sonra simülasyon ve BAD_BAR",
    "ATR (risk aralığı ve kovalama): D4 ve Formasyon için sinyal barı i'deki laboratuvar ATR14'ü (tam seri), C4 için candle_lab'ın "
    "atr_sig'i (pencere ATR14'ü); plasebo için kendi barı j'deki aynı tanım",
    "D4 SHORT (yalnız D4_10): giriş donchian_trend._fresh_breakout'un aynası (−close, −lo20); stop c[i] + 2 × ATR14[i]; çıkış "
    "c[k] > hi10[k]; hi55 = önceki 55 barın en yükseği (i hariç), aux_series'in kanal kuruluşuyla aynı",
    "Formasyon: kayıtlar signal_lab.catalog_events (LabConfig stride=1, pencere 300) ile; LONG THREE_WHITE_SOLDIERS, SHORT "
    "THREE_BLACK_CROWS; RSI14 = signal_lab.indicators (tam seri) i'de; LONG RSI > 70 kesin, SHORT RSI < 30 kesin, NaN geçmez; tetik "
    "= kaydın trigger.level'ı (kovalama ölçüsü); stop = kaydın stop'u; ATR14[i] sonlu ve > 0 olmalı; tetiği olmayan kayıt sinyal "
    "değildir (canlı v3'ün RECORD_GEOMETRY_INCOMPLETE'i)",
    "ham sinyal (plasebo oranı p ve Formasyon q havuzu): tespit + temiz pencere + ATR14[i] sonlu ve > 0 (+ D4'te i ≥ 210) + "
    "varyantın bütün sinyal ve tespit anı süzgeçleri (VOL_CONFIRM, BTC_UP/DOWN, COIN_UP, VOL_OK; Formasyon'da RSI); risk aralığı, "
    "kovalama, COST_STOP, NO_ENTRY_BAR ve BAD_BAR'dan ÖNCE",
    "uygun bar (D4 ve Formasyon plasebosu): ATR14[j] sonlu ve > 0, temiz pencere (D4 210, Formasyon 300), D4'te j ≥ 210, "
    "varyantın plaseboya uygulanan tespit anı süzgeçleri (COST_STOP girişte), karar anı karar penceresinde; p = dönemdeki ham "
    "sinyal / dönemdeki uygun bar (uygun bar 0 → p 0); seçim u = _h(sembol, dilim, kimlik, yön, ts[j]) < p(dönem)",
    "Formasyon plasebo stop'u: q = s × (close[i] − kayıt stop'u) / ATR14[i] (aynı seri ve yönde, varyantın kendi ham isabetleri, "
    "i < j, 2022 ısınması dahil); seçilen q = havuz[int(_h(sembol, dilim, kimlik, 'q', ts[j]) × havuz boyu)]; stop = c[j] − s × q × "
    "ATR14[j]; sonlu, > 0 ve koruyucu tarafta değilse BAD_STOP (sayılır); havuz boş → NO_REAL_RISK; tetik c[j]",
    "C4: değişmiş tanımlar CV kimliğini KORUR (candle_lab'ın plasebo anahtarı PLACEBO_<CV> aynı kalır; varyantı hücre ayırır); "
    "çıkışı dışında aynı olan tanımlar (C4_02, C4_03) tabanın tespitini ve plasebosunu paylaşır (detect_last ve placebo_context "
    "çıkışı okumaz; testle denetlenir); simülasyon varyantın kendi target_r / max_hold_bars / risk_atr_bounds değeriyle",
    "C4 tekilleştirme önceliği CV001 > CV002 > … > CV008 (config sırası); aynı sembol ve aynı karar barında tespit anı süzgecinden "
    "(rejim) geçen eşleşmelerden yalnız en öncelikli olanın olayı adaydır; yalnız-LONG/yalnız-SHORT hücreleri kendi yönlerinin "
    "CV'leri içinde, havuzlu hücre varyantın bütün CV'leriyle tekilleştirilir; elenen seçilenden sonra alttakine düşülmez",
    "havuzlu hücrede yön hükmü (§8.3 şart 8) = aynı varyantın yalnız LONG ve yalnız SHORT birincil hücrelerinin hükmü (fonlamasız ve "
    "fonlamalı)",
    "simülatör çıkışları: STOP/HEDEF signal_lab.simulate gibi (aynı barda ikisi → STOP; ilk bar dışında açılış boşluğu); kanal "
    "çıkışı simulate_rule gibi (k kapanışında tetik → k+1 açılışı); süre sınırında j+H−1 kapanışı; veri bitince son barın "
    "kapanışı: son barın kapanışı ≥ PRICE_END ise DATA_END, değilse DELISTED (seri erken bitti; sansür payına girmez, ayrıca "
    "sayılır)",
    "çıkış anı (kapasite, fonlama doldurma, işaretleme): STOP ve HEDEF isabet barının kapanışı; kural çıkışı çıkış açılışı; süre "
    "sınırı, DATA_END ve DELISTED son barın kapanışı (tam seride PRICE_END); çıkış ayı = çıkış barının open_time ayı",
    "boşluk geçen işlem: giriş barından çıkış barına kadar (giriş barının kendisinden önceki boşluk dahil) bir boşluk; süre = "
    "eksik zaman (açılış farkı − dilim) saat",
    "BAD_BAR: giriş barından çıkış barına kadar (ikisi dahil) bozuk bar geçen işlem hükümden, aylıktan ve kapasiteden atılır; "
    "VERİ BOZUK = hücrenin gerçek işlemleri içinde BAD_BAR payı > %1 (bütün karar penceresi)",
    "sansür payı = hücrenin gerçek işlemleri içinde DATA_END ile kapananların payı, dönem karar anına göre; doğrulamada > %10 → "
    "SANSÜR YÜKSEK",
    "fonlama: futures_lab.funding_carry DEĞİŞMEDEN (DATA_END/DELISTED TIME gibi: son barın kapanışı); NaN doldurma = −0,0001 × "
    "giriş × ⌈tutma saati / 8⌉ / risk, tutma saati = (çıkış anı − giriş barının açılışı) / 1 saat; fonlamalı R = R + funding_r",
    "fonlama NaN payı (§8.3 şart 6) = kapasitenin kabul ettiği ve çıkış ayı doğrulamada olan işlemlerde doldurmadan önceki NaN payı; "
    "BUMP ve NO_BUMP ikisinde de ≤ %5 olmalı (aksi FONLAMA EKSİK)",
    "aylık ölçü: ay = çıkış barının open_time UTC ayı; keşif ayları 2023-01…2024-12 (24), doğrulama 2025-01…2026-09 (21); işlemsiz "
    "ay 0; aylık % = Σ (w × R) × 0,5; ortalama yuvarlanmamış değerle sınanır; ay kümeli aralık: rng = numpy default_rng(20261005), "
    "integers(0, ay, (10000, ay)), satır ortalamalarının np.quantile 0,025 / 0,975'i; keşif aralığı bilgi (aynı tohum)",
    "kapasite: defter 2023-01-01'de boş başlar; karar anları sırayla; t'de önce çıkış anı ≤ t olanlar kapanır; aynı t'de adaylar "
    "_h(sembol, t_ms, kimlik) (kimlik = varyant kimliği) küçükten büyüğe; ret sırası SAME_SYMBOL, SLOTS_FULL, TOTAL_RISK (açık "
    "risk + nominal yeni risk 0,005 × E > E), sonra fit_size; adaylar BAD_BAR olmayan gerçek işlemler",
    "kapasite filtre tablosu: botun FiltersCache JSON'u ('futures' bölümü; anahtar 'BTC/USDT' ya da 'BTCUSDT'); evrenin bir sembolü "
    "tabloda yoksa kapasite FİLTRE BEKLİYOR; PIT kipinde tablo yok: min_notional 0, adım yok, NO_BUMP",
    "kapasite bilgileri: K = 3 ve config sırası duyarlılığı BUMP ile; config sırası = evren listesinin sırası (birincil evrende D4/C4 "
    "config listesi); günlük işaretleme ay sonlarında, açık pozisyon son kapanmış barın kapanışıyla gidiş-dönüş maliyetli, fonlamasız; "
    "eşzamanlılık [karar anı, çıkış anı) aralıklarıyla",
    "hüküm: r_stats (1000 tekrar, gün = t // 86 400 000), vs_placebo = round(gerçek ort.R − plasebo ort.R, 4) (r_stats'ın yuvarlanmış "
    "ortalamalarıyla, aggregate gibi) yalnız plasebo IS ve OOS n ≥ 20 ve gerçek iki dönemde işlem varken; diff_ci iki dönem için "
    "hesaplanıp rapora yazılır, sıkı hüküm verdict_strict ile (yalnız standart GÜÇLÜ ADAY'da etkili)",
    "çoklu test: signal_lab._day_boot_means (gerçek 20261006, plasebo 20261007, 20 000 tekrar, fonlamasız, doğrulama); gün < 5 → "
    "p = 1; p = (1 + #{fark ≤ 0}) / (B + 1); Holm 54 hücrede",
    "aday oranı: fonlamasız; plasebo grubunun VERİ AZ ayrımı verdict(..., vs None) ile; oran = VERİ AZ olmayanlar içinde replicated "
    "ve keşif ort.R > 0 olanların payı (candidate_rate)",
    "bilgi: D4_00'ın laboratuvar eşi = signal_lab.algo_events'in PLACEBO_TREND_DONCHIAN_20_10 LONG olayları, karar penceresinde, "
    "bu simülatörden (DATA_END dahil); temiz pencere uygulanmaz (özgün eş); bağlam dilimleri signal_lab.context kovalarıyla",
    "PIT havuzu: S3 listesi (data/futures/um/monthly/klines/) adları; dışlama PIT_EXCLUDE (kurallar + adlar; adlara göre yapılan "
    "sınıflandırma belirsizlik taşır, seçilen semboller rapora yazılır); sıralama ay başı m'den önceki 30 günde (open_time ∈ "
    "[m − 30 gün, m)) 1d quote_volume toplamı, en az 30 bar, eşitlikte sembol adı; ilk 40",
    "PIT eksik veri: evrendeki (ay, sembol) çifti eksik = o aydaki 4h bar sayısı < 0,90 × 6 × o aydaki 1d bar sayısı (1d bar > 0); "
    "eksik çift payı > %5 ya da liste alınamazsa PIT DOĞRULANAMADI; işlem karar ayının evrenindeyse sayılır",
    "bayt kanıtı: penceredeki her satır '%d,%r,%r,%r,%r,%r\\n' (open_time, açılış, yüksek, düşük, kapanış, hacim; Python repr) "
    "metninin sha256'sı; fonlama dosyası baytlarının sha256'sı",
    "yeniden adlandırma: yalnız bugünkü sembolün arşivi kullanılır, eski sembolün geçmişi birleştirilmez; bilinen durum G/USDT "
    "(Galxe GAL → Gravity G, 2024; GALUSDT geçmişi kullanılmaz, G'nin ilk barı kalite raporunda görünür)",
    "veri hatası → koşu durur, rapor yazılmaz, deneme ERROR; aynı kodla yeniden denenir (§0.6): bir sembolün mum ya da fonlama "
    "dosyası indirilemedi ya da okunamadı; BTC 1d serisi ya da birincil/bilgi evreninde bir coinin 4h serisi (ve yüklendiyse 1d "
    "serisi) PRICE_END'e ulaşmıyor (DELISTED yalnız PIT kipinde olur); PRICE_END'e ulaşan bir sembolün [PRICE_END, 2026-10-02) "
    "aralığında fonlama uzlaşması yok (2026-10 dosyası henüz yayımlanmadı); arşivde hiç verisi olmayan coin dışarıda kalır ve "
    "sonuçta yazılır (§11.6); PIT kipinde 1d serisi defter seçiminden bağımsız yüklenir (eksik çift denetimi) ve yüklenmemiş "
    "seçili sembolün her evren ayı eksik çift sayılır; aynı --out'taki önceki koşunun işlemleriyle birleştirme, yalnız-rapor ve "
    "PIT sonucunun birincil rapora yazılması aynı kod ağacını (git HEAD:tradingbot, HEAD:scripts) ve temiz çalışma ağacını "
    "ister; açık izinle yapılan birleştirme rapora yazılır",
    "PIT dışlama listesi (tam; taban adları, sonuna USDT eklenir): " + "; ".join(f"{k}: {', '.join(v)}" for k, v in PIT_EXCLUDE.items())
    + "; ayrıca alt çizgili her sembol ve USDT ile bitmeyen her sembol",
)

# ---------------------------------------------------------------------------- kayıt ve mühür
_CFG0 = L.LabConfig()


def _registry() -> dict[str, Any]:
    return {
        "version": VERSION,
        "doc": DOC,
        "question_tr": "D4, C4 ve Formasyon defterlerinin bugünkü kuralı ya da önceden yazılmış az sayıda değişikliği maliyet sonrası ve "
                       "defterin kapasitesi içinde ayda en az net +%1 veriyor mu; ana bot arşivden yeniden üretilebilir mi",
        "variants": VARIANTS,
        "c4": {"cv_order": list(CV_ORDER), "base_definition_sha": dict(CV_BASE_SHA), "definition_sha": _c4_registry_shas(),
               "detection_changes": C4_DETECTION_CHANGES, "placebo_p": 0.10,
               "dedup_tr": "tespit anında, tespit anı süzgeçlerinden sonra; öncelik CV sırası; tek yönlü hücre kendi yönünde"},
        "primary_cells": list(PRIMARY_CELLS),
        "control_cells": list(CONTROL_CELLS),
        "filters": {
            "BTC_UP": "open_time + 1 gün ≤ t olan son BTCUSDT 1d barında close > EMA200 (regime_gate.ema_last)",
            "BTC_DOWN": "aynı barda close ≤ EMA200",
            "COIN_UP": "aynı tanım, coinin kendi 1d serisiyle; en az 200 kapanış",
            "REGIME_FOLLOW": "LONG yalnız BTC_UP, SHORT yalnız BTC_DOWN",
            "VOL_OK": "ATR%(i) / atr_med(i) ≤ 1,25 (signal_lab.indicators, tam seri)",
            "VOL_OK_DSL": "DSL bağlamı context.atr_regime [None, 1.25] (500 barlık pencere)",
            "VOL_CONFIRM": "v[i] / önceki 20 bar hacim ortalaması > 1,5",
            "COST_STOP": f"|giriş − stop| / giriş ≥ {COST_STOP_MIN}",
            "signal_filters": list(SIGNAL_FILTERS), "entry_filters": list(ENTRY_FILTERS),
            "unknown_tr": "bilinmeyen değer → giriş yok (fail-closed)"},
        "trade_model": {
            "entry_tr": "karar kapanmış barın kapanışında; giriş sonraki barın açılışında",
            "same_bar_tr": "aynı barda stop ve hedef → STOP", "gap_tr": "boşlukla açılışta açılış fiyatı",
            "rule_exit_tr": "kural k kapanışında tetiklenir, çıkış k+1 açılışında", "time_exit_tr": "süre sınırında son barın kapanışı",
            "data_end_tr": "PRICE_END'e kadar çıkış yoksa son barın kapanışında DATA_END (maliyet dahil); NO_FUTURE_DATA kullanılmaz",
            "overlap_tr": "aynı varyant içinde üst üste binen işlemlere hükümde izin var",
            "cost": {"fee_pct": _CFG0.fee_pct, "slippage_bps": _CFG0.slippage_bps, "round_trip_pct": round(2 * _CFG0.cost_per_side * 100, 4)},
            "funding": {"def_tr": "futures_lab.funding_carry", "fill_rate": FUND_FILL_RATE, "fill_hours": FUND_FILL_HOURS}},
        "placebo": {
            "d4_fm_tr": "u = crc32('<sembol>|<dilim>|<kimlik>|<yön>|<açılış ms>') / 2^32 < p; p dönem başına ham sinyal / uygun bar",
            "d4_stop_tr": "c[j] ∓ (varyantın ATR katı) × ATR14[j]",
            "fm_stop_tr": "c[j] ∓ q × ATR14[j]; q varyantın kendi ham isabetlerinden (i < j)",
            "c4_tr": "candle_lab.variation_events'in PLACEBO_<id> olayları, PLACEBO_P = 0,10, değişmeden; hücre plasebosu birleşim, "
                     "tekilleştirilmez",
            "compare_tr": "gerçek hücre yalnız kendi eşleştirilmiş plasebosuyla", "min_n": MIN_PLACEBO_N},
        "stats": {
            "periods": {"decision_start_ms": DECISION_START_MS, "is_end_ms": IS_END_MS, "price_end_ms": PRICE_END_MS},
            "min_is": _CFG0.min_is, "min_oos": _CFG0.min_oos, "bootstrap_iters": _CFG0.bootstrap_iters,
            "r_stats_seed": 20260925, "strict_seed": L.STRICT_SEED, "strict_min_days": L.STRICT_MIN_DAYS,
            "strict_rule_tr": L.STRICT_RULE_TR, "verdicts": [V_STRONG, V_WEAK, V_LOSS, V_NONE, V_THIN],
            "versions_tr": "hüküm fonlamasız ve fonlamalı R ile ayrı ayrı (gerçek ve plasebo ikisi de fonlamalı)",
            "aggregate_tr": "signal_lab.aggregate KULLANILMAZ; istatistik fonksiyonları hücrenin işlem kümesiyle doğrudan"},
        "monthly": {"risk_pct": RISK_PCT, "risk_pct_info": RISK_PCT_INFO, "target_pct_month": TARGET_PCT_MONTH,
                    "seed": MONTH_SEED, "iters": MONTH_ITERS, "is_months": IS_MONTHS, "oos_months": OOS_MONTHS,
                    "attribution_tr": "çıkış (gerçekleşme) ayı; bilgi: karar ayı ve günlük işaretleme"},
        "capacity": {"books": CAPACITY, "common": CAP_COMMON, "modes": list(CAP_MODES),
                     "start_ms": DECISION_START_MS, "order_tr": "_h(sembol, t_ms, kimlik) küçükten büyüğe",
                     "rejects": ["SAME_SYMBOL", "SLOTS_FULL", "TOTAL_RISK", "LIQ_BUFFER_TOO_THIN", "MIN_ORDER_CONFLICT",
                                 "INSUFFICIENT_MARGIN"],
                     "w_tr": "w = fit.risk_usdt / (0,005 × E)"},
        "target": {"conditions_tr": [
            "1 sıkı hüküm GÜÇLÜ ADAY, fonlamasız ve fonlamalı",
            "2 kapasiteli doğrulama ortalama aylık net ≥ +1,0, fonlamalı ve fonlamasız (yuvarlanmamış)",
            "3 kapasiteli fonlamalı doğrulama ay kümeli %95 aralığının alt ucu > 0",
            "4 kapasiteli fonlamalı keşif aylık ortalaması > 0",
            "5 2–4 BUMP ve NO_BUMP'ın kötü olanında; filtre tablosu yoksa FİLTRE BEKLİYOR",
            "6 kapasitenin kabul ettiği doğrulama işlemlerinde NaN fonlama payı ≤ %5 (aksi FONLAMA EKSİK)",
            "7 SANSÜR YÜKSEK (doğrulama DATA_END > %10) ya da VERİ BOZUK (BAD_BAR > %1) değil",
            "8 havuzlu hücrede iki yönden hiçbirinin hükmü (fonlamasız ve fonlamalı) KAYBETTİRİR değil"],
            "censor_max": CENSOR_MAX, "bad_max": BAD_MAX, "nan_funding_max": NAN_FUND_MAX},
        "multiple": {"cells": len(PRIMARY_CELLS), "iters": MT_ITERS, "seeds": [MT_SEED_REAL, MT_SEED_PLACEBO], "alpha": HOLM_ALPHA,
                     "method_tr": "Holm adım adım; reddedilen hücre ÇOKLU TESTİ GEÇTİ",
                     "chance_tr": "gerçek aday oranı ≤ plasebo aday oranı → geçen her hücre TESADÜFLE AÇIKLANABİLİR"},
        "steps": {"tr": ["HEDEFİ KARŞILIYOR", "ÇOKLU TESTİ GEÇTİ ve TESADÜFLE AÇIKLANABİLİR değil",
                         "taban (D4_00, C4_00, FM_00) ya da veri gözetlemeli (C4_07) değil", "PIT teyidi geçti"],
                  "base": list(BASE_VARIANTS), "snooped": list(SNOOPED_VARIANTS),
                  "pit_pass_tr": ["(a) PIT kapasiteli doğrulama aylık ≥ +1,0 fonlamalı ve fonlamasız (NO_BUMP, min-notional 0)",
                                  "(b) fonlamalı doğrulama (gerçek − plasebo) nokta tahmini > 0",
                                  "(c) standart hüküm (fonlamasız ve fonlamalı) KAYBETTİRİR değil"]},
        "data": {"source": "data.binance.vision USDⓈ-M (signal_lab.ArchiveProvider); fonlama futures_data.FUNDING_URL (yalnız aylık)",
                 "start_4h_ms": START_4H_MS, "start_1d_ms": START_1D_MS, "price_end_ms": PRICE_END_MS,
                 "funding": [FUNDING_START_MS, FUNDING_END_MS], "clean_bars": {"D4": CLEAN_D4, "FM": CLEAN_FM, "regime_1d": CLEAN_REGIME},
                 "d4_min_i": D4_MIN_I},
        "universes": {"primary": list(PRIMARY_UNIVERSE), "info": list(INFO_UNIVERSE),
                      "pit": {"top": PIT_TOP, "days": PIT_DAYS, "min_bars": PIT_MIN_BARS, "missing_max": PIT_MISSING_MAX,
                              "pair_min_cover": PIT_PAIR_MIN_COVER, "months": [PIT_MONTHS[0], PIT_MONTHS[-1]], "exclude": PIT_EXCLUDE,
                              "list_url": PIT_LIST_URL,
                              "capacity_tr": "NO_BUMP, min-notional 0; listeden çıkarılan sembolde açık pozisyon son barın kapanışında"}},
        "main_bot": {"replayable": False, "proxy": False,
                     "tr": "ana bot arşivden sadakatle yeniden oynatılamaz (coin başkanı, öğrenilmiş p_win, ekonomik kapı, hafıza, "
                           "canlı anlık görüntü); proxy kurulmaz; VPS betiği scripts/main_book_audit.py (main_book_audit_v1) sonra",
                     "q1": {"window": "belgenin commit anı → 2027-01-01 00:00 UTC", "rule_tr": "n ≥ 30 ve aralık alt ucu > 0 ve aylık ≥ +%1",
                            "second_look": "2027-04-01 00:00 UTC (yalnız VERİ AZ ise; deneme sayılır)"},
                     "q2_q5_tr": "seçicilik, olasılık, yön/rejim, kapasite — açıklayıcı, deneme sayılmaz"},
        "outcome_tr": {"none": NO_TARGET_TR + " (ya da hangi basamakta elendikleri); defterler değişmez",
                       "some": "yalnız PAPER, yalnız-kayıt öneri; sahip onayı olmadan hiçbir şey açılmaz, config değişmez"},
        "scope_tr": ["hiçbir defter, strateji, ajan, config ya da çalışma zamanı değişmez; VPS'e hiçbir şey gitmez",
                     "T2, M2, Box ve altın (XAUUSDT/PAXG) bu çalışmada yok", "ana botun kendisi sınanmaz (§9)"],
        "trials": {"version": VERSION, "variants": 32, "primary_cells": len(PRIMARY_CELLS), "control_cells": len(CONTROL_CELLS),
                   "tr": "book_v1: 32 varyant, 54 birincil hücre (D4 13/15, C4 9/27, Formasyon 10/12) + 1 kontrol + ana bot Q1"},
        "readings_tr": list(READINGS_TR),
    }


BOOK_REGISTRY: dict[str, Any] = json.loads(json.dumps(_registry(), ensure_ascii=False))
BOOK_REGISTRY_SHA = hashlib.sha256(json.dumps(BOOK_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class BookDataError(RuntimeError):
    """Gerekli seri yok ya da okunamadı: rapor eksik veriyle SESSİZCE üretilmez."""


# ---------------------------------------------------------------------------- yardımcılar
def month_key(ms: int) -> str:
    d = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def month_start_ms(key: str) -> int:
    return int(datetime(int(key[:4]), int(key[5:]), 1, tzinfo=timezone.utc).timestamp() * 1000)


def _iso(ms: Any) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def period_of(t_ms: int) -> str | None:
    t = int(t_ms)
    if DECISION_START_MS <= t < IS_END_MS:
        return "IS"
    if IS_END_MS <= t < PRICE_END_MS:
        return "OOS"
    return None


def bot_symbol(archive_sym: str) -> str:
    s = str(archive_sym)
    return s[:-4] + "/USDT" if s.endswith("USDT") else s


def archive_symbol(sym: str) -> str:
    return str(sym).split(":")[0].replace("/", "").upper()


def bad_bars(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Bozuk bar (`candle_lab.valid_ends` tanımı)."""
    with np.errstate(invalid="ignore"):
        return (~(np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c)) | (h < np.maximum(o, c))
                | (lo > np.minimum(o, c)) | ~np.isfinite(v) | (v < 0))


def clean_ends(ts: np.ndarray, o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, v: np.ndarray, step: int,
               w: int) -> np.ndarray:
    """ok[i]: i'de biten w barlık pencere bozuk barsız ve boşluksuz (`candle_lab.valid_ends` ile aynı kural, w seçilebilir)."""
    n = len(ts)
    ok = np.zeros(n, dtype=bool)
    if n < w or w < 1:
        return ok
    bad = bad_bars(o, h, lo, c, v)
    gap = np.zeros(n, dtype=bool)
    gap[1:] = np.diff(np.asarray(ts, dtype=np.int64)) != int(step)
    cb = np.concatenate(([0], np.cumsum(bad)))
    cg = np.concatenate(([0], np.cumsum(gap)))
    end = np.arange(w - 1, n)
    ok[w - 1:] = ((cb[end + 1] - cb[end - w + 1]) == 0) & ((cg[end + 1] - cg[end - w + 2]) == 0)
    return ok


def channel(x: np.ndarray, n: int, kind: str) -> np.ndarray:
    """Önceki n barın en yükseği / en düşüğü, i barı hariç (`signal_lab.aux_series` kuruluşu)."""
    s = pd.Series(np.asarray(x, dtype=float))
    r = s.rolling(n).max() if kind == "hi" else s.rolling(n).min()
    return r.shift(1).to_numpy()


def regime_values(closes: Iterable[float]) -> list[str | None]:
    """Her k için `regime_gate.btc_regime(ilk k+1 bar)`ın sonucu ("UP" / "DOWN" / None), O(n). EMA200
    `regime_gate.ema_last` ile aynı aritmetik: ilk 200 SONLU kapanışın toplamı / 200 ile tohum, sonra v·α + e·(1 − α)."""
    from . import regime_gate as RG
    n_ema = RG.EMA_LEN
    alpha = 2.0 / (n_ema + 1.0)
    out: list[str | None] = []
    seed: list[float] = []
    e: float | None = None
    for x in closes:
        v = RG._f(x)
        if v is not None:
            if e is None:
                seed.append(v)
                if len(seed) == n_ema:
                    e = sum(seed) / n_ema
            else:
                e = v * alpha + e * (1.0 - alpha)
        if v is None or e is None:
            out.append(None)
        else:
            out.append(RG.UP if v > e else RG.DOWN)
    return out


def regime_daily(df1: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """1d seri → (açılış zamanları, +1 UP / −1 DOWN / 0 bilinmiyor). Bilinmiyor: EMA yok, kapanış sonlu değil ya da k'de biten
    200 barlık 1d pencere temiz değil."""
    ts = df1["timestamp"].to_numpy(dtype=np.int64)
    arr = {k: df1[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    vals = regime_values(arr["close"])
    clean = clean_ends(ts, arr["open"], arr["high"], arr["low"], arr["close"], arr["volume"], DAY_MS, CLEAN_REGIME)
    reg = np.array([0 if (v is None or not clean[k]) else (1 if v == "UP" else -1) for k, v in enumerate(vals)], dtype=np.int8)
    return ts, reg


def regime_at(t_ms: np.ndarray, d_ts: np.ndarray, reg: np.ndarray) -> np.ndarray:
    """Karar anı t'de kullanılan rejim: open_time + 1 gün ≤ t olan son 1d barınki; o bar t'den önceki son 24 saatte
    kapanmadıysa (1d seride t'ye değen boşluk) ya da yoksa 0 (bilinmiyor)."""
    t = np.asarray(t_ms, dtype=np.int64)
    if len(d_ts) == 0:
        return np.zeros(len(t), dtype=np.int8)
    close = np.asarray(d_ts, dtype=np.int64) + DAY_MS
    k = np.searchsorted(close, t, side="right") - 1
    kk = np.maximum(k, 0)
    fresh = (k >= 0) & (close[kk] > t - DAY_MS)
    return np.where(fresh, np.asarray(reg)[kk], 0).astype(np.int8)


class Series:
    """Bir dilimdeki seri: diziler, laboratuvar göstergeleri, kanallar, bozuk bar/boşluk ve temiz pencere maskeleri."""

    def __init__(self, df: pd.DataFrame, tf: str):
        self.df = df.reset_index(drop=True)
        self.tf, self.step = tf, tf_ms(tf)
        self.ts = self.df["timestamp"].to_numpy(dtype=np.int64)
        self.arr = {k: self.df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
        self.o, self.h, self.l, self.c, self.v = (self.arr[k] for k in ("open", "high", "low", "close", "volume"))
        self.n = len(self.ts)
        self.ind = L.indicators(self.df) if self.n else {}
        self.aux = L.aux_series(self.df) if self.n else {}
        self.bad = bad_bars(self.o, self.h, self.l, self.c, self.v)
        self.gap = np.zeros(self.n, dtype=bool)
        if self.n > 1:
            self.gap[1:] = np.diff(self.ts) != self.step
        self.t = self.ts + self.step
        self._clean: dict[int, np.ndarray] = {}
        self._chan: dict[str, np.ndarray] = {}
        self._ctx: list[dict[str, str]] | None = None
        with np.errstate(invalid="ignore"):
            a = self.ind.get("atr", np.zeros(0))
            self.atr_ok = np.isfinite(a) & (a > 0)

    def clean(self, w: int) -> np.ndarray:
        if w not in self._clean:
            self._clean[w] = clean_ends(self.ts, self.o, self.h, self.l, self.c, self.v, self.step, w)
        return self._clean[w]

    def chan(self, name: str) -> np.ndarray:
        if name not in self._chan:
            if name in self.aux:
                self._chan[name] = self.aux[name]
            else:
                kind, n = name[:2], int(name[2:])
                self._chan[name] = channel(self.h if kind == "hi" else self.l, n, kind)
        return self._chan[name]

    def ctx(self, i: int, side: str = LONG) -> dict[str, str]:
        return L.context(self.ind, self.arr, int(i), side)

    def vol_masks(self) -> tuple[np.ndarray, np.ndarray]:
        """(VOL_OK, VOL_CONFIRM) — `signal_lab.context`'in volatilite ve hacim kovaları (yönden bağımsız)."""
        if self._ctx is None:
            self._ctx = [self.ctx(i) for i in range(self.n)]
        vok = np.array([x["volatilite"] in ("düşük", "normal") for x in self._ctx], dtype=bool)
        vcf = np.array([x["hacim"] == "yüksek(>1.5x)" for x in self._ctx], dtype=bool)
        return vok, vcf


def fresh_breakout_mask(close: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """D4 kırılımı, defterin KENDİ fonksiyonuyla (`donchian_trend._fresh_breakout`): c[i] > ref[i] ve c[i−1] ≤ ref[i−1]."""
    from . import donchian_trend as DT
    s = {"close": np.asarray(close, dtype=float), "hi20": np.asarray(ref, dtype=float)}
    return np.array([DT._fresh_breakout(s, i) for i in range(len(s["close"]))], dtype=bool)


def filter_mask(names: Iterable[str], side: str, masks: Mapping[str, np.ndarray], n: int) -> np.ndarray:
    """Tespit anı süzgeçlerinin VE'si (COST_STOP girişte denetlenir, burada yok sayılır)."""
    out = np.ones(n, dtype=bool)
    for f in names:
        if f in ENTRY_FILTERS:
            continue
        key = ("BTC_UP" if side == LONG else "BTC_DOWN") if f == "REGIME_FOLLOW" else f
        out &= masks[key]
    return out


# ---------------------------------------------------------------------------- simülatör (§5.1)
def simulate_trade(S: Series, i: int, side: str, stop: float, exit_spec: Mapping[str, Any], risk_atr: tuple[float, float],
                   atr_i: float, cfg: L.LabConfig, *, trigger: float | None = None, chase_atr: float | None = None,
                   cost_stop: bool = False) -> tuple[dict[str, Any] | None, str]:
    """Sonraki açılışta giriş; çıkışlar `signal_lab.simulate` (hedef) / `simulate_rule` (kanal) kurallarıyla; veri bitince son
    barın kapanışında DATA_END (ya da seri erken bittiyse DELISTED). Döner: (işlem, "") ya da (None | BAD_BAR işlemi, neden)."""
    o, h, lo, c, ts, n, step = S.o, S.h, S.l, S.c, S.ts, S.n, S.step
    j = int(i) + 1
    if j >= n:
        return None, "NO_ENTRY_BAR"
    s = 1.0 if side == LONG else -1.0
    entry, stop, a = float(o[j]), float(stop), float(atr_i)
    if not a or math.isnan(a):
        return None, "NO_ATR"
    risk = s * (entry - stop)
    if risk <= float(risk_atr[0]) * a:
        return None, "STOP_TOO_CLOSE"
    if risk > float(risk_atr[1]) * a:
        return None, "STOP_TOO_FAR"
    if trigger is not None and chase_atr is not None and s * (entry - float(trigger)) > float(chase_atr) * a:
        return None, "CHASE"
    if cost_stop and abs(entry - stop) / entry < COST_STOP_MIN:
        return None, "STOP_TOO_TIGHT_FOR_COST"
    H = int(exit_spec["max_bars"])
    end_reason = "DATA_END" if int(ts[n - 1]) + step >= PRICE_END_MS else "DELISTED"
    exit_px: float | None = None
    reason, xb = "", j
    if exit_spec["kind"] == "target":
        target = entry + s * float(exit_spec["rr"]) * risk
        for k in range(j, min(n, j + H)):
            if s > 0:
                if lo[k] <= stop:
                    exit_px, reason = (min(o[k], stop) if k > j else stop), "STOP"
                elif h[k] >= target:
                    exit_px, reason = (max(o[k], target) if k > j else target), "TARGET"
            else:
                if h[k] >= stop:
                    exit_px, reason = (max(o[k], stop) if k > j else stop), "STOP"
                elif lo[k] <= target:
                    exit_px, reason = (min(o[k], target) if k > j else target), "TARGET"
            if exit_px is not None:
                xb = k
                break
    elif exit_spec["kind"] == "channel":
        ref = S.chan(exit_spec["ref"][side])
        for k in range(j, min(n, j + H)):
            if (s > 0 and lo[k] <= stop) or (s < 0 and h[k] >= stop):
                exit_px, reason, xb = ((min(o[k], stop) if s > 0 else max(o[k], stop)) if k > j else stop), "STOP", k
                break
            r_ = ref[k]
            if r_ == r_ and ((c[k] < r_) if s > 0 else (c[k] > r_)):
                if k + 1 >= n:
                    exit_px, reason, xb = float(c[k]), end_reason, k
                else:
                    exit_px, reason, xb = float(o[k + 1]), "RULE", k + 1
                break
    else:
        raise ValueError(f"bilinmeyen çıkış: {exit_spec['kind']}")
    if exit_px is None:
        if j + H <= n:
            exit_px, reason, xb = float(c[j + H - 1]), "TIME", j + H - 1
        else:
            exit_px, reason, xb = float(c[n - 1]), end_reason, n - 1
    exit_px = float(exit_px)
    cost = (entry + exit_px) * cfg.cost_per_side
    exit_ms = int(ts[xb]) if reason == "RULE" else int(ts[xb]) + step
    gaps = [int(ts[m] - ts[m - 1] - step) for m in range(max(j, 1), xb + 1) if S.gap[m]]
    tr = {"i": int(i), "j": j, "xb": int(xb), "entry": entry, "stop": stop, "risk": risk, "exit_px": exit_px, "reason": reason,
          "r": (s * (exit_px - entry) - cost) / risk, "cost_r": cost / risk, "hold": int(xb - j + 1),
          "entry_ms": int(ts[j]), "exit_ms": exit_ms, "exit_bar_ms": int(ts[xb]), "gap_cross": bool(gaps),
          "gap_hours": round(max(gaps) / HOUR_MS, 2) if gaps else 0.0}
    if bool(S.bad[j:xb + 1].any()):
        return tr, "BAD_BAR"
    return tr, ""


def mtm_marks(S: Series, tr: Mapping[str, Any], side: str, cfg: L.LabConfig) -> dict[str, float]:
    """Bilgi: işlemin açık olduğu her ay sonunda (giriş ≤ ay sonu < çıkış) son kapanmış barın kapanışıyla gidiş-dönüş maliyetli R."""
    out: dict[str, float] = {}
    s = 1.0 if side == LONG else -1.0
    e0, e1 = int(tr["entry_ms"]), int(tr["exit_ms"])
    mk = month_key(e0)
    while True:
        y, m = int(mk[:4]), int(mk[5:])
        nxt = f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}"
        me = month_start_ms(nxt)
        if me >= e1:
            break
        if me >= e0:
            kk = int(np.searchsorted(S.ts + S.step, me, side="right")) - 1
            px = float(S.c[kk]) if kk >= int(tr["j"]) else float(tr["entry"])
            cost = (float(tr["entry"]) + px) * cfg.cost_per_side
            out[mk] = (s * (px - float(tr["entry"])) - cost) / float(tr["risk"])
        mk = nxt
    return out


# ---------------------------------------------------------------------------- olay üreticileri
def _masks(S: Series, btc_reg: np.ndarray | None, coin_reg: np.ndarray | None) -> dict[str, np.ndarray]:
    vok, vcf = S.vol_masks()
    b = btc_reg if btc_reg is not None else np.zeros(S.n, dtype=np.int8)
    cr = coin_reg if coin_reg is not None else np.zeros(S.n, dtype=np.int8)
    return {"BTC_UP": b == 1, "BTC_DOWN": b == -1, "COIN_UP": cr == 1, "VOL_OK": vok, "VOL_CONFIRM": vcf}


def d4_raw(S: Series, spec: Mapping[str, Any], masks: Mapping[str, np.ndarray], side: str) -> tuple[np.ndarray, np.ndarray, int]:
    """(ham sinyal indeksleri, uygun bar maskesi, temiz pencere yüzünden düşen tespit sayısı)."""
    n = S.n
    idx_ok = np.arange(n) >= int(spec["min_i"])
    clean = S.clean(int(spec["clean_bars"]))
    base = S.atr_ok & idx_ok
    ref = S.chan(spec["entry_ref"][side])
    sig = fresh_breakout_mask(S.c, ref) if side == LONG else fresh_breakout_mask(-S.c, -ref)
    det = filter_mask(spec["filters"], side, masks, n)
    raw = np.flatnonzero(sig & base & clean & det)
    unclean = int((sig & base & ~clean).sum())
    elig = base & clean & filter_mask(spec["placebo_filters"], side, masks, n)
    return raw, elig, unclean


def fm_records(S: Series, symbol: str) -> list[L.Event]:
    """Ortak katalog kayıtları, adım 1 (canlı Formasyon her kapanmış barda değerlendirir)."""
    if S.n < L.LabConfig().window:
        return []
    cfg = dataclasses.replace(L.LabConfig(), stride=1)
    names = {(_FM_SIGNALS[s]["name"], s) for s in SIDES}
    return [e for e in L.catalog_events(S.df, symbol, S.tf, cfg) if (e.name, e.side) in names]


def fm_raw(S: Series, recs: list[L.Event], spec: Mapping[str, Any], masks: Mapping[str, np.ndarray],
           side: str) -> tuple[list[L.Event], np.ndarray, int]:
    """(ham isabet olayları i sırasıyla, uygun bar maskesi, temiz pencere yüzünden düşen kayıt sayısı)."""
    sg = spec["signals"][side]
    clean = S.clean(int(spec["clean_bars"]))
    det = filter_mask(spec["filters"], side, masks, S.n)
    rsi = S.ind["rsi"]
    out, unclean = [], 0
    for e in recs:
        if e.name != sg["name"] or e.side != side or e.trigger is None:
            continue
        i = int(e.i)
        if not S.atr_ok[i]:
            continue
        if not clean[i]:
            unclean += 1
            continue
        if spec["rsi_filter"]:
            r = float(rsi[i])
            if not ((r > sg["rsi"]) if sg["rsi_op"] == ">" else (r < sg["rsi"])):
                continue
        if not det[i]:
            continue
        out.append(e)
    out.sort(key=lambda e: e.i)
    elig = S.atr_ok & clean & filter_mask(spec["placebo_filters"], side, masks, S.n)
    return out, elig, unclean


def _placebo_bars(S: Series, symbol: str, vid: str, side: str, raw_i: Iterable[int], elig: np.ndarray) -> tuple[list[int], dict]:
    """D4/Formasyon plasebo çekilişi: dönem başına p = ham / uygun; u = _h(sembol, dilim, kimlik, yön, ts[j]) < p."""
    per = np.array([period_of(int(t)) or "" for t in S.t])
    raw_mask = np.zeros(S.n, dtype=bool)
    raw_mask[list(raw_i)] = True
    info: dict[str, Any] = {"raw": {}, "eligible": {}, "p": {}}
    for p_ in ("IS", "OOS"):
        m = per == p_
        nr, ne = int((raw_mask & m).sum()), int((elig & m).sum())
        info["raw"][p_], info["eligible"][p_] = nr, ne
        info["p"][p_] = nr / ne if ne else 0.0
    info["raw"]["warmup"] = int((raw_mask & (per == "")).sum())
    bars = []
    for j in np.flatnonzero(elig & (per != "")):
        pj = info["p"][per[j]]
        if pj > 0 and L._h(symbol, S.tf, vid, side, int(S.ts[j])) < pj:
            bars.append(int(j))
    return bars, info


def _row(symbol: str, S: Series, book: str, vid: str, kind: str, side: str, name: str, tr: Mapping[str, Any], cfg: L.LabConfig,
         scopes: Iterable[str] = ()) -> dict[str, Any]:
    t = int(S.ts[tr["i"]]) + S.step
    row = {"symbol": symbol, "tf": S.tf, "book": book, "variant": vid, "kind": kind, "side": side, "name": name,
           "i": int(tr["i"]), "t_ms": t, "period": period_of(t), **{k: tr[k] for k in (
               "entry", "stop", "risk", "exit_px", "reason", "r", "cost_r", "hold", "entry_ms", "exit_ms", "exit_bar_ms",
               "gap_cross", "gap_hours")}, "scopes": ",".join(scopes)}
    row["ctx"] = S.ctx(tr["i"], side)
    row["mtm"] = mtm_marks(S, tr, side, cfg) if month_key(tr["entry_ms"]) != month_key(tr["exit_ms"] - 1) else {}
    return row


def _bump(d: dict, key: str, by: int = 1) -> None:
    d[key] = d.get(key, 0) + by


def _sim_rows(S: Series, symbol: str, book: str, vid: str, kind: str, side: str, name: str, items: list[tuple],
              spec_exit: Mapping[str, Any], risk_atr: tuple[float, float], cfg: L.LabConfig, cost_stop: bool,
              chase_atr: float | None, counts: dict, scopes_of: Callable[[tuple], list[str]] | None = None) -> list[dict]:
    """items: (i, stop, tetik | None, atr_i[, anahtar]) — simüle edip satır üretir; atlama nedenleri `counts`a."""
    rows = []
    for it in items:
        i, stop, trig, a = it[0], it[1], it[2], it[3]
        if period_of(int(S.ts[i]) + S.step) is None:
            continue
        _bump(counts, "in_window")
        tr, why = simulate_trade(S, i, side, stop, spec_exit, risk_atr, a, cfg, trigger=trig, chase_atr=chase_atr,
                                 cost_stop=cost_stop)
        sc = scopes_of(it) if scopes_of is not None else []
        if why and why != "BAD_BAR":
            _bump(counts.setdefault("skipped", {}), why)
            continue
        if why == "BAD_BAR":
            _bump(counts.setdefault("skipped", {}), "BAD_BAR")
        row = _row(symbol, S, book, vid, kind, side, name, tr, cfg, sc)
        if why == "BAD_BAR":
            row["reason"], row["r"] = "BAD_BAR", float("nan")
        rows.append(row)
        _bump(counts, "trades" if not why else "bad_bar")
    return rows


def d4_series_rows(symbol: str, S: Series, masks: Mapping[str, np.ndarray], cfg: L.LabConfig,
                   variants: Iterable[str]) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    meta: dict[str, Any] = {}
    for vid in variants:
        spec = D4_VARIANTS[vid]
        if spec["tf"] != S.tf:
            continue
        cost_stop = "COST_STOP" in spec["filters"]
        for side in spec["sides"]:
            raw, elig, unclean = d4_raw(S, spec, masks, side)
            a = S.ind["atr"]
            real_cnt: dict[str, Any] = {"raw_all": int(len(raw)), "unclean_window": unclean}
            items = [(int(i), float(S.c[i] - (1 if side == LONG else -1) * spec["stop_atr"] * a[i]), None, float(a[i])) for i in raw]
            rows += _sim_rows(S, symbol, "D4", vid, "real", side, "DONCHIAN", items, spec["exit"], tuple(spec["risk_atr"]), cfg,
                              cost_stop, None, real_cnt)
            bars, pinfo = _placebo_bars(S, symbol, vid, side, raw, elig)
            pl_cnt: dict[str, Any] = {"drawn": len(bars)}
            items = [(j, float(S.c[j] - (1 if side == LONG else -1) * spec["stop_atr"] * a[j]), None, float(a[j])) for j in bars]
            rows += _sim_rows(S, symbol, "D4", vid, "placebo", side, "PLACEBO_DONCHIAN", items, spec["exit"],
                              tuple(spec["risk_atr"]), cfg, cost_stop, None, pl_cnt)
            meta[f"{vid}|{side}"] = {"real": real_cnt, "placebo": {**pl_cnt, **pinfo}}
    return rows, meta


def d4_lab_placebo_rows(symbol: str, S: Series, cfg: L.LabConfig) -> list[dict]:
    """Bilgi: D4_00'ın laboratuvar eşi (algo_events PLACEBO_TREND_DONCHIAN_20_10, LONG), karar penceresinde."""
    if S.tf != TF4 or S.n < D4_MIN_I + 2:
        return []
    evs = [e for e in L.algo_events(S.df, symbol, S.tf, S.ind["atr"], S.aux)
           if e.name == "PLACEBO_TREND_DONCHIAN_20_10" and e.side == LONG]
    spec = D4_VARIANTS["D4_00_BASE"]
    items = [(int(e.i), float(e.stop), None, float(S.ind["atr"][e.i])) for e in evs]
    return _sim_rows(S, symbol, "D4", "D4_00_BASE", "labplacebo", LONG, "PLACEBO_TREND_DONCHIAN_20_10", items, spec["exit"],
                     tuple(spec["risk_atr"]), cfg, False, None, {})


def fm_series_rows(symbol: str, S: Series, masks: Mapping[str, np.ndarray], cfg: L.LabConfig, variants: Iterable[str],
                   recs: list[L.Event] | None = None) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    meta: dict[str, Any] = {}
    recs = fm_records(S, symbol) if recs is None else recs
    a = S.ind["atr"]
    for vid in variants:
        spec = FM_VARIANTS[vid]
        cost_stop = "COST_STOP" in spec["filters"]
        risk = tuple(spec["risk_atr"])
        for side in spec["sides"]:
            s = 1.0 if side == LONG else -1.0
            hits, elig, unclean = fm_raw(S, recs, spec, masks, side)
            real_cnt: dict[str, Any] = {"raw_all": len(hits), "unclean_window": unclean}
            items = [(int(e.i), float(e.stop), e.trigger, float(a[e.i])) for e in hits]
            rows += _sim_rows(S, symbol, "FM", vid, "real", side, spec["signals"][side]["name"], items, spec["exit"], risk, cfg,
                              cost_stop, spec["chase_atr"], real_cnt)
            pool_i = [int(e.i) for e in hits]
            pool_q = [s * (float(S.c[e.i]) - float(e.stop)) / float(a[e.i]) for e in hits]
            bars, pinfo = _placebo_bars(S, symbol, vid, side, pool_i, elig)
            pl_cnt: dict[str, Any] = {"drawn": len(bars), "skipped": {}}
            items = []
            for j in bars:
                k = bisect_left(pool_i, j)                        # pool_i[:k] < j
                if k == 0:
                    _bump(pl_cnt["skipped"], "NO_REAL_RISK")
                    continue
                q = pool_q[int(L._h(symbol, S.tf, vid, "q", int(S.ts[j])) * k)]
                stop = float(S.c[j]) - s * q * float(a[j])
                if not (math.isfinite(stop) and stop > 0 and s * (float(S.c[j]) - stop) > 0):
                    _bump(pl_cnt["skipped"], "BAD_STOP")
                    continue
                items.append((j, stop, float(S.c[j]), float(a[j])))
            rows += _sim_rows(S, symbol, "FM", vid, "placebo", side, "PLACEBO_" + spec["signals"][side]["name"], items, spec["exit"],
                              risk, cfg, cost_stop, spec["chase_atr"], pl_cnt)
            meta[f"{vid}|{side}"] = {"real": real_cnt, "placebo": {**pl_cnt, **pinfo}}
    return rows, meta


def c4_detect(S: Series, symbol: str, keys: Iterable[str]) -> dict[str, dict[str, tuple[list[L.Event], np.ndarray, dict]]]:
    """Tespit kümesi başına her CV için `candle_lab.variation_events` (gerçek + PLACEBO_<id>)."""
    from . import candle_lab as CL
    out: dict[str, dict[str, tuple]] = {}
    for key in keys:
        out[key] = {}
        for cv in CV_ORDER:
            sk: dict[str, int] = {}
            evs, atr_sig = CL.variation_events(S.df, symbol, S.tf, c4_variation(cv, C4_DETECTION_CHANGES[key]), skipped=sk)
            out[key][cv] = (evs, atr_sig, sk)
    return out


def c4_dedup(real: Mapping[str, Mapping[int, Any]], cvs: Iterable[str], scope: str) -> tuple[set[tuple[str, int]], int]:
    """Tekilleştirme (§6.2, `candle_book.decide`): her karar barında hücrenin CV'lerinden (BOTH → hepsi, LONG/SHORT → o yönün
    CV'leri) eşleşenlerin EN ÖNCELİKLİSİ (CV sırası). Döner: ({(cv, i)}, bastırılan eşleşme sayısı)."""
    scope_cvs = [cv for cv in cvs if scope == "BOTH" or c4_variation(cv, None).side == scope]
    win: set[tuple[str, int]] = set()
    sup = 0
    for i in sorted({i for cv in scope_cvs for i in real.get(cv, {})}):
        hit = [cv for cv in scope_cvs if i in real.get(cv, {})]
        win.add((hit[0], i))
        sup += len(hit) - 1
    return win, sup


def c4_series_rows(symbol: str, S: Series, masks: Mapping[str, np.ndarray], cfg: L.LabConfig, variants: Iterable[str],
                   det: Mapping[str, Mapping[str, tuple]] | None = None) -> tuple[list[dict], dict]:
    from . import candle_lab as CL
    variants = [v for v in variants if v in C4_VARIANTS]
    if det is None:
        det = c4_detect(S, symbol, sorted({C4_VARIANTS[v]["detection"] for v in variants}))
    rows: list[dict] = []
    meta: dict[str, Any] = {}
    for vid in variants:
        spec = C4_VARIANTS[vid]
        cost_stop = "COST_STOP" in spec["filters"]
        cvs = list(spec["cvs"])
        real: dict[str, dict[int, L.Event]] = {}
        atrs: dict[str, np.ndarray] = {}
        vmeta: dict[str, Any] = {}
        for cv in cvs:
            evs, atr_sig, sk = det[spec["detection"]][cv]
            var = c4_variation(cv, spec["dsl_change"])
            side = var.side
            dm = filter_mask(spec["filters"], side, masks, S.n)
            atrs[cv] = atr_sig
            real[cv] = {int(e.i): e for e in evs if e.family == CL.FAMILY and dm[e.i]}
            pl = [e for e in evs if e.family == "placebo" and dm[e.i]]
            vc = CL.vcfg(cfg, var)
            exit_spec = {"kind": "target", "rr": float(var.target_r), "max_bars": int(var.max_hold_bars)}
            pl_cnt: dict[str, Any] = {"drawn": len(pl), "produced_all": sum(1 for e in evs if e.family == "placebo"),
                                      "variation_events_skipped": dict(sk)}
            items = [(int(e.i), float(e.stop), None, float(atr_sig[e.i])) for e in pl]
            rows += _sim_rows(S, symbol, "C4", vid, "placebo", side, "PLACEBO_" + cv, items, exit_spec,
                              (vc.min_risk_atr, vc.max_risk_atr), cfg, cost_stop, None, pl_cnt)
            vmeta[cv] = {"placebo": pl_cnt, "detected": len(real[cv])}
        winners: dict[str, set[tuple[str, int]]] = {}
        suppressed: dict[str, int] = {}
        for scope in spec["cells"]:
            winners[scope], suppressed[scope] = c4_dedup(real, cvs, scope)
        for cv in cvs:
            var = c4_variation(cv, spec["dsl_change"])
            vc = CL.vcfg(cfg, var)
            exit_spec = {"kind": "target", "rr": float(var.target_r), "max_bars": int(var.max_hold_bars)}
            items = []
            for i, e in sorted(real[cv].items()):
                sc = [scope for scope in spec["cells"] if (cv, i) in winners[scope]]
                if sc:
                    items.append((i, float(e.stop), None, float(atrs[cv][i]), tuple(sc)))
            cnt: dict[str, Any] = {}
            rows += _sim_rows(S, symbol, "C4", vid, "real", var.side, cv, items, exit_spec, (vc.min_risk_atr, vc.max_risk_atr), cfg,
                              cost_stop, None, cnt, scopes_of=lambda it: list(it[4]))
            vmeta[cv]["real"] = cnt
        meta[vid] = {"cvs": vmeta, "suppressed": suppressed}
    return rows, meta


# ---------------------------------------------------------------------------- fonlama
def apply_funding(rows: list[dict], S: Series, raw: Mapping[str, Any] | None) -> None:
    """`futures_lab.funding_carry` DEĞİŞMEDEN (DATA_END/DELISTED TIME gibi); NaN tutucu doldurulur (§5.2)."""
    from . import futures_lab as FL
    evs = []
    for k, r in enumerate(rows):
        if r["reason"] == "BAD_BAR":
            continue
        ev = L.Event(r["symbol"], r["tf"], "book", "T", r["side"], int(r["i"]), int(r["t_ms"]), float(r["stop"]),
                     exit_reason="TIME" if r["reason"] in ("TIME", "DATA_END", "DELISTED") else r["reason"], hold=int(r["hold"]))
        evs.append((k, ev))
    if evs:
        FL.funding_carry([e for _, e in evs], S.arr, S.ts, S.step, dict(raw or {"f_t": np.zeros(0, np.int64), "f_rate": np.zeros(0)}),
                         names=frozenset({"T"}))
    for k, ev in evs:
        r = rows[k]
        f = ev.funding_r
        nan = f is None or not math.isfinite(float(f))
        hours = max(0.0, (int(r["exit_ms"]) - int(r["entry_ms"])) / HOUR_MS)
        fill = -FUND_FILL_RATE * float(r["entry"]) * math.ceil(hours / FUND_FILL_HOURS - 1e-12) / float(r["risk"])
        r["funding_raw"] = None if nan else float(f)
        r["funding_nan"] = bool(nan)
        r["funding_r"] = fill if nan else float(f)
    for r in rows:
        if r["reason"] == "BAD_BAR":
            r["funding_raw"], r["funding_nan"], r["funding_r"] = None, True, float("nan")


# ---------------------------------------------------------------------------- sembol görevi
def process_symbol(symbol: str, df4: pd.DataFrame | None, df1: pd.DataFrame | None, btc_daily: tuple[np.ndarray, np.ndarray],
                   funding: Mapping[str, Any] | None, books: Iterable[str], cfg: L.LabConfig | None = None,
                   variants: Iterable[str] | None = None) -> tuple[list[dict], dict]:
    """Bir sembol: bütün istenen defterlerin gerçek + plasebo işlemleri (fonlama dahil) ve sayımları."""
    cfg = cfg or L.LabConfig()
    books = [b for b in BOOKS if b in set(books)]
    want = set(variants) if variants is not None else set(VARIANTS)
    d_ts, d_reg = btc_daily
    rows_all: list[dict] = []
    meta: dict[str, Any] = {"symbol": symbol, "books": books, "counts": {}}
    coin = regime_daily(df1) if df1 is not None and len(df1) else (np.zeros(0, np.int64), np.zeros(0, np.int8))
    for tf, df in ((TF4, df4), (TF1, df1)):
        if df is None or not len(df):
            continue
        need = [v for v, d in VARIANTS.items() if d["book"] in books and d["tf"] == tf and v in want]
        if not need:
            continue
        S = Series(df, tf)
        masks = _masks(S, regime_at(S.t, d_ts, d_reg), regime_at(S.t, coin[0], coin[1]))
        rows: list[dict] = []
        if "D4" in books:
            r_, m_ = d4_series_rows(symbol, S, masks, cfg, [v for v in need if v in D4_VARIANTS])
            rows += r_
            meta["counts"].update(m_)
            if tf == TF4 and "D4_00_BASE" in need:
                rows += d4_lab_placebo_rows(symbol, S, cfg)
        if tf == TF4 and "C4" in books and any(v in C4_VARIANTS for v in need):
            r_, m_ = c4_series_rows(symbol, S, masks, cfg, [v for v in need if v in C4_VARIANTS])
            rows += r_
            meta["counts"].update(m_)
        if tf == TF4 and "FM" in books and any(v in FM_VARIANTS for v in need):
            r_, m_ = fm_series_rows(symbol, S, masks, cfg, [v for v in need if v in FM_VARIANTS])
            rows += r_
            meta["counts"].update(m_)
        apply_funding(rows, S, funding)
        rows_all += rows
        dec = (S.t >= DECISION_START_MS) & (S.t < PRICE_END_MS)
        meta[f"unknown_{tf}"] = {"btc_regime": int((dec & ~(masks["BTC_UP"] | masks["BTC_DOWN"])).sum()),
                                 "coin_regime": int((dec & (regime_at(S.t, coin[0], coin[1]) == 0)).sum()), "bars": int(dec.sum())}
        meta[f"unclean_{tf}"] = {str(w): int((dec & ~S.clean(w)).sum()) for w in ((CLEAN_D4, CLEAN_FM) if tf == TF4 else (CLEAN_D4,))}
    return rows_all, meta


def _symbol_task(args: tuple) -> tuple[list[dict], dict]:
    symbol, df4, df1, btc_daily, funding, books, cfg_d, variants = args
    return process_symbol(symbol, df4, df1, btc_daily, funding, books, L.LabConfig(**cfg_d), variants)


# ---------------------------------------------------------------------------- veri: arşiv
class ZipCache:
    """data.binance.vision dosyaları için yerel önbellek: <cache>/archive/<sunucu>/<yol>. 404 kalıcı (".missing") yalnız
    dönemi `keep_404_days` günden eskiyse yazılır. `offline`: önbellekte olmayan dosya → ConnectionError (ağ YOK)."""

    _STAMP = re.compile(r"-(\d{4}-\d{2}(?:-\d{2})?)\.zip$")

    def __init__(self, cache_dir: Path | str, fetch: Callable[[str], bytes | None] | None = None, *, offline: bool = False,
                 now_ms: int | None = None, keep_404_days: int = 10):
        self.root = Path(cache_dir) / "archive"
        self.fetch = fetch or http_get
        self.offline = bool(offline)
        self.now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        self.keep_404_days = int(keep_404_days)
        self.requests = 0
        self.digests: dict[str, str] = {}

    def path(self, url: str) -> Path:
        return self.root / url.split("://", 1)[-1].split("?", 1)[0]

    def _period_end_ms(self, url: str) -> int | None:
        m = self._STAMP.search(url)
        if not m:
            return None
        s = m.group(1)
        if len(s) == 7:
            return L._next_month(month_start_ms(s))
        return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000) + DAY_MS

    def get(self, url: str) -> bytes | None:
        p = self.path(url)
        if p.exists():
            data = p.read_bytes()
            self.digests[url] = hashlib.sha256(data).hexdigest()
            return data
        miss = p.with_name(p.name + ".missing")
        if miss.exists():
            return None
        if self.offline:
            raise ConnectionError(f"çevrimdışı: önbellekte yok: {url}")
        self.requests += 1
        data = self.fetch(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            end = self._period_end_ms(url)
            if end is not None and end < self.now_ms - self.keep_404_days * DAY_MS:
                miss.write_text("404\n", encoding="utf-8")
            return None
        tmp = p.with_name(p.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(p)
        self.digests[url] = hashlib.sha256(data).hexdigest()
        return data


def http_get(url: str) -> bytes | None:
    """`signal_lab._http_get` (404 → None); ASCII olmayan sembol adları yüzde kodlanır."""
    return L._http_get(urllib.parse.quote(url, safe=":/?&=%"))


def clip(df: pd.DataFrame | None, start_ms: int, end_ms: int) -> pd.DataFrame:
    cols = ["timestamp", "open", "high", "low", "close", "volume", "close_time"]
    if df is None or not len(df):
        return pd.DataFrame({c: pd.Series(dtype="int64" if c in ("timestamp", "close_time") else float) for c in cols})
    df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] < end_ms)]
    df = df.drop_duplicates("timestamp", keep="last").sort_values("timestamp", kind="mergesort")
    return df[cols].reset_index(drop=True)


def load_klines(symbol: str, tf: str, start_ms: int, end_ms: int, get: Callable[[str], bytes | None], now_ms: int) -> pd.DataFrame:
    """USDⓈ-M arşivi `signal_lab.ArchiveProvider` + `signal_lab.download` ile; [start, end) aralığına kesin kırpılır."""
    prov = L.ArchiveProvider(fetch=get, clock_ms=lambda: int(now_ms))
    return clip(L.download(prov, symbol, tf, start_ms, end_ms - 1), start_ms, end_ms)


def series_digest(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    for t, o, hi, lo, c, v in zip(df["timestamp"].to_numpy(dtype=np.int64), *(df[k].to_numpy(dtype=float)
                                                                              for k in ("open", "high", "low", "close", "volume"))):
        h.update(("%d,%r,%r,%r,%r,%r\n" % (int(t), float(o), float(hi), float(lo), float(c), float(v))).encode())
    return h.hexdigest()


def series_quality(df: pd.DataFrame, tf: str, start_ms: int, end_ms: int | None = None) -> dict[str, Any]:
    """İlk bar (listeleme), eksik bar oranı, 24 saatten uzun boşluklar, boşluk ve bozuk bar sayısı, bayt özeti."""
    step = tf_ms(tf)
    end_ms = PRICE_END_MS if end_ms is None else int(end_ms)
    if df is None or not len(df):
        return {"bars": 0, "first": None, "last": None, "missing_ratio": None, "n_gaps": 0, "gaps_over_24h": [], "bad_bars": 0,
                "sha256": series_digest(clip(None, 0, 1))}
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    exp = int((min(int(end_ms), int(ts[-1]) + step) - int(ts[0])) // step)
    d = np.diff(ts)
    big = np.flatnonzero(d > DAY_MS)
    arr = {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    return {"bars": int(len(ts)), "first": _iso(ts[0]), "last": _iso(ts[-1]), "expected": exp,
            "missing_bars": int(exp - len(ts)), "missing_ratio": round(1 - len(ts) / exp, 6) if exp else None,
            "n_gaps": int((d != step).sum()), "n_gaps_over_24h": int(len(big)),
            "gaps_over_24h": [{"from": _iso(ts[k]), "to": _iso(ts[k + 1]), "hours": round(float(d[k]) / HOUR_MS, 1)} for k in big[:50]],
            "bad_bars": int(bad_bars(arr["open"], arr["high"], arr["low"], arr["close"], arr["volume"]).sum()),
            "start_lag_days": round((int(ts[0]) - int(start_ms)) / DAY_MS, 2), "ends_at_price_end": int(ts[-1]) + step >= end_ms,
            "sha256": series_digest(df)}


def load_funding(symbol: str, get: Callable[[str], bytes | None]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fonlama: yalnız aylık `fundingRate` dosyaları, FUNDING_START ayı … FUNDING_END'in ayı (2022-12 … 2026-10; arşivde günlük
    fonlama klasörü YOK, PRICE_END sonrası uzlaşmalar 2026-10 dosyasındadır); [FUNDING_START, FUNDING_END) aralığına kırpılır.
    Döner ({"f_t", "f_rate"}, bilgi); bilgi["post_end"] = [PRICE_END, FUNDING_END) aralığındaki uzlaşma sayısı (PRICE_END'e
    ulaşan seride 0 ise `run` veri hatasıyla durur)."""
    from . import futures_data as FD
    sym = archive_symbol(symbol)
    frames, files, missing = [], {}, []
    for mk in _month_range(month_key(FUNDING_START_MS), month_key(FUNDING_END_MS - 1)):
        url = FD.FUNDING_URL.format(sym=sym, month=mk)
        data = get(url)
        if data is None:
            missing.append(mk)                          # listeleme öncesi, eksik ya da (son ay) henüz yayımlanmadı
            continue
        frames.append(FD.parse_funding_zip(data))
        files[url] = hashlib.sha256(data).hexdigest()
    f = pd.concat(frames) if frames else pd.DataFrame({"t_ms": np.array([], dtype=np.int64), "rate": np.array([], dtype=float)})
    f = f[(f["t_ms"] >= FUNDING_START_MS) & (f["t_ms"] < FUNDING_END_MS)].drop_duplicates("t_ms").sort_values("t_ms")
    raw = {"f_t": f["t_ms"].to_numpy(dtype=np.int64), "f_rate": f["rate"].to_numpy(dtype=float)}
    post = int(((raw["f_t"] >= PRICE_END_MS) & (raw["f_t"] < FUNDING_END_MS)).sum())
    return raw, {"rows": int(len(f)), "missing": missing, "post_end": post, "files_sha256": files}


# ---------------------------------------------------------------------------- PIT evreni (§4.3)
def list_um_symbols(get_text: Callable[[str], str]) -> list[str]:
    """S3 listesi (CommonPrefixes), sayfalı (NextMarker)."""
    out: list[str] = []
    marker = ""
    for _ in range(100):
        x = get_text(PIT_LIST_URL + ("&marker=" + urllib.parse.quote(marker) if marker else ""))
        out += re.findall(r"<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>", x)
        if re.search(r"<IsTruncated>true</IsTruncated>", x) is None:
            return sorted(set(out))
        nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", x)
        if nm is None:
            raise BookDataError("PIT listesi: NextMarker yok")
        marker = nm.group(1)
    raise BookDataError("PIT listesi: sayfa sınırı aşıldı")


def pit_pool(archive_syms: Iterable[str]) -> tuple[list[str], dict[str, str]]:
    """Havuz ve dışlananlar (neden)."""
    banned = {b: cat for cat, xs in PIT_EXCLUDE.items() for b in xs}
    pool, excl = [], {}
    for s in sorted(set(archive_syms)):
        if "_" in s:
            excl[s] = "vadeli (alt çizgi)"
        elif not s.endswith("USDT"):
            excl[s] = "kota USDT değil"
        elif s[:-4] in banned:
            excl[s] = banned[s[:-4]]
        else:
            pool.append(s)
    return pool, excl


def parse_daily_qv(data: bytes) -> pd.DataFrame:
    """1d kline zip → [timestamp, quote_volume] (başlık atılır; mikro saniye → ms)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        raw = zf.read(zf.namelist()[0]).decode("utf-8")
    df = pd.read_csv(io.StringIO(raw), header=None, dtype=str)
    head = [str(x).strip().lower() for x in df.iloc[0].tolist()] if len(df) and not str(df.iloc[0, 0]).isdigit() else []
    qi = head.index("quote_volume") if "quote_volume" in head else 7     # Binance kline sütunu (başlıksız dosyada 8.)
    df = df[df[0].str.isdigit()]
    ts = df[0].astype("int64").to_numpy()
    ts = np.where(ts > 10 ** 14, ts // 1000, ts)
    return pd.DataFrame({"timestamp": ts, "quote_volume": pd.to_numeric(df[qi], errors="coerce").to_numpy(dtype=float)})


def pit_universe(pool: list[str], qv_loader: Callable[[str], pd.DataFrame]) -> dict[str, list[str]]:
    """Ay başı m için: [m − 30 gün, m) açılışlı 1d barlarda quote_volume toplamı; en az 30 bar; sıra (−hacim, ad); ilk 40."""
    data = {s: qv_loader(s) for s in pool}
    out: dict[str, list[str]] = {}
    for mk in PIT_MONTHS:
        m = month_start_ms(mk)
        rank = []
        for s, df in data.items():
            if df is None or not len(df):
                continue
            w = df[(df["timestamp"] >= m - PIT_DAYS * DAY_MS) & (df["timestamp"] < m)]
            if len(w) >= PIT_MIN_BARS:
                rank.append((-float(np.nansum(w["quote_volume"].to_numpy(dtype=float))), s))
        out[mk] = [bot_symbol(s) for _, s in sorted(rank)[:PIT_TOP]]
    return out


def pit_missing_pairs(universe: Mapping[str, list[str]], frames4: Mapping[str, pd.DataFrame | None],
                      frames1: Mapping[str, pd.DataFrame | None]) -> dict[str, Any]:
    """Eksik çift: o aydaki 4h bar < 0,90 × 6 × o aydaki 1d bar (1d bar > 0). 1d serisi YÜKLENMEMİŞ seçili sembol (denetlenemez)
    her evren ayında eksik çift sayılır (fail-closed)."""
    pairs = miss = 0
    rows = []
    for mk, syms in universe.items():
        a, b = month_start_ms(mk), L._next_month(month_start_ms(mk))
        for s in syms:
            d1, d4 = frames1.get(s), frames4.get(s)
            if d1 is None:
                pairs += 1
                miss += 1
                rows.append({"month": mk, "symbol": s, "bars_4h": None, "bars_1d": None, "not_loaded": True})
                continue
            n1 = int(((d1["timestamp"] >= a) & (d1["timestamp"] < b)).sum()) if len(d1) else 0
            if n1 == 0:
                continue
            pairs += 1
            n4 = int(((d4["timestamp"] >= a) & (d4["timestamp"] < b)).sum()) if d4 is not None and len(d4) else 0
            if n4 < PIT_PAIR_MIN_COVER * 6 * n1:
                miss += 1
                rows.append({"month": mk, "symbol": s, "bars_4h": n4, "bars_1d": n1})
    share = miss / pairs if pairs else 1.0
    return {"pairs": pairs, "missing": miss, "share": round(share, 6), "ok": share <= PIT_MISSING_MAX, "rows": rows[:200]}


# ---------------------------------------------------------------------------- istatistik (§8)
def _rs(df: pd.DataFrame, key: str) -> tuple[np.ndarray, np.ndarray]:
    return df[key].to_numpy(dtype=float), (df["t_ms"].to_numpy(dtype=np.int64) // DAY_MS)


def side_stats(real: pd.DataFrame, plac: pd.DataFrame, key: str, cfg: L.LabConfig) -> dict[str, Any]:
    """Standart + sıkı hüküm (signal_lab fonksiyonları DEĞİŞMEDEN). `key`: "r" (fonlamasız) ya da "rf" (fonlamalı)."""
    out: dict[str, Any] = {}
    st: dict[str, dict] = {}
    for who, df in (("real", real), ("placebo", plac)):
        for per in ("IS", "OOS"):
            d = df[df["period"] == per]
            rs, days = _rs(d, key)
            st[f"{who}_{per}"] = L.r_stats(rs, cfg.bootstrap_iters, days=days) if len(rs) else {"n": 0}
    is_st, oos_st, p_is, p_oos = st["real_IS"], st["real_OOS"], st["placebo_IS"], st["placebo_OOS"]
    vs = None
    if p_is.get("n", 0) >= MIN_PLACEBO_N and p_oos.get("n", 0) >= MIN_PLACEBO_N and is_st.get("n") and oos_st.get("n"):
        vs = {"IS": round(is_st["mean_r"] - p_is["mean_r"], 4), "OOS": round(oos_st["mean_r"] - p_oos["mean_r"], 4),
              "placebo_mean_r": [p_is["mean_r"], p_oos["mean_r"]], "placebo_n": [p_is["n"], p_oos["n"]]}
    ci = {}
    for per in ("IS", "OOS"):
        a, b = real[real["period"] == per], plac[plac["period"] == per]
        ra, da = _rs(a, key)
        rb, db = _rs(b, key)
        ci[per] = L.diff_ci(ra, da, rb, db, cfg.bootstrap_iters) if len(ra) and len(rb) else None
    verdict = L.verdict(is_st, oos_st, cfg, vs)
    if vs is not None:
        vs["ci95"] = ci
    out.update({"IS": is_st, "OOS": oos_st, "placebo": {"IS": p_is, "OOS": p_oos}, "vs_placebo": vs, "diff_ci95": ci,
                "verdict": verdict, "verdict_strict": L.verdict_strict(verdict, ci),
                "replicated": L.replicated(is_st, oos_st, cfg),
                "placebo_verdict": L.verdict(p_is, p_oos, cfg, None), "placebo_replicated": L.replicated(p_is, p_oos, cfg)})
    out["oos_diff_point"] = (round(float(np.mean(_rs(real[real["period"] == "OOS"], key)[0]))
                                   - float(np.mean(_rs(plac[plac["period"] == "OOS"], key)[0])), 6)
                             if (real["period"] == "OOS").any() and (plac["period"] == "OOS").any() else None)
    return out


def mt_pvalue(real: pd.DataFrame, plac: pd.DataFrame, iters: int = MT_ITERS) -> float:
    """Doğrulama farkının tek yönlü p'si (fonlamasız): iki taraf ayrı gün kümeli bootstrap (signal_lab._day_boot_means)."""
    a, b = real[real["period"] == "OOS"], plac[plac["period"] == "OOS"]
    ra, da = _rs(a, "r")
    rb, db = _rs(b, "r")
    if not len(ra) or not len(rb):
        return 1.0
    x = L._day_boot_means(ra, da, iters, MT_SEED_REAL)
    y = L._day_boot_means(rb, db, iters, MT_SEED_PLACEBO)
    if x is None or y is None:
        return 1.0
    return float((1 + int(np.sum((x - y) <= 0))) / (iters + 1))


def holm(pvals: Mapping[str, float], alpha: float = HOLM_ALPHA) -> dict[str, bool]:
    """Holm adım adım: p(k) ≤ α / (m − k + 1) olduğu sürece reddet; ilk başarısızlıkta dur."""
    items = sorted(pvals.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(items)
    out = {k: False for k in pvals}
    for k, (cid, p) in enumerate(items, start=1):
        if p <= alpha / (m - k + 1):
            out[cid] = True
        else:
            break
    return out


def candidate_rate(groups: Iterable[Mapping[str, Any]]) -> float | None:
    gs = [g for g in groups if g["verdict"] != V_THIN]
    return round(sum(1 for g in gs if g["replicated"] and g["IS"]["mean_r"] > 0) / len(gs), 4) if gs else None


# ---------------------------------------------------------------------------- aylık ölçü (§8.1)
def month_stats(series: Mapping[str, float], counts: Mapping[str, int], months: list[str]) -> dict[str, Any]:
    vals = np.array([float(series.get(k, 0.0)) for k in months], dtype=float)
    m = len(vals)
    mean = float(vals.mean()) if m else None
    ci = None
    if m:
        idx = np.random.default_rng(MONTH_SEED).integers(0, m, size=(MONTH_ITERS, m))
        means = vals[idx].mean(axis=1)
        ci = [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]
    worst = int(np.argmin(vals)) if m else None
    return {"months": m, "mean_pct_exact": mean, "mean_pct": None if mean is None else round(mean, 4),
            "ci95_exact": ci, "ci95": None if ci is None else [round(ci[0], 4), round(ci[1], 4)],
            "share_ge_target": round(float(np.mean(vals >= TARGET_PCT_MONTH)), 4) if m else None,
            "months_without_trades": int(sum(1 for k in months if not counts.get(k))),
            "worst_month": {"month": months[worst], "pct": round(float(vals[worst]), 4)} if worst is not None else None,
            "series": {k: round(float(v), 4) for k, v in zip(months, vals)}}


def monthly(df: pd.DataFrame, key: str, weight: np.ndarray | None = None, *, by: str = "exit_bar_ms",
            risk_pct: float = RISK_PCT) -> dict[str, Any]:
    """Ay başına Σ (w × R) × risk%; keşif ve doğrulama ayları ayrı (işlemsiz ay 0)."""
    w = np.ones(len(df)) if weight is None else np.asarray(weight, dtype=float)
    sums: dict[str, float] = {}
    cnt: Counter = Counter()
    for ms, r, ww in zip(df[by].to_numpy(dtype=np.int64), df[key].to_numpy(dtype=float), w):
        mk = month_key(int(ms))
        sums[mk] = sums.get(mk, 0.0) + ww * r * risk_pct
        cnt[mk] += 1
    return {"IS": month_stats(sums, cnt, IS_MONTHS), "OOS": month_stats(sums, cnt, OOS_MONTHS)}


# ---------------------------------------------------------------------------- kapasite (§8.2)
def load_filters(path: Path | str | None, symbols: Iterable[str]) -> dict[str, Any]:
    """Botun FiltersCache JSON'u (salt okunur): {"ok", "table": {sym: (min_notional, qty_step, min_qty)}, "verified_at",
    "sha256", "missing"}. Dosya yoksa ya da evrenin bir sembolü yoksa ok=False (FİLTRE BEKLİYOR)."""
    out: dict[str, Any] = {"ok": False, "path": str(path) if path else None, "table": {}, "verified_at": None, "sha256": None,
                           "missing": []}
    if not path or not Path(path).exists():
        out["missing"] = list(symbols)
        return out
    data = Path(path).read_bytes()
    out["sha256"] = hashlib.sha256(data).hexdigest()
    raw = json.loads(data.decode("utf-8"))
    out["verified_at"] = raw.get("verified_at")
    fut = raw.get("futures") or {}
    for s in symbols:
        d = fut.get(s) or fut.get(archive_symbol(s))
        if not isinstance(d, dict):
            out["missing"].append(s)
            continue
        out["table"][s] = (float(d.get("min_notional")), float(d.get("qty_step")), float(d.get("min_qty")))
    out["ok"] = not out["missing"]
    return out


def capacity_run(cands: pd.DataFrame, book: str, vid: str, *, table: Mapping[str, tuple] | None, bump: bool, slots: int | None = None,
                 order: str = "hash", order_list: Iterable[str] = ()) -> dict[str, Any]:
    """Kesintisiz defter (2023-01-01 → PRICE_END): kabul algoritması (§8.2). `table` None → min-notional 0, adım yok (PIT)."""
    from . import learning_mode as LM
    cap = CAPACITY[book]
    E, K, Lmax = float(cap["equity"]), int(slots or cap["slots"]), int(cap["leverage_max"])
    rank = {s: k for k, s in enumerate(order_list)}
    df = cands.reset_index(drop=True)
    keyf = (lambda r: L._h(r["symbol"], int(r["t_ms"]), vid)) if order == "hash" else (lambda r: rank.get(r["symbol"], 10 ** 6))
    recs = df.to_dict("records")
    for k, r in enumerate(recs):
        r["_k"] = k
    recs.sort(key=lambda r: (int(r["t_ms"]), keyf(r), str(r["symbol"])))
    open_: dict[str, dict] = {}
    acc: dict[int, dict] = {}
    rej: Counter = Counter()
    bumped = 0
    for r in recs:
        t = int(r["t_ms"])
        for s in [s for s, p in open_.items() if int(p["exit_ms"]) <= t]:
            del open_[s]
        if r["symbol"] in open_:
            rej["SAME_SYMBOL"] += 1
            continue
        if len(open_) >= K:
            rej["SLOTS_FULL"] += 1
            continue
        if sum(p["risk_usdt"] for p in open_.values()) + RISK_PCT / 100.0 * E > E:
            rej["TOTAL_RISK"] += 1
            continue
        mn, qs, mq = (table.get(r["symbol"]) if table is not None else (0.0, None, None))
        fit = LM.fit_size(equity=E, entry=float(r["entry"]), stop=float(r["stop"]), slots=K, leverage_max=Lmax, risk_pct=RISK_PCT,
                          reserve_pct=CAP_COMMON["reserve_pct"], liq_buffer_mult=CAP_COMMON["liq_buffer_mult"], mmr=CAP_COMMON["mmr"],
                          min_notional=mn, qty_step=qs, price_for_step=float(r["entry"]), hard_cap_pct=CAP_COMMON["hard_cap_pct"],
                          min_notional_bump=bool(bump), available_margin=E - sum(p["margin"] for p in open_.values()),
                          min_qty=mq, max_position_pct=CAP_COMMON["max_position_pct"])
        if not fit.ok:
            rej[fit.reason or "FIT"] += 1
            continue
        w = float(fit.risk_usdt) / (RISK_PCT / 100.0 * E)
        bumped += int(fit.size_rule == LM.SIZE_BUMP)
        open_[r["symbol"]] = {"exit_ms": int(r["exit_ms"]), "margin": float(fit.margin), "risk_usdt": float(fit.risk_usdt)}
        acc[r["_k"]] = {"w": w, "t_ms": t, "exit_ms": int(r["exit_ms"]), "bump": fit.size_rule == LM.SIZE_BUMP}
    idx = sorted(acc)
    sel = df.iloc[idx].copy()
    sel["w"] = [acc[k]["w"] for k in idx]
    return {"accepted": sel, "rejected": dict(rej), "bumped": bumped, "n_candidates": len(df)}


def concurrency(sel: pd.DataFrame) -> dict[str, Any]:
    out = {}
    for name, (a, b) in (("ALL", (DECISION_START_MS, PRICE_END_MS)), ("IS", (DECISION_START_MS, IS_END_MS)),
                         ("OOS", (IS_END_MS, PRICE_END_MS))):
        iv = [(max(int(s), a), min(int(e), b)) for s, e in zip(sel["t_ms"], sel["exit_ms"]) if min(int(e), b) > max(int(s), a)]
        pts = sorted([(s, 1) for s, _ in iv] + [(e, -1) for _, e in iv], key=lambda x: (x[0], x[1]))
        cur = mx = 0
        for _, d in pts:
            cur += d
            mx = max(mx, cur)
        out[name] = {"max": int(mx), "mean": round(sum(e - s for s, e in iv) / (b - a), 4)}
    return out


def mtm_monthly(sel: pd.DataFrame) -> dict[str, Any]:
    """Bilgi: ay sonu işaretlemeli özsermaye değişimi (fonlamasız). Özsermaye(ay sonu) = Σ kapanmış 0,5·w·R + Σ açık 0,5·w·R_ay_sonu."""
    months = IS_MONTHS + OOS_MONTHS
    eq_prev = 0.0
    series: dict[str, float] = {}
    rows = list(zip(sel["exit_ms"].to_numpy(dtype=np.int64), sel["r"].to_numpy(dtype=float), sel["w"].to_numpy(dtype=float),
                    sel["entry_ms"].to_numpy(dtype=np.int64), sel["mtm"]))
    for mk in months:
        y, m = int(mk[:4]), int(mk[5:])
        me = month_start_ms(f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}")
        eq = 0.0
        for ex, r, w, en, mt in rows:
            if ex <= me:
                eq += RISK_PCT * w * r
            elif en <= me:
                mtd = mt if isinstance(mt, dict) else json.loads(mt or "{}")
                eq += RISK_PCT * w * float(mtd.get(mk, 0.0))
        series[mk] = eq - eq_prev
        eq_prev = eq
    return {"IS": month_stats(series, {k: 1 for k in months}, IS_MONTHS),
            "OOS": month_stats(series, {k: 1 for k in months}, OOS_MONTHS)}


def capacity_cell(real: pd.DataFrame, book: str, vid: str, *, filters: Mapping[str, Any] | None, universe_order: Iterable[str],
                  pit: bool = False) -> dict[str, Any]:
    """Hücrenin kapasiteli ölçüleri: BUMP ve NO_BUMP (PIT'te yalnız NO_BUMP, min-notional 0) + bilgiler (K=3, config sırası,
    işaretleme)."""
    cands = real[(real["reason"] != "BAD_BAR") & real["period"].notna()]
    out: dict[str, Any] = {}
    modes = [("NO_BUMP", None, False)] if pit else [("BUMP", filters["table"], True), ("NO_BUMP", filters["table"], False)]
    for mode, table, bump in modes:
        res = capacity_run(cands, book, vid, table=table, bump=bump)
        sel = res["accepted"]
        oos_exit = sel[[month_key(int(x)) in set(OOS_MONTHS) for x in sel["exit_bar_ms"]]] if len(sel) else sel
        nan_share = float(oos_exit["funding_nan"].astype(bool).mean()) if len(oos_exit) else 0.0
        wv = sel["w"].to_numpy(dtype=float) if len(sel) else np.zeros(0)
        out[mode] = {"unfunded": monthly(sel, "r", sel["w"].to_numpy()), "funded": monthly(sel, "rf", sel["w"].to_numpy()),
                     "accepted": int(len(sel)), "candidates": res["n_candidates"], "rejected": res["rejected"], "bumped": res["bumped"],
                     "nan_funding_share_oos": round(nan_share, 6), "concurrency": concurrency(sel),
                     "w": {"min": float(wv.min()) if len(wv) else None, "p25": float(np.quantile(wv, 0.25)) if len(wv) else None,
                           "median": float(np.median(wv)) if len(wv) else None, "p75": float(np.quantile(wv, 0.75)) if len(wv) else None,
                           "max": float(wv.max()) if len(wv) else None, "share_lt1": round(float(np.mean(wv < 1 - 1e-9)), 4) if len(wv) else None,
                           "share_gt1": round(float(np.mean(wv > 1 + 1e-9)), 4) if len(wv) else None},
                     "mtm_info": mtm_monthly(sel), "risk1_info": {"OOS_mean_pct": None}}
        u = out[mode]["unfunded"]["OOS"]["mean_pct_exact"]
        out[mode]["risk1_info"]["OOS_mean_pct"] = None if u is None else round(u * RISK_PCT_INFO / RISK_PCT, 4)
    if not pit:
        k3 = capacity_run(cands, book, vid, table=filters["table"], bump=True, slots=CAP_COMMON["slots_info"])["accepted"]
        cfg_o = capacity_run(cands, book, vid, table=filters["table"], bump=True, order="config", order_list=universe_order)["accepted"]
        out["info"] = {"K3": {"unfunded_oos": monthly(k3, "r", k3["w"].to_numpy())["OOS"]["mean_pct"],
                              "funded_oos": monthly(k3, "rf", k3["w"].to_numpy())["OOS"]["mean_pct"], "accepted": int(len(k3))},
                       "config_order": {"unfunded_oos": monthly(cfg_o, "r", cfg_o["w"].to_numpy())["OOS"]["mean_pct"],
                                        "funded_oos": monthly(cfg_o, "rf", cfg_o["w"].to_numpy())["OOS"]["mean_pct"],
                                        "accepted": int(len(cfg_o))}}
    return out


# ---------------------------------------------------------------------------- hücreler, hedef, basamaklar
def cell_frames(T: pd.DataFrame, cid: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(gerçek, plasebo) satırları — BAD_BAR satırları gerçekte durur (pay için), istatistikten ayrıca atılır."""
    vid, scope = cid.split("|")
    d = T[T["variant"] == vid]
    if VARIANTS[vid]["book"] == "C4":
        real = d[(d["kind"] == "real") & d["scopes"].fillna("").str.split(",").apply(lambda xs: scope in xs)]
    else:
        real = d[(d["kind"] == "real") & ((d["side"] == scope) if scope != "BOTH" else True)]
    plac = d[(d["kind"] == "placebo") & ((d["side"] == scope) if scope != "BOTH" else True)]
    return real, plac


def _info(real: pd.DataFrame) -> dict[str, Any]:
    from . import candle_lab as CL
    good = real[real["reason"] != "BAD_BAR"]
    ex = {}
    for per in ("IS", "OOS"):
        g = good[good["period"] == per]
        ex[per] = {"exit_reasons": dict(Counter(g["reason"])), "mean_hold": round(float(g["hold"].mean()), 2) if len(g) else None,
                   "cost_r": round(float(g["cost_r"].mean()), 4) if len(g) else None,
                   "gap_cross": int(g["gap_cross"].astype(bool).sum()) if len(g) else 0,
                   "gap_hours_max": round(float(g["gap_hours"].max()), 2) if len(g) else 0.0}
    no = CL.non_overlap_mean_r(good[["symbol", "i", "hold", "r", "t_ms"]].to_dict("records"), IS_END_MS - 1) if len(good) else None
    slices: dict[str, Any] = {}
    for dim in ("hacim", "rsi", "trend", "volatilite"):
        col = "ctx." + dim
        if col not in good:
            continue
        for b, g in good.groupby(col):
            slices[f"{dim}={b}"] = {per: {"n": int((g["period"] == per).sum()),
                                          "mean_r": round(float(g.loc[g["period"] == per, "r"].mean()), 4) if (g["period"] == per).any() else None}
                                    for per in ("IS", "OOS")}
    return {"exits": ex, "non_overlap_mean_r": no, "context_slices": slices}


def evaluate_cell(T: pd.DataFrame, cid: str, cfg: L.LabConfig) -> dict[str, Any]:
    vid, scope = cid.split("|")
    real_all, plac_all = cell_frames(T, cid)
    real = real_all[real_all["reason"] != "BAD_BAR"]
    plac = plac_all[plac_all["reason"] != "BAD_BAR"]
    st = {"unfunded": side_stats(real, plac, "r", cfg), "funded": side_stats(real, plac, "rf", cfg)}
    n_all = len(real_all)
    n_bad = int((real_all["reason"] == "BAD_BAR").sum())
    cens = {}
    for per in ("IS", "OOS"):
        g = real[real["period"] == per]
        cens[per] = round(float((g["reason"] == "DATA_END").mean()), 6) if len(g) else 0.0
    nanp = {per: round(float(real.loc[real["period"] == per, "funding_nan"].astype(bool).mean()), 6) if (real["period"] == per).any()
            else None for per in ("IS", "OOS")}
    cell = {"cell": cid, "variant": vid, "scope": scope, "book": VARIANTS[vid]["book"], "tf": VARIANTS[vid]["tf"],
            "control": bool(VARIANTS[vid].get("control")), "stats": st,
            "counts": {"real": {p: int((real["period"] == p).sum()) for p in ("IS", "OOS")},
                       "placebo": {p: int((plac["period"] == p).sum()) for p in ("IS", "OOS")}, "bad_bar": n_bad,
                       "delisted": int((real["reason"] == "DELISTED").sum()), "symbols": int(real["symbol"].nunique())},
            "censor_share": cens, "bad_share": round(n_bad / n_all, 6) if n_all else 0.0, "nan_funding_share_real": nanp,
            "flags": [], "monthly_plain": {"unfunded": monthly(real, "r"), "funded": monthly(real, "rf")},
            "monthly_info": {"decision_month": monthly(real, "r", by="t_ms")["OOS"]["mean_pct"],
                             "risk1_oos": None},
            "info": _info(real_all)}
    pm = cell["monthly_plain"]["unfunded"]["OOS"]["mean_pct_exact"]
    cell["monthly_info"]["risk1_oos"] = None if pm is None else round(pm * RISK_PCT_INFO / RISK_PCT, 4)
    if cens["OOS"] > CENSOR_MAX:
        cell["flags"].append(ST_CENSOR)
    if cell["bad_share"] > BAD_MAX:
        cell["flags"].append(ST_BAD)
    if vid == "D4_00_BASE":
        lab = T[(T["variant"] == vid) & (T["kind"] == "labplacebo") & (T["reason"] != "BAD_BAR")]
        cell["info"]["lab_placebo"] = side_stats(real, lab, "r", cfg) if len(lab) else None
    return cell


def target_status(cell: Mapping[str, Any], cells: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """§8.3: bütün şartlar; başarısızlıklar kodlarıyla."""
    fails: list[str] = []
    st = cell["stats"]
    if st["unfunded"]["verdict_strict"] != V_STRONG:
        fails.append("1:SIKI_FONLAMASIZ")
    if st["funded"]["verdict_strict"] != V_STRONG:
        fails.append("1:SIKI_FONLAMALI")
    cap = cell.get("capacity")
    waiting = cap is None or cap.get("status") == ST_FILTER_WAIT
    if waiting:
        fails.append(ST_FILTER_WAIT)
    else:
        for mode in CAP_MODES:
            m = cap[mode]
            for fund in ("unfunded", "funded"):
                x = m[fund]["OOS"]["mean_pct_exact"]
                if x is None or not x >= TARGET_PCT_MONTH:
                    fails.append(f"2:AYLIK_{mode}_{fund}")
            ci = m["funded"]["OOS"]["ci95_exact"]
            if not ci or not ci[0] > 0:
                fails.append(f"3:ARALIK_{mode}")
            x = m["funded"]["IS"]["mean_pct_exact"]
            if x is None or not x > 0:
                fails.append(f"4:KESIF_{mode}")
            if m["nan_funding_share_oos"] > NAN_FUND_MAX:
                fails.append(f"6:{ST_FUND_MISSING}_{mode}")
    for f in (ST_CENSOR, ST_BAD):
        if f in cell["flags"]:
            fails.append("7:" + f)
    if cell["scope"] == "BOTH":
        for sc in ("LONG", "SHORT"):
            other = cells.get(cell_id(cell["variant"], sc))
            for fund in ("unfunded", "funded"):
                if other is None or other["stats"][fund]["verdict"] == V_LOSS:
                    fails.append(f"8:YON_{sc}_{fund}")
    hard = [f for f in fails if f != ST_FILTER_WAIT]
    if not fails:
        status = ST_MEETS
    elif not hard:
        status = ST_FILTER_WAIT                       # kapasite dışındaki şartlar geçti; hüküm tabloyu bekler
    elif all(f.startswith("6:") for f in hard):
        status = ST_FUND_MISSING
    else:
        status = ST_NOT_MEETS
    return {"meets": not fails, "status": status, "failed": fails}


def pit_check(cell: Mapping[str, Any], verified: bool = True) -> dict[str, Any]:
    """§8.5.4 geçiş ölçütü (PIT kipindeki hücre üzerinde). `verified` False (liste yok ya da eksik çift > %5) → PIT
    DOĞRULANAMADI."""
    if not verified:
        return {"pass": False, "status": ST_PIT_UNVERIFIED, "failed": ["VERI"]}
    cap = (cell.get("capacity") or {}).get("NO_BUMP")
    fails = []
    for fund in ("unfunded", "funded"):
        x = cap[fund]["OOS"]["mean_pct_exact"] if cap else None
        if x is None or not x >= TARGET_PCT_MONTH:
            fails.append(f"a:AYLIK_{fund}")
    d = cell["stats"]["funded"].get("oos_diff_point")
    if d is None or not d > 0:
        fails.append("b:FARK")
    for fund in ("unfunded", "funded"):
        if cell["stats"][fund]["verdict"] == V_LOSS:
            fails.append(f"c:KAYBETTIRIR_{fund}")
    return {"pass": not fails, "status": ST_PIT_PASS if not fails else ST_PIT_FAIL, "failed": fails}


def apply_pit(rep: dict[str, Any], pit_rep: Mapping[str, Any] | None) -> None:
    """Birincil raporda PIT TEYİDİ BEKLİYOR hücrelerine aynı mühürlü ve AYNI KODLA (kod ağacı aynı, iki ağaç temiz) üretilmiş
    PIT raporunun sonucunu yazar (§8.5.4); kod farklıysa yazmaz ve nedenini `pit_not_applied`e koyar."""
    if not pit_rep or pit_rep.get("registry_sha") != BOOK_REGISTRY_SHA:
        return
    if not code_match(pit_rep.get("code"), rep.get("code")):
        rep["pit_not_applied"] = (f"PIT raporu başka kod durumunda üretildi (PIT {pit_rep.get('code')}, birincil {rep.get('code')}); "
                                  "aynı kodla PIT teyidi yeniden koşulmalı")
        return
    rep.pop("pit_not_applied", None)
    for cid, c in (rep.get("cells") or {}).items():
        if c.get("final") != ST_PIT_WAIT:
            continue
        pc = ((pit_rep.get("cells") or {}).get(cid) or {}).get("pit_check")
        if not pc:
            continue
        c["pit"] = pc
        c["final"] = ST_CANDIDATE if pc.get("pass") else pc.get("status") or ST_PIT_FAIL
    rep["conclusion_tr"] = conclusion_tr(rep)


ST_MT_INCOMPLETE = "ÇOKLU TEST YAPILAMADI (54 hücrenin hepsi yok)"


def final_status(target: Mapping[str, Any], cid: str, *, complete: bool, holm_pass: bool, chance: bool) -> tuple[str, list[str]]:
    """§8.5 basamakları (birincil evren): 1 hedef → 2 çoklu test ve aday oranı → 3 taban / veri gözetlemeli değil → 4 PIT
    teyidi bekliyor (PIT sonucu `apply_pit` ile yazılır). Döner (durum, elendiği basamak)."""
    vid = cid.split("|")[0]
    if not target["meets"]:
        return target["status"], []
    if not complete:
        return ST_MT_INCOMPLETE, ["2"]
    if not holm_pass:
        return ST_MT_FAIL, ["2"]
    if chance:
        return ST_CHANCE, ["2"]
    if vid in BASE_VARIANTS or vid in SNOOPED_VARIANTS:
        return ST_BASE, ["3"]
    return ST_PIT_WAIT, ["4"]


def cell_counts(series_meta: Mapping[str, Any] | None, cid: str) -> dict[str, Any]:
    """Sembol sayımlarından hücre toplamı: ham sinyal, temiz pencere yüzünden düşen tespit, atlama nedenleri, plasebo çekilişi
    ve NO_REAL_RISK payı. Payın paydası (`placebo_selected`) bütün defterlerde risk geometrisi kurulmadan ÖNCE seçilen plasebo
    sayısıdır: D4/Formasyon'da çekilen bar; C4'te variation_events'in ürettiği plasebo + NO_REAL_RISK + BAD_STOP (hepsi tespit
    anı süzgecinden önce)."""
    vid, scope = cid.split("|")
    book = VARIANTS[vid]["book"]
    sides = VARIANTS[vid]["sides"] if scope == "BOTH" else [scope]
    out: dict[str, Any] = {"real_raw": 0, "unclean_window": 0, "real_skipped": {}, "placebo_drawn": 0, "placebo_selected": 0,
                           "placebo_skipped": {}}
    seen: set[str] = set()
    for sym, per_book in (series_meta or {}).items():
        m = (per_book or {}).get(book) or {}
        if sym in seen:
            continue
        seen.add(sym)
        cnt = m.get("counts") or {}
        if book == "C4":
            for cv, c in ((cnt.get(vid) or {}).get("cvs") or {}).items():
                if c4_variation(cv, None).side not in sides:
                    continue
                pl = c.get("placebo") or {}
                out["placebo_drawn"] += int(pl.get("drawn") or 0)
                vsk = {k.split(":")[-1]: int(v) for k, v in (pl.get("variation_events_skipped") or {}).items()}
                out["placebo_selected"] += (int(pl.get("produced_all", pl.get("drawn")) or 0) + vsk.get("NO_REAL_RISK", 0)
                                            + vsk.get("BAD_STOP", 0))
                for k, v in list((pl.get("skipped") or {}).items()) + list(vsk.items()):
                    out["placebo_skipped"][k] = out["placebo_skipped"].get(k, 0) + int(v)
                for k, v in ((c.get("real") or {}).get("skipped") or {}).items():
                    out["real_skipped"][k] = out["real_skipped"].get(k, 0) + int(v)
                out["real_raw"] += int(c.get("detected") or 0)
            continue
        for side in sides:
            c = cnt.get(f"{vid}|{side}") or {}
            r, pl = c.get("real") or {}, c.get("placebo") or {}
            out["real_raw"] += int(r.get("raw_all") or 0)
            out["unclean_window"] += int(r.get("unclean_window") or 0)
            out["placebo_drawn"] += int(pl.get("drawn") or 0)
            out["placebo_selected"] += int(pl.get("drawn") or 0)
            for k, v in (r.get("skipped") or {}).items():
                out["real_skipped"][k] = out["real_skipped"].get(k, 0) + int(v)
            for k, v in (pl.get("skipped") or {}).items():
                out["placebo_skipped"][k] = out["placebo_skipped"].get(k, 0) + int(v)
    d = out["placebo_selected"]
    out["no_real_risk_share"] = round(out["placebo_skipped"].get("NO_REAL_RISK", 0) / d, 6) if d else None
    return out



def build_report(T: pd.DataFrame, cfg: L.LabConfig, *, universe: str, filters: Mapping[str, Any] | None,
                 pit_membership: Mapping[str, list[str]] | None = None, pit_meta: Mapping[str, Any] | None = None,
                 books: Iterable[str] = BOOKS) -> dict[str, Any]:
    """Hücreler (54 + kontrol), hedef, çoklu test, aday oranı, basamaklar."""
    books = set(books)
    want = [c for c in ALL_CELLS if VARIANTS[c.split("|")[0]]["book"] in books]
    T = T.copy()
    T["rf"] = T["r"] + T["funding_r"]
    if pit_membership is not None:
        mem = {(mk, s) for mk, ss in pit_membership.items() for s in ss}
        T = T[np.array([(month_key(int(t)), s) in mem for t, s in zip(T["t_ms"], T["symbol"])], dtype=bool)]
    cells: dict[str, dict] = {}
    order = list(PRIMARY_UNIVERSE if universe == "primary" else INFO_UNIVERSE)
    for cid in want:
        cells[cid] = evaluate_cell(T, cid, cfg)
        vid = cid.split("|")[0]
        if universe == "pit":
            real, _ = cell_frames(T, cid)
            cells[cid]["capacity"] = capacity_cell(real, VARIANTS[vid]["book"], vid, filters=None, universe_order=(), pit=True)
        elif filters is not None and filters.get("ok"):
            real, _ = cell_frames(T, cid)
            cells[cid]["capacity"] = capacity_cell(real, VARIANTS[vid]["book"], vid, filters=filters, universe_order=order)
        else:
            cells[cid]["capacity"] = {"status": ST_FILTER_WAIT}
    verified = bool(((pit_meta or {}).get("missing_pairs") or {}).get("ok", True))
    for cid, c in cells.items():
        c["target"] = target_status(c, cells) if universe != "pit" else None
        if universe == "pit":
            c["pit_check"] = pit_check(c, verified)
    prim = [c for c in PRIMARY_CELLS if c in cells]
    complete = len(prim) == len(PRIMARY_CELLS)
    pvals = {}
    for cid in prim:
        real, plac = cell_frames(T, cid)
        pvals[cid] = mt_pvalue(real[real["reason"] != "BAD_BAR"], plac[plac["reason"] != "BAD_BAR"])
    rej = holm(pvals) if complete else {}
    real_groups = [{"verdict": cells[c]["stats"]["unfunded"]["verdict"], "replicated": cells[c]["stats"]["unfunded"]["replicated"],
                    "IS": cells[c]["stats"]["unfunded"]["IS"]} for c in prim]
    pl_groups = [{"verdict": cells[c]["stats"]["unfunded"]["placebo_verdict"],
                  "replicated": cells[c]["stats"]["unfunded"]["placebo_replicated"],
                  "IS": cells[c]["stats"]["unfunded"]["placebo"]["IS"]} for c in prim]
    cr = {"real": candidate_rate(real_groups), "placebo": candidate_rate(pl_groups)}
    chance = cr["real"] is not None and (cr["placebo"] is not None and cr["real"] <= cr["placebo"])
    for cid in prim:
        c = cells[cid]
        c["multiple"] = {"p": pvals[cid], "holm_pass": bool(rej.get(cid)) if complete else None, "complete": complete}
        if universe == "pit":
            continue
        c["final"], c["failed_steps"] = final_status(c["target"], cid, complete=complete, holm_pass=bool(rej.get(cid)),
                                                     chance=chance)
    for cid in CONTROL_CELLS:
        if cid in cells:
            cells[cid]["final"] = "KONTROL (hipotez değil)"
    meets = [c for c in prim if (cells[c].get("target") or {}).get("meets")]
    for c in meets:
        cells[c]["book_note"] = book_note(c)
    rep: dict[str, Any] = {"universe": universe, "cells": cells, "complete": complete, "candidate_rate": cr,
                           "chance_explains": bool(chance), "holm": {"alpha": HOLM_ALPHA, "rejected": sorted(k for k, v in rej.items() if v)},
                           "strict_strong": [c for c in prim if cells[c]["stats"]["unfunded"]["verdict_strict"] == V_STRONG],
                           "meets_target": meets, "meets_target_detail": {c: book_note(c) for c in meets}}
    if pit_meta is not None:
        rep["pit"] = dict(pit_meta)
    rep["conclusion_tr"] = conclusion_tr(rep)
    return rep


BOOK_NAME_TR = {"D4": "D4", "C4": "C4", "FM": "Formasyon"}


def book_note(cid: str) -> str:
    """§8.4: hedefi karşılayan hücrenin yanında hangi defterin kaç varyantından biri olduğu."""
    book = VARIANTS[cid.split("|")[0]]["book"]
    nv = sum(1 for d in VARIANTS.values() if d["book"] == book and not d.get("control"))
    nc = sum(1 for c in PRIMARY_CELLS if VARIANTS[c.split("|")[0]]["book"] == book)
    return f"{BOOK_NAME_TR[book]} defterinin {nv} varyantından biri; defterin {nc} birincil hücresi var"


def _with_notes(cids: Iterable[str]) -> str:
    return ", ".join(f"{c} ({book_note(c)})" for c in cids)


def conclusion_tr(rep: Mapping[str, Any]) -> str:
    cells = rep["cells"]
    empty = list(rep.get("empty_symbols") or [])
    tail = f" · Arşivde verisi olmayan coin (§11.6, dışarıda): {', '.join(empty)}" if empty else ""
    if rep["universe"] == "pit":
        ok = [c for c in PRIMARY_CELLS if c in cells and cells[c].get("pit_check", {}).get("pass")]
        return "PIT evreninde geçiş ölçütünü sağlayan hücre: " + (_with_notes(ok) if ok else "yok") + tail
    finals = Counter(cells[c].get("final") for c in PRIMARY_CELLS if c in cells)
    cand = [c for c in PRIMARY_CELLS if c in cells and cells[c].get("final") == ST_CANDIDATE]
    wait = [c for c in PRIMARY_CELLS if c in cells and cells[c].get("final") == ST_PIT_WAIT]
    meets = [c for c in PRIMARY_CELLS if c in cells and (cells[c].get("target") or {}).get("meets")]
    if cand:
        return "ÖNERİ ADAYI: " + _with_notes(cand) + " — " + BOOK_REGISTRY["outcome_tr"]["some"] + tail
    if wait:
        return ("HEDEFİ KARŞILAYIP çoklu testi geçen ve PIT teyidi bekleyen hücre: " + _with_notes(wait)
                + " — öneri değildir; aynı kodla PIT teyidi koşulmalı" + tail)
    parts = ", ".join(f"{k}: {v}" for k, v in sorted(finals.items(), key=lambda kv: -kv[1]))
    fw = [c for c in PRIMARY_CELLS if c in cells and cells[c].get("final") == ST_FILTER_WAIT]
    if fw:
        return (f"Hedef hükmü verilmedi — {ST_FILTER_WAIT}: kapasite filtre tablosu yok; kapasite dışındaki şartları geçen hücre: "
                + ", ".join(fw) + f". Diğerleri — {parts}" + tail)
    if meets:
        return ("Hiçbir hücre ÖNERİ ADAYI değil. HEDEFİ KARŞILAYIP sonraki basamakta elenen: "
                + "; ".join(f"{c} ({book_note(c)}) → {cells[c].get('final')}" for c in meets) + f". Basamaklar — {parts}" + tail)
    return f"Hiçbir hücre ÖNERİ ADAYI değil: {NO_TARGET_TR}. Elendikleri basamaklar — {parts}" + tail


# ---------------------------------------------------------------------------- olay dosyası
TRADE_COLS = ["symbol", "tf", "book", "variant", "kind", "side", "name", "i", "t_ms", "period", "entry", "stop", "risk", "exit_px",
              "reason", "r", "cost_r", "hold", "entry_ms", "exit_ms", "exit_bar_ms", "gap_cross", "gap_hours", "scopes",
              "funding_raw", "funding_nan", "funding_r", "ctx", "mtm"]


def trades_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in TRADE_COLS + ["ctx.hacim", "ctx.rsi", "ctx.trend", "ctx.volatilite"]})
    df = pd.DataFrame(rows)
    for k in ("hacim", "rsi", "trend", "volatilite"):
        df["ctx." + k] = [(x or {}).get(k) if isinstance(x, dict) else None for x in df["ctx"]]
    return df


class _HashingWriter:
    """csv.writer hedefi: metni UTF-8 olarak gzip'e yazar ve SIKIŞTIRILMAMIŞ baytların sha256'sını tutar."""

    def __init__(self, fh: Any):
        self.fh, self.h = fh, hashlib.sha256()

    def write(self, s: str) -> int:
        b = s.encode("utf-8")
        self.h.update(b)
        self.fh.write(b)
        return len(s)


def write_trades(path: Path, T: pd.DataFrame) -> str:
    """İşlem dosyası (gzip; başlıkta zaman damgası ve dosya adı YOK → aynı içerik aynı bayt). Döner: sıkıştırılmamış CSV
    metninin sha256'sı (koşu kanıtı; gzip baytları kanıt sayılmaz, §4.1)."""
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        hw = _HashingWriter(gz)
        w = csv.writer(hw)
        w.writerow(TRADE_COLS)
        for rec in T[TRADE_COLS].to_dict("records"):
            w.writerow([json.dumps(rec[c] or {}, ensure_ascii=False, sort_keys=True) if c in ("ctx", "mtm")
                        else ("" if rec[c] is None or (isinstance(rec[c], float) and math.isnan(rec[c])) else rec[c]) for c in TRADE_COLS])
    tmp.replace(path)
    return hw.h.hexdigest()


def read_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, compression="gzip", keep_default_na=False, na_values=[""], dtype={"scopes": str, "period": str},
                     float_precision="round_trip")                 # yazılan float'lar bit bit geri okunur
    df["scopes"] = df["scopes"].fillna("")
    df["period"] = df["period"].replace({"": None})
    for c in ("gap_cross", "funding_nan"):
        df[c] = df[c].astype(str).str.lower().isin(("true", "1"))
    df["ctx"] = [json.loads(x) if isinstance(x, str) and x else {} for x in df["ctx"]]
    df["mtm"] = [json.loads(x) if isinstance(x, str) and x else {} for x in df["mtm"]]
    for k in ("hacim", "rsi", "trend", "volatilite"):
        df["ctx." + k] = [x.get(k) for x in df["ctx"]]
    return df


# ---------------------------------------------------------------------------- çalıştırma
REPORT_JSON = "book_lab_report{suffix}.json"
REPORT_MD = "book_lab_report{suffix}.md"
TRADES_FILE = "book_lab_trades{suffix}.csv.gz"
META_FILE = "book_lab_meta{suffix}.json"


def _suffix(universe: str) -> str:
    return "" if universe == "primary" else f"_{universe}"


def _clean(x: Any) -> Any:
    if isinstance(x, Mapping):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_clean(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(float(x)) else None
    if isinstance(x, np.ndarray):
        return _clean(x.tolist())
    if isinstance(x, pd.DataFrame):
        return None
    return x


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _map(fn: Callable[[tuple], Any], tasks: list[tuple], jobs: int) -> list[Any]:
    if jobs > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as ex:
            return list(ex.map(fn, tasks))
    return [fn(t) for t in tasks]


CODE_PATHS = ("tradingbot", "scripts")


def git_state(root: Path | None = None) -> dict[str, Any]:
    """Kod durumu: HEAD commit'i, kod ağacı özeti (git HEAD:tradingbot ve HEAD:scripts ağaçları; belge commit'i değiştirmez) ve
    bu yollarda commit edilmemiş değişiklik var mı (`git status --porcelain`, izlenmeyen dosyalar dahil). git yoksa None."""
    cwd = str(root or Path(__file__).resolve().parents[1])

    def g(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    trees = [g("rev-parse", f"HEAD:{p}") for p in CODE_PATHS]
    status = g("status", "--porcelain", "--", *CODE_PATHS)
    return {"commit": g("rev-parse", "HEAD") or None,
            "code_tree": None if any(not t for t in trees) else hashlib.sha256("|".join(trees).encode()).hexdigest()[:16],
            "dirty": None if status is None else bool(status)}


def git_commit(root: Path | None = None) -> str | None:
    return git_state(root)["commit"]


def code_match(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """İki koşu aynı kodla mı: kod ağacı biliniyor ve aynı, iki çalışma ağacı da temiz (dirty is False)."""
    return (bool(a) and bool(b) and a.get("code_tree") is not None and a.get("code_tree") == b.get("code_tree")
            and a.get("dirty") is False and b.get("dirty") is False)


def _prior_meta(out_dir: Path, universe: str) -> dict[str, Any] | None:
    p = out_dir / META_FILE.format(suffix=_suffix(universe))
    if not p.exists():
        return None
    m = json.loads(p.read_text(encoding="utf-8"))
    if m.get("registry_sha") != BOOK_REGISTRY_SHA:
        raise BookDataError(f"{p}: önceki koşu başka mühürle ({m.get('registry_sha')} ≠ {BOOK_REGISTRY_SHA}); başka bir --out seçin")
    return m


def _reaches_end(df: pd.DataFrame | None, tf: str) -> bool:
    return df is not None and len(df) > 0 and int(df["timestamp"].iloc[-1]) + tf_ms(tf) >= PRICE_END_MS


def data_problem(symbol: str, df4: pd.DataFrame, df1: pd.DataFrame | None, finfo: Mapping[str, Any], *,
                 strict: bool) -> str | None:
    """Koşuyu durduran veri sorunu (okunuş: veri hatası) ya da None. `strict`: birincil/bilgi evreni (seri PRICE_END'e ulaşmalı).
    Arşivde hiç verisi olmayan coin burada sorun sayılmaz (çağıran dışarıda bırakır ve raporlar)."""
    has4, has1 = df4 is not None and len(df4) > 0, df1 is not None and len(df1) > 0
    if strict:
        if not has4 and has1:
            return f"veri {symbol}: 4h serisi boş ama 1d serisi var (tutarsız arşiv)"
        if has4 and not _reaches_end(df4, TF4):
            return f"veri {symbol}: 4h serisi PRICE_END'den önce bitiyor (son bar {_iso(df4['timestamp'].iloc[-1])})"
        if has4 and df1 is not None and not _reaches_end(df1, TF1):
            return (f"veri {symbol}: 1d serisi PRICE_END'e ulaşmıyor (son bar "
                    f"{_iso(df1['timestamp'].iloc[-1]) if has1 else 'yok'})")
    if (_reaches_end(df4, TF4) or _reaches_end(df1, TF1)) and not int(finfo.get("post_end") or 0):
        return (f"fonlama {symbol}: [PRICE_END, {_iso(FUNDING_END_MS)}) aralığında uzlaşma yok — "
                f"{month_key(PRICE_END_MS)} aylık fundingRate dosyası henüz yayımlanmamış olabilir (arşivde günlük fonlama yok)")
    return None


def run(*, cache_dir: Path | str, out_dir: Path | str, universe: str = "primary", books: Iterable[str] = BOOKS,
        filters_path: Path | str | None = None, offline: bool = False, jobs: int = 1, fetch: Callable[[str], bytes | None] | None = None,
        now_ms: int | None = None, log: Callable[[str], None] = print, report_only: bool = False,
        symbols_override: Iterable[str] | None = None, list_text: Callable[[str], str] | None = None,
        code_state: Mapping[str, Any] | None = None, allow_code_change: bool = False) -> dict[str, Any]:
    """Koşu: veri (sabit pencereler) → sembol görevleri (işlemler) → hücreler, kapasite, hedef, çoklu test, basamaklar → rapor.
    Çıktılar: <out>/book_lab_report[_<evren>].json/.md, book_lab_trades[_<evren>].csv.gz, book_lab_meta[_<evren>].json.
    `books`: yalnız bu defterlerin işlemleri yeniden üretilir; aynı mühürlü önceki koşunun diğer defter işlemleri korunur.
    `report_only`: veri ve olay üretimi yok; kayıtlı işlemlerden rapor (filtre tablosu sonradan geldiğinde kapasite). `fetch`
    ve `list_text`: testte sahte indiriciler. `symbols_override`: YALNIZ testler için evreni değiştirir (rapora yazılır).
    Veri hatası (okunuş "veri hatası") → BookDataError; hiçbir çıktı yazılmaz. `code_state`: kod durumu (yoksa `git_state`);
    aynı --out'taki önceki koşu başka kod ağacındaysa ya da ağaçlardan biri kirliyse koşu durur (`allow_code_change` açık izin,
    rapora yazılır)."""
    if universe not in UNIVERSES:
        raise ValueError(f"bilinmeyen evren: {universe} (geçerli: {', '.join(UNIVERSES)})")
    books = [b for b in BOOKS if b in set(books)]
    if not books:
        raise ValueError("defter seçilmedi (D4, C4, FM)")
    cfg = L.LabConfig()
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sfx = _suffix(universe)
    prior = _prior_meta(out_dir, universe)
    code = dict(code_state) if code_state is not None else git_state()
    t0 = time.time()
    cache = ZipCache(cache_dir, fetch=fetch, offline=offline, now_ms=now_ms)
    meta: dict[str, Any] = dict(prior or {})
    if prior is not None and not code_match(prior.get("code"), code):
        msg = (f"{out_dir}: önceki koşu başka kod durumunda ya da kirli ağaçta (önce {prior.get('code')}, şimdi {code}); "
               "birleştirme ve yalnız-rapor yapılmaz — başka bir --out seçin")
        if not allow_code_change:
            raise BookDataError(msg)
        log("UYARI: " + msg + " (açık izinle sürüyor; rapora yazılır)")
        meta["code_overrides"] = list(meta.get("code_overrides") or []) + [{"before": prior.get("code"), "now": code,
                                                                             "at": _iso(now_ms)}]
    meta.update({"registry_sha": BOOK_REGISTRY_SHA, "version": VERSION, "universe": universe, "code": code})
    pit_membership = None
    trades_path = out_dir / TRADES_FILE.format(suffix=sfx)
    if report_only:
        if prior is None or not trades_path.exists():
            raise BookDataError(f"{out_dir}: kayıtlı işlem yok; önce tam koşu")
        T = read_trades(trades_path)
        pit_membership = (prior.get("pit") or {}).get("universe")
    else:
        if universe == "pit":
            pit_info = _pit_setup(cache, list_text, offline, log)
            pit_membership = pit_info["universe"]
            syms = sorted({s for ss in pit_membership.values() for s in ss}, key=lambda s: (s != BTC_SYMBOL, s))
            meta["pit"] = {k: v for k, v in pit_info.items() if k != "qv"}
        else:
            syms = list(PRIMARY_UNIVERSE if universe == "primary" else INFO_UNIVERSE)
        if symbols_override is not None:
            syms = list(symbols_override)
            meta["symbols_overridden"] = True
        use1 = any(VARIANTS[v]["tf"] == TF1 for v in VARIANTS if VARIANTS[v]["book"] in books) or \
            any("COIN_UP" in VARIANTS[v]["filters"] for v in VARIANTS if VARIANTS[v]["book"] in books)
        need1 = use1 or universe == "pit"                 # PIT: eksik çift denetimi her defter seçiminde 1d ister
        strict = universe != "pit"                        # birincil/bilgi: seri PRICE_END'e ulaşmalı (DELISTED yalnız PIT'te)
        frames4, frames1, quality, fund_info, empty = {}, {}, {}, {}, []
        try:
            btc1 = load_klines(BTC_SYMBOL, TF1, START_1D_MS, PRICE_END_MS, cache.get, now_ms)
        except (ConnectionError, OSError, ValueError, zipfile.BadZipFile) as exc:
            raise BookDataError(f"veri {BTC_SYMBOL} 1d: {type(exc).__name__}: {str(exc)[:200]} — koşu ÇALIŞTIRILMADI") from exc
        if not _reaches_end(btc1, TF1):
            raise BookDataError(f"BTCUSDT 1d serisi boş ya da PRICE_END'e ulaşmıyor: BTC_UP/BTC_DOWN hesaplanamaz ({len(btc1)} bar)")
        quality[f"{BTC_SYMBOL} {TF1}"] = series_quality(btc1, TF1, START_1D_MS)
        btc_daily = regime_daily(btc1)
        tasks = []
        for s in syms:
            try:
                df4 = load_klines(s, TF4, START_4H_MS, PRICE_END_MS, cache.get, now_ms)
                df1 = (btc1 if s == BTC_SYMBOL else load_klines(s, TF1, START_1D_MS, PRICE_END_MS, cache.get, now_ms)) if need1 else None
                fraw, finfo = load_funding(s, cache.get)
            except (ConnectionError, OSError, ValueError, zipfile.BadZipFile) as exc:
                log(f"veri {s}: HATA {type(exc).__name__}: {str(exc)[:160]}")
                raise BookDataError(f"veri {s}: {type(exc).__name__}: {str(exc)[:200]} — koşu ÇALIŞTIRILMADI (eksik evrenle rapor "
                                    "üretilmez; aynı kodla yeniden denenir, §0.6)") from exc
            frames4[s], frames1[s] = df4, df1
            quality[f"{s} {TF4}"] = series_quality(df4, TF4, START_4H_MS)
            if df1 is not None:
                quality[f"{s} {TF1}"] = series_quality(df1, TF1, START_1D_MS)
            fund_info[s] = finfo
            log(f"veri {s}: 4h {len(df4)} bar · 1d {0 if df1 is None else len(df1)} bar · fonlama {finfo['rows']} uzlaşma")
            if not len(df4) and (df1 is None or not len(df1)):
                empty.append(s)                           # arşivde verisi yok: dışarıda kalır, sonuçta yazılır (§11.6)
                continue
            problem = data_problem(s, df4, df1, finfo, strict=strict)
            if problem:
                raise BookDataError(problem + " — koşu ÇALIŞTIRILMADI; aynı kodla yeniden denenir (§0.6)")
            d1 = df1 if use1 and df1 is not None and len(df1) else None
            tasks.append((s, df4 if len(df4) else None, d1, btc_daily, fraw, tuple(books), dataclasses.asdict(cfg), None))
        meta["empty_symbols"] = empty
        if universe == "pit":
            meta["pit"]["missing_pairs"] = pit_missing_pairs(pit_membership, frames4, frames1)
        res = _map(_symbol_task, tasks, jobs)
        rows = [r for rr, _ in res for r in rr]
        T_new = trades_frame(rows)
        if prior is not None and trades_path.exists():
            T_old = read_trades(trades_path)
            T = pd.concat([T_old[~T_old["book"].isin(books)], T_new], ignore_index=True) if len(T_old) else T_new
        else:
            T = T_new
        prev_q = dict((prior or {}).get("quality") or {})
        changed = {k: {"before": prev_q[k].get("sha256"), "now": v.get("sha256")} for k, v in quality.items()
                   if k in prev_q and prev_q[k].get("sha256") and v.get("sha256") and prev_q[k]["sha256"] != v["sha256"]}
        meta["digest_changes"] = {**(meta.get("digest_changes") or {}), **changed}
        sm = dict(meta.get("series") or {})
        for _, m_ in res:
            sm.setdefault(m_["symbol"], {}).update({b: m_ for b in books})
        meta["series"] = sm
        meta["quality"] = {**(meta.get("quality") or {}), **quality}
        meta["funding"] = {**(meta.get("funding") or {}), **fund_info}
        meta["coverage"] = coverage(frames4)
        meta["books_done"] = sorted(set(meta.get("books_done") or []) | set(books))
        meta["archive_requests"] = cache.requests
        meta["trades_sha256"] = write_trades(trades_path, T)
        meta["trades_sha256_basis"] = "sıkıştırılmamış CSV metni"
    syms_u = list(PRIMARY_UNIVERSE if universe == "primary" else INFO_UNIVERSE)
    filters = None if universe == "pit" else load_filters(filters_path, syms_u if not meta.get("symbols_overridden")
                                                          else sorted(set(T["symbol"])) if len(T) else [])
    rep = build_report(T, cfg, universe=universe, filters=filters, pit_membership=pit_membership,
                       pit_meta=meta.get("pit"), books=meta.get("books_done") or books)
    rep.update({"kind": "BOOK_LAB", "version": VERSION, "registry_sha": BOOK_REGISTRY_SHA, "doc": DOC, "generated_at": _iso(now_ms),
                "config": dataclasses.asdict(cfg), "cost_round_trip_pct": round(2 * cfg.cost_per_side * 100, 3),
                "books": meta.get("books_done") or books, "filters": {k: v for k, v in (filters or {}).items() if k != "table"},
                "data_quality": meta.get("quality"), "funding": {s: {k: v for k, v in f.items() if k != "files_sha256"}
                                                                 for s, f in (meta.get("funding") or {}).items()},
                "funding_files_sha256": {s: f.get("files_sha256") for s, f in (meta.get("funding") or {}).items()},
                "coverage": meta.get("coverage"), "series_counts": meta.get("series"), "trades_sha256": meta.get("trades_sha256"),
                "symbols_overridden": bool(meta.get("symbols_overridden")), "seconds": round(time.time() - t0, 1),
                "readings_tr": list(READINGS_TR), "code": meta.get("code"), "code_overrides": meta.get("code_overrides") or [],
                "empty_symbols": meta.get("empty_symbols") or []})
    rep["conclusion_tr"] = conclusion_tr(rep)
    for cid, c in rep["cells"].items():
        c["counts_detail"] = cell_counts(meta.get("series"), cid)
    rep["digest_changes"] = meta.get("digest_changes") or {}
    ap = out_dir / "book_lab_attempts.jsonl"
    rep["attempts"] = [json.loads(x) for x in ap.read_text(encoding="utf-8").splitlines() if x.strip()] if ap.exists() else []
    others = _other_reports(out_dir, universe)
    if universe == "primary":
        apply_pit(rep, others.get("pit"))
    rep = _clean(rep)
    _write_text(out_dir / META_FILE.format(suffix=sfx), json.dumps(_clean(meta), ensure_ascii=False, indent=1))
    _write_text(out_dir / REPORT_JSON.format(suffix=sfx), json.dumps(rep, ensure_ascii=False, indent=1))
    _write_text(out_dir / REPORT_MD.format(suffix=sfx), render_md(rep, others=others))
    return rep


def _pit_setup(cache: ZipCache, list_text: Callable[[str], str] | None, offline: bool, log: Callable[[str], None]) -> dict[str, Any]:
    """PIT evreni: liste (önbellekte kalıcı) → havuz → aylık ilk 40."""
    lp = cache.root.parent / "pit_listing.json"
    if lp.exists():
        names = json.loads(lp.read_text(encoding="utf-8"))
    else:
        if offline:
            raise BookDataError(f"{ST_PIT_UNVERIFIED}: PIT listesi önbellekte yok (çevrimdışı)")
        try:
            names = list_um_symbols(list_text or (lambda u: (http_get(u) or b"").decode("utf-8")))
        except Exception as exc:  # noqa: BLE001 — liste alınamazsa teyit yapılamaz
            raise BookDataError(f"{ST_PIT_UNVERIFIED}: sembol listesi alınamadı: {exc}") from exc
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(json.dumps(names), encoding="utf-8")
    pool, excl = pit_pool(names)
    months = _month_range(month_key(month_start_ms(PIT_MONTHS[0]) - (PIT_DAYS + 1) * DAY_MS), month_key(month_start_ms(PIT_MONTHS[-1]) - 1))

    def qv(sym: str) -> pd.DataFrame:
        parts = []
        for mk in months:
            d = cache.get(f"{L.ARCHIVE_BASE}/monthly/klines/{sym}/1d/{sym}-1d-{mk}.zip")
            if d is not None:
                parts.append(parse_daily_qv(d))
        return pd.concat(parts).drop_duplicates("timestamp").sort_values("timestamp") if parts else None

    with ThreadPoolExecutor(max_workers=8) as ex:
        loaded = dict(zip(pool, ex.map(qv, pool)))
    uni = pit_universe(pool, lambda s: loaded.get(s))
    log(f"PIT: liste {len(names)} · havuz {len(pool)} · dışlanan {len(excl)} · seçilen {len({s for v in uni.values() for s in v})}")
    return {"universe": uni, "listing_sha256": hashlib.sha256(json.dumps(names).encode()).hexdigest(), "listing_n": len(names),
            "pool_n": len(pool), "excluded": excl, "selected": sorted({s for v in uni.values() for s in v})}


def coverage(frames4: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """Dönem başına: en az bir barı olan coin, dönemi tamamen kapsayan coin, coin başına ilk bar."""
    out: dict[str, Any] = {"first_bar": {}}
    for per, (a, b) in (("IS", (DECISION_START_MS, IS_END_MS)), ("OOS", (IS_END_MS, PRICE_END_MS))):
        any_, full = 0, 0
        for s, df in frames4.items():
            if df is None or not len(df):
                continue
            ts = df["timestamp"].to_numpy(dtype=np.int64)
            t = ts + tf_ms(TF4)
            any_ += int(((t >= a) & (t < b)).any())
            full += int(ts[0] + tf_ms(TF4) <= a and ts[-1] + tf_ms(TF4) >= b)
        out[per] = {"coins_any": any_, "coins_full": full}
    for s, df in frames4.items():
        out["first_bar"][s] = _iso(df["timestamp"].iloc[0]) if df is not None and len(df) else None
    return out


def _other_reports(out_dir: Path, universe: str) -> dict[str, Any]:
    if universe != "primary":
        return {}
    out = {}
    for u in ("info", "pit"):
        p = out_dir / REPORT_JSON.format(suffix=_suffix(u))
        if p.exists():
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if r.get("registry_sha") == BOOK_REGISTRY_SHA:
                out[u] = r
    return out


# ---------------------------------------------------------------------------- markdown
def _f(x: Any, nd: int = 2) -> str:
    return "—" if x is None else f"{float(x):+.{nd}f}"


def _ci(ci: Any) -> str:
    return f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "—"


def _pct(x: Any) -> str:
    return "—" if x is None else f"%{100 * float(x):.1f}"


def render_md(rep: Mapping[str, Any], others: Mapping[str, Any] | None = None) -> str:
    """Kısa, düz Türkçe rapor."""
    u = rep.get("universe")
    out = [f"# Defter araştırması — {rep.get('version')} raporu ({u} evren)", "",
           f"Ön kayıt mührü `BOOK_REGISTRY_SHA = {rep.get('registry_sha')}` · belge `{rep.get('doc')}` · defterler "
           f"{', '.join(rep.get('books') or [])} · gidiş-dönüş maliyet %{rep.get('cost_round_trip_pct')}", "",
           "Geçmiş test; PAPER değil, canlı değil. Kâr garantisi değildir. Hiçbir defter, strateji ya da config değişmedi.", ""]
    if rep.get("symbols_overridden"):
        out += ["**UYARI: evren ön kayıttan FARKLI (yalnız test için); bu rapor ön kayıtlı koşu DEĞİLDİR.**", ""]
    code = rep.get("code") or {}
    out += [f"Kod: commit `{str(code.get('commit'))[:12]}` · kod ağacı `{code.get('code_tree')}` · çalışma ağacı "
            + ("temiz" if code.get("dirty") is False else "**KİRLİ ya da bilinmiyor (ön kayıtlı koşu sayılmaz)**"), ""]
    if rep.get("code_overrides"):
        out += ["**UYARI: önceki koşu başka kod durumundaydı; birleştirme açık izinle yapıldı:** "
                + "; ".join(f"{o.get('at')}: {o.get('before')} → {o.get('now')}" for o in rep["code_overrides"]), ""]
    out += ["## Sonuç", "", f"- {rep.get('conclusion_tr')}", ""]
    if rep.get("meets_target_detail"):
        out += ["- Hedefi karşılayan hücreler (§8.4): " + "; ".join(f"{c} — {n}" for c, n in rep["meets_target_detail"].items()), ""]
    if rep.get("pit_not_applied"):
        out += [f"- PIT sonucu yazılmadı: {rep['pit_not_applied']}", ""]
    if rep.get("empty_symbols"):
        out += [f"- Arşivde verisi olmayan coin (dışarıda): {', '.join(rep['empty_symbols'])}", ""]
    flt = rep.get("filters") or {}
    if u != "pit":
        out += [f"- Kapasite filtre tablosu: " + (f"verified_at {flt.get('verified_at')} · sha256 {flt.get('sha256')}" if flt.get("ok")
                                                   else f"{ST_FILTER_WAIT} (eksik: {', '.join((flt.get('missing') or [])[:8])})"), ""]
    cr = rep.get("candidate_rate") or {}
    out += [f"Aday oranı (fonlamasız): gerçek {_pct(cr.get('real'))} · plasebo {_pct(cr.get('placebo'))}"
            + (f" → {ST_CHANCE}" if rep.get("chance_explains") else "")
            + f" · sıkı GÜÇLÜ ADAY (fonlamasız): {len(rep.get('strict_strong') or [])} hücre · Holm'da reddedilen: "
            f"{', '.join((rep.get('holm') or {}).get('rejected') or []) or 'yok'}", ""]
    out += ["## Hücreler", "",
            "Ort.R keşif/doğrulama; plaseboya göre fark ve doğrulama farkının gün kümeli %95 aralığı; hüküm standart/sıkı "
            "(fonlamasız · fonlamalı); kapasiteli doğrulama aylık % (BUMP / NO_BUMP, fonlamalı) ve aralık; Holm p.", "",
            "| hücre | n gerçek | n plasebo | ort.R | fark | fark %95 | hüküm | fonlamalı | aylık sade | aylık kap. BUMP/NO_BUMP "
            "| %95 (BUMP) | p | durum |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cid, c in (rep.get("cells") or {}).items():
        su, sf = c["stats"]["unfunded"], c["stats"]["funded"]
        vs = su.get("vs_placebo")
        dci = (su.get("diff_ci95") or {}).get("OOS")
        cap = c.get("capacity") or {}
        capt = "—"
        ci_b = "—"
        if "BUMP" in cap or "NO_BUMP" in cap:
            capt = "/".join(_f((cap.get(m) or {}).get("funded", {}).get("OOS", {}).get("mean_pct")) for m in CAP_MODES if m in cap)
            ci_b = _ci(((cap.get("BUMP") or cap.get("NO_BUMP") or {}).get("funded") or {}).get("OOS", {}).get("ci95"))
        elif cap.get("status"):
            capt = cap["status"]
        mp = (c.get("monthly_plain") or {}).get("funded", {}).get("OOS", {}).get("mean_pct")
        pv = (c.get("multiple") or {}).get("p")
        state = c.get("final") or ("PIT: " + ((c.get("pit_check") or {}).get("status") or "—"))
        if c.get("flags"):
            state += " · " + ", ".join(c["flags"])
        out.append(f"| {cid} | {c['counts']['real']['IS']}/{c['counts']['real']['OOS']} | {c['counts']['placebo']['IS']}/"
                   f"{c['counts']['placebo']['OOS']} | {_f(su['IS'].get('mean_r'), 3)}/{_f(su['OOS'].get('mean_r'), 3)} | "
                   f"{(_f(vs.get('IS')) + '/' + _f(vs.get('OOS'))) if vs else '—'} | {_ci(dci)} | {su['verdict']} / "
                   f"{su['verdict_strict']} | {sf['verdict']} / {sf['verdict_strict']} | {_f(mp)} | {capt} | {ci_b} | "
                   f"{'—' if pv is None else format(float(pv), '.4f')} | {state} |")
    out += ["", "### Hücre ayrıntısı", "",
            "Sansür payı (DATA_END) keşif/doğrulama; BAD_BAR payı; gerçek işlemlerde NaN fonlama payı (doğrulama); simüle edilen "
            "plasebo ve NO_REAL_RISK payı (payda: risk geometrisinden önce seçilen plasebo; C4'te üretilen + NO_REAL_RISK + BAD_STOP); "
            "kapasite (BUMP): kabul/aday, çıkarılan, ret nedenleri, eşzamanlı en çok; kapasiteli keşif aylık % (fonlamalı, "
            "BUMP/NO_BUMP); hedef şartlarından düşenler.", "",
            "| hücre | sansür | BAD_BAR | NaN fonlama | plasebo / NO_REAL_RISK | kapasite BUMP | keşif aylık kap. | düşen şartlar |",
            "|---|---|---|---|---|---|---|---|"]
    for cid, c in (rep.get("cells") or {}).items():
        cd = c.get("counts_detail") or {}
        cap = c.get("capacity") or {}
        bm = cap.get("BUMP") or cap.get("NO_BUMP") or {}
        capd = (f"{bm.get('accepted')}/{bm.get('candidates')} · çıkarılan {bm.get('bumped')} · {bm.get('rejected')} · en çok "
                f"{(bm.get('concurrency') or {}).get('ALL', {}).get('max')}") if bm else (cap.get("status") or "—")
        isk = "/".join(_f((cap.get(m) or {}).get("funded", {}).get("IS", {}).get("mean_pct")) for m in CAP_MODES if m in cap) or "—"
        cs = c.get("censor_share") or {}
        out.append(f"| {cid} | {_pct(cs.get('IS'))}/{_pct(cs.get('OOS'))} | {_pct(c.get('bad_share'))} | "
                   f"{_pct((c.get('nan_funding_share_real') or {}).get('OOS'))} | {cd.get('placebo_drawn', '—')} / "
                   f"{_pct(cd.get('no_real_risk_share'))} | {capd} | {isk} | {', '.join((c.get('target') or {}).get('failed') or []) or '—'} |")
    out += ["", "## Veri", ""]
    cov = rep.get("coverage") or {}
    if cov:
        out += [f"- Coin sayısı: keşif en az bir bar {cov.get('IS', {}).get('coins_any')} · dönemi kapsayan "
                f"{cov.get('IS', {}).get('coins_full')}; doğrulama {cov.get('OOS', {}).get('coins_any')} · "
                f"{cov.get('OOS', {}).get('coins_full')}.", ""]
    q = rep.get("data_quality") or {}
    if q:
        out += ["| seri | bar | ilk | son | eksik | boşluk (24s+) | bozuk | sha256 |", "|---|---|---|---|---|---|---|---|"]
        for k, v in q.items():
            if "bars" not in v:
                out.append(f"| {k} | HATA {v.get('error')} | | | | | | |")
                continue
            out.append(f"| {k} | {v['bars']} | {v.get('first') or '—'} | {v.get('last') or '—'} | {_pct(v.get('missing_ratio'))} | "
                       f"{v.get('n_gaps', 0)} ({v.get('n_gaps_over_24h', 0)}) | {v.get('bad_bars', 0)} | {str(v.get('sha256'))[:16]} |")
        out.append("")
    if rep.get("pit"):
        p = rep["pit"]
        mp_ = p.get("missing_pairs") or {}
        out += ["## PIT evreni", "", f"- Liste {p.get('listing_n')} sembol · havuz {p.get('pool_n')} · seçilen "
                f"{len(p.get('selected') or [])} · eksik çift payı {_pct(mp_.get('share'))} ("
                + ("yeterli" if mp_.get("ok") else ST_PIT_UNVERIFIED) + ")", ""]
    for name, r in (others or {}).items():
        out += [f"## {'Bilgi evreni (40 coin; hükme girmez)' if name == 'info' else 'PIT teyidi'}", "", f"- {r.get('conclusion_tr')}", ""]
    if rep.get("digest_changes"):
        out += ["## Bayt özeti farkları (önceki koşuya göre)", ""] + [f"- {k}: {v.get('before')} → {v.get('now')}"
                                                                     for k, v in rep["digest_changes"].items()] + [""]
    if rep.get("attempts"):
        out += ["## Koşu denemeleri (§0.7)", ""] + [f"- {a.get('attempt')} · {a.get('started_at')} · {a.get('status')} · commit "
                                                    f"{str(a.get('commit'))[:12]} · kod ağacı {a.get('code_tree')}"
                                                    + ("" if a.get("dirty") is False else " · KİRLİ/bilinmiyor")
                                                    + f" · mühür {a.get('registry_sha')}"
                                                    + (f" · {a.get('error')}" if a.get("error") else "") for a in rep["attempts"]] + [""]
    out += ["Belirsiz kuralların okunuşu `BOOK_REGISTRY['readings_tr']` içindedir (mühre dahil).", ""]
    return "\n".join(out)


__all__ = ["BOOK_REGISTRY", "BOOK_REGISTRY_SHA", "BOOKS", "BookDataError", "C4_VARIANTS", "CV_ORDER", "D4_VARIANTS", "FM_VARIANTS",
           "PRIMARY_CELLS", "PRIMARY_UNIVERSE", "INFO_UNIVERSE", "READINGS_TR", "Series", "VARIANTS", "ZipCache", "book_note",
           "build_report", "c4_series_rows", "capacity_run", "clean_ends", "code_match", "conclusion_tr", "d4_series_rows",
           "data_problem", "final_status", "fm_series_rows", "git_state", "holm", "load_filters", "load_funding", "load_klines",
           "month_key", "monthly", "mt_pvalue", "period_of", "pit_missing_pairs", "pit_pool", "pit_universe", "process_symbol",
           "read_trades", "regime_daily", "regime_values", "render_md", "run", "series_quality", "simulate_trade", "write_trades"]
