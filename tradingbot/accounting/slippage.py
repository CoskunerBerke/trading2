"""Kayma (slippage) modeli — market emirlerde aleyhte fiyat sapması ve emir defteri VWAP tahmini."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from ..core import D, ZERO
from .models import Side, TickData, ser

_BPS = Decimal("10000")
_TWO = Decimal("2")


def _is_buy(side) -> bool:
    if isinstance(side, Side):
        return side is Side.BUY
    s = str(side).upper()
    return s in ("BUY", "LONG")


@dataclass
class SlippageModel:
    """`fixed_bps`: market emir için sabit aleyhte kayma (baz puan). `spread_half`: bid/ask varsa yarım spread eklenir."""
    fixed_bps: Decimal = ZERO
    spread_half: bool = False

    def __post_init__(self):
        self.fixed_bps = D(self.fixed_bps)

    @classmethod
    def zero(cls) -> "SlippageModel":
        return cls(fixed_bps=ZERO, spread_half=False)

    @classmethod
    def default(cls) -> "SlippageModel":
        """Eski paper_futures SLIP_PCT=%0.03 ≡ 3 bps."""
        return cls(fixed_bps=Decimal("3"), spread_half=False)

    @property
    def rate(self) -> Decimal:
        return self.fixed_bps / _BPS

    def spread_component(self, tick: TickData | None) -> Decimal:
        """Yarım spread (fiyat birimi); bid/ask yoksa 0."""
        if not self.spread_half or tick is None or tick.bid is None or tick.ask is None or tick.ask <= tick.bid:
            return ZERO
        return (tick.ask - tick.bid) / _TWO

    def fill_price(self, ref_price, side, tick: TickData | None = None, *, is_market: bool = True) -> Decimal:
        """Aleyhte kaydırılmış fill fiyatı. Limit (is_market=False) emirlerde kayma uygulanmaz."""
        ref = D(ref_price)
        if not is_market:
            return ref
        adj = ref * self.rate + self.spread_component(tick)
        return ref + adj if _is_buy(side) else max(ref - adj, ZERO)

    @staticmethod
    def cost(ref_price, fill_price, qty) -> Decimal:
        """Referansa göre kayma maliyeti (USDT, >=0)."""
        return abs(D(fill_price) - D(ref_price)) * D(qty)

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SlippageModel":
        return cls(fixed_bps=D(d.get("fixed_bps", 0)), spread_half=bool(d.get("spread_half", False)))


def vwap_estimate(levels: Sequence[tuple] | Iterable[tuple], qty) -> tuple[Decimal, Decimal]:
    """Emir defteri seviyeleri [(price, qty), ...] (en iyi fiyat önce) üzerinden `qty` için VWAP tahmini.
    Dönen: (vwap, doldurulan_qty). Defter yetmezse doldurulan_qty < qty."""
    need = D(qty)
    filled = ZERO
    cost = ZERO
    for price, avail in levels:
        if need <= 0:
            break
        take = min(D(avail), need)
        cost += take * D(price)
        filled += take
        need -= take
    if filled <= 0:
        return ZERO, ZERO
    return cost / filled, filled


__all__ = ["SlippageModel", "vwap_estimate"]
