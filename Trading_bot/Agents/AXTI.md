---
symbol: AXTI/USDT
verdict: LONG
conviction: 48
price: 95.19
updated: 2026-08-17T21:18:41+00:00
tags: [trading, agents]
---
# 🧠 AXTI/USDT — Ajan Raporu (🟢 LONG, kanaat %48)
> 🟢 LONG · kanaat %48 · 5/5 yönlü ajan hemfikir · fiyat 95.19 · trend boğa  ·  2026-08-18 00:19

Şema: [[Agents/AXTI.canvas]] · Backtest/karar: [[Coins/AXTI]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/AXTI.png]]

🔎 Tarayıcı: LONG skor **81**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
- Yön: **LONG** · ✅ geçerli
- Tetik: 91.13 desteğine geri çekilmede alıcı mumuyla long (kovalama) (geri çekilme)
- Giriş ~91.13 · Stop 84.39 (%7.4) · Hedef1 116.85 · Hedef2 111.36 · R/R 3.81
- Kaldıraç ≤ 1x (öneri 1x) · marj ≈ 13.51 USDT · notional ≈ 13.51 USDT · riske atılan ≈ 1.0 USDT

## ✅ YAP
- Yön LONG (kanaat %48). 91.13 desteğine geri çekilmede alıcı mumuyla long (kovalama).
- Stop 84.39 (−%7.4); hedef1 116.85, hedef2 111.36; R/R 3.81
- Kaldıraç ≤ 1x (öneri 1x), marj ≈ 13.51 USDT, riske atılan ≈ 1.0 USDT

## 🚫 YAPMA
- 4 saatlik RSI 87 aşırı alım: yeni long açma, geri çekilme bekle
- Saatlik RSI 78 aşırı alım: yeni long açma, geri çekilme bekle
- Piyasa emriyle kovalama; sadece koşul gerçekleşince gir
- Stop'u aşağı taşıma; hedef1'de yarısını kapat, kalanını başa-baş stopla taşı

## 🔀 EĞER … İSE
- EĞER 4h kapanış 91.13 altına inerse → long fikri iptal, 85.58 riski
- EĞER 4h kapanış 116.85 üstüne çıkarsa → hedef sonraki direnç

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| r1 | 116.85 | +22.75% |
| high_24h | 97.92 | +2.87% |
| s1 | 91.13 | -4.27% |
| s2 | 85.58 | -10.10% |
| low_24h | 80.89 | -15.02% |
| ema20_1d | 73.19 | -23.11% |
| ema50_1d | 69.34 | -27.16% |
| ema200_1d | nan | +nan% |
| ema_support | 73.19 | -23.11% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %10.22 (yüzdelik 2), 30g gerçekleşen vol %221. Önerilen max kaldıraç 1x, stop mesafesi ≈ %25.5.
- Günlük ATR %10.22 → son 200 günün %2'inden yüksek
- Bollinger bant genişliği %80.1 (yüzdelik 80) → genişlemiş, hareket başlamış
- 4h ATR %2.81
- Metrikler: atr_pct_1d=10.22, atr_pct_4h=2.81, atr_rank=2, bb_width_pct=80.11, bb_width_rank=80, realized_vol_30d=220.7, regime=DÜŞÜK, stop_pct=25.55, max_leverage=1

### 📈 Trend & EMA Çizgileri Ajanı — BOĞA (bias +0.45, güven 62)
Fiyat 20g EMA (~73.19) yakınında; yakın destek 20g EMA ~73.19. Çoklu zaman dilimi eğilimi: BOĞA.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %+9.59/10bar, ADX 29
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+2.86/10bar, ADX 33
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+4.33/10bar, ADX 53
- Metrikler: adx_1d=29, ema50_slope_1d=9.59

### 🕯️ Mum Yapısı Ajanı — BOĞA (bias +0.25, güven 46)
Mum yapısı boğa: aralığın %54'inde kapandı, gövde %38, üst fitil %46, alt fitil %17; son 5 mumun 4'i yeşil; karışık yapı
- Günlük son mum: aralığın %54'inde kapandı, gövde %38, üst fitil %46, alt fitil %17; son 5 mumun 4'i yeşil; karışık yapı
- 4 saatlik son mum: aralığın %63'inde kapandı, gövde %58, üst fitil %37, alt fitil %5; son 5 mumun 5'i yeşil; HH+HL (yükselen yapı)
- Metrikler: clv_1d=0.54, structure_1d=karışık yapı, clv_4h=0.63, structure_4h=HH+HL (yükselen yapı)

### 📊 Hacim Ajanı — GÜÇLÜ BOĞA (bias +0.85, güven 73)
Hacim güçlü boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.04 katı (düşük); alım/satım hacmi oranı 3.35; OBV 20-bar eğimi +1072.6%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 5.25 katı (yüksek); alım/satım hacmi oranı 3.38; OBV 20-bar eğimi +128.6%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.04, updown_vol_1d=3.35, vol_ratio_4h=5.25, updown_vol_4h=3.38

### 🧱 Destek/Direnç Ajanı — NÖTR (bias -0.09, güven 55)
Yakın direnç 116.85 (+22.8%); yakın destek 91.13 (−4.5%). Fiyat son aralığın %73'inde.
- 116.85 üstünde 4h kapanış → hedef aralık üstü 116.85
- 91.13 altında 4h kapanış → risk 85.58
- 20 günlük en yüksek 94.57, en düşük 36.23
- Metrikler: resistances=[116.85], supports=[91.13, 85.58, 83.87, 81.38, 79.995, 77.02], range_position=0.73, range_high=116.85, range_low=36.23

### 🚀 Momentum Ajanı — GÜÇLÜ BOĞA (bias +0.69, güven 74)
Momentum güçlü boğa (RSI 1d/4h/1h: 58/87/78).
- Günlük: RSI14 58, MACD hist + (daralıyor), ROC10 %+5.8
- 4 saatlik: RSI14 87, MACD hist + (genişliyor), ROC10 %+17.9
- Saatlik: RSI14 78, MACD hist + (daralıyor), ROC10 %+11.5
- ⚠️ 4 saatlik RSI 87 aşırı alım: yeni long açma, geri çekilme bekle
- ⚠️ Saatlik RSI 78 aşırı alım: yeni long açma, geri çekilme bekle
- Metrikler: rsi_1d=58, macd_hist_1d=0.6053, rsi_4h=87, macd_hist_4h=1.4172, rsi_1h=78, macd_hist_1h=0.8911

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — BOĞA (bias +0.46, güven 50)
Canlı akış boğa: emir defteri alış payı %61, funding %0.0000, 24s %+17.29.
- 24s: %+17.29, aralık 80.89–97.92, fiyat aralığın %84'inde, hacim 66M USDT
- Emir defteri (ilk 20 kademe): alış 23K / satış 15K USDT → alış payı %61
- Funding %0.0000 / 8s (yıllık ≈ %0) → nötr
- Açık pozisyon (OI): 142,877 AXTI
- Global long/short hesap oranı 0.57 (long %36)
- Metrikler: chg24_pct=17.29, high24=97.92, low24=80.89, pos24=0.84, vol24_usdt=65656449, ob_imbalance=0.61, spread_pct=0.0105, funding_pct=0.0, funding_annual_pct=0.0, open_interest=142877.18, long_short_ratio=0.57, long_pct=36.5

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.