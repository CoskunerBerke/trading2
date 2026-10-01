"""Portföy durumu — spot + futures birleşik görünüm (risk motorunun girdisi). Düz dict'lerden kurulur
(eski defterler ya da v2 defterler besleyebilir)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from math import isfinite

from ..core import from_iso, utc_now

MAJORS = {"BTC", "ETH"}
SPOT = "SPOT"
CLUSTERS_DEFAULT: dict[str, str] = {
    "BTC": "major", "ETH": "major", "BNB": "exchange", "SOL": "l1", "AVAX": "l1", "ADA": "l1", "DOT": "l1", "NEAR": "l1", "APT": "l1", "SUI": "l1",
    "TRX": "l1", "TON": "l1", "XRP": "payments", "XLM": "payments", "LTC": "payments", "BCH": "payments", "DOGE": "meme", "SHIB": "meme",
    "PEPE": "meme", "WIF": "meme", "BONK": "meme", "FLOKI": "meme", "LINK": "oracle", "FET": "ai", "RENDER": "ai", "TAO": "ai", "ARB": "l2",
    "OP": "l2", "MATIC": "l2", "POL": "l2", "UNI": "defi", "AAVE": "defi", "MKR": "defi", "PAXG": "gold", "XAUT": "gold",
}


def _finite_positive(x) -> float | None:
    """Sonlu ve pozitif ise float, degilse None. NaN/Inf/0/negatif/None/bozuk -> None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not isfinite(v) or v <= 0:
        return None
    return v


def spot_notional_from_prices(qty, mark, cost_basis) -> tuple[float, bool]:
    """(notional, unknown) — SPOT maruziyeti icin FAIL-CLOSED fiyat secimi.

    Bozuk/eksik/NaN/Inf/sifir/negatif mark fiyati maruziyeti SIFIR gostermemelidir; aksi halde
    spot allocation kapisi fiyat hatasi yuzunden kendiliginden acilir (fail-open). Sirasiyla:
    gecerli mark -> gecerli cost basis (muhafazakar) -> ikisi de gecersizse `unknown=True`.
    """
    q = _finite_positive(qty)
    if q is None:
        return 0.0, False                       # pozisyon yok
    px = _finite_positive(mark) or _finite_positive(cost_basis)
    if px is None:
        return 0.0, True                        # maruziyet BILINMIYOR
    return q * px, False


def cluster_of(symbol: str, clusters: dict[str, str] | None = None) -> str:
    base = symbol.split("/")[0].upper()
    for pre in ("1000", "1M"):
        if base.startswith(pre) and len(base) > len(pre):
            base = base[len(pre):]
    return (clusters or CLUSTERS_DEFAULT).get(base, "alt_other")


@dataclass
class OpenPosition:
    symbol: str
    market_type: str            # SPOT | USDM_PERP
    side: str                   # LONG | SHORT
    notional: float
    margin: float
    risk_usdt: float            # |entry-stop|/entry × notional
    entry: float
    stop: float | None
    leverage: float = 1.0
    liq_price: float | None = None
    cluster: str = "alt_other"
    opened_at: str = ""
    notional_unknown: bool = False   # fiyat GECERSIZ -> maruziyet olculemedi (sessiz 0 DEGIL)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PortfolioState:
    equity: float
    starting_equity: float
    high_water_mark: float
    available: float
    used_margin: float = 0.0
    open_positions: list[OpenPosition] = field(default_factory=list)
    realized_pnl_today: float = 0.0
    realized_pnl_week: float = 0.0
    day_key: str = ""
    week_key: str = ""
    consecutive_losses: int = 0
    last_loss_ts: str | None = None
    symbol_last_exit_ts: dict[str, str] = field(default_factory=dict)
    equity_day_open: float | None = None
    equity_week_open: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    # ------------------------------------------------------------ türev ölçüler
    @property
    def drawdown_pct(self) -> float:
        return (1 - self.equity / self.high_water_mark) * 100 if self.high_water_mark > 0 else 0.0

    @property
    def total_open_risk_usdt(self) -> float:
        """BIRLESIK toplam (spot + futures) — YALNIZ RAPORLAMA. Kabul kapisi bunu KULLANMAZ.

        Stopsuz bir spot pozisyonu `build_state` icinde TAM notional risk sayilir; bu deger futures
        stop-risk butcesiyle ayni kovaya konursa 8 USDT'lik stopsuz spot, 3 USDT'lik futures
        butcesini tek basina bloke eder. Kabul karari icin market kovasi kullanilir:
        `futures_stop_risk_usdt` / `spot_stop_risk_usdt` (bkz. `open_stop_risk_in`).
        """
        return sum(p.risk_usdt for p in self.open_positions)

    # --- MARKET KOVALARI: spot notional'i futures stop-risk butcesine KARISTIRMAZ ---
    @property
    def futures_stop_risk_usdt(self) -> float:
        """Yalniz acik FUTURES pozisyonlarinin gecerli stop riski."""
        return sum(p.risk_usdt for p in self.open_positions if p.market_type != SPOT)

    @property
    def spot_exposure_usdt(self) -> float:
        """Acik SPOT pozisyonlarinin notional toplami (maliyet/piyasa degeri) — RISK DEGIL.

        Fiyati OLCULEMEYEN pozisyonlar bu toplamda YOKTUR; onlarin varligi
        `spot_exposure_unknown` ile bildirilir ve kabul kapisi fail-closed davranir.
        """
        return sum(p.notional for p in self.open_positions
                   if p.market_type == SPOT and not p.notional_unknown)

    @property
    def spot_exposure_unknown(self) -> bool:
        """En az bir spot pozisyonun maruziyeti GECERSIZ FIYAT nedeniyle olculemedi."""
        return any(p.market_type == SPOT and p.notional_unknown for p in self.open_positions)

    @property
    def spot_symbols_unknown_price(self) -> list[str]:
        return [p.symbol for p in self.open_positions if p.market_type == SPOT and p.notional_unknown]

    @property
    def spot_stop_risk_usdt(self) -> float:
        """YALNIZ gercek/uygulanan stop'u olan spot pozisyonlarinin risk toplami.

        Spot stop'u `SpotLedger.positions()` icinde duran GERCEK STOP_MARKET/STOP_LIMIT/OCO
        emrinden turetilir (`stop_orders()`); yalnizca state'e yazilmis bir alan degildir. Stop yoksa
        pozisyon burada SAYILMAZ; `spot_unbounded_notional_usdt` altinda AYRI raporlanir.
        """
        return sum(p.risk_usdt for p in self.open_positions if p.market_type == SPOT and p.stop is not None)

    @property
    def spot_unbounded_notional_usdt(self) -> float:
        """Stop'u OLMAYAN spot pozisyonlarinin notional toplami — stopla SINIRLANMAMIS maruziyet."""
        return sum(p.notional for p in self.open_positions if p.market_type == SPOT and p.stop is None)

    @property
    def spot_symbols_without_stop(self) -> list[str]:
        return [p.symbol for p in self.open_positions if p.market_type == SPOT and p.stop is None]

    def open_stop_risk_in(self, market_type: str) -> float:
        """Adayin KENDI marketindeki acik stop riski — kabul kapisinin tek dogru tabani."""
        return self.spot_stop_risk_usdt if market_type == SPOT else self.futures_stop_risk_usdt

    def positions_in(self, market_type: str) -> list[OpenPosition]:
        return [p for p in self.open_positions if p.market_type == market_type]

    def net_exposure(self, symbol: str) -> float:
        """Aynı coin için spot+futures birleşik yönlü notional (long +, short −)."""
        return sum((p.notional if p.side == "LONG" else -p.notional) for p in self.open_positions if p.symbol == symbol)

    def altcoin_notional(self) -> float:
        return sum(p.notional for p in self.open_positions if p.symbol.split("/")[0].upper() not in MAJORS)

    def cluster_count(self, cluster: str, side: str) -> int:
        return sum(1 for p in self.open_positions if p.cluster == cluster and p.side == side)


