"""Panel ENTEGRASYON regresyonları — HTML↔API paritesi, polling, stale, salt-okunurluk, güvenlik.

Gerçek `create_app()` uygulaması TestClient ile çalıştırılır. HİÇBİR dış ağ çağrısı yapılmaz;
worker/dashboard süreci başlatılmaz; state dosyaları YAZILMAZ (bu paket bunu ölçerek kanıtlar).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient          # noqa: E402

from tradingbot.dashboard.app import create_app    # noqa: E402
from tradingbot.dashboard.config import DashboardConfig  # noqa: E402
from tradingbot.dashboard.views import Freshness   # noqa: E402

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
    (d / "risk.json").write_text(json.dumps({
        "generated_at": "2026-08-22T22:31:41+00:00", "mode": "PAPER",
        "profile": {"max_total_open_risk_pct": 6.0, "risk_per_trade_pct": 2.0},
        "killswitch": {"state": "ARMED"},
        "exposure": {"equity": 50.09, "drawdown_pct": 2.13, "total_open_risk_usdt": 8.83,
                     "open_positions": 2, "used_margin": 28.38861}}), encoding="utf-8")
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
    assert s["today_realized_net_usdt"] == pytest.approx(2.86)


def test_api_cards_carry_machine_value_and_display(client):
    """Kartlar hem ham `value` hem biçimlenmiş `display` taşır (polling bunu kullanır)."""
    cards = client.get("/api/live/summary").json()["cards"]
    keys = {c["key"] for c in cards}
    assert {"today_realized_net_usdt", "profit_factor", "margin_utilization_pct",
            "open_stop_risk_usdt", "risk_engine_reserved_usdt"} <= keys
    pf = next(c for c in cards if c["key"] == "profit_factor")
    assert pf["display"] == "1.03" and "$" not in pf["display"]


# --------------------------------------------------------------------------- 16 · polling
def test_polling_script_updates_summary_cards_and_wraps_table(client):
    """16 · Polling kartları GERÇEKTEN günceller ve tabloyu kendi kapsayıcısına sarar."""
    html = client.get("/").text
    assert "poll('sum','/api/live/summary'" in html
    assert "function(d){" in html.split("poll('sum'", 1)[1][:80], "summary callback boş bırakılmış"
    assert "getElementById('sc-'+c.key)" in html, "kart güncelleme kodu yok"
    assert """el.innerHTML='<div class="tw">'""" in html, "polling tabloyu .tw ile sarmıyor"
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
    assert ".card .v{overflow-wrap:anywhere}" in css          # uzun damga kartı taşırmaz


def test_decision_time_is_human_readable_and_keeps_raw_in_tooltip(client):
    """Karar üretim zamanı karttan taşmaz; ham ISO değeri tooltip'te korunur."""
    from tradingbot.dashboard.templates import fmt_utc
    assert fmt_utc("2026-08-22T22:31:41+00:00") == "22.08.2026 22:31:41 UTC"
    assert fmt_utc(None) == "—"
    html = client.get("/").text
    assert "Karar üretim zamanı" in html


def test_positions_table_has_sticky_class(client):
    """Açık pozisyon tablosu sticky sütun sınıfını taşır."""
    assert '<table class="pos">' in client.get("/").text


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
    for t in ("Kapanmış işlem", "Kazanan", "Kaybeden", "Toplam R", "Ortalama R", "Son güncelleme"):
        assert t in html, t
    assert "+0.123" in html and "-0.0457" in html            # 0.123456 → 3 hane, -0.045678 → 4 hane
    assert "Bu ağırlık TEK BAŞINA işlem kararı değildir" in html


def test_learning_long_why_is_truncated_with_details(client):
    """Uzun «why» metni hücreyi büyütmez: kısaltılmış önizleme + açılır detay."""
    html = client.get("/learning").text
    assert "<details><summary" in html
    sec = html.split("Öğrenilen ders", 1)[1]
    assert "…" in sec, "uzun gerekçe kısaltılmamış"


def test_learning_totals_match_state(client, state_dir):
    """24 · Öğrenme sayfası hesapları mevcut state ile paritededir."""
    ln = json.loads((state_dir / "learning.json").read_text(encoding="utf-8"))
    html = client.get("/learning").text
    cards = dict(re.findall(r'<div class="k">(.*?)</div><div class="v">(.*?)</div>', html, re.S))
    clean = {re.sub(r"<[^>]+>", "", k).strip(): re.sub(r"<[^>]+>", "", v).strip() for k, v in cards.items()}
    assert clean["Kapanmış işlem"] == str(ln["n_trades"])
    assert clean["Toplam R"] == f"{float(ln['sum_r']):+.3f}R"
    wins = sum(1 for x in ln["lessons"] if x.get("won") is True)
    losses = sum(1 for x in ln["lessons"] if x.get("won") is False)
    assert clean["Kazanan"] == str(wins) and clean["Kaybeden"] == str(losses)


# --------------------------------------------------------------------------- 25-27 · güvenlik / algoritma
def test_paper_and_safety_invariants_unchanged(client):
    """26 · PAPER · live order path false · gerçek emir 0."""
    h = client.get("/api/live/health").json()
    assert str(h["mode"]).upper() == "PAPER"
    from tradingbot.risk import ModeState
    assert ModeState.is_live_order_path_enabled() is False


def test_telegram_and_leverage_still_disabled():
    """25 · Shipped config'te Telegram ve dinamik kaldıraç HÂLÂ kapalı."""
    from tradingbot.config import load_config
    cfg = load_config()
    assert cfg.v3.telegram.enabled is False
    assert cfg.v3.monitoring.telegram_enabled is False
    assert cfg.v3.leverage.enabled is False
    assert cfg.v3.leverage.paper_only is True
    assert cfg.v3.leverage.max_leverage <= 5
    assert cfg.mode == "PAPER" and cfg.v3.mode.live_trading is False


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
