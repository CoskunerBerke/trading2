---
symbol: XRP/USDT
signal: WATCH
score: 35
price: 1.0015
regime: DÜŞÜŞ
has_edge: false
updated: 2026-08-17T20:13:05+00:00
tags: [trading, coin]
---
# ⚪ XRP/USDT — BEKLE (skor 35)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · binance · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 1.00 | 0.0 | 46 | 1.00 | 1.01 | 1.06 | 0.0080 | 0.80 | 32 | DÜŞÜŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 35
- Edge yok — OOS zayıf: Sharpe -1.70, PF 0.31, 13 işlem
- Edge yok: OOS zayıf: Sharpe -1.70, PF 0.31, 13 işlem
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 32)

## Analiz gerekçeleri
- Edge yok: OOS zayıf: Sharpe -1.70, PF 0.31, 13 işlem
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 32)
- RSI14 46, ATR %0.80

## En iyi strateji: Donchian Kırılım
`donchian(entry_len=40, exit_len=10)` · ❌ OOS zayıf: Sharpe -1.70, PF 0.31, 13 işlem
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (%70) | Out-of-sample (%30) | Tüm veri |
|---|---|---|---|
| Toplam getiri % | 24.64 | -4.88 | 18.55 |
| CAGR % | 17.04 | -8.01 | 8.88 |
| Sharpe | 1.67 | -1.70 | 1.04 |
| Max DD % | -9.13 | -6.88 | -9.13 |
| Win rate % | 40.62 | 30.77 | 37.78 |
| Profit factor | 2.39 | 0.31 | 1.70 |
| İşlem | 32.00 | 13.00 | 45.00 |
| Exposure % | 17.29 | 8.75 | 14.73 |
| Buy&Hold % | 270.07 | -52.10 | 76.94 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | OOS Sharpe | OOS PF | OOS işlem |
|---|---|---|---|---|---|---|---|
| 1 | `donchian(entry_len=40, exit_len=10)` | 1.67 | 2.39 | 32 | -1.70 | 0.31 | 13 |
| 2 | `donchian(entry_len=20, exit_len=10)` | 1.60 | 2.04 | 47 | -1.31 | 0.45 | 18 |
| 3 | `donchian(entry_len=20, exit_len=20)` | 1.57 | 2.13 | 44 | -1.51 | 0.40 | 18 |
| 4 | `donchian(entry_len=40, exit_len=20)` | 1.46 | 2.19 | 32 | -1.77 | 0.28 | 13 |
| 5 | `donchian(entry_len=55, exit_len=10)` | 1.44 | 2.12 | 29 | -2.44 | 0.09 | 11 |

[[Dashboard]] · [[Backtests/Sweep]]