"""PAPER sermaye katkısı + risk metriği etiket düzeltmesi kabul testleri.

Sözleşmeler:
* Katkı KÂR DEĞİLDİR: starting ve nakit AYNI tutarda artar → toplam PnL değişmez.
* Pozisyon/stop/TP/history/fee baytları AYNEN korunur; idempotent; yalnız PAPER + worker durmuşken.
* Risk bütçesi %6 ORANI sabit kalır; taban 100'e çıkınca bütçe 6.00, mevcut 2.83 risk ≈ %47.2.
* `%368.5` benzeri birleşik oran TANISALDIR — limit ihlali gibi sunulmaz; gerçek kapı
  `futures_risk_budget_utilization_pct`tir. Stopsuz spot açık uyarıyla gösterilir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.core import read_json
from tradingbot.ops.capital import (AUDIT_NAME, CapitalError, contribute_paper_capital)
from tradingbot.pnl import canonical_summary, portfolio_view
from tradingbot.risk import PROFILES, RiskEngine, build_state

# ------------------------------------------------------------------ gerçek şemalı fixture'lar


def _mk_state(tmp_path: Path, *, mode: str = "PAPER", live: bool = False,
              lock_pid: int | None = None) -> Path:
    st = tmp_path / "state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "mode.json").write_text(json.dumps(
        {"mode": mode, "live_order_path_enabled": live}), encoding="utf-8")
    if lock_pid is not None:
        (st / ".lock").write_text(str(lock_pid), encoding="utf-8")
    # Üretim şemasına yakın futures defteri: 6 pozisyon, 8 kapanış, PnL = −1.9
    fut = {"schema_version": 2, "kind": "futures", "starting_equity": "50",
           "wallet_balance": "48.0996", "equity": "48.0996", "max_positions": 3,
           "enforce_position_cap": False,
           "positions": {
               "XAUT/USDT": {"id": "F00005", "symbol": "XAUT/USDT", "side": "LONG", "qty": "0.002",
                             "entry_avg": "4479.32", "stop": "4485.15",
                             "targets": ["4520.0"], "targets_hit": 1,
                             "leverage": 1, "isolated_margin": "8.95864",
                             "opened_at": "2026-08-19T16:51:05+00:00"},
               "ETH/USDT": {"id": "F00010", "symbol": "ETH/USDT", "side": "LONG", "qty": "0.004",
                            "entry_avg": "2443.85", "stop": "2303.3872",
                            "targets": ["2600.0", "2700.0"], "targets_hit": 0,
                            "leverage": 2, "isolated_margin": "4.8877",
                            "opened_at": "2026-08-23T18:09:32+00:00"}},
           "history": [{"id": f"F0000{i}", "symbol": "X/USDT", "pnl": "-0.2375",
                        "net_pnl": "-0.2375", "closed_at": f"2026-08-2{i}T00:00:00+00:00"}
                       for i in range(1, 9)],
           "entries": [], "total_fees": "0.41", "total_funding": "0.02", "seq": 14,
           "meta": {}}
    (st / "futures_ledger.json").write_text(json.dumps(fut), encoding="utf-8")
    spot = {"schema_version": 2, "kind": "spot", "quote_asset": "USDT",
            "starting_equity": "50", "cash": "41.774", "locked_cash": "0",
            "assets": {"BNB": "0.012"}, "locked_assets": {},
            "history": [], "total_fees": "0.05", "meta": {}}
    (st / "spot_ledger.json").write_text(json.dumps(spot), encoding="utf-8")
    return st


# ================================================================== sermaye katkısı

def test_contribution_preserves_pnl_positions_and_history(tmp_path: Path):
    st = _mk_state(tmp_path)
    before = read_json(st / "futures_ledger.json")
    pnl_before = float(before["wallet_balance"]) - float(before["starting_equity"])
    pos_before = json.dumps(before["positions"], sort_keys=True)
    hist_before = json.dumps(before["history"], sort_keys=True)

    rec = contribute_paper_capital(st, futures_add=50, spot_add=50,
                                   adjustment_id="cap_200_test", operator="test",
                                   code_sha="deadbeef")
    assert rec["applied"] is True
    after = read_json(st / "futures_ledger.json")
    assert float(after["starting_equity"]) == 100.0
    assert float(after["wallet_balance"]) == pytest.approx(98.0996)
    assert float(after["equity"]) == pytest.approx(98.0996)
    # PnL KORUNDU (katkı kâr değildir)
    assert float(after["wallet_balance"]) - float(after["starting_equity"]) == pytest.approx(pnl_before)
    # pozisyon/stop/TP/targets_hit/lev/history BAYT BAYT aynı
    assert json.dumps(after["positions"], sort_keys=True) == pos_before
    assert json.dumps(after["history"], sort_keys=True) == hist_before
    assert after["total_fees"] == before["total_fees"]
    assert after["seq"] == before["seq"]
    # meta denetim izi
    assert after["meta"]["capital_contributions"][0]["adjustment_id"] == "cap_200_test"

    sp = read_json(st / "spot_ledger.json")
    assert float(sp["starting_equity"]) == 100.0
    assert float(sp["cash"]) == pytest.approx(91.774)
    assert sp["assets"] == {"BNB": "0.012"}, "BNB pozisyonuna DOKUNULMAZ"

    audit = read_json(st / AUDIT_NAME)
    a = audit["adjustments"][0]
    assert a["futures"]["starting_prev"] == "50" and a["futures"]["starting_new"] == "100"
    assert a["total_contributed_after"] == "200"
    assert a["code_sha"] == "deadbeef" and a["at"] and a["mode"] == "PAPER"
    assert a["futures"]["position_fingerprint"]


def test_contribution_is_idempotent(tmp_path: Path):
    st = _mk_state(tmp_path)
    r1 = contribute_paper_capital(st, futures_add=50, spot_add=50,
                                  adjustment_id="cap_200_x", operator="t")
    r2 = contribute_paper_capital(st, futures_add=50, spot_add=50,
                                  adjustment_id="cap_200_x", operator="t")
    assert r1["applied"] is True and r2.get("idempotent_noop") is True
    after = read_json(st / "futures_ledger.json")
    assert float(after["starting_equity"]) == 100.0, "ikinci çağrı +50 EKLEYEMEZ"
    assert float(read_json(st / "spot_ledger.json")["cash"]) == pytest.approx(91.774)
    assert len(read_json(st / AUDIT_NAME)["adjustments"]) == 1


def test_contribution_fail_closed_gates(tmp_path: Path):
    with pytest.raises(CapitalError, match="PAPER"):
        contribute_paper_capital(_mk_state(tmp_path / "a", mode="OBSERVE"),
                                 futures_add=50, spot_add=50, adjustment_id="cap_x1")
    with pytest.raises(CapitalError, match="live_order_path"):
        contribute_paper_capital(_mk_state(tmp_path / "b", live=True),
                                 futures_add=50, spot_add=50, adjustment_id="cap_x2")
    import os
    with pytest.raises(CapitalError, match="worker"):
        contribute_paper_capital(_mk_state(tmp_path / "c", lock_pid=os.getpid()),
                                 futures_add=50, spot_add=50, adjustment_id="cap_x3")
    with pytest.raises(CapitalError, match="negatif"):
        contribute_paper_capital(_mk_state(tmp_path / "d"), futures_add=-1, spot_add=0,
                                 adjustment_id="cap_x4")
    # hiçbiri dosya değiştirmedi
    for sub in ("a", "b", "d"):
        led = read_json(tmp_path / sub / "state" / "futures_ledger.json")
        assert float(led["starting_equity"]) == 50.0


def test_ledger_roundtrip_after_contribution(tmp_path: Path):
    """Katkı sonrası dosya GERÇEK FuturesLedgerV2/SpotLedger ile yüklenebilir kalır."""
    from tradingbot.accounting import FuturesLedgerV2
    from tradingbot.accounting.spot_ledger import SpotLedger
    st = _mk_state(tmp_path)
    contribute_paper_capital(st, futures_add=50, spot_add=50, adjustment_id="cap_rt")
    doc = read_json(st / "futures_ledger.json")
    doc_no_hist = dict(doc, history=[])          # history round-trip'i gerçek üretim şeması ister;
    led = FuturesLedgerV2.from_dict(doc_no_hist)  # bayt korunumu ayrı testte kanıtlı
    assert float(led.starting_equity) == 100.0
    assert float(led.wallet_balance) == pytest.approx(98.0996)
    assert set(led.positions) == {"XAUT/USDT", "ETH/USDT"}
    assert len(doc["history"]) == 8
    sp = SpotLedger.from_dict(read_json(st / "spot_ledger.json"))
    assert float(sp.starting_equity) == 100.0 and float(sp.cash) == pytest.approx(91.774)


# ================================================================== risk bütçesi ölçekleme

def _risk_snapshot(starting: float, positions: list[dict]) -> dict:
    state = build_state(equity=48.1, starting_equity=starting, available=10.0,
                        used_margin=34.31, positions=positions, history=[])
    eng = RiskEngine(PROFILES["PAPER_RESEARCH"])
    return eng.snapshot(state)


_FUT_POS = [
    {"symbol": "ETH/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 9.7754,
     "margin": 4.8877, "entry": 2443.85, "stop": 2303.3872, "leverage": 2},
    {"symbol": "QQQ/USDT", "market_type": "USDM_PERP", "side": "SHORT", "notional": 11.3453,
     "margin": 3.7818, "entry": 709.08, "stop": 716.0571, "leverage": 3},
    {"symbol": "PAXG/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 23.1155,
     "margin": 7.7052, "entry": 4623.09, "stop": 4535.4167, "leverage": 3},
    {"symbol": "CRCL/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 11.028,
     "margin": 3.676, "entry": 91.9, "stop": 84.7751, "leverage": 3},
    {"symbol": "BMNR/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 10.6016,
     "margin": 5.3008, "entry": 24.77, "stop": 22.7816, "leverage": 2},
    {"symbol": "XAUT/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 8.9586,
     "margin": 8.9586, "entry": 4479.32, "stop": 4485.15, "leverage": 1},
]


def test_budget_scales_with_contributed_capital_ratio_constant():
    """Taban 50→100: bütçe 3.00→6.00 (%6 SABİT); 2.83 risk %94.3→%47.2 kullanım."""
    old = _risk_snapshot(50.0, _FUT_POS)["exposure"]
    new = _risk_snapshot(100.0, _FUT_POS)["exposure"]
    assert old["max_total_open_risk_usdt"] == pytest.approx(3.0)
    assert new["max_total_open_risk_usdt"] == pytest.approx(6.0)
    assert new["equity_basis"] == pytest.approx(100.0)
    fut = new["futures_stop_risk_usdt"]
    assert fut == pytest.approx(2.8295, abs=0.01), "mevcut stop riski DEĞİŞMEZ"
    assert fut / new["max_total_open_risk_usdt"] * 100 == pytest.approx(47.2, abs=0.3)
    assert old["futures_stop_risk_usdt"] / old["max_total_open_risk_usdt"] * 100 == \
        pytest.approx(94.3, abs=0.3)
    # spot allocation ORANI sabit: %30 → 15.00'dan 30.00'a ölçeklenir
    assert old["max_spot_allocation_usdt"] == pytest.approx(15.0)
    assert new["max_spot_allocation_usdt"] == pytest.approx(30.0)


def test_no_fixed_position_cap_in_paper_and_no_forced_trades():
    """PAPER_RESEARCH'te sabit pozisyon tavanı YOK; kapasite artışı işlem ZORLAMAZ."""
    from tradingbot.risk.profiles import enforces_position_cap
    p = PROFILES["PAPER_RESEARCH"]
    assert p.max_open_positions is None and p.max_positions_per_market is None
    assert enforces_position_cap(p) is False
    # 7. aday: eski bütçede TOTAL_OPEN_RISK bloklardı; yeni bütçede kapı AÇILIR ama bu
    # yalnız kapasitedir — kabul yine sinyal/net-edge/risk zincirinin işidir.
    state_old = build_state(equity=48.1, starting_equity=50.0, available=10.0,
                            used_margin=34.31, positions=_FUT_POS, history=[])
    state_new = build_state(equity=98.1, starting_equity=100.0, available=60.0,
                            used_margin=34.31, positions=_FUT_POS, history=[])
    eng = RiskEngine(PROFILES["PAPER_RESEARCH"])
    plan = {"symbol": "SOL/USDT", "market_type": "USDM_PERP", "direction": "LONG",
            "entry": 100.0, "stop": 91.0, "targets": [110.0, 120.0], "notional": 10.0,
            "margin": 5.0, "leverage": 2, "amount_type": "notional", "expected_r": 2.0}
    d_old = eng.evaluate(dict(plan), state_old)
    d_new = eng.evaluate(dict(plan), state_new)
    assert "TOTAL_OPEN_RISK" in d_old.reasons and d_old.allowed is False, (
        "eski bütçede (2.83+0.9>3.00) kapasite kapısı bloklamalı")
    assert "TOTAL_OPEN_RISK" not in d_new.reasons, (
        "yeni bütçede (2.83+0.9<6.00) kapasite kapısı açık — diğer kapılar bağımsız, "
        "işlem ZORLANMAZ")


