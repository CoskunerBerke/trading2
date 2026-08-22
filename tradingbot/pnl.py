"""KANONİK KÂR/ZARAR VE GÖRÜNÜM KATMANI — panel ve Telegram TEK bu modülü kullanır.

Neden tek katman: panel `_positions_table()` içinde, Telegram ise mesaj kurucusunda ayrı formül
kullansaydı aynı işlem iki farklı sayı gösterirdi. Buradaki `position_view()` / `portfolio_view()`
çıktıları her iki tüketicinin de tek kaynağıdır (`tests/test_pnl_views.py` eşitliği zorlar).

Hesap Decimal ile yapılır; yuvarlama YALNIZ sunum katmanındadır (`fmt_money`/`fmt_pct`). Ücret ve
funding ASLA iki kez düşülmez:

* ``gross_unrealized``  = qty × (mark − entry) × yön   (ücret/funding YOK)
* ``net_unrealized``    = gross − giriş ücreti − tahmini kapanış ücreti + net funding
* ``realized_net``      = defterin `realized_pnl`'i (giriş+çıkış ücreti İÇİNDE) + net funding

`realized_pnl` alanı `FuturesLedgerV2` sözleşmesinde "net (ücret dahil, funding hariç)" demektir;
bu yüzden burada ücret TEKRAR düşülmez, yalnız funding eklenir.

Yüzde paydası (UI'da açıkça yazılır):
* FUTURES → kullanılan başlangıç teminatı (`initial_margin`)
* SPOT    → yatırılan maliyet (`notional`)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")
_HUNDRED = Decimal("100")
DEFAULT_TAKER_PCT = Decimal("0.05")          # %0.05 — FeeSchedule.taker_pct varsayılanı

PCT_BASIS_MARGIN = "initial_margin"          # futures
PCT_BASIS_COST = "cost_basis"                # spot


def dec(x: Any, default: Decimal = ZERO) -> Decimal:
    """Her türlü girdiyi güvenle Decimal'e çevir (None/boş/bozuk → default)."""
    if x is None or x == "":
        return default
    if isinstance(x, Decimal):
        return x
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return default
    return default if d != d else d          # NaN koruması


# ============================================================================ sunum yardımcıları
def fmt_money(x: Any, *, signed: bool = True, currency: str = "$") -> str:
    """Para gösterimi. Küçük değerler `+0.00`'a YUVARLANMAZ.

    |x| < 0.01 ve x != 0 → 6 anlamlı ondalık (ör. `+$0.004213`), böylece gerçek küçük kâr/zarar
    kullanıcıya sıfır gibi görünmez. Hesap hassasiyeti bu fonksiyondan bağımsızdır.
    """
    v = dec(x, Decimal("NaN") if x is None else ZERO)
    if v != v:
        return "—"
    sign = "+" if (signed and v > 0) else ("-" if v < 0 else ("" if not signed else ""))
    a = abs(v)
    if a == 0:
        return f"{currency}0.00"
    if a < Decimal("0.01"):
        return f"{sign}{currency}{a:.6f}".rstrip("0").rstrip(".")
    return f"{sign}{currency}{a:,.2f}"


def fmt_pct(x: Any, nd: int = 2) -> str:
    v = dec(x, Decimal("NaN") if x is None else ZERO)
    if v != v:
        return "—"
    if v == 0:
        return "0.00%"
    return f"{v:+,.{nd}f}%"


def fmt_qty(x: Any, nd: int = 8) -> str:
    """Coin/kontrat ADEDİ — USDT DEĞİL. Gereksiz sıfırlar kırpılır."""
    v = dec(x, Decimal("NaN") if x is None else ZERO)
    if v != v:
        return "—"
    s = f"{v:,.{nd}f}".rstrip("0").rstrip(".")
    return s or "0"


def pnl_class(x: Any) -> str:
    """CSS sınıfı — renk TEK BAŞINA anlam taşımaz, `+/-` işareti her zaman yazılır."""
    v = dec(x)
    return "up" if v > 0 else ("dn" if v < 0 else "flat")


