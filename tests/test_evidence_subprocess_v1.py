"""PATTERN KANITI ALT SÜRECİ (2026-10-05) — sorgu worker'ın GIL'ini paylaşmaz, KANIT DEĞİŞMEZ.

Kanıt sorguları (yayım sonrası ön ısıtma ve o sırada turun ıskaları) `fork` ile ayrılan tek bir alt süreçte koşar
(`tradingbot/patterns/evidence_child.py`). Bu testler kilitler:

1. Alt süreçte hesaplanan kanıt süreç içi hesaplananla BİT-AYNI; panel dosyası aynı.
2. Turun ıskası alt süreç canlıyken ona gider; uçuştaki sembolü isteyen tur bekler, ikinci sorgu YOK.
3. Daha yeni yayım eski alt süreci kapatır; eski sürümün kanıtı yeni sürümü okuyan tura verilmez.
4. Alt süreç ölümü / zaman aşımı / hesap hatası / fork yokluğu → loglanır, tur ve ön ısıtma bugünkü süreç içi yolla
   aynı sonucu üretir. Geri dönüş anahtarı kapalıyken alt süreç HİÇ kurulmaz.
5. Yayım anı ve sürüm görünürlüğü değişmez (fork yenileyici iş parçacığında değil, ön ısıtma işçisinde olur).
6. Ebeveynde ek indeks kopyası yok; eski motor serbest kalır; ebeveyn GC dondurması iş bitince kalkar.
7. Sentetik ölçüm: alt süreç ön ısıtması sürerken turun numpy/pandas işi ve 1 ms'lik periyodik iş parçacığı yavaşlamaz.

Alt süreç `fork` ile ebeveynin bellek görüntüsünü aldığı için ölçüm vekili (`_Probe`) her iki süreçte de çalışır;
çağrılar süreç kimliğiyle bir dosyaya yazılır (iş parçacığı adı alt süreçte de ön ısıtma işçisinin adıdır, ayırt etmez).
"""
from __future__ import annotations

import gc
import json
import logging
import os
import statistics
import sys
import threading
import time
import tracemalloc
import weakref
from pathlib import Path

import pytest

