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


def test_tour_step_fetches_realized_rows_and_the_exit_monitor_stays_offline(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=2, equity=EQUITY)
    prov = SynthFundingProvider(rate=0.0001, mark=100.0)
    monkeypatch.setattr(eng, "_gap_provider_factory", lambda: prov)
    now = utc_now()
    opened = now - timedelta(hours=9)
    sym = "ETH/USDT"
    pos = eng.ledger2.open(sym, "LONG", Decimal("100"), SizeSpec(Decimal("1"), AmountType.QUANTITY, 1), stop=Decimal("50"), now=opened)
    t2 = next(b for b in eng.strategy_books if b.name == "t2_trend_regime")
    pos_t2 = t2.ledger.open(sym, "SHORT", Decimal("100"), SizeSpec(Decimal("0.5"), AmountType.QUANTITY, 1), stop=Decimal("150"), now=opened)
    assert pos is not None and pos_t2 is not None
    # spot defterindeki varlık futures funding'ine GİRMEZ
    eng.spot2.market_buy("SOL/USDT", qty=Decimal("1"), ref_price=Decimal("10"), now=now)
    assert eng.spot2.qty("SOL/USDT") > 0
    eng.ensure_gap_reconciled()
    out = eng._funding_step(now)
    assert out["refresh"]["fetched"] >= 1
    asked = {s for k, s in prov.calls if k == "history"}
    assert sym in asked and "SOL/USDT" not in asked
    due = funding_settlements_between(opened, now, FUNDING_HOURS_UTC)
    assert due, "pencere en az bir settlement içermeli"
    eng.ledger2.tick({sym: TickData(last=Decimal("90"))}, now_utc=now, funding_rate_lookup=eng.funding_rates)
    t2.tick({sym: TickData(last=Decimal("90"))}, now=now, funding_rate_lookup=eng.funding_rates, bar_advance=False)
    for p, per in ((pos, Decimal("-0.01")), (pos_t2, Decimal("0.005"))):     # qty x mark(100) x 0.0001; LONG öder, SHORT alır
        rows = p.features["funding_settlements"]
        assert len(rows) == len(due)
        assert all(r["mark_basis"] == "SETTLEMENT_ROW" and Decimal(r["mark"]) == Decimal("100") for r in rows)   # 90 DEĞİL
        assert (p.funding_received - p.funding_paid) == per * len(due)
    n = len(prov.calls)
    closed = eng.exit_check()                                     # 60 sn izleyici: yalnız bellek
    assert len(prov.calls) == n, "çıkış izleyicisi funding ağına ÇIKMAMALI"
    assert isinstance(closed, list)


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
