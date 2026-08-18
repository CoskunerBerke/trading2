"""v3 yapılandırma bölümleri — `config.yaml` geriye uyumlu genişletilir. Riskle ilgili kritik hatalarda program başlamaz.

Bölümler: app, mode, markets, universe, data, scanner_v3, agents, coin_heads, llm, spot, futures_v3, execution, fees, tax_policy,
risk_profiles, portfolio, learning_v3, storage, obsidian_v3, dashboard, monitoring, deployment, security.
Bilinmeyen anahtarlar sessizce yutulmaz: uyarı listesine yazılır (`V3Config.warnings`).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields
from typing import Any

from .core import ConfigError

log = logging.getLogger(__name__)


def _build(cls, data: dict | None, warnings: list[str], section: str):
    data = data or {}
    allowed = {f.name for f in fields(cls)}
    unknown = [k for k in data if k not in allowed]
    if unknown:
        warnings.append(f"{section}: bilinmeyen anahtar(lar) yok sayıldı: {', '.join(unknown)}")
    return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class AppConfig:
    name: str = "trading2"
    version: str = "3.0"
    timezone_display: str = "Europe/Istanbul"
    run_label: str = "default"


@dataclass
class ModeConfig:
    mode: str = "PAPER"                       # OBSERVE | PAPER | TESTNET | SHADOW_LIVE | LIVE_LIMITED | LIVE
    live_trading: bool = False                # config guard (env ALLOW_LIVE_TRADING ile birlikte)
    withdrawals: bool = False                 # her zaman false/unsupported
    account_label: str = "default"


@dataclass
class MarketsConfig:
    spot_enabled: bool = True
    futures_enabled: bool = True
    quote_assets: list[str] = field(default_factory=lambda: ["USDT"])
    allow_usdc: bool = False


@dataclass
class UniverseSection:
    min_quote_volume_24h: float = 20_000_000
    max_spread_pct: float = 0.2
    min_depth_0_5pct_usdt: float = 50_000
    min_listing_age_days: int = 60
    max_symbols: int = 200
    refresh_minutes: int = 360
    tier2_top: int = 30
    tier3_top: int = 10


@dataclass
class DataConfig:
    primary: str = "binance"                  # binance | tradingview
    fallback: list[str] = field(default_factory=lambda: ["tradingview", "ccxt"])
    max_candle_age_bars: int = 2
    max_ticker_age_s: int = 120
    max_clock_drift_ms: int = 5000
    max_price_divergence_pct: float = 0.5
    rate_budget_safety: float = 0.7
    parquet_enabled: bool = True


@dataclass
class CoinHeadsSection:
    enabled: bool = True
    consensus_threshold: float = 0.22
    min_confidence: float = 0.25
    min_expected_r: float = 1.5
    max_workers: int = 4
    decision_ttl_minutes: int = 240
    funding_horizon_bars: int = 12


@dataclass
class LLMSection:
    provider: str = "noop"                    # noop | anthropic
    mode: str = "POSTMORTEM_ONLY"             # OFF | POSTMORTEM_ONLY | ADVISORY | VETO_ONLY | RESEARCH_COUNCIL
    model_cheap: str = "claude-haiku-4-5-20251001"
    model_strong: str = "claude-opus-5"
    model_batch: str = "claude-haiku-4-5-20251001"
    daily_usd_budget: float = 2.0
    daily_token_budget: int = 400_000
    per_tour_candidates: int = 3
    max_output_tokens: int = 1200
    cannot_execute: bool = True               # bilgi amaçlı; kodla zaten sabit
    api_key_env: str = "ANTHROPIC_API_KEY"


@dataclass
class FuturesV3Section:
    margin_mode: str = "isolated"
    leverage_default: int = 1
    leverage_max_paper_research: int = 2
    tp1_fraction: float = 0.5
    ambiguity_policy: str = "worst_case"
    liq_fee_pct: float = 0.5
    intrabar_source: str = "1m_or_high_low"   # bilgi


@dataclass
class ExecutionSection:
    gateway: str = "paper"                    # paper | binance_spot_testnet | binance_futures_testnet | live(disabled)
    testnet_enabled: bool = False
    client_order_prefix: str = "tb"
    reconcile_on_start: bool = True


@dataclass
class FeesSection:
    spot_maker_pct: float = 0.10
    spot_taker_pct: float = 0.10
    futures_maker_pct: float = 0.02
    futures_taker_pct: float = 0.05
    slippage_bps: float = 3.0
    bnb_discount: bool = False
    source: str = "config"


@dataclass
class TaxPolicySection:
    enabled: bool = False
    status: str = "UNVERIFIED_OR_NOT_EFFECTIVE"
    jurisdiction: str = "TR"
    residence: str = "TR"
    transaction_tax_rate: float = 0.0
    gain_withholding_rate: float = 0.0
    accounting_method: str = "FIFO"
    source_url: str = "https://www.tbmm.gov.tr/Haber/Detay?Id=24224a43-2062-4397-8761-019d2b9e0bd5"
    source_checked_at: str = "2026-03-26"
    manually_confirmed: bool = False
    version: str = "tr-2026-unverified"


@dataclass
class RiskProfilesSection:
    profile: str = "PAPER_RESEARCH"
    overrides: dict[str, Any] = field(default_factory=dict)
    i_understand: bool = False
    clusters: dict[str, str] = field(default_factory=dict)


@dataclass
class LearningV3Section:
    enabled: bool = True
    min_samples_train: int = 40
    holdout_frac: float = 0.2
    half_life_days: float = 60.0
    calibrator: str = "platt"
    shadow_trades: bool = True
    auto_promote_in_paper: bool = True


@dataclass
class StorageSection:
    sqlite_enabled: bool = True
    db_filename: str = "tradingbot.db"
    parquet_dir: str = "candles"
    backups_dir: str = "backups"
    keep_hourly: int = 24
    keep_daily: int = 7
    keep_weekly: int = 4


@dataclass
class ObsidianV3Section:
    coin_heads_enabled: bool = True
    prune_stale_hours: int = 48
    signals_retention_days: int = 30
    signals_max_files: int = 200
    write_only_on_change: bool = True


@dataclass
class DashboardSection:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    auth_token_env: str = "TRADINGBOT_DASHBOARD_TOKEN"
    allow_insecure_public: bool = False
    max_bars: int = 600


@dataclass
class MonitoringSection:
    json_logs: bool = True
    log_dir: str = "logs"
    heartbeat_stale_s: int = 2400
    telegram_enabled: bool = False
    discord_enabled: bool = False


@dataclass
class SecuritySection:
    live_confirmation_required: bool = True
    redact_logs: bool = True
    api_key_env_names: list[str] = field(default_factory=lambda: ["BINANCE_TESTNET_SPOT_KEY", "BINANCE_TESTNET_SPOT_SECRET",
                                                                  "BINANCE_TESTNET_FUTURES_KEY", "BINANCE_TESTNET_FUTURES_SECRET"])


@dataclass
class V3Config:
    app: AppConfig = field(default_factory=AppConfig)
    mode: ModeConfig = field(default_factory=ModeConfig)
    markets: MarketsConfig = field(default_factory=MarketsConfig)
    universe: UniverseSection = field(default_factory=UniverseSection)
    data: DataConfig = field(default_factory=DataConfig)
    coin_heads: CoinHeadsSection = field(default_factory=CoinHeadsSection)
    llm: LLMSection = field(default_factory=LLMSection)
    futures_v3: FuturesV3Section = field(default_factory=FuturesV3Section)
    execution: ExecutionSection = field(default_factory=ExecutionSection)
    fees: FeesSection = field(default_factory=FeesSection)
    tax_policy: TaxPolicySection = field(default_factory=TaxPolicySection)
    risk_profiles: RiskProfilesSection = field(default_factory=RiskProfilesSection)
    learning_v3: LearningV3Section = field(default_factory=LearningV3Section)
    storage: StorageSection = field(default_factory=StorageSection)
    obsidian_v3: ObsidianV3Section = field(default_factory=ObsidianV3Section)
    dashboard: DashboardSection = field(default_factory=DashboardSection)
    monitoring: MonitoringSection = field(default_factory=MonitoringSection)
    security: SecuritySection = field(default_factory=SecuritySection)
    warnings: list[str] = field(default_factory=list)


_SECTIONS = {"app": AppConfig, "mode": ModeConfig, "markets": MarketsConfig, "universe": UniverseSection, "data": DataConfig,
             "coin_heads": CoinHeadsSection, "llm": LLMSection, "futures_v3": FuturesV3Section, "execution": ExecutionSection, "fees": FeesSection,
             "tax_policy": TaxPolicySection, "risk_profiles": RiskProfilesSection, "learning_v3": LearningV3Section, "storage": StorageSection,
             "obsidian_v3": ObsidianV3Section, "dashboard": DashboardSection, "monitoring": MonitoringSection, "security": SecuritySection}

VALID_MODES = ("OBSERVE", "PAPER", "TESTNET", "SHADOW_LIVE", "LIVE_LIMITED", "LIVE")
VALID_LLM_MODES = ("OFF", "POSTMORTEM_ONLY", "ADVISORY", "VETO_ONLY", "RESEARCH_COUNCIL")


def load_v3(raw: dict[str, Any]) -> V3Config:
    warnings: list[str] = []
    kw = {}
    for name, cls in _SECTIONS.items():
        val = raw.get(name)
        if name == "mode" and isinstance(val, str):        # `mode: PAPER` kısa yazımı
            val = {"mode": val}
        kw[name] = _build(cls, val if isinstance(val, dict) else None, warnings, name)
    cfg = V3Config(**kw)
    cfg.warnings = warnings
    validate_v3(cfg)
    return cfg


def validate_v3(cfg: V3Config) -> None:
    """Risk-kritik hatalarda ConfigError (başlama). Sessiz varsayılana düşme yok."""
    m = cfg.mode.mode.upper()
    if m not in VALID_MODES:
        raise ConfigError(f"mode.mode geçersiz: {cfg.mode.mode} (geçerli: {', '.join(VALID_MODES)})")
    cfg.mode.mode = m
    if cfg.mode.withdrawals:
        raise ConfigError("mode.withdrawals desteklenmiyor — false olmalı")
    if m in ("LIVE", "LIVE_LIMITED"):
        raise ConfigError("LIVE/LIVE_LIMITED bu sürümde kapalı; config ile açılamaz")
    if cfg.mode.live_trading and os.environ.get("ALLOW_LIVE_TRADING", "").lower() != "true":
        raise ConfigError("mode.live_trading=true fakat ALLOW_LIVE_TRADING env yok — tutarsız (gerçek emir bu sürümde kapalı)")
    lm = cfg.llm.mode.upper()
    if lm not in VALID_LLM_MODES:
        raise ConfigError(f"llm.mode geçersiz: {cfg.llm.mode}")
    cfg.llm.mode = lm
    if cfg.llm.daily_usd_budget < 0 or cfg.llm.daily_token_budget < 0:
        raise ConfigError("llm bütçeleri negatif olamaz")
    if cfg.futures_v3.margin_mode.lower() != "isolated":
        raise ConfigError("futures_v3.margin_mode paper'da bile yalnız 'isolated' desteklenir")
    if not (1 <= cfg.futures_v3.leverage_default <= cfg.futures_v3.leverage_max_paper_research <= 125):
        raise ConfigError("futures_v3 kaldıraç ayarları tutarsız (1 ≤ default ≤ max ≤ 125)")
    if cfg.tax_policy.enabled and not cfg.tax_policy.manually_confirmed:
        raise ConfigError("tax_policy.enabled=true için manually_confirmed=true ve doğrulanmış kaynak gerekir")
    if cfg.execution.gateway.lower() == "live":
        raise ConfigError("execution.gateway=live bu sürümde kapalı")
    if cfg.dashboard.host not in ("127.0.0.1", "localhost", "::1") and not cfg.dashboard.allow_insecure_public and not os.environ.get(cfg.dashboard.auth_token_env):
        cfg.warnings.append(f"dashboard.host={cfg.dashboard.host} public; token env {cfg.dashboard.auth_token_env} tanımlı değil → dashboard başlatılmayacak")
    # risk profili çözümlenebilir mi (ConfigError yayılır)
    from .risk.profiles import resolve_profile
    resolve_profile(cfg.risk_profiles.profile, cfg.risk_profiles.overrides, i_understand=cfg.risk_profiles.i_understand)
