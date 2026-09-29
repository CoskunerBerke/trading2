# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — T2: kaynak paritesi, geç hesaplama, erteleme ve önbellek (2026-09-29).

docs/ortak_deneyim/SPEC_V1.md §4.1/§4.5 + §13 T2: aynı seri DÖRT yoldan — laboratuvar DataFrame'i
(`signal_lab.load_series`), motor çerçevesi (`data.prepare`, dt indeksi, kapanmamış kuyruk), Formasyon `DataService`
satırları (240, sahte besleme), `CsvCandleCache.write/read` gidiş-dönüşü — bit-aynı pencere, anlık görüntü ve `input_sha`
verir. Giriş turundaki çerçeveler ile üç tur (ve bir 4h sınırı) sonraki çerçeveler aynı `as_of` için aynı sonucu verir.
Bar sınırından sonraki Box olayı ilk adımda eksik (PENDING/GAP), sonraki turda tam (OK) ve tam kaynaklarla aynıdır.
`SituationCache`: aynı barlar → AYNI çekirdek nesnesi (gerçek ve karşı-olgusal, farklı kitaplar); eksik pencere
önbelleğe girmez; LRU. Her test eski kodda (7ec832c) düşer: `tradingbot.shared_experience` paketi yoktur.

Not (2026-09-29): borsa değerleri kısa ondalık dizgelerdir ve CSV gidiş-dönüşünde bit-aynı kalır; fikstür bu yüzden
tick'e yuvarlanmış değer kullanır. Rastgele tam hassasiyetli ikili kayan noktalar pandas'ın varsayılan C ayrıştırıcısında
~%14 oranında 1 ulp kayar (CsvCandleCache.read `float_precision` vermiyor) — bu, bu katmanın değil CSV önbelleğinin
önceden var olan özelliğidir; ilgili test açıkça belgeler."""
from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.data import prepare  # noqa: E402
from tradingbot.market.providers import FUTURES  # noqa: E402
from tradingbot.pattern_trader.data import BARS_PER_TF, CsvCandleCache, DataService  # noqa: E402
from tradingbot.shared_experience import situation as S  # noqa: E402
from tradingbot.shared_experience.cache import SituationCache  # noqa: E402
from tradingbot.signal_lab import cache_path as lab_cache_path  # noqa: E402
from tradingbot.signal_lab import load_series  # noqa: E402

H4, H1, M = 14_400_000, 3_600_000, 60_000
AS_OF = (1_790_000_000_000 // H4) * H4 + 5 * M            # Pazartesi 12:05 UTC
BOUNDARY = (AS_OF // H4) * H4                             # 12:00 (4h ve 1h sınırı)
LAST4 = BOUNDARY - H4                                     # 08:00 açılışlı son kapanmış 4h
LAST1 = BOUNDARY - H1                                     # 11:00 açılışlı son kapanmış 1h
SYM, BTC = "SOL/USDT", "BTC/USDT"
COLS = ["timestamp", "open", "high", "low", "close", "volume"]


def _lcg(seed: int):
    x = seed & 0xFFFFFFFF
    while True:
        x = (1664525 * x + 1013904223) & 0xFFFFFFFF
        yield x / 4294967296.0


def make_bars(n: int, step: int, first_open: int, seed: int, base: float, dec: int) -> list[dict]:
    import math
    g = _lcg(seed)
    out, c = [], round(base, dec)
    for i in range(n):
        o = c
        r = (next(g) - 0.5) * 0.03 + 0.006 * math.sin(i / 23.0 + seed)
        c = round(o * (1 + r), dec)
        hi = round(max(o, c) * (1 + next(g) * 0.008), dec)
        lo = round(min(o, c) * (1 - next(g) * 0.008), dec)
        v = round(1000.0 * (0.3 + next(g)) * (1.2 + math.sin(i / 11.0)), 3)
        out.append({"timestamp": first_open + i * step, "open": o, "high": hi, "low": lo, "close": c, "volume": v})
    return out


#: Ana seriler: 4h/BTC-4h 12:00'den 20 gün SONRASINA kadar (geç hesaplama için), 1h aynı şekilde. Her yol kendi
#: doğal boyunu ve kendi "şimdi"sini görür; as_of sonrası barlar yalnız sonraki turların çerçevelerinde vardır.
N4_BEFORE, N1_BEFORE, N_AFTER4, N_AFTER1 = 900, 600, 120, 480
M4 = make_bars(N4_BEFORE + N_AFTER4, H4, LAST4 - (N4_BEFORE - 1) * H4, 11, 150.0, 4)
M1 = make_bars(N1_BEFORE + N_AFTER1, H1, LAST1 - (N1_BEFORE - 1) * H1, 12, 150.0, 4)
MB = make_bars(N4_BEFORE + N_AFTER4, H4, LAST4 - (N4_BEFORE - 1) * H4, 13, 60000.0, 1)
SERIES = {(SYM, "4h"): M4, (SYM, "1h"): M1, (BTC, "4h"): MB}
STEP = {"4h": H4, "1h": H1}
LIMITS = {"4h": 700, "1h": 500}                           # engine.PERP_FRAME_LIMITS


def closed_at(rows: list[dict], tf: str, now_ms: int) -> list[dict]:
    return [r for r in rows if r["timestamp"] + STEP[tf] <= now_ms]


def forming_at(rows: list[dict], tf: str, now_ms: int, *, frac: float = 0.4) -> dict | None:
    """`now_ms` anında OLUŞAN barın kısmi hâli (kapanış/yüksek/düşük/hacim nihai değerden farklı)."""
    step = STEP[tf]
    for r in rows:
        if r["timestamp"] <= now_ms < r["timestamp"] + step:
            o = r["open"]
            c = round(o + (r["close"] - o) * frac + o * 0.003, 4)
            return {"timestamp": r["timestamp"], "open": o, "high": max(o, c), "low": min(o, c, r["low"]), "close": c,
                    "volume": round(r["volume"] * frac, 3)}
    return None


# ---------------------------------------------------------------------------- dört yol
def engine_frame(symbol: str, tf: str, fetch_ms: int, *, index_only: bool = False) -> pd.DataFrame:
    """`TradingEngine.perp_frames`: ccxt ham satırları (son LIMIT bar, OLUŞAN bar dahil) → `data.prepare`."""
    rows = SERIES[(symbol, tf)]
    closed = closed_at(rows, tf, fetch_ms)[-(LIMITS[tf] - 1):]
    tail = forming_at(rows, tf, fetch_ms)
    raw = [[r[k] for k in COLS] for r in closed + ([tail] if tail else [])]
    df = prepare(pd.DataFrame(raw, columns=COLS))
    return df.drop(columns=["timestamp"]) if index_only else df


def lab_frame(tmp: Path, symbol: str, tf: str, now_ms: int) -> pd.DataFrame:
    """`signal_lab.load_series`: laboratuvarın gzip CSV önbelleği (yalnız kapanmış barlar, close_time sütunu)."""
    rows = closed_at(SERIES[(symbol, tf)], tf, now_ms)
    df = pd.DataFrame(rows)[COLS]
    df["close_time"] = df["timestamp"] + STEP[tf] - 1
    p = lab_cache_path(tmp, symbol, tf)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, compression="gzip")
    return load_series(symbol, tf, days=400, cache_dir=tmp, provider_factory=None, now_ms=now_ms)


class FakeFeed:
    """`MarketFeed.get_klines` imzası: son `want` bar + OLUŞAN bar (DataService kapanmamışı kendisi süzer)."""

    def __init__(self, now_ms: int) -> None:
        self.now_ms = now_ms

    def get_klines(self, symbol, tf, market, want):
        assert market == FUTURES                              # Formasyon yalnız USDⓈ-M perpetual ister
        rows = closed_at(SERIES[(symbol, tf)], tf, self.now_ms)[-int(want):]
        tail = forming_at(SERIES[(symbol, tf)], tf, self.now_ms)
        df = pd.DataFrame(rows + ([tail] if tail else []))[COLS]
        return SimpleNamespace(df=df, market_type=market, source="fake", fetched_at=self.now_ms, is_stale=False, gaps=[],
                               from_cache=0, dropped_unclosed=1 if tail else 0, errors=[])


def formasyon_rows(symbol: str, tf: str, *, now_ms: int, as_of_ms: int) -> list[dict]:
    rows, status = DataService(FakeFeed(now_ms), clock_ms=lambda: now_ms).bars(symbol, tf, as_of_ms=as_of_ms)
    assert status["market"] == "USDM_PERP" and status["error"] is None
    return rows


def csv_frame(tmp: Path, symbol: str, tf: str, now_ms: int) -> pd.DataFrame:
    cache = CsvCandleCache(tmp / "csv")
    cache.write(symbol, tf, pd.DataFrame(closed_at(SERIES[(symbol, tf)], tf, now_ms))[COLS])
    return cache.read(symbol, tf)


def three(src_of, as_of: int, *, fetch_lb=None) -> tuple[S.Window, S.Window, S.Window]:
    return tuple(S.normalize_rows(src_of(sym, tf), tf, as_of, W=W, fetch_lb_ms=fetch_lb, source="x")
                 for sym, tf, W in ((SYM, "4h", S.W4H), (SYM, "1h", S.W1H), (BTC, "4h", S.WBTC)))


def same_window(a: S.Window, b: S.Window) -> bool:
    return (a.tf == b.tf and a.n == b.n and a.last_open_ms == b.last_open_ms and a.complete == b.complete
            and all(np.array_equal(getattr(a, k), getattr(b, k)) and getattr(a, k).dtype == getattr(b, k).dtype
                    for k in ("ts", "o", "h", "l", "c", "v")))


def truth(as_of: int = AS_OF) -> dict:
    """Referans: ana satır listesinden (kapanmış bar, alınma kuralı yok) — gerçek dünyanın nihai barları."""
    return S.snapshot(SYM, *three(lambda s, tf: SERIES[(s, tf)], as_of), as_of)


# ---------------------------------------------------------------------------- testler
def test_four_paths_identical_windows_snapshot_and_input_sha(tmp_path):
    assert S.W4H <= BARS_PER_TF["4h"] and S.W1H <= BARS_PER_TF["1h"]
    paths = {
        "lab": three(lambda s, tf: lab_frame(tmp_path / "lab", s, tf, AS_OF), AS_OF),
        "engine": three(lambda s, tf: engine_frame(s, tf, AS_OF), AS_OF, fetch_lb=AS_OF),
        "engine_dt_index": three(lambda s, tf: engine_frame(s, tf, AS_OF, index_only=True), AS_OF, fetch_lb=AS_OF),
        "formasyon": three(lambda s, tf: formasyon_rows(s, tf, now_ms=AS_OF, as_of_ms=AS_OF), AS_OF),
        "csv": three(lambda s, tf: csv_frame(tmp_path, s, tf, AS_OF), AS_OF),
    }
    # kaynakların doğal boyları gerçekten farklı (kırpma sınanıyor): motor 700/500, Formasyon 240, CSV 720, lab 900/600
    assert len(engine_frame(SYM, "4h", AS_OF)) == 700 and len(formasyon_rows(SYM, "4h", now_ms=AS_OF, as_of_ms=AS_OF)) == 240
    assert len(csv_frame(tmp_path / "c2", SYM, "4h", AS_OF)) == 720 and len(lab_frame(tmp_path / "l2", SYM, "4h", AS_OF)) == 900
    ref = paths["lab"]
    for name, ws in paths.items():
        for a, b in zip(ws, ref):
            assert same_window(a, b), name
            assert a.complete and a.n == 200
    snaps = {name: S.snapshot(SYM, *ws, AS_OF) for name, ws in paths.items()}
    want = truth()
    assert want["status"] == "OK" and want["missing"] == []
    for name, snap in snaps.items():
        assert snap == want, name
    assert len({s["input_sha"] for s in snaps.values()}) == 1


def test_engine_frame_unclosed_tail_is_dropped_and_fetch_rule_is_noop_after_close():
    fr = engine_frame(SYM, "4h", AS_OF)
    assert int(fr["timestamp"].iloc[-1]) == BOUNDARY          # 12:00 barı çerçevede OLUŞAN satır
    w = S.normalize_rows(fr, "4h", AS_OF, W=200, fetch_lb_ms=AS_OF)
    assert w.last_open_ms == LAST4 and w.complete
    assert same_window(w, S.normalize_rows(fr, "4h", AS_OF, W=200))


def test_late_materialization_three_tours_later_is_identical(tmp_path):
    """Giriş turundaki çerçeveler ile SONRAKİ turların çerçeveleri (aynı as_of): 3 tur (18 dk) sonra ve bir 4h sınırını
    aşan geç tur (16:10; yeni kapanmış 4h/1h barlar + yeni oluşan barlar) — hepsi bit-aynı."""
    entry = S.snapshot(SYM, *three(lambda s, tf: engine_frame(s, tf, AS_OF), AS_OF, fetch_lb=AS_OF), AS_OF)
    assert entry == truth()
    for later in (AS_OF + 3 * 6 * M, BOUNDARY + H4 + 10 * M, BOUNDARY + 5 * 86_400_000):
        via_engine = S.snapshot(SYM, *three(lambda s, tf: engine_frame(s, tf, later), AS_OF, fetch_lb=later), AS_OF)
        via_csv = S.snapshot(SYM, *three(lambda s, tf: csv_frame(tmp_path / str(later), s, tf, later), AS_OF), AS_OF)
        assert via_engine == entry and via_csv == entry, later
    for later in (AS_OF + 3 * 6 * M, BOUNDARY + H4 + 10 * M):
        via_fm = S.snapshot(SYM, *three(lambda s, tf: formasyon_rows(s, tf, now_ms=later, as_of_ms=AS_OF), AS_OF), AS_OF)
        assert via_fm == entry, later
    # geç çerçevede gerçekten as_of sonrası kapanmış barlar vardı (test kör değil)
    late = engine_frame(SYM, "1h", BOUNDARY + H4 + 10 * M)
    assert int(late["timestamp"].iloc[-2]) > LAST1 + 3 * H1
    # kapsam sınırı (SPEC §4.3 PARTIAL): Formasyon 240 bar tutar; 5 gün sonra 1h penceresi as_of'tan geriye yalnız
    # 240-120 = 120 bar uzanır → PARTIAL; eldeki barlar yine AYNI (nedensel), yalnız eksik
    far = BOUNDARY + 5 * 86_400_000
    fm = three(lambda s, tf: formasyon_rows(s, tf, now_ms=far, as_of_ms=AS_OF), AS_OF)
    s_far = S.snapshot(SYM, *fm, AS_OF)
    assert s_far["status"] == "PARTIAL" and s_far["n_h1"] == 120 and fm[1].complete
    full1 = three(lambda s, tf: SERIES[(s, tf)], AS_OF)[1]
    assert fm[1].ts.tolist() == full1.ts.tolist()[-120:] and fm[1].c.tolist() == full1.c.tolist()[-120:]
    assert s_far["h4_trend"] == entry["h4_trend"] and s_far["input_sha"] != entry["input_sha"]


def test_deferral_box_event_after_boundary_pending_then_ok():
    """Box olayı 12:05; önceki tur 11:58'de başladı → çerçevesinde 08:00 4h ve 11:00 1h barları OLUŞAN satır.
    1. adım: pencere eksik (toplayıcı taslakta tutar; anlık görüntü GAP, önbelleğe girmez). 2. adım (tur 12:06):
    tam, OK ve tam kaynaklarla (CSV/lab/ana seri) bit-aynı."""
    event = AS_OF
    tour1 = BOUNDARY - 2 * M
    w1 = three(lambda s, tf: engine_frame(s, tf, tour1), event, fetch_lb=tour1)
    assert [w.complete for w in w1] == [False, False, False]
    assert (w1[0].last_open_ms, w1[1].last_open_ms) == (LAST4 - H4, LAST1 - H1)
    s1 = S.snapshot(SYM, *w1, event)
    assert s1["status"] == "GAP" and s1["missing"][:3] == ["h4_window", "h1_window", "btc_window"]
    cache = SituationCache()
    assert cache.key_for(SYM, *w1) is None and cache.get_or_compute(SYM, *w1) is None and len(cache) == 0
    # alınma kuralı olmasaydı: oluşan (kısmi) 08:00 barı "tam" görünür ve YANLIŞ bir durum yazılırdı
    trap = S.snapshot(SYM, *three(lambda s, tf: engine_frame(s, tf, tour1), event), event)
    assert trap["status"] == "OK" and trap["input_sha"] != truth(event)["input_sha"]
    tour2 = BOUNDARY + 6 * M
    w2 = three(lambda s, tf: engine_frame(s, tf, tour2), event, fetch_lb=tour2)
    assert all(w.complete for w in w2)
    s2 = S.snapshot(SYM, *w2, event, core=cache.get_or_compute(SYM, *w2))
    assert s2["status"] == "OK" and s2 == truth(event)
    assert s2 == S.snapshot(SYM, *three(lambda s, tf: formasyon_rows(s, tf, now_ms=tour2, as_of_ms=event), event), event)


def test_cache_same_bars_same_object_across_books_and_sources(tmp_path):
    cache = SituationCache(max_entries=16)
    ws_engine = three(lambda s, tf: engine_frame(s, tf, AS_OF), AS_OF, fetch_lb=AS_OF)
    ws_csv = three(lambda s, tf: csv_frame(tmp_path, s, tf, AS_OF), AS_OF)
    ws_fm = three(lambda s, tf: formasyon_rows(s, tf, now_ms=AS_OF, as_of_ms=AS_OF), AS_OF)
    core_main = cache.get_or_compute(SYM, *ws_engine)       # ana defter gerçek girişi (tur içi)
    core_fm = cache.get_or_compute(SYM, *ws_fm)             # Formasyon karşı-olgusalı, aynı barlar
    core_csv = cache.get_or_compute(SYM, *ws_csv)
    assert core_main is core_fm is core_csv
    assert cache.stats() == {"entries": 1, "max_entries": 16, "hits": 2, "misses": 1, "puts": 1, "evictions": 0, "refused": 0}
    # aynı bar aralığında daha geç bir Box karşı-olgusalı (12:40): aynı pencereler → aynı çekirdek nesnesi
    later = AS_OF + 35 * M
    ws_box = three(lambda s, tf: engine_frame(s, tf, later), later, fetch_lb=later)
    assert cache.get_or_compute(SYM, *ws_box) is core_main
    real = S.snapshot(SYM, *ws_engine, AS_OF, core=core_main)
    cf = S.snapshot(SYM, *ws_box, later, core=core_main)
    assert {k for k in real if real[k] != cf[k]} == {"as_of_ms"}
    assert real == S.snapshot(SYM, *ws_engine, AS_OF)       # önbellekli çekirdek == doğrudan hesap
    real["h4_rsi14"] = -1.0                                  # anlık görüntü kopyadır: önbelleği bozamaz
    assert core_main["h4_rsi14"] != -1.0
    # BTC sembolünün çekirdeği ayrı anahtardır (is_btc)
    assert cache.get_or_compute(BTC, ws_engine[2], ws_engine[1], ws_engine[2]) is not core_main


def test_cache_key_separates_same_last_bar_different_content():
    """SPEC anahtarı (son bar + n) ortasında eksik bar olan bir kaynağı tam kaynakla ÇAKIŞTIRIRDI; `input_sha` ayırır."""
    full = three(lambda s, tf: SERIES[(s, tf)], AS_OF)
    gap_rows = [r for r in closed_at(M4, "4h", AS_OF) if r["timestamp"] != LAST4 - 50 * H4]
    gap4 = S.normalize_rows(gap_rows, "4h", AS_OF, W=200)
    assert (gap4.last_open_ms, gap4.n, gap4.complete) == (full[0].last_open_ms, full[0].n, True)
    k_full = SituationCache.key_for(SYM, *full)
    k_gap = SituationCache.key_for(SYM, gap4, full[1], full[2])
    assert k_full[:7] == k_gap[:7] and k_full != k_gap
    cache = SituationCache()
    a = cache.get_or_compute(SYM, *full)
    b = cache.get_or_compute(SYM, gap4, full[1], full[2])
    assert a is not b and cache.stats()["misses"] == 2
    snap = S.snapshot(SYM, gap4, full[1], full[2], AS_OF, core=b)
    assert snap == S.snapshot(SYM, gap4, full[1], full[2], AS_OF)


def test_cache_lru_and_refusals():
    cache = SituationCache(max_entries=2)
    cache.put(("a",), {"x": 1})
    cache.put(("b",), {"x": 2})
    assert cache.get(("a",)) == {"x": 1}                     # a en yeni olur
    cache.put(("c",), {"x": 3})                              # en eski (b) atılır
    assert cache.get(("b",)) is None and cache.get(("a",)) is not None and cache.get(("c",)) is not None
    cache.put(None, {"x": 4})
    assert cache.get(None) is None
    st = cache.stats()
    assert (st["entries"], st["evictions"], st["refused"]) == (2, 1, 1)
    with pytest.raises(ValueError):
        SituationCache(max_entries=0)


def test_csv_round_trip_exact_for_exchange_decimals_documented_limit_for_binary_floats(tmp_path):
    """CSV yolunun bit-aynılığı borsa ondalıkları içindir (fikstür böyle); tam hassasiyetli ikili kayan noktada pandas'ın
    varsayılan ayrıştırıcısı 1 ulp kaydırabilir → o durumda CSV yolu `input_sha`'yı değiştirebilir (CsvCandleCache'in
    önceden var olan özelliği; bu katman DEĞİŞTİRMEZ, rapora yazılır)."""
    rows = closed_at(M4, "4h", AS_OF)[-300:]
    cache = CsvCandleCache(tmp_path)
    cache.write(SYM, "4h", pd.DataFrame(rows)[COLS])
    back = cache.read(SYM, "4h")
    assert all(np.array_equal(back[k].to_numpy(), np.array([r[k] for r in rows])) for k in COLS)
    x = np.array([0.1 + i * 0.1234567890123 / 7.0 for i in range(2000)]) * 1234.56789
    y = pd.read_csv(io.StringIO(pd.DataFrame({"c": x}).to_csv(index=False)))["c"].to_numpy()
    z = pd.read_csv(io.StringIO(pd.DataFrame({"c": x}).to_csv(index=False)), float_precision="round_trip")["c"].to_numpy()
    assert np.array_equal(x, z)                               # round_trip ayrıştırıcı kesin
    assert np.allclose(x, y, rtol=1e-15, atol=0)              # varsayılan en fazla 1 ulp (eşit olmayabilir)
