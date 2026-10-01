# -*- coding: utf-8 -*-
"""PANEL ↔ MOTOR AYNI YAPI KAYDI (structures_v1) — gerçek FastAPI uygulaması, gerçek defter/karar dosyaları.

Motor tarafı GERÇEK üretim yoludur (formasyon botu: tarama → katalog → plan → tetik → risk → defter → çıkış); veri
SENTETİK ve etiketlidir. Panel aynı state dizinini okur: grafik katmanı ve plan kutusu motorun YAZDIĞI karar satırını
çizer; panel yapıyı yeniden hesaplamaz. Ayrıca: satır kimliği (defter+piyasa+sembol+dilim+işlem+an), geçmiş işlemin
kendi anı, bekleyen funding'in AYRI gösterimi, aynı coinde çakışan pozisyonların görünürlüğü, sade varsayılan.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_pattern_trader_v1 import CLOCK, M15, T0, _exinfo, _provider, _scanner, _set_mark  # noqa: E402
from test_structures_pattern_bot_v2 import EX, SYM, _enforce_cfg, _flag15, _frames  # noqa: E402

from tradingbot.dashboard import terminal as term  # noqa: E402
from tradingbot.structures import catalog as K  # noqa: E402
from tradingbot.structures.store import StructureStore  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_clock():
    CLOCK[0] = T0
    yield
    CLOCK[0] = T0


def _client(state_dir: Path, data_dir: Path):
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    data_dir.mkdir(parents=True, exist_ok=True)
    return TestClient(create_app(state_dir, data_dir))


def _engine_trade(tmp_path: Path):
    """Formasyon botu v2 zinciri: bayrak oluşur → plan → kırılış kapanışı → giriş. Döner: (cfg, sc, book, plan, pos, p, brk)."""
    flag, brk = _flag15()
    p = _provider({SYM: _frames(brk)}, _exinfo(EX))
    cfg = _enforce_cfg(tmp_path)
    sc, book = _scanner(cfg, p)
    CLOCK[0] = int(flag[-1]["timestamp"]) + M15
    _set_mark(p, SYM, flag[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = int(brk[-1]["timestamp"]) + M15
    _set_mark(p, SYM, brk[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    plan = next(pl for pl in book.plans.values() if pl["family"] == "D2_CHART_STRUCTURE")
    pos = book.ledger.positions[SYM]
    return cfg, sc, book, plan, pos, p, brk


def _write_candles(data_dir: Path, rows):
    data_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(data_dir / "binanceusdm_LNG-USDT_15m.csv", index=False)


def test_panel_draws_the_record_the_engine_wrote_for_the_open_trade(tmp_path):
    cfg, sc, book, plan, pos, p, brk = _engine_trade(tmp_path)
    cl = _client(cfg.state_path, tmp_path / "data")
    j = cl.get("/api/structures/LNG", params={"book": "pattern_trader", "market": "futures", "trade": pos.id}).json()
    assert j["origin"] == "trade" and j["market_id"] == "USDM_PERP"
    pr = j["decision"]["primary"]
    assert pr["pattern_id"] == plan["pattern_id"] == pos.features["structure"]["pattern_id"]
    assert j["decision"]["trade_id"] == pos.id and j["decision"]["action"] == "ENTER"
    kinds = {e["kind"]: e for e in j["elements"]}
    assert kinds["structure_trigger"]["price"] == pytest.approx(plan["trigger"]["level"])
    assert kinds["structure_invalidation"]["price"] == pytest.approx(plan["invalidation"]["level"])
    assert "structure_geometry" in kinds and "structure_anchors" in kinds, "dayanak/geometri motor kaydından"
    # SADE: işlemin gerçek stop/hedefi «İşlemler» katmanında; yapı stop/hedefi onlarla AYNI olduğu için tekrar çizilmez
    assert float(pos.stop) == pytest.approx(plan["stop"]) and "structure_stop" not in kinds and "structure_target" not in kinds
    assert pr["stop"] == pytest.approx(plan["stop"]) and pr["targets"] == [pytest.approx(plan["target"])]
    # stop sonradan değişirse (ör. sıkılaştırma) yapı stopu ayrı çizgi olarak GÖRÜNÜR
    from tradingbot.dashboard import structures_view as sv
    ctx = {"decision": j["decision"], "position": {"stop": float(pos.stop) * 1.01, "targets": []}, "book_id": "pattern_trader"}
    k2 = {e["kind"] for e in sv.elements(ctx)}
    assert {"structure_stop", "structure_target"} <= k2
    assert {e["source"]["params"]["pattern_id"] for e in j["elements"]} == {plan["pattern_id"]}, "yalnız SEÇİLİ kayıt (sade)"
    cross = {c["bot"]: c for c in j["cross"]}
    assert cross["pattern_trader"]["book_id"] == "pattern_trader" and cross["pattern_trader"]["same"], "kendi defteri eşlemede"
    assert cross["t2_trend_regime"]["note"] == "defter bu kurulumda yok" and cross["t2_trend_regime"]["label"] == "T2"
    # grafik API'si aynı katmanı taşır (mumlar gerçek CSV okuma yolundan)
    _write_candles(tmp_path / "data", brk)
    ch = cl.get("/api/chart/LNG", params={"tf": "15m", "market": "futures", "book": "pattern_trader", "trade": pos.id}).json()
    st = ch["structure"]
    assert st["origin"] == "trade" and st["decision"]["trade_id"] == pos.id and st["record_tf"] == "15m"
    assert [e["price"] for e in st["elements"] if e["kind"] == "structure_trigger"] == [pytest.approx(plan["trigger"]["level"])]
    # plan kutusu: bot/coin/yön/yapı/dilim/durum, tetik metni, stop/hedef/geçersizlik, gerçek giriş, TEK neden
    html = cl.get("/api/planbox/LNG", params={"book": "pattern_trader", "market": "futures", "trade": pos.id}).json()["html"]
    assert K.name_tr("BULL_FLAG") in html and "15m" in html and "Şu kapanıştan sonra giriş" in html
    assert "Neden girdi" in html and "Gerçek giriş" in html and "funding" in html
    assert "15m boğa kapanışı üstünde" in html


def test_row_identity_carries_book_market_symbol_timeframe_trade_and_moment(tmp_path):
    cfg, sc, book, plan, pos, p, brk = _engine_trade(tmp_path)
    cl = _client(cfg.state_path, tmp_path / "data")
    html = cl.get("/api/book/pattern_trader", params={"market": "futures"}).json()["positions_html"]
    assert 'data-book="pattern_trader"' in html and 'data-market="futures"' in html and 'data-base="LNG"' in html
    assert 'data-trade="%s"' % pos.id in html and 'data-tf="15m"' in html and 'data-asof="%s"' % pos.opened_at in html
    js = term.ROW_DELEGATE_JS
    assert "el.dataset.tf" in js and "el.dataset.trade" in js and "el.dataset.asof" in js and "&trade=" in js


def test_a_closed_trade_keeps_its_entry_moment_and_shows_exit_reason_and_net_after_cost(tmp_path):
    cfg, sc, book, plan, pos, p, brk = _engine_trade(tmp_path)
    tid = pos.id
    store = StructureStore(cfg.state_path)
    entry_row = [r for r in store.decisions(symbol=SYM, book_id="pattern_trader") if r.get("trade_id") == tid and r.get("action") == "ENTER"]
    assert len(entry_row) == 1
    CLOCK[0] += 10 * M15
    _set_mark(p, SYM, float(plan["target"]) * 1.001)
    assert len(sc.exit_check()) == 1
    # sonraki taramalar yeni kararlar yazabilir; işlemin GİRİŞ anı değişmez
    sc.scan_cycle(now_ms=CLOCK[0])
    cl = _client(cfg.state_path, tmp_path / "data")
    j = cl.get("/api/structures/LNG", params={"book": "pattern_trader", "market": "futures", "trade": tid}).json()
    assert j["decision"]["decision_id"] == entry_row[0]["decision_id"] and j["decision"]["at_ms"] == entry_row[0]["at_ms"]
    html = cl.get("/api/planbox/LNG", params={"book": "pattern_trader", "market": "futures", "trade": tid}).json()["html"]
    assert "Neden çıktı" in html and "maliyet sonrası net" in html and "Gerçek giriş" in html
    h = book.ledger.history_dicts()[-1]
    assert ("%.4f" % float(h["net_pnl"]))[:6] in html.replace(",", ""), "net sonuç defterin KENDİ kaydından"
    # kapanış bağlantısı da dilimi taşır
    closed = cl.get("/api/book/pattern_trader", params={"market": "futures"}).json()["closed_html"]
    assert "trade=%s" % tid in closed and "tf=15m" in closed


def _w(root: Path, name: str, doc) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


def _two_books(tmp_path: Path, *, trade_features=None):
    pos = {"symbol": "ETH/USDT", "side": "LONG", "entry_avg": 100.0, "entry": 100.0, "qty": 0.1, "stop": 95.0, "targets": [],
           "leverage": 1, "opened_at": "2026-09-16T08:00:00+00:00", "last_price": 101.0, "id": "F00007"}
    hist = [{"id": "F00003", "symbol": "ETH/USDT", "side": "LONG", "entry": 90.0, "exit_price": 92.0, "net_pnl": 0.15,
             "exit_reason": "stop", "opened_at": "2026-09-15T07:00:00+00:00", "closed_at": "2026-09-15T09:00:00+00:00",
             "funding": -0.01, "features": dict(trade_features or {})}]
    _w(tmp_path, "futures_ledger.json", {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "100",
                                         "positions": {"ETH/USDT": dict(pos, id="F00001")}, "history": []})
    _w(tmp_path, "strategy_paper_index.json", {"books": [{"key": "strategy_paper", "name": "t2_trend_regime", "summary_file": "strategy_paper.json"}]})
    _w(tmp_path, "strategy_paper/futures_ledger.json", {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "100",
                                                        "positions": {"ETH/USDT": pos}, "history": hist})
    _w(tmp_path, "strategy_paper.json", {"key": "strategy_paper", "name": "t2_trend_regime", "summary": {}, "positions": {}, "history_tail": []})


def test_overlapping_exposure_across_books_is_visible_and_not_pooled(tmp_path):
    _two_books(tmp_path)
    cl = _client(tmp_path, tmp_path / "data")
    html = cl.get("/api/planbox/ETH", params={"book": "strategy_paper", "market": "futures"}).json()["html"]
    assert "Aynı coinde açık pozisyonlar" in html and "Ana bot LONG" in html and "T2" in html
    assert "toplanmaz" in html and "Yapı:" in html, "kayıt yoksa bu açıkça yazılır"


def test_unreconciled_funding_is_shown_separately_from_the_net_result(tmp_path):
    _two_books(tmp_path, trade_features={"funding_coverage": {"contract": "funding_settlement_v2", "complete": False,
                                                              "missing": 2, "reason": "RATES_MISSING"}})
    cl = _client(tmp_path, tmp_path / "data")
    html = cl.get("/api/planbox/ETH", params={"book": "strategy_paper", "market": "futures", "trade": "F00003"}).json()["html"]
    assert "2 uzlaşmamış dönem" in html and "net sonuç kesin değil" in html and "maliyet sonrası net" in html


def test_cross_bot_mapping_lists_all_five_bots_and_marks_the_same_record(tmp_path):
    _two_books(tmp_path)
    st = StructureStore(tmp_path)
    prim = {"pattern_id": "abc123", "name": "BULL_FLAG", "timeframe": "1d", "status": "CONFIRMED", "side": "LONG",
            "trigger": {"rule": "close_above", "level": 101.0}, "invalidation": {"rule": "close_below", "level": 94.0},
            "stop": 93.5, "targets": [110.0]}
    st.record_decision({"bot": "t2_trend_regime", "action": "ENTER", "reason_code": "COMPATIBLE_CONFIRMED", "side": "LONG",
                        "pattern_ids": ["abc123"], "primary": prim, "text_tr": "Girdi: 1d BULL_FLAG teyitli."},
                       book_id="strategy_paper", market="USDM_PERP", symbol="ETH/USDT", at_ms=1_790_000_000_000,
                       trade_id="F00007")                  # T2'nin AÇIK pozisyonunun giriş satırı
    st.record_decision({"bot": "main", "action": "WAIT", "reason_code": "OPPOSING_CONFIRMED", "side": "SHORT",
                        "pattern_ids": ["abc123"], "primary": prim, "text_tr": "Girmedi: karşı yapı teyitli."},
                       book_id="main", market="USDM_PERP", symbol="ETH/USDT", at_ms=1_790_000_000_000)
    j = _client(tmp_path, tmp_path / "data").get("/api/structures/ETH", params={"book": "strategy_paper", "market": "futures"}).json()
    bots = [c["bot"] for c in j["cross"]]
    assert bots == ["main", "t2_trend_regime", "m2_tsmom28", "b1_box_fade", "pattern_trader"]
    same = {c["bot"]: c["same"] for c in j["cross"]}
    assert same["main"] and same["t2_trend_regime"] and not same["pattern_trader"]
    notes = {c["bot"]: c["note"] for c in j["cross"]}
    assert notes["m2_tsmom28"] == "defter bu kurulumda yok"
    html = _client(tmp_path, tmp_path / "data").get("/api/planbox/ETH", params={"book": "strategy_paper", "market": "futures"}).json()["html"]
    assert "Aynı yapı beş botta" in html and "✔ aynı kayıt" in html and "Neden girdi" in html


def test_spot_scope_never_shows_a_futures_structure_row(tmp_path):
    _two_books(tmp_path)
    StructureStore(tmp_path).record_decision({"bot": "main", "action": "ENTER", "pattern_ids": ["x1"], "primary": {"pattern_id": "x1"},
                                              "text_tr": "Girdi (futures)."}, book_id="main", market="USDM_PERP",
                                             symbol="ETH/USDT", at_ms=1_790_000_000_000)
    j = _client(tmp_path, tmp_path / "data").get("/api/structures/ETH", params={"book": "main", "market": "spot"}).json()
    assert j["market_id"] == "SPOT" and j["decision"] is None and j["elements"] == []


def test_every_catalog_name_has_a_turkish_label():
    assert [n for n in K.all_names() if n not in K.NAME_TR] == []


def test_chart_default_view_is_uncluttered_with_only_the_selected_structure_layer_on():
    from tradingbot.dashboard.chart_js import CHART_JS
    assert "structure:1" in CHART_JS and "patterns:0" in CHART_JS and "levels:0" in CHART_JS
    assert "window.__chartTrade" in CHART_JS and "&trade=" in CHART_JS


def test_coin_page_opens_the_chart_with_the_clicked_trade_identity(tmp_path):
    cfg, sc, book, plan, pos, p, brk = _engine_trade(tmp_path)
    cl = _client(cfg.state_path, tmp_path / "data")
    html = cl.get("/coin/LNG", params={"book": "pattern_trader", "market": "futures", "tf": "15m", "trade": pos.id,
                                       "as_of": pos.opened_at}).text
    assert 'window.__chartTrade="%s"' % pos.id in html and 'window.__chartAsOf="%s"' % pos.opened_at in html
    assert "Yapı kararı" in html and K.name_tr("BULL_FLAG") in html and "Neden girdi" in html
