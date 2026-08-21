"""SpotLedger — Decimal kağıt spot defteri: nakit/varlık/kilitli bakiyeler, FIFO lotlar, emir tipleri, komisyon, kayma.

* Emirler: MARKET, LIMIT, STOP_MARKET, STOP_LIMIT, OCO (limit TP + stop), trailing stop (STOP_MARKET + trailing_pct).
* Açığa satış YOK: SELL yalnızca serbest varlık kadar (aksi hâlde NO_SHORTING / INSUFFICIENT_ASSET reddi).
* Fill: komisyon fill notional üzerinden; BUY'da nakitten, SELL'de hasılattan düşülür (fee_asset=quote).
* Gerçekleşen PnL: FIFO — hasılat − maliyet − alış ücreti payı − satış ücreti (net). Gerçekleşmemiş: tahmini çıkış ücreti düşülmüş.
* Kilit: LIMIT BUY → nakit (qty*price*(1+taker)); SELL tipleri → varlık.
* Stop tetikleri: bar high/low varsa onlarla; gap-through: last stop'un ötesindeyse last'tan (kötü) doldurulur.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

from ..core import D, ZERO, StorageError, atomic_write_json, iso, quantize_price, quantize_qty, read_json, utc_now
from .fees import FeeSchedule
from .filters import default_filters, quantize_order
from .models import (
    SCHEMA_VERSION,
    AmountType,
    Fill,
    LedgerEntry,
    LedgerKind,
    Lot,
    MarketType,
    Order,
    OrderStatus,
    OrderType,
    Side,
    SymbolFilters,
    TickData,
    TimeInForce,
    TradeRecord,
    ser,
)
from .slippage import SlippageModel
from .tax import TaxPolicy

log = logging.getLogger(__name__)

_ONE = Decimal("1")
_HUNDRED = Decimal("100")

R_OK = "OK"
R_NO_SHORTING = "NO_SHORTING"
R_INSUFFICIENT_ASSET = "INSUFFICIENT_ASSET"
R_INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
R_BAD_PRICE = "BAD_PRICE"
R_UNSUPPORTED = "UNSUPPORTED_ORDER"
R_NO_PRICE = "NO_MARKET_PRICE"

_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TRY", "BTC", "ETH", "BNB", "EUR")


def split_symbol(symbol: str, quote_hint: str = "USDT") -> tuple[str, str]:
    """'ETH/USDT' → ('ETH','USDT'); 'ETHUSDT' → ('ETH','USDT')."""
    if "/" in symbol:
        b, q = symbol.split("/", 1)
        return b.upper(), q.upper()
    s = symbol.upper()
    for suf in (quote_hint.upper(),) + _QUOTE_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)], suf
    return s, quote_hint.upper()


class SpotLedger:
    schema_version = SCHEMA_VERSION

    def __init__(self, starting_cash, *, quote_asset: str = "USDT", fees: FeeSchedule | None = None,
                 slippage: SlippageModel | None = None, filters_lookup: Callable[[str], SymbolFilters] | None = None,
                 tax_policy: TaxPolicy | None = None, entries_keep: int = 2000, history_keep: int = 5000, orders_keep: int = 500):
        self.quote_asset = quote_asset.upper()
        self.starting_equity: Decimal = D(starting_cash)
        self.cash: Decimal = D(starting_cash)
        self.locked_cash: Decimal = ZERO
        self.assets: dict[str, Decimal] = {}
        self.locked_assets: dict[str, Decimal] = {}
        self.lots: dict[str, list[Lot]] = {}
        self.open_orders: dict[str, Order] = {}
        self.closed_orders: list[Order] = []
        self.history: list[TradeRecord] = []
        self.entries: list[LedgerEntry] = []
        self.position_meta: dict[str, dict] = {}       # symbol → {strategy, entry_time}
        self.fees = fees or FeeSchedule.spot_default()
        self.slippage = slippage or SlippageModel.zero()
        self.filters_lookup = filters_lookup
        self.tax_policy = tax_policy or TaxPolicy.disabled()
        self.entries_keep, self.history_keep, self.orders_keep = int(entries_keep), int(history_keep), int(orders_keep)
        self.total_fees: Decimal = ZERO
        self.seq: int = 0
        self.updated_at: str = ""
        self.last_reject_reason: str = ""
        self.meta: dict = {}

    # ------------------------------------------------------------------ yardımcılar
    def _filters(self, symbol: str, filters: SymbolFilters | None) -> SymbolFilters:
        if filters is not None:
            return filters
        if self.filters_lookup is not None:
            f = self.filters_lookup(symbol)
            if f is not None:
                return f
        return default_filters(symbol, MarketType.SPOT)

    def _entry(self, kind: LedgerKind, amount: Decimal, ref_id: str = "", note: str = "", ts: str | None = None) -> None:
        self.entries.append(LedgerEntry(ts=ts or iso(), kind=kind, amount=amount, ref_id=ref_id, note=note))
        if len(self.entries) > self.entries_keep:
            self.entries = self.entries[-self.entries_keep:]

    def free_asset(self, asset: str) -> Decimal:
        return self.assets.get(asset.upper(), ZERO)

    def total_asset(self, asset: str) -> Decimal:
        a = asset.upper()
        return self.assets.get(a, ZERO) + self.locked_assets.get(a, ZERO)

    def qty(self, symbol: str) -> Decimal:
        base, _ = split_symbol(symbol, self.quote_asset)
        return self.total_asset(base)

    def avg_cost(self, symbol: str) -> Decimal | None:
        lots = self.lots.get(symbol) or []
        q = sum((l.qty for l in lots), ZERO)
        return (sum((l.qty * l.cost_basis for l in lots), ZERO) / q) if q > 0 else None

    def _reject(self, order: Order, reason: str, detail: str = "") -> Order:
        order.status = OrderStatus.REJECTED
        order.reject_reason = reason
        order.updated_at = iso()
        order.events.append({"ts": order.updated_at, "status": reason, "note": detail})
        self.last_reject_reason = reason
        self._archive(order)
        log.info("spot emir reddedildi %s %s %s", order.symbol, reason, detail)
        return order

    def _archive(self, order: Order) -> None:
        self.open_orders.pop(order.id, None)
        self.closed_orders.append(order)
        if len(self.closed_orders) > self.orders_keep:
            self.closed_orders = self.closed_orders[-self.orders_keep:]

    # ------------------------------------------------------------------ emir oluşturma
    def place_order(self, symbol: str, side, order_type=OrderType.MARKET, *, qty=None, quote_amount=None, price=None,
                    stop_price=None, limit_price=None, trailing_pct=None, oco_stop_price=None, oco_stop_limit_price=None,
                    tif=TimeInForce.GTC, tick: TickData | None = None, ref_price=None, filters: SymbolFilters | None = None,
                    strategy: str = "", client_order_id: str = "", now: datetime | None = None, meta: dict | None = None) -> Order:
        """Emir ver. MARKET anında dolar (tick/ref_price gerekli); diğerleri açık emir olarak bekler (ACKNOWLEDGED)."""
        self.last_reject_reason = ""
        side_e = Side(str(side).upper()) if not isinstance(side, Side) else side
        otype = OrderType(str(order_type).upper()) if not isinstance(order_type, OrderType) else order_type
        now = now or utc_now()
        ts = iso(now)
        self.seq += 1
        oid = f"S{self.seq:06d}"
        f = self._filters(symbol, filters)
        td = TickData.coerce(tick) if tick is not None else (TickData(last=D(ref_price)) if ref_price is not None else None)
        order = Order(id=oid, client_order_id=client_order_id or oid, symbol=symbol, market_type=MarketType.SPOT, side=side_e,
                      order_type=otype, tif=tif if isinstance(tif, TimeInForce) else TimeInForce(str(tif).upper()),
                      created_at=ts, updated_at=ts, meta=dict(meta or {}))
        if strategy:
            order.meta["strategy"] = strategy
        # referans fiyat
        if otype is OrderType.MARKET:
            if td is None:
                return self._reject(order, R_NO_PRICE, symbol)
            ref = (td.ask if side_e is Side.BUY else td.bid) or td.last
        elif otype is OrderType.LIMIT:
            if price is None or D(price) <= 0:
                return self._reject(order, R_BAD_PRICE, "limit fiyatı gerekli")
            ref = D(price)
        elif otype in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT):
            if stop_price is None or D(stop_price) <= 0:
                if trailing_pct is None:
                    return self._reject(order, R_BAD_PRICE, "stop fiyatı gerekli")
            ref = D(stop_price) if stop_price is not None else (td.ref if td is not None else None)
            if ref is None:
                return self._reject(order, R_NO_PRICE, "trailing için fiyat gerekli")
            if otype is OrderType.STOP_LIMIT:
                if limit_price is None or D(limit_price) <= 0:
                    return self._reject(order, R_BAD_PRICE, "stop-limit için limit fiyatı gerekli")
                order.price = quantize_price(limit_price, f.price_tick, side_e.value)
        elif otype is OrderType.OCO:
            if side_e is not Side.SELL:
                return self._reject(order, R_UNSUPPORTED, "OCO yalnızca SELL")
            if price is None or oco_stop_price is None:
                return self._reject(order, R_BAD_PRICE, "OCO: price (TP) ve oco_stop_price gerekli")
            ref = D(price)
            order.stop_price = quantize_price(oco_stop_price, f.price_tick, "SELL")
            order.oco_limit_price = quantize_price(oco_stop_limit_price, f.price_tick, "SELL") if oco_stop_limit_price is not None else None
        else:
            return self._reject(order, R_UNSUPPORTED, otype.value)
        # miktar
        if qty is None:
            if quote_amount is None or side_e is not Side.BUY:
                return self._reject(order, R_BAD_PRICE, "qty ya da (BUY için) quote_amount gerekli")
            taker = self.fees.rate(False)
            raw_qty = D(quote_amount) / (ref * (_ONE + taker))
        else:
            raw_qty = D(qty)
        q, p, ok, why = quantize_order(f, raw_qty, ref, side_e, market=otype is OrderType.MARKET)
        if not ok:
            return self._reject(order, why, f"{raw_qty}@{ref}")
        order.qty = q
        if otype is OrderType.LIMIT or otype is OrderType.OCO:
            order.price = quantize_price(price, f.price_tick, side_e.value)
        if otype in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT):
            if stop_price is not None:
                order.stop_price = quantize_price(stop_price, f.price_tick, side_e.value)
            if trailing_pct is not None:
                order.trailing_pct = D(trailing_pct)
                if order.stop_price is None and td is not None:
                    tp = order.trailing_pct / _HUNDRED
                    order.stop_price = td.ref * (_ONE - tp) if side_e is Side.SELL else td.ref * (_ONE + tp)
        order.meta["filters"] = f.to_dict()
        base, _ = split_symbol(symbol, self.quote_asset)
        # bakiye / kilit
        if side_e is Side.SELL:
            free = self.free_asset(base)
            if free <= 0:
                return self._reject(order, R_NO_SHORTING, f"{base} yok (açığa satış yok)")
            if q > free:
                return self._reject(order, R_INSUFFICIENT_ASSET, f"{q} > {free}")
        else:
            lock_px = ref if otype is OrderType.MARKET else (order.price if order.price is not None else (order.stop_price or ref))
            need = q * lock_px * (_ONE + self.fees.rate(False))
            if need > self.cash:
                return self._reject(order, R_INSUFFICIENT_CASH, f"{need} > {self.cash}")
        return self.submit(order, td, now=now)

    def submit(self, order: Order, tick: TickData | None = None, *, now: datetime | None = None) -> Order:
        """Hazır bir Order nesnesini deftere al (PaperGateway kullanır). Kilitler ve gerekiyorsa hemen doldurur."""
        now = now or utc_now()
        ts = iso(now)
        base, _ = split_symbol(order.symbol, self.quote_asset)
        if order.id not in self.open_orders and order.status in (OrderStatus.CREATED, OrderStatus.RISK_APPROVED, OrderStatus.SUBMITTING):
            # kilitle
            if order.side is Side.SELL:
                self.assets[base] = self.free_asset(base) - order.qty
                self.locked_assets[base] = self.locked_assets.get(base, ZERO) + order.qty
            else:
                lock_px = order.price if order.price is not None else (order.stop_price or (tick.ref if tick is not None else ZERO))
                lock = order.qty * lock_px * (_ONE + self.fees.rate(False))
                self.cash -= lock
                self.locked_cash += lock
                order.meta["locked_cash"] = format(lock, "f")
            order.status = OrderStatus.ACKNOWLEDGED
            order.updated_at = ts
            order.events.append({"ts": ts, "status": "ACKNOWLEDGED"})
            self.open_orders[order.id] = order
        if tick is not None:
            self._try_fill(order, tick, ts, at_placement=True)
        return order

    def cancel_order(self, order_id: str, now: datetime | None = None) -> Order | None:
        order = self.open_orders.get(order_id)
        if order is None:
            return None
        self._release_locks(order)
        order.status = OrderStatus.CANCELED
        order.updated_at = iso(now or utc_now())
        order.events.append({"ts": order.updated_at, "status": "CANCELED"})
        self._archive(order)
        return order

    def _release_locks(self, order: Order) -> None:
        base, _ = split_symbol(order.symbol, self.quote_asset)
        rem = order.remaining
        if order.side is Side.SELL:
            self.locked_assets[base] = max(self.locked_assets.get(base, ZERO) - rem, ZERO)
            self.assets[base] = self.free_asset(base) + rem
        else:
            lock = D(order.meta.get("locked_cash", 0))
            self.locked_cash -= lock
            self.cash += lock
            order.meta["locked_cash"] = "0"

    # ------------------------------------------------------------------ doldurma
    def _try_fill(self, order: Order, td: TickData, ts: str, *, at_placement: bool = False) -> bool:
        if not order.is_open:
            return False
        ot, sd = order.order_type, order.side
        last, hi, lo = td.last, td.hi, td.lo
        filled = self._try_fill_inner(order, td, ts, at_placement=at_placement)
        # trailing güncelle (tetik kontrolünden SONRA: bar içi sıra bilinmediğinden önceki stop ile kontrol edilir)
        if not filled and order.is_open and order.trailing_pct is not None and order.trailing_pct > 0:
            tp = order.trailing_pct / _HUNDRED
            if sd is Side.SELL:
                cand = hi * (_ONE - tp)
                if order.stop_price is None or cand > order.stop_price:
                    order.stop_price = cand
            else:
                cand = lo * (_ONE + tp)
                if order.stop_price is None or cand < order.stop_price:
                    order.stop_price = cand
        return filled

    def _try_fill_inner(self, order: Order, td: TickData, ts: str, *, at_placement: bool = False) -> bool:
        ot, sd = order.order_type, order.side
        last, hi, lo = td.last, td.hi, td.lo
        if ot is OrderType.MARKET:
            ref = (td.ask if sd is Side.BUY else td.bid) or last
            # ÖNİZLEME İLE AYNI KOD YOLU (engine risk kontrolü de `market_fill_price` çağırır).
            px = self.market_fill_price(order.symbol, sd, tick=td)
            self._fill(order, px, order.remaining, ts, kind="market", ref_price=ref, is_maker=False)
            return True
        if ot is OrderType.LIMIT:
            lim = order.price
            crossed = (lo <= lim) if sd is Side.BUY else (hi >= lim)
            if not crossed:
                return False
            # limit fiyatından ya da daha iyisinden (gap): BUY için min(lim, last) — muhafazakâr: limit fiyatı
            px = lim
            self._fill(order, px, order.remaining, ts, kind="limit", ref_price=lim, is_maker=not at_placement)
            return True
        if ot in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT):
            sp = order.stop_price
            if sp is None:
                return False
            trig = (lo <= sp) if sd is Side.SELL else (hi >= sp)
            if not trig:
                return False
            if ot is OrderType.STOP_MARKET:
                base_px = sp
                # gap-through: last stop'un ötesinde → last'tan (kötü)
                if (sd is Side.SELL and last < sp) or (sd is Side.BUY and last > sp):
                    base_px = last
                px = self.slippage.fill_price(base_px, sd, td, is_market=True)
                self._fill(order, px, order.remaining, ts, kind="stop", ref_price=sp, is_maker=False)
                return True
            # STOP_LIMIT: tetiklendi; limit fiyatı erişilebilir mi?
            lim = order.price
            fillable = (last <= lim or lo <= lim) if sd is Side.BUY else (last >= lim or hi >= lim)
            if not fillable:
                order.meta["stop_triggered"] = True
                return False
            self._fill(order, lim, order.remaining, ts, kind="stop_limit", ref_price=lim, is_maker=False)
            return True
        if ot is OrderType.OCO:   # SELL: TP limit ya da stop
            if hi >= order.price:
                self._fill(order, order.price, order.remaining, ts, kind="oco_tp", ref_price=order.price, is_maker=True)
                return True
            if order.stop_price is not None and lo <= order.stop_price:
                base_px = order.stop_price if last >= order.stop_price else last
                if order.oco_limit_price is not None:
                    px = max(base_px, order.oco_limit_price) if base_px >= order.oco_limit_price else None
                    if px is None:
                        order.meta["stop_triggered"] = True
                        return False
                    self._fill(order, order.oco_limit_price, order.remaining, ts, kind="oco_stop_limit", ref_price=order.stop_price)
                    return True
                px = self.slippage.fill_price(base_px, sd, td, is_market=True)
                self._fill(order, px, order.remaining, ts, kind="oco_stop", ref_price=order.stop_price)
                return True
            return False
        return False

    def fill_order(self, order_id: str, price, qty=None, *, now: datetime | None = None, kind: str = "manual",
                   is_maker: bool = False) -> Order | None:
        """Açık bir emri (kısmen) elle doldur — kısmi fill simülasyonu için."""
        order = self.open_orders.get(order_id)
        if order is None:
            return None
        q = D(qty) if qty is not None else order.remaining
        self._fill(order, D(price), q, iso(now or utc_now()), kind=kind, ref_price=D(price), is_maker=is_maker)
        return order

    def _fill(self, order: Order, price: Decimal, qty: Decimal, ts: str, *, kind: str, ref_price: Decimal | None = None,
              is_maker: bool = False) -> Fill:
        f = order.meta.get("filters")
        step = D(f.get("qty_step", "0")) if isinstance(f, dict) else ZERO
        qty = min(quantize_qty(qty, step) if step > 0 else qty, order.remaining)
        base, _ = split_symbol(order.symbol, self.quote_asset)
        notional = qty * price
        fee = self.fees.fee(notional, is_maker=is_maker)
        slip = SlippageModel.cost(ref_price, price, qty) if ref_price is not None else ZERO
        fill = Fill(id=f"{order.id}-{len(order.fills) + 1}", order_id=order.id, symbol=order.symbol, side=order.side, qty=qty,
                    price=price, fee=fee, fee_asset=self.quote_asset, is_maker=is_maker, slippage=slip, ref_price=ref_price,
                    ts=ts, kind=kind)
        if order.side is Side.BUY:
            # kilidi bırak, gerçek maliyeti düş
            lock = D(order.meta.get("locked_cash", 0))
            share = lock * (qty / order.remaining) if order.remaining > 0 else lock
            self.locked_cash -= share
            self.cash += share
            order.meta["locked_cash"] = format(lock - share, "f")
            self.cash -= notional + fee
            self.assets[base] = self.free_asset(base) + qty
            self.lots.setdefault(order.symbol, []).append(Lot(fill_id=fill.id, qty=qty, cost_basis=price, ts=ts,
                                                             fee_per_unit=(fee / qty) if qty > 0 else ZERO))
            pm = self.position_meta.setdefault(order.symbol, {})
            pm.setdefault("entry_time", ts)
            if order.meta.get("strategy"):
                pm["strategy"] = order.meta["strategy"]
            self._entry(LedgerKind.TRANSFER, -notional, order.id, f"buy {order.symbol} {qty}@{price}", ts)
        else:
            self.locked_assets[base] = max(self.locked_assets.get(base, ZERO) - qty, ZERO)
            self.cash += notional - fee
            realized, cost, buy_fees, lots_used = self._consume_fifo(order.symbol, qty)
            net = notional - cost - buy_fees - fee
            self._entry(LedgerKind.PNL, notional - cost, order.id, f"sell {order.symbol} {qty}@{price} gross", ts)
            self._record_sale(order, fill, qty, price, notional, cost, buy_fees, fee, net, lots_used, ts, kind)
            if self.total_asset(base) <= 0:
                self.assets.pop(base, None)
                self.locked_assets.pop(base, None)
                self.lots.pop(order.symbol, None)
                self.position_meta.pop(order.symbol, None)
        self.total_fees += fee
        self._entry(LedgerKind.FEE, -fee, order.id, f"{kind} fee", ts)
        # emir durumu
        prev_filled = order.filled_qty
        order.filled_qty += qty
        order.avg_fill_price = ((order.avg_fill_price or ZERO) * prev_filled + price * qty) / order.filled_qty if order.filled_qty > 0 else price
        order.fills.append(fill)
        order.updated_at = ts
        if order.remaining <= 0:
            order.status = OrderStatus.FILLED
            order.events.append({"ts": ts, "status": "FILLED"})
            if order.side is Side.BUY:
                self._release_locks(order)   # kalan kilit farkını serbest bırak
            self._archive(order)
            # OCO/stop SELL emirleri: aynı sembol için diğer korunma emirleri kalır (kullanıcı yönetir)
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
            order.events.append({"ts": ts, "status": "PARTIALLY_FILLED", "qty": format(qty, "f")})
        return fill

    def _consume_fifo(self, symbol: str, qty: Decimal) -> tuple[Decimal, Decimal, Decimal, list[dict]]:
        lots = self.lots.get(symbol) or []
        need = qty
        cost = ZERO
        buy_fees = ZERO
        used: list[dict] = []
        while need > 0 and lots:
            lot = lots[0]
            take = min(lot.qty, need)
            cost += take * lot.cost_basis
            buy_fees += take * lot.fee_per_unit
            used.append({"fill_id": lot.fill_id, "qty": format(take, "f"), "cost_basis": format(lot.cost_basis, "f"), "ts": lot.ts})
            lot.qty -= take
            need -= take
            if lot.qty <= 0:
                lots.pop(0)
        if need > 0:   # lot yok (legacy import eksikliği) → maliyet 0 kabul
            used.append({"fill_id": "", "qty": format(need, "f"), "cost_basis": "0", "ts": ""})
        realized = ZERO
        return realized, cost, buy_fees, used

    def _record_sale(self, order: Order, fill: Fill, qty: Decimal, price: Decimal, proceeds: Decimal, cost: Decimal, buy_fees: Decimal,
                     sell_fee: Decimal, net: Decimal, lots_used: list[dict], ts: str, kind: str) -> TradeRecord:
        entry_px = (cost / qty) if qty > 0 else ZERO
        opened = lots_used[0].get("ts", "") if lots_used else ""
        pm = self.position_meta.get(order.symbol, {})
        gross = proceeds - cost
        rec = TradeRecord(id=f"{order.id}-{len(order.fills) + 1}", symbol=order.symbol, side="LONG", entry=entry_px, exit_reason=kind,
                          closed_at=ts, opened_at=opened or pm.get("entry_time", ""), pnl=net, fees=buy_fees + sell_fee, funding=ZERO,
                          setup_type=str(order.meta.get("strategy", pm.get("strategy", ""))), market_type=MarketType.SPOT,
                          amount_type=AmountType.QUANTITY, requested_notional=cost, effective_notional=cost, quantity=qty,
                          entry_fee=buy_fees, exit_fee=sell_fee, slippage_cost=fill.slippage, gross_pnl=gross, net_pnl=net,
                          exit_price=price, fills=[fill], tax_estimate=self.tax_policy.estimate(net),
                          costs={"fifo_lots": lots_used, "buy_fees": format(buy_fees, "f"), "sell_fee": format(sell_fee, "f"),
                                 "tax_policy_version": self.tax_policy.version})
        self.history.append(rec)
        if len(self.history) > self.history_keep:
            self.history = self.history[-self.history_keep:]
        return rec

    # ------------------------------------------------------------------ tik
    def tick(self, marks: Mapping[str, Any], now: datetime | None = None) -> list[Order]:
        """Açık emirleri fiyatlarla kontrol et. Dönen: bu tikte (kısmen/tamamen) dolan emirler."""
        now = now or utc_now()
        ts = iso(now)
        filled: list[Order] = []
        for oid in list(self.open_orders):
            order = self.open_orders.get(oid)
            if order is None:
                continue
            raw = marks.get(order.symbol)
            if raw is None:
                continue
            td = TickData.coerce(raw)
            if self._try_fill(order, td, ts):
                filled.append(order)
        self.updated_at = ts
        return filled

    # ------------------------------------------------------------------ kolaylık
    def market_fill_price(self, symbol: str, side=Side.BUY, *, tick=None, ref_price=None) -> Decimal:
        """Bir SPOT MARKET emrinin GERÇEKLEŞECEĞİ fiyat — durum DEĞİŞTİRMEZ.

        `_try_fill_inner` de tam olarak bunu çağırır: BUY için `ask`, SELL için `bid`, yoksa `last`
        seçimi ve spot kayma modeli birebir aynıdır. `tick` verildiyse `ref_price` kullanılmaz —
        `place_order` da aynı önceliği uygular. Böylece ask/last farkı 3 bps'yi aşsa bile risk
        önizlemesi gerçekleşecek girişi doğru görür.
        """
        sd = side if isinstance(side, Side) else Side(str(side).upper())
        td = TickData.coerce(tick) if tick is not None else (TickData(last=D(ref_price)) if ref_price is not None else None)
        if td is None:
            return ZERO
        ref = (td.ask if sd is Side.BUY else td.bid) or td.last
        if ref is None or D(ref) <= 0:
            return ZERO
        return self.slippage.fill_price(ref, sd, td, is_market=True)

    def market_buy(self, symbol: str, *, qty=None, quote_amount=None, tick=None, ref_price=None, strategy: str = "",
                   filters=None, now=None) -> Order:
        return self.place_order(symbol, Side.BUY, OrderType.MARKET, qty=qty, quote_amount=quote_amount, tick=tick, ref_price=ref_price,
                                strategy=strategy, filters=filters, now=now)

    def market_sell(self, symbol: str, *, qty=None, tick=None, ref_price=None, filters=None, now=None, reason: str = "manual") -> Order:
        base, _ = split_symbol(symbol, self.quote_asset)
        q = D(qty) if qty is not None else self.free_asset(base)
        return self.place_order(symbol, Side.SELL, OrderType.MARKET, qty=q, tick=tick, ref_price=ref_price, filters=filters, now=now,
                                meta={"reason": reason})

    def stop_orders(self, symbol: str) -> list[Order]:
        return [o for o in self.open_orders.values() if o.symbol == symbol and o.order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT, OrderType.OCO)]

    def positions(self) -> dict[str, dict]:
        """Sembol → {qty, avg_cost, entry_time, stop, strategy}. Legacy Portfolio.positions benzeri görünüm."""
        out: dict[str, dict] = {}
        for sym, lots in self.lots.items():
            q = sum((l.qty for l in lots), ZERO)
            if q <= 0:
                continue
            stops = self.stop_orders(sym)
            stop = min((o.stop_price for o in stops if o.stop_price is not None), default=None)
            pm = self.position_meta.get(sym, {})
            out[sym] = {"symbol": sym, "qty": q, "units": float(q), "avg_cost": self.avg_cost(sym), "entry_price": float(self.avg_cost(sym) or 0),
                        "entry_time": pm.get("entry_time", lots[0].ts), "stop": float(stop) if stop is not None else 0.0,
                        "strategy": pm.get("strategy", "")}
        return out

    # ------------------------------------------------------------------ değerleme
    def unrealized(self, symbol: str, price) -> Decimal:
        """Gerçekleşmemiş PnL, tahmini çıkış ücreti ve alış ücreti payı düşülmüş (net)."""
        lots = self.lots.get(symbol) or []
        q = sum((l.qty for l in lots), ZERO)
        if q <= 0:
            return ZERO
        px = D(price)
        gross = sum(((px - l.cost_basis) * l.qty for l in lots), ZERO)
        buy_fees = sum((l.fee_per_unit * l.qty for l in lots), ZERO)
        exit_fee = self.fees.fee(q * px, is_maker=False)
        return gross - buy_fees - exit_fee

    def _price_of(self, marks: Mapping[str, Any] | None, symbol: str) -> Decimal | None:
        if not marks or symbol not in marks:
            return None
        return TickData.coerce(marks[symbol]).ref

    def equity(self, marks: Mapping[str, Any] | None = None) -> Decimal:
        """Brüt: nakit (kilitli dahil) + varlıkların piyasa değeri (fiyat yoksa maliyet)."""
        val = self.cash + self.locked_cash
        for sym, lots in self.lots.items():
            q = sum((l.qty for l in lots), ZERO)
            px = self._price_of(marks, sym) or self.avg_cost(sym) or ZERO
            val += q * px
        return val

    def equity_net(self, marks: Mapping[str, Any] | None = None) -> Decimal:
        """Net: brüt − tahmini çıkış ücretleri."""
        val = self.cash + self.locked_cash
        for sym, lots in self.lots.items():
            q = sum((l.qty for l in lots), ZERO)
            px = self._price_of(marks, sym) or self.avg_cost(sym) or ZERO
            val += q * px - self.fees.fee(q * px, False)
        return val

    def summary(self, marks: Mapping[str, Any] | None = None) -> dict:
        eq = self.equity(marks)
        closed = self.history
        wins = [h for h in closed if h.pnl > 0]
        return {"cash": round(float(self.cash), 4), "locked_cash": round(float(self.locked_cash), 4),
                "equity": round(float(eq), 4), "equity_net": round(float(self.equity_net(marks)), 4),
                "starting_equity": float(self.starting_equity),
                "return_pct": round(float(eq / self.starting_equity * _HUNDRED - _HUNDRED), 2) if self.starting_equity > 0 else 0.0,
                "open": len(self.positions()), "open_orders": len(self.open_orders), "closed": len(closed),
                "win_rate": round(100 * len(wins) / len(closed), 1) if closed else 0.0,
                "total_fees": round(float(self.total_fees), 4), "schema_version": self.schema_version}

    def history_dicts(self) -> list[dict]:
        return [h.to_legacy_dict() for h in self.history]

    # ------------------------------------------------------------------ kalıcılık
    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "kind": "spot", "updated_at": self.updated_at or iso(),
                "quote_asset": self.quote_asset, "starting_equity": ser(self.starting_equity), "cash": ser(self.cash),
                "locked_cash": ser(self.locked_cash), "assets": ser(self.assets), "locked_assets": ser(self.locked_assets),
                "lots": {k: [l.to_dict() for l in v] for k, v in self.lots.items()},
                "open_orders": {k: v.to_dict() for k, v in self.open_orders.items()},
                "closed_orders": [o.to_dict() for o in self.closed_orders[-self.orders_keep:]],
                "history": [h.to_dict() for h in self.history], "entries": [e.to_dict() for e in self.entries],
                "position_meta": self.position_meta, "fees": self.fees.to_dict(), "slippage": self.slippage.to_dict(),
                "tax_policy": self.tax_policy.to_dict(), "total_fees": ser(self.total_fees), "seq": self.seq, "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict, **kwargs) -> "SpotLedger":
        if int(d.get("schema_version", 1)) < 2 or "lots" not in d:
            return cls.import_legacy_portfolio(d, **kwargs)
        led = cls(d.get("starting_equity", 0), quote_asset=d.get("quote_asset", "USDT"),
                  fees=FeeSchedule.from_dict(d["fees"]) if d.get("fees") else kwargs.get("fees"),
                  slippage=SlippageModel.from_dict(d["slippage"]) if d.get("slippage") else kwargs.get("slippage"),
                  filters_lookup=kwargs.get("filters_lookup"),
                  tax_policy=TaxPolicy.from_dict(d["tax_policy"]) if d.get("tax_policy") else kwargs.get("tax_policy"))
        led.cash = D(d.get("cash", 0))
        led.locked_cash = D(d.get("locked_cash", 0))
        led.assets = {k: D(v) for k, v in (d.get("assets") or {}).items()}
        led.locked_assets = {k: D(v) for k, v in (d.get("locked_assets") or {}).items()}
        led.lots = {k: [Lot.from_dict(x) for x in v] for k, v in (d.get("lots") or {}).items()}
        led.open_orders = {k: Order.from_dict(v) for k, v in (d.get("open_orders") or {}).items()}
        led.closed_orders = [Order.from_dict(o) for o in d.get("closed_orders", [])]
        led.history = [TradeRecord.from_dict(h) for h in d.get("history", [])]
        led.entries = [LedgerEntry.from_dict(e) for e in d.get("entries", [])]
        led.position_meta = dict(d.get("position_meta") or {})
        led.total_fees = D(d.get("total_fees", 0))
        led.seq = int(d.get("seq", 0))
        led.updated_at = d.get("updated_at", "")
        led.meta = dict(d.get("meta") or {})
        return led

    @classmethod
    def import_legacy_portfolio(cls, d: dict, **kwargs) -> "SpotLedger":
        """portfolio.Portfolio JSON'u → SpotLedger. Nakit + pozisyonlar (units@entry_price) + stop emirleri; equity korunur."""
        starting = D(d.get("starting_equity", d.get("cash", 0)))
        led = cls(starting, **{k: v for k, v in kwargs.items() if k in ("quote_asset", "fees", "slippage", "filters_lookup", "tax_policy")})
        led.cash = D(d.get("cash", 0))
        led.updated_at = d.get("updated_at", "")
        led.meta = {"imported_from": "portfolio_v1", "imported_at": iso()}
        for sym, p in (d.get("positions") or {}).items():
            units = D(p.get("units", 0))
            if units <= 0:
                continue
            entry = D(p.get("entry_price", 0))
            base, _ = split_symbol(sym, led.quote_asset)
            led.seq += 1
            fid = f"legacy-{led.seq}"
            led.assets[base] = led.free_asset(base) + units
            led.lots.setdefault(sym, []).append(Lot(fill_id=fid, qty=units, cost_basis=entry, ts=p.get("entry_time", ""), fee_per_unit=ZERO))
            led.position_meta[sym] = {"entry_time": p.get("entry_time", ""), "strategy": p.get("strategy", "")}
            stop = D(p.get("stop", 0) or 0)
            if stop > 0:
                led.place_order(sym, Side.SELL, OrderType.STOP_MARKET, qty=units, stop_price=stop, strategy=p.get("strategy", ""),
                                meta={"legacy": True})
        for h in d.get("history", []) or []:
            led.history.append(_legacy_spot_record(h))
        return led

    def save(self, path: Path | str, *, keep_backup: bool = True) -> None:
        self.updated_at = iso()
        atomic_write_json(path, self.to_dict(), indent=1, keep_backup=keep_backup)

    @classmethod
    def load(cls, path: Path | str, starting_cash=None, **kwargs) -> "SpotLedger":
        p = Path(path)
        if not p.exists():
            if starting_cash is None:
                raise StorageError(f"defter yok ve starting_cash verilmedi: {p}")
            return cls(starting_cash, **kwargs)
        d = read_json(p)
        if not isinstance(d, dict):
            raise StorageError(f"beklenmeyen defter içeriği: {p}")
        return cls.from_dict(d, **kwargs)


