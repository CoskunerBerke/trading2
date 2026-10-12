"""P3b-2b — örtü faktörü `X_TPL` (SAF; §2.2 satır O, teknik okuma `covers.factors.X_TPL.reading`).

Çıkış örtüsü: 1R/2R/3R'de üçte bir. Niyetin hedefleri tabanınkinin YERİNE GERÇEK girişten 1R, 2R, 3R olur (R = |giriş −
ilk stop|; `Target(R_FROM_ENTRY, k, frac)`), ilk ikisi ilk miktarın 1/3'ünü, sonuncusu kalanı kapatır; ilk hedeften sonra
stop canlı defterin TP1 kuralıyla başa-başa çekilir (`FuturesLedgerV2.break_even_price`; çekirdek aynısını yapar).
Tabanın stopu, kural çıkışı ve zaman stopu sürer.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .contract import TARGET_R_FROM_ENTRY, Target
from .o_cover import Factor, make_cover

LIVE_MODULES: tuple[str, ...] = ()


class ThirdsLadder(Factor):
    NAME, KIND = "X_TPL", "exit"
    PARAM_KEYS = ("targets_r", "fraction_each", "reading")

    def setup(self, fp: Mapping[str, Any]) -> None:
        r = fp["reading"]
        rs = [float(x) for x in fp["targets_r"]]
        if fp["fraction_each"] != "1/3" or rs != [1.0, 2.0, 3.0]:
            raise ValueError(f"{self.cover.variant_id}: X_TPL 1R/2R/3R × 1/3 olmalı")
        if (r["target_from"], r["replaces_base_targets"], r["last_target"], r["after_first_target"]) != (
                "REAL_ENTRY", True, "CLOSES_REST", "LEDGER_TP1_BREAKEVEN"):
            raise ValueError(f"{self.cover.variant_id}: X_TPL okunuşu beklenmiyor: {dict(r)}")
        frac = 1.0 / 3.0
        self.targets = tuple(Target(TARGET_R_FROM_ENTRY, k, frac if i < len(rs) - 1 else None) for i, k in enumerate(rs))

    def on_intent(self, it, bars, ctx):
        return replace(it, targets=self.targets)

    def on_manage(self, base, pos, bars, ctx):
        return base


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Any):
    return make_cover(variant_id, params, spec, base, ThirdsLadder)


__all__ = ["LIVE_MODULES", "ThirdsLadder", "make"]
