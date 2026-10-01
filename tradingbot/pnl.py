"""KANONİK KÂR/ZARAR VE GÖRÜNÜM KATMANI — panel ve Telegram TEK bu modülü kullanır.

Neden tek katman: panel `_positions_table()` içinde, Telegram ise mesaj kurucusunda ayrı formül
kullansaydı aynı işlem iki farklı sayı gösterirdi. Buradaki `position_view()` / `portfolio_view()`
çıktıları her iki tüketicinin de tek kaynağıdır (`tests/test_pnl_views.py` eşitliği zorlar).

Hesap Decimal ile yapılır; yuvarlama YALNIZ sunum katmanındadır (`fmt_money`/`fmt_pct`). Ücret ve
funding ASLA iki kez düşülmez:

* ``gross_unrealized``  = qty × (mark − entry) × yön   (ücret/funding YOK)
* ``net_unrealized``    = gross − giriş ücreti − tahmini kapanış ücreti + net funding
* ``realized_net``      = kapanmış işlemin `net_pnl` alanı (ÜCRET VE FUNDING ZATEN İÇİNDE)

GERÇEK LEDGER SÖZLEŞMESİ (`accounting/futures_ledger.py`):
`_finalize()` → ``net = gross − (entry_fee + exit_fee) + funding_net`` ve bunu `TradeRecord`'a hem
`pnl` hem `net_pnl` olarak yazar; `to_dict()` DAİMA `net_pnl` içerir → `realized_net()` kanonik dalı
kullanır, ücret/funding ASLA iki kez sayılmaz. `Position.realized_pnl` (`_close_part()`:
``gross − exit_fee``) YALNIZ çıkış ücretini içerir ve kapanış KAYITLARINDA bulunmaz.

Yüzde paydası (UI'da açıkça yazılır):
* FUTURES → kullanılan başlangıç teminatı (`initial_margin`)
* SPOT    → yatırılan maliyet (`notional`)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")
_HUNDRED = Decimal("100")
DEFAULT_TAKER_PCT = Decimal("0.05")          # %0.05 — FeeSchedule.taker_pct varsayılanı

PCT_BASIS_MARGIN = "initial_margin"          # futures
PCT_BASIS_COST = "cost_basis"                # spot

# --- stop riski hesaplanabilirlik durumları (UI ve sayaçlar bunları AYRI raporlar) ---
STOP_RISK_OK = "ok"                          # hesaplandı (ters stop dâhil — o durumda 0)
STOP_RISK_NO_STOP = "no_stop"                # stop alanı YOK/boş
STOP_RISK_MALFORMED = "malformed_stop"       # stop alanı var ama sayıya çevrilemiyor
STOP_RISK_INVALID_QTY = "invalid_qty"        # qty <= 0 / ayrıştırılamıyor
STOP_RISK_INVALID_ENTRY = "invalid_entry"    # entry <= 0 / ayrıştırılamıyor

# --- tazelik durumları — TEK KAYNAK; `dashboard.views` bu adları yeniden dışa verir ---
FRESH_OK = "live"
FRESH_STALE = "stale"
FRESH_UNKNOWN = "unknown"

# --- profit factor durumları: sayısal alan ASLA inf/NaN taşımaz, durum bu alanda taşınır ---
PF_FINITE = "finite"                         # brüt zarar > 0 → sonlu oran
PF_POSITIVE_INFINITY = "positive_infinity"   # brüt zarar 0, brüt kâr > 0 → matematiksel ∞
PF_UNDEFINED = "undefined"                   # brüt zarar 0 VE brüt kâr 0 → 0/0 tanımsız
PF_NO_CLOSED_TRADES = "no_closed_trades"     # hiç kapanmış işlem yok


def _parse_dec(x: Any) -> Decimal | None:
    """Ham girdi → Decimal; ayrıştırılamıyorsa None. SONLULUK KONTROLÜ YAPMAZ (çağıran yapar)."""
    if x is None or x == "" or isinstance(x, bool):
        return None
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None


def dec(x: Any, default: Decimal = ZERO) -> Decimal:
    """Her türlü girdiyi güvenle SONLU Decimal'e çevir (None/boş/bozuk/NaN/±Infinity → default).

    GİRİŞ SINIRI: `Decimal("Infinity")` aritmetiğe girerse `Inf × 0` / `Inf − Inf` varsayılan
    bağlamda `InvalidOperation` fırlatır ve `position_view()`/`portfolio_view()` düşer → TÜM
    sayfalar 500 olur. Bu yüzden sonlu olmayan her değer NaN ile AYNI muameleyi görür. Eskiden
    `isinstance(x, Decimal)` kısa devresi NaN/Inf Decimal nesnelerini de geçiriyordu.
    """
    d = _parse_dec(x)
    return d if (d is not None and d.is_finite()) else default


def _non_finite(x: Any) -> bool:
    """Girdi ayrıştırılıyor AMA NaN/sNaN/±Infinity ise True — bozuk kayıt tespiti.

    `dec()` böyle bir değeri sessizce `default`a çevirir; toplamların «bilinmiyor» olarak
    işaretlenebilmesi için bozukluk `dec()` ÇAĞRILMADAN ÖNCE burada yakalanır.
    """
    d = _parse_dec(x)
    return d is not None and not d.is_finite()


def dec_or_none(x: Any) -> Decimal | None:
    """Ayrıştırılabiliyorsa SONLU Decimal, aksi hâlde ``None`` — sessiz `0` ÜRETMEZ.

    `dec()` bozuk girdide `0` döner; bu bir FİYAT alanı için tehlikelidir: `stop="abc"` → `0`
    olunca `qty × (entry − 0)` tam notional kadar sahte "risk" üretir. Fiyat/eşik alanları bu
    yardımcıyı kullanır; "veri yok" ile "gerçekten sıfır" birbirine karışmaz.
    NaN, sNaN, Infinity ve -Infinity de `None`dur: `stop="-Infinity"` «geçerli stop» DEĞİLDİR.
    """
    d = _parse_dec(x)
    return d if (d is not None and d.is_finite()) else None


def finite_float_or_none(x: Any) -> float | None:
    """KANONİK JSON SINIRI: sonlu float ya da `None` — ASLA `inf`/`nan` döndürmez.

    Panel (`views`), kanonik özet (`canonical_summary`) ve API payload'ına giden HER sayısal
    alan bu tek yardımcıdan geçer. Daha önce `views._num()` yalnız NaN'ı eliyordu; `Infinity`
    kart `value`sundan JSON'a sızıyor ve Starlette (`allow_nan=False`) HTTP 500 veriyordu.
    Çok büyük Decimal'in `float()`ta `inf`e taşması da yakalanır.
    """
    d = _parse_dec(x)
    if d is None or not d.is_finite():
        return None
    f = float(d)
    return f if math.isfinite(f) else None


# ============================================================================ sunum yardımcıları
def fmt_money(x: Any, *, signed: bool = True, currency: str = "$") -> str:
    """Para gösterimi. Küçük değerler `+0.00`'a YUVARLANMAZ.

    |x| < 0.01 ve x != 0 → 6 anlamlı ondalık (ör. `+$0.004213`), böylece gerçek küçük kâr/zarar
    kullanıcıya sıfır gibi görünmez. Hesap hassasiyeti bu fonksiyondan bağımsızdır.
    """
    # None VEYA sonlu olmayan girdi (NaN/±Infinity) → "—"; sahte `$0.00`/`+$Infinity` YOK.
    v = dec(x, Decimal("NaN") if (x is None or _non_finite(x)) else ZERO)
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
    # None VEYA sonlu olmayan girdi (NaN/±Infinity) → "—"; sahte `$0.00`/`+$Infinity` YOK.
    v = dec(x, Decimal("NaN") if (x is None or _non_finite(x)) else ZERO)
    if v != v:
        return "—"
    if v == 0:
        return "0.00%"
    return f"{v:+,.{nd}f}%"


def fmt_qty(x: Any, nd: int = 8) -> str:
    """Coin/kontrat ADEDİ — USDT DEĞİL. Gereksiz sıfırlar kırpılır."""
    # None VEYA sonlu olmayan girdi (NaN/±Infinity) → "—"; sahte `$0.00`/`+$Infinity` YOK.
    v = dec(x, Decimal("NaN") if (x is None or _non_finite(x)) else ZERO)
    if v != v:
        return "—"
    s = f"{v:,.{nd}f}".rstrip("0").rstrip(".")
    return s or "0"


def pnl_class(x: Any) -> str:
    """CSS sınıfı — renk TEK BAŞINA anlam taşımaz, `+/-` işareti her zaman yazılır."""
    v = dec(x)                                   # NaN/±Inf → 0 → "flat" (karşılaştırma güvenli)
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
    stop_risk: Decimal | None = None  # stop'a kadar BRÜT tahmini kayıp (ücret HARİÇ); stop yoksa None
    stop_risk_status: str = STOP_RISK_OK   # neden hesaplanamadı — `STOP_RISK_*` (UI ayrı ayrı etiketler)

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

    # Stop'a kadar BRÜT tahmini kayıp — yön duyarlı, negatife düşmez.
    #   LONG  : qty × (entry − stop)      SHORT : qty × (stop − entry)
    # Ücret HARİÇ: `risk/engine.py` rezervasyonu da brüt hesaplar (bkz. risk.json → positions[].risk_usdt),
    # iki büyüklüğün karşılaştırılabilir kalması için aynı sözleşme kullanılır.
    #
    # BOZUK STOP ≠ SIFIR STOP: `dec()` varsayılanı kullanılsaydı `stop="abc"` → `0` olur ve LONG'da
    # `qty × (entry − 0)` = TAM NOTIONAL kadar sahte risk üretirdi. Eksik / bozuk / geçersiz miktar
    # durumları AYRI etiketlenir ve toplama HİÇ katılmaz.
    raw_stop = pos.get("stop")
    stop_d = dec_or_none(raw_stop)
    stop_risk, stop_status = None, STOP_RISK_OK
    if raw_stop in (None, ""):
        stop_status = STOP_RISK_NO_STOP
    elif stop_d is None:
        stop_status = STOP_RISK_MALFORMED
    elif qty <= 0:
        stop_status = STOP_RISK_INVALID_QTY
    elif entry <= 0:
        stop_status = STOP_RISK_INVALID_ENTRY
    else:
        raw = (entry - stop_d) if side == "LONG" else (stop_d - entry)
        stop_risk = qty * raw if raw > 0 else ZERO      # ters stop → 0 (negatif risk YOK)

    return PositionView(
        trade_id=str(pos.get("id") or pos.get("trade_id") or ""),
        symbol=str(pos.get("symbol") or ""), market=mkt, side=side, qty=qty, entry_price=entry,
        mark_price=None if stale else mark, leverage=lev, notional=notional, initial_margin=margin,
        stop=stop_d,                                   # bozuk stop → None (sahte `0` GÖSTERİLMEZ)
        take_profit=tp,
        liquidation_price=dec_or_none(pos.get("liquidation_price")),
        entry_fee=entry_fee, exit_fee_est=exit_fee_est, funding_net=funding_net,
        gross_unrealized=gross, net_unrealized=net, net_unrealized_pct=pct,
        pct_basis=PCT_BASIS_MARGIN if mkt == "FUTURES" else PCT_BASIS_COST,
        opened_at=str(pos.get("opened_at") or ""), price_is_stale=stale,
        meta={"leverage_reasons": (pos.get("meta") or {}).get("leverage_reasons")} if isinstance(pos.get("meta"), dict) else {},
        stop_risk=stop_risk, stop_risk_status=stop_status)


# ============================================================================ kapanmış işlem
def realized_net(trade: dict) -> Decimal:
    """Kapanmış işlemin NET gerçekleşen K/Z'si — ücret ve funding ÇİFT SAYILMAZ.

    KANONİK DAL: kayıtta `net_pnl` varsa doğrudan o döner. `FuturesLedgerV2._finalize()` ve
    `SpotLedger` kapanış kayıtlarının ikisi de `net_pnl = gross − entry_fee − exit_fee ± funding`
    yazar; `TradeRecord.to_dict()` bu alanı HER ZAMAN içerir → üretimde daima bu dal çalışır.

    GERİ DÖNÜŞ DALI: yalnız `net_pnl` taşımayan eski/dış kayıtlar için. Orada `realized_pnl`
    (ya da `pnl`) ücret tarafını zaten içerdiği varsayılır ve YALNIZ net funding eklenir; ücret
    ikinci kez düşülmez. Not: `Position.realized_pnl` (`_close_part`: `gross − exit_fee`) yalnız
    ÇIKIŞ ücretini içerir, giriş ücretini içermez — fakat o alan kapanış KAYITLARINDA bulunmaz.
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
    # Stop riski HESAPLANABİLEN pozisyonların stop'a kadar BRÜT tahmini kaybı (ücret hariç).
    # Aşağıdaki sayaçlardan herhangi biri > 0 iken bu toplam EKSİKTİR ve UI'da öyle etiketlenir.
    open_stop_risk: Decimal = ZERO
    positions_without_stop: int = 0        # stop alanı YOK/boş
    positions_stop_malformed: int = 0      # stop alanı var ama sayıya çevrilemiyor
    positions_invalid_qty: int = 0         # qty veya entry <= 0 / ayrıştırılamıyor
    # Parasal alanı NaN/±Infinity olan KAPANIŞ kayıtları: toplamlara GİRMEZ ve sessizce `0`
    # sayılmaz; bu sayaç > 0 iken gerçekleşen/toplam net K/Z «bilinmiyor» olarak raporlanır.
    trades_non_finite: int = 0

    @property
    def closed_trades(self) -> int:
        """Kapanmış işlem sayısı = kazanan + kaybeden + başa baş."""
        return self.wins + self.losses + self.breakeven

    @property
    def stop_risk_incomplete(self) -> int:
        """Stop riski toplamına GİREMEYEN pozisyon sayısı (eksik + bozuk + geçersiz)."""
        return self.positions_without_stop + self.positions_stop_malformed + self.positions_invalid_qty

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