# ================================================================== etiket düzeltmesi

def _summary(risk_exposure: dict, *, fut_doc=None, spot_doc=None) -> dict:
    pv = portfolio_view([], [], marks={})
    return canonical_summary(pv, futures_equity=98.1, spot_equity=99.9,
                             risk_state={"exposure": risk_exposure,
                                         "generated_at": "2026-08-26T00:00:00+00:00"},
                             futures_ledger_doc=fut_doc, spot_ledger_doc=spot_doc)


def test_combined_ratio_is_diagnostic_not_enforced():
    exp = {"total_open_risk_usdt": 11.0555, "futures_stop_risk_usdt": 2.8295,
           "max_total_open_risk_usdt": 3.0, "equity_basis": 50.0,
           "equity_basis_kind": "starting_equity",
           "spot_exposure_usdt": 8.226, "spot_unbounded_notional_usdt": 8.226,
           "spot_symbols_without_stop": ["BNB/USDT"], "spot_stop_risk_usdt": 0}
    s = _summary(exp)
    # gerçek kapı: 2.83/3.00 = %94.3
    assert s["futures_risk_budget_utilization_pct"] == pytest.approx(94.3, abs=0.05)
    # birleşik gözlem AYRI alanda ve TANISAL işaretli
    assert s["combined_conservative_observation_usdt"] == pytest.approx(11.0555)
    assert s["open_risk_budget_utilization_pct"] == pytest.approx(368.5, abs=0.1)
    assert s["open_risk_budget_utilization_semantics"] == "diagnostic_ratio_not_enforced"
    assert s["combined_observation_semantics"] == "futures_stop_risk_plus_unbounded_spot_notional"
    # stopsuz spot açık uyarı
    assert s["unbounded_spot_warning"] == "STOPSUZ SPOT — 8.23 USDT MARUZİYET (BNB/USDT)"


