"""P3b-2 — B ailesi (TSMOM): `TS_{7,14,28,56,84}_{LO,LS}` (SAF; docs/ENGINE_BRAIN_V1.md §2.2).

Kural (mühürlü satır): L günlük getirinin işareti, 1d; + → long, − → short (LS) / pozisyon yok (LO); stop 3 × ATR14
(1d) sinyal kapanışından; çıkış işaret dönünce (sonraki açılış); en çok 300 bar. Getiri L kapanmış 1d bar öncesinin
kapanışına göre (`close > close[−1−L]`; bölme yok) — canlı M2'nin (`ema200_trend`, TSMOM28) okuması. İşaret bir
DURUMDUR: pozisyonsuzken işaret + ise long (stop sonrası da), işaret dönünce çık; LS'de ters işaret o an geçerliyse ters
yöne geçilir (çekirdekte bir bar gecikmeyle). Sıfır getiri yön vermez (giriş yok; long'da çıkış, short'ta çıkış).
"""
from __future__ import annotations

from typing import Any, Mapping

from .contract import LONG, SHORT
from .pure import Cols, PureVariant

PARAM_KEYS = ("lookback_days",)
#: kimliğe giren canlı modüller: yok (göstergeler `strategy_lib.indicators`'ta, paket içi bağımlılık olarak sayılır)
LIVE_MODULES: tuple[str, ...] = ()


class TimeSeriesMomentum(PureVariant):
    FAMILY = "B"
    PARAM_KEYS = PARAM_KEYS
    EXIT_RULE = "TS_SIGN_FLIP"
    live_modules = LIVE_MODULES

    def setup(self, p: Mapping[str, Any]) -> None:
        lb = p["lookback_days"]
        if isinstance(lb, bool) or not isinstance(lb, int) or lb < 1:
            raise ValueError(f"{self.variant_id}: lookback_days ≥ 1 tam sayı olmalı")
        if self.signal_tf != "1d":
            raise ValueError(f"{self.variant_id}: TSMOM günlük getiridir (1d), gelen {self.signal_tf}")
        self.lb = int(lb)

    def _ret(self, cols: Cols) -> tuple[float, float] | None:
        if len(cols) <= self.lb:
            return None
        return cols.close[-1], cols.close[-1 - self.lb]

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        m = self._ret(cols)
        if m is None:
            return None
        c, ref = m
        ev = {"ref_close": ref, "ref_ts": cols.ts[-1 - self.lb], "lookback_days": self.lb}
        if c > ref:
            return LONG, ev
        if c < ref:
            return SHORT, ev
        return None

    def exit_reason(self, pos, cols: Cols, ctx) -> str | None:
        m = self._ret(cols)
        if m is None:
            return None
        c, ref = m
        if pos.side == LONG and not c > ref:
            return "TS_SIGN_FLIP_DOWN"
        if pos.side == SHORT and not c < ref:
            return "TS_SIGN_FLIP_UP"
        return None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> TimeSeriesMomentum:
    return TimeSeriesMomentum(variant_id, params, spec)


__all__ = ["LIVE_MODULES", "PARAM_KEYS", "TimeSeriesMomentum", "make"]
