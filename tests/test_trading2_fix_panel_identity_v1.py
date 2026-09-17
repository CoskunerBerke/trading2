# -*- coding: utf-8 -*-
"""PANEL KİMLİK SÖZLEŞMESİ (BULGU 5) — defter/piyasa/işlem kimliği uçtan uca korunuyor mu?

ETİKET: buradaki state dosyaları SENTETİKTİR (gerçek VPS görüntüsü DEĞİL); okunan/çizilen kod GERÇEKTİR —
`tradingbot.dashboard.terminal` ve `tradingbot.dashboard.state` doğrudan çağrılır, kopya/AST çıkarımı YOKTUR.

Kurgu: AYNI sembol (SOL/USDT) BEŞ ayrı yerde FARKLI kayıtla durur — ana spot, ana futures, T2, M2 ve mum
trader defteri. Bir kayıt başka bir kapsamda görünüyorsa bu kesinlikle bulaşmadır, tesadüf değildir:

    ana spot     LONG  giriş 101.11   (portfolio.json)
    ana futures  SHORT giriş 202.22   (futures_ledger.json)
    T2           LONG  giriş 303.33   (strategy_paper/futures_ledger.json)
    M2           SHORT giriş 404.44   (strategy_paper_m2/futures_ledger.json)
    mum trader   LONG  giriş 505.55   + KOŞULLU PLAN (pattern_trader.json)

`test_defect_*` testleri BUGÜNKÜ kodda DÜŞER ve kusuru çivi gibi sabitler. `test_counterexample_*` testleri
bugün GEÇER ve onarımdan sonra da GEÇMELİDİR (doğru olan davranış: gerilemesin).
"""
from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tradingbot.dashboard import terminal as term  # noqa: E402
from tradingbot.dashboard.state import StateReader  # noqa: E402

#: Plan metni yalnız mum trader defterine aittir; başka bir kapsamda görünmesi bulaşmadır.
PLAN_TEXT = "15m boğa kapanışı üstünde 555.55"
#: Girişler sembol aynı olsa bile defter/piyasa başına AYRIDIR (ondalıklar kimlik etiketidir).
SPOT_ENTRY, FUT_ENTRY, T2_ENTRY, M2_ENTRY, PAT_ENTRY = "101.11", "202.22", "303.33", "404.44", "505.55"


# --------------------------------------------------------------------------- sentetik state (yalnız tmp_path)
def _w(root: Path, name: str, doc) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


def _pos(symbol, side, entry, stop, last, qty=1.0, targets=None):
    return {"symbol": symbol, "side": side, "entry_avg": entry, "entry": entry, "qty": qty, "stop": stop,
            "targets": list(targets or []), "leverage": 1, "opened_at": "2026-09-16T08:00:00+00:00",
            "last_price": last, "isolated_margin": qty * entry, "fees_paid": 0.02, "funding_net": -0.01}


def _trade(tid, symbol, side, entry, exit_px, net, reason):
    return {"id": tid, "symbol": symbol, "side": side, "entry": entry, "exit_price": exit_px, "net_pnl": net,
            "pnl": net, "r_multiple": net / 2.0, "exit_reason": reason, "opened_at": "2026-09-16T07:00:00+00:00",
            "closed_at": "2026-09-16T09:00:00+00:00", "fees": 0.04, "funding": 0.0}


def _paper_book(root: Path, key: str, name: str, pos: list[dict], hist: list[dict]) -> None:
    """Kâğıt defter: YETKİLİ defter dosyası + defterin kendi özeti (aynı yazarın projeksiyonu)."""
    _w(root, "%s/futures_ledger.json" % key, {"schema_version": "futures_v2", "starting_equity": "100",
                                              "wallet_balance": "100", "positions": {p["symbol"]: p for p in pos},
                                              "history": list(hist)})
    _w(root, "%s.json" % key, {"schema_version": "strategy_paper_v1", "key": key, "name": name,
                               "generated_at": "2026-09-16T09:05:00+00:00", "starting_equity": 100.0,
                               "summary": {"equity_mtm": 100.0 + len(pos), "wallet_balance": 100.0, "unrealized": 0.5,
                                           "starting_equity": 100.0, "open": len(pos), "closed": len(hist)},
                               "positions": {p["symbol"]: p for p in pos}, "history_tail": list(hist),
                               "counters": {"opened": len(pos), "closed": len(hist)}, "data_gaps": {}})


