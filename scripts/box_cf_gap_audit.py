# -*- coding: utf-8 -*-
"""BOX CF GAP AUDIT — read-only measurement of the Box counterfactual ("olsaydı") vs real gap (schema box_cf_gap_audit_v1).

Question: the Box book's labelled counterfactuals (signals that were valid but not opened) show a much higher net R than
the real Box trades of the same period. Which of ten candidate explanations (H1-H10) account for that gap? The script only
MEASURES; it changes no decision, no parameter and no state file.

READ-ONLY contract
  * no network; never writes inside --state; the only output file is --json-out (written once, refused with exit 2 when it
    resolves inside --state or equals an input file); run as __main__ it sets sys.dont_write_bytecode
  * the default path uses the standard library only; repo modules are imported lazily: `tradingbot.config` only with
    --config (universe order), `tradingbot.learning_cf` / `learn.shadow` / `accounting` only with --replay-klines.
    The replay builds the ledger's execution model with `FuturesLedgerV2.from_dict` on this script's own read of the
    ledger file (not `FuturesLedgerV2.load`, whose JSON reader copies a corrupt file aside, i.e. writes into the state)
  * .jsonl inputs are streamed line by line; the two JSON inputs are size-checked (over MAX_JSON_BYTES → skipped with a
    message), reduced to slim rows at once and the raw document is dropped
  * deterministic: every bootstrap uses random.Random(--seed + section label)
  * exit codes: 0 ok · 2 bad arguments or a required input missing (or skipped for size) · 3 a required input malformed

Run on the VPS (pre-deploy copy in /tmp with PYTHONPATH, or post-deploy from the app dir):
    sudo -u tradingbot env PYTHONDONTWRITEBYTECODE=1 TRADINGBOT_STATE_DIR=/opt/tradingbot/data/state \
        /opt/tradingbot/venv/bin/python /opt/tradingbot/app/scripts/box_cf_gap_audit.py \
        --config /opt/tradingbot/app/config.yaml --json-out /tmp/box_gap_20261001.json

Inputs (relative to --state; R = required, O = optional)
  R <book>/counterfactual_trades.json   CF rows (+ meta counters); <book>/counterfactual_archive/ is counted, never read
  R <book>/futures_ledger.json          closed trades (history[], fills[], features) and open positions{} (occupancy only)
  O <book>/trade_memory.jsonl           entry rows: trade_id → signal_ts, signal_close, stop_at_entry
  O learning_mode.json · box_timer.json · protective_monitor.json · <book>.json · monitoring_gaps.jsonl
  O shared_experience/experience.jsonl  xp_cf / xp_entry snapshots (h4_trend, h4_vol_regime, btc_h4_trend)
  O shadow_book.json                    main-bot counterfactuals (S-MAIN)
  O --config (entry_universe.symbols order) · --replay-klines DIR (<SYMBOL>-5m-YYYY-MM-DD.csv, Binance public data)

Populations: CF_NET = CF rows with a finite outcome.r_net and net_status OK. REAL_AFTER = real trades with a
features.learning dict or opened_at ≥ learning_since (scripts/bot_scorecard.py rule). W = [min, max] CF created_at;
REAL_W = REAL_AFTER opened inside W. Gross-at-level R and cost parts of a real trade use the identity of
learning_cf._decompose with view.stop = initial_stop.

Verdict per hypothesis (pre-registered): the hypothesis gives an adjusted gap. shrink = (|raw| − |adjusted|) / |raw|.
  AÇIKLIYOR shrink ≥ 50 % and the adjusted gap's day-cluster CI contains 0 · KISMEN shrink ≥ 20 % · AÇIKLAMIYOR < 20 %
  VERİ AZ   fewer than 30 CF rows, 30 real rows or 3 UTC days in the compared cells (a single day: CI flagged 'tek gün')
Adjusted gap per hypothesis:
  H1 within-cell gap, post-stratified on (side, stop bucket)        H2 CF episode means vs real, post-stratified on day
  H3 CF without POSITION_OPEN rows                                  H4 CF minus pooled real stop overshoot on CF stop exits
  H5 CF minus the entry-bar (B1) upper-bound bias                   H6 post-stratified on (book occupancy band, side)
  H7 real without re-entries after a same-day stop and 23:50 signals
  H8 CF with the missing 00:00 funding imputed (real funding % of notional of the same symbol/side/midnight → side/midnight
     → side, divided by the CF stop %)
  H9 real with wrong-side 'hedef' exits counted as 0 R               H10 real without exits decided by closed 1h bars
--pair-window-min: H6 time-matched gap (each CF vs real trades of the same side opened within ± that many minutes).

Output: a Turkish summary (≤ 30 lines, decimal comma, ends with 'Sonuç:'), English fixed-width detail tables, the same
'Sonuç:' line last, and the --json-out document (floats rounded to 4 decimals; ids/symbols only in top-N lists).
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True                     # read-only run: no __pycache__ next to /tmp/box_cf_gap_audit.py

import argparse  # noqa: E402
import bisect  # noqa: E402
import csv  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402
import statistics  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Iterable, Optional  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "tradingbot" / "__init__.py").is_file() and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))                      # run from <app>/scripts: the repo is the parent directory

SCHEMA = "box_cf_gap_audit_v1"
DEFAULT_BOOK = "strategy_paper_box"
ENV_STATE = "TRADINGBOT_STATE_DIR"
CF_FILE = "counterfactual_trades.json"
CF_ARCHIVE_DIR = "counterfactual_archive"
LEDGER_FILE = "futures_ledger.json"
MEMORY_FILE = "trade_memory.jsonl"
LEARNING_FILE = "learning_mode.json"
BOX_TIMER_FILE = "box_timer.json"
PM_FILE = "protective_monitor.json"
GAPS_FILE = "monitoring_gaps.jsonl"
XP_FILE = "shared_experience/experience.jsonl"
SHADOW_FILE = "shadow_book.json"
#: JSON inputs larger than this are not loaded (skipped with a message); tests monkeypatch it.
MAX_JSON_BYTES = 200 * 1024 * 1024

M5_MS = 300_000
DAY_MS = 86_400_000
SIG_2350_MS = 85_800_000                               # 23:50 UTC bar open (time of day)
STOP_EXITS = ("stop", "başa-baş stop")
CAPACITY = frozenset({"INSUFFICIENT_MARGIN", "MIN_ORDER_CONFLICT", "MIN_NOTIONAL", "MIN_QTY", "STEP_ZERO_QTY", "MAX_QTY",
                      "LEVERAGE_TOO_HIGH"})
BUCKETS = (("B0", 0.5), ("B1", 1.0), ("B2", 2.22), ("B3", 4.0))
BUCKET_RANGE = {"B0": "<0.5%", "B1": "0.5-1%", "B2": "1-2.22%", "B3": "2.22-4%", "B4": ">=4%", "?": "unknown"}
HOUR_BANDS = ("00-06", "06-12", "12-18", "18-24")
MTE_BANDS = ("<60", "60-180", "180-360", ">=360")
OCC_BANDS = ("0-9", "10-19", "20-29", "30-39", "40+")
RANK_BANDS = ("1-10", "11-20", "21-30", "31+", "?")
WIN_R = 0.25
WIDE_STOP_PCT = 2.22
MIN_N = 30
MIN_DAYS = 3
H2_MIN_DAY_ROWS = 5
V_YES, V_PART, V_NO, V_THIN = "AÇIKLIYOR", "KISMEN", "AÇIKLAMIYOR", "VERİ AZ"
NO_DATA = "VERİ YOK"
SINGLE_DAY = "tek gün"
H_TITLES = {"H1": "stop/maliyet bileşimi", "H2": "dönem ve tekrar sayım", "H3": "POSITION_OPEN alt kümesi",
            "H4": "stop taşması (seviyeden dolum)", "H5": "giriş barı (B1) görülmüyor", "H6": "kalabalık defter/saat/sıra",
            "H7": "yalnız gerçekte olan sinyaller", "H8": "gün sonu ve funding", "H9": "yanlış taraf hedef",
            "H10": "fiyat kaynağı"}
H_ADJ = {"H1": "within-cell gap, post-stratified on (side, stop bucket)",
         "H2": "CF episode means vs real, post-stratified on UTC day (days with >=5 rows on both sides)",
         "H3": "CF without POSITION_OPEN rows", "H4": "CF minus pooled real stop overshoot on CF stop exits",
         "H5": "CF minus the entry-bar (B1) upper-bound bias", "H6": "post-stratified on (book occupancy band, side)",
         "H7": "real without re-entries after a same-day stop and 23:50 signals",
         "H8": "CF with the missing 00:00 funding imputed", "H9": "real with wrong-side 'hedef' exits counted as 0 R",
         "H10": "real without exits decided by closed 1h bars"}


class AuditError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code, self.msg = code, msg


# ----------------------------------------------------------------------------- small helpers
def fnum(x: Any) -> Optional[float]:
    """Decimal strings / numbers → float; anything else (None, "", garbage, NaN, bool) → None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_ts(x: Any) -> Optional[datetime]:
    """ISO-8601 string (+00:00 / Z / naive) or epoch ms/s → aware UTC datetime; unreadable → None."""
    if x is None or x == "" or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
        if not math.isfinite(v) or v <= 0:
            return None
        if v > 1e11:
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(x).strip()
    if re.fullmatch(r"\d{10,13}", s):
        return parse_ts(int(s))
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})\.(\d+)(.*)$", s)
    if m and len(m.group(2)) != 6:
        s = "%s.%s%s" % (m.group(1), (m.group(2) + "000000")[:6], m.group(3))
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ts_ms(x: Any) -> Optional[int]:
    dt = parse_ts(x)
    return int(round(dt.timestamp() * 1000)) if dt else None


