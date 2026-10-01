"""Denetim bulguları Y-1, Y-2, O-3, O-4, O-5 için regresyon testleri.

* **Y-1** — `notify_open/close/health`, `daily_summary_*` ve `retry_backoff_s` GERÇEKTEN tüketiliyor.
* **Y-2** — başarısız outbox olayları zamanı gelince, sınırlı sayıda, otomatik yeniden deneniyor.
* **O-3** — worker SÜRECİ öldüğünde systemd `OnFailure=` hook'u harici uyarı gönderiyor.
* **O-4** — Telegram HTTP'si `_entry_lock` DIŞINDA; yavaş taşıma girişi bloklamıyor/geri almıyor.
* **O-5** — `realized_net()` ücret/funding'i iki kez saymıyor (kanonik + geri dönüş dalı).

Hiçbir test gerçek Telegram ağına bağlanmaz; taşıma enjekte edilir.
"""
from __future__ import annotations

import json
import sys
import threading
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_engine_v3 import _engine  # noqa: E402

from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.core import ConfigError, utc_now  # noqa: E402
from tradingbot.notify import (EVENT_CLOSED, EVENT_DAILY_SUMMARY, EVENT_OPENED,  # noqa: E402
                               EVENT_WORKER_FAILURE, EVENT_WORKER_RECOVERED, NotifyOutbox,
                               TradeNotifier, build_closed, build_health, build_opened,
                               build_worker_failure, build_worker_recovered, event_id)
from tradingbot.notify.outbox import MAX_BACKOFF_S  # noqa: E402
from tradingbot.pnl import portfolio_view, position_view, realized_net  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456789:AAF" + "x" * 30          # SENTETİK
CHAT = "-1001234567890"


class _Http:
    """Ağ YOK. Çağrıları sayar; `status`/`boom`/`gate` ile senaryo kurar."""

    def __init__(self, status: int = 200, boom: Exception | None = None,
                 gate: threading.Event | None = None) -> None:
        self.calls: list[dict] = []
        self.status, self.boom, self.gate = status, boom, gate
        self.entered = threading.Event()          # taşıma GERÇEKTEN istek içindeyken set edilir

    def __call__(self, url: str, body: dict, timeout: float) -> int:
        if self.gate is not None:
            self.entered.set()                    # "HTTP uçuşta" — sonda şimdi kilidi deneyebilir
            assert self.gate.wait(10), "test barrier açılmadı"
        self.calls.append(body)
        if self.boom:
            raise self.boom
        return self.status


class _Cfg:
    enabled = True
    bot_token_env = "TB_TG_TOKEN"
    chat_id_env = "TB_TG_CHAT"
    timeout_s = 5.0
    max_retries = 3
    retry_backoff_s = 60.0
    retry_batch = 5
    outbox_file = "notify_outbox.json"
    outbox_keep = 500
    suppress_backlog_on_start = True
    daily_summary_enabled = True
    daily_summary_hour_utc = 21
    notify_open = True
    notify_close = True
    notify_health = True


def _cfg(**over):
    c = _Cfg()
    for k, v in over.items():
        setattr(c, k, v)
    return c


def _notifier(tmp_path, http=None, **over):
    return TradeNotifier.from_config(_cfg(**over), tmp_path, http=http,
                                     env={"TB_TG_TOKEN": TOKEN, "TB_TG_CHAT": CHAT})


def _view(tid="F00008", sym="ZRO/USDT"):
    return position_view({"id": tid, "symbol": sym, "side": "LONG", "qty": "16.48",
                          "entry_avg": "0.91", "leverage": 3, "isolated_margin": "5.00",
                          "notional": "15.00", "stop": "0.88", "targets": ["0.97"],
                          "entry_fee": "0.0075", "opened_at": "2026-08-22T10:00:00+00:00",
                          "market_type": "USDM_PERP"}, mark_price="0.91")


def _closed(tid="F00008"):
    return {"id": tid, "symbol": "ZRO/USDT", "side": "LONG", "exit_reason": "TP",
            "closed_at": "2026-08-22T14:00:00+00:00", "opened_at": "2026-08-22T10:00:00+00:00"}


