# -*- coding: utf-8 -*-
"""M2X AYNA DEFTERİ (2026-10-05) — M2'nin GERÇEKTEN açtığı işlemleri kendi kâğıt defterinde daha büyük boyutla kopyalar.

PAPER. Gerçek para yok. Ön kayıt: `docs/M2_AGGRESSIVE_V1.md` (b580af1); politika `m2x_policy.M2X_POLICY_V1` (mühürlü,
TEK kaynak; simülasyon `m2x_sim.M2xRunner` aynı fonksiyonları çağırır). Bu modül politikanın CANLI motordaki defteridir:

* **Girdi (§1.2):** M2'nin `step`inden hemen sonra (motor, M2'nin döngüsünde) `capture_parent_step` M2'nin bu adımda
  açtığı pozisyonları (aday) ve kural/yapı ile kapattıklarını (eşlenecek çıkış) YALNIZ OKUYARAK yakalar. Kural bir daha
  koşturulmaz; karşı-olgusal ya da yalnız-kayıt adaylar kopyalanmaz.
* **Tur (§1.4, §2.7):** bütün kâğıt defterlerden SONRA `tour_step`: M2'nin kural çıkışını aynı mark'la eşle → kapanmış 1h
  bar uçları (turun `pbars`ı) → tik (turun `pmarks`ı) → yetim mutabakatı → (bellekten) geç funding → sahip yeniden
  başlatma isteği → gözlem (O1 günlük görüntü / O2 giriş turu) → yeni girişler (`m2x_policy`) → kayıt.
* **Yalıtım (§1.6):** kendi fiyat/ağ erişimi YOKTUR (turun `pmarks`/`pbars`ı ve izleyicinin tek fiyat partisi); M2'nin
  defterine, öğrenmeye, karşı-olgusala, ortak deneyime, danışmana, yapı deposuna, `TradeMemory`ye YAZMAZ. Yazdığı her
  şey `state/<state_dir>/` altında, `state/<state_dir>.json` özetinde ve motorun indeks girdisindedir.
* **Kalıcılık:** defter `futures_ledger.json`; politika durumu `m2x_state.json` (zirveler, kademe, sayaç, U serisi,
  durdurma, dönem, günlük görüntüler); karar kaydı `m2x_events.jsonl` (yalnız ekleme); sahip isteği `m2x_control.json`.
* **Durdurma (§2.8):** DD ≥ %50 → `HALTED` (kalıcı). Yalnız sahip açar: `python -m tradingbot m2x-resume --i-reviewed
  --note "<neden>"` kontrol dosyasına tek kullanımlık istek yazar; worker sonraki turda dönem/durum denetimiyle işler.
"""
from __future__ import annotations

import json
import logging
import statistics
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from . import m2x_policy as P
from .accounting import (AmountType, FeeSchedule, FuturesLedgerV2, LiquidationParams, MarketType, SizeSpec,
                         SlippageModel, TaxPolicy, default_brackets, default_filters)
from .accounting.futures_ledger import EXIT_LIQ
from .core import atomic_write_json, from_iso, iso, utc_now

log = logging.getLogger(__name__)

BOOK_NAME = "m2x_aggressive"
KIND = "mirror"
LABEL_TR = "M2X · agresif M2 kopyası (PAPER)"
STATE_FILE = "m2x_state.json"
CONTROL_FILE = "m2x_control.json"
EVENTS_FILE = "m2x_events.jsonl"
STATE_SCHEMA = "m2x_state_v1"
SUMMARY_SCHEMA = "m2x_summary_v1"
CONTROL_SCHEMA = "m2x_control_v1"
#: Çıkış nedenleri (§1.4).
MIRROR_PARENT_CLOSED = "MIRROR_PARENT_CLOSED"
MIRROR_ORPHAN_CLOSED = "MIRROR_ORPHAN_CLOSED"
#: Atlama nedeni: aynı sembolde M2X'in hâlâ açık (yetim) pozisyonu var.
SKIP_SYMBOL_HELD = "M2X_SYMBOL_HELD"
#: Atlama nedeni: motorun kill switch'i devrede (RiskEngine'in ilk kapısıyla aynı anlam).
SKIP_KILL_SWITCH = "M2X_KILL_SWITCH"
#: Sahip yeniden başlatma sonuçları (§2.8).
RESUME_ACCEPTED = "ACCEPTED"
RESUME_NOT_HALTED = "REJECTED_NOT_HALTED"
RESUME_STALE_EPOCH = "REJECTED_STALE_EPOCH"
#: Yeniden başlatmada kademe K3 (§2.8): sahip inceledi ama temkin sürer.
RESUME_TIER = 2
#: Durum ve kayıt sınırları.
DAILY_KEEP = 400
EVENTS_KEEP = 200
RESUME_KEEP = 20
RECENT_SKIPS_KEEP = 50
U_TAIL_IDS = 20
PENDING_CHECK_MAX_TOURS = 200
#: Günlük ve aylık hedef (yalnız rapor; hüküm ölçüsü DEĞİL — ön kayıt Bağlam ve §3.5).
DAILY_TARGET_PCT = 1.0
MONTHLY_TARGET_PCT = 1.0

STATUS_RUNNING, STATUS_SOFT_HALT, STATUS_HALTED = "ÇALIŞIYOR", "YUMUŞAK DURDURMA", "DURDURULDU"


def _now_ms_default() -> int:
    return int(utc_now().timestamp() * 1000)


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _round(x: Any, nd: int = 6) -> float | None:
    v = _f(x)
    return None if v is None else round(v, nd)


