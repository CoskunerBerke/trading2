"""«Coin head'ler» tablosu AÇIK POZİSYON KAPSAMI — davranış testleri.

KÖK NEDEN (regresyon kilidi): `overview()` eski hâlinde `sorted(heads, key=-confidence)[:10]`
uyguluyordu. Bu bir TOP-N SUNUM KESİMİDİR; defterde veri kaybı yoktur ama top-10 dışında kalan
(ya da son coin-head seçkisinde HİÇ yer almayan) açık pozisyon tablodan düşüyor ve operatöre
"pozisyon yok" izlenimi veriyordu.

Yeni sözleşme (`views.coin_head_scope`):
  * bütün açık pozisyonlar ZORUNLU ve EN ÜSTTE,
  * sonra kalan kapasiteye güvene göre sıralı adaylar,
  * aynı sembol iki kez YOK,
  * karar UYDURULMAZ -> «KARAR VERİSİ YOK» satırı,
  * HTML render'ı ile API AYNI kaynaktan beslenir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient          # noqa: E402

from tradingbot.dashboard.app import create_app    # noqa: E402
from tradingbot.dashboard.config import DashboardConfig  # noqa: E402
from tradingbot.dashboard.state import StateReader  # noqa: E402
from tradingbot.dashboard.views import (COIN_HEAD_CANDIDATE_LIMIT, NO_DECISION_REASON,  # noqa: E402
                                        NO_DECISION_VERDICT, coin_head_scope, open_coverage)

# --- 5 GERÇEK açık futures pozisyonu (ekrandaki senaryo) --------------------------------------
OPEN = [("BZ/USDT", "F00001", "LONG", "90.61", "91.61"),
        ("XAUT/USDT", "F00002", "LONG", "4479.32", "4500.32"),
        ("LDO/USDT", "F00003", "SHORT", "1.50", "1.45"),
        ("AAVE/USDT", "F00004", "LONG", "260.00", "255.00"),
        ("ETH/USDT", "F00005", "SHORT", "3000.00", "2990.00")]


def _pos(sym, pid, side, entry, last, qty="1"):
    return {"id": pid, "symbol": sym, "market_type": "USDM_PERP", "side": side, "qty": qty,
            "entry_avg": entry, "leverage": 2, "isolated_margin": "10", "stop": "1",
            "targets": ["2"], "last_price": last, "entry_fee": "0",
            "opened_at": "2026-08-20T00:00:00+00:00", "fills": [{"id": "fill-" + pid}]}


POSITIONS = [_pos(*o) for o in OPEN]


def _head(sym, conf, verdict="FUTURES_LONG"):
    return {"symbol": sym, "verdict": verdict, "direction": "LONG", "confidence_calibrated": conf,
            "p_win": 0.55, "expected_return_net": 0.01, "expected_r": 1.5, "regime": "TREND",
            "generated_at": "2026-08-20T00:00:00+00:00", "vetoes": [],
            "spot_plan": {"valid": False}, "futures_plan": {"valid": True}}


# 12 aday; açık pozisyonlardan YALNIZ ikisi (XAUT, AAVE) seçkide ve yüksek güvenli.
HEADS = ([_head("XAUT/USDT", 0.91), _head("AAVE/USDT", 0.90)]
         + [_head("C%02d/USDT" % i, 0.80 - i * 0.01) for i in range(12)])


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    (d / "futures_ledger.json").write_text(json.dumps({
        "schema_version": 2, "kind": "futures", "equity": "500", "wallet_balance": "500",
        "fees": {"taker_pct": 0.0, "maker_pct": 0.0},
        "positions": {p["symbol"]: p for p in POSITIONS}, "history": []}), encoding="utf-8")
    (d / "coin_heads.json").write_text(json.dumps({
        "generated_at": "2026-08-20T00:00:00+00:00", "heads": HEADS,
        "chief": {"breadth": {"long": 2, "short": 0, "no_trade": 10, "hold": 2, "data_invalid": 0}}}),
        encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")
    return d


@pytest.fixture
def client(state_dir, tmp_path):
    return TestClient(create_app(state_dir, tmp_path / "market", None, DashboardConfig()))


def _syms(rows):
    return [str(r.get("symbol") or "") for r in rows]


# ===================================================================== 1) kapsam
def test_all_open_positions_are_shown_even_when_not_in_the_selection():
    """5 açık pozisyon, yalnız 2'si coin-head seçkisinde → yine de 5/5 görünür."""
    s = coin_head_scope(HEADS, POSITIONS)
    shown = _syms(s["heads"])
    for sym, *_ in OPEN:
        assert sym in shown, sym
    assert (s["open_positions_total"], s["open_positions_shown"]) == (5, 5)
    assert s["missing_open_symbols"] == [] and s["coverage_complete"] is True


def test_open_positions_come_first_then_candidates_by_confidence():
    s = coin_head_scope(HEADS, POSITIONS)
    shown = _syms(s["heads"])
    assert shown[:5] == [o[0] for o in OPEN]                    # önce açık pozisyonlar, defter sırası
    cand = shown[5:]
    confs = [float(h["confidence_calibrated"]) for h in s["heads"][5:]]
    assert confs == sorted(confs, reverse=True), cand           # sonra güvene göre azalan adaylar


