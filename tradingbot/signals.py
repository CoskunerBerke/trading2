"""SİNYAL düğümü — kararları AL/SAT çıktısına çevirir, kağıt portföyü günceller, JSON'a yazar."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from .analyzer import CoinAnalysis
from .config import BotConfig
from .decision import Decision, DecisionSummary
from .portfolio import Portfolio


@dataclass
class RunReport:
    run_time: str
    exchange: str
    timeframe: str
    summary: dict
    decisions: list[dict]
    analyses: list[dict]
    executed: list[dict] = field(default_factory=list)   # kağıt portföyde uygulananlar
    portfolio: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def apply_paper(decisions: list[Decision], portfolio: Portfolio, fee_pct: float, when: str) -> list[dict]:
    """AL/SAT kararlarını kağıt portföye işler (simülasyon)."""
    executed: list[dict] = []
    for d in decisions:
        if d.action == "SELL" and d.symbol in portfolio.positions:
            pnl = portfolio.paper_sell(d.symbol, d.price, fee_pct, when, d.reasons[0] if d.reasons else "")
            executed.append({"symbol": d.symbol, "side": "SELL", "price": d.price, "pnl": round(pnl or 0.0, 2)})
    for d in decisions:
        if d.action == "BUY" and d.units > 0 and d.symbol not in portfolio.positions:
            portfolio.paper_buy(d.symbol, d.units, d.price, d.stop or 0.0, d.strategy, fee_pct, when)
            executed.append({"symbol": d.symbol, "side": "BUY", "price": d.price, "units": d.units,
                             "notional": round(d.notional_usdt, 2), "stop": d.stop})
    return executed


def build_report(cfg: BotConfig, exchange: str, analyses: list[CoinAnalysis], decisions: list[Decision],
                 summary: DecisionSummary, executed: list[dict], portfolio: Portfolio, when: str) -> RunReport:
    prices = {a.symbol: a.price for a in analyses if a.price > 0}
    pf = {
        "cash": round(portfolio.cash, 2),
        "equity": round(portfolio.equity(prices), 2),
        "starting_equity": portfolio.starting_equity,
        "positions": {k: v.to_dict() for k, v in portfolio.positions.items()},
        "open_pnl": {k: round(v, 2) for k, v in portfolio.open_pnl(prices).items()},
        "closed_trades": len(portfolio.history),
        "realized_pnl": round(sum(h.get("pnl", 0.0) for h in portfolio.history), 2),
    }
    return RunReport(
        run_time=when, exchange=exchange, timeframe=cfg.exchange.timeframe, summary=summary.to_dict(),
        decisions=[d.to_dict() for d in decisions], analyses=[a.to_dict() for a in analyses],
        executed=executed, portfolio=pf,
    )


def persist(report: RunReport, state_dir: Path) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    latest = state_dir / "signals.json"
    latest.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log = state_dir / "signals_log.jsonl"
    slim = {"run_time": report.run_time, "summary": report.summary,
            "decisions": [{k: v for k, v in d.items() if k in ("symbol", "action", "confidence", "price", "units", "stop")} for d in report.decisions],
            "executed": report.executed, "equity": report.portfolio.get("equity")}
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(slim, ensure_ascii=False, default=str) + "\n")
    return latest


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def console_table(decisions: list[Decision], analyses: list[CoinAnalysis], summary: DecisionSummary) -> str:
    amap = {a.symbol: a for a in analyses}
    lines = []
    lines.append(f"Piyasa rejimi (BTC): {summary.market_regime}   Equity: {summary.equity:,.2f} USDT   Nakit: {summary.cash:,.2f}   Açık poz: {summary.open_positions}")
    lines.append(f"{'COIN':<10}{'KARAR':<7}{'GÜVEN':>6}{'FİYAT':>13}{'RSI':>6}{'REJİM':>10}{'OOS SHARPE':>12}{'STRATEJİ':<34}  GEREKÇE")
    lines.append("-" * 130)
    for d in decisions:
        a = amap.get(d.symbol)
        oos = a.test_metrics.get("sharpe", 0.0) if a else 0.0
        lines.append(
            f"{d.symbol:<10}{d.action_tr:<7}{d.confidence:>6}{d.price:>13.4g}{(a.rsi14 if a else 0):>6.0f}"
            f"{(a.regime if a else '-'):>10}{oos:>12.2f}  {(a.best_strategy if a else '')[:32]:<32}  {d.reasons[0] if d.reasons else ''}"
        )
    return "\n".join(lines)
