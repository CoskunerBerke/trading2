---
symbol: BTC/USDT
signal: WATCH
score: 35
price: 64309.96
regime: YATAY
has_edge: false
updated: 2026-08-17T20:31:39+00:00
tags: [trading, coin]
---
# ⚪ BTC/USDT — BEKLE (skor 35)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 64,310 | 1.9 | 70 | 63,449 | 63,607 | 63,985 | 385.13 | 0.60 | 24 | YATAY |

## Karar
**Karar:** ⚪ **BEKLE** · güven 35
- Edge yok — ❌ WFO OOS (4 adım): Sharpe -1.91 < 0.3, PF 0.34 < 1.1, 19 işlem, getiri -7.5% vs B&H -44.5%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -1.91 < 0.3, PF 0.34 < 1.1, 19 işlem, getiri -7.5% vs B&H -44.5%
- Rejim: YATAY (EMA200 üstü, ADX 24)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -1.91 < 0.3, PF 0.34 < 1.1, 19 işlem, getiri -7.5% vs B&H -44.5%
- Rejim: YATAY (EMA200 üstü, ADX 24)
- RSI14 70, ATR %0.60

## En iyi strateji: EMA Trend Takibi
`ema_trend(fast=9, slow=100, adx_min=20)` · ❌ ❌ WFO OOS (4 adım): Sharpe -1.91 < 0.3, PF 0.34 < 1.1, 19 işlem, getiri -7.5% vs B&H -44.5%
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 6.35 | -7.47 | -2.90 |
| CAGR % | 3.13 | -6.83 | -4.79 |
| Sharpe | 0.73 | -1.91 | -1.75 |
| Max DD % | -5.64 | -10.37 | -4.65 |
| Win rate % | 35.00 | 21.05 | 11.11 |
| Profit factor | 1.72 | 0.34 | 0.19 |
| İşlem | 20.00 | 19.00 | 9.00 |
| Exposure % | 8.61 | 10.79 | 7.76 |
| Buy&Hold % | 8.38 | -44.55 | -28.94 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `ema_trend(fast=9, slow=100, adx_min=0)` | 2.18 | 2.41 | -5.7 | 4 |
| 2 | 2025-10-20 | 2026-01-28 | `ema_trend(fast=9, slow=100, adx_min=0)` | -3.38 | -3.36 | -19.4 | 7 |
| 3 | 2026-01-28 | 2026-05-09 | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | -3.97 | -4.61 | -8.9 | 3 |
| 4 | 2026-05-09 | 2026-08-17 | `ema_trend(fast=9, slow=100, adx_min=20)` | -3.20 | -1.99 | -20.0 | 5 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `ema_trend(fast=9, slow=100, adx_min=20)` | 0.73 | 1.72 | 20 | -1.75 | 0.19 | 9 |
| 2 | `ema_trend(fast=20, slow=50, adx_min=20)` | 0.68 | 1.78 | 16 | -0.52 | 0.46 | 4 |
| 3 | `ema_trend(fast=20, slow=200, adx_min=20)` | 0.65 | 2.03 | 15 | -0.76 | 0.31 | 5 |
| 4 | `ema_trend(fast=9, slow=100, adx_min=0)` | 0.63 | 1.43 | 39 | -1.83 | 0.19 | 13 |
| 5 | `ema_trend(fast=20, slow=50, adx_min=0)` | 0.62 | 1.42 | 39 | -0.45 | 0.59 | 11 |

[[Dashboard]] · [[Backtests/Sweep]]