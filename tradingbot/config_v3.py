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
from .risk.leverage import validate_leverage_settings

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
    # --- DEGERLENDIRME EVRENI: panel top-listesi degil, botun analiz kapsami ---
    # Uygun sembol 40'in altindaysa sayi YAPAY doldurulmaz (below_target_reason raporlanir).
    eval_target_min: int = 40
    eval_target: int = 50
    eval_target_max: int = 60


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
class LeverageSection:
    """PAPER futures icin dinamik 2x-5x kaldirac. VARSAYILAN KAPALI.

    Kaldirac RISKI ARTIRMAZ: notional risk butcesi + stop mesafesinden gelir, kaldirac yalnizca
    `initial_margin = notional / leverage` degerini belirler. `max_leverage` 5 MUTLAK ust sinirdir.
    Zayif sinyal `min_leverage` ile ACILMAZ; NO_TRADE/HOLD/veto uretilir.
    """
    enabled: bool = False                     # yalniz PAPER arastirma profilinde acilir
    paper_only: bool = True                   # LIVE/TESTNET icin varsayilan KAPALI
    min_leverage: int = 2
    max_leverage: int = 5
    min_confidence: float = 0.30
    max_stop_atr_mult: float = 4.0
    min_stop_atr_mult: float = 0.5
    min_depth_usdt: float = 25_000.0
    max_spread_pct: float = 0.30
    min_liq_buffer_mult: float = 3.0
    conf_3x: float = 0.45
    conf_4x: float = 0.58
    conf_5x: float = 0.70
    edge_3x: float = 0.15
    edge_4x: float = 0.30
    edge_5x: float = 0.45
    max_atr_pct_3x: float = 8.0
    max_atr_pct_4x: float = 6.0
    max_atr_pct_5x: float = 4.0
    min_depth_4x: float = 100_000.0
    min_depth_5x: float = 250_000.0
    max_spread_4x: float = 0.12
    max_spread_5x: float = 0.06
    max_funding_4x: float = 0.03
    max_funding_5x: float = 0.015
    max_open_risk_frac_4x: float = 0.70
    max_open_risk_frac_5x: float = 0.50
    max_same_dir_4x: int = 3
    max_same_dir_5x: int = 2
    max_corr_5x: float = 0.80
    liq_buffer_4x: float = 3.5
    liq_buffer_5x: float = 4.5
    require_regime_alignment_5x: bool = True