def test_no_duplicate_symbol_when_open_position_is_also_a_candidate():
    """XAUT ve AAVE hem açık hem seçkide — tabloda BİR kez görünür."""
    shown = _syms(coin_head_scope(HEADS, POSITIONS)["heads"])
    assert len(shown) == len(set(shown))
    assert shown.count("XAUT/USDT") == 1 and shown.count("AAVE/USDT") == 1


def test_candidate_limit_can_never_drop_an_open_position():
    """Açık pozisyon sayısı aday limitinden büyük olsa bile hiçbiri düşmez."""
    s = coin_head_scope(HEADS, POSITIONS, candidate_limit=1)
    shown = _syms(s["heads"])
    assert len(shown) == 5 + 1                                  # açık adedi + aday kotası
    for sym, *_ in OPEN:
        assert sym in shown
    assert s["coverage_complete"] is True
    zero = coin_head_scope(HEADS, POSITIONS, candidate_limit=0)
    assert _syms(zero["heads"]) == [o[0] for o in OPEN] and zero["coverage_complete"] is True


def test_total_rows_are_open_count_plus_candidate_limit():
    s = coin_head_scope(HEADS, POSITIONS)
    assert len(s["heads"]) == 5 + min(COIN_HEAD_CANDIDATE_LIMIT, len(HEADS) - 2)
    assert s["candidate_limit"] == COIN_HEAD_CANDIDATE_LIMIT


# ===================================================================== 2) karar uydurulmaz
def test_open_positions_without_a_decision_get_an_explicit_fallback_row():
    """Güncel kararı olmayan 3 pozisyon (BZ, LDO, ETH) KARAR UYDURULMADAN gösterilir."""
    s = coin_head_scope(HEADS, POSITIONS)
    assert sorted(s["no_decision_symbols"]) == sorted(["BZ/USDT", "LDO/USDT", "ETH/USDT"])
    by = {str(h.get("symbol")): h for h in s["heads"]}
    for sym, pid, side, *_ in OPEN:
        h = by[sym]
        if sym in ("XAUT/USDT", "AAVE/USDT"):
            assert not h.get("no_decision") and h.get("verdict") == "FUTURES_LONG"
            continue
        assert h["no_decision"] is True
        assert h["verdict"] is None                             # karar UYDURULMAZ
        assert h["no_trade_reason"] == NO_DECISION_REASON
        assert h["direction"] == side                           # yön DEFTERDEN
        assert h["position_id"] == pid
        for k in ("confidence_calibrated", "p_win", "expected_r", "regime"):
            assert h[k] is None                                 # sahte model çıktısı YOK


def test_fallback_reason_and_label_are_the_documented_strings():
    assert NO_DECISION_VERDICT == "KARAR VERİSİ YOK"
    assert NO_DECISION_REASON == "Pozisyon defterde açık; son coin-head seçkisinde yer almıyor."


# ===================================================================== 3) kenar durumlar
def test_empty_ledger_keeps_pure_candidate_behaviour():
    s = coin_head_scope(HEADS, [])
    assert s["open_positions_total"] == 0 and s["coverage_complete"] is True
    assert s["missing_open_symbols"] == [] and s["no_decision_symbols"] == []
    assert len(s["heads"]) == COIN_HEAD_CANDIDATE_LIMIT


def test_missing_or_corrupt_coin_head_state_still_shows_every_open_position():
    """Coin head state'i yok/bozuk/bayat olsa bile açık pozisyonlar DÜŞMEZ."""
    for heads in (None, [], [{"bozuk": True}], ["metin", 42, None]):
        s = coin_head_scope(heads, POSITIONS)
        assert _syms(s["heads"]) == [o[0] for o in OPEN]
        assert s["coverage_complete"] is True
        assert sorted(s["no_decision_symbols"]) == sorted(o[0] for o in OPEN)


def test_corrupt_positions_are_ignored_without_crashing():
    s = coin_head_scope(HEADS, [None, "metin", {}, {"symbol": ""}, POSITIONS[0]])
    assert s["open_positions_total"] == 1 and _syms(s["heads"])[0] == "BZ/USDT"


def test_duplicate_position_entries_collapse_to_one_row():
    s = coin_head_scope(HEADS, POSITIONS + [POSITIONS[0]])
    assert s["open_positions_total"] == 5
    assert _syms(s["heads"]).count("BZ/USDT") == 1


# ===================================================================== 4) HTML / API paritesi
def test_html_and_api_show_exactly_the_same_rows(client):
    """İlk HTML render'ı ile polling/API sonucu BİREBİR aynı satır kümesini verir."""
    api = client.get("/api/overview").json()
    html = client.get("/").text
    assert api["open_positions_total"] == 5 and api["open_positions_shown"] == 5
    assert api["missing_open_symbols"] == [] and api["coverage_complete"] is True
    for sym in _syms(api["top_heads"]):
        assert sym in html, sym
    # ikinci istek (polling) aynı sonucu verir — kaynak tek
    again = client.get("/api/overview").json()
    assert _syms(again["top_heads"]) == _syms(api["top_heads"])
    assert (again["open_positions_shown"], again["coverage_complete"]) == (5, True)


