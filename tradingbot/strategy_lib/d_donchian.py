"""P3b-2 — D ailesi (Donchian): `DON_{20_10,55_20}_{4h,1d}_{LO,LS}` (SAF; docs/ENGINE_BRAIN_V1.md §2.2).

Kural (mühürlü satır): kanal kırılımı; stop 2 × ATR14 (sinyal dilimi) sinyal kapanışından; çıkış karşı kanal; en çok
300 bar. Tanım canlı D4'ünkidir (`donchian_trend`, laboratuvarın `TREND_DONCHIAN_20_10`'u; göstergeler
`signal_lab.aux_series` / `tradingbot.indicators.donchian` ile aynı): giriş = kapanış ÖNCEKİ N barın en yükseğini İLK
kez aşar (bir önceki kapanış kendi önceki N barının en yükseğini aşmamıştı) → long; LS'de simetriği (ÖNCEKİ N barın en
düşüğünün altına ilk kapanış) → short; çıkış = kapanış ÖNCEKİ M barın en düşüğünün altında (long) / en yükseğinin
üstünde (short) → sonraki açılış. `DON_20_10_4h_LO ≡ K_D4` (§2.2): o ızgara üyesi kataloğun alias'ıdır ve canlı D4
kopyasını çalıştırır (aynı `spec_sha`, tekilleşir); bu modülün 20/10/4h/LO kurulumu aynı sinyalleri verir (test).
Okunuş: çekirdek tek pozisyon tutar; long'un çıkış barında oluşan ilk short kırılımı (taze değil artık) alınmaz — LS'de
short yeni bir taze kırılım bekler.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import indicators as ind
from .contract import LONG, SHORT
from .pure import Cols, PureVariant, fresh_above, fresh_below

PARAM_KEYS = ("entry_channel_bars", "exit_channel_bars")
LIVE_MODULES: tuple[str, ...] = ()


class DonchianBreakout(PureVariant):
    FAMILY = "D"
    PARAM_KEYS = PARAM_KEYS
    EXIT_RULE = "DONCHIAN_OPPOSITE_CHANNEL"
    live_modules = LIVE_MODULES

    def setup(self, p: Mapping[str, Any]) -> None:
        n, m = p["entry_channel_bars"], p["exit_channel_bars"]
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 2 for x in (n, m)) or not m < n:
            raise ValueError(f"{self.variant_id}: 2 ≤ exit_channel_bars < entry_channel_bars tam sayı olmalı")
        self.n, self.m = int(n), int(m)

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        i = len(cols) - 1
        c = cols.close
        hi, hi_p = ind.donchian_high(cols.high, self.n, i), ind.donchian_high(cols.high, self.n, i - 1)
        if fresh_above(c, hi, hi_p):
            return LONG, {"channel_high": hi, "entry_channel_bars": self.n}
        lo, lo_p = ind.donchian_low(cols.low, self.n, i), ind.donchian_low(cols.low, self.n, i - 1)
        if fresh_below(c, lo, lo_p):
            return SHORT, {"channel_low": lo, "entry_channel_bars": self.n}
        return None

    def exit_reason(self, pos, cols: Cols, ctx) -> str | None:
        i = len(cols) - 1
        c = cols.close[-1]
        if pos.side == LONG:
            ref = ind.donchian_low(cols.low, self.m, i)
            return f"DONCHIAN_EXIT_LOW{self.m}" if ref == ref and c < ref else None
        ref = ind.donchian_high(cols.high, self.m, i)
        return f"DONCHIAN_EXIT_HIGH{self.m}" if ref == ref and c > ref else None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> DonchianBreakout:
    return DonchianBreakout(variant_id, params, spec)


__all__ = ["DonchianBreakout", "LIVE_MODULES", "PARAM_KEYS", "make"]
