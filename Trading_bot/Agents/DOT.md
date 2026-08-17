---
symbol: DOT/USDT
verdict: BEKLE
conviction: 19
price: 0.763
updated: 2026-08-17T20:53:34+00:00
tags: [trading, agents]
---
# 🧠 DOT/USDT — Ajan Raporu (⚪ BEKLE, kanaat %19)
> ⚪ BEKLE · kanaat %19 · 3/6 yönlü ajan hemfikir · fiyat 0.7630 · trend güçlü ayı  ·  2026-08-17 23:53

Şema: [[Agents/DOT.canvas]] · Backtest/karar: [[Coins/DOT]] · [[Agents/Baş Yönetici]]

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 3x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 0.7687 üstünde kapanış (long) / destek 0.7560 altında kapanış (short)

## 🚫 YAPMA
- Direnç 0.7687 hemen üstte: kırılım kapanışı görmeden long açma
- Destek 0.7560 hemen altta: kırılım kapanışı görmeden short açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h mum 0.7687 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 0.7778)
- EĞER 4h mum 0.7560 altında hacimle kapanırsa → short senaryosu açılır (hedef 0.7516)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 1.27 | +66.23% |
| ema50_1d | 0.8412 | +10.25% |
| ema20_1d | 0.7935 | +4.00% |
| ema_resistance | 0.7935 | +4.00% |
| r2 | 0.7778 | +1.93% |
| high_24h | 0.7700 | +0.92% |
| r1 | 0.7687 | +0.74% |
| s1 | 0.7560 | -0.92% |
| low_24h | 0.7530 | -1.31% |
| s2 | 0.7516 | -1.49% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %3.77 (yüzdelik 0), 30g gerçekleşen vol %45. Önerilen max kaldıraç 3x, stop mesafesi ≈ %9.4.
- Günlük ATR %3.77 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %14.8 (yüzdelik 28) → normal
- 4h ATR %1.14
- Metrikler: atr_pct_1d=3.77, atr_pct_4h=1.14, atr_rank=0, bb_width_pct=14.79, bb_width_rank=28, realized_vol_30d=45.0, regime=DÜŞÜK, stop_pct=9.43, max_leverage=3

### 📈 Trend & EMA Çizgileri Ajanı — GÜÇLÜ AYI (bias -0.65, güven 72)
Fiyat 20g EMA (~0.7935) yakınında; yakın direnç 20g EMA ~0.7935. Çoklu zaman dilimi eğilimi: GÜÇLÜ AYI.
- Günlük: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-3.25/10bar, ADX 18
- 4 saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.95/10bar, ADX 20
- Saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.25/10bar, ADX 13
- Metrikler: adx_1d=18, ema50_slope_1d=-3.25

### 🕯️ Mum Yapısı Ajanı — AYI (bias -0.16, güven 42)
Mum yapısı ayı: aralığın %38'inde kapandı, gövde %12, üst fitil %50, alt fitil %38; son 5 mumun 0'i yeşil; LH+LL (alçalan yapı)
- Günlük son mum: aralığın %38'inde kapandı, gövde %12, üst fitil %50, alt fitil %38; son 5 mumun 0'i yeşil; LH+LL (alçalan yapı)
- 4 saatlik son mum: aralığın %86'inde kapandı, gövde %43, üst fitil %14, alt fitil %43; son 5 mumun 2'i yeşil; LH+LL (alçalan yapı)
- Metrikler: clv_1d=0.38, structure_1d=LH+LL (alçalan yapı), clv_4h=0.86, structure_4h=LH+LL (alçalan yapı)

### 📊 Hacim Ajanı — BOĞA (bias +0.20, güven 44)
Hacim boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.53 katı (düşük); alım/satım hacmi oranı 1.02; OBV 20-bar eğimi +0.2%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 0.68 katı (düşük); alım/satım hacmi oranı 1.21; OBV 20-bar eğimi +0.7%; hareket hacimle onaysız
- Metrikler: vol_ratio_1d=0.53, updown_vol_1d=1.02, vol_ratio_4h=0.68, updown_vol_4h=1.21

### 🧱 Destek/Direnç Ajanı — BOĞA (bias +0.19, güven 55)
Yakın direnç 0.7687 (+0.7%); yakın destek 0.7560 (−0.9%). Fiyat son aralığın %3'inde.
- 0.7687 üstünde 4h kapanış → hedef 0.7778
- 0.7560 altında 4h kapanış → risk 0.7516
- 20 günlük en yüksek 0.8700, en düşük 0.7430
- ⚠️ Direnç 0.7687 hemen üstte: kırılım kapanışı görmeden long açma
- ⚠️ Destek 0.7560 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[0.76866667, 0.77775, 0.78633333, 0.797, 0.8022, 0.81425], supports=[0.756, 0.7516, 0.7455], range_position=0.03, range_high=1.438, range_low=0.743

### 🚀 Momentum Ajanı — AYI (bias -0.20, güven 50)
Momentum ayı (RSI 1d/4h/1h: 36/44/51).
- Günlük: RSI14 36, MACD hist − (genişliyor), ROC10 %-7.7
- 4 saatlik: RSI14 44, MACD hist + (genişliyor), ROC10 %+0.3
- Saatlik: RSI14 51, MACD hist + (genişliyor), ROC10 %+0.3
- Metrikler: rsi_1d=36, macd_hist_1d=-0.003, rsi_4h=44, macd_hist_4h=0.0006, rsi_1h=51, macd_hist_1h=0.0002

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji RSI Ortalamaya Dönüş · ❌ WFO OOS (4 adım): Sharpe -1.70 < 0.3, PF 0.01 < 1.1, 9 işlem, getiri -9.0% vs B&H -80.3%
- Strateji durumu: FLAT, sinyal: WATCH, skor 35
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=rsi_mr(length=7, lo=35, hi=60, trend_ema=200), wfo_sharpe=-1.6955, wfo_pf=0.015, wfo_trades=9, strategy_position=FLAT, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — BOĞA (bias +0.23, güven 50)
Canlı akış boğa: emir defteri alış payı %66, funding %-0.0074, 24s %-0.13.
- 24s: %-0.13, aralık 0.7530–0.7700, fiyat aralığın %59'inde, hacim 2M USDT
- Emir defteri (ilk 20 kademe): alış 440K / satış 226K USDT → alış payı %66
- Funding %-0.0074 / 8s (yıllık ≈ %-8) → shortlar ödüyor (short kalabalık)
- Açık pozisyon (OI): 43,458,227 DOT
- Global long/short hesap oranı 1.78 (long %64)
- Metrikler: chg24_pct=-0.13, high24=0.77, low24=0.753, pos24=0.59, vol24_usdt=2263014, ob_imbalance=0.66, spread_pct=0.1312, funding_pct=-0.0074, funding_annual_pct=-8.1, open_interest=43458226.9, long_short_ratio=1.78, long_pct=64.0

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.