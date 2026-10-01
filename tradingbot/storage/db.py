"""SQLite bağlantı sarmalayıcısı — WAL, tek yazar kilidi, şema sürümleme, yedekleme, bütünlük kontrolü.

* Decimal → TEXT (kayıpsız), datetime → ISO metni (adapter kayıtlı).
* Süreç düzeyinde `threading.RLock` ile tek yazar; `check_same_thread=False`.
* `transaction()` iç içe kullanılabilir (yalnızca en dıştaki COMMIT/ROLLBACK yapar).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from ..core import StorageError, iso, utc_now
from .schema import FTS_DDL, SCHEMA_VERSION, ddl_statements

log = logging.getLogger(__name__)

sqlite3.register_adapter(Decimal, lambda d: format(d, "f"))
sqlite3.register_adapter(datetime, lambda dt: iso(dt))


def json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return format(o, "f")
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


def dumps(obj: Any) -> str:
    """Decimal/datetime güvenli, deterministik JSON."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=json_default, separators=(",", ":"))


# ileride artımlı şema değişiklikleri: (version, açıklama, [sql...]) — version > mevcut ise uygulanır
MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (1, "initial schema", []),
]


class Database:
    """Tek dosyalı SQLite deposu. `Database(path)`; `with Database(path) as db:` de çalışır."""

    def __init__(self, path: Path | str, *, timeout_ms: int = 5000):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self.fts_available = False
        try:
            self._conn = sqlite3.connect(str(self.path), timeout=timeout_ms / 1000.0, check_same_thread=False,
                                         isolation_level=None)  # autocommit; işlemleri kendimiz yönetiriz
        except sqlite3.Error as exc:
            raise StorageError(f"sqlite açılamadı: {self.path}: {exc}") from exc
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(f"PRAGMA busy_timeout={int(timeout_ms)}")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._apply_schema()

    # ------------------------------------------------------------ şema
    def _apply_schema(self) -> None:
        with self.transaction():
            for sql in ddl_statements():
                self._conn.execute(sql)
            try:
                self._conn.execute(FTS_DDL)
                self.fts_available = True
            except sqlite3.OperationalError as exc:   # FTS5 derlenmemiş olabilir
                log.warning("FTS5 yok, evidence_fts atlandı: %s", exc)
                self.fts_available = False
            current = self.schema_version()
            for version, desc, stmts in MIGRATIONS:
                if version <= current:
                    continue
                for sql in stmts:
                    self._conn.execute(sql)
                self._conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at_utc, description) VALUES (?,?,?)",
                                   (version, iso(utc_now()), desc))
            if self.schema_version() < SCHEMA_VERSION:
                self._conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at_utc, description) VALUES (?,?,?)",
                                   (SCHEMA_VERSION, iso(utc_now()), "schema version stamp"))

    def schema_version(self) -> int:
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0) if row else 0

    def journal_mode(self) -> str:
        return str(self._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def tables(self) -> list[str]:
        return [r["name"] for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    # ------------------------------------------------------------ işlem
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Tek yazar kilidi + BEGIN IMMEDIATE / COMMIT / ROLLBACK (iç içe güvenli)."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError as exc:      # kilitli/bozuk DB → alan hatası (kill switch DB_WRITE_FAILURE tetikler)
                    raise StorageError(f"veritabanı yazıma açılamadı: {exc}") from exc
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple | list | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                return self._conn.execute(sql, params)
            except sqlite3.Error as exc:
                raise StorageError(f"sql hatası: {exc} :: {sql[:120]}") from exc

    def executemany(self, sql: str, seq: list[tuple] | list[dict]) -> sqlite3.Cursor:
        with self._lock:
            try:
                return self._conn.executemany(sql, seq)
            except sqlite3.Error as exc:
                raise StorageError(f"sql hatası: {exc} :: {sql[:120]}") from exc

    def query(self, sql: str, params: tuple | list | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            try:
                cur = self._conn.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
            except sqlite3.Error as exc:
                raise StorageError(f"sql sorgu hatası: {exc} :: {sql[:120]}") from exc

    def query_one(self, sql: str, params: tuple | list | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def count(self, table: str) -> int:
        return int(self.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"])  # type: ignore[index]

    # ------------------------------------------------------------ bakım
    def backup(self, dest_path: Path | str) -> Path:
        """Çevrimiçi yedek (`sqlite3.Connection.backup`). WAL içeriği de dahil edilir."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                target = sqlite3.connect(str(dest))
                try:
                    self._conn.backup(target)
                finally:
                    target.close()
            except sqlite3.Error as exc:
                raise StorageError(f"yedekleme başarısız: {dest}: {exc}") from exc
        return dest

    def integrity_check(self) -> bool:
        with self._lock:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and str(row[0]).lower() == "ok"

    def checkpoint(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def raw(self) -> sqlite3.Connection:
        return self._conn


__all__ = ["Database", "dumps", "json_default", "MIGRATIONS"]
