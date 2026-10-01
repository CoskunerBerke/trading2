# -*- coding: utf-8 -*-
"""Dashboard: strateji kagit defteri ozeti ana sayfada ve /portfolio/strategy'de; dosya yoksa 500 YOK."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from tradingbot.dashboard.state import STATE_FILES  # noqa: E402


def _client(tmp_path):
    from fastapi.testclient import TestClient
    from tradingbot.dashboard.app import create_app
    (tmp_path / "data").mkdir(exist_ok=True)
    return TestClient(create_app(tmp_path, tmp_path / "data"))


def test_state_file_registered():
    assert STATE_FILES["strategy_paper"] == "strategy_paper.json"


def test_pages_survive_without_the_file(tmp_path):
    c = _client(tmp_path)
    assert c.get("/portfolio/strategy").status_code == 200
    r = c.get("/")
    assert r.status_code == 200 and "Trend stratejileri" not in r.text


def test_pages_render_the_summary(tmp_path):
    doc = {"schema_version": "strategy_paper_v1", "generated_at": "2026-09-13T12:00:00+00:00", "run_id": "r",
           "name": "t2_trend_regime", "atr_mult": 3.0, "regime": "UP", "starting_equity": 100.0,
           "summary": {"equity_mtm": 104.25, "available": 80.0, "used_margin": 24.25},
           "positions": {"ETH/USDT": {"side": "LONG", "entry": 2000.0, "qty": 0.01, "stop": 1880.0, "leverage": 1,
                                      "opened_at": "2026-09-13T00:15:00+00:00", "last_price": 2040.0}},
           "history_tail": [], "last_actions": {"ETH/USDT": {"action": "OPENED", "reason": "EMA200_TREND", "at": "x"}},
           "counters": {"opened": 1, "closed": 0, "rejected": 0, "tours": 3}, "rejections": {}, "closed_recent": [],
           "note_tr": "KAGIT ILERI TEST"}
    (tmp_path / "strategy_paper.json").write_text(json.dumps(doc), encoding="utf-8")
    c = _client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200 and "Trend stratejileri" in r.text and "104.25" in r.text
    r = c.get("/portfolio/strategy")
    assert r.status_code == 200 and "ETH/USDT" in r.text and "t2_trend_regime" in r.text and "UP" in r.text
