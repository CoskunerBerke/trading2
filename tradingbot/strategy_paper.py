# -*- coding: utf-8 -*-
"""STRATEJİ KÂĞIT DEFTERİ — tek kurallı stratejinin uygulanması; canlı motor ile replay ORTAK yol (V10).

`apply_action` iki motorda da AYNI: boyut = profil işlem riski / stop mesafesi, `risk.evaluate`
(toplam risk tavanı, kaldıraç, cluster), `ledger.open` (gerçek filtre, kayma), `close_manual` (kayma).
Stop/hedef/funding/likidasyon/başa-baş: defterin kendi `tick`i.

`StrategyBook` canlı motorun AYRI kâğıt defteridir: kendi state dizini, kendi trade memory, kendi
özet dosyası. Ana botun defterine, öğrenicisine ve kararlarına DOKUNMAZ. Gerçek para YOK.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .accounting import (AmountType, FeeSchedule, FuturesLedgerV2, LiquidationParams, MarketType, SizeSpec,
                         SlippageModel, TaxPolicy, TickData, default_brackets)
from .candle_confirmation import closed_bars
from .core import atomic_write_json, iso, utc_now
from .learn import TradeMemory
from .regime_gate import BTC_SYMBOL
from .risk import RiskEngine, build_state, enforces_position_cap
from .ema200_trend import VARIANTS, daily_rows_from_frame, decide

log = logging.getLogger(__name__)
SUMMARY_FILE = "strategy_paper.json"
INDEX_FILE = "strategy_paper_index.json"
SCHEMA_VERSION = "strategy_paper_v1"


class BookSpec:
    """Bir defterin ayarları — ana bölüm ya da `extra` listesindeki sözlük, TEK biçime indirgenir."""

    def __init__(self, *, name: str, starting_equity_usdt: float = 100.0, atr_mult: float = 3.0,
                 breakeven_at_mfe_r: float = 0.0, state_dir: str = "strategy_paper", symbols=None, enabled: bool = True):
        self.name, self.starting_equity_usdt, self.atr_mult = str(name), float(starting_equity_usdt), float(atr_mult)
        self.breakeven_at_mfe_r, self.state_dir = float(breakeven_at_mfe_r), str(state_dir)
        self.symbols, self.enabled = list(symbols or []), bool(enabled)

    @classmethod
    def from_section(cls, sp) -> "BookSpec":
        return cls(name=sp.name, starting_equity_usdt=sp.starting_equity_usdt, atr_mult=sp.atr_mult,
                   breakeven_at_mfe_r=sp.breakeven_at_mfe_r, state_dir=sp.state_dir, symbols=sp.symbols, enabled=sp.enabled)

    @classmethod
    def from_dict(cls, d: dict) -> "BookSpec":
        allowed = {"name", "starting_equity_usdt", "atr_mult", "breakeven_at_mfe_r", "state_dir", "symbols", "enabled"}
        return cls(**{k: v for k, v in dict(d).items() if k in allowed})

    @property
    def summary_file(self) -> str:
        return SUMMARY_FILE if self.state_dir == "strategy_paper" else "%s.json" % self.state_dir


def book_specs(v3) -> list["BookSpec"]:
    """Config'ten etkin defter listesi: ana bölüm (enabled ise) + extra (her biri enabled ise)."""
    sp = v3.strategy_paper
    out: list[BookSpec] = []
    if bool(getattr(sp, "enabled", False)):
        out.append(BookSpec.from_section(sp))
    for ex in list(getattr(sp, "extra", None) or []):
        b = BookSpec.from_dict(ex)
        if b.enabled:
            out.append(b)
    return out