def _day_of(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def filter_kw(filt: Any) -> dict[str, float]:
    """`plan_entry`e borsa filtreleri — M2 defterinin `fit_size` çağrısı ve simülasyonla AYNI (`m2x_sim._filter_kw`)."""
    return {"min_notional": float(filt.min_notional), "qty_step": float(filt.qty_step), "min_qty": float(filt.min_qty)}


def size_spec(pl: P.EntryPlan) -> SizeSpec:
    """Defter emri — simülasyonla AYNI (`m2x_sim._size_spec`): çıkarılmış (BUMP) giriş planın adıma YUKARI yuvarlanmış
    MİKTARIyla, diğerleri notional'la."""
    from .learning_mode import SIZE_BUMP
    if pl.size_rule == SIZE_BUMP:
        return SizeSpec(Decimal(repr(float(pl.qty))), AmountType.QUANTITY, int(pl.leverage))
    return SizeSpec(Decimal(repr(float(pl.notional))), AmountType.NOTIONAL, int(pl.leverage))


def _pos_seq(pid: Any) -> tuple[int, str]:
    """M2 pozisyon kimliği (`F00012`) → açılış sırası (deterministik)."""
    s = str(pid or "")
    digits = "".join(ch for ch in s if ch.isdigit())
    return (int(digits) if digits else 10 ** 12, s)


class M2xBook:
    """M2X ayna defteri — canlı motorda `engine.m2x_book` (AYRI öznitelik; `strategy_books` listesine GİRMEZ)."""

    def __init__(self, cfg: Any, *, section: Any, filters_cache: Any = None, profile: Any = None,
                 funding_rates: Any = None, killswitch: Any = None) -> None:
        v3 = cfg.v3
        self.cfg = cfg
        self.section = section
        self.name = BOOK_NAME
        self.kind = KIND
        self.parent_name = str(section.parent)
        self.key = str(section.state_dir)
        self.summary_file = "%s.json" % self.key
        self.new_entries = bool(section.new_entries)
        self.filters_cache = filters_cache
        self.profile = profile
        #: motorun kill switch'i (yalnız OKUNUR): devredeyken yeni giriş yok (M2 de açamaz; aynı turdaki yarış için kapı)
        self.killswitch = killswitch
        self.state_dir = Path(cfg.state_path) / self.key
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.state_dir / "futures_ledger.json"
        self.state_file = self.state_dir / STATE_FILE
        self.control_file = self.state_dir / CONTROL_FILE
        self.events_file = self.state_dir / EVENTS_FILE
        # DEFTER: M2 ile AYNI ücret/kayma/likidasyon parametreleri (§1.5, §2.5); başa-baş yok; azami 40 pozisyon.
        fees = FeeSchedule(maker_pct=Decimal(str(v3.fees.futures_maker_pct)), taker_pct=Decimal(str(v3.fees.futures_taker_pct)),
                           source=v3.fees.source)
        self.ledger = FuturesLedgerV2.load(self.ledger_path, starting_equity=float(section.starting_equity_usdt),
                                           max_positions=int(P.M2X_POLICY_V1["max_positions"]), enforce_position_cap=True,
                                           fees=fees, slippage=SlippageModel(fixed_bps=Decimal(str(v3.fees.slippage_bps))),
                                           brackets=default_brackets(),
                                           liq_params=LiquidationParams(liq_fee_pct=Decimal(str(v3.futures_v3.liq_fee_pct))),
                                           tp1_fraction=Decimal(str(v3.futures_v3.tp1_fraction)),
                                           breakeven_at_mfe_r=Decimal(0), tax_policy=TaxPolicy.disabled())
        #: Gerçekleşmiş funding kaynağı (motorun BELLEKTEKİ `FundingRates`i; ağ adımı motorundur, M2X onu ÇAĞIRMAZ).
        self.funding_rates = funding_rates
        if funding_rates is not None:
            self.ledger.funding.bind_source(funding_rates)
        else:
            self.ledger.funding.fallback_to_last_known = False
        self.lock = threading.RLock()
        #: yalnız yakalama listeleri için KISA kilit: M2'nin döngüsündeki yakalama, izleyicinin M2X geçişini (kayıt G/Ç'si)
        #: BEKLEMEZ — sonraki defterlerin (D4/C4) zamanlaması M2X yüzünden kaymaz
        self._cap_lock = threading.Lock()
        #: koruyucu izleyicinin ölçümü (`protective_monitor.ObservationLog`); None → ölçüm yok
        self.observer: Any = None
        self._pending: list[dict[str, Any]] = []         # bu turda yakalanan M2 açılışları (aday)
        self._rule_closes: list[dict[str, Any]] = []     # bu turda M2'nin adımında kapanan pozisyonlar
        self._regime_now: dict[str, Any] | None = None
        self._parent: Any = None                         # son turdaki M2 defteri (yalnız OKUNUR)
        #: sembol → (fiyat, fiyat zamanı ms): bu defterde EN SON UYGULANAN doğrulanmış mark (tur ya da izleyici)
        self._last_marks: dict[str, tuple[float, int]] = {}
        self.data_events: list[dict[str, Any]] = []
        self.data_gaps: dict[str, dict[str, Any]] = {}
        self.closed_recent: list[dict[str, Any]] = []
        self._missed_checked = False
        self._load_state()
        # İZLEME KESİNTİSİ: StrategyBook ile aynı kural; kayıtlar M2X'in KENDİ klasörüne yazılır (yalıtım §1.7).
        self._resume_saved_at = self.ledger.updated_at
        self._resume_positions: list[str] = sorted(self.ledger.positions)
        self._resume_checked = False
        self._gap_until_ms: int | None = None
        self.monitoring_gap: dict[str, Any] | None = None

    # ------------------------------------------------------------------ durum dosyası
    def _fresh_meta(self) -> dict[str, Any]:
        # Zaman alanları İLK TURDA turun karar saatiyle doldurulur (`_birth`): duvar saati değil, defterin kendi saati.
        se = float(self.ledger.starting_equity)
        return {"schema": STATE_SCHEMA, "policy_version": P.POLICY_VERSION, "policy_sha": P.M2X_POLICY_SHA,
                "created_at": None, "epoch": 0, "epoch_started_at": None, "epoch_start_equity": se,
                "peak_at": None, "tier_since": None, "halt": None, "last_snapshot_day": None, "daily": [],
                "events": [], "last_observation": None, "last_tour_at": None, "tours": 0,
                "u": {"since": None, "closed_sum": 0.0, "n_closed": 0, "tail_ids": [], "initialised": False},
                "skips": {}, "skips_by_tier": {}, "entries": {"n": 0, "full": 0, "scaled": 0, "bump": 0},
                "divergences": {}, "parity": {"matched": 0, "sum_dr": 0.0, "liq_div_cost_r": 0.0},
                "pending_checks": [], "binding_cap": "-", "binding_hist": {}, "max_or_e": 0.0, "max_cl_e": 0.0,
                "resume_history": [], "control_processed": [], "parent_regime": None, "missed_seen": [],
                "recent_skips": [], "last_lambda": None, "parent_key": None, "parent_enabled": None}

    def _load_state(self) -> None:
        meta = self._fresh_meta()
        doc = None
        if self.state_file.exists():
            try:
                doc = json.loads(self.state_file.read_text(encoding="utf-8"))
                if not isinstance(doc, dict) or doc.get("schema") != STATE_SCHEMA:
                    raise ValueError("şema")
            except (OSError, ValueError) as exc:
                # bozuk durum kenara alınır (silinmez); politika BAŞTAN ve ihtiyatlı başlar (zirve = başlangıç)
                log.warning("M2X durum dosyası okunamadı (%s): %s — kenara alındı", self.state_file, exc)
                try:
                    self.state_file.replace(self.state_file.with_name(self.state_file.name + ".corrupt"))
                except OSError:
                    pass
                doc = None
        if doc:
            for k, v in doc.items():
                if k in meta and k not in ("schema", "policy_version", "policy_sha"):
                    meta[k] = v
            if doc.get("policy_sha") not in (None, P.M2X_POLICY_SHA):
                meta["events"] = (list(meta.get("events") or []) + [{"at": iso(), "kind": "POLICY_SHA_CHANGED",
                                                                      "from": doc.get("policy_sha"),
                                                                      "to": P.M2X_POLICY_SHA}])[-EVENTS_KEEP:]
        self.meta = meta
        pol = (doc or {}).get("policy") if isinstance((doc or {}).get("policy"), dict) else None
        se = float(self.ledger.starting_equity)
        if pol:
            self.st = P.PolicyState(peak=float(pol.get("peak", se)), tier_peak=float(pol.get("tier_peak", se)),
                                    tier=int(pol.get("tier", 0)), halted=bool(pol.get("halted", False)),
                                    halted_at=pol.get("halted_at"), streak=int(pol.get("streak", 0)),
                                    last_key=pol.get("last_key"),
                                    u_hist=[(str(a), (None if u is None else float(u))) for a, u in (pol.get("u_hist") or [])])
        else:
            self.st = P.PolicyState.start(se)

    def _state_doc(self) -> dict[str, Any]:
        st = self.st
        d = dict(self.meta)
        d.update({"schema": STATE_SCHEMA, "policy_version": P.POLICY_VERSION, "policy_sha": P.M2X_POLICY_SHA,
                  "saved_at": iso(),
                  "policy": {"peak": st.peak, "tier_peak": st.tier_peak, "tier": st.tier, "halted": st.halted,
                             "halted_at": st.halted_at, "streak": st.streak, "last_key": st.last_key,
                             "u_hist": [[a, u] for a, u in st.u_hist]}})
        return d

    # ------------------------------------------------------------------ yardımcılar
    @staticmethod
    def parent_id(pos: Any) -> str | None:
        m = ((getattr(pos, "meta", None) or {}).get("m2x") or {}) if isinstance(getattr(pos, "meta", None), dict) else {}
        v = m.get("m2_id")
        return str(v) if v is not None else None

    def _filters(self, sym: str) -> Any:
        fc = self.filters_cache
        if fc is not None:
            try:
                return fc.get(sym, MarketType.USDM_PERP)
            except TypeError:
                return fc.get(sym)
        return default_filters(sym, MarketType.USDM_PERP)

    def _max_leverage(self, filt: Any) -> int:
        lmax = int(P.M2X_POLICY_V1["leverage_max"])
        for cap in (getattr(self.profile, "futures_max_leverage", None), getattr(filt, "max_leverage", None)):
            if cap:
                lmax = min(lmax, int(cap))
        return max(1, lmax)

    def _log_event(self, row: dict[str, Any]) -> None:
        """Karar kaydı (yalnız ekleme). Arıza kararı ETKİLEMEZ."""
        try:
            with open(self.events_file, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            log.warning("M2X karar kaydı yazılamadı: %s", exc)

    def _policy_event(self, ev: dict[str, Any]) -> None:
        self.meta["events"] = (list(self.meta.get("events") or []) + [dict(ev)])[-EVENTS_KEEP:]
        self._log_event({"kind": "policy", **ev})

    def _bump(self, path: str, key: str, n: int = 1) -> None:
        d = self.meta.setdefault(path, {})
        d[key] = int(d.get(key, 0)) + int(n)

    def _skip(self, cand: dict[str, Any], reason: str, now: datetime, **extra: Any) -> None:
        tier = self.st.tier_name
        self._bump("skips", reason)
        bt = self.meta.setdefault("skips_by_tier", {}).setdefault(tier, {})
        bt[reason] = int(bt.get(reason, 0)) + 1
        row = {"at": iso(now), "m2_id": cand.get("m2_id"), "symbol": cand.get("symbol"), "reason": reason, "tier": tier,
               **{k: v for k, v in extra.items() if v is not None}}
        self.meta["recent_skips"] = (list(self.meta.get("recent_skips") or []) + [row])[-RECENT_SKIPS_KEEP:]
        self._log_event({"kind": "skip", **row, "m2_learning": cand.get("learning")})

    def _data_event(self, symbol: str, kind: str, reason: str, at: datetime, **extra: Any) -> None:
        ev = {"symbol": symbol, "kind": kind, "reason": reason, "at": iso(at), **extra}
        self.data_events = (self.data_events + [ev])[-50:]

    def _record_gaps(self, gaps: dict[str, dict] | None, now: datetime) -> None:
        for sym, g in (gaps or {}).items():
            prev = self.data_gaps.get(sym)
            if not prev or prev.get("reason") != g.get("reason"):
                self._data_event(sym, "PRICE_GAP", str(g.get("reason") or "NO_VERIFIED_FUTURES_PRICE"), now)
            self.data_gaps[sym] = dict(g)
        for sym in [s for s in self.data_gaps if s not in (gaps or {})]:
            self._data_event(sym, "PRICE_RESTORED", "", now)
            self.data_gaps.pop(sym, None)

    def _resume_once(self, now: datetime) -> None:
        """İZLEME KESİNTİSİ (StrategyBook ile aynı kural): bu süreçteki İLK etkinlikte bir kez; kayıt M2X klasörüne."""
        if self._resume_checked:
            return
        self._resume_checked = True
        from .strategy_paper import monitoring_gap_on_resume
        self._gap_until_ms = monitoring_gap_on_resume(self.ledger, self._resume_saved_at, now=now, book_key=self.key,
                                                      state_path=self.state_dir, positions=self._resume_positions)
        if self._gap_until_ms is not None:
            self.monitoring_gap = {"from": str(self._resume_saved_at), "to": iso(now), "positions": list(self._resume_positions)}

    def held_ids(self) -> dict[str, str]:
        with self.lock:
            return {s: str(p.id) for s, p in self.ledger.positions.items()}

    def index_entry(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name, "kind": self.kind, "parent": self.parent_name,
                "summary_file": self.summary_file}

    # ------------------------------------------------------------------ fiyat ve ölçüler
    def _after_tick(self, info: dict[str, Any], recs: list, now: datetime, source: str) -> None:
        applied = info.get("applied") or {}
        for sym, a in applied.items():
            ts = a.get("price_ts_ms") or a.get("applied_ms")
            px = _f(a.get("price"))
            if px is not None and ts is not None:
                prev = self._last_marks.get(sym)
                if prev is None or int(ts) >= int(prev[1]):
                    self._last_marks[sym] = (float(px), int(ts))
        if not applied:
            return
        if self.monitoring_gap:
            from .protective_monitor import note_gap_first_observations
            note_gap_first_observations(self.monitoring_gap, book_key=self.key, state_path=self.state_dir,
                                        applied=applied, now=now, closed_ids=[str(getattr(r, "id", "")) for r in recs])
        if self.observer is not None:
            from .strategy_paper import parse_ts_ms
            opened = {s: parse_ts_ms(p.opened_at) or 0 for s, p in self.ledger.positions.items()}
            self.observer.note(self.key, applied, int(now.timestamp() * 1000), opened_ms=opened, source=source)

    def _mark_view(self, now_ms: int) -> tuple[float, list[str], dict[str, float]]:
        """(E, boşluk sembolleri, kullanılan mark'lar). E = cüzdan + Σ q·(m − giriş) (§2.1). Her açık sembolde bu defterde
        EN SON UYGULANAN doğrulanmış mark (turun ya da izleyicinin; hangisi tazeyse) — yaşı ≤ PRICE_MAX_AGE_S değilse
        sembol boşluktur (fail-closed; değer yalnız gösterim için son bilinen fiyatla hesaplanır)."""
        from .strategy_paper import PRICE_FUTURE_SKEW_S, PRICE_MAX_AGE_S
        led = self.ledger
        e = float(led.wallet_balance)
        gaps: list[str] = []
        marks: dict[str, float] = {}
        for s, p in led.positions.items():
            lm = self._last_marks.get(s)
            if lm is None or now_ms - int(lm[1]) > PRICE_MAX_AGE_S * 1000 or int(lm[1]) - now_ms > PRICE_FUTURE_SKEW_S * 1000:
                gaps.append(s)
                m = float(lm[0]) if lm is not None else float(p.last_price or p.entry_avg)
            else:
                m = float(lm[0])
                marks[s] = m
            e += float(p.qty) * (m - float(p.entry_avg))
        return e, gaps, marks

    def _views(self, marks: dict[str, float]) -> list[P.PosView]:
        out = []
        for s, p in self.ledger.positions.items():
            m = marks.get(s)
            if m is None:
                lm = self._last_marks.get(s)
                m = float(lm[0]) if lm is not None else float(p.last_price or p.entry_avg)
            st = p.stop if p.stop is not None else p.initial_stop
            out.append(P.PosView(s, float(p.qty), float(p.entry_avg), float(st) if st is not None else None,
                                 float(p.liquidation_price) if p.liquidation_price else None, float(m)))
        return out

    # ------------------------------------------------------------------ M2'nin adımı (yakalama; YALNIZ OKUR)
    def capture_parent_step(self, parent: Any, held_before: dict[str, str], *, now: datetime,
                            regime: dict[str, Any] | None = None) -> dict[str, int]:
        """M2'nin `step`inden HEMEN sonra: bu adımda AÇILAN M2 pozisyonları (aday) ve kural/yapı ile KAPANANLAR (eşlenecek
        çıkış). M2'nin defterine YAZMAZ; pozisyon sözlüğü kopyala-yaz olduğundan okunan anlık görüntü tutarlıdır. Adımın
        zamanı (`now`) denetlenir: bu arada koruyucu izleyicinin kapattığı pozisyon kural çıkışı sayılmaz."""
        after = parent.held_ids()
        now_iso = iso(now)
        led = parent.ledger
        positions = led.positions
        before_ids = {str(v) for v in (held_before or {}).values()}
        opens: list[dict[str, Any]] = []
        for sym, pid in after.items():
            if str(pid) in before_ids:
                continue
            p = positions.get(sym)
            if p is None or str(p.id) != str(pid) or str(p.opened_at) != now_iso:
                continue
            opens.append(dict(self._candidate(p, regime), tour_at=now_iso))
        closes: list[dict[str, Any]] = []
        gone = {s: str(pid) for s, pid in (held_before or {}).items() if str(after.get(s) or "") != str(pid)}
        tail = list(led.history)[-400:]
        # adımda açılıp yakalamadan ÖNCE (ör. izleyici) kapanan M2 pozisyonu da adaydır: işlenirken eşi açık olmadığından
        # `M2X_PARENT_ALREADY_CLOSED` sayılır (§1.2: "aynı adımda açılıp kapananlar M2'nin geçmişinden bulunur")
        after_ids = {str(v) for v in after.values()}
        for h in tail:
            hid = str(h.id)
            if str(h.opened_at) == now_iso and hid not in before_ids and hid not in after_ids:
                opens.append({"m2_id": hid, "symbol": str(h.symbol), "side": str(h.side).upper(), "ref": None,
                              "fill": format(h.entry, "f"), "stop": None, "opened_at": str(h.opened_at),
                              "m2_closed_in_capture": str(h.exit_reason), "learning": None, "tour_at": now_iso})
        if gone:
            by_id = {str(h.id): h for h in tail}
            for sym, pid in gone.items():
                h = by_id.get(pid)
                if h is None or str(h.closed_at) != now_iso:
                    continue
                closes.append({"m2_id": pid, "symbol": sym, "reason": str(h.exit_reason), "tour_at": now_iso})
        opens.sort(key=lambda c: _pos_seq(c["m2_id"]))
        with self._cap_lock:
            self._pending.extend(opens)
            self._rule_closes.extend(closes)
            if regime is not None:
                self._regime_now = dict(regime)
        return {"opens": len(opens), "closes": len(closes)}

    @staticmethod
    def _candidate(p: Any, regime: dict[str, Any] | None) -> dict[str, Any]:
        feats = p.features if isinstance(p.features, dict) else {}
        meta = p.meta if isinstance(p.meta, dict) else {}
        lr = feats.get("learning") if isinstance(feats.get("learning"), dict) else None
        st = p.initial_stop if p.initial_stop is not None else p.stop
        sig = meta.get("signal") if isinstance(meta.get("signal"), dict) else {}
        risk = _f((lr or {}).get("risk_usdt"))
        if risk is None and st is not None:
            risk = float(p.qty) * abs(float(p.entry_avg) - float(st))
        return {"m2_id": str(p.id), "symbol": str(p.symbol), "side": str(p.side.value).upper(),
                "ref": str(meta.get("ref_entry") or format(p.entry_avg, "f")), "fill": format(p.entry_avg, "f"),
                "stop": format(st, "f") if st is not None else None, "opened_at": str(p.opened_at),
                "m2_qty": format(p.qty, "f"), "m2_leverage": int(p.leverage), "m2_risk_usdt": risk,
                "signal_ts": sig.get("signal_ts", feats.get("signal_ts")),
                "learning": {"active": (regime or {}).get("learning_on"), "extra_entries": (regime or {}).get("extra_entries"),
                             "unlocked_by": list((lr or {}).get("learning_unlocked_by") or []),
                             "class": ("learning_extra" if (lr or {}).get("learning_unlocked_by") else "policy")}}

    # ------------------------------------------------------------------ kapanış kaydı ve parite
    def _on_closed(self, rec: Any, how: str) -> None:
        if rec is None:
            return
        feats = rec.features if isinstance(rec.features, dict) else {}
        mx = feats.get("m2x") if isinstance(feats.get("m2x"), dict) else {}
        row = {"id": str(rec.id), "m2_id": mx.get("m2_id"), "symbol": rec.symbol, "how": how,
               "exit_reason": str(rec.exit_reason), "net_pnl": float(rec.net_pnl), "r": float(rec.r_multiple),
               "funding": float(rec.funding), "fees": float(rec.fees), "closed_at": rec.closed_at,
               "tier_at_entry": mx.get("tier"), "liq": str(rec.exit_reason) == EXIT_LIQ,
               "exit_basis": ((feats.get("exit_fill") or {}) if isinstance(feats.get("exit_fill"), dict) else {}).get("basis")}
        self.closed_recent = (self.closed_recent + [row])[-50:]
        self._log_event({"kind": "close", "at": rec.closed_at, **row})
        if how == "orphan":
            self._bump("divergences", "orphan")
        if mx.get("m2_id"):
            self.meta["pending_checks"] = list(self.meta.get("pending_checks") or []) + [
                {"id": str(rec.id), "m2_id": str(mx["m2_id"]), "reason": str(rec.exit_reason), "r": float(rec.r_multiple),
                 "how": how, "tours": 0}]

    def _resolve_checks(self, parent: Any) -> None:
        """M2X kapanışını M2'nin aynı işleminin kaydıyla eşler: eşleşen işlem başına R farkı ve LİKİDASYON AYRIŞMASI
        (§1.4a: M2X likide, M2 başka nedenle çıktı) ile ters durum (M2 likide, M2X değil). Yalnız OKUR."""
        pend = list(self.meta.get("pending_checks") or [])
        if not pend:
            return
        if parent is None:
            return
        by_id = {str(h.id): h for h in list(parent.ledger.history)[-1000:]}
        open_ids = {str(p.id) for p in parent.ledger.positions.values()}
        keep = []
        par = self.meta.setdefault("parity", {"matched": 0, "sum_dr": 0.0, "liq_div_cost_r": 0.0})
        for c in pend:
            h = by_id.get(str(c["m2_id"]))
            if h is None:
                c["tours"] = int(c.get("tours", 0)) + 1
                if str(c["m2_id"]) in open_ids and c["tours"] < PENDING_CHECK_MAX_TOURS:
                    keep.append(c)
                elif c["tours"] >= PENDING_CHECK_MAX_TOURS or str(c["m2_id"]) not in open_ids:
                    self._bump("divergences", "unmatched")
                continue
            m2_r, m2_liq = float(h.r_multiple), str(h.exit_reason) == EXIT_LIQ
            mine_liq = str(c["reason"]) == EXIT_LIQ
            par["matched"] = int(par.get("matched", 0)) + 1
            par["sum_dr"] = float(par.get("sum_dr", 0.0)) + (float(c["r"]) - m2_r)
            if mine_liq and not m2_liq:
                self._bump("divergences", "liquidation")
                par["liq_div_cost_r"] = float(par.get("liq_div_cost_r", 0.0)) + (float(c["r"]) - m2_r)
            elif m2_liq and not mine_liq:
                self._bump("divergences", "m2_liquidated_only")
            self._log_event({"kind": "parity", "at": iso(), "id": c["id"], "m2_id": c["m2_id"], "r": c["r"], "m2_r": m2_r,
                             "reason": c["reason"], "m2_reason": str(h.exit_reason)})
        self.meta["pending_checks"] = keep

    # ------------------------------------------------------------------ U (M2'nin birim-R eğrisi; §2.1)
    def _u_scan(self, parent: Any) -> None:
        if parent is None:
            return
        u = self.meta.setdefault("u", {"since": iso(), "closed_sum": 0.0, "n_closed": 0, "tail_ids": [], "initialised": False})
        hist = list(parent.ledger.history)
        since = _ts(u.get("since"))
        if not u.get("initialised"):
            new = [h for h in hist if since is not None and (_ts(h.closed_at) or since) >= since]
            u["initialised"] = True
        else:
            tail = set(str(x) for x in (u.get("tail_ids") or []))
            idx = None
            for i in range(len(hist) - 1, -1, -1):
                if str(hist[i].id) in tail:
                    idx = i
                    break
            if idx is None:
                new = [h for h in hist if since is not None and (_ts(h.closed_at) or since) >= since] if tail else hist
                if tail and hist:
                    self._policy_event({"at": iso(), "kind": "U_TAIL_LOST", "note": "M2 geçmişi değişti; U yeniden tarandı"})
                    u["closed_sum"], u["n_closed"] = 0.0, 0
            else:
                new = hist[idx + 1:]
        for h in new:
            u["closed_sum"] = float(u.get("closed_sum", 0.0)) + float(h.r_multiple)
            u["n_closed"] = int(u.get("n_closed", 0)) + 1
        u["tail_ids"] = [str(h.id) for h in hist[-U_TAIL_IDS:]]

    def _u_value(self, parent: Any, pmarks_f: dict[str, float]) -> float | None:
        """U = Σ (M2X başlangıcından beri kapanan M2 işlemlerinin net R'si) + Σ (M2'nin açık pozisyonlarının mark R'si).
        M2'nin açık bir pozisyonunun bu turda doğrulanmış mark'ı yoksa None (o günün B'si geçmez)."""
        if parent is None:
            return None
        u = float((self.meta.get("u") or {}).get("closed_sum", 0.0))
        for s, p in parent.ledger.positions.items():
            m = pmarks_f.get(s)
            st = p.initial_stop if p.initial_stop is not None else p.stop
            if m is None or st is None or float(p.entry_avg) <= float(st):
                return None
            u += (float(m) - float(p.entry_avg)) / (float(p.entry_avg) - float(st))
        return u

    # ------------------------------------------------------------------ izleyici (60 sn; AĞ YOK)
    def protect(self, marks: dict, marks_f: dict, gaps: dict | None, *, now: datetime, expect: dict | None = None,
                source: str = "monitor", funding_rate_lookup: Any = "__book__", apply_clock: Callable[[], int] | None = None) -> list:
        """KORUYUCU İZLEME ADIMI: izleyicinin bu geçişte aldığı TEK fiyat partisiyle (ağ yok) kimlik/sıra korumalı tik →
        kayıt. M2X tutamacı listede EN SONDADIR; sembolleri M2'ninkilerin alt kümesi olduğundan parti değişmez."""
        from .protective_monitor import guarded_tick
        frl = self.funding_rates if funding_rate_lookup == "__book__" else funding_rate_lookup
        with self.lock:
            self._resume_once(now)
            self._record_gaps(gaps or {}, now)
            recs: list = []
            if marks:
                recs, info = guarded_tick(self.ledger, marks, now=now, funding_rate_lookup=frl, bar_advance=False,
                                          expect=expect, apply_clock=apply_clock)
                for r in recs:
                    self._on_closed(r, "monitor")
                self._after_tick(info, recs, now, source)
            self._save(now, write_state=bool(recs))
            return recs

    # ------------------------------------------------------------------ TUR (§1.4)
    def tour_step(self, parent: Any, *, now: datetime, tick_now: datetime, pmarks: dict, pmarks_f: dict[str, float],
                  pbars: dict[str, dict] | None = None, bar_advance: bool = False,
                  wall_ms: Callable[[], int] | None = None) -> dict[str, Any]:
        """Bütün kâğıt defterlerden SONRA, turun zaten aldığı `pmarks`/`pbars` ile. `parent` None → M2 config'te kapalı
        (`PARENT_DISABLED`: yeni giriş yok, yetim mutabakatı yok; açık pozisyonlar izleyicinin partisiyle korunur)."""
        wall = wall_ms or _now_ms_default
        with self.lock:
            self._resume_once(now)
            with self._cap_lock:
                cands, closes = self._pending, self._rule_closes
                self._pending, self._rule_closes = [], []
                regime = self._regime_now
            resolved: set[str] = set()
            out: dict[str, Any] = {}
            try:
                out = self._tour_locked(parent, cands, closes, resolved, now=now, tick_now=tick_now, pmarks=pmarks or {},
                                        pmarks_f=pmarks_f or {}, pbars=pbars or {}, bar_advance=bool(bar_advance), wall=wall,
                                        regime=regime)
            finally:
                for c in cands:
                    if c["m2_id"] not in resolved:
                        try:
                            self._skip(c, P.SKIP_MISSED_TOUR, now)
                        except Exception:  # noqa: BLE001
                            pass
                self.meta["last_tour_at"] = iso(now)
                self._save(now, write_state=True)
            return out

    def _tour_locked(self, parent: Any, cands: list[dict[str, Any]], closes: list[dict[str, Any]], resolved: set[str], *,
                     now: datetime, tick_now: datetime, pmarks: dict, pmarks_f: dict[str, float], pbars: dict,
                     bar_advance: bool, wall: Callable[[], int], regime: dict[str, Any] | None = None) -> dict[str, Any]:
        from .protective_monitor import guarded_tick
        from .strategy_paper import apply_closed_bars_to_ledger
        led = self.ledger
        frl = self.funding_rates
        self._parent = parent
        if parent is not None:
            self.meta["parent_key"] = str(getattr(parent, "key", "") or "") or None
        self.meta["parent_enabled"] = parent is not None
        if not self.meta.get("created_at"):
            # DOĞUM: ilk tur. U bu andan sonra kapanan M2 işlemlerini sayar; ısınma (ilk 28 gün) bu andan ölçülür.
            t0 = iso(now)
            self.meta["created_at"] = self.meta["epoch_started_at"] = self.meta["tier_since"] = t0
            self.meta.setdefault("u", {})["since"] = t0
        self.meta["tours"] = int(self.meta.get("tours", 0)) + 1
        # BAYAT YAKALAMA: önceki turda yakalanıp o tur işlenemeyen aday sonradan GİRİLMEZ (§1.3 MISSED_PARENT_TOUR);
        # önceki turun kural çıkışı eşlenmez (yetim mutabakatı bu turun mark'ıyla kapatır, ayrışma sayılır).
        now_iso = iso(now)
        for c in [c for c in cands if c.get("tour_at") != now_iso]:
            resolved.add(c["m2_id"])
            self._skip(c, P.SKIP_MISSED_TOUR, now, stale_from=c.get("tour_at"))
        cands[:] = [c for c in cands if c.get("tour_at") == now_iso]
        closes = [c for c in closes if c.get("tour_at") == now_iso]
        self._note_regime(now, regime)
        self._note_missed(parent, cands, now)
        # 1) M2'nin kural/yapı çıkışı: AYNI mark'la (M2'nin `close_manual` çağrısıyla aynı fiyat ve tik)
        for c in closes:
            sym = c["symbol"]
            pos = led.positions.get(sym)
            if pos is None or self.parent_id(pos) != str(c["m2_id"]) or sym not in pmarks_f:
                continue
            rec = led.close_manual(sym, pmarks_f[sym], reason="%s:%s" % (MIRROR_PARENT_CLOSED, c["reason"]), now=now,
                                   tick=pmarks.get(sym))
            self._on_closed(rec, "mirror")
        # 1b) STOP_SYNC (§1.3): M2 eşinin stopu değiştiyse M2X aynı turda eşler. Bugünkü config'te M2 stop TAŞIMAZ
        #     (başa-baş 0, hedef yok); bu yol yalnız ileride taşınırsa çalışır ve kaydı `meta.m2x_stop_sync`tadır.
        if parent is not None:
            ppos = parent.ledger.positions
            for sym, pos in list(led.positions.items()):
                pp = ppos.get(sym)
                if pp is None or pp.stop is None or str(pp.id) != str(self.parent_id(pos) or ""):
                    continue
                if pos.stop is None or Decimal(pp.stop) != Decimal(pos.stop):
                    old = pos.stop
                    pos.stop = Decimal(pp.stop)
                    pos.meta["m2x_stop_sync"] = {"at": iso(now), "from": (format(old, "f") if old is not None else None),
                                                 "to": format(pos.stop, "f")}
                    self.meta["stop_syncs"] = int(self.meta.get("stop_syncs") or 0) + 1
                    self._log_event({"kind": "stop_sync", "at": iso(now), "symbol": sym, "m2_id": str(pp.id),
                                     "from": pos.meta["m2x_stop_sync"]["from"], "to": pos.meta["m2x_stop_sync"]["to"]})
        # 2) kapanmış 1h bar uçları (turun M2 ile AYNI satırları)
        mine = {s: b for s, b in (pbars or {}).items() if s in led.positions}
        if mine:
            apply_closed_bars_to_ledger(led, mine, now=tick_now, funding_rate_lookup=frl,
                                        on_closed=lambda r: self._on_closed(r, "bar"), on_event=self._data_event,
                                        gap_until_ms=self._gap_until_ms)
        # 3) tik (turun doğrulanmış mark'ı; kimlik/sıra/tazelik korumalı)
        tm = {s: pmarks[s] for s in list(led.positions) if s in pmarks}
        if tm:
            recs, info = guarded_tick(led, tm, now=tick_now, funding_rate_lookup=frl, bar_advance=bar_advance,
                                      expect=None, apply_clock=wall)
            for r in recs:
                self._on_closed(r, "tick")
            self._after_tick(info, recs, tick_now, "tour")
        # 4) yetim mutabakatı: eşi M2'de artık açık olmayan pozisyon bu turun mark'ıyla kapanır (ayrışma sayılır)
        m2_open = parent.held_ids() if parent is not None else None
        if m2_open is not None:
            for sym in list(led.positions):
                pos = led.positions.get(sym)
                if pos is None or str(m2_open.get(sym) or "") == str(self.parent_id(pos) or "-"):
                    continue
                if sym not in pmarks_f:
                    continue                                   # fiyat gelince kapanır (bekleyen yetim)
                rec = led.close_manual(sym, pmarks_f[sym], reason=MIRROR_ORPHAN_CLOSED, now=tick_now, tick=pmarks.get(sym))
                self._on_closed(rec, "orphan")
        # 4b) kapanmış işlemlerin bekleyen funding'i — YALNIZ bellekten (ağ yok)
        if frl is not None:
            try:
                led.settle_late_funding(frl, now=tick_now, hours_for=getattr(frl, "hours_for", None))
            except Exception as exc:  # noqa: BLE001 — funding arızası turu durdurmaz (dönemler bekler)
                log.warning("M2X geç funding uzlaştırması başarısız: %s", exc)
        self._u_scan(parent)
        self._resolve_checks(parent)
        # 5) gözlem (§2.7) + sahip isteği (§2.8)
        now_ms = int(wall())
        e, gap_syms, marks = self._mark_view(now_ms)
        gap = bool(gap_syms)
        self._process_control(now, e, gap)
        day = _day_of(now)
        evs: list[dict[str, Any]] = []
        obs_kind = None
        if self.meta.get("last_snapshot_day") != day:
            if gap:
                self._obs_gap(now, P.SNAPSHOT, gap_syms)
            else:
                self._fill_missed_days(day)
                u = self._u_value(parent, pmarks_f)
                before = self.st.peak
                evs = P.observe(self.st, x=e, kind=P.SNAPSHOT, at=iso(now), u=u)
                if self.st.peak > before:
                    self.meta["peak_at"] = iso(now)
                self.meta["last_snapshot_day"] = day
                self._daily_append(day, now, e, u)
                obs_kind = P.SNAPSHOT
        if cands and obs_kind is None:
            if gap:
                self._obs_gap(now, P.ENTRY, gap_syms)
            else:
                evs = P.observe(self.st, x=e, kind=P.ENTRY, at=iso(now))
                obs_kind = P.ENTRY
        if obs_kind is not None:
            self.meta["last_observation"] = {"at": iso(now), "kind": obs_kind, "data_gap": False, "equity": round(e, 6),
                                             "dd": round(self.st.dd(e), 6), "ddk": round(self.st.ddk(e), 6)}
        for ev in evs:
            self._on_policy_event(ev, now, e)
        o_now, c_now = P.totals(self._views(marks))
        if e > 0:
            self.meta["max_or_e"] = max(float(self.meta.get("max_or_e") or 0.0), o_now / e)
            self.meta["max_cl_e"] = max(float(self.meta.get("max_cl_e") or 0.0), c_now / e)
        # 6) yeni girişler
        n_new = 0
        binding = None
        if cands:
            binding, n_new = self._entries(parent, cands, resolved, now=now, pmarks=pmarks, e=e, gap=gap, o_now=o_now,
                                           c_now=c_now)
            self.meta["binding_cap"] = binding
            bh = self.meta.setdefault("binding_hist", {})
            bh[binding] = int(bh.get(binding, 0)) + 1
        return {"candidates": len(cands), "opened": n_new, "binding": binding, "tier": self.st.tier_name,
                "equity": round(e, 6), "data_gap": gap}

    # ------------------------------------------------------------------ gözlem yardımcıları
    def _obs_gap(self, now: datetime, kind: str, syms: list[str]) -> None:
        """Veri boşluğu (fail-closed): kademe, zirve ve DUR DEĞİŞMEZ; o turda yeni giriş yok. Görüntü aynı gün yeniden denenir."""
        self.meta["last_observation"] = {"at": iso(now), "kind": kind, "data_gap": True, "symbols": sorted(syms)[:20]}
        self._log_event({"kind": "obs_gap", "at": iso(now), "obs": kind, "symbols": sorted(syms)[:20]})

    def _fill_missed_days(self, day: str) -> None:
        """Görüntüsü OLMAYAN günler (gün boyu boşluk ya da kesinti): yukarı çıkış sayacı sıfırlanır ve U serisine boş
        değer girer (`observe(..., data_gap=True)`). En çok `u_lookback + 1` gün işlenir (daha eskisi seride kalmaz)."""
        last = self.meta.get("last_snapshot_day")
        if not last:
            return
        try:
            d0 = datetime.strptime(str(last), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            d1 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return
        missing = []
        d = d0 + timedelta(days=1)
        while d < d1:
            missing.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        if not missing:
            return
        cap = int(P.M2X_POLICY_V1["u_lookback"]) + 1
        for m in missing[-cap:]:
            P.observe(self.st, x=0.0, kind=P.SNAPSHOT, at=m, data_gap=True)
        self._log_event({"kind": "missed_days", "at": iso(), "days": missing[-cap:], "n": len(missing)})

    def _daily_append(self, day: str, now: datetime, e: float, u: float | None) -> None:
        row = {"day": day, "at": iso(now), "equity": round(float(e), 6), "wallet": round(float(self.ledger.wallet_balance), 6),
               "tier": self.st.tier_name, "dd": round(self.st.dd(e), 6), "ddk": round(self.st.ddk(e), 6),
               "peak": round(self.st.peak, 6), "tier_peak": round(self.st.tier_peak, 6), "u": _round(u),
               "n_pos": len(self.ledger.positions), "epoch": int(self.meta.get("epoch", 0))}
        self.meta["daily"] = (list(self.meta.get("daily") or []) + [row])[-DAILY_KEEP:]
        self._log_event({"kind": "snapshot", **row})

    def _on_policy_event(self, ev: dict[str, Any], now: datetime, e: float) -> None:
        kind = ev.get("kind")
        if kind in ("DOWN", "UP", "HALT"):
            self.meta["tier_since"] = iso(now)
        if kind == "HALT":
            self.meta["halt"] = {"at": ev.get("at"), "dd": ev.get("dd"), "equity": e, "peak": ev.get("peak"),
                                 "epoch": int(self.meta.get("epoch", 0))}
            log.warning("M2X DURDURULDU: zirveden düşüş %.1f%% (E %.2f, P %.2f) — yeni giriş yok; sahip incelemesi gerekli "
                        "(python -m tradingbot m2x-resume --i-reviewed --note ...)", 100 * float(ev.get("dd") or 0), e,
                        float(ev.get("peak") or 0))
        self._policy_event({k: v for k, v in ev.items()})

    def _note_regime(self, now: datetime, cur: dict[str, Any] | None) -> None:
        """M2'nin öğrenme rejimi (aktif/askıda, `extra_entries` kipi) değişirse "girdi rejimi değişti" olayı (§1.2)."""
        if cur is None:
            return
        prev = self.meta.get("parent_regime")
        if prev != cur:
            if prev is not None:
                self._policy_event({"at": iso(now), "kind": "INPUT_REGIME_CHANGED", "from": prev, "to": cur})
            self.meta["parent_regime"] = dict(cur)

    def _note_missed(self, parent: Any, cands: list[dict[str, Any]], now: datetime) -> None:
        """Bu süreç açılmadan önce (M2X turu işlenemediği aralıkta) M2'nin açtığı pozisyonlar: giriş YOK, bir kez
        `MISSED_PARENT_TOUR` sayılır (trend ortasında sonradan girilmez)."""
        if parent is None or not self.meta.get("last_tour_at") or getattr(self, "_missed_checked", False):
            return
        self._missed_checked = True
        last = _ts(self.meta.get("last_tour_at"))
        if last is None:
            return
        seen = set(str(x) for x in (self.meta.get("missed_seen") or []))
        mirrored = {self.parent_id(p) for p in self.ledger.positions.values()}
        cand_ids = {str(c["m2_id"]) for c in cands}
        for s, p in parent.ledger.positions.items():
            pid = str(p.id)
            at = _ts(p.opened_at)
            if at is None or at <= last or pid in seen or pid in mirrored or pid in cand_ids:
                continue
            seen.add(pid)
            self._skip({"m2_id": pid, "symbol": s}, P.SKIP_MISSED_TOUR, now)
        self.meta["missed_seen"] = sorted(seen)[-200:]

    # ------------------------------------------------------------------ sahip yeniden başlatması (§2.8)
    def _process_control(self, now: datetime, e: float, gap: bool) -> None:
        p = self.control_file
        if not p.exists():
            return
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("M2X kontrol dosyası okunamadı: %s", exc)
            return
        if not isinstance(doc, dict) or doc.get("consumed"):
            return
        rid = str(doc.get("request_id") or "")
        done = [str(x) for x in (self.meta.get("control_processed") or [])]
        if not rid or rid in done:
            self._write_control(dict(doc, consumed=True, result=doc.get("result") or "DUPLICATE", processed_at=iso(now)))
            return
        epoch = int(self.meta.get("epoch", 0))
        try:
            expected = int(doc.get("epoch_expected"))
        except (TypeError, ValueError):
            expected = None
        if not self.st.halted:
            result = RESUME_NOT_HALTED
        elif expected != epoch:
            result = RESUME_STALE_EPOCH
        elif gap:
            return                                         # E ölçülemiyor: istek BEKLER (tüketilmez)
        else:
            result = RESUME_ACCEPTED
            st = self.st
            st.peak = st.tier_peak = float(e)
            st.tier, st.streak, st.halted, st.halted_at, st.last_key = RESUME_TIER, 0, False, None, None
            self.meta["epoch"] = epoch + 1
            self.meta["epoch_started_at"] = iso(now)
            self.meta["epoch_start_equity"] = float(e)
            self.meta["tier_since"] = iso(now)
            self.meta["peak_at"] = iso(now)
            self.meta["halt"] = None
            self._policy_event({"at": iso(now), "kind": "RESUME", "epoch": epoch + 1, "equity": e, "tier": P.TIER_NAMES[RESUME_TIER]})
        rec = {"request_id": rid, "requested_at": doc.get("at"), "note": str(doc.get("note") or "")[:500],
               "epoch_expected": expected, "result": result, "processed_at": iso(now), "epoch": int(self.meta.get("epoch", 0))}
        self.meta["resume_history"] = (list(self.meta.get("resume_history") or []) + [rec])[-RESUME_KEEP:]
        self.meta["control_processed"] = (done + [rid])[-50:]
        self._log_event({"kind": "resume", **rec})
        self._write_control(dict(doc, consumed=True, result=result, processed_at=iso(now)))

    def _write_control(self, doc: dict[str, Any]) -> None:
        try:
            atomic_write_json(self.control_file, doc)
        except Exception as exc:  # noqa: BLE001
            log.warning("M2X kontrol dosyası yazılamadı: %s", exc)

    # ------------------------------------------------------------------ yeni girişler (§1.3, §2.3–2.6)
    def _entries(self, parent: Any, cands: list[dict[str, Any]], resolved: set[str], *, now: datetime, pmarks: dict,
                 e: float, gap: bool, o_now: float, c_now: float) -> tuple[str, int]:
        led = self.ledger
        m2_open = parent.held_ids() if parent is not None else {}
        alive: list[dict[str, Any]] = []
        for c in sorted(cands, key=lambda x: _pos_seq(x["m2_id"])):
            resolved.add(c["m2_id"])
            if str(m2_open.get(c["symbol"]) or "") != str(c["m2_id"]):
                self._skip(c, P.SKIP_PARENT_CLOSED, now)
            elif c["symbol"] in led.positions:
                self._skip(c, SKIP_SYMBOL_HELD, now)
            elif c.get("side") != "LONG" or not c.get("stop"):
                self._skip(c, "M2X_BAD_INPUT", now)
            else:
                alive.append(c)
        if not alive:
            return "-", 0
        gate = P.entry_gate(self.st, new_entries=self.new_entries)
        if gate is None and bool(getattr(self.killswitch, "active", False)):
            gate = SKIP_KILL_SWITCH
        if gate is None and gap:
            gate = P.SKIP_DATA_GAP
        if gate is not None:
            for c in alive:
                self._skip(c, gate, now)
            return "GATE", 0
        tier = self.st.tier
        k = P.V1
        r_pct = k.tier_risk_pct(tier)
        d = r_pct / 100.0 * e
        cap_pct = float(k.or_cap_pct[tier])
        h_or, h_cl = P.gaps(equity=e, peak=self.st.peak, open_risk=o_now, crash_loss=c_now, or_cap_pct=cap_pct, knobs=k)
        full: list[tuple[dict[str, Any], float, Any, Any, Any, int]] = []
        for c in alive:
            sym = c["symbol"]
            tick = pmarks.get(sym)
            if tick is None:
                self._skip(c, P.SKIP_DATA_GAP, now, stage="full")
                continue
            filt = self._filters(sym)
            lcap = self._max_leverage(filt)
            ref = Decimal(str(c["ref"]))
            fill = float(led.market_fill_price(sym, "LONG", ref, filters=filt, tick=tick))
            pl = P.plan_entry(equity=e, fill=fill, stop=float(c["stop"]), risk_usdt=d, tier_risk_pct=r_pct, available=1e18,
                              knobs=k, max_leverage=lcap, **filter_kw(filt))
            if not pl.ok:
                self._skip(c, pl.reason or "M2X_PLAN", now, stage="full")
                continue
            full.append((c, fill, pl, filt, tick, lcap))
        if not full:
            return "PLAN", 0
        sizes = [(d, P.crash_loss_one(pl.qty, float(c["ref"]), pl.liq)) for c, _f, pl, _ft, _t, _l in full]
        lam, alloc = P.allocate(sizes, h_or, h_cl)
        so, sc = sum(x[0] for x in sizes), sum(x[1] for x in sizes)
        lo = h_or / so if so > 0 else float("inf")
        lc = h_cl / sc if sc > 0 else float("inf")
        binding = "-" if lam >= 1.0 - 1e-12 else ("OR" if lo <= lc else "CL")
        self.meta["last_lambda"] = round(float(lam), 6)
        n_new = 0
        mn = float(P.M2X_POLICY_V1["min_scale"])
        for (c, fill, _pl, filt, tick, lcap), (scale, why) in zip(full, alloc):
            sym = c["symbol"]
            if scale is None:
                self._skip(c, why or P.SKIP_OR, now, lam=round(lam, 6))
                continue
            pl = P.plan_entry(equity=e, fill=fill, stop=float(c["stop"]), risk_usdt=scale * d, tier_risk_pct=r_pct,
                              available=float(led.available), knobs=k, max_leverage=lcap, min_risk_usdt=mn * d,
                              **filter_kw(filt))
            if not pl.ok:
                if pl.reason == P.SKIP_MARGIN:
                    binding = "MARGIN"
                self._skip(c, pl.reason or "M2X_PLAN", now, lam=round(lam, 6))
                continue
            ref_f = float(c["ref"])
            o_new = P.open_risk_one(pl.qty, ref_f, float(c["stop"]), entry=fill)
            c_new = P.crash_loss_one(pl.qty, ref_f, pl.liq)
            if o_new > h_or + 1e-9:
                self._skip(c, P.SKIP_OR, now, lam=round(lam, 6))
                continue
            if c_new > h_cl + 1e-9:
                self._skip(c, P.SKIP_CL, now, lam=round(lam, 6))
                continue
            tag = {"m2_id": c["m2_id"], "tier": self.st.tier_name, "epoch": int(self.meta.get("epoch", 0)),
                   "scale": round(float(scale), 6), "lam": round(float(lam), 6), "d": round(d, 6),
                   "risk_pct": r_pct, "size_rule": pl.size_rule, "l_need": int(pl.l_need), "l_liq": int(pl.l_liq),
                   "lev_reduced": int(pl.lev_reduced), "m2_qty": c.get("m2_qty"), "m2_leverage": c.get("m2_leverage"),
                   "m2_learning": c.get("learning"), "policy_version": P.POLICY_VERSION}
            pos = led.open(sym, "LONG", Decimal(str(c["ref"])), size_spec(pl), stop=Decimal(str(c["stop"])), filters=filt,
                           tick=tick, now=now, setup_type="m2x_mirror", trigger_text="M2X_MIRROR",
                           features={"m2x": dict(tag)}, meta={"m2x": dict(tag)}, allow_shrink=True)
            if pos is None:
                self._skip(c, "M2X_LEDGER_%s" % (led.last_reject_reason or "REJECT"), now, lam=round(lam, 6))
                continue
            o_act = P.open_risk_one(float(pos.qty), ref_f, float(c["stop"]), entry=float(pos.entry_avg))
            c_act = P.crash_loss_one(float(pos.qty), ref_f, float(pos.liquidation_price) if pos.liquidation_price else None)
            h_or -= o_act
            h_cl -= c_act
            n_new += 1
            from .protective_monitor import tick_times
            src, got = tick_times(tick)
            self._last_marks[sym] = (ref_f, int(src if src is not None else (got if got is not None else now.timestamp() * 1000)))
            ent = self.meta.setdefault("entries", {"n": 0, "full": 0, "scaled": 0, "bump": 0})
            ent["n"] = int(ent.get("n", 0)) + 1
            ent["full" if scale >= 1.0 - 1e-9 else "scaled"] = int(ent.get("full" if scale >= 1.0 - 1e-9 else "scaled", 0)) + 1
            from .learning_mode import SIZE_BUMP
            if pl.size_rule == SIZE_BUMP:
                ent["bump"] = int(ent.get("bump", 0)) + 1
            if pl.lev_reduced:
                self._bump("divergences", "lev_reduced")
            if isinstance(pos.meta.get("shrunk_to_margin"), dict):
                self._bump("divergences", "ledger_shrunk")
            self._log_event({"kind": "entry", "at": iso(now), "id": str(pos.id), "symbol": sym, **tag,
                             "qty": float(pos.qty), "fill": float(pos.entry_avg), "stop": float(c["stop"]),
                             "leverage": int(pos.leverage), "liq": _f(pos.liquidation_price),
                             "risk_usdt": round(float(pos.qty) * (float(pos.entry_avg) - float(c["stop"])), 6),
                             "full": bool(scale >= 1.0 - 1e-9), "m2_risk_usdt": c.get("m2_risk_usdt")})
        return binding, n_new

    # ------------------------------------------------------------------ özet ve kayıt
    def _save(self, now: datetime, *, write_state: bool = True) -> None:
        try:
            self.ledger.save(self.ledger_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("M2X defteri kaydedilemedi: %s", exc)
        if write_state:
            try:
                atomic_write_json(self.state_file, self._state_doc())
            except Exception as exc:  # noqa: BLE001
                log.warning("M2X durum dosyası kaydedilemedi: %s", exc)
        try:
            atomic_write_json(Path(self.cfg.state_path) / self.summary_file, self.summary_doc(now))
        except Exception as exc:  # noqa: BLE001
            log.warning("M2X özeti yazılamadı: %s", exc)

    def _status(self) -> str:
        if self.st.halted:
            return STATUS_HALTED
        if self.st.tier >= P.SOFT_HALT_TIER:
            return STATUS_SOFT_HALT
        return STATUS_RUNNING

    def _period_pcts(self, e: float, now: datetime) -> dict[str, Any]:
        """Bugün / ay başından / son 30 gün (mark'a göre, günlük görüntülerden) ve gerçekleşmiş ay / 30 gün (kapanan
        işlemlerin neti ÷ başlangıç bakiyesi; karnenin AYLIK HEDEF tanımıyla aynı taban)."""
        daily = [r for r in (self.meta.get("daily") or []) if isinstance(r, dict) and _f(r.get("equity"))]
        today = _day_of(now)
        month = today[:7]
        out: dict[str, Any] = {"today_pct": None, "mtd_pct": None, "last30_pct": None, "today_base_day": None,
                               "mtd_base_day": None, "last30_base_day": None}
        if daily:
            base = next((r for r in reversed(daily) if r["day"] <= today), None)
            if base:
                out["today_pct"], out["today_base_day"] = round((e / float(base["equity"]) - 1) * 100, 4), base["day"]
            mrows = [r for r in daily if str(r["day"])[:7] == month]
            mb = mrows[0] if mrows else next((r for r in reversed(daily) if r["day"] < today), None)
            if mb:
                out["mtd_pct"], out["mtd_base_day"] = round((e / float(mb["equity"]) - 1) * 100, 4), mb["day"]
            cut = (now.astimezone(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
            lb = next((r for r in reversed(daily) if r["day"] <= cut), None) or daily[0]
            out["last30_pct"], out["last30_base_day"] = round((e / float(lb["equity"]) - 1) * 100, 4), lb["day"]
        se = float(self.ledger.starting_equity) or 1.0
        ms = now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        l30 = now - timedelta(days=30)
        rm = rl = 0.0
        for h in self.ledger.history:
            at = _ts(h.closed_at)
            if at is None:
                continue
            if ms <= at <= now:
                rm += float(h.pnl)
            if l30 <= at <= now:
                rl += float(h.pnl)
        out["realised_mtd_pct"] = round(rm / se * 100, 4)
        out["realised_last30_pct"] = round(rl / se * 100, 4)
        # günlük dağılım (yalnız rapor): son 30 görüntüde ≥ +%1 ve ≤ −%1 gün payı, medyan gün
        rets = []
        tail = daily[-31:]
        for a, b in zip(tail, tail[1:]):
            ea, eb = float(a["equity"]), float(b["equity"])
            if ea > 0:
                rets.append((eb / ea - 1) * 100)
        out["daily_dist"] = {"n": len(rets), "ge_plus1": sum(1 for r in rets if r >= DAILY_TARGET_PCT),
                             "le_minus1": sum(1 for r in rets if r <= -DAILY_TARGET_PCT),
                             "median_pct": round(statistics.median(rets), 4) if rets else None,
                             "target_pct": DAILY_TARGET_PCT}
        out["monthly_target_pct"] = MONTHLY_TARGET_PCT
        return out

    def summary_doc(self, now: datetime | None = None) -> dict[str, Any]:
        """`state/<state_dir>.json` — StrategyBook özetiyle uyumlu alanlar (panel ve dağıtım denetimi aynı biçimi okur)
        + `m2x` bloğu (§4.1). Bugün / ay / 30 gün yüzdeleri düşüşün ve gerçekleşmiş sonucun YANINDADIR."""
        now = now or utc_now()
        led = self.ledger
        st = self.st
        e, gap_syms, marks = self._mark_view(int(now.timestamp() * 1000))
        views = self._views(marks)
        o_mark, cl = P.totals(views)
        o_entry, _ = P.totals(views, measure="entry")
        tier = st.tier
        cap = None if (st.halted or tier >= P.SOFT_HALT_TIER) else float(P.V1.or_cap_pct[tier])
        risk_pct = 0.0 if (st.halted or tier >= P.SOFT_HALT_TIER) else P.V1.tier_risk_pct(tier)
        h_cl = e - float(P.M2X_POLICY_V1["crash_budget_peak_frac"]) * st.peak - cl
        parent = self._parent
        p_open = {}
        if parent is not None:
            try:
                p_open = {s: p for s, p in parent.ledger.positions.items()}
            except Exception:  # noqa: BLE001
                p_open = {}
        created = _ts(self.meta.get("created_at"))
        days_live = (now - created).total_seconds() / 86400.0 if created else 0.0
        pos_rows: dict[str, Any] = {}
        for v in views:
            p = led.positions.get(v.symbol)
            if p is None:
                continue
            mid = self.parent_id(p)
            pp = p_open.get(v.symbol)
            ratio = (float(p.qty) / float(pp.qty)) if (pp is not None and str(pp.id) == str(mid) and float(pp.qty) > 0) else None
            pos_rows[v.symbol] = {"side": p.side.value, "entry": float(p.entry_avg), "qty": float(p.qty),
                                  "stop": float(p.stop) if p.stop else None, "leverage": p.leverage, "opened_at": p.opened_at,
                                  "last_price": float(p.last_price) if p.last_price else None,
                                  "liq_price": _f(p.liquidation_price), "m2_id": mid, "size_ratio_vs_m2": _round(ratio, 4),
                                  "open_risk_usdt": round(P.open_risk_one(v.qty, v.mark, v.stop), 6),
                                  "crash_loss_usdt": round(P.crash_loss_one(v.qty, v.mark, v.liq), 6),
                                  "mark": v.mark, "mark_fresh": v.symbol in marks,
                                  "tier_at_entry": ((p.meta or {}).get("m2x") or {}).get("tier")}
        u_meta = self.meta.get("u") or {}
        d20 = P.du20(st)
        pct = self._period_pcts(e, now)
        _p_ids = {str(x.id) for x in p_open.values()}
        mirrored = sum(1 for p in led.positions.values() if (self.parent_id(p) or "") in _p_ids)
        fs = led.summary(marks)
        m2x = {
            "policy_version": P.POLICY_VERSION, "policy_sha": P.M2X_POLICY_SHA, "doc": P.POLICY_DOC,
            "epoch": int(self.meta.get("epoch", 0)), "epoch_started_at": self.meta.get("epoch_started_at"),
            "epoch_start_equity": self.meta.get("epoch_start_equity"), "created_at": self.meta.get("created_at"),
            "warmup": days_live < float(P.M2X_POLICY_V1["warmup_days"]), "days_live": round(days_live, 2),
            "equity_mtm": round(e, 6), "equity_realised": round(float(led.wallet_balance), 6),
            "marks_fresh": not gap_syms, "stale_symbols": sorted(gap_syms)[:20],
            "starting_equity": float(led.starting_equity),
            "total_pct": round((e / float(led.starting_equity) - 1) * 100, 4) if led.starting_equity else None,
            "epoch_pct": (round((e / float(self.meta["epoch_start_equity"]) - 1) * 100, 4)
                          if _f(self.meta.get("epoch_start_equity")) else None),
            "peak": round(st.peak, 6), "peak_at": self.meta.get("peak_at"), "tier_peak": round(st.tier_peak, 6),
            # anlık düşüş YALNIZ gösterim (kademe/zirve yalnız gözlem noktalarında değişir, §2.7); zirve üstü → 0
            "dd_pct": round(max(0.0, st.dd(e)) * 100, 4), "tier_dd_pct": round(max(0.0, st.ddk(e)) * 100, 4),
            "tier": st.tier_name, "tier_since": self.meta.get("tier_since"), "risk_pct": risk_pct,
            "open_risk_cap_pct": cap, "open_risk_mark_usdt": round(o_mark, 6),
            "open_risk_mark_pct": round(o_mark / e * 100, 4) if e > 0 else None,
            "open_risk_entry_usdt": round(o_entry, 6), "open_risk_entry_pct": round(o_entry / e * 100, 4) if e > 0 else None,
            "crash_loss_usdt": round(cl, 6), "crash_loss_pct": round(cl / e * 100, 4) if e > 0 else None,
            "crash_budget_usdt": round(h_cl, 6), "crash_budget_pct": round(h_cl / e * 100, 4) if e > 0 else None,
            "binding_cap": self.meta.get("binding_cap") or "-", "binding_hist": dict(self.meta.get("binding_hist") or {}),
            "last_lambda": self.meta.get("last_lambda"),
            "max_or_e": round(float(self.meta.get("max_or_e") or 0.0), 6),
            "max_cl_e": round(float(self.meta.get("max_cl_e") or 0.0), 6),
            "u_curve": {"u": (st.u_hist[-1][1] if st.u_hist else None), "du20": d20,
                        "closed_sum_r": round(float(u_meta.get("closed_sum", 0.0)), 6), "n_closed": int(u_meta.get("n_closed", 0)),
                        "since": u_meta.get("since")},
            "step_up_streak": st.streak, "step_up_needed": int(P.M2X_POLICY_V1["step_up_streak"]), "last_key": st.last_key,
            "last_observation": self.meta.get("last_observation"), "last_snapshot_day": self.meta.get("last_snapshot_day"),
            "halted": st.halted, "halt": self.meta.get("halt"), "status": self._status(),
            "resume_history": list(self.meta.get("resume_history") or [])[-RESUME_KEEP:],
            "resume_cmd": "python -m tradingbot m2x-resume --i-reviewed --note \"<neden>\"",
            **pct,
            "skips": dict(self.meta.get("skips") or {}), "skips_by_tier": dict(self.meta.get("skips_by_tier") or {}),
            "entries": dict(self.meta.get("entries") or {}), "divergences": dict(self.meta.get("divergences") or {}),
            "parity": dict(self.meta.get("parity") or {}), "stop_syncs": int(self.meta.get("stop_syncs") or 0),
            "funding_pending_m2x_only": self._funding_pending_only(parent),
            "parent": {"name": self.parent_name, "key": (getattr(parent, "key", None) if parent is not None
                                                         else self.meta.get("parent_key")),
                       # yeniden başlatmadan sonra ilk turdan ÖNCE (izleyici yazımı) son turun bilgisi
                       "enabled": (parent is not None) if parent is not None or self.meta.get("parent_enabled") is None
                       else bool(self.meta.get("parent_enabled")), "open": len(p_open),
                       "mirrored": mirrored, "regime": self.meta.get("parent_regime")},
            "new_entries": self.new_entries, "recent_skips": list(self.meta.get("recent_skips") or [])[-RECENT_SKIPS_KEEP:],
            "events_recent": list(self.meta.get("events") or [])[-20:], "tours": int(self.meta.get("tours", 0)),
            "last_tour_at": self.meta.get("last_tour_at"),
            "tier_bounds_pct": [round(float(t[1]) * 100, 2) for t in P.M2X_POLICY_V1["tiers"][1:]] + [round(float(P.M2X_POLICY_V1["halt_dd"]) * 100, 2)],
            "daily_tail": list(self.meta.get("daily") or [])[-60:],
        }
        skipped = sum(int(v) for v in (self.meta.get("skips") or {}).values())
        return {"schema_version": SUMMARY_SCHEMA, "generated_at": iso(now), "name": self.name, "key": self.key, "kind": self.kind,
                "parent": self.parent_name, "label": LABEL_TR, "starting_equity": float(led.starting_equity),
                "rule_evaluated_at": self.meta.get("last_tour_at"), "regime": None, "atr_mult": None,
                "summary": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in fs.items()},
                "positions": pos_rows, "history_tail": led.history_dicts()[-50:],
                "counters": {"opened": len(led.history) + len(led.positions), "closed": len(led.history),
                             "tours": int(self.meta.get("tours", 0)), "rejected": skipped, "data_rejected": 0},
                "rejections": dict(self.meta.get("skips") or {}), "closed_recent": self.closed_recent[-20:],
                "last_actions": {}, "monitoring_gap": self.monitoring_gap, "data_gaps": dict(self.data_gaps),
                "data_events_recent": self.data_events[-30:], "m2x": m2x,
                "note_tr": ("KÂĞIT AYNA DEFTERİ — gerçek para yok. M2'nin gerçek işlemlerinin daha büyük boyutlu kopyasıdır; "
                            "yeni kanıt değildir. −%50 bir zarar tavanı DEĞİL, yeni giriş durdurmasıdır.")}

    def _funding_pending_only(self, parent: Any) -> int:
        try:
            mine = set(self.ledger.funding_pending_symbols())
            theirs = set(parent.ledger.funding_pending_symbols()) if parent is not None else set()
            return len(mine - theirs)
        except Exception:  # noqa: BLE001
            return 0


def _ts(x: Any) -> datetime | None:
    try:
        if not x:
            return None
        d = from_iso(str(x))
        return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------- sahip komutu (worker'a dokunmaz)
def write_resume_request(state_path: Path | str, *, state_dir: str = "strategy_paper_m2x", note: str,
                         now: datetime | None = None) -> dict[str, Any]:
    """`m2x-resume`: kontrol dosyasına TEK KULLANIMLIK istek yazar `{request_id, at, note, epoch_expected}`. Worker sonraki
    turda yalnız `HALTED` iken ve dönem eşleşirse kabul eder. Defterin kendisine ve durum dosyasına YAZMAZ."""
    now = now or utc_now()
    d = Path(state_path) / state_dir
    try:
        sd = json.loads((d / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sd = {}
    pol = (sd or {}).get("policy") or {}
    doc = {"schema": CONTROL_SCHEMA, "request_id": uuid.uuid4().hex, "at": iso(now), "note": str(note)[:500],
           "epoch_expected": int((sd or {}).get("epoch", 0) or 0), "consumed": False}
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / CONTROL_FILE, doc)
    return {"request": doc, "halted": bool(pol.get("halted")), "state_found": bool(sd)}


def check_lines(doc: dict[str, Any] | None, section: dict[str, Any] | None = None) -> list[str]:
    """`deploy --check` satırları (§4.3): config özeti ve özet dosyasından tek durum satırı. Bugün / ay / 30 gün
    yüzdeleri düşüşün ve gerçekleşmiş sonucun YANINDA, satır başında DEĞİL. Durdurmada satır `!!` ile başlar."""
    out = []
    if section is not None:
        out.append("m2x_aggressive (config): enabled = %s · new_entries = %s · policy = %s"
                   % (section.get("enabled", False), section.get("new_entries", True), section.get("policy_version", "m2x_v1")))
    m = (doc or {}).get("m2x") if isinstance(doc, dict) else None
    if not isinstance(m, dict):
        out.append("M2X: özet yok (defter kapalı ya da henüz tur işlemedi)")
        return out

    def f(x, nd=2):
        v = _f(x)
        return "—" if v is None else ("%.*f" % (nd, v))
    line = ("M2X: özkaynak %s · zirve %s · düşüş %%%s (kademe zirvesinden %%%s) · kademe %s (risk %%%s, OR tavanı %%%s) · "
            "OR %%%s · kriz kaybı %%%s / bütçe %%%s · gerçekleşmiş ay %%%s · bugün %%%s · ay %%%s · son 30 gün %%%s · DURUM: %s"
            % (f(m.get("equity_mtm")), f(m.get("peak")), f(m.get("dd_pct")), f(m.get("tier_dd_pct")), m.get("tier"),
               f(m.get("risk_pct")), f(m.get("open_risk_cap_pct"), 0), f(m.get("open_risk_mark_pct")),
               f(m.get("crash_loss_pct")), f(m.get("crash_budget_pct")), f(m.get("realised_mtd_pct")),
               f(m.get("today_pct")), f(m.get("mtd_pct")), f(m.get("last30_pct")), m.get("status")))
    if m.get("warmup"):
        line += " · ISINMA (ilk 28 gün)"
    if not m.get("new_entries", True):
        line += " · YENİ GİRİŞ KAPALI"
    if not (m.get("parent") or {}).get("enabled", True):
        line += " · M2 KAPALI (PARENT_DISABLED)"
    if m.get("halted"):
        line = "!! " + line + " — sahip incelemesi gerekli: " + str(m.get("resume_cmd"))
    out.append(line)
    return out


__all__ = ["BOOK_NAME", "CONTROL_FILE", "EVENTS_FILE", "KIND", "LABEL_TR", "M2xBook", "MIRROR_ORPHAN_CLOSED",
           "MIRROR_PARENT_CLOSED", "STATE_FILE", "check_lines", "filter_kw", "size_spec", "write_resume_request"]
