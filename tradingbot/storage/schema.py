"""SQLite şeması — idempotent DDL (`CREATE TABLE IF NOT EXISTS`).

Tasarım: her tabloda `id TEXT PRIMARY KEY`, `run_id`, `created_at_utc`, `schema_version`, sorgu için birkaç
tipli sütun (symbol, status, ts_utc ...) ve geri kalan her şey `payload_json` (esnek, `schema_version` ile
sürümlenir). Para/miktar sütunları TEXT'tir (Decimal kayıpsız saklanır).
"""
from __future__ import annotations

SCHEMA_VERSION = 1

# (ad, tip) — ortak sütunlar; her standart tabloda önce bunlar gelir
COMMON: tuple[tuple[str, str], ...] = (
    ("id", "TEXT PRIMARY KEY"),
    ("run_id", "TEXT"),
    ("created_at_utc", "TEXT"),
    ("schema_version", "INTEGER NOT NULL DEFAULT 1"),
)
PAYLOAD: tuple[tuple[str, str], ...] = (("payload_json", "TEXT"),)

_T = "TEXT"
_I = "INTEGER"

# tablo → ek tipli sütunlar (ortak sütunlar otomatik eklenir)
TABLE_SPECS: dict[str, tuple[tuple[str, str], ...]] = {
    "runs": (("kind", _T), ("status", _T), ("source", _T), ("mode", _T), ("started_at_utc", _T), ("finished_at_utc", _T)),
    "market_snapshots": (("symbol", _T), ("ts_utc", _T), ("source", _T), ("price", _T)),
    "candles_index": (("exchange", _T), ("symbol", _T), ("timeframe", _T), ("path", _T), ("first_ts_ms", _I), ("last_ts_ms", _I), ("rows", _I), ("updated_at_utc", _T)),
    "orderbook_snapshots": (("symbol", _T), ("ts_utc", _T), ("best_bid", _T), ("best_ask", _T), ("spread_bps", _T)),
    "funding_events": (("symbol", _T), ("ts_utc", _T), ("rate", _T), ("mark_price", _T)),
    "open_interest_snapshots": (("symbol", _T), ("ts_utc", _T), ("oi", _T), ("oi_usdt", _T)),
    "agent_runs": (("symbol", _T), ("agent", _T), ("ts_utc", _T), ("verdict", _T), ("bias", _T), ("confidence", _T)),
    "agent_evidence": (("symbol", _T), ("agent", _T), ("agent_run_id", _T), ("kind", _T), ("text", _T)),
    "coin_head_decisions": (("symbol", _T), ("ts_utc", _T), ("verdict", _T), ("conviction", _T), ("source", _T), ("plan_valid", _I), ("model_version", _T)),
    "chief_decisions": (("ts_utc", _T), ("risk_mode", _T), ("btc_verdict", _T), ("source", _T)),
    "risk_decisions": (("symbol", _T), ("ts_utc", _T), ("gate", _T), ("allowed", _I), ("reason", _T)),
    "trade_plans": (("symbol", _T), ("market_type", _T), ("side", _T), ("status", _T), ("entry", _T), ("stop", _T), ("coin_head_decision_id", _T)),
    "orders": (("symbol", _T), ("market_type", _T), ("side", _T), ("order_type", _T), ("status", _T), ("client_order_id", _T), ("exchange_order_id", _T), ("qty", _T), ("price", _T), ("position_id", _T), ("ts_utc", _T)),
    "fills": (("order_id", _T), ("position_id", _T), ("symbol", _T), ("market_type", _T), ("side", _T), ("qty", _T), ("price", _T), ("fee", _T), ("fee_asset", _T), ("ts_utc", _T)),
    "positions": (("symbol", _T), ("market_type", _T), ("side", _T), ("status", _T), ("entry_price", _T), ("qty", _T), ("leverage", _T), ("opened_at_utc", _T), ("closed_at_utc", _T), ("realized_pnl", _T), ("source", _T)),
    "position_events": (("position_id", _T), ("symbol", _T), ("event_type", _T), ("ts_utc", _T), ("price", _T), ("qty", _T)),
    "spot_lots": (("position_id", _T), ("symbol", _T), ("status", _T), ("qty", _T), ("cost_basis", _T), ("acquired_at_utc", _T), ("disposed_at_utc", _T)),
    "wallet_balances": (("account", _T), ("asset", _T), ("ts_utc", _T), ("free", _T), ("locked", _T), ("total", _T)),
    "funding_payments": (("position_id", _T), ("symbol", _T), ("ts_utc", _T), ("amount", _T), ("rate", _T)),
    "fees": (("position_id", _T), ("order_id", _T), ("symbol", _T), ("ts_utc", _T), ("amount", _T), ("asset", _T), ("kind", _T)),
    "tax_estimates": (("period", _T), ("symbol", _T), ("ts_utc", _T), ("amount", _T), ("currency", _T)),
    "trade_outcomes": (("position_id", _T), ("symbol", _T), ("market_type", _T), ("side", _T), ("exit_reason", _T), ("closed_at_utc", _T), ("pnl", _T), ("r_multiple", _T), ("won", _I), ("source", _T)),
    "counterfactual_trades": (("symbol", _T), ("plan_id", _T), ("decision_id", _T), ("ts_utc", _T), ("side", _T), ("outcome_pnl", _T), ("reason_not_taken", _T)),
    "learning_features": (("position_id", _T), ("symbol", _T), ("ts_utc", _T), ("feature_set_version", _T), ("label", _T)),
    "model_versions": (("kind", _T), ("version", _T), ("status", _T), ("n_train", _I)),
    "model_metrics": (("model_version_id", _T), ("metric", _T), ("value", _T), ("ts_utc", _T), ("window", _T)),
    "strategy_versions": (("name", _T), ("version", _T), ("status", _T), ("symbol", _T)),
    "incidents": (("severity", _T), ("kind", _T), ("ts_utc", _T), ("message", _T), ("resolved", _I)),
    "system_health": (("component", _T), ("ts_utc", _T), ("status", _T), ("message", _T)),
    "llm_calls": (("provider", _T), ("model", _T), ("ts_utc", _T), ("purpose", _T), ("tokens_in", _I), ("tokens_out", _I), ("cost_usd", _T), ("status", _T), ("correlation_id", _T)),
    "deployment_events": (("ts_utc", _T), ("kind", _T), ("version", _T), ("host", _T)),
}

