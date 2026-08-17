---
symbol: XRP/USDT
verdict: SHORT
conviction: 44
price: 1.0027
updated: 2026-08-17T20:53:02+00:00
tags: [trading, agents]
---
# 🧠 XRP/USDT — Ajan Raporu (🔴 SHORT, kanaat %44)
> 🔴 SHORT · kanaat %44 · 3/4 yönlü ajan hemfikir · fiyat 1.00 · trend güçlü ayı  ·  2026-08-17 23:53

Şema: [[Agents/XRP.canvas]] · Backtest/karar: [[Coins/XRP]] · [[Agents/Baş Yönetici]]

## 🎯 Futures planı
- Yön: **SHORT** · ✅ geçerli
- Tetik: 4h mum 0.9995 altında kapanırsa short (kırılım)
- Giriş ~0.9986 · Stop 1.02 (%2.4) · Hedef1 0.9507 · Hedef2 0.9268 · R/R 2.0
- Kaldıraç ≤ 4x (öneri 2x) · marj ≈ 15.0 USDT · notional ≈ 30.0 USDT · riske atılan ≈ 0.72 USDT

## ✅ YAP
- Yön SHORT (kanaat %44). 4h mum 0.9995 altında kapanırsa short.
- Stop 1.02 (+%2.4); hedef1 0.9507, hedef2 0.9268; R/R 2.0
- Kaldıraç ≤ 4x (öneri 2x), marj ≈ 15.0 USDT, riske atılan ≈ 0.72 USDT

## 🚫 YAPMA
- Direnç 1.01 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 0.9995 hemen altta: kırılım kapanışı görmeden short açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Kitle çok long (3.2): kontraryen risk, long'da sıkı stop
- Dipten short kovalama; sadece koşul gerçekleşince gir
- Short'ta funding pozitifse taşıma maliyeti lehine; negatifse squeeze riski — pozisyonu uzun tutma
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h kapanış 1.01 üstüne çıkarsa → short fikri iptal, 1.01 riski
- EĞER 4h kapanış 0.9995 altına inerse → hedef 0.9915

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 1.35 | +34.99% |
| ema50_1d | 1.08 | +7.75% |
| ema20_1d | 1.04 | +3.31% |
| ema_resistance | 1.04 | +3.31% |
| r2 | 1.01 | +1.14% |
| high_24h | 1.01 | +0.59% |
| r1 | 1.01 | +0.53% |
| s1 | 0.9995 | -0.31% |
| s2 | 0.9915 | -1.12% |
| low_24h | 0.9882 | -1.45% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %2.73 (yüzdelik 0), 30g gerçekleşen vol %31. Önerilen max kaldıraç 4x, stop mesafesi ≈ %6.8.
- Günlük ATR %2.73 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %11.7 (yüzdelik 34) → normal
- 4h ATR %0.80
- Metrikler: atr_pct_1d=2.73, atr_pct_4h=0.8, atr_rank=0, bb_width_pct=11.74, bb_width_rank=34, realized_vol_30d=30.6, regime=DÜŞÜK, stop_pct=6.84, max_leverage=4

### 📈 Trend & EMA Çizgileri Ajanı — GÜÇLÜ AYI (bias -0.86, güven 83)
Fiyat 20g EMA (~1.04) yakınında; yakın direnç 20g EMA ~1.04. Çoklu zaman dilimi eğilimi: GÜÇLÜ AYI.
- Günlük: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-3.01/10bar, ADX 25
- 4 saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.54/10bar, ADX 32
- Saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %+0.03/10bar, ADX 12
- Metrikler: adx_1d=25, ema50_slope_1d=-3.01

