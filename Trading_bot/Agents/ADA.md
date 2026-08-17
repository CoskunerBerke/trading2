---
symbol: ADA/USDT
verdict: SHORT
conviction: 38
price: 0.1742
updated: 2026-08-17T20:53:12+00:00
tags: [trading, agents]
---
# 🧠 ADA/USDT — Ajan Raporu (🔴 SHORT, kanaat %38)
> 🔴 SHORT · kanaat %38 · 4/5 yönlü ajan hemfikir · fiyat 0.1742 · trend ayı  ·  2026-08-17 23:53

Şema: [[Agents/ADA.canvas]] · Backtest/karar: [[Coins/ADA]] · [[Agents/Baş Yönetici]]

## 🎯 Futures planı
- Yön: **SHORT** · ✅ geçerli
- Tetik: 4h mum 0.1727 altında kapanırsa short (kırılım)
- Giriş ~0.1725 · Stop 0.1806 (%4.69) · Hedef1 0.1564 · Hedef2 0.1483 · R/R 2.0
- Kaldıraç ≤ 2x (öneri 1x) · marj ≈ 15.0 USDT · notional ≈ 15.0 USDT · riske atılan ≈ 0.7 USDT

## ✅ YAP
- Yön SHORT (kanaat %38). 4h mum 0.1727 altında kapanırsa short.
- Stop 0.1806 (+%4.69); hedef1 0.1564, hedef2 0.1483; R/R 2.0
- Kaldıraç ≤ 2x (öneri 1x), marj ≈ 15.0 USDT, riske atılan ≈ 0.7 USDT

## 🚫 YAPMA
- Destek 0.1727 hemen altta: kırılım kapanışı görmeden short açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Dipten short kovalama; sadece koşul gerçekleşince gir
- Short'ta funding pozitifse taşıma maliyeti lehine; negatifse squeeze riski — pozisyonu uzun tutma
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h kapanış 0.1770 üstüne çıkarsa → short fikri iptal, 0.1787 riski
- EĞER 4h kapanış 0.1727 altına inerse → hedef 0.1704

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 0.2498 | +43.38% |
| ema20_1d | 0.1820 | +4.46% |
| ema50_1d | 0.1800 | +3.36% |
| ema_resistance | 0.1800 | +3.36% |
| r2 | 0.1787 | +2.58% |
| high_24h | 0.1779 | +2.12% |
| r1 | 0.1770 | +1.61% |
| s1 | 0.1727 | -0.86% |
| low_24h | 0.1725 | -0.98% |
| s2 | 0.1704 | -2.18% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %4.97 (yüzdelik 20), 30g gerçekleşen vol %56. Önerilen max kaldıraç 2x, stop mesafesi ≈ %12.4.
- Günlük ATR %4.97 → son 200 günün %20'inden yüksek
- Bollinger bant genişliği %26.3 (yüzdelik 64) → normal
- 4h ATR %1.55
- Metrikler: atr_pct_1d=4.97, atr_pct_4h=1.55, atr_rank=20, bb_width_pct=26.26, bb_width_rank=64, realized_vol_30d=56.2, regime=DÜŞÜK, stop_pct=12.43, max_leverage=2

### 📈 Trend & EMA Çizgileri Ajanı — AYI (bias -0.43, güven 61)
Fiyat 50g EMA (~0.1800) yakınında; yakın direnç 50g EMA ~0.1800. Çoklu zaman dilimi eğilimi: AYI.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %+1.65/10bar, ADX 19
- 4 saatlik: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %-1.56/10bar, ADX 36
- Saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.54/10bar, ADX 32
- Metrikler: adx_1d=19, ema50_slope_1d=1.65

