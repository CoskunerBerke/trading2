"""S1d — `LIB_AG_V1` örtüleri (AG_O): canlı defter kopyalarının AYRI KOPYALARINA tek bir örtü (SAF). Mühür `LIB_AG_V1`.

Taban = canlı kopyanın kendisi, S1 `LIB_V1`'deki mühürlü parametreleriyle (T2, M2, BOX, D4, FM; C4'te `LIB_V1` örtülerinin
okunuşu: C4 defteri, sekiz varyasyon config sırasıyla, ilk eşleşen). Taban varyantları DEĞİŞMEZ; örtü ayrı bir varyanttır
(`AGO_<taban>_<örtü>`), kendi tabanını kurar (paylaşılan akış yok: FM'nin anahtar beyanı yok, üç örtü küçük).

* `KZ`: taban niyeti yalnız giriş anı (karar anı = sonraki açılış) New York öldürme bölgesindeyse (LONDON ∪ NYAM ∪ LUNCH ∪
  PM) geçer. 1d tabanlarda (T2, M2) karar 00:00 UTC'dedir (New York 19:00/20:00) → bölge dışı: yapısal olarak işlemsizdir
  (mühürde `structurally_empty`; deneme olarak yine sayılır).
* `NOREACT`: dolumdan sonraki 3 giriş-dilimi barında (giriş barı 1) en iyi lehte uç (MFE) < 0,3R ise (R = |dolum − ilk
  stop|) 3. barın kapanışında çık (çekirdek sonraki açılışta piyasa; 7/24 seride kapanışa eşdeğer). Tabanın CLOSE'u
  önceliklidir; örtünün CLOSE'u tabanın MOVE_STOP'unun önüne geçer; tabanın bilgi işaretleri korunur.
* `CAL`: giriş anının New York tarihi mühürlü NYSE tam tatili ya da FOMC açıklama günüyse giriş yok; takvimin kapsamı dışında
  (2023-01-01 öncesi, 2026-12-31 sonrası) giriş yok (`CAL_UNCOVERED`; tahmin edilmez).
"""
from __future__ import annotations

from typing import Any, Mapping

from . import ag_clock as C
from .contract import ACT_CLOSE, LONG, Action, Intent, PosView, Variant, check_params, close

LIVE_MODULES: tuple[str, ...] = ()
PARAM_KEYS = ("base", "overlay", "overlay_params")
SPEC_KEYS = ("family", "timeframes", "signal_tf", "exits", "base_variants", "base_params")
BASES = ("T2", "M2", "BOX", "D4", "C4", "FM")
OVERLAYS = ("KZ", "NOREACT", "CAL")
_ATTRS = ("signal_tf", "timeframes", "lookback", "needs_btc", "btc_lookback", "needs_funding", "funding_lookback_ms")


class AgOverlay(Variant):
    family = "AG_O"

    def __init__(self, variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Variant):
        p = check_params(variant_id, params, PARAM_KEYS)
        if spec is None or set(spec) != set(SPEC_KEYS) or spec["family"] != "AG_O":
            raise ValueError(f"{variant_id}: spec anahtarları {sorted(SPEC_KEYS)} ve aile AG_O olmalı")
        if p["base"] not in BASES or p["overlay"] not in OVERLAYS or variant_id != f"AGO_{p['base']}_{p['overlay']}":
            raise ValueError(f"{variant_id}: kimlik AGO_<taban>_<örtü> olmalı")
        self.variant_id = variant_id
        self.params = dict(p)
        self.base = base
        for a in _ATTRS:
            setattr(self, a, getattr(base, a))
        if list(spec["timeframes"]) != list(base.timeframes) or spec["signal_tf"] != base.signal_tf:
            raise ValueError(f"{variant_id}: dilimler tabanınki olmalı")
        self.kind = str(p["overlay"])
        op = dict(p["overlay_params"])
        if self.kind == "KZ":
            if set(op) != {"sessions_min", "kz", "clock"} or op["clock"] != "America/New_York":
                raise ValueError(f"{variant_id}: KZ parametreleri")
            _s, self.kz = C.parse_sessions(op["sessions_min"], op["kz"])
        elif self.kind == "NOREACT":
            if set(op) != {"bars", "min_mfe_r", "exit"} or op["exit"] != "CLOSE_AT_BAR_CLOSE_NEXT_OPEN":
                raise ValueError(f"{variant_id}: NOREACT parametreleri")
            self.nr_bars, self.nr_r = int(op["bars"]), float(op["min_mfe_r"])
            if self.nr_bars < 1 or not 0 < self.nr_r < 10:
                raise ValueError(f"{variant_id}: NOREACT değerleri")
        else:
            if set(op) != {"calendar", "blocked_dates", "covered_from", "covered_until", "uncovered", "clock"} \
                    or op["uncovered"] != "NO_ENTRY" or op["clock"] != "America/New_York":
                raise ValueError(f"{variant_id}: CAL parametreleri")
            self.blocked = frozenset(C.day_of_iso(d) for d in op["blocked_dates"])
            self.cov = (C.day_of_iso(op["covered_from"]), C.day_of_iso(op["covered_until"]))
            if not self.cov[0] <= self.cov[1] or not self.blocked:
                raise ValueError(f"{variant_id}: CAL kapsamı")

    # ------------------------------------------------------------------ çekirdek kancaları
    def bind_series(self, token: Any) -> None:
        bind = getattr(self.base, "bind_series", None)
        if callable(bind):
            bind(token)

    @property
    def fast(self) -> bool:
        return bool(getattr(self.base, "fast", False))

    @fast.setter
    def fast(self, v: bool) -> None:
        if hasattr(self.base, "fast"):
            self.base.fast = bool(v)

    def entry_ok(self, now: int) -> bool:
        if self.kind == "KZ":
            return C.in_kz(now, self.kz)
        if self.kind == "CAL":
            d = C.ny_day(now)
            return self.cov[0] <= d <= self.cov[1] and d not in self.blocked
        return True

    # ------------------------------------------------------------------ sözleşme
    def signals(self, bars, ctx) -> list[Intent]:
        if not self.entry_ok(int(ctx["now_ms"])):
            return []
        return list(self.base.signals(bars, ctx))

    def manage(self, pos: PosView, bars, ctx) -> Action:
        act = self.base.manage(pos, bars, ctx)
        if self.kind != "NOREACT" or act.kind == ACT_CLOSE or int(pos.bars_held) != self.nr_bars:
            return act
        rows = [r for r in (bars.get(self.signal_tf) or ()) if int(r["timestamp"]) >= int(pos.entry_bar_ts)]
        risk = abs(float(pos.entry) - float(pos.initial_stop))
        if not rows or risk <= 0:
            return act
        mfe = (max(float(r["high"]) for r in rows) - float(pos.entry)) if pos.side == LONG else \
            (float(pos.entry) - min(float(r["low"]) for r in rows))
        if mfe >= self.nr_r * risk:
            return act
        out = close("NOREACT_MFE_LT_%gR" % self.nr_r)
        if act.flags:
            out = Action(out.kind, out.reason, out.price, tuple(act.flags))
        return out


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Variant) -> AgOverlay:
    return AgOverlay(variant_id, params, spec, base)


__all__ = ["AgOverlay", "BASES", "LIVE_MODULES", "OVERLAYS", "PARAM_KEYS", "SPEC_KEYS", "make"]
