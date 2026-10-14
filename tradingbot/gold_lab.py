# -*- coding: utf-8 -*-
"""ALTIN LABORATUVARI — gold_v1 (docs/GOLD_LAB_V1.md; 2026-10-04): "15 dakikada EMA 20/50 kesişimi" ve SMC kavramları
(order block, FVG, BOS, CHoCH, likidite süpürmesi) altında maliyet sonrası kazandırıyor mu; mevcut laboratuvar setleri altında
ne veriyor? Salt araştırma: defterlere, stratejilere, ajanlara ve parametrelere DOKUNMAZ; yalnız geçmiş mumları okur, rapor
yazar. İçe aktarılırken ağa çıkmaz.

* ÖN KAYIT: bütün kural sabitleri ve metinleri (16 varyant, pivot, tampon, HTF, seans, SMC kurulum/iptal, plasebo, maliyet,
  dönem bölmesi, aylık hedef ölçüsü, veri pencereleri, bölümler) ve belgenin belirsiz yerlerinin okunuşu (`readings_tr`)
  `GOLD_REGISTRY`'dedir; `GOLD_REGISTRY_SHA` testle sabitlenir ve belgeye yazılır. Değişiklik = yeni sürüm + yeni deneme.
* Olaylar `signal_lab.Event` (aile "gold", ad = varyant adı); simülasyon `signal_lab.simulate` (varyantın RR'si ve süre
  sınırıyla `dataclasses.replace`), bağlam `signal_lab.context`, hüküm `signal_lab.aggregate` / `verdict_strict` DEĞİŞMEDEN.
* SHORT, LONG kuralının fiyat aynasıdır: (o, h, l, c) → (−o, −l, −h, −c) üzerinde LONG kuralı koşar, stop ve tetik geri
  çevrilir (yüksek ↔ düşük, boğa ↔ ayı, > ↔ <). Aynalık böylece tanım gereği birebirdir.
* Eşleştirilmiş plasebo `PLACEBO_<ad>`: bar başına u = crc32("<sembol>|<dilim>|<ad>|<yön>|<zaman damgası>") / 2³²,
  u < p (p = aynı seri ve yöndeki gerçek sinyal sayısı / bar sayısı); stop varyantın plasebo stop'u, tetik kapanış.
* Veri: Binance spot arşivi (PAXGUSDT; 2025'ten itibaren zaman damgaları MİKROSANİYE → ms), USDⓈ-M arşivi
  (`signal_lab.ArchiveProvider`; XAUUSDT/PAXGUSDT vadeli, fonlama yalnız bilgi), Dukascopy XAUUSD BID 1 dakikalık bi5
  aynası (LZMA alone; 24 baytlık big-endian kayıt: saniye, açılış, kapanış, düşük, yüksek ×0,001, hacim float32).

PAPER/geçmiş testtir; kâr garantisi değildir.
"""
from __future__ import annotations

import csv
import dataclasses
import gzip
import hashlib
import json
import lzma
import math
import re
import time
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd

from . import signal_lab as L
from .timeframes import tf_ms

LONG, SHORT = L.LONG, L.SHORT
SIDES = (LONG, SHORT)
GOLD_VERSION = "gold_v1"
GOLD_DOC = "docs/GOLD_LAB_V1.md"
FAMILY = "gold"
PLACEBO_FAMILY = "placebo"
PLACEBO_PREFIX = "PLACEBO_"
DAY_MS = 86_400_000
H4_MS = tf_ms("4h")

# ---------------------------------------------------------------------------- ön kayıtlı sabitler
PIVOT_K = 3                      # fraktal: önceki 3 ve sonraki 3 bar; teyit j+3 kapanışında
BUF_ATR = 0.1                    # tampon b = 0,1 × ATR(karar barı)
SWING_BARS = 10                  # EMA_X_SWING stop'u: i−9..i
SETUP_BARS = 20                  # SMC_BOS_OB / SMC_FVG giriş penceresi
ATR_STOP = 1.5                   # EMA_X_ATR stop'u
FVG_BODY_ATR = 1.0               # FVG orta barının gövdesi ≥ 1 ATR(m−1)
EMA_FAST, EMA_SLOW = 20, 50
HTF_TF, HTF_EMA = "4h", 50
SESSION_MIN = (7 * 60, 20 * 60)  # [07:00, 20:00) UTC, karar anında
RISK_PCT, RISK_PCT_INFO = 0.5, 1.0
TARGET_PCT_MONTH = 1.0
MONTH_SEED = 20261004
MONTH_MIN_FOR_CI = 5
MAKER_FEE_PCT, MAKER_SLIPPAGE_BPS = 0.02, 0.0
VENUE_MIN_N = 20
DUKA_COVERAGE_MIN = 0.95
NOTE_VENUE = "mekânda tutmadı"
NOTE_DUKA = "uzun geçmişte tutmadı"
NO_TARGET_TR = "bu taktikler altında maliyet sonrası kazandırmadı"

_A, _B = "A_EMA", "B_SMC"
_EMA_HOLD = {"15m": 96, "1h": 48}
_SMC_HOLD = {"1h": 72, "4h": 30}
_EMA_ENTRY = ("Bar i'de EMA20 EMA50'yi yukarı keser (EMA20[i] > EMA50[i] ve EMA20[i−1] ≤ EMA50[i−1]) → LONG; aşağı keser "
              "(EMA20[i] < EMA50[i] ve EMA20[i−1] ≥ EMA50[i−1]) → SHORT")
_SWING_STOP = "LONG: i−9..i barlarının en düşüğü − b; SHORT: i−9..i barlarının en yükseği + b"
_PIVOT_STOP = "son teyitli swing düşük − b (SHORT: son teyitli swing yüksek + b)"
#: kural adı → tanım. `placebo_stop`: SWING10 (son 10 barın ucu ∓ b), ATR (kapanış ∓ 1,5 ATR), BAR (o barın ucu ∓ b),
#: PIVOT (son teyitli swing ∓ b).
RULES: dict[str, dict[str, Any]] = {
    "EMA_X_SWING": {
        "family": _A, "tfs": ["15m", "1h"], "rr": 2.5, "max_hold_bars": dict(_EMA_HOLD), "entry_tr": _EMA_ENTRY,
        "stop_tr": _SWING_STOP, "trigger_tr": "karar barının kapanışı (close[i])", "filters": [],
        "placebo_stop": "SWING10", "placebo_stop_tr": "son 10 barın (i−9..i) uç değeri ∓ b"},
    "EMA_X_ATR": {
        "family": _A, "tfs": ["15m", "1h"], "rr": 2.5, "max_hold_bars": dict(_EMA_HOLD), "entry_tr": "EMA_X_SWING ile aynı",
        "stop_tr": "close[i] ∓ 1,5 × ATR[i] (LONG −, SHORT +)", "trigger_tr": "karar barının kapanışı (close[i])",
        "filters": [], "placebo_stop": "ATR", "placebo_stop_tr": "close[i] ∓ 1,5 × ATR[i]"},
    "EMA_X_SWING_HTF": {
        "family": _A, "tfs": ["15m", "1h"], "rr": 2.5, "max_hold_bars": dict(_EMA_HOLD),
        "entry_tr": "EMA_X_SWING + yalnız son KAPANMIŞ 4h barında kapanış > EMA50(4h) iken LONG, < iken SHORT",
        "stop_tr": "EMA_X_SWING ile aynı", "trigger_tr": "karar barının kapanışı (close[i])", "filters": ["HTF"],
        "placebo_stop": "SWING10", "placebo_stop_tr": "son 10 barın (i−9..i) uç değeri ∓ b"},
    "EMA_X_SWING_SESSION": {
        "family": _A, "tfs": ["15m", "1h"], "rr": 2.5, "max_hold_bars": dict(_EMA_HOLD),
        "entry_tr": "EMA_X_SWING + karar anı (bar kapanışı) 07:00–20:00 UTC arasında",
        "stop_tr": "EMA_X_SWING ile aynı", "trigger_tr": "karar barının kapanışı (close[i])", "filters": ["SESSION"],
        "placebo_stop": "SWING10", "placebo_stop_tr": "son 10 barın (i−9..i) uç değeri ∓ b"},
    "SMC_BOS_OB": {
        "family": _B, "tfs": ["1h", "4h"], "rr": 3.0, "max_hold_bars": dict(_SMC_HOLD),
        "entry_tr": ("BOS: kapanış son teyitli swing yükseği (H) yukarı kırar (close[i] > H ve close[i−1] ≤ H). Order block: son "
                     "teyitli swing düşüğün barından i'ye kadar olan barlar içinde, BOS'tan önceki SON ayı mumu (close < open); "
                     "yoksa kurulum yok. Bölge [OB_low, OB_high]. Giriş: sonraki 20 bar içinde low ≤ OB_high ve close ≥ OB_low "
                     "olan İLK bar k'da karar verilir. Bu 20 bar içinde daha önce close < OB_low olursa kurulum iptal. Aynı order "
                     "block'tan en çok bir işlem."),
        "stop_tr": "OB_low − b", "trigger_tr": "OB_high", "filters": [],
        "placebo_stop": "PIVOT", "placebo_stop_tr": _PIVOT_STOP},
    "SMC_SWEEP": {
        "family": _B, "tfs": ["1h", "4h"], "rr": 3.0, "max_hold_bars": dict(_SMC_HOLD),
        "entry_tr": "Son teyitli swing düşük S için low[i] < S ve close[i] > S (likidite süpürmesi). Aynı pivot en çok bir kez kullanılır.",
        "stop_tr": "low[i] − b", "trigger_tr": "close[i]", "filters": [],
        "placebo_stop": "BAR", "placebo_stop_tr": "o barın düşüğü − b (SHORT: yükseği + b)"},
    "SMC_CHOCH": {
        "family": _B, "tfs": ["1h", "4h"], "rr": 3.0, "max_hold_bars": dict(_SMC_HOLD),
        "entry_tr": ("Düşüş yapısı: son iki teyitli swing yüksek azalan VE son iki teyitli swing düşük azalan. Bu yapıdayken "
                     "close[i] > son teyitli swing yüksek ve close[i−1] ≤ o (karakter değişimi)."),
        "stop_tr": "Son teyitli swing düşük − b", "trigger_tr": "close[i]", "filters": [],
        "placebo_stop": "PIVOT", "placebo_stop_tr": _PIVOT_STOP},
    "SMC_FVG": {
        "family": _B, "tfs": ["1h", "4h"], "rr": 3.0, "max_hold_bars": dict(_SMC_HOLD),
        "entry_tr": ("Boğa FVG bar m'de: low[m] > high[m−2], bar m−1 boğa ve gövdesi ≥ 1 ATR(m−1). Bölge [high[m−2], low[m]]. "
                     "Giriş: sonraki 20 bar içinde low ≤ low[m] ve close ≥ high[m−2] olan İLK bar k'da karar verilir. Bu 20 bar "
                     "içinde daha önce close < high[m−2] olursa kurulum iptal."),
        "stop_tr": "high[m−2] − b", "trigger_tr": "low[m]", "filters": [],
        "placebo_stop": "PIVOT", "placebo_stop_tr": _PIVOT_STOP},
}
EMA_NAMES = tuple(n for n, r in RULES.items() if r["family"] == _A)
SMC_NAMES = tuple(n for n, r in RULES.items() if r["family"] == _B)