if sys.platform != "linux":                                  # pragma: no cover — üretim Linux (fork + /proc)
    pytest.skip("alt süreç yolu Linux fork ister", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402
from test_patterns import _candles  # noqa: E402

from tradingbot.config_v3 import HistorySection, load_v3  # noqa: E402
from tradingbot.engine_v3 import TradingEngineV3  # noqa: E402
from tradingbot.patterns import SimilarPatternEngine  # noqa: E402
from tradingbot.patterns import evidence_child as EC  # noqa: E402
from tradingbot.patterns.evidence_cache import EvidenceCache  # noqa: E402
from tradingbot.patterns.refresher import IndexRefresher  # noqa: E402

BAR_4H = 14_400_000
PREWARM_THREAD = "pattern-evidence-prewarm"
SYMS = ("ETH/USDT", "SOL/USDT", "AVAX/USDT")


def _ser(ev) -> str:
    return json.dumps(ev, sort_keys=True, default=repr)


def _history(symbols, n=420):
    now_ms = int(time.time() * 1000)
    start = now_ms - now_ms % BAR_4H - n * BAR_4H
    return {s: _candles(n, seed=11 + i, drift=0.0004 * (i - 1), tf_ms=BAR_4H, start=start) for i, s in enumerate(symbols)}


def _build_index(hist, *, drop_last: int = 0):
    eng = SimilarPatternEngine(min_sample=30, horizon=24)
    for s, df in hist.items():
        d = df.iloc[: len(df) - drop_last].reset_index(drop=True) if drop_last else df
        eng.add_series(s, "futures", "4h", d)
    return eng


class _Probe:
    """Gerçek motoru saran vekil. Her `query` çağrısı (pid, sembol, yön) olarak dosyaya yazılır. ALT SÜREÇTE (pid ≠
    ebeveyn) istenirse: bir sembolde dosya kapısında bekler, süreci öldürür, uyur ya da istisna atar."""

    def __init__(self, real, tmp: Path, *, gate=None, crash=None, sleep=None, busy=None, fail=None, fail_everywhere=None):
        self._real = real
        self.candles, self.events = real.candles, real.events
        self.parent = os.getpid()
        self.log = tmp / f"calls-{id(self)}.txt"
        self.entered, self.gate_file = tmp / f"entered-{id(self)}", tmp / f"gate-{id(self)}"
        self.gate, self.crash, self.sleep, self.fail, self.fail_everywhere = gate, crash, sleep, fail, fail_everywhere
        self.busy = busy

    def query(self, symbol, market, tf, side, k=60):
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {symbol} {side}\n")
        if symbol == self.fail_everywhere:
            raise RuntimeError("sentetik hesap hatası (her süreçte)")
        if os.getpid() != self.parent:
            if symbol == self.crash:
                os._exit(3)
            if symbol == self.sleep:
                time.sleep(60)                                # asılı: CPU harcamaz
            if symbol == self.busy:
                end = time.monotonic() + 3.0                  # yavaş ama çalışıyor: CPU harcar
                while time.monotonic() < end:
                    pass
            if symbol == self.fail:
                raise RuntimeError("sentetik alt süreç hesap hatası")
            if symbol == self.gate:
                self.entered.touch()
                end = time.monotonic() + 30
                while not self.gate_file.exists() and time.monotonic() < end:
                    time.sleep(0.01)
        return self._real.query(symbol, market, tf, side, k=k)

    def open_gate(self):
        self.gate_file.touch()

    def wait_entered(self, timeout=30.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.entered.exists():
                return True
            time.sleep(0.01)
        return False

    def calls(self) -> list[tuple[int, str, str]]:
        if not self.log.exists():
            return []
        out = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            pid, sym, side = line.split(" ")
            out.append((int(pid), sym, side))
        return out

    def parent_calls(self):
        return [c for c in self.calls() if c[0] == self.parent]

    def child_calls(self):
        return [c for c in self.calls() if c[0] != self.parent]


def _engine(tmp_path, monkeypatch, *, child=True):
    eng = TE._engine(tmp_path, monkeypatch, symbols=list(SYMS))
    eng.cfg.v3.history.evidence_subprocess = bool(child)
    return eng


def _refresher(eng, builds):
    r = IndexRefresher(build_fn=lambda syms: (builds.pop(0), {}), symbols_fn=lambda: list(eng.cfg.coins),
                       update_fn=lambda syms, now_ms: {"advanced": True}, interval_s=10 ** 6,
                       on_publish=eng._on_pattern_index_published)
    eng._refresher = r
    return r


def _now_for(eng_index) -> int:
    return max(int(df["timestamp"].iloc[-1]) for df in eng_index.candles.values()) + BAR_4H


def _pid_alive(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                                      # zombi de "ölü" sayılır
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


@pytest.fixture()
def hist():
    return _history(SYMS)


@pytest.fixture(autouse=True)
def _stop_workers():
    made = []
    orig = TradingEngineV3._evidence_cache

    def tracked(self):
        c = orig(self)
        if c not in made:
            made.append(c)
        return c
    TradingEngineV3._evidence_cache = tracked
    try:
        yield
    finally:
        TradingEngineV3._evidence_cache = orig
        for c in made:
            c.stop()
            c._close_child()
    assert not EC.parent_gc_frozen(), "ebeveyn GC dondurması kalktı mı"


# ------------------------------------------------------------------ 1) bit-aynı
def test_child_evidence_is_bit_identical_to_in_process_and_files_match(tmp_path, monkeypatch, hist):
    real = _build_index(hist)
    idx = _Probe(real, tmp_path)
    on = _engine(tmp_path / "on", monkeypatch, child=True)
    r = _refresher(on, [idx])
    assert r.refresh_once().get("published")
    cache = on._evidence_cache()
    assert cache.wait_idle(120)
    assert cache.stats["child_started"] == 1 and cache.stats["child_computed"] == len(SYMS)
    assert cache.stats["child_failures"] == 0 and cache._child is None, "alt süreç iş bitince kapanmalı"
    assert not idx.parent_calls() and len(idx.child_calls()) == 2 * len(SYMS), idx.calls()
    pre = {k[0]: cache.get(k) for k in cache.keys()}
    assert set(pre) == set(SYMS) and all(e.source == "prewarm" for e in pre.values())

    off = _engine(tmp_path / "off", monkeypatch, child=False)
    monkeypatch.setattr(off, "EVIDENCE_PREWARM", False)
    off._refresher = type("R", (), {"bundle": r.bundle})()
    now = _now_for(real)
    got = {s: on._pattern_evidence(s, now) for s in SYMS}
    assert not idx.parent_calls(), "AÇIK motorun turu ebeveynde sorgu çalıştırmamalı (ön ısıtılmış girdiyi kullanır)"
    for s in SYMS:
        a = got[s]
        b = off._pattern_evidence(s, now)
        assert a is pre[s].ev
        assert _ser(a) == _ser(b) and a == b, s
        assert _ser(a) == _ser(TradingEngineV3._evidence_query(real, s))
        fa = json.loads((on.cfg.state_path / "evidence" / f"{s.replace('/', '_')}.json").read_text(encoding="utf-8"))
        fb = json.loads((off.cfg.state_path / "evidence" / f"{s.replace('/', '_')}.json").read_text(encoding="utf-8"))
        assert fa["neighbors"] == fb["neighbors"] and fa["explanation_tr"] == fb["explanation_tr"]
        for side in ("LONG", "SHORT"):
            pa, pb = dict(fa["packets"][side]), dict(fb["packets"][side])
            for k in ("timestamp", "decision_id"):
                pa.pop(k, None), pb.pop(k, None)
            assert pa == pb
    assert len(idx.parent_calls()) == 2 * len(SYMS), "ebeveyndeki sorgular yalnız KAPALI motorun kendi hesabı"


# ------------------------------------------------------------------ 2) tur ıskası alt sürece gider
def test_tour_waits_for_the_in_flight_child_symbol_and_gets_the_same_result(tmp_path, monkeypatch, hist):
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, gate="ETH/USDT")
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [idx])
    r.refresh_once()
    assert idx.wait_entered(), "alt süreç ETH'de beklemeli"
    now = _now_for(real)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ev", eng._pattern_evidence("ETH/USDT", now)), name="tour")
    t.start()
    time.sleep(0.3)
    assert t.is_alive(), "tur uçuştaki sembolü BEKLEMELİ"
    idx.open_gate()
    t.join(60)
    assert not t.is_alive()
    assert eng._evidence_cache().wait_idle(120)
    assert _ser(out["ev"]) == _ser(TradingEngineV3._evidence_query(real, "ETH/USDT"))
    eth = [c for c in idx.calls() if c[1] == "ETH/USDT"]
    assert len(eth) == 2 and all(c[0] != idx.parent for c in eth), eth


def test_a_tour_miss_while_the_child_is_alive_is_computed_once_and_never_in_the_parent(tmp_path, monkeypatch, hist):
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, gate="ETH/USDT")
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [idx])
    r.refresh_once()
    assert idx.wait_entered()
    now = _now_for(real)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ev", eng._pattern_evidence("AVAX/USDT", now)), name="tour")
    t.start()
    time.sleep(0.2)
    idx.open_gate()
    t.join(60)
    assert eng._evidence_cache().wait_idle(120)
    assert _ser(out["ev"]) == _ser(TradingEngineV3._evidence_query(real, "AVAX/USDT"))
    assert not idx.parent_calls(), "tur ıskası alt süreç canlıyken ebeveynde hesaplanmamalı"
    for s in SYMS:
        for side in ("LONG", "SHORT"):
            assert sum(1 for c in idx.calls() if c[1:] == (s, side)) == 1, (s, side, idx.calls())


