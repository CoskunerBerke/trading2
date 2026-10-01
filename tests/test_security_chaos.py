"""Güvenlik + kaos + tekrar üretilebilirlik testleri (ağsız)."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "AKIAFAKEKEY1234567890abcdefGHIJKLMNOP"
FAKE_SECRET = "sk-fake-secret-9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e"


# ---------------------------------------------------------------- 1) API sırları hiçbir çıktıya sızmaz
def test_secret_never_in_logs_obsidian_dashboard_llm(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BINANCE_TESTNET_FUTURES_KEY", FAKE_KEY)
    monkeypatch.setenv("BINANCE_TESTNET_FUTURES_SECRET", FAKE_SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_SECRET)
    from tradingbot.ops.logging_setup import setup_logging
    logdir = tmp_path / "logs"
    setup_logging("INFO", True, logdir, "run_test")
    logging.getLogger("tradingbot.test").info("key=%s secret=%s Authorization: Bearer %s", FAKE_KEY, FAKE_SECRET, FAKE_SECRET)
    for h in logging.getLogger().handlers:
        h.flush()
    text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in logdir.glob("*") if p.is_file())
    assert FAKE_SECRET not in text and FAKE_KEY not in text
    from tradingbot.llm import redact
    r = redact({"api_key": FAKE_KEY, "nested": {"secret": FAKE_SECRET, "ok": "x"}, "text": f"token {FAKE_SECRET}"})
    assert FAKE_KEY not in json.dumps(r) and FAKE_SECRET not in json.dumps(r)
    from tradingbot.obsidian_coinheads import ObsidianCoinHeadWriter
    w = ObsidianCoinHeadWriter(tmp_path / "vault")
    dec = {"symbol": "BTC/USDT", "verdict": "NO_TRADE", "direction": "", "regime": "RANGE", "market_type": "none", "no_trade_reason": "test",
           "confidence_calibrated": 0.1, "p_win": 0.5, "specialist_reports": [], "factor_scores": [], "vetoes": [], "dissent": [], "consensus": {},
           "data_freshness": {}, "model_versions": {}, "api_secret": FAKE_SECRET}
    w.write_coin_head(dec, None, None)
    vault_text = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "vault").rglob("*") if p.is_file())
    assert FAKE_SECRET not in vault_text
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from tradingbot.dashboard.app import DashboardConfig, create_app
    st = tmp_path / "state"
    st.mkdir()
    (st / "coin_heads.json").write_text(json.dumps({"heads": [], "chief": {}}), encoding="utf-8")
    c = TestClient(create_app(st, tmp_path, None, DashboardConfig()))
    for path in ("/", "/risk", "/llm", "/health", "/api/overview", "/api/state/coin_heads", "/metrics"):
        body = c.get(path).text
        assert FAKE_SECRET not in body and FAKE_KEY not in body
    assert c.get("/api/state/env").status_code in (400, 404)


# ---------------------------------------------------------------- 2) live flag olmadan emir yok; withdrawal endpointi yok
def test_live_disabled_and_no_withdrawal_endpoint(monkeypatch):
    from tradingbot.core import ConfigError, ExecutionDisabledError
    from tradingbot.execution import LiveGateway
    from tradingbot.execution.gateway import live_confirm_token
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    with pytest.raises(ExecutionDisabledError):
        LiveGateway(config_allow_live=True, account_label="default", token=live_confirm_token("default")).submit(None)
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
    with pytest.raises(ExecutionDisabledError):
        LiveGateway(config_allow_live=False, account_label="default", token=live_confirm_token("default")).submit(None)
    with pytest.raises(ExecutionDisabledError):
        LiveGateway(config_allow_live=True, account_label="default", token="wrong").submit(None)
    with pytest.raises(NotImplementedError):     # bütün kilitler açık olsa bile bu sürümde canlı emir yolu YOK
        LiveGateway(config_allow_live=True, account_label="default", token=live_confirm_token("default")).submit(None)
    src = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (ROOT / "tradingbot").rglob("*.py"))
    assert "capital/withdraw" not in src and "withdrawApply" not in src and "/sapi/v1/withdraw" not in src
    from tradingbot.config_v3 import load_v3
    with pytest.raises(ConfigError):
        load_v3({"mode": {"mode": "LIVE_LIMITED"}})
    with pytest.raises(ConfigError):
        load_v3({"execution": {"gateway": "live"}})


# ---------------------------------------------------------------- 3) kaos: bozuk state, kilitli DB, disk dolu, LLM yok
def test_chaos_corrupt_state_files_do_not_crash_loop(tmp_path: Path):
    from tradingbot.core import StorageError, read_json
    from tradingbot.learn import ModelRegistry, ShadowBook
    from tradingbot.risk import KillSwitch, ModeState
    for name in ("killswitch.json", "mode.json", "models.json", "shadow_book.json"):
        (tmp_path / name).write_text("{corrupt json", encoding="utf-8")
    assert KillSwitch.load(tmp_path / "killswitch.json").state == "ARMED"
    assert ModeState(tmp_path / "mode.json").mode.value == "PAPER"
    assert ModelRegistry(tmp_path / "models.json").models == []
    assert ShadowBook(tmp_path / "shadow_book.json").trades == []
    assert list(tmp_path.glob("*.corrupt-1"))            # veri silinmedi, kenara alındı
    with pytest.raises(StorageError):
        read_json(tmp_path / "killswitch.json")


def test_chaos_db_locked_and_atomic_write_failure(tmp_path: Path, monkeypatch):
    import sqlite3
    from tradingbot.core import StorageError, atomic_write_json
    from tradingbot.storage import Database
    db = Database(tmp_path / "t.db")
    other = sqlite3.connect(str(tmp_path / "t.db"), timeout=0.1)
    other.execute("BEGIN IMMEDIATE")
    with pytest.raises(StorageError):
        with Database(tmp_path / "t.db", timeout_ms=200) as db2:
            db2.execute("INSERT INTO incidents(id, created_at_utc) VALUES ('x','t')")
    other.rollback()
    other.close()
    db.close()
    target = tmp_path / "ro" / "x.json"

    def boom(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr("tradingbot.core.atomic.os.replace", boom)
    with pytest.raises(StorageError):
        atomic_write_json(target, {"a": 1})
    assert not target.exists() and not list((tmp_path / "ro").glob("*.tmp-*"))


def test_chaos_llm_unavailable_is_fail_closed(tmp_path: Path):
    from tradingbot.llm import FakeProvider, LLMBudget, LLMMode, LLMResearchService
    svc = LLMResearchService(FakeProvider([RuntimeError("network down"), RuntimeError("network down")]),
                             LLMBudget(tmp_path / "b.json", daily_usd=1.0), mode=LLMMode.ADVISORY)
    adv = svc.advise({"symbol": "BTC/USDT", "x": 1}, ["e1"], [])
    assert adv is not None and adv.failed and adv.veto is False and adv.recommended_action != "PROCEED"


# ---------------------------------------------------------------- 4) tekrar üretilebilirlik
def test_replay_determinism_same_input_same_decision():
    sys.path.insert(0, str(Path(__file__).parent))
    import test_coinhead as T
    from tradingbot.coinhead import CoinHead, CoinHeadConfig
    from tradingbot.core import payload_hash
    fr = T.frames(seed=5, drift=0.0015)
    reports, brief = T.legacy(fr)
    hashes = set()
    for _ in range(3):
        d = CoinHead("ETH/USDT", CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)).decide(T._inputs(fr, reports, brief))
        dd = d.to_dict(include_reports=False)
        for k in ("expires_at", "generated_at", "latency_ms"):
            dd.pop(k, None)
        hashes.add(payload_hash(dd))
    assert len(hashes) == 1
