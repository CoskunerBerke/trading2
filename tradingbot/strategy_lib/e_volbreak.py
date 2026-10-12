"""P3b-2 — E ailesi (oynaklık kırılımı): `KEL_20_2_{1h,4h}`, `BBSQ_20_{1h,4h}` (SAF; docs/ENGINE_BRAIN_V1.md §2.2).

Mühürlü satırlar: LS; stop 1,5 × ATR14 (sinyal dilimi) sinyal kapanışından; hedef 2R (GERÇEK girişten); en çok 48 bar;
başka çıkış yok ("hedef ya da max_hold").

* **KEL** — Keltner 20, 2 ATR kapanış kırılımı; orta çizgi EMA20 (kapanış) (sahip kararı B7, 2026-10-11), bant
  ± 2 × ATR14 (LIB varsayılanı "ATR = ATR14"; `indicators_ext.keltner(…, 20, 2.0, atr_len=14)` tanımı). Kırılım =
  kapanışın üst bandı İLK kez geçmesi (önceki kapanış önceki barın üst bandını geçmemişti) → long; alt bant → short.
* **BBSQ** — BB(20,2) genişliği ((üst − alt) / orta) son 120 barın ≤ %20'liğinde iken kırılım; kırılım = kapanışın
  BB(20,2) bandının dışında olması (B7: üst bandın üstü → long, alt bandın altı → short), İLK kez (önceki kapanış
  önceki barın bandının içindeydi). Okunuş: "iken" = kırılım barında; yüzdelik = o barın genişliğinin, kendisi DAHİL son
  120 barın genişlikleri içindeki ortalama sırası / 120 (`indicators_ext.bb_width_pct_rank`'in tanımı; pencere tam
  dolmadan giriş yok) ≤ 0,20.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import indicators as ind
from .contract import LONG, SHORT
from .pure import ATR_LEN, Cols, PureVariant, fresh_above, fresh_below

KEL_PARAM_KEYS = ("keltner_period", "band_atr_mult", "center_line")
BBSQ_PARAM_KEYS = ("bb_period", "bb_std", "squeeze_lookback_bars", "squeeze_pct_max", "breakout_ref")
LIVE_MODULES: tuple[str, ...] = ()


def _pos_int(x: Any) -> bool:
    return not isinstance(x, bool) and isinstance(x, int) and x >= 2


def _pos_num(x: Any) -> bool:
    return not isinstance(x, bool) and isinstance(x, (int, float)) and x > 0


class KeltnerBreakout(PureVariant):
    FAMILY = "E"
    PARAM_KEYS = KEL_PARAM_KEYS
    EXIT_RULE = "TARGET_OR_MAX_HOLD"
    SIDES_ALLOWED = ("LS",)
    live_modules = LIVE_MODULES

    def setup(self, p: Mapping[str, Any]) -> None:
        n, mult, cl = p["keltner_period"], p["band_atr_mult"], p["center_line"]
        if not (_pos_int(n) and _pos_num(mult)):
            raise ValueError(f"{self.variant_id}: keltner_period ≥ 2, band_atr_mult > 0 olmalı")
        if not (isinstance(cl, Mapping) and cl.get("ma") == "EMA" and cl.get("price") == "close" and cl.get("period") == n):
            raise ValueError(f"{self.variant_id}: orta çizgi EMA{n} (kapanış) olmalı (B7): {cl!r}")
        if not self.target_r:
            raise ValueError(f"{self.variant_id}: hedef (target_r) gerekli")
        self.n, self.mult = int(n), float(mult)

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        i = len(cols) - 1
        e = ind.ema(cols.close, self.n)
        a = ind.atr(cols.high, cols.low, cols.close, ATR_LEN)
        lo, mid, up = ind.keltner_at(cols.high, cols.low, cols.close, i, length=self.n, mult=self.mult, atr_len=ATR_LEN,
                                     ema_series=e, atr_series=a)
        lo_p, _m, up_p = ind.keltner_at(cols.high, cols.low, cols.close, i - 1, length=self.n, mult=self.mult,
                                        atr_len=ATR_LEN, ema_series=e, atr_series=a)
        ev = {"keltner_mid": mid, "keltner_upper": up, "keltner_lower": lo, "atr14": a[i]}
        if fresh_above(cols.close, up, up_p):
            return LONG, ev
        if fresh_below(cols.close, lo, lo_p):
            return SHORT, ev
        return None


class BollingerSqueezeBreakout(PureVariant):
    FAMILY = "E"
    PARAM_KEYS = BBSQ_PARAM_KEYS
    EXIT_RULE = "TARGET_OR_MAX_HOLD"
    SIDES_ALLOWED = ("LS",)
    live_modules = LIVE_MODULES

    def setup(self, p: Mapping[str, Any]) -> None:
        n, k, lb, q, br = p["bb_period"], p["bb_std"], p["squeeze_lookback_bars"], p["squeeze_pct_max"], p["breakout_ref"]
        if not (_pos_int(n) and _pos_num(k) and _pos_int(lb) and _pos_num(q) and q < 100):
            raise ValueError(f"{self.variant_id}: BBSQ parametreleri geçersiz")
        if not (isinstance(br, Mapping) and br.get("rule") == "CLOSE_OUTSIDE_BB" and br.get("bb_period") == n
                and br.get("bb_std") == k):
            raise ValueError(f"{self.variant_id}: kırılım referansı kendi BB({n},{k}) bandı olmalı (B7): {br!r}")
        if not self.target_r:
            raise ValueError(f"{self.variant_id}: hedef (target_r) gerekli")
        self.n, self.k, self.lb, self.q = int(n), float(k), int(lb), float(q) / 100.0

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        i = len(cols) - 1
        c = cols.close
        if i - self.lb + 1 < self.n - 1:
            return None                                  # 120 genişliğin hepsi ölçülemiyor
        lo, mid, up = ind.bollinger_at(c, i, self.n, self.k)
        lo_p, _m, up_p = ind.bollinger_at(c, i - 1, self.n, self.k)
        if fresh_above(c, up, up_p):
            side = LONG
        elif fresh_below(c, lo, lo_p):
            side = SHORT
        else:
            return None
        widths = [ind.bb_width_at(c, j, self.n, self.k) for j in range(i - self.lb + 1, i + 1)]
        rank = ind.pct_rank(widths, widths[-1])
        if not (rank == rank and rank <= self.q):
            return None
        return side, {"bb_mid": mid, "bb_upper": up, "bb_lower": lo, "bb_width": widths[-1], "bb_width_pct_rank": rank}


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> PureVariant:
    if variant_id.startswith("KEL_"):
        return KeltnerBreakout(variant_id, params, spec)
    if variant_id.startswith("BBSQ_"):
        return BollingerSqueezeBreakout(variant_id, params, spec)
    raise KeyError(variant_id)


__all__ = ["BBSQ_PARAM_KEYS", "BollingerSqueezeBreakout", "KEL_PARAM_KEYS", "KeltnerBreakout", "LIVE_MODULES", "make"]
