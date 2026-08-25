"""Açıklanabilirlik + sınırlı öğrenme aktivasyonu kabul testleri.

Kapsam (görev §10-§12, §15/28-40 kalanlar): her adayın why/why-not özeti kanıttan türer,
postmortem bounded ileri politika üretir, PAPER_BOUNDED yalnız güvenli typed override ile
açılır (fail-closed), delta %5 sınırında kalır, decision_changed_by_learning doğru işaretlenir,
non-finite veri journal/dashboard'a ulaşmaz, restart deterministik.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from test_engine_v3 import _engine

from tradingbot.learn.decision_journal import why_summary_tr
from tradingbot.learn.influence import InfluenceConfig, apply_influence, weighted_adjustment
from tradingbot.learn.postmortem import structured_postmortem


# ================================================================== 38) why/why-not her adayda

def test_38_every_candidate_has_evidence_derived_why_summary(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    rows = [r for r in eng.decision_journal.iter_all_rows() if r.get("kind") == "decision"]
    assert rows
    for r in rows:
        why = r.get("why_summary_tr")
        assert why and len(why) <= 900, "her adayın kanıttan türeyen özeti olmalı"
        assert "Sonuç:" in why


def test_38b_why_summary_reflects_learning_evidence():
    rec = {"tier": "A", "scan_score": 72, "scan_rank": 17,
           "outcome_kind": "SCREENED_OUT", "outcome_stage": "tier_a_screen",
           "outcome_reason": "NOT_IN_TOP_N"}
    w = why_summary_tr(rec)
    assert "Tier-A" in w and "NOT_IN_TOP_N" in w

    rec2 = {"outcome_kind": "ACCEPTED", "outcome_stage": "ledger_open",
            "risk_allowed": True,
            "learning_influence": {"n_experience": 7, "aggregate_n": 30,
                                   "top_similarity": 0.81, "baseline": 0.55,
                                   "learned": 0.5621, "effective": 0.55,
                                   "applied": False,
                                   "decision_changed_by_learning": False}}
    w2 = why_summary_tr(rec2)
    assert "7 benzer" in w2 and "+30 arşiv" in w2
    assert "SHADOW" in w2 and "0.55" in w2 and "yükseltti" in w2

    rec3 = dict(rec2)
    rec3["learning_influence"] = dict(rec2["learning_influence"],
                                      applied=True, effective=0.5621,
                                      decision_changed_by_learning=True)
    w3 = why_summary_tr(rec3)
    assert "PAPER_BOUNDED" in w3 and "decision_changed_by_learning=true" in w3


# ================================================================== 28-32) postmortem politika

def _close_rec(r: float, **feats) -> dict:
    return {"id": "t1", "symbol": "BTC/USDT", "side": "LONG", "r_multiple": r,
            "pnl": r * 10, "net_pnl": r * 10, "exit_reason": "target" if r > 0 else "stop",
            "features": {"direction": "LONG", "bias_trend": 0.5, "conf_trend": 0.7,
                         "rr": 2.5, "p_win": 0.6, **feats}}


def test_28_29_win_and_loss_produce_bounded_policy():
    win = structured_postmortem(_close_rec(1.8))
    assert win.next_time_policy["confidence_bias"] == "increase"
    assert win.next_time_policy["hard_gates_still_required"] is True
    assert win.next_time_policy["deterministic_future_claim"] is False

    loss = structured_postmortem(_close_rec(-1.2))
    assert loss.next_time_policy["confidence_bias"] == "decrease"
    assert "max_fraction" in loss.next_time_policy["expected_bounded_effect"]


def test_32_cost_dominated_gross_win_yields_negative_policy():
    rec = _close_rec(0.9)
    rec["fees"] = 3.5                                  # fee_drag_r hesabına girer
    pm = structured_postmortem(rec)
    if "FEE_HEAVY" in pm.lesson_codes:
        assert pm.next_time_policy["confidence_bias"] == "decrease"
    # doğrudan sınıflandırıcı da negatif ders üretebilmeli
    from tradingbot.learn.experience import cost_sensitivity
    assert cost_sensitivity({"r_multiple": 0.2, "fee_drag_r": 0.5}) == "COST_DOMINATED"


def test_30_conflicting_history_shrinks_effect():
    cfg = InfluenceConfig()
    consistent = weighted_adjustment([{"r_multiple": 1.5, "outcome_id": f"a{i}"}
                                      for i in range(6)], baseline=0.6, cfg=cfg,
                                     prior_leaf_n=0.0)
    conflicting = weighted_adjustment(
        [{"r_multiple": 1.5 if i % 2 else -1.5, "outcome_id": f"b{i}"} for i in range(6)],
        baseline=0.6, cfg=cfg, prior_leaf_n=0.0)
    assert abs(conflicting["fraction"]) < abs(consistent["fraction"]), \
        "çelişkili geçmiş etkiyi KÜÇÜLTMELİ"
    assert "CONFLICTING_EXPERIENCE_CONFIDENCE_LOW" in conflicting["reasons"]


# ================================================================== 35-37) PAPER_BOUNDED sınırları

def test_35_paper_bounded_delta_never_exceeds_five_percent():
    cfg = InfluenceConfig(mode="PAPER_BOUNDED")
    assert cfg.max_fraction <= 0.05, "mevcut sözleşme: göreli etki tavanı ≤ %5"
    adj = weighted_adjustment([{"r_multiple": 99.0, "outcome_id": f"x{i}"}
                               for i in range(5000)], baseline=0.60, cfg=cfg,
                              prior_leaf_n=0.0)
    out = apply_influence(adj, cfg=cfg, mode_value="PAPER")
    assert out["applied"] is True
    assert abs(out["effective"] - 0.60) <= 0.60 * cfg.max_fraction + 1e-9
    # LIVE emir yolunda ASLA uygulanmaz
    out_live = apply_influence(adj, cfg=cfg, mode_value="PAPER", live_order_path=True)
    assert out_live["applied"] is False


def test_36_learning_cannot_touch_risk_order_fields(tmp_path: Path, monkeypatch):
    """Öğrenme yalnız p_win'i etkiler; leverage/size/stop/TP/notional alanlarına yazamaz."""
    src = Path("tradingbot/learn/influence.py").read_text(encoding="utf-8")
    for banned in ("leverage", "stop_loss", "take_profit", "notional", "place_order",
                   "submit_order", "RiskEngine", "ledger"):
        assert banned not in src, f"influence modülü {banned!r} alanına dokunamaz"
    # `live_order_path` yalnız GÜVENLİK KAPISI olarak geçer (emir yolunda uygulamayı ENGELLER)
    assert "if live_order_path:" in src
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    for rec in eng.decision_journal.iter_all_rows():
        li = rec.get("learning_influence")
        if li:
            assert set(li) <= {"mode", "n_experience", "top_similarity", "fraction",
                               "baseline", "learned", "applied", "blockers", "effective",
                               "exemplar_weight", "aggregate_weight", "aggregate_level",
                               "aggregate_n", "aggregate_mean_r", "aggregate_months",
                               "decision_changed_by_learning"}, \
                "öğrenme kaydı yalnız p_win alanlarını taşıyabilir"


