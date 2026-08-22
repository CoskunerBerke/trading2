"""Dinamik 2x–5x kaldıraç + Telegram bildirim regresyonları.

KALDIRAÇ RİSKİ ARTIRMAZ: notional risk bütçesi/stop mesafesinden gelir; kaldıraç yalnızca
`initial_margin = notional / leverage` değerini belirler. Stopta beklenen maksimum dolar zararı
2x–5x arasında AYNIDIR.

TELEGRAM: kapalıyken ağ çağrısı yoktur, token hiçbir çıktıya sızmaz, duplicate olay gönderilmez ve
restart sonrası eski açık pozisyonlar için sahte "açıldı" bildirimi üretilmez.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_engine_v3 import _engine  # noqa: E402

from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.core import ConfigError  # noqa: E402
from tradingbot.notify import (EVENT_CLOSED, EVENT_OPENED, NotifyOutbox, TelegramTransport,  # noqa: E402
                               TradeNotifier, build_closed, build_daily_summary, build_opened,
                               event_id, redact)
from tradingbot.pnl import portfolio_view, position_view  # noqa: E402
from tradingbot.risk.engine import size_position  # noqa: E402
from tradingbot.risk.leverage import LeverageConfig, LeverageContext, select_leverage  # noqa: E402

TOKEN = "123456789:AAF" + "x" * 30          # SENTETİK — gerçek token DEĞİL
CHAT = "-1001234567890"


def _ctx(**kw) -> LeverageContext:
    base = dict(stop_frac=0.04, atr_pct=3.0, confidence=0.80, conservative_net_edge_r=0.6,
                depth_usdt=500_000.0, spread_pct=0.02, funding_pct=0.005, regime_aligned=True,
                open_risk_frac=0.2, same_direction_open=1, portfolio_corr=0.3)
    base.update(kw)
    return LeverageContext(**base)


CFG = LeverageConfig(enabled=True)


# ===================================================================== 1) seviye seçimi
def test_strongest_conditions_select_5x():
    d = select_leverage(_ctx(), CFG)
    assert d.leverage == 5 and "TIER_5X_SATISFIED" in d.reasons
    assert d.liq_buffer_mult >= CFG.liq_buffer_5x


def test_five_is_the_absolute_ceiling():
    d = select_leverage(_ctx(stop_frac=0.005, confidence=0.99, conservative_net_edge_r=5.0), CFG)
    assert d.leverage <= 5
    assert select_leverage(_ctx(), LeverageConfig(enabled=True, max_leverage=5)).leverage <= 5


@pytest.mark.parametrize("kw,expected", [
    ({}, 5),
    ({"portfolio_corr": 0.95}, 4),                     # korelasyon 5x'i engeller
    ({"atr_pct": 7.0}, 3),                             # volatilite 4x/5x'i engeller
    ({"confidence": 0.40}, 2),                         # güven yalnız tabana yeter
])
def test_tier_selection_is_deterministic(kw, expected):
    a = select_leverage(_ctx(**kw), CFG)
    b = select_leverage(_ctx(**kw), CFG)
    assert a.leverage == b.leverage == expected, (kw, a.leverage, a.blocked_higher[:3])
    assert a.to_dict() == b.to_dict(), "aynı girdi → aynı çıktı (rastgelelik yok)"


def test_minimum_is_two_never_one():
    for kw in ({}, {"confidence": 0.31}, {"conservative_net_edge_r": 0.0}):
        d = select_leverage(_ctx(**kw), CFG)
        assert d.leverage == 0 or d.leverage >= 2, f"1x yeni futures işlemi açılamaz: {d.leverage}"


def test_weak_signal_is_no_trade_not_forced_2x():
    """Zayıf sinyal sırf taban 2x diye AÇILMAZ — NO_TRADE üretir."""
    d = select_leverage(_ctx(confidence=0.10), CFG)
    assert d.leverage == 0 and "CONFIDENCE_BELOW_BASE" in d.blocked_higher
    assert not d.tradeable


def test_missing_or_stale_data_blocks_the_trade():
    assert select_leverage(_ctx(data_stale=True), CFG).leverage == 0
    assert select_leverage(_ctx(data_conflict=True), CFG).leverage == 0
    assert select_leverage(_ctx(stop_frac=None), CFG).leverage == 0
    assert select_leverage(_ctx(confidence=None), CFG).leverage == 0


def test_unknown_optional_input_blocks_upgrade_not_base():
    """Bilinmeyen alan fail-closed: yükseltme verilmez, fakat taban işlemi düşürmez."""
    d = select_leverage(_ctx(depth_usdt=None), CFG)
    assert 2 <= d.leverage <= 3
    assert any("UNKNOWN" in c for c in d.blocked_higher)


def test_five_x_needs_a_stricter_liquidation_buffer():
    wide = select_leverage(_ctx(stop_frac=0.05), CFG)      # (1/5-0.004)/0.05 = 3.92 < 4.5
    assert wide.leverage == 4 and any("LIQ_BUFFER_BELOW_5X" in c for c in wide.blocked_higher)
    tight = select_leverage(_ctx(stop_frac=0.04), CFG)     # 4.90 ≥ 4.5
    assert tight.leverage == 5


def test_decision_records_reasons_and_blockers():
    d = select_leverage(_ctx(atr_pct=7.0), CFG)
    j = d.to_dict()
    assert j["leverage"] == 3 and j["reasons"] and j["blocked_higher"]
    assert j["liq_buffer_mult"] is not None and j["tier_checks"]


# ===================================================================== 2) risk bütçesi korunuyor
@pytest.mark.parametrize("lev", [1, 2, 3, 4, 5])
def test_leverage_never_increases_dollar_risk_at_stop(lev):
    """`size_position` notional'ı RİSKTEN türetir; kaldıraç yalnız marjı böler."""
    res = size_position(equity=5_000.0, risk_pct=2.0, entry=100.0, stop=98.0, min_notional=5.0,
                        max_leverage=5, max_position_pct=100.0, requested_leverage=lev)
    assert res.ok
    stop_frac = 0.02
    assert res.notional * stop_frac == pytest.approx(100.0, rel=1e-6), "risk 2% × 5000 = 100 USDT"
    assert res.margin == pytest.approx(res.notional / res.leverage, rel=1e-6)
    assert res.risk_usdt == pytest.approx(100.0, rel=1e-6)


