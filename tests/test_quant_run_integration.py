"""Quant Evaluation V1 — `quant.run` tam zincir entegrasyonu (üretim şemalı, ağsız).

Zincir:
    gerçek TradeMemory + gerçek FuturesLedgerV2 outcome'ları + gerçek ShadowBook
    + gerçek futures_ledger.json (Risk V2 girdisi) + gerçek walk-forward fold raporu (kanıt)
    → quant.run.main → atomic rapor → dashboard StateReader → /api/quant/summary

Kritik ispatlar: kanıt köprüsü rapor içinde GERÇEKTEN çalışır (kanıt varsa değerlendirir, yoksa
KEEP_CHAMPION), senaryolar/coverage/risk V2 rapora akar, dashboard aynı sayıları döndürür,
bozuk/eski/eksik/eski-şema raporda 500 yoktur ve mutasyon metotları 405'tir.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from tradingbot.accounting import FuturesLedgerV2, SizeSpec, SlippageModel, TickData
from tradingbot.core import read_json
from tradingbot.dashboard.app import DashboardConfig, create_app
from tradingbot.learn.memory import TradeMemory
from tradingbot.learn.shadow import ShadowBook
from tradingbot.quant.attribution import group_metrics
from tradingbot.quant.champion import KEEP_CHAMPION, PROMOTE_CANDIDATE
from tradingbot.quant.eligibility import (SymbolEligibility, build_artifact, write_artifact)
from tradingbot.quant.run import main as quant_main
from tradingbot.quant.walkforward import assign_rows, fold_report, make_folds

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
T0_MS = int(T0.timestamp() * 1000)
DAY = 86_400_000


def _closed_trades(n: int) -> list[dict]:
    """TEK gerçek ledger üzerinde sırayla aç/kapa → benzersiz üretim `trade_id`'leri (F00001…)."""
    led = FuturesLedgerV2(50_000, slippage=SlippageModel(fixed_bps=D(3)), max_positions=10)
    out = []
    for i in range(n):
        win = i % 3 != 0
        sym = "ETH/USDT" if i % 2 else "SOL/USDT"
        t_open = T0 + timedelta(hours=4 * i)
        led.open(sym, "LONG", D(100), SizeSpec(D(300), leverage=3), stop=D(95),
                 targets=[D(104), D(110)], now=t_open)
        tick = TickData(last=D(111), high=D(112), low=D(99)) if win else \
            TickData(last=D(94), high=D(100.5), low=D(93.5))
        assert led.tick({sym: tick}, now_utc=t_open + timedelta(hours=4))
        out.append(led.history_dicts()[-1])
    assert len({r["id"] for r in out}) == n, "üretim ledger'ı benzersiz trade_id üretmeli"
    return out


def _write_inputs(tmp_path: Path, n=24):
    mem_path = tmp_path / "trade_memory.jsonl"
    memory = TradeMemory(mem_path, source="LIVE_PAPER")
    for i, rec in enumerate(_closed_trades(n)):
        win = i % 3 != 0
        tid = memory.record_entry({
            "trade_id": rec["id"], "symbol": rec["symbol"], "market_type": "futures",
            "side": "LONG", "timeframe": "4h", "decision_ts": rec["opened_at"],
            "p_win": 0.6 if win else 0.45, "regime": "trend_up" if i % 2 else "range",
            "values": {"atr_pct": 1.2, "quote_volume": 750_000.0},
            "agents": {"trend": 0.7, "meanrev": -0.2},
            "plan": {"plan_id": f"p{i}", "entry": 100.0, "stop": 95.0,
                     "targets": [104.0, 110.0], "leverage": 3, "notional": 300.0}})
        memory.record_exit(tid, rec)
    shadow_path = tmp_path / "shadow_book.json"
    book = ShadowBook(shadow_path)
    book.add({"plan_id": "px", "symbol": "BTC/USDT", "direction": "LONG", "entry": 100.0,
              "stop": 95.0, "targets": [104.0], "market_type": "futures", "leverage": 2},
             ["RISK_BUDGET_EXCEEDED"], now=T0)
    ledger_path = tmp_path / "futures_ledger.json"
    live = FuturesLedgerV2(5000, slippage=SlippageModel.zero(), max_positions=10)
    for sym, lev in (("AAVE/USDT", 3), ("ETH/USDT", 2), ("LDO/USDT", 3)):
        live.open(sym, "LONG", D(100), SizeSpec(D(300), leverage=lev), stop=D(95), now=T0)
    live.save(ledger_path)
    base = [1.0, -0.5, 2.0, -1.0, 0.5, 1.5, -0.8, 0.9, -0.2, 1.1] * 3
    returns_path = tmp_path / "returns.json"
    returns_path.write_text(json.dumps({"AAVE/USDT": base, "ETH/USDT": [x * 1.1 for x in base],
                                        "LDO/USDT": [x * 0.9 for x in base]}), encoding="utf-8")
    return mem_path, shadow_path, ledger_path, returns_path


