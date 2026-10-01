"""Finansal aritmetik: Decimal + açık yuvarlama politikası + borsa tick/step kuantizasyonu.

Kurallar:
* Miktar (qty) borsa LOT_SIZE adımına AŞAĞI yuvarlanır (ROUND_DOWN) — fazla alım/satım olmasın.
* Fiyat: alış limit AŞAĞI, satış limit YUKARI (agresif olmayan yön) ya da açıkça `nearest`.
* Para tutarları (fee, pnl) 8 ondalıkta saklanır, gösterimde 2-6.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP, Decimal, getcontext
from enum import Enum
from typing import Union

getcontext().prec = 34

Number = Union[int, float, str, Decimal]
ZERO = Decimal("0")
ONE = Decimal("1")
MONEY_Q = Decimal("0.00000001")


class RoundingPolicy(str, Enum):
    DOWN = "DOWN"
    UP = "UP"
    NEAREST = "NEAREST"


_MODE = {RoundingPolicy.DOWN: ROUND_FLOOR, RoundingPolicy.UP: ROUND_CEILING, RoundingPolicy.NEAREST: ROUND_HALF_EVEN}


def D(x: Number | None, default: Decimal = ZERO) -> Decimal:
    """Güvenli Decimal dönüşümü. float → str(repr) üzerinden (ikili gürültü taşınmaz)."""
    if x is None:
        return default
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x))


def to_float(x: Decimal | None) -> float:
    return float(x) if x is not None else 0.0


def round_to_step(value: Number, step: Number, policy: RoundingPolicy = RoundingPolicy.DOWN) -> Decimal:
    """`value` değerini `step` katına yuvarlar. step<=0 ise değer değişmez."""
    v, s = D(value), D(step)
    if s <= 0:
        return v
    q = (v / s).to_integral_value(rounding=_MODE[policy])
    out = q * s
    # step'in ondalık hassasiyetine normalize et (0.30000000000000004 gibi kalıntı olmasın)
    return out.quantize(s.normalize()) if s.normalize().as_tuple().exponent < 0 else out.quantize(ONE)


def floor_to_step(value: Number, step: Number) -> Decimal:
    return round_to_step(value, step, RoundingPolicy.DOWN)


def ceil_to_step(value: Number, step: Number) -> Decimal:
    return round_to_step(value, step, RoundingPolicy.UP)


def quantize_qty(qty: Number, step: Number) -> Decimal:
    """Miktar her zaman aşağı yuvarlanır (Binance LOT_SIZE)."""
    return floor_to_step(qty, step)


def quantize_price(price: Number, tick: Number, side: str = "BUY", *, aggressive: bool = False) -> Decimal:
    """Fiyatı tick'e yuvarla. Varsayılan pasif yön: BUY → aşağı, SELL → yukarı. `aggressive=True` tersini yapar."""
    side = side.upper()
    down = (side == "BUY") != aggressive
    return round_to_step(price, tick, RoundingPolicy.DOWN if down else RoundingPolicy.UP)


def money(x: Number) -> Decimal:
    """Para tutarını 8 ondalığa sabitle (ledger saklama hassasiyeti)."""
    return D(x).quantize(MONEY_Q, rounding=ROUND_HALF_EVEN)


def pct_change(a: Number, b: Number) -> Decimal:
    """a'nın b'ye göre yüzde farkı (Decimal)."""
    a_, b_ = D(a), D(b)
    return (a_ / b_ - ONE) * Decimal(100) if b_ else ZERO


__all__ = ["D", "ZERO", "ONE", "RoundingPolicy", "round_to_step", "floor_to_step", "ceil_to_step", "quantize_qty",
           "quantize_price", "money", "pct_change", "to_float", "ROUND_DOWN", "ROUND_UP", "ROUND_HALF_UP"]