# Kapanış kaydında toplamlara giren parasal alanlar — herhangi biri sonlu değilse kayıt bozuktur.
_TRADE_MONEY_KEYS = ("net_pnl", "realized_pnl", "pnl", "fees", "fees_paid",
                     "funding_received", "funding_paid", "funding_net", "funding")


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
    non_finite_trades = 0
    for t in trades:
        # Bozuk kayıt (NaN/±Infinity): `dec()` bunu `0`a çevirirdi ve işlem «başa baş» gibi
        # toplama girerdi → sahte `$0.00`. Kayıt DIŞLANIR ve sayılır; özet «bilinmiyor» yazar.
        if any(_non_finite(t.get(k)) for k in _TRADE_MONEY_KEYS):
            non_finite_trades += 1
            continue
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
        any_stale_price=any(v.price_is_stale for v in views),
        open_stop_risk=sum((v.stop_risk for v in views if v.stop_risk is not None), ZERO),
        positions_without_stop=sum(1 for v in views if v.stop_risk_status == STOP_RISK_NO_STOP),
        positions_stop_malformed=sum(1 for v in views if v.stop_risk_status == STOP_RISK_MALFORMED),
        positions_invalid_qty=sum(1 for v in views
                                  if v.stop_risk_status in (STOP_RISK_INVALID_QTY, STOP_RISK_INVALID_ENTRY)),
        trades_non_finite=non_finite_trades)


