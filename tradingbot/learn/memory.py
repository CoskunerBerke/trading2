"""Değişmez işlem hafızası (LAYER 1) — yalnız ekleme; asla kesilmez. SQLite varsa `learning_features`/`trade_outcomes`
tablolarına da yazar (duck typing: `repo.upsert(table, row)`), yoksa/ek olarak `state/trade_memory.jsonl`."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

from ..core import iso, new_id, payload_hash, utc_now


def _append_line(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass


SOURCES = ("LIVE_PAPER", "HISTORICAL_REPLAY", "SHADOW", "TESTNET", "LIVE")


class TradeMemory:
    """Değişmez işlem hafızası (JSONL). Her kayıtta zorunlu `source` namespace: LIVE_PAPER | HISTORICAL_REPLAY | SHADOW | TESTNET | LIVE.
    Replay kayıtları gerçek PAPER hafızasıyla KARIŞMAZ: farklı `source` (ve replay için ayrı dosya yolu)."""

    def __init__(self, path: Path | str | None = None, repo: Any | None = None, *, source: str = "LIVE_PAPER"):
        self.path = Path(path) if path else None
        self.repo = repo
        if source not in SOURCES:
            raise ValueError(f"bilinmeyen memory source: {source}")
        self.source = source

    # ------------------------------------------------------------ yazım
    def record_entry(self, snapshot: dict[str, Any]) -> str:
        """Giriş anı: bütün ajan raporları, coin head kararı, dissent, veto, risk kararı, plan, model/prompt sürümleri, veri tazeliği."""
        tid = str(snapshot.get("trade_id") or snapshot.get("id") or new_id("trade"))
        src = str(snapshot.get("source") or self.source)
        if src not in SOURCES:
            raise ValueError(f"bilinmeyen memory source: {src}")
        row = {"kind": "entry", "trade_id": tid, "recorded_at": iso(utc_now()), "hash": payload_hash(snapshot), "source": src, **{k: v for k, v in snapshot.items() if k != "source"}}
        if self.path:
            _append_line(self.path, row)
        if self.repo is not None:
            try:
                self.repo.upsert("learning_features", {"id": f"lf_{tid}", "position_id": tid, "symbol": snapshot.get("symbol"),
                                                       "ts_utc": row["recorded_at"], "feature_set_version": str(snapshot.get("feature_version", 2)),
                                                       "label": None, "snapshot": snapshot}, ignore_existing=True)
            except Exception:  # noqa: BLE001 — DB opsiyonel; JSONL birincil kayıt
                pass
        return tid

    def record_exit(self, trade_id: str, outcome: dict[str, Any], price_path: list[dict] | None = None, postmortem: dict | None = None) -> None:
        row = {"kind": "exit", "trade_id": trade_id, "recorded_at": iso(utc_now()), "source": self.source, "outcome": outcome,
               "price_path": price_path or [], "postmortem": postmortem or {}}
        if self.path:
            _append_line(self.path, row)
        if self.repo is not None:
            try:
                self.repo.upsert("trade_outcomes", {"id": f"to_{trade_id}", "position_id": trade_id, "symbol": outcome.get("symbol"),
                                                    "market_type": outcome.get("market_type"), "side": outcome.get("side"),
                                                    "exit_reason": outcome.get("exit_reason"), "closed_at_utc": outcome.get("closed_at"),
                                                    "pnl": outcome.get("net_pnl", outcome.get("pnl")), "r_multiple": outcome.get("r_multiple"),
                                                    "won": 1 if float(outcome.get("r_multiple", 0) or 0) > 0 else 0, "outcome": outcome,
                                                    "postmortem": postmortem or {}}, ignore_existing=True)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ okuma
    def iter_rows(self, *, source: str | None = "auto") -> Iterator[dict]:
        """source="auto" → yalnız bu hafızanın namespace'i (eski kayıtlar source'suz → LIVE_PAPER sayılır); None → hepsi."""
        want = self.source if source == "auto" else source
        for row in self._iter_all_rows():
            if want is None or str(row.get("source") or "LIVE_PAPER") == want:
                yield row

    def _iter_all_rows(self) -> Iterator[dict]:
        if not self.path or not self.path.exists():
            return iter(())
        def gen():
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        return gen()

    def get(self, trade_id: str) -> dict | None:
        out: dict | None = None
        for r in self.iter_rows():
            if r.get("trade_id") != trade_id:
                continue
            if r.get("kind") == "entry":
                out = dict(r)
            elif r.get("kind") == "exit" and out is not None:
                out["outcome"], out["postmortem"], out["price_path"] = r.get("outcome"), r.get("postmortem"), r.get("price_path")
        return out

    def trades(self, *, closed_only: bool = False, limit: int | None = None) -> list[dict]:
        merged: dict[str, dict] = {}
        for r in self.iter_rows():
            tid = r.get("trade_id")
            if r.get("kind") == "entry":
                merged[tid] = dict(r)
            elif r.get("kind") == "exit" and tid in merged:
                merged[tid]["outcome"], merged[tid]["postmortem"] = r.get("outcome"), r.get("postmortem")
        rows = [v for v in merged.values() if (v.get("outcome") if closed_only else True)]
        rows.sort(key=lambda x: x.get("recorded_at", ""))
        return rows[-limit:] if limit else rows

    def query(self, *, symbol: str | None = None, direction: str | None = None, setup: str | None = None, regime: str | None = None,
              since: str | None = None, limit: int = 50) -> list[dict]:
        out = []
        for t in self.trades(closed_only=True):
            if symbol and t.get("symbol") != symbol:
                continue
            if direction and t.get("direction") != direction:
                continue
            if setup and t.get("setup_type") != setup:
                continue
            if regime and t.get("regime") != regime:
                continue
            if since and t.get("recorded_at", "") < since:
                continue
            out.append(t)
        return out[-limit:]

    def count(self) -> int:
        return len(self.trades())
