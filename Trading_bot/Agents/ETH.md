---
symbol: ETH/USDT
verdict: BEKLE
conviction: 14
price: 1906.15
updated: 2026-08-17T20:52:34+00:00
tags: [trading, agents]
---
# 🧠 ETH/USDT — Ajan Raporu (⚪ BEKLE, kanaat %14)
> ⚪ BEKLE · kanaat %14 · 2/3 yönlü ajan hemfikir · fiyat 1,906 · trend boğa  ·  2026-08-17 23:53

Şema: [[Agents/ETH.canvas]] · Backtest/karar: [[Coins/ETH]] · [[Agents/Baş Yönetici]]

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 5x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 1,911 üstünde kapanış (long) / destek 1,897 altında kapanış (short)

## 🚫 YAPMA
- Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- Direnç 1,911 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 1,897 hemen altta: kırılım kapanışı görmeden short açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h mum 1,911 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 1,927)
- EĞER 4h mum 1,897 altında hacimle kapanırsa → short senaryosu açılır (hedef 1,886)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 2,128 | +11.62% |
| r2 | 1,927 | +1.11% |
| ema_resistance | 1,919 | +0.68% |
| high_24h | 1,916 | +0.49% |
| r1 | 1,911 | +0.25% |
| s1 | 1,897 | -0.50% |
| s2 | 1,886 | -1.08% |
| ema20_1d | 1,883 | -1.20% |
| ema_support | 1,883 | -1.20% |
| low_24h | 1,869 | -1.94% |
| ema50_1d | 1,867 | -2.07% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %2.50 (yüzdelik 0), 30g gerçekleşen vol %30. Önerilen max kaldıraç 5x, stop mesafesi ≈ %6.3.
- Günlük ATR %2.50 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %4.6 (yüzdelik 0) → sıkışma, patlama yakın olabilir
- 4h ATR %0.71
- ⚠️ Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Metrikler: atr_pct_1d=2.5, atr_pct_4h=0.71, atr_rank=0, bb_width_pct=4.59, bb_width_rank=0, realized_vol_30d=30.2, regime=DÜŞÜK, stop_pct=6.26, max_leverage=5

### 📈 Trend & EMA Çizgileri Ajanı — BOĞA (bias +0.33, güven 56)
Fiyat 100g EMA (~1,919) yakınında; yakın destek 20g EMA ~1,883; yakın direnç 100g EMA ~1,919. Çoklu zaman dilimi eğilimi: BOĞA.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %+0.59/10bar, ADX 15
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.11/10bar, ADX 17
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.35/10bar, ADX 29
- ⚠️ Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- Metrikler: adx_1d=15, ema50_slope_1d=0.59

### 🕯️ Mum Yapısı Ajanı — AYI (bias -0.26, güven 46)
Mum yapısı ayı: aralığın %30'inde kapandı, gövde %29, üst fitil %42, alt fitil %30; son 5 mumun 2'i yeşil; karışık yapı
- Günlük son mum: aralığın %30'inde kapandı, gövde %29, üst fitil %42, alt fitil %30; son 5 mumun 2'i yeşil; karışık yapı
- 4 saatlik son mum: aralığın %28'inde kapandı, gövde %62, üst fitil %10, alt fitil %28 — iç bar (sıkışma); son 5 mumun 3'i yeşil; karışık yapı
- Metrikler: clv_1d=0.3, structure_1d=karışık yapı, clv_4h=0.28, structure_4h=karışık yapı

### 📊 Hacim Ajanı — NÖTR (bias +0.05, güven 37)
Hacim nötr: dengeli
- Günlük: son bar hacmi 20-bar ortalamasının 0.38 katı (düşük); alım/satım hacmi oranı 1.02; OBV 20-bar eğimi +0.4%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 1.43 katı (yüksek); alım/satım hacmi oranı 2.05; OBV 20-bar eğimi +1.8%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.38, updown_vol_1d=1.02, vol_ratio_4h=1.43, updown_vol_4h=2.05

### 🧱 Destek/Direnç Ajanı — NÖTR (bias +0.03, güven 55)
Yakın direnç 1,911 (+0.3%); yakın destek 1,897 (−0.5%). Fiyat son aralığın %44'inde.
- 1,911 üstünde 4h kapanış → hedef 1,927
- 1,897 altında 4h kapanış → risk 1,886
- 20 günlük en yüksek 1,981, en düşük 1,822
- ⚠️ Direnç 1,911 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 1,897 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[1910.94666667, 1927.3475, 1939.386, 1949.76, 1956.45, 1967.0], supports=[1896.538, 1885.5325, 1875.514, 1866.27, 1854.50166667, 1847.512], range_position=0.44, range_high=2423.75, range_low=1505.68

### 🚀 Momentum Ajanı — NÖTR (bias -0.03, güven 41)
Momentum nötr (RSI 1d/4h/1h: 50/61/58).
- Günlük: RSI14 50, MACD hist − (genişliyor), ROC10 %-1.5
- 4 saatlik: RSI14 61, MACD hist + (daralıyor), ROC10 %+1.3
- Saatlik: RSI14 58, MACD hist + (daralıyor), ROC10 %+0.7
- Metrikler: rsi_1d=50, macd_hist_1d=-5.8214, rsi_4h=61, macd_hist_4h=3.5731, rsi_1h=58, macd_hist_1h=0.2078

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji Donchian Kırılım · ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 1.05 < 1.1, 20 işlem, getiri +0.1% vs B&H -34.0%
- Strateji durumu: FLAT, sinyal: WATCH, skor 53
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=donchian(entry_len=40, exit_len=10), wfo_sharpe=0.0519, wfo_pf=1.0506, wfo_trades=20, strategy_position=FLAT, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — GÜÇLÜ BOĞA (bias +0.53, güven 50)
Canlı akış güçlü boğa: emir defteri alış payı %84, funding %0.0037, 24s %+1.27.
- 24s: %+1.27, aralık 1,869–1,916, fiyat aralığın %80'inde, hacim 346M USDT
- Emir defteri (ilk 20 kademe): alış 383K / satış 71K USDT → alış payı %84
- Funding %0.0037 / 8s (yıllık ≈ %4) → nötr
- Açık pozisyon (OI): 2,380,998 ETH
- Global long/short hesap oranı 2.34 (long %70)
- Metrikler: chg24_pct=1.27, high24=1915.5, low24=1869.17, pos24=0.8, vol24_usdt=345979120, ob_imbalance=0.84, spread_pct=0.0005, funding_pct=0.0037, funding_annual_pct=4.0, open_interest=2380998.2, long_short_ratio=2.34, long_pct=70.0

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.