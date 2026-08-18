"""İnce, tipli depo katmanı: `upsert(table, row)` / `list(table, **filters)`.

* Bilinen sütunlar (schema.columns_of) doğrudan yazılır; bilinmeyen anahtarlar `payload_json` içine gider.
* Okurken `payload_json` açılıp satıra geri düzleştirilir (sütun adları önceliklidir) → yazdığını okursun.
* `upsert_<table>(row)` / `list_<table>(**filters)` kısayolları dinamik olarak sağlanır.
* FTS varsa `agent_evidence` ve `trade_outcomes` metinleri `evidence_fts`'e de yazılır.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from ..core import StorageError, iso, new_id
from .db import Database, dumps
from .schema import TABLE_SPECS, columns_of

_PREFIX = {"positions": "pos", "position_events": "pev", "orders": "ord", "fills": "fil", "trade_outcomes": "out",
           "learning_features": "lf", "model_versions": "mv", "model_metrics": "mm", "coin_head_decisions": "chd",
           "chief_decisions": "chf", "risk_decisions": "rsk", "trade_plans": "pln", "incidents": "inc",
           "system_health": "hlt", "llm_calls": "llm", "wallet_balances": "wb", "funding_payments": "fp",
           "fees": "fee", "counterfactual_trades": "cf", "runs": "run", "agent_runs": "ar", "agent_evidence": "ev",
           "market_snapshots": "ms", "spot_lots": "lot", "candles_index": "ci"}
_FTS_SOURCES = {"agent_evidence": ("text",), "trade_outcomes": ("exit_reason",)}


def _scalar(v: Any) -> Any:
    """Tipli sütuna yazılabilir değer: Decimal→str, bool→int, dict/list→json, datetime→iso."""
    if v is None or isinstance(v, (int, float, str, bytes)):
        return int(v) if isinstance(v, bool) else v
    if isinstance(v, Decimal):
        return format(v, "f")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (dict, list, tuple, set)):
        return dumps(v)
    return str(v)


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------ yazma
    def split(self, table: str, row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Satırı (sütunlar, payload) olarak ayırır; `payload`/`payload_json` anahtarları payload'a katılır."""
        cols = set(columns_of(table))
        colvals: dict[str, Any] = {}
        payload: dict[str, Any] = {}
        for k, v in row.items():
            if k == "payload" and isinstance(v, dict):
                payload.update(v)
            elif k == "payload_json":
                if isinstance(v, str):
                    try:
                        payload.update(json.loads(v))
                    except json.JSONDecodeError as exc:
                        raise StorageError(f"payload_json geçersiz JSON ({table})") from exc
                elif isinstance(v, dict):
                    payload.update(v)
            elif k in cols:
                colvals[k] = _scalar(v)
            else:
                payload[k] = v
        return colvals, payload

    def upsert(self, table: str, row: dict[str, Any], *, ignore_existing: bool = False) -> str:
        """Ekle/güncelle; id döner. `ignore_existing=True` → INSERT OR IGNORE (idempotent import)."""
        if table not in TABLE_SPECS:
            raise StorageError(f"repo bilinmeyen tablo: {table}")
        colvals, payload = self.split(table, row)
        colvals.setdefault("id", new_id(_PREFIX.get(table, table[:3])))
        colvals.setdefault("created_at_utc", iso())
        colvals.setdefault("schema_version", 1)
        colvals["payload_json"] = dumps(payload) if payload else None
        names = list(colvals)
        placeholders = ",".join("?" for _ in names)
        if ignore_existing:
            sql = f"INSERT OR IGNORE INTO {table} ({','.join(names)}) VALUES ({placeholders})"
        else:
            sets = ",".join(f"{n}=excluded.{n}" for n in names if n != "id")
            sql = f"INSERT INTO {table} ({','.join(names)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {sets}"
        with self.db.transaction():
            cur = self.db.execute(sql, tuple(colvals[n] for n in names))
            if cur.rowcount and table in _FTS_SOURCES and self.db.fts_available:
                text = " ".join(str(colvals.get(c) or "") for c in _FTS_SOURCES[table]).strip()
                if payload.get("text"):
                    text = f"{text} {payload['text']}".strip()
                if text:
                    self.db.execute("DELETE FROM evidence_fts WHERE ref_table=? AND ref_id=?", (table, colvals["id"]))
                    self.db.execute("INSERT INTO evidence_fts(content, ref_table, ref_id) VALUES (?,?,?)",
                                    (text, table, colvals["id"]))
        return colvals["id"]

    def upsert_many(self, table: str, rows: list[dict[str, Any]], *, ignore_existing: bool = False) -> list[str]:
        with self.db.transaction():
            return [self.upsert(table, r, ignore_existing=ignore_existing) for r in rows]

    # ------------------------------------------------------------ okuma
    @staticmethod
    def _flatten(row: dict[str, Any]) -> dict[str, Any]:
        raw = row.pop("payload_json", None)
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"_payload_raw": raw}
            for k, v in payload.items():
                row.setdefault(k, v)
        return row

    def get(self, table: str, id_: str) -> dict[str, Any] | None:
        r = self.db.query_one(f"SELECT * FROM {table} WHERE id=?", (id_,))
        return self._flatten(r) if r else None

    def list(self, table: str, *, limit: int | None = None, order_by: str = "created_at_utc DESC, id DESC",
             **filters: Any) -> list[dict[str, Any]]:
        """Eşitlik filtreleriyle listele (yalnızca tipli sütunlar filtrelenebilir)."""
        cols = set(columns_of(table))
        where, params = [], []
        for k, v in filters.items():
            if k not in cols:
                raise StorageError(f"{table}: filtrelenemeyen sütun {k}")
            if v is None:
                where.append(f"{k} IS NULL")
            else:
                where.append(f"{k}=?")
                params.append(_scalar(v))
        sql = f"SELECT * FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order_by}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._flatten(r) for r in self.db.query(sql, tuple(params))]

    def search_text(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """FTS5 arama (LLM retrieval). FTS yoksa LIKE ile agent_evidence.text'e düşer."""
        if self.db.fts_available:
            return self.db.query("SELECT ref_table, ref_id, content FROM evidence_fts WHERE evidence_fts MATCH ? LIMIT ?",
                                 (query, limit))
        rows = self.db.query("SELECT id AS ref_id, text AS content FROM agent_evidence WHERE text LIKE ? LIMIT ?",
                             (f"%{query}%", limit))
        for r in rows:
            r["ref_table"] = "agent_evidence"
        return rows

    # ------------------------------------------------------------ kolaylık sorguları
    def open_positions(self, market_type: str | None = None) -> list[dict[str, Any]]:
        f: dict[str, Any] = {"status": "OPEN"}
        if market_type:
            f["market_type"] = market_type
        return self.list("positions", order_by="opened_at_utc", **f)

    def last_outcomes(self, n: int = 20, symbol: str | None = None) -> list[dict[str, Any]]:
        f = {"symbol": symbol} if symbol else {}
        return self.list("trade_outcomes", limit=n, order_by="closed_at_utc DESC, id DESC", **f)

    def last_health(self, component: str | None = None) -> dict[str, Any] | None:
        f = {"component": component} if component else {}
        rows = self.list("system_health", limit=1, order_by="ts_utc DESC, id DESC", **f)
        return rows[0] if rows else None

    def position_events_for(self, position_id: str) -> list[dict[str, Any]]:
        return self.list("position_events", order_by="ts_utc, id", position_id=position_id)

    def latest_model(self, kind: str) -> dict[str, Any] | None:
        rows = self.list("model_versions", limit=1, kind=kind)
        return rows[0] if rows else None

    # ------------------------------------------------------------ dinamik kısayollar
    def __getattr__(self, name: str):
        if name.startswith("upsert_") and name[7:] in TABLE_SPECS:
            table = name[7:]
            return lambda row, **kw: self.upsert(table, row, **kw)
        if name.startswith("list_") and name[5:] in TABLE_SPECS:
            table = name[5:]
            return lambda **filters: self.list(table, **filters)
        raise AttributeError(name)


__all__ = ["Repository"]
