"""P3b-2b — örtü faktörü `F_VOL` (SAF; §2.2 satır O, teknik okuma `covers.factors.F_VOL.reading`).

Filtre: ATR% ≤ son 120 barın %80'liği. ATR% = ATR14 (Wilder, `indicators.atr`) / kapanış × 100, tabanın sinyal diliminde
(Box'ta 1d — B3 ile aynı gerekçe: kutu günlüktür), tabanın penceresinde (canlı çerçeve); "%80'lik" = karar barının ATR%'sinin
kendisi DAHİL son 120 ATR% içindeki ortalama yüzdelik sırası (`indicators.pct_rank`, BBSQ sıkışmasıyla aynı tanım) ≤ 0,80.
Pencerede ölçülemeyen değer (ısınma, < 120 değer) → giriş yok.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import indicators as SI
from .o_cover import Factor, hlc, make_cover, rows_of

LIVE_MODULES: tuple[str, ...] = ()


class VolatilityCap(Factor):
    NAME, KIND = "F_VOL", "filter"
    PARAM_KEYS = ("lookback_bars", "atr_pct_max_pctile", "reading")

    def setup(self, fp: Mapping[str, Any]) -> None:
        r = fp["reading"]
        if (r["atr_len"], r["timeframe"], r["box_timeframe"], r["rank"], r["unmeasurable"]) != (
                14, "BASE_SIGNAL_TF", "1d", "PCT_RANK_AVERAGE_INCLUDING_CURRENT", "NO_ENTRY"):
            raise ValueError(f"{self.cover.variant_id}: F_VOL okunuşu beklenmiyor: {dict(r)}")
        self.n_atr = int(r["atr_len"])
        self.look = int(fp["lookback_bars"])
        self.cap = float(fp["atr_pct_max_pctile"]) / 100.0
        if not (self.look >= 2 and 0.0 < self.cap <= 1.0):
            raise ValueError(f"{self.cover.variant_id}: F_VOL parametreleri geçersiz")
        self.tf = r["box_timeframe"] if self.cover.base_name == "BOX" else self.cover.signal_tf

    def rank(self, bars) -> float | None:
        rows = rows_of(bars, self.tf)
        if len(rows) < self.look:
            return None
        h, lo, c = hlc(rows)
        a = SI.atr(h, lo, c, self.n_atr)
        pct = [a[k] / c[k] * 100.0 if c[k] > 0 else float("nan") for k in range(len(c) - self.look, len(c))]
        r = SI.pct_rank(pct, pct[-1])
        return None if r != r else r

    def on_intent(self, it, bars, ctx):
        r = self.rank(bars)
        return it if (r is not None and r <= self.cap) else None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Any):
    return make_cover(variant_id, params, spec, base, VolatilityCap)


__all__ = ["LIVE_MODULES", "VolatilityCap", "make"]
