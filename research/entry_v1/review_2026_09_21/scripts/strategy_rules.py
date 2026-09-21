# -*- coding: utf-8 -*-
"""V9/V10 — sadeleştirme stratejileri: ÜRETİM modülüne DELEGE eder (`tradingbot.ema200_trend`).

Karar mantığı tek kaynakta; bu dosya yalnız replay'in `(sym, t, frames, position, replay)` kanca
imzasını `decide(...)`ye çevirir. T3 = T1 kuralı + üretimin başa-baş koruması (run_rule `--no-breakeven`
VERİLMEZ), yani kural aynı, defter parametresi farklı.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:/Users/berke/wt-entry")))

from tradingbot.candle_confirmation import closed_bars  # noqa: E402
from tradingbot.regime_gate import BTC_SYMBOL  # noqa: E402
from tradingbot.ema200_trend import VARIANTS, daily_rows_from_frame, decide  # noqa: E402

H4_MS = 4 * 3_600_000


def make(variant: str, leverage: int = 1, trail_arm_r: float = 0.0, trail_dist_r: float = 0.0):
    """KALDIRAC (2026-09-20): `decide` eskiden kaldiraci 1'e SABITLIYORDU ve bu kosucu
    parametre gecmiyordu. Kaldirac islem basina RISKI ARTIRMAZ (risk stop mesafesiyle
    belirlenir); tek pozisyon tavanini (`MAX_POSITION_PCT x kaldirac`) acar ve likidasyon
    mesafesini kisaltir. Olculdu: 1x'te stop %6,67'den darsa sinyal sessizce reddediliyordu."""
    if variant not in VARIANTS:
        raise KeyError(variant)
    cache: dict[int, list] = {}

    def strat(sym, t, fr, pos, rp):
        now_ms = t + H4_MS
        d1 = closed_bars(daily_rows_from_frame(fr.get("1d"), tail=320), now_ms=now_ms, tf="1d")
        btc = None
        if variant in ("t2_trend_regime", "m2_tsmom28"):
            btc = cache.get(t)
            if btc is None:
                b1d = rp._slice(BTC_SYMBOL, t).get("1d") if BTC_SYMBOL in rp.frames else None
                btc = closed_bars(daily_rows_from_frame(b1d), now_ms=now_ms, tf="1d")
                cache[t] = btc
        # TRAIL: pozisyon nesnesi zaten elimizde (replay `pos`u veriyor). Kural, MFE'yi
        # KAPANMIS GUNLUK barlardan hesaplar — canli motorla AYNI satirlar, parite bozulmaz.
        _p = None
        if pos is not None:
            _om = getattr(pos, "opened_at", None)
            if isinstance(_om, str) and _om:
                import datetime as _dt
                try:
                    _om = int(_dt.datetime.fromisoformat(_om).timestamp() * 1000)
                except ValueError:
                    _om = None
            if _om is not None:
                _p = {"entry_avg": getattr(pos, "entry_avg", None),
                      "initial_stop": getattr(pos, "initial_stop", None),
                      "opened_ts": int(_om)}
        return decide(variant, daily_rows=d1, btc_daily_rows=btc, position_open=pos is not None,
                      leverage=leverage, trail_arm_r=trail_arm_r, trail_dist_r=trail_dist_r,
                      position=_p)
    return strat


STRATEGIES = {"t1_trend": lambda: make("t1_trend"), "t2_trend_regime": lambda: make("t2_trend_regime"),
              "t3_trend_botexit": lambda: make("t1_trend"),
              "m2_tsmom28": lambda: make("m2_tsmom28")}      # V12: uretim moduluyle AYNI kural (parite: v11_m2_*)


def build(name: str, leverage: int = 1, trail_arm_r: float = 0.0, trail_dist_r: float = 0.0):
    if name not in STRATEGIES:
        raise KeyError(name)
    var = {"t3_trend_botexit": "t1_trend"}.get(name, name)
    return make(var, leverage=leverage, trail_arm_r=trail_arm_r, trail_dist_r=trail_dist_r)