def _wf_evidence(tmp_path: Path, *, complete=True) -> Path:
    """GERÇEK walk-forward fold raporundan kanıt paketi girdisi."""
    plan = make_folds(T0_MS, T0_MS + 200 * DAY, train_days=30, test_days=10, tf="4h",
                      holdout_days=20)
    rows = []
    for w in plan["folds"]:
        for i in range(1, 16):
            rows.append({"ts_ms": w.train_start + i * DAY, "as_of_ms": w.train_start + i * DAY - 1000,
                         "symbol": "ETH/USDT" if i % 2 else "SOL/USDT",
                         "regime": "trend_up" if i % 3 else "range",
                         "r_multiple": 1.0 if i % 3 else -0.8, "net_pnl": 30.0 if i % 3 else -24.0,
                         "outcome_labeled": True, "is_counterfactual": False, "quality_flags": []})
        for i in range(1, 12):
            rows.append({"ts_ms": w.test_start + i * 6 * 3_600_000,
                         "as_of_ms": w.test_start + i * 6 * 3_600_000 - 1000,
                         "symbol": "ETH/USDT" if i % 2 else "SOL/USDT",
                         "regime": "trend_up" if i % 2 else "range",
                         "r_multiple": 0.6 if i % 4 else -0.4, "net_pnl": 18.0 if i % 4 else -12.0,
                         "outcome_labeled": True, "is_counterfactual": False, "quality_flags": []})
    asg = assign_rows(rows, plan)
    wf = fold_report(asg, plan, lambda rs: group_metrics(rs, min_sample=5, seed=7))
    doc = {"walk_forward": wf,
           "challenger_metrics": {"n": 250, "insufficient_sample": False, "expectancy_r": 0.30,
                                  "max_drawdown_r": -2.0, "tail_loss_r_cvar5": -1.0,
                                  "bootstrap_ci_mean_r": {"state": "ok", "low": 0.1, "high": 0.5},
                                  "concentration": {"top_symbol_share": 0.3, "top_trade_share": 0.1}},
           "champion_metrics": {"n": 250, "insufficient_sample": False, "expectancy_r": 0.05,
                                "max_drawdown_r": -3.0, "tail_loss_r_cvar5": -1.5,
                                "bootstrap_ci_mean_r": {"state": "ok", "low": 0.0, "high": 0.2},
                                "concentration": {"top_symbol_share": 0.3, "top_trade_share": 0.1}}}
    if complete:
        doc |= {"leakage": {"passed": True, "n_violations": 0},
                "data_quality": {"passed": True, "verdict": "OK", "checks": []},
                "isolation": {"passed": True, "detail": "ana ledger/outbox/gateway dokunulmadı"},
                "cost_model_equal": True,
                "execution_quality": {"state": "MODELED", "provenance": "ohlcv_derived"}}
    p = tmp_path / "evidence.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# --------------------------------------------------------------- tam zincir