# ------------------------------------------------------------------ 3) sürüm karışmaz, eski alt süreç kapanır
def test_a_newer_publish_closes_the_old_child_and_never_serves_the_old_version(tmp_path, monkeypatch, hist):
    v1_real, v2_real = _build_index(hist, drop_last=1), _build_index(hist)
    v1 = _Probe(v1_real, tmp_path, gate="ETH/USDT")
    v2 = _Probe(v2_real, tmp_path)
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [v1, v2])
    r.refresh_once()
    assert v1.wait_entered()
    pid_v1 = eng._evidence_cache()._child.pid
    r.refresh_once()
    assert r.bundle.version == 2
    now = _now_for(v2_real)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ev", eng._pattern_evidence("ETH/USDT", now)), name="tour")
    t.start()
    time.sleep(0.2)
    v1.open_gate()
    t.join(60)
    cache = eng._evidence_cache()
    assert cache.wait_idle(120)
    want_v1 = _ser(TradingEngineV3._evidence_query(v1_real, "ETH/USDT"))
    want_v2 = _ser(TradingEngineV3._evidence_query(v2_real, "ETH/USDT"))
    assert want_v1 != want_v2
    assert _ser(out["ev"]) == want_v2, "sürüm 2'yi okuyan tur sürüm 1 kanıtı ALAMAZ"
    assert {k[1] for k in cache.keys()} == {2}
    assert not any(c[1] in ("SOL/USDT", "AVAX/USDT") for c in v1.calls()), "sürüm 1 sembol sınırında bırakılmalı"
    assert not _pid_alive(pid_v1), "eski sürümün alt süreci kapanmalı"
    assert cache.stats["child_started"] == 2 and cache._child is None
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(v2_real, s))


