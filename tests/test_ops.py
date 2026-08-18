"""tradingbot.ops testleri — kilit, sağlık, yedek/geri yükleme, doktor, loglama/maskeleme, bildirim (çevrimdışı)."""
from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import pytest

from tradingbot.core import atomic_write_json, iso, utc_now
from tradingbot.ops import (AlreadyRunningError, HealthMonitor, HealthState, JsonLineFormatter, Notifier, RedactionFilter,
                            SingletonLock, heartbeat, print_report, read_heartbeat_age, restore_backup, run_backup, run_doctor,
                            setup_logging, verify_backup)
from tradingbot.ops.backup import latest_backup


# ----------------------------------------------------------------------------- lock
def test_lock_blocks_second_acquire(tmp_path: Path):
    p = tmp_path / "tradingbot.lock"
    a = SingletonLock(p).acquire()
    assert a.held and a.read_pid() == os.getpid()
    with pytest.raises(AlreadyRunningError):
        SingletonLock(p).acquire()
    assert SingletonLock(p).is_locked_by_other() is True
    a.release()
    assert not a.held
    with SingletonLock(p) as b:
        assert b.held
    assert SingletonLock(p).is_locked_by_other() is False


# ----------------------------------------------------------------------------- health
def test_health_states(tmp_path: Path):
    mon = HealthMonitor(tmp_path, heartbeat_max_age_s=60)
    # kalp atışı yok → DEGRADED
    r = mon.evaluate({})
    assert r.state == HealthState.DEGRADED and r.ready   # DEGRADED hâlâ "ready" (trafik alabilir)
    assert not mon.evaluate({"killswitch_state": "HALT_ALL"}).ready
    heartbeat(tmp_path, run_id="run_x")
    assert read_heartbeat_age(tmp_path) is not None and read_heartbeat_age(tmp_path) < 5
    assert mon.evaluate({}).state == HealthState.HEALTHY
    assert mon.evaluate({"killswitch_state": "HALT_ALL"}).state == HealthState.KILL_SWITCH
    assert mon.evaluate({"reconciliation_required": True}).state == HealthState.RECONCILIATION_REQUIRED
    assert mon.evaluate({"data_age_s": 10 ** 6}).state == HealthState.DATA_STALE
    assert mon.evaluate({"paused": True}).state == HealthState.PAUSED
    # öncelik: kill switch > recon
    assert mon.evaluate({"killswitch_state": "HALT_ENTRIES", "reconciliation_required": True}).state == HealthState.KILL_SWITCH
    rep = mon.evaluate({"error_count": 99})
    assert rep.state == HealthState.DEGRADED
    p = mon.write(rep)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["state"] == "DEGRADED" and d["ready"] is True and any(c["name"] == "errors" and not c["ok"] for c in d["checks"])


