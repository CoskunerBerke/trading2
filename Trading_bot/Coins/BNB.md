---
symbol: BNB/USDT
signal: WATCH
score: 53
price: 605.24
regime: YATAY
has_edge: false
updated: 2026-08-17T21:36:17+00:00
tags: [trading, coin]
---
# ⚪ BNB/USDT — BEKLE (skor 53)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 605.24 | -0.1 | 46 | 606.87 | 605.58 | 592.72 | 3.57 | 0.59 | 18 | YATAY |

## Karar
**Karar:** ⚪ **BEKLE** · güven 53
- Edge yok — ❌ WFO OOS (4 adım): Sharpe -0.59 < 0.3, PF 0.59 < 1.1, 14 işlem, getiri -3.1% vs B&H -11.2%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -0.59 < 0.3, PF 0.59 < 1.1, 14 işlem, getiri -3.1% vs B&H -11.2%
- Rejim: YATAY (EMA200 üstü, ADX 18)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -0.59 < 0.3, PF 0.59 < 1.1, 14 işlem, getiri -3.1% vs B&H -11.2%
- Rejim: YATAY (EMA200 üstü, ADX 18)
- RSI14 46, ATR %0.59

## En iyi strateji: EMA Trend Takibi
`ema_trend(fast=9, slow=100, adx_min=20)` · ❌ ❌ WFO OOS (4 adım): Sharpe -0.59 < 0.3, PF 0.59 < 1.1, 14 işlem, getiri -3.1% vs B&H -11.2%
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 7.52 | -3.09 | -0.38 |
| CAGR % | 3.69 | -2.82 | -0.63 |
| Sharpe | 0.68 | -0.59 | -0.13 |
| Max DD % | -3.88 | -6.02 | -2.67 |
| Win rate % | 44.00 | 28.57 | 37.50 |
| Profit factor | 1.90 | 0.59 | 0.88 |
| İşlem | 25.00 | 14.00 | 8.00 |
| Exposure % | 12.61 | 9.63 | 9.89 |
| Buy&Hold % | 12.83 | -11.16 | -33.34 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | 3.48 | 2.79 | 60.5 | 1 |
| 2 | 2025-10-20 | 2026-01-28 | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | -0.04 | -0.11 | -18.0 | 4 |
| 3 | 2026-01-28 | 2026-05-09 | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | -3.61 | -4.79 | -27.5 | 4 |
| 4 | 2026-05-09 | 2026-08-17 | `ema_trend(fast=9, slow=100, adx_min=20)` | -0.69 | -0.87 | -6.9 | 5 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `ema_trend(fast=9, slow=100, adx_min=20)` | 0.68 | 1.90 | 25 | -0.13 | 0.88 | 8 |
| 2 | `rsi_mr(length=14, lo=30, hi=60, trend_ema=0)` | 0.61 | 1.29 | 44 | -0.45 | 0.83 | 15 |
| 3 | `ema_trend(fast=9, slow=100, adx_min=0)` | 0.50 | 1.39 | 47 | -0.77 | 0.58 | 15 |
| 4 | `ema_trend(fast=20, slow=50, adx_min=20)` | 0.49 | 1.60 | 22 | -0.64 | 0.49 | 7 |
| 5 | `bb_mr(length=20, mult=2.0, trend_ema=200)` | 0.47 | 1.38 | 24 | -2.66 | 0.19 | 6 |

[[Dashboard]] · [[Backtests/Sweep]]