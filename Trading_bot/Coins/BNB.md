---
symbol: BNB/USDT
signal: WATCH
score: 53
price: 605.24
regime: YATAY
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ BNB/USDT — BEKLE (skor 53)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 605.24 | -0.1 | 46 | 606.87 | 605.58 | 592.72 | 3.57 | 0.59 | 18 | YATAY |

## Karar
**Karar:** ⚪ **BEKLE** · güven 53
- Edge yok — OOS zayıf: Sharpe -0.15, PF 0.87, 8 işlem
- Edge yok: OOS zayıf: Sharpe -0.15, PF 0.87, 8 işlem
- Rejim: YATAY (EMA200 üstü, ADX 18)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe -0.15, PF 0.87, 8 işlem
- Rejim: YATAY (EMA200 üstü, ADX 18)
- RSI14 46, ATR %0.59

## En iyi strateji: EMA Trend Takibi
`ema_trend(fast=9, slow=100, adx_min=20)` · ❌ OOS zayıf: Sharpe -0.15, PF 0.87, 8 işlem
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 5.68 | -0.35 | 5.32 |
| CAGR % | 4.03 | -0.58 | 2.62 |
| Sharpe | 0.91 | -0.15 | 0.64 |
| Max DD % | -3.04 | -2.24 | -3.04 |
| Win rate % | 47.06 | 37.50 | 44.00 |
| Profit factor | 2.42 | 0.87 | 1.78 |
| İşlem | 17.00 | 8.00 | 25.00 |
| Exposure % | 13.77 | 9.89 | 12.61 |
| Buy&Hold % | 70.38 | -33.34 | 12.83 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `ema_trend(fast=9, slow=100, adx_min=20)` | 0.91 | 2.42 | 17 | -0.15 | 0.87 | 8 |
| 2 | `bb_mr(length=20, mult=2.0, trend_ema=200)` | 0.90 | 1.91 | 18 | -2.66 | 0.19 | 6 |
| 3 | `ema_trend(fast=9, slow=100, adx_min=0)` | 0.87 | 1.82 | 32 | -0.79 | 0.57 | 15 |
| 4 | `ema_trend(fast=20, slow=50, adx_min=20)` | 0.86 | 2.35 | 15 | -0.64 | 0.49 | 7 |
| 5 | `rsi_mr(length=14, lo=30, hi=60, trend_ema=0)` | 0.77 | 1.41 | 29 | -0.40 | 0.85 | 15 |

[[Dashboard]] · [[Backtests/Sweep]]