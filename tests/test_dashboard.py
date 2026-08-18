"""Dashboard (FastAPI) — sahte state ile ağsız test. httpx varsa TestClient, yoksa atlanır."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot.core import ConfigError, iso, utc_now
from tradingbot.dashboard.app import DashboardConfig, create_app

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


def _state(tmp: Path) -> tuple[Path, Path]:
    st, data = tmp / "state", tmp / "data"
    st.mkdir(); data.mkdir()
    (st / "coin_heads.json").write_text(json.dumps({"generated_at": iso(), "run_id": "r1", "heads": [
        {"symbol": "BTC/USDT", "market_type": "futures", "regime": "TREND_UP", "verdict": "FUTURES_LONG", "no_trade_reason": "", "direction": "LONG",
         "confidence_calibrated": 0.6, "p_win": 0.55, "expected_return_net": 3.1, "expected_r": 2.0, "stop": 60000.0, "targets": [66000.0, 68000.0],
         "spot_plan": None, "futures_plan": {"valid": True, "entry": 63000.0, "stop": 60000.0, "targets": [66000.0], "notional": 15.0, "margin": 15.0, "expected_r": 2.0, "invalid_reason": ""},
         "consensus": {"trend": 0.5}, "dissent": [], "vetoes": [], "factor_scores": [{"group": "trend", "score": 0.5, "confidence": 0.7, "data_quality": 1.0, "n_independent": 1, "conflict": 0.0}],
         "specialist_reports": [{"agent_name": "trend", "stance": "BULL", "bias": 0.5, "confidence_raw": 70, "evidence_for": ["EMA dizilimi"], "evidence_against": [], "warnings": [], "metrics": {}, "factor_group": "trend", "veto": False, "veto_reason": "", "error": ""}],
         "data_freshness": {}, "expires_at": iso(), "notional": 15.0, "margin": 15.0, "leverage": 1}],
        "chief": {"market_risk_mode": "RISK-ON", "ranking": [], "permission": {}, "rules": [], "exposure": {}, "breadth": {"long": 1, "short": 0, "no_trade": 0, "data_invalid": 0}}}), encoding="utf-8")
    (st / "futures_ledger.json").write_text(json.dumps({"schema_version": 2, "wallet_balance": "50", "starting_equity": "50", "positions": {}, "history": [], "total_fees": "0"}), encoding="utf-8")
    (st / "portfolio.json").write_text(json.dumps({"cash": 50.0, "starting_equity": 50.0, "positions": {}, "history": []}), encoding="utf-8")
    (st / "killswitch.json").write_text(json.dumps({"state": "ARMED", "since": "", "reasons": [], "audit": []}), encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")
    (st / "risk.json").write_text(json.dumps({"profile": {"name": "PAPER_RESEARCH"}, "killswitch": {"state": "ARMED"}, "exposure": {"equity": 50}}), encoding="utf-8")
    (st / "models.json").write_text(json.dumps({"models": [], "events": []}), encoding="utf-8")
    (st / "llm_budget.json").write_text(json.dumps({"day": "2026-08-18", "spent_usd": 0.0, "spent_tokens": 0, "calls": 0}), encoding="utf-8")
    n = 400
    rng = np.random.default_rng(1)
    close = 60000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    ts = 1_700_000_000_000 + np.arange(n) * 14_400_000
    pd.DataFrame({"timestamp": ts, "open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 10.0}).to_csv(data / "tv-binance_BTC-USDT_4h.csv", index=False)
    return st, data


def test_dashboard_pages_api_health_metrics(tmp_path: Path):
    st, data = _state(tmp_path)
    app = create_app(st, data, None, DashboardConfig())
    c = TestClient(app)
    assert c.get("/health/live").status_code == 200
    r = c.get("/health/ready")
    assert r.status_code == 503                      # heartbeat yok
    (st / "heartbeat.json").write_text(json.dumps({"at": iso(utc_now()), "run_id": "r1"}), encoding="utf-8")
    assert c.get("/health/ready").status_code == 200
    for path in ("/", "/scanner", "/coin/BTC", "/portfolio/spot", "/portfolio/futures", "/orders", "/trades", "/learning", "/backtest", "/risk", "/health", "/llm", "/models"):
        r = c.get(path)
        assert r.status_code == 200, path
    assert "BTC" in c.get("/coin/BTC").text and "PAPER" in c.get("/").text
    j = c.get("/api/candles/BTC?tf=4h&n=200").json()
    assert len(j["t"]) == 200 and "sma25" in j["overlays"] and "rsi" in j["panels"] and j["plan"]["stop"] == 60000.0
    m = c.get("/metrics").text
    assert "tradingbot_up" in m and "killswitch_state" in m
    assert c.get("/api/state/coin_heads").status_code == 200 and c.get("/api/state/../etc").status_code in (400, 404)
    assert c.get("/static/plotly.min.js").status_code == 200


def test_dashboard_refuses_public_without_token(tmp_path: Path):
    st, data = _state(tmp_path)
    with pytest.raises(ConfigError):
        create_app(st, data, None, DashboardConfig(host="0.0.0.0", auth_token=None))
    app = create_app(st, data, None, DashboardConfig(host="0.0.0.0", auth_token="s3cret"))
    c = TestClient(app)
    assert c.get("/").status_code in (401, 403)
    assert c.get("/", headers={"Authorization": "Bearer s3cret"}).status_code == 200