def _setup(root: Path) -> None:
    """AYNI coin beş kapsamda FARKLI kayıtla: bulaşma tespiti tek bir sayıya indirgenir."""
    # --- ana bot / futures
    _w(root, "futures_ledger.json", {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "103.5",
                                     "equity": "103.5", "max_positions": 3,
                                     "positions": {"SOL/USDT": _pos("SOL/USDT", "SHORT", 202.22, 210.0, 200.0)},
                                     "history": [_trade("FUT1", "SOL/USDT", "SHORT", 222.22, 212.22, 1.25, "hedef1")]})
    # --- ana bot / spot (spot_ledger.json yok → gerçek `portfolio.json` yolu)
    _w(root, "portfolio.json", {"cash": 50.0,
                                "positions": {"SOL/USDT": {"symbol": "SOL/USDT", "units": 3.0, "entry_price": 101.11,
                                                           "last_price": 105.0, "entry_time": "2026-09-16T06:00:00+00:00"}},
                                "history": [_trade("SPT1", "SOL/USDT", "LONG", 111.11, 121.11, 2.5, "manuel")]})
    # --- iki kâğıt defter (T2 ve M2) kayıtlı
    _w(root, "strategy_paper_index.json", {"generated_at": "2026-09-16T09:00:00+00:00", "books": [
        {"key": "strategy_paper", "name": "t2_trend_regime", "summary_file": "strategy_paper.json"},
        {"key": "strategy_paper_m2", "name": "m2_tsmom28", "summary_file": "strategy_paper_m2.json"}]})
    _paper_book(root, "strategy_paper", "t2_trend_regime",
                [_pos("SOL/USDT", "LONG", 303.33, 290.0, 310.0)],
                [_trade("T2A", "SOL/USDT", "LONG", 333.33, 343.33, 3.5, "EMA200_CROSS_DOWN")])
    _paper_book(root, "strategy_paper_m2", "m2_tsmom28",
                [_pos("SOL/USDT", "SHORT", 404.44, 420.0, 400.0)],
                [_trade("M2A", "SOL/USDT", "SHORT", 444.44, 434.44, 4.5, "M2_TSMOM28_CROSS_DOWN")])
    # --- mum trader defteri: pozisyon + KOŞULLU plan (yalnız bu deftere ait)
    _w(root, "pattern_trader.json", {"schema_version": "pattern_trader_v1", "key": "pattern_trader",
                                     "name": "pattern_v1", "mode": "PAPER", "generated_at": "2026-09-16T09:10:00+00:00",
                                     "starting_equity": 100.0,
                                     "summary": {"equity_mtm": 101.2, "wallet_balance": 100.4, "unrealized": 0.8,
                                                 "starting_equity": 100.0},
                                     "positions": {"SOL/USDT": _pos("SOL/USDT", "LONG", 505.55, 495.0, 510.0)},
                                     "history_tail": [_trade("PT1", "SOL/USDT", "LONG", 555.55, 565.55, 5.5, "hedef1")],
                                     "counters": {"plans": 1, "opened": 1, "closed": 1},
                                     "active_plans": [{"plan_id": "p1", "symbol": "SOL/USDT", "side": "LONG",
                                                       "family": "C_COMPRESSION_BREAKOUT", "status": "AWAITING_TRIGGER",
                                                       "created_at": "2026-09-16T09:00:00+00:00",
                                                       "created_at_ms": 1_780_000_000_000,
                                                       "expires_at": "2026-09-16T11:00:00+00:00",
                                                       "trigger": {"level": 555.55, "tf": "15m", "rule": "close_above",
                                                                   "text_tr": PLAN_TEXT},
                                                       "invalidation": {"level": 545.0}, "stop": 544.0, "target": 590.0,
                                                       "rr_after_cost": 1.8, "reasons": []}],
                                     "recent_plans": [], "data_gaps": {}})


def _client(root: Path):
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    (root / "data").mkdir(exist_ok=True)
    return TestClient(create_app(root, root / "data"))


