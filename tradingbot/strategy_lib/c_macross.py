"""P3b-2 — C ailesi (ortalama kesişimi): `MAX_{20_50,50_200}_{4h,1d}_{LO,LS}` (SAF; docs/ENGINE_BRAIN_V1.md §2.2).

Kural (mühürlü satır): EMA(hızlı) / EMA(yavaş) kesişimi (kapanış; `tradingbot.indicators.ema` tanımı, pencere = canlı
çerçeve); stop 3 × ATR14 (sinyal dilimi) sinyal kapanışından; çıkış ters kesişim (sonraki açılış); en çok 300 bar.
Okunuş: sinyal kesişimin bıraktığı DURUMDUR (hızlı > yavaş → long sinyali, hızlı < yavaş → short sinyali; LO'da
"sinyal tersine dönünce pozisyon yok"); pozisyonsuzken durum yönünde girilir, ters kesişimde (hızlı ≤ yavaş long'da,
≥ short'ta) çıkılır. Böylece LS ters kesişimde yön değiştirir (çekirdekte bir bar gecikmeyle); salt olay okunuşunda
çıkış barının kesişimi hiç kullanılamaz ve LS ≈ LO olurdu. Eşitlik yön vermez.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import indicators as ind
from .contract import LONG, SHORT
from .pure import Cols, PureVariant

PARAM_KEYS = ("fast_ema", "slow_ema")
LIVE_MODULES: tuple[str, ...] = ()


class MovingAverageCross(PureVariant):
    FAMILY = "C"
    PARAM_KEYS = PARAM_KEYS
    EXIT_RULE = "MA_CROSS_REVERSE"
    live_modules = LIVE_MODULES

    def setup(self, p: Mapping[str, Any]) -> None:
        f, s = p["fast_ema"], p["slow_ema"]
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 2 for x in (f, s)) or not f < s:
            raise ValueError(f"{self.variant_id}: 2 ≤ fast_ema < slow_ema tam sayı olmalı")
        if self.signal_tf not in ("4h", "1d"):
            raise ValueError(f"{self.variant_id}: dilim 4h/1d olmalı")
        self.fast, self.slow = int(f), int(s)

    def _state(self, cols: Cols) -> tuple[float, float] | None:
        f = ind.ema(cols.close, self.fast)[-1]
        s = ind.ema(cols.close, self.slow)[-1]
        if f != f or s != s:
            return None
        return f, s

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        m = self._state(cols)
        if m is None:
            return None
        f, s = m
        ev = {"ema_fast": f, "ema_slow": s}
        if f > s:
            return LONG, ev
        if f < s:
            return SHORT, ev
        return None

    def exit_reason(self, pos, cols: Cols, ctx) -> str | None:
        m = self._state(cols)
        if m is None:
            return None
        f, s = m
        if pos.side == LONG and f <= s:
            return "MA_CROSS_DOWN"
        if pos.side == SHORT and f >= s:
            return "MA_CROSS_UP"
        return None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> MovingAverageCross:
    return MovingAverageCross(variant_id, params, spec)


__all__ = ["LIVE_MODULES", "MovingAverageCross", "PARAM_KEYS", "make"]
