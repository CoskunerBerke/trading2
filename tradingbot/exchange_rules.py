"""Binance spot emir kuralları — kağıt işlemleri gerçek borsa gibi kısıtlamak için.

ccxt'nin public market bilgisinden (API key gerekmez) her sembol için:
  * min_cost   : minimum emir tutarı (USDT)  — Binance'te tipik 5 USDT (bazı çiftlerde 1)
  * amount_step: adet adım hassasiyeti (LOT_SIZE)
  * price_tick : fiyat adımı
  * taker_fee  : komisyon (%0.10)
Kurallar `data/exchange_rules.json`'a önbelleklenir; ağ yoksa önbellek/varsayılan kullanılır.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, asdict
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_RULE = {"min_cost": 5.0, "amount_step": 1e-5, "price_tick": 0.01, "taker_fee_pct": 0.10, "min_amount": 1e-5}


@dataclass
class SymbolRule:
    symbol: str
    min_cost: float
    amount_step: float
    price_tick: float
    taker_fee_pct: float
    min_amount: float

    def round_amount(self, units: float) -> float:
        """Adedi adım hassasiyetine AŞAĞI yuvarla (Binance LOT_SIZE gibi)."""
        if self.amount_step <= 0:
            return units
        steps = math.floor(units / self.amount_step + 1e-12)
        return round(steps * self.amount_step, 10)

    def round_price(self, price: float) -> float:
        if self.price_tick <= 0:
            return price
        return round(math.floor(price / self.price_tick + 1e-12) * self.price_tick, 10)

    def validate(self, units: float, price: float) -> tuple[bool, str]:
        if units < self.min_amount:
            return False, f"adet {units:g} < min {self.min_amount:g}"
        cost = units * price
        if cost < self.min_cost:
            return False, f"tutar {cost:.2f} USDT < min {self.min_cost:g} USDT"
        return True, "ok"


class ExchangeRules:
    def __init__(self, cache_file: Path, exchange_id: str = "binance"):
        self.cache_file = Path(cache_file)
        self.exchange_id = exchange_id
        self.rules: dict[str, SymbolRule] = {}
        self._loaded = False

    def load(self, symbols: list[str], refresh: bool = False) -> "ExchangeRules":
        cached: dict = {}
        if self.cache_file.exists() and not refresh:
            try:
                cached = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                cached = {}
        missing = [s for s in symbols if s not in cached]
        if missing or refresh:
            try:
                import ccxt
                ex = getattr(ccxt, self.exchange_id)({"enableRateLimit": True})
                markets = ex.load_markets()
                for s in symbols:
                    m = markets.get(s)
                    if not m:
                        continue
                    lim = m.get("limits", {}) or {}
                    prec = m.get("precision", {}) or {}
                    step = prec.get("amount")
                    tick = prec.get("price")
                    # ccxt precision: TICK_SIZE modunda doğrudan adım; DECIMAL_PLACES modunda basamak sayısı
                    if isinstance(step, (int, float)) and step >= 1 and float(step).is_integer() and ex.precisionMode != 4:
                        step = 10 ** (-int(step))
                    if isinstance(tick, (int, float)) and tick >= 1 and float(tick).is_integer() and ex.precisionMode != 4:
                        tick = 10 ** (-int(tick))
                    cached[s] = {
                        "min_cost": float((lim.get("cost") or {}).get("min") or DEFAULT_RULE["min_cost"]),
                        "amount_step": float(step or DEFAULT_RULE["amount_step"]),
                        "price_tick": float(tick or DEFAULT_RULE["price_tick"]),
                        "taker_fee_pct": float((m.get("taker") or 0.001) * 100),
                        "min_amount": float((lim.get("amount") or {}).get("min") or DEFAULT_RULE["min_amount"]),
                    }
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                self.cache_file.write_text(json.dumps(cached, indent=2), encoding="utf-8")
                log.info("Borsa kuralları güncellendi (%s): %d sembol", self.exchange_id, len(cached))
            except Exception as exc:  # noqa: BLE001
                log.warning("Borsa kuralları alınamadı, varsayılan/önbellek kullanılacak: %s", exc)
        for s in symbols:
            d = cached.get(s, DEFAULT_RULE)
            self.rules[s] = SymbolRule(symbol=s, **{k: float(d.get(k, DEFAULT_RULE[k])) for k in DEFAULT_RULE})
        self._loaded = True
        return self

    def get(self, symbol: str) -> SymbolRule:
        return self.rules.get(symbol) or SymbolRule(symbol=symbol, **DEFAULT_RULE)

    def to_dict(self) -> dict:
        return {k: asdict(v) for k, v in self.rules.items()}
