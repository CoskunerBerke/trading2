"""Bildirim OLAYLARI ve mesaj kurucuları — sayılar KANONİK `tradingbot.pnl` katmanından gelir.

Panel ve Telegram aynı `PositionView` / `PortfolioView` nesnelerini okur; ayrı formül YOKTUR
(`tests/test_notify_telegram.py` bu eşitliği contract test ile zorlar).

Her mesaj `PAPER` etiketiyle başlar. Zarar `🔴` ve açık `-` işaretiyle gösterilir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..pnl import PortfolioView, PositionView, dec, fmt_money, fmt_pct, fmt_qty

EVENT_OPENED = "trade_opened"
EVENT_CLOSED = "trade_closed"
EVENT_HEALTH_DEGRADED = "health_degraded"
EVENT_HEALTH_RECOVERED = "health_recovered"
EVENT_DAILY_SUMMARY = "daily_summary"

# Kapanış nedeni → okunur etiket + simge
CLOSE_REASONS = {
    "STOP": ("STOP-LOSS", "🛑"), "STOP_LOSS": ("STOP-LOSS", "🛑"), "SL": ("STOP-LOSS", "🛑"),
    "TP": ("TAKE PROFIT", "🎯"), "TAKE_PROFIT": ("TAKE PROFIT", "🎯"), "TP1": ("TAKE PROFIT 1", "🎯"),
    "TP2": ("TAKE PROFIT 2", "🎯"), "LIQUIDATION": ("LİKİDASYON", "💥"), "LIQ": ("LİKİDASYON", "💥"),
    "TRAILING": ("TRAILING STOP", "📉"), "MANUAL": ("MANUEL KAPANIŞ", "✋"),
    "SIGNAL_EXIT": ("SİNYAL ÇIKIŞI", "↩️"), "EXIT": ("SİNYAL ÇIKIŞI", "↩️"),
}


def close_reason_label(reason: Any) -> tuple[str, str]:
    key = str(reason or "").upper().replace("-", "_").replace(" ", "_")
    return CLOSE_REASONS.get(key, (str(reason or "BİLİNMİYOR").upper(), "✅"))


@dataclass(frozen=True)
class NotifyEvent:
    """Tek bildirim. `id` idempotency anahtarıdır; aynı id ASLA iki kez gönderilmez."""
    id: str
    kind: str
    title: str
    text: str
    level: str = "info"                 # info | warning | error
    created_at: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "text": self.text,
                "level": self.level, "created_at": self.created_at, "meta": dict(self.meta)}


def event_id(kind: str, trade_id: str = "", ref: str = "") -> str:
    """İşlem ID + yaşam döngüsü olayı + fill/close referansı.

    Aynı işlemin açılışı ile kapanışı, aynı işlemin iki farklı kısmi kapanışı ve iki farklı işlemin
    aynı türdeki olayı BİRBİRİNDEN ayrılır.
    """
    return ":".join(x for x in (str(kind), str(trade_id or "-"), str(ref or "-")))


def _duration(opened_at: str, closed_at: str) -> str:
    from ..core import from_iso
    try:
        secs = max(0, int((from_iso(closed_at) - from_iso(opened_at)).total_seconds()))
    except (ValueError, TypeError):
        return "—"
    h, m = secs // 3600, secs % 3600 // 60
    if h and m:
        return f"{h} saat {m} dakika"
    if h:
        return f"{h} saat"
    return f"{m} dakika"


def _market_lines(v: PositionView) -> list[str]:
    if v.market == "SPOT":
        return ["Piyasa: SPOT", "Kaldıraç: Yok"]
    return ["Piyasa: FUTURES", f"Kaldıraç: {v.leverage}x"]


def build_opened(view: PositionView, *, max_loss_at_stop: Any = None, reason: str = "",
                 created_at: str = "") -> NotifyEvent:
    """`🟢 PAPER İŞLEM AÇILDI` mesajı. Coin adedi / notional / teminat AYRI AYRI yazılır."""
    base = view.symbol.split("/")[0]
    lines = [f"Coin: {view.symbol}", *_market_lines(view), f"Yön: {view.side}"]
    if view.market == "FUTURES":
        lines.append(f"Pozisyon değeri: {fmt_money(view.notional, signed=False, currency='')} USDT")
        lines.append(f"Kullanılan teminat: {fmt_money(view.initial_margin, signed=False, currency='')} USDT")
    else:
        lines.append(f"Yatırılan tutar: {fmt_money(view.notional, signed=False, currency='')} USDT")
    lines.append(f"Coin adedi: {fmt_qty(view.qty)} {base}")
    lines.append(f"Giriş fiyatı: {fmt_qty(view.entry_price, 8)} USDT")
    lines.append(f"Stop-loss: {fmt_qty(view.stop, 8) if view.stop is not None else '—'} USDT")
    lines.append(f"Take-profit: {fmt_qty(view.take_profit, 8) if view.take_profit is not None else '—'} USDT")
    if max_loss_at_stop is not None:
        lines.append(f"Stopta tahmini maksimum zarar: {fmt_money(max_loss_at_stop, signed=False, currency='')} USDT")
    lines.append(f"İşlem ID: {view.trade_id}")
    if reason:
        lines.append(f"Açılış nedeni: {reason}")
    return NotifyEvent(id=event_id(EVENT_OPENED, view.trade_id, view.opened_at), kind=EVENT_OPENED,
                       title="🟢 PAPER İŞLEM AÇILDI", text="\n".join(lines), created_at=created_at,
                       meta={"symbol": view.symbol, "trade_id": view.trade_id, "leverage": view.leverage})


def build_closed(trade: dict, *, net_pnl: Any, gross_pnl: Any = None, fees: Any = None,
                 funding: Any = None, margin: Any = None, created_at: str = "") -> NotifyEvent:
    """`✅/🔴 PAPER İŞLEM KAPANDI` mesajı. Zararda kırmızı simge ve negatif işaret."""
    net = dec(net_pnl)
    win = net > 0
    label, icon = close_reason_label(trade.get("exit_reason") or trade.get("close_reason"))
    sym = str(trade.get("symbol") or "")
    base = sym.split("/")[0]
    mkt = "SPOT" if str(trade.get("market_type", "")).upper() == "SPOT" else "FUTURES"
    lev = int(dec(trade.get("leverage"), Decimal("1")) or 1)
    qty = dec(trade.get("qty", trade.get("units")))
    lines = [f"Coin: {sym}", f"Piyasa: {mkt}",
             ("Kaldıraç: Yok" if mkt == "SPOT" else f"Kaldıraç: {lev}x"),
             f"Yön: {str(trade.get('side') or '').upper()}",
             f"Coin adedi: {fmt_qty(qty)} {base}",
             f"Giriş: {fmt_qty(trade.get('entry_avg', trade.get('entry')), 8)} USDT",
             f"Çıkış: {fmt_qty(trade.get('exit_avg', trade.get('exit')), 8)} USDT"]
    if gross_pnl is not None:
        lines.append(f"Brüt K/Z: {fmt_money(gross_pnl, currency='')} USDT")
    if fees is not None:
        lines.append(f"Ücretler: {fmt_money(-abs(dec(fees)), currency='')} USDT")
    if funding is not None:
        lines.append(f"Funding: {fmt_money(funding, currency='')} USDT")
    lines.append(f"Net K/Z: {fmt_money(net, currency='')} USDT")
    m = dec(margin)
    if mkt == "FUTURES" and m > 0:
        lines.append(f"Teminat getirisi: {fmt_pct(net / m * Decimal('100'))}")
    lines.append(f"Kapanış nedeni: {label}")
    lines.append(f"Süre: {_duration(str(trade.get('opened_at') or ''), str(trade.get('closed_at') or ''))}")
    lines.append(f"İşlem ID: {trade.get('id') or trade.get('trade_id') or '—'}")
    head = f"{icon if win else '🔴'} PAPER İŞLEM KAPANDI"
    ref = str(trade.get("closed_at") or trade.get("exit_id") or trade.get("seq") or "")
    return NotifyEvent(id=event_id(EVENT_CLOSED, str(trade.get("id") or trade.get("trade_id") or ""), ref),
                       kind=EVENT_CLOSED, title=head, text="\n".join(lines),
                       level="info" if win else "warning", created_at=created_at,
                       meta={"symbol": sym, "net_pnl": format(net, "f"), "reason": label})


def build_health(state: str, *, summary: str = "", recovered: bool = False, ref: str = "",
                 created_at: str = "") -> NotifyEvent:
    kind = EVENT_HEALTH_RECOVERED if recovered else EVENT_HEALTH_DEGRADED
    icon = "🟢" if recovered else "⚠️"
    title = f"{icon} PAPER WORKER {'YENİDEN SAĞLIKLI' if recovered else 'SAĞLIK SORUNU'}"
    text = "\n".join(x for x in (f"Durum: {state}", f"Özet: {summary}" if summary else "", "Mod: PAPER") if x)
    return NotifyEvent(id=event_id(kind, "worker", ref or state), kind=kind, title=title, text=text,
                       level="info" if recovered else "warning", created_at=created_at)


def build_daily_summary(pv: PortfolioView, *, day: str, opened: int = 0, closed: int = 0,
                        health: str = "UNKNOWN", tz_label: str = "UTC",
                        created_at: str = "") -> NotifyEvent:
    lines = [f"Tarih: {day} ({tz_label})", "Mod: PAPER",
             f"Açılan işlem: {opened}", f"Kapanan işlem: {closed}",
             f"Kazanan / Kaybeden: {pv.wins} / {pv.losses}",
             f"Gerçekleşen net K/Z: {fmt_money(pv.realized_today, currency='')} USDT",
             f"Açık pozisyon K/Z: {fmt_money(pv.open_net_unrealized, currency='')} USDT",
             f"Toplam ücret: {fmt_money(-abs(pv.total_fees), currency='')} USDT",
             f"Toplam funding: {fmt_money(pv.total_funding, currency='')} USDT",
             f"Açık pozisyon: {pv.open_total}", f"Sağlık: {health}"]
    return NotifyEvent(id=event_id(EVENT_DAILY_SUMMARY, "portfolio", day), kind=EVENT_DAILY_SUMMARY,
                       title="📊 PAPER GÜNLÜK ÖZET", text="\n".join(lines), created_at=created_at,
                       meta={"day": day})


__all__ = ["CLOSE_REASONS", "EVENT_CLOSED", "EVENT_DAILY_SUMMARY", "EVENT_HEALTH_DEGRADED",
           "EVENT_HEALTH_RECOVERED", "EVENT_OPENED", "NotifyEvent", "build_closed",
           "build_daily_summary", "build_health", "build_opened", "close_reason_label", "event_id"]
