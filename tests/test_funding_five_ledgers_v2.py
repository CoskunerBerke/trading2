# -*- coding: utf-8 -*-
"""FUNDING KAYNAĞI BEŞ DEFTERDE (2026-09-22) — ana bot, T2, M2, Box ve formasyon defteri AYNI gerçekleşmiş kaynağı
(settlement oranı + satırın kendi mark'ı) kullanır; ağ yalnız tur/tarayıcı adımında, 60 sn çıkış izleyicisi ağa
çıkmaz; spot defteri futures funding'ine dahil edilmez.

Eskiden (REVIEW-2026-09-22 §8): ana bot ve T2/M2 turda `static_rates(funding_pct)` ile ANLIK oranı her geçmiş
settlement'a gerçek oran gibi uyguluyordu (`estimated=False`); 60 sn izleyici son bilinen oranı tahmin ediyordu.
Sağlayıcı sentetiktir (etiketli); motor, defterler ve kaynak GERÇEK sınıflardır.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _profile  # noqa: E402

from tradingbot.accounting import SizeSpec, TickData  # noqa: E402
from tradingbot.accounting.models import AmountType  # noqa: E402
from tradingbot.core import FUNDING_HOURS_UTC, funding_settlements_between, utc_now  # noqa: E402
from tradingbot.pattern_trader.funding import FundingRates  # noqa: E402

BOX_PARAMS = {"near_frac": 0.10, "trigger": "break_prev", "long_stop": "day_low", "exit_kind": "box_mid", "exit_r": 2.0,
              "min_stop_pct": 2.22, "eod_close": True, "allow_long": True, "allow_short": True, "leverage": 3}


class SynthFundingProvider:
    """SENTETİK `/fapi/v1/fundingRate` + `fundingInfo` (varsayılan 8 saat). Çağrılar sembolüyle kaydedilir."""

    def __init__(self, rate: float = 0.0001, mark: float = 100.0):
        self.rate, self.mark, self.calls = rate, mark, []

    def funding_history(self, symbol, limit=1000, start_ms=None, end_ms=None):
        self.calls.append(("history", symbol))
        now = utc_now()
        rows = []
        for t in funding_settlements_between(now - timedelta(days=3), now, FUNDING_HOURS_UTC):
            ms = int(t.timestamp() * 1000)
            if (start_ms is None or ms >= start_ms) and (end_ms is None or ms <= end_ms):
                rows.append({"symbol": symbol.replace("/", ""), "funding_ts": ms, "rate": self.rate, "mark": self.mark})
        return rows

    def funding_info(self):
        self.calls.append(("info", None))
        return []


def _ov():
    return _profile(6.0) | {"futures_v3": {"realized_funding_source": True},
                            "strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                               "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"},
                                                         {"name": "b1_box_fade", "state_dir": "strategy_paper_box",
                                                          "rule_params": dict(BOX_PARAMS)}]},
                            "pattern_trader": {"enabled": True, "scan_seconds": 3600.0, "universe_refresh_minutes": 0.0},
                            "chart_analysis": {"enabled": False}, "news": {"enabled": False}}


def test_one_realized_source_is_bound_to_all_five_futures_ledgers(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=2, equity=EQUITY)
    prov = SynthFundingProvider()
    monkeypatch.setattr(eng, "_gap_provider_factory", lambda: prov)
    monkeypatch.setattr("tradingbot.pattern_trader.scheduler.PatternScanner.start", lambda self: None)
    fr = eng.funding_rates
    assert isinstance(fr, FundingRates)
    books = {b.name: b for b in eng.strategy_books}
    assert set(books) == {"t2_trend_regime", "m2_tsmom28", "b1_box_fade"}
    ledgers = {"main": eng.ledger2} | {k: b.ledger for k, b in books.items()}
    for name, led in ledgers.items():
        assert led.funding.hours_for_symbol == fr.hours_for, name          # sözleşmenin kendi aralığı
        assert led.funding.fallback_to_last_known is False, name           # bilinmeyen oran TAHMİN edilmez
    for b in books.values():
        assert b.funding_rates is fr
    eng.ensure_pattern_scanner()
    assert eng.pattern_scanner.funding is fr and eng.pattern_book.funding_rates is fr
    assert eng.pattern_book.ledger.funding.hours_for_symbol == fr.hours_for
    assert prov.calls == [], "kurulum ağa çıkmaz"


class ArmedProvider:
    """SENTETİK. Bağımsız doğrulayıcının sondası (2026-09-22): `armed` iken funding ağına dokunulursa TEST DÜŞER.
    Yalnız ETH için satır verir (BTC satırı yok → çıkış yolunda arama ıskalar ve bekler, ağa çıkmaz)."""

    def __init__(self):
        self.calls, self.armed = [], False

    def _hit(self, what):
        self.calls.append(what)
        if self.armed:
            raise AssertionError("funding network touched on the exit path: %s" % (what,))

    def funding_history(self, symbol, limit=1000, start_ms=None, end_ms=None):
        self._hit(("history", symbol))
        if not symbol.startswith("ETH"):
            return []
        now = utc_now()
        return [{"symbol": symbol.replace("/", ""), "funding_ts": int(t.timestamp() * 1000), "rate": 0.0001, "mark": 100.0}
                for t in funding_settlements_between(now - timedelta(days=3), now, FUNDING_HOURS_UTC)
                if (start_ms is None or int(t.timestamp() * 1000) >= start_ms) and (end_ms is None or int(t.timestamp() * 1000) <= end_ms)]

    def funding_info(self):
        self._hit(("info", None))
        return []


def test_all_three_exit_monitors_stay_offline_but_tick_every_futures_ledger_with_the_source(tmp_path, monkeypatch):
    """Doğrulayıcının testi (kaynak: bağımsız doğrulama, 2026-09-22): çıkış izleyicileri (ana defter, strateji
    defterleri, formasyon tarayıcısı) funding AĞINA dokunmaz ama gerçekleşmiş kaynakla (bellek) tick'ler; ağ adımından
    önce de sonra da. Bekleyen (satırı olmayan) sembol ağa çıkmadan BEKLER."""
    from tradingbot.accounting import TickData as TD
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=2, equity=EQUITY)
    prov = ArmedProvider()
    monkeypatch.setattr(eng, "_gap_provider_factory", lambda: prov)
    monkeypatch.setattr("tradingbot.pattern_trader.scheduler.PatternScanner.start", lambda self: None)
    now = utc_now()
    opened = now - timedelta(hours=9)
    sym = "ETH/USDT"
    small = SizeSpec(Decimal("0.1"), AmountType.QUANTITY, 1)
    p_main = eng.ledger2.open(sym, "LONG", Decimal("100"), small, stop=Decimal("50"), now=opened)
    p_btc = eng.ledger2.open("BTC/USDT", "LONG", Decimal("100"), small, stop=Decimal("50"), now=opened)
    t2 = next(b for b in eng.strategy_books if b.name == "t2_trend_regime")
    p_t2 = t2.ledger.open(sym, "LONG", Decimal("100"), small, stop=Decimal("50"), now=opened)
    eng.ensure_pattern_scanner()
    p_pb = eng.pattern_book.ledger.open(sym, "LONG", Decimal("100"), small, stop=Decimal("50"), now=opened)
    assert p_main and p_btc and p_t2 and p_pb
    eng.ensure_gap_reconciled()
    fake = lambda syms, **_: ({s: TD(last=Decimal("90")) for s in syms}, {s: 90.0 for s in syms}, {})  # noqa: E731
    monkeypatch.setattr(eng, "_paper_marks", fake)
    monkeypatch.setattr(eng.pattern_scanner.price, "marks", fake)
    monkeypatch.setattr(eng.runner.live, "snapshot", lambda s: {"ticker": {"last": 90.0}})
    prov.armed, n0 = True, len(prov.calls)
    eng.exit_check()                                   # (1) ağ adımından ÖNCE: aralık tablosu yok → ağ yok, dönem bekler
    assert len(prov.calls) == n0, prov.calls[n0:]
    prov.armed = False
    eng._funding_step(now)                             # (2) tur ağ adımı + tarayıcı ağ adımı belleği doldurur
    eng.pattern_scanner.funding_step()
    prov.armed, n = True, len(prov.calls)
    eng.exit_check()
    assert len(prov.calls) == n, prov.calls[n:]
    due = funding_settlements_between(opened, now, FUNDING_HOURS_UTC)
    for name, p in (("main", p_main), ("t2", p_t2), ("pattern", p_pb)):
        rows = p.features.get("funding_settlements") or []
        assert p.last_price == Decimal("90"), (name, "çıkış izleyicisi bu defteri tick'lemedi")
        assert len(rows) == len(due), (name, rows, p.features.get("funding_pending"))
        assert all(r["mark_basis"] == "SETTLEMENT_ROW" and Decimal(r["mark"]) == Decimal("100") for r in rows), name
    assert p_btc.features.get("funding_pending", {}).get("reason") == "RATE_UNKNOWN"


def test_the_engine_tour_itself_refreshes_and_accrues_the_main_and_strategy_ledgers(tmp_path, monkeypatch):
    """Tur yolu (metin değil DAVRANIŞ): `tour()` → `_funding_step` (ağ) → ana defter + strateji defteri tick'i kaynakla.
    Kaynak bağlantısı kaldırılırsa ya da tur `_funding_step`i çağırmazsa settlement satırı OLUŞMAZ."""
    from tradingbot.accounting import TickData as TD
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=2, equity=EQUITY)
    prov = SynthFundingProvider(rate=0.0001, mark=100.0)
    monkeypatch.setattr(eng, "_gap_provider_factory", lambda: prov)
    monkeypatch.setattr("tradingbot.pattern_trader.scheduler.PatternScanner.start", lambda self: None)
    now = utc_now()
    opened = now - timedelta(hours=9)
    small = SizeSpec(Decimal("0.1"), AmountType.QUANTITY, 1)
    p_main = eng.ledger2.open("ETH/USDT", "LONG", Decimal("100"), small, stop=Decimal("1"), now=opened)
    t2 = next(b for b in eng.strategy_books if b.name == "t2_trend_regime")
    p_t2 = t2.ledger.open("ETH/USDT", "LONG", Decimal("100"), small, stop=Decimal("1"), now=opened)
    fake = lambda syms, **_: ({s: TD(last=Decimal("150")) for s in syms}, {s: 150.0 for s in syms}, {})  # noqa: E731
    monkeypatch.setattr(eng, "_paper_marks", fake)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert any(c[0] == "history" and c[1] == "ETH/USDT" for c in prov.calls), "tur funding ağ adımını çağırmadı"
    due = funding_settlements_between(opened, now, FUNDING_HOURS_UTC)
    for name, p in (("main", p_main), ("t2", p_t2)):
        rows = p.features.get("funding_settlements") or []
        assert len(rows) == len(due) and all(r["mark_basis"] == "SETTLEMENT_ROW" for r in rows), (name, rows)


def test_without_the_source_periods_wait_instead_of_applying_the_current_rate(tmp_path, monkeypatch):
    """Kaynak kapalı (config): dönemler BEKLER — anlık oran geçmiş settlement'a uygulanmaz, tahmin yazılmaz."""
    ov = _ov()
    ov["futures_v3"] = {"realized_funding_source": False}
    eng = _engine(tmp_path, monkeypatch, ov, symbols=2, equity=EQUITY)
    assert eng.funding_rates is None and eng.ledger2.funding.fallback_to_last_known is False
    now = utc_now()
    pos = eng.ledger2.open("ETH/USDT", "LONG", Decimal("100"), SizeSpec(Decimal("1"), AmountType.QUANTITY, 1),
                           stop=Decimal("50"), now=now - timedelta(hours=9))
    pos.meta["last_funding_rate"] = "0.01"                       # eski sürümün bıraktığı son oran
    eng.ledger2.tick({"ETH/USDT": TickData(last=Decimal("100"))}, now_utc=now, funding_rate_lookup=eng.funding_rates)
    assert pos.funding_paid == 0 and pos.funding_received == 0
    assert pos.features["funding_pending"]["reason"] == "RATE_UNKNOWN"