# Özel şekilli tablolar
EVENTS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("event_id", "TEXT PRIMARY KEY"), ("event_type", "TEXT NOT NULL"), ("run_id", _T), ("created_at_utc", "TEXT NOT NULL"),
    ("source", _T), ("causation_id", _T), ("correlation_id", _T), ("schema_version", "INTEGER NOT NULL DEFAULT 1"),
    ("payload_hash", "TEXT NOT NULL UNIQUE"), ("payload_json", _T),
)
SCHEMA_MIGRATIONS_DDL = ("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                         "applied_at_utc TEXT NOT NULL, description TEXT)")
FTS_DDL = "CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(content, ref_table, ref_id)"


def columns_of(table: str) -> tuple[str, ...]:
    """Tablonun sütun adları (payload_json dahil)."""
    if table == "events":
        return tuple(c for c, _ in EVENTS_COLUMNS)
    if table == "schema_migrations":
        return ("version", "applied_at_utc", "description")
    if table not in TABLE_SPECS:
        raise KeyError(f"bilinmeyen tablo: {table}")
    return tuple(c for c, _ in COMMON + TABLE_SPECS[table] + PAYLOAD)


def _ddl(table: str, cols: tuple[tuple[str, str], ...]) -> str:
    return f"CREATE TABLE IF NOT EXISTS {table} (" + ", ".join(f"{n} {t}" for n, t in cols) + ")"


def ddl_statements() -> list[str]:
    """Bütün tablo + index DDL'leri (idempotent, sıra önemli değil)."""
    out = [SCHEMA_MIGRATIONS_DDL, _ddl("events", EVENTS_COLUMNS)]
    out += ["CREATE INDEX IF NOT EXISTS ix_events_type ON events(event_type, created_at_utc)",
            "CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id)",
            "CREATE INDEX IF NOT EXISTS ix_events_corr ON events(correlation_id)",
            "CREATE INDEX IF NOT EXISTS ix_events_created ON events(created_at_utc)"]
    for table, extra in TABLE_SPECS.items():
        out.append(_ddl(table, COMMON + extra + PAYLOAD))
        names = {n for n, _ in extra}
        out.append(f"CREATE INDEX IF NOT EXISTS ix_{table}_created ON {table}(created_at_utc)")
        for col in ("symbol", "ts_utc", "status", "position_id", "run_id"):
            if col in names or col == "run_id":
                out.append(f"CREATE INDEX IF NOT EXISTS ix_{table}_{col} ON {table}({col})")
    return out


ALL_TABLES: tuple[str, ...] = ("events", "schema_migrations") + tuple(TABLE_SPECS)

__all__ = ["SCHEMA_VERSION", "TABLE_SPECS", "ALL_TABLES", "EVENTS_COLUMNS", "FTS_DDL", "columns_of", "ddl_statements"]