@dataclass
class TelegramSection:
    """Telegram bildirimleri. TOKEN ASLA CONFIG'E YAZILMAZ — yalniz env degisken ADI tutulur."""
    enabled: bool = False
    bot_token_env: str = "TRADINGBOT_TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TRADINGBOT_TELEGRAM_CHAT_ID"
    timeout_s: float = 8.0
    max_retries: int = 3                      # sonsuz retry YOK
    retry_backoff_s: float = 2.0
    outbox_file: str = "notify_outbox.json"   # state_path altinda; atomik yazilir
    outbox_keep: int = 2000
    retry_batch: int = 5                      # tur başına en çok bu kadar başarısız olay yeniden denenir
    daily_summary_enabled: bool = True
    daily_summary_hour_utc: int = 21
    notify_open: bool = True
    notify_close: bool = True
    notify_health: bool = True
    suppress_backlog_on_start: bool = True    # restart'ta eski aciklar icin sahte bildirim YOK


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
    # GUVENLI VARSAYILAN: otomatik CHAMPION terfisi KAPALI. Feature-rich model yalniz CANDIDATE
    # olarak kalir; canli tahmin yoluna kendiliginden giremez. `true` verilmesi
    # PAPER_AUTO_PROMOTION_FORBIDDEN ile fail-closed reddedilir (bkz. validate_v3).
    auto_promote_in_paper: bool = False
    # --- Outcome Learning Loop V1 (karar gunlugu + sinirli ogrenme etkisi) ---
    # `decision_journal`: DEGERLENDIRILEN HER aday (kabul/red/veto/golge) icin kalici snapshot.
    # `influence_mode`: OFF | SHADOW | PAPER_BOUNDED. Varsayilan SHADOW -> ayarlama HESAPLANIR ve
    # kaydedilir ama baseline karar BIREBIR korunur. PAPER_BOUNDED yalniz PAPER modunda kabul
    # edilir (bkz. validate_v3) ve etki `influence_max_fraction` ile sinirlidir.
    decision_journal_enabled: bool = True
    decision_journal_max_lines: int = 20_000
    # --- KAYIPSIZ SAKLAMA: aktif gunluk sinirli kalir, tasan kayitlar SILINMEZ ---
    # Aktif dosyadan cikarilan her kayit once sikistirilmis + checksum'li bir segmente
    # muhurlenir (bkz. learn/journal_archive). Arsiv yoksa/yazilamazsa budama YAPILMAZ.
    # Yol state kokunden turer (`state_path/<dirname>`); mutlak yol hard-code EDILMEZ.
    decision_archive_enabled: bool = True
    decision_archive_dirname: str = "decision_archive"
    shadow_archive_dirname: str = "shadow_archive"
    # 0 = SINIRSIZ saklama, hicbir segment silinmez. Silme yalniz burasi acikca pozitif
    # yapilirsa mumkundur (varsayilan davranis: asla silme).
    decision_archive_max_segments: int = 0
    # --- UZUN VADELI RETRIEVAL: arsivlenmis golge sonuclar canli havuzda kalir ---
    # Indeks TUREV veridir: silinirse kayipsiz arsivden deterministik yeniden kurulur.
    # Kapatilirsa retrieval yalniz aktif pencereyi gorur (HOT_ONLY) — kayip degil, kapsam daralmasi.
    experience_index_enabled: bool = True
    experience_index_dirname: str = "experience_index"
    # --- DERS SAKLAMA: 200 yalniz SICAK/dashboard penceresidir, saklama siniri DEGILDIR ---
    # Eskiden `learning.py` `lessons[-200:]` ile tasan dersleri KALICI olarak siliyordu.
    # Artik tasan dersler once muhurlenmis segmente arsivlenir, sonra sicak pencere kisalir.
    # Arsiv kapaliysa ya da yazilamiyorsa BUDAMA DA YAPILMAZ (arsivsiz silme yasak).
    lesson_archive_enabled: bool = True
    lesson_archive_dirname: str = "lesson_archive"
    lesson_hot_window: int = 200
    # Aday basina taranan AZAMI segment — retrieval O(total archive) OLAMAZ.
    lesson_max_segments_scanned: int = 4
    # Asgari muhurleme blogu: tasma bu kadar birikmeden segment muhurlenmez (ders SILINMEZ,
    # yalniz muhurleme ertelenir). SegmentArchive.commit() manifesti bastan yazdigi icin
    # cok sayida kucuk segment maliyeti O(segment^2)'ye tasir.
    lesson_min_rotate_block: int = 50
    # Aday basina taranan deneyim UST SINIRI. Havuz bunun altindaysa TAM tarama yapilir
    # (davranis birebir eski haliyle ayni); ustundeyse sembol/yon kovalari + en yeni
    # kullanilabilir kuyruk taranir. Maliyet arsiv toplamiyla DOGRUSAL BUYUMEZ.
    retrieval_max_scan: int = 5_000
    # --- FEATURE YONETISIMI: genis olc, dar karar ver ---
    # Aktif bagimsiz bilgi ailesi tavani ve karar duzeyi yumusak girdi tavani. Kayit
    # `learn/feature_registry.py`dedir; ihlal config'i FAIL-CLOSED reddeder.
    max_active_families: int = 8
    max_active_soft_features: int = 12
    influence_mode: str = "SHADOW"
    influence_prior_strength: float = 20.0  # w = n/(n+prior_strength); >= 20 zorunlu
    influence_max_fraction: float = 0.05    # etkinin mutlak tavani (baseline orani)
    influence_top_k: int = 5
    # --- online ogrenme temposu: her kapanista yeniden egitim YOK ---
    retrain_min_new_closed: int = 10        # egitim icin gereken asgari YENI kapanmis islem
    retrain_cooldown_hours: float = 6.0     # iki egitim arasindaki asgari sure
    # --- PAPER arastirma politikasi (CHAMPION DEGIL; yalniz filtreler ya da kucultur) ---
    research_enabled: bool = True
    research_min_shadow_obs: int = 20       # aktiflesmeden once gereken eslesmis golge gozlemi
    research_min_active_obs: int = 20       # emeklilik karari icin gereken gozlem
    research_min_review_obs: int = 60       # manuel inceleme isareti icin gereken gozlem
    research_cooldown_hours: float = 24.0   # iki durum degisikligi arasindaki asgari sure
    research_retire_delta_r: float = -0.10  # bu kadar kotulesirse otomatik baseline'a donulur
    research_min_fold_consistency: float = 0.6   # offline fold tutarliligi tabani (aktivasyon kapisi)
    # --- ResearchCoordinator: aday uretim turu temposu (her turda agir is YOK) ---
    research_min_new_closed: int = 20       # arastirma turunu tetikleyen asgari YENI kapanis
    research_run_cooldown_hours: float = 12.0    # iki arastirma turu arasindaki asgari sure
    research_min_rows: int = 40             # walk-forward icin gereken asgari kronolojik kapanis
    research_seed: int = 7                  # deterministik aday uretimi/degerlendirmesi


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
    # --- canli yenileme (tarayici polling; Binance'a DOGRUDAN baglanti YOK) ---
    poll_positions_s: int = 7                 # acik pozisyon mark/PnL
    poll_portfolio_s: int = 20                # bakiye/teminat/acik risk
    poll_health_s: int = 12                   # saglik + heartbeat
    stale_price_s: int = 90                   # bu yasin uzerinde "FIYAT VERISI GUNCEL DEGIL"
    stale_run_s: int = 2400                   # strateji turu yasi uyarisi
    background_backoff_mult: int = 4          # arka plan sekmesinde aralik carpani
    timezone_label: str = "UTC"


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
class HistorySection:
    """Tarihsel veri gölü (public Binance; API anahtarı yok). Aralıklar gün; `max_available` → listing'den itibaren."""
    enabled: bool = True
    root_dir: str = "history"                        # cache_path altında
    tier_a_timeframes: list[str] = field(default_factory=lambda: ["1h", "4h", "1d"])
    tier_b_top_n: int = 50
    tier_b_timeframes: list[str] = field(default_factory=lambda: ["15m", "1h", "4h", "1d"])
    tier_c_top_n: int = 20
    tier_c_1m_days: int = 90
    tier_c_5m_days: int = 365
    max_available: bool = True                       # 15m ve üzeri: mümkün olan maksimum
    default_days: int = 360                          # max_available kapalıysa
    include_funding: bool = True
    include_open_interest: bool = True
    archive_first: bool = True                       # data.binance.vision aylık arşiv → REST tamamlama
    request_pause_s: float = 0.0                     # ek nezaket beklemesi (rate budget zaten var)


