# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN `advisor_v1` — SAF ÇEKİRDEK (2026-09-29). Yalnız KAYIT: hiçbir karar/defter/öğrenici DEĞİŞMEZ.

Soru (kullanıcı): "bütün verileri tek bir yere kaydedelim ki, botlarımız bu taktiklere bakarak evet böyle yapmışım ve kar
getirmiş diyip devam etsin veya tam tersi". Bu modül her gerçek giriş (`xp_entry`) ve her karşı-olgusal sinyal (`xp_cf`
rev 0) için şunu hesaplar: *ortak hafıza bu anda ne derdi?* — GİR / GİRME / NÖTR / VERİ AZ. Cevap yalnız sinyalden ÖNCE
kesinleşmiş sonuçlardan (nedensel saat, §4.1) ve ön kayıtlı kurallarla (`ADVISOR_SPEC`, mühür `ADVISOR_SHA`) çıkar.

Bağlayıcı belgeler: `docs/ortak_deneyim/DANISMAN_V1.md` (ön kayıt; çelişkide o geçerli) ve tasarım SPEC_ADVISOR_V1.
Kısa sözleşme:

* **Tek uygulama.** `AdvisorFold` ortak deneyim deposunun YAZIM SIRASI üzerinde deterministik bir sol katlamadır. Canlı
  toplayıcı her toplu yazımı (bir adım = aynı `recorded_at`) katlar; çevrimdışı CLI aynı sınıfla arşiv + sıcak dosyayı
  katlar → aynı hedef için tavsiye çekirdeği (`core_sha`) iki yolda BİREBİR aynıdır.
* **Kanıt kanalları AYRI.** `real` (birincil; gerçek işlemin ilk kesin net sonucu) ve `cf` (ilk `cf_label_v3` net
  etiket). Birincil tavsiyede olsaydı ağırlığı SIFIR; olsaydı kanalı aynı kurallarla ayrıca kaydedilir (`advice_cf`).
* **Saat.** `avail = max(yazım(değer), yazım(bağlam), olay)` — yazım = satırı yazan toplu yazımın MONOTON saati
  (`max(önceki, recorded_at)`), satırın kendi `recorded_at`'i DEĞİL; saati ilerletmeyen (geri adımlı) bir toplu yazımda
  katlanan kanıt, saati İLERLETEN sonraki toplu yazıma kadar BEKLER ve oradan görünür (2026-09-30 inceleme bulgusu: duvar
  saati geri adımı sızıntısı). Olay: gerçek `closed_at`, olsaydı `labeled_at` (`label_ts` ASLA). Kanıt ⇔ `avail < as_of`
  (KESİN). Bütün saatler ISO metinden TAM SAYI ms'ye çevrilir.
* **Hücre / geri çekilme.** `report.backoff_levels` (S0…S5, sonra F0…F5); cevap = n ≥ 30 VE ≥ 5 gün-kümeli İLK seviye.
* **Karar.** Gün-kümeli CR1 standart hata, Student t (C−1): üst uç < 0 GİRME; alt uç > 0 GİR; aksi NÖTR. Bütün toplamlar
  tam sayı mikro-R'dir (sıradan bağımsız).
* **Çekirdek yan etkisiz.** Dosya/ağ YOK; saat okunmaz (zaman yalnız satırlardan gelir). Canlı sarmalayıcı
  `advisor_live.LiveAdvisor`, çevrimdışı değerlendirme `advisor_eval`.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import struct
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

import numpy as np

from ..core import stable_id
from . import KINDS, ROW_SCHEMA
from . import report as XR
from . import rows as R
from . import situation as S

# ============================================================================ kimlik ve etiketler (MÜHÜRLÜ)
ADVISOR_ID = "advisor_v1"
ADVICE_SCHEMA = "shared_experience_advice_v1"
ADVICE_KIND = "xp_advice"
METHOD = "CR1_t_day"
#: Tavsiye satırının etkisi — altın testle sabit: bu satır HİÇBİR ŞEYİ değiştirmez.
EFFECT_NONE = "NONE"
GIR, GIRME, NOTR, VERI_AZ = "GIR", "GIRME", "NOTR", "VERI_AZ"
LABELS = (GIR, NOTR, GIRME, VERI_AZ)
LABEL_TR = {GIR: "GİR", GIRME: "GİRME", NOTR: "NÖTR", VERI_AZ: "VERİ AZ"}
CI_BELOW_0, CI_ABOVE_0, CI_SPANS_0, NO_SUFFICIENT_LEVEL = "CI_BELOW_0", "CI_ABOVE_0", "CI_SPANS_0", "NO_SUFFICIENT_LEVEL"
ST_OK, ST_LATE, ST_NO_GROUP = "OK", "LATE", "NO_GROUP"
TARGET_STATES = (ST_OK, ST_LATE, ST_NO_GROUP)
CH_REAL, CH_CF = "real", "cf"
CHANNELS = (CH_REAL, CH_CF)
PRIMARY_CHANNEL = CH_REAL
CF_WEIGHT_IN_PRIMARY = 0
GROUPS = ("setup", "family")
#: Durum boyutları ve geri çekilme sırası TEK kaynaktan (rapor; test eşitliği korur).
DIMS = XR.DIMS
BACKOFF_ORDER = XR.BACKOFF_ORDER
N_LEVELS = 6
LEVELS_S = tuple("S%d" % i for i in range(N_LEVELS))
LEVELS_F = tuple("F%d" % i for i in range(N_LEVELS))
LEVEL_NAMES = LEVELS_S + LEVELS_F
BASE_LEVELS = ("S5", "F5")
SITU_LEVELS = tuple(x for x in LEVEL_NAMES if x not in BASE_LEVELS)

# ============================================================================ kural sabitleri (MÜHÜRLÜ; KARARLAR 4-5)
MIN_N = 30
MIN_CLUSTERS = 5
SHRINK_K = 20
DISSENT_MIN_N = 10
LEVEL_MEAN_MIN_N = 10
R_QUANT = 1_000_000
R_ABS_MAX = 1_000_000.0
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
TAIL_H_MS = 259_200_000
CTX_TTL_REAL_DAYS = 365
CTX_TTL_CF_DAYS = 60
VAL_WAIT_TTL_DAYS = 7
REAL_EVENT_RETRO_MS = 72 * HOUR_MS
CF_LABEL_VERSIONS = ("cf_label_v3",)
#: Student t, iki yanlı %95 (6 ondalık; sayısal hesap). Ara serbestlikte bir ALTTAKİ tablo değeri (daha büyük t).
T975: dict[int, float] = {
    4: 2.776445, 5: 2.570582, 6: 2.446912, 7: 2.364624, 8: 2.306004, 9: 2.262157, 10: 2.228139, 11: 2.200985,
    12: 2.178813, 13: 2.160369, 14: 2.144787, 15: 2.131450, 16: 2.119905, 17: 2.109816, 18: 2.100922, 19: 2.093024,
    20: 2.085963, 21: 2.079614, 22: 2.073873, 23: 2.068658, 24: 2.063899, 25: 2.059539, 26: 2.055529, 27: 2.051831,
    28: 2.048407, 29: 2.045230, 30: 2.042272, 40: 2.021075, 60: 2.000298, 120: 1.979930}
T975_INF = 1.959964
T975_INF_DF = 10 ** 9
_T975_KEYS = sorted(T975)

#: Seviye başına tutulan boyut dizinleri ve düşürülenler — `report.backoff_levels`ten TÜRETİLİR (tek kaynak).
_BL = XR.backoff_levels({d: "X" for d in DIMS})
if len(_BL) != N_LEVELS:                                     # rapor sırası değişirse sessiz kalmasın
    raise ImportError("advisor: backoff_levels %d seviye verdi (beklenen %d)" % (len(_BL), N_LEVELS))
LEVEL_KEPT: tuple[tuple[int, ...], ...] = tuple(tuple(DIMS.index(d) for d in DIMS if d in lv) for lv, _dr in _BL)
LEVEL_DROPPED: tuple[tuple[str, ...], ...] = tuple(tuple(dr) for _lv, dr in _BL)

#: Karşı-olgusal neden aileleri (kod) — `rows.reason_family` çıktıları.
REASON_FAMILIES = ("CAPACITY", "EXCHANGE", "OCCUPANCY", "PARITY", "GATE", "LIQUIDITY", "OTHER")
BOOK_KEYS = tuple(R.BOOKS)
#: Kohort kodları: karnenin dilimleri + olsaydı.
COHORT_CODES = (R.SC_BEFORE, R.SC_POLICY, R.SC_LEARNING_EXTRA, "other", "cf")
_COH_I = {c: i for i, c in enumerate(COHORT_CODES)}

