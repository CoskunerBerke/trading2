"""P3b-2b — örtü faktörü `X_TIME` (SAF; §2.2 satır O, sahip kararı B2, teknik okuma `covers.factors.X_TIME.reading`).

Çıkış örtüsü: zaman stopu = tabanın `max_hold`'unun yarısı (⌊max_hold × 0,5⌋ sinyal barı; mühürlü `copy_books.<taban>.
max_hold`: T2/M2 300 × 1d `wf_only` → 150 (B2 `x_time_bars`), D4 300 × 4h → 150, C4 24 × 4h → 12); Box: 48 × 5m. Niyetin
`max_hold_bars`'ı bu değer olur (taban daha kısa bir zaman stopu taşıyorsa o kalır); sayım çekirdeğinkidir (giriş barı 1,
sonraki açılışta piyasa çıkışı). Tabanın diğer çıkışları (stop, hedef, kural, gün sonu) sürer.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .o_cover import Factor, make_cover

LIVE_MODULES: tuple[str, ...] = ()


class HalfHold(Factor):
    NAME, KIND = "X_TIME", "exit"
    PARAM_KEYS = ("max_hold_fraction", "box_bars_5m", "reading")

    def setup(self, fp: Mapping[str, Any]) -> None:
        r = fp["reading"]
        mh = self.cover.spec["base_max_hold"]
        frac = float(fp["max_hold_fraction"])
        if self.cover.base_name == "BOX":
            bars = int(fp["box_bars_5m"])
        else:
            bars = int(int(mh["bars"]) * frac)
            if mh.get("wf_only") and int(mh["x_time_bars"]) != bars:
                raise ValueError(f"{self.cover.variant_id}: X_TIME {bars} ≠ mühürlü x_time_bars {mh['x_time_bars']}")
        if dict(r["bars"]).get(self.cover.base_name) != bars or bars < 1:
            raise ValueError(f"{self.cover.variant_id}: X_TIME okunuşu {dict(r['bars'])} ≠ {bars}")
        self.bars = bars

    def on_intent(self, it, bars, ctx):
        mh = self.bars if it.max_hold_bars is None else min(int(it.max_hold_bars), self.bars)
        return replace(it, max_hold_bars=mh)

    def on_manage(self, base, pos, bars, ctx):
        return base


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Any):
    return make_cover(variant_id, params, spec, base, HalfHold)


__all__ = ["HalfHold", "LIVE_MODULES", "make"]
