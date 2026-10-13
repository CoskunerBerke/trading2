# -*- coding: utf-8 -*-
"""P3d — CSCV-PBO bilinen cevaplar (docs/ENGINE_BRAIN_V1.md §3.6, §8.1 P3d; mühür `STATS_V1.pbo`). Sentetik veri, ağ yok."""
from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from tradingbot.research_engine import cscv as CS
from tradingbot.research_engine import registry as RG


def _noise(seed: int, t: int, n: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, 1.0, (t, n))


def test_sealed_block_count_and_all_c16_8_splits():
    assert CS.BLOCKS == RG.STATS_V1["thresholds"]["pbo"]["blocks"] == 16
    is_w, oos_w = CS.splits()
    assert is_w.shape == (math.comb(16, 8), 16) == (12870, 16)
    assert np.all(is_w.sum(axis=1) == 8) and np.all(is_w + oos_w == 1.0)
    assert len({tuple(r) for r in is_w.astype(int).tolist()}) == 12870          # hepsi, tekrar yok
    res = CS.pbo(_noise(1, 800, 5))
    assert res["state"] == CS.OK and res["n_splits"] == 12870


@pytest.mark.parametrize("n_cfg, t", [(10, 800), (6, 480)])
def test_noise_gives_pbo_one_half_plus_minus_a_tenth(n_cfg, t):
    """Bilinen cevap 1: saf gürültüde IS-en-iyinin OOS sırası düzgündür → beklenen PBO 0,5 (tek matrisin PBO'su geniş
    dağılır — bölünmeler ilişkili —; kabul ölçütü 100 sabit tohumlu matrisin ortalamasıdır)."""
    vals = [CS.pbo(_noise(5000 + s, t, n_cfg))["pbo"] for s in range(100)]
    assert abs(float(np.mean(vals)) - 0.5) <= 0.1, np.mean(vals)


@pytest.mark.parametrize("seed", range(6))
def test_planted_edge_gives_pbo_below_a_tenth(seed):
    """Bilinen cevap 2: tek yapılandırmaya dikilmiş avantaj → IS-en-iyi OOS'ta da en iyi → PBO < 0,1."""
    m = _noise(seed, 800, 10)
    m[:, seed % 10] += 0.25
    assert CS.pbo(m)["pbo"] < 0.1


def test_not_computable_closes_the_gate_value():
    assert CS.pbo(_noise(1, 800, 1))["state"] == CS.NOT_COMPUTABLE                      # < 2 yapılandırma
    r = CS.pbo(_noise(1, 16 * 5 - 1, 4))
    assert r["state"] == CS.NOT_COMPUTABLE and r["pbo"] is None and r["reason"] == "FEW_DAYS"
    assert CS.pbo(_noise(1, 16 * 5, 4))["state"] == CS.OK                               # sınırda hesaplanır
    bad = _noise(1, 200, 3)
    bad[3, 1] = np.nan
    assert CS.pbo(bad)["state"] == CS.NOT_COMPUTABLE


def test_unequal_length_drops_the_oldest_days_only():
    m = _noise(3, 16 * 50 + 7, 4)
    r = CS.pbo(m)
    assert r["dropped_days"] == 7 and r["T_used"] == 800
    assert CS.pbo(m[7:])["pbo"] == r["pbo"]
    m2 = m.copy()
    m2[:7] = 1e6                                                                         # atılan eski günler sonucu oynatmaz
    assert CS.pbo(m2)["pbo"] == r["pbo"]


def test_pbo_equals_a_direct_per_split_reference():
    """Vektörel uygulama = bölünme başına düz başvuru (Sharpe, IS-en-iyi ilk sütun, ortalama sıra, logit ≤ 0)."""
    m = _noise(11, 16 * 6, 4)
    m[:, 2] += 0.05
    blk = m.reshape(16, 6, 4)

    def sr(x: np.ndarray) -> np.ndarray:
        sd = x.std(axis=0, ddof=1)
        return np.where(sd > 0, x.mean(axis=0) / np.where(sd > 0, sd, 1.0), 0.0)

    le0 = 0
    lam = []
    for comb in itertools.combinations(range(16), 8):
        rest = [b for b in range(16) if b not in comb]
        i_sr = sr(blk[list(comb)].reshape(-1, 4))
        o_sr = sr(blk[rest].reshape(-1, 4))
        best = int(np.argmax(i_sr))
        rank = 1 + sum(o_sr < o_sr[best]) + (sum(o_sr == o_sr[best]) - 1) / 2.0
        w = rank / 5.0
        lam.append(math.log(w / (1 - w)))
        le0 += lam[-1] <= 0
    assert CS.pbo(m)["pbo"] == le0 / 12870
    assert CS.logits(m) == pytest.approx(lam)


def test_group_matrix_uses_the_common_oos_days():
    s = {"a": {1: 0.1, 2: 0.2, 3: 0.3}, "b": {2: 1.0, 3: 2.0, 4: 3.0}}
    cfgs, days, mat = CS.matrix_from_series(s)
    assert cfgs == ["a", "b"] and days == [2, 3] and mat.tolist() == [[0.2, 1.0], [0.3, 2.0]]
    assert CS.group_pbo({"only": {1: 0.0}})["state"] == CS.NOT_COMPUTABLE


def test_pbo_is_deterministic():
    m = _noise(7, 640, 8)
    assert CS.pbo(m) == CS.pbo(m.copy())


def test_no_block_edge_purge_and_the_matrix_is_daily_mark_to_market():
    """Düzeltme mührü 9: blok kenarı arındırması geri alındı (Bailey vd. uygulaması: matris günlük piyasa değerlemesi
    getirileri); bilinen cevaplar (gürültü 0,5 ± 0,1; dikilmiş < 0,1) yukarıdaki testlerde arındırmasız tutar."""
    import inspect
    m = RG.STATS_V1["pbo"]["metric"]
    assert "edge_purge" not in m and m["matrix_returns"] == "DAILY_MTM_PRICE_PATH_RESIDUAL_ON_EXIT_DAY"
    assert m["matrix_fallback"] == "LINEAR_OVER_HELD_DAYS_ONLY_WITHOUT_PRICE_PATH"
    assert not [p for p in inspect.signature(CS.pbo).parameters if "purge" in p]
    assert "purge" not in inspect.getsource(CS.pbo) + inspect.getsource(CS._blocks)