ADVISOR_SPEC: dict[str, Any] = {
    "advisor_id": ADVISOR_ID, "advice_schema": ADVICE_SCHEMA, "advice_kind": ADVICE_KIND, "method": METHOD,
    "effect": EFFECT_NONE, "labels": dict(LABEL_TR),
    "reasons": {GIRME: CI_BELOW_0, GIR: CI_ABOVE_0, NOTR: CI_SPANS_0, VERI_AZ: NO_SUFFICIENT_LEVEL},
    "states": list(TARGET_STATES),
    "situation_schema_id": S.SCHEMA_ID, "situation_schema_sha": S.SCHEMA_SHA,
    "input": {"row_schema": ROW_SCHEMA, "kinds": list(KINDS),
              "row_valid": "schema == row_schema, kind in kinds, book in rows.BOOKS, int rev >= 0, key present",
              "key": "xp_entry/xp_outcome: trade_key; xp_cf: cf_key; key hash = blake2b-64('R|'+trade_key | 'C|'+cf_key)"},
    "targets": {
        "rows": {"xp_entry": {"key": "trade_key", "as_of": "opened_at"},
                 "xp_cf": {"key": "cf_key", "as_of": "created_at", "rev": 0}},
        "occurrence": "first occurrence of the key in fold order; later duplicates ignored (dup_ignored)",
        "situation_used": "snapshot_status == OK and snapshot.schema_id == situation_v1 and snapshot.schema_sha == "
                          "SCHEMA_SHA; dims = situation.bucket(snapshot, side) for dims; else only S5/F5",
        "late": "as_of unparseable or as_of_ms < batch_clock_ms - tail_h_ms -> LATE (no advice)",
        "no_group": "setup_key and family_key both null -> NO_GROUP (no advice)",
        "tail_h_ms": TAIL_H_MS},
    "evidence": {
        "real": {"value": "first xp_outcome revision (fold order) with final is true, in_net_stats is true, r_basis == NET, "
                          "finite r_net with abs <= r_abs_max, parseable closed_at",
                 "context": "xp_entry of the same trade_key with situation_used and setup_key not null",
                 "event": "closed_at", "clock_anomaly": "closed_at < opened_at -> excluded, counted"},
        "cf": {"value": "first xp_cf revision (fold order) with status LABELLED, r_basis == NET, in_net_stats is true, "
                        "label_version in cf_label_versions, finite r_net with abs <= r_abs_max, parseable labeled_at",
               "context": "xp_cf rev 0 of the same cf_key with situation_used",
               "event": "labeled_at (label_ts never)", "clock_anomaly": "labeled_at < created_at -> excluded, counted",
               "vanish": "SUPERSEDED/EXPIRED/DROPPED/VANISHED close the key (never evidence)",
               "flags_counted": ["a15 = context.baseline_blocked", "approx = value.approx or value.outcome.approx",
                                 "ambiguous = value.intrabar_ambiguous"],
               "not_read": ["thinned", "decision_id"]},
        "cf_label_versions": list(CF_LABEL_VERSIONS), "r_abs_max": R_ABS_MAX,
        "first_qualifying": "the value of a key is fixed by its first qualifying revision; later revisions ignored",
        "origins": "all origins and cohorts", "value_before_context": "waits (val_wait_days) and joins its context"},
    "clock": {"avail": "max(write_ms(value), write_ms(context), event_ms)",
              "write_ms": "batch_clock_ms of the batch that wrote the row (monotone; never the row's own recorded_at)",
              "non_advancing_batch": "evidence ingested in a batch whose recorded_at does not advance the batch clock "
                                     "(<= previous batch_clock or unparseable) is held and ingested at the start of the "
                                     "next batch that advances it, with avail = max(avail, that batch_clock_ms)",
              "visible": "avail_ms < target.as_of_ms (strict)",
              "time_parse": "(datetime - 1970-01-01T00:00:00+00:00) // 1 ms exact integer; naive -> UTC; 'Z' accepted"},
    "cells": {"dims": list(DIMS), "backoff_order": list(BACKOFF_ORDER), "levels": "report.backoff_levels(dims)",
              "level_names": list(LEVEL_NAMES), "group_order": list(GROUPS),
              "setup": "setup_key = book|setup_type|side", "family": "family_key = family|side (null -> no F levels)",
              "unknown_is_category": True},
    "primary_channel": PRIMARY_CHANNEL, "cf_weight_in_primary": CF_WEIGHT_IN_PRIMARY,
    "decision": {"min_n": MIN_N, "min_clusters": MIN_CLUSTERS, "cluster": "utc_day(evidence.as_of_ms)",
                 "r_quant": R_QUANT, "answer": "first level (S0..S5, F0..F5) with n >= min_n and clusters >= min_clusters",
                 "formula": "mean=S/N/1e6; D=Q1*N^2-2*S*N*Q2+S^2*Q3; se=sqrt((C*D)/((C-1)*N^4))/1e6; df=C-1; "
                            "ci=[mean-t*se, mean+t*se]",
                 "t975": {str(k): v for k, v in T975.items()}, "t975_inf": T975_INF, "t975_inf_df": T975_INF_DF,
                 "t_lookup": "exact df, else next lower tabulated df; df >= t975_inf_df -> t975_inf",
                 "table": {GIRME: "ci[1] < 0", GIR: "ci[0] > 0", NOTR: "otherwise at a sufficient level",
                           VERI_AZ: "no sufficient level"},
                 "levels_listed": "every level tried up to and including the answer; mean only if n >= 10"},
    "shrink": {"k": SHRINK_K, "formula": "(n_c*m_c + k*mean)/(n_c + k); n_c == 0 -> mean",
               "dissent": "n_c >= 10 and sign(shrunk) != sign(mean) (0 is its own sign)", "decisive": False},
    "ttl": {"ctx_real_days": CTX_TTL_REAL_DAYS, "ctx_cf_days": CTX_TTL_CF_DAYS, "val_wait_days": VAL_WAIT_TTL_DAYS},
    "fold": {"batch": "maximal run of consecutive rows with equal recorded_at string",
             "batch_clock": "max(previous batch_clock, recorded_at_ms(batch)); non-increasing -> clock_anomaly",
             "order": ["release evidence held by a non-advancing batch (only when this batch advances the clock)",
                       "prune(tail avail < clock - tail_h; ctx past ttl from as_of; value waits past ttl)",
                       "contexts (first occurrence; join waiting values)", "values (first qualifying; vanish closes)",
                       "targets (aggregates minus tail items with avail >= as_of)", "walk-forward tallies"]},
}
ADVISOR_SHA: str = hashlib.sha256(json.dumps(ADVISOR_SPEC, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                                  .encode("ascii")).hexdigest()[:16]

# ============================================================================ saat (tam sayı ms)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)


def ms_exact(x: Any) -> int | None:
    """ISO metin / datetime → epoch ms, TAM SAYI aritmetiğiyle (`(dt − EPOCH) // 1 ms`; float `timestamp()` DEĞİL).
    Saat dilimsiz → UTC; 'Z' kabul; okunamaz / boş → None."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, datetime):
        dt = x
    else:
        s = str(x).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - _EPOCH) // _ONE_MS


def avail_ms(value_rec_ms: int, context_rec_ms: int, event_ms: int) -> int:
    """Görünürlük saati (§4.1): üç terimin en büyüğü. Yazım anları MONOTON toplu yazım saatidir (satırın kendi
    `recorded_at`'i DEĞİL — duvar saati geri adımı bir sonucu sonraki bir hedefe sızdıramaz)."""
    return max(int(value_rec_ms), int(context_rec_ms), int(event_ms))


def key_hash(kind: str, key: str) -> int:
    """Anahtar → deterministik 64-bit işaretsiz tamsayı (`'R|'+trade_key` / `'C|'+cf_key`)."""
    pre = "C|" if kind == R.KIND_CF else "R|"
    return int.from_bytes(hashlib.blake2b((pre + str(key)).encode("utf-8"), digest_size=8).digest(), "little")


# ============================================================================ istatistik
def t975(df: int) -> float | None:
    """Tablo t değeri: tam df; aradaysa bir ALTTAKİ tablo df'si (daha büyük t); df ≥ 10⁹ → ∞. df < 4 → None."""
    if df >= T975_INF_DF:
        return T975_INF
    if df < _T975_KEYS[0]:
        return None
    i = bisect.bisect_right(_T975_KEYS, int(df)) - 1
    return T975[_T975_KEYS[i]]


@dataclass(frozen=True)
class CellStats:
    """Hücrenin TAM SAYI yeter istatistikleri (mikro-R): N, S = Σs_d, Q1 = Σs_d², Q2 = Σn_d·s_d, Q3 = Σn_d², C = #gün."""
    n: int = 0
    s: int = 0
    q1: int = 0
    q2: int = 0
    q3: int = 0
    c: int = 0

    @classmethod
    def from_days(cls, days: Mapping[int, Iterable[float]] | Iterable[tuple[int, Iterable[float]]]) -> "CellStats":
        """{gün: [R, …]} → istatistik (test ve oracle için)."""
        items = days.items() if isinstance(days, Mapping) else days
        n = s = q1 = q2 = q3 = c = 0
        for _d, rs in items:
            nd = sd = 0
            for r in rs:
                nd += 1
                sd += r_micro(r)
            if nd:
                n += nd
                s += sd
                q1 += sd * sd
                q2 += nd * sd
                q3 += nd * nd
                c += 1
        return cls(n, s, q1, q2, q3, c)


def r_micro(r: float) -> int:
    return int(round(float(r) * R_QUANT))


def ci_cr1(st: CellStats) -> tuple[float, float, int, float, float, float]:
    """(ortalama, se, df, t, alt, üst) — CR1 küme-sağlam, Student t (C−1). C ≥ 2 ve tablo df'si (≥ 4) gerekir."""
    n, s, c = st.n, st.s, st.c
    if n <= 0 or c < 2:
        raise ValueError("ci_cr1: n > 0 ve C >= 2 gerekir")
    mean = s / n / 1e6
    d = st.q1 * n * n - 2 * s * n * st.q2 + s * s * st.q3
    se = math.sqrt((c * d) / ((c - 1) * n ** 4)) / 1e6
    df = c - 1
    t = t975(df)
    if t is None:
        raise ValueError("ci_cr1: df %d tabloda yok (C >= 5 gerekir)" % df)
    h = t * se
    return mean, se, df, t, mean - h, mean + h


def label_for_ci(lo: float, hi: float) -> tuple[str, str]:
    """Karar tablosu (yeterli seviyede): üst uç < 0 → GİRME; alt uç > 0 → GİR; aksi NÖTR (sınırlar KESİN)."""
    if hi < 0:
        return GIRME, CI_BELOW_0
    if lo > 0:
        return GIR, CI_ABOVE_0
    return NOTR, CI_SPANS_0


def sufficient(n: int, c: int) -> bool:
    """Yeterli seviye: n ≥ 30 VE ≥ 5 gün-kümesi."""
    return n >= MIN_N and c >= MIN_CLUSTERS


def decide(st: CellStats | None) -> tuple[str, str]:
    """(etiket, neden). None ya da yetersiz → VERİ AZ."""
    if st is None or not sufficient(st.n, st.c):
        return VERI_AZ, NO_SUFFICIENT_LEVEL
    _m, _se, _df, _t, lo, hi = ci_cr1(st)
    return label_for_ci(lo, hi)


def shrunk(n_c: int, s_c: int, n: int, s: int, k: int = SHRINK_K) -> tuple[float, int]:
    """(büzülmüş ortalama R, işaret) — TAM SAYI: (s_c·N + k·S) / (N·(n_c + k)) / 1e6. n_c = 0 → havuz ortalaması."""
    num = s_c * n + k * s
    den = n * (n_c + k)
    return num / den / 1e6, (num > 0) - (num < 0)


def _r6(x: float | None) -> float | None:
    if x is None:
        return None
    v = round(float(x), 6)
    return v + 0.0                                           # −0.0 → 0.0


def core_sha(row: Mapping[str, Any]) -> str:
    """Tavsiye çekirdeğinin özeti: `core_sha`, `recorded_at` ve `meta` DIŞINDAKİ her alanın kanonik JSON'u."""
    core = {k: v for k, v in row.items() if k not in ("core_sha", "recorded_at", "meta")}
    txt = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(txt.encode("ascii")).hexdigest()[:16]


def advice_row_id(target_kind: str, target_key: str) -> str:
    return stable_id("adv", ADVISOR_SHA, target_kind, target_key)


def level_class(level: str | None) -> str | None:
    """SITU (S0–S4, F0–F4) | BASE (S5, F5) | None (VERİ AZ)."""
    if not level:
        return None
    return "BASE" if level in BASE_LEVELS else "SITU"


def situation_used(row: Mapping[str, Any]) -> bool:
    snap = row.get("snapshot")
    return (row.get("snapshot_status") == "OK" and isinstance(snap, Mapping) and snap.get("schema_id") == S.SCHEMA_ID
            and snap.get("schema_sha") == S.SCHEMA_SHA)


def target_dims(row: Mapping[str, Any]) -> dict[str, str] | None:
    """Hedefin durum boyutları (`situation.bucket`) — anlık görüntü kullanılamıyorsa None."""
    if not situation_used(row):
        return None
    b = S.bucket(row.get("snapshot"), str(row.get("side") or ""))
    return {d: b[d] for d in DIMS}


def levels_for(dims: Mapping[str, str] | None, *, setup_key: Any, family_key: Any) -> list[tuple[str, str, dict, list]]:
    """[(seviye adı, grup, tutulan boyutlar, düşürülenler)] — `report.backoff_levels` ile; durum yoksa yalnız S5/F5."""
    if dims is not None:
        bl = [(dict(d), list(dr)) for d, dr in XR.backoff_levels(dims)]
        idx = list(range(len(bl)))
    else:
        bl = [({}, list(LEVEL_DROPPED[N_LEVELS - 1]))]
        idx = [N_LEVELS - 1]
    out: list[tuple[str, str, dict, list]] = []
    for grp, gk, pre in (("setup", setup_key, "S"), ("family", family_key, "F")):
        if not gk:
            continue
        for i, (d, dr) in zip(idx, bl):
            out.append(("%s%d" % (pre, i), grp, d, dr))
    return out


# ============================================================================ katlama durumu
#: Bağlam kaydı (anahtar başına; paketli): kanal, kanıt uygun, durum kullanıldı, 5 boyut kodu, A15, köken canlı,
#: hedef durumu, hedef bayrakları, etiketler (gerçek/olsaydı), seviyeler, defter, kohort, neden ailesi, kanıt durumu;
#: as_of, bağlam yazım anı, hedef saati, hedefin kendi sonucu (mikro-R); kurulum/aile/coin kimlikleri.
_CTX = struct.Struct("<20Bqqqqiii")
(_C_CH, _C_EVOK, _C_SIT, _C_D0, _C_D1, _C_D2, _C_D3, _C_D4, _C_A15, _C_LIVE, _C_TSTATE, _C_TFLAGS, _C_LABR, _C_LABC,
 _C_LVLR, _C_LVLC, _C_BOOK, _C_COH, _C_RFAM, _C_EST, _C_ASOF, _C_REC, _C_TCLOCK, _C_Y, _C_SETUP, _C_FAM, _C_COIN) = range(27)
_NONE8 = 255
_EST_OPEN, _EST_OK, _EST_CLOSED, _EST_ANOMALY = 0, 1, 2, 3
_TF_PROSP, _TF_ANOM = 1, 2
#: Kanıt öğesi bayrakları.
_F_LIVE, _F_A15, _F_APX, _F_AMB = 1, 2, 4, 8
_ST_CODE = {ST_OK: 0, ST_LATE: 1, ST_NO_GROUP: 2}
_LAB_CODE = {lab: i for i, lab in enumerate(LABELS)}
_LVL_CODE = {lv: i for i, lv in enumerate(LEVEL_NAMES)}
_RF_CODE = {rf: i for i, rf in enumerate(REASON_FAMILIES)}
_BOOK_CODE = {b: i for i, b in enumerate(BOOK_KEYS)}
#: Hücre sütunları (hücre dizini başına int64): N, S = Σ mikro-R, C = gün-kümesi sayısı, canlı köken, A15, approx, belirsiz.
_COLS = ("n", "s", "c", "nl", "na", "np", "nb")
#: Hücre tablosu (hücre başına TEK `array('q')`): [gün sayısı, (gün<<40 | n, s) × gün, (coin<<40 | n, s) × coin] — gün ve
#: coin girdileri artan anahtarla sıralı (16 bayt/girdi). Q1..Q3 SAKLANMAZ: yalnız cevap seviyesinde tablodan TAM
#: sayılarla hesaplanır (seviye listesi yalnız n, C ve ortalamayı ister).
_KSH = 40
_KMASK = (1 << _KSH) - 1
_DONE_MERGE = 50_000
_COIN_BITS = 20
#: Durum kovası dosyası (anlık görüntü) biçimi.
STATE_MAGIC = b"XPADV1\n"
STATE_SCHEMA = "shared_experience_advisor_state_v1"
_RESOLVED_COLS = (("kh", "Q"), ("as_of", "q"), ("day", "q"), ("res_clock", "q"), ("t_clock", "q"), ("book", "b"), ("fam", "q"),
                  ("coh", "b"), ("kind", "b"), ("lab_r", "b"), ("lab_c", "b"), ("lvl_r", "b"), ("lvl_c", "b"),
                  ("y", "q"), ("flags", "b"), ("rfam", "b"))
#: Çözülmüş hedef bayrakları (CLI dizileri).
RF_LIVE, RF_PROSP, RF_OK, RF_ANOM, RF_A15 = 1, 2, 4, 8, 16
_COUNTERS = ("batches", "rows", "rows_invalid", "targets", "targets_ok", "targets_late", "targets_no_group",
             "evidence_real", "evidence_cf", "evidence_ineligible", "clock_anomaly", "clock_nonincreasing",
             "dup_ignored", "ctx_expired", "val_wait_expired", "orphan_values", "vanished", "resolved",
             "resolved_excluded", "as_of_ms_mismatch", "r_out_of_range", "tail_pruned", "clock_held_evidence")


def _cell_key(ch: int, grp: int, level: int, gk: int, dcodes: tuple[int, ...]) -> int:
    code = 0
    for i in LEVEL_KEPT[level]:
        code |= dcodes[i] << (5 * i)
    return (((((ch << 1) | grp) << 3 | level) << 24 | gk) << 25) | code


def _tab_find(t: array, base: int, m: int, key: int) -> tuple[int, bool]:
    """Hücre tablosunda `base`ten başlayan m girdilik bölümde (anahtar<<40 | n, s) `key` konumu — sondan hızlı yol,
    sonra ikili arama. Dönüş: (girdi sırası, bulundu mu)."""
    if m == 0:
        return 0, False
    last = t[base + 2 * (m - 1)] >> _KSH
    if last == key:
        return m - 1, True
    if last < key:
        return m, False
    lo, hi = 0, m
    while lo < hi:
        mid = (lo + hi) >> 1
        if (t[base + 2 * mid] >> _KSH) < key:
            lo = mid + 1
        else:
            hi = mid
    return lo, (lo < m and (t[base + 2 * lo] >> _KSH) == key)


@dataclass
class FoldResult:
    """Bir toplu yazımın katlama sonucu: tavsiye çekirdekleri (yazım zamanı ve `meta` HARİÇ), çözülen hedef sayısı.
    `first_target_seq`: ilk tavsiye satırının hedef sıra numarası (katlamanın `targets` sayacı; satırlar ardışık) —
    canlı sarmalayıcının yüksek su işareti (yeniden kurulumda zaten yazılmış hedefler yeniden yazılmaz)."""
    advice_rows: list[dict[str, Any]] = field(default_factory=list)
    resolved: int = 0
    batch_clock_ms: int | None = None
    clock_anomaly: bool = False
    rows: int = 0
    first_target_seq: int | None = None


class AdvisorFold:
    """Deterministik sol katlama (canlı ve çevrimdışı TEK uygulama). Durum yalnız girdiden türer (duvar saati yok).

    `born_ms`: danışmanın canlı doğumu (`advisor_meta.json`); None → hiçbir hedef ileriye dönük değil.
    `collect=True` (yalnız CLI): çözülen hedef dizileri (`resolved_arrays`) tutulur.
    `clock="event"` (yalnız CLI keşif modu, §4.7): kanıt saati olay anı (+72 sa gerçek); hedefler ertelenir
    (`deferred`) — tavsiye `advisor_eval` ikinci geçişte süpürme ile hesaplanır."""

    def __init__(self, *, born_ms: int | None = None, collect: bool = False, clock: str = "avail") -> None:
        if clock not in ("avail", "event"):
            raise ValueError("clock avail|event olmalı")
        self.born_ms = int(born_ms) if born_ms is not None else None
        self.collect = bool(collect)
        self.clock_mode = clock
        self._gk: dict[str, int] = {}
        self._gk_list: list[str] = []
        self._dv: list[dict[str, int]] = [{} for _ in DIMS]
        self._dv_list: list[list[str]] = [[] for _ in DIMS]
        self._coin: dict[str, int] = {"": 0}
        self._coin_list: list[str] = [""]
        self._cidx: dict[int, int] = {}
        self._ck = array("q")
        self._col: dict[str, array] = {k: array("q") for k in _COLS}
        self._tab: list[array] = []
        self._days_total = 0
        self._coins_total = 0
        self._tail_av: list[list[int]] = [[], []]
        self._tail_it: list[list[tuple]] = [[], []]
        self._ev_total = [0, 0]
        self._ctx: dict[int, bytes] = {}
        self._vw: dict[int, tuple] = {}
        self._ctx_exp: dict[int, list[int]] = {}
        self._vw_exp: dict[int, list[int]] = {}
        self._done = np.zeros(0, dtype=np.uint64)
        self._done_recent: set[int] = set()
        self.batch_clock: int | None = None
        self.counters: dict[str, int] = {k: 0 for k in _COUNTERS}
        self._ring: dict[int, dict[str, int]] = {}
        self._tally: dict[str, list[int]] = {}
        self.resolved_cols: dict[str, array] | None = ({k: array(t) for k, t in _RESOLVED_COLS} if self.collect else None)
        #: keşif (olay saati) modu: ertelenmiş kanıt ve hedefler
        self.deferred_evidence: list[tuple] = []
        self.deferred_targets: list[tuple] = []
        #: hedef evresinin paylaşılan kuyruk deltaları (yalnız `fold_batch` 4. evresinde dolu; durumun parçası DEĞİL)
        self._bsub: dict | None = None
        #: (2026-09-30) saati ilerletmeyen toplu yazımda katlanan kanıt: (kanal, avail, anahtar, hücreler, gün, coin, R,
        #: bayraklar) — saati ilerleten sonraki toplu yazımın BAŞINDA hücrelere girer (durumun parçası; anlık görüntüde)
        self._held: list[tuple] = []
        self._batch_held = False

    # ------------------------------------------------------------------ interning
    def _gk_id(self, s: Any) -> int:
        if not s:
            return -1
        k = str(s)
        i = self._gk.get(k)
        if i is None:
            i = len(self._gk_list)
            if i >= (1 << 24):
                raise OverflowError("advisor: grup anahtarı tablosu doldu")
            self._gk[k] = i
            self._gk_list.append(k)
        return i

    def gk_str(self, i: int) -> str | None:
        return self._gk_list[i] if 0 <= i < len(self._gk_list) else None

    def _dim_code(self, di: int, v: str) -> int:
        tab = self._dv[di]
        c = tab.get(v)
        if c is None:
            c = len(self._dv_list[di]) + 1
            if c > 31:
                c = 31                                       # taşma: ortak "diğer" kodu (deterministik; pratikte olmaz)
            else:
                tab[v] = c
                self._dv_list[di].append(v)
        return c

    def _coin_id(self, symbol: Any) -> int:
        k = XR.coin_of(symbol) or ""
        i = self._coin.get(k)
        if i is None:
            i = len(self._coin_list)
            if i >= (1 << _COIN_BITS):
                raise OverflowError("advisor: coin tablosu doldu")
            self._coin[k] = i
            self._coin_list.append(k)
        return i

    # ------------------------------------------------------------------ tamamlanan anahtar kümesi
    def is_done(self, kh: int) -> bool:
        if kh in self._done_recent:
            return True
        arr = self._done
        if arr.size == 0:
            return False
        v = np.uint64(kh)
        i = int(np.searchsorted(arr, v))
        return i < arr.size and arr[i] == v

    def _mark_done(self, kh: int) -> None:
        recent: set[int] = self._done_recent
        recent.add(kh)
        if len(recent) >= _DONE_MERGE:
            self._merge_done()

    def _merge_done(self) -> None:
        if self._done_recent:
            extra = np.fromiter(self._done_recent, dtype=np.uint64, count=len(self._done_recent))
            self._done = np.union1d(self._done, extra).astype(np.uint64)
            self._done_recent = set()

    # ------------------------------------------------------------------ hücreler
    def _ingest(self, ch: int, avail: int, kh: int, cells: tuple[int, ...], day: int, coin: int, r: int, fl: int,
                *, tail: bool = True) -> None:
        """Kanıt öğesini hücrelere (tam sayı gün ve coin tabloları, sütunlar) ve kuyruğa ekler (`tail=False`: yalnız
        olay-saati süpürmesi)."""
        cidx, tabs = self._cidx, self._tab
        cn, cs, cc = self._col["n"], self._col["s"], self._col["c"]
        dk, ck_ = (day << _KSH) | 1, (coin << _KSH) | 1
        for ck in cells:
            i = cidx.get(ck)
            if i is None:
                i = self._new_cell(ck)
            t = tabs[i]
            nd = t[0]
            j, found = _tab_find(t, 1, nd, day)
            p = 1 + 2 * j
            if found:
                t[p] += 1
                t[p + 1] += r
            else:
                t[p:p] = array("q", (dk, r))
                t[0] = nd = nd + 1
                cc[i] += 1
                self._days_total += 1
            base = 1 + 2 * nd
            j, found = _tab_find(t, base, (len(t) - base) >> 1, coin)
            p = base + 2 * j
            if found:
                t[p] += 1
                t[p + 1] += r
            else:
                t[p:p] = array("q", (ck_, r))
                self._coins_total += 1
            cn[i] += 1
            cs[i] += r
            if fl & (_F_LIVE | _F_A15 | _F_APX | _F_AMB):
                if fl & _F_LIVE:
                    self._col["nl"][i] += 1
                if fl & _F_A15:
                    self._col["na"][i] += 1
                if fl & _F_APX:
                    self._col["np"][i] += 1
                if fl & _F_AMB:
                    self._col["nb"][i] += 1
        self._ev_total[ch] += 1
        if not tail:
            return
        av, it = self._tail_av[ch], self._tail_it[ch]
        item = (avail, day, coin, r, fl, kh, cells)
        if not av or av[-1] <= avail:
            av.append(avail)
            it.append(item)
        else:
            j = bisect.bisect_right(av, avail)
            av.insert(j, avail)
            it.insert(j, item)

    def _evidence_cells(self, ch: int, setup: int, fam: int, dcodes: tuple[int, ...]) -> tuple[int, ...]:
        out: list[int] = []
        for grp, gk in ((0, setup), (1, fam)):
            if gk < 0:
                continue
            for lv in range(N_LEVELS):
                out.append(_cell_key(ch, grp, lv, gk, dcodes))
        return tuple(out)

    def _target_levels(self, ch: int, sit: bool, setup: int, fam: int, dcodes: tuple[int, ...]) -> list[tuple]:
        """[(ad, grup kodu, seviye, hücre anahtarı, grup anahtarı kimliği)]."""
        out: list[tuple] = []
        lvls = range(N_LEVELS) if sit else (N_LEVELS - 1,)
        for grp, gk, pre in ((0, setup, "S"), (1, fam, "F")):
            if gk < 0:
                continue
            for lv in lvls:
                out.append(("%s%d" % (pre, lv), grp, lv, _cell_key(ch, grp, lv, gk, dcodes), gk))
        return out

    def _new_cell(self, ck: int) -> int:
        i = len(self._ck)
        self._cidx[ck] = i
        self._ck.append(ck)
        for col in self._col.values():
            col.append(0)
        self._tab.append(array("q", (0,)))
        return i

    def _cell_nsc(self, i: int, dd: dict[int, list[int]] | None) -> tuple[int, int, int]:
        """Hücrenin (N, S, C)'si, kuyruktan çıkarılacak gün deltaları DÜŞÜLMÜŞ (kopya üzerinde)."""
        if i < 0:
            return 0, 0, 0
        n, s, c = self._col["n"][i], self._col["s"][i], self._col["c"][i]
        if dd:
            t = self._tab[i]
            nd = t[0]
            for day, (dn, ds) in dd.items():
                j, found = _tab_find(t, 1, nd, day)
                if found and (t[1 + 2 * j] & _KMASK) == dn:
                    c -= 1
                n -= dn
                s -= ds
        return n, s, c

    def _cell_q(self, i: int, dd: dict[int, list[int]] | None) -> tuple[int, int, int]:
        """(Q1, Q2, Q3) = (Σ s_d², Σ n_d·s_d, Σ n_d²) gün tablosundan TAM sayılarla, deltalar düşülmüş."""
        t = self._tab[i]
        nd = t[0]
        q1 = q2 = q3 = 0
        dd = dd or {}
        for k in range(nd):
            v = t[1 + 2 * k]
            n, sd = v & _KMASK, t[2 + 2 * k]
            x = dd.get(v >> _KSH)
            if x is not None:
                n -= x[0]
                sd -= x[1]
            q1 += sd * sd
            q2 += n * sd
            q3 += n * n
        return q1, q2, q3

    def _cell_coin(self, i: int, coin: int) -> tuple[int, int]:
        t = self._tab[i]
        base = 1 + 2 * t[0]
        j, found = _tab_find(t, base, (len(t) - base) >> 1, coin)
        if not found:
            return 0, 0
        p = base + 2 * j
        return t[p] & _KMASK, t[p + 1]

    # ------------------------------------------------------------------ satır ayrıştırma
    @staticmethod
    def _valid(row: Any) -> tuple[str, str] | None:
        """(tür, anahtar) ya da None (geçersiz)."""
        if not isinstance(row, Mapping) or row.get("schema") != ROW_SCHEMA:
            return None
        kind = row.get("kind")
        if kind not in KINDS or row.get("book") not in R.BOOKS:
            return None
        rev = row.get("rev")
        if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
            return None
        key = row.get("cf_key") if kind == R.KIND_CF else row.get("trade_key")
        if not isinstance(key, str) or not key:
            return None
        return kind, key

    @staticmethod
    def _finite_r(row: Mapping[str, Any]) -> float | None:
        v = row.get("r_net")
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        f = float(v)
        return f if math.isfinite(f) else None

    def _value_of(self, row: Mapping[str, Any], kind: str, rec_ms: int) -> tuple | None:
        """Değer adayı: ("V", değer yazım ms, olay ms, mikro-R, bayraklar) | ("X",) kaybolma | None (aday değil).
        `rec_ms` = toplu yazımın MONOTON saati (`fold_batch`)."""
        if kind == R.KIND_OUTCOME:
            if row.get("final") is not True or row.get("in_net_stats") is not True or row.get("r_basis") != R.NET:
                return None
            r = self._finite_r(row)
            ev = ms_exact(row.get("closed_at"))
            if r is None or ev is None:
                return None
            if abs(r) > R_ABS_MAX:
                self.counters["r_out_of_range"] += 1
                return None
            if self.clock_mode == "event":
                ev += REAL_EVENT_RETRO_MS
            return ("V", rec_ms, ev, r_micro(r), 0)
        if kind != R.KIND_CF:
            return None
        st = row.get("status")
        if st in R.VANISHED_STATUSES:
            return ("X",)
        if (st != R.LABELLED or row.get("r_basis") != R.NET or row.get("in_net_stats") is not True
                or row.get("label_version") not in CF_LABEL_VERSIONS):
            return None
        r = self._finite_r(row)
        ev = ms_exact(row.get("labeled_at"))
        if r is None or ev is None:
            return None
        if abs(r) > R_ABS_MAX:
            self.counters["r_out_of_range"] += 1
            return None
        out = row.get("outcome")
        apx = row.get("approx") is True or (isinstance(out, Mapping) and out.get("approx") is True)
        fl = (_F_APX if apx else 0) | (_F_AMB if row.get("intrabar_ambiguous") is True else 0)
        return ("V", rec_ms, ev, r_micro(r), fl)

    def _make_ctx(self, row: Mapping[str, Any], kind: str, rec_ms: int) -> tuple[list, dict[str, str] | None]:
        ch = 1 if kind == R.KIND_CF else 0
        as_of = ms_exact(row.get("created_at") if ch else row.get("opened_at"))
        raw = row.get("as_of_ms")
        if as_of is not None and isinstance(raw, int) and not isinstance(raw, bool) and raw != as_of:
            self.counters["as_of_ms_mismatch"] += 1              # sayılır, ASLA kullanılmaz
        dims = target_dims(row)
        sit = dims is not None
        dcodes = tuple(self._dim_code(i, dims[d]) for i, d in enumerate(DIMS)) if sit else (0, 0, 0, 0, 0)
        setup = self._gk_id(row.get("setup_key"))
        fam = self._gk_id(row.get("family_key"))
        evok = 1 if (sit and as_of is not None and (setup >= 0 if ch == 0 else True)) else 0
        coh = "cf" if ch else (row.get("scorecard_class") if row.get("scorecard_class") in COHORT_CODES[:3] else "other")
        rf = row.get("reason_family") if ch else None
        f = [ch, evok, 1 if sit else 0, *dcodes, 1 if (ch and row.get("baseline_blocked") is True) else 0,
             1 if row.get("origin") == R.LIVE else 0, _NONE8, 0, _NONE8, _NONE8, _NONE8, _NONE8,
             _BOOK_CODE.get(str(row.get("book")), _NONE8), _COH_I[coh], _RF_CODE.get(rf, _NONE8) if rf else _NONE8,
             _EST_OPEN, as_of if as_of is not None else -1, int(rec_ms), -1, 0, setup, fam, self._coin_id(row.get("symbol"))]
        return f, dims

    # ------------------------------------------------------------------ toplu yazım katlaması
    def fold_rows(self, rows: Iterable[Mapping[str, Any]]) -> FoldResult:
        """Satırları `recorded_at` koşularına böler ve her koşuyu bir toplu yazım olarak katlar (sonuçlar birleşir)."""
        out = FoldResult()
        run: list[Mapping[str, Any]] = []
        cur = object()
        for r in rows:
            ra = r.get("recorded_at") if isinstance(r, Mapping) else None
            if run and ra != cur:
                self._merge(out, self.fold_batch(run))
                run = []
            run.append(r)
            cur = ra
        if run:
            self._merge(out, self.fold_batch(run))
        return out

    @staticmethod
    def _merge(a: FoldResult, b: FoldResult) -> None:
        if not a.advice_rows and b.advice_rows:
            a.first_target_seq = b.first_target_seq
        a.advice_rows.extend(b.advice_rows)
        a.resolved += b.resolved
        a.batch_clock_ms = b.batch_clock_ms
        a.clock_anomaly = a.clock_anomaly or b.clock_anomaly
        a.rows += b.rows

    def fold_batch(self, rows: list[Mapping[str, Any]]) -> FoldResult:
        """BİR toplu yazım (aynı `recorded_at`): budama → bağlamlar → değerler → hedefler → çözümler."""
        res = FoldResult(rows=len(rows))
        cnt = self.counters
        cnt["batches"] += 1
        cnt["rows"] += len(rows)
        ra = next((r.get("recorded_at") for r in rows if isinstance(r, Mapping)), None)
        rec_ms = ms_exact(ra)
        prev = self.batch_clock
        anomaly = False
        if prev is not None and (rec_ms is None or rec_ms <= prev):
            anomaly = True
            cnt["clock_nonincreasing"] += 1
        if rec_ms is None:
            rec_ms = prev if prev is not None else 0
        clock = rec_ms if prev is None else max(prev, rec_ms)
        # (2026-09-30, inceleme bulgusu) satırların YAZIM ANI bu MONOTON saattir, kendi `recorded_at`'leri DEĞİL: duvar
        # saati geri adım atarsa (yeniden başlatma, NTP) geriye damgalı bir toplu yazımdaki sonuç, saat düzeldikten sonra
        # yazılan ve adımdan ÖNCE açılmış bir hedefe kanıt olamaz. Saati İLERLETMEYEN toplu yazımın gerçek yazım anı
        # bilinmez (yalnız öncekinden sonra olduğu bilinir): orada katlanan kanıt BEKLER ve saati ilerleten sonraki toplu
        # yazımın saatiyle (dosya sırasında ondan önce yazıldı) görünür olur.
        self._batch_held = anomaly and self.clock_mode == "avail"
        if not anomaly and self._held:
            self._release_held(clock)
        self.batch_clock = clock
        res.batch_clock_ms = clock
        res.clock_anomaly = anomaly
        self._prune(clock)
        valid: list[tuple[Mapping[str, Any], str, int]] = []
        for r in rows:
            v = self._valid(r)
            if v is None:
                cnt["rows_invalid"] += 1
                continue
            valid.append((r, v[0], key_hash(v[0], v[1])))
        # 2) bağlamlar (ilk görülme) + bekleyen değerlerin birleşmesi
        new_targets: list[tuple[int, Mapping[str, Any], dict | None]] = []
        for r, kind, kh in valid:
            if kind == R.KIND_ENTRY or (kind == R.KIND_CF and r.get("rev") == 0):
                if kh in self._ctx or self.is_done(kh):
                    cnt["dup_ignored"] += 1
                    continue
                f, dims = self._make_ctx(r, kind, clock)
                self._ctx[kh] = _CTX.pack(*f)
                exp = (f[_C_ASOF] if f[_C_ASOF] >= 0 else f[_C_REC]) + (CTX_TTL_CF_DAYS if f[_C_CH] else
                                                                           CTX_TTL_REAL_DAYS) * DAY_MS
                self._ctx_exp.setdefault(exp // HOUR_MS, []).append(kh)
                new_targets.append((kh, r, dims))
                w = self._vw.pop(kh, None)
                if w is not None:
                    self._consume(kh, w[0])
        # 3) değerler (anahtar başına ilk nitelikli revizyon; kaybolma anahtarı kapatır)
        for r, kind, kh in valid:
            if kind == R.KIND_ENTRY:
                continue
            v = self._value_of(r, kind, clock)
            if v is None:
                continue
            if self.is_done(kh):
                cnt["dup_ignored"] += 1
                continue
            raw = self._ctx.get(kh)
            if raw is None:
                if kh in self._vw:
                    cnt["dup_ignored"] += 1
                    continue
                self._vw[kh] = (v, clock)
                self._vw_exp.setdefault((clock + VAL_WAIT_TTL_DAYS * DAY_MS) // HOUR_MS, []).append(kh)
                cnt["orphan_values"] += 1
                continue
            if raw[_C_EST] != _EST_OPEN:
                cnt["dup_ignored"] += 1
                continue
            self._consume(kh, v)
            if raw[_C_TSTATE] != _NONE8:
                res.resolved += self._resolve(kh, clock)
        # 4) hedefler (toplamlar − kuyruktaki avail ≥ as_of öğeleri). Bu evrede kuyruk DEĞİŞMEZ: `avail ≥ clock`
        # öğelerinin hücre deltaları toplu yazım başına bir kez toplanır (`_batch_sub`), hedefler paylaşır.
        self._bsub = {"pivot": clock}
        seq0 = cnt["targets"] + 1
        try:
            for kh, r, dims in new_targets:
                adv = self._target(kh, r, dims, clock, anomaly)
                if adv is not None:
                    res.advice_rows.append(adv)
                raw = self._ctx.get(kh)
                if raw is not None and raw[_C_EST] != _EST_OPEN:
                    res.resolved += self._resolve(kh, clock)
        finally:
            self._bsub = None
        if res.advice_rows:
            res.first_target_seq = seq0
        return res

    def _batch_sub(self, ch: int) -> tuple[int, dict[int, tuple]]:
        """(pivot dizini, {hücre: (gün deltaları, bayrak sayıları, coin deltaları)}) — kanal `ch` kuyruğunun
        `avail ≥ pivot` (= toplu yazım saati) öğeleri; yalnız hedef evresinde, kanal başına bir kez kurulur."""
        bs = self._bsub
        got = bs.get(ch)
        if got is None:
            av, items = self._tail_av[ch], self._tail_it[ch]
            p = bisect.bisect_left(av, bs["pivot"])
            agg: dict[int, tuple] = {}
            for j in range(p, len(av)):
                _a, day, icoin, r, fl, _kh, cells = items[j]
                for ck in cells:
                    a = agg.get(ck)
                    if a is None:
                        a = agg[ck] = ({}, [0, 0, 0, 0], {})
                    dd, cc, co = a
                    x = dd.get(day)
                    if x is None:
                        dd[day] = [1, r]
                    else:
                        x[0] += 1
                        x[1] += r
                    cc[0] += 1 if fl & _F_LIVE else 0
                    cc[1] += 1 if fl & _F_A15 else 0
                    cc[2] += 1 if fl & _F_APX else 0
                    cc[3] += 1 if fl & _F_AMB else 0
                    y = co.get(icoin)
                    if y is None:
                        co[icoin] = [1, r]
                    else:
                        y[0] += 1
                        y[1] += r
            got = bs[ch] = (p, agg)
        return got

    # ------------------------------------------------------------------ budama
    def _prune(self, clock: int) -> None:
        cut = clock - TAIL_H_MS
        for ch in (0, 1):
            av = self._tail_av[ch]
            if av and av[0] < cut:
                j = bisect.bisect_left(av, cut)
                del av[:j]
                del self._tail_it[ch][:j]
                self.counters["tail_pruned"] += j
        hour = clock // HOUR_MS
        for exp_map, is_ctx in ((self._ctx_exp, True), (self._vw_exp, False)):
            if not exp_map:
                continue
            for hb in sorted(h for h in exp_map if h <= hour):
                keep: list[int] = []
                for kh in exp_map.pop(hb):
                    if is_ctx:
                        raw = self._ctx.get(kh)
                        if raw is None:
                            continue
                        f = _CTX.unpack(raw)
                        base = f[_C_ASOF] if f[_C_ASOF] >= 0 else f[_C_REC]
                        exp = base + (CTX_TTL_CF_DAYS if f[_C_CH] else CTX_TTL_REAL_DAYS) * DAY_MS
                        if exp < clock:
                            del self._ctx[kh]
                            self._mark_done(kh)
                            self.counters["ctx_expired"] += 1
                        else:
                            keep.append(kh)
                    else:
                        w = self._vw.get(kh)
                        if w is None:
                            continue
                        if w[1] + VAL_WAIT_TTL_DAYS * DAY_MS < clock:
                            del self._vw[kh]
                            self.counters["val_wait_expired"] += 1
                        else:
                            keep.append(kh)
                if keep:
                    exp_map[hb] = keep
        if self._ring:
            lo = (clock - DAY_MS) // HOUR_MS
            for hb in [h for h in self._ring if h < lo]:
                del self._ring[hb]

    # ------------------------------------------------------------------ değer tüketimi / çözüm
    def _consume(self, kh: int, v: tuple) -> None:
        f = list(_CTX.unpack(self._ctx[kh]))
        if v[0] == "X":
            f[_C_EST] = _EST_CLOSED
            self.counters["vanished"] += 1
        else:
            _tag, val_rec, ev, r, vfl = v
            as_of = f[_C_ASOF]
            if as_of < 0 or ev < as_of:
                f[_C_EST] = _EST_ANOMALY
                self.counters["clock_anomaly"] += 1
            else:
                f[_C_EST] = _EST_OK
                f[_C_Y] = r
                ch = f[_C_CH]
                if f[_C_EVOK]:
                    avail = ev if self.clock_mode == "event" else max(val_rec, f[_C_REC], ev)
                    dcodes = (f[_C_D0], f[_C_D1], f[_C_D2], f[_C_D3], f[_C_D4])
                    fl = vfl | (_F_LIVE if f[_C_LIVE] else 0) | (_F_A15 if f[_C_A15] else 0)
                    day = as_of // DAY_MS
                    if self.clock_mode == "event":
                        self.deferred_evidence.append((avail, ch, kh, f[_C_SETUP], f[_C_FAM], dcodes, day,
                                                       f[_C_COIN], r, fl))
                    elif self._batch_held:
                        self._held.append((ch, avail, kh, self._evidence_cells(ch, f[_C_SETUP], f[_C_FAM], dcodes), day,
                                           f[_C_COIN], r, fl))
                        self.counters["clock_held_evidence"] += 1
                    else:
                        self._ingest(ch, avail, kh, self._evidence_cells(ch, f[_C_SETUP], f[_C_FAM], dcodes), day,
                                     f[_C_COIN], r, fl)
                    self.counters["evidence_cf" if ch else "evidence_real"] += 1
                else:
                    self.counters["evidence_ineligible"] += 1
        self._ctx[kh] = _CTX.pack(*f)

    def _release_held(self, clock: int) -> None:
        """Saati ilerletmeyen toplu yazım(lar)da bekleyen kanıt → hücreler, `avail = max(avail, clock)` (katlama sırası)."""
        held, self._held = self._held, []
        for ch, avail, kh, cells, day, coin, r, fl in held:
            self._ingest(ch, max(int(avail), int(clock)), kh, tuple(cells), day, coin, r, fl)

    def _resolve(self, kh: int, clock: int) -> int:
        raw = self._ctx.pop(kh, None)
        self._mark_done(kh)
        if raw is None:
            return 0
        f = _CTX.unpack(raw)
        if f[_C_EST] != _EST_OK:
            self.counters["resolved_excluded"] += 1
            return 1
        self.counters["resolved"] += 1
        ok = f[_C_TSTATE] == _ST_CODE[ST_OK]
        prosp = bool(f[_C_TFLAGS] & _TF_PROSP)
        anom = bool(f[_C_TFLAGS] & _TF_ANOM)
        if ok and prosp and f[_C_LIVE] and not anom:
            book = BOOK_KEYS[f[_C_BOOK]] if f[_C_BOOK] != _NONE8 else "?"
            coh = COHORT_CODES[f[_C_COH]]
            for chn, lc in ((CH_REAL, f[_C_LABR]), (CH_CF, f[_C_LABC])):
                if lc == _NONE8:
                    continue
                k = "%s|%s|%s|%s" % (book, coh, chn, LABELS[lc])
                t = self._tally.get(k)
                if t is None:
                    t = self._tally[k] = [0, 0]
                t[0] += 1
                t[1] += f[_C_Y]
        if self.resolved_cols is not None:
            cols = self.resolved_cols
            as_of = f[_C_ASOF]
            flags = ((RF_LIVE if f[_C_LIVE] else 0) | (RF_PROSP if prosp else 0) | (RF_OK if ok else 0)
                     | (RF_ANOM if anom else 0) | (RF_A15 if f[_C_A15] else 0))
            for name, val in (("kh", kh), ("as_of", as_of), ("day", as_of // DAY_MS), ("res_clock", clock),
                              ("t_clock", f[_C_TCLOCK]), ("book", _s8(f[_C_BOOK])), ("fam", f[_C_FAM]),
                              ("coh", f[_C_COH]), ("kind", f[_C_CH]), ("lab_r", _s8(f[_C_LABR])),
                              ("lab_c", _s8(f[_C_LABC])), ("lvl_r", _s8(f[_C_LVLR])), ("lvl_c", _s8(f[_C_LVLC])),
                              ("y", f[_C_Y]), ("flags", flags), ("rfam", _s8(f[_C_RFAM]))):
                cols[name].append(val)
        return 1

    # ------------------------------------------------------------------ hedef
    def _target(self, kh: int, row: Mapping[str, Any], dims: dict | None, clock: int, anomaly: bool) -> dict | None:
        f = list(_CTX.unpack(self._ctx[kh]))
        ch = f[_C_CH]
        kind = R.KIND_CF if ch else R.KIND_ENTRY
        key = str(row.get("cf_key") if ch else row.get("trade_key"))
        as_of = f[_C_ASOF] if f[_C_ASOF] >= 0 else None
        setup, fam = f[_C_SETUP], f[_C_FAM]
        if self.clock_mode == "event":
            state = ST_OK if (setup >= 0 or fam >= 0) and as_of is not None else (
                ST_LATE if as_of is None else ST_NO_GROUP)
        elif as_of is None or as_of < clock - TAIL_H_MS:
            state = ST_LATE
        elif setup < 0 and fam < 0:
            state = ST_NO_GROUP
        else:
            state = ST_OK
        cnt = self.counters
        cnt["targets"] += 1
        cnt["targets_" + state.lower()] += 1
        prosp = self.born_ms is not None and clock >= self.born_ms
        sit = bool(f[_C_SIT])
        dcodes = (f[_C_D0], f[_C_D1], f[_C_D2], f[_C_D3], f[_C_D4])
        coin = XR.coin_of(row.get("symbol"))
        tdoc = {"kind": kind, "row_id": row.get("row_id"), "key": key, "book": row.get("book"),
                "book_name": row.get("book_name"), "setup_key": row.get("setup_key"), "family_key": row.get("family_key"),
                "symbol": row.get("symbol"), "coin": coin, "side": row.get("side"), "origin": row.get("origin"),
                "scorecard_class": None if ch else row.get("scorecard_class"),
                "reason_family": row.get("reason_family") if ch else None,
                "baseline_blocked": bool(row.get("baseline_blocked") is True) if ch else False,
                "as_of": row.get("created_at") if ch else row.get("opened_at"), "as_of_ms": as_of,
                "snapshot_status": row.get("snapshot_status"), "situation_used": sit,
                "dims": dict(dims) if dims is not None else None}
        docs: dict[str, dict | None] = {CH_REAL: None, CH_CF: None}
        if state == ST_OK:
            if self.clock_mode == "event":
                self.deferred_targets.append((as_of, kh, sit, setup, fam, dcodes, f[_C_COIN], coin))
            else:
                for ci, chn in ((0, CH_REAL), (1, CH_CF)):
                    docs[chn] = self._channel(ci, self._target_levels(ci, sit, setup, fam, dcodes), f[_C_COIN], coin,
                                              as_of, kh)
        adv_r = docs[CH_REAL]["advice"] if docs[CH_REAL] else None
        adv_c = docs[CH_CF]["advice"] if docs[CH_CF] else None
        f[_C_TSTATE] = _ST_CODE[state]
        f[_C_TFLAGS] = (_TF_PROSP if prosp else 0) | (_TF_ANOM if anomaly else 0)
        f[_C_LABR] = _LAB_CODE[adv_r] if adv_r else _NONE8
        f[_C_LABC] = _LAB_CODE[adv_c] if adv_c else _NONE8
        f[_C_LVLR] = _LVL_CODE[docs[CH_REAL]["level"]] if docs[CH_REAL] and docs[CH_REAL]["level"] else _NONE8
        f[_C_LVLC] = _LVL_CODE[docs[CH_CF]["level"]] if docs[CH_CF] and docs[CH_CF]["level"] else _NONE8
        f[_C_TCLOCK] = clock
        self._ctx[kh] = _CTX.pack(*f)
        self._ring_count(clock, str(row.get("book")), state, adv_r, adv_c)
        if self.clock_mode == "event":
            return None
        return self.make_row(target=tdoc, kind=kind, key=key, book=row.get("book"), batch_recorded_at=row.get("recorded_at"),
                             clock=clock, state=state, anomaly=anomaly, prosp=prosp, real=docs[CH_REAL], cf=docs[CH_CF])

    def make_row(self, *, target: dict, kind: str, key: str, book: Any, batch_recorded_at: Any, clock: int, state: str,
                 anomaly: bool, prosp: bool, real: dict | None, cf: dict | None) -> dict[str, Any]:
        """Tavsiye satırının ÇEKİRDEĞİ (+ `core_sha`). `recorded_at` ve `meta` çağıran ekler."""
        rid = advice_row_id(kind, key)
        row = {"schema": ADVICE_SCHEMA, "kind": ADVICE_KIND, "rev": 0, "row_id": rid, "decision_id": rid,
               "book": book, "app_mode": R.APP_MODE_PAPER, "advisor_id": ADVISOR_ID, "advisor_sha": ADVISOR_SHA,
               "situation_schema_sha": S.SCHEMA_SHA, "target": target, "batch_recorded_at": batch_recorded_at,
               "batch_clock_ms": clock, "evidence_cutoff_ms": target.get("as_of_ms"), "state": state,
               "clock_anomaly": bool(anomaly), "prospective": bool(prosp),
               "advisor_born_ms": self.born_ms if prosp else None,
               "advice": real["advice"] if real else None, "advice_tr": real["advice_tr"] if real else None,
               "advice_cf": cf["advice"] if cf else None, "advice_cf_tr": cf["advice_tr"] if cf else None,
               "real": real, "cf": cf, "method": METHOD, "effect": EFFECT_NONE}
        row["core_sha"] = core_sha(row)
        return row

    def _channel(self, ch: int, levels: list[tuple], coin_id: int, coin: str | None, as_of: int, kh: int,
                 *, tail: bool = True) -> dict[str, Any]:
        """Bir kanalın tavsiyesi: seviyeler sırayla; ilk yeterli seviye cevaplar (daha kesini ARANMAZ)."""
        cellset = {lv[3] for lv in levels}
        deltas: dict[int, dict[int, list[int]]] = {}
        cnts: dict[int, list[int]] = {}
        coin_d: dict[int, list[int]] = {}
        n_sub = 0
        if tail:
            av, items = self._tail_av[ch], self._tail_it[ch]
            i0 = bisect.bisect_left(av, as_of)
            n_sub = len(av) - i0
            shared = self._batch_sub(ch) if self._bsub is not None else None
            if shared is not None and i0 <= shared[0]:
                # (2026-09-30) [i0, pivot) tek tek + [pivot, son) toplu yazım başına BİR KEZ toplanmış deltalar: aynı
                # öğeler tam bir kez düşülür (tam sayı toplamları → sonuç birebir aynı; maliyet hedef × kanıt değil)
                _scan_tail(items, i0, shared[0], cellset, coin_id, deltas, cnts, coin_d)
                _merge_shared(shared[1], cellset, coin_id, deltas, cnts, coin_d)
            else:
                _scan_tail(items, i0, len(av), cellset, coin_id, deltas, cnts, coin_d)
        visible = self._ev_total[ch] - n_sub
        lv_out: list[list] = []
        ans = None
        cidx = self._cidx
        for name, grp, lv, ck, gk in levels:
            ci = cidx.get(ck, -1)
            dd = deltas.get(ck)
            n, s, cc = self._cell_nsc(ci, dd)
            lv_out.append([name, n, cc, _r6(s / n / 1e6) if n >= LEVEL_MEAN_MIN_N else None])
            if sufficient(n, cc):
                q1, q2, q3 = self._cell_q(ci, dd)
                ans = (name, grp, lv, ck, gk, CellStats(n, s, q1, q2, q3, cc), ci)
                break
        base = {"advice": VERI_AZ, "advice_tr": LABEL_TR[VERI_AZ], "reason": NO_SUFFICIENT_LEVEL, "level": None,
                "group": None, "group_key": None, "dims_used": None, "dropped": None, "n": None, "clusters": None,
                "mean_r": None, "sum_r": None, "se_r": None, "df": None, "t_crit": None, "ci95": None, "coin": None,
                "origin_mix": None, "cf_flags": None, "levels": lv_out, "visible_evidence": int(visible)}
        if ans is None:
            return base
        name, grp, lv, ck, gk, st, ci = ans
        mean, se, df, t, lo, hi = ci_cr1(st)
        lab, reason = label_for_ci(lo, hi)
        col = self._col
        sub = cnts.get(ck, [0, 0, 0, 0])
        n_c, s_c = self._cell_coin(ci, coin_id)
        y = coin_d.get(ck)
        if y is not None:
            n_c -= y[0]
            s_c -= y[1]
        sh, sh_sign = shrunk(n_c, s_c, st.n, st.s)
        mean_sign = (st.s > 0) - (st.s < 0)
        dims_used = {DIMS[i]: self._dv_list[i][self._dim_of(ck, i) - 1] for i in LEVEL_KEPT[lv]
                     if 0 < self._dim_of(ck, i) <= len(self._dv_list[i])}
        base.update({
            "advice": lab, "advice_tr": LABEL_TR[lab], "reason": reason, "level": name, "group": GROUPS[grp],
            "group_key": self.gk_str(gk), "dims_used": dims_used, "dropped": list(LEVEL_DROPPED[lv]),
            "n": st.n, "clusters": st.c, "mean_r": _r6(mean), "sum_r": _r6(st.s / R_QUANT), "se_r": _r6(se), "df": df,
            "t_crit": t, "ci95": [_r6(lo), _r6(hi)],
            "coin": {"coin": coin, "n": int(n_c), "mean_r": _r6(s_c / n_c / 1e6) if n_c > 0 else None,
                     "shrunk_mean_r": _r6(sh), "k": SHRINK_K,
                     "dissent": bool(n_c >= DISSENT_MIN_N and sh_sign != mean_sign)},
            "origin_mix": {"live": int(col["nl"][ci] - sub[0]), "backfill": int(st.n - (col["nl"][ci] - sub[0]))},
            "cf_flags": ({"a15": int(col["na"][ci] - sub[1]), "approx": int(col["np"][ci] - sub[2]),
                          "ambiguous": int(col["nb"][ci] - sub[3])} if ch == 1 else None)})
        return base

    @staticmethod
    def _dim_of(ck: int, i: int) -> int:
        return (ck >> (5 * i)) & 31

    # ------------------------------------------------------------------ 24 sa halka / koşan sayaçlar
    def _ring_count(self, clock: int, book: str, state: str, adv_r: str | None, adv_c: str | None) -> None:
        hb = self._ring.setdefault(clock // HOUR_MS, {})
        keys = (["%s|real|%s" % (book, adv_r), "%s|cf|%s" % (book, adv_c)] if state == ST_OK
                else ["%s|real|%s" % (book, state)])
        for k in keys:
            hb[k] = hb.get(k, 0) + 1

    def ring_24h(self) -> dict[str, dict[str, dict[str, int]]]:
        """Defter × kanal × etiket: son 24 saatlik toplu yazımlardaki hedef sayıları."""
        out: dict[str, dict[str, dict[str, int]]] = {}
        if self.batch_clock is None:
            return out
        lo = (self.batch_clock - DAY_MS) // HOUR_MS
        for hb, d in self._ring.items():
            if hb < lo:
                continue
            for k, n in d.items():
                book, chn, lab = k.split("|", 2)
                x = out.setdefault(book, {}).setdefault(chn, {})
                x[lab] = x.get(lab, 0) + int(n)
        return {b: {c: dict(sorted(v.items())) for c, v in sorted(d.items())} for b, d in sorted(out.items())}

    def tallies(self) -> dict[str, list[int]]:
        """Koşan sayaçlar (doğumdan beri, uygun ileriye dönük CANLI hedefler): "defter|kohort|kanal|etiket" → [n, Σy µR]."""
        return {k: list(v) for k, v in sorted(self._tally.items())}

    # ------------------------------------------------------------------ bellek / durum
    def memory_estimate(self) -> int:
        """Tutulan durumun YAKLAŞIK baytı (O(1); tavan ve panel için)."""
        n_tail = len(self._tail_it[0]) + len(self._tail_it[1])
        return int(len(self._ck) * 200 + (self._days_total + self._coins_total) * 18 + len(self._ctx) * 160
                   + len(self._vw) * 260 + (n_tail + len(self._held)) * 420 + int(self._done.nbytes)
                   + len(self._done_recent) * 70
                   + (len(self._gk_list) + len(self._coin_list)) * 120 + len(self._tally) * 150
                   + sum(len(d) for d in self._ring.values()) * 120)

    def stats(self) -> dict[str, Any]:
        return {"cells": len(self._ck), "cell_days": self._days_total, "coin_cells": self._coins_total,
                "pending_contexts": len(self._ctx), "value_waits": len(self._vw),
                "tail": [len(self._tail_it[0]), len(self._tail_it[1])], "evidence_total": list(self._ev_total),
                "held_evidence": len(self._held),
                "done_keys": int(self._done.size + len(self._done_recent)), "group_keys": len(self._gk_list),
                "coins": len(self._coin_list) - 1, "batch_clock_ms": self.batch_clock,
                "index_mb": round(self.memory_estimate() / 1048576.0, 3)}

    def resolved_arrays(self) -> dict[str, np.ndarray]:
        """Çözülmüş hedef dizileri (yalnız `collect=True`)."""
        if self.resolved_cols is None:
            return {}
        return {k: np.frombuffer(v.tobytes(), dtype=np.dtype(v.typecode if v.typecode != "b" else "i1")).copy()
                if len(v) else np.zeros(0, dtype=np.dtype(v.typecode if v.typecode != "b" else "i1"))
                for k, v in self.resolved_cols.items()}

    def lookup_tables(self) -> dict[str, list[str]]:
        return {"group_keys": list(self._gk_list), "coins": list(self._coin_list)}

    # ------------------------------------------------------------------ anlık görüntü (pickle YOK)
    def state_bytes(self) -> bytes:
        """Katlama durumunu gövde baytına paketler (JSON dizini + ham diziler; pickle YOK). Olay modu desteklenmez."""
        if self.clock_mode != "avail":
            raise ValueError("olay saati modu anlık görüntü yazmaz")
        self._merge_done()
        blobs: list[tuple[str, bytes, str]] = []

        def put(name: str, arr: array | np.ndarray) -> None:
            if isinstance(arr, np.ndarray):
                blobs.append((name, arr.tobytes(), arr.dtype.str))
            else:
                blobs.append((name, arr.tobytes(), arr.typecode))
        put("cell_key", self._ck)
        for k in _COLS:
            put("col_" + k, self._col[k])
        put("tab_len", array("q", (len(t) for t in self._tab)))
        tabs = array("q")
        for t in self._tab:
            tabs.extend(t)
        put("tab", tabs)
        for ch in (0, 1):
            items = self._tail_it[ch]
            put("tail%d_av" % ch, array("q", self._tail_av[ch]))
            put("tail%d_it" % ch, array("q", [x for it in items for x in it[1:5]]))
            put("tail%d_kh" % ch, np.array([it[5] for it in items], dtype=np.uint64))
            put("tail%d_nc" % ch, array("q", [len(it[6]) for it in items]))
            put("tail%d_cells" % ch, array("q", [ck for it in items for ck in it[6]]))
        put("ctx_kh", np.array(list(self._ctx), dtype=np.uint64))
        blobs.append(("ctx_raw", b"".join(self._ctx.values()), "ctx"))
        vw_kh = np.array(list(self._vw), dtype=np.uint64)
        vw = array("q")
        for (v, vclock) in self._vw.values():
            if v[0] == "X":
                vw.extend((1, 0, 0, 0, 0, vclock))
            else:
                vw.extend((0, v[1], v[2], v[3], v[4], vclock))
        put("vw_kh", vw_kh)
        put("vw", vw)
        put("done", self._done)
        js = {"schema": STATE_SCHEMA, "advisor_sha": ADVISOR_SHA, "situation_schema_sha": S.SCHEMA_SHA,
              "born_ms": self.born_ms, "batch_clock": self.batch_clock, "gk": self._gk_list, "dv": self._dv_list,
              "coins": self._coin_list, "ev_total": self._ev_total, "days_total": self._days_total,
              "coins_total": self._coins_total,
              "counters": self.counters, "ring": {str(k): v for k, v in self._ring.items()}, "tally": self._tally,
              "held": [[int(ch), int(av), str(kh), [int(c) for c in cells], int(day), int(coin), int(r), int(fl)]
                       for ch, av, kh, cells, day, coin, r, fl in self._held],
              "blobs": [[n, t, len(b)] for n, b, t in blobs]}
        head = json.dumps(js, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        return STATE_MAGIC + head + b"\n" + b"".join(b for _n, b, _t in blobs)

    @classmethod
    def from_state(cls, body: bytes | memoryview, *, collect: bool = False) -> "AdvisorFold":
        """`state_bytes` çıktısından katlama durumu. Mühür/şema uyuşmazlığı → ValueError (çağıran yeniden kurar).
        `body` bayt ya da memoryview olabilir (2026-09-30: diziler gövdeden KOPYASIZ dilimlerle kurulur)."""
        mv = memoryview(body)
        if bytes(mv[:len(STATE_MAGIC)]) != STATE_MAGIC:
            raise ValueError("advisor state: magic")
        nl = _find_nl(mv, len(STATE_MAGIC))
        if nl < 0:
            raise ValueError("advisor state: başlık")
        js = json.loads(bytes(mv[len(STATE_MAGIC):nl]).decode("ascii"))
        body = mv
        if js.get("schema") != STATE_SCHEMA or js.get("advisor_sha") != ADVISOR_SHA \
                or js.get("situation_schema_sha") != S.SCHEMA_SHA:
            raise ValueError("advisor state: şema/mühür uyuşmuyor")
        off = nl + 1
        blobs: dict[str, bytes] = {}
        for name, tcode, ln in js["blobs"]:
            blobs[name] = body[off:off + int(ln)]
            off += int(ln)
        if off != len(body):
            raise ValueError("advisor state: gövde boyu")

        def arr(name: str) -> array:
            a = array("q")
            a.frombytes(blobs[name])
            return a

        def u64(name: str) -> np.ndarray:
            return np.frombuffer(blobs[name], dtype=np.uint64).copy()
        f = cls(born_ms=js.get("born_ms"), collect=collect)
        f.batch_clock = js.get("batch_clock")
        f._gk_list = list(js["gk"])
        f._gk = {k: i for i, k in enumerate(f._gk_list)}
        f._dv_list = [list(x) for x in js["dv"]]
        f._dv = [{v: i + 1 for i, v in enumerate(lst)} for lst in f._dv_list]
        f._coin_list = list(js["coins"])
        f._coin = {k: i for i, k in enumerate(f._coin_list)}
        f._ev_total = [int(x) for x in js["ev_total"]]
        f._days_total = int(js["days_total"])
        f._coins_total = int(js.get("coins_total") or 0)
        for k, v in (js.get("counters") or {}).items():
            if k in f.counters:
                f.counters[k] = int(v)
        f._ring = {int(k): {str(a): int(b) for a, b in v.items()} for k, v in (js.get("ring") or {}).items()}
        f._tally = {str(k): [int(v[0]), int(v[1])] for k, v in (js.get("tally") or {}).items()}
        f._held = [(int(ch), int(av), int(kh), tuple(int(c) for c in cells), int(day), int(coin), int(r), int(fl))
                   for ch, av, kh, cells, day, coin, r, fl in (js.get("held") or [])]
        f._ck = arr("cell_key")
        f._cidx = {int(ck): i for i, ck in enumerate(f._ck)}
        f._col = {k: arr("col_" + k) for k in _COLS}
        if any(len(c) != len(f._ck) for c in f._col.values()):
            raise ValueError("advisor state: sütun boyu")
        tabs = arr("tab")
        pos = 0
        for ln in arr("tab_len"):
            f._tab.append(tabs[pos:pos + ln])
            pos += ln
        if pos != len(tabs) or len(f._tab) != len(f._ck):
            raise ValueError("advisor state: tablo boyu")
        for ch in (0, 1):
            av = list(arr("tail%d_av" % ch))
            it = arr("tail%d_it" % ch)
            kh = u64("tail%d_kh" % ch)
            nc = arr("tail%d_nc" % ch)
            cells = arr("tail%d_cells" % ch)
            items: list[tuple] = []
            p = 0
            for j in range(len(av)):
                k = nc[j]
                items.append((av[j], it[4 * j], it[4 * j + 1], it[4 * j + 2], it[4 * j + 3], int(kh[j]),
                              tuple(cells[p:p + k])))
                p += k
            f._tail_av[ch] = av
            f._tail_it[ch] = items
        ckh = u64("ctx_kh")
        raw = blobs["ctx_raw"]
        sz = _CTX.size
        if len(raw) != sz * len(ckh):
            raise ValueError("advisor state: bağlam boyu")
        for j, k in enumerate(ckh):
            rec = bytes(raw[sz * j:sz * (j + 1)])
            f._ctx[int(k)] = rec
            fl = _CTX.unpack(rec)
            base = fl[_C_ASOF] if fl[_C_ASOF] >= 0 else fl[_C_REC]
            exp = base + (CTX_TTL_CF_DAYS if fl[_C_CH] else CTX_TTL_REAL_DAYS) * DAY_MS
            f._ctx_exp.setdefault(exp // HOUR_MS, []).append(int(k))
        vkh = u64("vw_kh")
        vw = arr("vw")
        for j, k in enumerate(vkh):
            tag, a, b, c, d, vclock = vw[6 * j:6 * j + 6]
            v = ("X",) if tag == 1 else ("V", a, b, c, d)
            f._vw[int(k)] = (v, vclock)
            f._vw_exp.setdefault((vclock + VAL_WAIT_TTL_DAYS * DAY_MS) // HOUR_MS, []).append(int(k))
        f._done = u64("done")
        return f


def _find_nl(mv: memoryview, start: int, step: int = 1 << 20) -> int:
    """`mv[start:]` içindeki ilk yeni satırın konumu (parça parça; gövde KOPYALANMAZ) ya da −1."""
    n = len(mv)
    i = start
    while i < n:
        k = bytes(mv[i:min(n, i + step)]).find(b"\n")
        if k >= 0:
            return i + k
        i += step
    return -1


def _s8(v: int) -> int:
    """Kodlu bayt (255 = yok) → işaretli dizi değeri (−1 = yok)."""
    return -1 if v == _NONE8 else int(v)


def _scan_tail(items: list[tuple], lo: int, hi: int, cellset: set[int], coin_id: int,
               deltas: dict[int, dict[int, list[int]]], cnts: dict[int, list[int]], coin_d: dict[int, list[int]]) -> None:
    """Kuyruk öğeleri [lo, hi) → hedefin hücrelerindeki gün / bayrak / coin deltaları (yerinde toplar)."""
    for j in range(lo, hi):
        _a, day, icoin, r, fl, _kh, cells = items[j]
        for ck in cells:
            if ck not in cellset:
                continue
            dd = deltas.get(ck)
            if dd is None:
                dd = deltas[ck] = {}
                cnts[ck] = [0, 0, 0, 0]
            x = dd.get(day)
            if x is None:
                dd[day] = [1, r]
            else:
                x[0] += 1
                x[1] += r
            cc = cnts[ck]
            cc[0] += 1 if fl & _F_LIVE else 0
            cc[1] += 1 if fl & _F_A15 else 0
            cc[2] += 1 if fl & _F_APX else 0
            cc[3] += 1 if fl & _F_AMB else 0
            if icoin == coin_id:
                y = coin_d.get(ck)
                if y is None:
                    coin_d[ck] = [1, r]
                else:
                    y[0] += 1
                    y[1] += r


def _merge_shared(agg: dict[int, tuple], cellset: set[int], coin_id: int, deltas: dict[int, dict[int, list[int]]],
                  cnts: dict[int, list[int]], coin_d: dict[int, list[int]]) -> None:
    """Paylaşılan toplu yazım deltalarını (`_batch_sub`) hedefin deltalarına KOPYALAYARAK ekler (paylaşılan DEĞİŞMEZ)."""
    for ck in cellset:
        a = agg.get(ck)
        if a is None:
            continue
        add, acnt, acoin = a
        dd = deltas.get(ck)
        if dd is None:
            deltas[ck] = {d: [x[0], x[1]] for d, x in add.items()}
            cnts[ck] = list(acnt)
        else:
            for d, x in add.items():
                y = dd.get(d)
                if y is None:
                    dd[d] = [x[0], x[1]]
                else:
                    y[0] += x[0]
                    y[1] += x[1]
            cc = cnts[ck]
            for i in range(4):
                cc[i] += acnt[i]
        y = acoin.get(coin_id)
        if y is not None:
            z = coin_d.get(ck)
            if z is None:
                coin_d[ck] = [y[0], y[1]]
            else:
                z[0] += y[0]
                z[1] += y[1]


__all__ = [
    "ADVICE_KIND", "ADVICE_SCHEMA", "ADVISOR_ID", "ADVISOR_SHA", "ADVISOR_SPEC", "AdvisorFold", "BACKOFF_ORDER",
    "BASE_LEVELS", "BOOK_KEYS", "CF_LABEL_VERSIONS", "CF_WEIGHT_IN_PRIMARY", "CHANNELS", "CH_CF", "CH_REAL",
    "COHORT_CODES", "CTX_TTL_CF_DAYS", "CTX_TTL_REAL_DAYS", "CellStats", "DAY_MS", "DIMS", "EFFECT_NONE", "FoldResult",
    "GIR", "GIRME", "GROUPS", "LABELS", "LABEL_TR", "LEVEL_DROPPED", "LEVEL_KEPT", "LEVEL_NAMES", "METHOD", "MIN_CLUSTERS",
    "MIN_N", "NOTR", "PRIMARY_CHANNEL", "REASON_FAMILIES", "RF_A15", "RF_ANOM", "RF_LIVE", "RF_OK", "RF_PROSP",
    "R_QUANT", "SHRINK_K", "SITU_LEVELS", "ST_LATE", "ST_NO_GROUP", "ST_OK", "T975", "T975_INF", "TAIL_H_MS",
    "VAL_WAIT_TTL_DAYS", "VERI_AZ", "advice_row_id", "avail_ms", "ci_cr1", "core_sha", "decide", "key_hash",
    "label_for_ci", "level_class", "levels_for", "ms_exact", "r_micro", "shrunk", "situation_used", "sufficient", "t975",
    "target_dims",
]
