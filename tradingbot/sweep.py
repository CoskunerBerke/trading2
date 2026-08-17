"""Parametre taraması: her strateji konfigürasyonunu in-sample/out-of-sample test eder.

Seçim kuralı (aşırı-uyum/overfit'e karşı):
  * Sıralama in-sample Sharpe'a göre yapılır (min. işlem sayısı şartıyla).
  * Seçilen konfigürasyonun OOS (görülmemiş son %30) performansı ayrıca raporlanır.
  * OOS Sharpe / Profit Factor eşiklerin altındaysa coin "edge yok" (WATCH) sayılır.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .backtest import Metrics, run_backtest
from .config import BacktestConfig, RiskConfig
from .strategies import StrategySpec, generate_signals, iter_specs


@dataclass
class SweepRow:
    spec: StrategySpec
    train: Metrics
    test: Metrics

    def to_dict(self) -> dict:
        return {"spec": self.spec.to_dict(), "train": self.train.to_dict(), "test": self.test.to_dict()}


@dataclass
class SweepResult:
    symbol: str
    rows: list[SweepRow] = field(default_factory=list)
    best: SweepRow | None = None
    has_edge: bool = False
    edge_note: str = ""

    def top(self, k: int = 5) -> list[SweepRow]:
        return self.rows[:k]


def sweep_symbol(
    symbol: str,
    df: pd.DataFrame,
    *,
    bars_per_year: float,
    bt_cfg: BacktestConfig,
    risk_cfg: RiskConfig,
    families: list[str] | None = None,
) -> SweepResult:
    n = len(df)
    result = SweepResult(symbol=symbol)
    if n < 300:
        result.edge_note = f"Yetersiz veri ({n} bar)"
        return result

    split = int(n * bt_cfg.train_ratio)
    common = dict(
        bars_per_year=bars_per_year,
        fee_pct=bt_cfg.fee_pct,
        slippage_pct=bt_cfg.slippage_pct,
        risk_per_trade_pct=risk_cfg.risk_per_trade_pct,
        atr_stop_mult=risk_cfg.atr_stop_mult,
        max_position_pct=risk_cfg.max_position_pct,
        starting_equity=risk_cfg.starting_equity_usdt,
    )

    rows: list[SweepRow] = []
    for spec in iter_specs(families):
        sig = generate_signals(df, spec)  # göstergeler nedensel → tam seri üzerinde hesaplanabilir
        train = run_backtest(df.iloc[:split], sig.iloc[:split], **common).metrics
        test = run_backtest(df.iloc[split:], sig.iloc[split:], **common).metrics
        rows.append(SweepRow(spec, train, test))

    eligible = [r for r in rows if r.train.trades >= bt_cfg.min_trades]
    eligible.sort(key=lambda r: (r.train.sharpe, r.train.profit_factor), reverse=True)
    rest = [r for r in rows if r.train.trades < bt_cfg.min_trades]
    rest.sort(key=lambda r: r.train.sharpe, reverse=True)
    result.rows = eligible + rest

    if not eligible:
        result.edge_note = f"Hiçbir konfigürasyon {bt_cfg.min_trades}+ işlem üretmedi"
        return result

    best = eligible[0]
    result.best = best
    ok_sharpe = best.test.sharpe >= bt_cfg.min_oos_sharpe
    ok_pf = best.test.profit_factor >= bt_cfg.min_oos_profit_factor
    result.has_edge = bool(ok_sharpe and ok_pf and best.test.trades >= 3)
    if result.has_edge:
        result.edge_note = (
            f"OOS Sharpe {best.test.sharpe:.2f} ≥ {bt_cfg.min_oos_sharpe}, "
            f"PF {best.test.profit_factor:.2f} ≥ {bt_cfg.min_oos_profit_factor}"
        )
    else:
        result.edge_note = (
            f"OOS zayıf: Sharpe {best.test.sharpe:.2f}, PF {best.test.profit_factor:.2f}, "
            f"{best.test.trades} işlem"
        )
    return result
