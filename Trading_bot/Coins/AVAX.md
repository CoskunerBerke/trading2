---
symbol: AVAX/USDT
signal: WATCH
score: 39
price: 6.342
regime: YATAY
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ AVAX/USDT — BEKLE (skor 39)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 6.34 | -0.1 | 45 | 6.39 | 6.43 | 6.52 | 0.0943 | 1.49 | 10 | YATAY |

## Karar
**Karar:** ⚪ **BEKLE** · güven 39
- Edge yok — OOS zayıf: Sharpe -1.20, PF 0.55, 8 işlem
- Edge yok: OOS zayıf: Sharpe -1.20, PF 0.55, 8 işlem
- Rejim: YATAY (EMA200 altı, ADX 10)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe -1.20, PF 0.55, 8 işlem
- Rejim: YATAY (EMA200 altı, ADX 10)
- RSI14 45, ATR %1.49

## En iyi strateji: RSI Ortalamaya Dönüş
`rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` · ❌ OOS zayıf: Sharpe -1.20, PF 0.55, 8 işlem
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 3.47 | -2.24 | 1.15 |
| CAGR % | 2.47 | -3.71 | 0.57 |
| Sharpe | 0.75 | -1.20 | 0.19 |
| Max DD % | -4.41 | -3.04 | -4.41 |
| Win rate % | 40.00 | 25.00 | 34.78 |
| Profit factor | 1.86 | 0.55 | 1.13 |
| İşlem | 15.00 | 8.00 | 23.00 |
| Exposure % | 7.18 | 7.08 | 7.15 |
| Buy&Hold % | -32.78 | -53.84 | -69.24 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 0.75 | 1.86 | 15 | -1.20 | 0.55 | 8 |
| 2 | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | 0.42 | 1.53 | 15 | -0.90 | 0.60 | 8 |
| 3 | `ema_trend(fast=9, slow=200, adx_min=0)` | -0.19 | 0.79 | 19 | -2.01 | 0.13 | 9 |
| 4 | `donchian(entry_len=20, exit_len=10)` | -0.25 | 0.87 | 55 | -3.59 | 0.05 | 21 |
| 5 | `donchian(entry_len=40, exit_len=10)` | -0.28 | 0.79 | 34 | -3.30 | 0.01 | 11 |

[[Dashboard]] · [[Backtests/Sweep]]