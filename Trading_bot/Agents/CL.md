---
symbol: CL/USDT
verdict: BEKLE
conviction: 29
price: 84.06
updated: 2026-08-17T21:18:39+00:00
tags: [trading, agents]
---
# 🧠 CL/USDT — Ajan Raporu (⚪ BEKLE, kanaat %29)
> ⚪ BEKLE · kanaat %29 · 4/5 yönlü ajan hemfikir · fiyat 84.06 · trend nötr  ·  2026-08-18 00:19

Şema: [[Agents/CL.canvas]] · Backtest/karar: [[Coins/CL]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/CL.png]]

🔎 Tarayıcı: LONG skor **81**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 3x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 84.62 üstünde kapanış (long) / destek 82.10 altında kapanış (short)

## 🚫 YAPMA
- Direnç 84.62 hemen üstte: kırılım kapanışı görmeden long açma

## 🔀 EĞER … İSE
- EĞER 4h mum 84.62 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 85.80)
- EĞER 4h mum 82.10 altında hacimle kapanırsa → short senaryosu açılır (hedef 81.31)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| r2 | 85.80 | +2.07% |
| r1 | 84.62 | +0.67% |
| high_24h | 84.20 | +0.17% |
| ema50_1d | 80.94 | -3.71% |
| ema200_1d | nan | +nan% |
| ema_support | 83.53 | -0.63% |
| s1 | 82.10 | -2.34% |
| s2 | 81.31 | -3.27% |
| ema20_1d | 80.87 | -3.80% |
| low_24h | 80.86 | -3.81% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %3.90 (yüzdelik 14), 30g gerçekleşen vol %56. Önerilen max kaldıraç 3x, stop mesafesi ≈ %9.8.
- Günlük ATR %3.90 → son 200 günün %14'inden yüksek
- Bollinger bant genişliği %16.2 (yüzdelik 20) → normal
- 4h ATR %1.16
- Metrikler: atr_pct_1d=3.9, atr_pct_4h=1.16, atr_rank=14, bb_width_pct=16.18, bb_width_rank=20, realized_vol_30d=55.6, regime=DÜŞÜK, stop_pct=9.75, max_leverage=3

### 📈 Trend & EMA Çizgileri Ajanı — NÖTR (bias +0.10, güven 45)
Fiyat 100g EMA (~83.53) yakınında; yakın destek 100g EMA ~83.53. Çoklu zaman dilimi eğilimi: NÖTR.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %-0.25/10bar, ADX 14
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.43/10bar, ADX 16
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.57/10bar, ADX 27
- Metrikler: adx_1d=14, ema50_slope_1d=-0.25

### 🕯️ Mum Yapısı Ajanı — BOĞA (bias +0.49, güven 57)
Mum yapısı boğa: aralığın %66'inde kapandı, gövde %63, üst fitil %34, alt fitil %4; son 5 mumun 2'i yeşil; karışık yapı
- Günlük son mum: aralığın %66'inde kapandı, gövde %63, üst fitil %34, alt fitil %4; son 5 mumun 2'i yeşil; karışık yapı
- 4 saatlik son mum: aralığın %91'inde kapandı, gövde %75, üst fitil %9, alt fitil %17; son 5 mumun 3'i yeşil; HH+HL (yükselen yapı)
- Metrikler: clv_1d=0.66, structure_1d=karışık yapı, clv_4h=0.91, structure_4h=HH+HL (yükselen yapı)

### 📊 Hacim Ajanı — BOĞA (bias +0.15, güven 41)
Hacim boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.11 katı (düşük); alım/satım hacmi oranı 0.63; OBV 20-bar eğimi -71.2%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 2.19 katı (yüksek); alım/satım hacmi oranı 2.85; OBV 20-bar eğimi +14.5%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.11, updown_vol_1d=0.63, vol_ratio_4h=2.19, updown_vol_4h=2.85

### 🧱 Destek/Direnç Ajanı — AYI (bias -0.26, güven 55)
Yakın direnç 84.62 (+0.7%); yakın destek 82.10 (−2.4%). Fiyat son aralığın %39'inde.
- 84.62 üstünde 4h kapanış → hedef 85.80
- 82.10 altında 4h kapanış → risk 81.31
- 20 günlük en yüksek 88.27, en düşük 74.32
- ⚠️ Direnç 84.62 hemen üstte: kırılım kapanışı görmeden long açma
- Metrikler: resistances=[84.625, 85.80333333, 86.35, 88.132, 88.675, 89.4], supports=[82.09666667, 81.31, 80.65333333, 79.724, 78.7, 77.86666667], range_position=0.39, range_high=110.75, range_low=67.07

### 🚀 Momentum Ajanı — GÜÇLÜ BOĞA (bias +0.88, güven 83)
Momentum güçlü boğa (RSI 1d/4h/1h: 52/71/70).
- Günlük: RSI14 52, MACD hist + (genişliyor), ROC10 %+4.6
- 4 saatlik: RSI14 71, MACD hist + (genişliyor), ROC10 %+3.1
- Saatlik: RSI14 70, MACD hist + (genişliyor), ROC10 %+2.5
- Metrikler: rsi_1d=52, macd_hist_1d=0.0861, rsi_4h=71, macd_hist_4h=0.1691, rsi_1h=70, macd_hist_1h=0.2478

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — BOĞA (bias +0.21, güven 50)
Canlı akış boğa: emir defteri alış payı %52, funding %-0.0037, 24s %+3.17.
- 24s: %+3.17, aralık 80.86–84.20, fiyat aralığın %96'inde, hacim 613M USDT
- Emir defteri (ilk 20 kademe): alış 1,667K / satış 1,516K USDT → alış payı %52
- Funding %-0.0037 / 8s (yıllık ≈ %-4) → nötr
- Açık pozisyon (OI): 2,109,790 CL
- Global long/short hesap oranı 1.00 (long %50)
- Metrikler: chg24_pct=3.17, high24=84.2, low24=80.86, pos24=0.96, vol24_usdt=612521680, ob_imbalance=0.52, spread_pct=0.0119, funding_pct=-0.0037, funding_annual_pct=-4.0, open_interest=2109789.69, long_short_ratio=1.0, long_pct=50.0

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.