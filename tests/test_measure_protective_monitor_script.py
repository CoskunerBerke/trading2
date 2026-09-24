# -*- coding: utf-8 -*-
"""ÖLÇÜM BETİĞİ (scripts/measure_protective_monitor.py) — başlatma/durdurma, temizlik ve rapor yazımı (ağsız).

KUSUR (2026-09-24, PR #1 incelemesi): `RssSampler._stop` alanı `threading.Thread._stop` iç metodunu eziyordu; örnekleyici
başlatılıp `stop()` çağrılınca `join` → `self._stop()` → TypeError ('Event' object is not callable). `measure` bu yüzden
rapor yazmadan düşerdi. Motor burada SAHTEDİR (ağ yok); gerçek `ProtectiveMonitor` iş parçacığı çalışır. Üretim ölçümü
DEĞİLDİR.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _script():
    spec = importlib.util.spec_from_file_location("measure_protective_monitor", ROOT / "scripts" / "measure_protective_monitor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _alive(name: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == name and t.is_alive()]


def test_rss_sampler_starts_and_stops_cleanly():
    m = _script()
    s = m.RssSampler(every_s=0.01)
    s.start()
    s.stop()                                              # ÖNCE: TypeError ('Event' object is not callable)
    assert not s.is_alive() and s.samples >= 1 and s.max_mb > 0
    s.stop()                                              # ikinci durdurma da güvenli
    assert callable(threading.Thread._stop)


class _Stopper:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeEngine:
    """Ağsız motor: ana defterde bir açık pozisyon, M2 defteri boş. İzleyici GERÇEK `ProtectiveMonitor`."""
    tour_error: Exception | None = None
    index_error: Exception | None = None
    last = None

    def __init__(self, cfg):
        from types import SimpleNamespace
        self.cfg = cfg
        self.ledger2 = SimpleNamespace(positions={"ETH/USDT": SimpleNamespace(id="F1")})
        self.strategy_books = [SimpleNamespace(key="strategy_paper_m2", ledger=SimpleNamespace(positions={}))]
        self.pattern_book = None
        self.pattern_scanner, self.box_timer, self._refresher = _Stopper(), _Stopper(), _Stopper()
        self.protective_monitor = None
        self.drained = 0
        _FakeEngine.last = self

    def ensure_protective_monitor(self, interval_s):
        from tradingbot.accounting import TickData
        from tradingbot.core import iso
        from tradingbot.protective_monitor import ProtectiveMonitor
        eng = self

        class _Main:
            key = "main"

            def held(self):
                return {s: p.id for s, p in eng.ledger2.positions.items()}

            def protect(self, marks, marks_f, gaps, now, expect, apply_clock=None):
                eng.protective_monitor.observer.note("main", {s: {"id": expect[s]} for s in marks}, int(now.timestamp() * 1000),
                                                     source="monitor")
                return []

        class _M2:
            key = "strategy_paper_m2"

            def held(self):
                return {}

            def protect(self, *a, **k):
                return []

        def price_fn(syms, now_ms):
            from datetime import datetime, timezone
            at = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
            return {s: TickData(last=100, mark=100, ts=iso(at)) for s in syms}, {s: 100.0 for s in syms}, {}
        self.protective_monitor = ProtectiveMonitor(handles_fn=lambda: [_Main(), _M2()], price_fn=price_fn,
                                                    state_path=self.cfg.state_path, interval_s=float(interval_s))
        self.protective_monitor.start()

    def tour(self, do_scan=True, obsidian=True, charts=False):
        if self.tour_error is not None:
            raise self.tour_error
        return {"run_id": "r1", "opened": [], "closed": []}

    def index_refresh_status(self):
        if self.index_error is not None:
            raise self.index_error
        return {"enabled": True, "index": {"version": 1, "events": 7, "series": 1}}

    def stop_protective_monitor(self):
        self.protective_monitor.stop(10)

    def drain_protective_closes(self):
        self.drained += 1
        return []


def _setup(tmp_path, monkeypatch, *, tour_error=None, index_error=None):
    src = tmp_path / "src"
    (src / "state").mkdir(parents=True)
    (src / "state" / "futures_ledger.json").write_text('{"x": 1}', encoding="utf-8")
    (src / "market").mkdir()
    (src / "market" / "part.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mode: PAPER\n", encoding="utf-8")
    for k in ("TRADINGBOT_DATA", "TRADINGBOT_STATE_DIR", "TRADINGBOT_CACHE_DIR", "TRADINGBOT_VAULT_PATH"):
        monkeypatch.delenv(k, raising=False)                # betiğin yazdığı ortam test sonunda geri alınır
    monkeypatch.setattr(_FakeEngine, "tour_error", tour_error)
    monkeypatch.setattr(_FakeEngine, "index_error", index_error)
    monkeypatch.setattr("tradingbot.engine_v3.TradingEngineV3", _FakeEngine)
    out = tmp_path / "olcum.json"
    args = argparse.Namespace(source=str(src), work=str(tmp_path / "work"), work_is_disposable=False, config=str(cfg),
                              interval=60.0, index_wait=5.0, no_scan=True, no_obsidian=True, out=str(out), verbose=False)
    digest = {p.relative_to(src).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob("*") if p.is_file()}
    return src, args, out, digest


def _src_digest(src: Path) -> dict:
    return {p.relative_to(src).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob("*") if p.is_file()}


def test_measure_writes_the_report_and_cleans_up_every_thread(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch)
    assert m.cmd_measure(args) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["kind"] == "PRODUCTION_LIKE_MEASUREMENT" and rep["tour_error"] is None
    assert rep["positions_at_load"] == {"main": ["ETH/USDT"], "strategy_paper_m2": []}
    books = rep["monitor"]["books"]
    assert books["main"]["open_positions"] == 1 and books["main"]["verdict"] == "WITHIN_60S"
    assert books["strategy_paper_m2"]["verdict"] == "NOT_MEASURED_NO_OPEN_POSITION", "pozisyonsuz defter doğrulanmış SAYILMAZ"
    assert rep["monitor"]["runs"] >= 1 and rep["pattern_index"]["index"]["events"] == 7
    assert rep["memory"]["peak_rss_process_mb"] > 0 and rep["memory"]["samples"] >= 1
    eng = _FakeEngine.last
    assert eng.drained >= 1 and all(s.stopped for s in (eng.pattern_scanner, eng.box_timer, eng._refresher))
    assert not _alive("rss-sampler") and not _alive("protective-monitor")
    assert (Path(args.work) / "state" / "futures_ledger.json").exists()
    assert _src_digest(src) == digest, "kaynak kopya DEĞİŞMEDİ"


def test_a_failing_tour_still_writes_the_report_and_stops_the_threads(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch, tour_error=RuntimeError("ağ yok"))
    assert m.cmd_measure(args) == 2
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["tour_error"] == "RuntimeError: ağ yok"
    assert not _alive("rss-sampler") and not _alive("protective-monitor")


def test_an_unexpected_error_after_the_tour_still_cleans_up(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch, index_error=OSError("disk"))
    with pytest.raises(OSError):
        m.cmd_measure(args)
    eng = _FakeEngine.last
    assert all(s.stopped for s in (eng.pattern_scanner, eng.box_timer, eng._refresher))
    assert not _alive("rss-sampler") and not _alive("protective-monitor")
    assert _src_digest(src) == digest


def test_an_existing_work_directory_is_never_overwritten(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch)
    Path(args.work).mkdir()
    (Path(args.work) / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        m.cmd_measure(args)
    assert (Path(args.work) / "keep.txt").read_text(encoding="utf-8") == "x" and not out.exists()
