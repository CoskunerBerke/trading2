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

from ..pnl import (FRESH_OK, FRESH_STALE, FRESH_UNKNOWN, PF_POSITIVE_INFINITY, PortfolioView,
                   PositionView, canonical_summary, check_invariants, finite_float_or_none,
                   fmt_money, fmt_pct, fmt_qty, money_totals_unavailable_reason, portfolio_view,
                   profit_factor_state)

# Tazelik adları `pnl` katmanında TEK yerde tanımlıdır; burada yalnız yeniden dışa verilir
# (iki ayrı string listesi zamanla ayrışır ve `stale` sessizce `live` gibi görünürdü).
LIVE_OK = FRESH_OK
LIVE_STALE = FRESH_STALE
LIVE_UNKNOWN = FRESH_UNKNOWN


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
    risk_equity_basis_usdt: Any = None    # motorun KABUL kararında kullandığı taban
    risk_equity_basis_kind: Any = None    # starting_equity | live_equity
    risk_snapshot_age_s: Any = None       # risk.json yaşı (fiyat yaşından AYRI)
    risk_snapshot_state: Any = None       # live | stale | unknown
    # --- ÜÇ AYRI KAVRAM: tek kartta TOPLANMAZ (eski snapshot'ta None → «Veri yok») ---
    futures_stop_risk_usdt: Any = None    # kabul kapısının GERÇEK kovası
    futures_risk_budget_util_pct: Any = None
    spot_exposure_usdt: Any = None        # spot notional maruziyeti — RİSK DEĞİL
    spot_stop_risk_usdt: Any = None       # yalnız gerçek duran stop emri olan spot
    spot_symbols_without_stop: Any = None
    spot_allocation_util_pct: Any = None

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
        drawdown_pct=s.get("max_drawdown_pct", finite_float_or_none(pv.max_drawdown)),
        open_stop_risk_usdt=s.get("open_stop_risk_usdt"),
        risk_budget_max_usdt=s.get("risk_budget_max_usdt"),
        risk_budget_util_pct=s.get("open_risk_budget_utilization_pct"),
        risk_equity_basis_usdt=s.get("risk_equity_basis_usdt"),
        risk_equity_basis_kind=s.get("risk_equity_basis_kind"),
        risk_snapshot_age_s=s.get("risk_snapshot_age_s"),
        risk_snapshot_state=s.get("risk_snapshot_state"),
        futures_stop_risk_usdt=s.get("futures_stop_risk_usdt"),
        futures_risk_budget_util_pct=s.get("futures_risk_budget_utilization_pct"),
        spot_exposure_usdt=s.get("spot_exposure_usdt"),
        spot_stop_risk_usdt=s.get("spot_stop_risk_usdt"),
        spot_symbols_without_stop=list(s.get("spot_symbols_without_stop") or []),
        spot_allocation_util_pct=s.get("spot_allocation_utilization_pct"))


# --------------------------------------------------------------------------- tablo satırları
POSITION_COLUMNS = ["Sembol", "Piyasa", "Yön", "Coin adedi", "Giriş", "Mark/Son", "Kald.",
                    "Notional (USDT)", "Teminat (USDT)", "Stop", "TP", "Likidasyon",
                    "Açılış ücreti", "Funding", "Brüt K/Z", "Tah. kapanış ücreti",
                    "Net K/Z (USDT)", "Net K/Z (%)", "Açılış", "İşlem ID"]

# HİZALAMA SÖZLEŞMESİ — sunucu render'ı (`app._positions_table`) ve polling JS'i AYNI listeyi
# kullanır. Önce sunucu `range(3, 18)`, JS ise `i >= 3` diyordu → «Açılış» ve «İşlem ID» metin
# sütunları 7 saniyelik ilk polling'den sonra sağa hizalanıyordu (ölçülen: 60 vs 68 `td.num`).
POSITION_NUM_COLS = tuple(range(3, 18))      # sayısal hizalama (sağa yaslı, tabular-nums)
POSITION_PNL_COLS = (16, 17)                 # YALNIZ net K/Z alanları `up/dn/flat` renk alır


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
    """Profit factor'ün SAYISAL değeri — `inf`/`NaN` ASLA döndürmez (JSON sınırı için).

    Sonsuzluk bilgisi sayıda değil, `pnl.profit_factor_state()`'in ikinci alanında taşınır.
    """
    return profit_factor_state(pv)[0]


