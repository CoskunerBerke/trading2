"""Mühür S1c (P3f, 2026-10-13): S1'e (`registry.py`, `REGISTRY_SHA`, `T_SEAL_MS`) ve S1b'ye (`seal_s1b.py`) DOKUNULMADAN,
ders katmanının (`lessons.py`; docs/ENGINE_BRAIN_V1.md §4.1–§4.4, ana belge §5.7–§5.10) S1'de yazılmayan okunuşlarını AYRI
ADLI, AYRI DOSYALI bir mühürle sabitler (sahip kararı: mühürlü S1 dosyası asla düzenlenmez; yeni okumalar ayrı mühürle).

* `LESSON_READINGS_V1` — S1 `LESSON_REGISTRY_V1` ve `LIB_V1.transforms`'un açık bıraktığı yerlerin okunuşu: kanıt birimi
  ve ölçüleri, hücre anahtarı (bütün düzeyler), ders çiftleri (FİLTRE REAL, IZGARA CF 15 hücre), bakış zamanlaması (çift
  başına gece en çok bir bakış, en küçük bekleyen eşik), keşif anlamlılığı (etki > 0, p ≤ α_k VE q ≤ 0,10), ileri doğrulama
  penceresi (T_k sonrası, T_k ayından sonraki 3 TAM UTC ayı; tek atış), durumlar (gece APPLIED_BOUNDED yazamaz),
  geçersizleşme, dönüşüm eşlemesi + spec biçimi + tekilleştirme, canlı kural denetimi, aylık mühür ve §5.10 rejim
  birleştirmesi. Değerler `lessons.py`'nin KENDİ girdisidir (metin değil): kod bunları okur.
* `S1C_DIRECTIONS_V1` — asla gevşetme: bütün tanım alanları `fixed`; ileri doğrulamanın en az tam ay sayısı `min`
  (`registry.looser_than` makinesi; registry.py'ye dokunmadan).

Kanonik bayt `registry.canonical_bytes`; kayıtlar derin donuk. `S1C_SHA` = {iki sha, `S1C_SEAL_MS`, genişlettiği S1
`REGISTRY_SHA` ve S1b `S1B_SHA`} kanonik sha'sı. `S1C_SEAL_MS` = bu mühür commit'inin işleyen zamanından sonraki tam dakika
(T_SEAL_MS kuralı; test git'ten doğrular). `PINNED` tutmazsa (`check()`) ders katmanı ÇALIŞMAZ (kapalı hata). Ağsız.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from . import registry as RG
from . import seal_s1b as S1B

S1C_ID = "S1c"
S1C_DOC = "docs/ENGINE_BRAIN_V1.md §8.4 (P3f notu, 2026-10-13)"
#: bu mühür commit'inin UTC ms'si (commit'ten sonraki tam dakika; elle geriye alınmaz)
S1C_SEAL_MS = 1791914160000
FIXED, MIN = RG.FIXED, RG.MIN
READING_TR = ("P3f okuması 2026-10-13: S1'in açık bıraktığı yer; sahip kararıyla ayrı adlı S1c mührü (S1/S1b dokunulmadı); "
              "sıkılaştıran ya da nötr, hiçbir mühürlü eşik gevşetilmedi")

#: IZGARA çiftlerinin cfgrid_v1 hücreleri: EX_ANTE, taban (E_ACTUAL) değil, boyut ekseni değil (analysis.RANKED_VARIANTS)
#: ve SKIP hariç (SKIP − E_ACTUAL = −r = FİLTRE'nin CF ikizi; aynı soru iki kez sorulmaz)
GRID_CELLS: tuple[str, ...] = ("E_DELAY1", "E_LIMIT_025", "S_ATR075", "S_ATR100", "S_ATR150", "S_ATR200", "X_T1R", "X_T2R",
                               "X_T3R", "X_TRAIL2ATR", "X_NONE_TIME", "M_TP1_ON", "M_TP1_OFF", "M_BE1R_ON", "M_BE_OFF")

LESSON_READINGS_V1: Mapping[str, Any] = RG.freeze({
    "id": "LESSON_READINGS_V1",
    "extends": ["LESSON_REGISTRY_V1", "LIB_V1.transforms", "STATS_V1.p_day", "CONFIG_EPOCHS_V1"],
    "decision": READING_TR,
    "evidence": {
        "unit": "ATTRIBUTION_ROW",
        "exclude": ["ATTRIBUTION_ERROR", "NO_CONFIG_EPOCH", "NO_NET_R", "STALE_VERSION"],
        "order": "CLOSED_AT_THEN_TRADE_KEY",
        "day": "ENTRY_UTC_DAY",
        "as_of": "CLOSED_AT_LE_NIGHT_AS_OF",
        "real_value": "MINUS_NET_R",
        "cf_value": "CELL_R_CONS_MINUS_E_ACTUAL_R_CONS",
        "cf_rank_src": "cf_aux_v1",
        "requires_s2_complete": True,
        "reading_tr": "kanıt birimi bir atıf satırıdır (kapanmış işlem); hata satırı, config dönemi atanamayan (TRANSITION/"
                      "kapsam dışı) ve net_r'siz satır ile şimdiki attribution_v1/cfgrid_v1 sha'sını taşımayan satır girmez "
                      "(sayılır). REAL = ölçülmüş net R (gerçek maliyet; STATS_V1 p_day'in 'ADVERSE' senaryosu çekirdek "
                      "yeniden oynatımının ölçüsüdür, ders kanıtı ölçülmüştür); CF = cf_aux_v1 muhafazakâr R, AYNI işlemde "
                      "hücre − E_ACTUAL, yalnız rank_src = cf_aux_v1 satırları. Gün = giriş UTC günü (p_day). Atıf "
                      "bekleyen işi (S2 pending) varken bakış yok (eksik kanıtla bakılmaz)",
    },
    "cells": {
        "levels": {"L0": [], "L1": ["T"], "L2": ["T", "I"], "L3": ["T", "X"], "L4": ["T", "I", "X"]},
        "always": ["B", "E", "cohort"],
        "all_levels": True,
        "parent": {"L1": "L0", "L2": "L1", "L3": "L1", "L4": "L2"},
        "bucket_source": ["situation_entry.h4_trend|h4_vol_regime", "regime_fit.bucket"],
        "gold_coins": ["XAU", "PAXG"],
        "reading_tr": "hücre = (düzey, defter, config dönemi, kohort) + düzeyin boyutları; 'veri olan en kaba düzey' her "
                      "düzeyin uygunluk eşiğini geçen hücresinin AYRI bir hücre olduğu biçiminde okunur (aile büyür → BH "
                      "daha sıkı). Kova = situation_v1 4h trend|4h oynaklık (satırın situation_entry'si, yoksa S2'nin "
                      "regime_fit.bucket'ı); bilinmiyorsa L3/L4 yok. HierarchicalRate büzülmesinin ebeveyni L1→L0, L2→L1, "
                      "L3→L1, L4→L2; L0'ın ebeveyni 0,5",
    },
    "pairs": {
        "FILTER": {"evidence": "REAL", "h1": "MEAN_MINUS_NET_R_GT_0"},
        "GRID": {"evidence": "CF", "cells": list(GRID_CELLS), "h1": "MEAN_DELTA_GT_0",
                 "excluded": {"E_ACTUAL": "taban", "SKIP": "FİLTRE'nin CF ikizi", "Z_HALF_LEV": "boyut ekseni (öneri değil)"}},
        "COST": "REPORT_ONLY_CELL_MEAN_COST_R",
        "DISCRIMINATION": "NOT_COMPUTED_V1",
        "mechanism_code": "TOP_LOSS_CODE_OF_SUPPORTING_TRADES",
        "code_view": "DESCRIPTIVE_BOOK_X_CODE",
        "reading_tr": "çift = (hücre, ders türü/EX_ANTE hücresi). AYRIM v1'de hesaplanmaz (WF OOS AUC tanımı yok; bakış ve "
                      "deneme yok). Atıf kodu sonuçtan türer (ileriye bakar): hücre boyutu, bakış ya da aday OLAMAZ — her "
                      "dersin mekanizma kodu (destekleyen işlemlerin en sık kayıp kodu) ve defter × kod betimsel görünümü "
                      "(hüküm yok, deneme yok) olarak bütün defterler için raporlanır",
    },
    "looks": {
        "one_per_pair_per_night": True,
        "order": "LOWEST_PENDING_K",
        "sample": "ALL_EVIDENCE_AS_OF",
        "eligibility_at_look": True,
        "significant": "EFFECT_GT_0_AND_P_LE_ALPHA_K_AND_Q_LE_BH_Q",
        "family": "ALL_LOOKS_OF_THE_NIGHT",
        "cap_order": "N_DESC_CELL_ID_PAIR_ID",
        "p_missing": 1.0,
        "row_id": "LESSON|<pair_id>|g<gen>",
        "ci_quantiles": "floor(a/2*(B-1)), ceil((1-a/2)*(B-1)) of sorted resample means",
        "reading_tr": "bakış k, çiftin kanıt n'i n_k'yı geçmiş, çift uygun (n ≥ 30, ≥ 10 giriş günü) ve k defterde yokken "
                      "olur; gece çift başına EN ÇOK bir bakış (en küçük bekleyen k; birikmiş eşikler sonraki gecelere "
                      "kalır — gecelik üst sınırın 'ertesi geceye kalır' kuralı gibi). Örneklem o gecenin as_of'una kadar "
                      "kapanmış bütün kanıttır. Anlamlı = etki > 0 VE p_day ≤ α_k VE q_BH ≤ 0,10 (alfa bölünmesi ve BH "
                      "birlikte; daha sıkı). BH ailesi = o gecenin BÜTÜN bakışları (keşif + ileri doğrulama); p yoksa 1",
    },
    "forward_validation": {
        "t_k": "AS_OF_OF_DISCOVERY_NIGHT",
        "sample": "SAME_CELL_CLOSED_AFTER_T_K_UP_TO_WINDOW_END",
        "first_complete_months": 3,
        "defer": "NEXT_MONTH_END_IF_NOT_ELIGIBLE",
        "fold": "UTC_MONTH_OF_CLOSED_AT_WITH_TRADES",
        "fold_sign": "FOLD_MEAN_GT_0",
        "look_no": "K_PLUS_1_CAPPED_AT_LAST",
        "row_id": "LESSON_FWD|<pair_id>|g<gen>|h<k>",
        "pass": "MEAN_GT_0_AND_P_LE_ALPHA_AND_FOLDS",
        "sign_flip": "RETIRED",
        "otherwise": "REJECTED",
        "one_shot": True,
        "reading_tr": "≥ 3 aylık katman şartı, doğrulamanın T_k'dan en az 3 ay sonra yapılabileceği demektir; 'hücrenin bir "
                      "sonraki bakışı' bu yüzden TEK atışlık doğrulama bakışıdır: T_k ayından sonraki 3. TAM UTC ayının "
                      "bitişinden sonraki ilk gece, örneklem (T_k, o ayın sonu] içinde kapanan AYNI hücrenin kanıtı; uygun "
                      "değilse (n < 30 ya da < 10 gün) pencere bir sonraki ay sonuna uzar (yalnız örneklem büyüklüğüne "
                      "bağlı, sonuca değil). α = bakış k + 1'in alfası (k = 4 ise son alfa). Geçer = ortalama > 0, p_day ≤ α "
                      "ve ≥ 3 katmanın ≥ 2/3'ünde katman ortalaması > 0; ortalama ≤ 0 → RETIRED (işaret döndü); aksi → "
                      "REJECTED",
    },
    "states": {
        "runner_targets": ["OBSERVATION", "RESEARCH_HYPOTHESIS", "VALIDATED_POLICY_CANDIDATE", "REJECTED", "RETIRED"],
        "runner_forbidden": ["APPLIED_BOUNDED"],
        "vpc_rule": "FWD_VALIDATED_AND_NO_RULE_CHANGE",
        "invalidation": ["ATTRIBUTION_SHA", "CFGRID_SHA", "EVIDENCE_DIGEST"],
        "reading_tr": "ana belge §5.8 (daha sıkı): VALIDATED_POLICY_CANDIDATE = ileri doğrulama geçti VE kural değişikliği "
                      "gerekiyorsa türeyen aday Kapı B'yi geçti (P4). Gece yalnız kural değişikliği GEREKTİRMEYEN (canlıda "
                      "zaten olan) dersi oraya taşır; kural değişikliği isteyen ders ileri doğrulamadan sonra "
                      "RESEARCH_HYPOTHESIS + FWD_PASS kalır ve spec'i kuyruğa girer. APPLIED_BOUNDED'ı gece HİÇ yazamaz. "
                      "Geçersizleşme (sha ya da son bakışın kanıt özeti değişti) → RETIRED, kuşak + 1 → yeni LESSON_LOOK",
    },
    "transforms": {
        "FILTER": {"L2": "T1", "L3": "T4"},
        "GRID": {"S_ATR075": ["T2", 0.75], "S_ATR100": ["T2", 1.0], "S_ATR150": ["T2", 1.5], "S_ATR200": ["T2", 2.0],
                 "X_T1R": ["T3", "X_T1R"], "X_T2R": ["T3", "X_T2R"], "X_T3R": ["T3", "X_T3R"],
                 "X_TRAIL2ATR": ["T3", "X_TRAIL2ATR"], "X_NONE_TIME": ["T3", "X_NONE_TIME"]},
        "cohorts": ["POLICY", "UNTAGGED"],
        "bases": {"strategy_paper": "T2", "strategy_paper_m2": "M2", "strategy_paper_box": "BOX",
                  "strategy_paper_trend4h": "D4", "strategy_paper_candle4h": "C4", "pattern_trader": "FM"},
        "base_variants": {"T2": ["K_T2"], "M2": ["K_M2"], "BOX": ["K_BOX"], "D4": ["K_D4"],
                          "C4": [f"K_C4_CV{i:03d}" for i in range(1, 9)], "FM": ["K_FM"]},
        "scope": {"L0": "POOLED_OWN", "L1": "POOLED_OWN", "L2": "COIN", "L3": "POOLED_OWN", "L4": "COIN"},
        "spec_form": "COVER_FORM",
        "module_src_sha": "sha256(base_module_src_sha|LIB_V1.transforms sha|LESSON_READINGS_V1 sha)",
        "cover_equivalence": {},
        "dedup": ["LIB_V1_SPEC_SHA", "DERIVED_SPECS", "TRIALS_LIB_FAMILY"],
        "reading_tr": "FİLTRE L2 → T1 CELL_FILTER(I=c), L3 → T4 EXCLUDE_REGIME(X) (POOLED_OWN); L0/L1/L4 FİLTRE'sinin "
                      "mühürlü dönüşümü yok. IZGARA S_ATR* → T2 STOP_FLOOR(k), X_* → T3 EXIT_VARIANT(e) (kapsam: L2/L4 "
                      "COIN:c, diğerleri POOLED_OWN; L3/L4'te kova, L1+'da taktik koşulu spec'tedir); E_*/M_* dönüşümsüz. "
                      "Taban = canlı defterin LIB kopyası (LIB kopyası olmayan defter: ders var, dönüşüm yok); yalnız "
                      "taban kuralın kendi girişleri (POLICY/UNTAGGED kohortu) dönüşüm üretir. Spec örtü biçimindedir "
                      "(family O, params {base, factor, factor_params}); örtüyle spec_sha ancak tanım AYNIYSA eşit olur: "
                      "v1'de hiçbir cfgrid_v1 çıkış hücresi/kova bir O faktörüyle aynı tanımlı değil (X_TRAIL2ATR girişten "
                      "iz sürer, X_TRAIL2 +1R'de silahlanır; X_NONE_TIME ufuk sonu, X_TIME max_hold'un yarısı; kova ≠ "
                      "F_ER/F_BTC/F_VOL) → eşdeğerlik tablosu boş; tekilleştirme yine spec_sha ile LIB_v1 (örtüler dahil), "
                      "türeyen spec'ler ve deneme defterinin LIB ailesi üzerinden yapılır",
    },
    "live_rule": {
        "source": "RAW_CONFIG_AND_DECLARED_LIVE_CODE_CONSTANTS",
        "unreadable": "LIVE_CHECK_UNAVAILABLE",
        "T1": {"raw": "strategy_paper.extra[].symbols", "universe_books": ["BOX", "T2", "M2"],
               "universe_raw": "entry_universe.symbols", "unknown": "NOT_LIVE_NOTED"},
        "T2": {"T2": {"raw": "strategy_paper.atr_mult"}, "M2": {"raw": "strategy_paper.extra[m2_tsmom28].atr_mult"},
               "D4": {"code": "donchian_trend.STOP_ATR", "value": 2.0}},
        "T3": {"BOX": {"raw": "strategy_paper.extra[b1_box_fade].rule_params", "exit_kind": "r_multiple",
                       "cells": {"X_T1R": 1.0, "X_T2R": 2.0, "X_T3R": 3.0}}},
        "T4": "NONE_IN_V1_CONFIG",
        "reading_tr": "canlıda zaten olan kural aday olmaz (ham config'e + canlı kuralın bildirilmiş kod sabitine karşı): "
                      "T1 c defterin ham sembol listesinde yoksa (liste yoksa evren defterlerinde entry_universe), T2 canlı "
                      "ATR çarpanı = k ise (D4: kod sabiti 2 × ATR14), T3 Box'ın ham çıkışı r_multiple ve exit_r = hedefse; "
                      "v1 config'inde situation_v1 kova dışlaması yok (T4 hiç canlı değil). Config okunamazsa spec YAZILMAZ "
                      "(kuyrukta kalır, ertesi gece yeniden denenir)",
    },
    "derived_specs": {
        "queue_on": "FWD_PASS",
        "seal_night": "FIRST_NIGHT_DAY_GT_1_WITHOUT_MANIFEST",
        "label": "MONTH_OF_SEAL_NIGHT",
        "order": "Q_ASC_ABS_DELTA_DESC_SPEC_SHA",
        "overflow": "STAYS_QUEUED",
        "t_seal": "AS_OF_FLOOR_MINUTE",
        "manifest": "library/LIB_v1.<YYYY-MM>.json",
        "reading_tr": "ileri doğrulamayı geçen kural değişikliği spec'i derived_specs.jsonl'a PROPOSED yazılır; her ayın "
                      "1'inden sonraki ilk gece (ay için manifest yoksa) kuyruktan en çok 20'si (q artan, |Δ| azalan, "
                      "spec_sha) LIB_v1.<AAAA-AA> olarak mühürlenir: her biri bir SPEC deneme satırı (N_raw_cum + 1; LIB "
                      "ailesinde aynı spec_sha + kapsam ise bağlanır) ve kendi T_seal'ı; sığmayan kuyrukta kalır",
    },
    "sanity_5_10": {
        "book": "strategy_paper_box",
        "rule": "b1_box_fade",
        "param": "learning_mode.books.b1_box_fade.min_stop_pct",
        "anchor_epoch": "E2026-09-28T2120Z",
        "buckets": [["<0.5", 0.0, 0.5], ["0.5-1", 0.5, 1.0], [">=1", 1.0, 1e9]],
        "expected": {"narrow": "<0.5", "wide": "0.5-1", "narrow_mean": "LT_0", "wide_mean": "GT_0",
                     "narrow_codes": ["TIGHT_STOP_COST_MULTIPLIER", "STOP_TOO_TIGHT"]},
        "reference_real": {"<0.5": [72, -0.56], "0.5-1": [176, 0.17]},
        "regime_merge": "CONSECUTIVE_EPOCHS_WHOSE_OPENED_BY_LACKS_PARAM",
        "live_min": 0.5,
        "candidate": "NEVER",
        "reading_tr": "§5.10 tarihsel yeniden üretimdir, aday değil: her config dönemi AYRI raporlanır (dönem sınırını aşan "
                      "karşılaştırma yok) ve aynı min_stop rejiminin dönemleri (sonraki dönemin opened_by'ı parametreyi "
                      "içermiyorsa; TRANSITION da) birleştirilerek okunur (§8.4: mühürlü dosya %0,32 rejimini slot "
                      "değişikliğinde böler). Ham config'te min_stop_pct ≥ 0,5 → uygulanmış karar, yeni rejim yalnız izlenir",
    },
})

#: asla gevşetme: bütün tanım alanları `fixed`; ileri doğrulamanın tam ay sayısı `min`
_DEF_PATHS = ("evidence.unit", "evidence.exclude", "evidence.order", "evidence.day", "evidence.as_of", "evidence.real_value",
              "evidence.cf_value", "evidence.cf_rank_src", "evidence.requires_s2_complete", "cells.levels", "cells.always",
              "cells.all_levels", "cells.parent", "cells.bucket_source", "cells.gold_coins", "pairs.FILTER", "pairs.GRID",
              "pairs.COST", "pairs.DISCRIMINATION", "pairs.mechanism_code", "pairs.code_view",
              "looks.one_per_pair_per_night", "looks.order", "looks.sample", "looks.eligibility_at_look", "looks.significant",
              "looks.family", "looks.cap_order", "looks.p_missing", "looks.row_id", "looks.ci_quantiles", "forward_validation.t_k",
              "forward_validation.sample", "forward_validation.defer", "forward_validation.fold",
              "forward_validation.fold_sign", "forward_validation.look_no", "forward_validation.row_id", "forward_validation.pass",
              "forward_validation.sign_flip", "forward_validation.otherwise", "forward_validation.one_shot",
              "states.runner_targets", "states.runner_forbidden", "states.vpc_rule", "states.invalidation",
              "transforms.FILTER", "transforms.GRID", "transforms.cohorts", "transforms.bases", "transforms.base_variants",
              "transforms.scope", "transforms.spec_form", "transforms.module_src_sha", "transforms.cover_equivalence", "transforms.dedup",
              "live_rule.source", "live_rule.unreadable", "live_rule.T1", "live_rule.T2", "live_rule.T3", "live_rule.T4",
              "derived_specs.queue_on", "derived_specs.seal_night", "derived_specs.label", "derived_specs.order",
              "derived_specs.overflow", "derived_specs.t_seal", "derived_specs.manifest", "sanity_5_10.book", "sanity_5_10.rule", "sanity_5_10.param",
              "sanity_5_10.anchor_epoch", "sanity_5_10.buckets", "sanity_5_10.expected", "sanity_5_10.regime_merge",
              "sanity_5_10.reference_real", "sanity_5_10.live_min", "sanity_5_10.candidate")
S1C_DIRECTIONS_V1: Mapping[str, Any] = RG.freeze({
    "LESSON_READINGS": {**{p: FIXED for p in _DEF_PATHS}, "forward_validation.first_complete_months": MIN},
})

REGISTRIES: Mapping[str, Any] = MappingProxyType({"LESSON_READINGS_V1": LESSON_READINGS_V1,
                                                  "S1C_DIRECTIONS_V1": S1C_DIRECTIONS_V1})
S1C_VERSIONS: Mapping[str, tuple] = MappingProxyType({"LESSON_READINGS": (LESSON_READINGS_V1,)})


def shas(registries: Mapping[str, Any] | None = None) -> dict[str, str]:
    regs = REGISTRIES if registries is None else registries
    return {name: RG.sha256_canonical(reg) for name, reg in regs.items()}


def seal_doc(registries: Mapping[str, Any] | None = None, seal_ms: int | None = None, s1_registry_sha: str | None = None,
             s1b_sha: str | None = None) -> dict[str, Any]:
    """S1c'nin kanonik belgesi: iki sha, `S1C_SEAL_MS`, genişlettiği S1 `REGISTRY_SHA` ve S1b `S1B_SHA` (çağrı anında)."""
    return {"seal_id": S1C_ID, "shas": shas(registries), "s1c_seal_ms": int(S1C_SEAL_MS if seal_ms is None else seal_ms),
            "extends_s1_registry_sha": RG.REGISTRY_SHA if s1_registry_sha is None else s1_registry_sha,
            "extends_s1b_sha": S1B.S1B_SHA if s1b_sha is None else s1b_sha}


