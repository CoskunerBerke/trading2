---
updated: 2026-08-17T20:31:39+00:00
exchange: tradingview:BINANCE
timeframe: 4h
---
# 📊 Trading Bot — Dashboard
> Son çalışma: **2026-08-17 23:32** · Borsa: tradingview:BINANCE · TF: 4h · BTC rejimi: **YATAY**

Şema için: [[Trading Bot Şeması]] · Sinyal logu: [[Signals/Son Sinyal]] · Backtest: [[Backtests/Sweep]] · Portföy: [[Portfolio]]

## Akış (analiz → karar → sinyal)
```mermaid
flowchart LR
    BTC(("BTC<br/>BEKLE · 35")):::watch --> K
    ETH(("ETH<br/>BEKLE · 53")):::watch --> K
    SOL(("SOL<br/>BEKLE · 60")):::watch --> K
    BNB(("BNB<br/>BEKLE · 53")):::watch --> K
    XRP(("XRP<br/>BEKLE · 35")):::watch --> K
    ADA(("ADA<br/>BEKLE · 29")):::watch --> K
    DOGE(("DOGE<br/>BEKLE · 35")):::watch --> K
    AVAX(("AVAX<br/>BEKLE · 39")):::watch --> K
    LINK(("LINK<br/>BEKLE · 60")):::watch --> K
    DOT(("DOT<br/>BEKLE · 35")):::watch --> K
    K(("⚖️ KARAR<br/>YATAY")):::core --> S(("📣 SİNYAL<br/>AL 0 · SAT 0")):::core
    S --> P(("💼 PORTFÖY")):::core
    classDef buy fill:#1f7a3a,stroke:#3ddc84,color:#fff
    classDef sell fill:#8a1f1f,stroke:#ff5555,color:#fff
    classDef hold fill:#8a6d1f,stroke:#ffd54f,color:#fff
    classDef watch fill:#3a3a3a,stroke:#888,color:#ddd
    classDef core fill:#1e3a5f,stroke:#64b5f6,color:#fff
```

## Coin özeti
| Coin | Karar | Güven | Skor | Fiyat | 24s % | RSI | Rejim | Strateji | WFO Sharpe | WFO PF | Edge |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [[Coins/BTC\|BTC/USDT]] | ⚪ BEKLE | 35 | 35 | 64,310 | 1.9 | 70 | YATAY | EMA Trend Takibi | -1.91 | 0.34 | ❌ |
| [[Coins/ETH\|ETH/USDT]] | ⚪ BEKLE | 53 | 53 | 1,907 | 1.1 | 61 | YATAY | Donchian Kırılım | 0.05 | 1.05 | ❌ |
| [[Coins/SOL\|SOL/USDT]] | ⚪ BEKLE | 60 | 60 | 75.81 | 0.8 | 53 | YÜKSELİŞ | EMA Trend Takibi | -0.06 | 0.76 | ❌ |
| [[Coins/BNB\|BNB/USDT]] | ⚪ BEKLE | 53 | 53 | 605.24 | -0.1 | 46 | YATAY | EMA Trend Takibi | -0.59 | 0.59 | ❌ |
| [[Coins/XRP\|XRP/USDT]] | ⚪ BEKLE | 35 | 35 | 1.00 | 0.0 | 46 | DÜŞÜŞ | EMA Geri Çekilme | -0.97 | 0.46 | ❌ |
| [[Coins/ADA\|ADA/USDT]] | ⚪ BEKLE | 29 | 29 | 0.1738 | -2.5 | 33 | DÜŞÜŞ | Donchian Kırılım | -0.71 | 0.59 | ❌ |
| [[Coins/DOGE\|DOGE/USDT]] | ⚪ BEKLE | 35 | 35 | 0.0703 | 0.5 | 53 | DÜŞÜŞ | EMA Trend Takibi | -1.44 | 0.29 | ❌ |
| [[Coins/AVAX\|AVAX/USDT]] | ⚪ BEKLE | 39 | 39 | 6.34 | -0.1 | 45 | YATAY | RSI(2) Trend İçi Geri Çekilme | -1.94 | 0.13 | ❌ |
| [[Coins/LINK\|LINK/USDT]] | ⚪ BEKLE | 60 | 60 | 9.50 | 0.3 | 62 | YÜKSELİŞ | RSI Ortalamaya Dönüş | 0.05 | 0.93 | ❌ |
| [[Coins/DOT\|DOT/USDT]] | ⚪ BEKLE | 35 | 35 | 0.7610 | -0.5 | 44 | DÜŞÜŞ | RSI Ortalamaya Dönüş | -1.70 | 0.01 | ❌ |

## Portföy
- Equity: **50.00 USDT** (başlangıç 50.00)
- Nakit: 50.00 · Açık pozisyon: 0 · Kapanan işlem: 0 · Gerçekleşen P&L: 0.00

> ⚠️ Bu bot yalnızca analiz/karar/sinyal üretir ve **kağıt** portföy tutar. Gerçek emir göndermez. Geçmiş performans gelecek getiriyi garanti etmez.