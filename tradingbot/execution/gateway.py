"""Yürütme geçitleri (ExecutionGateway).

* PaperGateway              : SpotLedger / FuturesLedgerV2 üzerinde kağıt doldurma (varsayılan, ağ yok).
* BinanceSpotTestnetGateway : testnet.binance.vision — yalnızca `enabled=True` VE ortam değişkenlerinde anahtar varsa çalışır.
* BinanceFuturesTestnetGateway : testnet.binancefuture.com — aynı kural.
* LiveGateway               : HER ZAMAN ExecutionDisabledError; üçlü kilit (env + config + token) açılsa bile NotImplementedError.

Ortak kurallar: clientOrderId kayıt defteri ile çift gönderim engellenir; zaman aşımı → UNKNOWN → RECONCILING (asla yeniden gönderme);
gizli anahtarlar hiçbir zaman loglanmaz/yazılmaz; HTTP çağrısı enjekte edilebilir (`http(method, url, headers) -> dict`).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from urllib.parse import urlencode

from ..accounting.futures_ledger import FuturesLedgerV2
from ..accounting.models import (
    AmountType,
    MarketType,
    Order,
    OrderStatus,
    OrderType,
    PositionSide,
    Side,
    SizeSpec,
    TickData,
)
from ..accounting.spot_ledger import SpotLedger
from ..core import D, ExecutionDisabledError, stable_id, to_ms, utc_now
from .orders import IllegalTransitionError, OrderStateMachine

log = logging.getLogger(__name__)

S = OrderStatus
HttpCallable = Callable[[str, str, Mapping[str, str]], Mapping[str, Any]]
PriceFeed = Callable[[str], Any]

ENV_SPOT_KEY = "BINANCE_TESTNET_SPOT_KEY"
ENV_SPOT_SECRET = "BINANCE_TESTNET_SPOT_SECRET"
ENV_FUT_KEY = "BINANCE_TESTNET_FUTURES_KEY"
ENV_FUT_SECRET = "BINANCE_TESTNET_FUTURES_SECRET"
ENV_ALLOW_LIVE = "ALLOW_LIVE_TRADING"
SPOT_TESTNET_URL = "https://testnet.binance.vision"
FUTURES_TESTNET_URL = "https://testnet.binancefuture.com"


def sign_hmac_sha256(secret: str, query_string: str) -> str:
    """Binance imzası: HMAC-SHA256(secret, query_string) hex."""
    return hmac.new(secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


def live_confirm_token(account_label: str) -> str:
    """LiveGateway'in beklediği token (kullanıcı bunu bilinçli olarak üretip config'e yazmalı)."""
    return stable_id("LIVE-CONFIRM", account_label)


@runtime_checkable
class ExecutionGateway(Protocol):
    name: str

    def submit(self, order: Order) -> Order: ...
    def cancel(self, client_order_id: str) -> Order | None: ...
    def fetch_order(self, client_order_id: str) -> Order | None: ...
    def open_orders(self) -> list[Order]: ...
    def positions(self) -> list[dict]: ...


class _BaseGateway:
    name = "base"

    def __init__(self, clock: Callable[[], datetime] | None = None):
        self._registry: dict[str, Order] = {}
        self.sm = OrderStateMachine
        self.clock = clock or utc_now

    # kayıt defteri (çift gönderim koruması)
    def _existing(self, order: Order) -> Order | None:
        return self._registry.get(order.client_order_id)

    def _register(self, order: Order) -> None:
        self._registry[order.client_order_id] = order

    def fetch_order(self, client_order_id: str) -> Order | None:
        return self._registry.get(client_order_id)

    def open_orders(self) -> list[Order]:
        return [o for o in self._registry.values() if o.is_open]

    def registry(self) -> dict[str, Order]:
        return dict(self._registry)

    def _apply_status(self, order: Order, dst: OrderStatus, note: str = "") -> None:
        """Uzak/defter durumunu yasal yoldan uygula (gerekirse ACKNOWLEDGED üzerinden iki adım)."""
        if order.status is dst:
            return
        if self.sm.can(order.status, dst):
            self.sm.transition(order, dst, note)
            return
        if self.sm.can(order.status, S.ACKNOWLEDGED) and self.sm.can(S.ACKNOWLEDGED, dst):
            self.sm.transition(order, S.ACKNOWLEDGED, note)
            self.sm.transition(order, dst, note)
            return
        raise IllegalTransitionError(f"{order.client_order_id}: {order.status.value} → {dst.value} için yasal yol yok")


# ============================================================================ PAPER
class PaperGateway(_BaseGateway):
    """Kağıt geçit. `price_feed(symbol) -> TickData|fiyat` fill referansı sağlar.
    `fault`: None | 'timeout' | 'reject' — test/kaos senaryoları için enjekte edilebilir arıza."""
    name = "paper"

    def __init__(self, spot_ledger: SpotLedger | None = None, futures_ledger: FuturesLedgerV2 | None = None,
                 price_feed: PriceFeed | None = None, *, fault: str | None = None, clock: Callable[[], datetime] | None = None):
        super().__init__(clock)
        self.spot = spot_ledger
        self.futures = futures_ledger
        self.price_feed = price_feed
        self.fault = fault

    def _tick(self, symbol: str, order: Order) -> TickData | None:
        if self.price_feed is not None:
            raw = self.price_feed(symbol)
            if raw is not None:
                return TickData.coerce(raw)
        if order.meta.get("ref_price") is not None:
            return TickData(last=D(order.meta["ref_price"]))
        if order.price is not None:
            return TickData(last=order.price)
        return None

    def submit(self, order: Order) -> Order:
        dup = self._existing(order)
        if dup is not None:
            log.warning("çift gönderim engellendi: %s", order.client_order_id)
            return dup
        self.sm.transition(order, S.SUBMITTING, "paper submit")   # RISK_APPROVED değilse IllegalTransitionError
        self._register(order)
        now = self.clock()
        if self.fault == "timeout":
            self.sm.transition(order, S.UNKNOWN, "simulated timeout")
            self.sm.transition(order, S.RECONCILING, "awaiting reconcile")
            return order
        if self.fault == "reject":
            order.reject_reason = "SIMULATED_REJECT"
            self.sm.transition(order, S.REJECTED, "simulated reject")
            return order
        tick = self._tick(order.symbol, order)
        if order.market_type is MarketType.SPOT:
            return self._submit_spot(order, tick, now)
        return self._submit_futures(order, tick, now)

    # -- spot
    def _submit_spot(self, order: Order, tick: TickData | None, now: datetime) -> Order:
        if self.spot is None:
            order.reject_reason = "NO_SPOT_LEDGER"
            self.sm.transition(order, S.REJECTED, "spot defteri yok")
            return order
        led = self.spot.place_order(order.symbol, order.side, order.order_type, qty=order.qty, price=order.price,
                                    stop_price=order.stop_price, limit_price=order.price if order.order_type is OrderType.STOP_LIMIT else None,
                                    trailing_pct=order.trailing_pct, oco_stop_price=order.stop_price if order.order_type is OrderType.OCO else None,
                                    oco_stop_limit_price=order.oco_limit_price, tif=order.tif, tick=tick,
                                    strategy=str(order.meta.get("strategy", "")), client_order_id=order.client_order_id, now=now,
                                    meta=dict(order.meta))
        order.exchange_order_id = led.id
        order.meta["ledger_order_id"] = led.id
        order.qty = led.qty if led.qty > 0 else order.qty
        order.filled_qty = led.filled_qty
        order.avg_fill_price = led.avg_fill_price
        order.fills = list(led.fills)
        order.reject_reason = led.reject_reason
        self._apply_status(order, led.status, "spot ledger")
        return order

    # -- futures
    def _submit_futures(self, order: Order, tick: TickData | None, now: datetime) -> Order:
        if self.futures is None:
            order.reject_reason = "NO_FUTURES_LEDGER"
            self.sm.transition(order, S.REJECTED, "futures defteri yok")
            return order
        if order.order_type is not OrderType.MARKET:
            # stop/TP yönetimi defterin içinde; dış emir yalnızca kayıt olarak bekler
            self.sm.transition(order, S.ACKNOWLEDGED, "resting (ledger-managed)")
            return order
        if tick is None:
            order.reject_reason = "NO_MARKET_PRICE"
            self.sm.transition(order, S.REJECTED, "fiyat yok")
            return order
        ref = tick.ref
        if order.reduce_only:
            rec = self.futures.close_manual(order.symbol, ref, reason=str(order.meta.get("reason", "manuel")), now=now, tick=tick)
            if rec is None:
                order.reject_reason = "NO_POSITION"
                self.sm.transition(order, S.REJECTED, "kapatılacak pozisyon yok")
                return order
            f = rec.fills[-1] if rec.fills else None
            order.filled_qty = f.qty if f else order.qty
            order.avg_fill_price = f.price if f else ref
            if f:
                order.fills.append(f)
            order.meta["trade_id"] = rec.id
            self._apply_status(order, S.FILLED, "closed via ledger")
            return order
        m = order.meta
        pside = PositionSide.LONG if order.side is Side.BUY else PositionSide.SHORT
        size = SizeSpec(order.qty, AmountType.QUANTITY, leverage=int(m.get("leverage", 1)))
        pos = self.futures.open(order.symbol, pside, ref, size, stop=m.get("stop"), targets=m.get("targets"),
                                setup_type=str(m.get("setup_type", "")), trigger_text=str(m.get("trigger_text", "")),
                                features=dict(m.get("features") or {}), tick=tick, now=now,
                                meta={"client_order_id": order.client_order_id})
        if pos is None:
            order.reject_reason = self.futures.last_reject_reason or "REJECTED"
            self.sm.transition(order, S.REJECTED, order.reject_reason)
            return order
        f = pos.fills[0]
        order.exchange_order_id = pos.id
        order.meta["position_id"] = pos.id
        order.filled_qty = f.qty
        order.avg_fill_price = f.price
        order.fills.append(f)
        if f.qty < order.qty:
            order.qty = f.qty       # adım kuantizasyonu ile küçüldü (kalan iptal sayılır)
        self._apply_status(order, S.FILLED, "opened via ledger")
        return order

    def cancel(self, client_order_id: str) -> Order | None:
        order = self._registry.get(client_order_id)
        if order is None:
            return None
        if not order.is_open:
            return order
        if order.status is not S.CANCEL_REQUESTED:
            self.sm.transition(order, S.CANCEL_REQUESTED, "cancel")
        if self.spot is not None and order.market_type is MarketType.SPOT:
            lid = order.meta.get("ledger_order_id")
            if lid:
                self.spot.cancel_order(lid, now=self.clock())
        self.sm.transition(order, S.CANCELED, "paper cancel")
        return order

    def positions(self) -> list[dict]:
        out: list[dict] = []
        if self.futures is not None:
            for p in self.futures.positions.values():
                out.append({"symbol": p.symbol, "market_type": MarketType.USDM_PERP.value, "side": p.side.value, "qty": p.qty,
                            "entry": p.entry_avg, "leverage": p.leverage, "isolated_margin": p.isolated_margin})
        if self.spot is not None:
            for sym, p in self.spot.positions().items():
                out.append({"symbol": sym, "market_type": MarketType.SPOT.value, "side": "LONG", "qty": p["qty"], "entry": p["avg_cost"]})
        return out


# ============================================================================ BINANCE TESTNET
_STATUS_MAP = {"NEW": S.ACKNOWLEDGED, "PARTIALLY_FILLED": S.PARTIALLY_FILLED, "FILLED": S.FILLED, "CANCELED": S.CANCELED,
               "PENDING_CANCEL": S.CANCEL_REQUESTED, "REJECTED": S.REJECTED, "EXPIRED": S.EXPIRED, "EXPIRED_IN_MATCH": S.EXPIRED,
               "NEW_INSURANCE": S.FILLED, "NEW_ADL": S.FILLED}


class _BinanceTestnetGateway(_BaseGateway):
    """Ortak testnet mantığı. Anahtarlar YALNIZCA ortam değişkenlerinden okunur; hiçbir yere yazılmaz/loglanmaz."""
    name = "binance_testnet"
    key_env = ""
    secret_env = ""
    base_url = ""
    order_path = ""
    open_orders_path = ""
    market_type = MarketType.SPOT

    def __init__(self, enabled: bool = False, http: HttpCallable | None = None, env: Mapping[str, str] | None = None,
                 recv_window: int = 5000, clock: Callable[[], datetime] | None = None, base_url: str | None = None):
        super().__init__(clock)
        self.enabled = bool(enabled)
        self.http = http
        self.env = env if env is not None else os.environ
        self.recv_window = int(recv_window)
        if base_url:
            self.base_url = base_url

    # -- güvenlik kapısı
    def _creds(self) -> tuple[str, str]:
        if not self.enabled:
            raise ExecutionDisabledError(f"{self.name}: enabled=False (PAPER varsayılan)")
        key = self.env.get(self.key_env, "")
        secret = self.env.get(self.secret_env, "")
        if not key or not secret:
            raise ExecutionDisabledError(f"{self.name}: {self.key_env}/{self.secret_env} ortam değişkenleri yok")
        return key, secret

    def has_credentials(self) -> bool:
        return bool(self.env.get(self.key_env) and self.env.get(self.secret_env))

    def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> Mapping[str, Any]:
        key, secret = self._creds()
        if self.http is None:
            raise ExecutionDisabledError(f"{self.name}: http çağrısı enjekte edilmedi (ağ yok)")
        p = {k: v for k, v in params.items() if v is not None}
        p["timestamp"] = to_ms(self.clock())
        p["recvWindow"] = self.recv_window
        qs = urlencode(p, doseq=True)
        sig = sign_hmac_sha256(secret, qs)
        url = f"{self.base_url}{path}?{qs}&signature={sig}"
        headers = {"X-MBX-APIKEY": key}
        return self.http(method, url, headers)

    # -- eşleme
    @staticmethod
    def _sym(symbol: str) -> str:
        return symbol.replace("/", "").replace(":", "").upper()

    def _order_params(self, order: Order) -> dict[str, Any]:
        raise NotImplementedError

    def _apply_remote(self, order: Order, resp: Mapping[str, Any], note: str) -> Order:
        st = _STATUS_MAP.get(str(resp.get("status", "")).upper())
        if resp.get("orderId") is not None:
            order.exchange_order_id = str(resp["orderId"])
        exec_qty = D(resp.get("executedQty", 0) or 0)
        if exec_qty > 0:
            order.filled_qty = exec_qty
            avg = resp.get("avgPrice")
            if avg is None and resp.get("cummulativeQuoteQty") is not None and exec_qty > 0:
                avg = D(resp["cummulativeQuoteQty"]) / exec_qty
            order.avg_fill_price = D(avg) if avg is not None else order.avg_fill_price
        if resp.get("code") is not None and st is None:
            order.reject_reason = f"{resp.get('code')}: {resp.get('msg', '')}"
            self._apply_status(order, S.REJECTED, note)
            return order
        if st is None:
            self._apply_status(order, S.UNKNOWN if self.sm.can(order.status, S.UNKNOWN) else order.status, note)
            if order.status is S.UNKNOWN:
                self.sm.transition(order, S.RECONCILING, "unknown remote status")
            return order
        self._apply_status(order, st, note)
        return order

    # -- API
    def submit(self, order: Order) -> Order:
        dup = self._existing(order)
        if dup is not None:
            log.warning("çift gönderim engellendi: %s", order.client_order_id)
            return dup
        self._creds()   # önce kapı: enabled + anahtar; değilse hiç dokunma
        self.sm.transition(order, S.SUBMITTING, f"{self.name} submit")
        self._register(order)
        try:
            resp = self._signed_request("POST", self.order_path, self._order_params(order))
        except (TimeoutError, ConnectionError, OSError) as exc:
            log.warning("%s zaman aşımı/bağlantı hatası: %s → RECONCILING (yeniden gönderilmez)", self.name, type(exc).__name__)
            self.sm.transition(order, S.UNKNOWN, f"{type(exc).__name__}")
            self.sm.transition(order, S.RECONCILING, "awaiting reconcile")
            return order
        return self._apply_remote(order, resp, "submit response")

    def cancel(self, client_order_id: str) -> Order | None:
        order = self._registry.get(client_order_id)
        if order is None:
            return None
        if not order.is_open:
            return order
        if order.status is not S.CANCEL_REQUESTED:
            self.sm.transition(order, S.CANCEL_REQUESTED, "cancel")
        try:
            resp = self._signed_request("DELETE", self.order_path, {"symbol": self._sym(order.symbol), "origClientOrderId": client_order_id})
        except (TimeoutError, ConnectionError, OSError) as exc:
            self.sm.transition(order, S.UNKNOWN, f"cancel {type(exc).__name__}")
            self.sm.transition(order, S.RECONCILING, "awaiting reconcile")
            return order
        return self._apply_remote(order, resp, "cancel response")

    def fetch_order(self, client_order_id: str) -> Order | None:
        order = self._registry.get(client_order_id)
        if order is None:
            return None
        try:
            resp = self._signed_request("GET", self.order_path, {"symbol": self._sym(order.symbol), "origClientOrderId": client_order_id})
        except (TimeoutError, ConnectionError, OSError):
            return order
        if resp.get("code") is not None and str(resp.get("code")) in ("-2013",):   # Order does not exist
            return None
        return self._apply_remote(order, resp, "fetch") if order.status in (S.RECONCILING, S.UNKNOWN) or order.is_open else order

    def open_orders(self) -> list[Order]:
        resp = self._signed_request("GET", self.open_orders_path, {})
        out: list[Order] = []
        rows = resp if isinstance(resp, list) else resp.get("orders", []) if isinstance(resp, Mapping) else []
        for r in rows:
            cid = str(r.get("clientOrderId", ""))
            local = self._registry.get(cid)
            if local is not None:
                out.append(self._apply_remote(local, r, "open orders") if local.is_open or local.status in (S.RECONCILING, S.UNKNOWN) else local)
            else:
                out.append(Order(id=str(r.get("orderId", cid)), client_order_id=cid, symbol=str(r.get("symbol", "")), market_type=self.market_type,
                                 side=Side(str(r.get("side", "BUY")).upper()), order_type=OrderType.LIMIT if r.get("type") == "LIMIT" else OrderType.MARKET,
                                 qty=D(r.get("origQty", 0) or 0), price=D(r["price"]) if r.get("price") not in (None, "0", 0) else None,
                                 status=_STATUS_MAP.get(str(r.get("status", "NEW")), S.ACKNOWLEDGED), filled_qty=D(r.get("executedQty", 0) or 0),
                                 exchange_order_id=str(r.get("orderId", "")), meta={"remote_only": True}))
        return out

    def positions(self) -> list[dict]:
        return []


class BinanceSpotTestnetGateway(_BinanceTestnetGateway):
    name = "binance_spot_testnet"
    key_env = ENV_SPOT_KEY
    secret_env = ENV_SPOT_SECRET
    base_url = SPOT_TESTNET_URL
    order_path = "/api/v3/order"
    open_orders_path = "/api/v3/openOrders"
    market_type = MarketType.SPOT

    def _order_params(self, order: Order) -> dict[str, Any]:
        t = order.order_type
        p: dict[str, Any] = {"symbol": self._sym(order.symbol), "side": order.side.value, "newClientOrderId": order.client_order_id,
                             "quantity": format(order.qty, "f"), "newOrderRespType": "RESULT"}
        if t is OrderType.MARKET:
            p["type"] = "MARKET"
        elif t is OrderType.LIMIT:
            p.update(type="LIMIT", timeInForce=order.tif.value, price=format(order.price, "f"))
        elif t is OrderType.STOP_MARKET:
            p.update(type="STOP_LOSS", stopPrice=format(order.stop_price, "f"))
        elif t is OrderType.STOP_LIMIT:
            p.update(type="STOP_LOSS_LIMIT", timeInForce=order.tif.value, price=format(order.price, "f"), stopPrice=format(order.stop_price, "f"))
        elif t is OrderType.TAKE_PROFIT_MARKET:
            p.update(type="TAKE_PROFIT", stopPrice=format(order.stop_price, "f"))
        else:
            raise ExecutionDisabledError(f"spot testnet: desteklenmeyen emir tipi {t.value}")
        return p

    def positions(self) -> list[dict]:
        resp = self._signed_request("GET", "/api/v3/account", {})
        out = []
        for b in resp.get("balances", []) if isinstance(resp, Mapping) else []:
            free, locked = D(b.get("free", 0) or 0), D(b.get("locked", 0) or 0)
            if free + locked > 0:
                out.append({"asset": b.get("asset"), "free": free, "locked": locked, "market_type": MarketType.SPOT.value})
        return out


class BinanceFuturesTestnetGateway(_BinanceTestnetGateway):
    name = "binance_futures_testnet"
    key_env = ENV_FUT_KEY
    secret_env = ENV_FUT_SECRET
    base_url = FUTURES_TESTNET_URL
    order_path = "/fapi/v1/order"
    open_orders_path = "/fapi/v1/openOrders"
    market_type = MarketType.USDM_PERP

    def _order_params(self, order: Order) -> dict[str, Any]:
        t = order.order_type
        p: dict[str, Any] = {"symbol": self._sym(order.symbol), "side": order.side.value, "newClientOrderId": order.client_order_id,
                             "quantity": format(order.qty, "f"), "newOrderRespType": "RESULT"}
        if order.reduce_only:
            p["reduceOnly"] = "true"
        if t is OrderType.MARKET:
            p["type"] = "MARKET"
        elif t is OrderType.LIMIT:
            p.update(type="LIMIT", timeInForce=order.tif.value, price=format(order.price, "f"))
        elif t is OrderType.STOP_MARKET:
            p.update(type="STOP_MARKET", stopPrice=format(order.stop_price, "f"), workingType="MARK_PRICE")
        elif t is OrderType.STOP_LIMIT:
            p.update(type="STOP", timeInForce=order.tif.value, price=format(order.price, "f"), stopPrice=format(order.stop_price, "f"), workingType="MARK_PRICE")
        elif t is OrderType.TAKE_PROFIT_MARKET:
            p.update(type="TAKE_PROFIT_MARKET", stopPrice=format(order.stop_price, "f"), workingType="MARK_PRICE")
        else:
            raise ExecutionDisabledError(f"futures testnet: desteklenmeyen emir tipi {t.value}")
        return p

    def positions(self) -> list[dict]:
        resp = self._signed_request("GET", "/fapi/v2/positionRisk", {})
        out = []
        for r in resp if isinstance(resp, list) else []:
            amt = D(r.get("positionAmt", 0) or 0)
            if amt != 0:
                out.append({"symbol": r.get("symbol"), "market_type": MarketType.USDM_PERP.value, "side": "LONG" if amt > 0 else "SHORT",
                            "qty": abs(amt), "entry": D(r.get("entryPrice", 0) or 0), "leverage": int(r.get("leverage", 1) or 1),
                            "liquidation_price": D(r.get("liquidationPrice", 0) or 0)})
        return out


# ============================================================================ LIVE (KİLİTLİ)
class LiveGateway(_BaseGateway):
    """Gerçek para geçidi — bilinçli olarak devre dışı. Üç kilit: env ALLOW_LIVE_TRADING=true, config bayrağı, token.
    Üçü de açık olsa bile NotImplementedError: bu sürümde canlı emir yolu yoktur."""
    name = "live"

    def __init__(self, *, config_allow_live: bool = False, account_label: str = "", token: str | None = None,
                 env: Mapping[str, str] | None = None, clock: Callable[[], datetime] | None = None):
        super().__init__(clock)
        self.config_allow_live = bool(config_allow_live)
        self.account_label = account_label
        self.token = token
        self.env = env if env is not None else os.environ

    def _guard(self) -> None:
        if str(self.env.get(ENV_ALLOW_LIVE, "")).strip().lower() != "true":
            raise ExecutionDisabledError("LIVE kapalı: ALLOW_LIVE_TRADING=true değil")
        if not self.config_allow_live:
            raise ExecutionDisabledError("LIVE kapalı: config bayrağı kapalı")
        if not self.token or self.token != live_confirm_token(self.account_label):
            raise ExecutionDisabledError("LIVE kapalı: onay token'ı eşleşmiyor")
        raise NotImplementedError("LIVE emir yolu bu sürümde uygulanmadı (bilinçli)")

    def submit(self, order: Order) -> Order:
        self._guard()
        raise NotImplementedError  # pragma: no cover

    def cancel(self, client_order_id: str) -> Order | None:
        self._guard()
        raise NotImplementedError  # pragma: no cover

    def fetch_order(self, client_order_id: str) -> Order | None:
        self._guard()
        raise NotImplementedError  # pragma: no cover

    def open_orders(self) -> list[Order]:
        self._guard()
        raise NotImplementedError  # pragma: no cover

    def positions(self) -> list[dict]:
        self._guard()
        raise NotImplementedError  # pragma: no cover


__all__ = ["ExecutionGateway", "PaperGateway", "BinanceSpotTestnetGateway", "BinanceFuturesTestnetGateway", "LiveGateway",
           "sign_hmac_sha256", "live_confirm_token", "HttpCallable", "PriceFeed", "ENV_SPOT_KEY", "ENV_SPOT_SECRET", "ENV_FUT_KEY",
           "ENV_FUT_SECRET", "ENV_ALLOW_LIVE", "SPOT_TESTNET_URL", "FUTURES_TESTNET_URL"]
