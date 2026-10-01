"""Komisyon çizelgesi — maker/taker yüzdeleri, kaynak ve doğrulama zamanı.

Kural: komisyon HER ZAMAN gerçekleşen (fill) notional üzerinden alınır: `fee = fill_qty * fill_price * pct/100`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..core import D, ZERO
from .models import ser

_HUNDRED = Decimal("100")


@dataclass
class FeeSchedule:
    """Yüzde cinsinden komisyon (0.05 = %0.05). `bnb_discount` yalnızca bilgi amaçlı bayrak (uygulanmaz, açıkça verilmedikçe)."""
    maker_pct: Decimal = Decimal("0.02")
    taker_pct: Decimal = Decimal("0.05")
    source: str = "default"
    verified_at: str = ""
    effective_from: str = ""
    bnb_discount: bool = False
    market: str = "USDM_PERP"
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.maker_pct = D(self.maker_pct)
        self.taker_pct = D(self.taker_pct)

    # -- hesap
    def rate(self, is_maker: bool = False) -> Decimal:
        """Kesir olarak oran (0.0005)."""
        return (self.maker_pct if is_maker else self.taker_pct) / _HUNDRED

    def fee(self, notional, is_maker: bool = False) -> Decimal:
        """Fill notional (qty*price) üzerinden komisyon (USDT). Negatif notional mutlak değere çevrilir."""
        n = abs(D(notional))
        return n * self.rate(is_maker)

    def round_trip_rate(self, entry_maker: bool = False, exit_maker: bool = False) -> Decimal:
        return self.rate(entry_maker) + self.rate(exit_maker)

    # -- varsayılanlar
    @classmethod
    def spot_default(cls) -> "FeeSchedule":
        return cls(maker_pct=Decimal("0.10"), taker_pct=Decimal("0.10"), source="default_spot", market="SPOT")

    @classmethod
    def futures_default(cls) -> "FeeSchedule":
        return cls(maker_pct=Decimal("0.02"), taker_pct=Decimal("0.05"), source="default_futures", market="USDM_PERP")

    @classmethod
    def zero(cls) -> "FeeSchedule":
        return cls(maker_pct=ZERO, taker_pct=ZERO, source="zero")

    # -- (de)serileştirme
    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FeeSchedule":
        return cls(maker_pct=D(d.get("maker_pct", "0.02")), taker_pct=D(d.get("taker_pct", "0.05")),
                   source=str(d.get("source", "default")), verified_at=str(d.get("verified_at", "")),
                   effective_from=str(d.get("effective_from", "")), bnb_discount=bool(d.get("bnb_discount", False)),
                   market=str(d.get("market", "USDM_PERP")), meta=dict(d.get("meta") or {}))


__all__ = ["FeeSchedule"]
