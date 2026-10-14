#!/usr/bin/env python3
"""TUR ÇEKİŞMESİ ÖLÇÜMÜ (2026-10-05) — ağsız, durum dosyasız, tekrar çalıştırılabilir sentetik ölçüm.

Soru: yayım sonrası pattern kanıtı ön ısıtması koşarken worker'ın diğer iş parçacıkları (tur, Box zamanlayıcısı)
ne kadar yavaşlıyor? Üç durum aynı süreçte, aynı sentetik indeksle ölçülür:

  none    ön ısıtma yok (taban)
  inproc  ön ısıtma süreç içi iş parçacığında (`history.evidence_subprocess: false`, 2026-10-01 yolu)
  child   ön ısıtma `fork` edilen alt süreçte (varsayılan, `tradingbot/patterns/evidence_child.py`)

Her durumda ana iş parçacığı dört tur vekili koşar (saf Python, `build_feature_frame` = ajan göstergeleri, ayrı bir
süreçten parça parça soket okuma + JSON = HTTP vekili, atomik JSON yazımı) ve bir iş parçacığı 1 ms uyuyup uyanma
gecikmesini (GIL'i geri alma dahil) ölçer (Box zamanlayıcısı vekili). Gerçek `SimilarPatternEngine` ve gerçek
`EvidenceCache` kullanılır; veri sentetiktir (`tests/test_patterns._candles` ile aynı üretici), ağ ve borsa yoktur,
yalnız geçici dizine yazılır.

Kullanım:  python scripts/bench_tour_contention.py [--bars 1500] [--modes none,inproc,child]
`--bars` sembol başına 4h bar (16 sembol); 1500 ≈ 22k olay. VPS'teki worker'ın yanında koşturmak onunla CPU yarıştırır;
ayrı bir makinede ya da worker durdurulmuşken koşturun. Ayrıntı ve yerel sonuçlar: docs/TOUR_CONTENTION_V1.md.

BELLEK (düzeltme turu 3):  python scripts/bench_tour_contention.py --memory [--bars 1500]
Her durum ayrı bir yorumlayıcıda: iki indeks (eski + yeni) bellekteyken yeni indeksin ön ısıtması başlar; eski indeks
fork'tan ÖNCE (`child-between`: yayım turlar arasında) ya da SONRA (`child-mid-*`: yayım turun uçuştaki kanıt
çağrısına denk geldi) bırakılır. Ebeveyn + alt sürecin toplam PSS'i (MB) ve alt sürecin özel belleği (USS) her adımda
/proc/<pid>/smaps_rollup'tan okunur. `child-mid-nofix`: eski motor önbelleğe bildirilmez (ölüm kancası yok = düzeltme
turu 3'ten önceki davranış); `child-mid-fix`: bugünkü kod; `inproc-mid`: geri dönüş anahtarı (süreç içi).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BAR_4H = 14_400_000
SYMS = [f"S{i:02d}/USDT" for i in range(16)]


def _candles(n, seed=1, drift=0.0, vol=0.01, start=0, tf_ms=3_600_000):
    """`tests/test_patterns._candles` ile aynı sentetik mum üreticisi."""
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, vol, n)
    c = 100 * np.exp(np.cumsum(r))
    o = np.r_[100, c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, vol, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, vol, n))
    v = rng.uniform(50, 150, n)
    return pd.DataFrame({"timestamp": start + np.arange(n) * tf_ms, "open": o, "high": h, "low": lo, "close": c,
                         "volume": v, "quote_volume": v * c, "trades": 10, "taker_buy_base": v * 0.5,
                         "taker_buy_quote": v * c * 0.5, "close_time": start + np.arange(n) * tf_ms + tf_ms - 1})


def build_index(n_bars: int):
    from tradingbot.patterns import SimilarPatternEngine
    now_ms = int(time.time() * 1000)
    start = now_ms - now_ms % BAR_4H - n_bars * BAR_4H
    eng = SimilarPatternEngine(min_sample=30, horizon=24)
    for i, s in enumerate(SYMS):
        eng.add_series(s, "futures", "4h", _candles(n_bars, seed=11 + i, drift=0.0002 * (i % 5 - 2), tf_ms=BAR_4H,
                                                    start=start))
    return eng


# ------------------------------------------------------------------ tur vekilleri
def w_py():
    acc = 0
    for _ in range(250):
        d = {i: str(i) for i in range(2000)}
        acc += sum(len(v) for v in d.values()) + len(sorted(d.items(), key=lambda kv: -kv[0]))
    return acc


_FRAMES = [_candles(1000, seed=100 + i) for i in range(4)]


def w_numpy():
    from tradingbot.patterns.features import build_feature_frame
    return sum(float(np.nansum(build_feature_frame(_FRAMES[i % 4], "1h")["rsi14"].to_numpy())) for i in range(6))


def _server(sock):
    payload = json.dumps([[i, 1.0 * i, 2.0, 3.0, 4.0, 5.0] for i in range(2500)]).encode()
    hdr = len(payload).to_bytes(4, "big")
    while True:
        b = sock.recv(1)
        if not b or b == b"q":
            return
        sock.sendall(hdr + payload)


class Net:
    """HTTP vekili: ayrı süreçten 100 KB JSON, 16 KB'lık parçalarla okunur (her `recv` GIL'i bırakır)."""

    def __init__(self):
        a, b = socket.socketpair()
        self.p = mp.get_context("fork").Process(target=_server, args=(b,), daemon=True)
        self.p.start()
        b.close()
        self.s = a

    def fetch(self):
        self.s.sendall(b"x")
        hdr = b""
        while len(hdr) < 4:
            hdr += self.s.recv(4 - len(hdr))
        n = int.from_bytes(hdr, "big")
        buf = bytearray()
        while len(buf) < n:
            buf += self.s.recv(min(16384, n - len(buf)))
        return json.loads(buf)

    def close(self):
        try:
            self.s.sendall(b"q")
        except OSError:
            pass
        self.p.join(2)


def w_net(net):
    return sum(len(net.fetch()) for _ in range(30))


def w_file(tmpdir):
    doc = {"k%d" % i: [i, i * 0.5, "x" * 20] for i in range(200)}
    for i in range(200):
        p = os.path.join(tmpdir, "f%d.json" % (i % 20))
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        os.replace(p + ".tmp", p)


def _sampler(stop, lags):
    while not stop.is_set():
        t = time.perf_counter()
        time.sleep(0.001)
        lags.append(time.perf_counter() - t - 0.001)


def run(mode, eng, net, tmpdir):
    from tradingbot.engine_v3 import TradingEngineV3
    from tradingbot.patterns.evidence_cache import EvidenceCache
    cache = None
    if mode != "none":
        cache = EvidenceCache()
        bundle = type("B", (), {"engine": eng, "version": 1})()
        keys = [(s, 1, 0) for s in SYMS]
        cache.request_prewarm(bundle, keys, TradingEngineV3._evidence_query, lambda b: True, use_child=(mode == "child"))
        time.sleep(1.0)
    stop, lags = threading.Event(), []
    th = threading.Thread(target=_sampler, args=(stop, lags), daemon=True)
    th.start()
    res = {}
    for name, fn in (("py", w_py), ("numpy", w_numpy), ("net", lambda: w_net(net)), ("file", lambda: w_file(tmpdir))):
        t0 = time.perf_counter()
        fn()
        res[name] = round(time.perf_counter() - t0, 3)
    stop.set()
    th.join()
    lags.sort()
    res["lag_ms"] = {"p50": round(1000 * lags[len(lags) // 2], 2), "p90": round(1000 * lags[int(0.9 * len(lags))], 1),
                     "max": round(1000 * lags[-1], 1)}
    if cache is not None:
        res["prewarm_still_running"] = bool(cache._running)
        if mode == "child":
            res["child_private_mb"] = _child_private_mb(cache)
        cache.stop()
        cache._close_child()
    return res


def _child_private_mb(cache) -> float | None:
    """Alt sürecin özel belleği (MB): ölçümün SONUNDA /proc/<pid>/smaps_rollup'tan doğrudan okunur (ilk sembolün cevabı
    ölçüm bitmeden gelmezse alt sürecin kendi bildirdiği değer henüz yoktur) ve bildirilen en yüksekle birleştirilir.
    Ön ısıtma ölçümden önce bittiyse (alt süreç kapandı) None."""
    from tradingbot.patterns.evidence_child import _private_kb
    child = cache._child
    if child is None or not child.pid:
        return None
    kbs = [k for k in (_private_kb(child.pid), child.private_kb_max) if isinstance(k, int)]
    return round(max(kbs) / 1024.0, 1) if kbs else None


# ------------------------------------------------------------------ bellek ölçümü (düzeltme turu 3)
MEMORY_CASES = ("inproc-mid", "child-between", "child-mid-nofix", "child-mid-fix")


def _smaps(pid="self") -> dict:
    out = {}
    with open(f"/proc/{pid}/smaps_rollup", encoding="ascii") as fh:
        for ln in fh:
            p = ln.split()
            if len(p) >= 2 and p[0].endswith(":") and p[1].isdigit():
                out[p[0][:-1]] = int(p[1])
    return out


def _mem_point(cache) -> dict:
    s = _smaps()
    pt = {"parent_pss_mb": round(s["Pss"] / 1024), "total_pss_mb": None}
    tot = s["Pss"]
    c = cache._child if cache is not None else None
    if c is not None and c.pid:
        try:
            cs = _smaps(c.pid)
            tot += cs["Pss"]
            pt["child_pid"] = c.pid
            pt["child_uss_mb"] = round((cs.get("Private_Clean", 0) + cs.get("Private_Dirty", 0)) / 1024)
        except OSError:
            pass
    pt["total_pss_mb"] = round(tot / 1024)
    return pt


def _wait_entries(cache, n, timeout=600.0) -> None:
    end = time.monotonic() + timeout
    while len(cache) < n and time.monotonic() < end:
        time.sleep(0.05)


def memory_case(case: str, bars: int) -> dict:
    """Tek bir bellek durumu (ayrı yorumlayıcıda çağrılır; bkz. modül başlığı "BELLEK")."""
    import gc

    from tradingbot.engine_v3 import TradingEngineV3
    from tradingbot.patterns.evidence_cache import EvidenceCache
    gc.collect()
    p0 = _smaps()["Pss"]
    old = build_index(bars)
    new = build_index(bars)
    gc.collect()
    out: dict = {"case": case, "events_per_index": len(new.events),
                 "index_mb": round((_smaps()["Pss"] - p0) / 2048), "points": {}}
    pts = out["points"]
    pts["1 iki indeks (eski + yeni)"] = _mem_point(None)
    cache = EvidenceCache()
    if case == "child-between":
        del old
        gc.collect()
        pts["2 eski indeks fork'tan ÖNCE bırakıldı"] = _mem_point(None)
    elif case == "child-mid-fix":
        cache.track_engine(old)                   # bugünkü kod: yayımlanan her motor bilinir
    bundle = type("B", (), {"engine": new, "version": 2})()
    cache.request_prewarm(bundle, [(s, 2, 0) for s in SYMS], TradingEngineV3._evidence_query, lambda b: True,
                          use_child=(case != "inproc-mid"))
    _wait_entries(cache, 2)
    pts["3 ön ısıtma: 2 sembol"] = _mem_point(cache)
    if case != "child-between":
        del old
        gc.collect()
        time.sleep(1.0)
        pts["4 eski indeks bırakıldı (+1 sn)"] = _mem_point(cache)
        _wait_entries(cache, 4)
        pts["5 +2 sembol"] = _mem_point(cache)
    for _ in range(3):
        w_numpy()
        w_py()
    pts["6 tur benzeri iş"] = _mem_point(cache)
    cache.wait_idle(1800)
    out["job_private_mb_max"] = cache.stats.get("child_private_mb_max")
    out["reforks"] = cache.stats.get("child_reforks")
    cache.stop()
    cache._close_child()
    gc.collect()
    pts["7 iş bitti"] = _mem_point(None)
    return out


def memory_main(bars: int) -> int:
    import subprocess
    for case in MEMORY_CASES:
        r = subprocess.run([sys.executable, __file__, "--memory-case", case, "--bars", str(bars)],
                           capture_output=True, text=True, timeout=3600)
        line = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
        if r.returncode != 0 or not line:
            print(case, "HATA", r.returncode, r.stderr[-2000:], flush=True)
            continue
        res = json.loads(line[-1])
        print(f"\n{case}: {res['events_per_index']} olay/indeks, indeks ≈ {res['index_mb']} MB, iş boyunca alt süreç "
              f"özel belleği en çok {res.get('job_private_mb_max')} MB, yeniden fork {res.get('reforks')}", flush=True)
        for k, v in res["points"].items():
            print(f"  {k:<40} toplam PSS {v['total_pss_mb']:>5} MB  (ebeveyn {v['parent_pss_mb']} MB"
                  + (f", alt süreç USS {v['child_uss_mb']} MB pid {v['child_pid']}" if "child_uss_mb" in v else "")
                  + ")", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bars", type=int, default=1500)
    ap.add_argument("--modes", default="none,inproc,child")
    ap.add_argument("--memory", action="store_true", help="bellek ölçümü (her durum ayrı yorumlayıcıda)")
    ap.add_argument("--memory-case", choices=MEMORY_CASES, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.memory_case:
        print(json.dumps(memory_case(a.memory_case, a.bars), ensure_ascii=False), flush=True)
        return 0
    if a.memory:
        return memory_main(a.bars)
    t0 = time.perf_counter()
    eng = build_index(a.bars)
    print(f"indeks: {len(eng.events)} olay, kurulum {time.perf_counter() - t0:.1f} sn", flush=True)
    net, tmpdir = Net(), tempfile.mkdtemp(prefix="tb-bench-")
    out = {}
    try:
        for m in [x.strip() for x in a.modes.split(",") if x.strip()]:
            out[m] = run(m, eng, net, tmpdir)
            print(m, json.dumps(out[m]), flush=True)
    finally:
        net.close()
    if "none" in out:
        for m, r in out.items():
            if m != "none":
                print(m, "yavaşlama (× taban):", {k: round(r[k] / out["none"][k], 2) for k in ("py", "numpy", "net", "file")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
