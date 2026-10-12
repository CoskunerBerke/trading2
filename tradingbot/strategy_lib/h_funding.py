"""P3b-2 — H ailesi (fonlama): `FUND_FADE_{24h,72h}`, `FUND_FADE_LOW_{24h,72h}`, `FUND_CARRY` (SAF; docs/ENGINE_BRAIN_V1.md
§2.2). Fonlama `ctx["funding"]`'ten gelir (deponun uzlaşma satırları; çekirdek `funding_lookback_ms` geriye verir).

`f8` ve yüzdeliği fut_v1 laboratuvarının TANIMIDIR ve onun saf fonksiyonu çağrılır (`futures_features.align_funding`;
LIB okunuşu "H 'f8' = fut_v1 tanımı"): karar anı T (sinyal barının kapanışı) için görünür (uzlaşma ≤ T − 60 sn) son
uzlaşmanın 8 saate ölçeklenmiş oranı (oran × 8 / aralık; aralık ardışık farktan, {1, 2, 4, 8} saat); uzlaşma
aralık + 1 saat + 60 sn'den eskiyse yok. Yüzdelik = (T − 20 gün, güncel) penceresindeki DİĞER uzlaşmaların güncelden
KESİN küçük (`f8_hi`) / KESİN büyük (`f8_lo`) olanlarının payı, en az 30 değer (eşitlik uç sayılmaz). Pencere 20 gündür
(mühürlü `pct_window_days` / `pct_window.days` = fut_v1 `WINDOW_DAYS`; değilse kurulum hatası).

* **FUND_FADE** — f8 son 20 günün ≥ %95'liği (`f8_hi ≥ 0,95`) → short; ≤ %5'liği (`f8_lo ≥ 0,95`) → long. Çıkış
  yalnız en çok tutuş (24h = 6, 72h = 18 × 4h bar; sahip kararı B8 — ekin saati 4h bara bölünerek doğrulanır) ya da stop
  3 × ATR14(4h).
* **FUND_FADE_LOW** — aynısı, yalnız ER20 < 0,3 (4h kapanışları; B3).
* **FUND_CARRY** — |f8| son 20 günün ≥ %90'lığı iken (|f8| üzerinde aynı fonksiyon: `f8_hi(|f8|) ≥ 0,90`) fonlamayı ALAN
  taraf (f8 > 0 → short, f8 < 0 → long; 0 → yön yok); çıkış |f8| < %50'lik (`f8_hi(|f8|) < 0,50`) → sonraki açılış;
  stop 3 × ATR14(4h); en çok 300 bar (LIB varsayılanı). Okunuş: işaret dönse de |f8| yüksekse kural çıkışı yoktur
  (yazılmadı; stop ve zaman stopu korur); |f8| ölçülemiyorsa (fonlama yok/bayat) tutulur.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from .. import futures_features as FF
from . import indicators as ind
from .contract import LONG, SHORT, TF_MS
from .pure import Cols, PureVariant

FADE_PARAM_KEYS = ("horizon_label", "funding", "pct_window_days", "short_pct_min", "long_pct_max")
FADE_LOW_PARAM_KEYS = FADE_PARAM_KEYS + ("er20_max",)
CARRY_PARAM_KEYS = ("funding", "enter_abs_pct_min", "exit_abs_pct_below", "pct_window")
CARRY_SIDES = "fonlamayı alan taraf"
ER_LEN = 20
#: kimliğe giren canlı modüller: fut_v1 fonlama özellikleri (f8 + 20 günlük yüzdelik) ve onun dilim yardımcısı
LIVE_MODULES: tuple[str, ...] = ("tradingbot.futures_features", "tradingbot.timeframes")
_NAN = float("nan")


def f8_state(rows: Sequence[Sequence[Any]] | None, now_ms: int, *, absolute: bool = False) -> tuple[float, float, float]:
    """(f8, f8_hi, f8_lo) karar anı `now_ms`'de — `futures_features.align_funding` (fut_v1) uzlaşma satırlarından
    (`(ms, oran, …)`). `absolute`: oranların mutlak değeriyle (|f8| ve onun yüzdeliği). Yoksa NaN."""
    if not rows:
        return _NAN, _NAN, _NAN
    t = [int(r[0]) for r in rows]
    f = [abs(_rate(r[1])) if absolute else _rate(r[1]) for r in rows]
    out = FF.align_funding(np.asarray([int(now_ms)], dtype=np.int64), t, f)
    return float(out["f8"][0]), float(out["f8_hi"][0]), float(out["f8_lo"][0])


def _rate(x: Any) -> float:
    """Okunamayan oran NaN (fut_v1: NaN uzlaşma ne güncel ne pencere değeri olur)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return _NAN


