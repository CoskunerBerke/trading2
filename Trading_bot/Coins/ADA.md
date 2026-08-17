---
symbol: ADA/USDT
signal: WATCH
score: 29
price: 0.1738
regime: DÜŞÜŞ
has_edge: false
updated: 2026-08-17T21:36:17+00:00
tags: [trading, coin]
---
# ⚪ ADA/USDT — BEKLE (skor 29)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 0.1738 | -2.5 | 33 | 0.1777 | 0.1819 | 0.1799 | 0.0027 | 1.55 | 36 | DÜŞÜŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 29
- Edge yok — ❌ WFO OOS (4 adım): Sharpe -0.71 < 0.3, PF 0.59 < 1.1, 22 işlem, getiri -6.9% vs B&H -75.4%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -0.71 < 0.3, PF 0.59 < 1.1, 22 işlem, getiri -6.9% vs B&H -75.4%
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 36)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -0.71 < 0.3, PF 0.59 < 1.1, 22 işlem, getiri -6.9% vs B&H -75.4%
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 36)
- RSI14 33, ATR %1.55

## En iyi strateji: Donchian Kırılım
`donchian(entry_len=40, exit_len=20)` · ❌ ❌ WFO OOS (4 adım): Sharpe -0.71 < 0.3, PF 0.59 < 1.1, 22 işlem, getiri -6.9% vs B&H -75.4%
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 20.51 | -6.90 | -0.90 |
| CAGR % | 9.78 | -6.30 | -1.49 |
| Sharpe | 0.81 | -0.71 | -0.09 |
| Max DD % | -15.31 | -13.88 | -11.17 |
| Win rate % | 43.90 | 31.82 | 41.67 |
| Profit factor | 1.67 | 0.59 | 0.90 |
| İşlem | 41.00 | 22.00 | 12.00 |
| Exposure % | 16.97 | 11.83 | 13.55 |
| Buy&Hold % | -47.90 | -75.45 | -55.28 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `donchian(entry_len=55, exit_len=10)` | 0.89 | 2.45 | -8.1 | 6 |
| 2 | 2025-10-20 | 2026-01-28 | `donchian(entry_len=55, exit_len=10)` | -0.87 | -1.54 | -46.3 | 3 |
| 3 | 2026-01-28 | 2026-05-09 | `donchian(entry_len=55, exit_len=10)` | -4.45 | -7.64 | -21.6 | 7 |
| 4 | 2026-05-09 | 2026-08-17 | `donchian(entry_len=55, exit_len=10)` | 0.03 | -0.07 | -36.5 | 6 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `donchian(entry_len=40, exit_len=20)` | 0.81 | 1.67 | 41 | -0.09 | 0.90 | 12 |
| 2 | `donchian(entry_len=55, exit_len=20)` | 0.78 | 1.66 | 35 | -0.96 | 0.51 | 12 |
| 3 | `donchian(entry_len=40, exit_len=10)` | 0.75 | 1.56 | 43 | -0.98 | 0.59 | 14 |
| 4 | `donchian(entry_len=55, exit_len=10)` | 0.75 | 1.60 | 36 | -1.43 | 0.43 | 13 |
| 5 | `ema_trend(fast=9, slow=200, adx_min=0)` | 0.72 | 1.76 | 24 | 0.01 | 0.97 | 7 |

[[Dashboard]] · [[Backtests/Sweep]]