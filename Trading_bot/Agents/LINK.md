---
symbol: LINK/USDT
verdict: BEKLE
conviction: 22
price: 9.514
updated: 2026-08-17T20:53:28+00:00
tags: [trading, agents]
---
# 🧠 LINK/USDT — Ajan Raporu (⚪ BEKLE, kanaat %22)
> ⚪ BEKLE · kanaat %22 · 3/4 yönlü ajan hemfikir · fiyat 9.51 · trend boğa  ·  2026-08-17 23:53

Şema: [[Agents/LINK.canvas]] · Backtest/karar: [[Coins/LINK]] · [[Agents/Baş Yönetici]]

## 🎯 Futures planı
⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) 3x.

## ✅ YAP
- Yön yok → BEKLE. Pozisyon açma; sadece izle.
- Kaldıraç kullanma. Yeniden değerlendirme: direnç 9.60 üstünde kapanış (long) / destek 9.30 altında kapanış (short)

## 🚫 YAPMA
- Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- Direnç 9.60 hemen üstte: kırılım kapanışı görmeden long açma
- Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Sistematik (WFO) edge yok → bu plan yalnızca takdire bağlı; boyutu asgaride tut

## 🔀 EĞER … İSE
- EĞER 4h mum 9.60 üstünde hacimle kapanırsa → long senaryosu açılır (hedef 9.75)
- EĞER 4h mum 9.30 altında hacimle kapanırsa → short senaryosu açılır (hedef 9.06)

## 📍 Kilit seviyeler
| Seviye | Fiyat | Uzaklık |
|---|---|---|
| r2 | 9.75 | +2.44% |
| r1 | 9.60 | +0.88% |
| high_24h | 9.59 | +0.75% |
| ema200_1d | 9.51 | -0.06% |
| ema_support | 9.51 | -0.06% |
| low_24h | 9.35 | -1.74% |
| s1 | 9.30 | -2.22% |
| s2 | 9.06 | -4.78% |
| ema20_1d | 8.63 | -9.31% |
| ema50_1d | 8.42 | -11.46% |

## 🤖 Uzman ajan raporları
### 🌡️ Volatilite Ajanı — NÖTR (bias +0.00, güven 70)
Volatilite DÜŞÜK: günlük ATR %3.44 (yüzdelik 2), 30g gerçekleşen vol %47. Önerilen max kaldıraç 3x, stop mesafesi ≈ %8.6.
- Günlük ATR %3.44 → son 200 günün %2'inden yüksek
- Bollinger bant genişliği %19.0 (yüzdelik 74) → normal
- 4h ATR %1.60
- Metrikler: atr_pct_1d=3.44, atr_pct_4h=1.6, atr_rank=2, bb_width_pct=19.0, bb_width_rank=74, realized_vol_30d=47.0, regime=DÜŞÜK, stop_pct=8.59, max_leverage=3

### 📈 Trend & EMA Çizgileri Ajanı — BOĞA (bias +0.35, güven 57)
Fiyat 200g EMA (~9.51) yakınında; yakın destek 200g EMA ~9.51. Çoklu zaman dilimi eğilimi: BOĞA.
- Günlük: fiyat EMA200'ün altında, karışık dizilim, EMA50 eğimi %+2.03/10bar, ADX 25
- 4 saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+2.47/10bar, ADX 47
- Saatlik: fiyat EMA200'ün üstünde, boğa dizilimi (20>50>200), EMA50 eğimi %+0.51/10bar, ADX 17
- ⚠️ Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al
- Metrikler: adx_1d=25, ema50_slope_1d=2.03

