---
symbol: FET/USDT
verdict: SHORT
conviction: 58
price: 0.1243
updated: 2026-08-17T21:19:05+00:00
tags: [trading, agents]
---
# 🧠 FET/USDT — Ajan Raporu (🔴 SHORT, kanaat %58)
> 🔴 SHORT · kanaat %58 · 4/6 yönlü ajan hemfikir · fiyat 0.1243 · trend güçlü ayı  ·  2026-08-18 00:19

Şema: [[Agents/FET.canvas]] · Backtest/karar: [[Coins/FET]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/FET.png]]

🔎 Tarayıcı: SHORT skor **63**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
- Yön: **SHORT** · ✅ geçerli
- Tetik: 4h mum 0.1228 altında kapanırsa short (kırılım)
- Giriş ~0.1227 · Stop 0.1300 (%5.95) · Hedef1 0.1081 · Hedef2 0.1008 · R/R 2.0
- Kaldıraç ≤ 2x (öneri 1x) · marj ≈ 15.0 USDT · notional ≈ 15.0 USDT · riske atılan ≈ 0.89 USDT

## ✅ YAP
- Yön SHORT (kanaat %58). 4h mum 0.1228 altında kapanırsa short.
- Stop 0.1300 (+%5.95); hedef1 0.1081, hedef2 0.1008; R/R 2.0
- Kaldıraç ≤ 2x (öneri 1x), marj ≈ 15.0 USDT, riske atılan ≈ 0.89 USDT

## 🚫 YAPMA
- Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Direnç 0.1254 hemen üstte: kırılım kapanışı görmeden long açma
- Dipten short kovalama; sadece koşul gerçekleşince gir
- Short'ta funding pozitifse taşıma maliyeti lehine; negatifse squeeze riski — pozisyonu uzun tutma

## 🔀 EĞER … İSE
- EĞER 4h kapanış 0.1254 üstüne çıkarsa → short fikri iptal, 0.1317 riski
- EĞER 4h kapanış 0.1228 altına inerse → hedef sonraki destek

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 0.2094 | +68.44% |
| ema50_1d | 0.1529 | +23.02% |
| ema20_1d | 0.1377 | +10.81% |
| ema_resistance | 0.1377 | +10.81% |
| r2 | 0.1317 | +5.96% |
| r1 | 0.1254 | +0.88% |
| high_24h | 0.1244 | +0.08% |
| s1 | 0.1228 | -1.21% |
| low_24h | 0.1192 | -4.10% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %5.51 (yüzdelik 8), 30g gerçekleşen vol %56. Önerilen max kaldıraç 2x, stop mesafesi ≈ %13.8.
- Günlük ATR %5.51 → son 200 günün %8'inden yüksek
- Bollinger bant genişliği %16.8 (yüzdelik 8) → sıkışma, patlama yakın olabilir
- 4h ATR %1.98
- ⚠️ Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Metrikler: atr_pct_1d=5.51, atr_pct_4h=1.98, atr_rank=8, bb_width_pct=16.8, bb_width_rank=8, realized_vol_30d=56.2, regime=DÜŞÜK, stop_pct=13.78, max_leverage=2

### 📈 Trend & EMA Çizgileri Ajanı — GÜÇLÜ AYI (bias -1.00, güven 90)
Fiyat 20g EMA (~0.1377) yakınında; yakın direnç 20g EMA ~0.1377. Çoklu zaman dilimi eğilimi: GÜÇLÜ AYI.
- Günlük: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-6.14/10bar, ADX 32
- 4 saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-2.97/10bar, ADX 36
- Saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.63/10bar, ADX 27
- Metrikler: adx_1d=32, ema50_slope_1d=-6.14

### 🕯️ Mum Yapısı Ajanı — GÜÇLÜ AYI (bias -0.71, güven 66)
Mum yapısı güçlü ayı: aralığın %13'inde kapandı, gövde %87, üst fitil %0, alt fitil %13; son 5 mumun 2'i yeşil; LH+LL (alçalan yapı)
- Günlük son mum: aralığın %13'inde kapandı, gövde %87, üst fitil %0, alt fitil %13; son 5 mumun 2'i yeşil; LH+LL (alçalan yapı)
- 4 saatlik son mum: aralığın %19'inde kapandı, gövde %37, üst fitil %44, alt fitil %19; son 5 mumun 3'i yeşil; LH+LL (alçalan yapı)
- Metrikler: clv_1d=0.13, structure_1d=LH+LL (alçalan yapı), clv_4h=0.19, structure_4h=LH+LL (alçalan yapı)

### 📊 Hacim Ajanı — GÜÇLÜ AYI (bias -0.70, güven 66)
Hacim güçlü ayı: satıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 1.19 katı (normal); alım/satım hacmi oranı 0.44; OBV 20-bar eğimi -27.5%; hareket hacimle onaylı
- 4 saatlik: son bar hacmi 20-bar ortalamasının 0.82 katı (normal); alım/satım hacmi oranı 0.42; OBV 20-bar eğimi -28.8%; hareket hacimle onaysız
- Metrikler: vol_ratio_1d=1.19, updown_vol_1d=0.44, vol_ratio_4h=0.82, updown_vol_4h=0.42

### 🧱 Destek/Direnç Ajanı — BOĞA (bias +0.20, güven 55)
Yakın direnç 0.1254 (+0.9%); yakın destek 0.1228 (−1.2%). Fiyat son aralığın %1'inde.
- 0.1254 üstünde 4h kapanış → hedef 0.1317
- 0.1228 altında 4h kapanış → risk aralık altı 0.1228
- 20 günlük en yüksek 0.1606, en düşük 0.1265
- ⚠️ Direnç 0.1254 hemen üstte: kırılım kapanışı görmeden long açma
- Metrikler: resistances=[0.1254, 0.13171429, 0.1341, 0.13676667, 0.13905, 0.1401], supports=[0.1228], range_position=0.01, range_high=0.2888, range_low=0.1228

### 🚀 Momentum Ajanı — GÜÇLÜ AYI (bias -0.51, güven 65)
Momentum güçlü ayı (RSI 1d/4h/1h: 26/35/55).
- Günlük: RSI14 26, MACD hist − (genişliyor), ROC10 %-10.3
- 4 saatlik: RSI14 35, MACD hist − (daralıyor), ROC10 %-0.9
- Saatlik: RSI14 55, MACD hist + (genişliyor), ROC10 %+2.7
- Metrikler: rsi_1d=26, macd_hist_1d=-0.0004, rsi_4h=35, macd_hist_4h=-0.0002, rsi_1h=55, macd_hist_1h=0.0004

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — BOĞA (bias +0.34, güven 50)
Canlı akış boğa: emir defteri alış payı %63, funding %0.0048, 24s %+1.55.
- 24s: %+1.55, aralık 0.1192–0.1244, fiyat aralığın %98'inde, hacim 10M USDT
- Emir defteri (ilk 20 kademe): alış 230K / satış 137K USDT → alış payı %63
- Funding %0.0048 / 8s (yıllık ≈ %5) → nötr
- Açık pozisyon (OI): 164,807,635 FET
- Global long/short hesap oranı 1.05 (long %51)
- Metrikler: chg24_pct=1.55, high24=0.1244, low24=0.1192, pos24=0.98, vol24_usdt=9552688, ob_imbalance=0.63, spread_pct=0.0805, funding_pct=0.0048, funding_annual_pct=5.3, open_interest=164807635.0, long_short_ratio=1.05, long_pct=51.3

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.