# ============================================================================ kanonik özet
# Modül içi kısa ad — TEK uygulama `finite_float_or_none`tır; `views` de aynısını kullanır.
# (İki ayrı kopya — `pnl._f` ve `views._num` — zamanla ayrıştı: biri Infinity'yi eliyor,
# diğeri elemiyordu. Artık ikinci kopya YOK.)
_f = finite_float_or_none


def money_totals_unavailable_reason(pv: PortfolioView) -> dict[str, str]:
    """Toplam K/Z alanlarının hangi sebeple «bilinmiyor» olduğu — kart ve özet AYNI kuralı okur.

    Boş sözlük → bütün toplamlar hesaplanabilir. Aksi hâlde anahtar → neden:
      * `realized`   → bozuk kapanış kaydı (NaN/±Inf) var; gerçekleşen toplam bilinemez
      * `unrealized` → miktar/giriş fiyatı geçersiz açık pozisyon var; açık toplam bilinemez
    """
    why: dict[str, str] = {}
    if pv.trades_non_finite > 0:
        why["realized"] = (f"{pv.trades_non_finite} kapanış kaydında sonlu olmayan tutar "
                           "(bozuk ledger) — toplam hesaplanamaz")
    if pv.positions_invalid_qty > 0:
        why["unrealized"] = (f"{pv.positions_invalid_qty} açık pozisyonda miktar/giriş geçersiz "
                             "— açık K/Z toplamı hesaplanamaz")
    return why


