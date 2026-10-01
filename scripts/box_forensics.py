# -*- coding: utf-8 -*-
"""BOX FORENSICS — read-only post-mortem of the Box PAPER book (b1_box_fade, state dir `strategy_paper_box`).

Question: "Why did the Box book lose, trade by trade, and which losses come from the rule versus from the plumbing
(stuck positions that the end-of-day close never flattened, entry drift, costs, unverified price paths)?"

READ-ONLY and STANDALONE: Python standard library only, no `tradingbot` import, never writes a file, never prints raw
state dicts (only computed fields; free text is passed through a redactor). Run on the VPS as:

    sudo /opt/tradingbot/venv/bin/python /tmp/box_forensics.py --state /opt/tradingbot/data/state [--json]

Inputs (under --state; only the ledger is required):
  <book>/futures_ledger.json   closed trades (`history`, TradeRecord fields) and open `positions` (Position fields)
  <book>.json                  book summary (counters, rejections, data_events_recent, data_checks, rule_params, ...)
  <book>/trade_memory.jsonl    entry rows (features.signal_close / signal_ts / stop_at_entry), joined by trade_id
  box_timer.json               Box 5m clock status (optional)
  monitoring_gaps.jsonl        outage records; only rows of this book are counted (optional)

Classification of a closed trade (first match wins):
  STUCK_MULTI_DAY  eod_close is on and the trade was still open more than --eod-grace-min minutes after the first UTC
                   midnight following its open (the rule's end-of-day flat should have closed it by then)
  EOD              exit_reason BOX_EOD_FLAT (the rule's end-of-day flat)
  STOP             exit_reason `stop` / `başa-baş stop`
  TARGET           exit_reason `hedef1` / `hedef2` / ...
  OTHER            anything else (liquidation, structure exit BOX_OUTSIDE_BREAKOUT_EXIT, manual, ...)
A legitimate EOD close always happens shortly AFTER midnight (first closed 5m bar of the new day), therefore crossing a
UTC midnight alone is not "stuck"; the grace window separates the two.
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True                     # read-only run: no __pycache__ next to /tmp/box_forensics.py

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, Iterable, List, Optional, Tuple  # noqa: E402

SCHEMA = "box_forensics_v1"
DEFAULT_STATE = "/opt/tradingbot/data/state"
DEFAULT_BOOK = "strategy_paper_box"
LEDGER_FILE = "futures_ledger.json"
MEMORY_FILE = "trade_memory.jsonl"
BOX_TIMER_FILE = "box_timer.json"
MONITORING_GAPS_FILE = "monitoring_gaps.jsonl"
M5_MS = 300_000

CLASSES = ("STUCK_MULTI_DAY", "EOD", "STOP", "TARGET", "OTHER")
EOD_REASONS = {"BOX_EOD_FLAT"}
STOP_REASONS = {"stop", "başa-baş stop", "basa-bas stop", "be_stop", "breakeven_stop"}
TARGET_PREFIXES = ("hedef", "tp", "target")

_KV_SECRET = re.compile(r"(?i)\b(api[_-]?key|apikey|secret|signature|token|password|passwd|x-mbx-apikey|authorization)"
                        r"(\s*[=:]\s*|\"\s*:\s*\")[^\s&\"',;]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+_=\-]{32,}")


def _mask_token(m: "re.Match[str]") -> str:
    """Long mixed-case / hex tokens look like keys; UPPER_SNAKE reason codes (DATA_FRAME_MISSING_5M ...) stay readable."""
    t = m.group(0)
    if re.fullmatch(r"[A-Fa-f0-9]+", t):
        return "***"
    if re.fullmatch(r"[A-Z0-9_:\-]+", t):
        return t
    if re.fullmatch(r"[a-z_\-]+", t):
        return t
    return "***"


# ----------------------------------------------------------------------------- small helpers
def redact(text: Any, limit: int = 240) -> Optional[str]:
    """Free text from state files (errors, reasons) → printable text without credential-looking tokens."""
    if text is None:
        return None
    s = str(text)
    s = _KV_SECRET.sub(lambda m: m.group(1) + m.group(2) + "***", s)
    s = _BEARER.sub("***", s)
    s = _LONG_TOKEN.sub(_mask_token, s)
    return s if len(s) <= limit else s[:limit] + "…"


def fnum(x: Any) -> Optional[float]:
    """Decimal strings / numbers → float; anything else (None, "", garbage, NaN) → None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_ts(x: Any) -> Optional[datetime]:
    """ISO-8601 string (with +00:00 / Z / naive) or epoch ms/s → aware UTC datetime; unreadable → None."""
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
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})\.(\d+)(.*)$", s)
    if m and len(m.group(2)) != 6:                     # older Pythons accept exactly 3 or 6 fraction digits
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
    return int(dt.timestamp() * 1000) if dt else None


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def short_ts(dt: Optional[datetime]) -> str:
    return dt.strftime("%m-%d %H:%M") if dt else "?"


def rnd(x: Optional[float], nd: int = 4) -> Optional[float]:
    return None if x is None else round(x, nd) + 0.0          # + 0.0 turns -0.0 into 0.0


def side_sign(side: Any) -> int:
    s = str(side or "").upper()
    return -1 if s in ("SHORT", "SELL") else 1


