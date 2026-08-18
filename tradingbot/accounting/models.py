"""Muhasebe veri modelleri (v2) — Decimal alanlar, `to_dict()` (Decimal → str) ve `from_dict()`.

Bütün para/miktar alanları Decimal'dır; JSON'a yazarken string'e çevrilir (kayıpsız), okurken geri Decimal.
Zamanlar ISO-8601 UTC string olarak saklanır (`+00:00`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal
from enum import Enum
from typing import Any

from ..core import D, ZERO, iso

SCHEMA_VERSION = 2


# ----------------------------------------------------------------------------- enums
class MarketType(str, Enum):
    SPOT = "SPOT"
    USDM_PERP = "USDM_PERP"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is PositionSide.LONG else -1

    @property
    def open_side(self) -> "Side":
        return Side.BUY if self is PositionSide.LONG else Side.SELL

    @property
    def close_side(self) -> "Side":
        return Side.SELL if self is PositionSide.LONG else Side.BUY


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    OCO = "OCO"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"


class AmountType(str, Enum):
    NOTIONAL = "NOTIONAL"      # amount = pozisyon büyüklüğü (USDT), marj = amount / kaldıraç
    MARGIN = "MARGIN"          # amount = yatırılan marj (USDT), notional = amount * kaldıraç
    QUANTITY = "QUANTITY"      # amount = baz varlık adedi


class MarginMode(str, Enum):
    ISOLATED = "ISOLATED"
    CROSS = "CROSS"


class LedgerKind(str, Enum):
    FEE = "FEE"
    PNL = "PNL"
    FUNDING = "FUNDING"
    TRANSFER = "TRANSFER"
    LIQ_FEE = "LIQ_FEE"
    TAX = "TAX"
    SLIPPAGE_INFO = "SLIPPAGE_INFO"


# ----------------------------------------------------------------------------- serialization helpers
def ser(obj: Any) -> Any:
    """Decimal → str, Enum → value, dataclass → dict, iç içe listeler/sözlükler."""
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):     # dataclass → alan alan (to_dict'ten önce: sonsuz özyineleme olmasın)
        return {f.name: ser(getattr(obj, f.name)) for f in fields(obj)}
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {str(k): ser(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [ser(x) for x in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def dec_or_none(x: Any) -> Decimal | None:
    if x is None or x == "":
        return None
    return D(x)


def _enum(cls, v, default=None):
    if v is None:
        return default
    if isinstance(v, cls):
        return v
    return cls(str(v))


def _pick(cls, d: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


# ----------------------------------------------------------------------------- market tick
@dataclass
class TickData:
    """Bir tik/bar özeti. `mark` yoksa `last` kullanılır; `high/low` varsa stop/TP tetikleri bar içi uçlarla kontrol edilir."""
    last: Decimal
    mark: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    ts: str = ""

    def __post_init__(self):
        self.last = D(self.last)
        for k in ("mark", "high", "low", "bid", "ask"):
            v = getattr(self, k)
            setattr(self, k, dec_or_none(v))

    @property
    def ref(self) -> Decimal:
        """Tetik/değerleme referansı: mark varsa mark, yoksa last."""
        return self.mark if self.mark is not None else self.last

    @property
    def hi(self) -> Decimal:
        return self.high if self.high is not None else self.ref

    @property
    def lo(self) -> Decimal:
        return self.low if self.low is not None else self.ref

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def coerce(cls, x: Any) -> "TickData":
        if isinstance(x, TickData):
            return x
        if isinstance(x, dict):
            return cls(**_pick(cls, x))
        return cls(last=D(x))


# ----------------------------------------------------------------------------- symbol filters / sizing
@dataclass
class SymbolFilters:
    symbol: str
    market_type: MarketType = MarketType.USDM_PERP
    price_tick: Decimal = Decimal("0.01")
    qty_step: Decimal = Decimal("0.001")
    min_qty: Decimal = Decimal("0.001")
    max_qty: Decimal = Decimal("1000000")
    market_max_qty: Decimal = Decimal("1000000")
    min_notional: Decimal = Decimal("5")
    max_leverage: int = 20
    verified_at: str = ""
    source: str = "default"

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SymbolFilters":
        d = _pick(cls, dict(d))
        d["market_type"] = _enum(MarketType, d.get("market_type"), MarketType.USDM_PERP)
        for k in ("price_tick", "qty_step", "min_qty", "max_qty", "market_max_qty", "min_notional"):
            if k in d:
                d[k] = D(d[k])
        d["max_leverage"] = int(d.get("max_leverage", 20))
        return cls(**d)


@dataclass
class SizeSpec:
    """Boyut tanımı. NOTIONAL: amount USDT pozisyon; MARGIN: amount USDT marj; QUANTITY: amount adet."""
    amount: Decimal
    amount_type: AmountType = AmountType.NOTIONAL
    leverage: int = 1

    def __post_init__(self):
        self.amount = D(self.amount)
        self.amount_type = _enum(AmountType, self.amount_type, AmountType.NOTIONAL)
        self.leverage = max(1, int(self.leverage))

    def target_notional(self, price: Decimal | None = None) -> Decimal:
        if self.amount_type is AmountType.NOTIONAL:
            return self.amount
        if self.amount_type is AmountType.MARGIN:
            return self.amount * self.leverage
        if price is None:
            raise ValueError("QUANTITY için fiyat gerekli")
        return self.amount * D(price)

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SizeSpec":
        return cls(amount=D(d["amount"]), amount_type=_enum(AmountType, d.get("amount_type"), AmountType.NOTIONAL),
                   leverage=int(d.get("leverage", 1)))


# ----------------------------------------------------------------------------- orders / fills / lots
@dataclass
class Fill:
    id: str
    order_id: str
    symbol: str
    side: Side
    qty: Decimal
    price: Decimal
    fee: Decimal = ZERO
    fee_asset: str = "USDT"
    is_maker: bool = False
    slippage: Decimal = ZERO          # ref fiyata göre USDT maliyeti (>=0 aleyhte)
    ref_price: Decimal | None = None
    ts: str = ""
    kind: str = ""                    # entry / tp1 / stop / liq / manual / ...

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Fill":
        d = _pick(cls, dict(d))
        d["side"] = _enum(Side, d.get("side"), Side.BUY)
        for k in ("qty", "price", "fee", "slippage"):
            d[k] = D(d.get(k, 0))
        d["ref_price"] = dec_or_none(d.get("ref_price"))
        return cls(**d)


@dataclass
class Order:
    id: str
    client_order_id: str
    symbol: str
    market_type: MarketType
    side: Side
    order_type: OrderType
    qty: Decimal = ZERO
    price: Decimal | None = None
    stop_price: Decimal | None = None
    tif: TimeInForce = TimeInForce.GTC
    status: OrderStatus = OrderStatus.CREATED
    filled_qty: Decimal = ZERO
    avg_fill_price: Decimal | None = None
    reduce_only: bool = False
    position_side: PositionSide | None = None
    created_at: str = field(default_factory=iso)
    updated_at: str = ""
    exchange_order_id: str = ""
    reject_reason: str = ""
    trailing_pct: Decimal | None = None
    oco_limit_price: Decimal | None = None
    fills: list[Fill] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def remaining(self) -> Decimal:
        return self.qty - self.filled_qty

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_REQUESTED)

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        d = _pick(cls, dict(d))
        d["market_type"] = _enum(MarketType, d.get("market_type"), MarketType.SPOT)
        d["side"] = _enum(Side, d.get("side"), Side.BUY)
        d["order_type"] = _enum(OrderType, d.get("order_type"), OrderType.MARKET)
        d["tif"] = _enum(TimeInForce, d.get("tif"), TimeInForce.GTC)
        d["status"] = _enum(OrderStatus, d.get("status"), OrderStatus.CREATED)
        d["position_side"] = _enum(PositionSide, d.get("position_side"))
        d["qty"] = D(d.get("qty", 0))
        d["filled_qty"] = D(d.get("filled_qty", 0))
        for k in ("price", "stop_price", "avg_fill_price", "trailing_pct", "oco_limit_price"):
            d[k] = dec_or_none(d.get(k))
        d["fills"] = [Fill.from_dict(f) for f in d.get("fills", [])]
        return cls(**d)


@dataclass
class Lot:
    fill_id: str
    qty: Decimal
    cost_basis: Decimal            # birim maliyet (fill fiyatı)
    ts: str = ""
    fee_per_unit: Decimal = ZERO   # alış ücreti (quote) / adet

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Lot":
        return cls(fill_id=str(d.get("fill_id", "")), qty=D(d["qty"]), cost_basis=D(d["cost_basis"]), ts=d.get("ts", ""),
                   fee_per_unit=D(d.get("fee_per_unit", 0)))


# ----------------------------------------------------------------------------- position
@dataclass
class Position:
    id: str
    symbol: str
    market_type: MarketType
    side: PositionSide
    qty: Decimal
    entry_avg: Decimal
    lots: list[Lot] = field(default_factory=list)
    leverage: int = 1
    margin_mode: MarginMode = MarginMode.ISOLATED
    isolated_margin: Decimal = ZERO
    realized_pnl: Decimal = ZERO          # net (ücret dahil, funding hariç)
    fees_paid: Decimal = ZERO
    funding_paid: Decimal = ZERO          # ödenen (>=0)
    funding_received: Decimal = ZERO      # alınan (>=0)
    opened_at: str = ""
    closed_at: str = ""
    status: str = "OPEN"                  # OPEN / CLOSED
    stop: Decimal | None = None
    targets: list[Decimal] = field(default_factory=list)
    targets_hit: int = 0
    tp1_done: bool = False
    initial_qty: Decimal = ZERO
    initial_stop: Decimal | None = None
    mae_pct: Decimal = ZERO
    mfe_pct: Decimal = ZERO
    bars_held: int = 0
    last_funding_settlement_utc: str = ""
    last_price: Decimal | None = None
    liquidation_price: Decimal | None = None
    entry_fee: Decimal = ZERO
    exit_fee: Decimal = ZERO
    slippage_cost: Decimal = ZERO
    requested_notional: Decimal = ZERO
    requested_margin: Decimal = ZERO
    amount_type: AmountType = AmountType.NOTIONAL
    setup_type: str = ""
    trigger_text: str = ""
    features: dict = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    trailing_pct: Decimal | None = None
    meta: dict = field(default_factory=dict)

    @property
    def notional(self) -> Decimal:
        return self.qty * self.entry_avg

    def unrealized(self, price: Decimal) -> Decimal:
        return (D(price) - self.entry_avg) * self.qty * self.side.sign

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        d = _pick(cls, dict(d))
        d["market_type"] = _enum(MarketType, d.get("market_type"), MarketType.USDM_PERP)
        d["side"] = _enum(PositionSide, d.get("side"), PositionSide.LONG)
        d["margin_mode"] = _enum(MarginMode, d.get("margin_mode"), MarginMode.ISOLATED)
        d["amount_type"] = _enum(AmountType, d.get("amount_type"), AmountType.NOTIONAL)
        for k in ("qty", "entry_avg", "isolated_margin", "realized_pnl", "fees_paid", "funding_paid", "funding_received",
                  "initial_qty", "mae_pct", "mfe_pct", "entry_fee", "exit_fee", "slippage_cost", "requested_notional",
                  "requested_margin"):
            d[k] = D(d.get(k, 0))
        for k in ("stop", "initial_stop", "last_price", "liquidation_price", "trailing_pct"):
            d[k] = dec_or_none(d.get(k))
        d["targets"] = [D(t) for t in d.get("targets", []) if t is not None]
        d["lots"] = [Lot.from_dict(x) for x in d.get("lots", [])]
        d["fills"] = [Fill.from_dict(x) for x in d.get("fills", [])]
        d["leverage"] = int(d.get("leverage", 1))
        d["bars_held"] = int(d.get("bars_held", 0))
        d["targets_hit"] = int(d.get("targets_hit", 0))
        return cls(**d)


# ----------------------------------------------------------------------------- ledger entry
@dataclass
class LedgerEntry:
    ts: str
    kind: LedgerKind
    amount: Decimal                 # cüzdana etkisi (+ gelir, − gider)
    ref_id: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LedgerEntry":
        return cls(ts=d.get("ts", ""), kind=_enum(LedgerKind, d.get("kind"), LedgerKind.TRANSFER), amount=D(d.get("amount", 0)),
                   ref_id=d.get("ref_id", ""), note=d.get("note", ""))


# ----------------------------------------------------------------------------- closed trade record
LEGACY_TRADE_KEYS = ("id", "symbol", "side", "entry", "exit_reason", "closed_at", "opened_at", "pnl", "fees", "funding",
                     "r_multiple", "mae_pct", "mfe_pct", "bars_held", "leverage", "setup_type", "trigger_text", "features", "tp1_done")


@dataclass
class TradeRecord:
    """Kapanan işlem kaydı — learning.py'nin kullandığı bütün eski anahtarlar + tam maliyet dökümü."""
    id: str
    symbol: str
    side: str
    entry: Decimal
    exit_reason: str
    closed_at: str
    opened_at: str
    pnl: Decimal                       # = net_pnl (funding dahil) — eski anlam korunur
    fees: Decimal = ZERO
    funding: Decimal = ZERO            # net funding etkisi (− ödendi, + alındı) — eski anlam
    r_multiple: Decimal = ZERO
    mae_pct: Decimal = ZERO
    mfe_pct: Decimal = ZERO
    bars_held: int = 0
    leverage: int = 1
    setup_type: str = ""
    trigger_text: str = ""
    features: dict = field(default_factory=dict)
    tp1_done: bool = False
    # --- yeni alanlar
    market_type: MarketType = MarketType.USDM_PERP
    amount_type: AmountType = AmountType.NOTIONAL
    requested_margin: Decimal = ZERO
    requested_notional: Decimal = ZERO
    effective_margin: Decimal = ZERO
    effective_notional: Decimal = ZERO
    quantity: Decimal = ZERO
    entry_fee: Decimal = ZERO
    exit_fee: Decimal = ZERO
    funding_paid: Decimal = ZERO
    funding_received: Decimal = ZERO
    slippage_cost: Decimal = ZERO
    spread_cost: Decimal = ZERO
    tax_estimate: Decimal = ZERO
    gross_pnl: Decimal = ZERO
    net_pnl: Decimal = ZERO
    exit_price: Decimal | None = None
    liquidation_price: Decimal | None = None
    fills: list[Fill] = field(default_factory=list)
    costs: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return ser(self)

    def to_legacy_dict(self) -> dict:
        """learning.py / eski tüketiciler için float değerli sözlük (Decimal → float)."""
        out = {}
        for k in LEGACY_TRADE_KEYS:
            v = getattr(self, k)
            out[k] = float(v) if isinstance(v, Decimal) else v
        out.update({"net_pnl": float(self.net_pnl), "gross_pnl": float(self.gross_pnl), "quantity": float(self.quantity),
                    "exit_price": float(self.exit_price) if self.exit_price is not None else None,
                    "market_type": self.market_type.value, "amount_type": self.amount_type.value})
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        d = _pick(cls, dict(d))
        d["market_type"] = _enum(MarketType, d.get("market_type"), MarketType.USDM_PERP)
        d["amount_type"] = _enum(AmountType, d.get("amount_type"), AmountType.NOTIONAL)
        for f in fields(cls):
            if f.name in d and f.type in ("Decimal", "Decimal | None"):
                d[f.name] = dec_or_none(d[f.name]) if f.type == "Decimal | None" else D(d[f.name] or 0)
        d.setdefault("pnl", ZERO)
        d["fills"] = [Fill.from_dict(x) for x in d.get("fills", [])]
        d["bars_held"] = int(d.get("bars_held", 0))
        d["leverage"] = int(d.get("leverage", 1))
        for k in ("id", "symbol", "side", "exit_reason", "closed_at", "opened_at"):
            d.setdefault(k, "")
        d.setdefault("entry", ZERO)
        return cls(**d)

    @classmethod
    def from_legacy(cls, rec: dict) -> "TradeRecord":
        """paper_futures.FuturesLedger history satırı → TradeRecord (eski anahtarlar aynen)."""
        pnl = D(rec.get("pnl", 0))
        fees = D(rec.get("fees", 0))
        funding = D(rec.get("funding", 0))
        return cls(id=str(rec.get("id", "")), symbol=rec.get("symbol", ""), side=rec.get("side", ""), entry=D(rec.get("entry", 0)),
                   exit_reason=rec.get("exit_reason", ""), closed_at=rec.get("closed_at", ""), opened_at=rec.get("opened_at", ""),
                   pnl=pnl, fees=fees, funding=funding, r_multiple=D(rec.get("r_multiple", 0)), mae_pct=D(rec.get("mae_pct", 0)),
                   mfe_pct=D(rec.get("mfe_pct", 0)), bars_held=int(rec.get("bars_held", 0)), leverage=int(rec.get("leverage", 1)),
                   setup_type=rec.get("setup_type", ""), trigger_text=rec.get("trigger_text", ""), features=dict(rec.get("features") or {}),
                   tp1_done=bool(rec.get("tp1_done", False)), net_pnl=pnl, gross_pnl=pnl + fees - funding,
                   funding_paid=max(-funding, ZERO), funding_received=max(funding, ZERO),
                   costs={"legacy": True, "fees": format(fees, "f"), "funding": format(funding, "f")})


__all__ = ["SCHEMA_VERSION", "MarketType", "Side", "PositionSide", "OrderType", "TimeInForce", "OrderStatus", "AmountType",
           "MarginMode", "LedgerKind", "TickData", "SymbolFilters", "SizeSpec", "Fill", "Order", "Lot", "Position", "LedgerEntry",
           "TradeRecord", "LEGACY_TRADE_KEYS", "ser", "dec_or_none"]