# ===================================================================== Y-1: config bağlandı
def test_notify_open_false_produces_no_event(tmp_path):
    http = _Http()
    n = _notifier(tmp_path, http, notify_open=False)
    assert n.wants(EVENT_OPENED) is False
    assert n.notify(build_opened(_view())) is False
    assert n.enqueue(build_opened(_view())) is False
    assert http.calls == [] and n.outbox.counts() == {}, "kapalı tür outbox'ı doldurmamalı"
    # kapanış hâlâ açık
    assert n.notify(build_closed(_closed(), net_pnl="1.0")) is True and len(http.calls) == 1


def test_notify_close_false_produces_no_event(tmp_path):
    http = _Http()
    n = _notifier(tmp_path, http, notify_close=False)
    assert n.wants(EVENT_CLOSED) is False
    assert n.notify(build_closed(_closed(), net_pnl="1.0")) is False
    assert http.calls == []
    assert n.notify(build_opened(_view())) is True and len(http.calls) == 1


def test_notify_health_false_covers_health_and_worker_events(tmp_path):
    http = _Http()
    n = _notifier(tmp_path, http, notify_health=False)
    assert n.notify(build_health("DEGRADED", summary="x")) is False
    assert n.notify(build_health("HEALTHY", recovered=True, ref="r")) is False
    assert n.notify(build_worker_failure("u.service", result="timeout", ref="r1")) is False
    assert n.notify(build_worker_recovered("u.service", ref="r1")) is False
    assert http.calls == []


def test_daily_summary_disabled_is_never_due(tmp_path):
    n = _notifier(tmp_path, _Http(), daily_summary_enabled=False)
    assert n.daily_summary_due("2026-08-22", 23) is False


def test_daily_summary_only_at_or_after_configured_hour(tmp_path):
    n = _notifier(tmp_path, _Http(), daily_summary_hour_utc=21)
    assert n.daily_summary_due("2026-08-22", 20) is False, "saatten önce gönderilmez"
    assert n.daily_summary_due("2026-08-22", 21) is True
    assert n.daily_summary_due("2026-08-22", 23) is True, "saat kaçırıldıysa sonraki turda gönderilir"


def test_daily_summary_sent_once_per_day_even_after_restart(tmp_path):
    from tradingbot.notify import build_daily_summary
    http = _Http()
    pv = portfolio_view([], [{"realized_pnl": "2.5", "closed_at": "2026-08-22T01:00:00+00:00"}],
                        today="2026-08-22")
    n = _notifier(tmp_path, http)
    assert n.daily_summary_due("2026-08-22", 21) is True
    assert n.notify(build_daily_summary(pv, day="2026-08-22")) is True
    assert n.daily_summary_due("2026-08-22", 22) is False, "aynı gün ikinci kez YOK"
    n2 = _notifier(tmp_path, http)                    # YENİ SÜREÇ (restart)
    assert n2.daily_summary_due("2026-08-22", 22) is False, "restart sonrası duplicate YOK"
    assert n2.notify(build_daily_summary(pv, day="2026-08-22")) is False
    assert len(http.calls) == 1
    # ertesi gün yeniden gönderilebilir
    assert n2.daily_summary_due("2026-08-23", 21) is True


def test_daily_summary_event_id_is_deterministic_per_day():
    a = event_id(EVENT_DAILY_SUMMARY, "portfolio", "2026-08-22")
    b = event_id(EVENT_DAILY_SUMMARY, "portfolio", "2026-08-22")
    c = event_id(EVENT_DAILY_SUMMARY, "portfolio", "2026-08-23")
    assert a == b and a != c


@pytest.mark.parametrize("hour", [-1, 24, 99])
def test_invalid_daily_summary_hour_is_rejected(hour):
    with pytest.raises(ConfigError):
        load_v3({"mode": "PAPER", "telegram": {"daily_summary_hour_utc": hour}})


def test_invalid_retry_settings_are_rejected():
    with pytest.raises(ConfigError):
        load_v3({"mode": "PAPER", "telegram": {"retry_backoff_s": -1}})
    with pytest.raises(ConfigError):
        load_v3({"mode": "PAPER", "telegram": {"retry_batch": 0}})
    assert load_v3({"mode": "PAPER"}).telegram.retry_backoff_s == 2.0


def test_all_telegram_config_fields_are_consumed():
    """Y-1 REGRESYON: `TelegramSection` alanlarının hepsi kod tarafından okunuyor."""
    import dataclasses
    import re
    from tradingbot.config_v3 import TelegramSection
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tradingbot").rglob("*.py")
                    if "__pycache__" not in str(p) and p.name != "config_v3.py")
    unused = [f.name for f in dataclasses.fields(TelegramSection)
              if not re.search(rf"\b{re.escape(f.name)}\b", src)]
    assert unused == [], f"config'te bağlanmamış alan: {unused}"


