"""Canlı panel: polling uçları, bayat veri uyarısı, güvenlik ve worker bağımsızlığı."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tradingbot.core import ConfigError  # noqa: E402
from tradingbot.dashboard.app import create_app  # noqa: E402
from tradingbot.dashboard.config import DashboardConfig  # noqa: E402
from tradingbot.dashboard.state import StateReader  # noqa: E402

fastapi_testclient = pytest.importorskip("fastapi.testclient")

LEDGER = {
    "schema_version": 2, "kind": "futures", "starting_equity": "5000", "wallet_balance": "5000",
    "max_positions": 3, "enforce_position_cap": False,
    "fees": {"maker_pct": "0.02", "taker_pct": "0.05"},
    "positions": {
        "BZ/USDT": {"id": "F00001", "symbol": "BZ/USDT", "market_type": "USDM_PERP", "side": "LONG",
                    "qty": "10", "entry_avg": "100", "leverage": 1, "isolated_margin": "1000",
                    "stop": "95", "targets": ["110"], "last_price": "103", "entry_fee": "0.5",
                    "opened_at": "2026-08-22T08:00:00+00:00"},
        "XAUT/USDT": {"id": "F00002", "symbol": "XAUT/USDT", "market_type": "USDM_PERP", "side": "LONG",
                      "qty": "0.5", "entry_avg": "2400", "leverage": 1, "isolated_margin": "1200",
                      "stop": "2350", "targets": ["2500"], "last_price": "2390", "entry_fee": "0.6",
                      "opened_at": "2026-08-22T09:00:00+00:00"},
    },
    "history": [], "entries": [], "seq": 2,
}
CHIEF = {"chief": {"generated_at": "2026-08-22T12:00:00+00:00", "market_risk_mode": "RISK-ON",
                   "breadth": {"long": 3, "short": 0, "no_trade": 0, "data_invalid": 0, "hold": 2},
                   "exposure": {"risk_used_usdt": 50.0, "margin_util_pct": 44.0, "drawdown_pct": 1.2}},
         "heads": [], "generated_at": "2026-08-22T12:00:00+00:00"}


def _state(tmp_path: Path, *, ledger_age_s: float = 0.0) -> Path:
    st = tmp_path / "state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "futures_ledger.json").write_text(json.dumps(LEDGER), encoding="utf-8")
    (st / "coin_heads.json").write_text(json.dumps(CHIEF), encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER"}), encoding="utf-8")
    (st / "health.json").write_text(json.dumps({"state": "HEALTHY", "summary": "ok"}), encoding="utf-8")
    if ledger_age_s:
        old = time.time() - ledger_age_s
        os.utime(st / "futures_ledger.json", (old, old))
    return st


def _client(tmp_path: Path, **cfg_kw):
    cfg = DashboardConfig(host="127.0.0.1", **cfg_kw)
    return fastapi_testclient.TestClient(create_app(_state(tmp_path), tmp_path / "data", None, cfg))


def _client_aged(tmp_path: Path, age_s: float, **cfg_kw):
    cfg = DashboardConfig(host="127.0.0.1", **cfg_kw)
    app = create_app(_state(tmp_path, ledger_age_s=age_s), tmp_path / "data", None, cfg)
    return fastapi_testclient.TestClient(app)


# ===================================================================== polling uçları
def test_live_endpoints_expose_positions_and_summary(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/live/positions")
    assert r.status_code == 200
    d = r.json()
    assert d["open_total"] == 2 and d["open_long"] == 2 and d["open_short"] == 0
    assert len(d["rows"]) == 2 and len(d["columns"]) == len(d["rows"][0])
    assert "Coin adedi" in d["columns"] and "Miktar" not in d["columns"]
    assert d["inconsistencies"] == []
    s = c.get("/api/live/summary").json()
    assert s["chief"]["long_candidates"] == 3, "aday sayısı"
    assert s["chief"]["open_long"] == 2, "gerçek açık pozisyon"
    assert any(card["title"] == "Açık pozisyon net K/Z" for card in s["cards"])
    h = c.get("/api/live/health").json()
    assert h["mode"] == "PAPER" and h["price_age_s"] is not None


def test_live_positions_pnl_is_not_zero_when_price_moved(tmp_path):
    """Regresyon: fiyat hareket ettiğinde PnL `+0.00` KALMAZ."""
    c = _client(tmp_path)
    rows = c.get("/api/live/positions").json()["rows"]
    bz = next(r for r in rows if r[0] == "BZ/USDT")
    assert bz[16] not in ("$0.00", "+$0.00"), f"PnL sıfır görünüyor: {bz[16]}"
    assert bz[16].startswith("+$"), bz[16]
    xa = next(r for r in rows if r[0] == "XAUT/USDT")
    assert xa[16].startswith("-$"), xa[16]                # zarar negatif işaretli


def test_stale_price_is_flagged_and_live_badge_not_green(tmp_path):
    c = _client_aged(tmp_path, age_s=140, stale_price_s=90)
    fr = c.get("/api/live/positions").json()["freshness"]
    assert fr["price_state"] == "stale" and fr["price_age_s"] >= 130
    html = c.get("/").text
    assert "FİYAT VERİSİ GÜNCEL DEĞİL" in html
    assert "warn-box" in html, "bayat veride CANLI rozeti yeşil kalmamalı"


def test_fresh_price_shows_live(tmp_path):
    c = _client(tmp_path)
    fr = c.get("/api/live/positions").json()["freshness"]
    assert fr["price_state"] == "live"
    assert "CANLI" in c.get("/").text


def test_price_age_and_strategy_run_age_are_separate(tmp_path):
    """Fiyat yaşı ile STRATEJİ TURU yaşı ayrı alanlardır ve karıştırılmaz."""
    c = _client(tmp_path)
    fr = c.get("/api/live/positions").json()["freshness"]
    assert "price_age_s" in fr and "run_age_s" in fr and "heads_age_s" in fr
    assert fr["price_state"] in ("live", "stale", "unknown")
    assert fr["run_state"] in ("live", "stale", "unknown")
    html = c.get("/").text
    assert "Son fiyat güncellemesi" in html and "Son strateji turu" in html
    assert "saat dilimi" in html, "saat dilimi UI'da açıkça yazılmalı"


def test_polling_script_has_overlap_and_backoff_protection(tmp_path):
    html = _client(tmp_path, poll_positions_s=6, poll_portfolio_s=18, poll_health_s=11).get("/").text
    assert "if(busy[key])return;" in html, "aynı anda ikinci istek gönderilmemeli"
    assert "document.hidden?base*BG" in html, "arka plan sekmesinde backoff"
    assert "AbortController" in html, "istek zaman aşımı"
    assert "6000" in html and "18000" in html and "11000" in html, "config aralıkları HTML'e geçmeli"
    assert "api.binance.com" not in html and "fapi.binance.com" not in html, \
        "tarayıcı borsaya DOĞRUDAN bağlanmamalı"


def test_polling_intervals_are_validated():
    with pytest.raises(ConfigError):
        DashboardConfig(poll_positions_s=1).validate()
    with pytest.raises(ConfigError):
        DashboardConfig(poll_positions_s=30, stale_price_s=10).validate()
    DashboardConfig().validate()


def test_dashboard_reads_only_and_never_writes_state(tmp_path):
    """Panel bağlantısı worker'ı ETKİLEMEZ: state dosyaları değişmez."""
    st = _state(tmp_path)
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in st.iterdir()}
    cfg = DashboardConfig(host="127.0.0.1")
    c = fastapi_testclient.TestClient(create_app(st, tmp_path / "data", None, cfg))
    for _ in range(3):
        c.get("/")
        c.get("/api/live/positions")
        c.get("/api/live/summary")
        c.get("/api/live/health")
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in st.iterdir()}
    assert after == before, "panel state'e YAZMAMALI"


