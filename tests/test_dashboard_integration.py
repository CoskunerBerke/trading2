"""Panel ENTEGRASYON regresyonları — HTML↔API paritesi, polling, stale, salt-okunurluk, güvenlik.

Gerçek `create_app()` uygulaması TestClient ile çalıştırılır. HİÇBİR dış ağ çağrısı yapılmaz;
worker/dashboard süreci başlatılmaz; state dosyaları YAZILMAZ (bu paket bunu ölçerek kanıtlar).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient          # noqa: E402

from tradingbot.dashboard.app import create_app    # noqa: E402
from tradingbot.dashboard.config import DashboardConfig  # noqa: E402
from tradingbot.dashboard.templates import POS_TABLE_CLS  # noqa: E402
from tradingbot.dashboard.views import POSITION_COLUMNS, POSITION_NUM_COLS, POSITION_PNL_COLS, Freshness   # noqa: E402

POS = [{"id": "F00004", "symbol": "BZ/USDT", "market_type": "USDM_PERP", "side": "LONG",
        "qty": "0.5", "entry_avg": "90.61", "leverage": 1, "isolated_margin": "14.95065",
        "stop": "88.61", "targets": ["95.0"], "last_price": "91.11", "entry_fee": "0",
        "opened_at": "2026-08-19T15:39:46+00:00", "fills": [{"id": "f1"}]},
       {"id": "F00005", "symbol": "XAUT/USDT", "market_type": "USDM_PERP", "side": "LONG",
        "qty": "0.01", "entry_avg": "4479.32", "leverage": 1, "isolated_margin": "13.43796",
        "stop": "4429.32", "targets": ["4600"], "last_price": "4508.32", "entry_fee": "0",
        "opened_at": "2026-08-19T16:51:05+00:00", "fills": [{"id": "f2"}]}]

TRADES = [{"id": "T1", "net_pnl": "60.00", "closed_at": "2026-08-22T10:00:00+00:00"},
          {"id": "T2", "net_pnl": "34.38", "closed_at": "2026-08-22T11:00:00+00:00"},
          {"id": "T3", "net_pnl": "-30.00", "closed_at": "2026-08-22T12:00:00+00:00"},
          {"id": "T4", "net_pnl": "-31.52", "closed_at": "2026-08-22T13:00:00+00:00"},
          {"id": "T5", "net_pnl": "-30.00", "closed_at": "2026-08-22T14:00:00+00:00"}]


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Sentetik state — GERÇEK kullanıcı state'i, token veya API anahtarı KULLANILMAZ."""
    d = tmp_path / "state"
    d.mkdir()
    (d / "futures_ledger.json").write_text(json.dumps({
        "schema_version": 2, "kind": "futures", "equity": "50.09", "wallet_balance": "50.09",
        "fees": {"taker_pct": 0.0, "maker_pct": 0.0},
        "positions": {p["symbol"]: p for p in POS}, "history": TRADES}), encoding="utf-8")
    # ⚠ SENTETİK değerler — üretim `state/risk.json` verisi DEĞİLDİR (bkz. test_dashboard_accuracy).
    (d / "risk.json").write_text(json.dumps({
        "generated_at": "2026-08-22T22:31:41+00:00", "mode": "PAPER",
        "profile": {"max_total_open_risk_pct": 6.0, "risk_per_trade_pct": 2.0},
        "killswitch": {"state": "ARMED"},
        "exposure": {"equity": 50.09, "drawdown_pct": 2.13, "total_open_risk_usdt": 8.83,
                     "open_positions": 2, "used_margin": 28.38861,
                     "starting_equity": 50.09, "equity_basis": 50.09,
                     "equity_basis_kind": "starting_equity",
                     "max_total_open_risk_usdt": 3.0054}}), encoding="utf-8")
    (d / "mode.json").write_text(json.dumps(
        {"mode": "PAPER", "live_order_path_enabled": False, "history": []}), encoding="utf-8")
    (d / "learning.json").write_text(json.dumps({
        "n_trades": 5, "n_wins": 2, "sum_r": -0.153821, "updated_at": "2026-08-22T22:00:00+00:00",
        "weights": {"trend": 0.123456, "momentum": -0.045678},
        "agent_weights": {"trend": 1.0, "edge": -0.5},
        "agent_hits": {"trend": [0, 0], "edge": [3, 5]},
        "lessons": [{"id": "F00002", "symbol": "KORU/USDT", "side": "SHORT", "r": -1.0444,
                     "pnl": -1.0475, "won": False, "exit": "stop", "bars": 12, "mae": -15.15,
                     "mfe": 3.67, "setup": "breakdown", "at": "2026-08-20T01:45:03+00:00",
                     "why": ["ZARAR: stop." + " uzun gerekce" * 40]},
                    {"id": "F00003", "symbol": "BZ/USDT", "side": "LONG", "r": 0.9, "pnl": 1.2,
                     "won": True, "exit": "tp", "bars": 8, "mae": -2.0, "mfe": 5.0,
                     "setup": "breakout", "at": "2026-08-21T01:45:03+00:00", "why": "kisa"}]}),
        encoding="utf-8")
    return d


@pytest.fixture
def client(state_dir, tmp_path):
    return TestClient(create_app(state_dir, tmp_path / "market", None, DashboardConfig()))


def _cards_from_html(html: str) -> dict[str, str]:
    """YALNIZ kâr/zarar özet grid'i (`id="sumgrid"`) — diğer kart ızgaraları kapsam dışı."""
    sec = html.split('id="sumgrid"', 1)[1].split("</div><div class=\"grid\">", 1)[0]
    out = {}
    for m in re.finditer(r'<div class="k">(.*?)</div><div class="v">(.*?)</div>', sec, re.S):
        out[re.sub(r"<[^>]+>", "", m.group(1)).strip()] = re.sub(r"<[^>]+>", "", m.group(2)).strip()
    return out


# --------------------------------------------------------------------------- 15 · HTML ↔ API paritesi
def test_html_and_api_summary_show_identical_values(client):
    """15 · İlk HTML yüklemesi ile `/api/live/summary` AYNI kanonik değerleri kullanır."""
    html = client.get("/").text
    api = client.get("/api/live/summary").json()
    by_title = {c["title"]: c for c in api["cards"]}
    html_cards = _cards_from_html(html)
    assert html_cards, "HTML'de kâr/zarar kartı bulunamadı"
    for title, shown in html_cards.items():
        assert title in by_title, f"API'de eksik kart: {title}"
        assert shown == by_title[title]["display"], f"{title}: HTML={shown!r} API={by_title[title]['display']!r}"