def test_full_chain_run_to_dashboard(tmp_path: Path):
    # n=40: journal coverage MIN_RECORDS (30) eşiğini gerçekten geçen bir defter.
    mem, shadow, ledger, returns = _write_inputs(tmp_path, n=40)
    ev = _wf_evidence(tmp_path)
    elig = write_artifact(tmp_path / "elig.json", build_artifact(
        [SymbolEligibility(symbol="ETH/USDT", market_type="USDM_PERP", as_of_ms=T0_MS,
                           trading_status="TRADING", tick_size=0.01, step_size=0.001,
                           min_qty=0.001, min_notional=5.0, source="test")],
        as_of_ms=T0_MS, source="test"))
    st = tmp_path / "state"
    out = st / "quant_eval.json"
    rc = quant_main(["--memory", str(mem), "--shadow", str(shadow), "--out", str(out),
                     "--allow-state-out", "--ledger", str(ledger), "--returns", str(returns),
                     "--evidence", str(ev), "--eligibility", str(elig),
                     "--run-id", "ci-1", "--code-sha", "audit2", "--min-sample", "5"])
    assert rc == 0
    doc = read_json(out)

    # --- kanıt köprüsü GERÇEKTEN çalıştı (artık her durumda kanıtsız KEEP değil)
    assert doc["champion_challenger"]["decision"] == PROMOTE_CANDIDATE
    assert doc["champion_challenger"]["auto_promotion"] is False
    assert doc["champion_challenger"]["applies_changes"] is False
    assert doc["evidence"]["complete"] is True and doc["evidence"]["missing_critical"] == []
    assert doc["evidence"]["expectancy_delta_r"] == pytest.approx(0.25)

    # --- senaryolar, coverage, risk V2, eligibility rapora aktı
    sc = doc["execution_scenarios"]
    assert set(sc["results"]) == {"base", "adverse", "stress"}
    assert sc["results"]["base"]["expectancy_r"] > sc["results"]["stress"]["expectancy_r"]
    assert doc["coverage"]["n_records"] == 41 and doc["coverage"]["gates_passed"] is True
    rv2 = doc["risk_v2"]
    assert rv2["n_clusters"] == 1 and rv2["applies_to_active_engine"] is False
    assert rv2["increases_risk"] is False
    assert doc["eligibility"]["n_snapshots"] == 1
    assert doc["backtest_status"] in ("VALID", "PARTIAL")
    assert doc["data_kind"] == "LIVE_PAPER_JOURNAL"

    # --- dashboard aynı dosyayı servis eder
    (tmp_path / "data").mkdir()
    c = TestClient(create_app(st, tmp_path / "data", None, DashboardConfig()))
    j = c.get("/api/quant/summary").json()
    assert j["available"] is True and j["report_stale"] is False
    assert j["champion_challenger"]["decision"] == PROMOTE_CANDIDATE
    assert j["execution_scenarios"]["results"]["stress"]["expectancy_r"] == \
        pytest.approx(sc["results"]["stress"]["expectancy_r"])
    assert j["coverage"]["gates_passed"] is True
    assert j["risk_v2"]["n_clusters"] == 1
    assert j["backtest_status"] == doc["backtest_status"]
    html = c.get("/quant").text
    assert "ADVISORY ONLY — ACTIVE RISK ENGINE UNCHANGED" in html
    assert "Execution senaryoları" in html and "Journal kapsaması" in html
    assert "kârlılık kanıtı DEĞİLDİR" in html


def test_missing_evidence_keeps_champion_end_to_end(tmp_path: Path):
    mem, shadow, ledger, returns = _write_inputs(tmp_path, n=12)
    out = tmp_path / "q.json"
    rc = quant_main(["--memory", str(mem), "--shadow", str(shadow), "--out", str(out),
                     "--ledger", str(ledger), "--returns", str(returns), "--min-sample", "5"])
    assert rc == 0
    doc = read_json(out)
    assert doc["champion_challenger"]["decision"] == KEEP_CHAMPION
    assert doc["evidence"]["complete"] is False
    assert doc["champion_challenger"]["missing_critical"]
    assert any("eligibility artifact verilmedi" in w for w in doc["warnings"])
    assert doc["backtest_status"] == "PARTIAL"


