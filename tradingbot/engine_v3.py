"""7/24 MOTOR v3 — legacy TradingEngine'i genişletir (eski `tour` korunur).

Tur akışı:
  kill-switch/health kontrolü → TARA (legacy tarayıcı, tier-1) → legacy ajanlar (uzman raporları + brief)
  → veri kalitesi kapısı → COIN HEAD (faktör grupları, red team, spot/futures planı) → BAŞ YÖNETİCİ
  → GLOBAL RISK ENGINE → tetik (4h kapanış / geri çekilme) → PAPER EXECUTION (FuturesLedgerV2 / SpotLedger)
  → tick (stop/TP/liq/funding; bar_advance) → ÖĞRENME (v1 uyumlu + v2 hafıza/postmortem) → gölge işlemler
  → durum dosyaları (atomik: ledger → learner → triggers) → Obsidian (legacy + Coin Heads) → health/heartbeat.
Öncelik: veri doğru mu → maliyet sonrası edge → risk → red team → portföy → uygulanabilirlik → kayıt → ancak o zaman aç.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from decimal import Decimal

from .accounting import (AmountType, FeeSchedule, FiltersCache, FuturesLedgerV2, LiquidationParams, MarketType, SizeSpec, SlippageModel,
                         SpotLedger, TaxPolicy, TickData, default_brackets, static_rates)
from .agents.manager import CoinBrief
from .coinhead import ChiefPortfolioManager, CoinHeadConfig, CoinHeadInputs, CoinHeadRegistry, Verdict
from .config import BotConfig
from .core import atomic_write_json, iso, new_id, read_json, run_id_now, stable_id, utc_now
from .engine import TradingEngine
from .learn import LearnConfig, LearnerV2, ModelRegistry, ShadowBook, TradeMemory
from .learning import features_from_brief
from .market.quality import DataQualityConfig, DataQualityGate
from .risk import KillSwitch, ModeState, RiskEngine, build_state, resolve_profile, warn_if_below_recommended

log = logging.getLogger(__name__)


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
        self.mode_state = ModeState(st / "mode.json")
        if self.mode_state.mode.value != cfg.mode:
            log.warning("mode.json (%s) ile config mode (%s) farklı — mode.json esas (geçişler yalnız manuel)", self.mode_state.mode.value, cfg.mode)
        if cfg.mode != "PAPER":
            for w in warn_if_below_recommended(self.profile):
                log.warning("risk profili uyarısı: %s", w)
        # --- muhasebe v2 (legacy dosya otomatik içe aktarılır; legacy defter nesnesi de kalır)
        fees = FeeSchedule(maker_pct=Decimal(str(v3.fees.futures_maker_pct)), taker_pct=Decimal(str(v3.fees.futures_taker_pct)), source=v3.fees.source)
        slip = SlippageModel(fixed_bps=Decimal(str(v3.fees.slippage_bps)))
        self.ledger2 = FuturesLedgerV2.load(self.ledger_path, starting_equity=cfg.futures.starting_equity_usdt, max_positions=cfg.futures.max_positions,
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
        self.registry = CoinHeadRegistry(self.head_cfg, max_workers=ch.max_workers)
        self.registry.load(st)          # snapshot olay-zaman sırası (legacy hash-only state güvenli geçer)
        self._tour_no = 0               # aynı ms için deterministik tie-breaker
        self.chief_mgr = ChiefPortfolioManager(clusters=v3.risk_profiles.clusters or None)
        self.quality = DataQualityGate(DataQualityConfig(max_candle_age_bars=v3.data.max_candle_age_bars, max_ticker_age_s=v3.data.max_ticker_age_s,
                                                         max_clock_drift_ms=v3.data.max_clock_drift_ms, max_price_divergence_pct=v3.data.max_price_divergence_pct))
        # --- öğrenme v2 (v1 `self.learner` korunur)
        self.memory = TradeMemory(st / "trade_memory.jsonl")
        self.model_registry = ModelRegistry(st / "models.json")
        self.learner2 = LearnerV2(self.memory, self.model_registry, LearnConfig(min_samples_train=v3.learning_v3.min_samples_train,
                                  holdout_frac=v3.learning_v3.holdout_frac, half_life_days=v3.learning_v3.half_life_days, calibrator=v3.learning_v3.calibrator),
                                  st / "learn_v2.json")
        self.shadow = ShadowBook(st / "shadow_book.json")
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
                pos.append({"symbol": sym, "market_type": "SPOT", "side": "LONG", "notional": q * marks.get(sym, ac), "margin": q * ac, "entry": ac,
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

    # ------------------------------------------------------------------ hızlı çıkış monitörü (tur beklemeden)
    def exit_check(self) -> list[dict]:
        """Açık pozisyonlar için canlı fiyatla stop/TP/likidasyon/zaman kontrolü + defter kaydı + öğrenme; tur/tarama beklemez.
        Yeni giriş AÇMAZ. Dönen: kapanan işlemlerin legacy dict'leri."""
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
            out = []
            for rec in records:
                legacy = rec.to_legacy_dict()
                snap = self.last_decisions.get(rec.symbol) or {}
                try:
                    self.learner.learn(legacy)
                    self.learner2.on_trade_closed(legacy | {"features": legacy.get("features") or {}}, {"regime": snap.get("regime"), "consensus_score": snap.get("consensus_score"),
                                                                                                        "dissent": snap.get("dissent"), "vetoes": snap.get("vetoes")})
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
        btc_dec = decisions.get("BTC/USDT")
        chief = self.chief_mgr.decide(list(decisions.values()), {"equity": state.equity, "open_positions": [o.to_dict() for o in state.open_positions],
                                                                 "total_open_risk_usdt": state.total_open_risk_usdt, "pnl_today": state.realized_pnl_today,
                                                                 "drawdown_pct": state.drawdown_pct}, btc_regime=btc_dec.regime if btc_dec else None)
        self.registry.chief = chief.to_dict()
        # legacy chief (obsidian/alerts için) — v3 chief modunu yansıt
        legacy_chief = self.runner.chief.decide(briefs)
        legacy_chief.generated_at = iso(now)
        legacy_chief.risk_mode = chief.market_risk_mode
        # p_win (v2 model + hiyerarşik önsel; v1 tahmini yedek)
        for b in briefs:
            f = features_from_brief(b, legacy_chief, b.scan_score or None)
            d = decisions.get(b.symbol)
            pr = self.learner2.predict(f, regime=d.regime if d else None, symbol=b.symbol, setup=b.plan.entry_type or None)
            b.p_win = round(pr.p_win_calibrated if pr.ready else (0.5 * pr.prior_used + 0.5 * self.learner.predict(f)), 3)
            if d:
                d.p_win = b.p_win
        # 4) RİSK + TETİK + PAPER EXECUTION
        opened: list[str] = []
        risk_log: list[dict] = []
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
        self.spot2.tick(marks_f, now)
        self.spot2.save(st / "spot_ledger.json")
        lessons = []
        for rec in records:
            legacy = rec.to_legacy_dict()
            snap = self.last_decisions.get(rec.symbol) or {}
            lessons.append(self.learner.learn(legacy))
            self.learner2.on_trade_closed(legacy | {"features": legacy.get("features") or {}}, {"regime": snap.get("regime"), "consensus_score": snap.get("consensus_score"),
                                                                                                "dissent": snap.get("dissent"), "vetoes": snap.get("vetoes")})
        # gölge işlemleri etiketle
        self._label_shadows()
        # challenger eğitimi (yeterli veri varsa; PAPER'da otomatik terfi kapı geçerse)
        if records and self.cfg.v3.learning_v3.enabled:
            try:
                if self.learner2.train_challenger(now=now) and self.cfg.v3.learning_v3.auto_promote_in_paper:
                    self.learner2.maybe_promote(self.mode_state.mode.value)
            except (ValueError, TypeError) as exc:
                log.warning("challenger eğitimi atlandı: %s", exc)
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
            return self._execute_locked(decisions, chief, briefs, state, marks, now)

    def _execute_locked(self, decisions, chief, briefs: list[CoinBrief], state, marks: dict[str, TickData], now: datetime) -> tuple[list[str], list[dict]]:
        opened: list[str] = []
        risk_log: list[dict] = []
        bmap = {b.symbol: b for b in briefs}
        # kritik bölge başında durumu YETKİLİ defterlerden yenile (çağıranın state'i bayat olabilir: retry/eşzamanlı yol)
        state = self._portfolio_state({k: float(v.last) for k, v in marks.items()})
        entries_allowed = True
        for sym in chief.priority + [s for s, d in decisions.items() if d.is_actionable and s not in chief.priority]:
            d = decisions.get(sym)
            b = bmap.get(sym)
            if d is None or b is None or not d.is_actionable:
                continue
            if not entries_allowed:     # önceki fill sonrası risk durumu yazılamadı → yeni giriş yok (fail-closed); çıkışlar tick'te sürer
                risk_log.append({"symbol": sym, "verdict": d.verdict.value, "risk_allowed": False, "risk_reasons": ["RISK_STATE_PERSIST_FAILED"], "at": iso(now)})
                continue
            if self._stopping():        # kooperatif durdurma istendi → yeni giriş kabul edilmez
                risk_log.append({"symbol": sym, "verdict": d.verdict.value, "risk_allowed": False, "risk_reasons": ["SHUTDOWN_REQUESTED"], "at": iso(now)})
                continue
            perm = chief.permission.get(sym, {})
            plan = d.active_plan
            if plan is None or not plan.valid:
                continue
            market = "SPOT" if d.verdict == Verdict.SPOT_LONG else "USDM_PERP"
            plan_dict = {"symbol": sym, "market_type": market, "direction": d.direction, "entry": plan.entry, "stop": plan.stop, "targets": plan.targets,
                         "notional": plan.notional, "margin": plan.margin, "leverage": plan.size.leverage, "amount_type": "NOTIONAL", "expected_r": plan.expected_r,
                         "spread_pct": next((r.metrics.get("spread_pct") for r in d.specialist_reports if r.agent_name == "orderbook_liquidity" and r.usable), None),
                         "min_notional": float(self.filters.get(sym, MarketType.SPOT if market == "SPOT" else MarketType.USDM_PERP).min_notional)}
            rd = self.risk.evaluate(plan_dict, state, {"now_utc": now})
            entry = {"symbol": sym, "verdict": d.verdict.value, "chief_allow": bool(perm.get("allow")), "chief_reason": perm.get("reason"),
                     "risk_allowed": rd.allowed, "risk_reasons": rd.reasons, "risk_warnings": rd.warnings, "adjusted_notional": rd.adjusted_notional,
                     "adjusted_leverage": rd.adjusted_leverage, "at": iso(now)}
            risk_log.append(entry)
            if not perm.get("allow") or not rd.allowed:
                # güçlü aday reddedildi → gölge işlem (karşı-olgusal)
                if self.cfg.v3.learning_v3.shadow_trades and plan.expected_r >= self.head_cfg.min_expected_r:
                    self.shadow.add({"plan_id": stable_id("plan", self.run_id, sym), "symbol": sym, "market_type": market, "direction": d.direction, "entry": plan.entry,
                                     "stop": plan.stop, "targets": plan.targets, "horizon_bars": plan.time_horizon_bars, "leverage": plan.size.leverage},
                                    (["CHIEF:" + str(perm.get("reason"))] if not perm.get("allow") else []) + rd.reasons, now=now)
                continue
            # tetik: legacy mantık (kırılım: 4h kapanış seviyenin ötesinde; geri çekilme: fiyat seviyeye değdi)
            if not self._trigger_fired(b, d.direction, plan.entry, plan.entry_type):
                continue
            feats = features_from_brief(b, self.runner.chief.decide(briefs), b.scan_score or None)
            feats.update({"initial_stop": plan.stop, "p_win": b.p_win, "regime": d.regime, "consensus_score": d.consensus_score, "consensus_conf": d.consensus_confidence,
                          "n_dissent": len(d.dissent), "n_vetoes": len(d.vetoes), "expected_r": d.expected_r, "expected_cost_pct": d.expected_cost, "market_type": market,
                          "spread_pct": plan_dict.get("spread_pct")})
            notional = float(rd.adjusted_notional or plan.notional)
            if market == "USDM_PERP":
                pos = self.ledger2.open(sym, d.direction, b.price, SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, int(rd.adjusted_leverage or 1)),
                                        stop=plan.stop, targets=plan.targets, filters=self.filters.get(sym, MarketType.USDM_PERP), setup_type=plan.entry_type,
                                        trigger_text=plan.entry_trigger, features=feats, tick=marks.get(sym), now=now,
                                        meta={"coin_head_id": d.coin_head_id, "run_id": self.run_id, "decision_snapshot": d.to_dict(include_reports=False)})
                if pos is None:
                    entry["exec_reject"] = self.ledger2.last_reject_reason
                    continue
                trade_id = pos.id
                desc = f"{sym} {d.direction} FUTURES @ {float(pos.entry_avg):.6g} · notional {float(pos.qty * pos.entry_avg):.2f} · {pos.leverage}x · stop {plan.stop:.6g} · TP {', '.join(f'{t:.6g}' for t in plan.targets)} · P(win) %{(b.p_win or 0.5)*100:.0f}"
            else:
                order = self.spot2.market_buy(sym, quote_amount=Decimal(str(notional)), ref_price=Decimal(str(b.price)), tick=marks.get(sym), strategy=plan.entry_type, now=now)
                if order is None or str(getattr(order, "status", "")).upper() not in ("FILLED", "PARTIALLY_FILLED"):
                    entry["exec_reject"] = getattr(self.spot2, "last_reject_reason", "spot reject")
                    continue
                trade_id = getattr(order, "id", new_id("spot"))
                desc = f"{sym} SPOT LONG @ {b.price:.6g} · {notional:.2f} USDT · stop {plan.stop:.6g}"
            self.memory.record_entry({"trade_id": trade_id, "symbol": sym, "direction": d.direction, "market_type": market, "setup_type": plan.entry_type,
                                      "regime": d.regime, "features": feats, "decision": d.to_dict(include_reports=True), "chief": chief.to_dict(),
                                      "risk_decision": rd.to_dict(), "run_id": self.run_id, "mode": self.mode_state.mode.value,
                                      "model_versions": d.model_versions | {"p_win_model": self.learner2.snapshot().get("champion")}})
            self.last_decisions[sym] = d.to_dict(include_reports=False)
            opened.append(desc)
            # fill sonrası: yetkili defterlerden durum yenile → aynı turdaki sonraki adaylar bu pozisyonu/marjı/exposure'ı görür
            state, entries_allowed = self._refresh_after_fill(marks, risk_log, now)
            entry["state_after_fill"] = {"open_positions": len(state.open_positions), "used_margin": round(state.used_margin, 6),
                                         "total_open_risk_usdt": round(state.total_open_risk_usdt, 6), "persisted": entries_allowed}
        self.trig_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.trig_path, self.triggers, indent=None)
        return opened, risk_log

    def _trigger_fired(self, b: CoinBrief, direction: str, entry: float, entry_type: str) -> bool:
        if not b.price or not entry:
            return False
        if entry_type == "breakout":
            if not b.last_bar_4h or not b.last_close_4h:      # 4h çerçeve yoksa asla tetiklenmez (audit: last_close_4h=0 bug'ı)
                return False
            if self.triggers.get(b.symbol) == b.last_bar_4h:
                return False
            lvl = entry / 1.001 if direction == "LONG" else entry / 0.999
            fired = (b.last_close_4h > lvl) if direction == "LONG" else (b.last_close_4h < lvl)
            if fired and abs(b.price / entry - 1) > 0.015:
                fired = False
            self.triggers[b.symbol] = b.last_bar_4h
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
                self.shadow.label(sh, h4.reset_index(drop=True) if "timestamp" in h4 else h4)
            except (KeyError, ValueError, TypeError) as exc:
                log.warning("gölge işlem etiketlenemedi %s: %s", sh.id, exc)

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
