"""FuturesLedgerV2 — Decimal, izole marj, tek yön (one-way) USDⓈ-M kağıt defteri.

Cüzdan modeli:
* `wallet_balance`  : gerçekleşen bakiye (başlangıç + net PnL − komisyon ± funding − liq ücreti). Legacy `equity` ile aynı anlam.
* `used_margin`     : açık pozisyonların izole marj toplamı (cüzdandan ayrı tutulmaz, ayrılmış sayılır).
* `available`       : wallet_balance − used_margin. Açılışta `margin + entry_fee ≤ available` şartı aranır.

Boyut anlamı (SizeSpec):
* NOTIONAL: amount = pozisyon büyüklüğü (USDT) → qty = amount/fill (adıma AŞAĞI), margin = qty*fill/lev
* MARGIN  : amount = yatırılacak marj → notional = amount*lev → qty aşağı → efektif marj = qty*fill/lev (≤ amount)
* QUANTITY: amount = adet

Tik kuralları: mark (yoksa last) ve varsa high/low; öncelik LİKİDASYON > STOP > TP (aynı tikte stop+TP → stop; worst-case).
TP1'de kısmi kapanış (varsayılan %50) ve stop GERÇEK başabaşa (giriş+çıkış komisyonu + kayma tamponu) çekilir.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..core import D, ZERO, StorageError, atomic_write_json, iso, quantize_price, quantize_qty, read_json, utc_now
from .fees import FeeSchedule
from .filters import LeverageBracket, bracket_for, default_filters
from .funding import FundingEvent, FundingSchedule, RateLookup
from .liquidation import LiquidationParams, is_liquidated, liquidation_outcome, liquidation_price
from .models import (
    SCHEMA_VERSION,
    AmountType,
    Fill,
    LedgerEntry,
    LedgerKind,
    MarginMode,
    MarketType,
    Position,
    PositionSide,
    Side,
    SizeSpec,
    SymbolFilters,
    TickData,
    TradeRecord,
    ser,
)
from .slippage import SlippageModel
from .tax import TaxPolicy

log = logging.getLogger(__name__)

_ONE = Decimal("1")
_HUNDRED = Decimal("100")

# ret nedenleri
R_OK = "OK"
R_ALREADY_OPEN = "ALREADY_OPEN"
R_MAX_POSITIONS = "MAX_POSITIONS"
R_ZERO_QTY = "STEP_ZERO_QTY"
R_MIN_QTY = "MIN_QTY"
R_MAX_QTY = "MAX_QTY"
R_MIN_NOTIONAL = "MIN_NOTIONAL"
R_INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
R_LEVERAGE = "LEVERAGE_TOO_HIGH"
R_BAD_PRICE = "BAD_PRICE"
R_BAD_STOP = "BAD_STOP"

EXIT_STOP = "stop"
EXIT_BE_STOP = "başa-baş stop"
EXIT_TP1 = "hedef1"
EXIT_TP2 = "hedef2"
EXIT_LIQ = "likidasyon"
EXIT_MANUAL = "manuel"


def _side(x) -> PositionSide:
    if isinstance(x, PositionSide):
        return x
    s = str(x).upper()
    if s in ("LONG", "BUY"):
        return PositionSide.LONG
    if s in ("SHORT", "SELL"):
        return PositionSide.SHORT
    raise ValueError(f"geçersiz yön: {x}")


class FuturesLedgerV2:
    """İzole marj, tek yön kağıt futures defteri (Decimal). Sembol başına en fazla bir pozisyon."""

    schema_version = SCHEMA_VERSION

    def __init__(self, starting_equity, *, max_positions: int = 3, fees: FeeSchedule | None = None,
                 slippage: SlippageModel | None = None, brackets: list[LeverageBracket] | None = None,
                 liq_params: LiquidationParams | None = None, funding: FundingSchedule | None = None,
                 tax_policy: TaxPolicy | None = None, tp1_fraction=Decimal("0.5"), allow_shrink: bool = False,
                 worst_case: bool = True, tp_maker: bool = False, entries_keep: int = 2000, history_keep: int = 5000):
        self.starting_equity: Decimal = D(starting_equity)
        self.wallet_balance: Decimal = D(starting_equity)
        self.max_positions = int(max_positions)
        self.fees = fees or FeeSchedule.futures_default()
        self.slippage = slippage or SlippageModel.zero()
        self.brackets = brackets
        self.liq_params = liq_params or LiquidationParams()
        self.funding = funding or FundingSchedule()
        self.tax_policy = tax_policy or TaxPolicy.disabled()
        self.tp1_fraction = D(tp1_fraction)
        self.allow_shrink = bool(allow_shrink)
        self.worst_case = bool(worst_case)
        self.tp_maker = bool(tp_maker)
        self.entries_keep = int(entries_keep)
        self.history_keep = int(history_keep)
        self.positions: dict[str, Position] = {}
        self.history: list[TradeRecord] = []
        self.entries: list[LedgerEntry] = []
        self.total_fees: Decimal = ZERO
        self.total_funding: Decimal = ZERO       # net cüzdan etkisi (+ alındı)
        self.seq: int = 0
        self.updated_at: str = ""
        self.last_reject_reason: str = ""
        self.meta: dict = {}

    # ------------------------------------------------------------------ bakiye
    @property
    def used_margin(self) -> Decimal:
        return sum((p.isolated_margin for p in self.positions.values()), ZERO)

    @property
    def available(self) -> Decimal:
        return self.wallet_balance - self.used_margin

    @property
    def equity(self) -> Decimal:
        """Legacy anlam: gerçekleşen bakiye."""
        return self.wallet_balance

    def _entry(self, kind: LedgerKind, amount: Decimal, ref_id: str = "", note: str = "", ts: str | None = None) -> None:
        self.entries.append(LedgerEntry(ts=ts or iso(), kind=kind, amount=amount, ref_id=ref_id, note=note))
        if len(self.entries) > self.entries_keep:
            self.entries = self.entries[-self.entries_keep:]

    def _reject(self, reason: str, detail: str = "") -> None:
        self.last_reject_reason = reason
        log.info("futures open reddedildi: %s %s", reason, detail)
        return None

    def can_open(self, symbol: str) -> tuple[bool, str]:
        if symbol in self.positions:
            return False, R_ALREADY_OPEN
        if len(self.positions) >= self.max_positions:
            return False, R_MAX_POSITIONS
        return True, R_OK

    # ------------------------------------------------------------------ açılış
    def open(self, symbol: str, side, price, size: SizeSpec, stop=None, targets: Iterable | None = None, *,
             filters: SymbolFilters | None = None, fees: FeeSchedule | None = None, slippage: SlippageModel | None = None,
             brackets: list[LeverageBracket] | None = None, setup_type: str = "", trigger_text: str = "",
             features: dict | None = None, mark_price=None, tick: TickData | None = None, now: datetime | None = None,
             meta: dict | None = None) -> Position | None:
        """Pozisyon aç. Reddedilirse None döner ve `last_reject_reason` doldurulur (asla sessiz küçültme yok; `allow_shrink` hariç)."""
        self.last_reject_reason = ""
        pside = _side(side)
        ok, why = self.can_open(symbol)
        if not ok:
            return self._reject(why, symbol)
        filters = filters or default_filters(symbol, MarketType.USDM_PERP)
        fees = fees or self.fees
        slip = slippage or self.slippage
        brackets = brackets or self.brackets
        ref = D(price)
        if ref <= 0:
            return self._reject(R_BAD_PRICE, symbol)
        lev = max(1, int(size.leverage))
        if lev > filters.max_leverage:
            return self._reject(R_LEVERAGE, f"{lev}>{filters.max_leverage}")
        fill = slip.fill_price(ref, pside.open_side, tick, is_market=True)
        fill = quantize_price(fill, filters.price_tick, pside.open_side.value, aggressive=True) if filters.price_tick > 0 else fill
        # miktar
        if size.amount_type is AmountType.QUANTITY:
            raw_qty = size.amount
        else:
            raw_qty = size.target_notional(fill) / fill
        qty = quantize_qty(raw_qty, filters.qty_step)
        taker = fees.rate(False)
        margin_rate = _ONE / lev
        if qty > 0 and margin_rate * qty * fill + taker * qty * fill > self.available:
            if not self.allow_shrink:
                need = qty * fill * (margin_rate + taker)
                return self._reject(R_INSUFFICIENT_MARGIN, f"gerekli {need:.4f} > serbest {self.available:.4f}")
            qty = quantize_qty(self.available / (fill * (margin_rate + taker)), filters.qty_step)
        if qty <= 0:
            return self._reject(R_ZERO_QTY, symbol)
        if qty < filters.min_qty:
            return self._reject(R_MIN_QTY, f"{qty} < {filters.min_qty}")
        if qty > filters.market_max_qty:
            return self._reject(R_MAX_QTY, f"{qty} > {filters.market_max_qty}")
        notional = qty * fill
        if notional < filters.min_notional:
            return self._reject(R_MIN_NOTIONAL, f"{notional} < {filters.min_notional}")
        margin = notional * margin_rate
        entry_fee = fees.fee(notional, is_maker=False)
        if margin + entry_fee > self.available:
            return self._reject(R_INSUFFICIENT_MARGIN, f"gerekli {margin + entry_fee:.4f} > serbest {self.available:.4f}")
        # stop mantık kontrolü
        stop_d = D(stop) if stop is not None else None
        if stop_d is not None and stop_d > 0:
            if (pside is PositionSide.LONG and stop_d >= fill) or (pside is PositionSide.SHORT and stop_d <= fill):
                return self._reject(R_BAD_STOP, f"stop {stop_d} vs giriş {fill}")
        elif stop_d is not None and stop_d <= 0:
            stop_d = None
        tgts = [D(t) for t in (targets or []) if t is not None and D(t) > 0]
        now = now or utc_now()
        ts = iso(now)
        self.seq += 1
        pid = f"F{self.seq:05d}"
        slip_cost = SlippageModel.cost(ref, fill, qty)
        fill_rec = Fill(id=f"{pid}-e", order_id=pid, symbol=symbol, side=pside.open_side, qty=qty, price=fill, fee=entry_fee,
                        is_maker=False, slippage=slip_cost, ref_price=ref, ts=ts, kind="entry")
        req_notional = size.target_notional(fill) if size.amount_type is not AmountType.QUANTITY else notional
        req_margin = size.amount if size.amount_type is AmountType.MARGIN else req_notional / lev
        pos = Position(id=pid, symbol=symbol, market_type=MarketType.USDM_PERP, side=pside, qty=qty, entry_avg=fill,
                       leverage=lev, margin_mode=MarginMode.ISOLATED, isolated_margin=margin, opened_at=ts, stop=stop_d,
                       targets=tgts, initial_qty=qty, initial_stop=stop_d, last_funding_settlement_utc=ts, last_price=fill,
                       entry_fee=entry_fee, fees_paid=entry_fee, slippage_cost=slip_cost, requested_notional=req_notional,
                       requested_margin=req_margin, amount_type=size.amount_type, setup_type=setup_type,
                       trigger_text=trigger_text, features=dict(features or {}), fills=[fill_rec], meta=dict(meta or {}))
        pos.meta.setdefault("ref_entry", format(ref, "f"))
        pos.meta["fees"] = fees.to_dict()
        pos.meta["filters"] = filters.to_dict()
        pos.meta["initial_margin"] = format(margin, "f")
        pos.meta["gross_realized"] = "0"
        pos.meta["exit_fee_total"] = "0"
        pos.meta["exit_slippage"] = "0"
        pos.liquidation_price = self._liq_price(pos, brackets)
        pos.features.setdefault("initial_stop", float(stop_d) if stop_d is not None else None)
        pos.features["initial_units"] = float(qty)
        pos.features["liquidation_price"] = float(pos.liquidation_price)
        # cüzdan
        self.wallet_balance -= entry_fee
        self.total_fees += entry_fee
        self._entry(LedgerKind.FEE, -entry_fee, pid, "entry fee", ts)
        self.positions[symbol] = pos
        self.last_reject_reason = R_OK
        return pos

    def _liq_price(self, pos: Position, brackets=None) -> Decimal:
        notional = pos.qty * pos.entry_avg
        if self.liq_params.use_brackets:
            b = bracket_for(notional, brackets or self.brackets)
            mmr, maint = b.mmr, b.maint_amount
        else:
            mmr, maint = ZERO, ZERO
        cushion = notional * self.liq_params.fee_cushion_pct / _HUNDRED
        return liquidation_price(pos.side, pos.entry_avg, pos.qty, pos.isolated_margin, mmr, maint, cushion)

    # ------------------------------------------------------------------ başabaş
    def break_even_price(self, pos: Position, fees: FeeSchedule | None = None, slippage: SlippageModel | None = None) -> Decimal:
        """Kalan miktar için gerçek başabaş: giriş komisyonu payı + çıkış komisyonu + kayma tamponu sıfırlanır."""
        fees = fees or self.fees
        slip = slippage or self.slippage
        f_entry = (pos.entry_fee / (pos.initial_qty * pos.entry_avg)) if pos.initial_qty > 0 and pos.entry_avg > 0 else fees.rate(False)
        f_exit = fees.rate(False)
        s = slip.rate
        e = pos.entry_avg
        if pos.side is PositionSide.LONG:
            be = e * (_ONE + f_entry) / ((_ONE - s) * (_ONE - f_exit))
        else:
            be = e * (_ONE - f_entry) / ((_ONE + s) * (_ONE + f_exit))
        tick = D(pos.meta.get("filters", {}).get("price_tick", "0")) if isinstance(pos.meta.get("filters"), dict) else ZERO
        if tick > 0:
            # LONG: yukarı yuvarla (SELL pasif yön = yukarı), SHORT: aşağı
            be = quantize_price(be, tick, pos.side.close_side.value)
        return be

    # ------------------------------------------------------------------ kapanış parçaları
    def _close_part(self, pos: Position, fill_price: Decimal, qty: Decimal, kind: str, ts: str, *, ref_price: Decimal | None = None,
                    is_maker: bool = False, fee_override: Decimal | None = None) -> tuple[Decimal, Decimal]:
        """qty kadar kapat; cüzdanı güncelle. Dönen: (gross_pnl, exit_fee)."""
        qty = min(qty, pos.qty)
        if qty <= 0:
            return ZERO, ZERO
        fees = FeeSchedule.from_dict(pos.meta["fees"]) if isinstance(pos.meta.get("fees"), dict) else self.fees
        notional = qty * fill_price
        exit_fee = fee_override if fee_override is not None else fees.fee(notional, is_maker=is_maker)
        gross = (fill_price - pos.entry_avg) * qty * pos.side.sign
        released = pos.isolated_margin * (qty / pos.qty) if pos.qty > 0 else ZERO
        slip_cost = SlippageModel.cost(ref_price, fill_price, qty) if ref_price is not None else ZERO
        pos.fills.append(Fill(id=f"{pos.id}-{kind}-{len(pos.fills)}", order_id=pos.id, symbol=pos.symbol, side=pos.side.close_side,
                              qty=qty, price=fill_price, fee=exit_fee, is_maker=is_maker, slippage=slip_cost, ref_price=ref_price,
                              ts=ts, kind=kind))
        pos.qty -= qty
        pos.isolated_margin -= released
        pos.realized_pnl += gross - exit_fee
        pos.fees_paid += exit_fee
        pos.exit_fee += exit_fee
        pos.slippage_cost += slip_cost
        pos.meta["gross_realized"] = format(D(pos.meta.get("gross_realized", 0)) + gross, "f")
        pos.meta["exit_fee_total"] = format(D(pos.meta.get("exit_fee_total", 0)) + exit_fee, "f")
        pos.meta["exit_slippage"] = format(D(pos.meta.get("exit_slippage", 0)) + slip_cost, "f")
        self.wallet_balance += gross - exit_fee
        self.total_fees += exit_fee
        self._entry(LedgerKind.PNL, gross, pos.id, f"{kind} gross", ts)
        self._entry(LedgerKind.LIQ_FEE if kind == EXIT_LIQ else LedgerKind.FEE, -exit_fee, pos.id, f"{kind} fee", ts)
        if pos.qty <= 0:
            pos.qty = ZERO
            pos.isolated_margin = ZERO
        return gross, exit_fee

    def _finalize(self, pos: Position, reason: str, ts: str) -> TradeRecord:
        self.positions.pop(pos.symbol, None)
        pos.status = "CLOSED"
        pos.closed_at = ts
        gross = D(pos.meta.get("gross_realized", 0))
        exit_fee = D(pos.meta.get("exit_fee_total", 0))
        funding_net = pos.funding_received - pos.funding_paid
        fees_total = pos.entry_fee + exit_fee
        net = gross - fees_total + funding_net
        exit_fills = [f for f in pos.fills if f.kind != "entry"]
        exit_qty = sum((f.qty for f in exit_fills), ZERO)
        exit_px = (sum((f.qty * f.price for f in exit_fills), ZERO) / exit_qty) if exit_qty > 0 else None
        risk = abs(pos.entry_avg - pos.initial_stop) * pos.initial_qty if pos.initial_stop is not None else ZERO
        r_mult = (net / risk) if risk > 0 else ZERO
        spread_cost = D(pos.meta.get("spread_cost", 0))
        tax_est = self.tax_policy.estimate(net)
        rec = TradeRecord(
            id=pos.id, symbol=pos.symbol, side=pos.side.value, entry=pos.entry_avg, exit_reason=reason, closed_at=ts,
            opened_at=pos.opened_at, pnl=net, fees=fees_total, funding=funding_net, r_multiple=r_mult, mae_pct=pos.mae_pct,
            mfe_pct=pos.mfe_pct, bars_held=pos.bars_held, leverage=pos.leverage, setup_type=pos.setup_type,
            trigger_text=pos.trigger_text, features=dict(pos.features), tp1_done=pos.tp1_done,
            market_type=MarketType.USDM_PERP, amount_type=pos.amount_type, requested_margin=pos.requested_margin,
            requested_notional=pos.requested_notional, effective_margin=D(pos.meta.get("initial_margin", 0)),
            effective_notional=pos.initial_qty * pos.entry_avg, quantity=pos.initial_qty, entry_fee=pos.entry_fee,
            exit_fee=exit_fee, funding_paid=pos.funding_paid, funding_received=pos.funding_received,
            slippage_cost=pos.slippage_cost, spread_cost=spread_cost, tax_estimate=tax_est, gross_pnl=gross, net_pnl=net,
            exit_price=exit_px, liquidation_price=pos.liquidation_price, fills=list(pos.fills),
            costs={"entry_fee": format(pos.entry_fee, "f"), "exit_fee": format(exit_fee, "f"),
                   "funding_paid": format(pos.funding_paid, "f"), "funding_received": format(pos.funding_received, "f"),
                   "slippage": format(pos.slippage_cost, "f"), "spread": format(spread_cost, "f"),
                   "tax_estimate": format(tax_est, "f"), "tax_policy_version": self.tax_policy.version,
                   "liq_clamped": bool(pos.meta.get("liq_clamped", False))})
        self.history.append(rec)
        if len(self.history) > self.history_keep:
            self.history = self.history[-self.history_keep:]
        return rec

    # ------------------------------------------------------------------ tik
    def tick(self, marks: Mapping[str, Any], now_utc: datetime | None = None, funding_rate_lookup: RateLookup | None = None,
             bar_advance: bool = False) -> list[TradeRecord]:
        """Stop/TP/likidasyon/funding kontrolü. `marks`: {symbol: TickData | fiyat}. Dönen: kapanan işlemler."""
        now = now_utc or utc_now()
        ts = iso(now)
        closed: list[TradeRecord] = []
        for sym in list(self.positions):
            pos = self.positions[sym]
            raw = marks.get(sym)
            if raw is None:
                continue
            td = TickData.coerce(raw)
            mark = td.ref
            if mark <= 0:
                continue
            pos.last_price = mark
            if bar_advance:
                pos.bars_held += 1
            worst = td.lo if pos.side is PositionSide.LONG else td.hi
            best = td.hi if pos.side is PositionSide.LONG else td.lo
            move_worst = (worst / pos.entry_avg - _ONE) * _HUNDRED * pos.side.sign
            move_best = (best / pos.entry_avg - _ONE) * _HUNDRED * pos.side.sign
            pos.mae_pct = min(pos.mae_pct, move_worst)
            pos.mfe_pct = max(pos.mfe_pct, move_best)
            # trailing stop (opsiyonel)
            if pos.trailing_pct is not None and pos.trailing_pct > 0:
                trail = best * (_ONE - pos.trailing_pct / _HUNDRED) if pos.side is PositionSide.LONG else best * (_ONE + pos.trailing_pct / _HUNDRED)
                if pos.stop is None or (pos.side is PositionSide.LONG and trail > pos.stop) or (pos.side is PositionSide.SHORT and trail < pos.stop):
                    pos.stop = trail
            # funding — kaçırılan bütün settlement'lar
            fev = self.funding.accrue(pos, now, mark, funding_rate_lookup)
            for ev in fev:
                self.wallet_balance += ev.amount
                self.total_funding += ev.amount
                self._entry(LedgerKind.FUNDING, ev.amount, pos.id, f"funding rate={ev.rate}{' est' if ev.estimated else ''}", ev.ts)
            # likidasyon
            if pos.liquidation_price is not None and is_liquidated(pos.side, worst, pos.liquidation_price):
                closed.append(self._liquidate(pos, ts))
                continue
            stop_hit = pos.stop is not None and ((pos.side is PositionSide.LONG and worst <= pos.stop) or
                                                 (pos.side is PositionSide.SHORT and worst >= pos.stop))
            if stop_hit:
                # gap-through: mark stop'un ötesindeyse mark'tan doldur
                trig = pos.stop
                if (pos.side is PositionSide.LONG and mark < trig) or (pos.side is PositionSide.SHORT and mark > trig):
                    trig = mark
                fill = self.slippage.fill_price(trig, pos.side.close_side, td, is_market=True)
                reason = EXIT_BE_STOP if pos.tp1_done else EXIT_STOP
                self._close_part(pos, fill, pos.qty, reason, ts, ref_price=trig)
                closed.append(self._finalize(pos, reason, ts))
                continue
            # hedefler (sırayla; TP1 kısmi, son hedef tam)
            while pos.qty > 0 and pos.targets_hit < len(pos.targets):
                tgt = pos.targets[pos.targets_hit]
                hit = (best >= tgt) if pos.side is PositionSide.LONG else (best <= tgt)
                if not hit:
                    break
                idx = pos.targets_hit
                is_last = idx == len(pos.targets) - 1
                if is_last:
                    self._close_part(pos, tgt, pos.qty, EXIT_TP2 if idx >= 1 else EXIT_TP1, ts, ref_price=tgt, is_maker=self.tp_maker)
                    pos.targets_hit += 1
                    closed.append(self._finalize(pos, EXIT_TP2 if idx >= 1 else EXIT_TP1, ts))
                    break
                part = quantize_qty(pos.qty * self.tp1_fraction, self._step(pos))
                if part <= 0:
                    part = pos.qty
                self._close_part(pos, tgt, part, EXIT_TP1 if idx == 0 else f"hedef{idx + 1}", ts, ref_price=tgt, is_maker=self.tp_maker)
                pos.targets_hit += 1
                if idx == 0:
                    pos.tp1_done = True
                    be = self.break_even_price(pos)
                    if pos.stop is None or (pos.side is PositionSide.LONG and be > pos.stop) or (pos.side is PositionSide.SHORT and be < pos.stop):
                        pos.stop = be
                if pos.qty <= 0:
                    closed.append(self._finalize(pos, EXIT_TP1 if idx == 0 else f"hedef{idx + 1}", ts))
                    break
                # kalan pozisyonun likidasyon fiyatı marj oranı değişmediği için aynı kalır
        self.updated_at = ts
        return closed

    def _step(self, pos: Position) -> Decimal:
        f = pos.meta.get("filters")
        return D(f.get("qty_step", "0.001")) if isinstance(f, dict) else Decimal("0.001")

    def _liquidate(self, pos: Position, ts: str) -> TradeRecord:
        lp = pos.liquidation_price if pos.liquidation_price is not None else pos.entry_avg
        gross, liq_fee, clamped = liquidation_outcome(pos, lp, self.liq_params)
        # gross'u kırpılmış hale getirmek için fill fiyatını değil sonucu uyguluyoruz
        qty = pos.qty
        pos.meta["liq_clamped"] = clamped
        pos.fills.append(Fill(id=f"{pos.id}-liq", order_id=pos.id, symbol=pos.symbol, side=pos.side.close_side, qty=qty, price=lp,
                              fee=liq_fee, is_maker=False, ts=ts, kind=EXIT_LIQ))
        pos.qty = ZERO
        pos.isolated_margin = ZERO
        pos.realized_pnl += gross - liq_fee
        pos.fees_paid += liq_fee
        pos.exit_fee += liq_fee
        pos.meta["gross_realized"] = format(D(pos.meta.get("gross_realized", 0)) + gross, "f")
        pos.meta["exit_fee_total"] = format(D(pos.meta.get("exit_fee_total", 0)) + liq_fee, "f")
        self.wallet_balance += gross - liq_fee
        self.total_fees += liq_fee
        self._entry(LedgerKind.PNL, gross, pos.id, "liquidation gross", ts)
        self._entry(LedgerKind.LIQ_FEE, -liq_fee, pos.id, "liquidation fee", ts)
        return self._finalize(pos, EXIT_LIQ, ts)

    def close_manual(self, symbol: str, price, reason: str = EXIT_MANUAL, now: datetime | None = None,
                     tick: TickData | None = None) -> TradeRecord | None:
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        ts = iso(now or utc_now())
        ref = D(price)
        fill = self.slippage.fill_price(ref, pos.side.close_side, tick, is_market=True)
        self._close_part(pos, fill, pos.qty, reason, ts, ref_price=ref)
        return self._finalize(pos, reason, ts)

    def close_partial(self, symbol: str, price, fraction, reason: str = "kısmi", now: datetime | None = None) -> TradeRecord | None:
        """Kalanın `fraction` kadarını kapat; tamamı kapanırsa TradeRecord döner."""
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        ts = iso(now or utc_now())
        ref = D(price)
        fill = self.slippage.fill_price(ref, pos.side.close_side, None, is_market=True)
        qty = quantize_qty(pos.qty * D(fraction), self._step(pos))
        if qty <= 0:
            return None
        self._close_part(pos, fill, qty, reason, ts, ref_price=ref)
        if pos.qty <= 0:
            return self._finalize(pos, reason, ts)
        return None

    # ------------------------------------------------------------------ değerleme / özet
    def unrealized(self, marks: Mapping[str, Any] | None = None) -> Decimal:
        marks = marks or {}
        total = ZERO
        for s, p in self.positions.items():
            raw = marks.get(s)
            px = TickData.coerce(raw).ref if raw is not None else (p.last_price or p.entry_avg)
            total += p.unrealized(px)
        return total

    def summary(self, marks: Mapping[str, Any] | None = None) -> dict:
        """Legacy anahtarlarla uyumlu özet (float değerler) + wallet_balance/available."""
        unreal = self.unrealized(marks)
        closed = self.history
        wins = [h for h in closed if h.pnl > 0]
        mtm = self.wallet_balance + unreal
        ret = (mtm / self.starting_equity * _HUNDRED - _HUNDRED) if self.starting_equity > 0 else ZERO
        avg_r = (sum((h.r_multiple for h in closed), ZERO) / len(closed)) if closed else ZERO
        return {"equity": round(float(self.wallet_balance), 4), "unrealized": round(float(unreal), 4),
                "equity_mtm": round(float(mtm), 4), "starting_equity": float(self.starting_equity),
                "return_pct": round(float(ret), 2), "open": len(self.positions), "closed": len(closed),
                "win_rate": round(100 * len(wins) / len(closed), 1) if closed else 0.0, "avg_r": round(float(avg_r), 3),
                "total_fees": round(float(self.total_fees), 4), "used_margin": round(float(self.used_margin), 4),
                "wallet_balance": round(float(self.wallet_balance), 4), "available": round(float(self.available), 4),
                "total_funding": round(float(self.total_funding), 4), "schema_version": self.schema_version}

    def history_dicts(self) -> list[dict]:
        """learning.py ve Obsidian notları için eski biçim (float) sözlükler."""
        return [h.to_legacy_dict() for h in self.history]

    # ------------------------------------------------------------------ kalıcılık
    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "kind": "futures", "updated_at": self.updated_at or iso(),
                "starting_equity": ser(self.starting_equity), "wallet_balance": ser(self.wallet_balance),
                "equity": ser(self.wallet_balance),
                "max_positions": self.max_positions, "fees": self.fees.to_dict(), "slippage": self.slippage.to_dict(),
                "liq_params": self.liq_params.to_dict(), "tax_policy": self.tax_policy.to_dict(),
                "tp1_fraction": ser(self.tp1_fraction), "allow_shrink": self.allow_shrink, "worst_case": self.worst_case,
                "tp_maker": self.tp_maker, "positions": {k: v.to_dict() for k, v in self.positions.items()},
                "history": [h.to_dict() for h in self.history], "entries": [e.to_dict() for e in self.entries],
                "total_fees": ser(self.total_fees), "total_funding": ser(self.total_funding), "seq": self.seq, "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict, **overrides) -> "FuturesLedgerV2":
        if int(d.get("schema_version", 1)) < 2 or "wallet_balance" not in d:
            return cls.import_legacy_ledger(d, **overrides)
        max_pos = int(overrides.pop("max_positions", d.get("max_positions", 3)))
        led = cls(d.get("starting_equity", 0), max_positions=max_pos,
                  fees=FeeSchedule.from_dict(d["fees"]) if d.get("fees") else None,
                  slippage=SlippageModel.from_dict(d["slippage"]) if d.get("slippage") else None,
                  liq_params=LiquidationParams(**{k: v for k, v in (d.get("liq_params") or {}).items()
                                                if k in ("liq_fee_pct", "fee_cushion_pct", "use_brackets")}) if d.get("liq_params") else None,
                  tax_policy=TaxPolicy.from_dict(d["tax_policy"]) if d.get("tax_policy") else None,
                  tp1_fraction=d.get("tp1_fraction", "0.5"), allow_shrink=bool(d.get("allow_shrink", False)),
                  worst_case=bool(d.get("worst_case", True)), tp_maker=bool(d.get("tp_maker", False)))
        for k, v in overrides.items():
            setattr(led, k, v)
        led.wallet_balance = D(d.get("wallet_balance", d.get("equity", led.starting_equity)))
        led.positions = {k: Position.from_dict(v) for k, v in (d.get("positions") or {}).items()}
        led.history = [TradeRecord.from_dict(h) for h in d.get("history", [])]
        led.entries = [LedgerEntry.from_dict(e) for e in d.get("entries", [])]
        led.total_fees = D(d.get("total_fees", 0))
        led.total_funding = D(d.get("total_funding", 0))
        led.seq = int(d.get("seq", 0))
        led.updated_at = d.get("updated_at", "")
        led.meta = dict(d.get("meta") or {})
        return led

    @classmethod
    def import_legacy_ledger(cls, d: dict, **overrides) -> "FuturesLedgerV2":
        """paper_futures.FuturesLedger JSON'u (v1) → V2. Equity ve geçmiş aynen korunur."""
        starting = D(d.get("starting_equity", d.get("equity", 0)))
        led = cls(starting, **{k: v for k, v in overrides.items() if k in ("max_positions", "fees", "slippage", "brackets",
                                                                          "liq_params", "funding", "tax_policy", "tp1_fraction",
                                                                          "allow_shrink", "worst_case", "tp_maker")})
        led.wallet_balance = D(d.get("equity", starting))
        led.total_fees = D(d.get("total_fees", 0))
        led.seq = int(d.get("seq", 0))
        led.updated_at = d.get("updated_at", "")
        led.meta = {"imported_from": "futures_ledger_v1", "imported_at": iso()}
        for h in d.get("history", []) or []:
            led.history.append(TradeRecord.from_legacy(h))
        for sym, p in (d.get("positions") or {}).items():
            side = _side(p.get("side", "LONG"))
            qty = D(p.get("units", 0))
            entry = D(p.get("entry", 0))
            margin = D(p.get("margin", 0))
            stop = D(p["stop"]) if p.get("stop") else None
            tgts = [D(p[k]) for k in ("target1", "target2") if p.get(k)]
            funding = D(p.get("funding", 0))
            fees_ = D(p.get("fees", 0))
            feats = dict(p.get("features") or {})
            init_units = D(feats.get("initial_units") or qty)
            init_stop = D(feats["initial_stop"]) if feats.get("initial_stop") else stop
            pos = Position(id=str(p.get("id", f"F{led.seq:05d}")), symbol=sym, market_type=MarketType.USDM_PERP, side=side, qty=qty,
                           entry_avg=entry, leverage=int(p.get("leverage", 1)), isolated_margin=margin,
                           realized_pnl=D(p.get("realized", 0)), fees_paid=fees_, funding_paid=max(-funding, ZERO),
                           funding_received=max(funding, ZERO), opened_at=p.get("opened_at", ""), stop=stop, targets=tgts,
                           targets_hit=1 if p.get("tp1_done") else 0, tp1_done=bool(p.get("tp1_done", False)),
                           initial_qty=init_units, initial_stop=init_stop, mae_pct=D(p.get("mae_pct", 0)), mfe_pct=D(p.get("mfe_pct", 0)),
                           bars_held=int(p.get("bars_held", 0)), last_funding_settlement_utc=p.get("last_funding_at", "") or p.get("opened_at", ""),
                           last_price=D(p.get("last_price", entry) or entry), entry_fee=fees_ if not p.get("tp1_done") else ZERO,
                           requested_notional=D(p.get("notional", 0)), requested_margin=margin, setup_type=p.get("setup_type", ""),
                           trigger_text=p.get("trigger_text", ""), features=feats,
                           meta={"legacy": True, "gross_realized": format(D(p.get("realized", 0)) + fees_, "f"),
                                 "exit_fee_total": "0", "exit_slippage": "0", "initial_margin": format(margin, "f")})
            pos.liquidation_price = led._liq_price(pos)
            led.positions[sym] = pos
        return led

    def save(self, path: Path | str, *, keep_backup: bool = True) -> None:
        self.updated_at = iso()
        atomic_write_json(path, self.to_dict(), indent=1, keep_backup=keep_backup)

    @classmethod
    def load(cls, path: Path | str, starting_equity=None, **kwargs) -> "FuturesLedgerV2":
        p = Path(path)
        if not p.exists():
            if starting_equity is None:
                raise StorageError(f"defter yok ve starting_equity verilmedi: {p}")
            return cls(starting_equity, **kwargs)
        d = read_json(p)
        if not isinstance(d, dict):
            raise StorageError(f"beklenmeyen defter içeriği: {p}")
        return cls.from_dict(d, **kwargs)


__all__ = ["FuturesLedgerV2", "R_OK", "R_ALREADY_OPEN", "R_MAX_POSITIONS", "R_ZERO_QTY", "R_MIN_QTY", "R_MAX_QTY", "R_MIN_NOTIONAL",
           "R_INSUFFICIENT_MARGIN", "R_LEVERAGE", "R_BAD_PRICE", "R_BAD_STOP", "EXIT_STOP", "EXIT_BE_STOP", "EXIT_TP1", "EXIT_TP2",
           "EXIT_LIQ", "EXIT_MANUAL"]
