# Öğrenme Modu (Learning Mode): implementation spec (PAPER only)

- **Base:** `6662d5b` (2026-09-27). This is a read-only synthesis; no repo file was edited.
- **Scope:** main bot (`engine_v3`), StrategyBooks (T2, M2, Box, D4, C4, C4S), Formasyon (`pattern_trader`), and the shared risk, ledger, config and structures code.
- **User goal:** in PAPER, every bot takes as many trades as possible, so that one shared learning layer can collect R-multiple experience ("I saw this before, it won, so enter").
- **Rule for removing a limit:** a limit is removed when it only stops a valid signal from becoming a trade. A limit is kept when removing it would corrupt measurement (data identity, R geometry, double counting, impossible fills) or touch real money.

---

## 0. Key findings

1. **Every book is capped at about 3 concurrent positions, by three independent limits that all bind at that same level.** Relaxing only one of them changes nothing.
   - `TOTAL_OPEN_RISK`: 6% of equity at 2% per trade. It accounts for 2,129,578 of 2,554,298 rejections (83%) across 111 committed replay runs. In the 1-year replay it rejected T2 11,342 times and M2 16,390 times, against 16 and 18 opens.
   - Margin: 1x books and 30%-capped notionals use about 30% of equity as margin per position, so the next open fails with `INSUFFICIENT_MARGIN`.
   - Formasyon has a hard-wired count cap of 3 (`pattern_trader/book.py:71`, `enforce_position_cap=True`).
2. **The main bot's biggest current limiters are strategy gates, not capacity.**
   - The regime gate `r1` blocks 100% of SHORTs, and blocks everything while BTC is below its daily EMA200.
   - The candle veto and structures ENFORCE add further blocks.
   - These change what the strategy *is*, so they are listed as ASK_USER in §6.
3. **Measurement bug (must fix first, P0).** `decision_journal.classify_outcome` returns ACCEPTED whenever `entry["executed_notional"]` is set (`learn/decision_journal.py:89`).
   - The engine sets that field *before* `ledger.open` (`engine_v3.py:2156`).
   - So every `EXCHANGE_REJECTED` row is labelled ACCEPTED: 45 of the 52 "accepted" in the WIP handoff.
   - `entry["exec_reject"]` is written at `engine_v3.py:2192` but never persisted.
4. **Learning never sees most filtered signals.** Main writes counterfactual (shadow) trades only at 4 branches (`engine_v3.py:1934, 2011, 2118, 2148`), and two of those only when `expected_r >= 1.5`. The books and Formasyon write none at all.

### Prerequisites (ship before or with learning mode, regardless of the flag)