# ============================================================================ pozisyon görünümü
@dataclass(frozen=True)
class PositionView:
    """Tek açık pozisyonun kanonik görünümü. Panel ve Telegram AYNI nesneyi okur."""
    trade_id: str
    symbol: str
    market: str                       # SPOT | FUTURES
    side: str                         # LONG | SHORT
    qty: Decimal                      # coin/kontrat ADEDİ (USDT değil)
    entry_price: Decimal
    mark_price: Decimal | None
    leverage: int
    notional: Decimal                 # pozisyon değeri (USDT)
    initial_margin: Decimal           # kullanılan başlangıç teminatı (USDT)
    stop: Decimal | None
    take_profit: Decimal | None
    liquidation_price: Decimal | None
    entry_fee: Decimal
    exit_fee_est: Decimal
    funding_net: Decimal              # + alındı / − ödendi
    gross_unrealized: Decimal
    net_unrealized: Decimal
    net_unrealized_pct: Decimal | None
    pct_basis: str
    opened_at: str
    price_is_stale: bool = False      # mark yok → PnL güvenilir değil
    meta: dict = field(default_factory=dict)

    @property
    def has_mark(self) -> bool:
        return self.mark_price is not None and self.mark_price > 0

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            d[k] = format(v, "f") if isinstance(v, Decimal) else v
        return d


def _fee_rate(fees: Any) -> Decimal:
    """Taker oranı (kesir). Defterin `fees` sözlüğü ya da FeeSchedule kabul edilir."""
    if fees is None:
        return DEFAULT_TAKER_PCT / _HUNDRED
    rate = getattr(fees, "rate", None)
    if callable(rate):
        try:
            return dec(rate(False), DEFAULT_TAKER_PCT / _HUNDRED)
        except (TypeError, ValueError):
            pass
    if isinstance(fees, dict):
        return dec(fees.get("taker_pct"), DEFAULT_TAKER_PCT) / _HUNDRED
    return DEFAULT_TAKER_PCT / _HUNDRED