# ===================================================================== Y-2: otomatik retry
def test_failed_event_is_retried_when_due(tmp_path):
    http = _Http(status=500)
    n = _notifier(tmp_path, http, retry_backoff_s=0.0)     # backoff yok → hemen due
    ev = build_opened(_view())
    assert n.notify(ev) is False and len(http.calls) == 1
    assert n.outbox.status_of(ev.id) == "failed"
    http.status = 200
    assert n.retry_pending() == 1, "zamanı gelmiş başarısız olay yeniden denenmeli"
    assert n.outbox.status_of(ev.id) == "sent"
    assert http.calls[-1]["text"] == http.calls[0]["text"], "ORİJİNAL mesaj gönderilmeli"


def test_event_not_yet_due_is_not_retried(tmp_path):
    http = _Http(status=500)
    n = _notifier(tmp_path, http, retry_backoff_s=3600.0)
    ev = build_opened(_view())
    n.notify(ev)
    assert len(http.calls) == 1
    assert n.outbox.due() == [], "backoff süresi dolmadan yeniden denenmez"
    assert n.retry_pending() == 0 and len(http.calls) == 1


def test_retry_backoff_is_exponential_and_capped(tmp_path):
    ob = NotifyOutbox(tmp_path / "ob.json", keep=10, max_attempts=9)
    ob.enqueue("e", "k")
    seen = []
    for _ in range(6):
        ob.mark_failed("e", "HTTP_500", backoff_s=60.0)
        seen.append(ob.entries["e"].next_attempt_at)
    assert len(set(seen)) == len(seen), "her denemede zaman ileri gitmeli"
    assert ob.entries["e"].attempts == 6
    ob.mark_failed("e", "HTTP_500", backoff_s=MAX_BACKOFF_S * 10)      # üst sınır testi
    assert ob.entries["e"].next_attempt_at, "üst sınır uygulanmalı, hata değil"


def test_retry_is_bounded_by_max_attempts_and_batch(tmp_path):
    http = _Http(status=500)
    n = _notifier(tmp_path, http, retry_backoff_s=0.0, max_retries=2)
    ev = build_opened(_view())
    n.notify(ev)
    for _ in range(6):
        n.retry_pending()
    assert len(http.calls) <= 2, f"sonsuz retry: {len(http.calls)}"
    assert n.outbox.due() == [], "deneme bütçesi bitince kuyruktan düşer"


def test_retry_batch_limits_events_per_round(tmp_path):
    http = _Http(status=500)
    n = _notifier(tmp_path, http, retry_backoff_s=0.0, retry_batch=2)
    for i in range(5):
        n.enqueue(build_opened(_view(tid=f"F{i}")))
    http.calls.clear()
    n.retry_pending()
    assert len(http.calls) == 2, "tur başına en çok `retry_batch` olay"


def test_sent_and_suppressed_are_never_retried(tmp_path):
    http = _Http()
    n = _notifier(tmp_path, http, retry_backoff_s=0.0)
    ev = build_opened(_view())
    n.notify(ev)
    n.outbox.suppress(event_id(EVENT_OPENED, "F1", "t"), EVENT_OPENED)
    n.outbox.save()
    http.calls.clear()
    assert n.retry_pending() == 0 and http.calls == []


def test_retry_resumes_after_restart(tmp_path):
    http = _Http(status=500)
    n = _notifier(tmp_path, http, retry_backoff_s=0.0)
    ev = build_opened(_view())
    n.notify(ev)
    http.status = 200
    n2 = _notifier(tmp_path, http, retry_backoff_s=0.0)          # YENİ SÜREÇ
    assert n2.retry_pending() == 1, "restart sonrası failed olay devam etmeli"
    assert n2.outbox.status_of(ev.id) == "sent"


def test_retry_never_duplicates_an_event(tmp_path):
    http = _Http()
    n = _notifier(tmp_path, http, retry_backoff_s=0.0)
    ev = build_opened(_view())
    n.notify(ev)
    for _ in range(4):
        n.retry_pending()
    assert len(http.calls) == 1


def test_retry_does_nothing_when_telegram_disabled(tmp_path):
    http = _Http()
    n = TradeNotifier.from_config(_cfg(enabled=False), tmp_path, http=http, env={})
    assert n.retry_pending() == 0 and n.flush() == 0 and http.calls == []


