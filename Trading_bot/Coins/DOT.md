---
symbol: DOT/USDT
signal: WATCH
score: 35
price: 0.761
regime: DÜŞÜŞ
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ DOT/USDT — BEKLE (skor 35)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 0.7610 | -0.5 | 44 | 0.7653 | 0.7771 | 0.8106 | 0.0087 | 1.14 | 20 | DÜŞÜŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 35
- Edge yok — OOS zayıf: Sharpe -0.85, PF 0.40, 6 işlem
- Edge yok: OOS zayıf: Sharpe -0.85, PF 0.40, 6 işlem
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 20)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe -0.85, PF 0.40, 6 işlem
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 20)
- RSI14 44, ATR %1.14

## En iyi strateji: EMA Trend Takibi
`ema_trend(fast=20, slow=100, adx_min=0)` · ❌ OOS zayıf: Sharpe -0.85, PF 0.40, 6 işlem
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 5.31 | -1.83 | 3.38 |
| CAGR % | 3.77 | -3.04 | 1.68 |
| Sharpe | 0.74 | -0.85 | 0.37 |
| Max DD % | -4.56 | -2.68 | -4.56 |
| Win rate % | 41.18 | 16.67 | 34.78 |
| Profit factor | 1.90 | 0.40 | 1.37 |
| İşlem | 17.00 | 6.00 | 23.00 |
| Exposure % | 10.73 | 7.91 | 9.89 |
| Buy&Hold % | -51.18 | -63.64 | -82.54 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `ema_trend(fast=20, slow=100, adx_min=0)` | 0.74 | 1.90 | 17 | -0.85 | 0.40 | 6 |
| 2 | `ema_trend(fast=9, slow=100, adx_min=0)` | 0.53 | 1.41 | 28 | -1.48 | 0.30 | 9 |
| 3 | `ema_trend(fast=20, slow=50, adx_min=20)` | 0.51 | 1.52 | 16 | -3.48 | 0.00 | 11 |
| 4 | `ema_trend(fast=9, slow=100, adx_min=20)` | 0.48 | 1.44 | 19 | -2.36 | 0.00 | 6 |
| 5 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 0.47 | 1.39 | 18 | -2.41 | 0.02 | 7 |

[[Dashboard]] · [[Backtests/Sweep]]