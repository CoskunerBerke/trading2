# Documentation index

Start with **[HOW_IT_WORKS.md](HOW_IT_WORKS.md)** (English): what the system is, how it works, and why, with links into
the code. The other documents below are the project's design records, research notes, runbooks and review closures.
Unless marked *(English)*, a document is written in Turkish.

Most of these files are dated records: they describe the state of the code and the reasoning *at the time they were
written*, and several were superseded later. When a document and the code disagree, the code and
[HOW_IT_WORKS.md](HOW_IT_WORKS.md) describe the current behaviour. Everything is paper trading only, and nothing here
should be read as a claim of profitability.

## Architecture and data

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Older overview of the v3 platform: main flow, packages, deterministic code versus LLM, state files. |
| [DATA_PIPELINE.md](DATA_PIPELINE.md) | Market data providers, request-weight rate limiting, candle feed and cache, data quality codes. |
| [COIN_HEADS.md](COIN_HEADS.md) | Per-coin coin heads, the specialist report schema, factor groups and the red team. |
| [CHART_ANALYSIS_V1.md](CHART_ANALYSIS_V1.md) | Display-only layer that shows on the chart why each level is drawn and whether it affected a decision. |
| [OBSIDIAN.md](OBSIDIAN.md) | Obsidian vault output: notes, canvases and the rules that keep the vault small. |
| [structures/KARAR_HARITASI_2026-09-22.md](structures/KARAR_HARITASI_2026-09-22.md) | Map of the five bots' real decision paths, read from the code before the shared structure catalog was added. |
| [structures/POLITIKA_MATRISI_v1.md](structures/POLITIKA_MATRISI_v1.md) | The shared structures policy (`structures_v1`), with thresholds fixed before coding. |

## Accounting, risk and modes

| Document | What it covers |
|---|---|
| [PAPER_ACCOUNTING.md](PAPER_ACCOUNTING.md) | The `Decimal` futures and spot paper ledgers, amount types and worked fee examples. |
| [RISK_POLICY.md](RISK_POLICY.md) | Global risk engine checks and the risk profiles (PAPER_RESEARCH, TESTNET, LIVE_LIMITED). |
| [LIVE_GRADUATION.md](LIVE_GRADUATION.md) | Manual-only mode ladder from PAPER to LIVE and its gates (live is disabled in this version). |
| [BINANCE_TESTNET.md](BINANCE_TESTNET.md) | Testnet gateways, the order state machine and reconciliation (prepared, not enabled). |
| [LLM_POLICY.md](LLM_POLICY.md) | Optional LLM layer: modes, schema validation and what it is never allowed to do. |
| [observability-leverage-telegram.md](observability-leverage-telegram.md) | Dashboard accuracy, live PnL, dynamic 2x–5x paper leverage and Telegram notifications. |

## Strategy books

| Document | What it covers |
|---|---|
| [STRATEGY_PAPER_DATA_SOURCE_V1.md](STRATEGY_PAPER_DATA_SOURCE_V1.md) | Why T2/M2 paper trades may only open and close on verified USDⓈ-M perpetual data. |
| [BOX_KARAR_B.md](BOX_KARAR_B.md) | Decision to run the Box book's structure layer in SHADOW mode. |
| [DONCHIAN_TREND_4H.md](DONCHIAN_TREND_4H.md) | The D4 4h Donchian 20/10 observation book and why it is labelled "not proven". |
| [CANDLE_VARIATIONS_4H.md](CANDLE_VARIATIONS_4H.md) | C4 / C4S candle-variation books: translation, lab record, sealing and approval procedure. |
| [PATTERN_TRADER_V1.md](PATTERN_TRADER_V1.md) | First version of the Formasyon pattern book: universe discovery, conditional plans, separate ledger. |
| [PATTERN_TRADER_V3.md](PATTERN_TRADER_V3.md) | Formasyon protocol v3: the single 4h signal that passed the signal lab's strict test. |
| [PROTECTIVE_MONITOR_V1.md](PROTECTIVE_MONITOR_V1.md) | Book-independent protective monitoring thread and the main bot's outage policy. |

## Learning, counterfactuals and shared experience

