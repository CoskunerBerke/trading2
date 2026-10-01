# -*- coding: utf-8 -*-
"""Eski (`2399eb5`) ve yeni (aday üreteçli) `detect_chart_patterns` çıktısının BİREBİR aynı olduğu kanıtı.

Girdi: (1) deterministik sentetik seriler (tohum × eğim × dalga genliği), (2) varsa GERÇEK Binance arşivi
`C:/Users/berke/research/bn_archive/history/futures/<SEMBOL>/{1d,4h}/*.csv.gz` üzerinde kayan pencereler.
Eski modül yolu argümanla verilir (depo dışında tutulan kopya): `python chart_detector_equivalence.py <eski.py>`.
Çıktı: karşılaştırılan pencere sayısı, toplam formasyon, fark sayısı (0 beklenir).
"""
from __future__ import annotations

import csv
import glob
import gzip
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tradingbot.chart_patterns import ChartPatternConfig, detect_chart_patterns  # noqa: E402

ARCHIVE = Path("C:/Users/berke/research/bn_archive/history/futures")


def _load_old(path: str):
    spec = importlib.util.spec_from_file_location("chart_patterns_old", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chart_patterns_old"] = mod          # dataclass tip çözümü modülü sys.modules'ta arar
    spec.loader.exec_module(mod)
    return mod


def synth(n: int, seed: int, drift: float, amp: float) -> list[dict]:
    rows, px = [], 100.0
    for i in range(n):
        c = 100.0 + drift * i + amp * math.sin((i + seed) / 7.0) + 0.4 * amp * math.sin((i + 3 * seed) / 2.3)
        o = px
        h = max(o, c) * (1 + 0.004 + 0.002 * math.sin(i + seed))
        lo = min(o, c) * (1 - 0.004 - 0.002 * math.cos(i * 1.7 + seed))
        rows.append({"timestamp": 1_700_000_000_000 + i * 14_400_000, "open": o, "high": h, "low": lo, "close": c})
        px = c
    return rows


def archive_series(sym_dir: Path, tf: str) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(str(sym_dir / tf / "*" / "*.csv.gz"))):
        with gzip.open(f, "rt") as fh:
            for r in csv.DictReader(fh):
                try:
                    rows.append({"timestamp": int(r["timestamp"]), "open": float(r["open"]), "high": float(r["high"]),
                                 "low": float(r["low"]), "close": float(r["close"])})
                except (KeyError, ValueError):
                    continue
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def main() -> int:
    old = _load_old(sys.argv[1])
    cfg_new, cfg_old = ChartPatternConfig(), old.ChartPatternConfig()
    windows = pats = diffs = 0
    samples = []

    def check(rows, tag):
        nonlocal windows, pats, diffs
        a = old.detect_chart_patterns(rows, cfg_old)
        b = detect_chart_patterns(rows, cfg_new)
        windows += 1
        pats += len(b.get("patterns") or [])
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            diffs += 1
            if len(samples) < 5:
                samples.append(tag)

    for seed in range(1, 41):
        for drift in (-0.08, 0.0, 0.08):
            for amp in (3.0, 8.0):
                check(synth(160, seed, drift, amp), "synth:%d:%s:%s" % (seed, drift, amp))
    n_real = 0
    if ARCHIVE.exists():
        for d in sorted(ARCHIVE.iterdir()):
            for tf in ("1d", "4h"):
                rows = archive_series(d, tf)
                if len(rows) < 130:
                    continue
                step = 40 if tf == "1d" else 120
                for end in range(130, len(rows), step):
                    check(rows[max(0, end - 130):end], "%s:%s:%d" % (d.name, tf, end))
                    n_real += 1
    print("pencere=%d (gercek arsiv=%d) formasyon=%d fark=%d" % (windows, n_real, pats, diffs))
    if samples:
        print("fark ornekleri:", samples)
    return 0 if diffs == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