def position_view(pos: dict, *, mark_price: Any = None, fees: Any = None,
                  market: str | None = None) -> PositionView:
    """Ledger pozisyon sözlüğü → kanonik görünüm.

    `mark_price` verilmezse sırayla `last_price` ve `entry_avg` denenir; `entry_avg`'a düşüldüğünde
    `price_is_stale=True` olur ve PnL sıfır çıkar — bu durum UI'da AÇIKÇA işaretlenir (sessizce
    `+0.00` gösterilmez).

    Not: eski panel `pos.get("unrealized")` okuyordu; `Position.to_dict()` böyle bir ANAHTAR
    üretmez (`unrealized` bir METOTTUR) ve kod sessizce `realized_pnl`'e (açık pozisyonda 0)
    düşüyordu. Açık pozisyonların sürekli `+0.00` görünmesinin sebebi buydu.
    """
    side = str(pos.get("side") or "LONG").upper()
    sign = Decimal("1") if side == "LONG" else Decimal("-1")
    qty = dec(pos.get("qty", pos.get("units")))
    entry = dec(pos.get("entry_avg", pos.get("entry")))
    lev = int(dec(pos.get("leverage"), Decimal("1")) or 1)
    mkt = (market or ("SPOT" if str(pos.get("market_type", "")).upper() == "SPOT" else "FUTURES")).upper()

    mark = dec(mark_price, ZERO) if mark_price not in (None, "") else ZERO
    stale = False
    if mark <= 0:
        mark = dec(pos.get("last_price"), ZERO)
    if mark <= 0:
        mark, stale = entry, True                       # fiyat yok → PnL 0, fakat İŞARETLİ

    notional = dec(pos.get("notional")) or (qty * entry)
    margin = dec(pos.get("isolated_margin", pos.get("margin")))
    if margin <= 0 and lev > 0:
        margin = notional / Decimal(lev)

    entry_fee = dec(pos.get("entry_fee"))
    if entry_fee <= 0:
        entry_fee = dec(pos.get("fees_paid"))           # eski kayıtlar: toplam ücret alanı
    rate = _fee_rate(fees)
    exit_fee_est = abs(qty * mark) * rate
    funding_net = (dec(pos.get("funding_received")) - dec(pos.get("funding_paid"))
                   if ("funding_received" in pos or "funding_paid" in pos)
                   else dec(pos.get("funding_net", pos.get("funding"))))

    gross = qty * (mark - entry) * sign
    net = gross - entry_fee - exit_fee_est + funding_net

    basis = margin if mkt == "FUTURES" else notional
    pct = (net / basis * _HUNDRED) if basis > 0 else None

    targets = pos.get("targets") or []
    tp = dec(targets[0]) if targets else None

    return PositionView(
        trade_id=str(pos.get("id") or pos.get("trade_id") or ""),
        symbol=str(pos.get("symbol") or ""), market=mkt, side=side, qty=qty, entry_price=entry,
        mark_price=None if stale else mark, leverage=lev, notional=notional, initial_margin=margin,
        stop=dec(pos["stop"]) if pos.get("stop") not in (None, "") else None,
        take_profit=tp,
        liquidation_price=dec(pos["liquidation_price"]) if pos.get("liquidation_price") not in (None, "") else None,
        entry_fee=entry_fee, exit_fee_est=exit_fee_est, funding_net=funding_net,
        gross_unrealized=gross, net_unrealized=net, net_unrealized_pct=pct,
        pct_basis=PCT_BASIS_MARGIN if mkt == "FUTURES" else PCT_BASIS_COST,
        opened_at=str(pos.get("opened_at") or ""), price_is_stale=stale,
        meta={"leverage_reasons": (pos.get("meta") or {}).get("leverage_reasons")} if isinstance(pos.get("meta"), dict) else {})


# ============================================================================ kapanmış işlem
def realized_net(trade: dict) -> Decimal:
    """Kapanmış işlemin NET gerçekleşen K/Z'si.

    `FuturesLedgerV2` sözleşmesi: `realized_pnl` giriş+çıkış ücretini ZATEN içerir, funding'i
    içermez. Bu yüzden ücret TEKRAR düşülmez; yalnız net funding eklenir (çift sayım yok).
    """
    base = trade.get("net_pnl")
    if base is not None:                       # defter net alanı verdiyse o KANONİKTİR
        return dec(base)
    pnl = dec(trade.get("realized_pnl", trade.get("pnl")))
    funding = (dec(trade.get("funding_received")) - dec(trade.get("funding_paid"))
               if ("funding_received" in trade or "funding_paid" in trade)
               else dec(trade.get("funding_net", trade.get("funding"))))
    return pnl + funding


# ============================================================================ portföy görünümü
@dataclass(frozen=True)
class PortfolioView:
    positions: list[PositionView]
    open_long: int
    open_short: int
    open_total: int
    long_notional: Decimal
    short_notional: Decimal
    open_margin: Decimal
    open_net_unrealized: Decimal
    realized_today: Decimal
    realized_total: Decimal
    total_net: Decimal
    wins: int
    losses: int
    breakeven: int
    win_rate: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal | None
    total_fees: Decimal
    total_funding: Decimal
    any_stale_price: bool

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if k == "positions":
                d[k] = [p.to_dict() for p in v]
            elif isinstance(v, Decimal):
                d[k] = format(v, "f")
            else:
                d[k] = v
        return d


def _day_of(ts: Any) -> str:
    return str(ts or "")[:10]


