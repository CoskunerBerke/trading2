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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bars", type=int, default=1500)
    ap.add_argument("--modes", default="none,inproc,child")
    a = ap.parse_args(argv)
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