def test_margin_equals_notional_divided_by_leverage():
    for lev in (2, 3, 4, 5):
        res = size_position(equity=5_000.0, risk_pct=1.0, entry=200.0, stop=190.0, min_notional=5.0,
                            max_leverage=5, max_position_pct=100.0, requested_leverage=lev)
        assert res.margin == pytest.approx(res.notional / lev, rel=1e-6)


def test_max_loss_at_stop_is_identical_across_leverages():
    losses = []
    for lev in (2, 3, 4, 5):
        r = size_position(equity=5_000.0, risk_pct=0.5, entry=100.0, stop=96.0, min_notional=5.0,
                          max_leverage=5, max_position_pct=100.0, requested_leverage=lev)
        losses.append(round(r.notional * 0.04, 6))
    assert len(set(losses)) == 1, f"kaldıraç stoptaki zararı değiştirdi: {losses}"
    assert losses[0] == pytest.approx(25.0, rel=1e-6)      # %0.5 × 5000


# ===================================================================== 3) motor entegrasyonu
def _leveraged_engine(tmp_path, monkeypatch, **over):
    ov = {"leverage": {"enabled": True, **over}}
    return _engine(tmp_path, monkeypatch, ov, symbols=4, equity=5_000.0)


def test_engine_opens_futures_between_two_and_five(tmp_path, monkeypatch):
    from test_risk_capacity_and_gates import _force_final_risk_pct, _force_triggers
    eng = _leveraged_engine(tmp_path, monkeypatch)
    assert eng.leverage_cfg.enabled and eng.leverage_cfg.min_leverage == 2
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 for s in eng.cfg.coins})
    _force_triggers(monkeypatch, True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert eng.ledger2.positions, "senaryo geçersiz: hiç pozisyon açılmadı"
    for sym, p in eng.ledger2.positions.items():
        assert 2 <= p.leverage <= 5, (sym, p.leverage)
        assert p.isolated_margin == pytest.approx(p.qty * p.entry_avg / p.leverage, rel=1e-6)
        assert (p.meta or {}).get("leverage_decision"), "kaldıraç gerekçesi kaydedilmeli"
        snap = (p.meta or {}).get("risk_snapshot") or {}
        for k in ("final_notional", "initial_margin", "stop_frac", "max_loss_at_stop_usdt"):
            assert k in snap, k


def test_engine_risk_at_stop_unchanged_by_leverage(tmp_path, monkeypatch):
    """Kaldıraç açıkken de stoptaki gerçek zarar %0.5 risk bütçesini AŞMAZ."""
    from test_risk_capacity_and_gates import _force_final_risk_pct, _force_triggers, _open_risk
    eng = _leveraged_engine(tmp_path, monkeypatch)
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 for s in eng.cfg.coins})
    _force_triggers(monkeypatch, True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    for p in eng.ledger2.positions.values():
        risk = abs(float(p.entry_avg) - float(p.stop)) * float(p.qty)
        assert risk <= 5_000.0 * 0.5 / 100.0 + 1e-6, risk
    assert _open_risk(eng) <= 5_000.0 * eng.profile.max_total_open_risk_pct / 100.0 + 1e-6


def test_spot_positions_get_no_leverage(tmp_path, monkeypatch):
    v = position_view({"id": "S1", "symbol": "X/USDT", "side": "LONG", "qty": "5",
                       "entry_avg": "10", "notional": "50", "market_type": "SPOT"},
                      mark_price="11")
    assert v.market == "SPOT" and v.leverage == 1
    ev = build_opened(v)
    assert "Kaldıraç: Yok" in ev.text and "Piyasa: SPOT" in ev.text


def test_dynamic_leverage_is_disabled_by_default(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=2, equity=5_000.0)
    assert eng.leverage_cfg.enabled is False, "varsayılan KAPALI"
    assert load_v3({"mode": "PAPER"}).leverage.enabled is False


def test_live_and_testnet_default_off():
    """`paper_only=True` iken PAPER dışında etkinleştirilemez."""
    assert load_v3({"mode": "PAPER"}).leverage.paper_only is True
    with pytest.raises(ConfigError):
        load_v3({"mode": "TESTNET", "leverage": {"enabled": True}})
    ok = load_v3({"mode": "TESTNET", "leverage": {"enabled": True, "paper_only": False}})
    assert ok.leverage.enabled is True                     # bilinçli olarak açılabilir


def test_existing_positions_keep_their_leverage_snapshot(tmp_path, monkeypatch):
    """Eski 1x pozisyonlar deployment'tan sonra DEĞİŞMEZ: kaldıraç/qty/entry/stop korunur."""
    from tradingbot.accounting import FuturesLedgerV2
    eng = _leveraged_engine(tmp_path, monkeypatch)
    led = eng.ledger2
    from tradingbot.accounting.models import MarketType, Position, PositionSide
    old = Position(id="F00001", symbol="LEGACY/USDT", market_type=MarketType.USDM_PERP,
                   side=PositionSide.LONG, qty=D("3"), entry_avg=D("50"), leverage=1,
                   isolated_margin=D("150"), stop=D("48"), targets=[D("55")],
                   opened_at="2026-08-01T00:00:00+00:00")
    led.positions["LEGACY/USDT"] = old
    led.save(eng.ledger_path)
    again = FuturesLedgerV2.load(eng.ledger_path, starting_equity=5_000.0)
    p = again.positions["LEGACY/USDT"]
    assert (p.id, p.leverage, p.qty, p.entry_avg, p.stop) == ("F00001", 1, D("3"), D("50"), D("48"))
    assert p.targets == [D("55")], "TP değişmemeli"


# ===================================================================== 4) Telegram
class _FakeHttp:
    """Ağ YOK. Çağrıları kaydeder; `fail`/`boom` ile hata senaryosu kurar."""

    def __init__(self, status: int = 200, boom: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict, float]] = []
        self.status, self.boom = status, boom

    def __call__(self, url: str, body: dict, timeout: float) -> int:
        self.calls.append((url, body, timeout))
        if self.boom:
            raise self.boom
        return self.status