def profit_factor_state(pv: PortfolioView) -> tuple[float | None, str]:
    """KANONİK profit factor sözleşmesi — `(sayısal, durum)`.

    Sayısal alan ASLA `inf`/`NaN` taşımaz; sonsuzluk `PF_POSITIVE_INFINITY` durumuyla bildirilir.
    Panel HTML'i, `/api/live/summary` ve Telegram bu TEK yardımcıyı kullanır; ikinci bir kopya
    karar üretmez (iki kopya farklı sonuç verirse aynı işlem iki farklı oran gösterirdi).

        brüt zarar > 0             → (kâr/zarar, `finite`)
        brüt zarar 0, brüt kâr > 0 → (None, `positive_infinity`)   → UI `∞`
        brüt zarar 0, brüt kâr 0   → (None, `undefined`)           → UI `Veri yok` (`∞` DEĞİL)
        kapanmış işlem yok         → (None, `no_closed_trades`)    → UI `Veri yok`
    """
    if pv.closed_trades == 0:
        return None, PF_NO_CLOSED_TRADES
    if pv.profit_factor is not None:                 # brüt zarar > 0 → sonlu
        return _f(pv.profit_factor), PF_FINITE
    # `profit_factor is None` ⇔ brüt zarar == 0. Kâr var mı? `wins` > 0 ⇔ brüt kâr > 0.
    return None, (PF_POSITIVE_INFINITY if pv.wins > 0 else PF_UNDEFINED)


