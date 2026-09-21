# -*- coding: utf-8 -*-
"""STOP / LİKİDASYON — ÇIKIŞ DOLUM SÖZLEŞMESİ (`exit_fill_v1`, 2026-09-22; REVIEW-2026-09-22 F3).

ÖNCEKİ SÜRÜM NEYİ YANLIŞ İDDİA EDİYORDU
---------------------------------------
2026-09-20 sürümü "stop likidasyondan yakınsa fiyat önce stoptan geçmek ZORUNDADIR, belirsizlik yok" diyerek
likidasyon kontrolünü atlıyordu. Bu yalnız SÜREKLİ ve GÖZLENMİŞ bir fiyat yolunda doğrudur. Ölçülen kusur
(`docs/review/evidence-2026-09-21/stop_liq_gap_probe.py`): fiyat-yalnız tik likidasyonun 50 altında → kayıt
"stop", dolum 1456,02 (likidasyonun ALTINDA), net −102,05 — izole marj 99,00 iken marjdan büyük kayıp. Eski test
yalnız çıkış SEBEBİNİ ve R<0'ı doğruluyordu; dolum fiyatını ve muhasebeyi hiç sınamıyordu. "Yakın stop
delinmemiş ama likidasyon delinmiş" dalı aynı en-kötü fiyatla ERİŞİLEMEZDİ (LONG: stop>liq, worst>stop,
worst<=liq çelişir) ve onu sınadığını söyleyen test `pos.stop=None` kurarak başka yolu sınıyordu. Dal kaldırıldı.

YENİ SÖZLEŞME (tam metin: `tradingbot/accounting/futures_ledger.py` EXIT_FILL_CONTRACT)
* İlk gözlem (bar açılışı / fiyat-yalnız tikte fiyatın kendisi) likidasyonun ötesinde → LİKİDASYON.
* İlk gözlem yalnız stopun ötesinde → STOP, dolum ilk gözlemden.
* İlk gözlem güvenli: en kötü uç yalnız stopu geçtiyse STOP (seviyeden; kapanış da ötedeyse kapanıştan);
  ikisini de geçtiyse ve stop yakınsa bar içi sıra GÖZLENMEDİ → ihtiyatlı LİKİDASYON (kayıtta alternatif).
* Stop hiçbir yolda likidasyonun ötesinden doldurulmaz (kayma dahil).

ETİKET: gerçek `FuturesLedgerV2` ve gerçek likidasyon/ücret modeli çalışır; fiyatlar SENTETİKTİR. Bu dosya bir
kayıt/muhasebe sözleşmesini sınar; borsada kesin gerçekleşecek fiyat ya da kârlılık iddiası DEĞİLDİR.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

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
from tradingbot.accounting.futures_ledger import exit_decision  # noqa: E402
from tradingbot.accounting.models import PositionSide  # noqa: E402

D = Decimal
UTC = timezone.utc
ETH = "ETH/USDT"
T0 = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
TAKER = D("0.0005")                                      # varsayılan FeeSchedule taker %0,05
LIQ_FEE = D("0.005")                                     # LiquidationParams(liq_fee_pct=0.5)


def _f() -> SymbolFilters:
    return SymbolFilters(symbol=ETH, market_type=MarketType.USDM_PERP, price_tick=D("0.01"),
                         qty_step=D("0.001"), min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)


def _open(side: str, stop, *, targets=None, slippage=None):
    """2x, giriş 3000, notional 200 → qty 0,066 (adım), marj 99,00, giriş ücreti 0,099. Döner: (defter, poz, w0)."""
    led = FuturesLedgerV2("1000", slippage=slippage or SlippageModel.zero(), liq_params=LiquidationParams(liq_fee_pct=D("0.5")))
    w0 = led.wallet_balance
    pos = led.open(ETH, side, 3000, SizeSpec(200, AmountType.NOTIONAL, leverage=2), stop=stop, targets=targets,
                   filters=_f(), now=T0)
    assert pos is not None, led.last_reject_reason
    assert pos.qty == D("0.066") and pos.isolated_margin == D("99.000") and pos.entry_fee == D("0.099"), \
        (pos.qty, pos.isolated_margin, pos.entry_fee)
    return led, pos, w0


def _tick(led, **kw):
    td = TickData(**kw)
    if td.high is not None and td.low is not None:          # test girdisi GEÇERLİ bir bar olmalı (bozuk bar sınanmaz)
        assert td.low <= td.ref <= td.high and (td.open is None or td.low <= td.open <= td.high), kw
    return led.tick({ETH: td}, now_utc=T0 + timedelta(hours=1))


def _assert_stop_accounting(led, w0, rec, *, fill, side="LONG"):
    """Stop kaydı: dolum, miktar, brüt, ücretler, net ve cüzdan TEK TEK."""
    sign = D(1) if side == "LONG" else D(-1)
    qty = D("0.066")
    gross = (D(str(fill)) - D(3000)) * qty * sign
    exit_fee = qty * D(str(fill)) * TAKER
    assert rec.exit_reason == "stop", rec.exit_reason
    assert rec.exit_price == D(str(fill)), (rec.exit_price, fill)
    assert sum(fl.qty for fl in rec.fills if fl.kind != "entry") == qty
    assert rec.gross_pnl == gross, (rec.gross_pnl, gross)
    assert rec.exit_fee == exit_fee and rec.fees == D("0.099") + exit_fee, (rec.exit_fee, rec.fees)
    assert rec.net_pnl == gross - D("0.099") - exit_fee + rec.funding
    assert led.wallet_balance - w0 == rec.net_pnl, "cüzdan değişimi işlemin net sonucuyla birebir tutmalı"


def _assert_liquidation_accounting(led, w0, rec, pos_liq):
    """Likidasyon kaydı: çıkış likidasyon fiyatından; fiyat zararı + likidasyon ücreti izole marjı AŞAMAZ ve
    aşıyorsa `liq_clamped` bunu söyler; giriş ücreti ve funding AYRI kalemlerdir."""
    qty = D("0.066")
    assert rec.exit_reason == "likidasyon", rec.exit_reason
    assert rec.exit_price == pos_liq
    liq_fee = qty * pos_liq * LIQ_FEE
    raw_gross = (pos_liq - D(3000)) * qty * (D(1) if rec.side == "LONG" else D(-1))
    clamped = (-raw_gross + liq_fee) > D("99.000")
    assert bool(rec.costs["liq_clamped"]) is clamped
    expected_gross = -(D("99.000") - liq_fee) if clamped else raw_gross
    assert rec.gross_pnl == expected_gross and rec.exit_fee == liq_fee, (rec.gross_pnl, expected_gross, rec.exit_fee)
    assert -rec.gross_pnl + rec.exit_fee <= D("99.000"), "fiyat zararı + likidasyon ücreti izole marjı aşamaz"
    assert rec.net_pnl == rec.gross_pnl - D("0.099") - liq_fee + rec.funding
    assert led.wallet_balance - w0 == rec.net_pnl


# ============================================================ fiyat-yalnız (ardışık) tikler
@pytest.mark.parametrize("side,stop,beyond", [("LONG", 2400, -50), ("SHORT", 3600, +50)])
def test_a_price_tick_beyond_the_liquidation_is_a_liquidation_not_a_stop_filled_past_it(side, stop, beyond):
    """ÖNCE: stop yakın diye likidasyon atlanıyor, stop likidasyonun ÖTESİNDEN dolduruluyordu (LONG −102,05)."""
    led, pos, w0 = _open(side, stop)
    liq = pos.liquidation_price
    recs = _tick(led, last=liq + beyond, mark=liq + beyond)
    assert len(recs) == 1
    _assert_liquidation_accounting(led, w0, recs[0], liq)
    ef = recs[0].features["exit_fill"]
    assert ef["basis"] == "FIRST_OBSERVATION_BEYOND_LIQUIDATION" and ef["stop_triggered"] is True and ef["stop_filled"] is False


@pytest.mark.parametrize("side,stop,ticks,fill", [
    ("LONG", 2400, [2500, 2350], 2350),                  # önce güvenli gözlem, sonra stop ile likidasyon ARASI
    ("SHORT", 3600, [3500, 3650], 3650),
])
def test_sequential_ticks_that_land_between_stop_and_liquidation_fill_the_near_stop(side, stop, ticks, fill):
    """SÜREKLİ yolun gözlendiği durum: stop, likidasyondan önce gerçekleşebilen bir fiyatta dolar (gözlenen fiyattan)."""
    led, pos, w0 = _open(side, stop)
    assert _tick(led, last=ticks[0], mark=ticks[0]) == []
    recs = _tick(led, last=ticks[1], mark=ticks[1])
    assert len(recs) == 1
    _assert_stop_accounting(led, w0, recs[0], fill=fill, side=side)
    assert recs[0].features["exit_fill"]["basis"] == "GAP_FILL_AT_FIRST_OBSERVATION"


# ============================================================ OHLC bar, açılış BİLİNİYOR
@pytest.mark.parametrize("side,stop,bar,fill,basis", [
    # açılış güvenli, dip yalnız stopu geçti, kapanış güvenli → seviyeden
    ("LONG", 2400, dict(open=2990, high=2995, low=2300, last=2450), 2400, "STOP_AT_LEVEL"),
    ("SHORT", 3600, dict(open=3010, high=3700, low=3005, last=3500), 3600, "STOP_AT_LEVEL"),
    # açılış güvenli, kapanış da stopun ötesinde → ihtiyatlı: kapanıştan (tetik sonrası dolum gözlenmedi)
    ("LONG", 2400, dict(open=2990, high=2995, low=2300, last=2350), 2350, "STOP_CLOSE_BEYOND_LEVEL_PRUDENT"),
    # açılış stopun ötesinde ama likidasyondan önce (boşluk) ve bar toparlanıyor → açılıştan
    ("LONG", 2400, dict(open=2300, high=2460, low=2290, last=2450), 2300, "GAP_FILL_AT_FIRST_OBSERVATION"),
    ("SHORT", 3600, dict(open=3700, high=3710, low=3540, last=3550), 3700, "GAP_FILL_AT_FIRST_OBSERVATION"),
])
def test_bar_with_known_open_fills_the_stop_from_what_was_observed(side, stop, bar, fill, basis):
    led, pos, w0 = _open(side, stop)
    recs = _tick(led, mark=bar["last"], **bar)
    assert len(recs) == 1
    _assert_stop_accounting(led, w0, recs[0], fill=fill, side=side)
    assert recs[0].features["exit_fill"]["basis"] == basis
    assert recs[0].features["exit_fill"]["first_source"] == "BAR_OPEN"


@pytest.mark.parametrize("side,stop,open_off,high,low", [("LONG", 2400, -50, D(3000), D(1000)),
                                                         ("SHORT", 3600, +50, D(5000), D(2900))])
def test_a_bar_that_opens_beyond_the_liquidation_is_a_liquidation_even_if_it_recovers(side, stop, open_off, high, low):
    led, pos, w0 = _open(side, stop)
    liq = pos.liquidation_price
    recs = _tick(led, open=liq + open_off, high=high, low=low, last=D(3000), mark=D(3000))
    assert len(recs) == 1
    _assert_liquidation_accounting(led, w0, recs[0], liq)
    assert recs[0].features["exit_fill"]["basis"] == "FIRST_OBSERVATION_BEYOND_LIQUIDATION"


# ============================================================ yalnız OHLC belirsizliği
@pytest.mark.parametrize("with_open", [False, True])
def test_both_levels_inside_one_bar_is_resolved_by_the_declared_prudent_policy(with_open):
    """Açılış güvenli (ya da bilinmiyor), dip hem stopu hem likidasyonu geçmiş, kapanış stopun ÜSTÜNDE.
    ÖNCE: stop 2400'den, net −39,78 — "önce stoptan geçmek zorunda" varsayımıyla. Bar içi sıra gözlenmedi:
    ihtiyatlı politika LİKİDASYON yazar ve alternatifi (seviyeden stop) kayıtta açıkça tutar."""
    led, pos, w0 = _open("LONG", 2400)
    liq = pos.liquidation_price
    kw = dict(high=D(2995), low=liq - 50, last=D(2450), mark=D(2450))
    if with_open:
        kw["open"] = D(2990)
    recs = _tick(led, **kw)
    assert len(recs) == 1
    _assert_liquidation_accounting(led, w0, recs[0], liq)
    ef = recs[0].features["exit_fill"]
    assert ef["basis"] == "INTRABAR_ORDER_UNOBSERVED" and ef["policy"] == "PRUDENT_LIQUIDATION"
    assert ef["alternative"] == "STOP_AT_LEVEL" and ef["stop_triggered"] is True and ef["stop_filled"] is False
    assert ef["first_source"] == ("BAR_OPEN" if with_open else "UNKNOWN")


def test_a_nearer_liquidation_still_wins():
    """Likidasyon stoptan YAKINSA her yolda önce o gelir (değişmedi)."""
    led, pos, w0 = _open("LONG", 100)
    liq = pos.liquidation_price
    recs = _tick(led, last=liq - 10, mark=liq - 10)
    _assert_liquidation_accounting(led, w0, recs[0], liq)
    assert recs[0].features["exit_fill"]["basis"] == "FIRST_OBSERVATION_BEYOND_LIQUIDATION"
    led, pos, w0 = _open("LONG", 100)
    recs = _tick(led, open=D(2990), high=D(2995), low=liq - 10, last=D(2450), mark=D(2450))
    _assert_liquidation_accounting(led, w0, recs[0], liq)
    assert recs[0].features["exit_fill"]["basis"] == "LIQUIDATION_NEARER"


def test_same_bar_stop_and_target_is_still_the_stop():
    """Aynı barda stop ve hedef → STOP (worst-case; değişmedi)."""
    led, pos, w0 = _open("LONG", 2400, targets=[3300])
    recs = _tick(led, open=D(3000), high=D(3400), low=D(2300), last=D(3100), mark=D(3100))
    assert len(recs) == 1
    _assert_stop_accounting(led, w0, recs[0], fill=2400)


def test_slippage_can_never_push_a_stop_fill_beyond_the_liquidation():
    """Kayma modeli dolumu likidasyonun ötesine iterse kayıt LİKİDASYON'dur (izole marjda o dolum olamaz)."""
    led, pos, w0 = _open("LONG", None, slippage=SlippageModel(fixed_bps=D("0")))
    liq = pos.liquidation_price
    pos.stop = liq + D("1")                               # likidasyona çok yakın stop
    led.slippage = SlippageModel(fixed_bps=D("100"))      # %1 kayma → dolum likidasyonun altına düşer
    recs = _tick(led, last=liq + D("0.5"), mark=liq + D("0.5"))
    assert len(recs) == 1 and recs[0].exit_reason == "likidasyon"
    assert recs[0].features["exit_fill"]["basis"] == "SLIPPAGE_BEYOND_LIQUIDATION"
    _assert_liquidation_accounting(led, w0, recs[0], liq)


