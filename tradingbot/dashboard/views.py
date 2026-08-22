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

from ..pnl import (PortfolioView, PositionView, canonical_summary, check_invariants, fmt_money,
                   fmt_pct, fmt_qty, portfolio_view)

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
    open_risk_usdt: Any               # risk motoru REZERVASYONU (stop riskinden AYRI kavram)
    margin_util_pct: Any
    realized_today: Any
    unrealized_open: Any
    drawdown_pct: Any
    open_stop_risk_usdt: Any = None   # stop'a kadar BRÜT tahmini kayıp (defterden hesaplanır)
    risk_budget_max_usdt: Any = None
    risk_budget_util_pct: Any = None

    def to_dict(self) -> dict:
        return {k: (format(v, "f") if hasattr(v, "quantize") else v) for k, v in self.__dict__.items()}


def chief_view(chief: dict | None, pv: PortfolioView, summary: dict | None = None) -> ChiefView:
    """Chief snapshot'ı (ADAYLAR) + defter (AÇIK POZİSYONLAR) → tek okunur model.

    İki kaynak FARKLI zamanlara ait olabilir; bu yüzden aday sayıları ile açık pozisyon sayıları
    ayrı alanlarda tutulur ve panelde ayrı etiketlerle gösterilir.

    RİSK/TEMİNAT/DRAWDOWN alanları artık `coin_heads.json → chief.exposure` yerine KANONİK özetten
    gelir. Sebep: chief snapshot'ı bu alanları strateji turunda kopyalar ve `margin_util_pct` orada
    `float(ps.get("margin_util_pct", 0.0))` ile üretilir — risk durumunda böyle bir anahtar
    OLMADIĞI için her zaman `0.0` yazılıyordu (panelde «%0.0»). Kanonik değer defterden hesaplanır,
    böylece özet kartı ile baş yönetici kartı YAPI GEREĞİ aynı sayıyı gösterir.
    """
    c = chief or {}
    b = c.get("breadth") or {}
    s = summary or {}
    return ChiefView(
        generated_at=str(c.get("generated_at") or ""),
        market_risk_mode=str(c.get("market_risk_mode") or "—"),
        long_candidates=int(b.get("long") or 0), short_candidates=int(b.get("short") or 0),
        no_trade=int(b.get("no_trade") or 0), hold=int(b.get("hold") or 0),
        data_invalid=int(b.get("data_invalid") or 0),
        open_long=pv.open_long, open_short=pv.open_short, open_total=pv.open_total,
        long_notional=pv.long_notional, short_notional=pv.short_notional,
        open_risk_usdt=s.get("risk_engine_reserved_usdt"),
        margin_util_pct=s.get("margin_utilization_pct"),
        realized_today=pv.realized_today, unrealized_open=pv.open_net_unrealized,
        drawdown_pct=s.get("max_drawdown_pct", _num(pv.max_drawdown)),
        open_stop_risk_usdt=s.get("open_stop_risk_usdt"),
        risk_budget_max_usdt=s.get("risk_budget_max_usdt"),
        risk_budget_util_pct=s.get("open_risk_budget_utilization_pct"))


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


NO_DATA = "Veri yok"


@dataclass(frozen=True)
class SummaryCard:
    """Kart modeli: `value` MAKİNE için ham sayı, `display` İNSAN için biçimlenmiş metin.

    Panel HTML'i `display`'i OLDUĞU GİBİ basar. Daha önce zaten biçimlenmiş metin ikinci kez
    `money_html()`'e veriliyor, `Decimal("+$2.86")` çözülemediği için sessizce `$0.00`'a düşüyordu.
    """
    key: str
    title: str
    value: Any            # float | None  (None → hesaplanamadı)
    display: str
    kind: str             # money | pct | ratio | pair | count | text
    sub: str = ""

    @property
    def signed(self) -> bool:
        """Renk/işaret uygulanacak mı? Oran ve sayaçlar nötrdür."""
        return self.kind in ("money", "pct_signed")

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "value": self.value,
                "display": self.display, "kind": self.kind, "sub": self.sub}


def profit_factor_value(pv: PortfolioView) -> float | None:
    """Kanonik profit factor değeri.

      brüt zarar > 0            → gross_profit / abs(gross_loss)
      zarar yok, kâr var        → `inf`
      kapanmış işlem yok        → None  («Veri yok»)
      gerçekten 0               → 0.0
    """
    if pv.closed_trades == 0:
        return None
    v = _num(pv.profit_factor)
    return float("inf") if v is None else v


def _pf_display(pf: float | None) -> str:
    """Profit factor gösterimi: `$` YOK, `+` YOK — bu bir ORANDIR."""
    if pf is None:
        return NO_DATA
    return "∞" if pf == float("inf") else f"{pf:.2f}"