def test_overview_shows_labelled_cards_not_raw_json(tmp_path):
    html = _client(tmp_path).get("/").text
    assert "LONG işlem adayı" in html and "Açık LONG pozisyon" in html
    assert "karar — açık pozisyon DEĞİL" in html
    assert "&#x27;breadth&#x27;" not in html and '"breadth"' not in html, "ham JSON gösterilmemeli"
    assert "Beklenen Net Getiri" in html and "Net E[r]" not in html
    assert "model tahminidir" in html
    assert "Coin adedi" in html
    assert "yatırım tavsiyesi değildir" in html and "PAPER" in html


def test_html_escapes_free_text(tmp_path):
    st = _state(tmp_path)
    bad = dict(CHIEF)
    bad["chief"] = dict(CHIEF["chief"], market_risk_mode="<script>alert(1)</script>")
    (st / "coin_heads.json").write_text(json.dumps(bad), encoding="utf-8")
    c = fastapi_testclient.TestClient(create_app(st, tmp_path / "data", None,
                                                 DashboardConfig(host="127.0.0.1")))
    html = c.get("/").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_telegram_token_never_returned_by_dashboard_api(tmp_path):
    c = _client(tmp_path)
    for path in ("/api/live/positions", "/api/live/summary", "/api/live/health", "/api/overview"):
        body = c.get(path).text
        assert "bot_token" not in body.lower() and "TELEGRAM_BOT_TOKEN" not in body