class _Cfg:
    enabled = True
    bot_token_env = "TB_TG_TOKEN"
    chat_id_env = "TB_TG_CHAT"
    timeout_s = 5.0
    max_retries = 3
    outbox_file = "notify_outbox.json"
    outbox_keep = 500
    suppress_backlog_on_start = True


def _notifier(tmp_path, http=None, *, enabled=True, env=None):
    cfg = _Cfg()
    cfg.enabled = enabled
    return TradeNotifier.from_config(cfg, tmp_path, http=http,
                                     env=env if env is not None else {"TB_TG_TOKEN": TOKEN, "TB_TG_CHAT": CHAT})


def _view():
    return position_view({"id": "F00008", "symbol": "ZRO/USDT", "side": "LONG", "qty": "16.48",
                          "entry_avg": "0.91", "leverage": 3, "isolated_margin": "5.00",
                          "notional": "15.00", "stop": "0.88", "targets": ["0.97"],
                          "entry_fee": "0.0075", "opened_at": "2026-08-22T10:00:00+00:00",
                          "market_type": "USDM_PERP"}, mark_price="0.91")


def test_disabled_telegram_makes_no_network_call(tmp_path):
    http = _FakeHttp()
    n = _notifier(tmp_path, http, enabled=False)
    assert n.transport is None
    assert n.notify(build_opened(_view())) is False
    assert http.calls == [], "KAPALIYKEN hiçbir ağ çağrısı yapılmamalı"


