# ARCHITECTURE — trading2 v3

> Amaç: 7/24 çalışan, Binance Spot + USDⓈ-M perpetual paper-trading **araştırma platformu**. Hedef "kesin kâr" değil;
> masraflar sonrası pozitif beklenen değer, kontrollü drawdown, düşük ruin riski, veri kalitesi, tekrarlanabilirlik, denetlenebilirlik.
> Varsayılan ve zorunlu mod **PAPER**. Gerçek emir yolu bu sürümde kapalıdır.

## Ana akış

```
MARKET UNIVERSE (market/universe.py)  →  DATA QUALITY GATE (market/quality.py)
→ FAST SCANNER tier-1 (scanner.py + market/scanner_fast.py)  →  CANDIDATE FUNNEL
→ COIN HEADS (coinhead/head.py; registry.py)  ←  SPECIALIST AGENTS (agents/* legacy + coinhead/specialists.py)
→ RED TEAM / VETO (coinhead/redteam.py)  →  COIN HEAD CONSENSUS (coinhead/factors.py)
→ CHIEF PORTFOLIO MANAGER (coinhead/chief.py)  →  GLOBAL RISK ENGINE (risk/engine.py + killswitch.py)
→ SPOT PLAN | FUTURES PLAN | NO_TRADE
→ PAPER EXECUTION (accounting/spot_ledger.py, futures_ledger.py; execution/gateway.py PaperGateway)
→ POSITION MONITOR (ledger.tick: stop/TP/liq/funding, intrabar worst-case)  →  ACCOUNTING (Decimal, fee/funding/slippage/tax ayrı)
→ OUTCOME LABELING (learn/labels.py)  →  LEARNING (learn/learner_v2.py + legacy learning.py)  →  MODEL VALIDATION (validation.py, learn/registry.py)
→ OBSIDIAN (obsidian*.py, obsidian_coinheads.py) + WEB DASHBOARD (dashboard/)
```

Orkestrasyon: `tradingbot/engine_v3.py::TradingEngineV3.tour()` (legacy `engine.py::TradingEngine` korunur; `--legacy`).

## Paketler

| Paket | Sorumluluk |
|---|---|
| `core/` | Decimal para aritmetiği, UTC zaman, atomik yazım, id/hash, olay zarfı, alan hataları |
| `storage/` | SQLite WAL (39 tablo, events journal, FTS5), Repository, Parquet CandleStore, idempotent legacy migration |
| `market/` | Binance resmi REST provider'ları (spot/fapi), rate budget + backoff, MarketFeed, DataQualityGate, dinamik universe, tier-1 scanner |
| `accounting/` | SymbolFilters/brackets, FeeSchedule, FundingSchedule (00/08/16 UTC), liquidation (MMR), SlippageModel, TaxPolicy (kapalı), SpotLedger (FIFO), FuturesLedgerV2 |
| `execution/` | Order state machine, clientOrderId, PaperGateway, Binance testnet gateway'leri (opt-in, env secrets), LiveGateway (kapalı), reconcile |
| `coinhead/` | SpecialistReport şeması, faktör grupları (korelasyonlu kanıt tek oy), yeni uzmanlar, red team, CoinHead, registry, Chief |
| `risk/` | Risk profilleri, PortfolioState (spot+futures birleşik), RiskEngine, KillSwitch (kalıcı, manuel reset), OperatingMode geçiş kapıları |
| `learn/` | Değişmez hafıza, özellikler v2, model+kalibrasyon, hiyerarşik shrinkage, postmortem, shadow trades, model registry, drift, retrieval |
| `llm/` | Provider interface (NoOp/Anthropic), JSON şema, bütçe/circuit breaker/cache, modlar, fail-closed servis |
| `dashboard/` | FastAPI (127.0.0.1), 13 sayfa, plotly (yerel), /health/live, /health/ready, /metrics, SSE |
| `ops/` | JSON log + rotasyon + redaksiyon, singleton kilit, health, backup/restore, doctor, notifier |
| legacy | `engine.py, agents/*, scanner.py, analyzer.py, sweep.py, backtest.py, learning.py, paper_futures.py, portfolio.py, obsidian*.py` — korunur |

## Deterministik kod vs LLM

Deterministik: veri doğrulama, göstergeler, boyut, risk, borsa kuralları, stop/hedef, komisyon/funding/P&L/liq, order state machine, kill switch.
LLM (opsiyonel, varsayılan NoOp/POSTMORTEM_ONLY): bull/bear tez, çelişki, geçmiş ders özeti, red-team görüşü, postmortem. **Tek başına işlem açamaz; stop/kaldıraç/risk değiştiremez; API anahtarı görmez; PAPER→TESTNET→LIVE geçişi yapamaz.**

## Nihai onay
Bir işlem yalnızca üç bayrak birlikte doğruysa açılır: `coin_head_valid` ∧ `no_red_team_veto` ∧ `risk_engine_allowed` (Chief tek başına yetkisiz; LLM onayı yetersiz).

## Durum dosyaları (`state/`)
`futures_ledger.json` (v2, legacy import), `spot_ledger.json`, `coin_heads.json`, `risk.json`, `killswitch.json`, `mode.json`, `health.json`, `heartbeat.json`,
`trade_memory.jsonl` (yalnız ekleme), `learn_v2.json`, `models.json`, `shadow_book.json`, `universe.json`, `llm_budget.json`, `llm_calls.jsonl`, `tradingbot.db` (SQLite),
legacy: `portfolio.json, signals.json, signals_log.jsonl, agents.json, scan.json, triggers.json, learning.json`. Tüm yazımlar atomik (`tmp + os.replace`, `.bak`).

Kayıt sırası: **defter → öğrenme → tetikler** (crash penceresinde çift öğrenme yok).
