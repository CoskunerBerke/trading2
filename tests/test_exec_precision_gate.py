"""Hassasiyet kapisi (opt-in) — defterin `open()` yolunda dogrulama, `tick()` ETKILENMEZ."""
from __future__ import annotations

from decimal import Decimal as D

from tradingbot.accounting.futures_ledger import R_UNVERIFIED_PRECISION, FuturesLedgerV2
from tradingbot.accounting.models import (AmountType, MarketType, SizeSpec, SymbolFilters,
                                          TickData)


def _verified(sym="TRX/USDT"):
    return SymbolFilters(symbol=sym, market_type=MarketType.USDM_PERP, price_tick=D("0.0001"),
                         qty_step=D("1"), min_qty=D("1"), min_notional=D("5"),
                         source="binance:usdm:exchangeInfo", verified_at="2026-09-08T00:00:00Z")


def _size(notional="30", lev=3):
    return SizeSpec(D(notional), AmountType.NOTIONAL, lev)


def test_gate_is_off_by_default_behaviour_unchanged():
    """VARSAYILAN kapali: dogrulanmamis filtreyle de acilir (mevcut uretim davranisi)."""
    led = FuturesLedgerV2(D("100"))
    assert led.require_verified_precision is False
    pos = led.open("TRX/USDT", "LONG", D("0.3386"), _size(), stop=D("0.3341872085677379"),
                   targets=[D("0.34742558286452413")], tick=TickData(last=D("0.3386")))
    assert pos is not None                                   # eski davranis KORUNDU
    assert led.last_reject_reason in ("", "OK")


def test_gate_on_blocks_unverified_precision():
    led = FuturesLedgerV2(D("100"), require_verified_precision=True)
    pos = led.open("TRX/USDT", "LONG", D("0.3386"), _size(), stop=D("0.3341872085677379"),
                   targets=[D("0.34742558286452413")], tick=TickData(last=D("0.3386")))
    assert pos is None
    assert led.last_reject_reason == R_UNVERIFIED_PRECISION
    assert not led.positions                                 # sahte dolum URETILMEDI


def test_gate_on_allows_verified_precision_and_preserves_geometry():
    led = FuturesLedgerV2(D("100"), require_verified_precision=True)
    pos = led.open("TRX/USDT", "LONG", D("0.3386"), _size(), stop=D("0.3341872085677379"),
                   targets=[D("0.34742558286452413")], filters=_verified(),
                   tick=TickData(last=D("0.3386")))
    assert pos is not None and led.last_reject_reason in ("", "OK")
    assert pos.entry_avg == D("0.3386")                      # tam tick -> yuvarlama YOK
    risk = pos.entry_avg - D(pos.initial_stop)
    r_to_tp1 = (D("0.34742558286452413") - pos.entry_avg) / risk
    assert abs(r_to_tp1 - D("2")) < D("1e-9")                # 2R plan KORUNDU


def test_gate_does_not_disable_exits_for_open_positions():
    """Kapi ACIKKEN bile, ONCEDEN acilmis pozisyonun stop'u calisir."""
    led = FuturesLedgerV2(D("100"))                          # kapi KAPALI iken ac
    pos = led.open("ZEN/USDT", "LONG", D("7.06"), _size("10.57"),
                   stop=D("6.145746696862394"), targets=[D("8.86")],
                   tick=TickData(last=D("7.06")))
    assert pos is not None
    led.require_verified_precision = True                    # kapi SONRADAN acildi
    closed = led.tick({"ZEN/USDT": TickData(last=D("6.00"), mark=D("6.00"))})
    assert [c.exit_reason for c in closed] == ["stop"]       # koruyucu cikis CALISTI
    assert "ZEN/USDT" not in led.positions


def test_gate_reason_is_distinct_from_other_rejects():
    led = FuturesLedgerV2(D("100"), require_verified_precision=True)
    led.open("NATGAS/USDT", "LONG", D("2.932"), _size(), stop=D("2.8599766913815676"),
             tick=TickData(last=D("2.932")))
    assert led.last_reject_reason == R_UNVERIFIED_PRECISION
    assert R_UNVERIFIED_PRECISION not in ("OK", "BAD_PRICE", "MIN_NOTIONAL", "LEVERAGE_TOO_HIGH")