def test_missing_token_or_chat_is_fail_safe(tmp_path):
    http = _FakeHttp()
    for env in ({}, {"TB_TG_TOKEN": TOKEN}, {"TB_TG_CHAT": CHAT}):
        n = _notifier(tmp_path / str(len(env)), http, env=env)
        assert n.transport is None
        assert n.notify(build_opened(_view())) is False
    assert http.calls == []


def test_token_never_leaks_to_logs_status_or_exceptions(tmp_path, caplog):
    http = _FakeHttp(boom=RuntimeError(f"connect failed for {TOKEN}"))
    n = _notifier(tmp_path, http)
    with caplog.at_level("DEBUG"):
        n.notify(build_opened(_view()))
    assert TOKEN not in caplog.text
    status = json.dumps(n.status(), ensure_ascii=False)
    assert TOKEN not in status and CHAT not in status
    raw = (tmp_path / "notify_outbox.json").read_text(encoding="utf-8")
    assert TOKEN not in raw and CHAT not in raw
    assert redact(f"boom {TOKEN}") == "boom ***"


def test_opened_message_contract(tmp_path):
    http = _FakeHttp()
    n = _notifier(tmp_path, http)
    assert n.notify(build_opened(_view(), max_loss_at_stop="0.52", reason="4h kırılım"))
    text = http.calls[0][1]["text"]
    for want in ("PAPER İŞLEM AÇILDI", "Coin: ZRO/USDT", "Piyasa: FUTURES", "Yön: LONG",
                 "Kaldıraç: 3x", "Pozisyon değeri: 15.00 USDT", "Kullanılan teminat: 5.00 USDT",
                 "Coin adedi: 16.48 ZRO", "Giriş fiyatı: 0.91", "Stop-loss: 0.88",
                 "Take-profit: 0.97", "Stopta tahmini maksimum zarar: 0.52", "İşlem ID: F00008"):
        assert want in text, want
    assert text.index("PAPER") < 30, "PAPER etiketi mesajın BAŞINDA olmalı"


