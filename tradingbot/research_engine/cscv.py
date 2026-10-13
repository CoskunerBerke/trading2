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
if (METRIC, _M["remainder_days"], _M["group_axis"], _M["is_best_ties"], _M["oos_rank_ties"]) != (
        "DAILY_SHARPE", "DROP_OLDEST", "INTERSECTION_OF_MEMBER_OOS_DAYS", "FIRST_CONFIG", "AVERAGE_RANK") or \
        RG.STATS_V1["pbo"]["single_config_group"]["rule"] != "SINGLE_CONFIG_GROUP_NOT_COMPUTABLE_FAILS_CLOSED":
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


def _sharpe(w: np.ndarray, s: np.ndarray, q: np.ndarray, n: int) -> np.ndarray:
    su, sq = w @ s, w @ q
    mean = su / n
    var = np.maximum((sq - su * su / n) / (n - 1), 0.0)
    sd = np.sqrt(var)
    return np.where(sd > 0, mean / np.where(sd > 0, sd, 1.0), 0.0)


def pbo(matrix: Any, *, blocks: int = BLOCKS, min_configs: int = MIN_CONFIGS,
        min_days_per_block: int = MIN_DAYS_PER_BLOCK) -> dict[str, Any]:
    """`matrix`: T × N (gün × yapılandırma) günlük getiri. Döner: {state, pbo, n_splits, T, T_used, N, dropped_days,
    logits_le0, reason}. Hesaplanamazsa `pbo` None."""
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2:
        raise ValueError("CSCV matrisi gün × yapılandırma (2 boyut) olmalı")
    t, n = m.shape
    out: dict[str, Any] = {"state": NOT_COMPUTABLE, "pbo": None, "n_splits": 0, "T": int(t), "T_used": 0, "N": int(n),
                           "dropped_days": 0, "logits_le0": 0, "reason": None, "blocks": blocks}
    if not np.all(np.isfinite(m)):
        out["reason"] = "NON_FINITE"
        return out
    if n < min_configs:
        out["reason"] = "FEW_CONFIGS"
        return out
    if t < blocks * min_days_per_block:
        out["reason"] = "FEW_DAYS"
        return out
    length = t // blocks
    drop = t - length * blocks
    m = m[drop:]
    blk = m.reshape(blocks, length, n)
    s, q = blk.sum(axis=1), (blk * blk).sum(axis=1)
    half = length * (blocks // 2)
    is_w, oos_w = splits(blocks)
    is_sr, oos_sr = _sharpe(is_w, s, q, half), _sharpe(oos_w, s, q, half)
    best = np.argmax(is_sr, axis=1)                                   # eşitlikte ilk sütun
    ob = oos_sr[np.arange(len(best)), best]
    less = (oos_sr < ob[:, None]).sum(axis=1)
    ties = (oos_sr == ob[:, None]).sum(axis=1) - 1
    # ortalama sıra = less + 1 + ties/2 ; λ ≤ 0 ⟺ 2·sıra ≤ N + 1 ⟺ 2·less + 2 + ties ≤ N + 1
    le0 = (2 * less + 2 + ties) <= (n + 1)
    out.update(state=OK, pbo=float(le0.mean()), n_splits=int(len(best)), T_used=int(length * blocks),
               dropped_days=int(drop), logits_le0=int(le0.sum()))
    return out


def logits(matrix: Any, *, blocks: int = BLOCKS) -> list[float]:
    """Her bölünmenin λ'sı (gösterim/test; sonsuz değer yok: ω̄ ∈ (0, 1))."""
    m = np.asarray(matrix, dtype=float)
    t, n = m.shape
    length = t // blocks
    m = m[t - length * blocks:]
    blk = m.reshape(blocks, length, n)
    s, q = blk.sum(axis=1), (blk * blk).sum(axis=1)
    half = length * (blocks // 2)
    is_w, oos_w = splits(blocks)
    is_sr, oos_sr = _sharpe(is_w, s, q, half), _sharpe(oos_w, s, q, half)
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


def group_pbo(series: Mapping[str, Mapping[int, float]]) -> dict[str, Any]:
    """Seçim grubunun PBO'su (`matrix_from_series` + `pbo`); `configs` ve ortak gün sayısı eklenir."""
    cfgs, days, mat = matrix_from_series(series)
    res = pbo(mat)
    res["configs"] = cfgs
    return res


__all__ = ["BLOCKS", "METRIC", "MIN_CONFIGS", "MIN_DAYS_PER_BLOCK", "NOT_COMPUTABLE", "OK", "group_pbo", "logits",
           "matrix_from_series", "pbo", "splits"]
