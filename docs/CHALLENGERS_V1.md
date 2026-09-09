# Pre-registration — Research C, challengers V1

**Written and committed BEFORE any challenger was evaluated.** Nothing below was chosen by looking
at a challenger result. Everything below was chosen by looking at the step-1 decomposition, which is
reported separately in `docs/RESEARCH_C_FINDINGS_V1.md`.

| item | value |
|---|---|
| branch / base | `work/research-c-v1` on `1c4cba1` |
| production snapshot (read-only) | `futures_ledger.json` sha256 `5cdb376ddc057c5d…`, `entry_snapshot.jsonl` sha256 `49969a1acc92b329…`, `config.yaml` sha256 `3103e20024f920ef…` |
| bar source | Binance USDⓈ-M `fapi` 1m klines + `fundingRate`, cached under `data/` |
| data end (`data_end_ms`) | `1788983501573` = 2026-09-09T19:51:41.573Z |
| accounting | production `FuturesLedgerV2` with `replay_config_from_ledger` (fees, slippage, `tp1_fraction=0.5`, `worst_case=true`, `breakeven_at_mfe_r=1.0`) — never re-implemented |
| intrabar ordering | production order: liquidation → **stop** → targets. When stop and target are both inside one 1m bar the **stop wins**. Pessimistic, unchanged in every arm. |

---

## 1. What the measurement said (step 1, abridged — full version in the findings doc)

1. **Realised expectancy is negative at the point estimate and NOT statistically separable from
   zero.** n=29 closed trades, mean −0.3567 R/trade, 95% CI **[−0.852, +0.139]**; win rate 7/29 =
   24.1%, Wilson 95% **[12.2%, 42.1%]**, which *contains* the 36.1% breakeven rate implied by the
   realised payoff ratio. **29 trades cannot settle whether this system loses money.**
2. **Cost drag is real but small**: fees+funding 0.0376 R/trade, slippage 0.0419 R/trade, total
   **0.0795 R/trade, 95% CI [0.054, 0.105]**. Removing every cost still leaves −0.277 R/trade.
   Cost is ~22% of the loss, not its cause.
3. **Giveback is small.** True 1m-resolution MFE of the 22 realised losers: mean **+0.443 R**; only
   **3/22** ever reached +1.0 R, only 6/22 reached +0.75 R, **0/22** reached +1.5 R. The losers did
   not squander profit — they never had any. Same shape in the 1334-candidate counterfactual pool:
   losers mean MFE +0.252 R, **44/636** reached +1.0 R, **0/636** reached +2.0 R; winners mean MFE
   +1.678 R, **518/698** reached +1.0 R.
4. **The entry model's own scores do not rank candidates.** Spearman rho against day-demeaned
   forward R over 1334 mature candidates (144 symbol×day clusters):
   `p_win` −0.109 [−0.257, +0.059]; `expected_r` −0.055 [−0.203, +0.102];
   **`conservative_net_edge_r` +0.032 [−0.136, +0.197]** — that last one is the variable the
   production economic gate is built on; `baseline_rank` −0.053 [−0.208, +0.107]. None significant,
   none with a usable point estimate, and three of the twenty predictors tested touched p≈0.05,
   which is what pure chance produces at twenty tests.
5. **The evaluable window is 5–6 market days and one of them dominates.** Taking every mature
   candidate returns +0.263 R/trade at a 72 h horizon, but the per-day means are −0.027, −0.002,
   **+0.824**, +0.195, +0.072. At a 48 h horizon the pool mean is +0.099, CI [−0.066, +0.264].
   97% of candidates are LONG, so a single market-wide up-day carries the whole level. **No
   statement about the level of edge is identified by this data.**

**Therefore:** challengers that add a new predictive signal cannot be justified — there is no
measured signal to build on, and the window is too short to find one. The challengers below attack
(a) the shape of the loss (a full −1 R paid for a trade whose favourable excursion was ~0.25 R) and
(b) the one claim the production architecture actually rests on (the economic gate variable).

---

## 2. The three challengers

All three run on the identical footing: same universe (every candidate in `entry_snapshot.jsonl`),
same window, same 1m bars, same accounting object, same pessimistic intrabar ordering, same
notional/leverage as recorded at decision time, same 72 h primary evaluation horizon with the same
mark-to-market horizon close. Only the stated rule changes.

### C1 — Dead-trade time box

> **Rule.** At the first 1m bar whose close is at or after `entry_time + 24 h`, if the position is
> still open **and** its maximum favourable excursion so far is below **+1.0 R**, close at market at
> that bar's close. Otherwise change nothing. MFE is computed only from bars at or before that
> instant — no look-ahead.

Constants are not fitted: 24 h is one round trading day and one third of the evaluation horizon;
+1.0 R is the value already in production config as `futures_v3.breakeven_at_mfe_r`.

*Why this attacks the measured problem:* 6.9% of counterfactual losers ever reach +1.0 R versus 74%
of winners, and 16 of the 22 realised losers were still alive after 24 h paying storage on a trade
whose mean favourable excursion was +0.44 R.

*Pre-registered prediction:* ΔR positive but **below +0.10 R/trade**, i.e. **fails**.

### C2 — First target at 1.0 R

