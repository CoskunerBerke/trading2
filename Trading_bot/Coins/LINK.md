---
symbol: LINK/USDT
signal: WATCH
score: 60
price: 9.496
regime: YÜKSELİŞ
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ LINK/USDT — BEKLE (skor 60)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 9.50 | 0.3 | 62 | 9.33 | 9.01 | 8.53 | 0.1516 | 1.60 | 47 | YÜKSELİŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 60
- Edge yok — OOS zayıf: Sharpe -2.01, PF 0.19, 8 işlem
- Edge yok: OOS zayıf: Sharpe -2.01, PF 0.19, 8 işlem
- Rejim: YÜKSELİŞ (EMA200 üstü, ADX 47)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe -2.01, PF 0.19, 8 işlem
- Rejim: YÜKSELİŞ (EMA200 üstü, ADX 47)
- RSI14 62, ATR %1.60

## En iyi strateji: RSI Ortalamaya Dönüş
`rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` · ❌ OOS zayıf: Sharpe -2.01, PF 0.19, 8 işlem
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 12.57 | -4.11 | 7.94 |
| CAGR % | 8.83 | -6.75 | 3.90 |
| Sharpe | 2.39 | -2.01 | 1.10 |
| Max DD % | -2.00 | -5.60 | -5.60 |
| Win rate % | 75.00 | 25.00 | 58.33 |
| Profit factor | 5.79 | 0.19 | 1.96 |
| İşlem | 16.00 | 8.00 | 24.00 |
| Exposure % | 11.78 | 10.88 | 11.51 |
| Buy&Hold % | 31.02 | -27.68 | -5.89 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 2.39 | 5.79 | 16 | -2.01 | 0.19 | 8 |
| 2 | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | 1.96 | 5.50 | 16 | -1.72 | 0.29 | 8 |
| 3 | `rsi_mr(length=14, lo=30, hi=60, trend_ema=0)` | 1.21 | 1.66 | 30 | -1.21 | 0.56 | 14 |
| 4 | `rsi_mr(length=14, lo=30, hi=70, trend_ema=0)` | 1.15 | 1.68 | 30 | -1.23 | 0.53 | 14 |
| 5 | `ema_trend(fast=20, slow=200, adx_min=0)` | 0.70 | 2.00 | 15 | 0.45 | 0.36 | 7 |

[[Dashboard]] · [[Backtests/Sweep]]