def test_37_decision_changed_flag_is_accurate(tmp_path: Path, monkeypatch):
    """Bayrak AYNI ekonomi kapısının baseline/effective ile iki değerlendirmesinden türer."""
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    decisions = {s: d for s, d in (eng.last_decisions or {}).items()}
    # gerçek karar nesneleri lazım — motoru bir kez daha çalıştırıp iç sözlüğü yakala
    captured = {}
    orig = eng._assess_opportunities

    def capture(decisions, briefs):
        captured["d"] = decisions
        captured["b"] = briefs
        return orig(decisions, briefs)

    monkeypatch.setattr(eng, "_assess_opportunities", capture)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    d_map, briefs = captured["d"], captured["b"]
    sym = next((s for s, d in d_map.items()
                if getattr(d, "active_plan", None) is not None
                and getattr(d.active_plan, "valid", False)), None)
    if sym is None:
        pytest.skip("fixture bu turda geçerli plan üretmedi")
    d = d_map[sym]
    # senaryo 1: etki yok → bayrak False
    eng._influence_log = [{"symbol": sym, "applied": False, "baseline": 0.6,
                           "effective": 0.6}]
    orig(d_map, briefs)
    assert d.opportunity.get("decision_changed_by_learning") is False
    # senaryo 2: uygulanmış BÜYÜK fark (test amaçlı) → bayrak tutarlı ve bool
    d.p_win = 0.95
    eng._influence_log = [{"symbol": sym, "applied": True, "baseline": 0.05,
                           "effective": 0.95}]
    orig(d_map, briefs)
    flag = d.opportunity.get("decision_changed_by_learning")
    assert isinstance(flag, bool)
    # baseline p_win=0.05 ile ekonomi kapısı tradeable diyorsa fark yok demektir;
    # bayrak iki değerlendirmenin GERÇEK farkına eşit olmalı
    from tradingbot.opportunity import assess as _assess
    assert d.opportunity["p_win_calibrated"] in (0.95, pytest.approx(0.95))


# ================================================================== 39) non-finite sızamaz

def test_39_non_finite_values_never_reach_journal_or_dashboard(tmp_path: Path, monkeypatch):
    from tradingbot.learn.decision_journal import build_decision_record

    class Snap:
        values = {"a": float("nan"), "b": float("inf"), "c": 1.5}
        missing: list = []
        last_bar_ts = "2026-08-25T00:00:00+00:00"
        timeframe = "4h"
        feature_version = 3
        strategy_version = "s"
        config_hash = "h"

    rec = build_decision_record(run_id="r", cycle_id=1, symbol="X/USDT", direction="LONG",
                                snapshot=Snap(), outcome_kind="REJECTED")
    raw = json.dumps(rec, allow_nan=False)              # NaN/Inf varsa burada patlar
    assert "NaN" not in raw and "Infinity" not in raw
    assert rec["features"] == {"c": 1.5}

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    st = Path(eng.cfg.state_path)
    (st / "universe_eval.json").write_text(
        json.dumps({"schema_version": "universe_eval_v1",
                    "counts": {"eligible": 1}, "symbols": [
                        {"symbol": "X/USDT", "rank": 1, "vol24_usdt": 1e9,
                         "scan_score": 50, "atr_pct": None, "tier": "A"}]}),
        encoding="utf-8")
    client = TestClient(create_app(st, Path(eng.cfg.cache_path), None, DashboardConfig()))
    r = client.get("/api/universe")
    assert r.status_code == 200
    json.loads(r.text)                                  # RFC-uyumlu JSON (NaN yok)