def test_api_summary_exposes_canonical_fields(client):
    """Kanonik özet sözleşmesi: istenen bütün alanlar API'de bulunur."""
    s = client.get("/api/live/summary").json()["summary"]
    for k in ("today_realized_net_usdt", "all_time_realized_net_usdt", "open_net_usdt",
              "total_net_usdt", "winning_trades", "losing_trades", "breakeven_trades",
              "closed_trades", "win_rate_pct", "profit_factor", "max_drawdown_pct",
              "futures_equity_usdt", "spot_equity_usdt", "open_futures_notional_usdt",
              "open_futures_margin_usdt", "margin_utilization_pct", "open_stop_risk_usdt",
              "open_risk_budget_utilization_pct", "as_of", "source_freshness"):
        assert k in s, f"kanonik alan eksik: {k}"
    assert s["margin_utilization_pct"] == pytest.approx(56.7, abs=0.1)
    assert s["max_drawdown_pct"] == pytest.approx(2.13)
    # Fixture işlemleri SABİT tarihli (2026-08-22); `today` ise gerçek UTC saatten gelir → günlük
    # alan takvim döndükçe 0 olur. Tarih-bağımsız alan doğrulanır; «bugün» semantiği
    # `test_dashboard_accuracy.test_today_realized_matches_card` içinde açık `today=` ile test edilir.
    assert s["all_time_realized_net_usdt"] == pytest.approx(2.86)


def test_api_cards_carry_machine_value_and_display(client):
    """Kartlar hem ham `value` hem biçimlenmiş `display` taşır (polling bunu kullanır)."""
    cards = client.get("/api/live/summary").json()["cards"]
    keys = {c["key"] for c in cards}
    assert {"today_realized_net_usdt", "profit_factor", "margin_utilization_pct",
            "open_stop_risk_usdt", "risk_engine_reserved_usdt"} <= keys
    pf = next(c for c in cards if c["key"] == "profit_factor")
    assert pf["display"] == "1.03" and "$" not in pf["display"]


# --------------------------------------------------------------------- 15b · JSON sonluluk sınırı
def _client_with_trades(tmp_path, trades):
    d = tmp_path / "state"
    d.mkdir()
    (d / "futures_ledger.json").write_text(json.dumps({
        "schema_version": 2, "kind": "futures", "equity": "50.09",
        "fees": {"taker_pct": 0.0}, "positions": {}, "history": trades}), encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}), encoding="utf-8")
    return TestClient(create_app(d, tmp_path / "market", None, DashboardConfig()))


@pytest.mark.parametrize("name,trades,pf_state,display", [
    ("yalnız kazanan", [{"net_pnl": "1", "closed_at": "2026-08-22T10:00:00+00:00"},
                        {"net_pnl": "2", "closed_at": "2026-08-22T11:00:00+00:00"}],
     "positive_infinity", "∞"),
    ("kazanan + başa baş", [{"net_pnl": "1", "closed_at": "2026-08-22T10:00:00+00:00"},
                            {"net_pnl": "0", "closed_at": "2026-08-22T11:00:00+00:00"}],
     "positive_infinity", "∞"),
    ("yalnız başa baş", [{"net_pnl": "0", "closed_at": "2026-08-22T10:00:00+00:00"}],
     "undefined", "Veri yok"),
    ("kazanan + kaybeden", [{"net_pnl": "60", "closed_at": "2026-08-22T10:00:00+00:00"},
                            {"net_pnl": "-30", "closed_at": "2026-08-22T11:00:00+00:00"}],
     "finite", "2.00"),
    ("hiç kapanmış işlem yok", [], "no_closed_trades", "Veri yok"),
])
def test_api_summary_is_http200_and_rfc_json_in_all_pf_cases(tmp_path, name, trades, pf_state, display):
    """3 · GERÇEK uç nokta: profit factor ne olursa olsun HTTP 200 + RFC uyumlu JSON.

    REGRESYON: `float("inf")` üretiliyordu; Starlette `allow_nan=False` ile serileştirdiği için
    `/api/live/summary` HTTP 500 veriyor, summary polling duruyor ve kartlar donuyordu.
    """
    c = _client_with_trades(tmp_path, trades)
    r = c.get("/api/live/summary")
    assert r.status_code == 200, f"{name}: HTTP {r.status_code}"
    json.loads(r.text, parse_constant=_reject_constant)        # Infinity/NaN literali REDDEDİLİR
    body = r.json()
    assert body["summary"]["profit_factor_state"] == pf_state, name
    assert next(x for x in body["cards"] if x["key"] == "profit_factor")["display"] == display, name


def _reject_constant(tok):
    raise AssertionError(f"JSON'da RFC dışı sabit: {tok}")


def test_all_live_endpoints_carry_only_finite_numbers(client):
    """2 · Bütün canlı uçlarda sonlu olmayan sayı YOK (savunma kontrolü)."""
    def walk(x, path=""):
        if isinstance(x, float):
            assert math.isfinite(x), f"sonlu değil: {path} = {x}"
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")

    for path in ("/api/live/summary", "/api/live/positions", "/api/live/health"):
        r = c_get = client.get(path)
        assert r.status_code == 200, path
        json.loads(c_get.text, parse_constant=_reject_constant)
        walk(r.json(), path)


# --------------------------------------------------------------- 15c · bozuk ledger: sonsuz/NaN girdiler
_POS_OK = {"id": "F1", "symbol": "X/USDT", "market_type": "USDM_PERP", "side": "LONG", "qty": "1",
           "entry_avg": "100", "leverage": 1, "isolated_margin": "100", "stop": "90",
           "last_price": "101", "entry_fee": "0.01", "funding_net": "0",
           "opened_at": "2026-08-19T15:00:00+00:00"}
_T_OK = {"id": "T1", "net_pnl": "-5", "closed_at": "2026-08-22T10:00:00+00:00"}


def _client_with_ledger(tmp_path, *, trades=None, positions=None, raw_text=None):
    d = tmp_path / "state"
    d.mkdir(exist_ok=True)
    if raw_text is not None:
        (d / "futures_ledger.json").write_text(raw_text, encoding="utf-8")
    else:
        (d / "futures_ledger.json").write_text(json.dumps({
            "schema_version": 2, "kind": "futures", "equity": "50.09", "fees": {"taker_pct": 0.0},
            "positions": {p["symbol"]: p for p in (positions or [])},
            "history": trades if trades is not None else [_T_OK]}), encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}), encoding="utf-8")
    return TestClient(create_app(d, tmp_path / "market", None, DashboardConfig()), raise_server_exceptions=False)


def _assert_finite_payload(r, where):
    """HTTP 200 + `json()` çalışır + ham metinde RFC dışı literal yok + özyinelemeli sonlu."""
    assert r.status_code == 200, f"{where}: HTTP {r.status_code}"
    body = json.loads(r.text, parse_constant=_reject_constant)
    assert not re.search(r'(?<![\w"])(-?Infinity|NaN)(?![\w"])', r.text), f"{where}: literal sızıntısı"

    def walk(x, path):
        if isinstance(x, float):
            assert math.isfinite(x), f"{where}: sonlu değil {path}={x}"
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
    walk(body, where)
    return body


def _t(pnl, **kw):
    d = dict(_T_OK, id=f"T-{pnl}", net_pnl=pnl)
    d.update(kw)
    return d


