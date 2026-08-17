---
symbol: ETH/USDT
signal: WATCH
score: 53
price: 1907.25
regime: YATAY
has_edge: false
updated: 2026-08-17T21:36:17+00:00
tags: [trading, coin]
---
# ⚪ ETH/USDT — BEKLE (skor 53)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 1,907 | 1.1 | 61 | 1,892 | 1,891 | 1,874 | 13.61 | 0.71 | 17 | YATAY |

## Karar
**Karar:** ⚪ **BEKLE** · güven 53
- Edge yok — ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 1.05 < 1.1, 20 işlem, getiri +0.1% vs B&H -34.0%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 1.05 < 1.1, 20 işlem, getiri +0.1% vs B&H -34.0%
- Rejim: YATAY (EMA200 üstü, ADX 17)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 1.05 < 1.1, 20 işlem, getiri +0.1% vs B&H -34.0%
- Rejim: YATAY (EMA200 üstü, ADX 17)
- RSI14 61, ATR %0.71

## En iyi strateji: Donchian Kırılım
`donchian(entry_len=40, exit_len=10)` · ❌ ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 1.05 < 1.1, 20 işlem, getiri +0.1% vs B&H -34.0%
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 14.58 | 0.14 | -2.24 |
| CAGR % | 7.04 | 0.12 | -3.71 |
| Sharpe | 0.84 | 0.05 | -0.58 |
| Max DD % | -9.46 | -9.23 | -4.25 |
| Win rate % | 39.53 | 35.00 | 27.27 |
| Profit factor | 1.53 | 1.05 | 0.63 |
| İşlem | 43.00 | 20.00 | 11.00 |
| Exposure % | 18.75 | 14.82 | 13.85 |
| Buy&Hold % | -26.83 | -33.99 | -38.21 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `donchian(entry_len=55, exit_len=10)` | 3.16 | 8.55 | 35.1 | 6 |
| 2 | 2025-10-20 | 2026-01-28 | `donchian(entry_len=55, exit_len=10)` | -1.66 | -2.57 | -24.4 | 5 |
| 3 | 2026-01-28 | 2026-05-09 | `donchian(entry_len=55, exit_len=10)` | -2.66 | -4.57 | -21.6 | 6 |
| 4 | 2026-05-09 | 2026-08-17 | `ema_trend(fast=9, slow=100, adx_min=20)` | -0.79 | -0.78 | -17.6 | 3 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `donchian(entry_len=40, exit_len=10)` | 0.84 | 1.53 | 43 | -0.58 | 0.63 | 11 |
| 2 | `donchian(entry_len=55, exit_len=10)` | 0.77 | 1.53 | 36 | -1.33 | 0.24 | 10 |
| 3 | `ema_trend(fast=9, slow=100, adx_min=20)` | 0.73 | 1.74 | 16 | -2.07 | 0.07 | 6 |
| 4 | `donchian(entry_len=40, exit_len=20)` | 0.68 | 1.41 | 43 | -0.80 | 0.52 | 11 |
| 5 | `donchian(entry_len=55, exit_len=20)` | 0.63 | 1.42 | 36 | -1.56 | 0.16 | 10 |

[[Dashboard]] · [[Backtests/Sweep]]