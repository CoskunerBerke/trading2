---
symbol: XAUT/USDT
verdict: LONG
conviction: 38
price: 4395.57
updated: 2026-08-17T21:39:07+00:00
tags: [trading, agents]
---
# 🧠 XAUT/USDT — Ajan Raporu (🟢 LONG, kanaat %38)
> 🟢 LONG · kanaat %38 · 3/3 yönlü ajan hemfikir · fiyat 4,396 · trend boğa  ·  2026-08-18 00:39

Şema: [[Agents/XAUT.canvas]] · Backtest/karar: [[Coins/XAUT]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/XAUT.png]]

🔎 Tarayıcı: LONG skor **67**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
- Yön: **LONG** · ✅ geçerli
- Tetik: 4h mum 4,421 üstünde kapanırsa long (kırılım)
- Giriş ~4,425 · Stop 4,366 (%1.35) · Hedef1 4,571 · Hedef2 4,644 · R/R 2.45
- Kaldıraç ≤ 5x (öneri 2x) · marj ≈ 15.0 USDT · notional ≈ 30.0 USDT · riske atılan ≈ 0.41 USDT

## ✅ YAP
- Yön LONG (kanaat %38). 4h mum 4,421 üstünde kapanırsa long.
- Stop 4,366 (−%1.35); hedef1 4,571, hedef2 4,644; R/R 2.45
- Kaldıraç ≤ 5x (öneri 2x), marj ≈ 15.0 USDT, riske atılan ≈ 0.41 USDT

## 🚫 YAPMA
- Direnç 4,421 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 4,368 hemen altta: kırılım kapanışı görmeden short açma
- Piyasa emriyle kovalama; sadece koşul gerçekleşince gir
- Stop'u aşağı taşıma; hedef1'de yarısını kapat, kalanını başa-baş stopla taşı

## 🔀 EĞER … İSE
- EĞER 4h kapanış 4,368 altına inerse → long fikri iptal, 4,349 riski
- EĞER 4h kapanış 4,421 üstüne çıkarsa → hedef 4,459

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| r2 | 4,459 | +1.45% |
| r1 | 4,421 | +0.58% |
| high_24h | 4,409 | +0.31% |
| s1 | 4,368 | -0.62% |
| low_24h | 4,351 | -1.01% |
| s2 | 4,349 | -1.07% |
| ema20_1d | 4,257 | -3.15% |
| ema50_1d | 4,201 | -4.43% |
| ema200_1d | nan | +nan% |
| ema_support | 4,257 | -3.15% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %1.47 (yüzdelik 2), 30g gerçekleşen vol %24. Önerilen max kaldıraç 5x, stop mesafesi ≈ %3.7.
- Günlük ATR %1.47 → son 200 günün %2'inden yüksek
- Bollinger bant genişliği %13.5 (yüzdelik 98) → genişlemiş, hareket başlamış
- 4h ATR %0.45
- Metrikler: atr_pct_1d=1.47, atr_pct_4h=0.45, atr_rank=2, bb_width_pct=13.46, bb_width_rank=98, realized_vol_30d=24.0, regime=DÜŞÜK, stop_pct=3.67, max_leverage=5

### 📈 Trend & EMA Çizgileri Ajanı — BOĞA (bias +0.45, güven 62)
Fiyat 20g EMA (~4,257) yakınında; yakın destek 20g EMA ~4,257. Çoklu zaman dilimi eğilimi: BOĞA.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %+1.80/10bar, ADX 34
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.36/10bar, ADX 32
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.20/10bar, ADX 30
- Metrikler: adx_1d=34, ema50_slope_1d=1.8

### 🕯️ Mum Yapısı Ajanı — NÖTR (bias +0.03, güven 36)
Mum yapısı nötr: aralığın %10'inde kapandı, gövde %52, üst fitil %39, alt fitil %10; son 5 mumun 2'i yeşil; HH+HL (yükselen yapı)
- Günlük son mum: aralığın %10'inde kapandı, gövde %52, üst fitil %39, alt fitil %10; son 5 mumun 2'i yeşil; HH+HL (yükselen yapı)
- 4 saatlik son mum: aralığın %70'inde kapandı, gövde %22, üst fitil %8, alt fitil %70; son 5 mumun 3'i yeşil; karışık yapı
- Metrikler: clv_1d=0.1, structure_1d=HH+HL (yükselen yapı), clv_4h=0.7, structure_4h=karışık yapı

### 📊 Hacim Ajanı — GÜÇLÜ BOĞA (bias +0.55, güven 59)
Hacim güçlü boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.24 katı (düşük); alım/satım hacmi oranı 1.79; OBV 20-bar eğimi +16.4%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 1.70 katı (yüksek); alım/satım hacmi oranı 1.57; OBV 20-bar eğimi +40.8%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.24, updown_vol_1d=1.79, vol_ratio_4h=1.7, updown_vol_4h=1.57

### 🧱 Destek/Direnç Ajanı — NÖTR (bias -0.02, güven 55)
Yakın direnç 4,421 (+0.6%); yakın destek 4,368 (−0.6%). Fiyat son aralığın %56'inde.
- 4,421 üstünde 4h kapanış → hedef 4,459
- 4,368 altında 4h kapanış → risk 4,349
- 20 günlük en yüksek 4,427, en düşük 3,991
- ⚠️ Direnç 4,421 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 4,368 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[4420.90333333, 4459.13, 4507.77, 4571.4, 4644.43, 4752.245], supports=[4368.39, 4348.552, 4292.01666667, 4206.97, 4189.41, 4155.81], range_position=0.56, range_high=4756.93, range_low=3942.93

### 🚀 Momentum Ajanı — GÜÇLÜ BOĞA (bias +0.79, güven 79)
Momentum güçlü boğa (RSI 1d/4h/1h: 63/63/59).
- Günlük: RSI14 63, MACD hist + (daralıyor), ROC10 %+2.7
- 4 saatlik: RSI14 63, MACD hist + (genişliyor), ROC10 %+0.9
- Saatlik: RSI14 59, MACD hist + (daralıyor), ROC10 %+0.4
- Metrikler: rsi_1d=63, macd_hist_1d=12.0413, rsi_4h=63, macd_hist_4h=3.903, rsi_1h=59, macd_hist_1h=1.0089

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias +0.10, güven 50)
Canlı akış nötr: emir defteri alış payı %49, funding %0.0048, 24s %+0.88.
- 24s: %+0.88, aralık 4,351–4,409, fiyat aralığın %77'inde, hacim 36M USDT
- Emir defteri (ilk 20 kademe): alış 33K / satış 34K USDT → alış payı %49
- Funding %0.0048 / 8s (yıllık ≈ %5) → nötr
- Açık pozisyon (OI): 6,292 XAUT
- Global long/short hesap oranı 1.17 (long %54)
- Metrikler: chg24_pct=0.88, high24=4409.0, low24=4351.24, pos24=0.77, vol24_usdt=35708178, ob_imbalance=0.49, spread_pct=0.0002, funding_pct=0.0048, funding_annual_pct=5.2, open_interest=6291.943, long_short_ratio=1.17, long_pct=54.0

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.