@pytest.mark.parametrize("reason,label,icon", [("STOP", "STOP-LOSS", "🛑"),
                                               ("TAKE_PROFIT", "TAKE PROFIT", "🎯"),
                                               ("LIQUIDATION", "LİKİDASYON", "💥")])
def test_closed_message_reason_labels(tmp_path, reason, label, icon):
    trade = {"id": "F00008", "symbol": "ZRO/USDT", "side": "LONG", "qty": "16.48",
             "entry_avg": "0.91", "exit_avg": "0.97", "leverage": 3, "exit_reason": reason,
             "opened_at": "2026-08-22T10:00:00+00:00", "closed_at": "2026-08-22T14:18:00+00:00",
             "market_type": "USDM_PERP"}
    ev = build_closed(trade, net_pnl="0.96", gross_pnl="1.00", fees="0.03", funding="-0.01",
                      margin="5.00")
    assert label in ev.text and "PAPER İŞLEM KAPANDI" in ev.title
    assert (icon in ev.title) or ("✅" in ev.title)
    assert "Net K/Z: +0.96 USDT" in ev.text and "Teminat getirisi: +19.20%" in ev.text
    assert "Süre: 4 saat 18 dakika" in ev.text


def test_losing_close_uses_red_icon_and_negative_sign(tmp_path):
    ev = build_closed({"id": "F1", "symbol": "X/USDT", "side": "LONG", "exit_reason": "STOP"},
                      net_pnl="-0.42", margin="5.00")
    assert ev.title.startswith("🔴") and "Net K/Z: -0.42 USDT" in ev.text
    assert ev.level == "warning"


def test_duplicate_event_is_sent_only_once(tmp_path):
    http = _FakeHttp()
    n = _notifier(tmp_path, http)
    ev = build_opened(_view())
    assert n.notify(ev) is True
    assert n.notify(ev) is False and n.notify(ev) is False
    assert len(http.calls) == 1


def test_event_id_separates_lifecycle_and_fill(tmp_path):
    a = event_id(EVENT_OPENED, "F1", "fill-1")
    b = event_id(EVENT_CLOSED, "F1", "fill-1")
    c = event_id(EVENT_CLOSED, "F1", "fill-2")
    d = event_id(EVENT_CLOSED, "F2", "fill-1")
    assert len({a, b, c, d}) == 4


def test_restart_does_not_resend_open_notifications(tmp_path):
    """Worker yeniden başladığında MEVCUT açık pozisyonlar "yeni işlem" diye bildirilmez."""
    http = _FakeHttp()
    open_pos = [{"id": "F00001", "symbol": "BZ/USDT", "opened_at": "2026-08-01T00:00:00+00:00"},
                {"id": "F00002", "symbol": "XAUT/USDT", "opened_at": "2026-08-02T00:00:00+00:00"}]
    n = _notifier(tmp_path, http)
    assert n.bootstrap_open_positions(open_pos) == 2
    assert http.calls == []
    v = position_view({"id": "F00001", "symbol": "BZ/USDT", "side": "LONG", "qty": "1",
                       "entry_avg": "1", "opened_at": "2026-08-01T00:00:00+00:00"}, mark_price="1")
    assert n.notify(build_opened(v)) is False, "bastırılmış açılış tekrar gönderilmez"
    # ... fakat KAPANIŞ bildirimi normal biçimde gider.
    assert n.notify(build_closed({"id": "F00001", "symbol": "BZ/USDT", "exit_reason": "TP",
                                  "closed_at": "2026-08-22T00:00:00+00:00"}, net_pnl="1.0")) is True
    assert len(http.calls) == 1
    # Yeni süreç aynı outbox'ı okur → yine göndermez (kalıcı idempotency).
    n2 = _notifier(tmp_path, http)
    assert n2.bootstrap_open_positions(open_pos) == 0
    assert n2.notify(build_opened(v)) is False
    assert len(http.calls) == 1


