"""GLOBAL RISK ENGINE — deterministik, LLM'den bağımsız. Bir planı portföy durumuna karşı değerlendirir.

Yasaklar (kod olarak): martingale yok (zarar sonrası boyut artmaz), plansız averaging down yok, minimum emir için
riski büyütme yok (→ NO_TRADE_MIN_ORDER_CONFLICT), stop kaldırma yok, likidasyon stop'u yok.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..core import from_iso, utc_now
from .killswitch import KillSwitch
from .profiles import RiskProfile
from .state import PortfolioState, cluster_of

MAJORS = {"BTC", "ETH"}


@dataclass
class Check:
    code: str
    ok: bool
    value: Any = None
    limit: Any = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)      # başarısız kontrol kodları
    warnings: list[str] = field(default_factory=list)
    adjusted_notional: float | None = None
    adjusted_leverage: int | None = None
    risk_usdt: float | None = None
    checks: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class SizeResult:
    notional: float
    margin: float
    leverage: int
    risk_usdt: float
    ok: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def size_position(*, equity: float, risk_pct: float, entry: float, stop: float, min_notional: float, max_leverage: int,
                  max_position_pct: float, liq_buffer_mult: float | None = None, mmr: float = 0.004,
                  requested_leverage: int | None = None) -> SizeResult:
    """Risk bazlı boyut: notional = risk_usdt / stop%. Min notional'a çıkmak için risk BÜYÜTÜLMEZ."""
    if entry <= 0 or stop is None or stop <= 0 or entry == stop:
        return SizeResult(0, 0, 1, 0, False, "INVALID_STOP")
    stop_frac = abs(entry - stop) / entry
    risk_usdt = equity * risk_pct / 100.0
    notional = risk_usdt / stop_frac
    cap = equity * max_position_pct / 100.0 * max(1, max_leverage)
    notional = min(notional, cap)
    lev = max(1, min(max_leverage, requested_leverage or max_leverage))
    # likidasyon tamponu: yaklaşık liq mesafesi (1/lev − mmr) ≥ k × stop mesafesi
    if liq_buffer_mult:
        while lev > 1 and (1.0 / lev - mmr) < liq_buffer_mult * stop_frac:
            lev -= 1
        if (1.0 / lev - mmr) < liq_buffer_mult * stop_frac:
            return SizeResult(0, 0, lev, risk_usdt, False, "LIQ_BUFFER_TOO_THIN")
    margin_cap = equity * max_position_pct / 100.0
    notional = min(notional, margin_cap * lev)
    if notional < min_notional:
        return SizeResult(round(notional, 4), round(notional / lev, 4), lev, round(notional * stop_frac, 6), False, "NO_TRADE_MIN_ORDER_CONFLICT")
    return SizeResult(round(notional, 4), round(notional / lev, 4), lev, round(notional * stop_frac, 6), True)