def _pnl_window(history: list[dict], since: datetime) -> float:
    tot = 0.0
    for h in history:
        ts = h.get("closed_at") or h.get("exit_time")
        if not ts:
            continue
        try:
            t = from_iso(str(ts))
        except ValueError:
            continue
        if t >= since:
            tot += float(h.get("net_pnl", h.get("pnl", 0.0)) or 0.0)
    return tot


def build_state(*, equity: float, starting_equity: float, available: float, used_margin: float, positions: list[dict],
                history: list[dict], high_water_mark: float | None = None, now: datetime | None = None,
                clusters: dict[str, str] | None = None) -> PortfolioState:
    """Düz dict'lerden PortfolioState. positions: {symbol, market_type, side, notional, margin, entry, stop, leverage, liq_price, opened_at}."""
    now = now or utc_now()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = day0 - timedelta(days=day0.weekday())
    ops: list[OpenPosition] = []
    for p in positions:
        entry, stop, notional = float(p.get("entry") or 0), p.get("stop"), float(p.get("notional") or 0)
        risk = abs(entry - float(stop)) / entry * notional if (stop is not None and entry) else notional
        ops.append(OpenPosition(symbol=p["symbol"], market_type=p.get("market_type", "USDM_PERP"), side=p.get("side", "LONG"),
                                notional=notional, margin=float(p.get("margin") or notional), risk_usdt=risk, entry=entry,
                                stop=float(stop) if stop is not None else None, leverage=float(p.get("leverage") or 1),
                                liq_price=p.get("liq_price"), cluster=cluster_of(p["symbol"], clusters), opened_at=str(p.get("opened_at", "")),
                                notional_unknown=bool(p.get("notional_unknown", False))))
    # ardışık zarar sayacı (en son kapananlardan geriye)
    consec, last_loss = 0, None
    for h in sorted(history, key=lambda x: str(x.get("closed_at") or x.get("exit_time") or ""), reverse=True):
        pnl = float(h.get("net_pnl", h.get("pnl", 0.0)) or 0.0)
        if pnl < 0:
            consec += 1
            last_loss = last_loss or str(h.get("closed_at") or h.get("exit_time"))
        else:
            break
    sym_last: dict[str, str] = {}
    for h in history:
        ts = str(h.get("closed_at") or h.get("exit_time") or "")
        if ts and ts > sym_last.get(h.get("symbol", ""), ""):
            sym_last[h["symbol"]] = ts
    hwm = max(high_water_mark or 0.0, equity, starting_equity)
    return PortfolioState(equity=equity, starting_equity=starting_equity, high_water_mark=hwm, available=available,
                          used_margin=used_margin, open_positions=ops, realized_pnl_today=_pnl_window(history, day0),
                          realized_pnl_week=_pnl_window(history, week0), day_key=day0.strftime("%Y-%m-%d"),
                          week_key=week0.strftime("%G-W%V"), consecutive_losses=consec, last_loss_ts=last_loss,
                          symbol_last_exit_ts=sym_last)
