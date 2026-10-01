"""Vergi tahmini — VARSAYILAN KAPALI. Sürümlü politika; yalnızca `enabled=True` VE `manually_confirmed=True` ise tahmin üretir.

Bu modül vergi danışmanlığı değildir; yalnızca defter dışa aktarımı ve senaryo tahmini içindir.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from ..core import D, ZERO, atomic_write_text, from_iso, istanbul
from .models import TradeRecord, ser

TAX_STATUS_UNVERIFIED = "UNVERIFIED_OR_NOT_EFFECTIVE"
TAX_STATUS_CONFIRMED = "MANUALLY_CONFIRMED"
_HUNDRED = Decimal("100")


@dataclass
class TaxPolicy:
    version: str = "TR-crypto-v0-draft"
    jurisdiction: str = "TR"
    enabled: bool = False
    manually_confirmed: bool = False
    rate_pct: Decimal = ZERO
    status: str = TAX_STATUS_UNVERIFIED
    apply_to: str = "positive_net_pnl"      # positive_net_pnl | all_net_pnl
    note: str = "Yürürlükte olmayan/doğrulanmamış varsayım. Tahmin=0."
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.rate_pct = D(self.rate_pct)
        if self.is_active and self.status == TAX_STATUS_UNVERIFIED:
            self.status = TAX_STATUS_CONFIRMED

    @property
    def is_active(self) -> bool:
        return bool(self.enabled and self.manually_confirmed and self.rate_pct > 0)

    def estimate(self, net_pnl) -> Decimal:
        """Tahmini vergi (USDT). Aktif değilse 0."""
        if not self.is_active:
            return ZERO
        base = D(net_pnl)
        if self.apply_to == "positive_net_pnl":
            base = max(base, ZERO)
        return base * self.rate_pct / _HUNDRED

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TaxPolicy":
        return cls(version=str(d.get("version", "TR-crypto-v0-draft")), jurisdiction=str(d.get("jurisdiction", "TR")),
                   enabled=bool(d.get("enabled", False)), manually_confirmed=bool(d.get("manually_confirmed", False)),
                   rate_pct=D(d.get("rate_pct", 0)), status=str(d.get("status", TAX_STATUS_UNVERIFIED)),
                   apply_to=str(d.get("apply_to", "positive_net_pnl")), note=str(d.get("note", "")), meta=dict(d.get("meta") or {}))

    @classmethod
    def disabled(cls) -> "TaxPolicy":
        return cls()


@dataclass
class TaxLedgerRow:
    trade_id: str
    symbol: str
    market_type: str
    side: str
    opened_at_utc: str
    timestamp_utc: str
    timestamp_istanbul: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal | None
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal
    fifo_lots: list[dict] = field(default_factory=list)
    tax_estimate: Decimal = ZERO
    tax_policy_version: str = ""
    tax_status: str = TAX_STATUS_UNVERIFIED

    def to_dict(self) -> dict:
        return ser(self)


def _ist(ts: str) -> str:
    try:
        return istanbul(from_iso(ts), "%Y-%m-%d %H:%M:%S") if ts else ""
    except ValueError:
        return ""


def tax_row(rec: TradeRecord, policy: TaxPolicy) -> TaxLedgerRow:
    lots = rec.costs.get("fifo_lots") if isinstance(rec.costs, dict) else None
    return TaxLedgerRow(trade_id=rec.id, symbol=rec.symbol, market_type=rec.market_type.value, side=rec.side,
                        opened_at_utc=rec.opened_at, timestamp_utc=rec.closed_at, timestamp_istanbul=_ist(rec.closed_at),
                        quantity=rec.quantity, entry_price=rec.entry, exit_price=rec.exit_price, gross_pnl=rec.gross_pnl,
                        fees=rec.fees, funding=rec.funding, net_pnl=rec.net_pnl, fifo_lots=list(lots or []),
                        tax_estimate=policy.estimate(rec.net_pnl), tax_policy_version=policy.version, tax_status=policy.status)


def tax_rows(records: Iterable[TradeRecord], policy: TaxPolicy) -> list[TaxLedgerRow]:
    return [tax_row(r, policy) for r in records]


def export_tax_csv(rows: Iterable[TaxLedgerRow], path: Path | str) -> Path:
    rows = list(rows)
    cols = ["trade_id", "symbol", "market_type", "side", "opened_at_utc", "timestamp_utc", "timestamp_istanbul", "quantity",
            "entry_price", "exit_price", "gross_pnl", "fees", "funding", "net_pnl", "fifo_lots", "tax_estimate",
            "tax_policy_version", "tax_status"]
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in rows:
        d = r.to_dict()
        d["fifo_lots"] = ";".join(f"{l.get('qty')}@{l.get('cost_basis')}" for l in r.fifo_lots) if r.fifo_lots else ""
        w.writerow({k: d.get(k, "") for k in cols})
    p = Path(path)
    atomic_write_text(p, buf.getvalue())
    return p


__all__ = ["TAX_STATUS_UNVERIFIED", "TAX_STATUS_CONFIRMED", "TaxPolicy", "TaxLedgerRow", "tax_row", "tax_rows", "export_tax_csv"]
