"""Mühür S1b (sahip kararı 2026-10-13): S1'e (`registry.py`, `REGISTRY_SHA`, `T_SEAL_MS`, bütün S1 sha'ları) DOKUNULMADAN,
P3e'nin iki açık kalanını AYRI ADLI, AYRI DOSYALI bir mühürle sabitler (docs/ENGINE_BRAIN_V1.md §8.4 S1b notu):

* `FUNDING_MARK_V1` — çekirdeğin fonlama mark kaynağı (WF P3d, keşif P3e, parite (a) aynı yol: `kernel.settlement_marks`):
  uzlaşma anında (nominal dakika) BAŞLAYAN markpx 1h barının AÇILIŞI; yoksa o anda BİTEN barın KAPANIŞI; ikisi de yoksa
  uzlaşma okunamaz → `FUNDING_GAP`. Uzlaşmadan sonraki fiyat hiç kullanılmaz. Fonlama satırının kendi mark'ı (REST)
  yalnız denetimdir: göreli fark > %0,5 → `FUNDING_MARK_MISMATCH` (bilgi; kapıdan çıkarmaz), tutar markpx'le.
* `ENTRY_UNIVERSE_V1` — keşfin (§5.1) 40 coin'lik giriş evreni, config.yaml `entry_universe.symbols` sırasıyla
  (2026-10-13). Çalışma anındaki evren bu kümeyle (sırasız) aynı değilse keşif ve WF ÇALIŞMAZ; eksik/fazla/yinelenen
  coin'ler raporlanır.
* `S1B_DIRECTIONS_V1` — "asla gevşetme" yön tablosu: iki okumanın bütün tanım alanları `fixed` (S1'in
  `THRESHOLD_DIRECTIONS_V1` makinesi `registry.looser_than` ile, registry.py'ye dokunmadan).

Kanonik bayt `registry.canonical_bytes` (sıralı anahtar, `(",", ":")`, UTF-8); kayıtlar derin donuk. `S1B_SHA` = {üç sha,
`S1B_SEAL_MS`, genişlettiği S1'in `REGISTRY_SHA`'sı} kanonik sha'sıdır (S1 değişirse S1b tutmaz → yeniden mühür).
`S1B_SEAL_MS` = bu mühür commit'inin işleyen zamanından birkaç dakika sonraki tam dakika (T_SEAL_MS ile aynı kural; test
git'ten doğrular). `PINNED` mühür anının değerleridir: içerik değişip sabitler yenilenmezse `check()` tutmaz ve çekirdek,
keşif ve WF ÇALIŞMAZ (kapalı hata, açık neden). Ağsız; yalnız `registry`'nin saf yardımcılarını kullanır.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from . import registry as RG

S1B_ID = "S1b"
S1B_DOC = "docs/ENGINE_BRAIN_V1.md §8.4 (S1b notu, 2026-10-13)"
#: bu mühür commit'inin UTC ms'si (commit'ten birkaç dakika sonraki tam dakika; elle geriye alınmaz)
S1B_SEAL_MS = 1791908100000
FIXED = RG.FIXED
OWNER_DECISION_TR = ("sahip kararı 2026-10-13 (koordinatör iletisi): S1 dokunulmadan kalır; P3e açık kalanları ayrı adlı "
                     "S1b mühürüyle sabitlenir")

FUNDING_MARK_V1: Mapping[str, Any] = RG.freeze({
    "id": "FUNDING_MARK_V1",
    "applies_to": ["research_engine/kernel.py (WF P3d, keşif P3e)", "research_engine/parity.py (parite (a) başvuru defteri)"],
    "timestamps": "deponun gerçek uzlaşma zaman damgaları; nominal dakikaya yuvarlanır (kernel.snap_ms)",
    "mark_series": "markpx_1h (araştırma deposu, vadeli mark fiyatı 1h; keşifte yalnız arşivle doğrulanmış satırlar)",
    "mark_source": "MARKPX_1H_OPEN_OF_BAR_STARTING_AT_SETTLEMENT",
    "mark_fallback": "MARKPX_1H_CLOSE_OF_BAR_ENDING_AT_SETTLEMENT",
    "mark_missing": "FUNDING_GAP",
    "mark_after_settlement": "NEVER",
    "row_mark": "CHECK_ONLY",
    "rest_mark_check": {"max_rel_diff": 0.005, "flag": "FUNDING_MARK_MISMATCH", "use": "MARKPX", "gate_effect": "NONE"},
    "decision": OWNER_DECISION_TR,
    "reading_tr": "çekirdeğin fonlama tutarı (−yön × açık miktar × mark × oran) arşivin fonlama satırının mark'ına BAĞLI "
                  "DEĞİLDİR (arşiv fundingRate zip'inde mark yok → NaN; REST satırının mark'ı arşiv gelince onunla yer "
                  "değiştirir): mark = uzlaşma anında BAŞLAYAN markpx 1h barının açılışı; o bar yoksa uzlaşma anında BİTEN "
                  "barın kapanışı; ikisi de yoksa uzlaşma okunamaz → işlem FUNDING_GAP (kapı dışı, bugünkü gibi). Satırın "
                  "kendi mark'ı yalnız denetim: ikisi de varsa ve göreli fark > %0,5 ise FUNDING_MARK_MISMATCH (bilgi) "
                  "ve tutar markpx'le. Uzlaşmadan sonraki fiyat (o anda başlayan barın yüksek/düşük/kapanışı, sonraki "
                  "barlar) hiç kullanılmaz",
})

ENTRY_UNIVERSE_V1: Mapping[str, Any] = RG.freeze({
    "id": "ENTRY_UNIVERSE_V1",
    "coins": ["BTC", "ETH", "ZEC", "SOL", "NEAR", "UNI", "XRP", "ONE", "BNB", "ARB", "DOGE", "LSK", "SUI", "ENA", "G", "WLD",
              "ADA", "ONDO", "1000PEPE", "COTI", "LINK", "REZ", "DASH", "AAVE", "TAO", "BCH", "AVAX", "DOT", "FIL", "APT",
              "SYN", "LTC", "XLM", "INJ", "FET", "TRX", "XMR", "1000SHIB", "OP", "KSM"],
    "size": 40,
    "order": "CONFIG_ORDER",
    "source": "config.yaml entry_universe.symbols (2026-10-13; S1 mühür anındaki giriş evreni)",
    "rule": "RUNTIME_SET_MUST_EQUAL_SEALED_SET_ELSE_REFUSE_AND_REPORT_DIFF",
    "decision": OWNER_DECISION_TR,
    "reading_tr": "keşfin 40 coin'i (LIB_V1.explore.entry_universe_size = 40) S1'de yalnız SAYI olarak mühürlüydü; S1b "
                  "kimliklerini dondurur. Çalışma anındaki giriş evreni (universe.entry_universe) bu kümeyle sırasız AYNI "
                  "değilse keşif ve WF çalışmaz, eksik/fazla/yinelenen coin'ler raporlanır; liste değişikliği yeni mühür "
                  "ister. P23'ün 23 coin'i bu listenin içindedir",
})

S1B_DIRECTIONS_V1: Mapping[str, Any] = RG.freeze({
    "FUNDING_MARK": {k: FIXED for k in ("timestamps", "mark_series", "mark_source", "mark_fallback", "mark_missing",
                                         "mark_after_settlement", "row_mark", "rest_mark_check.max_rel_diff",
                                         "rest_mark_check.flag", "rest_mark_check.use", "rest_mark_check.gate_effect")},
    "ENTRY_UNIVERSE": {k: FIXED for k in ("coins", "size", "order", "rule")},
})

REGISTRIES: Mapping[str, Any] = MappingProxyType({"FUNDING_MARK_V1": FUNDING_MARK_V1, "ENTRY_UNIVERSE_V1": ENTRY_UNIVERSE_V1,
                                                  "S1B_DIRECTIONS_V1": S1B_DIRECTIONS_V1})
#: yön tablosunun kapsadığı kayıtlar (sonraki sürümler buraya eklenir; v1'den gevşek olamaz)
S1B_VERSIONS: Mapping[str, tuple] = MappingProxyType({"FUNDING_MARK": (FUNDING_MARK_V1,),
                                                      "ENTRY_UNIVERSE": (ENTRY_UNIVERSE_V1,)})


def shas(registries: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Kayıtların ŞİMDİKİ kanonik sha'ları (varsayılan: modülün `REGISTRIES`'i, çağrı anında okunur)."""
    regs = REGISTRIES if registries is None else registries
    return {name: RG.sha256_canonical(reg) for name, reg in regs.items()}


