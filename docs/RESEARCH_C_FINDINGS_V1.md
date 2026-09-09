# Research C — where the money is lost, and three pre-registered challengers that all failed

Branch `work/research-c-v1`, base `1c4cba1`. Production snapshot read-only. Nothing deployed,
nothing pushed, the bot was not started.

**Headline, stated first because it is the least comfortable thing in this document:**

> On the 29 closed PAPER trades the system's expectancy is **−0.357 R/trade with a 95% confidence
> interval of [−0.852, +0.139]**, and its win rate is 7/29 = 24.1% with a Wilson 95% interval of
> [12.2%, 42.1%] — an interval that **contains** the 36.1% breakeven rate implied by its own payoff
> ratio. **29 trades do not establish that this system loses money.** Every statement below about
> "where the loss comes from" is a decomposition of a point estimate that is itself not significant.
> The three pre-registered challengers were then measured on a much larger counterfactual sample —
> and **all three failed their own criteria; two were measurably harmful.**

---

## 0. Instruments, and why you should believe them

| instrument | what it does | validation |
|---|---|---|
| `tradingbot/replay/fidelity.py` (vendored verbatim from `research/replay-fidelity-v1@3acd7b6`) | replays a closed ledger trade on real 1m bars through the production `FuturesLedgerV2` | **Re-derived independently on this base, not taken on trust:** 29/29 exit reasons reproduced, 29/29 entry-price/quantity/fee parity, aggregate deviation **+0.2953 USDT / +0.3318 R** — matching the harness author's stated figure to the digit. `tradingbot/accounting` and `tradingbot/core` are byte-identical between `8163a79` and `1c4cba1`, so the harness transfers unchanged. |
| `tradingbot/replay/counterfactual.py` (new) | replays **every** candidate in `entry_snapshot.jsonl` — accepted and rejected — on real 1m bars, isolated ledger | Against the 4 accepted candidates whose real trades resolved inside the 72 h horizon (ONDO's horizon extends past `data_end` so its row is flagged immature, but its trade closed naturally at 2.5 days and the comparison stands): NATGAS cf −1.060 vs real −1.060; BNB +2.416 vs +2.411; XAUT −1.126 vs −1.123; ONDO −1.030 vs −1.117. **max \|Δ\| = 0.087 R.** The two whose real trades ran past 72 h (LTC, NVDA) differ for that reason and are labelled, not hidden. |
| `tradingbot/replay/challengers.py` (new) | one shared `run_plan` for baseline and every challenger; only a `Policy` object differs | Baseline arm reproduces the independent `counterfactual.py` run **exactly**: max \|ΔR\| = **0.0** across 2388 candidates. Two independent code paths, identical numbers. |
| `tradingbot/replay/stats.py` (new) | cluster bootstrap, Wilson intervals, Holm–Bonferroni, a 15-observation floor | — |

**No look-ahead anywhere.** The first bar fed to any replay is the first 1m bar *strictly after* the
decision instant; the entry bar's pre-entry portion is never seen. No indicator is recomputed, so
there is nothing to leak: the entry, stop and target prices are read verbatim from the record the
bot wrote at decision time.

**Intrabar ordering is the production ledger's own**: liquidation → **stop** → targets, so when a
stop and a target both sit inside one bar the stop wins. Measured: at 1m resolution this situation
**never occurred** — 0 of 1334 pool candidates had a single ambiguous bar. The ordering rule was
never load-bearing here. **The resolution choice still is**: the documented ZRO case resolves
BE_STOP +0.14 R at 1h and TP2 +2.37 R at 1m. Everything in this document is 1m.

**No LLM or agent opinion was generated for any historical bar.** No challenger here needs one.

---

## 1. The decomposition — where does −0.357 R/trade come from?

### 1.0 First, the interval that governs everything else

| statistic | n | value | 95% interval |
|---|---|---|---|
| expectancy | 29 | **−0.3567 R/trade** | **[−0.852, +0.139]** (normal); bootstrap [−0.808, +0.171] |
| win rate | 29 | 24.14% | Wilson **[12.2%, 42.1%]** |
| breakeven win rate at the realised payoff (wins +1.9083 R, losses −1.0774 R) | — | 36.09% | — |
| total | 29 | −7.03 USDT on 100 USDT start | — |

The win-rate interval contains the breakeven rate. The expectancy interval contains zero. The
correct summary is: *the point estimate is bad, and 29 trades is not enough to know.*

### 1.1 (c) Cost drag — real, small, and not the cause

Per trade, in units of the trade's own risk (n = 29, normal 95% CI):

| component | R/trade | 95% CI | total USDT |
|---|---|---|---|
| commissions (entry+exit) net of funding | **+0.0376** | [+0.0155, +0.0597] | 0.4289 |
| slippage (already inside the fill price) | **+0.0419** | [+0.0275, +0.0563] | 0.9797 |
| **total friction** | **+0.0795** | **[+0.0538, +0.1052]** | **1.4086** |

Removing *every* cost leaves **−0.277 R/trade**. Friction is ~22% of the loss point estimate.
It is heterogeneous and knowable in advance — cost in R is `expected_cost_pct / stop_distance_pct`,
so the SPY trade with a 0.74% stop paid **0.36 R** in friction before it started, while KORU with a
14.6% stop paid 0.007 R. But across the 1334-candidate pool the cost-in-R distribution is tight
(median 0.028, p90 0.050, max 0.161), so this lever has almost no room in the pool.
**Cost is not the problem.**

### 1.2 (b) Exit behaviour — there was almost no profit to give back

Using **true 1m-resolution MFE** from the replay, not the production ledger's live MFE (which is
systematically under-measured because ~79% of live exit checks see only a last price, no bar
extremes):

