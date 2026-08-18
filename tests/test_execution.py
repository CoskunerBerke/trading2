"""tradingbot.execution testleri — durum makinesi, clientOrderId, PaperGateway, testnet kapıları, LiveGateway kilidi, reconcile."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradingbot.accounting import (
    AmountType,
    FeeSchedule,
    FuturesLedgerV2,
    MarketType,
    Order,
    OrderStatus,
    OrderType,
    Side,
    SlippageModel,
    SpotLedger,
    SymbolFilters,
    TickData,
)
from tradingbot.core import ExecutionDisabledError, stable_id
from tradingbot.execution import (
    CLIENT_ID_RE,
    BinanceFuturesTestnetGateway,
    BinanceSpotTestnetGateway,
    IllegalTransitionError,
    LiveGateway,
    OrderStateMachine,
    PaperGateway,
    ReconcileReport,
    live_confirm_token,
    make_client_order_id,
    reconcile,
    sign_hmac_sha256,
    valid_client_order_id,
)

D = Decimal
S = OrderStatus
ETH = "ETH/USDT"
T0 = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
FUT_F = SymbolFilters(symbol=ETH, market_type=MarketType.USDM_PERP, qty_step=D("0.001"), min_qty=D("0.001"))
SPOT_F = SymbolFilters(symbol=ETH, market_type=MarketType.SPOT, qty_step=D("0.0001"), min_qty=D("0.0001"), max_leverage=1)


def _order(cid="tb-p-brk-ETHUSDT-abcdef12-e-01", market=MarketType.USDM_PERP, side=Side.BUY, otype=OrderType.MARKET, qty="0.016",
           status=S.RISK_APPROVED, **meta) -> Order:
    o = Order(id=cid, client_order_id=cid, symbol=ETH, market_type=market, side=side, order_type=otype, qty=D(qty), status=S.CREATED)
    o.meta.update(meta)
    if status is S.RISK_APPROVED:
        OrderStateMachine.transition(o, S.RISK_APPROVED, "test")
    return o


# ----------------------------------------------------------------------------- state machine
def test_state_machine_legal_path_and_illegal_raises():
    o = _order(status=S.CREATED)
    sm = OrderStateMachine
    for st in (S.RISK_APPROVED, S.SUBMITTING, S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED):
        sm.transition(o, st)
    assert o.status is S.FILLED and len(o.events) == 5 and o.events[-1]["from"] == "PARTIALLY_FILLED"
    with pytest.raises(IllegalTransitionError):
        sm.transition(o, S.CANCELED)                     # FILLED terminal
    o2 = _order(status=S.CREATED)
    with pytest.raises(IllegalTransitionError):
        sm.transition(o2, S.FILLED)                      # CREATED → FILLED yasadışı
    with pytest.raises(IllegalTransitionError):
        sm.transition(o2, S.SUBMITTING)                  # RISK_APPROVED atlanamaz
    # UNKNOWN → RECONCILING → FILLED ; ACK → CANCEL_REQUESTED → CANCELED
    o3 = _order()
    sm.transition(o3, S.SUBMITTING); sm.transition(o3, S.UNKNOWN)
    with pytest.raises(IllegalTransitionError):
        sm.transition(o3, S.FILLED)
    sm.transition(o3, S.RECONCILING); sm.transition(o3, S.FILLED)
    o4 = _order()
    sm.transition(o4, S.SUBMITTING); sm.transition(o4, S.ACKNOWLEDGED); sm.transition(o4, S.CANCEL_REQUESTED); sm.transition(o4, S.CANCELED)
    assert sm.can(S.SUBMITTING, S.REJECTED) and not sm.can(S.REJECTED, S.SUBMITTING)


# ----------------------------------------------------------------------------- client order id
def test_client_order_id_format_and_determinism():
    a = make_client_order_id("paper", "breakout", "ETH/USDT", "abcdef12", "entry", 1)
    b = make_client_order_id("paper", "breakout", "ETH/USDT", "abcdef12", "entry", 1)
    assert a == b == "tb-p-breako-ETHUSDT-abcdef12-e-01"
    assert len(a) <= 36 and CLIENT_ID_RE.match(a) and valid_client_order_id(a)
    c = make_client_order_id("testnet", "mean_reversion_long", "1000SHIB/USDT", stable_id("plan")[:8], "tp1", 12)
    assert len(c) <= 36 and CLIENT_ID_RE.match(c) and c.startswith("tb-t-")
    assert make_client_order_id("paper", "x", "ETHUSDT", "abcdef12", "entry", 2) != a
    assert not valid_client_order_id("has space") and not valid_client_order_id("x" * 37)


# ----------------------------------------------------------------------------- paper gateway
def _paper(fault=None):
    fut = FuturesLedgerV2(50, slippage=SlippageModel.zero())
    spot = SpotLedger(1000, fees=FeeSchedule.spot_default(), slippage=SlippageModel.zero(), filters_lookup=lambda s: SPOT_F)
    gw = PaperGateway(spot, fut, price_feed=lambda s: TickData(last=3000), fault=fault, clock=lambda: T0)
    return gw, fut, spot


def test_paper_gateway_market_filled_and_duplicate_protection():
    gw, fut, _ = _paper()
    o = _order(leverage=2, stop=2900, targets=[3100, 3200])
    r = gw.submit(o)
    assert r is o and o.status is S.FILLED and o.filled_qty == D("0.016") and o.avg_fill_price == D("3000")
    assert ETH in fut.positions and fut.positions[ETH].leverage == 2 and fut.positions[ETH].stop == D("2900")
    wallet = fut.wallet_balance
    # aynı client_order_id ile ikinci gönderim → ikinci fill YOK
    dup = _order(leverage=2)
    r2 = gw.submit(dup)
    assert r2 is o and dup.status is S.RISK_APPROVED and fut.wallet_balance == wallet and len(fut.positions) == 1
    assert gw.fetch_order(o.client_order_id) is o and gw.open_orders() == []
    # reduce_only market → kapat
    c = _order(cid="tb-p-brk-ETHUSDT-abcdef12-c-01", side=Side.SELL)
    c.reduce_only = True
    gw.submit(c)
    assert c.status is S.FILLED and not fut.positions and c.meta["trade_id"] == "F00001"
    # spot market
    s = _order(cid="tb-p-brk-ETHUSDT-abcdef12-e-02", market=MarketType.SPOT, qty="0.1")
    gw.submit(s)
    assert s.status is S.FILLED and s.exchange_order_id.startswith("S") and gw.spot.qty(ETH) == D("0.1")
    assert any(p["symbol"] == ETH for p in gw.positions())


def test_paper_gateway_requires_risk_approval_and_reject_reason():
    gw, fut, _ = _paper()
    o = _order(status=S.CREATED)
    with pytest.raises(IllegalTransitionError):
        gw.submit(o)
    big = _order(qty="10", leverage=1)        # 30000 USDT notional > cüzdan
    gw.submit(big)
    assert big.status is S.REJECTED and big.reject_reason == "INSUFFICIENT_MARGIN"


def test_paper_gateway_timeout_goes_to_reconciling_and_reconcile_resolves():
    gw, fut, _ = _paper(fault="timeout")
    o = _order(leverage=2)
    gw.submit(o)
    assert o.status is S.RECONCILING and [e["to"] for e in o.events][-3:] == ["SUBMITTING", "UNKNOWN", "RECONCILING"]
    assert not fut.positions
    # yeniden gönderim yok: aynı id ile submit yine aynı nesneyi döndürür
    assert gw.submit(_order(leverage=2)) is o and not fut.positions
    rep = reconcile(gw, [o], list(fut.positions.values()))
    assert isinstance(rep, ReconcileReport) and o.status is S.CANCELED and rep.resolved and rep.ok


def test_paper_gateway_cancel_resting_spot_order():
    gw, _, spot = _paper()
    o = _order(cid="tb-p-brk-ETHUSDT-abcdef12-e-03", market=MarketType.SPOT, otype=OrderType.LIMIT, qty="0.1")
    o.price = D("2900")
    gw.submit(o)
    assert o.status is S.ACKNOWLEDGED and spot.locked_cash > 0
    gw.cancel(o.client_order_id)
    assert o.status is S.CANCELED and spot.locked_cash == 0


# ----------------------------------------------------------------------------- live gateway guards
def test_live_gateway_always_disabled():
    gw = LiveGateway(env={})
    with pytest.raises(ExecutionDisabledError):
        gw.submit(_order())
    gw = LiveGateway(config_allow_live=True, account_label="acct", token=live_confirm_token("acct"), env={"ALLOW_LIVE_TRADING": "false"})
    with pytest.raises(ExecutionDisabledError):
        gw.submit(_order())
    gw = LiveGateway(config_allow_live=False, account_label="acct", token=live_confirm_token("acct"), env={"ALLOW_LIVE_TRADING": "true"})
    with pytest.raises(ExecutionDisabledError):
        gw.positions()
    gw = LiveGateway(config_allow_live=True, account_label="acct", token="wrong", env={"ALLOW_LIVE_TRADING": "true"})
    with pytest.raises(ExecutionDisabledError):
        gw.cancel("x")
    # üç kilit açık → yine de NotImplementedError (canlı yol yok)
    gw = LiveGateway(config_allow_live=True, account_label="acct", token=stable_id("LIVE-CONFIRM", "acct"), env={"ALLOW_LIVE_TRADING": "true"})
    with pytest.raises(NotImplementedError):
        gw.submit(_order())


# ----------------------------------------------------------------------------- testnet gateways
def test_testnet_gateway_disabled_or_missing_secrets_raises():
    for cls in (BinanceSpotTestnetGateway, BinanceFuturesTestnetGateway):
        gw = cls(enabled=False, env={})
        with pytest.raises(ExecutionDisabledError):
            gw.submit(_order())
        gw = cls(enabled=True, env={})              # anahtar yok
        with pytest.raises(ExecutionDisabledError):
            gw.submit(_order())
        assert not gw.has_credentials()
        gw = cls(enabled=True, env={cls.key_env: "k", cls.secret_env: "s"}, http=None)   # http enjekte edilmedi → ağ yok
        with pytest.raises(ExecutionDisabledError):
            gw.submit(_order())


def test_hmac_known_vector():
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    qs = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    assert sign_hmac_sha256(secret, qs) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_testnet_gateway_with_fake_http_signs_and_maps_status():
    calls = []

    def http(method, url, headers):
        calls.append((method, url, dict(headers)))
        if method == "POST":
            return {"orderId": 123, "clientOrderId": "tb-t-brk-ETHUSDT-abcdef12-e-01", "status": "FILLED", "executedQty": "0.016", "avgPrice": "3000.5"}
        if method == "GET" and "/order?" in url:
            return {"orderId": 123, "status": "FILLED", "executedQty": "0.016", "avgPrice": "3000.5"}
        return []

    gw = BinanceFuturesTestnetGateway(enabled=True, env={"BINANCE_TESTNET_FUTURES_KEY": "key123", "BINANCE_TESTNET_FUTURES_SECRET": "sec"},
                                      http=http, clock=lambda: T0)
    o = _order(cid="tb-t-brk-ETHUSDT-abcdef12-e-01")
    gw.submit(o)
    assert o.status is S.FILLED and o.filled_qty == D("0.016") and o.avg_fill_price == D("3000.5") and o.exchange_order_id == "123"
    method, url, headers = calls[0]
    assert method == "POST" and url.startswith("https://testnet.binancefuture.com/fapi/v1/order?")
    assert headers["X-MBX-APIKEY"] == "key123" and "signature=" in url and "sec" not in url.split("signature=")[0].replace("secret", "")
    assert "symbol=ETHUSDT" in url and "newClientOrderId=tb-t-brk-ETHUSDT-abcdef12-e-01" in url and "type=MARKET" in url
    qs = url.split("?", 1)[1].rsplit("&signature=", 1)[0]
    assert url.endswith(sign_hmac_sha256("sec", qs))
    # çift gönderim → http tekrar çağrılmaz
    n = len(calls)
    assert gw.submit(_order(cid="tb-t-brk-ETHUSDT-abcdef12-e-01")) is o and len(calls) == n


def test_testnet_timeout_goes_reconciling_never_resubmits():
    calls = {"n": 0}

    def http(method, url, headers):
        calls["n"] += 1
        raise TimeoutError("simulated")

    gw = BinanceSpotTestnetGateway(enabled=True, env={"BINANCE_TESTNET_SPOT_KEY": "k", "BINANCE_TESTNET_SPOT_SECRET": "s"}, http=http)
    o = _order(cid="tb-t-brk-ETHUSDT-abcdef12-e-09", market=MarketType.SPOT)
    gw.submit(o)
    assert o.status is S.RECONCILING and calls["n"] == 1
    gw.submit(_order(cid="tb-t-brk-ETHUSDT-abcdef12-e-09", market=MarketType.SPOT))
    assert calls["n"] == 1


# ----------------------------------------------------------------------------- reconcile
class _FakeGateway:
    name = "fake"

    def __init__(self, remote: dict[str, Order], open_remote: list[Order] | None = None, positions: list[dict] | None = None):
        self.remote = remote
        self._open = open_remote or []
        self._pos = positions or []

    def fetch_order(self, cid):
        return self.remote.get(cid)

    def open_orders(self):
        return self._open

    def positions(self):
        return self._pos

    def submit(self, order):
        raise AssertionError("reconcile asla submit çağırmamalı")

    def cancel(self, cid):
        raise AssertionError("no")


def test_reconcile_mismatch_and_resolution():
    # yerel ACK, uzak FILLED → mismatch + adopt
    local = _order(cid="tb-p-a-ETHUSDT-abcdef12-e-01")
    OrderStateMachine.transition(local, S.SUBMITTING); OrderStateMachine.transition(local, S.ACKNOWLEDGED)
    remote = _order(cid="tb-p-a-ETHUSDT-abcdef12-e-01", status=S.CREATED)
    remote.status = S.FILLED; remote.filled_qty = D("0.016"); remote.avg_fill_price = D("3001")
    # yerel RECONCILING, uzakta yok → CANCELED
    lost = _order(cid="tb-p-a-ETHUSDT-abcdef12-e-02")
    OrderStateMachine.transition(lost, S.SUBMITTING); OrderStateMachine.transition(lost, S.UNKNOWN); OrderStateMachine.transition(lost, S.RECONCILING)
    # yerel RECONCILING, uzakta ACK → ACK
    rec_ok = _order(cid="tb-p-a-ETHUSDT-abcdef12-e-03")
    OrderStateMachine.transition(rec_ok, S.SUBMITTING); OrderStateMachine.transition(rec_ok, S.UNKNOWN); OrderStateMachine.transition(rec_ok, S.RECONCILING)
    remote_ack = _order(cid="tb-p-a-ETHUSDT-abcdef12-e-03", status=S.CREATED); remote_ack.status = S.ACKNOWLEDGED
    # uzakta bilinmeyen açık emir
    stranger = _order(cid="tb-p-a-ETHUSDT-abcdef12-e-77", status=S.CREATED); stranger.status = S.ACKNOWLEDGED
    gw = _FakeGateway({local.client_order_id: remote, rec_ok.client_order_id: remote_ack}, open_remote=[stranger],
                      positions=[{"symbol": "ETHUSDT", "side": "LONG", "qty": D("0.016")}])
    rep = reconcile(gw, [local, lost, rec_ok], local_positions=[{"symbol": ETH, "side": "LONG", "qty": D("0.010")}])
    assert not rep.ok
    assert local.status is S.FILLED and local.filled_qty == D("0.016") and rep.mismatches[0]["remote"] == "FILLED"
    assert lost.status is S.CANCELED and any(r["why"] == "not_found_remote" for r in rep.resolved)
    assert rec_ok.status is S.ACKNOWLEDGED
    assert rep.unknown_remote == ["tb-p-a-ETHUSDT-abcdef12-e-77"]
    assert rep.position_mismatches and rep.position_mismatches[0]["symbol"] == "ETHUSDT"
    d = rep.to_dict()
    assert d["ok"] is False and d["checked_orders"] == 3
    # eşleşen pozisyonlar → mismatch yok
    rep2 = reconcile(_FakeGateway({}, positions=[{"symbol": "ETHUSDT", "side": "LONG", "qty": D("0.016")}]), [],
                     local_positions=[{"symbol": ETH, "side": "LONG", "qty": D("0.016")}])
    assert rep2.ok
