"""ON COINLIK ANALIZ EKRANI (`/universe`) — panelin ne gosterdigi ve ne UYDURMADIGI.

Ekranin tek isi okumaktir: kararlar `coin_heads.json`, ret gerekceleri `risk.json`, veri
kimligi `frame_provenance.json`. Bu testler dort seyi kilitler:

1. Evrendeki her sembol satirlanir — karari olmayan da (eksik kapsam GORUNUR olmali).
2. Evren DISINDA kalan ACIK pozisyon satirda KALIR ve EVREN DISI olarak isaretlenir.
3. Perpetual cerceve alinamamis sembol SPOT olarak isaretlenir ve uyari cikar.
4. Acilmama nedeni risk gunlugunden OLDUGU GIBI tasinir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.core import iso
from tradingbot.dashboard.app import DashboardConfig, create_app
from tradingbot.dashboard.views import universe_table

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

TEN = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
       "LINK/USDT", "DOGE/USDT", "AVAX/USDT", "LTC/USDT", "AAVE/USDT"]


def _head(sym: str, **kw) -> dict:
    d = {"symbol": sym, "market_type": "futures", "regime": "TREND_UP", "verdict": "FUTURES_LONG",
         "direction": "LONG", "p_win": 0.55, "expected_r": 1.8, "stop": 100.0, "targets": [120.0],
         "futures_plan": {"entry_type": "breakout", "entry_trigger": "4h kapanış 110 üzeri",
                          "invalidation": "4h kapanış 98 altı", "stop": 100.0, "targets": [120.0]}}
    d.update(kw)
    return d


# --------------------------------------------------------------------------- yuk (saf veri)
def test_every_universe_symbol_gets_a_row_even_without_a_decision():
    p = universe_table(universe=TEN, heads=[_head("BTC/USDT")], positions=[],
                       provenance={"by_symbol": {}}, risk_decisions=[])
    assert len(p["rows"]) == 10
    assert [r[0] for r in p["rows"]] == TEN
    assert p["coverage"]["universe_with_decision"] == 1
    assert set(p["coverage"]["missing_decision"]) == set(TEN) - {"BTC/USDT"}


def test_open_position_outside_the_universe_keeps_its_row_and_is_labelled():
    """Cikis yonetimi surdugu icin bu satir panelden DUSURULEMEZ."""
    p = universe_table(universe=TEN, heads=[], positions=[{"symbol": "ADA/USDT"}],
                       provenance={"by_symbol": {}}, risk_decisions=[])
    assert len(p["rows"]) == 11
    row = next(r for r in p["rows"] if r[0] == "ADA/USDT")
    assert row[1] == "EVREN DIŞI"
    assert p["coverage"]["outside_universe_open"] == ["ADA/USDT"]


def test_open_position_inside_the_universe_is_labelled_open():
    p = universe_table(universe=TEN, heads=[], positions=[{"symbol": "SOL/USDT"}],
                       provenance={"by_symbol": {}}, risk_decisions=[])
    row = next(r for r in p["rows"] if r[0] == "SOL/USDT")
    assert row[1] == "AÇIK"
    assert p["coverage"]["outside_universe_open"] == []


def test_spot_framed_symbol_is_marked_and_counted():
    prov = {"by_symbol": {"ETH/USDT": {"market": "SPOT", "entry_ok": False,
                                       "reason": "FUTURES_FRAMES_UNAVAILABLE"},
                          "BTC/USDT": {"market": "USDM_PERP", "entry_ok": True}}}
    p = universe_table(universe=TEN, heads=[], positions=[], provenance=prov, risk_decisions=[])
    by = {r[0]: r for r in p["rows"]}
    assert by["ETH/USDT"][12] == "SPOT"
    assert by["BTC/USDT"][12] == "PERP"
    assert by["SOL/USDT"][12] == "—"                  # provenans yok → uydurma YOK
    assert p["coverage"]["entry_blocked_on_data"] == ["ETH/USDT"]


def test_block_reason_is_carried_verbatim_from_the_risk_log():
    rd = [{"symbol": "BTC/USDT", "block_code": "SYMBOL_NOT_IN_ENTRY_UNIVERSE",
           "block_detail": "NOT_IN_UNIVERSE"},
          {"symbol": "ETH/USDT", "block_code": "NEGATIVE_NET_EDGE"}]
    p = universe_table(universe=TEN, heads=[], positions=[], provenance={}, risk_decisions=rd)
    by = {r[0]: r for r in p["rows"]}
    assert by["BTC/USDT"][13] == "SYMBOL_NOT_IN_ENTRY_UNIVERSE (NOT_IN_UNIVERSE)"
    assert by["ETH/USDT"][13] == "NEGATIVE_NET_EDGE"
    assert by["SOL/USDT"][13] == "—"


def test_last_risk_entry_wins_when_a_symbol_is_logged_twice():
    rd = [{"symbol": "BTC/USDT", "block_code": "NO_TRIGGER"},
          {"symbol": "BTC/USDT", "block_code": "NEGATIVE_NET_EDGE"}]
    p = universe_table(universe=TEN, heads=[], positions=[], provenance={}, risk_decisions=rd)
    assert next(r for r in p["rows"] if r[0] == "BTC/USDT")[13] == "NEGATIVE_NET_EDGE"


def test_plan_fields_are_read_not_invented():
    p = universe_table(universe=["BTC/USDT"], heads=[_head("BTC/USDT")], positions=[],
                       provenance={}, risk_decisions=[])
    row = p["rows"][0]
    assert row[5] == "breakout"
    assert row[6] == "4h kapanış 110 üzeri"
    assert row[7] == "4h kapanış 98 altı"
    # plan alani olmayan sembolde uydurma yok
    q = universe_table(universe=["BTC/USDT"], heads=[{"symbol": "BTC/USDT"}], positions=[],
                       provenance={}, risk_decisions=[])
    assert q["rows"][0][5] == "—" and q["rows"][0][6] == "—"


# --------------------------------------------------------------------------- sayfa (HTTP)
def _app(tmp: Path, *, universe: list[str], prov_by: dict | None = None,
         heads: list[dict] | None = None, positions: dict | None = None,
         risk_decisions: list[dict] | None = None):
    st, data = tmp / "state", tmp / "data"
    st.mkdir(); data.mkdir()
    (st / "coin_heads.json").write_text(json.dumps(
        {"generated_at": iso(), "run_id": "r1", "heads": heads or []}), encoding="utf-8")
    (st / "futures_ledger.json").write_text(json.dumps(
        {"schema_version": 2, "wallet_balance": "100", "starting_equity": "100",
         "positions": positions or {}, "history": [], "total_fees": "0"}), encoding="utf-8")
    (st / "risk.json").write_text(json.dumps(
        {"profile": {"name": "PAPER_RESEARCH"}, "last_decisions": risk_decisions or []}), encoding="utf-8")
    (st / "frame_provenance.json").write_text(json.dumps(
        {"generated_at": iso(), "universe_enabled": bool(universe), "entry_universe": universe,
         "entry_blocked_on_data": [], "by_symbol": prov_by or {}}), encoding="utf-8")
    return TestClient(create_app(st, data, None, DashboardConfig()))


def test_page_renders_all_ten_symbols(tmp_path: Path):
    c = _app(tmp_path, universe=TEN, heads=[_head(s) for s in TEN])
    r = c.get("/universe")
    assert r.status_code == 200
    for s in TEN:
        assert s in r.text
    assert "10 / 10" in r.text


def test_page_says_so_when_the_universe_is_off(tmp_path: Path):
    c = _app(tmp_path, universe=[])
    r = c.get("/universe")
    assert r.status_code == 200
    assert "KAPALI" in r.text
    assert "entry_universe" in r.text


def test_page_warns_about_spot_framed_symbols(tmp_path: Path):
    c = _app(tmp_path, universe=TEN,
             prov_by={"ETH/USDT": {"market": "SPOT", "entry_ok": False,
                                   "reason": "FUTURES_FRAMES_UNAVAILABLE"}})
    r = c.get("/universe")
    assert "YENİ GİRİŞ kapalı" in r.text
    assert "ETH/USDT" in r.text


def test_page_warns_about_universe_symbols_without_a_decision(tmp_path: Path):
    c = _app(tmp_path, universe=TEN, heads=[_head("BTC/USDT")])
    r = c.get("/universe")
    assert "kararı OLMAYAN" in r.text
    assert "1 / 10" in r.text


def test_page_lists_an_outside_open_position_without_claiming_it_is_entryable(tmp_path: Path):
    c = _app(tmp_path, universe=TEN,
             positions={"ADA/USDT": {"symbol": "ADA/USDT", "side": "LONG", "qty": "10",
                                     "entry_price": "1", "last_price": "1", "leverage": 2}})
    r = c.get("/universe")
    assert "ADA/USDT" in r.text
    assert "EVREN DIŞI" in r.text
    assert "yalnız çıkış yönetimi" in r.text


def test_universe_page_is_in_the_navigation(tmp_path: Path):
    c = _app(tmp_path, universe=TEN)
    assert '/universe' in c.get("/").text


def test_no_trade_reason_fills_in_when_the_candidate_never_reached_ranking():
    """Siralamaya girmemis aday da 'neden acilmadi' sorusunu cevaplamali.

    Risk gunlugu yalnizca siralamaya GIREN adaylari tasir. NO_TRADE veren bir coin head
    oraya hic ulasmaz; gerekcesi kendi `no_trade_reason` alanindadir.
    """
    heads = [_head("BTC/USDT", verdict="NO_TRADE", no_trade_reason="NO_TRADE_RED_TEAM_VETO")]
    p = universe_table(universe=TEN, heads=heads, positions=[], provenance={}, risk_decisions=[])
    assert next(r for r in p["rows"] if r[0] == "BTC/USDT")[13] == "NO_TRADE_RED_TEAM_VETO"


def test_risk_log_reason_wins_over_the_head_reason():
    """Aday siralamaya girdiyse NIHAI gerekce kapinin kodudur."""
    heads = [_head("BTC/USDT", no_trade_reason="ESKI_ASAMA")]
    rd = [{"symbol": "BTC/USDT", "block_code": "NEGATIVE_NET_EDGE"}]
    p = universe_table(universe=TEN, heads=heads, positions=[], provenance={}, risk_decisions=rd)
    assert next(r for r in p["rows"] if r[0] == "BTC/USDT")[13] == "NEGATIVE_NET_EDGE"