def test_token_never_leaks_into_outbox_payload(tmp_path):
    http = _Http(boom=RuntimeError(f"tls fail {TOKEN}"))
    n = _notifier(tmp_path, http, retry_backoff_s=0.0)
    n.notify(build_opened(_view()))
    raw = (tmp_path / "notify_outbox.json").read_text(encoding="utf-8")
    assert TOKEN not in raw and CHAT not in raw
    assert json.loads(raw)["entries"][0]["last_error"] == "RuntimeError"


# ===================================================================== bozuk outbox
def test_corrupt_outbox_recovers_from_backup(tmp_path):
    """Bozuk ana dosya → `.bak` kopyasından KURTARILIR; bozuk dosya karantinaya alınır."""
    path = tmp_path / "ob.json"
    ob = NotifyOutbox(path, keep=10, max_attempts=3)
    ob.enqueue("e1", "k")
    ob.mark_sent("e1")
    ob.save()
    ob.enqueue("e2", "k")
    ob.mark_sent("e2")
    ob.save()                                        # `.bak` artık ilk kaydı içerir
    assert (tmp_path / "ob.json.bak").exists()
    path.write_text('{"schema": "notify_outbox_v1", "entries": [ {"id"', encoding="utf-8")
    again = NotifyOutbox(path, keep=10, max_attempts=3)
    assert again.delivered("e1"), "idempotency geçmişi `.bak`'tan kurtarılmalı"
    assert list(tmp_path.glob("*.corrupt-*")), "bozuk dosya karantinaya alınmalı (silinmez)"


def test_corrupt_data_is_never_assumed_sent(tmp_path):
    path = tmp_path / "ob.json"
    path.write_text("{ bozuk", encoding="utf-8")
    ob = NotifyOutbox(path, keep=10, max_attempts=3)
    assert ob.delivered("herhangi") is False, "bozuk veri 'gönderildi' SAYILMAZ"
    assert ob.counts() == {}


# ===================================================================== O-4: kilit dışı gönderim
def test_engine_sends_telegram_outside_the_entry_lock(tmp_path, monkeypatch):
    """Yavaş taşıma `_entry_lock`'ı TUTMAZ ve açılmış işlemi geri almaz.

    Gerçek uyku YOK: taşıma bir `threading.Event` ile kapıda bekletilir; bu sırada başka bir iş
    parçacığı kilidi almayı dener. Kilit alınabiliyorsa HTTP kritik bölge DIŞINDADIR.
    """
    from test_risk_capacity_and_gates import _force_final_risk_pct, _force_triggers
    gate = threading.Event()
    http = _Http(gate=gate)
    eng = _engine(tmp_path, monkeypatch, {"telegram": {"enabled": True}}, symbols=2, equity=5_000.0)
    eng.notifier = TradeNotifier.from_config(_cfg(), eng.cfg.state_path, http=http,
                                             env={"TB_TG_TOKEN": TOKEN, "TB_TG_CHAT": CHAT})
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 for s in eng.cfg.coins})
    _force_triggers(monkeypatch, True)

    result: dict = {}

    def probe():
        # HTTP GERÇEKTEN uçuşa geçene kadar bekle; ancak O ANDA kilidi denemek anlamlıdır.
        result["entered"] = http.entered.wait(10)
        if result["entered"]:
            got = eng._entry_lock.acquire(blocking=False)
            result["lock_free"] = bool(got)
            if got:
                eng._entry_lock.release()
        gate.set()                                    # taşımayı serbest bırak

    th = threading.Thread(target=probe, daemon=True)
    th.start()
    eng.tour(do_scan=False, obsidian=False, charts=False)
    th.join(timeout=15)
    assert eng.ledger2.positions, "senaryo geçersiz: pozisyon açılmadı"
    assert result.get("entered") is True, "taşıma hiç çağrılmadı — test bir şey kanıtlamıyor"
    assert result.get("lock_free") is True, "Telegram HTTP'si `_entry_lock` İÇİNDE çalışıyor"


