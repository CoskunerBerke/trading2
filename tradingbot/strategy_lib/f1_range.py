"""P3b-2 — F1 ailesi (aralık dönüşü): `RSI2_{1h,4h}`, `BBRE_{1h,4h}` (SAF; docs/ENGINE_BRAIN_V1.md §2.2).

Mühürlü satırlar: LS; yalnız ER20 < 0,3 (ER20 = 20 barlık Kaufman verim oranı, SİNYAL diliminde — sahip kararı B3);
stop 1,5 × ATR14 (sinyal dilimi) sinyal kapanışından; çıkış orta bant = sinyal dilimindeki BB(20,2) orta çizgisi (SMA20;
RSI2'de sahip kararı B5, BBRE'de kendi bandı) — long'da kapanış ≥ orta, short'ta ≤ orta → sonraki açılış; en çok 24 bar.

* **RSI2** — Wilder RSI(2) (`tradingbot.indicators.rsi`) < 10 → long, > 90 → short. Koşul bir DURUMDUR (yazıldığı gibi:
  "RSI2 < 10 long"): pozisyonsuzken koşul sağlanıyorsa girilir.
* **BBRE** — BB(20,2) dışına çıkıp içine kapanış: önceki kapanış önceki barın alt bandının altında, bu kapanış bu barın
  bandının içinde (alt ≤ kapanış ≤ üst) → long; üst bant için simetriği → short. Okunuş: "dışına çıkma" kapanışladır.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import indicators as ind
from .contract import LONG, SHORT
from .pure import Cols, PureVariant

RSI2_PARAM_KEYS = ("rsi_period", "long_below", "short_above", "er20_max")
BBRE_PARAM_KEYS = ("bb_period", "bb_std", "er20_max")
ER_LEN = 20
#: orta bant çıkışının bandı (B5: BB(20,2) orta çizgisi; BBRE'de kendi parametreleri)
MID_BB = (20, 2.0)
LIVE_MODULES: tuple[str, ...] = ()


class _RangeBase(PureVariant):
    FAMILY = "F1"
    EXIT_RULE = "MIDDLE_BAND"
    SIDES_ALLOWED = ("LS",)
    live_modules = LIVE_MODULES
    mid_bb: tuple[int, float] = MID_BB

    def _er_ok(self, cols: Cols) -> tuple[bool, float | None]:
        e = ind.er(cols.close, ER_LEN, len(cols) - 1)
        return (e is not None and e < self.er_max), e

    def exit_reason(self, pos, cols: Cols, ctx) -> str | None:
        _lo, mid, _up = ind.bollinger_at(cols.close, len(cols) - 1, self.mid_bb[0], self.mid_bb[1])
        if mid != mid:
            return None
        c = cols.close[-1]
        if pos.side == LONG and c >= mid:
            return "MIDDLE_BAND"
        if pos.side == SHORT and c <= mid:
            return "MIDDLE_BAND"
        return None

    def _check_er(self, p: Mapping[str, Any]) -> None:
        x = p["er20_max"]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 < x <= 1:
            raise ValueError(f"{self.variant_id}: er20_max (0, 1] olmalı")
        self.er_max = float(x)


class RSI2Reversion(_RangeBase):
    PARAM_KEYS = RSI2_PARAM_KEYS

    def setup(self, p: Mapping[str, Any]) -> None:
        self._check_er(p)
        n, lo, hi = p["rsi_period"], p["long_below"], p["short_above"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1 or not 0 < lo < hi < 100:
            raise ValueError(f"{self.variant_id}: RSI parametreleri geçersiz")
        ex = self.spec_exit_rule
        if not (isinstance(ex, Mapping) and ex.get("bb_period") == MID_BB[0] and ex.get("bb_std") == MID_BB[1]
                and ex.get("decision") == "B5"):
            raise ValueError(f"{self.variant_id}: orta bant çıkışı BB(20,2) orta çizgisi olmalı (B5): {ex!r}")
        self.n, self.lo, self.hi = int(n), float(lo), float(hi)

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        ok, e = self._er_ok(cols)
        if not ok:
            return None
        r = ind.rsi(cols.close, self.n)[-1]
        if r != r:
            return None
        ev = {"rsi": r, "er20": e}
        if r < self.lo:
            return LONG, ev
        if r > self.hi:
            return SHORT, ev
        return None


class BollingerReentry(_RangeBase):
    PARAM_KEYS = BBRE_PARAM_KEYS

    def setup(self, p: Mapping[str, Any]) -> None:
        self._check_er(p)
        n, k = p["bb_period"], p["bb_std"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 2 or isinstance(k, bool) or not k > 0:
            raise ValueError(f"{self.variant_id}: BB parametreleri geçersiz")
        self.n, self.k = int(n), float(k)
        self.mid_bb = (self.n, self.k)

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        i = len(cols) - 1
        c = cols.close
        lo, mid, up = ind.bollinger_at(c, i, self.n, self.k)
        lo_p, _m, up_p = ind.bollinger_at(c, i - 1, self.n, self.k)
        if lo != lo or lo_p != lo_p:
            return None
        inside = lo <= c[-1] <= up
        if inside and c[-2] < lo_p:
            side = LONG
        elif inside and c[-2] > up_p:
            side = SHORT
        else:
            return None
        ok, e = self._er_ok(cols)
        if not ok:
            return None
        return side, {"bb_mid": mid, "bb_upper": up, "bb_lower": lo, "er20": e}


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> PureVariant:
    if variant_id.startswith("RSI2_"):
        return RSI2Reversion(variant_id, params, spec)
    if variant_id.startswith("BBRE_"):
        return BollingerReentry(variant_id, params, spec)
    raise KeyError(variant_id)


__all__ = ["BBRE_PARAM_KEYS", "BollingerReentry", "ER_LEN", "LIVE_MODULES", "MID_BB", "RSI2Reversion", "RSI2_PARAM_KEYS",
           "make"]