# ------------------------------------------------------------------ 4) arızalar → bugünkü yol, aynı sonuç
def test_a_child_crash_is_logged_and_the_rest_is_computed_in_process(tmp_path, monkeypatch, hist, caplog):
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, crash="SOL/USDT")
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [idx])
    with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
        r.refresh_once()
        assert eng._evidence_cache().wait_idle(120)
    cache = eng._evidence_cache()
    assert any("alt süreci arızalandı" in rec.getMessage() for rec in caplog.records)
    assert cache.stats["child_failures"] == 1 and cache.stats["child_in_process_fallbacks"] >= 1
    assert {c[1] for c in idx.parent_calls()} == {"SOL/USDT", "AVAX/USDT"}, "işin kalanı süreç içi (bugünkü yol)"
    now = _now_for(real)
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(real, s))


def _bundle(engine, version=1):
    return type("B", (), {"engine": engine, "version": version})()


def test_a_child_crash_during_a_tour_request_falls_back_to_the_same_in_process_result(tmp_path, hist, caplog):
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, crash="SOL/USDT")
    cache = EvidenceCache()
    with cache.compute_lock:
        assert cache._start_child(_bundle(idx), TradingEngineV3._evidence_query)
        pid = cache._child.pid
        with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
            ev = cache.compute(idx, "SOL/USDT", TradingEngineV3._evidence_query)
    assert _ser(ev) == _ser(TradingEngineV3._evidence_query(real, "SOL/USDT"))
    assert cache._child is None and not _pid_alive(pid)
    assert any("alt süreci arızalandı" in rec.getMessage() for rec in caplog.records)
    assert not EC.parent_gc_frozen()


