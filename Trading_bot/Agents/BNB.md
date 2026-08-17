---
symbol: BNB/USDT
verdict: BEKLE
conviction: 5
price: 605.35
updated: 2026-08-17T20:52:52+00:00
tags: [trading, agents]
---
# 🧠 BNB/USDT — Ajan Raporu (⚪ BEKLE, kanaat %5)
> ⚪ BEKLE · kanaat %5 · 2/3 yönlü ajan hemfikir · fiyat 605.35 · trend nötr  ·  2026-08-17 23:53

Şema: [[Agents/BNB.canvas]] · Backtest/karar: [[Coins/BNB]] · [[Agents/Baş Yönetici]]

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 5x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 609.55 üstünde kapanış (long) / destek 602.10 altında kapanış (short)

## 🚫 YAPMA
- Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- Direnç 609.55 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 602.10 hemen altta: kırılım kapanışı görmeden short açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h mum 609.55 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 612.33)
- EĞER 4h mum 602.10 altında hacimle kapanırsa → short senaryosu açılır (hedef 596.44)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 644.26 | +6.43% |
| ema_resistance | 644.26 | +6.43% |
| r2 | 612.33 | +1.15% |
| r1 | 609.55 | +0.69% |
| high_24h | 608.00 | +0.44% |
| ema_support | 602.92 | -0.40% |
| s1 | 602.10 | -0.54% |
| low_24h | 601.01 | -0.72% |
| ema20_1d | 596.97 | -1.39% |
| s2 | 596.44 | -1.47% |
| ema50_1d | 591.06 | -2.36% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %2.00 (yüzdelik 0), 30g gerçekleşen vol %22. Önerilen max kaldıraç 5x, stop mesafesi ≈ %5.0.
- Günlük ATR %2.00 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %8.4 (yüzdelik 27) → normal
- 4h ATR %0.59
- Metrikler: atr_pct_1d=2.0, atr_pct_4h=0.59, atr_rank=0, bb_width_pct=8.37, bb_width_rank=27, realized_vol_30d=22.4, regime=DÜŞÜK, stop_pct=5.0, max_leverage=5

### 📈 Trend & EMA Çizgileri Ajanı — NÖTR (bias -0.02, güven 41)
Fiyat 100g EMA (~602.92) yakınında; yakın destek 100g EMA ~602.92; yakın direnç 200g EMA ~644.26. Çoklu zaman dilimi eğilimi: NÖTR.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %+1.22/10bar, ADX 19
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.01/10bar, ADX 18
- Saatlik: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %-0.03/10bar, ADX 18
- ⚠️ Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- Metrikler: adx_1d=19, ema50_slope_1d=1.22

### 🕯️ Mum Yapısı Ajanı — AYI (bias -0.27, güven 47)
Mum yapısı ayı: aralığın %21'inde kapandı, gövde %68, üst fitil %11, alt fitil %21; son 5 mumun 1'i yeşil; HH+HL (yükselen yapı)
- Günlük son mum: aralığın %21'inde kapandı, gövde %68, üst fitil %11, alt fitil %21; son 5 mumun 1'i yeşil; HH+HL (yükselen yapı)
- 4 saatlik son mum: aralığın %10'inde kapandı, gövde %63, üst fitil %28, alt fitil %10; son 5 mumun 2'i yeşil; karışık yapı
- Metrikler: clv_1d=0.21, structure_1d=HH+HL (yükselen yapı), clv_4h=0.1, structure_4h=karışık yapı

### 📊 Hacim Ajanı — BOĞA (bias +0.35, güven 50)
Hacim boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.86 katı (normal); alım/satım hacmi oranı 1.68; OBV 20-bar eğimi +38.0%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 1.02 katı (normal); alım/satım hacmi oranı 0.82; OBV 20-bar eğimi -0.7%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.86, updown_vol_1d=1.68, vol_ratio_4h=1.02, updown_vol_4h=0.82

### 🧱 Destek/Direnç Ajanı — NÖTR (bias +0.07, güven 55)
Yakın direnç 609.55 (+0.7%); yakın destek 602.10 (−0.5%). Fiyat son aralığın %33'inde.
- 609.55 üstünde 4h kapanış → hedef 612.33
- 602.10 altında 4h kapanış → risk 596.44
- 20 günlük en yüksek 620.55, en düşük 562.03
- ⚠️ Direnç 609.55 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 602.10 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[609.545, 612.325, 614.99, 620.55, 628.22, 634.16333333], supports=[602.1, 596.44, 594.4925, 591.86, 588.0, 585.266], range_position=0.33, range_high=745.74, range_low=537.25

### 🚀 Momentum Ajanı — BOĞA (bias +0.30, güven 55)
Momentum boğa (RSI 1d/4h/1h: 57/46/47).
- Günlük: RSI14 57, MACD hist + (daralıyor), ROC10 %+1.8
- 4 saatlik: RSI14 46, MACD hist − (daralıyor), ROC10 %-0.2
- Saatlik: RSI14 47, MACD hist + (daralıyor), ROC10 %+0.2
- Metrikler: rsi_1d=57, macd_hist_1d=0.7713, rsi_4h=46, macd_hist_4h=-0.3341, rsi_1h=47, macd_hist_1h=0.1716

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji EMA Trend Takibi · ❌ WFO OOS (4 adım): Sharpe -0.59 < 0.3, PF 0.59 < 1.1, 14 işlem, getiri -3.1% vs B&H -11.2%
- Strateji durumu: FLAT, sinyal: WATCH, skor 53
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=ema_trend(fast=9, slow=100, adx_min=20), wfo_sharpe=-0.5934, wfo_pf=0.5911, wfo_trades=14, strategy_position=FLAT, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias -0.06, güven 50)
Canlı akış nötr: emir defteri alış payı %41, funding %0.0034, 24s %+0.04.
- 24s: %+0.04, aralık 601.01–608.00, fiyat aralığın %62'inde, hacim 51M USDT
- Emir defteri (ilk 20 kademe): alış 70K / satış 100K USDT → alış payı %41
- Funding %0.0034 / 8s (yıllık ≈ %4) → nötr
- Açık pozisyon (OI): 603,862 BNB
- Global long/short hesap oranı 2.15 (long %68)
- Metrikler: chg24_pct=0.04, high24=608.0, low24=601.01, pos24=0.62, vol24_usdt=50971279, ob_imbalance=0.41, spread_pct=0.0017, funding_pct=0.0034, funding_annual_pct=3.7, open_interest=603861.65, long_short_ratio=2.15, long_pct=68.3

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.