def seal_doc(registries: Mapping[str, Any] | None = None, seal_ms: int | None = None,
             s1_registry_sha: str | None = None) -> dict[str, Any]:
    """S1b'nin kanonik belgesi: üç sha, `S1B_SEAL_MS`, genişlettiği S1'in `REGISTRY_SHA`'sı (hepsi çağrı anında okunur)."""
    return {"seal_id": S1B_ID, "shas": shas(registries), "s1b_seal_ms": int(S1B_SEAL_MS if seal_ms is None else seal_ms),
            "extends_s1_registry_sha": RG.REGISTRY_SHA if s1_registry_sha is None else s1_registry_sha}


SHAS: Mapping[str, str] = MappingProxyType(shas())
FUNDING_MARK_V1_SHA = SHAS["FUNDING_MARK_V1"]
ENTRY_UNIVERSE_V1_SHA = SHAS["ENTRY_UNIVERSE_V1"]
S1B_DIRECTIONS_V1_SHA = SHAS["S1B_DIRECTIONS_V1"]
S1B_SEAL: Mapping[str, Any] = RG.freeze(seal_doc())
S1B_SHA = RG.sha256_canonical(S1B_SEAL)

#: mühür anında sabitlenen değerler (içerik değişip bunlar yenilenmezse `check()` tutmaz → çalışma yok)
PINNED: Mapping[str, str] = MappingProxyType({
    "FUNDING_MARK_V1": "165057d63cf6818464d773916761a6b4a143883a20da1221c15b12de5a80a5dd",
    "ENTRY_UNIVERSE_V1": "f1d5744789fa585e6bc5a059168c9bdd1bb5138902dbacbc90d81139362ee3f6",
    "S1B_DIRECTIONS_V1": "106f15397c60e5f1ebb31d64b0bcea503fbf9c19349cd23e286374dd3f221c29",
    "S1B": "b3254fe69f72c48dda1ec372e6230d8b4a8106bac61043c4f5b88b4db9d08c31",
})


