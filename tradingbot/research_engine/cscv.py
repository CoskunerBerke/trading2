"""P3d — CSCV ile aşırı uydurma olasılığı (PBO) (docs/ENGINE_BRAIN_V1.md §3.6; mühür `STATS_V1.pbo`; Bailey, Borwein,
López de Prado, Zhu 2014 "The Probability of Backtest Overfitting").

* **Girdi:** seçim grubunun matrisi = gün × yapılandırma (OOS günlük getiri; ortak zaman ekseni). Grup üyelerinin OOS gün
  kümeleri farklıysa yalnız HEPSİNİN OOS olduğu günler (kesişim) kullanılır (`matrix_from_series`).
* **Bloklar:** S = 16 (mühür) eşit, bitişik blok; T, S'ye bölünmüyorsa EN ESKİ T mod S gün atılır (eşit blok şartı; en
  yeni veri korunur). Grup < 2 yapılandırma ya da T < 16 × 5 gün → `NOT_COMPUTABLE` (kapı geçmez).
* **Bölünmeler:** C(16, 8) = 12.870 bölünmenin HEPSİ (`itertools.combinations` sözlük sırası); IS = seçilen 8 blok,
  OOS = kalan 8.
* **Başarım ölçüsü:** yapılandırmanın IS/OOS dilimindeki günlük Sharpe'ı (ortalama / örneklem std, ddof = 1; std = 0 → 0);
  Bailey vd.'nin varsayılanı ve DSR'nin ölçüsü. Mühürlü P3d okuması U3 (`STATS_V1.pbo.metric`, düzeltme mührü 7) — artık
  gün, ortak eksen ve eşitlik kuralları da aynı okumadadır. Tek yapılandırmalı grup (örn. K_FM@POOLED_OWN) hesaplanamaz →
  kapı geçmez (U5, `STATS_V1.pbo.single_config_group`).
* **Blok kenarı arındırması** (düzeltme mührü 8, U3 okumasına eklendi; muhafazakâr): eşit bloklar kurulduktan sonra her
  bloğun ilk h takvim günü atılır (h = grubun mühürlü en uzun max_hold'u, gün; önceki bloğun işlemlerinin fiyat yolu bu
  günlere taşar); arındırmadan sonra bir blokta < 5 gün kalırsa hesaplanamaz. Grup eksikse (R3) PBO hesaplanmaz (`stats`).
* **Logit:** IS'te en iyi yapılandırma (eşitlikte ilk sütun) → OOS'ta göreli sırası ω̄ = sıra / (N + 1) (sıra 1 = en kötü;
  eşitlikte ortalama sıra) → λ = ln(ω̄ / (1 − ω̄)). **PBO** = λ ≤ 0 olan bölünmelerin payı (mühür tanımı). λ ≤ 0 ⟺
  2 × sıra ≤ N + 1 (tam aritmetikle sınanır).

Ağsız, yalnız numpy; belirlenimci (rastgelelik yok).
"""
from __future__ import annotations

import itertools
import math
from typing import Any, Mapping, Sequence

import numpy as np

from . import registry as RG

_TH = RG.STATS_V1["thresholds"]["pbo"]
BLOCKS = int(_TH["blocks"])                                   # 16
MIN_CONFIGS = int(_TH["min_configs"])                         # 2
MIN_DAYS_PER_BLOCK = int(_TH["min_oos_days_per_block"])       # 5
OK, NOT_COMPUTABLE = "OK", "NOT_COMPUTABLE"
_M = RG.STATS_V1["pbo"]["metric"]
METRIC = str(_M["metric"])
# mühürlü okuma (U3) uygulananla aynı olmalı (kapalı hata)
if (METRIC, _M["remainder_days"], _M["group_axis"], _M["is_best_ties"], _M["oos_rank_ties"], _M["edge_purge"]) != (
        "DAILY_SHARPE", "DROP_OLDEST", "INTERSECTION_OF_MEMBER_OOS_DAYS", "FIRST_CONFIG", "AVERAGE_RANK",
        "DROP_FIRST_H_CALENDAR_DAYS_OF_EACH_OF_THE_16_BLOCKS") or \
        RG.STATS_V1["pbo"]["single_config_group"]["rule"] != "SINGLE_CONFIG_GROUP_NOT_COMPUTABLE_FAILS_CLOSED" or \
        RG.STATS_V1["pbo"]["group_completeness"]["rule"] != "INCOMPLETE_GROUP_FAILS_CLOSED":
    raise RuntimeError("STATS_V1.pbo okumaları cscv.py uygulamasıyla uyuşmuyor")