def test_cards_do_not_present_combined_ratio_as_limit_breach():
    from tradingbot.dashboard.views import build
    exp = {"total_open_risk_usdt": 11.0555, "futures_stop_risk_usdt": 2.8295,
           "max_total_open_risk_usdt": 3.0, "equity_basis": 50.0,
           "equity_basis_kind": "starting_equity",
           "spot_exposure_usdt": 8.226, "spot_unbounded_notional_usdt": 8.226,
           "spot_symbols_without_stop": ["BNB/USDT"], "spot_stop_risk_usdt": 0}
    vm = build([], [], None, marks={}, futures_equity=48.1, spot_equity=49.9,
               risk_state={"exposure": exp, "generated_at": "2026-08-26T00:00:00+00:00"})
    cards = {c.key: c for c in vm["cards"]}
    combo = cards["open_risk_budget_utilization_pct"]
    assert "TANISAL" in combo.sub and "LİMİT İHLALİ DEĞİL" in combo.sub
    assert "tanısal" in combo.title.lower()
    assert "diagnostic_ratio_not_enforced" in combo.sub
    reserved = cards["risk_engine_reserved_usdt"]
    assert "Birleşik konservatif gözlem" in reserved.title
    assert "UYGULANMAZ" in reserved.sub
    fut = cards["futures_risk_budget_utilization_pct"]
    assert fut.value == pytest.approx(94.3, abs=0.05)
    warn = cards["unbounded_spot_warning"]
    assert "STOPSUZ SPOT" in warn.display and "8.23" in warn.display