### 🕯️ Mum Yapısı Ajanı — GÜÇLÜ AYI (bias -0.58, güven 61)
Mum yapısı güçlü ayı: aralığın %25'inde kapandı, gövde %68, üst fitil %7, alt fitil %25; son 5 mumun 2'i yeşil; LH+LL (alçalan yapı)
- Günlük son mum: aralığın %25'inde kapandı, gövde %68, üst fitil %7, alt fitil %25; son 5 mumun 2'i yeşil; LH+LL (alçalan yapı)
- 4 saatlik son mum: aralığın %28'inde kapandı, gövde %70, üst fitil %3, alt fitil %28 — iç bar (sıkışma); son 5 mumun 3'i yeşil; LH+LL (alçalan yapı)
- Metrikler: clv_1d=0.25, structure_1d=LH+LL (alçalan yapı), clv_4h=0.28, structure_4h=LH+LL (alçalan yapı)

### 📊 Hacim Ajanı — NÖTR (bias -0.15, güven 41)
Hacim nötr: dengeli
- Günlük: son bar hacmi 20-bar ortalamasının 0.48 katı (düşük); alım/satım hacmi oranı 0.66; OBV 20-bar eğimi -3.6%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 0.97 katı (normal); alım/satım hacmi oranı 1.26; OBV 20-bar eğimi +0.2%; hareket hacimle onaysız
- Metrikler: vol_ratio_1d=0.48, updown_vol_1d=0.66, vol_ratio_4h=0.97, updown_vol_4h=1.26

### 🧱 Destek/Direnç Ajanı — BOĞA (bias +0.19, güven 55)
Yakın direnç 1.01 (+0.5%); yakın destek 0.9995 (−0.3%). Fiyat son aralığın %3'inde.
- 1.01 üstünde 4h kapanış → hedef 1.01
- 0.9995 altında 4h kapanış → risk 0.9915
- 20 günlük en yüksek 1.12, en düşük 0.9862
- ⚠️ Direnç 1.01 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 0.9995 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[1.008, 1.01413333, 1.02496667, 1.0279, 1.04593333, 1.0508], supports=[0.99955, 0.9915, 0.9872], range_position=0.03, range_high=1.5496, range_low=0.9862

### 🚀 Momentum Ajanı — AYI (bias -0.28, güven 53)
Momentum ayı (RSI 1d/4h/1h: 34/46/51).
- Günlük: RSI14 34, MACD hist − (daralıyor), ROC10 %-4.0
- 4 saatlik: RSI14 46, MACD hist + (daralıyor), ROC10 %-0.1
- Saatlik: RSI14 51, MACD hist + (daralıyor), ROC10 %+0.5
- Metrikler: rsi_1d=34, macd_hist_1d=-0.0035, rsi_4h=46, macd_hist_4h=0.001, rsi_1h=51, macd_hist_1h=0.0001

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji EMA Geri Çekilme · ❌ WFO OOS (4 adım): Sharpe -0.97 < 0.3, PF 0.46 < 1.1, 26 işlem, getiri -6.2% vs B&H -64.5%
- Strateji durumu: FLAT, sinyal: WATCH, skor 35
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=ema_pullback(fast=10, slow=50, trend=200), wfo_sharpe=-0.9673, wfo_pf=0.458, wfo_trades=26, strategy_position=FLAT, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias -0.14, güven 50)
Canlı akış nötr: emir defteri alış payı %48, funding %0.0004, 24s %+0.31.
- 24s: %+0.31, aralık 0.9882–1.01, fiyat aralığın %71'inde, hacim 52M USDT
- Emir defteri (ilk 20 kademe): alış 724K / satış 774K USDT → alış payı %48
- Funding %0.0004 / 8s (yıllık ≈ %0) → nötr
- Açık pozisyon (OI): 406,183,061 XRP
- Global long/short hesap oranı 3.17 (long %76)
- ⚠️ Kitle çok long (3.2): kontraryen risk, long'da sıkı stop
- Metrikler: chg24_pct=0.31, high24=1.0086, low24=0.9882, pos24=0.71, vol24_usdt=51696578, ob_imbalance=0.48, spread_pct=0.01, funding_pct=0.0004, funding_annual_pct=0.4, open_interest=406183061.2, long_short_ratio=3.17, long_pct=76.0

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.