# Shared Experience Layer ("Ortak Deneyim Katmanı"), Phase 1: implementation spec v1

- **Base:** branch `claude/gifted-knuth-0ehpcs`, HEAD `2a7b03a` (code `c0b8c94`, live on the PAPER VPS since 2026-09-28 21:20 UTC, learning mode L1 active).
- **Supersedes:** the Phase-1 plan written against `6662d5b` (before learning mode). Section 2 lists every anchor from that plan re-validated against HEAD.
- **Nature:** design only. No repo file was edited. Nothing is committed.
- **User question the layer must answer:** "In this situation (trend / volatility / BTC / volume / structure), for this setup, did we win or lose before?" The answer comes with n, win rate, mean **net** R, a confidence interval and INSUFFICIENT_SAMPLE, shown separately for real trades and counterfactuals.

---

## 0. Summary of the design, and what changed since the 6662d5b plan

| Topic | 6662d5b plan | This spec (HEAD) | Why |
|---|---|---|---|
| Row kinds | entry / skip / outcome | **xp_entry / xp_outcome / xp_cf** (counterfactual is first-class). **No skip rows.** | Learning mode now writes a labelled counterfactual for every valid signal that was not taken and whose reason is in `COUNTERFACTUAL_OK` (`learning_mode.py:108`). Every other reject (DATA_*, DUPLICATE, SIGNAL_ALREADY_USED, NO_TRIGGER, BAD_STOP) is by contract not an observation. Skip rows without outcomes cannot answer "did we win or lose". |
| Decision-path hooks | H3–H6 in `strategy_paper.py` (skip deque) | **None.** `strategy_paper.py`, `pattern_trader/*`, `box_timer.py`, `learning_cf.py`, `learn/shadow.py` and `learning_mode.py` are untouched. | Counterfactual recorders already hold what the skip hooks would have captured, with geometry and labels. |
| Engine hooks | init after :386, tour after :1756 | **One call after `engine_v3.py:1835`** plus one health line before `:1884`. The collector is built lazily on the first RECORD step, so `__init__` needs no hook. | Smaller footprint. With the section OFF the call is a single attribute read and return. |
| Learning tags | n/a | Every real row carries a projection of `features.learning` (`size_rule`, `learning_unlocked_by`, `exploration`, `policy_basis`, `leverage_fallback`, …) plus `in_lab_universe`, and a **cohort** computed with the same rule as `scripts/bot_scorecard.py:183-187, 223-229`. | Requirement (a). The report's cohorts match the dashboard and the release `--check` scorecard exactly. |
| R | `r_net = rec.r_multiple` for real trades | Real rows: `r_net`, `r_gross`, `cost_r` (+ `fee_r`, `funding_r`, `slippage_r`). CF rows: `r_gross` / `cost_r` / `r_net` / `label_version` taken from the net-R fix. Legacy CF labels (no `label_version`) are recorded with `r_net = null`, `r_basis = GROSS_LEGACY`, and are **never mixed** into net statistics. | Requirement (b). Live Box shows why: labelled CF mean is +0.40R gross, but at the learning floor stop of 0.32% the round-trip cost (2 × 0.05% taker + 2 × 3 bps slippage = 0.16%) is **0.5R**, so gross CF R and net real R (−0.57R) cannot be compared. |
| Snapshot completeness | closed-bar rule only | Closed-bar rule **plus a fetch-time rule**: a bar is usable only if it had closed before its frame was fetched. Otherwise the snapshot is deferred (PENDING) to a later tour. | Box and Formasyon events happen between tours. A 4h bar that closed after the tour fetched its frames is present in that frame as a **partial** (forming) row. Using it would be neither causal nor deterministic. |
| Reading counterfactual sources | n/a | Read **in memory** (`book.cf.sb.trades`, `engine.shadow.trades`) under the owner's lock, copying only new or changed records. The JSON files are never parsed in the worker. | The Box file grows by about 1,200 records a day. Parsing it every tour would cost about 40 MB of transient memory and roughly 100 ms. Scanning 5,000 in-memory records costs 0.8 ms (measured, §12). |
| Cursors | join index of up to 45k keys | O(new) ledger cursors (last key plus position), a bounded CF id set, and a persisted `cursor.json` of about 1 MB. | Memory budget (§12). |
| Store writes | one fsync per row (DecisionJournal) | `ExperienceStore(DecisionJournal)` with a **batched** `append_rows` (one open and one fsync per step). Rotation, archive and `load_seen` are inherited. | A first-start backfill of about 2–3k rows would otherwise exceed the time budget. |
| Hot file | 10,000 lines | **5,000 lines** (about 1.7 days, about 5.5 MB) | Rotation reads the hot file whole (`decision_journal.py:519-523`). This keeps the rotation peak around 20 MB. |
| Report | per book × bucket | **Question-shaped** (setup + situation → real and CF answers), with a pre-registered backoff when a cell is too small, the scorecard's verdict labels, and an iid bootstrap CI plus a symbol×day cluster CI. | Requirement (f). |

**What stays the same:** a read-only collector at tour end; `situation_v1` as a pure function of `(symbol, as_of, closed 4h/1h/BTC-4h bars)` with W = 200 ≤ 240; LRU cache per bar; package name `shared_experience` (never `learn/experience*`); OFF by default in code; env off-switch only; lossless rotating archive; `INSUFFICIENT_SAMPLE`.

---

## 1. Scope and hard rules

1. **RECORD only.** The layer never writes to a ledger, position, plan, counterfactual recorder, shadow book, learner, journal, summary, `features` or `meta`. It never calls `save`, `open`, `close_manual`, `tick`, `record`, `supersede`, `label_pending`, `settle_late_funding`, `apply_action`, `RiskEngine.*`, or `ShadowBook.add/label`. An AST guard test enforces this (§13, T6).
2. **OFF is byte-identical.** With `shared_experience` absent or `mode: OFF`, every state file, `health.json`, ledger JSON, CF file, journal row and replay hash is bit-for-bit what `c0b8c94` writes. That includes learning mode ON and OFF.
3. **RECORD is decision-identical.** With `mode: RECORD`, every decision artefact is identical to OFF: ledgers, CF files, `risk.json`, decision journal, summaries, plans, funnel and `signals_seen.json`. The only differences allowed are `state/shared_experience/**` and the `health.json["shared_experience"]` key.
4. **Runtime gate.** Rows are written only while `mode_state.mode == PAPER`. In any other mode the status is `SUSPENDED:<mode>` and nothing is written.
5. **No network.** Bars come only from tour frames (`runner.last_frames`, USDM_PERP provenance) and Formasyon's closed-bar CSV cache (`CsvCandleCache`). There is no lazy fetch knob in v1.
6. **Isolation.**
   - Only `engine_v3.py` and `cli_v3.py` import `tradingbot.shared_experience`.
   - Nothing under `tradingbot/learn/`, `learning_mode.py` or `learning_basis.py` reads the store. In particular, `_prepared_experience_pool` (`engine_v3.py:4316`), which feeds the p_win influence path, never reads it.
7. **Replay** never runs the collector: `HistoricalReplay` does not call `TradingEngineV3.tour`.
8. **Out of scope for v1:** SPOT trades (main `spot2` ledger) and SPOT counterfactuals (`market_type == "SPOT"`), dashboard UI, any ADVISE/ENFORCE mode, and taker-volume features.

---

## 2. Anchor re-validation (6662d5b plan → HEAD `2a7b03a`)

| Plan cite (6662d5b) | HEAD | Status |
|---|---|---|
| `engine_v3.py:386` end of Formasyon init (H1) | PatternBook block `engine_v3.py:420-432`; ShadowBook `:453-454` | **Hook dropped.** The collector is lazy (§10). |
| `engine_v3.py:1756` `_journal_decisions(risk_log, decisions, now)` (H2) | **`engine_v3.py:1835`** | Hook moved here. |
| main entries `:1605`; closes `:1653-1700`; `_strategy_paper_tour :1672`; `_pattern_trader_tour :1753` | `_execute` call `:1682` (def `:1907`, `_execute_locked :1916`); tour tick under `_ledger_lock` `:1728-1747`; `_strategy_paper_tour(...)` `:1749`; `_pattern_trader_tour(now)` `:1832` | All before `:1835`. |
| protective queue `:976-1034`; `exit_check :1062-1092`; close-chain repair `:1704` | `drain_protective_closes` def `:1058`, called `:1777`; `exit_check` def `:1128`; `_complete_close_chain()` `:1783` (def `:4728`); `_label_shadows()` `:1785` → `_lm_label_main_cf` `:3521` (def `:4061`) | All close/label paths write the ledger or ShadowBook **before** `:1835`. Monitor-thread closes are covered because the collector reads `ledger2.history`. |
| `_ledger_lock` `:878` | property `engine_v3.py:941-948` (RLock `_exit_lock`) | Try-acquire with timeout. |
| `_frame_provenance` `:1459-1470`; SPOT substitute `:1467` | `:1526-1541` (USDM_PERP `:1528`, SPOT `:1536-1539`); `_bind_provenance` `:1219-1240` | `market == "USDM_PERP"` is required for tour-frame bars. |
| risk_log structure `:1919-1924` | `entry = {...}` `:2015-2020`; early `risk_log.append` at `:1978-2012` | **Not used for skips.** Used only to map opened `trade_id` → signal key. |
| `_signal_id` `:3366-3373` | **`:3763-3770`** (pure `stable_id("signal", sym, market, "4h", b.last_bar_4h, d.direction, plan.entry_type)`) | Recomputed read-only for main real trades (§5.4). |
| `code_sha/config_hash` `:3780-3815` | `code_sha()` `:4655`, `config_hash()` `:4676` | Reused for the row envelope. |
| `_prepared_experience_pool` `:3450` | `:4316` (reads `shadow_book.json` rows at `:4336`) | Must never read our store (AST test). |
| observer injection `:1135-1138` | `:1200-1204` | Not used. |
| `strategy_paper.apply_action :343-476` | `:611-723` (`_open_learning :499-608`) | Not hooked. |
| `StrategyBook.__init__` observer `~541` | `observer :788`, `cf :795`, `lock :759` | Read-only: `book.lock`, `book.ledger`, `book.cf`. |
| H4 `:832-836`, H5 `:786-791`, H6 `:837` | `apply_action` call `:1136-1140`; data-reject branch `:1071-1080`; `_learning_after :1328` | **All dropped** (no skip hooks). |
| `_reject :584-590` | `:844-850` | n/a |
| `expected_last_closed_open :146`; `frame_freshness :152` | **`:154`**; **`:160`** | Reused by `bars.py` (completeness). |
| `_on_closed :654-668`; memory `setup_type "trend"` `:707` | `:914-928`; **`:980`** | Still thin. Not used; the ledger is the source. |
| `reconcile_funding :627-652` | `:887-912` | Late funding produces outcome revisions. |
| `futures_ledger._finalize :520-564` (risk_usdt `:556`) | **`:531-575`** (`features["risk_usdt"]` `:567`, history trim `:572-574`) | `history_keep` default 5000 (`:208`, `:240`). |
| id `F%05d` `:377-378` | **`:386-387`** | The trade key includes `opened_at`. |
| MFE/MAE pct `:587-590` | `:600-601` | |
| `settle_late_funding :777+` | **`:772`** (window 500) | |
| `TradeRecord models.py:457-499` | same | **No `meta` on TradeRecord.** Tags must come from `features`. |
| `DecisionJournal :333-578` | **`:350-~660`** (`_write_line :386`, `load_seen :501`, `_read_lines :519`, `rotate :550`); `KIND_OUTCOME = "outcome_link"` `:36` | Our kinds must never equal `"decision"` or `"outcome_link"`. |
| `SegmentArchive journal_archive.py:145` | `:145` | Reused. |
| `config_v3 StructuresSection :700-720`; `_SECTIONS :757-770`; `validate_v3 :805`; env `:790-800` | `StructuresSection :702-721`; **`LearningModeSection :724-741`**; `V3Config.learning_mode :775`; `_SECTIONS :779-793`; unknown-key check `:804-814`; env `TRADINGBOT_LEARNING_MODE :837-843`; `validate_v3 :848`; `_validate_learning_mode(...)` call `:1163`, def `:1174` | New section follows the learning_mode pattern (§9). |
| `cli_v3.register :1091`, `learning-status :1213` | same (`learning-reconcile :1214-1222`) | |
| `pattern_trader/book.py` lock `:90`; `_set_status :211-227`; `_on_closed :308`; sibling logic `:222` | lock **`:104`**; `_set_status :257-273` (sibling `:268`); `_on_closed :354-402`; `cf :154`; `_cf_recorder :1282`; `_cf_record :1321-1357`; D8 supersede `:1163-1172`; `pos.features` plan_id/family `:1142-1143`; `in_lab_universe :1162` | Read-only adapter. |
| `pattern_trader/data.py:28-30 BARS_PER_TF`; `CsvCandleCache :42-92`; `bars_from_df :95` | `BARS_PER_TF :30` (240 each for 15m/1h/4h); `CsvCandleCache :42-94` (keeps `max(240×3, 500)` = 720 closed bars per timeframe); `bars_from_df :97` | **W ≤ 240 still binds.** |
| `candle_confirmation.py:39-54` | `closed_bars :39-54` (`ts + step <= now_ms`) | Same closed rule. |
| `learn/multitimeframe_context.confirmed_swings :358` | `:358-378` | Vendored (§4.4). |
| `signal_lab.context :301`, `signal_lab.indicators :280` | `signal_lab.py:285-306` (`context`; vol edges `:298`, RSI `:299`, volatility `:301`); `indicators :267-282` (`vol_avg` prior 20 at `:280`, `atr_med` 200/50 at `:281`) | Edges reused. |
| `engine.py:58` frame sizes; `:75-76` 6 columns | `PERP_FRAME_LIMITS :58` (1d 400 / 4h 700 / 1h 500 / 5m 500 / 15m 500); `fetch_ohlcv` 6 columns `:75-76` | Coverage: 4h ≈ 116 days, 1h ≈ 20.8 days. |
| `market/providers.py:23-68 taker_buy_base` | `:4, :24-25, :67-68` | Still dropped by engine frames and CSV. Taker features deferred. |
| `box_timer.py:176-201` | `_evaluate :191-250` (frames only 1d + 5m `:203-210`; `with self.book.lock:` `:225-231`, holding step + closed bars + tick + save) | Snapshot must come from the collector. Box lock can be held for seconds. |
| `ops/backup.py:68-90` | `rglob :72`; retention 24/7/4/10 | Hot file bounded. |
| `dashboard/app.py:2661` config_hash compare | `:2718` | Label only. |
| release invariants `tb-deploy-7ad8832.sh:302-338` | **`tb-deploy-c0b8c94.sh`**: checks list `:861-898`, config python `:747-860`, `--check` block `:218-490` (rollback triggers `:420-488`), `memmax_check :144`, `memory_report :132` | New release script extends these. |

