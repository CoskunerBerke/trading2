"""Eski `state/*.json` dosyalarını SQLite'a idempotent aktarır (dosyalar ASLA silinmez/değiştirilmez).

Kimlikler `stable_id("legacy", <kaynak>, ...)` ile deterministik; yazım `INSERT OR IGNORE` → tekrar çalıştırmak
0 yeni satır üretir. Bozuk/eksik dosyalar raporda `skipped` olarak listelenir, diğerleri aktarılmaya devam eder.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import iso, stable_id
from .db import Database
from .repo import Repository

log = logging.getLogger(__name__)

RUN_ID = "legacy_import"
TABLES = ("runs", "positions", "position_events", "spot_lots", "trade_outcomes", "learning_features", "wallet_balances",
          "model_versions", "model_metrics", "coin_head_decisions", "chief_decisions", "agent_runs", "agent_evidence",
          "risk_decisions", "market_snapshots")


@dataclass
class MigrationReport:
    imported: dict[str, int] = field(default_factory=dict)     # tablo → yeni satır
    files: dict[str, str] = field(default_factory=dict)        # dosya → ok | missing | corrupt | error
    skipped: list[str] = field(default_factory=list)           # "dosya: neden"

    @property
    def total(self) -> int:
        return sum(self.imported.values())

    def to_dict(self) -> dict[str, Any]:
        return {"imported": dict(self.imported), "total": self.total, "files": dict(self.files), "skipped": list(self.skipped)}


def _sid(*parts: Any) -> str:
    return stable_id("legacy", *parts)


def _load(path: Path, report: MigrationReport) -> Any | None:
    """JSON oku; yoksa/bozuksa None (rapora işlenir). Dosyaya dokunulmaz."""
    if not path.exists():
        report.files[path.name] = "missing"
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        report.files[path.name] = "corrupt"
        report.skipped.append(f"{path.name}: {exc}")
        return None
    report.files[path.name] = "ok"
    return obj


def _put(repo: Repository, table: str, row: dict[str, Any]) -> None:
    row.setdefault("run_id", RUN_ID)
    row.setdefault("schema_version", 1)
    repo.upsert(table, row, ignore_existing=True)


# ------------------------------------------------------------------ kaynaklar
def _portfolio(repo: Repository, d: dict) -> None:
    for sym, p in (d.get("positions") or {}).items():
        pid = _sid("portfolio_pos", sym, p.get("entry_time"))
        _put(repo, "positions", {"id": pid, "symbol": sym, "market_type": "SPOT", "side": "LONG", "status": "OPEN",
                                 "entry_price": p.get("entry_price"), "qty": p.get("units"), "leverage": 1,
                                 "opened_at_utc": p.get("entry_time"), "created_at_utc": p.get("entry_time"),
                                 "source": "legacy_portfolio", "stop": p.get("stop"), "strategy": p.get("strategy")})
        _put(repo, "spot_lots", {"id": _sid("portfolio_lot", sym, p.get("entry_time")), "position_id": pid, "symbol": sym,
                                 "status": "OPEN", "qty": p.get("units"), "cost_basis": p.get("entry_price"),
                                 "acquired_at_utc": p.get("entry_time"), "created_at_utc": p.get("entry_time")})
    for h in d.get("history") or []:
        sym, et, xt = h.get("symbol"), h.get("entry_time"), h.get("exit_time")
        pid = _sid("portfolio_hist", sym, et, xt)
        _put(repo, "positions", {"id": pid, "symbol": sym, "market_type": "SPOT", "side": "LONG", "status": "CLOSED",
                                 "entry_price": h.get("entry_price"), "qty": h.get("units"), "leverage": 1,
                                 "opened_at_utc": et, "closed_at_utc": xt, "created_at_utc": et, "realized_pnl": h.get("pnl"),
                                 "source": "legacy_portfolio", "exit_price": h.get("exit_price"), "pnl_pct": h.get("pnl_pct"),
                                 "reason": h.get("reason"), "strategy": h.get("strategy")})
        _put(repo, "spot_lots", {"id": _sid("portfolio_lot", sym, et, xt), "position_id": pid, "symbol": sym, "status": "DISPOSED",
                                 "qty": h.get("units"), "cost_basis": h.get("entry_price"), "acquired_at_utc": et,
                                 "disposed_at_utc": xt, "created_at_utc": et, "exit_price": h.get("exit_price")})
        pnl = float(h.get("pnl") or 0.0)
        _put(repo, "trade_outcomes", {"id": _sid("portfolio_outcome", sym, et, xt), "position_id": pid, "symbol": sym,
                                      "market_type": "SPOT", "side": "LONG", "exit_reason": h.get("reason"), "closed_at_utc": xt,
                                      "created_at_utc": xt, "pnl": h.get("pnl"), "won": int(pnl > 0), "source": "legacy_portfolio",
                                      "pnl_pct": h.get("pnl_pct"), "strategy": h.get("strategy")})
    ts = d.get("updated_at") or iso()
    _put(repo, "wallet_balances", {"id": _sid("portfolio_wallet", ts), "account": "paper_spot", "asset": "USDT", "ts_utc": ts,
                                   "created_at_utc": ts, "free": d.get("cash"), "total": d.get("cash"),
                                   "starting_equity": d.get("starting_equity")})


def _futures_position(repo: Repository, p: dict, *, closed: bool) -> None:
    lid = p.get("id") or _sid("futures_anon", p.get("symbol"), p.get("opened_at"))
    pid = _sid("futures_pos", lid)
    sym = p.get("symbol")
    payload = {k: v for k, v in p.items() if k not in ("id", "symbol", "side", "entry", "units", "leverage", "opened_at", "features")}
    _put(repo, "positions", {"id": pid, "symbol": sym, "market_type": "FUTURES", "side": p.get("side"),
                             "status": "CLOSED" if closed else "OPEN", "entry_price": p.get("entry"), "qty": p.get("units"),
                             "leverage": p.get("leverage"), "opened_at_utc": p.get("opened_at"), "created_at_utc": p.get("opened_at"),
                             "closed_at_utc": p.get("closed_at") if closed else None,
                             "realized_pnl": p.get("pnl") if closed else p.get("realized"), "source": "legacy_futures",
                             "legacy_id": lid, **payload})
    _put(repo, "position_events", {"id": _sid("futures_pev", lid, "open"), "position_id": pid, "symbol": sym, "event_type": "OPEN",
                                   "ts_utc": p.get("opened_at"), "created_at_utc": p.get("opened_at"), "price": p.get("entry"),
                                   "qty": p.get("units"), "setup_type": p.get("setup_type"), "trigger_text": p.get("trigger_text")})
    feats = p.get("features") or {}
    if feats:
        _put(repo, "learning_features", {"id": _sid("futures_lf", lid), "position_id": pid, "symbol": sym,
                                         "ts_utc": p.get("opened_at"), "created_at_utc": p.get("opened_at"),
                                         "feature_set_version": "legacy_v1", "features": feats,
                                         "label": (int(float(p.get("pnl") or 0) > 0) if closed else None)})
    if closed:
        pnl = float(p.get("pnl") or 0.0)
        _put(repo, "position_events", {"id": _sid("futures_pev", lid, "close"), "position_id": pid, "symbol": sym,
                                       "event_type": "CLOSE", "ts_utc": p.get("closed_at"), "created_at_utc": p.get("closed_at"),
                                       "reason": p.get("exit_reason"), "pnl": p.get("pnl")})
        _put(repo, "trade_outcomes", {"id": _sid("futures_outcome", lid), "position_id": pid, "symbol": sym, "market_type": "FUTURES",
                                      "side": p.get("side"), "exit_reason": p.get("exit_reason"), "closed_at_utc": p.get("closed_at"),
                                      "created_at_utc": p.get("closed_at"), "pnl": p.get("pnl"), "r_multiple": p.get("r_multiple"),
                                      "won": int(pnl > 0), "source": "legacy_futures", "legacy_id": lid,
                                      **{k: p.get(k) for k in ("fees", "funding", "mae_pct", "mfe_pct", "bars_held", "leverage",
                                                                "setup_type", "trigger_text", "tp1_done", "opened_at", "entry")}})


def _futures(repo: Repository, d: dict) -> None:
    for p in (d.get("positions") or {}).values():
        _futures_position(repo, p, closed=False)
    for h in d.get("history") or []:
        _futures_position(repo, h, closed=True)
    ts = d.get("updated_at") or iso()
    _put(repo, "wallet_balances", {"id": _sid("futures_wallet", ts), "account": "paper_futures", "asset": "USDT", "ts_utc": ts,
                                   "created_at_utc": ts, "total": d.get("equity"), "starting_equity": d.get("starting_equity"),
                                   "total_fees": d.get("total_fees"), "seq": d.get("seq")})


def _learning(repo: Repository, d: dict) -> None:
    ts = d.get("updated_at") or "0"
    mid = _sid("learning_model", ts)
    _put(repo, "model_versions", {"id": mid, "kind": "legacy_lr", "version": ts, "status": "legacy", "created_at_utc": ts if ts != "0" else None,
                                  "n_train": int(d.get("n_trades") or 0),
                                  **{k: d.get(k) for k in ("weights", "bias", "agent_weights", "agent_hits", "setup_stats", "symbol_stats",
                                                            "exit_stats", "blacklist", "lessons", "lr", "l2", "n_wins", "sum_r")}})
    n = int(d.get("n_trades") or 0)
    metrics = {"n_trades": n, "win_rate": (100.0 * float(d.get("n_wins") or 0) / n) if n else None,
               "expectancy_r": (float(d.get("sum_r") or 0) / n) if n else None}
    for a, ht in (d.get("agent_hits") or {}).items():
        try:
            h, t = ht
            metrics[f"hit_rate_{a}"] = (100.0 * h / t) if t else None
        except (TypeError, ValueError):
            continue
    for m, v in metrics.items():
        _put(repo, "model_metrics", {"id": _sid("learning_metric", ts, m), "model_version_id": mid, "metric": m,
                                     "value": v, "ts_utc": ts, "created_at_utc": ts if ts != "0" else None, "window": "all"})


def _signals(repo: Repository, d: dict) -> None:
    rt = d.get("run_time")
    _put(repo, "runs", {"id": _sid("signals_run", rt), "kind": "spot_wfo", "status": "done", "source": "legacy_signals",
                        "started_at_utc": rt, "finished_at_utc": rt, "created_at_utc": rt,
                        **{k: d.get(k) for k in ("exchange", "timeframe", "summary", "executed", "portfolio")}})
    analyses = {a.get("symbol"): a for a in d.get("analyses") or []}
    for dec in d.get("decisions") or []:
        sym = dec.get("symbol")
        _put(repo, "coin_head_decisions", {"id": _sid("signals_decision", rt, sym), "symbol": sym, "ts_utc": rt, "created_at_utc": rt,
                                           "verdict": dec.get("action"), "conviction": dec.get("confidence"), "source": "spot_wfo",
                                           "plan_valid": int(dec.get("action") in ("BUY", "SELL")), "run_ref": _sid("signals_run", rt),
                                           "decision": dec, "analysis": analyses.get(sym)})
    summ = d.get("summary") or {}
    _put(repo, "chief_decisions", {"id": _sid("signals_chief", rt), "ts_utc": rt, "created_at_utc": rt, "source": "spot_wfo",
                                   "risk_mode": summ.get("market_regime"), "summary": summ})


def _signals_log(repo: Repository, path: Path, report: MigrationReport) -> None:
    if not path.exists():
        report.files[path.name] = "missing"
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        report.files[path.name] = "corrupt"
        report.skipped.append(f"{path.name}: {exc}")
        return
    bad = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            report.skipped.append(f"{path.name}: satır {i + 1} bozuk")
            continue
        rt = d.get("run_time")
        _put(repo, "runs", {"id": _sid("signals_run", rt), "kind": "spot_wfo", "status": "done", "source": "legacy_signals_log",
                            "started_at_utc": rt, "finished_at_utc": rt, "created_at_utc": rt,
                            **{k: d.get(k) for k in ("summary", "decisions", "executed", "equity")}})
    report.files[path.name] = "ok" if not bad else "partial"


def _agents(repo: Repository, d: dict) -> None:
    gen = d.get("generated_at")
    for b in d.get("briefs") or []:
        sym, bts = b.get("symbol"), b.get("generated_at") or gen
        for r in b.get("reports") or []:
            agent = r.get("agent")
            arid = _sid("agent_run", gen, sym, agent)
            _put(repo, "agent_runs", {"id": arid, "symbol": sym, "agent": agent, "ts_utc": bts, "created_at_utc": bts,
                                      "bias": r.get("bias"), "confidence": r.get("confidence"), "report": r})
            text = "\n".join([str(r.get("summary") or "")] + [str(x) for x in (r.get("findings") or [])]
                             + [f"UYARI: {w}" for w in (r.get("warnings") or [])]).strip()
            if text:
                _put(repo, "agent_evidence", {"id": _sid("agent_evidence", gen, sym, agent), "symbol": sym, "agent": agent,
                                              "agent_run_id": arid, "kind": "report", "text": text, "created_at_utc": bts})
        plan = b.get("plan") or {}
        _put(repo, "coin_head_decisions", {"id": _sid("agents_decision", gen, sym), "symbol": sym, "ts_utc": bts, "created_at_utc": bts,
                                           "verdict": b.get("verdict"), "conviction": b.get("conviction"), "source": "legacy_agents",
                                           "plan_valid": int(bool(plan.get("valid"))),
                                           **{k: v for k, v in b.items() if k not in ("reports", "symbol", "verdict", "conviction")}})
    chief = d.get("chief") or {}
    _put(repo, "chief_decisions", {"id": _sid("agents_chief", gen), "ts_utc": chief.get("generated_at") or gen, "created_at_utc": gen,
                                   "risk_mode": chief.get("risk_mode"), "btc_verdict": chief.get("btc_verdict"), "source": "legacy_agents",
                                   **{k: v for k, v in chief.items() if k not in ("risk_mode", "btc_verdict")}})


def _triggers(repo: Repository, d: dict) -> None:
    for sym, bar in d.items():
        _put(repo, "risk_decisions", {"id": _sid("trigger", sym, bar), "symbol": sym, "ts_utc": str(bar), "gate": "bar_seen",
                                      "allowed": 1, "reason": "legacy trigger dedupe (4h bar seen)", "bar_time": bar})


def _scan(repo: Repository, d: dict) -> None:
    gen = d.get("generated_at")
    _put(repo, "runs", {"id": _sid("scan_run", gen), "kind": "scan", "status": "done", "source": "legacy_scan", "started_at_utc": gen,
                        "finished_at_utc": gen, "created_at_utc": gen,
                        **{k: d.get(k) for k in ("universe", "scanned", "flagged", "min_volume", "seconds")}})
    for row in d.get("rows") or d.get("setups") or []:
        sym = row.get("symbol")
        _put(repo, "market_snapshots", {"id": _sid("scan", gen, sym), "symbol": sym, "ts_utc": gen, "created_at_utc": gen,
                                        "source": "legacy_scan", "price": row.get("price"), **{k: v for k, v in row.items() if k != "symbol"}})


# ------------------------------------------------------------------ giriş
def migrate_state_dir(state_dir: Path | str, db: Database) -> MigrationReport:
    """Bütün eski state dosyalarını aktar. Tekrar çalıştırmak güvenlidir (0 yeni satır)."""
    state_dir = Path(state_dir)
    repo = Repository(db)
    report = MigrationReport()
    before = {t: db.count(t) for t in TABLES}
    steps = [("portfolio.json", _portfolio), ("futures_ledger.json", _futures), ("learning.json", _learning),
             ("signals.json", _signals), ("agents.json", _agents), ("triggers.json", _triggers), ("scan.json", _scan)]
    for name, fn in steps:
        obj = _load(state_dir / name, report)
        if obj is None:
            continue
        if not isinstance(obj, dict):
            report.files[name] = "corrupt"
            report.skipped.append(f"{name}: beklenmeyen JSON tipi {type(obj).__name__}")
            continue
        try:
            with db.transaction():
                fn(repo, obj)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:   # şema uyumsuzluğu → bu dosyayı atla
            report.files[name] = "error"
            report.skipped.append(f"{name}: {type(exc).__name__}: {exc}")
            log.warning("legacy import %s atlandı: %s", name, exc)
    with db.transaction():
        _signals_log(repo, state_dir / "signals_log.jsonl", report)
    after = {t: db.count(t) for t in TABLES}
    report.imported = {t: after[t] - before[t] for t in TABLES if after[t] - before[t]}
    log.info("legacy migration: %d yeni satır, dosyalar=%s, atlanan=%d", report.total, report.files, len(report.skipped))
    return report


__all__ = ["migrate_state_dir", "MigrationReport", "RUN_ID"]
