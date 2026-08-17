---
symbol: SOXS/USDT
verdict: SHORT
conviction: 44
price: 38.37
updated: 2026-08-17T21:19:00+00:00
tags: [trading, agents]
---
# 🧠 SOXS/USDT — Ajan Raporu (🔴 SHORT, kanaat %44)
> 🔴 SHORT · kanaat %44 · 3/4 yönlü ajan hemfikir · fiyat 38.37 · trend güçlü ayı  ·  2026-08-18 00:19

Şema: [[Agents/SOXS.canvas]] · Backtest/karar: [[Coins/SOXS]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/SOXS.png]]

🔎 Tarayıcı: SHORT skor **64**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
- Yön: **SHORT** · ✅ geçerli
- Tetik: 39.42 direncine tepkide satıcı mumuyla short (kovalama) (geri çekilme)
- Giriş ~39.42 · Stop 41.63 (%5.62) · Hedef1 34.98 · Hedef2 32.76 · R/R 2.0
- Kaldıraç ≤ 5x (öneri 1x) · marj ≈ 15.0 USDT · notional ≈ 15.0 USDT · riske atılan ≈ 0.84 USDT

## ✅ YAP
- Yön SHORT (kanaat %44). 39.42 direncine tepkide satıcı mumuyla short (kovalama).
- Stop 41.63 (+%5.62); hedef1 34.98, hedef2 32.76; R/R 2.0
- Kaldıraç ≤ 5x (öneri 1x), marj ≈ 15.0 USDT, riske atılan ≈ 0.84 USDT

## 🚫 YAPMA
- Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Dipten short kovalama; sadece koşul gerçekleşince gir
- Short'ta funding pozitifse taşıma maliyeti lehine; negatifse squeeze riski — pozisyonu uzun tutma

## 🔀 EĞER … İSE
- EĞER 4h kapanış 39.42 üstüne çıkarsa → short fikri iptal, 40.33 riski
- EĞER 4h kapanış 37.61 altına inerse → hedef sonraki destek

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| high_24h | 40.45 | +5.42% |
| r2 | 40.33 | +5.12% |
| r1 | 39.42 | +2.72% |
| s1 | 37.61 | -1.98% |
| low_24h | 36.97 | -3.65% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %2.31 (yüzdelik 5), 30g gerçekleşen vol %37. Önerilen max kaldıraç 5x, stop mesafesi ≈ %5.8.
- Günlük ATR %2.31 → son 200 günün %5'inden yüksek
- Bollinger bant genişliği %9.5 (yüzdelik 11) → sıkışma, patlama yakın olabilir
- 4h ATR %2.31
- ⚠️ Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Metrikler: atr_pct_1d=2.31, atr_pct_4h=2.31, atr_rank=5, bb_width_pct=9.45, bb_width_rank=11, realized_vol_30d=37.2, regime=DÜŞÜK, stop_pct=5.78, max_leverage=5

### 📈 Trend & EMA Çizgileri Ajanı — GÜÇLÜ AYI (bias -1.00, güven 90)
Çoklu zaman dilimi eğilimi: GÜÇLÜ AYI.
- 4 saatlik: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %-2.23/10bar, ADX 35
- Saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-1.58/10bar, ADX 37

### 🕯️ Mum Yapısı Ajanı — NÖTR (bias +0.05, güven 37)
Mum yapısı nötr: aralığın %79'inde kapandı, gövde %76, üst fitil %21, alt fitil %3 — iç bar (sıkışma); son 5 mumun 1'i yeşil; LH+LL (alça
- 4 saatlik son mum: aralığın %79'inde kapandı, gövde %76, üst fitil %21, alt fitil %3 — iç bar (sıkışma); son 5 mumun 1'i yeşil; LH+LL (alçalan yapı)
- Metrikler: clv_4h=0.79, structure_4h=LH+LL (alçalan yapı)

### 📊 Hacim Ajanı — AYI (bias -0.40, güven 53)
Hacim ayı: satıcı hacmi baskın
- 4 saatlik: son bar hacmi 20-bar ortalamasının 2.47 katı (yüksek); alım/satım hacmi oranı 0.63; OBV 20-bar eğimi -25.8%; hareket hacimle onaylı
- Metrikler: vol_ratio_4h=2.47, updown_vol_4h=0.63

### 🧱 Destek/Direnç Ajanı — BOĞA (bias +0.19, güven 55)
Yakın direnç 39.42 (+2.7%); yakın destek 37.61 (−2.0%). Fiyat son aralığın %2'inde.
- 39.42 üstünde 4h kapanış → hedef 40.33
- 37.61 altında 4h kapanış → risk aralık altı 37.61
- 20 günlük en yüksek 77.50, en düşük 37.61
- Metrikler: resistances=[39.415, 40.33285714, 41.50333333, 42.2325, 43.125, 45.2825], supports=[37.61], range_position=0.02, range_high=77.5, range_low=37.61

### 🚀 Momentum Ajanı — AYI (bias -0.42, güven 61)
Momentum ayı (RSI 1d/4h/1h: -/38/45).
- 4 saatlik: RSI14 38, MACD hist − (daralıyor), ROC10 %-4.6
- Saatlik: RSI14 45, MACD hist + (genişliyor), ROC10 %+0.0
- Metrikler: rsi_4h=38, macd_hist_4h=-0.1821, rsi_1h=45, macd_hist_1h=0.0337

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias +0.14, güven 50)
Canlı akış nötr: emir defteri alış payı %65, funding %0.0000, 24s %-4.62.
- 24s: %-4.62, aralık 36.97–40.45, fiyat aralığın %40'inde, hacim 137M USDT
- Emir defteri (ilk 20 kademe): alış 494K / satış 265K USDT → alış payı %65
- Funding %0.0000 / 8s (yıllık ≈ %0) → nötr
- Açık pozisyon (OI): 295,257 SOXS
- Global long/short hesap oranı 1.91 (long %66)
- Metrikler: chg24_pct=-4.62, high24=40.45, low24=36.97, pos24=0.4, vol24_usdt=137043858, ob_imbalance=0.65, spread_pct=0.0261, funding_pct=0.0, funding_annual_pct=0.0, open_interest=295256.57, long_short_ratio=1.91, long_pct=65.6

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.