**New learning-mode facts the layer depends on (all verified at HEAD):**

- **Main learning tags** go to `feats["learning"]` (and `feats["exploration"]`) at `engine_v3.py:2400-2419`, and are finalised after the fill by `_lm_after_open` (`:4212`, written at `:4244`, re-copied at `:2480`). `TradeRecord.features` keeps them (`futures_ledger.py:553`: `features=dict(pos.features)`).
- **Book learning tags:** `feats["learning"] = lrn` and `feats["in_lab_universe"]` in `strategy_paper._open_learning` `:571-573`, updated with realised `risk_usdt` / `risk_fraction_of_budget` at `:596-605`.
- **Formasyon learning tags:** `res.pos.features["learning"]` `book.py:1477`; `in_lab_universe :1162`.
- **Main counterfactuals** are `ShadowTrade` rows in `engine.shadow` with `book == "main"`, `signal_key`, `label_kind`, `features`, `learning_unlocked=False` (`engine_v3.py:3972-4012`). They are labelled by `_lm_label_main_cf` (`:4061`), capped by `_lm_cf_cap` (`:4033`), superseded by `_lm_cf_supersede` (`:4044`), and forgotten through `_lm_cf_forget` (`:4026`). Legacy shadows (`book is None`) are ignored.
- **Book counterfactuals:** `CounterfactualRecorder` (`learning_cf.py:232`) → `ShadowBook(state_dir/"counterfactual_trades.json", archive=SegmentArchive(...))`.
  - Ids are deterministic: `"cf_" + stable_id("cf", book, signal_key, symbol, side, variation)`.
  - Lists are mutated by `_enforce_cap`, `supersede` and `label_pending` (stale removal), and outcomes are set in place by `label_records` (`:178-229`).
  - The active list is bounded at `ShadowBook.MAX_TRADES = 5000` (`learn/shadow.py:152`), with oldest records archived first (`:188-206`). The archive is on by default (`LearningV3Section.decision_archive_enabled = True`, `config_v3.py:293`).
- **Signal keys:**
  - books: `_signal_key(act)` = `"signal_ts:<ms>"` (`strategy_paper.py:384-395`);
  - Formasyon: `plan_id` (`book.py:1167, 1346`);
  - main: `_signal_id` (`engine_v3.py:3763`).

---

## 3. Architecture

```
tour() … 1832 _pattern_trader_tour → 1835 _journal_decisions → [NEW] _shared_experience_step(risk_log, decisions, briefs, now)
                                                                   │  (main thread; nothing below writes outside state/shared_experience)
             ┌─────────────────────────────────────────────────────┴──────────────────────────────────────┐
   adapters.py (copy-only, try-lock)                                                                bars.py (no network)
   MainAdapter      ledger2 (+_ledger_lock), shadow(book=="main")                                   tour frames (USDM_PERP only, fetch_lb = _tour_now_ms)
   StrategyAdapter  book.ledger, book.cf.sb (book.lock)   × T2 M2 Box D4 C4 C4S                     CsvCandleCache (closed-only)
   PatternAdapter   pattern_book.ledger, pattern_book.cf.sb (book.lock)                              │
             └──────────────► collector.py ◄──────── situation.py (pure) ◄── cache.py (LRU per bar) ─┘
                               │ rows.py (pure builders) → store.py (ExperienceStore: batched append, lossless rotation)
                               └ cursor.json (O(new) cursors), status.json, health key
report.py (CLI process; streams store; no engine, no network, no writes except --out)
```

### 3.1 New files

| File | Content and signatures |
|---|---|
| `tradingbot/shared_experience/__init__.py` | `ROW_SCHEMA = "shared_experience_row_v1"`, `KINDS = ("xp_entry", "xp_outcome", "xp_cf")`, `LAYER_VERSION = "1.0.0"`. No other imports, so the OFF path loads nothing. |
| `…/situation.py` (pure: stdlib + numpy; no imports from books, risk or ledger) | `SCHEMA_ID = "situation_v1"`; `W4H = W1H = WBTC = 200`; `SCHEMA_SPEC: dict`; `SCHEMA_SHA: str` (16 hex, pinned by test)<br>`@dataclass(frozen=True) class Window: tf: str; ts: np.ndarray[int64]; o,h,l,c,v: np.ndarray[float64]; complete: bool; last_open_ms: int\|None; n: int; source: str`<br>`def normalize_rows(src, tf: str, as_of_ms: int, *, W: int, fetch_lb_ms: int\|None = None, source: str = "?") -> Window`: accepts a DataFrame with a `timestamp` column or a dt index (tour frames / lab), or a list of dict rows (Formasyon `DataService`, `bars_from_df`). Applies the rules in §4.1.<br>`def compute_core(h4: Window, h1: Window, btc: Window, *, is_btc: bool) -> dict` (bar-derived, side-agnostic, cacheable)<br>`def calendar(as_of_ms: int) -> dict`<br>`def input_sha(h4, h1, btc) -> str`<br>`def snapshot(symbol, h4, h1, btc, as_of_ms) -> dict` (= provenance + core + calendar + status/missing)<br>`def bucket(snap: dict, side: str) -> dict` (§4.5)<br>Private helpers: `_atr_wilder`, `_ema_sma_seed`, `_rsi_wilder`, `_er`, `_confirmed_swings` (vendored copy of `multitimeframe_context.py:358-378`), `_volume_profile`, `_pearson_logret`. |
| `…/bars.py` | `class BarSource: __init__(self, *, frames: Mapping[str, dict], provenance: Mapping[str, dict], tour_now_ms: int, csv_dir: Path)`<br>`def window(self, symbol: str, tf: str, *, as_of_ms: int, W: int) -> Window`: tries the tour frame first (only if `provenance[sym]["market"] == "USDM_PERP"`; `fetch_lb = tour_now_ms`), then `CsvCandleCache(csv_dir).read(sym, tf)` (`fetch_lb = None`, closed-only), and returns the first **complete** window; otherwise the best incomplete one.<br>Memoises CSV reads per step (per `(sym, tf)`; ≤ 720 rows). The CSV class is imported from `pattern_trader.data` (read-only use). |
| `…/cache.py` | `class SituationCache: __init__(self, max_entries: int = 2048)`; `get(key) -> dict \| None`; `put(key, core)`; `stats()`. OrderedDict LRU, main thread only. Key = `(symbol, h4.last_open_ms, h1.last_open_ms, btc.last_open_ms, h4.n, h1.n, btc.n)`. Only complete windows are cached. |
| `…/rows.py` (pure) | `row_id(kind, book, src_id, rev) -> str` (`core.stable_id`, 16 hex)<br>`trade_key(book, trade_id, opened_at) -> str`, `cf_key(book, cf_id) -> str`, `join_key(book, symbol, side, signal_key, variation) -> str \| None`<br>`entry_row(src: EntryFacts, *, snap, snap_status, snap_meta, origin, env) -> dict`<br>`outcome_row(src: ClosedFacts, *, rev, final, env) -> dict`<br>`cf_row(src: CfFacts, *, rev, status, snap=None, snap_status=None, snap_meta=None, origin, env) -> dict`<br>`real_r(closed: ClosedFacts) -> dict` (§5.3)<br>`cf_r(outcome: dict, *, entry, stop) -> dict` (§5.3)<br>`cohort(features: dict, opened_at: str, learning_since: str \| None) -> str` (§5.5)<br>`project_learning(features) -> dict \| None`, `book_ctx(book_type, features) -> dict`, `cf_features(features) -> dict` (whitelists, §5.2) |
| `…/adapters.py` | `@dataclass class EntryFacts`, `ClosedFacts`, `CfFacts` (plain copies)<br>`class SourceDelta: new_open: list[EntryFacts]; new_closed: list[ClosedFacts]; revised: list[ClosedFacts]; cf_seen: list[CfFacts]; cf_vanished: list[str]; resync: bool; busy: bool`<br>`class MainAdapter(eng)`, `class StrategyAdapter(book)`, `class PatternAdapter(book)`, each with `.book`, `.book_name`, `.book_type` and `read(cursor: SourceCursor, *, lock_timeout_s: float) -> SourceDelta`.<br>`def main_signal_keys(eng, risk_log, decisions, briefs) -> dict[str, str]` (trade_id → `_signal_id`, §5.4) |
| `…/collector.py` | `class SharedExperienceCollector`:<br>`@classmethod from_engine(cls, eng) -> "SharedExperienceCollector"`<br>`step(self, eng, *, risk_log, decisions, briefs, now) -> dict`<br>`health(self) -> dict`<br>`status(self) -> dict`<br>Internals: `_cursor: CursorState` (load/save `cursor.json`), `_drafts: dict[str, Draft]` (pending snapshots; memory only), breaker and budget. |
| `…/store.py` | `class ExperienceStore(DecisionJournal)`: `__init__(self, root: Path, *, hot_max_lines=5000, archive_max_segments=0, code_sha=None)`, where path = `root/"experience.jsonl"` and archive = `SegmentArchive(root/"archive", stream_id="shared_experience", record_schema_version=ROW_SCHEMA, code_sha=code_sha, max_segments=archive_max_segments)`<br>`append_rows(self, rows: list[dict]) -> tuple[int, int]` (written, rejected): dedupe by `decision_id` against `_seen`; `json.dumps(allow_nan=False)` per row (NaN → reject and count); **one** `open("a")` + `flush` + `fsync` under `self._lock`; updates `_line_count`.<br>`disk_bytes(self) -> int` (hot size + archive `manifest()` totals).<br>Inherited: `rotate()`, `load_seen()`, `iter_all_rows()`, `stats()`. |
| `…/stats.py` | `summarize(rs: list[float], *, clusters: list[str] \| None, min_n: int) -> dict`. Reuses `pattern_trader.report._r_stats` / `_bootstrap_ci` (seed 20260916, n < 5 → None) and `patterns.engine.wilson`, and adds `cluster_bootstrap_ci(rs, clusters, iters=2000, seed=20260929)`. `verdict(st) -> str` (§11.3). **As built (§18):** no `stats.py`; the statistics live in `report.py`, and the iid CI is a numpy bootstrap with the same method and seed but not numerically the scorecard's `_bootstrap_ci`. |
| `…/report.py` | `iter_rows(root: Path) -> Iterator[dict]` (archive then hot, deduped by `row_id`, streaming)<br>`build(root, query: Query) -> dict`<br>`render_tr(doc) -> str`<br>`status_doc(root) -> dict` |
| `docs/ogrenme_modu/DENEYIM_KATMANI_V1.md` | Pre-registration doc (Turkish): the situation_v1 definitions, bucket edges, default dimensions, backoff order, verdict rules, cohort rules. Committed **before** any data is looked at. |