def _tlist(html: str) -> str:
    """Ana ekrandaki açık işlem listesi (kenar sütunu) — kartlar/grafik metni karışmasın."""
    return html.split('class="tlist"', 1)[1].split("</aside>", 1)[0] if 'class="tlist"' in html else ""


# ====================================================================== (a) ANA KUSUR: plan sembole göre sızıyor
def test_defect_a_plan_box_leaks_the_pattern_book_plan_into_every_other_book_and_market(tmp_path: Path):
    """`plan_box` defter/piyasa ALIR ama `pattern_plans_for(state, sym)` YALNIZ SEMBOLE bakar.

    Sonuç: mum trader defterinin futures planı, kullanıcı ana bot/spot'tayken de, M2/futures'tayken de
    grafiğin altında "plan" diye çizilir. Plan o kapsamda YOKTUR.
    """
    _setup(tmp_path)
    st = StateReader(tmp_path)
    for book_id, market in (("main", "spot"), ("main", "futures"),
                            ("strategy_paper", "futures"), ("strategy_paper_m2", "futures")):
        html = term.plan_box(st, book_id=book_id, base="SOL", market=market)
        assert PLAN_TEXT not in html, (
            "mum trader planı %s/%s kapsamında gösteriliyor — plan o deftere ait DEĞİL" % (book_id, market))
    # Kayıt katmanında kusurun tam yeri: filtre yalnız sembol.
    plans = term.pattern_plans_for(st, "SOL/USDT")
    assert plans and all(str(p.get("book_id") or "pattern_trader") == "pattern_trader" for p in plans)
    assert len(inspect.signature(term.pattern_plans_for).parameters) >= 3, (
        "pattern_plans_for defter/piyasa kapsamı ALMIYOR (yalnız sembol) — sızıntının kaynağı burası")
    # Uçtan uca: canlı uç nokta da aynı planı yanlış kapsamda döndürüyor.
    c = _client(tmp_path)
    assert PLAN_TEXT not in c.get("/api/planbox/SOL?book=main&market=spot").json()["html"]
    assert PLAN_TEXT not in c.get("/?book=strategy_paper_m2&coin=SOL").text


# ====================================================================== (b) hesap kartları piyasayı GÖRMÜYOR
def test_defect_b_account_snapshot_ignores_the_selected_market_and_always_reads_futures(tmp_path: Path):
    """`account_snapshot(state, book_id, *, vm=None)` piyasa parametresi ALMAZ; `out["market"]` sabit "futures"tur
    ve ana bot için kaynak DAİMA `futures_positions()`tır. Spot seçmek grafiği değiştirir, kartları değiştirmez."""
    _setup(tmp_path)
    st = StateReader(tmp_path)
    # Spot defterinde GERÇEKTEN ayrı bir kayıt var (kaynak mevcut, sorun okunmaması).
    spot = st.spot_positions()
    assert [p["symbol"] for p in spot] == ["SOL/USDT"] and str(spot[0]["entry_avg"]) == SPOT_ENTRY
    c = _client(tmp_path)
    rows = _tlist(c.get("/?book=main&market=spot&coin=SOL").text)
    assert 'data-market="spot"' in rows, "satır spot seçildiğini iddia ediyor"
    assert SPOT_ENTRY in rows, "Spot seçiliyken SPOT defterinin kaydı gösterilmeli"
    assert FUT_ENTRY not in rows, "Spot seçiliyken FUTURES pozisyonu gösteriliyor — kartlar piyasayla birlikte dönmedi"
    # Kusurun kaynağı imzada: piyasa hiç parametre değil, `out["market"]` sabit yazılır.
    assert term.account_snapshot(st, "main")["market"] != "futures" or "market" in inspect.signature(
        term.account_snapshot).parameters, "account_snapshot piyasa kimliğini hiç almıyor, sabit «futures» yazıyor"


