"""Ajan orkestrasyonu: veri (TradingView çoklu zaman dilimi + Binance canlı) → uzman ajanlar → coin yöneticisi → baş yönetici."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .. import indicators as ind
from ..config import BotConfig
from ..data import MarketData, drop_unclosed_last_bar
from .base import CoinContext
from .manager import ChiefAgent, ChiefBrief, CoinBrief, CoinManagerAgent
from .market import BinanceLive, MarketDataAgent
from .technical import TECHNICAL_AGENTS

log = logging.getLogger(__name__)

FRAME_SPECS = {"1d": 420, "4h": 730, "1h": 30}   # zaman dilimi → geriye dönük gün


class AgentRunner:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.markets = {tf: MarketData(cfg.exchange.candidates, tf, days, cfg.cache_path,
                                       source=cfg.exchange.source, tv_exchange=cfg.exchange.tv_exchange)
                        for tf, days in FRAME_SPECS.items()}
        self.live = BinanceLive(ttl_sec=60)
        self.agents = TECHNICAL_AGENTS + [MarketDataAgent()]
        self.manager = CoinManagerAgent()
        self.chief = ChiefAgent(max_concurrent=cfg.risk.max_open_positions)
        self.last_frames: dict[str, dict] = {}

    def set_weights(self, weights: dict | None) -> None:
        self.manager = CoinManagerAgent(weights or None)

    def _frames(self, symbol: str, prefetched: dict | None = None) -> dict:
        """prefetched: {"1d": df, "4h": df, "1h": df} (ham OHLCV) — verilenler kullanılır, eksikler TradingView/ccxt'den çekilir."""
        frames = {}
        prefetched = prefetched or {}
        for tf, md in self.markets.items():
            try:
                df = prefetched.get(tf)
                if df is None:
                    df = md.fetch(symbol)
                df = drop_unclosed_last_bar(df, tf)
                frames[tf] = ind.add_snapshot_indicators(df)
            except Exception as exc:  # noqa: BLE001
                log.warning("%s %s verisi alınamadı: %s", symbol, tf, exc)
        return frames

    def run_symbol(self, symbol: str, analysis=None, prefetched: dict | None = None) -> CoinBrief:
        t0 = time.time()
        frames = self._frames(symbol, prefetched)
        live = self.live.snapshot(symbol)
        ctx = CoinContext(symbol=symbol, frames=frames, live=live, analysis=analysis,
                          equity_usdt=self.cfg.risk.starting_equity_usdt, risk_pct=self.cfg.risk.risk_per_trade_pct,
                          atr_stop_mult=self.cfg.risk.atr_stop_mult)
        reports = [a.run(ctx) for a in self.agents]
        brief = self.manager.decide(ctx, reports)
        brief.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        h4 = ctx.frame("4h")
        if h4 is not None:
            brief.last_close_4h = float(h4["close"].iloc[-1])
            brief.last_bar_4h = str(h4.index[-1])
        self.last_frames[symbol] = frames
        log.info("%s ajanlar: %s (kanaat %d) %.1fs", symbol, brief.verdict, brief.conviction, time.time() - t0)
        return brief

    def run_all(self, symbols: list[str], analyses: dict | None = None, prefetched: dict | None = None) -> tuple[list[CoinBrief], ChiefBrief]:
        analyses = analyses or {}
        prefetched = prefetched or {}
        briefs = [self.run_symbol(s, analyses.get(s), prefetched.get(s)) for s in symbols]
        chief = self.chief.decide(briefs)
        chief.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return briefs, chief


def persist_agents(briefs: list[CoinBrief], chief: ChiefBrief, state_dir: Path) -> tuple[Path, list[str]]:
    """state/agents.json yazar; önceki verdictlerle karşılaştırıp değişiklik (alarm) listesi döner."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "agents.json"
    prev = {}
    if path.exists():
        try:
            prev = {b["symbol"]: b for b in json.loads(path.read_text(encoding="utf-8")).get("briefs", [])}
        except Exception:  # noqa: BLE001
            prev = {}
    alerts = []
    for b in briefs:
        p = prev.get(b.symbol)
        if p and p.get("verdict") != b.verdict:
            alerts.append(f"{b.symbol}: {p.get('verdict')} → {b.verdict} (kanaat %{b.conviction}) — {b.headline}")
        elif p and p.get("plan", {}).get("valid") != b.plan.valid and b.plan.valid:
            alerts.append(f"{b.symbol}: plan GEÇERLİ oldu — {b.plan.trigger_text} (R/R {b.plan.rr})")
    data = {"generated_at": chief.generated_at, "chief": chief.to_dict(), "briefs": [b.to_dict() for b in briefs]}
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    if alerts:
        with open(state_dir / "alerts.log", "a", encoding="utf-8") as fh:
            for a in alerts:
                fh.write(f"{chief.generated_at} {a}\n")
    return path, alerts