---

## 4. Situation snapshot `situation_v1`

### 4.1 Windows, normalisation and completeness (requirement c)

- **Windows:**
  - `W4H = 200` closed 4h bars of the symbol;
  - `W1H = 200` closed 1h bars;
  - `WBTC = 200` closed 4h bars of BTC/USDT.
- W must be ≤ 240 because Formasyon's `DataService` holds 240 bars per timeframe (`pattern_trader/data.py:30`). Changing that number would change Formasyon's decisions, so it is not allowed. Tour frames hold 700 (4h) and 500 (1h) bars (`engine.py:58`), and the CSV cache holds 720. Truncating every source to the same W makes all of them produce the same output.
- **Consequences:**
  - no EMA200 (unstable on 240 bars);
  - no 1d fields in v1 (Formasyon has no 1d bars; deriving 1d from 4h is left for v2).

**`normalize_rows(src, tf, as_of_ms, W, fetch_lb_ms)`:**

1. Coerce the input to `(ts_ms, o, h, l, c, v)`. `ts` comes from the `timestamp` column, else the dt index.
2. Drop rows with non-finite or non-positive OHLC, or volume < 0. Dedupe on `ts` (keep last) and sort.
3. **Closed rule** (same as `candle_confirmation.py:39-54` and `strategy_paper.frame_freshness :160`): keep a row only if `ts + tf_ms <= as_of_ms`.
4. **Fetch rule (new):** if `fetch_lb_ms` is given, also require `ts + tf_ms <= fetch_lb_ms`. For tour frames `fetch_lb_ms = eng._tour_now_ms`: frames are fetched after the tour starts (`engine_v3.py:1411` vs the fetch loop at `:1509-1541`), so a bar that closed before the tour start was complete when it was fetched. The CSV cache is closed-only by construction (MarketFeed drops the unclosed bar), so `fetch_lb_ms = None`.
5. Keep the last `W` rows.
6. Set `complete = (last_open_ms == strategy_paper.expected_last_closed_open(as_of_ms, tf))` (`strategy_paper.py:154`).

**Deferral rule.** If any of the three windows is not complete from any source, the row is not written. It stays a **draft (PENDING)** and is retried on every step.
- After `pending_max_age_h` (default 48h) it is written with `snapshot_status = GAP` and `missing` listing the incomplete timeframes.
- Example: a Box CF at 12:05, with frames from a tour that started at 11:58, sees the 08:00 4h bar only as a partial row. It is deferred to the next tour, whose frames are fetched after 12:00.
- Main and in-tour book events (`as_of` = tour `now`) are complete immediately.

**Result:** the snapshot is a pure function of the exact closed-bar windows. Computing it one or several tours later gives a bit-identical result (tested, T2).

### 4.2 Provenance fields (not features)

- `schema_id`, `schema_sha`, `symbol`, `as_of_ms`
- `h4_last_open_ms`, `h1_last_open_ms`, `btc_last_open_ms`
- `n_h4`, `n_h1`, `n_btc`
- `input_sha`: sha256 over, for each window in the order (4h, 1h, btc), the timeframe tag followed by every row packed with `struct.pack(">q5d", ts, o, h, l, c, v)`; the first 16 hex characters are kept.
- `status`: OK | PARTIAL | GAP | NO_BARS | ERROR
- `missing`: list of field names

The following go into the row's `snapshot_meta`, **not** into the snapshot, because they are not part of the determinism contract: `source` ({h4, h1, btc} ∈ tour_frames | csv_cache), `lag_steps` (number of steps the draft waited), `computed_at`.

### 4.3 Features: exact definitions

Notation: arrays of the window, `[-1]` = last closed bar at `as_of`.

- Floats are rounded to 6 decimals when emitted.
- Buckets are computed from unrounded values.
- A field that cannot be computed is `null` and listed in `missing`. It is never set to 0.

**Helpers:**

| Helper | Definition |
|---|---|
| TR | `TR_i = max(h_i − l_i, |h_i − c_{i−1}|, |l_i − c_{i−1}|)` for i ≥ 1 |
| ATR14 (Wilder, SMA seed) | `ATR_14 = mean(TR_1..TR_14)`; then `ATR_i = (13·ATR_{i−1} + TR_i)/14`. Needs n ≥ 15. |
| EMA_p | seed at index p−1 = mean(c_0..c_{p−1}); then `EMA_i = EMA_{i−1} + 2/(p+1)·(c_i − EMA_{i−1})`. Needs n ≥ p. |
| RSI14 | Wilder, with average gain and loss seeded by the mean of the first 14 diffs. Needs n ≥ 15. Average loss = 0 → 100. |

**Features:**

| # | Field | Definition | Needs |
|---|---|---|---|
| 1 | `h4_ret_6_pct` | `(c[-1]/c[-7] − 1)·100` (24h) | 7 |
| 2 | `h4_ret_42_pct` | `(c[-1]/c[-43] − 1)·100` (7 days) | 43 |
| 3 | `h4_ema_state` | +1 if `c > EMA20 > EMA50`; −1 if `c < EMA20 < EMA50`; else 0 | 50 |
| 4 | `h4_ema50_slope_atr` | `(EMA50[-1] − EMA50[-11]) / (10·ATR14[-1])` | 60 |
| 5 | `h4_close_vs_ema50_atr` | `(c − EMA50)/ATR14` | 50 |
| 6 | `h4_er20` | `|c[-1] − c[-21]| / Σ|Δc|` over the last 20 bars; sum 0 → null | 21 |
| 7 | `h4_structure` | From the vendored `confirmed_swings(k=3)` over the last 120 bars (unique extreme in a 7-bar window; confirmed only if i + 3 ≤ n − 1). Last two highs and last two lows: HH_HL if both higher; LH_LL if both lower; MIXED otherwise; UNKNOWN if fewer than 2 of either. | 10 |
| 8 | `h4_trend` | UP if #3 = +1 and #4 > 0; DOWN if #3 = −1 and #4 < 0; RANGE otherwise; UNKNOWN if an input is null | 60 |
| 9 | `h4_atr_pct` | `100·ATR14/c` | 15 |
| 10 | `h4_atr_ratio` | `atr_pct[-1] / median(atr_pct[-101:-1])`, needing ≥ 50 prior values (lab `min_periods=50`, `signal_lab.py:281`) | 65 |
| 11 | `h4_vol_regime` | LOW if #10 < 0.8, HIGH if > 1.25, else NORMAL (`signal_lab.py:301`); UNKNOWN if null | 65 |
| 12 | `h4_range_pos_20` | `(c − min l[-20:]) / (max h[-20:] − min l[-20:])` | 20 |
| 13 | `h4_bbw_pctile_100` | `bbw_i = 4·std20(c)_i / SMA20_i` (ddof 0); the share of `bbw[-101:-1]` that is strictly below `bbw[-1]` | 120 |
| 14 | `h4_dist_swing_high_atr` | Nearest confirmed swing high (whole window) with level > c and **no close above the level after its confirmation index**: `(level − c)/ATR14`. Null if none (`h4_swing_high_found = false`). | 15 |
| 15 | `h4_dist_swing_low_atr` | Mirror of #14 for swing lows: `(c − level)/ATR14` | 15 |
| 16 | `h4_rsi14` | RSI14 | 15 |
| 17 | `h4_rsi_bucket` | `<30` / `30-50` / `50-70` / `>70` (`signal_lab.py:299`) | 15 |
| 18 | `h4_vol_ratio20` | `v[-1] / mean(v[-21:-1])` (prior 20, as `signal_lab.py:280`); mean 0 → null | 21 |
| 19 | `h4_vol_bucket` | LOW if #18 < 0.8, HIGH if > 1.5, else NORMAL (`signal_lab.py:298`) | 21 |
| 20 | `h4_effort_result` | `#18 / max(|c − o|/ATR14, 0.1)` (absorption proxy) | 21 |
| 21 | `h4_exhaustion` | Let S = { i ∈ [n−10, n−1] : c_i ≥ max(c_{i−19..i}) }. UP_EXHAUST if n−1 ∈ S, \|S\| ≥ 3, and the vol_ratio20 at the last three members of S is strictly decreasing. DOWN_EXHAUST is the mirror with closing lows. Otherwise NONE. | 40 |
| 22 | `h1_rsi14` | RSI14 on 1h | 15 |
| 23 | `h1_ret_4_pct` | `(c[-1]/c[-5] − 1)·100` | 5 |
| 24 | `h1_vol_ratio20` | as #18, on 1h | 21 |
| 25 | `h1_close_vs_ema20_atr` | `(c − EMA20_1h)/ATR14_1h` | 20 |
| 26 | `h1_va_pos` | Volume profile over the last 120 1h bars: 48 equal bins between min l and max h; each bar's volume is split equally over the bins its [l, h] touches; POC = argmax (lowest index on a tie); value area grows from the POC to the adjacent bin with more volume (tie → upper) until it holds ≥ 70% of total volume. ABOVE_VA if c > VAH, BELOW_VA if c < VAL, else IN_VA. Degenerate range or zero volume → null. | 30 |
| 27 | `h1_poc_dist_atr` | `(c − POC_mid)/ATR14_1h` | 30 |
| 28 | `btc_h4_trend` | #8 on the BTC window | 60 |
| 29 | `btc_h4_ret_6_pct` | #1 on BTC | 7 |
| 30 | `btc_h4_vol_regime` | #11 on BTC | 65 |
| 31 | `corr_btc_h4_50` | Pearson correlation of log returns over the last ≤ 50 timestamp-aligned returns, needing ≥ 40; zero variance → null. BTC itself: 1.0 and `is_btc = true`. | 41 |
| 32 | `rs_btc_h4_42_pct` | `#2 − btc #2` | 43 |
| 33 | `hour_utc`, `session`, `weekday` | From `as_of`: session is ASIA [0, 8), EU [8, 16), US [16, 24) UTC; weekday 0 = Monday. Not cached (computed per call). | — |

**Status:**
- **OK:** all three windows are complete, n = W, and `missing` is empty.
- **PARTIAL:** complete, but n < W (new listing, or a frame that does not reach back far enough for backfill), or some field is null.
- **GAP:** incomplete after `pending_max_age_h`.
- **NO_BARS:** no 4h or no BTC window at all.
- **ERROR:** an exception, counted in `status.json`.

### 4.4 Elder ideas: v1 versus later

| Idea | v1 (OHLCV proxy) | Later |
|---|---|---|
| Higher-timeframe trend context (HH/HL) | #3, #4, #6, #7, #8 | 1d from 4h (v2) |
| Supply/demand zones | #12, #14, #15 | Real zones (base → impulse, freshness, times tested); weekly levels (`learn/weekly_structure`) |
| Volume profile / POC / value area | #26, #27 (1h OHLCV approximation) | aggTrades profile |
| Exhaustion | #21 (total volume) | Taker volume shrinking (needs `taker_buy_base`, which `engine.py:75-76` and `CsvCandleCache.COLUMNS` drop) |
| Absorption | #20 | Taker delta / CVD |

### 4.5 Buckets (the user's five dimensions) and the cache