# ====================================================================== (c) kapanış bağlantıları kimliği düşürüyor
def test_defect_c_closed_block_links_drop_the_book_and_market(tmp_path: Path):
    """Kenar sütunundaki kapanış satırları `/coin/SYMBOL` (defter/piyasa YOK) ve "tüm kapanışlar" bağlantısı
    çıplak `/trades?tab=closed` üretir: T2'den tıklayan kullanıcı ANA defterin sayfasına düşer."""
    _setup(tmp_path)
    st = StateReader(tmp_path)
    html = term.closed_block(term.account_snapshot(st, "strategy_paper"))
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert hrefs, "kapanış kutusu bağlantı üretmeli"
    for href in hrefs:
        assert "book=strategy_paper" in href, "bağlantı seçili defteri taşımıyor: %s" % href
    coin_links = [h for h in hrefs if h.startswith("/coin/")]
    assert coin_links and all("market=" in h for h in coin_links), (
        "coin bağlantısı piyasa kimliği taşımıyor: %s" % coin_links)


# ====================================================================== (d) canlı yenileme plan kutusunu atlıyor
def test_defect_d_live_refresh_never_updates_the_plan_box(tmp_path: Path):
    """`live_refresh_js` yalnız cardshost/poshost/closedhost günceller. Pozisyon kapandığında plan kutusundaki
    "Açık işlem" satırı ekranda BAYAT kalır — kullanıcı kapanmış işlemi açık sanır."""
    _setup(tmp_path)
    js = term.live_refresh_js("strategy_paper")
    assert "planhost" in js, "canlı yenileme plan kutusunu (planhost) hiç tazelemiyor"
    c = _client(tmp_path)
    page = c.get("/?book=strategy_paper&coin=SOL").text
    assert 'id="planhost"' in page, "plan kutusu ekranda GERÇEKTEN var (yani bayatlayabilir)"
    assert "Açık işlem" in term.plan_box(StateReader(tmp_path), book_id="strategy_paper", base="SOL", market="futures")


# ====================================================================== (e) boş ama YETKİLİ defter diriltiliyor
def test_defect_e_empty_authoritative_ledger_resurrects_stale_summary_positions(tmp_path: Path):
    """`book_positions`/`book_trades` "if not pos" ile özete düşer. Defter dosyası VAR ve meşru olarak BOŞSA
    (tüm pozisyonlar kapandı), eski özetteki pozisyonlar geri dirilir. "Eksik dosya" ile "boş defter" AYRI şeydir."""
    _setup(tmp_path)
    # Yetkili defter: pozisyonlar kapandı, geçmiş bu turda henüz özete işlenmedi. Özet BAYAT kaldı.
    _w(tmp_path, "strategy_paper/futures_ledger.json",
       {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "100",
        "positions": {}, "history": []})
    st = StateReader(tmp_path)
    assert (tmp_path / "strategy_paper" / "futures_ledger.json").exists()
    assert (st.book_summary_file("strategy_paper") or {}).get("positions"), "özet hâlâ bayat kaydı taşıyor (kurgu)"
    assert st.book_positions("strategy_paper") == [], (
        "YETKİLİ defter boş: bayat özetten pozisyon diriltilmemeli")
    assert st.book_trades("strategy_paper") == [], (
        "YETKİLİ defter geçmişi boş: bayat özet `history_tail` diriltilmemeli")
    assert term.account_snapshot(st, "strategy_paper")["open_total"] == 0


# ====================================================================== KARŞI ÖRNEK 1: plan KENDİ defterinde durur
def test_counterexample_plan_box_shows_the_plan_in_the_pattern_book_itself(tmp_path: Path):
    """Doğru olan davranış: mum trader defteri seçiliyken plan GÖRÜNÜR (onarım bunu susturmamalı)."""
    _setup(tmp_path)
    st = StateReader(tmp_path)
    html = term.plan_box(st, book_id="pattern_trader", base="SOL", market="futures")
    assert PLAN_TEXT in html and "Teyit/tetik bekliyor" in html
    assert "544" in html and "590" in html, "planın stop/hedefi kayıttan okunur"
    # Aynı defterin açık işlemi de KENDİ kapsamında okunur (kart tarafı, `plan_box`tan bağımsız).
    assert str(term.account_snapshot(st, "pattern_trader")["positions"][0]["entry_avg"]) == PAT_ENTRY
    c = _client(tmp_path)
    assert PLAN_TEXT in c.get("/api/planbox/SOL?book=pattern_trader&market=futures").json()["html"]