def as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def read_json_file(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    """(content, error). Missing file → (None, None); unreadable → (None, error text)."""
    if not path.is_file():
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, "%s: %s" % (type(exc).__name__, redact(exc, 160))


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    """Rows of a JSONL file; blank or malformed lines are skipped (the file is never modified)."""
    if not path.is_file():
        return
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


# ----------------------------------------------------------------------------- inputs
def load_memory(path: Path) -> Dict[str, Dict[str, Any]]:
    """trade_id → the LAST entry row's features (signal_close, signal_ts, stop_at_entry, ...)."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if row.get("kind") != "entry" or not row.get("trade_id"):
            continue
        feats = as_dict(row.get("features"))
        out[str(row["trade_id"])] = {"signal_close": fnum(feats.get("signal_close")), "signal_ts": feats.get("signal_ts"),
                                     "stop_at_entry": fnum(feats.get("stop_at_entry")), "recorded_at": row.get("recorded_at"),
                                     "strategy": feats.get("strategy")}
    return out


def reconcile(led: Dict[str, Any], start: Optional[float], wallet: Optional[float]) -> Optional[Dict[str, Any]]:
    """Wallet change = Σ closed pnl (fees + funding included) + open positions' realized part (gross realized − fees
    paid + funding net). A non-zero remainder means truncated history or an inconsistent ledger."""
    if start is None or wallet is None:
        return None
    closed = sum(fnum(h.get("pnl")) or fnum(h.get("net_pnl")) or 0.0 for h in (led.get("history") or []) if isinstance(h, dict))
    opened = 0.0
    for p in as_dict(led.get("positions")).values():
        p = as_dict(p)
        opened += ((fnum(as_dict(p.get("meta")).get("gross_realized")) or 0.0) - (fnum(p.get("fees_paid")) or 0.0)
                   + (fnum(p.get("funding_received")) or 0.0) - (fnum(p.get("funding_paid")) or 0.0))
    change = wallet - start
    return {"wallet_change": round(change, 4), "closed_pnl_all": round(closed, 4), "open_realized": round(opened, 4),
            "unexplained": round(change - closed - opened, 4)}


def summary_path(state: Path, book: str) -> Path:
    return state / ("strategy_paper.json" if book == "strategy_paper" else "%s.json" % book)


# ----------------------------------------------------------------------------- per trade
def classify(exit_reason: str, *, crossed: Optional[bool], overstay_min: Optional[float], eod_close: bool,
             grace_min: float) -> str:
    if eod_close and crossed and overstay_min is not None and overstay_min > grace_min:
        return "STUCK_MULTI_DAY"
    r = str(exit_reason or "").strip()
    rl = r.lower()
    if r in EOD_REASONS or r.upper() in EOD_REASONS:
        return "EOD"
    if rl in STOP_REASONS:
        return "STOP"
    if rl.startswith(TARGET_PREFIXES):
        return "TARGET"
    return "OTHER"


def day_crossing(opened: Optional[datetime], closed: Optional[datetime]) -> Tuple[Optional[bool], Optional[int], Optional[float]]:
    """(crossed a UTC midnight?, number of UTC midnights crossed, minutes open after the FIRST midnight)."""
    if opened is None or closed is None:
        return None, None, None
    days = (closed.date() - opened.date()).days
    if days <= 0:
        return False, 0, None
    first_midnight = datetime(opened.year, opened.month, opened.day, tzinfo=timezone.utc) + timedelta(days=1)
    return True, days, (closed - first_midnight).total_seconds() / 60.0


def signal_bar_close_ms(signal_ts: Any, data_source: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """Close time of the 5m bar the entry was based on: memory `signal_ts` (5m bar open) + 5 min, else the last closed
    5m bar recorded in the position's data_source (`bars["5m"]`, bar open ms) + 5 min."""
    ms = ts_ms(signal_ts)
    if ms is not None:
        return ms + M5_MS, "signal_ts"
    b = as_dict(as_dict(data_source).get("bars")).get("5m")
    ms = ts_ms(b)
    if ms is not None:
        return ms + M5_MS, "data_source.bars.5m"
    return None, None


def trade_row(i: int, h: Dict[str, Any], mem: Dict[str, Dict[str, Any]], *, eod_close: bool, grace_min: float) -> Dict[str, Any]:
    feats = as_dict(h.get("features"))
    tid = str(h.get("id") or "")
    m = mem.get(tid) or {}
    side = str(h.get("side") or "?").upper()
    sgn = side_sign(side)
    opened, closed = parse_ts(h.get("opened_at")), parse_ts(h.get("closed_at"))
    crossed, n_mid, overstay = day_crossing(opened, closed)
    hold_h = (closed - opened).total_seconds() / 3600.0 if (opened and closed) else None
    reason = str(h.get("exit_reason") or "")
    reason_cls = classify(reason, crossed=False, overstay_min=None, eod_close=False, grace_min=grace_min)
    cls = classify(reason, crossed=crossed, overstay_min=overstay, eod_close=eod_close, grace_min=grace_min)

    pnl = fnum(h.get("pnl"))
    if pnl is None:
        pnl = fnum(h.get("net_pnl"))
    fees = fnum(h.get("fees"))
    funding = fnum(h.get("funding"))
    gross = fnum(h.get("gross_pnl"))
    if gross is None and pnl is not None:
        gross = pnl + (fees or 0.0) - (funding or 0.0)
    entry = fnum(h.get("entry"))
    qty = fnum(h.get("quantity"))
    risk_usdt = fnum(feats.get("risk_usdt"))
    r_mult = fnum(h.get("r_multiple"))

    stop_at_entry = m.get("stop_at_entry")
    signal_close = m.get("signal_close")
    stop_pct_fill = None
    if risk_usdt and qty and entry:
        stop_pct_fill = risk_usdt / (qty * entry) * 100.0          # |entry - initial_stop| / entry (ledger truth)
    elif stop_at_entry and entry:
        stop_pct_fill = abs(entry - stop_at_entry) / entry * 100.0
    stop_pct_plan = abs(signal_close - stop_at_entry) / signal_close * 100.0 if (signal_close and stop_at_entry) else None
    drift = (entry - signal_close) / signal_close * 100.0 if (signal_close and entry) else None

    sbc, lag_basis = signal_bar_close_ms(m.get("signal_ts"), as_dict(feats.get("data_source")))
    lag_min = (opened.timestamp() * 1000 - sbc) / 60000.0 if (opened and sbc is not None) else None

    ef = as_dict(feats.get("exit_fill"))
    first_src = ef.get("first_source")
    if not ef:                                         # only stop/liquidation exits carry `exit_fill`
        exit_via = {"EOD": "RULE", "TARGET": "TARGET_LEVEL", "OTHER": "RULE_OR_MANUAL"}.get(reason_cls, "UNKNOWN")
    elif first_src in ("BAR_OPEN", "UNKNOWN"):
        exit_via = "CLOSED_BAR"
    elif first_src == "PRICE":
        exit_via = "LIVE_MARK"
    else:
        exit_via = "UNKNOWN"
    pu = feats.get("path_unverified")
    pu_d = as_dict(pu)
    cov = as_dict(feats.get("funding_coverage"))
    xs = as_dict(feats.get("exit_structure"))
    return {
        "n": i, "id": tid or None, "symbol": h.get("symbol"), "side": side,
        "opened_at": iso_z(opened), "closed_at": iso_z(closed), "hold_h": rnd(hold_h, 2),
        "crossed_utc_day": crossed, "utc_midnights_crossed": n_mid, "open_min_after_first_midnight": rnd(overstay, 1),
        "exit_reason": redact(reason, 80), "class": cls, "reason_class": reason_cls,
        "pnl": rnd(pnl), "r_multiple": rnd(r_mult), "gross_pnl": rnd(gross), "fees": rnd(fees),
        "entry_fee": rnd(fnum(h.get("entry_fee"))), "exit_fee": rnd(fnum(h.get("exit_fee"))),
        "slippage_cost": rnd(fnum(h.get("slippage_cost"))), "funding": rnd(funding),
        "funding_complete": cov.get("complete") if isinstance(cov.get("complete"), bool) else None,
        "leverage": h.get("leverage"), "entry": entry, "exit_price": fnum(h.get("exit_price")), "quantity": qty,
        "notional": rnd(entry * qty, 4) if (entry and qty) else None, "risk_usdt": rnd(risk_usdt),
        "fees_r": rnd(fees / risk_usdt, 4) if (fees is not None and risk_usdt) else None,
        "stop_pct_fill": rnd(stop_pct_fill, 3), "stop_pct_plan": rnd(stop_pct_plan, 3),
        "stop_at_entry": stop_at_entry, "signal_close": signal_close,
        "entry_drift_pct": rnd(drift, 3), "entry_drift_dir_pct": rnd(drift * sgn, 3) if drift is not None else None,
        "entry_lag_min": rnd(lag_min, 1), "entry_lag_basis": lag_basis,
        "exit_basis": ef.get("basis"), "exit_first_source": first_src, "exit_via": exit_via,
        "stop_triggered": ef.get("stop_triggered"),
        "path_unverified": bool(pu), "path_unverified_bars": pu_d.get("unresolved_bars") if pu_d else None,
        "path_any_stop_crossed": pu_d.get("any_stop_crossed") if pu_d else None,
        "exit_structure_reason": redact(xs.get("reason_code"), 80) if xs else None,
        "setup_type": h.get("setup_type") or None, "trigger_text": redact(h.get("trigger_text"), 80) or None,
        "mae_pct": rnd(fnum(h.get("mae_pct")), 3), "mfe_pct": rnd(fnum(h.get("mfe_pct")), 3), "bars_held": h.get("bars_held"),
        "memory_joined": bool(m),
    }


