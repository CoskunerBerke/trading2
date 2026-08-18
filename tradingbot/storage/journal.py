"""Olay günlüğü (append-only) — `events` tablosu üzerinde idempotent ekleme ve tekrar oynatma."""
from __future__ import annotations

import json
from typing import Any, Callable, Iterator

from ..core import EventEnvelope
from .db import Database, dumps

_INSERT = ("INSERT OR IGNORE INTO events (event_id, event_type, run_id, created_at_utc, source, causation_id, correlation_id, "
           "schema_version, payload_hash, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)")
_SELECT = ("SELECT event_id, event_type, run_id, created_at_utc, source, causation_id, correlation_id, schema_version, "
           "payload_hash, payload_json FROM events")


def _to_env(row: dict[str, Any]) -> EventEnvelope:
    payload = json.loads(row["payload_json"]) if row.get("payload_json") else {}
    return EventEnvelope(event_id=row["event_id"], event_type=row["event_type"], run_id=row["run_id"],
                         created_at_utc=row["created_at_utc"], source=row["source"], payload=payload,
                         causation_id=row.get("causation_id"), correlation_id=row.get("correlation_id"),
                         schema_version=int(row.get("schema_version") or 1), payload_hash=row["payload_hash"])


class EventJournal:
    def __init__(self, db: Database):
        self.db = db

    def append(self, env: EventEnvelope) -> bool:
        """Zarfı ekler. Aynı `payload_hash` zaten varsa False döner (idempotent)."""
        with self.db.transaction():
            cur = self.db.execute(_INSERT, (env.event_id, env.event_type, env.run_id, env.created_at_utc, env.source,
                                            env.causation_id, env.correlation_id, env.schema_version, env.payload_hash,
                                            dumps(env.payload)))
            return cur.rowcount == 1

    def append_many(self, envs: list[EventEnvelope]) -> int:
        n = 0
        with self.db.transaction():
            for e in envs:
                n += int(self.append(e))
        return n

    def _rows(self, where: str = "", params: tuple = (), limit: int | None = None) -> list[EventEnvelope]:
        sql = _SELECT + (f" WHERE {where}" if where else "") + " ORDER BY created_at_utc, event_id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [_to_env(r) for r in self.db.query(sql, params)]

    def since(self, ts_utc: str, limit: int | None = None) -> list[EventEnvelope]:
        return self._rows("created_at_utc >= ?", (ts_utc,), limit)

    def by_type(self, event_type: str, limit: int | None = None) -> list[EventEnvelope]:
        return self._rows("event_type = ?", (event_type,), limit)

    def by_correlation(self, correlation_id: str) -> list[EventEnvelope]:
        return self._rows("correlation_id = ?", (correlation_id,))

    def by_run(self, run_id: str) -> list[EventEnvelope]:
        return self._rows("run_id = ?", (run_id,))

    def iter_all(self, batch: int = 500) -> Iterator[EventEnvelope]:
        last_key: tuple[str, str] | None = None
        while True:
            if last_key is None:
                rows = self._rows(limit=batch)
            else:
                rows = self._rows("(created_at_utc, event_id) > (?, ?)", last_key, batch)
            if not rows:
                return
            yield from rows
            last_key = (rows[-1].created_at_utc, rows[-1].event_id)

    def replay(self, handler: Callable[[EventEnvelope], Any], *, since: str | None = None,
               event_type: str | None = None) -> int:
        """Olayları zaman sırasıyla `handler`'a verir; işlenen sayısını döner."""
        n = 0
        for env in self.iter_all():
            if since and env.created_at_utc < since:
                continue
            if event_type and env.event_type != event_type:
                continue
            handler(env)
            n += 1
        return n

    def count(self) -> int:
        return self.db.count("events")


__all__ = ["EventJournal"]
