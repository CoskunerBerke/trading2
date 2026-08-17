---
symbol: AVAX/USDT
verdict: BEKLE
conviction: 23
price: 6.34
updated: 2026-08-17T20:53:23+00:00
tags: [trading, agents]
---
# 🧠 AVAX/USDT — Ajan Raporu (⚪ BEKLE, kanaat %23)
> ⚪ BEKLE · kanaat %23 · 3/5 yönlü ajan hemfikir · fiyat 6.34 · trend güçlü ayı  ·  2026-08-17 23:53

Şema: [[Agents/AVAX.canvas]] · Backtest/karar: [[Coins/AVAX]] · [[Agents/Baş Yönetici]]

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 2x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 6.44 üstünde kapanış (long) / destek 6.30 altında kapanış (short)

## 🚫 YAPMA
- Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Destek 6.30 hemen altta: kırılım kapanışı görmeden short açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h mum 6.44 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 6.54)
- EĞER 4h mum 6.30 altında hacimle kapanırsa → short senaryosu açılır (hedef 6.21)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 8.95 | +41.11% |
| ema50_1d | 6.64 | +4.74% |
| r2 | 6.54 | +3.11% |
| ema20_1d | 6.44 | +1.65% |
| ema_resistance | 6.44 | +1.65% |
| r1 | 6.44 | +1.53% |
| high_24h | 6.40 | +0.99% |
| s1 | 6.30 | -0.57% |
| low_24h | 6.28 | -0.99% |
| s2 | 6.21 | -1.99% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %4.66 (yüzdelik 27), 30g gerçekleşen vol %50. Önerilen max kaldıraç 2x, stop mesafesi ≈ %11.6.
- Günlük ATR %4.66 → son 200 günün %27'inden yüksek
- Bollinger bant genişliği %7.3 (yüzdelik 1) → sıkışma, patlama yakın olabilir
- 4h ATR %1.49
- ⚠️ Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Metrikler: atr_pct_1d=4.66, atr_pct_4h=1.49, atr_rank=27, bb_width_pct=7.34, bb_width_rank=1, realized_vol_30d=50.1, regime=DÜŞÜK, stop_pct=11.64, max_leverage=2

### 📈 Trend & EMA Çizgileri Ajanı — GÜÇLÜ AYI (bias -0.60, güven 70)
Fiyat 20g EMA (~6.44) yakınında; yakın direnç 20g EMA ~6.44. Çoklu zaman dilimi eğilimi: GÜÇLÜ AYI.
- Günlük: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-1.77/10bar, ADX 15
- 4 saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.58/10bar, ADX 10
- Saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.32/10bar, ADX 24
- Metrikler: adx_1d=15, ema50_slope_1d=-1.77

### 🕯️ Mum Yapısı Ajanı — BOĞA (bias +0.29, güven 48)
Mum yapısı boğa: aralığın %51'inde kapandı, gövde %1, üst fitil %48, alt fitil %51 — doji (kararsızlık); son 5 mumun 2'i yeşil; karışık y
- Günlük son mum: aralığın %51'inde kapandı, gövde %1, üst fitil %48, alt fitil %51 — doji (kararsızlık); son 5 mumun 2'i yeşil; karışık yapı
- 4 saatlik son mum: aralığın %74'inde kapandı, gövde %23, üst fitil %26, alt fitil %51; son 5 mumun 2'i yeşil; HH+HL (yükselen yapı)
- Metrikler: clv_1d=0.51, structure_1d=karışık yapı, clv_4h=0.74, structure_4h=HH+HL (yükselen yapı)

### 📊 Hacim Ajanı — AYI (bias -0.35, güven 50)
Hacim ayı: satıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.94 katı (normal); alım/satım hacmi oranı 0.74; OBV 20-bar eğimi -17.3%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 0.60 katı (düşük); alım/satım hacmi oranı 0.92; OBV 20-bar eğimi -0.3%; hareket hacimle onaysız
- Metrikler: vol_ratio_1d=0.94, updown_vol_1d=0.74, vol_ratio_4h=0.6, updown_vol_4h=0.92

### 🧱 Destek/Direnç Ajanı — BOĞA (bias +0.45, güven 55)
Yakın direnç 6.44 (+1.5%); yakın destek 6.30 (−0.6%). Fiyat son aralığın %14'inde.
- 6.44 üstünde 4h kapanış → hedef 6.54
- 6.30 altında 4h kapanış → risk 6.21
- 20 günlük en yüksek 6.99, en düşük 6.04
- ⚠️ Destek 6.30 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[6.43675, 6.53742857, 6.6094, 6.66942857, 6.773, 6.85566667], supports=[6.304, 6.21366667, 6.1235, 6.04, 5.954, 5.681], range_position=0.14, range_high=10.49, range_low=5.681

### 🚀 Momentum Ajanı — AYI (bias -0.48, güven 64)
Momentum ayı (RSI 1d/4h/1h: 44/45/50).
- Günlük: RSI14 44, MACD hist − (genişliyor), ROC10 %-1.7
- 4 saatlik: RSI14 45, MACD hist − (daralıyor), ROC10 %-0.2
- Saatlik: RSI14 50, MACD hist + (genişliyor), ROC10 %+0.2
- Metrikler: rsi_1d=44, macd_hist_1d=-0.0054, rsi_4h=45, macd_hist_4h=-0.0106, rsi_1h=50, macd_hist_1h=0.0027

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji RSI(2) Trend İçi Geri Çekilme · ❌ WFO OOS (4 adım): Sharpe -1.94 < 0.3, PF 0.13 < 1.1, 13 işlem, getiri -11.1% vs B&H -68.9%
- Strateji durumu: FLAT, sinyal: WATCH, skor 39
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=rsi2_pullback(length=3, lo=10, hi=65, trend_ema=200), wfo_sharpe=-1.9414, wfo_pf=0.1323, wfo_trades=13, strategy_position=FLAT, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias -0.11, güven 50)
Canlı akış nötr: emir defteri alış payı %41, funding %-0.0103, 24s %+0.11.
- 24s: %+0.11, aralık 6.28–6.40, fiyat aralığın %50'inde, hacim 7M USDT
- Emir defteri (ilk 20 kademe): alış 71K / satış 102K USDT → alış payı %41
- Funding %-0.0103 / 8s (yıllık ≈ %-11) → shortlar ödüyor (short kalabalık)
- Açık pozisyon (OI): 7,813,061 AVAX
- Global long/short hesap oranı 1.49 (long %60)
- Metrikler: chg24_pct=0.11, high24=6.403, low24=6.277, pos24=0.5, vol24_usdt=7425108, ob_imbalance=0.41, spread_pct=0.0158, funding_pct=-0.0103, funding_annual_pct=-11.3, open_interest=7813061.0, long_short_ratio=1.49, long_pct=59.9

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.