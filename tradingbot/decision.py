"""KARAR düğümü — coin analizlerini portföy bağlamında AL / SAT / TUT / BEKLE kararına çevirir.

Girdi : CoinAnalysis listesi + Portföy + risk ayarları
Çıktı : Decision listesi (boyut, stop, güven, gerekçeler) + DecisionSummary
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from .analyzer import CoinAnalysis
from .config import RiskConfig
from .portfolio import Portfolio

ACTION_TR = {"BUY": "AL", "SELL": "SAT", "HOLD": "TUT", "WATCH": "BEKLE"}


@dataclass
class Decision:
    symbol: str
    action: str                 # BUY / SELL / HOLD / WATCH
    confidence: int = 0         # 0-100
    price: float = 0.0
    units: float = 0.0
    notional_usdt: float = 0.0
    stop: float | None = None
    strategy: str = ""
    reasons: list[str] = field(default_factory=list)
    holding: bool = False

    @property
    def action_tr(self) -> str:
        return ACTION_TR.get(self.action, self.action)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["action_tr"] = self.action_tr
        return d


@dataclass
class DecisionSummary:
    equity: float
    cash: float
    open_positions: int
    market_regime: str
    btc_filter_active: bool
    n_buy: int
    n_sell: int
    n_hold: int
    n_watch: int

    def to_dict(self) -> dict:
        return asdict(self)


def _size(analysis: CoinAnalysis, equity: float, cash: float, risk: RiskConfig, scale: float) -> tuple[float, float, float]:
    """(units, notional, stop) — risk bazlı boyut, max pozisyon ve nakit ile sınırlı."""
    price = analysis.price
    stop_dist = analysis.atr14 * risk.atr_stop_mult
    if price <= 0 or stop_dist <= 0:
        return 0.0, 0.0, 0.0
    risk_amt = equity * risk.risk_per_trade_pct / 100.0 * scale
    units = risk_amt / stop_dist
    cap_units = min(equity * risk.max_position_pct / 100.0 * scale, cash * 0.98) / price
    units = min(units, cap_units)
    # Binance minimum emir tutarı: risk boyutu minimumun altındaysa ve üst sınır izin veriyorsa minimuma çek
    if analysis.min_notional > 0 and units * price < analysis.min_notional:
        units = min(cap_units, analysis.min_notional * 1.02 / price) if cap_units * price >= analysis.min_notional else 0.0
    if analysis.amount_step > 0 and units > 0:
        units = math.floor(units / analysis.amount_step + 1e-12) * analysis.amount_step
    if analysis.min_notional > 0 and units * price < analysis.min_notional * 0.999:
        units = 0.0
    units = max(0.0, units)
    stop = price - stop_dist
    if analysis.strategy_stop:
        stop = max(stop, analysis.strategy_stop)
    return units, units * price, stop


def decide(analyses: list[CoinAnalysis], portfolio: Portfolio, risk: RiskConfig) -> tuple[list[Decision], DecisionSummary]:
    prices = {a.symbol: a.price for a in analyses if a.price > 0}
    equity = portfolio.equity(prices)
    cash = portfolio.cash

    btc = next((a for a in analyses if a.symbol.startswith("BTC/")), None)
    market_regime = btc.regime if btc else "BİLİNMİYOR"
    btc_bear = bool(risk.btc_regime_filter and btc and btc.regime == "DÜŞÜŞ")

    decisions: list[Decision] = []
    buy_candidates: list[tuple[CoinAnalysis, Decision]] = []

    for a in analyses:
        d = Decision(symbol=a.symbol, action="WATCH", price=a.price, strategy=a.best_strategy)
        pos = portfolio.positions.get(a.symbol)
        d.holding = pos is not None
        if a.error:
            d.reasons.append(a.error)
            decisions.append(d)
            continue

        if pos is not None:
            # --- pozisyon var: SAT mı TUT mu? ----------------------------------
            new_stop = max(pos.stop, a.strategy_stop or 0.0, a.price - a.atr14 * risk.atr_stop_mult)
            if a.price <= pos.stop:
                d.action, d.confidence = "SELL", 90
                d.reasons.append(f"Stop tetiklendi: fiyat {a.price:.4g} ≤ stop {pos.stop:.4g}")
            elif a.signal == "SELL" or a.exit_signal_now:
                d.action, d.confidence = "SELL", 80
                d.reasons.append("Strateji çıkış sinyali verdi")
            elif a.strategy_position == "FLAT":
                d.action, d.confidence = "SELL", 70
                d.reasons.append("Strateji artık pozisyonda değil (stop/çıkış) → senkron için sat")
            else:
                d.action, d.confidence = "HOLD", max(50, a.score)
                d.reasons.append(f"Pozisyon korunuyor; iz süren stop {new_stop:.4g}")
                pos.stop = new_stop
            d.units, d.notional_usdt, d.stop = pos.units, pos.units * a.price, pos.stop
            d.reasons.extend(a.reasons[:2])
            decisions.append(d)
            continue

        # --- pozisyon yok: AL mı BEKLE mi? -----------------------------------------
        conf = a.score
        scale = 1.0
        if btc_bear and not a.symbol.startswith("BTC/"):
            conf -= 10
            scale = 0.5
        late_entry = a.signal == "HOLD" and a.bars_since_entry is not None and a.bars_since_entry <= 2
        if (a.signal == "BUY" or late_entry) and a.has_edge:
            if conf >= risk.min_confidence_to_buy:
                d.action = "BUY"
                d.confidence = conf
                d.reasons.append("Giriş sinyali + doğrulanmış OOS edge" if a.signal == "BUY" else f"Geç giriş ({a.bars_since_entry} bar önce sinyal)")
                if btc_bear:
                    d.reasons.append("BTC düşüş rejimi → boyut %50, güven -10")
                buy_candidates.append((a, d))
            else:
                d.confidence = conf
                d.reasons.append(f"Sinyal var ama güven {conf} < {risk.min_confidence_to_buy:.0f} → bekle")
        else:
            d.confidence = conf
            if a.signal == "HOLD":
                d.reasons.append(f"Strateji {a.bars_since_entry} bardır LONG — giriş kaçırıldı, sonraki sinyali bekle")
            elif a.signal == "SELL":
                d.reasons.append("Çıkış sinyali (pozisyon yok) → bekle")
            else:
                d.reasons.append("Giriş sinyali yok" if a.has_edge else f"Edge yok — {a.edge_note}")
        d.reasons.extend(a.reasons[:2])
        decisions.append(d)

    # --- AL adaylarını güvene göre sırala, kapasite ve nakit ile sınırla ---------
    open_n = len(portfolio.positions) - sum(1 for d in decisions if d.action == "SELL")
    buy_candidates.sort(key=lambda x: x[1].confidence, reverse=True)
    avail_cash = cash
    for a, d in buy_candidates:
        if open_n >= risk.max_open_positions:
            d.action = "WATCH"
            d.reasons.insert(0, f"Max açık pozisyon ({risk.max_open_positions}) dolu")
            continue
        scale = 0.5 if (btc_bear and not a.symbol.startswith("BTC/")) else 1.0
        units, notional, stop = _size(a, equity, avail_cash, risk, scale)
        if units <= 0 or notional < max(1.0, a.min_notional):
            d.action = "WATCH"
            d.reasons.insert(0, f"Yetersiz nakit / Binance min emir ({a.min_notional:g} USDT) sağlanamıyor")
            continue
        d.units, d.notional_usdt, d.stop = round(units, 8), round(notional, 2), stop
        d.reasons.append(f"Boyut: {notional:.0f} USDT (risk %{risk.risk_per_trade_pct * scale:g}, stop {stop:.4g})")
        avail_cash -= notional
        open_n += 1

    counts = {k: sum(1 for d in decisions if d.action == k) for k in ("BUY", "SELL", "HOLD", "WATCH")}
    summary = DecisionSummary(
        equity=equity, cash=cash, open_positions=len(portfolio.positions), market_regime=market_regime,
        btc_filter_active=btc_bear, n_buy=counts["BUY"], n_sell=counts["SELL"], n_hold=counts["HOLD"], n_watch=counts["WATCH"],
    )
    return decisions, summary