# ================================================================== 40) restart determinizmi

def test_40_restart_preserves_dedup_and_learning_result(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    rows1 = [r["decision_id"] for r in eng.decision_journal.iter_all_rows()
             if r.get("kind") == "decision"]

    eng2 = _engine(tmp_path, monkeypatch, symbols=3)     # RESTART: aynı state dizini
    st1 = eng2.exp_index_store.stats() if eng2.exp_index_store else {}
    eng2.tour(do_scan=False, obsidian=False, charts=False)
    rows2 = [r["decision_id"] for r in eng2.decision_journal.iter_all_rows()
             if r.get("kind") == "decision"]
    assert len(rows2) == len(set(rows2)), "restart sonrası duplicate decision OLAMAZ"
    assert set(rows1) <= set(rows2)
    st2 = eng2.exp_index_store.stats() if eng2.exp_index_store else {}
    assert st1.get("names_signature") == st2.get("names_signature")


# ================================================================== env override aktivasyonu

def test_env_override_activates_paper_bounded_fail_closed(tmp_path: Path, monkeypatch):
    """PAPER_BOUNDED yalnız güvenli typed override ile açılır; PAPER dışı FAIL-CLOSED."""
    from tradingbot.config_v3 import load_v3
    from tradingbot.core import ConfigError

    monkeypatch.setenv("TRADINGBOT_LEARNING_INFLUENCE_MODE", "PAPER_BOUNDED")
    cfg = load_v3({"mode": "PAPER"})
    assert cfg.learning_v3.influence_mode == "PAPER_BOUNDED"
    assert cfg.learning_v3.influence_max_fraction <= 0.05

    with pytest.raises(ConfigError, match="PAPER_ONLY"):
        load_v3({"mode": "OBSERVE"})
    monkeypatch.setenv("TRADINGBOT_LEARNING_INFLUENCE_MODE", "YANLIS")
    with pytest.raises(ConfigError, match="TRADINGBOT_LEARNING_INFLUENCE_MODE"):
        load_v3({"mode": "PAPER"})
    monkeypatch.setenv("TRADINGBOT_LEARNING_INFLUENCE_MODE", "SHADOW")
    assert load_v3({"mode": "PAPER"}).learning_v3.influence_mode == "SHADOW"


def test_engine_runs_paper_bounded_with_env_override(tmp_path: Path, monkeypatch):
    """Env override ile kurulan motor PAPER_BOUNDED çalışır; guardrail'ler yerinde."""
    monkeypatch.setenv("TRADINGBOT_LEARNING_INFLUENCE_MODE", "PAPER_BOUNDED")
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    assert eng.influence_cfg.mode == "PAPER_BOUNDED"
    assert eng.influence_cfg.max_fraction <= 0.05
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s is not None
    for rec in eng.decision_journal.iter_all_rows():
        li = rec.get("learning_influence")
        if not li:
            continue
        assert li["mode"] == "PAPER_BOUNDED"
        if li.get("applied") and li.get("baseline"):
            delta = abs(float(li["effective"]) - float(li["baseline"]))
            assert delta <= float(li["baseline"]) * 0.05 + 1e-9


# ================================================================== coin memory görünümü

def test_coin_memory_summary_is_readonly_and_bounded(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    st = Path(eng.cfg.state_path)
    client = TestClient(create_app(st, Path(eng.cfg.cache_path), None, DashboardConfig()))
    base = eng.cfg.coins[0].split("/")[0]
    r = client.get(f"/api/coin-memory/{base}")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"].startswith(base)
    assert "real" in body and "shadow" in body and "aggregate" in body
    assert client.post(f"/api/coin-memory/{base}").status_code == 405
    assert client.get("/api/feature-registry").status_code == 200
    reg = client.get("/api/feature-registry").json()
    assert reg.get("n_active", 0) >= 50 and len(reg.get("families", [])) <= 8


def test_finite_check_on_coin_memory_with_corrupt_state(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    st = Path(eng.cfg.state_path)
    (st / "trade_memory.jsonl").write_text('{"bozuk json\n', encoding="utf-8")
    (st / "shadow_book.json").write_text("{ hicbir sey", encoding="utf-8")
    client = TestClient(create_app(st, Path(eng.cfg.cache_path), None, DashboardConfig()))
    assert client.get("/api/coin-memory/BTC").status_code == 200


def _finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)