_SPLITS: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def splits(blocks: int = BLOCKS) -> tuple[np.ndarray, np.ndarray]:
    """(IS, OOS) blok göstergeleri: C(blocks, blocks/2) × blocks 0/1 matrisleri (sözlük sırası)."""
    if blocks not in _SPLITS:
        if blocks % 2 or blocks < 2:
            raise ValueError("CSCV blok sayısı çift ve ≥ 2 olmalı")
        comb = list(itertools.combinations(range(blocks), blocks // 2))
        is_ = np.zeros((len(comb), blocks))
        for i, c in enumerate(comb):
            is_[i, list(c)] = 1.0
        _SPLITS[blocks] = (is_, 1.0 - is_)
    return _SPLITS[blocks]


def _sharpe(w: np.ndarray, s: np.ndarray, q: np.ndarray, cnt: np.ndarray) -> np.ndarray:
    n = (w @ cnt)[:, None]
    su, sq = w @ s, w @ q
    mean = su / n
    var = np.maximum((sq - su * su / n) / (n - 1), 0.0)
    sd = np.sqrt(var)
    return np.where(sd > 0, mean / np.where(sd > 0, sd, 1.0), 0.0)


def _blocks(m: np.ndarray, blocks: int, purge_days: int, days: Sequence[int] | None
            ) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """Eşit bloklar (en eski T mod S gün atılır), sonra her bloğun İLK `purge_days` günü atılır (blok kenarı arındırması:
    önceki bloğun işlemlerinin fiyat yolu bu günlere taşar). Döner: (blok toplamları, kare toplamları, blok gün sayıları,
    atılan eski gün, arındırılan gün)."""
    t, n = m.shape
    length = t // blocks
    drop = t - length * blocks
    m = m[drop:]
    d = (np.arange(t) if days is None else np.asarray(list(days), dtype=np.int64))[drop:]
    bid = np.repeat(np.arange(blocks), length)
    keep = np.ones(len(m), dtype=bool)
    if purge_days > 0:
        for b in range(blocks):
            sl = slice(b * length, (b + 1) * length)
            keep[sl] = d[sl] >= d[b * length] + int(purge_days)
    s = np.zeros((blocks, n))
    q = np.zeros((blocks, n))
    np.add.at(s, bid[keep], m[keep])
    np.add.at(q, bid[keep], m[keep] * m[keep])
    cnt = np.bincount(bid[keep], minlength=blocks).astype(float)
    return s, q, cnt, drop, int((~keep).sum())


def _split_sharpes(s: np.ndarray, q: np.ndarray, cnt: np.ndarray, blocks: int) -> tuple[np.ndarray, np.ndarray]:
    is_w, oos_w = splits(blocks)
    return _sharpe(is_w, s, q, cnt), _sharpe(oos_w, s, q, cnt)


def pbo(matrix: Any, *, blocks: int = BLOCKS, min_configs: int = MIN_CONFIGS,
        min_days_per_block: int = MIN_DAYS_PER_BLOCK, purge_days: int = 0,
        days: Sequence[int] | None = None) -> dict[str, Any]:
    """`matrix`: T × N (gün × yapılandırma) günlük getiri; `days`: satırların UTC günleri (blok kenarı arındırması takvim
    günüyle; verilmezse satır sırası); `purge_days`: her bloğun başından atılan gün (U3: grubun mühürlü en uzun max_hold'u,
    gün). Arındırmadan sonra bir blokta < 5 gün kalırsa hesaplanamaz. Döner: {state, pbo, n_splits, T, T_used, N,
    dropped_days, purged_days, logits_le0, reason}. Hesaplanamazsa `pbo` None."""
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2:
        raise ValueError("CSCV matrisi gün × yapılandırma (2 boyut) olmalı")
    t, n = m.shape
    out: dict[str, Any] = {"state": NOT_COMPUTABLE, "pbo": None, "n_splits": 0, "T": int(t), "T_used": 0, "N": int(n),
                           "dropped_days": 0, "purged_days": 0, "purge_days": int(purge_days), "logits_le0": 0,
                           "reason": None, "blocks": blocks}
    if not np.all(np.isfinite(m)):
        out["reason"] = "NON_FINITE"
        return out
    if n < min_configs:
        out["reason"] = "FEW_CONFIGS"
        return out
    if t < blocks * min_days_per_block:
        out["reason"] = "FEW_DAYS"
        return out
    s, q, cnt, drop, purged = _blocks(m, blocks, purge_days, days)
    out.update(dropped_days=int(drop), purged_days=purged)
    if cnt.min() < max(min_days_per_block, 2):
        out["reason"] = "FEW_DAYS_AFTER_PURGE"
        return out
    is_sr, oos_sr = _split_sharpes(s, q, cnt, blocks)
    best = np.argmax(is_sr, axis=1)                                   # eşitlikte ilk sütun
    ob = oos_sr[np.arange(len(best)), best]
    less = (oos_sr < ob[:, None]).sum(axis=1)
    ties = (oos_sr == ob[:, None]).sum(axis=1) - 1
    # ortalama sıra = less + 1 + ties/2 ; λ ≤ 0 ⟺ 2·sıra ≤ N + 1 ⟺ 2·less + 2 + ties ≤ N + 1
    le0 = (2 * less + 2 + ties) <= (n + 1)
    out.update(state=OK, pbo=float(le0.mean()), n_splits=int(len(best)), T_used=int(cnt.sum()),
               logits_le0=int(le0.sum()))
    return out


def logits(matrix: Any, *, blocks: int = BLOCKS, purge_days: int = 0, days: Sequence[int] | None = None) -> list[float]:
    """Her bölünmenin λ'sı (gösterim/test; sonsuz değer yok: ω̄ ∈ (0, 1))."""
    m = np.asarray(matrix, dtype=float)
    n = m.shape[1]
    s, q, cnt, _d, _p = _blocks(m, blocks, purge_days, days)
    is_sr, oos_sr = _split_sharpes(s, q, cnt, blocks)
    best = np.argmax(is_sr, axis=1)
    ob = oos_sr[np.arange(len(best)), best]
    rank = (oos_sr < ob[:, None]).sum(axis=1) + 1 + ((oos_sr == ob[:, None]).sum(axis=1) - 1) / 2.0
    w = rank / (n + 1)
    return [math.log(x / (1.0 - x)) for x in w.tolist()]


def matrix_from_series(series: Mapping[str, Mapping[int, float]], days: Sequence[int] | None = None
                       ) -> tuple[list[str], list[int], np.ndarray]:
    """{yapılandırma: {gün: getiri}} → (yapılandırmalar sıralı, ortak günler sıralı, T × N matris). `days` verilmezse
    ortak eksen = yapılandırmaların gün kümelerinin KESİŞİMİ (her yapılandırmanın OOS'u olan günler)."""
    cfgs = sorted(series)
    if days is None:
        common: set[int] | None = None
        for c in cfgs:
            ks = set(series[c])
            common = ks if common is None else common & ks
        days = sorted(common or ())
    else:
        days = sorted(days)
    mat = np.array([[float(series[c].get(d, 0.0)) for c in cfgs] for d in days], dtype=float).reshape(len(days), len(cfgs))
    return cfgs, list(days), mat


def group_pbo(series: Mapping[str, Mapping[int, float]], *, purge_days: int = 0) -> dict[str, Any]:
    """Seçim grubunun PBO'su (`matrix_from_series` + `pbo`, takvim günüyle blok kenarı arındırması); `configs` eklenir."""
    cfgs, days, mat = matrix_from_series(series)
    res = pbo(mat, purge_days=purge_days, days=days)
    res["configs"] = cfgs
    return res


__all__ = ["BLOCKS", "METRIC", "MIN_CONFIGS", "MIN_DAYS_PER_BLOCK", "NOT_COMPUTABLE", "OK", "group_pbo", "logits",
           "matrix_from_series", "pbo", "splits"]
