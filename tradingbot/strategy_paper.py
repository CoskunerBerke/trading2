# -*- coding: utf-8 -*-
"""STRATEJİ KÂĞIT DEFTERİ — tek kurallı stratejinin uygulanması; canlı motor ile replay ORTAK yol (V10).

`apply_action` iki motorda da AYNI: boyut = profil işlem riski / stop mesafesi, `risk.evaluate`
(toplam risk tavanı, kaldıraç, cluster), `ledger.open` (gerçek filtre, kayma), `close_manual` (kayma).
Stop/hedef/funding/likidasyon/başa-baş: defterin kendi `tick`i.

`StrategyBook` canlı motorun AYRI kâğıt defteridir: kendi state dizini, kendi trade memory, kendi
özet dosyası. Ana botun defterine, öğrenicisine ve kararlarına DOKUNMAZ. Gerçek para YOK.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .accounting import (AmountType, FeeSchedule, FuturesLedgerV2, LiquidationParams, MarketType, SizeSpec,
                         SlippageModel, TaxPolicy, TickData, default_brackets)
from .candle_confirmation import closed_bars
from .core import atomic_write_json, iso, utc_now
from .learn import TradeMemory
from .regime_gate import BTC_SYMBOL
from .risk import RiskEngine, build_state, enforces_position_cap
from .ema200_trend import VARIANTS, daily_rows_from_frame, decide

log = logging.getLogger(__name__)
SUMMARY_FILE = "strategy_paper.json"
INDEX_FILE = "strategy_paper_index.json"
SCHEMA_VERSION = "strategy_paper_v1"
#: Kâğıt defterlerin işlem piyasası: USDⓈ-M perpetual. Kural verisi de BU piyasadan doğrulanmalı (2026-09-16 onarımı).
PAPER_MARKET = "USDM_PERP"
#: `decide`/`read_daily` sözleşmesinden: kural GÜNLÜK kapanmış barları okur (T2: EMA200; M2: 28 gün önceki kapanış);
#: BTC rejimi yalnız YENİ girişte gerekir (`decide`: position_open iken BTC'ye bakılmaz). Başka dilim zorunlu değildir.
RULE_TIMEFRAMES = ("1d",)


# ------------------------------------------------------------------ VERİ KAYNAĞI SÖZLEŞMESİ (2026-09-16)
@dataclass(frozen=True)
class DataVerdict:
    """Kural verisinin kimlik doğrulaması — motor (canlı) ve replay AYNI sözleşmeyi `apply_action`a taşır.

    `ok`: sembolün kural çerçevesi doğrulanmış USDⓈ-M perpetual verisi (piyasa + bu turun provenansı + yüklü barlarla
    bağ). `entry_ok`: ayrıca YENİ giriş için gerekenler: provenansın `entry_ok` bayrağı ve BTC referansının aynı
    doğrulaması. `reason`: ilk başarısızlık kodu (`DATA_*`); boşsa sorun yok. Eksik/geçersiz sözleşme = ret (fail-closed):
    `apply_action` bir `DataVerdict` olmadan ne OPEN ne CLOSE uygular.
    """
    ok: bool
    entry_ok: bool
    reason: str = ""
    market: str | None = None
    source: str | None = None
    tour_id: str | None = None
    bars: dict = field(default_factory=dict)      # {"1d": son kapanmış bar açılış ms, ...} — gerçekten kullanılan barlar
    btc: dict = field(default_factory=dict)       # {"ok", "market", "reason", "bars"}

    def to_dict(self) -> dict[str, Any]:
        return {"ok": bool(self.ok), "entry_ok": bool(self.entry_ok), "reason": self.reason, "market": self.market,
                "source": self.source, "tour_id": self.tour_id, "bars": dict(self.bars), "btc": dict(self.btc)}


def _last_ts(frame) -> int | None:
    try:
        if frame is None or len(frame) == 0:
            return None
        if "timestamp" in frame.columns:
            return int(frame["timestamp"].iloc[-1])
        return int(frame.index[-1].value // 1_000_000)
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return None


def _check_frames(frames: dict | None, prov: dict | None, run_id: str, want_market: str, tfs=RULE_TIMEFRAMES) -> tuple[str, dict]:
    """Tek sembol için kimlik kontrolü. Döner: (neden | "", kullanılan bar zamanları). Provenans defter adından ya da
    beklenen piyasadan UYDURULMAZ: sağlayıcının bu tur için yazdığı kayıt + o kayıtta bağlanan bar zaman damgaları
    ile bellekteki çerçeve birebir eşleşmeli (eski turun onayı / bayat önbellek / kısmi indirme eşleşmez)."""
    if not isinstance(prov, dict) or not prov.get("market"):
        return "PROVENANCE_MISSING", {}
    if str(prov.get("tour_id") or "") != str(run_id or "") or not run_id:
        return "PROVENANCE_STALE", {}
    if str(prov.get("market")) != want_market:
        return "MARKET_%s" % str(prov.get("market")).upper(), {}
    bound = prov.get("frames") or {}
    used: dict[str, int] = {}
    for tf in tfs:
        ts = _last_ts((frames or {}).get(tf))
        if ts is None:
            return "FRAME_MISSING_%s" % tf.upper(), {}
        b = bound.get(tf) or {}
        if b.get("last_ts") is None or int(b.get("last_ts")) != ts:
            return "FRAME_MISMATCH_%s" % tf.upper(), {}
        used[tf] = ts
    return "", used


def verify_paper_data(*, symbol: str, frames: dict | None, provenance: dict | None, run_id: str,
                      btc_frames: dict | None = None, btc_provenance: dict | None = None, need_btc: bool = True,
                      want_market: str = PAPER_MARKET) -> DataVerdict:
    """Kural verisi doğrulaması (SAF). `need_btc`: yeni giriş yolu (rejim referansı gerekir); açık pozisyonun kural
    kapanışı yalnız sembol verisine bağlıdır (`decide` position_open iken BTC okumaz) → BTC eksikliği kapanışı engellemez."""
    why, used = _check_frames(frames, provenance, run_id, want_market)
    if why:
        return DataVerdict(ok=False, entry_ok=False, reason="DATA_" + why, market=(provenance or {}).get("market") if isinstance(provenance, dict) else None,
                           source=(provenance or {}).get("source") if isinstance(provenance, dict) else None,
                           tour_id=(provenance or {}).get("tour_id") if isinstance(provenance, dict) else None)
    prov = provenance or {}
    entry_ok, reason = True, ""
    if not bool(prov.get("entry_ok", False)):
        entry_ok, reason = False, "DATA_ENTRY_BLOCKED:%s" % (prov.get("reason") or "PROVIDER")
    btc: dict[str, Any] = {"required": bool(need_btc)}
    if need_btc:
        bwhy, bused = _check_frames(btc_frames, btc_provenance, run_id, want_market)
        btc.update({"ok": not bwhy, "market": (btc_provenance or {}).get("market") if isinstance(btc_provenance, dict) else None,
                    "reason": ("DATA_BTC_" + bwhy) if bwhy else "", "bars": bused})
        if bwhy and entry_ok:
            entry_ok, reason = False, "DATA_BTC_" + bwhy
    return DataVerdict(ok=True, entry_ok=entry_ok, reason=reason, market=str(prov.get("market")), source=prov.get("source"),
                       tour_id=str(prov.get("tour_id")), bars=used, btc=btc)


class BookSpec:
    """Bir defterin ayarları — ana bölüm ya da `extra` listesindeki sözlük, TEK biçime indirgenir."""

    def __init__(self, *, name: str, starting_equity_usdt: float = 100.0, atr_mult: float = 3.0,
                 breakeven_at_mfe_r: float = 0.0, state_dir: str = "strategy_paper", symbols=None, enabled: bool = True):
        self.name, self.starting_equity_usdt, self.atr_mult = str(name), float(starting_equity_usdt), float(atr_mult)
        self.breakeven_at_mfe_r, self.state_dir = float(breakeven_at_mfe_r), str(state_dir)
        self.symbols, self.enabled = list(symbols or []), bool(enabled)

    @classmethod
    def from_section(cls, sp) -> "BookSpec":
        return cls(name=sp.name, starting_equity_usdt=sp.starting_equity_usdt, atr_mult=sp.atr_mult,
                   breakeven_at_mfe_r=sp.breakeven_at_mfe_r, state_dir=sp.state_dir, symbols=sp.symbols, enabled=sp.enabled)

    @classmethod
    def from_dict(cls, d: dict) -> "BookSpec":
        allowed = {"name", "starting_equity_usdt", "atr_mult", "breakeven_at_mfe_r", "state_dir", "symbols", "enabled"}
        return cls(**{k: v for k, v in dict(d).items() if k in allowed})

    @property
    def summary_file(self) -> str:
        return SUMMARY_FILE if self.state_dir == "strategy_paper" else "%s.json" % self.state_dir


def book_specs(v3) -> list["BookSpec"]:
    """Config'ten etkin defter listesi: ana bölüm (enabled ise) + extra (her biri enabled ise)."""
    sp = v3.strategy_paper
    out: list[BookSpec] = []
    if bool(getattr(sp, "enabled", False)):
        out.append(BookSpec.from_section(sp))
    for ex in list(getattr(sp, "extra", None) or []):
        b = BookSpec.from_dict(ex)
        if b.enabled:
            out.append(b)
    return out


