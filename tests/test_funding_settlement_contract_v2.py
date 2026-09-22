# -*- coding: utf-8 -*-
"""FUNDING SETTLEMENT SÖZLEŞMESİ v2 (2026-09-22) — verinin ULAŞMA ZAMANI maliyeti değiştirmez.

Kusur (bağımsız inceleme, sentetik, ücret/kayma sıfır):
* FİYAT: 07:59 LONG 1 @100 stop 95; 08:00 settlement oran 0.001 mark 100; 08:01 stop 94. Oran kapanıştan ÖNCE
  biliniyorsa `accrue` güncel 94'ü kullanıyordu (−0.094), SONRA gelirse `settle_late_funding` 100'ü (−0.100).
* MİKTAR: 08:01'de funding bilinmezken yarısı kapanıyor, 08:02'de oran geliyor: açık tahakkuk güncel 0.5 ile
  (−0.050) yazıyordu; baştan bilinseydi −0.100. Watermark ilerlediği için geç uzlaştırma eksik kısmı tamamlamıyordu.

Beklenen (sözleşme, `accounting/funding.py`): tutar = settlement anındaki miktar × o settlement'ın gerçekleşmiş oranı ×
kendi mark'ı; iki yol AYNI fonksiyonları kullanır. Testler GERÇEK defter (`FuturesLedgerV2`), GERÇEK kaynak
(`FundingRates`, bellek + `refresh`) ve sentetik sağlayıcıyla (satırın yayım anı enjekte edilir) koşar.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.accounting import FeeSchedule, SlippageModel, TickData  # noqa: E402
from tradingbot.accounting.funding import FUNDING_SETTLEMENT_CONTRACT, FundingSchedule, static_rates  # noqa: E402
from tradingbot.accounting.futures_ledger import FuturesLedgerV2  # noqa: E402
from tradingbot.accounting.models import AmountType, LedgerKind, SizeSpec  # noqa: E402
from tradingbot.pattern_trader.funding import FundingRates  # noqa: E402

UTC = timezone.utc
SYM = "TEST/USDT"
T0759 = datetime(2026, 9, 22, 7, 59, tzinfo=UTC)
T0800 = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
T0801 = datetime(2026, 9, 22, 8, 1, tzinfo=UTC)
T0802 = datetime(2026, 9, 22, 8, 2, tzinfo=UTC)
D = Decimal


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class SynthProvider:
    """`/fapi/v1/fundingRate` şekli; satır `publish_ms`den ÖNCE yoktur. `calls` ağ çağrılarını sayar."""

    def __init__(self, rows, clock):
        self.rows, self.clock, self.calls = rows, clock, 0

    def funding_history(self, symbol, limit=1000, start_ms=None, end_ms=None):
        self.calls += 1
        now = self.clock()
        return [dict(r) for r in self.rows if r["publish_ms"] <= now and r["symbol"] == symbol.replace("/", "")
                and (start_ms is None or r["funding_ts"] >= start_ms) and (end_ms is None or r["funding_ts"] <= end_ms)]

    def funding_info(self):
        self.calls += 1
        return []


def _world(*, publish_at, rate="0.001", mark="100", with_mark=True, settle_at=T0800):
    clock = {"ms": _ms(T0759)}
    row = {"symbol": "TESTUSDT", "funding_ts": _ms(settle_at), "rate": float(rate), "publish_ms": _ms(publish_at)}
    if with_mark:
        row["mark"] = float(mark)
    prov = SynthProvider([row], lambda: clock["ms"])
    rates = FundingRates(prov, clock_ms=lambda: clock["ms"], rate_ttl_s=30.0)
    led = FuturesLedgerV2(D("1000"), fees=FeeSchedule(maker_pct=D("0"), taker_pct=D("0")), slippage=SlippageModel.zero(),
                          funding=FundingSchedule())
    led.funding.bind_source(rates)
    return led, rates, clock, prov


def _net(rates, clock, at):
    """Tarayıcı/tur ağ adımı (defter kilidi DIŞINDA)."""
    clock["ms"] = _ms(at)
    rates.refresh([SYM], now_ms=_ms(at))


def _open(led, side="LONG", qty="1", price="100", stop="95", at=T0759):
    pos = led.open(SYM, side, D(price), SizeSpec(D(qty), AmountType.QUANTITY, 1), stop=D(stop), now=at)
    assert pos is not None, led.last_reject_reason
    return pos


def _funding_entries(led, trade_id):
    return sum((e.amount for e in led.entries if e.kind is LedgerKind.FUNDING and e.ref_id == trade_id), D("0"))


def _reconciled(led, rec, start=D("1000")):
    """Cüzdan, kayıt, defter girişleri ve toplam funding BİRBİRİNİ TUTAR; R aynı payda ile."""
    assert _funding_entries(led, rec.id) == rec.funding == led.total_funding
    assert led.wallet_balance == start + rec.net_pnl
    risk = D(rec.features["risk_usdt"])
    if risk > 0:
        assert rec.r_multiple == rec.net_pnl / risk


# ----------------------------------------------------------------------------------------------- vaka A: fiyat
def _case_a(publish_at, *, side="LONG", rate="0.001", stop=None, exit_px="94"):
    led, rates, clock, _ = _world(publish_at=publish_at, rate=rate)
    _open(led, side=side, stop=stop or ("95" if side == "LONG" else "105"))
    _net(rates, clock, T0801)
    led.tick({SYM: TickData(last=D(exit_px))}, now_utc=T0801, funding_rate_lookup=rates)
    assert not led.positions, "stop tetiklenmeliydi"
    _net(rates, clock, T0802)
    led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for)
    return led, led.history[-1]


def test_price_case_same_result_whether_the_rate_arrives_before_or_after_the_close():
    led_e, early = _case_a(T0800)
    led_l, late = _case_a(T0802)
    assert early.funding == late.funding == D("-0.1")                  # settlement mark 100, güncel 94 DEĞİL
    assert early.net_pnl == late.net_pnl == D("-6.1")
    assert led_e.wallet_balance == led_l.wallet_balance == D("993.9")
    _reconciled(led_e, early)
    _reconciled(led_l, late)
    # dayanak kayıtta: açık yol ve geç yol aynı mark'ı ve miktarı yazdı
    (se,), (sl,) = early.features["funding_settlements"], late.features["funding_settlements"]
    assert (se["path"], sl["path"]) == ("OPEN", "LATE")
    assert se["mark"] == sl["mark"] and D(se["mark"]) == D("100") and D(se["qty"]) == D(sl["qty"]) == D("1")
    assert se["mark_basis"] == sl["mark_basis"] == "SETTLEMENT_ROW"
    assert early.features["funding_contract"] == late.features["funding_contract"] == FUNDING_SETTLEMENT_CONTRACT
    assert early.features["funding_coverage"]["complete"] and late.features["funding_coverage"]["complete"]


@pytest.mark.parametrize("side,rate,expected", [("LONG", "0.001", "-0.1"), ("SHORT", "0.001", "0.1"),
                                                ("LONG", "-0.002", "0.2"), ("SHORT", "-0.002", "-0.2")])
def test_sign_rules_hold_on_both_paths(side, rate, expected):
    """rate>0 LONG öder/SHORT alır; rate<0 tersi — iki geliş zamanında AYNI tutar."""
    exit_px = "94" if side == "LONG" else "106"
    _, early = _case_a(T0800, side=side, rate=rate, exit_px=exit_px)
    _, late = _case_a(T0802, side=side, rate=rate, exit_px=exit_px)
    assert early.funding == late.funding == D(expected)


# ----------------------------------------------------------------------------------------------- vaka B: miktar
def _case_b(publish_at, *, side="LONG"):
    led, rates, clock, _ = _world(publish_at=publish_at)
    _open(led, side=side, stop="50" if side == "LONG" else "150")
    _net(rates, clock, T0801)
    led.tick({SYM: TickData(last=D("100"))}, now_utc=T0801, funding_rate_lookup=rates)
    led.close_partial(SYM, D("100"), D("0.5"), now=T0801)
    _net(rates, clock, T0802)
    led.tick({SYM: TickData(last=D("100"))}, now_utc=T0802, funding_rate_lookup=rates)     # kalan 0.5 hâlâ açık
    led.close_manual(SYM, D("100"), now=T0802)
    led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for)
    return led, led.history[-1]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_quantity_case_uses_the_quantity_open_at_the_settlement(side):
    led_e, early = _case_b(T0800, side=side)
    led_l, late = _case_b(T0802, side=side)
    want = D("-0.1") if side == "LONG" else D("0.1")
    assert early.funding == late.funding == want                       # 1 birim; güncel 0.5 DEĞİL
    assert early.net_pnl == late.net_pnl == want
    _reconciled(led_e, early)
    _reconciled(led_l, late)
    assert [D(x["qty"]) for x in late.features["funding_settlements"]] == [D("1")]


def test_partial_close_before_the_settlement_pays_only_the_remaining_quantity():
    """07:59:30'da yarısı kapanır → 08:00'de açık 0.5 → −0.050 (iki yolda)."""
    for publish in (T0800, T0802):
        led, rates, clock, _ = _world(publish_at=publish)
        _open(led, stop="50")
        led.close_partial(SYM, D("100"), D("0.5"), now=T0759 + timedelta(seconds=30))
        _net(rates, clock, T0801)
        led.tick({SYM: TickData(last=D("100"))}, now_utc=T0801, funding_rate_lookup=rates)
        led.close_manual(SYM, D("100"), now=T0801)
        _net(rates, clock, T0802)
        led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for)
        assert led.history[-1].funding == D("-0.05"), publish


def test_same_instant_ordering_close_at_the_settlement_pays_open_at_the_settlement_does_not():
    # t anında KAPANAN miktar o dönemi öder (pozisyon settlement'ta açıktı) — geç yol
    led, rates, clock, _ = _world(publish_at=T0802)
    _open(led, stop="50")
    led.close_manual(SYM, D("100"), now=T0800)
    _net(rates, clock, T0802)
    led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for)
    assert led.history[-1].funding == D("-0.1")
    # aynı an, açık yol: tik settlement ANINDA (now == t) önce tahakkuk eder, sonra kapanır
    led2, rates2, clock2, _ = _world(publish_at=T0800)
    _open(led2, stop="99.5")
    _net(rates2, clock2, T0800)
    led2.tick({SYM: TickData(last=D("99"))}, now_utc=T0800, funding_rate_lookup=rates2)
    assert led2.history[-1].funding == D("-0.1")
    # t anında AÇILAN pozisyon o dönemi ödemez (pencere açılıştan SONRAKİ settlement'lar)
    led3, rates3, clock3, _ = _world(publish_at=T0800)
    _open(led3, stop="50", at=T0800)
    _net(rates3, clock3, T0801)
    led3.tick({SYM: TickData(last=D("100"))}, now_utc=T0801, funding_rate_lookup=rates3)
    led3.close_manual(SYM, D("100"), now=T0801)
    led3.settle_late_funding(rates3, now=T0801, hours_for=rates3.hours_for)
    assert led3.history[-1].funding == 0


# ----------------------------------------------------------------------------------------------- bekleme / tekillik / restart
def test_missing_settlement_mark_waits_visibly_then_posts_once():
    """Oran var, satırın mark'ı YOK → dönem BEKLER (güncel fiyat vekil yapılmaz), bekleyen maliyet kayıtta görünür."""
    led, rates, clock, prov = _world(publish_at=T0800, with_mark=False)
    pos = _open(led, stop="50")
    _net(rates, clock, T0801)
    led.tick({SYM: TickData(last=D("94.5"))}, now_utc=T0801, funding_rate_lookup=rates)
    assert pos.funding_paid == 0 and pos.last_funding_settlement_utc == pos.opened_at
    assert pos.features["funding_pending"] == {"since": "2026-09-22T08:00:00+00:00", "reason": "SETTLEMENT_MARK_MISSING"}
    led.close_manual(SYM, D("94.5"), now=T0801)
    rec = led.history[-1]
    assert rec.funding == 0 and rec.features["funding_coverage"]["complete"] is False      # bekleyen maliyet ≠ sıfır maliyet
    assert SYM in led.funding_pending_symbols()
    # satırın mark'ı sonradan gelir (düzeltilmiş yayım) → bir kez işlenir
    prov.rows[0]["mark"] = 100.0
    _net(rates, clock, T0802 + timedelta(minutes=1))
    assert len(led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for)) == 1
    assert led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for) == []
    assert rec.funding == D("-0.1") and rec.features["funding_coverage"]["complete"] is True
    assert "funding_pending" not in rec.features
    _reconciled(led, rec)


