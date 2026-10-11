"""P3b — strateji kütüphanesi `LIB_v1` (docs/ENGINE_BRAIN_V1.md §2.1): saf varyant fonksiyonları.

* Her varyant `signals(bars, ctx) -> list[Intent]` ve `manage(pos, bars, ctx) -> Action` yazar; G/Ç, saat, ağ ve
  rastgelelik YOK (AST testi; tek istisna `identity`'nin depo kaynaklarını okuması).
* Kopyalar (`K_*`) yeniden yazılmaz: canlı kural fonksiyonları çağrılır (`paper_rules.decide_from_rows` /
  `decide_with_structures`, `pattern_trader.strategy_v3.build_plans_v3`); saf çağrılamayan tek canlı giriş yolu C4'ün
  kapısıdır (bağdaştırıcı: `k_c4`).
* Paket motor paketini import etmez; motor ve (AUTO_v1'de) worker aynı modülü kullanır. Parametreler mühürlü kayıttan
  gelir (`catalog.make(variant_id, params)`).
* P3b-1: yalnız K ailesi (13 varyant). B…O aileleri P3b-2.
"""
from __future__ import annotations

from .catalog import BOOK_OF, IMPLEMENTED, make, module_name
from .contract import (ACT_CLOSE, ACT_MOVE_STOP, ACT_NONE, LONG, NONE, SHORT, TARGET_PRICE, TARGET_R_FROM_ENTRY, TF_MS,
                       Action, BookEntry, EntryRef, Intent, PosView, RiskAtrBounds, Target, Variant, close, move_stop)

__all__ = ["ACT_CLOSE", "ACT_MOVE_STOP", "ACT_NONE", "Action", "BOOK_OF", "BookEntry", "EntryRef", "IMPLEMENTED", "Intent",
           "LONG", "NONE", "PosView", "RiskAtrBounds", "SHORT", "TARGET_PRICE", "TARGET_R_FROM_ENTRY", "TF_MS", "Target",
           "Variant", "close", "make", "module_name", "move_stop"]