# ====================================================================== KARŞI ÖRNEK 2: defterler birbirine karışmaz
def test_counterexample_each_book_keeps_its_own_record_and_source(tmp_path: Path):
    """Bugün DOĞRU olan: aynı coin dört defterde ayrı okunur, özkaynaklar toplanmaz, kaynak dosya ayrıdır."""
    _setup(tmp_path)
    st = StateReader(tmp_path)
    snaps = {b: term.account_snapshot(st, b) for b in ("main", "strategy_paper", "strategy_paper_m2", "pattern_trader")}
    assert [s["open_total"] for s in snaps.values()] == [1, 1, 1, 1]
    assert snaps["main"]["positions"][0]["side"] == "SHORT" and snaps["strategy_paper"]["positions"][0]["side"] == "LONG"
    assert snaps["strategy_paper_m2"]["positions"][0]["side"] == "SHORT"
    assert len({s["source"] for s in snaps.values()}) == 4, "her defter KENDİ muhasebe kaynağından okunur"
    entries = {str(s["positions"][0].get("entry_avg")) for s in snaps.values()}
    assert entries == {FUT_ENTRY, T2_ENTRY, M2_ENTRY, PAT_ENTRY}, entries
    # Ekranda da ayrı: T2 sayfasında M2'nin girişi YOK.
    c = _client(tmp_path)
    t2 = _tlist(c.get("/?book=strategy_paper&coin=SOL").text)
    assert T2_ENTRY in t2 and M2_ENTRY not in t2 and PAT_ENTRY not in t2
    assert [b["label"] for b in st.books()] == ["Ana bot", "T2 · EMA200 trend", "M2 · 28g momentum", "Mum trader"]


# ====================================================================== KARŞI ÖRNEK 3: yenileme diğer üç kutuyu tazeler
def test_counterexample_live_refresh_updates_cards_positions_and_closed_hosts(tmp_path: Path):
    """Onarım planhost'u eklerken var olan üç hedefi ve SSE bağını BOZMAMALI."""
    _setup(tmp_path)
    js = term.live_refresh_js("strategy_paper")
    for host in ("cardshost", "poshost", "closedhost"):
        assert "put('%s'" % host in js, host
    assert "__onState" in js and "'/api/book/'" in js and 'book="strategy_paper"' in js and "setInterval" in js
    c = _client(tmp_path)
    api = c.get("/api/book/strategy_paper").json()
    assert api["open_total"] == 1 and T2_ENTRY in api["positions_html"] and api["closed"] == 1


# ====================================================================== KARŞI ÖRNEK 4: EKSİK dosyada özet meşrudur
def test_counterexample_missing_ledger_file_may_still_fall_back_to_the_summary(tmp_path: Path):
    """Defter dosyası HİÇ YOKSA (henüz yazılmadı / okunamıyor) özet projeksiyonu meşru kaynaktır — bu yol
    kalmalı. (e) kusuru yalnız dosya VAR ve MEŞRU OLARAK BOŞ olduğunda geçerlidir."""
    _setup(tmp_path)
    (tmp_path / "strategy_paper" / "futures_ledger.json").unlink()
    st = StateReader(tmp_path)
    pos = st.book_positions("strategy_paper")
    assert len(pos) == 1 and str(pos[0]["entry_avg"]) == T2_ENTRY and pos[0]["book_id"] == "strategy_paper"
    assert [t["id"] for t in st.book_trades("strategy_paper")] == ["T2A"]


# ====================================================================== KARŞI ÖRNEK 5: kapanış içeriği doğru
def test_counterexample_closed_block_still_shows_symbol_net_result_and_reason(tmp_path: Path):
    """Bağlantılara kimlik eklenirken satırın İÇERİĞİ (coin, net, Türkçe çıkış nedeni) bozulmamalı."""
    _setup(tmp_path)
    st = StateReader(tmp_path)
    html = term.closed_block(term.account_snapshot(st, "strategy_paper_m2"))
    assert "SOL/USDT" in html and "4.50" in html
    assert "Strateji çıkışı (28g momentum döndü)" in html and "M2_TSMOM28_CROSS_DOWN" not in html
    assert "tüm kapanışlar" in html