def canonical_summary(pv: PortfolioView, *, futures_equity: Any = None, spot_equity: Any = None,
                      risk_state: dict | None = None, as_of: str | None = None,
                      source_freshness: dict | None = None, risk_age_s: Any = None,
                      risk_stale_s: Any = None,
                      futures_ledger_doc: dict | None = None,
                      spot_ledger_doc: dict | None = None) -> dict:
    """Panel HTML'i ve `/api/live/summary` için TEK kanonik portföy özeti.

    Sözleşme: alan HESAPLANAMIYORSA `None` döner ve `unavailable_reason` nedeni yazar.
    Sessizce `0` ÜRETİLMEZ — gerçek sıfır ile "veri yok" birbirine karıştırılamaz.

    KAVRAM AYRIMI (aynı kartta karıştırılmaz):
      * `open_stop_risk_usdt`      — açık pozisyonların STOP seviyesine kadar brüt tahmini kaybı
                                     (bu katmanda defterden hesaplanır)
      * `risk_engine_reserved_usdt`— RİSK MOTORUNUN rezerve ettiği toplam açık risk
                                     (`risk.json → exposure.total_open_risk_usdt`)
      * `risk_budget_max_usdt`     — azami toplam risk bütçesi; YALNIZ risk motorunun yayımladığı
                                     `exposure.max_total_open_risk_usdt` okunur (tahmin YOK)
      * `open_risk_budget_utilization_pct` — rezervasyon / bütçe
      * `futures_stop_risk_usdt`   — YALNIZ futures pozisyonlarının stop riski (kabul kapısının kovası)
      * `spot_exposure_usdt`       — açık spot notional maruziyeti (RİSK DEĞİL, stopla sınırlı değil)
      * `spot_stop_risk_usdt`      — YALNIZ gerçek duran stop emri olan spot pozisyonların riski;
                                     stopsuz spot `spot_unbounded_notional_usdt` altında AYRI durur
    Bu üç büyüklük aynı kartta TOPLANMAZ; eski snapshot'larda alan yoksa `None` + neden yazılır.

    `risk_age_s` / `risk_stale_s`: risk anlık görüntüsünün yaşı ve eşiği. Fiyat tazeliğinden AYRI
    kavramdır; `risk.json` strateji turunda yazılır, fiyat her tickte güncellenir.
    """
    rs = (risk_state or {}).get("exposure") if isinstance((risk_state or {}).get("exposure"), dict) else (risk_state or {})
    why: dict[str, str] = {}

    fe, se = _f(futures_equity), _f(spot_equity)
    if fe is None:
        why["futures_equity_usdt"] = "futures_ledger.json içinde equity/wallet_balance yok"
    if se is None:
        why["spot_equity_usdt"] = "portfolio.json okunamadı"

    margin = _f(pv.open_margin)
    margin_util = None
    if fe is None:
        why["margin_utilization_pct"] = "futures özkaynak bilinmiyor"
    elif fe <= 0:
        why["margin_utilization_pct"] = "futures özkaynak sıfır/negatif — oran tanımsız"
    elif margin is not None:
        margin_util = margin / fe * 100.0

    reserved = _f(rs.get("total_open_risk_usdt")) if isinstance(rs, dict) else None
    if reserved is None:
        why["risk_engine_reserved_usdt"] = "risk.json → exposure.total_open_risk_usdt yok"

    # AZAMİ RİSK BÜTÇESİ — panel KENDİ tabanını SEÇMEZ.
    # Risk motoru kabul kararını `equity_basis × max_total_open_risk_pct` ile verir ve
    # `size_on_live_equity=False` (PAPER_RESEARCH) iken taban `starting_equity`'dir, canlı equity
    # DEĞİL. Panel daha önce `exposure.equity` kullanıyordu → gösterilen oran motorun gerçekten
    # uyguladığı orandan farklıydı. Artık YALNIZ motorun yayımladığı değer okunur; alan yoksa
    # başka bir equity alanından TAHMİN ÜRETİLMEZ.
    budget_max = _f(rs.get("max_total_open_risk_usdt")) if isinstance(rs, dict) else None
    basis = _f(rs.get("equity_basis")) if isinstance(rs, dict) else None
    basis_kind = rs.get("equity_basis_kind") if isinstance(rs, dict) else None
    # --- UC AYRI KAVRAM (TOPLANMAZ): futures stop riski / spot maruziyeti / stopsuz spot ---
    # Eski snapshot'larda bu alanlar YOKTUR -> `None` + `unavailable_reason` (FAIL-SAFE "Veri yok").
    # Baska bir alandan TAHMIN URETILMEZ; ozellikle `total_open_risk_usdt` bu uc kavramin yerine
    # GECMEZ (birlesik toplamdir ve stopsuz spotu tam notional sayar).
    fut_risk = _f(rs.get("futures_stop_risk_usdt")) if isinstance(rs, dict) else None
    spot_exp = _f(rs.get("spot_exposure_usdt")) if isinstance(rs, dict) else None
    spot_unknown = bool(rs.get("spot_exposure_unknown")) if isinstance(rs, dict) else False
    spot_bad_price = list(rs.get("spot_symbols_unknown_price") or []) if isinstance(rs, dict) else []
    if spot_unknown:
        spot_exp = None                       # geçersiz fiyat → sessiz 0 ÜRETİLMEZ
    spot_risk = _f(rs.get("spot_stop_risk_usdt")) if isinstance(rs, dict) else None
    spot_unbounded = _f(rs.get("spot_unbounded_notional_usdt")) if isinstance(rs, dict) else None
    spot_no_stop = list(rs.get("spot_symbols_without_stop") or []) if isinstance(rs, dict) else []
    spot_cap = _f(rs.get("max_spot_allocation_usdt")) if isinstance(rs, dict) else None
    _fut_contrib = _f((futures_ledger_doc or {}).get("starting_equity"))
    _spot_contrib = _f((spot_ledger_doc or {}).get("starting_equity"))
    if _fut_contrib is None:
        why["futures_contributed_capital_usdt"] = "futures defteri okunamadı — katkı tabanı bilinmiyor"
    if _spot_contrib is None:
        why["spot_contributed_capital_usdt"] = "spot defteri okunamadı — katkı tabanı bilinmiyor"
    _old_snap = "risk.json eski şema — motor bu alanı henüz yayımlamıyor"
    if fut_risk is None:
        why["futures_stop_risk_usdt"] = "risk.json → exposure.futures_stop_risk_usdt yok (" + _old_snap + ")"
    if spot_exp is None:
        why["spot_exposure_usdt"] = (
            ("spot fiyatı geçersiz (" + ", ".join(str(x) for x in spot_bad_price[:3])
             + ") — maruziyet ölçülemedi, yeni spot giriş fail-closed reddedilir")
            if spot_unknown else "risk.json → exposure.spot_exposure_usdt yok (" + _old_snap + ")")
    if spot_risk is None:
        why["spot_stop_risk_usdt"] = "risk.json → exposure.spot_stop_risk_usdt yok (" + _old_snap + ")"
    if spot_cap is None:
        why["spot_allocation_utilization_pct"] = "spot allocation tavanı yayımlanmamış"

    budget_util = None
    if budget_max is None:
        why["risk_budget_max_usdt"] = ("risk.json → exposure.max_total_open_risk_usdt yok "
                                       "(eski snapshot) — equity tabanı bilinmediği için tahmin üretilmez")
        why["open_risk_budget_utilization_pct"] = "azami risk bütçesi bilinmiyor"
    elif budget_max > 0 and reserved is not None:
        budget_util = reserved / budget_max * 100.0

    # FUTURES butce kullanimi: kabul kapisinin GERCEKTEN olctugu kova (spot maruziyeti HARIC).
    futures_budget_util = None
    if budget_max is None or fut_risk is None:
        why["futures_risk_budget_utilization_pct"] = ("futures stop riski ya da azami bütçe "
                                                      "bilinmiyor — tahmin üretilmez")
    elif budget_max > 0:
        futures_budget_util = fut_risk / budget_max * 100.0
    spot_alloc_util = None
    if spot_cap is not None and spot_cap > 0 and spot_exp is not None:
        spot_alloc_util = spot_exp / spot_cap * 100.0

    # Risk anlık görüntüsünün YAŞI — fiyat tazeliğinden AYRI kavramdır (ayrı eşik, ayrı etiket).
    r_age = _f(risk_age_s)
    r_stale_lim = _f(risk_stale_s)
    if r_age is None:
        risk_state_label = FRESH_UNKNOWN
        why["risk_snapshot_age_s"] = "risk.json → generated_at yok/çözülemedi — veri yaşı bilinmiyor"
    elif r_stale_lim is not None and r_age > r_stale_lim:
        risk_state_label = FRESH_STALE
    else:
        risk_state_label = FRESH_OK

    dd = _f(pv.max_drawdown)
    if dd is None:
        why["max_drawdown_pct"] = "risk.json içinde drawdown_pct yok"
    if pv.win_rate is None:
        why["win_rate_pct"] = "karara bağlanan (kazanan+kaybeden) işlem yok"
    pf, pf_state = profit_factor_state(pv)
    if pf is None:
        why["profit_factor"] = {
            PF_NO_CLOSED_TRADES: "kapanmış işlem yok",
            PF_POSITIVE_INFINITY: "brüt zarar 0, brüt kâr > 0 → oran matematiksel olarak sonsuz",
            PF_UNDEFINED: "brüt kâr ve brüt zarar 0 → oran tanımsız",
        }.get(pf_state, "hesaplanamadı")

    # Bozuk kayıt varsa toplam «bilinmiyor»dur — dışlanmış kayıtlarla hesaplanan sayı GÖSTERİLMEZ.
    bad = money_totals_unavailable_reason(pv)
    realized_ok, unreal_ok = "realized" not in bad, "unrealized" not in bad
    if not realized_ok:
        why["today_realized_net_usdt"] = why["all_time_realized_net_usdt"] = bad["realized"]
    if not unreal_ok:
        why["open_net_usdt"] = bad["unrealized"]
    if bad:
        why["total_net_usdt"] = " · ".join(bad.values())

    return {
        "today_realized_net_usdt": _f(pv.realized_today) if realized_ok else None,
        "all_time_realized_net_usdt": _f(pv.realized_total) if realized_ok else None,
        "open_net_usdt": _f(pv.open_net_unrealized) if unreal_ok else None,
        "total_net_usdt": _f(pv.total_net) if not bad else None,
        "trades_non_finite": pv.trades_non_finite,
        "winning_trades": pv.wins, "losing_trades": pv.losses, "breakeven_trades": pv.breakeven,
        "closed_trades": pv.closed_trades,
        "win_rate_pct": _f(pv.win_rate),
        # `profit_factor` DAİMA sonlu sayı ya da `null`; sonsuzluk `profit_factor_state`'te taşınır.
        "profit_factor": pf, "profit_factor_state": pf_state,
        "max_drawdown_pct": dd,
        "futures_equity_usdt": fe, "spot_equity_usdt": se,
        "open_futures_notional_usdt": _f(pv.long_notional + pv.short_notional),
        "open_futures_margin_usdt": margin,
        "margin_utilization_pct": margin_util,
        "open_stop_risk_usdt": _f(pv.open_stop_risk),
        "open_stop_risk_is_partial": pv.stop_risk_incomplete > 0,
        "positions_without_stop": pv.positions_without_stop,
        "positions_stop_malformed": pv.positions_stop_malformed,
        "positions_invalid_qty": pv.positions_invalid_qty,
        "risk_engine_reserved_usdt": reserved,
        # UC AYRI KAVRAM — panel bunlari TEK KARTTA TOPLAMAZ.
        "futures_stop_risk_usdt": fut_risk,
        "futures_risk_budget_utilization_pct": futures_budget_util,
        "spot_exposure_usdt": spot_exp,
        "spot_exposure_unknown": spot_unknown,
        "spot_symbols_unknown_price": spot_bad_price,
        "spot_stop_risk_usdt": spot_risk,
        "spot_unbounded_notional_usdt": spot_unbounded,
        "spot_symbols_without_stop": spot_no_stop,
        "spot_allocation_max_usdt": spot_cap,
        "spot_allocation_utilization_pct": spot_alloc_util,
        "risk_budget_max_usdt": budget_max,
        "risk_equity_basis_usdt": basis, "risk_equity_basis_kind": basis_kind,
        # GERIYE UYUMLULUK: eski alan adi KORUNUR; fakat pay (birlesik gozlem) ile payda
        # (futures stop-risk butcesi) AYNI kavram degildir -> TANISAL, UYGULANMAZ.
        # Gercek kapasite kapisi `futures_risk_budget_utilization_pct`tir.
        "open_risk_budget_utilization_pct": budget_util,
        "open_risk_budget_utilization_semantics": "diagnostic_ratio_not_enforced",
        "combined_conservative_observation_usdt": reserved,
        "combined_observation_semantics": "futures_stop_risk_plus_unbounded_spot_notional",
        "unbounded_spot_warning": (
            ("STOPSUZ SPOT — %.2f USDT MARUZİYET (%s)" % (spot_unbounded, ", ".join(spot_no_stop)))
            if (spot_unbounded and spot_no_stop) else None),
        # SERMAYE MUHASEBESI: katki != kar. contributed = starting_equity (katkilarla artar);
        # net PnL = guncel MTM - contributed. Alanlar ledger ozetlerinden birebir okunur.
        "futures_contributed_capital_usdt": _fut_contrib,
        "spot_contributed_capital_usdt": _spot_contrib,
        "total_contributed_capital_usdt": (
            round(_fut_contrib + _spot_contrib, 4)
            if (_fut_contrib is not None and _spot_contrib is not None) else None),
        "futures_total_net_pnl_usdt": (
            round(fe - _fut_contrib, 4)
            if (fe is not None and _fut_contrib is not None) else None),
        "risk_snapshot_age_s": r_age, "risk_snapshot_state": risk_state_label,
        "open_positions": pv.open_total, "open_long": pv.open_long, "open_short": pv.open_short,
        "any_stale_price": pv.any_stale_price,
        "as_of": as_of, "source_freshness": source_freshness,
        "unavailable_reason": why,
    }


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


__all__ = ["DEFAULT_TAKER_PCT", "FRESH_OK", "FRESH_STALE", "FRESH_UNKNOWN", "Inconsistency",
           "PCT_BASIS_COST", "PCT_BASIS_MARGIN", "PF_FINITE", "PF_NO_CLOSED_TRADES",
           "PF_POSITIVE_INFINITY", "PF_UNDEFINED", "PortfolioView", "PositionView",
           "STOP_RISK_INVALID_ENTRY", "STOP_RISK_INVALID_QTY", "STOP_RISK_MALFORMED",
           "STOP_RISK_NO_STOP", "STOP_RISK_OK", "canonical_summary", "check_invariants", "dec",
           "dec_or_none", "finite_float_or_none", "fmt_money", "fmt_pct", "fmt_qty",
           "money_totals_unavailable_reason", "pnl_class", "portfolio_view", "position_view",
           "profit_factor_state", "realized_net"]
