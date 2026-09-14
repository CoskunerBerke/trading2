# -*- coding: utf-8 -*-
"""V7 — rejim kuralları: ÜRETİM modülüne DELEGE eder (`tradingbot.regime_gate`).

BTC'nin son KAPANMIŞ günlük barı replay'in kendi `_slice` kuralıyla alınır (karar anı t'de
kapanmış barlar); geleceğe bakma yok. Kural `attach(rp)` ile replay örneğine bağlanır.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:/Users/berke/wt-entry")))

from tradingbot.regime_gate import (BTC_SYMBOL as BTC, VARIANTS, btc_regime,  # noqa: E402
                                    evaluate_variant, rows_for_regime as _rows)


def make(variant: str):
    if variant not in VARIANTS:
        raise KeyError(variant)
    tag = variant.split("_", 1)[0].upper()
    holder = {"rp": None, "cache": {}}

    def rule(symbol, d, plan, frames):
        rp = holder["rp"]
        if rp is None:
            return False, tag + "_NO_REPLAY"
        f4 = frames.get("4h")
        if f4 is None or len(f4) == 0:
            return False, tag + "_NO_TIME"
        t = int(f4["timestamp"].iloc[-1])
        reg = holder["cache"].get(t)
        if reg is None:
            btc = rp._slice(BTC, t).get("1d")
            reg = (btc_regime(_rows(btc)) if btc is not None else None) or "NONE"
            holder["cache"][t] = reg
        return evaluate_variant(variant, direction=str(getattr(d, "direction", "") or ""),
                                regime=None if reg == "NONE" else reg)

    rule.attach = lambda rp: holder.__setitem__("rp", rp)
    return rule


ARMS = {v: v for v in VARIANTS}


def build(name: str):
    return make(ARMS[name])


def all_names() -> list[str]:
    return list(ARMS)