| population | n | mean MFE | ≥0.5 R | ≥0.75 R | ≥1.0 R | ≥1.5 R | ≥2.0 R |
|---|---|---|---|---|---|---|---|
| realised losers | 22 | **+0.443 R** | 7 | 6 | **3** | 0 | 0 |
| realised winners | 7 | **+2.780 R** | 7 | 7 | 7 | 7 | 7 |
| counterfactual pool losers | 636 | **+0.252 R** | 124 | 74 | **44** | 3 | 0 |
| counterfactual pool winners | 698 | +1.678 R | — | — | 518 | — | 252 |

The losing trades did not squander profit. **They never had any.** Only 3 of 22 realised losers ever
reached +1.0 R; none reached +1.5 R. The whole recoverable "giveback" pool is those 3 trades.

That bound is confirmed by measuring the rule production already deployed for exactly this purpose
(`breakeven_at_mfe_r = 1.0`, live since 2026-09-09T11:24Z, i.e. after 28 of these 29 trades):

> **BE-at-MFE-1.0 R applied from the start of all 29 realised trades: ΔR = +0.1073 R/trade,
> 95% CI [+0.0000, +0.2273], and it changes exactly 3 of 29 outcomes** (BZ −1.033→+0.015,
> BMNR −1.006→+0.004, MSFT −1.070→−0.015).

The interval's lower bound sits *on* zero, because in 2.5% of bootstrap draws none of those three
trades is sampled. This is the only lever measured anywhere in this work with a point estimate
above the +0.10 R actionability floor — **and it is already live in production.** It is not a
finding you can act on; it is a finding that the exit side has already been harvested.

**Conclusion for (b): the exit is not where the money is. Expectancy net of the best implementable
exit repair is still ≈ −0.25 R/trade.**

### 1.3 (a) Entry selection — the model does not rank its own candidates

Instrument: every one of the 2797 candidates replayed forward on real 1m bars. Of these:

| bucket | n | why |
|---|---|---|
| **mature and evaluable** | **1334** | 95 symbols, 144 symbol×day clusters, 5 UTC days |
| immature (`ts + 72 h > data_end`) | 1173 | data ends 2026-09-09T19:51Z; excluded from every estimate |
| venue-infeasible | 409 | **all BTC/USDT** — Binance `BTCUSDT` perp `minQty` 0.001 (≈110 USDT) and `minNotional` 50 USDT versus a planned notional of 30 USDT. The bot spends 14.6% of its evaluation budget on a symbol it cannot trade at this account size. |

**Direct accepted-vs-rejected test is dead on arrival.** Production accepted 14 of 2797 candidates
in this window, and **6** of those are mature. Day-demeaned difference accepted − rejected =
**+0.529 R, 95% CI [−0.625, +1.762], p = 0.35, n = 6 vs 1328. UNDERPOWERED — no verdict.** Six
observations cannot answer this question and no amount of arithmetic will change that.