def _p(**kw):
    d = dict(_POS_OK)
    d.update(kw)
    return d


_RAW_INF = ('{"schema_version":2,"kind":"futures","equity":"50.09","fees":{"taker_pct":0.0},'
            '"positions":{},"history":[{"id":"A","net_pnl":%s,"closed_at":"2026-08-22T10:00:00+00:00"},'
            '{"id":"B","net_pnl":"-5","closed_at":"2026-08-22T11:00:00+00:00"}]}')


@pytest.mark.parametrize("name,kw", [
    ("net_pnl=\"Infinity\"", dict(trades=[_t("Infinity"), _T_OK])),
    ("net_pnl=\"-Infinity\"", dict(trades=[_t("-Infinity"), _T_OK])),
    ("net_pnl=\"NaN\"", dict(trades=[_t("NaN"), _T_OK])),
    ("net_pnl=Infinity literal", dict(raw_text=_RAW_INF % "Infinity")),
    ("net_pnl=-Infinity literal", dict(raw_text=_RAW_INF % "-Infinity")),
    ("Inf ve -Inf birlikte", dict(trades=[_t("Infinity"), _t("-Infinity"), _T_OK])),
    ("isolated_margin=\"Infinity\"", dict(positions=[_p(isolated_margin="Infinity")])),
    ("funding=\"Infinity\"", dict(positions=[_p(funding_net="Infinity")])),
    ("entry_fee=\"Infinity\"", dict(positions=[_p(entry_fee="Infinity")])),
    ("mark=\"Infinity\"", dict(positions=[_p(last_price="Infinity")])),
    ("qty=\"Infinity\"", dict(positions=[_p(qty="Infinity")])),
    ("entry=\"Infinity\"", dict(positions=[_p(entry_avg="Infinity")])),
    ("stop=\"-Infinity\"", dict(positions=[_p(stop="-Infinity")])),
])
def test_corrupt_ledger_never_breaks_live_api(tmp_path, name, kw):
    """3 · Bozuk ledger (NaN/±Infinity) → TÜM canlı uçlar HTTP 200, RFC JSON, özyinelemeli sonlu.

    REGRESYON (denetim): `net_pnl="Infinity"` → `views._num()` Infinity'yi elemiyor → kart `value`
    → `/api/live/summary` HTTP 500. Daha kötüsü `qty`/`mark` Infinity `Inf×0` ile
    `position_view()`in KENDİSİNİ düşürüyordu (→ `/` dahil her sayfa 500).
    """
    c = _client_with_ledger(tmp_path, **kw)
    for path in ("/api/live/summary", "/api/live/positions", "/api/live/health"):
        _assert_finite_payload(c.get(path), f"{name} {path}")
    assert c.get("/").status_code == 200, f"{name}: ana sayfa"
    assert c.get("/portfolio/futures").status_code == 200, f"{name}: futures sayfası"


def test_corrupt_trade_is_reported_not_zeroed(tmp_path):
    """3b · Bozuk kapanış kaydı SESSİZCE `0`a çevrilip toplama KATILMAZ: toplam `null` + neden.

    (Aksi hâlde «Toplam gerçekleşen» sahte bir sayı, kayıt da «başa baş» görünürdü.)
    """
    c = _client_with_ledger(tmp_path, trades=[_t("Infinity"), _t("60"), _t("-30")])
    body = _assert_finite_payload(c.get("/api/live/summary"), "summary")
    s = body["summary"]
    assert s["trades_non_finite"] == 1
    for k in ("today_realized_net_usdt", "all_time_realized_net_usdt", "total_net_usdt"):
        assert s[k] is None, k
        assert "sonlu olmayan" in s["unavailable_reason"][k], k
    assert s["closed_trades"] == 2 and s["winning_trades"] == 1 and s["losing_trades"] == 1   # bozuk kayıt SAYILMADI
    cards = {x["key"]: x for x in body["cards"]}
    for k in ("today_realized_net_usdt", "all_time_realized_net_usdt", "total_net_usdt"):
        assert cards[k]["value"] is None and cards[k]["display"] == "Veri yok", k
        assert "⚠" in cards[k]["sub"], k
    # HTML ile API aynı
    assert _cards_from_html(c.get("/").text)["Toplam gerçekleşen net K/Z"] == "Veri yok"


def test_corrupt_position_marks_unrealized_unknown(tmp_path):
    """3c · qty/entry sonlu değilse açık K/Z toplamı `null` + neden; stop riski de «kısmi»."""
    c = _client_with_ledger(tmp_path, positions=[_p(qty="Infinity"), _p(symbol="Y/USDT", id="F2")])
    s = _assert_finite_payload(c.get("/api/live/summary"), "summary")["summary"]
    assert s["open_net_usdt"] is None and s["total_net_usdt"] is None
    assert "miktar/giriş geçersiz" in s["unavailable_reason"]["open_net_usdt"]
    assert s["positions_invalid_qty"] == 1 and s["open_stop_risk_is_partial"] is True
    assert s["open_stop_risk_usdt"] == pytest.approx(10.0)          # yalnız geçerli pozisyon


def test_valid_ledger_golden_values_unchanged(client):
    """3d · Geçerli fixture'ın altın değerleri sonluluk sertleştirmesinden ETKİLENMEDİ."""
    s = _assert_finite_payload(client.get("/api/live/summary"), "summary")["summary"]
    assert s["all_time_realized_net_usdt"] == pytest.approx(2.86)
    assert s["open_net_usdt"] == pytest.approx(0.54)
    assert s["total_net_usdt"] == pytest.approx(3.40)
    assert s["trades_non_finite"] == 0 and s["profit_factor"] == pytest.approx(1.03125)
    assert s["margin_utilization_pct"] == pytest.approx(56.7, abs=0.1)


def test_finite_guard_is_the_single_json_boundary(tmp_path):
    """4 · NEGATİF-KONTROL HEDEFİ: ortak sonlu-dönüşüm yardımcısı kaldırılırsa BU test düşer.

    `profit_factor`a BAĞLI DEĞİLDİR — genel para alanı (`today/all_time/total`) üzerinden
    `Infinity` sızıntısını ölçer; hem birim (`finite_float_or_none`) hem uç nokta birlikte.
    """
    from decimal import Decimal
    from tradingbot.pnl import finite_float_or_none
    for bad in (float("inf"), float("-inf"), float("nan"), Decimal("Infinity"), Decimal("-Infinity"),
                Decimal("NaN"), "Infinity", "-Infinity", "NaN", Decimal("1e400")):
        assert finite_float_or_none(bad) is None, repr(bad)
    for good, exp in ((5, 5.0), ("2.5", 2.5), (Decimal("-0.14"), -0.14), (0, 0.0)):
        assert finite_float_or_none(good) == pytest.approx(exp), repr(good)
    c = _client_with_ledger(tmp_path, trades=[_t("Infinity"), _t("-Infinity"), _T_OK])
    body = _assert_finite_payload(c.get("/api/live/summary"), "summary")
    vals = {x["key"]: x["value"] for x in body["cards"]}
    for k in ("today_realized_net_usdt", "all_time_realized_net_usdt", "total_net_usdt"):
        assert vals[k] is None or math.isfinite(vals[k]), k


