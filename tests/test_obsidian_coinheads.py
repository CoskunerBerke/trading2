"""ObsidianCoinHeadWriter testleri — md + canvas, deterministik id'ler, gruplar, kenarlar, dondurulmuş işlem notu,
olay cap/arşiv, yalnızca Coin Heads/ budama, değişmeyen içerikte yazmama."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from tradingbot.obsidian_coinheads import INCIDENT_CAP, OWNED_DIRS, ObsidianCoinHeadWriter, safe_base


def _report(name: str, group: str, bias: float, veto: bool = False, metrics: dict | None = None) -> dict:
    return {"agent_name": name, "factor_group": group, "stance": "BULL" if bias > 0.15 else ("BEAR" if bias < -0.15 else "NEUTRAL"),
            "bias": bias, "confidence_raw": 60, "evidence_for": ["EMA25 > EMA99"] if bias > 0 else [], "evidence_against": ["RSI 72"] if bias <= 0 else [],
            "warnings": ["Bollinger sıkışması"] if group == "volatility" else [], "metrics": metrics or {}, "levels": {"s1": 100.0, "r1": 120.0},
            "veto": veto, "veto_reason": "spread çok geniş" if veto else "", "error": ""}


def _decision(n_agents: int = 3, veto: bool = False) -> dict:
    groups = ["trend", "momentum", "volatility", "volume_flow", "liquidity", "historical_edge"]
    reps = [_report(f"agent{i}", groups[i % len(groups)], 0.4 if i % 2 == 0 else -0.2, veto=(veto and i == 0),
                    metrics={"ema25": 110.0, "ema99": 105.0, "ema200": 100.0, "rsi14": 61.2, "atr_pct": 1.4, "funding_pct": 0.01, "price": 111.0}) for i in range(n_agents)]
    return {"coin_head_id": "ch1", "run_id": "run_1", "snapshot_id": "snap_1", "symbol": "SOL/USDT", "market_type": "futures", "regime": "TREND_UP",
            "verdict": "FUTURES_LONG", "no_trade_reason": "", "direction": "LONG", "confidence_raw": 65, "confidence_calibrated": 0.61, "p_win": 0.58,
            "expected_return_gross": 2.5, "expected_cost": 0.3, "expected_return_net": 2.2, "expected_r": 1.9, "expected_shortfall": -1.4,
            "entry_trigger": "4h kapanış > 112", "entry_zone": [111.0, 112.5], "invalidation": "108 altı kapanış", "stop": 107.0, "targets": [116.0, 120.0],
            "time_horizon": 12, "position_size": 0.5, "margin": 10.0, "notional": 55.0, "leverage": 5,
            "consensus": {"trend": 0.5, "momentum": 0.3}, "dissent": ["momentum ajanı zayıf"], "vetoes": ["spread çok geniş"] if veto else [],
            "model_versions": {"pwin": "v1"}, "data_freshness": {"candles_4h": "12s"}, "expires_at": "2026-08-18T12:00:00+00:00",
            "spot_plan": {"market_type": "spot", "direction": "LONG", "entry_type": "pullback", "entry_trigger": "x", "entry_zone": [111, 112], "entry": 111.5, "stop": 107.0,
                          "stop_pct": 4.03, "targets": [116.0], "size": {"amount": 0.4, "amount_type": "base", "leverage": 1}, "margin": 0, "notional": 44.6,
                          "expected_cost_pct": 0.2, "expected_r": 1.1, "valid": True, "invalid_reason": "", "time_horizon_bars": 12, "invalidation": ""},
            "futures_plan": {"market_type": "futures", "direction": "LONG", "entry_type": "breakout", "entry_trigger": "y", "entry_zone": [111, 112.5], "entry": 111.75,
                             "stop": 107.0, "stop_pct": 4.25, "targets": [116.0, 120.0], "size": {"amount": 55, "amount_type": "quote", "leverage": 5}, "margin": 11.0,
                             "notional": 55.0, "expected_cost_pct": 0.35, "expected_r": 1.9, "valid": True, "invalid_reason": "", "time_horizon_bars": 12, "invalidation": "108 altı"},
            "factor_scores": [{"group": "trend", "score": 0.5, "confidence": 0.7, "data_quality": 1.0, "n_independent": 2, "conflict": 0.1}],
            "specialist_reports": reps, "generated_at": "2026-08-18T09:00:00+00:00", "latency_ms": 12.0}


def test_md_and_canvas_written(tmp_path: Path):
    w = ObsidianCoinHeadWriter(tmp_path)
    md = w.write_coin_head(_decision(3, veto=True), brief={"price": 111.0}, chart_rel="Charts/SOL.png")
    assert md == tmp_path / "Coin Heads" / "SOL.md" and md.exists()
    txt = md.read_text(encoding="utf-8")
    for sec in ("## 📸 Snapshot", "## 🤖 UZMAN AJANLAR", "## 🧭 COIN HEAD KARARI", "## 🟢 SPOT PLANI", "## ⚡ FUTURES PLANI", "## 🔴 RED TEAM",
                "## 🛡️ RİSK", "## 💸 MALİYET TAHMİNİ", "## 📜 SON İŞLEMLER", "## 🎓 ÖĞRENİLEN DERSLER", "## 🧪 MODEL / VERİ TAZELİĞİ"):
        assert sec in txt, sec
    assert txt.startswith("---\nsymbol: SOL/USDT\nbase: SOL\nverdict: FUTURES_LONG")
    assert "updated_local:" in txt and "run_id: run_1" in txt and "tags: [coin-head]" in txt
    assert "| EMA25 |" in txt and "| RSI |" in txt and "| Funding |" in txt and "| Destek |" in txt
    assert "[[Coins/SOL]]" in txt and "[[Agents/SOL]]" in txt and "[[Portfolio/Futures]]" in txt and "[[Runs/2026-08-18]]" in txt
    assert "![[Charts/SOL.png]]" in txt and "spread çok geniş" in txt
    canvas = json.loads((tmp_path / "Coin Heads" / "SOL.canvas").read_text(encoding="utf-8"))
    ids = {n["id"] for n in canvas["nodes"]}
    for role in ("head", "spot_plan", "fut_plan", "red_team", "risk_engine", "chief", "paper_exec", "trade_result", "learning",
                 "grp_specialists", "grp_decision", "grp_control", "grp_execution", "chart", "ag_agent0"):
        assert f"SOL:{role}" in ids, role
    groups = [n for n in canvas["nodes"] if n["type"] == "group"]
    assert len(groups) == 4 and all(n.get("label") for n in groups)
    # kenarlar geçerli düğümlere işaret eder ve id biçimi doğru
    for e in canvas["edges"]:
        assert e["fromNode"] in ids and e["toNode"] in ids
        assert e["id"] == f"{e['fromNode']}->{e['toNode'].split(':', 1)[1]}"
    # ızgara: sütun×560
    head = next(n for n in canvas["nodes"] if n["id"] == "SOL:head")
    assert head["x"] == 560 and head["y"] % 190 == 0
    assert next(n for n in canvas["nodes"] if n["id"] == "SOL:learning")["x"] == 8 * 560


def test_deterministic_ids_across_agent_counts_and_skip_unchanged(tmp_path: Path):
    w = ObsidianCoinHeadWriter(tmp_path)
    w.write_coin_head(_decision(3))
    c1 = json.loads((tmp_path / "Coin Heads" / "SOL.canvas").read_text(encoding="utf-8"))
    p = tmp_path / "Coin Heads" / "SOL.md"
    m1 = p.stat().st_mtime_ns
    # aynı karar tekrar → içerik aynı, dosyaya dokunulmaz
    time.sleep(0.02)
    assert w._write(p, p.read_text(encoding="utf-8")) is False
    assert p.stat().st_mtime_ns == m1
    w.write_coin_head(_decision(6))
    c2 = json.loads((tmp_path / "Coin Heads" / "SOL.canvas").read_text(encoding="utf-8"))
    ids1 = {n["id"] for n in c1["nodes"]}
    ids2 = {n["id"] for n in c2["nodes"]}
    fixed = {f"SOL:{r}" for r in ("head", "spot_plan", "fut_plan", "red_team", "risk_engine", "chief", "paper_exec", "trade_result", "learning")}
    assert fixed <= ids1 and fixed <= ids2
    pos1 = {n["id"]: (n["x"], n["y"]) for n in c1["nodes"] if n["id"] in fixed}
    pos2 = {n["id"]: (n["x"], n["y"]) for n in c2["nodes"] if n["id"] in fixed}
    assert all(pos1[k][0] == pos2[k][0] for k in fixed), "sütunlar sabit"
    assert {i for i in ids2 if ":ag_" in i} > {i for i in ids1 if ":ag_" in i}
    e1 = {e["id"] for e in c1["edges"]}
    e2 = {e["id"] for e in c2["edges"]}
    assert "SOL:head->spot_plan" in e1 and "SOL:head->spot_plan" in e2 and "SOL:ag_agent0->head" in e1 & e2


def test_trade_note_frozen_after_close(tmp_path: Path):
    w = ObsidianCoinHeadWriter(tmp_path)
    t = {"id": "pos_1", "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0, "opened_at": "2026-08-18T01:00:00+00:00", "status": "OPEN", "leverage": 3}
    p = w.write_trade(t)
    assert p == tmp_path / "Trades" / "pos_1.md" and "status: OPEN" in p.read_text(encoding="utf-8")
    t2 = {**t, "status": "CLOSED", "closed_at": "2026-08-18T05:00:00+00:00", "exit_price": 110.0, "pnl": 3.0, "r_multiple": 1.5, "exit_reason": "TP1",
          "costs": {"fee": 0.1, "funding": 0.02}}
    w.write_trade(t2, postmortem={"lesson": "iyi giriş", "tags": ["a", "b"]})
    txt = p.read_text(encoding="utf-8")
    assert "status: CLOSED" in txt and "iyi giriş" in txt and "TP1" in txt
    # kapanmış → yeniden yazılmaz
    w.write_trade({**t2, "pnl": 999.0}, postmortem={"lesson": "DEĞİŞTİ"})
    assert "DEĞİŞTİ" not in p.read_text(encoding="utf-8") and "999" not in p.read_text(encoding="utf-8")


def test_incidents_cap_and_archive(tmp_path: Path):
    w = ObsidianCoinHeadWriter(tmp_path)
    for i in range(INCIDENT_CAP + 5):
        w.append_incident({"kind": "TEST", "severity": "warning", "text": f"olay {i}", "ts": "2026-08-18T00:00:00+00:00"})
    p = tmp_path / "Operations" / "Incidents.md"
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
    assert len(lines) == INCIDENT_CAP and "olay 5" in lines[0] and f"olay {INCIDENT_CAP + 4}" in lines[-1]
    arch = tmp_path / "Operations" / "Incidents Archive 2026-08.md"
    alines = [ln for ln in arch.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
    assert len(alines) == 5 and "olay 0" in alines[0] and "olay 4" in alines[-1]


def test_prune_only_coin_heads(tmp_path: Path):
    w = ObsidianCoinHeadWriter(tmp_path)
    w.write_coin_head(_decision(2))
    d = _decision(2); d["symbol"] = "ETH/USDT"; w.write_coin_head(d)
    w.write_trade({"id": "t1", "symbol": "ETH/USDT", "side": "LONG", "status": "OPEN"})
    (tmp_path / "Agents").mkdir(); (tmp_path / "Agents" / "ETH.md").write_text("eski", encoding="utf-8")
    old = time.time() - 72 * 3600
    for f in (tmp_path / "Coin Heads" / "ETH.md", tmp_path / "Coin Heads" / "ETH.canvas", tmp_path / "Trades" / "t1.md", tmp_path / "Agents" / "ETH.md"):
        os.utime(f, (old, old))
    removed = w.prune_stale(["SOL"], older_than_hours=48)
    assert sorted(x.name for x in removed) == ["ETH.canvas", "ETH.md"]
    assert (tmp_path / "Trades" / "t1.md").exists() and (tmp_path / "Agents" / "ETH.md").exists()
    assert (tmp_path / "Coin Heads" / "SOL.md").exists()
    # aktif ama eski → korunur
    os.utime(tmp_path / "Coin Heads" / "SOL.md", (old, old))
    assert w.prune_stale(["SOL"], older_than_hours=48) == []


def test_other_writers_and_owned_dirs(tmp_path: Path):
    w = ObsidianCoinHeadWriter(tmp_path)
    w.write_portfolio({"cash": 50.0}, {"equity": 49.5, "total_fees": 0.1}, [{"symbol": "BTC/USDT", "side": "LONG", "qty": 0.001, "entry_avg": 60000, "leverage": 3, "isolated_margin": 20, "notional": 60, "stop": 58000, "liquidation_price": 41000, "fees_paid": 0.02, "funding_paid": 0.01, "funding_received": 0}])
    w.append_run_event("tour", "tur tamamlandı", run_id="run_1", ts="2026-08-18T09:15:00+00:00")
    w.append_run_event("tour", "ikinci", run_id="run_2", ts="2026-08-18T13:15:00+00:00")
    w.write_risk({"profile": {"max_open_positions": 3}, "exposure": {"equity": 50, "positions": [{"symbol": "BTC/USDT", "market_type": "futures", "side": "LONG", "notional": 60}]}},
                 {"state": "HALT_ENTRIES", "since": "2026-08-18T08:00:00+00:00", "reasons": [{"code": "DAILY_LOSS", "detail": "-3%", "ts": "x"}], "audit": []})
    w.write_models({"models": [{"id": "pwin_v1", "kind": "logreg", "status": "active", "metrics": {"auc": 0.61}, "created_at": "2026-08-01"}]})
    w.write_health({"state": "DEGRADED", "summary": "DEGRADED · heartbeat", "checks": [{"name": "heartbeat", "ok": False, "detail": {"age_s": 999}}], "generated_at": "2026-08-18T09:00:00+00:00"})
    w.write_data_quality({"feeds": [{"name": "binance_rest", "ok": True, "age_s": 3}], "generated_at": "2026-08-18T09:00:00+00:00"})
    w.write_universe({"generated_at": "2026-08-18T09:00:00+00:00", "spot": ["BTC/USDT", "CON/USDT"], "futures": ["BTC/USDT:USDT"], "counts": {"spot": 2, "futures": 1}})
    runs = (tmp_path / "Runs" / "2026-08-18.md").read_text(encoding="utf-8")
    assert runs.count("- `") == 2 and "run_2" in runs
    assert "HALT_ENTRIES" in (tmp_path / "Risk" / "Kill Switch.md").read_text(encoding="utf-8")
    assert "pwin_v1" in (tmp_path / "Models" / "Registry.md").read_text(encoding="utf-8")
    assert "DEGRADED" in (tmp_path / "Operations" / "Health.md").read_text(encoding="utf-8")
    assert "binance_rest" in (tmp_path / "Data Quality" / "Feeds.md").read_text(encoding="utf-8")
    assert "[[Coin Heads/CON_|CON/USDT]]" in (tmp_path / "Data Quality" / "Universe.md").read_text(encoding="utf-8")
    assert "41,000" in (tmp_path / "Portfolio" / "Futures.md").read_text(encoding="utf-8")
    top = {p.name for p in tmp_path.iterdir()}
    assert top <= set(OWNED_DIRS), top
    assert safe_base("con/usdt") == "CON_" and safe_base("BTC/USDT") == "BTC" and safe_base("A<B") == "A_B"


def test_trade_note_chain_links_and_frozen_probe(tmp_path: Path):
    """İşlem notu Ders/Model/Portföy zincir bağlantılarını taşır; `trade_note_frozen` yalnız kapanmış notta True."""
    w = ObsidianCoinHeadWriter(tmp_path)
    t = {"id": "pos_9", "symbol": "SUI/USDT", "side": "SHORT", "entry": 0.65, "opened_at": "2026-08-18T16:48:57+00:00", "status": "OPEN"}
    assert w.trade_note_path("pos_9") == tmp_path / "Trades" / "pos_9.md"
    assert not w.trade_note_frozen("pos_9")          # not yok
    w.write_trade(t)
    assert not w.trade_note_frozen("pos_9")          # açık not dondurulmuş sayılmaz
    w.write_trade({**t, "status": "CLOSED", "closed_at": "2026-08-19T15:06:15+00:00", "exit_price": 0.6831,
                   "net_pnl": -0.7798, "r_multiple": -1.2183, "exit_reason": "stop"},
                  postmortem={"lesson_text_tr": ["ZARAR (-1.22R): stop."], "postmortem_version": 1})
    txt = w.trade_note_path("pos_9").read_text(encoding="utf-8")
    assert w.trade_note_frozen("pos_9")
    for link in ("[[Learning/Dersler]]", "[[Learning/Öğrenme]]", "[[Models/Registry]]", "[[Portfolio/Futures]]", "[[Coin Heads/SUI]]"):
        assert link in txt, link
    assert "ZARAR (-1.22R): stop." in txt
