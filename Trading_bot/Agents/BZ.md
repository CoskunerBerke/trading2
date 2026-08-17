---
symbol: BZ/USDT
verdict: LONG
conviction: 45
price: 89.09
updated: 2026-08-17T21:38:58+00:00
tags: [trading, agents]
---
# 🧠 BZ/USDT — Ajan Raporu (🟢 LONG, kanaat %45)
> 🟢 LONG · kanaat %45 · 5/5 yönlü ajan hemfikir · fiyat 89.09 · trend boğa  ·  2026-08-18 00:39

Şema: [[Agents/BZ.canvas]] · Backtest/karar: [[Coins/BZ]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/BZ.png]]

🔎 Tarayıcı: LONG skor **79**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
- Yön: **LONG** · ✅ geçerli
- Tetik: 4h mum 90.06 üstünde kapanırsa long (kırılım)
- Giriş ~90.15 · Stop 87.26 (%3.21) · Hedef1 95.54 · Hedef2 97.36 · R/R 1.87
- Kaldıraç ≤ 3x (öneri 2x) · marj ≈ 15.0 USDT · notional ≈ 30.0 USDT · riske atılan ≈ 0.96 USDT

## ✅ YAP
- Yön LONG (kanaat %45). 4h mum 90.06 üstünde kapanırsa long.
- Stop 87.26 (−%3.21); hedef1 95.54, hedef2 97.36; R/R 1.87
- Kaldıraç ≤ 3x (öneri 2x), marj ≈ 15.0 USDT, riske atılan ≈ 0.96 USDT

## 🚫 YAPMA
- Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- 4h'de üst üste güçlü yeşil mumlar: tepeden kovalama, geri çekilme bekle
- Destek 88.34 hemen altta: kırılım kapanışı görmeden short açma
- Piyasa emriyle kovalama; sadece koşul gerçekleşince gir
- Stop'u aşağı taşıma; hedef1'de yarısını kapat, kalanını başa-baş stopla taşı

## 🔀 EĞER … İSE
- EĞER 4h kapanış 88.34 altına inerse → long fikri iptal, 87.00 riski
- EĞER 4h kapanış 90.06 üstüne çıkarsa → hedef 91.32

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| r2 | 91.32 | +2.50% |
| r1 | 90.06 | +1.09% |
| high_24h | 89.37 | +0.31% |
| s1 | 88.34 | -0.84% |
| s2 | 87.00 | -2.35% |
| ema20_1d | 85.42 | -4.12% |
| ema50_1d | 85.04 | -4.54% |
| ema200_1d | nan | +nan% |
| ema_support | 87.27 | -2.05% |
| low_24h | 86.02 | -3.45% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %3.72 (yüzdelik 11), 30g gerçekleşen vol %53. Önerilen max kaldıraç 3x, stop mesafesi ≈ %9.3.
- Günlük ATR %3.72 → son 200 günün %11'inden yüksek
- Bollinger bant genişliği %14.4 (yüzdelik 14) → sıkışma, patlama yakın olabilir
- 4h ATR %1.08
- ⚠️ Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Metrikler: atr_pct_1d=3.72, atr_pct_4h=1.08, atr_rank=11, bb_width_pct=14.44, bb_width_rank=14, realized_vol_30d=53.5, regime=DÜŞÜK, stop_pct=9.29, max_leverage=3

### 📈 Trend & EMA Çizgileri Ajanı — BOĞA (bias +0.33, güven 56)
Fiyat 100g EMA (~87.27) yakınında; yakın destek 100g EMA ~87.27. Çoklu zaman dilimi eğilimi: BOĞA.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %+0.32/10bar, ADX 16
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.49/10bar, ADX 13
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.61/10bar, ADX 30
- Metrikler: adx_1d=16, ema50_slope_1d=0.32

### 🕯️ Mum Yapısı Ajanı — BOĞA (bias +0.47, güven 56)
Mum yapısı boğa: aralığın %67'inde kapandı, gövde %54, üst fitil %33, alt fitil %13 — iç bar (sıkışma); son 5 mumun 2'i yeşil; karışık ya
- Günlük son mum: aralığın %67'inde kapandı, gövde %54, üst fitil %33, alt fitil %13 — iç bar (sıkışma); son 5 mumun 2'i yeşil; karışık yapı
- 4 saatlik son mum: aralığın %86'inde kapandı, gövde %73, üst fitil %14, alt fitil %13; son 5 mumun 4'i yeşil; HH+HL (yükselen yapı)
- ⚠️ 4h'de üst üste güçlü yeşil mumlar: tepeden kovalama, geri çekilme bekle
- Metrikler: clv_1d=0.67, structure_1d=karışık yapı, clv_4h=0.86, structure_4h=HH+HL (yükselen yapı)

### 📊 Hacim Ajanı — BOĞA (bias +0.35, güven 50)
Hacim boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.09 katı (düşük); alım/satım hacmi oranı 0.98; OBV 20-bar eğimi -3.3%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 2.16 katı (yüksek); alım/satım hacmi oranı 3.95; OBV 20-bar eğimi +114.2%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.09, updown_vol_1d=0.98, vol_ratio_4h=2.16, updown_vol_4h=3.95

### 🧱 Destek/Direnç Ajanı — NÖTR (bias +0.03, güven 55)
Yakın direnç 90.06 (+1.1%); yakın destek 88.34 (−0.8%). Fiyat son aralığın %42'inde.
- 90.06 üstünde 4h kapanış → hedef 91.32
- 88.34 altında 4h kapanış → risk 87.00
- 20 günlük en yüksek 91.75, en düşük 78.14
- ⚠️ Destek 88.34 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[90.06, 91.31666667, 91.94, 93.32, 95.54, 97.36], supports=[88.34, 87.0, 86.0625, 84.5475, 83.5, 82.615], range_position=0.42, range_high=115.11, range_low=70.19

### 🚀 Momentum Ajanı — GÜÇLÜ BOĞA (bias +0.88, güven 83)
Momentum güçlü boğa (RSI 1d/4h/1h: 53/72/71).
- Günlük: RSI14 53, MACD hist + (genişliyor), ROC10 %+4.1
- 4 saatlik: RSI14 72, MACD hist + (genişliyor), ROC10 %+3.1
- Saatlik: RSI14 71, MACD hist + (genişliyor), ROC10 %+2.4
- Metrikler: rsi_1d=53, macd_hist_1d=0.1464, rsi_4h=72, macd_hist_4h=0.1913, rsi_1h=71, macd_hist_1h=0.2392

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — BOĞA (bias +0.39, güven 50)
Canlı akış boğa: emir defteri alış payı %52, funding %-0.0090, 24s %+2.98.
- 24s: %+2.98, aralık 86.02–89.37, fiyat aralığın %92'inde, hacim 292M USDT
- Emir defteri (ilk 20 kademe): alış 603K / satış 556K USDT → alış payı %52
- Funding %-0.0090 / 8s (yıllık ≈ %-10) → shortlar ödüyor (short kalabalık)
- Açık pozisyon (OI): 2,478,138 BZ
- Global long/short hesap oranı 0.57 (long %36)
- Metrikler: chg24_pct=2.98, high24=89.37, low24=86.02, pos24=0.92, vol24_usdt=291565929, ob_imbalance=0.52, spread_pct=0.0112, funding_pct=-0.009, funding_annual_pct=-9.9, open_interest=2478137.75, long_short_ratio=0.57, long_pct=36.5

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.