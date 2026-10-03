# Shared Experience Layer v1: orchestrator decisions (2026-09-29)

This file answers the open questions in SPEC_V1.md §17. Where it conflicts with SPEC_V1, this file wins.

## User intent (verbatim)

The user asked two things:

- "bu botlarımızı farklı farklı öğrenme stratejileri aslında bir olmalı değil mi? bir olsa belki de diğer coinler de kullanabilir vaziyete gelebilecek ve gerçekten bu coinler öğrenebiliyor mu?"
- Earlier: "sistemin … öncesinde de kar etmiştim gireyim veya zarar etmiştim bekliyim demesi gerek … Bunu kurmalıyız en iyi şekilde".

What we answered and must build:

- **One memory.** All books write into one store.
- **Learning by SITUATION, not by coin.** Situation plus setup is pooled across coins. Per-coin effects are used only through partial pooling (shrinkage toward the pooled estimate), so new or rarely traded coins benefit immediately.
- **Setups stay separate.** `setup_key = book|setup_type|side`, plus a coarser `family` (TREND / FADE / BREAKOUT / CANDLE_PATTERN / MOMENTUM) with `side`, for cross-bot pooling among similar logics. Opposite logics (trend vs fade) are never averaged together.
- **Phase 1 is RECORD only.** Decisions never change.

## Answers to SPEC_V1 §17

1. **Net-R contract.** It exists in code now (commit 3cca8a8, `learning_cf.py`).
   - **Counterfactual outcome keys:** `label_version` ("cf_label_v2" = net replay; "cf_label_v1" = gross only; "cf_label_v1c" = closed-form estimate, never mixed into net statistics), `r_gross`, `r_net`, `cost_r`, `cost_parts_r`, `won_net`, `funding_complete`, `net_status`.
   - **Legacy labels** get net fields lazily or via `scripts/cf_backfill_net.py`. The layer treats a changed outcome as a new revision.
   - **Real trades:** `r_multiple` is already net (the ledger). `cost_r` = (fees + slippage + funding paid) / risk_usdt. That is all-in, the same basis as counterfactual `cost_r`.
2. **Backfill scope.** All ledger history, tagged `pre_learning` / `origin`.
3. **SPOT.** Excluded in v1. Main spot trades and SPOT counterfactuals are not recorded, and this is counted in status.
4. **Thresholds.** A win is `r_net > 0`, as in the scorecard.
   - **Verdict:** `min_n = 30` per cell (same as the scorecard).
   - **Descriptive:** cells with n >= 10 are shown descriptively with a CI.
   - **Below 10:** INSUFFICIENT_SAMPLE.
   - **Per-coin partial pooling:** `shrunk_mean = (n_coin·mean_coin + k·mean_pooled) / (n_coin + k)`, with k = 20 (configurable). Report both values.
5. **Dimensions and backoff order.** The proposed order is approved: structure → volume → BTC → vol → trend. Add `family` pooling as described above.
6. **C4 zero activity.** Investigated; no bug (the probability of 0 signals in 2 closes is 9–24%). A read-only VPS diagnostic exists. Nothing to do in this layer.
7. **A15 `POSITION_OPEN` counterfactuals with non-empty `baseline_blocked_by`.** Include them in counterfactual statistics with a flag column, and also show a filtered view that excludes them.
8. **Release.** Ship together with the net-R release in one deploy.
9. **Scope.** PAPER only.
10. **Surfaces and limits.**
    - Report via the CLI, plus a small read-only dashboard card (counts, last sweep, top cells). The full dashboard is Phase 2.
    - `hot_max_lines = 5000` is acceptable.

## Also in this release (W2)

- **Fix the review findings on `cf_label_v2`:**
  - **High:** main 4h replay intra-bar ordering. A bar's high moves the stop to break-even, and then a stop fill at that same bar's open is used. That ordering is impossible and must go.
  - **Medium:** stop fills on 4h/1d bars are pessimistic compared with the real 60 s-mark monitor.
  - **Low:** v1c "upper bound" wording and finality; Formasyon `qmark` double slippage.
- **Evaluate the pre-existing live bug:** a closed 1h bar applied after a mark-driven break-even move closes the position at that bar's earlier open, in `strategy_paper` `apply_closed_bars`.
  - Fix it only if it is clearly wrong and a minimal, well-tested change exists. It changes live PAPER accounting, so isolate it in its own change and document it.
  - Keep it out of the learning-OFF parity claim; it is a bug fix.
