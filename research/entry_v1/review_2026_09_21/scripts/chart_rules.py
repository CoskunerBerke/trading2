# -*- coding: utf-8 -*-
"""V5 — grafik formasyonu giriş kuralları: ÜRETİM modülüne DELEGE eder, mantık KOPYALAMAZ.

Karar mantığı `tradingbot.chart_confirmation.evaluate_variant` içindedir; canlı motor ve
replay aynı fonksiyonu çağırır. Bu dosya yalnız replay'in `(symbol, decision, plan, frames)`
kanca imzasını o fonksiyona çevirir.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:/Users/berke/wt-entry")))

from tradingbot.chart_confirmation import VARIANTS, evaluate_variant  # noqa: E402
from tradingbot.chart_patterns import ChartPatternConfig  # noqa: E402
from tradingbot.learn.weekly_structure import rows_from_frame  # noqa: E402

CFG = ChartPatternConfig()          # üretim varsayılanları; bu turda AYARLANMAZ


def make(variant: str):
    if variant not in VARIANTS:
        raise KeyError(variant)

    def rule(symbol, d, plan, frames):
        return evaluate_variant(variant, direction=str(getattr(d, "direction", "") or ""),
                                bars_4h=rows_from_frame(frames.get("4h")),
                                bars_1d=rows_from_frame(frames.get("1d")), cfg=CFG)
    return rule


ARMS = {v: v for v in VARIANTS}


def build(name: str):
    return make(ARMS[name])


def all_names() -> list[str]:
    return list(ARMS)