#: Belgenin belirsiz yerleri için EN HARFİYEN okunuş (kodun yaptığı tam olarak budur; mühre girer).
READINGS_TR = (
    "seans: '07:00–20:00 UTC arasında' = karar anı T (bar kapanışı = açılış + dilim) için UTC saat T ∈ [07:00, 20:00); 07:00 "
    "dahil, 20:00 hariç",
    "HTF: 4h barları karar diliminin KENDİ serisinden UTC'ye hizalı 4 saatlik kutulara yeniden örneklenir (sol etiket, kapanış = "
    "kutudaki son kapanış, yalnız verisi olan kutular); kutu ancak başlangıç + 4h ≤ T iken kapanmıştır; EMA50(4h) bu kapanışlar "
    "üzerinde adjust=False, min_periods=50; eşitlik ya da tanımsız EMA → işlem yok",
    "EMA: pandas ewm(span, adjust=False, min_periods=span) (laboratuvarın ema50'si gibi); tanımsız değerle kesişim yok",
    "pivot: bar j'nin yükseği j−3..j−1 ve j+1..j+3 yükseklerinin HEPSİNDEN kesin büyük (eşitlik pivot değil; düşük simetrik); "
    "'son teyitli' = j + 3 ≤ i olan en büyük j; serinin ilk ve son 3 barı pivot olamaz",
    "tampon: b = 0,1 × ATR[karar barı]; SMC_BOS_OB ve SMC_FVG'de karar barı giriş barı k'dır (kurulum barı değil)",
    "sinyal için karar barında ATR sonlu ve > 0 olmalı (b tanımlı); değilse sinyal yok, kurulum biter, pivot/order block "
    "harcanmaz; EMA_X_SWING'de ayrıca i−9..i (10 bar) bulunmalı",
    "SMC_BOS_OB: H ve swing düşük, BOS barı i itibarıyla son teyitli olanlardır; order block arama aralığı [swing düşük barı, i−1] "
    "(BOS barı i 'BOS'tan önceki' olmadığı için hariç); bölge = o mumun [low, high] (fitilden fitile); giriş penceresi i+1..i+20; "
    "her barda önce giriş koşulu, sonra iptal (close < OB_low) denetlenir; aynı anda birden çok bekleyen kurulum olabilir; aynı "
    "order block'un kurulumları aynı barda dokunursa o barda en çok BİR sinyal",
    "'aynı order block'tan en çok bir işlem' = harfiyen İŞLEM: sinyal signal_lab.simulate'te (laboratuvar ayarı, varyantın RR'si ve "
    "süresi) işleme dönüşürse order block harcanır; atlanan sinyal (CHASE, STOP_TOO_CLOSE/FAR, NO_FUTURE_DATA) harcamaz ve aynı "
    "order block'a sonraki bir BOS kurulumu yeniden sinyal verebilir; harcanmışlık karar barında denetlenir ve yalnız ÖNCEKİ "
    "barların sinyallerini görür (işleme dönüşüp dönüşmediği giriş barının açılışında bellidir; geleceğe bakmaz)",
    "SMC_SWEEP: 'aynı pivot en çok bir kez' = pivot başına en çok bir sinyal (sinyal anında harcanır)",
    "SMC_CHOCH: 'azalan' kesin (H_son < H_önceki, L_son < L_önceki); pivot başına sınır yok (her uygun kırılım bir sinyal)",
    "SMC_FVG: gövde = close − open (bar m−1), ATR(m−1) laboratuvarın ATR14'ü; giriş penceresi m+1..m+20; FVG başına en çok bir "
    "sinyal (ilk uygun bar)",
    "plasebo anahtarı: <sembol> = seri etiketi (PAXGUSDT spot, XAUUSDT/PAXGUSDT vadeli, XAUUSD Dukascopy), <ad> = varyant adı "
    "(PLACEBO_ öneki YOK), <yön> = LONG/SHORT, <zaman damgası> = barın AÇILIŞ zamanı (ms, tamsayı); signal_lab._h ile",
    "plasebo: her bar adaydır; HTF ve seans süzgeci plaseboya UYGULANMAZ; stop hesaplanamazsa (ATR yok, 10 bar yok, teyitli "
    "swing yok) plasebo olayı yok ve PLACEBO_<ad>:NO_STOP sayılır; p = gerçek sinyal (simülasyondan önceki olay) sayısı / "
    "serinin bar sayısı — seri başına sabittir (gelecekteki sinyal sayısını da içerir; belgeye göre bilinçli)",
    "pencere: barın açılış zamanı [başlangıç günü 00:00 UTC, bitiş günü + 1 gün 00:00 UTC) aralığında (bitiş günü dahil)",
    "aylık ölçü: işlemin ayı = t_ms'nin (karar anı = giriş anı) UTC takvim ayı; hedef yalnız TAM doğrulama aylarıyla ölçülür: ay "
    "başı > kesim (aggregate cutoff_ms) VE ayın her karar anında işlem açılabilir (ay sonu ≤ son açılabilir karar anı + dilim; son "
    "açılabilir karar anı = ts[n−1−süre] + dilim, signal_lab.simulate'in NO_FUTURE_DATA sınırı); kesimin düştüğü kısmi ay ve "
    "serinin sonundaki kısa ay hedefe girmez, R'leri ve işlem sayıları bilgi olarak raporlanır (edge_months); işlemsiz tam ay 0 R; "
    "ay kümeli bootstrap bootstrap_iters tekrar, tohum MONTH_SEED, en az 5 ay; ≥ +%1 karşılaştırması yuvarlanmamış değerle",
    "eşzamanlılık: işlem aralığı [ts[i+1], ts[i+hold] + dilim) (çıkış barının kapanışı); en çok = süpürme çizgisi (aynı anda "
    "önce çıkış); ortalama = OOS penceresi [kesim, serinin sonu] üzerinde zaman ağırlıklı açık işlem sayısı",
    "iki yön birlikte: hedefi karşılıyor = birleşik OOS aylık ortalama ≥ +%1 VE İKİ yönün de sıkı hükmü GÜÇLÜ ADAY; sonuç cümlesi: "
    "hedefi karşılayan tek yönlü hücreler (varsa iki yön satırları ayrıca); yalnız iki yön satırı karşılıyorsa 'hiçbir tek yönlü "
    "hücre karşılamadı; iki yön birlikte karşılayan: …'; ikisi de yoksa '" + NO_TARGET_TR + "' (çelişkili cümle yazılmaz)",
    "mekân ve uzun geçmiş notları ana serinin STANDART hükmü GÜÇLÜ ADAY olan hücreler içindir; mekân: n ≥ 20 ve ort.R ≤ 0 → "
    "'mekânda tutmadı'; Dukascopy: OOS ort.R ≤ 0 → 'uzun geçmişte tutmadı'; notlar raporun ana seri bölümünden hesaplanır (aynı "
    "koşu ya da aynı klasördeki aynı mühür, ayar ve pencereli önceki rapor); ana seri yoksa mekân ve Dukascopy bölümü KOŞMAZ (hata)",
    "Dukascopy kapsaması = indirilmiş Pzt–Cum günleri / pencere içindeki bütün Pzt–Cum günleri; gün indirilmiş sayılır: manifest'te "
    "status 200 ve bayt > 0 kaydı var VE dosya aynada boş olmayan bir dosya olarak duruyor (tatil 404'leri eksik sayılır); Pazar "
    "dosyaları okunur ama oranda yoktur; okunan dilimlerden biri boşsa bölüm yine 'yapılamadı'",
    "Dukascopy barları: hacmi 0 olan 1 DAKİKALIK barlar yeniden örneklemeden ÖNCE atılır; OHLC'si tutarsız (düşük > min(açılış, "
    "kapanış) ya da yüksek < max(açılış, kapanış)) ya da 100–20 000 dışında olan 1 dakikalık satır atılır ve sayılır; kayıt "
    "saniyesi [0, 86400) dışında ya da kesin artan olmayan dosya HATA; Cumartesi okunmaz",
    "aday oranı: laboratuvarın candidate_rate'i (bütün bağlam dilimleri) ve aynı tanımla yalnız birincil (HEPSİ) hücreler",
    "maker duyarlılığı: aynı işlemler maker maliyetiyle (%0,02 + 0 bps) yeniden fiyatlanır (r_maker); hükme girmez",
    "mevcut setler: katalog + ek sinyaller + algoritmalar dilim başına tek process_series koşusu ve tek aggregate (signal_lab.run "
    "gibi); mum varyasyonları AYRI yalnız-varyasyon koşusu ve ayrı aggregate (scripts/signal_lab.py --variations yalnız "
    "--only-variations ile koşar; birlikte koşu kesimi değiştirir); 'all' = örnek olmayan bütün kayıtlar; coinler arası momentum "
    "(XSMOM) tek sembolde tanımsızdır, koşmaz",
)