def test_a_hung_child_times_out_is_killed_and_the_tour_computes_itself(tmp_path, monkeypatch, hist, caplog):
    monkeypatch.setattr(EC.EvidenceChild, "REQUEST_TIMEOUT_S", 1.0)
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, sleep="ETH/USDT")
    cache = EvidenceCache()
    with cache.compute_lock:
        assert cache._start_child(_bundle(idx), TradingEngineV3._evidence_query)
        pid = cache._child.pid
        t0 = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
            ev = cache.compute(idx, "ETH/USDT", TradingEngineV3._evidence_query)
    assert time.monotonic() - t0 < 30
    assert _ser(ev) == _ser(TradingEngineV3._evidence_query(real, "ETH/USDT"))
    assert not _pid_alive(pid) and cache._child is None
    assert any("cevap yok" in rec.getMessage() for rec in caplog.records)


def test_a_child_that_burns_no_cpu_is_declared_hung_but_a_slow_busy_child_is_not(tmp_path, monkeypatch, hist, caplog):
    monkeypatch.setattr(EC.EvidenceChild, "STALL_TIMEOUT_S", 1.5)
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, sleep="ETH/USDT", busy="SOL/USDT")
    cache = EvidenceCache()
    with cache.compute_lock:
        assert cache._start_child(_bundle(idx), TradingEngineV3._evidence_query)
        child = cache._child
        ev = cache.compute(idx, "SOL/USDT", TradingEngineV3._evidence_query)       # 3 sn CPU yakar → asılı DEĞİL
        assert cache._child is child and child.computed == 1 and not idx.parent_calls()
        t0 = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
            ev2 = cache.compute(idx, "ETH/USDT", TradingEngineV3._evidence_query)  # CPU harcamadan uyur → asılı
        assert time.monotonic() - t0 < 20
    assert _ser(ev) == _ser(TradingEngineV3._evidence_query(real, "SOL/USDT"))
    assert _ser(ev2) == _ser(TradingEngineV3._evidence_query(real, "ETH/USDT"))
    assert cache._child is None
    assert any("asılı sayıldı" in rec.getMessage() for rec in caplog.records)


def test_a_compute_error_in_the_child_tour_recomputes_in_process_prewarm_only_logs(tmp_path, monkeypatch, hist, caplog):
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, fail="SOL/USDT")
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [idx])
    with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
        r.refresh_once()
        assert eng._evidence_cache().wait_idle(120)
    cache = eng._evidence_cache()
    assert any("ön ısıtılamadı" in rec.getMessage() and "SOL/USDT" in rec.getMessage() for rec in caplog.records)
    assert cache.stats["child_failures"] == 0 and cache.stats["errors"] == 1, cache.stats
    assert {k[0] for k in cache.keys()} == {"ETH/USDT", "AVAX/USDT"}, "sağlam alt süreç işi sürdürmeli"
    now = _now_for(real)
    assert _ser(eng._pattern_evidence("SOL/USDT", now)) == _ser(TradingEngineV3._evidence_query(real, "SOL/USDT"))
    # canlı alt süreçte tur isteği: hata → süreç içi yeniden hesap (bugünkü sonuç birebir)
    c2 = EvidenceCache()
    with c2.compute_lock:
        assert c2._start_child(_bundle(idx), TradingEngineV3._evidence_query)
        with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
            ev = c2.compute(idx, "SOL/USDT", TradingEngineV3._evidence_query)
        assert c2._child is not None and c2._child.alive, "hesap hatası alt süreci kapatmaz"
    c2._close_child()
    assert _ser(ev) == _ser(TradingEngineV3._evidence_query(real, "SOL/USDT"))
    assert any("tur süreç içi hesaplıyor" in rec.getMessage() for rec in caplog.records)


