---
symbol: PAXG/USDT
verdict: LONG
conviction: 53
price: 4409.95
updated: 2026-08-17T21:39:09+00:00
tags: [trading, agents]
---
# 🧠 PAXG/USDT — Ajan Raporu (🟢 LONG, kanaat %53)
> 🟢 LONG · kanaat %53 · 4/4 yönlü ajan hemfikir · fiyat 4,410 · trend güçlü boğa  ·  2026-08-18 00:39

Şema: [[Agents/PAXG.canvas]] · Backtest/karar: [[Coins/PAXG]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/PAXG.png]]

🔎 Tarayıcı: LONG skor **66**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
- Yön: **LONG** · ✅ geçerli
- Tetik: 4h mum 4,436 üstünde kapanırsa long (kırılım)
- Giriş ~4,440 · Stop 4,379 (%1.37) · Hedef1 4,579 · Hedef2 4,648 · R/R 2.28
- Kaldıraç ≤ 5x (öneri 2x) · marj ≈ 15.0 USDT · notional ≈ 30.0 USDT · riske atılan ≈ 0.41 USDT

## ✅ YAP
- Yön LONG (kanaat %53). 4h mum 4,436 üstünde kapanırsa long.
- Stop 4,379 (−%1.37); hedef1 4,579, hedef2 4,648; R/R 2.28
- Kaldıraç ≤ 5x (öneri 2x), marj ≈ 15.0 USDT, riske atılan ≈ 0.41 USDT

## 🚫 YAPMA
- Direnç 4,436 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 4,392 hemen altta: kırılım kapanışı görmeden short açma
- Piyasa emriyle kovalama; sadece koşul gerçekleşince gir
- Stop'u aşağı taşıma; hedef1'de yarısını kapat, kalanını başa-baş stopla taşı

## 🔀 EĞER … İSE
- EĞER 4h kapanış 4,392 altına inerse → long fikri iptal, 4,375 riski
- EĞER 4h kapanış 4,436 üstüne çıkarsa → hedef 4,457

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| r2 | 4,457 | +1.07% |
| r1 | 4,436 | +0.59% |
| high_24h | 4,423 | +0.30% |
| s1 | 4,392 | -0.41% |
| s2 | 4,375 | -0.80% |
| low_24h | 4,366 | -1.01% |
| ema200_1d | 4,344 | -1.49% |
| ema_support | 4,344 | -1.49% |
| ema20_1d | 4,269 | -3.20% |
| ema50_1d | 4,210 | -4.53% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %1.49 (yüzdelik 0), 30g gerçekleşen vol %24. Önerilen max kaldıraç 5x, stop mesafesi ≈ %3.7.
- Günlük ATR %1.49 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %13.8 (yüzdelik 82) → genişlemiş, hareket başlamış
- 4h ATR %0.46
- Metrikler: atr_pct_1d=1.49, atr_pct_4h=0.46, atr_rank=0, bb_width_pct=13.81, bb_width_rank=82, realized_vol_30d=24.5, regime=DÜŞÜK, stop_pct=3.72, max_leverage=5

### 📈 Trend & EMA Çizgileri Ajanı — GÜÇLÜ BOĞA (bias +0.80, güven 80)
Fiyat 200g EMA (~4,344) yakınında; yakın destek 200g EMA ~4,344. Çoklu zaman dilimi eğilimi: GÜÇLÜ BOĞA.
- Günlük: fiyat EMA200'ün üstünde, karışık dizilim, EMA50 eğimi %+1.86/10bar, ADX 35
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.37/10bar, ADX 31
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.19/10bar, ADX 27
- Metrikler: adx_1d=35, ema50_slope_1d=1.86

### 🕯️ Mum Yapısı Ajanı — NÖTR (bias +0.05, güven 37)
Mum yapısı nötr: aralığın %12'inde kapandı, gövde %60, üst fitil %28, alt fitil %12; son 5 mumun 2'i yeşil; HH+HL (yükselen yapı)
- Günlük son mum: aralığın %12'inde kapandı, gövde %60, üst fitil %28, alt fitil %12; son 5 mumun 2'i yeşil; HH+HL (yükselen yapı)
- 4 saatlik son mum: aralığın %73'inde kapandı, gövde %24, üst fitil %3, alt fitil %73 — iç bar (sıkışma); son 5 mumun 3'i yeşil; karışık yapı
- Metrikler: clv_1d=0.12, structure_1d=HH+HL (yükselen yapı), clv_4h=0.73, structure_4h=karışık yapı

### 📊 Hacim Ajanı — GÜÇLÜ BOĞA (bias +0.55, güven 59)
Hacim güçlü boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.17 katı (düşük); alım/satım hacmi oranı 1.71; OBV 20-bar eğimi +36.5%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 2.25 katı (yüksek); alım/satım hacmi oranı 1.90; OBV 20-bar eğimi +35.4%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.17, updown_vol_1d=1.71, vol_ratio_4h=2.25, updown_vol_4h=1.9

### 🧱 Destek/Direnç Ajanı — NÖTR (bias -0.03, güven 55)
Yakın direnç 4,436 (+0.6%); yakın destek 4,392 (−0.4%). Fiyat son aralığın %57'inde.
- 4,436 üstünde 4h kapanış → hedef 4,457
- 4,392 altında 4h kapanış → risk 4,375
- 20 günlük en yüksek 4,441, en düşük 3,994
- ⚠️ Direnç 4,436 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 4,392 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[4435.96666667, 4457.25, 4501.465, 4579.19666667, 4648.32, 4757.215], supports=[4391.89, 4374.54333333, 4359.2325, 4314.8, 4300.275, 4216.29], range_position=0.57, range_high=4760.59, range_low=3941.68

### 🚀 Momentum Ajanı — GÜÇLÜ BOĞA (bias +0.79, güven 79)
Momentum güçlü boğa (RSI 1d/4h/1h: 64/63/58).
- Günlük: RSI14 64, MACD hist + (daralıyor), ROC10 %+2.8
- 4 saatlik: RSI14 63, MACD hist + (genişliyor), ROC10 %+0.8
- Saatlik: RSI14 58, MACD hist + (daralıyor), ROC10 %+0.3
- Metrikler: rsi_1d=64, macd_hist_1d=12.3291, rsi_4h=63, macd_hist_4h=3.6688, rsi_1h=58, macd_hist_1h=0.6798

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — BOĞA (bias +0.16, güven 50)
Canlı akış boğa: emir defteri alış payı %54, funding %0.0037, 24s %+0.83.
- 24s: %+0.83, aralık 4,366–4,423, fiyat aralığın %77'inde, hacim 15M USDT
- Emir defteri (ilk 20 kademe): alış 47K / satış 39K USDT → alış payı %54
- Funding %0.0037 / 8s (yıllık ≈ %4) → nötr
- Açık pozisyon (OI): 14,992 PAXG
- Global long/short hesap oranı 1.60 (long %62)
- Metrikler: chg24_pct=0.83, high24=4423.08, low24=4365.54, pos24=0.77, vol24_usdt=14507442, ob_imbalance=0.54, spread_pct=0.0002, funding_pct=0.0037, funding_annual_pct=4.1, open_interest=14991.901, long_short_ratio=1.6, long_pct=61.6

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.