def test_futures_only_view_unchanged_without_spot():
    exp = {"total_open_risk_usdt": 2.8295, "futures_stop_risk_usdt": 2.8295,
           "max_total_open_risk_usdt": 3.0, "equity_basis": 50.0,
           "equity_basis_kind": "starting_equity",
           "spot_exposure_usdt": 0.0, "spot_unbounded_notional_usdt": 0.0,
           "spot_symbols_without_stop": [], "spot_stop_risk_usdt": 0}
    s = _summary(exp)
    assert s["open_risk_budget_utilization_pct"] == pytest.approx(94.3, abs=0.05)
    assert s["futures_risk_budget_utilization_pct"] == pytest.approx(94.3, abs=0.05)
    assert s["unbounded_spot_warning"] is None


def test_contributed_capital_fields_and_legacy_schema_no_500():
    fut_doc = {"starting_equity": "100", "wallet_balance": "98.0996"}
    spot_doc = {"starting_equity": "100", "cash": "91.774"}
    s = _summary({"total_open_risk_usdt": 2.8, "futures_stop_risk_usdt": 2.8,
                  "max_total_open_risk_usdt": 6.0, "equity_basis": 100.0,
                  "equity_basis_kind": "starting_equity"},
                 fut_doc=fut_doc, spot_doc=spot_doc)
    assert s["futures_contributed_capital_usdt"] == pytest.approx(100.0)
    assert s["spot_contributed_capital_usdt"] == pytest.approx(100.0)
    assert s["total_contributed_capital_usdt"] == pytest.approx(200.0)
    assert s["futures_total_net_pnl_usdt"] == pytest.approx(98.1 - 100.0)
    # eski şema / eksik defter → None + neden; istisna YOK
    s2 = _summary({"total_open_risk_usdt": 2.8})
    assert s2["futures_contributed_capital_usdt"] is None
    assert "futures_contributed_capital_usdt" in s2["unavailable_reason"]
    s3 = _summary({})                                    # tamamen boş exposure
    assert s3["open_risk_budget_utilization_pct"] is None
    json.dumps({k: v for k, v in s3.items()}, allow_nan=False)


def test_dashboard_http_never_500_with_new_fields(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from test_engine_v3 import _engine
    from tradingbot.dashboard.app import DashboardConfig, create_app
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    st = Path(eng.cfg.state_path)
    client = TestClient(create_app(st, Path(eng.cfg.cache_path), None, DashboardConfig()))
    assert client.get("/").status_code == 200
    assert client.get("/api/live/summary").status_code == 200
    body = client.get("/api/live/summary").json()["summary"]
    assert body["open_risk_budget_utilization_semantics"] == "diagnostic_ratio_not_enforced"
    assert "futures_contributed_capital_usdt" in body
    assert "total_contributed_capital_usdt" in body
    # bozuk spot defteri → yine 200
    (st / "spot_ledger.json").write_text("{ bozuk", encoding="utf-8")
    assert client.get("/api/live/summary").status_code == 200