def _pf_display(pf_state: str, pf: float | None) -> str:
    """Profit factor gösterimi: `$` YOK, `+` YOK — bu bir ORANDIR.

    `∞` YALNIZ gerçekten sonsuz olduğunda (zarar 0, kâr > 0) yazılır. `0/0` tanımsızdır ve
    `∞` DEĞİL `Veri yok` gösterilir — aksi hâlde hiç kâr etmemiş bir bot "sonsuz iyi" görünürdü.
    """
    if pf_state == PF_POSITIVE_INFINITY:
        return "∞"
    return NO_DATA if pf is None else f"{pf:.2f}"


def summary_cards(pv: PortfolioView, summary: dict | None = None) -> list[SummaryCard]:
    """Genel kâr/zarar + risk özeti. Hesaplanamayan alan sessizce `0` DEĞİL, `Veri yok` olur."""
    s = summary or {}

    def pct1(x, nd=1):
        return NO_DATA if x is None else f"%{float(x):.{nd}f}"

    _pf, _pf_state = profit_factor_state(pv)
    # Bozuk kayıt varsa toplam kartı `canonical_summary` ile AYNI kuralla «Veri yok» olur —
    # dışlanmış kayıtlarla hesaplanmış sayı (sahte `$0.00` / eksik toplam) GÖSTERİLMEZ.
    bad = money_totals_unavailable_reason(pv)

    def money(x, *blocked_by: str):
        """(value, display, ek-altyazı) — `blocked_by` anahtarlarından biri `bad`ta ise Veri yok."""
        reasons = [bad[k] for k in blocked_by if k in bad]
        if reasons:
            return None, NO_DATA, " · ⚠ " + " · ".join(reasons)
        return finite_float_or_none(x), fmt_money(x), ""

    rt_v, rt_d, rt_n = money(pv.realized_today, "realized")
    ra_v, ra_d, ra_n = money(pv.realized_total, "realized")
    op_v, op_d, op_n = money(pv.open_net_unrealized, "unrealized")
    tn_v, tn_d, tn_n = money(pv.total_net, "realized", "unrealized")
    cards = [
        SummaryCard("today_realized_net_usdt", "Bugün gerçekleşen net K/Z", rt_v, rt_d, "money",
                    "kapanan işlemler (UTC gün)" + rt_n),
        SummaryCard("all_time_realized_net_usdt", "Toplam gerçekleşen net K/Z", ra_v, ra_d, "money",
                    "ücret + funding dahil" + ra_n),
        SummaryCard("open_net_usdt", "Açık pozisyon net K/Z", op_v, op_d, "money",
                    "tahmini kapanış ücreti düşülmüş" + (" · ⚠ fiyat yok" if pv.any_stale_price else "") + op_n),
        SummaryCard("total_net_usdt", "Toplam net K/Z", tn_v, tn_d, "money", "gerçekleşen + açık" + tn_n),
        SummaryCard("win_loss", "Kazanan / Kaybeden", None, f"{pv.wins} / {pv.losses}", "pair",
                    f"başa baş {pv.breakeven} · kapanmış {pv.closed_trades}"),
        SummaryCard("win_rate_pct", "Kazanma oranı", finite_float_or_none(pv.win_rate), pct1(finite_float_or_none(pv.win_rate)), "pct",
                    "kazanan / (kazanan + kaybeden) — başa baş HARİÇ"),
        SummaryCard("profit_factor", "Profit factor", _pf, _pf_display(_pf_state, _pf),
                    "ratio", "brüt kâr / brüt zarar — oran, para birimi değil"),
        SummaryCard("max_drawdown_pct", "Maks. drawdown", finite_float_or_none(pv.max_drawdown),
                    pct1(finite_float_or_none(pv.max_drawdown), 2), "pct", "risk motoru (risk.json)"),
    ]
    if summary is not None:
        mu, sr = s.get("margin_utilization_pct"), s.get("open_stop_risk_usdt")
        partial = stop_risk_note(s)
        stale_note = risk_stale_note(s)
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
                        "money_plain",
                        "risk.json → total_open_risk_usdt (stop riskinden AYRI kavram)" + stale_note),
            SummaryCard("open_risk_budget_utilization_pct", "Risk bütçesi kullanımı",
                        s.get("open_risk_budget_utilization_pct"),
                        pct1(s.get("open_risk_budget_utilization_pct")), "pct",
                        risk_budget_sub(s) + stale_note),
            # --- ÜÇ AYRI KAVRAM: spot notional ile futures stop riski AYNI KARTTA TOPLANMAZ ---
            SummaryCard("futures_stop_risk_usdt", "Futures stop riski", s.get("futures_stop_risk_usdt"),
                        _usdt_or_no_data(s.get("futures_stop_risk_usdt")), "money_plain",
                        "yalnız futures pozisyonları — kabul kapısının kovası" + stale_note),
            SummaryCard("futures_risk_budget_utilization_pct", "Futures bütçe kullanımı",
                        s.get("futures_risk_budget_utilization_pct"),
                        pct1(s.get("futures_risk_budget_utilization_pct")), "pct",
                        "futures stop riski / azami risk bütçesi" + stale_note),
            SummaryCard("spot_exposure_usdt", "Spot maruziyeti", s.get("spot_exposure_usdt"),
                        _usdt_or_no_data(s.get("spot_exposure_usdt")), "money_plain",
                        "açık spot notional — RİSK DEĞİL" + spot_stop_note(s) + stale_note),
            SummaryCard("spot_allocation_utilization_pct", "Spot allocation kullanımı",
                        s.get("spot_allocation_utilization_pct"),
                        pct1(s.get("spot_allocation_utilization_pct")), "pct",
                        _spot_cap_sub(s) + stale_note),
        ]
    return cards


