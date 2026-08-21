"""COIN HEAD — bir coin için uzman raporlarını, karşı görüşleri ve riskleri birleştirir; Spot ve Futures planı üretir.

Öncelik: veri doğru mu → maliyet sonrası edge var mı → risk kabul edilebilir mi → red team veto var mı → plan.
Şüphe varsa NO_TRADE. LLM burada çalışmaz; yalnızca yapılandırılmış öneri (advisory) girdi olarak alınabilir.

RED TEAM SÖZLEŞMESİ: yalnız `hard_veto_codes` planı geçersiz yapar. `soft_penalty_codes`
(zayıf OOS edge, korelasyon/yığılma, funding, yeni listelenme, rejim uyumsuzluğu, orta seviye
spread/derinlik, tercih dışı fakat geçerli stop) `plan.soft_flags`'e yazılır ve yalnızca boyutu
küçültür — "10 ayrı engelden geçemezse hiç işlem açma" davranışı bilinçli olarak kaldırılmıştır.

LLM SÖZLEŞMESİ: advisory çıktı TEK BAŞINA hard veto ÜRETEMEZ. `llm_advice["veto"]` yalnız kayıtlı
`RED_TEAM_SOFT_PENALTY` yumuşak cezasına ve telemetriye dönüşür. Bir hard gate ancak kaynak kod
tarafından ölçülmüş ve `decision_gates.GATES`'te kayıtlı gerçek güvenlik koşuluyla üretilebilir;
model metnindeki serbest bir kod/reason asla kapı olarak kabul edilmez. Deterministik şema hatası
(`LLM_SCHEMA_INVALID`) bunun dışındadır ve fail-closed kalır.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..core import iso, stable_id, utc_now
from ..core.timeutil import from_ms
from .factors import aggregate, consensus
from .redteam import RedTeamContext, RedTeamVetoAgent
from .schema import (NO_TRADE_DATA_INVALID, NO_TRADE_LOW_CONSENSUS, NO_TRADE_MARKET_UNAVAILABLE, NO_TRADE_MIN_ORDER_CONFLICT,
                     NO_TRADE_NO_VALID_PLAN, NO_TRADE_RED_TEAM_VETO, CoinHeadDecision, PlanSize, TradePlanV3, Verdict, new_decision)
from .specialists import NEW_SPECIALISTS, SpecialistContext, adapt_legacy_reports


@dataclass
class CoinHeadConfig:
    # ASAGIDAKI UC ESIK ARTIK SERT VETO DEGILDIR. Ard arda dizilen "hepsi gecmeli" zinciri, maliyet
    # sonrasi pozitif firsatlari sistematik olarak olduruyordu. Bunlar artik YUMUSAK KANIT uretir ve
    # `opportunity.conservative_net_edge_r` uzerinden puani/boyutu dusurur.
    consensus_threshold: float = 0.22          # tercih edilen konsensus (SOFT: LOW_CONSENSUS)
    min_confidence: float = 0.25               # tercih edilen guven      (SOFT: LOW_CONFIDENCE)
    min_expected_r: float = 1.5                # tercih edilen R/R        (SOFT: RR_BELOW_PREFERRED)
    # Yon belirlemek icin gereken asgari sinyal: bunun altinda "anlamli yon yok" demektir.
    direction_epsilon: float = 0.05
    fee_taker_pct: float = 0.05                # futures
    spot_fee_pct: float = 0.10
    slippage_pct: float = 0.03
    funding_horizon_bars: int = 12             # 4h bar → 48 saat → 6 funding dönemi
    max_leverage: int = 5
    equity_usdt: float = 50.0
    risk_pct: float = 2.0
    min_notional_spot: float = 5.0
    min_notional_futures: float = 5.0
    decision_ttl_minutes: int = 240
    factor_weights: dict[str, float] = field(default_factory=dict)
    calibration: dict[str, float] = field(default_factory=dict)   # grup → geçmiş kalibrasyon çarpanı (öğrenmeden)


@dataclass
class CoinHeadInputs:
    frames: dict[str, pd.DataFrame]
    live: dict[str, Any] = field(default_factory=dict)
    legacy_reports: list[Any] | None = None       # AgentReport listesi
    legacy_brief: Any | None = None               # CoinBrief (plan/seviye/last_close_4h için)
    availability: dict[str, bool] = field(default_factory=lambda: {"spot": True, "futures": True})
    quality: dict[str, Any] = field(default_factory=dict)
    btc_frames: dict[str, pd.DataFrame] | None = None
    eth_frames: dict[str, pd.DataFrame] | None = None
    btc_regime: str | None = None
    portfolio: dict[str, Any] = field(default_factory=dict)      # {same_direction_open:{LONG:n,SHORT:n}, net_exposure:{symbol: float}, kill_switch_active, open_position:{side,...}}
    edge: dict[str, Any] | None = None                            # {has_edge, oos_sharpe, oos_trades}
    filters: dict[str, Any] = field(default_factory=dict)        # {spot:{min_notional,qty_step,...}, futures:{...,max_leverage}}
    llm_advice: dict[str, Any] | None = None                      # {veto, decision_support, ...} yalnızca ADVISORY/VETO_ONLY
    run_id: str = ""
    snapshot_id: str = ""                                         # opak/benzersiz kimlik — sıralama için KULLANILMAZ
    now_ms: int | None = None
    snapshot_at_ms: int | None = None                             # olay zamanı (engine run zamanı, UTC ms); None → now_ms
    snapshot_seq: int = 0                                         # aynı zaman damgası için deterministik tie-breaker (tur sırası)
    pattern_evidence: dict[str, Any] | None = None               # SimilarPatternEngine.query çıktısı {"LONG": {...}, "SHORT": {...}} (opsiyonel)
    listing_age_days: float | None = None


class CoinHead:
    def __init__(self, symbol: str, cfg: CoinHeadConfig | None = None, coin_head_id: str | None = None):
        self.symbol = symbol
        self.cfg = cfg or CoinHeadConfig()
        self.coin_head_id = coin_head_id or stable_id("coinhead", symbol)
        self.last_decision: CoinHeadDecision | None = None

    # ------------------------------------------------------------ yardımcılar
    def _price(self, inp: CoinHeadInputs) -> float:
        t = (inp.live or {}).get("ticker") or {}
        if t.get("last"):
            return float(t["last"])
        for tf in ("1h", "4h", "1d"):
            df = inp.frames.get(tf)
            if df is not None and len(df):
                return float(df["close"].iloc[-1])
        return 0.0

    def _plan_from_legacy(self, brief: Any, market: str, direction: str, price: float) -> TradePlanV3 | None:
        p = getattr(brief, "plan", None)
        if p is None or p.direction != direction or not p.entry or not p.stop:
            return None
        # sağlamlık: giriş fiyattan %5'ten uzaksa ya da stop/target tutarsızsa legacy planı kullanma (ATR planına düş)
        if p.entry <= 0 or p.stop <= 0 or price <= 0 or abs(p.entry / price - 1) > 0.05:
            return None
        if p.target1 and abs(p.target1 / p.entry - 1) > 0.5:      # hedef makul değil (ölçek/veri tutarsızlığı)
            return None
        if (direction == "LONG" and not (p.stop < p.entry < (p.target1 or p.entry + 1))) or \
           (direction == "SHORT" and not (p.stop > p.entry > (p.target1 or p.entry - 1))):
            return None
        lo, hi = (p.entry * 0.999, p.entry * 1.001)
        plan = TradePlanV3(market_type=market, direction=direction, entry_type={"kırılım": "breakout", "geri çekilme": "pullback"}.get(p.entry_type, p.entry_type or "market"),
                           entry_trigger=p.trigger_text, entry_zone=(lo, hi), invalidation=f"stop {p.stop:.6g}", stop=float(p.stop),
                           targets=[float(x) for x in (p.target1, p.target2) if x], time_horizon_bars=self.cfg.funding_horizon_bars,
                           size=PlanSize(amount=float(p.notional_usdt or 0), amount_type="NOTIONAL", leverage=int(p.suggested_leverage or 1) if market == "futures" else 1),
                           margin=float(p.margin_usdt or 0) if market == "futures" else float(p.notional_usdt or 0), notional=float(p.notional_usdt or 0))
        return plan

    def _plan_from_atr(self, inp: CoinHeadInputs, market: str, direction: str, price: float) -> TradePlanV3 | None:
        df = inp.frames.get("4h")
        if df is None or len(df) < 30 or price <= 0:
            return None
        atr_pct = float(df["atr_pct"].iloc[-1]) if "atr_pct" in df else float(df["atr14"].iloc[-1]) / float(df["close"].iloc[-1]) * 100
        if not atr_pct or atr_pct != atr_pct:
            return None
        dist = price * atr_pct / 100 * 2.5      # yüzde bazlı: canlı fiyat ile çerçeve ölçeği farklı olsa bile tutarlı
        stop = price - dist if direction == "LONG" else price + dist
        t1 = price + 2 * dist if direction == "LONG" else price - 2 * dist
        t2 = price + 3 * dist if direction == "LONG" else price - 3 * dist
        return TradePlanV3(market_type=market, direction=direction, entry_type="pullback", entry_trigger=f"{price:.6g} civarı teyitli giriş",
                           entry_zone=(price * 0.9975, price * 1.0025), invalidation=f"stop {stop:.6g}", stop=stop, targets=[t1, t2],
                           time_horizon_bars=self.cfg.funding_horizon_bars, size=PlanSize(0.0, "NOTIONAL", 1))

    def _cost_and_r(self, plan: TradePlanV3, market: str, funding_pct: float | None, spread_pct: float | None) -> None:
        """Beklenen maliyet (% notional, gidiş-dönüş) ve beklenen R hesabı; plan.valid belirlenir."""
        fee = (self.cfg.spot_fee_pct if market == "spot" else self.cfg.fee_taker_pct) * 2
        slip = self.cfg.slippage_pct * 2
        spr = (spread_pct or 0.0)
        fund = 0.0
        if market == "futures" and funding_pct is not None:
            periods = max(1, self.cfg.funding_horizon_bars * 4 // 8)
            signed = funding_pct if plan.direction == "LONG" else -funding_pct
            fund = max(0.0, signed) * periods
        plan.expected_cost_pct = round(fee + slip + spr + fund, 4)
        e = plan.entry
        if not e or not plan.stop or not plan.targets:
            plan.valid, plan.invalid_reason = False, "plan eksik"
            return
        risk = abs(e - plan.stop) / e * 100
        reward = abs(plan.targets[0] - e) / e * 100
        plan.expected_r = round((reward - plan.expected_cost_pct) / risk, 3) if risk else 0.0
        # ASAMA 1 -- YALNIZ GEOMETRI: entry/stop/target var mi, stop mesafesi pozitif mi.
        # ASAMA 2 (ekonomi) motorda `opportunity.assess()` ile yapilir; sabit R/R esigi orada
        # YUMUSAK kanit olarak yer alir. Boylece R/R 1.2 ama yuksek kalibre p_win'li bir islem
        # maliyet sonrasi pozitifse yasayabilir; R/R 2.0 ama dusuk p_win'li islem elenir.
        if risk <= 0:
            plan.valid, plan.invalid_reason = False, "stop mesafesi sıfır"      # HARD: ZERO_STOP_DISTANCE
        else:
            plan.valid, plan.invalid_reason = True, ""
            plan.soft_flags = list(plan.soft_flags)
            if plan.expected_r < self.cfg.min_expected_r:
                plan.soft_flags.append("RR_BELOW_PREFERRED")

    def _size(self, plan: TradePlanV3, market: str, inp: CoinHeadInputs) -> None:
        from ..risk.engine import size_position
        f = (inp.filters or {}).get(market, {}) or {}
        min_notional = float(f.get("min_notional", self.cfg.min_notional_spot if market == "spot" else self.cfg.min_notional_futures))
        max_lev = 1 if market == "spot" else int(f.get("max_leverage", self.cfg.max_leverage))
        res = size_position(equity=self.cfg.equity_usdt, risk_pct=self.cfg.risk_pct, entry=plan.entry, stop=plan.stop, min_notional=min_notional,
                            max_leverage=max_lev, max_position_pct=30.0, liq_buffer_mult=3.0 if market == "futures" else None,
                            requested_leverage=plan.size.leverage if market == "futures" else 1)
        if not res.ok:
            plan.valid, plan.invalid_reason = False, res.reason
            plan.notional, plan.margin = res.notional, res.margin
            return
        # legacy planın notional'ı varsa küçük olanı kullan (risk asla büyütülmez)
        notional = min(res.notional, plan.notional) if plan.notional else res.notional
        plan.notional, plan.margin = round(notional, 4), round(notional / res.leverage, 4)
        plan.size = PlanSize(amount=plan.notional, amount_type="NOTIONAL", leverage=res.leverage)

    # ------------------------------------------------------------ ana karar
    def decide(self, inp: CoinHeadInputs) -> CoinHeadDecision:
        t0 = time.time()
        run_id = inp.run_id or "run_adhoc"
        snap = inp.snapshot_id or stable_id("snap", self.symbol, iso())
        d = new_decision(self.coin_head_id, run_id, snap, self.symbol)
        price = self._price(inp)
        f_fut = (inp.filters or {}).get("futures", {}) or {}
        ctx = SpecialistContext(symbol=self.symbol, run_id=run_id, snapshot_id=snap, frames=inp.frames, live=inp.live,
                                market_type="both", quality=inp.quality, btc_frames=inp.btc_frames, eth_frames=inp.eth_frames,
                                equity_usdt=self.cfg.equity_usdt, risk_pct=self.cfg.risk_pct, filters=f_fut, fee_taker_pct=self.cfg.fee_taker_pct,
                                max_leverage=self.cfg.max_leverage, now_ms=inp.now_ms, listing_age_days=inp.listing_age_days,
                                pattern_evidence=inp.pattern_evidence)
        # 1) uzmanlar
        reports = adapt_legacy_reports(inp.legacy_reports or [], ctx)
        for spec in NEW_SPECIALISTS:
            reports.append(spec(ctx))
        rep_by = {r.agent_name: r for r in reports}
        integrity = rep_by.get("data_integrity")
        regime = str((rep_by.get("market_regime").metrics if rep_by.get("market_regime") else {}).get("regime", "UNKNOWN"))
        d.regime = regime
        d.specialist_reports = reports
        d.pattern_evidence = inp.pattern_evidence      # karar akisinda hesaplanan kanit; snapshot yeniden sorgulamaz
        d.data_freshness = {"ticker_age_s": integrity.data_freshness_seconds if integrity else None, "issues": (integrity.metrics or {}).get("issues", []) if integrity else []}
        d.model_versions = {"coin_head": "v3.0", "factor_groups": "v3.0", "legacy_agents": "1"}
        d.expires_at = iso(from_ms((inp.now_ms or int(utc_now().timestamp() * 1000)) + self.cfg.decision_ttl_minutes * 60_000))
        # 2) veri bütünlüğü
        if integrity is not None and integrity.veto:
            d.verdict, d.no_trade_reason, d.vetoes = Verdict.DATA_INVALID, NO_TRADE_DATA_INVALID, [integrity.veto_reason]
            d.latency_ms = round((time.time() - t0) * 1000, 1)
            self.last_decision = d
            return d
        # 3) faktör grupları + konsensüs
        d.factor_scores = aggregate(reports)
        score, conf, dissent = consensus(d.factor_scores, regime, self.cfg.factor_weights, self.cfg.calibration)
        d.consensus = {g.group: g.score for g in d.factor_scores}
        d.consensus_score, d.consensus_confidence, d.dissent = score, conf, dissent
        d.confidence_raw = round(min(1.0, abs(score) / 0.6), 4)
        d.confidence_calibrated = round(conf, 4)
        d.evidence = [{"agent": r.agent_name, "for": r.evidence_for[:3], "against": r.evidence_against[:3]} for r in reports if r.usable]
        # Yon: anlamsiz derecede kucuk sinyal islem uretmez; fakat 0.22'lik eski esik artik SERT
        # degil YUMUSAK kanittir (yon gucu p_win/belirsizlik/edge hesabina girer).
        eps = self.cfg.direction_epsilon
        direction = "LONG" if score >= eps else ("SHORT" if score <= -eps else "")
        # açık pozisyon varsa: yön korunuyorsa HOLD, ters dönerse EXIT, zayıflarsa REDUCE
        opos = (inp.portfolio or {}).get("open_position")
        if opos:
            side = opos.get("side", "LONG")
            if direction == side:
                d.verdict, d.direction = Verdict.HOLD, side
            elif direction and direction != side:
                d.verdict, d.direction, d.no_trade_reason = Verdict.EXIT, side, "konsensüs tersine döndü"
            else:
                d.verdict, d.direction, d.no_trade_reason = Verdict.REDUCE, side, "konsensüs zayıfladı"
            d.latency_ms = round((time.time() - t0) * 1000, 1)
            self.last_decision = d
            return d
        if not direction:
            d.verdict, d.no_trade_reason = Verdict.NO_TRADE, NO_TRADE_LOW_CONSENSUS
            d.latency_ms = round((time.time() - t0) * 1000, 1)
            self.last_decision = d
            return d
        # Zayif konsensus/guven artik REDDETMEZ; yumusak kanit olarak tasinir ve ekonomik
        # degerlendirmede puani ve pozisyon boyutunu dusurur.
        if abs(score) < self.cfg.consensus_threshold:
            d.soft_flags.append("LOW_CONSENSUS")
        if conf < self.cfg.min_confidence:
            d.soft_flags.append("LOW_CONFIDENCE")
        if len(dissent) >= 3:
            d.soft_flags.append("HIGH_DISSENT")
        d.direction = direction
        # 4) planlar
        der = rep_by.get("derivatives")
        funding_pct = (der.metrics or {}).get("funding_pct") if der and der.usable else None
        ob = rep_by.get("orderbook_liquidity")
        spread = (ob.metrics or {}).get("spread_pct") if ob and ob.usable else None
        avail = inp.availability or {}
        plans: dict[str, TradePlanV3] = {}
        for market in ("spot", "futures"):
            if not avail.get(market, False):
                continue
            if market == "spot" and direction == "SHORT":
                continue
            plan = self._plan_from_legacy(inp.legacy_brief, market, direction, price) if inp.legacy_brief is not None else None
            plan = plan or self._plan_from_atr(inp, market, direction, price)
            if plan is None:
                continue
            self._cost_and_r(plan, market, funding_pct if market == "futures" else None, spread)
            if plan.valid:
                self._size(plan, market, inp)
            plans[market] = plan
        d.spot_plan, d.futures_plan = plans.get("spot"), plans.get("futures")
        if not plans:
            d.verdict, d.no_trade_reason = Verdict.NO_TRADE, NO_TRADE_MARKET_UNAVAILABLE if not any(avail.values()) else NO_TRADE_NO_VALID_PLAN
            d.latency_ms = round((time.time() - t0) * 1000, 1)
            self.last_decision = d
            return d
        # 5) red team (piyasa bazında; futures'ta funding/liq eklenir)
        edge = inp.edge or {}
        pf = inp.portfolio or {}
        same_dir = (pf.get("same_direction_open") or {}).get(direction, 0)
        atr_pct = float(inp.frames["4h"]["atr_pct"].iloc[-1]) if inp.frames.get("4h") is not None and len(inp.frames["4h"]) else None
        rt_reports = {}
        for market, plan in plans.items():
            liq_dist = None
            if market == "futures" and plan.size.leverage:
                liq_dist = (1.0 / plan.size.leverage - 0.004) * 100
            rt = RedTeamContext(direction=direction, data_stale=bool(d.data_freshness.get("ticker_age_s") and d.data_freshness["ticker_age_s"] > 300),
                                missing_4h="MISSING_4H_FRAME" in d.data_freshness.get("issues", []),
                                spread_pct=spread, depth_usdt=(ob.metrics or {}).get("depth_top20_usdt") if ob and ob.usable else None,
                                expected_cost_pct=plan.expected_cost_pct, expected_return_gross_pct=abs(plan.targets[0] - plan.entry) / plan.entry * 100 if plan.targets and plan.entry else None,
                                oos_sharpe=edge.get("oos_sharpe"), oos_trades=edge.get("oos_trades"), has_edge=edge.get("has_edge"),
                                corr_btc=(rep_by.get("correlation_beta").metrics or {}).get("corr_btc_120b") if rep_by.get("correlation_beta") and rep_by["correlation_beta"].usable else None,
                                same_direction_open=same_dir, btc_regime=inp.btc_regime, stop_pct=plan.stop_pct, atr_pct=atr_pct,
                                liq_distance_pct=liq_dist, funding_pct=funding_pct if market == "futures" else None,
                                funding_z=(der.metrics or {}).get("funding_z") if der and der.usable and market == "futures" else None,
                                llm_schema_invalid=(inp.llm_advice or {}).get("schema_invalid"), min_order_conflict=(not plan.valid and plan.invalid_reason == NO_TRADE_MIN_ORDER_CONFLICT),
                                risk_limit_hit=pf.get("risk_limit_hit"), kill_switch_active=pf.get("kill_switch_active"), model_drift=pf.get("model_drift"),
                                listing_age_days=inp.listing_age_days, delist_flag=pf.get("delist_flag"), clock_or_api_issue=pf.get("clock_or_api_issue"))
            rep = RedTeamVetoAgent(ctx, rt)
            rep.agent_name = f"red_team_veto:{market}"
            rep.market_type = market
            reports.append(rep)
            rt_reports[market] = rep
            # LLM ADVISORY TEK BASINA HARD VETO URETEMEZ.
            # Eskiden `llm_advice["veto"]` dogrudan `rep.veto = True` yapiyor ve plani gecersiz
            # kiliyordu: merkezi `decision_gates.GATES` siniflandirmasi ATLANIYORDU. Model, kayitli
            # ve deterministik bir guvenlik kanitı olmadan islemi oldurebiliyordu. Artik LLM'in
            # `veto`/`veto_reasons` ciktisi YALNIZ kayitli `RED_TEAM_SOFT_PENALTY` yumusak cezasina
            # ve telemetriye donusur. LLM metnindeki serbest kod/reason ASLA kapi olarak kabul
            # edilmez (kayitli olmayan kod registry'ye sizamaz).
            # Deterministik `LLM_SCHEMA_INVALID` bunun DISINDADIR: onu sema dogrulayici uretir,
            # model metni degil; `review()` icinde SERT kalir (fail-closed).
            _llm_soft: list[str] = []
            if inp.llm_advice and inp.llm_advice.get("veto"):
                _reasons = [str(x)[:120] for x in (inp.llm_advice.get("veto_reasons") or [])[:3]]
                rep.warnings = list(rep.warnings) + ["LLM_ADVISORY_VETO(soft): " + ", ".join(_reasons)]
                rep.metrics = dict(rep.metrics or {}) | {
                    "llm_advisory": {"veto": True, "reasons": _reasons, "applied_as": "RED_TEAM_SOFT_PENALTY",
                                     "can_hard_veto": False}}
                _llm_soft = ["RED_TEAM_SOFT_PENALTY"]        # KAYITLI kod; ham LLM metni kod DEĞİLDİR
            # YUMUSAK red-team kodlari plani GECERSIZ YAPMAZ: `plan.soft_flags` uzerinden
            # `opportunity.assess()` icinde ust sinirli ceza olarak boyutu kucultur.
            _soft = list((rep.metrics or {}).get("soft_penalty_codes") or []) + _llm_soft
            if _soft:
                plan.soft_flags = list(plan.soft_flags) + [c for c in _soft if c not in plan.soft_flags]
            # SERT: yalnizca `hard_veto_codes` (kaynak kodda OLCULMUS, registry'de KAYITLI gercek
            # guvenlik kosulu) reddeder. `rep.veto` yalnizca `review()`in sert listesinden gelir.
            if rep.veto:
                plan.valid = False
                plan.invalid_reason = rep.veto_reason
        # 6) seçim: geçerli planlar arasında maliyet sonrası beklenen R'ye göre
        valid = {m: p for m, p in plans.items() if p.valid}
        d.vetoes = [r.veto_reason for r in rt_reports.values() if r.veto]
        if not valid:
            reasons = [p.invalid_reason for p in plans.values()]
            d.verdict = Verdict.NO_TRADE
            d.no_trade_reason = NO_TRADE_RED_TEAM_VETO if d.vetoes else (NO_TRADE_MIN_ORDER_CONFLICT if any(NO_TRADE_MIN_ORDER_CONFLICT in r for r in reasons) else NO_TRADE_NO_VALID_PLAN)
            d.latency_ms = round((time.time() - t0) * 1000, 1)
            self.last_decision = d
            return d
        best_m = max(valid, key=lambda m: valid[m].expected_r)
        best = valid[best_m]
        d.market_type = best_m
        d.verdict = Verdict.SPOT_LONG if best_m == "spot" else (Verdict.FUTURES_LONG if direction == "LONG" else Verdict.FUTURES_SHORT)
        d.entry_trigger, d.entry_zone, d.invalidation, d.stop, d.targets = best.entry_trigger, best.entry_zone, best.invalidation, best.stop, list(best.targets)
        d.time_horizon, d.notional, d.margin, d.leverage = best.time_horizon_bars, best.notional, best.margin, best.size.leverage
        d.position_size = round(best.notional / best.entry, 8) if best.entry else 0.0
        d.expected_cost = best.expected_cost_pct
        d.expected_return_gross = round(abs(best.targets[0] - best.entry) / best.entry * 100, 4) if best.targets else 0.0
        d.expected_return_net = round(d.expected_return_gross - d.expected_cost, 4)
        d.expected_r = best.expected_r
        d.p_win = round(0.5 + 0.25 * conf * (1 if abs(score) >= self.cfg.consensus_threshold else 0), 3)   # ön tahmin; öğrenme katmanı ezer
        d.expected_shortfall = round(best.stop_pct + best.expected_cost_pct, 4)
        net_now = float((pf.get("net_exposure") or {}).get(self.symbol, 0.0))
        d.net_exposure_after = {"before": net_now, "after": net_now + (best.notional if direction == "LONG" else -best.notional)}
        d.latency_ms = round((time.time() - t0) * 1000, 1)
        self.last_decision = d
        return d
