---
symbol: LINK/USDT
signal: WATCH
score: 60
price: 9.496
regime: YÜKSELİŞ
has_edge: false
updated: 2026-08-17T20:31:39+00:00
tags: [trading, coin]
---
# ⚪ LINK/USDT — BEKLE (skor 60)
> Son kapanan bar: 2026-08-17 16:00:00+00:00 · tradingview:BINANCE · 4h · 4379 bar

## Anlık görüntü
| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |
|---|---|---|---|---|---|---|---|---|---|
| 9.50 | 0.3 | 62 | 9.33 | 9.01 | 8.53 | 0.1516 | 1.60 | 47 | YÜKSELİŞ |

## Karar
**Karar:** ⚪ **BEKLE** · güven 60
- Edge yok — ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 0.93 < 1.1, 11 işlem, getiri +0.2% vs B&H -37.0%
- Edge yok: ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 0.93 < 1.1, 11 işlem, getiri +0.2% vs B&H -37.0%
- Rejim: YÜKSELİŞ (EMA200 üstü, ADX 47)

## Analiz gerekçeleri
- Edge yok: ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 0.93 < 1.1, 11 işlem, getiri +0.2% vs B&H -37.0%
- Rejim: YÜKSELİŞ (EMA200 üstü, ADX 47)
- RSI14 62, ATR %1.60

## En iyi strateji: RSI Ortalamaya Dönüş
`rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` · ❌ ❌ WFO OOS (4 adım): Sharpe 0.05 < 0.3, PF 0.93 < 1.1, 11 işlem, getiri +0.2% vs B&H -37.0%
- Strateji durumu: **FLAT**
- Son barda giriş sinyali: hayır · çıkış sinyali: hayır

### Metrikler
| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |
|---|---|---|---|
| Toplam getiri % | 16.93 | 0.15 | -7.16 |
| CAGR % | 8.14 | 0.14 | -11.64 |
| Sharpe | 1.19 | 0.05 | -1.98 |
| Max DD % | -9.88 | -7.96 | -9.87 |
| Win rate % | 58.33 | 45.45 | 25.00 |
| Profit factor | 2.02 | 0.93 | 0.19 |
| İşlem | 24.00 | 11.00 | 8.00 |
| Exposure % | 11.51 | 10.71 | 10.88 |
| Buy&Hold % | -5.89 | -37.01 | -27.68 |

### Walk-forward adımları
| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |
|---|---|---|---|---|---|---|---|
| 1 | 2025-07-12 | 2025-10-20 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 2.40 | 3.44 | 22.9 | 4 |
| 2 | 2025-10-20 | 2026-01-28 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 1.01 | 1.67 | -37.2 | 2 |
| 3 | 2026-01-28 | 2026-05-09 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | -4.03 | -5.34 | -10.1 | 3 |
| 4 | 2026-05-09 | 2026-08-17 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 0.39 | 0.60 | -9.3 | 2 |

### Tarama — ilk 5 konfigürasyon
| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |
|---|---|---|---|---|---|---|---|
| 1 | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 1.19 | 2.02 | 24 | -1.98 | 0.19 | 8 |
| 2 | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | 1.05 | 2.00 | 24 | -1.66 | 0.31 | 8 |
| 3 | `bb_mr(length=20, mult=2.0, trend_ema=200)` | 0.76 | 1.86 | 20 | 2.24 | 18.61 | 4 |
| 4 | `ema_trend(fast=20, slow=200, adx_min=0)` | 0.61 | 1.37 | 22 | 0.36 | 0.34 | 7 |
| 5 | `rsi_mr(length=14, lo=30, hi=60, trend_ema=0)` | 0.48 | 1.19 | 44 | -1.31 | 0.53 | 14 |

[[Dashboard]] · [[Backtests/Sweep]]