def apply_action(act: dict[str, Any] | None, *, symbol: str, price: float, tick: TickData | None, now: datetime,
                 ledger: FuturesLedgerV2, risk: RiskEngine, profile, state, filters, run_id: str,
                 reject: Callable[[str, str], None], on_closed: Callable[[Any], None],
                 on_opened: Callable[[Any, dict[str, Any]], None] | None = None) -> str:
    """Stratejinin kararını uygular. Döner: OPENED | CLOSED | REJECTED | NONE. İki motor da bunu çağırır."""
    if not act:
        return "NONE"
    action = str(act.get("action") or "").upper()
    pos = ledger.positions.get(symbol)
    if action == "CLOSE":
        if pos is None:
            return "NONE"
        rec = ledger.close_manual(symbol, price, reason=str(act.get("reason") or "STRATEGY_EXIT"), now=now, tick=tick)
        if rec is None:
            return "NONE"
        on_closed(rec)
        return "CLOSED"
    if action != "OPEN" or pos is not None:
        return "NONE"
    direction = str(act.get("direction") or "LONG").upper()
    entry = float(price)
    stop = float(act.get("stop") or 0.0)
    if entry <= 0 or stop <= 0 or (direction == "LONG" and stop >= entry) or (direction == "SHORT" and stop <= entry):
        reject(symbol, "STRATEGY_BAD_STOP")
        return "REJECTED"
    stop_frac = abs(entry - stop) / entry
    risk_usdt = float(profile.risk_per_trade_pct) / 100.0 * float(state.equity)
    notional = risk_usdt / stop_frac
    lev = int(act.get("leverage") or 1)
    plan_dict = {"symbol": symbol, "market_type": "USDM_PERP", "direction": direction, "entry": entry, "stop": stop,
                 "targets": list(act.get("targets") or []), "notional": notional, "margin": notional / max(1, lev),
                 "leverage": lev, "amount_type": "NOTIONAL", "expected_r": float(act.get("expected_r") or 0.0),
                 "min_notional": 5.0}
    rd = risk.evaluate(plan_dict, state, {"now_utc": now})
    if not rd.allowed:
        reject(symbol, (rd.reasons or ["RISK_DENIED"])[0])
        return "REJECTED"
    notional = float(rd.adjusted_notional or notional)
    if notional <= 0:
        reject(symbol, "ZERO_NOTIONAL")
        return "REJECTED"
    pos = ledger.open(symbol, direction, entry, SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, int(rd.adjusted_leverage or lev)),
                      filters=filters, stop=stop, targets=list(act.get("targets") or []),
                      setup_type=str(act.get("setup_type") or "strategy"), trigger_text=str(act.get("reason") or ""),
                      features={"regime": act.get("regime"), "market_type": "USDM_PERP", "strategy": act.get("name"),
                                "expected_r": float(act.get("expected_r") or 0.0), "p_win": None},
                      tick=tick, now=now, meta={"run_id": run_id, "strategy": str(act.get("name") or "")})
    if pos is None:
        reject(symbol, ledger.last_reject_reason or "LEDGER_REJECT")
        return "REJECTED"
    if on_opened is not None:
        on_opened(pos, act)
    return "OPENED"


