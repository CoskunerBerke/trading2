"""7/24 MOTOR v3 — legacy TradingEngine'i genişletir (eski `tour` korunur).

Tur akışı:
  kill-switch/health kontrolü → TARA (legacy tarayıcı, tier-1) → legacy ajanlar (uzman raporları + brief)
  → veri kalitesi kapısı → COIN HEAD (faktör grupları, red team, spot/futures planı) → BAŞ YÖNETİCİ (yalnız
  SIRALAMA/açıklama/yumuşak ceza) → tetik (4h kapanış / geri çekilme) → maliyet sonrası ekonomi → duplicate
  → araştırma politikası → BÜTÜN boyut çarpanları → NİHAİ notional/risk → GLOBAL RISK ENGINE (yetkili
  kapasite, nihai değerlerle) → PAPER EXECUTION (FuturesLedgerV2 / SpotLedger)
  → tick (stop/TP/liq/funding; bar_advance) → ÖĞRENME (v1 uyumlu + v2 hafıza/postmortem) → gölge işlemler
  → durum dosyaları (atomik: ledger → learner → triggers) → Obsidian (legacy + Coin Heads) → health/heartbeat.
Öncelik: veri doğru mu → maliyet sonrası edge → risk → red team → portföy → uygulanabilirlik → kayıt → ancak o zaman aç.
RİSK YALNIZ GERÇEKTEN AÇILAN POZİSYONLARLA TÜKENİR: tetiklenmeyen, duplicate, politika-eleyen ya da
emir reddi alan aday hiçbir kapasite tüketmez (bkz. `_execute_locked` sözleşmesi).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from decimal import Decimal

from .accounting import (AmountType, FeeSchedule, FiltersCache, FuturesLedgerV2, LiquidationParams, MarketType, Side, SizeSpec,
                         SlippageModel, SpotLedger, TaxPolicy, TickData, default_brackets, static_rates)
from .agents.manager import CoinBrief
from .coinhead import ChiefPortfolioManager, CoinHeadConfig, CoinHeadInputs, CoinHeadRegistry, Verdict
from .config import BotConfig
from .core import atomic_write_json, iso, new_id, read_json, run_id_now, stable_id, utc_now
from .engine import TradingEngine
from .learn import LearnConfig, LearnerV2, ModelRegistry, ShadowBook, TradeMemory
from .learning import features_from_brief
from .market.quality import DataQualityConfig, DataQualityGate
from .risk import (KillSwitch, ModeState, RiskEngine, build_state, enforces_position_cap, resolve_profile,
                   spot_notional_from_prices,
                   warn_if_below_recommended)
from .risk.leverage import LeverageConfig, LeverageContext, select_leverage, validate_leverage_settings

log = logging.getLogger(__name__)


def _as_multiplier(value) -> float:
    """Boyut çarpanını güvenle oku: ``None`` = "verilmedi" → 1.0, açıkça verilen ``0.0`` → 0.0.

    Eski `float(value or 1.0)` ifadesi açıkça verilen `0.0`'ı `1.0`'a çeviriyordu: "hiç açma"
    talimatı sessizce "tam boyut aç"a dönüşüyordu. `None` ile `0.0` artık AYRI ele alınır.
    Çarpanlar yalnız küçültür: [0.0, 1.0] aralığına kırpılır.
    """
    if value is None:
        return 1.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0                      # okunamayan çarpan fail-closed: emir açma
    if v != v:                          # NaN
        return 0.0
    return max(0.0, min(1.0, v))

# Yumusak kanit -> muhafazakar edge'den dusulecek R cezasi. Hicbiri TEK BASINA reddetmez; toplam ceza
# `GateLedger.soft_penalty_r()` icinde ust sinirlidir (cok sayida orta zayiflik otomatik vetoya donmez).
# Karar hunisi: her turda ve kayan 24 saatte tutulur. `trades_opened_24h` YALNIZ gozlem metrigidir,
# karar kapisi DEGILDIR. `daily_trade_cap`/`per_run_trade_cap` her zaman null olarak raporlanir.
_FUNNEL_KEYS = ("actionable", "ranked", "chief_blocked", "hard_safety_blocked", "no_trigger",
                "trigger_fired", "positive_point_edge", "positive_conservative_edge",
                "negative_edge_blocked", "research_small", "duplicate_blocked",
                "research_policy_blocked", "size_multiplier_zero", "leverage_gate_blocked",
                "risk_capacity_blocked", "capacity_approved", "exchange_rejected", "opened")

_SOFT_PENALTY_R = {"LOW_CONSENSUS": 0.06, "LOW_CONFIDENCE": 0.06, "HIGH_DISSENT": 0.05,
                   "RR_BELOW_PREFERRED": 0.05, "PATTERN_WEAK": 0.05, "SPREAD_WIDE": 0.04,
                   "VOL_REGIME_HIGH": 0.04, "FUNDING_ADVERSE": 0.04, "MARKET_REGIME_MISMATCH": 0.10,
                   "SAME_DIRECTION_CROWDED": 0.08, "CLUSTER_CROWDED": 0.08,
                   "RED_TEAM_SOFT_PENALTY": 0.04, "SMALL_SAMPLE": 0.05,
                   # --- RED TEAM'in EKONOMIK/ISTATISTIKSEL kodlari: artik SERT VETO DEGIL ---
                   "WEAK_OOS_EDGE": 0.08, "LOW_TRADE_COUNT": 0.05, "HIGH_CORRELATION_EXPOSURE": 0.08,
                   "CROWDED_SAME_DIRECTION": 0.08, "AGAINST_BTC_REGIME": 0.08, "STOP_TOO_FAR": 0.05,
                   "STOP_TOO_CLOSE": 0.05, "FUNDING_EXTREME": 0.06, "FUNDING_CROWDED": 0.04,
                   "NEW_LISTING": 0.06, "WIDE_SPREAD": 0.05, "LOW_LIQUIDITY": 0.05,
                   "LIQ_BUFFER_THIN": 0.05}

# YETKILI risk kapasitesi kodlari: `RiskEngine.evaluate()` bunlardan birini reddettiginde karar
# gercek kapasite doldugu icin verilmistir (KOTA DEGIL).
_CAPACITY_CODES = ("TOTAL_OPEN_RISK", "MARGIN_UTILIZATION", "MAX_POSITIONS", "MAX_POSITIONS_MARKET",
                   "CLUSTER_CAP", "ALTCOIN_EXPOSURE", "MAX_POSITION_PCT", "SPOT_ALLOCATION")


class TradingEngineV3(TradingEngine):
    def __init__(self, cfg: BotConfig):
        super().__init__(cfg)
        v3 = cfg.v3
        st = cfg.state_path
        st.mkdir(parents=True, exist_ok=True)
        # --- risk / mod / kill switch
        self.profile = resolve_profile(v3.risk_profiles.profile, v3.risk_profiles.overrides, i_understand=v3.risk_profiles.i_understand)
        self.killswitch = KillSwitch.load(st / "killswitch.json")
        self.risk = RiskEngine(self.profile, self.killswitch, v3.risk_profiles.clusters or None)
        # --- dinamik futures kaldıracı (2x–5x). VARSAYILAN KAPALI; yalnız PAPER'da açılabilir.
        _lv = v3.leverage
        # KANONIK DOGRULAMA URETIM ZINCIRINDE: ham config degerleri (kelepceleme ONCESI) tek kural
        # kumesinden gecer. min<2 / max>5 / min>max / paper_only ihlali -> BASLATMA YOK.
        validate_leverage_settings(enabled=bool(_lv.enabled), paper_only=bool(_lv.paper_only),
                                   min_leverage=int(_lv.min_leverage), max_leverage=int(_lv.max_leverage),
                                   mode=cfg.mode)
        self.leverage_cfg = LeverageConfig(
            enabled=bool(_lv.enabled) and (cfg.mode == "PAPER" or not _lv.paper_only),
            paper_only=bool(_lv.paper_only),
            min_leverage=int(_lv.min_leverage),
            max_leverage=min(int(_lv.max_leverage), int(self.profile.futures_max_leverage)),
            min_confidence=_lv.min_confidence, max_stop_atr_mult=_lv.max_stop_atr_mult,
            min_stop_atr_mult=_lv.min_stop_atr_mult, min_depth_usdt=_lv.min_depth_usdt,
            max_spread_pct=_lv.max_spread_pct, min_liq_buffer_mult=_lv.min_liq_buffer_mult,
            conf_3x=_lv.conf_3x, conf_4x=_lv.conf_4x, conf_5x=_lv.conf_5x,
            edge_3x=_lv.edge_3x, edge_4x=_lv.edge_4x, edge_5x=_lv.edge_5x,
            max_atr_pct_3x=_lv.max_atr_pct_3x, max_atr_pct_4x=_lv.max_atr_pct_4x, max_atr_pct_5x=_lv.max_atr_pct_5x,
            min_depth_4x=_lv.min_depth_4x, min_depth_5x=_lv.min_depth_5x,
            max_spread_4x=_lv.max_spread_4x, max_spread_5x=_lv.max_spread_5x,
            max_funding_4x=_lv.max_funding_4x, max_funding_5x=_lv.max_funding_5x,
            max_open_risk_frac_4x=_lv.max_open_risk_frac_4x, max_open_risk_frac_5x=_lv.max_open_risk_frac_5x,
            max_same_dir_4x=_lv.max_same_dir_4x, max_same_dir_5x=_lv.max_same_dir_5x,
            max_corr_5x=_lv.max_corr_5x, liq_buffer_4x=_lv.liq_buffer_4x, liq_buffer_5x=_lv.liq_buffer_5x,
            require_regime_alignment_5x=_lv.require_regime_alignment_5x)
        # Profil tavani (`futures_max_leverage`) tabani asagi kelepceliyorsa SESSIZCE 1x'e dusulmez:
        # etkin config yeniden dogrulanir ve motor baslamaz.
        if self.leverage_cfg.enabled:
            self.leverage_cfg.validate(mode=cfg.mode)
        # --- PAPER bildirimleri (Telegram). KAPALIYKEN hicbir ag cagrisi yapilmaz. ---
        from .notify import TradeNotifier
        self.notifier = TradeNotifier.from_config(v3.telegram, st)
        self.mode_state = ModeState(st / "mode.json")
        if self.mode_state.mode.value != cfg.mode:
            log.warning("mode.json (%s) ile config mode (%s) farklı — mode.json esas (geçişler yalnız manuel)", self.mode_state.mode.value, cfg.mode)
        if cfg.mode != "PAPER":
            for w in warn_if_below_recommended(self.profile):
                log.warning("risk profili uyarısı: %s", w)
        # --- muhasebe v2 (legacy dosya otomatik içe aktarılır; legacy defter nesnesi de kalır)
        fees = FeeSchedule(maker_pct=Decimal(str(v3.fees.futures_maker_pct)), taker_pct=Decimal(str(v3.fees.futures_taker_pct)), source=v3.fees.source)
        slip = SlippageModel(fixed_bps=Decimal(str(v3.fees.slippage_bps)))
        # DEFTER ADET TAVANI: yapılandırılmış değer (3) KORUNUR ve JSON'a integer yazılır; tavanın
        # UYGULANIP UYGULANMADIĞINI risk profili belirler. Canlı motor ve `HistoricalReplay` AYNI
        # ortak sözleşmeyi (`risk.enforces_position_cap`) kullanır — iki motor ayrı formül üretmez.
        self.ledger2 = FuturesLedgerV2.load(self.ledger_path, starting_equity=cfg.futures.starting_equity_usdt,
                                            max_positions=cfg.futures.max_positions,
                                            enforce_position_cap=enforces_position_cap(self.profile),
                                            fees=fees, slippage=slip, brackets=default_brackets(),
                                            liq_params=LiquidationParams(liq_fee_pct=Decimal(str(v3.futures_v3.liq_fee_pct))),
                                            tp1_fraction=Decimal(str(v3.futures_v3.tp1_fraction)), tax_policy=TaxPolicy.disabled())
        self.spot2 = SpotLedger.load(st / "spot_ledger.json", starting_cash=cfg.risk.starting_equity_usdt)
        self.ledger = self.ledger2          # legacy yardımcılar (learning_notes/summary) v2 defteri görsün
        self.filters = FiltersCache(cfg.cache_path / "symbol_filters.json")
        # --- coin heads
        ch = v3.coin_heads
        self.head_cfg = CoinHeadConfig(consensus_threshold=ch.consensus_threshold, min_confidence=ch.min_confidence, min_expected_r=ch.min_expected_r,
                                       fee_taker_pct=v3.fees.futures_taker_pct, spot_fee_pct=v3.fees.spot_taker_pct, slippage_pct=v3.fees.slippage_bps / 100,
                                       funding_horizon_bars=ch.funding_horizon_bars, max_leverage=self.profile.futures_max_leverage,
                                       equity_usdt=cfg.futures.starting_equity_usdt, risk_pct=self.profile.risk_per_trade_pct,
                                       decision_ttl_minutes=ch.decision_ttl_minutes)
        self._entry_lock = __import__("threading").RLock()
        self._pattern_engine = None                            # SimilarPatternEngine (HistoryStore'dan tembel yüklenir)
        self._pattern_loaded = False
        self._exit_lock = __import__("threading").RLock()
        self._stop_check = None                                # kooperatif durdurma: True → yeni giriş yok (çıkışlar sürer)
        self._gap_checked = False                              # süreç başına bir kez offline-gap uzlaştırması
        self._gap_blocked = False                              # GAP_AMBIGUOUS → yeni giriş yok (çıkışlar sürer)
        self._gap_provider_factory = None                      # test enjeksiyonu; None → gerçek USDⓈ-M public provider
        if self.ledger2.positions:
            log.info("resume: %d açık futures pozisyonu (%s) · defter güncelleme %s",
                     len(self.ledger2.positions), ", ".join(sorted(self.ledger2.positions)), self.ledger2.updated_at or "-")
        from .learn.research_coordinator import CoordinatorConfig, ResearchCoordinator
        from .learn.research_policy import ResearchGates, ResearchPolicyBook
        from .learn.telemetry import SnapshotTelemetry
        self.snap_telemetry = SnapshotTelemetry.load(cfg.state_path)
        self._pred_snapshots = {}                       # sembol -> karar ani snapshot (predict + kayit ayni nesne)
        _lv = cfg.v3.learning_v3
        # PAPER arastirma politikasi: aktif aday YOKSA bot baseline davranisini AYNEN surdurur.
        self.research = ResearchPolicyBook(st / "research_policy.json", ResearchGates(
            min_shadow_obs=_lv.research_min_shadow_obs, min_active_obs=_lv.research_min_active_obs,
            min_review_obs=_lv.research_min_review_obs, cooldown_hours=_lv.research_cooldown_hours,
            retire_delta_r=_lv.research_retire_delta_r,
            min_fold_consistency=_lv.research_min_fold_consistency),
            risk_profile_max_leverage=float(min(1.0, self.profile.futures_max_leverage)))
        # EKSIK HALKA: aday uretimi/offline degerlendirme/SHADOW gecisi bu katmanda yurur.
        self.research_coordinator = ResearchCoordinator(
            self.research, memory_path=st / "trade_memory.jsonl", state_path=st, bot_cfg=cfg,
            risk_profile_max_leverage=float(min(1.0, self.profile.futures_max_leverage)),
            cfg=CoordinatorConfig(enabled=_lv.research_enabled,
                                  min_new_closed=_lv.research_min_new_closed,
                                  cooldown_hours=_lv.research_run_cooldown_hours,
                                  min_rows=_lv.research_min_rows, seed=_lv.research_seed))
        self._last_train_at = None                      # online ogrenme temposu (cooldown)
        self._closes_since_train = 0
        # Benzersiz sinyal tekrar korumasi (SABIT SAYI KOTASI DEGIL): ayni
        # symbol|market|timeframe|closed_bar_ts|side|setup ikinci kez acilamaz.
        self._sig_path = st / "signals_seen.json"
        self._seen_signals = list((read_json(self._sig_path, default=None) or {}).get("ids") or [])
        self._funnel_path = st / "decision_funnel.json"
        self.registry = CoinHeadRegistry(self.head_cfg, max_workers=ch.max_workers)
        self.registry.load(st)          # snapshot olay-zaman sırası (legacy hash-only state güvenli geçer)
        self._tour_no = 0               # aynı ms için deterministik tie-breaker
        from .coinhead.chief import ChiefConfig as _ChiefCfg
        # Chief'in SERT kapisi artik yalnizca GERCEK risk butcesidir (sabit islem sayisi kotasi YOK).
        self.chief_mgr = ChiefPortfolioManager(
            _ChiefCfg(max_total_open_risk_pct=self.profile.max_total_open_risk_pct,
                      risk_per_trade_pct=self.profile.risk_per_trade_pct),
            clusters=v3.risk_profiles.clusters or None)
        self.quality = DataQualityGate(DataQualityConfig(max_candle_age_bars=v3.data.max_candle_age_bars, max_ticker_age_s=v3.data.max_ticker_age_s,
                                                         max_clock_drift_ms=v3.data.max_clock_drift_ms, max_price_divergence_pct=v3.data.max_price_divergence_pct))
        # --- öğrenme v2 (v1 `self.learner` korunur)
        self.memory = TradeMemory(st / "trade_memory.jsonl")
        self.model_registry = ModelRegistry(st / "models.json")
        self.learner2 = LearnerV2(self.memory, self.model_registry, LearnConfig(min_samples_train=v3.learning_v3.min_samples_train,
                                  holdout_frac=v3.learning_v3.holdout_frac, half_life_days=v3.learning_v3.half_life_days, calibrator=v3.learning_v3.calibrator),
                                  st / "learn_v2.json")
        # KAYIPSIZ SAKLAMA: gölge defteri de taşan kayıtları önce arşive mühürler.
        # Yol state kökünden türer; arşiv kalıcı state altındadır (yedeklemeye dahil).
        self.shadow_archive = None
        try:
            if v3.learning_v3.decision_archive_enabled:
                from .learn.journal_archive import SegmentArchive
                self.shadow_archive = SegmentArchive(
                    st / v3.learning_v3.shadow_archive_dirname, stream_id="shadow_book",
                    record_schema_version="shadow_trade_v1",
                    code_sha=getattr(cfg, "code_sha", None),
                    max_segments=v3.learning_v3.decision_archive_max_segments)
        except Exception as exc:  # noqa: BLE001 — arşiv kurulamazsa SİLME de yapılmaz
            log.warning("gölge arşivi başlatılamadı (budama devre dışı, kayıp yok): %s", exc)
            self.shadow_archive = None
        self.shadow = ShadowBook(st / "shadow_book.json", archive=self.shadow_archive)
        # --- Outcome Learning Loop V1: karar günlüğü + sınırlı öğrenme etkisi ---
        # Arıza worker'ı ÇÖKERTMEZ: journal/influence başlatılamazsa baseline davranış sürer.
        from .learn.decision_journal import DecisionJournal
        from .learn.influence import InfluenceConfig
        self.decision_journal = None
        self.exp_index_store = None
        self._journal_errors = 0
        self._influence_log: list[dict] = []
        try:
            self.influence_cfg = InfluenceConfig(
                mode=v3.learning_v3.influence_mode,
                prior_strength=v3.learning_v3.influence_prior_strength,
                max_fraction=v3.learning_v3.influence_max_fraction,
                top_k=v3.learning_v3.influence_top_k)
            self.influence_cfg.validate()
            if v3.learning_v3.decision_journal_enabled:
                archive = None
                if v3.learning_v3.decision_archive_enabled:
                    from .learn.journal_archive import SegmentArchive
                    archive = SegmentArchive(
                        st / v3.learning_v3.decision_archive_dirname,
                        stream_id="decision_journal",
                        record_schema_version="decision_journal_v1",
                        code_sha=getattr(cfg, "code_sha", None),
                        max_segments=v3.learning_v3.decision_archive_max_segments)
                self.decision_journal = DecisionJournal(
                    st / "decision_journal.jsonl",
                    max_lines=v3.learning_v3.decision_journal_max_lines,
                    archive=archive)
                self.decision_journal.load_seen()
            # UZUN VADELİ RETRIEVAL: aktif pencereden çıkmış gölge sonuçlar canlı havuzda kalır.
            # İndeks TÜREV veridir (silinirse kayıpsız arşivden yeniden kurulur) ve aday başına
            # arşiv TARAMAZ. Karar günlüğü arşivi BURAYA GİRMEZ — çift sayım olurdu.
            if self.shadow_archive is not None and v3.learning_v3.experience_index_enabled:
                from .learn.experience_index import ExperienceIndexStore
                self.exp_index_store = ExperienceIndexStore(
                    st / v3.learning_v3.experience_index_dirname, self.shadow_archive,
                    shadow_weight=self.influence_cfg.shadow_weight,
                    shadow_fidelity=self.influence_cfg.shadow_fidelity)
        except Exception as exc:  # noqa: BLE001 — öğrenme altyapısı karar yolunu bloke edemez
            log.warning("outcome-learning başlatılamadı, baseline sürüyor: %s", exc)
            from .learn.influence import InfluenceConfig as _IC
            self.influence_cfg = _IC(mode="OFF")
            self.decision_journal = None
            self.exp_index_store = None
        self.universe = read_json(st / "universe.json", default=None)
        self.last_bar_seen: str = ""
        self.run_id = ""
        self.last_decisions: dict = {}
        self.last_chief: dict | None = None
        # Obsidian coin heads (modül varsa)
        try:
            from .obsidian_coinheads import ObsidianCoinHeadWriter
            self.ch_writer = ObsidianCoinHeadWriter(cfg.obsidian.root) if v3.obsidian_v3.coin_heads_enabled else None
            if self.ch_writer is not None:
                self.ch_writer.evidence_dir = st / "evidence"
        except ImportError:
            self.ch_writer = None

    # ------------------------------------------------------------------ yardımcılar
    def _availability(self, symbol: str) -> dict[str, bool]:
        base = symbol.split("/")[0]
        if self.universe and isinstance(self.universe.get("merged"), dict):
            m = self.universe["merged"].get(symbol) or self.universe["merged"].get(f"{base}USDT")
            if m:
                return {"spot": bool(m.get("spot")), "futures": bool(m.get("futures"))}
        return {"spot": symbol in set(self.cfg.coins), "futures": True}

    def _portfolio_state(self, marks: dict[str, float]):
        pos = []
        for sym, p in self.ledger2.positions.items():
            pos.append({"symbol": sym, "market_type": "USDM_PERP", "side": p.side.value, "notional": float(p.qty * p.entry_avg), "margin": float(p.isolated_margin),
                        "entry": float(p.entry_avg), "stop": float(p.stop) if p.stop else None, "leverage": p.leverage,
                        "liq_price": float(p.liquidation_price) if p.liquidation_price else None, "opened_at": p.opened_at})
        for sym, sp in self.spot2.positions().items():
            q, ac = float(sp.get("qty", 0) or 0), float(sp.get("avg_cost", 0) or 0)
            if q > 0:
                # FAIL-CLOSED FIYAT: bozuk/NaN/Inf/sifir mark maruziyeti SIFIR gostermez;
                # once gecerli mark, sonra gecerli maliyet tabani, ikisi de yoksa BILINMIYOR.
                _notional, _unknown = spot_notional_from_prices(q, marks.get(sym), ac)
                pos.append({"symbol": sym, "market_type": "SPOT", "side": "LONG", "notional": _notional,
                            "margin": q * ac, "entry": ac, "notional_unknown": _unknown,
                            "stop": sp.get("stop") or None, "leverage": 1, "opened_at": str(sp.get("entry_time", ""))})
        fs = self.ledger2.summary(marks)
        ss = self.spot2.summary(marks)
        # birleşik equity: futures MTM + spot P&L (spot cüzdanı ayrı başlangıçla tutulur; sadece kâr/zararı eklenir)
        equity = float(fs["equity_mtm"]) + (float(ss.get("equity", 0) or 0) - float(ss.get("starting_equity", 0) or 0))
        history = self.ledger2.history_dicts() + self.spot2.history_dicts()
        hwm_path = self.cfg.state_path / "equity_hwm.json"
        hwm = float((read_json(hwm_path, default={}) or {}).get("hwm", 0.0))
        state = build_state(equity=equity, starting_equity=float(self.ledger2.starting_equity), available=float(fs["available"]),
                            used_margin=float(fs["used_margin"]), positions=pos, history=history, high_water_mark=hwm, now=utc_now(),
                            clusters=self.cfg.v3.risk_profiles.clusters or None)
        atomic_write_json(hwm_path, {"hwm": state.high_water_mark, "updated_at": iso()})
        return state

    def set_stop_check(self, fn) -> None:
        """`fn() -> bool`: durdurma isteği var mı. İstek varken yeni PAPER girişi açılmaz; açık pozisyon çıkışları ve defter kaydı sürer."""
        self._stop_check = fn

    def _stopping(self) -> bool:
        try:
            return bool(self._stop_check and self._stop_check())
        except Exception:  # noqa: BLE001
            return False

    def _persist_risk_state(self, state, risk_log: list[dict], now: datetime) -> bool:
        """risk.json'u atomik yaz (yetkili spot+futures defterlerinden türetilen birleşik durum). False → yazım başarısız (çağıran fail-closed)."""
        try:
            atomic_write_json(self.cfg.state_path / "risk.json", {"generated_at": iso(now), "mode": self.mode_state.mode.value, **self.risk.snapshot(state),
                                                                   "last_decisions": risk_log[-50:]})
            return True
        except Exception as exc:  # noqa: BLE001 — risk durumu yazılamıyorsa yeni giriş kabul edilmez; çıkışlar etkilenmez
            log.error("risk.json yazılamadı: %s — yeni girişler bu turda kapalı (fail-closed)", exc)
            return False

    def _refresh_after_fill(self, marks: dict[str, TickData], risk_log: list[dict], now: datetime):
        """PAPER fill sonrası: yetkili defterlerden portföy durumunu YENİDEN hesapla (aynı turdaki sonraki aday bunu görür) ve risk.json'u
        atomik güncelle. Dönen (state, entries_allowed)."""
        state = self._portfolio_state({k: float(v.last) for k, v in marks.items()})
        ok = self._persist_risk_state(state, risk_log, now)
        return state, ok

    # ------------------------------------------------------------------ tarihsel pattern kanıtı
    def _load_pattern_engine(self):
        """HistoryStore (cache/history) içindeki futures 4h serilerinden SimilarPatternEngine kur (bir kez, hata → None, fail-safe)."""
        if self._pattern_loaded:
            return self._pattern_engine
        self._pattern_loaded = True
        try:
            hc = self.cfg.v3.history
            if not hc.enabled:
                return None
            from .history import HistoryStore
            from .patterns import SimilarPatternEngine
            store = HistoryStore(self.cfg.cache_path / hc.root_dir)
            series = [(m, s, t) for m, s, t in store.series() if m == "futures" and t == "4h"]
            if not series:
                return None
            clusters = {s: name for name, syms in (self.cfg.v3.risk_profiles.clusters or {}).items() for s in (syms or [])}
            eng = SimilarPatternEngine(min_sample=30, horizon=self.head_cfg.funding_horizon_bars * 2, fee_pct=self.head_cfg.fee_taker_pct,
                                       slippage_pct=self.head_cfg.slippage_pct, clusters=clusters)
            btc = store.read("futures", "BTC/USDT", "4h")
            n = 0
            for m, s, t in series:
                df = store.read(m, s, t)
                if len(df) < 200:
                    continue
                fund = store.read("futures", s, "funding")
                n += eng.add_series(s, m, t, df, btc_df=btc if (s != "BTC/USDT" and len(btc)) else None, funding_df=fund if len(fund) else None)
            self._pattern_engine = eng if n else None
            log.info("pattern index: %d olay, %d seri", n, len(series))
        except Exception as exc:  # noqa: BLE001 — kanıt yoksa Coin Head kanıtsız çalışır (specialist usable=False)
            log.warning("pattern index kurulamadı: %s", exc)
            self._pattern_engine = None
        return self._pattern_engine

    def _pattern_evidence(self, symbol: str, now_ms: int) -> dict | None:
        """Sembol için LONG/SHORT kanıtı; veri 3 bardan eskiyse (bayat) kanıt verilmez. state/evidence/<sym>.json'a paket + açıklama yazılır."""
        eng = self._load_pattern_engine()
        if eng is None or (symbol, "futures", "4h") not in eng.candles:
            return None
        try:
            last_ts = int(eng.candles[(symbol, "futures", "4h")]["timestamp"].iloc[-1])
            if now_ms - last_ts > 3 * 14_400_000:
                return None
            from .patterns import explain_tr, packet_from_query
            ev = {side: eng.query(symbol, "futures", "4h", side, k=60) for side in ("LONG", "SHORT")}
            packets = {side: packet_from_query(r, decision_id=stable_id("evidence", self.run_id, symbol, side), timestamp=iso(utc_now()), timeframes=["4h"]) for side, r in ev.items()}
            atomic_write_json(self.cfg.state_path / "evidence" / f"{symbol.replace('/', '_')}.json",
                              {"symbol": symbol, "run_id": self.run_id, "generated_at": iso(utc_now()), "packets": {s: p.to_dict() for s, p in packets.items()},
                               "explanation_tr": {s: explain_tr(p) for s, p in packets.items()}, "neighbors": {s: r.get("neighbors", [])[:10] for s, r in ev.items()}}, indent=1)
            return ev
        except Exception as exc:  # noqa: BLE001
            log.warning("%s pattern kanıtı üretilemedi: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------ offline gap uzlaştırması (süreç başına bir kez)
    def ensure_gap_reconciled(self) -> None:
        """Restart sonrası ilk çalışmada kesinti penceresini uzlaştırır: kaçan stop/TP/liq/funding olayları
        arşiv mumlarıyla olay-zamanında işlenir; veri belirsizse GAP_AMBIGUOUS → yeni giriş yok (fail-closed)."""
        with self._exit_lock:
            if self._gap_checked:
                return
            self._gap_checked = True
            from .ops.gap import GapReconciler
            factory = self._gap_provider_factory
            if factory is None:
                def factory():
                    from .market.http import HttpClient
                    from .market.providers import BinanceFuturesProvider
                    from .market.ratelimit import BudgetPool
                    pool = BudgetPool(safety=self.cfg.v3.data.rate_budget_safety)
                    return BinanceFuturesProvider(HttpClient(BinanceFuturesProvider.base_url, pool.get("fapi.binance.com")))
            try:
                rep = GapReconciler(self.ledger2, self.ledger_path, self.cfg.state_path, factory).reconcile(self.run_id or None)
            except Exception as exc:  # noqa: BLE001 — uzlaştırıcı hatası fail-closed: giriş yok, çıkışlar canlı yoldan sürer
                log.exception("gap-reconcile hatası: %s", exc)
                self._gap_blocked = True
                return
            self._gap_blocked = bool(rep.blocked)
            spot_open = self.spot2.positions()
            if spot_open:
                log.warning("gap-reconcile spot defterini KAPSAMAZ; %d açık spot pozisyonu canlı tick ile değerlenecek", len(spot_open))
            for rec in rep.closed:
                legacy = rec.to_legacy_dict()
                snap = self.last_decisions.get(rec.symbol) or {}
                try:
                    self.learner.learn(legacy)
                    self.learner2.on_trade_closed(legacy | {"features": legacy.get("features") or {}},
                                                  {"regime": snap.get("regime"), "consensus_score": snap.get("consensus_score"),
                                                   "dissent": snap.get("dissent"), "vetoes": snap.get("vetoes")})
                    self._journal_outcome(legacy)
                except Exception as exc:  # noqa: BLE001
                    log.exception("gap-reconcile öğrenme hatası: %s", exc)

    # ------------------------------------------------------------------ hızlı çıkış monitörü (tur beklemeden)
    def exit_check(self) -> list[dict]:
        """Açık pozisyonlar için canlı fiyatla stop/TP/likidasyon/zaman kontrolü + defter kaydı + öğrenme; tur/tarama beklemez.
        Yeni giriş AÇMAZ. Dönen: kapanan işlemlerin legacy dict'leri."""
        self.ensure_gap_reconciled()
        with self._exit_lock:
            if not self.ledger2.positions:
                return []
            marks: dict[str, TickData] = {}
            for sym in list(self.ledger2.positions):
                try:
                    snap = self.runner.live.snapshot(sym) or {}
                    px = float(((snap.get("ticker") or {}).get("last")) or 0)
                    if px > 0:
                        marks[sym] = TickData(last=Decimal(str(px)), mark=Decimal(str(px)))
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s exit-monitor fiyat alınamadı: %s", sym, exc)
            if not marks:
                return []
            now = utc_now()
            records = self.ledger2.tick(marks, now_utc=now, bar_advance=False)
            self.ledger2.save(self.ledger_path)
            from .ops.gap import write_watermark
            write_watermark(self.cfg.state_path, now, self.run_id or None)
            out = []
            for rec in records:
                legacy = rec.to_legacy_dict()
                snap = self.last_decisions.get(rec.symbol) or {}
                try:
                    self.learner.learn(legacy)
                    self.learner2.on_trade_closed(legacy | {"features": legacy.get("features") or {}}, {"regime": snap.get("regime"), "consensus_score": snap.get("consensus_score"),
                                                                                                        "dissent": snap.get("dissent"), "vetoes": snap.get("vetoes")})
                    self._journal_outcome(legacy)
                except Exception as exc:  # noqa: BLE001 — öğrenme hatası defteri geri almaz
                    log.exception("exit-monitor öğrenme hatası: %s", exc)
                out.append(legacy)
                log.info("exit-monitor: %s %s kapandı (%s) net %.4f", rec.symbol, rec.side, rec.exit_reason, float(rec.net_pnl))
            if records:
                try:
                    state = self._portfolio_state({k: float(v.last) for k, v in marks.items()})
                    self._persist_risk_state(state, [], now)
                except Exception as exc:  # noqa: BLE001
                    log.warning("exit-monitor risk durumu yazılamadı: %s", exc)
            return out

    def _marks(self, briefs: list[CoinBrief]) -> dict[str, TickData]:
        out: dict[str, TickData] = {}
        for b in briefs:
            if not b.price:
                continue
            frames = self.runner.last_frames.get(b.symbol) or {}
            h1 = frames.get("1h")
            hi = lo = None
            if h1 is not None and len(h1):
                hi, lo = float(h1["high"].iloc[-1]), float(h1["low"].iloc[-1])
                # sağlamlık: 1h uçları canlı fiyatla tutarsızsa (ölçek/veri farkı) kullanma
                if not (0.8 * b.price <= lo <= hi <= 1.2 * b.price):
                    hi = lo = None
                else:
                    hi, lo = max(hi, b.price), min(lo, b.price)
            mk = next((r for r in b.reports if r.agent == "market"), None)
            mark = None
            if mk and mk.metrics.get("mark"):
                mark = mk.metrics["mark"]
            out[b.symbol] = TickData(last=Decimal(str(b.price)), mark=Decimal(str(mark)) if mark else None,
                                     high=Decimal(str(hi)) if hi else None, low=Decimal(str(lo)) if lo else None, ts=iso())
        for sym, p in self.ledger2.positions.items():
            if sym not in out and p.last_price:
                out[sym] = TickData(last=p.last_price, ts=iso())
        return out

    def _quality_for(self, symbol: str, now_ms: int) -> dict:
        frames = self.runner.last_frames.get(symbol) or {}
        h4 = frames.get("4h")
        if h4 is None:
            return {"ok": False, "verdict": "DATA_INVALID", "issues": ["MISSING_4H_FRAME"], "sources": []}
        try:
            rep = self.quality.check_klines(h4.reset_index(drop=True) if "timestamp" in h4 else h4, "4h", now_ms)
            return {"ok": rep.ok, "verdict": rep.verdict, "issues": list(rep.codes), "sources": ["frames"]}
        except Exception as exc:  # noqa: BLE001 — kalite kapısı hatası veri geçersiz sayılır (fail-closed)
            return {"ok": False, "verdict": "DATA_INVALID", "issues": [f"QUALITY_CHECK_ERROR:{type(exc).__name__}"], "sources": []}

    def _load_legacy_ledger(self):
        """v3: `futures_ledger.json`'ın tek sahibi FuturesLedgerV2 (`self.ledger2`, __init__ içinde hemen atanır);
        legacy v1 yükleyici bu yolu ne okur ne yazar."""
        return None

    # ------------------------------------------------------------------ TUR
    def tour(self, *, do_scan: bool = True, symbols_override: list[str] | None = None, charts: bool = True, obsidian: bool = True) -> dict:
        t0 = time.time()
        self.run_id = run_id_now()
        self._tour_no += 1
        now = utc_now()
        now_ms = int(now.timestamp() * 1000)
        st = self.cfg.state_path
        # 0) heartbeat + kill switch tetikleri
        atomic_write_json(st / "heartbeat.json", {"at": iso(now), "run_id": self.run_id, "pid": __import__("os").getpid()})
        # 0.5) restart sonrası kesinti penceresi uzlaştırması (süreç başına bir kez; belirsizse giriş kilidi)
        self.ensure_gap_reconciled()
        # 1) TARA (legacy tier-1)
        scan = None
        if self.scanner and do_scan and symbols_override is None:
            try:
                scan = self.scanner.scan()
                from .scanner import persist_scan
                persist_scan(scan, st)
                self.last_scan, self.last_scan_at = scan, time.time()
            except Exception as exc:  # noqa: BLE001 — tarama hatası turu durdurmaz; kayıt altına alınır
                log.exception("Tarama hatası: %s", exc)
                scan = self.last_scan
        scan_map = {r.symbol: r for r in (scan.setups if scan else [])}
        core = list(self.cfg.scanner.core_coins) if self.scanner else list(self.cfg.coins)
        symbols = symbols_override or list(dict.fromkeys(core + [r.symbol for r in (scan.setups if scan else [])] + list(self.ledger2.positions)))
        core_set = set(self.cfg.coins) | set(core)
        # 2) legacy ajanlar → brief + raporlar
        self.runner.set_weights(self.learner.learned_agent_weights())
        analyses = self._load_last_analyses()
        briefs: list[CoinBrief] = []
        for s in symbols:
            pre = None
            if s not in core_set:
                try:
                    pre = self.perp_frames(s)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s perp verisi alınamadı: %s", s, exc)
            try:
                b = self.runner.run_symbol(s, analyses.get(s), pre)
            except Exception as exc:  # noqa: BLE001
                log.exception("%s ajan hatası: %s", s, exc)
                continue
            if s in scan_map:
                b.scan_score, b.scan_direction = scan_map[s].score, scan_map[s].direction
            briefs.append(b)
        marks = self._marks(briefs)
        marks_f = {k: float(v.last) for k, v in marks.items()}
        state = self._portfolio_state(marks_f)
        trips = self.risk.evaluate_kill_triggers(state, {"stale_data": False})
        if trips:
            log.error("KILL SWITCH tetiklendi: %s", trips)
        # 3) COIN HEADS
        btc_frames = self.runner.last_frames.get("BTC/USDT")
        eth_frames = self.runner.last_frames.get("ETH/USDT")
        btc_regime = None
        inputs: dict[str, CoinHeadInputs] = {}
        snap_id = stable_id("snap", self.run_id)   # opak kimlik; sıralama snapshot_at_ms/snapshot_seq ile yapılır
        same_dir = {"LONG": sum(1 for p in state.open_positions if p.side == "LONG"), "SHORT": sum(1 for p in state.open_positions if p.side == "SHORT")}
        for b in briefs:
            frames = self.runner.last_frames.get(b.symbol) or {}
            live = dict(self.runner.live.snapshot(b.symbol)) if b.price else {}
            edge = None
            a = analyses.get(b.symbol)
            if a is not None:
                edge = {"has_edge": bool(a.has_edge), "oos_sharpe": (a.test_metrics or {}).get("sharpe"), "oos_trades": (a.test_metrics or {}).get("trades")}
            opos = self.ledger2.positions.get(b.symbol)
            f_fut = self.filters.get(b.symbol, MarketType.USDM_PERP)
            f_spot = self.filters.get(b.symbol, MarketType.SPOT)
            inputs[b.symbol] = CoinHeadInputs(frames=frames, live=live, legacy_reports=b.reports, legacy_brief=b, availability=self._availability(b.symbol),
                                              quality=self._quality_for(b.symbol, now_ms), btc_frames=btc_frames, eth_frames=eth_frames, btc_regime=btc_regime,
                                              portfolio={"same_direction_open": same_dir, "net_exposure": {b.symbol: state.net_exposure(b.symbol)},
                                                         "kill_switch_active": not self.killswitch.allows_entry(),
                                                         "open_position": {"side": opos.side.value} if opos else None},
                                              edge=edge, filters={"futures": {"min_notional": float(f_fut.min_notional), "max_leverage": min(f_fut.max_leverage, self.profile.futures_max_leverage)},
                                                                  "spot": {"min_notional": float(f_spot.min_notional)}},
                                              run_id=self.run_id, snapshot_id=snap_id, now_ms=now_ms,
                                              snapshot_at_ms=now_ms, snapshot_seq=self._tour_no,
                                              pattern_evidence=self._pattern_evidence(b.symbol, now_ms))
        decisions = self.registry.run_many(inputs)
        # ASAMA 2 -- EKONOMIK FIRSAT DEGERLENDIRMESI. Coin head yalnizca GEOMETRIK olarak gecerli plan
        # uretti; kabul/red karari burada tek bir buyuklukle verilir: conservative_net_edge_r.
        self._assess_opportunities(decisions, briefs)
        btc_dec = decisions.get("BTC/USDT")
        chief = self.chief_mgr.decide(list(decisions.values()), {"equity": state.equity, "open_positions": [o.to_dict() for o in state.open_positions],
                                                                 "total_open_risk_usdt": state.total_open_risk_usdt,
                                                                 # ADVISORY projeksiyon YETKILI kapiyla ayni kovayi olcsun:
                                                                 # birlesik toplam kullanilirsa panel "sigmaz" derken motor kabul eder.
                                                                 "futures_stop_risk_usdt": state.futures_stop_risk_usdt,
                                                                 "spot_exposure_usdt": state.spot_exposure_usdt,
                                                                 "pnl_today": state.realized_pnl_today,
                                                                 "drawdown_pct": state.drawdown_pct}, btc_regime=btc_dec.regime if btc_dec else None)
        self.registry.chief = chief.to_dict()
        # legacy chief (obsidian/alerts için) — v3 chief modunu yansıt
        legacy_chief = self.runner.chief.decide(briefs)
        legacy_chief.generated_at = iso(now)
        legacy_chief.risk_mode = chief.market_risk_mode
        # p_win (v2 model + hiyerarşik önsel; v1 tahmini yedek)
        from .learn.snapshot import prediction_schema_hash
        self._pred_snapshots = {}
        self._influence_log = []
        self._shadow_syms = set()
        for b in briefs:
            f = features_from_brief(b, legacy_chief, b.scan_score or None)
            d = decisions.get(b.symbol)
            # TRAIN/SERVE PARITESI: karar ani snapshot'i tahminden ONCE uretilir; ayni nesne giris
            # kaydinda yeniden kullanilir. Boylece modelin egitildigi vektor ile serve edilen vektor
            # ayni builder'dan cikar. (`d.p_win` burada hala HEAD on tahmini -- model henuz ezmedi.)
            snap = self._snapshot_v3(b.symbol, d)
            if snap is not None:
                self._pred_snapshots[b.symbol] = snap
                pr = self.learner2.predict(snap.prediction_vector(), regime=d.regime if d else None, symbol=b.symbol,
                                           setup=b.plan.entry_type or None, schema_hash=prediction_schema_hash())
            elif d is not None and d.active_plan is not None:
                # plan var ama snapshot uretilemedi -> legacy koprü; v3 sampiyon varsa sema uyusmazligi sayilir
                pr = self.learner2.predict(f, regime=d.regime, symbol=b.symbol, setup=b.plan.entry_type or None)
            else:
                pr = self.learner2.prior_only(regime=d.regime if d else None, symbol=b.symbol, setup=b.plan.entry_type or None)
            baseline_p_win = round(pr.p_win_calibrated if pr.ready else (0.5 * pr.prior_used + 0.5 * self.learner.predict(f)), 3)
            # --- Outcome Learning Loop: geçmiş deneyimden SINIRLI ayarlama ---------------
            # SHADOW (varsayılan): hesaplanır ve kaydedilir, baseline BİREBİR korunur.
            # PAPER_BOUNDED: yalnız PAPER'da, yalnız p_win üzerinde, `max_fraction` tavanıyla.
            # Hard veto / risk kapısı / kill switch bu değerden BAĞIMSIZDIR ve geçilemez.
            b.p_win = baseline_p_win
            inf = self._learning_influence(b, d, snap, baseline_p_win, f)
            if inf is not None:
                self._influence_log.append(inf)
                if inf.get("applied") and inf.get("effective") is not None:
                    b.p_win = round(float(inf["effective"]), 3)
            if d:
                d.p_win = b.p_win
        # 4) RİSK + TETİK + PAPER EXECUTION
        opened: list[str] = []
        risk_log: list[dict] = []
        self._journal_cycle = getattr(self, "_tour_no", 0)
        if self.cfg.futures.enabled and self.mode_state.mode.value in ("PAPER", "TESTNET", "SHADOW_LIVE"):
            opened, risk_log = self._execute(decisions, chief, briefs, state, marks, now)
            # bu turda açılan pozisyonlar için önceki barın uçları geçerli değil → yalnız son fiyat
            for desc in opened:
                sym = desc.split(" ")[0]
                if sym in marks:
                    marks[sym] = TickData(last=marks[sym].last, mark=marks[sym].mark, ts=marks[sym].ts)
        # 5) İZLE: tick (bar_advance yeni 4h bar kapanışında)
        funding = {}
        for b in briefs:
            mk = next((r for r in b.reports if r.agent == "market"), None)
            if mk and "funding_pct" in mk.metrics:
                funding[b.symbol] = Decimal(str(mk.metrics["funding_pct"])) / Decimal(100)
        cur_bar = max((b.last_bar_4h for b in briefs if b.last_bar_4h), default="")
        bar_advance = bool(cur_bar and cur_bar != self.last_bar_seen)
        if cur_bar:
            self.last_bar_seen = cur_bar
        records = self.ledger2.tick(marks, now_utc=now, funding_rate_lookup=static_rates(funding), bar_advance=bar_advance)
        # 6) KAYIT SIRASI: önce defter, sonra öğrenme (crash penceresinde çift öğrenme olmasın)
        self.ledger2.save(self.ledger_path)
        from .ops.gap import write_watermark
        write_watermark(st, now, self.run_id or None)
        self.spot2.tick(marks_f, now)
        self.spot2.save(st / "spot_ledger.json")
        self._notify_closed(records, now)
        lessons = []
        for rec in records:
            legacy = rec.to_legacy_dict()
            snap = self.last_decisions.get(rec.symbol) or {}
            lessons.append(self.learner.learn(legacy))
            self.learner2.on_trade_closed(legacy | {"features": legacy.get("features") or {}}, {"regime": snap.get("regime"), "consensus_score": snap.get("consensus_score"),
                                                                                                "dissent": snap.get("dissent"), "vetoes": snap.get("vetoes")})
            self._journal_outcome(legacy, lessons[-1] if lessons else None)
        # gölge işlemleri etiketle (araştırma politikasının elediği girişlerin karşı-olgusal sonucu burada oluşur)
        self._label_shadows()
        # kapanan gerçek işlemleri araştırma adayına eşleşmiş gözlem olarak yaz
        for rec in records:
            self._observe_research_close(rec)
        # ÖĞRENME TEMPOSU: her kapanışta yeniden eğitim YOK — asgari yeni kapanış + cooldown kapısı.
        # OTOMATİK TERFİ YOK: yeni model yalnız CANDIDATE olarak kaydedilir; CHAMPION'a geçiş açık
        # manuel operatör onayı ister (`python -m tradingbot learning-promote --operator <ad>`).
        self._closes_since_train += len(records)
        if self.cfg.v3.learning_v3.enabled and self._training_due(now):
            try:
                out = self.learner2.train_challenger(now=now)
                if out:
                    self._last_train_at, self._closes_since_train = now, 0
                    log.info("challenger eğitildi → CANDIDATE %s (terfi YOK, manuel onay gerekir)", out.get("model_id"))
            except (ValueError, TypeError) as exc:
                log.warning("challenger eğitimi atlandı: %s", exc)
        # araştırma adayı: kapılar geçilirse aktifleşir, kötüleşirse baseline'a dönülür
        # ARAŞTIRMA DÖNGÜSÜ — tek orkestrasyon noktası: kayıp analizinden aday üret → offline
        # walk-forward → SHADOW → istatistik kapıları geçilirse ACTIVE → kötüleşirse RETIRED +
        # baseline. Mod kapısı geçilmezse katman SALT-OKUNUR; hiçbir durum geçişi yapılmaz.
        try:
            _res = self.research_coordinator.tick(
                now=now, mode_value=self.mode_state.mode.value,
                gateway=self.cfg.v3.execution.gateway,
                live_order_path_enabled=self.mode_state.is_live_order_path_enabled(),
                n_new_closes=len(records))
            if _res.get("ran"):
                log.info("araştırma turu: %s", {k: _res.get(k) for k in ("status", "code", "proposed", "verdict")})
            if _res.get("activated"):
                log.info("araştırma adayı PAPER_RESEARCH_ACTIVE: %s", _res["activated"])
        except Exception as exc:  # noqa: BLE001 — araştırma katmanı işlem akışını DURDURAMAZ
            log.warning("araştırma döngüsü atlandı: %s", exc)
        # 7) görseller (legacy)
        chart_paths = {}
        if charts:
            for b in briefs:
                if b.verdict != "BEKLE" or b.symbol in self.ledger2.positions or b.symbol in core_set or b.scan_score:
                    chart_paths[b.symbol] = self._chart(b)
        # 8) durum dosyaları
        self.last_decisions = {s: d.to_dict(include_reports=False) for s, d in decisions.items()}
        self.registry.save(st, self.run_id)
        state = self._portfolio_state(marks_f)      # tur sonu: fill/çıkış sonrası güncel birleşik durum
        self._persist_risk_state(state, risk_log, now)
        # Karar günlüğü: DEĞERLENDİRİLEN HER aday (kabul/red/veto) tek seferde yazılır.
        # Hot loop'un DIŞINDA, tur sonunda ve fail-safe: arıza turu bozmaz.
        self._journal_decisions(risk_log, decisions, now)
        self.mode_state.save()
        from .agents import persist_agents
        _, alerts = persist_agents(briefs, legacy_chief, st)
        for o in opened:
            alerts.append(f"📈 KAĞIT POZİSYON AÇILDI: {o}")
        for l in lessons:
            alerts.append(f"{'✅' if l['won'] else '❌'} KAPANDI {l['symbol']} {l['side']} {l['r']:+.2f}R ({l['exit']}) — {l['why'][0][:120]}")
        for code in trips:
            alerts.append(f"🛑 KILL SWITCH: {code}")
        # 9) Obsidian
        if obsidian:
            self._write_obsidian(briefs, legacy_chief, alerts, scan, chart_paths, analyses)
            self._write_obsidian_v3(decisions, chief, briefs, state, chart_paths, alerts)
            if self.cfg.obsidian.git_sync:
                self._git_sync()
        # 10) health
        health = {"state": "KILL_SWITCH" if self.killswitch.active else "HEALTHY", "at": iso(now), "run_id": self.run_id, "seconds": round(time.time() - t0, 1),
                  "symbols": len(symbols), "decisions": len(decisions), "opened": len(opened), "closed": len(records), "kill_trips": trips,
                  "mode": self.mode_state.mode.value, "profile": self.profile.name}
        atomic_write_json(st / "health.json", health)
        self._notify_health(str(health.get("state") or "UNKNOWN"), str(health.get("summary") or ""), now)
        self._notify_maintenance(health, now)
        self._persist_funnel(now, len(records))
        self.snap_telemetry.save()          # snapshot/sema sayaclari dashboard ve /metrics icin
        summary = {"at": iso(now), "run_id": self.run_id, "symbols": symbols,
                   "scan": {"universe": scan.universe, "scanned": scan.scanned, "flagged": scan.flagged, "setups": len(scan.setups)} if scan else None,
                   "chief": f"{chief.market_risk_mode} · BTC {chief.btc_eth_regime.get('btc')} · {chief.breadth['long']} LONG / {chief.breadth['short']} SHORT / "
                            f"{chief.breadth['no_trade']} NO_TRADE / {chief.breadth['data_invalid']} DATA_INVALID · izin: {len(chief.priority)}",
                   "opened": opened, "closed": [l["symbol"] for l in lessons], "ledger": self.ledger2.summary(marks),
                   "learning": self.learner.snapshot() | {"v2": self.learner2.snapshot()}, "risk": {"profile": self.profile.name, "killswitch": self.killswitch.state, "trips": trips},
                   "seconds": round(time.time() - t0, 1)}
        return summary

    # ------------------------------------------------------------------ uygulama
    def _execute(self, decisions, chief, briefs: list[CoinBrief], state, marks: dict[str, TickData], now: datetime) -> tuple[list[str], list[dict]]:
        with self._entry_lock:          # aday değerlendirme→fill→durum yenileme tek seri kritik bölge (reservation/commit)
            out = self._execute_locked(decisions, chief, briefs, state, marks, now)
        # O-4: TELEGRAM HTTP'si KİLİT DIŞINDA. Kritik bölgede yalnız defter kaydı ve hızlı/yerel
        # outbox yazımı yapılır; yavaş bir taşıma giriş kilidini TUTMAZ ve açılmış işlemi geri almaz.
        if getattr(self, "notifier", None) and self.notifier.enabled:
            self.notifier.flush()
        return out

    def _execute_locked(self, decisions, chief, briefs: list[CoinBrief], state, marks: dict[str, TickData], now: datetime) -> tuple[list[str], list[dict]]:
        """GERCEK KARAR SIRASI (denetim sonrasi):

            degerlendirme -> siralama -> TETIK -> ekonomi -> DUPLICATE -> arastirma politikasi
            -> BUTUN boyut carpanlari -> NIHAI notional/risk -> YETKILI risk kapasitesi
            -> ledger/borsa acilisi

        Iki mimari hata bilincli olarak kapatildi:

        1. **Chief risk REZERVE ETMIYOR.** Eskiden `ChiefPortfolioManager.decide()` siralamadaki
           her adayin riskini hemen dusuyordu; tetik/duplicate/politika/emir kontrolleri ise burada
           daha sonra yapiliyordu. Tetiklenmeyen en guclu aday kapasiteyi yiyor, gercekten tetiklenen
           sonraki aday `RISK_CAPACITY_BLOCKED` aliyordu. Artik kapasite YALNIZ `RiskEngine.evaluate()`
           icinde ve YALNIZ gercekten acilmis pozisyonlarin riskine karsi zorlanir; acilis
           basarisizsa hicbir sey tuketilmez, sonraki aday degerlendirilmeye devam eder.
        2. **RiskEngine NIHAI boyutu goruyor.** Eskiden `risk.evaluate()` ham `plan.notional` ile
           cagriliyor, notional daha SONRA kucultuluyordu; nihai riski %0.5 olan dort islem risk
           motorunda dort adet %2 islem gibi gorunuyor ve kaldirilan islem kotasi yapay risk
           kitligi olarak geri geliyordu. Artik butun carpanlar (firsat x chief yumusak cezasi x
           arastirma politikasi) once uygulanir, `final_notional`/`final_risk_*` uretilir ve risk
           motoru TAM OLARAK bu degerleri degerlendirir.

        Ayni turda daha once basariyla acilmis pozisyonlarin riski `_refresh_after_fill()` ile
        yetkili defterlerden yeniden okunur; boylece sonraki adayin kapasite hesabina girer.
        """
        opened: list[str] = []
        risk_log: list[dict] = []
        bmap = {b.symbol: b for b in briefs}
        # kritik bolge basinda durumu YETKILI defterlerden yenile (caginin state'i bayat olabilir: retry/eszamanli yol)
        state = self._portfolio_state({k: float(v.last) for k, v in marks.items()})
        # RESTART BACKLOG: mevcut acik pozisyonlar icin SAHTE "yeni islem acildi" bildirimi YOK.
        # (Bir kez calisir; bu pozisyonlar KAPANDIGINDA gercek kapanis bildirimi yine gonderilir.)
        if getattr(self, "notifier", None):
            self.notifier.bootstrap_open_positions([p.to_dict() for p in self.ledger2.positions.values()])
        entries_allowed = True
        funnel = self._funnel = {k: 0 for k in _FUNNEL_KEYS}
        funnel["actionable"] = sum(1 for d in decisions.values() if d.is_actionable)
        self._opportunity_cost = []
        # SIRALAMA: adaylarin TAMAMI islenmeden once muhafazakar edge'e gore sirali islenir; boylece
        # daha guclu ucuncu firsat, daha zayif iki firsat yuzunden disarida kalmaz. Sabit kota YOK.
        _order = list(chief.priority) + [s for s, d in decisions.items()
                                         if d.is_actionable and s not in chief.priority]
        _order.sort(key=lambda s: (-((getattr(decisions.get(s), "opportunity", None) or {})
                                     .get("conservative_net_edge_r") or -9.0), s))
        for sym in _order:
            d = decisions.get(sym)
            b = bmap.get(sym)
            if d is None or b is None or not d.is_actionable:
                continue
            if not entries_allowed:     # onceki fill sonrasi risk durumu yazilamadi -> yeni giris yok (fail-closed); cikislar tick'te surer
                risk_log.append({"symbol": sym, "verdict": d.verdict.value, "risk_allowed": False, "risk_reasons": ["RISK_STATE_PERSIST_FAILED"], "at": iso(now)})
                continue
            if self._stopping():        # kooperatif durdurma istendi -> yeni giris kabul edilmez
                risk_log.append({"symbol": sym, "verdict": d.verdict.value, "risk_allowed": False, "risk_reasons": ["SHUTDOWN_REQUESTED"], "at": iso(now)})
                continue
            if self._gap_blocked:       # kesinti penceresi uzlastirilamadi (GAP_AMBIGUOUS) -> yeni giris yok (cikislar surer)
                risk_log.append({"symbol": sym, "verdict": d.verdict.value, "risk_allowed": False, "risk_reasons": ["GAP_RECONCILE_PENDING"], "at": iso(now)})
                continue
            perm = chief.permission.get(sym, {})
            plan = d.active_plan
            if plan is None or not plan.valid:
                continue
            market = "SPOT" if d.verdict == Verdict.SPOT_LONG else "USDM_PERP"
            funnel["ranked"] += 1
            entry = {"symbol": sym, "verdict": d.verdict.value, "chief_allow": bool(perm.get("allow")), "chief_reason": perm.get("reason"),
                     "chief_capacity_projection": perm.get("capacity_projection"),
                     "plan_notional": round(float(plan.notional or 0.0), 6),
                     "risk_allowed": None, "risk_reasons": [], "risk_warnings": [], "adjusted_notional": None,
                     "adjusted_leverage": None, "at": iso(now)}
            risk_log.append(entry)
            # ---------------------------------------------------------------- 1) CHIEF (siralama + SERT red-team)
            # Chief kapasite REZERVE ETMEZ; buradaki tek sert kaynagi gercek red-team hard veto'sudur.
            if not perm.get("allow"):
                funnel["chief_blocked"] += 1
                if perm.get("block_code"):
                    funnel["hard_safety_blocked"] += 1
                entry["block_code"] = perm.get("block_code") or "CHIEF_BLOCKED"
                entry["hard_veto"] = bool(perm.get("block_code"))
                if self.cfg.v3.learning_v3.shadow_trades and plan.expected_r >= self.head_cfg.min_expected_r:
                    self._shadow_add({"plan_id": stable_id("plan", self.run_id, sym), "symbol": sym, "market_type": market, "direction": d.direction, "entry": plan.entry,
                                     "stop": plan.stop, "targets": plan.targets, "horizon_bars": plan.time_horizon_bars, "leverage": plan.size.leverage},
                                    ["CHIEF:" + str(perm.get("reason"))], now=now)
                continue
            # ---------------------------------------------------------------- 2) TETIK (kapasite TUKETMEZ)
            # tetik: legacy mantik (kirilim: 4h kapanis seviyenin otesinde; geri cekilme: fiyat seviyeye degdi)
            if not self._trigger_fired(b, d.direction, plan.entry, plan.entry_type):
                funnel["no_trigger"] += 1
                entry["block_code"] = "NO_TRIGGER"
                continue
            funnel["trigger_fired"] += 1
            feats = features_from_brief(b, self.runner.chief.decide(briefs), b.scan_score or None)
            feats.update({"initial_stop": plan.stop, "p_win": b.p_win, "regime": d.regime, "consensus_score": d.consensus_score, "consensus_conf": d.consensus_confidence,
                          "n_dissent": len(d.dissent), "n_vetoes": len(d.vetoes), "expected_r": d.expected_r, "expected_cost_pct": d.expected_cost, "market_type": market,
                          "spread_pct": next((r.metrics.get("spread_pct") for r in d.specialist_reports if r.agent_name == "orderbook_liquidity" and r.usable), None)})
            # ---------------------------------------------------------------- 3) EKONOMI (kapasite TUKETMEZ)
            # --- HARD: maliyet ve belirsizlik sonrasi ekonomi ---
            _opp = getattr(d, "opportunity", None) or {}
            if _opp:
                if _opp.get("tradeable"):
                    funnel["positive_conservative_edge"] += 1
                elif _opp.get("research_only"):
                    # Point-estimate pozitif ama belirsizlik yutuyor -> gercek giris YOK, karsi-olgusal izle.
                    funnel["research_small"] += 1
                    entry["block_code"] = "RESEARCH_SIZE_ONLY"
                    if self.cfg.v3.learning_v3.shadow_trades:
                        self._shadow_add({"plan_id": stable_id("plan", self.run_id, sym), "symbol": sym,
                                         "market_type": market, "direction": d.direction, "entry": plan.entry,
                                         "stop": plan.stop, "targets": plan.targets,
                                         "horizon_bars": plan.time_horizon_bars, "leverage": plan.size.leverage},
                                        ["RESEARCH_SIZE_ONLY"], now=now)
                    continue
                else:
                    funnel["negative_edge_blocked"] += 1
                    entry["block_code"] = "NEGATIVE_NET_EDGE"
                    continue
                if _opp.get("net_expectancy_r", 0) > 0:
                    funnel["positive_point_edge"] += 1
            # ---------------------------------------------------------------- 4) DUPLICATE (kapasite TUKETMEZ)
            # --- HARD: ayni benzersiz sinyalin tekrari (yeni bar/yeni setup ENGELLENMEZ) ---
            _sig = self._signal_id(sym, market, d, plan, b)
            if _sig in self._seen_signals:
                funnel["duplicate_blocked"] += 1
                entry["block_code"] = "DUPLICATE_SIGNAL"
                continue
            # ---------------------------------------------------------------- 5) ARASTIRMA POLITIKASI (kapasite TUKETMEZ)
            # ARASTIRMA POLITIKASI: chief siralamasindan SONRA calisir ve yalniz daraltabilir.
            # Aktif aday yoksa `allow=True, size_multiplier=1.0` doner -> davranis birebir ayni kalir.
            snap_v3 = self._pred_snapshots.get(sym)     # predict aninda uretildi; yeniden hesaplanmaz
            _rd2 = self._research_entry(sym, d, plan, snap_v3)
            res, shadow_res = _rd2["active"], _rd2["shadow"]
            entry["research_policy_id"] = res["policy_id"]
            entry["research_reasons"] = res["reasons"]
            entry["research_size_multiplier"] = res["size_multiplier"]
            entry["shadow_policy_id"] = shadow_res["policy_id"]
            entry["shadow_decision"] = {"allow": shadow_res["allow"],
                                        "size_multiplier": shadow_res["size_multiplier"],
                                        "reasons": shadow_res["reasons"]}
            if not res["allow"]:
                # Pozisyon ACILMAZ. Baseline'in ne yapacagi karsi-olgusal golge islemle izlenir;
                # golge etiketlendiginde eslesmis gozlem olarak adaya yazilir.
                funnel["research_policy_blocked"] += 1
                entry["block_code"] = "RESEARCH_POLICY_BLOCK"
                shs = self._shadow_add({"plan_id": stable_id("plan", self.run_id, sym), "symbol": sym, "market_type": market,
                                       "direction": d.direction, "entry": plan.entry, "stop": plan.stop,
                                       "targets": plan.targets, "horizon_bars": plan.time_horizon_bars,
                                       "leverage": plan.size.leverage},
                                      ["RESEARCH_POLICY_BLOCK"] + list(res["reasons"]), now=now)
                if shs and res["policy_id"]:
                    self.research.add_pending(res["policy_id"], shs[0].id,
                                              {"decision": {"allow": False, "size_multiplier": 0.0,
                                                            "reasons": res["reasons"]},
                                               "source": "counterfactual_shadow_trade",
                                               "symbol": sym, "side": d.direction})
                continue
            # ---------------------------------------------------------------- 6) BUTUN BOYUT CARPANLARI -> NIHAI BOYUT
            # DINAMIK BOYUT: firsat gucu (muhafazakar edge) x chief yumusak cezasi x arastirma politikasi.
            # Hepsi yalnizca KUCULTUR. Kucuk boyut min-notional'a uymuyorsa risk BUYUTULMEZ (risk
            # motorunda MIN_ORDER_CONFLICT ile reddedilir).
            _opp_mult = _as_multiplier(_opp.get("size_multiplier") if _opp else None)
            _res_mult = _as_multiplier(res.get("size_multiplier"))
            _chief_pen = float(perm.get("size_penalty_r") or 0.0)
            _chief_mult = max(0.25, 1.0 - min(0.5, _chief_pen * 2.0)) if _chief_pen > 0 else 1.0
            final_size_multiplier = round(_opp_mult * _chief_mult * _res_mult, 6)
            entry["opportunity"] = _opp or None
            entry["size_multiplier_parts"] = {"opportunity": _opp_mult, "chief_soft": round(_chief_mult, 6),
                                              "research": _res_mult}
            entry["size_multiplier_total"] = final_size_multiplier
            # SIFIR CARPAN ASLA EMIR ACMAZ (acikca verilen 0.0 artik 1.0'a yuvarlanmiyor).
            if final_size_multiplier <= 0.0:
                funnel["size_multiplier_zero"] += 1
                entry["block_code"] = "SIZE_MULTIPLIER_ZERO"
                continue
            final_notional = round(float(plan.notional or 0.0) * final_size_multiplier, 6)
            # UYGULAMA FIYATI: emir `plan.entry`den DEGIL, defterin referans fiyatindan (`b.price`)
            # ve aleyhte kaymayla dolar; stop ise plandan gelir. Risk kontrolu `plan.entry` ile
            # yapilirsa TASINAN risk ile OLCULEN risk farkli olur ve toplam acik risk fill sonrasi
            # profil tavanini ASABILIR. Bu yuzden risk motoru emrin gercekten dolacagi fiyati gorur
            # -> Chief telemetrisi, RiskEngine ve defter AYNI nihai risk degerini kullanir.
            exec_entry = self._execution_entry(sym, market, d.direction, b.price or plan.entry, marks.get(sym))
            _stop_frac = abs(exec_entry - plan.stop) / exec_entry if (exec_entry and plan.stop) else 0.0
            final_risk_usdt = round(final_notional * _stop_frac, 6)
            _eq = state.equity if self.profile.size_on_live_equity else state.starting_equity
            final_risk_pct = round(final_risk_usdt / _eq * 100.0, 6) if _eq else 0.0
            entry["final_notional"] = final_notional
            entry["final_risk_usdt"] = final_risk_usdt
            entry["final_risk_pct"] = final_risk_pct
            entry["execution_entry"] = round(exec_entry, 10)
            # ---------------------------------------------------------------- 6b) DINAMIK KALDIRAC (2x-5x)
            # Kaldirac notional'i DEGISTIRMEZ (notional risk butcesinden geldi); yalnizca
            # `initial_margin = notional / leverage` degerini belirler. Zayif sinyal `min_leverage`
            # ile ACILMAZ: taban kapilari gecilemezse aday NO_TRADE olur.
            lev_dec = None
            plan_leverage = int(plan.size.leverage or 1)
            if market == "USDM_PERP" and self.leverage_cfg.enabled:
                lev_dec = select_leverage(self._leverage_context(sym, d, plan, exec_entry, state, chief, _opp),
                                          self.leverage_cfg)
                entry["leverage_decision"] = lev_dec.to_dict()
                if not lev_dec.tradeable:
                    funnel["leverage_gate_blocked"] += 1
                    entry["block_code"] = "LEVERAGE_GATE_BLOCKED"
                    # REDDEDILEN AMA VERI/STOP ACISINDAN GECERLI ADAY -> salt GOZLEMSEL golge kayit.
                    # Gercek fill/ledger/emir URETMEZ; `is_counterfactual=True` ile ayri dosyada durur.
                    # Veri bayat/celiskili ya da stop bilinmiyorsa aday "gecerli" degildir: kayit YOK.
                    if (self.cfg.v3.learning_v3.shadow_trades
                            and not {"DATA_STALE", "DATA_CONFLICT", "STOP_UNKNOWN"} & set(lev_dec.blocked_higher)):
                        self._shadow_add({"plan_id": stable_id("plan", self.run_id, sym), "symbol": sym, "market_type": market,
                                         "direction": d.direction, "entry": plan.entry, "stop": plan.stop, "targets": plan.targets,
                                         "horizon_bars": plan.time_horizon_bars, "leverage": plan.size.leverage},
                                        ["LEVERAGE_GATE_BLOCKED"] + list(lev_dec.blocked_higher)[:6], now=now)
                    continue
                plan_leverage = lev_dec.leverage
            entry["leverage"] = plan_leverage
            # ---------------------------------------------------------------- 7) YETKILI RISK KAPASITESI (NIHAI degerlerle)
            plan_dict = {"symbol": sym, "market_type": market, "direction": d.direction, "entry": exec_entry, "stop": plan.stop, "targets": plan.targets,
                         "notional": final_notional, "margin": round(final_notional / max(plan_leverage, 1), 6),
                         "leverage": plan_leverage, "amount_type": "NOTIONAL", "expected_r": plan.expected_r,
                         "spread_pct": feats.get("spread_pct"),
                         "min_notional": float(self.filters.get(sym, MarketType.SPOT if market == "SPOT" else MarketType.USDM_PERP).min_notional)}
            rd = self.risk.evaluate(plan_dict, state, {"now_utc": now})
            entry.update({"risk_allowed": rd.allowed, "risk_reasons": rd.reasons, "risk_warnings": rd.warnings,
                          "adjusted_notional": rd.adjusted_notional, "adjusted_leverage": rd.adjusted_leverage,
                          "risk_usdt": rd.risk_usdt})
            if not rd.allowed:
                if any(c in _CAPACITY_CODES for c in rd.reasons):
                    funnel["risk_capacity_blocked"] += 1
                    entry["block_code"] = "RISK_CAPACITY_BLOCKED"
                    self._opportunity_cost.append({"symbol": sym, "side": d.direction,
                                                   "conservative_net_edge_r": _opp.get("conservative_net_edge_r"),
                                                   "final_risk_usdt": final_risk_usdt,
                                                   "reason": "RISK_CAPACITY_BLOCKED", "at": iso(now)})
                else:
                    entry["block_code"] = "RISK_ENGINE_BLOCKED"
                # guclu aday reddedildi -> golge islem (karsi-olgusal)
                if self.cfg.v3.learning_v3.shadow_trades and plan.expected_r >= self.head_cfg.min_expected_r:
                    self._shadow_add({"plan_id": stable_id("plan", self.run_id, sym), "symbol": sym, "market_type": market, "direction": d.direction, "entry": plan.entry,
                                     "stop": plan.stop, "targets": plan.targets, "horizon_bars": plan.time_horizon_bars, "leverage": plan.size.leverage},
                                    list(rd.reasons), now=now)
                continue
            funnel["capacity_approved"] += 1
            # Risk motoru yalnizca KUCULTUR: nihai boyutu asla buyutme.
            notional = min(final_notional, float(rd.adjusted_notional if rd.adjusted_notional is not None else final_notional))
            entry["executed_notional"] = round(notional, 6)
            # GOZLEM SOZLESMESI: stoptaki azami zarar UYGULANAN notional'dan hesaplanir.
            # Onceden `final_risk_usdt` (RISK_PER_TRADE kucultmesi ONCESI istenen notional) yaziliyordu;
            # kucultme devreye girdiginde metadata gercekte acilan pozisyondan DAHA BUYUK bir zarar
            # bildiriyordu (or. 1.1738 yazilirken gercek risk 0.9919). Kabul/red karari DEGISMEZ.
            applied_risk_usdt = round(notional * _stop_frac, 6)
            entry["applied_risk_usdt"] = applied_risk_usdt
            # ---------------------------------------------------------------- 8) LEDGER / BORSA ACILISI
            if market == "USDM_PERP":
                pos = self.ledger2.open(sym, d.direction, b.price, SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, int(rd.adjusted_leverage or 1)),
                                        stop=plan.stop, targets=plan.targets, filters=self.filters.get(sym, MarketType.USDM_PERP), setup_type=plan.entry_type,
                                        trigger_text=plan.entry_trigger, features=feats, tick=marks.get(sym), now=now,
                                        meta={"coin_head_id": d.coin_head_id, "run_id": self.run_id,
                                              "decision_snapshot": d.to_dict(include_reports=False),
                                              # KALDIRAC SNAPSHOT'I: pozisyon omru boyunca DEGISMEZ (restart dahil).
                                              "leverage_decision": (lev_dec.to_dict() if lev_dec else
                                                                    {"leverage": plan_leverage, "reasons": ["STATIC_PLAN_LEVERAGE"],
                                                                     "blocked_higher": ["DYNAMIC_LEVERAGE_DISABLED"]}),
                                              "risk_snapshot": {"final_notional": final_notional,
                                                                # UYGULANAN degerler (deftere giden):
                                                                "applied_notional": round(notional, 6),
                                                                "initial_margin": round(notional / max(plan_leverage, 1), 6),
                                                                "stop_frac": round(_stop_frac, 8),
                                                                # dolum sonrasi GERCEKLESEN degerle guncellenir (asagi bkz.)
                                                                "max_loss_at_stop_usdt": applied_risk_usdt,
                                                                "applied_risk_usdt": applied_risk_usdt,
                                                                # istenen (kucultme oncesi) — seffaflik icin AYRI alan
                                                                "requested_notional": final_notional,
                                                                "requested_risk_usdt": final_risk_usdt,
                                                                "risk_engine_risk_usdt": rd.risk_usdt,
                                                                "execution_entry": round(exec_entry, 10)}})
                if pos is None:
                    entry["exec_reject"] = self.ledger2.last_reject_reason
                    entry["block_code"] = "EXCHANGE_REJECTED"
                    funnel["exchange_rejected"] += 1
                    continue
                # GERCEKLESEN DOLUM: defter qty'yi lot adimina yuvarlar, bu yuzden dolan notional
                # istenen/uygulanan notional'dan KUCUK olabilir. Gozlem metadata'si defterin
                # GERCEKTEN actigi pozisyonu bildirir; kabul karari (rd) DEGISMEZ.
                _rs = (pos.meta or {}).get("risk_snapshot")
                if isinstance(_rs, dict) and pos.stop is not None:
                    _filled_notional = float(pos.qty) * float(pos.entry_avg)
                    _filled_risk = abs(float(pos.entry_avg) - float(pos.stop)) * float(pos.qty)
                    _rs.update({"filled_notional": round(_filled_notional, 6),
                                "max_loss_at_stop_usdt": round(_filled_risk, 6),
                                "initial_margin": round(_filled_notional / max(pos.leverage, 1), 6)})
                    entry["filled_risk_usdt"] = round(_filled_risk, 6)
                trade_id = pos.id
                entry["trade_id"] = trade_id
                desc = f"{sym} {d.direction} FUTURES @ {float(pos.entry_avg):.6g} · notional {float(pos.qty * pos.entry_avg):.2f} · {pos.leverage}x · stop {plan.stop:.6g} · TP {', '.join(f'{t:.6g}' for t in plan.targets)} · P(win) %{(b.p_win or 0.5)*100:.0f}"
            else:
                order = self.spot2.market_buy(sym, quote_amount=Decimal(str(notional)), ref_price=Decimal(str(b.price)), tick=marks.get(sym), strategy=plan.entry_type, now=now)
                if order is None or str(getattr(order, "status", "")).upper() not in ("FILLED", "PARTIALLY_FILLED"):
                    entry["exec_reject"] = getattr(self.spot2, "last_reject_reason", "spot reject")
                    entry["block_code"] = "EXCHANGE_REJECTED"
                    funnel["exchange_rejected"] += 1
                    continue
                trade_id = getattr(order, "id", new_id("spot"))
                desc = f"{sym} SPOT LONG @ {b.price:.6g} · {notional:.2f} USDT · stop {plan.stop:.6g}"
            # Eslesmis gozlem beklemede: ACTIVE gercek islemi daraltti, SHADOW ise yalniz
            # KARSI-OLGUSAL degerlendirildi (gercek giris ondan ETKILENMEDI).
            for _pol in (res, shadow_res):
                if _pol["policy_id"]:
                    self.research.add_pending(_pol["policy_id"], trade_id,
                                              {"decision": {"allow": _pol["allow"],
                                                            "size_multiplier": float(_pol["size_multiplier"]),
                                                            "reasons": _pol["reasons"]},
                                               "source": ("applied" if _pol is res else "counterfactual"),
                                               "symbol": sym, "side": d.direction})
            funnel["opened"] += 1
            self._notify_opened(sym, market, plan, notional, plan_leverage, final_risk_usdt, trade_id, now)
            self._seen_signals = (self._seen_signals + [_sig])[-5000:]
            # TETIK KAYDI ACILIS ANINDA islenir: tetiklenip acilmamis (kapasite/emir reddi) bir aday
            # barini YAKMAZ, kapasite serbest kaldiginda yeniden degerlendirilebilir. Ayni bar/taraf/
            # setup'in ikinci kez ACILMASINI `_seen_signals` (DUPLICATE_SIGNAL) engeller.
            if plan.entry_type == "breakout" and b.last_bar_4h:
                self.triggers[b.symbol] = b.last_bar_4h
            self.memory.record_entry({"trade_id": trade_id, "symbol": sym, "direction": d.direction, "market_type": market, "setup_type": plan.entry_type,
                                      "regime": d.regime, "features": ((snap_v3.vector() | feats) if snap_v3 else feats),
                                      "snapshot": (snap_v3.to_dict() if snap_v3 else None),
                                      "decision": d.to_dict(include_reports=True), "chief": chief.to_dict(),
                                      "risk_decision": rd.to_dict(), "run_id": self.run_id, "mode": self.mode_state.mode.value,
                                      "model_versions": d.model_versions | {"p_win_model": self.learner2.snapshot().get("champion")}})
            self.last_decisions[sym] = d.to_dict(include_reports=False)
            opened.append(desc)
            # fill sonrasi: yetkili defterlerden durum yenile -> ayni turdaki sonraki adaylar bu pozisyonu/marji/exposure'i gorur
            state, entries_allowed = self._refresh_after_fill(marks, risk_log, now)
            entry["state_after_fill"] = {"open_positions": len(state.open_positions), "used_margin": round(state.used_margin, 6),
                                         "total_open_risk_usdt": round(state.total_open_risk_usdt, 6), "persisted": entries_allowed}
        self.trig_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.trig_path, self.triggers, indent=None)
        return opened, risk_log

    # ------------------------------------------------------------------ bildirimler
    def _notify_opened(self, sym, market, plan, notional, leverage, max_loss, trade_id, now) -> None:
        """Açılış olayını YALNIZ outbox'a yazar — AĞ ÇAĞRISI YAPMAZ (bkz. O-4).

        `_entry_lock` içinde çağrılır; gerçek gönderim kilit bırakıldıktan sonra `_execute()`
        içindeki `notifier.flush()` ile yapılır. Gönderim başarısız olsa bile pozisyon geri alınmaz.
        """
        if not getattr(self, "notifier", None) or not self.notifier.wants("trade_opened"):
            return
        from .notify import build_opened
        from .pnl import position_view
        pos = self.ledger2.positions.get(sym)
        raw = pos.to_dict() if pos is not None else {
            "id": trade_id, "symbol": sym, "side": "LONG", "qty": 0, "entry_avg": plan.entry,
            "leverage": leverage, "notional": notional, "stop": plan.stop, "targets": plan.targets,
            "opened_at": iso(now), "market_type": "SPOT" if market == "SPOT" else "USDM_PERP"}
        view = position_view(raw, mark_price=raw.get("entry_avg"), fees=self.ledger2.fees,
                             market="SPOT" if market == "SPOT" else "FUTURES")
        self.notifier.enqueue(build_opened(view, max_loss_at_stop=max_loss,
                                           reason=str(plan.entry_trigger or "")[:160], created_at=iso(now)))

    def _notify_closed(self, records, now) -> None:
        """Kapanış bildirimleri. Önce kuyruğa yazılır, sonra TEK seferde gönderilir (kilit dışı).

        Restart'ta bastırılan AÇILIŞLAR bunu ENGELLEMEZ: eski pozisyon kapandığında GERÇEK kapanış
        bildirimi gönderilir.
        """
        if not getattr(self, "notifier", None) or not self.notifier.wants("trade_closed"):
            return
        from .notify import build_closed
        for rec in records or []:
            d = rec.to_dict() if hasattr(rec, "to_dict") else dict(rec)
            self.notifier.enqueue(build_closed(
                d, net_pnl=d.get("net_pnl", d.get("realized_pnl", d.get("pnl"))),
                gross_pnl=d.get("gross_pnl"), fees=d.get("fees"), funding=d.get("funding"),
                margin=d.get("isolated_margin", d.get("margin")), created_at=iso(now)))
        self.notifier.flush()                 # `_entry_lock` DIŞINDA (tick yolu kilidi tutmaz)

    def _notify_health(self, state_name: str, summary: str, now) -> None:
        if not getattr(self, "notifier", None) or not self.notifier.wants("health_degraded"):
            return
        from .notify import build_health
        prev = getattr(self, "_last_health_state", None)
        if prev == state_name:
            return
        self._last_health_state = state_name
        bad = str(state_name).upper() not in ("HEALTHY", "OK")
        if prev is None and not bad:
            return                                   # ilk turda "iyilesti" spam'i yok
        self.notifier.notify(build_health(state_name, summary=summary, recovered=not bad,
                                          ref=iso(now)[:16], created_at=iso(now)))

    def _notify_maintenance(self, health: dict, now) -> None:
        """Tur sonu bildirim bakımı — HEPSİ kilit DIŞINDA ve sınırlı süreli.

        1. Zamanı gelmiş başarısız olayları sınırlı sayıda yeniden dener (`retry_backoff_s`).
        2. Worker gerçekten ready/healthy ise bekleyen bir `worker_failure` için KURTARMA gönderir.
        3. Yapılandırılan UTC saatinde günde TAM BİR KEZ günlük özet üretir.
        """
        n = getattr(self, "notifier", None)
        if not n or not n.enabled:
            return
        try:
            n.retry_pending()                         # bounded: `retry_batch` kadar, due olanlar
            state_name = str(health.get("state") or "UNKNOWN").upper()
            hb_age = health.get("heartbeat_age_s")
            healthy = state_name in ("HEALTHY", "OK")
            if healthy and n.wants("worker_recovered"):
                ref = n.pending_worker_failure()
                if ref:                               # KURTARMA yalnız gerçekten sağlıklıyken
                    from .notify import build_worker_recovered
                    n.enqueue(build_worker_recovered("tradingbot-worker.service", ref=ref,
                                                     heartbeat_age_s=hb_age, ready=True,
                                                     created_at=iso(now)))
            day = now.date().isoformat()
            if n.daily_summary_due(day, now.hour):
                from .notify import build_daily_summary
                from .pnl import portfolio_view
                marks = {s: float(p.last_price or p.entry_avg) for s, p in self.ledger2.positions.items()}
                pv = portfolio_view([p.to_dict() for p in self.ledger2.positions.values()],
                                    self.ledger2.history_dicts(), marks=marks, fees=self.ledger2.fees,
                                    today=day)
                n.enqueue(build_daily_summary(pv, day=day, opened=int(self._funnel.get("opened", 0)) if getattr(self, "_funnel", None) else 0,
                                              closed=int(health.get("closed") or 0), health=state_name,
                                              created_at=iso(now)))
            n.flush()
        except Exception as exc:                      # noqa: BLE001 — bildirim ASLA turu düşürmez
            log.warning("bildirim bakımı başarısız (tur etkilenmedi): %s", type(exc).__name__)

    def _leverage_context(self, sym, d, plan, exec_entry: float, state, chief, opp: dict) -> LeverageContext:
        """Kaldıraç girdilerini TEK yerde topla. Bilinmeyen alan `None` kalır → yükseltme verilmez."""
        def _metric(agent: str, key: str):
            for r in d.specialist_reports:
                if r.agent_name == agent and r.usable:
                    v = (r.metrics or {}).get(key)
                    return None if v is None else float(v)
            return None

        atr_pct = None
        fr = (self.runner.last_frames.get(sym) or {}).get("4h")
        if fr is not None and len(fr) and "atr_pct" in fr:
            try:
                atr_pct = float(fr["atr_pct"].iloc[-1])
            except (TypeError, ValueError, IndexError):
                atr_pct = None
        stop_frac = abs(exec_entry - plan.stop) / exec_entry if (exec_entry and plan.stop) else None
        eq = max(state.equity if self.profile.size_on_live_equity else state.starting_equity, 1e-9)
        budget = eq * self.profile.max_total_open_risk_pct / 100.0
        same_dir = sum(1 for o in state.open_positions if o.side == d.direction)
        mode = str(getattr(chief, "market_risk_mode", "") or "")
        aligned = None
        if mode in ("RISK-ON", "RISK-OFF"):
            aligned = (mode == "RISK-ON" and d.direction == "LONG") or (mode == "RISK-OFF" and d.direction == "SHORT")
        elif mode:
            aligned = False                       # NÖTR: 5x için hizalanma sayılmaz
        funding = _metric("derivatives", "funding_pct")
        if funding is not None:                   # aleyhte funding pozitif olsun
            funding = funding if d.direction == "LONG" else -funding
        issues = (d.data_freshness or {}).get("issues") or []
        age = (d.data_freshness or {}).get("ticker_age_s")
        return LeverageContext(
            stop_frac=stop_frac, atr_pct=atr_pct,
            confidence=float(d.confidence_calibrated) if d.confidence_calibrated is not None else None,
            conservative_net_edge_r=(opp or {}).get("conservative_net_edge_r"),
            depth_usdt=_metric("orderbook_liquidity", "depth_top20_usdt"),
            spread_pct=_metric("orderbook_liquidity", "spread_pct"),
            funding_pct=funding, regime_aligned=aligned,
            open_risk_frac=(state.total_open_risk_usdt / budget) if budget > 0 else None,
            same_direction_open=same_dir,
            portfolio_corr=_metric("correlation_beta", "corr_btc_120b"),
            data_stale=bool(age is not None and age > 300) or bool(issues),
            data_conflict=bool(d.vetoes),
            profile_max_leverage=int(self.profile.futures_max_leverage))

    def _execution_entry(self, symbol: str, market: str, direction: str, ref_price: float,
                         tick: TickData | None = None) -> float:
        """Emrin GERÇEKTEN dolacağı fiyat — defterin KENDİ fill yolundan sorulur, yan etkisiz.

        Motor kendi yaklaşık kayma formülünü ÜRETMEZ. Futures tarafında yön, sabit kayma,
        yarım-spread ve price-tick kuantizasyonu `FuturesLedgerV2.market_fill_price` ile; spot
        tarafında ask/bid/last seçimi ve spot kayma modeli `SpotLedger.market_fill_price` ile
        birebir aynıdır — açılışta da aynı fonksiyon çağrılır. Böylece RiskEngine'e verilen entry
        defterde gerçekleşen entry ile eşleşir; özellikle ask-last farkı sabit kaymadan büyük
        olduğunda eski yaklaşık formül yanlış giriş fiyatı gösteriyordu.
        """
        ref = float(ref_price or 0.0)
        if ref <= 0:
            return ref
        if market == "SPOT":
            return float(self.spot2.market_fill_price(symbol, Side.BUY, tick=tick, ref_price=ref))
        return float(self.ledger2.market_fill_price(symbol, direction, Decimal(str(ref)),
                                                    filters=self.filters.get(symbol, MarketType.USDM_PERP),
                                                    tick=tick))

    def _trigger_fired(self, b: CoinBrief, direction: str, entry: float, entry_type: str) -> bool:
        """SAF sorgu: durum DEĞİŞTİRMEZ.

        Eskiden bu metot değerlendirme sırasında `self.triggers[symbol] = last_bar_4h` yazıyordu.
        Tetik artık risk kapısından ÖNCE çalıştığı için bu yazım, kapasite/emir yüzünden hiç
        açılmamış bir adayın barını yakardı. Kayıt artık YALNIZ gerçek açılışta işlenir
        (`_execute_locked`); aynı bar/taraf/setup'ın ikinci kez AÇILMASINI `_seen_signals`
        (DUPLICATE_SIGNAL) engeller.
        """
        if not b.price or not entry:
            return False
        if entry_type == "breakout":
            if not b.last_bar_4h or not b.last_close_4h:      # 4h çerçeve yoksa asla tetiklenmez (audit: last_close_4h=0 bug'ı)
                return False
            if self.triggers.get(b.symbol) == b.last_bar_4h:  # bu barda zaten giriş yapıldı
                return False
            lvl = entry / 1.001 if direction == "LONG" else entry / 0.999
            fired = (b.last_close_4h > lvl) if direction == "LONG" else (b.last_close_4h < lvl)
            if fired and abs(b.price / entry - 1) > 0.015:    # kovalama yasak
                fired = False
            return fired
        return abs(b.price / entry - 1) <= 0.0025

    def _label_shadows(self) -> None:
        pend = self.shadow.pending(utc_now())
        for sh in pend[:20]:
            frames = self.runner.last_frames.get(sh.symbol) or {}
            h4 = frames.get("4h")
            if h4 is None:
                continue
            try:
                out = self.shadow.label(sh, h4.reset_index(drop=True) if "timestamp" in h4 else h4)
            except (KeyError, ValueError, TypeError) as exc:
                log.warning("gölge işlem etiketlenemedi %s: %s", sh.id, exc)
                continue
            if out is None:
                continue
            # AKTİF adayın ELEDİĞİ giriş: baseline'ın karşı-olgusal sonucu artık biliniyor →
            # eşleşmiş gözlem (adayın risk bütçesi katkısı 0; işlem hiç açılmadı).
            from .learn.research_policy import BLOCKED
            for pending in self.research.pop_pending_for_trade(sh.id):
                dec = dict(pending.get("decision") or {})
                self.research.observe(pending["policy_id"], trade_id=sh.id,
                                      baseline_r=float(out.get("r_multiple", 0) or 0),
                                      risk_budget_contribution_r=0.0, kind=BLOCKED, size_multiplier=0.0,
                                      reasons=list(dec.get("reasons") or []))

    # ------------------------------------------------------------------ görsel / Obsidian (v2 defterle uyumlu)
    def _chart(self, b: CoinBrief) -> str:
        from .charts import render_signal_chart
        frames = self.runner.last_frames.get(b.symbol) or {}
        h4 = frames.get("4h")
        if h4 is None or len(h4) < 30:
            return ""
        pos = self.ledger2.positions.get(b.symbol)
        posd = None
        if pos:
            tg = [float(t) for t in pos.targets] + [None, None]
            posd = {"side": pos.side.value, "entry": float(pos.entry_avg), "stop": float(pos.stop) if pos.stop else None, "target1": tg[0], "target2": tg[1]}
        out = self.vault / "Charts" / f"{b.base}.png"
        try:
            render_signal_chart(h4, out, title=f"{b.symbol} · 4h · {b.headline}", plan=b.plan, levels=b.key_levels, position=posd,
                                footer=f"P(kazanç) %{(b.p_win or 0.5)*100:.0f} · tarayıcı skoru {b.scan_score or '-'} · {b.generated_at}")
            b.chart = f"Charts/{b.base}.png"
            if pos and not (self.vault / "Charts" / "history" / f"{b.base}_{pos.id}.png").exists():
                render_signal_chart(h4, self.vault / "Charts" / "history" / f"{b.base}_{pos.id}.png", title=f"{b.symbol} · {pos.id} açılış", plan=b.plan, levels=b.key_levels, position=posd)
            return b.chart
        except Exception as exc:  # noqa: BLE001 — grafik hatası turu durdurmaz
            log.warning("%s grafik hatası: %s", b.symbol, exc)
            return ""

    def _futures_note(self, briefs) -> str:
        from .core import istanbul
        prices = {b.symbol: b.price for b in briefs}
        s = self.ledger2.summary(prices)
        local = istanbul()
        out = ["---", "tags: [trading, paper, futures]", "schema: v2", "---",
               "# 📈 Kağıt Futures Defteri v2 (Decimal · izole marj · gerçek para YOK)",
               f"> {local} (Europe/Istanbul) · Cüzdan **{s['wallet_balance']:.4f}** · Equity(MTM) **{s['equity_mtm']:.4f} USDT** (başlangıç {s['starting_equity']}) · getiri {s['return_pct']:+.2f}% · "
               f"açık {s['open']} · kapanan {s['closed']} · kazanma %{s['win_rate']} · ort. {s['avg_r']:+.2f}R · komisyon {s['total_fees']:.4f} · funding {s['total_funding']:+.4f}",
               f"> Mod **{self.mode_state.mode.value}** · risk profili **{self.profile.name}** · kill switch **{self.killswitch.state}**", "",
               "## Açık pozisyonlar", "| ID | Sembol | Yön | Giriş | Şimdi | Miktar | Notional | Marj | Kaldıraç | Stop | Hedefler | Liq | MAE/MFE % | Funding | Amount type | Görsel |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for sym, p in self.ledger2.positions.items():
            px = prices.get(sym, float(p.last_price or p.entry_avg))
            out.append(f"| {p.id} | {sym} | {'🟢' if p.side.value=='LONG' else '🔴'} {p.side.value} | {float(p.entry_avg):.6g} | {px:.6g} | {float(p.qty):g} | {float(p.qty*p.entry_avg):.2f} | "
                       f"{float(p.isolated_margin):.2f} | {p.leverage}x | {float(p.stop):.6g} | {', '.join(f'{float(t):.6g}' for t in p.targets)} | "
                       f"{float(p.liquidation_price):.6g} | {float(p.mae_pct):.1f}/{float(p.mfe_pct):.1f} | {float(p.funding_received - p.funding_paid):+.4f} | {p.amount_type.value} | ![[Charts/{sym.split('/')[0]}.png\\|200]] |")
        if not self.ledger2.positions:
            out.append("| - | Açık pozisyon yok | | | | | | | | | | | | | | |")
        out += ["", "## Kapanan işlemler (son 30)", "| ID | Sembol | Yön | Giriş | Çıkış | Neden | Brüt | Komisyon | Funding | Kayma | Net | R | Bar | Kaldıraç | Setup | Kapanış |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for h in self.ledger2.history_dicts()[-30:][::-1]:
            out.append(f"| {h['id']} | {h['symbol']} | {h['side']} | {float(h['entry']):.6g} | {float(h.get('exit_price') or 0):.6g} | {h['exit_reason']} | {float(h.get('gross_pnl', 0)):+.4f} | "
                       f"{float(h.get('fees', 0)):.4f} | {float(h.get('funding', 0)):+.4f} | {float(h.get('slippage_cost', 0)):.4f} | {float(h.get('net_pnl', h.get('pnl', 0))):+.4f} | "
                       f"{float(h['r_multiple']):+.2f} | {h.get('bars_held', 0)} | {h['leverage']}x | {h.get('setup_type', '')} | {str(h.get('closed_at', ''))[:16]} |")
        out += ["", "Kurallar: TP1'de kısmi kapama + GERÇEK başa-baş (komisyon+kayma dahil) · likidasyon bracket/MMR ile · funding 00/08/16 UTC settlement, kaçırılan dönemler toplu · "
                "aynı tikte stop+hedef → stop (worst-case) · komisyon fill notional üzerinden · vergi ayrı ve doğrulanana kadar 0",
                "", "[[Learning/Öğrenme]] · [[Learning/Dersler]] · [[Scanner]] · [[Dashboard]] · [[Risk/Limits]]"]
        return "\n".join(out)

    def _training_due(self, now) -> bool:
        """Her kapanışta yeniden eğitme YOK: asgari yeni kapanış + cooldown kapısı birlikte geçilmeli."""
        lc = self.cfg.v3.learning_v3
        if self._closes_since_train < lc.retrain_min_new_closed:
            return False
        if self._last_train_at is not None:
            if (now - self._last_train_at).total_seconds() < lc.retrain_cooldown_hours * 3600:
                return False
        return True

    def _assess_opportunities(self, decisions: dict, briefs) -> None:
        """Her islenebilir karar icin `OpportunityAssessment` uretir ve karara baglar.

        Sabit esik zinciri yerine: kalibre p_win + gerceklesmis kazanc/kayip dagilimi + maliyet +
        belirsizlik + yumusak kanit -> muhafazakar net edge. Maliyet CIFT SAYILMAZ (bkz.
        `opportunity.assess` ve `expectancy_basis`).
        """
        from .decision_gates import GateLedger, UnknownGateCode
        from .opportunity import assess, hierarchical_expectancy
        bmap = {b.symbol: b for b in briefs}
        for sym, d in decisions.items():
            if not getattr(d, "is_actionable", False):
                continue
            plan = getattr(d, "active_plan", None)
            if plan is None or not getattr(plan, "valid", False):
                continue
            gates = GateLedger()
            # FAIL-CLOSED: kayıtsız bir kapı kodu sessizce yumuşak KABUL EDİLMEZ. Kod bir yazım
            # hatasıysa (ör. `KILL_SWITCH_ACTIV`) aday `UNKNOWN_GATE_CODE` ile SERT reddedilir ve
            # kod telemetriye yazılır; motor çalışmaya devam eder ama işlem AÇILMAZ.
            _unknown: list[str] = []
            for code in list(getattr(d, "soft_flags", []) or []) + list(getattr(plan, "soft_flags", []) or []):
                try:
                    gates.penalise(code, _SOFT_PENALTY_R.get(code, 0.05), detail="coin head kanıtı")
                except UnknownGateCode as exc:
                    _unknown.append(exc.code)
                    log.error("kayıtsız kapı kodu %s (%s) — aday fail-closed reddedildi", exc.code, sym)
            b = bmap.get(sym)
            if b is not None and getattr(b, "dont_list", None):
                for _w in list(b.dont_list)[:4]:
                    gates.penalise("RED_TEAM_SOFT_PENALTY", 0.04, detail=str(_w)[:80])
            if _unknown:
                gates.block("UNKNOWN_GATE_CODE", detail=",".join(sorted(set(_unknown))[:5]))
            stop_pct = plan.stop_pct
            if stop_pct <= 0:
                gates.block("ZERO_STOP_DISTANCE")
            stats = hierarchical_expectancy(learner=self.learner2, symbol=sym, side=d.direction,
                                            setup=plan.entry_type or "-", regime=d.regime,
                                            fallback_win_r=plan.expected_r)
            if d.p_win:                                   # kalibre model tahmini onceliklidir
                stats["p_win"] = max(0.05, min(0.95, float(d.p_win)))
            a = assess(symbol=sym, side=d.direction, setup=plan.entry_type or "-", gates=gates,
                       p_win=stats["p_win"], avg_win_r=stats["avg_win_r"], avg_loss_r=stats["avg_loss_r"],
                       sample_size=stats["sample_size"], cost_pct_notional=plan.expected_cost_pct,
                       stop_dist_pct=stop_pct, expectancy_basis=stats["expectancy_basis"],
                       risk_per_trade_pct=self.profile.risk_per_trade_pct,
                       provenance=stats["provenance"] | {"expected_r_geometry": plan.expected_r})
            d.opportunity = a.to_dict()

    def _retention_alarm(self) -> dict:
        """Son rotasyonun SALT OKUNUR durumu — arıza halinde açık alarm, asla istisna sızdırmaz.

        `silent_deletion` DAİMA False'tur: bu hattın sözleşmesi gereği bir kayıt ancak arşive
        mühürlendikten sonra aktif dosyadan çıkarılır.
        """
        try:
            j = getattr(self, "decision_journal", None)
            rot = getattr(self, "_journal_rotation", None) or {}
            base = {"silent_deletion": False, "last_rotation_health": rot.get("health"),
                    "last_rotation_error": rot.get("error"),
                    "archived_last_rotation": int(rot.get("archived") or 0),
                    "shadow_archive_errors": int(getattr(self.shadow, "archive_errors", 0) or 0),
                    "shadow_last_archive_error": getattr(self.shadow, "last_archive_error", None)}
            if j is not None and hasattr(j, "retention_stats"):
                st = j.retention_stats()
                base.update({k: st.get(k) for k in
                             ("hot_records", "archived_records", "lifetime_records", "n_segments",
                              "oldest_ts", "newest_ts", "archive_health", "last_archive_error",
                              "retention_policy", "deleted_segments")})
            return base
        except Exception:  # noqa: BLE001 — telemetri arızası turu ETKİLEMEZ
            return {"silent_deletion": False, "archive_health": "UNKNOWN"}

    def _persist_funnel(self, now, n_closed: int) -> None:
        """Karar hunisi + kayan 24 saat. `trades_opened_24h` YALNIZ gozlem metrigidir, kapi degildir."""
        f = dict(getattr(self, "_funnel", None) or {k: 0 for k in _FUNNEL_KEYS})
        f["closed"] = int(n_closed)
        prev = read_json(self._funnel_path, default=None) or {}
        hist = [h for h in (prev.get("history") or []) if h.get("at")][-500:]
        hist.append({"at": iso(now), **f})
        cutoff = (now - timedelta(hours=24)).isoformat()
        recent = [h for h in hist if str(h.get("at", "")) >= cutoff]
        roll = {k: sum(int(h.get(k, 0) or 0) for h in recent) for k in list(_FUNNEL_KEYS) + ["closed"]}
        denom = max(1, f.get("actionable", 0))
        atomic_write_json(self._funnel_path, {
            "schema": "decision_funnel_v1", "at": iso(now), "run": f,
            "rolling_24h": roll, "trades_opened_24h": roll.get("opened", 0),
            "hard_block_rate": round((f.get("negative_edge_blocked", 0) + f.get("risk_capacity_blocked", 0)
                                      + f.get("duplicate_blocked", 0) + f.get("exchange_rejected", 0)) / denom, 4),
            "no_trade_rate": round(1.0 - f.get("opened", 0) / denom, 4),
            "opportunity_cost_count": len(getattr(self, "_opportunity_cost", []) or []),
            "opportunity_cost": list(getattr(self, "_opportunity_cost", []) or [])[-20:],
            # RAPORLAMA SOZLESMESI: sabit islem sayisi kotasi YOKTUR.
            "daily_trade_cap": None, "per_run_trade_cap": None,
            # SAKLAMA ALARMI: arsiv manifesti hic yazilamasa bile (disk dolu vb.) son rotasyon
            # sonucu BURADA gorunur; sessiz kayip yerine acik durum.
            "retention": self._retention_alarm(),
            "history": hist[-500:]})
        atomic_write_json(self._sig_path, {"ids": self._seen_signals[-5000:]})

    def _signal_id(self, sym: str, market: str, d, plan, b) -> str:
        """Benzersiz sinyal kimligi: symbol|market|timeframe|closed_bar_ts|side|setup_type.

        Ayni benzersiz sinyal iki kez acilamaz. Fakat YENI kapanmis bar / yeni setup / onceki islem
        kapandiktan sonraki yeni sinyal "limit" gerekcesiyle engellenemez.
        """
        bar = str(getattr(b, "last_bar_4h", "") or "")
        return stable_id("signal", sym, market, "4h", bar, d.direction, plan.entry_type or "-")

    def _research_mode_ok(self) -> tuple[bool, str]:
        """MUTLAK MOD KAPISI — araştırma yalnız saf PAPER kâğıt yolunda çalışır."""
        from .learn.research_coordinator import mode_gate
        return mode_gate(self.mode_state.mode.value, self.cfg.v3.execution.gateway,
                         self.mode_state.is_live_order_path_enabled())

    def _research_entry(self, sym: str, d, plan, snap) -> dict:
        """Giriş anında iki ayrı karar üretir.

        * `active`  — GERÇEK girişi daraltabilir (yalnız reddeder ya da küçültür).
        * `shadow`  — SADECE karşı-olgusal ölçüm; gerçek girişi ASLA değiştirmez.

        Mod kapısı geçilmezse ikisi de baseline (değişiklik yok) döner.
        """
        from .learn.research_policy import apply_research_policy
        base = {"allow": True, "size_multiplier": 1.0, "reasons": ["NO_POLICY"], "policy_id": None}
        allowed, reason = self._research_mode_ok()
        if not (self.cfg.v3.learning_v3.research_enabled and allowed):
            off = dict(base, reasons=[("RESEARCH_DISABLED" if not self.cfg.v3.learning_v3.research_enabled
                                       else f"MODE_GATE:{reason}")])
            return {"active": off, "shadow": dict(off)}
        vals = (snap.values if snap is not None else {}) or {}
        kw = dict(side=d.direction, symbol=sym, p_win=float(d.p_win or 0.5),
                  expected_net_r=float(plan.expected_r or 0.0))
        out = {}
        for key, pol in (("active", self.research.active_policy()), ("shadow", self.research.shadow_policy())):
            try:
                out[key] = apply_research_policy(pol, vals, **kw)
            except Exception as exc:  # noqa: BLE001 — araştırma hatası girişi ENGELLEMEZ, baseline'a düşer
                log.warning("%s araştırma politikası (%s) uygulanamadı: %s", sym, key, exc)
                out[key] = dict(base, reasons=[f"RESEARCH_ERROR:{exc}"])
        return out

    def _observe_research_close(self, rec) -> None:
        """Kapanan GERCEK islemi eslesmis gozleme cevirir.

        `baseline_r` = islemin tam boyutta gerceklesen R'si.
        `risk_budget_contribution_r` = adayin AYNI islemdeki risk butcesi katkisi:
        eleme -> 0.0, kucultme -> R x carpan, dokunmama -> R.
        Gercek islemin R'si bu hesaptan ETKILENMEZ; "trade R degisti" DEGILDIR.
        """
        from .learn.research_policy import contribution_of
        try:
            trade_id = str(getattr(rec, "id", "") or "")
            pendings = self.research.pop_pending_for_trade(trade_id)
            if not pendings:
                return
            r = float((rec.to_legacy_dict() or {}).get("r_multiple", 0) or 0)
            for pend in pendings:
                dec = dict(pend.get("decision") or {})
                contribution, kind = contribution_of(dec, r)
                self.research.observe(pend["policy_id"], trade_id=trade_id, baseline_r=r,
                                      risk_budget_contribution_r=contribution, kind=kind,
                                      size_multiplier=float(dec.get("size_multiplier", 1.0)),
                                      reasons=list(dec.get("reasons") or []))
        except Exception as exc:  # noqa: BLE001 -- gozlem hatasi islem akisini DURDURAMAZ
            log.warning("arastirma gozlemi yazilamadi: %s", exc)

    # ------------------------------------------------------------------ Outcome Learning Loop V1
    def _shadow_add(self, plan: dict, reasons, **kw):
        """`ShadowBook.add` sarmalayıcısı — hangi adayın gölge kaydı aldığını izler.

        Davranış birebir aynıdır; yalnız sembol `_shadow_syms` kümesine eklenir ki karar günlüğü
        `SHADOW` sonucunu doğru sınıflandırabilsin.
        """
        try:
            sym = str(plan.get("symbol") or "")
            if sym:
                if not hasattr(self, "_shadow_syms") or self._shadow_syms is None:
                    self._shadow_syms = set()
                self._shadow_syms.add(sym)
        except Exception:  # noqa: BLE001 — izleme, gölge kaydını ASLA engellemez
            pass
        return self.shadow.add(plan, reasons, **kw)

    def _prepared_experience_pool(self, cfg):
        """Deneyim havuzunu TUR BAŞINA BİR KEZ hazırlar (dosya okuma + vektörleme).

        Ölçüm: aday başına yeniden vektörleme 10.000 deneyimde ~455 ms sürüyordu; 20 adaylı
        turda ~9 sn ederdi (worker 15 sn aralıkla çalışır). Hazır havuzla aday başına maliyet
        ~55 ms'ye indi, hazırlık ise tur başına tek sefer ~420 ms.
        Dosya imzası (mtime, size) değişmediyse havuz yeniden kurulmaz. Hata → boş havuz
        (baseline fail-safe).
        """
        from .learn.experience import ExperienceIndex, PreparedPool, prepare_pool
        idx = getattr(self, "_exp_index", None)
        if idx is None:
            idx = self._exp_index = ExperienceIndex()
        st = self.cfg.state_path
        mem = idx.rows("memory", st / "trade_memory.jsonl",
                       lambda: self.memory.trades(closed_only=True))
        shad = idx.rows("shadow", st / "shadow_book.json",
                        lambda: [t.to_dict() for t in self.shadow.trades])
        # UZUN VADELİ GEÇMİŞ: aktif dosyadan çıkmış gölge sonuçlar arşiv indeksinden gelir.
        # `refresh()` TUR BAŞINA bir kez çağrılır ve yalnız YENİ segmenti okur; aday başına
        # arşiv taraması YOKTUR. Arıza baseline'ı bozmaz (boş geçmiş → eski davranış).
        hist: list = []
        store = getattr(self, "exp_index_store", None)
        if store is not None:
            try:
                self._exp_index_refresh = store.refresh()
                hist = store.rows()
            except Exception as exc:  # noqa: BLE001
                self._journal_errors += 1
                log.warning("deneyim indeksi okunamadı (baseline sürüyor): %s", exc)
                hist = []
        sig = (idx._sig.get("memory"), idx._sig.get("shadow"),
               cfg.shadow_weight, cfg.shadow_fidelity,
               store.signature() if store is not None else None)
        cached = getattr(self, "_exp_pool", None)
        if cached is not None and getattr(cached, "signature", None) == sig:
            return cached
        try:
            pool = prepare_pool(memory_rows=mem, shadow_trades=shad,
                                indexed_history=hist,
                                shadow_weight=cfg.shadow_weight,
                                shadow_fidelity=cfg.shadow_fidelity)
        except Exception as exc:  # noqa: BLE001 — hazırlama hatası baseline'ı bozamaz
            self._journal_errors += 1
            log.warning("deneyim havuzu hazırlanamadı (baseline sürüyor): %s", exc)
            pool = PreparedPool()
        pool.signature = sig
        self._exp_pool = pool
        return pool

    def _prior_leaf_n(self, symbol: str, setup: str | None) -> float:
        """Hiyerarşik prior'ın bu yaprakta KAÇ örnek kullandığı (çift sayım payını hesaplamak için)."""
        try:
            stats = getattr(self.learner2, "win", None)
            if stats is None:
                return 0.0
            leaf = f"{symbol}|{setup or '-'}"
            for key in (leaf, str(symbol)):
                s = getattr(stats, "stats", {}).get(f"leaf:{key}")
                n = getattr(s, "n", None) if s is not None else None
                if isinstance(n, (int, float)) and n > 0:
                    return float(n)
        except Exception:  # noqa: BLE001
            return 0.0
        return 0.0

    def _learning_influence(self, b, d, snap, baseline_p_win: float, legacy_feats: dict) -> dict | None:
        """Geçmiş deneyimden sınırlı öğrenme ayarlaması. ASLA istisna sızdırmaz.

        Retrieval yalnız KARAR ANINDAN ÖNCE kapanmış işlemleri görür (no-lookahead).
        `applied=False` iken baseline birebir korunur; hard veto/risk/kill switch bu değerden
        BAĞIMSIZDIR ve bu fonksiyon tarafından geçilemez.
        """
        cfg = getattr(self, "influence_cfg", None)
        if cfg is None or cfg.mode == "OFF":
            return None
        try:
            from .learn.experience import query_pool
            from .learn.influence import apply_influence, combine_components, weighted_adjustment
            as_of_ms = None
            if snap is not None and getattr(snap, "last_bar_ts", None):
                try:
                    from .core import from_iso as _from_iso
                    as_of_ms = int(_from_iso(snap.last_bar_ts).timestamp() * 1000)
                except (ValueError, TypeError):
                    as_of_ms = None
            query = dict(legacy_feats or {})
            query.update({"symbol": b.symbol, "direction": (d.direction if d else None),
                          "setup_type": (b.plan.entry_type if b.plan else None),
                          "regime": (d.regime if d else None)})
            prepared = self._prepared_experience_pool(cfg)
            # SINIRLI TARAMA: aday basina maliyet arsiv buyuklugunden BAGIMSIZ kalir.
            pool = query_pool(prepared, query, as_of_ms=as_of_ms, top_k=cfg.top_k,
                              max_scan=self.cfg.v3.learning_v3.retrieval_max_scan)
            # ÇİFT SAYIM KORUMASI: hiyerarşik prior aynı kapanışları zaten kullandı; similarity
            # yalnız RESIDUAL payı uygular. `prior_leaf_n` = bu sembol/setup yaprağının örnek sayısı.
            leaf_n = self._prior_leaf_n(b.symbol, b.plan.entry_type if b.plan else None)
            adj = weighted_adjustment(pool, baseline=baseline_p_win, cfg=cfg, prior_leaf_n=leaf_n)
            comp = combine_components(raw_model_p=baseline_p_win, hierarchical_p=baseline_p_win,
                                      adjustment=adj, cfg=cfg)
            applied = apply_influence(adj, cfg=cfg, mode_value=self.mode_state.mode.value,
                                      live_order_path=bool(getattr(self.cfg.v3.mode, "live_order_path", False)))
            n_real = sum(1 for e in pool if e.source == "REAL_PAPER")
            return {"symbol": b.symbol, "direction": (d.direction if d else None),
                    "as_of_ms": as_of_ms, "n_experience": adj.get("n_experience"),
                    "n_real": n_real, "n_shadow": len(pool) - n_real,
                    "effective_n": adj.get("effective_n"),
                    "prior_leaf_n": leaf_n, "prior_weight": adj.get("prior_weight"),
                    "residual_share": adj.get("residual_share"),
                    "dropped_duplicates": adj.get("dropped_duplicates"),
                    "top_similarity": (pool[0].similarity if pool else None),
                    "fraction": adj.get("fraction"), "reasons": adj.get("reasons"),
                    "baseline": adj.get("baseline"), "learned": adj.get("learned"),
                    "components": comp,
                    "applied": applied.get("applied"), "blockers": applied.get("blockers"),
                    "effective": applied.get("effective"), "mode": cfg.mode}
        except Exception as exc:  # noqa: BLE001 — öğrenme arızası baseline kararı bozamaz
            self._journal_errors += 1
            log.warning("learning influence atlandı (baseline korunur): %s", exc)
            return None

    def _journal_decisions(self, risk_log: list[dict], decisions: dict, now) -> None:
        """DEĞERLENDİRİLEN HER aday için karar snapshot'ı yazar. Arıza turu ÇÖKERTMEZ.

        **Evaluated candidate tanımı:** bu turda Coin Head kararı üretilmiş HER sembol
        (`decisions` sözlüğünün tamamı) — yani yeterli piyasa verisiyle değerlendirmeye giren
        ilk noktadan itibaren. Payda budur; `risk_log` yalnız sıralamaya giren alt kümedir ve
        varsa birleştirilir. Böylece NON_ACTIONABLE ve NO_VALID_PLAN adayları da kaydedilir.
        Aday başına TEK nihai kayıt üretilir (aşama geçmişi `stage_history` alanındadır).
        """
        j = getattr(self, "decision_journal", None)
        if j is None or not decisions:
            return
        try:
            from .learn.decision_journal import build_decision_record, classify_outcome
            infl = {i["symbol"]: i for i in getattr(self, "_influence_log", []) if i.get("symbol")}
            by_sym: dict[str, dict] = {}
            for e in risk_log:                       # aynı sembolün SON kaydı nihai durumdur
                s = str(e.get("symbol") or "")
                if s:
                    by_sym[s] = e
            shadowed = set(getattr(self, "_shadow_syms", ()) or ())
            n = 0
            for sym, d in decisions.items():
                e = by_sym.get(sym)
                plan = getattr(d, "active_plan", None)
                is_act = bool(getattr(d, "is_actionable", False))
                has_plan = bool(plan is not None and getattr(plan, "valid", False))
                v = getattr(d, "verdict", None)
                verdict = str(getattr(v, "value", v) or "")
                kind, stage, reason = classify_outcome(
                    e, is_actionable=is_act, has_valid_plan=has_plan,
                    verdict=verdict, shadowed=sym in shadowed)
                rec = build_decision_record(
                    run_id=self.run_id, cycle_id=getattr(self, "_journal_cycle", 0),
                    symbol=sym, direction=str(getattr(d, "direction", "") or ""),
                    market_type=(e or {}).get("market_type"),
                    decision_ts=iso(now), entry=e,
                    snapshot=self._pred_snapshots.get(sym), decision=d,
                    outcome_kind=kind, trade_id=(e or {}).get("trade_id"),
                    code_sha=getattr(self.cfg, "code_sha", None),
                    policy_id=(e or {}).get("research_policy_id"))
                rec.update({"outcome_stage": stage, "outcome_reason": reason,
                            "is_actionable": is_act, "has_valid_plan": has_plan,
                            "entered_ranking": e is not None,
                            "shadow_recorded": sym in shadowed,
                            "stage_history": [k for k, val in (self._funnel or {}).items() if val]
                            if getattr(self, "_funnel", None) else None})
                if sym in infl:
                    rec["learning_influence"] = {k: infl[sym].get(k) for k in
                                                 ("mode", "n_experience", "top_similarity",
                                                  "fraction", "baseline", "learned", "applied",
                                                  "blockers")}
                if j.append_decision(rec):
                    n += 1
            self._journaled_last_tour = n
            self._evaluated_last_tour = len(decisions)
            # KAYIPSIZ rotasyon: taşan kayıtlar önce arşive mühürlenir, sonra çıkarılır.
            # Arşiv başarısızsa budama YAPILMAZ ve durum açık alarma dönüşür (sessiz kayıp yok).
            rot = j.rotate()
            self._journal_rotation = rot
            if rot.get("error"):
                self._journal_errors += 1
                log.warning("karar günlüğü arşivi başarısız — BUDAMA YAPILMADI (kayıp yok): %s",
                            rot.get("error"))
        except Exception as exc:  # noqa: BLE001
            self._journal_errors += 1
            log.warning("karar günlüğü yazılamadı (tur etkilenmedi): %s", exc)

    def _journal_outcome(self, rec_legacy: dict, lesson: dict | None = None) -> None:
        """Kapanan işlemi aynı `trade_id` üzerinden karar snapshot'ına bağlar (idempotent)."""
        j = getattr(self, "decision_journal", None)
        if j is None:
            return
        try:
            from .learn.decision_journal import build_outcome_link
            tid = str(rec_legacy.get("id") or rec_legacy.get("trade_id") or "")
            if not tid:
                return
            j.append_outcome(build_outcome_link(trade_id=tid, outcome=rec_legacy, lesson=lesson))
        except Exception as exc:  # noqa: BLE001
            self._journal_errors += 1
            log.warning("outcome bağlantısı yazılamadı: %s", exc)

    def _snapshot_v3(self, sym: str, d):
        """Canli PAPER karar ani FeatureSnapshotV3 -- replay ile AYNI builder ve AYNI esleme yardimcilari.

        `learner2.predict`ten ONCE cagrilir; donen nesne hem model girdisi hem de giris kaydi icin
        kullanilir. Hata halinde islem akisi DEGISMEZ ama sessiz kalmaz: sayaclar artar (telemetry).
        """
        from .learn.snapshot import (LeakageError, agents_from_factor_scores, build_snapshot,
                                     pattern_fields_from_evidence)
        plan = d.active_plan if d is not None else None
        if plan is None:
            return None
        try:
            frames = self.runner.last_frames.get(sym) or {}
            # DETERMINISTIK frame secimi ve GERCEK timeframe etiketi: "4h" yokken baska bir frame'i
            # alip yine "4h" yazmak yasak (namespace bozulur).
            tf = "4h" if "4h" in frames else (sorted(frames)[0] if frames else None)
            bars = frames.get(tf) if tf else None
            if bars is None or len(bars) < 30:
                return None
            decision_ts = int(bars["timestamp"].iloc[-1])
            btc = None
            if sym != "BTC/USDT":
                btc = (self.runner.last_frames.get("BTC/USDT") or {}).get(tf)
                if btc is not None:
                    btc = btc[btc["timestamp"] <= decision_ts]
            live = dict(self.runner.live.snapshot(sym) or {})
            tick = live.get("ticker") or {}
            cons = d.consensus if isinstance(getattr(d, "consensus", None), dict) else {}
            market = "SPOT" if d.verdict == Verdict.SPOT_LONG else "USDM_PERP"
            snap = build_snapshot(
                symbol=sym, market_type=market, timeframe=str(tf), side=d.direction,
                decision_ts_ms=decision_ts, bars=bars[bars["timestamp"] <= decision_ts], source="LIVE_PAPER",
                btc_bars=btc,
                micro={"spread_pct": tick.get("spread_pct"), "depth_ratio": tick.get("depth_ratio"),
                       "data_freshness_s": tick.get("age_s")},
                funding={"rate": (live.get("funding") or {}).get("rate")},
                decision={"consensus_score": (sum(cons.values()) / len(cons)) if cons else None,
                          "consensus_conf": getattr(d, "consensus_confidence", None),
                          "n_dissent": len(getattr(d, "dissent", []) or []),
                          "n_vetoes": len(getattr(d, "vetoes", []) or []),
                          "head_confidence": getattr(d, "confidence_calibrated", None)},
                plan={"setup_type": plan.entry_type, "expected_r": plan.expected_r,
                      "expected_cost_pct": plan.expected_cost_pct, "p_win": d.p_win,
                      "entry": plan.entry, "stop": plan.stop, "targets": list(plan.targets or []),
                      "rr": plan.rr, "leverage": plan.size.leverage,
                      "notional": plan.notional, "margin": plan.margin},
                pattern=pattern_fields_from_evidence(getattr(d, "pattern_evidence", None), d.direction),
                agents=agents_from_factor_scores(getattr(d, "factor_scores", None)),
                run_id=self.run_id, strict=True)
            if snap.last_bar_ts > snap.decision_ts:      # ikinci savunma hatti (fail-closed)
                raise LeakageError(f"last_bar_ts {snap.last_bar_ts} > decision_ts {snap.decision_ts}")
            self.snap_telemetry.success()
            return snap
        except LeakageError as exc:
            self.snap_telemetry.failure(exc, leakage=True)
            log.warning("%s FeatureSnapshotV3 nedensellik ihlali: %s", sym, exc)
            return None
        except Exception as exc:  # noqa: BLE001 -- snapshot hatasi islem akisini DEGISTIRMEZ, ama sessiz kalmaz
            self.snap_telemetry.failure(exc)
            log.warning("%s FeatureSnapshotV3 uretilemedi: %s", sym, exc)
            return None

    def _write_trade_notes(self) -> None:
        """Kapanan her işlem için `Trades/<id>.md` notu (post-mortem ile) — dondurulmuş not varsa atlanır."""
        if not self.ch_writer:
            return
        mem = getattr(self.learner2, "memory", None)
        for h in self.ledger2.history_dicts()[-30:] + self.spot2.history_dicts()[-30:]:
            tid = str(h.get("id") or "")
            if not tid or self.ch_writer.trade_note_frozen(tid):
                continue
            pm = None
            if mem is not None:
                try:
                    pm = (mem.get(tid) or {}).get("postmortem") or None
                except Exception as exc:  # noqa: BLE001 — post-mortem okunamazsa not yine de yazılır
                    log.warning("%s post-mortem okunamadı: %s", tid, exc)
            self.ch_writer.write_trade(h, pm)

    def _write_obsidian_v3(self, decisions, chief, briefs, state, chart_paths, alerts) -> None:
        if not self.ch_writer:
            return
        bmap = {b.symbol: b for b in briefs}
        try:
            for sym, d in decisions.items():
                b = bmap.get(sym)
                self.ch_writer.write_coin_head(d.to_dict(include_reports=True), b.to_dict() if b else None, chart_paths.get(sym) or None)
            self.ch_writer.write_portfolio(self.spot2.summary({}), self.ledger2.summary({}), [o.to_dict() for o in state.open_positions])
            self.ch_writer.write_risk(self.risk.snapshot(state), self.killswitch.to_dict())
            self.ch_writer.write_models(self.model_registry.to_dict())
            self._write_trade_notes()
            for a in alerts:
                if "AÇILDI" in a or "KAPANDI" in a or "KILL" in a:
                    self.ch_writer.append_run_event("trade" if "KILL" not in a else "incident", a, self.run_id)
            self.ch_writer.prune_stale({s.split("/")[0] for s in decisions} | {s.split("/")[0] for s in self.ledger2.positions}, self.cfg.v3.obsidian_v3.prune_stale_hours)
        except Exception as exc:  # noqa: BLE001 — Obsidian yazımı turu durdurmaz
            log.warning("Obsidian Coin Heads yazımı başarısız: %s", exc)
