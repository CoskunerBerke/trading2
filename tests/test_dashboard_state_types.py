# -*- coding: utf-8 -*-
"""Panel: GEÇERLİ JSON ama YANLIŞ üst düzey türdeki state dosyası (liste/sayı/metin) sayfayı 500'e düşürmez.

Bot state dosyalarını daima JSON nesnesi olarak yazar; yarım/bozuk bir yazım ya da elle düzenleme sonucu dosya geçerli
ama başka türde JSON olabilir (ör. `[]`). Eskiden `StateReader.get` bunu olduğu gibi döndürüyordu ve `(... or {}).get`
çağrısı AttributeError → HTTP 500 veriyordu (/, /risk, /scanner, /portfolio/spot, /metrics ...). Artık bu dosya
«yok» sayılır; yalnız `orders.json` iki biçimde de (liste ya da {"orders": [...]}) okunmaya devam eder.
`strategy_paper_index.json` (`books` liste değilse de) ve `chart_analysis/config.json` için de aynı kural geçerlidir.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tradingbot.dashboard.state import StateReader

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from tradingbot.dashboard.app import create_app  # noqa: E402

PAGES = ("/", "/risk", "/scanner", "/portfolio/spot", "/portfolio/futures", "/trades", "/orders", "/health", "/learning",
         "/api/overview", "/api/live/summary", "/api/live/positions", "/api/live/health", "/metrics",
         "/?book=strategy_paper")


def _write(p: Path, doc) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize("bad", [[1, "x"], "bozuk", 42])
def test_wrong_top_level_type_is_treated_as_missing_not_500(tmp_path, bad):
    state, data = tmp_path / "state", tmp_path / "data"
    data.mkdir()
    for fn in ("risk.json", "portfolio.json", "scan.json", "spot_ledger.json", "research_policy.json",
               "snapshot_telemetry.json", "universe.json", "futures_ledger.json", "health.json"):
        _write(state / fn, bad)
    _write(state / "strategy_paper_index.json", {"books": [{"key": "strategy_paper", "name": "t2_trend_regime",
                                                            "summary_file": "strategy_paper.json"}]})
    _write(state / "strategy_paper" / "futures_ledger.json", bad)
    rd = StateReader(state)
    assert rd.get("risk") is None and rd.get("portfolio") is None and rd.book_ledger("strategy_paper") is None
    c = TestClient(create_app(state, data), raise_server_exceptions=False)
    for page in PAGES:
        assert c.get(page).status_code == 200, page
    assert c.get("/api/state/risk").status_code == 404


def test_orders_file_may_still_be_a_list_or_an_object(tmp_path):
    state = tmp_path / "state"
    order = {"id": "O1", "symbol": "BTC/USDT", "side": "BUY", "status": "FILLED"}
    _write(state / "orders.json", [order, "not-a-dict"])
    rd = StateReader(state)
    assert rd.get("orders") == [order, "not-a-dict"]
    assert rd.orders() == [order]
    _write(state / "orders.json", {"orders": [order]})
    assert rd.orders() == [order]


def _candles(data: Path, n: int = 300) -> None:
    """Şimdiye kadar kapanmış `n` adet 1h BTC mumu (grafik ucu analiz yoluna girsin diye)."""
    now_h = int(time.time() // 3600) * 3600 * 1000
    rows = ["timestamp,open,high,low,close,volume"]
    for i in range(n):
        px = 100.0 + i * 0.1
        rows.append("%d,%s,%s,%s,%s,10" % (now_h - (n - i) * 3_600_000, px, px + 1, px - 1, px + 0.5))
    data.mkdir(parents=True, exist_ok=True)
    (data / "binanceusdm_BTC-USDT_1h.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


INDEX_PAGES = ("/", "/trades", "/portfolio/strategy", "/coin/BTC", "/?book=strategy_paper", "/?book=pattern_trader",
               "/trades?book=strategy_paper", "/api/book/strategy_paper", "/api/chart/BTC?market=futures&tf=1h",
               "/api/planbox/BTC", "/api/learning-mode")


@pytest.mark.parametrize("bad", [[1, "x"], "bozuk", 42, {"books": 42}, {"books": "x"}, {"books": {"key": "strategy_paper"}}])
def test_strategy_index_with_wrong_type_is_ignored_not_500(tmp_path, bad):
    state, data = tmp_path / "state", tmp_path / "data"
    _candles(data)
    _write(state / "strategy_paper_index.json", bad)
    _write(state / "strategy_paper.json", {"name": "t2_trend_regime", "key": "strategy_paper"})
    rd = StateReader(state)
    assert rd.strategy_index_books() == []
    assert [b["book_id"] for b in rd.books()] == ["main"]
    c = TestClient(create_app(state, data), raise_server_exceptions=False)
    for page in INDEX_PAGES:
        assert c.get(page).status_code < 500, page
    for page in ("/", "/trades", "/portfolio/strategy"):
        assert c.get(page).status_code == 200, page


def test_strategy_index_keeps_valid_books_and_skips_non_dict_entries(tmp_path):
    state = tmp_path / "state"
    good = {"key": "strategy_paper", "name": "t2_trend_regime", "summary_file": "strategy_paper.json"}
    _write(state / "strategy_paper_index.json", {"books": [1, "x", None, good]})
    rd = StateReader(state)
    assert rd.strategy_index_books() == [good]
    assert [b["book_id"] for b in rd.books()] == ["main", "strategy_paper"]


@pytest.mark.parametrize("bad", [[1, "x"], "bozuk", 42])
def test_chart_analysis_config_with_wrong_type_is_ignored_not_500(tmp_path, bad):
    state, data = tmp_path / "state", tmp_path / "data"
    _candles(data)
    _write(state / "chart_analysis" / "config.json", bad)
    c = TestClient(create_app(state, data), raise_server_exceptions=False)
    assert c.get("/api/chart/BTC?market=futures&tf=1h").status_code == 200
