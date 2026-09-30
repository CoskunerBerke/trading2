# -*- coding: utf-8 -*-
"""Panel: GEÇERLİ JSON ama YANLIŞ üst düzey türdeki state dosyası (liste/sayı/metin) sayfayı 500'e düşürmez.

Bot state dosyalarını daima JSON nesnesi olarak yazar; yarım/bozuk bir yazım ya da elle düzenleme sonucu dosya geçerli
ama başka türde JSON olabilir (ör. `[]`). Eskiden `StateReader.get` bunu olduğu gibi döndürüyordu ve `(... or {}).get`
çağrısı AttributeError → HTTP 500 veriyordu (/, /risk, /scanner, /portfolio/spot, /metrics ...). Artık bu dosya
«yok» sayılır; yalnız `orders.json` iki biçimde de (liste ya da {"orders": [...]}) okunmaya devam eder.
"""
from __future__ import annotations

import json
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
