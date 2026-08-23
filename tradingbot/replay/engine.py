"""HistoricalReplay — hızlandırılmış event-time replay: aynı Coin Head / Chief / RiskEngine / FuturesLedgerV2 / SpotLedger / LearnerV2 kod yolu,
tamamen ayrı state (state/replay/<run_id>/), gerçek PAPER state'ine DOKUNMAZ. Memory source = HISTORICAL_REPLAY.

- Karar kadansı: birincil zaman diliminin (vars. 4h) bar KAPANIŞI; ileri bakış yok (frames ≤ t; pattern kanıtı yalnız t'den önceki olaylar).
- Determinizm: run_id/seed verilir; wall-clock yok (now = bar zamanı); aynı veri+config+seed → aynı işlemler ve aynı hash.
- Walk-forward: train → validation → sonraki test, purge/embargo, ileri pencere; shuffle yok. Learner öğrenmesi zaman sırasıyla ilerler.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from ..accounting import AmountType, FeeSchedule, FuturesLedgerV2, LiquidationParams, SizeSpec, SlippageModel, SpotLedger, TaxPolicy, TickData, default_brackets
from ..coinhead import ChiefPortfolioManager, CoinHeadConfig, CoinHeadInputs, CoinHeadRegistry, Verdict
from ..core import atomic_write_json, iso, stable_id
from ..indicators import add_snapshot_indicators
from ..learn import LearnConfig, LearnerV2, ModelRegistry, TradeMemory
from ..market.providers import tf_ms
from ..risk import KillSwitch, RiskEngine, build_state, enforces_position_cap, resolve_profile

log = logging.getLogger(__name__)
DAY_MS = 86_400_000


@dataclass
class WFWindow:
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    idx: int
    purge_bars: int = 0
    embargo_bars: int = 0
    bar_ms: int = 0

    def bounds(self) -> dict:
        """Kesin sınırlar (ms) — purge/embargo bölgesi [train_end, test_start) olarak açıkça kaydedilir."""
        gap = (self.purge_bars + self.embargo_bars) * self.bar_ms
        purge_ms = self.purge_bars * self.bar_ms
        return {"idx": self.idx, "train_start_ms": self.train_start, "train_end_ms": self.train_end,
                "purge_start_ms": self.train_end, "purge_end_ms": self.train_end + purge_ms,
                "embargo_start_ms": self.train_end + purge_ms, "embargo_end_ms": self.train_end + gap,
                "test_start_ms": self.test_start, "test_end_ms": self.test_end,
                "purge_bars": self.purge_bars, "embargo_bars": self.embargo_bars, "bar_ms": self.bar_ms}


def walk_forward_windows(start_ms: int, end_ms: int, *, train_days: int, test_days: int, purge_bars: int = 6,
                         embargo_bars: int = 6, bar_ms: int | None = None, tf: str | None = None) -> list[WFWindow]:
    """Anchored-forward pencereler: [train) purge [test) → sonraki pencere test kadar ileri kayar. Random shuffle YOK.
    purge/embargo BAR cinsindendir → `tf` verilirse bar süresi `tf_ms(tf)` ile hesaplanır (4h varsayımı YOK);
    ikisi de verilmezse fail-closed."""
    if bar_ms is None:
        if not tf:
            raise ValueError("walk_forward_windows: `tf` ya da `bar_ms` zorunlu (sabit 4h varsayımı kaldırıldı)")
        bar_ms = tf_ms(tf)
    out, i = [], 0
    ts = start_ms + train_days * DAY_MS
    gap = (purge_bars + embargo_bars) * bar_ms
    while ts + gap + test_days * DAY_MS <= end_ms:
        out.append(WFWindow(start_ms, ts, ts + gap, ts + gap + test_days * DAY_MS, i,
                            purge_bars=purge_bars, embargo_bars=embargo_bars, bar_ms=bar_ms))
        ts += test_days * DAY_MS
        i += 1
    return out


@dataclass
class ReplayResult:
    run_id: str
    seed: int
    symbols: list[str]
    market: str
    tf: str
    start_ms: int
    end_ms: int
    n_decisions: int = 0
    n_actionable: int = 0
    n_opened: int = 0
    trades: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    windows: list[dict] = field(default_factory=list)
    determinism_hash: str = ""
    state_dir: str = ""
    rejections: dict = field(default_factory=lambda: {"total": 0, "by_reason": {}, "by_symbol": {}})
    point_in_time: bool = False
    survivorship_bias: dict = field(default_factory=lambda: {"present": True, "note": "bugün listeli evren; delisted kapsam dışı"})
    telemetry: dict = field(default_factory=dict)      # wall/cpu/memory — determinism hash'ine GİRMEZ

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class HistoricalReplay:
    def __init__(self, cfg, *, run_id: str, store, symbols: list[str], market: str = "futures", tf: str = "4h", seed: int = 0,
                 state_root: Path | str | None = None, pattern_engine=None, start_ms: int | None = None, end_ms: int | None = None,
                 lookback_bars: int = 400, min_bars: int = 250, decision_stride: int = 1):
        self.cfg, self.run_id, self.store, self.symbols, self.market, self.tf, self.seed = cfg, run_id, store, list(symbols), market, tf, int(seed)
        self.pattern_engine = pattern_engine
        self.start_ms, self.end_ms = start_ms, end_ms
        self.lookback_bars, self.min_bars, self.stride = lookback_bars, min_bars, max(1, decision_stride)
        root = Path(state_root) if state_root else (Path(cfg.state_path) / "replay")
        # DEFENSE-IN-DEPTH: CLI doğrulamış olsa da motor kanonik kontrolü tekrar uygular
        # (traversal, symlink kaçışı, boş/tehlikeli run-id, canlı state çakışması, live-state symlink'leri).
        from .research import resolve_replay_dir
        self.state_dir = resolve_replay_dir(cfg.state_path, run_id, root)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        resolve_replay_dir(cfg.state_path, run_id, root)      # mkdir SONRASI tekrar (symlink ile değiştirilmediğini doğrula)
        v3 = cfg.v3
        ch = v3.coin_heads
        self.profile = resolve_profile(v3.risk_profiles.profile, v3.risk_profiles.overrides, i_understand=v3.risk_profiles.i_understand)
        self.head_cfg = CoinHeadConfig(consensus_threshold=ch.consensus_threshold, min_confidence=ch.min_confidence, min_expected_r=ch.min_expected_r,
                                       fee_taker_pct=v3.fees.futures_taker_pct, spot_fee_pct=v3.fees.spot_taker_pct, slippage_pct=v3.fees.slippage_bps / 100,
                                       funding_horizon_bars=ch.funding_horizon_bars, max_leverage=self.profile.futures_max_leverage,
                                       equity_usdt=cfg.futures.starting_equity_usdt, risk_pct=self.profile.risk_per_trade_pct, decision_ttl_minutes=ch.decision_ttl_minutes)
        self.registry = CoinHeadRegistry(self.head_cfg, max_workers=1)
        self.chief = ChiefPortfolioManager(clusters=v3.risk_profiles.clusters or None)
        self.killswitch = KillSwitch.load(self.state_dir / "killswitch.json")
        self.risk = RiskEngine(self.profile, self.killswitch, v3.risk_profiles.clusters or None)
        fees = FeeSchedule(maker_pct=Decimal(str(v3.fees.futures_maker_pct)), taker_pct=Decimal(str(v3.fees.futures_taker_pct)), source=v3.fees.source)
        slip = SlippageModel(fixed_bps=Decimal(str(v3.fees.slippage_bps)))
        # REPLAY <-> CANLI PAPER PARITESI: defter adet tavani canli motorla AYNI ortak sozlesmeden
        # turer (`risk.enforces_position_cap`). Eskiden replay sabit `max_positions=3` uyguluyor,
        # canli PAPER ise uygulamiyordu; replay daha az islem actigi icin trade memory / outcome /
        # loss attribution / walk-forward / arastirma politikalari YANLIS dagilimdan ogreniyordu.
        self.ledger2 = FuturesLedgerV2(cfg.futures.starting_equity_usdt, max_positions=cfg.futures.max_positions,
                                       enforce_position_cap=enforces_position_cap(self.profile),
                                       fees=fees, slippage=slip, brackets=default_brackets(),
                                       liq_params=LiquidationParams(liq_fee_pct=Decimal(str(v3.futures_v3.liq_fee_pct))), tp1_fraction=Decimal(str(v3.futures_v3.tp1_fraction)),
                                       tax_policy=TaxPolicy.disabled())
        self.spot2 = SpotLedger.load(self.state_dir / "spot_ledger.json", starting_cash=cfg.risk.starting_equity_usdt)
        self.memory = TradeMemory(self.state_dir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
        self.model_registry = ModelRegistry(self.state_dir / "models.json")
        self.learner2 = LearnerV2(self.memory, self.model_registry, LearnConfig(min_samples_train=v3.learning_v3.min_samples_train), state_path=self.state_dir / "learn_v2.json")
        from ..learn.telemetry import SnapshotTelemetry
        self.snap_telemetry = SnapshotTelemetry.load(self.state_dir)     # snapshot hatalari sessiz kalmaz
        self.frames: dict[str, dict[str, pd.DataFrame]] = {}
        self.primary: dict[str, pd.DataFrame] = {}
        self._entry_meta: dict[str, dict] = {}
        self.result = ReplayResult(run_id, self.seed, self.symbols, market, tf, 0, 0, state_dir=str(self.state_dir))

    # ------------------------------------------------------------ veri
    def load(self) -> None:
        for sym in self.symbols:
            fr = {}
            for tf in ("1d", self.tf, "1h"):
                df = self.store.read(self.market, sym, tf)
                if len(df) >= 50:
                    ind = add_snapshot_indicators(df[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True))
                    fr[tf] = ind
            if self.tf in fr and len(fr[self.tf]) >= self.min_bars:
                self.frames[sym] = fr
                self.primary[sym] = fr[self.tf]
        if not self.primary:
            raise ValueError("replay için yeterli veri yok")
        all_ts = sorted(set(int(t) for df in self.primary.values() for t in df["timestamp"]))
        s = self.start_ms or all_ts[self.min_bars]
        e = self.end_ms or all_ts[-2]
        self.timeline = [t for t in all_ts if s <= t <= e][:: self.stride]
        self.result.start_ms, self.result.end_ms = int(self.timeline[0]), int(self.timeline[-1])

    def _slice(self, sym: str, t: int) -> dict[str, pd.DataFrame]:
        out = {}
        for tf, df in self.frames[sym].items():
            step = tf_ms(tf)
            sub = df[df["timestamp"] + step - 1 <= t + tf_ms(self.tf) - 1]      # yalnız t'de KAPANMIŞ barlar
            if len(sub) >= 30:
                out[tf] = sub.tail(self.lookback_bars).reset_index(drop=True)
        return out

    def _portfolio_state(self, marks_f: dict[str, float], now: datetime):
        pos = [{"symbol": s, "market_type": "USDM_PERP", "side": p.side.value, "notional": float(p.qty * p.entry_avg), "margin": float(p.isolated_margin),
                "entry": float(p.entry_avg), "stop": float(p.stop) if p.stop else None, "leverage": p.leverage, "opened_at": p.opened_at} for s, p in self.ledger2.positions.items()]
        fs = self.ledger2.summary(marks_f)
        return build_state(equity=float(fs["equity_mtm"]), starting_equity=float(self.ledger2.starting_equity), available=float(fs["available"]),
                           used_margin=float(fs["used_margin"]), positions=pos, history=self.ledger2.history_dicts(), high_water_mark=0.0, now=now,
                           clusters=self.cfg.v3.risk_profiles.clusters or None)

    # ------------------------------------------------------------ çalıştır
    def run(self, *, windows: list[WFWindow] | None = None, on_progress=None) -> ReplayResult:
        self.load()
        seq = 0
        wf = windows or []
        for t in self.timeline:
            seq += 1
            now = datetime.fromtimestamp((t + tf_ms(self.tf)) / 1000, tz=timezone.utc)      # bar kapanış anı
            inputs, marks, marks_f, bars = {}, {}, {}, {}
            for sym in self.primary:
                fr = self._slice(sym, t)
                if self.tf not in fr:
                    continue
                cur = fr[self.tf].iloc[-1]
                if int(cur["timestamp"]) != t:
                    continue                                                    # bu sembolde t'de bar yok
                price = float(cur["close"])
                bars[sym] = cur
                marks[sym] = TickData(last=Decimal(str(price)), mark=Decimal(str(price)))
                marks_f[sym] = price
                ev = None
                if self.pattern_engine is not None:
                    key = (sym, self.market, self.tf)
                    if key in self.pattern_engine.candles:
                        cdf = self.pattern_engine.candles[key]
                        idx = int(cdf.index[cdf["timestamp"] == t][0]) if (cdf["timestamp"] == t).any() else None
                        if idx is not None:
                            ev = {side: self.pattern_engine.query(sym, self.market, self.tf, side, idx=idx, k=60) for side in (("LONG", "SHORT") if self.market == "futures" else ("LONG",))}
                opos = self.ledger2.positions.get(sym)
                same_dir = {"LONG": sum(1 for p in self.ledger2.positions.values() if p.side.value == "LONG"), "SHORT": sum(1 for p in self.ledger2.positions.values() if p.side.value == "SHORT")}
                inputs[sym] = CoinHeadInputs(frames=fr, live={"ticker": {"last": price, "high": float(cur["high"]), "low": float(cur["low"])}, "ts": (t + tf_ms(self.tf)) / 1000},
                                             availability={"spot": self.market == "spot", "futures": self.market == "futures"}, quality={"ok": True, "verdict": "OK", "issues": []},
                                             btc_frames=self.frames.get("BTC/USDT"), portfolio={"same_direction_open": same_dir, "kill_switch_active": not self.killswitch.allows_entry(),
                                                                                                "open_position": {"side": opos.side.value} if opos else None},
                                             pattern_evidence=ev, run_id=self.run_id, snapshot_id=stable_id("snap", self.run_id, t), now_ms=t + tf_ms(self.tf) - 1,
                                             snapshot_at_ms=t + tf_ms(self.tf) - 1, snapshot_seq=seq)
            if not inputs:
                continue
            decisions = self.registry.run_many(inputs)
            self.result.n_decisions += len(decisions)
            state = self._portfolio_state(marks_f, now)
            btc_dec = decisions.get("BTC/USDT")
            chief = self.chief.decide(list(decisions.values()), {"equity": state.equity, "open_positions": [o.to_dict() for o in state.open_positions],
                                                                 "total_open_risk_usdt": state.total_open_risk_usdt,
                                                                 # ADVISORY projeksiyon YETKILI kapiyla ayni kovayi olcsun:
                                                                 # birlesik toplam kullanilirsa panel "sigmaz" derken motor kabul eder.
                                                                 "futures_stop_risk_usdt": state.futures_stop_risk_usdt,
                                                                 "spot_exposure_usdt": state.spot_exposure_usdt,
                                                                 "pnl_today": state.realized_pnl_today,
                                                                 "drawdown_pct": state.drawdown_pct}, btc_regime=btc_dec.regime if btc_dec else None)
            in_test = any(w.test_start <= t < w.test_end for w in wf) if wf else True
            for sym in chief.priority + [s for s, d in decisions.items() if d.is_actionable and s not in chief.priority]:
                d = decisions.get(sym)
                if d is None or not d.is_actionable or sym in self.ledger2.positions:
                    continue
                self.result.n_actionable += 1
                plan = d.active_plan
                if plan is None or not plan.valid or not chief.permission.get(sym, {}).get("allow"):
                    continue
                mkt = "SPOT" if d.verdict == Verdict.SPOT_LONG else "USDM_PERP"
                if mkt == "SPOT" and self.market != "spot":
                    continue
                plan_dict = {"symbol": sym, "market_type": mkt, "direction": d.direction, "entry": plan.entry, "stop": plan.stop, "targets": plan.targets,
                             "notional": plan.notional, "margin": plan.margin, "leverage": plan.size.leverage, "amount_type": "NOTIONAL", "expected_r": plan.expected_r,
                             "min_notional": 5.0}
                rd = self.risk.evaluate(plan_dict, state, {"now_utc": now})
                if not rd.allowed:
                    self._reject(sym, (rd.reasons or ["RISK_DENIED"])[0])
                    continue
                notional = float(rd.adjusted_notional or plan.notional or 0)
                if notional <= 0:
                    self._reject(sym, "ZERO_NOTIONAL")
                    continue
                if mkt == "USDM_PERP":
                    pos = self.ledger2.open(sym, d.direction, marks_f[sym], SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, int(rd.adjusted_leverage or 1)),
                                            stop=plan.stop, targets=plan.targets, setup_type=plan.entry_type, trigger_text=plan.entry_trigger,
                                            features={"regime": d.regime, "expected_r": d.expected_r, "p_win": d.p_win, "market_type": mkt},
                                            tick=marks[sym], now=now, meta={"run_id": self.run_id, "replay": True, "in_test": in_test})
                    if pos is None:
                        # gerçek borsa filtresi (ör. STEP_ZERO_QTY): minimumlar GEVŞETİLMEZ, sahte fill üretilmez;
                        # yalnız sayılır ve raporda sembol bazında görünür.
                        self._reject(sym, self.ledger2.last_reject_reason or "LEDGER_REJECT")
                        continue
                    self._entry_meta[pos.id] = {"symbol": sym, "in_test": in_test, "regime": d.regime, "decision": d.to_dict(include_reports=False), "opened_ts": t}
                    # KÖK NEDEN DÜZELTMESİ: yalnız expected_r/p_win değil, PAYLAŞILAN FeatureSnapshotV3
                    # (MA/volatilite/hacim/funding/mikroyapı/ajan/plan bağlamı) — canlı yolla aynı builder.
                    snap = self._snapshot(sym, t, d, plan, mkt, marks_f)
                    self.memory.record_entry({"trade_id": pos.id, "symbol": sym, "direction": d.direction, "market_type": mkt,
                                              "setup_type": plan.entry_type, "regime": d.regime,
                                              "features": (snap.vector() if snap else {"expected_r": d.expected_r, "p_win": d.p_win}),
                                              "snapshot": (snap.to_dict() if snap else None),
                                              "decision": d.to_dict(include_reports=False), "run_id": self.run_id, "in_test": in_test})
                    self.result.n_opened += 1
                    state = self._portfolio_state(marks_f, now)                       # aynı adımda sonraki aday güncel durumu görür
            # sonraki barın uçlarıyla tick (bir sonraki karar anına kadar olan bar) — event-time ilerleme
            self._advance(t, now)
            if on_progress and seq % 50 == 0:
                on_progress({"t": iso(now), "decisions": self.result.n_decisions, "opened": self.result.n_opened, "closed": len(self.result.trades)})
        self._finish(wf)
        return self.result

    def _advance(self, t: int, now: datetime) -> None:
        marks = {}
        for sym, df in self.primary.items():
            nxt = df[df["timestamp"] > t].head(1)
            if len(nxt):
                r = nxt.iloc[0]
                marks[sym] = TickData(last=Decimal(str(float(r["close"]))), mark=Decimal(str(float(r["close"]))), high=Decimal(str(float(r["high"]))), low=Decimal(str(float(r["low"]))))
        if not marks:
            return
        nxt_now = datetime.fromtimestamp((t + 2 * tf_ms(self.tf)) / 1000, tz=timezone.utc)
        recs = self.ledger2.tick(marks, now_utc=nxt_now, bar_advance=True)
        for rec in recs:
            legacy = rec.to_legacy_dict()
            meta = self._entry_meta.pop(rec.id, {})
            snap = meta.get("decision") or {}
            self.learner2.on_trade_closed(legacy | {"features": legacy.get("features") or {}}, {"regime": snap.get("regime"), "consensus_score": snap.get("consensus_score"),
                                                                                                "dissent": snap.get("dissent"), "vetoes": snap.get("vetoes")})
            self.result.trades.append({"id": rec.id, "symbol": rec.symbol, "side": rec.side, "entry": float(rec.entry), "exit": float(rec.exit_price) if rec.exit_price else None,
                                       "exit_reason": rec.exit_reason, "net_r": float(rec.r_multiple), "net_pnl": float(rec.net_pnl), "fees": float(rec.fees),
                                       "funding": float(rec.funding), "bars_held": rec.bars_held, "mae_pct": float(rec.mae_pct), "mfe_pct": float(rec.mfe_pct),
                                       "opened_at": rec.opened_at, "closed_at": rec.closed_at, "in_test": bool(meta.get("in_test", True)), "regime": meta.get("regime")})

    def _snapshot(self, sym: str, t: int, d, plan, market_type: str, marks_f: dict):
        """Karar ani FeatureSnapshotV3 -- yalniz t'de KAPANMIS barlardan.

        Canli PAPER yolu (`TradingEngineV3._snapshot_v3`) ile AYNI builder ve AYNI esleme yardimcilarini
        (`agents_from_factor_scores`, `pattern_fields_from_evidence`) kullanir; iki yol birbirinden
        sessizce ayrilamaz (`test_replay_and_live_prediction_vectors_match` bunu dogrular).
        """
        from ..learn.snapshot import (LeakageError, agents_from_factor_scores, build_snapshot,
                                      pattern_fields_from_evidence)
        try:
            fr = self._slice(sym, t)
            bars = fr.get(self.tf)
            if bars is None or len(bars) < 30:
                return None
            btc = None
            if "BTC/USDT" in self.frames and sym != "BTC/USDT":
                btc = self._slice("BTC/USDT", t).get(self.tf)
            cons = d.consensus if isinstance(getattr(d, "consensus", None), dict) else {}
            snap = build_snapshot(
                symbol=sym, market_type=market_type, timeframe=self.tf, side=d.direction,
                decision_ts_ms=int(t), bars=bars, source="HISTORICAL_REPLAY", btc_bars=btc,
                decision={"consensus_score": (sum(cons.values()) / len(cons)) if cons else None,
                          "consensus_conf": getattr(d, "consensus_confidence", None),
                          "n_dissent": len(getattr(d, "dissent", []) or []),
                          "n_vetoes": len(getattr(d, "vetoes", []) or []),
                          "head_confidence": getattr(d, "confidence_calibrated", None)},
                plan={"setup_type": plan.entry_type, "expected_r": plan.expected_r,
                      "expected_cost_pct": plan.expected_cost_pct, "p_win": d.p_win,
                      "entry": marks_f.get(sym), "stop": plan.stop, "targets": list(plan.targets or []),
                      "rr": plan.rr, "leverage": plan.size.leverage,
                      "notional": plan.notional, "margin": plan.margin},
                pattern=pattern_fields_from_evidence(getattr(d, "pattern_evidence", None), d.direction),
                agents=agents_from_factor_scores(getattr(d, "factor_scores", None)),
                run_id=self.run_id, seed=self.seed, strict=True)
            if snap.last_bar_ts > snap.decision_ts:      # ikinci savunma hatti (fail-closed)
                raise LeakageError(f"last_bar_ts {snap.last_bar_ts} > decision_ts {snap.decision_ts}")
            self.snap_telemetry.success()
            return snap
        except LeakageError as exc:
            self.snap_telemetry.failure(exc, leakage=True)
            raise
        except Exception as exc:  # noqa: BLE001 -- snapshot uretilemezse replay durmaz, kayit "eksik" isaretlenir
            self.snap_telemetry.failure(exc)
            log.warning("%s snapshot uretilemedi: %s", sym, exc)
            return None

    def _reject(self, symbol: str, reason: str) -> None:
        """Red nedenlerini sembol/neden kırılımıyla say (sessiz yutma yok)."""
        r = self.result.rejections
        reason = str(reason or "UNKNOWN")
        r["total"] = int(r.get("total", 0)) + 1
        r["by_reason"][reason] = int(r["by_reason"].get(reason, 0)) + 1
        r["by_symbol"].setdefault(symbol, {})
        r["by_symbol"][symbol][reason] = int(r["by_symbol"][symbol].get(reason, 0)) + 1

    def _finish(self, wf: list[WFWindow]) -> None:
        tr = self.result.trades
        def _m(ts: list[dict]) -> dict:
            if not ts:
                return {"n": 0, "expectancy_r": None, "win_rate": None, "max_dd_r": None, "net_pnl": 0.0}
            r = [x["net_r"] for x in ts]
            eq, peak, dd = 0.0, 0.0, 0.0
            for v in r:
                eq += v; peak = max(peak, eq); dd = max(dd, peak - eq)
            return {"n": len(r), "expectancy_r": sum(r) / len(r), "win_rate": sum(1 for v in r if v > 0) / len(r), "max_dd_r": dd,
                    "net_pnl": sum(x["net_pnl"] for x in ts), "profit_factor": (sum(v for v in r if v > 0) / abs(sum(v for v in r if v < 0))) if any(v < 0 for v in r) else None}
        self.result.metrics = {"all": _m(tr), "out_of_sample": _m([x for x in tr if x["in_test"]]), "in_sample": _m([x for x in tr if not x["in_test"]]),
                               "by_side": {s: _m([x for x in tr if x["side"] == s]) for s in ("LONG", "SHORT")},
                               "learner": self.learner2.snapshot(), "ledger": self.ledger2.summary({})}
        for w in wf:
            ts = [x for x in tr if w.test_start <= int(datetime.fromisoformat(x["opened_at"]).timestamp() * 1000) < w.test_end]
            self.result.windows.append({"idx": w.idx, "train": [iso(datetime.fromtimestamp(w.train_start / 1000, tz=timezone.utc)), iso(datetime.fromtimestamp(w.train_end / 1000, tz=timezone.utc))],
                                        "test": [iso(datetime.fromtimestamp(w.test_start / 1000, tz=timezone.utc)), iso(datetime.fromtimestamp(w.test_end / 1000, tz=timezone.utc))],
                                        "bounds": w.bounds(), **_m(ts)})
        canon = json.dumps([[x["symbol"], x["side"], round(x["entry"], 8), round(x["exit"] or 0, 8), x["exit_reason"], round(x["net_r"], 6)] for x in tr], sort_keys=True)
        self.result.determinism_hash = hashlib.sha256(canon.encode()).hexdigest()
        self.ledger2.save(self.state_dir / "futures_ledger.json")
        self.spot2.save(self.state_dir / "spot_ledger.json")
        self.learner2.save()
        self.snap_telemetry.save()
        atomic_write_json(self.state_dir / "replay_result.json", self.result.to_dict(), indent=1)


__all__ = ["HistoricalReplay", "ReplayResult", "WFWindow", "walk_forward_windows"]
