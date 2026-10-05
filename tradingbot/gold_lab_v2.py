# -*- coding: utf-8 -*-
"""ALTIN LABORATUVARI — gold_v2 (docs/GOLD_LAB_V2.md; ön kayıt 2026-10-05): günlük/4 saatlik trend takibi (aile A, Dukascopy
XAUUSD 2006-01 → 2020-07, bu depoda hiç kullanılmamış veri) ve PAXG hafta sonu geri dönüşü (aile B, Binance spot PAXGUSDT
2020-08-28 → 2026-09-30) maliyet ve fonlama vekili sonrası, aynı maruziyetteki rastgele girişten daha iyi mi; defter başına
aylık net +%1 (işlem başına %0,5 risk) hedefi tutuyor mu? Salt araştırma: defterlere, stratejilere, ajanlara ve parametrelere
DOKUNMAZ; yalnız geçmiş mumları okur, rapor yazar. İçe aktarılırken ağa çıkmaz.

* ÖN KAYIT: bütün kural sabitleri ve metinleri (12 varyant, 24 hüküm hücresi, 3 ANA hücre, Dukascopy çözme/temizlik/işlem
  günü/4h kutu/segment/kapsama/zaman damgası kuralları, fonlama vekili, plasebolar, sabit tarihli dönemler, aylık hedef ölçüsü,
  "daha yüksek risk" satırı, C, görülmüş/mekân satırları, çoklu deneme, öneri kuralı) ve belgenin belirsiz yerlerinin okunuşu
  (`READINGS_TR`) `GOLD_V2_REGISTRY`'dedir; `GOLD_V2_REGISTRY_SHA` testle sabitlenir ve belgeye yazılır. gold_v1'in kaydı
  (`gold_lab.GOLD_REGISTRY`) değişmez.
* Simülasyon `signal_lab.simulate_rule` DEĞİŞMEDEN (yeni çıkış türleri `channel20` ve `month_end` onun `channel` ve `hold`
  türlerine eşlenir); istatistik `signal_lab.r_stats`, hüküm `verdict` / `diff_ci` / `verdict_strict` değişmeden; aylık ölçü
  `gold_lab.monthly_stats` / `meets_target`; Binance yükleyicileri ve bi5 denetimi `gold_lab`'dan. Yalnız sabit tarihli dönem
  etiketi `aggregate`'in 2/3 kesiminin yerine geçer.

PAPER/geçmiş testtir; kâr garantisi değildir.
"""
from __future__ import annotations

import calendar
import csv
import dataclasses
import gzip
import hashlib
import json
import lzma
import math
import os
import time
import zlib
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd

from . import gold_lab as G
from . import signal_lab as L
from .timeframes import tf_ms

LONG, SHORT = L.LONG, L.SHORT
BOTH_CELL = "İKİ YÖN"                 # hücre adı
BOTH_KEY = "İKİ"                      # plasebo anahtarındaki yön
CELLS = (LONG, BOTH_CELL)
GOLD2_VERSION = "gold_v2"
GOLD2_DOC = "docs/GOLD_LAB_V2.md"
FAMILY = "gold2"
PLACEBO_FAMILY = "placebo"
INFO_FAMILY = "placebo_info"
PLACEBO_PREFIX = "PLACEBO_"
INFO_PREFIX = "INFO_"
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
H8_MS = 8 * HOUR_MS
TZ_NY = "America/New_York"

# ---------------------------------------------------------------------------- ön kayıtlı sabitler
DUKA_SYMBOL = "XAUUSD"
PAXG = "PAXGUSDT"
DUKA_H_FILE = "BID_candles_hour_1.bi5"
DUKA_D_FILE = "BID_candles_day_1.bi5"
MIRROR_DONE_LINE = "hourly mirror pass finished"
DAY_SHIFT_H = 7                       # D(t) = (New York yerel saati + 7 saat)'in takvim tarihi; işlem günü 17:00 NY'de başlar
BIN_H = 4                             # 4h kutu: işlem günü içinde ⌊(yerel − 17:00) / 4 saat⌋
GAP_DUKA_H, GAP_BINANCE_H = 120, 48   # segment eşiği (saat)
WEEK_GAP_H = 40                       # "haftanın ilk saati": önceki tutulan bar 40 saatten eski
HW_SUMMER, HW_WINTER = (21, 22), (22, 23)
HW_MIN_SHARE = 0.90
COV_MONTH_SHARE, COV_MONTH_BARS, COV_HOURS_PER_DAY = 0.95, 0.85, 23
COV_DAY_SHARE, COV_DAY_MIN_BARS = 0.95, 20
SHORT_SESSION_BARS = 12
WEEKEND_FLAG_SHARE = 0.01
WARMUP = 210                          # gün/4h: segment içi sıra ≥ 210
MONTH_WARMUP_DAYS = 30                # aylık: i_m segment başından ≥ 30 günlük bar sonra
MONTH_END_WEEKDAYS = 3                # ay sonu barı ayın son 3 Pzt–Cum günü içinde
MONTH_SAFETY_BARS = 31                # month_end güvenlik sınırı (hiç bağlamaz)
RISK_MAX_ATR = 10.0                   # simulate_rule'un sabit üst sınırı (risk > 10 ATR → işlem yok)
FUND_RATE_8H = 0.0001                 # fonlama vekili: 8 saatte %0,01 (long öder, short alır)
K_MATCH = 5                           # maruziyet eşli plasebo: gerçek işlem başına 5 eş
PROFILE_CAP_RISK = 2.0                # risk profili tavanı (config_v3 profil tavanı 2.0; PAPER_RESEARCH risk_per_trade_pct)
OPEN_RISK_CAP, OPEN_RISK_LEARNING = 6.0, 100.0
DD_FLAG_PCT = 20.0
LEV_PAPER, LEV_MAX = 2.0, 5.0
C_TARGET_VOL, C_MAX_W, C_TOTAL_CAP = 0.10, 2.0, 2.0
C_SIG_BARS = 61
ANN_DUKA, ANN_BINANCE = 260, 365
NOTE_VENUE = G.NOTE_VENUE             # "mekânda tutmadı"
NO_TARGET_TR = "bu kurallar altında maliyet ve fonlama sonrası +%1/ay %0,5 riskte bulunamadı"
CALIB_SEED = 20261005
CALIB_WORLDS = 20

_A, _B = "A", "B"
_ENTRY_DONCH20 = "signal_lab TREND_DONCHIAN_20_10 aynen: close[i] > hi20[i] ve close[i−1] ≤ hi20[i−1]"
VARIANTS: dict[str, dict[str, Any]] = {
    "A_DONCH_20_10": {
        "family": _A, "tf": "1d", "bars": "1d", "entry": "breakout", "n": 20, "stop_atr": 2.0,
        "exit": {"kind": "channel", "n": 10, "max_bars": 300}, "placebo": "matched", "lab_twin": "TREND_DONCHIAN_20_10",
        "entry_tr": _ENTRY_DONCH20, "stop_tr": "close[i] − 2 ATR[i]",
        "exit_tr": "channel: close[k] < lo10[k] → k+1 açılışı; en çok 300 bar"},
    "A_TSMOM_28": {
        "family": _A, "tf": "1d", "bars": "1d", "entry": "tsmom", "stop_atr": 3.0,
        "exit": {"kind": "sign", "max_bars": 300}, "placebo": "matched", "lab_twin": "TSMOM_28",
        "entry_tr": "signal_lab TSMOM_28 aynen: mom28[i] > 0 ≥ mom28[i−1]", "stop_tr": "close[i] − 3 ATR[i]",
        "exit_tr": "sign: mom28[k] ≤ 0 → k+1 açılışı; en çok 300 bar"},
    "A_DONCH_55_20": {
        "family": _A, "tf": "1d", "bars": "1d", "entry": "breakout", "n": 55, "stop_atr": 2.0,
        "exit": {"kind": "channel20", "n": 20, "max_bars": 300}, "placebo": "matched",
        "entry_tr": "close[i] > hi55[i] ve close[i−1] ≤ hi55[i−1] (Turtle Sistem 2)", "stop_tr": "close[i] − 2 ATR[i]",
        "exit_tr": "channel20 (yeni tür): close[k] < lo20[k] → k+1 açılışı; en çok 300 bar"},
    "A_TSMOM_1M": {
        "family": _A, "tf": "1M", "bars": "1d", "entry": "month_mom", "months": 1, "stop_atr": 5.0,
        "exit": {"kind": "month_end", "safety_max_bars": MONTH_SAFETY_BARS}, "placebo": "monthly",
        "entry_tr": "Ay sonu: close[i_m] / close[i_{m−1}] − 1 > 0", "stop_tr": "close[i_m] − 5 ATR[i_m]",
        "exit_tr": "month_end (ay m+1'in son barının kapanışı)"},
    "A_TSMOM_3M": {
        "family": _A, "tf": "1M", "bars": "1d", "entry": "month_mom", "months": 3, "stop_atr": 5.0,
        "exit": {"kind": "month_end", "safety_max_bars": MONTH_SAFETY_BARS}, "placebo": "monthly",
        "entry_tr": "close[i_m] / close[i_{m−3}] − 1 > 0", "stop_tr": "close[i_m] − 5 ATR[i_m]", "exit_tr": "month_end"},
    "A_TSMOM_12M": {
        "family": _A, "tf": "1M", "bars": "1d", "entry": "month_mom", "months": 12, "stop_atr": 5.0,
        "exit": {"kind": "month_end", "safety_max_bars": MONTH_SAFETY_BARS}, "placebo": "monthly",
        "entry_tr": "close[i_m] / close[i_{m−12}] − 1 > 0 (MOP'un ana tanımı)", "stop_tr": "close[i_m] − 5 ATR[i_m]",
        "exit_tr": "month_end"},
    "A_SMA10M": {
        "family": _A, "tf": "1M", "bars": "1d", "entry": "month_sma", "months": 10, "stop_atr": 5.0,
        "exit": {"kind": "month_end", "safety_max_bars": MONTH_SAFETY_BARS}, "placebo": "monthly",
        "entry_tr": "close[i_m] > son 10 ay sonu kapanışının ortalaması (m dahil)", "stop_tr": "close[i_m] − 5 ATR[i_m]",
        "exit_tr": "month_end"},
    "A_4H_DONCH_D200": {
        "family": _A, "tf": "4h", "bars": "4h", "entry": "breakout", "n": 20, "d200": True, "stop_atr": 2.0,
        "exit": {"kind": "channel", "n": 10, "max_bars": 180}, "placebo": "matched",
        "entry_tr": ("4h'te close[i] > hi20[i] ve close[i−1] ≤ hi20[i−1] VE kapanış anı ≤ karar anı olan son günlük barda close > "
                     "SMA200(1d)"),
        "stop_tr": "close[i] − 2 ATR(4h)[i]", "exit_tr": "channel (4h): close[k] < lo10[k] → k+1 açılışı; en çok 180 bar (30 işlem günü)"},
    "B_WKND_REV_ALL": {
        "family": _B, "tf": "1h", "bars": "1h", "entry": "weekend", "z_min": 0.0, "z_strict": True, "hold_h": 23, "stop_atr": 1.0,
        "exit": {"kind": "hold"}, "placebo": "weekend",
        "entry_tr": "|z| > 0: z < 0 → LONG, z > 0 → SHORT", "stop_tr": "close[i] ∓ 1,0 ATR_d",
        "exit_tr": "hold: S + 23 saat = Pazartesi 17:00 NY (ana piyasanın ilk gün kapanışı)"},
    "B_WKND_REV_050": {
        "family": _B, "tf": "1h", "bars": "1h", "entry": "weekend", "z_min": 0.5, "z_strict": False, "hold_h": 23, "stop_atr": 1.0,
        "exit": {"kind": "hold"}, "placebo": "weekend",
        "entry_tr": "|z| ≥ 0,5", "stop_tr": "close[i] ∓ 1,0 ATR_d", "exit_tr": "hold: Pazartesi 17:00 NY (S + 23 saat)"},
    "B_WKND_REV_100": {
        "family": _B, "tf": "1h", "bars": "1h", "entry": "weekend", "z_min": 1.0, "z_strict": False, "hold_h": 23, "stop_atr": 1.0,
        "exit": {"kind": "hold"}, "placebo": "weekend",
        "entry_tr": "|z| ≥ 1,0", "stop_tr": "close[i] ∓ 1,0 ATR_d", "exit_tr": "hold: Pazartesi 17:00 NY (S + 23 saat)"},
    "B_WKND_REV_050_LDN": {
        "family": _B, "tf": "1h", "bars": "1h", "entry": "weekend", "z_min": 0.5, "z_strict": False, "hold_h": 9, "stop_atr": 1.0,
        "exit": {"kind": "hold"}, "placebo": "weekend",
        "entry_tr": "|z| ≥ 0,5", "stop_tr": "close[i] ∓ 1,0 ATR_d", "exit_tr": "hold: S + 9 saat ≈ Londra 08:00"},
}
A_NAMES = tuple(n for n, v in VARIANTS.items() if v["family"] == _A)
B_NAMES = tuple(n for n, v in VARIANTS.items() if v["family"] == _B)
MONTHLY = ("month_mom", "month_sma")
MAIN_CELLS = (("A_DONCH_20_10", LONG), ("A_TSMOM_28", LONG), ("B_WKND_REV_ALL", BOTH_CELL))

#: ön kayıtlı pencereler (UTC; bitiş günü dahil). Testler `run(data_windows=...)` ile değiştirebilir; rapor bunu yazar.
WINDOWS: dict[str, Any] = {
    "A": {"is": ["2006-01-01", "2013-12-31"], "oos": ["2014-01-01", "2020-07-31"], "months": ["2014-01", "2020-06"],
          "open_end": "2020-07-01"},
    "B": {"is": ["2020-08-28", "2023-12-31"], "oos": ["2024-01-01", "2026-09-30"], "months": ["2024-01", "2026-09"],
          "open_end": "2026-10-01"},
    "seen": {"paxg": ["2020-08-28", "2026-09-30"], "dukascopy": ["2020-08-01", "2026-09-30"]},
    "venue": {"XAUUSDT": ["2025-12-01", "2026-09-30"], "PAXGUSDT": ["2025-03-01", "2026-09-30"]},
    "calib": {"a": ["2006-01-01", "2020-07-31"], "b": ["2020-08-28", "2026-09-30"]},
}

#: Belgenin belirsiz yerleri için EN HARFİYEN okunuş (kodun yaptığı tam olarak budur; mühre girer).
READINGS_TR = (
    "plasebo anahtarları: <ts[i]> = karar barının AÇILIŞ zamanı (UTC ms, tamsayı); <sembol> = XAUUSD (Dukascopy), PAXGUSDT "
    "(spot), XAUUSDT/PAXGUSDT (vadeli); <dilim> = 1d / 4h / 1h, aylık varyantlarda '1M'; <ad> = varyant adı (öneksiz); "
    "crc32 = zlib.crc32(UTF-8 metin); u = crc32 / 2³²",
    "maruziyet eşli plasebo seçimi: C(P_τ)[crc32(anahtar) mod |C|] (crc32 TAMSAYISI, 2³²'ye bölünmeden); C kronolojik sıralı; "
    "K = 5 seçim iadelidir (aynı bar iki kez seçilebilir); aday yoksa eş yok ve ESŞ_ADAY_YOK sayılır",
    "dönem: karar anı t_ms (karar barının kapanışı; aile B'de S) [başlangıç günü 00:00 UTC, bitiş günü + 1 gün 00:00 UTC) "
    "aralığında; keşif ⇔ t_ms < doğrulama başlangıcı; aralık dışındaki karar DÖNEM_DIŞI sayılır ve hiçbir hücreye girmez",
    "işlem günü: D(t) = (t'nin America/New_York yerel saati + 7 saat)'in takvim tarihi, t = saatlik barın AÇILIŞ zamanı; günlük bar "
    "zaman damgası (D−1) 17:00 NY, kapanış anı D 17:00 NY (UTC'ye zoneinfo ile); 4h kutu başı = t − ((yerel + 7 saat) − D − 4 × "
    "kutu), kapanış = kutu başı + 4 saat (işlem günü içinde yaz/kış geçişi olmaz: geçiş Pazar 02:00'dedir, o saatin D'si Pazar)",
    "temizlik sırası: hacim 0 VE açılış = yüksek = düşük = kapanış → kapalı piyasa (atılır); kalanlardan bi5_sanity'yi geçmeyen → "
    "geçersiz (atılır); hacmi 0 hareketli ve hacmi > 0 düz satırlar tutulur ve ayrı sayılır; yıl = satırın UTC yılı",
    "çözme hatası: LZMA açılamıyorsa (bozuk/yarım dosya) ay 'çözülemedi' → kullanılamaz ve listelenir; açılıp kayıt denetimi "
    "(uzunluk 24'ün katı, saniye aralığı, kesin artış, t % 3600 / t % 86400) tutmazsa koşu durur (GoldDataError)",
    "kullanılabilir ay (kapsama): manifest'te kind 'hour', status 200 ve bayt > 0 satırı VE dosya aynada boş değil VE LZMA "
    "açılıyor VE temizlik sonrası o dosyanın pencere içindeki geçerli saatlik barı ≥ 0,85 × ayın takvim Pzt–Cum gün sayısı × 23; "
    "kullanılamaz bir ayın çözülebilen barları seriden atılmaz (yalnız kapsama eşiğinde ve aylık karar şartında kullanılamaz sayılır)",
    "gün şartı (kapsama): payda = dönemin bütün takvim Pzt–Cum tarihleri (tatiller dahil); pay = D'si o tarih olan geçerli saatlik "
    "bar sayısı ≥ 20 olan tarihler",
    "zaman damgası denetimi: hafta başı = önceki tutulan saatlik bardan > 40 saat sonra gelen tutulan bar (serinin ilk barı "
    "hariç); yaz/kış = o barın anında America/New_York'un UTC farkı −4 / −5 saat; dönemi o barın zamanı; bir dönemde hafta "
    "yoksa denetim geçmez; Cuma'nın son saati = hafta başından önceki tutulan bar (bilgi)",
    "segment: tutulan bütün saatlik barlar (D'si hafta sonu olanlar dahil) üzerinde ardışık fark > 120 saat (Binance: kendi "
    "barlarında > 48 saat) → yeni segment; günlük/4h barın segmenti saatlerinin segmentidir; ATR14, kanallar, mom28, SMA200, "
    "C'nin σ'sı segment başından yeniden hesaplanır; ısınma = segment içi sıra ≥ 210; BOŞLUK_GERİ_BAKIŞ = ilk segment dışındaki "
    "segmentlerde ısınmada kalan bar sayısı",
    "segmentler arası eşleme: Dukascopy'de 4h barın SMA200(1d) barı ve C'nin günlük σ barı karar barıyla aynı segmentte olmalı; "
    "Binance'te 4h/1h ve 1d ayrı seriler olduğu için yalnız günlük serinin kendi segmenti aranır",
    "KESİLDİ / BOŞLUK_TUTUŞ: signal_lab.simulate_rule karar barından segment sonuna kadar olan dilimde koşar; NO_FUTURE_DATA → "
    "dilim serinin son segmentiyse KESİLDİ, değilse BOŞLUK_TUTUŞ; KESİLDİ piyasaya göre kapanış (bilgi): girişten veri sonuna "
    "kadar stop görülmediyse son kapanışta, görüldüyse stopta (maliyet dahil)",
    "çıkış eşlemesi: 'channel' (lo10/hi10) ve 'channel20' (lo20/hi20) simulate_rule'un 'channel' türüyle, aux'ta lo10/hi10 "
    "yerine ilgili kanal verilerek; 'hold' (N bar) simulate_rule 'hold' bars = max_bars = N; 'month_end' = simulate_rule 'hold' "
    "bars = n + 1 (hiç tetiklenmez), max_bars = n (ay m+1'in bar sayısı) → TIME çıkışı ay m+1'in son barının kapanışında; JSON'da "
    "exit_reason TIME, exit_kind MONTH_END",
    "ay sonu: i_m = takvim ayının (Dukascopy'de D'nin, Binance'te UTC tarihinin ayı) son günlük barı; 'son 3 Pzt–Cum günü içinde' "
    "= tarihi ≥ ayın sondan üçüncü Pzt–Cum takvim günü (7/24 seride Cumartesi/Pazar ay sonu barı da bu aralıktadır); kullanılabilir "
    "ay = bu şart VE (Dukascopy) kapsama anlamında kullanılabilir ay; m+1 veride hiç yoksa ve m serinin son ayıysa karar verilir ve "
    "işlem KESİLDİ sayılır (plaseboda aynı); m+1 serinin ortasında eksik, kullanılamaz, başka segmentte ya da i_m + 1 ilk barı "
    "değilse karar yok",
    "aylık şartlar: i_m'nin segment içi sırası ≥ 30; ATR14 sonlu ve > 0; A_TSMOM_L'de m−L'nin, A_SMA10M'de m−9..m'nin ay sonu "
    "barları var, kullanılabilir ve i_m ile aynı segmentte; getiri tam 0 ya da kapanış ortalamaya eşit → karar yok",
    "fonlama vekili: n_8s = ⌊çıkış / 8 saat⌋ − ⌊giriş / 8 saat⌋ (ms; 00/08/16 UTC uzlaşmaları 1970'ten 8 saatin katlarıdır); giriş "
    "anı = giriş barının açılışı; çıkış anı: kural ve hold çıkışında çıkış barının açılışı, stopta stop barının kapanışı, süre ve "
    "month_end çıkışında son barın kapanışı; R_fon = R − s × 0,0001 × n_8s × giriş / risk",
    "vadeli net R = R + funding_r (futures_lab.funding_carry: −s·Σ oran·px / risk); belgedeki 'R − funding_r' fonlamanın MALİYET "
    "olarak yazılışıdır (vekille aynı işaret: pozitif oranda LONG öder); funding_r NaN → net R yok, sayılır, ortalamaya girmez "
    "(plaseboda aynısı)",
    "maruziyet eşli plasebo: H_τ simulate_rule'un hold alanından: RULE → hold − 1, STOP → hold, TIME → max_bars; aday: segment "
    "içi sıra ≥ 210, ATR sonlu ve > 0, karar anı P_τ'da, c + 1 + H_τ ≤ segment sonu; eş stopu close[c] ∓ kat × ATR[c] (vadeli "
    "mekânda close ve ATR sinyal serisinin, yani PAXG spot'un); İKİ YÖN eşleri = LONG ve SHORT eşlerinin birleşimi",
    "aylık plasebo: aday = işaret şartı dışındaki bütün aylık şartları geçen ay sonu barları; p = LONG (SHORT bilgi satırında "
    "SHORT) sinyal sayısı (simülasyondan önce) / aday sayısı; İKİ YÖN işaret rastgeleleştirmesi gerçek sinyal verilen aylarda",
    "aile B: F ve S Cuma'nın tarihinden: Cuma 17:00 ve iki gün sonraki Pazar 18:00 New York (zoneinfo); P_F = açılışı F − 1 saat, "
    "P_S = açılışı S − 1 saat olan barın kapanışı; ATR_d = kapanışı ≤ F olan son Binance 1d barının ATR14'ü (segment içi); denetim "
    "sırası: VERİ_SONU (S − 1 saat verinin son barından sonra; hafta hiç sayılmaz), EKSİK_BAR (F − 1 ya da S − 1 barı yok), "
    "BOŞLUK (F − 1 ile S − 1 farklı segmentte), EKSİK_BAR (S..S + N saatlerinde açılan barlardan biri yok ya da ardışık değil; "
    "verinin son barından sonraki saatler aranmaz), HACİMSİZ, ATR_YOK; tutuş verinin sonunu aşan hafta uygundur, sinyal kurulur ve "
    "simülasyon KESİLDİ der; uygun hafta = bu atlamalara takılmayan hafta, varyantın N'sine göre, z'den bağımsız",
    "aile B eşiği: B_WKND_REV_ALL |z| > 0, diğerleri |z| ≥ eşik; z tam 0 → işlem yok",
    "aile B plasebosu: p = LONG (SHORT bilgi satırında SHORT) sinyal sayısı / uygun hafta sayısı; İKİ YÖN işaret "
    "rastgeleleştirmesi gerçek sinyal (simülasyondan önceki olay) verilen haftalarda; stop o haftanın ATR_d'si, tutuş N",
    "bilgi plaseboları: A gün/4h: aday = segment içi sıra ≥ 210 ve ATR sonlu > 0 her bar; B her saat: aday = ATR_d'si (kapanışı ≤ "
    "barın kapanışı olan son 1d bar) tanımlı her 1h bar, EKSİK_BAR ve HACİMSİZ (karar barının hacmi 0) ona da uygulanır; anahtar "
    "'<sembol>|<dilim>|<ad>|<yön>|<ts>' (gold_v1 düzeni); p = o yönün sinyal sayısı / aday; İKİ YÖN = iki yönün birleşimi",
    "hüküm: signal_lab.r_stats (gün = t_ms // 1 gün, UTC), verdict, diff_ci, verdict_strict değişmeden; grup aggregate'in HEPSİ "
    "grubuyla aynı formüller (plasebo farkı için plasebonun iki dönemde de n ≥ min_oos ve gerçeğin iki dönemde n > 0); diff_ci "
    "yalnız standart GÜÇLÜ ADAY'da vs_placebo['ci95']'e girer (aggregate gibi); farkın aralığı her hücrede ayrıca bilgi olarak "
    "'diff_ci' alanında; bağlam dilimleri hesaplanmaz",
    "aday oranı: aggregate/gold_lab tanımı (VERİ AZ olmayan hücrelerde replicated VE keşif ort.R > 0 payı); hüküm plasebosunun "
    "her hücresi kendi başına (plasebosuz) aynı hükümden geçer; aile başına ve toplam",
    "aylık ölçü: gold_lab.monthly_stats(key='r_fon'; kesim = doğrulama başlangıcı − 1 ms; open_end_ms = A 2020-07-01, B 2026-10-01; "
    "last_ms = hüküm serisi sonu − 1 ms); ay = karar anının UTC takvim ayı; eşzamanlılık [entry_ms, exit_ms), exit_ms = fonlama "
    "vekilindeki çıkış anı; aynı ölçü 'r' ile bilgi",
    "daha yüksek risk: X = 1 / (ortalama aylık R_fon) yüzde (yuvarlanmamış); aralık [a × X / 0,5, b × X / 0,5], [a, b] = %0,5 "
    "riskteki aylık % aralığı; toplam açık risk = doğrulama eşzamanlılığının en çoğu × X; fazla: ay R_fon − ay işlem sayısı × "
    "hüküm plasebosunun doğrulama ort. R_fon'u (yalnız tam aylar); yön kayması payı = ortalama aylık plasebo kısmı / ortalama aylık "
    "R_fon; düşüş: bütün hüküm serisi işlemleri çıkış anına göre sıralı birikimli R_fon, tepe 0'dan başlar, × X; ima edilen "
    "kaldıraç bütün hüküm serisi işlemlerinde (X / 100) / (risk / giriş)",
    "C: σ_yıllık = kapanış anı ≤ karar anı olan son 61 günlük kapanışın 60 log getirisinin örneklem standart sapması (ddof = 1) × "
    "√yıllık (Dukascopy 260, Binance 365), 61 bar aynı segmentte (Dukascopy'de karar barının segmenti); σ tanımsız ya da 0 → "
    "ağırlık 0 (sayılır); tavan giriş sırasıyla (eşit girişte karar sırasıyla), açık işlem = entry_ms ≤ t < exit_ms",
    "görülmüş seriler: PAXG spot 2020-08-28 → 2026-09-30 ve Dukascopy 2020-08-01 → 2026-09-30 kendi pencereleriyle AYRI seri "
    "(ısınma pencere içinde; Dukascopy görülmüş serisine kapsama/zaman damgası eşiği uygulanmaz, ölçüler raporlanır); dönem "
    "bölünmez; eş plasebo adayı bütün seri; fark ve aralığı bilgi",
    "vadeli mekân aile A: sinyal, stop ve kural çıkışının girdileri (ATR, kanal, mom28, ısınma) PAXG spot barlarından; işlem aynı "
    "açılış zaman damgalı vadeli barında; vadelide karşılığı olmayan karar barı MEKÂN_BAR_YOK; segment ve ay yapısı (month_end) "
    "vadeli serininki; aile B mekân: kendi 1h/1d barlarıyla",
    "mekânda tutmadı: hücrenin STANDART hükmü GÜÇLÜ ADAY ve aynı varyant/hücrenin bir vadeli satırında net (gerçek fonlamalı) "
    "n ≥ 20 ve ort. net R ≤ 0",
    "ayna hazır: aynada .part dosyası yok, verilen günlükte 'hourly mirror pass finished' satırı var, /proc'ta komut satırında "
    "ayna kökünü içeren başka süreç yok; anlık görüntü: manifest.jsonl ve okunan bütün bi5 dosyalarının sha256'sı koşu başında "
    "ve sonunda; kesim anı = manifest'teki en geç ts",
    "günlük dosya (bilgi): yıl başından saniye, t % 86400 == 0; kapalı piyasa (düz, hacim 0) satırları atılır; fark = ayın günlük "
    "dosyadaki son barının kapanışı − saatlik türetimin ay sonu kapanışı; fark ve mutlak fark medyanı",
    "kısmi gün: ayın ilk işlem günü (D'nin ayı) için önceki takvim ayının saatlik dosyası okunmadıysa KISMİ_GÜN; hafta sonu "
    "saatleri: D'si Cumartesi/Pazar olan tutulan saatler, payda verisi olan ISO hafta sayısı; kısa seans: < 12 geçerli saatlik "
    "barı olan Pzt–Cum D günü; D'si pencerenin (aile A: 2006-01-01 → 2020-07-31; görülmüş: 2020-08-01 → 2026-09-30) tarihleri "
    "dışında kalan saatler günlük/4h seriye girmez (sayılır)",
    "eşzamanlı açık işlem (bilgi): gold_lab.concurrency, hücrenin bütün hüküm serisi işlemleri",
    "sentetik ayar (calib): rastgele yürüyüş; Dukascopy benzeri saatlik seri (Pazar 18:00 – Cuma 17:00 NY, her gün 17–18 kapalı) "
    "2006-01 → 2020-07 ve PAXG benzeri 7/24 saatlik seri 2020-08-28 → 2026-09-30 (1d = UTC günü); aynı 24 hücre ve hüküm "
    "plasebo hücreleri; dünya k'nın tohumu CALIB_SEED + k; gerçek veriye dokunmaz",
)