# --------------------------------------------------------------------------- 16 · polling
def test_polling_script_updates_summary_cards_and_wraps_table(client):
    """16 · Polling kartları GERÇEKTEN günceller ve tabloyu kendi kapsayıcısına sarar."""
    html = client.get("/").text
    assert "poll('sum','/api/live/summary'" in html
    assert "function(d){" in html.split("poll('sum'", 1)[1][:80], "summary callback boş bırakılmış"
    assert "getElementById('sc-'+c.key)" in html, "kart güncelleme kodu yok"
    # Tablo artık bağımsız `buildPosTable(d,NUMCOLS,PNLCOLS)` ile kurulur; `.tw` sarmalayıcı ve
    # sınıflar `test_polling_table_built_by_real_js_matches_server` içinde GERÇEK JS ile doğrulanır.
    assert "el.innerHTML=buildPosTable(d,NUMCOLS,PNLCOLS)" in html, "polling tablo kurucusunu kullanmıyor"
    for key in ("sc-today_realized_net_usdt", "sc-profit_factor", "sc-margin_utilization_pct"):
        assert f'id="{key}"' in html, f"kart id yok: {key}"


# --------------------------------------------------------------------------- 17-18 · stale
def test_stale_price_is_red_and_fresh_is_green():
    """17-18 · >90s kırmızı; strateji turu taze olsa bile fiyat bayatsa KIRMIZI kalır."""
    fresh = Freshness(price_age_s=10, run_age_s=60, heads_age_s=60, heartbeat_age_s=5,
                      stale_price_s=90, stale_run_s=2400)
    stale = Freshness(price_age_s=91, run_age_s=60, heads_age_s=60, heartbeat_age_s=5,
                      stale_price_s=90, stale_run_s=2400)
    assert fresh.price_state == "live" and stale.price_state == "stale"
    assert stale.run_state == "live", "strateji turu taze olmalı — fiyat stale'den bağımsız"


def test_stale_threshold_not_relaxed(client):
    """Eşik keyfî YÜKSELTİLMEZ: varsayılan 90s korunur ve polling'de aynı değer kullanılır."""
    assert DashboardConfig().stale_price_s == 90
    assert "__STALE__" not in client.get("/").text
    assert re.search(r"price_age_s>90\b", client.get("/").text)


def test_stale_banner_has_explanation_and_recovers_without_reload(client):
    """Uyarı açıklaması bulunur ve yeni fiyatla sayfa yenilenmeden yeşile döner."""
    html = client.get("/").text
    assert "Strateji çalışıyor; ancak pozisyon fiyatları belirtilen süredir güncellenmedi." in html
    assert "getElementById('stalenote')" in html
    assert "n.style.display=(st==='stale')?'':'none'" in html


def test_health_and_price_freshness_are_separate_concepts(client):
    """Sağlık `HEALTHY` ile fiyat tazeliği TEK kavram gibi gösterilmez."""
    api = client.get("/api/live/health").json()
    assert "health" in api and "price_age_s" in api
    fr = client.get("/api/live/positions").json()["freshness"]
    assert {"price_age_s", "run_age_s", "price_state", "run_state"} <= set(fr)


# --------------------------------------------------------------------------- 19-20 · salt-okunurluk
def _fingerprint(d: Path) -> dict[str, tuple[int, str]]:
    return {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(d.iterdir()) if p.is_file()}


def test_dashboard_get_requests_do_not_touch_state(client, state_dir):
    """19 · Panel GET istekleri state byte/mtime DEĞİŞTİRMEZ."""
    before = _fingerprint(state_dir)
    for path in ("/", "/learning", "/risk", "/trades", "/portfolio/futures",
                 "/api/live/positions", "/api/live/summary", "/api/live/health", "/metrics"):
        assert client.get(path).status_code == 200, path
    assert _fingerprint(state_dir) == before


def test_dashboard_resolves_no_hostname(client, monkeypatch):
    """20a · Panel hiçbir ana makine adı ÇÖZMEZ — dış servise gitmesi imkânsızdır.

    (TestClient ASGI taşıması DNS kullanmaz; gerçek bir dış çağrı `getaddrinfo`'ya düşerdi.)
    """
    import socket

    def _boom(*a, **k):
        raise AssertionError("panel bir ana makine adı çözmeye çalıştı — dış ağ çağrısı")

    monkeypatch.setattr(socket, "getaddrinfo", _boom, raising=False)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _boom, raising=False)
    for path in ("/", "/api/live/positions", "/api/live/summary", "/api/live/health", "/learning",
                 "/metrics", "/risk", "/trades"):
        assert client.get(path).status_code == 200, path