def test_an_error_everywhere_gives_todays_tour_result(tmp_path, monkeypatch, hist):
    """Sembol her süreçte istisna veriyorsa tur bugünkü gibi `None` döner (alt süreç açık ya da kapalı aynı)."""
    real = _build_index(hist)
    outs = {}
    for child in (True, False):
        idx = _Probe(real, tmp_path, fail_everywhere="SOL/USDT")
        eng = _engine(tmp_path / str(child), monkeypatch, child=child)
        r = _refresher(eng, [idx])
        r.refresh_once()
        assert eng._evidence_cache().wait_idle(120)
        outs[child] = {s: eng._pattern_evidence(s, _now_for(real)) for s in SYMS}
    assert outs[True]["SOL/USDT"] is None and outs[False]["SOL/USDT"] is None
    assert _ser(outs[True]) == _ser(outs[False])


def test_no_fork_on_this_platform_falls_back_in_process(tmp_path, monkeypatch, hist, caplog):
    import multiprocessing as mp
    real_ctx = mp.get_context

    def no_fork(method=None):
        if method == "fork":
            raise ValueError("cannot find context for 'fork'")
        return real_ctx(method)
    monkeypatch.setattr(mp, "get_context", no_fork)
    real = _build_index(hist)
    idx = _Probe(real, tmp_path)
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [idx])
    with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
        r.refresh_once()
        assert eng._evidence_cache().wait_idle(120)
    assert any("başlatılamadı" in rec.getMessage() for rec in caplog.records)
    assert len(idx.parent_calls()) == 2 * len(SYMS) and not idx.child_calls()
    now = _now_for(real)
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(real, s))


def test_kill_switch_off_never_forks_and_is_todays_path(tmp_path, monkeypatch, hist):
    def boom(*a, **k):
        raise AssertionError("anahtar kapalıyken alt süreç kurulmamalı")
    monkeypatch.setattr(EC.EvidenceChild, "start", classmethod(boom))
    real = _build_index(hist)
    idx = _Probe(real, tmp_path)
    eng = _engine(tmp_path, monkeypatch, child=False)
    r = _refresher(eng, [idx])
    r.refresh_once()
    cache = eng._evidence_cache()
    assert cache.wait_idle(120)
    assert cache.stats["child_started"] == 0 and cache.stats["child_failures"] == 0
    assert len(idx.parent_calls()) == 2 * len(SYMS) and not idx.child_calls()
    now = _now_for(real)
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(real, s))


def test_the_switch_defaults_on_and_config_can_turn_it_off(tmp_path, monkeypatch):
    assert HistorySection().evidence_subprocess is True
    assert TE._engine(tmp_path, monkeypatch)._evidence_subprocess_on() is True
    assert load_v3({"history": {"evidence_subprocess": False}}).history.evidence_subprocess is False
    partial = TradingEngineV3.__new__(TradingEngineV3)            # config'siz kısmi nesne → bugünkü yol
    assert partial._evidence_subprocess_on() is False


# ------------------------------------------------------------------ 5) yayım anı ve sürüm görünürlüğü
def test_the_publish_moment_and_version_visibility_are_unchanged(tmp_path, monkeypatch, hist):
    starts = []
    real_start = EC.EvidenceChild.start.__func__

    def slow_start(cls, *a, **k):
        starts.append(threading.current_thread().name)
        time.sleep(1.0)                                       # yavaş fork yayımı GECİKTİRMEMELİ
        return real_start(cls, *a, **k)
    monkeypatch.setattr(EC.EvidenceChild, "start", classmethod(slow_start))
    real = _build_index(hist)
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [real])
    t0 = time.monotonic()
    out = r.refresh_once()
    took = time.monotonic() - t0
    assert out.get("published") and out["index_version"] == 1
    assert r.bundle.engine is real and r.bundle.version == 1, "paket yayım anında görünür"
    assert eng._pattern_engine_and_version() == (real, 1)
    assert took < 0.8, ("yayım alt süreç kurulumunu beklememeli", took)
    assert eng._evidence_cache().wait_idle(120)
    assert starts == [PREWARM_THREAD], "fork ön ısıtma işçisinde olur, yenileyici iş parçacığında DEĞİL"


