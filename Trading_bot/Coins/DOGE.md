---
symbol: DOGE/USDT
signal: WATCH
score: 35
price: 0.07031
regime: DÜŞÜŞ
has_edge: false
updated: 2026-08-17T20:31:39+00:00
tags: [trading, coin]
---
# ⚪ DOGE/USDT — BEKLE (skor 35)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 0.0703 | 0.5 | 53 | 0.0701 | 0.0702 | 0.0716 | 0.0005 | 0.78 | 23 | DÜŞÜŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 35
- Edge yok — ❌ WFO OOS (4 adım): Sharpe -1.44 < 0.3, PF 0.29 < 1.1, 13 işlem, getiri -8.7% vs B&H -64.0%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -1.44 < 0.3, PF 0.29 < 1.1, 13 işlem, getiri -8.7% vs B&H -64.0%
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 23)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -1.44 < 0.3, PF 0.29 < 1.1, 13 işlem, getiri -8.7% vs B&H -64.0%
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 23)
- RSI14 53, ATR %0.78

## En iyi strateji: EMA Trend Takibi
`ema_trend(fast=9, slow=200, adx_min=0)` · ❌ ❌ WFO OOS (4 adım): Sharpe -1.44 < 0.3, PF 0.29 < 1.1, 13 işlem, getiri -8.7% vs B&H -64.0%
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 7.20 | -8.69 | -3.60 |
| CAGR % | 3.54 | -7.95 | -5.93 |
| Sharpe | 0.52 | -1.44 | -1.57 |
| Max DD % | -6.17 | -11.18 | -5.05 |
| Win rate % | 31.58 | 23.08 | 20.00 |
| Profit factor | 1.57 | 0.29 | 0.01 |
| İşlem | 19.00 | 13.00 | 5.00 |
| Exposure % | 6.33 | 4.94 | 2.97 |
| Buy&Hold % | -31.15 | -63.99 | -49.65 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `ema_trend(fast=9, slow=200, adx_min=20)` | 0.01 | -0.03 | -0.6 | 2 |
| 2 | 2025-10-20 | 2026-01-28 | `ema_trend(fast=20, slow=200, adx_min=0)` | -1.59 | -1.63 | -37.4 | 1 |
| 3 | 2026-01-28 | 2026-05-09 | `donchian(entry_len=40, exit_len=10)` | -2.57 | -6.30 | -9.8 | 9 |
| 4 | 2026-05-09 | 2026-08-17 | `ema_trend(fast=9, slow=200, adx_min=0)` | -2.34 | -0.92 | -35.8 | 1 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `ema_trend(fast=9, slow=200, adx_min=0)` | 0.52 | 1.57 | 19 | -1.57 | 0.01 | 5 |
| 2 | `donchian(entry_len=40, exit_len=10)` | 0.43 | 1.21 | 44 | -2.26 | 0.26 | 11 |
| 3 | `donchian(entry_len=40, exit_len=20)` | 0.41 | 1.20 | 44 | -2.30 | 0.26 | 11 |
| 4 | `ema_trend(fast=9, slow=100, adx_min=0)` | 0.32 | 1.23 | 26 | -1.79 | 0.09 | 7 |
| 5 | `ema_trend(fast=9, slow=50, adx_min=20)` | 0.17 | 1.09 | 29 | -1.33 | 0.38 | 9 |

[[Dashboard]] · [[Backtests/Sweep]]