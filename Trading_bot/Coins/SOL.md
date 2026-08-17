---
symbol: SOL/USDT
signal: WATCH
score: 60
price: 75.81
regime: YÜKSELİŞ
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ SOL/USDT — BEKLE (skor 60)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 75.81 | 0.8 | 53 | 75.58 | 75.51 | 75.29 | 0.6687 | 0.88 | 24 | YÜKSELİŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 60
- Edge yok — OOS zayıf: Sharpe 0.25, PF 1.19, 11 işlem
- Edge yok: OOS zayıf: Sharpe 0.25, PF 1.19, 11 işlem
- Rejim: YÜKSELİŞ (EMA200 üstü, ADX 24)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe 0.25, PF 1.19, 11 işlem
- Rejim: YÜKSELİŞ (EMA200 üstü, ADX 24)
- RSI14 53, ATR %0.88

## En iyi strateji: EMA Trend Takibi
`ema_trend(fast=20, slow=50, adx_min=0)` · ❌ OOS zayıf: Sharpe 0.25, PF 1.19, 11 işlem
- Strateji durumu: **LONG** (2 bardır, giriş 75.79, stop 74.40)
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 9.98 | 0.69 | 10.74 |
| CAGR % | 7.03 | 1.16 | 5.23 |
| Sharpe | 1.11 | 0.25 | 0.88 |
| Max DD % | -5.29 | -2.36 | -5.34 |
| Win rate % | 55.17 | 36.36 | 50.00 |
| Profit factor | 2.08 | 1.19 | 1.80 |
| İşlem | 29.00 | 11.00 | 40.00 |
| Exposure % | 18.11 | 20.40 | 18.79 |
| Buy&Hold % | -3.51 | -44.19 | -46.30 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `ema_trend(fast=20, slow=50, adx_min=0)` | 1.11 | 2.08 | 29 | 0.25 | 1.19 | 11 |
| 2 | `donchian(entry_len=40, exit_len=10)` | 0.79 | 1.83 | 32 | -0.56 | 0.71 | 11 |
| 3 | `donchian(entry_len=40, exit_len=20)` | 0.61 | 1.59 | 32 | -0.89 | 0.56 | 11 |
| 4 | `ema_trend(fast=9, slow=50, adx_min=0)` | 0.55 | 1.29 | 42 | -0.62 | 0.72 | 19 |
| 5 | `rsi_mr(length=7, lo=35, hi=70, trend_ema=200)` | 0.47 | 1.21 | 36 | -1.56 | 0.20 | 5 |

[[Dashboard]] · [[Backtests/Sweep]]