- `bucket(snap, side)` returns:
  - `trend` = `h4_trend`
  - `vol` = `h4_vol_regime`
  - `btc` = `btc_h4_trend`
  - `volume` = `h4_vol_bucket`
  - `structure` = `h4_structure`
  - `align` = WITH (UP & LONG or DOWN & SHORT) / AGAINST (the opposite) / NEUTRAL (RANGE) / UNKNOWN
- The snapshot is side-agnostic and cached per `(symbol, h4_last_open, h1_last_open, btc_last_open, n_h4, n_h1, n_btc)`. Every book, real or counterfactual, that sees the same bars gets the same snapshot object. This is the "same situation" join across books.
- `SCHEMA_SPEC` pins: field list and order, W values, k = 3 and swing lookback 120, all bucket edges, volume-profile parameters, rounding, helper algorithm names, and the `input_sha` packing. `SCHEMA_SHA` is pinned by a test. Any change requires `situation_v2`.

---

## 5. Row schema `shared_experience_row_v1` (append-only JSONL)

### 5.1 Envelope (every row)

| Field | Value |
|---|---|
| `schema` | `"shared_experience_row_v1"` |
| `row_id` | `row_id(kind, book, src_id, rev)`, 16 hex characters |
| `decision_id` | = `row_id`. This is the DecisionJournal idempotency key. `kind` is never `decision` or `outcome_link`, so `iter_all_rows` dedupes by `decision_id`. |
| `kind` | `xp_entry` \| `xp_outcome` \| `xp_cf` |
| `rev` | int ≥ 0 |
| `book` | Ledger key: `main`, `strategy_paper` (T2), `strategy_paper_m2`, `strategy_paper_box`, `strategy_paper_trend4h` (D4), `strategy_paper_candle4h` (C4), `strategy_paper_candle4h_strict` (C4S), `pattern_trader` |
| `book_name` | `learning_mode.BOOK_NAMES` name (`main`, `t2_trend_regime`, …, `pattern_trader`) |
| `book_type` | MAIN \| STRATEGY \| PATTERN |
| `symbol`, `side`, `market` | `market` is always `USDM_PERP` in v1 |
| `setup_type` | main: `pos.setup_type` = `plan.entry_type`; books: `act.setup_type` (`trend`, `box_fade`, `trend_donchian`, `candle:<CV id>`); Formasyon: `family`. The same source is used for CF rows (`features.setup_type` / `family`). |
| `variation` | C4: CV id (real: `features.candle_variation.id`; CF: `ShadowTrade.variation`); else null |
| `setup_key` | `f"{book_name}|{setup_type}|{side}"` |
| `signal_key`, `signal_key_src` | §5.4 |
| `join_key` | `f"{book}|{symbol}|{side}|{signal_key}|{variation or ''}"`, or null when there is no signal key |
| `origin` | LIVE \| BACKFILL_OPEN \| BACKFILL_HISTORY \| BACKFILL_CF |
| `recorded_at`, `app_mode` (PAPER), `code_sha`, `config_hash`, `layer_version` | |

### 5.2 `xp_entry` (one per real trade; `rev` 0)

- **Keys and timing:** `trade_key` (`book|trade_id|opened_at`), `trade_id`, `opened_at`, `as_of_ms` (= `opened_at`).
- **Geometry:** `entry_px` (fill `entry_avg`), `initial_stop`, `stop_dist_pct`, `targets`, `leverage`, `notional`, `risk_usdt`.
  - `initial_stop` comes from `pos.initial_stop` when the position is open. After close it is `entry ∓ features.risk_usdt/quantity`, because TradeRecord has no stop field.
- **`cohort`:** UNTAGGED \| POLICY \| LEARNING_EXTRA (§5.5), plus `pre_learning: bool`.
- **`learning`:** whitelist projection of `features.learning`, or null:
  - `size_rule`, `slots`, `risk_pct`, `leverage`, `leverage_max`, `risk_usdt`, `risk_fraction_of_budget`, `equity_basis`, `learning_unlocked_by[]`, `exploration`, `leverage_fallback`;
  - `policy_basis` {`policy_basis`, `learning_codes`, `policy_codes`, `p_win`, `p_win_policy`, `conservative_net_edge_r_policy`, `size_multiplier_policy`} (main only);
  - `has_baseline_size` (bool). The `baseline_size` dict itself is not copied.
- **`in_lab_universe`:** `features.in_lab_universe` (D4/C4/C4S/Formasyon), else null. `exploration` is also copied to top level for filtering.
- **`book_ctx`:** at most 16 scalars or compact dicts.
  - MAIN: `p_win`, `expected_r`, `regime`, `consensus_score`, `consensus_conf`, `n_vetoes`, `expected_cost_pct`, `spread_pct`, `structure{action, reason_code, shadow}`, `regime_gate.verdict`, `candle_confirmation.verdict`.
  - STRATEGY: `strategy`, `regime`, `expected_r`, `structure{action, reason_code, shadow}`, `candle_variation{id, definition_sha, target_r, max_hold_bars, observation}`.
  - PATTERN: `family`, `cohort`, `protocol_version`, `rr_after_cost`, `target_source`, `side_rule`, `age_h_at_entry`.
- **Snapshot:** `snapshot` (situation_v1), `snapshot_status`, `snapshot_meta`.

### 5.3 `xp_outcome` (one per close; `rev` increases on revision; `final` flag)

- **Keys:** `trade_key`, `trade_id`, `opened_at`, `closed_at`, `final`.
- **Exit:** `exit_reason`, `exit_basis` (`features.exit_fill.basis`), `path_unverified`.
- **R, net and gross** (`risk = features.risk_usdt` = |entry_avg − initial_stop| × initial_qty, `futures_ledger.py:567`):
  - `r_net` = `rec.r_multiple` (= net_pnl / risk)
  - `fee_r` = (entry_fee + exit_fee) / risk
  - `funding_r` = −(funding_received − funding_paid) / risk (positive = cost)
  - `cost_r` = `fee_r + funding_r`
  - `r_gross` = gross_pnl / risk (= `r_net + cost_r`)
  - `slippage_r` = slippage_cost / risk (information only; slippage is already inside the fill prices, so it is inside `r_gross`)
  - `cost_r_all_in` = `cost_r + slippage_r` (comparable with CF `cost_r`)
  - `label_version` = `"ledger_v2"`
- **Path:**
  - `mfe_pct`, `mae_pct`;
  - `mfe_r = mfe_pct/risk_pct`, `mae_r = mae_pct/risk_pct`, where `risk_pct = 100·risk/(quantity·entry)`;
  - `hold_hours`;
  - `hold_bars_4h` (from timestamps, because Formasyon's `bars_held` stays 0);
  - `bars_held_ledger`.
- **Money:** `net_pnl`, `gross_pnl`, `entry_fee`, `exit_fee`, `funding`, `funding_complete` (`features.funding_coverage.complete`).
- **Labels:** `label2` = WIN if `r_net > 0`, else LOSS; `label3` = WIN if ≥ +0.25R, LOSS if ≤ −0.25R, else SCRATCH.
- **Revisions:**
  - If `funding_complete` is false at close, write `rev 0, final=false`.
  - Re-check each step while the key is inside `history[-500:]` (the `settle_late_funding` window).
  - Any change in (`r_net`, `funding`, `funding_complete`) gives `rev + 1`.
  - `final=true` when coverage completes, the record leaves the window, or 72h have passed.
  - The report always uses the highest `rev` per `trade_key`.

### 5.4 Signal key derivation (join by signal)

| Book | Real trade | Counterfactual |
|---|---|---|
| main | `eng._signal_id(sym, "USDM_PERP", d, d.active_plan, brief)`, computed **in the same tour** for every `risk_log` entry with `trade_id` (`decisions[sym]`, `bmap[sym]`; the same objects `_execute_locked` used at `:1943-1988`). Src `ENGINE_SIGNAL_ID`. Cross-checked against `eng._seen_signals` (the open appends `_sig`). Positions opened before the layer started, or while it was off: `null`. | `ShadowTrade.signal_key` (src `CF_RECORD`) |
| T2/M2/Box/D4/C4/C4S | 1) `pos.meta["signal"]["signal_ts"]` (D4/C4 `one_entry_per_signal`, `strategy_paper.py:962-965`) → src `META_SIGNAL_TS`; 2) `features.data_source.bars[decision_tf]`, with decision_tf = `StrategyBook._decision_tf()` (T2/M2 1d, Box 5m, D4/C4 4h) → src `DATA_SOURCE_BAR`. Formatted `"signal_ts:%d"`, identical to `_signal_key(act)` (tested per family). | `ShadowTrade.signal_key` |
| Formasyon | `features.plan_id` → src `PLAN_ID` | `ShadowTrade.signal_key` (= plan_id) |

**Uses of `join_key`:**
1. **Supersede detection.** A CF that vanishes while a real `xp_entry` exists with the same `join_key` becomes status SUPERSEDED and is excluded from CF statistics, because the same observation was taken for real.
2. **Report cross-reference.** The report counts "signals seen both as CF and real".

**Joins across books** go through the snapshot cache key: the same bars give the same situation.

### 5.5 Cohorts (identical to the release scorecard)

- `learning_class` at `scripts/bot_scorecard.py:183-187`:
  - LEARNING_EXTRA if `features.learning.learning_unlocked_by` is non-empty;
  - POLICY if `features.learning` is a dict with an empty list;
  - **UNTAGGED** if there is no `features.learning` (baseline path, suspended, or before learning).
- `pre_learning` = no learning tag **and** `opened_at < learning_mode_since`. `learning_mode_since` comes from `eng._lm_since()` (`engine_v3.py:3875`, file `learning_mode.json`), the same rule as `_after` at `bot_scorecard.py:223-229`.
- The report's "real" columns are: all · POLICY · LEARNING_EXTRA · UNTAGGED-after · PRE_LEARNING.

### 5.6 `xp_cf` (per counterfactual; rev 0 carries the snapshot)

- **Keys:** `cf_key` (`book|cf_id`), `cf_id` (`ShadowTrade.id`).
- **`status`:** PENDING \| LABELLED \| SUPERSEDED \| EXPIRED \| DROPPED \| VANISHED.
- **Timing and reason:** `created_at`, `as_of_ms` (= `created_at`), `reason` (= `reason_not_opened[0]`), `reasons`, `reason_family`:
  - CAPACITY: TOTAL_OPEN_RISK, INSUFFICIENT_MARGIN, MIN_ORDER_CONFLICT
  - EXCHANGE: MIN_NOTIONAL, MIN_QTY, STEP_ZERO_QTY, MAX_QTY, LEVERAGE_TOO_HIGH
  - OCCUPANCY: POSITION_OPEN, ALREADY_OPEN*, OPPOSITE_EXPOSURE_CONFLICT
  - PARITY: RISK_OUTSIDE_TESTED_RANGE, CHASE_LIMIT, EXPIRED_BEFORE_ENTRY, ENTRY_DRIFT, ALSO_MATCHED
  - GATE: CANDLE_VETO*, CHART_VETO*, REGIME_VETO*, STRUCTURE*, NEGATIVE_NET_EDGE, RESEARCH_SIZE_ONLY, COSTS_EXCEED_EDGE, LEVERAGE_GATE_BLOCKED, KILL_SWITCH_ACTIVE
  - LIQUIDITY: LIQUIDITY_UNKNOWN
  - OTHER
