"""P3b-2b — örtü faktörü `X_TRAIL2` (SAF; §2.2 satır O, teknik okuma `covers.factors.X_TRAIL2.reading`).

Çıkış örtüsü: +1R'den sonra 2 ATR iz süren stop. Girişten bu yana (giriş barı dahil, karar barına kadar) kapanmış
sinyal-dilimi barlarının en iyi ucu (LONG en yüksek, SHORT en düşük) GERÇEK giriş ± `arm_r` × R'ye ulaşınca (R = |giriş −
ilk stop|) silahlanır; o andan her kararda aday = en iyi uç ∓ `trail_atr` × ATR14 (Wilder, sinyal dilimi, karar barında,
tabanın penceresinde; Box 5m). Aday → çekirdeğin MOVE_STOP'u (yalnız sıkılaştırır) ya da delinmişse CLOSE
(`o_cover.stop_overlay`). Tabanın çıkışları sürer.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import indicators as SI
from .contract import LONG
from .o_cover import Factor, best_since_entry, hlc, make_cover, rows_of, stop_overlay

LIVE_MODULES: tuple[str, ...] = ()


class AtrTrail(Factor):
    NAME, KIND = "X_TRAIL2", "exit"
    PARAM_KEYS = ("arm_r", "trail_atr", "reading")

    def setup(self, fp: Mapping[str, Any]) -> None:
        r = fp["reading"]
        if (r["atr_len"], r["timeframe"], r["extreme"], r["arm_on"], r["breach"]) != (
                14, "BASE_SIGNAL_TF", "BEST_SINCE_ENTRY_BAR", "BEST_EXTREME", "CLOSE_NEXT_OPEN"):
            raise ValueError(f"{self.cover.variant_id}: X_TRAIL2 okunuşu beklenmiyor: {dict(r)}")
        self.n_atr = int(r["atr_len"])
        self.arm, self.k = float(fp["arm_r"]), float(fp["trail_atr"])
        if not (self.arm > 0 and self.k > 0):
            raise ValueError(f"{self.cover.variant_id}: X_TRAIL2 parametreleri pozitif olmalı")

    def candidate(self, pos, rows) -> float | None:
        best = best_since_entry(pos, rows)
        risk = abs(float(pos.entry) - float(pos.initial_stop))
        if best is None or not risk > 0:
            return None
        armed = best >= pos.entry + self.arm * risk if pos.side == LONG else best <= pos.entry - self.arm * risk
        if not armed:
            return None
        h, lo, c = hlc(rows)
        a = SI.atr(h, lo, c, self.n_atr)[-1]
        if not (a == a and a > 0):
            return None
        return best - self.k * a if pos.side == LONG else best + self.k * a

    def on_manage(self, base, pos, bars, ctx):
        rows = rows_of(bars, self.cover.signal_tf)
        return stop_overlay(pos, rows, self.candidate(pos, rows), self.NAME)


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Any):
    return make_cover(variant_id, params, spec, base, AtrTrail)


__all__ = ["AtrTrail", "LIVE_MODULES", "make"]