def iso_ms(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def day_start(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def as_dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def side_sign(x: Any) -> int:
    return -1 if str(x or "").upper() in ("SHORT", "SELL") else 1


def side_name(s: int) -> str:
    return "LONG" if s > 0 else "SHORT"


def stop_bucket(sp: Optional[float]) -> str:
    """Stop-distance bucket: B0 <0.5 · B1 0.5-1 · B2 1-2.22 · B3 2.22-4 · B4 ≥4 (% of the entry fill)."""
    if sp is None:
        return "?"
    for name, hi in BUCKETS:
        if sp < hi:
            return name
    return "B4"


def hour_band(hour: Optional[int]) -> str:
    return "?" if hour is None else HOUR_BANDS[min(3, max(0, int(hour) // 6))]


def mte_band(m: Optional[float]) -> str:
    if m is None:
        return "?"
    return "<60" if m < 60 else ("60-180" if m < 180 else ("180-360" if m < 360 else ">=360"))


def occ_band(n: Optional[int]) -> str:
    return "?" if n is None else (OCC_BANDS[min(4, n // 10)])


def rank_band(rank: Optional[int]) -> str:
    if rank is None:
        return "?"
    return "1-10" if rank <= 10 else ("11-20" if rank <= 20 else ("21-30" if rank <= 30 else "31+"))


def exit_class(reason: Any) -> str:
    r = str(reason or "").strip()
    if r in STOP_EXITS or r in ("breakeven_stop",):
        return "STOP"
    if r.lower().startswith(("hedef", "target")):
        return "TARGET"
    if r in ("BOX_EOD_FLAT", "horizon"):
        return "EOD"
    if r == "likidasyon":
        return "LIQ"
    return "OTHER" if r else "?"


def cf_family(reason: Any) -> str:
    code = str(reason or "").split(":")[0].strip().upper()
    if code in CAPACITY:
        return "CAPACITY"
    if code in ("POSITION_OPEN", "KILL_SWITCH_ACTIVE"):
        return code
    return "OTHER"


def rule_ms(rule_version: Any) -> Optional[float]:
    m = re.search(r"_ms([0-9.]+)", str(rule_version or ""))
    if not m:
        return None
    return fnum(m.group(1).rstrip("."))


def box_eod_bars(sig_ms: Optional[int], now_ms: int) -> int:
    """Reimplementation of strategy_paper._box_eod_bars: 5m bars from the first bar after `now` to the signal day's last
    (23:55) bar; 0 when the signal day is over (no counterfactual is written)."""
    if sig_ms is None:
        return 0
    eod = sig_ms - sig_ms % DAY_MS + DAY_MS
    first = (int(now_ms) // M5_MS + 1) * M5_MS
    return max(0, (eod - first) // M5_MS)


def b1_end(opened_ms: int) -> int:
    """End of the 5m bar that contains the entry (the counterfactual labeller never walks this bar)."""
    return (int(opened_ms) // M5_MS) * M5_MS + M5_MS


def overshoot(side: int, entry_fill: Optional[float], entry_ref: Optional[float], stop: Optional[float],
              fill_ref: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Stop overshoot of a real stop exit: (side·(stop − first sampled price) / entry_fill × 100, the same in R of the
    reference risk |entry_ref − stop| = learning_cf._decompose exit_fill_model)."""
    if None in (entry_fill, stop, fill_ref) or not entry_fill:
        return None, None
    pct = side * (stop - fill_ref) / entry_fill * 100.0
    ref = entry_ref if entry_ref is not None else entry_fill
    risk = abs(ref - stop)
    return pct, (side * (stop - fill_ref) / risk if risk > 0 else None)


def occupancy_at(opens: list, closes: list, t: Optional[int], own: bool = False) -> Optional[int]:
    """Real Box positions with opened_at ≤ t < closed_at (sorted open / close times; open positions have no close);
    `own`: the row is itself one of them and is not counted."""
    if t is None:
        return None
    n = bisect.bisect_right(opens, t) - bisect.bisect_right(closes, t)
    return max(0, n - (1 if own else 0))


def stopped_in_b1(opened_ms: Optional[int], closed_ms: Optional[int], is_stop: bool) -> bool:
    return bool(is_stop and opened_ms is not None and closed_ms is not None and closed_ms < b1_end(opened_ms))


def episodes(rows: list) -> dict:
    """(symbol, signal day, side) → rows in time order (one market move on one symbol)."""
    ep: dict = defaultdict(list)
    for r in sorted(rows, key=lambda x: (x["t0"] or 0, x["id"])):
        ep[(r["sym"], r["day"], r["side"])].append(r)
    return dict(ep)


def stop_clusters(rows: list) -> Counter:
    """(symbol, side, stop rounded to 10 decimals) → count (successive records sharing one stop level)."""
    return Counter((r["sym"], r["side"], None if r["stop"] is None else round(r["stop"], 10)) for r in rows)


def held_index(real_rows: list, positions: list) -> dict:
    """symbol → [(opened_ms, closed_ms or +inf, row)] over every real trade and every still-open position."""
    idx: dict = defaultdict(list)
    for r in real_rows:
        if r["t0"] is not None:
            idx[r["sym"]].append((r["t0"], r["closed"] if r["closed"] is not None else float("inf"), r))
    for p in positions:
        if p["t0"] is not None:
            idx[p["sym"]].append((p["t0"], float("inf"), dict(p, xcls="OPEN", r=None, kind="open")))
    return dict(idx)


def held_trade(index: dict, sym: str, t0: Optional[int]) -> Optional[dict]:
    """The real trade holding `sym` at t0 (opened_at ≤ t0 < closed_at, or still open); the latest if several."""
    if t0 is None:
        return None
    cands = [x for x in index.get(sym, []) if x[0] <= t0 < x[1]]
    return max(cands, key=lambda x: x[0])[2] if cands else None


def h7_flags(real_rows: list) -> None:
    """reentry: an earlier real trade on the same symbol, side and signal day closed with a stop before this one opened;
    late2350: the signal bar is the 23:50 bar (no counterfactual can exist: horizon 0) or the trade opened ≥ 23:50."""
    stops: dict = defaultdict(list)
    for r in real_rows:
        if r.get("exit") == "stop" and r["closed"] is not None:
            stops[(r["sym"], r["side"], r["day"])].append(r["closed"])
    for r in real_rows:
        r["reentry"] = bool(r["t0"] is not None and any(c <= r["t0"] for c in stops.get((r["sym"], r["side"], r["day"]), [])))
        r["late2350"] = bool((r["sig"] is not None and r["sig"] % DAY_MS == SIG_2350_MS)
                             or (r["t0"] is not None and r["t0"] % DAY_MS >= SIG_2350_MS))
        r["h7flag"] = r["reentry"] or r["late2350"]


def wrong_side(row: dict) -> bool:
    """A 'hedef' exit on the losing side of the entry: side · (exit_price − entry) < 0 (instant target loss)."""
    return bool(str(row.get("exit") or "").startswith("hedef") and row.get("exit_price") is not None
                and row.get("entry") is not None and row["side"] * (row["exit_price"] - row["entry"]) < 0)


def impute_funding(cf_rows: list, real_rows: list) -> Counter:
    """CF rows with funding_complete False miss the 00:00 settlement at the end of their horizon. Impute it from the real
    00:00 settlements (% of notional) of the same symbol/side/midnight → side/midnight → side, × funding_missing, divided
    by the CF stop %; sets row["fund_imp"] (R, signed like r_net). Returns the count per imputation level."""
    by_ssm: dict = defaultdict(list)
    by_sm: dict = defaultdict(list)
    by_s: dict = defaultdict(list)
    for r in real_rows:
        for mid, p in r.get("mid_fund") or []:
            by_ssm[(r["sym"], r["side"], mid)].append(p)
            by_sm[(r["side"], mid)].append(p)
            by_s[r["side"]].append(p)
    levels: Counter = Counter()
    for r in cf_rows:
        r["fund_imp"] = 0.0
        if r.get("fund_ok") is not False or not r.get("sp") or not r.get("day"):
            continue
        mid = day_start(r["day"]) + DAY_MS
        for lvl, src in (("symbol_side_midnight", by_ssm.get((r["sym"], r["side"], mid))),
                         ("side_midnight", by_sm.get((r["side"], mid))), ("side", by_s.get(r["side"]))):
            if src:
                r["fund_imp"] = mean(src) * max(1, r.get("fund_missing") or 1) / r["sp"]
                levels[lvl] += 1
                break
        else:
            levels["none"] += 1
    return levels


def mean(xs: Iterable[float]) -> Optional[float]:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def quantile(sorted_xs: list, q: float) -> Optional[float]:
    if not sorted_xs:
        return None
    pos = q * (len(sorted_xs) - 1)
    i = int(math.floor(pos))
    j = min(i + 1, len(sorted_xs) - 1)
    return sorted_xs[i] + (sorted_xs[j] - sorted_xs[i]) * (pos - i)


def gstats(rows: list, val: Callable[[dict], Optional[float]] = lambda r: r.get("r")) -> dict:
    """n, mean, median, 10 % trimmed mean, win rate (r ≥ 0.25), stop rate, top-5 share of Σr."""
    vs = [v for v in (val(r) for r in rows) if v is not None]
    n = len(vs)
    if not n:
        return {"n": 0, "mean": None, "median": None, "trim10": None, "win_rate": None, "stop_rate": None,
                "top5_share": None, "sum": 0.0}
    s = sorted(vs)
    tot = sum(vs)
    k = int(n * 0.1)
    trim = s[k:n - k] or s
    top5 = sum(s[-5:])
    return {"n": n, "mean": tot / n, "median": statistics.median(s), "trim10": sum(trim) / len(trim),
            "win_rate": sum(1 for v in vs if v >= WIN_R) / n,
            "stop_rate": sum(1 for r in rows if r.get("is_stop")) / len(rows) if rows else None,
            "top5_share": (top5 / tot) if tot > 0 else None, "sum": tot}


# ----------------------------------------------------------------------------- bootstrap
def _rng(seed: int, label: str) -> random.Random:
    return random.Random("%s:%s" % (seed, label))


def ci95(reps: Optional[list]) -> Optional[list]:
    if not reps:
        return None
    s = sorted(reps)
    return [quantile(s, 0.025), quantile(s, 0.975)]


def boot_iid(vals: list, B: int, rng: random.Random) -> Optional[list]:
    n = len(vals)
    if n < 2:
        return None
    ch = rng.choices
    return ci95([sum(ch(vals, k=n)) / n for _ in range(B)])


def _merge(per: dict, draw: dict) -> dict:
    out: dict = {}
    for c, w in draw.items():
        for k, v in per[c].items():
            out[k] = out.get(k, 0.0) + w * v
    return out


def boot_cluster(per: dict, stat: Callable[[dict], Optional[dict]], B: int, rng: random.Random) -> tuple:
    """Cluster bootstrap of a statistic over per-cluster sums: (point dict, list of replicate dicts | None when < 2
    clusters). Clusters are resampled with replacement; every sum of a drawn cluster enters with its multiplicity, so
    gaps resample both sides jointly."""
    keys = sorted(per)
    point = stat(_merge(per, {c: 1 for c in keys})) if keys else None
    if len(keys) < 2 or point is None:
        return point, None
    undefined = [k for k, v in point.items() if v is None]
    reps = []
    for _ in range(B):
        draw = Counter(rng.choices(keys, k=len(keys)))
        v = stat(_merge(per, draw))
        if v is not None:
            for k in undefined:                        # undefined at the point estimate → no interval either
                v[k] = None
            reps.append(v)
    return point, reps


def rep_ci(reps: Optional[list], fn: Callable[[dict], Optional[float]]) -> Optional[list]:
    if reps is None:
        return None
    vals = []
    for d in reps:
        try:
            v = fn(d)
        except (KeyError, TypeError, ZeroDivisionError):
            v = None
        if v is not None:
            vals.append(v)
    return ci95(vals) if len(vals) >= 2 else None


def _add(d: dict, key: tuple, v: float) -> None:
    d[key] = d.get(key, 0.0) + v


def aggs(sides: dict, cluster_fn: Callable[[dict], Any], feeders: list) -> dict:
    """Per-cluster sums: every feeder(side, row, dict) adds its numbers to the row's cluster."""
    per: dict = defaultdict(dict)
    for side, rows in sides.items():
        for r in rows:
            c = cluster_fn(r)
            if c is None:
                continue
            d = per[c]
            for f in feeders:
                f(side, r, d)
    return dict(per)


def feed_mean(prefix: str, val: Callable[[dict], Optional[float]] = lambda r: r.get("r"),
              include: Callable[[str, dict], bool] = lambda s, r: True,
              cell: Callable[[dict], Any] = lambda r: "*") -> Callable:
    def f(side: str, r: dict, d: dict) -> None:
        if not include(side, r):
            return
        v = val(r)
        if v is None:
            return
        k = cell(r)
        if k is None:
            return
        _add(d, (prefix, side, k, "s"), v)
        _add(d, (prefix, side, k, "n"), 1.0)
    return f


def cells_of(d: dict, prefix: str) -> dict:
    """{side: {cell: [sum, n]}} of one prefix."""
    out: dict = {"cf": {}, "re": {}}
    for k, v in d.items():
        if k[0] != prefix:
            continue
        slot = out[k[1]].setdefault(k[2], [0.0, 0.0])
        slot[0 if k[3] == "s" else 1] += v
    return out


def mean_of(d: dict, prefix: str, side: str) -> Optional[float]:
    s = n = 0.0
    for cell, (cs, cn) in cells_of(d, prefix)[side].items():
        s, n = s + cs, n + cn
    return s / n if n > 0 else None


def poststrat(d: dict, prefix: str, min_cell: float) -> Optional[dict]:
    """Post-stratification over the cells of `prefix`: cells with n_cf ≥ 1 and n_real ≥ min_cell are covered;
    w_K = n_cf,K / Σ covered n_cf; reweighted real = Σ w_K · mean_real,K; within = mean_cf(covered) − reweighted real;
    mix = reweighted real − raw real mean; coverage = covered CF rows / all CF rows."""
    c = cells_of(d, prefix)
    cf, re_ = c["cf"], c["re"]
    n_cf_all = sum(v[1] for v in cf.values())
    n_re_all = sum(v[1] for v in re_.values())
    if n_cf_all <= 0 or n_re_all <= 0:
        return None
    cov = [k for k, v in cf.items() if v[1] >= 1 and re_.get(k, [0.0, 0.0])[1] >= min_cell]
    n_cov = sum(cf[k][1] for k in cov)
    if n_cov <= 0:
        return None
    raw_cf = sum(v[0] for v in cf.values()) / n_cf_all
    raw_re = sum(v[0] for v in re_.values()) / n_re_all
    cf_cov = sum(cf[k][0] for k in cov) / n_cov
    rw = sum(cf[k][1] / n_cov * (re_[k][0] / re_[k][1]) for k in cov)
    return {"raw_gap": raw_cf - raw_re, "within": cf_cov - rw, "mix": rw - raw_re, "coverage": n_cov / n_cf_all,
            "reweighted_real": rw, "cf_covered_mean": cf_cov, "cells": len(cov),
            "n_cf_covered": n_cov, "n_real_covered": sum(re_[k][1] for k in cov)}


def plain_gap(d: dict, prefix: str) -> Optional[float]:
    a, b = mean_of(d, prefix, "cf"), mean_of(d, prefix, "re")
    return None if a is None or b is None else a - b


def verdict(raw: Optional[float], adj: Optional[float], adj_ci: Optional[list], *, n_cf: int, n_real: int,
            n_days: int) -> tuple[str, Optional[float]]:
    """Pre-registered rule → (verdict word, shrink)."""
    if adj is None or raw is None or n_cf < MIN_N or n_real < MIN_N or n_days < MIN_DAYS:
        return V_THIN, None
    if abs(raw) < 1e-12:
        return V_NO, None
    shrink = (abs(raw) - abs(adj)) / abs(raw)
    if shrink >= 0.5 and adj_ci is not None and adj_ci[0] <= 0.0 <= adj_ci[1]:
        return V_YES, shrink
    if shrink >= 0.2:
        return V_PART, shrink
    return V_NO, shrink


# ----------------------------------------------------------------------------- inputs
def file_info(path: Path) -> Optional[dict]:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"bytes": st.st_size, "mtime": iso_ms(int(st.st_mtime * 1000))}


def load_json(path: Path, *, required: bool, warn: list, hook: Optional[Callable[[dict], Any]] = None) -> Any:
    """Whole-file JSON with the size guard. Missing → None (required: AuditError 2); too big → skipped with a message
    (required: AuditError 2); malformed → required: AuditError 3, optional: None + warning. `hook` (json object_hook)
    reduces each record to its slim row while parsing, so the raw nested dicts never pile up."""
    if not path.is_file():
        if required:
            raise AuditError(2, "gerekli dosya yok: %s" % path)
        return None
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        msg = "%s çok büyük (%.1f MB > %.0f MB sınırı); okunmadı" % (path.name, size / 1e6, MAX_JSON_BYTES / 1e6)
        if required:
            raise AuditError(2, "gerekli dosya atlandı: " + msg)
        warn.append(msg)
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh, object_hook=hook)
    except (OSError, ValueError) as exc:
        if required:
            raise AuditError(3, "gerekli dosya bozuk: %s (%s)" % (path, type(exc).__name__))
        warn.append("%s okunamadı (%s)" % (path.name, type(exc).__name__))
        return None


SLIM = "__slim__"


def _is_trade_record(d: dict) -> bool:
    return "exit_reason" in d and "closed_at" in d and "opened_at" in d and "symbol" in d


def _is_shadow_trade(d: dict) -> bool:
    return "reason_not_opened" in d and "created_at" in d and "symbol" in d


def ledger_hook(d: dict) -> Any:
    """futures_ledger.json object_hook: every closed trade record becomes its slim row at once."""
    return {SLIM: "real", "row": real_row(d)} if _is_trade_record(d) else d


def cf_hook(d: dict) -> Any:
    """counterfactual_trades.json object_hook: slim CF row + the scorecard rule's view of the outcome (labelled = numeric
    r_multiple; net = numeric r_net) for the sanity line."""
    if not _is_shadow_trade(d):
        return d
    row = cf_row(d)
    o = d.get("outcome")
    row["sc_lab"] = isinstance(o, dict) and fnum(o.get("r_multiple")) is not None
    row["sc_net"] = fnum(o.get("r_net")) if row["sc_lab"] else None
    return {SLIM: "cf", "row": row}


def shadow_hook(d: dict) -> Any:
    """shadow_book.json object_hook: main-bot counterfactual → the few S-MAIN fields."""
    if not _is_shadow_trade(d):
        return d
    o = d.get("outcome")
    rs = d.get("reason_not_opened") or []
    return {SLIM: "main", "book": d.get("book"), "lab": isinstance(o, dict), "r": fnum(as_dict(o).get("r_net")),
            "chief": bool(rs) and isinstance(rs, list) and str(rs[0]).upper().startswith("CHIEF"),
            "mt": str(d.get("market_type") or "?").upper(), "t0": ts_ms(d.get("created_at"))}


def unslim(items: Any, kind: str, make: Callable[[dict], dict]) -> tuple:
    """(slim rows, number of non-object entries) of a list parsed with one of the hooks above."""
    rows, bad = [], 0
    for x in items if isinstance(items, list) else []:
        if isinstance(x, dict) and x.get(SLIM) == kind:
            rows.append(x["row"] if "row" in x else x)
        elif isinstance(x, dict):
            rows.append(make(x))
        else:
            bad += 1
    return rows, bad


def iter_jsonl(path: Path, needles: tuple = (), skip: Optional[Callable[[str], bool]] = None) -> Iterable[dict]:
    """Rows of a JSONL file, streamed; blank/malformed lines skipped; `needles`: cheap substring pre-filter (any);
    `skip(line)`: True → the line is not parsed at all."""
    if not path.is_file():
        return
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if needles and not any(n in line for n in needles):
                continue
            if skip is not None and skip(line):
                continue
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _sig_from_key(key: Any) -> Optional[int]:
    m = re.fullmatch(r"(signal_ts|signal_close_ms):(-?\d+)", str(key or ""))
    if not m:
        return None
    v = int(m.group(2))
    return v if m.group(1) == "signal_ts" else v - M5_MS


def _time_fields(row: dict, t0: Optional[int], sig: Optional[int]) -> None:
    row["t0"], row["sig"] = t0, sig
    ref = sig if sig is not None else t0
    row["day"] = day_of(ref) if ref is not None else None
    if t0 is not None:
        row["hour"] = datetime.fromtimestamp(t0 / 1000.0, tz=timezone.utc).hour
        eod = day_start(row["day"]) + DAY_MS
        row["mte"] = (eod - t0) / 60000.0
    else:
        row["hour"], row["mte"] = None, None
    row["hb"], row["mteb"] = hour_band(row["hour"]), mte_band(row["mte"])


def cf_row(t: dict) -> dict:
    """One counterfactual record → slim row (only the fields the sections use)."""
    o = t.get("outcome") if isinstance(t.get("outcome"), dict) else None
    oc = o or {}
    feats = as_dict(t.get("features"))
    lrn = as_dict(feats.get("learning"))
    reasons = t.get("reason_not_opened") or []
    reason = str(reasons[0]) if isinstance(reasons, list) and reasons else ""
    side = side_sign(t.get("direction"))
    entry_ref, stop = fnum(t.get("entry")), fnum(t.get("stop"))
    entry_fill = fnum(oc.get("entry_fill")) or entry_ref
    sp = abs(entry_fill - stop) / entry_fill * 100.0 if (entry_fill and stop is not None) else None
    r = fnum(oc.get("r_net"))
    r_gross = fnum(oc.get("r_gross"))
    if r_gross is None:
        r_gross = fnum(oc.get("r_multiple"))
    parts = {k: fnum(v) for k, v in as_dict(oc.get("cost_parts_r")).items()}
    gl = cost = None
    if r is not None and r_gross is not None and parts and parts.get("path") is not None:
        gl = r_gross - parts["path"]
        cost = gl - r
    row = {"kind": "cf", "id": str(t.get("id") or ""), "sym": str(t.get("symbol") or "?"), "side": side,
           "entry_ref": entry_ref, "entry_fill": entry_fill, "stop": stop, "sp": sp, "bk": stop_bucket(sp),
           "r": r, "r_gross": r_gross, "gl": gl, "cost": cost, "parts": parts,
           "exit": oc.get("net_exit_reason"), "exit_gross": oc.get("exit_reason"),
           "is_stop": str(oc.get("net_exit_reason") or "") in STOP_EXITS, "basis": oc.get("net_exit_basis"),
           "bars": oc.get("bars") if isinstance(oc.get("bars"), int) else None,
           "fund_ok": oc.get("funding_complete") if isinstance(oc.get("funding_complete"), bool) else None,
           "fund_missing": oc.get("funding_missing") if isinstance(oc.get("funding_missing"), int) else None,
           "lv": oc.get("label_version") or ("cf_label_v1" if o is not None else None),
           "net_status": oc.get("net_status"), "pending": o is None, "approx_r": fnum(oc.get("r_net_approx")),
           "ambiguous": int(oc.get("intrabar_ambiguous_bars") or 0) if isinstance(oc.get("intrabar_ambiguous_bars"), int) else 0,
           "reason": reason.split(":")[0], "fam": cf_family(reason), "rule_ms": rule_ms(t.get("rule_version")),
           "bb": bool(feats.get("baseline_blocked_by")), "why": lrn.get("why"), "policy_grade": lrn.get("policy_grade"),
           "horizon": t.get("horizon_bars") if isinstance(t.get("horizon_bars"), int) else None}
    row["net_ok"] = r is not None and str(oc.get("net_status") or "") == "OK"
    _time_fields(row, ts_ms(t.get("created_at")), _sig_from_key(t.get("signal_key") or t.get("plan_id")))
    return row


def decompose_real(h: dict, stop0: Optional[float]) -> Optional[dict]:
    """learning_cf._decompose identity on a ledger history dict (view.stop = initial_stop), standard library only:
    a0 = gross-at-level R (last stop fill moved to exit_fill.stop), exit_fill_model = stop overshoot, entry_fill,
    exit_slippage, fees, funding (positive = cost) and e = (gross − fees + funding) / risk_fill (≙ r_multiple)."""
    fills = [f for f in (h.get("fills") or []) if isinstance(f, dict)]
    ents = [f for f in fills if f.get("kind") == "entry"]
    if not ents or stop0 is None:
        return None
    side = 1.0 if str(h.get("side") or "").upper() == "LONG" else -1.0
    ent = ents[0]
    exits = [f for f in fills if f.get("kind") != "entry"]
    q0 = fnum(h.get("quantity"))
    e_fill = fnum(ent.get("price"))
    e_ref = fnum(ent.get("ref_price")) if ent.get("ref_price") is not None else e_fill
    if not q0 or e_fill is None or e_ref is None:
        return None
    risk_ref, risk_fill = abs(e_ref - stop0) * q0, abs(e_fill - stop0) * q0
    if risk_ref <= 0 or risk_fill <= 0:
        return None

    def ref_of(f: dict) -> float:
        return float(f["ref_price"] if f.get("ref_price") is not None else f["price"])
    try:
        g_rr = sum(side * (ref_of(f) - e_ref) * float(f["qty"]) for f in exits)
        g_rf = sum(side * (ref_of(f) - e_fill) * float(f["qty"]) for f in exits)
    except (KeyError, TypeError, ValueError):
        return None
    g_level = g_rr
    ef = as_dict(as_dict(h.get("features")).get("exit_fill"))
    if exits and str(h.get("exit_reason") or "") in STOP_EXITS and ef.get("stop") is not None \
            and exits[-1].get("ref_price") is not None:
        g_level = g_rr - side * (float(exits[-1]["ref_price"]) - float(ef["stop"])) * float(exits[-1]["qty"])
    gross, fees, fund = fnum(h.get("gross_pnl")), fnum(h.get("fees")), fnum(h.get("funding"))
    if gross is None or fees is None or fund is None:
        return None
    a0, a = g_level / risk_ref, g_rr / risk_ref
    b, c = g_rf / risk_fill, gross / risk_fill
    dd, e = (gross - fees) / risk_fill, (gross - fees + fund) / risk_fill
    return {"a0": a0, "e": e, "parts": {"exit_fill_model": a0 - a, "entry_fill": a - b, "exit_slippage": b - c,
                                        "fees": c - dd, "funding": dd - e}}


def real_row(h: dict, mem: Optional[dict] = None) -> dict:
    """One ledger history record → slim row (R, decomposition, timing, exit fill, learning cohort, funding). `mem`
    (trade_id → trade-memory entry) may also be attached later with `attach_memory`."""
    feats = as_dict(h.get("features"))
    lrn = feats.get("learning")
    side = side_sign(h.get("side"))
    fills = [f for f in (h.get("fills") or []) if isinstance(f, dict)]
    ent = next((f for f in fills if f.get("kind") == "entry"), {})
    exits = [f for f in fills if f.get("kind") != "entry"]
    entry_fill = fnum(ent.get("price")) or fnum(h.get("entry"))
    entry_ref = fnum(ent.get("ref_price")) or entry_fill
    stop = fnum(feats.get("initial_stop"))
    sp = abs(entry_fill - stop) / entry_fill * 100.0 if (entry_fill and stop is not None) else None
    r = fnum(h.get("r_multiple"))
    dec = decompose_real(h, stop)
    ef = as_dict(feats.get("exit_fill"))
    exit_reason = str(h.get("exit_reason") or "")
    is_stop = exit_reason in STOP_EXITS
    fill_ref = None
    if is_stop:
        if ef.get("basis") == "GAP_FILL_AT_FIRST_OBSERVATION":
            fill_ref = fnum(ef.get("first_price"))
        if fill_ref is None and exits:
            fill_ref = fnum(exits[-1].get("ref_price"))
    stop_lvl = fnum(ef.get("stop")) if ef.get("stop") is not None else stop
    ov_pct, ov_r = overshoot(side, entry_fill, entry_ref, stop_lvl, fill_ref) if is_stop else (None, None)
    qty = fnum(h.get("quantity"))
    notional = entry_fill * qty if (entry_fill and qty) else None
    mid_fund = []
    for it in feats.get("funding_settlements") or []:
        it = as_dict(it)
        st = ts_ms(it.get("settlement"))
        amt = fnum(it.get("amount"))
        if st is not None and amt is not None and notional and st % DAY_MS == 0:
            mid_fund.append((st, amt / notional * 100.0))
    cov = as_dict(feats.get("funding_coverage"))
    row = {"kind": "re", "id": str(h.get("id") or ""), "sym": str(h.get("symbol") or "?"), "side": side,
           "entry_ref": entry_ref, "entry_fill": entry_fill, "stop": stop, "sp": sp, "bk": stop_bucket(sp), "r": r,
           "gl": dec["a0"] if dec else None, "cost": (dec["a0"] - r) if (dec and r is not None) else None,
           "parts": dec["parts"] if dec else {}, "mismatch": bool(dec and r is not None and abs(dec["e"] - r) > 1e-6),
           "decomposed": dec is not None, "exit": exit_reason, "xcls": exit_class(exit_reason), "is_stop": is_stop,
           "basis": ef.get("basis"), "first_source": ef.get("first_source"), "ov_pct": ov_pct, "ov_r": ov_r,
           "exit_price": fnum(h.get("exit_price")), "entry": fnum(h.get("entry")),
           "closed": ts_ms(h.get("closed_at")), "has_learning": isinstance(lrn, dict),
           "cohort": "extra" if (isinstance(lrn, dict) and lrn.get("learning_unlocked_by")) else "policy",
           "slots": as_dict(lrn).get("slots"), "fund_complete": cov.get("complete") if isinstance(cov.get("complete"), bool) else None,
           "notional": notional, "mid_fund": mid_fund, "memory": False}
    _time_fields(row, ts_ms(h.get("opened_at")), None)
    if mem:
        attach_memory(row, mem.get(row["id"]))
    return row


def attach_memory(row: dict, m: Optional[dict]) -> None:
    """Trade-memory entry → signal bar time (day, 23:50 flag, minutes to the day's end) and, when the ledger lacks
    features.initial_stop, the rule's stop_at_entry for the stop distance (no decomposition then)."""
    if not m:
        return
    row["memory"] = True
    if row["stop"] is None and m.get("stop_at_entry") is not None and row["entry_fill"]:
        row["stop"] = m["stop_at_entry"]
        row["sp"] = abs(row["entry_fill"] - row["stop"]) / row["entry_fill"] * 100.0
        row["bk"] = stop_bucket(row["sp"])
    if m.get("sig") is not None:
        _time_fields(row, row["t0"], m["sig"])


def load_memory(path: Path, wanted: dict) -> dict:
    """trade_memory.jsonl streamed: trade_id → {sig, signal_close, stop_at_entry} for the wanted ids only (kind None or
    'entry'). With repeated ids (ledger reset), the row recorded closest to the trade's opened_at wins."""
    out: dict = {}
    best: dict = {}
    tid_re = re.compile(r'"trade_id"\s*:\s*"([^"]*)"')

    def unwanted(line: str) -> bool:                   # most lines belong to other trades: skip them unparsed
        m = tid_re.search(line)
        return m is not None and m.group(1) not in wanted
    for row in iter_jsonl(path, needles=('"trade_id"',), skip=unwanted):
        if row.get("kind") not in (None, "entry"):
            continue
        tid = str(row.get("trade_id") or "")
        if tid not in wanted:
            continue
        feats = as_dict(row.get("features"))
        opened = wanted[tid]
        rec = ts_ms(row.get("recorded_at"))
        dist = abs(rec - opened) if (rec is not None and opened is not None) else float("inf")
        if tid in out and dist == float("inf"):
            continue                                   # undated duplicate: keep the first row
        if tid in best and dist >= best[tid]:
            continue
        sig = feats.get("signal_ts")
        sig_ms = int(sig) if isinstance(sig, (int, float)) and not isinstance(sig, bool) else ts_ms(sig)
        best[tid] = dist
        out[tid] = {"sig": sig_ms, "signal_close": fnum(feats.get("signal_close")),
                    "stop_at_entry": fnum(feats.get("stop_at_entry"))}
    return out


def load_config_ranks(path: Path, warn: list) -> Optional[dict]:
    try:
        from tradingbot.config import load_yaml_strict
    except Exception as exc:  # noqa: BLE001 — optional: the universe rank becomes VERİ YOK
        warn.append("--config: tradingbot.config içe aktarılamadı (%s); evren sırası yok" % type(exc).__name__)
        return None
    try:
        doc = load_yaml_strict(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        warn.append("--config okunamadı (%s); evren sırası yok" % type(exc).__name__)
        return None
    syms = as_dict(as_dict(doc).get("entry_universe")).get("symbols") or []
    ranks = {str(s): i + 1 for i, s in enumerate(syms) if isinstance(s, str)}
    if not ranks:
        warn.append("--config: entry_universe.symbols boş; evren sırası yok")
        return None
    return ranks


# ----------------------------------------------------------------------------- formatting
def ff(x: Optional[float], nd: int = 3, sign: bool = True) -> str:
    if x is None:
        return "-"
    return ("%+.*f" if sign else "%.*f") % (nd, x)


def tr(x: Optional[float], nd: int = 2, sign: bool = True) -> str:
    """Turkish decimal comma (summary only)."""
    return "—" if x is None else ff(x, nd, sign).replace(".", ",")


def tr_ci(ci: Optional[list], single: bool = False) -> str:
    if single:
        return "[GA %s]" % SINGLE_DAY
    if not ci:
        return "[GA —]"
    return "[GA %s..%s]" % (tr(ci[0]), tr(ci[1]))


def en_ci(ci: Optional[list], single: bool = False) -> str:
    if single:
        return "[single day]"
    return "[%s, %s]" % (ff(ci[0]), ff(ci[1])) if ci else "[-]"


def pct(x: Optional[float]) -> str:
    return "-" if x is None else "%.0f%%" % (x * 100.0)


def table(L: list, title: str, headers: list, rows: list) -> None:
    L.append("")
    L.append(title)
    if not rows:
        L.append("  (no rows)")
        return
    cols = [[str(h)] + [str(r[i]) for r in rows] for i, h in enumerate(headers)]
    w = [max(len(x) for x in c) for c in cols]
    L.append("  " + "  ".join(str(h).rjust(w[i]) if i else str(h).ljust(w[i]) for i, h in enumerate(headers)))
    for r in rows:
        L.append("  " + "  ".join(str(v).rjust(w[i]) if i else str(v).ljust(w[i]) for i, v in enumerate(r)))


def srow(name: str, st: dict) -> list:
    return [name, st["n"], ff(st["mean"]), ff(st["median"]), ff(st["trim10"]), pct(st["win_rate"]), pct(st["stop_rate"]),
            "-" if st["top5_share"] is None else "%.2f" % st["top5_share"]]


STAT_HDR = ["group", "n", "mean", "median", "trim10", "win", "stop", "top5"]


def r4(x: Any) -> Any:
    """JSON: floats rounded to 4 decimals (recursively), non-finite → None, tuple keys → strings."""
    if isinstance(x, bool) or x is None or isinstance(x, (int, str)):
        return x
    if isinstance(x, float):
        return round(x, 4) + 0.0 if math.isfinite(x) else None
    if isinstance(x, dict):
        return {(k if isinstance(k, str) else "|".join(str(p) for p in k) if isinstance(k, tuple) else str(k)): r4(v)
                for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [r4(v) for v in x]
    return str(x)


# ----------------------------------------------------------------------------- the audit
class Audit:
    def __init__(self, a: argparse.Namespace, *, now: Optional[datetime] = None):
        self.a = a
        self.now = now or datetime.now(timezone.utc)
        self.state = Path(a.state)
        self.bdir = self.state / a.book
        self.warn: list = []
        self.missing_optional: list = []
        self.inputs: dict = {}
        self.L: list = []                              # detail lines (English)
        self.rep: dict = {"schema": SCHEMA, "generated_at": iso_ms(int(self.now.timestamp() * 1000))}
        self.verdicts: list = []
        self.B = max(1, int(a.boot))
        self.seed = int(a.seed)
        self.min_cell = max(1, int(a.min_cell))
        self.ledger_cfg: dict = {}

    # -------------------------------------------------------------- loading
    def _note_input(self, path: Path, required: bool = False) -> bool:
        info = file_info(path)
        if info is not None:
            self.inputs[str(path)] = info
            return True
        if not required:
            self.missing_optional.append(str(path.relative_to(self.state)) if self._in_state(path) else str(path))
        return False

    def _in_state(self, p: Path) -> bool:
        try:
            p.resolve().relative_to(self.state.resolve())
            return True
        except ValueError:
            return False

    def load(self) -> None:
        a = self.a
        led_path, cf_path = self.bdir / LEDGER_FILE, self.bdir / CF_FILE
        self._note_input(led_path, True)
        self._note_input(cf_path, True)
        led = load_json(led_path, required=True, warn=self.warn, hook=ledger_hook)
        if not isinstance(led, dict) or not isinstance(led.get("history", []), list):
            raise AuditError(3, "gerekli dosya bozuk: %s (JSON nesnesi / history listesi değil)" % led_path)
        self.ledger_cfg = {k: led[k] for k in ("schema_version", "kind", "starting_equity", "wallet_balance", "max_positions",
                                               "enforce_position_cap", "fees", "slippage", "liq_params", "tax_policy",
                                               "tp1_fraction", "breakeven_at_mfe_r", "allow_shrink", "worst_case", "tp_maker")
                           if k in led}
        positions = []
        for sym, p in as_dict(led.get("positions")).items():
            p = as_dict(p)
            ep = fnum(p.get("entry_avg")) or fnum(p.get("entry"))
            ist = fnum(as_dict(p.get("features")).get("initial_stop")) or fnum(p.get("initial_stop")) or fnum(p.get("stop"))
            positions.append({"id": str(p.get("id") or ""), "sym": str(sym), "side": side_sign(p.get("side")),
                              "t0": ts_ms(p.get("opened_at")),
                              "sp": abs(ep - ist) / ep * 100.0 if (ep and ist) else None})
        self.real_all, bad = unslim(led.get("history"), "real", real_row)
        del led                                        # raw ledger dropped before the trade memory is streamed
        if bad:
            self.warn.append("%d defter geçmişi satırı nesne değil; atlandı" % bad)
        wanted = {r["id"]: r["t0"] for r in self.real_all if r["id"]}
        mem_path = self.bdir / MEMORY_FILE
        mem = load_memory(mem_path, wanted) if self._note_input(mem_path) else {}
        for r in self.real_all:
            attach_memory(r, mem.get(r["id"]))
        del mem
        self.positions = positions
        self.mem_joined = sum(1 for r in self.real_all if r["memory"])

        doc = load_json(cf_path, required=True, warn=self.warn, hook=cf_hook)
        if not isinstance(doc, dict) or not isinstance(doc.get("trades", []), list):
            raise AuditError(3, "gerekli dosya bozuk: %s (JSON nesnesi / trades listesi değil)" % cf_path)
        self.cf_meta = {k: v for k, v in as_dict(doc.get("meta")).items() if k != "book_counters"}
        self.cf_all, _bad = unslim(doc.get("trades"), "cf", lambda t: as_dict(cf_hook(t)).get("row") or cf_row(t))
        del doc
        self.n_cf_file = len(self.cf_all)
        # scorecard rule (scripts/bot_scorecard.counterfactual_card): labelled = numeric r_multiple; net = numeric r_net
        sc = [r["sc_net"] for r in self.cf_all if r.get("sc_lab") and r.get("sc_net") is not None]
        self.scorecard_mean = mean(sc)
        self.scorecard_n = len(sc)
        arch = self.bdir / CF_ARCHIVE_DIR
        self.archive_files = sum(1 for p in arch.rglob("*") if p.is_file()) if arch.is_dir() else None

        lm_path = self.state / LEARNING_FILE
        lm = load_json(lm_path, required=False, warn=self.warn) if self._note_input(lm_path) else None
        self.lm_since_file = as_dict(lm).get("since")
        bt_path = self.state / BOX_TIMER_FILE
        self.box_timer = as_dict(load_json(bt_path, required=False, warn=self.warn)) if self._note_input(bt_path) else {}
        pm_path = self.state / PM_FILE
        self.pm = as_dict(load_json(pm_path, required=False, warn=self.warn)) if self._note_input(pm_path) else {}
        sm_path = self.state / ("%s.json" % a.book)
        self.summary = as_dict(load_json(sm_path, required=False, warn=self.warn)) if self._note_input(sm_path) else {}
        gp = self.state / GAPS_FILE
        self.gaps = [r for r in iter_jsonl(gp) if str(r.get("book") or "") == a.book] if self._note_input(gp) else None
        self.ranks = None
        if a.config:
            cp = Path(a.config)
            self.inputs[str(cp)] = file_info(cp)
            self.ranks = load_config_ranks(cp, self.warn)

    def load_xp(self) -> None:
        """experience.jsonl (streamed): rev-0 snapshots of this book's CF rows (cf_id) and real entries (trade_id +
        opened_at). Only three strata are kept per row."""
        path = self.state / XP_FILE
        self.xp_cf: dict = {}
        self.xp_re: dict = {}
        self.xp_ok = self._note_input(path)
        if not self.xp_ok:
            return
        cf_ids = {r["id"] for r in self.cf_all}
        re_keys = {(r["id"], r["t0"]) for r in self.real_all}
        for row in iter_jsonl(path, needles=('"xp_cf"', '"xp_entry"')):
            if str(row.get("book") or "") != self.a.book:
                continue
            snap = as_dict(row.get("snapshot"))
            if not snap:
                continue
            keep = {k: snap.get(k) for k in ("h4_trend", "h4_vol_regime", "btc_h4_trend")}
            if row.get("kind") == "xp_cf" and str(row.get("cf_id") or "") in cf_ids:
                self.xp_cf[str(row["cf_id"])] = keep
            elif row.get("kind") == "xp_entry":
                k = (str(row.get("trade_id") or ""), ts_ms(row.get("opened_at")))
                if k in re_keys:
                    self.xp_re[k] = keep

    # -------------------------------------------------------------- populations
    def populations(self) -> None:
        a = self.a
        self.learning_since = ts_ms(a.learning_since) if a.learning_since else ts_ms(self.lm_since_file)
        self.learning_since_src = "--learning-since" if a.learning_since else (LEARNING_FILE if self.learning_since else None)
        since, until = ts_ms(a.since) if a.since else None, ts_ms(a.until) if a.until else None

        def in_filter(r: dict) -> bool:
            return r["t0"] is not None and (since is None or r["t0"] >= since) and (until is None or r["t0"] <= until)
        self.cf_in = [r for r in self.cf_all if in_filter(r)]
        self.cf = [r for r in self.cf_in if r["net_ok"]]
        self.cf_excluded = [r for r in self.cf_in if not r["net_ok"]]
        if a.slots_change:
            self.slots_change, self.slots_src = ts_ms(a.slots_change), "--slots-change"
        else:
            s40 = sorted(r["t0"] for r in self.real_all if r["slots"] == 40 and r["t0"] is not None)
            self.slots_change, self.slots_src = (s40[0], "first trade with features.learning.slots == 40") if s40 else (None, None)
        for r in self.real_all:
            r["after"] = r["has_learning"] or (self.learning_since is not None and r["t0"] is not None
                                               and r["t0"] >= self.learning_since)
            if not r["after"]:
                r["cohort"] = "before"
        for r in self.real_all + self.cf_all:
            r["slots_regime"] = ("all" if self.slots_change is None or r["t0"] is None
                                 else ("20" if r["t0"] < self.slots_change else "40"))
        self.real_after = [r for r in self.real_all if r["after"] and in_filter(r)]
        ts = [r["t0"] for r in self.cf] or [r["t0"] for r in self.cf_in]
        self.W = (min(ts), max(ts)) if ts else None
        self.re = [r for r in self.real_after if self.W and self.W[0] <= r["t0"] <= self.W[1] and r["r"] is not None]
        self.days = sorted({r["day"] for r in self.cf + self.re if r["day"]})
        self.decompose_mismatch = sum(1 for r in self.real_all if r["mismatch"])
        # occupancy / refill / ranks on every row
        intervals = [(r["t0"], r["closed"]) for r in self.real_all if r["t0"] is not None]
        intervals += [(p["t0"], None) for p in self.positions if p["t0"] is not None]
        self.opens = sorted(o for o, _c in intervals)
        self.closes = sorted(c for _o, c in intervals if c is not None)
        stops_by_side: dict = defaultdict(list)
        for r in self.real_all:
            if r["exit"] == "stop" and r["closed"] is not None:
                stops_by_side[r["side"]].append(r["closed"])
        for v in stops_by_side.values():
            v.sort()
        w = float(a.refill_window_min) * 60000.0
        for r in self.cf_all + self.real_all:
            r["occ"] = self.occupancy(r["t0"], own=(r["kind"] == "re"))
            r["occb"] = occ_band(r["occ"])
            rank = self.ranks.get(r["sym"]) if self.ranks else None
            r["rank"], r["rankb"] = rank, (rank_band(rank) if self.ranks else "?")
        for r in self.real_all:
            lst = stops_by_side.get(r["side"], [])
            r["refill"] = bool(r["t0"] is not None and bisect.bisect_left(lst, r["t0"]) > bisect.bisect_left(lst, r["t0"] - w))

    def occupancy(self, t: Optional[int], own: bool = False) -> Optional[int]:
        return occupancy_at(self.opens, self.closes, t, own)

    # -------------------------------------------------------------- generic evaluation
    def evaluate(self, h: str, point: Optional[dict], reps: Optional[list], *, n_cf: int, n_real: int, n_days: int,
                 single: bool, extra: Optional[dict] = None) -> dict:
        raw = point.get("raw") if point else None
        adj = point.get("adj") if point else None
        adj_ci = rep_ci(reps, lambda d: d["adj"])
        eff_ci = rep_ci(reps, lambda d: d["raw"] - d["adj"])
        word, shrink = verdict(raw, adj, adj_ci, n_cf=n_cf, n_real=n_real, n_days=n_days)
        if single:
            word = V_THIN
        v = {"h": h, "title_tr": H_TITLES[h], "adjustment": H_ADJ[h], "raw_gap": raw, "adjusted_gap": adj,
             "adjusted_ci95": adj_ci, "effect_r": (raw - adj) if (raw is not None and adj is not None) else None,
             "ci95": eff_ci, "ci_flag": SINGLE_DAY if single else None, "shrink": shrink, "verdict": word,
             "n_cf": n_cf, "n_real": n_real, "n_days": n_days}
        if extra:
            v.update(extra)
        self.verdicts.append(v)
        return v

    def day_boot(self, feeders: list, stat: Callable, label: str, cf_rows: Optional[list] = None,
                 re_rows: Optional[list] = None, cluster: Callable = lambda r: r["day"]) -> tuple:
        per = aggs({"cf": self.cf if cf_rows is None else cf_rows, "re": self.re if re_rows is None else re_rows},
                   cluster, feeders)
        point, reps = boot_cluster(per, stat, self.B, _rng(self.seed, label))
        return point, reps, len(per) == 1             # exactly one UTC day: no interval ('tek gün')

    # -------------------------------------------------------------- S0
    def s0(self) -> None:
        L = self.L
        lv = Counter(r["lv"] or "pending" for r in self.cf_all)
        exc: dict = defaultdict(list)
        for r in self.cf_excluded:
            exc[(r["lv"] or "-", r["net_status"] or "-", "pending" if r["pending"] else "labelled")].append(r)
        exc_rows = [{"label_version": k[0], "net_status": k[1], "state": k[2], "n": len(v),
                     "mean_r_gross": mean(x["r_gross"] for x in v if x["r_gross"] is not None)} for k, v in sorted(exc.items())]
        coh = Counter(r["cohort"] for r in self.real_all)
        cnt = as_dict(as_dict(self.summary.get("learning")).get("counters")) or as_dict(self.summary.get("counters"))
        rej = sorted(((str(k), int(fnum(v) or 0)) for k, v in as_dict(self.summary.get("rejections")).items()),
                     key=lambda kv: -kv[1])
        gaps = None
        if self.gaps is not None:
            gaps = {"n": len(self.gaps), "hours": sum(fnum(g.get("gap_s")) or 0.0 for g in self.gaps) / 3600.0}
        sanity_ok = (self.scorecard_mean is not None and mean(r["r"] for r in self.cf_all if r["net_ok"]) is not None
                     and abs(mean(r["r"] for r in self.cf_all if r["net_ok"]) - self.scorecard_mean) <= 1e-3)
        cf_net_all = [r for r in self.cf_all if r["net_ok"]]
        self.sanity = {"cf_net_mean_unfiltered": mean(r["r"] for r in cf_net_all), "n_cf_net_unfiltered": len(cf_net_all),
                       "scorecard_rule_mean": self.scorecard_mean, "scorecard_rule_n": self.scorecard_n,
                       "match_1e3": sanity_ok, "reference_2026_10_01": 0.4187}
        if not sanity_ok and self.scorecard_mean is not None:
            self.warn.append("CF_NET ortalaması karne kuralından farklı (%s / %s)" % (
                ff(self.sanity["cf_net_mean_unfiltered"], 4), ff(self.scorecard_mean, 4)))
        if self.archive_files is not None:
            self.warn.append("%s/ var (%d dosya); arşivlenmiş karşı-olgusal satırlar OKUNMADI" % (CF_ARCHIVE_DIR, self.archive_files))
        if self.decompose_mismatch:
            self.warn.append("gerçek işlem ayrıştırma uyuşmazlığı (decompose_mismatch): %d işlem" % self.decompose_mismatch)
        if len(self.days) < MIN_DAYS:
            self.warn.append("karşılaştırılan gün sayısı %d < %d: güven aralıkları anlamsız, hüküm VERİ AZ" % (len(self.days), MIN_DAYS))
        no_t0 = sum(1 for r in self.cf_all + self.real_all if r["t0"] is None)
        if no_t0:
            self.warn.append("%d satırın zamanı okunamadı (dışarıda bırakıldı)" % no_t0)
        self.rep["s0"] = {
            "cf_meta": self.cf_meta, "cf_rows_in_file": self.n_cf_file,
            "recorded_total_minus_rows": (int(self.cf_meta["recorded_total"]) - self.n_cf_file
                                          if isinstance(self.cf_meta.get("recorded_total"), int) else None),
            "label_version_histogram": dict(lv), "excluded": exc_rows,
            "archive": {"present": self.archive_files is not None, "files": self.archive_files, "read": False},
            "real_by_cohort": dict(coh), "real_memory_joined": self.mem_joined, "open_positions": len(self.positions),
            "window": [iso_ms(self.W[0]), iso_ms(self.W[1])] if self.W else None,
            "learning_since": iso_ms(self.learning_since), "learning_since_source": self.learning_since_src,
            "slots_change": iso_ms(self.slots_change), "slots_change_source": self.slots_src,
            "decompose_mismatch": self.decompose_mismatch, "sanity": self.sanity,
            "book_counters": {k: v for k, v in cnt.items() if isinstance(v, (int, float))},
            "rejections_top": dict(rej[:10]), "monitoring_gaps": gaps}
        L.append("== S0 inventory ==")
        for p, info in sorted(self.inputs.items()):
            if info:
                L.append("  input %-60s %12d bytes  mtime %s" % (p, info["bytes"], info["mtime"]))
        for p in self.missing_optional:
            L.append("  optional input missing: %s (%s)" % (p, NO_DATA))
        L.append("  CF meta: %s · rows in file %d · recorded_total - rows %s" % (
            ", ".join("%s=%s" % kv for kv in sorted(self.cf_meta.items()) if not isinstance(kv[1], dict)) or "-",
            self.n_cf_file, self.rep["s0"]["recorded_total_minus_rows"]))
        L.append("  label_version histogram: %s" % (", ".join("%s=%d" % kv for kv in sorted(lv.items())) or "-"))
        L.append("  archive: %s" % ("%d files present, NOT read" % self.archive_files if self.archive_files is not None
                                    else "none"))
        L.append("  real trades by cohort: %s · trade_memory joined %d/%d · open positions %d" % (
            ", ".join("%s=%d" % kv for kv in sorted(coh.items())) or "-", self.mem_joined, len(self.real_all), len(self.positions)))
        L.append("  window W: %s -> %s · learning_since %s (%s) · slots_change %s (%s)" % (
            iso_ms(self.W[0]) if self.W else "-", iso_ms(self.W[1]) if self.W else "-", iso_ms(self.learning_since) or "-",
            self.learning_since_src or "-", iso_ms(self.slots_change) or "-", self.slots_src or "-"))
        L.append("  populations: CF rows %d (after --since/--until %d) · CF_NET %d · excluded %d · REAL all %d · REAL_AFTER %d"
                 " · REAL_W %d · UTC days %d · decompose_mismatch %d" % (
                     len(self.cf_all), len(self.cf_in), len(self.cf), len(self.cf_excluded), len(self.real_all),
                     len(self.real_after), len(self.re), len(self.days), self.decompose_mismatch))
        L.append("  sanity: CF_NET mean %s (n %d) vs scorecard rule %s (n %d) -> %s (reference 2026-10-01 scorecard: 0.4187)" % (
            ff(self.sanity["cf_net_mean_unfiltered"], 4), len(cf_net_all), ff(self.scorecard_mean, 4), self.scorecard_n,
            "MATCH" if sanity_ok else "MISMATCH"))
        table(L, "  excluded CF rows (not in CF_NET)", ["label_version", "net_status", "state", "n", "mean_r_gross"],
              [[e["label_version"], e["net_status"], e["state"], e["n"], ff(e["mean_r_gross"])] for e in exc_rows])
        if cnt:
            L.append("  book counters: %s" % ", ".join("%s=%s" % kv for kv in sorted(cnt.items()) if isinstance(kv[1], (int, float))))
        if rej:
            L.append("  top rejections: %s" % ", ".join("%s=%d" % kv for kv in rej[:10]))
        L.append("  monitoring gaps (%s): %s" % (self.a.book, ("%d, %.1f h" % (gaps["n"], gaps["hours"])) if gaps else NO_DATA))

    # -------------------------------------------------------------- headline + H1
    def headline(self) -> None:
        B = self.B
        cf_v, re_v = [r["r"] for r in self.cf], [r["r"] for r in self.re]
        feeders = [feed_mean("raw")]

        def stat(d: dict) -> Optional[dict]:
            a, b = mean_of(d, "raw", "cf"), mean_of(d, "raw", "re")
            return {"cf": a, "re": b, "raw": (a - b) if (a is not None and b is not None) else None}
        point, reps, single = self.day_boot(feeders, stat, "headline")
        sd_point, sd_reps = boot_cluster(aggs({"cf": self.cf, "re": self.re}, lambda r: (r["sym"], r["day"]), feeders),
                                         stat, B, _rng(self.seed, "headline_symday"))
        self.head = {
            "cf": {**gstats(self.cf), "ci95_iid": boot_iid(cf_v, B, _rng(self.seed, "cf_iid")),
                   "ci95_day": rep_ci(reps, lambda d: d["cf"])},
            "real": {**gstats(self.re), "ci95_iid": boot_iid(re_v, B, _rng(self.seed, "re_iid")),
                     "ci95_day": rep_ci(reps, lambda d: d["re"])},
            "raw_gap": (point or {}).get("raw"), "raw_gap_ci95_day": rep_ci(reps, lambda d: d["raw"]),
            "raw_gap_ci95_symbol_day": rep_ci(sd_reps, lambda d: d["raw"]), "single_day": single}
        groups = [("CF_NET", self.cf), ("REAL_W", self.re),
                  ("REAL_W policy", [r for r in self.re if r["cohort"] == "policy"]),
                  ("REAL_W extra", [r for r in self.re if r["cohort"] == "extra"])]
        groups += [("CF %s" % f, [r for r in self.cf if r["fam"] == f]) for f in ("CAPACITY", "POSITION_OPEN", "KILL_SWITCH_ACTIVE", "OTHER")]
        groups += [("%s %s" % (lab, side_name(s_)), [r for r in src if r["side"] == s_])
                   for lab, src in (("CF", self.cf), ("REAL_W", self.re)) for s_ in (1, -1)]
        self.head["groups"] = {name: gstats(rows) for name, rows in groups if rows}
        L = self.L
        L.append("")
        L.append("== headline (net R; CI: iid / UTC-day cluster / symbol x day cluster) ==")
        L.append("  CF_NET n %d mean %s iid %s day %s · REAL_W n %d mean %s iid %s day %s" % (
            len(self.cf), ff(self.head["cf"]["mean"]), en_ci(self.head["cf"]["ci95_iid"]), en_ci(self.head["cf"]["ci95_day"], single),
            len(self.re), ff(self.head["real"]["mean"]), en_ci(self.head["real"]["ci95_iid"]),
            en_ci(self.head["real"]["ci95_day"], single)))
        L.append("  raw gap CF - REAL %s day %s · symbol x day %s" % (
            ff(self.head["raw_gap"]), en_ci(self.head["raw_gap_ci95_day"], single), en_ci(self.head["raw_gap_ci95_symbol_day"])))
        table(L, "  per group", STAT_HDR, [srow(name, st) for name, st in self.head["groups"].items()])
        self.rep["populations"] = {
            "cf_rows": len(self.cf_all), "cf_in_filter": len(self.cf_in), "cf_net": len(self.cf),
            "cf_excluded": len(self.cf_excluded), "real_all": len(self.real_all), "real_after": len(self.real_after),
            "real_w": len(self.re), "days": len(self.days), "window": [iso_ms(self.W[0]), iso_ms(self.W[1])] if self.W else None,
            "learning_since": iso_ms(self.learning_since), "slots_change": iso_ms(self.slots_change),
            "since": self.a.since, "until": self.a.until, "headline": self.head}

    def h1(self) -> None:
        L, mc = self.L, self.min_cell
        sb = lambda r: (side_name(r["side"]), r["bk"]) if r["bk"] != "?" else None  # noqa: E731
        feeders = [feed_mean("raw"), feed_mean("sb", cell=sb),
                   feed_mean("dsb", cell=lambda r: (r["day"], side_name(r["side"]), r["bk"]) if r["bk"] != "?" else None),
                   feed_mean("hsb", cell=lambda r: (r["hb"], side_name(r["side"]), r["bk"]) if r["bk"] != "?" else None),
                   feed_mean("gl", val=lambda r: r["gl"], include=lambda s, r: r["cost"] is not None),
                   feed_mean("cost", val=lambda r: r["cost"], include=lambda s, r: r["gl"] is not None),
                   feed_mean("glsb", val=lambda r: r["gl"], include=lambda s, r: r["cost"] is not None, cell=sb),
                   feed_mean("costsb", val=lambda r: r["cost"], include=lambda s, r: r["gl"] is not None, cell=sb)]

        def stat(d: dict) -> Optional[dict]:
            ps = poststrat(d, "sb", mc)
            raw = plain_gap(d, "raw")
            if raw is None:
                return None
            out = {"raw": raw, "adj": ps["within"] if ps else None, "mix": ps["mix"] if ps else None,
                   "coverage": ps["coverage"] if ps else None}
            for p in ("dsb", "hsb"):
                q = poststrat(d, p, mc)
                out[p] = q["within"] if q else None
            out["d_gl"], out["d_cost"] = plain_gap(d, "gl"), plain_gap(d, "cost")
            g, c = poststrat(d, "glsb", mc), poststrat(d, "costsb", mc)
            out["d_gl_within"], out["d_cost_within"] = (g or {}).get("within"), (c or {}).get("within")
            return out
        point, reps, single = self.day_boot(feeders, stat, "h1")
        def sd_stat(d: dict) -> Optional[dict]:
            q = poststrat(d, "sb", mc)
            return {"adj": q["within"]} if q else None
        _sdp, sd_reps = boot_cluster(aggs({"cf": self.cf, "re": self.re}, lambda r: (r["sym"], r["day"]), feeders[:2]),
                                     sd_stat, self.B, _rng(self.seed, "h1_symday"))
        point = point or {}
        per = aggs({"cf": self.cf, "re": self.re}, lambda r: "*", [feed_mean("sb", cell=sb)])
        ps = poststrat(per.get("*", {}), "sb", mc) or {}
        cov_rows = [r for r in self.cf + self.re if sb(r) is not None]
        cov_cells = set()
        cc = cells_of(per.get("*", {}), "sb")
        for k, v in cc["cf"].items():
            if v[1] >= 1 and cc["re"].get(k, [0, 0])[1] >= mc:
                cov_cells.add(k)
        used = [r for r in cov_rows if sb(r) in cov_cells]
        n_cf = sum(1 for r in used if r["kind"] == "cf")
        n_re = sum(1 for r in used if r["kind"] == "re")
        n_days = len({r["day"] for r in used})
        self.primary = {"within": point.get("adj"), "ci95_day": rep_ci(reps, lambda d: d["adj"]),
                        "ci95_symbol_day": rep_ci(sd_reps, lambda d: d["adj"]), "mix": point.get("mix"),
                        "coverage": point.get("coverage"), "single_day": single}
        res = {"post_strat": {
            "side_bucket": {**ps, "ci95_day": self.primary["ci95_day"], "ci95_symbol_day": self.primary["ci95_symbol_day"]},
            "day_side_bucket": {"within": point.get("dsb"), "ci95_day": rep_ci(reps, lambda d: d["dsb"])},
            "hourband_side_bucket": {"within": point.get("hsb"), "ci95_day": rep_ci(reps, lambda d: d["hsb"])}},
            "delta": {"d_gross_level_raw": point.get("d_gl"), "d_cost_raw": point.get("d_cost"),
                      "d_net_raw": point.get("raw"), "d_gross_level_within": point.get("d_gl_within"),
                      "d_cost_within": point.get("d_cost_within"),
                      "ci95_d_gross_level_raw": rep_ci(reps, lambda d: d["d_gl"]),
                      "ci95_d_cost_raw": rep_ci(reps, lambda d: d["d_cost"])}}
        self.delta = res["delta"]
        # side × bucket table
        tab = []
        rows_json = []
        for side in (1, -1):
            for bk in ("B0", "B1", "B2", "B3", "B4", "?"):
                for lab, src in (("CF", self.cf), ("REAL", self.re)):
                    g = [r for r in src if r["side"] == side and r["bk"] == bk]
                    if not g:
                        continue
                    parts = {k: mean(r["parts"].get(k) for r in g if r["parts"].get(k) is not None)
                             for k in ("exit_fill_model", "entry_fill", "exit_slippage", "fees", "funding")}
                    item = {"pop": lab, "side": side_name(side), "bucket": bk, "n": len(g),
                            "share": len(g) / max(1, len(src)), "mean_net": mean(r["r"] for r in g),
                            "mean_gross_level": mean(r["gl"] for r in g if r["gl"] is not None),
                            "mean_cost": mean(r["cost"] for r in g if r["cost"] is not None), "cost_parts": parts,
                            "median_stop_pct": statistics.median([r["sp"] for r in g if r["sp"] is not None])
                            if any(r["sp"] is not None for r in g) else None}
                    rows_json.append(item)
                    tab.append([lab, item["side"], "%s %s" % (bk, BUCKET_RANGE[bk]), item["n"], pct(item["share"]),
                                ff(item["mean_net"]), ff(item["mean_gross_level"]), ff(item["mean_cost"], 3, False),
                                ff(parts["exit_fill_model"], 3, False), ff(parts["entry_fill"], 3, False),
                                ff(parts["exit_slippage"], 3, False), ff(parts["fees"], 3, False), ff(parts["funding"])])
        res["side_bucket"] = rows_json
        med = []
        med_json = {}
        for lab, src in (("CF", self.cf), ("REAL", self.re)):
            for side in (1, -1):
                cells = []
                for hb in HOUR_BANDS:
                    v = [r["sp"] for r in src if r["side"] == side and r["hb"] == hb and r["sp"] is not None]
                    m = statistics.median(v) if v else None
                    med_json["%s|%s|%s" % (lab, side_name(side), hb)] = {"n": len(v), "median_stop_pct": m}
                    cells.append("%s (%d)" % (ff(m, 2, False), len(v)))
                med.append([lab, side_name(side)] + cells)
        res["median_stop_pct_by_hour"] = med_json
        self.rep["h1"] = res
        L.append("")
        L.append("== H1 stop / cost composition ==")
        table(L, "  side x stop bucket (CF_NET vs REAL_W; cost parts in R, positive = cost)",
              ["pop", "side", "bucket", "n", "share", "net", "gross_lvl", "cost", "exit_fill", "entry_fill", "exit_slip",
               "fees", "funding"], tab)
        table(L, "  median stop % by hour band (n)", ["pop", "side"] + list(HOUR_BANDS), med)
        dl = res["delta"]
        L.append("  delta (CF - REAL): net %s = gross_level %s - cost %s (raw) · within side x bucket: gross_level %s, cost %s" % (
            ff(dl["d_net_raw"]), ff(dl["d_gross_level_raw"]), ff(dl["d_cost_raw"]), ff(dl["d_gross_level_within"]),
            ff(dl["d_cost_within"])))
        for name, key in (("(side, bucket) [primary]", "side_bucket"), ("(day, side, bucket)", "day_side_bucket"),
                          ("(hour band, side, bucket)", "hourband_side_bucket")):
            q = res["post_strat"][key]
            L.append("  post-stratified %-28s within %s %s%s" % (
                name, ff(q.get("within")), en_ci(q.get("ci95_day"), single),
                (" · coverage %s · mix %s · reweighted real %s · cells %s · symbol x day CI %s" % (
                    pct(q.get("coverage")), ff(q.get("mix")), ff(q.get("reweighted_real")), q.get("cells"),
                    en_ci(q.get("ci95_symbol_day")))) if key == "side_bucket" else ""))
        self.evaluate("H1", point if point else None, reps, n_cf=n_cf, n_real=n_re, n_days=n_days, single=single)

    # -------------------------------------------------------------- H2
    def h2(self) -> None:
        L = self.L
        ep = episodes(self.cf)
        for k, rows in ep.items():
            for i, r in enumerate(rows):
                r["ep"], r["ep_first"], r["ep_n"] = k, i == 0, len(rows)
        clusters = stop_clusters(self.cf)
        ep_sizes = sorted(len(v) for v in ep.values())
        cl_sizes = sorted(clusters.values())
        ep_means = [mean(x["r"] for x in v) for v in ep.values()]
        first = [v[0]["r"] for v in ep.values()]

        def feed(side: str, r: dict, d: dict) -> None:
            day = r["day"]
            if side == "cf":
                _add(d, ("rows", day), 1.0)
                _add(d, ("cfs", day), r["r"])
                if r.get("ep_first"):
                    _add(d, ("E", day), mean(x["r"] for x in ep[r["ep"]]))
                    _add(d, ("k", day), 1.0)
                    _add(d, ("F", day), r["r"])
            else:
                _add(d, ("rs", day), r["r"])
                _add(d, ("rn", day), 1.0)

        def stat(d: dict) -> Optional[dict]:
            days = {k[1] for k in d if k[0] in ("rows", "rn")}
            cfn = sum(d.get(("rows", x), 0.0) for x in days)
            ren = sum(d.get(("rn", x), 0.0) for x in days)
            if cfn <= 0 or ren <= 0:
                return None
            cfm = sum(d.get(("cfs", x), 0.0) for x in days) / cfn
            rem = sum(d.get(("rs", x), 0.0) for x in days) / ren
            k_all = sum(d.get(("k", x), 0.0) for x in days)
            out = {"raw": cfm - rem, "cf_mean": cfm, "cf_episode_mean": sum(d.get(("E", x), 0.0) for x in days) / k_all,
                   "cf_first_mean": sum(d.get(("F", x), 0.0) for x in days) / k_all, "adj": None, "common": None}
            com = [x for x in days if d.get(("rows", x), 0.0) >= H2_MIN_DAY_ROWS and d.get(("rn", x), 0.0) >= H2_MIN_DAY_ROWS]
            kc = sum(d.get(("k", x), 0.0) for x in com)
            if com and kc > 0:
                out["adj"] = sum(d[("k", x)] / kc * (d[("E", x)] / d[("k", x)] - d[("rs", x)] / d[("rn", x)])
                                 for x in com if d.get(("k", x), 0.0) > 0)
                cn = sum(d[("rows", x)] for x in com)
                rn = sum(d[("rn", x)] for x in com)
                out["common"] = sum(d[("cfs", x)] for x in com) / cn - sum(d[("rs", x)] for x in com) / rn
            return out
        point, reps, single = self.day_boot([feed], stat, "h2")
        point = point or {}
        by_day = []
        for day in self.days:
            c = [r["r"] for r in self.cf if r["day"] == day]
            e = [r["r"] for r in self.re if r["day"] == day]
            by_day.append({"day": day, "cf_n": len(c), "cf_mean": mean(c), "real_n": len(e), "real_mean": mean(e),
                           "gap": (mean(c) - mean(e)) if (c and e) else None, "episodes": sum(1 for k in ep if k[1] == day)})
        by_slots = []
        for reg in sorted({r["slots_regime"] for r in self.cf + self.re}):
            c = [r["r"] for r in self.cf if r["slots_regime"] == reg]
            e = [r["r"] for r in self.re if r["slots_regime"] == reg]
            by_slots.append({"regime": reg, "cf_n": len(c), "cf_mean": mean(c), "real_n": len(e), "real_mean": mean(e),
                             "gap": (mean(c) - mean(e)) if (c and e) else None})
        tot = sum(r["r"] for r in self.cf)
        day_sum = Counter()
        for r in self.cf:
            day_sum[r["day"]] += r["r"]
        top_day = day_sum.most_common(1)[0] if day_sum else None
        iid = self.head["cf"]["ci95_iid"]
        dci = self.head["cf"]["ci95_day"]
        w_iid = (iid[1] - iid[0]) if iid else None
        w_day = (dci[1] - dci[0]) if dci else None
        deff = (w_day / w_iid) ** 2 if (w_iid and w_day) else None
        com_days = [b["day"] for b in by_day if b["cf_n"] >= H2_MIN_DAY_ROWS and b["real_n"] >= H2_MIN_DAY_ROWS]
        top_eps = sorted(ep.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:self.a.top]
        res = {"by_day": by_day, "by_slots_regime": by_slots,
               "episodes": {"count": len(ep), "max_size": ep_sizes[-1] if ep_sizes else None,
                            "p90_size": quantile(ep_sizes, 0.9)},
               "stop_clusters": {"count": len(clusters), "max_size": cl_sizes[-1] if cl_sizes else None,
                                 "p90_size": quantile(cl_sizes, 0.9)},
               "per_record_mean": mean(r["r"] for r in self.cf), "episode_mean": mean(ep_means),
               "first_record_mean": mean(first),
               "ci95_day": {"per_record": rep_ci(reps, lambda d: d["cf_mean"]),
                            "episode": rep_ci(reps, lambda d: d["cf_episode_mean"]),
                            "first_record": rep_ci(reps, lambda d: d["cf_first_mean"])},
               "effective_n": {"episodes": len(ep), "by_design_effect": (len(self.cf) / deff) if deff else None,
                               "design_effect": deff},
               "top_day": {"day": top_day[0], "share_of_sum_r": (top_day[1] / tot) if tot else None} if top_day else None,
               "ci_width": {"iid": w_iid, "day_cluster": w_day},
               "common_days": com_days, "gap_common_days": point.get("common"),
               "gap_common_days_ci95": rep_ci(reps, lambda d: d["common"]),
               "top_episodes": [{"symbol": k[0], "day": k[1], "side": side_name(k[2]), "n": len(v),
                                 "mean_r": mean(x["r"] for x in v)} for k, v in top_eps],
               "top_records": [{"id": r["id"], "symbol": r["sym"], "day": r["day"], "side": side_name(r["side"]), "r": r["r"]}
                               for r in sorted(self.cf, key=lambda x: -x["r"])[:self.a.top]]}
        self.rep["h2"] = res
        L.append("")
        L.append("== H2 period and replication ==")
        table(L, "  by UTC day (signal day)", ["day", "cf_n", "cf_mean", "real_n", "real_mean", "gap", "episodes"],
              [[b["day"], b["cf_n"], ff(b["cf_mean"]), b["real_n"], ff(b["real_mean"]), ff(b["gap"]), b["episodes"]]
               for b in by_day])
        table(L, "  by slots regime (slots_change %s)" % (iso_ms(self.slots_change) or "none"),
              ["regime", "cf_n", "cf_mean", "real_n", "real_mean", "gap"],
              [[b["regime"], b["cf_n"], ff(b["cf_mean"]), b["real_n"], ff(b["real_mean"]), ff(b["gap"])] for b in by_slots])
        L.append("  episodes (symbol, day, side): %d, max size %s, p90 %s · stop clusters (symbol, side, stop): %d, max %s, p90 %s" % (
            len(ep), res["episodes"]["max_size"], ff(res["episodes"]["p90_size"], 1, False), len(clusters),
            res["stop_clusters"]["max_size"], ff(res["stop_clusters"]["p90_size"], 1, False)))
        L.append("  CF mean per record %s %s · mean of episode means %s %s · first record per episode %s %s" % (
            ff(res["per_record_mean"]), en_ci(res["ci95_day"]["per_record"], single), ff(res["episode_mean"]),
            en_ci(res["ci95_day"]["episode"], single), ff(res["first_record_mean"]), en_ci(res["ci95_day"]["first_record"], single)))
        L.append("  effective n: episodes %d · n / design effect %s (deff %s) · CI width iid %s vs day-cluster %s" % (
            len(ep), ff(res["effective_n"]["by_design_effect"], 1, False), ff(deff, 2, False), ff(w_iid, 3, False),
            ff(w_day, 3, False)))
        if top_day:
            L.append("  top day %s carries %s of the CF sum of r_net" % (top_day[0], pct(res["top_day"]["share_of_sum_r"])))
        L.append("  gap on days with >=%d rows on both sides (%s): %s %s" % (
            H2_MIN_DAY_ROWS, ", ".join(com_days) or "none", ff(point.get("common")), en_ci(res["gap_common_days_ci95"], single)))
        table(L, "  largest episodes (top %d)" % self.a.top, ["symbol", "day", "side", "n", "mean_r"],
              [[e["symbol"], e["day"], e["side"], e["n"], ff(e["mean_r"])] for e in res["top_episodes"]])
        used_cf = [r for r in self.cf if r["day"] in com_days]
        used_re = [r for r in self.re if r["day"] in com_days]
        self.evaluate("H2", {"raw": point.get("raw"), "adj": point.get("adj")}, reps, n_cf=len(used_cf),
                      n_real=len(used_re), n_days=len(com_days), single=single)

    # -------------------------------------------------------------- H3
    def h3(self) -> None:
        L, mc = self.L, self.min_cell
        po = [r for r in self.cf if r["fam"] == "POSITION_OPEN"]
        groups: dict = defaultdict(list)
        for r in self.cf:
            groups[(r["fam"], "-" if r["rule_ms"] is None else "%g" % r["rule_ms"], "blocked" if r["bb"] else "free")].append(r)
        fam_rows = [{"family": k[0], "rule_ms": k[1], "baseline_blocked": k[2], "n": len(v), "mean_r": mean(x["r"] for x in v),
                     "mean_stop_pct": mean(x["sp"] for x in v if x["sp"] is not None),
                     "long_share": sum(1 for x in v if x["side"] > 0) / len(v)} for k, v in sorted(groups.items())]
        # join POSITION_OPEN rows to the held real trade
        index = held_index(self.real_all, self.positions)
        joined = [(c, held_trade(index, c["sym"], c["t0"])) for c in po]
        split: dict = defaultdict(list)
        for c, h in joined:
            split["unjoined" if h is None else h["xcls"]].append(c)
        held = [h for _c, h in joined if h is not None]
        rel = Counter(("same" if h["side"] == c["side"] else "opposite") for c, h in joined if h is not None)
        mins = sorted((c["t0"] - h["t0"]) / 60000.0 for c, h in joined if h is not None)
        wide = [r for r in self.re if r["sp"] is not None and r["sp"] >= WIDE_STOP_PCT]
        pol = [r for r in self.re if r["cohort"] == "policy"]
        side_cell = lambda r: side_name(r["side"])  # noqa: E731
        feeders = [feed_mean("raw"), feed_mean("nopo", include=lambda s, r: s == "re" or r["fam"] != "POSITION_OPEN"),
                   feed_mean("pw", cell=side_cell, include=lambda s, r: (r["fam"] == "POSITION_OPEN") if s == "cf"
                             else (r["sp"] is not None and r["sp"] >= WIDE_STOP_PCT)),
                   feed_mean("pp", cell=side_cell, include=lambda s, r: (r["fam"] == "POSITION_OPEN") if s == "cf"
                             else r["cohort"] == "policy")]

        def stat(d: dict) -> Optional[dict]:
            raw = plain_gap(d, "raw")
            if raw is None:
                return None
            pw, pp = poststrat(d, "pw", mc), poststrat(d, "pp", mc)
            return {"raw": raw, "adj": plain_gap(d, "nopo"), "po_vs_wide": (pw or {}).get("within"),
                    "po_vs_policy": (pp or {}).get("within")}
        point, reps, single = self.day_boot(feeders, stat, "h3")
        point = point or {}
        res = {"by_family": fam_rows, "position_open_n": len(po), "position_open_mean": mean(r["r"] for r in po),
               "join": {"joined": len(held), "unjoined": len(po) - len(held),
                        "held_exit": dict(Counter(h["xcls"] for h in held)),
                        "held_exit_reason": dict(Counter(str(h.get("exit") or "OPEN") for h in held)),
                        "held_mean_r": mean(h["r"] for h in held if h.get("r") is not None),
                        "held_mean_stop_pct": mean(h["sp"] for h in held if h.get("sp") is not None),
                        "side_relation": dict(rel), "minutes_since_held_open_median": quantile(mins, 0.5)},
               "cf_mean_by_held_outcome": {k: {"n": len(v), "mean_r": mean(x["r"] for x in v)} for k, v in sorted(split.items())},
               "po_vs_real_wide": {"real_n": len(wide), "gap_side_matched": point.get("po_vs_wide"),
                                   "ci95_day": rep_ci(reps, lambda d: d["po_vs_wide"])},
               "po_vs_policy": {"real_n": len(pol), "gap_side_matched": point.get("po_vs_policy"),
                                "ci95_day": rep_ci(reps, lambda d: d["po_vs_policy"])},
               "top_joined": [{"cf_id": c["id"], "symbol": c["sym"], "cf_r": c["r"], "held_id": h.get("id"),
                               "held_exit": h["xcls"], "held_r": h.get("r")}
                              for c, h in sorted(((c, h) for c, h in joined if h is not None), key=lambda x: -x[0]["r"])
                              [:self.a.top]]}
        self.rep["h3"] = res
        L.append("")
        L.append("== H3 POSITION_OPEN subset ==")
        table(L, "  CF by family x rule_ms x baseline_blocked_by", ["family", "rule_ms", "baseline", "n", "mean_r",
                                                                  "mean_stop%", "long_share"],
              [[f["family"], f["rule_ms"], f["baseline_blocked"], f["n"], ff(f["mean_r"]), ff(f["mean_stop_pct"], 2, False),
                pct(f["long_share"])] for f in fam_rows])
        j = res["join"]
        L.append("  POSITION_OPEN rows %d joined to the held real trade %d (unjoined %d) · held exit %s · held mean R %s · "
                 "held mean stop %% %s · side %s · median minutes since held open %s" % (
                     len(po), j["joined"], j["unjoined"], dict(j["held_exit"]) or "-", ff(j["held_mean_r"]),
                     ff(j["held_mean_stop_pct"], 2, False), dict(j["side_relation"]) or "-",
                     ff(j["minutes_since_held_open_median"], 1, False)))
        table(L, "  POSITION_OPEN CF mean by the held trade's later outcome", ["held_outcome", "n", "cf_mean_r"],
              [[k, v["n"], ff(v["mean_r"])] for k, v in res["cf_mean_by_held_outcome"].items()])
        L.append("  POSITION_OPEN vs REAL_W stop >= 2.22%% (n %d), side-matched gap %s %s" % (
            len(wide), ff(point.get("po_vs_wide")), en_ci(res["po_vs_real_wide"]["ci95_day"], single)))
        L.append("  POSITION_OPEN vs REAL_W policy cohort (n %d), side-matched gap %s %s" % (
            len(pol), ff(point.get("po_vs_policy")), en_ci(res["po_vs_policy"]["ci95_day"], single)))
        nopo = [r for r in self.cf if r["fam"] != "POSITION_OPEN"]
        self.evaluate("H3", point if point.get("raw") is not None else None, reps, n_cf=len(nopo), n_real=len(self.re),
                      n_days=len({r["day"] for r in nopo + self.re}), single=single)

    # -------------------------------------------------------------- H4
    def h4(self) -> None:
        L = self.L
        stops = [r for r in self.re if r["is_stop"]]
        by_bf: dict = defaultdict(list)
        for r in stops:
            by_bf[(r["basis"] or "-", r["first_source"] or "-")].append(r)
        ov_rows = []
        for bk in ("B0", "B1", "B2", "B3", "B4", "?"):
            g = [r for r in stops if r["bk"] == bk and r["ov_pct"] is not None]
            if not g:
                continue
            sp_ = sorted(r["ov_pct"] for r in g)
            sr = sorted(r["ov_r"] for r in g if r["ov_r"] is not None)
            ov_rows.append({"bucket": bk, "n": len(g), "overshoot_pct_mean": mean(sp_), "overshoot_pct_median": quantile(sp_, 0.5),
                            "overshoot_pct_p90": quantile(sp_, 0.9), "overshoot_r_mean": mean(sr),
                            "overshoot_r_median": quantile(sr, 0.5), "overshoot_r_p90": quantile(sr, 0.9)})
        ovs = [r["ov_pct"] for r in stops if r["ov_pct"] is not None]
        pooled = mean(ovs)
        gaps = sorted(fnum(x) for x in (self.box_timer.get("protect_gaps_s") or []) if fnum(x) is not None)
        cad = {"box_timer": ({"n": len(gaps), "p50_s": quantile(gaps, 0.5), "p90_s": quantile(gaps, 0.9),
                              "max_s": gaps[-1] if gaps else None, "tick_every_s": fnum(self.box_timer.get("tick_every_s")),
                              "poll_s": fnum(self.box_timer.get("poll_s")), "missed_bars": self.box_timer.get("missed_bars"),
                              "last_eval_seconds": fnum(as_dict(self.box_timer.get("last_eval")).get("seconds"))}
                             if self.box_timer else NO_DATA)}
        pm_pos = as_dict(as_dict(as_dict(as_dict(self.pm.get("observations")).get("books")).get(self.a.book)).get("positions"))
        if self.pm:
            per_obs, src, max_gaps = [], Counter(), []
            ids = {r["id"]: r for r in self.real_all}
            for pid, rec in pm_pos.items():
                rec = as_dict(rec)
                r = ids.get(str(pid))
                n = rec.get("observations")
                if r is None or not isinstance(n, int) or n <= 0 or r["closed"] is None or r["t0"] is None:
                    continue
                per_obs.append((r["closed"] - r["t0"]) / 1000.0 / n)
                if fnum(rec.get("max_gap_s")) is not None:
                    max_gaps.append(fnum(rec.get("max_gap_s")))
                for k, v in as_dict(rec.get("sources")).items():
                    if isinstance(v, int):
                        src[str(k)] += v
            per_obs.sort()
            max_gaps.sort()
            cad["protective_monitor"] = {"since": as_dict(self.pm.get("observations")).get("since") or self.pm.get("since"),
                                         "joined_positions": len(per_obs), "s_per_obs_p50": quantile(per_obs, 0.5),
                                         "s_per_obs_p90": quantile(per_obs, 0.9), "sources": dict(src),
                                         "max_gap_s_p90": quantile(max_gaps, 0.9), "max_gap_s_max": max_gaps[-1] if max_gaps else None}
        else:
            cad["protective_monitor"] = NO_DATA
        cf_basis = Counter(r["basis"] or "-" for r in self.cf if r["is_stop"])

        def feed(side: str, r: dict, d: dict) -> None:
            s = side_name(r["side"])
            for key in ("*", s):
                if side == "cf":
                    _add(d, ("cs", key), r["r"])
                    _add(d, ("cn", key), 1.0)
                    if r["is_stop"] and r["sp"]:
                        _add(d, ("ci", key), 1.0 / r["sp"])
                else:
                    _add(d, ("rs", key), r["r"])
                    _add(d, ("rn", key), 1.0)
            if side == "re" and r["is_stop"] and r["ov_pct"] is not None:
                _add(d, ("os",), r["ov_pct"])
                _add(d, ("on",), 1.0)

        def stat(d: dict) -> Optional[dict]:
            if not d.get(("cn", "*")) or not d.get(("rn", "*")):
                return None
            pool = d[("os",)] / d[("on",)] if d.get(("on",)) else None
            out = {"raw": d[("cs", "*")] / d[("cn", "*")] - d[("rs", "*")] / d[("rn", "*")], "pooled": pool}
            for key in ("*", "LONG", "SHORT"):
                if not d.get(("cn", key)) or not d.get(("rn", key)) or pool is None:
                    out["adj" if key == "*" else key] = None
                    continue
                adj_cf = (d[("cs", key)] - pool * d.get(("ci", key), 0.0)) / d[("cn", key)]
                out["adj" if key == "*" else key] = adj_cf - d[("rs", key)] / d[("rn", key)]
            return out
        point, reps, single = self.day_boot([feed], stat, "h4")
        point = point or {}
        adj_cf = None
        if pooled is not None and self.cf:
            adj_cf = mean(r["r"] - ((pooled / r["sp"]) if (r["is_stop"] and r["sp"]) else 0.0) for r in self.cf)
        res = {"real_stop_exits_by_basis": {"%s|%s" % k: {"n": len(v), "mean_r": mean(x["r"] for x in v)}
                                            for k, v in sorted(by_bf.items())},
               "overshoot_by_bucket": ov_rows, "pooled_overshoot_pct": pooled, "pooled_n": len(ovs),
               "sampling_cadence": cad, "cf_stop_exits_by_basis": dict(cf_basis),
               "adjusted_cf_mean": adj_cf, "adjusted_gap": point.get("adj"),
               "adjusted_gap_ci95": rep_ci(reps, lambda d: d["adj"]),
               "by_side": {s: {"adjusted_gap": point.get(s), "ci95_day": rep_ci(reps, lambda d, s=s: d[s])}
                           for s in ("LONG", "SHORT")}}
        self.rep["h4"] = res
        L.append("")
        L.append("== H4 stop overshoot (CF fills stops at the level) ==")
        table(L, "  real stop exits by exit_fill.basis x first_source", ["basis", "first_source", "n", "mean_r"],
              [[k[0], k[1], len(v), ff(mean(x["r"] for x in v))] for k, v in sorted(by_bf.items())])
        table(L, "  real stop overshoot by stop bucket (pct of entry fill; R = exit_fill_model part)",
              ["bucket", "n", "ov%_mean", "ov%_med", "ov%_p90", "ovR_mean", "ovR_med", "ovR_p90"],
              [["%s %s" % (o["bucket"], BUCKET_RANGE[o["bucket"]]), o["n"], ff(o["overshoot_pct_mean"], 4, False),
                ff(o["overshoot_pct_median"], 4, False), ff(o["overshoot_pct_p90"], 4, False), ff(o["overshoot_r_mean"]),
                ff(o["overshoot_r_median"]), ff(o["overshoot_r_p90"])] for o in ov_rows])
        bt = cad["box_timer"]
        L.append("  box_timer protect_gaps_s: %s" % (bt if bt == NO_DATA else "n %d p50 %s p90 %s max %s (tick_every_s %s, poll_s %s, "
                                                    "missed_bars %s, last_eval %s s)" % (
            bt["n"], ff(bt["p50_s"], 1, False), ff(bt["p90_s"], 1, False), ff(bt["max_s"], 1, False),
            ff(bt["tick_every_s"], 0, False), ff(bt["poll_s"], 0, False), bt["missed_bars"], ff(bt["last_eval_seconds"], 1, False))))
        pm = cad["protective_monitor"]
        L.append("  protective_monitor: %s" % (pm if pm == NO_DATA else "joined %d positions, seconds per observation p50 %s p90 %s, "
                                                                    "max_gap_s p90 %s max %s, sources %s (since %s)" % (
            pm["joined_positions"], ff(pm["s_per_obs_p50"], 1, False), ff(pm["s_per_obs_p90"], 1, False),
            ff(pm["max_gap_s_p90"], 1, False), ff(pm["max_gap_s_max"], 1, False), pm["sources"] or "-", pm["since"] or "-")))
        L.append("  CF stop exits by net_exit_basis: %s" % (", ".join("%s=%d" % kv for kv in sorted(cf_basis.items())) or "-"))
        L.append("  pooled real overshoot %s%% (n %d) -> adjusted CF mean %s, adjusted gap %s %s · LONG %s %s · SHORT %s %s" % (
            ff(pooled, 4, False), len(ovs), ff(adj_cf), ff(point.get("adj")), en_ci(res["adjusted_gap_ci95"], single),
            ff(point.get("LONG")), en_ci(res["by_side"]["LONG"]["ci95_day"], single), ff(point.get("SHORT")),
            en_ci(res["by_side"]["SHORT"]["ci95_day"], single)))
        self.evaluate("H4", point if point.get("raw") is not None else None, reps, n_cf=len(self.cf),
                      n_real=len(self.re) if pooled is not None else 0, n_days=len(self.days), single=single)

    # -------------------------------------------------------------- H5
    def h5(self) -> None:
        L = self.L
        for r in self.re:
            end = b1_end(r["t0"])
            r["stop_b1"] = stopped_in_b1(r["t0"], r["closed"], r["is_stop"])
            r["stop_b2"] = bool(r["is_stop"] and r["closed"] is not None and end <= r["closed"] < end + M5_MS)
        rows = []
        for bk in ("B0", "B1", "B2", "B3", "B4", "?"):
            g = [r for r in self.re if r["bk"] == bk]
            if not g:
                continue
            b1 = [r for r in g if r["stop_b1"]]
            rows.append({"bucket": bk, "real_n": len(g), "stopped_in_b1": len(b1), "share": len(b1) / len(g),
                         "mean_r_b1": mean(r["r"] for r in b1), "mean_r_not_b1": mean(r["r"] for r in g if not r["stop_b1"]),
                         "stopped_next_bar": sum(1 for r in g if r["stop_b2"]),
                         "cf_n": sum(1 for r in self.cf if r["bk"] == bk),
                         "cf_stops_bars_1": sum(1 for r in self.cf if r["bk"] == bk and r["is_stop"] and r["bars"] == 1)})

        def feed(side: str, r: dict, d: dict) -> None:
            bk = r["bk"]
            if side == "cf":
                _add(d, ("cs",), r["r"])
                _add(d, ("cn",), 1.0)
                _add(d, ("cb", bk), 1.0)
            else:
                _add(d, ("rs",), r["r"])
                _add(d, ("rn",), 1.0)
                _add(d, ("n", bk), 1.0)
                if r["stop_b1"]:
                    _add(d, ("b1n", bk), 1.0)
                    _add(d, ("b1s", bk), r["r"])
                else:
                    _add(d, ("ns", bk), r["r"])

        def stat(d: dict) -> Optional[dict]:
            if not d.get(("cn",)) or not d.get(("rn",)):
                return None
            raw = d[("cs",)] / d[("cn",)] - d[("rs",)] / d[("rn",)]
            bks = [k[1] for k in d if k[0] == "cb" and d.get(("n", k[1]), 0.0) > 0]
            wt = sum(d[("cb", b)] for b in bks)
            if wt <= 0:
                return {"raw": raw, "adj": None, "bias": None}
            bias = 0.0
            for b in bks:
                n, nb1 = d[("n", b)], d.get(("b1n", b), 0.0)
                if nb1 <= 0 or n - nb1 <= 0:
                    continue
                bias += d[("cb", b)] / wt * (nb1 / n) * (d.get(("ns", b), 0.0) / (n - nb1) - d[("b1s", b)] / nb1)
            return {"raw": raw, "adj": raw - bias, "bias": bias}
        point, reps, single = self.day_boot([feed], stat, "h5")
        point = point or {}
        lags = sorted(fnum(x) for x in (self.box_timer.get("lags_s") or []) if fnum(x) is not None)
        res = {"by_bucket": rows, "upper_bound_bias": point.get("bias"), "bias_ci95_day": rep_ci(reps, lambda d: d["bias"]),
               "adjusted_gap": point.get("adj"),
               "box_timer_lags_s": {"p50": quantile(lags, 0.5), "max": lags[-1] if lags else None, "n": len(lags)}
               if self.box_timer else NO_DATA}
        self.rep["h5"] = res
        L.append("")
        L.append("== H5 entry bar (B1) not walked by the CF labeller ==")
        table(L, "  real stops before the end of the entry 5m bar (B1) by stop bucket; CF stops at outcome.bars == 1",
              ["bucket", "real_n", "stop_in_B1", "share", "mean_r_B1", "mean_r_rest", "stop_next_bar", "cf_n", "cf_stop_bars1"],
              [["%s %s" % (x["bucket"], BUCKET_RANGE[x["bucket"]]), x["real_n"], x["stopped_in_b1"], pct(x["share"]),
                ff(x["mean_r_b1"]), ff(x["mean_r_not_b1"]), x["stopped_next_bar"], x["cf_n"], x["cf_stops_bars_1"]] for x in rows])
        L.append("  upper-bound bias (CF bucket weights) %s %s -> adjusted gap %s" % (
            ff(point.get("bias")), en_ci(res["bias_ci95_day"], single), ff(point.get("adj"))))
        bl = res["box_timer_lags_s"]
        L.append("  box_timer lags_s: %s" % (bl if bl == NO_DATA else "p50 %s max %s (n %d)" % (
            ff(bl["p50"], 1, False), ff(bl["max"], 1, False), bl["n"])))
        self.evaluate("H5", point if point.get("raw") is not None else None, reps, n_cf=len(self.cf), n_real=len(self.re),
                      n_days=len(self.days), single=single)

    # -------------------------------------------------------------- H6
    def h6(self) -> None:
        L, mc = self.L, self.min_cell
        win = float(self.a.pair_window_min) * 60000.0
        re_by_side: dict = defaultdict(list)
        for r in sorted(self.re, key=lambda x: x["t0"]):
            re_by_side[r["side"]].append(r)
        re_ts = {s: [r["t0"] for r in v] for s, v in re_by_side.items()}
        for c in self.cf:
            lst, ts_ = re_by_side.get(c["side"], []), re_ts.get(c["side"], [])
            i, j = bisect.bisect_left(ts_, c["t0"] - win), bisect.bisect_right(ts_, c["t0"] + win)
            c["pair"] = (c["r"] - mean(r["r"] for r in lst[i:j])) if j > i else None
        feeders = [feed_mean("raw"), feed_mean("os", cell=lambda r: (r["occb"], side_name(r["side"]))),
                   feed_mean("pair", val=lambda r: r.get("pair"), include=lambda s, r: s == "cf")]

        def stat(d: dict) -> Optional[dict]:
            raw = plain_gap(d, "raw")
            if raw is None:
                return None
            ps = poststrat(d, "os", mc)
            return {"raw": raw, "adj": ps["within"] if ps else None, "pair": mean_of(d, "pair", "cf")}
        point, reps, single = self.day_boot(feeders, stat, "h6")
        point = point or {}

        def strat(key: str, bands: tuple) -> list:
            out = []
            for b in bands:
                for side in (1, -1):
                    c = [r for r in self.cf if r[key] == b and r["side"] == side]
                    e = [r for r in self.re if r[key] == b and r["side"] == side]
                    if c or e:
                        out.append({"band": b, "side": side_name(side), "cf_n": len(c), "cf_mean": mean(r["r"] for r in c),
                                    "real_n": len(e), "real_mean": mean(r["r"] for r in e)})
            return out
        res = {"by_occupancy": strat("occb", OCC_BANDS + ("?",)), "by_hour_band": strat("hb", HOUR_BANDS),
               "by_min_to_eod": strat("mteb", MTE_BANDS + ("?",)),
               "by_universe_rank": strat("rankb", RANK_BANDS) if self.ranks else NO_DATA}
        fam_occ: dict = defaultdict(list)
        for r in self.cf:
            fam_occ[(r["fam"], r["occb"])].append(r["r"])
        res["cf_family_by_occupancy"] = {"%s|%s" % k: {"n": len(v), "mean_r": mean(v)} for k, v in sorted(fam_occ.items())}
        ref = [r for r in self.re if r["refill"]]
        nref = [r for r in self.re if not r["refill"]]
        res["refill"] = {"window_min": self.a.refill_window_min, "refill_n": len(ref), "refill_mean": mean(r["r"] for r in ref),
                         "other_n": len(nref), "other_mean": mean(r["r"] for r in nref)}
        same_pass = NO_DATA
        if self.ranks:
            secs: dict = defaultdict(lambda: ([], []))
            for r in self.cf_in:
                if r["t0"] is not None and r["rank"] is not None:
                    secs[r["t0"] // 1000][0].append(r["rank"])
            for r in self.real_all:
                if r["t0"] is not None and r["rank"] is not None and (r["t0"] // 1000) in secs:
                    secs[r["t0"] // 1000][1].append(r["rank"])
            both = [(c, o) for c, o in secs.values() if c and o]
            same_pass = {"passes": len(both), "share_all_cf_ranks_after_opened": (sum(1 for c, o in both if min(c) > max(o))
                                                                                  / len(both)) if both else None}
        res["same_pass"] = same_pass
        res["time_matched_gap"] = {"window_min": self.a.pair_window_min, "n_pairs": sum(1 for r in self.cf if r.get("pair") is not None),
                                   "gap": point.get("pair"), "ci95_day": rep_ci(reps, lambda d: d["pair"])}
        if self.xp_ok:
            snap = {}
            for key in ("h4_trend", "h4_vol_regime", "btc_h4_trend"):
                vals: dict = defaultdict(lambda: [[], []])
                for r in self.cf:
                    s = self.xp_cf.get(r["id"])
                    if s is not None:
                        vals[str(s.get(key))][0].append(r["r"])
                for r in self.re:
                    s = self.xp_re.get((r["id"], r["t0"]))
                    if s is not None:
                        vals[str(s.get(key))][1].append(r["r"])
                snap[key] = {k: {"cf_n": len(v[0]), "cf_mean": mean(v[0]), "real_n": len(v[1]), "real_mean": mean(v[1])}
                             for k, v in sorted(vals.items())}
            res["snapshot_strata"] = {"coverage_cf": sum(1 for r in self.cf if r["id"] in self.xp_cf) / max(1, len(self.cf)),
                                      "coverage_real": sum(1 for r in self.re if (r["id"], r["t0"]) in self.xp_re)
                                      / max(1, len(self.re)), "strata": snap}
        else:
            res["snapshot_strata"] = NO_DATA
        res["post_strat_occupancy_side"] = {"within": point.get("adj"), "ci95_day": rep_ci(reps, lambda d: d["adj"])}
        self.rep["h6"] = res
        L.append("")
        L.append("== H6 crowding, time of day, universe order, refill ==")
        for title, key in (("book occupancy at t0 (other open Box positions)", "by_occupancy"),
                           ("hour band (t0)", "by_hour_band"), ("minutes to the signal day's end", "by_min_to_eod"),
                           ("universe rank band (--config)", "by_universe_rank")):
            if res[key] == NO_DATA:
                L.append("")
                L.append("  %s: %s" % (title, NO_DATA))
                continue
            table(L, "  " + title, ["band", "side", "cf_n", "cf_mean", "real_n", "real_mean"],
                  [[x["band"], x["side"], x["cf_n"], ff(x["cf_mean"]), x["real_n"], ff(x["real_mean"])] for x in res[key]])
        table(L, "  CF family x occupancy band", ["family|occupancy", "n", "mean_r"],
              [[k, v["n"], ff(v["mean_r"])] for k, v in res["cf_family_by_occupancy"].items()])
        rf = res["refill"]
        L.append("  real refill (same-side stop within %s min before open): n %d mean %s · others n %d mean %s" % (
            rf["window_min"], rf["refill_n"], ff(rf["refill_mean"]), rf["other_n"], ff(rf["other_mean"])))
        L.append("  same-pass rank order: %s" % (same_pass if same_pass == NO_DATA else "%d passes, every CF ranked after every "
                                                 "opened symbol in %s" % (same_pass["passes"],
                                                                          pct(same_pass["share_all_cf_ranks_after_opened"]))))
        tm = res["time_matched_gap"]
        L.append("  time-matched gap (same side, real opened within +/-%s min): %s %s (pairs %d)" % (
            tm["window_min"], ff(tm["gap"]), en_ci(tm["ci95_day"], single), tm["n_pairs"]))
        ss = res["snapshot_strata"]
        if ss == NO_DATA:
            L.append("  snapshot strata (shared_experience): %s" % NO_DATA)
        else:
            L.append("  snapshot strata coverage: CF %s · real %s" % (pct(ss["coverage_cf"]), pct(ss["coverage_real"])))
            for key, vals in ss["strata"].items():
                table(L, "  stratum %s" % key, ["value", "cf_n", "cf_mean", "real_n", "real_mean"],
                      [[k, v["cf_n"], ff(v["cf_mean"]), v["real_n"], ff(v["real_mean"])] for k, v in vals.items()])
        L.append("  post-stratified on (occupancy band, side): within %s %s" % (
            ff(point.get("adj")), en_ci(res["post_strat_occupancy_side"]["ci95_day"], single)))
        per = aggs({"cf": self.cf, "re": self.re}, lambda r: "*", [feed_mean("os", cell=lambda r: (r["occb"], side_name(r["side"])))])
        cc = cells_of(per.get("*", {}), "os")
        cov = {k for k, v in cc["cf"].items() if cc["re"].get(k, [0, 0])[1] >= mc}
        used = [r for r in self.cf + self.re if (r["occb"], side_name(r["side"])) in cov]
        self.evaluate("H6", point if point.get("raw") is not None else None, reps,
                      n_cf=sum(1 for r in used if r["kind"] == "cf"), n_real=sum(1 for r in used if r["kind"] == "re"),
                      n_days=len({r["day"] for r in used}), single=single)

    # -------------------------------------------------------------- H7
    def h7(self) -> None:
        L = self.L
        h7_flags(self.real_all)
        feeders = [feed_mean("raw"), feed_mean("nf", include=lambda s, r: s == "cf" or not r["h7flag"])]
        point, reps, single = self.day_boot(feeders, lambda d: ({"raw": plain_gap(d, "raw"), "adj": plain_gap(d, "nf")}
                                                                if plain_gap(d, "raw") is not None else None), "h7")
        point = point or {}
        re_ = [r for r in self.re if r["reentry"]]
        lt = [r for r in self.re if r["late2350"]]
        keep = [r for r in self.re if not r["h7flag"]]
        res = {"reentry": {"n": len(re_), "mean_r": mean(r["r"] for r in re_)},
               "signal_2350": {"n": len(lt), "mean_r": mean(r["r"] for r in lt)},
               "gap_without_flagged": point.get("adj"), "ci95_day": rep_ci(reps, lambda d: d["adj"]), "real_kept": len(keep)}
        self.rep["h7"] = res
        L.append("")
        L.append("== H7 signal classes only the real book can have ==")
        L.append("  re-entries after a same-day stop (same symbol, side, signal day): n %d mean %s" % (len(re_), ff(res["reentry"]["mean_r"])))
        L.append("  23:50 signals (signal bar 23:50 UTC or opened >= 23:50): n %d mean %s" % (len(lt), ff(res["signal_2350"]["mean_r"])))
        L.append("  gap without flagged real rows (n %d): %s %s" % (len(keep), ff(point.get("adj")), en_ci(res["ci95_day"], single)))
        self.evaluate("H7", point if point.get("raw") is not None else None, reps, n_cf=len(self.cf), n_real=len(keep),
                      n_days=len({r["day"] for r in self.cf + keep}), single=single)

    # -------------------------------------------------------------- H8
    def h8(self) -> None:
        L = self.L
        inc = [r for r in self.cf if r["fund_ok"] is False]
        com = [r for r in self.cf if r["fund_ok"] is True]
        horizon = [r for r in self.cf if exit_class(r["exit"]) == "EOD"]
        eod = [r for r in self.real_all if r["exit"] == "BOX_EOD_FLAT" and r["closed"] is not None]
        after = sorted((r["closed"] % DAY_MS) / 1000.0 for r in eod if (r["closed"] % DAY_MS) < 6 * 3600 * 1000)
        fund_r: dict = defaultdict(list)
        for r in self.re:
            if r["parts"].get("funding") is not None:
                fund_r[side_name(r["side"])].append(-r["parts"]["funding"])
        levels = impute_funding(self.cf, self.real_all)
        feeders = [feed_mean("raw"), feed_mean("imp", val=lambda r: r["r"] + (r.get("fund_imp") or 0.0) if r["kind"] == "cf" else r["r"])]
        point, reps, single = self.day_boot(feeders, lambda d: ({"raw": plain_gap(d, "raw"), "adj": plain_gap(d, "imp")}
                                                                if plain_gap(d, "raw") is not None else None), "h8")
        point = point or {}
        res = {"cf_funding_incomplete": {"n": len(inc), "mean_r": mean(r["r"] for r in inc)},
               "cf_funding_complete": {"n": len(com), "mean_r": mean(r["r"] for r in com)},
               "cf_horizon_share": len(horizon) / len(self.cf) if self.cf else None,
               "cf_mean_funding_part": mean(r["parts"].get("funding") for r in self.cf if r["parts"].get("funding") is not None),
               "real_eod_flat": {"n": len(eod), "seconds_after_midnight_p50": quantile(after, 0.5),
                                 "seconds_after_midnight_p90": quantile(after, 0.9)},
               "real_funding_r_by_side": {k: mean(v) for k, v in sorted(fund_r.items())},
               "real_funding_incomplete": sum(1 for r in self.real_all if r["fund_complete"] is False),
               "imputation_levels": dict(levels),
               "adjusted_cf_mean": mean(r["r"] + r["fund_imp"] for r in self.cf),
               "adjusted_gap": point.get("adj"), "ci95_day": rep_ci(reps, lambda d: d["adj"])}
        self.rep["h8"] = res
        L.append("")
        L.append("== H8 end of day and funding ==")
        L.append("  CF funding_complete False n %d mean %s · True n %d mean %s · CF horizon (EOD) exits %s · mean CF funding part %s" % (
            len(inc), ff(res["cf_funding_incomplete"]["mean_r"]), len(com), ff(res["cf_funding_complete"]["mean_r"]),
            pct(res["cf_horizon_share"]), ff(res["cf_mean_funding_part"])))
        L.append("  real BOX_EOD_FLAT n %d, seconds after midnight p50 %s p90 %s · real funding R by side %s · funding incomplete %d" % (
            len(eod), ff(res["real_eod_flat"]["seconds_after_midnight_p50"], 0, False),
            ff(res["real_eod_flat"]["seconds_after_midnight_p90"], 0, False),
            {k: round(v, 4) for k, v in res["real_funding_r_by_side"].items()} or "-", res["real_funding_incomplete"]))
        L.append("  imputed missing 00:00 funding (real %% of notional / CF stop %%; levels %s): adjusted CF mean %s, gap %s %s" % (
            dict(levels) or "-", ff(res["adjusted_cf_mean"]), ff(point.get("adj")), en_ci(res["ci95_day"], single)))
        self.evaluate("H8", point if point.get("raw") is not None else None, reps, n_cf=len(self.cf), n_real=len(self.re),
                      n_days=len(self.days), single=single)

    # -------------------------------------------------------------- H9
    def h9(self) -> None:
        L = self.L
        for r in self.real_all:
            r["wrong_side"] = wrong_side(r)
        bad = [r for r in self.re if r["wrong_side"]]
        feeders = [feed_mean("raw"), feed_mean("z", val=lambda r: 0.0 if r.get("wrong_side") else r["r"])]
        point, reps, single = self.day_boot(feeders, lambda d: ({"raw": plain_gap(d, "raw"), "adj": plain_gap(d, "z")}
                                                                if plain_gap(d, "raw") is not None else None), "h9")
        point = point or {}
        secs = [(r["closed"] - r["t0"]) / 1000.0 for r in bad if r["closed"] is not None]
        res = {"n": len(bad), "n_all_real": sum(1 for r in self.real_all if r["wrong_side"]), "sum_r": sum(r["r"] for r in bad),
               "mean_seconds_open": mean(secs), "upper_bound_effect": (point.get("raw") - point.get("adj"))
               if (point.get("raw") is not None and point.get("adj") is not None) else None,
               "top": [{"id": r["id"], "symbol": r["sym"], "r": r["r"]} for r in sorted(bad, key=lambda x: x["r"])[:self.a.top]]}
        self.rep["h9"] = res
        L.append("")
        L.append("== H9 wrong-side targets (real 'hedef' exit on the losing side of the entry) ==")
        L.append("  REAL_W n %d (all real %d), sum R %s, mean seconds open %s · upper bound on the gap (their R as 0): %s" % (
            len(bad), res["n_all_real"], ff(res["sum_r"]), ff(res["mean_seconds_open"], 1, False), ff(res["upper_bound_effect"])))
        self.evaluate("H9", point if point.get("raw") is not None else None, reps, n_cf=len(self.cf), n_real=len(self.re),
                      n_days=len(self.days), single=single)

    # -------------------------------------------------------------- H10
    def h10(self) -> None:
        L = self.L
        real_tab: dict = defaultdict(list)
        for r in self.re:
            real_tab[(r["basis"] or "NO_EXIT_FILL", r["first_source"] or "-")].append(r["r"])
        cf_tab: dict = defaultdict(list)
        for r in self.cf:
            cf_tab[r["basis"] or "-"].append(r["r"])
        bar = lambda r: r["first_source"] in ("BAR_OPEN", "UNKNOWN")  # noqa: E731
        feeders = [feed_mean("raw"), feed_mean("nb", include=lambda s, r: s == "cf" or not bar(r))]
        point, reps, single = self.day_boot(feeders, lambda d: ({"raw": plain_gap(d, "raw"), "adj": plain_gap(d, "nb")}
                                                                if plain_gap(d, "raw") is not None else None), "h10")
        point = point or {}
        keep = [r for r in self.re if not bar(r)]
        res = {"real_exit_basis_x_first_source": {"%s|%s" % k: {"n": len(v), "mean_r": mean(v)} for k, v in sorted(real_tab.items())},
               "cf_net_exit_basis": {k: {"n": len(v), "mean_r": mean(v)} for k, v in sorted(cf_tab.items())},
               "gap_without_bar_decided_real_exits": point.get("adj"), "ci95_day": rep_ci(reps, lambda d: d["adj"])}
        self.rep["h10"] = res
        L.append("")
        L.append("== H10 price source (descriptive) ==")
        table(L, "  real exits by exit_fill.basis x first_source", ["basis", "first_source", "n", "mean_r"],
              [[k[0], k[1], len(v), ff(mean(v))] for k, v in sorted(real_tab.items())])
        table(L, "  CF exits by net_exit_basis", ["net_exit_basis", "n", "mean_r"], [[k, len(v), ff(mean(v))] for k, v in sorted(cf_tab.items())])
        L.append("  gap without real exits decided by closed 1h bars (n %d): %s %s" % (
            len(keep), ff(point.get("adj")), en_ci(res["ci95_day"], single)))
        self.evaluate("H10", point if point.get("raw") is not None else None, reps, n_cf=len(self.cf), n_real=len(keep),
                      n_days=len({r["day"] for r in self.cf + keep}), single=single)

    # -------------------------------------------------------------- S-MAIN
    def s_main(self) -> None:
        L = self.L
        path = self.state / SHADOW_FILE
        L.append("")
        L.append("== S-MAIN main-bot counterfactuals (shadow_book.json, book == 'main') ==")
        if not self._note_input(path):
            self.rep["s_main"] = {"status": NO_DATA}
            L.append("  %s" % NO_DATA)
            return
        doc = load_json(path, required=False, warn=self.warn, hook=shadow_hook)
        if not isinstance(doc, dict):
            self.rep["s_main"] = {"status": NO_DATA}
            L.append("  %s (unreadable or skipped)" % NO_DATA)
            return
        meta = {k: as_dict(doc.get("meta")).get(k) for k in ("lm_expired", "lm_dropped", "lm_superseded")}
        now_ms = int(self.now.timestamp() * 1000)
        rows = [t for t in (doc.get("trades") or []) if isinstance(t, dict) and t.get(SLIM) == "main" and t.get("book") == "main"]
        del doc
        pend_age = sorted((now_ms - r["t0"]) / 3_600_000.0 for r in rows if not r["lab"] and r["t0"] is not None)
        lab = [r for r in rows if r["lab"] and r["r"] is not None]
        by_mt: dict = defaultdict(list)
        for r in lab:
            by_mt[r["mt"]].append(r["r"])
        res = {"n": len(rows), "labelled": sum(1 for r in rows if r["lab"]), "pending": sum(1 for r in rows if not r["lab"]),
               "pending_age_h": {"p50": quantile(pend_age, 0.5), "max": pend_age[-1] if pend_age else None},
               "r_net_by_reason": {"CHIEF*": {"n": sum(1 for r in lab if r["chief"]), "mean": mean(r["r"] for r in lab if r["chief"])},
                                   "other": {"n": sum(1 for r in lab if not r["chief"]),
                                             "mean": mean(r["r"] for r in lab if not r["chief"])}},
               "r_net_by_market_type": {k: {"n": len(v), "mean": mean(v)} for k, v in sorted(by_mt.items())},
               "spot_share": (sum(1 for r in rows if r["mt"] == "SPOT") / len(rows)) if rows else None, "meta": meta}
        self.rep["s_main"] = res
        L.append("  main CF rows %d · labelled %d · pending %d (age h p50 %s, max %s) · SPOT share %s · meta %s" % (
            res["n"], res["labelled"], res["pending"], ff(res["pending_age_h"]["p50"], 1, False),
            ff(res["pending_age_h"]["max"], 1, False), pct(res["spot_share"]), meta))
        table(L, "  r_net by reason / market type", ["group", "n", "mean_r_net"],
              [["reason CHIEF*", res["r_net_by_reason"]["CHIEF*"]["n"], ff(res["r_net_by_reason"]["CHIEF*"]["mean"])],
               ["reason other", res["r_net_by_reason"]["other"]["n"], ff(res["r_net_by_reason"]["other"]["mean"])]]
              + [["market %s" % k, v["n"], ff(v["mean"])] for k, v in res["r_net_by_market_type"].items()])

    # -------------------------------------------------------------- R (replay)
    def replay(self) -> None:
        L = self.L
        L.append("")
        L.append("== R replay of real trades as counterfactuals (--replay-klines) ==")
        d = self.a.replay_klines
        if not d:
            self.rep["replay"] = {"status": NO_DATA, "reason": "--replay-klines not given"}
            L.append("  %s (--replay-klines not given)" % NO_DATA)
            return
        files = {}
        for p in Path(d).iterdir():
            m = re.fullmatch(r"([A-Z0-9]+)-5m-(\d{4}-\d{2}-\d{2})\.csv", p.name)
            if m and p.is_file():
                files[(m.group(1), m.group(2))] = p
                self.inputs[str(p)] = file_info(p)
        if not files:
            self.rep["replay"] = {"status": NO_DATA, "reason": "no <SYMBOL>-5m-YYYY-MM-DD.csv files"}
            L.append("  %s (no <SYMBOL>-5m-YYYY-MM-DD.csv in %s)" % (NO_DATA, d))
            return
        res = replay_rows(self.re, files, self.ledger_cfg)
        rows = res["rows"]
        if not rows:
            self.rep["replay"] = {"status": NO_DATA, "reason": "no real trade fully covered", "skipped": res["skipped"]}
            L.append("  %s (no REAL_W trade fully covered; skipped %s)" % (NO_DATA, res["skipped"]))
            return
        per = aggs({"cf": rows}, lambda r: r["day"], [feed_mean("d", val=lambda r: r["diff"])])
        point, reps = boot_cluster(per, lambda dd: {"m": mean_of(dd, "d", "cf")}, self.B, _rng(self.seed, "replay"))
        by_b = {}
        for bk in ("B0", "B1", "B2", "B3", "B4", "?"):
            g = [r for r in rows if r["bk"] == bk]
            if g:
                pb = aggs({"cf": g}, lambda r: r["day"], [feed_mean("d", val=lambda r: r["diff"])])
                _p, rb = boot_cluster(pb, lambda dd: {"m": mean_of(dd, "d", "cf")}, self.B, _rng(self.seed, "replay" + bk))
                by_b[bk] = {"n": len(g), "mean_diff": mean(r["diff"] for r in g), "ci95_day": rep_ci(rb, lambda x: x["m"])}
        trans = Counter((r["real_exit"], r["replay_exit"]) for r in rows)
        out = {"status": "OK", "n": len(rows), "skipped": res["skipped"], "mean_diff": mean(r["diff"] for r in rows),
               "ci95_day": rep_ci(reps, lambda x: x["m"]), "by_bucket": by_b,
               "transition": {"%s->%s" % k: v for k, v in sorted(trans.items())}}
        self.rep["replay"] = out
        L.append("  replayed %d REAL_W trades (skipped %s): mean(replay r_net - realized R ex-funding) %s %s" % (
            len(rows), res["skipped"], ff(out["mean_diff"]), en_ci(out["ci95_day"], len(per) == 1)))
        table(L, "  by stop bucket", ["bucket", "n", "mean_diff", "ci95_day"],
              [["%s %s" % (k, BUCKET_RANGE[k]), v["n"], ff(v["mean_diff"]), en_ci(v["ci95_day"])] for k, v in by_b.items()])
        table(L, "  exit transition real -> replay", ["real", "replay", "n"], [[k[0], k[1], v] for k, v in sorted(trans.items())])

    # -------------------------------------------------------------- summary
    def summary_lines(self) -> list:
        h = self.head
        S = ["== BOX OLSAYDI–GERÇEK FARKI =="]
        if self.W:
            S.append("Pencere W: %s → %s UTC · %d gün · öğrenme başlangıcı %s" % (
                iso_ms(self.W[0])[:16].replace("T", " "), iso_ms(self.W[1])[:16].replace("T", " "), len(self.days),
                (iso_ms(self.learning_since) or "—")[:16].replace("T", " ")))
        sd = h["single_day"]
        S.append("Olsaydı (CF_NET): n=%d · ort. %s R %s" % (h["cf"]["n"], tr(h["cf"]["mean"]), tr_ci(h["cf"]["ci95_day"], sd)))
        S.append("Gerçek (aynı pencere): n=%d · ort. %s R %s" % (h["real"]["n"], tr(h["real"]["mean"]),
                                                               tr_ci(h["real"]["ci95_day"], sd)))
        S.append("Ham fark: %s R %s" % (tr(h["raw_gap"]), tr_ci(h["raw_gap_ci95_day"], sd)))
        p = self.primary
        S.append("Yön × stop katmanlı fark: %s R %s (kapsam %s, karışım etkisi %s R)" % (
            tr(p["within"]), tr_ci(p["ci95_day"], sd), "—" if p["coverage"] is None else "%%%.0f" % (p["coverage"] * 100),
            tr(p["mix"])))
        dl = self.delta
        S.append("Δbrüt (seviyede) %s R − Δmaliyet %s R = Δnet %s R" % (
            tr(dl["d_gross_level_raw"]), tr(dl["d_cost_raw"]), tr(dl["d_net_raw"])))
        for v in self.verdicts:
            S.append("%s %s: etki ≈ %s R %s → %s" % (v["h"], v["title_tr"], tr(v["effect_r"]),
                                                     tr_ci(v["ci95"], v["ci_flag"] == SINGLE_DAY), v["verdict"]))
        warn = list(self.warn)
        if self.missing_optional:
            warn.insert(0, "eksik isteğe bağlı dosya (%s): %s" % (NO_DATA, ", ".join(self.missing_optional)))
        S.append("Uyarılar:" + ("" if warn else " yok"))
        room = 29 - len(S)
        for w in warn[:max(0, room - 1)] if len(warn) > room else warn:
            S.append(" - " + w)
        if len(warn) > room:
            S.append(" - … %d uyarı daha (JSON/ayrıntı)" % (len(warn) - max(0, room - 1)))
        S.append(self.conclusion())
        return S

    def conclusion(self) -> str:
        n_cf, n_re = len(self.cf), len(self.re)
        if n_cf < MIN_N or n_re < MIN_N or len(self.days) < MIN_DAYS:
            return ("Sonuç: veri az (olsaydı n=%d, gerçek n=%d, gün=%d; en az %d/%d/%d gerekir) — hipotezler için hüküm "
                    "verilemez. Bu bir kâr iddiası değildir." % (n_cf, n_re, len(self.days), MIN_N, MIN_N, MIN_DAYS))
        groups = defaultdict(list)
        for v in self.verdicts:
            groups[v["verdict"]].append(v["h"])
        parts = []
        for word, label in ((V_YES, "farkı açıklayan"), (V_PART, "kısmen açıklayan"), (V_NO, "açıklamayan"), (V_THIN, "verisi az")):
            parts.append("%s: %s" % (label, ", ".join(groups[word]) if groups[word] else "yok"))
        raw = self.head["raw_gap"]
        lead = "ham fark %s R" % tr(raw)
        if raw is not None and raw <= 0:
            lead += " (olsaydı ortalaması gerçeğin üstünde değil)"
        return "Sonuç: %s; %s. Bu bir kâr iddiası değildir; yalnız olsaydı–gerçek farkının kaynağını ölçer." % (lead, "; ".join(parts))

    def run(self) -> tuple:
        self.load()
        self.load_xp()
        self.populations()
        self.s0()
        self.headline()
        self.h1()
        self.h2()
        self.h3()
        self.h4()
        self.h5()
        self.h6()
        self.h7()
        self.h8()
        self.h9()
        self.h10()
        self.s_main()
        self.replay()
        summary = self.summary_lines()
        self.rep["args"] = {k: v for k, v in vars(self.a).items()}
        self.rep["inputs"] = {k: v for k, v in self.inputs.items() if v}
        self.rep["warnings"] = list(self.warn)
        self.rep["missing_optional"] = list(self.missing_optional)
        self.rep["verdicts"] = [{k: v[k] for k in ("h", "effect_r", "ci95", "verdict", "n_cf", "n_real", "n_days",
                                                    "raw_gap", "adjusted_gap", "adjusted_ci95", "shrink", "ci_flag", "adjustment")}
                                for v in self.verdicts]
        self.rep["summary_tr"] = summary
        self.rep["conclusion_tr"] = summary[-1]
        order = ["schema", "generated_at", "args", "inputs", "populations", "s0", "h1", "h2", "h3", "h4", "h5", "h6", "h7",
                 "h8", "h9", "h10", "s_main", "replay", "verdicts", "warnings", "missing_optional", "summary_tr", "conclusion_tr"]
        doc = {k: self.rep.get(k) for k in order}
        return r4(doc), summary, self.L


# ----------------------------------------------------------------------------- replay helpers (lazy repo imports)
def _csv_bars(path: Path) -> dict:
    """Binance public-data 5m CSV → {open_ms: (open, high, low, close)}; a header row is skipped; µs open_time → ms."""
    out = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for rec in csv.reader(fh):
            if len(rec) < 5:
                continue
            try:
                t = float(rec[0])
                o, h, lo, c = (float(x) for x in rec[1:5])
            except ValueError:
                continue
            if t > 1e14:
                t /= 1000.0
            out[int(t)] = (o, h, lo, c)
    return out


def binance_symbol(sym: str) -> str:
    return str(sym).split(":")[0].replace("/", "").upper()


def replay_view(*, trade_id: str, symbol: str, side: int, opened_iso: str, opened_ms: int, entry: float, stop: float,
                box_mid: float, sig_ms: int) -> Any:
    """The ShadowTrade CounterfactualRecorder.record would write for this real trade (created_at = opened_at)."""
    from tradingbot.learn.shadow import ShadowTrade
    direction = side_name(side)
    tg = [box_mid] if (math.isfinite(box_mid) and box_mid > 0 and ((box_mid > entry) if side > 0 else (box_mid < entry))) else []
    h = box_eod_bars(sig_ms, opened_ms)
    created = parse_ts(opened_iso)
    return ShadowTrade(id="replay_" + trade_id, plan_id="signal_ts:%d" % sig_ms, symbol=symbol, market_type="USDM_PERP",
                       direction=direction, created_at=opened_iso, entry=float(entry), stop=float(stop), targets=tg,
                       horizon_bars=int(h), variant="as_planned", reason_not_opened=["REPLAY_OF_REAL"],
                       label_ts=(created + timedelta(minutes=5 * h)).isoformat(timespec="seconds"), tf_minutes=5,
                       leverage=1.0, book="b1_box_fade", signal_key="signal_ts:%d" % sig_ms, label_kind="TARGET_STOP_TIME",
                       approx=False)


def replay_rows(real_rows: list, files: dict, ledger_cfg: dict) -> dict:
    import pandas as pd

    from tradingbot.accounting import FuturesLedgerV2
    from tradingbot.learn.shadow import label_with_candles
    from tradingbot.learning_cf import ExecModel, net_outcome
    model = ExecModel.of_ledger(FuturesLedgerV2.from_dict(dict(ledger_cfg)))
    cache: dict = {}

    def bars(sym: str, day: str) -> Optional[dict]:
        k = (sym, day)
        if k not in cache:
            if len(cache) > 64:
                cache.clear()
            p = files.get(k)
            cache[k] = _csv_bars(p) if p else None
        return cache[k]
    rows, skipped = [], Counter()
    for r in sorted(real_rows, key=lambda x: (x["sym"], x["t0"])):
        if r["stop"] is None or r["entry_ref"] is None or r["t0"] is None:
            skipped["no_stop_or_entry"] += 1
            continue
        sig = r["sig"] if r["sig"] is not None else (r["t0"] // M5_MS - 1) * M5_MS
        d0 = sig - sig % DAY_MS
        bsym = binance_symbol(r["sym"])
        prev, cur = bars(bsym, day_of(d0 - DAY_MS)), bars(bsym, day_of(d0))
        if not prev or not cur:
            skipped["no_file"] += 1
            continue
        if any((d0 - DAY_MS + k * M5_MS) not in prev for k in range(288)):
            skipped["prev_day_incomplete"] += 1
            continue
        start = (r["t0"] // M5_MS) * M5_MS
        need = range(start, d0 + DAY_MS, M5_MS)
        if any(t not in cur for t in need):
            skipped["window_incomplete"] += 1
            continue
        box_mid = (max(v[1] for v in prev.values()) + min(v[2] for v in prev.values())) / 2.0
        h = box_eod_bars(sig, r["t0"])
        if h <= 0:
            skipped["horizon_0"] += 1
            continue
        view = replay_view(trade_id=r["id"], symbol=r["sym"], side=r["side"], opened_iso=iso_ms(r["t0"]).replace("Z", "+00:00"),
                           opened_ms=r["t0"], entry=r["entry_ref"], stop=r["stop"], box_mid=box_mid, sig_ms=sig)
        df = pd.DataFrame([{"timestamp": t, "open": v[0], "high": v[1], "low": v[2], "close": v[3]}
                           for t, v in sorted(cur.items()) if t >= start])
        g = label_with_candles(view, df)
        if g is None:
            skipped["unlabelled"] += 1
            continue
        out = net_outcome(view, df, g, model=model, funding_lookup=None)
        if out.get("r_net") is None:
            skipped[str(out.get("net_status") or "net_none")] += 1
            continue
        fund = r["parts"].get("funding")
        if fund is None or r["r"] is None:
            skipped["no_decomposition"] += 1
            continue
        realized_ex_f = r["r"] + fund
        rows.append({"id": r["id"], "day": r["day"], "bk": r["bk"], "r_net": float(out["r_net"]), "realized_ex_funding": realized_ex_f,
                     "diff": float(out["r_net"]) - realized_ex_f,
                     "real_exit": exit_class(r["exit"]), "replay_exit": exit_class(out.get("net_exit_reason"))})
    return {"rows": rows, "skipped": dict(skipped)}


# ----------------------------------------------------------------------------- CLI
def _configure_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Box olsaydı–gerçek farkı denetimi (salt okunur; H1-H10).")
    ap.add_argument("--state", default=None, help="state klasörü (varsayılan $%s)" % ENV_STATE)
    ap.add_argument("--book", default=DEFAULT_BOOK, help="defter klasörü (varsayılan %s)" % DEFAULT_BOOK)
    ap.add_argument("--learning-since", default=None, help="ISO; varsayılan learning_mode.json 'since'")
    ap.add_argument("--since", default=None, help="ISO; t0 alt sınırı (isteğe bağlı)")
    ap.add_argument("--until", default=None, help="ISO; t0 üst sınırı (isteğe bağlı)")
    ap.add_argument("--slots-change", default=None, help="ISO; varsayılan: slots == 40 olan ilk işlemin açılışı")
    ap.add_argument("--config", default=None, help="config.yaml (entry_universe.symbols sırası için)")
    ap.add_argument("--boot", type=int, default=2000, help="bootstrap tekrar sayısı (varsayılan 2000)")
    ap.add_argument("--seed", type=int, default=20261001, help="bootstrap tohumu (varsayılan 20261001)")
    ap.add_argument("--min-cell", type=int, default=3, help="katmanlamada gerçek tarafın hücre başına en az satırı")
    ap.add_argument("--pair-window-min", type=float, default=60.0, help="H6 zaman eşli fark penceresi (dk)")
    ap.add_argument("--refill-window-min", type=float, default=10.0, help="H6 yeniden doldurma penceresi (dk)")
    ap.add_argument("--replay-klines", default=None, help="<SYMBOL>-5m-YYYY-MM-DD.csv klasörü (isteğe bağlı)")
    ap.add_argument("--json-out", default=None, help="JSON çıktı dosyası (state dışında)")
    ap.add_argument("--top", type=int, default=10, help="en-iyi/en-büyük listelerinin uzunluğu")
    return ap


def _resolve(p: str) -> Path:
    return Path(p).expanduser().resolve()


def main(argv: Optional[list] = None, *, now: Optional[datetime] = None) -> int:
    _configure_console()
    ap = build_parser()
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code not in (0, None) else 0
    a.state = a.state or os.environ.get(ENV_STATE) or None
    if not a.state:
        print("HATA: --state verilmedi ve $%s tanımlı değil" % ENV_STATE, file=sys.stderr)
        return 2
    state = Path(a.state)
    if not state.is_dir():
        print("HATA: state klasörü yok: %s" % state, file=sys.stderr)
        return 2
    for name in ("learning_since", "since", "until", "slots_change"):
        v = getattr(a, name)
        if v and parse_ts(v) is None:
            print("HATA: --%s okunamadı: %r" % (name.replace("_", "-"), v), file=sys.stderr)
            return 2
    if a.boot < 1 or a.top < 0 or a.min_cell < 1:
        print("HATA: --boot ≥ 1, --top ≥ 0, --min-cell ≥ 1 olmalı", file=sys.stderr)
        return 2
    if a.config and not Path(a.config).is_file():
        print("HATA: --config dosyası yok: %s" % a.config, file=sys.stderr)
        return 2
    if a.replay_klines and not Path(a.replay_klines).is_dir():
        print("HATA: --replay-klines klasörü yok: %s" % a.replay_klines, file=sys.stderr)
        return 2
    if a.json_out:
        out = _resolve(a.json_out)
        st = state.resolve()
        inside = out == st or st in out.parents
        same_input = (a.config and out == _resolve(a.config)) or (a.replay_klines and _resolve(a.replay_klines) in out.parents)
        if inside or same_input:
            print("HATA: --json-out state içinde ya da bir girdi dosyasıyla aynı olamaz: %s" % out, file=sys.stderr)
            return 2
        if not out.parent.is_dir():
            print("HATA: --json-out klasörü yok: %s" % out.parent, file=sys.stderr)
            return 2
    try:
        doc, summary, details = Audit(a, now=now).run()
    except AuditError as exc:
        print("HATA: %s" % exc.msg, file=sys.stderr)
        return exc.code
    if a.json_out:                                     # written first: a closed pipe (| head) cannot lose it
        try:
            with open(a.json_out, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=1)
        except OSError as exc:                         # e.g. a /tmp file of the same name owned by another user
            print("HATA: --json-out yazılamadı: %s (%s)" % (a.json_out, type(exc).__name__), file=sys.stderr)
            return 2
    print("\n".join(summary))
    print("\n".join(details))
    if a.json_out:
        print("")
        print("JSON: %s" % a.json_out)
    print("")
    print(summary[-1])
    return 0


if __name__ == "__main__":
    try:
        rc = main()
        sys.stdout.flush()
    except BrokenPipeError:                            # output piped into head/less that exited early
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        rc = 0
    raise SystemExit(rc)