def _legacy_spot_record(h: dict) -> TradeRecord:
    units = D(h.get("units", 0))
    entry = D(h.get("entry_price", 0))
    exit_px = D(h.get("exit_price", 0))
    pnl = D(h.get("pnl", 0))
    gross = (exit_px - entry) * units
    return TradeRecord(id=str(h.get("id", f"legacy-{h.get('symbol', '')}-{h.get('exit_time', '')}")), symbol=h.get("symbol", ""), side="LONG",
                       entry=entry, exit_reason=h.get("reason", ""), closed_at=h.get("exit_time", ""), opened_at=h.get("entry_time", ""),
                       pnl=pnl, fees=gross - pnl if gross - pnl > 0 else ZERO, setup_type=h.get("strategy", ""), market_type=MarketType.SPOT,
                       amount_type=AmountType.QUANTITY, quantity=units, gross_pnl=gross, net_pnl=pnl, exit_price=exit_px,
                       costs={"legacy": True, "legacy_row": dict(h)})


__all__ = ["SpotLedger", "split_symbol", "R_OK", "R_NO_SHORTING", "R_INSUFFICIENT_ASSET", "R_INSUFFICIENT_CASH", "R_BAD_PRICE",
           "R_UNSUPPORTED", "R_NO_PRICE"]
