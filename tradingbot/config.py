from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class ExchangeConfig:
    source: str = "tradingview"          # tradingview | ccxt
    tv_exchange: str = "BINANCE"
    rules_exchange: str = "binance"
    candidates: list[str] = field(default_factory=lambda: ["binance", "bybit", "okx", "kucoin"])
    timeframe: str = "4h"
    history_days: int = 730
    cache_dir: str = "data"


@dataclass
class BacktestConfig:
    fee_pct: float = 0.10
    slippage_pct: float = 0.05
    train_ratio: float = 0.70
    min_trades: int = 15
    wfo_folds: int = 4
    wfo_initial_train_ratio: float = 0.45
    min_oos_sharpe: float = 0.30
    min_oos_profit_factor: float = 1.10
    min_oos_trades: int = 8


@dataclass
class RiskConfig:
    starting_equity_usdt: float = 50.0
    risk_per_trade_pct: float = 2.0
    atr_stop_mult: float = 2.5
    max_position_pct: float = 30.0
    max_open_positions: int = 3
    btc_regime_filter: bool = True
    min_confidence_to_buy: float = 55.0


@dataclass
class ScannerConfig:
    enabled: bool = True
    min_volume_usdt: float = 20_000_000
    flag_score: int = 60
    top_n: int = 12
    max_symbols: int = 160
    core_coins: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])


@dataclass
class FuturesConfig:
    enabled: bool = True
    starting_equity_usdt: float = 50.0
    max_positions: int = 3
    min_pwin: float = 0.45


@dataclass
class LearningConfig:
    min_trades: int = 20


@dataclass
class ObsidianConfig:
    vault_path: str = "C:/Users/berke/Trading bot/Trading_bot"
    folder: str = ""
    canvas_name: str = "Trading Bot Şeması.canvas"
    git_sync: bool = False

    @property
    def root(self) -> Path:
        p = Path(self.vault_path)
        return p / self.folder if self.folder else p


@dataclass
class BotConfig:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    coins: list[str] = field(default_factory=list)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    obsidian: ObsidianConfig = field(default_factory=ObsidianConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    futures: FuturesConfig = field(default_factory=FuturesConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    state_dir: str = "state"
    project_root: Path = PROJECT_ROOT

    @property
    def state_path(self) -> Path:
        p = Path(self.state_dir)
        return p if p.is_absolute() else self.project_root / p

    @property
    def cache_path(self) -> Path:
        p = Path(self.exchange.cache_dir)
        return p if p.is_absolute() else self.project_root / p


def _build(cls, data: dict[str, Any] | None):
    data = data or {}
    allowed = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    return cls(**allowed)


def load_config(path: str | os.PathLike | None = None) -> BotConfig:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    cfg = BotConfig(
        exchange=_build(ExchangeConfig, raw.get("exchange")),
        coins=list(raw.get("coins") or []),
        backtest=_build(BacktestConfig, raw.get("backtest")),
        risk=_build(RiskConfig, raw.get("risk")),
        obsidian=_build(ObsidianConfig, raw.get("obsidian")),
        scanner=_build(ScannerConfig, raw.get("scanner")),
        futures=_build(FuturesConfig, raw.get("futures")),
        learning=_build(LearningConfig, raw.get("learning")),
        state_dir=raw.get("state_dir", "state"),
        project_root=cfg_path.parent if cfg_path.exists() else PROJECT_ROOT,
    )
    # Ortam değişkeni ile vault yolu ezilebilir
    env_vault = os.environ.get("TRADINGBOT_VAULT_PATH")
    if env_vault:
        cfg.obsidian.vault_path = env_vault
    if os.environ.get("TRADINGBOT_VAULT_GIT_SYNC", "").lower() in ("1", "true", "yes"):
        cfg.obsidian.git_sync = True
    return cfg