class StrategyBook:
    """Canlı motorda tek kurallı stratejinin AYRI kâğıt defteri (ileri test)."""

    def __init__(self, cfg, *, profile, killswitch, filters_cache, run_id: str, spec: "BookSpec | None" = None):
        v3 = cfg.v3
        sp = spec or BookSpec.from_section(v3.strategy_paper)
        self.spec = sp
        self.key = sp.state_dir
        self.summary_file = sp.summary_file
        self.cfg, self.profile, self.filters_cache, self.run_id = cfg, profile, filters_cache, run_id
        self.name = str(sp.name)
        self.atr_mult = float(sp.atr_mult)
        self.symbols: list[str] = list(sp.symbols) if sp.symbols else []
        self.state_dir = Path(cfg.state_path) / str(sp.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.state_dir / "futures_ledger.json"
        fees = FeeSchedule(maker_pct=Decimal(str(v3.fees.futures_maker_pct)), taker_pct=Decimal(str(v3.fees.futures_taker_pct)), source=v3.fees.source)
        slip = SlippageModel(fixed_bps=Decimal(str(v3.fees.slippage_bps)))
        self.ledger = FuturesLedgerV2.load(self.ledger_path, starting_equity=float(sp.starting_equity_usdt),
                                          max_positions=cfg.futures.max_positions,
                                          enforce_position_cap=enforces_position_cap(profile),
                                          fees=fees, slippage=slip, brackets=default_brackets(),
                                          liq_params=LiquidationParams(liq_fee_pct=Decimal(str(v3.futures_v3.liq_fee_pct))),
                                          tp1_fraction=Decimal(str(v3.futures_v3.tp1_fraction)),
                                          breakeven_at_mfe_r=Decimal(str(sp.breakeven_at_mfe_r)),
                                          tax_policy=TaxPolicy.disabled())
        self.risk = RiskEngine(profile, killswitch, v3.risk_profiles.clusters or None)
        self.memory = TradeMemory(self.state_dir / "trade_memory.jsonl", source="STRATEGY_PAPER")
        self.lock = threading.RLock()
        self.last_actions: dict[str, dict[str, Any]] = {}
        self.counters = {"opened": 0, "closed": 0, "rejected": 0, "tours": 0}
        self.rejections: dict[str, int] = {}
        self.regime: str | None = None
        self.closed_recent: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ yardımcılar
    def _state(self, marks_f: dict[str, float]):
        pos = [{"symbol": s, "market_type": "USDM_PERP", "side": p.side.value, "notional": float(p.qty * p.entry_avg),
                "margin": float(p.isolated_margin), "entry": float(p.entry_avg), "stop": float(p.stop) if p.stop else None,
                "leverage": p.leverage, "liq_price": float(p.liquidation_price) if p.liquidation_price else None,
                "opened_at": p.opened_at} for s, p in self.ledger.positions.items()]
        fs = self.ledger.summary(marks_f)
        return build_state(equity=float(fs["equity_mtm"]), starting_equity=float(self.ledger.starting_equity),
                           available=float(fs["available"]), used_margin=float(fs["used_margin"]), positions=pos,
                           history=self.ledger.history_dicts(), high_water_mark=0.0, now=utc_now(),
                           clusters=self.cfg.v3.risk_profiles.clusters or None)

    def _reject(self, symbol: str, reason: str) -> None:
        self.counters["rejected"] += 1
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        self.last_actions[symbol] = {"action": "REJECTED", "reason": reason, "at": iso(utc_now())}

    def _on_closed(self, rec) -> None:
        self.counters["closed"] += 1
        d = rec.to_legacy_dict() if hasattr(rec, "to_legacy_dict") else {}
        self.closed_recent.append({"id": getattr(rec, "id", None), "symbol": getattr(rec, "symbol", None),
                                   "exit_reason": getattr(rec, "exit_reason", None),
                                   "net_pnl": float(getattr(rec, "net_pnl", 0) or 0), "r": float(getattr(rec, "r_multiple", 0) or 0),
                                   "closed_at": getattr(rec, "closed_at", None), "features": d.get("features")})
        self.closed_recent = self.closed_recent[-50:]

    def _on_opened(self, pos, act: dict[str, Any]) -> None:
        self.counters["opened"] += 1
        try:
            self.memory.record_entry({"trade_id": pos.id, "symbol": pos.symbol, "direction": pos.side.value, "market_type": "USDM_PERP",
                                      "setup_type": "trend", "regime": act.get("regime"),
                                      "features": {"strategy": act.get("name"), "signal_close": act.get("signal_close"),
                                                   "ema200": act.get("ema200"), "atr14": act.get("atr14")},
                                      "run_id": self.run_id, "in_test": True})
        except Exception as exc:  # noqa: BLE001 — bellek arızası işlemi ETKİLEMEZ
            log.warning("strateji bellek kaydı yazılamadı (%s): %s", pos.symbol, exc)

    # ------------------------------------------------------------------ tur
    def step(self, *, symbols: list[str], frames_by_symbol: dict[str, dict], marks: dict[str, TickData],
             marks_f: dict[str, float], now: datetime) -> None:
        """Her turda: kural → apply_action. Ana bot dokunulmaz; yalnız bu defter değişir."""
        with self.lock:
            self.counters["tours"] += 1
            now_ms = int(now.timestamp() * 1000)
            btc = closed_bars(daily_rows_from_frame((frames_by_symbol.get(BTC_SYMBOL) or {}).get("1d")), now_ms=now_ms, tf="1d")
            from .regime_gate import btc_regime
            self.regime = btc_regime(btc)
            state = self._state(marks_f)
            for sym in symbols:
                if sym not in marks_f:
                    continue
                d1 = closed_bars(daily_rows_from_frame((frames_by_symbol.get(sym) or {}).get("1d")), now_ms=now_ms, tf="1d")
                try:
                    act = decide(self.name, daily_rows=d1, btc_daily_rows=btc,
                                 position_open=sym in self.ledger.positions, atr_mult=self.atr_mult)
                except Exception as exc:  # noqa: BLE001 — strateji arızası SESSİZ GEÇMEZ
                    self._reject(sym, "STRATEGY_ERROR:%s" % type(exc).__name__)
                    continue
                res = apply_action(act, symbol=sym, price=float(marks_f[sym]), tick=marks.get(sym), now=now,
                                   ledger=self.ledger, risk=self.risk, profile=self.profile, state=state,
                                   filters=self.filters_cache.get(sym, MarketType.USDM_PERP), run_id=self.run_id,
                                   reject=self._reject, on_closed=self._on_closed, on_opened=self._on_opened)
                if res in ("OPENED", "CLOSED"):
                    self.last_actions[sym] = {"action": res, "reason": (act or {}).get("reason"), "at": iso(now)}
                    state = self._state(marks_f)
                elif act is None and sym not in self.last_actions:
                    self.last_actions[sym] = {"action": "NONE", "reason": "NO_SIGNAL", "at": iso(now)}

    def tick(self, marks: dict[str, TickData], *, now: datetime, funding_rate_lookup=None, bar_advance: bool) -> list:
        """Defterin stop/hedef/funding/likidasyon kontrolü; ana defterle AYNI çağrı biçimi."""
        with self.lock:
            recs = self.ledger.tick(marks, now_utc=now, funding_rate_lookup=funding_rate_lookup, bar_advance=bar_advance)
            for rec in recs:
                self._on_closed(rec)
            return recs

    def save(self, marks_f: dict[str, float], now: datetime) -> None:
        with self.lock:
            self.ledger.save(self.ledger_path)
            fs = self.ledger.summary(marks_f)
            doc = {"schema_version": SCHEMA_VERSION, "generated_at": iso(now), "run_id": self.run_id,
                   "name": self.name, "atr_mult": self.atr_mult, "regime": self.regime,
                   "starting_equity": float(self.ledger.starting_equity),
                   "summary": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in fs.items()},
                   "positions": {s: {"side": p.side.value, "entry": float(p.entry_avg), "qty": float(p.qty),
                                     "stop": float(p.stop) if p.stop else None, "leverage": p.leverage,
                                     "opened_at": p.opened_at, "last_price": float(p.last_price) if p.last_price else None}
                                 for s, p in self.ledger.positions.items()},
                   "history_tail": self.ledger.history_dicts()[-50:],
                   "last_actions": self.last_actions, "counters": dict(self.counters),
                   "rejections": dict(self.rejections), "closed_recent": self.closed_recent[-20:],
                   "note_tr": "KÂĞIT İLERİ TEST — gerçek para yok. Ana botun defterinden bağımsız."}
            doc["key"] = self.key
            atomic_write_json(Path(self.cfg.state_path) / self.summary_file, doc)


def validate_settings(*, enabled: bool, name: str | None, app_mode: str | None, starting_equity: float,
                      atr_mult: float) -> None:
    """Config doğrulaması (SAF). Gerçek parayla (LIVE) etkinleştirilemez."""
    if name not in VARIANTS:
        raise ValueError("strategy_paper.name gecersiz: %r (gecerli: %s)" % (name, ", ".join(VARIANTS)))
    if enabled and str(app_mode or "").upper() in ("LIVE", "LIVE_LIMITED"):
        raise ValueError("STRATEGY_PAPER_IS_PAPER_ONLY: strategy_paper.enabled yalniz PAPER/TESTNET/OBSERVE/SHADOW_LIVE modda")
    if starting_equity <= 0 or atr_mult <= 0:
        raise ValueError("strategy_paper.starting_equity_usdt ve atr_mult pozitif olmali")


__all__ = ["INDEX_FILE", "SCHEMA_VERSION", "SUMMARY_FILE", "BookSpec", "StrategyBook", "apply_action",
           "book_specs", "validate_settings"]