def _usdt_or_no_data(v: Any) -> str:
    """Sonlu sayı → «x USDT»; None/NaN → «Veri yok». Sessiz 0 ÜRETİLMEZ."""
    f = finite_float_or_none(v)
    return NO_DATA if f is None else fmt_money(f, signed=False, currency="") + " USDT"


def spot_stop_note(s: dict) -> str:
    """Stopsuz spot pozisyonlar AÇIKÇA yazılır — 'riski azaldı' izlenimi verilmez."""
    syms = list(s.get("spot_symbols_without_stop") or [])
    if not syms:
        return ""
    return " · stopsuz (stopla sınırlanmamış): " + ", ".join(str(x) for x in syms[:4])


def _spot_cap_sub(s: dict) -> str:
    cap = finite_float_or_none(s.get("spot_allocation_max_usdt"))
    if cap is None:
        return s.get("unavailable_reason", {}).get("spot_allocation_utilization_pct", NO_DATA)
    return "azami %s USDT — spot notional tavanı (stop riskinden AYRI kapı)" % fmt_money(cap, signed=False, currency="")


def risk_budget_sub(s: dict) -> str:
    """Risk bütçesi kartının altyazısı — TABAN AÇIKÇA YAZILIR.

    Operatör `%20.3` ile `%21.5` arasındaki farkın nereden geldiğini kartta görebilmelidir:
    motor `starting_equity` tabanını kullanırken panel canlı equity'yi gösterirse aynı büyüklük
    iki farklı sayı olur. Taban bilinmiyorsa oran zaten `Veri yok`tur.
    """
    if s.get("risk_budget_max_usdt") is None:
        return s.get("unavailable_reason", {}).get("risk_budget_max_usdt", NO_DATA)
    label = {"starting_equity": "Başlangıç özkaynağı tabanı",
             "live_equity": "Canlı özkaynak tabanı"}.get(str(s.get("risk_equity_basis_kind") or ""),
                                                         "Özkaynak tabanı")
    out = "azami %s USDT" % fmt_money(s["risk_budget_max_usdt"], signed=False, currency="")
    basis = s.get("risk_equity_basis_usdt")
    if basis is not None:
        out += " · %s: %s USDT" % (label, fmt_money(basis, signed=False, currency=""))
    return out