_CFG0 = L.LabConfig()
GOLD_V2_REGISTRY: dict[str, Any] = {
    "version": GOLD2_VERSION,
    "doc": GOLD2_DOC,
    "question_tr": ["Aile A: günlük ve 4 saatlik trend takibi kuralları bu depoda hiç koşulmamış 2006–2020 Dukascopy verisinde "
                    "maliyet sonrası aynı maruziyetteki rastgele girişten daha iyi mi",
                    "Aile B: PAXG'deki hafta sonu hareketi ana piyasa Pazar akşamı açıldığında geri dönüyor mu"],
    "variants": VARIANTS,
    "cells": [f"{n} {c}" for n in VARIANTS for c in CELLS],
    "main_cells": [f"{n} {c}" for n, c in MAIN_CELLS],
    "cell_tr": "LONG = yalnız-long sistem; İKİ YÖN = LONG ve SHORT işlemlerinin birleşimi tek grup; SHORT tek başına bilgi",
    "short_tr": "SHORT her varyantta LONG'un simetriğidir (kanal: lo ↔ hi; işaret: > ↔ <; stop: − ↔ +; aile B: z > 0 → SHORT)",
    "dukascopy": {
        "symbol": DUKA_SYMBOL, "hour_file": DUKA_H_FILE, "day_file": DUKA_D_FILE,
        "layout_tr": "<kök>/XAUUSD/<YYYY>/<MM0>/BID_candles_hour_1.bi5 (MM0 = 0 tabanlı ay); <kök>/XAUUSD/<YYYY>/BID_candles_day_1.bi5",
        "record_tr": "LZMA alone; 24 baytlık big-endian: int32 saniye (saatlik: ay başından; günlük: yıl başından), int32 açılış, "
                     "kapanış, düşük, yüksek (÷1000 USD), float32 hacim",
        "checks_tr": "saatlik: [0, gün × 86400), kesin artan, t % 3600 == 0; günlük: [0, yıl günü × 86400), kesin artan, t % 86400 "
                     "== 0; tutmazsa GoldDataError",
        "clean_tr": "yalnız hacim 0 VE düz → atılır; hacmi 0 hareketli ve hacmi > 0 düz tutulur ve sayılır; bi5_sanity geçmeyen atılır",
        "mirror_tr": "koşu yalnız ayna işi bitince ('" + MIRROR_DONE_LINE + "', süreç yok, .part yok); manifest + bi5 sha256 baş/son",
        "timestamp_check": {"week_gap_h": WEEK_GAP_H, "summer": list(HW_SUMMER), "winter": list(HW_WINTER),
                            "min_share": HW_MIN_SHARE, "tz": TZ_NY,
                            "tr": "her dönemde haftaların ≥ %90'ı beklenen kümede değilse GoldDataError → aile A 'yapılamadı'"},
        "trading_day": {"tz": TZ_NY, "shift_h": DAY_SHIFT_H, "tr": "D(t) = (NY yerel + 7 saat) tarihi; Cmt/Paz D → günlük/4h dışı"},
        "bin_h": BIN_H, "bins_tr": "kutu başları 17:00, 21:00, 01:00, 05:00, 09:00, 13:00 NY; yalnız verisi olan kutular",
        "coverage": {"month_share": COV_MONTH_SHARE, "month_bars": COV_MONTH_BARS, "hours_per_day": COV_HOURS_PER_DAY,
                     "day_share": COV_DAY_SHARE, "day_min_bars": COV_DAY_MIN_BARS,
                     "tr": "keşif ve doğrulamanın HER BİRİNDE iki şart; tutmazsa aile A 'yapılamadı'"},
        "gap_h": GAP_DUKA_H, "short_session_bars": SHORT_SESSION_BARS, "weekend_flag_share": WEEKEND_FLAG_SHARE,
        "day_file_tr": "günlük dosya hükümde kullanılmaz; ay sonu kapanış farkı medyanı bilgi",
        "series_end": "2020-07-31", "series_end_tr": "hüküm serisi 2020-07-31 23:59 UTC'de biter; sonrası gereken işlem KESİLDİ",
    },
    "binance": {"source": "data.binance.vision (gold_lab yükleyicileri ve önbelleği)", "spot": PAXG, "tfs": ["1h", "4h", "1d"],
                "gap_h": GAP_BINANCE_H, "quality_tr": "eksik bar oranı ve 24 saatten uzun boşluklar raporlanır"},
    "cost": {"fee_pct": _CFG0.fee_pct, "slippage_bps": _CFG0.slippage_bps, "round_trip_pct": round(2 * _CFG0.cost_per_side * 100, 4),
             "funding_proxy": {"rate_8h": FUND_RATE_8H, "settle_utc": [0, 8, 16],
                               "formula_tr": "R_fon = R − s × 0,0001 × n_8s × giriş / risk; s = +1 LONG, −1 SHORT"},
             "monthly_reentry_tr": "aylık tutuşlu varyantlarda her ay ayrı işlem; maliyet her ay yeniden ödenir",
             "venue_tr": "vadeli satırlarda gerçek fonlama (futures_lab.funding_carry); kesintili fonlama → net R yok"},
    "trade_model": {"sim": "signal_lab.simulate_rule (değişmeden)", "min_risk_atr": _CFG0.min_risk_atr, "max_risk_atr": RISK_MAX_ATR,
                    "chase": None, "risk_atr_tr": "aile A: kendi diliminin ATR14'ü; aile B: ATR_d",
                    "entry_tr": "karar bar kapanışında; giriş sonraki barın açılışında; ilk stop bar içinde (kötümser)",
                    "overlap_tr": "aynı varyant ve yönde üst üste binen işlemlere izin var"},
    "common": {"warmup": WARMUP, "month_warmup_days": MONTH_WARMUP_DAYS, "month_end_weekdays": MONTH_END_WEEKDAYS,
               "month_safety_bars": MONTH_SAFETY_BARS, "atr_tr": "signal_lab.indicators ATR14 (Wilder, alpha 1/14), segment içinde",
               "channel_tr": "hiN[i] = high[i−N..i−1] en büyüğü, loN[i] = low[i−N..i−1] en küçüğü (bar i HARİÇ)",
               "mom_tr": "mom28[i] = close[i] / close[i−28] − 1", "sma_tr": "SMA200(1d) = son 200 günlük kapanış ortalaması (gün dahil)",
               "weekend": {"F": "Cuma 17:00 America/New_York", "S": "Pazar 18:00 America/New_York", "holiday_adjust": False}},
    "placebo": {
        "matched": {"k": K_MATCH, "key_tr": 'crc32("<sembol>|<dilim>|<ad>|<yön>|<ts[i_τ]>|<k>") mod |C(P_τ)|',
                    "tr": "gerçek işlem başına 5 eş; aynı yön, aynı dönem, hold H_τ; stop close ∓ kat × ATR; giriş şartı ve süzgeç YOK"},
        "monthly": {"long_tr": 'u = crc32("<sembol>|<dilim>|<ad>|LONG|<ts[i]>") / 2^32 < p; p = LONG sinyal / aday',
                    "both_tr": 'işaret: crc32("<sembol>|<dilim>|<ad>|İKİ|<ts[i]>") / 2^32 < 0,5 → LONG'},
        "weekend": {"long_tr": 'u = crc32("PAXGUSDT|1h|<ad>|LONG|<ts[i]>") / 2^32 < p; p = LONG sinyal / uygun hafta',
                    "both_tr": 'işaret: crc32("PAXGUSDT|1h|<ad>|İKİ|<ts[i]>") / 2^32 < 0,5 → LONG'},
        "info_tr": "A gün/4h kural çıkışlı rastgele (gold_v1 tarzı) ve B her saat plasebosu yalnız bilgi",
        "skips_tr": "maliyet, fonlama vekili, segment/BOŞLUK, EKSİK_BAR, HACİMSİZ, KESİLDİ plaseboya da uygulanır",
    },
    "periods": {"A": {"is": WINDOWS["A"]["is"], "oos": WINDOWS["A"]["oos"]}, "B": {"is": WINDOWS["B"]["is"], "oos": WINDOWS["B"]["oos"]},
                "tr": "sabit tarihli dönemler (karar anına göre); laboratuvarın 2/3 kesimi KULLANILMAZ"},
    "stats": {"min_is": _CFG0.min_is, "min_oos": _CFG0.min_oos, "bootstrap_iters": _CFG0.bootstrap_iters,
              "strict_seed": L.STRICT_SEED, "strict_min_days": L.STRICT_MIN_DAYS, "strict_rule_tr": L.STRICT_RULE_TR,
              "verdicts": [L.V_STRONG, L.V_WEAK, L.V_LOSS, L.V_NONE, L.V_THIN],
              "month_ci_tr": "ortalama R için ay kümeli %95 aralığı bilgi sütunu (hükme girmez)",
              "verdict_r_tr": "hüküm fonlamasız R ile; R_fon özeti yanında"},
    "monthly": {"risk_pct": G.RISK_PCT, "target_pct_month": G.TARGET_PCT_MONTH, "seed": G.MONTH_SEED, "key": "r_fon",
                "months": {"A": WINDOWS["A"]["months"], "B": WINDOWS["B"]["months"]},
                "open_end": {"A": WINDOWS["A"]["open_end"], "B": WINDOWS["B"]["open_end"]},
                "meets_target_tr": "sıkı hüküm GÜÇLÜ ADAY VE doğrulama ortalama aylık net (R_fon, yuvarlanmamış) ≥ +%1, %0,5 riskte",
                "note_tr": "bileşik getiri yok; işlemsiz ay 0; aylık varyantlarda karar ayına yazma işlemi bir ay önce raporlar",
                "half_tr": "ortalamanın %1'e tam yetmesi, gerçek ortalamanın %1'in altında olma olasılığının kabaca %50 olduğu anlamına gelir"},
    "higher_risk": {"profile_cap": PROFILE_CAP_RISK, "open_risk_caps": [OPEN_RISK_CAP, OPEN_RISK_LEARNING], "dd_flag_pct": DD_FLAG_PCT,
                    "leverage_flags": [LEV_PAPER, LEV_MAX],
                    "text_tr": "ortalama tahmin X% riskte +%1/ay'a karşılık gelir; %95 aralık [a × X / 0,5, b × X / 0,5]; ulaşılacağı "
                               "garanti değildir",
                    "only_tr": "yalnız sıkı hükmü GÜÇLÜ ADAY olan hücreler; diğerlerinde 'kenar yok'",
                    "curse_tr": "X, hükmü veren doğrulama ortalamasının kendisinden hesaplanır (kazanan laneti); iyimserdir"},
    "c_sizing": {"target_vol": C_TARGET_VOL, "max_w": C_MAX_W, "total_cap": C_TOTAL_CAP, "sig_bars": C_SIG_BARS,
                 "ann": {"dukascopy": ANN_DUKA, "binance": ANN_BINANCE},
                 "tr": "bilgi, ayrı hipotez DEĞİL; yalnız sıkı GÜÇLÜ ADAY hücrelerde basılır; g = R_fon × risk / giriş"},
    "seen_venue": {"seen_tr": "A görülmüş: PAXG spot ve Dukascopy 2020-08 → 2026-09, dönem bölünmez; hüküm DEĞİL",
                   "venue_tr": "A ve B vadeli XAUUSDT / PAXGUSDT; gerçek fonlama; hüküm DEĞİL; uygulama mekânı denetimi",
                   "note": NOTE_VENUE, "min_n": G.VENUE_MIN_N},
    "multiple": {"cells": 24, "main": 3, "cumulative_cells": 56, "gold_v1_sha": G.GOLD_REGISTRY_SHA,
                 "chance_tr": "rastgele yürüyüşte hücrelerin %0–0,5'i GÜÇLÜ ADAY: 24 hücrede 0–0,12; 56 hücrede 0–0,28 beklenir",
                 "calib": {"seed": CALIB_SEED, "worlds": CALIB_WORLDS}},
    "recommendation_tr": ["öneri yalnız bir ANA hücre (i) sıkı GÜÇLÜ ADAY, (ii) fonlama vekilli doğrulama ort. ≥ +%1/ay (%0,5 risk), "
                          "(iii) kendi ailesinin hüküm plasebo hücrelerinde aday oranı 0, (iv) 'mekânda tutmadı' notu yok iken",
                          "öneri yalnız PAPER ve yalnız-kayıt; sahip onayı olmadan hiçbir şey açılmaz",
                          "ikincil hücre hedefi karşılarsa yalnız 'yeni bir ön kayıt (gold_v3) gerekir'",
                          "ANA hücre sıkı GÜÇLÜ ADAY ama hedefin altındaysa 'kenar var, hedefin altında'",
                          "hiçbiri: " + NO_TARGET_TR],
    "windows": WINDOWS,
    "trials": {"version": GOLD2_VERSION, "variants": 12, "cells": 24, "main": 3, "cumulative_gold_cells": 56,
               "tr": "gold_v2: 12 varyant (24 hüküm hücresi; 3'ü ANA). Kümülatif altın: 56 hücre (gold_v1 32 + gold_v2 24)"},
    "readings_tr": list(READINGS_TR),
}
GOLD_V2_REGISTRY_SHA = hashlib.sha256(json.dumps(GOLD_V2_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class GoldDataError(G.GoldDataError):
    """Gerekli seri yok, okunamadı ya da ayna koşu sırasında değişti: rapor eksik veriyle SESSİZCE üretilmez."""


# ---------------------------------------------------------------------------- zaman yardımcıları
def day_ms(s: str) -> int:
    return G.day_ms(s)


def win_ms(a: str, b: str) -> tuple[int, int]:
    """[a günü 00:00 UTC, b günü + 1 gün 00:00 UTC)."""
    return G.window_ms(a, b)


def mi_of(y: int, m: int) -> int:
    return int(y) * 12 + int(m) - 1


def mi_str(mi: int) -> str:
    return f"{int(mi) // 12:04d}-{int(mi) % 12 + 1:02d}"


def mi_parse(s: str) -> int:
    y, m = str(s).split("-")[:2]
    return mi_of(int(y), int(m))


def month_start_ms(mi: int) -> int:
    return day_ms(mi_str(mi) + "-01")


def days_in_month(mi: int) -> int:
    return calendar.monthrange(int(mi) // 12, int(mi) % 12 + 1)[1]


def dnum_of(d: date) -> int:
    return (d - date(1970, 1, 1)).days


def date_of(dnum: int) -> date:
    return date(1970, 1, 1) + timedelta(days=int(dnum))


def weekday(dnum: Any) -> Any:
    """Pazartesi = 0 (1970-01-01 Perşembe)."""
    return (np.asarray(dnum, dtype=np.int64) + 3) % 7


def weekdays_in_month(mi: int) -> int:
    y, m = int(mi) // 12, int(mi) % 12 + 1
    return sum(1 for d in range(1, days_in_month(mi) + 1) if date(y, m, d).weekday() < 5)


def third_last_weekday(mi: int) -> int:
    """Ayın sondan üçüncü Pzt–Cum takvim gününün dnum'u."""
    y, m = int(mi) // 12, int(mi) % 12 + 1
    d, k = date(y, m, days_in_month(mi)), 0
    while True:
        if d.weekday() < 5:
            k += 1
            if k == MONTH_END_WEEKDAYS:
                return dnum_of(d)
        d -= timedelta(days=1)


def mi_from_dnum(dnum: Any) -> np.ndarray:
    m = np.asarray(dnum, dtype=np.int64).astype("datetime64[D]").astype("datetime64[M]").astype(np.int64)
    return m + 1970 * 12


def ny_parts(ts_ms: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(D dnum, işlem günü içindeki saat h_td ∈ [0, 24) (0 = 17:00 NY), UTC farkı saat (−4 / −5))."""
    ts = np.asarray(ts_ms, dtype=np.int64)
    if not ts.size:
        z = np.zeros(0, dtype=np.int64)
        return z, z.astype(float), z
    utc = pd.to_datetime(ts, unit="ms", utc=True)
    loc_ms = utc.tz_convert(TZ_NY).tz_localize(None).as_unit("ms").asi8
    off = (loc_ms - ts) // HOUR_MS
    sh = loc_ms + DAY_SHIFT_H * HOUR_MS
    dn = sh // DAY_MS
    return dn.astype(np.int64), (sh - dn * DAY_MS) / HOUR_MS, off.astype(np.int64)


def ny_to_utc_ms(dnum: Any, hour: int) -> np.ndarray:
    """New York yerel takvim günü dnum, saat `hour`:00 → UTC ms (zoneinfo; o günün kendi yaz/kış farkıyla)."""
    d = np.atleast_1d(np.asarray(dnum, dtype=np.int64))
    if not d.size:
        return np.zeros(0, dtype=np.int64)
    loc = pd.to_datetime(d * DAY_MS + int(hour) * HOUR_MS, unit="ms")
    return loc.tz_localize(TZ_NY).tz_convert("UTC").as_unit("ms").asi8.astype(np.int64)


def utc_hour(ts_ms: Any) -> np.ndarray:
    return (np.asarray(ts_ms, dtype=np.int64) // HOUR_MS) % 24


def easter(y: int) -> date:
    """Batı (Gregoryen) Paskalyası — tatil sayımı (bilgi) için."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mo = (h + l_ - 7 * m + 114) // 31
    da = (h + l_ - 7 * m + 114) % 31 + 1
    return date(y, mo, da)


def holiday_friday(d: date) -> str:
    if d == easter(d.year) - timedelta(days=2):
        return "Kutsal Cuma"
    if (d.month, d.day) == (12, 25):
        return "Noel"
    if (d.month, d.day) == (1, 1):
        return "Yılbaşı"
    return ""


def _h(*parts: Any) -> float:
    return L._h(*parts)


def _crc(*parts: Any) -> int:
    return zlib.crc32("|".join(str(x) for x in parts).encode())


def _iso(ms: Any) -> str | None:
    return G._iso(ms)


# ---------------------------------------------------------------------------- Dukascopy: dosyalar, çözme, temizlik
def duka_hour_path(root: Path | str, mi: int) -> Path:
    return Path(root) / DUKA_SYMBOL / f"{int(mi) // 12:04d}" / f"{int(mi) % 12:02d}" / DUKA_H_FILE


def duka_day_path(root: Path | str, year: int) -> Path:
    return Path(root) / DUKA_SYMBOL / f"{int(year):04d}" / DUKA_D_FILE


def decode_span(data: bytes, start_ms: int, span_s: int, unit_s: int) -> pd.DataFrame:
    """bi5 (LZMA alone) → barlar. Saniye [0, span_s), kesin artan ve unit_s'nin katı olmalı (değilse ValueError). LZMA
    açılamıyorsa lzma.LZMAError/EOFError yükselir (çağıran 'çözülemedi' sayar)."""
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    if not data:
        return pd.DataFrame(columns=cols)
    raw = lzma.decompress(data, format=lzma.FORMAT_ALONE)
    if len(raw) % G.DUKA_DTYPE.itemsize:
        raise ValueError(f"bi5 uzunluğu {len(raw)} 24'ün katı değil")
    a = np.frombuffer(raw, dtype=G.DUKA_DTYPE)
    t = a["t"].astype(np.int64)
    if len(t) and (t.min() < 0 or t.max() >= span_s or np.any(np.diff(t) <= 0) or np.any(t % unit_s)):
        raise ValueError(f"bi5 kayıt saniyeleri [0, {span_s}) dışında, kesin artan değil ya da {unit_s}'in katı değil "
                         f"({t.min()}..{t.max()})")
    return pd.DataFrame({"timestamp": int(start_ms) + t * 1000, "open": a["o"].astype(float) / G.DUKA_DIV,
                         "high": a["h"].astype(float) / G.DUKA_DIV, "low": a["l"].astype(float) / G.DUKA_DIV,
                         "close": a["c"].astype(float) / G.DUKA_DIV, "volume": a["v"].astype(float)})


def decode_hour_file(data: bytes, mi: int) -> pd.DataFrame:
    return decode_span(data, month_start_ms(mi), days_in_month(mi) * 86_400, 3600)


def decode_day_file(data: bytes, year: int) -> pd.DataFrame:
    return decode_span(data, day_ms(f"{int(year):04d}-01-01"), (366 if calendar.isleap(int(year)) else 365) * 86_400, 86_400)


def clean_bars(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Kapalı piyasa (hacim 0 VE düz) atılır; bi5_sanity'yi geçmeyen atılır; hacmi 0 hareketli / hacmi > 0 düz tutulur ve
    sayılır. Döner (tutulan, satır başına bayraklar)."""
    if not len(df):
        z = np.zeros(0, dtype=bool)
        return df, {"flat_closed": z, "invalid": z, "vol0_moving": z, "flat_vol_pos": z, "keep": z}
    o, h, lo, c, v = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    flat = (o == h) & (h == lo) & (lo == c)
    closed = flat & (v == 0)
    ok = G.bi5_sanity(df)
    invalid = ~closed & ~ok
    keep = ~closed & ok
    fl = {"flat_closed": closed, "invalid": invalid, "vol0_moving": keep & (v == 0), "flat_vol_pos": keep & flat & (v > 0),
          "keep": keep}
    return df[keep].reset_index(drop=True), fl


def read_manifest(root: Path | str) -> dict[str, Any]:
    """manifest.jsonl → {"hour": {YYYY-MM: [satırlar]}, "day": {YYYY: [...]}, "max_ts", "status_counts", "exists"}."""
    p = Path(root) / "manifest.jsonl"
    out: dict[str, Any] = {"hour": {}, "day": {}, "max_ts": None, "status_counts": {}, "exists": p.exists(), "lines": 0}
    if not p.exists():
        return out
    st: Counter = Counter()
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("kind") not in ("hour", "day"):
            continue
        out["lines"] += 1
        out[r["kind"]].setdefault(str(r.get("key")), []).append(r)
        st[f"{r['kind']}:{r.get('status')}"] += 1
        ts = r.get("ts")
        if isinstance(ts, str) and (out["max_ts"] is None or ts > out["max_ts"]):
            out["max_ts"] = ts
    out["status_counts"] = dict(st)
    return out


def manifest_ok(man: Mapping[str, Any], kind: str, key: str) -> bool:
    return any(r.get("status") == 200 and G._positive(r.get("bytes")) for r in (man.get(kind) or {}).get(key) or [])


def load_duka_hourly(root: Path | str, mi0: int, mi1: int, start_ms: int, end_ms: int, *,
                     man: Mapping[str, Any] | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Aylık saatlik dosyaları [mi0, mi1] okur, [start_ms, end_ms) penceresine kırpar ve temizler. Döner (tutulan saatlik
    barlar, bilgi: ay başına durum, yıl başına sayımlar, okunan dosyalar). Kayıt denetimi tutmazsa GoldDataError."""
    man = man if man is not None else read_manifest(root)
    parts, months, files = [], {}, []
    years: dict[str, Counter] = {}
    for mi in range(int(mi0), int(mi1) + 1):
        key = mi_str(mi)
        p = duka_hour_path(root, mi)
        rec = {"manifest_ok": manifest_ok(man, "hour", key), "file": p.is_file() and p.stat().st_size > 0, "decoded": False,
               "rows": 0, "kept": 0, "usable": False, "need": int(math.ceil(COV_MONTH_BARS * weekdays_in_month(mi) * COV_HOURS_PER_DAY))}
        months[key] = rec
        if not rec["file"]:
            continue
        data = p.read_bytes()
        files.append(str(p))
        try:
            df = decode_hour_file(data, mi)
        except (lzma.LZMAError, EOFError) as exc:
            rec["error"] = f"çözülemedi: {type(exc).__name__}"
            continue
        except ValueError as exc:
            raise GoldDataError(f"Dukascopy saatlik dosyası kayıt denetimini geçmedi: {p}: {exc}") from exc
        rec["decoded"] = True
        df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] < end_ms)].reset_index(drop=True)
        kept, fl = clean_bars(df)
        yrs = (df["timestamp"].to_numpy(dtype=np.int64) // DAY_MS).astype("datetime64[D]").astype("datetime64[Y]").astype(int) + 1970
        for y in np.unique(yrs):
            m = yrs == y
            cnt = years.setdefault(str(int(y)), Counter())
            cnt["rows"] += int(m.sum())
            for k in ("flat_closed", "vol0_moving", "flat_vol_pos", "invalid"):
                cnt[k] += int(fl[k][m].sum())
        rec["rows"], rec["kept"] = int(len(df)), int(len(kept))
        rec["usable"] = bool(rec["manifest_ok"] and rec["kept"] >= rec["need"])
        if len(kept):
            parts.append(kept)
    if parts:
        h = pd.concat(parts).sort_values("timestamp", kind="mergesort").drop_duplicates("timestamp").reset_index(drop=True)
    else:
        h = pd.DataFrame({k: np.zeros(0) for k in ("timestamp", "open", "high", "low", "close", "volume")})
        h["timestamp"] = h["timestamp"].astype(np.int64)
    return h, {"months": months, "years": {k: dict(v) for k, v in sorted(years.items())}, "files": files,
               "usable_months": sorted(k for k, r in months.items() if r["usable"])}


def day_file_info(root: Path | str, years: Iterable[int], month_end_close: Mapping[int, float]) -> dict[str, Any]:
    """Bilgi: günlük dosyanın ay sonu kapanışı − saatlik türetimin ay sonu kapanışı (fark ve mutlak fark medyanı)."""
    diffs, read, files = [], 0, []
    for y in years:
        p = duka_day_path(root, y)
        if not (p.is_file() and p.stat().st_size > 0):
            continue
        files.append(str(p))
        try:
            df = decode_day_file(p.read_bytes(), y)
        except (lzma.LZMAError, EOFError):
            continue
        except ValueError as exc:
            raise GoldDataError(f"Dukascopy günlük dosyası kayıt denetimini geçmedi: {p}: {exc}") from exc
        read += 1
        df, _ = clean_bars(df)
        if not len(df):
            continue
        mis = mi_from_dnum(df["timestamp"].to_numpy(dtype=np.int64) // DAY_MS)
        cl = df["close"].to_numpy(dtype=float)
        for mi in np.unique(mis):
            if int(mi) in month_end_close:
                diffs.append(float(cl[mis == mi][-1]) - float(month_end_close[int(mi)]))
    d = np.array(diffs, dtype=float)
    return {"years_read": read, "files": files, "months_compared": int(len(d)),
            "median_diff": round(float(np.median(d)), 4) if len(d) else None,
            "median_abs_diff": round(float(np.median(np.abs(d))), 4) if len(d) else None}


# ---------------------------------------------------------------------------- ayna hazır mı, anlık görüntü
def _sha(p: Path | str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mirror_ready(root: Path | str, log_path: Path | str | None) -> dict[str, Any]:
    """Ayna işi bitti mi: .part yok, günlükte bitiş satırı var, komut satırında ayna kökünü içeren süreç yok. Değilse
    GoldDataError (koşu başlamaz)."""
    root = Path(root)
    why = []
    if not root.is_dir():
        why.append(f"ayna klasörü yok: {root}")
    parts = [str(p) for p in root.rglob("*.part")] if root.is_dir() else []
    if parts:
        why.append(f"aynada yarım dosya var ({len(parts)}; ör. {parts[0]})")
    if log_path is None:
        why.append("ayna günlüğü verilmedi (--duka-log): bitiş doğrulanamaz")
    else:
        lp = Path(log_path)
        if not lp.is_file() or MIRROR_DONE_LINE not in lp.read_text(encoding="utf-8", errors="replace"):
            why.append(f"ayna günlüğünde '{MIRROR_DONE_LINE}' yok: {lp}")
    procs = []
    proc = Path("/proc")
    if proc.is_dir():
        keys = {str(root), str(root.resolve())} if root.exists() else {str(root)}
        me = os.getpid()
        for d in proc.iterdir():
            if not d.name.isdigit() or int(d.name) == me:
                continue
            try:
                cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if any(k in cmd for k in keys) and "gold_lab_v2" not in cmd and "pytest" not in cmd:
                procs.append(int(d.name))
    if procs:
        why.append(f"ayna kökünü kullanan süreç çalışıyor: {procs[:5]}")
    if why:
        raise GoldDataError("Dukascopy aynası hazır değil: " + "; ".join(why))
    return {"ready": True, "proc_checked": proc.is_dir(), "log": str(log_path)}


def mirror_snapshot(root: Path | str, files: Iterable[str]) -> dict[str, Any]:
    root = Path(root)
    man = root / "manifest.jsonl"
    snap = {"manifest_sha256": _sha(man) if man.exists() else None,
            "files": {str(Path(f).relative_to(root)) if str(f).startswith(str(root)) else str(f): _sha(f) for f in sorted(set(files))},
            "cut_ts": read_manifest(root).get("max_ts")}
    snap["files_sha256"] = hashlib.sha256(json.dumps(snap["files"], sort_keys=True).encode()).hexdigest()
    snap["n_files"] = len(snap["files"])
    return snap


def check_snapshot(root: Path | str, snap: Mapping[str, Any]) -> None:
    now = mirror_snapshot(root, [str(Path(root) / k) if not Path(k).is_absolute() else k for k in snap["files"]])
    if now["manifest_sha256"] != snap["manifest_sha256"] or now["files"] != snap["files"]:
        changed = [k for k in snap["files"] if now["files"].get(k) != snap["files"][k]]
        raise GoldDataError(f"Dukascopy aynası koşu sırasında değişti (manifest {'değişti' if now['manifest_sha256'] != snap['manifest_sha256'] else 'aynı'}; "
                            f"değişen dosya {len(changed)}): koşu GEÇERSİZ")


# ---------------------------------------------------------------------------- seriler: segment, işlem günü, 4h
def segments(ts: Any, gap_ms: int) -> np.ndarray:
    ts = np.asarray(ts, dtype=np.int64)
    if not ts.size:
        return np.zeros(0, dtype=np.int64)
    return np.r_[0, np.cumsum(np.diff(ts) > int(gap_ms))].astype(np.int64)


def _seg_arrays(seg: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[tuple[int, int]]]:
    n = len(seg)
    if not n:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), []
    st = np.flatnonzero(np.r_[True, seg[1:] != seg[:-1]])
    en = np.r_[st[1:], n] - 1
    lens = en - st + 1
    pos = np.arange(n) - np.repeat(st, lens)
    return pos.astype(np.int64), np.repeat(en, lens).astype(np.int64), list(zip(st.tolist(), en.tolist()))


def make_frame(ts: Any, cms: Any, o: Any, h: Any, lo: Any, c: Any, v: Any, seg: Any, dnum: Any, *, symbol: str, tf: str,
               source: str) -> dict[str, Any]:
    """Seri sözlüğü: dizi alanları + segment içi sıra, segment sonu, ay. `source` 'dukascopy' / 'binance'."""
    F: dict[str, Any] = {"ts": np.asarray(ts, dtype=np.int64), "cms": np.asarray(cms, dtype=np.int64),
                         "open": np.asarray(o, dtype=float), "high": np.asarray(h, dtype=float), "low": np.asarray(lo, dtype=float),
                         "close": np.asarray(c, dtype=float), "volume": np.asarray(v, dtype=float), "seg": np.asarray(seg, dtype=np.int64),
                         "dnum": np.asarray(dnum, dtype=np.int64)}
    F["segpos"], F["seg_end"], F["segs"] = _seg_arrays(F["seg"])
    F["mi"] = mi_from_dnum(F["dnum"]) if len(F["dnum"]) else np.zeros(0, dtype=np.int64)
    F["n"] = len(F["ts"])
    F["symbol"], F["tf"], F["source"] = symbol, tf, source
    F["sclose"] = F["close"]
    return F


def duka_frames(h: pd.DataFrame, *, symbol: str = DUKA_SYMBOL, d_range: tuple[str, str] | None = None) -> tuple[dict, dict, dict[str, Any]]:
    """Tutulan saatlik barlar → (günlük, 4h, bilgi). İşlem günü D = (NY + 7 saat) tarihi; Cmt/Paz D saatleri ve (d_range verilirse)
    D'si pencerenin tarihleri dışında kalan saatler günlük/4h seriye girmez (sayılır)."""
    ts = h["timestamp"].to_numpy(dtype=np.int64)
    o, hi, lo, c, v = (h[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    seg = segments(ts, GAP_DUKA_H * HOUR_MS)
    dn, htd, _ = ny_parts(ts)
    wd = weekday(dn)
    inside = np.ones(len(ts), dtype=bool)
    if d_range is not None:
        inside = (dn >= dnum_of(date.fromisoformat(d_range[0]))) & (dn <= dnum_of(date.fromisoformat(d_range[1])))
    wk = (wd < 5) & inside
    info: dict[str, Any] = {"hours": int(len(ts)), "weekend_hours": int((~(wd < 5)).sum()), "outside_window_hours": int((~inside).sum()),
                            "segments": int(seg.max() + 1) if len(seg) else 0}
    isow = (dn + 3) // 7                                                  # ISO hafta (Pazartesi başlangıçlı) numarası
    weeks_all = np.unique(isow) if len(isow) else np.zeros(0)
    weeks_we = np.unique(isow[wd >= 5]) if (wd >= 5).any() else np.zeros(0)
    br = np.flatnonzero(np.diff(ts) > GAP_DUKA_H * HOUR_MS) if len(ts) > 1 else np.zeros(0, dtype=np.int64)
    info["segment_breaks"] = [{"from": _iso(ts[k]), "to": _iso(ts[k + 1]), "hours": round(float(ts[k + 1] - ts[k]) / HOUR_MS, 1)}
                              for k in br[:100]]
    info["weekend_weeks"] = int(len(weeks_we))
    info["weeks"] = int(len(weeks_all))
    info["weekend_flag"] = bool(len(weeks_all) and len(weeks_we) / len(weeks_all) > WEEKEND_FLAG_SHARE)
    # gün
    idx = np.flatnonzero(wk)
    dnw = dn[idx]
    if len(idx):
        st = np.flatnonzero(np.r_[True, dnw[1:] != dnw[:-1]])
        en = np.r_[st[1:], len(idx)] - 1
        ii, jj = idx[st], idx[en]
        dd = dnw[st]
        D = make_frame(ny_to_utc_ms(dd - 1, 17), ny_to_utc_ms(dd, 17), o[ii], np.maximum.reduceat(hi[idx], st),
                       np.minimum.reduceat(lo[idx], st), c[jj], np.add.reduceat(v[idx], st), seg[ii], dd,
                       symbol=symbol, tf="1d", source="dukascopy")
        D["hours"] = (en - st + 1).astype(np.int64)
        if np.any(seg[ii] != seg[jj]):
            raise GoldDataError("bir işlem günü iki segmente bölündü (beklenmez: segment eşiği 120 saat)")
        # 4h
        b = np.floor(htd[idx] / BIN_H).astype(np.int64)
        key = dnw * 6 + b
        s4 = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
        e4 = np.r_[s4[1:], len(idx)] - 1
        i4, j4 = idx[s4], idx[e4]
        start4 = ts[i4] - np.round((htd[i4] - BIN_H * b[s4]) * HOUR_MS).astype(np.int64)
        H4 = make_frame(start4, start4 + BIN_H * HOUR_MS, o[i4], np.maximum.reduceat(hi[idx], s4), np.minimum.reduceat(lo[idx], s4),
                        c[j4], np.add.reduceat(v[idx], s4), seg[i4], dnw[s4], symbol=symbol, tf="4h", source="dukascopy")
    else:
        D = make_frame(*([np.zeros(0)] * 9), symbol=symbol, tf="1d", source="dukascopy")
        D["hours"] = np.zeros(0, dtype=np.int64)
        H4 = make_frame(*([np.zeros(0)] * 9), symbol=symbol, tf="4h", source="dukascopy")
    short = [str(date_of(d)) for d, k in zip(D["dnum"], D["hours"]) if k < SHORT_SESSION_BARS]
    info.update({"days": int(D["n"]), "bars_4h": int(H4["n"]), "short_session_days": len(short), "short_session_list": short[:200]})
    return D, H4, info


def binance_frame(df: pd.DataFrame, tf: str, *, symbol: str, gap_h: int = GAP_BINANCE_H) -> dict[str, Any]:
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    step = tf_ms(tf)
    return make_frame(ts, ts + step, *(df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")),
                      segments(ts, gap_h * HOUR_MS), ts // DAY_MS, symbol=symbol, tf=tf, source="binance")


def add_indicators(F: dict[str, Any], *, ann: int | None = None) -> dict[str, Any]:
    """Segment içinde: ATR14 (signal_lab.indicators), hiN/loN (bar hariç), mom28, SMA200 ve (ann verilirse) C'nin σ_yıllık'ı."""
    n = F["n"]
    keys = ("atr", "hi10", "lo10", "hi20", "lo20", "hi55", "lo55", "mom28", "sma200", "sig")
    for k in keys:
        F[k] = np.full(n, np.nan)
    for s0, s1 in F["segs"]:
        sl = slice(s0, s1 + 1)
        df = pd.DataFrame({"high": F["high"][sl], "low": F["low"][sl], "close": F["close"][sl], "volume": F["volume"][sl]})
        F["atr"][sl] = L.indicators(df)["atr"]
        hs, ls, cs = df["high"], df["low"], df["close"]
        for N in (10, 20, 55):
            F[f"hi{N}"][sl] = hs.rolling(N).max().shift(1).to_numpy()
            F[f"lo{N}"][sl] = ls.rolling(N).min().shift(1).to_numpy()
        F["mom28"][sl] = (cs / cs.shift(28) - 1).to_numpy()
        F["sma200"][sl] = cs.rolling(200).mean().to_numpy()
        if ann:
            with np.errstate(divide="ignore", invalid="ignore"):
                lr = np.log(cs).diff()
            F["sig"][sl] = (lr.rolling(C_SIG_BARS - 1).std(ddof=1) * math.sqrt(ann)).to_numpy()
    F["ann"] = ann
    return F


# ---------------------------------------------------------------------------- Dukascopy veri denetimleri (sonuç hesaplamaz)
def week_open_check(ts: Any, periods: Mapping[str, tuple[int, int]]) -> dict[str, Any]:
    """Haftanın ilk saati h_w (önceki tutulan bar > 40 saat eski) beklenen kümede mi: yaz {21, 22}, kış {22, 23} UTC. Dönem
    başına pay ≥ %90 değilse `pass` False."""
    ts = np.asarray(ts, dtype=np.int64)
    out: dict[str, Any] = {"periods": {}, "summer": {}, "winter": {}, "friday_last": {"summer": {}, "winter": {}}}
    if len(ts) < 2:
        out["pass"] = False
        out["periods"] = {p: {"weeks": 0, "ok": 0, "share": None, "pass": False} for p in periods}
        return out
    st = np.flatnonzero(np.diff(ts) > WEEK_GAP_H * HOUR_MS) + 1
    hw = utc_hour(ts[st])
    _, _, off = ny_parts(ts[st])
    dst = off == -4
    ok = np.where(dst, np.isin(hw, HW_SUMMER), np.isin(hw, HW_WINTER))
    fl = utc_hour(ts[st - 1])
    _, _, offf = ny_parts(ts[st - 1])
    out["summer"] = {str(k): int(v) for k, v in sorted(Counter(hw[dst].tolist()).items())}
    out["winter"] = {str(k): int(v) for k, v in sorted(Counter(hw[~dst].tolist()).items())}
    out["friday_last"] = {"summer": {str(k): int(v) for k, v in sorted(Counter(fl[offf == -4].tolist()).items())},
                          "winter": {str(k): int(v) for k, v in sorted(Counter(fl[offf != -4].tolist()).items())}}
    allp = True
    for p, (a, b) in periods.items():
        m = (ts[st] >= a) & (ts[st] < b)
        n = int(m.sum())
        k = int(ok[m].sum())
        share = k / n if n else None
        good = bool(n and share >= HW_MIN_SHARE)
        out["periods"][p] = {"weeks": n, "ok": k, "share": None if share is None else round(share, 4), "pass": good}
        allp &= good
    out["pass"] = bool(allp)
    return out


def coverage_check(months: Mapping[str, Mapping[str, Any]], kept_ts: Any, periods: Mapping[str, tuple[str, str]]) -> dict[str, Any]:
    """Kapsama eşiği (dönem başına iki şart): ayların ≥ %95'i kullanılabilir VE Pzt–Cum günlerinin ≥ %95'inde ≥ 20 geçerli bar."""
    dn, _, _ = ny_parts(kept_ts)
    per_day = Counter(dn.tolist())
    out: dict[str, Any] = {"periods": {}}
    allp = True
    for p, (a, b) in periods.items():
        mis = list(range(mi_parse(a[:7]), mi_parse(b[:7]) + 1))
        bad_m = [mi_str(m) for m in mis if not (months.get(mi_str(m)) or {}).get("usable")]
        d0, d1 = dnum_of(date.fromisoformat(a)), dnum_of(date.fromisoformat(b))
        wdays = [d for d in range(d0, d1 + 1) if (d + 3) % 7 < 5]
        bad_d = [str(date_of(d)) for d in wdays if per_day.get(d, 0) < COV_DAY_MIN_BARS]
        ms = 1 - len(bad_m) / len(mis) if mis else 0.0
        ds = 1 - len(bad_d) / len(wdays) if wdays else 0.0
        good = bool(mis and wdays and ms >= COV_MONTH_SHARE and ds >= COV_DAY_SHARE)
        out["periods"][p] = {"months": len(mis), "months_usable": len(mis) - len(bad_m), "month_share": round(ms, 4),
                             "bad_months": bad_m, "weekdays": len(wdays), "days_ok": len(wdays) - len(bad_d),
                             "day_share": round(ds, 4), "bad_days": bad_d[:400], "n_bad_days": len(bad_d), "pass": good}
        allp &= good
    out["pass"] = bool(allp)
    return out


def partial_days(D: Mapping[str, Any], loaded_months: set[int]) -> list[str]:
    """KISMİ_GÜN: ayın ilk işlem günü (D'nin ayı) ve önceki takvim ayının saatlik dosyası okunmadı."""
    out = []
    mi = D["mi"]
    for k in range(D["n"]):
        if (k == 0 or mi[k] != mi[k - 1]) and int(mi[k]) - 1 not in loaded_months:
            out.append(str(date_of(D["dnum"][k])))
    return out


# ---------------------------------------------------------------------------- ay tablosu
def month_table(D: Mapping[str, Any], usable: set[int] | None = None) -> dict[int, dict[str, Any]]:
    """Ay (D'nin ayı) → ilk/son günlük bar, bar sayısı, ay sonu şartı (son bar ≥ sondan üçüncü Pzt–Cum), kullanılabilir mi."""
    out: dict[int, dict[str, Any]] = {}
    mi = D["mi"]
    n = D["n"]
    if not n:
        return out
    st = np.flatnonzero(np.r_[True, mi[1:] != mi[:-1]])
    en = np.r_[st[1:], n] - 1
    for a, b in zip(st.tolist(), en.tolist()):
        m = int(mi[a])
        end_ok = bool(D["dnum"][b] >= third_last_weekday(m))
        out[m] = {"first": a, "last": b, "n": b - a + 1, "end_ok": end_ok,
                  "usable": bool(end_ok and (usable is None or m in usable))}
    return out


# ---------------------------------------------------------------------------- sinyaller (aile A)
def _shift1(x: np.ndarray) -> np.ndarray:
    return np.r_[np.nan, x[:-1]] if len(x) else x


def d200_side(F: Mapping[str, Any], D: Mapping[str, Any], *, same_seg: bool) -> np.ndarray:
    """4h bar başına +1 / −1 / 0: kapanış anı ≤ karar anı olan son günlük barda close > / < SMA200(1d)."""
    n = F["n"]
    if not n or not D["n"]:
        return np.zeros(n)
    jd = np.searchsorted(D["cms"], F["cms"], side="right") - 1
    ok = jd >= 0
    j = np.maximum(jd, 0)
    if same_seg:
        ok &= D["seg"][j] == F["seg"]
    dc, ds = D["close"][j], D["sma200"][j]
    with np.errstate(invalid="ignore"):
        side = np.where(dc > ds, 1.0, np.where(dc < ds, -1.0, 0.0))
    return np.where(ok & np.isfinite(ds), side, 0.0)


def rule_signals(name: str, F: Mapping[str, Any], D: Mapping[str, Any] | None = None, *, same_seg: bool = True) -> list[tuple]:
    """Gün/4h varyantının olayları: [(gi, yön, stop, çıkış)] (karar gi barının kapanışında)."""
    v = VARIANTS[name]
    c, atr, sp = F["close"], F["atr"], F["segpos"]
    with np.errstate(invalid="ignore"):
        ok = (sp >= WARMUP) & np.isfinite(atr) & (atr > 0)
        if v["entry"] == "breakout":
            N = v["n"]
            hi, lo = F[f"hi{N}"], F[f"lo{N}"]
            c1, hi1, lo1 = _shift1(c), _shift1(hi), _shift1(lo)
            lg = ok & (c > hi) & (c1 <= hi1)
            sh = ok & (c < lo) & (c1 >= lo1)
        elif v["entry"] == "tsmom":
            m, m1 = F["mom28"], _shift1(F["mom28"])
            lg = ok & (m > 0) & (m1 <= 0)
            sh = ok & (m < 0) & (m1 >= 0)
        else:
            raise ValueError(f"kural varyantı değil: {name}")
    if v.get("d200"):
        side = d200_side(F, D, same_seg=same_seg)
        lg &= side > 0
        sh &= side < 0
    mult = float(v["stop_atr"])
    out = [(int(i), LONG, float(c[i] - mult * atr[i]), dict(v["exit"])) for i in np.flatnonzero(lg)]
    out += [(int(i), SHORT, float(c[i] + mult * atr[i]), dict(v["exit"])) for i in np.flatnonzero(sh)]
    return sorted(out, key=lambda x: (x[0], x[1]))


def monthly_signals(name: str, D: Mapping[str, Any], mt: Mapping[int, Mapping[str, Any]]) -> tuple[list[tuple], list[tuple], Counter]:
    """Aylık varyant: (olaylar [(gi, yön, stop, çıkış, ay)], adaylar [(gi, çıkış, ay)], atlama sayımı). Aday = işaret şartı dışındaki
    bütün şartları geçen ay sonu barı. m+1 verinin dışında (m son ay) → çıkış güvenlik sınırıyla; simülasyon KESİLDİ der."""
    v = VARIANTS[name]
    c, atr, sp, seg = D["close"], D["atr"], D["segpos"], D["seg"]
    mult = float(v["stop_atr"])
    looks = list(range(-(v["months"] - 1), 1)) if v["entry"] == "month_sma" else [-int(v["months"])]
    last_mi = max(mt) if mt else None
    sigs, cands, why = [], [], Counter()
    for m in sorted(mt):
        rec = mt[m]
        i = rec["last"]
        if not rec["usable"]:
            why["AY_KULLANILAMAZ"] += 1
            continue
        if sp[i] < MONTH_WARMUP_DAYS or not (np.isfinite(atr[i]) and atr[i] > 0):
            why["ISINMA"] += 1
            continue
        lk = [mt.get(m + d) for d in looks]
        if any(r is None or not r["usable"] or seg[r["last"]] != seg[i] for r in lk):
            why["GERİ_BAKIŞ_AYI"] += 1
            continue
        nxt = mt.get(m + 1)
        if nxt is None:
            if m != last_mi:
                why["SONRAKİ_AY_YOK"] += 1
                continue
            nb = MONTH_SAFETY_BARS
        elif not nxt["usable"] or seg[nxt["first"]] != seg[i] or nxt["first"] != i + 1:
            why["SONRAKİ_AY_KULLANILAMAZ"] += 1
            continue
        else:
            nb = int(nxt["n"])
        spec = {"kind": "month_end", "bars": nb}
        cands.append((i, spec, m))
        if v["entry"] == "month_sma":
            x = c[i] - float(np.mean([c[r["last"]] for r in lk]))
        else:
            x = c[i] / c[lk[0]["last"]] - 1
        if x > 0:
            sigs.append((i, LONG, float(c[i] - mult * atr[i]), spec, m))
        elif x < 0:
            sigs.append((i, SHORT, float(c[i] + mult * atr[i]), spec, m))
    return sigs, cands, why


# ---------------------------------------------------------------------------- simülasyon
def _map_exit(spec: Mapping[str, Any], F: Mapping[str, Any], sl: slice) -> tuple[dict, dict]:
    kind = spec["kind"]
    if kind in ("channel", "channel20"):
        n_ = int(spec["n"])
        return {"kind": "channel", "max_bars": int(spec["max_bars"])}, {"lo10": F[f"lo{n_}"][sl], "hi10": F[f"hi{n_}"][sl]}
    if kind == "sign":
        return {"kind": "sign", "max_bars": int(spec["max_bars"])}, {"mom28": F["mom28"][sl]}
    if kind == "hold":
        return {"kind": "hold", "bars": int(spec["bars"]), "max_bars": int(spec["bars"])}, {}
    if kind == "month_end":
        nb = int(spec["bars"])
        return {"kind": "hold", "bars": nb + 1, "max_bars": nb}, {}
    raise ValueError(f"bilinmeyen çıkış: {kind}")


def _mtm(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, s: float, stop: float, atr: float, cfg: L.LabConfig) -> float | None:
    """KESİLDİ bilgi satırı: girişten verinin sonuna; stop görüldüyse stopta, değilse son kapanışta (maliyet dahil)."""
    n = len(o)
    if n < 2:
        return None
    entry = float(o[1])
    risk = s * (entry - stop)
    if not (atr and atr == atr) or risk <= cfg.min_risk_atr * atr or risk > RISK_MAX_ATR * atr:
        return None
    px = float(c[n - 1])
    for k in range(1, n):
        if (s > 0 and lo[k] <= stop) or (s < 0 and h[k] >= stop):
            px = float(stop) if k == 1 else (min(o[k], stop) if s > 0 else max(o[k], stop))
            break
    cost = (entry + px) * cfg.cost_per_side
    return (s * (px - entry) - cost) / risk


def simulate_one(F: Mapping[str, Any], gi: int, side: str, stop: float, spec: Mapping[str, Any], atr_val: float, cfg: L.LabConfig, *,
                 family: str, name: str, cell: str, period: str | None, t_ms: int | None = None,
                 funding: dict | None = None) -> tuple[str, dict | None, float | None]:
    """Bir olay → signal_lab.simulate_rule (karar barından segment sonuna olan dilimde). Döner (atlama nedeni, satır, KESİLDİ
    piyasaya göre R). NO_FUTURE_DATA → son segmentte KESİLDİ, değilse BOŞLUK_TUTUŞ."""
    e = int(F["seg_end"][gi])
    sl = slice(gi, e + 1)
    arr = {k: F[k][sl] for k in ("open", "high", "low", "close")}
    s2, aux = _map_exit(spec, F, sl)
    t_ms = int(F["cms"][gi]) if t_ms is None else int(t_ms)
    ev = L.Event(F["symbol"], F["tf"] if spec["kind"] != "month_end" else "1M", family, name, side, 0, t_ms, float(stop),
                 trigger=None, exit=s2)
    why = L.simulate_rule(ev, arr, np.array([float(atr_val)]), aux, cfg)
    s = 1.0 if side == LONG else -1.0
    if why == "NO_FUTURE_DATA":
        if e == F["n"] - 1:
            return "KESİLDİ", None, _mtm(arr["open"], arr["high"], arr["low"], arr["close"], s, float(stop), float(atr_val), cfg)
        return "BOŞLUK_TUTUŞ", None, None
    if why:
        return why, None, None
    gj, gx = gi + 1, gi + ev.hold
    reason = ev.exit_reason
    kind = spec["kind"]
    exit_kind = "STOP" if reason == "STOP" else ("MONTH_END" if kind == "month_end" else ("HOLD" if kind == "hold" else reason))
    exit_ms = int(F["ts"][gx]) if reason == "RULE" else int(F["cms"][gx])
    entry_ms, entry_px = int(F["ts"][gj]), float(F["open"][gj])
    risk = s * (entry_px - float(stop))
    n8 = exit_ms // H8_MS - entry_ms // H8_MS
    row = {"symbol": F["symbol"], "tf": ev.tf, "family": family, "name": name, "side": side, "cell": cell, "i": int(gi),
           "t_ms": t_ms, "ts_i": int(F["ts"][gi]), "stop": float(stop), "r": float(ev.r), "cost_r": float(ev.cost_r),
           "exit_reason": reason, "exit_kind": exit_kind, "hold": int(ev.hold), "max_bars": int(s2["max_bars"]),
           "entry_ms": entry_ms, "exit_ms": exit_ms, "entry_px": entry_px, "risk_px": risk, "n8": int(n8),
           "r_fon": float(ev.r) - s * FUND_RATE_8H * n8 * entry_px / risk, "period": period, "seg": int(F["seg"][gi]),
           "funding_r": None, "r_net": None, "sig_ann": None, "ctx": {}}
    if funding is not None:
        from . import futures_lab as FL                  # tembel: yalnız vadeli mekân satırlarında
        ts_sl = F["ts"][sl]
        FL.funding_carry([ev], arr, ts_sl, tf_ms(F["tf"]), funding, names=frozenset({name}))
        fr = ev.funding_r
        row["funding_r"] = None if fr is None or not math.isfinite(float(fr)) else float(fr)
        row["r_net"] = None if row["funding_r"] is None else float(ev.r) + row["funding_r"]
    return "", row, None


def hold_of(row: Mapping[str, Any]) -> int:
    """H_τ: RULE → hold − 1 (çıkış barı − giriş barı); STOP → hold (stop barı − giriş barı + 1); TIME → max_bars."""
    if row["exit_reason"] == "RULE":
        return int(row["hold"]) - 1
    if row["exit_reason"] == "STOP":
        return int(row["hold"])
    return int(row["max_bars"])


# ---------------------------------------------------------------------------- sayım
class Counts:
    """(aile, ad, etiket) → sinyal, işlem, atlama nedenleri; KESİLDİ piyasaya göre R'leri (bilgi)."""

    def __init__(self) -> None:
        self.d: dict[str, dict[str, Any]] = {}

    def _k(self, family: str, name: str, tag: str) -> dict[str, Any]:
        return self.d.setdefault(f"{family}|{name}|{tag}", {"signals": 0, "trades": 0, "skipped": {}, "mtm": []})

    def signal(self, family: str, name: str, tag: str) -> None:
        self._k(family, name, tag)["signals"] += 1

    def result(self, family: str, name: str, tag: str, why: str, mtm: float | None = None) -> None:
        c = self._k(family, name, tag)
        if why:
            c["skipped"][why] = c["skipped"].get(why, 0) + 1
            if why == "KESİLDİ" and mtm is not None:
                c["mtm"].append(float(mtm))
        else:
            c["trades"] += 1

    def skip(self, family: str, name: str, tag: str, why: str, k: int = 1) -> None:
        c = self._k(family, name, tag)
        c["skipped"][why] = c["skipped"].get(why, 0) + int(k)

    def get(self, family: str, name: str, tags: Iterable[str]) -> dict[str, Any]:
        out: dict[str, Any] = {"signals": 0, "trades": 0, "skipped": {}, "kesildi_mtm": None}
        mt: list[float] = []
        for t in tags:
            c = self.d.get(f"{family}|{name}|{t}")
            if not c:
                continue
            out["signals"] += c["signals"]
            out["trades"] += c["trades"]
            for k, v in c["skipped"].items():
                out["skipped"][k] = out["skipped"].get(k, 0) + v
            mt += c["mtm"]
        out["kesildi_mtm"] = {"n": len(mt), "mean_r": round(float(np.mean(mt)), 4) if mt else None}
        return out

    def as_json(self) -> dict[str, Any]:
        return {k: {**v, "mtm": {"n": len(v["mtm"]), "mean_r": round(float(np.mean(v["mtm"])), 4) if v["mtm"] else None}}
                for k, v in sorted(self.d.items())}


def period_of(t_ms: int, periods: Mapping[str, tuple[int, int]]) -> str | None:
    for p, (a, b) in periods.items():
        if a <= t_ms < b:
            return p
    return None


def _run_events(F: Mapping[str, Any], evs: Iterable[tuple], cfg: L.LabConfig, counts: Counts, periods: Mapping[str, tuple[int, int]], *,
                family: str, name: str, atr_of: Callable[[int], float] | None = None, funding: dict | None = None,
                t_of: Callable[[int], int] | None = None, sig_of: Callable[[int, int], float | None] | None = None) -> list[dict]:
    """[(gi, yön, stop, çıkış, etiket, ...)] → satırlar (dönem etiketli; sayım dahil)."""
    rows = []
    for ev in evs:
        gi, side, stop, spec, tag = ev[:5]
        t_ms = int(F["cms"][gi]) if t_of is None else int(t_of(gi))
        per = period_of(t_ms, periods)
        counts.signal(family, name, tag)
        if per is None:
            counts.result(family, name, tag, "DÖNEM_DIŞI")
            continue
        av = float(F["atr"][gi]) if atr_of is None else float(atr_of(gi))
        why, row, mtm = simulate_one(F, gi, side, stop, spec, av, cfg, family=family, name=name, cell=tag, period=per, t_ms=t_ms,
                                     funding=funding)
        counts.result(family, name, tag, why, mtm)
        if row is not None:
            if sig_of is not None:
                row["sig_ann"] = sig_of(gi, t_ms)
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------- plasebolar (aile A)
def matched_placebos(name: str, real: list[dict], F: Mapping[str, Any], periods: Mapping[str, tuple[int, int]], counts: Counts, *,
                     family: str = PLACEBO_FAMILY) -> list[tuple]:
    """Maruziyet eşli hüküm plasebosu: tamamlanmış her gerçek işlem için K = 5 eş (aynı yön, aynı dönem, hold H_τ)."""
    v = VARIANTS[name]
    mult = float(v["stop_atr"])
    sp, atr, cms, se = F["segpos"], F["atr"], F["cms"], F["seg_end"]
    with np.errstate(invalid="ignore"):
        base = (sp >= WARMUP) & np.isfinite(atr) & (atr > 0)
    byp = {p: np.flatnonzero(base & (cms >= a) & (cms < b)) for p, (a, b) in periods.items()}
    tfl = F["tf"]
    out = []
    pname = PLACEBO_PREFIX + name
    for r in sorted(real, key=lambda x: (x["t_ms"], x["side"])):
        H = hold_of(r)
        C0 = byp.get(r["period"])
        C = C0[C0 + 1 + H <= se[C0]] if C0 is not None else np.zeros(0, dtype=np.int64)
        if not len(C):
            counts.skip(family, pname, r["side"], "EŞ_ADAY_YOK", K_MATCH)
            continue
        for k in range(1, K_MATCH + 1):
            c = int(C[_crc(F["symbol"], tfl, name, r["side"], int(r["ts_i"]), k) % len(C)])
            s = 1.0 if r["side"] == LONG else -1.0
            out.append((c, r["side"], float(F["sclose"][c] - s * mult * atr[c]), {"kind": "hold", "bars": H}, r["side"]))
    return out


def monthly_placebos(name: str, sigs: list[tuple], cands: list[tuple], F: Mapping[str, Any]) -> list[tuple]:
    """Aylık hüküm plasebosu: LONG (SHORT bilgi) p tabanlı; İKİ YÖN işaret rastgeleleştirmesi (gerçek sinyal ayları)."""
    v = VARIANTS[name]
    mult = float(v["stop_atr"])
    c, atr, ts = F["sclose"], F["atr"], F["ts"]
    out = []
    n = len(cands)
    for side, s in ((LONG, 1.0), (SHORT, -1.0)):
        k = sum(1 for x in sigs if x[1] == side)
        p = k / n if n else 0.0
        for gi, spec, _m in cands:
            if _h(F["symbol"], "1M", name, side, int(ts[gi])) < p:
                out.append((gi, side, float(c[gi] - s * mult * atr[gi]), dict(spec), side))
    for gi, _side, _stop, spec, _m in sigs:
        side = LONG if _h(F["symbol"], "1M", name, BOTH_KEY, int(ts[gi])) < 0.5 else SHORT
        s = 1.0 if side == LONG else -1.0
        out.append((gi, side, float(c[gi] - s * mult * atr[gi]), dict(spec), BOTH_KEY))
    return out


def info_placebos(name: str, sigs: list[tuple], F: Mapping[str, Any]) -> list[tuple]:
    """A gün/4h bilgi plasebosu (gold_v1/signal_lab tarzı): aday = ısınmadan sonra ATR'si tanımlı her bar; u < p; varyantın
    stop katı ve çıkışı."""
    v = VARIANTS[name]
    mult = float(v["stop_atr"])
    with np.errstate(invalid="ignore"):
        cand = np.flatnonzero((F["segpos"] >= WARMUP) & np.isfinite(F["atr"]) & (F["atr"] > 0))
    out = []
    for side, s in ((LONG, 1.0), (SHORT, -1.0)):
        p = sum(1 for x in sigs if x[1] == side) / len(cand) if len(cand) else 0.0
        if p <= 0:
            continue
        for gi in cand:
            if _h(F["symbol"], F["tf"], name, side, int(F["ts"][gi])) < p:
                out.append((int(gi), side, float(F["sclose"][gi] - s * mult * F["atr"][gi]), dict(v["exit"]), side))
    return out


def a_variant_rows(name: str, F: Mapping[str, Any], D: Mapping[str, Any], mt: Mapping[int, Mapping[str, Any]], cfg: L.LabConfig,
                   periods: Mapping[str, tuple[int, int]], counts: Counts, *, same_seg: bool = True, info: bool = True,
                   sig_of: Callable[[int, int], float | None] | None = None) -> list[dict]:
    """Aile A'nın bir varyantı: gerçek olaylar, hüküm plasebosu ve (gün/4h) bilgi plasebosu → satırlar."""
    v = VARIANTS[name]
    pname = PLACEBO_PREFIX + name
    if v["entry"] in MONTHLY:
        sigs, cands, why = monthly_signals(name, D, mt)
        for k, x in why.items():
            counts.skip(FAMILY, name, "AY", k, x)
        real = _run_events(D, [(gi, sd, st, sp, sd) for gi, sd, st, sp, _m in sigs], cfg, counts, periods, family=FAMILY, name=name,
                           sig_of=sig_of)
        plac = _run_events(D, monthly_placebos(name, sigs, cands, D), cfg, counts, periods, family=PLACEBO_FAMILY, name=pname,
                           sig_of=sig_of)
        return real + plac
    sigs = rule_signals(name, F, D, same_seg=same_seg)
    real = _run_events(F, [(gi, sd, st, sp, sd) for gi, sd, st, sp in sigs], cfg, counts, periods, family=FAMILY, name=name,
                       sig_of=sig_of)
    plac = _run_events(F, matched_placebos(name, real, F, periods, counts), cfg, counts, periods, family=PLACEBO_FAMILY, name=pname,
                       sig_of=sig_of)
    rows = real + plac
    if info:
        rows += _run_events(F, info_placebos(name, sigs, F), cfg, counts, periods, family=INFO_FAMILY, name=INFO_PREFIX + name,
                            sig_of=sig_of)
    return rows


def daily_sig_lookup(D: Mapping[str, Any], F: Mapping[str, Any] | None, *, same_seg: bool) -> Callable[[int, int], float | None]:
    """C'nin σ_yıllık'ı: kapanış anı ≤ karar anı olan son günlük bar (Dukascopy'de karar barıyla aynı segment)."""
    def f(gi: int, t_ms: int) -> float | None:
        j = int(np.searchsorted(D["cms"], t_ms, side="right")) - 1
        if j < 0:
            return None
        if same_seg and F is not None and D["seg"][j] != F["seg"][gi]:
            return None
        x = D["sig"][j]
        return float(x) if np.isfinite(x) else None
    return f


# ---------------------------------------------------------------------------- aile B (hafta sonu)
def weekend_table(H: Mapping[str, Any], Dd: Mapping[str, Any], start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    """Penceredeki her Cuma için F, S (UTC), F − 1 ve S − 1 saat barlarının indeksi, ATR_d, C'nin σ'sı, tatil bayrağı."""
    d0 = dnum_of(date.fromisoformat(_iso(start_ms)[:10]))
    d1 = dnum_of(date.fromisoformat(_iso(end_ms - 1)[:10]))
    fr = [d for d in range(d0, d1 + 1) if (d + 3) % 7 == 4]
    if not fr:
        return []
    F_ms = ny_to_utc_ms(fr, 17)
    S_ms = ny_to_utc_ms(np.asarray(fr) + 2, 18)
    ts = H["ts"]

    def idx(x: int) -> int:
        k = int(np.searchsorted(ts, x))
        return k if k < len(ts) and ts[k] == x else -1
    out = []
    for d, f_ms, s_ms in zip(fr, F_ms.tolist(), S_ms.tolist()):
        if s_ms >= end_ms:
            continue
        jd = int(np.searchsorted(Dd["cms"], f_ms, side="right")) - 1
        js = int(np.searchsorted(Dd["cms"], s_ms, side="right")) - 1
        atr_d = float(Dd["atr"][jd]) if jd >= 0 else float("nan")
        sig = float(Dd["sig"][js]) if js >= 0 and np.isfinite(Dd["sig"][js]) else None
        out.append({"friday": str(date_of(d)), "F": int(f_ms), "S": int(s_ms), "iF": idx(f_ms - HOUR_MS), "iS": idx(s_ms - HOUR_MS),
                    "atr_d": atr_d, "sig": sig, "holiday": holiday_friday(date_of(d))})
    return out


def week_status(w: Mapping[str, Any], N: int, H: Mapping[str, Any]) -> str:
    """'' (uygun) ya da atlama nedeni (sıra: VERİ_SONU, EKSİK_BAR, BOŞLUK, EKSİK_BAR (tutuş), HACİMSİZ, ATR_YOK; tutuş verinin
    sonunu aşarsa KESİLDİ'yi simülasyon verir)."""
    iF, iS = w["iF"], w["iS"]
    ts, n = H["ts"], H["n"]
    if not n or w["S"] - HOUR_MS > int(ts[-1]):
        return "VERİ_SONU"
    if iF < 0 or iS < 0:
        return "EKSİK_BAR"
    if H["seg"][iF] != H["seg"][iS]:
        return "BOŞLUK"
    last = int(ts[-1])
    for k in range(0, N + 1):
        t = w["S"] + k * HOUR_MS
        if t > last:
            break                                      # veri bitti: simülasyon KESİLDİ der
        j = iS + 1 + k
        if j >= n or ts[j] != t or H["seg"][j] != H["seg"][iS]:
            return "EKSİK_BAR"
    if H["volume"][iF] == 0 or H["volume"][iS] == 0:
        return "HACİMSİZ"
    if not (np.isfinite(w["atr_d"]) and w["atr_d"] > 0):
        return "ATR_YOK"
    return ""


def weekend_signals(name: str, H: Mapping[str, Any], weeks: list[dict]) -> tuple[list[dict], list[tuple], Counter]:
    """(uygun haftalar, olaylar [(hafta, yön, stop)], atlama sayımı): z = (P_S − P_F) / ATR_d; z < 0 → LONG, z > 0 → SHORT."""
    v = VARIANTS[name]
    N, zmin = int(v["hold_h"]), float(v["z_min"])
    c = H["close"]
    elig, sigs, why = [], [], Counter()
    for w in weeks:
        st = week_status(w, N, H)
        if st:
            why[st] += 1
            continue
        elig.append(w)
        z = (c[w["iS"]] - c[w["iF"]]) / w["atr_d"]
        hit = (abs(z) > zmin) if v["z_strict"] else (abs(z) >= zmin)
        if z == 0 or not hit:
            continue
        side = LONG if z < 0 else SHORT
        s = 1.0 if side == LONG else -1.0
        sigs.append((w, side, float(c[w["iS"]] - s * w["atr_d"])))
    return elig, sigs, why


def b_variant_rows(name: str, H: Mapping[str, Any], Dd: Mapping[str, Any], weeks: list[dict], cfg: L.LabConfig,
                   periods: Mapping[str, tuple[int, int]], counts: Counts, *, symbol: str, funding: dict | None = None,
                   info: bool = True) -> list[dict]:
    """Aile B'nin bir varyantı: hafta sonu olayları, S'ye bağlı hüküm plasebosu, (bilgi) her saat plasebosu → satırlar."""
    v = VARIANTS[name]
    N = int(v["hold_h"])
    spec = {"kind": "hold", "bars": N}
    c = H["close"]
    elig, sigs, why = weekend_signals(name, H, weeks)
    for k, x in why.items():
        counts.skip(FAMILY, name, "HAFTA", k, x)
    by_i = {w["iS"]: w for w in elig}
    atr_of = lambda gi: by_i[gi]["atr_d"]  # noqa: E731
    t_of = lambda gi: by_i[gi]["S"]  # noqa: E731
    sig_of = lambda gi, t: by_i[gi]["sig"]  # noqa: E731
    real = _run_events(H, [(w["iS"], sd, st, spec, sd) for w, sd, st in sigs], cfg, counts, periods, family=FAMILY, name=name,
                       atr_of=atr_of, t_of=t_of, funding=funding, sig_of=sig_of)
    pl = []
    for side, s in ((LONG, 1.0), (SHORT, -1.0)):
        p = sum(1 for x in sigs if x[1] == side) / len(elig) if elig else 0.0
        for w in elig:
            if _h(symbol, "1h", name, side, int(H["ts"][w["iS"]])) < p:
                pl.append((w["iS"], side, float(c[w["iS"]] - s * w["atr_d"]), spec, side))
    for w, _sd, _st in sigs:
        side = LONG if _h(symbol, "1h", name, BOTH_KEY, int(H["ts"][w["iS"]])) < 0.5 else SHORT
        s = 1.0 if side == LONG else -1.0
        pl.append((w["iS"], side, float(c[w["iS"]] - s * w["atr_d"]), spec, BOTH_KEY))
    rows = real + _run_events(H, pl, cfg, counts, periods, family=PLACEBO_FAMILY, name=PLACEBO_PREFIX + name, atr_of=atr_of, t_of=t_of,
                              funding=funding, sig_of=sig_of)
    if info:
        rows += b_info_rows(name, H, Dd, sigs, cfg, periods, counts, symbol=symbol)
    return rows


def b_info_rows(name: str, H: Mapping[str, Any], Dd: Mapping[str, Any], sigs: list[tuple], cfg: L.LabConfig,
                periods: Mapping[str, tuple[int, int]], counts: Counts, *, symbol: str) -> list[dict]:
    """Bilgi: haftanın her saatinden plasebo (aday = ATR_d'si tanımlı her 1h bar); EKSİK_BAR ve HACİMSİZ uygulanır."""
    v = VARIANTS[name]
    N = int(v["hold_h"])
    jd = np.searchsorted(Dd["cms"], H["cms"], side="right") - 1
    atr_d = np.where(jd >= 0, Dd["atr"][np.maximum(jd, 0)], np.nan) if Dd["n"] else np.full(H["n"], np.nan)
    with np.errstate(invalid="ignore"):
        cand = np.flatnonzero(np.isfinite(atr_d) & (atr_d > 0))
    ts, seg, n = H["ts"], H["seg"], H["n"]
    iname = INFO_PREFIX + name
    picks = []
    for side, s in ((LONG, 1.0), (SHORT, -1.0)):
        p = sum(1 for x in sigs if x[1] == side) / len(cand) if len(cand) else 0.0
        if p <= 0:
            continue
        for gi in cand:
            if _h(symbol, "1h", name, side, int(ts[gi])) >= p:
                continue
            j = gi + 1 + N
            if j < n and (ts[j] - ts[gi] != (N + 1) * HOUR_MS or seg[j] != seg[gi]):
                counts.skip(INFO_FAMILY, iname, side, "EKSİK_BAR")
                continue
            if H["volume"][gi] == 0:
                counts.skip(INFO_FAMILY, iname, side, "HACİMSİZ")
                continue
            picks.append((int(gi), side, float(H["close"][gi] - s * atr_d[gi]), {"kind": "hold", "bars": N}, side))
    return _run_events(H, picks, cfg, counts, periods, family=INFO_FAMILY, name=iname, atr_of=lambda gi: atr_d[gi])


# ---------------------------------------------------------------------------- hüküm (signal_lab fonksiyonları değişmeden)
def _vals(rows: Iterable[Mapping[str, Any]], key: str) -> list[Mapping[str, Any]]:
    return [e for e in rows if e.get(key) is not None and math.isfinite(float(e[key]))]


def pstats(rows: list[Mapping[str, Any]], cfg: L.LabConfig, key: str = "r", *, by: str = "day") -> dict[str, Any]:
    """signal_lab.r_stats (gün kümeli; `by='month'` ay kümeli bilgi) + maliyet payı."""
    sel = _vals(rows, key)
    if not sel:
        return {"n": 0}
    rs = np.array([float(e[key]) for e in sel], dtype=float)
    if by == "month":
        cl = np.array([mi_from_dnum([int(e["t_ms"]) // DAY_MS])[0] for e in sel])
    else:
        cl = np.array([int(e["t_ms"]) // DAY_MS for e in sel])
    st = L.r_stats(rs, cfg.bootstrap_iters, days=cl)
    st["cost_r"] = round(float(np.mean([float(e["cost_r"]) for e in sel])), 4)
    return st


def judge(real: list[Mapping[str, Any]], plac: list[Mapping[str, Any]], cfg: L.LabConfig, *, key: str = "r") -> dict[str, Any]:
    """aggregate'in HEPSİ grubunun hükmü, sabit tarihli dönem etiketiyle (satırların `period` alanı IS/OOS)."""
    g = {p: pstats([e for e in real if e["period"] == p], cfg, key) for p in ("IS", "OOS")}
    pg = {p: pstats([e for e in plac if e["period"] == p], cfg, key) for p in ("IS", "OOS")}
    vs, dci = None, None
    if pg["IS"].get("n", 0) >= cfg.min_oos and pg["OOS"].get("n", 0) >= cfg.min_oos and g["IS"].get("n") and g["OOS"].get("n"):
        vs = {"IS": round(g["IS"]["mean_r"] - pg["IS"]["mean_r"], 4), "OOS": round(g["OOS"]["mean_r"] - pg["OOS"]["mean_r"], 4),
              "placebo_mean_r": [pg["IS"]["mean_r"], pg["OOS"]["mean_r"]], "placebo_n": [pg["IS"]["n"], pg["OOS"]["n"]]}
        dci = {}
        for p in ("IS", "OOS"):
            a, b = _vals([e for e in real if e["period"] == p], key), _vals([e for e in plac if e["period"] == p], key)
            dci[p] = L.diff_ci(np.array([float(e[key]) for e in a]), np.array([int(e["t_ms"]) // DAY_MS for e in a]),
                               np.array([float(e[key]) for e in b]), np.array([int(e["t_ms"]) // DAY_MS for e in b]), cfg.bootstrap_iters)
    verdict = L.verdict(g["IS"], g["OOS"], cfg, vs)
    if verdict == L.V_STRONG and vs is not None:
        vs["ci95"] = dci
    strict = L.verdict_strict(verdict, vs.get("ci95") if vs else None)
    return {"IS": g["IS"], "OOS": g["OOS"], "placebo": {"IS": G._brief(pg["IS"]), "OOS": G._brief(pg["OOS"])}, "vs_placebo": vs,
            "diff_ci": dci, "verdict": verdict, "verdict_strict": strict, "replicated": L.replicated(g["IS"], g["OOS"], cfg)}


def judge_all(real: list[Mapping[str, Any]], plac: list[Mapping[str, Any]], cfg: L.LabConfig, key: str = "r") -> dict[str, Any]:
    """Bölünmeden (görülmüş / mekân satırları, bilgi): n, ort., %95 aralık, plaseboya göre fark ve aralığı."""
    a, b = _vals(real, key), _vals(plac, key)
    st, pst = pstats(a, cfg, key), pstats(b, cfg, key)
    diff = round(st["mean_r"] - pst["mean_r"], 4) if st.get("n") and pst.get("n") else None
    dci = L.diff_ci(np.array([float(e[key]) for e in a]), np.array([int(e["t_ms"]) // DAY_MS for e in a]),
                    np.array([float(e[key]) for e in b]), np.array([int(e["t_ms"]) // DAY_MS for e in b]),
                    cfg.bootstrap_iters) if a and b else None
    return {"all": st, "placebo": G._brief(pst), "diff": diff, "diff_ci": dci}


def _ncand(groups: list[Mapping[str, Any]]) -> int:
    return sum(1 for g in groups if g["verdict"] != L.V_THIN and g["replicated"] and g["IS"]["mean_r"] > 0)


def cand_rate(groups: list[Mapping[str, Any]]) -> float | None:
    gs = [g for g in groups if g["verdict"] != L.V_THIN]
    return round(sum(1 for g in gs if g["replicated"] and g["IS"]["mean_r"] > 0) / len(gs), 4) if gs else None


def placebo_tags(name: str, cell: str) -> tuple[str, ...]:
    """Hücrenin hüküm plasebosu satırlarının etiketleri."""
    if cell == LONG:
        return (LONG,)
    if cell == SHORT:
        return (SHORT,)
    return (LONG, SHORT) if VARIANTS[name]["placebo"] == "matched" else (BOTH_KEY,)


def cell_rows(rows: list[Mapping[str, Any]], name: str, cell: str) -> tuple[list, list, list]:
    """(gerçek, hüküm plasebosu, bilgi plasebosu) satırları."""
    sides = (LONG, SHORT) if cell == BOTH_CELL else (cell,)
    real = [e for e in rows if e["family"] == FAMILY and e["name"] == name and e["side"] in sides]
    tags = placebo_tags(name, cell)
    pl = [e for e in rows if e["family"] == PLACEBO_FAMILY and e["name"] == PLACEBO_PREFIX + name and e["cell"] in tags]
    inf = [e for e in rows if e["family"] == INFO_FAMILY and e["name"] == INFO_PREFIX + name and e["side"] in sides]
    return real, pl, inf


# ---------------------------------------------------------------------------- aylık hedef, daha yüksek risk, C
def _month_sums(rows: list[Mapping[str, Any]], key: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for e in _vals(rows, key):
        out.setdefault(G.month_key(int(e["t_ms"])), []).append(float(e[key]))
    return out


def max_drawdown(rows: list[Mapping[str, Any]], key: str = "r_fon") -> float:
    """Çıkış anına göre sıralı birikimli R'nin tepeden (0 dahil) en derin inişi (R; ≥ 0)."""
    sel = sorted(_vals(rows, key), key=lambda e: (int(e["exit_ms"]), int(e["t_ms"])))
    eq = np.cumsum([float(e[key]) for e in sel]) if sel else np.zeros(0)
    if not len(eq):
        return 0.0
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    return float(max(0.0, np.max(peak - eq)))


def higher_risk(strict: str, mon: Mapping[str, Any] | None, all_rows: list[Mapping[str, Any]], oos_rows: list[Mapping[str, Any]],
                plac_oos_mean_fon: float | None) -> dict[str, Any]:
    """"Daha yüksek risk" bilgi satırı (yalnız sıkı GÜÇLÜ ADAY hücrelerde sayı basılır)."""
    if strict != L.V_STRONG or not mon or mon.get("mean_pct_month_exact") is None:
        return {"text": "kenar yok"}
    mean_r = float(mon["mean_pct_month_exact"]) / G.RISK_PCT
    if mean_r <= 0:
        return {"text": "fonlama sonrası kenar yok", "mean_r_month": mean_r}
    X = 1.0 / mean_r
    ci = mon.get("ci95_pct_month")
    a, b = (ci[0] * X / G.RISK_PCT, ci[1] * X / G.RISK_PCT) if ci else (None, None)
    text = (f"ortalama tahmin {X:.2f}% riskte +%1/ay'a karşılık gelir; %95 aralık [{_fmt(a)}, {_fmt(b)}]; ulaşılacağı garanti "
            "değildir")
    out: dict[str, Any] = {"text": text, "X": X, "ci_pct_at_X": [a, b], "mean_r_month": mean_r}
    flags = []
    if X > PROFILE_CAP_RISK:
        flags.append("profil tavanını aşar — uygulanamaz")
    cmax = int(((mon.get("concurrency") or {}).get("max")) or 0)
    tor = cmax * X
    out["total_open_risk_pct"] = {"max_concurrent": cmax, "value": tor, "caps": [OPEN_RISK_CAP, OPEN_RISK_LEARNING],
                                  "flag": tor > OPEN_RISK_CAP}
    if tor > OPEN_RISK_CAP:
        flags.append(f"toplam açık risk %{tor:.1f} > %{OPEN_RISK_CAP:.0f}")
    full = list((mon.get("monthly_r") or {}).keys())
    ms = _month_sums(oos_rows, "r_fon")
    if plac_oos_mean_fon is not None and full:
        pk = [len(ms.get(k, [])) * float(plac_oos_mean_fon) for k in full]
        ex = [sum(ms.get(k, [])) - x for k, x in zip(full, pk)]
        mex = float(np.mean(ex))
        out["excess"] = {"mean_r_month": mex, "X_excess": (1.0 / mex) if mex > 0 else None,
                         "text": "plasebo üstü fazla yok" if mex <= 0 else f"plasebo üstü fazlayla X = {1.0 / mex:.2f}%",
                         "drift_share": float(np.mean(pk)) / mean_r}
    else:
        out["excess"] = {"text": "plasebo karşılaştırması yok"}
    ddr_all, ddr_oos = max_drawdown(all_rows), max_drawdown(oos_rows)
    dd_all, dd_oos = ddr_all * X, ddr_oos * X
    out["drawdown_r"] = {"all": ddr_all, "oos": ddr_oos}
    out["drawdown_pct"] = {"all": dd_all, "oos": dd_oos, "flag": dd_all > DD_FLAG_PCT}
    if dd_all > DD_FLAG_PCT:
        flags.append(f"en derin düşüş %{dd_all:.1f} > %{DD_FLAG_PCT:.0f}")
    lev = [(X / 100.0) / (float(e["risk_px"]) / float(e["entry_px"])) for e in all_rows if float(e["risk_px"]) > 0]
    if lev:
        md, mx = float(np.median(lev)), float(np.max(lev))
        out["leverage"] = {"median": md, "max": mx, "over_paper": mx > LEV_PAPER, "over_max": mx > LEV_MAX}
        if mx > LEV_PAPER:
            flags.append(f"kaldıraç en çok {mx:.2f} > {LEV_PAPER:.0f}" + (f" (> {LEV_MAX:.0f})" if mx > LEV_MAX else ""))
    out["flags"] = flags
    out["curse_tr"] = GOLD_V2_REGISTRY["higher_risk"]["curse_tr"]
    return out


def _fmt(x: Any, nd: int = 2) -> str:
    return "—" if x is None else f"{x:+.{nd}f}"


def c_sizing(strict: str, rows: list[Mapping[str, Any]], cfg: L.LabConfig, oos_start: int, oos_months: tuple[int, int],
             series_months: tuple[int, int]) -> dict[str, Any]:
    """C (bilgi): oynaklık hedefli ağırlık f = min(0,10 / σ, 2), toplam tavan Σf ≤ 2 giriş sırasıyla; aylık % = Σ f × g × 100."""
    if strict != L.V_STRONG:
        return {"text": "kenar yok"}
    sel = sorted(_vals(rows, "r_fon"), key=lambda e: (int(e["entry_ms"]), int(e["t_ms"])))
    open_: list[tuple[int, float]] = []
    trimmed, no_sig, uncapped_max = 0, 0, 0.0
    w = []
    for e in sel:
        open_ = [(x, f) for x, f in open_ if x > int(e["entry_ms"])]
        sg = e.get("sig_ann")
        f0 = min(C_TARGET_VOL / float(sg), C_MAX_W) if sg else 0.0
        if not sg:
            no_sig += 1
        tot = sum(f for _, f in open_)
        uncapped_max = max(uncapped_max, sum(f for _, f in open_) + f0)
        f = max(0.0, min(f0, C_TOTAL_CAP - tot))
        if f < f0:
            trimmed += 1
        open_.append((int(e["exit_ms"]), f))
        w.append(f)
    gm = [f * float(e["r_fon"]) * float(e["risk_px"]) / float(e["entry_px"]) * 100.0 for f, e in zip(w, sel)]
    by: dict[str, float] = {}
    for x, e in zip(gm, sel):
        k = G.month_key(int(e["t_ms"]))
        by[k] = by.get(k, 0.0) + x
    oos = [mi_str(m) for m in range(oos_months[0], oos_months[1] + 1)]
    vals = np.array([by.get(k, 0.0) for k in oos])
    ci = None
    if len(vals) >= G.MONTH_MIN_FOR_CI:
        idx = np.random.default_rng(G.MONTH_SEED).integers(0, len(vals), size=(cfg.bootstrap_iters, len(vals)))
        mm = vals[idx].mean(axis=1)
        ci = [float(np.quantile(mm, 0.025)), float(np.quantile(mm, 0.975))]
    allm = [mi_str(m) for m in range(series_months[0], series_months[1] + 1)]
    eq = np.cumsum([by.get(k, 0.0) for k in allm])
    dd = float(np.max(np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq)) if len(eq) else 0.0
    return {"text": "bilgi (C)", "oos_mean_pct_month": float(vals.mean()) if len(vals) else None, "ci95_pct_month": ci,
            "max_drawdown_pct": dd, "trimmed": trimmed, "no_sigma": no_sig, "uncapped_max_total_w": uncapped_max,
            "uncapped_flag": uncapped_max > C_TOTAL_CAP,
            "warn_tr": GOLD_V2_REGISTRY["higher_risk"]["text_tr"].split(";")[-1].strip()}


# ---------------------------------------------------------------------------- hücre raporu
def _periods_ms(fam_w: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    a = win_ms(*fam_w["is"])
    b = win_ms(*fam_w["oos"])
    return {"IS": (a[0], b[0]), "OOS": (b[0], b[1])}


def family_cells(rows: list[dict], counts: Counts, names: Iterable[str], cfg: L.LabConfig, fam_w: Mapping[str, Any]) -> dict[str, Any]:
    """Hüküm hücreleri (LONG, İKİ YÖN), SHORT ve bilgi plasebosu satırları, aylık ölçü, daha yüksek risk, C, aday oranları."""
    per = _periods_ms(fam_w)
    oos0, end = per["OOS"]
    open_end = day_ms(fam_w["open_end"])
    m0, m1 = mi_parse(fam_w["months"][0]), mi_parse(fam_w["months"][1])
    s0, s1 = mi_parse(fam_w["is"][0][:7]), mi_parse(fam_w["oos"][1][:7])
    cells, shorts, infos, pcells = [], [], [], []
    for name in names:
        v = VARIANTS[name]
        for cell in CELLS + (SHORT,):
            real, pl, inf = cell_rows(rows, name, cell)
            jd = judge(real, pl, cfg)
            sides = (LONG, SHORT) if cell == BOTH_CELL else (cell,)
            base = {"name": name, "family": v["family"], "tf": v["tf"], "cell": cell,
                    "main": (name, cell) in MAIN_CELLS, **jd,
                    "fon": {p: G._brief(pstats([e for e in real if e["period"] == p], cfg, "r_fon")) for p in ("IS", "OOS")},
                    "placebo_fon": {p: G._brief(pstats([e for e in pl if e["period"] == p], cfg, "r_fon")) for p in ("IS", "OOS")},
                    "month_ci": {p: (pstats([e for e in real if e["period"] == p], cfg, "r", by="month") or {}).get("ci95")
                                 for p in ("IS", "OOS")},
                    "counts": counts.get(FAMILY, name, sides),
                    "placebo_counts": counts.get(PLACEBO_FAMILY, PLACEBO_PREFIX + name, placebo_tags(name, cell))}
            if cell == SHORT:
                shorts.append(base)
                continue
            mon = G.monthly_stats(real, oos0 - 1, end - 1, iters=cfg.bootstrap_iters, key="r_fon", open_end_ms=open_end)
            mon_r = G.monthly_stats(real, oos0 - 1, end - 1, iters=cfg.bootstrap_iters, key="r", open_end_ms=open_end)
            pfon = base["placebo_fon"]["OOS"].get("mean_r")
            pfon_exact = float(np.mean([float(e["r_fon"]) for e in _vals(pl, "r_fon") if e["period"] == "OOS"])) \
                if pfon is not None else None
            oos_rows = [e for e in real if e["period"] == "OOS"]
            base.update({"monthly": mon, "monthly_r": {k: mon_r.get(k) for k in ("months", "mean_pct_month", "ci95_pct_month",
                                                                                   "share_months_ge_target")},
                         "meets_target": G.meets_target(mon, [jd["verdict_strict"]]),
                         "higher_risk": higher_risk(jd["verdict_strict"], mon, real, oos_rows, pfon_exact),
                         "c_sizing": c_sizing(jd["verdict_strict"], real, cfg, oos0, (m0, m1), (s0, s1)),
                         "concurrency_all": G.concurrency(real, per["IS"][0], end) if real else {"max": 0, "mean": None},
                         "note": ""})
            cells.append(base)
            pj = judge(pl, [], cfg)
            pcells.append({"name": PLACEBO_PREFIX + name, "family": v["family"], "cell": cell, "IS": pj["IS"], "OOS": pj["OOS"],
                           "verdict": pj["verdict"], "replicated": pj["replicated"]})
            if inf:
                ij = judge(real, inf, cfg)
                infos.append({"name": name, "cell": cell, "placebo": ij["placebo"], "vs_placebo": ij["vs_placebo"],
                              "counts": counts.get(INFO_FAMILY, INFO_PREFIX + name, sides)})
    return {"cells": cells, "short_info": shorts, "info_placebo": infos, "placebo_cells": pcells,
            "candidate_rate": {"real": cand_rate(cells), "placebo": cand_rate(pcells), "real_tested": sum(1 for g in cells if g["verdict"] != L.V_THIN),
                               "placebo_tested": sum(1 for g in pcells if g["verdict"] != L.V_THIN),
                               "strong": sum(1 for g in cells if g["verdict"] == L.V_STRONG),
                               "strict_strong": sum(1 for g in cells if g["verdict_strict"] == L.V_STRONG),
                               "candidates": _ncand(cells), "placebo_candidates": _ncand(pcells)},
            "periods": {k: [_iso(a), _iso(b)] for k, (a, b) in per.items()}}


# ---------------------------------------------------------------------------- aile koşuları
def run_family_a(hourly: pd.DataFrame, cfg: L.LabConfig, fam_w: Mapping[str, Any], *, usable_months: set[int] | None,
                 symbol: str = DUKA_SYMBOL, names: Iterable[str] = A_NAMES, info: bool = True) -> tuple[dict, list[dict], dict]:
    """Aile A (Dukascopy): saatlik → günlük/4h, 8 varyant, hüküm hücreleri. Döner (rapor, satırlar, seri bilgisi)."""
    D, H4, finfo = duka_frames(hourly, symbol=symbol, d_range=(fam_w["is"][0], fam_w["oos"][1]))
    add_indicators(D, ann=ANN_DUKA)
    add_indicators(H4)
    mt = month_table(D, usable_months)
    per = _periods_ms(fam_w)
    counts = Counts()
    rows: list[dict] = []
    for name in names:
        F = D if VARIANTS[name]["bars"] == "1d" else H4
        rows += a_variant_rows(name, F, D, mt, cfg, per, counts, same_seg=True, info=info,
                               sig_of=daily_sig_lookup(D, F, same_seg=True))
    finfo["gap_lookback"] = {"1d": _gap_lookback(D), "4h": _gap_lookback(H4)}
    finfo["months_end_ok"] = sum(1 for r in mt.values() if r["end_ok"])
    finfo["months_usable"] = sum(1 for r in mt.values() if r["usable"])
    rep = family_cells(rows, counts, names, cfg, fam_w)
    rep["counts"] = counts.as_json()
    return rep, rows, {"frames": finfo, "D": D, "H4": H4}


def _gap_lookback(F: Mapping[str, Any]) -> int:
    """BOŞLUK_GERİ_BAKIŞ: ilk segment dışındaki segmentlerde ısınmada kalan bar sayısı."""
    return int(sum(min(WARMUP, b - a + 1) for a, b in F["segs"][1:]))


def run_family_b(h1: pd.DataFrame, d1: pd.DataFrame, cfg: L.LabConfig, fam_w: Mapping[str, Any], *, symbol: str = PAXG,
                 names: Iterable[str] = B_NAMES, info: bool = True, funding: dict | None = None,
                 split: bool = True) -> tuple[dict, list[dict], dict]:
    """Aile B (PAXG 1h + 1d): hafta sonu tablosu, 4 varyant, hüküm hücreleri (split=False: tek dönem 'ALL', bilgi satırı)."""
    H = binance_frame(h1, "1h", symbol=symbol)
    Dd = add_indicators(binance_frame(d1, "1d", symbol=symbol), ann=ANN_BINANCE)
    if split:
        per = _periods_ms(fam_w)
        start, end = per["IS"][0], per["OOS"][1]
    else:
        start, end = win_ms(*fam_w["all"])
        per = {"ALL": (start, end)}
    weeks = weekend_table(H, Dd, start, end)
    counts = Counts()
    rows: list[dict] = []
    for name in names:
        rows += b_variant_rows(name, H, Dd, weeks, cfg, per, counts, symbol=symbol, funding=funding, info=info and split)
    winfo = {"weeks": len(weeks), "holiday_weekends": dict(Counter(w["holiday"] for w in weeks if w["holiday"])),
             "segments_1h": len(H["segs"]), "segments_1d": len(Dd["segs"])}
    if not split:
        return {"counts": counts.as_json()}, rows, {"weeks": winfo}
    rep = family_cells(rows, counts, names, cfg, fam_w)
    rep["counts"] = counts.as_json()
    rep["weeks"] = winfo
    return rep, rows, {"weeks": winfo}


# ---------------------------------------------------------------------------- görülmüş ve mekân satırları (bilgi)
def info_rows_a(F: Mapping[str, Any], D: Mapping[str, Any], cfg: L.LabConfig, *, same_seg: bool, names: Iterable[str] = A_NAMES,
                usable: set[int] | None = None) -> tuple[list[dict], Counts]:
    """Aile A bölünmeden (tek dönem 'ALL'): gerçek + hüküm plasebosu (bilgi plasebosu yok)."""
    per = {"ALL": (-(2 ** 62), 2 ** 62)}
    mt = month_table(D, usable)
    counts = Counts()
    rows: list[dict] = []
    for name in names:
        FF = D if VARIANTS[name]["bars"] == "1d" else F
        rows += a_variant_rows(name, FF, D, mt, cfg, per, counts, same_seg=same_seg, info=False)
    return rows, counts


def summarize_info(rows: list[dict], counts: Counts, names: Iterable[str], cfg: L.LabConfig, *, series: str, label: str,
                   key: str = "r") -> list[dict]:
    out = []
    for name in names:
        for cell in CELLS:
            real, pl, _ = cell_rows(rows, name, cell)
            ja = judge_all(real, pl, cfg, key)
            jf = judge_all(real, pl, cfg, "r_fon") if key == "r" else None
            sides = (LONG, SHORT) if cell == BOTH_CELL else (cell,)
            fr = [float(e["funding_r"]) for e in real if e.get("funding_r") is not None]
            out.append({"series": series, "label": label, "name": name, "cell": cell, **ja,
                        "fon": (jf or {}).get("all"), "n_unknown_net": sum(1 for e in real if key == "r_net" and e.get("r_net") is None),
                        "funding_r_mean": round(float(np.mean(fr)), 4) if fr else None,
                        "counts": counts.get(FAMILY, name, sides), "note": ""})
    return out


def venue_frame_a(Fs: Mapping[str, Any], Fv: Mapping[str, Any]) -> dict[str, Any]:
    """Vadeli işlem serisi + aynı açılış zaman damgalı spot sinyal verileri (ATR, kanal, mom28, ısınma, kapanış)."""
    pos = np.searchsorted(Fs["ts"], Fv["ts"])
    ok = (pos < Fs["n"]) & (Fs["ts"][np.minimum(pos, max(Fs["n"] - 1, 0))] == Fv["ts"]) if Fs["n"] else np.zeros(Fv["n"], dtype=bool)
    p = np.minimum(pos, max(Fs["n"] - 1, 0))
    V = dict(Fv)
    for k in ("atr", "hi10", "lo10", "hi20", "lo20", "hi55", "lo55", "mom28", "sma200", "sig"):
        V[k] = np.where(ok, Fs[k][p], np.nan) if Fs["n"] else np.full(Fv["n"], np.nan)
    V["sclose"] = np.where(ok, Fs["close"][p], np.nan) if Fs["n"] else np.full(Fv["n"], np.nan)
    V["segpos"] = np.where(ok, Fs["segpos"][p], -1) if Fs["n"] else np.full(Fv["n"], -1)
    V["spot_idx"] = np.where(ok, p, -1)
    return V


def venue_rows_a(name: str, Fs: Mapping[str, Any], Ds: Mapping[str, Any], V: Mapping[str, Any], Dv: Mapping[str, Any], cfg: L.LabConfig,
                 counts: Counts, funding: dict | None) -> list[dict]:
    """Aile A mekân: sinyal PAXG spot'tan, işlem vadeli barında; gerçek fonlama."""
    v = VARIANTS[name]
    per = {"ALL": (-(2 ** 62), 2 ** 62)}
    pname = PLACEBO_PREFIX + name
    FF = Dv if v["bars"] == "1d" else V
    idx = {int(t): k for k, t in enumerate(FF["ts"])}
    if v["entry"] in MONTHLY:
        sigs, cands, _ = monthly_signals(name, Ds, month_table(Ds))
        mtv = month_table(Dv)
        last_v = max(mtv) if mtv else None

        def remap(gi_s: int, m: int) -> tuple[int, dict] | None:
            gv = idx.get(int(Ds["ts"][gi_s]))
            if gv is None:
                return None
            nxt = mtv.get(m + 1)
            if nxt is None:
                return (gv, {"kind": "month_end", "bars": MONTH_SAFETY_BARS}) if m == last_v else None
            if not nxt["usable"] or nxt["first"] != gv + 1 or Dv["seg"][nxt["first"]] != Dv["seg"][gv]:
                return None
            return gv, {"kind": "month_end", "bars": int(nxt["n"])}
        evs, cmap = [], []
        for gi, sd, st, _sp, m in sigs:
            r = remap(gi, m)
            if r is None:
                counts.skip(FAMILY, name, sd, "MEKÂN_BAR_YOK")
                continue
            evs.append((r[0], sd, st, r[1], sd, m))
        for gi, _sp, m in cands:
            r = remap(gi, m)
            if r is not None:
                cmap.append((r[0], r[1], m))
        real = _run_events(Dv, evs, cfg, counts, per, family=FAMILY, name=name, funding=funding)
        pl = monthly_placebos(name, [(e[0], e[1], e[2], e[3], e[5]) for e in evs], cmap, Dv)
        return real + _run_events(Dv, pl, cfg, counts, per, family=PLACEBO_FAMILY, name=pname, funding=funding)
    Fsig = Ds if v["bars"] == "1d" else Fs
    sigs = rule_signals(name, Fsig, Ds, same_seg=False)
    evs = []
    for gi, sd, st, sp in sigs:
        gv = idx.get(int(Fsig["ts"][gi]))
        if gv is None:
            counts.skip(FAMILY, name, sd, "MEKÂN_BAR_YOK")
            continue
        evs.append((gv, sd, st, sp, sd))
    real = _run_events(FF, evs, cfg, counts, per, family=FAMILY, name=name, funding=funding)
    pl = matched_placebos(name, real, FF, per, counts)
    return real + _run_events(FF, pl, cfg, counts, per, family=PLACEBO_FAMILY, name=pname, funding=funding)


def apply_notes(report: dict[str, Any]) -> None:
    """'mekânda tutmadı': STANDART hükmü GÜÇLÜ ADAY hücre, bir vadeli satırında n ≥ 20 ve ort. net R ≤ 0."""
    cells = {(c["name"], c["cell"]): c for fam in ("A", "B") for c in ((report.get(fam) or {}).get("cells") or [])}
    ven = report.get("venue")
    for c in cells.values():
        c["note"] = ""
    if not ven:
        return
    for r in ven.get("rows") or []:
        c = cells.get((r["name"], r["cell"]))
        st = r.get("all") or {}
        r["note"] = NOTE_VENUE if (c and c["verdict"] == L.V_STRONG and int(st.get("n") or 0) >= G.VENUE_MIN_N
                                   and st.get("mean_r") is not None and float(st["mean_r"]) <= 0) else ""
        if r["note"] and c:
            c["note"] = NOTE_VENUE
    ven["notes"] = [r for r in ven.get("rows") or [] if r["note"]]
    ven["notes_evaluated"] = bool(cells)


# ---------------------------------------------------------------------------- sonuç
def _num(x: Any, nd: int = 2) -> str:
    return "—" if x is None else f"{float(x):.{nd}f}"


def venue_effect(report: Mapping[str, Any], name: str, cell: str) -> list[dict[str, Any]]:
    """Hücrenin vadeli mekân satırları: n, brüt ve net (gerçek fonlamalı) ort. R, ortalama gerçek fonlama (R)."""
    out = []
    for r in ((report.get("venue") or {}).get("rows") or []):
        if r.get("name") != name or r.get("cell") != cell:
            continue
        st, g = r.get("all") or {}, r.get("gross") or {}
        out.append({"series": r.get("series"), "n": int(st.get("n") or 0), "gross_mean_r": g.get("mean_r"), "net_mean_r": st.get("mean_r"),
                    "funding_r_mean": r.get("funding_r_mean"), "note": r.get("note") or ""})
    return out


def recommendation_detail(report: Mapping[str, Any], c: Mapping[str, Any]) -> str:
    """Öneri satırının içeriği (belge 'Öneri kuralı'): %95 aralık, en derin düşüş, vadeli mekândaki gerçek fonlama etkisi, kazanan
    laneti notu."""
    mon = c.get("monthly") or {}
    ddr = (c.get("higher_risk") or {}).get("drawdown_r") or {}
    dd_all = None if ddr.get("all") is None else float(ddr["all"]) * G.RISK_PCT
    dd_oos = None if ddr.get("oos") is None else float(ddr["oos"]) * G.RISK_PCT
    ven = venue_effect(report, c["name"], c["cell"])
    vtxt = "; ".join(f"{v['series']}: n {v['n']}, ort.R brüt {_fmt(v['gross_mean_r'], 3)} → net {_fmt(v['net_mean_r'], 3)} (gerçek "
                     f"fonlama ort. {_fmt(v['funding_r_mean'], 3)} R)" for v in ven) or "satır yok"
    return (f"doğrulama ort. aylık {_fmt(mon.get('mean_pct_month'))}% (R_fon, %0,5 risk) · %95 aralık {_ci(mon.get('ci95_pct_month'))} · "
            f"≥ +%1 ay payı {_num(mon.get('share_months_ge_target'))} · en derin düşüş (birikimli R_fon, bileşiksiz) %0,5 riskte bütün "
            f"hüküm serisi %{_num(dd_all, 1)}, doğrulama %{_num(dd_oos, 1)} · vadeli mekân (gerçek fonlama; hüküm DEĞİL): {vtxt} · "
            f"{GOLD_V2_REGISTRY['higher_risk']['curse_tr']}; ulaşılacağı garanti değildir")


def conclusion(report: Mapping[str, Any]) -> dict[str, Any]:
    """Öneri kuralı (belgenin 'Sonuç ve sonraki adım'ı). Bir şart ölçülemiyorsa (ailenin plasebo aday oranı tanımsız ya da
    hücrenin vadeli mekân satırı yok) tutmuş SAYILMAZ: öneri yapılmaz ve nedeni açıkça yazılır."""
    lines, rec = [], []
    fam_status = {f: (report.get(f) or {}).get("status") for f in ("A", "B")}
    for f in ("A", "B"):
        st = fam_status[f]
        if st and st != "koşuldu":
            lines.append(f"Aile {f}: {st} — " + ("PAXG/XAUUSDT satırları onun yerine geçmez" if f == "A" else "hüküm yok"))
    cells = [c for f in ("A", "B") for c in ((report.get(f) or {}).get("cells") or [])]
    main_pass = {f: any(c["main"] and c["verdict_strict"] == L.V_STRONG for c in cells if c["family"] == f) for f in ("A", "B")}
    for c in cells:
        fam = report.get(c["family"]) or {}
        prate = (fam.get("candidate_rate") or {}).get("placebo")
        ok_pl = prate is not None and prate == 0
        if c["main"] and c["verdict_strict"] == L.V_STRONG:
            ok_ven = bool(venue_effect(report, c["name"], c["cell"]))
            if c["meets_target"] and ok_pl and ok_ven and not c.get("note"):
                rec.append(c)
                lines.append(f"ÖNERİ (yalnız PAPER, yalnız-kayıt; sahip onayı olmadan hiçbir şey açılmaz): {c['name']} {c['cell']} — "
                             + recommendation_detail(report, c))
            elif c["meets_target"]:
                why = []
                if prate is None:
                    why.append(f"aile {c['family']} hüküm plasebo aday oranı hesaplanamadı (plasebo hücrelerinin hepsi VERİ AZ): şart "
                               "(iii) ölçülemedi")
                elif not ok_pl:
                    why.append(f"aile {c['family']} plasebo aday oranı {prate}")
                if not ok_ven:
                    why.append("hücrenin vadeli mekân satırı yok (venue bölümü koşulmadı ya da vadeli seri boş): şart (iv) ve mekân fonlama etkisi "
                               "ölçülemedi")
                if c.get("note"):
                    why.append(c["note"])
                lines.append(f"{c['name']} {c['cell']}: hedefi karşılıyor ama öneri şartı tutmadı ({'; '.join(why)})")
            else:
                lines.append(f"{c['name']} {c['cell']}: kenar var, hedefin altında")
        elif not c["main"] and L.V_STRONG in (c["verdict_strict"], c["verdict"]):
            if c["family"] == "B" and not main_pass["B"]:
                ctx = "B_WKND_REV_ALL İKİ YÖN sıkı GÜÇLÜ ADAY değilken: tarama yapıntısı, kanıt değil"
            elif c["family"] == "A" and not main_pass["A"]:
                ctx = "A'nın ANA hücreleri sıkı GÜÇLÜ ADAY değilken: kanıt değil"
            else:
                ctx = "ikincil hücreden tek aday 56 altın hücresi içinde zayıf kanıttır"
            vd = "sıkı GÜÇLÜ ADAY" if c["verdict_strict"] == L.V_STRONG else f"standart GÜÇLÜ ADAY (sıkı: {c['verdict_strict']})"
            tgt = "hedefi karşılıyor" if c["meets_target"] else "hedefin altında"
            lines.append(f"{c['name']} {c['cell']} (ikincil): {vd}, {tgt} — {ctx}; yalnız yeni bir ön kayıt (gold_v3) gerekir; öneri DEĞİL")
    if not rec and not any(c["meets_target"] for c in cells):
        lines.append(NO_TARGET_TR)
    return {"lines": lines, "recommend": [f"{c['name']} {c['cell']}" for c in rec]}


# ---------------------------------------------------------------------------- sentetik ayar (calib)
def synthetic_duka_hourly(start: str, end: str, seed: int, *, vol: float = 0.0015) -> pd.DataFrame:
    """Dukascopy benzeri saatlik rastgele yürüyüş (gerçek fiyat DEĞİL): Pazar 18:00 – Cuma 17:00 NY, her gün 17–18 NY kapalı."""
    a, b = win_ms(start, end)
    ts = np.arange(a, b, HOUR_MS, dtype=np.int64)
    loc = pd.to_datetime(ts, unit="ms", utc=True).tz_convert(TZ_NY)
    hr, wd = loc.hour.to_numpy(), loc.weekday.to_numpy()
    open_ = ((wd <= 3) & (hr != 17)) | ((wd == 6) & (hr >= 18)) | ((wd == 4) & (hr < 17))
    return _walk(ts[open_], seed, vol)


def synthetic_paxg_hourly(start: str, end: str, seed: int, *, vol: float = 0.0015) -> tuple[pd.DataFrame, pd.DataFrame]:
    """PAXG benzeri 7/24 saatlik rastgele yürüyüş ve UTC günlük barları (gerçek fiyat DEĞİL)."""
    a, b = win_ms(start, end)
    h = _walk(np.arange(a, b, HOUR_MS, dtype=np.int64), seed, vol)
    d = h.assign(day=h["timestamp"] // DAY_MS).groupby("day").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                                                  close=("close", "last"), volume=("volume", "sum")).reset_index()
    d["timestamp"] = d["day"].astype(np.int64) * DAY_MS
    return h, d[["timestamp", "open", "high", "low", "close", "volume"]]


def _walk(ts: np.ndarray, seed: int, vol: float, sub: int = 4) -> pd.DataFrame:
    rnd = np.random.default_rng(seed)
    n = len(ts)
    path = 1000.0 * np.exp(np.cumsum(rnd.normal(0, vol / math.sqrt(sub), n * sub))).reshape(n, sub)
    o = np.r_[1000.0, path[:-1, -1]] if n else np.zeros(0)
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, path.max(1)) if n else o,
                         "low": np.minimum(o, path.min(1)) if n else o, "close": path[:, -1] if n else o,
                         "volume": rnd.uniform(1, 10, n)})


def _calib_task(args: tuple) -> dict[str, Any]:
    k, cfg_d, win = args
    cfg = L.LabConfig(**cfg_d)
    seed = CALIB_SEED + int(k)
    wa = {**WINDOWS["A"], **(win.get("A") or {})}
    wb = {**WINDOWS["B"], **(win.get("B") or {})}
    ca, cb = win["calib"]["a"], win["calib"]["b"]
    ra, _, _ = run_family_a(synthetic_duka_hourly(ca[0], ca[1], seed), cfg, wa, usable_months=None, info=False)
    h, d = synthetic_paxg_hourly(cb[0], cb[1], seed + 10_000)
    rb, _, _ = run_family_b(h, d, cfg, wb, info=False)
    out = {"world": int(k), "seed": seed}
    for f, r in (("A", ra), ("B", rb)):
        cr = r["candidate_rate"]
        out[f] = {"cells": len(r["cells"]), "strong": cr["strong"], "strict_strong": cr["strict_strong"], "tested": cr["real_tested"],
                  "candidates": cr["candidates"], "placebo_candidates": cr["placebo_candidates"], "placebo_tested": cr["placebo_tested"],
                  "weak": sum(1 for c in r["cells"] if c["verdict"] == L.V_WEAK)}
    return out


def calibration(cfg: L.LabConfig, windows: Mapping[str, Any], *, worlds: int = CALIB_WORLDS, jobs: int = 1) -> dict[str, Any]:
    """Bu 24 hücrenin yapısıyla sentetik rastgele yürüyüşlerde tesadüfi GÜÇLÜ ADAY oranı (bilgi; gerçek veriye dokunmaz)."""
    res = G._map(_calib_task, [(k, dataclasses.asdict(cfg), windows) for k in range(int(worlds))], jobs)
    tot = {f: {k: sum(r[f][k] for r in res) for k in ("cells", "strong", "strict_strong", "tested", "candidates", "placebo_candidates",
                                                       "placebo_tested", "weak")} for f in ("A", "B")}
    cells = sum(t["cells"] for t in tot.values())
    strong = sum(t["strong"] for t in tot.values())
    strict = sum(t["strict_strong"] for t in tot.values())
    return {"worlds": int(worlds), "seed": CALIB_SEED, "per_world": res, "totals": tot,
            "strong_rate_per_cell": round(strong / cells, 5) if cells else None,
            "strict_rate_per_cell": round(strict / cells, 5) if cells else None,
            "expected_per_24": round(24 * strong / cells, 4) if cells else None,
            "tr": "sentetik rastgele yürüyüş (kenar YOK): hücre başına tesadüfi GÜÇLÜ ADAY oranı; bilgi"}


# ---------------------------------------------------------------------------- çalıştırma
SECTIONS = ("A", "B", "seen", "venue", "calib")
REPORT_JSON, REPORT_MD = "gold_lab_v2_report.json", "gold_lab_v2_report.md"
EVENT_COLS = ["section", "symbol", "tf", "family", "name", "side", "cell", "i", "t_ms", "ts_i", "period", "seg", "entry_ms", "exit_ms",
              "entry_px", "stop", "risk_px", "r", "r_fon", "n8", "cost_r", "exit_reason", "exit_kind", "hold", "max_bars", "funding_r",
              "r_net", "sig_ann"]


def events_file(section: str) -> str:
    return f"gold_lab_v2_events_{section}.csv.gz"


def write_events(path: Path, rows: list[dict]) -> None:
    tmp = path.with_name(path.name + ".part")
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(EVENT_COLS)
        for e in rows:
            w.writerow([e.get(c) for c in EVENT_COLS])
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _merge(base: Mapping[str, Any], over: Mapping[str, Any] | None) -> dict[str, Any]:
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, Mapping) and isinstance(out.get(k), Mapping) else v
    return out


def prior_report(out_dir: Path | str, cfg_d: Mapping[str, Any], windows: Mapping[str, Any]) -> dict[str, Any] | None:
    """Aynı mühür, ayar ve pencereli önceki rapor (yeni bölümler onunla birleşir); farklıysa GoldDataError."""
    p = Path(out_dir) / REPORT_JSON
    if not p.exists():
        return None
    try:
        rep = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GoldDataError(f"{p}: önceki rapor okunamadı ({exc}); başka bir --out seçin ya da dosyayı kaldırın") from exc
    why = []
    if not isinstance(rep, dict) or rep.get("kind") != "GOLD_LAB_V2":
        why.append("gold_v2 raporu değil")
    else:
        if rep.get("registry_sha") != GOLD_V2_REGISTRY_SHA:
            why.append(f"mühür {rep.get('registry_sha')} ≠ {GOLD_V2_REGISTRY_SHA}")
        if rep.get("config") != G._as_json(cfg_d):
            why.append("laboratuvar ayarı farklı")
        if rep.get("windows") != G._as_json(windows):
            why.append("veri pencereleri farklı")
    if why:
        raise GoldDataError(f"{p}: önceki rapor bu koşuyla birleştirilemez ({'; '.join(why)}); başka bir --out seçin")
    return rep


def run(*, sections: Iterable[str], cache_dir: Path | str, out_dir: Path | str, cfg: L.LabConfig | None = None,
        fetch: Callable[[str], bytes | None] | None = None, offline: bool = False, jobs: int = 1, duka_root: Path | str | None = None,
        duka_log: Path | str | None = None, now_ms: int | None = None, log: Callable[[str], None] = print,
        data_windows: Mapping[str, Any] | None = None, calib_worlds: int = CALIB_WORLDS) -> dict[str, Any]:
    """Bölümleri koşar; <out>/gold_lab_v2_report.json + .md ve bölüm başına olay dosyası yazar. Aynı mühür/ayar/pencereli önceki
    rapor varsa yeni bölümler onunla birleşir. `data_windows` yalnız testler içindir (rapora `windows_overridden` yazılır)."""
    cfg = cfg or L.LabConfig()
    sections = list(sections)
    bad = [s for s in sections if s not in SECTIONS + ("all",)]
    secs = list(SECTIONS) if "all" in sections else [s for s in SECTIONS if s in sections]
    if bad or not secs:
        raise ValueError(f"bilinmeyen bölüm: {', '.join(bad) or '(boş)'} (geçerli: {', '.join(SECTIONS)}, all)")
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    win = _merge(WINDOWS, data_windows)
    overridden = win != json.loads(json.dumps(WINDOWS))
    out_dir = Path(out_dir)
    cfg_d = dataclasses.asdict(cfg)
    prior = prior_report(out_dir, cfg_d, win)
    cache = G.ArchiveCache(cache_dir, fetch=fetch, offline=offline, now_ms=now_ms)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    report: dict[str, Any] = {k: v for k, v in (prior or {}).items() if k not in secs}
    report.update({"kind": "GOLD_LAB_V2", "version": GOLD2_VERSION, "registry_sha": GOLD_V2_REGISTRY_SHA, "doc": GOLD2_DOC,
                   "config": cfg_d, "cost_round_trip_pct": round(2 * cfg.cost_per_side * 100, 3), "generated_at": _iso(now_ms),
                   "windows": win, "windows_overridden": overridden, "readings_tr": list(READINGS_TR),
                   "disclosure_tr": list(DISCLOSURE_TR), "limits_tr": list(LIMITS_TR),
                   "note_tr": "Geçmiş test (PAPER değil, canlı değil); kâr garantisi değildir. Hüküm yalnız aile A (Dukascopy "
                              "2006–2020) ve aile B (PAXG spot) hüküm hücrelerinden; görülmüş, mekân, SHORT, bilgi plaseboları ve C "
                              "bilgidir."})
    runs: dict[str, Any] = dict(report.get("section_runs") or {})
    ev_by: dict[str, list[dict]] = {s: [] for s in secs}
    spot: dict[str, pd.DataFrame] = {}

    def need_spot(tfs: Iterable[str]) -> None:
        a, b = win_ms(*win["seen"]["paxg"])
        for tf in tfs:
            if tf in spot:
                continue
            df, q = G.load_spot(PAXG, tf, a, b, cache.get)
            log(f"veri spot {PAXG} {tf}: {len(df)} bar · eksik {q.get('missing_ratio')} · 24s+ boşluk {q['n_gaps_over_24h']}")
            if not len(df):
                raise GoldDataError(f"spot {PAXG} {tf} serisi boş")
            spot[tf] = df
            report.setdefault("data", {})[f"spot {PAXG} {tf}"] = q

    if "A" in secs:
        ts0 = time.time()
        report["A"] = section_a(cfg, win, duka_root=duka_root, duka_log=duka_log, log=log)
        ev_by["A"] = report["A"].pop("_rows", [])
        runs["A"] = {"generated_at": _iso(now_ms), "seconds": round(time.time() - ts0, 1)}
    if "B" in secs:
        ts0 = time.time()
        need_spot(["1h", "1d"])
        fam_w = win["B"]
        a, b = win_ms(fam_w["is"][0], fam_w["oos"][1])
        h1 = spot["1h"][(spot["1h"]["timestamp"] >= a) & (spot["1h"]["timestamp"] < b)].reset_index(drop=True)
        rep, rows, _ = run_family_b(h1, spot["1d"], cfg, fam_w)
        rep["status"] = "koşuldu"
        report["B"] = rep
        ev_by["B"] = rows
        log(f"aile B: {sum(1 for e in rows if e['family'] == FAMILY)} işlem")
        runs["B"] = {"generated_at": _iso(now_ms), "seconds": round(time.time() - ts0, 1)}
    if "seen" in secs:
        ts0 = time.time()
        need_spot(["4h", "1d"])
        report["seen"], ev_by["seen"] = section_seen(cfg, win, spot, duka_root=duka_root, duka_log=duka_log, log=log)
        runs["seen"] = {"generated_at": _iso(now_ms), "seconds": round(time.time() - ts0, 1)}
    if "venue" in secs:
        ts0 = time.time()
        need_spot(["1h", "4h", "1d"])
        report["venue"], ev_by["venue"] = section_venue(cfg, win, spot, cache, now_ms, log=log)
        runs["venue"] = {"generated_at": _iso(now_ms), "seconds": round(time.time() - ts0, 1)}
    if "calib" in secs:
        ts0 = time.time()
        report["calib"] = calibration(cfg, win, worlds=calib_worlds, jobs=jobs)
        log(f"sentetik ayar: {report['calib']['worlds']} dünya · hücre başına GÜÇLÜ ADAY {report['calib']['strong_rate_per_cell']}")
        runs["calib"] = {"generated_at": _iso(now_ms), "seconds": round(time.time() - ts0, 1)}
    apply_notes(report)
    allc = [c for f in ("A", "B") for c in ((report.get(f) or {}).get("cells") or [])]
    allp = [c for f in ("A", "B") for c in ((report.get(f) or {}).get("placebo_cells") or [])]
    report["candidate_rate_total"] = {"real": cand_rate(allc), "placebo": cand_rate(allp), "cells": len(allc),
                                      "strict_strong": sum(1 for c in allc if c["verdict_strict"] == L.V_STRONG)}
    report["conclusion"] = conclusion(report)
    report["sections"] = [s for s in SECTIONS if s in report]
    report["section_runs"] = runs
    report["seconds"] = round(time.time() - t0, 1)
    report["archive_requests"] = cache.requests
    report = G._clean(report)
    for sec in secs:
        write_events(out_dir / events_file(sec), [{**e, "section": sec} for e in ev_by[sec]])
    _write_text(out_dir / REPORT_JSON, json.dumps(report, ensure_ascii=False, indent=1))
    _write_text(out_dir / REPORT_MD, render_md(report))
    return report


def section_a(cfg: L.LabConfig, win: Mapping[str, Any], *, duka_root: Path | str | None, duka_log: Path | str | None,
              log: Callable[[str], None] = print) -> dict[str, Any]:
    """Aile A: ayna hazır → anlık görüntü → oku/temizle → zaman damgası ve kapsama denetimi → (geçerse) 8 varyant → anlık görüntü
    yeniden (değişmişse GoldDataError)."""
    if duka_root is None:
        raise GoldDataError("aile A için Dukascopy aynası gerekli (--duka-root)")
    root = Path(duka_root)
    ready = mirror_ready(root, duka_log)
    fam_w = win["A"]
    a, b = win_ms(fam_w["is"][0], fam_w["oos"][1])
    mi0, mi1 = mi_parse(fam_w["is"][0][:7]), mi_parse(fam_w["oos"][1][:7])
    man = read_manifest(root)
    planned = [str(duka_hour_path(root, m)) for m in range(mi0, mi1 + 1) if duka_hour_path(root, m).is_file()]
    planned += [str(duka_day_path(root, y)) for y in range(mi0 // 12, mi1 // 12 + 1) if duka_day_path(root, y).is_file()]
    snap = mirror_snapshot(root, planned)
    hourly, hinfo = load_duka_hourly(root, mi0, mi1, a, b, man=man)
    per = _periods_ms(fam_w)
    out: dict[str, Any] = {"mirror": {**ready, "snapshot": dict(snap),
                                      "manifest_status": man.get("status_counts")},
                           "data": {"months": hinfo["months"], "years": hinfo["years"], "hours_kept": int(len(hourly)),
                                    "usable_months": len(hinfo["usable_months"])}}
    tsc = week_open_check(hourly["timestamp"].to_numpy(dtype=np.int64), per)
    cov = coverage_check(hinfo["months"], hourly["timestamp"].to_numpy(dtype=np.int64),
                         {"IS": tuple(fam_w["is"]), "OOS": tuple(fam_w["oos"])})
    out["data"]["timestamp_check"] = tsc
    out["data"]["coverage"] = cov
    rows: list[dict] = []
    if not tsc["pass"]:
        out["status"] = "yapılamadı (zaman damgası)"
    elif not cov["pass"]:
        out["status"] = "yapılamadı (kapsama)"
    else:
        usable = {mi_parse(k) for k in hinfo["usable_months"]}
        rep, rows, extra = run_family_a(hourly, cfg, fam_w, usable_months=usable)
        D = extra["D"]
        loaded = {mi_parse(k) for k, r in hinfo["months"].items() if r["decoded"]}
        kd = partial_days(D, loaded)
        extra["frames"].update({"partial_days": len(kd), "partial_list": kd[:100]})
        mclose = {m: float(D["close"][r["last"]]) for m, r in month_table(D).items()}
        extra["frames"]["day_file"] = day_file_info(root, range(mi0 // 12, mi1 // 12 + 1), mclose)
        out.update(rep)
        out["data"]["frames"] = extra["frames"]
        out["status"] = "koşuldu"
    check_snapshot(root, snap)
    out["mirror"]["snapshot_end"] = "aynı"
    log(f"aile A: {out['status']} · {sum(1 for e in rows if e['family'] == FAMILY)} işlem")
    out["_rows"] = rows
    return out


def section_seen(cfg: L.LabConfig, win: Mapping[str, Any], spot: Mapping[str, pd.DataFrame], *, duka_root: Path | str | None,
                 duka_log: Path | str | None, log: Callable[[str], None] = print) -> tuple[dict[str, Any], list[dict]]:
    """Aile A görülmüş satırları (bilgi): PAXG spot 1d/4h ve Dukascopy 2020-08 → 2026-09; dönem bölünmez."""
    Ds = add_indicators(binance_frame(spot["1d"], "1d", symbol=PAXG), ann=ANN_BINANCE)
    F4 = add_indicators(binance_frame(spot["4h"], "4h", symbol=PAXG))
    rows, counts = info_rows_a(F4, Ds, cfg, same_seg=False)
    out = {"label_tr": "görülmüş/mekân — hüküm DEĞİL", "rows": summarize_info(rows, counts, A_NAMES, cfg, series=f"{PAXG} spot",
                                                                            label="görülmüş")}
    allrows = [{**e, "series": "paxg"} for e in rows]
    if duka_root is not None:
        root = Path(duka_root)
        mirror_ready(root, duka_log)
        a, b = win_ms(*win["seen"]["dukascopy"])
        mi0, mi1 = mi_parse(win["seen"]["dukascopy"][0][:7]), mi_parse(win["seen"]["dukascopy"][1][:7])
        planned = [str(duka_hour_path(root, m)) for m in range(mi0, mi1 + 1) if duka_hour_path(root, m).is_file()]
        snap = mirror_snapshot(root, planned)
        hourly, hinfo = load_duka_hourly(root, mi0, mi1, a, b)
        out["dukascopy_data"] = {"usable_months": len(hinfo["usable_months"]), "months": mi1 - mi0 + 1, "hours_kept": int(len(hourly)),
                                 "years": hinfo["years"]}
        if len(hourly):
            D, H4, finfo = duka_frames(hourly, d_range=tuple(win["seen"]["dukascopy"]))
            add_indicators(D, ann=ANN_DUKA)
            add_indicators(H4)
            r2, c2 = info_rows_a(H4, D, cfg, same_seg=True, usable={mi_parse(k) for k in hinfo["usable_months"]})
            out["rows"] += summarize_info(r2, c2, A_NAMES, cfg, series="XAUUSD Dukascopy", label="görülmüş")
            out["dukascopy_data"]["frames"] = finfo
            allrows += [{**e, "series": "dukascopy"} for e in r2]
        else:
            out["dukascopy_data"]["status"] = "yapılamadı (veri yok)"
        check_snapshot(root, snap)
    else:
        out["dukascopy_data"] = {"status": "koşmadı (--duka-root yok)"}
    log(f"görülmüş satırlar: {len(out['rows'])}")
    return out, allrows


def section_venue(cfg: L.LabConfig, win: Mapping[str, Any], spot: Mapping[str, pd.DataFrame], cache: G.ArchiveCache, now_ms: int, *,
                  log: Callable[[str], None] = print) -> tuple[dict[str, Any], list[dict]]:
    """Vadeli mekân satırları (bilgi): A (sinyal PAXG spot'tan) ve B (kendi barlarıyla), gerçek fonlama, net R."""
    Ds = add_indicators(binance_frame(spot["1d"], "1d", symbol=PAXG), ann=ANN_BINANCE)
    F4 = add_indicators(binance_frame(spot["4h"], "4h", symbol=PAXG))
    out: dict[str, Any] = {"label_tr": "mekân — hüküm DEĞİL; net R = R + funding_r (gerçek fonlama)", "rows": [], "data": {}}
    allrows: list[dict] = []
    for sym, (a_, b_) in win["venue"].items():
        a, b = win_ms(a_, b_)
        fund = G.load_funding(sym, a, b, cache.get)
        out["data"][f"funding {sym}"] = {"rows": fund["rows"], "missing_months": fund["missing_months"]}
        raw = {"f_t": fund["f_t"], "f_rate": fund["f_rate"]}
        fr = {}
        for tf in ("1h", "4h", "1d"):
            df, q = G.load_futures(sym, tf, a, b, cache.get, now_ms)
            out["data"][f"futures {sym} {tf}"] = q
            log(f"veri vadeli {sym} {tf}: {len(df)} bar")
            fr[tf] = df
        if not all(len(fr[t]) for t in fr):
            out["data"][f"futures {sym}"] = "yapılamadı (seri boş)"
            continue
        Dv = binance_frame(fr["1d"], "1d", symbol=sym)
        Dv_al = venue_frame_a(Ds, Dv)
        F4v = venue_frame_a(F4, binance_frame(fr["4h"], "4h", symbol=sym))
        counts = Counts()
        rows: list[dict] = []
        for name in A_NAMES:
            rows += venue_rows_a(name, F4, Ds, F4v, Dv_al, cfg, counts, raw)
        rb, rows_b, _ = run_family_b(fr["1h"], fr["1d"], cfg, {"all": [a_, b_]}, symbol=sym, funding=raw, split=False)
        for r in rows_b:
            if r["family"] == FAMILY or r["family"] == PLACEBO_FAMILY:
                rows.append(r)
        cb = Counts()
        cb.d = {k: {**v, "mtm": []} for k, v in (rb.get("counts") or {}).items()}
        out["rows"] += summarize_info(rows, counts, A_NAMES, cfg, series=f"{sym} vadeli", label="mekân", key="r_net")
        out["rows"] += summarize_info(rows, cb, B_NAMES, cfg, series=f"{sym} vadeli", label="mekân", key="r_net")
        for r in out["rows"]:
            if r["series"] == f"{sym} vadeli":
                gross = judge_all(*cell_rows(rows, r["name"], r["cell"])[:2], cfg, "r")
                r["gross"] = gross["all"]
        allrows += rows
    return out, allrows


# ---------------------------------------------------------------------------- markdown
def _p(x: Any, nd: int = 2) -> str:
    return "—" if x is None else f"{x:+.{nd}f}"


def _ci(ci: Any) -> str:
    return f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "—"


def _cell_line(c: Mapping[str, Any]) -> str:
    mon = c.get("monthly") or {}
    vs = c.get("vs_placebo") or {}
    dci = (c.get("diff_ci") or {}).get("OOS")
    sh = mon.get("share_months_ge_target")
    fon = c.get("fon") or {}
    return (f"| {c['name']} | {c['cell']} | {c['IS'].get('n', 0)}/{c['OOS'].get('n', 0)} | {_p(c['IS'].get('mean_r'), 3)}/"
            f"{_p(c['OOS'].get('mean_r'), 3)} | {_p(fon.get('IS', {}).get('mean_r'), 3)}/{_p(fon.get('OOS', {}).get('mean_r'), 3)} | "
            f"{(_p(vs.get('IS')) + '/' + _p(vs.get('OOS'))) if vs else '—'} | {_ci(dci)} | {c['verdict']} | {c['verdict_strict']} | "
            f"{mon.get('months', 0)} | {_p(mon.get('mean_pct_month'))} | {_ci(mon.get('ci95_pct_month'))} | "
            f"{'—' if sh is None else f'%{100 * sh:.0f}'} | {'EVET' if c.get('meets_target') else 'hayır'} | "
            f"{(c.get('higher_risk') or {}).get('text', '')}{(' · ' + '; '.join(c['higher_risk'].get('flags') or [])) if (c.get('higher_risk') or {}).get('flags') else ''} |")


_CELL_HEAD = ["| varyant | hücre | işlem keşif/doğr. | ort.R keşif/doğr. | ort.R_fon keşif/doğr. | plaseboya göre | fark %95 doğr. | hüküm | "
              "sıkı | tam ay | aylık % (R_fon, %0,5) | %95 | ≥%1 ay | hedef | daha yüksek risk |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]


def _hr_detail(c: Mapping[str, Any]) -> list[str]:
    """'Daha yüksek risk' satırının sayıları (belge maddeleri 4–8); X yalnız sıkı GÜÇLÜ ADAY hücrelerde vardır."""
    hr = c.get("higher_risk") or {}
    if hr.get("X") is None:
        return []
    tor, ex, dd, lev = (hr.get(k) or {} for k in ("total_open_risk_pct", "excess", "drawdown_pct", "leverage"))
    mark = " — İŞARETLİ"
    return [f"- {c['name']} {c['cell']} — daha yüksek risk (bilgi; kenar değildir), X = %{hr['X']:.2f}: toplam açık risk = en çok "
            f"{tor.get('max_concurrent')} eşzamanlı işlem × X = %{_num(tor.get('value'))} (PAPER_RESEARCH tavanı %{OPEN_RISK_CAP:.1f}, "
            f"öğrenme modu %{OPEN_RISK_LEARNING:.1f}){mark if tor.get('flag') else ''} · plasebo üstü fazla: {ex.get('text', '—')}; aylık "
            f"fazla R {_num(ex.get('mean_r_month'), 3)}; yön kayması payı {_num(ex.get('drift_share'))} · en derin düşüş X riskte bütün "
            f"hüküm serisi %{_num(dd.get('all'), 1)}{mark if dd.get('flag') else ''}, doğrulama %{_num(dd.get('oos'), 1)} · ima edilen "
            f"kaldıraç medyan {_num(lev.get('median'))}, en çok {_num(lev.get('max'))}{mark if lev.get('over_paper') else ''} · "
            f"{hr.get('curse_tr', '')}"]


MONTH_SKIPS = ("SONRAKİ_AY_KULLANILAMAZ", "SONRAKİ_AY_YOK", "AY_KULLANILAMAZ", "ISINMA", "GERİ_BAKIŞ_AYI")


def _month_skip_lines(report: Mapping[str, Any]) -> list[str]:
    """Aylık varyantların karar atlamaları (bilgi; bütün hüküm serisi): m+1'in kullanılabilirliğine bağlı atlamalar dahil."""
    cnt = (report.get("A") or {}).get("counts") or {}
    out = []
    for name in A_NAMES:
        if VARIANTS[name]["entry"] not in MONTHLY:
            continue
        sk = (cnt.get(f"{FAMILY}|{name}|AY") or {}).get("skipped") or {}
        out.append(f"- {name} aylık karar atlamaları (bilgi; bütün hüküm serisi): " + " · ".join(f"{k} {int(sk.get(k, 0))}" for k in MONTH_SKIPS))
    if out:
        out.append("- SONRAKİ_AY_KULLANILAMAZ: m+1 ayı kullanılamadığı (kapsama, ay sonu şartı, bitişiklik ya da segment) için m ayında karar "
                   "verilmedi. Fiyat kullanılmaz ama karar anından sonraki verinin DURUMUNA bağlıdır; ön kayıtlıdır ve plaseboya aynısı "
                   "uygulanır.")
    return out


def render_md(report: Mapping[str, Any]) -> str:
    """Kısa, düz Türkçe rapor: önce ANA hücreler, sonra ikincil hücreler, sonra bilgi eki."""
    out = [f"# Altın laboratuvarı — {report.get('version')} raporu", "",
           f"Ön kayıt mührü `GOLD_V2_REGISTRY_SHA = {report.get('registry_sha')}` · belge `{report.get('doc')}` · bölümler: "
           f"{', '.join(report.get('sections') or [])} · gidiş-dönüş maliyet %{report.get('cost_round_trip_pct')}", "",
           "Geçmiş test; PAPER değil, canlı değil. Kâr garantisi değildir. Olumsuz sonuç olduğu gibi yazılır.", ""]
    if report.get("windows_overridden"):
        out += ["**UYARI: veri pencereleri ön kayıttan FARKLI (yalnız test için); bu rapor ön kayıtlı koşu DEĞİLDİR.**", ""]
    con = report.get("conclusion") or {}
    out += ["## Sonuç", ""] + [f"- {x}" for x in con.get("lines") or ["—"]] + [""]
    cells = [c for f in ("A", "B") for c in ((report.get(f) or {}).get("cells") or [])]
    for f in ("A", "B"):
        st = (report.get(f) or {}).get("status")
        if st and st != "koşuldu":
            out += [f"- Aile {f}: **{st}**.", ""]
    if cells:
        out += ["## ANA hücreler (önceden kayıtlı sorular)", ""] + _CELL_HEAD + [_cell_line(c) for c in cells if c["main"]] + [""]
        for c in cells:
            if c["main"]:
                mc = c.get("month_ci") or {}
                rr = c.get("monthly_r") or {}
                out.append(f"- {c['name']} {c['cell']}: ort.R'nin ay kümeli %95 aralığı (bilgi) keşif {_ci(mc.get('IS'))} · doğrulama "
                           f"{_ci(mc.get('OOS'))}; fonlamasız aylık % (bilgi) {_p(rr.get('mean_pct_month'))} {_ci(rr.get('ci95_pct_month'))}.")
                out += _hr_detail(c)
        out += ["", "Hüküm fonlamasız R ile (laboratuvar tanımı); hedef ve 'daha yüksek risk' yalnız R_fon ile (fonlama vekili 8 saatte "
                "%0,01). Ortalamanın %1'e tam yetmesi, gerçek ortalamanın %1'in altında olma olasılığının kabaca %50 olduğu anlamına "
                "gelir. Aylık varyantlarda karar ayına yazma, işlemi tutulduğu aydan bir ay ÖNCE raporlar.", ""]
        out += ["## İkincil hücreler", ""] + _CELL_HEAD + [_cell_line(c) for c in cells if not c["main"]] + [""]
        sec_hr = [x for c in cells if not c["main"] for x in _hr_detail(c)]
        ms = _month_skip_lines(report)
        if sec_hr or ms:
            out += sec_hr + ms + [""]
        out += ["## Çoklu deneme", ""]
        for f in ("A", "B"):
            cr = (report.get(f) or {}).get("candidate_rate")
            if cr:
                out.append(f"- Aile {f}: aday oranı (iki dönemde aralık 0'ın üstünde) gerçek {cr.get('real')} · hüküm plasebosu "
                           f"{cr.get('placebo')} ({cr.get('real_tested')}/{cr.get('placebo_tested')} hücre hükme girdi); GÜÇLÜ ADAY "
                           f"{cr.get('strong')}, sıkı {cr.get('strict_strong')}.")
        tot = report.get("candidate_rate_total") or {}
        out += [f"- Toplam: aday oranı gerçek {tot.get('real')} · plasebo {tot.get('placebo')} ({tot.get('cells')} hücre; sıkı GÜÇLÜ ADAY "
                f"{tot.get('strict_strong')}).",
                "- 24 hücrede tesadüfen 0–0,12, kümülatif 56 altın hücresinde 0–0,28 aday beklenir (rastgele yürüyüş ölçümü).", ""]
    a = report.get("A")
    if a:
        d = a.get("data") or {}
        out += ["## Veri — Dukascopy XAUUSD (aile A)", ""]
        mir = a.get("mirror") or {}
        snap = mir.get("snapshot") or {}
        out.append(f"- Ayna: kesim anı {snap.get('cut_ts')} · manifest sha256 {str(snap.get('manifest_sha256'))[:16]}… · {snap.get('n_files')} "
                   f"dosya (özet {str(snap.get('files_sha256'))[:16]}…) · koşu sonunda {mir.get('snapshot_end', '—')}.")
        tsc = d.get("timestamp_check") or {}
        for p, r in (tsc.get("periods") or {}).items():
            out.append(f"- Zaman damgası denetimi {p}: {r.get('ok')}/{r.get('weeks')} hafta beklenen kümede (pay {r.get('share')}; eşik "
                       f"{HW_MIN_SHARE}) → {'geçti' if r.get('pass') else 'GEÇMEDİ'}.")
        if tsc:
            out.append(f"- h_w yaz {tsc.get('summer')} · kış {tsc.get('winter')} · Cuma son saat {tsc.get('friday_last')}.")
        cov = d.get("coverage") or {}
        for p, r in (cov.get("periods") or {}).items():
            out.append(f"- Kapsama {p}: kullanılabilir ay {r.get('months_usable')}/{r.get('months')} ({r.get('month_share')}); ≥ 20 barlı "
                       f"Pzt–Cum günü {r.get('days_ok')}/{r.get('weekdays')} ({r.get('day_share')}) → {'geçti' if r.get('pass') else 'GEÇMEDİ'}"
                       f"; eşik altı aylar: {', '.join(r.get('bad_months') or []) or '—'}.")
        yrs = d.get("years") or {}
        if yrs:
            out += ["", "| yıl | satır | kapalı (atılan) | hacimsiz hareketli (tutulan) | hacimli düz (tutulan) | geçersiz (atılan) |",
                    "|---|---|---|---|---|---|"]
            for y, r in yrs.items():
                out.append(f"| {y} | {r.get('rows', 0)} | {r.get('flat_closed', 0)} | {r.get('vol0_moving', 0)} | {r.get('flat_vol_pos', 0)} | "
                           f"{r.get('invalid', 0)} |")
            out.append("")
        fr = d.get("frames") or {}
        if fr:
            out.append(f"- Seri: {fr.get('hours')} saat, {fr.get('days')} işlem günü, {fr.get('bars_4h')} 4h bar, {fr.get('segments')} segment; "
                       f"BOŞLUK_GERİ_BAKIŞ {fr.get('gap_lookback')}; hafta sonu saatleri {fr.get('weekend_hours')} "
                       f"({fr.get('weekend_weeks')}/{fr.get('weeks')} hafta{' — İŞARETLİ' if fr.get('weekend_flag') else ''}); kısa seans "
                       f"günü {fr.get('short_session_days')}; KISMİ_GÜN {fr.get('partial_days')} (serinin ilk ayının ilk işlem günü her zaman sayılır: "
                       f"önceki ayın dosyası okunmaz; beklenen, bilgi); günlük dosya ay sonu fark medyanı "
                       f"{(fr.get('day_file') or {}).get('median_diff')} (mutlak {(fr.get('day_file') or {}).get('median_abs_diff')}).")
            out.append("")
    b = report.get("B")
    if b:
        out += ["## Veri — PAXG spot (aile B)", "", f"- Hafta sonları: {(b.get('weeks') or {}).get('weeks')} · tatil: "
                f"{(b.get('weeks') or {}).get('holiday_weekends')}.", ""]
    data = report.get("data") or {}
    if data:
        out += ["| seri | bar | ilk | son | eksik bar | 24 saatten uzun boşluk |", "|---|---|---|---|---|---|"]
        for k, q in data.items():
            if isinstance(q, Mapping) and "bars" in q:
                mr = q.get("missing_ratio")
                out.append(f"| {k} | {q.get('bars')} | {q.get('first') or '—'} | {q.get('last') or '—'} | "
                           f"{'—' if mr is None else f'%{100 * mr:.2f}'} | {q.get('n_gaps_over_24h', 0)} |")
        out.append("")
    out += ["## Bilgi eki (hüküm DEĞİL)", ""]
    shorts = [c for f in ("A", "B") for c in ((report.get(f) or {}).get("short_info") or [])]
    if shorts:
        out += ["### SHORT (tek başına bilgi)", "", "| varyant | işlem keşif/doğr. | ort.R keşif/doğr. | plaseboya göre | hüküm |", "|---|---|---|---|---|"]
        for c in shorts:
            vs = c.get("vs_placebo") or {}
            out.append(f"| {c['name']} | {c['IS'].get('n', 0)}/{c['OOS'].get('n', 0)} | {_p(c['IS'].get('mean_r'), 3)}/{_p(c['OOS'].get('mean_r'), 3)} | "
                       f"{(_p(vs.get('IS')) + '/' + _p(vs.get('OOS'))) if vs else '—'} | {c['verdict']} |")
        out.append("")
    infos = [c for f in ("A", "B") for c in ((report.get(f) or {}).get("info_placebo") or [])]
    if infos:
        out += ["### Bilgi plaseboları", "", "| varyant | hücre | bilgi plasebosu ort.R keşif/doğr. | gerçeğin farkı |", "|---|---|---|---|"]
        for c in infos:
            vs = c.get("vs_placebo") or {}
            pl = c.get("placebo") or {}
            out.append(f"| {c['name']} | {c['cell']} | {_p((pl.get('IS') or {}).get('mean_r'), 3)}/{_p((pl.get('OOS') or {}).get('mean_r'), 3)} | "
                       f"{(_p(vs.get('IS')) + '/' + _p(vs.get('OOS'))) if vs else '—'} |")
        out.append("")
    if cells:
        out += ["### KESİLDİ ve BOŞLUK_TUTUŞ (gerçek / plasebo)", "", "| varyant | hücre | KESİLDİ | BOŞLUK_TUTUŞ | KESİLDİ piyasaya göre ort.R |",
                "|---|---|---|---|---|"]
        for c in cells:
            rc, pc = c.get("counts") or {}, c.get("placebo_counts") or {}
            sk, psk = rc.get("skipped") or {}, pc.get("skipped") or {}
            out.append(f"| {c['name']} | {c['cell']} | {sk.get('KESİLDİ', 0)}/{psk.get('KESİLDİ', 0)} | {sk.get('BOŞLUK_TUTUŞ', 0)}/"
                       f"{psk.get('BOŞLUK_TUTUŞ', 0)} | {_p((rc.get('kesildi_mtm') or {}).get('mean_r'), 3)} |")
        out += ["", "- Not: giriş barı (karar barından sonraki bar) dilimin son barıysa simulate_rule 'gelecek veri yok' der; o barda stop "
                "görülse bile işlem KESİLDİ (son segmentte) ya da BOŞLUK_TUTUŞ sayılır. Laboratuvarın kuralıdır, plaseboya aynısı uygulanır; "
                "ileriye bakma değildir.", ""]
        out += ["### Eşzamanlı açık işlem (bilgi)", "", "| varyant | hücre | bütün hüküm serisi en çok / zaman ağırlıklı ort. | doğrulama en çok / ort. |",
                "|---|---|---|---|"]
        for c in cells:
            ca = c.get("concurrency_all") or {}
            co = (c.get("monthly") or {}).get("concurrency") or {}
            out.append(f"| {c['name']} | {c['cell']} | {ca.get('max', 0)} / {_num(ca.get('mean'))} | {co.get('max', 0)} / {_num(co.get('mean'))} |")
        out.append("")
        cs = [c for c in cells if (c.get("c_sizing") or {}).get("text") == "bilgi (C)"]
        out += ["### C — oynaklık hedefli boyut (bilgi, ayrı hipotez DEĞİL)", ""]
        if not cs:
            out += ["- Sıkı hükmü GÜÇLÜ ADAY hücre yok: kenar yok.", ""]
        for c in cs:
            z = c["c_sizing"]
            out.append(f"- {c['name']} {c['cell']}: doğrulama ort. %{z.get('oos_mean_pct_month'):.2f}/ay · %95 {_ci(z.get('ci95_pct_month'))} · "
                       f"en derin düşüş %{z.get('max_drawdown_pct'):.1f} · {z.get('warn_tr')}")
        out.append("")
    for sec, title in (("seen", "Görülmüş veri (aile A; dönem bölünmez)"), ("venue", "Mekân — Binance vadeli (gerçek fonlama)")):
        s = report.get(sec)
        if not s:
            continue
        out += [f"### {title}", "", "| seri | varyant | hücre | n | ort.R | ort.R_fon / net | plasebo ort. | fark | fark %95 | not |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for r in s.get("rows") or []:
            st = r.get("all") or {}
            pl = r.get("placebo") or {}
            g = r.get("gross") or st
            second = (r.get("fon") or {}).get("mean_r") if sec == "seen" else st.get("mean_r")
            out.append(f"| {r['series']} ({r.get('label')}) | {r['name']} | {r['cell']} | {st.get('n', 0)} | {_p(g.get('mean_r'), 3)} | {_p(second, 3)} | "
                       f"{_p(pl.get('mean_r'), 3)} | {_p(r.get('diff'), 3)} | {_ci(r.get('diff_ci'))} | {r.get('note') or ''} |")
        out.append("")
        if sec == "venue":
            out += [f"- {VENUE_FUNDING_NOTE_TR}", ""]
    cal = report.get("calib")
    if cal:
        out += ["### Sentetik ayar (rastgele yürüyüş)", "",
                f"- {cal.get('worlds')} dünya · hücre başına GÜÇLÜ ADAY {cal.get('strong_rate_per_cell')} · sıkı {cal.get('strict_rate_per_cell')} · "
                f"24 hücrede beklenen {cal.get('expected_per_24')}.", ""]
    out += ["## Ön kayıttan önce veriden görülenler (biçim doğrulaması; belgedeki açıklamanın tekrarı)", ""] + [f"- {x}" for x in DISCLOSURE_TR] + [""]
    out += ["## Notlar ve bilinen sınırlar", ""] + [f"- {x}" for x in LIMITS_TR] + [""]
    out += ["Belirsiz kuralların okunuşu `GOLD_V2_REGISTRY['readings_tr']` içindedir (mühre dahil).", ""]
    return "\n".join(out)


LIMITS_TR = (
    "`A_4H_DONCH_D200`'ün hüküm plasebosu SMA200(1d) süzgecini İÇERMEZ; orada 'plaseboyu geçiyor' rejim süzgecinin katkısını da kapsar.",
    "Aylık varyantların 5 ATR stopu yalnız R birimini tanımlayan felaket stopudur; Moskowitz–Ooi–Pedersen'de stop yoktur (sapma).",
    "Aylık varyantlarda her ay ayrı işlem sayılır, maliyet her ay yeniden ödenir (gerçek defter pozisyonu taşırdı; sonucu aşağı çeker).",
    "Dukascopy BID Binance fiyatı değildir; iki yön de BID ile simüle edilir, spread maliyet modelinin içindedir; 2006–2010 spread'leri "
    "bugünkünden genişti.",
    "Fonlama vekili sabit 8 saatte %0,01'dir; gerçek XAUUSDT fonlaması primle değişir (mekân satırları gerçek fonlamayı gösterir).",
    "PAXG'nin 2020–2021 hafta sonu likiditesi çok inceydi; 2021Q4–2025Q1 arasında 1 USDT'lik fiyat adımları vardı; açılış anında 3 bps "
    "kayma iyimser olabilir; spotta SHORT varsayımsaldır (gerçekte vadelide yapılırdı).",
    "Tek varlık: piyasa yönü ile kural kenarını ayırmak zordur; maruziyet eşli plasebo bunu ancak kısmen ayırır.",
    "Aile A'nın önseli yayımlanmış trend takibi kanıtıyla (yaklaşık 2016'ya kadar) örtüşür; doğrulama 'bu depoda görülmemiş veride, tek "
    "varlıkta, botun maliyetiyle' bir sınamadır.",
    "'Daha yüksek risk' satırı ve C, hükmü veren aynı doğrulama ortalamasından türer (kazanan laneti); yalnız bilgidir, kenar değildir.",
    "Aylık varyantlarda m ayının kararı m+1 ayının kullanılabilir olmasına bağlıdır (fiyat değil, veri durumu); ön kayıtlıdır, plaseboya "
    "aynısı uygulanır; atlama sayısı (SONRAKİ_AY_KULLANILAMAZ) ikincil hücrelerin altında yazılır.",
    "Zaman damgası ya da kapsama denetimi geçmezse koşu GoldDataError ile durmak yerine aile A'yı 'yapılamadı' diye yazar ve varyantları "
    "hiç koşmaz; sonuç belgedekiyle aynıdır (aile A'da hüküm hücresi yok; görülmüş/mekân satırları onun yerine geçmez).",
    "Mühürlü okunuşlardan birinde (maruziyet eşli plasebo seçimi) atlama etiketi 'ESŞ_ADAY_YOK' diye yanlış yazılmıştır; kodun saydığı "
    "etiket 'EŞ_ADAY_YOK'tur. Mühür değişmesin diye metin düzeltilmedi.",
    "Öneri kuralının bir şartı ölçülemiyorsa (ailenin hüküm plasebo aday oranı tanımsız ya da hücrenin vadeli mekân satırı yok) şart "
    "tutmuş SAYILMAZ; öneri yapılmaz ve nedeni sonuçta yazılır.",
)

#: Belgenin "Bu belge yazılırken veriden ne görüldü" bölümü (belge: "bu açıklama sonuç raporunda tekrarlanır").
DISCLOSURE_TR = (
    "Dukascopy dosya biçimi 2026-10-05 05:20–05:21 UTC'de aynanın DIŞINDA bir klasöre (scratchpad/gold/duka_h) indirilen deneme "
    "dosyalarıyla doğrulandı. Çözülen dosyalar yalnız ikidir: 2010-01 saatlik (2010/00/BID_candles_hour_1.bi5, 744 kayıt) ve 2010 "
    "günlük (2010/BID_candles_day_1.bi5, 365 kayıt); her birinin ilk iki ve son kaydı fiyatlarıyla ekrana basıldı.",
    "Görülenler: 2010-01-01 00:00 ve 01:00 UTC saatlik barları (≈ 1.096 USD), 2010-01-31 23:00 UTC (Pazar) saatlik barı (kapanış "
    "≈ 1.083, hacim > 0), 2010-01-01 günlük barı (açılış ≈ 1.096), 2010-01-02 günlük barı (Cumartesi; düz, hacim 0) ve 2010-12-31 "
    "günlük barı (kapanış ≈ 1.420). Bundan 2010'un yönü (≈ +%30) ve Ocak 2010'un yönü (≈ −%1) bilinir; ikisi de keşif dönemindedir. "
    "Doğrulama dönemine (2014-01 → 2020-07) ait hiçbir fiyat görülmedi. İkisi de kamuya açık bilgidir.",
    "Aynı anda indirilen 2006-06 saatlik dosyası (2006/05/BID_candles_hour_1.bi5) çözülmedi; 2015/00/BID_candles_day_1.bi5 isteği 503 "
    "döndü (çözülmedi).",
    "gold_v1 sırasında (2026-10-04) dakikalık biçimi doğrulamak için 2006-01-01 (Pazar; düz, hacim 0) ve 2006-01-03 dakikalık dosyaları "
    "çözüldü; ilk üç / son iki kayıt ve sütunların en küçük/en büyük değerleri basıldı (2006-01-03 gün içi ≈ 516–535 USD). gold_v1'in "
    "Dukascopy bölümü kapsama eşiğine takıldığı için (%2,4) hiç koşmadı ve hiçbir olay üretmedi.",
    "Aynanın geri kalanından ön kayıttan önce yalnız manifest (indirme durumu, bayt) okundu; hiçbir getiri dizisi, gösterge, sinyal, "
    "işlem ya da R hesaplanmadı. Bu dosyalar hükümden çıkarılmaz (birkaç fiyat seviyesi kuralların sonucunu belirlemez).",
)

VENUE_FUNDING_NOTE_TR = ("Not: vadeli satırlarda gerçek fonlama (futures_lab.funding_carry) stopla kapanan işlemde pencereyi stop barının "
                         "AÇILIŞINDA kapatır; fonlama vekili (R_fon) stop barının KAPANIŞINDA. Fark en çok bir barın fonlamasıdır; yalnız "
                         "bilgi satırlarını etkiler, hükme girmez.")


__all__ = ["A_NAMES", "B_NAMES", "BOTH_CELL", "CELLS", "Counts", "FAMILY", "GOLD2_VERSION", "GOLD_V2_REGISTRY", "GOLD_V2_REGISTRY_SHA",
           "GoldDataError", "MAIN_CELLS", "READINGS_TR", "REPORT_JSON", "REPORT_MD", "SECTIONS", "VARIANTS", "WINDOWS", "add_indicators",
           "apply_notes", "binance_frame", "calibration", "cell_rows", "clean_bars", "conclusion", "coverage_check", "decode_day_file",
           "decode_hour_file", "decode_span", "duka_day_path", "duka_frames", "duka_hour_path", "family_cells", "higher_risk",
           "judge", "load_duka_hourly", "matched_placebos", "mirror_ready", "mirror_snapshot", "month_table", "monthly_signals",
           "ny_parts", "ny_to_utc_ms", "render_md", "rule_signals", "run", "run_family_a", "run_family_b", "simulate_one",
           "synthetic_duka_hourly", "synthetic_paxg_hourly", "week_open_check", "weekend_table"]
