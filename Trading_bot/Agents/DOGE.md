---
symbol: DOGE/USDT
verdict: BEKLE
conviction: 19
price: 0.07027
updated: 2026-08-17T20:53:19+00:00
tags: [trading, agents]
---
# 🧠 DOGE/USDT — Ajan Raporu (⚪ BEKLE, kanaat %19)
> ⚪ BEKLE · kanaat %19 · 2/5 yönlü ajan hemfikir · fiyat 0.0703 · trend güçlü ayı  ·  2026-08-17 23:53

Şema: [[Agents/DOGE.canvas]] · Backtest/karar: [[Coins/DOGE]] · [[Agents/Baş Yönetici]]

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 4x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 0.0705 üstünde kapanış (long) / destek 0.0700 altında kapanış (short)

## 🚫 YAPMA
- Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Direnç 0.0705 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 0.0700 hemen altta: kırılım kapanışı görmeden short açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Kitle çok long (3.0): kontraryen risk, long'da sıkı stop
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h mum 0.0705 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 0.0709)
- EĞER 4h mum 0.0700 altında hacimle kapanırsa → short senaryosu açılır (hedef 0.0694)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 0.0956 | +36.12% |
| ema50_1d | 0.0736 | +4.74% |
| r2 | 0.0709 | +0.94% |
| high_24h | 0.0706 | +0.47% |
| r1 | 0.0705 | +0.28% |
| ema20_1d | 0.0704 | +0.15% |
| ema_resistance | 0.0704 | +0.15% |
| s1 | 0.0700 | -0.37% |
| s2 | 0.0694 | -1.24% |
| low_24h | 0.0690 | -1.75% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %2.97 (yüzdelik 0), 30g gerçekleşen vol %36. Önerilen max kaldıraç 4x, stop mesafesi ≈ %7.4.
- Günlük ATR %2.97 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %3.7 (yüzdelik 0) → sıkışma, patlama yakın olabilir
- 4h ATR %0.78
- ⚠️ Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Metrikler: atr_pct_1d=2.97, atr_pct_4h=0.78, atr_rank=0, bb_width_pct=3.71, bb_width_rank=0, realized_vol_30d=35.6, regime=DÜŞÜK, stop_pct=7.43, max_leverage=4

### 📈 Trend & EMA Çizgileri Ajanı — GÜÇLÜ AYI (bias -0.74, güven 77)
Fiyat 20g EMA (~0.0704) yakınında; yakın direnç 20g EMA ~0.0704. Çoklu zaman dilimi eğilimi: GÜÇLÜ AYI.
- Günlük: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-2.34/10bar, ADX 19
- 4 saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.03/10bar, ADX 23
- Saatlik: fiyat EMA200'ün üstünde, karışık dizilim, EMA50 eğimi %+0.16/10bar, ADX 14
- Metrikler: adx_1d=19, ema50_slope_1d=-2.34

### 🕯️ Mum Yapısı Ajanı — AYI (bias -0.33, güven 49)
Mum yapısı ayı: aralığın %49'inde kapandı, gövde %1, üst fitil %50, alt fitil %49 — doji (kararsızlık); son 5 mumun 1'i yeşil; LH+LL (al
- Günlük son mum: aralığın %49'inde kapandı, gövde %1, üst fitil %50, alt fitil %49 — doji (kararsızlık); son 5 mumun 1'i yeşil; LH+LL (alçalan yapı)
- 4 saatlik son mum: aralığın %20'inde kapandı, gövde %70, üst fitil %10, alt fitil %20 — iç bar (sıkışma); son 5 mumun 3'i yeşil; karışık yapı
- Metrikler: clv_1d=0.49, structure_1d=LH+LL (alçalan yapı), clv_4h=0.2, structure_4h=karışık yapı

### 📊 Hacim Ajanı — BOĞA (bias +0.20, güven 44)
Hacim boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.52 katı (düşük); alım/satım hacmi oranı 0.80; OBV 20-bar eğimi -1.8%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 1.06 katı (normal); alım/satım hacmi oranı 1.23; OBV 20-bar eğimi +0.6%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.52, updown_vol_1d=0.8, vol_ratio_4h=1.06, updown_vol_4h=1.23

### 🧱 Destek/Direnç Ajanı — BOĞA (bias +0.18, güven 55)
Yakın direnç 0.0705 (+0.3%); yakın destek 0.0700 (−0.4%). Fiyat son aralığın %5'inde.
- 0.0705 üstünde 4h kapanış → hedef 0.0709
- 0.0700 altında 4h kapanış → risk 0.0694
- 20 günlük en yüksek 0.0736, en düşük 0.0677
- ⚠️ Direnç 0.0705 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 0.0700 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[0.07047, 0.07093333, 0.07109333, 0.07148, 0.072015, 0.0725], supports=[0.07001, 0.06939571, 0.06895, 0.06828167, 0.06766], range_position=0.05, range_high=0.11861, range_low=0.06766

### 🚀 Momentum Ajanı — BOĞA (bias +0.32, güven 56)
Momentum boğa (RSI 1d/4h/1h: 44/53/54).
- Günlük: RSI14 44, MACD hist + (daralıyor), ROC10 %+0.7
- 4 saatlik: RSI14 53, MACD hist + (daralıyor), ROC10 %+0.7
- Saatlik: RSI14 54, MACD hist + (daralıyor), ROC10 %+0.1
- Metrikler: rsi_1d=44, macd_hist_1d=0.0002, rsi_4h=53, macd_hist_4h=0.0001, rsi_1h=54, macd_hist_1h=0.0

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji EMA Trend Takibi · ❌ WFO OOS (4 adım): Sharpe -1.44 < 0.3, PF 0.29 < 1.1, 13 işlem, getiri -8.7% vs B&H -64.0%
- Strateji durumu: FLAT, sinyal: WATCH, skor 35
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=ema_trend(fast=9, slow=200, adx_min=0), wfo_sharpe=-1.4422, wfo_pf=0.2917, wfo_trades=13, strategy_position=FLAT, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias -0.06, güven 50)
Canlı akış nötr: emir defteri alış payı %52, funding %0.0086, 24s %+0.57.
- 24s: %+0.57, aralık 0.0690–0.0706, fiyat aralığın %79'inde, hacim 20M USDT
- Emir defteri (ilk 20 kademe): alış 673K / satış 622K USDT → alış payı %52
- Funding %0.0086 / 8s (yıllık ≈ %9) → nötr
- Açık pozisyon (OI): 3,004,942,483 DOGE
- Global long/short hesap oranı 2.99 (long %75)
- ⚠️ Kitle çok long (3.0): kontraryen risk, long'da sıkı stop
- Metrikler: chg24_pct=0.57, high24=0.0706, low24=0.06904, pos24=0.79, vol24_usdt=20482584, ob_imbalance=0.52, spread_pct=0.0142, funding_pct=0.0086, funding_annual_pct=9.4, open_interest=3004942483.0, long_short_ratio=2.99, long_pct=75.0

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.