> **Rule.** Replace target 1 with `entry ± 1.0 × |entry − initial_stop|` (direction-signed). Keep
> target 2 exactly as recorded. Keep `tp1_fraction = 0.5` and the existing move-stop-to-breakeven
> after TP1. Nothing else changes.
> **Exclusion:** if the recorded target 2 is not strictly beyond the 1.0 R level in the trade
> direction, the candidate is dropped from **both** arms of the C2 comparison and the number of
> exclusions is reported.

*Why this attacks the measured problem:* the realised payoff ratio demands a 36.1% hit rate and the
book delivers 24.1%. Moving the first partial from 2.0 R to 1.0 R trades average win size for hit
rate, using only geometry already in the system.

*Pre-registered prediction:* **|ΔR| < 0.10 R/trade, sign uncertain** — i.e. **fails**.

### C3 — Raise the production economic gate to the median of its own variable

> **Rule.** Accept only candidates with `conservative_net_edge_r ≥ τ`, where **τ = 0.5931**, the
> median of that variable over the mature evaluable pool at the 72 h horizon. τ is a statistic of
> the *predictor distribution only* and was computed without reference to any outcome.

*Why this attacks the measured problem:* it is the direct, falsifiable test of the assumption the
whole entry architecture rests on — that `conservative_net_edge_r` orders candidates by quality. If
raising this bar to the median does not improve the pool, that is evidence against this variable
carrying usable rank information IN THIS SAMPLE. It does not license the claim that no threshold on
this variable can ever work: this design has 5 market days, 144 clusters and no power calculation.

*Pre-registered prediction:* **ΔR ≈ 0** — a pre-registered null. **Fails.**

---

## 3. Metrics, acceptance criteria, and stopping rules

**Population.** The *mature evaluable pool*: candidates whose `ts + 72 h ≤ data_end_ms` and which
the production ledger can actually open. n = 1334, 95 symbols, 144 symbol×day clusters, 5 UTC days.
Excluded and reported, never hidden: 1173 immature candidates and 409 BTC/USDT candidates that the
venue cannot fill at the planned notional (Binance `BTCUSDT` `minQty` 0.001 ≈ 110 USDT, `minNotional`
50 USDT, versus a planned notional of 30 USDT).

**Primary effect measure.**
* C1, C2 (exit-side, same candidate set in both arms): **paired** `ΔR = R_challenger − R_baseline`
  per candidate. Pairing cancels the day effect exactly.
* C3 (selection-side, different subsets): difference in **day-demeaned** mean R between the passing
  subset and its complement, drawn from a **common** cluster resample.

**Uncertainty.** Cluster bootstrap, 5000 draws, clusters = `symbol × UTC day` (144). Symbol-only
(95) and day-only (5) clusterings are reported alongside. The day-only clustering has 5 blocks and
is stated as such rather than dressed up as an interval.

**A challenger is ACCEPTED only if ALL of the following hold.**

| id | criterion |
|---|---|
| A1 | point estimate ≥ **+0.10 R/trade** in the favourable direction |
| A2 | 95% cluster-bootstrap CI (symbol×day) excludes 0 |
| A3 | survives **Holm–Bonferroni at family-wise α = 0.05** across all three challengers |
| A4 | **leave-one-day-out**: the sign of the point estimate is preserved when each of the 5 UTC days is dropped in turn |
| A5 | same sign at the **48 h** horizon |
| A6 | *C1 and C2 only*: same sign on the **29 realised closed trades** replayed with the fidelity harness — an out-of-pool sample that predates the candidate window |
| A7 | *C3 only*: a **portfolio-constrained** replay (one shared 100 USDT ledger, position cap 3, chronological arrival, real margin competition) reproduces the same sign. The isolated per-candidate harness **cannot** settle a selection change; without A7, C3 is at most "per-candidate effect, portfolio effect unknown". |

**Underpowered rule.** Any cut with fewer than 15 observations **or** fewer than 15 clusters is
labelled UNDERPOWERED. This is a crude minimum-size screen, **not a power calculation**: a cut
that passes it is NOT thereby shown to be adequately powered, and `underpowered: false` in
`verdict_v1.json` means only that the cut cleared this screen. An underpowered cut may not support acceptance and its point estimate is not
reported as a finding.

**+0.10 R floor, justified.** Measured cost drag is 0.0795 R/trade and the fidelity harness's own
measured deviation on the 29 replayed trades is +0.332 R in aggregate (≈ +0.011 R/trade average, but
that figure is the aggregate of *those 29 trades under that harness* and is not a general
significance threshold). An effect below +0.10 R/trade is inside the range where cost modelling and
replay error can produce it, so it is not actionable even if its interval excludes zero.

**No changing the rules after the fact.** If a challenger fails, it is reported as failed. No
threshold, horizon, clustering, or population will be re-chosen to rescue it. Any variant explored
after seeing results is labelled EXPLORATORY and cannot be reported as a confirmed effect.

**What this design still cannot do,** stated in advance:
* It cannot establish a *level* of edge. Five market days with 97% LONG exposure is one market
  observation, not five.
* It cannot evaluate any rule needing a signal that did not exist at decision time. No LLM or agent
  opinion is generated for a historical bar anywhere in this work.
* The isolated harness reproduces exits, not portfolio dynamics. C1 and C2 respect that boundary by
  construction; C3 does not, which is why A7 exists.
