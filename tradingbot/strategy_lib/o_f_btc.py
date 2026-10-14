"""P3b-2b — örtü faktörü `F_BTC` (SAF; §2.2 satır O, sahip kararı B4, teknik okuma `covers.factors.F_BTC.reading`).

Filtre: BTC 4h rejimi niyetin yönüyle aynı — LONG yalnız UP'ta, SHORT yalnız DOWN'da; rejim = T2'nin kullandığı canlı
`regime_gate.btc_regime` (kendisi çağrılır), karar anında KAPANMIŞ son 699 BTC 4h barında (canlı perp çerçevesinin 4h
penceresi; `ctx["btc_4h_bars"]`, çekirdek verir): son kapanış > EMA200 → UP, değilse DOWN; satırlarda `ema200` sütunu yok →
`regime_gate.ema_last` (SMA200 tohumlu, pencerenin kapanışlarından); < 200 bar → rejim bilinmiyor → giriş yok.
"""
from __future__ import annotations

from typing import Any, Mapping

from .. import regime_gate
from .o_cover import Factor, make_cover

LIVE_MODULES: tuple[str, ...] = (
    "tradingbot.regime_gate",           # btc_regime / ema_last: rejimin canlı tanımı (B4)
)


class BtcRegime(Factor):
    NAME, KIND = "F_BTC", "filter"
    PARAM_KEYS = ("regime", "reading")
    needs_btc_4h = True

    def setup(self, fp: Mapping[str, Any]) -> None:
        g, r = fp["regime"], fp["reading"]
        if (g["definition"], g["timeframe"], int(g["ema_len"])) != ("regime_gate.btc_regime", "4h", regime_gate.EMA_LEN):
            raise ValueError(f"{self.cover.variant_id}: F_BTC rejim tanımı beklenmiyor: {dict(g)}")
        if (r["window_bars"], r["unknown"], r["ema200_column"]) != (699, "NO_ENTRY", False):
            raise ValueError(f"{self.cover.variant_id}: F_BTC okunuşu beklenmiyor: {dict(r)}")
        self.btc_4h_lookback = int(r["window_bars"])

    def regime(self, ctx) -> str | None:
        rows = [{"timestamp": r["timestamp"], "close": r["close"]}            # ema200 sütunu yok (okunuş)
                for r in list(ctx.get("btc_4h_bars") or ())[-self.btc_4h_lookback:]]
        return regime_gate.btc_regime(rows) if rows else None

    def on_intent(self, it, bars, ctx):
        reg = self.regime(ctx)
        ok = (it.side == "LONG" and reg == regime_gate.UP) or (it.side == "SHORT" and reg == regime_gate.DOWN)
        return it if ok else None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Any):
    return make_cover(variant_id, params, spec, base, BtcRegime)


__all__ = ["BtcRegime", "LIVE_MODULES", "make"]