SHAS: Mapping[str, str] = MappingProxyType(shas())
LESSON_READINGS_V1_SHA = SHAS["LESSON_READINGS_V1"]
S1C_DIRECTIONS_V1_SHA = SHAS["S1C_DIRECTIONS_V1"]
S1C_SEAL: Mapping[str, Any] = RG.freeze(seal_doc())
S1C_SHA = RG.sha256_canonical(S1C_SEAL)

#: mühür anında sabitlenen değerler (içerik değişip bunlar yenilenmezse `check()` tutmaz → ders katmanı çalışmaz)
PINNED: Mapping[str, str] = MappingProxyType({
    "LESSON_READINGS_V1": "b1d069233fe71472a45912e05f8a1bb2844a2603846b29b2c75d9cb7e01a008f",
    "S1C_DIRECTIONS_V1": "01ef6b0f737b27598e5756a86258ba128042c33db3924a15e3a42d285d03ef3f",
    "S1C": "277922ca4c84b29017e93b05e5e3947bce57b5e890ef8413828f03917a52de95",
})


class S1cSealError(RuntimeError):
    """S1c mührü tutmuyor (eksik ya da sha uyuşmazlığı): ders katmanı çalışmaz."""


def check() -> tuple[bool, str]:
    """(tutuyor mu, neden). Kayıtların, S1b'nin ve S1c'nin sha'ları ŞİMDİ yeniden hesaplanır ve sabitlerle karşılaştırılır."""
    ok, why = S1B.check()
    if not ok:
        return False, f"S1C_EXTENDS_BROKEN_S1B: {why}"
    now = shas()
    for name, pin in PINNED.items():
        if name == "S1C":
            continue
        if now.get(name) != pin:
            return False, f"S1C_SHA_MISMATCH: {name} {str(now.get(name))[:12]} ≠ sabit {pin[:12]}"
    s1c = RG.sha256_canonical(seal_doc())
    if s1c != PINNED["S1C"]:
        return False, f"S1C_SHA_MISMATCH: S1C {s1c[:12]} ≠ sabit {PINNED['S1C'][:12]} (S1, S1b ya da S1C_SEAL_MS değişti)"
    return True, "OK"


def require() -> None:
    ok, why = check()
    if not ok:
        raise S1cSealError(why)


__all__ = ["GRID_CELLS", "LESSON_READINGS_V1", "LESSON_READINGS_V1_SHA", "PINNED", "REGISTRIES", "S1C_DIRECTIONS_V1",
           "S1C_DIRECTIONS_V1_SHA", "S1C_ID", "S1C_SEAL", "S1C_SEAL_MS", "S1C_SHA", "S1C_VERSIONS", "SHAS", "S1cSealError",
           "check", "require", "seal_doc", "shas"]
