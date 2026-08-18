"""Uzlaştırma (reconcile): yerel emir/pozisyon durumu ile geçidin bildirdiği durumu karşılaştırır ve RECONCILING emirleri çözer.

Kurallar:
* UNKNOWN/RECONCILING emir uzakta bulunursa → uzak durum uygulanır (ACK/PARTIAL/FILLED/CANCELED/EXPIRED).
* Uzakta bulunamazsa → CANCELED (not: 'not found remotely'); ASLA yeniden gönderilmez.
* Açık yerel emrin uzak durumu farklıysa → mismatch kaydı (+ uygulanabiliyorsa uzak durum yerel emre yazılır).
* Uzakta olup yerelde olmayan emirler → unknown_remote.
* Pozisyonlar sembol bazında qty/side karşılaştırılır.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..accounting.models import Order, OrderStatus, Position, ser
from ..core import D, ZERO, ExecutionDisabledError, iso
from .orders import NEEDS_RECONCILE, OPEN_STATES, IllegalTransitionError, OrderStateMachine

log = logging.getLogger(__name__)
S = OrderStatus


@dataclass
class ReconcileReport:
    at: str
    gateway: str
    checked_orders: int = 0
    resolved: list[dict] = field(default_factory=list)          # RECONCILING → çözüldü
    mismatches: list[dict] = field(default_factory=list)        # yerel/uzak durum farkı
    missing_remote: list[str] = field(default_factory=list)     # yerelde açık, uzakta yok
    unknown_remote: list[str] = field(default_factory=list)     # uzakta açık, yerelde yok
    position_mismatches: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.mismatches or self.missing_remote or self.unknown_remote or self.position_mismatches or self.errors)

    def to_dict(self) -> dict:
        d = ser(self)
        d["ok"] = self.ok
        return d


def _pos_key(p: Any) -> tuple[str, str]:
    if isinstance(p, Position):
        return p.symbol, p.side.value
    return str(p.get("symbol", "")), str(p.get("side", "LONG")).upper()


def _pos_qty(p: Any):
    return p.qty if isinstance(p, Position) else D(p.get("qty", 0) or 0)


def _norm_sym(s: str) -> str:
    return s.replace("/", "").replace(":", "").upper()


def reconcile(gateway, local_orders: Iterable[Order], local_positions: Iterable[Any] | None = None) -> ReconcileReport:
    rep = ReconcileReport(at=iso(), gateway=getattr(gateway, "name", type(gateway).__name__))
    sm = OrderStateMachine
    local_orders = list(local_orders)
    local_by_cid = {o.client_order_id: o for o in local_orders}
    for o in local_orders:
        if o.status not in OPEN_STATES and o.status not in NEEDS_RECONCILE:
            continue
        rep.checked_orders += 1
        try:
            remote = gateway.fetch_order(o.client_order_id)
        except ExecutionDisabledError as exc:
            rep.errors.append(f"{o.client_order_id}: {exc}")
            continue
        if o.status is S.UNKNOWN:
            sm.transition(o, S.RECONCILING, "reconcile start")
        if remote is None:
            if o.status is S.RECONCILING or o.status is S.SUBMITTING:
                if o.status is S.SUBMITTING:
                    sm.transition(o, S.UNKNOWN, "reconcile: not found")
                    sm.transition(o, S.RECONCILING, "reconcile")
                sm.transition(o, S.CANCELED, "not found remotely — never resubmitted")
                rep.resolved.append({"client_order_id": o.client_order_id, "to": S.CANCELED.value, "why": "not_found_remote"})
            else:
                rep.missing_remote.append(o.client_order_id)
            continue
        rstatus = remote.status
        if remote is o:
            # geçit aynı nesneyi döndürdü (paper / registry) — durum zaten güncel
            if o.status is S.RECONCILING:
                # kağıt geçit fault senaryosu: uzakta gerçek fill yok → iptal say
                sm.transition(o, S.CANCELED, "reconcile: paper timeout, no fill")
                rep.resolved.append({"client_order_id": o.client_order_id, "to": S.CANCELED.value, "why": "paper_timeout"})
            continue
        if o.status is S.RECONCILING:
            target = rstatus if rstatus in (S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.EXPIRED, S.REJECTED) else S.ACKNOWLEDGED
            try:
                if sm.can(o.status, target):
                    sm.transition(o, target, "reconcile: remote status")
                else:
                    sm.transition(o, S.ACKNOWLEDGED, "reconcile")
                    sm.transition(o, target, "reconcile: remote status")
            except IllegalTransitionError as exc:
                rep.errors.append(str(exc))
                continue
            o.filled_qty = remote.filled_qty
            o.avg_fill_price = remote.avg_fill_price
            rep.resolved.append({"client_order_id": o.client_order_id, "to": target.value, "why": "remote_found"})
            continue
        if rstatus != o.status or remote.filled_qty != o.filled_qty:
            rep.mismatches.append({"client_order_id": o.client_order_id, "local": o.status.value, "remote": rstatus.value,
                                   "local_filled": ser(o.filled_qty), "remote_filled": ser(remote.filled_qty)})
            try:
                if rstatus != o.status:
                    if sm.can(o.status, rstatus):
                        sm.transition(o, rstatus, "reconcile: adopt remote")
                    elif sm.can(o.status, S.ACKNOWLEDGED) and sm.can(S.ACKNOWLEDGED, rstatus):
                        sm.transition(o, S.ACKNOWLEDGED, "reconcile")
                        sm.transition(o, rstatus, "reconcile: adopt remote")
                o.filled_qty = remote.filled_qty
                o.avg_fill_price = remote.avg_fill_price
            except IllegalTransitionError as exc:
                rep.errors.append(str(exc))
    # uzakta açık, yerelde bilinmeyen
    try:
        for r in gateway.open_orders():
            if r.client_order_id not in local_by_cid:
                rep.unknown_remote.append(r.client_order_id)
    except ExecutionDisabledError as exc:
        rep.errors.append(f"open_orders: {exc}")
    # pozisyonlar
    if local_positions is not None:
        try:
            remote_pos = gateway.positions()
        except ExecutionDisabledError as exc:
            rep.errors.append(f"positions: {exc}")
            remote_pos = []
        rmap: dict[str, tuple[str, Any]] = {}
        for rp in remote_pos:
            sym, side = _pos_key(rp)
            rmap[_norm_sym(sym)] = (side, _pos_qty(rp))
        lmap: dict[str, tuple[str, Any]] = {}
        for lp in local_positions:
            sym, side = _pos_key(lp)
            lmap[_norm_sym(sym)] = (side, _pos_qty(lp))
        for sym in sorted(set(rmap) | set(lmap)):
            l = lmap.get(sym, ("-", ZERO))
            r = rmap.get(sym, ("-", ZERO))
            if l[1] != r[1] or (l[1] > 0 and r[1] > 0 and l[0] != r[0]):
                rep.position_mismatches.append({"symbol": sym, "local_side": l[0], "local_qty": ser(l[1]), "remote_side": r[0], "remote_qty": ser(r[1])})
    if not rep.ok:
        log.warning("reconcile: uyumsuzluk var — %s", rep.to_dict())
    return rep


__all__ = ["ReconcileReport", "reconcile"]
