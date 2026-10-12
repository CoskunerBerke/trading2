"""P3b-2a — `strategy_lib.indicators` altın testleri (docs/ENGINE_BRAIN_V1.md §2.2, §8.4 P3b-2a notu).

* Elle hesaplanmış küçük seriler (EMA, Wilder ATR14/RSI, BB, Keltner, Donchian, ER, yüzdelik sıra).
* Depodaki BAŞVURU uygulamalarına eşitlik, rastgele serilerde (sabit tohum): `tradingbot.indicators.ema/atr/rsi/
  bollinger/donchian`, `signal_lab.indicators()["atr"]` ve `aux_series()` (`hi20`/`lo10`, `bb_up`/`bb_dn`),
  `indicators_ext.keltner(…, atr_len=14)` ve `bb_width_pct_rank`. EWM tabanlılar bayt-aynı; kayan toplamlılar göreli
  ≤ 1e-9.
* H ailesinin fonlama ölçüsü fut_v1'in KENDİ fonksiyonudur (`futures_features.align_funding`): varyanta verilen pencere
  (`funding_lookback_ms` = `live_requirements("4h")["funding_ms"]`) bütün seriyle aynı sonucu verir.
"""
from __future__ import annotations

import math
import random

import numpy as np
import pandas as pd
import pytest

from tradingbot import futures_features as FF
from tradingbot import indicators as I
from tradingbot import indicators_ext as X
from tradingbot import signal_lab as L
from tradingbot.strategy_lib import h_funding as HF
from tradingbot.strategy_lib import indicators as G

NAN = float("nan")


def _same(a, b, *, rel: float = 0.0) -> None:
    assert len(a) == len(b)
    for k, (x, y) in enumerate(zip(a, b)):
        if x != x or y != y:
            assert x != x and y != y, (k, x, y)
        elif rel == 0.0:
            assert x == y, (k, x, y)
        else:
            assert abs(x - y) <= rel * max(1.0, abs(y)), (k, x, y)


# ============================================================================ elle hesaplanmış altın değerler
def test_golden_hand_computed_values():
    e = G.ema([1.0, 2.0, 3.0, 4.0], 3)                       # alpha = 1/(1 + 1) = 0,5; ısınma 3
    assert e[0] != e[0] and e[1] != e[1] and e[2:] == [2.25, 3.125]
    h, lo, c = [2.0, 3.0, 4.0], [1.0, 1.0, 2.0], [1.5, 2.0, 3.0]
    assert G.true_range(h, lo, c) == [1.0, 2.0, 2.0]
    a = G.atr(h, lo, c, 2)                                    # Wilder alpha 0,5
    assert a[0] != a[0] and a[1:] == [1.5, 1.75]
    r = G.rsi([10.0, 11.0, 10.0, 12.0], 2)                   # kazanç ort. 0,5 → 1,25; kayıp 0,5 → 0,25
    assert r[0] != r[0] and r[1] != r[1] and r[2] == 50.0 and r[3] == pytest.approx(100.0 - 100.0 / 6.0, abs=1e-12)
    assert G.rsi([1.0, 2.0, 3.0, 4.0], 2)[3] == 100.0, "ortalama kayıp 0 → 100 (canlı tanım)"
    lo3, mid3, up3 = G.bollinger_at([1.0, 2.0, 3.0], 2, 3, 2.0)
    sd = math.sqrt(2.0 / 3.0)
    assert (mid3, lo3, up3) == pytest.approx((2.0, 2.0 - 2 * sd, 2.0 + 2 * sd), abs=1e-12)
    assert G.bb_width_at([1.0, 2.0, 3.0], 2, 3, 2.0) == pytest.approx(4 * sd / 2.0, abs=1e-12)
    assert G.keltner_at(h, lo, c, 2, length=3, mult=2.0, atr_len=2) == (2.375 - 3.5, 2.375, 2.375 + 3.5)
    hi = [1.0, 5.0, 3.0, 4.0]
    assert G.donchian_high(hi, 2, 3) == 5.0 == G.donchian_high(hi, 2, 2) and G.donchian_high(hi, 2, 1) != G.donchian_high(hi, 2, 1)
    assert G.donchian_low([4.0, 1.0, 3.0, 2.0], 2, 3) == 1.0
    assert G.er([1.0, 2.0, 1.0, 2.0], 3, 3) == pytest.approx(1 / 3)
    assert G.er([1.0, 2.0, 3.0, 4.0], 3, 3) == 1.0
    assert G.er([2.0, 1.0, 3.0, 0.0], 3, 3) == pytest.approx(2 / 6)
    assert G.er([1.0, 1.0, 1.0, 1.0], 3, 3) is None, "hareketsiz pencere: ölçülemez"
    assert G.er([1.0, 2.0], 3, 1) is None
    assert G.pct_rank([1.0, 2.0, 3.0, 4.0, 5.0], 1.0) == 0.2 and G.pct_rank([1.0, 2.0, 3.0, 4.0, 5.0], 3.0) == 0.6
    assert G.pct_rank([1.0, 1.0, 2.0, 2.0], 2.0) == 3.5 / 4, "eşitlerde ortalama sıra (pandas rank)"


