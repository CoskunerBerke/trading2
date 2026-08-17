---
symbol: XRP/USDT
signal: WATCH
score: 35
price: 1.0015
regime: DÜŞÜŞ
has_edge: false
updated: 2026-08-17T20:31:39+00:00
tags: [trading, coin]
---
# ⚪ XRP/USDT — BEKLE (skor 35)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 1.00 | 0.0 | 46 | 1.00 | 1.01 | 1.06 | 0.0080 | 0.80 | 32 | DÜŞÜŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 35
- Edge yok — ❌ WFO OOS (4 adım): Sharpe -0.97 < 0.3, PF 0.46 < 1.1, 26 işlem, getiri -6.2% vs B&H -64.5%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -0.97 < 0.3, PF 0.46 < 1.1, 26 işlem, getiri -6.2% vs B&H -64.5%
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 32)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe -0.97 < 0.3, PF 0.46 < 1.1, 26 işlem, getiri -6.2% vs B&H -64.5%
- Rejim: DÜŞÜŞ (EMA200 altı, ADX 32)
- RSI14 46, ATR %0.80

## En iyi strateji: EMA Geri Çekilme
`ema_pullback(fast=10, slow=50, trend=200)` · ❌ ❌ WFO OOS (4 adım): Sharpe -0.97 < 0.3, PF 0.46 < 1.1, 26 işlem, getiri -6.2% vs B&H -64.5%
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 42.19 | -6.22 | -4.66 |
| CAGR % | 19.25 | -5.68 | -7.64 |
| Sharpe | 1.31 | -0.97 | -2.35 |
| Max DD % | -11.89 | -11.85 | -5.71 |
| Win rate % | 23.94 | 15.38 | 0.00 |
| Profit factor | 2.07 | 0.46 | 0.00 |
| İşlem | 71.00 | 26.00 | 10.00 |
| Exposure % | 15.39 | 7.51 | 4.03 |
| Buy&Hold % | 76.94 | -64.47 | -52.10 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `ema_pullback(fast=10, slow=50, trend=200)` | 0.41 | 1.03 | -12.2 | 15 |
| 2 | 2025-10-20 | 2026-01-28 | `ema_pullback(fast=10, slow=50, trend=200)` | -3.14 | -2.94 | -24.3 | 4 |
| 3 | 2026-01-28 | 2026-05-09 | `ema_pullback(fast=10, slow=50, trend=200)` | -1.67 | -1.04 | -24.1 | 3 |
| 4 | 2026-05-09 | 2026-08-17 | `ema_pullback(fast=10, slow=50, trend=200)` | -3.35 | -3.36 | -29.6 | 4 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `ema_pullback(fast=10, slow=50, trend=200)` | 1.31 | 2.07 | 71 | -2.35 | 0.00 | 10 |
| 2 | `donchian(entry_len=40, exit_len=10)` | 1.16 | 1.77 | 45 | -1.62 | 0.33 | 13 |
| 3 | `donchian(entry_len=20, exit_len=10)` | 1.13 | 1.61 | 65 | -1.36 | 0.44 | 18 |
| 4 | `donchian(entry_len=20, exit_len=20)` | 1.05 | 1.60 | 62 | -1.56 | 0.40 | 18 |
| 5 | `donchian(entry_len=40, exit_len=20)` | 0.96 | 1.63 | 45 | -1.69 | 0.30 | 13 |

[[Dashboard]] · [[Backtests/Sweep]]