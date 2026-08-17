---
symbol: ETH/USDT
signal: WATCH
score: 53
price: 1907.25
regime: YATAY
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ ETH/USDT — BEKLE (skor 53)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 1,907 | 1.1 | 61 | 1,892 | 1,891 | 1,874 | 13.61 | 0.71 | 17 | YATAY |

## Karar
**Karar:** ⚪ **BEKLE** · güven 53
- Edge yok — OOS zayıf: Sharpe -1.21, PF 0.27, 10 işlem
- Edge yok: OOS zayıf: Sharpe -1.21, PF 0.27, 10 işlem
- Rejim: YATAY (EMA200 üstü, ADX 17)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe -1.21, PF 0.27, 10 işlem
- Rejim: YATAY (EMA200 üstü, ADX 17)
- RSI14 61, ATR %0.71

## En iyi strateji: Donchian Kırılım
`donchian(entry_len=55, exit_len=10)` · ❌ OOS zayıf: Sharpe -1.21, PF 0.27, 10 işlem
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 14.69 | -2.96 | 11.30 |
| CAGR % | 10.29 | -4.89 | 5.50 |
| Sharpe | 1.53 | -1.21 | 0.94 |
| Max DD % | -5.04 | -3.88 | -5.81 |
| Win rate % | 50.00 | 30.00 | 44.44 |
| Profit factor | 2.34 | 0.27 | 1.73 |
| İşlem | 26.00 | 10.00 | 36.00 |
| Exposure % | 18.47 | 12.25 | 16.60 |
| Buy&Hold % | 18.63 | -38.21 | -26.83 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `donchian(entry_len=55, exit_len=10)` | 1.53 | 2.34 | 26 | -1.21 | 0.27 | 10 |
| 2 | `donchian(entry_len=40, exit_len=10)` | 1.44 | 2.04 | 32 | -0.61 | 0.62 | 11 |
| 3 | `donchian(entry_len=55, exit_len=20)` | 1.41 | 2.23 | 26 | -1.40 | 0.19 | 10 |
| 4 | `donchian(entry_len=40, exit_len=20)` | 1.30 | 1.91 | 32 | -0.79 | 0.52 | 11 |
| 5 | `donchian(entry_len=20, exit_len=10)` | 1.10 | 1.56 | 51 | -1.51 | 0.47 | 23 |

[[Dashboard]] · [[Backtests/Sweep]]