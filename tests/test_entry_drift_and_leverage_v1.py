# -*- coding: utf-8 -*-
"""GIRIS KAYMASI KAPISI + IHTIYAC KADAR KALDIRAC (2026-09-21).

1) GIRIS KAYMASI
----------------
Kural girisi/stopu/hedefi KAPANMIS bardan turetir; defter ise turun CANLI markiyle acar ve
arada 15 dakikaya kadar gecikme mesrudur (BAR_LAG_TOLERANCE_MS["5m"]=900_000, tur 15 dk).
Kapi olmadan sonuc YONLU bir secim yanliligiydi — boyut yeni fiyati ESKI stopla kullaniyor:
  mark stopa YAKLASTIYSA  -> stop_frac kuculur -> notional siser -> MAX_POSITION_PCT REDDEDER
  mark stoptan UZAKLASTIYSA -> notional kuculur -> islem ACILIR
Yani deftere yalnizca "hareket zaten olmus" girisler suzuluyordu. Bir fade kuralinda (box)
bu tam olarak en kotu alt kume; canli box 11 kapanisin 9'unu kaybetti.

2) IHTIYAC KADAR KALDIRAC
-------------------------
Tek pozisyon tavani `equity x max_position_pct/100 x kaldirac`. Kaldirac SABIT 1 iken stop
mesafesi ozkaynagin %6,67'sinden dar olan her sinyal tavana takiliyordu (box'ta olculdu:
143/143 aday MAX_POSITION_PCT ile reddedilmis). Artik kaldirac islemin IHTIYACI kadar,
`leverage_max` tavanina kadar yukselir. Kaldirac islem basina RISKI ARTIRMAZ (risk stop
mesafesiyle belirlenir); likidasyonu yaklastirir — bu yuzden GEREKMEDIKCE yukseltilmez.

IKISI DE VARSAYILAN KAPALI: drift=0 ve leverage_max=0 iken davranis bit-bit eskisi gibi.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.accounting import FuturesLedgerV2, MarketType, SlippageModel, SymbolFilters  # noqa: E402
from tradingbot.risk import KillSwitch, RiskEngine, build_state, resolve_profile  # noqa: E402
from tradingbot.strategy_paper import DataVerdict, apply_action  # noqa: E402

ETH = "ETH/USDT"
T0 = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)


def _f():
    from decimal import Decimal as D
    return SymbolFilters(symbol=ETH, market_type=MarketType.USDM_PERP, price_tick=D("0.01"),
                         qty_step=D("0.0001"), min_qty=D("0.0001"), min_notional=D("5"), max_leverage=20)


def _kur(equity=1000.0):
    led = FuturesLedgerV2(equity, slippage=SlippageModel.zero())
    prof = resolve_profile("PAPER_RESEARCH")
    risk = RiskEngine(prof, KillSwitch())
    state = build_state(equity=equity, starting_equity=equity, available=equity,
                        used_margin=0.0, positions=[], history=[], now=T0)
    return led, prof, risk, state


def _verdict():
    return DataVerdict(ok=True, entry_ok=True, market="USDM_PERP", source="test",
                       tour_id="t1", bars={"1d": 1}, btc={}, reason="")


def _act(stop, signal_close=None, leverage=1, leverage_max=0):
    a = {"action": "OPEN", "direction": "LONG", "stop": stop, "targets": [],
         "leverage": leverage, "leverage_max": leverage_max, "reason": "TEST", "name": "test"}
    if signal_close is not None:
        a["signal_close"] = signal_close
    return a


def _uygula(act, price, drift=0.0, equity=1000.0):
    led, prof, risk, state = _kur(equity)
    redler = []
    res = apply_action(act, symbol=ETH, price=price, tick=None, now=T0, ledger=led, risk=risk,
                       profile=prof, state=state, filters=_f(), run_id="r",
                       reject=lambda s, r: redler.append(r), on_closed=lambda rec: None,
                       data=_verdict(), max_entry_drift_pct=drift)
    return res, redler, led


# --------------------------------------------------------------- kayma kapisi
def test_the_drift_gate_is_off_by_default():
    """ON KOSUL: kapi kapaliyken buyuk kayma bile islemi ACAR (eski davranis)."""
    res, red, _ = _uygula(_act(stop=90.0, signal_close=100.0), price=130.0)   # %30 kayma
    assert res == "OPENED", "varsayilan davranis degismemeli (red=%s)" % red


def test_a_drift_beyond_tolerance_is_rejected_with_its_own_reason():
    res, red, _ = _uygula(_act(stop=90.0, signal_close=100.0), price=130.0, drift=1.0)
    assert res == "REJECTED" and red == ["ENTRY_DRIFT"], (
        "kayma kapisi tetiklenmedi ya da sebep yanlis: %s / %s" % (res, red))


def test_a_drift_within_tolerance_still_opens():
    res, red, _ = _uygula(_act(stop=90.0, signal_close=100.0), price=100.5, drift=1.0)
    assert res == "OPENED", "tolerans icindeki kayma REDDEDILDI (%s)" % red


def test_the_gate_is_symmetric_it_does_not_only_block_one_direction():
    """ASIL NOKTA: yanlilik YONLUYDU. Kapi iki yonu de ayni esikle keser."""
    yukari, _, _ = _uygula(_act(stop=90.0, signal_close=100.0), price=103.0, drift=1.0)
    asagi, _, _ = _uygula(_act(stop=90.0, signal_close=100.0), price=97.0, drift=1.0)
    assert yukari == "REJECTED" and asagi == "REJECTED", (
        "kapi tek yonlu davraniyor (yukari=%s asagi=%s)" % (yukari, asagi))


def test_a_missing_signal_price_does_not_silently_block_everything():
    """Kural `signal_close` uretmiyorsa kapi SESSIZCE her seyi kesmez — fail-open BILEREK."""
    res, red, _ = _uygula(_act(stop=90.0, signal_close=None), price=130.0, drift=1.0)
    assert res == "OPENED", "signal_close yokken kapi kapatti (%s)" % red


# --------------------------------------------------------------- kaldirac
def test_leverage_stays_at_base_when_the_trade_fits():
    """Genis stop -> notional tavana siğar -> kaldirac YUKSELTILMEZ."""
    # stop %20 uzak: notional = 20/0.20 = 100 USDT; tavan 1000*0.30*1 = 300 -> sigar
    _, _, led = _uygula(_act(stop=80.0, leverage=1, leverage_max=4), price=100.0)
    pos = led.positions[ETH]
    assert int(pos.leverage) == 1, "gereksiz yere kaldirac yukseltildi (%s)" % pos.leverage


def test_leverage_rises_only_as_far_as_the_trade_needs():
    """Dar stop -> notional tavani asar -> kaldirac IHTIYAC kadar yukselir, tavana kadar degil."""
    # stop %2 uzak: notional = 20/0.02 = 1000; tavan/kaldirac = 300 -> gereken ceil(1000/300)=4
    _, _, led = _uygula(_act(stop=98.0, leverage=1, leverage_max=8), price=100.0)
    pos = led.positions[ETH]
    assert int(pos.leverage) == 4, "ihtiyac 4x iken %sx kullanildi" % pos.leverage


def test_leverage_never_exceeds_the_declared_ceiling():
    # stop %1 uzak: gereken ceil(2000/300)=7 ama tavan 3
    res, red, led = _uygula(_act(stop=99.0, leverage=1, leverage_max=3), price=100.0)
    if res == "OPENED":
        assert int(led.positions[ETH].leverage) <= 3, "tavan asildi"
    else:
        assert red and red[0] in ("MAX_POSITION_PCT", "RISK_DENIED"), red


def test_without_a_ceiling_the_old_fixed_leverage_is_kept():
    """ON KOSUL: leverage_max=0 iken davranis bit-bit eskisi gibi."""
    res, red, led = _uygula(_act(stop=98.0, leverage=1, leverage_max=0), price=100.0)
    assert res == "REJECTED" and red == ["MAX_POSITION_PCT"], (
        "tavansiz kolda eski davranis korunmadi: %s / %s" % (res, red))