def test_transport_timeout_does_not_break_the_caller(tmp_path):
    http = _FakeHttp(boom=TimeoutError("timed out"))
    n = _notifier(tmp_path, http)
    assert n.notify(build_opened(_view())) is False, "hata yutulur, istisna DIŞARI SIZMAZ"
    assert n.outbox.status_of(build_opened(_view()).id) == "failed"


def test_retry_is_bounded(tmp_path):
    http = _FakeHttp(status=500)
    n = _notifier(tmp_path, http)
    ev = build_opened(_view())
    for _ in range(6):
        n.notify(ev)
    assert len(http.calls) <= _Cfg.max_retries, "sonsuz retry YOK"


def test_outbox_is_atomic_and_survives_reload(tmp_path):
    ob = NotifyOutbox(tmp_path / "ob.json", keep=10, max_attempts=2)
    ob.enqueue("e1", "k")
    ob.mark_sent("e1")
    ob.save()
    again = NotifyOutbox(tmp_path / "ob.json", keep=10, max_attempts=2)
    assert again.delivered("e1") and again.counts().get("sent") == 1
    assert json.loads((tmp_path / "ob.json").read_text(encoding="utf-8"))["schema"] == "notify_outbox_v1"


def test_daily_summary_contains_required_fields(tmp_path):
    pv = portfolio_view([], [{"realized_pnl": "2.5", "closed_at": "2026-08-22T01:00:00+00:00"}],
                        today="2026-08-22")
    ev = build_daily_summary(pv, day="2026-08-22", opened=3, closed=1, health="HEALTHY")
    for want in ("PAPER GÜNLÜK ÖZET", "Tarih: 2026-08-22 (UTC)", "Mod: PAPER", "Açılan işlem: 3",
                 "Kapanan işlem: 1", "Kazanan / Kaybeden: 1 / 0", "Gerçekleşen net K/Z: +2.50",
                 "Açık pozisyon: 0", "Sağlık: HEALTHY"):
        assert want in (ev.title + ev.text), want


def test_transport_not_configured_makes_no_call():
    t = TelegramTransport("", "", http=_FakeHttp())
    assert t.configured is False
    ok, info = t.send(build_opened(_view()))
    assert ok is False and info == "NOT_CONFIGURED"


# ===================================================================== 5) contract parity
def test_dashboard_and_telegram_report_identical_numbers(tmp_path):
    """AYNI işlem için panel ve Telegram AYNI kanonik değerleri kullanır."""
    from tradingbot.dashboard.views import build
    pos = {"id": "F00008", "symbol": "ZRO/USDT", "side": "LONG", "qty": "16.48",
           "entry_avg": "0.91", "leverage": 3, "isolated_margin": "5.00", "notional": "15.00",
           "stop": "0.88", "targets": ["0.97"], "entry_fee": "0.0075", "last_price": "0.97",
           "opened_at": "2026-08-22T10:00:00+00:00", "market_type": "USDM_PERP"}
    vm = build([pos], [], None, marks={"ZRO/USDT": "0.97"}, fees={"taker_pct": "0.05"})
    dash = vm["portfolio"].positions[0]
    tele = position_view(pos, mark_price="0.97", fees={"taker_pct": "0.05"})
    assert dash.net_unrealized == tele.net_unrealized
    assert dash.gross_unrealized == tele.gross_unrealized
    assert dash.initial_margin == tele.initial_margin == D("5.00")
    assert dash.leverage == tele.leverage == 3
    assert dash.qty == tele.qty == D("16.48")
    ev = build_opened(tele)
    assert f"Coin adedi: {vm['rows'][0][3]}" in ev.text, "panel satırı ile mesaj aynı adedi göstermeli"
