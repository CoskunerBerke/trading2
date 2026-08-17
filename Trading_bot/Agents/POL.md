---
symbol: POL/USDT
verdict: BEKLE
conviction: 18
price: 0.08009
updated: 2026-08-17T21:18:58+00:00
tags: [trading, agents]
---
# 🧠 POL/USDT — Ajan Raporu (⚪ BEKLE, kanaat %18)
> ⚪ BEKLE · kanaat %18 · 4/5 yönlü ajan hemfikir · fiyat 0.0801 · trend nötr  ·  2026-08-18 00:19

Şema: [[Agents/POL.canvas]] · Backtest/karar: [[Coins/POL]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/POL.png]]

🔎 Tarayıcı: LONG skor **66**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 4x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 0.0815 üstünde kapanış (long) / destek 0.0792 altında kapanış (short)

## 🚫 YAPMA
- Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- 4 saatlik RSI 78 aşırı alım: yeni long açma, geri çekilme bekle

## 🔀 EĞER … İSE
- EĞER 4h mum 0.0815 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 0.0841)
- EĞER 4h mum 0.0792 altında hacimle kapanırsa → short senaryosu açılır (hedef 0.0783)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| ema200_1d | 0.0955 | +19.26% |
| high_24h | 0.0849 | +6.01% |
| r2 | 0.0841 | +5.05% |
| r1 | 0.0815 | +1.74% |
| ema_resistance | 0.0810 | +1.17% |
| s1 | 0.0792 | -1.16% |
| s2 | 0.0783 | -2.24% |
| ema50_1d | 0.0766 | -4.35% |
| ema_support | 0.0766 | -4.35% |
| ema20_1d | 0.0751 | -6.20% |
| low_24h | 0.0746 | -6.85% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %2.96 (yüzdelik 0), 30g gerçekleşen vol %31. Önerilen max kaldıraç 4x, stop mesafesi ≈ %7.4.
- Günlük ATR %2.96 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %9.2 (yüzdelik 4) → sıkışma, patlama yakın olabilir
- 4h ATR %1.71
- ⚠️ Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma
- Metrikler: atr_pct_1d=2.96, atr_pct_4h=1.71, atr_rank=0, bb_width_pct=9.22, bb_width_rank=4, realized_vol_30d=31.2, regime=DÜŞÜK, stop_pct=7.41, max_leverage=4

### 📈 Trend & EMA Çizgileri Ajanı — NÖTR (bias +0.13, güven 46)
Fiyat 100g EMA (~0.0810) yakınında; yakın destek 50g EMA ~0.0766; yakın direnç 100g EMA ~0.0810. Çoklu zaman dilimi eğilimi: NÖTR.
- Günlük: fiyat EMA200'ün altında, ayı dizilimi (20<50<200), EMA50 eğimi %-0.87/10bar, ADX 14
- 4 saatlik: fiyat EMA200'ün üstünde, karışık dizilim, EMA50 eğimi %+0.56/10bar, ADX 20
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+1.73/10bar, ADX 47
- ⚠️ Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- Metrikler: adx_1d=14, ema50_slope_1d=-0.87

### 🕯️ Mum Yapısı Ajanı — AYI (bias -0.21, güven 44)
Mum yapısı ayı: aralığın %34'inde kapandı, gövde %45, üst fitil %21, alt fitil %34; son 5 mumun 2'i yeşil; LH+LL (alçalan yapı)
- Günlük son mum: aralığın %34'inde kapandı, gövde %45, üst fitil %21, alt fitil %34; son 5 mumun 2'i yeşil; LH+LL (alçalan yapı)
- 4 saatlik son mum: aralığın %32'inde kapandı, gövde %28, üst fitil %68, alt fitil %4; son 5 mumun 5'i yeşil; HH+HL (yükselen yapı)
- Metrikler: clv_1d=0.34, structure_1d=LH+LL (alçalan yapı), clv_4h=0.32, structure_4h=HH+HL (yükselen yapı)

### 📊 Hacim Ajanı — BOĞA (bias +0.15, güven 41)
Hacim boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.44 katı (düşük); alım/satım hacmi oranı 0.72; OBV 20-bar eğimi -4.9%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 11.76 katı (yüksek); alım/satım hacmi oranı 7.45; OBV 20-bar eğimi +383.1%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.44, updown_vol_1d=0.72, vol_ratio_4h=11.76, updown_vol_4h=7.45

### 🧱 Destek/Direnç Ajanı — BOĞA (bias +0.36, güven 55)
Yakın direnç 0.0815 (+1.7%); yakın destek 0.0792 (−1.2%). Fiyat son aralığın %34'inde.
- 0.0815 üstünde 4h kapanış → hedef 0.0841
- 0.0792 altında 4h kapanış → risk 0.0783
- 20 günlük en yüksek 0.0786, en düşük 0.0701
- Metrikler: resistances=[0.08148667, 0.084135, 0.08513, 0.08626, 0.087635, 0.08925], supports=[0.07916, 0.07829833, 0.07692333, 0.07603, 0.07526167, 0.07434444], range_position=0.34, range_high=0.10492, range_low=0.06734

### 🚀 Momentum Ajanı — BOĞA (bias +0.35, güven 57)
Momentum boğa (RSI 1d/4h/1h: 48/78/67).
- Günlük: RSI14 48, MACD hist + (daralıyor), ROC10 %-0.4
- 4 saatlik: RSI14 78, MACD hist + (genişliyor), ROC10 %+6.8
- Saatlik: RSI14 67, MACD hist + (daralıyor), ROC10 %+5.0
- ⚠️ 4 saatlik RSI 78 aşırı alım: yeni long açma, geri çekilme bekle
- Metrikler: rsi_1d=48, macd_hist_1d=0.0002, rsi_4h=78, macd_hist_4h=0.0005, rsi_1h=67, macd_hist_1h=0.0004

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — BOĞA (bias +0.25, güven 50)
Canlı akış boğa: emir defteri alış payı %70, funding %0.0050, 24s %+6.56.
- 24s: %+6.56, aralık 0.0746–0.0849, fiyat aralığın %53'inde, hacim 6M USDT
- Emir defteri (ilk 20 kademe): alış 30K / satış 13K USDT → alış payı %70
- Funding %0.0050 / 8s (yıllık ≈ %5) → nötr
- Açık pozisyon (OI): 173,746,705 POL
- Global long/short hesap oranı 1.64 (long %62)
- Metrikler: chg24_pct=6.56, high24=0.0849, low24=0.0746, pos24=0.53, vol24_usdt=6104495, ob_imbalance=0.7, spread_pct=0.0125, funding_pct=0.005, funding_annual_pct=5.5, open_interest=173746705.0, long_short_ratio=1.64, long_pct=62.2

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.