def test_restart_between_close_and_arrival_posts_exactly_once(tmp_path: Path):
    led, rates, clock, _ = _world(publish_at=T0802)
    _open(led, stop="95")
    _net(rates, clock, T0801)
    led.tick({SYM: TickData(last=D("94"))}, now_utc=T0801, funding_rate_lookup=rates)
    p = tmp_path / "led.json"
    led.save(p)
    for _ in range(2):                                              # iki yeniden başlatma
        led = FuturesLedgerV2.load(p)
        led.funding.bind_source(rates)
        _net(rates, clock, T0802)
        led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for)
        led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for)
        led.save(p)
    led = FuturesLedgerV2.load(p)
    rec = led.history[-1]
    assert rec.funding == D("-0.1") and led.total_funding == D("-0.1") and rec.net_pnl == D("-6.1")
    assert _funding_entries(led, rec.id) == D("-0.1")
    assert led.wallet_balance == D("993.9")


def test_open_path_settlement_is_never_posted_again_by_the_late_path():
    led, rates, clock, _ = _world(publish_at=T0800)
    _open(led, stop="95")
    _net(rates, clock, T0801)
    led.tick({SYM: TickData(last=D("94"))}, now_utc=T0801, funding_rate_lookup=rates)
    for _ in range(3):
        assert led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for) == []
    assert led.history[-1].funding == D("-0.1") and led.total_funding == D("-0.1")