def test_dashboard_layer_imports_no_network_client():
    """20b · Panel modülleri HTTP/borsa istemcisi IMPORT ETMEZ — veri yalnız state dosyalarından."""
    import ast
    forbidden = ("requests", "httpx", "urllib.request", "urllib3", "websocket", "ccxt",
                 "aiohttp", "tradingbot.market", "..market")
    for p in sorted(Path("tradingbot/dashboard").glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + ["." * node.level + (node.module or "")]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for n in names:
                assert not any(n.startswith(f) or n == f for f in forbidden), f"{p.name}: {n}"


def test_dashboard_rejects_mutating_methods(client):
    """Panel salt-okunurdur: POST/PUT/DELETE 405."""
    for m in ("post", "put", "delete", "patch"):
        assert getattr(client, m)("/").status_code == 405


# --------------------------------------------------------------------------- 21 · responsive/taşma
def test_layout_contracts_prevent_page_level_overflow(client):
    """21 · Yatay taşma SAYFAYA değil tablonun kapsayıcısına aittir; mobilde kartlar tek sütun.

    NOT: Bu bir CSS SÖZLEŞME testidir. Bu ortamda tarayıcı/screenshot YOKTUR; 375/768/1440/1920
    piksel GÖRSEL doğrulaması YAPILMAMIŞTIR.
    """
    css = client.get("/").text
    assert "html,body{max-width:100%;overflow-x:hidden}" in css
    assert ".tw{overflow-x:auto" in css and ".tw{max-width:100%}" in css
    assert "white-space:nowrap" in css                       # sayısal sütunlar bölünmez
    assert ".tw table.pos td:nth-child(-n+3)" in css          # Sembol · Piyasa · Yön sticky
    assert "@media(max-width:600px)" in css and ".grid{grid-template-columns:1fr}" in css
    assert ".card .v,.card .small{overflow-wrap:anywhere}" in css   # uzun damga/alan adı taşırmaz
    # Sticky sütunlar SABİT genişlikte olmalı: `left` ofsetleri ancak o zaman doğrudur.
    assert "text-overflow:ellipsis" in css
    widths = [int(m) for m in re.findall(r"\.tw table\.pos td:nth-child\(\d\).*?width:(\d+)px", css)]
    lefts = [int(m) for m in re.findall(r"\.tw table\.pos td:nth-child\(\d\),[^{]*\{left:(\d+)(?:px)?[;}]", css)]
    assert len(widths) >= 3 and len(lefts) == 3, (widths, lefts)
    # her sütunun `left`i, kendinden öncekilerin genişlik toplamına EŞİT olmalı (çakışma yok)
    assert lefts == [0, widths[0], widths[0] + widths[1]], (lefts, widths)


def test_decision_time_is_human_readable_and_keeps_raw_in_tooltip(client):
    """Karar üretim zamanı karttan taşmaz; ham ISO değeri tooltip'te korunur."""
    from tradingbot.dashboard.templates import fmt_utc
    assert fmt_utc("2026-08-22T22:31:41+00:00") == "22.08.2026 22:31:41 UTC"
    assert fmt_utc(None) == "—"
    html = client.get("/").text
    assert "Karar üretim zamanı" in html


# --------------------------------------------------------------------------- 21b · tablo DOM paritesi
class _Node:
    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children, self.text = tag, dict(attrs), [], []

    @property
    def classes(self) -> frozenset:
        return frozenset((self.attrs.get("class") or "").split())

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def find(self, tag=None, **attrs):
        for n in self.walk():
            if n is not self and (tag is None or n.tag == tag) and all(n.attrs.get(k) == v for k, v in attrs.items()):
                return n
        return None

    def all(self, tag):
        return [n for n in self.walk() if n is not self and n.tag == tag]

    def inner_text(self) -> str:
        return "".join(self.text) + "".join(c.inner_text() for c in self.children)


class _Tree(HTMLParser):
    """Küçük DOM ağacı — `<script>` içeriği CDATA'dır, İÇİNDEKİ `<table …>` METNİ eleman DEĞİLDİR.

    Önceki test `'<table class="pos">' in html` diyordu; satır içi polling JS şablonu aynı literali
    taşıdığı için sunucu tablosundan sınıf KALDIRILSA bile test geçiyordu (yalancı yeşil).
    """
    VOID = {"meta", "link", "br", "img", "input", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = _Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in self.VOID:
            self.stack.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].text.append(data)


def _dom(html: str) -> _Node:
    t = _Tree()
    t.feed(html)
    return t.root


def _table_shape(container: _Node) -> dict:
    """`.tw > table` için sınıf/başlık/hücre-sınıf imzası — sunucu ve polling AYNI imzayı vermeli."""
    tw = container.find("div")
    assert tw is not None and "tw" in tw.classes, "`.tw` sarmalayıcı yok"
    tbl = next((c for c in tw.children if c.tag == "table"), None)
    assert tbl is not None, "`.tw > table` yok"
    ths = tbl.all("th")
    # «açık pozisyon yok» satırı (tek `td[colspan]`) veri satırı DEĞİLDİR
    rows = [tr for tr in tbl.all("tr") if tr.all("td") and not any("colspan" in td.attrs for td in tr.all("td"))]
    return {
        "table_classes": tbl.classes,
        "headers": [th.inner_text().strip() for th in ths],
        "header_num": [i for i, th in enumerate(ths) if "num" in th.classes],
        "rows": [[(td.classes & {"num", "up", "dn", "flat"},
                   any("title" in n.attrs for n in td.walk()),      # td veya içindeki <a> title taşır
                   td.inner_text().strip())
                  for td in tr.all("td")] for tr in rows],
    }


def _server_postbl(client) -> _Node:
    root = _dom(client.get("/").text)
    postbl = root.find("div", id="postbl")
    assert postbl is not None, "#postbl yok"
    return postbl


def test_first_render_table_has_sticky_class_in_dom(client):
    """8 · İLK RENDER: `#postbl > .tw > table.pos` GERÇEK DOM'da — inline JS metni SAYILMAZ."""
    shape = _table_shape(_server_postbl(client))
    assert POS_TABLE_CLS in shape["table_classes"], f"sunucu tablosu sınıfı: {shape['table_classes']}"
    assert shape["headers"] == POSITION_COLUMNS
    assert shape["header_num"] == list(POSITION_NUM_COLS)
    assert len(shape["rows"]) == len(POS)


def _build_pos_table_js(html: str) -> tuple[str, list[int], list[int]]:
    """Sayfadaki gerçek `buildPosTable` kaynağını ve enjekte edilen NUM/PNL listelerini çıkar."""
    i = html.index("function buildPosTable(")
    depth, j = 0, html.index("{", i)
    for k in range(j, len(html)):                       # parantez eşleme — iç içe `{}` güvenli
        if html[k] == "{":
            depth += 1
        elif html[k] == "}":
            depth -= 1
            if depth == 0:
                break
    src = html[i:k + 1]
    num = json.loads(re.search(r"var NUMCOLS=(\[[^\]]*\])", html).group(1))
    pnl = json.loads(re.search(r"PNLCOLS=(\[[^\]]*\])", html).group(1))
    return src, num, pnl


def _polled_table_html(client) -> str:
    """Polling tablosunu GERÇEK tarayıcı JS'i ile kur: `buildPosTable` node içinde çalıştırılır.

    Python'da JS'i taklit etmek yerine kaynak olduğu gibi yürütülür; JS'te `i>=3` gibi bir
    sapma olursa bu fonksiyonun çıktısı da sapar (negatif kontrol 8 bunu yakalar).
    """
    html = client.get("/").text
    src, num, pnl = _build_pos_table_js(html)
    payload = client.get("/api/live/positions").json()
    prog = (src + "\nprocess.stdout.write(buildPosTable(JSON.parse(process.argv[1]),"
            + json.dumps(num) + "," + json.dumps(pnl) + "));")
    r = subprocess.run(["node", "-e", prog, json.dumps(payload)], capture_output=True,
                       text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node yok — polling JS gerçek yürütme testi atlanır")
def test_polling_table_built_by_real_js_matches_server(client):
    """9 · POLLING (gerçek JS, node ile): `.tw > table.pos` + başlık sırası + hücre sınıfları
    sunucu ilk render'ı ile BİREBİR (num/up/dn/flat sütun sütun; metin sütunlarında `num` YOK)."""
    srv = _table_shape(_server_postbl(client))
    pol = _table_shape(_dom(_polled_table_html(client)))
    assert POS_TABLE_CLS in pol["table_classes"], "polling tablosu sticky sınıfını taşımıyor"
    assert pol["headers"] == srv["headers"] == POSITION_COLUMNS
    assert pol["header_num"] == srv["header_num"] == list(POSITION_NUM_COLS)
    assert len(pol["rows"]) == len(srv["rows"]) == len(POS)
    for ri, (rs, rp) in enumerate(zip(srv["rows"], pol["rows"])):
        assert len(rs) == len(rp) == len(POSITION_COLUMNS), ri
        for ci, ((cs, _, ts), (cp, tp_has, tp)) in enumerate(zip(rs, rp)):
            assert cs == cp, f"satır {ri} sütun {ci} ({POSITION_COLUMNS[ci]}): sunucu={set(cs)} polling={set(cp)}"
            if ci >= 3:                                   # ilk üç sütun sunucuda rozet/link — metin aynı, sarmalayıcı farklı
                assert ts == tp, f"satır {ri} sütun {ci}: {ts!r} != {tp!r}"
            if ci < 3:
                assert tp_has, f"satır {ri} sütun {ci}: polling `title` yok"
    # up/dn/flat YALNIZ K/Z sütunlarında; «Açılış» ve «İşlem ID» (18, 19) `num` DEĞİL
    for shape, name in ((srv, "sunucu"), (pol, "polling")):
        for row in shape["rows"]:
            for ci, (cls, _, _) in enumerate(row):
                assert ("num" in cls) == (ci in POSITION_NUM_COLS), f"{name}: sütun {ci} num hizalaması"
                assert not (cls & {"up", "dn", "flat"}) or ci in POSITION_PNL_COLS, f"{name}: sütun {ci} renk sınıfı"


def test_polling_js_consumes_injected_alignment_contract(client):
    """9b · JS, `i>=3` gibi KENDİ hizalama kuralını taşımaz; Python sözleşmesini (NUM/PNL) tüketir.

    `node` olmayan ortamda da çalışan kaynak-düzeyi güvence (gerçek yürütme için 9'a bakın).
    """
    html = client.get("/").text
    src, num, pnl = _build_pos_table_js(html)
    assert num == list(POSITION_NUM_COLS) and pnl == list(POSITION_PNL_COLS)
    assert "NUM.indexOf(i)" in src and "PNL.indexOf(i)" in src
    assert not re.search(r"\bi\s*>=\s*3\b", src), "JS'te sabit `i>=3` hizalama kuralı var"
    assert "el.innerHTML=buildPosTable(d,NUMCOLS,PNLCOLS)" in html


def test_empty_positions_polling_table_keeps_contract(tmp_path):
    """Boş pozisyon listesinde polling tablosu da `.tw > table.pos` + doğru başlıkları taşır."""
    if _NODE is None:
        pytest.skip("node yok")
    d = tmp_path / "state"
    d.mkdir()
    (d / "futures_ledger.json").write_text(json.dumps({"schema_version": 2, "kind": "futures",
                                                      "equity": "50", "positions": {}, "history": []}),
                                           encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}), encoding="utf-8")
    c = TestClient(create_app(d, tmp_path / "market", None, DashboardConfig()))
    pol = _table_shape(_dom(_polled_table_html(c)))
    assert POS_TABLE_CLS in pol["table_classes"] and pol["headers"] == POSITION_COLUMNS
    assert pol["rows"] == []                    # yalnız «açık pozisyon yok» satırı (td.mut, sayılmaz)


