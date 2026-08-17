"""Kağıt (paper) portföy — JSON'da tutulur. Gerçek borsa emri YOK."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Position:
    symbol: str
    units: float
    entry_price: float
    entry_time: str
    stop: float
    strategy: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Portfolio:
    cash: float
    starting_equity: float
    positions: dict[str, Position] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)  # kapanan işlemler
    updated_at: str = ""

    # ---- kalıcılık -----------------------------------------------------------
    @classmethod
    def load(cls, path: Path, starting_equity: float) -> "Portfolio":
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            p = cls(cash=float(data["cash"]), starting_equity=float(data.get("starting_equity", starting_equity)))
            p.positions = {k: Position(**v) for k, v in data.get("positions", {}).items()}
            p.history = list(data.get("history", []))
            p.updated_at = data.get("updated_at", "")
            return p
        return cls(cash=starting_equity, starting_equity=starting_equity)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data = {
            "cash": round(self.cash, 4),
            "starting_equity": self.starting_equity,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "history": self.history[-500:],
            "updated_at": self.updated_at,
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- değerleme -----------------------------------------------------------
    def equity(self, prices: dict[str, float]) -> float:
        val = self.cash
        for sym, pos in self.positions.items():
            val += pos.units * prices.get(sym, pos.entry_price)
        return val

    def open_pnl(self, prices: dict[str, float]) -> dict[str, float]:
        return {s: (prices.get(s, p.entry_price) - p.entry_price) * p.units for s, p in self.positions.items()}

    # ---- kağıt işlem ---------------------------------------------------------
    def paper_buy(self, symbol: str, units: float, price: float, stop: float, strategy: str, fee_pct: float, when: str) -> None:
        cost = units * price * (1 + fee_pct / 100)
        if cost > self.cash + 1e-9 or units <= 0:
            return
        self.cash -= cost
        self.positions[symbol] = Position(symbol, units, price, when, stop, strategy)

    def paper_sell(self, symbol: str, price: float, fee_pct: float, when: str, reason: str) -> float | None:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None
        proceeds = pos.units * price * (1 - fee_pct / 100)
        cost = pos.units * pos.entry_price * (1 + fee_pct / 100)
        pnl = proceeds - cost
        self.cash += proceeds
        self.history.append({
            "symbol": symbol, "entry_time": pos.entry_time, "exit_time": when,
            "entry_price": pos.entry_price, "exit_price": price, "units": pos.units,
            "pnl": round(pnl, 4), "pnl_pct": round(pnl / cost * 100, 3) if cost else 0.0,
            "reason": reason, "strategy": pos.strategy,
        })
        return pnl