def test_api_exposes_the_structural_coverage_fields(client):
    api = client.get("/api/overview").json()
    for k in ("open_positions_total", "open_positions_shown", "missing_open_symbols",
              "coverage_complete"):
        assert k in api, k
    assert isinstance(api["missing_open_symbols"], list)
    assert api["coverage_complete"] is True


def test_heading_shows_the_coverage_counter(client):
    html = client.get("/").text
    assert "Açık pozisyon kapsamı: 5 / 5" in html
    assert "AÇIK POZİSYON LİSTESİ DEĞİLDİR" in html          # tablo kapsamı açıkça yazılı


def test_html_rows_carry_ledger_position_id_side_and_live_pnl(client):
    """Açık satırlar defterdeki gerçek symbol/side/position_id ve anlık PnL ile eşleşir."""
    html = client.get("/").text
    api = client.get("/api/live/positions").json()
    pnl_by = {str(p["symbol"]): p["net_unrealized"] for p in api["positions"]}
    for sym, pid, side, *_ in OPEN:
        assert pid in html, pid                                # işlem ID sütunu
        assert sym in html
        val = float(pnl_by[sym])
        assert format(val, "+,.2f") in html or format(val, "+.2f") in html, (sym, val)
    ids = {str(p["trade_id"]) for p in api["positions"]}
    assert ids == {o[1] for o in OPEN}


def test_fallback_rows_are_rendered_with_the_explicit_label(client):
    html = client.get("/").text
    assert NO_DECISION_VERDICT in html
    assert NO_DECISION_REASON in html


def test_dashboard_get_requests_do_not_write_state(client, state_dir):
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in state_dir.iterdir()}
    for path in ("/", "/api/overview", "/api/live/positions", "/api/live/summary", "/api/live/health"):
        assert client.get(path).status_code == 200
    after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in state_dir.iterdir()}
    assert before == after


def test_state_reader_overview_uses_the_same_scope(state_dir):
    ov = StateReader(state_dir).overview()
    assert _syms(ov["top_heads"])[:5] == [o[0] for o in OPEN]
    assert ov["coin_head_scope"]["coverage_complete"] is True
    assert ov["open_positions_shown"] == ov["open_positions_total"] == 5


# ===================================================================== 5) eksik kapsam görünür olmalı
def test_open_coverage_measures_the_rows_it_is_given():
    """Sayaç VARSAYIM DEĞİL ÖLÇÜMDÜR: verilen satırlarda olmayan açık pozisyon `missing` olur."""
    full = coin_head_scope(HEADS, POSITIONS)["heads"]
    assert open_coverage(full, POSITIONS) == {
        "open_positions_total": 5, "open_positions_shown": 5,
        "missing_open_symbols": [], "coverage_complete": True}
    dropped = [r for r in full if str(r.get("symbol")) not in ("BZ/USDT", "ETH/USDT")]
    cov = open_coverage(dropped, POSITIONS)
    assert cov["open_positions_shown"] == 3 and cov["open_positions_total"] == 5
    assert cov["missing_open_symbols"] == ["BZ/USDT", "ETH/USDT"]
    assert cov["coverage_complete"] is False
    assert open_coverage([], POSITIONS)["open_positions_shown"] == 0
    assert open_coverage(full, [])["coverage_complete"] is True


def test_heading_reports_missing_coverage_in_red_when_a_row_is_dropped(client, monkeypatch):
    """Başlık RENDER EDİLEN satırlardan ölçer: bir açık satır düşerse 4/5 + kırmızı uyarı.

    Yük, kapsam alanlarında HÂLÂ 5/5 iddia ederken satırlardan biri düşürülür; başlık iddiaya
    değil satırlara bakmalıdır.
    """
    import tradingbot.dashboard.app as app_mod
    real = app_mod.coin_head_table

    def dropped(heads, positions, trades=None, **kw):
        p = real(heads, positions, trades, **kw)
        keep = [i for i, h in enumerate(p["heads"]) if str(h.get("symbol")) != "LDO/USDT"]
        p["heads"] = [p["heads"][i] for i in keep]
        p["rows"] = [p["rows"][i] for i in keep]
        p["meta"] = [p["meta"][i] for i in keep]
        return p                                               # kapsam alanlari HALA 5/5 diyor

    monkeypatch.setattr(app_mod, "coin_head_table", dropped)
    html = client.get("/").text
    assert "Açık pozisyon kapsamı: 4 / 5" in html               # iddiaya DEĞİL satıra bakar
    assert "LDO/USDT" in html and "warn-box" in html
    api = client.get("/api/live/coin-heads").json()
    assert api["open_positions_shown"] == 4 and api["coverage_complete"] is False
    assert api["missing_open_symbols"] == ["LDO/USDT"]
