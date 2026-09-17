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
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .accounting import (AmountType, FeeSchedule, FiltersCache, FuturesLedgerV2, LiquidationParams, MarketType, Side, SizeSpec,
                         SlippageModel, SpotLedger, TaxPolicy, TickData, default_brackets, static_rates)
from .agents.manager import CoinBrief
from .coinhead import ChiefPortfolioManager, CoinHeadConfig, CoinHeadInputs, CoinHeadRegistry, Verdict
from .config import BotConfig
from .core import (atomic_write_json, from_iso, iso, new_id, read_json, run_id_now,
                   stable_id, utc_now)
from .engine import TradingEngine
from .entry_universe import GATE_CODE as ENTRY_UNIVERSE_GATE, entry_block_reason
from .learn import LearnConfig, LearnerV2, ModelRegistry, ShadowBook, TradeMemory
from .learning import features_from_brief
from .market.quality import DataQualityConfig, DataQualityGate
from .risk import (KillSwitch, ModeState, RiskEngine, build_state, enforces_position_cap, resolve_profile,
                   spot_notional_from_prices,
                   warn_if_below_recommended)
from .risk.leverage import LeverageConfig, LeverageContext, select_leverage, validate_leverage_settings

log = logging.getLogger(__name__)


def _f_num(x):
    """Sayıya çevir; olamıyorsa `None` (SIFIR DEĞİL)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


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
                "trigger_fired", "candle_blocked", "chart_blocked", "regime_blocked", "positive_point_edge", "positive_conservative_edge",
                "negative_edge_blocked", "research_small", "duplicate_blocked",
                "research_policy_blocked", "size_multiplier_zero", "leverage_gate_blocked",
                "precision_unresolved",
                "risk_capacity_blocked", "capacity_approved", "exchange_rejected", "opened")

# TEK KAYNAK: `economics_gate.SOFT_PENALTY_R`. Ikinci bir tablo TUTULMAZ — canli motorla
# replay'in farkli ceza tablosu tasimasi, bu paketin daha once yasadigi kusur sinifidir.
from .economics_gate import SOFT_PENALTY_R as _SOFT_PENALTY_R  # noqa: E402

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
        # KANIT ONARIMI V1.1: spot listeleme onbellegi (`universe.futures_only_penalty_r` cezasinin verisi).
        from .market.spot_listing import SpotListing
        self.spot_listing = SpotListing(st / "spot_listing.json",
                                        ttl_minutes=int(getattr(self.cfg.v3.universe, "spot_listing_ttl_minutes", 1440)))
        self._spot_provider_factory_override = None
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
                                            tp1_fraction=Decimal(str(v3.futures_v3.tp1_fraction)),
                                            breakeven_at_mfe_r=Decimal(str(getattr(v3.futures_v3, "breakeven_at_mfe_r", 0.0))),
                                            tax_policy=TaxPolicy.disabled())
        self.spot2 = SpotLedger.load(st / "spot_ledger.json", starting_cash=cfg.risk.starting_equity_usdt)
        self.ledger = self.ledger2          # legacy yardımcılar (learning_notes/summary) v2 defteri görsün
        self.filters = FiltersCache(cfg.cache_path / "symbol_filters.json")
        # KARAR CERCEVESI PROVENANSI (tur basina yeniden kurulur): sembol -> hangi piyasadan
        # geldi ve o sembolde YENI girise guvenilebilir mi.
        self._frame_provenance: dict[str, dict] = {}
        self._entry_data_blocked: set[str] = set()
        # Pattern kaniti onbellegi: anahtar (sembol, indeks son bari). Indeks tur icinde
        # degismedigi icin ayni cevap yeniden hesaplanmaz (olculdu: sorgu basina 12,6 sn).
        self._pattern_cache: dict[tuple, dict] = {}
        # Arka plan arsiv/indeks yenileyicisi (kapaliysa None). Ilk turda baslatilir.
        self._refresher = None
        # YÜRÜTME HASSASİYETİ: yapılandırma defterin kapısını belirler (varsayılan KAPALI → davranış
        # birebir eski). Açıkken doğrulanmamış adımla yeni giriş açılmaz; çıkışlar etkilenmez.
        self.ledger2.require_verified_precision = bool(getattr(v3.execution, "require_verified_precision", False))
        self._filters_refresh_result: dict | None = None
        # --- coin heads
        ch = v3.coin_heads
        self.head_cfg = CoinHeadConfig(consensus_threshold=ch.consensus_threshold, min_confidence=ch.min_confidence, min_expected_r=ch.min_expected_r,
                                       fee_taker_pct=v3.fees.futures_taker_pct, spot_fee_pct=v3.fees.spot_taker_pct, slippage_pct=v3.fees.slippage_bps / 100,
                                       funding_horizon_bars=ch.funding_horizon_bars, max_leverage=self.profile.futures_max_leverage,
                                       equity_usdt=cfg.futures.starting_equity_usdt, risk_pct=self.profile.risk_per_trade_pct,
                                       decision_ttl_minutes=ch.decision_ttl_minutes,
                                       target_r_multiple=getattr(ch, "target_r_multiple", None),
                                       target2_r_multiple=getattr(ch, "target2_r_multiple", None))
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
        # --- KAYIPSIZ DERS SAKLAMA: `learning.json` içindeki `lessons` artık budanırken SİLİNMEZ.
        # Taşan dersler önce mühürlenmiş segmente arşivlenir; arşiv yazılamazsa budama da yapılmaz.
        self.lesson_store = None
        try:
            if v3.learning_v3.lesson_archive_enabled:
                from .learn.lesson_store import LessonStore
                self.lesson_store = LessonStore(
                    st / v3.learning_v3.lesson_archive_dirname,
                    hot_window=v3.learning_v3.lesson_hot_window,
                    max_segments=v3.learning_v3.decision_archive_max_segments,
                    code_sha=getattr(cfg, "code_sha", None),
                    max_segments_scanned=v3.learning_v3.lesson_max_segments_scanned,
                    min_rotate_block=v3.learning_v3.lesson_min_rotate_block)
        except Exception as exc:  # noqa: BLE001 — arşiv kurulamazsa SİLME de yapılmaz
            log.warning("ders arşivi başlatılamadı (budama devre dışı, kayıp yok): %s", exc)
            self.lesson_store = None
        self.learner.lesson_store = self.lesson_store
        self.learner.hot_window = max(1, int(v3.learning_v3.lesson_hot_window))
        # --- öğrenme v2 (v1 `self.learner` korunur)
        self.memory = TradeMemory(st / "trade_memory.jsonl")
        # STRATEJİ KÂĞIT DEFTERİ (V10): tek kurallı trend, AYRI defter, ileri test. Kapalıyken None.
        self.strategy_books = []
        from .strategy_paper import StrategyBook, book_specs
        for _spec in book_specs(v3):
            _book = StrategyBook(cfg, profile=self.profile, killswitch=self.killswitch,
                                 filters_cache=self.filters, run_id="", spec=_spec)   # run_id turda atanir
            self.strategy_books.append(_book)
            log.info("STRATEJI KAGIT DEFTERI: name=%s atr_mult=%s baslangic=%s USDT state=%s",
                     _book.name, _book.atr_mult, float(_book.ledger.starting_equity), _book.state_dir)
        self.strategy_book = self.strategy_books[0] if self.strategy_books else None     # geriye uyumlu ad
        # V15: defterlerin kuralı 1d dışında dilim istiyorsa (box → 5m) motor onu GERÇEKTEN çeker. Aksi halde
        # defter her turda FRAME_MISSING alır ve hiç işlem açmaz. Liste kural kaydından; burada sabit yok.
        try:
            from . import paper_rules
            _want = {tf for b in self.strategy_books for tf in paper_rules.rule_timeframes(b.name)}
            _added = self.runner.ensure_timeframes(sorted(_want - set(self.runner.markets)))
            if _added:
                log.info("STRATEJI KAGIT DEFTERI: ek zaman dilimi cekilecek: %s", ", ".join(_added))
        except Exception as exc:  # noqa: BLE001 — dilim eklenemezse defter veri hukmunde acikca reddeder
            log.warning("kagit defter ek zaman dilimi eklenemedi: %s", exc)
        # FORMASYON PAPER TRADER V1 (2026-09-16): yeni listeleme oncelikli mum formasyonu defteri. Kapaliyken None;
        # tarayici ARKA PLAN is parcacigindadir (`ensure_pattern_scanner`), tur ve 60 sn cikis izleyicisi BEKLEMEZ.
        self.pattern_book = None
        self.pattern_scanner = None
        if getattr(v3, "pattern_trader", None) is not None and v3.pattern_trader.enabled:
            try:
                from .pattern_trader.book import PatternBook
                self.pattern_book = PatternBook(cfg, profile=self.profile, killswitch=self.killswitch,
                                                filters_cache=self.filters, section=v3.pattern_trader)
                log.info("FORMASYON PAPER TRADER: baslangic=%s USDT azami_pozisyon=%d aileler=%s state=%s",
                         float(self.pattern_book.ledger.starting_equity), int(v3.pattern_trader.max_open_positions),
                         ",".join(v3.pattern_trader.families), self.pattern_book.state_dir)
            except Exception as exc:  # noqa: BLE001 — defter kurulamazsa ana bot ETKILENMEZ
                log.exception("formasyon defteri kurulamadi (ana bot surer): %s", exc)
                self.pattern_book = None
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
        # --- PAPER LEARNING LOOP INTEGRITY V3: kapanış zinciri bütünlüğü -------------------
        # `provenance`: açılan pozisyonun KARAR ANI kimliği (defter bunu taşımaz).
        # `learned_index`: hangi kapanışın öğrenildiğinin AÇIK kaydı — idempotency çıkarım değil.
        # İkisi de append-only ve defterin DIŞINDADIR; `futures_ledger.json` etkilenmez.
        # Arıza turu ÇÖKERTMEZ: depolar `None` kalır ve baseline öğrenme aynen sürer.
        self.provenance = None
        self.learned_index = None
        self.mgmt_executor = None
        try:
            from .learn.position_mgmt import ManagementExecutor
            from .learn.provenance import ProvenanceStore
            from .learn.reconcile import LearnedIndex
            self.provenance = ProvenanceStore(st / "entry_provenance.jsonl")
            self.learned_index = LearnedIndex(st / "learned_closes.jsonl")
            # Çıkış politikası bu sürümde AKTİF DEĞİL: yalnız SHADOW sözleşmesi kurulur.
            self.mgmt_executor = ManagementExecutor()
        except Exception as exc:  # noqa: BLE001
            log.warning("kapanış zinciri altyapısı kurulamadı (baseline sürüyor): %s", exc)
        # --- EXIT GIVEBACK & PROFIT PROTECTION V1: açık pozisyon fiyat yolu (SALT GÖZLEM) ---
        # Yol kaydı yeni bir veri kaynağı EKLEMEZ: motorun zaten aldığı mark güncellemelerini
        # kullanır. Çıkış politikaları yalnız karşı-olgusal olarak değerlendirilir; yürütücü
        # `SHADOW`dur ve deftere DOKUNMAZ. Mevcut stop/TP davranışı DEĞİŞMEZ.
        self.path_store = None
        self.exit_policy_cfg = None
        self.exit_executor = None
        try:
            from .learn.exit_executor import ExitExecutor
            from .learn.exit_policy import ExitPolicyConfig
            from .learn.position_path import PositionPathStore
            _ex = v3.exit_policy
            self.exit_policy_cfg = ExitPolicyConfig.from_dict(
                {"policy_version": _ex.policy_version} | dict(_ex.policy or {}))
            # Mod doğrulaması `validate_v3`te fail-closed yapıldı; burada ikinci kez zorlanır.
            self.exit_executor = ExitExecutor(self.exit_policy_cfg, mode=_ex.action_mode)
            if _ex.path_enabled:
                self.path_store = PositionPathStore(
                    st / "position_path.jsonl",
                    min_interval_s=_ex.min_snapshot_interval_s,
                    min_r_change=_ex.min_r_change)
        except Exception as exc:  # noqa: BLE001 — çıkış gözlemi karar yolunu bloke EDEMEZ
            log.warning("çıkış yolu gözlemi kurulamadı (baseline sürüyor): %s", exc)
            self.path_store = None
            self.exit_executor = None
        # --- ENTRY SELECTIVITY CHALLENGER V1: aday snapshot'ı (SALT GÖZLEM) ------------------
        # Snapshot yeni bir veri kaynağı EKLEMEZ: sıralama anında zaten elde olan karar/plan/
        # chief nesnelerini yazar. Challenger'lar YALNIZ karşı-olgusal değerlendirilir; hiçbir
        # aile aktif giriş kararını, boyutu, kaldıracı, stop/TP'yi ya da RiskEngine'i ETKİLEMEZ.
        self.entry_snapshot_store = None
        self.entry_cfg = None
        self.entry_mode = "SHADOW"
        self.weekly_cfg = None
        self.candle_cfg = None
        self.weekly_challenger_cfg = None
        self.mtf_cfg = None
        self.mtf_mode = "SHADOW"
        self.experiment_cfg = None
        self.experiment_store = None
        self.experiment_mode = "SHADOW"
        #: pfexp_v1_1 DRAIN (kabul kapalı, yalnız mevcut simülasyonların takibi) — kurulamazsa None.
        self.experiment_drain = None
        self._experiment_drain_state = {"status": None, "reason": "NOT_CONFIGURED"}
        try:
            from .learn.entry_challenger import EntryChallengerConfig
            from .learn.entry_eval import ALLOWED_MODES as _EN_MODES
            from .learn.entry_snapshot import SCHEMA_VERSION as ENTRY_SNAPSHOT_SCHEMA
            from .learn.entry_snapshot import EntrySnapshotStore
            _en = v3.entry_selectivity
            self.entry_cfg = EntryChallengerConfig.from_dict(
                {"policy_version": _en.policy_version} | dict(_en.policy or {}))
            # Mod doğrulaması `validate_v3`te fail-closed yapıldı; burada İKİNCİ kez zorlanır:
            # bir config yolu atlanmış olsa bile motor SHADOW dışına ÇIKAMAZ.
            self.entry_mode = str(_en.mode or "SHADOW").upper()
            if self.entry_mode not in _EN_MODES:
                raise ValueError(f"entry_selectivity.mode={self.entry_mode} bu sürümde kapalı")
            if _en.snapshot_enabled:
                # ARŞİV-ÖNCE saklama: sıcak dosya tavanı aşınca satırlar önce sıkıştırılmış,
                # checksum'lı bir segmente mühürlenir; ancak ondan sonra sıcak dosyadan
                # çıkarılır. Arşiv kurulamazsa `archive=None` kalır → BUDAMA DA OLMAZ.
                _arc = None
                try:
                    from .learn.entry_snapshot import ENTRY_ARCHIVE_STREAM_ID
                    from .learn.journal_archive import SegmentArchive
                    _arc = SegmentArchive(
                        st / "entry_snapshot_archive", stream_id=ENTRY_ARCHIVE_STREAM_ID,
                        record_schema_version=ENTRY_SNAPSHOT_SCHEMA,
                        code_sha=getattr(cfg, "code_sha", None),
                        max_segments=int(_en.snapshot_archive_max_segments))
                except Exception as exc:  # noqa: BLE001 — arşivsiz çalışır, SİLMEZ
                    log.warning("giriş snapshot arşivi kurulamadı (budama kapalı): %s", exc)
                    _arc = None
                self.entry_snapshot_store = EntrySnapshotStore(
                    st / "entry_snapshot.jsonl",
                    max_per_cycle=int(_en.max_snapshots_per_cycle),
                    archive=_arc, max_lines=int(_en.snapshot_max_lines))
            # HAFTALIK BAĞLAM (F/G): saf, salt gözlem. Kurulamazsa baseline aynen sürer.
            if _en.weekly_context_enabled:
                from .learn.candle_context import CandleContextConfig
                from .learn.entry_challenger_v2 import WeeklyChallengerConfig
                from .learn.weekly_structure import WeeklyStructureConfig
                self.weekly_cfg = WeeklyStructureConfig.from_dict(
                    dict(_en.weekly_structure_policy or {}))
                self.candle_cfg = CandleContextConfig.from_dict(dict(_en.candle_policy or {}))
                self.weekly_challenger_cfg = WeeklyChallengerConfig.from_dict(
                    dict(_en.weekly_challenger_policy or {}))
            # MUM ONAYI (V4): OFF disinda ise baslangicta bir kez, okunur sekilde loglanir.
            _ccm = str(getattr(_en, "candle_confirmation_mode", "OFF") or "OFF").upper()
            if _ccm != "OFF":
                from .learn.candle_context import CandleContextConfig as _CCC
                if getattr(self, "candle_cfg", None) is None:
                    self.candle_cfg = _CCC.from_dict(dict(_en.candle_policy or {}))
                log.info("MUM ONAYI: mode=%s variant=%s policy=%s", _ccm,
                         _en.candle_confirmation_variant, self.candle_cfg.policy_version)
            # GRAFIK FORMASYONU ONAYI (V5): OFF disinda ise baslangicta bir kez loglanir.
            _chm = str(getattr(_en, "chart_confirmation_mode", "OFF") or "OFF").upper()
            if _chm != "OFF":
                from .chart_patterns import ChartPatternConfig as _CPC
                if getattr(self, "chart_cfg", None) is None:
                    self.chart_cfg = _CPC.from_dict(dict(_en.chart_policy or {}))
                log.info("GRAFIK ONAYI: mode=%s variant=%s policy=%s", _chm,
                         _en.chart_confirmation_variant, self.chart_cfg.policy_version)
            # PIYASA REJIMI KAPISI (V7): OFF disinda ise baslangicta bir kez loglanir.
            _rgm = str(getattr(_en, "regime_gate_mode", "OFF") or "OFF").upper()
            if _rgm != "OFF":
                log.info("REJIM KAPISI: mode=%s variant=%s (BTC 1d close > EMA200 -> UP)", _rgm,
                         _en.regime_gate_variant)
            # ÇOK ZAMAN DİLİMLİ LİKİDİTE TEYİDİ (H): saf, salt gözlem, SHADOW.
            # Mod burada da İKİNCİ kez zorlanır — config yolu atlanmış olsa bile H aktifleşemez.
            if getattr(_en, "mtf_enabled", False):
                from .learn.multitimeframe_context import MultiTimeframeConfig
                self.mtf_mode = str(getattr(_en, "mtf_mode", "SHADOW") or "SHADOW").upper()
                if self.mtf_mode not in _EN_MODES:
                    raise ValueError(f"entry_selectivity.mtf_mode={self.mtf_mode} kapalı")
                if bool(getattr(_en, "mtf_auto_promotion", False)):
                    raise ValueError("entry_selectivity.mtf_auto_promotion=true yasak")
                self.mtf_cfg = MultiTimeframeConfig.from_dict(dict(_en.mtf_policy or {}))
            # KARLILIK DENEYI (P0-P4): tamamen izole PAPER simulasyonu. Kanonik defter,
            # RiskEngine, muhasebe, gateway ve sermaye durumu ASLA yazilmaz.
            if getattr(_en, "experiment_enabled", False):
                from .learn.profitability_experiment import ExperimentConfig
                from .learn.profitability_store import ExperimentStore
                self.experiment_mode = str(
                    getattr(_en, "experiment_mode", "SHADOW") or "SHADOW").upper()
                if self.experiment_mode not in _EN_MODES:
                    raise ValueError(f"experiment_mode={self.experiment_mode} kapalı")
                if bool(getattr(_en, "experiment_auto_promotion", False)):
                    raise ValueError("experiment_auto_promotion=true yasak")
                _xp = self._experiment_identity_policy(
                    dict(getattr(_en, "experiment_policy", None) or {}))
                _eid = str(_xp.get("experiment_id") or ExperimentConfig().experiment_id)
                # Dosyalar deney KİMLİĞİNE göre adlanır: pfexp_v1 dosyaları asla yazılmaz.
                self.experiment_store = ExperimentStore(st, experiment_id=_eid)
                # `evaluation_start_at` BİR KEZ dondurulur (kimlik dosyası); config'den ALINMAZ,
                # başka bir deneyin başlangıcı DEVRALINMAZ, geriye/ileriye çekilemez.
                _frozen = self.experiment_store.freeze_identity(code_sha=self.code_sha())
                _xp["evaluation_start_at"] = _frozen["evaluation_start_at"]
                _xp["frozen_at"] = _frozen["frozen_at"]
                _xp["code_sha"] = self.code_sha()
                self.experiment_cfg = ExperimentConfig.from_dict(_xp)
                self._experiment_identity_source = str(_frozen.get("source") or "")
                # pfexp_v1_1: kabul KAPALI, yalnız mevcut simülasyonlar doğal kapanışa kadar
                # izlenir. Kimlik birebir yeniden kurulamazsa drain KURULMAZ (salt okunur).
                self._setup_experiment_drain(st, self.experiment_cfg)
        except Exception as exc:  # noqa: BLE001 — giriş gözlemi karar yolunu bloke EDEMEZ
            log.warning("giriş seçiciliği gözlemi kurulamadı (baseline sürüyor): %s", exc)
            self.entry_snapshot_store = None
            self.entry_cfg = None
            self.entry_mode = "SHADOW"
            self.weekly_cfg = None
            self.candle_cfg = None
            self.weekly_challenger_cfg = None
            self.mtf_cfg = None
            self.mtf_mode = "SHADOW"
            self.experiment_cfg = None
            self.experiment_store = None
            self.experiment_drain = None
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
    def _history_store(self):
        from .history import HistoryStore
        return HistoryStore(self.cfg.cache_path / self.cfg.v3.history.root_dir)

    def _index_symbols(self) -> list[str]:
        """İndekse alınacak semboller: giriş evreni ∪ AÇIK POZİSYONLAR.

        Açık pozisyon her koşulda içeridedir — evrenden çıkarılmış bir coinin çıkış
        değerlendirmesi de tarihsel bağlam ister. Tavan `IndexRefresher.max_symbols`tadır
        ve bellek sınırı oradan gelir.
        """
        eu = self.cfg.v3.entry_universe
        base = list(eu.symbols) if eu.enabled else list(self.cfg.coins)
        return list(dict.fromkeys(base + list(self.ledger2.positions)))

    def _build_pattern_index(self, symbols: list[str]):
        """`symbols` için YENİ bir `SimilarPatternEngine` kur. Dönen: (engine, {seri: son_bar}).

        Saf kurucudur: hiçbir şey yayımlamaz, mevcut indekse DOKUNMAZ. Yayım
        `IndexRefresher`ın işidir; böylece yarım kurulmuş bir indeks karar yoluna sızamaz.
        """
        from .patterns import SimilarPatternEngine
        store = self._history_store()
        want = set(symbols)
        series = [(m, s, t) for m, s, t in store.series()
                  if m == "futures" and t == "4h" and (not want or s in want)]
        if not series:
            return None, {}
        clusters = {s: name for name, syms in (self.cfg.v3.risk_profiles.clusters or {}).items() for s in (syms or [])}
        eng = SimilarPatternEngine(min_sample=30, horizon=self.head_cfg.funding_horizon_bars * 2,
                                   fee_pct=self.head_cfg.fee_taker_pct,
                                   slippage_pct=self.head_cfg.slippage_pct, clusters=clusters)
        btc = store.read("futures", "BTC/USDT", "4h")
        n = 0
        last_ts: dict[str, int] = {}
        for m, s, t in series:
            df = store.read(m, s, t)
            if len(df) < 200:
                continue
            fund = store.read("futures", s, "funding")
            n += eng.add_series(s, m, t, df, btc_df=btc if (s != "BTC/USDT" and len(btc)) else None,
                                funding_df=fund if len(fund) else None)
            last_ts[f"{s}|{m}|{t}"] = int(df["timestamp"].iloc[-1])
        return (eng if n else None), last_ts

    def _make_refresher(self):
        """Arka plan yenileyicisini kur (başlatmaz). Kapalıysa `None`."""
        hc = self.cfg.v3.history
        if not (hc.enabled and getattr(hc, "auto_refresh", False)):
            return None
        from .history.incremental import IncrementalUpdater
        from .patterns.refresher import IndexRefresher

        def _update(symbols, now_ms):
            upd = IncrementalUpdater(self._history_store(), self._futures_provider_factory(),
                                     market="futures", max_requests=int(hc.refresh_max_requests))
            return upd.update(symbols, tuple(hc.refresh_timeframes), now_ms=now_ms)

        return IndexRefresher(build_fn=self._build_pattern_index, symbols_fn=self._index_symbols,
                              update_fn=_update, interval_s=float(hc.refresh_minutes) * 60.0,
                              max_symbols=int(hc.refresh_max_symbols),
                              # Indeks YALNIZ 4h serisinden kurulur (`_build_pattern_index`),
                              # bu yuzden yeniden kurulumu yalniz 4h ilerlemesi tetikler.
                              index_timeframes=("4h",))

    def _load_pattern_engine(self):
        """Karar yolunun gördüğü indeks. Yenileyici açıksa YAYIMLANMIŞ paketten gelir.

        Yenileyici kapalıyken eski davranış aynen korunur: süreç başına bir kez kurulur.
        Açıkken indeks arka planda yenilenir ve tek atamayla yayımlanır; bu fonksiyon
        yalnızca okur ve hiçbir zaman kurulum için BEKLEMEZ.
        """
        r = getattr(self, "_refresher", None)
        if r is not None:
            b = r.bundle                      # tek okuma: yarım durum görülemez
            return None if b is None else b.engine
        if self._pattern_loaded:
            return self._pattern_engine
        self._pattern_loaded = True
        try:
            if not self.cfg.v3.history.enabled:
                return None
            eng, _last = self._build_pattern_index(self._index_symbols())
            self._pattern_engine = eng
            if eng is not None:
                log.info("pattern index: %d olay, %d seri", len(eng.events), len(eng.candles))
        except Exception as exc:  # noqa: BLE001 — kanıt yoksa Coin Head kanıtsız çalışır (specialist usable=False)
            log.warning("pattern index kurulamadı: %s", exc)
            self._pattern_engine = None
        return self._pattern_engine

    def ensure_index_refresher(self) -> dict:
        """Yenileyiciyi kur ve arka planda baslat. HEMEN doner — kurulum beklemez.

        Ilk cagride paket henuz yayimlanmamis olabilir; o turda pattern kaniti YOKTUR ve
        bu dogru davranistir (uydurma kanit yerine kanitsiz karar). Bir sonraki turda
        paket hazirdir.
        """
        if getattr(self, "_refresher", None) is not None:
            return self._refresher.status()
        r = self._make_refresher()
        if r is None:
            return {"enabled": False, "reason": "history.auto_refresh kapalı"}
        self._refresher = r
        r.start()
        log.info("indeks yenileyicisi başladı: her %.0f dk, en fazla %d sembol",
                 r.interval_s / 60.0, r.max_symbols)
        return r.status()

    def index_refresh_status(self) -> dict:
        r = getattr(self, "_refresher", None)
        if r is None:
            return {"enabled": False, "reason": "history.auto_refresh kapalı",
                    "index": None if self._pattern_engine is None else
                    {"version": 0, "events": len(self._pattern_engine.events),
                     "series": len(self._pattern_engine.candles)}}
        return r.status()

    def _pattern_index_version(self) -> int:
        """Yayımlanmış indeksin sürümü; yenileyici kapalıysa 0 (tek, değişmeyen indeks)."""
        r = getattr(self, "_refresher", None)
        b = r.bundle if r is not None else None
        return int(b.version) if b is not None else 0

    def _pattern_evidence(self, symbol: str, now_ms: int) -> dict | None:
        """Sembol için LONG/SHORT kanıtı; veri 3 bardan eskiyse (bayat) kanıt verilmez. state/evidence/<sym>.json'a paket + açıklama yazılır.

        SONUÇ ÖNBELLEKLENİR ve anahtarı indeksin SON BARIDIR. Gerekçe ölçüldü: `SimilarPatternEngine`
        mum tablosunu `_load_pattern_engine` içinde bir kez kurar ve tur içinde GÜNCELLEMEZ; bu yüzden
        aynı sembol/yön sorgusu süreç boyunca AYNI cevabı verir. 138.891 olaylı indekste tek sorgu
        12,6 sn sürüyordu ve on sembol × iki yön ile tur başına 251 sn ediyordu — turun %88'i, her
        turda yeniden hesaplanan özdeş bir sonuç için.

        Anahtar indeksin son barı olduğu için önbellek YANLIŞ TAZE olamaz: indeks yeni veriyle
        kurulursa (yeni süreç ya da yeniden yükleme) anahtar değişir ve kanıt yeniden hesaplanır.
        Bayatlık kapısı önbellekten ÖNCE çalışır: eski bir cevap, veri bayatladıktan sonra
        döndürülmez.
        """
        eng = self._load_pattern_engine()
        if eng is None or (symbol, "futures", "4h") not in eng.candles:
            return None
        try:
            last_ts = int(eng.candles[(symbol, "futures", "4h")]["timestamp"].iloc[-1])
            if now_ms - last_ts > 3 * 14_400_000:
                return None
            cache = getattr(self, "_pattern_cache", None)
            if cache is None:
                cache = self._pattern_cache = {}
            # ANAHTAR = (sembol, indeks sürümü, indeksin son barı). Sürüm, arka planda yeni
            # bir indeks YAYIMLANDIĞINDA artar; böylece yenileme önbelleği kesin olarak
            # geçersiz kılar. Son bar ayrıca tutulur: sürüm hiç artmasa bile (yenileyici
            # kapalı) yeni veriyle kurulmuş bir indeks eski cevabı ALAMAZ.
            version = self._pattern_index_version()
            key = (symbol, version, last_ts)
            hit = cache.get(key)
            if hit is not None:
                return hit
            if cache and any(k[1] != version for k in cache):
                # Eski sürüm girdileri erişilemez; bellekte de tutulmaz.
                for k in [k for k in cache if k[1] != version]:
                    cache.pop(k, None)
            from .patterns import explain_tr, packet_from_query
            ev = {side: eng.query(symbol, "futures", "4h", side, k=60) for side in ("LONG", "SHORT")}
            packets = {side: packet_from_query(r, decision_id=stable_id("evidence", self.run_id, symbol, side), timestamp=iso(utc_now()), timeframes=["4h"]) for side, r in ev.items()}
            atomic_write_json(self.cfg.state_path / "evidence" / f"{symbol.replace('/', '_')}.json",
                              {"symbol": symbol, "run_id": self.run_id, "generated_at": iso(utc_now()), "packets": {s: p.to_dict() for s, p in packets.items()},
                               "explanation_tr": {s: explain_tr(p) for s, p in packets.items()}, "neighbors": {s: r.get("neighbors", [])[:10] for s, r in ev.items()}}, indent=1)
            cache[key] = ev
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
                    lesson = self.learner.learn(legacy)
                    # PROVENANS: düğüm anahtarlarını YALNIZ v2 öğrenici üretir ve DÖNDÜRÜR.
                    # Dönüş atılırsa indekse `learning_keys` HİÇ yazılmaz (bkz. note_learned).
                    v2_lesson = self.learner2.on_trade_closed(legacy | {"features": legacy.get("features") or {}},
                                                              {"regime": snap.get("regime"), "consensus_score": snap.get("consensus_score"),
                                                               "dissent": snap.get("dissent"), "vetoes": snap.get("vetoes")})
                    self._journal_outcome(legacy, lesson)
                    # `exit_check` ile AYNI boşluk: gap-reconcile kapanışları da indekse yazılmalı.
                    from .learn.reconcile import note_learned
                    note_learned(getattr(self, "learned_index", None), legacy, lesson,
                                 source="GAP_RECONCILE",
                                 learning_keys=(v2_lesson or {}).get("learning_keys"))
                except Exception as exc:  # noqa: BLE001
                    log.exception("gap-reconcile öğrenme hatası: %s", exc)

    # ------------------------------------------------------------------ hızlı çıkış monitörü (tur beklemeden)
    def exit_check(self) -> list[dict]:
        """Açık pozisyonlar için canlı fiyatla stop/TP/likidasyon/zaman kontrolü + defter kaydı + öğrenme; tur/tarama beklemez.
        Yeni giriş AÇMAZ. Dönen: kapanan işlemlerin legacy dict'leri."""
        self.ensure_gap_reconciled()
        self._strategy_paper_exit_check()
        self._pattern_exit_check()
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
            # FİYAT YOLU: bu yol yalnız SON FİYATI bilir (bar uçları YOK) ve bunu açıkça
            # `last_only` olarak işaretler. Kapanış kontrolünden SONRA çağrılır ki kapanan
            # pozisyon için yanıltıcı bir "açık pozisyon" snapshot'ı yazılmasın.
            from .learn.position_path import TICK_LAST_ONLY
            self._record_position_path(marks, None, now, tick_kind=TICK_LAST_ONLY)
            out = []
            for rec in records:
                legacy = rec.to_legacy_dict()
                snap = self.last_decisions.get(rec.symbol) or {}
                try:
                    lesson = self.learner.learn(legacy)
                    # PROVENANS: v2 dersinin DÖNÜŞÜ tutulur; `lesson` legacy öğreniciden gelir
                    # ve `learning_keys` İÇERMEZ (bkz. note_learned sözleşmesi).
                    v2_lesson = self.learner2.on_trade_closed(legacy | {"features": legacy.get("features") or {}}, {"regime": snap.get("regime"), "consensus_score": snap.get("consensus_score"),
                                                                                                                    "dissent": snap.get("dissent"), "vetoes": snap.get("vetoes")})
                    self._journal_outcome(legacy, lesson)
                    # ÖĞRENİLDİ KAYDI — bu yol eskiden indekse HİÇ yazmıyordu. Ders sıcak
                    # pencereden (200) arşive döndükten sonra kapanış "eksik" görünüp İKİNCİ
                    # kez öğrenilebilirdi; kapanışların çoğu bu 60 sn'lik monitörden geçer.
                    from .learn.reconcile import note_learned
                    note_learned(getattr(self, "learned_index", None), legacy, lesson,
                                 source="EXIT_MONITOR",
                                 learning_keys=(v2_lesson or {}).get("learning_keys"))
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

    # ------------------------------------------------------------------ VERI KIMLIGI (2026-09-16): provenans bagi + dogrulanmis perp fiyati
    def _bind_provenance(self, symbol: str) -> None:
        """Sembolun bu turdaki cerceve provenansini GERCEKTEN yuklenen veriye baglar: tur kimligi, dilim basina son bar
        zaman damgasi/satir sayisi ve (varsa) canli snapshot'taki USDS-M perpetual mark fiyati. `run_symbol` basarisiz olursa
        cagrilmaz -> eski turun onayi/cercevesi bu turda dogrulanamaz (fail-closed). Kagit defterler bu bagi
        `strategy_paper.verify_paper_data` ile kontrol eder; panel/grafik kaydi da ayni sozlugu okur."""
        prov = (getattr(self, "_frame_provenance", None) or {}).get(symbol)
        if not isinstance(prov, dict):
            return
        frames = self.runner.last_frames.get(symbol) or {}
        bound: dict[str, dict] = {}
        for tf, df in frames.items():
            try:
                if df is None or len(df) == 0:
                    continue
                ts = int(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else int(df.index[-1].value // 1_000_000)
                bound[tf] = {"last_ts": ts, "n": int(len(df))}
            except (AttributeError, TypeError, ValueError, KeyError, IndexError):
                continue
        prov["tour_id"] = str(self.run_id)
        prov["frames"] = bound
        prov["bound_at"] = iso()
        prov["as_of_ms"] = int(getattr(self, "_tour_now_ms", 0) or 0) or int(utc_now().timestamp() * 1000)   # turun karar saati (kural bu anda kapanmis barlari okur)
        # KAYIT (fiyat kaynagi DEGIL): tur aninda gorulen perp mark ve zamanlari. Kagit defter fiyati her kontrolde
        # `_paper_marks` ile canli sagladan alinir; bagli fiyat sonraki 60 sn kontrollerinde YENIDEN KULLANILMAZ (2026-09-16).
        try:
            from .strategy_paper import verified_price
            snap = self.runner.live.snapshot(symbol) or {}          # run_symbol az once cekti: onbellekten (ag yok)
            v = verified_price(snap, now_ms=int(utc_now().timestamp() * 1000))   # kayit: BAGLAMA aninda fiyatin yasi
            prov["perp_mark"] = ({"price": v["mark"], "ts": snap.get("ts"), "price_ts_ms": v["price_ts_ms"], "fetched_at_ms": v["fetched_at_ms"],
                                  "age_s": v["age_s"], "fresh": bool(v["ok"]), "reason": v["reason"]} if v["mark"] > 0 else None)
        except Exception:  # noqa: BLE001
            prov["perp_mark"] = None

    def _paper_marks(self, symbols, *, now: datetime | None = None) -> tuple[dict[str, TickData], dict[str, float], dict[str, dict]]:
        """Kagit defterler (T2/M2) icin DOGRULANMIS ve GUNCEL USDS-M perpetual fiyati — spot ticker DEGIL (fiyat-yalniz tick).

        ZAMAN SOZLESMESI (2026-09-16): her kontrolde canli sagla (`runner.live.snapshot`, sembol basina paylasilan 60 sn
        onbellek: ana defter, T2 ve M2 ayni istegi tekrarlamaz) sorulur; hukum `strategy_paper.verified_price` (sonlu/pozitif
        mark, kaynak zamani `funding.ts` yoksa alinma zamani, `now`ya gore yas <= PRICE_MAX_AGE_S, gelecekte degil). Turun
        provenansina bagli `perp_mark` KAYITTIR, fiyat kaynagi degil: tur kimligi ayni diye eski fiyat yeniden kullanilmaz.
        Bar uclari (1h high/low) bu tick'e EKLENMEZ — kapanmis bar uclari ayri sozlesmede (`_paper_closed_bars` →
        `StrategyBook.apply_closed_bars`). Gecerli fiyat yoksa/bayatsa: tick YOK (uydurma gerceklesme yok), bosluk `gaps`
        ile gerekceli gorunur (NO_VERIFIED_FUTURES_PRICE | STALE_FUTURES_PRICE | INVALID_FUTURES_PRICE_TIME), izleme surer."""
        from .strategy_paper import verified_price
        now = now or utc_now()
        now_ms = int(now.timestamp() * 1000)
        out: dict[str, TickData] = {}
        outf: dict[str, float] = {}
        gaps: dict[str, dict] = {}
        for sym in dict.fromkeys(symbols):
            try:
                snap = self.runner.live.snapshot(sym) or {}
            except Exception as exc:  # noqa: BLE001
                snap = {"errors": ["snapshot: %s" % exc]}
            v = verified_price(snap, now_ms=now_ms)
            if not v["ok"]:
                gaps[sym] = {"reason": v["reason"], "detail": v["detail"], "at": iso(now),
                             "price_ts": iso(datetime.fromtimestamp(v["price_ts_ms"] / 1000.0, tz=timezone.utc)) if v.get("price_ts_ms") else None,
                             "age_s": v["age_s"], "last_seen_mark": v["mark"] if v["mark"] > 0 else None}
                continue
            mark = float(v["mark"])
            out[sym] = TickData(last=Decimal(str(mark)), mark=Decimal(str(mark)),
                                ts=iso(datetime.fromtimestamp(v["price_ts_ms"] / 1000.0, tz=timezone.utc)))   # fiyatin KAYNAK zamani
            outf[sym] = mark
        return out, outf, gaps

    def _paper_closed_bars(self, symbols, marks_f: dict[str, float]) -> dict[str, dict]:
        """Kagit defterler icin kapanmis 1h barlari — YALNIZ bu turda provenansi USDM_PERP olan ve bagli cerceveden
        (SPOT ikamesi fitili futures stop'unu tetiklemez). Hangi barin uygulanacagina (kapanis, pozisyon acilisi, imlec)
        defter karar verir (`StrategyBook.apply_closed_bars`); burada yalniz ham satirlar ve canli mark tasinir."""
        from .strategy_paper import BAR_TIMEFRAME
        out: dict[str, dict] = {}
        for sym in dict.fromkeys(symbols):
            prov = (getattr(self, "_frame_provenance", None) or {}).get(sym) or {}
            if prov.get("tour_id") != str(self.run_id) or prov.get("market") != "USDM_PERP":
                continue
            h1 = (self.runner.last_frames.get(sym) or {}).get(BAR_TIMEFRAME)
            if h1 is None or not len(h1) or "timestamp" not in h1.columns:
                continue
            try:
                tail = h1.tail(48)
                rows = [{"timestamp": int(t), "high": float(h), "low": float(lo), "close": float(c)}
                        for t, h, lo, c in zip(tail["timestamp"], tail["high"], tail["low"], tail["close"])]
            except (KeyError, TypeError, ValueError):
                continue
            # PIYASA KIMLIGI (2026-09-17): cerceve provenansi barlarla BIRLIKTE tasinir — defter kimligi fiyat
            # bandindan TAHMIN ETMEZ, bildirilen piyasayi denetler (`apply_closed_bars_to_ledger`).
            out[sym] = {"tf": BAR_TIMEFRAME, "rows": rows, "mark": float(marks_f.get(sym) or 0.0),
                        "market": str(prov.get("market")), "source": prov.get("source"), "tour_id": prov.get("tour_id"),
                        "first_bar_ms": rows[0]["timestamp"] if rows else 0}
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
        self._tour_now_ms = now_ms          # ZAMAN SOZLESMESI: turun karar saati; provenans bagi ve kagit defter kurali bu ana gore okur
        st = self.cfg.state_path
        # 0) heartbeat + kill switch tetikleri
        atomic_write_json(st / "heartbeat.json", {"at": iso(now), "run_id": self.run_id, "pid": __import__("os").getpid()})
        # 0.4) BELLEK: onceki tur istisna ile bittiyse aday memosu asili kalabilir; tur basinda
        #      savunmaci olarak birakilir (`tour()` genelinde try/finally YOK).
        self._drop_entry_snapshot_cache()
        # 0.5) restart sonrası kesinti penceresi uzlaştırması (süreç başına bir kez; belirsizse giriş kilidi)
        self.ensure_gap_reconciled()
        # 0.6) VENUE OLAYLARI (gözlem): sözleşme/funding değişiklikleri.
        # BURADA, `exit_check` içinde DEĞİL: hızlı çıkış monitörü stop/TP/likidasyon için
        # hiçbir ağ isteğini BEKLEMEZ. Arıza turu durdurmaz, olay üretmez.
        try:
            self._venue_events = self.ensure_venue_events()
        except Exception as exc:  # noqa: BLE001
            log.warning("venue olay toplama hatası (tur sürer): %s", exc)
            self._venue_events = {"ok": False, "error": str(exc)}
        # 0.7) ARSIV/INDEKS YENILEYICISI — arka planda. `start()` is parcacigini kurar ve
        # HEMEN doner; tur yenilemeye ASLA blok olmaz, dolayisiyla stop/TP yonetimi de
        # gecikmez (`watch` dongusu tek is parcaciklidir, bkz. `patterns/refresher`).
        try:
            self.ensure_index_refresher()
        except Exception as exc:  # noqa: BLE001 — yenileyici arizasi turu durdurmaz
            log.warning("indeks yenileyicisi başlatılamadı (tur sürer): %s", exc)
        # 0.6) yürütme hassasiyeti: kapı AÇIKSA bayat/eksik sembol filtrelerini resmi kaynaktan yenile
        #      (ağırlık 1). Kapalıyken hiçbir istek atılmaz — eski davranış birebir korunur.
        self.ensure_symbol_filters()
        # 0.7) KANIT ONARIMI V1.1: spot listeleme onbellegi (ceza acikken, gunde ~1 istek)
        self.ensure_spot_listing()
        # 1) TARA (legacy tier-1)
        scan = None
        if self.scanner and do_scan and symbols_override is None:
            try:
                scan = self.scanner.scan()
                from .scanner import persist_scan
                persist_scan(scan, st)
                self.last_scan, self.last_scan_at = scan, time.time()
                # DEGERLENDIRME EVRENI: taramanin ZATEN cektigi veriden turetilir (ek API
                # cagrisi YOK, rate-limit guvenli). Yalniz YENI taramada yenilenir (cadence
                # = scan_every). Ariza turu durdurmaz.
                try:
                    from .universe_eval import build_eval_universe
                    _u = self.cfg.v3.universe
                    prev_u = read_json(st / "universe_eval.json", default=None)
                    doc = build_eval_universe(
                        scan, target_min=_u.eval_target_min, target=_u.eval_target,
                        target_max=_u.eval_target_max, prev=prev_u, run_id=self.run_id,
                        now_iso=iso(now), flag_score=float(getattr(self.scanner, "flag_score", 0)),
                        deep_symbols=tuple(r.symbol for r in scan.setups))
                    atomic_write_json(st / "universe_eval.json", doc)
                    self._eval_universe = doc
                except Exception as exc:  # noqa: BLE001
                    log.warning("değerlendirme evreni üretilemedi (tur sürer): %s", exc)
            except Exception as exc:  # noqa: BLE001 — tarama hatası turu durdurmaz; kayıt altına alınır
                log.exception("Tarama hatası: %s", exc)
                scan = self.last_scan
        scan_map = {r.symbol: r for r in (scan.setups if scan else [])}
        core = list(self.cfg.scanner.core_coins) if self.scanner else list(self.cfg.coins)
        _eu = self.cfg.v3.entry_universe
        _scan_setup_symbols = [r.symbol for r in (scan.setups if scan else [])]
        if symbols_override:
            symbols = list(dict.fromkeys(symbols_override))
        elif _eu.enabled:
            # SABIT EVREN: tur kapsami = evren ∪ ACIK POZISYONLAR. Acik pozisyon evrende
            # olmasa bile kapsamda kalir; cikis yonetimi fiyat gerektirir ve liste degisikligi
            # pozisyon kapatma gerekcesi DEGILDIR. Tarayici adaylari yalnizca acikca istenirse
            # eklenir (varsayilan: hayir — API/LLM tuketimi ve giris yuzeyi dar tutulur).
            from .entry_universe import tour_symbols
            _extra = _scan_setup_symbols if (_eu.analyze_outside or _eu.scanner_feeds_entries) else []
            symbols = tour_symbols(universe=_eu.symbols, open_positions=list(self.ledger2.positions) + self._strategy_open_symbols(),
                                   extra=_extra)
        else:
            symbols = list(dict.fromkeys(core + _scan_setup_symbols + list(self.ledger2.positions) + self._strategy_open_symbols()))
        core_set = set(self.cfg.coins) | set(core)
        # VERI KIMLIGI: giris evreni USDⓈ-M perpetual sozlesmelerdir; bu sembollerin karar
        # cerceveleri de PERPETUAL mumlardan gelmelidir. `core_set` muafiyeti cerceveleri
        # TradingView `BINANCE:<SYM>` (SPOT) akisindan aldirir — evren sembolleri icin bu
        # muafiyet KALDIRILIR ve spot ikamesi fail-closed reddedilir.
        futures_required = set(_eu.symbols) if _eu.enabled else set()
        self._frame_provenance = {}
        self._entry_data_blocked: set[str] = set()
        # 2) legacy ajanlar → brief + raporlar
        self.runner.set_weights(self.learner.learned_agent_weights())
        analyses = self._load_last_analyses()
        briefs: list[CoinBrief] = []
        for s in symbols:
            pre = None
            if s not in core_set or s in futures_required:
                try:
                    pre = self.perp_frames(s)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s perp verisi alınamadı: %s", s, exc)
            _need = ("1d", "4h", "1h")
            _perp_ok = bool(pre) and all(pre.get(tf) is not None and len(pre.get(tf)) for tf in _need)
            if _perp_ok:
                self._frame_provenance[s] = {"market": "USDM_PERP", "source": "binance_usdm", "entry_ok": True}
            else:
                # SESSIZ SPOT IKAMESI YOK — ama ANALIZ de susturulmaz. Perpetual cerceve
                # eksikse sembol TradingView (SPOT) mumlariyla analiz edilmeye devam eder
                # (acik pozisyonun baglami, panel, cikis degerlendirmesi bunu gerektirir) ve
                # YENI GIRIS bu sembolde kapatilir. Iki soru ayridir: "ne gorebiliyoruz" ve
                # "neye guvenip pozisyon acabiliriz". Cikis yolu (`exit_check`/`ledger2.tick`)
                # zaten canli ticker'dan beslenir ve bundan ETKILENMEZ.
                self._frame_provenance[s] = {"market": "SPOT", "source": f"tradingview:{self.cfg.exchange.tv_exchange}",
                                             "entry_ok": not (s in futures_required),
                                             "reason": "FUTURES_FRAMES_UNAVAILABLE" if s in futures_required else ""}
                if s in futures_required:
                    self._entry_data_blocked.add(s)
                    log.warning("%s: perpetual çerçeve alınamadı — analiz SPOT ile sürer, YENİ GİRİŞ kapalı", s)
            try:
                b = self.runner.run_symbol(s, analyses.get(s), pre)
            except Exception as exc:  # noqa: BLE001
                log.exception("%s ajan hatası: %s", s, exc)
                self._frame_provenance.pop(s, None)      # bu turda yuklenmemis veri icin provenans YOK (eski cerceve onaylanmaz)
                continue
            self._bind_provenance(s)                     # provenans GERCEKTEN yuklenen cerceveye baglanir (tur kimligi + bar zamani)
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
            # HABER/OLAY BAGLAMI — GOZLEM. `news_catalyst` uzmani bunu okur ve bias'ini
            # 0'da tutar; hicbir kapiya, skora ya da boyuta girmez.
            if self.cfg.v3.news.enabled and live:
                try:
                    from .market.news import NewsStore, context_for_decision
                    live["news_context"] = context_for_decision(
                        NewsStore(st / "news.jsonl"), b.symbol, now_iso=iso(now),
                        window_hours=float(self.cfg.v3.news.context_window_hours),
                        limit=int(self.cfg.v3.news.max_items_in_decision))
                except Exception as exc:  # noqa: BLE001 — baglam arizasi turu durdurmaz
                    log.warning("%s haber baglami okunamadi: %s", b.symbol, exc)
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
        btc_dec = decisions.get("BTC/USDT")
        _chief_state = {"equity": state.equity, "open_positions": [o.to_dict() for o in state.open_positions],
                        "total_open_risk_usdt": state.total_open_risk_usdt,
                        # ADVISORY projeksiyon YETKILI kapiyla ayni kovayi olcsun:
                        # birlesik toplam kullanilirsa panel "sigmaz" derken motor kabul eder.
                        "futures_stop_risk_usdt": state.futures_stop_risk_usdt,
                        "spot_exposure_usdt": state.spot_exposure_usdt,
                        "pnl_today": state.realized_pnl_today,
                        "drawdown_pct": state.drawdown_pct}
        _btc_reg = btc_dec.regime if btc_dec else None
        # legacy chief (obsidian/alerts için) — v3 chief modunu yansıt.
        # `market_risk_mode` YALNIZ BTC rejimi + verdict sayilarindan turer (chief.py:93-99);
        # `d.opportunity`ye BAGIMLI DEGILDIR, bu yuzden ekonomik kapidan ONCE guvenle alinir.
        # `ChiefPortfolioManager.decide` SAFTIR: kararlari mutasyona ugratmaz, iki kez cagrilabilir.
        legacy_chief = self.runner.chief.decide(briefs)
        legacy_chief.generated_at = iso(now)
        legacy_chief.risk_mode = self.chief_mgr.decide(list(decisions.values()), _chief_state,
                                                       btc_regime=_btc_reg).market_risk_mode
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
        # ASAMA 2 -- EKONOMIK FIRSAT DEGERLENDIRMESI. Coin head yalnizca GEOMETRIK olarak gecerli plan
        # uretti; kabul/red karari burada tek bir buyuklukle verilir: conservative_net_edge_r.
        #
        # SIRA KRITIKTIR (2026-09-09 onarimi). Bu cagri eskiden `d.p_win` KALIBRE EDILMEDEN ONCE,
        # yukaridaki ogrenici dongusunden 48 satir once yapiliyordu. `_assess_opportunities` icindeki
        # `if d.p_win:` dali o anda HEAD onselini (`head.py:354`: 0.5 + 0.25*confidence, daima >= 0.5,
        # yani hicbir zaman falsy) okuyor ve hiyerarsik ogrenicinin olasiligini EZIYORDU. Uretim
        # verisinde olculdu: 2797 adayin 2797'sinde (%100) kapinin kullandigi olasilik HEAD onseliydi;
        # kayitli istatistiksel p_win HICBIR adayda kullanilmamisti. Ortalama sisme +0.988R/aday.
        # Ornek (NATGAS, 2026-09-08T13:51Z, KABUL EDILDI): kapi p=0.625 kullandi, istatistiksel
        # tahmin 0.342'ydi; gercek olasilikla brut beklenti -0.136R, yani islem ACILMAMALIYDI.
        # Kod kendi yorumunun ("kalibre model tahmini onceliklidir") tersini yapiyordu.
        self._assess_opportunities(decisions, briefs)
        # Yetkili chief karari: `d.opportunity` artik dolu, siralama/izinler dogru edge ile kurulur.
        chief = self.chief_mgr.decide(list(decisions.values()), _chief_state, btc_regime=_btc_reg)
        self.registry.chief = chief.to_dict()
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
        # 6b) STRATEJİ KÂĞIT DEFTERİ (V10): ana defterden SONRA, aynı marks/funding/bar ilerlemesiyle.
        self._strategy_paper_tour(symbols, marks, marks_f, funding, bar_advance, now)
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
            # PROVENANS: v2 dersinin DÖNÜŞÜ tutulur. `lessons[-1]` LEGACY öğrenicinindir ve
            # `learning_keys` İÇERMEZ; düğüm anahtarlarını yalnız v2 üretir.
            v2_lesson = self.learner2.on_trade_closed(legacy | {"features": legacy.get("features") or {}}, {"regime": snap.get("regime"), "consensus_score": snap.get("consensus_score"),
                                                                                                            "dissent": snap.get("dissent"), "vetoes": snap.get("vetoes")})
            self._journal_outcome(legacy, lessons[-1] if lessons else None)
            # ÖĞRENİLDİ KAYDI: bu kapanış bir daha öğrenilmeyecek. Kimlik deterministiktir
            # (`trade_id` + `closed_at` + `exit_reason`), bu yüzden restart/retry duplicate ÜRETMEZ.
            try:
                from .learn.reconcile import note_learned
                note_learned(getattr(self, "learned_index", None), legacy,
                             lessons[-1] if lessons else None,
                             learning_keys=(v2_lesson or {}).get("learning_keys"))
            except Exception as exc:  # noqa: BLE001 — indeks arızası öğrenmeyi geçersiz KILMAZ
                log.warning("öğrenildi kaydı yazılamadı: %s", exc)
        # CRASH PENCERESİ ONARIMI: defter `ledger2.save()` ile ÖNCE kalıcı olur, öğrenme SONRA
        # çalışır. Arada süreç ölürse `ledger2.tick()` o kapanışı bir daha DÖNDÜRMEZ ve işlem
        # kalıcı olarak öğrenilmemiş kalırdı. Bu çağrı eksik adımı tamamlar; normalde no-op'tur.
        chain_res = self._complete_close_chain()
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
        # 8b) GRAFIK ANALIZI (CHART ANALYSIS V1): karar kaydi (risk.json) ve planlar yazildiktan SONRA;
        #     salt gosterim kaydi — defter/ogrenme/kapi DEGISMEZ, ariza turu durdurmaz.
        self._chart_analysis_tour(symbols, marks_f, now)
        # 8c) FORMASYON PAPER TRADER: tarayici arka planda calisir; burada yalniz baslatma + ekonomik rapor yazilir.
        self._pattern_trader_tour(now)
        # Karar günlüğü: DEĞERLENDİRİLEN HER aday (kabul/red/veto) tek seferde yazılır.
        # Hot loop'un DIŞINDA, tur sonunda ve fail-safe: arıza turu bozmaz.
        self._journal_decisions(risk_log, decisions, now)
        # Açık pozisyon yönetim gözlemi + kapanış zinciri özeti. İKİSİ DE SALT OKUNURDUR:
        # motor bu dosyaları okumaz, yalnız yazar. `REDUCE/EXIT` bugün ADVISORY_ONLY'dir.
        self._write_position_management(marks, decisions, now)
        # Fiyat yolu: TUR tick'i 1h bar uçlarını da taşır (`_marks`), bu yüzden `bar_extremes`.
        from .learn.position_path import TICK_BAR_EXTREMES
        self._record_position_path(marks, decisions, now, tick_kind=TICK_BAR_EXTREMES)
        self._write_exit_eval(now)
        self._write_entry_eval(now)
        # KANIT ONARIMI V1: ufku dolan adaylar etiketlenir (ayri dosya, salt ekleme, fail-safe).
        self._label_entry_outcomes(now)
        # KARLILIK DENEYI — IZOLE PAPER. Kanonik hicbir seyi degistirmez; yalnizca kendi
        # olay defterine ve kitabina yazar. Ariza turu DURDURMAZ.
        self._run_profitability_experiment(now)
        # BELLEK: aday snapshot memosu SON TUKETICIDEN SONRA birakilir.
        # `_write_entry_eval` (yukarida) ve `_run_profitability_experiment` (hemen ustte)
        # ayni turda `by_candidate()` cagirir. Birakma bu ikisinin ARASINA konursa son
        # tuketici yeniden ayristirir VE memo turlar arasi kalici olur (~258 MB) — bagimsiz
        # dogrulamada olculdu. Dogru yer: her ikisinden de sonra.
        self._drop_entry_snapshot_cache()
        self._write_llm_status(now)
        self._learning_chain = self._write_learning_chain(chain_res, now)
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
        atomic_write_json(st / "history_refresh.json",
                          {"generated_at": iso(now), **self.index_refresh_status()})
        atomic_write_json(st / "frame_provenance.json",
                          {"generated_at": iso(now), "run_id": self.run_id,
                           "entry_universe": list(_eu.symbols) if _eu.enabled else [],
                           "universe_enabled": bool(_eu.enabled),
                           "entry_blocked_on_data": sorted(self._entry_data_blocked),
                           "by_symbol": self._frame_provenance})
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
        # GİRİŞ SEÇİCİLİĞİ: sıralamaya giren adayların KARAR ANI girdileri. Snapshot tur SONUNDA
        # (baseline kararı belli olunca) tek seferde yazılır; hiçbir sonuç alanı GİRMEZ.
        self._entry_pending = []
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
            # ---------------------------------------------------------------- 0) SABIT GIRIS EVRENI
            # Bu kapi YALNIZ yeni girisi baglar. Ayni sembolde ACIK bir pozisyon varsa fiyat
            # takibi, stop/TP yonetimi ve kapanis `exit_check`/`ledger2.tick` uzerinden AYNEN
            # surer; bu dal oralara dokunmaz. Evrenden cikarilan bir coin kapatilmaz, yalnizca
            # yeniden ACILMAZ.
            _eu = self.cfg.v3.entry_universe
            _uni_block = entry_block_reason(sym, market=market, universe=_eu.symbols,
                                            direction=d.direction, enabled=bool(_eu.enabled),
                                            allow_long=bool(_eu.allow_long), allow_short=bool(_eu.allow_short))
            if _uni_block:
                funnel["hard_safety_blocked"] += 1
                risk_log.append({"symbol": sym, "verdict": d.verdict.value, "risk_allowed": False,
                                 "risk_reasons": [ENTRY_UNIVERSE_GATE], "block_code": ENTRY_UNIVERSE_GATE,
                                 "block_detail": _uni_block, "hard_veto": True, "at": iso(now)})
                continue
            # VERI KIMLIGI KAPISI: karar cercevesi perpetual DEGILSE bu sembolde yeni giris yok.
            # Spot mumu perpetual sozlesmenin yerine GECMEZ (baz farki, funding, ayri likidite).
            if market == "USDM_PERP" and sym in getattr(self, "_entry_data_blocked", ()):
                funnel["hard_safety_blocked"] += 1
                risk_log.append({"symbol": sym, "verdict": d.verdict.value, "risk_allowed": False,
                                 "risk_reasons": ["DATA_INVALID"], "block_code": "DATA_INVALID",
                                 "block_detail": "FUTURES_FRAMES_UNAVAILABLE", "hard_veto": True, "at": iso(now)})
                continue
            funnel["ranked"] += 1
            entry = {"symbol": sym, "verdict": d.verdict.value, "chief_allow": bool(perm.get("allow")), "chief_reason": perm.get("reason"),
                     "chief_capacity_projection": perm.get("capacity_projection"),
                     "plan_notional": round(float(plan.notional or 0.0), 6),
                     "risk_allowed": None, "risk_reasons": [], "risk_warnings": [], "adjusted_notional": None,
                     "adjusted_leverage": None, "at": iso(now)}
            risk_log.append(entry)
            self._entry_capture(sym, d, plan, perm, state, entry, market, now)
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
            # ---------------------------------------------------------------- 2b) MUM ONAYI (kapasite TUKETMEZ)
            # Operator karari (V4): secili varyant ENFORCE ise gecmeyen aday ACILMAZ; tum
            # varyantlarin golge hukmu karar kaydina yazilir. Mantik `candle_confirmation.py`
            # icinde — replay AYNI fonksiyonu cagirir (tek kaynak).
            cc = self._candle_confirmation(sym, d.direction, now)
            if cc is not None:
                entry["candle_confirmation"] = cc
                if cc.get("blocks"):
                    funnel["candle_blocked"] += 1
                    entry["block_code"] = "CANDLE_VETO:" + str((cc.get("verdict") or {}).get("reason") or "?")
                    continue
            # ---------------------------------------------------------------- 2c) GRAFIK FORMASYONU ONAYI (kapasite TUKETMEZ)
            # V5: kirilisla teyitli cift dip/OBO/ucgen/bayrak. Mantik `chart_confirmation.py`
            # icinde — replay AYNI fonksiyonu cagirir (tek kaynak). OFF iken yol degismez.
            ch = self._chart_confirmation(sym, d.direction, now)
            if ch is not None:
                entry["chart_confirmation"] = ch
                if ch.get("blocks"):
                    funnel["chart_blocked"] += 1
                    entry["block_code"] = "CHART_VETO:" + str((ch.get("verdict") or {}).get("reason") or "?")
                    continue
            # ---------------------------------------------------------------- 2d) PIYASA REJIMI (kapasite TUKETMEZ)
            # V7: BTC gunluk close > EMA200 -> UP. Secili varyant ENFORCE ise gecmeyen aday ACILMAZ.
            # Mantik `regime_gate.py` icinde — replay AYNI fonksiyonu cagirir (tek kaynak).
            rg = self._regime_gate(sym, d.direction, now)
            if rg is not None:
                entry["regime_gate"] = rg
                if rg.get("blocks"):
                    funnel["regime_blocked"] += 1
                    entry["block_code"] = "REGIME_VETO:" + str((rg.get("verdict") or {}).get("reason") or "?")
                    continue
            feats = features_from_brief(b, self.runner.chief.decide(briefs), b.scan_score or None)
            feats.update({"initial_stop": plan.stop, "p_win": b.p_win, "regime": d.regime, "consensus_score": d.consensus_score, "consensus_conf": d.consensus_confidence,
                          "n_dissent": len(d.dissent), "n_vetoes": len(d.vetoes), "expected_r": d.expected_r, "expected_cost_pct": d.expected_cost, "market_type": market,
                          "spread_pct": next((r.metrics.get("spread_pct") for r in d.specialist_reports if r.agent_name == "orderbook_liquidity" and r.usable), None)})
            self._entry_attach_features(sym, feats)
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
            # YÜRÜTME KURALI BİR KEZ ÇÖZÜLÜR ve AYNI nesne önizleme → risk → defter boyunca kullanılır.
            f_sym, prec_prov = self._resolve_entry_filters(sym, market)
            entry["precision"] = prec_prov.to_dict()
            if f_sym is None:
                # Doğrulanmamış adım: YENİ GİRİŞ AÇILMAZ; sahte dolum/ACCEPT üretilmez, gölge kaydı yok.
                funnel["precision_unresolved"] += 1
                entry["block_code"] = "UNRESOLVED_PRECISION"
                continue
            exec_entry = self._execution_entry(sym, market, d.direction, b.price or plan.entry, marks.get(sym), filters=f_sym)
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
                         "min_notional": float(f_sym.min_notional)}
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
                pos = self._execute_futures_entry(
                    symbol=sym, direction=d.direction, ref_price=b.price, notional=notional,
                    leverage=int(rd.adjusted_leverage or 1), stop=plan.stop, targets=plan.targets,
                    filters=f_sym, provenance=prec_prov, setup_type=plan.entry_type,
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
            # GİRİŞ PROVENANCE'I: kapanışta outcome'u KARAR ANINA bağlayan tek köprü.
            # Defter `TradeRecord` içinde `decision_id` alanı YOKTUR ve karar günlüğü 20.000
            # satır tavanında arşive döndüğü için geçmişe dönük arama güvenilir değildir
            # (2026-09-02: 18 kapanışın yalnız 2'si bağlanabildi). Kimlik AÇILIŞ ANINDA yazılır.
            self._record_entry_provenance(trade_id=trade_id, symbol=sym, direction=d.direction,
                                          decision=d, plan=plan, risk_decision=rd, brief=b,
                                          notional=notional, leverage=plan_leverage, now=now)
            self.last_decisions[sym] = d.to_dict(include_reports=False)
            opened.append(desc)
            # fill sonrasi: yetkili defterlerden durum yenile -> ayni turdaki sonraki adaylar bu pozisyonu/marji/exposure'i gorur
            state, entries_allowed = self._refresh_after_fill(marks, risk_log, now)
            entry["state_after_fill"] = {"open_positions": len(state.open_positions), "used_margin": round(state.used_margin, 6),
                                         "total_open_risk_usdt": round(state.total_open_risk_usdt, 6), "persisted": entries_allowed}
        self._entry_flush(now)
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
                         tick: TickData | None = None, filters=None) -> float:
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
                                                    filters=(filters if filters is not None
                                                             else self.filters.get(symbol, MarketType.USDM_PERP)),
                                                    tick=tick))

    # ------------------------------------------------------------------ yürütme hassasiyeti (execspec)
    def _futures_provider_factory(self):
        """Resmi USDⓈ-M public sağlayıcı (gap-reconcile ile AYNI kalıp; test enjeksiyonu `_gap_provider_factory`)."""
        factory = self._gap_provider_factory
        if factory is not None:
            return factory()
        from .market.http import HttpClient
        from .market.providers import BinanceFuturesProvider
        from .market.ratelimit import BudgetPool
        pool = BudgetPool(safety=self.cfg.v3.data.rate_budget_safety)
        return BinanceFuturesProvider(HttpClient(BinanceFuturesProvider.base_url, pool.get("fapi.binance.com")))

    def _spot_provider_factory(self):
        """Resmi Binance SPOT public sağlayıcı (spot listeleme kapısı; test enjeksiyonu `_spot_provider_factory_override`)."""
        factory = getattr(self, "_spot_provider_factory_override", None)
        if factory is not None:
            return factory()
        from .market.http import HttpClient
        from .market.providers import BinanceSpotProvider
        from .market.ratelimit import BudgetPool
        pool = BudgetPool(safety=self.cfg.v3.data.rate_budget_safety)
        return BinanceSpotProvider(HttpClient(BinanceSpotProvider.base_url, pool.get("api.binance.com")))

    def ensure_spot_listing(self, *, force: bool = False) -> dict:
        """`spot_listing.json` bayatsa resmi spot exchangeInfo'dan yenile.

        YALNIZ `universe.futures_only_penalty_r > 0` iken ağa çıkar. Sağlayıcı hatası eski önbelleği
        KORUR (bayat ama kullanılabilir); hiç önbellek yoksa ceza fail-safe uygulanır (yasak değil).
        """
        u = self.cfg.v3.universe
        if float(getattr(u, "futures_only_penalty_r", 0.0) or 0.0) <= 0 and not force:
            return {"ok": True, "skipped": "gate_off"}
        sl = self.spot_listing
        if not force and not sl.is_stale():
            return {"ok": True, "skipped": "fresh", "fetched_at": sl.fetched_at, "n": sl.size}
        try:
            res = sl.refresh(self._spot_provider_factory())
        except Exception as exc:  # noqa: BLE001 — yenileme arızası turu düşürmez
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not res.get("ok"):
            log.warning("spot listeleme yenilenemedi: %s — önbellek %s", res.get("error"),
                        f"korunuyor (n={sl.size}, fetched_at={sl.fetched_at})" if sl.available else "YOK (kapı fail-closed)")
        else:
            log.info("spot listeleme yenilendi: %d sembol", int(res.get("n") or 0))
        return res

    def ensure_symbol_filters(self, *, force: bool = False) -> dict:
        """`symbol_filters.json` bayatsa resmi exchangeInfo'dan (ağırlık 1) STRICT yenile.

        YALNIZ `require_verified_precision=True` iken ağa çıkar; kapalıyken eski davranış (hiç
        istek yok). Sağlayıcı hatası eski önbelleği KORUR ve sonuç `_filters_refresh_result`
        içinde açıkça durur — sessiz 0.01 varsayılanı YOKTUR.
        """
        ex = self.cfg.v3.execution
        if not getattr(ex, "require_verified_precision", False) and not force:
            return {"ok": True, "skipped": "gate_off"}
        max_age = float(getattr(ex, "filters_max_age_hours", 24.0)) * 3600.0
        if not force and not self.filters.is_stale(max_age):
            return {"ok": True, "skipped": "fresh", "verified_at": self.filters.verified_at}
        from .accounting.filters import refresh_futures_filters
        try:
            provider = self._futures_provider_factory()
            res = refresh_futures_filters(self.filters, provider)
        except Exception as exc:  # noqa: BLE001 — yenileme arızası turu düşürmez, kapı açıkken giriş zaten kapanır
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "n_ok": 0}
        self._filters_refresh_result = res
        if not res.get("ok"):
            log.warning("sembol filtreleri yenilenemedi: %s — eski önbellek korunuyor (verified_at=%s)",
                        res.get("error"), self.filters.verified_at or "yok")
        else:
            log.info("sembol filtreleri yenilendi: %d doğrulandı, %d atlandı, %d hatalı",
                     res.get("n_ok", 0), len(res.get("skipped") or {}), len(res.get("errors") or {}))
        return res

    def ensure_venue_events(self, *, force: bool = False) -> dict:
        """Borsanin KENDI ucundan dogrulanabilir sozlesme degisikliklerini yakala (gozlem).

        Iki hafif istek (`exchangeInfo` + `fundingInfo`, agirlik 1) ve bir goruntu farki.
        Ilk calistirmada taban yazilir, OLAY URETILMEZ — "ilk kez gordum" degisiklik degildir.
        Saglayici hatasi onceki goruntuyu KORUR ve olay uretmez: erisilemeyen bir uc nokta
        "her sey degisti" anlamina gelmez.

        Uretilen kayitlar YALNIZ gozlemdir; hicbir kapiya, skora ya da boyuta girmez.
        """
        nw = self.cfg.v3.news
        if not (nw.enabled and nw.venue_events) and not force:
            return {"ok": True, "skipped": "gate_off"}
        st = self.cfg.state_path
        prev = read_json(st / "venue_snapshot.json", default=None) or {}
        now = utc_now()
        if not force and prev.get("observed_at"):
            age_min = (now - from_iso(prev["observed_at"])).total_seconds() / 60.0
            if age_min < float(nw.refresh_minutes):
                return {"ok": True, "skipped": "fresh", "age_minutes": round(age_min, 1)}
        eu = self.cfg.v3.entry_universe
        watch = list(eu.symbols) if eu.enabled else list(self.cfg.coins)
        watch = list(dict.fromkeys(watch + list(self.ledger2.positions)))
        from .market import venue_events as VE
        from .market.news import NewsStore
        items, snap, res = VE.collect(self._futures_provider_factory(), symbols=watch,
                                      prev={k: v for k, v in prev.items() if k in ("contracts", "funding")},
                                      observed_at=iso(now))
        if not res.get("ok"):
            log.warning("venue olaylari alinamadi: %s — onceki goruntu korunuyor", res.get("errors"))
            return res | {"added": 0}
        # PROJE DUYURULARI — venue olaylarindan AYRI bir kaynak: her coinin kendi
        # deposundan yayimlanan surumler. Erisim yoksa venue olaylari yine kaydedilir;
        # eksiklik `project_news` durumunda GORUNUR kalir, sessizce "kaynak yok" olmaz.
        self._project_news = {"enabled": False}
        if nw.project_releases:
            from .market.project_news import ProjectReleases
            pr = ProjectReleases(per_repo=int(nw.project_per_repo))
            try:
                items += pr.fetch(watch, now_iso=iso(now))
            except Exception as exc:  # noqa: BLE001 — proje kaynagi venue yolunu bozamaz
                log.warning("proje duyuruları alınamadı: %s", exc)
            self._project_news = pr.status() | {"enabled": True}
            if not self._project_news.get("ok"):
                log.warning("proje duyuru kaynağı kısmi: %s", self._project_news.get("errors"))
        added = NewsStore(st / "news.jsonl").add(items)
        atomic_write_json(st / "venue_snapshot.json", snap | {"observed_at": iso(now), "symbols": watch})
        for it in items:
            log.info("OLAY (%s): %s", it.category, it.title)
        return res | added | {"project_news": getattr(self, "_project_news", None)}

    def _resolve_entry_filters(self, symbol: str, market: str):
        """Yeni giriş için kuralı BİR KEZ, provenansıyla çözer → (filters | None, provenance).

        Kapı KAPALI: çözülemese de eski davranış (`FiltersCache.get` → varsayılan) korunur, yalnız
        provenans kaydedilir. Kapı AÇIK: çözülemezse `None` döner ve giriş `UNRESOLVED_PRECISION`
        ile bloke olur. Dönen nesne önizleme → risk → defter boyunca AYNI nesnedir (kimlik sabit).
        """
        from .execspec import resolve_rule
        mt = MarketType.SPOT if market == "SPOT" else MarketType.USDM_PERP
        ex = self.cfg.v3.execution
        gate = bool(getattr(ex, "require_verified_precision", False))
        max_age = float(getattr(ex, "filters_max_age_hours", 24.0)) * 3600.0 if gate else None
        f, prov = resolve_rule(symbol, cache=self.filters, market_type=mt, max_age_seconds=max_age)
        if f is None and not gate:
            f = self.filters.get(symbol, mt)                 # eski davranış: varsayılan filtre
        return f, prov

    def _execute_futures_entry(self, *, symbol: str, direction: str, ref_price, notional, leverage: int,
                               stop, targets, filters, provenance, setup_type: str = "", trigger_text: str = "",
                               features: dict | None = None, tick: TickData | None = None, now=None,
                               meta: dict | None = None):
        """Doğrulanmış teklifi AYNI filtre nesnesiyle deftere işler. Reddedilirse None döner.

        Kural provenansı pozisyon meta'sına yazılır (`meta.precision`); böylece dolumda hangi
        tick/step'in ve hangi kaynağın kullanıldığı pozisyonla birlikte kalıcıdır.
        """
        meta = dict(meta or {})
        meta["precision"] = provenance.to_dict() if hasattr(provenance, "to_dict") else (provenance or None)
        return self.ledger2.open(symbol, direction, ref_price,
                                 SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, int(leverage or 1)),
                                 stop=stop, targets=targets, filters=filters, setup_type=setup_type,
                                 trigger_text=trigger_text, features=features, tick=tick, now=now, meta=meta)

    def _trigger_fired(self, b: CoinBrief, direction: str, entry: float, entry_type: str) -> bool:
        """SAF sorgu: durum DEGISTIRMEZ. Mantik `entry_trigger.trigger_fired` icinde — TEK kaynak.

        Replay motoru AYNI fonksiyonu cagirir; "planlanan seviyeye gelmeden girme" kurali
        iki motorda ayri formulle YASAMAZ. Kayit yalniz gercek acilista islenir
        (`_execute_locked`); ayni bar/taraf/setup ikinci kez acilmasini `_seen_signals`
        (DUPLICATE_SIGNAL) engeller.
        """
        from .entry_trigger import trigger_fired
        ok, _why = trigger_fired(direction=direction, entry_type=entry_type, entry=entry,
                                 price=b.price, last_close=b.last_close_4h,
                                 last_bar=b.last_bar_4h,
                                 already_fired_bar=self.triggers.get(b.symbol))
        return ok

    def _candle_confirmation(self, symbol: str, direction: str, now: datetime) -> dict | None:
        """SAF sorgu: durum DEGISTIRMEZ. Mantik `candle_confirmation.candle_confirmation` icinde — TEK kaynak.

        `OFF` iken None doner ve karar yolu birebir eskisi gibidir. Barlar motorun ZATEN
        cektigi `runner.last_frames` karelerinden okunur (yeni API cagrisi YOK) ve karar
        aninda KAPANMAMIS bar elenir. Degerlendirme arizasi ENFORCE modda FAIL-CLOSED
        (aday acilmaz, sebep kayda gecer); SHADOW modda yalniz loglanir.
        """
        _en = self.cfg.v3.entry_selectivity
        mode = str(getattr(_en, "candle_confirmation_mode", "OFF") or "OFF").upper()
        if mode == "OFF":
            return None
        variant = str(getattr(_en, "candle_confirmation_variant", "") or "")
        try:
            from .candle_confirmation import candle_confirmation, closed_bars
            from .learn.candle_context import CandleContextConfig
            from .learn.weekly_structure import rows_from_frame
            cfg = getattr(self, "candle_cfg", None)
            if cfg is None:
                cfg = self.candle_cfg = CandleContextConfig.from_dict(dict(_en.candle_policy or {}))
            frames = (getattr(self.runner, "last_frames", None) or {}).get(symbol) or {}
            now_ms = int(now.timestamp() * 1000)
            b4 = closed_bars(rows_from_frame(frames.get("4h")), now_ms=now_ms, tf="4h")
            b1 = closed_bars(rows_from_frame(frames.get("1d")), now_ms=now_ms, tf="1d")
            return candle_confirmation(mode=mode, variant=variant, direction=direction,
                                       bars_4h=b4, bars_1d=b1, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 — ariza SESSIZ GECMEZ
            log.warning("mum onayi degerlendirilemedi (%s): %s", symbol, exc)
            return {"schema_version": "candle_confirmation_v1", "mode": mode, "variant": variant,
                    "verdict": {"ok": False, "reason": "CANDLE_ERROR:%s" % type(exc).__name__},
                    "blocks": mode == "ENFORCE", "shadow": {}, "error": str(exc)[:200]}

    def _chart_confirmation(self, symbol: str, direction: str, now: datetime) -> dict | None:
        """SAF sorgu: durum DEGISTIRMEZ. Mantik `chart_confirmation.chart_confirmation` icinde — TEK kaynak.

        `OFF` iken None doner. Barlar `runner.last_frames` karelerinden okunur (yeni API cagrisi
        YOK), kapanmamis bar elenir. Ariza ENFORCE modda FAIL-CLOSED; SHADOW modda loglanir.
        """
        _en = self.cfg.v3.entry_selectivity
        mode = str(getattr(_en, "chart_confirmation_mode", "OFF") or "OFF").upper()
        if mode == "OFF":
            return None
        variant = str(getattr(_en, "chart_confirmation_variant", "") or "")
        try:
            from .candle_confirmation import closed_bars
            from .chart_confirmation import chart_confirmation
            from .chart_patterns import ChartPatternConfig
            from .learn.weekly_structure import rows_from_frame
            cfg = getattr(self, "chart_cfg", None)
            if cfg is None:
                cfg = self.chart_cfg = ChartPatternConfig.from_dict(dict(_en.chart_policy or {}))
            frames = (getattr(self.runner, "last_frames", None) or {}).get(symbol) or {}
            now_ms = int(now.timestamp() * 1000)
            b4 = closed_bars(rows_from_frame(frames.get("4h")), now_ms=now_ms, tf="4h")
            b1 = closed_bars(rows_from_frame(frames.get("1d")), now_ms=now_ms, tf="1d")
            return chart_confirmation(mode=mode, variant=variant, direction=direction,
                                      bars_4h=b4, bars_1d=b1, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 — ariza SESSIZ GECMEZ
            log.warning("grafik onayi degerlendirilemedi (%s): %s", symbol, exc)
            return {"schema_version": "chart_confirmation_v1", "mode": mode, "variant": variant,
                    "verdict": {"ok": False, "reason": "CHART_ERROR:%s" % type(exc).__name__},
                    "blocks": mode == "ENFORCE", "shadow": {}, "error": str(exc)[:200]}

    def _regime_gate(self, symbol: str, direction: str, now: datetime) -> dict | None:
        """SAF sorgu: durum DEGISTIRMEZ. Mantik `regime_gate.regime_confirmation` icinde — TEK kaynak.

        BTC gunluk karesi `runner.last_frames["BTC/USDT"]["1d"]` (motorun ZATEN cektigi kare);
        kapanmamis bar elenir. BTC karesi yoksa rejim BILINMIYOR ve ENFORCE modda aday ACILMAZ
        (fail-closed). Ariza da ENFORCE modda fail-closed.
        """
        _en = self.cfg.v3.entry_selectivity
        mode = str(getattr(_en, "regime_gate_mode", "OFF") or "OFF").upper()
        if mode == "OFF":
            return None
        variant = str(getattr(_en, "regime_gate_variant", "") or "")
        try:
            from .candle_confirmation import closed_bars
            from .regime_gate import BTC_SYMBOL, regime_confirmation, rows_for_regime
            frames = (getattr(self.runner, "last_frames", None) or {}).get(BTC_SYMBOL) or {}
            now_ms = int(now.timestamp() * 1000)
            bars = closed_bars(rows_for_regime(frames.get("1d")), now_ms=now_ms, tf="1d")
            return regime_confirmation(mode=mode, variant=variant, direction=direction,
                                       btc_daily_bars=bars)
        except Exception as exc:  # noqa: BLE001 — ariza SESSIZ GECMEZ
            log.warning("rejim kapisi degerlendirilemedi (%s): %s", symbol, exc)
            return {"schema_version": "regime_gate_v1", "mode": mode, "variant": variant, "regime": None,
                    "verdict": {"ok": False, "reason": "REGIME_ERROR:%s" % type(exc).__name__},
                    "blocks": mode == "ENFORCE", "shadow": {}, "error": str(exc)[:200]}

    def _strategy_open_symbols(self) -> list[str]:
        """Strateji defterlerinin acik pozisyonlari: tur kapsamina girer ki cerceve/fiyat alinsin ve kural
        kapanisi calissin (ana bot bu sembolde pozisyonu kapatmis olsa bile)."""
        out: list[str] = []
        for b in (getattr(self, "strategy_books", None) or []):
            out.extend(list(b.ledger.positions))
        return list(dict.fromkeys(out))

    def _strategy_paper_tour(self, symbols, marks: dict, marks_f: dict, funding: dict, bar_advance: bool, now: datetime) -> None:
        """Tek kurallı stratejinin turu: kural → ayrı defter → tick → özet. Arıza ana turu DURDURMAZ."""
        books = list(getattr(self, "strategy_books", None) or [])
        if not books:
            return
        # GIRIS EVRENI (V13): defterler YALNIZ olculen sabit evrende (entry_universe.symbols) yeni pozisyon
        # acar; tur listesi ana defterin acik pozisyonlarini/tarayici adaylarini da icerir ve bunlar OLCULMEMIS
        # bir evrendir (ZEN/USDT olayi, 2026-09-13). Defterin kendi acik pozisyonlari yine de yonetilir.
        _eu = self.cfg.v3.entry_universe
        universe = list(_eu.symbols) if _eu.enabled else list(symbols)
        index = []
        # VERI KIMLIGI (2026-09-16): kagit defterler ana botun spot-ticker `marks`ini DEGIL, dogrulanmis USDS-M perpetual
        # fiyatini kullanir (`_paper_marks`); cerceve provenansi (tur kimligi + bar bagi) defter adimina tasinir.
        scope = list(dict.fromkeys([s for b in books for s in (b.symbols or universe)] + [s for b in books for s in b.ledger.positions]))
        # ZAMAN SOZLESMESI (2026-09-16): fiyat = kontrol anina gore GUNCEL dogrulanmis perp mark (fiyat-yalniz tick); kapanmis
        # 1h bar uclari ayri sozlesmeyle defterde uygulanir (pozisyon acilisindan sonra, bir kez); kural `now` aninda
        # kapanmis gunluk barlari okur ve ayni `as_of` ile guncellik denetlenir.
        tick_now = utc_now()
        pmarks, pmarks_f, pgaps = self._paper_marks(scope, now=tick_now)
        pbars = self._paper_closed_bars(scope, pmarks_f)
        if pgaps:
            log.warning("kagit defter: %d sembol icin gecerli/guncel perp fiyati YOK (tick yok): %s", len(pgaps),
                        ", ".join("%s=%s" % (s, g.get("reason")) for s, g in sorted(pgaps.items()))[:300])
        for book in books:
            try:
                book.run_id = str(getattr(self, "run_id", "") or "")
                syms = book.symbols or universe
                frames = {s: (self.runner.last_frames.get(s) or {}) for s in set(syms) | set(book.ledger.positions) | {"BTC/USDT"}}
                # 1) kural (giris/kural cikisi) — veri kimligi + guncellik hukmu ile (onceki sira korunur: kural once)
                book.step(symbols=list(syms), frames_by_symbol=frames, marks=pmarks, marks_f=pmarks_f, now=now,
                          provenance_by_symbol=self._frame_provenance, data_gaps=pgaps)
                # 2) gecmis OHLC: kapanmis 1h barlarin uclari — yalniz pozisyon acilisindan SONRA acilmis, tuketilmemis barlar
                #    (bu adimda acilan pozisyon icin hicbir bar uygun degildir: giris oncesi fitil yeni pozisyonu stop'lamaz;
                #    stop sonrasi ayni turda yeniden giris de olmaz — kural bir sonraki turda yeniden degerlendirir)
                book.apply_closed_bars(pbars, now=tick_now, funding_rate_lookup=static_rates(funding))
                # 3) canli fiyat kontrolu (fiyat-yalniz; bar_advance ana turun 4h bar ilerlemesi)
                book.tick(pmarks, now=tick_now, funding_rate_lookup=static_rates(funding), bar_advance=bar_advance)
                book.save(pmarks_f, tick_now)
                index.append({"key": book.key, "name": book.name, "summary_file": book.summary_file})
            except Exception as exc:  # noqa: BLE001 — bir defterin arızası ne ana botu ne diğer defteri ETKİLER
                log.warning("strateji kagit defteri turu basarisiz (%s): %s", book.key, exc)
        try:
            from .strategy_paper import INDEX_FILE
            atomic_write_json(self.cfg.state_path / INDEX_FILE, {"generated_at": iso(now), "books": index})
        except Exception as exc:  # noqa: BLE001
            log.warning("strateji defter indeksi yazilamadi: %s", exc)

    # ------------------------------------------------------------------ CHART ANALYSIS V1 (salt gosterim)
    def _chart_analysis_tour(self, symbols, marks_f: dict, now: datetime) -> None:
        """Her turda analiz anini kaydeder: yeni kapanmis bar ya da karar degisimi -> yeni analysis_id; ayni ise
        HIC yazmaz. Panelin okudugu kapi modlari/config ozeti `state/chart_analysis/config.json`a yazilir.
        Ariza ana turu, cikis yonetimini ve defterleri ETKILEMEZ (try/except)."""
        ca = getattr(self.cfg.v3, "chart_analysis", None)
        if ca is None or not getattr(ca, "enabled", False):
            return
        try:
            from .candle_confirmation import closed_bars
            from .chart_analysis import (BOOK_MAIN, HISTORY_TAIL, bars_from_frame, build_snapshot, closed_bars_at, code_sha, config_hash,
                                         gates_from_v3, plan_for_market, position_to_dict, spot_position_to_dict)
            from .chart_analysis import TF_MS
            from .chart_analysis_store import DIRNAME, ChartAnalysisStore
            from .ema200_trend import daily_rows_from_frame
            tf = str(getattr(ca, "timeframe", "4h") or "4h")
            step = int(TF_MS.get(tf, 14_400_000))
            as_of = int(now.timestamp() * 1000)
            store = ChartAnalysisStore(self.cfg.state_path, keep_per_series=int(getattr(ca, "keep_per_series", 300)))
            gates = gates_from_v3(self.cfg.v3)
            chash, code = config_hash(self.cfg.v3), code_sha()
            cfgd = {"swing_lookback": int(ca.swing_lookback), "cluster_tolerance_atr": float(ca.cluster_tolerance_atr),
                    "trendline_touch_tolerance_pct": float(ca.trendline_touch_tolerance_pct)}
            cfg_doc = {"generated_at": iso(now), "gates": gates, "cfg": cfgd, "timeframe": tf, "code_sha": code, "config_hash": chash,
                       "keep_per_series": int(getattr(ca, "keep_per_series", 300)), "skipped": {}}
            atomic_write_json(self.cfg.state_path / DIRNAME / "config.json", cfg_doc)
            skipped: dict[str, dict] = {}
            risk = read_json(self.cfg.state_path / "risk.json", default=None) or {}
            last_dec: dict[str, dict] = {}
            for e in (risk.get("last_decisions") or []):
                if isinstance(e, dict) and e.get("symbol"):
                    last_dec[str(e["symbol"])] = e
            btc_fr = (self.runner.last_frames.get("BTC/USDT") or {}).get("1d")
            btc_rows = closed_bars(daily_rows_from_frame(btc_fr, tail=320), now_ms=as_of, tf="1d") if btc_fr is not None else []
            books = [{"book_id": BOOK_MAIN, "name": BOOK_MAIN, "atr_mult": None, "ledger": self.ledger2, "memory": None}]
            for b in (getattr(self, "strategy_books", None) or []):
                books.append({"book_id": b.key, "name": b.name, "atr_mult": b.atr_mult, "ledger": b.ledger, "memory": b.memory})
            scope = list(dict.fromkeys(list(symbols) + [s for b in books for s in list(b["ledger"].positions)]))
            written = 0
            for sym in scope:
                fr = self.runner.last_frames.get(sym) or {}
                df = fr.get(tf)
                if df is None or len(df) < 30:
                    continue
                bars = closed_bars_at(bars_from_frame(df, tail=int(getattr(ca, "bars", 400))), as_of_ms=as_of, tf=tf)
                if not bars:
                    continue
                d1 = fr.get("1d")
                daily = closed_bars(daily_rows_from_frame(d1, tail=320), now_ms=as_of, tf="1d") if d1 is not None else []
                prov = (getattr(self, "_frame_provenance", None) or {}).get(sym) or {}
                # Cerceve (mum) piyasasi YALNIZ provenanstan: provenans yoksa kaynak KANITSIZ USDM_PERP sayilmaz (2026-09-16 #3).
                bar_market = str(prov.get("market")) if prov.get("market") in ("USDM_PERP", "SPOT") else None
                btc_market = ((getattr(self, "_frame_provenance", None) or {}).get("BTC/USDT") or {}).get("market")
                head = self.last_decisions.get(sym) or {}
                mark = float(marks_f[sym]) if sym in marks_f else None
                spot_book = getattr(self, "spot2", None)
                for b in books:
                    is_main = b["book_id"] == BOOK_MAIN
                    # PIYASA-KESIN KAYIT (CHART ANALYSIS V1 onarimi, bulgu #2): kayit kimligi DEFTERIN piyasasidir.
                    # Kagit defterler (T2/M2) yalniz USDM_PERP; ana bot kaydi cerceve piyasasini tasir ve pozisyon/plan/
                    # gecmis YALNIZ o piyasadan gelir (SPOT kaydi futures defterini TASIMAZ, tersi de). 2e31926 ana
                    # botun SPOT kaydina futures defterinin pozisyonunu yaziyordu.
                    # 2026-09-16 #3: kagit defter icin mumlar USDM_PERP degilse (SPOT ikamesi ya da provenans yok) spot
                    # cerceve futures diye YENIDEN ETIKETLENMEZ ve kayit YAZILMAZ; neden config.json.skipped'a yazilir
                    # (panel 'motor kaydi yok: piyasa uyusmazligi' gosterir). Islem yolu (defter.step/tick) bundan ETKILENMEZ.
                    skey = "%s|%s|%s" % (b["book_id"], sym, tf)
                    if bar_market is None:
                        skipped[skey] = {"status": "NO_PROVENANCE", "bar_market": None, "book_market": ("?" if is_main else "USDM_PERP"),
                                         "reason": prov.get("reason") or "FRAME_PROVENANCE_MISSING", "source": prov.get("source"), "at": iso(now)}
                        continue
                    if not is_main and bar_market != "USDM_PERP":
                        skipped[skey] = {"status": "MARKET_MISMATCH", "bar_market": bar_market, "book_market": "USDM_PERP",
                                         "reason": prov.get("reason") or "", "source": prov.get("source"), "at": iso(now)}
                        continue
                    market_type = bar_market if is_main else "USDM_PERP"
                    pos = None
                    plan = plan_for_market(head, market_type)        # yalniz gecerli plan, panelle AYNI kural
                    if is_main and market_type == "SPOT":
                        spd = (spot_book.positions() if spot_book is not None else {}).get(sym)
                        posd = spot_position_to_dict(sym, spd) if spd else None
                        hist = [h for h in spot_book.history_dicts() if h.get("symbol") == sym][-HISTORY_TAIL:] if spot_book is not None else []
                    else:
                        pos = b["ledger"].positions.get(sym)
                        posd = position_to_dict(pos) if pos is not None else None
                        hist = [h for h in b["ledger"].history_dicts() if h.get("symbol") == sym][-HISTORY_TAIL:]
                    ef = None
                    if pos is not None and b["memory"] is not None:
                        try:
                            row = b["memory"].get(pos.id) or {}
                            ef = row.get("features") or (row.get("entry") or {}).get("features")
                        except Exception:  # noqa: BLE001
                            ef = None
                    snap = build_snapshot(symbol=sym, market_type=market_type, timeframe=tf, tf_ms=step,
                                          book={"book_id": b["book_id"], "name": b["name"], "atr_mult": b["atr_mult"]},
                                          bars=bars, as_of_ms=as_of, daily_rows=daily, btc_daily_rows=btc_rows, gates=gates,
                                          decision=last_dec.get(sym) if is_main else None, plan=plan if is_main else None,
                                          position=posd, history=hist, entry_features=ef, mark_price=mark, cfg=cfgd,
                                          code=code, cfg_hash=chash, mark_source={"kind": "ticker_last", "module": "engine_v3", "function": "_marks"},
                                          # gunluk sembol cercevesi ayni saglayicidan gelir (provenans sembol basina); BTC rejim
                                          # cercevesinin piyasasi ayrica kaydedilir ve kagit defterde USDM_PERP degilse ISARETLENIR
                                          source={"frames": "runner.last_frames", "provenance": prov, "bar_market": bar_market, "daily_market": bar_market,
                                                  "btc_market": btc_market, "btc_market_ok": (btc_market == "USDM_PERP") if not is_main else None})
                    written += int(bool(store.save(snap).get("written")))
            if skipped:
                cfg_doc["skipped"] = skipped
                atomic_write_json(self.cfg.state_path / DIRNAME / "config.json", cfg_doc)
                log.warning("grafik analizi: %d seri icin kayit YAZILMADI (piyasa uyusmazligi/provenans yok): %s", len(skipped),
                            ", ".join("%s=%s/%s" % (k, v.get("status"), v.get("bar_market")) for k, v in list(skipped.items())[:6]))
            if written:
                log.info("grafik analizi: %d yeni analiz ani kaydedildi", written)
        except Exception as exc:  # noqa: BLE001 -- gosterim katmani ana turu ASLA durdurmaz
            log.warning("grafik analizi turu basarisiz (ana tur ETKILENMEZ): %s", exc)

    # ------------------------------------------------------------------ FORMASYON PAPER TRADER V1
    def _pattern_feed(self):
        """MarketFeed: resmi USDM sağlayıcı + panelin okuduğu CSV önbelleği (artımlı indirme, kapanmamış bar düşer)."""
        from .market.feed import MarketFeed
        from .pattern_trader.data import CsvCandleCache
        return MarketFeed([self._futures_provider_factory()], cache_store=CsvCandleCache(self.cfg.cache_path))

    def ensure_pattern_scanner(self) -> dict:
        """Formasyon tarayıcısını kur ve ARKA PLANDA başlat. HEMEN döner — tarama ana turu BEKLETMEZ.
        Kapalıysa/`pattern_book` yoksa hiçbir şey yapmaz ve gerekçe döner."""
        if self.pattern_book is None:
            return {"enabled": False, "reason": "pattern_trader kapalı"}
        if self.pattern_scanner is not None:
            return self.pattern_scanner.status()
        pt = self.cfg.v3.pattern_trader
        from .pattern_trader.data import DataService, PriceService
        from .pattern_trader.scheduler import PatternScanner
        provider = self._futures_provider_factory()
        feed = self._pattern_feed()
        sc = PatternScanner(book=self.pattern_book, data=DataService(feed), price=PriceService(provider, feed),
                            universe_provider=provider, state_path=self.cfg.state_path,
                            min_quote_volume_24h=float(pt.min_quote_volume_24h), max_spread_pct=float(pt.max_spread_pct),
                            max_symbols_per_cycle=int(pt.max_symbols_per_cycle), universe_refresh_minutes=float(pt.universe_refresh_minutes),
                            cycle_seconds=float(pt.scan_seconds), run_id=lambda: str(getattr(self, "run_id", "") or ""))
        self.pattern_scanner = sc
        sc.start()
        log.info("formasyon tarayıcısı başladı: her %.0f sn, tur başına en fazla %d sembol", sc.cycle_seconds, sc.max_symbols_per_cycle)
        return sc.status()

    def _pattern_trader_tour(self, now: datetime) -> None:
        """Tur adımı: tarayıcıyı başlat (arka plan) + ekonomik raporu yaz. Arıza ana turu DURDURMAZ."""
        if self.pattern_book is None:
            return
        try:
            self.ensure_pattern_scanner()
        except Exception as exc:  # noqa: BLE001
            log.warning("formasyon tarayıcısı başlatılamadı (tur sürer): %s", exc)
        try:                                        # panel ilk turdan itibaren guncel defteri gorsun (tarama turu beklemeden)
            self.pattern_book.save(None, now)
        except Exception as exc:  # noqa: BLE001
            log.warning("formasyon defteri ozeti yazilamadi: %s", exc)
        try:
            from .pattern_trader.report import build_report
            summary = read_json(self.cfg.state_path / self.pattern_book.summary_file, default=None) or {}
            scan = read_json(self.cfg.state_path / "pattern_scan.json", default=None) or {}
            rep = build_report(summary, history=self.pattern_book.ledger.history_dicts(), findings=self.pattern_book.findings,
                               plans=self.pattern_book.plans, scan=scan, now=now)
            atomic_write_json(self.cfg.state_path / "pattern_report.json", rep)
        except Exception as exc:  # noqa: BLE001 — rapor arızası defteri/turu ETKİLEMEZ
            log.warning("formasyon raporu yazılamadı: %s", exc)

    def _pattern_exit_check(self) -> None:
        """60 sn çıkış izleyicisi (formasyon defteri): doğrulanmış güncel perp mark ile stop/hedef/likidasyon.
        Tarama iş parçacığından BAĞIMSIZ — kuyruk ne kadar uzun olursa olsun bu yol BEKLEMEZ."""
        if self.pattern_book is None or not self.pattern_book.ledger.positions:
            return
        try:
            if self.pattern_scanner is None:
                self.ensure_pattern_scanner()
            if self.pattern_scanner is not None:
                self.pattern_scanner.exit_check()
        except Exception as exc:  # noqa: BLE001
            log.warning("formasyon defteri exit-monitor basarisiz: %s", exc)

    def _strategy_paper_exit_check(self) -> None:
        """60 sn çıkış izleyicisi: strateji defterinin açık pozisyonlarını canlı fiyatla tick'ler."""
        for book in list(getattr(self, "strategy_books", None) or []):
            if not book.ledger.positions:
                continue
            try:
                # VERI KIMLIGI (2026-09-16): spot ticker `last` DEGIL, dogrulanmis USDS-M perpetual mark; yoksa tick YOK
                # (uydurma gerceklesme yok), bosluk defterde gorunur, izleme surer.
                # ZAMAN SOZLESMESI: her kontrolde canli sagla sorulur (paylasilan 60 sn onbellek); turun bagli fiyati
                # yeniden kullanilmaz; fiyat yasi kontrol anina gore denetlenir (bayat → STALE_FUTURES_PRICE boslugu);
                # bar uclari bu yola EKLENMEZ (fiyat-yalniz tick: giris oncesi/tuketilmis fitil yeni olay uretmez).
                now = utc_now()
                marks, marks_f, gaps = self._paper_marks(list(book.ledger.positions), now=now)
                book.record_gaps(gaps, now)                  # bosluk acilis/kapanis olaylari (durum/gerekce degisince bir kez)
                if marks:
                    book.tick(marks, now=now, bar_advance=False)
                book.save(marks_f, now)
            except Exception as exc:  # noqa: BLE001
                log.warning("strateji kagit defteri exit-monitor basarisiz (%s): %s", book.key, exc)

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
        from .economics_gate import assess_one
        bmap = {b.symbol: b for b in briefs}
        for sym, d in decisions.items():
            if not getattr(d, "is_actionable", False):
                continue
            plan = getattr(d, "active_plan", None)
            if plan is None or not getattr(plan, "valid", False):
                continue
            # EKONOMI KAPISI — TEK KAYNAK `economics_gate.assess_one`. Replay motoru da AYNI
            # fonksiyonu cagirir; mantik ikinci kez KOPYALANMAZ (bkz. modul basligi).
            b = bmap.get(sym)
            _listed = None
            _fp = float(getattr(self.cfg.v3.universe, "futures_only_penalty_r", 0.0) or 0.0)
            if _fp > 0 and str(getattr(plan, "market_type", "")) != "spot":
                _listed = self.spot_listing.is_listed(sym)
            a, _unknown = assess_one(
                symbol=sym, direction=d.direction, setup=plan.entry_type or "-", regime=d.regime,
                soft_flags=list(getattr(d, "soft_flags", []) or []) + list(getattr(plan, "soft_flags", []) or []),
                redteam_warnings=(getattr(b, "dont_list", None) or []) if b is not None else [],
                stop_pct=plan.stop_pct, expected_cost_pct=plan.expected_cost_pct,
                expected_r=plan.expected_r,
                is_spot=(str(getattr(plan, "market_type", "")) == "spot"),
                learner=self.learner2, p_win_override=d.p_win,
                short_penalty_r=float(getattr(self.cfg.v3.futures_v3, "short_penalty_r", 0.0) or 0.0),
                futures_only_penalty_r=_fp, spot_listed=_listed,
                risk_per_trade_pct=self.profile.risk_per_trade_pct)
            for _c in _unknown:
                log.error("kayıtsız kapı kodu %s (%s) — aday fail-closed reddedildi", _c, sym)
            d.opportunity = a.to_dict()
            # ÖĞRENME KARARI DEĞİŞTİRDİ Mİ? — PAPER_BOUNDED'ta etkin p_win baseline'dan
            # farklıysa AYNI ekonomi kapısı baseline ile de değerlendirilir; `tradeable`
            # sonucu farklıysa açıkça işaretlenir. SHADOW'da p_win zaten baseline'dır.
            try:
                inf = next((i for i in reversed(getattr(self, "_influence_log", []) or [])
                            if i.get("symbol") == sym), None)
                changed = False
                if (inf and inf.get("applied") and inf.get("baseline") is not None
                        and float(inf.get("effective") or 0) != float(inf["baseline"])):
                    # KARSI-OLGU: AYNI kapi, yalniz p_win baseline. Kapinin kendisi tek
                    # kaynaktan gelir; ceza/kod listesi burada YENIDEN KURULMAZ.
                    base_a, _ = assess_one(
                        symbol=sym, direction=d.direction, setup=plan.entry_type or "-",
                        regime=d.regime,
                        soft_flags=list(getattr(d, "soft_flags", []) or []) + list(getattr(plan, "soft_flags", []) or []),
                        redteam_warnings=(getattr(b, "dont_list", None) or []) if b is not None else [],
                        stop_pct=plan.stop_pct, expected_cost_pct=plan.expected_cost_pct,
                        expected_r=plan.expected_r,
                        is_spot=(str(getattr(plan, "market_type", "")) == "spot"),
                        learner=self.learner2,
                        p_win_override=float(inf["baseline"]),
                        short_penalty_r=float(getattr(self.cfg.v3.futures_v3, "short_penalty_r", 0.0) or 0.0),
                        futures_only_penalty_r=_fp, spot_listed=_listed,
                        risk_per_trade_pct=self.profile.risk_per_trade_pct)
                    changed = bool(a.tradeable) != bool(base_a.tradeable)
                d.opportunity["decision_changed_by_learning"] = changed
                if inf is not None:
                    inf["decision_changed_by_learning"] = changed
            except Exception:  # noqa: BLE001 — işaretleme arızası ekonomi kararını bozamaz
                d.opportunity["decision_changed_by_learning"] = None

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
            "tiers": dict(getattr(self, "_tier_counts", None) or {}),
            "coverage": {"evaluated": int(getattr(self, "_evaluated_last_tour", 0) or 0),
                         "journaled": int(getattr(self, "_journaled_last_tour", 0) or 0)},
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
                                aggregate_base=(store.aggregates() if store is not None else None),
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
            # TAM-GECMIS TOPLAM KANITI: exemplar penceresi disinda kalan arsiv sonuclari
            # sinirli toplam hucrelerinden gelir; sayilan exemplar'lar DUSULUR (cift sayim yok).
            agg_view = None
            book = getattr(prepared, "aggregate_book", None)
            if book is not None:
                try:
                    from .learn.experience import feature_profile as _fp
                    agg_view = book.query(symbol=b.symbol,
                                          direction=(d.direction if d else None),
                                          regime=(d.regime if d else None),
                                          setup=(b.plan.entry_type if b.plan else None),
                                          profile=_fp(query),
                                          as_of_ms=as_of_ms, subtract=pool)
                except Exception:  # noqa: BLE001 — toplam kanit arizasi exemplar yolunu bozamaz
                    agg_view = None
            # ÇİFT SAYIM KORUMASI: hiyerarşik prior aynı kapanışları zaten kullandı; similarity
            # yalnız RESIDUAL payı uygular. `prior_leaf_n` = bu sembol/setup yaprağının örnek sayısı.
            leaf_n = self._prior_leaf_n(b.symbol, b.plan.entry_type if b.plan else None)
            adj = weighted_adjustment(pool, baseline=baseline_p_win, cfg=cfg,
                                      prior_leaf_n=leaf_n, aggregate=agg_view)
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
                    "exemplar_weight": adj.get("exemplar_weight"),
                    "aggregate_weight": adj.get("aggregate_weight"),
                    "aggregate_level": (agg_view or {}).get("level"),
                    "aggregate_n": (agg_view or {}).get("n"),
                    "aggregate_mean_r": (agg_view or {}).get("mean_r"),
                    "aggregate_months": (agg_view or {}).get("months"),
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
            from .learn.decision_journal import (build_decision_record, classify_outcome,
                                                 decision_id_for)
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
                    decision_ts=iso(now), entry=e,
                    snapshot=self._pred_snapshots.get(sym), decision=d,
                    outcome_kind=kind, trade_id=(e or {}).get("trade_id"),
                    # DENETIM KIMLIGI: `cfg.code_sha` uretimde HIC set edilmiyor; `code_sha()`
                    # git HEAD'den bir kez turetir ve onbellekler. Bu cagri yeri hala eski
                    # `getattr` yolunu kullaniyordu, bu yuzden karar kaydinda alan BOS kaliyordu
                    # (olculdu: 0/30). Hangi kod ve hangi config karari uretti sorusu, kayittan
                    # cevaplanamiyorsa denetim yapilamaz. Git yoksa yine `None` — UYDURULMAZ.
                    code_sha=self.code_sha(), config_hash=self.config_hash(),
                    market_type=(e or {}).get("market_type") or ("SPOT" if verdict == "SPOT_LONG"
                                                                 else "USDM_PERP" if is_act else None),
                    policy_id=(e or {}).get("research_policy_id"))
                rec.update({"outcome_stage": stage, "outcome_reason": reason,
                            "is_actionable": is_act, "has_valid_plan": has_plan,
                            "entered_ranking": e is not None,
                            "shadow_recorded": sym in shadowed,
                            "stage_history": [k for k, val in (self._funnel or {}).items() if val]
                            if getattr(self, "_funnel", None) else None})
                # VERI KIMLIGI: karar cercevesi hangi piyasadan geldi ve o sembolde YENI
                # girise guvenilebilir miydi. Sonradan "hangi mumla karar verdik" sorusu
                # kayittan cevaplanabilsin diye kalici.
                _prov = (getattr(self, "_frame_provenance", None) or {}).get(sym)
                if _prov:
                    rec["frame_provenance"] = dict(_prov)
                # HABER/OLAY BAGLAMI — YALNIZ GOZLEM (`usable=False`). Hicbir kapiya girmez;
                # kayitta durmasi "karar aninda ne biliyorduk" sorusunu cevaplamak icindir.
                try:
                    _nw = self.cfg.v3.news
                    if _nw.enabled:
                        from .market.news import NewsStore, context_for_decision
                        rec["news_context"] = context_for_decision(
                            NewsStore(self.cfg.state_path / "news.jsonl"), sym, now_iso=iso(now),
                            window_hours=float(_nw.context_window_hours),
                            limit=int(_nw.max_items_in_decision))
                except Exception:  # noqa: BLE001 — baglam arizasi kaydi engellemez
                    pass
                # AÇIKLANABİLİRLİK: şampiyon model hazırsa aile bazlı logit katkıları
                # (top± feature). Model hazır değilse alan YOK — uydurma yok.
                try:
                    from .learn.feature_registry import feature_contributions
                    model, _mid, _params = self.learner2._champion_model()
                    snap_v = self._pred_snapshots.get(sym)
                    vals = getattr(snap_v, "values", None) if snap_v is not None else None
                    if model is not None and isinstance(vals, dict) and vals:
                        vec = [float(vals.get(n, 0.0) or 0.0)
                               for n in (getattr(model, "feature_names", None) or [])]
                        contrib = feature_contributions(model, vec)
                        if contrib:
                            rec["feature_contributions"] = contrib
                except Exception:  # noqa: BLE001 — açıklama arızası kaydı engellemez
                    pass
                if sym in infl:
                    rec["learning_influence"] = {k: infl[sym].get(k) for k in
                                                 ("mode", "n_experience", "top_similarity",
                                                  "fraction", "baseline", "learned", "applied",
                                                  "blockers", "effective",
                                                  "exemplar_weight", "aggregate_weight",
                                                  "aggregate_level", "aggregate_n",
                                                  "aggregate_mean_r", "aggregate_months",
                                                  "decision_changed_by_learning")}
                try:
                    from .learn.decision_journal import why_summary_tr
                    rec["why_summary_tr"] = why_summary_tr(rec)
                except Exception:  # noqa: BLE001
                    pass
                if j.append_decision(rec):
                    n += 1
            # --- TIER-A STAGE-1: derin analize girmeyen evren sembolleri de KAYIT ALIR ---
            # Kayıt küçüktür (ham mum/feature yok): hangi aşamada, neden elendi, skoru neydi.
            # Payda = Tier A evreni; "değerlendirilen her aday" sözleşmesi burada tamamlanır.
            uni = getattr(self, "_eval_universe", None) or {}
            n_screen = 0
            deep_syms = set(decisions)
            for row in (uni.get("symbols") or []):
                sym = str(row.get("symbol") or "")
                if not sym or sym in deep_syms:
                    continue
                srec = {"schema_version": "decision_journal_v1", "kind": "decision",
                        "decision_id": decision_id_for(self.run_id,
                                                       getattr(self, "_journal_cycle", 0),
                                                       sym, "SCREEN"),
                        "run_id": self.run_id,
                        "cycle_id": str(getattr(self, "_journal_cycle", 0)),
                        "decision_ts": iso(now), "symbol": sym, "direction": None,
                        "market_type": "USDM_PERP", "tier": "A",
                        "outcome_kind": "SCREENED_OUT", "outcome_stage": "tier_a_screen",
                        "outcome_reason": row.get("screen_reason") or "NOT_DEEP_ANALYZED",
                        "scan_score": row.get("scan_score"), "scan_rank": row.get("rank"),
                        "vol24_usdt": row.get("vol24_usdt"), "atr_pct": row.get("atr_pct"),
                        "universe_artifact_sha": uni.get("artifact_sha"),
                        "is_actionable": None, "has_valid_plan": None,
                        "entered_ranking": False, "shadow_recorded": False,
                        "code_sha": self.code_sha(), "config_hash": self.config_hash()}
                try:
                    from .learn.decision_journal import why_summary_tr
                    srec["why_summary_tr"] = why_summary_tr(srec)
                except Exception:  # noqa: BLE001
                    pass
                if j.append_decision(srec):
                    n_screen += 1
            self._journaled_last_tour = n + n_screen
            n_tier_a_universe = len(uni.get("symbols") or [])
            self._tier_counts = {
                "tier_a_universe": n_tier_a_universe or len(decisions),
                "tier_b_deep": len(decisions),
                "tier_c_ranked": len({str(e.get("symbol") or "") for e in risk_log
                                      if e.get("symbol")}),
                "screened_journaled": n_screen,
                "universe_artifact_sha": uni.get("artifact_sha"),
                "universe_as_of": uni.get("as_of")}
            self._evaluated_last_tour = len(decisions) + n_screen
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
            # KARAR KİMLİĞİ provenance'tan gelir. Eskiden bu alan HİÇ geçilmiyordu ve üretimdeki
            # bütün outcome bağlantı kayıtları `decision_id: null` ile yazılıyordu (2026-09-02'de
            # 6/6 ölçüldü) — yani "bağlantı kaydı" aslında hiçbir karara bağlanmıyordu.
            did = None
            store = getattr(self, "provenance", None)
            if store is not None:
                try:
                    did = (store.get(tid) or {}).get("entry_decision_id")
                except Exception:  # noqa: BLE001
                    did = None
            j.append_outcome(build_outcome_link(trade_id=tid, outcome=rec_legacy,
                                                decision_id=did, lesson=lesson))
        except Exception as exc:  # noqa: BLE001
            self._journal_errors += 1
            log.warning("outcome bağlantısı yazılamadı: %s", exc)

    # ------------------------------------------------- kapanış zinciri bütünlüğü (V3)
    def code_sha(self) -> str | None:
        """Çalışan kod sürümü. `cfg.code_sha` yoksa git HEAD'den BİR KEZ türetilir.

        Üretimde bu alan boştu (2026-09-02: karar günlüğünde 0/20000 dolu) çünkü kimse
        `cfg.code_sha` set etmiyordu. Git yoksa/başarısızsa `None` döner ve UYDURULMAZ.
        """
        cached = getattr(self, "_code_sha_cache", ...)
        if cached is not ...:
            return cached
        sha = getattr(self.cfg, "code_sha", None)
        if not sha:
            try:
                import subprocess
                r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.cfg.project_root),
                                   capture_output=True, text=True, timeout=5)
                sha = r.stdout.strip() or None if r.returncode == 0 else None
            except Exception:  # noqa: BLE001 — sürüm bilgisi eksikliği turu DURDURMAZ
                sha = None
        self._code_sha_cache = sha
        return sha

    def config_hash(self) -> str | None:
        """Etkin v3 yapılandırmasının deterministik özeti (quant manifest ile AYNI yöntem)."""
        cached = getattr(self, "_config_hash_cache", ...)
        if cached is not ...:
            return cached
        h = None
        try:
            from dataclasses import asdict
            from .core import payload_hash
            h = payload_hash(asdict(self.cfg.v3)) if self.cfg.v3 is not None else None
        except Exception:  # noqa: BLE001
            h = None
        self._config_hash_cache = h
        return h

    def _record_entry_provenance(self, *, trade_id, symbol, direction, decision, plan,
                                 risk_decision, brief, notional, leverage, now) -> None:
        """Açılış anında karar kimliğini kalıcı yapar. Arıza AÇILIŞI GEÇERSİZ KILMAZ."""
        store = getattr(self, "provenance", None)
        if store is None or not trade_id:
            return
        try:
            from .learn.decision_journal import decision_id_for
            from .learn.provenance import build_entry_provenance
            snap = (getattr(self, "_pred_snapshots", None) or {}).get(symbol)
            spec = None
            try:
                spec = {r.agent_name: float(r.bias) for r in (decision.specialist_reports or [])
                        if getattr(r, "usable", True)}
            except Exception:  # noqa: BLE001
                spec = None
            opp = getattr(decision, "opportunity", None) or {}
            rd = risk_decision.to_dict() if hasattr(risk_decision, "to_dict") else {}
            store.record(build_entry_provenance(
                trade_id=trade_id, symbol=symbol, direction=direction,
                decision_id=decision_id_for(self.run_id, getattr(self, "_journal_cycle", self._tour_no),
                                            symbol, direction),
                journal_id=getattr(snap, "snapshot_id", None),
                run_id=self.run_id, cycle_id=getattr(self, "_journal_cycle", self._tour_no),
                code_sha=self.code_sha(), config_hash=self.config_hash(),
                policy_id=(decision.model_versions or {}).get("policy_id") or "champion",
                p_win=getattr(brief, "p_win", None),
                expected_r=getattr(decision, "expected_r", None),
                expected_net_return=opp.get("conservative_net_edge_r"),
                features=(snap.vector() if snap is not None else None),
                specialist_scores=spec, regime=getattr(decision, "regime", None),
                risk_decision={k: v for k, v in rd.items() if isinstance(v, (int, float))},
                stop=getattr(plan, "stop", None), targets=getattr(plan, "targets", None),
                size_usdt=notional, leverage=leverage, opened_at=iso(now)))
        except Exception as exc:  # noqa: BLE001 — provenance arızası pozisyonu ETKİLEMEZ
            log.warning("giriş provenance yazılamadı (%s): %s", trade_id, exc)

    def _complete_close_chain(self) -> dict:
        """Eksik kalmış outcome/ders adımlarını tamamlar (crash penceresi onarımı).

        Normal işleyişte hiçbir şey yapmaz: plan boştur ve maliyeti birkaç küme
        karşılaştırmasıdır. Yalnız `ledger2.save()` ile öğrenme arasında süreç öldüyse iş yapar.
        """
        idx = getattr(self, "learned_index", None)
        if idx is None:
            return {"ran": False, "reason": "NO_INDEX"}
        try:
            from .learn.reconcile import complete_missing_chain
            res = complete_missing_chain(
                history=self.ledger2.history, memory=self.memory, learner=self.learner,
                index=idx, provenance_store=getattr(self, "provenance", None),
                journal_outcome=self._journal_outcome)
            if res.get("lessons_added") or res.get("outcomes_added"):
                log.warning("kapanış zinciri onarıldı: +%s outcome, +%s ders (%s)",
                            res.get("outcomes_added"), res.get("lessons_added"),
                            [t["trade_id"] for t in (res.get("trades") or [])][:10])
            return res
        except Exception as exc:  # noqa: BLE001 — onarım arızası turu DURDURMAZ
            log.warning("kapanış zinciri onarımı atlandı: %s", exc)
            return {"ran": False, "error": str(exc)[:200]}

    def _record_position_path(self, marks: dict, decisions: dict | None, now,
                              *, tick_kind: str) -> dict:
        """Açık pozisyonların fiyat yolunu kaydeder ve politika niyetlerini değerlendirir.

        SALT GÖZLEM: hiçbir emir üretilmez, defter değişmez, stop/TP'ye dokunulmaz. Yürütücü
        `SHADOW` olduğu için bütün niyetler `applied=False` + `blocker` ile döner.

        `tick_kind` bilinçli olarak taşınır: `exit_check` yalnız son fiyatı bilir (bar uçları
        YOK), tur ise 1h bar uçlarını da verir. Bar uçları olmadan hesaplanan MFE gerçek en iyi
        noktayı kaçırabilir; değerlendirme bunu bilmek zorundadır.
        """
        store = getattr(self, "path_store", None)
        if store is None or not self.ledger2.positions:
            return {}
        try:
            from .learn.exit_policy import HOLD, evaluate_all
            from .learn.position_path import build_snapshot
            # SNAPSHOT ZAMANI = KAYIT ANI, tur başlangıcı DEĞİL. `tour()` içindeki `now` turun
            # EN BAŞINDA alınır; `_marks()` ise turun ortasında çalışır ve tick'leri o anki
            # saatle damgalar. Tur ~600 sn sürdüğü için tur-başı `now`a göre her mark
            # GELECEKTEN gelmiş görünüyor ve `FUTURE_TIMESTAMP` ile reddediliyordu — ilk
            # üretim turunda 9 pozisyonun 9'u da sessizce atlandı (dosya hiç oluşmadı).
            snap_now = utc_now()
            decs = decisions or {}
            written = 0
            intents: list[dict] = []
            rejected: dict[str, int] = {}
            considered = 0
            for sym, pos in sorted(self.ledger2.positions.items()):
                tick = marks.get(sym)
                if tick is None:
                    continue
                considered += 1
                mark = float(tick.ref)
                rec, rej = build_snapshot(
                    position=pos, mark=mark, now=snap_now, tick_kind=tick_kind,
                    decision=decs.get(sym), run_id=self.run_id,
                    code_sha=self.code_sha(), config_hash=self.config_hash(),
                    mark_ts=getattr(tick, "ts", None) or None)
                if rec is None:
                    rejected[rej or "UNKNOWN"] = rejected.get(rej or "UNKNOWN", 0) + 1
                    continue
                if store.append(rec):
                    written += 1
                # Politika niyetleri HER snapshot için değerlendirilir (yazılmasa bile),
                # böylece panel güncel kalır; fakat hiçbiri uygulanmaz.
                for d in evaluate_all(rec, self.exit_policy_cfg,
                                      reduces_done=0).values():
                    if d["action"] != HOLD:
                        intents.append(d)
            ex = getattr(self, "exit_executor", None)
            res = {}
            if ex is not None and intents:
                res = ex.execute_many(
                    intents, mode_value=self.mode_state.mode.value,
                    live_order_path=self.mode_state.is_live_order_path_enabled(),
                    killswitch_state=self.killswitch.state,
                    position_open=True, mark_stale=False, actions_this_tour=0)
            # SESSİZ BAŞARISIZLIK YASAK: hiçbir snapshot yazılmadıysa ve sebebi REDDEDİLME ise
            # bu bir gözlem boşluğudur ve loga çıkar. (Önemsiz ara adım atlaması red DEĞİLDİR.)
            if considered and not written and rejected:
                log.warning("pozisyon yolu YAZILMADI — %d pozisyonun hepsi reddedildi: %s",
                            considered, rejected)
            out = {"schema_version": "exit_path_cycle_v1", "at": iso(snap_now),
                   "tick_kind": tick_kind, "positions_considered": considered,
                   "snapshots_written": written,
                   "rejected": rejected, "n_intents": len(intents),
                   "applied": int(res.get("applied", 0)), "execution": res or None,
                   "store": store.stats()}
            self._exit_cycle = out
            return out
        except Exception as exc:  # noqa: BLE001 — gözlem arızası turu DURDURAMAZ
            log.warning("pozisyon yolu kaydedilemedi: %s", exc)
            return {}

    def _write_exit_eval(self, now) -> dict:
        """Kapanmış işlemler için champion/challenger karşı-olgusal raporunu yazar.

        Tam yol olmayan işlemler `NO_COMPLETE_PATH` ile geçilir; sahte karşılaştırma YAPILMAZ.
        """
        store = getattr(self, "path_store", None)
        if store is None or self.exit_policy_cfg is None:
            return {}
        try:
            from .learn.close_chain import canonical_closes
            from .learn.exit_eval import aggregate, evaluate_trade
            _ex = self.cfg.v3.exit_policy
            paths = store.paths_by_trade()
            evals = [evaluate_trade(trade_id=c["trade_id"], path=paths.get(c["trade_id"]) or [],
                                    close=c, cfg=self.exit_policy_cfg,
                                    fee_rate=_ex.eval_fee_rate,
                                    slip_rate=_ex.eval_slippage_rate)
                     for c in canonical_closes(self.ledger2.history)]
            doc = aggregate(evals, cfg=self.exit_policy_cfg, now=now)
            doc["run_id"] = self.run_id
            doc["exit_action_mode"] = getattr(self.exit_executor, "mode", "SHADOW")
            doc["applied_total"] = 0
            # YOL KAYDININ SAĞLIĞI raporun İÇİNDE görünür: "0 yol-tam kapanış" ile "yol hiç
            # yazılmıyor" iki AYRI durumdur ve panelde karışmamalıdır.
            doc["path_cycle"] = {k: v for k, v in (getattr(self, "_exit_cycle", None) or {}).items()
                                 if k in ("at", "tick_kind", "positions_considered",
                                          "snapshots_written", "rejected", "n_intents", "applied")}
            doc["path_store"] = (store.stats() if store is not None else None)
            doc["trades"] = [{k: e.get(k) for k in
                              ("trade_id", "symbol", "side", "closed_at", "exit_reason",
                               "actual_r", "status", "path")}
                             | {"results": {p: {kk: r.get(kk) for kk in
                                                ("net_r", "gross_r", "exit_cost_r", "n_actions",
                                                 "exit_reason", "delta_vs_champion_r",
                                                 "missed_gain_r", "avoided_loss_r", "capture_ratio")}
                                            for p, r in (e.get("results") or {}).items()}}
                             for e in evals]
            atomic_write_json(self.cfg.state_path / "exit_eval.json", doc)
            return doc
        except Exception as exc:  # noqa: BLE001
            log.warning("çıkış değerlendirmesi yazılamadı: %s", exc)
            return {}

    # ------------------------------------------------------------------ giriş seçiciliği (SHADOW)
    def _entry_capture(self, sym, decision, plan, perm, state, entry_log, market, now) -> None:
        """Sıralamaya giren adayın KARAR ANI girdilerini tampona alır.

        Hiçbir şey yazmaz ve hiçbir karar vermez: yalnız o anda görülebilen nesnelere referans
        tutar. Sonuç (R, kazandı/kaybetti) bu tampona GİREMEZ; snapshot tur sonunda, işlem
        kapanmadan çok önce yazılır.
        """
        if getattr(self, "entry_snapshot_store", None) is None:
            return
        try:
            buf = getattr(self, "_entry_pending", None)
            if buf is None or len(buf) >= int(self.entry_snapshot_store.max_per_cycle):
                return
            spec = None
            try:
                spec = {r.agent_name: float(r.bias)
                        for r in (decision.specialist_reports or []) if getattr(r, "usable", True)}
            except Exception:  # noqa: BLE001 — kanıt paketi eksikse snapshot yine yazılır
                spec = None
            dirn = str(getattr(decision, "direction", "") or "")
            # Portföy ısısı KARAR ANINDAKİ yetkili durumdan okunur (her fill sonrası yenilenir).
            # `open_positions` SPOT holdinglerini de içerir (spot yönü daima LONG) ve
            # `total_open_risk_usdt` stopsuz spotu TAM notional ile toplar: bunlar BİRLEŞİK
            # portföy TANI alanlarıdır (panel: diagnostic_ratio_not_enforced). Kabul kapısının
            # GERÇEK kovası futures stop riskidir; o AYRICA ve aynı anda dondurulur (aşağıda).
            open_all = list(getattr(state, "open_positions", None) or [])
            same_dir = sum(1 for p in open_all
                           if str(getattr(p, "side", "")).upper().endswith(dirn.upper()))
            fut_ctx = self._futures_bucket_context(open_all, dirn) if state is not None else {}
            ctx = dict(perm or {}) | {
                "open_positions": len(open_all),
                "total_open_risk_usdt": float(getattr(state, "total_open_risk_usdt", 0.0) or 0.0),
                "same_direction_open": same_dir,
                # FUTURES KOVASI — kabul kapısının kovası (`risk/engine.py` TOTAL_OPEN_RISK ile
                # AYNI kapsam): yalnız futures pozisyonları, stopsuz spot notional HARİÇ, aday
                # HARİÇ (giriş öncesi). Karar anında ölçülür ve buffer'da DONAR.
                "futures_stop_risk_usdt": fut_ctx.get("futures_stop_risk_usdt"),
                "same_direction_open_futures": fut_ctx.get("same_direction_open_futures"),
                # KARAR ANI risk bütçesi: şampiyonun kendi türetmesiyle AYNI formül. Snapshot'a
                # ölçülmüş alan olarak girer; E ailesi ısı oranını sonradan "o anki equity" ile
                # değil, bu donmuş değerle hesaplar. Ölçülemezse `None` (MISSING), sıfır değil.
                "risk_budget_usdt": self._decision_time_risk_budget(state),
            }
            # KARAR ANI ÇERÇEVELERİ: motorun ZATEN çektiği, yalnız KAPANMIŞ barları taşıyan
            # kareler (`drop_unclosed_last_bar` veri hattında uygulanır). Yeni API çağrısı YOK.
            frames = (getattr(self.runner, "last_frames", None) or {}).get(sym) or {}
            buf.append({"symbol": sym, "direction": dirn, "decision": decision, "plan": plan,
                        "chief": ctx, "entry_log": entry_log, "market": market,
                        "rank": len(buf), "specialists": spec, "features": None,
                        "daily_frame": frames.get("1d"), "intraweek_frame": frames.get("4h"),
                        # H (D→H1) için saatlik kare: motorun ZATEN çektiği nesneye referans.
                        # YENİ SAĞLAYICI İSTEĞİ YOK; kare yoksa H `ABSTAIN` üretir.
                        "hourly_frame": frames.get("1h"),
                        "ts": now})
        except Exception as exc:  # noqa: BLE001 — gözlem arızası girişi ETKİLEMEZ
            log.warning("giriş adayı yakalanamadı (%s): %s", sym, exc)

    @staticmethod
    def _futures_bucket_context(open_positions, direction) -> dict:
        """Giriş ÖNCESİ futures kovası: `risk/state.PortfolioState.futures_stop_risk_usdt` ile
        AYNI tanım (market_type != SPOT olan pozisyonların `risk_usdt` toplamı) ve yalnız
        futures pozisyonlarının yön sayımı.

        SPOT holdingleri (yönü daima LONG, stopsuzsa TAM notional risk sayılır) bu kovaya
        GİRMEZ; mevcut aday da girmez (liste fill'den ÖNCEKİ durumdur). Pozisyon yoksa
        ölçülmüş SIFIR döner; liste okunamazsa `None` (MISSING) — sıfır uydurulmaz.
        """
        try:
            rows = list(open_positions or [])
        except TypeError:
            return {"futures_stop_risk_usdt": None, "same_direction_open_futures": None}
        d = str(direction or "").upper()
        fut = [p for p in rows
               if str(getattr(p, "market_type", "USDM_PERP") or "USDM_PERP").upper() != "SPOT"]
        risk = 0.0
        for p in fut:
            v = _f_num(getattr(p, "risk_usdt", None))
            if v is None:
                return {"futures_stop_risk_usdt": None,      # bir pozisyonun riski ölçülemedi
                        "same_direction_open_futures": sum(
                            1 for q in fut if str(getattr(q, "side", "")).upper().endswith(d))}
            risk += v
        return {"futures_stop_risk_usdt": risk,
                "same_direction_open_futures": sum(
                    1 for q in fut if str(getattr(q, "side", "")).upper().endswith(d))}

    def _decision_time_risk_budget(self, state) -> float | None:
        """Karar anı toplam açık risk bütçesi (USDT) — `_size_plan` ile AYNI türetme.

        `equity × max_total_open_risk_pct / 100` (profil `size_on_live_equity=false` derse
        `starting_equity`). Salt okunur; hiçbir kanonik nesneyi değiştirmez. Ölçülemezse
        `None` döner — sıfır DEĞİL.
        """
        try:
            prof = getattr(self, "profile", None)
            if prof is None or state is None:
                return None
            live = bool(getattr(prof, "size_on_live_equity", True))
            eq = _f_num(getattr(state, "equity", None) if live
                        else getattr(state, "starting_equity", None))
            pct = _f_num(getattr(prof, "max_total_open_risk_pct", None))
            if eq is None or pct is None or eq <= 0 or pct <= 0:
                return None
            return eq * pct / 100.0
        except Exception:  # noqa: BLE001 — bütçe ölçülemezse snapshot alanı MISSING kalır
            return None

    def _entry_attach_features(self, sym, feats: dict) -> None:
        """Tetik sonrası hesaplanan özellik vektörünü adaya bağlar (hâlâ karar anı verisi).

        Karar anı `FeatureSnapshotV3`i de eklenir çünkü likidite alanları (`spread_pct`,
        `est_slippage_pct`, `depth_ratio`, `liquidity_ok`) YALNIZ orada ölçülür — karar
        günlüğünde 0/49 boş, `trade_memory`de 23/29 dolu olmasının nedeni budur (2026-09-02
        üretim ölçümü). Bu alanlar olmadan D ailesi üretimde HİÇ karar veremez.

        `snapshot.vector()` KULLANILMAZ: o, eksik alanı `0.0` ile doldurur ve "ölçülmedi" ile
        "ölçüldü ve sıfır" ayrımını yok ederdi. Yalnız `values` içindeki, `missing` listesinde
        BULUNMAYAN alanlar alınır; gerçekten eksik olan alan snapshot'ta `MISSING` kalır.
        """
        try:
            snap_v3 = (getattr(self, "_pred_snapshots", None) or {}).get(sym)
            measured: dict = {}
            if snap_v3 is not None:
                miss = set(getattr(snap_v3, "missing", None) or ())
                measured = {k: v for k, v in (getattr(snap_v3, "values", None) or {}).items()
                            if k not in miss}
            for rec in reversed(getattr(self, "_entry_pending", None) or []):
                if rec.get("symbol") == sym and rec.get("features") is None:
                    # `None` bir ÖLÇÜM DEĞİLDİR: eksik bir alanın ölçülmüş bir alanı
                    # ezmesine izin verilmez.
                    rec["features"] = measured | {k: v for k, v in (feats or {}).items()
                                                  if v is not None}
                    return
        except Exception:  # noqa: BLE001
            pass

    def _entry_flush(self, now) -> dict:
        """Tampondaki adaylar için append-only snapshot yazar. Arıza turu DURDURMAZ.

        Baseline kararı (`accepted` / `reject_reason`) risk günlüğünden okunur: bu, motorun
        GERÇEKTEN verdiği karardır ve challenger'ın karşılaştırma tabanıdır.
        """
        store = getattr(self, "entry_snapshot_store", None)
        buf = getattr(self, "_entry_pending", None) or []
        if store is None or not buf:
            self._entry_pending = []
            return {}
        written = 0
        # --- BAĞ YAZIMI GÖZLENEBİLİRLİĞİ (1A) ------------------------------------------
        # `link_trade` dönüşü ARTIK YUTULMUYOR. "İşlem açılmadı" ile "bağ yazılamadı" iki
        # AYRI olaydır ve aynı görünemez; bir bağ arızası turu DURDURMAZ ama SESSİZ KALMAZ.
        link_ctr = {"link_attempted": 0, "link_written": 0, "link_failed": 0,
                    "link_duplicate": 0, "link_not_needed": 0, "link_skipped_no_snapshot": 0}
        link_reasons: list[dict] = []
        try:
            from .learn.entry_snapshot import build_entry_snapshot
            try:
                existing_links = set(store.trade_links().keys())
            except Exception:  # noqa: BLE001 — indeks okunamazsa yinelenen tespiti kapanır
                existing_links = set()
            for rec in buf:
                el = rec.get("entry_log") or {}
                snap = build_entry_snapshot(
                    run_id=self.run_id,
                    cycle_id=getattr(self, "_journal_cycle", self._tour_no),
                    symbol=rec.get("symbol"), direction=rec.get("direction"),
                    decision=rec.get("decision"), plan=rec.get("plan"),
                    opportunity=(getattr(rec.get("decision"), "opportunity", None) or {}),
                    chief_permission=rec.get("chief"),
                    risk_decision={"allowed": el.get("risk_allowed"),
                                   "reasons": el.get("risk_reasons") or []},
                    baseline_rank=rec.get("rank"),
                    baseline_accepted=bool(el.get("trade_id")),
                    baseline_reject_reason=el.get("block_code"),
                    features=rec.get("features"), specialist_scores=rec.get("specialists"),
                    code_sha=self.code_sha(), config_hash=self.config_hash(),
                    policy_version=getattr(self.entry_cfg, "policy_version", None),
                    now=rec.get("ts") or now)
                snap["market_type"] = snap.get("market_type") or rec.get("market")
                self._attach_weekly_context(snap, rec)
                self._attach_mtf_context(snap, rec)
                appended = store.append(snap)
                tid = str(el.get("trade_id") or "")
                if not tid:
                    link_ctr["link_not_needed"] += 1        # işlem AÇILMADI — arıza DEĞİL
                elif not appended:
                    # Snapshot yazılmadı (yineleme ya da hata): mevcut sözleşme gereği bağ da
                    # yazılmaz. Bu ÜÇÜNCÜ bir durumdur ve artık görünürdür.
                    link_ctr["link_skipped_no_snapshot"] += 1
                    link_reasons.append({"trade_id": tid, "candidate_id": snap["candidate_id"],
                                         "code": "LINK_SKIPPED_SNAPSHOT_NOT_WRITTEN"})
                    log.warning("giriş bağı ATLANDI (snapshot yazılmadı): trade=%s candidate=%s",
                                tid, snap["candidate_id"])
                elif tid in existing_links:
                    link_ctr["link_duplicate"] += 1
                    link_reasons.append({"trade_id": tid, "candidate_id": snap["candidate_id"],
                                         "code": "LINK_ALREADY_PRESENT"})
                else:
                    # AÇILAN pozisyon adaya AYRI bir satırla bağlanır; snapshot değişmez.
                    link_ctr["link_attempted"] += 1
                    ok = False
                    try:
                        ok = bool(store.link_trade(snap["candidate_id"], tid))
                    except Exception as lexc:  # noqa: BLE001 — bağ arızası turu DURDURMAZ
                        log.warning("giriş bağı yazılamadı (istisna) trade=%s: %s", tid, lexc)
                    if ok:
                        link_ctr["link_written"] += 1
                        existing_links.add(tid)
                        link_reasons.append({"trade_id": tid,
                                             "candidate_id": snap["candidate_id"],
                                             "code": "LINK_OK"})
                    else:
                        link_ctr["link_failed"] += 1
                        link_reasons.append({"trade_id": tid,
                                             "candidate_id": snap["candidate_id"],
                                             "code": "LINK_WRITE_FAILED"})
                        # Eksik bağ UYDURULMAZ; yalnız açıkça raporlanır.
                        log.warning("GİRİŞ BAĞI YAZILAMADI: trade=%s candidate=%s — kanıt "
                                    "zinciri bu işlem için EKSİK kalacak", tid,
                                    snap["candidate_id"])
                written += 1
            rot = store.rotate()          # ARŞİV-ÖNCE; arşiv düşerse budama YOK
            self._entry_cycle = {"at": iso(now), "candidates": len(buf), "written": written,
                                 "appended": store.appended, "duplicates": store.duplicates,
                                 "errors": store.errors, "mode": self.entry_mode,
                                 "rotation": {k: rot.get(k) for k in
                                              ("archived", "trimmed", "health", "error",
                                               "hot_lines", "segment_id")},
                                 "links": dict(link_ctr),
                                 "link_events": link_reasons[-50:],
                                 "link_health": ("OK" if not link_ctr["link_failed"]
                                                 else "LINK_WRITE_FAILED")}
            return self._entry_cycle
        except Exception as exc:  # noqa: BLE001 — gözlem arızası turu DURDURAMAZ
            log.warning("giriş snapshot'ı yazılamadı: %s", exc)
            return {}
        finally:
            self._entry_pending = []

    @staticmethod
    def _atr_from_frame(frame) -> float | None:
        """Çerçevenin SON KAPANMIŞ barındaki `atr14`. Ölçülemezse `None` (sıfır değil)."""
        try:
            if frame is None or not len(frame) or "atr14" not in getattr(frame, "columns", ()):
                return None
            v = float(frame["atr14"].iloc[-1])
            return v if v > 0 and v == v else None
        except Exception:  # noqa: BLE001
            return None

    def _attach_weekly_context(self, snap: dict, rec: dict) -> None:
        """Snapshot'a haftalık yapı + bağlamsal mum kaydını ekler. Arıza turu DURDURMAZ.

        Eski satırlar bu alanlar olmadan da geçerlidir: okuyucular `.get()` kullanır ve
        eksiklik `UNKNOWN` olarak görünür — geriye dönük şema kırılmaz.
        """
        if getattr(self, "weekly_cfg", None) is None:
            return
        try:
            from .learn.candle_context import build_candle_context
            from .learn.weekly_structure import build_weekly_structure, rows_from_frame
            px = snap.get("entry_price")
            atr_pct = snap.get("atr_pct")
            # Haftalık modül MUTLAK ATR ister; snapshot yüzde taşır.
            atr_abs = ((float(atr_pct) / 100.0 * float(px))
                       if (atr_pct is not None and px) else None)
            if atr_abs is None:
                # Yedek: karar anı çerçevesinin SON KAPANMIŞ barındaki `atr14`. Bu, motorun
                # zaten hesapladığı point-in-time bir göstergedir; yeni veri çekilmez ve
                # gelecekten hiçbir şey okunmaz. Yoksa `None` kalır — sıfır SAYILMAZ.
                atr_abs = self._atr_from_frame(rec.get("intraweek_frame"))
            weekly = build_weekly_structure(
                symbol=snap.get("symbol"), direction=snap.get("direction"),
                now=rec.get("ts") or utc_now(), daily_frame=rec.get("daily_frame"),
                intraweek_frame=rec.get("intraweek_frame"), current_price=px,
                atr=atr_abs, cfg=self.weekly_cfg)
            snap["weekly_structure"] = weekly
            bars = rows_from_frame(rec.get("intraweek_frame"))
            # Karar anından SONRAKİ hiçbir bar kullanılmaz.
            cutoff = weekly.get("as_of_ms")
            if cutoff is not None:
                bars = [b for b in bars if b["timestamp"] <= cutoff]
            snap["candle_context"] = build_candle_context(
                bars=bars[-40:], atr=atr_abs,
                week_high=weekly.get("previous_completed_week_high"),
                week_low=weekly.get("previous_completed_week_low"),
                current_price=px, cfg=self.candle_cfg)
        except Exception as exc:  # noqa: BLE001 — bağlam arızası snapshot'ı ENGELLEMEZ
            log.warning("haftalık bağlam eklenemedi (%s): %s", snap.get("symbol"), exc)
            snap["weekly_structure"] = {"week_available": False,
                                        "data_quality": "UNAVAILABLE",
                                        "unavailable_reason": f"BUILD_FAILED:{type(exc).__name__}"}

    def _attach_mtf_context(self, snap: dict, rec: dict) -> None:
        """Snapshot'a ÇOK ZAMAN DİLİMLİ likidite teyidi bağlamını ekler (H — SHADOW).

        Sözleşme:

        * **Yeni sağlayıcı isteği YOK.** Yalnız `AgentRunner.last_frames` içindeki, motorun
          zaten çektiği `1d` ve `1h` kareleri kullanılır. Kare yoksa H `ABSTAIN` üretir.
        * Bağlam snapshot `append` edilmeden ÖNCE eklenir; böylece DEĞİŞMEZ kaydın parçası
          olur ve sonuç görüldükten sonra geriye dönük YAZILAMAZ.
        * Arıza turu DURDURMAZ ve aktif hiçbir kararı değiştirmez; en kötü ihtimalle H alanı
          dürüst bir `ABSTAIN` taşır.
        * H4→M15 ve H1→M5 / M15→M1 için ÜRETİMDE HİÇBİR İSTEK YAPILMAZ; durumları şemada
          `DATA_UNAVAILABLE_ABSTAIN` ve `FUTURE_RESEARCH_ONLY` olarak raporlanır.
        """
        if getattr(self, "mtf_cfg", None) is None:
            return
        try:
            from .learn.multitimeframe_context import (PAIR_D_H1, PAIR_H4_M15,
                                                       FUTURE_RESEARCH_ONLY_PAIRS,
                                                       evaluate_variants, pair_status)
            px = snap.get("entry_price")
            atr_pct = snap.get("atr_pct")
            # H mutlak ATR ister; snapshot yüzde taşır. Ölçülemezse `None` kalır — SIFIR DEĞİL.
            htf_atr = ((float(atr_pct) / 100.0 * float(px))
                       if (atr_pct is not None and px) else None)
            if htf_atr is None:
                htf_atr = self._atr_from_frame(rec.get("daily_frame"))
            ltf_atr = self._atr_from_frame(rec.get("hourly_frame"))
            as_of = rec.get("ts") or utc_now()
            as_of_ms = int(as_of.timestamp() * 1000)
            variants = evaluate_variants(
                symbol=snap.get("symbol"), baseline_direction=snap.get("direction"),
                as_of_ms=as_of_ms, pair=PAIR_D_H1,
                htf_frame=rec.get("daily_frame"), ltf_frame=rec.get("hourly_frame"),
                htf_atr=htf_atr, ltf_atr=ltf_atr, current_price=px,
                candidate_id=snap.get("candidate_id"), decision_id=snap.get("decision_id"),
                code_sha=self.code_sha(), base=dict(self.mtf_cfg.to_dict()))
            snap["mtf_context"] = {
                "schema_version": next(iter(variants.values()))["schema_version"],
                "mode": self.mtf_mode,
                "applied": False,
                "auto_promotion": False,
                "supported_pairs": [PAIR_D_H1],
                "pair_status": {p: pair_status(p) for p in
                                (PAIR_D_H1, PAIR_H4_M15, *FUTURE_RESEARCH_ONLY_PAIRS)},
                "variants": variants,
                "note_tr": ("SHADOW: karşı-olgusal; aktif giriş/emir yolunu ETKİLEMEZ. "
                            "Kaynak video KÂRLILIK KANITI DEĞİLDİR."),
            }
        except Exception as exc:  # noqa: BLE001 — H arızası snapshot'ı ENGELLEMEZ
            log.warning("çok zaman dilimli bağlam eklenemedi (%s): %s", snap.get("symbol"), exc)
            snap["mtf_context"] = {"schema_version": "multitimeframe_liquidity_confirmation_v1",
                                   "mode": getattr(self, "mtf_mode", "SHADOW"),
                                   "applied": False, "auto_promotion": False,
                                   "variants": {},
                                   "build_error": f"BUILD_FAILED:{type(exc).__name__}",
                                   "decision": "ABSTAIN"}

    # ------------------------------------------------------------------ kârlılık deneyi
    @staticmethod
    def _experiment_identity_policy(xp: dict) -> dict:
        """Config'ten gelen deney politikasının KİMLİK kurallarını uygular (fail-closed).

        * Tarihsel `pfexp_v1` / `pfexp_v1.0.0` motor tarafından YENİDEN ÇALIŞTIRILAMAZ:
          kanıtı salt okunurdur (`ValueError` → deney kurulmaz, aktif yol etkilenmez).
        * `evaluation_start_at` / `frozen_at` config ile VERİLEMEZ: başlangıç yalnız kimlik
          dosyasından okunur ya da ilk kurulumda "şimdi" dondurulur. Böylece başlangıç ne
          geriye ne ileriye çekilebilir.
        """
        from .learn.profitability_experiment import (LEGACY_POLICY_VERSION, V11_EXPERIMENT_ID,
                                                     V11_POLICY_VERSION)
        from .learn.profitability_store import LEGACY_EXPERIMENT_ID
        xp = dict(xp or {})
        if (str(xp.get("experiment_id") or "") == LEGACY_EXPERIMENT_ID
                or str(xp.get("policy_version") or "") == LEGACY_POLICY_VERSION):
            raise ValueError("pfexp_v1 / pfexp_v1.0.0 SUPERSEDED_INCOMPLETE_ENTRY_INPUT: "
                             "tarihsel deney salt okunurdur, yeniden çalıştırılamaz")
        if (str(xp.get("experiment_id") or "") == V11_EXPERIMENT_ID
                or str(xp.get("policy_version") or "") == V11_POLICY_VERSION):
            raise ValueError("pfexp_v1_1 / pfexp_v1.1.0 SUPERSEDED_E_SCOPE_MISMATCH: kabul "
                             "kapalı; yalnız drain ile izlenir, aktif deney olarak "
                             "yeniden başlatılamaz")
        for k in ("evaluation_start_at", "frozen_at"):
            if k in xp:
                log.warning("experiment_policy.%s config ile VERİLEMEZ; yok sayıldı "
                            "(başlangıç yalnız kimlik dosyasından dondurulur)", k)
                xp.pop(k, None)
        return xp

    def _setup_experiment_drain(self, st, current_cfg) -> None:
        """pfexp_v1_1 için KABUL-KAPALI takip (drain) kurulumu — kimlik birebir değilse KURULMAZ.

        Sözleşme: v1.1 kimliği (`experiment_id`, `policy_version`, `config_id`) kimlik
        dosyasındaki donmuş başlangıçtan BİREBİR yeniden kurulur ve v1.1 kitabının kimliğiyle
        karşılaştırılır. Uyuşmazlık, eksik kimlik dosyası ya da yabancı olay → drain YOK
        (`SUPERSEDED_E_SCOPE_MISMATCH_READ_ONLY`): izolasyon, F00036'nın kapanışını almak
        uğruna gevşetilmez.
        """
        from .learn.profitability_experiment import (STATUS_V11_DRAINING, STATUS_V11_READ_ONLY,
                                                     V11_EXPERIMENT_ID, V11_POLICY_VERSION,
                                                     ExperimentConfig)
        from .learn.profitability_store import ExperimentStore
        self.experiment_drain = None
        self._experiment_drain_state = {"status": None, "reason": "NOT_CONFIGURED"}
        try:
            if current_cfg is None or current_cfg.experiment_id == V11_EXPERIMENT_ID:
                return
            dstore = ExperimentStore(st, experiment_id=V11_EXPERIMENT_ID)
            if not dstore.events_path.exists() and not dstore.books_path.exists():
                self._experiment_drain_state = {"status": None, "reason": "NO_V11_STATE"}
                return
            ident = dstore.read_identity()
            if not ident:
                self._experiment_drain_state = {"status": STATUS_V11_READ_ONLY,
                                                "reason": "NO_V11_IDENTITY_FILE"}
                return
            dcfg = ExperimentConfig.from_dict({
                "experiment_id": V11_EXPERIMENT_ID, "policy_version": V11_POLICY_VERSION,
                "evaluation_start_at": ident["evaluation_start_at"],
                "frozen_at": ident["frozen_at"], "code_sha": self.code_sha()})
            bid = dstore.read_books_identity()
            if bid is not None and bid != dcfg.identity_key():
                self._experiment_drain_state = {"status": STATUS_V11_READ_ONLY,
                                                "reason": f"CONFIG_ID_MISMATCH:{bid[2]}"}
                return
            want = dcfg.identity_key()
            foreign = sum(1 for e in dstore.iter_events()
                          if (str(e.get("experiment_id")), str(e.get("policy_version")),
                              str(e.get("config_id"))) != want)
            if foreign:
                self._experiment_drain_state = {"status": STATUS_V11_READ_ONLY,
                                                "reason": f"FOREIGN_EVENTS:{foreign}"}
                return
            self.experiment_drain = {"cfg": dcfg, "store": dstore,
                                     "closed_at": str(current_cfg.evaluation_start_at),
                                     "superseded_by": current_cfg.experiment_id}
            self._experiment_drain_state = {"status": STATUS_V11_DRAINING, "reason": "OK"}
        except Exception as exc:  # noqa: BLE001 — drain kurulamazsa salt okunur kalır
            self.experiment_drain = None
            self._experiment_drain_state = {"status": STATUS_V11_READ_ONLY,
                                            "reason": f"SETUP_FAILED:{type(exc).__name__}"}

    def _experiment_superseded_versions(self, store) -> list:
        """Tarihsel `pfexp_v1` ve kabul-kapalı `pfexp_v1_1` kanıtlarının SALT OKUNUR özetleri."""
        out: list = []
        if getattr(store, "is_legacy", False):
            return out
        try:
            from .learn.profitability_store import legacy_v1_summary, legacy_v11_summary
            s1 = legacy_v1_summary(self.cfg.state_path)
            if s1:
                out.append(s1)
            if str(getattr(store, "experiment_id", "")) != "pfexp_v1_1":
                d = getattr(self, "experiment_drain", None) or {}
                ds = getattr(self, "_experiment_drain_state", None) or {}
                s11 = legacy_v11_summary(
                    self.cfg.state_path, drain_enabled=bool(d),
                    superseded_by=(d.get("superseded_by") if d else
                                   getattr(getattr(self, "experiment_cfg", None), "experiment_id", None)),
                    admissions_closed_at=(d.get("closed_at") if d else
                                          getattr(getattr(self, "experiment_cfg", None),
                                                  "evaluation_start_at", None)),
                    drain_reason=ds.get("reason"))
                if s11:
                    out.append(s11)
        except Exception as exc:  # noqa: BLE001 — özet okunamazsa açıkça raporlanır
            out.append({"status": "UNREADABLE", "error": type(exc).__name__})
        return out

    @staticmethod
    def _experiment_recent_decisions(store, n: int = 10) -> list:
        """Son giriş kararlarının panel özeti: politika kararları + P1'in E kapsam alanları."""
        try:
            from .learn.profitability_store import EV_DECISION
            by_trade: dict = {}
            order: list = []
            for e in store.iter_events():
                if e.get("kind") != EV_DECISION:
                    continue
                pl = e.get("payload") or {}
                tid = str(pl.get("trade_id") or "")
                if not tid:
                    continue
                if tid not in by_trade:
                    by_trade[tid] = {"trade_id": tid, "symbol": pl.get("symbol"),
                                     "side": pl.get("side"), "as_of": pl.get("as_of"),
                                     "decisions": {}, "e_scope_fields": None, "a_leg": None}
                    order.append(tid)
                row = by_trade[tid]
                row["decisions"][str(e.get("policy"))] = {
                    "decision": pl.get("decision"), "reason_codes": list(pl.get("reason_codes") or [])}
                if str(e.get("policy")) == "P1_SELECTIVE_AE":
                    legs = ((pl.get("evidence") or {}).get("legs") or {})
                    ev_e = ((legs.get("E") or {}).get("evidence") or {})
                    ev_a = ((legs.get("A") or {}).get("evidence") or {})
                    diag = ev_e.get("diagnostics") or {}
                    row["e_scope_fields"] = {
                        "scope": ev_e.get("scope"),
                        "futures_stop_risk_usdt": ev_e.get("portfolio_futures_stop_risk_usdt"),
                        "futures_risk_budget_usdt": ev_e.get("risk_budget_usdt"),
                        "futures_heat_fraction": ev_e.get("futures_heat_fraction"),
                        "combined_diagnostic_exposure_usdt": (
                            diag.get("portfolio_open_risk_usdt_combined")
                            if diag else ev_e.get("portfolio_open_risk_usdt")),
                        "non_futures_component_usdt": diag.get("non_futures_component_usdt"),
                        "combined_heat_fraction_diagnostic": (
                            diag.get("combined_heat_fraction_diagnostic")
                            if diag else ev_e.get("open_risk_fraction")),
                        "same_direction_open_futures": ev_e.get("same_direction_open_futures"),
                        "same_direction_open_combined": (
                            diag.get("same_direction_open_combined")
                            if diag else ev_e.get("same_direction_open")),
                        "decision": (legs.get("E") or {}).get("decision"),
                        "reason_codes": (legs.get("E") or {}).get("reason_codes"),
                    }
                    row["a_leg"] = {"decision": (legs.get("A") or {}).get("decision"),
                                    "p_win": ev_a.get("p_win"),
                                    "breakeven_p": ev_a.get("breakeven_p"),
                                    "conservative_net_edge_r": ev_a.get("conservative_net_edge_r")}
            return [by_trade[t] for t in order[-int(n):]]
        except Exception as exc:  # noqa: BLE001
            return [{"error": type(exc).__name__}]

    def _experiment_candidates(self, cfg) -> list[dict]:
        """Deney penceresinde açılmış ŞAMPİYON girişleri — SALT OKUNUR türetme.

        Kaynaklar yalnız KOPYA olarak okunur: kanonik defter pozisyonları/geçmişi, giriş
        snapshot bağları ve motorun zaten çektiği 1s kareleri. Hiçbir yeni sağlayıcı isteği
        yapılmaz ve hiçbir kanonik nesne DEĞİŞTİRİLMEZ.
        """
        from .learn.profitability_ae import point_in_time_ae
        from .learn.profitability_experiment import closed_returns
        start = from_iso(str(cfg.evaluation_start_at)) if cfg.evaluation_start_at else None
        # SICAK YOL: arşiv HER TURDA TARANMAZ (mevcut değişmez; bkz. retention testleri).
        # Deney penceresi saklama penceresinden çok dardır, gereken kanıt daima sıcaktadır.
        store = getattr(self, "entry_snapshot_store", None)
        links = (store.trade_links() if store else {})
        snaps = (store.by_candidate() if store else {})
        cand_by_trade = {t: snaps.get(c) for t, c in links.items()}
        # A/E KARARI TEK KANONİK YOLDAN: değişmez giriş snapshot'ı + mevcut challenger
        # değerlendiricisi, snapshot'ın donmuş as-of anında. `entry_selectivity.json.trades`
        # (kapanmış işlem atıfı) canlı giriş kararının kaynağı OLAMAZ: yeni açılan pozisyonun
        # orada satırı yoktur (doğrulanmış kusur). Kapanış geçmişi/öğrenilmiş sonuç OKUNMAZ.
        # Giriş politikası sürümü DENEY sürümüne kilitlidir (v1.2 → entry_v1.1.0). Motorun
        # kanonik eşikleri aynen kullanılır; yalnız sürüm etiketi deneyin istediğine sabitlenir.
        # Farklı sürümle yazılmış snapshot yeniden yorumlanmaz (adaptör ABSTAIN eder).
        from dataclasses import replace as _dc_replace
        base_cfg = getattr(self, "entry_cfg", None)
        want_pv = cfg.entry_policy_version
        pit_cfg = None
        if base_cfg is not None:
            pit_cfg = (base_cfg if getattr(base_cfg, "policy_version", None) == want_pv
                       else _dc_replace(base_cfg, policy_version=want_pv))
        out = []
        for pos in list(self.ledger2.positions.values()):
            d = pos.to_dict() if hasattr(pos, "to_dict") else dict(pos)
            tid = str(d.get("id") or "")
            opened = from_iso(str(d.get("opened_at") or "")) if d.get("opened_at") else None
            if not tid or opened is None:
                continue
            if start is not None and opened < start:
                continue                      # PRE_EXPERIMENT_OBSERVATION_ONLY
            entry = _f_num(d.get("entry_avg"))
            stop0 = _f_num(d.get("initial_stop"))
            qty = _f_num(d.get("qty"))
            risk = (abs(entry - stop0) * qty
                    if (entry is not None and stop0 is not None and qty) else None)
            sym = str(d.get("symbol") or "")
            frames = (getattr(self.runner, "last_frames", None) or {}).get(sym) or {}
            rets = None
            try:
                rets = closed_returns(frames.get("1h"),
                                      as_of_ms=int(opened.timestamp() * 1000),
                                      lookback=cfg.correlation_lookback_bars)
            except Exception:  # noqa: BLE001 — korelasyon ölçülemezse UNKNOWN kalır
                rets = None
            snap = cand_by_trade.get(tid)
            snap = snap if isinstance(snap, dict) else None
            entry_ae = (point_in_time_ae(snap, pit_cfg, required_entry_policy_version=want_pv)
                        if pit_cfg is not None else None)
            out.append({
                "trade_id": tid, "symbol": sym, "side": str(d.get("side") or ""),
                "entry": entry, "qty": qty, "initial_stop": stop0,
                "targets": [t for t in (d.get("targets") or [])],
                "leverage": _f_num(d.get("leverage")), "risk_usdt": risk,
                "entry_fee": _f_num(d.get("entry_fee")),
                "slippage_cost": _f_num(d.get("slippage_cost")),
                "opened_at": str(d.get("opened_at")),
                # BEŞ politikanın ortak as-of anı: snapshot'ın donmuş `ts`i; yoksa açılış anı.
                "as_of": str((snap or {}).get("ts") or d.get("opened_at")),
                "champion_accepted": True,
                "candidate_id": ((snap or {}).get("candidate_id") if snap else None),
                "decision_id": ((snap or {}).get("decision_id") if snap else None),
                "entry_ae": entry_ae,
                "returns_1h": rets,
            })
        return sorted(out, key=lambda r: str(r["opened_at"]))

    def _experiment_closes(self, cfg) -> dict:
        """Deney penceresinde KAPANMIŞ kanonik işlemler — kopya okuma."""
        from .learn.close_chain import canonical_closes
        start = from_iso(str(cfg.evaluation_start_at)) if cfg.evaluation_start_at else None
        out = {}
        for c in canonical_closes(self.ledger2.history):
            o = from_iso(str(c.get("opened_at") or "")) if c.get("opened_at") else None
            if o is None or (start is not None and o < start):
                continue                      # PRE_EXPERIMENT: deneye GİRMEZ
            out[str(c.get("trade_id"))] = c
        return out

    def _run_profitability_experiment(self, now) -> dict:
        """Aktif deney (pfexp_v1_2) + pfexp_v1_1 DRAIN'i. İkisi de TAMAMEN İZOLE.

        Sözleşme: kanonik defter/RiskEngine/muhasebe/gateway/sermaye DEĞİŞMEZ, hiçbir emir
        üretilmez, `applied` daima `False`tur. Arıza turu DURDURMAZ. Drain, aktif deneyden
        AYRI depo ve AYRI kimlikle çalışır; yeni kabul ÜRETEMEZ.
        """
        cfg = getattr(self, "experiment_cfg", None)
        store = getattr(self, "experiment_store", None)
        if cfg is None or store is None:
            return {}
        out = self._run_experiment_cycle(cfg, store, now, admissions_open=True)
        try:
            self._run_experiment_drain(now)
        except Exception as exc:  # noqa: BLE001 — drain arızası aktif deneyi/turu DURDURMAZ
            log.warning("pfexp_v1_1 drain çalıştırılamadı: %s", exc)
        return out

    def _run_experiment_drain(self, now) -> dict:
        """pfexp_v1_1: KABUL KAPALI — yalnız mevcut simüle pozisyonlar için mark/çıkış."""
        d = getattr(self, "experiment_drain", None) or {}
        if not d.get("cfg") or not d.get("store"):
            return {}
        return self._run_experiment_cycle(d["cfg"], d["store"], now, admissions_open=False,
                                          drain={"closed_at": d.get("closed_at"),
                                                 "superseded_by": d.get("superseded_by")})

    def _run_experiment_cycle(self, cfg, store, now, *, admissions_open: bool,
                              drain: dict | None = None) -> dict:
        """Tek deney kimliği için bir tur: (1) yeni kabuller [yalnız admissions_open], (2) marklar
        + P3/P4 çıkış yönetimi, (3) kanonik kapanış aynalama, (4) kitap + rapor."""
        try:
            from .learn import profitability_experiment as PX
            from .learn.profitability_store import (EV_CLOSE, EV_DECISION, EV_MARK, EV_OPEN)
            books, meta = store.load_books(cfg)
            events = []
            seen_decided = {p: {c.trade_id for c in books[p].closes} |
                               set(books[p].positions) for p in PX.POLICIES}

            # --- 1) YENİ ŞAMPİYON GİRİŞLERİ (filtre-only) — DRAIN'DE HİÇ ÇALIŞMAZ ------------
            for cand in (self._experiment_candidates(cfg) if admissions_open else []):
                tid = cand["trade_id"]
                for pol in PX.POLICIES:
                    b = books[pol]
                    if tid in seen_decided[pol]:
                        continue
                    ev_id = PX.event_id(cfg, pol, EV_DECISION, tid)
                    if ev_id in store.known_event_ids():
                        continue
                    d = PX.decide_entry(pol, cand, b, cfg)
                    events.append(PX.make_event(cfg, pol, EV_DECISION, d, tid, now=now))
                    if d["decision"] == PX.ACCEPT:
                        b.n_accept += 1
                        pos = PX.open_simulated(b, cand, cfg)
                        events.append(PX.make_event(
                            cfg, pol, EV_OPEN,
                            {"position": pos.to_dict(), "returns_1h": cand.get("returns_1h")},
                            tid, now=now))
                    elif d["decision"] == PX.FILTER:
                        b.n_filter += 1
                    else:
                        b.n_abstain += 1

            # --- 2) MARKLAR (kanonik yol; fiyat UYDURULMAZ) -------------------------------
            path_rows = []
            try:
                ps = getattr(self, "path_store", None)
                if ps is not None:
                    path_rows = [r for r in ps.iter_rows()] if hasattr(ps, "iter_rows") else []
            except Exception:  # noqa: BLE001
                path_rows = []
            for r in path_rows[-2000:]:
                tid = str(r.get("trade_id") or "")
                mark = r.get("mark")
                if not tid or mark is None:
                    continue
                for pol in PX.POLICIES:
                    b = books[pol]
                    if tid not in b.positions:
                        continue
                    key = (tid, r.get("snapshot_id") or r.get("ts"))
                    ev_id = PX.event_id(cfg, pol, EV_MARK, *key)
                    if ev_id in store.known_event_ids():
                        continue
                    PX.apply_mark(b, tid, mark)
                    events.append(PX.make_event(cfg, pol, EV_MARK,
                                                {"trade_id": tid, "mark": mark,
                                                 "ts": r.get("ts")}, *key, now=now))
                    # --- P3/P4: KENDİ çıkış yönetimi (kanonik stop/TP'ye DOKUNMAZ) -------
                    # Eşikler `exit_policy` sürümünden gelir ve YENİDEN AYARLANMAZ.
                    if pol not in (PX.P3, PX.P4) or self.exit_policy_cfg is None:
                        continue
                    sp = b.positions.get(tid)
                    if sp is None:
                        continue
                    snap_x = dict(r) | {"mark": mark, "current_stop": sp.stop,
                                        "entry": sp.entry, "initial_stop": sp.initial_stop,
                                        "side": sp.side, "qty": sp.qty,
                                        "mfe_r": sp.mfe_r, "trade_id": tid}
                    try:
                        from .learn.exit_policy import (CHALLENGER_A, TIGHTEN_STOP,
                                                        challenger_a)
                        act = challenger_a(snap_x, self.exit_policy_cfg)
                    except Exception:  # noqa: BLE001 — politika arızası kapanış UYDURMAZ
                        continue
                    if act.get("action") == TIGHTEN_STOP and act.get("stop_after") is not None:
                        ns = _f_num(act["stop_after"])
                        if ns is not None:
                            sp.stop = ns
                    # Sıkıştırılmış stop bu markta ihlal edildiyse SİMÜLE çıkış.
                    sgn = 1.0 if str(sp.side).upper().endswith("LONG") else -1.0
                    if sp.stop and ((mark - sp.stop) * sgn) <= 0:
                        ck = (tid, "policy_stop")
                        if PX.event_id(cfg, pol, EV_CLOSE, *ck) in store.known_event_ids():
                            continue
                        fee_rt = abs(sp.entry_fee) * 2.0 if sp.entry_fee else 0.0
                        sc = PX.close_simulated(
                            b, tid, exit_price=sp.stop, closed_at=str(r.get("ts") or iso(now)),
                            exit_kind=PX.X_POLICY_STOP, fees=fee_rt, funding=0.0)
                        if sc is not None:
                            events.append(PX.make_event(
                                cfg, pol, EV_CLOSE,
                                {"close": sc.to_dict(), "policy_action": CHALLENGER_A},
                                *ck, now=now))

            # --- 3) KANONİK KAPANIŞLARIN AYNALANMASI --------------------------------------
            for tid, c in self._experiment_closes(cfg).items():
                for pol in PX.POLICIES:
                    b = books[pol]
                    if tid not in b.positions:
                        continue
                    ev_id = PX.event_id(cfg, pol, EV_CLOSE, tid)
                    if ev_id in store.known_event_ids():
                        continue
                    px = _f_num((c.get("raw") or {}).get("exit_avg"))
                    if px is None:
                        p0 = b.positions[tid]
                        sign = 1.0 if str(p0.side).upper().endswith("LONG") else -1.0
                        pnl = _f_num(c.get("net_pnl"))
                        px = ((p0.entry + (pnl / (p0.qty * sign)))
                              if (pnl is not None and p0.qty) else None)
                    if px is None:
                        continue              # fiyat ölçülemedi → UYDURMA YOK, açık kalır
                    sc = PX.close_simulated(
                        b, tid, exit_price=px, closed_at=str(c.get("closed_at")),
                        exit_kind=PX.X_CANONICAL,
                        fees=abs(_f_num(c.get("fees")) or 0.0),
                        funding=(_f_num(c.get("funding")) or 0.0))
                    if sc is not None:
                        events.append(PX.make_event(cfg, pol, EV_CLOSE,
                                                    {"close": sc.to_dict()}, tid, now=now))

            # DRAIN GÜVENCESİ: kabul kapalıyken karar/dolum olayı YAPISAL olarak üretilemez;
            # yine de ikinci bir kilit — böyle bir olay oluşsa bile deftere GİREMEZ ve sayılır.
            drain_rejected = 0
            if not admissions_open:
                keep = [e for e in events if e.get("kind") not in (EV_DECISION, EV_OPEN)]
                drain_rejected = len(events) - len(keep)
                events = keep
            wrote = store.append_many(events)
            saved = store.save_books(books, cfg)
            doc = PX.compare(books, cfg, now=now)
            doc["run_id"] = self.run_id
            doc["config_hash"] = self.config_hash()
            doc["mode"] = self.experiment_mode
            doc["store"] = store.stats()
            doc["books_source"] = meta
            doc["cycle"] = wrote | {"books_saved": saved.get("ok")}
            doc["pre_experiment_excluded"] = self._experiment_pre_count(cfg)
            # KİMLİK ve DÜRÜSTLÜK: başlangıcın nereden dondurulduğu, tarihsel sürümün salt
            # okunur özeti ve panelde AYNEN görünen beyanlar raporun KENDİSİNDE durur.
            doc["identity_source"] = getattr(self, "_experiment_identity_source", None)
            doc["identity_file"] = str(getattr(store, "identity_path", ""))
            doc["report_file"] = str(getattr(store, "report_path", ""))
            doc["entry_policy_version"] = cfg.entry_policy_version
            doc["e_scope"] = cfg.e_scope
            doc["recent_entry_decisions"] = self._experiment_recent_decisions(store)
            if admissions_open:
                doc["status"] = PX.STATUS_ACTIVE
                doc["admissions_open"] = True
                doc["superseded_versions"] = self._experiment_superseded_versions(store)
            else:
                open_sim = {p: sorted(b.positions) for p, b in books.items()}
                complete = not any(open_sim.values())
                doc["status"] = (PX.STATUS_V11_COMPLETE if complete else PX.STATUS_V11_DRAINING)
                doc["admissions_open"] = False
                doc["admissions_closed_at"] = (drain or {}).get("closed_at")
                doc["superseded_by"] = (drain or {}).get("superseded_by")
                doc["superseded_reason_tr"] = PX.V11_SUPERSEDED_REASON_TR
                doc["drain"] = {"follow_up_only": True, "complete": complete,
                                "open_sim_positions": open_sim,
                                "rejected_admission_events": drain_rejected}
                doc["superseded_versions"] = []
                doc["profitability_conclusion"] = None
            doc["statements_tr"] = list(PX.HONESTY_STATEMENTS_TR)
            doc["missing_means"] = "ABSTAIN"
            doc["profitability_proven"] = False
            doc["winning_policy_selected"] = None
            doc["auto_promotion_possible_today"] = False
            # KÖK NEDEN GÖZLEMİ — kanonik kapanışların TAMAMI (deney penceresi DEĞİL).
            try:
                from .learn.close_chain import canonical_closes as _cc
                doc["root_cause"] = PX.root_cause_summary(_cc(self.ledger2.history))
            except Exception as exc:  # noqa: BLE001
                doc["root_cause"] = {"state": "UNAVAILABLE",
                                     "error": type(exc).__name__}
            # Rapor DENEY KİMLİĞİNE göre adlanır (`profitability_experiment_v1_1.json`);
            # tarihsel `profitability_experiment.json` (pfexp_v1) bir daha YAZILMAZ.
            atomic_write_json(store.report_path, doc)
            return {k: doc.get(k) for k in ("experiment_id", "config_id", "mode",
                                            "n_comparable_closes", "applied_to_canonical")}
        except Exception as exc:  # noqa: BLE001 — deney arızası turu DURDURAMAZ
            log.warning("kârlılık deneyi çalıştırılamadı (%s): %s", getattr(cfg, "experiment_id", "?"), exc)
            return {"error": f"EXPERIMENT_FAILED:{type(exc).__name__}"}

    def _experiment_pre_count(self, cfg) -> dict:
        """Deney başlangıcından ÖNCE açılmış pozisyon/kapanış sayısı — kanıt DIŞI."""
        start = from_iso(str(cfg.evaluation_start_at)) if cfg.evaluation_start_at else None
        if start is None:
            return {"open": None, "closed": None, "state": "UNKNOWN"}
        n_open = 0
        open_rows = []
        for pos in list(self.ledger2.positions.values()):
            d = pos.to_dict() if hasattr(pos, "to_dict") else dict(pos)
            o = from_iso(str(d.get("opened_at") or "")) if d.get("opened_at") else None
            if o is not None and o < start:
                n_open += 1
                open_rows.append({"trade_id": str(d.get("id") or ""),
                                  "symbol": str(d.get("symbol") or ""),
                                  "opened_at": str(d.get("opened_at") or ""),
                                  "label": "PRE_EXPERIMENT_OBSERVATION_ONLY"})
        from .learn.close_chain import canonical_closes
        n_cl = 0
        n_cl_after_start = 0
        for c in canonical_closes(self.ledger2.history):
            o = from_iso(str(c.get("opened_at") or "")) if c.get("opened_at") else None
            if o is not None and o < start:
                n_cl += 1
                cl = from_iso(str(c.get("closed_at") or "")) if c.get("closed_at") else None
                if cl is not None and cl >= start:
                    n_cl_after_start += 1     # başlangıçtan SONRA kapanan ön-deney pozisyonu
        return {"open": n_open, "closed": n_cl,
                "closed_after_start_still_excluded": n_cl_after_start,
                "open_ids": sorted(open_rows, key=lambda r: r["opened_at"])[:50],
                "label": "PRE_EXPERIMENT_OBSERVATION_ONLY"}

    #: `entry_selectivity.json.snapshot_cycle` içine GEÇEN `_entry_cycle` anahtarları.
    #: 1A bağ gözlenebilirliği (`links`, `link_events`, `link_health`) `_entry_flush`te
    #: ölçülüyordu ama bu beyaz listede OLMADIĞI için rapora hiç ulaşmıyordu; kanonik JSONL
    #: bağı doğru yazılırken panel "bağ yok" görüyordu. Anahtar kümesi burada tek yerde durur.
    ENTRY_CYCLE_REPORT_KEYS = ("at", "candidates", "written", "appended", "duplicates",
                               "errors", "mode", "links", "link_events", "link_health")

    @classmethod
    def _entry_cycle_summary(cls, cycle) -> dict:
        """`_entry_cycle` → rapor özeti. YALNIZ mevcut alanları geçirir; hiçbir şey uydurmaz.

        Bağ alanları ölçülmemişse (eski tur, arıza) anahtar rapora GİRMEZ: "ölçülmedi" ile
        "ölçüldü ve sıfır" ayrımı korunur. Kanonik kaynak `entry_snapshot.jsonl`dir; bu özet
        yalnız görünürlük sağlar.
        """
        return {k: v for k, v in (cycle or {}).items() if k in cls.ENTRY_CYCLE_REPORT_KEYS}

    def _drop_entry_snapshot_cache(self) -> None:
        """Aday snapshot `by_candidate` memosunu bırakır. ASLA istisna sızdırmaz."""
        store = getattr(self, "entry_snapshot_store", None)
        drop = getattr(store, "drop_hot_cache", None)
        if drop is None:
            return
        try:
            drop()
        except Exception:  # noqa: BLE001 — bellek temizliği turu bozamaz
            pass

    def _label_entry_outcomes(self, now) -> dict:
        """KANIT ONARIMI V1: ufku dolan aday snapshot'larını ileri fiyatla etiketler (tur sonu, fail-safe).

        Yalnız SICAK snapshot satırları okunur (arşiv açılmaz — `iter_all_rows` sıcak döngüde
        ÇAĞRILMAZ, deponun kendi değişmezi). Etiketler AYRI dosyaya eklenir
        (`entry_outcomes.jsonl`); `entry_snapshot.jsonl` DEĞİŞMEZ. İşlem davranışına dokunmaz.
        Durum belgesi: `entry_outcomes_status.json`. Arıza turu DURDURMAZ.
        """
        lv = self.cfg.v3.learning_v3
        if not bool(getattr(lv, "outcome_labeling_enabled", False)):
            return {"skipped": "disabled"}
        store = getattr(self, "entry_snapshot_store", None)
        if store is None:
            return {"skipped": "no_store"}
        from pathlib import Path as _Path
        st = _Path(self.cfg.state_path)
        try:
            from .learn.outcome_labeler import OutcomeStore, label_pending, summarize
            outcomes = OutcomeStore(st / "entry_outcomes.jsonl")
            horizon = int(getattr(lv, "outcome_horizon_hours", 168))
            cost_r = float(getattr(lv, "outcome_cost_r", 0.16))
            max_syms = int(getattr(lv, "outcome_max_symbols_per_tour", 15))
            stats = label_pending(store.iter_hot_rows(), outcomes, self._futures_provider_factory(),
                                  now_ms=int(now.timestamp() * 1000), horizon_h=horizon, cost_r=cost_r,
                                  max_symbols=max_syms, run_id=self.run_id)
            doc = stats.to_dict() | {"at": iso(now), "run_id": self.run_id, "horizon_h": horizon, "cost_r": cost_r,
                                     "max_symbols_per_tour": max_syms, "summary": summarize(outcomes)}
            atomic_write_json(st / "entry_outcomes_status.json", doc)
            if stats.labeled or stats.errors:
                log.info("aday sonuç etiketleme: %d etiket (+%d/-%d/zaman %d), %d sembol çekildi, %d ertelendi, %d hata",
                         stats.labeled, stats.wins, stats.losses, stats.timeouts, stats.symbols_fetched,
                         stats.symbols_deferred, len(stats.errors))
            return doc
        except Exception as exc:  # noqa: BLE001 — etiketleme arızası turu bozmaz
            log.warning("aday sonuç etiketleme arızası (tur sürer): %s", exc)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _write_entry_eval(self, now) -> dict:
        """Kapanmış işlemler için giriş challenger'larının karşı-olgusal raporunu yazar.

        Snapshot'ı olmayan kapanış `NO_SNAPSHOT` ile geçilir; `trade_memory` kayıtlarından
        türetilen gözlem snapshot'ları `LEGACY_MEMORY` işaretiyle rapora girer ve TERFİ KANITI
        SAYILMAZ (`entry_eval.finalize` kapıları yalnız `LINKED` üzerinden hesaplar).
        """
        store = getattr(self, "entry_snapshot_store", None)
        if store is None or getattr(self, "entry_cfg", None) is None:
            return {}
        try:
            from .learn.close_chain import canonical_closes
            from .learn.entry_eval import build_report
            from .learn.entry_snapshot import snapshot_from_memory_entry
            _en = self.cfg.v3.entry_selectivity
            # SICAK yol: arşiv HER TURDA taranmaz. Varsayılan saklama penceresi terfi
            # penceresinden geniştir, dolayısıyla gereken kanıt sıcakta bulunur.
            snaps = store.by_candidate()
            links = store.trade_links()
            if _en.include_legacy_memory:
                # GÖZLEM KÖPRÜSÜ: yeni depo boşken panel "hiç veri yok" göstermesin diye eski
                # giriş kayıtları da değerlendirilir — ama ayrı sınıfta ve kapı dışında.
                # YALNIZ `entry` satırları okunur: `trades()` giriş ile çıkışı birleştirir ve
                # sonucu taşıyan bir satır snapshot köprüsüne GİREMEZ.
                for row in [r for r in self.memory.iter_rows()
                            if isinstance(r, dict) and r.get("kind") == "entry"][-400:]:
                    if not row.get("trade_id"):
                        continue
                    tid = str(row["trade_id"])
                    if tid in links:
                        continue
                    ls = snapshot_from_memory_entry(row)
                    if ls and ls["candidate_id"] not in snaps:
                        snaps[ls["candidate_id"]] = ls
                        links[tid] = ls["candidate_id"]
            budget = None
            try:
                budget = (float(self.ledger2.summary({})["equity"])
                          * float(self.profile.max_total_open_risk_pct) / 100.0)
            except Exception:  # noqa: BLE001 — bütçe ölçülemezse E ailesi MISSING_DATA der
                budget = None
            closes_for_audit = canonical_closes(self.ledger2.history)
            # SICAK DÖNGÜ ARŞİVİ AÇMAZ. Bu, karar günlüğü hattında da korunan bir
            # değişmezdir: tur maliyeti arşiv boyutuna bağlanamaz. Varsayılan saklama
            # penceresi (20.000 satır ≈ 50 gün) 30 günlük terfi penceresinden geniştir,
            # dolayısıyla terfi için gereken kanıt DAİMA sıcaktadır. Arşivlenmiş kanıt
            # çevrimdışı araçlarla `store.resolve_missing(...)` üzerinden erişilebilir.
            resolve = {"archive_scanned": False, "reason": "DISABLED_IN_HOT_LOOP",
                       "archive_segments": int((store.retention_stats() or {})
                                               .get("n_segments") or 0)}
            doc = build_report(closes=closes_for_audit, snapshots=snaps,
                               links=links, cfg=self.entry_cfg, risk_budget_usdt=budget,
                               now=now)
            doc["run_id"] = self.run_id
            doc["entry_mode"] = self.entry_mode
            doc["applied_total"] = 0
            # KIMLIK: raporun HANGI kodla ve HANGI config'le üretildiği raporun KENDİSİNDE
            # durmalı. Kimliğini söylemeyen bir kanıt belgesi, sonradan hangi sürümün ürettiği
            # bilinemediği için denetlenemez.
            doc["code_sha"] = self.code_sha()
            doc["config_hash"] = self.config_hash()
            doc["snapshot_store"] = store.stats()
            doc["archive_resolution"] = resolve
            doc["snapshot_cycle"] = self._entry_cycle_summary(
                getattr(self, "_entry_cycle", None))
            doc["risk_budget_usdt"] = (round(budget, 6) if budget is not None else None)
            doc["replay_audit"] = self._entry_replay_audit(snaps, links, closes_for_audit)
            # F/G AİLELERİ (haftalık yapı + yapısal R:R) — ayrı bölüm, V1 çıktısı BOZULMAZ.
            doc["weekly_context"] = self._entry_eval_v2(closes_for_audit, snaps, links)
            # H AİLESİ — AYRI dosya ve AYRI kapılar. V1/V2 çıktısı BOZULMAZ; H yalnız kendi
            # bölümüne özet bırakır (panel tek dosyadan da okuyabilsin).
            doc["multitimeframe"] = self._write_mtf_eval(closes_for_audit, snaps, links, now)
            # Panelin okuyacağı özet: tam değerlendirme listesi diski şişirmesin.
            doc["trades"] = [{k: e.get(k) for k in
                              ("trade_id", "candidate_id", "symbol", "direction", "regime",
                               "closed_at", "exit_reason", "actual_r", "actual_net_pnl",
                               "status", "link_status", "evidence_grade", "cost_r")}
                             | {"families": {f: {kk: v.get(kk) for kk in
                                                 ("decision", "blocked_loser", "blocked_winner",
                                                  "avoided_loss_r", "missed_gain_r", "delta_r",
                                                  "reason_codes")}
                                             for f, v in (e.get("families") or {}).items()}}
                             for e in (doc.get("evaluations") or [])][-200:]
            doc.pop("evaluations", None)
            atomic_write_json(self.cfg.state_path / "entry_selectivity.json", doc)
            return doc
        except Exception as exc:  # noqa: BLE001
            log.warning("giriş seçiciliği değerlendirmesi yazılamadı: %s", exc)
            return {}

    def _write_mtf_eval(self, closes: list, snaps: dict, links: dict, now) -> dict:
        """H ailesinin karşı-olgusal raporunu `mtf_eval.json` dosyasına yazar.

        Arıza turu DURDURMAZ ve V1/V2 raporlarını BOZMAZ. Terfi kanıtı YALNIZ değişmez giriş
        snapshot'ında gerçekten `mtf_context` taşıyan ve gerçek `trade_id` bağı bulunan
        kapanışlardan hesaplanır: H'den ÖNCE açılmış her pozisyon `PRE_H_EXCLUDED`dır.
        """
        if getattr(self, "mtf_cfg", None) is None:
            return {}
        try:
            from .learn.mtf_eval import build_report as mtf_report
            # İZOLASYON KANITI raporun İÇİNDE durur: H'nin hiçbir aktif sayacı
            # oynatmadığı, okuyucunun ayrıca doğrulaması gereken bir iddia olmamalı.
            isolation = {
                "verified": True,
                "mode": self.mtf_mode,
                "applied": 0,
                "writes_ledger": False,
                "touches_gateway": False,
                "imports_risk_engine": False,
                "changes_ranking": False,
                "detail": ("H saf bir bağlam üreticisidir; snapshot dışında hiçbir yere "
                           "yazmaz ve aktif giriş/çıkış yolunu ETKİLEMEZ."),
            }
            doc = mtf_report(closes=closes, snapshots=snaps, links=links,
                             mode=self.mtf_mode, isolation=isolation, now=now)
            doc["run_id"] = self.run_id
            doc["code_sha"] = self.code_sha()
            doc["config_hash"] = self.config_hash()
            doc["policy_version"] = getattr(self.mtf_cfg, "policy_version", None)
            doc["config_id"] = getattr(self.mtf_cfg, "config_id", None)
            n_snap = sum(1 for r in snaps.values()
                         if isinstance(r, dict) and r.get("mtf_context"))
            n_link = sum(1 for tid, cid in links.items()
                         if isinstance(snaps.get(cid), dict) and snaps[cid].get("mtf_context"))
            doc["n_h_snapshots"] = n_snap
            doc["n_h_links"] = n_link
            if n_link == 0:
                doc["state"] = "PENDING_FIRST_H_LINK"
            doc["trades"] = [{k: e.get(k) for k in
                              ("trade_id", "candidate_id", "symbol", "direction", "closed_at",
                               "exit_reason", "actual_r", "actual_net_pnl", "status",
                               "evidence_grade")}
                             | {"variants": {v: {kk: d.get(kk) for kk in
                                                 ("decision", "reason_codes", "blocked_loser",
                                                  "blocked_winner", "avoided_loss_r",
                                                  "missed_gain_r", "structural_rr")}
                                             for v, d in (e.get("variants") or {}).items()}}
                             for e in (doc.get("evaluations") or [])][-200:]
            doc.pop("evaluations", None)
            atomic_write_json(self.cfg.state_path / "mtf_eval.json", doc)
            return {k: doc.get(k) for k in
                    ("schema_version", "mode", "applied", "auto_promotion", "state",
                     "n_h_snapshots", "n_h_links", "n_h_linked_closes", "n_pre_h_excluded",
                     "supported_pairs", "policy_version", "config_id")}
        except Exception as exc:  # noqa: BLE001 — H raporu turu DURDURAMAZ
            log.warning("çok zaman dilimli değerlendirme yazılamadı: %s", exc)
            return {"error": f"MTF_EVAL_FAILED:{type(exc).__name__}"}

    def _entry_eval_v2(self, closes: list, snaps: dict, links: dict) -> dict:
        """F/G ailelerinin karşı-olgusal raporu. Arıza turu DURDURMAZ; V1 raporu bozulmaz."""
        if getattr(self, "weekly_challenger_cfg", None) is None:
            return {"enabled": False, "reason": "WEEKLY_CONTEXT_DISABLED"}
        try:
            from .learn.entry_eval_v2 import build_report_v2
            doc = build_report_v2(closes=closes, snapshots=snaps, links=links,
                                  base_policy=self.weekly_challenger_cfg.to_dict())
            doc["enabled"] = True
            doc["weekly_config_id"] = getattr(self.weekly_cfg, "config_id", None)
            doc["candle_config_id"] = getattr(self.candle_cfg, "config_id", None)
            doc["applied_total"] = 0
            return doc
        except Exception as exc:  # noqa: BLE001
            log.warning("haftalık bağlam değerlendirmesi yazılamadı: %s", exc)
            return {"enabled": True, "error": f"{type(exc).__name__}", "applied_total": 0}

    def _entry_replay_audit(self, snaps: dict, links: dict, closes: list) -> dict:
        """FAZ 5 — geçmiş veriyle karar anını sadakatle yeniden üretebiliyor muyuz?

        Beklenen sonuç FAIL-CLOSED'dır (`NOT_REPLAYABLE`): karar günlüğünde `opportunity` yok,
        likidite alanları boş, `code_sha`/`config_hash` boş. Eksikler tam listeyle raporlanır;
        yerlerine varsayılan konup sentetik kârlılık ÜRETİLMEZ.
        """
        try:
            from .learn.entry_replay import replay_audit
            jr: list[dict] = []
            j = getattr(self, "decision_journal", None)
            if j is not None:
                for row in j.iter_rows():
                    if isinstance(row, dict) and str(row.get("outcome_kind")) == "ACCEPTED":
                        jr.append(row)
                jr = jr[-500:]
            mr = [r for r in self.memory.iter_rows()
                  if isinstance(r, dict) and r.get("kind") == "entry"][-500:]
            return replay_audit(journal_rows=jr, memory_rows=mr,
                                snapshots=list(snaps.values()), closes=closes, links=links)
        except Exception as exc:  # noqa: BLE001 — denetim arızası turu ETKİLEMEZ
            log.warning("giriş replay denetimi yapılamadı: %s", exc)
            return {}

    def _write_llm_status(self, now) -> dict:
        """LLM alt sisteminin GERÇEK durumunu yazar — salt gözlem, sır BASILMAZ.

        Panel bugüne kadar yalnız boş bütçe kartları gösteriyordu; bu "bütçe henüz harcanmadı"
        gibi okunuyordu. Oysa üretimdeki gerçek şudur: motor hiçbir yerde bir LLM servisi
        KURMUYOR, dolayısıyla hiç çağrı yapılamaz. Durum bu dosyada açıkça bildirilir.

        Anahtarın KENDİSİ hiçbir koşulda okunmaz/yazılmaz: yalnız env değişkeni ADI ve o adın
        tanımlı olup olmadığı (bool) raporlanır. Bu uç LLM'i ETKİNLEŞTİRMEZ.
        """
        try:
            _l = self.cfg.v3.llm
            mode = str(_l.mode or "OFF").upper()
            provider = str(_l.provider or "noop").lower()
            # Motorda kurulu bir servis var mı? (Bu sürümde YOK — uydurulmaz, ölçülür.)
            wired = any(getattr(self, a, None) is not None
                        for a in ("llm", "llm_service", "llm_client"))
            key_env = str(_l.api_key_env or "")
            key_present = bool(os.environ.get(key_env)) if key_env else False
            calls = 0
            budget = read_json(self.cfg.state_path / "llm_budget.json", default=None)
            if isinstance(budget, dict):
                try:
                    calls = int(budget.get("calls") or 0)
                except (TypeError, ValueError):
                    calls = 0
            log_path = self.cfg.state_path / "llm_calls.jsonl"
            if log_path.exists():
                try:
                    calls = max(calls, sum(1 for ln in
                                           log_path.read_text(encoding="utf-8",
                                                              errors="replace").splitlines()
                                           if ln.strip()))
                except OSError:
                    pass
            if mode == "OFF" or provider == "noop":
                status, why = "DISABLED", "config: llm kapalı (mode=OFF ya da provider=noop)"
            elif not wired:
                status, why = ("NOT_CONFIGURED",
                               "motorda kurulu LLM servisi yok — çağrı yolu HİÇ BAĞLI DEĞİL")
            elif not key_present:
                status, why = "NOT_CONFIGURED", f"{key_env} ortam değişkeni tanımlı değil"
            elif calls <= 0:
                status, why = "NO_CALLS", "yapılandırıldı fakat bu ortamda hiç çağrı kaydı yok"
            else:
                status, why = "ACTIVE", f"{calls} çağrı kaydı var"
            doc = {"schema_version": "llm_status_v1", "at": iso(now), "run_id": self.run_id,
                   "status": status, "reason_tr": why, "mode": mode, "provider": provider,
                   "service_wired": bool(wired), "api_key_env": key_env or None,
                   "api_key_present": key_present, "calls_recorded": calls,
                   "budget_file_present": isinstance(budget, dict),
                   "cannot_execute": True,
                   "note_tr": ("Bu belge yalnız GÖZLEMDİR: LLM'i etkinleştirmez, sağlayıcı "
                               "eklemez ve hiçbir sır değeri taşımaz (yalnız env değişkeni ADI).")}
            atomic_write_json(self.cfg.state_path / "llm_status.json", doc)
            return doc
        except Exception as exc:  # noqa: BLE001 — telemetri arızası turu ETKİLEMEZ
            log.warning("llm durumu yazılamadı: %s", exc)
            return {}

    def _write_position_management(self, marks: dict, decisions: dict, now) -> dict:
        """Açık pozisyonlar için SALT OKUNUR yönetim gözlemi yazar.

        Bu bir yürütme yolu DEĞİLDİR: `ADVISORY_ONLY` olarak işaretlenir ve motor bu dosyayı
        okumaz. Amaç, bugün ekrana yazılan `HOLD/REDUCE/EXIT` görüşünün ölçülebilir hale
        gelmesi ve ekonominin değerlendirilmediği yerde `UNKNOWN` görünmesidir.
        """
        try:
            from .learn.position_mgmt import ADVISORY_ONLY, build_snapshot_doc, management_snapshot
            rows = []
            for sym, pos in sorted(self.ledger2.positions.items()):
                tick = marks.get(sym)
                mark = float(tick.ref) if tick is not None else None
                rows.append(management_snapshot(position=pos, mark=mark,
                                                decision=decisions.get(sym),
                                                trade_id=str(pos.id), now=now,
                                                executor_mode=ADVISORY_ONLY))
            doc = build_snapshot_doc(rows, run_id=self.run_id, executor_mode=ADVISORY_ONLY, now=now)
            ex = getattr(self, "mgmt_executor", None)
            if ex is not None:
                intents = ex.plan(rows)
                doc["shadow_intents"] = intents
                doc["shadow_execution"] = ex.execute(intents)
            atomic_write_json(self.cfg.state_path / "position_management.json", doc)
            return doc
        except Exception as exc:  # noqa: BLE001 — gözlem arızası turu DURDURMAZ
            log.warning("pozisyon yönetim gözlemi yazılamadı: %s", exc)
            return {}

    def _write_learning_chain(self, chain_res: dict, now) -> dict:
        """Zincir özetini state'e atomik yazar — panel ve quant AYNI sayıyı okur."""
        try:
            from .learn.provenance import ProvenanceStore  # noqa: F401  (tip belgeleme)
            from .learn.reconcile import build_plan
            plan = build_plan(history=self.ledger2.history, memory=self.memory,
                              learner=self.learner, index=getattr(self, "learned_index", None),
                              provenance_store=getattr(self, "provenance", None))
            rep = plan.get("report") or {}
            last = None
            idx = getattr(self, "learned_index", None)
            if idx is not None:
                recs = sorted(idx.load().values(), key=lambda r: str(r.get("learned_at") or ""))
                last = recs[-1] if recs else None
            doc = {
                "schema_version": rep.get("schema_version"),
                "generated_at": iso(now), "run_id": self.run_id,
                "canonical_final_closes": rep.get("canonical_final_closes", 0),
                "outcomes": rep.get("outcomes", 0), "lessons": rep.get("lessons", 0),
                "entry_linked": rep.get("entry_linked", 0),
                "legacy_unlinked": rep.get("legacy_unlinked", 0),
                "missing_outcome": rep.get("missing_outcome", 0),
                "missing_lesson": rep.get("missing_lesson", 0),
                "duplicate_lessons": rep.get("duplicate_lesson_count", 0),
                "orphan_lessons": rep.get("orphan_lessons") or [],
                "last_learned_trade": (last or {}).get("trade_id"),
                "last_learned_at": (last or {}).get("learned_at"),
                "last_reconcile": {k: chain_res.get(k) for k in
                                   ("ran", "outcomes_added", "lessons_added", "indexed")},
                "influence_mode": getattr(getattr(self, "influence_cfg", None), "mode", "OFF"),
                "influence_applied": sum(1 for i in (getattr(self, "_influence_log", None) or [])
                                         if i.get("applied")),
                "code_sha": self.code_sha(), "config_hash": self.config_hash(),
                "rows": rep.get("rows") or [],
            }
            # QUANT TAZELİĞİ: rapor offline üretilir (worker'dan bağımsız, bilinçli). O yüzden
            # burada YENİDEN ÜRETİLMEZ; fakat kanonik kapanış sayısıyla karşılaştırılır. Eski bir
            # n=9 raporu, 18 kapanış varken "güncel" görünemez.
            q = read_json(self.cfg.state_path / "quant_eval.json", default=None) or {}
            qn = (q.get("overall") or {}).get("n")
            qn = int(qn) if isinstance(qn, (int, float)) else None
            doc["quant_sample_count"] = qn
            doc["quant_run_id"] = (q.get("manifest") or {}).get("run_id")
            doc["quant_covers_all_closes"] = (qn == doc["canonical_final_closes"]) if qn is not None else None
            doc["quant_sample_gap"] = ((doc["canonical_final_closes"] - qn) if qn is not None else None)
            atomic_write_json(self.cfg.state_path / "learning_chain.json", doc)
            return doc
        except Exception as exc:  # noqa: BLE001
            log.warning("öğrenme zinciri özeti yazılamadı: %s", exc)
            return {}

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
