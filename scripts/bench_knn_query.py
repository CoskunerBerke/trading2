#!/usr/bin/env python3
"""kNN KANIT SORGUSU ÖLÇÜMÜ (2026-10-06) — eski olay-başına döngü ile hızlı yol, AYNI indekste, ağsız ve durum dosyasız.

Soru: `SimilarPatternEngine.query` (pattern kanıtı) VPS ölçeğinde (~150k olay, 13 seri) sorgu başına ne kadar sürüyor ve
13 sembollük ön ısıtma (sembol başına LONG + SHORT, `TradingEngineV3._evidence_query` ile aynı çağrı) ne kadar tutuyor?
Hızlı yolun sonucu eskisiyle BİREBİR aynı mı (dönen sözlüklerin `==`'i ve JSON serileştirmesi)? Ek bellek ne kadar?

Veri sentetiktir ama üretimdeki biçimdedir (`realistic_history`): 4h futures mumları, rejim değiştiren oynaklık, kalın
kuyruklar, BTC bağlamı ve funding serisi (üretimdeki `_build_pattern_index` gibi), seyrek sıfır hacim ve düz fiyat
aralıkları (NaN özellikler ve sıfır-sapmalı yollar). Kümeler boş (üretim config'i gibi): her yabancı olay "cluster"
seviyesindedir.

Kullanım:
  python scripts/bench_knn_query.py                     # ~150k olay; hızlı ön ısıtma + eski yol 2 sembolde (LONG+SHORT)
  python scripts/bench_knn_query.py --legacy-symbols 13 # eski yol bütün ön ısıtmada (yerelde ~15 dk)
  python scripts/bench_knn_query.py --bars 1500         # küçük indeks (hızlı deneme)

VPS'teki worker'ın yanında koşturmak onunla CPU yarıştırır; ayrı bir makinede koşturun. Sonuçlar: docs/TOUR_CONTENTION_V2.md.
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BAR_4H = 14_400_000
SYMS13 = ["BTC/USDT", "ETH/USDT", "ZEC/USDT", "SOL/USDT", "NEAR/USDT", "UNI/USDT", "XRP/USDT", "ONE/USDT", "BNB/USDT",
          "ARB/USDT", "DOGE/USDT", "LSK/USDT", "SUI/USDT"]


def realistic_candles(n: int, *, seed: int, start: int, tf_ms: int = BAR_4H, flat_every: int = 0, zero_vol_every: int = 0):
    """Rejim değiştiren oynaklık (GARCH benzeri), Student-t kuyruk, yavaş değişen eğilim. `flat_every` > 0: o aralıkla
    70 barlık DÜZ fiyat (sıfır-sapmalı yollar; 16 ve 32 pencerede tam sıfır, kısmen düz uçlarda yaklaşık sabit yol);
    `zero_vol_every` > 0: o aralıkla 60 barlık sıfır hacim (rel_vol/vol_z NaN → standardize sıfır)."""
    rng = np.random.default_rng(seed)
    vol = np.empty(n)
    v = 0.012
    for i in range(n):                                       # oynaklık rejimi: yavaş ortalamaya dönüş + şoklar
        v = 0.97 * v + 0.03 * 0.012 + (0.004 * rng.standard_normal() if rng.random() < 0.02 else 0.0)
        vol[i] = min(max(v, 0.003), 0.06)
    drift = 0.0006 * np.sin(np.arange(n) / (250 + 40 * (seed % 7)) + seed)
    r = drift + vol * rng.standard_t(4, n) / np.sqrt(2.0)
    if flat_every:
        for s in range(flat_every // 2, n - 80, flat_every):
            r[s:s + 70] = 0.0
            r[s + 70] = 1e-13                                # neredeyse düz: normalize EDİLMEYEN küçük yol (sd ≤ 1e-12)
    c = 100.0 * (1 + seed % 5) * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 1, n) * vol)
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 1, n) * vol)
    if flat_every:
        flat = r == 0.0
        h = np.where(flat, np.maximum(o, c), h)
        lo = np.where(flat, np.minimum(o, c), lo)
    vv = rng.lognormal(4.5, 0.6, n)
    if zero_vol_every:
        for s in range(zero_vol_every // 3, n - 60, zero_vol_every):
            vv[s:s + 60] = 0.0
    ts = start + np.arange(n, dtype=np.int64) * tf_ms
    return pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": vv,
                         "quote_volume": vv * c, "trades": 10, "taker_buy_base": vv * 0.5, "taker_buy_quote": vv * c * 0.5,
                         "close_time": ts + tf_ms - 1})


def realistic_funding(start: int, end: int, *, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(10_000 + seed)
    ts = np.arange(start - start % 28_800_000, end, 28_800_000, dtype=np.int64)
    rate = 0.0001 + np.cumsum(rng.normal(0, 0.00003, len(ts))) * 0.1
    return pd.DataFrame({"timestamp": ts, "rate": rate})


def realistic_history(symbols, n_bars: int, *, now_ms: int | None = None, flat_every: int = 0, zero_vol_every: int = 0):
    """{sembol: (mumlar, funding)}; son bar şimdiden bir bar önce kapanmış."""
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    start = now_ms - now_ms % BAR_4H - n_bars * BAR_4H
    out = {}
    for i, s in enumerate(symbols):
        df = realistic_candles(n_bars, seed=101 + 13 * i, start=start, flat_every=flat_every if i % 4 == 1 else 0,
                               zero_vol_every=zero_vol_every if i % 3 == 2 else 0)
        out[s] = (df, realistic_funding(start, start + n_bars * BAR_4H, seed=i))
    return out


def build_engine(hist, *, clusters=None, **kw):
    """Üretimdeki `_build_pattern_index` sırası: BTC bağlamı (BTC dışındaki serilere) + funding."""
    from tradingbot.patterns import SimilarPatternEngine
    eng = SimilarPatternEngine(min_sample=kw.pop("min_sample", 30), horizon=kw.pop("horizon", 24), clusters=clusters, **kw)
    btc = hist.get("BTC/USDT", (None, None))[0]
    for s, (df, fund) in hist.items():
        eng.add_series(s, "futures", "4h", df, btc_df=btc if (s != "BTC/USDT" and btc is not None) else None,
                       funding_df=fund)
    return eng


def _ser(ev) -> str:
    return json.dumps(ev, sort_keys=True, default=repr)


def _rss_kb() -> dict:
    out = {}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(("VmRSS", "VmHWM")):
                k, v = line.split(":")
                out[k] = int(v.split()[0])
    except OSError:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bars", type=int, default=11_860, help="sembol başına 4h bar (13 sembol; 11860 ≈ 152k olay)")
    ap.add_argument("--symbols", type=int, default=13)
    ap.add_argument("--legacy-symbols", type=int, default=2, help="eski yolun koşacağı sembol sayısı (LONG+SHORT)")
    ap.add_argument("--json", type=str, default="", help="özetin yazılacağı dosya")
    a = ap.parse_args()
    syms = SYMS13[: a.symbols] if a.symbols <= len(SYMS13) else [f"S{i:02d}/USDT" for i in range(a.symbols)]
    t0 = time.perf_counter()
    hist = realistic_history(syms, a.bars, flat_every=1500, zero_vol_every=2000)
    eng = build_engine(hist)
    build_s = time.perf_counter() - t0
    print(f"indeks: {len(eng.events)} olay, {len(eng.candles)} seri, kurulum {build_s:.1f} sn", flush=True)

    def evidence(sym):                                       # = TradingEngineV3._evidence_query
        return {side: eng.query(sym, "futures", "4h", side, k=60) for side in ("LONG", "SHORT")}

    # --- hızlı yol: ilk sorgu tembel dizileri kurar (bellek ölçümü), sonra 13 sembollük ön ısıtma
    eng.fast_query = True
    gc.collect()
    rss0 = _rss_kb()
    tracemalloc.start()
    m0 = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    t = time.perf_counter()
    first = eng.query(syms[0], "futures", "4h", "LONG", k=60)
    first_s = time.perf_counter() - t
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss1 = _rss_kb()
    retained, peak_extra = cur - m0, peak - m0
    print(f"ilk hızlı sorgu (dizi kurulumu dahil, tracemalloc açık — yavaşlatır): {first_s:.2f} sn; kalan ek bellek "
          f"{retained / 2**20:.1f} MB, tepe {peak_extra / 2**20:.1f} MB (tracemalloc); RSS {rss0} → {rss1}", flush=True)
    # Ön ısıtma SOĞUK başlar (üretimdeki gibi: yayımdan sonraki ilk sorgu dizileri kurar) — süre tracemalloc'suz.
    eng._knn_ix = None
    st0 = dict(eng.knn_stats)
    fast, fast_t = {}, {}
    t_all = time.perf_counter()
    for s in syms:
        t = time.perf_counter()
        fast[s] = evidence(s)
        fast_t[s] = time.perf_counter() - t
    fast_total = time.perf_counter() - t_all
    st1 = dict(eng.knn_stats)
    assert fast[syms[0]]["LONG"] == first, "aynı sorgu iki kez farklı sonuç verdi"
    warm_t = {}
    for s in syms:
        t = time.perf_counter()
        assert evidence(s) == fast[s], "sıcak dizilerle sonuç farklı"
        warm_t[s] = time.perf_counter() - t
    warm_total = sum(warm_t.values())
    tracemalloc.start()                                      # sıcak bir sorgunun geçici belleği (diziler hazır)
    w0 = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    eng.query(syms[-1], "futures", "4h", "SHORT", k=60)
    query_peak = tracemalloc.get_traced_memory()[1] - w0
    tracemalloc.stop()
    print(f"hızlı ön ısıtma (soğuk, dizi kurulumu dahil): {len(syms)} sembol, {fast_total:.2f} sn (ilk sembol "
          f"{fast_t[syms[0]]:.2f} sn); sıcak diziyle {warm_total:.2f} sn (sorgu başına {warm_total / (2 * len(syms)):.3f} sn); "
          f"sıcak sorgunun geçici tepe belleği {query_peak / 2**20:.1f} MB; sayaç {st0} → {st1}", flush=True)

    # --- eski yol (geri dönüş anahtarı) aynı indekste; sonuçlar birebir karşılaştırılır
    eng.fast_query = False
    legacy_t, same = {}, {}
    for s in syms[: a.legacy_symbols]:
        t = time.perf_counter()
        old = evidence(s)
        legacy_t[s] = time.perf_counter() - t
        same[s] = bool(old == fast[s] and _ser(old) == _ser(fast[s]))
        print(f"  eski yol {s}: {legacy_t[s]:.1f} sn (LONG+SHORT), hızlı (sıcak) {warm_t[s]:.3f} sn, "
              f"x{legacy_t[s] / max(warm_t[s], 1e-9):.0f}, aynı={same[s]}", flush=True)
    eng.fast_query = True
    per_sym_old = sum(legacy_t.values()) / max(1, len(legacy_t))
    per_sym_new = sum(warm_t[s] for s in legacy_t) / max(1, len(legacy_t))
    legacy_prewarm = per_sym_old * len(syms)
    summary = {
        "events": len(eng.events), "series": len(eng.candles), "build_s": round(build_s, 1),
        "fast_first_query_traced_s": round(first_s, 3), "fast_prewarm_cold_s": round(fast_total, 3),
        "fast_first_symbol_cold_s": round(fast_t[syms[0]], 3), "fast_prewarm_warm_s": round(warm_total, 3),
        "fast_per_query_warm_s": round(warm_total / (2 * len(syms)), 4),
        "legacy_symbols": len(legacy_t), "legacy_per_symbol_s": round(per_sym_old, 2),
        "legacy_prewarm_s" if len(legacy_t) == len(syms) else "legacy_prewarm_estimate_s": round(legacy_prewarm, 1),
        "speedup_per_symbol_warm": round(per_sym_old / max(per_sym_new, 1e-9), 1) if legacy_t else None,
        "speedup_prewarm_cold": round(legacy_prewarm / max(fast_total, 1e-9), 1) if legacy_t else None,
        "identical": all(same.values()) if same else None, "identical_symbols": sorted(s for s, v in same.items() if v),
        "extra_retained_mb": round(retained / 2**20, 1), "extra_peak_mb": round(peak_extra / 2**20, 1),
        "warm_query_peak_mb": round(query_peak / 2**20, 1),
        "knn_index_mb": round(eng.knn_index_nbytes() / 2**20, 1), "knn_stats": dict(eng.knn_stats),
        "rss_kb_before": rss0, "rss_kb_after_first": rss1,
    }
    print(json.dumps(summary, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return 0 if summary["identical"] in (True, None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