- **Geometry and labelling:** `entry_ref` (`ShadowTrade.entry`), `stop`, `targets`, `horizon_bars`, `tf_minutes`, `label_kind`, `approx`, `rule_version`, `label_ts`.
- **Tags:** `learning_unlocked` (always false), `in_lab_universe`, `baseline_blocked_by` (A15 records), `features` (whitelist of `strategy`, `setup_type`, `regime`, `signal_close`, `atr14`, `lab_algo`, `structure{action, reason_code}`, `learning.fit{size_rule, reason, why, policy_grade}`, `candle_variation`; for main also `p_win`, `expected_r`, `conservative_net_edge_r`, `net_expectancy_r`, and the gate verdicts).
- **rev 0 only:** `snapshot`, `snapshot_status`, `snapshot_meta`.
- **When LABELLED:** `outcome{exit_reason, bars, mfe_pct, mae_pct, label_method, label_kind, approx}`, `r_gross`, `cost_r`, `r_net`, `label_version`, `r_basis` (NET \| GROSS_LEGACY \| UNKNOWN_LABEL_VERSION), `mfe_r`, `mae_r`, `labeled_at`, `final=true`.
- **Net-R contract (assumed from the parallel net-R fix):** the `ShadowTrade.outcome` dict gains `r_gross`, `cost_r`, `r_net` and `label_version`.
  - If `label_version ∈ NET_LABEL_VERSIONS` (a constant in `rows.py`, set to the net-R fix's version string), copy them.
  - If `label_version` is absent: `r_gross = outcome.r_multiple`, `r_net = null`, `r_basis = GROSS_LEGACY`.
  - If `label_version` is present but unknown: copy the fields and set `r_basis = UNKNOWN_LABEL_VERSION`. These rows are excluded from net statistics and flagged.
- **Revisions:**
  - A change in the fingerprint `sha8(status, label_version, r_net, r_gross, exit_reason)` gives `rev + 1`. This covers an in-place relabel by the net-R migration.
  - **Vanish classification** (no private attribute is read): a CF id that was seen before and is now absent from the active list gets
    - SUPERSEDED if a real `xp_entry` with the same `join_key` exists in the same book;
    - otherwise, if it was PENDING: EXPIRED if `now > label_ts + (STALE_GRACE_BARS + 1)·tf` (`learning_cf.py:44`), else DROPPED (pending cap or archive trim);
    - otherwise, if it was LABELLED: no revision (archive trim).
  - SUPERSEDED, EXPIRED and DROPPED rows are final and excluded from CF statistics. They are shown in coverage counts.

### 5.7 Size

Measured prototype: about 0.8 KB for a snapshot-bearing envelope with 40 fields. Estimates per row:

| Row | Size |
|---|---|
| CF rev 0 | ≈ 1.6 KB |
| CF label rev | ≈ 0.5 KB |
| entry | ≈ 2 KB |
| outcome | ≈ 0.8 KB |

---

## 6. Sources and adapters (requirement a)

| Book | Ledger (real) | Lock | Counterfactual source | Lock | Tag location |
|---|---|---|---|---|---|
| main | `eng.ledger2.positions` / `.history` | `eng._ledger_lock` (`:941`), try 0.5 s | `eng.shadow.trades` where `t.book == "main"` | none (main thread only: written at `:3972-4075`, read by the collector on the same thread) | `features.learning`, `features.exploration`, `features.learning.policy_basis` |
| T2, M2, Box, D4, C4, C4S | `book.ledger` | `book.lock` (`strategy_paper.py:759`), try `lock_timeout_s` | `book.cf.sb.trades` if `book.cf is not None` (lazy, `:1215-1225`) | `book.lock` | `features.learning`, `features.in_lab_universe`, `features.candle_variation` |
| Formasyon | `eng.pattern_book.ledger` | `book.lock` (`book.py:104`) | `pattern_book.cf.sb.trades` if not None (`:1282-1291`) | `book.lock` | `features.learning`, `features.in_lab_universe`, `features.plan_id/family` |

**Copy-only critical sections.** Under the lock, the adapter:
- copies `list(ledger.positions.items())` and the tail `history[k:]` references;
- for records it has not seen, projects the needed fields into facts dataclasses (floats, str, shallow dict copies of `features`);
- for CF lists, copies `list(sb.trades)` and projects only ids that are new or whose fingerprint `(outcome is None, id(outcome))` changed.

No I/O and no snapshot computation happen under a lock. If the lock is busy, the book is skipped for this step (`lock_busy_skips += 1`); nothing is lost because the cursors are incremental.

**Ledger cursor (O(new)).** Per book: `{n, last_key, first_key}` where key = `trade_id|opened_at`.
- New closed records = `history[idx(last_key)+1:]`, where `idx` is found by scanning backwards from the end (bounded by the number of new records).
- If `last_key` is missing (ledger reset or restore), set `resync = true`: rebuild the known set from `history` against `cursor.unfinal` and `open_entries`, then re-emit with deterministic `row_id`s. Duplicates are harmless because the report dedupes by `row_id`.

**Open-position entries.** Keys in `ledger.positions` that are not in `cursor.open_entries` produce an `xp_entry` (as_of = `opened_at`). A trade that opened and closed between two steps (Box, the 60 s monitor) produces its `xp_entry` from the TradeRecord, immediately before its `xp_outcome`.

**CF cursor.** Per book:
- `known: set[cf_id]` = ids present in the active list that already have a row. Pruned to the active list every step, so its size is ≤ 5000 per book.
- `pending: {cf_id: {rev, fp, status, join_key, label_ts, tf_minutes}}`.

---

## 7. Collector `step()` algorithm

1. **Gate.** If `mode != RECORD` or the breaker is tripped, return. If `eng.mode_state.mode.value != "PAPER"`: status `SUSPENDED:<mode>`, return.
2. Set up `t0`, `budget = tour_budget_s`, `rows_cap = max_rows_per_tour`, and `bars = BarSource(frames=eng.runner.last_frames, provenance=eng._frame_provenance, tour_now_ms=eng._tour_now_ms, csv_dir=eng.cfg.cache_path)`.
3. `sigmap = main_signal_keys(eng, risk_log, decisions, briefs)` (pure).
4. For each adapter (main, 6 strategy books, Formasyon): `delta = adapter.read(cursor[book])`. This is copy-only and try-lock.
5. **Priority queue** (stop when the budget or row cap is hit; the rest carries over to the next step):
   1. `xp_outcome` for `new_closed` and `revised` (no snapshot needed), and `xp_cf` label and vanish revisions for known CFs;
   2. `xp_entry` for LIVE `new_open`, then `xp_cf` rev 0 for LIVE new CFs, each through `materialize()`;
   3. retry `drafts` (PENDING snapshots);
   4. backfill (first start, `origin = BACKFILL_*`), newest first.
6. **`materialize(symbol, as_of)`:**
   - windows from `bars.window(...)` for (4h, 1h, btc 4h);
   - if any is incomplete: keep or refresh the draft (`lag_steps += 1`); if its age exceeds `pending_max_age_h`, emit with GAP;
   - else: look up the cache by key, `compute_core` on a miss, then add calendar data.
7. `store.append_rows(batch)` (one fsync), then `store.rotate()` (early-exit on the line counter), then `cursor.save()` (atomic, only if changed), then `status.json` (atomic).
8. **Counters:**
   - `rows_by_kind`, `snapshot_status_mix`, `drafts`, `lock_busy_skips`, `budget_overruns`, `resyncs`;
   - `vanished_by_status`, `legacy_gross_cf`, `unknown_label_version`, `write_errors`, `archive_errors`;
   - `errors_total`, `last_error_code` (via `learn.telemetry.sanitize_code`), `step_ms_p50/p95` (last 50 steps).
9. **Breaker:** 5 consecutive exceptions **or** 3 consecutive budget overruns → `DISABLED_BY_BREAKER` for the life of the process (a restart re-enables). Status and health say so.

**Backfill (first start, `cursor.json` absent, `backfill: true`):**
- open positions → BACKFILL_OPEN;
- `ledger.history` (≤ 5000 per book) → BACKFILL_HISTORY entry + outcome;
- active CF lists → BACKFILL_CF (status as currently seen).

Snapshot coverage for old `as_of` values:
- 4h full window while `as_of ≥ now − (700 − 200)·4h ≈ 83 days`;
- 1h full window while `as_of ≥ now − 300h ≈ 12.5 days`;
- beyond that: PARTIAL (1h fields null) or NO_BARS.

Estimated at the live rates: about 2–3k rows, cleared in about 4–6 steps at the 600-row cap.

---

## 8. Storage

**Location:** `state/shared_experience/`

| File | Purpose |
|---|---|
| `experience.jsonl` | Hot file, ≤ `hot_max_lines` = 5000. **Rotation hysteresis (2026-09-29, §18):** a rotation runs only when the file exceeds 5000 lines and trims it to `hot_max_lines × 0.5` = 2500, so each segment holds ≥ 2500 rows (about one rotation per day at live rates). Below the limit `rotate()` does no I/O. Crash recovery (`recover()` + pending trim) runs once per process, when the collector is built, and again after an archive error. |
| `archive/segments/seg-*.jsonl.gz`, `archive/manifest.json` | `SegmentArchive`: lossless seal → manifest → trim, with crash recovery through `pending_trim`; `max_segments = 0` means unlimited |
| `cursor.json` | Schema `shared_experience_cursor_v1`: per book `{ledger:{n,last_key,first_key}, open_entries:[…], unfinal:{key:{rev,fp,closed_at}}, cf:{known:[…], pending:{…}}}` and `backfill:{done, per_book}`. About 1 MB at 40k known CF ids (measured: 3.3 MB at 40,320 ids). Atomic write, only when changed; **above 256 KB at most every 5 steps** (2026-09-29, §18). As built it also carries, per book and only when non-empty: `sig_tid` (main: unconsumed `trade_id → signal id`, 2 days / 500), `cf_gone` (tombstones of vanished CF ids: last written rev, 3 days / 2000) and `pend_e` (entry drafts of trades that closed while their snapshot was still pending). |
| `status.json` | Written every step. Small. |

**Volume at live rates.** From the `--check` after about 10 hours: Box CF ≈ 1,170/day, main CF ≥ 130/day, real entries ≈ 150–200/day, outcomes ≈ 120–150/day.
- Design estimate: about 3,000 rows/day, about 3.3 MB/day raw, about 0.4 MB/day gzipped, so roughly 150 MB/year archive.
- **Measured (2026-09-29 review, harness row sizes):** CF rev 0 3,063 B, CF rev > 0 1,841 B, entry 3,088 B, outcome 1,700 B → about 2,900 rows/day and **about 7.1 MB/day raw**. Whole-file gzip ratio 0.11 → about 0.8 MB/day archive (about 300 MB/year). The hot file (about 13 MB at 5000 lines) fills about 1.7 days after deploy; after that one rotation seals about 2500 rows roughly once a day.
- **Backups (2026-09-29, §18):** the hourly backup copies the immutable `archive/segments/` only at UTC hour 00; the other 23 hourly copies carry manifest + hot file + cursor + status (≤ about 20 MB). Manual/daily/weekly backups always carry the segments.

**Hard cap `max_total_mb` (default 1024):**
- Above 90% of the cap: status WARN.
- Above 100%: CF rev-0 rows **of books that already have ≥ 10,000 labelled CF rows** are thinned to 1-in-2 (flag `thinned`), then all writes stop with status DEGRADED.
- Nothing is ever deleted.

**Restart:**
- `ExperienceStore.load_seen()` streams the hot file (≤ 5000 ids) and `cursor.json` is loaded; then the store's crash recovery runs once, before any append.
- If `cursor.json` is lost (or is up to 5 steps stale, because large cursors are written at most every 5 steps): resync as in §6. `row_id`s are deterministic, so the worst case is duplicate lines. The report keeps the **last** copy of an equal (kind, key, rev) (§18).
- A failed batch write is truncated back to its start size, so it leaves no bytes behind (`rollback`; a failed truncate is counted as `rollback_failed`).

**Reads inside the worker:** only its own hot file, once at start, streaming. The worker never reads a book's `trade_memory.jsonl`, `position_path.jsonl` or CF JSON files. If a later version needs such a JSONL, it must use the `learn.memory.MemoryTail` / `PositionPathStore` offset-index mechanism (CONTRACT round 3/4 rows) and never a full parse per tour.

---

## 9. Config (requirement e)

**`config_v3.py`:**
- Add `SharedExperienceSection` after `LearningModeSection` (`:724-741`).
- Add `shared_experience: SharedExperienceSection = field(default_factory=SharedExperienceSection)` to `V3Config` after `:775`.
- Add `"shared_experience": SharedExperienceSection` to `_SECTIONS` after `:793`.

**As built (§18):** the section also has `enabled: bool = False` (the collector runs only when `enabled` is true **and** `mode == RECORD`) and `lazy_fetch_max_per_tour: int = 0` (0..20; 0 = no network). `config.yaml` is `{enabled: true, mode: RECORD}`.

```python
@dataclass
class SharedExperienceSection:
    """ORTAK DENEYİM KATMANI v1 (2026-09-29) — yalnız KAYIT: karar/defter/öğrenici DEĞİŞMEZ. Kod varsayılanı OFF.
    Env `TRADINGBOT_SHARED_EXPERIENCE=off` yalnız KAPATABİLİR. Bilinmeyen anahtar ConfigError."""
    mode: str = "OFF"                 # OFF | RECORD   (ADVISE/ENFORCE yok → ConfigError SHARED_EXPERIENCE_MODE_NOT_IMPLEMENTED)
    state_dir: str = "shared_experience"
    hot_max_lines: int = 5000         # 500..50000
    archive_max_segments: int = 0     # 0 = sınırsız, kayıpsız
    max_total_mb: int = 1024          # 64..10240
    tour_budget_s: float = 2.0        # 0.1..10
    max_rows_per_tour: int = 600      # 10..5000
    backfill: bool = True
    cache_entries: int = 2048         # 64..10000
    pending_max_age_h: float = 48.0   # 1..240
    lock_timeout_s: float = 0.2       # 0..2
```

**Validation:**
- `load_v3` gets an unknown-key ConfigError for `shared_experience`, copying the learning_mode check at `:804-814`.
- A new `_validate_shared_experience(cfg)` is called right after `_validate_learning_mode(cfg, _prof)` at `:1163`. It checks types and ranges, requires `mode ∈ {OFF, RECORD}` (case-normalised), and requires `state_dir` to be a plain name (no `/`, `\` or `..`).

**Env off-switch.** After `:837-843`, add `TRADINGBOT_SHARED_EXPERIENCE`:
- `{off, false, 0, disabled}` → `mode = "OFF"` with a warning log;
- any other non-empty value → `ConfigError("TRADINGBOT_SHARED_EXPERIENCE geçersiz … yalnız kapatabilir (off)")`.

**Default:** OFF in code. `config.yaml` (new top-level section, after `learning_mode`):

```yaml
shared_experience:            # ORTAK DENEYİM KATMANI v1 — yalnız KAYIT (karar değişmez); kod varsayılanı OFF
  mode: RECORD                # kapatmak: OFF + restart, ya da env TRADINGBOT_SHARED_EXPERIENCE=off
```

**Side effects:**
- `config_hash()` (`:4676`) changes once at deploy, because the new section enters `asdict(v3)`. That happens whatever the mode, exactly as with learning_mode. The hash is a label only; the dashboard compares it for display at `app.py:2718`. **As built (§18):** `config_hash()` drops the `shared_experience` section, so the hash does NOT change at deploy and decision-journal rows are identical with the layer OFF or RECORD.
- Rolling back to old code leaves `state/shared_experience/` orphaned and harmless.

---

## 10. Engine hooks (exact current anchors)

**Hook E1: `engine_v3.py`, insert after line 1835.** Line 1835 is `self._journal_decisions(risk_log, decisions, now)`; the insert goes before `:1838` (`_write_position_management`).

```python
        # ORTAK DENEYİM KATMANI v1 (2026-09-29): yalnız KAYIT — tüm kararlar/kapanışlar/etiketlerden SONRA, salt okur.
        self._shared_experience_step(risk_log, decisions, briefs, now)
```

**New method, placed right after `_journal_decisions` (def `:4464`):**

```python
    def _shared_experience_step(self, risk_log, decisions, briefs, now) -> None:
        """Ortak deneyim katmanı (yalnız KAYIT). Kapalıyken tek öznitelik okuması; arıza turu/kararı ETKİLEMEZ."""
        sec = getattr(self.cfg.v3, "shared_experience", None)
        if sec is None or str(getattr(sec, "mode", "OFF")).upper() != "RECORD":
            return
        xp = self.__dict__.get("_shared_xp")
        if xp is False:                                  # kurulum kalıcı başarısız (bu süreçte)
            return
        try:
            if xp is None:
                from .shared_experience.collector import SharedExperienceCollector
                xp = self.__dict__["_shared_xp"] = SharedExperienceCollector.from_engine(self)
            xp.step(self, risk_log=risk_log, decisions=decisions, briefs=briefs, now=now)
        except Exception as exc:  # noqa: BLE001 — kayıt katmanı ASLA turu durdurmaz
            self.__dict__["_shared_xp_fail"] = int(self.__dict__.get("_shared_xp_fail", 0)) + 1
            if xp is None and self.__dict__["_shared_xp_fail"] >= 3:
                self.__dict__["_shared_xp"] = False
            log.warning("ortak deneyim katmanı adımı atlandı (karar ETKİLENMEZ): %s", exc)
```

**Hook E2: `engine_v3.py`, insert between `:1883` and `:1884`**, i.e. after the `health["learning_mode"]` block and before `atomic_write_json(st / "health.json", health)`:

```python
        _xp = self.__dict__.get("_shared_xp")
        if _xp:                                          # OFF → anahtar yok (health.json bit-aynı)
            health["shared_experience"] = _xp.health()   # {mode, state, rows_total, rows_last, drafts, step_ms, errors, disk_mb}
```

**Everything else is unchanged.** Not touched: `strategy_paper.py`, `pattern_trader/*`, `box_timer.py`, `learning_cf.py`, `learn/shadow.py`, `learning_mode.py`, `accounting/*`, `replay/*`, dashboard.

**CLI (`cli_v3.py`):** in `register` (`:1091`), after `learning-reconcile` (`:1214-1222`):
- `shared-experience-report` (§11)
- `shared-experience-status` (prints `status.json`, a store summary, coverage per book over the last 24h / 7d, and NO_DATA flags)

Both are read-only, run in their own process, and make no network calls.

---

## 11. Report CLI (requirement f)

### 11.1 Question → command

```
python -m tradingbot shared-experience-report
    [--book <book_name|all>] [--setup <setup_type>] [--side LONG|SHORT] [--variation CV00x]
    [--trend UP|DOWN|RANGE] [--vol LOW|NORMAL|HIGH] [--btc UP|DOWN|RANGE] [--volume LOW|NORMAL|HIGH]
    [--structure HH_HL|LH_LL|MIXED]           # any subset; unspecified dims are "any"
    [--like SYMBOL]                           # "this situation" = latest stored OK snapshot of SYMBOL (shown with its as_of)
    [--kind real|cf|both] [--cohort all|policy|extra|untagged|pre] [--cf-reason-family GATE|CAPACITY|…]
    [--since ISO] [--until ISO] [--origin live|all] [--snapshot ok|ok+partial]
    [--min-n 30] [--no-backoff] [--cells] [--json] [--out FILE]
```

- The default `--min-n 30` equals `pattern_trader.report.MIN_TRADES_FOR_VERDICT`, the threshold behind the scorecard's "ZARARDA (kanıtlı)" label, so the two tools agree.
- `--cells` lists every cell of the setup with n ≥ `min_n` (the "where did we win" map).

### 11.2 Output (Turkish; JSON has the same content)

```
ORTAK DENEYİM — tanımlayıcı; karar yok; kanıt değil (PAPER, seçilim yanlılığı olabilir)
DURUM  trend=UP (4h) · yapı=HH_HL · oynaklık=HIGH · BTC=UP · hacim=NORMAL        [--like SOL/USDT @ 2026-09-29T08:00Z]
KURULUM b1_box_fade | box_fade | LONG                         pencere: tüm zaman · yalnız OK snapshot
                                    n   küme  kazanç%  [%95]         ort.net R  [%95 iid]      [%95 küme]       maliyet R  hüküm
Gerçek — hepsi                     14    9    35.7   [16.3, 61.2]  −0.21  [−0.55,+0.12]  [−0.70,+0.25]   0.41       VERİ YETERSİZ
   politika                         3    3    …                                                                   VERİ YETERSİZ
   öğrenme-ekstra                  11    7    …
Olsaydı (karşı-olgusal, net)       41   18    …                                                                   BELİRSİZ
Olsaydı — brüt, eski etiket        22   11    (net değil; hükme ve karşılaştırmaya girmez)
Kapsam: bekleyen 6 · değiştirildi 2 · süresi doldu 1 · düşürüldü 0 · GAP 0 · PARTIAL 3
Geri çekilme: tam hücre n<30 → 'yapı' düşürüldü → n=…; → 'hacim' düşürüldü → n=… (önceden kayıtlı sıra)
```

### 11.3 Statistics and verdict

For each column, using the selection's final revisions:
- `n`
- `n_clusters`: distinct (symbol, UTC day of `as_of`)
- wins = `r_net > 0`; win rate with a Wilson 95% interval (`patterns/engine.py:98`)
- mean, median and sum of net R; `ci95_mean_r` from `_r_stats` → `_bootstrap_ci` (iid, seed 20260916, n < 5 → null)
- `ci95_mean_r_cluster`: cluster bootstrap over the symbol×day clusters (2000 resamples, seed 20260929, n_clusters < 5 → null)
- mean `cost_r` (real: `cost_r_all_in`)
- mean `mfe_r` / `mae_r`, average hold in hours, exit-reason mix

**Verdict** (labels identical to `scripts/bot_scorecard.py:45`):
- `VERİ YETERSİZ` (INSUFFICIENT_SAMPLE) if n < `min_n`, or n_clusters < 5, or either CI is null.
- `ZARARDA (kanıtlı)` if **both** CIs have upper bound < 0.
- `KÂRDA (kanıtlı, PAPER)` if **both** CIs have lower bound > 0.
- `BELİRSİZ` otherwise.

The cluster CI guards against clustered Box counterfactuals (many signals on one symbol and day) overstating precision.

**Which rows count:**
- **Real:** `xp_entry` joined with the latest `xp_outcome` by `trade_key`; open trades are listed as "açık n".
- **Counterfactual:** latest rev with status LABELLED and `r_basis = NET`. GROSS_LEGACY and UNKNOWN_LABEL_VERSION rows are shown only on their own line. SUPERSEDED, EXPIRED, DROPPED and PENDING rows appear only in coverage.
- **Situation:** taken from the rev-0 snapshot of the entry or CF (the snapshot at decision time).
- **Default snapshot filter:** OK only. `--snapshot ok+partial` includes PARTIAL.

**Backoff (pre-registered, `--no-backoff` disables).** If the exact cell has n < `min_n`, drop dimensions in this order and report every level: `structure` → `volume` → `btc` → `vol` → `trend`. Never merge setups or sides.

### 11.4 Memory and I/O (separate CLI process)

- Single streaming pass over `iter_rows()` (archive then hot, `row_id` dedupe).
- State per `trade_key` / `cf_key`: an interned bucket tuple index plus `(r_net, r_gross, cost_r, status, rev, cluster)`, about 60 B each. At a year of data (about 550k CF plus 70k real keys) that is about 40 MB peak.
- `--since` defaults to all; operators can bound it.
- The CLI writes nothing except `--out`.
- `shared-experience-status` reads only `status.json`, `cursor.json` and the manifest.

---

## 12. Memory and CPU budget (requirement d; live VPS: worker 3.6G now, 4.5G peak, 6G limit)

**Measured on this machine (Python 3.12, numpy):**
- normalise + compute a 3 × 200-bar snapshot with plain loops: **4.1 ms**;
- scanning 5,000 in-memory `ShadowTrade` records for new or changed items: **0.8 ms**.

| Item | Steady | Transient | Notes |
|---|---|---|---|
| Snapshot LRU (2048 × about 2.5 KB) | ≈ 5 MB | — | Complete windows only |
| CF `known` ids (≤ 5000 per book × 8) | ≈ 3.6 MB | — | Pruned to the active lists every step |
| Pending CF, unfinal outcomes, open entries | < 1 MB | — | Bounded by the recorder caps (2000 per book) and slots |
| Drafts (PENDING snapshots) | ≤ 1.8 MB | — | ≤ `max_rows_per_tour` |
| Journal `_seen` (hot ≤ 5000) | ≈ 0.4 MB | — | |
| Adapter copies (facts for new items only) | — | < 2 MB | Freed at step end |
| CSV reads (Formasyon-only symbols, ≤ 720 rows) | — | < 1 MB | Memoised per step |
| Rotation (reads hot file ≤ 5000 lines, seals gzip) | — | ≈ 20 MB | About once per 1.7 days |
| **Total** | **≤ 12 MB (cap 25 MB)** | **≤ 40 MB** | < 1% of the 6G limit; 1.5G headroom at today's 4.5G peak. Measured at full lists: 22 MB retained (see below) |

**CPU per step, typical:**
- snapshots: about 15 new rows × 4 ms, minus cache hits → < 60 ms;
- scans: < 10 ms;
- one fsync: < 10 ms;
- **< 100 ms, compared with a 6-minute tour.**

The budget is 2 s and the first-start backfill is spread over steps by the row cap.

**Measured at VPS list sizes (2026-09-29 review + FIX stage; replaces the estimates above as alarm baselines).** Scale bench: 8 books × 5000 active CFs (40,320 known ids), 8 × 1000 closed trades, 91 open positions, real collector/store/ledgers/recorders:

| Item | Before the FIX stage | After (rotation hysteresis, cursor throttle, backfill copy cap) |
|---|---|---|
| Retained memory | 22.5 MB (`_memq` 10.3, cursor books 10.3, cache 1.2, `_seen` 0.8) | 22.2 MB (same parts; `_seen` 0.5) |
| Transient per steady step | about 61 MB (hot-file read + re-parse + cursor JSON every step) | small: the hot file is read only at a rotation (about once a day); the 3.3 MB cursor JSON only every 5 steps |
| Steady step (13 rows/step) | p50 404 ms, p95 489, max 785 (rotate 227 ms, a 13-row segment per step) | **p50 91 ms, p95 179, max 217** (rotate 0.0 ms between rotations) |
| First-start backfill | 162 steps, p50 887 ms, p95 1,269, max 1,610 | 181 steps, p50 584 ms, p95 968, max 1,262 |
| Restart | construction 291 ms, first step 429 ms | construction 274 ms, first step 315 ms |
| Per-book lock hold (collector copy) | backfill max 428 ms; steady ≤ 32 ms | backfill p50 about 15 ms per book (CF scan 5000 records: 16 → 10 ms), max about 0.46 s (GC pauses on the large heap, not the copy); steady ≤ 17 ms |
| Growth with segment count | per-step bookkeeping grew linearly (≈ 1.9 s at 21.6k segments; breaker at about 31k segments ≈ 130 days) | none between rotations (no manifest/segment-dir access); one rotation ≈ one 2500-row segment per day |

At harness volume (24 tours × 30 min, about 300–650 rows) steps are 8–35 ms p50. **`--check` thresholds:** steady `step_ms_p95` < 1,000 ms at full lists (alarm at 1,500 ms = the breaker's overrun factor × budget); retained memory about 25 MB; `cursor.json` up to about 3.5 MB; hot file ≤ about 15 MB. The first ~180 steps after the first deploy are backfill (p95 about 1 s).

**The layer adds no threads, no network, no frame retention** (frames are never stored in the cache; only derived scalars are) and no JSON parsing of CF or ledger files in the worker.

**Rollback triggers** from the release `--check` stay the same (hwm > 90% × MemoryMax, tour > 35 min, Box missed bars). Added: `shared_experience.state == DISABLED_BY_BREAKER` or `step_ms_p95 > 5000` → turn the layer OFF. This does **not** roll back learning mode.

---

## 13. Tests (new files; all Python 3.12; add to the CI list `.github/workflows/chart-analysis.yml` ~`:46-94`)

**T1 `tests/test_shared_experience_situation_v1.py`**
- `SCHEMA_SHA` pinned; golden vector on a fixed 700/500-bar fixture.
- Causality: mutating any bar with open + tf > as_of, or appending future bars, leaves snapshot and `input_sha` identical.
- Boundary: open + tf == as_of is included; as_of − 1 ms excludes it.
- **Fetch rule:** a partial last 4h row with `fetch_lb < open + tf` is excluded and the window is incomplete, even though open + tf ≤ as_of.
- BTC bars after `as_of` are ignored; is_btc gives corr = 1.0.
- Truncation: 700-, 240- and 200-bar inputs give identical output.
- Short history (n = 50) → PARTIAL, `missing` list correct, no zeros.
- NaN, zero or negative prices are dropped; zero volume gives null vol_ratio.
- Deterministic across repeated runs.
- Bucket edges pinned (0.8/1.25, 0.8/1.5, RSI 30/50/70), matching `signal_lab.context` on synthetic inputs.

**T2 `tests/test_shared_experience_parity_v1.py`**
- The same series through four paths gives identical snapshot and `input_sha`:
  - lab DataFrame (`signal_lab.load_series` columns);
  - engine frame (`data.prepare`, dt index, unclosed tail);
  - Formasyon `DataService` rows (240, fake feed);
  - `CsvCandleCache.write/read` round trip.
- **Late materialisation:** frames at the entry tour versus frames three tours later (same `as_of`) are identical.
- **Deferral:** a Box-timer event after a bar boundary gives PENDING on the first step and OK on the next; the result equals the snapshot from complete frames.

**T3 `tests/test_shared_experience_store_v1.py`**
- `append_rows` round trip, idempotent by `decision_id`, a NaN row rejected and counted.
- One fsync per batch (spy on `os.fsync`).
- A partial last line is tolerated; two threads lose no lines.
- Rotation seal + trim; crash with `pending_trim` recovers without duplication; archive failure means no trim.
- `max_total_mb` WARN → thinning → DEGRADED order.
- `iter_all_rows` dedupe; kinds never `decision` or `outcome_link`.

**T4 `tests/test_shared_experience_real_v1.py`** (real trades, requirement a)
- **Main:** a close via the tour stop (`:1728-1747`), the protective queue (`drain_protective_closes`) and `exit_check` each produce exactly one `xp_outcome`.
- **Books:** a rule CLOSE, a tick stop, an `apply_closed_bars` stop and a Box-timer close each produce exactly one.
- **Formasyon:** target, stop and TIME_STOP each produce exactly one.
- Open and close between two steps produces entry then outcome.
- R math: `r_net == rec.r_multiple`; `r_gross == r_net + cost_r`; mfe_r / mae_r match TradeRecord; `initial_stop` derived from `risk_usdt/qty` equals `pos.initial_stop`.
- Late funding → rev 1 with `final=true`; the report picks it.
- Ledger reset (same id, different `opened_at`) → two keys and a resync counter.
- Restart without `cursor.json` → no duplicate `row_id` on read.
- Pre-existing positions → BACKFILL_OPEN.
- **Learning tags per book:** `size_rule`, `learning_unlocked_by`, `exploration`, `policy_basis` (main), `in_lab_universe` (D4/C4/Formasyon) are projected exactly.
- Cohort equals `scripts/bot_scorecard.learning_class` + `_after` on the same fixtures (reuse `tests/test_learning_mode_wiring.py:423-470` helpers).
- **Signal key per family** equals `_signal_key(act)` at open. Main equals `_signal_id` and is in `_seen_signals`.

**T5 `tests/test_shared_experience_counterfactual_v1.py`** (requirement b)
- Recorder CFs (Box TARGET_STOP_TIME, D4 HORIZON approx, C4 `also_matched` with variation, Formasyon plan_id) and main `book == "main"` shadows produce `xp_cf` rev 0 with a snapshot. Legacy shadows (`book` None) are ignored.
- Labelling produces a rev-1 LABELLED row with the outcome.
- **Net contract:**
  - an outcome with `label_version ∈ NET_LABEL_VERSIONS` → fields copied, `r_basis = NET`;
  - an outcome without it → `r_net null`, `GROSS_LEGACY`;
  - an unknown version → `UNKNOWN_LABEL_VERSION`;
  - an in-place relabel → new rev.
- `supersede` (book and main `_lm_cf_supersede`) → SUPERSEDED via `join_key`. Cap drop → DROPPED. Stale expiry → EXPIRED. Archive trim of a LABELLED record → no revision. Archive trim of a PENDING record → DROPPED.
- CF and real entries on the same bars share the identical snapshot object (cache key).
- The Box timer holding `book.lock` → `lock_busy_skips` and no loss on the next step.

**T6 `tests/test_shared_experience_no_decision_change_v1.py`** (requirement e)
- **Golden equality.** Run the real `TradingEngineV3.tour` (harness `tests/test_engine_v3._engine :46`, `test_strategy_paper_engine_v1._install :27`, `test_learning_mode_wiring._eng :75`) with learning mode **ON and OFF**, each with `shared_experience` absent, OFF and RECORD, over at least 4 tours plus a Box `run_once` and a Formasyon `scan_cycle`. Compare:
  - byte equality of every ledger JSON, `counterfactual_trades.json`, `shadow_book.json`, `shadow_book_learning_tags.json`, `plans.json`, summaries, `risk.json`, `decision_funnel.json`, `signals_seen.json`, `decision_journal.jsonl`;
  - `health.json` minus the `shared_experience` key.
- absent == OFF also covers `health.json` and the state file set.
- **Fault injection** (snapshot raises; store `OSError`; disk full; lock busy; cursor corrupt; `from_engine` raises) → the same equality holds and counters increment. Construction failing 3 times → the sentinel stops retries.
- **Replay:** the `HistoricalReplay` determinism hash is unchanged with RECORD configured.
- **AST guard:**
  - The `shared_experience` package calls none of `save`, `open` (except in `store.py` on its own path), `close_manual`, `tick`, `record`, `supersede`, `label_pending`, `label`, `add`, `settle_late_funding`, `reconcile_funding`, `apply_action`, `evaluate`, `RiskEngine`.
  - It never assigns to attributes or items of objects it does not own (`.features`, `.meta`, `.trades`, `.positions`, `.history`, `.plans`, `.outcome`).
  - Only `engine_v3` and `cli_v3` import it.
  - No module under `tradingbot/learn`, `learning_mode.py`, `learning_basis.py` or `learning_cf.py` references `shared_experience`.

**T7 `tests/test_shared_experience_report_v1.py`**
- Aggregates on fixture rows; highest rev used; Wilson and both CIs deterministic.
- Verdict matrix (THIN / LOSS / WIN / OPEN, including iid-vs-cluster disagreement → BELİRSİZ); `min_n` default 30.
- Backoff order and labels; real and CF columns separate; legacy gross excluded from net and verdict.
- `--like` uses the latest OK snapshot; JSON output stable; no writes except `--out`.
- **Streaming memory:** a 200k-row synthetic store gives a `tracemalloc` peak below 60 MB.

**T8 `tests/test_config_shared_experience_v1.py`**
- Code default OFF; `config.yaml` is exactly `{mode: RECORD}`.
- Unknown key → ConfigError; ADVISE / ENFORCE → ConfigError; range errors.
- `state_dir` path traversal → ConfigError.
- Env `off` → OFF; env `on` or any other value → ConfigError.

**T9 `tests/test_shared_experience_budget_v1.py`**
- Time budget and row cap: priority order and carry-over.
- Breaker after 5 exceptions or 3 overruns.
- Steady `tracemalloc` of a collector with 8 × 5000 CF + 8 × 5000 history below 25 MB.
- `cursor.json` below 2 MB.

**End-to-end acceptance (pre-deploy, not a unit test).**
- Harness: `…/scratchpad/verify2/v2/run.py`. Add a `--xp off|record` switch that overrides only `shared_experience.mode` in the copied config.
- Run 24 tours `--variant on` twice (xp off and record), outputs to `/dev/shm`. `compare.py` must show identical decisions, ledgers and CF files.
- Also report: rows per book/kind, snapshot status mix (expect OK ≥ 95% after the first tour; PENDING drains), step_ms p95, and the RSS delta (expect < 40 MB).
- Delete the `/dev/shm` outputs afterwards.

---

## 14. Deploy invariants (new `deploy/releases/tb-deploy-<tip>.sh`, derived from `tb-deploy-c0b8c94.sh`)

**Prerequisite:** the VPS runs `c0b8c94` (or the net-R release, if shipped together).

**New invariants, appended to the checks list at `:861-898`; all 26 existing ones stay:**
1. `v3.shared_experience.enabled is True` and `v3.shared_experience.mode == "RECORD"`, and the raw `config.yaml` section is exactly `{"enabled": true, "mode": "RECORD"}` (as built, §18; the design said `{"mode": "RECORD"}`). `mode_state.mode == PAPER` (the collector suspends itself otherwise).
2. The service environment has no `TRADINGBOT_SHARED_EXPERIENCE`, or the operator intended it (a set value is printed loudly).
3. `tradingbot.shared_experience.situation.SCHEMA_SHA == "<pinned>"` and `W4H == W1H == WBTC == 200 ≤ pattern_trader.data.BARS_PER_TF[...]`.
4. `state/shared_experience` is creatable and writable by the service user.
5. No module outside the allowed importers imports the package (the same AST check as T6, run on the deployed tree).
6. Learning-mode invariants unchanged (`lm_scalars`, `lm_books`, `lm_runtime`, `mode_file_ok`).

**`--check` additions** (python block `:218-490`):
- print `state/shared_experience/status.json`: state, last_step_at, `step_ms_p50/p95`, `rows_by_kind`, drafts, snapshot status mix, `lock_busy_skips`, `vanished_by_status`, `legacy_gross_cf`, errors, breaker, hot lines, disk MB;
- **coverage per book, last 24h:** entries / outcomes / CF new / CF labelled. Zero-activity books are flagged `VERİ YOK`; today C4 and D4 would show it.
- New rollback trigger (§12). It advises `mode: OFF`, not a learning rollback.

**Post-deploy, first 2–3 tours:**
- rows for main, T2, M2 and Box entries, and CF rows for Box and main;
- drafts drain to about 0 between hour boundaries;
- `hwm_mb` up by < 50 MB against the pre-deploy `--check`;
- tour seconds up by < 5 s;
- `health.json` still has `learning_mode` as before.

**Off switch:** `shared_experience.mode: OFF` + `systemctl restart tradingbot-worker`, or a drop-in `TRADINGBOT_SHARED_EXPERIENCE=off` + restart. No state migration is needed.

**Backup:** covered by `rglob`; the hot file is bounded and the archive is gzipped. As built (§18) the hourly backup carries `shared_experience/archive/segments/` only at UTC hour 00 (`BackupResult.skipped_xp_segments` counts the rest). To restore from another hourly backup, copy the segments from the newest UTC-00 (or manual) backup; segment files are immutable.

---

## 15. Order of work

1. **Coordinate with the net-R agent:** freeze the outcome field names, the `label_version` string (→ `NET_LABEL_VERSIONS`), whether legacy labels are migrated in place, and the real-vs-CF cost definition (`cost_r` vs `cost_r_all_in`). This is blocking for `rows.cf_r` only.
2. Pre-registration doc `docs/ogrenme_modu/DENEYIM_KATMANI_V1.md` (definitions, edges, dimensions, backoff, verdict, cohorts). Commit it before any data is looked at.
3. `situation.py` + `bars.py` + `cache.py`, with T1 and T2 (golden vectors, causality, fetch rule, parity).
4. `rows.py` + `store.py`, with T3.
5. `adapters.py` + `collector.py` on fakes, with T4, T5 and T9.
6. Config section + validation + env, with T8.
7. Engine wiring (E1, E2) and CLI registration, with T6 (golden equality, fault injection, AST guard, replay hash).
8. `report.py` + `stats.py`, with T7.
9. Full pytest, the CI list, `ruff check tradingbot tests scripts`, and the end-to-end harness run (§13; clean `/dev/shm`).
10. `config.yaml` `mode: RECORD` + release script + DURUM/SESSION_HANDOFF entry. The user deploys.
11. Optional, later: an offline `shared-experience-backfill` CLI that reads CF JSON files and archives plus the lab or CSV cache into `backfill/*.jsonl` (a separate stream, never appended by the worker).

---

## 16. Risks

1. **Partial-bar contamination.** A frame fetched before a bar closed is the main causality trap. It is mitigated by the fetch rule, deferral and T1/T2.
2. **Selection bias.**
   - CFs exist only for `COUNTERFACTUAL_OK` reasons.
   - Real learning-extra trades are chosen by relaxed limits.
   - Box CFs cluster by symbol and day.
   - Mitigations: the report never pools real and CF, uses cluster CIs and shows the banner.
3. **Gross/net mixing.** Legacy CF labels are gross and at the Box floor cost about 0.5R. They are excluded from net statistics by construction.
4. **CF source mutability.** Recorders mutate lists and outcomes in place from other threads. Mitigated by try-lock, copy-only projection, and revision fingerprints.
5. **Lock contention.** The Box timer holds `book.lock` for a whole pass. The collector skips and retries; `lock_busy_skips` is visible.
6. **Signal-key gaps.** Main positions opened before the layer existed have a null key: no supersede join, but entry and outcome are still recorded. D4/C4 closed-between-steps trades use DATA_SOURCE_BAR (tested equal).
7. **Schema drift.** Mitigated by the pinned `SCHEMA_SHA`, the golden vector and the v2 bump procedure.
8. **Coupling to `learn/experience*`.** Mitigated by naming and the AST guard.
9. **Disk.** About 150 MB/year gzipped; the cap with thinning and DEGRADED exists but should not trigger.
10. **Zero-activity books.** C4 shows 0 opens and 0 CFs after about 10 hours; D4 and Formasyon show 0 learning opens. The layer reports NO_DATA; it does not diagnose. See Q6.

---

## 17. Open questions

1. **Net-R contract** (blocking for CF net statistics):
   - the exact outcome keys and `label_version` string;
   - whether legacy labels are relabelled in place (the layer handles it as a revision) or only new labels get net fields;
   - whether real trades should be compared on `cost_r` (fees + funding; slippage inside fills) or `cost_r_all_in`.
2. **Backfill scope:** all ledger history (including pre-learning trades; Box has 74 overall) or only since `learning_mode_since`? Proposed: all, tagged `pre_learning` and `origin`.
3. **SPOT** (main spot trades and SPOT CFs): excluded in v1 (proposed), or recorded with `market = SPOT`?
4. **Verdict threshold:** keep `min_n = 30` (the scorecard's `MIN_TRADES_FOR_VERDICT`), or use a lower per-cell default such as 15, given cluster CIs? Also, should a win be `r_net > 0` (as the scorecard) or `> +0.1R` (scratch band)?
5. **Default dimensions and backoff order** (structure → volume → BTC → vol → trend): approve for the pre-registration doc?
6. **C4 zero activity** (8 variations × about 40 coins over 2–3 4h closes, 0 opens and 0 CFs) and **D4/Formasyon 0 learning opens:** investigate as a separate task? The layer only surfaces it.
7. **A15 `POSITION_OPEN` CFs with non-empty `baseline_blocked_by`:** include in CF statistics (proposed: yes, with a filter), or report separately?
8. **Release timing:** ship together with the net-R release (proposed; otherwise the CF net column stays empty and only real trades answer), or ship RECORD first so snapshots start accumulating?
9. **RECORD outside PAPER** (TESTNET/OBSERVE/SHADOW_LIVE): v1 proposes PAPER-only.
10. **Dashboard surface** for the report (Phase 2), and whether `hot_max_lines = 5000` is acceptable for hourly backup size.

---

## 18. As built, and review fixes (2026-09-29)

KARARLAR.md still wins over this file. This section records where the code differs from the design above, so the design text is not read as the contract.

**Deviations recorded by the build stages:**
- **Config.** `SharedExperienceSection` has `enabled` in addition to `mode`; the collector runs only when `enabled` is true and `mode == RECORD`. `config.yaml` is `{enabled: true, mode: RECORD}`. There is a `lazy_fetch_max_per_tour` knob (0..20, default 0 = no network); §1.5 said v1 has none.
- **Decision identity.** `engine_v3.config_hash()` drops the `shared_experience` section (a third engine edit beyond E1/E2), so OFF and RECORD give the same hash as HEAD.
- **Budget and breaker.** Work loops stop at 0.75 × `tour_budget_s`; the breaker fires after 3 consecutive steps over 1.5 × budget or 5 consecutive exceptions.
- **LIVE boundary.** The cursor's `born_ms` (the first RECORD tour's start). Earlier trades and CFs are backfill (`BACKFILL_*` origins).
- **Rows.** `setup_key` uses the ledger's book key; `join_key` has a fixed field order; real and CF `cost_r` are all-in; the cohort helper is split between rows and report; the store degrades at 110% of the cap.
- **Report.** There is no `stats.py`; the statistics live in `report.py`. There is no `--variation` flag (`--setup candle:CV00x` selects a C4 variation). The iid CI is a numpy bootstrap with the same method and seed as the scorecard, but **not numerically the scorecard's `_bootstrap_ci`** (PCG64 vs `random.Random`, and bootstrap results depend on the order of the values). Reusing the pure-Python function would cost about 10 million Python calls per cell at n = 5000. The report states this in `params.ci_iid_note`.
- **Summary sweep.** Nothing writes `report_summary.json` automatically; the CLI help and the dashboard card say so. A low-priority timer is optional (release notes).

**Review fixes (FIX stage):**

| Finding | Change | Test |
|---|---|---|
| Rotation every step once the hot file is full (HIGH) | `ExperienceStore.rotate`: no I/O below `hot_max_lines`; when over, trim to `hot_max_lines × 0.5`; `recover()` once per process (collector start, before any append) and after an archive error; archive bytes re-read only after a rotation; `_seen` drops only the archived block's ids | `test_a_full_hot_file_does_not_seal_a_segment_per_step…`, `test_an_archive_failure_retries_recovery…` |
| Write amplification (MEDIUM) | As above, plus `cursor.json` above 256 KB is written at most every 5 steps (content is re-derivable; `row_id`s are deterministic). The manifest is written by `SegmentArchive` (shared with the decision journal, unchanged), now about twice per rotation instead of twice per step | `test_a_large_cursor_is_written_at_most_every_n_steps…` |
| Backups carry the whole archive (MEDIUM) | `ops/backup.run_backup`: the hourly backup copies `shared_experience/archive/segments/` only at UTC hour 00 (`skipped_xp_segments` counts the rest) | `test_hourly_backups_carry_the_immutable_xp_segments_only_once_a_day` |
| Failed write leaves rows; reader keeps the stale copy (MEDIUM) | `append_rows` truncates the file back to its pre-write size on `OSError` (`rollback`, `rollback_failed`); the report keeps the **last** copy of an equal (kind, key, rev) (`dup_rows_replaced`) | `test_a_failed_write_leaves_nothing_on_disk…` (fsync EIO and partial write + ENOSPC), `test_the_reader_keeps_the_last_copy…` |
| Deferred entry draft lost on restart (LOW) | Entry drafts of closed trades are persisted in the cursor (`pend_e`, Decimal-exact) and re-registered at load; the report counts `real_entry_missing` regardless of the snapshot filter | `test_a_trade_closed_between_steps_keeps_its_deferred_entry_across_a_restart` |
| SPOT real trades not counted (LOW) | `spot_real_excluded` (SPOT_LONG opens in the risk log) and the gauge `spot_real_closed_seen` (`len(eng.spot2.history)`) in status counters | `test_spot_real_opens_are_counted_not_silently_skipped` |
| Main signal key lost when the opening step skips the ledger (LOW) | Unconsumed `trade_id → signal id` kept in the main cursor (`sig_tid`, 2 days / 500); `main_entry_no_sigkey` counter | `test_a_main_open_whose_step_skipped_the_ledger…` |
| Re-appearing CF id collides with its old revisions (LOW) | Tombstones `cf_gone` (last written rev; 3 days / 2000 per book): a re-recorded id continues at `last_rev + 1`; `cf_reappeared` counter | `test_a_counterfactual_id_that_reappears_continues_its_revisions` |
| C4S double-counted in the CANDLE_PATTERN family (LOW) | C4S has no family (setup key stays separate) | `test_c4s_rows_stay_out_of_the_candle_family_pool`, store family test |
| iid CI vs scorecard (LOW) | Documented (see above; not reused) | `test_the_report_says_its_iid_ci_is_not_numerically_the_scorecards` |
| Memory/CPU above §12 (LOW) | §12 now carries the measured numbers and `--check` thresholds | — |
| Lock/GIL during backfill (LOW) | At most 100 history records and 100 CF facts per book are copied under a lock per step; CF scan fast path (16 → 10 ms per 5000 records) | `test_backfill_copies_at_most_a_hundred_records_per_book_under_the_lock` |
