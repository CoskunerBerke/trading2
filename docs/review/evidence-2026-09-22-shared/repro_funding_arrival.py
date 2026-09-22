# -*- coding: utf-8 -*-
"""SENTETİK — funding'in ULAŞMA ZAMANINA göre değişen maliyet (fiyat ve miktar vakaları), GERÇEK defter yolunda.

Kullanılan gerçek sınıflar: `FuturesLedgerV2` (open/tick/close_partial/close_manual/settle_late_funding),
`FundingSchedule.accrue` (tick içinden), `FundingRates` (bellek okuması + `refresh` ağ adımı). Sağlayıcı sentetiktir:
bir settlement satırı ancak `publish_ms`den sonra yayımlanır (Binance satırı settlement anında yayımlar; burada
gecikme ENJEKTE edilir). Ücret ve kayma sıfır. Fiyatlar, oranlar ve miktarlar görevdeki tanımlarla aynıdır.

Çalıştırma: `python docs/review/evidence-2026-09-22-shared/repro_funding_arrival.py` (depo kökünden).
Beklenen (doğru) sonuç: her vakada iki geliş zamanı AYNI funding'i verir (A: −0.100, B: −0.100).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tradingbot.accounting import FeeSchedule, SlippageModel, TickData  # noqa: E402
from tradingbot.accounting.funding import FundingSchedule  # noqa: E402
from tradingbot.accounting.futures_ledger import FuturesLedgerV2  # noqa: E402
from tradingbot.accounting.models import AmountType, SizeSpec  # noqa: E402
from tradingbot.pattern_trader.funding import FundingRates  # noqa: E402

UTC = timezone.utc
SYM = "TEST/USDT"
T0759 = datetime(2026, 9, 22, 7, 59, tzinfo=UTC)
T0800 = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
T0801 = datetime(2026, 9, 22, 8, 1, tzinfo=UTC)
T0802 = datetime(2026, 9, 22, 8, 2, tzinfo=UTC)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class SynthProvider:
    """`/fapi/v1/fundingRate` biçiminde satır; satır `publish_ms`den önce YOKTUR (ulaşma zamanı enjekte edilir)."""

    def __init__(self, rows: list[dict], clock):
        self.rows, self.clock = rows, clock

    def funding_history(self, symbol, limit=1000, start_ms=None, end_ms=None):
        now = self.clock()
        return [dict(r) for r in self.rows if r["publish_ms"] <= now and (start_ms is None or r["funding_ts"] >= start_ms)
                and (end_ms is None or r["funding_ts"] <= end_ms)]

    def funding_info(self):
        return []                      # sapan sembol yok → varsayılan 8 saat DOĞRULANMIŞ


def make(publish_at: datetime, stop):
    clock = {"ms": ms(T0759)}
    prov = SynthProvider([{"symbol": "TESTUSDT", "funding_ts": ms(T0800), "rate": 0.001, "mark": 100.0,
                           "publish_ms": ms(publish_at)}], lambda: clock["ms"])
    rates = FundingRates(prov, clock_ms=lambda: clock["ms"], rate_ttl_s=30.0)   # 08:02 çekimi gerçekten ağa çıksın
    led = FuturesLedgerV2(Decimal("1000"), fees=FeeSchedule(maker_pct=Decimal("0"), taker_pct=Decimal("0")),
                          slippage=SlippageModel.zero(), funding=FundingSchedule(fallback_to_last_known=False,
                                                                                 hours_for_symbol=rates.hours_for))
    pos = led.open(SYM, "LONG", Decimal("100"), SizeSpec(Decimal("1"), AmountType.QUANTITY, 1), stop=Decimal(str(stop)), now=T0759)
    assert pos is not None, led.last_reject_reason
    return led, rates, clock


def step(rates, clock, at: datetime):
    """Tarayıcı adımı: ağ (`refresh`) — defter kilidi dışında, çıkış yolundan ayrı."""
    clock["ms"] = ms(at)
    rates.refresh([SYM], now_ms=ms(at))


def funding_source(rates):
    # Üretim çağrı biçimi (bu teslimde): kaynak nesnesi (oran + settlement mark). Eski sürümde yalnız `lookup` vardı.
    return rates if callable(rates) and hasattr(rates, "settlement_mark") else rates.lookup


def case_a(publish_at: datetime) -> dict:
    led, rates, clock = make(publish_at, stop=95)
    step(rates, clock, T0801)
    led.tick({SYM: TickData(last=Decimal("94"))}, now_utc=T0801, funding_rate_lookup=funding_source(rates))
    step(rates, clock, T0802)
    led.settle_late_funding(rates.lookup, now=T0802, mark_for=rates.settlement_mark, hours_for=rates.hours_for)
    rec = led.history[-1]
    return {"funding": rec.funding, "net": rec.net_pnl, "exit": rec.exit_reason, "wallet": led.wallet_balance,
            "total_funding": led.total_funding}


def case_b(publish_at: datetime) -> dict:
    led, rates, clock = make(publish_at, stop=50)
    step(rates, clock, T0801)
    led.tick({SYM: TickData(last=Decimal("100"))}, now_utc=T0801, funding_rate_lookup=funding_source(rates))
    led.close_partial(SYM, Decimal("100"), Decimal("0.5"), now=T0801)
    step(rates, clock, T0802)
    led.tick({SYM: TickData(last=Decimal("100"))}, now_utc=T0802, funding_rate_lookup=funding_source(rates))
    led.close_manual(SYM, Decimal("100"), now=T0802)
    led.settle_late_funding(rates.lookup, now=T0802, mark_for=rates.settlement_mark, hours_for=rates.hours_for)
    rec = led.history[-1]
    return {"funding": rec.funding, "net": rec.net_pnl, "exit": rec.exit_reason, "wallet": led.wallet_balance,
            "total_funding": led.total_funding}


def main() -> int:
    bad = 0
    for name, fn in (("A (fiyat)", case_a), ("B (miktar)", case_b)):
        early = fn(T0800)                  # oran settlement anında yayımlandı → kapanıştan ÖNCE biliniyor
        late = fn(T0802)                   # oran kapanıştan SONRA geldi
        same = early["funding"] == late["funding"] and early["net"] == late["net"]
        bad += int(not same) + int(early["funding"] != Decimal("-0.1"))
        print("%s  erken: funding=%s net=%s çıkış=%s cüzdan=%s | geç: funding=%s net=%s cüzdan=%s | aynı=%s"
              % (name, early["funding"], early["net"], early["exit"], early["wallet"], late["funding"], late["net"],
                 late["wallet"], same))
    print("SONUÇ:", "TUTARLI" if bad == 0 else "TUTARSIZ (%d)" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