def apply_action(act: dict[str, Any] | None, *, symbol: str, price: float, tick: TickData | None, now: datetime,
                 ledger: FuturesLedgerV2, risk: RiskEngine, profile, state, filters, run_id: str,
                 reject: Callable[[str, str], None], on_closed: Callable[[Any], None],
                 on_opened: Callable[[Any, dict[str, Any]], None] | None = None,
                 data: DataVerdict | None = None) -> str:
    """Stratejinin kararını uygular. Döner: OPENED | CLOSED | REJECTED | NONE. İki motor da bunu çağırır.

    `data` (2026-09-16): kural verisinin kimlik hükmü. Sözleşme fail-closed'dur — hüküm yoksa ya da `ok` değilse ne
    OPEN ne CLOSE uygulanır (SPOT ikamesi / kaynağı bilinmeyen veriyle futures işlemi yok); OPEN ayrıca `entry_ok`
    (provenansın giriş izni + BTC referansı) ister. Açık pozisyonun stop/hedef/likidasyonu bu fonksiyondan değil,
    defterin `tick`inden yürür ve bu hükümden ETKİLENMEZ."""
    if not act:
        return "NONE"
    action = str(act.get("action") or "").upper()
    pos = ledger.positions.get(symbol)
    if action == "CLOSE":
        if pos is None:
            return "NONE"
        if data is None or not data.ok:
            reject(symbol, (data.reason if (data is not None and data.reason) else "DATA_VERDICT_MISSING"))
            return "REJECTED"
        rec = ledger.close_manual(symbol, price, reason=str(act.get("reason") or "STRATEGY_EXIT"), now=now, tick=tick)
        if rec is None:
            return "NONE"
        on_closed(rec)
        return "CLOSED"
    if action != "OPEN" or pos is not None:
        return "NONE"
    if data is None or not data.ok or not data.entry_ok:
        reject(symbol, (data.reason if (data is not None and data.reason) else "DATA_VERDICT_MISSING"))
        return "REJECTED"
    direction = str(act.get("direction") or "LONG").upper()
    entry = float(price)
    stop = float(act.get("stop") or 0.0)
    if entry <= 0 or stop <= 0 or (direction == "LONG" and stop >= entry) or (direction == "SHORT" and stop <= entry):
        reject(symbol, "STRATEGY_BAD_STOP")
        return "REJECTED"
    stop_frac = abs(entry - stop) / entry
    risk_usdt = float(profile.risk_per_trade_pct) / 100.0 * float(state.equity)
    notional = risk_usdt / stop_frac
    lev = int(act.get("leverage") or 1)
    plan_dict = {"symbol": symbol, "market_type": "USDM_PERP", "direction": direction, "entry": entry, "stop": stop,
                 "targets": list(act.get("targets") or []), "notional": notional, "margin": notional / max(1, lev),
                 "leverage": lev, "amount_type": "NOTIONAL", "expected_r": float(act.get("expected_r") or 0.0),
                 "min_notional": 5.0}
    rd = risk.evaluate(plan_dict, state, {"now_utc": now})
    if not rd.allowed:
        reject(symbol, (rd.reasons or ["RISK_DENIED"])[0])
        return "REJECTED"
    notional = float(rd.adjusted_notional or notional)
    if notional <= 0:
        reject(symbol, "ZERO_NOTIONAL")
        return "REJECTED"
    # Kanıt: hangi veriyle girildiği pozisyon/işlem kaydına yazılır (piyasa, kaynak, tur, kullanılan bar zamanları).
    data_src = {"market": data.market, "source": data.source, "tour_id": data.tour_id, "bars": dict(data.bars), "btc": dict(data.btc)}
    pos = ledger.open(symbol, direction, entry, SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, int(rd.adjusted_leverage or lev)),
                      filters=filters, stop=stop, targets=list(act.get("targets") or []),
                      setup_type=str(act.get("setup_type") or "strategy"), trigger_text=str(act.get("reason") or ""),
                      features={"regime": act.get("regime"), "market_type": "USDM_PERP", "strategy": act.get("name"),
                                "expected_r": float(act.get("expected_r") or 0.0), "p_win": None, "data_source": data_src},
                      tick=tick, now=now, meta={"run_id": run_id, "strategy": str(act.get("name") or ""), "data_source": data_src})
    if pos is None:
        reject(symbol, ledger.last_reject_reason or "LEDGER_REJECT")
        return "REJECTED"
    if on_opened is not None:
        on_opened(pos, act)
    return "OPENED"


