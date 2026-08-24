"""Quant Evaluation V1 — offline rapor üreticisi testleri (uçtan uca, ağsız).

Kapsam: girdiler salt okunur kalır, determinizm, journal→outcome bağlantısı rapora akar,
varsayılan KEEP_CHAMPION (bu yoldan PROMOTE çıkamaz), state dizinine yazım fail-closed,
çıktı dashboard /api/quant/summary sözleşmesiyle uyumlu ve RFC-safe.
"""
from __future__ import annotations

import json

import pytest

from tradingbot.core import read_json
from tradingbot.quant.run import SCHEMA_VERSION, build_report, main


def _write_inputs(tmp_path, n=30):
    mem = tmp_path / "trade_memory.jsonl"
    lines = []
    for i in range(n):
        win = i % 3 != 0
        tid = f"t{i}"
        lines.append(json.dumps({"kind": "entry", "trade_id": tid, "source": "LIVE_PAPER",
                                 "symbol": "ETH/USDT" if i % 2 else "SOL/USDT",
                                 "market_type": "futures", "side": "LONG",
                                 "decision_ts": f"2026-01-{(i % 27) + 1:02d}T12:00:00+00:00",
                                 "p_win": 0.6 if win else 0.45,
                                 "plan": {"plan_id": f"p{i}", "entry": 100.0, "stop": 95.0,
                                          "targets": [110.0], "leverage": 3}}))
        lines.append(json.dumps({"kind": "exit", "trade_id": tid,
                                 "outcome": {"exit_reason": "hedef" if win else "stop",
                                             "net_pnl": 30.0 if win else -25.0, "fees": 1.0,
                                             "funding": 0.2, "r_multiple": 1.2 if win else -1.0,
                                             "mae_pct": -1.0, "mfe_pct": 4.0, "bars_held": 6}}))
    mem.write_text("\n".join(lines), encoding="utf-8")
    shadow = tmp_path / "shadow_book.json"
    shadow.write_text(json.dumps({"trades": [
        {"id": "sh1", "plan_id": "px", "symbol": "BTC/USDT", "market_type": "futures",
         "direction": "SHORT", "created_at": "2026-01-05T00:00:00+00:00", "entry": 60000.0,
         "stop": 62000.0, "targets": [57000.0], "horizon_bars": 12, "variant": "as_planned",
         "reason_not_opened": ["VETO"], "label_ts": "2026-01-07T00:00:00+00:00",
         "tf_minutes": 240, "leverage": 2.0,
         "outcome": {"r_multiple": 0.8, "exit_reason": "target", "bars": 8,
                     "mae_pct": -0.4, "mfe_pct": 3.1, "won": True}}]}), encoding="utf-8")
    return mem, shadow


def test_end_to_end_report_deterministic_and_inputs_untouched(tmp_path):
    mem, shadow = _write_inputs(tmp_path)
    mem_bytes, shadow_bytes = mem.read_bytes(), shadow.read_bytes()
    out = tmp_path / "reports" / "quant_eval.json"
    rc = main(["--memory", str(mem), "--shadow", str(shadow), "--out", str(out),
               "--run-id", "r-test", "--code-sha", "sha-test", "--seed", "7"])
    assert rc == 0 and out.exists()
    assert mem.read_bytes() == mem_bytes and shadow.read_bytes() == shadow_bytes  # salt okunur
    doc1 = read_json(out)
    rc2 = main(["--memory", str(mem), "--shadow", str(shadow), "--out", str(out),
                "--run-id", "r-test", "--code-sha", "sha-test", "--seed", "7"])
    assert rc2 == 0 and read_json(out) == doc1               # determinizm
    assert doc1["schema_version"] == SCHEMA_VERSION
    assert doc1["journal"]["n_records"] == 31                # 30 memory + 1 shadow
    assert doc1["journal"]["n_labeled"] == 31
    assert doc1["overall"]["n"] == 30                        # gerçek fill havuzu (shadow hariç)
    assert doc1["champion_challenger"]["decision"] != "PROMOTE_CANDIDATE"
    assert doc1["manifest"]["valid_backtest"] is False       # kalite kapısı çalışmadı → geçerli backtest DEĞİL
    assert "TEST DATA" in doc1["manifest"]["label"]
    dumped = json.dumps(doc1, allow_nan=False)               # RFC-safe
    assert "NaN" not in dumped and "Infinity" not in dumped


def test_state_out_fails_closed(tmp_path):
    mem, shadow = _write_inputs(tmp_path, n=3)
    state_out = tmp_path / "state" / "quant_eval.json"
    rc = main(["--memory", str(mem), "--shadow", str(shadow), "--out", str(state_out)])
    assert rc == 2 and not state_out.exists()
    rc2 = main(["--memory", str(mem), "--shadow", str(shadow), "--out", str(state_out),
                "--allow-state-out"])
    assert rc2 == 0 and state_out.exists()                   # yalnız açık onayla


def test_build_report_insufficient_sample_warns(tmp_path):
    rep = build_report(memory_rows=[], shadow_trades=[], run_id="r", code_sha="s")
    assert rep["overall"]["n"] == 0 and rep["overall"]["insufficient_sample"] is True
    assert any("yetersiz örnek" in w for w in rep["warnings"])
    assert rep["champion_challenger"]["decision"] == "KEEP_CHAMPION"
    assert rep["walk_forward"] is None and rep["risk_clusters"] is None


def test_report_matches_dashboard_contract(tmp_path):
    """Üretilen dosya /api/quant/summary'nin okuduğu anahtarlarla uyumlu olmalı."""
    mem, shadow = _write_inputs(tmp_path)
    out = tmp_path / "q.json"
    assert main(["--memory", str(mem), "--shadow", str(shadow), "--out", str(out)]) == 0
    doc = read_json(out)
    for key in ("schema_version", "champion_challenger", "overall", "journal",
                "walk_forward", "attribution_summary", "risk_clusters", "manifest", "warnings"):
        assert key in doc, key
    with pytest.raises(SystemExit):                          # eksik argüman → argparse hatası
        main(["--memory", str(mem)])