### 🕯️ Mum Yapısı Ajanı — NÖTR (bias -0.08, güven 38)
Mum yapısı nötr: aralığın %44'inde kapandı, gövde %37, üst fitil %19, alt fitil %44 — iç bar (sıkışma); son 5 mumun 3'i yeşil; HH+HL (yük
- Günlük son mum: aralığın %44'inde kapandı, gövde %37, üst fitil %19, alt fitil %44 — iç bar (sıkışma); son 5 mumun 3'i yeşil; HH+HL (yükselen yapı)
- 4 saatlik son mum: aralığın %26'inde kapandı, gövde %21, üst fitil %52, alt fitil %26 — ayı yutan; son 5 mumun 3'i yeşil; HH+HL (yükselen yapı)
- Metrikler: clv_1d=0.44, structure_1d=HH+HL (yükselen yapı), clv_4h=0.26, structure_4h=HH+HL (yükselen yapı)

### 📊 Hacim Ajanı — GÜÇLÜ BOĞA (bias +0.70, güven 66)
Hacim güçlü boğa: alıcı hacmi baskın
- Günlük: son bar hacmi 20-bar ortalamasının 0.91 katı (normal); alım/satım hacmi oranı 1.61; OBV 20-bar eğimi +12.7%; hareket hacimle onaysız
- 4 saatlik: son bar hacmi 20-bar ortalamasının 0.71 katı (normal); alım/satım hacmi oranı 1.59; OBV 20-bar eğimi +74.4%; hareket hacimle onaysız
- Metrikler: vol_ratio_1d=0.91, updown_vol_1d=1.61, vol_ratio_4h=0.71, updown_vol_4h=1.59

### 🧱 Destek/Direnç Ajanı — AYI (bias -0.36, güven 55)
Yakın direnç 9.60 (+0.9%); yakın destek 9.30 (−2.3%). Fiyat son aralığın %65'inde.
- 9.60 üstünde 4h kapanış → hedef 9.75
- 9.30 altında 4h kapanış → risk 9.06
- 20 günlük en yüksek 9.75, en düşük 7.89
- ⚠️ Direnç 9.60 hemen üstte: kırılım kapanışı görmeden long açma
- Metrikler: resistances=[9.59766667, 9.746, 10.034, 10.87], supports=[9.303, 9.059, 8.9084, 8.7534, 8.618, 8.5552], range_position=0.65, range_high=10.87, range_low=6.996

### 🚀 Momentum Ajanı — BOĞA (bias +0.45, güven 62)
Momentum boğa (RSI 1d/4h/1h: 69/62/53).
- Günlük: RSI14 69, MACD hist + (genişliyor), ROC10 %+14.6
- 4 saatlik: RSI14 62, MACD hist − (genişliyor), ROC10 %+1.0
- Saatlik: RSI14 53, MACD hist − (daralıyor), ROC10 %+0.8
- Metrikler: rsi_1d=69, macd_hist_1d=0.118, rsi_4h=62, macd_hist_4h=-0.0181, rsi_1h=53, macd_hist_1h=-0.0005

### 🧪 Backtest/Edge Ajanı — NÖTR (bias +0.00, güven 40)
En iyi strateji RSI Ortalamaya Dönüş · ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 0.93 < 1.1, 11 işlem, getiri +0.2% vs B&H -37.0%
- Strateji durumu: FLAT, sinyal: WATCH, skor 60
- ⚠️ Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut
- Metrikler: has_edge=False, best=rsi_mr(length=14, lo=25, hi=60, trend_ema=0), wfo_sharpe=0.0524, wfo_pf=0.9272, wfo_trades=11, strategy_position=FLAT, signal=WATCH

### 📡 Binance Canlı Piyasa Ajanı — NÖTR (bias +0.06, güven 50)
Canlı akış nötr: emir defteri alış payı %48, funding %0.0005, 24s %+0.58.
- 24s: %+0.58, aralık 9.35–9.59, fiyat aralığın %70'inde, hacim 15M USDT
- Emir defteri (ilk 20 kademe): alış 80K / satış 86K USDT → alış payı %48
- Funding %0.0005 / 8s (yıllık ≈ %1) → nötr
- Açık pozisyon (OI): 12,773,164 LINK
- Global long/short hesap oranı 1.67 (long %62)
- Metrikler: chg24_pct=0.58, high24=9.585, low24=9.348, pos24=0.7, vol24_usdt=14748728, ob_imbalance=0.48, spread_pct=0.0105, funding_pct=0.0005, funding_annual_pct=0.6, open_interest=12773163.85, long_short_ratio=1.67, long_pct=62.5

> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.