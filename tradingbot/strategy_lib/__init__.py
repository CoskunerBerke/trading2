"""P3b — strateji kütüphanesi `LIB_v1` (docs/ENGINE_BRAIN_V1.md §2.1): saf varyant fonksiyonları.

* Her varyant `signals(bars, ctx) -> list[Intent]` ve `manage(pos, bars, ctx) -> Action` yazar; G/Ç, saat, ağ ve
  rastgelelik YOK (AST testi; tek istisna `identity`'nin depo kaynaklarını okuması).
* Kopyalar (`K_*`) yeniden yazılmaz: canlı kural fonksiyonları çağrılır (`paper_rules.decide_from_rows` /
  `decide_with_structures`, `pattern_trader.strategy_v3.build_plans_v3`); saf çağrılamayan tek canlı giriş yolu C4'ün
  kapısıdır (bağdaştırıcı: `k_c4`).
* Paket motor paketini import etmez; motor ve (AUTO_v1'de) worker aynı modülü kullanır. Parametreler mühürlü kayıttan
  gelir (`catalog.make(variant_id, params[, spec])`).
* P3b-1: K ailesi (13). P3b-2a: saf aileler B, C, D, E, F1, F2, H (45; göstergeler `indicators`, ortak taban `pure`);
  alias'lar (`DON_20_10_4h_LO ≡ K_D4`, `BOX_n10_mid_ms0.5 ≡ K_BOX`) tabanın kopyasını çalıştırır; I, J, G mühürlü boşluk
  yüzünden `BLOCKED` (çalışan kod yok). P3b-2b: O örtüleri (35; çerçeve `o_cover`, faktör modülleri `o_f_*`/`o_x_*`,
  paylaşılan taban akışı `stream`); kopyaların hızlı çerçevesi (`livebars.FastFrame`) ve Box/D4 ön-süzgeci.
"""
from __future__ import annotations

from .catalog import BLOCKED, BOOK_OF, IMPLEMENTED, BlockedVariant, make, module_name, status
from .contract import (ACT_CLOSE, ACT_MOVE_STOP, ACT_NONE, LONG, NONE, SHORT, TARGET_PRICE, TARGET_R_FROM_ENTRY, TF_MS,
                       Action, BookEntry, EntryRef, Intent, PosView, RiskAtrBounds, Target, Variant, canon, close, hold,
                       move_stop)

__all__ = ["ACT_CLOSE", "ACT_MOVE_STOP", "ACT_NONE", "Action", "BLOCKED", "BOOK_OF", "BlockedVariant", "BookEntry",
           "EntryRef", "IMPLEMENTED", "Intent",
           "LONG", "NONE", "PosView", "RiskAtrBounds", "SHORT", "TARGET_PRICE", "TARGET_R_FROM_ENTRY", "TF_MS", "Target",
           "Variant", "canon", "close", "hold", "make", "module_name", "move_stop", "status"]
