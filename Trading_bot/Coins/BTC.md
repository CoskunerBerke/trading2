---
symbol: BTC/USDT
signal: WATCH
score: 35
price: 64309.96
regime: YATAY
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ BTC/USDT — BEKLE (skor 35)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 64,310 | 1.9 | 70 | 63,449 | 63,607 | 63,985 | 385.13 | 0.60 | 24 | YATAY |

## Karar
**Karar:** ⚪ **BEKLE** · güven 35
- Edge yok — OOS zayıf: Sharpe -2.19, PF 0.27, 11 işlem
- Edge yok: OOS zayıf: Sharpe -2.19, PF 0.27, 11 işlem
- Rejim: YATAY (EMA200 üstü, ADX 24)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe -2.19, PF 0.27, 11 işlem
- Rejim: YATAY (EMA200 üstü, ADX 24)
- RSI14 70, ATR %0.60

## En iyi strateji: RSI Ortalamaya Dönüş
`rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` · ❌ OOS zayıf: Sharpe -2.19, PF 0.27, 11 işlem
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 7.49 | -4.44 | 2.71 |
| CAGR % | 5.29 | -7.28 | 1.35 |
| Sharpe | 1.45 | -2.19 | 0.39 |
| Max DD % | -3.62 | -6.29 | -7.05 |
| Win rate % | 58.82 | 18.18 | 42.86 |
| Profit factor | 3.12 | 0.27 | 1.27 |
| İşlem | 17.00 | 11.00 | 28.00 |
| Exposure % | 11.39 | 10.58 | 11.14 |
| Buy&Hold % | 52.73 | -28.94 | 8.38 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | 1.45 | 3.12 | 17 | -2.19 | 0.27 | 11 |
| 2 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 1.33 | 2.82 | 17 | -2.37 | 0.25 | 11 |
| 3 | `ema_trend(fast=9, slow=200, adx_min=0)` | 1.28 | 2.99 | 15 | -2.35 | 0.04 | 9 |
| 4 | `ema_trend(fast=9, slow=100, adx_min=0)` | 1.26 | 2.08 | 26 | -2.04 | 0.17 | 13 |
| 5 | `ema_trend(fast=20, slow=50, adx_min=0)` | 0.95 | 1.62 | 28 | -0.55 | 0.56 | 11 |

[[Dashboard]] · [[Backtests/Sweep]]