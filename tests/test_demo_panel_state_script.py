# -*- coding: utf-8 -*-
"""Demo panel durumu betiği (`scripts/demo_panel_state.py`): yalnız SENTETİK veri, gerçek defter biçimi, güvenli CLI.

* `--help` hiçbir şey yazmaz (eskiden `--help` adlı bir klasöre demo state yazıyordu).
* Bu betiğin yazmadığı dolu bir `state/` klasörüne yazmaz (işaret dosyası yoksa çıkış 2, dosyalar aynen).
* Defterler gerçek `FuturesLedgerV2` biçimindedir (yükleyici kabul eder), mumlar demo fiyatında biter,
  aynı tohum + aynı saat aynı dosyaları üretir ve panel bu durumla hatasız açılır.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradingbot.accounting import FuturesLedgerV2

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_panel_state.py"
_spec = importlib.util.spec_from_file_location("demo_panel_state", SCRIPT)
DP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DP)
NOW_MS = int(datetime(2026, 9, 30, 12, 7, 30, tzinfo=timezone.utc).timestamp() * 1000)


def _tree(p: Path) -> dict[str, bytes]:
    return {q.relative_to(p).as_posix(): q.read_bytes() for q in sorted(p.rglob("*")) if q.is_file()}


def test_help_prints_usage_and_writes_nothing(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout.lower()
    assert list(tmp_path.iterdir()) == [], "--help bir klasör/dosya yazmamalı"


def test_refuses_a_state_dir_it_did_not_write(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "futures_ledger.json").write_text('{"real": true}', encoding="utf-8")
    before = _tree(tmp_path)
    assert DP.main([str(tmp_path)]) == 2
    with pytest.raises(ValueError):
        DP.build(tmp_path)
    assert _tree(tmp_path) == before


def test_demo_state_uses_real_ledger_format_and_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    out = DP.build(a, seed=7, now_ms=NOW_MS)
    DP.build(b, seed=7, now_ms=NOW_MS)
    assert _tree(a) == _tree(b), "aynı tohum + aynı saat aynı dosyaları üretmeli"
    state = out["state"]
    assert (state / DP.MARKER).exists()
    idx = json.loads((state / "strategy_paper_index.json").read_text(encoding="utf-8"))
    keys = [x["key"] for x in idx["books"]]
    assert keys == [k for k, _n, _t in DP.BOOKS if k != "main"]
    n_trades = 0
    for rel in ["futures_ledger.json"] + ["%s/futures_ledger.json" % k for k in keys]:
        led = FuturesLedgerV2.from_dict(json.loads((state / rel).read_text(encoding="utf-8")))
        n_trades += len(led.positions) + len(led.history)
        for h in led.history:
            assert h.exit_reason and h.closed_at
    assert n_trades >= 15, "demo işlemlerinin çoğu gerçekten açılmalı (ör. STEP_ZERO_QTY reddi olmamalı)"
    for base, px in DP.PRICES.items():
        with (out["data"] / ("binanceusdm_%s-USDT_1h.csv" % base)).open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == DP.N_BARS
        assert float(rows[-1]["close"]) == pytest.approx(px, rel=1e-6)
        for r in rows:
            assert float(r["low"]) <= min(float(r["open"]), float(r["close"])) <= max(float(r["open"]), float(r["close"])) <= float(r["high"])
    sm = json.loads((state / "shared_experience" / "report_summary.json").read_text(encoding="utf-8"))
    assert sm["schema"] == "shared_experience_summary_v1" and sm["top_cells"]
    # kendi çıktısına yeniden yazabilir (işaret dosyası var); ortak deneyim satırları ikinci kez EKLENMEZ
    hot = state / "shared_experience" / "experience.jsonl"
    n_rows = len(hot.read_text(encoding="utf-8").splitlines())
    assert DP.main([str(a)]) == 0
    assert len(hot.read_text(encoding="utf-8").splitlines()) == n_rows


def test_dashboard_renders_the_demo_state(tmp_path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app

    out = DP.build(tmp_path / "demo", seed=7)
    c = TestClient(create_app(out["state"], out["data"]))
    home = c.get("/")
    assert home.status_code == 200
    assert "Ortak deneyim" in home.text and "Gölge danışman" in home.text
    for path in ("/?book=strategy_paper_box", "/patterns", "/trades", "/api/chart/BTC?market=futures&tf=1h",
                 "/api/shared-experience", "/api/shared-experience-advisor"):
        assert c.get(path).status_code == 200, path
