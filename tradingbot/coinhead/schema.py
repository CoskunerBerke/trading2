"""Coin Head şemaları — uzman raporu, faktör grubu skoru, plan v3 ve Coin Head kararı.

Bütün dataclass'lar `to_dict()` ile JSON'a serileşir; kimlikler deterministik (`stable_id`), zamanlar UTC ISO.
Legacy `AgentReport` → `SpecialistReport.from_legacy(...)` ile sarmalanır (mevcut ajanlara dokunulmaz).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from ..core import iso, stable_id, utc_now


class Stance(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"


class Verdict(str, Enum):
    SPOT_LONG = "SPOT_LONG"
    FUTURES_LONG = "FUTURES_LONG"
    FUTURES_SHORT = "FUTURES_SHORT"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    NO_TRADE = "NO_TRADE"
    DATA_INVALID = "DATA_INVALID"
    RISK_BLOCKED = "RISK_BLOCKED"


# NO_TRADE gerekçe kodları (Coin Head + Risk Engine ortak sözlüğü)
NO_TRADE_DATA_INVALID = "NO_TRADE_DATA_INVALID"
NO_TRADE_MIN_ORDER_CONFLICT = "NO_TRADE_MIN_ORDER_CONFLICT"
NO_TRADE_LOW_CONSENSUS = "NO_TRADE_LOW_CONSENSUS"
NO_TRADE_RED_TEAM_VETO = "NO_TRADE_RED_TEAM_VETO"
NO_TRADE_MARKET_UNAVAILABLE = "NO_TRADE_MARKET_UNAVAILABLE"
NO_TRADE_NO_VALID_PLAN = "NO_TRADE_NO_VALID_PLAN"


def stance_from_bias(bias: float) -> Stance:
    """Legacy AgentReport.stance eşikleri ile aynı (±0.15 / ±0.5)."""
    if bias >= 0.5:
        return Stance.STRONG_BULL
    if bias >= 0.15:
        return Stance.BULL
    if bias <= -0.5:
        return Stance.STRONG_BEAR
    if bias <= -0.15:
        return Stance.BEAR
    return Stance.NEUTRAL


def _enum_val(x: Any) -> Any:
    return x.value if isinstance(x, Enum) else x


def _clean(obj: Any) -> Any:
    """asdict çıktısındaki Enum'ları değere çevirir (JSON güvenli)."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return _enum_val(obj)


@dataclass
class SpecialistReport:
    """Bir uzman ajanın tek bir sembol/snapshot için raporu (legacy veya yeni)."""
    analysis_id: str
    run_id: str
    snapshot_id: str
    symbol: str
    market_type: str                     # spot | futures | both
    agent_name: str
    agent_version: str
    as_of_utc: str
    data_sources: list[str] = field(default_factory=list)
    data_freshness_seconds: float | None = None
    timeframes: list[str] = field(default_factory=list)
    stance: Stance = Stance.NEUTRAL
    bias: float = 0.0                    # -1..1
    confidence_raw: float = 0.0          # 0-100
    confidence_calibrated: float = 0.0   # 0-1
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    levels: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    veto: bool = False
    veto_reason: str = ""
    error: str = ""
    latency_ms: float = 0.0
    factor_group: str = ""

    def to_dict(self) -> dict:
        return _clean(asdict(self))

    @property
    def usable(self) -> bool:
        return not self.error

    @classmethod
    def from_legacy(cls, rep: Any, factor_group: str, run_id: str, snapshot_id: str, symbol: str,
                    market_type: str = "both", *, agent_version: str = "legacy-1", as_of: str | None = None,
                    bias: float | None = None, timeframes: list[str] | None = None) -> "SpecialistReport":
        """`tradingbot.agents.base.AgentReport` → SpecialistReport. `bias` verilirse raporun bias'ı yerine kullanılır
        (market ajanının likidite/türev bölünmesi için)."""
        b = float(rep.bias if bias is None else bias)
        b = max(-1.0, min(1.0, b))
        conf = float(max(0, min(100, rep.confidence)))
        findings = list(getattr(rep, "findings", []) or [])
        summary = getattr(rep, "summary", "") or ""
        ev_for: list[str] = []
        ev_against: list[str] = []
        if b > 0.05:
            ev_for = findings or ([summary] if summary else [])
        elif b < -0.05:
            ev_against = findings or ([summary] if summary else [])
        else:
            ev_for = findings or ([summary] if summary else [])
        name = f"{rep.agent}" if factor_group in ("", None) or factor_group == _LEGACY_DEFAULT_GROUP.get(rep.agent, factor_group) else f"{rep.agent}:{factor_group}"
        return cls(
            analysis_id=stable_id("analysis", run_id, snapshot_id, symbol, name),
            run_id=run_id, snapshot_id=snapshot_id, symbol=symbol, market_type=market_type,
            agent_name=name, agent_version=agent_version, as_of_utc=as_of or iso(),
            data_sources=["legacy_frames"] if rep.agent != "market" else ["binance_live"],
            data_freshness_seconds=None, timeframes=list(timeframes or []),
            stance=stance_from_bias(b), bias=b, confidence_raw=conf, confidence_calibrated=round(conf / 100.0, 4),
            evidence_for=ev_for, evidence_against=ev_against,
            metrics=dict(getattr(rep, "metrics", {}) or {}), levels=dict(getattr(rep, "levels", {}) or {}),
            warnings=list(getattr(rep, "warnings", []) or []), veto=False, veto_reason="",
            error=str(getattr(rep, "error", "") or ""), latency_ms=0.0, factor_group=factor_group,
        )


