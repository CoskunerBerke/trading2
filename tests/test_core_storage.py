"""Phase 1 — core yardımcıları + SQLite depo + legacy migration (ağsız)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from tradingbot.core import (D, StorageError, atomic_write_json, atomic_write_text, envelope, floor_to_step,
                             funding_settlements_between, payload_hash, quantize_price, quantize_qty, read_json,
                             stable_id)
from tradingbot.storage import CandleStore, Database, EventJournal, Repository, migrate_state_dir


# ------------------------------------------------------------------ core
def test_decimal_quantization_rules():
    assert quantize_qty("0.0166666", "0.001") == Decimal("0.016")          # aşağı
    assert quantize_qty(0.1 * 3, "0.1") == Decimal("0.3")                    # float gürültüsü yok
    assert floor_to_step("71.9", "1") == Decimal("71")
    assert quantize_price("3000.123", "0.01", "SELL") == Decimal("3000.13")  # satış yukarı
    assert quantize_price("3000.129", "0.01", "BUY") == Decimal("3000.12")   # alış aşağı
    assert D(None) == Decimal("0") and D(1.1) == Decimal("1.1")


def test_funding_settlements_all_missed_periods():
    s = datetime(2026, 8, 17, 7, 59, tzinfo=timezone.utc)
    e = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    hours = [t.hour for t in funding_settlements_between(s, e)]
    assert hours == [8, 16, 0, 8]
    assert funding_settlements_between(e, s) == []


def test_payload_hash_canonical_and_stable_id():
    assert payload_hash({"a": 1, "b": Decimal("1.10")}) == payload_hash({"b": Decimal("1.10"), "a": 1})
    assert stable_id("coinhead", "BTC/USDT") == stable_id("coinhead", "BTC/USDT") and len(stable_id("x")) == 16


def test_atomic_write_and_corrupt_read(tmp_path: Path):
    p = tmp_path / "s.json"
    assert atomic_write_json(p, {"x": Decimal("1.5")}) is True
    assert atomic_write_json(p, {"x": Decimal("1.5")}, skip_if_unchanged=True) is False
    assert read_json(p) == {"x": "1.5"}
    atomic_write_text(p, "{bad json", keep_backup=True)          # yedek .bak alınır
    assert read_json(p) == {"x": "1.5"}                             # .bak'a düşer
    assert (tmp_path / "s.json.corrupt-1").exists()                 # bozuk kopya silinmez
    (tmp_path / "s.json.bak").unlink()
    with pytest.raises(StorageError):
        read_json(p)
    assert read_json(p, default={"d": 1}) == {"d": 1}
    assert not list(tmp_path.glob("*.tmp-*"))                       # geçici dosya kalmaz


# ------------------------------------------------------------------ storage
def test_database_wal_schema_journal_repo_backup(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    assert db.journal_mode() == "wal" and len(db.tables()) >= 35 and db.schema_version() >= 1
    db2 = Database(tmp_path / "t.db")   # idempotent yeniden açılış
    assert db2.schema_version() == db.schema_version()
    db2.close()
    j = EventJournal(db)
    e = envelope("order.filled", {"qty": Decimal("0.001")}, run_id="r1", source="ledger", correlation_id="trade1")
    assert j.append(e) is True and j.append(e) is False and j.count() == 1   # payload_hash dedup
    assert j.by_correlation("trade1")[0].event_type == "order.filled"
    repo = Repository(db)
    repo.upsert("positions", {"id": "p1", "symbol": "BTC/USDT", "market_type": "USDM_PERP", "side": "LONG", "status": "OPEN",
                              "qty": Decimal("0.001"), "extra": {"x": 1}})
    row = repo.get("positions", "p1")
    assert row["qty"] == "0.001" and repo.open_positions()[0]["symbol"] == "BTC/USDT"
    b = db.backup(tmp_path / "b.db")
    assert b.exists() and db.integrity_check() and Database(b).count("positions") == 1
    db.close()


def test_candle_store_dedup(tmp_path: Path):
    cs = CandleStore(tmp_path / "c")
    df = pd.DataFrame({"timestamp": [1, 2, 3], "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    cs.write("BTC/USDT", "4h", df)
    cs.write("BTC/USDT", "4h", df)
    assert len(cs.read("BTC/USDT", "4h")) == 3 and cs.last_ts("BTC/USDT", "4h") == 3


def _legacy_state(d: Path) -> None:
    (d / "portfolio.json").write_text(json.dumps({"cash": 45.0, "starting_equity": 50.0, "updated_at": "t",
        "positions": {"SOL/USDT": {"symbol": "SOL/USDT", "units": 0.05, "entry_price": 100.0, "entry_time": "2026-08-01T00:00:00+00:00", "stop": 90.0, "strategy": "s"}},
        "history": [{"symbol": "BTC/USDT", "entry_time": "2026-07-01T00:00:00+00:00", "exit_time": "2026-07-02T00:00:00+00:00", "entry_price": 100.0, "exit_price": 110.0, "units": 0.1, "pnl": 0.9, "pnl_pct": 9.0, "reason": "signal", "strategy": "s"}]}), encoding="utf-8")
    pos = {"id": "F00001", "symbol": "ETH/USDT", "side": "LONG", "entry": 3000.0, "units": 0.01, "notional": 30.0, "leverage": 2, "margin": 15.0, "stop": 2900.0,
           "target1": 3100.0, "target2": 3200.0, "opened_at": "2026-08-10T00:00:00+00:00", "setup_type": "kırılım", "trigger_text": "t", "features": {"bias_trend": 0.5},
           "tp1_done": False, "realized": 0.0, "fees": 0.015, "funding": 0.0, "mae_pct": -0.5, "mfe_pct": 1.0, "last_price": 3010.0, "last_funding_at": "2026-08-10T00:00:00+00:00", "bars_held": 0}
    hist = {"id": "F00000", "symbol": "XRP/USDT", "side": "SHORT", "entry": 1.0, "exit_reason": "hedef2", "closed_at": "2026-08-05T00:00:00+00:00", "opened_at": "2026-08-04T00:00:00+00:00",
            "pnl": 1.2, "fees": 0.02, "funding": 0.01, "r_multiple": 2.0, "mae_pct": -0.2, "mfe_pct": 3.0, "bars_held": 0, "leverage": 3, "setup_type": "geri çekilme", "trigger_text": "t", "features": {"bias_trend": -0.6}, "tp1_done": True}
    (d / "futures_ledger.json").write_text(json.dumps({"equity": 51.2, "starting_equity": 50.0, "updated_at": "t", "positions": {"ETH/USDT": pos}, "history": [hist], "total_fees": 0.035, "seq": 1}), encoding="utf-8")
    (d / "learning.json").write_text(json.dumps({"weights": {"bias_trend": 0.1}, "bias": 0.0, "n_trades": 1, "n_wins": 1, "sum_r": 2.0, "agent_hits": {"trend": [1, 1]},
                                                  "setup_stats": {"geri çekilme|SHORT": {"n": 1, "wins": 1, "sum_r": 2.0}}, "symbol_stats": {}, "exit_stats": {"hedef2": 1}, "lessons": [], "blacklist": [], "agent_weights": {"trend": 0.3}}), encoding="utf-8")
    (d / "triggers.json").write_text(json.dumps({"BTC/USDT": "2026-08-17 16:00:00+00:00"}), encoding="utf-8")
    (d / "signals_log.jsonl").write_text('{"run_time": "2026-08-17T20:13:05+00:00", "summary": {"equity": 50}, "decisions": [], "executed": [], "equity": 50}\n', encoding="utf-8")


def test_legacy_migration_idempotent_and_tolerant(tmp_path: Path):
    st = tmp_path / "state"
    st.mkdir()
    _legacy_state(st)
    (st / "agents.json").write_text("{corrupt", encoding="utf-8")   # bozuk dosya diğerlerini engellemez
    db = Database(tmp_path / "t.db")
    rep = migrate_state_dir(st, db)
    assert rep.total > 0 and rep.files["agents.json"] in ("corrupt", "error") and rep.files["portfolio.json"] == "ok"
    assert Repository(db).count("positions") if hasattr(Repository(db), "count") else True
    assert db.count("positions") >= 3 and db.count("trade_outcomes") >= 2
    rep2 = migrate_state_dir(st, db)
    assert rep2.total == 0                     # idempotent
    # eski dosyalar değişmedi
    assert json.loads((st / "futures_ledger.json").read_text(encoding="utf-8"))["equity"] == 51.2
    db.close()