# ----------------------------------------------------------------------------- backup / restore
def _mk_state(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state / "futures_ledger.json", {"equity": 50.0, "positions": {}, "history": []})
    atomic_write_json(state / "coin_heads.json", {"generated_at": iso(), "heads": []})
    (state / "trade_memory.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    con = sqlite3.connect(state / "journal.db")
    con.execute("create table t(x int)"); con.execute("insert into t values (1)"); con.commit(); con.close()
    (state / "tradingbot.lock").write_text("123", encoding="utf-8")


def test_backup_verify_restore_roundtrip_with_retention(tmp_path: Path):
    state = tmp_path / "state"
    backups = tmp_path / "backups"
    _mk_state(state)
    results = []
    for _ in range(3):
        results.append(run_backup(state, backups, "hourly", keep_hourly=2))
        time.sleep(1.05)  # ts saniye çözünürlüklü
    archives = sorted((backups / "hourly").glob("*.tar.gz"))
    assert len(archives) == 2, "retention keep_hourly=2"
    assert results[-1].pruned  # ilk arşiv silindi
    last = Path(results[-1].archive)
    assert last.with_name(last.name + ".sha256").exists()
    v = verify_backup(last)
    assert v["ok"] and v["members"] > 0 and v["sha256"] == results[-1].sha256
    assert latest_backup(backups) == last
    # bozulmuş arşiv → doğrulama başarısız
    bad = backups / "hourly" / "tradingbot-hourly-00000000T000000Z.tar.gz"
    bad.write_bytes(b"garbage")
    bad.with_name(bad.name + ".sha256").write_text("deadbeef  x\n", encoding="utf-8")
    assert verify_backup(bad)["ok"] is False
    # dry-run
    dr = restore_backup(last, state, dry_run=True)
    assert dr["dry_run"] and any("futures_ledger.json" in m for m in dr["members"])
    # state'i değiştir, geri yükle
    atomic_write_json(state / "futures_ledger.json", {"equity": 1.0, "positions": {}, "history": []})
    res = restore_backup(last, state)
    assert res["ok"] and res["previous"] and Path(res["previous"]).exists()
    d = json.loads((state / "futures_ledger.json").read_text(encoding="utf-8"))
    assert d["equity"] == 50.0
    assert not (state / "tradingbot.lock").exists()   # kilit dosyası yedeğe girmez
    con = sqlite3.connect(state / "journal.db")
    assert con.execute("select count(*) from t").fetchone()[0] == 1
    con.close()
    with pytest.raises(Exception):
        restore_backup(bad, state)


# ----------------------------------------------------------------------------- doctor
def test_doctor_on_tmp_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    state = tmp_path / "state"
    _mk_state(state)
    heartbeat(state)
    rep = run_doctor({"mode": "PAPER"}, state, tmp_path / "data", tmp_path / "vault", quick=True, backups_dir=tmp_path / "backups")
    names = {c.name for c in rep.checks}
    assert {"config", "state_writable", "lock", "state_json", "vault_writable", "disk_free", "clock_skew", "deps_required",
            "db_integrity", "backup_freshness", "heartbeat", "mode", "allow_live_env"} <= names
    assert rep.ok, [c.to_dict() for c in rep.failures]
    assert next(c for c in rep.checks if c.name == "clock_skew").detail.startswith("quick")
    buf = io.StringIO(); print_report(rep, file=buf)
    assert "doctor: OK" in buf.getvalue()
    # bozuk JSON → fail
    (state / "risk.json").write_text("{bozuk", encoding="utf-8")
    rep2 = run_doctor(None, state, quick=True, backups_dir=tmp_path / "backups")
    assert not rep2.ok and any(c.name == "state_json" and not c.ok for c in rep2.checks)
    assert any(c.name == "config" and not c.ok for c in rep2.checks)
    # LIVE modu + ALLOW_LIVE_TRADING yok → fail
    (state / "risk.json").unlink()
    atomic_write_json(state / "mode.json", {"mode": "LIVE", "history": []})
    rep3 = run_doctor({}, state, quick=True, backups_dir=tmp_path / "backups")
    assert any(c.name == "mode" and not c.ok for c in rep3.checks) and not rep3.ok
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
    rep4 = run_doctor({}, state, quick=True, backups_dir=tmp_path / "backups")
    assert next(c for c in rep4.checks if c.name == "mode").ok
    assert any(c.name == "allow_live_env" and not c.ok for c in rep4.checks)


# ----------------------------------------------------------------------------- logging
def test_redaction_filter_and_json_log_line(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-SUPERSECRETVALUE1234567890")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "binancesecret987654321")
    stream = io.StringIO()
    root = setup_logging("INFO", json_lines=True, log_dir=tmp_path / "logs", run_id="run_test", stream=stream)
    log = logging.getLogger("tradingbot.test")
    log.info("anahtar sk-ant-api03-SUPERSECRETVALUE1234567890 ve secret=binancesecret987654321 ve api_key: abcdef123456 Authorization: Bearer XYZ12345678", extra={"symbol": "BTC/USDT"})
    for h in root.handlers:
        h.flush()
    line = stream.getvalue().strip().splitlines()[-1]
    d = json.loads(line)
    assert d["level"] == "INFO" and d["logger"] == "tradingbot.test" and d["run_id"] == "run_test" and d["symbol"] == "BTC/USDT"
    assert "SUPERSECRET" not in line and "binancesecret" not in line and "abcdef123456" not in line and "XYZ12345678" not in line
    assert "***" in d["msg"]
    files = list((tmp_path / "logs").glob("tradingbot.log*"))
    assert files and any("run_test" in f.read_text(encoding="utf-8") for f in files)
    # bağımsız filtre
    f = RedactionFilter(extra_secrets=["hunter2secret"])
    assert f.redact("pw hunter2secret token=abcd1234 x") == "pw *** token=*** x"
    fmt = JsonLineFormatter("r1")
    rec = logging.LogRecord("x", logging.WARNING, "f.py", 1, "hello %s", ("w",), None)
    out = json.loads(fmt.format(rec))
    assert out["msg"] == "hello w" and out["run_id"] == "r1" and out["ts"].endswith("+00:00")
    # temizlik: handler'ları kaldır
    setup_logging("WARNING", json_lines=False, stream=io.StringIO())


# ----------------------------------------------------------------------------- notify
def test_notifier_only_sends_when_env_set():
    calls: list[tuple[str, dict]] = []

    def http(url, body, timeout):
        calls.append((url, body)); return 200

    n = Notifier.from_env(enabled=True, http=http, env={}, include_log=False)
    assert n.send("t", "x") == [] and not calls
    n2 = Notifier.from_env(enabled=True, http=http, env={"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42", "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/x"}, include_log=False)
    res = n2.send("Başlık", "metin", "warning")
    assert [r.channel for r in res] == ["telegram", "discord"] and all(r.ok for r in res) and len(calls) == 2
    assert calls[0][1]["chat_id"] == "42" and "Başlık" in calls[1][1]["content"]
    n3 = Notifier.from_env(enabled=False, http=http, env={"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/x"}, include_log=False)
    assert n3.send("t") == []
    n4 = Notifier.from_env(enabled=True, http=http, env={"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/x"}, include_log=False, min_level="error")
    assert n4.send("t", "x", "info") == []
