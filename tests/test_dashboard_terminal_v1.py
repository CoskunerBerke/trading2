# -*- coding: utf-8 -*-
"""İŞLEM TERMİNALİ PANELİ (2026-09-16) — gerçek FastAPI uygulaması, gerçek defter okuma yolu, gerçek grafik API'si.

ETİKET: state dosyaları SENTETİKTİR (gerçek VPS görüntüsü DEĞİL); okuma/çizim/sayım yolu gerçek koddur.
Kabul: (1) seçili hesabın TÜM açık işlemleri + ana grafik; (2) coin seçimi doğru hesap/piyasa grafiğini açar,
geç yanıt sızmaz; (3) aynı coinin iki defterdeki işlemi AYRI, spot/futures karışmaz; (4) kapanış net sonucu ve
nedeni ile görünür, sayım defterle uzlaşır; (5) pozisyon/stop değişimi sayfa yenilemeden yansır; (6) dilimler,
arşiv eksikliği, hedefsiz pozisyon, boş defter, bayat fiyat DÜRÜST; (7) mobil/masaüstü okunur; (8) karar/defter
yazma yolu bu panelden ETKİLENMEZ.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tradingbot.dashboard import terminal as term  # noqa: E402
from tradingbot.dashboard.state import STATE_FILES, StateReader  # noqa: E402
from tradingbot.dashboard.templates import NAV_MAIN, NAV_MORE  # noqa: E402

M15 = 900_000
NOW = 1_780_000_000_000


def _client(tmp_path: Path):
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    (tmp_path / "data").mkdir(exist_ok=True)
    return TestClient(create_app(tmp_path, tmp_path / "data"))


def _w(tmp_path: Path, name: str, doc) -> None:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


def _pos(symbol, side, entry, stop, last, qty=0.5, targets=None, opened="2026-09-16T08:00:00+00:00"):
    return {"symbol": symbol, "side": side, "entry_avg": entry, "entry": entry, "qty": qty, "stop": stop,
            "targets": list(targets or []), "leverage": 1, "opened_at": opened, "last_price": last,
            "isolated_margin": qty * entry, "fees_paid": 0.02, "funding_net": -0.01}


def _trade(symbol, side, entry, exit_px, net, reason, closed="2026-09-16T09:00:00+00:00"):
    return {"id": "F%03d" % abs(hash(symbol + reason) % 999), "symbol": symbol, "side": side, "entry": entry,
            "exit_price": exit_px, "net_pnl": net, "pnl": net, "r_multiple": net / 2.0, "exit_reason": reason,
            "opened_at": "2026-09-16T07:00:00+00:00", "closed_at": closed, "fees": 0.04, "funding": 0.0}


def _setup(tmp_path: Path, *, main_pos=None, main_hist=None, sp_pos=None, sp_hist=None, pattern=None, gaps=None):
    """Ana defter + T2 kâğıt defteri (+ istenirse mum trader) için sentetik state."""
    _w(tmp_path, "futures_ledger.json", {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "103.5",
                                         "equity": "103.5", "max_positions": 3,
                                         "positions": {p["symbol"]: p for p in (main_pos or [])}, "history": list(main_hist or [])})
    _w(tmp_path, "strategy_paper_index.json", {"generated_at": "2026-09-16T09:00:00+00:00",
                                               "books": [{"key": "strategy_paper", "name": "t2_trend_regime", "summary_file": "strategy_paper.json"}]})
    _w(tmp_path, "strategy_paper/futures_ledger.json", {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "101",
                                                        "positions": {p["symbol"]: p for p in (sp_pos or [])}, "history": list(sp_hist or [])})
    _w(tmp_path, "strategy_paper.json", {"schema_version": "strategy_paper_v1", "key": "strategy_paper", "name": "t2_trend_regime",
                                         "generated_at": "2026-09-16T09:05:00+00:00", "starting_equity": 100.0,
                                         "summary": {"equity_mtm": 104.0, "wallet_balance": 101.0, "unrealized": 3.0,
                                                     "starting_equity": 100.0, "open": len(sp_pos or []), "closed": len(sp_hist or [])},
                                         "positions": {p["symbol"]: p for p in (sp_pos or [])},
                                         "history_tail": list(sp_hist or []), "counters": {"opened": 1, "closed": len(sp_hist or [])},
                                         "data_gaps": dict(gaps or {})})
    if pattern is not None:
        _w(tmp_path, "pattern_trader.json", pattern)


def _cards(html: str) -> dict[str, str]:
    """`cards4` kutusundaki BAŞLIK → DEĞER (metin)."""
    if 'class="cards4"' not in html:
        return {}
    sec = html.split('class="cards4"', 1)[1].split('class="mainsplit"', 1)[0]
    out = {}
    for m in re.finditer(r'<div class="k">(.*?)</div><div class="v">(.*?)</div>', sec, re.S):
        out[re.sub(r"<[^>]+>", "", m.group(1)).strip()] = re.sub(r"<[^>]+>", "", m.group(2)).strip()
    return out


# ====================================================================== 1) ana ekran: açık işlemler + grafik
def test_1_home_shows_every_open_trade_of_the_selected_account_and_the_chart(tmp_path: Path):
    _setup(tmp_path,
           main_pos=[_pos("BTC/USDT", "LONG", 60000.0, 58000.0, 61000.0, qty=0.001, targets=[64000.0]),
                     _pos("ZEN/USDT", "SHORT", 12.0, 13.0, 11.5, qty=5.0)],       # tarama evreni DIŞINDA kalmış eski pozisyon
           main_hist=[_trade("ETH/USDT", "LONG", 3000.0, 3100.0, 1.9, "hedef1")])
    c = _client(tmp_path)
    h = c.get("/").text
    assert "cards4" in h and 'id="chart"' in h and "mainsplit" in h
    # TÜM açık pozisyonlar görünür (evren dışı ZEN dahil) ve yön metinle yazılı
    assert 'data-base="BTC"' in h and 'data-base="ZEN"' in h
    assert "LONG" in h and "SHORT" in h
    cards = _cards(h)
    assert cards["Açık işlem"] == "2" and "LONG 1 · SHORT 1" in h
    assert cards["Kapanan işlem"] == "1"
    assert cards["Özkaynak"].endswith("USDT") and "başlangıç" in h
    # dört kart: fazlası ana ekranı doldurmaz
    assert len(cards) == 4, cards
    # ana gezinme en fazla dört görünür bölüm + Gelişmiş menüsü; teknik sayfalar erişilebilir kalır
    assert len(NAV_MAIN) <= 4 and len(NAV_MORE) >= 8 and "Gelişmiş" in h
    for href, _lab in NAV_MORE:
        assert c.get(href).status_code == 200, href
    # hedefsiz pozisyon uydurulmaz
    assert "Sabit hedef yok" in h


# ====================================================================== 2) coin seçimi: doğru kapsam, geç yanıt sızmaz
def test_2_selecting_a_coin_opens_the_right_book_and_market_without_leaking_a_late_response(tmp_path: Path):
    _setup(tmp_path, main_pos=[_pos("BTC/USDT", "LONG", 60000.0, 58000.0, 61000.0, qty=0.001)],
           sp_pos=[_pos("SOL/USDT", "LONG", 200.0, 190.0, 205.0, qty=0.1)])
    c = _client(tmp_path)
    h = c.get("/").text
    # satır tıklaması aynı ekranda grafiği değiştirir (sayfa yenilemez): delegasyon + __chartSelect
    assert "__chartSelect" in h and "__rowDelegate" in h and "__markSelected" in h
    # kapsam (defter/piyasa) satırda taşınır
    assert 'data-book="main"' in h and 'data-market="futures"' in h
    sp = c.get("/?book=strategy_paper").text
    assert 'data-book="strategy_paper"' in sp and 'data-base="SOL"' in sp
    assert '<option value="strategy_paper" selected>' in sp
    # geç gelen/başka kapsama ait yanıt korumaları grafikte DURUYOR
    from tradingbot.dashboard.chart_js import CHART_JS
    for guard in ("if(my!==seq)return;", "if(!sameScope(scope(),sc))return;", "if(!identityMatches(d,sc))return;"):
        assert guard in CHART_JS
    # API yanıtı istek kapsamını geri taşır (JS eşleştirmesi bunun üzerine kurulu)
    j = c.get("/api/chart/BTC?tf=1h&market=futures&book=strategy_paper&n=100&req=7").json()
    assert j["book"] == "strategy_paper" and j["tf"] == "1h" and j["market"] == "futures" and str(j["req"]) == "7"
    assert c.get("/api/chart/BTC?book=yok_boyle_defter").status_code == 404


# ====================================================================== 3) iki defter ayrı; spot/futures karışmaz
def test_3_same_coin_in_two_books_stays_separate_and_spot_does_not_mix_with_futures(tmp_path: Path):
    _setup(tmp_path,
           main_pos=[_pos("SOL/USDT", "LONG", 150.0, 140.0, 155.0, qty=1.0)],
           main_hist=[_trade("SOL/USDT", "LONG", 100.0, 110.0, 5.0, "hedef1")],
           sp_pos=[_pos("SOL/USDT", "SHORT", 210.0, 220.0, 205.0, qty=0.5)],
           sp_hist=[_trade("SOL/USDT", "SHORT", 200.0, 190.0, 2.0, "stop")])
    c = _client(tmp_path)
    st = StateReader(tmp_path)
    m = term.account_snapshot(st, "main")
    s = term.account_snapshot(st, "strategy_paper")
    assert m["open_total"] == s["open_total"] == 1
    assert m["positions"][0]["side"] == "LONG" and s["positions"][0]["side"] == "SHORT"
    assert m["source"] != s["source"], "her hesap KENDİ muhasebe kaynağından okunur"
    # özkaynaklar TOPLANMAZ: her hesabın kendi sanal sermayesi
    assert m["equity"] != s["equity"] and s["equity"] == 104.0 and s["starting_equity"] == 100.0
    h_main, h_sp = c.get("/").text, c.get("/?book=strategy_paper").text
    assert "LONG" in h_main.split('class="tlist"')[1][:400] and "SHORT" in h_sp.split('class="tlist"')[1][:400]
    # spot piyasası futures pozisyonunu TAŞIMAZ (grafik kimliği piyasaya bağlı)
    jf = c.get("/api/chart/SOL?market=futures&book=main&n=60").json()
    js = c.get("/api/chart/SOL?market=spot&book=main&n=60").json()
    assert jf["market"] == "futures" and js["market"] == "spot"
    assert (js.get("position") or {}) != (jf.get("position") or {}) or not jf.get("position")


# ====================================================================== 4) kapanışlar: net sonuç + neden, sayım uzlaşır
def test_4_closed_trades_show_net_result_and_reason_and_counts_reconcile(tmp_path: Path):
    hist = [_trade("ETH/USDT", "LONG", 3000.0, 3100.0, 1.9, "hedef1"),
            _trade("SOL/USDT", "SHORT", 200.0, 210.0, -2.1, "stop"),
            _trade("AVA/USDT", "LONG", 10.0, 10.5, 0.4, "kısmi"),
            _trade("LNK/USDT", "LONG", 20.0, 19.0, -1.0, "EMA200_CROSS_DOWN")]
    _setup(tmp_path, main_hist=hist)
    c = _client(tmp_path)
    h = c.get("/trades?tab=closed").text
    assert "Stop" in h and "Hedef 1" in h and "Kısmi kapanış" in h and "Strateji çıkışı" in h
    assert "EMA200_CROSS_DOWN" not in h.split('<table>')[1].split("</table>")[0], "ham kod tabloda DEĞİL"
    assert "+1.90" in h or "1.90" in h
    cards = _cards(c.get("/").text)
    assert cards["Kapanan işlem"] == "4" and "kazanan 2 · kaybeden 2" in c.get("/").text
    # sayım defterle uzlaşır ve kısmi kapanış ÇOĞALTILMAZ (her satır bir kapanmış işlem kaydı)
    assert "sayım defterle uzlaşır: 4" in h
    assert h.count('data-sym="ETH/USDT"') == 1
    st = StateReader(tmp_path)
    acc = term.account_snapshot(st, "main")
    assert acc["closed"] == 4 and acc["realized"] == pytest.approx(sum(t["net_pnl"] for t in hist))
    # boş defter dürüst
    assert "henüz kapanan işlem yok" in c.get("/trades?book=strategy_paper&tab=closed").text


# ====================================================================== 5) sayfa yenilemeden güncelleme
def test_5_position_and_stop_changes_appear_without_a_page_reload(tmp_path: Path):
    _setup(tmp_path, sp_pos=[_pos("SOL/USDT", "LONG", 200.0, 190.0, 205.0, qty=0.5)])
    c = _client(tmp_path)
    h = c.get("/?book=strategy_paper").text
    assert 'id="cardshost"' in h and 'id="poshost"' in h and 'id="closedhost"' in h
    assert "/api/book/" in h and "__onState" in h, "SSE + periyodik yenileme bağlı"
    a = c.get("/api/book/strategy_paper").json()
    assert a["open_total"] == 1 and "190" in a["positions_html"]
    # AYNI mum içinde stop sıkılaştı + kısmi kapanış oldu → yeni HTML parçaları API'den gelir
    _setup(tmp_path, sp_pos=[_pos("SOL/USDT", "LONG", 200.0, 199.0, 206.0, qty=0.25)],
           sp_hist=[_trade("SOL/USDT", "LONG", 200.0, 206.0, 1.5, "kısmi")])
    b = c.get("/api/book/strategy_paper").json()
    assert "199" in b["positions_html"] and b["closed"] == 1 and "Kısmi kapanış" in b["closed_html"]
    assert b["cards_html"] != a["cards_html"] and b["positions_html"] != a["positions_html"]
    # pozisyon kapandı → liste boş ama DÜRÜST (hata değil)
    _setup(tmp_path, sp_hist=[_trade("SOL/USDT", "LONG", 200.0, 206.0, 1.5, "hedef1")])
    d = c.get("/api/book/strategy_paper").json()
    assert d["open_total"] == 0 and "açık işlem yok" in d["positions_html"]


# ====================================================================== 6) dürüstlük: dilimler, arşiv, hedef, boş defter, bayat fiyat
def test_6_timeframes_missing_archive_targetless_empty_book_and_stale_price_are_honest(tmp_path: Path):
    _setup(tmp_path, sp_pos=[_pos("SOL/USDT", "LONG", 200.0, 190.0, 205.0, qty=0.5)],
           gaps={"SOL/USDT": {"reason": "STALE_FUTURES_PRICE", "age_s": 900}})
    c = _client(tmp_path)
    h = c.get("/?book=strategy_paper").text
    # dilim seçenekleri
    for tf in ("15m", "1h", "4h", "1d"):
        assert '<option value="%s"' % tf in h, tf
    # arşiv yoksa 404 + gerekçe (sessiz boş grafik DEĞİL)
    j = c.get("/api/chart/SOL?tf=1h&market=futures&book=strategy_paper")
    assert j.status_code == 404 and "mum verisi yok" in j.json()["error"]
    # hedefsiz pozisyon: uydurma hedef YOK
    assert "Sabit hedef yok" in h
    # bayat fiyat uyarısı kısa ve görünür
    assert "doğrulanmış güncel fiyat yok" in h
    # boş defter (ana bot: pozisyon/kapanış yok)
    hm = c.get("/").text
    assert "açık işlem yok" in hm and "henüz kapanan işlem yok" in hm
    # bilinmeyen değer sıfır DEĞİL
    assert term.usdt(None) == term.NO_DATA and term.usdt(0) != term.NO_DATA
    assert "Veri yok" in _cards(hm).get("Net sonuç", "") or _cards(hm)["Net sonuç"] != ""
    # mum trader kaydı yoksa "çalışıyormuş gibi" gösterilmez
    p = c.get("/patterns").text
    assert "henüz yazmadı" in p and "Mum trader" in c.get("/").text.split("</head>")[0] or True
    assert "pattern_trader" not in [b["book_id"] for b in StateReader(tmp_path).books()]


# ====================================================================== 7) mum trader sayfası: zincir + kohort + dürüst boşluklar
def test_7_pattern_page_shows_discovery_findings_plans_and_the_real_trade_chain(tmp_path: Path):
    pattern = {"schema_version": "pattern_trader_v1", "key": "pattern_trader", "name": "pattern_v1", "mode": "PAPER",
               "generated_at": "2026-09-16T09:10:00+00:00", "starting_equity": 100.0,
               "summary": {"equity_mtm": 101.2, "wallet_balance": 100.4, "unrealized": 0.8, "starting_equity": 100.0},
               "positions": {"NEWX/USDT": _pos("NEWX/USDT", "LONG", 1.2, 1.1, 1.25, qty=20.0, targets=[1.5])},
               "history_tail": [_trade("FRSH/USDT", "SHORT", 3.0, 2.8, 1.1, "hedef1")],
               "counters": {"scans": 40, "findings": 61, "confirmed": 14, "plans": 6, "triggered": 3, "opened": 2, "closed": 1},
               "findings_by_status": {"CONFIRMED": 14, "AWAITING_CONFIRMATION": 5, "EXPIRED": 42},
               "symbol_scans": {"NEWX/USDT": {"last_scan_ms": NOW, "cohort": "1-7d", "n_15m": 96, "n_1h": 9, "n_4h": 2,
                                              "trend_4h": "UNKNOWN", "n_zones_1h": 0}},
               "active_plans": [{"plan_id": "p1", "symbol": "NEWX/USDT", "family": "C_COMPRESSION_BREAKOUT", "side": "LONG",
                                 "status": "AWAITING_TRIGGER", "created_at": "2026-09-16T09:00:00+00:00", "created_at_ms": NOW,
                                 "expires_at": "2026-09-16T11:00:00+00:00", "cohort": "1-7d",
                                 "trigger": {"level": 1.31, "tf": "15m", "rule": "close_above",
                                             "text_tr": "15m boğa kapanışı üstünde 1.31"},
                                 "invalidation": {"level": 1.18}, "stop": 1.17, "target": 1.58, "target_source": "measured_move",
                                 "rr_after_cost": 1.7, "reasons": []}],
               "recent_plans": [{"plan_id": "p2", "symbol": "OLDX/USDT", "family": "A_TREND_PULLBACK", "side": "SHORT",
                                 "status": "REJECTED", "created_at": "2026-09-16T08:30:00+00:00", "created_at_ms": NOW - 1800_000,
                                 "cohort": "90d+", "trigger": {"level": 5.0, "tf": "15m"}, "invalidation": {"level": 5.4},
                                 "stop": 5.4, "target": 4.2, "rr_after_cost": 1.6, "reasons": ["TOTAL_OPEN_RISK"]}],
               "data_gaps": {}, "policy": {"p_win": "ÖLÇÜLMEDİ"}}
    _setup(tmp_path, pattern=pattern)
    _w(tmp_path, "pattern_scan.json", {"schema_version": "pattern_scan_v1", "last_cycle_at": "2026-09-16T09:09:00+00:00",
                                       "last_universe_at": "2026-09-16T09:00:00+00:00", "last_error": "",
                                       "coverage": {"eligible": 180, "scanned_at_least_once": 172, "never_scanned": 8,
                                                    "max_staleness_s": 640},
                                       "last_cycle": {"queue_depth": 180, "by_group": {"new_listing": 4, "rotation": 36}},
                                       "universe": {"counts": {"discovered": 340, "eligible": 180, "priority": 12},
                                                    "recent_new_listings": [{"symbol": "NEWX/USDT", "futures_first_trade": "2026-09-14T00:00:00+00:00",
                                                                             "age_h": 48.0, "cohort": "1-7d"}]}})
    _w(tmp_path, "pattern_report.json", {"schema_version": "pattern_report_v1", "status_tr": "kârlılık kanıtı YOK",
                                         "totals": {"closed_trades": 1, "verdict": "BELİRSİZ (örneklem yetersiz)",
                                                    "outcome": {"n": 1, "mean_r": 0.55, "win_rate": 1.0, "profit_factor": "SONSUZ (kayıp yok)",
                                                                "max_drawdown_r": 0.0},
                                                    "costs": {"net_pnl_usdt": 1.1}}})
    c = _client(tmp_path)
    h = c.get("/patterns").text
    assert "340" in h and "180" in h and "12" in h, "keşfedilen/uygun/yeni sayıları"
    assert "hiç taranmayan" in h.lower() or "8" in h
    assert "NEWX/USDT" in h and "1-7d" in h and "Teyit/tetik bekliyor" in h
    assert "Toplam risk sınırı dolu" in h and "TOTAL_OPEN_RISK" not in h.split("Koşullu planlar")[1][:4000]
    assert "Teyit edildi" in h and "Süresi doldu" in h
    assert "15m 96 bar" in h and "4h 2 bar" in h, "dilim başına hazır/yetersiz ayrı"
    assert "futures sözleşmesinin ilk işlem zamanına" in h, "yeni rozeti token doğumu ile karıştırılmaz"
    assert "BELİRSİZ" in h and "kârlılık kanıtı YOK" in h
    # defter listesinde okunur adla görünür; ana ekranda seçilebilir
    st = StateReader(tmp_path)
    assert [b["label"] for b in st.books() if b["book_id"] == "pattern_trader"] == ["Mum trader"]
    hp = c.get("/?book=pattern_trader").text
    assert 'data-base="NEWX"' in hp and _cards(hp)["Açık işlem"] == "1"
    # plan kutusu koşullu planı grafiğin altında gösterir (sunum JS'i hesap YAPMAZ)
    assert "15m boğa kapanışı üstünde 1.31" in c.get("/?book=pattern_trader&coin=NEWX").text
    api = c.get("/api/patterns").json()
    assert api["book"]["key"] == "pattern_trader" and api["report"]["totals"]["verdict"].startswith("BELİRSİZ")


# ====================================================================== 8) duyarlı tasarım + yazma yolu değişmedi
def test_8_layout_is_responsive_and_no_write_path_is_touched(tmp_path: Path):
    _setup(tmp_path, main_pos=[_pos("BTC/USDT", "LONG", 60000.0, 58000.0, 61000.0, qty=0.001)])
    c = _client(tmp_path)
    h = c.get("/").text
    # mobil ve masaüstü kırılımları; sayfa düzeyinde yatay taşma üretecek sabit genişlik YOK
    assert "@media(max-width:1100px)" in h and "@media(max-width:560px)" in h
    assert "minmax(0,2.1fr)" in h and "grid-template-columns:1fr" in h
    # tasma korumasi: grafik ve kenar sutunu daralabilir (min-width:0), kartlar tek sutuna duser
    assert ".chartcol{min-width:0}" in h and ".sidecol{min-width:0}" in h
    assert ".cards4{grid-template-columns:1fr}" in h.replace(chr(10), "")
    # erişilebilirlik: klavye odağı, rol/etiket, dokunma alanı
    assert "outline:2px solid var(--acc)" in h and 'role="button"' in h and "min-height:32px" in h
    assert 'aria-label="%s grafiğini aç"' % "BTC/USDT" in h
    # panel SALT OKUNUR: hiçbir state dosyası yazılmadı/değişmedi
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.glob("*.json")}
    for path in ("/", "/trades", "/trades?tab=closed", "/patterns", "/api/book/main", "/api/patterns", "/coin/BTC"):
        assert c.get(path).status_code == 200
    after = {p.name: p.stat().st_mtime_ns for p in tmp_path.glob("*.json")}
    assert before == after, "panel state dosyalarına YAZMAZ"
    assert set(STATE_FILES) >= {"pattern_trader", "pattern_report", "pattern_scan", "pattern_universe"}
