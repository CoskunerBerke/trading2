# -*- coding: utf-8 -*-
"""LIKIDASYON vs STOP — HANGISI ONCE (2026-09-20).

OLCULEN KUSUR
-------------
`FuturesLedgerV2.tick` likidasyonu KOSULSUZ olarak stoptan once kontrol ediyordu. Tek bir
barda her iki seviye de delindiginde motor stop yerine likidasyonu uyguluyor ve zarar 1R
yerine tum marja (+likidasyon ucreti) cikiyordu.

Oysa belirsizlik YOK: stop likidasyondan DAHA YAKINSA fiyat, uzaktaki likidasyona varmak
icin once yakindaki stoptan GECMEK ZORUNDADIR. Sira fizikseldir.

NEDEN SIMDI: kusur 1x kaldiracta UYKUDAYDI — likidasyon ulasilamiyordu. Olculdu (620
sampiyon kapanis, bn_archive): L=2'de stop mesafesi medyan %19,2 / maks %39,7, likidasyon
%49,80 -> 0/620 mesru likidasyon. Kullanicinin "asla 1x olmasin" talimati uygulandigi anda
kanal aciliyor: L=2'de 2 islem -1R yerine -1,40R/-1,41R, L=3'te 10 islem (~+3,6R fazladan
zarar, en kotusu -2,08R), L=5'te 7.

SOZLESME
--------
1. Stop likidasyondan YAKINSA ve bar ikisini de delmisse -> STOP uygulanir (zarar ~1R).
2. Likidasyon stoptan YAKINSA -> likidasyon ONCELIKLI kalir. Aksi halde gercek likidasyon
   riski gizlenir ve motor gap barlarinda fazla iyimser olur.
3. Stop daha yakin ama DELINMEMIS, likidasyon delinmisse -> likidasyon (tutarsiz girdide
   guvenli taraf; fail-closed).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.accounting import (  # noqa: E402
    AmountType,
    FuturesLedgerV2,
    LiquidationParams,
    MarketType,
    SizeSpec,
    SlippageModel,
    SymbolFilters,
    TickData,
)

D = Decimal
UTC = timezone.utc
ETH = "ETH/USDT"
T0 = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


def _f() -> SymbolFilters:
    return SymbolFilters(symbol=ETH, market_type=MarketType.USDM_PERP, price_tick=D("0.01"),
                         qty_step=D("0.001"), min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)


def _led(equity="1000") -> FuturesLedgerV2:
    return FuturesLedgerV2(equity, slippage=SlippageModel.zero(),
                           liq_params=LiquidationParams(liq_fee_pct=D("0.5")))


def _ac(led, stop):
    """2x LONG — likidasyon fiyati ~girisin %50 altinda olur (mmr ile birlikte)."""
    return led.open(ETH, "LONG", 3000, SizeSpec(200, AmountType.NOTIONAL, leverage=2),
                    stop=stop, filters=_f(), now=T0)


def test_a_nearer_stop_fires_before_a_farther_liquidation(tmp_path=None):
    """ASIL ISPAT: bar ikisini de deliyor, stop daha YAKIN -> stop uygulanir."""
    led = _led()
    pos = _ac(led, stop=2400)                        # %20 asagi
    assert pos is not None and pos.liquidation_price is not None
    liq = float(pos.liquidation_price)
    assert liq < 2400, "on kosul: likidasyon stoptan UZAK olmali (%.2f)" % liq

    # Bar hem stopu hem likidasyonu deliyor (dip likidasyonun da altinda).
    closed = led.tick({ETH: TickData(last=float(liq) - 50, mark=float(liq) - 50, low=float(liq) - 50)},
                      now_utc=T0 + timedelta(hours=1))
    assert closed, "pozisyon kapanmaliydi"
    rec = closed[0]
    assert "likidasyon" not in str(rec.exit_reason).lower(), (
        "stop daha yakin oldugu halde LIKIDASYON uygulandi (sebep=%s) — fiyat uzaktaki "
        "likidasyona varmak icin once yakindaki stoptan gecmek zorundadir" % rec.exit_reason)
    assert float(rec.r_multiple) < 0


def test_a_nearer_liquidation_still_wins(tmp_path=None):
    """ON KOSUL / SOZLESME 2: likidasyon stoptan YAKINSA oncelikli kalir."""
    led = _led()
    pos = _ac(led, stop=100)                         # cok UZAK stop -> likidasyon daha yakin
    assert pos is not None and pos.liquidation_price is not None
    liq = float(pos.liquidation_price)
    assert liq > 100, "on kosul: likidasyon stoptan YAKIN olmali"

    closed = led.tick({ETH: TickData(last=float(liq) - 10, mark=float(liq) - 10, low=float(liq) - 10)},
                      now_utc=T0 + timedelta(hours=1))
    assert closed, "pozisyon kapanmaliydi"
    assert "likidasyon" in str(closed[0].exit_reason).lower(), (
        "likidasyon daha yakinken stop uygulandi (sebep=%s) — gercek likidasyon riski "
        "gizlenmemeli" % closed[0].exit_reason)


def test_an_untouched_nearer_stop_does_not_shield_a_breached_liquidation(tmp_path=None):
    """SOZLESME 3: tutarsiz girdide guvenli taraf — stop delinmemis, likidasyon delinmisse likidasyon."""
    led = _led()
    pos = _ac(led, stop=2400)
    liq = float(pos.liquidation_price)
    # Fiziksel olarak imkansiz girdi: dip likidasyonun altinda AMA stopun ustunde degil...
    # bunu ancak stopu tick'ten SONRA kaldirarak kurabiliriz (gercek dunyada olmaz).
    pos.stop = None
    closed = led.tick({ETH: TickData(last=float(liq) - 50, mark=float(liq) - 50, low=float(liq) - 50)},
                      now_utc=T0 + timedelta(hours=1))
    assert closed and "likidasyon" in str(closed[0].exit_reason).lower()


def test_a_normal_stop_without_liquidation_is_unchanged(tmp_path=None):
    """ON KOSUL: siradan stop yolu DEGISMEDI (onarim davranisi genisletmedi)."""
    led = _led()
    _ac(led, stop=2400)
    closed = led.tick({ETH: TickData(last=2350, mark=2350, low=2350)}, now_utc=T0 + timedelta(hours=1))
    assert closed and "likidasyon" not in str(closed[0].exit_reason).lower()
