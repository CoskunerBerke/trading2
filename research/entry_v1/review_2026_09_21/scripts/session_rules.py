# -*- coding: utf-8 -*-
"""V6 — seans kuralları: ÜRETİM modülüne DELEGE eder (`tradingbot.session_gate`).

Karar anı = kapanmış 4h barın kapanış zamanı (son timestamp + 4h). Replay'de bu, `t + 4h`
ile aynıdır; üretimde `now` karşılığıdır.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:/Users/berke/wt-entry")))

from session_gate import VARIANTS, evaluate_variant  # noqa: E402  (arastirma paketi; uretime GIRMEDI)

H4_MS = 4 * 3_600_000


def make(variant: str):
    if variant not in VARIANTS:
        raise KeyError(variant)

    def rule(symbol, d, plan, frames):
        f4 = frames.get("4h")
        if f4 is None or len(f4) == 0:
            return False, variant.split("_", 1)[0].upper() + "_NO_TIME"
        decision_ms = int(f4["timestamp"].iloc[-1]) + H4_MS
        return evaluate_variant(variant, decision_ms=decision_ms)
    return rule


ARMS = {v: v for v in VARIANTS}


def build(name: str):
    return make(ARMS[name])


def all_names() -> list[str]:
    return list(ARMS)