# --------------------------------------------------------------------------- 22-24 · öğrenme
def test_learning_small_sample_banner(client):
    """22 · <30 kapanmış işlem → «Yetersiz örneklem»."""
    html = client.get("/learning").text
    assert "Yetersiz örneklem — performans sonucu kesin değildir" in html


@pytest.mark.parametrize("n,expected", [
    (0, "Yetersiz örneklem"), (29, "Yetersiz örneklem"),
    (30, "Sınırlı örneklem"), (49, "Sınırlı örneklem"),
    (50, "Değerlendirilebilir örneklem"), (500, "Değerlendirilebilir örneklem"),
])
def test_sample_bands(n, expected):
    from tradingbot.dashboard.templates import sample_banner
    assert expected in sample_banner(n)


def test_agent_zero_over_zero_is_no_data_not_zero_pct(client):
    """23 · Ajan `0/0` → «Veri yok»; «%0» YAZILMAZ (yanıldı anlamına gelmez)."""
    html = client.get("/learning").text
    sec = html.split("Ajan isabet", 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr>(.*?)</tr>", sec, re.S)
    trend = next(r for r in rows if ">trend<" in r)
    edge = next(r for r in rows if ">edge<" in r)
    assert "Veri yok" in trend and "%0" not in re.sub(r"<[^>]+>", "", trend)
    assert "%60" in edge                                     # 3/5 ölçüm VAR → oran gösterilir


def test_learning_labels_are_turkish_and_weights_signed(client):
    """Ham iç alan adları Türkçeleştirilir; ağırlıklar işaretli ve anlamlı ondalıkla gösterilir."""
    html = client.get("/learning").text
    for t in ("Kapanmış işlem", "Kazanan", "Kazanmayan", "Toplam R", "Ortalama R", "Son güncelleme"):
        assert t in html, t
    assert "+0.123" in html and "-0.0457" in html            # 0.123456 → 3 hane, -0.045678 → 4 hane
    assert "Bu ağırlık TEK BAŞINA işlem kararı değildir" in html


def test_learning_long_why_is_truncated_with_details(client):
    """Uzun «why» metni hücreyi büyütmez: kısaltılmış önizleme + açılır detay."""
    html = client.get("/learning").text
    assert "<details><summary" in html
    sec = html.split("Öğrenilen ders", 1)[1]
    assert "…" in sec, "uzun gerekçe kısaltılmamış"


def _learning_cards(client) -> dict[str, str]:
    html = client.get("/learning").text
    cards = dict(re.findall(r'<div class="k">(.*?)</div><div class="v">(.*?)</div>', html, re.S))
    return {re.sub(r"<[^>]+>", "", k).strip(): re.sub(r"<[^>]+>", "", v).strip() for k, v in cards.items()}


def test_learning_totals_match_all_time_counters(client, state_dir):
    """24 · Öğrenme kartları TÜM ZAMAN sayaçlarından gelir — `lessons` uzunluğundan DEĞİL.

    Fixture: `n_trades=5`, `n_wins=2`, fakat `lessons` YALNIZ 2 kayıt. Eski kod kazanan/kaybeden'i
    `lessons`tan sayıp `1 / 1 · %50.0` gösteriyordu; doğrusu `2 kazanan / 3 kazanmayan · %40.0`.
    """
    ln = json.loads((state_dir / "learning.json").read_text(encoding="utf-8"))
    assert (ln["n_trades"], ln["n_wins"], len(ln["lessons"])) == (5, 2, 2)   # kurulum kontrolü
    c = _learning_cards(client)
    assert c["Kapanmış işlem"] == "5"          # n_trades (lessons=2 DEĞİL)
    assert c["Kazanan"] == "2"                 # n_wins   (lessons'tan sayılan 1 DEĞİL)
    assert c["Kazanmayan"] == "3"              # n_trades − n_wins (kaybeden + başa baş)
    assert c["Kazanma oranı"] == "%40.0"       # n_wins / n_trades — %50.0 DEĞİL
    assert c["Toplam R"] == f"{float(ln['sum_r']):+.3f}R"


def test_learning_window_holds_when_lessons_are_truncated(tmp_path):
    """`lessons` 200 ile budandığında bile üst kartlar TÜM ZAMAN sayaçlarını gösterir."""
    d = tmp_path / "state"
    d.mkdir()
    (d / "learning.json").write_text(json.dumps({
        "n_trades": 250, "n_wins": 100, "sum_r": 12.5, "updated_at": "2026-08-22T22:00:00+00:00",
        "lessons": [{"id": f"T{i}", "won": i % 2 == 0, "r": 0.1} for i in range(200)]}), encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}), encoding="utf-8")
    c = TestClient(create_app(d, tmp_path / "market", None, DashboardConfig()))
    cards = _learning_cards(c)
    assert cards["Kapanmış işlem"] == "250"    # budanmış 200 DEĞİL
    assert cards["Kazanan"] == "100"           # lessons'taki 100 won=True ile ÇAKIŞMASIN diye 250/100 seçildi
    assert cards["Kazanmayan"] == "150"
    assert cards["Kazanma oranı"] == "%40.0"   # 100/250 — lessons'tan gelseydi %50.0 olurdu
    assert cards["Ortalama R"] == f"{12.5 / 250:+.3f}R"
    # 200 SAKLAMA SINIRI DEĞİLDİR — metin artık «en fazla 200 ders tutar» İDDİA ETMEZ.
    html = c.get("/learning").text
    assert "Ekranda son 200 ders gösteriliyor" in html
    assert "en fazla 200 ders tutar" not in html