def portfolio_view(positions: list[dict], trades: list[dict], *, marks: dict[str, Any] | None = None,
                   fees: Any = None, today: str | None = None,
                   max_drawdown_pct: Any = None) -> PortfolioView:
    """Açık pozisyonlar + kapanmış işlemler → tek kanonik portföy özeti.

    `today` `YYYY-MM-DD` (UTC) biçimindedir; verilmezse günlük kırılım 0 döner (uydurma yapılmaz).
    """
    marks = marks or {}
    views = [position_view(p, mark_price=marks.get(str(p.get("symbol") or "")), fees=fees) for p in positions]
    longs = [v for v in views if v.side == "LONG"]
    shorts = [v for v in views if v.side == "SHORT"]

    realized_total = ZERO
    realized_today_v = ZERO
    wins = losses = breakeven = 0
    gross_win = gross_loss = ZERO
    total_fees = ZERO
    total_funding = ZERO
    for t in trades:
        r = realized_net(t)
        realized_total += r
        if today and _day_of(t.get("closed_at") or t.get("exit_time") or t.get("ts")) == today:
            realized_today_v += r
        if r > 0:
            wins += 1
            gross_win += r
        elif r < 0:
            losses += 1
            gross_loss += -r
        else:
            breakeven += 1
        total_fees += dec(t.get("fees", t.get("fees_paid")))
        total_funding += (dec(t.get("funding_received")) - dec(t.get("funding_paid"))
                          if ("funding_received" in t or "funding_paid" in t)
                          else dec(t.get("funding_net", t.get("funding"))))

    open_net = sum((v.net_unrealized for v in views), ZERO)
    decided = wins + losses
    return PortfolioView(
        positions=views, open_long=len(longs), open_short=len(shorts), open_total=len(views),
        long_notional=sum((v.notional for v in longs), ZERO),
        short_notional=sum((v.notional for v in shorts), ZERO),
        open_margin=sum((v.initial_margin for v in views), ZERO),
        open_net_unrealized=open_net, realized_today=realized_today_v, realized_total=realized_total,
        total_net=realized_total + open_net, wins=wins, losses=losses, breakeven=breakeven,
        win_rate=(Decimal(wins) / Decimal(decided) * _HUNDRED) if decided else None,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        max_drawdown=dec(max_drawdown_pct) if max_drawdown_pct is not None else None,
        total_fees=total_fees, total_funding=total_funding,
        any_stale_price=any(v.price_is_stale for v in views))


# ============================================================================ tutarlılık kontrolü
@dataclass(frozen=True)
class Inconsistency:
    code: str
    message: str


def check_invariants(pv: PortfolioView, *, table_rows: int | None = None,
                     chief_breadth: dict | None = None) -> list[Inconsistency]:
    """Panelde sessizce yanlış sayı göstermemek için değişmezler.

        gerçek_açık_pozisyon_sayısı == açık_pozisyonlar_tablosundaki_satır_sayısı
        gerçek_açık_long + gerçek_açık_short == gerçek_açık_toplam

    `chief_breadth` YALNIZ bilgi amaçlı karşılaştırılır: `breadth.long` AÇIK POZİSYON DEĞİL, son
    turdaki LONG *aday/karar* sayısıdır — eşit olmaması tutarsızlık DEĞİLDİR ve uyarı üretmez.
    """
    out: list[Inconsistency] = []
    if pv.open_long + pv.open_short != pv.open_total:
        out.append(Inconsistency("OPEN_SIDE_SUM_MISMATCH",
                                 f"açık LONG ({pv.open_long}) + SHORT ({pv.open_short}) ≠ toplam ({pv.open_total})"))
    if table_rows is not None and table_rows != pv.open_total:
        out.append(Inconsistency("OPEN_COUNT_TABLE_MISMATCH",
                                 f"açık pozisyon sayacı ({pv.open_total}) ≠ tablo satırı ({table_rows})"))
    return out


__all__ = ["DEFAULT_TAKER_PCT", "Inconsistency", "PCT_BASIS_COST", "PCT_BASIS_MARGIN", "PortfolioView",
           "PositionView", "check_invariants", "dec", "fmt_money", "fmt_pct", "fmt_qty", "pnl_class",
           "portfolio_view", "position_view", "realized_net"]
