"""Parametre taraması + WALK-FORWARD optimizasyon.

Neden walk-forward?  Tek bir "%70 eğit / %30 test" bölmesi tek bir piyasa dönemine bağlıdır.
Walk-forward, gerçek kullanımı taklit eder: her adımda yalnızca o ana kadarki veriyle en iyi
konfigürasyon seçilir, sonra hiç görülmemiş SONRAKİ dönemde çalıştırılır. Bu OOS dönemleri
birleştirilir → "en iyisini seç" prosedürünün gerçek ileriye dönük performansı ölçülür.

Akış (anchored walk-forward, `folds` adım):
    [---- eğit ----][test1]
    [------- eğit -------][test2]
    [---------- eğit ----------][test3]
    [-------------- eğit -------------][test4]
  * her adımda tüm konfigürasyonlar eğitim penceresinde sıralanır (Sharpe, min işlem şartı)
  * en iyisi test penceresinde çalıştırılır; test getirileri/işlemleri biriktirilir
  * canlı kullanım için: TÜM veride en iyi konfigürasyon seçilir (son adımın mantığıyla)
Edge kuralı: birleşik OOS Sharpe ≥ eşik, OOS PF ≥ eşik, OOS işlem ≥ min.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import BacktestResult, Metrics, run_backtest
from .config import BacktestConfig, RiskConfig
from .strategies import StrategySpec, generate_signals, iter_specs


@dataclass
class SweepRow:
    spec: StrategySpec
    train: Metrics          # tüm veri (in-sample)
    test: Metrics           # son %30 (bilgi amaçlı, tek bölme OOS)

    def to_dict(self) -> dict:
        return {"spec": self.spec.to_dict(), "train": self.train.to_dict(), "test": self.test.to_dict()}


@dataclass
class FoldInfo:
    fold: int
    train_end: str
    test_end: str
    spec: str
    test_sharpe: float
    test_return_pct: float
    test_trades: int
    test_buy_hold_pct: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class SweepResult:
    symbol: str
    rows: list[SweepRow] = field(default_factory=list)
    best: SweepRow | None = None
    wfo: Metrics | None = None                 # birleşik walk-forward OOS metrikleri
    folds: list[FoldInfo] = field(default_factory=list)
    has_edge: bool = False
    edge_note: str = ""

    def top(self, k: int = 5) -> list[SweepRow]:
        return self.rows[:k]


def _rank(rows: list[tuple[StrategySpec, Metrics]], min_trades: int) -> list[tuple[StrategySpec, Metrics]]:
    ok = [r for r in rows if r[1].trades >= min_trades]
    ok.sort(key=lambda r: (r[1].sharpe, r[1].profit_factor), reverse=True)
    rest = [r for r in rows if r[1].trades < min_trades]
    rest.sort(key=lambda r: r[1].sharpe, reverse=True)
    return ok + rest


def _aggregate(results: list[BacktestResult], bars_per_year: float, starting_equity: float) -> Metrics:
    """Ardışık OOS pencerelerini tek bir eğri gibi birleştirir."""
    m = Metrics()
    rets: list[np.ndarray] = []
    trades = []
    bh = 1.0
    bars = 0
    exposure_bars = 0.0
    for r in results:
        if r.equity is None or len(r.equity) < 2:
            continue
        eq = r.equity.to_numpy(float)
        rr = np.diff(eq) / eq[:-1]
        rets.append(rr[np.isfinite(rr)])
        trades.extend(r.trades)
        bh *= 1.0 + r.metrics.buy_hold_return_pct / 100.0
        bars += r.metrics.bars
        exposure_bars += r.metrics.exposure_pct / 100.0 * r.metrics.bars
    if not rets:
        return m
    all_r = np.concatenate(rets)
    curve = starting_equity * np.cumprod(1.0 + all_r)
    curve = np.concatenate([[starting_equity], curve])
    m.bars = bars
    m.total_return_pct = float((curve[-1] / starting_equity - 1.0) * 100.0)
    years = len(all_r) / bars_per_year
    m.cagr_pct = float(((curve[-1] / starting_equity) ** (1.0 / years) - 1.0) * 100.0) if years > 0 and curve[-1] > 0 else 0.0
    sd = all_r.std(ddof=1) if all_r.size > 1 else 0.0
    m.sharpe = float(all_r.mean() / sd * math.sqrt(bars_per_year)) if sd > 0 else 0.0
    peaks = np.maximum.accumulate(curve)
    m.max_drawdown_pct = float(((curve - peaks) / peaks).min() * 100.0)
    m.trades = len(trades)
    if trades:
        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [-t.pnl for t in trades if t.pnl <= 0]
        m.win_rate_pct = 100.0 * len(wins) / len(trades)
        gp, gl = sum(wins), sum(losses)
        m.profit_factor = float(gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
        m.avg_trade_pct = float(np.mean([t.pnl_pct for t in trades]))
    m.exposure_pct = 100.0 * exposure_bars / bars if bars else 0.0
    m.buy_hold_return_pct = float((bh - 1.0) * 100.0)
    return m


def sweep_symbol(
    symbol: str,
    df: pd.DataFrame,
    *,
    bars_per_year: float,
    bt_cfg: BacktestConfig,
    risk_cfg: RiskConfig,
    families: list[str] | None = None,
    min_notional: float = 0.0,
    amount_step: float = 0.0,
) -> SweepResult:
    n = len(df)
    result = SweepResult(symbol=symbol)
    if n < 300:
        result.edge_note = f"Yetersiz veri ({n} bar)"
        return result

    common = dict(
        bars_per_year=bars_per_year, fee_pct=bt_cfg.fee_pct, slippage_pct=bt_cfg.slippage_pct,
        risk_per_trade_pct=risk_cfg.risk_per_trade_pct, atr_stop_mult=risk_cfg.atr_stop_mult,
        max_position_pct=risk_cfg.max_position_pct, starting_equity=risk_cfg.starting_equity_usdt,
        min_notional=min_notional, amount_step=amount_step,
    )
    specs = list(iter_specs(families))
    sigs = {spec.label: generate_signals(df, spec) for spec in specs}   # göstergeler nedensel → bir kez hesapla

    # --- walk-forward pencereleri ------------------------------------------------
    folds = max(1, int(bt_cfg.wfo_folds))
    first_train_end = int(n * bt_cfg.wfo_initial_train_ratio)
    test_len = (n - first_train_end) // folds
    boundaries = [(first_train_end + k * test_len, first_train_end + (k + 1) * test_len if k < folds - 1 else n) for k in range(folds)]

    oos_results: list[BacktestResult] = []
    for k, (tr_end, te_end) in enumerate(boundaries):
        min_tr = max(6, int(round(bt_cfg.min_trades * tr_end / n)))
        ranked = _rank([(s, run_backtest(df.iloc[:tr_end], sigs[s.label].iloc[:tr_end], **common).metrics) for s in specs], min_tr)
        if not ranked or ranked[0][1].trades < min_tr:
            continue
        chosen = ranked[0][0]
        res = run_backtest(df.iloc[tr_end:te_end], sigs[chosen.label].iloc[tr_end:te_end], **common)
        oos_results.append(res)
        result.folds.append(FoldInfo(
            fold=k + 1, train_end=str(df.index[tr_end - 1])[:10], test_end=str(df.index[te_end - 1])[:10],
            spec=chosen.label, test_sharpe=round(res.metrics.sharpe, 3), test_return_pct=round(res.metrics.total_return_pct, 3),
            test_trades=res.metrics.trades, test_buy_hold_pct=round(res.metrics.buy_hold_return_pct, 2),
        ))
    result.wfo = _aggregate(oos_results, bars_per_year, risk_cfg.starting_equity_usdt)

    # --- tüm veri sıralaması (canlı kullanım için seçim) + bilgi amaçlı son %30 -----
    split = int(n * bt_cfg.train_ratio)
    rows = []
    for s in specs:
        full = run_backtest(df, sigs[s.label], **common).metrics
        last = run_backtest(df.iloc[split:], sigs[s.label].iloc[split:], **common).metrics
        rows.append(SweepRow(s, full, last))
    ok = [r for r in rows if r.train.trades >= bt_cfg.min_trades]
    ok.sort(key=lambda r: (r.train.sharpe, r.train.profit_factor), reverse=True)
    rest = [r for r in rows if r.train.trades < bt_cfg.min_trades]
    rest.sort(key=lambda r: r.train.sharpe, reverse=True)
    result.rows = ok + rest
    if not ok:
        result.edge_note = f"Hiçbir konfigürasyon {bt_cfg.min_trades}+ işlem üretmedi"
        return result
    result.best = ok[0]

    w = result.wfo
    if w is None or w.trades == 0:
        result.edge_note = "Walk-forward OOS'ta işlem oluşmadı"
        return result
    ok_sharpe = w.sharpe >= bt_cfg.min_oos_sharpe
    ok_pf = w.profit_factor >= bt_cfg.min_oos_profit_factor
    ok_n = w.trades >= bt_cfg.min_oos_trades
    result.has_edge = bool(ok_sharpe and ok_pf and ok_n)
    tag = "✅" if result.has_edge else "❌"
    result.edge_note = (f"{tag} WFO OOS ({len(result.folds)} adım): Sharpe {w.sharpe:.2f} "
                        f"{'≥' if ok_sharpe else '<'} {bt_cfg.min_oos_sharpe}, PF {w.profit_factor:.2f} "
                        f"{'≥' if ok_pf else '<'} {bt_cfg.min_oos_profit_factor}, {w.trades} işlem, "
                        f"getiri {w.total_return_pct:+.1f}% vs B&H {w.buy_hold_return_pct:+.1f}%")
    return result
