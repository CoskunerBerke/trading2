# -*- coding: utf-8 -*-
"""FORMASYON PAPER DEFTERİ — ayrı sanal bakiye/kimlik; ortak risk (`RiskEngine`), ortak uygulayıcı (`apply_action`), gerçek
`FuturesLedgerV2`; plan yaşam döngüsü kalıcı ve denetlenebilir.

Durum geçişleri (plan): AWAITING_TRIGGER → TRIGGERED → RISK_CHECK → OPENED → MANAGED → CLOSED; alternatif sonlar
REJECTED / BROKEN / EXPIRED / CANCELLED. Aynı plan iki kez emir açamaz (`position_id`), aynı sembolde ikinci pozisyon
açılmaz, karşı taraf tetiklenince diğer plan iptal olur, zarardan sonra sembol bazında soğuma vardır (otomatik tersleme /
martingale YOK). Veri kaynağı, fiyat güncelliği, spread/derinlik, stop mesafesi, maliyet sonrası R/R ve kill-switch
kontrolleri zorunludur; olasılık modeli YOKTUR (p_win=None) — uydurma 0,50 ile kapı geçirilmez.

ÖĞRENME MODU (2026-09-28, öğrenme modu; yalnız PAPER): motor `set_learning(BookLearning|None, universe_symbols=…,
gate=…)` verir, tarayıcı her tur başında `begin_cycle` ile sabitler. Açıkken: adet tavanı yok (D1), slot boyutu (D2/D3,
`pattern_trader/learning.py`), zarar sonrası soğuma yok (D5), yuvarlama riski ölçeklenir (D6), R/R tabanı yok (D7),
likidite bilinmiyorsa pencere içinde bekler (D16), derinlik eşiği notional'a bağlı (D17), evren protokol ∪ ana evren
(S9); açılmayan geçerli sinyal `counterfactual_trades.json`a düşer. Kapalıyken (None) her yol bit-bit eskisi gibidir.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from ..accounting import (R_MAX_POSITIONS, FeeSchedule, FuturesLedgerV2, LiquidationParams, MarketType, PositionSide,
                          SlippageModel, TaxPolicy, TickData, default_brackets)
from ..accounting.funding import FundingSchedule
from ..core import atomic_write_json, iso, read_json, utc_now
from ..learn import TradeMemory
from ..learn.candle_context import CandleContextConfig, detect_trend
from ..learning_mode import (STATE_ACTIVE, STATE_DISABLED, SUSPENDED_PREFIX, LearningMode, baseline_view,
                             learning_tags, policy_reserve_usdt)
from ..risk import RiskEngine, build_state
from ..strategy_paper import (PAPER_MARKET, DataVerdict, _baseline_blocks, apply_action, apply_closed_bars_to_ledger,
                              monitoring_gap_on_resume, parse_ts_ms)
from ..timeframes import tf_ms
from .data import REQUIREMENTS, readiness
from .detect import ST_BROKEN, ST_CONFIRMED, ST_EXPIRED, atr14, detect_findings, levels_for, update_finding
from .learning import LEARNING_MIN_DEPTH_USDT, cf_horizon, open_learning
from .strategy import (CONTEXT_TF, ENTRY_TF, PL_AWAITING, PL_BROKEN, PL_CANCELLED, PL_CLOSED, PL_EXPIRED, PL_MANAGED, PL_OPENED,
                       PL_REJECTED, PL_RISK_CHECK, PL_TRIGGERED, PROTOCOL_VERSION, STRUCTURE_TF, TERMINAL, build_plans,
                       cost_fraction, cost_fraction_at_fill, evaluate_trigger, rr_after_cost)
from .universe import iso_ms

log = logging.getLogger(__name__)
BOOK_KEY = "pattern_trader"
BOOK_NAME = "pattern_v1"
SUMMARY_FILE = "pattern_trader.json"
SCHEMA_VERSION = "pattern_trader_v1"
MAX_EVENTS = 300
#: RESMÎ FİLTRE BAYATLIK SINIRI (2026-09-17). Keşif kaydından bağlanan `exchangeInfo` filtreleri bu yaştan eskiyse
#: giriş yapılmaz. Kural değişimi nadirdir ama SESSİZCE eski kuralla emir açmak yerine yenilemeyi beklemek
#: tercih edilir. Config'ten TÜRETİLMEZ: tarama sıklığı 0'a çekilse bile sınır aynı kalır.
FILTERS_MAX_AGE_MS = 2 * 3_600_000
#: YUVARLAMA SONRASI RİSK PAYI. Gerçekleşme fiyatı tick ızgarasına oturunca giriş-stop mesafesi (dolayısıyla
#: gerçekleşen risk) büyüyebilir. Bu orandan fazlası kabul EDİLMEZ: risk tavanı yuvarlamayla gevşemez.
RISK_ROUNDING_TOLERANCE = 0.02


class PatternBook:
    """Tek nesne: bulgular + planlar + defter. İş parçacığı güvenli (RLock): tarayıcı ve çıkış izleyicisi aynı defteri kullanır."""

    def __init__(self, cfg, *, profile, killswitch, filters_cache, section) -> None:
        v3 = cfg.v3
        self.cfg, self.profile, self.filters_cache, self.section = cfg, profile, filters_cache, section
        self.key, self.name = BOOK_KEY, BOOK_NAME
        self.summary_file = SUMMARY_FILE
        self.state_dir = Path(cfg.state_path) / BOOK_KEY
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.state_dir / "futures_ledger.json"
        fees = FeeSchedule(maker_pct=Decimal(str(v3.fees.futures_maker_pct)), taker_pct=Decimal(str(v3.fees.futures_taker_pct)), source=v3.fees.source)
        slip = SlippageModel(fixed_bps=Decimal(str(v3.fees.slippage_bps)))
        self.cost_frac = cost_fraction(taker_fee_pct=float(v3.fees.futures_taker_pct), slippage_bps=float(v3.fees.slippage_bps))
        #: Gerçekleşme fiyatı hesaplandıktan SONRA kalan maliyet (giriş kayması fiyata gömülüdür; iki kez sayılmaz).
        self.cost_frac_at_fill = cost_fraction_at_fill(taker_fee_pct=float(v3.fees.futures_taker_pct),
                                                       slippage_bps=float(v3.fees.slippage_bps))
        #: ÖĞRENME MODU (2026-09-28, öğrenme modu) — D1: defter DAİMA bugünkü gibi (enforce=True) kurulur; adet tavanı
        #: yalnız öğrenme AKTİF olan turda kalkar (`begin_cycle` bayrağı tur başına ayarlar). Askıda/kapalıyken sıra ve
        #: kalıcı bayrak bugünküyle aynıdır (önce RiskEngine, sonra defter tavanı).
        self._lm_cfg = LearningMode.from_config(cfg, mode_gate=lambda: (False, "BOOK_CONFIG_ONLY"))
        self.learning_capable = bool(self._lm_cfg.enabled and self._lm_cfg.book_cfg(BOOK_KEY).enabled)
        self.ledger = FuturesLedgerV2.load(self.ledger_path, starting_equity=float(section.starting_equity_usdt),
                                          max_positions=int(section.max_open_positions), enforce_position_cap=True,
                                          fees=fees, slippage=slip, brackets=default_brackets(),
                                          liq_params=LiquidationParams(liq_fee_pct=Decimal(str(v3.futures_v3.liq_fee_pct))),
                                          # FUNDING (2026-09-17): oran bilinmiyorsa TAHMIN YOK — dönem bekler. Bu KURULUŞTA
                                          # ayarlanır (yalnız `bind_funding`e bırakılmaz): tarayıcı hiç başlamasa bile eski
                                          # bir `meta.last_funding_rate` tahmini kesinti ÜRETEMEZ.
                                          funding=FundingSchedule(fallback_to_last_known=False),
                                          tp1_fraction=Decimal("1"), breakeven_at_mfe_r=Decimal("0"), tax_policy=TaxPolicy.disabled())
        #: İZLEME KESİNTİSİ (2026-09-23): defterin bu süreç açılmadan ÖNCEKİ son kaydı ve o anda açık pozisyonlar;
        #: bu süreçteki İLK defter etkinliğinde (`_resume_once`) bir kez denetlenir.
        self._resume_saved_at = self.ledger.updated_at
        self._resume_positions: list[str] = sorted(self.ledger.positions)
        self._resume_checked = False
        self._gap_until_ms: int | None = None
        self.monitoring_gap: dict[str, Any] | None = None
        #: KORUYUCU İZLEYİCİ (2026-09-24): gözlem ölçümü (`protective_monitor.ObservationLog`); None → ölçüm yok.
        self.observer: Any = None
        self.risk = RiskEngine(profile, killswitch, v3.risk_profiles.clusters or None)
        self.memory = TradeMemory(self.state_dir / "trade_memory.jsonl", source="PATTERN_PAPER")
        self.lock = threading.RLock()
        self.candle_cfg = CandleContextConfig()
        self.params = {"stop_buffer_atr": float(section.stop_buffer_atr), "min_rr_after_cost": float(section.min_rr_after_cost),
                       "fallback_rr": float(section.fallback_rr), "measured_move_mult": float(section.measured_move_mult)}
        self.families = tuple(section.families)
        #: PROTOKOL (2026-09-25): "classic" → v1/v2 (yapı moduna göre, eski davranış); "momentum_4h_v3" → yalnız laboratuvarda
        #: sıkı testi geçen 4h sinyali (`strategy_v3`). `symbols` doluysa YENİ plan yalnız bu coinlerde kurulur; açık
        #: pozisyonlar ve eski protokolden kalan pozisyonlar kendi stop/hedef/zaman stopuyla yönetilmeye devam eder.
        self.protocol = str(getattr(section, "protocol", "classic") or "classic")
        _syms = [str(x).upper() for x in (getattr(section, "symbols", None) or [])]
        if self.protocol == "momentum_4h_v3" and not _syms:
            from .strategy_v3 import V3_SYMBOLS
            _syms = list(V3_SYMBOLS)
        self.allowed_symbols: frozenset[str] | None = frozenset(_syms) if _syms else None
        self.run_id = ""
        self.plans: dict[str, dict[str, Any]] = {}
        self.findings: dict[str, dict[str, Any]] = {}
        self.counters = {"scans": 0, "findings": 0, "confirmed": 0, "plans": 0, "triggered": 0, "opened": 0, "closed": 0, "rejected": 0,
                         "expired": 0, "broken": 0, "cancelled": 0, "data_rejected": 0}
        self.rejections: dict[str, int] = {}
        self.events: list[dict[str, Any]] = []
        self.data_gaps: dict[str, dict[str, Any]] = {}
        self.cooldown_until: dict[str, int] = {}
        self.symbol_scans: dict[str, dict[str, Any]] = {}
        self.cohort_stats: dict[str, dict[str, int]] = {}
        self.closed_recent: list[dict[str, Any]] = []
        #: Gerçekleşmiş funding oran kaynağı (`bind_funding` ile tarayıcıdan gelir). None iken funding dönemleri
        #: BEKLER: bilinmeyen maliyet sıfır SAYILMAZ, kapsama `funding_coverage` ile görünür kalır.
        self.funding_rates: Any = None
        #: ORTAK YAPI (structures_v1): OFF → v1 (A/B/C) aynen; SHADOW → v1 işlem yapar, v2 kararları yalnız kaydedilir;
        #: ENFORCE → bulgular ve planlar ORTAK KATALOGDAN (protokol v2), zincirin geri kalanı (tetik→risk→giriş→yönetim) aynı.
        _st = getattr(v3, "structures", None)
        self.structure_mode = _st.mode_for("pattern_trader") if _st is not None else "OFF"
        self._structure_store = None
        #: Tur içi filtre belleği (sembol → (keşif anı ms, SymbolFilters)). PAYLAŞILAN önbelleğe YAZILMAZ;
        #: bkz. `_resolve_filters` — ana botun resmî yenileme damgası kirletilmez, kilitsiz nesneye iş
        #: parçacığından yazılmaz.
        self._filters_memo: dict[str, tuple[int, Any]] = {}
        #: ÖĞRENME MODU (2026-09-28, öğrenme modu): motor `set_learning` ile görünümü verir, tarayıcı her tur başında
        #: `begin_cycle` ile BİR KEZ alır (tur ortasında değişmez). `learning` None → her yol bugünkü gibi.
        self.learning: Any = None
        self._lm_next: Any = None
        self._lm_gate: Callable[[], tuple[bool, str]] | None = None
        self._lm_universe_next: frozenset[str] = frozenset()
        self._lm_universe: frozenset[str] = frozenset()
        self._lm_engine_status: dict[str, Any] = {}
        self._lm_reason = STATE_DISABLED
        self._lm_since: str | None = None
        self._lm_ever = False
        self.risk_learning: RiskEngine | None = None      # öğrenme profili (toplam açık risk 100); ilk kullanımda
        self.cf: Any = None                               # karşı-olgusal kayıtçı (`counterfactual_trades.json`); ilk kullanımda
        self.learning_counters: dict[str, int] = {"opened": 0, "liquidity_waits": 0, "cooldown_skipped": 0,
                                                  "rr_floor_ignored": 0, "rescaled_after_rounding": 0,
                                                  "min_notional_bumped": 0, "shrunk_to_margin": 0,
                                                  "counterfactual_recorded": 0, "max_positions_blocked": 0,
                                                  "counterfactual_superseded": 0}
        self._restore()

    # ------------------------------------------------------------------ kalıcılık
    def _restore(self) -> None:
        try:
            p = read_json(self.state_dir / "plans.json", default=None) or {}
            self.plans = {k: v for k, v in (p.get("plans") or {}).items() if isinstance(v, dict)}
            f = read_json(self.state_dir / "findings.json", default=None) or {}
            self.findings = {k: v for k, v in (f.get("findings") or {}).items() if isinstance(v, dict)}
            s = read_json(Path(self.cfg.state_path) / self.summary_file, default=None) or {}
            if isinstance(s, dict) and s.get("key") == self.key:
                for k in self.counters:
                    if k in ("opened", "closed"):
                        continue
                    self.counters[k] = int((s.get("counters") or {}).get(k) or 0)
                self.rejections = {str(k): int(v) for k, v in (s.get("rejections") or {}).items()}
                self.events = [e for e in (s.get("events_recent") or []) if isinstance(e, dict)][-MAX_EVENTS:]
                self.cooldown_until = {str(k): int(v) for k, v in (s.get("cooldown_until_ms") or {}).items()}
                self.cohort_stats = {str(k): dict(v) for k, v in (s.get("cohort_stats") or {}).items() if isinstance(v, dict)}
                self.closed_recent = list(s.get("closed_recent") or [])[-50:]
                self.symbol_scans = {str(k): dict(v) for k, v in (s.get("symbol_scans") or {}).items() if isinstance(v, dict)}
                _lc = (s.get("learning") or {}).get("counters") if isinstance(s.get("learning"), dict) else None
                for k in self.learning_counters:                # öğrenme sayaçları (yalnız özet taşıyorsa)
                    if isinstance(_lc, dict) and k in _lc:
                        self.learning_counters[k] = int(_lc.get(k) or 0)
        except Exception as exc:  # noqa: BLE001 — bozuk özet: sayaçlar sıfırdan, defter ETKİLENMEZ
            log.warning("formasyon defteri durumu geri yüklenemedi: %s", exc)
        closed = len(self.ledger.history_dicts())
        self.counters["closed"] = closed
        self.counters["opened"] = closed + len(self.ledger.positions)

    def save(self, marks_f: dict[str, float] | None, now: datetime) -> None:
        with self.lock:
            self._resume_once(now)
            self.ledger.save(self.ledger_path)
            if self.cf is not None:                    # öğrenme karşı-olgusalı: yalnız değiştiyse tek atomik yazım
                try:
                    self.cf.sync_book_counters(self.learning_counters)   # sayaçların kalıcı yedeği (özetten bağımsız)
                    self.cf.save()
                except Exception as exc:  # noqa: BLE001 — kayıt arızası defteri ETKİLEMEZ
                    log.warning("formasyon karşı-olgusal kaydı yazılamadı: %s", exc)
            atomic_write_json(self.state_dir / "plans.json", {"schema_version": "pattern_plans_v1", "generated_at": iso(now), "plans": self.plans})
            atomic_write_json(self.state_dir / "findings.json", {"schema_version": "pattern_findings_v1", "generated_at": iso(now), "findings": self.findings})
            fs = self.ledger.summary(dict(marks_f or {}))
            by_status: dict[str, int] = {}
            for pl in self.plans.values():
                by_status[pl.get("status") or "?"] = by_status.get(pl.get("status") or "?", 0) + 1
            fstat: dict[str, int] = {}
            for f in self.findings.values():
                fstat[f.get("status") or "?"] = fstat.get(f.get("status") or "?", 0) + 1
            doc = {"schema_version": SCHEMA_VERSION, "key": self.key, "name": self.name, "protocol_version": PROTOCOL_VERSION, "generated_at": iso(now),
                   "run_id": self.run_id, "mode": "PAPER", "starting_equity": float(self.ledger.starting_equity),
                   "summary": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in fs.items()},
                   "positions": {s: {"side": p.side.value, "entry": float(p.entry_avg), "qty": float(p.qty), "stop": float(p.stop) if p.stop else None,
                                     "targets": [float(t) for t in p.targets], "leverage": p.leverage, "opened_at": p.opened_at,
                                     "last_price": float(p.last_price) if p.last_price else None, "plan_id": p.meta.get("plan_id"),
                                     "family": p.meta.get("family"), "cohort": p.meta.get("cohort"), "age_h_at_entry": p.meta.get("age_h_at_entry"),
                                     "mae_pct": float(p.mae_pct), "mfe_pct": float(p.mfe_pct),
                                     **({"learning": dict(p.meta["learning"])} if p.meta.get("learning") else {})}
                             for s, p in self.ledger.positions.items()},
                   "history_tail": self.ledger.history_dicts()[-50:], "counters": dict(self.counters), "rejections": dict(self.rejections),
                   "plans_by_status": by_status, "findings_by_status": fstat, "n_plans": len(self.plans), "n_findings": len(self.findings),
                   "active_plans": [self._plan_brief(pl) for pl in self.plans.values() if pl.get("status") in (PL_AWAITING, PL_TRIGGERED, PL_RISK_CHECK, PL_OPENED, PL_MANAGED)][:200],
                   "recent_plans": sorted([self._plan_brief(pl) for pl in self.plans.values()], key=lambda d: -(d.get("created_at_ms") or 0))[:60],
                   "events_recent": self.events[-60:], "data_gaps": dict(self.data_gaps), "cooldown_until_ms": dict(self.cooldown_until),
                   "cohort_stats": self.cohort_stats, "symbol_scans": self.symbol_scans, "closed_recent": self.closed_recent[-30:],
                   "policy": {"protocol": self.protocol, "symbols": sorted(self.allowed_symbols) if self.allowed_symbols else None,
                              "families": list(self.families), "params": dict(self.params), "entry_tf": ENTRY_TF, "structure_tf": STRUCTURE_TF, "context_tf": CONTEXT_TF,
                              "requirements": dict(REQUIREMENTS), "max_open_positions": int(self.section.max_open_positions),
                              "max_hold_bars": int(self.section.max_hold_bars), "cooldown_bars_after_loss": int(self.section.cooldown_bars_after_loss),
                              "max_spread_pct": float(self.section.max_spread_pct), "min_depth_0_5pct_usdt": float(self.section.min_depth_0_5pct_usdt),
                              "cost_round_trip_frac": round(self.cost_frac, 6), "p_win": "ÖLÇÜLMEDİ", "risk_profile": self.profile.name,
                              "risk_per_trade_pct": float(self.profile.risk_per_trade_pct)},
                   "note_tr": "FORMASYON PAPER TRADER — gerçek para yok; ana bot/T2/M2 defterlerinden bağımsız sanal bakiye. Kârlılık kanıtı YOK; "
                              "planlar ölçülecek hipotezlerdir."}
            if self.learning_capable or self._lm_ever:  # ÖĞRENME MODU: yalnız yapılandırılmışsa (kapalıyken özet bit-aynı)
                doc["learning"] = self.learning_summary()
            atomic_write_json(Path(self.cfg.state_path) / self.summary_file, doc)

    @staticmethod
    def _plan_brief(pl: dict[str, Any]) -> dict[str, Any]:
        return {k: pl.get(k) for k in ("plan_id", "symbol", "family", "side", "status", "created_at", "created_at_ms", "expires_at", "trigger", "stop", "target",
                                        "target_source", "rr_after_cost", "cohort", "age_h_at_plan", "position_id", "reasons", "triggered_at_ms")}

    # ------------------------------------------------------------------ yardımcılar
    def _event(self, kind: str, symbol: str, reason: str, at: datetime, **extra: Any) -> None:
        self.events.append({"kind": kind, "symbol": symbol, "reason": reason, "at": iso(at), **extra})
        self.events = self.events[-MAX_EVENTS:]

    def _reject(self, symbol: str, reason: str) -> None:
        key = "data_rejected" if str(reason).startswith("DATA_") else "rejected"
        self.counters[key] += 1
        self.rejections[reason] = self.rejections.get(reason, 0) + 1

    _STATUS_TR = {PL_CANCELLED: "iptal", PL_BROKEN: "yapı bozuldu", PL_EXPIRED: "süresi doldu", PL_REJECTED: "işlem açılamadı",
                  PL_CLOSED: "işlem kapandı"}

    def _set_status(self, pl: dict[str, Any], status: str, at_ms: int, reason: str = "") -> None:
        pl["status"] = status
        pl.setdefault("status_history", []).append({"status": status, "at_ms": int(at_ms), "at": iso_ms(at_ms), "reason": reason})
        if reason:
            pl.setdefault("reasons", []).append(reason)
        # KARAR DEPOSU (bulgu #18): v2 planının sonucu panelin okuduğu depoya yazılır — reddedilen plan "girdi",
        # biten plan "bekliyor" görünmez. Açılış (ENTER + işlem kimliği) `_try_open`da yazılır.
        # Kardeş plan iptali (bir plan doldu → diğerleri) ve "sembolde pozisyon açık" iptali yazılmaz: sembolün son kararı
        # girişin kendisidir (tur-3 #3: önce kardeş iptali ENTER satırını eziyordu).
        _sibling = str(reason or "").startswith(("OTHER_PLAN_FILLED", "SAME_SYMBOL_POSITION_OPEN"))
        if pl.get("structure") and pl.get("version") == "pattern_protocol_v2.0.0" and status in self._STATUS_TR and not _sibling:
            closed = status == PL_CLOSED
            _an = (getattr(self, "_scan_analyses", None) or None) if (not closed and getattr(self, "_scan_symbol", None) == pl.get("symbol")) else None
            self._record_plan_decision(
                pl, _an, int(at_ms), action="EXIT" if closed else "CANCEL",
                trade_id=pl.get("position_id") if closed else None, reason_code="PLAN_%s:%s" % (status, reason or "-"),
                text_tr="%s: plan %s — %s" % ("Çıktı" if closed else "Girmedi", self._STATUS_TR[status], reason or "-"))

    def _state(self, marks_f: dict[str, float]):
        pos = [{"symbol": s, "market_type": "USDM_PERP", "side": p.side.value, "notional": float(p.qty * p.entry_avg), "margin": float(p.isolated_margin),
                "entry": float(p.entry_avg), "stop": float(p.stop) if p.stop else None, "leverage": p.leverage,
                "liq_price": float(p.liquidation_price) if p.liquidation_price else None, "opened_at": p.opened_at} for s, p in self.ledger.positions.items()]
        fs = self.ledger.summary(marks_f)
        return build_state(equity=float(fs["equity_mtm"]), starting_equity=float(self.ledger.starting_equity), available=float(fs["available"]),
                           used_margin=float(fs["used_margin"]), positions=pos, history=self.ledger.history_dicts(), high_water_mark=0.0, now=utc_now(),
                           clusters=self.cfg.v3.risk_profiles.clusters or None)

    # ------------------------------------------------------------------ funding (2026-09-17)
    def bind_funding(self, rates: Any) -> None:
        """Gerçekleşmiş settlement oran kaynağını deftere BAĞLAR: sözleşmenin kendi aralığı (`hours_for`) tahakkuk
        takvimine geçer ve bilinmeyen oran TAHMİNLE doldurulmaz (`fallback_to_last_known=False`) — bilinmeyen dönem
        BEKLER, sıfır maliyet olarak net performansa girmez."""
        self.funding_rates = rates
        self.ledger.funding.bind_source(rates)

    def pending_funding_symbols(self, window: int = 200) -> list[str]:
        """Açık pozisyonlar + funding kapsaması EKSİK kapanmış işlemlerin sembolleri (`refresh`in çekeceği liste)."""
        out = list(self.ledger.positions)
        for rec in self.ledger.history[-int(window):]:
            cov = (rec.features or {}).get("funding_coverage") if isinstance(rec.features, dict) else None
            if isinstance(cov, dict) and cov.get("complete") is False and rec.symbol not in out:
                out.append(rec.symbol)
        return out

    def reconcile_funding(self, now: datetime) -> list[dict]:
        """Kapanmış işlemlerin BEKLEYEN funding'ini, oranlar bellekte bulununca deftere işler (AĞ YOK; ağ adımı
        `FundingRates.refresh`, tarayıcı iş parçacığında). Defter + işlem kaydı + özet satırı + plan AYNI anda güncellenir;
        yinelenmezlik kayıttaki `funding_settled_ts`e dayanır (yeniden başlatmada korunur). Bkz.
        `FuturesLedgerV2.settle_late_funding`."""
        rates = getattr(self, "funding_rates", None)
        if rates is None:
            return []
        with self.lock:
            self._resume_once(now)
            # Kaynak NESNESİ verilir (oran + settlement mark'ı + dayanağı): açık pozisyon tahakkukuyla AYNI sözleşme.
            posted = self.ledger.settle_late_funding(rates, now=now, hours_for=getattr(rates, "hours_for", None))
            if not posted:
                return []
            touched = {p["trade_id"] for p in posted}
            by_id = {rec.id: rec for rec in self.ledger.history if rec.id in touched}
            for rid, rec in by_id.items():
                f = rec.features if isinstance(rec.features, dict) else {}
                try:
                    f["funding_coverage"] = self.funding_coverage(symbol=rec.symbol, opened_at=str(rec.opened_at), until=str(rec.closed_at),
                                                                  settled_until=f.get("funding_settled_until"),
                                                                  hours_utc=f.get("funding_hours_utc") or None,
                                                                  settled_ts=f.get("funding_settled_ts"))
                except Exception as exc:  # noqa: BLE001 — kapsama hesabı uzlaştırmayı ETKİLEMEZ
                    log.warning("formasyon defteri funding kapsaması yeniden yazılamadı: %s", exc)
                for row in self.closed_recent:
                    if row.get("id") == rid:
                        row.update({"net_pnl": float(rec.net_pnl), "r": float(rec.r_multiple), "funding_late": True,
                                    "funding_complete": (f.get("funding_coverage") or {}).get("complete")})
                pid = f.get("plan_id")
                pl = self.plans.get(str(pid)) if pid else None
                if pl is not None:
                    pl["net_pnl"], pl["net_r"] = float(rec.net_pnl), float(rec.r_multiple)
            for p in posted:
                self._event("FUNDING_LATE_POSTED", p["symbol"], "SETTLEMENT_" + p["settlement"], now,
                            trade_id=p["trade_id"], amount=p["amount"], rate=p["rate"])
            return posted

    def funding_coverage(self, *, symbol: str, opened_at: str, until: str, settled_until: str | None,
                         hours_utc: Any = None, settled_ts: Any = None) -> dict[str, Any]:
        """Bir pozisyon/işlem için funding KAPSAMASI. AĞ ÇAĞRISI YOKTUR: settlement saatleri ve gerçekten
        uygulanan settlement anları defterin tick'inde KAYDA geçirilmiştir (`features.funding_hours_utc`,
        `features.funding_settled_ts`); burada yalnız okunur. (Karşıt doğrulama bulgusu: eskiden `hours_for`
        çağrılıyordu ve bu, kapanış yolunda kilit altında bir HTTP isteğine dönüşebiliyordu.)

        `settled` GERÇEKTEN uygulanan dönemlerden sayılır; yoksa watermark'tan türetilir (eski kayıtlar için).
        `complete=False` ise o işlemin funding maliyeti ÖLÇÜLMEMİŞTİR — sıfır sanılmamalıdır."""
        from ..accounting.funding import funding_coverage
        hours = tuple(hours_utc) if hours_utc is not None else self.ledger.funding.hours_for(symbol)
        return funding_coverage(opened_at=opened_at, until=until, settled_until=settled_until, hours_utc=hours,
                                settled_ts=settled_ts,
                                rate_source="lookup" if getattr(self, "funding_rates", None) is not None else "none")

    def _on_closed(self, rec) -> None:
        self.counters["closed"] += 1
        # FUNDING KAPSAMASI kaydın KENDİ `features`ına yazılır (rapor bunu okur; uydurma maliyet EKLENMEZ).
        try:
            f = getattr(rec, "features", None)
            if isinstance(f, dict):
                f["funding_coverage"] = self.funding_coverage(symbol=str(getattr(rec, "symbol", "") or ""),
                                                              opened_at=str(getattr(rec, "opened_at", "") or ""),
                                                              until=str(getattr(rec, "closed_at", "") or ""),
                                                              settled_until=f.get("funding_settled_until"),
                                                              hours_utc=f.get("funding_hours_utc"),
                                                              settled_ts=f.get("funding_settled_ts"))
        except Exception as exc:  # noqa: BLE001 — kapsama hesabı kapanışı ETKİLEMEZ
            log.warning("formasyon defteri funding kapsaması yazılamadı: %s", exc)
        d = rec.to_legacy_dict() if hasattr(rec, "to_legacy_dict") else {}
        meta = getattr(rec, "meta", None) or {}
        pid = (meta.get("plan_id") if isinstance(meta, dict) else None) or (d.get("features") or {}).get("plan_id")
        self.closed_recent.append({"id": getattr(rec, "id", None), "symbol": getattr(rec, "symbol", None), "exit_reason": getattr(rec, "exit_reason", None),
                                   "net_pnl": float(getattr(rec, "net_pnl", 0) or 0), "r": float(getattr(rec, "r_multiple", 0) or 0),
                                   "closed_at": getattr(rec, "closed_at", None), "plan_id": pid, "family": (d.get("features") or {}).get("family"),
                                   "cohort": (d.get("features") or {}).get("cohort"),
                                   "path_unverified": bool((d.get("features") or {}).get("path_unverified")),
                                   "exit_basis": ((d.get("features") or {}).get("exit_fill") or {}).get("basis"),
                                   "funding_complete": ((d.get("features") or {}).get("funding_coverage") or {}).get("complete")})
        self.closed_recent = self.closed_recent[-50:]
        pl = self.plans.get(str(pid)) if pid else None
        closed_ms = int(utc_now().timestamp() * 1000)
        try:
            from ..core import from_iso
            closed_ms = int(from_iso(str(getattr(rec, "closed_at", "") or "")).timestamp() * 1000)
        except Exception:  # noqa: BLE001
            pass
        if pl is not None:
            self._set_status(pl, PL_CLOSED, closed_ms, "EXIT_%s" % str(getattr(rec, "exit_reason", "") or "").upper())
            pl["net_r"] = float(getattr(rec, "r_multiple", 0) or 0)
            pl["net_pnl"] = float(getattr(rec, "net_pnl", 0) or 0)
        if float(getattr(rec, "net_pnl", 0) or 0) < 0:
            sym = str(getattr(rec, "symbol", "") or "")
            if self.learning_on:
                # D5 (2026-09-28, öğrenme modu): öğrenmede soğuma KURULMAZ (laboratuvarda yoktu; tersleme/martingale yok)
                self.learning_counters["cooldown_skipped"] += 1
            else:
                self.cooldown_until[sym] = closed_ms + int(self.section.cooldown_bars_after_loss) * tf_ms(ENTRY_TF)
        try:
            self.memory.record_exit(str(getattr(rec, "id", "")), {"exit_reason": getattr(rec, "exit_reason", None), "net_pnl": float(getattr(rec, "net_pnl", 0) or 0),
                                                                  "r_multiple": float(getattr(rec, "r_multiple", 0) or 0), "closed_at": getattr(rec, "closed_at", None)})
        except Exception as exc:  # noqa: BLE001 — bellek arızası işlemi ETKİLEMEZ
            log.warning("formasyon defteri çıkış kaydı yazılamadı: %s", exc)

    # ------------------------------------------------------------------ tarama: bulgu → plan → tetik → giriş
    def process_symbol(self, symbol: str, *, bars_by_tf: dict[str, list[dict[str, Any]]], statuses: dict[str, dict[str, Any]], as_of_ms: int,
                       universe_entry: dict[str, Any] | None, price: dict[str, Any] | None, liquidity: Callable[[], dict[str, Any] | None] | None,
                       run_id: str, decision_ms: int | None = None) -> dict[str, Any]:
        """Bir sembolün kapalı barlarıyla tek tarama adımı. `price`: `verified_price` sözlüğü (ok/mark/price_ts_ms/...) ya da None;
        `liquidity()`: yalnız TETİK anında çağrılır (spread/derinlik). Döner: özet (bulgu/plan/tetik/giriş sayıları, nedenler).

        `decision_ms` (2026-09-17): GEÇERLİLİK SÜRESİ bu anla denetlenir. `as_of_ms` turun REFERANS anıdır ve bar
        kapanışı/güncellik hükümlerinde kullanılır (determinizm); uzun bir turda o an sembol sırası geldiğinde
        dakikalarca ESKİ olabilir. Süre kontrolünde eski bir zaman kullanmak, süresi dolmuş planın açılmasına yol
        açar — bu yüzden tarayıcı buraya sembolün taranma ANINI verir. Verilmezse `as_of_ms`e düşülür."""
        with self.lock:
            self.run_id = str(run_id or self.run_id)
            self.counters["scans"] += 1
            now = datetime.fromtimestamp(int(as_of_ms) / 1000.0, tz=timezone.utc)
            self._resume_once(now)
            ue = universe_entry or {}
            cohort = str(ue.get("cohort") or "UNKNOWN")
            out: dict[str, Any] = {"symbol": symbol, "as_of_ms": int(as_of_ms), "new_findings": 0, "confirmed": 0, "new_plans": 0, "triggered": 0, "opened": 0,
                                   "rejected": [], "skipped": [], "readiness": {}}
            b15 = bars_by_tf.get(ENTRY_TF) or []
            b1h = bars_by_tf.get(STRUCTURE_TF) or []
            b4h = bars_by_tf.get(CONTEXT_TF) or []
            for tf, rows in ((ENTRY_TF, b15), (STRUCTURE_TF, b1h), (CONTEXT_TF, b4h)):
                out["readiness"][tf] = readiness(len(rows))
            cs = self.cohort_stats.setdefault(cohort, {"scans": 0, "bars_15m_scanned": 0, "findings": 0, "confirmed": 0, "plans": 0, "triggered": 0, "opened": 0})
            cs["scans"] += 1
            last_scan = self.symbol_scans.get(symbol) or {}
            prev_bar = int(last_scan.get("last_15m_bar_ts") or 0)
            new_bars = [b for b in b15 if int(b["timestamp"]) > prev_bar]
            cs["bars_15m_scanned"] += len(new_bars)
            # 1) bulgular: her dilimde son kapalı bar (idempotent: kimlik = sembol|dilim|şekil|bar)
            ds = {tf: {"source": (statuses.get(tf) or {}).get("source"), "last_close_ms": (statuses.get(tf) or {}).get("last_close_ms"),
                       "fetched_at_ms": (statuses.get(tf) or {}).get("fetched_at_ms"), "is_stale": (statuses.get(tf) or {}).get("is_stale")} for tf in bars_by_tf}
            atr1h = atr14(b1h) if len(b1h) >= REQUIREMENTS["atr"] else None
            levels_1h = levels_for(b1h, tf=STRUCTURE_TF, atr=atr1h) if len(b1h) >= REQUIREMENTS["levels"] else {"pivots": [], "zones": [], "reason": "NOT_ENOUGH_1H_BARS", "n": len(b1h)}
            atr4h = atr14(b4h) if len(b4h) >= REQUIREMENTS["atr"] else None
            trend_4h = detect_trend(b4h, atr=atr4h, cfg=self.candle_cfg) if len(b4h) >= REQUIREMENTS["trend"] else {"trend": "UNKNOWN", "reason": "NOT_ENOUGH_4H_BARS", "n_bars": len(b4h)}
            # ORTAK KATALOG (structures_v1): 15m/1h/4h analizi — piyasa kimliği her dilimin KENDİ provenansından.
            analyses: dict[str, Any] = {}
            analysis_failed = False
            if self.structure_mode != "OFF" or self.protocol == "momentum_4h_v3":
                try:
                    analyses = self._structure_analyses(symbol, bars_by_tf, statuses, ds, as_of_ms)
                except Exception as exc:  # noqa: BLE001 — yapı arızası çıkış/zaman stopunu ENGELLEMEZ (bulgu #16)
                    log.warning("formasyon defteri yapı analizi başarısız (%s): %s", symbol, exc)
                    analyses = {}
                    analysis_failed = True
                    out["skipped"].append({"reason": "STRUCTURE_ANALYSIS_ERROR:%s" % type(exc).__name__})
            self._scan_analyses = analyses              # giriş kaydı (ENTER) bu taramanın analizine bağlanır
            self._scan_symbol = symbol                  # (tur-3 #4) analiz YALNIZ bu sembolün kayıtlarına bağlanır
            if self.structure_mode == "ENFORCE":
                # v2 bulguları = katalog kayıtları; pozisyon açıkken de her taramada güncellenir (panel aynı kaydı okur)
                self._catalog_findings(symbol, analyses, cohort, ue, now, out, cs)
            for tf, rows in ((ENTRY_TF, b15), (STRUCTURE_TF, b1h), (CONTEXT_TF, b4h)):
                if self.structure_mode == "ENFORCE":
                    break                                  # v2: bulgular katalogdan (aşağıda), v1 dedektörü çalışmaz
                if len(rows) < REQUIREMENTS["shape"]:
                    continue
                # Ilk tarama (last_scan yok) => since=0: son MAX_BACKFILL_BARS kapanmis bar taranir. Bot yeni
                # basladiginda ya da coin evrene yeni girdiginde son bar DISINDAKI sekiller de gorulur.
                since = int((last_scan.get("last_bar_ts") or {}).get(tf) or 0)
                fresh = detect_findings(symbol, tf, rows, as_of_ms=as_of_ms, cfg=self.candle_cfg, data_source=ds.get(tf) or {},
                                        htf_trend=trend_4h if tf != CONTEXT_TF else {}, levels=levels_1h if tf == ENTRY_TF else None,
                                        since_ts=since)
                for f in fresh:
                    if f["finding_id"] not in self.findings:
                        f["cohort"], f["age_h"] = cohort, ue.get("age_h")
                        self.findings[f["finding_id"]] = f
                        out["new_findings"] += 1
                        self.counters["findings"] += 1
                        cs["findings"] += 1
                # mevcut bulguların durumu: sonraki kapalı barlarla
                for fid, f in list(self.findings.items()):
                    if f.get("symbol") != symbol or f.get("tf") != tf or f.get("status") in (ST_BROKEN, ST_EXPIRED, ST_CONFIRMED):
                        continue
                    upd = update_finding(f, rows, as_of_ms=as_of_ms, cfg=self.candle_cfg)
                    if upd.get("status") == ST_CONFIRMED and f.get("status") != ST_CONFIRMED:
                        out["confirmed"] += 1
                        self.counters["confirmed"] += 1
                        cs["confirmed"] += 1
                        self._event("FINDING_CONFIRMED", symbol, upd["shape"], now, tf=tf, finding_id=fid, confirmed_at=iso_ms(upd.get("confirmed_at_ms")))
                    self.findings[fid] = upd
            # 2) mevcut planlar: tetik/bozulma/süre (yalnız yeni kapalı 15m barlarla; idempotent)
            # KARAR ANI: turun referansı ile sembolün gerçek tarama anının SONRAKİSİ (geriye giden bir değer
            # geçerlilik penceresini GENİŞLETEMEZ).
            dec_ms = max(int(as_of_ms), int(decision_ms if decision_ms is not None else as_of_ms))
            triggered: list[dict[str, Any]] = []
            for pid, pl in list(self.plans.items()):
                if pl.get("symbol") != symbol or pl.get("status") not in (PL_AWAITING, PL_TRIGGERED):
                    continue
                if self.protocol == "momentum_4h_v3":
                    if pl.get("version") != "pattern_protocol_v3.0.0":
                        # protokol geçişi: eski (v1/v2) bekleyen plan açılmaz; açık pozisyonlar kendi kuralıyla sürer
                        self._set_status(pl, PL_CANCELLED, int(as_of_ms), "PROTOCOL_MOMENTUM_4H_V3")
                        self.counters["cancelled"] += 1
                        continue
                    if pl["status"] == PL_TRIGGERED:
                        triggered.append(pl)           # fiyat beklenen v3 planı: süre dolana kadar yeniden denenir
                    continue
                if self.structure_mode == "ENFORCE" and not (pl.get("version") == "pattern_protocol_v2.0.0" and pl.get("structure")):
                    # ENFORCE'ta giriş yalnız ortak kayıttan kurulan v2 planıyla olur; dağıtımdan (ya da mod geçişinden)
                    # kalan v1 planı yapı denetiminden geçmemiştir → İPTAL (tur-6 #3: önce ENFORCE'ta da dolabiliyordu).
                    self._set_status(pl, PL_CANCELLED, int(as_of_ms), "STRUCTURE_MODE_ENFORCE_V1_PLAN")
                    self.counters["cancelled"] += 1
                    continue
                if pl["status"] in (PL_AWAITING, PL_TRIGGERED) and pl.get("version") == "pattern_protocol_v2.0.0" and pl.get("structure"):
                    if self.structure_mode != "ENFORCE":
                        # v2 planı yalnız ENFORCE'ta kurulur ve yalnız orada kayıtla yönetilir. SHADOW/OFF'a geçişte (ya da
                        # geri almada) kalan açık v2 planı İPTAL: önce SHADOW/OFF'ta kayıt denetimi olmadan açılıyordu
                        # (tur-5 doğrulayıcı F3). Açık pozisyonlar defterin kendi stop/hedef/zaman stopuyla sürer.
                        self._set_status(pl, PL_CANCELLED, int(as_of_ms), "STRUCTURE_MODE_%s" % self.structure_mode)
                        self.counters["cancelled"] += 1
                        continue
                    # v2: plan ORTAK KAYDI izler (tetik/bozulma/süre olayı kayıttan; ayrı tetik değerlendirmesi YOK)
                    self._follow_record(pl, analyses, as_of_ms=int(as_of_ms), dec_ms=dec_ms, now=now, out=out, cs=cs,
                                        levels_1h=levels_1h)
                elif pl["status"] == PL_AWAITING:
                    last_eval = int(pl.get("last_evaluated_bar_ts") or 0)
                    # Plan KENDİ diliminin kapanışıyla değerlendirilir (v2: 1h yapı 1h kapanışıyla; v1 planları 15m)
                    ptf = str(pl.get("entry_tf") or ENTRY_TF)
                    pstep = tf_ms(ptf)
                    for b in (bars_by_tf.get(ptf) or []):
                        ts = int(b["timestamp"])
                        if ts <= last_eval or ts + pstep > int(as_of_ms):
                            continue
                        if self.is_expired(pl, ts + pstep):
                            self._set_status(pl, PL_EXPIRED, ts + pstep, "NOT_TRIGGERED_BEFORE_EXPIRY")
                            self.counters["expired"] += 1
                            break
                        res = evaluate_trigger(pl, b)
                        pl["last_evaluated_bar_ts"] = ts
                        if res == PL_TRIGGERED:
                            self._set_status(pl, PL_TRIGGERED, ts + pstep, "CLOSE_%s_%s" % (pl["trigger"]["rule"].upper(), pl["trigger"]["level"]))
                            pl["triggered_at_ms"], pl["trigger_bar_ts"], pl["trigger_close"] = ts + pstep, ts, float(b["close"])
                            self.counters["triggered"] += 1
                            cs["triggered"] += 1
                            out["triggered"] += 1
                            self._event("PLAN_TRIGGERED", symbol, pl["family"], now, plan_id=pid, side=pl["side"], bar_close=iso_ms(ts + pstep))
                            break
                        if res == PL_BROKEN:
                            self._set_status(pl, PL_BROKEN, ts + pstep, "CLOSE_BEYOND_INVALIDATION")
                            self.counters["broken"] += 1
                            break
                    if pl["status"] == PL_AWAITING and self.is_expired(pl, dec_ms):
                        self._set_status(pl, PL_EXPIRED, dec_ms, "EXPIRED_AT_SCAN")
                        self.counters["expired"] += 1
                if pl.get("status") == PL_TRIGGERED:
                    triggered.append(pl)
            # 3) tetiklenen planlar → risk kontrolü → giriş (sembol başına tek pozisyon; sıra: en erken tetik)
            # v2 planı YALNIZ kaydı bu taramada TEYİTLİ-TAZE ise açılır (tur-3 #5: analiz arızası/yokluğunda açılmaz)
            if self.structure_mode == "ENFORCE":
                triggered = [pl for pl in triggered if not (pl.get("version") == "pattern_protocol_v2.0.0" and pl.get("structure"))
                             or (not analysis_failed and self._record_confirmed_now(pl, analyses))]
            for pl in sorted(triggered, key=lambda d: int(d.get("triggered_at_ms") or 0)):
                res = self._try_open(pl, now=now, as_of_ms=as_of_ms, price=price, statuses=statuses, liquidity=liquidity,
                                     universe_entry=ue, decision_ms=dec_ms)
                if res == "OPENED":
                    out["opened"] += 1
                    cs["opened"] += 1
                elif res == "REJECTED":
                    out["rejected"].append({"plan_id": pl["plan_id"], "reason": (pl.get("reasons") or ["?"])[-1]})
            # 4) zaman aşımı: azami tutma süresi dolan pozisyon (doğrulanmış güncel fiyat varsa) kapatılır
            self._time_stop(symbol, now=now, as_of_ms=as_of_ms, price=price)
            # 5) yeni planlar: pozisyon yoksa, soğuma yoksa, evren uygunsa
            blocked = self._entry_block_reason(symbol, ue, as_of_ms)
            # S9 (2026-09-28, öğrenme modu): öğrenmede giriş evreni = protokol coinleri ∪ ana botun giriş evreni;
            # kapalıyken `entry_symbols()` protokol evreninin KENDİSİDİR (bit-aynı)
            _allowed = self.entry_symbols()
            if not blocked and _allowed is not None and symbol not in _allowed:
                blocked = "NOT_IN_PROTOCOL_UNIVERSE"
            if blocked:
                out["skipped"].append({"reason": blocked})
                if blocked == "POSITION_OPEN" and self.learning_on and not analysis_failed:
                    self._cf_signals_while_open(symbol, as_of_ms=int(as_of_ms), dec_ms=dec_ms, analyses=analyses,
                                                bars_by_tf=bars_by_tf, ue=ue, ds=ds, price=price)
            else:
                if self.protocol == "momentum_4h_v3":
                    from ..structures.bots import used_patterns_of
                    from .strategy_v3 import build_plans_v3
                    plans, skipped = ([], [{"reason": "STRUCTURE_ANALYSIS_ERROR"}]) if analysis_failed else build_plans_v3(
                        symbol, as_of_ms=as_of_ms, analyses=analyses, bars_by_tf=bars_by_tf, universe_entry=ue, data_source=ds,
                        used_patterns=used_patterns_of(self.ledger))
                elif self.structure_mode == "ENFORCE":
                    from ..structures.bots import used_patterns_of
                    from .strategy_v2 import build_plans_v2
                    plans, skipped = build_plans_v2(symbol, as_of_ms=as_of_ms, analyses=analyses, bars_by_tf=bars_by_tf, levels_1h=levels_1h,
                                                    trend_4h=trend_4h, cost_frac=self.cost_frac, params=self.params, universe_entry=ue,
                                                    data_source=ds, used_patterns=used_patterns_of(self.ledger))
                else:
                    sym_findings = [f for f in self.findings.values() if f.get("symbol") == symbol]
                    plans, skipped = build_plans(symbol, as_of_ms=as_of_ms, bars_by_tf=bars_by_tf, findings=sym_findings, levels_1h=levels_1h, trend_4h=trend_4h,
                                                 cost_frac=self.cost_frac, params=self.params, universe_entry=ue, data_source=ds, enabled_families=self.families)
                    if self.structure_mode == "SHADOW" and analyses:
                        self._shadow_v2(symbol, analyses, bars_by_tf, levels_1h, trend_4h, ue, ds, as_of_ms)
                out["skipped"].extend(skipped)
                for pl in plans:
                    if pl["plan_id"] in self.plans:
                        continue
                    if any(q.get("symbol") == symbol and q.get("family") == pl["family"] and q.get("side") == pl["side"] and q.get("status") in (PL_AWAITING, PL_TRIGGERED)
                           for q in self.plans.values()):
                        continue                       # aynı aile/yönde bekleyen plan zaten var (yinelenen plan yok)
                    self.plans[pl["plan_id"]] = pl
                    if self.learning_on:                   # S9: laboratuvar evreni dışı plan etiketli (askıda iptal edilir)
                        pl["in_lab_universe"] = bool(self.allowed_symbols is None or symbol in self.allowed_symbols)
                    out["new_plans"] += 1
                    self.counters["plans"] += 1
                    cs["plans"] += 1
                    self._event("PLAN_CREATED", symbol, pl["family"], now, plan_id=pl["plan_id"], side=pl["side"], trigger=pl["trigger"]["level"], stop=pl["stop"], target=pl["target"])
                    if pl.get("structure"):
                        if pl.get("status") != PL_TRIGGERED:
                            self._record_plan_decision(pl, analyses, as_of_ms)
                        if pl.get("status") == PL_TRIGGERED:
                            # teyitli katalog kaydından doğan plan: teyit kapanışından sonraki İLK doğrulanmış fiyatla
                            # (bu tarama) risk/giriş denenir; kovalama ve R/R `_try_open`da yeniden ölçülür
                            self.counters["triggered"] += 1
                            cs["triggered"] += 1
                            out["triggered"] += 1
                            r = self._try_open(pl, now=now, as_of_ms=as_of_ms, price=price, statuses=statuses, liquidity=liquidity,
                                               universe_entry=ue, decision_ms=dec_ms)
                            if r == "OPENED":
                                out["opened"] += 1
                                cs["opened"] += 1
                            elif r == "REJECTED":
                                out["rejected"].append({"plan_id": pl["plan_id"], "reason": (pl.get("reasons") or ["?"])[-1]})
            self.symbol_scans[symbol] = {"last_scan_ms": int(as_of_ms), "last_15m_bar_ts": int(b15[-1]["timestamp"]) if b15 else prev_bar, "cohort": cohort,
                                         "last_bar_ts": {tf: int(rows[-1]["timestamp"]) for tf, rows in ((ENTRY_TF, b15), (STRUCTURE_TF, b1h), (CONTEXT_TF, b4h)) if rows},
                                         "n_15m": len(b15), "n_1h": len(b1h), "n_4h": len(b4h), "trend_4h": trend_4h.get("trend"), "n_zones_1h": len(levels_1h.get("zones") or [])}
            if len(self.symbol_scans) > 2000:
                for k in sorted(self.symbol_scans, key=lambda s: self.symbol_scans[s].get("last_scan_ms") or 0)[:200]:
                    self.symbol_scans.pop(k, None)
            out["levels_1h"] = {"n_zones": len(levels_1h.get("zones") or []), "reason": levels_1h.get("reason")}
            out["trend_4h"] = trend_4h.get("trend")
            if self.cf is not None:                        # öğrenme karşı-olgusalı: eldeki kapalı barlarla (ağ YOK)
                self.label_counterfactuals(symbol, bars_by_tf, now)
            return out

    # ------------------------------------------------------------------ ORTAK KATALOG (structures_v1)
    def _structure_analyses(self, symbol: str, bars_by_tf: dict[str, list], statuses: dict[str, dict], ds: dict[str, dict],
                            as_of_ms: int) -> dict[str, Any]:
        from ..structures import analyze
        out: dict[str, Any] = {}
        for tf in (ENTRY_TF, STRUCTURE_TF, CONTEXT_TF):
            rows = bars_by_tf.get(tf) or []
            if not rows:
                out[tf] = None
                continue
            mk = str((statuses.get(tf) or {}).get("market") or "UNVERIFIED")
            out[tf] = analyze(market=mk, symbol=symbol, timeframe=tf, bars=rows, as_of_ms=int(as_of_ms),
                              data_provenance=dict(ds.get(tf) or {}, market=mk))
        try:
            from ..structures.store import StructureStore
            if self._structure_store is None:
                self._structure_store = StructureStore(self.cfg.state_path)
            for an in out.values():
                if an:
                    self._structure_store.save_latest(an)
        except Exception as exc:  # noqa: BLE001
            log.warning("formasyon defteri analiz kaydı yazılamadı: %s", exc)
        return out

    def _catalog_findings(self, symbol: str, analyses: dict[str, Any], cohort: str, ue: dict[str, Any], now: datetime,
                          out: dict[str, Any], cs: dict[str, int]) -> None:
        """v2 bulguları = ortak katalog kayıtları (15m/1h/4h). Panel ve sayaçlar aynı kaydı okur."""
        from ..structures.catalog import POLICY_VERSION as SK_POLICY
        for tf, an in (analyses or {}).items():
            for r in (an or {}).get("records") or []:
                fid = r["pattern_id"]
                prev = self.findings.get(fid)
                row = {"version": SK_POLICY, "finding_id": fid, "pattern_id": fid, "symbol": symbol, "market": r.get("market"),
                       "tf": tf, "shape": r.get("name"), "family": r.get("family"), "side": r.get("side"), "status": r.get("status"),
                       "bar_ts": int(r["anchors"][-1]["ts"]) if r.get("anchors") else None, "seen_at_ms": r.get("detected_at_ms"),
                       "confirmed_at_ms": r.get("confirmed_at_ms"), "broken_at_ms": r.get("broken_at_ms"),
                       "trigger": r.get("trigger"), "invalidation": r.get("invalidation"), "analysis_id": r.get("analysis_id"),
                       "cohort": cohort, "age_h": ue.get("age_h")}
                if prev is None:
                    out["new_findings"] += 1
                    self.counters["findings"] += 1
                    cs["findings"] += 1
                if r.get("status") == "CONFIRMED" and (prev or {}).get("status") != "CONFIRMED":
                    out["confirmed"] += 1
                    self.counters["confirmed"] += 1
                    cs["confirmed"] += 1
                    self._event("FINDING_CONFIRMED", symbol, str(r.get("name")), now, tf=tf, finding_id=fid,
                                confirmed_at=iso_ms(r.get("confirmed_at_ms")))
                self.findings[fid] = row
        if len(self.findings) > 5000:
            for k in sorted(self.findings, key=lambda x: int(self.findings[x].get("seen_at_ms") or 0))[:1000]:
                self.findings.pop(k, None)

    def _record_plan_decision(self, pl: dict[str, Any], analyses: dict[str, Any] | None, at_ms: int, *, action: str | None = None,
                              trade_id: str | None = None, shadow: bool = False, reason_code: str | None = None,
                              text_tr: str | None = None) -> None:
        try:
            from ..structures.store import StructureStore
            if self._structure_store is None:
                self._structure_store = StructureStore(self.cfg.state_path)
            act = action or ("ENTER" if pl.get("status") == PL_TRIGGERED else "WAIT_TRIGGER")
            st = dict(pl.get("structure") or {})
            # Kaydın O ANKİ durumu (örn. girişte TEYİTLİ): plan oluşurkenki durum eski kalır; taramanın analizi varsa
            # aynı kimlikli kayıttan durum/teyit/analiz kimliği güncellenir (seviye/stop/hedef PLANDAN — değişmez).
            _tfa = (analyses or {}).get(str(pl.get("entry_tf") or "")) or {}
            _cur = next((r for r in (_tfa.get("records") or []) if r.get("pattern_id") == st.get("pattern_id")), None)
            if _cur is not None:
                st.update(status=_cur.get("status"), confirmed_at_ms=_cur.get("confirmed_at_ms"),
                          analysis_id=_cur.get("analysis_id"))
            dec = {"bot": "pattern_trader", "policy_version": st.get("policy_version"), "action": act,
                   "reason_code": reason_code or ("PLAN_" + str(pl.get("family"))), "side": pl.get("side"), "decision_tf": pl.get("entry_tf"),
                   "as_of_ms": int(at_ms), "pattern_ids": [st.get("pattern_id")] if st.get("pattern_id") else [],
                   "primary": dict(st, **(pl.get("structure_geometry") or {}), trigger=pl.get("trigger"),
                                   invalidation=pl.get("invalidation"), stop=pl.get("stop"), targets=[pl.get("target")],
                                   side=pl.get("side")),
                   "plan": {"plan_id": pl.get("plan_id"), "trigger": pl.get("trigger"), "invalidation": pl.get("invalidation"),
                            "stop": pl.get("stop"), "targets": [pl.get("target")], "expires_at_ms": pl.get("expires_at_ms"),
                            "timeframe": pl.get("entry_tf")},
                   "analysis_ids": {tf: (a or {}).get("analysis_id") for tf, a in (analyses or {}).items()},
                   "text_tr": text_tr or (("Girdi: " if act == "ENTER" else "Bekliyor: ") + "%s — %s" % (
                       pl.get("family_title_tr") or pl.get("family"), (pl.get("trigger") or {}).get("text_tr"))),
                   "mode": "SHADOW" if shadow else self.structure_mode}
            self._structure_store.record_decision(dec, book_id=BOOK_KEY, market="USDM_PERP", symbol=str(pl.get("symbol")),
                                                  at_ms=int(at_ms), analyses=analyses, trade_id=trade_id)
        except Exception as exc:  # noqa: BLE001 — kayıt arızası planı ETKİLEMEZ
            log.warning("formasyon planı yapı kararı yazılamadı: %s", exc)

    def _shadow_v2(self, symbol, analyses, bars_by_tf, levels_1h, trend_4h, ue, ds, as_of_ms) -> None:
        from .strategy_v2 import build_plans_v2
        plans, _ = build_plans_v2(symbol, as_of_ms=as_of_ms, analyses=analyses, bars_by_tf=bars_by_tf, levels_1h=levels_1h,
                                  trend_4h=trend_4h, cost_frac=self.cost_frac, params=self.params, universe_entry=ue, data_source=ds)
        for pl in plans[:5]:
            self._record_plan_decision(pl, analyses, as_of_ms, shadow=True)

    @staticmethod
    def _record_confirmed_now(pl: dict[str, Any], analyses: dict[str, Any] | None) -> bool:
        an = (analyses or {}).get(str(pl.get("entry_tf") or ENTRY_TF)) or {}
        if str(an.get("market") or "") != PAPER_MARKET:
            return False
        pid = str(pl.get("pattern_id") or "")
        return any(r.get("pattern_id") == pid and r.get("status") == "CONFIRMED" for r in (an.get("records") or []))

    def _follow_record(self, pl: dict[str, Any], analyses: dict[str, Any] | None, *, as_of_ms: int, dec_ms: int, now: datetime,
                       out: dict[str, Any], cs: dict[str, int], levels_1h: dict[str, Any] | None) -> None:
        """v2 bekleyen plan → ortak kaydın O ANKİ durumu. Oluşuyorsa seviyeler kayıttan yenilenir (gelişen yapı);
        teyit → TETİKLENDİ (teyit kapanışı anı, geriye yazılmaz); bozuldu/süresi doldu → aynı durum; kayıt analizden
        çekildiyse → İPTAL. Analiz yoksa (veri boşluğu) plan bekler; kendi süresi dolarsa SÜRESİ DOLDU."""
        from ..structures import catalog as SK
        from .strategy import DEFAULTS as DEFAULTS_PT
        from .strategy_v2 import plan_levels_from_record
        tf = str(pl.get("entry_tf") or ENTRY_TF)
        an = (analyses or {}).get(tf)
        pid = str(pl.get("pattern_id") or (pl.get("structure") or {}).get("pattern_id") or "")
        # PİYASA KİMLİĞİ (bulgu #11): planın KENDİ dilimi doğrulanmış perp çerçeveden değilse analiz YOK sayılır
        if not an or not isinstance(an.get("records"), list) or str(an.get("market") or "") != PAPER_MARKET:
            if self.is_expired(pl, dec_ms):
                self._set_status(pl, PL_EXPIRED, dec_ms, "EXPIRED_AT_SCAN_NO_ANALYSIS")
                self.counters["expired"] += 1
            return
        rec = next((r for r in an["records"] if r.get("pattern_id") == pid), None)
        if rec is None and pl["status"] == PL_TRIGGERED:
            # teyit gerçekleşti; kayıt görünmüyorsa giriş denenmez (adım 3 süzgeci). Planın KENDİ geçerlilik sınırı BURADA
            # işler: giriş yolu (`_try_open`) bu plana hiç ulaşmadığı için süre orada dolamaz — önce plan hiç bitmiyor ve
            # aynı aile/yöndeki yeni planları engelliyordu (tur-4 doğrulayıcı #1).
            if self.is_expired(pl, dec_ms):
                self._set_status(pl, PL_EXPIRED, dec_ms, "EXPIRED_AT_SCAN_RECORD_MISSING")
                self.counters["expired"] += 1
            return
        if rec is None:
            self._set_status(pl, PL_CANCELLED, int(as_of_ms), "RECORD_WITHDRAWN")
            pl["cancel_detail"] = {"analysis_id": an.get("analysis_id"), "note": "kayıt analizde yok: yapı tanımı artık sağlanmıyor"}
            self.counters["cancelled"] += 1
            return
        st = rec.get("status")
        if pl["status"] == PL_TRIGGERED:
            # tetiklenmiş, fiyat/giriş bekleyen plan (bulgu #10): kayıt teyitli-taze kaldıkça bekler; bozulur ya da
            # bayatlarsa plan da biter (giriş bayat ya da bozulmuş yapıyla AÇILMAZ)
            if st == SK.ST_BROKEN:
                self._set_status(pl, PL_BROKEN, int(rec.get("broken_at_ms") or as_of_ms), "RECORD_BROKEN")
                self.counters["broken"] += 1
            elif st == SK.ST_EXPIRED:
                self._set_status(pl, PL_EXPIRED, int(rec.get("expired_at_ms") or as_of_ms), "RECORD_EXPIRED")
                self.counters["expired"] += 1
            return
        if st in (SK.ST_FORMING, SK.ST_CONFIRMED):
            lv, why = plan_levels_from_record(rec, side=str(pl["side"]), zones=list((levels_1h or {}).get("zones") or []),
                                              cost_frac=self.cost_frac, p={**DEFAULTS_PT, **(self.params or {})}, atr_fallback=pl.get("atr"))
            if lv is None:
                self._set_status(pl, PL_CANCELLED, int(as_of_ms), "RECORD_REVISION_%s" % why)
                self.counters["cancelled"] += 1
                return
            keys = ("trigger", "invalidation", "stop", "target")
            if any(pl.get(k) != lv.get(k) for k in keys):
                pl["record_revisions"] = int(pl.get("record_revisions") or 0) + 1
                pl.setdefault("revision_history", []).append({"at_ms": int(as_of_ms), "status": st, **{k: pl.get(k) for k in keys}})
                pl["revision_history"] = pl["revision_history"][-5:]
            pl.update({k: lv[k] for k in ("trigger", "invalidation", "stop", "atr", "target", "target_source", "rr_gross",
                                          "rr_after_cost", "structure_geometry")})
            # ad/aile de KAYITTAN (tur-7: bayrak/flama kimliği aynı kalırken adı teyide kadar değişebilir; işlem kaydı ve
            # panel teyit anındaki adı taşımalı)
            pl["structure"] = dict(pl.get("structure") or {}, status=st, confirmed_at_ms=rec.get("confirmed_at_ms"),
                                   analysis_id=rec.get("analysis_id"), side=str(pl["side"]), trigger=dict(lv["trigger"]),
                                   name=rec.get("name"), family=rec.get("family"))
            if rec.get("expires_at_ms"):
                pl["expires_at_ms"] = int(rec["expires_at_ms"])
                pl["expires_at"] = iso_ms(pl["expires_at_ms"])
            if st == SK.ST_CONFIRMED:
                from ..structures.bots import used_patterns_of
                from ..structures.policy import already_used
                if already_used(rec, used_patterns_of(self.ledger)):
                    # kayıt, bu defterde girişe dayanak olmuş bir kırılımın kardeş yorumu olarak teyit oldu (aynı seviye,
                    # taze pencere): aynı kırılım ikinci işlem açmaz (tur-5 doğrulayıcı F2)
                    self._set_status(pl, PL_CANCELLED, int(as_of_ms), "SAME_BREAK_ALREADY_USED")
                    pl["cancel_detail"] = {"pattern_id": rec.get("pattern_id"), "confirmed_at_ms": rec.get("confirmed_at_ms")}
                    self.counters["cancelled"] += 1
                    return
                cb = rec.get("confirm_bar") or {}
                t_ms = int(rec.get("confirmed_at_ms") or as_of_ms)
                self._set_status(pl, PL_TRIGGERED, t_ms, "RECORD_CONFIRMED_%s" % rec.get("name"))
                pl["triggered_at_ms"], pl["trigger_bar_ts"], pl["trigger_close"] = t_ms, cb.get("ts"), cb.get("close")
                self.counters["triggered"] += 1
                cs["triggered"] += 1
                out["triggered"] += 1
                self._event("PLAN_TRIGGERED", str(pl.get("symbol")), pl["family"], now, plan_id=pl["plan_id"], side=pl["side"],
                            bar_close=iso_ms(t_ms), source="record")
            elif self.is_expired(pl, dec_ms):
                self._set_status(pl, PL_EXPIRED, dec_ms, "EXPIRED_AT_SCAN")
                self.counters["expired"] += 1
            return
        if st == SK.ST_BROKEN:
            self._set_status(pl, PL_BROKEN, int(rec.get("broken_at_ms") or as_of_ms), "RECORD_BROKEN")
            self.counters["broken"] += 1
            return
        self._set_status(pl, PL_EXPIRED, int(rec.get("expired_at_ms") or as_of_ms), "RECORD_EXPIRED")
        self.counters["expired"] += 1

    def _entry_block_reason(self, symbol: str, ue: dict[str, Any], as_of_ms: int) -> str | None:
        if symbol in self.ledger.positions:
            return "POSITION_OPEN"
        if int(self.cooldown_until.get(symbol) or 0) > int(as_of_ms) and not self.learning_on:   # D5: öğrenmede soğuma YOK
            return "COOLDOWN_AFTER_LOSS"
        if not ue:
            return "NOT_IN_UNIVERSE"
        if not ue.get("eligible"):
            return "UNIVERSE_%s" % (ue.get("reason") or "INELIGIBLE")
        if str(ue.get("status")) != "TRADING":
            return "STATUS_%s" % ue.get("status")
        return None

    @staticmethod
    def is_expired(pl: dict[str, Any], as_of_ms: int) -> bool:
        """GEÇERLİLİK SINIRI — TEK tanım (2026-09-17): plan `as_of_ms > expires_at_ms` ise geçersizdir; sınır anı
        (`as_of_ms == expires_at_ms`) HÂLÂ geçerlidir. Tetik döngüsü, AWAITING süpürmesi ve giriş yolu AYNI
        fonksiyonu çağırır; karar anı olarak her biri kendi gerçek anını verir.

        FAIL-CLOSED: `expires_at_ms` yoksa ya da çözülemiyorsa plan GEÇERSİZ sayılır (True). Okunamayan bir
        geçerlilik alanını "hiç dolmaz" saymak, bozuk/eski bir `plans.json` kaydına süresiz emir hakkı verirdi."""
        raw = pl.get("expires_at_ms") if isinstance(pl, dict) else None
        # Alan adı `_ms`tir: sayısal değer DOĞRUDAN milisaniyedir. `parse_ts_ms`in saniye/ms sezgisi burada
        # UYGULANMAZ (küçük bir sayıyı saniye sanıp geçerliliği 1000 kat uzatabilirdi); metin (ISO) değer için
        # eski kayıtlara karşı geri düşüş olarak kalır.
        if isinstance(raw, bool) or raw is None:
            v = None
        elif isinstance(raw, (int, float)):
            v = int(raw) if (raw == raw and raw > 0) else None
        else:
            v = parse_ts_ms(raw)
        if v is None:
            return True
        return int(as_of_ms) > v

    @staticmethod
    def _filters_evidence(f, origin: str, age_s: float | None = None) -> dict[str, Any]:
        """İşlem kanıtına giren filtre dökümü: kaynak, doğrulama zamanı ve GERÇEKTEN kullanılan değerler."""
        return {"source": f.source, "verified_at": f.verified_at, "origin": origin, "age_s": age_s,
                "price_tick": str(f.price_tick), "qty_step": str(f.qty_step), "min_qty": str(f.min_qty),
                "market_max_qty": str(f.market_max_qty), "min_notional": str(f.min_notional),
                "max_leverage": int(f.max_leverage), "contract_type": str(getattr(f, "contract_type", "") or "")}

    def _resolve_filters(self, symbol: str, universe_entry: dict[str, Any], as_of_ms: int):
        """GİRİŞTE KULLANILACAK RESMÎ EMİR KURALLARI — keşiften girişe AYNI sembol/piyasa için doğrulanmış filtre.

        Sıra: (1) motorun paylaşılan önbelleğinde doğrulanmış VE GÜNCEL kayıt varsa o; (2) yoksa bu turun KENDİ
        `exchangeInfo` keşif kaydından (`universe_entry["filters"]`) STRICT üretilir — iki yenileme arasında
        listelenen sözleşme böylece varsayılana düşmez; (3) ikisi de yoksa `None` döner ve giriş REDDEDİLİR.

        YAŞ HER İKİ KAYNAKTA da denetlenir (`FILTERS_MAX_AGE_MS`) ve zaman damgası ÇÖZÜLEMİYORSA kayıt kabul
        EDİLMEZ (fail-closed): "bayatlık bilinmiyor" ile "bayat değil" aynı şey değildir.

        PAYLAŞILAN ÖNBELLEĞE YAZILMAZ (2026-09-17, karşıt doğrulama bulgusu): `filters_cache` motorun ANA BOT ile
        paylaştığı nesnedir ve `FiltersCache.put()` önbellek geneli `verified_at` damgasını yeniler — bu da ana
        botun resmî `exchangeInfo` yenilemesini "taze" sanıp atlatır (`engine_v3.ensure_symbol_filters`). Ayrıca
        tarayıcı AYRI bir iş parçacığıdır ve `FiltersCache` kilitsizdir. Bu yüzden tur içi tekrarı önlemek için
        defterin KENDİ yerel belleği kullanılır. Döner: (filters | None, gerekçe, kanıt sözlüğü)."""
        from ..accounting.filters import from_universe_entry
        if self.filters_cache.has(symbol, MarketType.USDM_PERP):
            f = self.filters_cache.get(symbol, MarketType.USDM_PERP)
            if str(getattr(f, "source", "") or "") not in ("", "default"):
                ts = parse_ts_ms(getattr(f, "verified_at", None))
                if ts is None:
                    return None, "FILTERS_UNDATED", {"origin": "filters_cache", "verified_at": str(getattr(f, "verified_at", ""))[:40]}
                age = int(as_of_ms) - int(ts)
                if age <= FILTERS_MAX_AGE_MS:
                    return f, "", self._filters_evidence(f, "filters_cache", round(age / 1000.0, 1))
        hit = self._filters_memo.get(symbol)           # tur içi tekrar: AYNI keşif kaydından ikinci ayrıştırma yok
        if hit is not None and 0 <= int(as_of_ms) - hit[0] <= FILTERS_MAX_AGE_MS:
            return hit[1], "", self._filters_evidence(hit[1], "book_memo", round((int(as_of_ms) - hit[0]) / 1000.0, 1))
        seen = parse_ts_ms(universe_entry.get("as_of_ms")) or parse_ts_ms(universe_entry.get("last_seen_at"))
        if seen is None:                               # FAIL-CLOSED: keşif anı bilinmiyorsa bayatlık DENETLENEMEZ
            return None, "FILTERS_UNDATED", {"origin": "universe_entry",
                                             "detail": "keşif kaydında as_of_ms/last_seen_at yok ya da çözülemedi"}
        if int(as_of_ms) - seen > FILTERS_MAX_AGE_MS:
            return None, "FILTERS_STALE", {"discovered_ms": seen, "age_s": round((int(as_of_ms) - seen) / 1000.0, 1),
                                           "max_age_s": round(FILTERS_MAX_AGE_MS / 1000.0, 1)}
        try:
            f = from_universe_entry(symbol, universe_entry, MarketType.USDM_PERP)
        except (ValueError, ArithmeticError, TypeError) as exc:
            return None, "FILTERS_UNVERIFIED", {"detail": str(exc)[:160]}
        self._filters_memo[symbol] = (int(seen), f)
        if len(self._filters_memo) > 4000:
            for k in list(self._filters_memo)[:500]:
                self._filters_memo.pop(k, None)
        return f, "", self._filters_evidence(f, "universe_entry", round((int(as_of_ms) - seen) / 1000.0, 1))

    def _try_open(self, pl: dict[str, Any], *, now: datetime, as_of_ms: int, price: dict[str, Any] | None, statuses: dict[str, dict[str, Any]],
                  liquidity: Callable[[], dict[str, Any] | None] | None, universe_entry: dict[str, Any],
                  decision_ms: int | None = None) -> str:
        symbol = pl["symbol"]
        decision_ms = max(int(as_of_ms), int(decision_ms if decision_ms is not None else as_of_ms))
        if pl.get("status") in TERMINAL:
            return str(pl.get("status"))               # ZATEN terminal (örn. kardeş plan doldu → CANCELLED): dokunma
        # ÖĞRENME MODU (2026-09-28, öğrenme modu): bu turun görünümü; None → aşağıdaki her satır bugünkü gibi
        lm = self.learning if self.learning_on else None
        cf_at = datetime.fromtimestamp(decision_ms / 1000.0, tz=timezone.utc)
        self._set_status(pl, PL_RISK_CHECK, int(as_of_ms), "")

        def _reject(reason: str, **extra: Any) -> str:
            self._set_status(pl, PL_REJECTED, int(as_of_ms), reason)
            pl["reject_detail"] = extra
            self._reject(symbol, reason)
            self._event("PLAN_REJECTED", symbol, reason, now, plan_id=pl["plan_id"], **{k: v for k, v in extra.items() if isinstance(v, (int, float, str))})
            return "REJECTED"

        # GEÇERLİLİK SÜRESİ — HER giriş yolunda, gerçekleşmeden ÖNCE (fiyat/likidite beklemesinden dönüş DAHİL).
        # Süresi dolan plan TERMİNAL duruma geçer: sonraki taramada ya da yeniden başlatmada DİRİLMEZ.
        if self.is_expired(pl, decision_ms):
            self._set_status(pl, PL_EXPIRED, decision_ms, "EXPIRED_BEFORE_ENTRY")
            self.counters["expired"] += 1
            self._event("PLAN_EXPIRED", symbol, "EXPIRED_BEFORE_ENTRY", now, plan_id=pl["plan_id"],
                        expires_at=iso_ms(int(pl["expires_at_ms"])), decided_at=iso_ms(decision_ms), as_of=iso_ms(int(as_of_ms)))
            if lm is not None:                         # D16: pencere sonunda hâlâ likidite yoksa işlem YOK + karşı-olgusal
                self._cf_expired(pl, at=cf_at)
            return "EXPIRED"
        # S9: öğrenmede birleşik evrenden kurulan plan, öğrenme askıdayken protokol evrenine döner (açılmaz)
        if lm is None and pl.get("in_lab_universe") is False:
            self._set_status(pl, PL_CANCELLED, int(as_of_ms), "NOT_IN_PROTOCOL_UNIVERSE")
            self.counters["cancelled"] += 1
            return "CANCELLED"
        # evren/durum
        blocked = self._entry_block_reason(symbol, universe_entry, as_of_ms)
        if blocked:
            if blocked == "POSITION_OPEN":
                self._set_status(pl, PL_CANCELLED, int(as_of_ms), "SAME_SYMBOL_POSITION_OPEN")
                self.counters["cancelled"] += 1
                if lm is not None and not self._cf_held_skip(symbol):   # D8: sembol dolu — sinyal kaybolmaz (karşı-olgusal)
                    _e, _ref = self._cf_entry_ref(pl, price)
                    self._cf_record(pl, "POSITION_OPEN", entry=_e, at=cf_at, extra={"entry_ref": _ref})
                return "CANCELLED"
            return _reject(blocked)
        # veri kimliği/güncelliği (giriş dilimi): sağlayıcı hatası ya da bayat seri → ret
        st15 = statuses.get(ENTRY_TF) or {}
        if st15.get("error") or not st15.get("source"):
            return _reject("DATA_ERROR", detail=str(st15.get("error") or "no source")[:120])
        if st15.get("is_stale"):
            return _reject("DATA_STALE_15M", age_s=st15.get("age_s"))
        # PİYASA KİMLİĞİ (2026-09-17): giriş dilimi GERÇEKTEN USDⓈ-M perpetual çerçeveden mi geldi? Kimlik
        # çerçevenin kendi provenansından okunur; sabit yazılmaz (SPOT ikamesi sessizce perp sayılmaz).
        if str(st15.get("market") or "") != PAPER_MARKET:
            return _reject("DATA_MARKET_%s" % (str(st15.get("market") or "UNKNOWN").upper()),
                           market=str(st15.get("market") or ""), source=str(st15.get("source") or ""))
        _ptf = str(pl.get("entry_tf") or ENTRY_TF)
        if _ptf != ENTRY_TF:
            stp = statuses.get(_ptf) or {}
            if str(stp.get("market") or "") != PAPER_MARKET or stp.get("error"):
                return _reject("DATA_MARKET_%s_%s" % (str(stp.get("market") or "UNKNOWN").upper(), _ptf),
                               market=str(stp.get("market") or ""), source=str(stp.get("source") or ""))
        # fiyat: doğrulanmış, güncel perp mark (aynı sözleşme: strategy_paper.verified_price)
        if not price or not price.get("ok"):
            self.data_gaps[symbol] = {"reason": (price or {}).get("reason") or "NO_VERIFIED_FUTURES_PRICE", "at": iso(now)}
            # fiyat yoksa plan TETİKLENMİŞ kalır (sonraki taramada yeniden denenir; süre dolarsa iptal)
            self._set_status(pl, PL_TRIGGERED, int(as_of_ms), "PRICE_GAP_%s" % ((price or {}).get("reason") or "NO_PRICE"))
            return "WAIT_PRICE"                       # süre kontrolü yukarıda, TEK yerde (bekleme dönüşünü de kapsar)
        self.data_gaps.pop(symbol, None)
        mark = float(price["mark"])
        if lm is not None:                             # pencere sonu karşı-olgusalı için son doğrulanmış fiyat
            pl["last_mark"] = {"mark": mark, "at_ms": int(decision_ms)}
        # kovalamama: tetik seviyesinden uzaklık
        dist = abs(mark - float(pl["trigger"]["level"]))
        if dist > float(pl["chase_atr"]) * float(pl["atr"]):
            self._set_status(pl, PL_CANCELLED, int(as_of_ms), "CHASE_LIMIT")
            pl["cancel_detail"] = {"mark": mark, "trigger": pl["trigger"]["level"], "distance_atr": round(dist / float(pl["atr"]), 3), "chase_atr": pl["chase_atr"]}
            self.counters["cancelled"] += 1
            self._event("PLAN_CANCELLED", symbol, "CHASE_LIMIT", now, plan_id=pl["plan_id"], distance_atr=round(dist / float(pl["atr"]), 3))
            if lm is not None:
                self._cf_record(pl, "CHASE_LIMIT", entry=mark, at=cf_at, extra={"entry_ref": "mark", **pl["cancel_detail"]})
            return "CANCELLED"
        # fiyat stop'un yanlış tarafındaysa (giriş anında yapı bozulmuş) → iptal
        if (pl["side"] == "LONG" and mark <= float(pl["stop"])) or (pl["side"] == "SHORT" and mark >= float(pl["stop"])):
            self._set_status(pl, PL_CANCELLED, int(as_of_ms), "MARK_BEYOND_STOP_AT_ENTRY")
            self.counters["cancelled"] += 1
            return "CANCELLED"
        # v3 (laboratuvar kuralı): risk test aralığında mı; hedef GERÇEK giriş fiyatından R katı
        if pl.get("risk_atr_bounds"):
            lo_atr, hi_atr = (float(x) for x in pl["risk_atr_bounds"])
            risk_atr = abs(mark - float(pl["stop"])) / float(pl["atr"])
            if not (lo_atr < risk_atr <= hi_atr):
                self._set_status(pl, PL_CANCELLED, int(as_of_ms), "RISK_OUTSIDE_TESTED_RANGE")
                pl["cancel_detail"] = {"risk_atr": round(risk_atr, 4), "bounds": [lo_atr, hi_atr]}
                self.counters["cancelled"] += 1
                if lm is not None:
                    self._cf_record(pl, "RISK_OUTSIDE_TESTED_RANGE", entry=mark, at=cf_at,
                                    extra={"entry_ref": "mark", **pl["cancel_detail"]})
                return "CANCELLED"
        if pl.get("target_from_entry_rr"):
            rr = float(pl["target_from_entry_rr"])
            d = abs(mark - float(pl["stop"]))
            pl["target"] = round(mark + rr * d if pl["side"] == "LONG" else mark - rr * d, 10)
        # likidite: tetik anında ölçülür; bilinmiyorsa iyi likidite SAYILMAZ
        liq = None
        try:
            liq = self._call_unlocked(liquidity) if liquidity is not None else None
        except Exception as exc:  # noqa: BLE001
            liq = {"error": f"{type(exc).__name__}: {exc}"[:120]}
        if not liq or liq.get("spread_pct") is None:
            if lm is not None:                         # D16: tek ölçüm arızası TERMİNAL değil — pencere içinde yeniden
                return self._liquidity_wait(pl, "LIQUIDITY_UNKNOWN", as_of_ms=int(as_of_ms), decision_ms=decision_ms, mark=mark,
                                            now=now, detail=str((liq or {}).get("error") or "spread ölçülemedi"))
            return _reject("LIQUIDITY_UNKNOWN", detail=str((liq or {}).get("error") or "spread ölçülemedi")[:120])
        if float(liq["spread_pct"]) > float(self.section.max_spread_pct):
            return _reject("SPREAD_WIDE", spread_pct=float(liq["spread_pct"]), max_spread_pct=float(self.section.max_spread_pct))
        depth = liq.get("depth_0_5pct")
        if depth is None:
            if lm is not None:
                return self._liquidity_wait(pl, "DEPTH_UNKNOWN", as_of_ms=int(as_of_ms), decision_ms=decision_ms, mark=mark,
                                            now=now, detail="derinlik ölçülemedi")
            return _reject("DEPTH_UNKNOWN")
        if lm is None and float(depth) < float(self.section.min_depth_0_5pct_usdt):
            return _reject("THIN_DEPTH", depth_0_5pct=float(depth), min_depth=float(self.section.min_depth_0_5pct_usdt))
        if lm is not None and float(depth) < LEARNING_MIN_DEPTH_USDT:
            # D17: öğrenmede eşik max(2.000, 20 × notional); taban burada, notional'a bağlı kısım boyuttan SONRA
            return _reject("THIN_DEPTH", depth_0_5pct=float(depth), min_depth=LEARNING_MIN_DEPTH_USDT)
        pl["liquidity_at_trigger"] = {k: liq.get(k) for k in ("spread_pct", "depth_0_5pct", "depth_1pct", "source", "ts")}
        if lm is not None and pl.get("liquidity_wait"):
            pl["liquidity_wait"]["pending"] = False    # ölçüldü: bekleme bitti (etiket `learning_unlocked_by`de kalır)
        # maliyet sonrası R/R gerçek giriş fiyatıyla yeniden hesaplanır (geometri değişmiş olabilir)
        g, n = rr_after_cost(mark, float(pl["stop"]), float(pl["target"]), cost_frac=self.cost_frac)
        pl["rr_at_entry"] = {"gross": g, "after_cost": n, "mark": mark}
        unlocked: list[str] = []                       # öğrenmenin açtığı (baseline'da engelleyecek) kodlar
        if n < float(pl["min_rr_after_cost"]):
            if lm is None:
                return _reject("RR_BELOW_MIN_AT_ENTRY", rr_after_cost=n)
            unlocked.append("RR_BELOW_MIN_AT_ENTRY")   # D7: öğrenmede R/R tabanı YOK (laboratuvar paritesi); değer özellikte
        # RESMÎ EMİR KURALLARI (2026-09-17): keşiften girişe bağlı, doğrulanmış filtre. Eksik/geçersiz/bayat →
        # giriş YOK (varsayılan tick/step ile sessiz açılış KAPALI); gerekçe planda ve olayda görünür.
        filters, fwhy, fev = self._resolve_filters(symbol, universe_entry, int(as_of_ms))
        pl["filters_used"] = fev
        if filters is None:
            return _reject(fwhy, **{k: v for k, v in fev.items() if isinstance(v, (int, float, str))})
        # YUVARLAMA SONRASI GEOMETRİ: gerçekleşme fiyatı kayma + tick kuantizasyonundan geçince stop tarafı ve
        # maliyet sonrası R/R değişebilir; ikisi de giriş ÖNCESİ, DEFTERİN KENDİ fiyat yoluyla denetlenir
        # (`market_fill_price` — açılışta kullanılan fonksiyonun ta kendisi, ayrı bir tahmin DEĞİL).
        tick_pre = TickData(last=Decimal(str(mark)), mark=Decimal(str(mark)), ts=iso_ms(price.get("price_ts_ms")) or iso(now))
        pside = PositionSide.LONG if pl["side"] == "LONG" else PositionSide.SHORT
        qmark = float(self.ledger.market_fill_price(symbol, pside, Decimal(str(mark)), filters=filters,
                                                    slippage=self.ledger.slippage, tick=tick_pre))
        if qmark <= 0:
            return _reject("PRICE_QUANTIZED_TO_ZERO", price_tick=str(filters.price_tick), mark=mark)
        if (pl["side"] == "LONG" and qmark <= float(pl["stop"])) or (pl["side"] == "SHORT" and qmark >= float(pl["stop"])):
            return _reject("STOP_BEYOND_QUANTIZED_ENTRY", quantized_entry=qmark, stop=float(pl["stop"]))
        # Maliyet oranı GERÇEKLEŞME fiyatına göre: giriş kayması artık `qmark`e gömülüdür, ikinci kez sayılmaz.
        gq, nq = rr_after_cost(qmark, float(pl["stop"]), float(pl["target"]), cost_frac=self.cost_frac_at_fill)
        # RİSK: tick ızgarası giriş-stop mesafesini GENİŞLETEBİLİR. Boyut ham mark'la hesaplandığı için
        # gerçekleşen risk bütçeyi aşabilir; yuvarlamanın risk üzerindeki etkisi giriş ÖNCESİ ölçülür.
        d0, d1 = abs(mark - float(pl["stop"])), abs(qmark - float(pl["stop"]))
        risk_ratio = (d1 / d0) * (mark / qmark) if d0 > 0 and qmark > 0 else float("inf")
        pl["rr_at_entry"].update({"quantized_entry": qmark, "gross_quantized": gq, "after_cost_quantized": nq,
                                  "risk_ratio_after_rounding": round(risk_ratio, 6),
                                  "max_risk_ratio": 1.0 + RISK_ROUNDING_TOLERANCE})
        if nq < float(pl["min_rr_after_cost"]):
            if lm is None:
                return _reject("RR_BELOW_MIN_AFTER_ROUNDING", rr_after_cost=nq, quantized_entry=qmark)
            unlocked.append("RR_BELOW_MIN_AFTER_ROUNDING")
        if risk_ratio > 1.0 + RISK_ROUNDING_TOLERANCE:
            if lm is None:
                return _reject("RISK_ABOVE_CAP_AFTER_ROUNDING", risk_ratio=round(risk_ratio, 6), quantized_entry=qmark,
                               max_risk_ratio=1.0 + RISK_ROUNDING_TOLERANCE)
            # D6: ret yerine ÖLÇEKLE — boyut gerçekleşme fiyatıyla kurulur, notional 1/oran küçülür (risk bütçede kalır)
            unlocked.append("RISK_ABOVE_CAP_AFTER_ROUNDING")
            pl["rr_at_entry"]["risk_rescaled"] = {"ratio": round(risk_ratio, 6), "notional_factor": round(1.0 / risk_ratio, 6)}
        # ortak uygulayıcı: risk (RiskEngine.evaluate) + defter (gerçek filtre, kayma) — fail-closed veri hükmüyle
        verdict = DataVerdict(ok=True, entry_ok=True, market=str(st15.get("market")), source=str(st15.get("source")), tour_id=self.run_id,
                              bars={tf: (statuses.get(tf) or {}).get("last_open_ms") for tf in statuses if (statuses.get(tf) or {}).get("last_open_ms")},
                              as_of_ms=int(as_of_ms), detail={"price_ts_ms": price.get("price_ts_ms"), "price_age_s": price.get("age_s")})
        act = {"action": "OPEN", "direction": pl["side"], "stop": float(pl["stop"]), "targets": [float(pl["target"])], "leverage": int(pl.get("leverage") or 1),
               "name": "%s:%s" % (BOOK_NAME, pl["family"]), "reason": pl["plan_id"], "expected_r": 0.0, "regime": pl.get("evidence", {}).get("htf_trend"),
               "setup_type": pl["family"]}
        if pl.get("version") == "pattern_protocol_v3.0.0":
            act["cap_notional_to_position_pct"] = True     # geniş 4h stop: tavana küçült (risk ≤ bütçe, kaldıraç 1x)
        tick = TickData(last=Decimal(str(mark)), mark=Decimal(str(mark)), ts=iso_ms(price.get("price_ts_ms")) or iso(now))
        rejected: list[str] = []
        opened_pos: list[Any] = []

        def _on_opened(pos, a):
            opened_pos.append(pos)

        if lm is None:
            res = apply_action(act, symbol=symbol, price=mark, tick=tick, now=now, ledger=self.ledger, risk=self.risk, profile=self.profile,
                               state=self._state({symbol: mark}), filters=filters, run_id=self.run_id,
                               reject=lambda s, r: rejected.append(r), on_closed=self._on_closed, on_opened=_on_opened, data=verdict)
            if res != "OPENED" or not opened_pos:
                if self.learning_capable and rejected and rejected[-1] == R_MAX_POSITIONS:
                    self.learning_counters["max_positions_blocked"] += 1   # D1: askıda defter tavanı (yalnız sayaç)
                return _reject(rejected[-1] if rejected else "LEDGER_%s" % res)
            pos = opened_pos[0]
        else:
            pos, why = self._open_learning(lm, pl, act=act, symbol=symbol, mark=mark, qmark=qmark, tick=tick, now=now,
                                           filters=filters, verdict=verdict, depth=float(depth), unlocked=unlocked,
                                           as_of_ms=int(as_of_ms), universe_entry=universe_entry)
            if pos is None:
                r = _reject(why, **{k: v for k, v in ((pl.get("learning") or {}).get("fit") or {}).items()
                                    if k in ("notional", "leverage", "size_rule") and isinstance(v, (int, float, str))})
                self._cf_record(pl, why, entry=qmark, at=cf_at, extra={"entry_ref": "quantized_entry"})
                return r
        if act.get("_notional_scaled"):
            pl["size_scaled_to_cap"] = dict(act["_notional_scaled"])
            pos.meta["size_scaled_to_cap"] = dict(act["_notional_scaled"])
        # v2 planı KENDİ protokol sürümünü ve dilimini taşır; zaman stopu birimi değişmedi (15m bar × max_hold_bars)
        pos.meta.update({"plan_id": pl["plan_id"], "family": pl["family"], "protocol_version": pl.get("version") or PROTOCOL_VERSION, "cohort": pl.get("cohort"),
                         "age_h_at_entry": universe_entry.get("age_h"), "futures_first_trade_ms": universe_entry.get("futures_first_trade_ms"),
                         "max_hold_bars": int(pl.get("max_hold_bars") or self.section.max_hold_bars), "entry_tf": pl.get("entry_tf") or ENTRY_TF, "time_stop_tf": ENTRY_TF,
                         "trigger": dict(pl["trigger"]), "target_source": pl["target_source"],
                         "price_source": {"kind": "usdm_perp_mark", "price_ts_ms": price.get("price_ts_ms"), "age_s": price.get("age_s")}})
        pos.features.update({"plan_id": pl["plan_id"], "family": pl["family"], "cohort": pl.get("cohort"), "age_h_at_entry": universe_entry.get("age_h"),
                             "rr_after_cost": n, "target_source": pl["target_source"], "side_rule": pl["trigger"]["rule"]})
        if pl.get("structure"):
            # ORTAK YAPI: işlem kaydına girişin dayanağı olan katalog kaydı (panel "neden girdi?" ve aynı yapının ikinci
            # işlemi açmaması bununla okunur)
            # `side`/`trigger` AÇIKÇA planın kendisinden (eski planlar da taşısın): aynı kırılım eşleşmesi bunları okur
            pos.features["structure"] = dict(pl["structure"], action="ENTER", reason_code="PLAN_" + str(pl.get("family")),
                                             side=str(pl.get("side")), trigger=dict(pl.get("trigger") or {}),
                                             bot="pattern_trader", text_tr="Girdi: %s planı tetiklendi (%s)." % (
                                                 pl.get("family"), (pl.get("trigger") or {}).get("text_tr")))
            self._record_plan_decision(pl, (getattr(self, "_scan_analyses", None) or None)
                                       if getattr(self, "_scan_symbol", None) == symbol else None, int(as_of_ms), action="ENTER",
                                       trade_id=pos.id)
        self._set_status(pl, PL_OPENED, int(as_of_ms), "FILLED_%s" % pos.id)
        pl["position_id"], pl["entry_price"], pl["size"] = pos.id, float(pos.entry_avg), {"qty": float(pos.qty), "notional": float(pos.qty * pos.entry_avg), "leverage": pos.leverage}
        pl["risk"] = {"risk_usdt": round(abs(float(pos.entry_avg) - float(pl["stop"])) * float(pos.qty), 6), "risk_pct_of_start": round(abs(float(pos.entry_avg) - float(pl["stop"])) * float(pos.qty) / float(self.ledger.starting_equity) * 100.0, 4)}
        self._set_status(pl, PL_MANAGED, int(as_of_ms), "")
        self.counters["opened"] += 1
        if lm is not None:
            self.learning_counters["opened"] += 1
            pos.features["in_lab_universe"] = bool(self.allowed_symbols is None or symbol in self.allowed_symbols)
        if self.cf is not None:
            # D8 kaydı (pozisyon açıkken taze sinyal; plan KURULMAMIŞTI) aynı plan kimliğiyle şimdi GERÇEK işlem: kayıt düşer
            # (aynı gözlem hem dolum hem "açılmadı" sayılmaz; defterlerle aynı kural, 2026-09-28 öğrenme modu)
            try:
                n_sup = self.cf.supersede(signal_key=str(pl["plan_id"]), symbol=symbol, direction=str(pl.get("side") or ""))
            except Exception as exc:  # noqa: BLE001 — karşı-olgusal arızası işlemi ETKİLEMEZ
                log.warning("formasyon karşı-olgusalı geri alınamadı (%s): %s", symbol, exc)
                n_sup = 0
            if n_sup:
                self.learning_counters["counterfactual_superseded"] += int(n_sup)
        self._event("PLAN_OPENED", symbol, pl["family"], now, plan_id=pl["plan_id"], side=pl["side"], entry=float(pos.entry_avg), stop=float(pl["stop"]), target=float(pl["target"]), position_id=pos.id)
        # karşı plan(lar) iptal: bir yön gerçekleşince aynı sembolde bekleyen tüm planlar kapanır (tersleme yok)
        for q in self.plans.values():
            if q.get("symbol") == symbol and q.get("plan_id") != pl["plan_id"] and q.get("status") in (PL_AWAITING, PL_TRIGGERED):
                self._set_status(q, PL_CANCELLED, int(as_of_ms), "OTHER_PLAN_FILLED:%s" % pl["plan_id"])
                self.counters["cancelled"] += 1
        try:
            mfeats = {"plan_id": pl["plan_id"], "family": pl["family"], "side": pl["side"],
                      "cohort": pl.get("cohort"), "age_h_at_entry": universe_entry.get("age_h"), "rr_after_cost": n, "target_source": pl["target_source"],
                      "finding_ids": list(pl.get("finding_ids") or []), "evidence": pl.get("evidence")}
            if lm is not None and isinstance(pos.features.get("learning"), dict):
                # ÖĞRENME MODU (2026-09-28, öğrenme modu): işlem hafızası satırı da etiketleri taşır (yalnız öğrenmede açılan)
                mfeats["learning"] = dict(pos.features["learning"])
                mfeats["in_lab_universe"] = bool(pos.features.get("in_lab_universe"))
            self.memory.record_entry({"trade_id": pos.id, "symbol": symbol, "direction": pos.side.value, "market_type": "USDM_PERP", "setup_type": pl["family"],
                                      "regime": pl.get("evidence", {}).get("htf_trend"), "features": mfeats, "run_id": self.run_id, "in_test": True})
        except Exception as exc:  # noqa: BLE001
            log.warning("formasyon defteri giriş belleği yazılamadı (%s): %s", symbol, exc)
        return "OPENED"

    # ------------------------------------------------------------------ ÖĞRENME MODU (2026-09-28, öğrenme modu)
    def set_learning(self, learning: Any, *, universe_symbols: Any = None, gate: Callable[[], tuple[bool, str]] | None = None,
                     status: dict[str, Any] | None = None) -> None:
        """Motor HER ANA TURDA çağırır. `learning` = `LearningMode.book("pattern_trader")` (kapalı/askıda → None);
        `universe_symbols` = ana botun giriş evreni (S9: protokol coinleri ∪ bu liste); `gate` = `LearningMode.active`
        (her formasyon turu başında yeniden sorulur, düşerse o tur baseline); `status` = `LearningMode.status()` (özet).
        Görünüm tarayıcının SONRAKİ turunda (`begin_cycle`) devreye girer — tur ortasında değişmez."""
        with self.lock:
            self._lm_next = learning if (learning is not None and bool(getattr(learning, "on", False))) else None
            self._lm_universe_next = frozenset(str(x).upper() for x in (universe_symbols or ()) if x)
            self._lm_gate = gate
            self._lm_engine_status = dict(status or {})

    def begin_cycle(self, now: datetime | None = None) -> bool:
        """Formasyon turu başı: öğrenme görünümünü BU TUR için sabitler (kapı bir kez sorulur). Döner: aktif mi."""
        with self.lock:
            bl = self._lm_next
            if bl is not None:
                reason = STATE_ACTIVE
            else:
                reason = str(self._lm_engine_status.get("reason") or "") or (
                    SUSPENDED_PREFIX + "NO_ENGINE_VIEW" if self.learning_capable else STATE_DISABLED)
            if bl is not None and self._lm_gate is not None:
                try:
                    ok, why = self._lm_gate()
                except Exception as exc:  # noqa: BLE001 — kapı arızası öğrenmeyi AÇMAZ (fail-closed)
                    ok, why = False, "MODE_GATE_ERROR:" + type(exc).__name__
                if not ok:
                    bl = None
                    why = str(why or "MODE_GATE")
                    reason = why if (why == STATE_DISABLED or why.startswith(SUSPENDED_PREFIX)) else SUSPENDED_PREFIX + why
            was_on = self.learning is not None
            self.learning = bl
            # D1 (2026-09-28, öğrenme modu): adet tavanı YALNIZ aktif öğrenme turunda kalkar; askı/kapalı → bugünkü defter
            # (tavan defterde, RiskEngine'den SONRA). Bayrak `allow_shrink` değildir; tur başına ayarlanır.
            self.ledger.enforce_position_cap = bl is None
            self._lm_universe = self._lm_universe_next if bl is not None else frozenset()
            if bl is not None:
                self._lm_ever = True
                if bool(getattr(bl, "counterfactual", False)):
                    try:
                        self._cf_recorder(bl)          # sayaç yedeği ilk aktif turda geri yüklensin (kayıt beklemeden)
                    except Exception as exc:  # noqa: BLE001 — kayıtçı arızası defteri ETKİLEMEZ
                        log.warning("formasyon karşı-olgusal kayıtçısı kurulamadı: %s", exc)
            if self._lm_since is None or (bl is not None) != was_on:
                self._lm_since = iso(now or utc_now())
            self._lm_reason = reason
            return bl is not None

    @property
    def learning_on(self) -> bool:
        lm = getattr(self, "learning", None)
        return lm is not None and bool(getattr(lm, "on", False))

    def entry_symbols(self) -> frozenset[str] | None:
        """YENİ plan kurulabilecek semboller. Kapalıyken `allowed_symbols` (bit-aynı); öğrenmede protokol ∪ ana evren (S9)."""
        base = self.allowed_symbols
        if base is None or not self.learning_on or not self._lm_universe:
            return base
        return base | self._lm_universe

    def learning_summary(self) -> dict[str, Any]:
        """Panel/sağlık: öğrenme durumu, defter görünümü, sayaçlar ve karşı-olgusal istatistikleri."""
        lm = self.learning if self.learning_on else None
        base = self.allowed_symbols
        extra = sorted(self._lm_universe - base) if (base is not None and lm is not None) else []
        es = self.entry_symbols()
        return {"enabled_at_start": bool(self.learning_capable), "active": lm is not None, "reason": self._lm_reason,
                "since": self._lm_engine_status.get("since") or self._lm_since,
                "engine_status": dict(self._lm_engine_status) or None,
                "book": lm.to_dict() if (lm is not None and hasattr(lm, "to_dict")) else None,
                "position_cap": {"max_open_positions": int(self.section.max_open_positions),
                                 "ledger_enforced": bool(self.ledger.enforce_position_cap),
                                 "applies_now": lm is None},
                "universe": {"protocol_symbols": len(base) if base is not None else None, "extra_symbols": extra[:100],
                             "n_extra": len(extra), "n_entry_symbols": len(es) if es is not None else None},
                "open_under_learning": sum(1 for p in self.ledger.positions.values() if p.meta.get("learning")),
                "counters": dict(self.learning_counters),
                "counterfactual": self.cf.stats() if self.cf is not None else None,
                "note_tr": "ÖĞRENME MODU — yalnız PAPER; %0,5 risk, slot boyutu. USDT sonuçları öğrenme ölçeğindedir; "
                           "R ölçütleri esastır. Karşı-olgusallar P&L'e GİRMEZ."}

    def _learning_risk(self) -> RiskEngine:
        """Öğrenme RiskEngine'i: taban profilin KOPYASI (toplam açık risk 100; işlem başı %2 tavan AYNEN), AYNI kill switch."""
        if self.risk_learning is None:
            self.risk_learning = RiskEngine(self._lm_cfg.profile_for(self.profile), self.risk.ks,
                                            self.cfg.v3.risk_profiles.clusters or None)
        return self.risk_learning

    def _cf_recorder(self, lm: Any):
        if self.cf is None:
            from ..learning_cf import CounterfactualRecorder
            self.cf = CounterfactualRecorder(self.state_dir / "counterfactual_trades.json", book=BOOK_KEY,
                                             max_pending=int(getattr(lm, "max_pending", 2000) or 2000),
                                             archive=self._cf_archive())
            # sayaçların kalıcı yedeği: özet `learning` alanını kaybettiyse (öğrenme kapalı geçti / eski kod) geri gelir
            for k, v in self.cf.sync_book_counters(self.learning_counters).items():
                self.learning_counters[k] = int(v)
        return self.cf

    def _cf_archive(self) -> Any:
        """Etiketlenmiş kayıtlar taşarsa KAYIPSIZ arşiv (strateji defterleriyle aynı kural, 2026-09-28 öğrenme modu);
        kurulamazsa None → budama yok (dosya büyür, sessiz silme yok)."""
        lv = getattr(self.cfg.v3, "learning_v3", None)
        if not bool(getattr(lv, "decision_archive_enabled", False)):
            return None
        try:
            from ..learn.journal_archive import SegmentArchive
            return SegmentArchive(self.state_dir / "counterfactual_archive", stream_id="counterfactual_%s" % BOOK_KEY,
                                  record_schema_version="shadow_trade_v1",
                                  max_segments=int(getattr(lv, "decision_archive_max_segments", 0) or 0))
        except Exception as exc:  # noqa: BLE001 — arşiv kurulamazsa SİLME de yapılmaz
            log.warning("formasyon karşı-olgusal arşivi kurulamadı: %s", exc)
            return None

    @staticmethod
    def _cf_entry_ref(pl: dict[str, Any], price: dict[str, Any] | None) -> tuple[float | None, str]:
        """Karşı-olgusal giriş referansı: doğrulanmış mark varsa o; yoksa teyit kapanışı (doğrulanmış perp barı)."""
        try:
            if price and price.get("ok") and float(price.get("mark") or 0.0) > 0:
                return float(price["mark"]), "mark"
        except (TypeError, ValueError):
            pass
        if pl.get("trigger_close") is not None:
            return float(pl["trigger_close"]), "trigger_close"
        lvl = (pl.get("trigger") or {}).get("level")
        return (float(lvl), "trigger_level") if lvl is not None else (None, "none")

    def _cf_record(self, pl: dict[str, Any], reason: str, *, entry: Any, at: datetime, extra: dict[str, Any] | None = None) -> bool:
        """Açılmayan geçerli sinyal → karşı-olgusal kayıt (yalnız öğrenme aktif + `counterfactual`). Sinyal anahtarı =
        plan kimliği (turlar boyunca sabit, P1); geometri/neden süzgeci ve tekillik kayıtçıdadır. Asla işlem ETKİLEMEZ."""
        lm = self.learning if self.learning_on else None
        if lm is None or not bool(getattr(lm, "counterfactual", False)) or entry is None:
            return False
        try:
            e, stop, side = float(entry), float(pl["stop"]), str(pl.get("side") or "").upper()
            rr = pl.get("target_from_entry_rr")
            if rr:
                d = abs(e - stop)
                target = e + float(rr) * d if side == "LONG" else e - float(rr) * d
            else:
                target = float(pl.get("target"))
            tf_min, horizon = cf_horizon(int(pl.get("max_hold_bars") or self.section.max_hold_bars))
            sym = str(pl.get("symbol") or "")
            feats = {"plan_id": pl.get("plan_id"), "family": pl.get("family"), "protocol_version": pl.get("version"),
                     "cohort": pl.get("cohort"), "age_h_at_plan": pl.get("age_h_at_plan"), "entry_tf": pl.get("entry_tf"),
                     "in_lab_universe": bool(self.allowed_symbols is None or sym in self.allowed_symbols),
                     "rsi14_at_confirm": (pl.get("evidence") or {}).get("rsi14_at_confirm"),
                     "triggered_at_ms": pl.get("triggered_at_ms"), "min_rr_after_cost": pl.get("min_rr_after_cost"),
                     "rr_at_entry": pl.get("rr_at_entry"), "liquidity_at_trigger": pl.get("liquidity_at_trigger"),
                     "liquidity_wait": pl.get("liquidity_wait"), "time_stop_bars_15m": pl.get("max_hold_bars"),
                     **(extra or {})}
            from ..learning_cf import LABEL_TARGET_STOP_TIME
            ok = self._cf_recorder(lm).record(signal_key=str(pl["plan_id"]), symbol=sym, direction=side, entry=e, stop=stop,
                                              targets=[target], reason=str(reason), created_at=at, tf_minutes=tf_min,
                                              horizon_bars=horizon, label_kind=LABEL_TARGET_STOP_TIME, features=feats,
                                              rule_version=pl.get("version"))
        except Exception as exc:  # noqa: BLE001 — karşı-olgusal arızası planı/defteri ETKİLEMEZ
            log.warning("formasyon karşı-olgusalı yazılamadı (%s %s): %s", pl.get("symbol"), reason, exc)
            return False
        if ok:
            self.learning_counters["counterfactual_recorded"] += 1
            pl["counterfactual"] = {"reason": str(reason), "at": iso(at), "entry": e}
            self._event("COUNTERFACTUAL_RECORDED", str(pl.get("symbol") or ""), str(reason), at, plan_id=pl.get("plan_id"))
        return ok

    def _cf_expired(self, pl: dict[str, Any], *, at: datetime) -> bool:
        """D16/C4: pencere doldu. Likidite hâlâ ölçülemiyorsa neden LIQUIDITY_UNKNOWN; aksi halde EXPIRED_BEFORE_ENTRY.
        Giriş referansı son doğrulanmış mark (yoksa teyit kapanışı)."""
        w = pl.get("liquidity_wait") or {}
        last = pl.get("last_mark") or {}
        if w.get("pending"):
            pl["expired_while"] = "LIQUIDITY_UNKNOWN"
            return self._cf_record(pl, "LIQUIDITY_UNKNOWN", entry=w.get("mark"), at=at,
                                   extra={"entry_ref": "last_mark", "wait_codes": list(w.get("codes") or [])})
        if last.get("mark"):
            return self._cf_record(pl, "EXPIRED_BEFORE_ENTRY", entry=last["mark"], at=at, extra={"entry_ref": "last_mark"})
        e, ref = self._cf_entry_ref(pl, None)
        return self._cf_record(pl, "EXPIRED_BEFORE_ENTRY", entry=e, at=at, extra={"entry_ref": ref})

    def _cf_signals_while_open(self, symbol: str, *, as_of_ms: int, dec_ms: int, analyses: dict[str, Any], bars_by_tf: dict[str, list],
                               ue: dict[str, Any], ds: dict[str, Any], price: dict[str, Any] | None) -> int:
        """D8: sembolde pozisyon AÇIKKEN taze sinyal (yalnız v3 protokolü; aynı kırılım `used_patterns` ile elenir) —
        plan KURULMAZ, yalnız POSITION_OPEN karşı-olgusalı yazılır; A15 tek hareket tek kayıt kuralıyla (`_cf_held_skip`)."""
        if self.protocol != "momentum_4h_v3" or self._cf_held_skip(symbol):
            return 0
        allowed = self.entry_symbols()
        if allowed is not None and symbol not in allowed:
            return 0
        try:
            from ..structures.bots import used_patterns_of
            from .strategy_v3 import build_plans_v3
            plans, _ = build_plans_v3(symbol, as_of_ms=as_of_ms, analyses=analyses, bars_by_tf=bars_by_tf, universe_entry=ue,
                                      data_source=ds, used_patterns=used_patterns_of(self.ledger))
        except Exception as exc:  # noqa: BLE001
            log.warning("formasyon açık-pozisyon sinyal taraması başarısız (%s): %s", symbol, exc)
            return 0
        at = datetime.fromtimestamp(int(dec_ms) / 1000.0, tz=timezone.utc)
        n = 0
        for pl in plans:
            if pl["plan_id"] in self.plans or (n and self._cf_held_skip(symbol)):
                continue
            e, ref = self._cf_entry_ref(pl, price)
            n += int(self._cf_record(pl, "POSITION_OPEN", entry=e, at=at, extra={"entry_ref": ref}))
        return n

    def _cf_held_skip(self, symbol: str) -> bool:
        """A15 "+CF" tek hareket tek kayıt — Box/D4 (`StrategyBook._cf_held`) ile AYNI kural, D8'in iki yolunda (taze sinyal ve
        sembol dolu diye iptal edilen plan) (2026-09-28, öğrenme modu; dördüncü doğrulama turu): tutulan pozisyon öğrenme-
        ekstra DEĞİLSE (politika ya da etiketsiz) taban da aynı pozisyonu tutuyordur → sinyali ALMAZDI, kayıt yok. Bu
        sembolde sonuçlanmamış POSITION_OPEN kaydı varsa tabanın varsayımsal pozisyonu hâlâ açıktır → yeni kayıt yok (uçtan
        uca koşu: politika UNI pozisyonu tutulurken 4 saatte bir üç örtüşen kayıt). Taban kapıları (adet, risk, marj) burada
        ölçülmez: her kayıt varsayımsal pozisyon sayılır (Box/D4'ün `baseline_blocked_by` ayrımı yok). True → kayıt yazma."""
        pos = self.ledger.positions.get(symbol)
        held = (getattr(pos, "meta", None) or {}).get("learning") if pos is not None else None
        if not (isinstance(held, dict) and held.get("learning_unlocked_by")):
            return True
        cf = self.cf
        return bool(cf is not None and cf.has_pending(symbol=symbol, reason="POSITION_OPEN", hypothetical_only=True))

    def _liquidity_wait(self, pl: dict[str, Any], code: str, *, as_of_ms: int, decision_ms: int, mark: float, now: datetime,
                        detail: str = "") -> str:
        """D16: likidite/derinlik ölçülemedi → plan TETİKLENMİŞ bekler, 60 dk penceresi içinde her taramada yeniden
        denenir; pencere dolunca işlem YOK + karşı-olgusal (`_cf_expired`). Ret sayılmaz."""
        w = dict(pl.get("liquidity_wait") or {"codes": [], "attempts": 0, "first_at_ms": int(decision_ms)})
        w["attempts"] = int(w.get("attempts") or 0) + 1
        if code not in (w.get("codes") or []):
            w["codes"] = list(w.get("codes") or []) + [code]
        w.update({"pending": True, "last_code": code, "last_at_ms": int(decision_ms), "mark": float(mark), "detail": str(detail)[:120]})
        pl["liquidity_wait"] = w
        self._set_status(pl, PL_TRIGGERED, int(as_of_ms), "LIQUIDITY_WAIT_%s" % code)
        self.learning_counters["liquidity_waits"] += 1
        if w["attempts"] == 1:
            self._event("PLAN_LIQUIDITY_WAIT", str(pl.get("symbol") or ""), code, now, plan_id=pl.get("plan_id"),
                        expires_at=iso_ms(int(pl["expires_at_ms"])) if pl.get("expires_at_ms") else None)
        return "WAIT_LIQUIDITY"

    def _open_learning(self, lm: Any, pl: dict[str, Any], *, act: dict[str, Any], symbol: str, mark: float, qmark: float, tick: Any,
                       now: datetime, filters: Any, verdict: Any, depth: float, unlocked: list[str], as_of_ms: int,
                       universe_entry: dict[str, Any]) -> tuple[Any, str]:
        """D2/D3/D6/D17 girişi: slot boyutu (öğrenme RiskEngine'i) + baseline'da engelleyecek kodların etiketi. Taban
        kapıları (adet tavanı, risk, marj) öğrenme defterinin DEĞİL taban görünümünün (`baseline_view`) durumuyla ölçülür;
        taban da alacaksa (etiket boş) politika rezervi kullanılabilir (2026-09-28, ikinci doğrulama turu)."""
        state = self._state({symbol: mark})
        view, _dm = baseline_view(state, learning_tags(self.ledger.positions))
        tags = list(unlocked)
        if len(view.open_positions) >= int(self.section.max_open_positions):
            tags.append(R_MAX_POSITIONS)
        if int(self.cooldown_until.get(symbol) or 0) > int(as_of_ms):
            tags.append("COOLDOWN_AFTER_LOSS")
        if self.allowed_symbols is not None and symbol not in self.allowed_symbols:
            tags.append("NOT_IN_PROTOCOL_UNIVERSE")
        tags.extend((pl.get("liquidity_wait") or {}).get("codes") or [])
        if float(depth) < float(self.section.min_depth_0_5pct_usdt):
            tags.append("THIN_DEPTH")
        bcodes, bsize = _baseline_blocks(act, symbol=symbol, direction=str(act.get("direction") or "LONG"), entry=mark,
                                         stop=float(act.get("stop") or 0.0), tick=tick, now=now, ledger=self.ledger,
                                         risk=self.risk, profile=self.profile, state=state, filters=filters)
        tags.extend(c for c in bcodes if c not in tags)
        p_res = (policy_reserve_usdt(equity=float(self.risk.equity_basis(state)), slots=int(lm.slots),
                                     reserve_pct=float(lm.reserve_pct)) if tags else 0.0)
        res = open_learning(act=act, symbol=symbol, price=mark, fill_price=qmark, tick=tick, now=now, ledger=self.ledger,
                            risk=self._learning_risk(), state=state, filters=filters, run_id=self.run_id, learning=lm, data=verdict,
                            max_position_pct=float(getattr(self.profile, "max_position_pct", 0.0) or 0.0) or None,
                            depth=depth, unlocked=tags, reserve_usdt=p_res, baseline_size=bsize)
        pl["learning"] = dict(res.info.get("meta") or {}, fit=res.info.get("fit"), min_depth_0_5pct=res.info.get("min_depth_0_5pct"))
        if res.pos is None:
            return None, res.reason
        meta = res.pos.meta.get("learning") or {}
        if "RR_BELOW_MIN_AT_ENTRY" in unlocked or "RR_BELOW_MIN_AFTER_ROUNDING" in unlocked:
            self.learning_counters["rr_floor_ignored"] += 1
        if "RISK_ABOVE_CAP_AFTER_ROUNDING" in unlocked:
            self.learning_counters["rescaled_after_rounding"] += 1
        if meta.get("size_rule") == "BUMP_MIN_NOTIONAL":
            self.learning_counters["min_notional_bumped"] += 1
        elif meta.get("size_rule") == "SHRUNK_TO_MARGIN":
            self.learning_counters["shrunk_to_margin"] += 1
        # D7: R/R değeri ve taban özellik olarak kalır (kapı değil)
        rra = pl.get("rr_at_entry") or {}
        extra = {"rr_after_cost": rra.get("after_cost"), "rr_after_cost_quantized": rra.get("after_cost_quantized"),
                 "min_rr_after_cost": pl.get("min_rr_after_cost"),
                 "rr_below_min": bool({"RR_BELOW_MIN_AT_ENTRY", "RR_BELOW_MIN_AFTER_ROUNDING"} & set(unlocked)),
                 "risk_ratio_after_rounding": rra.get("risk_ratio_after_rounding"), "risk_rescaled": rra.get("risk_rescaled"),
                 "depth_0_5pct": float(depth), "liquidity_wait_attempts": int((pl.get("liquidity_wait") or {}).get("attempts") or 0)}
        res.pos.features["learning"] = dict(res.pos.features.get("learning") or {}, **extra)
        return res.pos, ""

    def label_counterfactuals(self, symbol: str, bars_by_tf: dict[str, list] | None, now: datetime) -> int:
        """Bekleyen karşı-olgusalları bu sembolün ELDEKİ kapalı barlarıyla etiketler (ağ YOK; yazım `save`de)."""
        cf = self.cf
        if cf is None:
            return 0
        try:
            return int(cf.label_pending({symbol: dict(bars_by_tf or {})}, now))
        except Exception as exc:  # noqa: BLE001 — etiket arızası taramayı ETKİLEMEZ
            log.warning("formasyon karşı-olgusal etiketi başarısız (%s): %s", symbol, exc)
            return 0

    def _time_stop(self, symbol: str, *, now: datetime, as_of_ms: int, price: dict[str, Any] | None) -> None:
        pos = self.ledger.positions.get(symbol)
        if pos is None or not price or not price.get("ok"):
            return
        from ..strategy_paper import parse_ts_ms
        opened = parse_ts_ms(pos.opened_at)
        max_bars = int(pos.meta.get("max_hold_bars") or self.section.max_hold_bars)
        if opened is None or int(as_of_ms) - opened < max_bars * tf_ms(ENTRY_TF):
            return
        mark = float(price["mark"])
        tick = TickData(last=Decimal(str(mark)), mark=Decimal(str(mark)), ts=iso_ms(price.get("price_ts_ms")) or iso(now))
        rec = self.ledger.close_manual(symbol, mark, reason="TIME_STOP", now=now, tick=tick)
        if rec is not None:
            self._on_closed(rec)
            self._event("TIME_STOP", symbol, "MAX_HOLD_BARS", now, bars=max_bars, mark=mark)

    # ------------------------------------------------------------------ fiyat yolu (izleyici) ve bar uçları
    def tick(self, marks: dict[str, TickData], *, now: datetime, funding_rate_lookup=None, bar_advance: bool = False,
             expect: dict[str, str] | None = None, source: str = "scanner", apply_clock=None) -> list:
        """Güncel fiyat tiki — `protective_monitor.guarded_tick` ile (kimlik + fiyat zamanı sırası; bkz. StrategyBook.tick)."""
        from ..protective_monitor import guarded_tick, note_gap_first_observations
        with self.lock:
            self._resume_once(now)
            recs, info = guarded_tick(self.ledger, marks, now=now, funding_rate_lookup=funding_rate_lookup,
                                      bar_advance=bar_advance, expect=expect, apply_clock=apply_clock)
            for rec in recs:
                self._on_closed(rec)
            applied = info.get("applied") or {}
            if applied and self.monitoring_gap:
                note_gap_first_observations(self.monitoring_gap, book_key=self.key, state_path=self.cfg.state_path,
                                            applied=applied, now=now, closed_ids=[str(getattr(r, "id", "")) for r in recs])
            if applied and self.observer is not None:
                from ..strategy_paper import parse_ts_ms
                opened = {s: parse_ts_ms(p.opened_at) or 0 for s, p in self.ledger.positions.items()}
                self.observer.note(self.key, applied, int(now.timestamp() * 1000), opened_ms=opened, source=source)
            return recs

    def held_ids(self) -> dict[str, str]:
        """Açık pozisyonların kimlik anlık görüntüsü (kısa kilit)."""
        with self.lock:
            return {s: str(p.id) for s, p in self.ledger.positions.items()}

    def protect(self, marks: dict[str, TickData], marks_f: dict[str, float], gaps: dict[str, dict] | None, *, now: datetime,
                expect: dict[str, str] | None = None, source: str = "monitor", funding_rate_lookup: Any = "__book__",
                apply_clock=None) -> list:
        """KORUYUCU İZLEME ADIMI (tek kısa atomik bölüm; AĞ YOK): fiyat boşlukları → korumalı tick → kayıt."""
        frl = getattr(self, "funding_rates", None) if funding_rate_lookup == "__book__" else funding_rate_lookup
        with self.lock:
            self._resume_once(now)
            self.record_gaps(gaps or {}, now)
            recs = self.tick(marks, now=now, funding_rate_lookup=frl, bar_advance=False, expect=expect, source=source,
                             apply_clock=apply_clock) if marks else []
            self.save(marks_f, now)
            return recs

    def _call_unlocked(self, fn: Callable[[], Any]) -> Any:
        """AĞ BEKLEMESİ DEFTER KİLİDİNİ TUTMAZ (2026-09-24): tetik anındaki likidite (spread/derinlik) isteği sürerken kilit
        TAMAMEN bırakılır ve sonra aynı derinlikte geri alınır. Arada yalnız koruyucu yol (izleyici/tick) çalışabilir: mevcut
        pozisyonları kapatır ya da tick'ler, YENİ pozisyon açmaz. Giriş yolu kilidi geri aldıktan sonra defteri (cüzdan,
        pozisyon sayısı) yeniden okur; bu sembolde pozisyon yoktu ve olamaz."""
        lk = self.lock
        try:
            state = lk._release_save()                 # yalnız bu iş parçacığı tutuyorsa (RLock sözleşmesi)
        except RuntimeError:
            return fn()
        try:
            return fn()
        finally:
            lk._acquire_restore(state)

    def apply_closed_bars(self, bars_by_symbol: dict[str, dict[str, Any]] | None, *, now: datetime, funding_rate_lookup=None) -> list:
        with self.lock:
            self._resume_once(now)
            return apply_closed_bars_to_ledger(self.ledger, bars_by_symbol, now=now, funding_rate_lookup=funding_rate_lookup, on_closed=self._on_closed,
                                               on_event=lambda sym, kind, reason, at, **extra: self._event(kind, sym, reason, at, **extra),
                                               gap_until_ms=getattr(self, "_gap_until_ms", None))

    def _resume_once(self, now: datetime) -> None:
        """İZLEME KESİNTİSİ (2026-09-23): bu süreçteki İLK defter etkinliğinde (tarama adımı, fiyat tiki, bar, fiyat
        boşluğu, funding ya da kayıt — hangisi önce gelirse) BİR KEZ denetlenir. Defter uzun süre izlenmediyse kesinti
        YÜKLEME anındaki pozisyonlarla kaydedilir (ilk adımın TIME_STOP'u onları kapatsa bile); kesintinin bittiği ana
        kadar kapanmış barlar bu süreçte uygulanmaz. Çağıran `self.lock`u tutar."""
        if getattr(self, "_resume_checked", True):
            return
        self._resume_checked = True
        held = list(getattr(self, "_resume_positions", None) or [])
        self._gap_until_ms = monitoring_gap_on_resume(self.ledger, getattr(self, "_resume_saved_at", None), now=now,
                                                      book_key=self.key, state_path=self.cfg.state_path, positions=held)
        if self._gap_until_ms is not None:
            self.monitoring_gap = {"from": str(getattr(self, "_resume_saved_at", None)), "to": iso(now), "positions": list(held)}
            self._event("MONITORING_GAP", "*", "bars_closed_in_gap_not_applied", now,
                        since=str(getattr(self, "_resume_saved_at", None)), positions=held)

    def record_gaps(self, gaps: dict[str, dict[str, Any]], now: datetime) -> None:
        with self.lock:
            self._resume_once(now)
            for sym, g in (gaps or {}).items():
                prev = self.data_gaps.get(sym)
                if not prev or prev.get("reason") != g.get("reason"):
                    self._event("PRICE_GAP", sym, str(g.get("reason") or "NO_VERIFIED_FUTURES_PRICE"), now, detail=g.get("detail"))
                self.data_gaps[sym] = dict(g)
            for sym in [s for s in self.data_gaps if s not in (gaps or {}) and s in self.ledger.positions]:
                self._event("PRICE_RESTORED", sym, "", now)
                self.data_gaps.pop(sym, None)


__all__ = ["BOOK_KEY", "BOOK_NAME", "SUMMARY_FILE", "SCHEMA_VERSION", "PatternBook"]
