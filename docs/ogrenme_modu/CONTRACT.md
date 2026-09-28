# Learning mode (öğrenme modu), release L1: build contract

This contract sits on top of SPEC.md in the same folder. Where the two differ, this file wins.

- **Repo:** /home/user/trading2
- **Branch:** claude/gifted-knuth-0ehpcs, base HEAD 6662d5b
- **Mode:** PAPER only
- **User instruction (verbatim):** "bütün algoritmalardaki bir şeyin sınırı olmaması lazım … hepsinde mümkün olduğunca en fazla işleme girmesi lazım ki, botumuz öğrenebilen bir bot olsun"

## Hard rules for every implementer

- **Switch off means byte-identical behaviour.** With `learning_mode` absent or `enabled: false`, every code path must behave bit-for-bit as today. That covers decisions, reject codes, ledger JSON, summaries and the replay hash. Every learning branch is guarded as `if learning is not None and learning.on:`.
- **Never touch these:** the `PROFILES` constants, `risk_profiles.overrides`, the kill switch, and live/testnet execution gateways.
- **Never mutate the persisted ledger attribute `allow_shrink`.** Use the per-call override only.
- **Never add or remove a DATA_INTEGRITY or LAB_PARITY guard** unless this file says so explicitly. Guards that stay as they are:
  - DUPLICATE_SIGNAL / SIGNAL_ALREADY_USED / PATTERN_ALREADY_USED
  - DATA_* / BAD_STOP / UNRESOLVED_PRECISION
  - RISK_OUTSIDE_TESTED_RANGE
  - the 60-min entry windows
  - one position per symbol per book
  - the exchange filters in the ledger
- **Comments** follow the repo's existing style: Turkish, brief, dated "(2026-09-28, öğrenme modu)".
- **Do not commit or push.** The orchestrator commits.
- **Do not edit files outside your stage's file list.** If you need a foundation change, stop and report it in your output (field `needs_from_other_stage`).
- **Never write** state/, .env, keys or raw production data into the repo.

## Decisions (resolved; do not re-ask)