class StrategyBook:
    """Canlı motorda tek kurallı stratejinin AYRI kâğıt defteri (ileri test)."""

    def __init__(self, cfg, *, profile, killswitch, filters_cache, run_id: str, spec: "BookSpec | None" = None):
        v3 = cfg.v3
        sp = spec or BookSpec.from_section(v3.strategy_paper)
        self.spec = sp
        self.key = sp.state_dir
        self.summary_file = sp.summary_file
        self.cfg, self.profile, self.filters_cache, self.run_id = cfg, profile, filters_cache, run_id
        self.name = str(sp.name)
        self.atr_mult = float(sp.atr_mult)
        self.symbols: list[str] = list(sp.symbols) if sp.symbols else []
        self.state_dir = Path(cfg.state_path) / str(sp.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.state_dir / "futures_ledger.json"
        fees = FeeSchedule(maker_pct=Decimal(str(v3.fees.futures_maker_pct)), taker_pct=Decimal(str(v3.fees.futures_taker_pct)), source=v3.fees.source)
        slip = SlippageModel(fixed_bps=Decimal(str(v3.fees.slippage_bps)))
        self.ledger = FuturesLedgerV2.load(self.ledger_path, starting_equity=float(sp.starting_equity_usdt),
                                          max_positions=cfg.futures.max_positions,
                                          enforce_position_cap=enforces_position_cap(profile),
                                          fees=fees, slippage=slip, brackets=default_brackets(),
                                          liq_params=LiquidationParams(liq_fee_pct=Decimal(str(v3.futures_v3.liq_fee_pct))),
                                          tp1_fraction=Decimal(str(v3.futures_v3.tp1_fraction)),
                                          breakeven_at_mfe_r=Decimal(str(sp.breakeven_at_mfe_r)),
                                          tax_policy=TaxPolicy.disabled())
        self.risk = RiskEngine(profile, killswitch, v3.risk_profiles.clusters or None)
        self.memory = TradeMemory(self.state_dir / "trade_memory.jsonl", source="STRATEGY_PAPER")
        self.lock = threading.RLock()
        self.last_actions: dict[str, dict[str, Any]] = {}
        self.counters = {"opened": 0, "closed": 0, "rejected": 0, "tours": 0, "data_rejected": 0}
        self.rejections: dict[str, int] = {}
        self.regime: str | None = None
        self.closed_recent: list[dict[str, Any]] = []
        # VERİ KAYNAĞI izlenebilirliği (2026-09-16): bu turun sembol başına hükmü, fiyat boşlukları ve son olaylar (kalıcı kuyruk)
        self.data_checks: dict[str, dict[str, Any]] = {}
        self.data_gaps: dict[str, dict[str, Any]] = {}
        self.data_events: list[dict[str, Any]] = []
        self._restore_counters()

    def _restore_counters(self) -> None:
        """Yeniden baslatmada sayaclar SIFIRLANMAZ (V13). opened/closed defterin kendisinden (kalici gercek),
        rejected/tours/retler/son kapanislar bir onceki ozet dosyasindan gelir. Ozet yoksa ya da bozuksa yalniz
        defter gercegi kullanilir; defterin kendisine hicbir kosulda dokunulmaz."""
        try:
            p = Path(self.cfg.state_path) / self.summary_file
            if p.exists():
                prev = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(prev, dict) and prev.get("key", self.key) == self.key and prev.get("name", self.name) == self.name:
                    pc = prev.get("counters") or {}
                    self.counters["rejected"] = int(pc.get("rejected") or 0)
                    self.counters["tours"] = int(pc.get("tours") or 0)
                    self.counters["data_rejected"] = int(pc.get("data_rejected") or 0)
                    self.rejections = {str(k): int(v) for k, v in (prev.get("rejections") or {}).items()}
                    self.closed_recent = list(prev.get("closed_recent") or [])[-50:]
                    self.data_events = [e for e in (prev.get("data_events_recent") or []) if isinstance(e, dict)][-50:]
        except Exception as exc:  # noqa: BLE001 -- ozet bozuksa sayac sifirdan baslar, defter ETKILENMEZ
            log.warning("strateji defter sayaclari geri yuklenemedi (%s): %s", self.key, exc)
        closed = len(self.ledger.history_dicts())
        self.counters["closed"] = closed
        self.counters["opened"] = closed + len(self.ledger.positions)

    # ------------------------------------------------------------------ yardımcılar
    def _state(self, marks_f: dict[str, float]):
        pos = [{"symbol": s, "market_type": "USDM_PERP", "side": p.side.value, "notional": float(p.qty * p.entry_avg),
                "margin": float(p.isolated_margin), "entry": float(p.entry_avg), "stop": float(p.stop) if p.stop else None,
                "leverage": p.leverage, "liq_price": float(p.liquidation_price) if p.liquidation_price else None,
                "opened_at": p.opened_at} for s, p in self.ledger.positions.items()]
        fs = self.ledger.summary(marks_f)
        return build_state(equity=float(fs["equity_mtm"]), starting_equity=float(self.ledger.starting_equity),
                           available=float(fs["available"]), used_margin=float(fs["used_margin"]), positions=pos,
                           history=self.ledger.history_dicts(), high_water_mark=0.0, now=utc_now(),
                           clusters=self.cfg.v3.risk_profiles.clusters or None)

    def _reject(self, symbol: str, reason: str) -> None:
        if str(reason).startswith("DATA_"):
            self.counters["data_rejected"] += 1       # veri kaynağı reddi: risk/defter reddinden AYRI sayaç
        else:
            self.counters["rejected"] += 1
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        self.last_actions[symbol] = {"action": "REJECTED", "reason": reason, "at": iso(utc_now())}

    def _data_event(self, symbol: str, kind: str, reason: str, at: datetime, **extra: Any) -> None:
        ev = {"symbol": symbol, "kind": kind, "reason": reason, "at": iso(at), **extra}
        self.data_events.append(ev)
        self.data_events = self.data_events[-50:]

    def _reject_data(self, symbol: str, verdict: DataVerdict, stage: str, at: datetime, would_act: str | None) -> None:
        """Veri kaynağı reddi: sayaç + gerekçe + olay (sembol/defter/gerekçe bazında görünür; sinyal yokluğu ve risk
        reddinden ayrı). `would_act`: kanıtsız veriyle hesaplanan (UYGULANMAYAN) kural sonucu — yalnız bilgi."""
        self._reject(symbol, verdict.reason or "DATA_UNVERIFIED")
        self.last_actions[symbol] = {"action": "DATA_REJECTED", "reason": verdict.reason, "stage": stage, "at": iso(at),
                                     "market": verdict.market, "tour_id": verdict.tour_id, "would_act": would_act}
        self._data_event(symbol, "REJECT", verdict.reason, at, stage=stage, market=verdict.market, source=verdict.source,
                         tour_id=verdict.tour_id, bars=dict(verdict.bars), btc=dict(verdict.btc), would_act=would_act)

    def record_gaps(self, gaps: dict[str, dict[str, Any]], now: datetime) -> None:
        """Doğrulanmış futures fiyatı olmayan semboller (tick YOK, uydurma gerçekleşme YOK): durum + olay (durum
        değişince bir kez; her 60 sn'de yinelenmez)."""
        with self.lock:
            for sym, g in (gaps or {}).items():
                prev = self.data_gaps.get(sym)
                if not prev or prev.get("reason") != g.get("reason"):
                    self._data_event(sym, "PRICE_GAP", str(g.get("reason") or "NO_VERIFIED_FUTURES_PRICE"), now, detail=g.get("detail"))
                self.data_gaps[sym] = dict(g)
            for sym in [s for s in self.data_gaps if s not in (gaps or {})]:
                self._data_event(sym, "PRICE_RESTORED", "", now)
                self.data_gaps.pop(sym, None)

    def _on_closed(self, rec) -> None:
        self.counters["closed"] += 1
        d = rec.to_legacy_dict() if hasattr(rec, "to_legacy_dict") else {}
        self.closed_recent.append({"id": getattr(rec, "id", None), "symbol": getattr(rec, "symbol", None),
                                   "exit_reason": getattr(rec, "exit_reason", None),
                                   "net_pnl": float(getattr(rec, "net_pnl", 0) or 0), "r": float(getattr(rec, "r_multiple", 0) or 0),
                                   "closed_at": getattr(rec, "closed_at", None), "features": d.get("features")})
        self.closed_recent = self.closed_recent[-50:]

    def _on_opened(self, pos, act: dict[str, Any]) -> None:
        self.counters["opened"] += 1
        try:
            self.memory.record_entry({"trade_id": pos.id, "symbol": pos.symbol, "direction": pos.side.value, "market_type": "USDM_PERP",
                                      "setup_type": "trend", "regime": act.get("regime"),
                                      "features": {"strategy": act.get("name"), "signal_close": act.get("signal_close"),
                                                   "ema200": act.get("ema200"), "atr14": act.get("atr14"),
                                                   # CHART ANALYSIS V1: giris ANINDAKI referanslar (sonradan degismez)
                                                   "signal_ts": act.get("signal_ts"), "ref_close": act.get("ref_close"),
                                                   "ref_ts": act.get("ref_ts"), "stop_at_entry": act.get("stop"),
                                                   "atr_mult": self.atr_mult},
                                      "run_id": self.run_id, "in_test": True})
        except Exception as exc:  # noqa: BLE001 — bellek arızası işlemi ETKİLEMEZ
            log.warning("strateji bellek kaydı yazılamadı (%s): %s", pos.symbol, exc)

    # ------------------------------------------------------------------ tur
    def step(self, *, symbols: list[str], frames_by_symbol: dict[str, dict], marks: dict[str, TickData],
             marks_f: dict[str, float], now: datetime, provenance_by_symbol: dict[str, dict] | None = None,
             data_gaps: dict[str, dict] | None = None) -> None:
        """Her turda: veri kimliği doğrulaması → kural → apply_action. Ana bot dokunulmaz; yalnız bu defter değişir.

        `provenance_by_symbol` (2026-09-16): sağlayıcının BU tur için yazdığı çerçeve provenansı (motor
        `_frame_provenance`, tur kimliği ve bar bağıyla). Yoksa/eşleşmiyorsa/SPOT ise kural bu sembol için
        DEĞERLENDİRİLMEZ (ne OPEN ne kural CLOSE'u SPOT ikamesiyle üretilir); ret gerekçeli kaydedilir. Açık
        pozisyonun stop/hedef/likidasyon takibi `tick` ile ayrı sürer. `marks`/`marks_f`: DOĞRULANMIŞ futures
        fiyatı (motor `_paper_marks`); `data_gaps`: fiyatı doğrulanamayan semboller (bu turda tick de yok)."""
        with self.lock:
            self.counters["tours"] += 1
            now_ms = int(now.timestamp() * 1000)
            prov_all = provenance_by_symbol or {}
            self.data_checks = {}
            self.record_gaps(data_gaps or {}, now)
            btc_fr = frames_by_symbol.get(BTC_SYMBOL) or {}
            btc = closed_bars(daily_rows_from_frame(btc_fr.get("1d")), now_ms=now_ms, tf="1d")
            from .regime_gate import btc_regime
            self.regime = btc_regime(btc)
            state = self._state(marks_f)
            # KAPSAM (V13): `symbols` = YENI giris izinli evren (olculen on coin). Defterin kendi acik
            # pozisyonlari evren disinda kalsa bile YONETILIR (kural kapanisi + stop); `decide` acik
            # pozisyonda hicbir zaman OPEN dondurmez, dolayisiyla evren disinda yeni giris olamaz.
            for sym in dict.fromkeys(list(symbols) + list(self.ledger.positions)):
                pos_open = sym in self.ledger.positions
                if sym not in marks_f:
                    g = (data_gaps or {}).get(sym)
                    if g:
                        self.data_checks[sym] = {"ok": False, "entry_ok": False, "reason": "DATA_NO_FUTURES_PRICE", "detail": g.get("detail")}
                        self.last_actions[sym] = {"action": "DATA_GAP", "reason": "DATA_NO_FUTURES_PRICE", "at": iso(now)}
                    continue
                fr = frames_by_symbol.get(sym) or {}
                verdict = verify_paper_data(symbol=sym, frames=fr, provenance=prov_all.get(sym), run_id=self.run_id,
                                            btc_frames=btc_fr, btc_provenance=prov_all.get(BTC_SYMBOL), need_btc=not pos_open)
                self.data_checks[sym] = verdict.to_dict()
                d1 = closed_bars(daily_rows_from_frame(fr.get("1d")), now_ms=now_ms, tf="1d")
                if not verdict.ok or (not pos_open and not verdict.entry_ok):
                    # Kanıtsız veriyle kural UYGULANMAZ. Yalnız bilgi için: bu veriyle kural ne derdi (uygulanmadı)?
                    try:
                        wa = decide(self.name, daily_rows=d1, btc_daily_rows=btc, position_open=pos_open, atr_mult=self.atr_mult)
                        would = str((wa or {}).get("action") or "NONE")
                    except Exception:  # noqa: BLE001
                        would = "ERROR"
                    self._reject_data(sym, verdict, "SIGNAL" if not verdict.ok else "ENTRY", now, would)
                    continue
                try:
                    act = decide(self.name, daily_rows=d1, btc_daily_rows=btc, position_open=pos_open, atr_mult=self.atr_mult)
                except Exception as exc:  # noqa: BLE001 — strateji arızası SESSİZ GEÇMEZ
                    self._reject(sym, "STRATEGY_ERROR:%s" % type(exc).__name__)
                    continue
                res = apply_action(act, symbol=sym, price=float(marks_f[sym]), tick=marks.get(sym), now=now,
                                   ledger=self.ledger, risk=self.risk, profile=self.profile, state=state,
                                   filters=self.filters_cache.get(sym, MarketType.USDM_PERP), run_id=self.run_id,
                                   reject=self._reject, on_closed=self._on_closed, on_opened=self._on_opened, data=verdict)
                if res in ("OPENED", "CLOSED"):
                    self.last_actions[sym] = {"action": res, "reason": (act or {}).get("reason"), "at": iso(now),
                                              "data": {"market": verdict.market, "source": verdict.source, "tour_id": verdict.tour_id, "bars": dict(verdict.bars)}}
                    state = self._state(marks_f)
                elif act is None and sym not in self.last_actions:
                    self.last_actions[sym] = {"action": "NONE", "reason": "NO_SIGNAL", "at": iso(now)}

    def tick(self, marks: dict[str, TickData], *, now: datetime, funding_rate_lookup=None, bar_advance: bool) -> list:
        """Defterin stop/hedef/funding/likidasyon kontrolü; ana defterle AYNI çağrı biçimi."""
        with self.lock:
            recs = self.ledger.tick(marks, now_utc=now, funding_rate_lookup=funding_rate_lookup, bar_advance=bar_advance)
            for rec in recs:
                self._on_closed(rec)
            return recs

    def save(self, marks_f: dict[str, float], now: datetime) -> None:
        with self.lock:
            self.ledger.save(self.ledger_path)
            fs = self.ledger.summary(marks_f)
            doc = {"schema_version": SCHEMA_VERSION, "generated_at": iso(now), "run_id": self.run_id,
                   "name": self.name, "atr_mult": self.atr_mult, "regime": self.regime,
                   "starting_equity": float(self.ledger.starting_equity),
                   "summary": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in fs.items()},
                   "positions": {s: {"side": p.side.value, "entry": float(p.entry_avg), "qty": float(p.qty),
                                     "stop": float(p.stop) if p.stop else None, "leverage": p.leverage,
                                     "opened_at": p.opened_at, "last_price": float(p.last_price) if p.last_price else None}
                                 for s, p in self.ledger.positions.items()},
                   "history_tail": self.ledger.history_dicts()[-50:],
                   "last_actions": self.last_actions, "counters": dict(self.counters),
                   "rejections": dict(self.rejections), "closed_recent": self.closed_recent[-20:],
                   # VERİ KAYNAĞI (2026-09-16): bu turun sembol hükümleri, fiyat boşlukları, son olaylar (yeniden başlatmada korunur)
                   "data_checks": dict(self.data_checks), "data_gaps": dict(self.data_gaps), "data_events_recent": self.data_events[-30:],
                   "data_policy": {"market": PAPER_MARKET, "rule_timeframes": list(RULE_TIMEFRAMES), "price_source": "usdm_perp_mark",
                                   "note_tr": "Yeni giriş yalnız bu turun doğrulanmış USDⓈ-M perpetual çerçevesi (+ BTC referansı) ile; "
                                              "kural kapanışı sembol çerçevesine bağlı; stop/hedef takibi doğrulanmış perp mark fiyatıyla."},
                   "note_tr": "KÂĞIT İLERİ TEST — gerçek para yok. Ana botun defterinden bağımsız."}
            doc["key"] = self.key
            atomic_write_json(Path(self.cfg.state_path) / self.summary_file, doc)


def validate_settings(*, enabled: bool, name: str | None, app_mode: str | None, starting_equity: float,
                      atr_mult: float) -> None:
    """Config doğrulaması (SAF). Gerçek parayla (LIVE) etkinleştirilemez."""
    if name not in VARIANTS:
        raise ValueError("strategy_paper.name gecersiz: %r (gecerli: %s)" % (name, ", ".join(VARIANTS)))
    if enabled and str(app_mode or "").upper() in ("LIVE", "LIVE_LIMITED"):
        raise ValueError("STRATEGY_PAPER_IS_PAPER_ONLY: strategy_paper.enabled yalniz PAPER/TESTNET/OBSERVE/SHADOW_LIVE modda")
    if starting_equity <= 0 or atr_mult <= 0:
        raise ValueError("strategy_paper.starting_equity_usdt ve atr_mult pozitif olmali")


__all__ = ["INDEX_FILE", "PAPER_MARKET", "RULE_TIMEFRAMES", "SCHEMA_VERSION", "SUMMARY_FILE", "BookSpec", "DataVerdict",
           "StrategyBook", "apply_action", "book_specs", "validate_settings", "verify_paper_data"]