class RiskEngine:
    def __init__(self, profile: RiskProfile, killswitch: KillSwitch | None = None, clusters: dict[str, str] | None = None):
        self.profile = profile
        self.ks = killswitch or KillSwitch()
        self.clusters = clusters

    # ------------------------------------------------------------ boyutlandırma tabanı (TEK KAYNAK)
    def equity_basis(self, state: PortfolioState) -> float:
        """Risk yüzdelerinin uygulandığı özkaynak tabanı.

        `size_on_live_equity=False` (PAPER_RESEARCH) iken taban `starting_equity`'dir, CANLI equity
        DEĞİL. `evaluate()` kabul kararını ve `snapshot()` gözlem çıktısını AYNI bu fonksiyondan
        alır; panel bu tabanı tahmin ETMEZ, motorun yayımladığını okur.
        """
        return state.equity if self.profile.size_on_live_equity else state.starting_equity

    def equity_basis_kind(self) -> str:
        return "live_equity" if self.profile.size_on_live_equity else "starting_equity"

    # ------------------------------------------------------------ ana değerlendirme
    def evaluate(self, plan: dict, state: PortfolioState, market_ctx: dict | None = None) -> RiskDecision:
        """plan: {symbol, market_type, direction(LONG/SHORT), entry, stop, targets, notional, margin, leverage, amount_type,
        expected_r, spread_pct, liq_price, atr_pct, min_notional}. market_ctx: {now_utc, min_notional, mmr}."""
        p, ctx = self.profile, market_ctx or {}
        checks: list[Check] = []
        warns: list[str] = []
        now = ctx.get("now_utc") or utc_now()
        symbol, mtype, side = plan["symbol"], plan.get("market_type", "USDM_PERP"), plan.get("direction", "LONG")
        entry, stop = float(plan.get("entry") or 0), plan.get("stop")
        notional = float(plan.get("notional") or 0)
        lev = int(plan.get("leverage") or 1)
        equity_basis = self.equity_basis(state)        # DAVRANIŞ AYNI — ifade tek kaynağa taşındı

        def add(code, ok, value=None, limit=None, note=""):
            checks.append(Check(code, bool(ok), value, limit, note))

        add("KILL_SWITCH_ACTIVE", self.ks.allows_entry(), self.ks.state, "ARMED")
        add("STOP_PRESENT", stop is not None and float(stop) > 0 and entry > 0, stop, "stop zorunlu")
        # ADET limitleri OPSIYONEL: None ise kapi HIC uygulanmaz ve karar gercek risk butcesine kalir
        # (toplam acik risk / margin / liq buffer / same-symbol). TESTNET/LIVE profilleri adet
        # tavanlarini KORUR; yalnizca PAPER_RESEARCH None kullanir.
        if p.max_open_positions is not None:
            add("MAX_POSITIONS", len(state.open_positions) < p.max_open_positions, len(state.open_positions), p.max_open_positions)
        if p.max_positions_per_market is not None:
            add("MAX_POSITIONS_MARKET", len(state.positions_in(mtype)) < p.max_positions_per_market, len(state.positions_in(mtype)), p.max_positions_per_market)
        add("ALREADY_OPEN_SAME_SYMBOL", not any(o.symbol == symbol and o.market_type == mtype for o in state.open_positions), symbol)
        net = state.net_exposure(symbol)
        opposite = (net > 0 and side == "SHORT") or (net < 0 and side == "LONG")
        add("OPPOSITE_EXPOSURE_CONFLICT", not opposite, net, "spot long ↔ futures short aynı coin yasak")
        # risk/işlem — boyutu yalnız AŞAĞI ayarla
        risk_usdt = None
        adj_notional = notional
        if stop is not None and entry > 0 and float(stop) > 0:
            stop_frac = abs(entry - float(stop)) / entry
            risk_usdt = notional * stop_frac
            allowed_risk = equity_basis * p.risk_per_trade_pct / 100.0
            if risk_usdt > allowed_risk * 1.0001:
                adj_notional = allowed_risk / stop_frac
                warns.append(f"RISK_PER_TRADE: notional {notional:.2f} → {adj_notional:.2f}")
                risk_usdt = allowed_risk
            add("RISK_PER_TRADE", True, round(risk_usdt, 4), round(allowed_risk, 4))
            total_after = state.total_open_risk_usdt + risk_usdt
            add("TOTAL_OPEN_RISK", total_after <= equity_basis * p.max_total_open_risk_pct / 100.0 + 1e-9, round(total_after, 4),
                round(equity_basis * p.max_total_open_risk_pct / 100.0, 4))
        # kaldıraç
        adj_lev = lev
        if mtype != "SPOT":
            if lev > p.futures_max_leverage:
                adj_lev = p.futures_max_leverage
                warns.append(f"LEVERAGE_CAP: {lev}x → {adj_lev}x")
            add("LEVERAGE_CAP", True, lev, p.futures_max_leverage)
            if p.futures_margin_utilization_cap_pct is not None and state.equity > 0:
                util = (state.used_margin + adj_notional / max(adj_lev, 1)) / state.equity * 100
                add("MARGIN_UTILIZATION", util <= p.futures_margin_utilization_cap_pct, round(util, 2), p.futures_margin_utilization_cap_pct)
            if p.min_liquidation_buffer_mult is not None and stop is not None and entry > 0:
                liq = plan.get("liq_price")
                mmr = float(ctx.get("mmr", 0.004))
                liq_dist = abs(entry - float(liq)) / entry if liq else (1.0 / max(adj_lev, 1) - mmr)
                stop_dist = abs(entry - float(stop)) / entry
                add("LIQ_BUFFER", liq_dist >= p.min_liquidation_buffer_mult * stop_dist, round(liq_dist / stop_dist, 2) if stop_dist else None, p.min_liquidation_buffer_mult)
        else:
            adj_lev = 1
            add("SPOT_NO_SHORT", side != "SHORT", side)
        # tek coin cap
        add("MAX_POSITION_PCT", adj_notional <= equity_basis * p.max_position_pct / 100.0 * max(adj_lev, 1) + 1e-9, round(adj_notional, 2),
            round(equity_basis * p.max_position_pct / 100.0 * max(adj_lev, 1), 2))
        # zarar limitleri / DD
        if p.daily_loss_stop_pct is not None:
            lim = -state.starting_equity * p.daily_loss_stop_pct / 100.0 if not p.size_on_live_equity else -state.high_water_mark * p.daily_loss_stop_pct / 100.0
            add("DAILY_LOSS", state.realized_pnl_today > lim, round(state.realized_pnl_today, 4), round(lim, 4))
        if p.weekly_loss_stop_pct is not None:
            lim = -state.high_water_mark * p.weekly_loss_stop_pct / 100.0
            add("WEEKLY_LOSS", state.realized_pnl_week > lim, round(state.realized_pnl_week, 4), round(lim, 4))
        if p.max_drawdown_kill_pct is not None:
            add("MAX_DRAWDOWN", state.drawdown_pct < p.max_drawdown_kill_pct, round(state.drawdown_pct, 2), p.max_drawdown_kill_pct)
        # küme / altcoin
        cl = cluster_of(symbol, self.clusters)
        if p.correlated_cluster_cap is not None:
            add("CLUSTER_CAP", state.cluster_count(cl, side) < p.correlated_cluster_cap, f"{cl}:{state.cluster_count(cl, side)}", p.correlated_cluster_cap)
        if p.altcoin_net_exposure_cap_pct is not None and symbol.split("/")[0].upper() not in MAJORS and state.equity > 0:
            alt = (state.altcoin_notional() + adj_notional) / state.equity * 100
            add("ALTCOIN_EXPOSURE", alt <= p.altcoin_net_exposure_cap_pct, round(alt, 1), p.altcoin_net_exposure_cap_pct)
        # spread / beklenen R
        if p.max_spread_pct is not None and plan.get("spread_pct") is not None:
            add("SPREAD", float(plan["spread_pct"]) <= p.max_spread_pct, plan["spread_pct"], p.max_spread_pct)
        if p.min_expected_r is not None and plan.get("expected_r") is not None:
            add("MIN_EXPECTED_R", float(plan["expected_r"]) >= p.min_expected_r, plan["expected_r"], p.min_expected_r)
        # cooldown'lar
        if p.consecutive_loss_cooldown_n is not None and state.consecutive_losses >= p.consecutive_loss_cooldown_n and state.last_loss_ts:
            hrs = (now - from_iso(state.last_loss_ts)).total_seconds() / 3600
            add("CONSEC_LOSS_COOLDOWN", hrs >= p.cooldown_hours, round(hrs, 1), p.cooldown_hours, f"{state.consecutive_losses} ardışık zarar")
        if p.symbol_cooldown_hours and symbol in state.symbol_last_exit_ts:
            hrs = (now - from_iso(state.symbol_last_exit_ts[symbol])).total_seconds() / 3600
            add("SYMBOL_COOLDOWN", hrs >= p.symbol_cooldown_hours, round(hrs, 1), p.symbol_cooldown_hours)
        # min emir — riski büyütmeden
        min_notional = float(plan.get("min_notional") or ctx.get("min_notional") or 5.0)
        add("MIN_ORDER_CONFLICT", adj_notional >= min_notional, round(adj_notional, 4), min_notional, "boyut minimuma çıkarılmaz")
        failed = [c.code for c in checks if not c.ok]
        return RiskDecision(allowed=not failed, reasons=failed, warnings=warns,
                            adjusted_notional=round(adj_notional, 4) if not failed else None,
                            adjusted_leverage=adj_lev if not failed else None,
                            risk_usdt=round(risk_usdt, 6) if risk_usdt is not None else None, checks=checks)

    # ------------------------------------------------------------ kill switch tetikleri
    def evaluate_kill_triggers(self, state: PortfolioState, health: dict | None = None) -> list[str]:
        """Portföy + sağlık girdilerinden kill switch tetiklerini üretir ve switch'i tetikler."""
        p, h = self.profile, health or {}
        trips: list[str] = []
        basis = state.high_water_mark if p.size_on_live_equity else state.starting_equity
        if p.daily_loss_stop_pct is not None and state.realized_pnl_today <= -basis * p.daily_loss_stop_pct / 100.0:
            trips.append("DAILY_LOSS")
        if p.weekly_loss_stop_pct is not None and state.realized_pnl_week <= -basis * p.weekly_loss_stop_pct / 100.0:
            trips.append("WEEKLY_LOSS")
        if p.max_drawdown_kill_pct is not None and state.drawdown_pct >= p.max_drawdown_kill_pct:
            trips.append("MAX_DRAWDOWN")
        flags = {"stale_data": "STALE_DATA", "ws_sequence_gap": "WS_SEQUENCE_CORRUPTION", "price_divergence": "PRICE_DIVERGENCE",
                 "clock_drift": "CLOCK_DRIFT", "repeated_rejections": "REPEATED_ORDER_REJECTION", "reconciliation_mismatch": "RECONCILIATION_MISMATCH",
                 "db_write_failure": "DB_WRITE_FAILURE", "disk_full": "DISK_FULL", "rate_limit_ban": "RATE_LIMIT_BAN",
                 "llm_schema_streak": "LLM_SCHEMA_FAILURE_STREAK", "model_drift": "MODEL_DRIFT", "wide_spread": "WIDE_SPREAD",
                 "extreme_volatility": "EXTREME_VOLATILITY", "exchange_maintenance": "EXCHANGE_MAINTENANCE",
                 "balance_mismatch": "BALANCE_MISMATCH", "unexpected_open_position": "UNEXPECTED_OPEN_POSITION"}
        for k, code in flags.items():
            if h.get(k):
                trips.append(code)
        for code in trips:
            self.ks.trip(code, str(h.get(code.lower(), "")) if h else "", source="risk_engine")
        return trips

    def snapshot(self, state: PortfolioState) -> dict:
        """SALT-OKUNUR gözlem çıktısı. Kabul kararını ETKİLEMEZ; yalnız motorun ZATEN kullandığı
        değerleri yayımlar.

        `equity_basis` / `max_total_open_risk_usdt`: `evaluate()` içindeki `TOTAL_OPEN_RISK` kapısı
        tam olarak bu iki değeri kullanır (bkz. `equity_basis()` ve aşağıdaki `add("TOTAL_OPEN_RISK"…)`).
        Panel bunları okumadığında bütçeyi `exposure.equity`'den TAHMİN ediyordu ve motorun gerçekte
        uyguladığı orandan farklı bir yüzde gösteriyordu.
        """
        p = self.profile
        basis = self.equity_basis(state)
        return {"profile": p.to_dict(), "killswitch": self.ks.to_dict(),
                "exposure": {"equity": state.equity, "hwm": state.high_water_mark, "drawdown_pct": round(state.drawdown_pct, 3),
                             "starting_equity": state.starting_equity,
                             "equity_basis": basis, "equity_basis_kind": self.equity_basis_kind(),
                             "max_total_open_risk_usdt": round(basis * p.max_total_open_risk_pct / 100.0, 6),
                             "open_positions": len(state.open_positions), "total_open_risk_usdt": round(state.total_open_risk_usdt, 4),
                             "used_margin": state.used_margin, "altcoin_notional": round(state.altcoin_notional(), 4),
                             "pnl_today": round(state.realized_pnl_today, 4), "pnl_week": round(state.realized_pnl_week, 4),
                             "consecutive_losses": state.consecutive_losses,
                             "positions": [o.to_dict() for o in state.open_positions]}}