# legacy ajan adı → varsayılan grup (adlandırma için; tam eşleme factors.py'de)
_LEGACY_DEFAULT_GROUP = {"trend": "trend", "momentum": "momentum", "volatility": "volatility", "volume": "volume_flow",
                         "levels": "structure_levels", "candles": "structure_levels", "analog": "historical_edge",
                         "edge": "historical_edge", "market": "liquidity"}


@dataclass
class FactorGroupScore:
    group: str
    score: float                 # -1..1 (grup içi korelasyonlu kanıt tek oy)
    confidence: float            # 0-1
    data_quality: float          # 0-1 (hatasız rapor oranı)
    n_independent: int           # farklı ajan sayısı
    conflict: float              # grup içi bias std sapması
    calibration_weight: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlanSize:
    amount: float = 0.0
    amount_type: str = "base"    # base | quote
    leverage: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TradePlanV3:
    market_type: str = "spot"            # spot | futures
    direction: str = "LONG"              # LONG | SHORT
    entry_type: str = ""                 # breakout | pullback | market
    entry_trigger: str = ""
    entry_zone: tuple[float, float] = (0.0, 0.0)
    invalidation: str = ""
    stop: float = 0.0
    targets: list[float] = field(default_factory=list)
    time_horizon_bars: int = 0
    size: PlanSize = field(default_factory=PlanSize)
    margin: float = 0.0
    notional: float = 0.0
    expected_cost_pct: float = 0.0
    expected_r: float = 0.0
    valid: bool = False
    invalid_reason: str = ""

    @property
    def entry(self) -> float:
        lo, hi = self.entry_zone
        return (lo + hi) / 2.0 if lo and hi else (lo or hi)

    @property
    def stop_pct(self) -> float:
        e = self.entry
        return abs(e - self.stop) / e * 100.0 if e and self.stop else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_zone"] = list(self.entry_zone)
        d["entry"] = self.entry
        d["stop_pct"] = round(self.stop_pct, 4)
        return d


@dataclass
class CoinHeadDecision:
    coin_head_id: str
    run_id: str
    snapshot_id: str
    symbol: str
    market_type: str = "none"            # spot | futures | none
    regime: str = "UNKNOWN"
    verdict: Verdict = Verdict.NO_TRADE
    no_trade_reason: str = ""
    direction: str = ""                  # LONG | SHORT | ""
    confidence_raw: float = 0.0
    confidence_calibrated: float = 0.0
    p_win: float = 0.5
    expected_return_gross: float = 0.0   # % notional
    expected_cost: float = 0.0           # % notional
    expected_return_net: float = 0.0     # % notional
    expected_r: float = 0.0
    expected_shortfall: float = 0.0      # % notional (kayıp senaryosu: stop + maliyet)
    entry_trigger: str = ""
    entry_zone: tuple[float, float] = (0.0, 0.0)
    invalidation: str = ""
    stop: float = 0.0
    targets: list[float] = field(default_factory=list)
    time_horizon: int = 0                # bar (4h)
    position_size: float = 0.0           # base miktar
    margin: float = 0.0
    notional: float = 0.0
    leverage: int = 1
    consensus: dict[str, float] = field(default_factory=dict)      # grup → skor
    dissent: list[str] = field(default_factory=list)
    vetoes: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)             # [{agent, for, against}]
    model_versions: dict[str, str] = field(default_factory=dict)
    data_freshness: dict[str, Any] = field(default_factory=dict)
    expires_at: str = ""
    spot_plan: TradePlanV3 | None = None
    futures_plan: TradePlanV3 | None = None
    factor_scores: list[FactorGroupScore] = field(default_factory=list)
    specialist_reports: list[SpecialistReport] = field(default_factory=list)
    consensus_score: float = 0.0
    consensus_confidence: float = 0.0
    net_exposure_after: dict[str, float] = field(default_factory=dict)
    generated_at: str = ""
    latency_ms: float = 0.0

    def to_dict(self, *, include_reports: bool = True) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("spot_plan", "futures_plan", "factor_scores", "specialist_reports")}
        d = _clean(d)
        d["entry_zone"] = list(self.entry_zone)
        d["spot_plan"] = self.spot_plan.to_dict() if self.spot_plan else None
        d["futures_plan"] = self.futures_plan.to_dict() if self.futures_plan else None
        d["factor_scores"] = [f.to_dict() for f in self.factor_scores]
        d["specialist_reports"] = [r.to_dict() for r in self.specialist_reports] if include_reports else []
        return d

    @property
    def is_actionable(self) -> bool:
        return self.verdict in (Verdict.SPOT_LONG, Verdict.FUTURES_LONG, Verdict.FUTURES_SHORT)

    @property
    def active_plan(self) -> TradePlanV3 | None:
        if self.verdict == Verdict.SPOT_LONG:
            return self.spot_plan
        if self.verdict in (Verdict.FUTURES_LONG, Verdict.FUTURES_SHORT):
            return self.futures_plan
        return None


def new_decision(coin_head_id: str, run_id: str, snapshot_id: str, symbol: str) -> CoinHeadDecision:
    return CoinHeadDecision(coin_head_id=coin_head_id, run_id=run_id, snapshot_id=snapshot_id, symbol=symbol,
                            generated_at=iso(utc_now()))


__all__ = ["Stance", "Verdict", "SpecialistReport", "FactorGroupScore", "PlanSize", "TradePlanV3", "CoinHeadDecision",
           "stance_from_bias", "new_decision", "NO_TRADE_DATA_INVALID", "NO_TRADE_MIN_ORDER_CONFLICT",
           "NO_TRADE_LOW_CONSENSUS", "NO_TRADE_RED_TEAM_VETO", "NO_TRADE_MARKET_UNAVAILABLE", "NO_TRADE_NO_VALID_PLAN"]