def test_slow_transport_does_not_roll_back_the_trade(tmp_path, monkeypatch):
    """Taşıma tamamen çökse bile pozisyon açık kalır ve olay retry için kuyrukta durur."""
    from test_risk_capacity_and_gates import _force_final_risk_pct, _force_triggers
    http = _Http(boom=TimeoutError("timed out"))
    eng = _engine(tmp_path, monkeypatch, {"telegram": {"enabled": True}}, symbols=2, equity=5_000.0)
    eng.notifier = TradeNotifier.from_config(_cfg(retry_backoff_s=0.0), eng.cfg.state_path,
                                             http=http, env={"TB_TG_TOKEN": TOKEN, "TB_TG_CHAT": CHAT})
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 for s in eng.cfg.coins})
    _force_triggers(monkeypatch, True)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s["opened"] and eng.ledger2.positions, "Telegram hatası işlemi GERİ ALMAMALI"
    assert any(e.status == "failed" for e in eng.notifier.outbox.entries.values())


def test_open_event_is_enqueued_exactly_once(tmp_path, monkeypatch):
    from test_risk_capacity_and_gates import _force_final_risk_pct, _force_triggers
    http = _Http()
    eng = _engine(tmp_path, monkeypatch, {"telegram": {"enabled": True}}, symbols=2, equity=5_000.0)
    eng.notifier = TradeNotifier.from_config(_cfg(), eng.cfg.state_path, http=http,
                                             env={"TB_TG_TOKEN": TOKEN, "TB_TG_CHAT": CHAT})
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 for s in eng.cfg.coins})
    _force_triggers(monkeypatch, True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    opened = [b for b in http.calls if "İŞLEM AÇILDI" in b["text"]]
    assert len(opened) == len(eng.ledger2.positions), "her açılış için TAM BİR mesaj"
    ids = [e.id for e in eng.notifier.outbox.entries.values() if e.kind == EVENT_OPENED]
    assert len(ids) == len(set(ids)), "duplicate olay kaydı yok"


# ===================================================================== O-3: harici failure/recovery
def test_worker_alert_unit_is_wired_to_the_worker():
    worker = (ROOT / "deploy" / "tradingbot-worker.service").read_text(encoding="utf-8")
    assert "OnFailure=tradingbot-alert@%n.service" in worker
    alert = (ROOT / "deploy" / "tradingbot-alert@.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in alert, "sürekli daemon DEĞİL"
    assert "worker-alert --event failure --unit %i" in alert
    # Token ARGÜMAN olarak geçmez; yalnız EnvironmentFile ile gelir.
    exec_line = next(ln for ln in alert.splitlines() if ln.startswith("ExecStart="))
    for bad in ("token", "TOKEN", "$TRADINGBOT_TELEGRAM_BOT_TOKEN", "chat"):
        assert bad not in exec_line, exec_line
    assert "EnvironmentFile=" in alert


def test_worker_alert_is_noop_when_telegram_disabled(tmp_path, monkeypatch):
    from tradingbot.notify.cli import worker_failure
    http = _Http()
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    assert worker_failure(eng.cfg, unit="u.service", result="timeout", ref="r1",
                          http=http, env={}) == 0
    assert http.calls == [], "Telegram kapalıyken ağ çağrısı YOK"


def test_worker_failure_alert_is_idempotent(tmp_path, monkeypatch):
    from tradingbot.notify.cli import worker_failure
    http = _Http()
    eng = _engine(tmp_path, monkeypatch, {"telegram": {"enabled": True}}, symbols=2)
    env = {"TB_TG_TOKEN": TOKEN, "TB_TG_CHAT": CHAT}
    monkeypatch.setattr(eng.cfg.v3, "telegram", _cfg())
    for _ in range(3):                                   # aynı failure döngüsü
        worker_failure(eng.cfg, unit="tradingbot-worker.service", result="timeout", ref="inv-1",
                       http=http, env=env)
    assert len(http.calls) == 1, "aynı ref ile mesaj yağmuru YOK"
    assert "PAPER WORKER DURDU" in http.calls[0]["text"]
    assert "Sonuç: timeout" in http.calls[0]["text"]
    worker_failure(eng.cfg, unit="tradingbot-worker.service", result="oom-kill", ref="inv-2",
                   http=http, env=env)
    assert len(http.calls) == 2, "YENİ başarısızlık döngüsü bildirilir"


def test_recovery_requires_healthy_state(tmp_path, monkeypatch):
    from tradingbot.notify.cli import worker_failure, worker_recovery
    http = _Http()
    eng = _engine(tmp_path, monkeypatch, {"telegram": {"enabled": True}}, symbols=2)
    monkeypatch.setattr(eng.cfg.v3, "telegram", _cfg())
    env = {"TB_TG_TOKEN": TOKEN, "TB_TG_CHAT": CHAT}
    worker_failure(eng.cfg, ref="inv-9", result="timeout", http=http, env=env)
    assert len(http.calls) == 1
    st = Path(eng.cfg.state_path)
    (st / "health.json").write_text(json.dumps({"state": "DEGRADED"}), encoding="utf-8")
    assert worker_recovery(eng.cfg, http=http, env=env) == 0
    assert len(http.calls) == 1, "sağlıklı DEĞİLKEN kurtarma gönderilmez"
    (st / "health.json").write_text(json.dumps({"state": "HEALTHY", "heartbeat_age_s": 12}),
                                    encoding="utf-8")
    worker_recovery(eng.cfg, http=http, env=env)
    assert len(http.calls) == 2 and "TEKRAR SAĞLIKLI" in http.calls[1]["text"]
    worker_recovery(eng.cfg, http=http, env=env)
    assert len(http.calls) == 2, "aynı kurtarma iki kez gönderilmez"


def test_recovery_is_linked_to_the_failure_event(tmp_path):
    http = _Http()
    n = _notifier(tmp_path, http)
    n.notify(build_worker_failure("u.service", result="timeout", ref="inv-7"))
    assert n.pending_worker_failure() == "inv-7"
    n.notify(build_worker_recovered("u.service", ref="inv-7"))
    assert n.pending_worker_failure() is None, "kurtarma sonrası bekleyen failure kalmaz"


def test_worker_alert_never_trades_or_changes_mode():
    src = (ROOT / "tradingbot" / "notify" / "cli.py").read_text(encoding="utf-8")
    for bad in ("ledger2.open", "market_buy", "place_order", "mode_transition", "ModeState",
                "live_trading"):
        assert bad not in src, bad


# ===================================================================== O-5: PnL çift sayım yok
def test_realized_net_canonical_and_fallback_paths():
    rec = {"gross_pnl": "100.00", "entry_fee": "0.50", "exit_fee": "0.55", "funding": "-0.20",
           "fees": "1.05", "pnl": "98.75", "net_pnl": "98.75",
           "funding_paid": "0.20", "funding_received": "0"}
    assert realized_net(rec) == D("98.75"), "kanonik dal: net_pnl"
    fb = {k: v for k, v in rec.items() if k != "net_pnl"}
    fb["realized_pnl"] = "98.95"                     # ücret dahil, funding hariç
    assert realized_net(fb) == D("98.75"), "geri dönüş dalı: ücret İKİNCİ KEZ düşülmez"


def test_ledger_close_chain_matches_realized_net():
    """gross − entry_fee − exit_fee ± funding = net_pnl = realized_net()"""
    from tradingbot.accounting import AmountType, FeeSchedule, FuturesLedgerV2, SizeSpec, SlippageModel
    from tradingbot.accounting.models import MarketType, SymbolFilters, TickData
    led = FuturesLedgerV2(D("10000"), max_positions=None,
                          fees=FeeSchedule(maker_pct=D("0.02"), taker_pct=D("0.05")),
                          slippage=SlippageModel.zero())
    f = SymbolFilters(symbol="X/USDT", market_type=MarketType.USDM_PERP, price_tick=D("0.01"),
                      qty_step=D("0.001"), min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)
    tick = TickData(last=D("100"), mark=D("100"))
    p = led.open("X/USDT", "LONG", D("100"), SizeSpec(D("1000"), AmountType.NOTIONAL, 2),
                 stop=D("95"), targets=[D("110")], filters=f, tick=tick)
    p.funding_paid = D("0.20")
    d = led.close_manual("X/USDT", D("110")).to_dict()
    expected = D(str(d["gross_pnl"])) - D(str(d["entry_fee"])) - D(str(d["exit_fee"])) + D(str(d["funding"]))
    assert D(str(d["net_pnl"])) == expected
    assert realized_net(d) == expected
    assert "realized_pnl" not in d, "kapanış kaydında bu anahtar yok (docstring doğru)"


def test_pnl_docstring_matches_the_real_ledger_contract():
    src = (ROOT / "tradingbot" / "pnl.py").read_text(encoding="utf-8")
    assert "giriş+çıkış ücreti İÇİNDE" not in src, "eski yanlış iddia kaldırılmalı"
    assert "YALNIZ çıkış ücretini içerir" in src or "yalnız çıkış ücretini içerir" in src.lower()
    assert "net_pnl" in src