def test_learning_missing_counters_report_no_data(tmp_path):
    """Sayaç alanı yoksa `Veri yok` — `lessons`tan TÜRETİLMEZ, sessiz `0` da gösterilmez."""
    d = tmp_path / "state"
    d.mkdir()
    (d / "learning.json").write_text(json.dumps({
        "updated_at": "2026-08-22T22:00:00+00:00",
        "lessons": [{"id": "T1", "won": True, "r": 1.0}, {"id": "T2", "won": False, "r": -1.0}]}),
        encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}), encoding="utf-8")
    cards = _learning_cards(TestClient(create_app(d, tmp_path / "market", None, DashboardConfig())))
    for k in ("Kapanmış işlem", "Kazanan", "Kazanmayan", "Kazanma oranı"):
        assert cards[k] == "Veri yok", k


def _learning_client(tmp_path, ln: dict):
    d = tmp_path / "state"
    d.mkdir(exist_ok=True)
    (d / "learning.json").write_text(json.dumps(ln), encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}), encoding="utf-8")
    return TestClient(create_app(d, tmp_path / "market", None, DashboardConfig()), raise_server_exceptions=False)


_LESSON = {"id": "L1", "symbol": "A/USDT", "side": "LONG", "won": True, "r": 0.9, "pnl": 1.2,
           "exit": "tp", "bars": 8, "mae": -2.0, "mfe": 5.0, "setup": "brk",
           "at": "2026-08-20T01:00:00+00:00", "why": "kisa"}


def _lesson(**kw):
    d = dict(_LESSON)
    d.update(kw)
    return d


@pytest.mark.parametrize("name,ln,expect", [
    ("sum_r=\"nope\"", {"n_trades": 5, "n_wins": 2, "sum_r": "nope", "lessons": [_lesson()]},
     {"Toplam R": "Veri yok", "Ortalama R": "Veri yok"}),
    ("sum_r=\"Infinity\"", {"n_trades": 5, "n_wins": 2, "sum_r": "Infinity", "lessons": [_lesson()]},
     {"Toplam R": "Veri yok", "Ortalama R": "Veri yok"}),
    ("sum_r=Infinity literal", {"n_trades": 5, "n_wins": 2, "sum_r": float("inf"), "lessons": [_lesson()]},
     {"Toplam R": "Veri yok"}),
    ("sum_r=NaN", {"n_trades": 5, "n_wins": 2, "sum_r": float("nan"), "lessons": [_lesson()]},
     {"Toplam R": "Veri yok"}),
    ("lesson.r=\"abc\"", {"n_trades": 1, "n_wins": 1, "sum_r": 0.9, "lessons": [_lesson(r="abc")]}, {}),
    ("lesson.r=Infinity", {"n_trades": 1, "n_wins": 1, "sum_r": 0.9, "lessons": [_lesson(r=float("inf"))]}, {}),
    ("lesson.mae=\"-Infinity\"", {"n_trades": 1, "n_wins": 1, "sum_r": 0.9, "lessons": [_lesson(mae="-Infinity")]}, {}),
    ("lesson.mfe=\"NaN\"", {"n_trades": 1, "n_wins": 1, "sum_r": 0.9, "lessons": [_lesson(mfe="NaN")]}, {}),
    ("lesson.pnl=\"Infinity\"", {"n_trades": 1, "n_wins": 1, "sum_r": 0.9, "lessons": [_lesson(pnl="Infinity")]}, {}),
    ("lesson.bars=\"x\"", {"n_trades": 1, "n_wins": 1, "sum_r": 0.9, "lessons": [_lesson(bars="x")]}, {}),
])
def test_learning_page_survives_corrupt_numerics(tmp_path, name, ln, expect):
    """6 · Bozuk `sum_r` / ders sayısal alanı → HTTP 200 ve «Veri yok»/«—»; asla 500, asla `inf`.

    REGRESYON: `_r(x)` çıplak `float(x)` kullanıyordu → `sum_r="nope"` ValueError → HTTP 500.
    """
    c = _learning_client(tmp_path, ln)
    r = c.get("/learning")
    assert r.status_code == 200, f"{name}: HTTP {r.status_code}"
    assert not re.search(r"[+-]?inf\b|\bnan\b", r.text, re.I), f"{name}: ham inf/nan sızdı"
    cards = _learning_cards(c)
    for k, v in expect.items():
        assert cards[k] == v, f"{name}: {k}={cards[k]!r}"
    sec = r.text.split("Dersler", 1)[1] if "Dersler" in r.text else ""
    assert "+infR" not in sec and "-infR" not in sec and "inf%" not in sec and "nan%" not in sec


def test_learning_valid_r_formatting_unchanged(tmp_path):
    """6b · Geçerli R değerlerinin işareti/biçimi korunur (+/- · 3 hane · `R` soneki)."""
    c = _learning_client(tmp_path, {"n_trades": 4, "n_wins": 1, "sum_r": -0.153821,
                                    "lessons": [_lesson(r=0.9), _lesson(id="L2", r=-1.0444, won=False)]})
    cards = _learning_cards(c)
    assert cards["Toplam R"] == "-0.154R" and cards["Ortalama R"] == "-0.038R"
    html = c.get("/learning").text
    assert "+0.900R" in html and "-1.044R" in html


