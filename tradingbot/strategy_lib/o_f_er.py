"""P3b-2b — örtü faktörü `F_ER` (SAF; §2.2 satır O, sahip kararı B3, teknik okuma `covers.factors.F_ER.reading`).

Filtre: T2/M2/D4/C4 tabanında ER20 ≥ 0,3 (trend), Box tabanında ER20 < 0,3 (yatay). ER20 = Kaufman verim oranı
(`indicators.er`), tabanın sinyal diliminin (T2/M2 1d, D4/C4 4h; Box'ta 1d — B3: kutu günlüktür) karar anında kapanmış son
21 barının kapanışlarından; ölçülemezse (< 21 bar, hareketsiz pencere) giriş yok.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import indicators as SI
from .o_cover import Factor, make_cover, rows_of

LIVE_MODULES: tuple[str, ...] = ()


class EfficiencyRatio(Factor):
    NAME, KIND = "F_ER", "filter"
    PARAM_KEYS = ("er20_min_trend", "er20_max_box", "reading")

    def setup(self, fp: Mapping[str, Any]) -> None:
        r = fp["reading"]
        if (r["bars"], r["timeframe"], r["box_timeframe"], r["unmeasurable"]) != (20, "BASE_SIGNAL_TF", "1d", "NO_ENTRY"):
            raise ValueError(f"{self.cover.variant_id}: F_ER okunuşu beklenmiyor: {dict(r)}")
        self.n = int(r["bars"])
        self.box = self.cover.base_name == "BOX"
        self.tf = r["box_timeframe"] if self.box else self.cover.signal_tf
        self.lo, self.hi = float(fp["er20_min_trend"]), float(fp["er20_max_box"])

    def value(self, bars) -> float | None:
        rows = rows_of(bars, self.tf)
        if len(rows) < self.n + 1:
            return None
        c = [float(r["close"]) for r in rows[-(self.n + 1):]]
        return SI.er(c, self.n, self.n)

    def on_intent(self, it, bars, ctx):
        e = self.value(bars)
        if e is None:
            return None
        return it if ((e < self.hi) if self.box else (e >= self.lo)) else None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Any):
    return make_cover(variant_id, params, spec, base, EfficiencyRatio)


__all__ = ["EfficiencyRatio", "LIVE_MODULES", "make"]