# ----------------------------------------------------------------------------- aggregation
def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnls = [r["pnl"] for r in rows if r.get("pnl") is not None]
    rs = [r["r_multiple"] for r in rows if r.get("r_multiple") is not None]
    wins = sum(1 for p in pnls if p > 0)
    pos = sum(p for p in pnls if p > 0)
    neg = -sum(p for p in pnls if p < 0)
    cum, peak, mdd = 0.0, 0.0, 0.0
    for r in sorted(rows, key=lambda x: x.get("closed_at") or ""):
        if r.get("r_multiple") is None:
            continue
        cum += r["r_multiple"]
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    srt = sorted(rs)
    med = None
    if srt:
        k = len(srt) // 2
        med = srt[k] if len(srt) % 2 else (srt[k - 1] + srt[k]) / 2.0
    return {"n": len(rows), "net_pnl": round(sum(pnls), 4), "wins": wins,
            "win_rate": round(wins / len(pnls), 4) if pnls else None,
            "mean_r": round(sum(rs) / len(rs), 4) if rs else None, "median_r": rnd(med, 4), "sum_r": round(sum(rs), 4),
            "profit_factor": round(pos / neg, 4) if neg > 0 else None,
            "max_drawdown_r": round(mdd, 4) if rs else None,
            "fees": round(sum(r["fees"] or 0.0 for r in rows), 4), "funding": round(sum(r["funding"] or 0.0 for r in rows), 4),
            "gross_pnl": round(sum(r["gross_pnl"] or 0.0 for r in rows), 4),
            "funding_incomplete": sum(1 for r in rows if r.get("funding_complete") is False),
            "path_unverified": sum(1 for r in rows if r.get("path_unverified"))}


def aggregate_all(rows: List[Dict[str, Any]], split_at: Optional[datetime]) -> Dict[str, Any]:
    by_class = {c: aggregate([r for r in rows if r["class"] == c]) for c in CLASSES}
    reasons: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        reasons.setdefault(r.get("exit_reason") or "?", []).append(r)
    out = {"overall": aggregate(rows), "by_class": by_class,
           "by_exit_reason": {k: aggregate(v) for k, v in sorted(reasons.items(), key=lambda kv: -len(kv[1]))},
           "by_side": {s: aggregate([r for r in rows if r.get("side") == s]) for s in sorted({r.get("side") or "?" for r in rows})},
           "excluding_stuck": aggregate([r for r in rows if r["class"] != "STUCK_MULTI_DAY"]), "split": None}
    if split_at is not None:
        before = [r for r in rows if r.get("opened_at") and parse_ts(r["opened_at"]) < split_at]
        after = [r for r in rows if r.get("opened_at") and parse_ts(r["opened_at"]) >= split_at]
        out["split"] = {"at": iso_z(split_at), "opened_before": aggregate(before), "opened_at_or_after": aggregate(after)}
    return out


