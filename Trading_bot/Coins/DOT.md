---
symbol: DOT/USDT
signal: WATCH
score: 35
price: 0.761
regime: DÜŞÜŞ
has_edge: false
updated: 2026-08-17T21:36:17+00:00
tags: [trading, coin]
---
# ⚪ DOT/USDT — BEKLE (skor 35)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 0.7610 | -0.5 | 44 | 0.7653 | 0.7771 | 0.8106 | 0.0087 | 1.14 | 20 | DÜŞÜŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 35
- Edge yok — ❌ WFO OOS (4 adım): Sharpe -1.70 < 0.3, PF 0.01 < 1.1, 9 işlem, getiri -9.0% vs B&H -80.3%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -1.70 < 0.3, PF 0.01 < 1.1, 9 işlem, getiri -9.0% vs B&H -80.3%
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 20)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -1.70 < 0.3, PF 0.01 < 1.1, 9 işlem, getiri -9.0% vs B&H -80.3%
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 20)
- RSI14 44, ATR %1.14

## En iyi strateji: RSI Ortalamaya Dönüş
`rsi_mr(length=7, lo=35, hi=60, trend_ema=200)` · ❌ ❌ WFO OOS (4 adım): Sharpe -1.70 < 0.3, PF 0.01 < 1.1, 9 işlem, getiri -9.0% vs B&H -80.3%
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 1.97 | -8.97 | 0.93 |
| CAGR % | 0.98 | -8.21 | 1.56 |
| Sharpe | 0.22 | -1.70 | 0.60 |
| Max DD % | -5.95 | -10.46 | -1.79 |
| Win rate % | 56.67 | 11.11 | 66.67 |
| Profit factor | 1.11 | 0.01 | 1.60 |
| İşlem | 30.00 | 9.00 | 3.00 |
| Exposure % | 5.94 | 5.40 | 2.05 |
| Buy&Hold % | -82.54 | -80.30 | -63.64 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `ema_trend(fast=20, slow=50, adx_min=20)` | -0.09 | -0.17 | -22.6 | 2 |
| 2 | 2025-10-20 | 2026-01-28 | `ema_trend(fast=9, slow=200, adx_min=20)` | -1.24 | -1.94 | -39.7 | 2 |
| 3 | 2026-01-28 | 2026-05-09 | `ema_trend(fast=20, slow=100, adx_min=20)` | -2.89 | -4.51 | -24.7 | 3 |
| 4 | 2026-05-09 | 2026-08-17 | `ema_trend(fast=9, slow=200, adx_min=20)` | -3.65 | -2.63 | -44.0 | 2 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `rsi_mr(length=7, lo=35, hi=60, trend_ema=200)` | 0.22 | 1.11 | 30 | 0.60 | 1.60 | 3 |
| 2 | `ema_trend(fast=20, slow=100, adx_min=20)` | 0.21 | 1.17 | 17 | -2.05 | 0.00 | 4 |
| 3 | `ema_trend(fast=20, slow=100, adx_min=0)` | 0.19 | 1.14 | 23 | -1.22 | 0.28 | 6 |
| 4 | `donchian(entry_len=20, exit_len=10)` | 0.18 | 1.07 | 63 | -0.06 | 0.91 | 13 |
| 5 | `ema_trend(fast=9, slow=200, adx_min=20)` | 0.15 | 1.11 | 19 | -1.46 | 0.18 | 6 |

[[Dashboard]] · [[Backtests/Sweep]]