# ============================================================================ başvurulara eşitlik
def _series(seed: int, n: int) -> tuple[list[float], list[float], list[float]]:
    rnd = random.Random(seed)
    c = [100.0]
    for _ in range(n - 1):
        c.append(c[-1] * math.exp(rnd.gauss(0.0, 0.02)) if rnd.random() > 0.1 else c[-1])      # %10 düz (eşitlikler)
    h = [x * (1 + abs(rnd.gauss(0.0, 0.01))) for x in c]
    lo = [x * (1 - abs(rnd.gauss(0.0, 0.01))) for x in c]
    return h, lo, c


@pytest.mark.parametrize("seed", range(12))
def test_equal_to_the_repository_reference_implementations(seed):
    n = random.Random(seed).randint(1, 520)
    h, lo, c = _series(seed, n)
    s, hs, ls = pd.Series(c), pd.Series(h), pd.Series(lo)
    for span in (2, 14, 20, 50, 200):
        _same(G.ema(c, span), I.ema(s, span).tolist())
    _same(G.atr(h, lo, c, 14), I.atr(hs, ls, s, 14).tolist())
    df = pd.DataFrame({"timestamp": range(n), "open": c, "high": h, "low": lo, "close": c, "volume": [1.0] * n})
    _same(G.atr(h, lo, c, 14), list(L.indicators(df)["atr"]))
    for k in (2, 14):
        _same(G.rsi(c, k), I.rsi(s, k).tolist())
    blo, _bm, bup = I.bollinger(s, 20, 2.0)
    bb = [G.bollinger_at(c, i, 20, 2.0) for i in range(n)]
    _same([x[0] for x in bb], blo.tolist(), rel=1e-9)
    _same([x[2] for x in bb], bup.tolist(), rel=1e-9)
    if n:
        aux = L.aux_series(df)
        _same([x[0] for x in bb], list(aux["bb_dn"]), rel=1e-9)
        _same([G.donchian_high(h, 20, i) for i in range(n)], list(aux["hi20"]))
        _same([G.donchian_low(lo, 10, i) for i in range(n)], list(aux["lo10"]))
    klo, _km, kup = X.keltner(hs, ls, s, 20, 2.0, 14)
    kk = [G.keltner_at(h, lo, c, i) for i in range(n)]
    _same([x[0] for x in kk], klo.tolist())
    _same([x[2] for x in kk], kup.tolist())
    dlo, dup = I.donchian(hs, ls, 55)
    _same([G.donchian_high(h, 55, i) for i in range(n)], dup.tolist())
    _same([G.donchian_low(lo, 55, i) for i in range(n)], dlo.tolist())
    rk = X.bb_width_pct_rank(s, 20, 2.0, 120).tolist()
    w = [G.bb_width_at(c, i) for i in range(n)]
    mine = [G.pct_rank(w[i - 119:i + 1], w[i]) if i >= 119 + 19 else NAN for i in range(n)]
    _same(mine, [x if i >= 119 + 19 else NAN for i, x in enumerate(rk)], rel=1e-12)


def test_er_matches_its_definition_on_random_series():
    _h, _lo, c = _series(5, 300)
    for i in range(20, 300):
        num = abs(c[i] - c[i - 20])
        den = sum(abs(c[k] - c[k - 1]) for k in range(i - 19, i + 1))
        got = G.er(c, 20, i)
        assert (got is None) if den == 0 else got == pytest.approx(num / den, rel=1e-12)


def test_funding_measure_is_the_fut_v1_function_on_the_variant_window():
    rnd = random.Random(3)
    t0 = 1_600_000_000_000 // 3_600_000 * 3_600_000
    rows, t, r = [], t0, 0.0001
    for k in range(400):
        step = (8 if k < 200 else 4) * 3_600_000                  # 8h → 4h geçişi
        t += step
        r = 0.0001 + 0.9 * (r - 0.0001) + rnd.gauss(0.0, 0.0002)
        rows.append((t + rnd.randint(0, 9), r, 100.0))             # ms kayması
    lb = int(FF.live_requirements("4h")["funding_ms"])
    assert HF._FundingBase.funding_lookback_ms == lb == FF.WINDOW_DAYS * 86_400_000 + 9 * 3_600_000
    ft = np.asarray([x[0] for x in rows], dtype=np.int64)
    fr = np.asarray([x[1] for x in rows], dtype=float)
    for T in range(t0 + 30 * 86_400_000, t + 86_400_000, 4 * 3_600_000):
        win = [x for x in rows if T - lb < x[0] <= T]
        full = FF.align_funding(np.asarray([T], dtype=np.int64), ft, fr)
        got = HF.f8_state(win, T)
        _same(list(got), [float(full["f8"][0]), float(full["f8_hi"][0]), float(full["f8_lo"][0])])
        afull = FF.align_funding(np.asarray([T], dtype=np.int64), ft, np.abs(fr))
        _same(list(HF.f8_state(win, T, absolute=True)), [float(afull["f8"][0]), float(afull["f8_hi"][0]),
                                                          float(afull["f8_lo"][0])])
    assert HF.f8_state(None, t) == HF.f8_state([], t) and all(x != x for x in HF.f8_state(None, t))
    assert all(x != x for x in HF.f8_state([(t0, None, 1.0)], t0 + 1)), "okunamayan oran: ölçü yok"