def _pct(x: Any, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 < x < 100:
        raise ValueError(f"{what} (0, 100) yüzde olmalı: {x!r}")
    return float(x) / 100.0


class _FundingBase(PureVariant):
    FAMILY = "H"
    live_modules = LIVE_MODULES
    needs_funding = True
    funding_lookback_ms = int(FF.live_requirements("4h")["funding_ms"])

    def _check_common(self, p: Mapping[str, Any], window_days: Any) -> None:
        if p["funding"] != "f8":
            raise ValueError(f"{self.variant_id}: fonlama ölçüsü f8 (fut_v1) olmalı")
        if window_days != FF.WINDOW_DAYS:
            raise ValueError(f"{self.variant_id}: yüzdelik penceresi fut_v1'in {FF.WINDOW_DAYS} günü olmalı: {window_days!r}")
        if self.signal_tf != "4h":
            raise ValueError(f"{self.variant_id}: dilim 4h olmalı")


class FundingFade(_FundingBase):
    PARAM_KEYS = FADE_PARAM_KEYS
    EXIT_RULE = "MAX_HOLD_OR_STOP"
    SIDES_ALLOWED = ("LS",)

    def setup(self, p: Mapping[str, Any]) -> None:
        self._check_common(p, p["pct_window_days"])
        self.short_min = _pct(p["short_pct_min"], f"{self.variant_id}: short_pct_min")
        self.long_min = 1.0 - _pct(p["long_pct_max"], f"{self.variant_id}: long_pct_max")
        label = str(p["horizon_label"])
        hours = int(label[:-1]) if label.endswith("h") and label[:-1].isdigit() else -1
        bars = hours * 3_600_000 / TF_MS[self.signal_tf]
        ex = self.spec_exit_rule
        if not (bars == self.max_hold and isinstance(ex, Mapping) and ex.get("max_hold_bars") == self.max_hold
                and ex.get("decision") == "B8") or self.target_r is not None:
            raise ValueError(f"{self.variant_id}: '{label}' = en çok tutuş {bars} × 4h olmalı (B8); gelen {self.max_hold}")

    def _extra_filter(self, cols: Cols) -> tuple[bool, dict[str, Any]]:
        return True, {}

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        f8, hi, lo = f8_state(ctx.get("funding"), int(ctx["now_ms"]))
        if f8 != f8 or hi != hi or lo != lo:
            return None
        if hi >= self.short_min:
            side = SHORT
        elif lo >= self.long_min:
            side = LONG
        else:
            return None
        ok, ev = self._extra_filter(cols)
        if not ok:
            return None
        return side, {"f8": f8, "f8_hi": hi, "f8_lo": lo, **ev}


class FundingFadeLowEr(FundingFade):
    PARAM_KEYS = FADE_LOW_PARAM_KEYS

    def setup(self, p: Mapping[str, Any]) -> None:
        super().setup(p)
        x = p["er20_max"]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 < x <= 1:
            raise ValueError(f"{self.variant_id}: er20_max (0, 1] olmalı")
        self.er_max = float(x)

    def _extra_filter(self, cols: Cols) -> tuple[bool, dict[str, Any]]:
        e = ind.er(cols.close, ER_LEN, len(cols) - 1)
        return (e is not None and e < self.er_max), {"er20": e}


class FundingCarry(_FundingBase):
    PARAM_KEYS = CARRY_PARAM_KEYS
    EXIT_RULE = "ABS_F8_BELOW_PCTL"
    SIDES_ALLOWED = (CARRY_SIDES,)

    def setup(self, p: Mapping[str, Any]) -> None:
        w = p["pct_window"]
        self._check_common(p, w.get("days") if isinstance(w, Mapping) else None)
        if not (isinstance(w, Mapping) and w.get("decision") == "B8"):
            raise ValueError(f"{self.variant_id}: pct_window sahip kararı B8 olmalı")
        self.enter_min = _pct(p["enter_abs_pct_min"], f"{self.variant_id}: enter_abs_pct_min")
        self.exit_below = _pct(p["exit_abs_pct_below"], f"{self.variant_id}: exit_abs_pct_below")
        if not self.exit_below < self.enter_min or self.target_r is not None:
            raise ValueError(f"{self.variant_id}: çıkış yüzdeliği girişinkinden küçük olmalı, hedef yok")
        self.allow_short = True                          # yön fonlamanın işaretinden (alan taraf)

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        rows, now = ctx.get("funding"), int(ctx["now_ms"])
        af8, ahi, _alo = f8_state(rows, now, absolute=True)
        if af8 != af8 or ahi != ahi or ahi < self.enter_min:
            return None
        f8, _hi, _lo = f8_state(rows, now)
        if f8 != f8 or f8 == 0.0:
            return None
        return (SHORT if f8 > 0 else LONG), {"f8": f8, "abs_f8_hi": ahi}

    def exit_reason(self, pos, cols: Cols, ctx) -> str | None:
        _af8, ahi, _alo = f8_state(ctx.get("funding"), int(ctx["now_ms"]), absolute=True)
        if ahi != ahi:
            return None
        return "ABS_F8_BELOW_P%d" % round(self.exit_below * 100) if ahi < self.exit_below else None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> PureVariant:
    if variant_id.startswith("FUND_FADE_LOW_"):
        return FundingFadeLowEr(variant_id, params, spec)
    if variant_id.startswith("FUND_FADE_"):
        return FundingFade(variant_id, params, spec)
    if variant_id == "FUND_CARRY":
        return FundingCarry(variant_id, params, spec)
    raise KeyError(variant_id)


__all__ = ["CARRY_PARAM_KEYS", "FADE_LOW_PARAM_KEYS", "FADE_PARAM_KEYS", "FundingCarry", "FundingFade", "FundingFadeLowEr",
           "LIVE_MODULES", "f8_state", "make"]