# ====================================================================== (f) NET SONUÇ yetkili kayıtla uzlaşır
def test_realized_total_comes_from_the_whole_history_not_the_visible_rows(tmp_path: Path):
    """Gerçekleşen toplam, ekrandaki son N satırdan DEĞİL defterin TAM geçmişinden gelir."""
    _setup(tmp_path)
    # Görünen liste 2000 satırla sınırlıdır; geçmiş BUNDAN UZUN olmalı ki "tam geçmiş" iddiası gerçekten sınansın
    # (karşıt doğrulama bulgusu: 40 satırlık kurgu, onarım geri alınsa bile AYNI sayıyı veriyordu).
    n = 2500
    hist = [_trade("T2_%04d" % i, "SOL/USDT", "LONG", 300.0 + i, 301.0 + i, 0.5, "hedef1") for i in range(n)]
    _w(tmp_path, "strategy_paper/futures_ledger.json",
       {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "120",
        "positions": {}, "history": hist})
    st = StateReader(tmp_path)
    assert len(st.book_trades("strategy_paper", limit=2000)) == 2000, "görünen liste sınırlı"
    acc = term.account_snapshot(st, "strategy_paper")
    assert acc["closed"] == n and acc["realized"] == round(n * 0.5, 6), (acc["closed"], acc["realized"])
    assert acc["wins"] == n and acc["losses"] == 0
    assert acc["realized"] != round(2000 * 0.5, 6), "toplam GÖRÜNEN satırlardan üretilmiş"
    # Ekranda YALNIZ son 8 satır var; kart toplamı yine 40 işlemin toplamıdır.
    assert term.closed_block(acc, book_id="strategy_paper", market="futures").count("<tr>") <= 9  # 8 satır + başlık
    assert str(n) in term.summary_cards(acc), "kapanan işlem kartı TAM sayıyı gösterir"
    assert acc["net"] == acc["realized"], "açık pozisyon yokken net = gerçekleşen"


def test_gross_open_pnl_is_never_labelled_net_and_open_costs_survive(tmp_path: Path):
    """Kâğıt defterin açık K/Z'si BRÜTtür: net sonuca eklenmez, kart bunu söyler; açık pozisyonun gerçekleşmiş
    giriş ücreti ve tahakkuk etmiş funding'i kaybolmaz."""
    _setup(tmp_path)
    st = StateReader(tmp_path)
    acc = term.account_snapshot(st, "strategy_paper")
    assert acc["unrealized"] == 0.5 and acc["unrealized_kind"] == "gross"
    assert acc["net"] is None, "brüt açık K/Z gerçekleşenle TOPLANMAZ"
    assert acc["open_costs"] == {"fees": 0.02, "funding": -0.01}, acc["open_costs"]
    cards = term.summary_cards(acc)
    assert "açık (brüt)" in cards and "brüt açık K/Z net sonuca EKLENMEZ" in cards
    assert "ücret 0.02 USDT" in cards and "funding -0.01 USDT" in cards


def test_a_book_with_no_closes_reports_zero_not_missing(tmp_path: Path):
    """HİÇ kapanışı olmayan GEÇERLİ defterde gerçekleşen 0,0'dır — «Veri yok» değil. (Veri kaynağı eksikse
    durum farklıdır: o zaman toplam ALT SINIR olarak işaretlenir.)"""
    _setup(tmp_path)
    _w(tmp_path, "strategy_paper/futures_ledger.json",
       {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "100",
        "positions": {}, "history": []})
    st = StateReader(tmp_path)
    acc = term.account_snapshot(st, "strategy_paper")
    assert acc["closed"] == 0 and acc["realized"] == 0.0 and acc["history_complete"] is True
    assert acc["net"] == 0.0, "kapanış yok: gerçekleşen 0 ve net sonuç 0"
    # Kaynak dosya YOKSA toplam yalnız özet kuyruğundandır → ALT SINIR olarak işaretlenir.
    (tmp_path / "strategy_paper" / "futures_ledger.json").unlink()
    acc2 = term.account_snapshot(StateReader(tmp_path), "strategy_paper")
    assert acc2["history_complete"] is False and "ALT SINIR" in " ".join(acc2["warnings"])


