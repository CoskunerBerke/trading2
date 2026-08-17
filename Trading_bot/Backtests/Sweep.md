---
updated: 2026-08-17T21:36:17+00:00
tags: [trading, backtest]
---
# 🧪 Backtest Sıralaması (parametre taraması)
> 10 coin · 4h · **walk-forward** (anchored, ileriye dönük) OOS · Binance komisyon+kayma+min emir dahil · ATR stop

**0/10** coinde OOS edge doğrulandı · **10/10** coinde strateji OOS'ta buy&hold'u geçti

| # | Coin | Strateji | IS Sharpe | WFO Sharpe | WFO Max DD | WFO Win % | WFO PF | WFO işlem | WFO getiri % | Buy&Hold % | Edge |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | [[Coins/LINK\|LINK/USDT]] | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 1.19 | 0.05 | -8.0 | 45 | 0.93 | 11 | 0.2 | -37.0 | ❌ |
| 2 | [[Coins/ETH\|ETH/USDT]] | `donchian(entry_len=40, exit_len=10)` | 0.84 | 0.05 | -9.2 | 35 | 1.05 | 20 | 0.1 | -34.0 | ❌ |
| 3 | [[Coins/SOL\|SOL/USDT]] | `ema_trend(fast=20, slow=50, adx_min=0)` | 0.70 | -0.06 | -9.8 | 32 | 0.76 | 22 | -0.9 | -51.7 | ❌ |
| 4 | [[Coins/BNB\|BNB/USDT]] | `ema_trend(fast=9, slow=100, adx_min=20)` | 0.68 | -0.59 | -6.0 | 29 | 0.59 | 14 | -3.1 | -11.2 | ❌ |
| 5 | [[Coins/ADA\|ADA/USDT]] | `donchian(entry_len=40, exit_len=20)` | 0.81 | -0.71 | -13.9 | 32 | 0.59 | 22 | -6.9 | -75.4 | ❌ |
| 6 | [[Coins/XRP\|XRP/USDT]] | `ema_pullback(fast=10, slow=50, trend=200)` | 1.31 | -0.97 | -11.8 | 15 | 0.46 | 26 | -6.2 | -64.5 | ❌ |
| 7 | [[Coins/DOGE\|DOGE/USDT]] | `ema_trend(fast=9, slow=200, adx_min=0)` | 0.52 | -1.44 | -11.2 | 23 | 0.29 | 13 | -8.7 | -64.0 | ❌ |
| 8 | [[Coins/DOT\|DOT/USDT]] | `rsi_mr(length=7, lo=35, hi=60, trend_ema=200)` | 0.22 | -1.70 | -10.5 | 11 | 0.01 | 9 | -9.0 | -80.3 | ❌ |
| 9 | [[Coins/BTC\|BTC/USDT]] | `ema_trend(fast=9, slow=100, adx_min=20)` | 0.73 | -1.91 | -10.4 | 21 | 0.34 | 19 | -7.5 | -44.5 | ❌ |
| 10 | [[Coins/AVAX\|AVAX/USDT]] | `rsi2_pullback(length=3, lo=10, hi=65, trend_ema=200)` | 0.40 | -1.94 | -12.5 | 8 | 0.13 | 13 | -11.1 | -68.9 | ❌ |

Seçim kuralı: her walk-forward adımında yalnızca o ana kadarki veriyle en iyi konfigürasyon seçilir ve hiç görülmemiş sonraki dönemde çalıştırılır; bu OOS dönemleri birleştirilir (WFO). WFO Sharpe/PF/işlem eşiği geçilmezse coin **BEKLE**'de kalır. Yüksek win-rate ≠ yüksek getiri; sıralama Sharpe'a göredir.

[[Dashboard]]