"""Panel GÖRÜNÜM MODELİ — ham JSON yerine etiketli, tutarlılığı denetlenmiş kartlar/tablolar.

Bütün sayılar kanonik `tradingbot.pnl` katmanından gelir; Telegram da AYNI katmanı kullanır.

KAVRAM AYRIMI (kullanıcı şikâyetinin kaynağı):
* `breadth.long` = son STRATEJİ TURUNDAKİ **LONG işlem adayı/kararı** sayısıdır.
* Açık LONG **pozisyon** sayısı defterden gelir ve tamamen ayrı bir büyüklüktür.
  `chief.breadth.long = 3` iken `açık pozisyon = 2` olması tutarsızlık DEĞİLDİR: 3 yeni LONG
  adayı + 2 açık pozisyon (bunlar `breadth.hold` içinde sayılır) demektir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..pnl import (PortfolioView, PositionView, check_invariants, fmt_money, fmt_pct, fmt_qty,
                   portfolio_view)

LIVE_OK = "live"
LIVE_STALE = "stale"
LIVE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Freshness:
    """Her bölümün veri zamanı ayrı ayrı raporlanır — fiyat yaşı ile tur yaşı KARIŞTIRILMAZ."""
    price_age_s: float | None
    run_age_s: float | None
    heads_age_s: float | None
    heartbeat_age_s: float | None
    stale_price_s: int
    stale_run_s: int
    tz_label: str = "UTC"

    @property
    def price_state(self) -> str:
        if self.price_age_s is None:
            return LIVE_UNKNOWN
        return LIVE_OK if self.price_age_s <= self.stale_price_s else LIVE_STALE

    @property
    def run_state(self) -> str:
        if self.run_age_s is None:
            return LIVE_UNKNOWN
        return LIVE_OK if self.run_age_s <= self.stale_run_s else LIVE_STALE

    def to_dict(self) -> dict:
        return {"price_age_s": self.price_age_s, "run_age_s": self.run_age_s,
                "heads_age_s": self.heads_age_s, "heartbeat_age_s": self.heartbeat_age_s,
                "price_state": self.price_state, "run_state": self.run_state,
                "stale_price_s": self.stale_price_s, "stale_run_s": self.stale_run_s,
                "tz_label": self.tz_label}


@dataclass(frozen=True)
class ChiefView:
    """Baş yönetici bölümünün OKUNUR modeli — uzun ham JSON gösterilmez."""
    generated_at: str
    market_risk_mode: str
    long_candidates: int              # İŞLEM ADAYI (açık pozisyon DEĞİL)
    short_candidates: int
    no_trade: int
    hold: int
    data_invalid: int
    open_long: int                    # GERÇEK açık pozisyon (defterden)
    open_short: int
    open_total: int
    long_notional: Any
    short_notional: Any
    open_risk_usdt: Any
    margin_util_pct: Any
    realized_today: Any
    unrealized_open: Any
    drawdown_pct: Any

    def to_dict(self) -> dict:
        return {k: (format(v, "f") if hasattr(v, "quantize") else v) for k, v in self.__dict__.items()}


def chief_view(chief: dict | None, pv: PortfolioView) -> ChiefView:
    """Chief snapshot'ı (ADAYLAR) + defter (AÇIK POZİSYONLAR) → tek okunur model.

    İki kaynak FARKLI zamanlara ait olabilir; bu yüzden aday sayıları ile açık pozisyon sayıları
    ayrı alanlarda tutulur ve panelde ayrı etiketlerle gösterilir.
    """
    c = chief or {}
    b = c.get("breadth") or {}
    e = c.get("exposure") or {}
    return ChiefView(
        generated_at=str(c.get("generated_at") or ""),
        market_risk_mode=str(c.get("market_risk_mode") or "—"),
        long_candidates=int(b.get("long") or 0), short_candidates=int(b.get("short") or 0),
        no_trade=int(b.get("no_trade") or 0), hold=int(b.get("hold") or 0),
        data_invalid=int(b.get("data_invalid") or 0),
        open_long=pv.open_long, open_short=pv.open_short, open_total=pv.open_total,
        long_notional=pv.long_notional, short_notional=pv.short_notional,
        open_risk_usdt=e.get("risk_used_usdt", e.get("open_risk_usdt")),
        margin_util_pct=e.get("margin_util_pct"),
        realized_today=pv.realized_today, unrealized_open=pv.open_net_unrealized,
        drawdown_pct=e.get("drawdown_pct"))


# --------------------------------------------------------------------------- tablo satırları
POSITION_COLUMNS = ["Sembol", "Piyasa", "Yön", "Coin adedi", "Giriş", "Mark/Son", "Kald.",
                    "Notional (USDT)", "Teminat (USDT)", "Stop", "TP", "Likidasyon",
                    "Açılış ücreti", "Funding", "Brüt K/Z", "Tah. kapanış ücreti",
                    "Net K/Z (USDT)", "Net K/Z (%)", "Açılış", "İşlem ID"]


def position_row(v: PositionView) -> list[Any]:
    """Bir açık pozisyonun tablo satırı. `Coin adedi` USDT DEĞİL, coin/kontrat adedidir."""
    return [
        v.symbol, v.market, v.side, fmt_qty(v.qty),
        fmt_qty(v.entry_price, 8),
        ("—" if v.mark_price is None else fmt_qty(v.mark_price, 8)),
        ("—" if v.market == "SPOT" else f"{v.leverage}x"),
        fmt_money(v.notional, signed=False, currency=""),
        fmt_money(v.initial_margin, signed=False, currency=""),
        ("—" if v.stop is None else fmt_qty(v.stop, 8)),
        ("—" if v.take_profit is None else fmt_qty(v.take_profit, 8)),
        ("—" if v.liquidation_price is None else fmt_qty(v.liquidation_price, 8)),
        fmt_money(-abs(v.entry_fee), currency=""),
        fmt_money(v.funding_net, currency=""),
        fmt_money(v.gross_unrealized, currency=""),
        fmt_money(-abs(v.exit_fee_est), currency=""),
        fmt_money(v.net_unrealized),
        ("—" if v.net_unrealized_pct is None else fmt_pct(v.net_unrealized_pct)),
        v.opened_at, v.trade_id,
    ]


def summary_cards(pv: PortfolioView) -> list[tuple[str, str, str]]:
    """(başlık, değer, altyazı) — genel kâr/zarar özeti. Veri yoksa `Veri yok` yazılır."""
    def _or_none(x, f):
        return "Veri yok" if x is None else f(x)
    return [
        ("Bugün gerçekleşen net K/Z", fmt_money(pv.realized_today), "kapanan işlemler (UTC gün)"),
        ("Toplam gerçekleşen net K/Z", fmt_money(pv.realized_total), "ücret + funding dahil"),
        ("Açık pozisyon net K/Z", fmt_money(pv.open_net_unrealized),
         "tahmini kapanış ücreti düşülmüş" + (" · ⚠ fiyat yok" if pv.any_stale_price else "")),
        ("Toplam net K/Z", fmt_money(pv.total_net), "gerçekleşen + açık"),
        ("Kazanan / Kaybeden", f"{pv.wins} / {pv.losses}", f"başa baş {pv.breakeven}"),
        ("Kazanma oranı", _or_none(pv.win_rate, lambda x: f"%{x:.1f}"), "karara bağlanan işlemler"),
        ("Profit factor", _or_none(pv.profit_factor, lambda x: f"{x:.2f}"), "brüt kâr / brüt zarar"),
        ("Maks. drawdown", _or_none(pv.max_drawdown, lambda x: f"%{x:.2f}"), "risk state"),
    ]


def build(state_positions: list[dict], trades: list[dict], chief: dict | None, *,
          marks: dict[str, Any] | None = None, fees: Any = None, today: str | None = None,
          max_drawdown_pct: Any = None, freshness: Freshness | None = None) -> dict:
    """Panelin TEK giriş noktası: her bölüm için hazır, tutarlılığı denetlenmiş model."""
    pv = portfolio_view(state_positions, trades, marks=marks, fees=fees, today=today,
                        max_drawdown_pct=max_drawdown_pct)
    rows = [position_row(v) for v in pv.positions]
    issues = check_invariants(pv, table_rows=len(rows))
    return {"portfolio": pv, "chief": chief_view(chief, pv), "rows": rows,
            "columns": POSITION_COLUMNS, "cards": summary_cards(pv),
            "inconsistencies": [i.__dict__ for i in issues],
            "freshness": (freshness.to_dict() if freshness else None)}


__all__ = ["ChiefView", "Freshness", "LIVE_OK", "LIVE_STALE", "LIVE_UNKNOWN", "POSITION_COLUMNS",
           "build", "chief_view", "position_row", "summary_cards"]