_CFG0 = L.LabConfig()
GOLD_REGISTRY: dict[str, Any] = {
    "version": GOLD_VERSION,
    "doc": GOLD_DOC,
    "question_tr": "EMA 20/50 kesişimi (1:2,5) ve SMC kavramları altında maliyet sonrası kazandırıyor mu; mevcut setler ne veriyor",
    "rules": RULES,
    "variants": [f"{n} {tf}" for n, r in RULES.items() for tf in r["tfs"]],
    "primary_cells": [f"{tf} {n} {s} HEPSİ" for n, r in RULES.items() for tf in r["tfs"] for s in SIDES],
    "short_tr": "SHORT her varyantta LONG'un tam aynasıdır: (o, h, l, c) → (−o, −l, −h, −c) üzerinde LONG kuralı; yüksek ↔ düşük, "
                "boğa (close > open) ↔ ayı (close < open), > ↔ <",
    "common": {
        "pivot_k": PIVOT_K, "pivot_confirm_tr": "pivot j ancak j+3 barının kapanışında teyitli; o andan önce hiçbir kural göremez",
        "pivot_tr": "bar j'nin yükseği önceki 3 ve sonraki 3 barın yükseklerinin hepsinden büyükse swing yüksek (düşük simetrik)",
        "buffer_atr": BUF_ATR, "buffer_tr": "b = 0,1 × ATR (karar barındaki ATR)",
        "swing_bars": SWING_BARS, "swing_tr": "i−9..i (karar barı dahil son 10 bar)",
        "setup_bars": SETUP_BARS, "atr_stop": ATR_STOP, "fvg_body_atr": FVG_BODY_ATR,
        "ema": [EMA_FAST, EMA_SLOW], "ema_tr": "kapanış üzerinde üstel ortalama, adjust=False",
        "atr_tr": "laboratuvarın ATR14'ü (signal_lab.indicators: Wilder EWM, min_periods 14)",
        "htf": {"tf": HTF_TF, "ema": HTF_EMA, "rule_tr": "son KAPANMIŞ 4h barında kapanış > EMA50(4h) → LONG serbest, < → SHORT serbest"},
        "session": {"start_utc": "07:00", "end_utc": "20:00", "minutes": list(SESSION_MIN), "inclusive_start": True,
                    "inclusive_end": False, "rule_tr": "karar anı (bar kapanışı) 07:00–20:00 UTC arasında; okunuş [07:00, 20:00)"},
        "smc_cancel_tr": "SMC_BOS_OB: pencerede girişten önce close < OB_low → iptal; SMC_FVG: girişten önce close < high[m−2] → iptal",
        "one_per_tr": "aynı order block'tan en çok bir işlem; aynı swept pivot en çok bir kez",
        "fvg_displacement_tr": "bar m−1 boğa (close > open) ve gövdesi ≥ 1 ATR(m−1)",
    },
    "placebo": {
        "name_tr": "PLACEBO_<ad>: aynı dilim, aynı yön, aynı RR, aynı süre sınırı; kovalama ölçüsü (tetik) karar barının kapanışı",
        "key_tr": 'u = crc32("<sembol>|<dilim>|<ad>|<yön>|<zaman damgası>") / 2^32; u < p ise plasebo olayı',
        "rate_tr": "p = o varyantın aynı seri ve yöndeki sinyal sayısı / bar sayısı; sonuçtan bağımsız, sabit",
        "stops": {n: r["placebo_stop_tr"] for n, r in RULES.items()},
        "compare_tr": "gerçek grup eşleştirilmiş plaseboyla aynı dilim, yön ve bağlamda karşılaştırılır (signal_lab.aggregate)",
    },
    "trade_model": {
        "entry_tr": "karar bar kapanışında; giriş sonraki barın açılışında", "same_bar_tr": "aynı barda stop ve hedef → STOP",
        "gap_tr": "boşlukla açılışta açılış fiyatı", "chase_atr": _CFG0.chase_atr, "min_risk_atr": _CFG0.min_risk_atr,
        "max_risk_atr": _CFG0.max_risk_atr, "target_tr": "hedef = RR × gerçek girişteki risk (Event.target None)",
        "time_exit_tr": "süre dolunca son barın kapanışında çıkış", "overlap_tr": "aynı varyant ve yönde üst üste binen işlemlere izin var",
        "sim": "signal_lab.simulate + dataclasses.replace(cfg, default_rr=RR, max_hold_bars=süre[dilim])",
    },
    "cost": {
        "fee_pct": _CFG0.fee_pct, "slippage_bps": _CFG0.slippage_bps,
        "round_trip_pct": round(2 * _CFG0.cost_per_side * 100, 4),
        "maker_info": {"fee_pct": MAKER_FEE_PCT, "slippage_bps": MAKER_SLIPPAGE_BPS, "tr": "bilgi amaçlı duyarlılık; hükme girmez"},
        "funding_tr": "fonlama yalnız vadeli mekân koşusunda bilgi (funding_r, futures_lab.funding_carry tanımı); hükme girmez",
    },
    "stats": {
        "split": _CFG0.split, "min_is": _CFG0.min_is, "min_oos": _CFG0.min_oos, "bootstrap_iters": _CFG0.bootstrap_iters,
        "strict_seed": L.STRICT_SEED, "strict_min_days": L.STRICT_MIN_DAYS, "strict_rule_tr": L.STRICT_RULE_TR,
        "verdicts": [L.V_STRONG, L.V_WEAK, L.V_LOSS, L.V_NONE, L.V_THIN],
        "split_tr": "her dilimde dönemin ilk 2/3'ü keşif (IS), kalanı doğrulama (OOS); kesim signal_lab.aggregate'inki",
        "primary_tr": "16 varyant × 2 yön, bağlam HEPSİ; bağlam dilimleri yalnız bilgi",
        "multiple_tr": "gerçek hücrelerin aday oranı plaseboların oranıyla karşılaştırılır; rastgele yürüyüşte hücrelerin %0–0,5'i "
                       "tesadüfen GÜÇLÜ ADAY: 32 hücrede tesadüfen 0–1 aday beklenir",
    },
    "monthly": {
        "risk_pct": RISK_PCT, "risk_pct_info": RISK_PCT_INFO, "target_pct_month": TARGET_PCT_MONTH, "seed": MONTH_SEED,
        "min_months_for_ci": MONTH_MIN_FOR_CI,
        "risk_tr": "işlem başına risk %0,5 = strateji defterlerinin risk_per_trade_pct'i (config.yaml learning_mode); aylık % = "
                   "aylık toplam R × 0,5; bilgi amaçlı %1 risk",
        "meets_target_tr": "OOS ortalama aylık net ≥ +%1 (risk %0,5) VE sıkı hüküm GÜÇLÜ ADAY",
        "scope_tr": "her birincil hücre ve her varyantın iki yönü birlikte; OOS'ta takvim ayı başına toplam R; ay kümeli bootstrap "
                    "%95 aralığı; ≥ +%1 ay payı; eşzamanlı açık işlemin en çoğu ve ortalaması",
        "note_tr": "bileşik getiri yok; defterin bütün işlemleri taşıyabildiği varsayılır",
    },
    "data": {
        "main": {"source": "data.binance.vision spot", "symbol": "PAXGUSDT", "start": "2020-08-01", "end": "2026-09-30",
                 "tfs": ["15m", "1h", "4h", "1d"], "tr": "1 PAXG = 1 troy ons; hafta sonu dahil (7/24)"},
        "venue": {"source": "data.binance.vision futures/um", "symbols": {"XAUUSDT": ["2025-12-01", "2026-09-30"],
                                                                          "PAXGUSDT": ["2025-03-01", "2026-09-30"]},
                  "tfs": ["15m", "1h", "4h"], "tr": "bilgi; fonlama arşivi yalnız bilgi"},
        "dukascopy": {"source": "Dukascopy XAUUSD BID 1 dakika", "symbol": "XAUUSD", "start": "2006-01-01", "end": "2026-09-30",
                      "tfs": ["15m", "1h", "4h"], "coverage_min": DUKA_COVERAGE_MIN,
                      "resample_tr": "UTC'ye hizalı 15m/1h/4h (sol etiket); hacim 0 barlar atılır; yalnız verisi olan kutular",
                      "coverage_tr": "Pzt–Cum günlerinin %95'inden azı inmişse bölüm 'yapılamadı'; hüküm ana seriden"},
        "quality_tr": "her seride eksik bar oranı ve 24 saatten uzun boşluklar raporlanır; fiyat verisi depoya yüklenmez",
    },
    "sections": {
        "main": {"tfs": ["15m", "1h", "4h"], "tr": "hüküm; 32 birincil hücre; IS/OOS 2/3"},
        "venue": {"tfs": ["15m", "1h", "4h"], "split": False, "min_n_note": VENUE_MIN_N,
                  "tr": "bilgi; dönem bölünmez; n, ort.R, kazanma, fonlama R; ana seride GÜÇLÜ ADAY hücre n ≥ 20 iken ort.R ≤ 0 → "
                        "'" + NOTE_VENUE + "'"},
        "dukascopy": {"tfs": ["15m", "1h", "4h"], "split": _CFG0.split,
                      "tr": "koşullu sağlamlık; aynı maliyet ve 2/3 bölme; ana seride GÜÇLÜ ADAY hücre OOS ort.R ≤ 0 → '"
                            + NOTE_DUKA + "'"},
        "existing": {"catalog": ["1h", "4h"], "extras": ["15m", "1h", "4h"], "algos": ["1h", "4h", "1d"],
                     "variations": ["1h", "4h"], "variations_spec": "all",
                     "tr": "KEŞİF: tanımlar değiştirilmeden signal_lab.process_series + aggregate; laboratuvarın kendi hükmüyle "
                           "ayrı raporlanır; 32 hücreye sayılmaz; tek başına aday kanıtı değildir; katalog 15m'de çalıştırılmaz "
                           "(6 yılda ~210 bin bar; maliyetli)"},
    },
    "outcome_tr": {
        "none": "hiçbir hücre hedefi karşılamazsa: " + NO_TARGET_TR + "; sonuçtan sonra ayarlama yok (ayarlama = yeni sürüm)",
        "some": "hedefi karşılayan hücre olursa: yalnız PAPER, önce yalnız-kayıt defter önerisi; sahip onayı olmadan hiçbir şey açılmaz",
    },
    "scope_tr": ["mevcut ajan ekibinin (tradingbot/agents) sinyalleri sınanmaz; geçmişe dönük tekrar oynatma altyapısı yok",
                 "bot bugün 1h/4h turlarıyla çalışır; 15 dakikalık bir hücre için ayrı döngü gerekir (mimari iş, bu çalışmanın dışında)",
                 "hiçbir defter, strateji, ajan ya da parametre değişmez; PAPER/geçmiş test"],
    "trials": {"version": GOLD_VERSION, "variants": 16, "primary_cells": 32,
               "tr": "gold_v1: 16 varyant (32 birincil hücre) + mevcut laboratuvar setlerinin altına uygulanması (keşif)"},
    "readings_tr": list(READINGS_TR),
}
GOLD_REGISTRY_SHA = hashlib.sha256(json.dumps(GOLD_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class GoldDataError(RuntimeError):
    """Gerekli seri yok ya da okunamadı: rapor eksik veriyle SESSİZCE üretilmez."""


# ---------------------------------------------------------------------------- yardımcılar
def names_for_tf(tf: str) -> list[str]:
    return [n for n, r in RULES.items() if tf in r["tfs"]]


def base_name(name: str) -> str:
    return name[len(PLACEBO_PREFIX):] if name.startswith(PLACEBO_PREFIX) else name


def variant_cfg(cfg: L.LabConfig, name: str, tf: str) -> L.LabConfig:
    """Varyantın kendi çıkışı: hedef = RR × gerçek girişteki risk, süre sınırı dilime göre (plasebo da aynısını kullanır)."""
    r = RULES[base_name(name)]
    return dataclasses.replace(cfg, default_rr=float(r["rr"]), max_hold_bars=int(r["max_hold_bars"][tf]))


def maker_cfg(cfg: L.LabConfig) -> L.LabConfig:
    return dataclasses.replace(cfg, fee_pct=MAKER_FEE_PCT, slippage_bps=MAKER_SLIPPAGE_BPS)


def day_ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)


def window_ms(start: str, end: str) -> tuple[int, int]:
    """[başlangıç günü 00:00 UTC, bitiş günü + 1 gün 00:00 UTC): bitiş günü DAHİL."""
    return day_ms(start), day_ms(end) + DAY_MS


def _iso(ms: Any) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ema(x: Any, span: int) -> np.ndarray:
    return pd.Series(np.asarray(x, dtype=float)).ewm(span=span, adjust=False, min_periods=span).mean().to_numpy()


def in_session(t_ms: Any) -> np.ndarray:
    """Karar anı (ms) UTC saatte [07:00, 20:00) içinde mi."""
    m = (np.asarray(t_ms, dtype=np.int64) // 60_000) % 1440
    return (m >= SESSION_MIN[0]) & (m < SESSION_MIN[1])


def htf_side(ts: Any, step: int, close: Any) -> np.ndarray:
    """Bar başına +1 / −1 / 0: karar anı T = ts + step itibarıyla son KAPANMIŞ 4h barında (başlangıç + 4h ≤ T) kapanış
    EMA50(4h)'in üstünde / altında / eşit ya da tanımsız. 4h barları bu serinin UTC kutularından (sol etiket, kutudaki son
    kapanış)."""
    ts = np.asarray(ts, dtype=np.int64)
    c = np.asarray(close, dtype=float)
    n = len(ts)
    if n == 0:
        return np.zeros(0)
    b = ts // H4_MS * H4_MS
    starts = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    ends = np.r_[starts[1:], n] - 1
    bins, c4 = b[starts], c[ends]
    e4 = ema(c4, HTF_EMA)
    with np.errstate(invalid="ignore"):
        sgn = np.where(c4 > e4, 1.0, np.where(c4 < e4, -1.0, 0.0))
    k = np.searchsorted(bins + H4_MS, ts + int(step), side="right") - 1
    return np.where(k >= 0, sgn[np.maximum(k, 0)], 0.0)


def swing_pivots(high: Any, low: Any, k: int = PIVOT_K) -> tuple[np.ndarray, np.ndarray]:
    """(ph, pl): bar j'nin yükseği önceki k ve sonraki k barın yükseklerinin HEPSİNDEN kesin büyük → ph[j] (düşük simetrik).
    Teyit j + k barının kapanışındadır (`confirmed`); bu dizi yalnız tanımı verir."""
    h, lo = np.asarray(high, dtype=float), np.asarray(low, dtype=float)
    n = len(h)
    ph, pl = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    if n < 2 * k + 1:
        return ph, pl
    mh, ml = h[k:n - k], lo[k:n - k]
    okh, okl = np.ones(n - 2 * k, dtype=bool), np.ones(n - 2 * k, dtype=bool)
    with np.errstate(invalid="ignore"):
        for d in range(1, k + 1):
            okh &= (mh > h[k - d:n - k - d]) & (mh > h[k + d:n - k + d])
            okl &= (ml < lo[k - d:n - k - d]) & (ml < lo[k + d:n - k + d])
    ph[k:n - k], pl[k:n - k] = okh, okl
    return ph, pl


def confirmed(piv: Any, k: int = PIVOT_K) -> tuple[np.ndarray, np.ndarray]:
    """(son, önceki): bar i'de görülebilen (j + k ≤ i) en son ve ondan önceki pivotun indeksi; yoksa −1."""
    piv = np.asarray(piv, dtype=bool)
    n = len(piv)
    js = np.flatnonzero(piv)
    if js.size == 0:
        return np.full(n, -1, dtype=np.int64), np.full(n, -1, dtype=np.int64)
    idx = np.searchsorted(js + k, np.arange(n), side="right") - 1
    last = np.where(idx >= 0, js[np.maximum(idx, 0)], -1)
    prev = np.where(idx >= 1, js[np.maximum(idx - 1, 0)], -1)
    return last.astype(np.int64), prev.astype(np.int64)


def _view(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, s: float) -> tuple[np.ndarray, ...]:
    """LONG kuralının gördüğü fiyat: LONG için kendisi, SHORT için ayna (−o, −l, −h, −c)."""
    return (o, h, lo, c) if s > 0 else (-o, -lo, -h, -c)


def _frame(df: pd.DataFrame) -> tuple[np.ndarray, ...]:
    return (df["timestamp"].to_numpy(dtype=np.int64),) + tuple(df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))


# ---------------------------------------------------------------------------- olaylar (LONG kuralı; SHORT aynada)
def _bos_ob(Op, H, Lw, C, last_h, last_l, ok, atr, add, traded) -> None:
    """`traded(i, stop, tetik)`: bu sinyal `signal_lab.simulate`'te işleme dönüşür mü (giriş i+1 açılışında belli olur).
    Order block yalnız İŞLEME dönüşen sinyalle harcanır; harcanmışlık sonraki barların kararlarında denetlenir."""
    n = len(C)
    lb = np.maximum.accumulate(np.where(C < Op, np.arange(n), -1)) if n else np.zeros(0, dtype=np.int64)
    used: set[int] = set()                                      # işleme dönüşmüş order block'lar
    pending: list[tuple[int, int]] = []
    for i in range(1, n):
        if pending:
            keep: list[tuple[int, int]] = []
            fired: set[int] = set()                             # bu barda sinyal veren order block'lar
            for st, ob in pending:
                if i > st + SETUP_BARS:
                    continue                                    # pencere doldu
                zlo, zhi = Lw[ob], H[ob]
                if Lw[i] <= zhi and C[i] >= zlo:                # İLK dokunuş: karar burada
                    if ob not in used and ob not in fired and ok[i]:
                        stop = zlo - BUF_ATR * atr[i]
                        add("SMC_BOS_OB", i, stop, zhi)
                        fired.add(ob)
                        if traded(i, stop, zhi):
                            used.add(ob)
                    continue
                if C[i] < zlo:                                  # girişten önce bölgenin altında kapanış → iptal
                    continue
                keep.append((st, ob))
            pending = keep
        jh = last_h[i]
        if jh < 0:
            continue
        hv = H[jh]
        if C[i] > hv and C[i - 1] <= hv:                        # BOS
            jl, ob = last_l[i], lb[i - 1]
            if jl >= 0 and ob >= jl:
                pending.append((i, int(ob)))


def _sweep(H, Lw, C, last_l, ok, atr, add) -> None:
    used: set[int] = set()
    for i in range(len(C)):
        jl = last_l[i]
        if jl < 0 or jl in used or not ok[i]:
            continue
        sv = Lw[jl]
        if Lw[i] < sv and C[i] > sv:
            add("SMC_SWEEP", i, Lw[i] - BUF_ATR * atr[i], C[i])
            used.add(int(jl))


def _choch(H, Lw, C, last_h, prev_h, last_l, prev_l, ok, atr, add) -> None:
    for i in range(1, len(C)):
        a, b, c, d = last_h[i], prev_h[i], last_l[i], prev_l[i]
        if a < 0 or b < 0 or c < 0 or d < 0 or not ok[i]:
            continue
        h2, h1, l2, l1 = H[a], H[b], Lw[c], Lw[d]
        if h2 < h1 and l2 < l1 and C[i] > h2 and C[i - 1] <= h2:
            add("SMC_CHOCH", i, l2 - BUF_ATR * atr[i], C[i])


def _fvg(Op, H, Lw, C, ok, atr, add) -> None:
    n = len(C)
    if n < 3:
        return
    with np.errstate(invalid="ignore"):
        body = C[1:n - 1] - Op[1:n - 1]
        cand = (Lw[2:] > H[:n - 2]) & (C[1:n - 1] > Op[1:n - 1]) & (body >= FVG_BODY_ATR * atr[1:n - 1])
    for m in np.flatnonzero(cand) + 2:
        zlo, zhi = H[m - 2], Lw[m]
        for k in range(m + 1, min(n, m + SETUP_BARS + 1)):
            if Lw[k] <= zhi and C[k] >= zlo:
                if ok[k]:
                    add("SMC_FVG", k, zlo - BUF_ATR * atr[k], zhi)
                break
            if C[k] < zlo:
                break


def gold_events(df: pd.DataFrame, symbol: str, tf: str, *, atr: np.ndarray | None = None,
                names: Iterable[str] | None = None, cfg: L.LabConfig | None = None) -> list[L.Event]:
    """Varyant olayları (aile "gold"); karar i barının kapanışında (t_ms = ts[i] + dilim). Hedef None: simulate RR × gerçek
    girişteki riski uygular. SHORT = aynada LONG. `cfg` (varsayılan LabConfig()) yalnız SMC_BOS_OB'nin "order block başına
    en çok bir İŞLEM" kuralı içindir: sinyalin işleme dönüşüp dönüşmediği `process` ile aynı ayarla sınanır."""
    names = list(names) if names is not None else names_for_tf(tf)
    bad = [x for x in names if x not in RULES or tf not in RULES[x]["tfs"]]
    if bad:
        raise ValueError(f"{tf} diliminde tanımsız varyant: {', '.join(bad)}")
    ts, o, h, lo, c = _frame(df)
    n, step = len(ts), tf_ms(tf)
    atr = L.indicators(df)["atr"] if atr is None else np.asarray(atr, dtype=float)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(atr) & (atr > 0)
    sess = in_session(ts + step)
    arr = {"open": o, "high": h, "low": lo, "close": c}
    ob_cfg = variant_cfg(cfg or L.LabConfig(), "SMC_BOS_OB", tf) if "SMC_BOS_OB" in names else None
    out: list[L.Event] = []
    for side, s in ((LONG, 1.0), (SHORT, -1.0)):
        Op, H, Lw, C = _view(o, h, lo, c, s)

        def add(name: str, i: int, stop_long: float, trig_long: float, _side: str = side, _s: float = s) -> None:
            if name in names:
                out.append(L.Event(symbol, tf, FAMILY, name, _side, int(i), int(ts[i]) + step, float(_s * stop_long),
                                   trigger=float(_s * trig_long), target=None))

        def traded(i: int, stop_long: float, trig_long: float, _side: str = side, _s: float = s) -> bool:
            ev = L.Event(symbol, tf, FAMILY, "SMC_BOS_OB", _side, int(i), int(ts[i]) + step, float(_s * stop_long),
                         trigger=float(_s * trig_long), target=None)
            return not L.simulate(ev, arr, atr, ob_cfg)
        if any(x in EMA_NAMES for x in names) and n:
            e20, e50 = ema(C, EMA_FAST), ema(C, EMA_SLOW)
            cross = np.zeros(n, dtype=bool)
            with np.errstate(invalid="ignore"):
                cross[1:] = (e20[1:] > e50[1:]) & (e20[:-1] <= e50[:-1])
            lo10 = pd.Series(Lw).rolling(SWING_BARS, min_periods=SWING_BARS).min().to_numpy()
            base = cross & ok
            swing = base & np.isfinite(lo10)
            for i in np.flatnonzero(swing):
                add("EMA_X_SWING", i, lo10[i] - BUF_ATR * atr[i], C[i])
            for i in np.flatnonzero(base):
                add("EMA_X_ATR", i, C[i] - ATR_STOP * atr[i], C[i])
            if "EMA_X_SWING_HTF" in names:
                for i in np.flatnonzero(swing & (htf_side(ts, step, C) > 0)):
                    add("EMA_X_SWING_HTF", i, lo10[i] - BUF_ATR * atr[i], C[i])
            for i in np.flatnonzero(swing & sess):
                add("EMA_X_SWING_SESSION", i, lo10[i] - BUF_ATR * atr[i], C[i])
        if any(x in SMC_NAMES for x in names) and n:
            ph, pl = swing_pivots(H, Lw)
            last_h, prev_h = confirmed(ph)
            last_l, prev_l = confirmed(pl)
            if "SMC_BOS_OB" in names:
                _bos_ob(Op, H, Lw, C, last_h, last_l, ok, atr, add, traded)
            if "SMC_SWEEP" in names:
                _sweep(H, Lw, C, last_l, ok, atr, add)
            if "SMC_CHOCH" in names:
                _choch(H, Lw, C, last_h, prev_h, last_l, prev_l, ok, atr, add)
            if "SMC_FVG" in names:
                _fvg(Op, H, Lw, C, ok, atr, add)
    out.sort(key=lambda e: (e.i, e.name, e.side))
    return out


def _placebo_stops(kind: str, H: np.ndarray, Lw: np.ndarray, C: np.ndarray, atr: np.ndarray, ok: np.ndarray,
                   cache: dict[str, np.ndarray]) -> np.ndarray:
    """LONG görünümünde plasebo stop dizisi (hesaplanamayan bar NaN)."""
    if kind == "SWING10":
        if "lo10" not in cache:
            cache["lo10"] = pd.Series(Lw).rolling(SWING_BARS, min_periods=SWING_BARS).min().to_numpy()
        st = cache["lo10"] - BUF_ATR * atr
    elif kind == "ATR":
        st = C - ATR_STOP * atr
    elif kind == "BAR":
        st = Lw - BUF_ATR * atr
    elif kind == "PIVOT":
        if "last_l" not in cache:
            cache["last_l"] = confirmed(swing_pivots(H, Lw)[1])[0]
        jl = cache["last_l"]
        st = np.where(jl >= 0, Lw[np.maximum(jl, 0)], np.nan) - BUF_ATR * atr
    else:
        raise ValueError(f"bilinmeyen plasebo stop'u: {kind}")
    return np.where(ok, st, np.nan)


def placebo_events(df: pd.DataFrame, symbol: str, tf: str, signals: Mapping[tuple[str, str], int], *,
                   atr: np.ndarray | None = None, p: Mapping[tuple[str, str], float] | None = None,
                   names: Iterable[str] | None = None, skipped: dict[str, int] | None = None) -> tuple[list[L.Event], dict]:
    """Eşleştirilmiş plasebo (`PLACEBO_<ad>`, aile "placebo"). Her bar için u = _h(sembol, dilim, ad, yön, ts[i]); u < p →
    olay. p verilmezse = signals[(ad, yön)] / bar sayısı. Stop varyantın plasebo stop'u, tetik close[i]. Döner: (olaylar,
    {(ad, yön): p})."""
    names = list(names) if names is not None else names_for_tf(tf)
    ts, o, h, lo, c = _frame(df)
    n, step = len(ts), tf_ms(tf)
    atr = L.indicators(df)["atr"] if atr is None else np.asarray(atr, dtype=float)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(atr) & (atr > 0)
    out: list[L.Event] = []
    probs: dict[tuple[str, str], float] = {}
    tsl = [int(t) for t in ts]
    for side, s in ((LONG, 1.0), (SHORT, -1.0)):
        Op, H, Lw, C = _view(o, h, lo, c, s)
        cache: dict[str, np.ndarray] = {}
        for name in names:
            prob = float(p[(name, side)]) if p is not None and (name, side) in p else (
                float(signals.get((name, side), 0)) / n if n else 0.0)
            probs[(name, side)] = prob
            if prob <= 0:
                continue
            stops = None
            pname = PLACEBO_PREFIX + name
            for i in range(n):
                if L._h(symbol, tf, name, side, tsl[i]) >= prob:
                    continue
                if stops is None:
                    stops = _placebo_stops(RULES[name]["placebo_stop"], H, Lw, C, atr, ok, cache)
                st = stops[i]
                if not math.isfinite(st):
                    if skipped is not None:
                        key = f"{pname}:{side}:NO_STOP"
                        skipped[key] = skipped.get(key, 0) + 1
                    continue
                out.append(L.Event(symbol, tf, PLACEBO_FAMILY, pname, side, i, tsl[i] + step, float(s * st),
                                   trigger=float(s * C[i]), target=None))
    out.sort(key=lambda e: (e.i, e.name, e.side))
    return out, probs


# ---------------------------------------------------------------------------- simülasyon
def process(df: pd.DataFrame, symbol: str, tf: str, cfg: L.LabConfig, *, names: Iterable[str] | None = None,
            placebo_p: Mapping[tuple[str, str], float] | None = None, maker: bool = True,
            funding_raw: dict | None = None) -> tuple[list[dict], dict]:
    """Bir seri: gerçek + plasebo olayları → `signal_lab.simulate` (varyantın RR'si ve süre sınırı) → `signal_lab.context`.
    Döner (işlem sözlükleri, meta). Sözlük = asdict(Event) + entry_ms / exit_ms (eşzamanlılık) + r_maker (maker maliyeti,
    bilgi). `funding_raw` ({"f_t", "f_rate"}) verilirse funding_r (bilgi; futures_lab.funding_carry tanımı)."""
    names = list(names) if names is not None else names_for_tf(tf)
    arr = {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    step = tf_ms(tf)
    ind = L.indicators(df)
    atr = ind["atr"]
    real = gold_events(df, symbol, tf, atr=atr, names=names, cfg=cfg)
    sig = Counter((e.name, e.side) for e in real)
    no_stop: dict[str, int] = {}
    plac, probs = placebo_events(df, symbol, tf, sig, atr=atr, p=placebo_p, names=names, skipped=no_stop)
    counts: dict[str, dict[str, dict[str, Any]]] = {}
    for nm in names:
        for key in (nm, PLACEBO_PREFIX + nm):
            counts[key] = {s: {"signals": 0, "trades": 0, "skipped": {}} for s in SIDES}
    for k, v in no_stop.items():                          # plasebo çekilişi oldu ama stop yok: sinyal sayılır, atlanır
        pname, side, why = k.split(":")
        cnt = counts[pname][side]
        cnt["signals"] += v
        cnt["skipped"][why] = cnt["skipped"].get(why, 0) + v
    done: list[L.Event] = []
    makers: list[float | None] = []
    for ev in real + plac:
        vc = variant_cfg(cfg, ev.name, tf)
        cnt = counts[ev.name][ev.side]
        cnt["signals"] += 1
        why = L.simulate(ev, arr, atr, vc)
        if why:
            cnt["skipped"][why] = cnt["skipped"].get(why, 0) + 1
            continue
        ev.ctx = L.context(ind, arr, ev.i, ev.side)
        cnt["trades"] += 1
        done.append(ev)
        if maker:
            m = dataclasses.replace(ev, ctx={})
            makers.append(m.r if not L.simulate(m, arr, atr, maker_cfg(vc)) else None)
        else:
            makers.append(None)
    if funding_raw is not None and done:
        from . import futures_lab as FL                  # tembel: yalnız vadeli mekân koşusunda
        FL.funding_carry(done, arr, ts, step, funding_raw, names=frozenset(e.name for e in done))
    out = []
    for ev, rm in zip(done, makers):
        d = asdict(ev)
        j = ev.i + 1
        d["entry_ms"] = int(ts[j])
        d["exit_ms"] = int(ts[j + ev.hold - 1]) + step
        d["r_maker"] = rm
        out.append(d)
    n = len(ts)
    holds = {nm: int(RULES[nm]["max_hold_bars"][tf]) for nm in names}
    meta = {"symbol": symbol, "tf": tf, "bars": int(n), "first": int(ts[0]) if n else None,
            "last": int(ts[-1]) if n else None, "signals": len(real), "placebo_signals": len(plac) + sum(no_stop.values()),
            "trades": len(out), "counts": counts, "placebo_p": {f"{k[0]}|{k[1]}": round(v, 8) for k, v in probs.items()},
            # son açılabilir karar anı (simulate: i + 1 + süre ≤ n) — aylık ölçünün tam ayları için
            "last_open_ms": {nm: int(ts[n - 1 - hd]) + step if n - 1 - hd >= 0 else None for nm, hd in holds.items()}}
    return out, meta


# ---------------------------------------------------------------------------- aylık hedef ölçüsü
def month_key(ms: int) -> str:
    d = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def month_keys(a_ms: int, b_ms: int) -> list[str]:
    """a'nın ayından b'nin ayına kadar (dahil) bütün takvim ayları."""
    a = datetime.fromtimestamp(int(a_ms) / 1000, tz=timezone.utc)
    b = datetime.fromtimestamp(int(b_ms) / 1000, tz=timezone.utc)
    y, m, out = a.year, a.month, []
    while (y, m) <= (b.year, b.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def concurrency(rows: list[Mapping[str, Any]], t0: float, t1: float) -> dict[str, Any]:
    """Açık işlem sayısı: aralık [entry_ms, exit_ms); en çok (aynı anda önce çıkış) ve [t0, t1] üzerinde zaman ağırlıklı ort."""
    pts = sorted([(int(e["entry_ms"]), 1) for e in rows] + [(int(e["exit_ms"]), -1) for e in rows])
    cur = mx = 0
    for _, d in pts:
        cur += d
        mx = max(mx, cur)
    span = float(t1) - float(t0)
    tot = sum(max(0.0, min(float(e["exit_ms"]), float(t1)) - max(float(e["entry_ms"]), float(t0))) for e in rows)
    return {"max": int(mx), "mean": round(tot / span, 4) if span > 0 else None}


def month_bounds(mk: str) -> tuple[int, int]:
    """"YYYY-MM" → [ay başı, sonraki ay başı) ms (UTC)."""
    a = day_ms(mk + "-01")
    return a, L._next_month(a)


def monthly_stats(rows: list[Mapping[str, Any]], cutoff: float, last_ms: int, *, iters: int, key: str = "r",
                  open_end_ms: int | None = None) -> dict[str, Any]:
    """Hedef ölçüsü yalnız TAM doğrulama (OOS) takvim aylarıyla: ay başı > kesim VE ay sonu ≤ `open_end_ms` (son açılabilir
    karar anı + dilim; verilmezse serinin sonu last_ms + 1). Tam ay başına OOS (t_ms > kesim) işlemlerin toplam R'si (işlemsiz
    ay 0); aylık % = ay R × risk; ay kümeli bootstrap %95 aralığı (MONTH_SEED); ≥ +%1 ay payı. Kısmi kenar ayları (kesimin ayı,
    serinin sonundaki kısa ay) hedefe girmez: R'leri, işlem sayıları ve OOS payları `edge_months`'ta (bilgi). Eşzamanlılık bütün
    OOS işlemleri üzerinde [kesim, serinin sonu] (bilgi). `mean_pct_month_exact` yuvarlanmamıştır; hedef onunla sınanır."""
    cut = float(cutoff)
    end = int(open_end_ms) if open_end_ms is not None else int(last_ms) + 1
    allm = month_keys(int(cutoff), int(last_ms))
    bounds = {k: month_bounds(k) for k in allm}
    full = [k for k in allm if bounds[k][0] > cut and bounds[k][1] <= end]
    sums = dict.fromkeys(full, 0.0)
    hits: Counter = Counter()
    edge: dict[str, dict[str, Any]] = {}
    for k in allm:
        if k not in sums:
            a, b = bounds[k]
            share = max(0.0, min(b, end) - max(a, cut)) / (b - a)
            edge[k] = {"r": 0.0, "trades": 0, "oos_share": round(share, 4)}
    oos = [e for e in rows if e.get(key) is not None and math.isfinite(float(e[key])) and float(e["t_ms"]) > cut]
    for e in oos:
        mk = month_key(int(e["t_ms"]))
        if mk in sums:
            sums[mk] += float(e[key])
            hits[mk] += 1
        else:
            ed = edge.setdefault(mk, {"r": 0.0, "trades": 0, "oos_share": None})
            ed["r"] += float(e[key])
            ed["trades"] += 1
    vals = np.array([sums[k] for k in full], dtype=float)
    mcount = int(len(vals))
    mean_r = float(vals.mean()) if mcount else None
    ci = None
    if mcount >= MONTH_MIN_FOR_CI:
        idx = np.random.default_rng(MONTH_SEED).integers(0, mcount, size=(iters, mcount))
        means = vals[idx].mean(axis=1)
        ci = [round(float(np.quantile(means, 0.025)) * RISK_PCT, 4), round(float(np.quantile(means, 0.975)) * RISK_PCT, 4)]
    return {"months": mcount, "first_month": full[0] if full else None, "last_month": full[-1] if full else None,
            "trades": int(sum(hits.values())), "oos_trades": len(oos),
            "months_without_trades": int(sum(1 for k in full if not hits[k])),
            "mean_r_month": None if mean_r is None else round(mean_r, 4),
            "mean_pct_month": None if mean_r is None else round(mean_r * RISK_PCT, 4),
            "mean_pct_month_exact": None if mean_r is None else mean_r * RISK_PCT,
            "mean_pct_month_risk1": None if mean_r is None else round(mean_r * RISK_PCT_INFO, 4),
            "ci95_pct_month": ci,
            "share_months_ge_target": round(float(np.mean(vals * RISK_PCT >= TARGET_PCT_MONTH)), 4) if mcount else None,
            "concurrency": concurrency(oos, cut, float(last_ms)),
            "monthly_r": {k: round(float(sums[k]), 4) for k in full},
            "edge_months": {k: {**v, "r": round(float(v["r"]), 4)} for k, v in sorted(edge.items())},
            "open_end": _iso(end)}


def meets_target(mon: Mapping[str, Any] | None, strict_verdicts: Iterable[str]) -> bool:
    """OOS tam ay ortalaması (YUVARLANMAMIŞ, risk %0,5) ≥ +%1 VE bütün sıkı hükümler GÜÇLÜ ADAY."""
    sv = list(strict_verdicts)
    x = (mon or {}).get("mean_pct_month_exact")
    return bool(x is not None and math.isfinite(float(x)) and float(x) >= TARGET_PCT_MONTH and sv
                and all(v == L.V_STRONG for v in sv))


# ---------------------------------------------------------------------------- rapor: ana seri
def _brief(st: Mapping[str, Any] | None) -> dict[str, Any]:
    st = st or {}
    return {k: st.get(k) for k in ("n", "mean_r", "win_rate", "ci95", "cost_r", "profit_factor") if k in st} or {"n": 0}


def _counts(metas: list[dict], tf: str, name: str, side: str) -> dict[str, Any]:
    out: dict[str, Any] = {"signals": 0, "trades": 0, "skipped": {}}
    for m in metas:
        if m.get("tf") != tf:
            continue
        c = ((m.get("counts") or {}).get(name) or {}).get(side) or {}
        out["signals"] += int(c.get("signals") or 0)
        out["trades"] += int(c.get("trades") or 0)
        for k, v in (c.get("skipped") or {}).items():
            out["skipped"][k] = out["skipped"].get(k, 0) + int(v)
    return out


def _last_ms(metas: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in metas:
        if m.get("last") is not None:
            v = int(m["last"]) + tf_ms(m["tf"]) - 1
            out[m["tf"]] = max(out.get(m["tf"], v), v)
    return out


def _open_end(metas: list[dict], tf: str, name: str) -> int | None:
    """Varyantın son açılabilir karar anı + dilim (aylık ölçünün tam ay sınırı); bilinmiyorsa None (= serinin sonu)."""
    vals = [int(m["last_open_ms"][name]) + tf_ms(tf) for m in metas
            if m.get("tf") == tf and (m.get("last_open_ms") or {}).get(name) is not None]
    return max(vals) if vals else None


def _period_stats(rows: list[Mapping[str, Any]], cut: float | None, cfg: L.LabConfig) -> dict[str, dict]:
    """Grubu olmayan hücre (aggregate 10 işlemin altındaki grubu yazmaz) için aynı kesimle IS/OOS özeti (n tutarlı kalsın)."""
    out = {}
    for per in ("IS", "OOS"):
        sel = [e for e in rows if cut is not None and (float(e["t_ms"]) <= float(cut)) == (per == "IS")]
        rs = np.array([float(e["r"]) for e in sel], dtype=float)
        out[per] = L.r_stats(rs, cfg.bootstrap_iters, days=np.array([int(e["t_ms"]) // DAY_MS for e in sel])) if len(rs) else {"n": 0}
    return out


def _rate(gs: list[dict]) -> float | None:
    return round(sum(1 for g in gs if g["replicated"] and g["IS"]["mean_r"] > 0) / len(gs), 4) if gs else None


def main_report(events: list[dict], metas: list[dict], cfg: L.LabConfig) -> dict[str, Any]:
    """32 birincil hücre (standart + sıkı hüküm, plaseboya göre), aylık hedef ölçüsü, iki yön birlikte, aday oranı."""
    agg = L.aggregate(events, cfg)
    cut = agg.get("cutoff_ms") or {}
    last = _last_ms(metas)
    hep = {(g["tf"], g["family"], g["name"], g["side"]): g for g in agg.get("groups") or [] if g["context"] == "HEPSİ"}
    cells, both = [], []
    for name, rule in RULES.items():
        for tf in rule["tfs"]:
            per_side = {}
            oe = _open_end(metas, tf, name)
            for side in SIDES:
                g = hep.get((tf, FAMILY, name, side))
                pg = hep.get((tf, PLACEBO_FAMILY, PLACEBO_PREFIX + name, side))
                rows = [e for e in events if e["tf"] == tf and e["family"] == FAMILY and e["name"] == name and e["side"] == side]
                mon = monthly_stats(rows, cut[tf], last[tf], iters=cfg.bootstrap_iters, open_end_ms=oe) \
                    if tf in cut and tf in last else None
                mk = monthly_stats(rows, cut[tf], last[tf], iters=cfg.bootstrap_iters, key="r_maker", open_end_ms=oe) if mon else None
                oos_m = [float(e["r_maker"]) for e in rows if e.get("r_maker") is not None and tf in cut and e["t_ms"] > cut[tf]]
                verdict = g["verdict"] if g else L.V_THIN
                strict = g.get("verdict_strict", verdict) if g else L.V_THIN
                ps = {"IS": dict(g["IS"]), "OOS": dict(g["OOS"])} if g else _period_stats(rows, cut.get(tf), cfg)
                cell = {"tf": tf, "name": name, "side": side, "rr": rule["rr"], "max_hold_bars": rule["max_hold_bars"][tf],
                        **_counts(metas, tf, name, side),
                        "placebo_counts": _counts(metas, tf, PLACEBO_PREFIX + name, side), "IS": ps["IS"], "OOS": ps["OOS"],
                        "placebo": {"IS": _brief(pg["IS"]), "OOS": _brief(pg["OOS"])} if pg else None,
                        "vs_placebo": g.get("vs_placebo") if g else None, "verdict": verdict, "verdict_strict": strict,
                        "monthly": mon,
                        "maker_info": {"oos_mean_r": round(float(np.mean(oos_m)), 4) if oos_m else None,
                                       "mean_pct_month": mk.get("mean_pct_month") if mk else None},
                        "meets_target": meets_target(mon, [strict])}
                cells.append(cell)
                per_side[side] = cell
            rows = [e for e in events if e["tf"] == tf and e["family"] == FAMILY and e["name"] == name]
            mon = monthly_stats(rows, cut[tf], last[tf], iters=cfg.bootstrap_iters, open_end_ms=oe) \
                if tf in cut and tf in last else None
            stricts = [per_side[s]["verdict_strict"] for s in SIDES]
            both.append({"tf": tf, "name": name, "monthly": mon, "verdict_strict": stricts,
                         "meets_target": meets_target(mon, stricts)})
    real = [g for g in hep.values() if g["family"] == FAMILY and g["verdict"] != L.V_THIN]
    pl = [g for g in hep.values() if g["family"] == PLACEBO_FAMILY and g["verdict"] != L.V_THIN]
    hit = [c for c in cells if c["meets_target"]]
    hit_both = [b for b in both if b["meets_target"]]
    concl = conclusion_tr(hit, hit_both)
    return {"cells": cells, "both_sides": both, "tested": agg.get("tested", 0),
            "candidate_rate": agg.get("candidate_rate"),
            "candidate_rate_primary": {"real": _rate(real), "placebo": _rate(pl), "real_tested": len(real),
                                       "placebo_tested": len(pl),
                                       "strong": sum(1 for c in cells if c["verdict"] == L.V_STRONG),
                                       "strict_strong": sum(1 for c in cells if c["verdict_strict"] == L.V_STRONG)},
            "cutoff_ms": {k: int(v) for k, v in cut.items()}, "last_ms": last, "tf_summary": agg.get("tf_summary"),
            "groups": agg.get("groups") or [], "meets_target_any": bool(hit), "meets_target_both_any": bool(hit_both),
            "conclusion_tr": concl}


def conclusion_tr(hit: list[Mapping[str, Any]], hit_both: list[Mapping[str, Any]]) -> str:
    """Sonuç cümlesi: "kazandırmadı" yalnız ne tek yönlü bir hücre ne de bir iki yön satırı hedefi karşılıyorsa yazılır."""
    both = ", ".join(f"{b['tf']} {b['name']}" for b in hit_both)
    if hit:
        concl = ("hedefi karşılayan hücre: " + ", ".join(f"{c['tf']} {c['name']} {c['side']}" for c in hit)
                 + " — " + GOLD_REGISTRY["outcome_tr"]["some"])
        return concl + (" · iki yön birlikte hedefi karşılayan: " + both if hit_both else "")
    if hit_both:
        return ("hiçbir tek yönlü birincil hücre hedefi karşılamadı; iki yön birlikte hedefi karşılayan: " + both + " — "
                + GOLD_REGISTRY["outcome_tr"]["some"])
    return "hiçbir birincil hücre (iki yön birlikte de) hedefi karşılamadı: " + NO_TARGET_TR


# ---------------------------------------------------------------------------- rapor: mekân, uzun geçmiş, mevcut setler
def _main_cell(main: Mapping[str, Any] | None, tf: str, name: str, side: str) -> dict | None:
    for c in (main or {}).get("cells") or []:
        if (c["tf"], c["name"], c["side"]) == (tf, name, side):
            return c
    return None


def venue_report(events: list[dict], metas: list[dict], cfg: L.LabConfig, main: Mapping[str, Any] | None) -> dict[str, Any]:
    """Vadeli mekân (bilgi; dönem bölmeden): sembol × dilim × varyant × yön için n, ort.R, kazanma, gün kümeli aralık,
    fonlama R (bilgi) ve plasebo ort.R; ana seride GÜÇLÜ ADAY hücre n ≥ 20 iken ort.R ≤ 0 → 'mekânda tutmadı'."""
    rows = []
    for sym in sorted({m["symbol"] for m in metas}):
        for name, rule in RULES.items():
            for tf in rule["tfs"]:
                for side in SIDES:
                    sel = [e for e in events if e["symbol"] == sym and e["tf"] == tf and e["name"] == name and e["side"] == side
                           and e["family"] == FAMILY]
                    pl = [float(e["r"]) for e in events if e["symbol"] == sym and e["tf"] == tf and e["side"] == side
                          and e["name"] == PLACEBO_PREFIX + name]
                    rs = np.array([float(e["r"]) for e in sel], dtype=float)
                    st = L.r_stats(rs, cfg.bootstrap_iters, days=np.array([int(e["t_ms"]) // DAY_MS for e in sel])) if len(rs) else {"n": 0}
                    fr = [float(e["funding_r"]) for e in sel if e.get("funding_r") is not None and math.isfinite(float(e["funding_r"]))]
                    note = note_venue(_main_cell(main, tf, name, side), st)
                    rows.append({"symbol": sym, "tf": tf, "name": name, "side": side, "all": st,
                                 "funding_r": {"mean": round(float(np.mean(fr)), 4) if fr else None, "known": len(fr),
                                               "unknown": len(sel) - len(fr)},
                                 "placebo_mean_r": round(float(np.mean(pl)), 4) if pl else None, "placebo_n": len(pl),
                                 "counts": _counts([m for m in metas if m["symbol"] == sym], tf, name, side), "note": note})
    return {"rows": rows, "notes": [r for r in rows if r["note"]], "split": False, "notes_evaluated": bool(main)}


def duka_report(events: list[dict], metas: list[dict], cfg: L.LabConfig, main: Mapping[str, Any] | None) -> dict[str, Any]:
    """Dukascopy (2/3 bölme, aynı maliyet): hücreler + ana seride GÜÇLÜ ADAY hücre OOS ort.R ≤ 0 → 'uzun geçmişte tutmadı'."""
    agg = L.aggregate(events, cfg)
    cut = agg.get("cutoff_ms") or {}
    hep = {(g["tf"], g["family"], g["name"], g["side"]): g for g in agg.get("groups") or [] if g["context"] == "HEPSİ"}
    cells = []
    for name, rule in RULES.items():
        for tf in rule["tfs"]:
            for side in SIDES:
                g = hep.get((tf, FAMILY, name, side))
                rows = [e for e in events if e["tf"] == tf and e["family"] == FAMILY and e["name"] == name and e["side"] == side]
                ps = {"IS": g["IS"], "OOS": g["OOS"]} if g else _period_stats(rows, cut.get(tf), cfg)
                oos = ps["OOS"]
                note = note_duka(_main_cell(main, tf, name, side), oos)
                cells.append({"tf": tf, "name": name, "side": side, "IS": _brief(ps["IS"]), "OOS": _brief(oos),
                              "vs_placebo": g.get("vs_placebo") if g else None, "verdict": g["verdict"] if g else L.V_THIN,
                              "verdict_strict": g.get("verdict_strict") if g else L.V_THIN,
                              **_counts(metas, tf, name, side), "note": note})
    return {"status": "koşuldu", "cells": cells, "notes": [c for c in cells if c["note"]], "notes_evaluated": bool(main),
            "cutoff_ms": agg.get("cutoff_ms") or {}, "candidate_rate": agg.get("candidate_rate")}


def note_venue(mc: Mapping[str, Any] | None, st: Mapping[str, Any]) -> str:
    """Ana seride (standart hüküm) GÜÇLÜ ADAY hücre, mekânda n ≥ 20 iken ort.R ≤ 0 → 'mekânda tutmadı'."""
    ok = mc and mc.get("verdict") == L.V_STRONG and int(st.get("n") or 0) >= VENUE_MIN_N and st.get("mean_r") is not None
    return NOTE_VENUE if ok and float(st["mean_r"]) <= 0 else ""


def note_duka(mc: Mapping[str, Any] | None, oos: Mapping[str, Any]) -> str:
    """Ana seride (standart hüküm) GÜÇLÜ ADAY hücre, Dukascopy OOS ort.R ≤ 0 → 'uzun geçmişte tutmadı'."""
    ok = mc and mc.get("verdict") == L.V_STRONG and oos.get("mean_r") is not None
    return NOTE_DUKA if ok and float(oos["mean_r"]) <= 0 else ""


def apply_notes(report: dict[str, Any]) -> None:
    """Mekân ve Dukascopy notlarını raporun KENDİ ana seri bölümünden (yeniden) hesaplar: bölümler ayrı koşularda birleşse de
    notlar her zaman rapordaki ana seriye dayanır."""
    main = report.get("main")
    ven = report.get("venue")
    if ven:
        for r in ven.get("rows") or []:
            r["note"] = note_venue(_main_cell(main, r["tf"], r["name"], r["side"]), r.get("all") or {})
        ven["notes"] = [r for r in ven.get("rows") or [] if r["note"]]
        ven["notes_evaluated"] = bool(main)
    duka = report.get("dukascopy")
    if duka and duka.get("status") == "koşuldu":
        for c in duka.get("cells") or []:
            c["note"] = note_duka(_main_cell(main, c["tf"], c["name"], c["side"]), c.get("OOS") or {})
        duka["notes"] = [c for c in duka.get("cells") or [] if c["note"]]
        duka["notes_evaluated"] = bool(main)


EXISTING_SETS = ("lab", "variations")


def existing_report(by_set: Mapping[str, list[dict]], metas: list[dict], cfg: L.LabConfig, *, top: int = 15) -> dict[str, Any]:
    """Mevcut laboratuvar setleri (KEŞİF), her küme KENDİ aggregate'iyle: "lab" = katalog + ek sinyaller + algoritmalar (tek
    koşu, signal_lab.run gibi), "variations" = mum varyasyonları (yalnız-varyasyon koşusu, --only-variations gibi)."""
    sets = {k: _existing_set(by_set.get(k) or [], [m for m in metas if m.get("set") == k], cfg, top=top) for k in EXISTING_SETS}
    return {"label_tr": "KEŞİF — 32 birincil hücreye sayılmaz; çok sayıda grup içerdiği için tek başına aday kanıtı değildir",
            "sets": sets, "xsmom_tr": "XSMOM_28_WEEKLY koşmadı: tek sembolde coinler arası momentum tanımsız"}


def _existing_set(events: list[dict], metas: list[dict], cfg: L.LabConfig, *, top: int) -> dict[str, Any]:
    agg = L.aggregate(events, cfg)
    real = [g for g in agg.get("groups") or [] if g["family"] not in ("placebo", "control")]
    cnt = lambda gs, k: dict(Counter(g[k] for g in gs))  # noqa: E731
    hep = [g for g in real if g["context"] == "HEPSİ"]
    best = sorted([g for g in real if g["verdict"] in (L.V_STRONG, L.V_WEAK)], key=lambda g: -g["OOS"]["mean_r"])[:top]
    return {"tested": agg.get("tested", 0), "candidate_rate": agg.get("candidate_rate"), "events": len(events),
            "verdicts_all": cnt(real, "verdict"), "verdicts_strict_all": cnt(real, "verdict_strict"),
            "verdicts_hepsi": cnt(hep, "verdict"), "verdicts_strict_hepsi": cnt(hep, "verdict_strict"),
            "top": [{"tf": g["tf"], "family": g["family"], "name": g["name"], "side": g["side"], "context": g["context"],
                     "bucket": g["bucket"], "IS": _brief(g["IS"]), "OOS": _brief(g["OOS"]), "vs_placebo": g.get("vs_placebo"),
                     "verdict": g["verdict"], "verdict_strict": g.get("verdict_strict")} for g in best],
            "tf_summary": agg.get("tf_summary"), "cutoff_ms": agg.get("cutoff_ms") or {},
            "series": [{k: m.get(k) for k in ("symbol", "tf", "bars", "signals", "trades", "error")} for m in metas]}


# ---------------------------------------------------------------------------- veri: arşiv önbelleği
SPOT_BASE = "https://data.binance.vision/data/spot"
_STAMP = re.compile(r"-(\d{4}-\d{2}(?:-\d{2})?)\.zip$")


class ArchiveCache:
    """data.binance.vision dosyaları için yerel önbellek: <cache>/archive/<sunucu>/<yol>. 404 kalıcı olarak (".missing")
    yalnız dosyanın dönemi `keep_404_days` günden eskiyse yazılır (henüz yayımlanmamış dosya kalıcı eksik sayılmaz).
    `offline`: önbellekte olmayan dosya → ConnectionError (ağ YOK)."""

    def __init__(self, cache_dir: Path | str, fetch: Callable[[str], bytes | None] | None = None, *, offline: bool = False,
                 now_ms: int | None = None, keep_404_days: int = 10):
        self.root = Path(cache_dir) / "archive"
        self.fetch = fetch or L._http_get
        self.offline = bool(offline)
        self.now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        self.keep_404_days = int(keep_404_days)
        self.requests = 0

    def path(self, url: str) -> Path:
        return self.root / url.split("://", 1)[-1]

    def _period_end_ms(self, url: str) -> int | None:
        m = _STAMP.search(url)
        if not m:
            return None
        s = m.group(1)
        if len(s) == 7:
            return L._next_month(day_ms(s + "-01"))
        return day_ms(s) + DAY_MS

    def get(self, url: str) -> bytes | None:
        p = self.path(url)
        if p.exists():
            return p.read_bytes()
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
        return data


def _months(start_ms: int, end_ms: int) -> list[int]:
    out, cur = [], L._month_start(start_ms)
    while cur < end_ms:
        out.append(cur)
        cur = L._next_month(cur)
    return out


def _clip(df: pd.DataFrame, start_ms: int, end_ms: int) -> pd.DataFrame:
    cols = ["timestamp", "open", "high", "low", "close", "volume", "close_time"]
    if df is None or not len(df):
        return pd.DataFrame(columns=cols)
    df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] < end_ms)]
    df = df.drop_duplicates("timestamp", keep="last").sort_values("timestamp", kind="mergesort")
    return df[cols].reset_index(drop=True)


def parse_spot_zip(data: bytes, tf: str) -> pd.DataFrame:
    """Spot kline zip → ms zaman damgalı çerçeve. Binance spot arşivi 2025-01-01'den itibaren MİKROSANİYE yazar
    (`signal_lab._parse_archive_zip` > 10¹⁴ olanları ÷1000 yapar); sonuç ms aralığında ve dilime hizalı değilse ValueError."""
    df = L._parse_archive_zip(data)
    if not len(df):
        return df
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    if ts.min() < 10 ** 12 or ts.max() >= 10 ** 13:
        raise ValueError(f"spot zaman damgası ms aralığında değil: {ts.min()}..{ts.max()}")
    if np.any(ts % tf_ms(tf)):
        raise ValueError(f"spot zaman damgası {tf} dilimine hizalı değil")
    return df


def spot_url(sym: str, tf: str, stamp: str) -> str:
    kind = "monthly" if len(stamp) == 7 else "daily"
    return f"{SPOT_BASE}/{kind}/klines/{sym}/{tf}/{sym}-{tf}-{stamp}.zip"


def series_quality(df: pd.DataFrame, tf: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    """Eksik bar oranı (ilk ve son bar arasında beklenen bar sayısına göre) ve 24 saatten uzun boşluklar."""
    step = tf_ms(tf)
    if df is None or not len(df):
        return {"bars": 0, "missing_ratio": None, "gaps_over_24h": [], "n_gaps_over_24h": 0, "window": [_iso(start_ms), _iso(end_ms)]}
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    exp = int((ts[-1] - ts[0]) // step) + 1
    d = np.diff(ts)
    big = np.flatnonzero(d > DAY_MS)
    return {"bars": int(len(ts)), "first": _iso(ts[0]), "last": _iso(ts[-1]), "expected": exp,
            "missing_bars": int(exp - len(ts)), "missing_ratio": round(1 - len(ts) / exp, 6) if exp else None,
            "n_gaps_over_24h": int(len(big)),
            "gaps_over_24h": [{"from": _iso(ts[k]), "to": _iso(ts[k + 1]), "hours": round(float(d[k]) / 3_600_000, 1)} for k in big[:50]],
            "start_lag_days": round((int(ts[0]) - int(start_ms)) / DAY_MS, 2), "window": [_iso(start_ms), _iso(end_ms)]}


def load_spot(symbol: str, tf: str, start_ms: int, end_ms: int, get: Callable[[str], bytes | None]) -> tuple[pd.DataFrame, dict]:
    """Binance spot arşivi: her ay için ay dosyası; yayımlanmamışsa (404) o ayın pencere içindeki gün dosyaları. Pencereye
    kesin kırpılır, tekrarlar atılır, sıralanır."""
    sym = symbol.replace("/", "").upper()
    parts, fallback, missing = [], [], []

    def fetch(url: str) -> pd.DataFrame | None:
        data = get(url)
        if data is None:
            return None
        try:
            return parse_spot_zip(data, tf)
        except (ValueError, zipfile.BadZipFile) as exc:
            raise GoldDataError(f"bozuk spot arşivi: {url}: {exc} (önbellekteki dosyayı silip yeniden indirin)") from exc

    for mon in _months(start_ms, end_ms):
        stamp = pd.Timestamp(mon, unit="ms", tz="UTC").strftime("%Y-%m")
        df = fetch(spot_url(sym, tf, stamp))
        if df is not None:
            parts.append(df)
            continue
        got, day = 0, max(mon, start_ms - start_ms % DAY_MS)
        while day < min(L._next_month(mon), end_ms):
            d = fetch(spot_url(sym, tf, pd.Timestamp(day, unit="ms", tz="UTC").strftime("%Y-%m-%d")))
            if d is not None:
                parts.append(d)
                got += 1
            day += DAY_MS
        (fallback if got else missing).append({"month": stamp, "days": got})
    df = _clip(pd.concat([x for x in parts if len(x)]) if any(len(x) for x in parts) else None, start_ms, end_ms)
    q = series_quality(df, tf, start_ms, end_ms)
    q.update({"source": "binance_spot", "symbol": sym, "tf": tf, "daily_fallback": fallback, "missing_months": missing})
    return df, q


def load_futures(symbol: str, tf: str, start_ms: int, end_ms: int, get: Callable[[str], bytes | None],
                 now_ms: int) -> tuple[pd.DataFrame, dict]:
    """USDⓈ-M arşivi `signal_lab.ArchiveProvider` + `signal_lab.download` ile (ay dosyası; son 40 günde gün dosyaları)."""
    prov = L.ArchiveProvider(fetch=get, clock_ms=lambda: int(now_ms))
    df = _clip(L.download(prov, symbol, tf, start_ms, end_ms - 1), start_ms, end_ms)
    q = series_quality(df, tf, start_ms, end_ms)
    q.update({"source": "binance_usdm", "symbol": symbol.replace("/", "").upper(), "tf": tf})
    return df, q


def load_funding(symbol: str, start_ms: int, end_ms: int, get: Callable[[str], bytes | None]) -> dict[str, Any]:
    """Fonlama arşivi (bilgi): ay dosyası, yoksa gün dosyaları → {"f_t", "f_rate"} (pencereye kırpılmış, sıralı)."""
    from . import futures_data as FD                     # tembel: yalnız mekân bölümünde
    sym = symbol.replace("/", "").upper()
    frames, missing = [], []
    for mon in _months(start_ms, end_ms):
        stamp = pd.Timestamp(mon, unit="ms", tz="UTC").strftime("%Y-%m")
        data = get(FD.FUNDING_URL.format(sym=sym, month=stamp))
        if data is not None:
            frames.append(FD.parse_funding_zip(data))
            continue
        got, day = 0, mon
        while day < min(L._next_month(mon), end_ms):
            d = get(FD.FUNDING_DAILY_URL.format(sym=sym, day=pd.Timestamp(day, unit="ms", tz="UTC").strftime("%Y-%m-%d")))
            if d is not None:
                frames.append(FD.parse_funding_zip(d))
                got += 1
            day += DAY_MS
        if not got:
            missing.append(stamp)
    f = pd.concat(frames) if frames else pd.DataFrame({"t_ms": np.array([], dtype=np.int64), "rate": np.array([], dtype=float)})
    f = f[(f["t_ms"] >= start_ms - DAY_MS) & (f["t_ms"] < end_ms + DAY_MS)].drop_duplicates("t_ms").sort_values("t_ms")
    return {"f_t": f["t_ms"].to_numpy(dtype=np.int64), "f_rate": f["rate"].to_numpy(dtype=float), "rows": int(len(f)),
            "missing_months": missing}


# ---------------------------------------------------------------------------- veri: Dukascopy
#: bi5 kaydı (doğrulandı, ayna dosyalarıyla: düşük ≤ açılış,kapanış ≤ yüksek): gün başından saniye, açılış, kapanış, düşük,
#: yüksek (int32, nokta 0,001), hacim (float32); hepsi big-endian, 24 bayt.
DUKA_DTYPE = np.dtype([("t", ">i4"), ("o", ">i4"), ("c", ">i4"), ("l", ">i4"), ("h", ">i4"), ("v", ">f4")])
DUKA_DIV = 1000.0
DUKA_PLAUSIBLE = (100.0, 20_000.0)
DUKA_FILE = "BID_candles_min_1.bi5"


def duka_path(root: Path | str, d: date) -> Path:
    return Path(root) / "XAUUSD" / f"{d.year:04d}" / f"{d.month - 1:02d}" / f"{d.day:02d}" / DUKA_FILE


def decode_bi5(data: bytes, day_start_ms: int) -> pd.DataFrame:
    """bi5 (LZMA alone) → 1 dakikalık UTC barlar [timestamp, open, high, low, close, volume]. Boş dosya → boş çerçeve."""
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    if not data:
        return pd.DataFrame(columns=cols)
    raw = lzma.decompress(data, format=lzma.FORMAT_ALONE)
    if len(raw) % DUKA_DTYPE.itemsize:
        raise ValueError(f"bi5 uzunluğu {len(raw)} 24'ün katı değil")
    a = np.frombuffer(raw, dtype=DUKA_DTYPE)
    t = a["t"].astype(np.int64)
    if len(t) and (t.min() < 0 or t.max() >= 86_400 or np.any(np.diff(t) <= 0)):
        raise ValueError(f"bi5 kayıt saniyeleri [0, 86400) dışında ya da kesin artan değil ({t.min()}..{t.max()})")
    return pd.DataFrame({"timestamp": int(day_start_ms) + t * 1000,
                         "open": a["o"].astype(float) / DUKA_DIV, "high": a["h"].astype(float) / DUKA_DIV,
                         "low": a["l"].astype(float) / DUKA_DIV, "close": a["c"].astype(float) / DUKA_DIV,
                         "volume": a["v"].astype(float)})


def bi5_sanity(df: pd.DataFrame) -> np.ndarray:
    """Satır başına geçerlilik: düşük ≤ min(açılış, kapanış), yüksek ≥ max(açılış, kapanış), fiyat makul aralıkta."""
    o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    lo_ok, hi_ok = DUKA_PLAUSIBLE
    return (lo <= np.minimum(o, c)) & (h >= np.maximum(o, c)) & (lo >= lo_ok) & (h <= hi_ok)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """UTC'ye hizalı, sol etiketli kutular; yalnız verisi olan kutular (açılış ilk, kapanış son, yüksek en büyük, düşük en
    küçük, hacim toplam)."""
    step = tf_ms(tf)
    cols = ["timestamp", "open", "high", "low", "close", "volume", "close_time"]
    if not len(df):
        return pd.DataFrame(columns=cols)
    df = df.sort_values("timestamp", kind="mergesort")
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    b = ts // step * step
    st = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    en = np.r_[st[1:], len(b)] - 1
    o, h, lo, c, v = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    return pd.DataFrame({"timestamp": b[st], "open": o[st], "high": np.maximum.reduceat(h, st), "low": np.minimum.reduceat(lo, st),
                         "close": c[en], "volume": np.add.reduceat(v, st), "close_time": b[st] + step - 1})


DUKA_COVERAGE_BASIS_TR = ("Pzt–Cum günleri; gün indirilmiş sayılır: manifest'te status 200 ve bayt > 0 VE dosya aynada boş "
                          "olmayan bir dosya")


def _positive(x: Any) -> bool:
    try:
        return float(x) > 0
    except (TypeError, ValueError):
        return False


def duka_coverage(root: Path | str, start: str, end: str) -> dict[str, Any]:
    """Kapsama = indirilmiş Pzt–Cum günleri / pencere içindeki bütün Pzt–Cum günleri. Gün indirilmiş sayılır: manifest'te
    status 200 ve bayt > 0 kaydı var VE `duka_path` boş olmayan bir dosya (manifest tek başına yetmez)."""
    man = Path(root) / "manifest.jsonl"
    ok: set[str] = set()
    status: Counter = Counter()
    if man.exists():
        for line in man.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            status[str(r.get("status"))] += 1
            if r.get("status") == 200 and _positive(r.get("bytes")):
                ok.add(str(r.get("date")))
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    wk = [d0 + timedelta(days=k) for k in range((d1 - d0).days + 1)]
    wk = [d for d in wk if d.weekday() < 5]
    man_ok = [d for d in wk if d.isoformat() in ok]
    got = 0
    for d in man_ok:
        p = duka_path(root, d)
        got += int(p.is_file() and p.stat().st_size > 0)
    cov = got / len(wk) if wk else 0.0
    return {"weekdays": len(wk), "weekdays_manifest_ok": len(man_ok), "weekdays_file_missing": len(man_ok) - got,
            "weekdays_ok": got, "coverage": round(cov, 6), "threshold": DUKA_COVERAGE_MIN, "enough": cov >= DUKA_COVERAGE_MIN,
            "manifest": man.exists(), "status_counts": dict(status), "basis_tr": DUKA_COVERAGE_BASIS_TR}


def load_dukascopy(root: Path | str, start: str, end: str, tfs: Iterable[str]) -> tuple[dict[str, pd.DataFrame], dict]:
    """Aynadaki günleri okur (Cumartesi hariç), hacmi 0 barları ve tutarsız satırları atar, gün gün yeniden örnekler."""
    tfs = list(tfs)
    parts: dict[str, list[pd.DataFrame]] = {tf: [] for tf in tfs}
    q = {"days_read": 0, "days_missing_file": 0, "rows_1m": 0, "rows_vol0": 0, "rows_bad": 0}
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    for k in range((d1 - d0).days + 1):
        d = d0 + timedelta(days=k)
        if d.weekday() == 5:
            continue
        p = duka_path(root, d)
        if not p.exists():
            q["days_missing_file"] += 1
            continue
        try:
            m1 = decode_bi5(p.read_bytes(), day_ms(d.isoformat()))
        except (ValueError, lzma.LZMAError, OSError) as exc:
            raise GoldDataError(f"Dukascopy dosyası okunamadı: {p}: {exc}") from exc
        q["days_read"] += 1
        q["rows_1m"] += len(m1)
        if not len(m1):
            continue
        vol = m1["volume"].to_numpy(dtype=float) > 0
        good = bi5_sanity(m1)
        q["rows_vol0"] += int((~vol).sum())
        q["rows_bad"] += int((vol & ~good).sum())
        m1 = m1[vol & good]
        for tf in tfs:
            parts[tf].append(resample(m1, tf))
    out: dict[str, pd.DataFrame] = {}
    q["bars_duplicate"] = {}
    for tf, v in parts.items():
        if any(len(x) for x in v):
            df = pd.concat([x for x in v if len(x)]).sort_values("timestamp", kind="mergesort")
            n0 = len(df)
            df = df.drop_duplicates("timestamp", keep="first").reset_index(drop=True)
            q["bars_duplicate"][tf] = int(n0 - len(df))
        else:
            df = resample(pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]), tf)
            q["bars_duplicate"][tf] = 0
        out[tf] = df
    s_ms, e_ms = window_ms(start, end)
    q["series"] = {tf: series_quality(df, tf, s_ms, e_ms) for tf, df in out.items()}
    return out, q


# ---------------------------------------------------------------------------- çalıştırma
SECTIONS = ("main", "venue", "dukascopy", "existing")


def variation_ids() -> list[str]:
    """`--variations all` ile aynı: örnek OLMAYAN bütün mum varyasyonu kayıtları (bozuk kayıt ValueError)."""
    from . import candle_variations as CV
    ids = [e["id"] for e in CV.VARIATIONS if isinstance(e, dict) and isinstance(e.get("id"), str) and e.get("example") is not True]
    for vid in ids:
        CV.get(vid)
    return ids


def existing_flags(tf: str) -> dict[str, Any]:
    s = GOLD_REGISTRY["sections"]["existing"]
    return {"catalog": tf in s["catalog"], "extras": tf in s["extras"], "algos": tf in s["algos"],
            "variations": tuple(variation_ids()) if tf in s["variations"] else ()}


def _gold_task(args: tuple) -> tuple[list[dict], dict]:
    df, symbol, tf, cfg_d, funding = args
    return process(df, symbol, tf, L.LabConfig(**cfg_d), funding_raw=funding)


def _existing_task(args: tuple) -> tuple[dict[str, list[dict]], list[dict]]:
    """Bir dilim: (a) katalog/ek/algoritma tek koşuda, (b) mum varyasyonları ayrı yalnız-varyasyon koşusunda."""
    df, symbol, tf, cfg_d = args
    f = existing_flags(tf)
    cfg = L.LabConfig(**cfg_d)
    out: dict[str, list[dict]] = {k: [] for k in EXISTING_SETS}
    sets = (["lab"] if f["catalog"] or f["extras"] or f["algos"] else []) + (["variations"] if f["variations"] else [])
    if len(df) < cfg.window + cfg.max_hold_bars + 10:              # signal_lab._task ile aynı eşik
        return out, [{"symbol": symbol, "tf": tf, "bars": len(df), "error": "YETERSİZ_VERİ", "set": k} for k in sets]
    metas = []
    if f["catalog"] or f["extras"] or f["algos"]:
        evs, meta = L.process_series(df, symbol, tf, cfg, catalog=f["catalog"], algos=f["algos"], extras=f["extras"])
        out["lab"] = [asdict(e) for e in evs]
        metas.append({**meta, "set": "lab"})
    if f["variations"]:
        evs, meta = L.process_series(df, symbol, tf, cfg, catalog=False, algos=False, extras=False, variations=f["variations"])
        out["variations"] = [asdict(e) for e in evs]
        metas.append({**meta, "set": "variations"})
    return out, metas


def _map(fn: Callable[[tuple], Any], tasks: list[tuple], jobs: int) -> list[Any]:
    if jobs > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as ex:
            return list(ex.map(fn, tasks))
    return [fn(t) for t in tasks]


def _clean(x: Any) -> Any:
    if isinstance(x, Mapping):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(float(x)) else None
    if isinstance(x, np.ndarray):
        return _clean(x.tolist())
    return x


EVENT_COLS = ["section", "symbol", "tf", "family", "name", "side", "i", "t_ms", "entry_ms", "exit_ms", "stop", "trigger", "r",
              "r_maker", "cost_r", "exit_reason", "hold", "funding_r", "ctx"]
REPORT_JSON, REPORT_MD = "gold_lab_report.json", "gold_lab_report.md"
#: bölümün rapordaki veri anahtarları (yeniden koşulunca eskileri silinir; spot anahtarları yüklenince yenilenir)
_DATA_OWNER = (("venue", ("futures ", "funding ")), ("dukascopy", ("dukascopy",)))


def events_file(section: str) -> str:
    """Bölüm başına olay dosyası: ayrı koşular birbirinin olaylarını silmez."""
    return f"gold_lab_events_{section}.csv.gz"


def write_events(path: Path, events: list[dict]) -> None:
    tmp = path.with_name(path.name + ".part")
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(EVENT_COLS)
        for e in events:
            w.writerow([json.dumps(e.get("ctx") or {}, ensure_ascii=False) if c == "ctx" else e.get(c) for c in EVENT_COLS])
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _as_json(x: Any) -> Any:
    """JSON gidiş-dönüşü (önceki raporla karşılaştırma için)."""
    return json.loads(json.dumps(_clean(x), ensure_ascii=False))


def prior_report(out_dir: Path | str, cfg_d: Mapping[str, Any], windows: Mapping[str, Any]) -> dict[str, Any] | None:
    """<out>/gold_lab_report.json varsa ve AYNI mühür, ayar ve veri pencereleriyle yazılmışsa onu döner (yeni bölümler onunla
    birleşir); farklıysa GoldDataError: başka koşulların raporu sessizce ezilmez ya da karıştırılmaz."""
    p = Path(out_dir) / REPORT_JSON
    if not p.exists():
        return None
    try:
        rep = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GoldDataError(f"{p}: önceki rapor okunamadı ({exc}); başka bir --out seçin ya da dosyayı kaldırın") from exc
    why = []
    if not isinstance(rep, dict) or rep.get("kind") != "GOLD_LAB":
        why.append("altın laboratuvarı raporu değil")
    else:
        if rep.get("registry_sha") != GOLD_REGISTRY_SHA:
            why.append(f"mühür {rep.get('registry_sha')} ≠ {GOLD_REGISTRY_SHA}")
        if rep.get("config") != _as_json(cfg_d):
            why.append("laboratuvar ayarı farklı")
        if rep.get("data_windows") != _as_json(windows):
            why.append("veri pencereleri farklı")
    if why:
        raise GoldDataError(f"{p}: önceki rapor bu koşuyla birleştirilemez ({'; '.join(why)}); başka bir --out seçin ya da eski "
                            "raporu kaldırın")
    return rep


def run(*, sections: Iterable[str], cache_dir: Path | str, out_dir: Path | str, cfg: L.LabConfig | None = None,
        fetch: Callable[[str], bytes | None] | None = None, offline: bool = False, jobs: int = 1,
        duka_root: Path | str | None = None, now_ms: int | None = None, log: Callable[[str], None] = print,
        data_windows: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bölümleri koşar; <out>/gold_lab_report.json, <out>/gold_lab_report.md ve bölüm başına
    <out>/gold_lab_events_<bölüm>.csv.gz yazar. Aynı klasörde aynı mühür, ayar ve pencereli önceki rapor varsa yeni bölümler
    onunla BİRLEŞİR (bu koşuda koşulmayan bölümler ve olay dosyaları korunur); farklıysa HATA. Mekân ve Dukascopy notları
    ana seri hücrelerine dayanır: ana seri ne bu koşuda ne önceki raporda varsa bu bölümler KOŞMAZ (GoldDataError); notlar
    yazmadan önce raporun ana seri bölümünden yeniden hesaplanır (`apply_notes`). `fetch`: arşiv indiricisi (testte sahte;
    None → signal_lab._http_get); `offline`: yalnız önbellek. `data_windows`: yalnız testler için pencere değiştirme (CLI
    vermez; ön kayıtlı pencere GOLD_REGISTRY['data']); kullanılan pencere ve `windows_overridden` rapora yazılır."""
    cfg = cfg or L.LabConfig()
    secs = list(SECTIONS) if "all" in set(sections) else [s for s in SECTIONS if s in set(sections)]
    bad = [s for s in sections if s not in SECTIONS + ("all",)]
    if bad or not secs:
        raise ValueError(f"bilinmeyen bölüm: {', '.join(bad) or '(boş)'} (geçerli: {', '.join(SECTIONS)}, all)")
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    reg_win = json.loads(json.dumps(GOLD_REGISTRY["data"]))
    win = json.loads(json.dumps(reg_win))
    for k, v in (data_windows or {}).items():
        win[k].update(v)
    overridden = win != reg_win
    out_dir = Path(out_dir)
    cfg_d = asdict(cfg)
    prior = prior_report(out_dir, cfg_d, win)
    if {"venue", "dukascopy"} & set(secs) and "main" not in secs and not (prior or {}).get("main"):
        raise GoldDataError(f"mekân ve Dukascopy notları ('{NOTE_VENUE}', '{NOTE_DUKA}') ana serinin GÜÇLÜ ADAY hücrelerine "
                            f"dayanır: aynı koşuya main ekleyin ya da önce aynı --out klasörüne --section main koşun ({out_dir})")
    cache = ArchiveCache(cache_dir, fetch=fetch, offline=offline, now_ms=now_ms)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    report: dict[str, Any] = {k: v for k, v in (prior or {}).items() if k not in secs}
    report.update({"kind": "GOLD_LAB", "version": GOLD_VERSION, "registry_sha": GOLD_REGISTRY_SHA, "doc": GOLD_DOC,
                   "config": cfg_d, "cost_round_trip_pct": round(2 * cfg.cost_per_side * 100, 3), "generated_at": _iso(now_ms),
                   "data_windows": win, "windows_overridden": overridden, "readings_tr": list(READINGS_TR),
                   "note_tr": "Geçmiş test (PAPER değil, canlı değil); kâr garantisi değildir. Hüküm yalnız ana seriden (32 "
                              "birincil hücre); mekân, uzun geçmiş ve mevcut setler bilgi/keşiftir."})
    data = dict(report.get("data") or {})
    for sec, prefixes in _DATA_OWNER:
        if sec in secs:
            data = {k: v for k, v in data.items() if not k.startswith(prefixes)}
    report["data"] = data
    runs: dict[str, Any] = dict(report.get("section_runs") or {})
    ev_by_sec: dict[str, list[dict]] = {s: [] for s in secs}

    def done(sec: str, t_sec: float) -> None:
        runs[sec] = {"generated_at": _iso(now_ms), "seconds": round(time.time() - t_sec, 1)}

    spot: dict[str, pd.DataFrame] = {}
    if "main" in secs or "existing" in secs:
        mw = win["main"]
        s_ms, e_ms = window_ms(mw["start"], mw["end"])
        need = sorted({*(GOLD_REGISTRY["sections"]["main"]["tfs"] if "main" in secs else []),
                       *([t for k in ("catalog", "extras", "algos", "variations") for t in GOLD_REGISTRY["sections"]["existing"][k]]
                         if "existing" in secs else [])}, key=tf_ms)
        for tf in need:
            df, q = load_spot(mw["symbol"], tf, s_ms, e_ms, cache.get)
            log(f"veri spot {mw['symbol']} {tf}: {len(df)} bar · eksik {q.get('missing_ratio')} · 24s+ boşluk {q['n_gaps_over_24h']}")
            if not len(df):
                raise GoldDataError(f"spot {mw['symbol']} {tf} serisi boş (pencere {mw['start']} → {mw['end']})")
            spot[tf] = df
            report["data"][f"spot {mw['symbol']} {tf}"] = q
    if "main" in secs:
        ts0 = time.time()
        tfs = GOLD_REGISTRY["sections"]["main"]["tfs"]
        res = _map(_gold_task, [(spot[tf], win["main"]["symbol"], tf, cfg_d, None) for tf in tfs], jobs)
        evs = [e for r in res for e in r[0]]
        metas = [r[1] for r in res]
        main = main_report(evs, metas, cfg)
        main["series"] = metas
        report["main"] = main
        ev_by_sec["main"] = [{**e, "section": "main"} for e in evs]
        log(f"ana seri: {sum(1 for e in evs if e['family'] == FAMILY)} işlem · {main['conclusion_tr']}")
        done("main", ts0)
    if "venue" in secs:
        ts0 = time.time()
        vw = win["venue"]
        evs, metas = [], []
        for sym, (a, b) in vw["symbols"].items():
            s_ms, e_ms = window_ms(a, b)
            fund = load_funding(sym, s_ms, e_ms, cache.get)
            report["data"][f"funding {sym}"] = {"rows": fund["rows"], "missing_months": fund["missing_months"]}
            tasks = []
            for tf in GOLD_REGISTRY["sections"]["venue"]["tfs"]:
                df, q = load_futures(sym, tf, s_ms, e_ms, cache.get, now_ms)
                report["data"][f"futures {sym} {tf}"] = q
                log(f"veri vadeli {sym} {tf}: {len(df)} bar")
                if len(df):
                    tasks.append((df, sym, tf, cfg_d, {"f_t": fund["f_t"], "f_rate": fund["f_rate"]}))
            for ev_, me_ in _map(_gold_task, tasks, jobs):
                evs += ev_
                metas.append(me_)
        report["venue"] = venue_report(evs, metas, cfg, report.get("main"))
        report["venue"]["series"] = metas
        ev_by_sec["venue"] = [{**e, "section": "venue"} for e in evs]
        done("venue", ts0)
    if "dukascopy" in secs:
        ts0 = time.time()
        dw = win["dukascopy"]
        root = Path(duka_root) if duka_root is not None else Path(cache_dir) / "dukascopy"
        cov = duka_coverage(root, dw["start"], dw["end"])
        report["data"]["dukascopy coverage"] = cov
        if not cov["enough"]:
            report["dukascopy"] = {"status": "yapılamadı", "coverage": cov,
                                   "tr": f"kapsama %{100 * cov['coverage']:.1f} < %{100 * DUKA_COVERAGE_MIN:.0f}; hüküm ana seriden"}
        else:
            frames, q = load_dukascopy(root, dw["start"], dw["end"], GOLD_REGISTRY["sections"]["dukascopy"]["tfs"])
            report["data"]["dukascopy"] = q
            empty = [tf for tf, f in frames.items() if not len(f)]
            if empty:
                report["dukascopy"] = {"status": "yapılamadı", "coverage": cov,
                                       "tr": f"okunan bar yok ({', '.join(empty)}); hüküm ana seriden"}
            else:
                res = _map(_gold_task, [(frames[tf], dw["symbol"], tf, cfg_d, None) for tf in frames], jobs)
                evs = [e for r in res for e in r[0]]
                metas = [r[1] for r in res]
                report["dukascopy"] = {**duka_report(evs, metas, cfg, report.get("main")), "coverage": cov, "series": metas}
                ev_by_sec["dukascopy"] = [{**e, "section": "dukascopy"} for e in evs]
        log(f"Dukascopy: {report['dukascopy']['status']} (kapsama %{100 * cov['coverage']:.1f})")
        done("dukascopy", ts0)
    if "existing" in secs:
        ts0 = time.time()
        tfs = sorted({t for k in ("catalog", "extras", "algos", "variations") for t in GOLD_REGISTRY["sections"]["existing"][k]}, key=tf_ms)
        res = _map(_existing_task, [(spot[tf], win["main"]["symbol"], tf, cfg_d) for tf in tfs], jobs)
        by_set = {k: [e for r in res for e in r[0][k]] for k in EXISTING_SETS}
        report["existing"] = existing_report(by_set, [m for r in res for m in r[1]], cfg)
        ev_by_sec["existing"] = [{**e, "section": f"existing:{k}"} for k, evs in by_set.items() for e in evs]
        done("existing", ts0)
    apply_notes(report)
    report["sections"] = [s for s in SECTIONS if s in report]
    report["section_runs"] = runs
    report["seconds"] = round(time.time() - t0, 1)
    report["archive_requests"] = cache.requests
    report = _clean(report)
    for sec in secs:
        write_events(out_dir / events_file(sec), ev_by_sec[sec])
    _write_text(out_dir / REPORT_JSON, json.dumps(report, ensure_ascii=False, indent=1))
    _write_text(out_dir / REPORT_MD, render_md(report))
    return report


# ---------------------------------------------------------------------------- markdown
def _f(x: Any, nd: int = 2, sign: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _ci(ci: Any) -> str:
    return f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "—"


def _cuts(cut: Mapping[str, Any] | None) -> str:
    """Dilim başına keşif/doğrulama kesimi (UTC, dakikaya kadar)."""
    items = sorted((cut or {}).items(), key=lambda kv: tf_ms(kv[0]))
    return " · ".join(f"{tf} {(_iso(v) or '—')[:16].replace('T', ' ')} UTC" for tf, v in items) or "—"


def _windows_line(win: Mapping[str, Any] | None) -> str:
    win = win or {}
    m, d = win.get("main") or {}, win.get("dukascopy") or {}
    ven = ", ".join(f"{k} {a} → {b}" for k, (a, b) in ((win.get("venue") or {}).get("symbols") or {}).items())
    return f"ana {m.get('start')} → {m.get('end')} · mekân {ven or '—'} · Dukascopy {d.get('start')} → {d.get('end')}"


def _cov_line(cov: Mapping[str, Any]) -> str:
    return (f"kapsaması %{100 * float(cov.get('coverage') or 0):.1f} ({cov.get('weekdays_ok', 0)}/{cov.get('weekdays', 0)} Pzt–Cum "
            f"günü; eşik %{100 * DUKA_COVERAGE_MIN:.0f}; manifest 200 ama dosya yok: {cov.get('weekdays_file_missing', 0)}; taban: "
            f"{cov.get('basis_tr') or DUKA_COVERAGE_BASIS_TR})")


def render_md(report: Mapping[str, Any]) -> str:
    """Kısa, düz Türkçe rapor."""
    out = [f"# Altın laboratuvarı — {report.get('version')} raporu", "",
           f"Ön kayıt mührü `GOLD_REGISTRY_SHA = {report.get('registry_sha')}` · belge `{report.get('doc')}` · bölümler: "
           f"{', '.join(report.get('sections') or [])} · gidiş-dönüş maliyet %{report.get('cost_round_trip_pct')}", "",
           "Geçmiş test; PAPER değil, canlı değil. Kâr garantisi değildir. Hüküm yalnız ana seriden verilir.", ""]
    if report.get("windows_overridden"):
        out += ["**UYARI: veri pencereleri ön kayıttan FARKLI (yalnız test için); bu rapor ön kayıtlı koşu DEĞİLDİR.** Kullanılan "
                f"pencereler: {_windows_line(report.get('data_windows'))}", ""]
    runs = report.get("section_runs") or {}
    if runs:
        out += ["Bölüm koşuları: " + " · ".join(f"{s} {r.get('generated_at')} ({r.get('seconds')} sn)" for s, r in runs.items()), ""]
    main = report.get("main")
    if main:
        out += ["## Sonuç", "", f"- {main.get('conclusion_tr')}", ""]
    data = report.get("data") or {}
    if data:
        out += ["## Veri", "", "| seri | bar | ilk | son | eksik bar | 24 saatten uzun boşluk |", "|---|---|---|---|---|---|"]
        rows = [(k, q) for k, q in data.items() if isinstance(q, Mapping) and "bars" in q]
        rows += [(f"dukascopy XAUUSD {tf}", q) for tf, q in ((data.get("dukascopy") or {}).get("series") or {}).items()]
        for k, q in rows:
            mr = q.get("missing_ratio")
            out.append(f"| {k} | {q.get('bars')} | {q.get('first') or '—'} | {q.get('last') or '—'} | "
                       f"{'—' if mr is None else f'%{100 * mr:.2f}'} | {q.get('n_gaps_over_24h', 0)} |")
        out.append("")
        if data.get("dukascopy coverage"):
            out += [f"- Dukascopy {_cov_line(data['dukascopy coverage'])}."]
        dq = data.get("dukascopy")
        if dq:
            out += [f"- Dukascopy okuma: okunan gün {dq.get('days_read', 0)} · dosyası olmayan gün {dq.get('days_missing_file', 0)} · "
                    f"1 dakikalık satır {dq.get('rows_1m', 0)} · hacmi 0 atılan {dq.get('rows_vol0', 0)} · tutarsız ya da makul "
                    f"aralık dışı atılan {dq.get('rows_bad', 0)} · yinelenen bar {sum((dq.get('bars_duplicate') or {}).values())}."]
        if data.get("dukascopy coverage") or dq:
            out.append("")
    if main:
        out += ["## Ana seri — 32 birincil hücre (bağlam HEPSİ)", "",
                f"Keşif/doğrulama kesimi (signal_lab.aggregate; her dilimde dönemin ilk 2/3'ü; belgedeki tarih kaba tahmindir, geçerli "
                f"olan budur): {_cuts(main.get('cutoff_ms'))}.", "",
                "Aylık %: yalnız TAM doğrulama ayları (kesimin düştüğü kısmi ay ve serinin sonunda işlem açılamayan kısa ay hariç; "
                "R'leri JSON'da `edge_months`); ay başına toplam R × 0,5 (işlem başına risk %0,5); ay kümeli %95 aralık. %1 risk ve "
                "maker maliyeti (%0,02 + 0 bps) yalnız bilgidir.", "",
                "| dilim | varyant | yön | işlem keşif/doğr. | ort.R keşif/doğr. | plaseboya göre | hüküm | sıkı | tam ay | aylık % | %95 "
                "| %1 risk | maker | ≥%1 ay | eşzamanlı en çok/ort. | hedef |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c in main.get("cells") or []:
            mon = c.get("monthly") or {}
            vs = c.get("vs_placebo")
            conc = mon.get("concurrency") or {}
            sh = mon.get("share_months_ge_target")
            out.append(f"| {c['tf']} | {c['name']} | {c['side']} | {c['IS'].get('n', 0)}/{c['OOS'].get('n', 0)} | "
                       f"{_f(c['IS'].get('mean_r'), 3)}/{_f(c['OOS'].get('mean_r'), 3)} | "
                       f"{(_f(vs.get('IS')) + '/' + _f(vs.get('OOS'))) if vs else '—'} | {c['verdict']} | {c['verdict_strict']} | "
                       f"{mon.get('months', 0)} | {_f(mon.get('mean_pct_month'))} | {_ci(mon.get('ci95_pct_month'))} | "
                       f"{_f(mon.get('mean_pct_month_risk1'))} | {_f((c.get('maker_info') or {}).get('mean_pct_month'))} | "
                       f"{'—' if sh is None else f'%{100 * sh:.0f}'} | {conc.get('max', '—')}/{_f(conc.get('mean'), 2, False)} | "
                       f"{'EVET' if c.get('meets_target') else 'hayır'} |")
        out += ["", "### İki yön birlikte", "", "| dilim | varyant | tam ay | aylık % | %95 | %1 risk | ≥%1 ay | sıkı (LONG/SHORT) | hedef |",
                "|---|---|---|---|---|---|---|---|---|"]
        for b in main.get("both_sides") or []:
            mon = b.get("monthly") or {}
            sh = mon.get("share_months_ge_target")
            out.append(f"| {b['tf']} | {b['name']} | {mon.get('months', 0)} | {_f(mon.get('mean_pct_month'))} | "
                       f"{_ci(mon.get('ci95_pct_month'))} | {_f(mon.get('mean_pct_month_risk1'))} | "
                       f"{'—' if sh is None else f'%{100 * sh:.0f}'} | {'/'.join(b.get('verdict_strict') or [])} | "
                       f"{'EVET' if b.get('meets_target') else 'hayır'} |")
        cr, crp = main.get("candidate_rate") or {}, main.get("candidate_rate_primary") or {}
        pct = lambda x: "—" if x is None else f"%{100 * x:.1f}"  # noqa: E731
        out += ["", f"Aday oranı (iki dönemde de aralık 0'ın üstünde): birincil hücreler gerçek {pct(crp.get('real'))} · plasebo "
                    f"{pct(crp.get('placebo'))} ({crp.get('real_tested', 0)}/{crp.get('placebo_tested', 0)} hücre hükme girdi); "
                    f"bütün bağlam dilimleri gerçek {pct(cr.get('real'))} · plasebo {pct(cr.get('placebo'))}. GÜÇLÜ ADAY "
                    f"{crp.get('strong', 0)}, sıkı GÜÇLÜ ADAY {crp.get('strict_strong', 0)} (32 hücrede tesadüfen 0–1 beklenir).", ""]
    ven = report.get("venue")
    if ven:
        out += ["## Mekân denetimi — Binance vadeli (bilgi, dönem bölünmeden)", "",
                f"'{NOTE_VENUE}' notu: " + ("ana seri hücrelerinden hesaplandı (ana seride GÜÇLÜ ADAY, burada n ≥ 20 ve ort.R ≤ 0)."
                                            if ven.get("notes_evaluated") else "DEĞERLENDİRİLMEDİ (raporda ana seri yok)."), "",
                "| sembol | dilim | varyant | yön | n | ort.R | kazanma | fonlama R | plasebo ort.R | not |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in ven.get("rows") or []:
            st = r.get("all") or {}
            wr = st.get("win_rate")
            out.append(f"| {r['symbol']} | {r['tf']} | {r['name']} | {r['side']} | {st.get('n', 0)} | {_f(st.get('mean_r'), 3)} | "
                       f"{'—' if wr is None else f'%{100 * wr:.0f}'} | {_f((r.get('funding_r') or {}).get('mean'), 3)} | "
                       f"{_f(r.get('placebo_mean_r'), 3)} | {r.get('note') or ''} |")
        out.append("")
    duka = report.get("dukascopy")
    if duka:
        out += ["## Uzun geçmiş — Dukascopy XAUUSD (koşullu)", ""]
        cov = duka.get("coverage") or {}
        if duka.get("status") != "koşuldu":
            out += [f"Yapılamadı: {duka.get('tr')}. Dukascopy {_cov_line(cov)}. Hüküm ana seriden verilir.", ""]
        else:
            out += [f"Dukascopy {_cov_line(cov)}.", "",
                    f"Keşif/doğrulama kesimi: {_cuts(duka.get('cutoff_ms'))}.", "",
                    f"'{NOTE_DUKA}' notu: " + ("ana seri hücrelerinden hesaplandı (ana seride GÜÇLÜ ADAY, burada doğrulama ort.R ≤ 0)."
                                               if duka.get("notes_evaluated") else "DEĞERLENDİRİLMEDİ (raporda ana seri yok)."), "",
                    "| dilim | varyant | yön | işlem keşif/doğr. | ort.R keşif/doğr. | hüküm | sıkı | not |", "|---|---|---|---|---|---|---|---|"]
            for c in duka.get("cells") or []:
                out.append(f"| {c['tf']} | {c['name']} | {c['side']} | {c['IS'].get('n', 0)}/{c['OOS'].get('n', 0)} | "
                           f"{_f(c['IS'].get('mean_r'), 3)}/{_f(c['OOS'].get('mean_r'), 3)} | {c['verdict']} | {c['verdict_strict']} | "
                           f"{c.get('note') or ''} |")
            out.append("")
    ex = report.get("existing")
    if ex:
        pct = lambda x: "—" if x is None else f"%{100 * x:.1f}"  # noqa: E731
        out += ["## Mevcut laboratuvar setleri — KEŞİF", "", ex.get("label_tr", ""), "", f"- {ex.get('xsmom_tr')}", ""]
        titles = {"lab": "Katalog + ek sinyaller + algoritmalar", "variations": "C4 mum varyasyonları (yalnız-varyasyon koşusu)"}
        for k, st in (ex.get("sets") or {}).items():
            cr = st.get("candidate_rate") or {}
            out += [f"### {titles.get(k, k)}", "",
                    f"- Hükme giren gerçek grup: {st.get('tested', 0)} · aday oranı gerçek {pct(cr.get('real'))} · plasebo "
                    f"{pct(cr.get('placebo'))}",
                    f"- Hükümler (bütün dilimler): {st.get('verdicts_all')} · sıkı: {st.get('verdicts_strict_all')}",
                    f"- Hükümler (HEPSİ): {st.get('verdicts_hepsi')} · sıkı: {st.get('verdicts_strict_hepsi')}", ""]
            if st.get("top"):
                out += ["| dilim | aile | ad | yön | bağlam | ort.R keşif/doğr. | hüküm | sıkı |", "|---|---|---|---|---|---|---|---|"]
                for g in st["top"]:
                    out.append(f"| {g['tf']} | {g['family']} | {g['name']} | {g['side']} | {g['context']}={g['bucket']} | "
                               f"{_f(g['IS'].get('mean_r'), 3)}/{_f(g['OOS'].get('mean_r'), 3)} | {g['verdict']} | "
                               f"{g.get('verdict_strict')} |")
                out.append("")
    out += ["Belirsiz kuralların okunuşu `GOLD_REGISTRY['readings_tr']` içindedir (mühre dahil).", ""]
    return "\n".join(out)


__all__ = ["ArchiveCache", "EXISTING_SETS", "FAMILY", "GOLD_REGISTRY", "GOLD_REGISTRY_SHA", "GOLD_VERSION", "GoldDataError",
           "PLACEBO_PREFIX", "READINGS_TR", "REPORT_JSON", "REPORT_MD", "RULES", "SECTIONS", "apply_notes", "bi5_sanity",
           "conclusion_tr", "concurrency", "confirmed", "decode_bi5", "duka_coverage", "duka_path", "duka_report", "events_file",
           "existing_report", "gold_events", "htf_side", "in_session", "load_dukascopy", "load_funding", "load_futures", "load_spot",
           "main_report", "meets_target", "month_bounds", "monthly_stats", "names_for_tf", "note_duka", "note_venue",
           "parse_spot_zip", "placebo_events", "prior_report", "process", "render_md", "resample", "run", "series_quality",
           "swing_pivots", "variant_cfg", "venue_report", "window_ms"]
