# -*- coding: utf-8 -*-
"""HIZLI kNN KANIT SORGUSU — eski olay-başına döngüyle BİT-AYNI (2026-10-06; docs/TOUR_CONTENTION_V2.md).

Sözleşme: her sorguda `SimilarPatternEngine.query`'nin döndürdüğü sözlük hızlı yolda (`fast_query=True`) ve eski döngüde
(`fast_query=False`) `==` ve JSON serileştirmesi olarak AYNIDIR. Daha sıkısı: seçim döngüsünün (`_select`) seçtiği
listenin TAMAMI (yalnız ilk 20 komşu değil) — her elemanın yuvarlanmamış mesafesi (float, bit bit), seviyesi ve olay
nesnesi — iki yolda aynıdır.

Kapsam:
1. Gerçekçi biçimde 13 seri (BTC bağlamı, funding, düz fiyat aralıkları = sıfır-sapmalı ve normalize edilmemiş yollar,
   sıfır hacim = NaN anlık görüntüler), rastgele sorgular: bütün seviyeler, iki yön, dört pencere, k ∈ {0, 1, …, 400},
   tarihsel idx, query_ts/now_ts, embargo, min_separation, min_sample, küme eşlemesi.
2. Çekişmeli: birebir aynı iki seri (mesafe eşitlikleri), aynı sembol spot + futures + 1h (aynı (sembol, olay_ts)),
   bütün sütunu NaN anlık görüntüler, min_sep çakışmaları, auto genişlemesi, aday listesinin tükenmesi.
3. Embargo ve çıkış sınırları: cutoff = eşik ve eşik−1, çıkış = query_ts ve query_ts−1.
4. Kanıt kendisi: 1. aşamanın alt sınırı HER adayda eski mesafenin altında; 2. aşama, sınırı sağlayan HER yaklaşıklıkla
   (rastgele, sıkı uç lo = d, −∞) aynı seçimi verir.
5. Geri dönüş anahtarı, yedeğe düşüş (arıza / koşulsuz sorgu), önbellek geçersizleme, bellek, config ve karar kimliği.
6. Depodaki diğer testlerin indeks verisi (tur ve alt süreç testlerinin sentetik 4h serileri) üzerinde yeniden oynatma.
"""
from __future__ import annotations

import gc
import importlib.util
import json
import logging
import random
import sys
import weakref
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_patterns import _candles  # noqa: E402

from tradingbot.patterns import engine as PE  # noqa: E402
from tradingbot.patterns import SimilarPatternEngine  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BAR_4H = 14_400_000
H1 = 3_600_000


