"""Kapasite/performans kabulü (görev §16) — 40-60 sembollü evrende tur bütçesi.

Fixture donanımı VPS ile birebir değildir; bu test ÖLÇEK DAVRANIŞINI kanıtlar: derin küme
sınırlı kalırken evren 50'ye çıkınca tur süresi patlamaz, aday başına retrieval sınırlı kalır,
no-op indeks yenilemesi milisaniyeliktir ve stage-1 journal yazımı ucuzdur.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from test_engine_v3 import _engine
from test_universe_funnel import _mk_scan

from tradingbot.core import iso, utc_now
from tradingbot.universe_eval import build_eval_universe


def test_full_cycle_with_50_symbol_universe_stays_bounded(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=8)          # Tier B = 8 derin sembol
    scan = _mk_scan(n_ok=50)
    doc = build_eval_universe(scan, run_id="cap", now_iso=iso(utc_now()), flag_score=60,
                              deep_symbols=tuple(eng.cfg.coins))
    eng._eval_universe = doc

    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        s = eng.tour(do_scan=False, obsidian=False, charts=False)
        times.append(time.perf_counter() - t0)
        assert s is not None
    times.sort()
    p50, worst = times[1], times[-1]

    # 15 dk aralığın %50'si = 450 s; fixture'da ÇOK altında kalmalı (ölçek kanıtı)
    assert worst < 60.0, f"50 sembollü tur fixture'da bile sınırlı olmalı ({worst:.1f}s)"

    fun = json.loads((Path(eng.cfg.state_path) / "decision_funnel.json")
                     .read_text(encoding="utf-8"))
    assert fun["tiers"]["tier_a_universe"] == 50
    assert fun["coverage"]["journaled"] == fun["coverage"]["evaluated"] >= 50

    # no-op indeks yenilemesi ucuz
    t1 = time.perf_counter()
    res = eng.exp_index_store.refresh()
    noop_s = time.perf_counter() - t1
    assert res["new_segments"] == 0 and noop_s < 0.5

    # journal retention özeti O(1)
    t2 = time.perf_counter()
    eng.decision_journal.retention_stats()
    assert (time.perf_counter() - t2) < 0.1

    print(f"\n[bench-cycle] deep=8 universe=50 tour_p50={p50:.2f}s worst={worst:.2f}s "
          f"noop_refresh={noop_s * 1000:.1f}ms "
          f"journaled/tour={fun['coverage']['journaled']}")


def test_stage1_journal_write_cost_is_negligible(tmp_path: Path, monkeypatch):
    """60 sembollük evrende stage-1 kayıtların toplam maliyeti ölçülür (fsync dahil)."""
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    scan = _mk_scan(n_ok=60)
    doc = build_eval_universe(scan, target_max=60, run_id="cap2",
                              now_iso=iso(utc_now()), flag_score=60,
                              deep_symbols=tuple(eng.cfg.coins))
    eng._eval_universe = doc
    t0 = time.perf_counter()
    eng.tour(do_scan=False, obsidian=False, charts=False)
    dt = time.perf_counter() - t0
    n_screen = eng._tier_counts["screened_journaled"]
    assert n_screen >= 55
    per = dt / max(1, n_screen)
    assert per < 0.5, f"stage-1 kayıt başına maliyet sınırlı olmalı ({per * 1000:.0f} ms)"
    print(f"\n[bench-stage1] screened={n_screen} tour={dt:.2f}s per_record={per * 1000:.1f}ms")
