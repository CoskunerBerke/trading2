"""Funding tahakkuku (USDⓈ-M perpetual) — 00/08/16 UTC settlement'larına hizalı, kaçırılan HER dönem ayrı ayrı uygulanır.

* rate > 0 → LONG öder, SHORT alır; rate < 0 → tersi.
* tutar = qty * mark * rate  (settlement anındaki mark; elimizde yoksa verilen mark)
* rate_lookup(symbol, settlement_dt) → Decimal|None. None dönerse son bilinen oran kullanılır ve olay `estimated=True` işaretlenir.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Mapping

from ..core import D, ZERO, FUNDING_HOURS_UTC, from_iso, funding_settlements_between, iso
from .models import Position, PositionSide, dec_or_none, ser

RateLookup = Callable[[str, datetime], "Decimal | float | str | None"]


@dataclass
class FundingEvent:
    ts: str
    symbol: str
    side: str
    rate: Decimal
    mark: Decimal
    qty: Decimal
    amount: Decimal          # cüzdana etkisi: + alındı, − ödendi
    estimated: bool = False

    def to_dict(self) -> dict:
        return ser(self)


def static_rates(rates: Mapping[str, "Decimal | float | str"]) -> RateLookup:
    """Sembol → sabit oran sözlüğünden lookup üretir (engine'in mevcut funding dict'i için)."""
    def _lookup(symbol: str, _when: datetime):
        v = rates.get(symbol)
        return None if v is None else D(v)
    return _lookup


@dataclass
class FundingSchedule:
    hours_utc: tuple[int, ...] = FUNDING_HOURS_UTC
    fallback_to_last_known: bool = True

    def settlements_due(self, position: Position, now_utc: datetime) -> list[datetime]:
        start_s = position.last_funding_settlement_utc or position.opened_at
        if not start_s:
            return []
        return funding_settlements_between(from_iso(start_s), now_utc, self.hours_utc)

    def accrue(self, position: Position, now_utc: datetime, mark_price, rate_lookup: RateLookup | None) -> list[FundingEvent]:
        """(last_settlement, now] aralığındaki bütün settlement'ları uygular; pozisyonun funding_paid/received alanlarını günceller.
        Dönen olayların `amount` toplamı cüzdana eklenmelidir (çağıran ledger yapar)."""
        due = self.settlements_due(position, now_utc)
        if not due:
            return []
        mark = D(mark_price)
        last_rate = dec_or_none(position.meta.get("last_funding_rate"))
        events: list[FundingEvent] = []
        settled_until: datetime | None = None
        for t in due:
            raw = rate_lookup(position.symbol, t) if rate_lookup is not None else None
            estimated = False
            if raw is None:
                if last_rate is None or not self.fallback_to_last_known:
                    break              # oran bilinmiyor → bu ve sonraki dönemler BEKLER (sessiz kayıp yok); watermark ileri sarılmaz
                rate, estimated = last_rate, True
            else:
                rate = D(raw)
            if rate == ZERO or position.qty <= 0:
                last_rate, settled_until = rate, t
                continue
            pay = position.qty * mark * rate           # >0: long öder
            amount = -pay if position.side is PositionSide.LONG else pay
            if amount < 0:
                position.funding_paid += -amount
            else:
                position.funding_received += amount
            events.append(FundingEvent(ts=iso(t), symbol=position.symbol, side=position.side.value, rate=rate, mark=mark,
                                       qty=position.qty, amount=amount, estimated=estimated))
            last_rate, settled_until = rate, t
        if settled_until is not None:
            position.last_funding_settlement_utc = iso(settled_until)
        if last_rate is not None:
            position.meta["last_funding_rate"] = format(last_rate, "f")
        return events


__all__ = ["FundingEvent", "FundingSchedule", "RateLookup", "static_rates"]