def _bench():
    spec = importlib.util.spec_from_file_location("bench_knn_query", ROOT / "scripts" / "bench_knn_query.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _bench()
SYMS13 = list(B.SYMS13)


def _ser(r) -> str:
    return json.dumps(r, sort_keys=True, default=repr)


@pytest.fixture()
def spy(monkeypatch):
    """`_select`'in her çağrıda SEÇTİĞİ listenin tamamı: (mesafe float'ı, seviye, olay kimliği)."""
    log: list = []
    real = SimilarPatternEngine._select

    def wrapped(self, cands, level, k, min_sep):
        chosen, pos = real(self, cands, level, k, min_sep)
        log.append([(d, lvl, id(e)) for d, lvl, e in chosen])
        return chosen, pos
    monkeypatch.setattr(SimilarPatternEngine, "_select", wrapped)
    return log


def _both(eng, spy, *a, **kw) -> tuple[dict, str]:
    """Aynı sorgu eski döngüyle ve hızlı yolla; sözlük, JSON ve seçilen listenin tamamı AYNI. Dönen: (sonuç, yol)."""
    eng.fast_query = False
    n0 = len(spy)
    old = eng.query(*a, **kw)
    sel_old = spy[n0:]
    eng.fast_query = True
    st = dict(eng.knn_stats)
    n1 = len(spy)
    new = eng.query(*a, **kw)
    sel_new = spy[n1:]
    assert new == old and _ser(new) == _ser(old), (a, kw)
    assert len(sel_old) <= 1 and len(sel_new) == len(sel_old), (a, kw, len(sel_old), len(sel_new))
    if sel_old:                                                       # mesafeler BİT BİT (float ==; -0.0/NaN yok)
        assert [(x[0].hex(), x[1], x[2]) for x in sel_new[-1]] == [(x[0].hex(), x[1], x[2]) for x in sel_old[0]], (a, kw)
    if eng.knn_stats["fast"] > st["fast"]:
        path = "fast"
    elif eng.knn_stats["fallback"] > st["fallback"]:
        path = "fallback"
    else:
        path = "none"                                                 # DATA_INVALID: sorgu vektörü yok (iki yolda aynı)
    return old, path


# ------------------------------------------------------------------ indeksler
@pytest.fixture(scope="module")
def real13():
    """13 seri × 400 4h bar (~3,5k olay): BTC bağlamı + funding; dört seride düz fiyat aralıkları, dördünde sıfır hacim."""
    hist = B.realistic_history(SYMS13, 400, now_ms=1_790_000_000_000, flat_every=200, zero_vol_every=150)
    with np.errstate(all="ignore"):
        eng = B.build_engine(hist)
    return eng


def _adversarial():
    """Eşitlik ve tekilleştirme çekişmesi: A ve B birebir aynı mumlar (her barda aynı mesafe → sembol sırası), A ayrıca
    spot 4h ve futures 1h (aynı (sembol, olay_ts) farklı seri), C ayrı, F uzun düz aralıklı (sıfır-sapmalı yollar). BTC
    bağlamı ve funding YOK → o sütunlar bütünüyle NaN (ölçekleyici NaN → standart değer 0)."""
    t0 = 1_700_000_000_000
    eng = SimilarPatternEngine(min_sample=8, horizon=12, clusters={"A/USDT": "k1", "B/USDT": "k1", "C/USDT": "k2"})
    a = _candles(330, seed=5, drift=0.0004, tf_ms=BAR_4H, start=t0)
    with np.errstate(all="ignore"):
        eng.add_series("A/USDT", "futures", "4h", a)
        eng.add_series("B/USDT", "futures", "4h", a.copy())
        eng.add_series("A/USDT", "spot", "4h", a.copy())
        eng.add_series("A/USDT", "futures", "1h", _candles(900, seed=6, tf_ms=H1, start=t0 + 100 * BAR_4H))
        eng.add_series("C/USDT", "futures", "4h", _candles(330, seed=7, drift=-0.0003, tf_ms=BAR_4H, start=t0))
        f = _candles(330, seed=8, tf_ms=BAR_4H, start=t0)
        for col in ("open", "high", "low", "close"):
            f.loc[140:230, col] = float(f.loc[139, "close"])
        f.loc[231, "close"] = float(f.loc[139, "close"]) * (1 + 1e-13)    # neredeyse düz: normalize edilmeyen yol
        eng.add_series("F/USDT", "futures", "4h", f)
    return eng


# ------------------------------------------------------------------ 1) rastgele, gerçekçi
@pytest.mark.parametrize("seed", [1, 2])
def test_randomized_queries_are_bit_identical_on_a_realistic_13_series_index(real13, spy, seed):
    eng = real13
    rng = random.Random(seed)
    n_bars = len(eng.candles[(SYMS13[0], "futures", "4h")])
    clusters = [{}, {s: f"k{i % 3}" for i, s in enumerate(SYMS13)}]
    paths = []
    try:
        for _ in range(28):
            eng.clusters = rng.choice(clusters)                       # sorgunun kümesi; olayların kümesi kurulumda sabit
            eng.min_sample = rng.choice([3, 30, 30, 500])
            eng.min_separation = rng.choice([None, None, 1, 40])
            eng.embargo_bars = rng.choice([0, 1, 1, 3])
            sym = rng.choice(SYMS13 + ["NOPE/USDT"])
            kw = {"k": rng.choice([0, 1, 5, 30, 60, 60, 150, 400]), "level": rng.choice(["auto", "auto", "same_coin", "cluster",
                                                                                            "universe", "x"]),
                  "window": rng.choice([16, 32, 64, 64, 128])}
            if rng.random() < 0.4:
                kw["idx"] = rng.randrange(100, n_bars)
            if rng.random() < 0.25:
                kw["query_ts"] = int(eng.candles[(SYMS13[0], "futures", "4h")]["timestamp"].iloc[rng.randrange(150, n_bars)]) \
                    + rng.choice([-1, 0, 1, BAR_4H - 1])
            if rng.random() < 0.25:
                kw["now_ts"] = 1_790_000_000_000 + rng.randrange(-200, 200) * 86_400_000
            _, p = _both(eng, spy, sym, "futures", "4h", rng.choice(["LONG", "SHORT"]), **kw)
            paths.append(p)
    finally:
        eng.clusters, eng.min_sample, eng.min_separation, eng.embargo_bars = {}, 30, None, 1
    assert paths.count("fast") >= 15, paths                          # anlamlı: sorguların çoğu hızlı yoldan geçti


def test_production_call_for_every_symbol_is_bit_identical(real13, spy):
    """Üretimdeki çağrı (`TradingEngineV3._evidence_query`: son bar, k=60, auto, pencere 64) — 13 sembol × 2 yön."""
    from tradingbot.engine_v3 import TradingEngineV3
    eng = real13
    for s in SYMS13:
        eng.fast_query = False
        old = TradingEngineV3._evidence_query(eng, s)
        eng.fast_query = True
        st = dict(eng.knn_stats)
        new = TradingEngineV3._evidence_query(eng, s)
        assert new == old and _ser(new) == _ser(old), s
        assert eng.knn_stats["fast"] == st["fast"] + 2, (s, eng.knn_stats)
        assert old["LONG"]["n"] > 0 and old["SHORT"]["n"] > 0


# ------------------------------------------------------------------ 2) çekişmeli
def test_adversarial_ties_duplicates_markets_and_nan_columns_are_bit_identical(spy):
    eng = _adversarial()
    ok = eng._knn_index().window(eng, 16)[1]
    assert not ok.all(), "düz seri iyi koşullu olmayan yollar üretmeli (her zaman eski kodla hesaplanan olaylar)"
    assert np.isnan(eng._mu).any(), "BTC/funding sütunları bütünüyle NaN olmalı"
    paths = []
    combos = [(sym, market, side, level, kk, w)
              for sym, market in (("A/USDT", "futures"), ("B/USDT", "futures"), ("C/USDT", "futures"), ("A/USDT", "spot"))
              for side in ("LONG", "SHORT") for level in ("auto", "same_coin", "cluster", "universe")
              for kk in ((1, 8, None), (25, 8, None), (60, 3, 30), (10 ** 6, 500, None)) for w in (16, 64)]
    for sym, market, side, level, (k, ms, sep), w in random.Random(0).sample(combos, 72):
        eng.min_sample, eng.min_separation = ms, sep
        _, p = _both(eng, spy, sym, market, "4h", side, k=k, level=level, window=w, idx=None if k != 25 else 250)
        paths.append(p)
    assert paths.count("fast") > 0.8 * len(paths), paths
    # birebir aynı iki seride mesafe eşitlikleri GERÇEKTEN oluştu (eşitlik sırası sembol → olay_ts → olay sırası)
    eng.min_sample, eng.min_separation = 8, None
    r = eng.query("C/USDT", "futures", "4h", "LONG", k=60, level="universe", window=16)
    nb = [(x["symbol"], x["event_ts"], x["distance"]) for x in r["neighbors"]]
    assert any(a[2] == b[2] and a[1] == b[1] and a[0] != b[0] for a, b in zip(nb, nb[1:])), nb


def test_a_query_on_a_flat_path_falls_back_to_the_old_loop_with_the_same_result(spy):
    eng = _adversarial()
    _, p = _both(eng, spy, "F/USDT", "futures", "4h", "LONG", idx=220, k=40, window=16)
    assert p == "fallback"                                           # sorgu yolu iyi koşullu değil → eski döngü (aynı)
    _, p = _both(eng, spy, "F/USDT", "futures", "4h", "LONG", idx=220, k=40, window=128)
    assert p == "fast"


def test_embargo_and_exit_boundaries_are_bit_identical_and_actually_hit(real13, spy):
    eng = real13
    sym = SYMS13[2]
    ix = eng._knn_index()
    rng = random.Random(4)
    picked = [j for j, e in enumerate(eng.events) if e.symbol == sym and "SHORT" in e.outcomes]
    step = BAR_4H
    hits = 0
    try:
        for j in rng.sample(picked, 6):
            e = eng.events[j]
            x_exit = int(eng.candles[(e.symbol, e.market, e.tf)]["timestamp"].iloc[e.outcomes["SHORT"].exit_idx])
            for emb in (0, 1, 2):
                eng.embargo_bars = emb
                for qts in (x_exit, x_exit + 1, e.cutoff_ts + emb * step, e.cutoff_ts + emb * step + 1):
                    _both(eng, spy, sym, "futures", "4h", "SHORT", query_ts=qts, k=400, level="same_coin")
                    got = eng._knn_candidates(ix, sym, "SHORT", qts, step, "same_coin", 64, "UP_MIDVOL", "default",
                                              np.zeros(len(PE.SNAP_COLS)), eng.events[0].path[64])
                    inside = got is not None and j in set(got[0].tolist())
                    want = e.cutoff_ts < qts - emb * step and x_exit < qts      # eski kuralın kendisi
                    assert inside == want, (j, emb, qts)
                    hits += int(not want)
    finally:
        eng.embargo_bars = 1
    assert hits > 0


def test_the_candidate_list_can_run_out_and_deep_min_sep_scans_stay_identical(real13, spy):
    eng = real13
    try:
        eng.min_separation = 60
        before = eng.knn_stats["exact"]
        for s in SYMS13[:3]:
            _both(eng, spy, s, "futures", "4h", "LONG", k=200, level="same_coin")          # tükenir (az aday)
            _both(eng, spy, s, "futures", "4h", "SHORT", k=10 ** 6, level="auto")         # bütün adaylar sırayla
        assert eng.knn_stats["exact"] - before > 3000                 # derin tarama gerçekten oldu
    finally:
        eng.min_separation = None


# ------------------------------------------------------------------ 3) kanıtın kendisi
def test_the_lower_bound_holds_for_every_candidate(real13):
    """lo(c) ≤ d(c) her adayda (d = eski kodun mesafesi); payın kullanılan kısmı sınırın yarısından az (f32 yuvarlaması)."""
    eng = real13
    ix = eng._knn_index()
    worst = 0.0
    n_checked = 0
    for s, side, w in ((SYMS13[0], "LONG", 64), (SYMS13[5], "SHORT", 16), (SYMS13[9], "LONG", 128), (SYMS13[12], "SHORT", 32)):
        qv = eng.query_vector(s, "futures", "4h")
        paths, snap, row = qv
        qz, qp = eng._std(snap), paths[w]
        qts = int(eng.frames[(s, "futures", "4h")]["cutoff_ts"].iloc[-1])
        cand, lo = eng._knn_candidates(ix, s, side, qts, BAR_4H, "x", w, PE.regime_code(row), "default", qz, qp)
        okw = ix.window(eng, w)[1]
        for c, l_ in zip(cand.tolist(), lo.tolist()):
            d = eng._pair_dist(qz, qp, eng.events[c], w)
            assert l_ <= d, (s, c, l_, d)
            if okw[c]:
                b = float(ix.zb[c])
                worst = max(worst, abs((l_ + b) - d) / b)
            n_checked += 1
    assert n_checked > 10_000 and worst < 0.75, (n_checked, worst)


@pytest.mark.parametrize("mode", ["random", "tight", "inf"])
def test_any_lower_bound_gives_the_same_selection(real13, spy, monkeypatch, mode):
    """2. aşamanın mantığı sayısal sınırdan bağımsız: geçerli HER alt sınır (rastgele, sıkı uç lo = d, kısmen −∞) aynı
    seçimi verir."""
    eng = real13
    real = SimilarPatternEngine._knn_candidates
    rng = np.random.default_rng({"random": 1, "tight": 2, "inf": 3}[mode])

    def fake(self, ix, symbol, side, qts, step, level, window, q_regime, cluster, qz, qp):
        got = real(self, ix, symbol, side, qts, step, level, window, q_regime, cluster, qz, qp)
        if got is None:
            return None
        cand, _ = got
        d = np.array([self._pair_dist(qz, qp, self.events[c], window) for c in cand.tolist()])
        if mode == "random":
            lo = d - rng.uniform(0, 0.05, d.size)
        elif mode == "tight":
            lo = d.copy()
        else:
            lo = np.where(rng.random(d.size) < 0.3, -np.inf, d - 1e-9)
        return cand, lo
    monkeypatch.setattr(SimilarPatternEngine, "_knn_candidates", fake)
    for s, side, level, k in ((SYMS13[1], "LONG", "auto", 60), (SYMS13[3], "SHORT", "universe", 150),
                              (SYMS13[7], "LONG", "same_coin", 30), (SYMS13[11], "SHORT", "auto", 1)):
        _, p = _both(eng, spy, s, "futures", "4h", side, k=k, level=level)
        assert p == "fast"


# ------------------------------------------------------------------ 4) geri dönüş, yedek, önbellek, bellek
def test_kill_switch_never_enters_the_fast_path(real13, monkeypatch):
    eng = real13
    called = []
    monkeypatch.setattr(SimilarPatternEngine, "_knn_select", lambda self, *a, **k: called.append(1))
    eng.fast_query = False
    eng.query(SYMS13[0], "futures", "4h", "LONG", k=60)
    assert not called
    eng.fast_query = True
    assert SimilarPatternEngine.fast_query is True and SimilarPatternEngine(min_sample=30).fast_query is True


def test_an_internal_error_falls_back_with_the_same_result_and_warns_once(spy, monkeypatch, caplog):
    eng = _adversarial()
    want = []
    eng.fast_query = False
    for s in ("A/USDT", "C/USDT"):
        want.append(eng.query(s, "futures", "4h", "LONG", k=40))
    eng.fast_query = True

    def boom(self, *a, **k):
        raise RuntimeError("sentetik")
    with monkeypatch.context() as mp:
        mp.setattr(SimilarPatternEngine, "_knn_select", boom)
        with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.engine"):
            got = [eng.query(s, "futures", "4h", "LONG", k=40) for s in ("A/USDT", "C/USDT")]
    assert got == want and eng.knn_stats["fallback"] == 2 and "sentetik" in eng.knn_stats["error"]
    assert len([r for r in caplog.records if "hızlı yolu" in r.getMessage()]) == 1
    # dizi KURULAMAZSA: bu motorda bir daha denenmez (her sorguda bellek/CPU harcamaz), sonuç aynı
    eng2 = _adversarial()
    builds = []

    def no_mem(self, e):
        builds.append(1)
        raise MemoryError("sentetik bellek")
    with monkeypatch.context() as mp:
        mp.setattr(PE._KnnIndex, "__init__", no_mem)
        got2 = [eng2.query(s, "futures", "4h", "LONG", k=40) for s in ("A/USDT", "C/USDT")]
    assert got2 == want and builds == [1] and eng2._knn_broken and eng2._knn_ix is None
    # alt süreçte (kuran süreç değil) uyarı yazılmaz
    eng3 = _adversarial()
    eng3._knn_pid = -1
    caplog.clear()
    with monkeypatch.context() as mp:
        mp.setattr(SimilarPatternEngine, "_knn_select", boom)
        with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.engine"):
            assert eng3.query("A/USDT", "futures", "4h", "LONG", k=40) == want[0]
    assert not [r for r in caplog.records if "hızlı yolu" in r.getMessage()]


def test_lossy_timestamp_columns_never_enter_the_fast_path(spy):
    """Gözden geçirme bulgusu (2026-10-06; denetçinin yeniden üretimi): NA'lı pandas `Int64` zaman damgası sütunu
    `to_numpy()` ile float64'e döner ve 2^53 üstü değerler YUVARLANIR → vektörel çıkış damgası eski `iloc` değerinden
    ayrılır, 1. aşamanın çıkış süzgeci farklı aday kümesi seçerdi (2. aşama çıkışı yeniden denetlemez). Artık yalnız
    kayıpsız int64 dönüşümü kabul edilir; değilse motor BİR KEZ işaretlenir ve her sorgu eski döngüyle (aynı sonuç)."""
    eng = _adversarial()
    key = ("C/USDT", "futures", "4h")
    df = eng.candles[key].copy()
    ts = pd.array([2 ** 60 + 2 * i + 1 for i in range(len(df))], dtype="Int64")
    ts[2] = pd.NA                                                   # hiçbir olayın çıkış barı değil (olaylar idx ≥ 128)
    df["timestamp"] = ts
    eng.candles[key] = df
    assert df["timestamp"].to_numpy().dtype == np.float64            # kayıplı dönüşümün kendisi (pandas)
    assert int(df["timestamp"].to_numpy()[300]) != int(df["timestamp"].iloc[300])
    with pytest.raises(TypeError):
        PE._ts_values(df["timestamp"])
    evs = [e for e in eng.events if e.symbol == "C/USDT" and "LONG" in e.outcomes]
    tried = 0
    for e in evs[len(evs) // 3: len(evs) // 3 + 12]:
        qts = int(df["timestamp"].iloc[e.outcomes["LONG"].exit_idx])  # tam çıkış anında sorgu: sınır olayı
        for level in ("same_coin", "auto"):
            _res, path = _both(eng, spy, "C/USDT", "futures", "4h", "LONG", query_ts=qts, k=60, level=level,
                               idx=min(e.idx + 30, len(df) - 2))
            assert path == "fallback"
            tried += 1
    assert tried == 24 and eng.knn_stats["fast"] == 0 and eng._knn_ix is None
    assert eng._knn_broken.startswith("TypeError") and "timestamp" in eng._knn_broken


@pytest.mark.parametrize("kind", ["int64", "int32", "uint64", "float64", "float32"])
def test_lossless_timestamp_dtypes_are_accepted_and_equal_the_old_lookup(kind):
    s = pd.Series(np.array([1_700_000_000_000 + 14_400_000 * i for i in range(50)], dtype=np.int64))
    if kind == "int32":
        s = pd.Series(np.arange(50, dtype=np.int32) * 7 - 3)
    elif kind == "float32":
        s = pd.Series(np.arange(50, dtype=np.float32) * 4.0)
    else:
        s = s.astype(kind)
    a = PE._ts_values(s)
    assert a.dtype == np.int64 and [int(a[i]) for i in range(len(s))] == [int(s.iloc[i]) for i in range(len(s))]


@pytest.mark.parametrize("bad", ["nullable_no_na", "object", "float_nan", "float_fraction", "uint_overflow", "datetime",
                                 "float_huge"])
def test_lossy_or_non_numeric_timestamp_dtypes_are_refused(bad):
    base = np.array([1_700_000_000_000 + 14_400_000 * i for i in range(10)], dtype=np.int64)
    s = {"nullable_no_na": lambda: pd.Series(pd.array(base, dtype="Int64")),
         "object": lambda: pd.Series(base.astype(object)),
         "float_nan": lambda: pd.Series(np.where(np.arange(10) == 3, np.nan, base.astype(float))),
         "float_fraction": lambda: pd.Series(base.astype(float) + 0.5),
         "uint_overflow": lambda: pd.Series(np.array([2 ** 63 + 5, 1], dtype=np.uint64)),
         "datetime": lambda: pd.Series(pd.to_datetime(base, unit="ms")),
         "float_huge": lambda: pd.Series(np.array([2.0 ** 63, 1.0]))}[bad]()
    with pytest.raises(TypeError):
        PE._ts_values(s)


_REAL_TS_VALUES = PE._ts_values


def test_an_array_build_failure_is_cached_per_engine_and_warned_once(spy, monkeypatch, caplog):
    """Gözden geçirme bulgusu (2026-10-06): tembel yön/pencere dizisinin kurulumu (ör. bozuk bir olay ya da kayıplı zaman
    damgası) düşerse motor İŞARETLENİR — sonraki sorgular diziyi yeniden kurmaya çalışıp düşmez, doğrudan eski döngüye
    gider (sonuç aynı), uyarı motor başına bir kez."""
    eng = _adversarial()
    calls = []

    def bad_ts(s):
        calls.append(1)
        raise TypeError("sentetik kayıplı zaman damgası")
    monkeypatch.setattr(PE, "_ts_values", bad_ts)
    with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.engine"):
        for s in ("A/USDT", "C/USDT", "A/USDT"):
            for side in ("LONG", "SHORT"):
                _res, path = _both(eng, spy, s, "futures", "4h", side, k=40)
                assert path == "fallback"
    assert calls == [1], "yön dizisi her sorguda yeniden kurulmamalı"
    assert eng._knn_broken.startswith("TypeError") and eng._knn_ix is None and eng.knn_stats["fast"] == 0
    assert len([r for r in caplog.records if "hızlı yolu kurulamadı" in r.getMessage()]) == 1
    # pencere dizisi de aynı kurala uyar
    eng2 = _adversarial()
    wcalls = []
    real_unit = PE._unit_rows

    def bad_unit(p):
        if p.shape[0] > 1:                                            # yalnız olay dizisi (sorgu yolu tek satır)
            wcalls.append(1)
            raise MemoryError("sentetik bellek")
        return real_unit(p)
    monkeypatch.setattr(PE, "_ts_values", _REAL_TS_VALUES)
    monkeypatch.setattr(PE, "_unit_rows", bad_unit)
    for s in ("A/USDT", "C/USDT"):
        _res, path = _both(eng2, spy, s, "futures", "4h", "LONG", k=40)
        assert path == "fallback"
    assert wcalls == [1] and eng2._knn_broken.startswith("MemoryError") and eng2._knn_ix is None


def test_the_arrays_follow_the_index_and_are_rebuilt_after_add_series(spy):
    eng = _adversarial()
    _both(eng, spy, "C/USDT", "futures", "4h", "LONG", k=60)
    ix1 = eng._knn_ix
    assert ix1 is not None
    _both(eng, spy, "C/USDT", "futures", "4h", "SHORT", k=60)
    assert eng._knn_ix is ix1, "aynı indekste diziler yeniden kurulmamalı"
    with np.errstate(all="ignore"):
        eng.add_series("D/USDT", "futures", "4h", _candles(300, seed=9, tf_ms=BAR_4H, start=1_700_000_000_000))
    _both(eng, spy, "C/USDT", "futures", "4h", "LONG", k=60)          # yeni olaylar + yeni ölçekleyici
    assert eng._knn_ix is not ix1 and eng._knn_ix.n == len(eng.events)


def test_extra_memory_is_bounded_and_freed_with_the_engine():
    eng = _adversarial()
    eng.query("C/USDT", "futures", "4h", "LONG", k=60)
    eng.query("C/USDT", "futures", "4h", "SHORT", k=60)
    n = len(eng.events)
    per_event = eng.knn_index_nbytes() / n
    assert per_event <= 100 + 2 * 9 + 66 + 1, per_event               # belge §5: 100 B + 9 B/yön + 66 B/pencere
    ref = weakref.ref(eng)
    ix_ref = weakref.ref(eng._knn_ix)
    del eng
    gc.collect()
    assert ref() is None and ix_ref() is None, "diziler motorla birlikte serbest kalmalı (dış referans yok)"


# ------------------------------------------------------------------ 5) config, karar kimliği, kurulum, log
def test_config_switch_is_bool_only_default_on_and_not_part_of_the_decision_identity(tmp_path, monkeypatch):
    from dataclasses import asdict

    import test_engine_v3 as TE

    from tradingbot.config_v3 import load_v3
    from tradingbot.core import payload_hash
    from tradingbot.core.errors import ConfigError
    assert load_v3({}).history.evidence_fast_knn is True
    assert load_v3({"history": {"evidence_fast_knn": False}}).history.evidence_fast_knn is False
    with pytest.raises(ConfigError, match="evidence_fast_knn"):
        load_v3({"history": {"evidence_fast_knn": "false"}})           # tırnaklı YAML değeri: açık hata
    eng = TE._engine(tmp_path, monkeypatch)
    d = asdict(eng.cfg.v3)
    d.pop("shared_experience", None)
    if (d.get("learning_mode") or {}).get("extra_entries") == "open":
        d["learning_mode"].pop("extra_entries", None)
    d["history"].pop("evidence_subprocess")
    d["history"].pop("evidence_fast_knn")
    d.pop("m2x_aggressive", None)
    want = payload_hash(d)                                            # alan eklenmeden önceki özet
    hashes = set()
    for on in (True, False):
        eng.cfg.v3.history.evidence_fast_knn = on
        eng.__dict__.pop("_config_hash_cache", None)
        hashes.add(eng.config_hash())
    assert hashes == {want}


class _Store:
    """`_build_pattern_index`'in okuduğu kadarı: series() ve read()."""

    def __init__(self, frames):
        self.frames = frames

    def series(self):
        return [("futures", s, "4h") for s in self.frames]

    def read(self, market, symbol, tf, *a, **k):
        if tf == "4h" and symbol in self.frames:
            return self.frames[symbol]
        return pd.DataFrame()


def test_the_index_builder_applies_the_switch_and_startup_logs_it(tmp_path, monkeypatch, caplog):
    import test_engine_v3 as TE
    eng = TE._engine(tmp_path, monkeypatch)
    frames = {s: _candles(260, seed=40 + i, tf_ms=BAR_4H, start=1_700_000_000_000) for i, s in enumerate(eng.cfg.coins)}
    monkeypatch.setattr(eng, "_history_store", lambda: _Store(frames))
    for on in (True, False):
        eng.cfg.v3.history.evidence_fast_knn = on
        with np.errstate(all="ignore"):
            idx, last = eng._build_pattern_index(list(eng.cfg.coins))
        assert idx is not None and idx.fast_query is on and set(last) == {f"{s}|futures|4h" for s in frames}
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="tradingbot.engine_v3"):
            eng._log_evidence_fast_knn_setting()
        line = [r.getMessage() for r in caplog.records if "kNN sorgusu" in r.getMessage()]
        assert line and (("HIZLI" in line[0]) if on else ("ESKİ" in line[0])), line


def test_the_cli_pattern_engine_applies_the_switch(tmp_path, monkeypatch):
    """Gözden geçirme bulgusu (2026-10-06): anahtar kanıt için SimilarPatternEngine kuran HER yere ulaşır — worker
    (`_build_pattern_index`, yukarıda) ve CLI'nin `_pattern_engine`'i: `pattern-query`, `evidence-show --live` ve
    `historical-replay` (ReplayEngine kendisine verilen bu motoru sorgular; kendi motorunu kurmaz)."""
    import inspect
    import types

    import tradingbot.history as H
    from tradingbot import cli_v3
    from tradingbot.config_v3 import load_v3
    from tradingbot.replay import engine as RE
    frames = {s: _candles(260, seed=60 + i, tf_ms=BAR_4H, start=1_700_000_000_000) for i, s in enumerate(("A/USDT", "C/USDT"))}

    class _HS:
        def __init__(self, root):
            pass

        def series(self):
            return [("futures", s, "4h") for s in frames]

        def read(self, market, symbol, tf, *a, **k):
            return frames[symbol] if (market, tf) == ("futures", "4h") and symbol in frames else pd.DataFrame()
    monkeypatch.setattr(H, "HistoryStore", _HS)
    args = types.SimpleNamespace(min_sample=30, horizon=24, stride=1)
    for raw, want in (({}, True), ({"history": {"evidence_fast_knn": True}}, True),
                      ({"history": {"evidence_fast_knn": False}}, False)):
        cfg = types.SimpleNamespace(cache_path=tmp_path, v3=load_v3(raw))
        with np.errstate(all="ignore"):
            _store, eng, n_ev = cli_v3._pattern_engine(cfg, args, market="futures", tf="4h")
        assert n_ev > 0 and eng.fast_query is want, (raw, eng.fast_query)
    src = inspect.getsource(RE)
    assert "SimilarPatternEngine(" not in src and "self.pattern_engine.query(" in src


# ------------------------------------------------------------------ 6) gerçek turlar: eski döngü ile hızlı yol, state/ bayt bayt
def test_real_tours_across_index_publishes_are_byte_identical_with_the_old_loop(tmp_path, monkeypatch):
    """`test_tour_perf_no_decision_change_v1` düzeneği (gerçek `tour()` × 5, iki yayım, ön ısıtma AÇIK, süreç içi =
    VPS ayarı): hızlı yol ve eski döngü (`fast_query=False`) ile `state/` altındaki HER dosya bayt bayt aynı."""
    pytest.importorskip("time_machine")
    import test_tour_perf_no_decision_change_v1 as TP

    from tradingbot.engine_v3 import TradingEngineV3
    runs = {}
    for name, fast in (("fast", True), ("old", False)):
        with monkeypatch.context() as mp:
            mp.setattr(TradingEngineV3, "_evidence_subprocess_on", lambda self: False)
            mp.setattr(SimilarPatternEngine, "fast_query", fast)
            runs[name] = TP._run(tmp_path / name, monkeypatch, optimized=True)
    assert runs["fast"]["versions"] == runs["old"]["versions"] == [1, 2, 2, 3, 3]
    assert TP._diff(runs["fast"], runs["old"]) == []
    assert any(k.startswith("state/evidence/") for k in runs["fast"]["files"])
    st_fast = runs["fast"]["eng"]._refresher.bundle.engine.knn_stats
    st_old = runs["old"]["eng"]._refresher.bundle.engine.knn_stats
    assert st_fast["fast"] > 0 and st_fast["legacy"] == 0 and st_fast["fallback"] == 0, st_fast
    assert st_old["legacy"] > 0 and st_old["fast"] == 0, st_old


# ------------------------------------------------------------------ 7) depodaki diğer testlerin indeks verisi
def _tour_fixture_indexes():
    """tests/test_tour_perf_no_decision_change_v1.py `_index_history`/`_index` (3 sürüm) ve
    tests/test_evidence_subprocess_v1.py `_history`/`_build_index` ile AYNI üreteç ve tohumlar."""
    out = []
    now_ms = 1_789_995_600_000
    syms = ["ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT"]
    start = now_ms - now_ms % BAR_4H - 420 * BAR_4H
    hist = {s: _candles(420, seed=31 + i, drift=0.0003 * (i - 1), tf_ms=BAR_4H, start=start) for i, s in enumerate(syms)}
    for drop in (2, 1, 0):
        eng = SimilarPatternEngine(min_sample=30, horizon=24)
        for s, df in hist.items():
            eng.add_series(s, "futures", "4h", df.iloc[: len(df) - drop].reset_index(drop=True) if drop else df)
        out.append((eng, syms))
    syms2 = ["ETH/USDT", "SOL/USDT", "AVAX/USDT"]
    hist2 = {s: _candles(420, seed=11 + i, drift=0.0004 * (i - 1), tf_ms=BAR_4H, start=start) for i, s in enumerate(syms2)}
    eng = SimilarPatternEngine(min_sample=30, horizon=24)
    for s, df in hist2.items():
        eng.add_series(s, "futures", "4h", df)
    out.append((eng, syms2))
    return out


def test_replay_on_the_repositorys_tour_fixture_indexes_is_bit_identical():
    from tradingbot.engine_v3 import TradingEngineV3
    with np.errstate(all="ignore"):
        idxs = _tour_fixture_indexes()
    for eng, syms in idxs:
        for s in syms:
            eng.fast_query = False
            old = TradingEngineV3._evidence_query(eng, s)
            eng.fast_query = True
            new = TradingEngineV3._evidence_query(eng, s)
            assert new == old and _ser(new) == _ser(old), s
        assert eng.knn_stats["fast"] == 2 * len(syms), eng.knn_stats