# ------------------------------------------------------------------ 6) bellek
def test_the_parent_keeps_no_extra_index_copy_and_frees_the_old_engine(tmp_path, monkeypatch, hist):
    eng = _engine(tmp_path, monkeypatch)
    tracemalloc.start()
    try:
        base0 = tracemalloc.get_traced_memory()[0]
        v1 = _build_index(hist, drop_last=1)
        index_bytes = tracemalloc.get_traced_memory()[0] - base0
        ref_v1 = weakref.ref(v1)
        v2 = _build_index(hist)
        r = _refresher(eng, [v1, v2])
        del v1, v2
        gc.collect()
        before = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        r.refresh_once()
        cache = eng._evidence_cache()
        assert cache.wait_idle(120)
        peak = tracemalloc.get_traced_memory()[1] - before
        assert cache.stats["child_computed"] == len(SYMS)
        assert peak < 0.1 * index_bytes, ("ebeveyn indeks boyutunda bellek ayırdı", peak, index_bytes)
        r.refresh_once()                                      # sürüm 2: sürüm 1 erişilemez olmalı
        assert cache.wait_idle(120)
    finally:
        tracemalloc.stop()
    gc.collect()
    assert ref_v1() is None, "eski indeks ebeveynde tutuluyor (alt süreç/önbellek referansı)"
    assert cache._child is None and not EC.parent_gc_frozen()
    assert cache.stats["child_private_mb_max"] is not None and cache.stats["child_private_mb_max"] > 0


# ------------------------------------------------------------------ 7) sentetik çekişme ölçümü
def _tour_work(frames) -> float:
    """Turun numpy/pandas ağırlıklı adımlarının vekili (ajan göstergeleri): sık ve kısa GIL bırakan iş."""
    from tradingbot.patterns.features import build_feature_frame
    t0 = time.perf_counter()
    for i in range(8):
        build_feature_frame(frames[i % len(frames)], "1h")
    return time.perf_counter() - t0


def _lag_sampler(stop: threading.Event, lags: list) -> None:
    """Box zamanlayıcısı vekili: 1 ms uyur, uyanma gecikmesini (GIL'i geri alma dahil) ölçer."""
    while not stop.is_set():
        t = time.perf_counter()
        time.sleep(0.001)
        lags.append(time.perf_counter() - t - 0.001)


def test_synthetic_tour_and_periodic_thread_are_not_slowed_by_the_child_prewarm(tmp_path, monkeypatch):
    syms = ("ETH/USDT", "SOL/USDT", "AVAX/USDT", "BTC/USDT", "BNB/USDT", "XRP/USDT")
    hist = _history(syms, n=600)
    real = _build_index(hist)
    frames = [_candles(1000, seed=100 + i, tf_ms=3_600_000) for i in range(2)]
    _tour_work(frames)                                        # ısınma
    alone = min(_tour_work(frames) for _ in range(2))

    cache = EvidenceCache()
    keys = [(s, 1, int(real.candles[(s, "futures", "4h")]["timestamp"].iloc[-1])) for s in syms]
    cache.request_prewarm(_bundle(real), keys, TradingEngineV3._evidence_query, lambda b: True, use_child=True)
    end = time.monotonic() + 30
    while cache._child is None and time.monotonic() < end:
        time.sleep(0.01)
    assert cache._child is not None, "alt süreç başlamadı"
    stop, lags = threading.Event(), []
    th = threading.Thread(target=_lag_sampler, args=(stop, lags), daemon=True)
    th.start()
    loaded = _tour_work(frames)
    stop.set()
    th.join(10)
    still_running = cache._running and cache._child is not None
    assert cache.wait_idle(300)
    cache.stop()
    assert still_running, "ölçüm ön ısıtma sürerken yapılmalı (aksi hâlde test anlamsız)"
    ratio = loaded / alone
    p90_ms = 1000 * sorted(lags)[int(0.9 * len(lags))]
    print(f"\nsentetik tur: yalnız {alone:.2f} sn, alt süreç ön ısıtması sürerken {loaded:.2f} sn (x{ratio:.2f}); "
          f"periyodik iş parçacığı gecikmesi p50 {1000 * statistics.median(lags):.2f} ms, p90 {p90_ms:.2f} ms")
    assert ratio < 3.0, ("tur alt süreç ön ısıtmasıyla yavaşladı", alone, loaded)
    assert p90_ms < 25.0, ("periyodik iş parçacığı GIL bekledi", p90_ms)
    assert cache.stats["child_computed"] == len(syms)