def risk_stale_note(s: dict) -> str:
    """Risk anlık görüntüsü bayatsa/zamanı bilinmiyorsa kartın altyazısına eklenen uyarı.

    Değer GİZLENMEZ (operatör son bilinen rezervasyonu görmeye devam eder) fakat «bayat» etiketi
    ZORUNLUDUR — bayat risk verisi taze gibi sunulamaz.
    """
    st = s.get("risk_snapshot_state")
    if st == LIVE_STALE:
        return " · ⚠ Risk verisi güncel değil (%s)" % _age_text(s.get("risk_snapshot_age_s"))
    if st == LIVE_UNKNOWN:
        return " · ⚠ Risk verisi yaşı bilinmiyor"
    return " · risk verisi %s önce" % _age_text(s.get("risk_snapshot_age_s"))


def stop_risk_note(s: dict) -> str:
    """Stop riski toplamına giremeyen pozisyonları AYRI AYRI etiketler (eksik/bozuk/geçersiz)."""
    parts = []
    for key, label in (("positions_without_stop", "stop'suz"),
                       ("positions_stop_malformed", "stop değeri bozuk"),
                       ("positions_invalid_qty", "miktar/giriş geçersiz")):
        n = int(s.get(key) or 0)
        if n:
            parts.append(f"{n} {label}")
    return (" · ⚠ toplama girmeyen: " + ", ".join(parts)) if parts else ""


def _age_text(sec: Any) -> str:
    if sec is None:
        return "bilinmiyor"
    sec = int(sec)
    if sec < 90:
        return f"{sec}sn"
    if sec < 5400:
        return f"{sec // 60}dk"
    if sec < 172800:
        return f"{sec // 3600}sa"
    return f"{sec // 86400}g"




def build(state_positions: list[dict], trades: list[dict], chief: dict | None, *,
          marks: dict[str, Any] | None = None, fees: Any = None, today: str | None = None,
          max_drawdown_pct: Any = None, freshness: Freshness | None = None,
          futures_equity: Any = None, spot_equity: Any = None, risk_state: dict | None = None,
          as_of: str | None = None, risk_age_s: Any = None, risk_stale_s: Any = None) -> dict:
    """Panelin TEK giriş noktası: her bölüm için hazır, tutarlılığı denetlenmiş model.

    `summary` KANONİK özettir; HTML sayfası da `/api/live/summary` de AYNI sözlüğü kullanır,
    böylece iki yüzey farklı sayı gösteremez.
    """
    pv = portfolio_view(state_positions, trades, marks=marks, fees=fees, today=today,
                        max_drawdown_pct=max_drawdown_pct)
    fr = freshness.to_dict() if freshness else None
    summary = canonical_summary(pv, futures_equity=futures_equity, spot_equity=spot_equity,
                                risk_state=risk_state, as_of=as_of, source_freshness=fr,
                                risk_age_s=risk_age_s, risk_stale_s=risk_stale_s)
    rows = [position_row(v) for v in pv.positions]
    issues = check_invariants(pv, table_rows=len(rows))
    return {"portfolio": pv, "chief": chief_view(chief, pv, summary), "rows": rows,
            "columns": POSITION_COLUMNS, "cards": summary_cards(pv, summary), "summary": summary,
            "inconsistencies": [i.__dict__ for i in issues], "freshness": fr}


__all__ = ["ChiefView", "Freshness", "LIVE_OK", "LIVE_STALE", "LIVE_UNKNOWN", "NO_DATA",
           "POSITION_COLUMNS", "POSITION_NUM_COLS", "POSITION_PNL_COLS", "SummaryCard", "build",
           "chief_view", "position_row", "profit_factor_value", "risk_budget_sub",
           "risk_stale_note", "stop_risk_note", "summary_cards"]
