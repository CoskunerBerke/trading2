---
symbol: AVAX/USDT
signal: WATCH
score: 39
price: 6.342
regime: YATAY
has_edge: false
updated: 2026-08-17T20:31:39+00:00
tags: [trading, coin]
---
# ⚪ AVAX/USDT — BEKLE (skor 39)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 6.34 | -0.1 | 45 | 6.39 | 6.43 | 6.52 | 0.0943 | 1.49 | 10 | YATAY |

## Karar
**Karar:** ⚪ **BEKLE** · güven 39
- Edge yok — ❌ WFO OOS (4 adım): Sharpe -1.94 < 0.3, PF 0.13 < 1.1, 13 işlem, getiri -11.1% vs B&H -68.9%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -1.94 < 0.3, PF 0.13 < 1.1, 13 işlem, getiri -11.1% vs B&H -68.9%
- Rejim: YATAY (EMA200 altı, ADX 10)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -1.94 < 0.3, PF 0.13 < 1.1, 13 işlem, getiri -11.1% vs B&H -68.9%
- Rejim: YATAY (EMA200 altı, ADX 10)
- RSI14 45, ATR %1.49

## En iyi strateji: RSI(2) Trend İçi Geri Çekilme
`rsi2_pullback(length=3, lo=10, hi=65, trend_ema=200)` · ❌ ❌ WFO OOS (4 adım): Sharpe -1.94 < 0.3, PF 0.13 < 1.1, 13 işlem, getiri -11.1% vs B&H -68.9%
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 2.58 | -11.11 | 0.86 |
| CAGR % | 1.28 | -10.17 | 1.43 |
| Sharpe | 0.40 | -1.94 | 1.51 |
| Max DD % | -4.00 | -12.54 | -0.12 |
| Win rate % | 50.00 | 7.69 | 100.00 |
| Profit factor | 1.38 | 0.13 | 99.00 |
| İşlem | 18.00 | 13.00 | 1.00 |
| Exposure % | 2.65 | 6.35 | 0.23 |
| Buy&Hold % | -69.24 | -68.86 | -53.84 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `ema_trend(fast=9, slow=100, adx_min=0)` | -2.28 | -4.96 | -1.7 | 6 |
| 2 | 2025-10-20 | 2026-01-28 | `rsi2_pullback(length=3, lo=10, hi=65, trend_ema=100)` | -0.85 | -0.45 | -41.2 | 1 |
| 3 | 2026-01-28 | 2026-05-09 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | -2.10 | -3.38 | -15.6 | 4 |
| 4 | 2026-05-09 | 2026-08-17 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | -2.50 | -2.76 | -36.1 | 2 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `rsi2_pullback(length=3, lo=10, hi=65, trend_ema=200)` | 0.40 | 1.38 | 18 | 1.51 | 99.00 | 1 |
| 2 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 0.14 | 1.08 | 22 | -1.57 | 0.45 | 8 |
| 3 | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | -0.00 | 0.97 | 22 | -1.24 | 0.50 | 8 |
| 4 | `rsi2_pullback(length=3, lo=10, hi=80, trend_ema=200)` | -0.14 | 0.88 | 17 | 0.69 | 99.00 | 1 |
| 5 | `rsi2_pullback(length=2, lo=20, hi=80, trend_ema=200)` | -0.23 | 0.91 | 102 | -0.23 | 0.89 | 16 |

[[Dashboard]] · [[Backtests/Sweep]]