@dataclass
class QuantEvalSection:
    """Quant Evaluation V1 — offline/salt-okunur araştırma bileşenleri. GÜVENLİ VARSAYILANLAR:
    her şey kapalı ya da read-only; `auto_promotion=true` hiçbir koşulda kabul edilmez
    (fail-closed, `learning_v3.auto_promote_in_paper` ile aynı ilke)."""
    journal_enabled: bool = False              # birleşik karar→sonuç günlüğü (offline üretim)
    attribution_enabled: bool = False          # çok boyutlu attribution raporu (offline)
    replay_cost_manifest: bool = True          # manifest yalnız metadata — güvenli, default açık
    walk_forward_enabled: bool = False         # fold üretimi/raporu (offline)
    risk_v2_advisory: bool = False             # Risk V2 önerileri — YALNIZ tavsiye, emir yolu yok
    challenger_shadow: bool = False            # challenger shadow karşılaştırması (ayrı book)
    dashboard_view: bool = True                # /quant read-only görünümü — state'i yalnız okur
    auto_promotion: bool = False               # true → ConfigError; terfi yalnız manuel


@dataclass
class ExitPolicySection:
    """Çıkış politikası (`EXIT_GIVEBACK_AND_PROFIT_PROTECTION_V1`) — GÜVENLİ VARSAYILANLAR.

    `action_mode` yalnız `SHADOW` olabilir. `PAPER_BOUNDED` bu sürümde config ile AÇILAMAZ:
    gerçek azaltma/çıkış yolu ancak `exit_eval` terfi kapıları geçildikten sonra ve ayrı bir
    operatör kararıyla açılır (bkz. `learn/exit_executor.ALLOWED_MODES`).

    `path_enabled=false` yalnız yol KAYDINI durdurur; mevcut stop/TP davranışı hiçbir koşulda
    bu bölümden etkilenmez.
    """
    path_enabled: bool = True                 # açık pozisyon fiyat yolu kaydı (salt gözlem)
    action_mode: str = "SHADOW"               # SHADOW | (PAPER_BOUNDED bu sürümde YASAK)
    policy_version: str = "exit_v1.0.0"
    #: `learn.exit_policy.ExitPolicyConfig` alanları; verilmeyenler güvenli varsayılanda kalır.
    policy: dict[str, Any] = field(default_factory=dict)
    #: Yol deposu ayarları — 60 sn'lik exit-monitor'ün diski şişirmesini engeller.
    min_snapshot_interval_s: float = 55.0
    min_r_change: float = 0.02
    max_mark_age_s: float = 900.0
    #: Karşı-olgusal değerlendirme maliyet modeli (champion ile AYNI tarife kullanılır).
    eval_fee_rate: float = 0.0005
    eval_slippage_rate: float = 0.0003
    auto_promotion: bool = False              # true → ConfigError; terfi yalnız manuel