### 🕯️ Mum Yapısı Ajanı — GÜÇLÜ AYI (bias -0.83, güven 72)
Mum yapısı güçlü ayı: aralığın %6'inde kapandı, gövde %32, üst fitil %62, alt fitil %6 — kayan yıldız (satıcı reddi); son 5 mumun 0'i yeşil; k
- Günlük son mum: aralığın %6'inde kapandı, gövde %32, üst fitil %62, alt fitil %6 — kayan yıldız (satıcı reddi); son 5 mumun 0'i yeşil; karışık yapı
- 4 saatlik son mum: aralığın %9'inde kapandı, gövde %64, üst fitil %27, alt fitil %9; son 5 mumun 2'i yeşil; LH+LL (alçalan yapı)
- Metrikler: clv_1d=0.06, structure_1d=karışık yapı, clv_4h=0.09, structure_4h=LH+LL (alçalan yapı)

### 📊 Hacim Ajanı — AYI (bias -0.20, güven 44)
Hacim ayı: satıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.47 katı (düşük); alım/satım hacmi oranı 1.17; OBV 20-bar eğimi +2.6%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 1.15 katı (normal); alım/satım hacmi oranı 0.73; OBV 20-bar eğimi -7.5%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.47, updown_vol_1d=1.17, vol_ratio_4h=1.15, updown_vol_4h=0.73

### 🧱 Destek/Direnç Ajanı — BOĞA (bias +0.40, güven 55)
Yakın direnç 0.1770 (+1.6%); yakın destek 0.1727 (−0.9%). Fiyat son aralığın %24'inde.
- 0.1770 üstünde 4h kapanış → hedef 0.1787
- 0.1727 altında 4h kapanış → risk 0.1704
- 20 günlük en yüksek 0.2117, en düşük 0.1533
- ⚠️ Destek 0.1727 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[0.177, 0.1787, 0.18098333, 0.18365, 0.1863, 0.19], supports=[0.1727, 0.1704, 0.169, 0.166925, 0.1641, 0.161025], range_position=0.24, range_high=0.2887, range_low=0.1382

### 🚀 Momentum Ajanı — GÜÇLÜ AYI (bias -0.73, güven 76)
Momentum güçlü ayı (RSI 1d/4h/1h: 44/33/40).
- Günlük: RSI14 44, MACD hist − (genişliyor), ROC10 %-13.1
- 4 saatlik: fiyat yeni düşük, RSI değil → pozitif uyumsuzluk
- 4 saatlik: RSI14 33, MACD hist + (daralıyor), ROC10 %-1.9
- Saatlik: RSI14 40, MACD hist − (genişliyor), ROC10 %-0.4
- Metrikler: rsi_1d=44, macd_hist_1d=-0.0024, rsi_4h=33, macd_hist_4h=0.0001, rsi_1h=40, macd_hist_1h=-0.0001

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji Donchian Kırılım · ❌ WFO OOS (4 adım): Sharpe -0.71 < 0.3, PF 0.59 < 1.1, 22 işlem, getiri -6.9% vs B&H -75.4%
- Strateji durumu: FLAT, sinyal: WATCH, skor 29
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=donchian(entry_len=40, exit_len=20), wfo_sharpe=-0.7106, wfo_pf=0.5914, wfo_trades=22, strategy_position=FLAT, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias -0.04, güven 50)
Canlı akış nötr: emir defteri alış payı %53, funding %0.0011, 24s %-1.64.
- 24s: %-1.64, aralık 0.1725–0.1779, fiyat aralığın %31'inde, hacim 12M USDT
- Emir defteri (ilk 20 kademe): alış 383K / satış 339K USDT → alış payı %53
- Funding %0.0011 / 8s (yıllık ≈ %1) → nötr
- Açık pozisyon (OI): 491,584,499 ADA
- Global long/short hesap oranı 1.81 (long %64)
- Metrikler: chg24_pct=-1.64, high24=0.1779, low24=0.1725, pos24=0.31, vol24_usdt=12092658, ob_imbalance=0.53, spread_pct=0.0574, funding_pct=0.0011, funding_annual_pct=1.2, open_interest=491584499.0, long_short_ratio=1.81, long_pct=64.4

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.