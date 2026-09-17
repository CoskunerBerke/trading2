# -*- coding: utf-8 -*-
"""FORMASYON PAPER DEFTERİ — ayrı sanal bakiye/kimlik; ortak risk (`RiskEngine`), ortak uygulayıcı (`apply_action`), gerçek
`FuturesLedgerV2`; plan yaşam döngüsü kalıcı ve denetlenebilir.

Durum geçişleri (plan): AWAITING_TRIGGER → TRIGGERED → RISK_CHECK → OPENED → MANAGED → CLOSED; alternatif sonlar
REJECTED / BROKEN / EXPIRED / CANCELLED. Aynı plan iki kez emir açamaz (`position_id`), aynı sembolde ikinci pozisyon
açılmaz, karşı taraf tetiklenince diğer plan iptal olur, zarardan sonra sembol bazında soğuma vardır (otomatik tersleme /
martingale YOK). Veri kaynağı, fiyat güncelliği, spread/derinlik, stop mesafesi, maliyet sonrası R/R ve kill-switch
kontrolleri zorunludur; olasılık modeli YOKTUR (p_win=None) — uydurma 0,50 ile kapı geçirilmez.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from ..accounting import (FeeSchedule, FuturesLedgerV2, LiquidationParams, MarketType, PositionSide, SlippageModel, TaxPolicy,
                          TickData, default_brackets)
from ..accounting.funding import FundingSchedule
from ..core import atomic_write_json, iso, read_json, utc_now
from ..learn import TradeMemory
from ..learn.candle_context import CandleContextConfig, detect_trend
from ..risk import RiskEngine, build_state
from ..strategy_paper import PAPER_MARKET, DataVerdict, apply_action, apply_closed_bars_to_ledger, parse_ts_ms
from ..timeframes import tf_ms
from .data import REQUIREMENTS, readiness
from .detect import ST_BROKEN, ST_CONFIRMED, ST_EXPIRED, atr14, detect_findings, levels_for, update_finding
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
        self.ledger = FuturesLedgerV2.load(self.ledger_path, starting_equity=float(section.starting_equity_usdt),
                                          max_positions=int(section.max_open_positions), enforce_position_cap=True,
                                          fees=fees, slippage=slip, brackets=default_brackets(),
                                          liq_params=LiquidationParams(liq_fee_pct=Decimal(str(v3.futures_v3.liq_fee_pct))),
                                          # FUNDING (2026-09-17): oran bilinmiyorsa TAHMIN YOK — dönem bekler. Bu KURULUŞTA
                                          # ayarlanır (yalnız `bind_funding`e bırakılmaz): tarayıcı hiç başlamasa bile eski
                                          # bir `meta.last_funding_rate` tahmini kesinti ÜRETEMEZ.
                                          funding=FundingSchedule(fallback_to_last_known=False),
                                          tp1_fraction=Decimal("1"), breakeven_at_mfe_r=Decimal("0"), tax_policy=TaxPolicy.disabled())
        self.risk = RiskEngine(profile, killswitch, v3.risk_profiles.clusters or None)
        self.memory = TradeMemory(self.state_dir / "trade_memory.jsonl", source="PATTERN_PAPER")
        self.lock = threading.RLock()
        self.candle_cfg = CandleContextConfig()
        self.params = {"stop_buffer_atr": float(section.stop_buffer_atr), "min_rr_after_cost": float(section.min_rr_after_cost),
                       "fallback_rr": float(section.fallback_rr), "measured_move_mult": float(section.measured_move_mult)}
        self.families = tuple(section.families)
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
        #: Tur içi filtre belleği (sembol → (keşif anı ms, SymbolFilters)). PAYLAŞILAN önbelleğe YAZILMAZ;
        #: bkz. `_resolve_filters` — ana botun resmî yenileme damgası kirletilmez, kilitsiz nesneye iş
        #: parçacığından yazılmaz.
        self._filters_memo: dict[str, tuple[int, Any]] = {}
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
        except Exception as exc:  # noqa: BLE001 — bozuk özet: sayaçlar sıfırdan, defter ETKİLENMEZ
            log.warning("formasyon defteri durumu geri yüklenemedi: %s", exc)
        closed = len(self.ledger.history_dicts())
        self.counters["closed"] = closed
        self.counters["opened"] = closed + len(self.ledger.positions)

    def save(self, marks_f: dict[str, float] | None, now: datetime) -> None:
        with self.lock:
            self.ledger.save(self.ledger_path)
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
                                     "mae_pct": float(p.mae_pct), "mfe_pct": float(p.mfe_pct)} for s, p in self.ledger.positions.items()},
                   "history_tail": self.ledger.history_dicts()[-50:], "counters": dict(self.counters), "rejections": dict(self.rejections),
                   "plans_by_status": by_status, "findings_by_status": fstat, "n_plans": len(self.plans), "n_findings": len(self.findings),
                   "active_plans": [self._plan_brief(pl) for pl in self.plans.values() if pl.get("status") in (PL_AWAITING, PL_TRIGGERED, PL_RISK_CHECK, PL_OPENED, PL_MANAGED)][:200],
                   "recent_plans": sorted([self._plan_brief(pl) for pl in self.plans.values()], key=lambda d: -(d.get("created_at_ms") or 0))[:60],
                   "events_recent": self.events[-60:], "data_gaps": dict(self.data_gaps), "cooldown_until_ms": dict(self.cooldown_until),
                   "cohort_stats": self.cohort_stats, "symbol_scans": self.symbol_scans, "closed_recent": self.closed_recent[-30:],
                   "policy": {"families": list(self.families), "params": dict(self.params), "entry_tf": ENTRY_TF, "structure_tf": STRUCTURE_TF, "context_tf": CONTEXT_TF,
                              "requirements": dict(REQUIREMENTS), "max_open_positions": int(self.section.max_open_positions),
                              "max_hold_bars": int(self.section.max_hold_bars), "cooldown_bars_after_loss": int(self.section.cooldown_bars_after_loss),
                              "max_spread_pct": float(self.section.max_spread_pct), "min_depth_0_5pct_usdt": float(self.section.min_depth_0_5pct_usdt),
                              "cost_round_trip_frac": round(self.cost_frac, 6), "p_win": "ÖLÇÜLMEDİ", "risk_profile": self.profile.name,
                              "risk_per_trade_pct": float(self.profile.risk_per_trade_pct)},
                   "note_tr": "FORMASYON PAPER TRADER — gerçek para yok; ana bot/T2/M2 defterlerinden bağımsız sanal bakiye. Kârlılık kanıtı YOK; "
                              "planlar ölçülecek hipotezlerdir."}
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

    def _set_status(self, pl: dict[str, Any], status: str, at_ms: int, reason: str = "") -> None:
        pl["status"] = status
        pl.setdefault("status_history", []).append({"status": status, "at_ms": int(at_ms), "at": iso_ms(at_ms), "reason": reason})
        if reason:
            pl.setdefault("reasons", []).append(reason)

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
        sch = self.ledger.funding
        sch.hours_for_symbol = rates.hours_for if hasattr(rates, "hours_for") else None
        sch.fallback_to_last_known = False

    def funding_coverage(self, *, symbol: str, opened_at: str, until: str, settled_until: str | None,
                         hours_utc: Any = None, settled_ts: Any = None) -> dict[str, Any]:
        """Bir pozisyon/işlem için funding KAPSAMASI. AĞ ÇAĞRISI YOKTUR: settlement saatleri ve gerçekten
        uygulanan settlement anları defterin tick'inde KAYDA geçirilmiştir (`features.funding_hours_utc`,
        `features.funding_settled_ts`); burada yalnız okunur. (Karşıt doğrulama bulgusu: eskiden `hours_for`
        çağrılıyordu ve bu, kapanış yolunda kilit altında bir HTTP isteğine dönüşebiliyordu.)

        `settled` GERÇEKTEN uygulanan dönemlerden sayılır; yoksa watermark'tan türetilir (eski kayıtlar için).
        `complete=False` ise o işlemin funding maliyeti ÖLÇÜLMEMİŞTİR — sıfır sanılmamalıdır."""
        from ..core import from_iso, funding_settlements_between
        hours = tuple(hours_utc) if hours_utc is not None else self.ledger.funding.hours_for(symbol)
        out: dict[str, Any] = {"interval_known": bool(hours), "hours_utc": list(hours), "settled_until": settled_until,
                               "rate_source": "lookup" if getattr(self, "funding_rates", None) is not None else "none"}
        if not hours:
            return {**out, "due": None, "settled": None, "missing": None, "complete": False, "reason": "INTERVAL_UNKNOWN"}
        try:
            o, u = from_iso(str(opened_at)), from_iso(str(until))
        except (ValueError, TypeError):
            return {**out, "due": None, "settled": None, "missing": None, "complete": False, "reason": "TIME_UNREADABLE"}
        due_ts = funding_settlements_between(o, u, hours)
        if isinstance(settled_ts, list):
            applied = {str(t) for t in settled_ts}
            settled = sum(1 for t in due_ts if iso(t) in applied)
            out["source"] = "applied_settlements"
        else:                                             # eski kayıt: yalnız watermark var (alt sınır)
            try:
                w = from_iso(str(settled_until)) if settled_until else o
            except (ValueError, TypeError):
                return {**out, "due": len(due_ts), "settled": None, "missing": None, "complete": False, "reason": "TIME_UNREADABLE"}
            settled = len(funding_settlements_between(o, w, hours))
            out["source"] = "watermark"
        missing = max(0, len(due_ts) - settled)
        return {**out, "due": len(due_ts), "settled": settled, "missing": missing, "complete": missing == 0,
                "reason": "" if missing == 0 else "RATES_MISSING"}

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
                                   "cohort": (d.get("features") or {}).get("cohort")})
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
            for tf, rows in ((ENTRY_TF, b15), (STRUCTURE_TF, b1h), (CONTEXT_TF, b4h)):
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
                if pl["status"] == PL_AWAITING:
                    last_eval = int(pl.get("last_evaluated_bar_ts") or 0)
                    for b in b15:
                        ts = int(b["timestamp"])
                        if ts <= last_eval or ts + tf_ms(ENTRY_TF) > int(as_of_ms):
                            continue
                        if self.is_expired(pl, ts + tf_ms(ENTRY_TF)):
                            self._set_status(pl, PL_EXPIRED, ts + tf_ms(ENTRY_TF), "NOT_TRIGGERED_BEFORE_EXPIRY")
                            self.counters["expired"] += 1
                            break
                        res = evaluate_trigger(pl, b)
                        pl["last_evaluated_bar_ts"] = ts
                        if res == PL_TRIGGERED:
                            self._set_status(pl, PL_TRIGGERED, ts + tf_ms(ENTRY_TF), "CLOSE_%s_%s" % (pl["trigger"]["rule"].upper(), pl["trigger"]["level"]))
                            pl["triggered_at_ms"], pl["trigger_bar_ts"], pl["trigger_close"] = ts + tf_ms(ENTRY_TF), ts, float(b["close"])
                            self.counters["triggered"] += 1
                            cs["triggered"] += 1
                            out["triggered"] += 1
                            self._event("PLAN_TRIGGERED", symbol, pl["family"], now, plan_id=pid, side=pl["side"], bar_close=iso_ms(ts + tf_ms(ENTRY_TF)))
                            break
                        if res == PL_BROKEN:
                            self._set_status(pl, PL_BROKEN, ts + tf_ms(ENTRY_TF), "CLOSE_BEYOND_INVALIDATION")
                            self.counters["broken"] += 1
                            break
                    if pl["status"] == PL_AWAITING and self.is_expired(pl, dec_ms):
                        self._set_status(pl, PL_EXPIRED, dec_ms, "EXPIRED_AT_SCAN")
                        self.counters["expired"] += 1
                if pl.get("status") == PL_TRIGGERED:
                    triggered.append(pl)
            # 3) tetiklenen planlar → risk kontrolü → giriş (sembol başına tek pozisyon; sıra: en erken tetik)
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
            if blocked:
                out["skipped"].append({"reason": blocked})
            else:
                sym_findings = [f for f in self.findings.values() if f.get("symbol") == symbol]
                plans, skipped = build_plans(symbol, as_of_ms=as_of_ms, bars_by_tf=bars_by_tf, findings=sym_findings, levels_1h=levels_1h, trend_4h=trend_4h,
                                             cost_frac=self.cost_frac, params=self.params, universe_entry=ue, data_source=ds, enabled_families=self.families)
                out["skipped"].extend(skipped)
                for pl in plans:
                    if pl["plan_id"] in self.plans:
                        continue
                    if any(q.get("symbol") == symbol and q.get("family") == pl["family"] and q.get("side") == pl["side"] and q.get("status") in (PL_AWAITING, PL_TRIGGERED)
                           for q in self.plans.values()):
                        continue                       # aynı aile/yönde bekleyen plan zaten var (yinelenen plan yok)
                    self.plans[pl["plan_id"]] = pl
                    out["new_plans"] += 1
                    self.counters["plans"] += 1
                    cs["plans"] += 1
                    self._event("PLAN_CREATED", symbol, pl["family"], now, plan_id=pl["plan_id"], side=pl["side"], trigger=pl["trigger"]["level"], stop=pl["stop"], target=pl["target"])
            self.symbol_scans[symbol] = {"last_scan_ms": int(as_of_ms), "last_15m_bar_ts": int(b15[-1]["timestamp"]) if b15 else prev_bar, "cohort": cohort,
                                         "last_bar_ts": {tf: int(rows[-1]["timestamp"]) for tf, rows in ((ENTRY_TF, b15), (STRUCTURE_TF, b1h), (CONTEXT_TF, b4h)) if rows},
                                         "n_15m": len(b15), "n_1h": len(b1h), "n_4h": len(b4h), "trend_4h": trend_4h.get("trend"), "n_zones_1h": len(levels_1h.get("zones") or [])}
            if len(self.symbol_scans) > 2000:
                for k in sorted(self.symbol_scans, key=lambda s: self.symbol_scans[s].get("last_scan_ms") or 0)[:200]:
                    self.symbol_scans.pop(k, None)
            out["levels_1h"] = {"n_zones": len(levels_1h.get("zones") or []), "reason": levels_1h.get("reason")}
            out["trend_4h"] = trend_4h.get("trend")
            return out

    def _entry_block_reason(self, symbol: str, ue: dict[str, Any], as_of_ms: int) -> str | None:
        if symbol in self.ledger.positions:
            return "POSITION_OPEN"
        if int(self.cooldown_until.get(symbol) or 0) > int(as_of_ms):
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
        v = parse_ts_ms(pl.get("expires_at_ms")) if isinstance(pl, dict) else None
        if v is None:
            return True
        return int(as_of_ms) > int(v)

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
            return "EXPIRED"
        # evren/durum
        blocked = self._entry_block_reason(symbol, universe_entry, as_of_ms)
        if blocked:
            if blocked == "POSITION_OPEN":
                self._set_status(pl, PL_CANCELLED, int(as_of_ms), "SAME_SYMBOL_POSITION_OPEN")
                self.counters["cancelled"] += 1
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
        # fiyat: doğrulanmış, güncel perp mark (aynı sözleşme: strategy_paper.verified_price)
        if not price or not price.get("ok"):
            self.data_gaps[symbol] = {"reason": (price or {}).get("reason") or "NO_VERIFIED_FUTURES_PRICE", "at": iso(now)}
            # fiyat yoksa plan TETİKLENMİŞ kalır (sonraki taramada yeniden denenir; süre dolarsa iptal)
            self._set_status(pl, PL_TRIGGERED, int(as_of_ms), "PRICE_GAP_%s" % ((price or {}).get("reason") or "NO_PRICE"))
            return "WAIT_PRICE"                       # süre kontrolü yukarıda, TEK yerde (bekleme dönüşünü de kapsar)
        self.data_gaps.pop(symbol, None)
        mark = float(price["mark"])
        # kovalamama: tetik seviyesinden uzaklık
        dist = abs(mark - float(pl["trigger"]["level"]))
        if dist > float(pl["chase_atr"]) * float(pl["atr"]):
            self._set_status(pl, PL_CANCELLED, int(as_of_ms), "CHASE_LIMIT")
            pl["cancel_detail"] = {"mark": mark, "trigger": pl["trigger"]["level"], "distance_atr": round(dist / float(pl["atr"]), 3), "chase_atr": pl["chase_atr"]}
            self.counters["cancelled"] += 1
            self._event("PLAN_CANCELLED", symbol, "CHASE_LIMIT", now, plan_id=pl["plan_id"], distance_atr=round(dist / float(pl["atr"]), 3))
            return "CANCELLED"
        # fiyat stop'un yanlış tarafındaysa (giriş anında yapı bozulmuş) → iptal
        if (pl["side"] == "LONG" and mark <= float(pl["stop"])) or (pl["side"] == "SHORT" and mark >= float(pl["stop"])):
            self._set_status(pl, PL_CANCELLED, int(as_of_ms), "MARK_BEYOND_STOP_AT_ENTRY")
            self.counters["cancelled"] += 1
            return "CANCELLED"
        # likidite: tetik anında ölçülür; bilinmiyorsa iyi likidite SAYILMAZ
        liq = None
        try:
            liq = liquidity() if liquidity is not None else None
        except Exception as exc:  # noqa: BLE001
            liq = {"error": f"{type(exc).__name__}: {exc}"[:120]}
        if not liq or liq.get("spread_pct") is None:
            return _reject("LIQUIDITY_UNKNOWN", detail=str((liq or {}).get("error") or "spread ölçülemedi")[:120])
        if float(liq["spread_pct"]) > float(self.section.max_spread_pct):
            return _reject("SPREAD_WIDE", spread_pct=float(liq["spread_pct"]), max_spread_pct=float(self.section.max_spread_pct))
        depth = liq.get("depth_0_5pct")
        if depth is None:
            return _reject("DEPTH_UNKNOWN")
        if float(depth) < float(self.section.min_depth_0_5pct_usdt):
            return _reject("THIN_DEPTH", depth_0_5pct=float(depth), min_depth=float(self.section.min_depth_0_5pct_usdt))
        pl["liquidity_at_trigger"] = {k: liq.get(k) for k in ("spread_pct", "depth_0_5pct", "depth_1pct", "source", "ts")}
        # maliyet sonrası R/R gerçek giriş fiyatıyla yeniden hesaplanır (geometri değişmiş olabilir)
        g, n = rr_after_cost(mark, float(pl["stop"]), float(pl["target"]), cost_frac=self.cost_frac)
        pl["rr_at_entry"] = {"gross": g, "after_cost": n, "mark": mark}
        if n < float(pl["min_rr_after_cost"]):
            return _reject("RR_BELOW_MIN_AT_ENTRY", rr_after_cost=n)
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
            return _reject("RR_BELOW_MIN_AFTER_ROUNDING", rr_after_cost=nq, quantized_entry=qmark)
        if risk_ratio > 1.0 + RISK_ROUNDING_TOLERANCE:
            return _reject("RISK_ABOVE_CAP_AFTER_ROUNDING", risk_ratio=round(risk_ratio, 6), quantized_entry=qmark,
                           max_risk_ratio=1.0 + RISK_ROUNDING_TOLERANCE)
        # ortak uygulayıcı: risk (RiskEngine.evaluate) + defter (gerçek filtre, kayma) — fail-closed veri hükmüyle
        verdict = DataVerdict(ok=True, entry_ok=True, market=str(st15.get("market")), source=str(st15.get("source")), tour_id=self.run_id,
                              bars={tf: (statuses.get(tf) or {}).get("last_open_ms") for tf in statuses if (statuses.get(tf) or {}).get("last_open_ms")},
                              as_of_ms=int(as_of_ms), detail={"price_ts_ms": price.get("price_ts_ms"), "price_age_s": price.get("age_s")})
        act = {"action": "OPEN", "direction": pl["side"], "stop": float(pl["stop"]), "targets": [float(pl["target"])], "leverage": int(pl.get("leverage") or 1),
               "name": "%s:%s" % (BOOK_NAME, pl["family"]), "reason": pl["plan_id"], "expected_r": 0.0, "regime": pl.get("evidence", {}).get("htf_trend"),
               "setup_type": pl["family"]}
        tick = TickData(last=Decimal(str(mark)), mark=Decimal(str(mark)), ts=iso_ms(price.get("price_ts_ms")) or iso(now))
        rejected: list[str] = []
        opened_pos: list[Any] = []

        def _on_opened(pos, a):
            opened_pos.append(pos)

        res = apply_action(act, symbol=symbol, price=mark, tick=tick, now=now, ledger=self.ledger, risk=self.risk, profile=self.profile,
                           state=self._state({symbol: mark}), filters=filters, run_id=self.run_id,
                           reject=lambda s, r: rejected.append(r), on_closed=self._on_closed, on_opened=_on_opened, data=verdict)
        if res != "OPENED" or not opened_pos:
            return _reject(rejected[-1] if rejected else "LEDGER_%s" % res)
        pos = opened_pos[0]
        pos.meta.update({"plan_id": pl["plan_id"], "family": pl["family"], "protocol_version": PROTOCOL_VERSION, "cohort": pl.get("cohort"),
                         "age_h_at_entry": universe_entry.get("age_h"), "futures_first_trade_ms": universe_entry.get("futures_first_trade_ms"),
                         "max_hold_bars": int(self.section.max_hold_bars), "entry_tf": ENTRY_TF, "trigger": dict(pl["trigger"]), "target_source": pl["target_source"],
                         "price_source": {"kind": "usdm_perp_mark", "price_ts_ms": price.get("price_ts_ms"), "age_s": price.get("age_s")}})
        pos.features.update({"plan_id": pl["plan_id"], "family": pl["family"], "cohort": pl.get("cohort"), "age_h_at_entry": universe_entry.get("age_h"),
                             "rr_after_cost": n, "target_source": pl["target_source"], "side_rule": pl["trigger"]["rule"]})
        self._set_status(pl, PL_OPENED, int(as_of_ms), "FILLED_%s" % pos.id)
        pl["position_id"], pl["entry_price"], pl["size"] = pos.id, float(pos.entry_avg), {"qty": float(pos.qty), "notional": float(pos.qty * pos.entry_avg), "leverage": pos.leverage}
        pl["risk"] = {"risk_usdt": round(abs(float(pos.entry_avg) - float(pl["stop"])) * float(pos.qty), 6), "risk_pct_of_start": round(abs(float(pos.entry_avg) - float(pl["stop"])) * float(pos.qty) / float(self.ledger.starting_equity) * 100.0, 4)}
        self._set_status(pl, PL_MANAGED, int(as_of_ms), "")
        self.counters["opened"] += 1
        self._event("PLAN_OPENED", symbol, pl["family"], now, plan_id=pl["plan_id"], side=pl["side"], entry=float(pos.entry_avg), stop=float(pl["stop"]), target=float(pl["target"]), position_id=pos.id)
        # karşı plan(lar) iptal: bir yön gerçekleşince aynı sembolde bekleyen tüm planlar kapanır (tersleme yok)
        for q in self.plans.values():
            if q.get("symbol") == symbol and q.get("plan_id") != pl["plan_id"] and q.get("status") in (PL_AWAITING, PL_TRIGGERED):
                self._set_status(q, PL_CANCELLED, int(as_of_ms), "OTHER_PLAN_FILLED:%s" % pl["plan_id"])
                self.counters["cancelled"] += 1
        try:
            self.memory.record_entry({"trade_id": pos.id, "symbol": symbol, "direction": pos.side.value, "market_type": "USDM_PERP", "setup_type": pl["family"],
                                      "regime": pl.get("evidence", {}).get("htf_trend"), "features": {"plan_id": pl["plan_id"], "family": pl["family"], "side": pl["side"],
                                      "cohort": pl.get("cohort"), "age_h_at_entry": universe_entry.get("age_h"), "rr_after_cost": n, "target_source": pl["target_source"],
                                      "finding_ids": list(pl.get("finding_ids") or []), "evidence": pl.get("evidence")}, "run_id": self.run_id, "in_test": True})
        except Exception as exc:  # noqa: BLE001
            log.warning("formasyon defteri giriş belleği yazılamadı (%s): %s", symbol, exc)
        return "OPENED"

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
    def tick(self, marks: dict[str, TickData], *, now: datetime, funding_rate_lookup=None, bar_advance: bool = False) -> list:
        with self.lock:
            recs = self.ledger.tick(marks, now_utc=now, funding_rate_lookup=funding_rate_lookup, bar_advance=bar_advance)
            for rec in recs:
                self._on_closed(rec)
            return recs

    def apply_closed_bars(self, bars_by_symbol: dict[str, dict[str, Any]] | None, *, now: datetime, funding_rate_lookup=None) -> list:
        with self.lock:
            return apply_closed_bars_to_ledger(self.ledger, bars_by_symbol, now=now, funding_rate_lookup=funding_rate_lookup, on_closed=self._on_closed,
                                               on_event=lambda sym, kind, reason, at, **extra: self._event(kind, sym, reason, at, **extra))

    def record_gaps(self, gaps: dict[str, dict[str, Any]], now: datetime) -> None:
        with self.lock:
            for sym, g in (gaps or {}).items():
                prev = self.data_gaps.get(sym)
                if not prev or prev.get("reason") != g.get("reason"):
                    self._event("PRICE_GAP", sym, str(g.get("reason") or "NO_VERIFIED_FUTURES_PRICE"), now, detail=g.get("detail"))
                self.data_gaps[sym] = dict(g)
            for sym in [s for s in self.data_gaps if s not in (gaps or {}) and s in self.ledger.positions]:
                self._event("PRICE_RESTORED", sym, "", now)
                self.data_gaps.pop(sym, None)


__all__ = ["BOOK_KEY", "BOOK_NAME", "SUMMARY_FILE", "SCHEMA_VERSION", "PatternBook"]