# ====================================================================== (g) desteklenmeyen defter/piyasa AÇIKÇA söylenir
def test_unsupported_book_market_pair_is_stated_not_silently_swapped(tmp_path: Path):
    """T2/M2/formasyon defterleri yalnız USDⓈ-M perpetual tutar. Spot seçilince BAŞKA hesabın verisi GÖSTERİLMEZ;
    durum açıkça yazılır."""
    _setup(tmp_path)
    st = StateReader(tmp_path)
    for bid in ("strategy_paper", "strategy_paper_m2", "pattern_trader"):
        acc = term.account_snapshot(st, bid, market="spot")
        assert acc["unsupported"] is True and acc["positions"] == [] and acc["open_total"] == 0
        assert "Spot görünümü bu hesap için YOKTUR" in " ".join(acc["warnings"])
        for entry in (T2_ENTRY, M2_ENTRY, PAT_ENTRY, FUT_ENTRY, SPOT_ENTRY):
            assert entry not in term.summary_cards(acc), "başka hesabın sayısı sızdı: %s" % entry
        assert "bu piyasada kaydı yoktur" in term.closed_block(acc, book_id=bid, market="spot")
        assert "Spot görünümü YOKTUR" in term.plan_box(st, book_id=bid, base="SOL", market="spot")
    assert st.book_supports_market("main", "spot") and st.book_supports_market("main", "futures")


# ====================================================================== (h) kapanmış işlemin kimliği ve zamanı korunur
def test_clicking_a_closed_trade_keeps_its_book_market_id_and_time(tmp_path: Path):
    """Satır bağlantısı defter + piyasa + trade_id + kapanış anını taşır; hedef sayfa o İŞLEMİ kendi defterinde
    bulur ve grafik kapsamını açıkça söyler."""
    _setup(tmp_path)
    st = StateReader(tmp_path)
    acc = term.account_snapshot(st, "strategy_paper_m2")
    href = re.findall(r'href="(/coin/[^"]+)"', term.closed_block(acc, book_id="strategy_paper_m2", market="futures"))[0]
    for bit in ("book=strategy_paper_m2", "market=futures", "trade=M2A", "as_of="):
        assert bit in href, (bit, href)
    c = _client(tmp_path)
    page = c.get(href.replace("&amp;", "&")).text
    assert "Seçilen işlem" in page and "M2A" in page and "strategy_paper_m2" in page
    assert "434.44" in page, "o işlemin çıkış fiyatı kendi kaydından okunur"
    # Mum verisi YOK: durum açıkça söylenir, boş/uydurma gösterim yapılmaz.
    assert "mum verisi YOK" in page or "KAPSAMIYOR" in page
    # BAŞKA defterin işlem kimliği bu hesapta BULUNAMAZ (sessizce ana deftere düşmez).
    wrong = c.get("/coin/SOL?book=strategy_paper&market=futures&trade=M2A").text
    assert "BULUNAMADI" in wrong and "434.44" not in wrong


def test_open_position_row_on_the_coin_page_follows_the_selected_book(tmp_path: Path):
    """Coin sayfasındaki «Açık pozisyon» bölümü SEÇİLİ defterden okunur (eskiden daima ana futures defteriydi)."""
    _setup(tmp_path)
    c = _client(tmp_path)
    assert T2_ENTRY in c.get("/coin/SOL?book=strategy_paper&market=futures").text
    assert M2_ENTRY in c.get("/coin/SOL?book=strategy_paper_m2&market=futures").text
    assert FUT_ENTRY in c.get("/coin/SOL?book=main&market=futures").text
    assert SPOT_ENTRY in c.get("/coin/SOL?book=main&market=spot").text


