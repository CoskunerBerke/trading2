# -*- coding: utf-8 -*-
"""V15 — Box Theory stratejisi: ÜRETİM modülüne DELEGE eder (`tradingbot.box_theory`).

Karar mantığı tek kaynakta; bu dosya yalnız replay'in `(sym, t, frames, position, replay)` kanca
imzasını `decide(...)`ye çevirir. Parametreler `BoxParams` ile gelir, burada sabit yoktur.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:/Users/berke/wt-entry")))

from tradingbot.box_theory import BoxParams  # noqa: E402
from tradingbot.candle_confirmation import closed_bars  # noqa: E402
from tradingbot.paper_rules import decide_from_rows  # noqa: E402
from tradingbot.box_theory import rows_from_frame  # noqa: E402
from tradingbot.ema200_trend import daily_rows_from_frame  # noqa: E402
from tradingbot.timeframes import tf_ms  # noqa: E402

M5_MS = tf_ms("5m")


def make(params: BoxParams, *, tf: str = "5m"):
    """Replay kancası. `tf` replay'in birincil dilimi (karar anı = barın KAPANIŞI)."""
    step = tf_ms(tf)
    p = params.validate()

    def strat(sym, t, fr, pos, rp):
        now_ms = t + step
        d1 = closed_bars(daily_rows_from_frame(fr.get("1d"), tail=10), now_ms=now_ms, tf="1d")
        m5 = closed_bars(rows_from_frame(fr.get(tf), tail=400), now_ms=now_ms, tf=tf)
        return decide_from_rows("b1_box_fade", daily=d1, intraday=m5, btc_rows=None,
                                position=pos, params=p)
    return strat


def build(params: BoxParams | None = None, **kw):
    return make(params or BoxParams(**kw))