@pytest.mark.parametrize("name,ln,bad_marker", [
    ("n_wins>n_trades", {"n_trades": 3, "n_wins": 7}, "çelişkili sayaç"),
    ("negatif ikisi", {"n_trades": -5, "n_wins": -2}, "n_trades negatif"),
    ("n_wins negatif", {"n_trades": 5, "n_wins": -1}, "n_wins negatif"),
])
def test_learning_contradictory_counters_are_not_rendered_as_numbers(tmp_path, name, ln, bad_marker):
    """7 · Çelişkili/negatif sayaç → dört kart da «Veri yok» + açık neden; negatif «Kazanmayan» ve
    %100 üstü oran UYDURULMAZ, değerler de sessizce KIRPILMAZ (7→3, -5→0 yapılmaz)."""
    ln = dict(ln, sum_r=1.0, lessons=[_lesson()])
    c = _learning_client(tmp_path, ln)
    r = c.get("/learning")
    assert r.status_code == 200, name
    cards = _learning_cards(c)
    for k in ("Kapanmış işlem", "Kazanan", "Kazanmayan", "Kazanma oranı"):
        assert cards[k] == "Veri yok", f"{name}: {k}={cards[k]!r}"
    assert bad_marker in r.text, name
    # kırpılmış sahte değer de yok (3 / 0 gibi), negatif de yok, %100 üstü de yok
    assert not re.search(r'<div class="v">-\d', r.text), f"{name}: negatif kart"
    assert not re.search(r"%(1\d\d|[2-9]\d\d)\.\d", r.text), f"{name}: %100 üstü oran"


@pytest.mark.parametrize("name,ln,expect", [
    ("n_trades=\"abc\"", {"n_trades": "abc", "n_wins": 2},
     {"Kapanmış işlem": "Veri yok", "Kazanan": "2", "Kazanmayan": "Veri yok", "Kazanma oranı": "Veri yok"}),
    ("n_wins=\"abc\"", {"n_trades": 5, "n_wins": "abc"},
     {"Kapanmış işlem": "5", "Kazanan": "Veri yok", "Kazanmayan": "Veri yok", "Kazanma oranı": "Veri yok"}),
    ("n_trades=None", {"n_trades": None, "n_wins": 2},
     {"Kapanmış işlem": "Veri yok", "Kazanan": "2", "Kazanmayan": "Veri yok", "Kazanma oranı": "Veri yok"}),
    ("n_wins=None", {"n_trades": 5, "n_wins": None},
     {"Kapanmış işlem": "5", "Kazanan": "Veri yok", "Kazanmayan": "Veri yok", "Kazanma oranı": "Veri yok"}),
    ("geçerli 0/0", {"n_trades": 0, "n_wins": 0},
     {"Kapanmış işlem": "0", "Kazanan": "0", "Kazanmayan": "0", "Kazanma oranı": "Veri yok"}),
    ("geçerli 5/2", {"n_trades": 5, "n_wins": 2},
     {"Kapanmış işlem": "5", "Kazanan": "2", "Kazanmayan": "3", "Kazanma oranı": "%40.0"}),
    ("geçerli 250/100", {"n_trades": 250, "n_wins": 100},
     {"Kapanmış işlem": "250", "Kazanan": "100", "Kazanmayan": "150", "Kazanma oranı": "%40.0"}),
])
def test_learning_counter_contract(tmp_path, name, ln, expect):
    """7b · Bozuk/eksik sayaç → ilgili kartlar «Veri yok»; geçerli sözleşme (0/0, 5/2, 250/100) aynen."""
    c = _learning_client(tmp_path, dict(ln, sum_r=1.0, lessons=[_lesson()]))
    assert c.get("/learning").status_code == 200, name
    cards = _learning_cards(c)
    for k, v in expect.items():
        assert cards[k] == v, f"{name}: {k}={cards[k]!r} (beklenen {v!r})"
    assert "n_trades − n_wins" in c.get("/learning").text          # açıklama korunuyor


# --------------------------------------------------------------------------- 25-27 · güvenlik / algoritma
def test_paper_and_safety_invariants_unchanged(client):
    """26 · PAPER · live order path false · gerçek emir 0."""
    h = client.get("/api/live/health").json()
    assert str(h["mode"]).upper() == "PAPER"
    from tradingbot.risk import ModeState
    assert ModeState.is_live_order_path_enabled() is False


def test_telegram_off_and_leverage_bounded_paper_only():
    """25 · Shipped config: Telegram KAPALI; dinamik kaldıraç AÇIK ama 2x–5x ve YALNIZ PAPER.

    Kaldıraç bilinçli olarak etkinleştirildi (bkz. `config.yaml → leverage:`). Değişmez sözleşme
    artık "kapalı" değil, "SINIRLI ve PAPER'a kilitli"dir: 1x yeni giriş yasak, 5x mutlak tavan,
    `paper_only=true` ile LIVE/TESTNET'te açılamaz. Telegram ve gerçek emir yolu KAPALI kalır.
    """
    from tradingbot.config import load_config
    cfg = load_config()
    assert cfg.v3.telegram.enabled is False
    assert cfg.v3.monitoring.telegram_enabled is False
    assert cfg.v3.leverage.enabled is True
    assert cfg.v3.leverage.paper_only is True
    assert cfg.v3.leverage.min_leverage == 2          # 1x yeni futures girişi YASAK
    assert cfg.v3.leverage.max_leverage == 5          # MUTLAK tavan
    assert cfg.mode == "PAPER" and cfg.v3.mode.live_trading is False
    assert cfg.v3.execution.gateway == "paper" and cfg.v3.execution.testnet_enabled is False


def test_algorithm_layer_does_not_import_dashboard():
    """27 · Trade motoru panel katmanını IMPORT ETMEZ — panel değişiklikleri algoritmaya sızamaz."""
    import ast
    roots = ["tradingbot/engine_v3.py", "tradingbot/decision.py", "tradingbot/decision_gates.py",
             "tradingbot/opportunity.py", "tradingbot/risk/engine.py", "tradingbot/risk/leverage.py",
             "tradingbot/learning.py", "tradingbot/accounting/futures_ledger.py",
             "tradingbot/coinhead/chief.py"]
    for rel in roots:
        p = Path(rel)
        if not p.exists():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mod = ""
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            elif isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            assert "dashboard" not in mod, f"{rel} panel katmanını import ediyor: {mod}"


def test_pnl_changes_are_additive_only():
    """27b · `pnl.py` eklemeleri mevcut alanları DEĞİŞTİRMEZ (altın değerler)."""
    from tradingbot.pnl import position_view
    v = position_view({"id": "G1", "symbol": "BTC/USDT", "side": "LONG", "qty": "0.02",
                       "entry_avg": "50000", "leverage": 4, "isolated_margin": "250",
                       "stop": "49000", "targets": ["52000"], "last_price": "50500",
                       "entry_fee": "0.5", "market_type": "USDM_PERP"})
    from decimal import Decimal
    assert v.notional == Decimal("1000") and v.initial_margin == Decimal("250")
    assert v.gross_unrealized == Decimal("10") and v.leverage == 4
    from tradingbot.pnl import PCT_BASIS_MARGIN
    assert v.pct_basis == PCT_BASIS_MARGIN
    assert v.stop_risk == Decimal("20")                       # YENİ alan — eskiler etkilenmedi