@dataclass
class EntrySelectivitySection:
    """Giriş seçiciliği (`ENTRY_SELECTIVITY_CHALLENGER_V1`) — GÜVENLİ VARSAYILANLAR.

    `mode` yalnız `SHADOW` olabilir. `PAPER_BOUNDED` bu sürümde config ile AÇILAMAZ: bir
    challenger ailesinin gerçek giriş kararını daraltması ancak `entry_eval` terfi kapıları
    geçildikten sonra ve ayrı bir operatör kararıyla olur
    (bkz. `learn/entry_eval.ALLOWED_MODES`).

    `snapshot_enabled=false` yalnız aday snapshot KAYDINI durdurur; mevcut sıralama, kabul
    kararı, boyut, kaldıraç, stop/TP ve RiskEngine davranışı hiçbir koşulda bu bölümden
    etkilenmez.
    """
    snapshot_enabled: bool = True             # sıralamaya giren aday snapshot'ı (salt gözlem)
    mode: str = "SHADOW"                      # SHADOW | (PAPER_BOUNDED bu sürümde YASAK)
    policy_version: str = "entry_v1.0.0"
    #: `learn.entry_challenger.EntryChallengerConfig` alanları; verilmeyenler güvenli varsayılanda.
    policy: dict[str, Any] = field(default_factory=dict)
    #: Tek turda yazılacak azami snapshot — patolojik bir tur diski şişiremez.
    max_snapshots_per_cycle: int = 200
    #: `trade_memory` giriş kayıtlarından türetilen gözlem snapshot'ları rapora eklensin mi.
    #: Bunlar `LEGACY_MEMORY` işaretlidir ve TERFİ KANITI SAYILMAZ (yalnız görünürlük).
    include_legacy_memory: bool = True
    #: Sıcak `entry_snapshot.jsonl` satır tavanı. Aşan satırlar ÖNCE arşive mühürlenir,
    #: SONRA sıcak dosyadan çıkarılır (arşiv-önce, kayıpsız). Arşiv yazılamazsa budama YOK.
    snapshot_max_lines: int = 20_000
    #: Arşivde tutulacak azami segment. 0 → SINIRSIZ (hiçbir segment silinmez, varsayılan).
    snapshot_archive_max_segments: int = 0
    auto_promotion: bool = False              # true → ConfigError; terfi yalnız manuel
    #: WEEKLY_MARKET_STRUCTURE_AND_CONTEXTUAL_PRICE_ACTION_V1 — F ve G aileleri.
    #: `enabled=false` yalnız GÖZLEMİ durdurur; hiçbir aktif karar bu bölümden etkilenmez.
    weekly_context_enabled: bool = True
    #: `learn.weekly_structure.WeeklyStructureConfig` alanları.
    weekly_structure_policy: dict[str, Any] = field(default_factory=dict)
    #: `learn.candle_context.CandleContextConfig` alanları.
    candle_policy: dict[str, Any] = field(default_factory=dict)
    #: `learn.entry_challenger_v2.WeeklyChallengerConfig` taban alanları (varyantlar üstüne biner).
    weekly_challenger_policy: dict[str, Any] = field(default_factory=dict)


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
    leverage: LeverageSection = field(default_factory=LeverageSection)
    telegram: TelegramSection = field(default_factory=TelegramSection)
    learning_v3: LearningV3Section = field(default_factory=LearningV3Section)
    storage: StorageSection = field(default_factory=StorageSection)
    obsidian_v3: ObsidianV3Section = field(default_factory=ObsidianV3Section)
    dashboard: DashboardSection = field(default_factory=DashboardSection)
    monitoring: MonitoringSection = field(default_factory=MonitoringSection)
    security: SecuritySection = field(default_factory=SecuritySection)
    history: HistorySection = field(default_factory=HistorySection)
    quant_eval: QuantEvalSection = field(default_factory=QuantEvalSection)
    exit_policy: ExitPolicySection = field(default_factory=ExitPolicySection)
    entry_selectivity: EntrySelectivitySection = field(default_factory=EntrySelectivitySection)
    warnings: list[str] = field(default_factory=list)


