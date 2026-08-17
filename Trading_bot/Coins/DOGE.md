---
symbol: DOGE/USDT
signal: WATCH
score: 35
price: 0.07031
regime: DÜŞÜŞ
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ DOGE/USDT — BEKLE (skor 35)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 0.0703 | 0.5 | 53 | 0.0701 | 0.0702 | 0.0716 | 0.0005 | 0.78 | 23 | DÜŞÜŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 35
- Edge yok — OOS zayıf: Sharpe -2.13, PF 0.27, 11 işlem
- Edge yok: OOS zayıf: Sharpe -2.13, PF 0.27, 11 işlem
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 23)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe -2.13, PF 0.27, 11 işlem
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 23)
- RSI14 53, ATR %0.78

## En iyi strateji: Donchian Kırılım
`donchian(entry_len=40, exit_len=10)` · ❌ OOS zayıf: Sharpe -2.13, PF 0.27, 11 işlem
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 12.50 | -5.63 | 6.16 |
| CAGR % | 8.78 | -9.21 | 3.03 |
| Sharpe | 1.10 | -2.13 | 0.46 |
| Max DD % | -4.88 | -8.22 | -8.22 |
| Win rate % | 45.45 | 18.18 | 38.64 |
| Profit factor | 1.75 | 0.27 | 1.24 |
| İşlem | 33.00 | 11.00 | 44.00 |
| Exposure % | 15.95 | 5.78 | 12.90 |
| Buy&Hold % | 37.42 | -49.65 | -31.15 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `donchian(entry_len=40, exit_len=10)` | 1.10 | 1.75 | 33 | -2.13 | 0.27 | 11 |
| 2 | `donchian(entry_len=40, exit_len=20)` | 1.09 | 1.74 | 33 | -2.17 | 0.26 | 11 |
| 3 | `ema_trend(fast=9, slow=100, adx_min=0)` | 1.03 | 2.09 | 19 | -1.60 | 0.12 | 7 |
| 4 | `ema_trend(fast=9, slow=50, adx_min=0)` | 0.89 | 1.55 | 39 | -3.89 | 0.14 | 23 |
| 5 | `donchian(entry_len=20, exit_len=20)` | 0.83 | 1.38 | 50 | -2.40 | 0.20 | 19 |

[[Dashboard]] · [[Backtests/Sweep]]