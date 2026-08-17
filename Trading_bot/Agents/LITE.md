---
symbol: LITE/USDT
verdict: BEKLE
conviction: 33
price: 970.27
updated: 2026-08-17T21:39:03+00:00
tags: [trading, agents]
---
# 🧠 LITE/USDT — Ajan Raporu (⚪ BEKLE, kanaat %33)
> ⚪ BEKLE · kanaat %33 · 3/3 yönlü ajan hemfikir · fiyat 970.27 · trend boğa  ·  2026-08-18 00:39

Şema: [[Agents/LITE.canvas]] · Backtest/karar: [[Coins/LITE]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/LITE.png]]

🔎 Tarayıcı: LONG skor **67**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 1x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 998.63 üstünde kapanış (long) / destek 940.83 altında kapanış (short)

## 🚫 YAPMA

## 🔀 EĞER … İSE
- EĞER 4h mum 998.63 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 1,028)
- EĞER 4h mum 940.83 altında hacimle kapanırsa → short senaryosu açılır (hedef 926.37)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| r2 | 1,028 | +5.90% |
| high_24h | 1,001 | +3.12% |
| r1 | 998.63 | +2.92% |
| s1 | 940.83 | -3.03% |
| s2 | 926.37 | -4.53% |
| ema20_1d | 853.40 | -12.04% |
| ema50_1d | 826.20 | -14.85% |
| ema200_1d | nan | +nan% |
| low_24h | 923.47 | -4.82% |
| ema_support | 853.40 | -12.04% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %7.16 (yüzdelik 9), 30g gerçekleşen vol %121. Önerilen max kaldıraç 1x, stop mesafesi ≈ %17.9.
- Günlük ATR %7.16 → son 200 günün %9'inden yüksek
- Bollinger bant genişliği %47.8 (yüzdelik 97) → genişlemiş, hareket başlamış
- 4h ATR %2.07
- Metrikler: atr_pct_1d=7.16, atr_pct_4h=2.07, atr_rank=9, bb_width_pct=47.81, bb_width_rank=97, realized_vol_30d=121.0, regime=DÜŞÜK, stop_pct=17.89, max_leverage=1

### 📈 Trend & EMA Çizgileri Ajanı — BOĞA (bias +0.45, güven 62)
Fiyat 20g EMA (~853.40) yakınında; yakın destek 20g EMA ~853.40. Çoklu zaman dilimi eğilimi: BOĞA.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %+4.50/10bar, ADX 18
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+2.17/10bar, ADX 32
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+1.45/10bar, ADX 31
- Metrikler: adx_1d=18, ema50_slope_1d=4.5

### 🕯️ Mum Yapısı Ajanı — NÖTR (bias -0.14, güven 41)
Mum yapısı nötr: aralığın %40'inde kapandı, gövde %40, üst fitil %60, alt fitil %0; son 5 mumun 3'i yeşil; karışık yapı
- Günlük son mum: aralığın %40'inde kapandı, gövde %40, üst fitil %60, alt fitil %0; son 5 mumun 3'i yeşil; karışık yapı
- 4 saatlik son mum: aralığın %13'inde kapandı, gövde %72, üst fitil %15, alt fitil %13 — iç bar (sıkışma); son 5 mumun 3'i yeşil; HH+HL (yükselen yapı)
- Metrikler: clv_1d=0.4, structure_1d=karışık yapı, clv_4h=0.13, structure_4h=HH+HL (yükselen yapı)

### 📊 Hacim Ajanı — GÜÇLÜ BOĞA (bias +0.55, güven 59)
Hacim güçlü boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.07 katı (düşük); alım/satım hacmi oranı 1.59; OBV 20-bar eğimi +182.0%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 2.84 katı (yüksek); alım/satım hacmi oranı 3.51; OBV 20-bar eğimi +48.1%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.07, updown_vol_1d=1.59, vol_ratio_4h=2.84, updown_vol_4h=3.51

### 🧱 Destek/Direnç Ajanı — NÖTR (bias -0.12, güven 55)
Yakın direnç 998.63 (+2.9%); yakın destek 940.83 (−3.1%). Fiyat son aralığın %80'inde.
- 998.63 üstünde 4h kapanış → hedef 1,028
- 940.83 altında 4h kapanış → risk 926.37
- 20 günlük en yüksek 967.43, en düşük 583.82
- Metrikler: resistances=[998.635, 1027.51, 1064.38], supports=[940.82666667, 926.365, 912.015, 896.69, 880.98333333, 870.375], range_position=0.8, range_high=1064.38, range_low=583.82

### 🚀 Momentum Ajanı — GÜÇLÜ BOĞA (bias +0.79, güven 79)
Momentum güçlü boğa (RSI 1d/4h/1h: 60/66/58).
- Günlük: RSI14 60, MACD hist + (daralıyor), ROC10 %+6.9
- 4 saatlik: RSI14 66, MACD hist + (genişliyor), ROC10 %+4.3
- Saatlik: RSI14 58, MACD hist + (daralıyor), ROC10 %+1.9
- Metrikler: rsi_1d=60, macd_hist_1d=10.8965, rsi_4h=66, macd_hist_4h=3.5196, rsi_1h=58, macd_hist_1h=1.307

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias +0.01, güven 50)
Canlı akış nötr: emir defteri alış payı %47, funding %0.0000, 24s %+3.98.
- 24s: %+3.98, aralık 923.47–1,001, fiyat aralığın %61'inde, hacim 66M USDT
- Emir defteri (ilk 20 kademe): alış 9K / satış 10K USDT → alış payı %47
- Funding %0.0000 / 8s (yıllık ≈ %0) → nötr
- Açık pozisyon (OI): 20,135 LITE
- Global long/short hesap oranı 0.88 (long %47)
- Metrikler: chg24_pct=3.98, high24=1000.52, low24=923.47, pos24=0.61, vol24_usdt=65853197, ob_imbalance=0.47, spread_pct=0.001, funding_pct=0.0, funding_annual_pct=0.0, open_interest=20134.87, long_short_ratio=0.88, long_pct=46.8

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.