def test_invariant_no_decision_ever_fills_a_stop_beyond_the_liquidation():
    """Erişilemez dalın yerini alan DAVRANIŞ değişmezi: stop/likidasyon/tik biçimi ızgarasında hiçbir karar,
    likidasyonun ötesinde bir stop dolum referansı üretmez; likidasyon ötesindeki ilk gözlem her zaman likidasyondur."""
    liq = {"LONG": D("1506.02"), "SHORT": D("4482.07")}
    stops = {"LONG": [D(2400), D(1600), D(1510), D(100)], "SHORT": [D(3600), D(4400), D(4480), D(9000)]}
    prices = [D(x) for x in (1000, 1456, 1506, 1507, 1550, 2300, 2399, 2400, 2450, 3000, 3550, 3600, 3700,
                             4400, 4481, 4483, 4600, 5000)]
    n = 0
    for side in ("LONG", "SHORT"):
        ps, L = PositionSide(side), liq[side]
        beyond_liq = (lambda p: p <= L) if side == "LONG" else (lambda p: p >= L)
        for stop in stops[side]:
            for first in prices:
                for worst in prices:
                    for close in prices:
                        lo, hi = min(first, worst, close), max(first, worst, close)
                        for td in (TickData(last=first, mark=first),
                                   TickData(last=close, mark=close, high=hi, low=lo, open=first),
                                   TickData(last=close, mark=close, high=hi, low=lo)):
                            d = exit_decision(ps, stop, L, td)
                            n += 1
                            if d is None:
                                continue
                            if d["kind"] == "stop":
                                assert not beyond_liq(d["ref"]), (side, stop, td, d)
                            if td.open is not None and beyond_liq(td.open):
                                assert d["kind"] == "liquidation", (side, stop, td, d)
    assert n > 10_000