| Item | Decision |
|---|---|
| Risk target per trade | `risk_per_trade_pct` = 0.5% of the equity basis (`risk.equity_basis(state)`, i.e. starting equity in PAPER_RESEARCH). The learning RiskEngine profile keeps `risk_per_trade_pct` = 2.0 as the hard cap, which is what allows the min-notional bump. |
| Learning RiskEngine profile | `profile_for(base, main=False)` = `dataclasses.replace(base, max_total_open_risk_pct=100.0)`, and for main also `max_spot_allocation_pct=100.0`. Never registered in `PROFILES`. |
| Sizing | `fit_size`, exactly as SPEC §3: slots K per book, reserve 5%, liquidation buffer 2.0 × stop, `L_max` per book, min-notional bump (qty rounded UP to step) only within the 2% cap and available margin. `ledger.open(..., allow_shrink=True)` is a backstop only. Record `size_rule` ∈ {SLOT, BUMP_MIN_NOTIONAL, SHRUNK_TO_MARGIN} in `pos.meta["learning"]` and `features["learning"]`. |
| S1 | Main regime gate: ENFORCE → SHADOW while learning is active. |
| S2 | Main candle veto: ENFORCE → SHADOW while learning is active. |
| S3 / S4 | Structures for main, T2 and M2: **entries** SHADOW while learning is active (no WAIT / WAIT_TRIGGER / entry CANCEL / entry-geometry rewrite; the rule's own entry and stop are used). Management (structure exits, stop tightening) stays as configured (ENFORCE). If entry and management cannot be separated cleanly for a bot, stop and report; do not silently go full SHADOW. |
| S5 | Economics gate: RESEARCH_SIZE_ONLY and NEGATIVE_NET_EDGE open as exploration trades, tagged `exploration=RESEARCH_SIZE` or `NEG_EDGE`. `short_penalty_r` and `futures_only_penalty_r` are recorded as features and not applied. ZERO_STOP_DISTANCE and UNKNOWN_GATE_CODE stay. |
| S7 | Leverage CONFIDENCE_BELOW_BASE falls back to 2x (tag `LEARNING_MIN_LEVERAGE_FALLBACK`). |
| B1 | Leverage fallback set, per SPEC. |
| S9 | Formasyon entry universe = protocol 30 ∪ main entry universe (40), tagged `in_lab_universe`. |
| C9 | D4, C4 and C4S symbols = book list ∪ main entry universe, tagged `in_lab_universe`. |
| C1 / S16 | Box `min_stop_pct` = 0.32 while learning is active. |
| C10 | C4: `also_matched` variations and variations blocked by an open position produce counterfactual records. No book split. |
| C11 | C4S: learning `enabled: false` (it stays the strict control). |
| D1, D2/D3, D5, D6, D7, D16, D17, D18 | As SPEC. |
| Deferred to a later release (do NOT implement) | B7 per-timeframe provenance, S10 universe growth, S13 own timer, S14 new equity dirs, S15 influence, S6 t1 book, S8 NO_TRIGGER, replay `--learning-mode` flag. Replay must keep passing `learning=None` (bit-identical). |
| P0 | `learn/decision_journal.classify_outcome`: ACCEPTED only after a successful fill. EXCHANGE_REJECTED becomes OPEN_FAILED (add the class if missing; check all readers). Persist `exec_reject`. |
| P1 | The counterfactual `plan_id` is the per-bar signal key, never a run_id-derived id. |
| P2 | `FuturesLedgerV2.open(..., allow_shrink: bool | None = None)`: `None` uses the attribute (unchanged); a bool overrides for this call only; nothing is persisted. |

### Additions from the second verification round (2026-09-28)

All of these apply only while learning is active; the OFF path is unchanged.

| Item | Rule |
|---|---|
| Baseline view | `learning_unlocked_by` is computed against `learning_mode.baseline_view(state, …)`, not the learning book. The view drops open positions tagged learning-extra and counts policy positions at their recorded `baseline_size` (floored qty × fill, leverage). It is approximate: trades the baseline would hold but the learning book never opened, and the P&L difference, are unknown. Policy opens record `meta.learning.baseline_size`. |
| Policy reserve | Candidates tagged learning-extra (non-empty `learning_unlocked_by`, computed before sizing) see free margin reduced by `policy_reserve_usdt` = min(2 slots × (1 − reserve) × E / K, 10% × E). Policy-grade candidates use the full free margin. The slot size and risk rules are unchanged. The fit record carries `policy_grade` and `policy_reserve_usdt`. |
| A15 held symbols | Box and D4: a fresh entry signal of the **baseline** rule (the book's own `rule_params`; for Box the baseline `min_stop_pct`) on a symbol the book already holds is recorded as a `POSITION_OPEN` counterfactual. C4 already did this, and Formasyon D8 is unchanged. Learning-parameter signals (Box 0.32) on held symbols are not recorded: in the 24h end-to-end run that path produced about 511 Box records per day, against about 47 baseline-grade ones. T2/M2 are excluded because their entry is a state predicate, true on every bar of a held position. |
| F7 Formasyon | A D8 `POSITION_OPEN` counterfactual is superseded when the same plan id later opens for real (counter `counterfactual_superseded`). |
| Main risk tags | `risk_usdt` / `risk_fraction_of_budget` come from the filled position (qty × \|fill − stop\|); the fit value is kept in `risk_usdt_fit`. |
| Counter backup | Book learning counters are also kept in `counterfactual_trades.json` `meta.book_counters` and merged by max when the recorder is built. Neither the OFF path nor the old code writes that file. |
| Shadow tag backup | The main `ShadowBook` keeps the optional fields of tagged rows, plus `meta`, in `state/shadow_book_learning_tags.json`, keyed by row id. The file is written only when tagged rows or meta exist. On load, rows whose tags the old code stripped get them back. |
| Experience cache | The engine caches `learn.experience.experience_row(row)`: a projection without `decision` / `chief` / `snapshot` / `risk_decision` / `model_versions`, and with postmortem reduced to `lesson_codes`. The pool is identical. `health.learning_mode.memory` = {rss_mb, hwm_mb, exp_cache_rows}. |
| Rollback | Before the old code starts, run `scripts/learning_mode_rollback_prep.py --state <state>` with the worker stopped. It cancels pending Formasyon plans that are outside the protocol universe or in a liquidity wait. |

## Interfaces (fixed; stage A creates them, later stages consume them)

`tradingbot/learning_mode.py`: pure module, stdlib plus `risk.profiles` only.

```python
BOOK_NAMES = ("main", "t2_trend_regime", "m2_tsmom28", "b1_box_fade", "d4_donchian_20_10",
              "c4_candle_variations", "c4s_candle_variations_strict", "pattern_trader")
OVERRIDE_KEYS = ("regime_gate_shadow", "candle_veto_shadow", "structures_entry_shadow",
                 "economics_exploration", "leverage_confidence_fallback")
# structures_entry_shadow: list of bot names, e.g. [main, t2_trend_regime, m2_tsmom28]; the others are bool

@dataclass(frozen=True)
class BookLearningCfg:
    enabled: bool = False
    slots: int = 20
    leverage_max: int = 3
    min_stop_pct: float | None = None
    symbols: str | None = None   # None | "universe"

@dataclass(frozen=True)
class BookLearning:              # what a book receives for ONE pass; immutable
    on: bool
    name: str
    slots: int
    leverage_max: int
    risk_pct: float              # 0.5
    reserve_pct: float           # 5
    liq_buffer_mult: float       # 2.0
    min_notional_bump: bool
    counterfactual: bool
    max_pending: int
    min_stop_pct: float | None
    symbols: str | None
    hard_cap_pct: float = 2.0

class LearningMode:
    def __init__(self, section, *, mode_gate: Callable[[], tuple[bool, str]]): ...
    @classmethod
    def from_config(cls, cfg, mode_gate) -> "LearningMode": ...  # section missing → disabled instance
    enabled: bool                # config flag
    def active(self) -> tuple[bool, str]: ...                    # enabled AND mode_gate OK; else (False, reason)
    def refresh(self) -> bool: ...                               # re-evaluate and cache (one call per pass)
    @property
    def on(self) -> bool: ...                                    # cached result of the last refresh()
    def status(self) -> dict: ...                                # {enabled, active, reason, since}
    def book(self, name) -> BookLearning | None: ...             # None unless on AND book enabled
    def override(self, key, default): ...                        # returns default unless on
    def structures_entry_shadow(self, bot) -> bool: ...

def profile_for(base, *, main: bool = False): ...
def fit_size(*, equity, entry, stop, slots, leverage_max, risk_pct, reserve_pct=5.0, liq_buffer_mult=2.0,
             mmr=0.004, min_notional=5.0, qty_step=None, price_for_step=None, hard_cap_pct=2.0,
             min_notional_bump=True, available_margin=None) -> FitResult
    # FitResult(ok, notional, leverage, margin, risk_usdt, size_rule, reason, detail: dict)
def leverage_fallback(base_failures: Iterable[str], *, stop_atr: float | None, allow_confidence: bool) -> int | None
COUNTERFACTUAL_OK: frozenset[str]
COUNTERFACTUAL_NEVER: frozenset[str]
def counterfactual_ok(reason: str) -> bool   # prefix-aware: STRUCTURE:*, CANDLE_VETO*, REGIME_VETO*
```

`tradingbot/learning_cf.py`: counterfactual recorder, one per book.

```python
class CounterfactualRecorder:
    def __init__(self, path: Path, *, book: str, max_pending: int = 2000, archive=None): ...  # wraps learn.shadow.ShadowBook
    def record(self, *, signal_key: str, symbol: str, direction: str, entry: float, stop: float, targets: list[float],
               reason: str, created_at: datetime, tf_minutes: int, horizon_bars: int, label_kind: str,
               features: dict | None = None, variation: str | None = None, rule_version: str | None = None) -> bool
        # Returns False (no record) for: invalid geometry, reason not counterfactual_ok, duplicate (book, signal_key, symbol, direction, variation).
        # Only the as_planned variant is used.
    def label_pending(self, frames_by_symbol: dict[str, dict[str, Any]], now: datetime) -> int
        # frames_by_symbol[sym][tf] may be a DataFrame (timestamp column or datetime index) or a list of dict rows.
        # Only bars that are CLOSED at `now` are used (open + tf <= now). Stop first, then target (label_with_candles).
        # For TARGET_STOP_TIME the horizon is the rule's max hold; for HORIZON it is 30 bars, flagged approx=True.
    def save(self) -> None
    def stats(self) -> dict        # pending, labeled, dropped, recorded_total
```

`learn/shadow.ShadowTrade` gains optional fields, all defaulted so old files still load:
- `book`
- `signal_key`
- `variation`
- `label_kind`
- `features`
- `learning_unlocked`
- `rule_version`
- `approx`

Existing main behaviour of ShadowBook stays byte-identical when these are unset. Check `to_dict` consumers and tests.

`strategy_paper.apply_action(..., learning: BookLearning | None = None)`:
- **With `None`:** bit-identical to today.
- **With a BookLearning:**
  - size with `fit_size`: equity = `risk.equity_basis(state)`, `min_notional` from `filters.min_notional`, `qty_step` from `filters.qty_step`;
  - leverage = `FitResult.leverage`;
  - call `risk.evaluate` using the learning RiskEngine that the caller passed as `risk`;
  - call `ledger.open(..., allow_shrink=True)`;
  - write `pos.meta["learning"] = {size_rule, slots, risk_pct, risk_fraction_of_budget, learning_unlocked_by: [...]}`.
- **On REJECTED,** the caller (book) writes the counterfactual through its recorder. apply_action exposes the act geometry and the reject reason to the caller.

`StrategyBook.step(..., learning: BookLearning | None = None)`: the engine passes it. The book lazily builds `self.risk_learning` and `self.cf` (CounterfactualRecorder at `state_dir/counterfactual_trades.json`).

Config (`config_v3.LearningModeSection`, top-level key `learning_mode`):
- The shape follows SPEC §2.1, with `strategy_overrides` keys limited to OVERRIDE_KEYS.
- Validation errors are ConfigError (SPEC §2.1 list), including unknown keys under `learning_mode`.
- Code default: `enabled: false`.

## config.yaml values for L1 (stage C writes them)

```yaml
learning_mode:
  enabled: true
  risk_per_trade_pct: 0.5
  max_total_open_risk_pct: 100
  margin_reserve_pct: 5
  liq_buffer_mult: 2.0
  min_notional_bump: true
  counterfactual: true
  counterfactual_max_pending: 2000
  books:
    main:                         {enabled: true, slots: 20, leverage_max: 5}   # 2026-09-28: kademe tavanı (6fb39cd)
    t2_trend_regime:              {enabled: true, slots: 40, leverage_max: 4}
    m2_tsmom28:                   {enabled: true, slots: 40, leverage_max: 4}
    b1_box_fade:                  {enabled: true, slots: 20, leverage_max: 4, min_stop_pct: 0.32}
    d4_donchian_20_10:            {enabled: true, slots: 20, leverage_max: 3, symbols: universe}
    c4_candle_variations:         {enabled: true, slots: 20, leverage_max: 3, symbols: universe}
    c4s_candle_variations_strict: {enabled: false}
    pattern_trader:               {enabled: true, slots: 30, leverage_max: 3}
  strategy_overrides:
    regime_gate_shadow: true
    candle_veto_shadow: true
    structures_entry_shadow: [main, t2_trend_regime, m2_tsmom28]
    economics_exploration: true
    leverage_confidence_fallback: true
```

## Test commands

- Targeted: `python -m pytest -q tests/<file>`.
- CI list: the `pytest` invocation in `.github/workflows/chart-analysis.yml` (lines ~46-94).
- Full suite: `python -m pytest -q -p no:cacheprovider tests`. The `test_deploy_vps` "Remote branch HEAD not found" failures are only an environment artifact inside detached worktrees.
- Lint: `ruff check tradingbot tests scripts`.
