---
symbol: GOOGL/USDT
verdict: SHORT
conviction: 46
price: 344.42
updated: 2026-08-17T21:39:05+00:00
tags: [trading, agents]
---
# 🧠 GOOGL/USDT — Ajan Raporu (🔴 SHORT, kanaat %46)
> 🔴 SHORT · kanaat %46 · 2/3 yönlü ajan hemfikir · fiyat 344.42 · trend güçlü ayı  ·  2026-08-18 00:39

Şema: [[Agents/GOOGL.canvas]] · Backtest/karar: [[Coins/GOOGL]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/GOOGL.png]]

🔎 Tarayıcı: SHORT skor **67**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
- Yön: **SHORT** · ✅ geçerli
- Tetik: 4h mum 341.89 altında kapanırsa short (kırılım)
- Giriş ~341.55 · Stop 346.97 (%1.59) · Hedef1 330.43 · Hedef2 324.10 · R/R 2.05
- Kaldıraç ≤ 5x (öneri 2x) · marj ≈ 15.0 USDT · notional ≈ 30.0 USDT · riske atılan ≈ 0.48 USDT

## ✅ YAP
- Yön SHORT (kanaat %46). 4h mum 341.89 altında kapanırsa short.
- Stop 346.97 (+%1.59); hedef1 330.43, hedef2 324.10; R/R 2.05
- Kaldıraç ≤ 5x (öneri 2x), marj ≈ 15.0 USDT, riske atılan ≈ 0.48 USDT

## 🚫 YAPMA
- Direnç 347.13 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 341.89 hemen altta: kırılım kapanışı görmeden short açma
- Kitle çok long (5.6): kontraryen risk, long'da sıkı stop
- Dipten short kovalama; sadece koşul gerçekleşince gir
- Short'ta funding pozitifse taşıma maliyeti lehine; negatifse squeeze riski — pozisyonu uzun tutma

## 🔀 EĞER … İSE
- EĞER 4h kapanış 347.13 üstüne çıkarsa → short fikri iptal, 348.41 riski
- EĞER 4h kapanış 341.89 altına inerse → hedef 335.68

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema50_1d | 352.03 | +2.21% |
| ema200_1d | nan | +nan% |
| ema_resistance | 350.65 | +1.81% |
| ema20_1d | 350.65 | +1.81% |
| high_24h | 350.20 | +1.68% |
| r2 | 348.41 | +1.16% |
| r1 | 347.13 | +0.79% |
| low_24h | 342.51 | -0.55% |
| s1 | 341.89 | -0.73% |
| s2 | 335.68 | -2.54% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite NORMAL: günlük ATR %2.50 (yüzdelik 53), 30g gerçekleşen vol %41. Önerilen max kaldıraç 5x, stop mesafesi ≈ %6.3.
- Günlük ATR %2.50 → son 200 günün %53'inden yüksek
- Bollinger bant genişliği %12.6 (yüzdelik 52) → normal
- 4h ATR %0.52
- Metrikler: atr_pct_1d=2.5, atr_pct_4h=0.52, atr_rank=53, bb_width_pct=12.55, bb_width_rank=52, realized_vol_30d=41.4, regime=NORMAL, stop_pct=6.26, max_leverage=5

### 📈 Trend & EMA Çizgileri Ajanı — GÜÇLÜ AYI (bias -1.00, güven 90)
Fiyat 20g EMA (~350.65) yakınında; yakın direnç 20g EMA ~350.65. Çoklu zaman dilimi eğilimi: GÜÇLÜ AYI.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %-0.22/10bar, ADX 19
- 4 saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.25/10bar, ADX 24
- Saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.35/10bar, ADX 43
- Metrikler: adx_1d=19, ema50_slope_1d=-0.22

### 🕯️ Mum Yapısı Ajanı — BOĞA (bias +0.37, güven 51)
Mum yapısı boğa: aralığın %51'inde kapandı, gövde %44, üst fitil %49, alt fitil %7; son 5 mumun 4'i yeşil; HH+HL (yükselen yapı)
- Günlük son mum: aralığın %51'inde kapandı, gövde %44, üst fitil %49, alt fitil %7; son 5 mumun 4'i yeşil; HH+HL (yükselen yapı)
- 4 saatlik son mum: aralığın %71'inde kapandı, gövde %2, üst fitil %29, alt fitil %68 — doji (kararsızlık), çekiç/pin bar (alıcı reddi), iç bar (sıkışma); son 5 mumun 1'i yeşil; karışık yapı
- Metrikler: clv_1d=0.51, structure_1d=HH+HL (yükselen yapı), clv_4h=0.71, structure_4h=karışık yapı

### 📊 Hacim Ajanı — NÖTR (bias -0.05, güven 37)
Hacim nötr: dengeli
- Günlük: son bar hacmi 20-bar ortalamasının 0.11 katı (düşük); alım/satım hacmi oranı 0.97; OBV 20-bar eğimi +7.0%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 1.50 katı (yüksek); alım/satım hacmi oranı 0.49; OBV 20-bar eğimi -9.2%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.11, updown_vol_1d=0.97, vol_ratio_4h=1.5, updown_vol_4h=0.49

### 🧱 Destek/Direnç Ajanı — NÖTR (bias +0.07, güven 55)
Yakın direnç 347.13 (+0.8%); yakın destek 341.89 (−0.7%). Fiyat son aralığın %31'inde.
- 347.13 üstünde 4h kapanış → hedef 348.41
- 341.89 altında 4h kapanış → risk 335.68
- 20 günlük en yüksek 384.87, en düşük 321.28
- ⚠️ Direnç 347.13 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 341.89 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[347.13, 348.41, 350.43666667, 351.52, 353.69, 354.78], supports=[341.894, 335.68, 330.4275, 324.095, 316.88, 315.13333333], range_position=0.31, range_high=408.94, range_low=315.0

### 🚀 Momentum Ajanı — GÜÇLÜ AYI (bias -0.66, güven 73)
Momentum güçlü ayı (RSI 1d/4h/1h: 49/32/35).
- Günlük: RSI14 49, MACD hist − (daralıyor), ROC10 %-2.9
- 4 saatlik: RSI14 32, MACD hist − (genişliyor), ROC10 %-1.0
- Saatlik: RSI14 35, MACD hist − (daralıyor), ROC10 %-1.2
- Metrikler: rsi_1d=49, macd_hist_1d=-0.9366, rsi_4h=32, macd_hist_4h=-0.2119, rsi_1h=35, macd_hist_1h=-0.3189

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias -0.13, güven 50)
Canlı akış nötr: emir defteri alış payı %64, funding %0.0132, 24s %-1.40.
- 24s: %-1.40, aralık 342.51–350.20, fiyat aralığın %25'inde, hacim 60M USDT
- Emir defteri (ilk 20 kademe): alış 83K / satış 46K USDT → alış payı %64
- Funding %0.0132 / 8s (yıllık ≈ %15) → longlar ödüyor (long kalabalık)
- Açık pozisyon (OI): 219,739 GOOGL
- Global long/short hesap oranı 5.60 (long %85)
- ⚠️ Kitle çok long (5.6): kontraryen risk, long'da sıkı stop
- Metrikler: chg24_pct=-1.4, high24=350.2, low24=342.51, pos24=0.25, vol24_usdt=59712375, ob_imbalance=0.64, spread_pct=0.0029, funding_pct=0.0132, funding_annual_pct=14.5, open_interest=219739.13, long_short_ratio=5.6, long_pct=84.9

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.