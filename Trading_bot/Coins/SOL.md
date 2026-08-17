---
symbol: SOL/USDT
signal: WATCH
score: 60
price: 75.81
regime: YÜKSELİŞ
has_edge: false
updated: 2026-08-17T21:36:17+00:00
tags: [trading, coin]
---
# ⚪ SOL/USDT — BEKLE (skor 60)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 75.81 | 0.8 | 53 | 75.58 | 75.51 | 75.29 | 0.6687 | 0.88 | 24 | YÜKSELİŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 60
- Edge yok — ❌ WFO OOS (4 adım): Sharpe -0.06 < 0.3, PF 0.76 < 1.1, 22 işlem, getiri -0.9% vs B&H -51.7%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -0.06 < 0.3, PF 0.76 < 1.1, 22 işlem, getiri -0.9% vs B&H -51.7%
- Rejim: YÜKSELİŞ (EMA200 üstü, ADX 24)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -0.06 < 0.3, PF 0.76 < 1.1, 22 işlem, getiri -0.9% vs B&H -51.7%
- Rejim: YÜKSELİŞ (EMA200 üstü, ADX 24)
- RSI14 53, ATR %0.88

## En iyi strateji: EMA Trend Takibi
`ema_trend(fast=20, slow=50, adx_min=0)` · ❌ ❌ WFO OOS (4 adım): Sharpe -0.06 < 0.3, PF 0.76 < 1.1, 22 işlem, getiri -0.9% vs B&H -51.7%
- Strateji durumu: **LONG** (2 bardır, giriş 75.79, stop 74.40)
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 13.07 | -0.90 | 0.68 |
| CAGR % | 6.34 | -0.82 | 1.14 |
| Sharpe | 0.70 | -0.06 | 0.18 |
| Max DD % | -9.76 | -9.75 | -3.79 |
| Win rate % | 50.00 | 31.82 | 36.36 |
| Profit factor | 1.56 | 0.76 | 1.12 |
| İşlem | 40.00 | 22.00 | 11.00 |
| Exposure % | 18.79 | 19.05 | 20.40 |
| Buy&Hold % | -46.30 | -51.73 | -44.19 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `ema_trend(fast=20, slow=50, adx_min=0)` | 1.38 | 3.19 | 17.9 | 5 |
| 2 | 2025-10-20 | 2026-01-28 | `ema_trend(fast=20, slow=50, adx_min=0)` | -2.27 | -4.56 | -33.6 | 7 |
| 3 | 2026-01-28 | 2026-05-09 | `ema_trend(fast=20, slow=50, adx_min=0)` | -0.30 | -0.91 | -23.8 | 6 |
| 4 | 2026-05-09 | 2026-08-17 | `ema_trend(fast=20, slow=50, adx_min=0)` | 0.79 | 1.55 | -19.0 | 4 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `ema_trend(fast=20, slow=50, adx_min=0)` | 0.70 | 1.56 | 40 | 0.18 | 1.12 | 11 |
| 2 | `donchian(entry_len=40, exit_len=10)` | 0.41 | 1.29 | 43 | -0.76 | 0.63 | 11 |
| 3 | `donchian(entry_len=40, exit_len=20)` | 0.17 | 1.09 | 43 | -1.12 | 0.48 | 11 |
| 4 | `ema_trend(fast=20, slow=100, adx_min=20)` | 0.17 | 1.16 | 20 | -0.45 | 0.64 | 6 |
| 5 | `ema_trend(fast=20, slow=100, adx_min=0)` | 0.11 | 1.07 | 30 | -0.07 | 0.93 | 12 |

[[Dashboard]] · [[Backtests/Sweep]]