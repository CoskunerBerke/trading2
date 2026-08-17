"""Coin başına ANALİZ düğümü ("yuvarlak").

Her coin için:
  * gösterge anlık görüntüsü (fiyat, RSI, EMA'lar, ATR%, ADX, 24s değişim)
  * piyasa rejimi (YÜKSELİŞ / DÜŞÜŞ / YATAY)
  * parametre taraması → en iyi strateji + in-sample / out-of-sample metrikleri
  * stratejinin ŞU ANKİ durumu (LONG/FLAT, son barda giriş/çıkış sinyali var mı)
  * 0-100 arası analiz skoru
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict

import pandas as pd

from . import indicators as ind
from .backtest import run_backtest
from .config import BotConfig
from .data import bars_per_year, drop_unclosed_last_bar
from .strategies import FAMILY_TITLES, generate_signals
from .sweep import SweepResult, sweep_symbol

log = logging.getLogger(__name__)


@dataclass
class CoinAnalysis:
    symbol: str
    price: float = 0.0
    change_24h_pct: float = 0.0
    rsi14: float = 0.0
    ema20: float = 0.0
    ema50: float = 0.0
    ema200: float = 0.0
    atr14: float = 0.0
    atr_pct: float = 0.0
    adx14: float = 0.0
    regime: str = "YATAY"
    regime_score: int = 0            # -2..+2
    last_bar_time: str = ""
    bars: int = 0
    # strateji
    best_strategy: str = ""
    best_family_title: str = ""
    best_spec: dict = field(default_factory=dict)
    train_metrics: dict = field(default_factory=dict)   # tüm veri (in-sample)
    test_metrics: dict = field(default_factory=dict)    # walk-forward birleşik OOS
    full_metrics: dict = field(default_factory=dict)    # son %30 tek bölme (bilgi)
    wfo_folds: list[dict] = field(default_factory=list)
    has_edge: bool = False
    edge_note: str = ""
    min_notional: float = 0.0
    amount_step: float = 0.0
    strategy_position: str = "FLAT"  # LONG / FLAT
    entry_signal_now: bool = False
    exit_signal_now: bool = False
    bars_since_entry: int | None = None
    strategy_stop: float | None = None
    strategy_entry_price: float | None = None
    # skor
    score: int = 0
    signal: str = "WATCH"            # BUY / SELL / HOLD / WATCH
    reasons: list[str] = field(default_factory=list)
    top_configs: list[dict] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def base(self) -> str:
        return self.symbol.split("/")[0]


def classify_regime(price: float, e20: float, e50: float, e200: float, adx_v: float) -> tuple[str, int]:
    score = 0
    if price > e200:
        score += 1
    else:
        score -= 1
    if e20 > e50:
        score += 1
    else:
        score -= 1
    if adx_v < 18:
        return "YATAY", 0 if abs(score) < 2 else score // 2
    if score >= 1:
        return "YÜKSELİŞ", score
    if score <= -1:
        return "DÜŞÜŞ", score
    return "YATAY", 0


def analyze_symbol(symbol: str, raw: pd.DataFrame, cfg: BotConfig, *, now_ms: int | None = None,
                   families: list[str] | None = None, min_notional: float = 0.0, amount_step: float = 0.0) -> CoinAnalysis:
    a = CoinAnalysis(symbol=symbol, min_notional=min_notional, amount_step=amount_step)
    tf = cfg.exchange.timeframe
    df = drop_unclosed_last_bar(raw, tf, now_ms)
    a.bars = len(df)
    if len(df) < 250:
        a.error = f"Yetersiz veri: {len(df)} bar"
        a.reasons.append(a.error)
        return a

    snap = ind.add_snapshot_indicators(df)
    last = snap.iloc[-1]
    a.price = float(last["close"])
    a.last_bar_time = str(snap.index[-1])
    bars_24h = max(1, int(round(24 * 3600 * 1000 / (df["timestamp"].iloc[-1] - df["timestamp"].iloc[-2]))))
    if len(snap) > bars_24h:
        a.change_24h_pct = float((a.price / snap["close"].iloc[-1 - bars_24h] - 1.0) * 100.0)
    a.rsi14 = float(last["rsi14"])
    a.ema20, a.ema50, a.ema200 = float(last["ema20"]), float(last["ema50"]), float(last["ema200"])
    a.atr14 = float(last["atr14"])
    a.atr_pct = float(last["atr_pct"])
    a.adx14 = float(last["adx14"]) if pd.notna(last["adx14"]) else 0.0
    a.regime, a.regime_score = classify_regime(a.price, a.ema20, a.ema50, a.ema200, a.adx14)

    # --- tarama ---------------------------------------------------------------
    bpy = bars_per_year(tf)
    sw: SweepResult = sweep_symbol(symbol, df, bars_per_year=bpy, bt_cfg=cfg.backtest,
                                   risk_cfg=cfg.risk, families=families,
                                   min_notional=min_notional, amount_step=amount_step)
    a.has_edge = sw.has_edge
    a.edge_note = sw.edge_note
    a.top_configs = [r.to_dict() for r in sw.top(5)]
    a.wfo_folds = [f.to_dict() for f in sw.folds]
    if sw.wfo is not None:
        a.test_metrics = sw.wfo.to_dict()
    if sw.best is None:
        a.signal = "WATCH"
        a.reasons.append(sw.edge_note or "Uygun strateji yok")
        a.score = _score(a)
        return a

    spec = sw.best.spec
    a.best_strategy = spec.label
    a.best_family_title = FAMILY_TITLES.get(spec.family, spec.family)
    a.best_spec = spec.to_dict()
    a.train_metrics = sw.best.train.to_dict()
    a.full_metrics = sw.best.test.to_dict()

    sig = generate_signals(df, spec)
    full = run_backtest(
        df, sig, bars_per_year=bpy, fee_pct=cfg.backtest.fee_pct, slippage_pct=cfg.backtest.slippage_pct,
        risk_per_trade_pct=cfg.risk.risk_per_trade_pct, atr_stop_mult=cfg.risk.atr_stop_mult,
        max_position_pct=cfg.risk.max_position_pct, starting_equity=cfg.risk.starting_equity_usdt,
        min_notional=min_notional, amount_step=amount_step,
    )
    a.strategy_position = "LONG" if full.in_position_at_end else "FLAT"
    a.entry_signal_now = bool(sig["entries"].iloc[-1]) and not full.in_position_at_end
    a.exit_signal_now = bool(sig["exits"].iloc[-1]) and full.in_position_at_end
    a.bars_since_entry = full.open_bars_held if full.in_position_at_end else None
    a.strategy_stop = full.last_stop
    a.strategy_entry_price = full.open_entry_price

    # --- sinyal ---------------------------------------------------------------
    if a.entry_signal_now and a.has_edge:
        a.signal = "BUY"
        a.reasons.append(f"{a.best_family_title} son kapanan barda GİRİŞ sinyali verdi")
    elif a.exit_signal_now:
        a.signal = "SELL"
        a.reasons.append(f"{a.best_family_title} son barda ÇIKIŞ sinyali verdi")
    elif a.strategy_position == "LONG" and a.has_edge:
        a.signal = "HOLD"
        a.reasons.append(f"Strateji {a.bars_since_entry} bardır LONG (stop {a.strategy_stop:.4g})")
    elif a.entry_signal_now and not a.has_edge:
        a.signal = "WATCH"
        a.reasons.append("Giriş sinyali var ama OOS edge zayıf → sadece izle")
    else:
        a.signal = "WATCH"
        a.reasons.append("Sinyal yok" if a.has_edge else f"Edge yok: {a.edge_note}")

    a.reasons.append(f"Rejim: {a.regime} (EMA200 {'üstü' if a.price > a.ema200 else 'altı'}, ADX {a.adx14:.0f})")
    a.reasons.append(f"RSI14 {a.rsi14:.0f}, ATR %{a.atr_pct:.2f}")
    a.score = _score(a)
    return a


def _score(a: CoinAnalysis) -> int:
    """0-100: edge kalitesi (0-45) + rejim (0-25) + momentum/RSI (0-20) + volatilite (0-10)."""
    s = 0.0
    if a.has_edge:
        oos = float(a.test_metrics.get("sharpe", 0.0))
        s += 20 + min(25.0, max(0.0, oos) * 12.5)          # Sharpe 2.0 → tam puan
    else:
        s += 5
    s += {2: 25, 1: 18, 0: 10, -1: 4, -2: 0}.get(a.regime_score, 10)
    if 35 <= a.rsi14 <= 65:
        s += 20
    elif a.rsi14 < 35:
        s += 14
    elif a.rsi14 <= 75:
        s += 10
    else:
        s += 3
    if a.atr_pct <= 3:
        s += 10
    elif a.atr_pct <= 6:
        s += 6
    elif a.atr_pct <= 10:
        s += 3
    return int(max(0, min(100, round(s))))