| id | Fix | Where |
|---|---|---|
| P0 | Classify ACCEPTED only after a successful fill. Rows with `block_code == EXCHANGE_REJECTED` become OPEN_FAILED. Persist `exec_reject` (the ledger's `last_reject_reason`) into the decision journal. | `learn/decision_journal.py:88-90`, `engine_v3.py:2155-2195` |
| P1 | Counterfactual `plan_id` must be the **signal key**, which is stable per bar (`_signal_id`, `engine_v3.py:3366-3373`), not `stable_id("plan", run_id, sym)`. The run_id changes every 15-min tour, so a veto that persists over one 4h bar would otherwise be recorded about 16 times (the ShadowBook dedupe key includes `plan_id`). | new counterfactual calls (§4) |
| P2 | Add a per-call override `open(..., allow_shrink: bool | None = None)`. The ledger attribute is **persisted** in JSON (`futures_ledger.py:950, 983`), so learning mode must never mutate it. | `accounting/futures_ledger.py:306-365` |

---

## 1. Limit inventory and decisions

Columns:
- **Cat** (category): CAP = CAPACITY, RB = RISK_BUDGET, DI = DATA_INTEGRITY, LP = LAB_PARITY, SL = STRATEGY_LOGIC, EX = EXCHANGE_RULE, SAFE = SAFETY_REAL_MONEY.
- **Decision** is what happens *in learning mode*. With the switch OFF, every row behaves exactly as it does today.
- "+CF" means the rejection writes a counterfactual record (§4).
- Where investigators disagreed, the row says how the conflict was resolved; I read the code to decide.

### A. Shared risk and capacity (all futures books)

| # | Limit | Where | Books | Cat | Decision | Why |
|---|---|---|---|---|---|---|
| A1 | TOTAL_OPEN_RISK 6% | `risk/engine.py:145-154`, `risk/profiles.py:52` | all 8 | RB | **RELAX → 100** (per-book learning profile) | This is the main slot limiter (83% of replay rejections). With 0.5% risk and slot sizing (§3) it never binds; margin becomes the limit. Do **not** use `risk_profiles.overrides`: it has no PAPER guard and moves every book at once. Do **not** edit the `PAPER_RESEARCH` constant, which about 15 tests pin. |
| A2 | risk_per_trade 2% | `profiles.py:52`, `strategy_paper.py:421`, `coinhead/head.py:219` | all | RB | **RELAX → 0.5% target**. 2% stays as the hard cap (used by A7). | R is scale-free. A smaller fixed risk lets 20–49 positions fit in margin (§3). |
| A3 | INSUFFICIENT_MARGIN (reject, no shrink) | `accounting/futures_ledger.py:346-365` | all | RB | **RELAX**: per-call `allow_shrink=True` as a backstop only (P2). Slot sizing is meant to keep margin from binding in the first place. | Shrinking keeps R valid. Shrink-as-primary would size trades by arrival (universe) order, which is a selection bias. Record `meta.size_rule=SHRUNK_TO_MARGIN`. |
| A4 | MAX_POSITION_PCT rejects instead of scaling (30% × lev) | `risk/engine.py:189-191`, `strategy_paper.py:437-444` | T2, M2, Box (main pre-capped; pattern classic) | RB | **RELAX**: replaced by the slot scale-to-fit cap (§3); size-related rejects no longer happen. | Box: 143/143 signals rejected; 887 across the replay runs. The slot cap is always tighter than 30%·L when K ≥ 4. |
| A5 | Book sizing on MTM equity vs the risk engine's starting-equity basis | `strategy_paper.py:421` vs `risk/engine.py:91-98` | books | RB | **RELAX**: learning sizing uses `risk.equity_basis(state)`. | Otherwise size depends on the P&L path; R is unaffected. |
| A6 | Leverage fixed or need-based (D4/C4/C4S 1x forced by validator; pattern v3 1x; T2/M2 2→4; Box 3→4) | `candle_book.py:103-106`, `pattern_trader/strategy_v3.py:127`, `strategy_paper.py:424-436` | books, Formasyon | RB | **RELAX**: learning `leverage_max` per book (§2), always bounded so that liquidation distance ≥ 2 × stop distance. | Leverage changes only margin, not risk. 1x is the reason 1x books hold only 3 positions. Rule validators are **not** changed: learning sets the leverage cap at the `apply_action` level. |
| A7 | MIN_ORDER_CONFLICT: "risk is never raised to reach minimum" | `risk/engine.py:220-222`, `:59-81` | all | EX (policy part) | **RELAX**: bump notional up to `min_notional`, with qty rounded **up** to step, only if `min_notional × stop_frac ≤ 2% × E` and margin is available. Otherwise reject **+CF**. | 290,028 replay rejections. The exchange rule itself is untouched; only "never exceed the tiny learning size" is relaxed, and it stays within the 2% cap. |
| A8 | Ledger exchange filters (MIN_NOTIONAL, MIN_QTY, STEP_ZERO_QTY, MAX_QTY, LEVERAGE_TOO_HIGH) | `futures_ledger.py:318-371` | all | EX | **KEEP +CF** | Removing them would record fills the exchange refuses. |
| A9 | Position-count caps (profile / ledger / `futures.max_positions: 3`) | `risk/engine.py:125-128`, `profiles.py:62-77`, `config.yaml:107` | main, books | CAP | **KEEP** (already off in PAPER_RESEARCH; the learning profile keeps `None`) | They must stay on for TESTNET/LIVE profiles. |
| A10 | Kill switch (one shared instance) | `risk/killswitch.py`, `risk/engine.py:120`, `engine_v3.py:153, 348, 378` | all | SAFE | **KEEP** | Operator halt. In PAPER it never trips automatically. |
| A11 | Daily/weekly/drawdown kill, consecutive-loss and symbol cooldowns, cluster and altcoin caps, margin-utilisation, liq-buffer, spread and min-expected-R profile gates | `risk/engine.py:162-219`, `profiles.py:52-56` | all | SAFE / RB | **KEEP** | Already `None`/0 in PAPER_RESEARCH, so they block nothing now; they stay intact for live profiles. |
| A12 | `build_state` counts profit-locked stops as open risk (abs) | `risk/state.py:201` | all | RB | **KEEP** (moot once A1 = 100) | — |
| A13 | OPPOSITE_EXPOSURE_CONFLICT (spot long vs futures short) | `risk/engine.py:130-132` | main | RB | **KEEP +CF**. Conflict resolved: the shared investigator said RELAX. | A hedged pair makes per-trade attribution ambiguous, and it is rare. |
| A14 | SPOT_ALLOCATION 30% | `risk/engine.py:171-188` | main (spot) | CAP | **RELAX → 100** in the main learning profile. Keep SPOT_NO_SHORT and the unknown-price fail-closed branch. | Spot cash stays the real limit. |
| A15 | One position per symbol per book | `futures_ledger.py:275-277`, `risk/engine.py:129`, `coinhead/head.py:276-287`, `pattern_trader/book.py:777` | all | CAP / LP | **KEEP +CF** | The ledger is one-way and keyed by symbol; the labs also use one position per symbol. |
| A16 | `resolve_profile` / `validate_v3` bounds | `profiles.py:80-102`, `config_v3.py:1116` | all | SAFE | **KEEP**, and add the learning guards in §2. | — |
| A17 | LIVE/testnet guards (modes, gateway, `leverage.paper_only`) | `risk/modes.py`, `config_v3.py:808-816, 1113`, `risk/leverage.py:41-49` | all | SAFE | **KEEP** | — |

### B. Main bot (`engine_v3._execute_locked`)

Order in which a candidate is checked: coin head → chief → universe → perp identity → trigger → candle → chart → regime → structures → economics → duplicate → research policy → size multipliers → precision → leverage → RiskEngine → ledger.

| # | Limit | Where | Cat | Decision | Why |
|---|---|---|---|---|---|
| B1 | Leverage base gates → NO_TRADE (LEVERAGE_GATE_BLOCKED) | `risk/leverage.py:147-172`, `engine_v3.py:2108-2124` | RB | **RELAX partially**: fall back to `min_leverage = 2` with reason `LEARNING_MIN_LEVERAGE_FALLBACK` when the base failures are a subset of {STOP_TOO_FAR_FOR_LEVERAGE, STOP_TOO_TIGHT_FOR_LEVERAGE (only if stop ≥ 0.1 ATR), DEPTH_BELOW_BASE}. **KEEP** NO_TRADE for DATA_STALE, DATA_CONFLICT, STOP_UNKNOWN, CONFIDENCE_UNKNOWN, LIQ_BUFFER_TOO_THIN_FOR_BASE and SPREAD_ABOVE_BASE. CONFIDENCE_BELOW_BASE → **ASK_USER** (§6, S7). | 276/20,000 LEVERAGE_BLOCKED. Conflict resolved: the shared investigator proposed a 1x fallback, but config forbids new 1x futures entries (`min_leverage < 2` raises ConfigError). SPREAD_ABOVE_BASE is kept because the paper cost model uses a fixed 3 bps, so a wide spread makes paper R optimistic. This matches Formasyon's SPREAD_WIDE. |
| B2 | Leverage tiers read `open_risk_frac` from the risk budget | `engine_v3.py:2369-2390`, `leverage.py:196, 207` | RB | **KEEP the base budget**: compute against PAPER_RESEARCH 6%, not the learning 100%. | Otherwise 4x/5x become easier as the budget grows and liquidation moves closer. |
| B3 | Size multipliers: opportunity 0.2–1, chief soft 0.25–1, research size | `engine_v3.py:2062-2079`, `coinhead/chief.py:168-181` | RB | **RELAX → record-only** (logged in features). Notional comes from learning sizing (§3). | They only shrink USDT and push candidates under min_notional. R is unaffected. |
| B4 | Coin-head notional cap 30% × 1x | `coinhead/head.py:214-229` | RB | **KEEP** | At E = 100, slot sizing is tighter anyway. Revisit only if S14 raises main equity. |
| B5 | Coin-head `NO_TRADE_MIN_ORDER_CONFLICT` and red-team MIN_ORDER_CONFLICT | `risk/engine.py:59-81`, `coinhead/redteam.py:147-151` | EX | **RELAX with the A7 rule** inside `size_position` when `learning_min_notional_bump` is passed. | Otherwise the plan is vetoed before the engine-level bump ever runs. |
| B6 | Fixed 40-symbol entry universe, scanner off | `config.yaml:96-97, 271-317`, `entry_universe.py` | CAP | **ASK_USER** (S10), hard cap 60 symbols (§5) | — |
| B7 | Perp-frame provenance is all-or-nothing over the union of every book's timeframes (a missing 5m or 1h blocks main/T2/M2/D4/C4 entries) | `engine_v3.py:1433, 1457, 1466-1472`, `strategy_paper.py:282` | DI (scope bug) | **RELAX**: store `prov["frames_ok"] = {tf: bool}`. Each consumer requires `entry_ok` only for its own timeframes (main: 1d/4h/1h; book: `rule.timeframes`). Keep FRAME_MISMATCH per timeframe. | Removes cross-book coupling without ever letting a SPOT frame feed a perp rule. |
| B8 | DUPLICATE_SIGNAL / ALREADY_FIRED_THIS_BAR | `engine_v3.py:2026-2030`, `entry_trigger.py:40` | LP | **KEEP** | Prevents double-counting one observation. |
| B9 | Research policy ACTIVE veto | `engine_v3.py:2031-2060` | SL | **KEEP**. Conflict resolved: the main investigator said ASK_USER, the gates investigator said KEEP. | It is the learning layer itself saying "this lost before", and it already writes a paired counterfactual. |
| B10 | Red-team hard vetoes / DATA_INVALID / no direction (\|score\| < 0.05) | `coinhead/redteam.py:29-32`, `coinhead/head.py:258-292` | DI | **KEEP**. COSTS_EXCEED_EDGE: KEEP **+CF**. | Stale data, liquidation before the stop, untradeable books. |
| B11 | Perp identity and UNRESOLVED_PRECISION | `engine_v3.py:1456-1473, 2086-2092` | DI | **KEEP** | TRX 2.00R → 1.28R with default ticks. |
| B12 | Cycle halts (RISK_STATE_PERSIST_FAILED, SHUTDOWN, GAP) | `engine_v3.py:1881-1889` | DI | **KEEP** (GAP is dead code) | — |
| B13 | Shadow only when `expected_r ≥ 1.5`; no shadow at all for NEGATIVE_NET_EDGE and the candle/regime/structure vetoes | `engine_v3.py:1934, 2021, 2148` | — | **RELAX**: record every valid-geometry block (§4) | — |
| B14 | Legacy caps (`risk.max_open_positions 3`, `futures.min_pwin`, `min_confidence_to_buy`, `btc_regime_filter`, `leverage_max_paper_research`) | `config.yaml:85-87, 108, 200` | CAP | **KEEP** (not read by the v3 engine) | Update the chief brief text "en fazla 3 pozisyon; toplam risk ≤ %6" (§7). |

### C. StrategyBooks (`strategy_paper.py`, `paper_rules.py`, `box_timer.py`)

| # | Limit | Where | Books | Cat | Decision | Why |
|---|---|---|---|---|---|---|
| C1 | Box `min_stop_pct: 2.22` (our sizing workaround, "videoda YOKTUR") | `config.yaml:452`, `box_theory.py:63-66, 342` | Box | RB | **RELAX → 0.32%** in learning (= 2 × the 0.16% round-trip cost). Going to 0 is ASK_USER (S16). | It silently drops 79–92% of Box signals. It only existed because of A4, which is gone. Below 0.32%, cost exceeds half the risk (DENEY_V15: net −0.21 to −0.46R at 0). |
| C2 | RISK_OUTSIDE_TESTED_RANGE (lab ATR bounds) | `strategy_paper.py:403-412` | D4, C4, C4S | LP | **KEEP both bounds +CF**. Conflict resolved: the books investigator proposed relaxing the upper bound. | The task defines lab ATR bounds as parity. The counterfactual keeps the information. |
| C3 | one_entry_per_signal, Box same-bar dedupe | `paper_rules.py:304-321`, `box_timer.py:154-166` | D4, C4, C4S, Box | LP | **KEEP** | Double counting. |
| C4 | 60-min entry window / Box same-day rule | `donchian_trend.py:151`, `candle_book.py:257`, `box_theory.py:319` | D4, C4, C4S, Box | LP | **KEEP +CF** (when the expiry is observed) | Keep the cycle ≤ 50 min (§5). |
| C5 | ENTRY_DRIFT (0 = off) | `strategy_paper.py:383-401` | all | LP | **KEEP 0** | — |
| C6 | STRATEGY_BAD_STOP / BAD_STOP | `strategy_paper.py:378-382`, `futures_ledger.py:367-370` | all | DI | **KEEP** | R is undefined. |
| C7 | Data verdict chain (provenance, market, frames, freshness, perp mark ≤ 180 s) | `strategy_paper.py:152-293, 758-791` | all | DI | **KEEP** | — |
| C8 | BTC 1d reference frame for T2/M2 | `strategy_paper.py:284-291` | T2, M2 | DI | **KEEP** | — |
| C9 | D4/C4/C4S fixed 23-coin list | `config.yaml:480, 496, 521` | D4, C4, C4S | CAP | **RELAX → entry universe (40)** in learning. Tag `in_lab_universe`. | Frames are already fetched every tour, so API cost is zero. |
| C10 | C4: one slot per symbol, only `hits[0]` trades | `candle_book.py:252-270` | C4 | CAP | **RELAX via +CF**: one counterfactual per `also_matched` variation and per variation blocked by an open position. Splitting into per-variation books is **ASK_USER** (S12). | Every variation builds experience at almost no cost. |
| C11 | C4S: empty variations plus strict gate means 0 trades | `config.yaml:527`, `candle_variations.py:506-510` | C4S | SL | **ASK_USER** (S11) | — |
| C12 | C4 variation approval gate | `candle_variations.py:463-513` | C4 | SL | **KEEP** | Identity and user approval. |
| C13 | Rule bodies: T2/M2 (LONG, EMA200 / TSMOM28, 210 bars), Box (box, edge, trigger, EOD), D4 (fresh 20-bar breakout, LONG) | `ema200_trend.py`, `box_theory.py`, `donchian_trend.py` | T2, M2, Box, D4 | SL | **KEEP** | The strategy itself. |
| C14 | T2/M2 BTC regime-UP condition inside the rule | `ema200_trend.py:256-260` | T2, M2 | SL | **ASK_USER** (S6) | — |
| C15 | Structures ENFORCE for T2/M2 (WAIT, WAIT_TRIGGER, CANCEL, M2 structure exits) | `config.yaml:538-539`, `structures/bots.py:160-194` | T2, M2 | SL / LP | **ASK_USER** (S4) | — |
| C16 | Box structures SHADOW; D4/C4 skip structures | `config.yaml:542`, `paper_rules.py:283` | — | — | **KEEP** | Already non-blocking. |
| C17 | Book step runs late inside the main tour | `engine_v3.py:1672` | T2, M2, D4, C4, C4S | CAP | **KEEP**. This is the hard constraint in §5; an own timer is optional (S13). | — |
| C18 | Box timer: each 5m bar evaluated once; 15-min freshness | `box_timer.py:106-113` | Box | DI | **KEEP**; monitor `missed_bars` | — |

### D. Formasyon (`pattern_trader/*`, protocol `momentum_4h_v3`)

| # | Limit | Where | Cat | Decision | Why |
|---|---|---|---|---|---|
| D1 | Hard count cap 3 (`enforce_position_cap=True`) | `pattern_trader/book.py:70-72`, `config.yaml:549` | CAP | **REMOVE in learning**: build the ledger with `enforce_position_cap=False` when learning is enabled at startup, and add a book-level check `if not lm_active and n_open >= max_open_positions → R_MAX_POSITIONS` so that a runtime suspension falls back to baseline. | This book ignores the profile contract (`enforces_position_cap`). Making that unconditional is a separate bug-fix decision. |
| D2 | 1x leverage with a 30% notional cap → about 30 USDT margin each → INSUFFICIENT_MARGIN on the 4th | `strategy_v3.py:127`, `book.py:1003-1007` | RB | **RELAX**: slot sizing with `leverage_max 3` (§3) | — |
| D3 | TOTAL_OPEN_RISK / 2% | `book.py:88, 1015` | RB | **RELAX** through A1/A2 (per-book learning profile) | — |
| D4 | `starting_equity_usdt` limited to (0, 1000] | `config_v3.py:1053` | RB | **KEEP**; raise to 10,000 only if S14 is chosen | — |
| D5 | COOLDOWN_AFTER_LOSS (8 × 15m) | `book.py:344-346, 779-780`, `config.yaml:561` | RB | **REMOVE in learning** (do not set `cooldown_until`) | The lab had no cooldown, and there is no martingale logic. |
| D6 | RISK_ABOVE_CAP_AFTER_ROUNDING (2% tolerance) | `book.py:47-50, 986-998` | RB | **RELAX**: scale the notional by `1/ratio` instead of rejecting. Conflict resolved: scaling rather than skipping keeps risk within budget. | It rejects every stop tighter than about 1.48% (checked with the ledger's own fill price at 3 bps). |
| D7 | v3 `min_rr_after_cost 1.0` | `strategy_v3.py:126`, `book.py:964-967, 994-995` | LP | **REMOVE in learning** | The lab had no R/R floor, so removing it restores parity. Cost stays inside R. |
| D8 | POSITION_OPEN on the same symbol | `book.py:777-778, 889-894` | CAP | **KEEP +CF** | — |
| D9 | 30-coin protocol universe | `book.py:99-103` | SL | **ASK_USER** (S9) | — |
| D10 | Protocol v3 only (classic families off) | `config.yaml:554` | SL | **ASK_USER** (S9) | — |
| D11 | v3 signal: 3WS LONG with RSI14 > 70 | `strategy_v3.py:85-99` | SL | **KEEP** | — |
| D12 | Fresh confirmation plus 60-min window | `strategy_v3.py:40, 88-90`, `book.py:882-887` | LP | **KEEP +CF** | — |
| D13 | PATTERN_ALREADY_USED / SAME_BREAK | `strategy_v3.py:91-93` | LP | **KEEP** | — |
| D14 | CHASE_LIMIT 1 ATR; risk range 0.1–5 ATR; 96h time stop | `book.py:921-942`, `strategy_v3.py:43` | LP | **KEEP +CF** (chase and range) | — |
| D15 | Geometry sanity, data identity/freshness, 2h filters freshness, SPREAD_WIDE 0.15% | `book.py:897-933, 955, 819-862` | DI / EX | **KEEP** | — |
| D16 | LIQUIDITY_UNKNOWN / DEPTH_UNKNOWN is terminal on a single snapshot failure | `book.py:947-959` | DI | **RELAX**: WAIT and retry inside the 60-min window. If still unknown at expiry: **no trade, +CF**. Conflict resolved: the pattern investigator proposed opening with a tag. | A spread that was never measured cannot vouch for fill realism. |
| D17 | THIN_DEPTH 20,000 | `config.yaml:564` | DI | **RELAX → depth ≥ max(2,000, 20 × notional)** | Positions are now about 10 USDT. |
| D18 | VOLUME_UNKNOWN blackout when ticker24h fails | `pattern_trader/universe.py:87-90, 116-128` | DI | **RELAX**: carry forward the previous volume with `volume_carried=True` | Entry-time liquidity is re-measured anyway. |
| D19 | Discovery LOW_VOLUME / WIDE_SPREAD; scan budget 40 | `universe.py:127-132`, `scheduler.py` | CAP | **KEEP** (only matters under S9) | — |
| D20 | Classic MAX_POSITION_PCT reject; classic plan filters; structures ENFORCE (inert under v3) | `book.py:1006, 504-506` | RB / SL | Sizing: **RELAX** (slot sizing covers all protocols). Classic filters and structures: **KEEP**. | — |

### E. Strategy-logic items (details in §6)

S1 regime gate · S2 candle veto · S3 main structures · S4 T2/M2 structures · S5 economics gate and soft penalties · S6 T2/M2 regime inside the rule · S7 confidence thresholds · S8 NO_TRIGGER · S9 Formasyon universe/protocol · S10 entry universe · S11 C4S · S12 C4 split · S13 own 4h timer · S14 larger virtual equity · S15 learning influence · S16 Box min_stop 0 · S17 main structure stop-tightening.

---

## 2. Mechanism: one PAPER-only switch

### 2.1 Config (`config.yaml`, new top-level section; `config_v3.LearningModeSection`)

```yaml
learning_mode:                     # ÖĞRENME MODU — yalnız PAPER (aksi ConfigError)
  enabled: true                    # kod varsayılanı false; kapatmak: false + restart
  risk_per_trade_pct: 0.5          # öğrenme hedef riski; 2.0 profil tavanı AYNEN kalır (A7 için)
  max_total_open_risk_pct: 100     # A1
  margin_reserve_pct: 5            # Σ margin ≤ %95 equity
  liq_buffer_mult: 2.0             # liq mesafesi ≥ 2 × stop mesafesi (kaldıraç bununla kısılır)
  min_notional_bump: true          # A7
  counterfactual: true             # §4
  counterfactual_max_pending: 2000 # defter başına
  books:
    main:                         {enabled: true, slots: 20}
    t2_trend_regime:              {enabled: true, slots: 40, leverage_max: 4}
    m2_tsmom28:                   {enabled: true, slots: 40, leverage_max: 4}
    b1_box_fade:                  {enabled: true, slots: 20, leverage_max: 4, min_stop_pct: 0.32}
    d4_donchian_20_10:            {enabled: true, slots: 20, leverage_max: 3, symbols: universe}
    c4_candle_variations:         {enabled: true, slots: 20, leverage_max: 3, symbols: universe}
    c4s_candle_variations_strict: {enabled: false}
    pattern_trader:               {enabled: true, slots: 30, leverage_max: 3}
  strategy_overrides: {}           # §6 kullanıcı cevapları; yalnız öğrenme aktifken uygulanır
```

**Validation (`validate_v3`).** When `enabled` is true, raise **ConfigError** unless all of the following hold:
- `mode.mode == PAPER`
- `execution.gateway == paper`
- `execution.testnet_enabled == false`
- `risk_profiles.profile == PAPER_RESEARCH`
- every book name exists

Bounds, also ConfigError when violated:
- `0 < risk_per_trade_pct ≤ 2`
- `0 < max_total_open_risk_pct ≤ 100`
- `1 ≤ slots ≤ 200`
- `1 ≤ leverage_max ≤ min(5, profile.futures_max_leverage)`
- `liq_buffer_mult ≥ 1.5`
- `min_stop_pct ≥ 0`
- `strategy_overrides` keys limited to the S-list whitelist

Unknown keys under `learning_mode` are a **ConfigError**, not the loader's warning (`config_v3.py:20-26` silently drops them, and a typo here must not pass unnoticed).

Why a separate check is needed at all: `pattern_trader` and `strategy_paper` are also allowed in TESTNET/OBSERVE/SHADOW_LIVE (`config_v3.py:1046`, `strategy_paper.py:1321`). The config comment "PAPER dışında ConfigError" at `config.yaml:546` is wrong.

**Runtime gate.** `LearningMode.active() -> (bool, reason)` returns true when `enabled` is set and `research_coordinator.mode_gate(mode_state.mode.value, gateway, mode_state.is_live_order_path_enabled())` is OK. This is the same absolute gate `_research_mode_ok` uses (`engine_v3.py:3375`).
- It is evaluated once per main tour, once per Box timer pass and once per Formasyon cycle, and the result is cached for that pass.
- If `enabled` is set but the gate fails (for example after a runtime mode transition), the state is **`LEARNING_MODE_SUSPENDED:<reason>`** in health and the dashboard, and **every** reader below takes the baseline path.
- Strategy overrides are never written into `cfg`. They are read through `lm.effective(key, base_value)`, so suspension reverts them too.

### 2.2 Code paths that read it (exhaustive)

1. **`tradingbot/learning_mode.py` (new, pure):**
   - `active`
   - `book(name)`
   - `profile_for(base, book)`: `dataclasses.replace(base, risk_per_trade_pct=…, max_total_open_risk_pct=…, max_spot_allocation_pct=100 (main only))`. The result is **never** added to `PROFILES`.
   - `fit_size(E, s, L_max, slots, min_notional, step, avail_margin, mmr)`, see §3.
   - `leverage_fallback(base_failures)`
   - `counterfactual_ok(reason)`
   - `effective(key, base)`
2. **`config_v3.py`:** the section dataclass plus the validation in 2.1.
3. **`engine_v3.py`:**
   - `__init__`: build `self.lm` and `self.risk_learning = RiskEngine(profile_for(...), self.killswitch, clusters)`, sharing the same kill switch.
   - Start of each tour: `self._lm_on = lm.active()`.
   - In `_execute_locked`:
     - step 6: multipliers become record-only (B3);
     - step 6b: leverage fallback (B1);
     - step 7: `risk_learning.evaluate` with ctx `learning_min_notional_bump`;
     - step 8: `ledger2.open(..., allow_shrink=True)`;
     - counterfactual calls (§4).
   - `_leverage_context` keeps the base budget (B2).
   - Perp provenance becomes per-timeframe (B7).
   - `_strategy_paper_tour` passes `lm_on` plus the per-book learning spec, and swaps the symbol list (C9).
   - The candle/regime/structures mode readers go through `lm.effective` (only if §6 answers set overrides).
4. **`risk/engine.py`:**
   - `size_position` and `evaluate` honour `ctx["learning_min_notional_bump"]` (A7, B5).
   - They are never called with it except by (3) and (5), and only when `_lm_on`.
5. **`strategy_paper.py`:**
   - `StrategyBook.__init__` builds the learning RiskEngine and a `ShadowBook(state_dir/"shadow_trades.json")`.
   - `step()` gets a new kw `learning` (a `BookLearning | None`).
   - `apply_action(..., learning=None)`: with `None` it is bit-identical to today. When set, it uses `fit_size` instead of `risk_per_trade × equity / s` plus the leverage ramp and cap, and passes `allow_shrink`.
   - After a REJECTED result, `step()` writes the counterfactual (it has the `act` geometry, which `_reject` does not).
   - `HistoricalReplay` passes `learning=None` unless the replay is run with `--learning-mode`. The flag is recorded in the replay meta.
6. **`box_timer.py`:** rule params are built twice at book init (base and learning `min_stop_pct`); the timer picks one per pass by `lm.active()`.
7. **`pattern_trader/book.py`, `strategy_v3.py`, `universe.py`:** ledger cap (D1), cooldown (D5), rounding (D6), min R/R (D7), liquidity retry (D16), depth (D17), sizing via `apply_action(learning=…)` (D2/D3), volume carry (D18), counterfactual (§4).
8. **`learn/decision_journal.py`, dashboard, obsidian:** read-only labels (§7).

### 2.3 What stays untouched (LIVE/testnet guarantee)

- The `PROFILES` constants, `risk_profiles.profile`, `risk_profiles.overrides` and the kill switch are not modified.
- Live execution gateways (`execution/*` binance testnet) never receive a learning object. Learning objects are only passed to paper ledgers.
- Three independent layers protect this:
  - (a) the config ConfigError;
  - (b) the runtime `mode_gate`;
  - (c) every learning branch is written as `if learning is not None and learning.on:`, and `learning` is constructed only by `LearningMode` after (a) and (b) pass.
- `leverage.paper_only` and the structures, candle and regime validators (ENFORCE forbidden in LIVE) keep working as before.

### 2.4 Turning it off

Set `learning_mode.enabled: false` and restart the worker.
- New entries follow the baseline immediately.
- Positions opened under learning mode keep their stored stop, targets and leverage, and normal exit management handles them. There are no forced closes.
- Because open risk will usually exceed 6%, `TOTAL_OPEN_RISK` then blocks new entries until enough of them close. This is expected and must be documented on the dashboard.
- No state migration is needed: `allow_shrink` is not persisted and the ledger schema is unchanged.

---

## 3. Sizing under many concurrent positions

**Rule ("slot scale-to-fit"), applied per book when learning is on.** R-multiples come from the entry, the initial stop and fees, so they are scale-free and none of this changes them.

```
E       = risk.equity_basis(state)            (starting equity in PAPER_RESEARCH)
s       = |entry - stop| / entry
m_slot  = (1 - reserve) * E / K                reserve = 5%, K = slots
L       = min(L_max_book, ceil(notional_risk / m_slot), floor(1 / (liq_buffer_mult*s + mmr)))
notional_risk = r_L * E / s                    r_L = 0.5%
notional = min(notional_risk, m_slot * L)       -> never rejected for size (replaces MAX_POSITION_PCT)
if notional < min_notional: bump to min_notional (qty rounded UP to step) iff min_notional*s <= 2%*E and margin free;
                            else reject + counterfactual
ledger.open(allow_shrink=True)                   backstop only; meta.size_rule records SLOT / BUMP / SHRUNK
```

Invariants:
- Per-trade USDT risk ≤ min(r_L·E, the 2% cap).
- Σ margin ≤ 95% of E. Isolated margin bounds each position's loss, so even a simultaneous gap through every stop cannot take the book's equity below about 0.
- Liquidation distance (≈ 1/L − mmr) is at least 2 × the stop distance: L2 ≈ 49.5%, L3 ≈ 32.9%, L4 ≈ 24.5%, L5 ≈ 19.5%.
- `risk_fraction_of_budget` is recorded on every trade.

**Why fixed small risk plus slots, rather than 2% with shrink.** With allow_shrink alone, the first few signals of a tour take full size and later ones get the leftover, so size ends up depending on universe order (BTC/ETH first). Equal slots treat every signal the same, and USDT P&L stays roughly proportional to ΣR.

### Worked numbers

Config and assumptions:
- Books: E = 200. Formasyon and main: E = 100.
- min_notional: 5 for most alts; **BTC 50** (measured, `replay/engine.py:203`). The pattern investigator's figure of 100 is unverified.
- Round-trip cost is about 0.16%.
- "Today" means the PAPER_RESEARCH defaults: 2% risk, 6% total open risk, max_position_pct 30.

| Book | stop s | Today: size / binding cap | Learning: r = 0.5%, K, L | Learning notional / margin / risk | Positions that fit |
|---|---|---|---|---|---|
| M2 | ~13% | notional 30.8 at L2, margin 15.4. **TOTAL_OPEN_RISK binds at 3** (12/4 USDT); margin would bind at 12. | K40, L2 (liq cap floor(1/0.265) = 3) | 7.7 / 3.85 / 1.00 USDT | **49** |
| T2 | ~10–15% (3 × ATR14 on 1d) | same pattern as M2, **3** | K40, L2 | ≈ 9.5 / 4.75 / 0.95 | **40** |
| Box | 2.8% (current floor) | notional 142.9 at L3, margin 47.6. **3** by risk, 4 by margin. | K20, L4 (need) | 35.7 / 8.9 / 1.00 | **21** |
| Box at C1 floor | 0.5% | would be MAX_POSITION_PCT / min_stop drop | K20, L4 | 38 (capped) / 9.5 / 0.19 (0.095%) | 20 |
| C4 / D4 | ~3% (1–3 ATR on 4h) | 133 → scaled to 60 at 1x, margin 60. **Margin binds at 3.** | K20, L3 (liq 32.9% ≥ 6%) | 28.5 / 9.5 / 0.86 | **20** |
| C4 tight | 1.5% | same, 3 | K20, L3 | 28.5 / 9.5 / 0.43 | 20 |
| Formasyon | ~4% (4h 3WS) | 30 (30% × 1x), margin 30. **Count cap and margin bind at 3.** | K30, L3 | 9.5 / 3.17 / 0.38 | **30** |
| Main | ~3% (1–4 ATR on 4h), L2–5 by tiers | notional ≤ 30 (head cap), risk ≈ 0.9. **TOTAL_OPEN_RISK binds at ≈ 6**; margin at L2 also ≈ 6. | K20, L = tier choice | L2: 9.5 / 4.75 / 0.29. L5: 16.7 / 3.3 / 0.5. | **20** |

Min-notional consequences:
- **BTC in M2/T2** (s = 13%): the bump needs 50 × 0.13 = 6.5 USDT of risk, which is more than the 2% cap of 4 USDT at E = 200, so it is rejected and recorded as a counterfactual. At E = 1,000 the bump succeeds (risk 0.65%).
- **Formasyon BTC:** the bump needs 50 × 0.04 = 2.0 USDT, which exactly equals the 2% cap of 2.0 at E = 100, so it succeeds when margin is free.
- **Main at E = 100:** BTC always fails (min_notional 50 against a head cap of 30), so it goes to a counterfactual.
- Making BTC and ETH tradable across the board needs larger virtual equity in fresh state dirs (S14, ASK_USER). USDT scale does not affect R.

Other effects:
- Per-coin cap: in learning, the slot cap (m_slot·L) replaces `max_position_pct`. It is always tighter when K ≥ 4, and it **scales**, never rejects.
- Worst-case simultaneous stop-out: M2 at 49 positions × 1 USDT = 24.5% of E. D4/C4 at 20 × 0.86 = 8.6% of E. A single isolated position can never lose more than its margin plus fees.
- Funding: more positions accrue more funding (`realized_funding_source`). That is a real cost, correctly included in R.

---

## 4. Counterfactual record for signals that are still not taken

The design reuses `learn/shadow.py` `ShadowBook` / `ShadowTrade` (`is_counterfactual=True`, stop-first conservative labelling, `MAX_TRADES = 5000` plus an archive).

**When to record.** All of these must hold:
- learning is active and `counterfactual: true`;
- the signal's geometry is valid (entry > 0, stop on the correct side, s > 0);
- the data verdict for the rule's decision timeframes is OK;
- the reject reason is in `COUNTERFACTUAL_OK`;
- the signal key has not been recorded already.

`COUNTERFACTUAL_OK`:
- Capacity and exchange: TOTAL_OPEN_RISK, INSUFFICIENT_MARGIN, MIN_NOTIONAL, MIN_QTY, STEP_ZERO_QTY, MAX_QTY, LEVERAGE_TOO_HIGH, MIN_ORDER_CONFLICT.
- Occupancy: ALREADY_OPEN(_SAME_SYMBOL), POSITION_OPEN, OPPOSITE_EXPOSURE_CONFLICT.
- Parity: RISK_OUTSIDE_TESTED_RANGE, CHASE_LIMIT, EXPIRED_BEFORE_ENTRY, ENTRY_DRIFT.
- Liquidity: LIQUIDITY_UNKNOWN at expiry.
- Main: NEGATIVE_NET_EDGE, RESEARCH_SIZE_ONLY, CANDLE_VETO, REGIME_VETO, STRUCTURE:* (whenever those gates stay ENFORCE); LEVERAGE_GATE_BLOCKED for non-data reasons; CHIEF/RISK blocks (the `expected_r ≥ 1.5` condition is dropped in learning); COSTS_EXCEED_EDGE; KILL_SWITCH_ACTIVE.
- C4 `also_matched` variations, and T2/M2 STRUCTURE_* (if ENFORCE).

**Never recorded:**
- DATA_* / DATA_INVALID / UNRESOLVED_PRECISION, where the data or geometry itself is untrusted.
- STRATEGY_BAD_STOP / BAD_STOP, where R is undefined.
- DUPLICATE_SIGNAL / SIGNAL_ALREADY_USED / PATTERN_ALREADY_USED, which would be the same observation twice.
- NO_TRIGGER, because the signal has not fired yet and would be re-evaluated every tour.

**Record fields.** The existing ShadowTrade fields, plus:
- `book`, `signal_key` (used as `plan_id`, see P1), `variation` (C4), `rule_version`;
- `reason_not_opened`;
- `learning_unlocked=false`;
- `features`: the same dict a real trade would carry, including the regime, candle and structure verdicts;
- `tf_minutes`: T2/M2 1440, D4/C4/Formasyon 240, Box 5;
- `label_kind`.

Only the `as_planned` variant is used, to keep cost low.

**Labelling.** The labeller runs once per pass using frames already in memory (`runner.last_frames`, the Box timer frames, the Formasyon candle cache), so it makes **no extra API calls**. `label_kind` by book:

| Books | label_kind | How |
|---|---|---|
| Formasyon, C4, Box, main | `TARGET_STOP_TIME` | Exact: the exits are pure bar functions (target / stop / time stop / EOD). |
| D4, T2, M2 | `RULE_EXIT` | Re-run the rule's pure exit predicate on closed bars (D4 10-bar low; T2/M2 state flip). |
| any | `HORIZON` | Fallback: 30 bars, R at the horizon close plus MFE/MAE, flagged APPROX. |

**Budget and consumption:**
- At most 2,000 pending records per book. Beyond that, the oldest pending record is dropped and counted in `counterfactual_dropped`.
- About 1 KB per record; labelling costs are in milliseconds per pass.
- Counterfactuals never touch a ledger, equity or the P&L scorecards.
- The learner reads them with `is_counterfactual=True` and weights or evaluates them separately from filled trades. Main's existing research pairing (`research.add_pending`) is unchanged.

---

## 5. Load budget (VPS: 7.6 GiB RAM, 4 CPUs; worker RSS ≈ 3.4G, peak ≈ 4.5G)

**Memory limit discrepancy.** The release scripts state a 6G limit (`deploy/releases/tb-deploy-7ad8832.sh:440`), but the repo unit file still says `MemoryMax=4G` and `CPUQuota=150%` (`deploy/tradingbot-worker.service:51-52`). The deploy step must verify which one is live (`systemctl show -p MemoryMax`). At 4G, today's 4.5G peak would already be an OOM risk (see the OOM on 2026-09-09 in `RESEARCH_TASK_LOG.md:43`).

**What drives load today:**
- Tour ≈ 1,414 s for 41 symbols (≈ 34.5 s per symbol, dominated by agent analysis and pattern-index queries), plus a 900 s wait, for a cycle of **≈ 38.6 min**.
- The protective monitor makes **one bulk `premiumIndex` call (weight 10) per 60 s, whatever the number of positions**.
- Perp frames per symbol per tour: 1d/400 (w2) + 4h/700 (w5) + 1h/500 (w5) + 5m/500 (w5) = w17. For 40 symbols that is about 680 per cycle, or about 18 per minute.
- Box timer: 5m/300 (w2) per symbol every 5 min, with the daily bars cached; about 16 per minute for 40 symbols.
- Formasyon: bounded by RateBudget (1200 × 0.7 = 840 per minute).
- The Binance USDⓈ-M IP limit is 2,400 per minute, so current use is **under 5%**.

**Effect of more open positions (learning mode), estimate:**
- **RAM:** a position with its meta is a few KB. 8 books × 40 positions is about 320 positions, which is under 10 MB. `trade_memory.jsonl` grows with closes: at roughly 200 closes/day × about 10 KB, that is about 2 MB/day of disk. The experience pool is capped by `retrieval_max_scan=5000`. Counterfactuals are ≤ 2,000 pending × 1 KB per book. **Net RAM change: under 50 MB.**
- **CPU:** exit checks are O(positions) every 60 s (microseconds each). Economics and risk per candidate are negligible next to agent analysis. **Tour time does not change** with position count.
- **Disk and I/O:** each ledger JSON is rewritten on every event, and its size grows with the closed history. Alert if any `futures_ledger.json` exceeds 5 MB, and archive the closed history then.
- **API:** unchanged. Positions stay inside the universe, and marks come from one bulk call.

**Effect of universe expansion (S10), where the hard technical limit is:**
- D4, C4 and Formasyon have **60-min entry windows**, and the books are evaluated **once per main cycle**, late in the tour. In the worst case a 4h close lands just after the book step, so the next evaluation is one full cycle later.
- Requirement: `cycle = 34.5·S + 900 ≤ 3,000 s` (50 min, leaving a 10-min margin), which gives **S ≤ 60 symbols**.
- Beyond 60, D4/C4 need their own 4h timer (S13); otherwise signals silently expire.
- Per extra symbol: about w17 per cycle, plus w2 per 5 min for Box, plus about 0.5–1 MB of frames. Do **not** raise `history.refresh_max_symbols` (16). Pattern-index series cost about 35 MB each (≈ 3.1 GB / 89 series), which is the real memory risk.
- The Box timer pass must finish in under 300 s: 60 symbols × about 0.5 s is about 30 s, which is fine.
- Formasyon widened to "all eligible" (300–500 perps): set `max_symbols_per_cycle 120`, which gives a full rotation in 3–5 minutes, well inside 60. API use rises but stays under RateBudget.
- Scanner as an entry feed (option B): **no**. It costs about 15,360 requests/day (`config.yaml:89-95`), and scanner symbols are not in `futures_required`, so SPOT frames could be substituted (`engine_v3.py:1468`).

**Safe caps (the only hard limits in learning mode):**

| Cap | Value |
|---|---|
| Entry universe | ≤ 60 symbols |
| Slots per book | the §2 values (margin arithmetic) |
| Counterfactual pending | ≤ 2,000 per book |
| Formasyon `max_symbols_per_cycle` | ≤ 120 |
| `history.refresh_max_symbols` | unchanged |

**Rollback triggers** (roll back universe growth first, then lower the counterfactual cap):
- worker peak RSS above 90% of MemoryMax (5.4G at 6G);
- tour longer than 35 min;
- Box `missed_bars` above 0 per hour.

---

## 6. ASK_USER: items that change what a strategy is

Recommendations follow from the user's stated goal. None of these ship without an explicit answer. Each answer becomes a `learning_mode.strategy_overrides` key, so turning learning mode off also reverts it.

| id | Item | Today | Expected trade-count effect | Recommendation |
|---|---|---|---|---|
| S1 | Main regime gate `r1_long_only_uptrend` ENFORCE → SHADOW (`config.yaml:382`) | Blocks 100% of SHORTs, and all LONGs while BTC 1d ≤ EMA200 | In the pre-gate replay, SHORTs were 25 of 42 main trades (≈ 60%), so main trades rise about 2.5× in mixed regimes, and from 0 to all candidates in a BTC downtrend. DENEY_V7: it reduces losses but is not proven profitable. | **SHADOW.** The verdict stays in `entry["regime_gate"]` as a feature. |
| S2 | Main candle veto `c3_4h_veto` ENFORCE → SHADOW | Drops triggered candidates with an opposite 4h candle; fails closed on error | Unknown (no committed funnel; `state/risk.json` `candle_blocked` would show it). DENEY_V4: no variant validated. | **SHADOW** (all 4 variant verdicts stay as features). |
| S3 | Main structures ENFORCE → SHADOW, keeping STRUCTURE_FRAME_MARKET_MISMATCH as a hard block | Blocks pullbacks without a confirmed structure, opposing confirmed, broken compatible, chase > 1 ATR, forming | Probably the largest main drop (every pullback plan without a confirmed structure). The T2/M2 analogue was 72–76%. | **SHADOW for entries**; see S17. |
| S4 | T2/M2 structures ENFORCE → SHADOW | 1-year replay: T2 4,404 of 5,786 and M2 5,965 of 8,319 rule-OPENs blocked | With A1 relaxed, T2/M2 entries rise by up to about 4× the rule-OPEN pass-through. Without A1, M2 can **fall** (39 → 18 in the replay), because M2 structure exits freed slots. **Ship only together with A1–A3.** | **SHADOW.** The labs tested T2/M2 without this layer (Box was already moved for the same reason). M2's 22 structure exits disappear, restoring lab parity. |
| S5 | Economics: RESEARCH_SIZE_ONLY and NEGATIVE_NET_EDGE open as exploration trades; `short_penalty_r 0.50` and `futures_only_penalty_r 0.35` set to 0 (logged as features). Keep ZERO_STOP_DISTANCE and UNKNOWN_GATE_CODE. | Killed; a shadow only for RESEARCH_SIZE_ONLY | RESEARCH 185/20,000; NEGATIVE count unknown (folded into other kinds). The p_win and conservative-edge ranking was **reversed** in WIP handoff §3.2 (winners 0.343 vs losers 0.434), so the gate's key input is not validated. | **Yes, with tags** `exploration=RESEARCH_SIZE` / `NEG_EDGE`; the learner reports them separately. |
| S6 | T2/M2 BTC-regime-UP inside the rule | T2/M2 take 0 trades while BTC < EMA200 | Keeping it gives 0 in downtrends. The alternative is a **new** PAPER book `t1_trend` (already in `ema200_trend.VARIANTS`, no regime condition) with its own state_dir. | Keep T2/M2 as they are; add `t1_trend` **if** regime-free trend experience is wanted. |
| S7 | Confidence thresholds: leverage CONFIDENCE_BELOW_BASE (< 0.30) falls back to 2x. Coin-head `min_confidence` / `consensus_threshold` are already soft. | NO_TRADE | Part of the 276/20,000 LEVERAGE_BLOCKED | Yes (tag `LEARNING_MIN_LEVERAGE_FALLBACK`). |
| S8 | Main NO_TRIGGER: let a pullback within 1% enter at market | Defers | 496/20,000, the largest in-funnel drop. It changes the entry geometry (worse R/R). | **No.** Keep it; the signal has not fired. |
| S9 | Formasyon: universe 30 lab coins → the 40-coin entry universe ∪ V3 (tagged), or all eligible; and/or run classic as a second PatternBook | About 1.5 signals/day | All-eligible gives about 10× signals, out of sample. A classic book doubles scanner load and needs `BOOK_KEY` separation. | Union with the 40 coins (tagged) only; classic: no (v3 approved 2026-09-25; classic had no edge after cost). |
| S10 | Entry universe 40 → ≤ 60 verified perps (spread ≤ 0.06%; relax only listing age). This also widens main entries. | 40 | +50% symbols for all books | Yes, up to the **60 hard cap** (§5). |
| S11 | C4S (0 trades by construction) | idle | 0 | Keep as the strict control, or disable. Do **not** weaken the gate. |
| S12 | Split C4 into 8 per-variation books (config only) | 1 slot per symbol, `hits[0]` only | Each variation gets its own slots; 8 more ledgers and dashboard entries; no API cost. | Counterfactual now (C10); split only if the user wants filled trades per variation. |
| S13 | Own 4h timer for D4/C4 | tied to the main tour | Removes silent 60-min expiries | Only needed if S10 goes above 60 symbols, or if expiries appear. |
| S14 | Fresh `_learn` state dirs with larger virtual equity (books 1,000; Formasyon 1,000; main 500) | 200 / 100 / 100 | BTC/ETH and other high-min-notional coins become tradable. Resets the USDT curves; the old dirs are kept. | Optional; R is unaffected. |
| S15 | `learning_v3.influence_mode: PAPER_BOUNDED`, `max_fraction 0.20` (validator allows it in PAPER only) | SHADOW (applied = 0) | This is literally "önceden kazandım → gir": p_win moves ±20%. With S5 relaxed it mainly changes tags and leverage tiers, not entry. With S5 kept, it can turn NEGATIVE edges into trades. | Yes if S5 stays ENFORCE; optional otherwise. |
| S16 | Box `min_stop_pct` 0 (instead of 0.32) | 2.22 | 26–32k signals per window vs 2.5–5.6k (DENEY_V15); net −0.21 to −0.46R at 0 | 0.32 by default. |
| S17 | Main structure TIGHTEN_STOP management under SHADOW (an **exit** change) | ON in ENFORCE | Changes hold time and R distribution | Ask separately. The default when S3 = SHADOW is to keep stop-tightening ENFORCE (entries SHADOW, management ENFORCE); this needs a separate mode key. |

---

## 7. Tests, dashboard and deploy

### 7.1 Tests (all new, `tests/test_learning_mode_*.py`)

1. **OFF parity (golden).** Run one main tour fixture, `StrategyBook.step` for each rule family, a Box timer pass, and a `PatternBook` cycle, each with `learning_mode` absent and with `enabled: false`. Decisions, reject codes, ledger JSON bytes and journal rows must be identical, and the `HistoricalReplay` window hash must be unchanged. `apply_action(learning=None)` must be bit-identical to today.
2. **PAPER-only guard.** `enabled: true` with mode ∈ {TESTNET, OBSERVE, SHADOW_LIVE, LIVE_LIMITED, LIVE} → ConfigError. Also ConfigError for: gateway `binance_*`, `testnet_enabled: true`, a non-PAPER_RESEARCH profile, an unknown key or book, or out-of-range bounds.
3. **Runtime suspension.** A `mode_state` transition away from PAPER (or `is_live_order_path_enabled` patched to True) makes `active()` return False, sets the `LEARNING_MODE_SUSPENDED` health flag, and every path returns to baseline, including the Formasyon book-level count check (D1) and the strategy overrides.
4. **Limits lifted.**
   - 12 synthetic valid signals in one book all open; no TOTAL_OPEN_RISK, INSUFFICIENT_MARGIN or MAX_POSITION_PCT.
   - Formasyon opens a 4th position.
   - The cooldown is not set.
   - RISK_ABOVE_CAP rescales instead of rejecting.
   - min R/R 0.
   - The leverage fallback returns 2 only for the allowed failure set; DATA_STALE, SPREAD_ABOVE_BASE and LIQ_BUFFER still return NO_TRADE.
   - The Box `min_stop` learning params are used.
   - D4/C4 see 40 symbols.
5. **Sizing properties (hypothesis)** for s ∈ [0.2%, 20%], K ∈ [4, 200], L_max ∈ [1, 5]:
   - notional ≤ m_slot·L;
   - risk ≤ min(r_L·E, 2%·E);
   - `1/L − mmr ≥ liq_buffer_mult·s`;
   - after K opens, Σ margin ≤ 95% E;
   - a closed trade's R is identical at E = 200 and E = 1,000.
6. **Min-notional bump.** The bump happens only within the 2% cap and available margin, and qty is rounded up to the step. Otherwise it rejects and writes a counterfactual.
7. **Keeps hold in learning mode:** DUPLICATE_SIGNAL, SIGNAL_ALREADY_USED, DATA_*, BAD_STOP, UNVERIFIED_PRECISION, a tripped kill switch, and one position per symbol all still block.
8. **Counterfactual.**
   - A signal blocked for capacity over 16 tours of one bar creates exactly **one** record (P1).
   - No record for DATA_*, DUPLICATE or NO_TRIGGER.
   - Stop-first labelling on synthetic bars, for each `label_kind`.
   - The pending cap and drop counter work.
   - Ledger equity is unchanged by counterfactuals.
9. **P0 journal fix.** An EXCHANGE_REJECTED row is classified OPEN_FAILED, and `exec_reject` is persisted.
10. **Profiles.** `PROFILES` is unchanged (existing tests still pass), and the learning profile is not registered in it.
11. **allow_shrink.** It is honoured per call and **not** persisted (the ledger JSON still has `allow_shrink: false`).
12. **Per-timeframe provenance (B7).** With 5m missing, main/T2/D4 entries are allowed and Box is blocked. A SPOT frame never reaches a perp rule's timeframe.
13. **Replay parity.** A `HistoricalReplay --learning-mode` run reproduces the live `apply_action` decisions on the same bars.

### 7.2 Dashboard and scorecards

- A global banner: **"ÖĞRENME MODU AÇIK — yalnız PAPER"**, or **"ÖĞRENME MODU ASKIDA: <reason>"**. A per-book badge, and a `learning_mode_since` timestamp.
- Every performance chart and scorecard is split **before/after** `learning_mode_since`. USDT P&L after that point is labelled "öğrenme ölçeği (%0,5 risk, slot K)" and must not be compared with USDT from before. **R-based metrics are primary.**
- Every trade carries these tags:
  - `size_rule` (SLOT / BUMP_MIN_NOTIONAL / SHRUNK_TO_MARGIN);
  - `risk_fraction_of_budget`;
  - `learning_unlocked_by: [codes]`, computed cheaply by re-running the baseline checks: the limits that would have blocked the trade in baseline, or empty;
  - `exploration` (S5/S7);
  - `in_lab_universe` (C9/S9/S10).
- The scorecard has three columns:
  1. **policy trades** (baseline would also have taken them);
  2. **learning-extra trades** (`learning_unlocked_by` non-empty);
  3. **counterfactuals** (labelled, never in P&L).
- Risk budget cards show the learning profile (total risk 100%, r 0.5%, Σmargin/E, slots used/K). The chief brief text "en fazla 3 pozisyon; toplam risk ≤ %6" (`agents/manager.py:278` path) must be updated.
- New funnel keys: `learning_unlocked`, `counterfactual_recorded`, `counterfactual_dropped`, `learning_leverage_fallback`, `min_notional_bumped`, `shrunk_to_margin`.

### 7.3 Deploy invariants

1. `--check` prints and asserts: `mode=PAPER`, `gateway=paper`, `testnet_enabled=false`, `risk_profiles.profile=PAPER_RESEARCH`, `overrides={}`, `learning_mode.active=true`, and the per-book slots and leverage.
2. MemoryMax is verified on the host (6G expected; the repo unit file says 4G). The RSS alert is at 90%.
3. Release order, each step separately revertible with `enabled: false`:
   1. P0–P2 fixes (no behaviour change);
   2. learning capacity and sizing (A/B/C/D RELAX/REMOVE rows) plus counterfactuals;
   3. the §6 answers as `strategy_overrides`.
4. **Pre-deploy acceptance:**
   - full pytest;
   - a 1-year real-archive replay with `--learning-mode` for T2/M2/D4/C4 and the three DENEY_V15 Box windows;
   - it must report opens, maximum concurrency, maximum Σmargin/E (≤ 95%), **liquidations = 0**, and minimum equity;
   - compare against the committed OFF runs.
5. **Post-deploy, first 2–3 tours:**
   - opens per book above the baseline;
   - no INSUFFICIENT_MARGIN spike;
   - tour under 30 min;
   - Box `missed_bars` = 0;
   - peak RSS under 5.4G.
6. Existing `state_dir`s and `state/killswitch.json` are untouched, unless S14 is chosen, in which case new `_learn` dirs are used and the old ones stay readable.
7. Rollback: `enabled: false` and restart. Open positions keep being managed; TOTAL_OPEN_RISK blocks new entries until open risk falls under 6% (this is shown on the dashboard).

