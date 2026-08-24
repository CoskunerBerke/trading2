"""Quant Evaluation V1 — dashboard testleri (ağsız, sahte state).

Kapsam: eski endpoint şemalarının geriye uyumluluğu, yeni read-only /quant + /api/quant/summary,
RFC-safe JSON (NaN'lı quant_eval.json 500 vermez), boş state crash olmaz, mutasyon metodları 405,
token/secret sızıntısı yok.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.dashboard.app import DashboardConfig, create_app

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


def _dirs(tmp: Path) -> tuple[Path, Path]:
    st, data = tmp / "state", tmp / "data"
    st.mkdir(), data.mkdir()
    (st / "futures_ledger.json").write_text(json.dumps({"schema_version": 2, "wallet_balance": "50",
                                                        "starting_equity": "50", "positions": {},
                                                        "history": [], "total_fees": "0"}), encoding="utf-8")
    (st / "portfolio.json").write_text(json.dumps({"cash": 50.0, "starting_equity": 50.0,
                                                   "positions": {}, "history": []}), encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")
    return st, data


def _quant_doc(**over):
    d = {"schema_version": "quant_eval_v1",
         "champion_challenger": {"decision": "KEEP_CHAMPION", "note": "yetersiz örnek"},
         "overall": {"n": 42, "insufficient_sample": False, "expectancy_r": 0.12,
                     "expectancy_usdt": 3.4, "profit_factor": 1.6, "profit_factor_state": "ok",
                     "max_drawdown_r": -2.5, "tail_loss_r_cvar5": -1.1,
                     "fees_usdt": 12.0, "funding_usdt": 3.0, "slippage_usdt": 2.0,
                     "gross_pnl_usdt": 160.0, "net_pnl_usdt": 143.0,
                     "calibration": {"brier": 0.21, "n": 42, "state": "ok"}},
         "journal": {"n_records": 100, "n_labeled": 60, "n_accepted": 40},
         "walk_forward": {"mode": "anchored", "n_folds": 4, "oos_sign_consistency": 0.75,
                          "oos_expectancy_r_by_fold": [0.1, 0.2, -0.05, 0.15],
                          "pbo": None, "pbo_state": "not_computable", "holdout_locked": True,
                          "purged_rows": 3, "unassigned_rows": 0},
         "attribution_summary": {"symbol": {"ETH/USDT": {"n": 20, "expectancy_r": 0.2,
                                                         "net_pnl_usdt": 80.0, "max_drawdown_r": -1.0,
                                                         "insufficient_sample": False}}},
         "risk_clusters": {"clusters": [{"cluster": 0, "symbols": ["AAVE/USDT", "ETH/USDT"],
                                         "long_usdt": 30.0, "short_usdt": 0.0, "net_usdt": 30.0,
                                         "share_of_total": 0.75}],
                           "total_long_usdt": 30.0, "total_short_usdt": 10.0, "total_usdt": 40.0},
         "manifest": {"run_id": "r1", "code_sha": "abc", "config_hash": "h", "seed": 7,
                      "valid_backtest": True, "manifest_hash": "m",
                      "label": "TEST DATA / RESEARCH — kârlılık kanıtı değildir"},
         "warnings": ["minimum sample: hour_bucket boyutu yetersiz"]}
    d.update(over)
    return d


def test_quant_page_empty_state_no_crash(tmp_path):
    st, data = _dirs(tmp_path)
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    r = c.get("/quant")
    assert r.status_code == 200 and "quant_eval.json yok" in r.text
    j = c.get("/api/quant/summary").json()
    assert j["available"] is False and "reason" in j


def test_quant_page_and_api_with_report(tmp_path):
    st, data = _dirs(tmp_path)
    (st / "quant_eval.json").write_text(json.dumps(_quant_doc(), ensure_ascii=False), encoding="utf-8")
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    r = c.get("/quant")
    assert r.status_code == 200
    assert "KEEP_CHAMPION" in r.text and "kârlılık kanıtı DEĞİLDİR" in r.text
    assert "Walk-forward" in r.text and "Risk kümeleri" in r.text
    j = c.get("/api/quant/summary").json()
    assert j["available"] is True
    assert j["champion_challenger"]["decision"] == "KEEP_CHAMPION"
    assert j["overall"]["expectancy_r"] == pytest.approx(0.12)
    assert j["walk_forward"]["pbo"] is None                  # sahte sayı üretilmedi


def test_quant_api_rfc_safe_with_nan_in_state(tmp_path):
    st, data = _dirs(tmp_path)
    doc = _quant_doc()
    doc["overall"]["expectancy_r"] = float("nan")
    doc["overall"]["profit_factor"] = float("inf")
    # worker benzeri bare-NaN literal yazımı: json.dumps allow_nan default True
    (st / "quant_eval.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert "NaN" in (st / "quant_eval.json").read_text(encoding="utf-8")
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    r = c.get("/api/quant/summary")
    assert r.status_code == 200                              # 500 YOK
    assert "NaN" not in r.text and "Infinity" not in r.text
    j = r.json()
    assert j["overall"]["expectancy_r"] is None              # yalnız ilgili leaf null
    assert j["overall"]["n"] == 42                            # sağlam kardeş alan korundu
    assert "unavailable_reason" in j
    assert c.get("/quant").status_code == 200                # HTML sayfası da crash olmaz


def test_old_endpoints_backward_compatible(tmp_path):
    st, data = _dirs(tmp_path)
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    ov = c.get("/api/overview")
    assert ov.status_code == 200
    j = ov.json()
    for key in ("top_heads", "coin_head_scope", "coin_head_rows", "open_positions_total",
                "coverage_complete"):
        assert key in j, key
    lh = c.get("/api/live/coin-heads")
    assert lh.status_code == 200
    lj = lh.json()
    assert "coin_head_scope" in lj and "open_positions_total" in lj
    assert c.get("/learning").status_code == 200
    assert c.get("/health/live").status_code == 200


def test_mutating_methods_rejected_405(tmp_path):
    st, data = _dirs(tmp_path)
    (st / "quant_eval.json").write_text(json.dumps(_quant_doc()), encoding="utf-8")
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    for method in ("post", "put", "patch", "delete"):
        r = getattr(c, method)("/api/quant/summary")
        assert r.status_code == 405, method
        r2 = getattr(c, method)("/quant")
        assert r2.status_code == 405, method


def test_no_token_or_secret_leakage(tmp_path, monkeypatch):
    st, data = _dirs(tmp_path)
    (st / "quant_eval.json").write_text(json.dumps(_quant_doc()), encoding="utf-8")
    cfg = DashboardConfig(auth_token="cok-gizli-token-123")
    c = TestClient(create_app(st, data, None, cfg))
    r = c.get("/api/quant/summary", headers={"Authorization": "Bearer cok-gizli-token-123"})
    assert r.status_code == 200
    assert "cok-gizli-token-123" not in r.text
    html = c.get("/quant", headers={"Authorization": "Bearer cok-gizli-token-123"}).text
    assert "cok-gizli-token-123" not in html
    assert c.get("/api/quant/summary").status_code == 401     # tokensiz erişim yok