def test_partial_close_then_full_close_both_before_arrival_settles_the_full_quantity_once():
    """TP1 benzeri kısmi kapanış 08:01, kalan 08:30'da kapanır, oran 09:00'da gelir → qty(08:00)=1, tek kayıt."""
    led, rates, clock, _ = _world(publish_at=datetime(2026, 9, 22, 9, 0, tzinfo=UTC))
    _open(led, stop="50")
    _net(rates, clock, T0801)
    led.tick({SYM: TickData(last=D("101"))}, now_utc=T0801, funding_rate_lookup=rates)
    led.close_partial(SYM, D("101"), D("0.5"), now=T0801)
    led.close_manual(SYM, D("99"), now=datetime(2026, 9, 22, 8, 30, tzinfo=UTC))
    rec = led.history[-1]
    assert rec.funding == 0 and rec.features["funding_coverage"]["complete"] is False
    _net(rates, clock, datetime(2026, 9, 22, 9, 1, tzinfo=UTC))
    assert len(led.settle_late_funding(rates, now=T0802, hours_for=rates.hours_for)) == 1
    assert rec.funding == D("-0.1")
    _reconciled(led, rec)


def test_rate_only_lookup_is_labelled_and_never_claims_a_settlement_mark():
    """Eski yalnız-oran lookup (static_rates) hâlâ kullanılabilir ama olay `TICK_MARK` taşır; üretim bunu kullanmaz."""
    led = FuturesLedgerV2(D("1000"), fees=FeeSchedule(maker_pct=D("0"), taker_pct=D("0")), slippage=SlippageModel.zero())
    pos = _open(led, stop="50")
    led.tick({SYM: TickData(last=D("94"))}, now_utc=T0801, funding_rate_lookup=static_rates({SYM: D("0.001")}))
    (row,) = pos.features["funding_settlements"]
    assert row["mark_basis"] == "TICK_MARK" and D(row["mark"]) == D("94")
    assert pos.features["funding_rate_source"] == "rate_only"


