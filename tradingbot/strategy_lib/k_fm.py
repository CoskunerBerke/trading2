"""P3b — `K_FM`: canlı Formasyon defterinin protokolü `momentum_4h_v3` kopyası (4h üç beyaz asker + RSI14 > 70 → LONG).

Yeniden yazılmaz: plan `pattern_trader.strategy_v3.build_plans_v3`, analiz ortak katalogun `structures.analyze`'ı (4h,
Formasyon veri servisinin penceresi `livebars.FM_BARS_4H`), kullanılmış yapılar `structures.bots.used_patterns_of` ile
kopyanın KENDİ girişlerinden (canlı defterle aynı: pozisyon kaydının `features.structure` alanı, eylem ENTER). Plandan
niyete: stop = plan stopu; hedef GİRİŞTEN 2R; kovalama sınırı |giriş − tetik| ≤ 1 × ATR ve risk aralığı 0,1–5 ATR
(`PatternBook._try_open`'ın kapıları; giriş anında, dolumdan önceki fiyatla); zaman stopu 24 × 4h (canlıda 384 × 15m);
aynı yapıyla tek giriş. Defterin canlı-yalnız kapıları (likidite/spread/derinlik, evren, bekleme) kopyada yoktur
(veri yok); parite (b) bunları "açıklanan fark" olarak sayar. Protokol sabitleri `strategy_v3.V3_SIGNAL`'dadır
(`module_src_sha` ile sabitlenir).
"""
from __future__ import annotations

from typing import Any, Mapping

from ..pattern_trader import strategy_v3 as S3
from ..structures.analysis import analyze
from . import livebars
from .contract import (NONE, TARGET_R_FROM_ENTRY, TF_MS, Action, EntryRef, Intent, PosView, RiskAtrBounds, Target,
                       Variant, check_params)
from .k_trend import used_structures

PARAM_KEYS = ("protocol", "structures")


class FormationCopy(Variant):
    family = "K"
    signal_tf = S3.V3_TF
    timeframes = (S3.V3_TF,)
    needs_btc = False
    live_modules = ("tradingbot.pattern_trader.strategy_v3", "tradingbot.structures.analysis",
                    "tradingbot.structures.bots")

    def __init__(self, variant_id: str, params: Mapping[str, Any]):
        p = check_params(variant_id, params, PARAM_KEYS)
        if str(p["protocol"]) != S3.PROTOCOL_NAME:
            raise ValueError(f"{variant_id}: yalnız {S3.PROTOCOL_NAME} protokolü kopyalanır")
        self.variant_id = variant_id
        self.params = dict(p)
        self.lookback = {self.signal_tf: livebars.FM_BARS_4H}

    def signals(self, bars, ctx) -> list[Intent]:
        now = int(ctx["now_ms"])
        sym = str(ctx.get("symbol") or "?")
        rows = livebars.fm_rows(bars.get(self.signal_tf))
        if not rows:
            return []
        an = analyze(market="USDM_PERP", symbol=sym, timeframe=S3.V3_TF, bars=rows, as_of_ms=now,
                     data_provenance={"market": "USDM_PERP", "source": "strategy_lib"})
        plans, _skipped = S3.build_plans_v3(sym, as_of_ms=now, analyses={S3.V3_TF: an}, bars_by_tf={S3.V3_TF: rows},
                                            used_patterns=used_structures(ctx.get("history")))
        out: list[Intent] = []
        sig = S3.V3_SIGNAL
        for pl in plans:
            atr = float(pl["atr"])
            trig = float(pl["trigger"]["level"])
            st = dict(pl["structure"], action="ENTER", reason_code="PLAN_" + str(pl.get("family")), side=str(pl["side"]),
                      trigger=dict(pl.get("trigger") or {}), bot="pattern_trader")
            lo, hi = (float(x) for x in pl["risk_atr_bounds"])
            out.append(Intent(signal_ts=int(pl["triggered_at_ms"]) - TF_MS[S3.V3_TF],
                              side=str(pl["side"]).upper(),
                              entry_ref=EntryRef(price=float(pl.get("trigger_close") or rows[-1]["close"]), anchor=trig,
                                                 max_anchor_dist=float(pl["chase_atr"]) * atr),
                              stop=float(pl["stop"]),
                              targets=(Target(TARGET_R_FROM_ENTRY, float(pl["target_from_entry_rr"])),),
                              exit_rule="TIME_STOP_%d_BARS_%s" % (int(sig["hold_bars"]), S3.V3_TF),
                              max_hold_bars=int(sig["hold_bars"]), one_entry_per_signal=True,
                              risk_atr_bounds=RiskAtrBounds(lo, hi, atr),
                              evidence={"rule": S3.PROTOCOL_NAME, "plan_id": pl["plan_id"], "pattern_id": pl["pattern_id"],
                                        "structure": st, "rsi14_at_confirm": (pl.get("evidence") or {}).get("rsi14_at_confirm")}))
        return out[:1]

    def manage(self, pos: PosView, bars, ctx) -> Action:
        return NONE                                     # çıkışlar stop / 2R hedef / zaman stopu (çekirdek)


def make(variant_id: str, params: Mapping[str, Any]) -> FormationCopy:
    return FormationCopy(variant_id, params)


__all__ = ["FormationCopy", "PARAM_KEYS", "make"]