_SECTIONS = {"app": AppConfig, "mode": ModeConfig, "markets": MarketsConfig, "universe": UniverseSection, "data": DataConfig,
             "coin_heads": CoinHeadsSection, "llm": LLMSection, "futures_v3": FuturesV3Section, "execution": ExecutionSection, "fees": FeesSection,
             "tax_policy": TaxPolicySection, "risk_profiles": RiskProfilesSection, "leverage": LeverageSection,
             "telegram": TelegramSection, "learning_v3": LearningV3Section, "storage": StorageSection,
             "obsidian_v3": ObsidianV3Section, "dashboard": DashboardSection, "monitoring": MonitoringSection, "security": SecuritySection,
             "history": HistorySection, "quant_eval": QuantEvalSection,
             "exit_policy": ExitPolicySection,
             "entry_selectivity": EntrySelectivitySection}

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
    # GUVENLI RUNTIME OVERRIDE (VPS drop-in icin): yalniz OGRENME modu, typed ve fail-closed.
    # Kaynak agacini kirletmeden (config.yaml repo'da) PAPER_BOUNDED acip kapatmayi saglar.
    # Gecersiz deger ConfigError ile REDDEDILIR; PAPER-disi modda PAPER_BOUNDED yine yasak
    # (asagidaki validate_v3 kurali env yolu icin de gecerlidir).
    env_mode = os.environ.get("TRADINGBOT_LEARNING_INFLUENCE_MODE", "").strip().upper()
    if env_mode:
        from .learn.influence import MODES as _ENV_MODES
        if env_mode not in _ENV_MODES:
            raise ConfigError(
                f"TRADINGBOT_LEARNING_INFLUENCE_MODE geçersiz: {env_mode!r} "
                f"(geçerli: {', '.join(_ENV_MODES)})")
        if env_mode != cfg.learning_v3.influence_mode:
            log.warning("learning_v3.influence_mode env override: %s -> %s",
                        cfg.learning_v3.influence_mode, env_mode)
        cfg.learning_v3.influence_mode = env_mode
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
    # KALDIRAÇ: kural kümesi TEK kanonik yerde (`risk.leverage.validate_leverage_settings`).
    # Motor kurulumu (`TradingEngineV3.__init__`) AYNI fonksiyonu çağırır; iki kopya kural yok.
    lev = cfg.leverage
    validate_leverage_settings(enabled=bool(lev.enabled), paper_only=bool(lev.paper_only),
                               min_leverage=int(lev.min_leverage), max_leverage=int(lev.max_leverage), mode=m)
    tg = cfg.telegram
    if tg.max_retries < 0 or tg.timeout_s <= 0:
        raise ConfigError("telegram.max_retries ≥ 0 ve timeout_s > 0 olmalı")
    if not (0 <= int(tg.daily_summary_hour_utc) <= 23):
        raise ConfigError(f"telegram.daily_summary_hour_utc 0..23 aralığında olmalı "
                          f"(verilen: {tg.daily_summary_hour_utc})")
    if tg.retry_backoff_s < 0:
        raise ConfigError("telegram.retry_backoff_s negatif olamaz")
    if tg.retry_batch < 1:
        raise ConfigError("telegram.retry_batch en az 1 olmalı")
    for _f in ("bot_token_env", "chat_id_env"):
        _v = str(getattr(tg, _f) or "")
        if _v and (":" in _v or len(_v) > 100):
            raise ConfigError(f"telegram.{_f} bir ORTAM DEĞİŞKENİ ADI olmalı — token değeri config'e yazılamaz")
    lm = cfg.llm.mode.upper()
    if lm not in VALID_LLM_MODES:
        raise ConfigError(f"llm.mode geçersiz: {cfg.llm.mode}")
    cfg.llm.mode = lm
    if cfg.llm.daily_usd_budget < 0 or cfg.llm.daily_token_budget < 0:
        raise ConfigError("llm bütçeleri negatif olamaz")
    if cfg.learning_v3.auto_promote_in_paper:
        # Otomatik CHAMPION terfisi hiçbir modda kabul edilmez: araştırma adayı ile canlı tahmin modeli
        # arasındaki sınır operatör onayıyla geçilir. Sessiz varsayılana düşme YOK.
        raise ConfigError("PAPER_AUTO_PROMOTION_FORBIDDEN: learning_v3.auto_promote_in_paper=true "
                          "desteklenmiyor — terfi yalnız açık manuel operatör onayıyla yapılır")
    # Outcome Learning Loop: etki sözleşmesi fail-closed doğrulanır.
    # FEATURE YONETISIMI: tavan ihlali fail-closed (indikator enflasyonu ONLENIR).
    try:
        from .learn.feature_registry import FeatureGovernanceError, validate_registry
        validate_registry(max_families=cfg.learning_v3.max_active_families,
                          max_soft_inputs=cfg.learning_v3.max_active_soft_features)
    except FeatureGovernanceError as exc:
        raise ConfigError(f"FEATURE_GOVERNANCE: {exc}") from exc
    from .learn.influence import MODES as _INFLUENCE_MODES, PAPER_BOUNDED as _PB
    _lv3 = cfg.learning_v3
    if _lv3.influence_mode not in _INFLUENCE_MODES:
        raise ConfigError(f"learning_v3.influence_mode geçersiz: {_lv3.influence_mode} "
                          f"(geçerli: {', '.join(_INFLUENCE_MODES)})")
    if _lv3.influence_mode == _PB and m != "PAPER":
        raise ConfigError("LEARNING_INFLUENCE_PAPER_ONLY: learning_v3.influence_mode=PAPER_BOUNDED "
                          f"yalnız PAPER modunda kullanılabilir (mevcut mode={m})")
    if _lv3.influence_prior_strength < 20.0:
        raise ConfigError("learning_v3.influence_prior_strength >= 20 olmalı "
                          "(öğrenme etkisinin küçük kalması için)")
    if not (0.0 < _lv3.influence_max_fraction <= 0.20):
        raise ConfigError("learning_v3.influence_max_fraction (0, 0.20] aralığında olmalı")
    if cfg.quant_eval.auto_promotion:
        # `learning_v3.auto_promote_in_paper` ile AYNI ilke: challenger'dan CHAMPION'a geçiş
        # yalnız açık manuel operatör onayıyla olur — config bunu otomatikleştiremez.
        raise ConfigError("QUANT_AUTO_PROMOTION_FORBIDDEN: quant_eval.auto_promotion=true "
                          "desteklenmiyor — terfi yalnız manuel operatör onayıyla yapılır")
    # ÇIKIŞ POLİTİKASI: gerçek azaltma/çıkış yolu bu sürümde config ile AÇILAMAZ.
    _ex = cfg.exit_policy
    from .learn.exit_executor import ALLOWED_MODES as _EX_MODES, KNOWN_MODES as _EX_KNOWN
    _am = str(_ex.action_mode or "").upper()
    if _am not in _EX_KNOWN:
        raise ConfigError(f"exit_policy.action_mode geçersiz: {_ex.action_mode!r} "
                          f"(bilinen: {', '.join(_EX_KNOWN)})")
    if _am not in _EX_MODES:
        raise ConfigError(
            f"EXIT_EXECUTION_NOT_ACTIVATED: exit_policy.action_mode={_am} bu sürümde kapalı "
            f"(izinli: {', '.join(_EX_MODES)}). Gerçek çıkış yolu ancak terfi kapıları geçilip "
            "açık operatör onayı verildikten sonra açılır.")
    _ex.action_mode = _am
    if _ex.auto_promotion:
        # `learning_v3.auto_promote_in_paper` ve `quant_eval.auto_promotion` ile AYNI ilke.
        raise ConfigError("EXIT_AUTO_PROMOTION_FORBIDDEN: exit_policy.auto_promotion=true "
                          "desteklenmiyor — terfi yalnız manuel operatör onayıyla yapılır")
    if _ex.min_snapshot_interval_s < 0 or _ex.min_r_change < 0 or _ex.max_mark_age_s <= 0:
        raise ConfigError("exit_policy zamanlama/eşik alanları negatif olamaz "
                          "(max_mark_age_s pozitif olmalı)")
    if _ex.eval_fee_rate < 0 or _ex.eval_slippage_rate < 0:
        raise ConfigError("exit_policy maliyet oranları negatif olamaz")
    try:
        from .learn.exit_policy import ExitPolicyConfig as _EPC
        _EPC.from_dict({"policy_version": _ex.policy_version} | dict(_ex.policy or {}))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"exit_policy.policy geçersiz: {exc}") from exc
    # GİRİŞ SEÇİCİLİĞİ: gerçek giriş filtresi bu sürümde config ile AÇILAMAZ.
    _en = cfg.entry_selectivity
    from .learn.entry_eval import ALLOWED_MODES as _EN_MODES, KNOWN_MODES as _EN_KNOWN
    _enm = str(_en.mode or "").upper()
    if _enm not in _EN_KNOWN:
        raise ConfigError(f"entry_selectivity.mode geçersiz: {_en.mode!r} "
                          f"(bilinen: {', '.join(_EN_KNOWN)})")
    if _enm not in _EN_MODES:
        raise ConfigError(
            f"ENTRY_SELECTIVITY_NOT_ACTIVATED: entry_selectivity.mode={_enm} bu sürümde kapalı "
            f"(izinli: {', '.join(_EN_MODES)}). Gerçek giriş filtresi ancak terfi kapıları "
            "geçilip açık operatör onayı verildikten sonra açılır.")
    _en.mode = _enm
    if _en.auto_promotion:
        # `exit_policy.auto_promotion` ve `quant_eval.auto_promotion` ile AYNI ilke.
        raise ConfigError("ENTRY_AUTO_PROMOTION_FORBIDDEN: entry_selectivity.auto_promotion=true "
                          "desteklenmiyor — terfi yalnız manuel operatör onayıyla yapılır")
    if _en.max_snapshots_per_cycle < 1:
        raise ConfigError("entry_selectivity.max_snapshots_per_cycle >= 1 olmalı")
    if _en.snapshot_max_lines < 0 or _en.snapshot_archive_max_segments < 0:
        raise ConfigError("entry_selectivity saklama alanları negatif olamaz "
                          "(snapshot_max_lines=0 → rotasyon kapalı, silme YOK)")
    if 0 < _en.snapshot_max_lines < _en.max_snapshots_per_cycle:
        # Tavan tek turun yazabileceğinden küçükse her tur rotasyon tetiklenir ve sıcak
        # dosya asla bir turu bile taşıyamaz — sessiz kanıt kaybı riski.
        raise ConfigError("entry_selectivity.snapshot_max_lines, max_snapshots_per_cycle'dan "
                          "küçük olamaz")
    try:
        from .learn.entry_challenger import EntryChallengerConfig as _ECC
        _ECC.from_dict({"policy_version": _en.policy_version} | dict(_en.policy or {}))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"entry_selectivity.policy geçersiz: {exc}") from exc
    # HAFTALIK BAĞLAM (F/G aileleri): SHADOW dışına çıkış yolu YOKTUR — `entry_selectivity.mode`
    # zaten yukarıda `SHADOW`a kilitlendi ve bu aileler o modun altında çalışır.
    try:
        from .learn.candle_context import CandleContextConfig as _CCC
        from .learn.entry_challenger_v2 import WeeklyChallengerConfig as _WCC
        from .learn.weekly_structure import WeeklyStructureConfig as _WSC
        _WSC.from_dict(dict(_en.weekly_structure_policy or {}))
        _CCC.from_dict(dict(_en.candle_policy or {}))
        _WCC.from_dict(dict(_en.weekly_challenger_policy or {}))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"entry_selectivity haftalık bağlam politikası geçersiz: {exc}") from exc
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
