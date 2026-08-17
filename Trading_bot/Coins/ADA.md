---
symbol: ADA/USDT
signal: WATCH
score: 29
price: 0.1738
regime: DÜŞÜŞ
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ ADA/USDT — BEKLE (skor 29)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 0.1738 | -2.5 | 33 | 0.1777 | 0.1819 | 0.1799 | 0.0027 | 1.55 | 36 | DÜŞÜŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 29
- Edge yok — OOS zayıf: Sharpe -1.42, PF 0.43, 13 işlem
- Edge yok: OOS zayıf: Sharpe -1.42, PF 0.43, 13 işlem
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 36)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe -1.42, PF 0.43, 13 işlem
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 36)
- RSI14 33, ATR %1.55

## En iyi strateji: Donchian Kırılım
`donchian(entry_len=55, exit_len=10)` · ❌ OOS zayıf: Sharpe -1.42, PF 0.43, 13 işlem
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 13.99 | -4.29 | 9.10 |
| CAGR % | 9.81 | -7.05 | 4.45 |
| Sharpe | 1.33 | -1.42 | 0.69 |
| Max DD % | -3.98 | -6.81 | -7.84 |
| Win rate % | 47.83 | 23.08 | 38.89 |
| Profit factor | 3.04 | 0.43 | 1.59 |
| İşlem | 23.00 | 13.00 | 36.00 |
| Exposure % | 14.03 | 10.27 | 12.90 |
| Buy&Hold % | 17.30 | -55.28 | -47.90 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `donchian(entry_len=55, exit_len=10)` | 1.33 | 3.04 | 23 | -1.42 | 0.43 | 13 |
| 2 | `donchian(entry_len=55, exit_len=20)` | 1.29 | 2.83 | 23 | -1.01 | 0.50 | 12 |
| 3 | `donchian(entry_len=40, exit_len=10)` | 1.20 | 2.49 | 29 | -0.97 | 0.60 | 14 |
| 4 | `donchian(entry_len=40, exit_len=20)` | 1.07 | 2.14 | 29 | -0.22 | 0.85 | 12 |
| 5 | `donchian(entry_len=20, exit_len=10)` | 0.87 | 1.59 | 48 | -1.23 | 0.56 | 21 |

[[Dashboard]] · [[Backtests/Sweep]]