def test_legacy_positions_record_when_the_contract_took_over():
    """Sözleşmeden önce işlenmiş settlement'lar sonradan doğrulanmış SAYILMAZ: devralma anı kayıtta."""
    led, rates, clock, _ = _world(publish_at=T0800)
    pos = _open(led, stop="50", at=datetime(2026, 9, 21, 23, 0, tzinfo=UTC))
    pos.last_funding_settlement_utc = "2026-09-22T00:00:00+00:00"     # eski sürümün işlediği dönem
    pos.features.pop("funding_contract", None)
    _net(rates, clock, T0801)
    led.tick({SYM: TickData(last=D("100"))}, now_utc=T0801, funding_rate_lookup=rates)
    assert pos.features["funding_contract_from"] == "2026-09-22T00:00:00+00:00"
    assert pos.features["funding_contract"] == FUNDING_SETTLEMENT_CONTRACT


# ----------------------------------------------------------------------------------------------- tur ağ bütçesi
def test_refresh_budget_bounds_the_network_time_and_leaves_the_rest_pending():
    """Yavaş ağ turu sınırsız uzatmaz: bütçe aşılınca kalan semboller bir sonraki adıma kalır (dönemleri bekler)."""
    tick = {"t": 0.0}

    class SlowProvider:
        def __init__(self):
            self.asked = []

        def funding_history(self, symbol, limit=1000, start_ms=None, end_ms=None):
            self.asked.append(symbol)
            tick["t"] += 10.0                                    # her istek 10 sn sürüyor (sentetik saat)
            return []

        def funding_info(self):
            return []

    prov = SlowProvider()
    fr = FundingRates(prov, clock_ms=lambda: _ms(T0802), monotonic=lambda: tick["t"])
    out = fr.refresh(["A/USDT", "B/USDT", "C/USDT"], budget_s=15.0)
    assert prov.asked == ["A/USDT", "B/USDT"] and out["budget_exhausted"] == ["C/USDT"]


