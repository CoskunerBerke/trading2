---
symbol: META/USDT
verdict: BEKLE
conviction: 35
price: 570.18
updated: 2026-08-17T21:39:00+00:00
tags: [trading, agents]
---
# 🧠 META/USDT — Ajan Raporu (⚪ BEKLE, kanaat %35)
> ⚪ BEKLE · kanaat %35 · 3/3 yönlü ajan hemfikir · fiyat 570.18 · trend güçlü ayı  ·  2026-08-18 00:39

Şema: [[Agents/META.canvas]] · Backtest/karar: [[Coins/META]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/META.png]]

🔎 Tarayıcı: SHORT skor **68**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 4x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 577.70 üstünde kapanış (long) / destek 563.71 altında kapanış (short)

## 🚫 YAPMA
- 4 saatlik RSI 21 aşırı satım: yeni short açma, tepki bekle
- Saatlik RSI 20 aşırı satım: yeni short açma, tepki bekle

## 🔀 EĞER … İSE
- EĞER 4h mum 577.70 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 581.17)
- EĞER 4h mum 563.71 altında hacimle kapanırsa → short senaryosu açılır (hedef 559.40)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema50_1d | 597.40 | +4.77% |
| ema200_1d | nan | +nan% |
| high_24h | 593.03 | +4.01% |
| ema_resistance | 592.10 | +3.84% |
| ema20_1d | 592.10 | +3.84% |
| r2 | 581.17 | +1.93% |
| r1 | 577.70 | +1.32% |
| low_24h | 565.65 | -0.79% |
| s1 | 563.71 | -1.13% |
| s2 | 559.40 | -1.89% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite NORMAL: günlük ATR %2.79 (yüzdelik 37), 30g gerçekleşen vol %47. Önerilen max kaldıraç 4x, stop mesafesi ≈ %7.0.
- Günlük ATR %2.79 → son 200 günün %37'inden yüksek
- Bollinger bant genişliği %13.1 (yüzdelik 37) → normal
- 4h ATR %0.76
- Metrikler: atr_pct_1d=2.79, atr_pct_4h=0.76, atr_rank=37, bb_width_pct=13.09, bb_width_rank=37, realized_vol_30d=47.4, regime=NORMAL, stop_pct=6.98, max_leverage=4

### 📈 Trend & EMA Çizgileri Ajanı — GÜÇLÜ AYI (bias -0.75, güven 77)
Fiyat 20g EMA (~592.10) yakınında; yakın direnç 20g EMA ~592.10. Çoklu zaman dilimi eğilimi: GÜÇLÜ AYI.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %-0.38/10bar, ADX 15
- 4 saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.24/10bar, ADX 32
- Saatlik: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.96/10bar, ADX 52
- Metrikler: adx_1d=15, ema50_slope_1d=-0.38

### 🕯️ Mum Yapısı Ajanı — NÖTR (bias -0.07, güven 38)
Mum yapısı nötr: aralığın %88'inde kapandı, gövde %37, üst fitil %12, alt fitil %50; son 5 mumun 3'i yeşil; LH+LL (alçalan yapı)
- Günlük son mum: aralığın %88'inde kapandı, gövde %37, üst fitil %12, alt fitil %50; son 5 mumun 3'i yeşil; LH+LL (alçalan yapı)
- 4 saatlik son mum: aralığın %50'inde kapandı, gövde %50, üst fitil %0, alt fitil %50; son 5 mumun 1'i yeşil; LH+LL (alçalan yapı)
- Metrikler: clv_1d=0.88, structure_1d=LH+LL (alçalan yapı), clv_4h=0.5, structure_4h=LH+LL (alçalan yapı)

### 📊 Hacim Ajanı — AYI (bias -0.15, güven 41)
Hacim ayı: satıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.04 katı (düşük); alım/satım hacmi oranı 1.29; OBV 20-bar eğimi +863.8%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 4.10 katı (yüksek); alım/satım hacmi oranı 0.10; OBV 20-bar eğimi -6219.4%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.04, updown_vol_1d=1.29, vol_ratio_4h=4.1, updown_vol_4h=0.1

### 🧱 Destek/Direnç Ajanı — NÖTR (bias +0.08, güven 55)
Yakın direnç 577.70 (+1.3%); yakın destek 563.71 (−1.1%). Fiyat son aralığın %30'inde.
- 577.70 üstünde 4h kapanış → hedef 581.17
- 563.71 altında 4h kapanış → risk 559.40
- 20 günlük en yüksek 612.84, en düşük 519.43
- Metrikler: resistances=[577.7, 581.17, 586.10333333, 589.855, 593.075, 597.09857143], supports=[563.71, 559.4, 556.285, 550.67, 541.47, 539.02], range_position=0.3, range_high=689.85, range_low=519.43

### 🚀 Momentum Ajanı — NÖTR (bias -0.12, güven 45)
Momentum nötr (RSI 1d/4h/1h: 49/21/20).
- Günlük: RSI14 49, MACD hist + (daralıyor), ROC10 %+0.5
- 4 saatlik: RSI14 21, MACD hist − (genişliyor), ROC10 %-3.7
- Saatlik: RSI14 20, MACD hist − (daralıyor), ROC10 %-3.4
- ⚠️ 4 saatlik RSI 21 aşırı satım: yeni short açma, tepki bekle
- ⚠️ Saatlik RSI 20 aşırı satım: yeni short açma, tepki bekle
- Metrikler: rsi_1d=49, macd_hist_1d=1.9576, rsi_4h=21, macd_hist_4h=-2.0517, rsi_1h=20, macd_hist_1h=-1.821

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — AYI (bias -0.42, güven 50)
Canlı akış ayı: emir defteri alış payı %26, funding %0.0051, 24s %-3.76.
- 24s: %-3.76, aralık 565.65–593.03, fiyat aralığın %17'inde, hacim 27M USDT
- Emir defteri (ilk 20 kademe): alış 8K / satış 21K USDT → alış payı %26
- Funding %0.0051 / 8s (yıllık ≈ %6) → nötr
- Açık pozisyon (OI): 46,982 META
- Global long/short hesap oranı 2.01 (long %67)
- Metrikler: chg24_pct=-3.76, high24=593.03, low24=565.65, pos24=0.17, vol24_usdt=27266718, ob_imbalance=0.26, spread_pct=0.0018, funding_pct=0.0051, funding_annual_pct=5.5, open_interest=46981.85, long_short_ratio=2.01, long_pct=66.7

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.