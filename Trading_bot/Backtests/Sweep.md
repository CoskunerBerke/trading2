---
updated: 2026-08-17T20:13:05+00:00
tags: [trading, backtest]
---
# 🧪 Backtest Sıralaması (parametre taraması)
> 10 coin · 4h · in-sample %70 / out-of-sample %30 · komisyon+kayma dahil · ATR stop

**0/10** coinde OOS edge doğrulandı · **10/10** coinde strateji OOS'ta buy&hold'u geçti

| # | Coin | Strateji | IS Sharpe | OOS Sharpe | OOS Max DD | OOS Win % | OOS PF | OOS işlem | Strateji % | Buy&Hold % | Edge |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | [[Coins/SOL\|SOL/USDT]] | `ema_trend(fast=20, slow=50, adx_min=0)` | 1.11 | 0.25 | -2.4 | 36 | 1.19 | 11 | 0.7 | -44.2 | ❌ |
| 2 | [[Coins/BNB\|BNB/USDT]] | `ema_trend(fast=9, slow=100, adx_min=20)` | 0.91 | -0.15 | -2.2 | 38 | 0.87 | 8 | -0.3 | -33.3 | ❌ |
| 3 | [[Coins/DOT\|DOT/USDT]] | `ema_trend(fast=20, slow=100, adx_min=0)` | 0.74 | -0.85 | -2.7 | 17 | 0.40 | 6 | -1.8 | -63.6 | ❌ |
| 4 | [[Coins/AVAX\|AVAX/USDT]] | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 0.75 | -1.20 | -3.0 | 25 | 0.55 | 8 | -2.2 | -53.8 | ❌ |
| 5 | [[Coins/ETH\|ETH/USDT]] | `donchian(entry_len=55, exit_len=10)` | 1.53 | -1.21 | -3.9 | 30 | 0.27 | 10 | -3.0 | -38.2 | ❌ |
| 6 | [[Coins/ADA\|ADA/USDT]] | `donchian(entry_len=55, exit_len=10)` | 1.33 | -1.42 | -6.8 | 23 | 0.43 | 13 | -4.3 | -55.3 | ❌ |
| 7 | [[Coins/XRP\|XRP/USDT]] | `donchian(entry_len=40, exit_len=10)` | 1.67 | -1.70 | -6.9 | 31 | 0.31 | 13 | -4.9 | -52.1 | ❌ |
| 8 | [[Coins/LINK\|LINK/USDT]] | `rsi_mr(length=14, lo=25, hi=60, trend_ema=0)` | 2.39 | -2.01 | -5.6 | 25 | 0.19 | 8 | -4.1 | -27.7 | ❌ |
| 9 | [[Coins/DOGE\|DOGE/USDT]] | `donchian(entry_len=40, exit_len=10)` | 1.10 | -2.13 | -8.2 | 18 | 0.27 | 11 | -5.6 | -49.7 | ❌ |
| 10 | [[Coins/BTC\|BTC/USDT]] | `rsi_mr(length=14, lo=25, hi=70, trend_ema=0)` | 1.45 | -2.19 | -6.3 | 18 | 0.27 | 11 | -4.4 | -28.9 | ❌ |

Seçim kuralı: in-sample Sharpe'a göre en iyi konfigürasyon seçilir, sonra hiç görmediği son %30 veride test edilir. OOS Sharpe/PF eşiği geçilmezse coin **BEKLE**'de kalır. Yüksek win-rate ≠ yüksek getiri; sıralama Sharpe'a göredir.

[[Dashboard]]