# ----------------------------------------------------------------------------------------------- replay: satır mark'ı / vekil
def test_replay_source_uses_the_row_mark_then_a_declared_proxy_then_waits():
    """Arşivde 2023 sonu öncesi `markPrice` boştur. Replay vekili (settlement anındaki bar açılışı) İLAN EDİLİR ve
    olay `BAR_OPEN_PROXY` taşır; vekil de yoksa dönem BEKLER (tutar uydurulmaz)."""
    import pandas as pd

    from tradingbot.replay.funding_archive import ArchiveFundingRates

    t1, t2 = _ms(T0800), _ms(T0800) + 8 * 3_600_000

    class Store:
        def read(self, market, symbol, kind):
            return pd.DataFrame({"timestamp": [t1, t2], "rate": [0.001, 0.001], "mark": [100.0, float("nan")]})

    src = ArchiveFundingRates(Store())
    w1 = T0800
    w2 = T0800 + timedelta(hours=8)
    assert src.settlement_mark(SYM, w1) == D("100") and src.mark_basis(SYM, w1) == "SETTLEMENT_ROW"
    assert src.settlement_mark(SYM, w2) is None                              # vekil yok → bekler
    src.mark_proxy = lambda s, w: D("101") if w == w2 else None
    assert src.settlement_mark(SYM, w2) == D("101") and src.mark_basis(SYM, w2) == "BAR_OPEN_PROXY"
    led = FuturesLedgerV2(D("1000"), fees=FeeSchedule(maker_pct=D("0"), taker_pct=D("0")), slippage=SlippageModel.zero(),
                          funding=FundingSchedule(fallback_to_last_known=False))
    pos = _open(led, stop="50")
    led.tick({SYM: TickData(last=D("90"))}, now_utc=w2 + timedelta(minutes=1), funding_rate_lookup=src)
    rows = pos.features["funding_settlements"]
    assert [(r["mark_basis"], D(r["mark"])) for r in rows] == [("SETTLEMENT_ROW", D("100")), ("BAR_OPEN_PROXY", D("101"))]
    assert pos.funding_paid == D("0.1") + D("0.101")
    cov = src.coverage()["marks"]
    assert cov["bar_open_proxy"] >= 1 and cov["settlement_row"] >= 1
