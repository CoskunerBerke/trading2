# -*- coding: utf-8 -*-
"""V4 — mum formasyonu giriş kuralları: ÜRETİM modülüne DELEGE eder, mantık KOPYALAMAZ.

Karar mantığı `tradingbot.candle_confirmation.evaluate_variant` içindedir; canlı motor ve
replay aynı fonksiyonu çağırır. Bu dosya yalnız replay'in `(symbol, decision, plan, frames)`
kanca imzasını o fonksiyona çevirir. Böylece DENEY_V4'te ölçülen şey ile üretimde çalışan
şey birebir aynı koddur.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:/Users/berke/wt-entry")))

from tradingbot.candle_confirmation import VARIANTS, evaluate_variant  # noqa: E402
from tradingbot.learn.candle_context import CandleContextConfig  # noqa: E402
from tradingbot.learn.weekly_structure import rows_from_frame  # noqa: E402

CFG = CandleContextConfig()          # üretim varsayılanları; bu turda AYARLANMAZ


def make(variant: str):
    if variant not in VARIANTS:
        raise KeyError(variant)

    def rule(symbol, d, plan, frames):
        # `frames` yalnız KAPANMIŞ barları içerir (`HistoricalReplay._slice`).
        return evaluate_variant(variant, direction=str(getattr(d, "direction", "") or ""),
                                bars_4h=rows_from_frame(frames.get("4h")),
                                bars_1d=rows_from_frame(frames.get("1d")), cfg=CFG)
    return rule


#: ÖN KAYITLI KOLLAR; parametre ızgarası YOK.
ARMS = {v: v for v in VARIANTS}


def build(name: str):
    return make(ARMS[name])


def all_names() -> list[str]:
    return list(ARMS)
