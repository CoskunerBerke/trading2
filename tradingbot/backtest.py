"""Bar-bar (event) backtester — long-only, spot.

* Girişler bir sonraki barın AÇILIŞINDA yapılır (look-ahead yok).
* Komisyon + kayma her iki yönde de kesilir.
* ATR tabanlı iz süren stop (chandelier): stop = max(stop, close - ATR*k).
* Pozisyon boyutu risk bazlı: (equity * risk%) / stop mesafesi, üst sınır max_position%.
* Metrikler: toplam getiri, CAGR, Sharpe (yıllıklandırılmış), Max DD, win rate,
  profit factor, işlem sayısı, exposure, buy&hold kıyası.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    units: float
    pnl: float
    pnl_pct: float
    reason: str


@dataclass
class Metrics:
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    trades: int = 0
    exposure_pct: float = 0.0
    buy_hold_return_pct: float = 0.0
    avg_trade_pct: float = 0.0
    bars: int = 0

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass
class BacktestResult:
    metrics: Metrics
    trades: list[Trade] = field(default_factory=list)
    equity: pd.Series | None = None
    in_position_at_end: bool = False
    last_stop: float | None = None
    open_entry_time: str | None = None
    open_entry_price: float | None = None
    open_bars_held: int = 0


def _sharpe(returns: np.ndarray, bars_per_year: float) -> float:
    if returns.size < 2:
        return 0.0
    sd = returns.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return 0.0
    return float(returns.mean() / sd * math.sqrt(bars_per_year))


def _max_drawdown(equity: np.ndarray) -> float:
    peaks = np.maximum.accumulate(equity)
    dd = (equity - peaks) / peaks
    return float(dd.min() * 100.0) if dd.size else 0.0


def run_backtest(
    df: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    bars_per_year: float,
    fee_pct: float = 0.10,
    slippage_pct: float = 0.05,
    risk_per_trade_pct: float = 1.0,
    atr_stop_mult: float = 2.5,
    max_position_pct: float = 25.0,
    starting_equity: float = 10_000.0,
    atr_length: int = 14,
) -> BacktestResult:
    n = len(df)
    empty = BacktestResult(Metrics(bars=n))
    if n < 3:
        return empty

    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    atr = ind.atr(df["high"], df["low"], df["close"], atr_length).to_numpy(float)
    entries = signals["entries"].to_numpy(bool)
    exits = signals["exits"].to_numpy(bool)

    fee = fee_pct / 100.0
    slip = slippage_pct / 100.0
    risk = risk_per_trade_pct / 100.0
    max_pos = max_position_pct / 100.0

    cash = starting_equity
    units = 0.0
    stop = 0.0
    entry_px = 0.0
    entry_i = -1
    pending_entry = False
    pending_exit = False
    pending_reason = ""
    equity = np.empty(n)
    in_pos_bars = 0
    trades: list[Trade] = []
    times = df.index

    def _close_position(i: int, px: float, reason: str):
        nonlocal cash, units, entry_px, entry_i
        px_eff = px * (1.0 - slip)
        proceeds = units * px_eff * (1.0 - fee)
        cost = units * entry_px * (1.0 + slip) * (1.0 + fee)
        pnl = proceeds - cost
        trades.append(
            Trade(
                entry_time=str(times[entry_i]), exit_time=str(times[i]),
                entry_price=float(entry_px), exit_price=float(px), units=float(units),
                pnl=float(pnl), pnl_pct=float(pnl / cost * 100.0) if cost else 0.0, reason=reason,
            )
        )
        cash += proceeds
        units = 0.0
        entry_px = 0.0
        entry_i = -1

    for i in range(n):
        # 1) Önceki barın kararını bu barın açılışında uygula
        if pending_exit and units > 0:
            _close_position(i, o[i], pending_reason)
            pending_exit = False
        if pending_entry and units == 0 and np.isfinite(atr[i - 1]) and atr[i - 1] > 0:
            px = o[i]
            stop_dist = atr[i - 1] * atr_stop_mult
            eq_now = cash
            risk_units = (eq_now * risk) / stop_dist
            cap_units = (eq_now * max_pos) / (px * (1.0 + slip) * (1.0 + fee))
            u = max(0.0, min(risk_units, cap_units))
            cost = u * px * (1.0 + slip) * (1.0 + fee)
            if u > 0 and cost <= cash:
                cash -= cost
                units = u
                entry_px = px
                entry_i = i
                stop = px - stop_dist
            pending_entry = False

        # 2) Bar içi stop kontrolü (gap varsa açılıştan)
        if units > 0 and lo[i] <= stop:
            fill = min(o[i], stop)
            _close_position(i, fill, "stop")

        # 3) Bar kapanışı: iz süren stop güncelle, sinyalleri kuyruğa al
        if units > 0:
            in_pos_bars += 1
            if np.isfinite(atr[i]):
                stop = max(stop, c[i] - atr[i] * atr_stop_mult)
            if exits[i]:
                pending_exit = True
                pending_reason = "signal"
        elif entries[i]:
            pending_entry = True

        equity[i] = cash + units * c[i]

    # Açık pozisyonu son kapanıştan değerle (kapatmadan)
    eq_series = pd.Series(equity, index=times)
    rets = np.diff(equity) / equity[:-1]
    rets = rets[np.isfinite(rets)]

    m = Metrics(bars=n)
    m.total_return_pct = float((equity[-1] / starting_equity - 1.0) * 100.0)
    years = n / bars_per_year
    m.cagr_pct = float(((equity[-1] / starting_equity) ** (1.0 / years) - 1.0) * 100.0) if years > 0 and equity[-1] > 0 else 0.0
    m.sharpe = _sharpe(rets, bars_per_year)
    m.max_drawdown_pct = _max_drawdown(equity)
    m.trades = len(trades)
    if trades:
        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [-t.pnl for t in trades if t.pnl <= 0]
        m.win_rate_pct = 100.0 * len(wins) / len(trades)
        gp, gl = sum(wins), sum(losses)
        m.profit_factor = float(gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
        if not math.isfinite(m.profit_factor):
            m.profit_factor = 99.0
        m.avg_trade_pct = float(np.mean([t.pnl_pct for t in trades]))
    m.exposure_pct = 100.0 * in_pos_bars / n
    m.buy_hold_return_pct = float((c[-1] / c[0] - 1.0) * 100.0) if c[0] > 0 else 0.0

    open_pos = units > 0
    return BacktestResult(
        metrics=m, trades=trades, equity=eq_series,
        in_position_at_end=open_pos,
        last_stop=float(stop) if open_pos else None,
        open_entry_time=str(times[entry_i]) if open_pos else None,
        open_entry_price=float(entry_px) if open_pos else None,
        open_bars_held=(n - 1 - entry_i) if open_pos else 0,
    )