def test_incomplete_proofs_keep_champion(tmp_path: Path):
    mem, shadow, ledger, returns = _write_inputs(tmp_path, n=12)
    ev = _wf_evidence(tmp_path, complete=False)          # metrikler var, güvenlik kanıtı yok
    out = tmp_path / "q.json"
    assert quant_main(["--memory", str(mem), "--shadow", str(shadow), "--out", str(out),
                       "--evidence", str(ev), "--min-sample", "5"]) == 0
    doc = read_json(out)
    assert doc["champion_challenger"]["decision"] == KEEP_CHAMPION
    assert set(doc["evidence"]["missing_critical"]) >= {"leakage_passed", "isolation_verified"}


def test_report_is_deterministic_and_rfc_safe(tmp_path: Path):
    mem, shadow, ledger, returns = _write_inputs(tmp_path, n=12)
    ev = _wf_evidence(tmp_path)
    out = tmp_path / "q.json"
    args = ["--memory", str(mem), "--shadow", str(shadow), "--out", str(out),
            "--ledger", str(ledger), "--returns", str(returns), "--evidence", str(ev),
            "--seed", "7", "--min-sample", "5", "--now-ms", "1800000000000"]
    assert quant_main(args) == 0
    first = out.read_bytes()
    assert quant_main(args) == 0
    assert out.read_bytes() == first                     # byte-eşit tekrar
    dumped = json.dumps(read_json(out), allow_nan=False)
    assert "NaN" not in dumped and "Infinity" not in dumped


def test_inputs_are_never_modified(tmp_path: Path):
    mem, shadow, ledger, returns = _write_inputs(tmp_path, n=12)
    before = {p: p.read_bytes() for p in (mem, shadow, ledger, returns)}
    assert quant_main(["--memory", str(mem), "--shadow", str(shadow),
                       "--out", str(tmp_path / "q.json"), "--ledger", str(ledger),
                       "--returns", str(returns)]) == 0
    for p, b in before.items():
        assert p.read_bytes() == b, p                    # SALT OKUNUR


# --------------------------------------------------------------- dashboard dayanıklılığı

def test_dashboard_stale_legacy_and_broken_reports(tmp_path: Path):
    st, data = tmp_path / "state", tmp_path / "data"
    st.mkdir(), data.mkdir()
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    # 1) eski şema raporu (yeni alanların hiçbiri yok)
    (st / "quant_eval.json").write_text(json.dumps(
        {"schema_version": "quant_eval_v1", "journal": {"n_records": 3, "n_labeled": 1},
         "overall": {"n": 3, "insufficient_sample": True}}), encoding="utf-8")
    r = c.get("/api/quant/summary")
    assert r.status_code == 200 and r.json()["available"] is True
    assert r.json()["execution_scenarios"] is None and r.json()["coverage"] is None
    assert c.get("/quant").status_code == 200
    # 2) non-finite değerler
    (st / "quant_eval.json").write_text(
        '{"schema_version":"quant_eval_v1","overall":{"expectancy_r":NaN,"n":5},'
        '"execution_scenarios":{"results":{"base":{"expectancy_r":Infinity,"n":5}}}}',
        encoding="utf-8")
    r2 = c.get("/api/quant/summary")
    assert r2.status_code == 200 and "NaN" not in r2.text and "Infinity" not in r2.text
    assert r2.json()["overall"]["expectancy_r"] is None
    assert r2.json()["overall"]["n"] == 5
    assert c.get("/quant").status_code == 200
    # 3) bozuk dosya
    (st / "quant_eval.json").write_text('{"schema_version": "quant', encoding="utf-8")
    r3 = c.get("/api/quant/summary")
    assert r3.status_code == 200 and r3.json()["available"] is False
    assert c.get("/quant").status_code == 200
    # 4) mutasyon metotları
    for m in ("post", "put", "patch", "delete"):
        assert getattr(c, m)("/api/quant/summary").status_code == 405
        assert getattr(c, m)("/quant").status_code == 405
