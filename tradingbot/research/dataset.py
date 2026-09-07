"""Salt okunur araştırma veri kümesi (`research_dataset_v1`) — envanter, kapsam, mutabakat.

Bu modül canlı state'in BOUNDED bir kopyasını (export dizini) okur. Hiçbir dosyaya yazmaz.
Her kapanmış/açık işlem için:
* kanonik ekonomi (defterden birebir),
* değişmez giriş kapsaması (`entry_snapshot.jsonl` bağı),
* yol kapsaması (`position_path.jsonl`),
* sonuç/ders bütünlüğü (`trade_memory.jsonl`)
raporlanır ve kanıt sınıfı atanır. Eksik kanıt UYDURULMAZ.

Muhasebe ilkeleri:
* Tam kapanmış işlemlerin `net_pnl`'i ile AÇIK pozisyonların gerçekleşmiş kısmi kârı AYRI tutulur —
  çift sayım yoktur.
* Kayma (slippage) dolum fiyatının İÇİNDEDİR; ayrıca düşülmez, yalnız raporlanır.
* Mutabakat kanonik `entries` defteri üzerinden yapılır (FEE + FUNDING + PNL = cüzdan değişimi).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import CANONICAL_OBSERVED, MISSING_OR_UNMEASURABLE, POINT_IN_TIME

SCHEMA_VERSION = "research_dataset_v1"

#: Yol kapsaması durumları.
PATH_NONE = "NO_PATH"
PATH_PARTIAL = "PARTIAL_PATH"
PATH_FULL = "FULL_PATH"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def to_ms(iso_ts: Any) -> float | None:
    if not iso_ts:
        return None
    s = str(iso_ts).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).timestamp() * 1000.0
    except ValueError:
        return None


def read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def read_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return default


@dataclass
class ResearchDataset:
    """Export dizininden yüklenmiş salt okunur veri kümesi."""

    root: Path
    cutoff_utc: str | None = None
    app_head: str | None = None
    futures_ledger: dict[str, Any] = field(default_factory=dict)
    spot_ledger: dict[str, Any] = field(default_factory=dict)
    entry_rows: list[dict[str, Any]] = field(default_factory=list)
    entry_links: dict[str, str] = field(default_factory=dict)
    path_rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    memory_rows: list[dict[str, Any]] = field(default_factory=list)
    learning: dict[str, Any] = field(default_factory=dict)
    learn_v2: dict[str, Any] = field(default_factory=dict)
    entry_selectivity: dict[str, Any] = field(default_factory=dict)
    exit_eval: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path | str) -> "ResearchDataset":
        root = Path(root)
        st = root / "state"
        cutoff = app_head = None
        man = root / "MANIFEST.txt"
        if man.exists():
            for line in man.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("cutoff_utc="):
                    cutoff = line.split("=", 1)[1].strip()
                elif line.startswith("app_head="):
                    app_head = line.split("=", 1)[1].strip()
        entry_raw = read_jsonl(st / "entry_snapshot.jsonl")
        links = {str(r.get("candidate_id")): str(r.get("trade_id"))
                 for r in entry_raw if r.get("kind") == "link" and r.get("candidate_id")}
        decisions = [r for r in entry_raw if r.get("kind") != "link"]
        paths: dict[str, list[dict[str, Any]]] = {}
        for r in read_jsonl(st / "position_path.jsonl"):
            tid = str(r.get("trade_id") or "")
            if tid:
                paths.setdefault(tid, []).append(r)
        for rows in paths.values():
            rows.sort(key=lambda r: _f(r.get("ts_ms")) or 0.0)
        return cls(root=root, cutoff_utc=cutoff, app_head=app_head,
                   futures_ledger=read_json(st / "futures_ledger.json", {}) or {},
                   spot_ledger=read_json(st / "spot_ledger.json", {}) or {},
                   entry_rows=decisions, entry_links=links, path_rows=paths,
                   memory_rows=read_jsonl(st / "trade_memory.jsonl"),
                   learning=read_json(st / "learning.json", {}) or {},
                   learn_v2=read_json(st / "learn_v2.json", {}) or {},
                   entry_selectivity=read_json(st / "entry_selectivity.json", {}) or {},
                   exit_eval=read_json(st / "exit_eval.json", {}) or {})

    @property
    def closed_trades(self) -> list[dict[str, Any]]:
        return list(self.futures_ledger.get("history") or [])

    @property
    def open_positions(self) -> list[dict[str, Any]]:
        pos = self.futures_ledger.get("positions") or {}
        return list(pos.values()) if isinstance(pos, dict) else list(pos)

    def entry_snapshot_for(self, trade_id: str) -> dict[str, Any] | None:
        """Bir işlemin DEĞİŞMEZ giriş snapshot'ı — YALNIZ bağ kaydı üzerinden bulunur.

        Sembol/zaman yakınlığıyla tahmin YAPILMAZ; bağ yoksa kanıt yoktur.
        """
        cands = {c for c, t in self.entry_links.items() if t == str(trade_id)}
        if not cands:
            return None
        for row in self.entry_rows:
            if str(row.get("candidate_id")) in cands:
                return row
        return None

    def entry_plan_for(self, trade_id: str) -> dict[str, Any] | None:
        """Karar anında yazılmış plan (`trade_memory` `kind=entry`) — stop + hedefler.

        Bu, `entry_snapshot_v1`den AYRI ve daha eski bir kanıttır: tam ekonomik bağlamı
        taşımaz ama geometri (stop/hedef) POINT_IN_TIME'dır. Karıştırılmaz.
        """
        for r in self.memory_rows:
            if str(r.get("trade_id")) == str(trade_id) and r.get("kind") == "entry":
                return r
        return None


def path_coverage(rows: list[dict[str, Any]], *, opened_at_ms: float | None,
                  closed_at_ms: float | None) -> dict[str, Any]:
    """Yol kapsaması — SAHTE yol üretmez, eksik pencereyi açıkça raporlar."""
    if not rows:
        return {"state": PATH_NONE, "n": 0, "first_ms": None, "last_ms": None,
                "covered_fraction": 0.0, "tick_kinds": {},
                "note": "kayıt yok — bu işlem için gözlenmiş çıkış karşı-olgusu ÖLÇÜLEMEZ"}
    ts = [t for t in (_f(r.get("ts_ms")) for r in rows) if t is not None]
    first, last = (min(ts), max(ts)) if ts else (None, None)
    kinds: dict[str, int] = {}
    for r in rows:
        k = str(r.get("tick_kind") or "unknown")
        kinds[k] = kinds.get(k, 0) + 1
    frac = None
    state = PATH_PARTIAL
    if opened_at_ms is not None and closed_at_ms is not None and closed_at_ms > opened_at_ms:
        span = closed_at_ms - opened_at_ms
        covered = max(0.0, min(closed_at_ms, last or 0.0) - max(opened_at_ms, first or 0.0))
        frac = round(covered / span, 6)
        if first is not None and (first - opened_at_ms) <= 3_600_000 and frac >= 0.95:
            state = PATH_FULL
    return {"state": state, "n": len(rows), "first_ms": first, "last_ms": last,
            "covered_fraction": frac, "tick_kinds": kinds,
            "note": ("mark serisi — bar içi yüksek/düşük YOK; gözlemler arası hareket "
                     "görülmez, bu yüzden MFE ALT sınır, stop tetiği ise EKSİK sayılabilir")}


def trade_inventory(ds: ResearchDataset) -> dict[str, Any]:
    """Her işlem için ekonomi + kapsam + kanıt sınıfı. AÇIK ve KAPALI ayrı tutulur."""
    mem_by_trade: dict[str, list[dict[str, Any]]] = {}
    for r in ds.memory_rows:
        tid = str(r.get("trade_id") or r.get("id") or "")
        if tid:
            mem_by_trade.setdefault(tid, []).append(r)

    rows: list[dict[str, Any]] = []
    for h in ds.closed_trades:
        tid = str(h.get("id"))
        o_ms, c_ms = to_ms(h.get("opened_at")), to_ms(h.get("closed_at"))
        cov = path_coverage(ds.path_rows.get(tid, []), opened_at_ms=o_ms, closed_at_ms=c_ms)
        snap = ds.entry_snapshot_for(tid)
        feats = h.get("features") or {}
        plan_row = ds.entry_plan_for(tid)
        plan = (plan_row or {}).get("decision") or {}
        plan_targets = [t for t in (_f(x) for x in (plan.get("targets") or [])) if t is not None]
        plan_stop = _f(plan.get("stop"))
        rows.append({
            "trade_id": tid, "status": "CLOSED", "symbol": h.get("symbol"),
            "side": h.get("side"), "market_type": h.get("market_type"),
            "opened_at": h.get("opened_at"), "closed_at": h.get("closed_at"),
            "opened_at_ms": o_ms, "closed_at_ms": c_ms,
            "exit_reason": h.get("exit_reason"),
            "entry": _f(h.get("entry")), "exit_price": _f(h.get("exit_price")),
            "initial_stop": (plan_stop if plan_stop is not None
                             else _f(feats.get("initial_stop") or feats.get("stop"))),
            "targets": plan_targets,
            "plan_evidence": (POINT_IN_TIME if plan_row else MISSING_OR_UNMEASURABLE),
            "plan_source": ("trade_memory:entry" if plan_row else "futures_ledger:features"),
            "leverage": _f(h.get("leverage")), "quantity": _f(h.get("quantity")),
            "gross_pnl": _f(h.get("gross_pnl")), "net_pnl": _f(h.get("net_pnl")),
            "fees": _f(h.get("fees")), "funding": _f(h.get("funding")),
            "slippage_cost": _f(h.get("slippage_cost")),
            "r_multiple": _f(h.get("r_multiple")),
            "mfe_pct": _f(h.get("mfe_pct")), "mae_pct": _f(h.get("mae_pct")),
            "bars_held": _f(h.get("bars_held")), "tp1_done": bool(h.get("tp1_done")),
            "n_fills": len(h.get("fills") or []),
            "entry_snapshot": (POINT_IN_TIME if snap else MISSING_OR_UNMEASURABLE),
            "entry_snapshot_candidate_id": (snap or {}).get("candidate_id"),
            "entry_policy_version": (snap or {}).get("policy_version"),
            "path_coverage": cov,
            "outcome_memory_rows": len(mem_by_trade.get(tid, [])),
            "economics_evidence": CANONICAL_OBSERVED,
        })

    open_rows: list[dict[str, Any]] = []
    for p in ds.open_positions:
        tid = str(p.get("id"))
        o_ms = to_ms(p.get("opened_at"))
        cov = path_coverage(ds.path_rows.get(tid, []), opened_at_ms=o_ms, closed_at_ms=None)
        snap = ds.entry_snapshot_for(tid)
        plan_row = ds.entry_plan_for(tid)
        plan = (plan_row or {}).get("decision") or {}
        plan_targets = [t for t in (_f(x) for x in (plan.get("targets") or [])) if t is not None]
        entry, last = _f(p.get("entry_avg")), _f(p.get("last_price"))
        qty = _f(p.get("qty")) or 0.0
        sign = 1.0 if str(p.get("side")).upper() == "LONG" else -1.0
        unreal = (sign * ((last if last is not None else entry) - entry) * qty
                  if entry is not None else None)
        open_rows.append({
            "trade_id": tid, "status": "OPEN", "symbol": p.get("symbol"), "side": p.get("side"),
            "opened_at": p.get("opened_at"), "opened_at_ms": o_ms,
            "entry": entry, "last_price": last, "qty": qty,
            "initial_qty": _f(p.get("initial_qty")),
            "initial_stop": _f(p.get("initial_stop")), "stop": _f(p.get("stop")),
            "targets": ([t for t in (_f(x) for x in (p.get("targets") or [])) if t is not None]
                        or plan_targets),
            "plan_evidence": (POINT_IN_TIME if plan_row else MISSING_OR_UNMEASURABLE),
            "realized_pnl": _f(p.get("realized_pnl")), "unrealized_pnl": unreal,
            "tp1_done": bool(p.get("tp1_done")), "targets_hit": _f(p.get("targets_hit")),
            "mfe_pct": _f(p.get("mfe_pct")), "mae_pct": _f(p.get("mae_pct")),
            "leverage": _f(p.get("leverage")),
            "entry_snapshot": (POINT_IN_TIME if snap else MISSING_OR_UNMEASURABLE),
            "entry_snapshot_candidate_id": (snap or {}).get("candidate_id"),
            "path_coverage": cov,
            "economics_evidence": CANONICAL_OBSERVED,
        })

    n_closed = len(rows)
    with_snap = sum(1 for r in rows if r["entry_snapshot"] == POINT_IN_TIME)
    full_path = sum(1 for r in rows if r["path_coverage"]["state"] == PATH_FULL)
    part_path = sum(1 for r in rows if r["path_coverage"]["state"] == PATH_PARTIAL)
    return {
        "schema_version": SCHEMA_VERSION,
        "closed": rows, "open": open_rows,
        "summary": {
            "n_closed": n_closed, "n_open": len(open_rows),
            "closed_with_entry_snapshot": with_snap,
            "closed_without_entry_snapshot": n_closed - with_snap,
            "closed_with_entry_plan": sum(1 for r in rows
                                          if r["plan_evidence"] == POINT_IN_TIME),
            "closed_with_targets": sum(1 for r in rows if r["targets"]),
            "closed_full_path": full_path, "closed_partial_path": part_path,
            "closed_no_path": n_closed - full_path - part_path,
            "open_with_entry_snapshot": sum(1 for r in open_rows
                                            if r["entry_snapshot"] == POINT_IN_TIME),
            "open_full_path": sum(1 for r in open_rows
                                  if r["path_coverage"]["state"] == PATH_FULL),
            "n_memory_rows": len(ds.memory_rows),
            "n_entry_decisions": len(ds.entry_rows),
            "n_entry_links": len(ds.entry_links),
        },
    }


def reconcile_economics(ds: ResearchDataset) -> dict[str, Any]:
    """Kanonik defter mutabakatı — FEE + FUNDING + PNL = cüzdan değişimi (fail-closed)."""
    led = ds.futures_ledger
    start = _f(led.get("starting_equity"))
    wallet = _f(led.get("wallet_balance"))
    totals: dict[str, float] = {}
    for e in led.get("entries") or []:
        k = str(e.get("kind") or e.get("type") or "UNKNOWN")
        v = _f(e.get("amount"))
        if v is not None:
            totals[k] = totals.get(k, 0.0) + v
    entries_sum = sum(totals.values())
    wallet_delta = (wallet - start) if (wallet is not None and start is not None) else None
    residual = (wallet_delta - entries_sum) if wallet_delta is not None else None

    closed = ds.closed_trades
    closed_net = sum(_f(h.get("net_pnl")) or 0.0 for h in closed)
    closed_gross = sum(_f(h.get("gross_pnl")) or 0.0 for h in closed)
    closed_fees = sum(_f(h.get("fees")) or 0.0 for h in closed)
    closed_funding = sum(_f(h.get("funding")) or 0.0 for h in closed)
    closed_slip = sum(_f(h.get("slippage_cost")) or 0.0 for h in closed)
    open_realized = sum(_f(p.get("realized_pnl")) or 0.0 for p in ds.open_positions)
    open_unreal, open_measurable = 0.0, True
    for p in ds.open_positions:
        e, last, q = _f(p.get("entry_avg")), _f(p.get("last_price")), _f(p.get("qty"))
        if e is None or last is None or q is None:
            open_measurable = False
            continue
        open_unreal += (1.0 if str(p.get("side")).upper() == "LONG" else -1.0) * (last - e) * q

    spot = ds.spot_ledger
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "FUTURES_USDM_PERP (spot AYRI — market/risk kapsamları karıştırılmaz)",
        "futures": {
            "starting_equity": start, "wallet_balance": wallet,
            "wallet_delta_usdt": wallet_delta,
            "ledger_entry_totals": {k: round(v, 10) for k, v in sorted(totals.items())},
            "ledger_entry_sum": round(entries_sum, 10),
            "reconciliation_residual_usdt": (round(residual, 10) if residual is not None else None),
            "reconciled": bool(residual is not None and abs(residual) <= 1e-8),
            "closed_trades": {
                "n": len(closed), "net_pnl_usdt": round(closed_net, 10),
                "gross_pnl_usdt": round(closed_gross, 10),
                "fees_usdt": round(closed_fees, 10), "funding_usdt": round(closed_funding, 10),
                "slippage_usdt_in_fill_price": round(closed_slip, 10),
                "slippage_note": ("kayma dolum fiyatının İÇİNDE — net_pnl'den AYRICA "
                                  "düşülmez, yalnız görünürlük için raporlanır"),
                "partial_exit_note": ("TP1 kısmi çıkışları kapanmış işlemin net_pnl'ine ZATEN "
                                      "dahildir; ayrıca eklenmez"),
                "n_with_tp1": sum(1 for h in closed if h.get("tp1_done")),
            },
            "open_positions": {
                "n": len(ds.open_positions),
                "realized_partial_pnl_usdt": round(open_realized, 10),
                "unrealized_mtm_usdt": (round(open_unreal, 10) if open_measurable else None),
                "unrealized_measurable": open_measurable,
                "n_with_tp1": sum(1 for p in ds.open_positions if p.get("tp1_done")),
                "note": "gerçekleşmiş kısmi kâr ile açık MTM AYRI tutulur — çift sayım yok",
            },
            "identity_check": {
                "closed_net_plus_open_realized": round(closed_net + open_realized, 10),
                "wallet_delta": (round(wallet_delta, 10) if wallet_delta is not None else None),
                "unexplained_usdt": (round(wallet_delta - closed_net - open_realized, 10)
                                     if wallet_delta is not None else None),
                "unexplained_source": ("açık pozisyonların giriş ücreti + tahakkuk etmiş funding "
                                       "(pozisyon kapanmadığı için net_pnl'e girmemiştir)"),
            },
        },
        "spot": {"starting_equity": _f(spot.get("starting_equity")), "cash": _f(spot.get("cash")),
                 "assets": spot.get("assets"), "n_history": len(spot.get("history") or []),
                 "total_fees": _f(spot.get("total_fees")),
                 "note": "stopsuz spot holding — futures stop-risk kovasına GİRMEZ"},
    }


__all__ = ["PATH_FULL", "PATH_NONE", "PATH_PARTIAL", "ResearchDataset", "SCHEMA_VERSION",
           "path_coverage", "read_json", "read_jsonl", "reconcile_economics", "to_ms",
           "trade_inventory"]
