"""P3b-2b — örtü faktörü `X_CHAN3` (SAF; §2.2 satır O, teknik okuma `covers.factors.X_CHAN3.reading`).

Çıkış örtüsü: chandelier 3 × ATR22 (LeBeau): LONG aday = girişten bu yana (giriş barı dahil, karar barına kadar) en
yüksek − 3 × ATR22, SHORT en düşük + 3 × ATR22; ATR22 = Wilder ATR(22), sinyal diliminde, karar barında, tabanın
penceresinde (Box 5m); ilk karardan itibaren. Aday → çekirdeğin MOVE_STOP'u (yalnız sıkılaştırır) ya da delinmişse CLOSE
(`o_cover.stop_overlay`). Tabanın çıkışları sürer.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import indicators as SI
from .contract import LONG
from .o_cover import Factor, best_since_entry, hlc, make_cover, rows_of, stop_overlay

LIVE_MODULES: tuple[str, ...] = ()


class Chandelier(Factor):
    NAME, KIND = "X_CHAN3", "exit"
    PARAM_KEYS = ("atr_period", "atr_mult", "reading")

    def setup(self, fp: Mapping[str, Any]) -> None:
        r = fp["reading"]
        if (r["timeframe"], r["extreme"], r["start"], r["breach"]) != (
                "BASE_SIGNAL_TF", "BEST_SINCE_ENTRY_BAR", "FIRST_DECISION", "CLOSE_NEXT_OPEN"):
            raise ValueError(f"{self.cover.variant_id}: X_CHAN3 okunuşu beklenmiyor: {dict(r)}")
        self.n, self.k = int(fp["atr_period"]), float(fp["atr_mult"])
        if not (self.n >= 1 and self.k > 0):
            raise ValueError(f"{self.cover.variant_id}: X_CHAN3 parametreleri geçersiz")

    def candidate(self, pos, rows) -> float | None:
        best = best_since_entry(pos, rows)
        if best is None:
            return None
        h, lo, c = hlc(rows)
        a = SI.atr(h, lo, c, self.n)[-1]
        if not (a == a and a > 0):
            return None
        return best - self.k * a if pos.side == LONG else best + self.k * a

    def on_manage(self, base, pos, bars, ctx):
        rows = rows_of(bars, self.cover.signal_tf)
        return stop_overlay(pos, rows, self.candidate(pos, rows), self.NAME)


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Any):
    return make_cover(variant_id, params, spec, base, Chandelier)


__all__ = ["Chandelier", "LIVE_MODULES", "make"]
