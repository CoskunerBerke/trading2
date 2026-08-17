---
symbol: AAOI/USDT
verdict: BEKLE
conviction: 27
price: 155.02
updated: 2026-08-17T21:19:03+00:00
tags: [trading, agents]
---
# 🧠 AAOI/USDT — Ajan Raporu (⚪ BEKLE, kanaat %27)
> ⚪ BEKLE · kanaat %27 · 4/4 yönlü ajan hemfikir · fiyat 155.02 · trend boğa  ·  2026-08-18 00:19

Şema: [[Agents/AAOI.canvas]] · Backtest/karar: [[Coins/AAOI]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]

![[Charts/AAOI.png]]

🔎 Tarayıcı: LONG skor **64**
🤖 Öğrenen model P(kazanç): **%50**

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 1x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 158.47 üstünde kapanış (long) / destek 153.61 altında kapanış (short)

## 🚫 YAPMA
- Destek 153.61 hemen altta: kırılım kapanışı görmeden short açma

## 🔀 EĞER … İSE
- EĞER 4h mum 158.47 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 177.38)
- EĞER 4h mum 153.61 altında hacimle kapanırsa → short senaryosu açılır (hedef 149.32)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| r2 | 177.38 | +14.42% |
| high_24h | 161.26 | +4.03% |
| r1 | 158.47 | +2.23% |
| s1 | 153.61 | -0.91% |
| s2 | 149.32 | -3.68% |
| ema20_1d | 130.31 | -15.94% |
| ema50_1d | 129.47 | -16.48% |
| ema200_1d | nan | +nan% |
| low_24h | 147.27 | -5.00% |
| ema_support | 130.31 | -15.94% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %8.89 (yüzdelik 0), 30g gerçekleşen vol %165. Önerilen max kaldıraç 1x, stop mesafesi ≈ %22.2.
- Günlük ATR %8.89 → son 200 günün %0'inden yüksek
- Bollinger bant genişliği %74.7 (yüzdelik 96) → genişlemiş, hareket başlamış
- 4h ATR %2.67
- Metrikler: atr_pct_1d=8.89, atr_pct_4h=2.67, atr_rank=0, bb_width_pct=74.73, bb_width_rank=96, realized_vol_30d=165.1, regime=DÜŞÜK, stop_pct=22.22, max_leverage=1

### 📈 Trend & EMA Çizgileri Ajanı — BOĞA (bias +0.45, güven 62)
Fiyat 20g EMA (~130.31) yakınında; yakın destek 20g EMA ~130.31. Çoklu zaman dilimi eğilimi: BOĞA.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %+4.62/10bar, ADX 29
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+3.80/10bar, ADX 32
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.78/10bar, ADX 20
- Metrikler: adx_1d=29, ema50_slope_1d=4.62

### 🕯️ Mum Yapısı Ajanı — NÖTR (bias -0.12, güven 40)
Mum yapısı nötr: aralığın %21'inde kapandı, gövde %55, üst fitil %24, alt fitil %21 — iç bar (sıkışma); son 5 mumun 3'i yeşil; karışık ya
- Günlük son mum: aralığın %21'inde kapandı, gövde %55, üst fitil %24, alt fitil %21 — iç bar (sıkışma); son 5 mumun 3'i yeşil; karışık yapı
- 4 saatlik son mum: aralığın %34'inde kapandı, gövde %18, üst fitil %47, alt fitil %34; son 5 mumun 3'i yeşil; HH+HL (yükselen yapı)
- Metrikler: clv_1d=0.21, structure_1d=karışık yapı, clv_4h=0.34, structure_4h=HH+HL (yükselen yapı)

### 📊 Hacim Ajanı — BOĞA (bias +0.35, güven 50)
Hacim boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.06 katı (düşük); alım/satım hacmi oranı 1.15; OBV 20-bar eğimi +23.3%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 2.16 katı (yüksek); alım/satım hacmi oranı 5.66; OBV 20-bar eğimi +293.7%; hareket hacimle onaylı
- Metrikler: vol_ratio_1d=0.06, updown_vol_1d=1.15, vol_ratio_4h=2.16, updown_vol_4h=5.66

### 🧱 Destek/Direnç Ajanı — BOĞA (bias +0.24, güven 55)
Yakın direnç 158.47 (+2.2%); yakın destek 153.61 (−0.9%). Fiyat son aralığın %64'inde.
- 158.47 üstünde 4h kapanış → hedef 177.38
- 153.61 altında 4h kapanış → risk 149.32
- 20 günlük en yüksek 154.91, en düşük 74.17
- ⚠️ Destek 153.61 hemen altta: kırılım kapanışı görmeden short açma
- Metrikler: resistances=[158.47, 177.38, 199.84], supports=[153.60666667, 149.32, 143.895, 140.52, 133.0, 129.792], range_position=0.64, range_high=199.84, range_low=74.17

### 🚀 Momentum Ajanı — BOĞA (bias +0.34, güven 57)
Momentum boğa (RSI 1d/4h/1h: 63/65/50).
- Günlük: RSI14 63, MACD hist + (daralıyor), ROC10 %+22.9
- 4 saatlik: RSI14 65, MACD hist − (genişliyor), ROC10 %+0.3
- Saatlik: RSI14 50, MACD hist + (daralıyor), ROC10 %-0.1
- Metrikler: rsi_1d=63, macd_hist_1d=4.0003, rsi_4h=65, macd_hist_4h=-0.2967, rsi_1h=50, macd_hist_1h=0.0831

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 0)
WFO analizi yok (önce `run`).

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias +0.07, güven 50)
Canlı akış nötr: emir defteri alış payı %54, funding %0.0123, 24s %+0.67.
- 24s: %+0.67, aralık 147.27–161.26, fiyat aralığın %55'inde, hacim 67M USDT
- Emir defteri (ilk 20 kademe): alış 18K / satış 15K USDT → alış payı %54
- Funding %0.0123 / 8s (yıllık ≈ %13) → longlar ödüyor (long kalabalık)
- Açık pozisyon (OI): 73,179 AAOI
- Global long/short hesap oranı 0.93 (long %48)
- Metrikler: chg24_pct=0.67, high24=161.26, low24=147.27, pos24=0.55, vol24_usdt=66599553, ob_imbalance=0.54, spread_pct=0.0194, funding_pct=0.0123, funding_annual_pct=13.5, open_interest=73178.93, long_short_ratio=0.93, long_pct=48.3

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.