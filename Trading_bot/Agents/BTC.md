---
symbol: BTC/USDT
verdict: BEKLE
conviction: 8
price: 64334.58
updated: 2026-08-17T21:38:40+00:00
tags: [trading, agents]
---
# 🧠 BTC/USDT — Ajan Raporu (⚪ BEKLE, kanaat %8)
> ⚪ BEKLE · kanaat %8 · 1/2 yönlü ajan hemfikir · fiyat 64,335 · trend ayı  ·  2026-08-18 00:39

Şema: [[Agents/BTC.canvas]] · Backtest/karar: [[Coins/BTC]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/BTC.png]]

🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 5x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 64,488 üstünde kapanış (long) / destek 64,116 altında kapanış (short)

## 🚫 YAPMA
- Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Direnç 64,488 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 64,116 hemen altta: kırılım kapanışı görmeden short açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h mum 64,488 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 64,704)
- EĞER 4h mum 64,116 altında hacimle kapanırsa → short senaryosu açılır (hedef 63,778)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 71,890 | +11.74% |
| r2 | 64,704 | +0.57% |
| high_24h | 64,610 | +0.43% |
| r1 | 64,488 | +0.24% |
| ema50_1d | 64,355 | +0.03% |
| ema_resistance | 64,355 | +0.03% |
| s1 | 64,116 | -0.34% |
| ema20_1d | 63,795 | -0.84% |
| ema_support | 63,795 | -0.84% |
| s2 | 63,778 | -0.87% |
| low_24h | 62,716 | -2.52% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %1.95 (yüzdelik 0), 30g gerçekleşen vol %22. Önerilen max kaldıraç 5x, stop mesafesi ≈ %4.9.
- Günlük ATR %1.95 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %4.4 (yüzdelik 2) → sıkışma, patlama yakın olabilir
- 4h ATR %0.60
- ⚠️ Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Metrikler: atr_pct_1d=1.95, atr_pct_4h=0.6, atr_rank=0, bb_width_pct=4.43, bb_width_rank=2, realized_vol_30d=21.9, regime=DÜŞÜK, stop_pct=4.87, max_leverage=5

### 📈 Trend & EMA Çizgileri Ajanı — AYI (bias -0.47, güven 63)
Fiyat 50g EMA (~64,355) yakınında; yakın destek 20g EMA ~63,795; yakın direnç 50g EMA ~64,355. Çoklu zaman dilimi eğilimi: AYI.
- Günlük: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.46/10bar, ADX 19
- 4 saatlik: fiyat EMA200'ün üstünde, ayı dizilimi (20<50<200), EMA50 eğimi %-0.10/10bar, ADX 24
- Saatlik: fiyat EMA200'ün üstünde, karışık dizilim, EMA50 eğimi %+0.46/10bar, ADX 41
- Metrikler: adx_1d=19, ema50_slope_1d=-0.46

### 🕯️ Mum Yapısı Ajanı — NÖTR (bias -0.12, güven 40)
Mum yapısı nötr: aralığın %27'inde kapandı, gövde %28, üst fitil %45, alt fitil %27; son 5 mumun 2'i yeşil; karışık yapı
- Günlük son mum: aralığın %27'inde kapandı, gövde %28, üst fitil %45, alt fitil %27; son 5 mumun 2'i yeşil; karışık yapı
- 4 saatlik son mum: aralığın %52'inde kapandı, gövde %17, üst fitil %48, alt fitil %35; son 5 mumun 5'i yeşil; karışık yapı
- Metrikler: clv_1d=0.27, structure_1d=karışık yapı, clv_4h=0.52, structure_4h=karışık yapı

### 📊 Hacim Ajanı — GÜÇLÜ BOĞA (bias +0.50, güven 57)
Hacim güçlü boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.39 katı (düşük); alım/satım hacmi oranı 0.99; OBV 20-bar eğimi -0.2%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 2.52 katı (yüksek); alım/satım hacmi oranı 3.65; OBV 20-bar eğimi +20.5%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.39, updown_vol_1d=0.99, vol_ratio_4h=2.52, updown_vol_4h=3.65

### 🧱 Destek/Direnç Ajanı — NÖTR (bias +0.10, güven 55)
Yakın direnç 64,488 (+0.2%); yakın destek 64,116 (−0.3%). Fiyat son aralığın %26'inde.
- 64,488 üstünde 4h kapanış → hedef 64,704
- 64,116 altında 4h kapanış → risk 63,778
- 20 günlük en yüksek 65,745, en düşük 62,275
- ⚠️ Direnç 64,488 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 64,116 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[64488.145, 64703.544, 65061.67, 65491.94125, 65776.595, 66956.15], supports=[64116.0, 63777.725, 63206.78, 62802.27, 62632.8175, 62317.614], range_position=0.26, range_high=82850.0, range_low=57800.19

### 🚀 Momentum Ajanı — NÖTR (bias -0.06, güven 43)
Momentum nötr (RSI 1d/4h/1h: 42/70/72).
- Günlük: RSI14 42, MACD hist − (genişliyor), ROC10 %-2.2
- 4 saatlik: RSI14 70, MACD hist + (genişliyor), ROC10 %+1.9
- Saatlik: RSI14 72, MACD hist + (daralıyor), ROC10 %+1.1
- Metrikler: rsi_1d=42, macd_hist_1d=-163.5379, rsi_4h=70, macd_hist_4h=171.9057, rsi_1h=72, macd_hist_1h=73.0834

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji EMA Trend Takibi · ❌ WFO OOS (4 adım): Sharpe -1.91 < 0.3, PF 0.34 < 1.1, 19 işlem, getiri -7.5% vs B&H -44.5%
- Strateji durumu: FLAT, sinyal: WATCH, skor 35
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=ema_trend(fast=9, slow=100, adx_min=20), wfo_sharpe=-1.9145, wfo_pf=0.341, wfo_trades=19, strategy_position=FLAT, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias +0.12, güven 50)
Canlı akış nötr: emir defteri alış payı %48, funding %0.0035, 24s %+2.09.
- 24s: %+2.09, aralık 62,716–64,610, fiyat aralığın %85'inde, hacim 922M USDT
- Emir defteri (ilk 20 kademe): alış 285K / satış 310K USDT → alış payı %48
- Funding %0.0035 / 8s (yıllık ≈ %4) → nötr
- Açık pozisyon (OI): 106,419 BTC
- Global long/short hesap oranı 1.55 (long %61)
- Metrikler: chg24_pct=2.09, high24=64610.01, low24=62716.0, pos24=0.85, vol24_usdt=921924512, ob_imbalance=0.48, spread_pct=0.0, funding_pct=0.0035, funding_annual_pct=3.8, open_interest=106419.078, long_short_ratio=1.55, long_pct=60.8

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.