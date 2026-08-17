"""Ajan çerçevesi — her uzman ajan bir CoinContext alır, AgentReport döner.

bias      : -1.0 (güçlü SHORT/ayı) … 0 (nötr) … +1.0 (güçlü LONG/boğa)
confidence: 0-100, ajanın kendi bulgusuna güveni
levels    : ajanın çıkardığı fiyat seviyeleri (destek/direnç/EMA vs.)
warnings  : yöneticinin "YAPMA" listesine girecek uyarılar
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

import pandas as pd


@dataclass
class AgentReport:
    agent: str                      # kısa kod: volatility, trend, ...
    title: str                      # Türkçe başlık
    bias: float = 0.0
    confidence: int = 0
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    levels: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def stance_lower(self) -> str:
        return tr_lower(self.stance)

    @property
    def stance(self) -> str:
        if self.bias >= 0.5:
            return "GÜÇLÜ BOĞA"
        if self.bias >= 0.15:
            return "BOĞA"
        if self.bias <= -0.5:
            return "GÜÇLÜ AYI"
        if self.bias <= -0.15:
            return "AYI"
        return "NÖTR"


@dataclass
class CoinContext:
    symbol: str
    frames: dict[str, pd.DataFrame]          # {"1d": df, "4h": df, "1h": df} — kapanmış barlar, snapshot göstergeli
    live: dict[str, Any] = field(default_factory=dict)   # Binance canlı: ticker, orderbook, funding, oi, lsr
    analysis: Any = None                     # CoinAnalysis (WFO sonucu) — opsiyonel
    equity_usdt: float = 50.0
    risk_pct: float = 2.0
    atr_stop_mult: float = 2.5

    @property
    def base(self) -> str:
        return self.symbol.split("/")[0]

    def frame(self, tf: str) -> pd.DataFrame | None:
        df = self.frames.get(tf)
        return df if df is not None and len(df) > 50 else None

    @property
    def price(self) -> float:
        lv = self.live.get("ticker") or {}
        if lv.get("last"):
            return float(lv["last"])
        for tf in ("1h", "4h", "1d"):
            df = self.frame(tf)
            if df is not None:
                return float(df["close"].iloc[-1])
        return 0.0


class Agent:
    name = "base"
    title = "Ajan"
    weight = 1.0

    def run(self, ctx: CoinContext) -> AgentReport:
        rep = AgentReport(agent=self.name, title=self.title)
        try:
            self.analyze(ctx, rep)
            rep.bias = float(max(-1.0, min(1.0, rep.bias)))
            rep.confidence = int(max(0, min(100, rep.confidence)))
        except Exception as exc:  # noqa: BLE001
            rep.error = f"{type(exc).__name__}: {exc}"
            rep.summary = f"Ajan hata verdi: {rep.error}"
            rep.bias, rep.confidence = 0.0, 0
        return rep

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:  # pragma: no cover - soyut
        raise NotImplementedError


_TR_LOWER = str.maketrans("IİĞÜŞÖÇ", "ıiğüşöç")


def tr_lower(s: str) -> str:
    return s.translate(_TR_LOWER).lower()


def pct(a: float, b: float) -> float:
    """a'nın b'ye göre yüzde farkı."""
    return (a / b - 1.0) * 100.0 if b else 0.0


def fmt_px(x: float) -> str:
    if x >= 1000:
        return f"{x:,.0f}"
    if x >= 1:
        return f"{x:,.2f}"
    return f"{x:.4f}"
