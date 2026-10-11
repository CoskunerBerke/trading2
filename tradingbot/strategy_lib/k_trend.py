"""P3b — `K_T2`, `K_M2`: canlı T2 (EMA200 trend + BTC rejimi) ve M2 (TSMOM28) defterlerinin kopyaları (SAF).

Yeniden yazılmaz: karar canlı `StrategyBook.step`'in çağırdığı fonksiyonun kendisidir — `paper_rules.decide_with_structures`
(→ `decide_from_rows` → `ema200_trend.decide`, ardından ortak yapı katmanı `structures.bots.trend_decide`; mühürlü
parametre `structures`: ENFORCE → giriş zamanlaması + yapı çıkışı uygulanır, SHADOW → karar değişmez, OFF → `ctx=None`),
kullanılmış yapı kümesi `structures.bots.used_patterns_of` ile kopyanın KENDİ girişlerinden (`ctx["history"]`).
Yapı kovalama sınırının fiyatı karar anında bilinen son fiyattır (sinyal barının kapanışı; canlıda turun mark'ı).
Zaman stopu yoktur (canlı kural; WF `max_hold`'u kayıtta `wf_only`, sahip kararı B2).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from .. import paper_rules
from ..structures import bots as SB
from . import livebars
from .contract import (EXIT_LIVE_RULE, LONG, NONE, Action, BookEntry, EntryRef, Intent, PosView, Variant, check_params,
                       close)

PARAM_KEYS = ("atr_mult", "breakeven_at_mfe_r", "max_entry_drift_pct", "structures")
STRUCTURE_MODES = ("OFF", "SHADOW", "ENFORCE")
RULES = {"K_T2": "t2_trend_regime", "K_M2": "m2_tsmom28"}


def used_structures(history: Sequence[BookEntry] | None) -> Any:
    """Kopyanın girişlerinden kullanılmış yapı kümesi — canlı `used_patterns_of(ledger)` fonksiyonunun kendisiyle."""
    hist = [SimpleNamespace(symbol=e.symbol, features={"structure": dict(e.evidence.get("structure") or {})}
                            if e.evidence.get("structure") else {}) for e in (history or ())]
    return SB.used_patterns_of(SimpleNamespace(positions={}, history=hist))


class TrendCopy(Variant):
    family = "K"
    signal_tf = "1d"
    timeframes = ("1d",)
    needs_btc = True
    live_modules = ("tradingbot.paper_rules", "tradingbot.ema200_trend", "tradingbot.regime_gate",
                    "tradingbot.structures.bots", "tradingbot.indicators")

    def __init__(self, variant_id: str, params: Mapping[str, Any]):
        p = check_params(variant_id, params, PARAM_KEYS)
        if float(p["breakeven_at_mfe_r"]) != 0.0:
            raise ValueError(f"{variant_id}: breakeven_at_mfe_r ≠ 0 çekirdekte modellenmez (P3b-1)")
        if str(p["structures"]).upper() not in STRUCTURE_MODES:
            raise ValueError(f"{variant_id}: structures {STRUCTURE_MODES} olmalı")
        self.variant_id = variant_id
        self.rule = RULES[variant_id]
        self.params = dict(p)
        self.mode = str(p["structures"]).upper()
        self.drift = float(p["max_entry_drift_pct"]) or None
        self.rule_params = paper_rules.build_params(self.rule, atr_mult=float(p["atr_mult"]))
        self.lookback = {"1d": livebars.LIVE_CLOSED_BARS["1d"]}
        self.btc_lookback = livebars.LIVE_CLOSED_BARS["1d"]

    # ------------------------------------------------------------------ yardımcılar
    def _decide(self, bars: Mapping[str, Sequence[Mapping[str, Any]]], ctx: Mapping[str, Any], position: Any) -> Any:
        now = int(ctx["now_ms"])
        frames = {"1d": livebars.frame(bars.get("1d"), "1d", daily_indicators=True)}
        btc = livebars.btc_daily_rows(ctx.get("btc_bars"), now)
        sctx = None
        if self.mode != "OFF":
            last = (bars.get("1d") or [None])[-1]
            sctx = SB.StructureContext(mode=self.mode, symbol=str(ctx.get("symbol") or "?"), as_of_ms=now,
                                       price=float(last["close"]) if last else None,
                                       used_patterns=used_structures(ctx.get("history")),
                                       provenance={"market": "USDM_PERP", "source": "strategy_lib"})
        act, _dec, _an = paper_rules.decide_with_structures(self.rule, frames=frames, btc_rows=btc, now_ms=now,
                                                            position=position, params=self.rule_params, ctx=sctx)
        return act

    # ------------------------------------------------------------------ sözleşme
    def signals(self, bars, ctx) -> list[Intent]:
        act = self._decide(bars, ctx, None)
        if not act or str(act.get("action") or "").upper() != "OPEN":
            return []
        ev = {"rule": self.rule, "reason": act.get("reason"), "regime": act.get("regime"), "atr14": act.get("atr14"),
              "ema200": act.get("ema200")}
        if act.get("structure"):
            ev["structure"] = dict(act["structure"])
        return [Intent(signal_ts=int(act["signal_ts"]), side=str(act.get("direction") or LONG).upper(),
                       entry_ref=EntryRef(price=float(act["signal_close"]), max_drift_pct=self.drift),
                       stop=float(act["stop"]), targets=(), exit_rule=EXIT_LIVE_RULE, max_hold_bars=None,
                       one_entry_per_signal=False, risk_atr_bounds=None, evidence=ev)]

    def manage(self, pos: PosView, bars, ctx) -> Action:
        st = pos.intent.evidence.get("structure")
        position = {"opened_at": int(pos.opened_ms), "side": pos.side,
                    "features": {"structure": dict(st) if st else None,
                                 "data_source": {"bars": {"1d": int(pos.signal_ts)}}}}
        act = self._decide(bars, ctx, position)
        if act and str(act.get("action") or "").upper() == "CLOSE":
            return close(str(act.get("reason") or "RULE_EXIT"))
        return NONE


def make(variant_id: str, params: Mapping[str, Any]) -> TrendCopy:
    return TrendCopy(variant_id, params)


__all__ = ["PARAM_KEYS", "RULES", "TrendCopy", "make", "used_structures"]
