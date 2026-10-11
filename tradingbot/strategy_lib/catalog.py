"""P3b — `strategy_lib` varyant kataloğu: varyant kimliği → kuran modül (SAF).

P3b-1 yalnız canlı defter kopyalarını (K ailesi, 13 varyant) içerir; B…O aileleri P3b-2'dedir. Parametreler bu pakette
YAZILMAZ: çağıran mühürlü kaydın (`LIB_V1`) varyant `params`'ını verir (tek kaynak); anahtar kümesi tam olmalıdır.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any, Mapping

from .contract import Variant

C4_VARIATIONS = tuple(f"K_C4_CV{i:03d}" for i in range(1, 9))
#: varyant kimliği → modül (bu paketin içinde, göreli ad)
MODULES: Mapping[str, str] = {"K_T2": "k_trend", "K_M2": "k_trend", "K_BOX": "k_box", "K_D4": "k_d4",
                              **{v: "k_c4" for v in C4_VARIATIONS}, "K_FM": "k_fm"}
IMPLEMENTED: tuple[str, ...] = tuple(MODULES)
#: parite ve hız ölçümünün aile birimi: kopyanın canlı defteri
BOOK_OF: Mapping[str, str] = {"K_T2": "T2", "K_M2": "M2", "K_BOX": "BOX", "K_D4": "D4",
                              **{v: "C4" for v in C4_VARIATIONS}, "K_FM": "FM"}


def module_name(variant_id: str) -> str:
    try:
        return f"{__package__}.{MODULES[variant_id]}"
    except KeyError:
        raise KeyError(f"strategy_lib'de uygulanmamış varyant: {variant_id}") from None


def make(variant_id: str, params: Mapping[str, Any]) -> Variant:
    """Varyantı mühürlü parametreleriyle kur. Bilinmeyen varyant KeyError, eksik/fazla parametre ValueError."""
    return import_module(module_name(variant_id)).make(variant_id, params)


__all__ = ["BOOK_OF", "C4_VARIATIONS", "IMPLEMENTED", "MODULES", "make", "module_name"]