| Document | What it covers |
|---|---|
| [LEARNING_SYSTEM.md](LEARNING_SYSTEM.md) | Layers of the statistical learning system: append-only trade memory, structured postmortems, calibrated model. |
| [HISTORICAL_LEARNING.md](HISTORICAL_LEARNING.md) | Architecture of historical pattern evidence and 24/7 paper learning, with honest limitations. |
| [EDGE_LEARNING_QUALITY_V2.md](EDGE_LEARNING_QUALITY_V2.md) | Separating edge from execution, lesson lifecycle, agent weights and offline challengers. |
| [PAPER_LEARNING_LOOP_INTEGRITY_V3.md](PAPER_LEARNING_LOOP_INTEGRITY_V3.md) | Making sure every closed paper trade is learned exactly once and idempotently. |
| [EVIDENCE_REPAIRS_V1.md](EVIDENCE_REPAIRS_V1.md) | Measured segment penalties turned into soft evidence, profit protection and candidate outcome labels. |
| [ogrenme_modu/SPEC.md](ogrenme_modu/SPEC.md) *(English)* | Learning mode implementation spec: which limits may be relaxed in paper and which must stay. |
| [ogrenme_modu/CONTRACT.md](ogrenme_modu/CONTRACT.md) *(English)* | Build contract for learning mode release L1 (takes precedence over the spec). |
| [ogrenme_modu/DURUM.md](ogrenme_modu/DURUM.md) | Status log of learning mode L1: what was built, review findings and deployment notes. |
| [BOX_CF_GAP_AUDIT.md](BOX_CF_GAP_AUDIT.md) | Read-only audit script that measures where the gap between the Box book's counterfactual and real net R comes from (hypotheses H1–H10), and how to run it on the VPS. |
| [ortak_deneyim/SPEC_V1.md](ortak_deneyim/SPEC_V1.md) *(English)* | Shared experience layer, phase 1: rows, situation snapshot, store and report design. |
| [ortak_deneyim/KARARLAR.md](ortak_deneyim/KARARLAR.md) *(English, quotes the user's Turkish request)* | Binding decisions that answer the open questions of the shared experience spec. |
| [ortak_deneyim/DANISMAN_V1.md](ortak_deneyim/DANISMAN_V1.md) | Pre-registration of the shadow advisor `advisor_v1`: rule, seals and walk-forward evaluation. |

## Research and experiments

| Document | What it covers |
|---|---|
| [CROWD_LAB_FUT_V2.md](CROWD_LAB_FUT_V2.md) | Pre-registered crowd lab (taker flow, open interest, long/short ratios, funding): "follow or fade the crowd". |
| [FUTURES_OI_FUNDING_LAB.md](FUTURES_OI_FUNDING_LAB.md) | Pre-registered open-interest and funding lab (`fut_v1`) with its seal. |
| [GOLD_LAB_V1.md](GOLD_LAB_V1.md) | Pre-registered gold lab (`gold_v1`): the 20/50 EMA and order-block/FVG/sweep/CHoCH rules on PAXG/XAU, with its seal. |
| [GOLD_LAB_V1_RESULTS.md](GOLD_LAB_V1_RESULTS.md) | gold_v1 results: no cell met the +1%/month target; 0 strong candidates in 32 cells. |
| [quant_evaluation_v1.md](quant_evaluation_v1.md) | Inventory and design of the offline quant evaluation package. |
| [ENTRY_SELECTIVITY_CHALLENGER_V1.md](ENTRY_SELECTIVITY_CHALLENGER_V1.md) | Five shadow challenger families that ask "what if this family had blocked the entry?". |
| [WEEKLY_MARKET_STRUCTURE_V1.md](WEEKLY_MARKET_STRUCTURE_V1.md) | Two further shadow challenger families: weekly structure and contextual price action. |
| [MULTI_TIMEFRAME_LIQUIDITY_CONFIRMATION_V1.md](MULTI_TIMEFRAME_LIQUIDITY_CONFIRMATION_V1.md) | The H challenger family (multi-timeframe liquidity confirmation), shadow only. |
| [EXIT_GIVEBACK_AND_PROFIT_PROTECTION_V1.md](EXIT_GIVEBACK_AND_PROFIT_PROTECTION_V1.md) | Measuring how much open trades give back and comparing exit policies counterfactually (shadow). |
| [PROFITABILITY_ROOT_CAUSE_V1.md](PROFITABILITY_ROOT_CAUSE_V1.md) | Root-cause analysis of early paper losses, without changing thresholds to fit them. |
| [PROFITABILITY_EXPERIMENT_V1.md](PROFITABILITY_EXPERIMENT_V1.md) | Isolated paper contest of five frozen policies (superseded version, kept read-only). |
| [PROFITABILITY_EXPERIMENT_V1_1.md](PROFITABILITY_EXPERIMENT_V1_1.md) | Second version of the five-arm experiment with decision-time inputs (superseded). |
| [PROFITABILITY_EXPERIMENT_V1_2.md](PROFITABILITY_EXPERIMENT_V1_2.md) | Third version of the five-arm experiment with scope-matched inputs (shadow paper only). |
| [ACCEPTANCE_CRITERIA_GATE_ORDER.md](ACCEPTANCE_CRITERIA_GATE_ORDER.md) | Acceptance criteria written before deploying the fix that runs the economics gate after calibration. |

## Operations and security

| Document | What it covers |
|---|---|
| [OPERATIONS.md](OPERATIONS.md) | Health states, JSON logs, `doctor`, kill switch, mode commands and a daily/weekly routine in short form. |
| [VPS_DEPLOYMENT.md](VPS_DEPLOYMENT.md) | Deploying the paper bot to a Linux server with Docker Compose or systemd. |
| [VPS_PHASE8_PLAN.md](VPS_PHASE8_PLAN.md) | Capacity plan for whole-universe history collection and server sizing. |
| [BACKUP_RESTORE.md](BACKUP_RESTORE.md) | Backup kinds, checksum verification, retention and restore. |
| [INCIDENT_RUNBOOK.md](INCIDENT_RUNBOOK.md) | Symptom → check → action table for common incidents. |
| [PANEL_KULLANIM.md](PANEL_KULLANIM.md) | How to read the read-only dashboard (trading-terminal layout). |
| [SECURITY.md](SECURITY.md) | Secrets handling, redaction, live-trading locks and the LLM boundary. |
| [THREAT_MODEL.md](THREAT_MODEL.md) | Threats, their impact and the mitigations in place. |

## Reviews and session records (historical)

| Document | What it covers |
|---|---|
| [BASELINE_AUDIT.md](BASELINE_AUDIT.md) | Audit of the code base before the v3 work started. |
| [SESSION_HANDOFF.md](SESSION_HANDOFF.md) | Handoff notes from an early v3 development session. |
| [RESEARCH_TASK_LOG.md](RESEARCH_TASK_LOG.md) | Research and execution task log: verified findings, fixes and next steps at the time. |
| [WIP_ENTRY_SELECTIVITY_V1_HANDOFF.md](WIP_ENTRY_SELECTIVITY_V1_HANDOFF.md) | Recovery handoff of the entry-selectivity work (completed; kept as a record). |
| [WIP_MULTITIMEFRAME_H_V1_HANDOFF.md](WIP_MULTITIMEFRAME_H_V1_HANDOFF.md) | Handoff of the multi-timeframe H family at the time it was code-complete. |
| [review/CHART_FIXES_2026-09-15.md](review/CHART_FIXES_2026-09-15.md) | Closing the independent review findings of chart analysis V1. |
| [review/PAPER_DATA_SOURCE_2026-09-16.md](review/PAPER_DATA_SOURCE_2026-09-16.md) | Closing note of the T2/M2 data-source verification fix. |
| [review/PAPER_PRICE_TIME_2026-09-16.md](review/PAPER_PRICE_TIME_2026-09-16.md) | Closing note of the T2/M2 live price and time verification fix. |
| [review/PATTERN_TRADER_AND_PANEL_2026-09-16.md](review/PATTERN_TRADER_AND_PANEL_2026-09-16.md) | Closing note of Formasyon paper trader V1 and the trading-terminal dashboard. |
| [review/TRADING2_FIVE_FINDINGS_2026-09-17.md](review/TRADING2_FIVE_FINDINGS_2026-09-17.md) | Bounded fix of five review findings. |
| [review/BOX_THEORY_V15_2026-09-18.md](review/BOX_THEORY_V15_2026-09-18.md) | The Box rule, its measurement and the decision about the Box book. |
| [review/REVIEW-2026-09-21.md](review/REVIEW-2026-09-21.md) | Independent review of the Box theory branch. |
| [review/REVIEW-2026-09-22.md](review/REVIEW-2026-09-22.md) | Review of protective exit defects F1–F3 and their fixes. |
| [review/REVIEW-2026-09-23.md](review/REVIEW-2026-09-23.md) | Review of the shared pattern analysis across the bots and its link to paper decisions. |

## Other folders

- [`screenshots/`](screenshots): dashboard screenshots used in the main README (synthetic demo data).
- [`review/`](review): besides the review notes above, `evidence-*` folders and `screens/` hold the raw evidence
  (JSON, logs, images) those notes cite.
- [`ogrenme_modu/l1_asama_raporlari.json`](ogrenme_modu/l1_asama_raporlari.json): stage reports of learning mode L1.