**The answerable version is whether the model's scores rank candidates.** Spearman rank correlation
against **day-demeaned** forward R (day-demeaning removes the market-wide move, which is what makes
this a cross-sectional test rather than a bet on one week), cluster-bootstrapped on symbol×day,
n = 1334, 144 clusters:

| decision-time score | rho | 95% CI | p |
|---|---|---|---|
| `p_win` (calibrated, the probability the gate now uses) | **−0.109** | [−0.257, +0.059] | 0.17 |
| **`conservative_net_edge_r`** (the variable the economic gate is built on) | **+0.032** | [−0.136, +0.197] | 0.74 |
| `net_expectancy_r` | +0.063 | [−0.093, +0.214] | 0.46 |
| `expected_r` | −0.055 | [−0.203, +0.102] | 0.47 |
| `baseline_rank` (production's own ordering) | −0.053 | [−0.208, +0.107] | 0.54 |
| `confidence` | +0.097 | [−0.090, +0.272] | 0.33 |
| `consensus_score` | +0.140 | [−0.030, +0.306] | 0.11 |
| `sample_size` | −0.087 | [−0.230, +0.069] | 0.26 |
| 7 specialist scores (mtf, trend, momentum, volume, levels, derivatives, liquidity) | −0.06 … +0.18 | all CIs touch 0 except `trend` | — |

Twenty predictors were tested; three touched p ≈ 0.05 (`expected_cost_pct` −0.180 p=0.041,
`stop_distance_pct` −0.158 p=0.047, `spec_trend` +0.182 p=0.045). A fourth, `n_vetoes`, returned
p = 0.000 — it is **constant at zero across all 2797 candidates**, so that is a degenerate artifact
and is discarded, not counted. Twenty tests at α = 0.05 produce one such hit by chance alone, and
Holm rejects all three real ones. **There is no measured ranking signal in the entry model, and the point estimate for the
calibrated probability is the wrong sign.**

### 1.4 What the pool level is *not*

Taking **every** mature candidate returns **+0.263 R/trade** at a 72 h horizon. Do not use that
number. Per UTC day:

| day | n | mean R |
|---|---|---|
| 2026-09-02 | 82 | −0.027 |
| 2026-09-03 | 303 | −0.002 |
| **2026-09-04** | **329** | **+0.824** |
| 2026-09-05 | 304 | +0.195 |
| 2026-09-06 | 316 | +0.072 |

97% of candidates are LONG. One market-wide up-day carries the entire result; drop it and the pool
mean falls to ≈ +0.08 R. At a 48 h horizon the pool mean is **+0.099, 95% CI [−0.066, +0.264]** —
indistinguishable from zero. **Five market days with 97% one-way exposure is one observation, not
1334.** The *level* of edge is not identified by this data at any horizon. Only *relative*,
within-day comparisons are.

### 1.5 Decomposition, assembled

Realised, n = 29, all figures R/trade:

```
  realised net expectancy                                     −0.357   CI [−0.852, +0.139]
    − commissions and funding                                 +0.038   CI [+0.016, +0.060]
    − slippage                                                +0.042   CI [+0.028, +0.056]
  = frictionless expectancy                                   −0.277
    − best implementable exit repair (BE@1R, ALREADY LIVE)    +0.107   CI [+0.000, +0.227]
  = expectancy with costs and exits both at their best        ≈ −0.17  (residual, no valid CI:
                                                                        the terms are not independent)
```

Everything left over is entry selection, and section 1.3 measured entry selection to have **no
ranking signal at all**. That residual is the honest location of the problem — and it is the one
place this dataset has the least power to fix, because production accepted 14 candidates in 7 days.

---

## 2. The three pre-registered challengers

`docs/CHALLENGERS_V1.md` was **committed at `5207066` before any challenger was run.** Predictions
were recorded in it in advance. Nothing below was re-thresholded, re-clustered or re-populated
afterwards.

Population: 1334 mature evaluable candidates, 144 symbol×day clusters, 5 UTC days.
Baseline pool mean R = +0.263 (see §1.4 for why that level is not a claim).
C1/C2 measured as **paired** ΔR per candidate (day effects cancel exactly); C3 as a difference in
day-demeaned R between the passing subset and its complement, from a common cluster resample.

| | C1 — dead-trade time box (cut at 24 h if MFE < 1 R) | C2 — first target at 1.0 R | C3 — economic gate raised to the median of its own variable |
|---|---|---|---|
| **pre-registered prediction** | positive but < +0.10 R → fails | \|ΔR\| < 0.10 R, sign uncertain → fails | ΔR ≈ 0 → fails |
| **measured effect** | **−0.1641 R/trade** | **−0.0616 R/trade** | **+0.0600 R/trade** |
| 95% CI (cluster = symbol×day, 144) | **[−0.2639, −0.0721]** | **[−0.1206, −0.0069]** | [−0.2751, +0.3801] |
| bootstrap p (two-sided) | < 0.0002 | 0.0244 | 0.744 |
| cluster = symbol (95) | −0.1641 [−0.2515, −0.0667] | −0.0616 [−0.1186, −0.0100] | — |
| cluster = day (5 blocks — reported, not trusted) | −0.1641 [−0.2911, −0.0268] | −0.0616 [−0.1289, −0.0097] | — |
| mean R baseline → challenger | +0.263 → **+0.099** | +0.263 → **+0.201** | pass 667 / fail 667 |
| win rate baseline → challenger | 52.3% → **44.9%** | 52.3% → **57.6%** | — |
| A1 effect ≥ +0.10 R | **FAIL** (wrong sign) | **FAIL** | **FAIL** (+0.060) |
| A2 CI excludes 0 favourably | **FAIL** | **FAIL** | **FAIL** |
| A3 Holm at FWER 0.05 | rejected the null, but **in the harmful direction** | same | not rejected |
| A4 leave-one-day-out | negative on all 5 (−0.094 … −0.212) | negative on all 5 (−0.029 … −0.082) | sign **flips** on 2026-09-04 (−0.147) |
| A5 same sign at 48 h | yes: −0.049 [−0.121, +0.013] | yes: −0.027 [−0.074, +0.013] | yes: +0.079 [−0.165, +0.313] |
| A6 same sign on the out-of-pool 29 realised trades | yes: −0.036 [−0.381, +0.261] | yes: −0.068 [−0.156, +0.015] | n/a |
| A7 portfolio-constrained replay | n/a | n/a | **NOT REACHED** (see below) |
| **VERDICT** | **NOT ACCEPTED — measurably harmful** | **NOT ACCEPTED — harmful, below the actionability floor** | **NOT ACCEPTED — null, as predicted** |

**C1 was my prediction's biggest miss and the most interesting result.** I predicted a small positive
effect. It is **−0.164 R/trade with a CI comfortably clear of zero, on all five days, at both
horizons, and with the same sign on the independent 29 realised trades.** The mechanism is visible
per trade: the time box does salvage slow losers (KORU −1.03→−0.30, AAPL −1.07→−0.17, XPD −1.08→+0.02)
but it decapitates the slow winners, which are where all the money is — ZRO +2.366→+0.391,
BNB +2.418→−0.185, LTC +1.848→+0.130, XAUT +1.198→+0.066. Win rate falls 52.3%→44.9%.
**In a system whose entire payoff lives in a handful of 2–3 R runners, patience is not a bug.**

**C2 behaved exactly as designed and that is why it fails.** It raises the win rate 52.3% → 57.6% and
lowers mean R 0.263 → 0.201. Moving the first partial from 2 R to 1 R buys hit rate at more than its
price. Same negative sign at 48 h and on the realised 29.

**C3 is the pre-registered null that matters most.** Raising the production economic gate to the
median of its own decision variable moves the pool by +0.060 R with a CI of [−0.275, +0.380] — and
the sign flips when the single dominant day is removed. **The variable the entry architecture is
built on did not separate good candidates from bad ones IN THIS 1334-candidate, 5-day sample.**
That is a failure to detect separation, not a demonstration that no threshold on this variable can
work; no power calculation was performed, so the sample size needed to detect a small effect is
unknown. A7 (portfolio-constrained confirmation)
was **not reached**: C3 failed A1–A4 on the per-candidate metric, and no portfolio simulation can
rescue a filter whose per-candidate effect is indistinguishable from zero. I did not run one and I
am not going to present one as if it added information.

**Family-wise correction.** Holm across the three: C1 p<0.0002 vs threshold 0.0167 → rejected;
C2 p=0.0244 vs 0.025 → rejected; C3 p=0.744 vs 0.05 → not rejected. Both rejections are in the
*harmful* direction. No challenger is accepted at any level.

---

## 3. Exploratory — NOT findings, hypotheses for a future pre-registration

These comparisons were made **after** seeing outcomes. They are post-hoc, they are part of a family
of ~25 tests across this document, and they carry no multiplicity correction. **Do not act on them.
Do not report them as results.** They are recorded because they are the most promising things to
pre-register next.

| post-hoc cut (day-demeaned R, cluster = symbol×day) | effect | 95% CI | p | n |
|---|---|---|---|---|
| `LEVERAGE_GATE_BLOCKED` vs everything else | **−0.534** | [−0.800, −0.258] | 0.0005 | 299 / 1035 |
| same, restricted to LONG only (rules out the SHORT confound) | −0.536 | [−0.799, −0.262] | <0.0001 | 291 / 1002 |
| `NO_TRIGGER` vs everything else | **+0.371** | [+0.079, +0.665] | 0.0115 | 495 / 839 |
| same, LONG only | +0.370 | [+0.087, +0.677] | 0.0115 | 474 / 819 |
| SHORT vs LONG | −0.843 | [−1.717, −0.268] | 0.0015 | 41 / 1293 — **only 7 clusters, underpowered by this document's own rule** |
| `RISK_CAPACITY_BLOCKED` vs everything else | +0.008 | [−0.255, +0.277] | 0.94 | 524 / 810 |

Read plainly: the **leverage gate appears to be blocking candidates that were genuinely worse**, and
the **`NO_TRIGGER` condition appears to be discarding candidates that were genuinely better** — i.e.
the trigger may be anti-predictive. The SHORT penalty direction is consistent with the independently
measured −0.63 R from 2026-09-09, but 7 clusters cannot establish it here. `RISK_CAPACITY_BLOCKED`,
the single largest rejection reason (1208 of 2783), is **exactly neutral** — meaning production's
most common reason for not trading is capital, not judgment, and it neither helps nor hurts.

---

## 4. What this data CANNOT settle

Stated without hedging:

1. **Whether the system loses money.** n = 29, expectancy CI [−0.852, +0.139], win-rate CI containing
   the breakeven rate. Not settled. Roughly 150–250 closed trades at this variance would be needed
   to separate −0.36 R from 0 at 95%.
2. **The level of edge in the candidate pool.** Five market days, 97% LONG, one day carrying the
   whole result. Not settled at 72 h, not settled at 48 h, and not settleable by more compute — only
   by more calendar time.
3. **Whether production's acceptance decision adds value.** Six mature accepted candidates. Not
   settled, and not close.
4. **Anything about the 29 realised trades' entry quality.** Their decision-time snapshots do not
   exist: `entry_snapshot.jsonl` begins 2026-09-02T19:06Z and 23 of the 29 opened before that. The
   ledger's `features` block for the older trades carries the pre-`1c4cba1` HEAD prior
   (`p_win = 0.5`), not a calibrated probability, so it cannot substitute.
5. **Any challenger that would take candidates production rejected.** The isolated harness measures
   per-candidate forward return; it does **not** reproduce margin competition, the position cap, or
   entry ordering. C1 and C2 respect that boundary by construction (they change exits on trades
   already taken). C3 does not, which is why A7 existed — and A7 was not reached.
6. **Any rule requiring a signal that did not exist at decision time.** No LLM or agent opinion was
   generated for a historical bar. If a challenger needs one, it is not evaluable and it was not
   evaluated.
7. **BTC/USDT — 14.6% of the candidate stream.** Venue-infeasible at this account size, therefore
   absent from every estimate here.
8. **Whether the exploratory effects in §3 are real.** They are post-hoc, uncorrected, and drawn
   from the same 5 days.

---

## 5. The shadow stream (deliverable, runnable)

Because §4 says the data cannot deliver a verdict on the thing that matters, the working shadow
stream is delivered as required.

```
python scripts/shadow_stream.py --snapshot <SNAP> --eval data/chal_72h.json \
       --out data/shadow_stream_72h.jsonl
# 2797 records, 1624 mature, 14 accepted, 2783 rejected — rejected candidates are KEPT
```

Committed sample: `research_out/shadow_stream_sample.jsonl` (40 records, including all 14 accepted).
Schema `research_shadow_v1`, namespace `research_shadow`. Per record:

* `decision` — accepted vs rejected, `reject_reason`, rank, `chief_allow`, `risk_allowed`, `risk_reasons`
* `frozen_inputs` — every decision-time field, plus `specialist_scores`, `weekly_structure`, `candle_context`, `mtf_context`, `missing_fields`
* `code_version` — `code_sha`, `config_hash`, `policy_version`, snapshot schema
* `probability` — the value **and its source** (`MODELED` / `MEASURED` / `MISSING`, from the snapshot's own `sources` map)
* `cost` — `expected_cost_pct`, its source, the derived cost-in-R, funding/spread/slippage where recorded
* `penalties` — `uncertainty_penalty_r`, config SHORT penalty, config futures-only penalty
* `size` — planned notional, planned leverage, size multiplier
* `maturity` — horizon, `matures_at`, `data_end_ms`, `mature` flag
* `labelling_rule` — the exact rule that produced R, in the record itself
* `dependence` — `episode`, `cluster`, `day`, `episode_size`, and the stated rule that **the independent unit is the episode, not the row**
* `results` — `baseline`, `C1`, `C2` side by side, plus C3 as a selection flag

**Simulated results are never written into the learning records of real closed trades, and this is
enforced rather than intended:** `assert_safe_output` refuses any path containing `state`, `learn*`,
`learning*`, `futures_ledger`, `entry_snapshot`, `position_path`, `coin_heads` or
`entry_selectivity`. Verified — `--out state/learn_v2.json` raises and the run dies.

---

## 6. Reproduction

```
python scripts/replay_fidelity.py       --snapshot <SNAP> --out data/verify_fidelity.json \
                                        --cache data/replay_bars --interval 1m --horizon 72 \
                                        --be-mfe-from 2026-09-09T11:24:45+00:00
python scripts/counterfactual_run.py    --snapshot <SNAP> --out data/cf_72h.json  --horizon 72 --data-end-ms 1788983501573
python scripts/counterfactual_run.py    --snapshot <SNAP> --out data/cf_48h.json  --horizon 48 --data-end-ms 1788983501573
python scripts/challenger_eval.py       --snapshot <SNAP> --out data/chal_72h.json --horizon 72 --data-end-ms 1788983501573
python scripts/challenger_eval.py       --snapshot <SNAP> --out data/chal_48h.json --horizon 48 --data-end-ms 1788983501573
python scripts/challenger_realised29.py --snapshot <SNAP> --out data/realised29_challengers.json
python scripts/challenger_verdict.py    --eval data/chal_72h.json --eval-48 data/chal_48h.json \
                                        --fidelity data/realised29_challengers.json --out data/verdict_v1.json
python scripts/shadow_stream.py         --snapshot <SNAP> --eval data/chal_72h.json --out data/shadow_stream_72h.jsonl
```

`data_end_ms = 1788983501573` (`/fapi/v1/time` at 2026-09-09T19:51:41Z). All bars and funding rates
are cached under `data/` (git-ignored); the runs above make **zero** network calls against a warm
cache. `<SNAP>` is the read-only production snapshot directory.

## 7. If someone asks "so what should we change?"

Nothing, on this evidence. That is the finding.

The one lever with a point estimate above the actionability floor (BE-at-MFE-1 R, +0.107 R/trade) is
already deployed. Of the three things measured that were not already deployed, two are harmful and
one is null. The residual problem — an entry model whose own scores have zero measured rank
correlation with forward return — cannot be fixed by a threshold on those scores, because §1.3
measured that there is nothing there to threshold.

The useful next step is not a new rule. It is **calendar time in the shadow stream**: the pipeline in
§5 now records everything needed to answer these questions properly, and the binding constraint is
that the entry-decision record is 7 days long and contains 14 acceptances.