# ====================================================================== (i) canlı uç nokta piyasayı taşır
def test_book_api_carries_the_market_end_to_end(tmp_path: Path):
    """`/api/book/{id}` piyasa parametresi alır; satır bağlantıları ve kartlar o kapsamdan üretilir."""
    _setup(tmp_path)
    c = _client(tmp_path)
    spot = c.get("/api/book/main?market=spot").json()
    assert spot["market"] == "spot" and SPOT_ENTRY in spot["positions_html"] and FUT_ENTRY not in spot["positions_html"]
    fut = c.get("/api/book/main?market=futures").json()
    assert fut["market"] == "futures" and FUT_ENTRY in fut["positions_html"]
    t2 = c.get("/api/book/strategy_paper?market=futures").json()
    assert "book=strategy_paper" in t2["closed_html"] and "market=futures" in t2["closed_html"]
    unsupported = c.get("/api/book/strategy_paper?market=spot").json()
    assert unsupported["unsupported"] is True and T2_ENTRY not in unsupported["positions_html"]
    # Yenileme betiği plan kutusunu da SEÇİLİ defter/piyasa ile tazeler.
    js = term.live_refresh_js("strategy_paper", market="futures")
    assert "refreshPlan()" in js and "market=" in js and 'market="futures"' in js


def test_no_open_position_means_zero_unrealized_not_a_stale_summary_value(tmp_path: Path):
    """Açık pozisyon YOKSA açık K/Z 0'dır. Özet dosyası bir önceki turdan kalmış bir `unrealized` taşıyabilir;
    kart bunu göstermemeli (gerçek tarayıcı doğrulamasında görüldü: 0 açık işlemle "açık (brüt) 0,5 USDT")."""
    _setup(tmp_path)
    _w(tmp_path, "strategy_paper/futures_ledger.json",
       {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "103.5",
        "positions": {}, "history": [_trade("T2A", "SOL/USDT", "LONG", 333.33, 343.33, 3.5, "hedef1")]})
    st = StateReader(tmp_path)
    assert (st.book_summary_file("strategy_paper") or {})["summary"]["unrealized"] == 0.5, "kurgu: özet bayat"
    acc = term.account_snapshot(st, "strategy_paper")
    assert acc["open_total"] == 0 and acc["unrealized"] == 0.0, (acc["open_total"], acc["unrealized"])
    assert acc["net"] == acc["realized"] == 3.5, (acc["net"], acc["realized"])
    assert "0.50 USDT" not in term.summary_cards(acc), "bayat açık K/Z kartta görünüyor"


# ====================================================================== (j) canlı yenileme betiği KAPSAM-KESİN
def test_live_refresh_javascript_targets_the_selected_scope_and_guards_the_response(tmp_path: Path):
    """Karşıt doğrulama bulgusu: (d) onarımı SAF İSTEMCİ TARAFI JavaScript'tir ve kanıtı yalnız gevşek metin
    eşleşmesiydi. Burada betiğin KAPSAM sözleşmesi tam olarak sabitlenir.

    DAVRANIŞ ayrıca GERÇEK tarayıcıda uçtan uca doğrulandı (gerçek FastAPI + sentetik state, bkz. kapanış
    belgesi): pozisyon kapandıktan sonra plan kutusundaki «Açık işlem» satırı ekrandan KALKTI, kartlar
    «KAPANAN İŞLEM 2 / AÇIK İŞLEM 0»a döndü ve konsolda hata olmadı.
    """
    import re as _re
    js = term.live_refresh_js("strategy_paper", "?token=x", market="futures")
    body = js.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    assert body.count("{") == body.count("}") and body.count("(") == body.count(")"), "ayraçlar dengesiz"
    assert 'var book="strategy_paper", market="futures"' in body, body[:200]
    assert "'/api/planbox/'+encodeURIComponent(b)+'?book='+encodeURIComponent(book)" in body
    assert "&market='+encodeURIComponent(market)" in body, "plan isteği SEÇİLİ piyasayı taşımalı"
    assert "String(d.book)===book" in body, "yanıt BAŞKA defterin planıysa yazılmamalı"
    assert _re.search(r"put\('cardshost'.*put\('poshost'.*put\('closedhost'.*refreshPlan\(\)", body, _re.S),         "plan kutusu kart/pozisyon/kapanış ile AYNI yenileme turunda tazelenmeli"
    # GÜVENLİK: defter/piyasa değerleri betiğe GÖMÜLÜR; kaçışsız gömme dizgiden çıkışa izin verirdi.
    hostile = term.live_refresh_js('a"b</script><img src=x>', market='c"d')
    assert "</script><img" not in hostile, "betik dizgisinden ÇIKIŞ mümkün"
    assert '"a\\"b\u003c\/script\u003e' in hostile or "a\\\"b" in hostile, hostile[:300]
