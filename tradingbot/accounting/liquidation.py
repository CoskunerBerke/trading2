"""Likidasyon (izole marj, USDⓈ-M, tek yön).

liq_long  = (entry*qty − margin + maint_amount − fee_cushion) / (qty * (1 − mmr))
liq_short = (entry*qty + margin − maint_amount + fee_cushion) / (qty * (1 + mmr))

Likidasyon gerçekleşince: likidasyon ücreti (varsayılan %0.5 × liq notional) alınır ve toplam kayıp izole marj ile sınırlanır
(izole modda cüzdanın kalanı etkilenmez).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..core import D, ZERO
from .filters import LeverageBracket, bracket_for
from .models import Position, PositionSide, ser

_ONE = Decimal("1")
_HUNDRED = Decimal("100")


@dataclass
class LiquidationParams:
    liq_fee_pct: Decimal = Decimal("0.5")      # liq notional yüzdesi
    fee_cushion_pct: Decimal = ZERO            # kapanış komisyonu için likidasyon fiyatına eklenen tampon (% notional)
    use_brackets: bool = True                  # False → mmr = 0, maint_amount = 0 (basit model)

    def __post_init__(self):
        self.liq_fee_pct = D(self.liq_fee_pct)
        self.fee_cushion_pct = D(self.fee_cushion_pct)

    def to_dict(self) -> dict:
        return ser(self)


def liquidation_price(side: PositionSide | str, entry, qty, margin, mmr=ZERO, maint_amount=ZERO, fee_cushion=ZERO) -> Decimal:
    """İzole marj likidasyon fiyatı. qty<=0 ise entry döner."""
    side = PositionSide(str(side.value if isinstance(side, PositionSide) else side).upper())
    e, q, m, r, ma, fc = D(entry), D(qty), D(margin), D(mmr), D(maint_amount), D(fee_cushion)
    if q <= 0:
        return e
    if side is PositionSide.LONG:
        px = (e * q - m + ma - fc) / (q * (_ONE - r))
        return max(px, ZERO)
    return (e * q + m - ma + fc) / (q * (_ONE + r))


def simple_liq(side: PositionSide | str, entry, leverage: int, mmr=ZERO) -> Decimal:
    """Marj = notional/kaldıraç, maint_amount=0 varsayımıyla kısa yol (qty'den bağımsız)."""
    e, lev, r = D(entry), max(1, int(leverage)), D(mmr)
    side_e = PositionSide(str(side.value if isinstance(side, PositionSide) else side).upper())
    if side_e is PositionSide.LONG:
        return e * (_ONE - _ONE / lev) / (_ONE - r)
    return e * (_ONE + _ONE / lev) / (_ONE + r)


def liquidation_price_for(position: Position, brackets: list[LeverageBracket] | None = None,
                          params: LiquidationParams | None = None) -> Decimal:
    """Pozisyonun bracket'ına göre likidasyon fiyatı."""
    params = params or LiquidationParams()
    notional = position.qty * position.entry_avg
    if params.use_brackets:
        b = bracket_for(notional, brackets)
        mmr, maint = b.mmr, b.maint_amount
    else:
        mmr, maint = ZERO, ZERO
    cushion = notional * params.fee_cushion_pct / _HUNDRED
    return liquidation_price(position.side, position.entry_avg, position.qty, position.isolated_margin, mmr, maint, cushion)


def liquidation_buffer_pct(side: PositionSide | str, price, liq_price) -> Decimal:
    """Fiyattan likidasyona uzaklık (%). Pozitif = güvenli taraf; negatif = likidasyon fiyatı aşıldı."""
    p, l = D(price), D(liq_price)
    if p <= 0:
        return ZERO
    side_e = PositionSide(str(side.value if isinstance(side, PositionSide) else side).upper())
    return (p - l) / p * _HUNDRED if side_e is PositionSide.LONG else (l - p) / p * _HUNDRED


def is_liquidated(side: PositionSide | str, worst_price, liq_price) -> bool:
    side_e = PositionSide(str(side.value if isinstance(side, PositionSide) else side).upper())
    w, l = D(worst_price), D(liq_price)
    return w <= l if side_e is PositionSide.LONG else w >= l


def liquidation_outcome(position: Position, liq_price, params: LiquidationParams | None = None) -> tuple[Decimal, Decimal, bool]:
    """Likidasyon kapanışının sonucu: (gross_pnl, liq_fee, clamped).
    Toplam kayıp (−gross + liq_fee) izole marjı aşamaz; aşarsa gross buna göre kırpılır."""
    params = params or LiquidationParams()
    lp = D(liq_price)
    liq_notional = position.qty * lp
    fee = liq_notional * params.liq_fee_pct / _HUNDRED
    fee = min(fee, position.isolated_margin)
    gross = (lp - position.entry_avg) * position.qty * position.side.sign
    clamped = False
    if -gross + fee > position.isolated_margin:
        gross = -(position.isolated_margin - fee)
        clamped = True
    return gross, fee, clamped


__all__ = ["LiquidationParams", "liquidation_price", "simple_liq", "liquidation_price_for", "liquidation_buffer_pct",
           "is_liquidated", "liquidation_outcome"]
