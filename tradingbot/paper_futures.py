"""FUTURES KAĞIT DEFTERİ — yönetici planlarını gerçek piyasa koşullarında simüle eder (gerçek emir YOK).

* Plan tetiklenince (4h kapanış seviyenin üstünde/altında) pozisyon açılır: yön, notional, kaldıraç, marj, stop, TP1/TP2.
* Her tur canlı fiyatla kontrol: stop → tam kapanış; TP1 → yarısı kapanır, stop başa-baş'a çekilir; TP2 → kalan kapanır.
* Komisyon (taker %0.05 futures) + kayma + yaklaşık funding (8 saatte bir) düşülür.
* Likidasyon yaklaşık: marj / notional mesafesi aşılırsa (izole marj varsayımı) → kapanış.
* Her kapanış bir TradeRecord olarak journal'a gider → öğrenme motoru.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

FUT_FEE_PCT = 0.05     # taker (%)
SLIP_PCT = 0.03


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class FuturesPosition:
    id: str
    symbol: str
    side: str                 # LONG / SHORT
    entry: float
    units: float
    notional: float
    leverage: int
    margin: float
    stop: float
    target1: float
    target2: float
    opened_at: str
    setup_type: str = ""
    trigger_text: str = ""
    features: dict = field(default_factory=dict)      # giriş anındaki ajan/piyasa özellikleri (öğrenme için)
    tp1_done: bool = False
    realized: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    mae_pct: float = 0.0      # en kötü aleyhte hareket
    mfe_pct: float = 0.0      # en iyi lehte hareket
    last_price: float = 0.0
    last_funding_at: str = ""
    bars_held: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def unrealized(self, price: float) -> float:
        d = (price - self.entry) if self.side == "LONG" else (self.entry - price)
        return d * self.units

    def liq_price(self) -> float:
        # izole marj yaklaşık: kayıp = marj*0.95 olduğunda
        move = self.margin * 0.95 / max(self.units, 1e-12)
        return self.entry - move if self.side == "LONG" else self.entry + move


@dataclass
class FuturesLedger:
    equity: float
    starting_equity: float
    positions: dict[str, FuturesPosition] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    updated_at: str = ""
    max_positions: int = 3
    total_fees: float = 0.0
    seq: int = 0

    # ------------------------------------------------------------ kalıcılık
    @classmethod
    def load(cls, path: Path, starting_equity: float, max_positions: int = 3) -> "FuturesLedger":
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            led = cls(equity=float(d["equity"]), starting_equity=float(d.get("starting_equity", starting_equity)), max_positions=max_positions)
            led.positions = {k: FuturesPosition(**v) for k, v in d.get("positions", {}).items()}
            led.history = list(d.get("history", []))
            led.total_fees = float(d.get("total_fees", 0.0))
            led.seq = int(d.get("seq", 0))
            return led
        return cls(equity=starting_equity, starting_equity=starting_equity, max_positions=max_positions)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = _now()
        from .core import atomic_write_json
        atomic_write_json(path, {"equity": round(self.equity, 6), "starting_equity": self.starting_equity, "updated_at": self.updated_at,
                                 "positions": {k: v.to_dict() for k, v in self.positions.items()}, "history": self.history[-1000:],
                                 "total_fees": round(self.total_fees, 6), "seq": self.seq}, keep_backup=True)

    # ------------------------------------------------------------ açılış
    def can_open(self, symbol: str) -> tuple[bool, str]:
        if symbol in self.positions:
            return False, "zaten pozisyon var"
        if len(self.positions) >= self.max_positions:
            return False, f"max pozisyon ({self.max_positions}) dolu"
        used = sum(p.margin for p in self.positions.values())
        if self.equity - used < 5:
            return False, "serbest marj < 5 USDT"
        return True, "ok"

    def open(self, symbol: str, side: str, price: float, notional: float, leverage: int, stop: float, target1: float, target2: float,
             setup_type: str = "", trigger_text: str = "", features: dict | None = None, amount_step: float = 0.0) -> FuturesPosition | None:
        ok, why = self.can_open(symbol)
        if not ok:
            return None
        used = sum(p.margin for p in self.positions.values())
        free = self.equity - used
        margin = notional / max(leverage, 1)
        if margin > free * 0.98:
            margin = free * 0.98
            notional = margin * leverage
        if notional < 5:
            return None
        fill = price * (1 + SLIP_PCT / 100) if side == "LONG" else price * (1 - SLIP_PCT / 100)
        units = notional / fill
        if amount_step > 0:
            units = int(units / amount_step) * amount_step
            notional = units * fill
        if units <= 0 or notional < 5:
            return None
        fee = notional * FUT_FEE_PCT / 100
        self.equity -= fee
        self.total_fees += fee
        self.seq += 1
        pos = FuturesPosition(id=f"F{self.seq:05d}", symbol=symbol, side=side, entry=fill, units=units, notional=notional, leverage=leverage,
                              margin=margin, stop=stop, target1=target1, target2=target2, opened_at=_now(), setup_type=setup_type,
                              trigger_text=trigger_text, features=features or {}, fees=fee, last_price=fill, last_funding_at=_now())
        self.positions[symbol] = pos
        return pos

    # ------------------------------------------------------------ tik / kapanış
    def _close_part(self, pos: FuturesPosition, price: float, frac: float, reason: str) -> float:
        fill = price * (1 - SLIP_PCT / 100) if pos.side == "LONG" else price * (1 + SLIP_PCT / 100)
        u = pos.units * frac
        pnl = ((fill - pos.entry) if pos.side == "LONG" else (pos.entry - fill)) * u
        fee = u * fill * FUT_FEE_PCT / 100
        pos.units -= u
        pos.notional = pos.units * pos.entry
        pos.margin = pos.notional / max(pos.leverage, 1)
        pos.realized += pnl - fee
        pos.fees += fee
        self.equity += pnl - fee
        self.total_fees += fee
        return pnl - fee

    def tick(self, prices: dict[str, float], funding: dict[str, float] | None = None, bar_advance: bool = False) -> list[dict]:
        """Canlı fiyatlarla stop/TP kontrolü. Dönen: kapanan işlemlerin kayıtları (TradeRecord üretimi için)."""
        events: list[dict] = []
        funding = funding or {}
        for sym in list(self.positions):
            pos = self.positions[sym]
            px = prices.get(sym)
            if not px:
                continue
            pos.last_price = px
            if bar_advance:
                pos.bars_held += 1
            move = (px / pos.entry - 1) * 100 * (1 if pos.side == "LONG" else -1)
            pos.mfe_pct = max(pos.mfe_pct, move)
            pos.mae_pct = min(pos.mae_pct, move)
            # funding (8 saatte bir yaklaşık): long öder (rate>0), short alır
            fr = funding.get(sym)
            if fr is not None:
                try:
                    last = datetime.fromisoformat(pos.last_funding_at)
                    if (datetime.now(timezone.utc) - last).total_seconds() >= 8 * 3600:
                        cost = pos.units * px * fr * (1 if pos.side == "LONG" else -1)
                        pos.funding -= cost
                        self.equity -= cost
                        pos.last_funding_at = _now()
                except ValueError:
                    pos.last_funding_at = _now()
            hit_stop = px <= pos.stop if pos.side == "LONG" else px >= pos.stop
            hit_liq = px <= pos.liq_price() if pos.side == "LONG" else px >= pos.liq_price()
            hit_t1 = (px >= pos.target1 if pos.side == "LONG" else px <= pos.target1) and not pos.tp1_done
            hit_t2 = px >= pos.target2 if pos.side == "LONG" else px <= pos.target2
            if hit_liq:
                self._close_part(pos, pos.liq_price(), 1.0, "likidasyon")
                events.append(self._finalize(pos, "likidasyon"))
            elif hit_stop:
                self._close_part(pos, pos.stop, 1.0, "stop" if not pos.tp1_done else "başa-baş stop")
                events.append(self._finalize(pos, "stop" if not pos.tp1_done else "başa-baş stop"))
            elif hit_t2:
                self._close_part(pos, pos.target2, 1.0, "hedef2")
                events.append(self._finalize(pos, "hedef2"))
            elif hit_t1:
                self._close_part(pos, pos.target1, 0.5, "hedef1")
                pos.tp1_done = True
                pos.stop = pos.entry   # başa-baş
        return events

    def close_manual(self, symbol: str, price: float, reason: str) -> dict | None:
        pos = self.positions.get(symbol)
        if not pos:
            return None
        self._close_part(pos, price, 1.0, reason)
        return self._finalize(pos, reason)

    def _finalize(self, pos: FuturesPosition, reason: str) -> dict:
        self.positions.pop(pos.symbol, None)
        risk_per_unit = abs(pos.entry - (pos.features.get("initial_stop") or pos.stop)) or 1e-9
        init_units = pos.features.get("initial_units") or (pos.notional / pos.entry if pos.entry else 1)
        risk_usdt = risk_per_unit * float(init_units)
        rec = {
            "id": pos.id, "symbol": pos.symbol, "side": pos.side, "entry": pos.entry, "exit_reason": reason, "closed_at": _now(),
            "opened_at": pos.opened_at, "pnl": round(pos.realized + pos.funding, 6), "fees": round(pos.fees, 6), "funding": round(pos.funding, 6),
            "r_multiple": round((pos.realized + pos.funding) / risk_usdt, 3) if risk_usdt else 0.0,
            "mae_pct": round(pos.mae_pct, 3), "mfe_pct": round(pos.mfe_pct, 3), "bars_held": pos.bars_held, "leverage": pos.leverage,
            "setup_type": pos.setup_type, "trigger_text": pos.trigger_text, "features": pos.features, "tp1_done": pos.tp1_done,
        }
        self.history.append(rec)
        return rec

    # ------------------------------------------------------------ özet
    def summary(self, prices: dict[str, float] | None = None) -> dict:
        prices = prices or {}
        unreal = sum(p.unrealized(prices.get(s, p.last_price)) for s, p in self.positions.items())
        closed = self.history
        wins = [h for h in closed if h["pnl"] > 0]
        return {"equity": round(self.equity, 4), "unrealized": round(unreal, 4), "equity_mtm": round(self.equity + unreal, 4),
                "starting_equity": self.starting_equity, "return_pct": round((self.equity + unreal) / self.starting_equity * 100 - 100, 2),
                "open": len(self.positions), "closed": len(closed), "win_rate": round(100 * len(wins) / len(closed), 1) if closed else 0.0,
                "avg_r": round(sum(h["r_multiple"] for h in closed) / len(closed), 3) if closed else 0.0,
                "total_fees": round(self.total_fees, 4), "used_margin": round(sum(p.margin for p in self.positions.values()), 4)}
