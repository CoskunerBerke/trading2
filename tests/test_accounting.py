"""tradingbot.accounting testleri — Decimal kesinlik, komisyon/funding/likidasyon/vergi ayrımı, spot FIFO, legacy import."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal


from tradingbot.accounting import (
    AmountType,
    FeeSchedule,
    FundingSchedule,
    FuturesLedgerV2,
    LiquidationParams,
    OrderStatus,
    OrderType,
    PositionSide,
    Side,
    SizeSpec,
    SlippageModel,
    SpotLedger,
    SymbolFilters,
    TaxPolicy,
    TickData,
    TradeRecord,
    LEGACY_TRADE_KEYS,
    R_INSUFFICIENT_MARGIN,
    R_MIN_NOTIONAL,
    R_ZERO_QTY,
    EXIT_STOP,
    EXIT_BE_STOP,
    EXIT_LIQ,
    liquidation_price,
    simple_liq,
    liquidation_buffer_pct,
    quantize_order,
    tax_rows,
    vwap_estimate,
)
from tradingbot.accounting.models import MarketType

D = Decimal
UTC = timezone.utc
ETH = "ETH/USDT"


def _led(equity="50", **kw) -> FuturesLedgerV2:
    return FuturesLedgerV2(equity, slippage=SlippageModel.zero(), **kw)


def _f(step="0.001", tick="0.01", min_notional="5", max_lev=20) -> SymbolFilters:
    return SymbolFilters(symbol=ETH, market_type=MarketType.USDM_PERP, price_tick=D(tick), qty_step=D(step), min_qty=D(step),
                         min_notional=D(min_notional), max_leverage=max_lev)


T0 = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


# ----------------------------------------------------------------------------- fee schedule
def test_fee_schedule_defaults_and_fee_on_notional():
    fut = FeeSchedule.futures_default()
    assert fut.maker_pct == D("0.02") and fut.taker_pct == D("0.05")
    spot = FeeSchedule.spot_default()
    assert spot.maker_pct == D("0.10") and spot.taker_pct == D("0.10")
    assert fut.fee(D("48"), is_maker=False) == D("0.024")
    assert fut.fee(D("48"), is_maker=True) == D("0.0096")
    assert FeeSchedule.from_dict(fut.to_dict()).taker_pct == fut.taker_pct


# ----------------------------------------------------------------------------- 50 USDT / 2x worked example
def test_notional_semantics_exact_numbers():
    led = _led()
    pos = led.open(ETH, "LONG", 3000, SizeSpec(50, AmountType.NOTIONAL, leverage=2), stop=2900, targets=[3100, 3200], filters=_f(), now=T0)
    assert pos is not None, led.last_reject_reason
    assert pos.qty == D("0.016")
    assert pos.notional == D("48")
    assert pos.isolated_margin == D("24")
    assert pos.entry_fee == D("0.024")
    assert led.wallet_balance == D("50") - D("0.024")
    assert led.used_margin == D("24") and led.available == D("50") - D("0.024") - D("24")
    rec = led.close_manual(ETH, 3060, now=T0 + timedelta(hours=1))
    assert rec.exit_fee == D("0.02448")
    assert rec.gross_pnl == D("0.96")
    assert rec.net_pnl == D("0.91152") and rec.pnl == D("0.91152")
    assert rec.fees == D("0.024") + D("0.02448")
    assert rec.funding == 0 and rec.tax_estimate == 0
    assert led.wallet_balance == D("50") + D("0.91152")
    assert led.used_margin == 0

    led2 = _led()
    led2.open(ETH, "LONG", 3000, SizeSpec(50, AmountType.NOTIONAL, leverage=2), stop=2900, filters=_f(), now=T0)
    rec2 = led2.close_manual(ETH, 2940)
    assert rec2.exit_fee == D("0.02352")
    assert rec2.net_pnl == D("-1.00752")


def test_margin_semantics_exact_numbers():
    led = _led()
    pos = led.open(ETH, "LONG", 3000, SizeSpec(50, AmountType.MARGIN, leverage=2), stop=2900, filters=_f(), now=T0)
    assert pos is not None, led.last_reject_reason      # 49.5 marj + 0.0495 ücret ≤ 50 cüzdan → OK
    assert pos.qty == D("0.033")
    assert pos.notional == D("99")
    assert pos.isolated_margin == D("49.5")
    assert pos.entry_fee == D("0.0495")
    assert pos.requested_margin == D("50") and pos.amount_type is AmountType.MARGIN
    rec = led.close_manual(ETH, 3060)
    assert rec.exit_fee == D("0.05049")
    assert rec.gross_pnl == D("1.98")
    assert rec.net_pnl == D("1.88001")
    assert rec.effective_margin == D("49.5") and rec.effective_notional == D("99")
    led2 = _led()
    led2.open(ETH, "LONG", 3000, SizeSpec(50, AmountType.MARGIN, leverage=2), stop=2900, filters=_f())
    assert led2.close_manual(ETH, 2940).net_pnl == D("-2.07801")


def test_insufficient_margin_no_silent_shrink():
    led = _led()
    assert led.open(ETH, "LONG", 3000, SizeSpec(200, AmountType.NOTIONAL, leverage=1), stop=2900, filters=_f()) is None
    assert led.last_reject_reason == R_INSUFFICIENT_MARGIN
    assert not led.positions and led.wallet_balance == D("50")
    # allow_shrink=True ise sığacak kadar küçültür
    led3 = _led(allow_shrink=True)
    p = led3.open(ETH, "LONG", 3000, SizeSpec(200, AmountType.NOTIONAL, leverage=1), stop=2900, filters=_f())
    assert p is not None and p.isolated_margin + p.entry_fee <= D("50")


def test_step_tick_min_notional_rejections():
    led = _led()
    # step 1 → qty 0.0166 → 0 → STEP_ZERO_QTY
    assert led.open(ETH, "LONG", 3000, SizeSpec(50, AmountType.NOTIONAL, 2), filters=_f(step="1")) is None
    assert led.last_reject_reason == R_ZERO_QTY
    # min_notional 100 > 48 → MIN_NOTIONAL
    assert led.open(ETH, "LONG", 3000, SizeSpec(50, AmountType.NOTIONAL, 2), filters=_f(min_notional="100")) is None
    assert led.last_reject_reason == R_MIN_NOTIONAL
    # tick: fiyat 3000.005 tick 0.01 → giriş aggressive yuvarlanır (LONG yukarı) → 3000.01
    p = led.open(ETH, "LONG", D("3000.005"), SizeSpec(50, AmountType.NOTIONAL, 2), filters=_f())
    assert p is not None and p.entry_avg == D("3000.01")
    # quantize_order helper
    q, px, ok, why = quantize_order(_f(), D("0.0169"), D("3000"), Side.BUY)
    assert q == D("0.016") and ok
    q, px, ok, why = quantize_order(_f(step="0.01"), D("0.001"), D("3000"), Side.BUY)
    assert not ok and why == "ZERO_QTY"


# ----------------------------------------------------------------------------- funding
def test_funding_sign_and_all_missed_settlements():
    open_at = datetime(2026, 8, 18, 7, 59, tzinfo=UTC)
    tick_at = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
    rate = D("0.0001")
    lookup = lambda sym, when: rate
    for side, sign in (("LONG", -1), ("SHORT", 1)):
        led = _led()
        pos = led.open(ETH, side, 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2000 if side == "LONG" else 4000, filters=_f(), now=open_at)
        wallet_before = led.wallet_balance
        closed = led.tick({ETH: TickData(last=3000, mark=3000)}, now_utc=tick_at, funding_rate_lookup=lookup)
        assert not closed
        expected_one = D("0.016") * 3000 * rate     # 0.0048
        # 4 settlement: 08:00, 16:00, 00:00, 08:00
        assert led.wallet_balance - wallet_before == sign * expected_one * 4
        if side == "LONG":
            assert pos.funding_paid == expected_one * 4 and pos.funding_received == 0
        else:
            assert pos.funding_received == expected_one * 4 and pos.funding_paid == 0
        assert pos.last_funding_settlement_utc == "2026-08-19T08:00:00+00:00"
        # tekrar tik → yeni settlement yok
        led.tick({ETH: 3000}, now_utc=tick_at + timedelta(minutes=5), funding_rate_lookup=lookup)
        assert led.wallet_balance - wallet_before == sign * expected_one * 4
        rec = led.close_manual(ETH, 3000)
        assert rec.funding == sign * expected_one * 4
        assert rec.fees == pos.entry_fee + rec.exit_fee and rec.tax_estimate == 0
        assert rec.net_pnl == rec.gross_pnl - rec.fees + rec.funding


def test_funding_fallback_estimated_rate():
    open_at = datetime(2026, 8, 18, 7, 59, tzinfo=UTC)
    led = _led()
    pos = led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2000, filters=_f(), now=open_at)
    calls = {"n": 0}

    def lookup(sym, when):
        calls["n"] += 1
        return D("0.0002") if calls["n"] == 1 else None      # sonrakiler bilinmiyor → son bilinen oran (tahmini)

    events = FundingSchedule().accrue(pos, datetime(2026, 8, 19, 9, 0, tzinfo=UTC), D("3000"), lookup)
    assert len(events) == 4 and events[0].estimated is False and all(e.estimated for e in events[1:])
    assert all(e.rate == D("0.0002") for e in events)


# ----------------------------------------------------------------------------- liquidation
def test_liquidation_price_long_short_and_simple():
    lp = liquidation_price("LONG", 3000, D("0.016"), 24, mmr=D("0.004"))
    assert abs(lp - D("1506.02")) < D("0.01")
    sp = liquidation_price("SHORT", 3000, D("0.016"), 24, mmr=D("0.004"))
    assert abs(sp - D("4482.07")) < D("0.01")
    assert abs(simple_liq("LONG", 3000, 2, D("0.004")) - lp) < D("0.001")
    assert liquidation_buffer_pct("LONG", 3000, lp) > 49
    led = _led()
    pos = led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=1000, filters=_f())
    assert abs(pos.liquidation_price - D("1506.02")) < D("0.01")


def test_liquidation_event_clamps_loss_and_charges_fee():
    led = _led(liq_params=LiquidationParams(liq_fee_pct=D("0.5")))
    pos = led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=1000, filters=_f(), now=T0)   # stop liq'in altında
    margin, entry_fee, liq = pos.isolated_margin, pos.entry_fee, pos.liquidation_price
    closed = led.tick({ETH: TickData(last=1400, mark=1400, low=1350)}, now_utc=T0 + timedelta(hours=1))
    assert len(closed) == 1 and closed[0].exit_reason == EXIT_LIQ
    rec = closed[0]
    assert rec.exit_fee > 0 and rec.exit_fee == D("0.016") * liq * D("0.005")
    # toplam kayıp marjı aşmaz: cüzdan = başlangıç − giriş ücreti − marj
    assert led.wallet_balance == D("50") - entry_fee - margin
    assert rec.gross_pnl - rec.exit_fee == -margin
    assert rec.net_pnl == -margin - entry_fee
    assert rec.costs["liq_clamped"] is True
    assert led.used_margin == 0 and not led.positions


def test_short_liquidation_mirror():
    led = _led()
    pos = led.open(ETH, "SHORT", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=6000, filters=_f(), now=T0)
    assert abs(pos.liquidation_price - D("4482.07")) < D("0.01")
    closed = led.tick({ETH: TickData(last=4500, high=4600)}, now_utc=T0 + timedelta(hours=1))
    assert closed and closed[0].exit_reason == EXIT_LIQ
    assert led.wallet_balance == D("50") - pos.entry_fee - D("24")


# ----------------------------------------------------------------------------- tick priorities
def test_same_tick_stop_and_target_prefers_stop():
    led = _led()
    led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2950, targets=[3050, 3100], filters=_f(), now=T0)
    closed = led.tick({ETH: TickData(last=3000, high=3120, low=2940)}, now_utc=T0 + timedelta(hours=1))
    assert len(closed) == 1 and closed[0].exit_reason == EXIT_STOP
    assert closed[0].exit_price == D("2950")


def test_stop_gap_through_fills_at_mark():
    led = _led()
    led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2950, filters=_f(), now=T0)
    closed = led.tick({ETH: TickData(last=2900, mark=2900)}, now_utc=T0 + timedelta(hours=1))
    assert closed[0].exit_price == D("2900")


def test_tp1_partial_then_true_break_even():
    led = _led()
    pos = led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900, targets=[3100, 3300], filters=_f(), now=T0)
    closed = led.tick({ETH: TickData(last=3100, high=3105)}, now_utc=T0 + timedelta(hours=1), bar_advance=True)
    assert not closed and pos.tp1_done and pos.targets_hit == 1
    assert pos.qty == D("0.008") and pos.isolated_margin == D("12")
    be = pos.stop
    assert be > D("3000")                                   # gerçek başabaş > giriş (komisyonlar)
    assert be < D("3005")
    # BE'de stop → kalan kısmın net PnL'i ≈ 0 (yukarı yuvarlama nedeniyle ≥ 0)
    wallet_before = led.wallet_balance
    closed = led.tick({ETH: TickData(last=be, low=be - 1)}, now_utc=T0 + timedelta(hours=2), bar_advance=True)
    assert len(closed) == 1 and closed[0].exit_reason == EXIT_BE_STOP
    rec = closed[0]
    remaining_entry_fee = pos.entry_fee / 2
    second_leg_net = (be - 3000) * D("0.008") - rec.fills[-1].fee - remaining_entry_fee
    assert D("0") <= second_leg_net < D("0.001")
    assert rec.tp1_done and rec.bars_held == 2 and rec.net_pnl > 0
    assert rec.mfe_pct > 3 and rec.mae_pct <= 0
    # legacy anahtarlar
    ld = rec.to_legacy_dict()
    for k in LEGACY_TRADE_KEYS:
        assert k in ld
    assert isinstance(ld["pnl"], float) and ld["tp1_done"] is True


def test_max_positions_and_already_open():
    led = _led(max_positions=1)
    assert led.open(ETH, "LONG", 3000, SizeSpec(10, AmountType.NOTIONAL, 2), stop=2900, filters=_f())
    assert led.open(ETH, "LONG", 3000, SizeSpec(10, AmountType.NOTIONAL, 2), stop=2900, filters=_f()) is None
    assert led.last_reject_reason == "ALREADY_OPEN"
    assert led.open("BTC/USDT", "LONG", 60000, SizeSpec(10, AmountType.NOTIONAL, 2), stop=59000) is None
    assert led.last_reject_reason == "MAX_POSITIONS"


# ----------------------------------------------------------------------------- persistence / legacy
def test_futures_save_load_decimal_exact_and_summary(tmp_path):
    led = _led()
    led.open(ETH, "LONG", 3000, SizeSpec(50, AmountType.NOTIONAL, 2), stop=2900, targets=[3100, 3200], filters=_f(), now=T0)
    led.close_manual(ETH, 3060)
    led.open("BTC/USDT", "SHORT", D("60000.1"), SizeSpec(D("30.5"), AmountType.NOTIONAL, 3), stop=61000, filters=SymbolFilters("BTC/USDT", qty_step=D("0.0001"), min_qty=D("0.0001")))
    assert "BTC/USDT" in led.positions, led.last_reject_reason
    p = tmp_path / "fut.json"
    led.save(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    l2 = FuturesLedgerV2.load(p)
    assert l2.wallet_balance == led.wallet_balance and isinstance(l2.wallet_balance, Decimal)
    assert l2.history[0].net_pnl == D("0.91152")
    bp = l2.positions["BTC/USDT"]
    assert bp.entry_avg == D("60000.1") and bp.qty == led.positions["BTC/USDT"].qty and bp.side is PositionSide.SHORT
    s = l2.summary({"BTC/USDT": 60000})
    for k in ("equity", "unrealized", "equity_mtm", "starting_equity", "return_pct", "open", "closed", "win_rate", "avg_r", "total_fees", "used_margin"):
        assert k in s
    assert "wallet_balance" in s and "available" in s
    assert s["open"] == 1 and s["closed"] == 1 and s["win_rate"] == 100.0
    json.dumps(s)   # JSON uyumlu (float)


def test_import_legacy_futures_ledger_preserves_equity_and_history():
    legacy = {"equity": 47.5, "starting_equity": 50.0, "updated_at": "2026-08-17T21:54:40+00:00", "total_fees": 0.12, "seq": 3,
              "positions": {ETH: {"id": "F00003", "symbol": ETH, "side": "LONG", "entry": 3000.0, "units": 0.016, "notional": 48.0, "leverage": 2,
                                  "margin": 24.0, "stop": 2900.0, "target1": 3100.0, "target2": 3200.0, "opened_at": "2026-08-17T20:00:00+00:00",
                                  "setup_type": "kırılım", "trigger_text": "4h", "features": {"initial_stop": 2900.0, "initial_units": 0.016},
                                  "tp1_done": False, "realized": 0.0, "fees": 0.024, "funding": 0.0, "mae_pct": -0.5, "mfe_pct": 1.2,
                                  "last_price": 3010.0, "last_funding_at": "2026-08-17T20:00:00+00:00", "bars_held": 2}},
              "history": [{"id": "F00001", "symbol": ETH, "side": "SHORT", "entry": 3100.0, "exit_reason": "stop", "closed_at": "2026-08-16T10:00:00+00:00",
                           "opened_at": "2026-08-16T02:00:00+00:00", "pnl": -1.5, "fees": 0.05, "funding": 0.01, "r_multiple": -1.0, "mae_pct": -3.0,
                           "mfe_pct": 0.4, "bars_held": 2, "leverage": 3, "setup_type": "geri çekilme", "trigger_text": "x", "features": {"a": 1}, "tp1_done": False}]}
    led = FuturesLedgerV2.import_legacy_ledger(legacy)
    assert led.wallet_balance == D("47.5") and led.starting_equity == D("50")
    assert led.seq == 3 and led.total_fees == D("0.12")
    h = led.history[0]
    assert isinstance(h, TradeRecord) and h.pnl == D("-1.5") and h.fees == D("0.05") and h.funding == D("0.01")
    ld = h.to_legacy_dict()
    for k in LEGACY_TRADE_KEYS:
        assert k in ld
    assert ld["features"] == {"a": 1} and ld["r_multiple"] == -1.0
    pos = led.positions[ETH]
    assert pos.qty == D("0.016") and pos.isolated_margin == D("24") and pos.stop == D("2900") and pos.targets == [D("3100"), D("3200")]
    assert pos.bars_held == 2 and pos.liquidation_price is not None
    s = led.summary({ETH: 3010})
    assert s["equity"] == 47.5 and s["used_margin"] == 24.0 and s["open"] == 1
    # dosyadan yüklerken schema_version yoksa legacy import
    d2 = FuturesLedgerV2.from_dict(legacy)
    assert d2.wallet_balance == D("47.5")


# ----------------------------------------------------------------------------- tax
def test_tax_disabled_zero_enabled_confirmed_applied():
    led = _led()
    led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900, filters=_f())
    rec = led.close_manual(ETH, 3060)
    assert rec.tax_estimate == 0
    pol = TaxPolicy()
    assert pol.status == "UNVERIFIED_OR_NOT_EFFECTIVE" and pol.estimate(D("100")) == 0
    assert TaxPolicy(enabled=True, rate_pct=20).estimate(D("100")) == 0            # onaysız → 0
    active = TaxPolicy(enabled=True, manually_confirmed=True, rate_pct=20, version="test-v1")
    assert active.estimate(D("100")) == D("20") and active.estimate(D("-5")) == 0
    led2 = FuturesLedgerV2(50, tax_policy=active)
    led2.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900, filters=_f())
    r2 = led2.close_manual(ETH, 3060)
    assert r2.tax_estimate == r2.net_pnl * D("0.2")
    assert r2.net_pnl == D("0.91152")           # vergi net_pnl'den ayrı
    rows = tax_rows([r2], active)
    row = rows[0]
    assert row.tax_policy_version == "test-v1" and row.timestamp_istanbul and row.tax_estimate == r2.tax_estimate
    assert row.timestamp_istanbul != row.timestamp_utc


# ----------------------------------------------------------------------------- spot ledger
SPOT_F = SymbolFilters(symbol=ETH, market_type=MarketType.SPOT, price_tick=D("0.01"), qty_step=D("0.0001"), min_qty=D("0.0001"), min_notional=D("5"), max_leverage=1)


def _spot(cash="1000") -> SpotLedger:
    return SpotLedger(cash, fees=FeeSchedule.spot_default(), slippage=SlippageModel.zero(), filters_lookup=lambda s: SPOT_F)


def test_spot_fifo_two_buys_one_sell_and_no_shorting():
    led = _spot()
    o1 = led.market_buy(ETH, qty=D("0.1"), tick=TickData(last=3000), now=T0)
    assert o1.status is OrderStatus.FILLED
    o2 = led.market_buy(ETH, qty=D("0.1"), tick=TickData(last=3200), now=T0 + timedelta(minutes=1))
    assert o2.status is OrderStatus.FILLED
    assert led.qty(ETH) == D("0.2")
    fee1, fee2 = D("300") * D("0.001"), D("320") * D("0.001")
    assert led.cash == D("1000") - D("300") - fee1 - D("320") - fee2
    assert led.avg_cost(ETH) == D("3100")
    # 0.15 sat @ 3300 → FIFO: 0.1@3000 + 0.05@3200
    o3 = led.market_sell(ETH, qty=D("0.15"), tick=TickData(last=3300), now=T0 + timedelta(minutes=2))
    assert o3.status is OrderStatus.FILLED
    rec = led.history[-1]
    proceeds = D("0.15") * 3300
    cost = D("0.1") * 3000 + D("0.05") * 3200
    sell_fee = proceeds * D("0.001")
    buy_fees = D("0.1") * (fee1 / D("0.1")) + D("0.05") * (fee2 / D("0.1"))
    assert rec.gross_pnl == proceeds - cost
    assert rec.net_pnl == proceeds - cost - buy_fees - sell_fee
    assert rec.costs["fifo_lots"][0]["cost_basis"] == "3000" and len(rec.costs["fifo_lots"]) == 2
    assert led.qty(ETH) == D("0.05") and led.lots[ETH][0].cost_basis == D("3200")
    # açığa satış reddi
    bad = led.market_sell(ETH, qty=D("1"), tick=TickData(last=3300))
    assert bad.status is OrderStatus.REJECTED and bad.reject_reason == "INSUFFICIENT_ASSET"
    none = led.market_sell("BTC/USDT", qty=D("0.01"), tick=TickData(last=60000), filters=SymbolFilters("BTC/USDT", market_type=MarketType.SPOT, qty_step=D("0.0001"), min_qty=D("0.0001")))
    assert none.status is OrderStatus.REJECTED and none.reject_reason == "NO_SHORTING"
    # unrealized net of est. exit fee
    u = led.unrealized(ETH, 3400)
    assert u == (D("3400") - 3200) * D("0.05") - D("0.05") * (fee2 / D("0.1")) - D("0.05") * 3400 * D("0.001")


def test_spot_limit_fill_on_cross_and_locks():
    led = _spot()
    o = led.place_order(ETH, Side.BUY, OrderType.LIMIT, qty=D("0.1"), price=2900, tick=TickData(last=3000), now=T0)
    assert o.status is OrderStatus.ACKNOWLEDGED
    assert led.locked_cash == D("0.1") * 2900 * D("1.001") and led.cash == D("1000") - led.locked_cash
    assert not led.tick({ETH: TickData(last=2950, low=2920)})
    filled = led.tick({ETH: TickData(last=2930, low=2890)}, now=T0 + timedelta(hours=1))
    assert filled and o.status is OrderStatus.FILLED and o.avg_fill_price == D("2900")
    assert led.locked_cash == 0 and led.qty(ETH) == D("0.1")
    assert led.cash == D("1000") - D("290") - D("290") * D("0.001")
    # iptal kilidi bırakır
    o2 = led.place_order(ETH, Side.BUY, OrderType.LIMIT, qty=D("0.1"), price=2000, tick=TickData(last=2930))
    assert led.locked_cash > 0
    led.cancel_order(o2.id)
    assert led.locked_cash == 0 and o2.status is OrderStatus.CANCELED


def test_spot_stop_gap_through_and_partial_fill():
    led = _spot()
    led.market_buy(ETH, qty=D("0.1"), tick=TickData(last=3000), now=T0)
    stop = led.place_order(ETH, Side.SELL, OrderType.STOP_MARKET, qty=D("0.1"), stop_price=2900)
    assert stop.status is OrderStatus.ACKNOWLEDGED and led.free_asset("ETH") == 0 and led.locked_assets["ETH"] == D("0.1")
    filled = led.tick({ETH: TickData(last=2850, low=2840)}, now=T0 + timedelta(hours=1))   # gap: last < stop → 2850
    assert filled and stop.status is OrderStatus.FILLED and stop.avg_fill_price == D("2850")
    assert led.history[-1].exit_reason == "stop" and led.qty(ETH) == 0
    # kısmi fill
    led2 = _spot()
    o = led2.place_order(ETH, Side.BUY, OrderType.LIMIT, qty=D("0.1"), price=2900, tick=TickData(last=3000))
    led2.fill_order(o.id, 2900, qty=D("0.04"))
    assert o.status is OrderStatus.PARTIALLY_FILLED and o.filled_qty == D("0.04") and led2.qty(ETH) == D("0.04")
    led2.fill_order(o.id, 2900)
    assert o.status is OrderStatus.FILLED and led2.locked_cash == 0


def test_spot_oco_and_trailing():
    led = _spot()
    led.market_buy(ETH, qty=D("0.1"), tick=TickData(last=3000), now=T0)
    oco = led.place_order(ETH, Side.SELL, OrderType.OCO, qty=D("0.05"), price=3200, oco_stop_price=2900)
    trail = led.place_order(ETH, Side.SELL, OrderType.STOP_MARKET, qty=D("0.05"), trailing_pct=D("2"), tick=TickData(last=3000))
    assert oco.status is OrderStatus.ACKNOWLEDGED and trail.stop_price == D("2940")
    led.tick({ETH: TickData(last=3100, high=3150, low=3050)})
    assert trail.stop_price == D("3150") * D("0.98") and trail.status is OrderStatus.ACKNOWLEDGED
    led.tick({ETH: TickData(last=3210, high=3220, low=3100)})
    assert oco.status is OrderStatus.FILLED and oco.avg_fill_price == D("3200")
    led.tick({ETH: TickData(last=3000, high=3010, low=2990)})
    assert trail.status is OrderStatus.FILLED and led.qty(ETH) == 0


def test_spot_save_load_and_legacy_import(tmp_path):
    led = _spot()
    led.market_buy(ETH, qty=D("0.1"), tick=TickData(last=D("3000.5")), now=T0)
    led.place_order(ETH, Side.SELL, OrderType.STOP_MARKET, qty=D("0.1"), stop_price=2900)
    p = tmp_path / "spot.json"
    led.save(p)
    l2 = SpotLedger.load(p)
    assert l2.cash == led.cash and isinstance(l2.cash, Decimal) and l2.lots[ETH][0].cost_basis == D("3000.5")
    assert len(l2.open_orders) == 1 and l2.locked_assets["ETH"] == D("0.1")
    legacy = {"cash": 700.0, "starting_equity": 1000.0, "updated_at": "x",
              "positions": {ETH: {"symbol": ETH, "units": 0.1, "entry_price": 3000.0, "entry_time": "2026-08-17T10:00:00+00:00", "stop": 2900.0, "strategy": "trend"}},
              "history": [{"symbol": "BTC/USDT", "entry_time": "a", "exit_time": "b", "entry_price": 60000.0, "exit_price": 61000.0, "units": 0.001,
                           "pnl": 0.9, "pnl_pct": 1.5, "reason": "stop", "strategy": "trend"}]}
    l3 = SpotLedger.import_legacy_portfolio(legacy)
    assert l3.cash == D("700") and l3.qty(ETH) == D("0.1") and l3.equity() == D("700") + D("0.1") * 3000
    assert l3.positions()[ETH]["stop"] == 2900.0 and l3.positions()[ETH]["strategy"] == "trend"
    assert l3.history[0].pnl == D("0.9") and l3.history[0].symbol == "BTC/USDT"
    assert (tmp_path / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
    l4 = SpotLedger.load(tmp_path / "legacy.json")
    assert l4.cash == D("700")


# ----------------------------------------------------------------------------- slippage
def test_slippage_and_vwap():
    m = SlippageModel(fixed_bps=3)
    assert m.fill_price(3000, "BUY") == D("3000") * (1 + D("0.0003"))
    assert m.fill_price(3000, "SELL") == D("3000") * (1 - D("0.0003"))
    assert m.fill_price(3000, "BUY", is_market=False) == 3000
    ms = SlippageModel(fixed_bps=0, spread_half=True)
    assert ms.fill_price(3000, "BUY", TickData(last=3000, bid=2999, ask=3001)) == D("3001")
    vwap, filled = vwap_estimate([(D("100"), D("1")), (D("101"), D("1"))], D("1.5"))
    assert filled == D("1.5") and vwap == (D("100") + D("50.5")) / D("1.5")
    led = FuturesLedgerV2(50, slippage=SlippageModel(fixed_bps=3))
    pos = led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900, filters=_f())
    assert pos.entry_avg == D("3000.9") and pos.slippage_cost == D("0.9") * pos.qty