class S1bSealError(RuntimeError):
    """S1b mührü tutmuyor (eksik ya da sha uyuşmazlığı): çekirdek, keşif ve WF çalışmaz."""


def check() -> tuple[bool, str]:
    """(tutuyor mu, neden). Kayıtların ve S1b'nin sha'ları ŞİMDİ yeniden hesaplanır ve sabitlerle karşılaştırılır."""
    now = shas()
    for name, pin in PINNED.items():
        if name == "S1B":
            continue
        if now.get(name) != pin:
            return False, f"S1B_SHA_MISMATCH: {name} {str(now.get(name))[:12]} ≠ sabit {pin[:12]}"
    s1b = RG.sha256_canonical(seal_doc())
    if s1b != PINNED["S1B"]:
        return False, f"S1B_SHA_MISMATCH: S1B {s1b[:12]} ≠ sabit {PINNED['S1B'][:12]} (S1 ya da S1B_SEAL_MS değişti)"
    return True, "OK"


def require() -> None:
    ok, why = check()
    if not ok:
        raise S1bSealError(why)


__all__ = ["ENTRY_UNIVERSE_V1", "ENTRY_UNIVERSE_V1_SHA", "FUNDING_MARK_V1", "FUNDING_MARK_V1_SHA", "PINNED", "REGISTRIES",
           "S1B_DIRECTIONS_V1", "S1B_DIRECTIONS_V1_SHA", "S1B_ID", "S1B_SEAL", "S1B_SEAL_MS", "S1B_SHA", "S1B_VERSIONS",
           "SHAS", "S1bSealError", "check", "require", "seal_doc", "shas"]
