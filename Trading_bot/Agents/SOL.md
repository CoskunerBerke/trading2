---
symbol: SOL/USDT
verdict: BEKLE
conviction: 10
price: 75.92
updated: 2026-08-17T21:18:36+00:00
tags: [trading, agents]
---
# 🧠 SOL/USDT — Ajan Raporu (⚪ BEKLE, kanaat %10)
> ⚪ BEKLE · kanaat %10 · 2/3 yönlü ajan hemfikir · fiyat 75.92 · trend boğa  ·  2026-08-18 00:19

Şema: [[Agents/SOL.canvas]] · Backtest/karar: [[Coins/SOL]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/SOL.png]]

🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 4x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 76.28 üstünde kapanış (long) / destek 75.67 altında kapanış (short)

## 🚫 YAPMA
- Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- Direnç 76.28 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 75.67 hemen altta: kırılım kapanışı görmeden short açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h mum 76.28 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 76.81)
- EĞER 4h mum 75.67 altında hacimle kapanırsa → short senaryosu açılır (hedef 75.23)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 89.18 | +17.46% |
| ema_resistance | 78.15 | +2.94% |
| r2 | 76.81 | +1.17% |
| r1 | 76.28 | +0.47% |
| high_24h | 76.22 | +0.40% |
| s1 | 75.67 | -0.33% |
| ema50_1d | 75.49 | -0.57% |
| ema_support | 75.49 | -0.57% |
| s2 | 75.23 | -0.90% |
| ema20_1d | 75.12 | -1.06% |
| strategy_stop | 74.40 | -2.00% |
| low_24h | 74.10 | -2.40% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %2.79 (yüzdelik 0), 30g gerçekleşen vol %30. Önerilen max kaldıraç 4x, stop mesafesi ≈ %7.0.
- Günlük ATR %2.79 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %6.9 (yüzdelik 0) → sıkışma, patlama yakın olabilir
- 4h ATR %0.88
- ⚠️ Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Metrikler: atr_pct_1d=2.79, atr_pct_4h=0.88, atr_rank=0, bb_width_pct=6.95, bb_width_rank=0, realized_vol_30d=30.3, regime=DÜŞÜK, stop_pct=6.98, max_leverage=4

### 📈 Trend & EMA Çizgileri Ajanı — BOĞA (bias +0.25, güven 52)
Fiyat 50g EMA (~75.49) yakınında; yakın destek 50g EMA ~75.49; yakın direnç 100g EMA ~78.15. Çoklu zaman dilimi eğilimi: BOĞA.
- Günlük: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %+0.04/10bar, ADX 9
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.02/10bar, ADX 24
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.22/10bar, ADX 12
- ⚠️ Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- Metrikler: adx_1d=9, ema50_slope_1d=0.04

### 🕯️ Mum Yapısı Ajanı — GÜÇLÜ AYI (bias -0.57, güven 60)
Mum yapısı güçlü ayı: aralığın %32'inde kapandı, gövde %46, üst fitil %22, alt fitil %32; son 5 mumun 1'i yeşil; LH+LL (alçalan yapı)
- Günlük son mum: aralığın %32'inde kapandı, gövde %46, üst fitil %22, alt fitil %32; son 5 mumun 1'i yeşil; LH+LL (alçalan yapı)
- 4 saatlik son mum: aralığın %23'inde kapandı, gövde %64, üst fitil %13, alt fitil %23 — iç bar (sıkışma); son 5 mumun 3'i yeşil; LH+LL (alçalan yapı)
- Metrikler: clv_1d=0.32, structure_1d=LH+LL (alçalan yapı), clv_4h=0.23, structure_4h=LH+LL (alçalan yapı)

### 📊 Hacim Ajanı — AYI (bias -0.15, güven 41)
Hacim ayı: dengeli
- Günlük: son bar hacmi 20-bar ortalamasının 0.54 katı (düşük); alım/satım hacmi oranı 0.85; OBV 20-bar eğimi -4.4%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 1.11 katı (normal); alım/satım hacmi oranı 1.13; OBV 20-bar eğimi +0.4%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.54, updown_vol_1d=0.85, vol_ratio_4h=1.11, updown_vol_4h=1.13

### 🧱 Destek/Direnç Ajanı — NÖTR (bias +0.04, güven 55)
Yakın direnç 76.28 (+0.5%); yakın destek 75.67 (−0.3%). Fiyat son aralığın %41'inde.
- 76.28 üstünde 4h kapanış → hedef 76.81
- 75.67 altında 4h kapanış → risk 75.23
- 20 günlük en yüksek 77.84, en düşük 70.58
- ⚠️ Direnç 76.28 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 75.67 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[76.27666667, 76.81, 77.3575, 77.84, 78.9125, 80.0], supports=[75.67, 75.23333333, 74.62833333, 74.26, 73.386, 72.645], range_position=0.41, range_high=98.41, range_low=60.13

### 🚀 Momentum Ajanı — GÜÇLÜ BOĞA (bias +0.59, güven 69)
Momentum güçlü boğa (RSI 1d/4h/1h: 48/53/58).
- Günlük: RSI14 48, MACD hist + (daralıyor), ROC10 %+2.6
- 4 saatlik: RSI14 53, MACD hist + (genişliyor), ROC10 %+0.3
- Saatlik: RSI14 58, MACD hist + (daralıyor), ROC10 %+0.3
- Metrikler: rsi_1d=48, macd_hist_1d=0.1258, rsi_4h=53, macd_hist_4h=0.0776, rsi_1h=58, macd_hist_1h=0.0298

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji EMA Trend Takibi · ❌ WFO OOS (4 adım): Sharpe -0.06 < 0.3, PF 0.76 < 1.1, 22 işlem, getiri -0.9% vs B&H -51.7%
- Strateji durumu: LONG, sinyal: WATCH, skor 60
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=ema_trend(fast=20, slow=50, adx_min=0), wfo_sharpe=-0.0581, wfo_pf=0.7588, wfo_trades=22, strategy_position=LONG, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias +0.05, güven 50)
Canlı akış nötr: emir defteri alış payı %42, funding %-0.0030, 24s %+1.13.
- 24s: %+1.13, aralık 74.10–76.22, fiyat aralığın %86'inde, hacim 101M USDT
- Emir defteri (ilk 20 kademe): alış 884K / satış 1,200K USDT → alış payı %42
- Funding %-0.0030 / 8s (yıllık ≈ %-3) → nötr
- Açık pozisyon (OI): 8,661,823 SOL
- Global long/short hesap oranı 2.36 (long %70)
- Metrikler: chg24_pct=1.13, high24=76.22, low24=74.1, pos24=0.86, vol24_usdt=100857152, ob_imbalance=0.42, spread_pct=0.0132, funding_pct=-0.003, funding_annual_pct=-3.3, open_interest=8661823.42, long_short_ratio=2.36, long_pct=70.2

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.