# ------------------------------------------------------------------ 8) işletim sistemi ayarları
def _proc_field(pid, idx):
    with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
        return fh.read().rsplit(")", 1)[1].split()[idx]


def test_the_child_is_niced_first_in_line_for_oom_and_dies_with_its_parent(tmp_path, hist):
    import signal
    import subprocess
    real = _build_index(hist)
    cache = EvidenceCache()
    with cache.compute_lock:
        assert cache._start_child(_bundle(real), TradingEngineV3._evidence_query)
        pid = cache._child.pid
        assert int(_proc_field(pid, 16)) == min(19, os.nice(0) + EC.EvidenceChild.NICE)      # nice alanı
        with open(f"/proc/{pid}/oom_score_adj", encoding="ascii") as fh:
            assert int(fh.read()) == EC.EvidenceChild.OOM_SCORE_ADJ
    cache._close_child()
    assert not _pid_alive(pid) and not EC.parent_gc_frozen()

    # ebeveyn SIGKILL ile ölünce alt süreç de ölür (tekil kilit tanımlayıcısını devralmış hâlde ortada kalmaz)
    script = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(Path(__file__).parent)!r}); sys.path.insert(0, {str(Path(__file__).parent.parent)!r})\n"
        "from test_patterns import _candles\n"
        "from tradingbot.patterns import SimilarPatternEngine\n"
        "from tradingbot.patterns.evidence_child import EvidenceChild\n"
        "from tradingbot.engine_v3 import TradingEngineV3\n"
        "e = SimilarPatternEngine(min_sample=30, horizon=24)\n"
        "e.add_series('ETH/USDT', 'futures', '4h', _candles(300, seed=3, tf_ms=14_400_000))\n"
        "c = EvidenceChild.start(e, TradingEngineV3._evidence_query, version=1)\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(120)\n")
    helper = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        line = helper.stdout.readline()
        assert line.strip().isdigit(), line
        grandchild = int(line)
        assert _pid_alive(grandchild)
        helper.send_signal(signal.SIGKILL)
        helper.wait(10)
        end = time.monotonic() + 10
        while _pid_alive(grandchild) and time.monotonic() < end:
            time.sleep(0.05)
        assert not _pid_alive(grandchild), "ebeveyni ölen alt süreç yaşamaya devam etti"
    finally:
        if helper.poll() is None:
            helper.kill()


# ------------------------------------------------------------------ 9) karar kimliği
def test_the_switch_is_not_part_of_the_decision_identity(tmp_path, monkeypatch):
    """`config_hash()` karar günlüğüne/provenansa yazılır; karar-nötr anahtar onu DEĞİŞTİRMEZ (alandan önceki özetle aynı)."""
    from dataclasses import asdict

    from tradingbot.core import payload_hash
    eng = TE._engine(tmp_path, monkeypatch)
    d = asdict(eng.cfg.v3)
    d.pop("shared_experience", None)
    if (d.get("learning_mode") or {}).get("extra_entries") == "open":
        d["learning_mode"].pop("extra_entries", None)
    d["history"].pop("evidence_subprocess")
    want = payload_hash(d)
    hashes = set()
    for on in (True, False):
        eng.cfg.v3.history.evidence_subprocess = on
        eng.__dict__.pop("_config_hash_cache", None)
        hashes.add(eng.config_hash())
    assert hashes == {want}
