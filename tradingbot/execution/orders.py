"""Emir durum makinesi ve deterministik clientOrderId üretimi.

Yasal geçişler:
    CREATED → RISK_APPROVED → SUBMITTING → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
    ACKNOWLEDGED/PARTIALLY_FILLED → CANCEL_REQUESTED → CANCELED
    SUBMITTING → REJECTED | UNKNOWN
    UNKNOWN → RECONCILING → ACKNOWLEDGED | FILLED | CANCELED | EXPIRED
Yasadışı geçişte `IllegalTransitionError` fırlatılır (sessiz düzeltme yok).
"""
from __future__ import annotations

import re
from typing import Iterable

from ..accounting.models import Order, OrderStatus
from ..core import TradingBotError, iso

S = OrderStatus

LEGAL_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    S.CREATED: frozenset({S.RISK_APPROVED, S.REJECTED, S.CANCELED}),
    S.RISK_APPROVED: frozenset({S.SUBMITTING, S.REJECTED, S.CANCELED}),
    S.SUBMITTING: frozenset({S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED, S.REJECTED, S.UNKNOWN}),
    S.ACKNOWLEDGED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_REQUESTED, S.CANCELED, S.EXPIRED, S.UNKNOWN}),
    S.PARTIALLY_FILLED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_REQUESTED, S.CANCELED, S.EXPIRED, S.UNKNOWN}),
    S.CANCEL_REQUESTED: frozenset({S.CANCELED, S.FILLED, S.PARTIALLY_FILLED, S.UNKNOWN}),
    S.UNKNOWN: frozenset({S.RECONCILING}),
    S.RECONCILING: frozenset({S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.EXPIRED, S.REJECTED}),
    S.FILLED: frozenset(),
    S.CANCELED: frozenset(),
    S.REJECTED: frozenset(),
    S.EXPIRED: frozenset(),
}

TERMINAL = frozenset({S.FILLED, S.CANCELED, S.REJECTED, S.EXPIRED})
OPEN_STATES = frozenset({S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.CANCEL_REQUESTED})
NEEDS_RECONCILE = frozenset({S.SUBMITTING, S.UNKNOWN, S.RECONCILING})


class IllegalTransitionError(TradingBotError):
    """Yasadışı emir durum geçişi."""


class OrderStateMachine:
    """Order.status alanını yalnızca yasal geçişlerle değiştirir; her geçişi order.events'e yazar."""

    transitions = LEGAL_TRANSITIONS

    @classmethod
    def can(cls, src: OrderStatus, dst: OrderStatus) -> bool:
        return dst in cls.transitions.get(src, frozenset())

    @classmethod
    def next_states(cls, src: OrderStatus) -> frozenset[OrderStatus]:
        return cls.transitions.get(src, frozenset())

    @classmethod
    def transition(cls, order: Order, dst: OrderStatus | str, note: str = "", *, ts: str | None = None) -> Order:
        dst_e = dst if isinstance(dst, OrderStatus) else OrderStatus(str(dst).upper())
        src = order.status
        if not cls.can(src, dst_e):
            raise IllegalTransitionError(f"{order.client_order_id}: {src.value} → {dst_e.value} yasadışı")
        order.status = dst_e
        order.updated_at = ts or iso()
        ev = {"ts": order.updated_at, "from": src.value, "to": dst_e.value}
        if note:
            ev["note"] = note
        order.events.append(ev)
        return order

    @classmethod
    def is_terminal(cls, status: OrderStatus) -> bool:
        return status in TERMINAL


# ----------------------------------------------------------------------------- clientOrderId
CLIENT_ID_RE = re.compile(r"^[\.A-Z\:/a-z0-9_-]{1,36}$")
_ENV_CODE = {"paper": "p", "testnet": "t", "live": "l", "spot_testnet": "ts", "futures_testnet": "tf", "backtest": "b"}
_INTENT_CODE = {"entry": "e", "tp1": "t1", "tp2": "t2", "tp": "t", "stop": "s", "close": "c", "cancel": "x", "reduce": "r",
                "breakeven": "be", "trail": "tr", "manual": "m"}


def _clean(s: str, maxlen: int) -> str:
    s = re.sub(r"[^A-Za-z0-9_\-\.:/]", "", str(s))
    return s[:maxlen]


def make_client_order_id(env: str, strategy: str, symbol: str, plan_hash8: str, intent: str, seq: int) -> str:
    """`tb-{env}-{strategy}-{SYMBOL}-{plan_hash8}-{intent}-{seq}` — deterministik, ≤36 karakter, Binance regex uyumlu.
    Aynı girdiler her zaman aynı id'yi verir (yeniden deneme = aynı id → borsada çift emir olmaz)."""
    env_c = _ENV_CODE.get(str(env).lower(), _clean(env, 2).lower() or "p")
    sym = _clean(symbol.replace("/", "").replace(":", "").upper(), 12)
    ph = _clean(plan_hash8, 8).lower()
    if len(ph) < 8:
        ph = ph.ljust(8, "0")
    intent_c = _INTENT_CODE.get(str(intent).lower(), _clean(intent, 2).lower() or "e")
    seq_s = f"{int(seq):02d}"
    strat = _clean(strategy, 6).lower() or "s"
    cid = f"tb-{env_c}-{strat}-{sym}-{ph}-{intent_c}-{seq_s}"
    while len(cid) > 36 and len(strat) > 1:
        strat = strat[:-1]
        cid = f"tb-{env_c}-{strat}-{sym}-{ph}-{intent_c}-{seq_s}"
    while len(cid) > 36 and len(sym) > 3:
        sym = sym[:-1]
        cid = f"tb-{env_c}-{strat}-{sym}-{ph}-{intent_c}-{seq_s}"
    if len(cid) > 36 or not CLIENT_ID_RE.match(cid):
        raise ValueError(f"clientOrderId üretilemedi: {cid!r}")
    return cid


def valid_client_order_id(cid: str) -> bool:
    return bool(CLIENT_ID_RE.match(cid or ""))


__all__ = ["OrderStateMachine", "IllegalTransitionError", "LEGAL_TRANSITIONS", "TERMINAL", "OPEN_STATES", "NEEDS_RECONCILE",
           "make_client_order_id", "valid_client_order_id", "CLIENT_ID_RE"]
