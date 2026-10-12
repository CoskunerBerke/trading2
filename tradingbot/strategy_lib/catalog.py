"""P3b — `strategy_lib` varyant kataloğu: varyant kimliği → kuran modül (SAF).

Parametreler bu pakette YAZILMAZ: çağıran mühürlü kaydın (`LIB_V1`) varyant `params`'ını (anahtar kümesi tam) ve saf
ailelerde satırın `spec`'ini (`{family, timeframes, signal_tf, sides, exits}`; F2'de ayrıca `base_params` = K_BOX'un
mühürlü parametreleri) verir (tek kaynak; motor tarafı `kernel.make_variant`).

Durumlar (`status`): `IMPLEMENTED` (çalışan modül), `ALIAS` (ızgara üyesi tabanının kopyasını çalıştırır: §2.2
`DON_20_10_4h_LO ≡ K_D4`, `BOX_n10_mid_ms0.5 ≡ K_BOX`; aynı modül → aynı `module_src_sha` → aynı `spec_sha`),
`BLOCKED` (mühürlü boşluk: I, J, G ailelerinin 13 varyantı — §8.5 B9 ve B10 altın stopları; ÇALIŞAN KOD YOK, `make`
`BlockedVariant` fırlatır), `PENDING` (O örtüleri: sonraki P3b-2 adımı). Liste kayıtla (`registry.blocked_variants`,
`alias_of`) testte eşitlenir.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any, Mapping

from .contract import Variant

C4_VARIATIONS = tuple(f"K_C4_CV{i:03d}" for i in range(1, 9))
_K: dict[str, str] = {"K_T2": "k_trend", "K_M2": "k_trend", "K_BOX": "k_box", "K_D4": "k_d4",
                      **{v: "k_c4" for v in C4_VARIATIONS}, "K_FM": "k_fm"}
_B = {f"TS_{lb}_{s}": "b_tsmom" for lb in (7, 14, 28, 56, 84) for s in ("LO", "LS")}
_C = {f"MAX_{f}_{sl}_{tf}_{s}": "c_macross" for f, sl in ((20, 50), (50, 200)) for tf in ("4h", "1d") for s in ("LO", "LS")}
_D = {f"DON_{e}_{x}_{tf}_{s}": "d_donchian" for e, x in ((20, 10), (55, 20)) for tf in ("4h", "1d") for s in ("LO", "LS")
      if (e, x, tf, s) != (20, 10, "4h", "LO")}
_E = {**{f"KEL_20_2_{tf}": "e_volbreak" for tf in ("1h", "4h")}, **{f"BBSQ_20_{tf}": "e_volbreak" for tf in ("1h", "4h")}}
_F1 = {**{f"RSI2_{tf}": "f1_range" for tf in ("1h", "4h")}, **{f"BBRE_{tf}": "f1_range" for tf in ("1h", "4h")}}
_F2 = {f"BOX_n{n}_{x}_ms{ms}": "f2_boxgrid" for n in (10, 20) for x in ("mid", "2r") for ms in (0.5, 1.0)
       if (n, x, ms) != (10, "mid", 0.5)}
_H = {**{f"FUND_FADE_{h}": "h_funding" for h in ("24h", "72h")}, **{f"FUND_FADE_LOW_{h}": "h_funding" for h in ("24h", "72h")},
      "FUND_CARRY": "h_funding"}
#: varyant kimliği → modül (bu paketin içinde, göreli ad)
MODULES: Mapping[str, str] = {**_K, **_B, **_C, **_D, **_E, **_F1, **_F2, **_H}
IMPLEMENTED: tuple[str, ...] = tuple(MODULES)
#: varyant → aile (uygulanmış; alias'lar hariç)
FAMILY_OF: Mapping[str, str] = {**{v: "K" for v in _K}, **{v: "B" for v in _B}, **{v: "C" for v in _C},
                                **{v: "D" for v in _D}, **{v: "E" for v in _E}, **{v: "F1" for v in _F1},
                                **{v: "F2" for v in _F2}, **{v: "H" for v in _H}}
#: §2.2 alias'lı ızgara üyeleri → tabanın kopyası (kayıttaki `alias_of`; testte eşitlik)
ALIASES: Mapping[str, str] = {"DON_20_10_4h_LO": "K_D4", "BOX_n10_mid_ms0.5": "K_BOX"}
#: mühürlü boşluk yüzünden ÇALIŞTIRILMAYAN varyantlar (kayıttaki `blocked_variants`; testte eşitlik) → boşluk maddesi
BLOCKED: Mapping[str, str] = {
    **{f"BTCETH_Z_{z}": "SEALED_GAP:B9 (spread stopu, bacak oranı)" for z in ("2.0", "2.5")},
    **{f"XS_MOM_{lb}_k{k}": "SEALED_GAP:B9 (XSEC stopu/R birimi, beta penceresi, dengeleme anı)" for lb in (7, 28)
       for k in (3, 5)},
    "XS_REV_1D_k5": "SEALED_GAP:B9 (XSEC stopu/R birimi, dengeleme sıklığı, beta nötrlüğü)",
    **{g: "SEALED_GAP:B10 (altın stopu)" for g in ("G_LDN_ORB", "G_NY_ORB", "G_ASIA_DRIFT", "G_MONTH_END", "G_XAU_BTC_28",
                                                    "G_XAU_BTC_56")},
}
BLOCKED_FAMILIES = ("I", "J", "G")
#: henüz uygulanmamış (boşluk değil): O örtüleri (35) — sonraki P3b-2 adımı
PENDING_PREFIXES: Mapping[str, str] = {"O_": "O"}
IMPLEMENTED_S, ALIAS_S, BLOCKED_S, PENDING_S = "IMPLEMENTED", "ALIAS", "BLOCKED", "PENDING"
#: parite ve hız ölçümünün aile birimi: kopyanın canlı defteri (yalnız K)
BOOK_OF: Mapping[str, str] = {"K_T2": "T2", "K_M2": "M2", "K_BOX": "BOX", "K_D4": "D4",
                              **{v: "C4" for v in C4_VARIATIONS}, "K_FM": "FM"}


class BlockedVariant(KeyError):
    """Mühürlü boşluk yüzünden çalıştırılmayan varyant (`UNSPECIFIED_PARAM`): kurulamaz, kimliği hesaplanmaz."""


def status(variant_id: str) -> str:
    if variant_id in MODULES:
        return IMPLEMENTED_S
    if variant_id in ALIASES:
        return ALIAS_S
    if variant_id in BLOCKED:
        return BLOCKED_S
    if any(variant_id.startswith(p) for p in PENDING_PREFIXES):
        return PENDING_S
    raise KeyError(f"strategy_lib kataloğunda yok: {variant_id}")


def module_name(variant_id: str) -> str:
    """Varyantın modülü; alias tabanının modülüdür (aynı kaynak → aynı `module_src_sha`)."""
    if variant_id in BLOCKED:
        raise BlockedVariant(f"{variant_id}: {BLOCKED[variant_id]} — çalıştırılmaz")
    try:
        return f"{__package__}.{MODULES[ALIASES.get(variant_id, variant_id)]}"
    except KeyError:
        raise KeyError(f"strategy_lib'de uygulanmamış varyant: {variant_id}") from None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None = None) -> Variant:
    """Varyantı mühürlü parametreleriyle kur. Bilinmeyen/bekleyen varyant KeyError, boşluklu varyant `BlockedVariant`,
    eksik/fazla parametre ya da spec ValueError. Kopyalar (K) yalnız `params` alır; saf aileler `spec` ister. Alias
    tabanın kopyası olarak (tabanın kimliğiyle) kurulur; parametreleri tabanınkidir (çağıran onu verir)."""
    mod = import_module(module_name(variant_id))
    base = ALIASES.get(variant_id, variant_id)
    if FAMILY_OF.get(base) == "K":
        return mod.make(base, params)
    return mod.make(base, params, spec)


__all__ = ["ALIASES", "ALIAS_S", "BLOCKED", "BLOCKED_FAMILIES", "BLOCKED_S", "BOOK_OF", "BlockedVariant", "C4_VARIATIONS",
           "FAMILY_OF", "IMPLEMENTED", "IMPLEMENTED_S", "MODULES", "PENDING_PREFIXES", "PENDING_S", "make", "module_name",
           "status"]