def drift_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    d = [r for r in rows if r.get("entry_drift_pct") is not None]
    lags = sorted(r["entry_lag_min"] for r in rows if r.get("entry_lag_min") is not None)
    sp = [(r["stop_pct_fill"], r["stop_pct_plan"]) for r in rows if r.get("stop_pct_fill") is not None and r.get("stop_pct_plan") is not None]
    return {"n_with_signal_close": len(d),
            "mean_abs_drift_pct": round(sum(abs(r["entry_drift_pct"]) for r in d) / len(d), 4) if d else None,
            "mean_dir_drift_pct": round(sum(r["entry_drift_dir_pct"] for r in d) / len(d), 4) if d else None,
            "n_filled_after_favorable_move": sum(1 for r in d if r["entry_drift_dir_pct"] > 0),
            "n_filled_closer_to_stop": sum(1 for r in d if r["entry_drift_dir_pct"] < 0),
            "n_with_lag": len(lags), "median_lag_min": lags[len(lags) // 2] if lags else None,
            "max_lag_min": lags[-1] if lags else None,
            "n_stop_pct_both": len(sp),
            "mean_stop_pct_fill": round(sum(a for a, _ in sp) / len(sp), 4) if sp else None,
            "mean_stop_pct_plan": round(sum(b for _, b in sp) / len(sp), 4) if sp else None,
            "n_fill_stop_tighter_than_plan": sum(1 for a, b in sp if a < b)}


# ----------------------------------------------------------------------------- open positions / summary sections
def open_rows(positions: Dict[str, Any], mem: Dict[str, Dict[str, Any]], last_actions: Dict[str, Any], *, now: datetime,
              eod_close: bool, grace_min: float) -> List[Dict[str, Any]]:
    out = []
    for sym, p in sorted(as_dict(positions).items()):
        p = as_dict(p)
        feats, meta = as_dict(p.get("features")), as_dict(p.get("meta"))
        side = str(p.get("side") or "?").upper()
        sgn = side_sign(side)
        opened = parse_ts(p.get("opened_at"))
        crossed, n_mid, overstay = day_crossing(opened, now)
        entry = fnum(p.get("entry_avg")) or fnum(p.get("entry"))
        qty = fnum(p.get("qty")) if p.get("qty") is not None else fnum(p.get("units"))
        iqty = fnum(p.get("initial_qty")) or qty
        stop, istop = fnum(p.get("stop")), fnum(p.get("initial_stop"))
        istop = istop if istop is not None else stop
        last = fnum(p.get("last_price"))
        upnl = (last - entry) * qty * sgn if (last and entry and qty) else None
        risk = abs(entry - istop) * iqty if (entry and istop and iqty) else None
        m = mem.get(str(p.get("id") or "")) or {}
        sc = m.get("signal_close")
        gaps = as_dict(meta.get("ohlc_gaps"))
        la = as_dict(as_dict(last_actions).get(sym))
        out.append({
            "symbol": sym, "id": p.get("id"), "side": side, "opened_at": iso_z(opened),
            "age_h": rnd((now - opened).total_seconds() / 3600.0, 2) if opened else None,
            "crossed_utc_day": crossed, "utc_midnights_crossed": n_mid,
            "overdue_eod": bool(eod_close and crossed and overstay is not None and overstay > grace_min),
            "entry": entry, "qty": qty, "notional": rnd(entry * qty, 4) if (entry and qty) else None,
            "stop": stop, "initial_stop": istop,
            "stop_pct": rnd(abs(entry - istop) / entry * 100.0, 3) if (entry and istop) else None,
            "leverage": p.get("leverage"), "last_price": last, "liquidation_price": fnum(p.get("liquidation_price")),
            "unrealized_pnl": rnd(upnl), "unrealized_r": rnd(upnl / risk, 4) if (upnl is not None and risk) else None,
            "mae_pct": rnd(fnum(p.get("mae_pct")), 3), "mfe_pct": rnd(fnum(p.get("mfe_pct")), 3),
            "entry_drift_pct": rnd((entry - sc) / sc * 100.0, 3) if (sc and entry) else None,
            "path_unverified": bool(feats.get("path_unverified")),
            "ohlc_gap_bars": sum(len(v or []) for v in gaps.values() if isinstance(v, list)),
            "ohlc_cursor": {k: iso_z(parse_ts(v)) for k, v in as_dict(meta.get("ohlc_cursor")).items()},
            "last_action": {"action": la.get("action"), "reason": redact(la.get("reason"), 80), "at": la.get("at"),
                            "stage": la.get("stage"), "would_act": la.get("would_act")} if la else None,
        })
    return out


def events_section(events: Any) -> Dict[str, Any]:
    evs = [e for e in (events or []) if isinstance(e, dict)]
    by_kind: Dict[str, int] = {}
    by_kind_reason: Dict[str, int] = {}
    by_stage: Dict[str, int] = {}
    would: Dict[str, int] = {}
    blocked = []
    for e in evs:
        k = str(e.get("kind") or "?")
        by_kind[k] = by_kind.get(k, 0) + 1
        kr = "%s:%s" % (k, redact(e.get("reason"), 80) or "")
        by_kind_reason[kr] = by_kind_reason.get(kr, 0) + 1
        if k == "REJECT":
            st = str(e.get("stage") or "?")
            by_stage[st] = by_stage.get(st, 0) + 1
            wa = str(e.get("would_act") or "NONE")
            would[wa] = would.get(wa, 0) + 1
            if wa.upper() == "CLOSE":
                blocked.append({"symbol": e.get("symbol"), "at": e.get("at"), "reason": redact(e.get("reason"), 80)})
    ats = sorted(str(e.get("at")) for e in evs if e.get("at"))
    return {"n": len(evs), "first_at": ats[0] if ats else None, "last_at": ats[-1] if ats else None,
            "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
            "by_kind_reason": dict(sorted(by_kind_reason.items(), key=lambda kv: -kv[1])),
            "reject_by_stage": by_stage, "reject_would_act": would, "blocked_rule_closes": blocked}


def checks_section(checks: Any) -> Dict[str, Any]:
    c = as_dict(checks)
    by_reason: Dict[str, int] = {}
    ok = 0
    for v in c.values():
        v = as_dict(v)
        if v.get("ok"):
            ok += 1
        key = redact(v.get("reason"), 80) or ("OK" if v.get("ok") else "?")
        by_reason[key] = by_reason.get(key, 0) + 1
    return {"n_symbols": len(c), "ok": ok, "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1]))}


def summary_section(sm: Dict[str, Any]) -> Dict[str, Any]:
    rej = {str(k): int(fnum(v) or 0) for k, v in as_dict(sm.get("rejections")).items()}
    fund = as_dict(sm.get("funding"))
    mg = as_dict(sm.get("monitoring_gap"))
    return {"generated_at": sm.get("generated_at"), "rule_evaluated_at": sm.get("rule_evaluated_at"),
            "rule_stale_s": fnum(sm.get("rule_stale_s")), "rule_family": sm.get("rule_family"), "name": sm.get("name"),
            "counters": {str(k): v for k, v in as_dict(sm.get("counters")).items()},
            "rejections": dict(sorted(rej.items(), key=lambda kv: -kv[1])),
            "rejections_total": sum(rej.values()),
            "rejections_data_total": sum(v for k, v in rej.items() if k.startswith("DATA_")),
            "structures_mode": as_dict(sm.get("structures")).get("mode"),
            "funding_incomplete_closed_ids": len(fund.get("incomplete_closed") or []),
            "funding_pending_positions": len(as_dict(fund.get("pending_positions"))),
            "monitoring_gap": {"from": mg.get("from"), "to": mg.get("to"), "positions": list(mg.get("positions") or [])} if mg else None,
            "data_gaps_now": sorted(as_dict(sm.get("data_gaps")).keys())}


def box_timer_section(bt: Any) -> Optional[Dict[str, Any]]:
    bt = as_dict(bt)
    if not bt:
        return None
    le = as_dict(bt.get("last_eval"))
    return {"generated_at": bt.get("generated_at"), "alive": bt.get("alive"), "evaluations": bt.get("evaluations"),
            "missed_bars": bt.get("missed_bars"), "duplicates_blocked": bt.get("duplicates_blocked"), "errors": bt.get("errors"),
            "last_error": redact(bt.get("last_error"), 200), "lag_p50_s": bt.get("lag_p50_s"), "lag_max_s": bt.get("lag_max_s"),
            "last_eval": {"bar_open": le.get("bar_open"), "at": le.get("at"), "lag_s": le.get("lag_s"),
                          "symbols": le.get("symbols"), "frames": le.get("frames"), "fetch_errors": le.get("fetch_errors"),
                          "price_gaps": le.get("price_gaps"), "positions": le.get("positions")} if le else None}


def monitoring_gaps_section(path: Path, book: str) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    rows = [r for r in iter_jsonl(path) if str(r.get("book") or "") == book and r.get("kind", "MONITORING_GAP") == "MONITORING_GAP"]
    hours = sum((fnum(r.get("gap_s")) or 0.0) for r in rows) / 3600.0
    return {"n": len(rows), "total_hours": round(hours, 2),
            "recent": [{"from": r.get("from"), "to": r.get("to"), "hours": rnd((fnum(r.get("gap_s")) or 0.0) / 3600.0, 2),
                        "positions": list(r.get("positions") or [])} for r in rows[-8:]]}


# ----------------------------------------------------------------------------- report
def build_report(state: Path, *, book: str = DEFAULT_BOOK, grace_min: float = 60.0, eod_mode: str = "auto",
                 since: Optional[str] = None, split_at: Optional[datetime] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    warnings: List[str] = []
    led_path = state / book / LEDGER_FILE
    sm_path = summary_path(state, book)
    mem_path = state / book / MEMORY_FILE
    bt_path = state / BOX_TIMER_FILE
    mg_path = state / MONITORING_GAPS_FILE
    led, led_err = read_json_file(led_path)
    if led_err or not isinstance(led, dict):
        return {"kind": "BOX_FORENSICS", "schema": SCHEMA, "state": str(state), "book": book,
                "error": led_err or ("ledger not found: %s" % led_path if led is None else "ledger is not a JSON object")}
    sm, sm_err = read_json_file(sm_path)
    if sm_err:
        warnings.append("summary unreadable: %s" % sm_err)
    sm = as_dict(sm)
    bt, bt_err = read_json_file(bt_path)
    if bt_err:
        warnings.append("box_timer unreadable: %s" % bt_err)
    try:
        mem = load_memory(mem_path)
    except OSError as exc:
        mem = {}
        warnings.append("trade memory unreadable: %s" % redact(exc, 160))

    rp = as_dict(sm.get("rule_params"))
    if eod_mode == "on":
        eod_close, eod_src = True, "--eod-close on"
    elif eod_mode == "off":
        eod_close, eod_src = False, "--eod-close off"
    elif isinstance(rp.get("eod_close"), bool):
        eod_close, eod_src = rp["eod_close"], "summary rule_params"
    else:
        eod_close, eod_src = True, "default (config b1_box_fade eod_close: true)"

    hist = [h for h in (led.get("history") or []) if isinstance(h, dict)]
    skipped = len(led.get("history") or []) - len(hist)
    if skipped:
        warnings.append("%d history rows are not objects and were skipped" % skipped)
    hist.sort(key=lambda h: (ts_ms(h.get("closed_at")) or 0, str(h.get("id") or "")))
    if since:
        sdt = parse_ts(since)
        if sdt is None:
            warnings.append("--since %r unreadable; ignored" % since)
        else:
            hist = [h for h in hist if (parse_ts(h.get("closed_at")) or sdt) >= sdt]
    rows = [trade_row(i + 1, h, mem, eod_close=eod_close, grace_min=grace_min) for i, h in enumerate(hist)]
    missing_ts = sum(1 for r in rows if r["opened_at"] is None or r["closed_at"] is None)
    if missing_ts:
        warnings.append("%d trades lack a readable opened_at/closed_at; their day crossing is unknown" % missing_ts)

    start = fnum(led.get("starting_equity"))
    wallet = fnum(led.get("wallet_balance"))
    if wallet is None:
        wallet = fnum(led.get("equity"))
    positions = as_dict(led.get("positions"))
    opens = open_rows(positions, mem, as_dict(sm.get("last_actions")), now=now, eod_close=eod_close, grace_min=grace_min)
    agg = aggregate_all(rows, split_at)
    report: Dict[str, Any] = {
        "kind": "BOX_FORENSICS", "schema": SCHEMA, "generated_at": iso_z(now), "state": str(state), "book": book,
        "read_only": True,
        "files": {"ledger": str(led_path), "summary": str(sm_path) if sm else None,
                  "trade_memory": str(mem_path) if mem_path.is_file() else None,
                  "box_timer": str(bt_path) if isinstance(bt, dict) else None,
                  "monitoring_gaps": str(mg_path) if mg_path.is_file() else None},
        "settings": {"eod_close": eod_close, "eod_close_source": eod_src, "eod_grace_min": grace_min, "since": since,
                     "split_at": iso_z(split_at),
                     "rule_params": {k: rp.get(k) for k in ("near_frac", "trigger", "long_stop", "exit_kind", "exit_r", "min_stop_pct",
                                                            "eod_close", "allow_long", "allow_short", "leverage", "leverage_max")
                                     if k in rp}},
        "ledger": {"starting_equity": start, "wallet_balance": wallet, "updated_at": led.get("updated_at"),
                   "return_pct": round((wallet / start - 1.0) * 100.0, 2) if (start and wallet is not None) else None,
                   "closed_total": len(led.get("history") or []), "closed_in_report": len(rows), "open_total": len(positions),
                   "memory_rows_joined": sum(1 for r in rows if r["memory_joined"]),
                   "reconciliation": reconcile(led, start, wallet)},
        "trades": rows, "aggregates": agg, "entry_quality": drift_stats(rows), "open_positions": opens,
        "summary": summary_section(sm) if sm else None,
        "data_events": events_section(sm.get("data_events_recent")) if sm else None,
        "data_checks": checks_section(sm.get("data_checks")) if sm else None,
        "box_timer": box_timer_section(bt), "monitoring_gaps": monitoring_gaps_section(mg_path, book),
        "warnings": warnings,
    }
    report["highlights"] = highlights(report)
    return report


def highlights(rep: Dict[str, Any]) -> List[str]:
    """Facts only (no recommendation): where the loss sits and which plumbing flags are present."""
    out: List[str] = []
    agg = rep["aggregates"]
    ov, st, ex = agg["overall"], agg["by_class"]["STUCK_MULTI_DAY"], agg["excluding_stuck"]
    if ov["n"]:
        out.append("closed %d trades, net %+.2f USDT, mean R %s, win rate %s" % (
            ov["n"], ov["net_pnl"], _f(ov["mean_r"], "+.3f"), _pct(ov["win_rate"])))
    if st["n"]:
        share = (st["net_pnl"] / ov["net_pnl"] * 100.0) if ov["net_pnl"] else None
        out.append("STUCK_MULTI_DAY: %d trades held past UTC midnight + %g min with eod_close on, net %+.2f USDT%s, mean R %s" % (
            st["n"], rep["settings"]["eod_grace_min"], st["net_pnl"],
            (" (%.0f%% of total net)" % share) if share is not None and ov["net_pnl"] < 0 and st["net_pnl"] < 0 else "",
            _f(st["mean_r"], "+.3f")))
        out.append("excluding STUCK_MULTI_DAY: %d trades, net %+.2f USDT, mean R %s, win rate %s" % (
            ex["n"], ex["net_pnl"], _f(ex["mean_r"], "+.3f"), _pct(ex["win_rate"])))
    if ov["n"] and ov["gross_pnl"]:
        out.append("costs: fees %.2f USDT, funding %+.2f USDT, gross (before fees/funding) %+.2f USDT" % (
            ov["fees"], ov["funding"], ov["gross_pnl"]))
    eq = rep["entry_quality"]
    if eq["n_with_signal_close"]:
        out.append("entry drift vs 5m signal close (%d trades): mean |drift| %.3f%%, mean directional %+.3f%% "
                   "(%d filled after a favorable move, %d closer to the stop)" % (
                       eq["n_with_signal_close"], eq["mean_abs_drift_pct"], eq["mean_dir_drift_pct"],
                       eq["n_filled_after_favorable_move"], eq["n_filled_closer_to_stop"]))
    if eq["n_with_lag"]:
        out.append("entry lag after the 5m signal bar close: median %.1f min, max %.1f min (%d trades)" % (
            eq["median_lag_min"], eq["max_lag_min"], eq["n_with_lag"]))
    if ov["funding_incomplete"] or ov["path_unverified"]:
        out.append("flags: %d trades with incomplete funding, %d closed with an unverified price path" % (
            ov["funding_incomplete"], ov["path_unverified"]))
    overdue = [o["symbol"] for o in rep["open_positions"] if o["overdue_eod"]]
    if overdue:
        out.append("OPEN and past EOD grace (eod_close should have flattened): %s" % ", ".join(str(s) for s in overdue))
    de = rep.get("data_events") or {}
    if de.get("blocked_rule_closes"):
        out.append("%d recent data REJECT events where the rule WOULD have closed (close blocked by data)" % len(de["blocked_rule_closes"]))
    return out


# ----------------------------------------------------------------------------- text rendering
def _f(v: Any, spec: str = ".2f", dash: str = "-") -> str:
    if v is None:
        return dash
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return str(v)


def _pct(v: Optional[float]) -> str:
    return "-" if v is None else "%.0f%%" % (v * 100.0)


def _px(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return ("%.6g" % v) if abs(v) < 1000 else ("%.2f" % v)


def _agg_line(name: str, a: Dict[str, Any]) -> str:
    return "  %-18s %4d %10s %8s %8s %6s %7s %8s %8s %8s" % (
        name, a["n"], _f(a["net_pnl"], "+.2f"), _f(a["mean_r"], "+.3f"), _f(a["median_r"], "+.3f"), _pct(a["win_rate"]),
        _f(a["profit_factor"], ".2f"), _f(a["max_drawdown_r"], "+.2f"), _f(a["fees"], ".2f"), _f(a["funding"], "+.2f"))


def render(rep: Dict[str, Any]) -> str:
    if rep.get("error"):
        return "BOX FORENSICS: %s" % rep["error"]
    L: List[str] = []
    s, lg = rep["settings"], rep["ledger"]
    L.append("BOX FORENSICS (PAPER, read-only) · book %s · state %s · now %s" % (rep["book"], rep["state"], rep["generated_at"]))
    L.append("ledger: start %s · wallet %s (%s%%) · closed %d (in report %d) · open %d · updated_at %s · memory joined %d" % (
        _f(lg["starting_equity"]), _f(lg["wallet_balance"]), _f(lg["return_pct"], "+.2f"), lg["closed_total"],
        lg["closed_in_report"], lg["open_total"], lg["updated_at"] or "-", lg["memory_rows_joined"]))
    rc = lg.get("reconciliation")
    if rc:
        L.append("reconcile: wallet change %s = closed Σpnl %s + open realized %s; unexplained %s" % (
            _f(rc["wallet_change"], "+.4f"), _f(rc["closed_pnl_all"], "+.4f"), _f(rc["open_realized"], "+.4f"),
            _f(rc["unexplained"], "+.4f")))
    rp = s["rule_params"]
    L.append("eod_close=%s (%s) · STUCK grace %g min after first UTC midnight%s%s" % (
        s["eod_close"], s["eod_close_source"], s["eod_grace_min"], (" · since %s" % s["since"]) if s["since"] else "",
        (" · rule_params " + ", ".join("%s=%s" % kv for kv in rp.items())) if rp else ""))
    for w in rep["warnings"]:
        L.append("WARNING: %s" % w)
    L.append("")
    L.append("CLOSED TRADES (by close time)")
    for t in rep["trades"]:
        cross = "same UTC day" if t["crossed_utc_day"] is False else (
            "crossed %s UTC midnight(s), open %s min after the first" % (t["utc_midnights_crossed"], _f(t["open_min_after_first_midnight"], ".0f"))
            if t["crossed_utc_day"] else "day crossing unknown")
        L.append("%3d %-15s %-12s %-5s %s -> %s  %sh  %s  reason=%s" % (
            t["n"], t["class"], t["symbol"] or "?", t["side"], short_ts(parse_ts(t["opened_at"])), short_ts(parse_ts(t["closed_at"])),
            _f(t["hold_h"], ".1f"), cross, t["exit_reason"] or "?"))
        fund = _f(t["funding"], "+.4f") + (" (incomplete)" if t["funding_complete"] is False else "")
        L.append("      pnl %s  R %s  fees %s (%s R)  funding %s  lev %s  entry %s -> exit %s" % (
            _f(t["pnl"], "+.4f"), _f(t["r_multiple"], "+.3f"), _f(t["fees"], ".4f"), _f(t["fees_r"], ".3f"), fund,
            t["leverage"] if t["leverage"] is not None else "-", _px(t["entry"]), _px(t["exit_price"])))
        extra = []
        extra.append("stop%% %s%s" % (_f(t["stop_pct_fill"], ".2f"), (" (plan %s)" % _f(t["stop_pct_plan"], ".2f")) if t["stop_pct_plan"] is not None else ""))
        if t["entry_drift_pct"] is not None:
            extra.append("drift %s%% (dir %s%%)" % (_f(t["entry_drift_pct"], "+.3f"), _f(t["entry_drift_dir_pct"], "+.3f")))
        else:
            extra.append("drift -")
        if t["entry_lag_min"] is not None:
            extra.append("lag %s min" % _f(t["entry_lag_min"], ".1f"))
        extra.append("exit via %s" % t["exit_via"])
        if t["exit_basis"]:
            extra.append("basis %s/%s" % (t["exit_basis"], t["exit_first_source"] or "?"))
        if t["path_unverified"]:
            extra.append("PATH_UNVERIFIED(%s bars%s)" % (t["path_unverified_bars"] if t["path_unverified_bars"] is not None else "?",
                                                         ", stop crossed" if t["path_any_stop_crossed"] else ""))
        if t["exit_structure_reason"]:
            extra.append("structure %s" % t["exit_structure_reason"])
        if t["trigger_text"]:
            extra.append("entry %s" % t["trigger_text"])
        L.append("      " + "  ".join(extra))
    if not rep["trades"]:
        L.append("  (none)")
    L.append("")
    hdr = "  %-18s %4s %10s %8s %8s %6s %7s %8s %8s %8s" % ("", "n", "net USDT", "mean R", "med R", "win", "PF", "maxDD R", "fees", "funding")
    agg = rep["aggregates"]
    L.append("BY CLASS")
    L.append(hdr)
    for c in CLASSES:
        L.append(_agg_line(c, agg["by_class"][c]))
    L.append(_agg_line("ALL", agg["overall"]))
    L.append(_agg_line("ALL minus STUCK", agg["excluding_stuck"]))
    L.append("BY EXIT REASON")
    for k, a in agg["by_exit_reason"].items():
        L.append(_agg_line(str(k)[:18], a))
    L.append("BY SIDE")
    for k, a in agg["by_side"].items():
        L.append(_agg_line(str(k)[:18], a))
    if agg.get("split"):
        sp = agg["split"]
        L.append("SPLIT at %s (by open time)" % sp["at"])
        L.append(_agg_line("opened before", sp["opened_before"]))
        L.append(_agg_line("opened at/after", sp["opened_at_or_after"]))
    eq = rep["entry_quality"]
    L.append("ENTRY QUALITY: signal_close known %d · mean |drift| %s%% · mean dir drift %s%% · after favorable move %d · closer to stop %d"
             " · median lag %s min · stop%% fill/plan %s/%s (fill tighter %d of %d)" % (
                 eq["n_with_signal_close"], _f(eq["mean_abs_drift_pct"], ".3f"), _f(eq["mean_dir_drift_pct"], "+.3f"),
                 eq["n_filled_after_favorable_move"], eq["n_filled_closer_to_stop"], _f(eq["median_lag_min"], ".1f"),
                 _f(eq["mean_stop_pct_fill"], ".2f"), _f(eq["mean_stop_pct_plan"], ".2f"), eq["n_fill_stop_tighter_than_plan"],
                 eq["n_stop_pct_both"]))
    L.append("")
    L.append("OPEN POSITIONS")
    for o in rep["open_positions"]:
        la = o["last_action"] or {}
        L.append("  %-12s %-5s opened %s  age %sh  %s%s" % (
            o["symbol"], o["side"], short_ts(parse_ts(o["opened_at"])), _f(o["age_h"], ".1f"),
            ("crossed %s UTC midnight(s)" % o["utc_midnights_crossed"]) if o["crossed_utc_day"] else "same UTC day",
            "  OVERDUE_EOD" if o["overdue_eod"] else ""))
        L.append("      entry %s stop %s (init %s, %s%%) lev %s last %s uPnL %s (%s R) mae/mfe %s/%s%%%s%s" % (
            _px(o["entry"]), _px(o["stop"]), _px(o["initial_stop"]), _f(o["stop_pct"], ".2f"), o["leverage"], _px(o["last_price"]),
            _f(o["unrealized_pnl"], "+.4f"), _f(o["unrealized_r"], "+.3f"), _f(o["mae_pct"], "+.2f"), _f(o["mfe_pct"], "+.2f"),
            "  PATH_UNVERIFIED" if o["path_unverified"] else "", ("  ohlc gaps %d" % o["ohlc_gap_bars"]) if o["ohlc_gap_bars"] else ""))
        if la:
            L.append("      last rule action: %s %s at %s%s" % (la.get("action"), la.get("reason") or "", la.get("at") or "?",
                                                               (" (would %s)" % la["would_act"]) if la.get("would_act") else ""))
    if not rep["open_positions"]:
        L.append("  (none)")
    L.append("")
    sm = rep.get("summary")
    if sm is None:
        L.append("SUMMARY: %s not found (counters, rejections and data events unavailable)" % summary_path(Path(rep["state"]), rep["book"]))
    else:
        L.append("SUMMARY: generated %s · rule evaluated %s (stale %s s) · structures %s · counters %s" % (
            sm["generated_at"] or "-", sm["rule_evaluated_at"] or "-", _f(sm["rule_stale_s"], ".0f"), sm["structures_mode"] or "-",
            ", ".join("%s=%s" % kv for kv in sm["counters"].items()) or "-"))
        if sm["monitoring_gap"]:
            mg = sm["monitoring_gap"]
            L.append("  monitoring gap this process: %s -> %s positions %s" % (mg["from"], mg["to"], ", ".join(mg["positions"]) or "-"))
        L.append("  rejections total %d (data %d):" % (sm["rejections_total"], sm["rejections_data_total"]))
        for k, v in list(sm["rejections"].items())[:25]:
            L.append("    %6d  %s" % (v, redact(k, 90)))
        if len(sm["rejections"]) > 25:
            L.append("    ... %d more reasons (see --json)" % (len(sm["rejections"]) - 25))
        de = rep["data_events"]
        L.append("  data events (recent %d, %s .. %s): %s" % (de["n"], de["first_at"] or "-", de["last_at"] or "-",
                                                           ", ".join("%s=%d" % kv for kv in de["by_kind"].items()) or "-"))
        for k, v in list(de["by_kind_reason"].items())[:15]:
            L.append("    %6d  %s" % (v, k))
        if de["reject_by_stage"]:
            L.append("    REJECT by stage: %s · would_act: %s" % (
                ", ".join("%s=%d" % kv for kv in de["reject_by_stage"].items()),
                ", ".join("%s=%d" % kv for kv in de["reject_would_act"].items())))
        for b in de["blocked_rule_closes"][:10]:
            L.append("    blocked rule CLOSE: %s at %s (%s)" % (b["symbol"], b["at"], b["reason"]))
        dc = rep["data_checks"]
        L.append("  data checks (last tour): %d symbols, %d ok · %s" % (
            dc["n_symbols"], dc["ok"], ", ".join("%s=%d" % kv for kv in list(dc["by_reason"].items())[:10]) or "-"))
        if sm["funding_incomplete_closed_ids"] or sm["funding_pending_positions"]:
            L.append("  funding: incomplete closed %d · pending open %d" % (sm["funding_incomplete_closed_ids"], sm["funding_pending_positions"]))
    bt = rep.get("box_timer")
    if bt:
        L.append("BOX TIMER: generated %s alive %s evaluations %s missed_bars %s duplicates_blocked %s errors %s lag p50/max %s/%s s" % (
            bt["generated_at"] or "-", bt["alive"], bt["evaluations"], bt["missed_bars"], bt["duplicates_blocked"], bt["errors"],
            _f(bt["lag_p50_s"], ".1f"), _f(bt["lag_max_s"], ".1f")))
        if bt["last_error"]:
            L.append("  last_error: %s" % bt["last_error"])
    mg = rep.get("monitoring_gaps")
    if mg:
        L.append("MONITORING GAPS (%s): %d, total %.1f h" % (rep["book"], mg["n"], mg["total_hours"]))
        for g in mg["recent"]:
            L.append("  %s -> %s (%sh) positions %s" % (g["from"], g["to"], _f(g["hours"], ".1f"), ", ".join(g["positions"]) or "-"))
    L.append("")
    L.append("HIGHLIGHTS (facts, no recommendation)")
    for h in rep["highlights"]:
        L.append("  - " + h)
    if not rep["highlights"]:
        L.append("  (none)")
    return "\n".join(L)


# ----------------------------------------------------------------------------- CLI
def _configure_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description="Read-only forensics of the Box PAPER book (never writes).")
    ap.add_argument("--state", default=DEFAULT_STATE, help="state directory (default %s)" % DEFAULT_STATE)
    ap.add_argument("--book", default=DEFAULT_BOOK, help="book state dir under --state (default %s)" % DEFAULT_BOOK)
    ap.add_argument("--json", action="store_true", help="print one JSON document instead of text")
    ap.add_argument("--eod-grace-min", type=float, default=60.0,
                    help="minutes after the first UTC midnight a trade may stay open before it counts as STUCK_MULTI_DAY")
    ap.add_argument("--eod-close", choices=("auto", "on", "off"), default="auto",
                    help="auto = summary rule_params.eod_close (default on)")
    ap.add_argument("--since", default=None, help="only trades closed at/after this ISO time")
    ap.add_argument("--split-at", default=None, help="also aggregate trades opened before / at-or-after this ISO time")
    ap.add_argument("--now", default=None, help="reference time for open-position age (ISO; default: now)")
    a = ap.parse_args(argv)
    state = Path(a.state)
    if not state.is_dir():
        msg = "state directory not found: %s" % state
        print(json.dumps({"kind": "BOX_FORENSICS", "schema": SCHEMA, "error": msg}) if a.json else msg,
              file=sys.stdout if a.json else sys.stderr)
        return 2
    split_at = parse_ts(a.split_at) if a.split_at else None
    now = parse_ts(a.now) if a.now else None
    if (a.split_at and split_at is None) or (a.now and now is None):
        print("unreadable --split-at/--now time", file=sys.stderr)
        return 2
    rep = build_report(state, book=a.book, grace_min=float(a.eod_grace_min), eod_mode=a.eod_close, since=a.since,
                       split_at=split_at, now=now)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    else:
        print(render(rep))
    return 2 if rep.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