def summary_cards(pv: PortfolioView, summary: dict | None = None) -> list[SummaryCard]:
    """Genel kâr/zarar + risk özeti. Hesaplanamayan alan sessizce `0` DEĞİL, `Veri yok` olur."""
    s = summary or {}

    def pct1(x, nd=1):
        return NO_DATA if x is None else f"%{float(x):.{nd}f}"

    _pf = profit_factor_value(pv)
    cards = [
        SummaryCard("today_realized_net_usdt", "Bugün gerçekleşen net K/Z", _num(pv.realized_today),
                    fmt_money(pv.realized_today), "money", "kapanan işlemler (UTC gün)"),
        SummaryCard("all_time_realized_net_usdt", "Toplam gerçekleşen net K/Z", _num(pv.realized_total),
                    fmt_money(pv.realized_total), "money", "ücret + funding dahil"),
        SummaryCard("open_net_usdt", "Açık pozisyon net K/Z", _num(pv.open_net_unrealized),
                    fmt_money(pv.open_net_unrealized), "money",
                    "tahmini kapanış ücreti düşülmüş" + (" · ⚠ fiyat yok" if pv.any_stale_price else "")),
        SummaryCard("total_net_usdt", "Toplam net K/Z", _num(pv.total_net), fmt_money(pv.total_net),
                    "money", "gerçekleşen + açık"),
        SummaryCard("win_loss", "Kazanan / Kaybeden", None, f"{pv.wins} / {pv.losses}", "pair",
                    f"başa baş {pv.breakeven} · kapanmış {pv.closed_trades}"),
        SummaryCard("win_rate_pct", "Kazanma oranı", _num(pv.win_rate), pct1(_num(pv.win_rate)), "pct",
                    "kazanan / (kazanan + kaybeden) — başa baş HARİÇ"),
        SummaryCard("profit_factor", "Profit factor", _pf, _pf_display(_pf),
                    "ratio", "brüt kâr / brüt zarar — oran, para birimi değil"),
        SummaryCard("max_drawdown_pct", "Maks. drawdown", _num(pv.max_drawdown),
                    pct1(_num(pv.max_drawdown), 2), "pct", "risk motoru (risk.json)"),
    ]
    if summary is not None:
        mu, sr = s.get("margin_utilization_pct"), s.get("open_stop_risk_usdt")
        partial = " · ⚠ stop'suz pozisyon var" if s.get("open_stop_risk_is_partial") else ""
        cards += [
            SummaryCard("open_futures_margin_usdt", "Açık futures teminatı", s.get("open_futures_margin_usdt"),
                        NO_DATA if s.get("open_futures_margin_usdt") is None
                        else fmt_money(s["open_futures_margin_usdt"], signed=False, currency="") + " USDT",
                        "money_plain", "kullanılan başlangıç marjı"),
            SummaryCard("margin_utilization_pct", "Teminat kullanımı", mu, pct1(mu),
                        "pct", "açık teminat / futures özkaynak"),
            SummaryCard("open_stop_risk_usdt", "Açık stop riski", sr,
                        NO_DATA if sr is None else fmt_money(sr, signed=False, currency="") + " USDT",
                        "money_plain", "stop'a kadar BRÜT tahmini kayıp (ücret hariç)" + partial),
            SummaryCard("risk_engine_reserved_usdt", "Risk motoru rezervasyonu",
                        s.get("risk_engine_reserved_usdt"),
                        NO_DATA if s.get("risk_engine_reserved_usdt") is None
                        else fmt_money(s["risk_engine_reserved_usdt"], signed=False, currency="") + " USDT",
                        "money_plain", "risk.json → total_open_risk_usdt (stop riskinden AYRI kavram)"),
            SummaryCard("open_risk_budget_utilization_pct", "Risk bütçesi kullanımı",
                        s.get("open_risk_budget_utilization_pct"),
                        pct1(s.get("open_risk_budget_utilization_pct")), "pct",
                        NO_DATA if s.get("risk_budget_max_usdt") is None
                        else "azami %s USDT" % fmt_money(s["risk_budget_max_usdt"], signed=False, currency="")),
        ]
    return cards


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def build(state_positions: list[dict], trades: list[dict], chief: dict | None, *,
          marks: dict[str, Any] | None = None, fees: Any = None, today: str | None = None,
          max_drawdown_pct: Any = None, freshness: Freshness | None = None,
          futures_equity: Any = None, spot_equity: Any = None, risk_state: dict | None = None,
          as_of: str | None = None) -> dict:
    """Panelin TEK giriş noktası: her bölüm için hazır, tutarlılığı denetlenmiş model.

    `summary` KANONİK özettir; HTML sayfası da `/api/live/summary` de AYNI sözlüğü kullanır,
    böylece iki yüzey farklı sayı gösteremez.
    """
    pv = portfolio_view(state_positions, trades, marks=marks, fees=fees, today=today,
                        max_drawdown_pct=max_drawdown_pct)
    fr = freshness.to_dict() if freshness else None
    summary = canonical_summary(pv, futures_equity=futures_equity, spot_equity=spot_equity,
                                risk_state=risk_state, as_of=as_of, source_freshness=fr)
    rows = [position_row(v) for v in pv.positions]
    issues = check_invariants(pv, table_rows=len(rows))
    return {"portfolio": pv, "chief": chief_view(chief, pv, summary), "rows": rows,
            "columns": POSITION_COLUMNS, "cards": summary_cards(pv, summary), "summary": summary,
            "inconsistencies": [i.__dict__ for i in issues], "freshness": fr}


__all__ = ["ChiefView", "Freshness", "LIVE_OK", "LIVE_STALE", "LIVE_UNKNOWN", "NO_DATA",
           "POSITION_COLUMNS", "SummaryCard", "build", "chief_view", "position_row", "summary_cards"]
