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
8. Fork'un KENDİSİ asılırsa (OpenBLAS fork öncesi kancası; gerçek tetikle de sınanır) worker süresiz DONMAZ: deadman
   (SIGALRM, varsayılan eylem) onu sonlandırır, işaret dosyası kalır ve yeniden başlayan worker alt süreci denemez.
   SIGALRM başka bir amaçla kullanılıyorsa fork yapılmaz. Worker kodunda yeni BLAS çağrısı izin listesi testini kırar.
9. Alt süreç ebeveyn ölünce PDEATHSIG olmadan da çıkar (boru EOF), SIGTERM'i yok sayar.
10. Geri dönüş anahtarının yolu bugünkü log satırını yazar, ek kilit almaz; alt süreçli işin satırı işin kendi kaydından
    yazılır. Anahtar yalnız gerçek bool ve yalnız Linux'ta etkili; etkin değer başlangıçta loglanır.
11. Kapanış ön ısıtma işini BEKLEMEZ (ana iş parçacığı dönünce, SIGTERM'le, `watch`'ın `stop`'uyla): alt süreç öldürülür,
    kapanıştan sonra yeni fork yok. Alt süreç ebeveynin soket/boru/dosya tanımlayıcılarını tutmaz; `close` bekçi
    borusuna güvenmeden zaman aşımına uyar. Fork işaret dosyası makineye özgüdür: yedeğe girmez, geri yüklemede kalır.
12. BELLEK (düzeltme turu 3): eski sürümün alt süreci yeni yayım ANINDA ölür (uçuştaki tur süreç içi aynı kanıtı alır);
    fork anında canlı olan eski indeks ebeveynde bırakılınca alt süreç öldürülüp yeniden kurulur — eski indeksin
    sayfaları alt süreçte KALMAZ (ölçüm testi, kancasız kontrol koluyla); bellek payı daralınca fork yok / alt süreç
    öldürülür ve iş süreç içi sürer. Hepsinde kanıt aynı, arıza sayılmaz (bellek koruması uyarı yazar).

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
    d["history"].pop("evidence_fast_knn")   # 2026-10-06: hızlı kNN anahtarı da karar kimliğine girmez
    d.pop("m2x_aggressive", None)          # M2X ayna defteri de karar kimliğine girmez (config_hash ile aynı)
    want = payload_hash(d)
    hashes = set()
    for on in (True, False):
        eng.cfg.v3.history.evidence_subprocess = on
        eng.__dict__.pop("_config_hash_cache", None)
        hashes.add(eng.config_hash())
    assert hashes == {want}


# ------------------------------------------------------------------ 10) fork'un KENDİSİ asılırsa: deadman + işaret dosyası
REPO = str(Path(__file__).resolve().parent.parent)
TESTS = str(Path(__file__).resolve().parent)


def _script(body: str, marker: Path) -> str:
    return ("import os, sys, time, threading, signal\n"
            f"sys.path.insert(0, {REPO!r}); sys.path.insert(0, {TESTS!r})\n"
            f"MARKER = {str(marker)!r}\n" + body)


def test_a_fork_that_hangs_is_ended_by_the_deadman_and_the_restarted_worker_keeps_the_child_off(tmp_path, hist,
                                                                                                caplog, monkeypatch):
    """Fork öncesi kanca asılırsa (OpenBLAS'ın havuz kapatması gibi) worker SÜRESİZ donmaz: SIGALRM'nin varsayılan
    eylemi onu sonlandırır (systemd yeniden başlatır). İşaret dosyası kalır; yeniden başlayan worker alt süreci hiç
    denemez (bir kez uyarır, süreç içi hesaplar); dosya silinince yeniden dener."""
    import signal
    import subprocess
    marker = tmp_path / EC.MARKER_NAME
    body = (
        "hang = threading.Event()\n"
        # evidence_child'dan ÖNCE kaydedilir → CPython 'before' kancalarını ters sırada koşar → deadman kurulduktan
        # SONRA asılır (gerçekte asılan OpenBLAS'ın C kancası zaten bütün Python kancalarından sonra koşar)
        "os.register_at_fork(before=lambda: hang.is_set() and time.sleep(120))\n"
        "from test_patterns import _candles\n"
        "from tradingbot.patterns import SimilarPatternEngine\n"
        "from tradingbot.patterns import evidence_child as EC\n"
        "from tradingbot.engine_v3 import TradingEngineV3\n"
        "e = SimilarPatternEngine(min_sample=30, horizon=24)\n"
        "e.add_series('ETH/USDT', 'futures', '4h', _candles(300, seed=3, tf_ms=14_400_000))\n"
        "EC.EvidenceChild.FORK_DEADMAN_S = 1.0\n"
        "c = EC.EvidenceChild.start(e, TradingEngineV3._evidence_query, version=1, marker=MARKER)\n"
        "c.close()\n"
        "print('healthy', os.path.exists(MARKER), signal.getitimer(signal.ITIMER_REAL)[0], flush=True)\n"
        "hang.set()\n"
        "EC.EvidenceChild.start(e, TradingEngineV3._evidence_query, version=2, marker=MARKER)\n"
        "print('UNREACHABLE', flush=True)\n")
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-c", _script(body, marker)], capture_output=True, text=True, timeout=90)
    took = time.monotonic() - t0
    assert p.returncode == -signal.SIGALRM, (p.returncode, p.stdout, p.stderr[-2000:])
    assert "healthy False 0.0" in p.stdout, "sağlam fork işaret ya da zamanlayıcı bırakmaz"
    assert "UNREACHABLE" not in p.stdout
    assert took < 60, took
    assert marker.exists(), "deadman'in sonlandırdığı fork işaret dosyasını bırakır"

    # yeniden başlayan worker: alt süreç HİÇ denenmez, uyarı bir kez, kanıt süreç içi ve aynı
    real = _build_index(hist)
    eng = _engine(tmp_path, monkeypatch)
    eng.cfg.state_path.mkdir(parents=True, exist_ok=True)
    marker2 = eng._evidence_fork_marker()
    assert marker2 == eng.cfg.state_path / EC.MARKER_NAME
    marker2.write_bytes(marker.read_bytes())
    calls = []
    real_start = EC.EvidenceChild.start.__func__
    monkeypatch.setattr(EC.EvidenceChild, "start",
                        classmethod(lambda cls, *a, **k: calls.append(1) or real_start(cls, *a, **k)))
    idx = _Probe(real, tmp_path)
    r = _refresher(eng, [idx, _Probe(real, tmp_path)])
    with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
        r.refresh_once()
        cache = eng._evidence_cache()
        assert cache.wait_idle(120)
        r.refresh_once()                                          # ikinci yayım: yine kapalı, ikinci uyarı yok
        assert cache.wait_idle(120)
    assert calls == [], "işaret varken fork denenmemeli"
    warns = [rec.getMessage() for rec in caplog.records if "alt süreci KAPALI" in rec.getMessage()]
    assert len(warns) == 1 and "fork ederken sonlandı" in warns[0], warns
    assert "fork ederken sonlandı" in cache.stats["child_blocked"] and cache.stats["child_started"] == 0
    now = _now_for(real)
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(real, s))
    marker2.unlink()                                              # sahip dosyayı siler → bir sonraki yayımda yeniden
    builds = [_build_index(hist, drop_last=1)]
    r._build_fn = lambda syms: (builds.pop(0), {})
    r.refresh_once()
    assert cache.wait_idle(120)
    assert calls == [1] and cache.stats["child_started"] == 1 and cache.stats["child_blocked"] == ""
    assert not marker2.exists()


def test_with_a_real_multithreaded_blas_job_in_flight_a_fork_never_freezes_the_worker(tmp_path):
    """GERÇEK tehlike: başka bir iş parçacığı çok iş parçacıklı matris çarpımı koşarken art arda fork. Her koşu ya
    biter ya da deadman worker'ı sonlandırır — SÜRESİZ DONMA YOK (zaman aşımı testi düşürür). OpenBLAS havuzu yoksa
    (tek iş parçacığı ya da OpenBLAS değil) asılma oluşamaz: test bir şey sınamadan geçmesin diye ATLANIR (deadman'i
    `test_a_fork_that_hangs_is_ended_by_the_deadman...` benzetimle her makinede sınar)."""
    import signal
    import subprocess
    n_blas = EC.blas_threads()
    if n_blas is None or n_blas <= 1:
        pytest.skip(f"OpenBLAS iş parçacığı havuzu yok ({n_blas}): gerçek fork asılması bu makinede oluşamaz")
    marker = tmp_path / EC.MARKER_NAME
    body = (
        "import numpy as np\n"
        "from tradingbot.patterns import evidence_child as EC\n"
        "EC.EvidenceChild.FORK_DEADMAN_S = 2.0\n"
        "A = np.random.default_rng(0).random((300, 300))\n"
        "A @ A\n"
        "def work():\n"
        "    while True:\n"
        "        A @ A\n"
        "threading.Thread(target=work, daemon=True).start()\n"
        "class E: pass\n"
        "e = E()\n"
        "for i in range(60):\n"
        "    c = EC.EvidenceChild.start(e, lambda eng, s: {'x': 1.0}, version=i, marker=MARKER)\n"
        "    assert c.compute('S') == {'x': 1.0}\n"
        "    c.close()\n"
        "print('completed', EC.blas_threads(), flush=True)\n")
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-c", _script(body, marker)], capture_output=True, text=True, timeout=180)
    took = time.monotonic() - t0
    print(f"\ngerçek BLAS + fork: çıkış {p.returncode}, {took:.1f} sn, {p.stdout.strip()!r}")
    assert p.returncode in (0, -signal.SIGALRM), (p.returncode, p.stdout, p.stderr[-2000:])
    if p.returncode == 0:
        assert "completed" in p.stdout and not marker.exists()
    else:
        assert marker.exists(), "deadman'in sonlandırdığı fork işaret dosyasını bırakır"


def test_the_deadman_refuses_to_fork_when_sigalrm_is_taken_and_leaves_no_timer(hist):
    """SIGALRM'yi başka biri kullanıyorsa fork YAPILMAZ (asılı fork'u sonlandıramazdık / başkasının zamanlayıcısını
    bozardık); sağlam fork'tan sonra zamanlayıcı kalmaz; başka fork'lar (bayraksız) zamanlayıcı kurmaz."""
    import signal
    real = _build_index(hist)
    q = TradingEngineV3._evidence_query
    assert EC.deadman_unavailable() is None
    c = EC.EvidenceChild.start(real, q, version=1)
    try:
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        assert _ser(c.compute("ETH/USDT")) == _ser(q(real, "ETH/USDT"))
    finally:
        c.close()
    EC._deadman_before_fork()                                     # bayraksız fork (başka kod): zamanlayıcı YOK
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    old = signal.signal(signal.SIGALRM, lambda *a: None)
    try:
        assert "işleyicisi" in (EC.deadman_unavailable() or "")
        with pytest.raises(EC.EvidenceChildError, match="fork koruması kurulamadı"):
            EC.EvidenceChild.start(real, q, version=1)
    finally:
        signal.signal(signal.SIGALRM, old)
    signal.setitimer(signal.ITIMER_REAL, 1000.0)
    try:
        assert "ITIMER_REAL" in (EC.deadman_unavailable() or "")
        with pytest.raises(EC.EvidenceChildError, match="fork koruması kurulamadı"):
            EC.EvidenceChild.start(real, q, version=1)
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 900, "başkasının zamanlayıcısı bozulmamalı"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    assert not EC.parent_gc_frozen()


def test_the_fork_marker_records_this_process_and_recognises_another(tmp_path):
    m = tmp_path / EC.MARKER_NAME
    assert EC.fork_hung_before(None) is None and EC.fork_hung_before(m) is None
    assert EC._mark(m) and m.exists()
    assert EC.fork_hung_before(m) is None and not m.exists(), "bu sürecin kendi kalıntısı sessizce silinir"
    m.write_text("12345:999 1700000000\n", encoding="ascii")
    msg = EC.fork_hung_before(m)
    assert msg and "pid 12345" in msg and "2023-11-14 22:13:20 UTC" in msg and m.exists()
    pid, start = EC._self_identity().split(":")
    m.write_text(f"{pid}:{int(start) + 1} 1700000000\n", encoding="ascii")      # aynı pid, başka süreç (pid yeniden)
    assert EC.fork_hung_before(m), "pid yeniden kullanılsa da önceki süreç tanınır"
    assert not EC._mark(tmp_path / "yok" / "m"), "yazılamazsa False (deadman yine korur)"


def test_the_child_exits_on_parent_death_even_without_pdeathsig(tmp_path):
    """Alt süreç devraldığı ebeveyn boru ucunu kapatır: ebeveyn ölünce `recv` EOF görür ve çıkar — PR_SET_PDEATHSIG
    olmasa da (Linux dışı / prctl başarısız). Yoksa devraldığı tekil kilit tanımlayıcısıyla ortada kalırdı."""
    import signal
    import subprocess
    body = (
        "from test_patterns import _candles\n"
        "from tradingbot.patterns import SimilarPatternEngine\n"
        "from tradingbot.patterns import evidence_child as EC\n"
        "from tradingbot.engine_v3 import TradingEngineV3\n"
        "EC._PRCTL = lambda *a: 0\n"                                  # PDEATHSIG YOK
        "e = SimilarPatternEngine(min_sample=30, horizon=24)\n"
        "e.add_series('ETH/USDT', 'futures', '4h', _candles(300, seed=3, tf_ms=14_400_000))\n"
        "c = EC.EvidenceChild.start(e, TradingEngineV3._evidence_query, version=1)\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(120)\n")
    helper = subprocess.Popen([sys.executable, "-c", _script(body, tmp_path / "m")], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True)
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
        assert not _pid_alive(grandchild), "ebeveyni ölen alt süreç (PDEATHSIG'siz) boru EOF'uyla çıkmalı"
    finally:
        if helper.poll() is None:
            helper.kill()


def test_the_child_ignores_sigterm_so_a_control_group_stop_does_not_break_the_final_tour(hist):
    import signal
    real = _build_index(hist)
    q = TradingEngineV3._evidence_query
    c = EC.EvidenceChild.start(real, q, version=1)
    try:
        os.kill(c.pid, signal.SIGTERM)
        time.sleep(0.3)
        assert c.alive, "SIGTERM alt süreci öldürmemeli (kapatmayı ebeveyn yapar)"
        assert _ser(c.compute("SOL/USDT")) == _ser(q(real, "SOL/USDT"))
    finally:
        pid = c.pid
        c.close()
    assert not _pid_alive(pid)


# ------------------------------------------------------------------ 11) log satırları ve geri dönüş anahtarının yolu
def test_the_kill_switch_path_logs_todays_line_and_takes_no_extra_lock(hist, caplog):
    import re
    real = _build_index(hist)
    cache = EvidenceCache()
    closes = []
    cache._close_child = lambda: closes.append(1)
    keys = [(s, 1, int(real.candles[(s, "futures", "4h")]["timestamp"].iloc[-1])) for s in SYMS]
    with caplog.at_level(logging.INFO, logger="tradingbot.patterns.evidence_cache"):
        cache.request_prewarm(_bundle(real), keys, TradingEngineV3._evidence_query, lambda b: True)
        assert cache.wait_idle(120)
    cache.stop()
    msgs = [rec.getMessage() for rec in caplog.records if "ön ısıtıldı" in rec.getMessage()]
    assert len(msgs) == 1 and re.fullmatch(r"pattern kanıtı ön ısıtıldı: sürüm 1, 3 sembol hesaplandı, 0 zaten hazırdı, "
                                           r"\d+\.\d sn", msgs[0]), msgs
    assert closes == [], "geri dönüş anahtarında iş sonu kapatma (ek compute_lock) YOK"
    assert cache.stats["child_started"] == 0


def test_the_child_job_log_line_counts_child_and_in_process_symbols_from_the_job_record(tmp_path, monkeypatch, hist,
                                                                                         caplog):
    """Alt süreç iş ortasında arızalansa da satır işin kendi kaydından yazılır: pid, bellek, kim kaç sembol."""
    import re
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, crash="SOL/USDT")
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [idx])
    with caplog.at_level(logging.INFO, logger="tradingbot.patterns.evidence_cache"):
        r.refresh_once()
        assert eng._evidence_cache().wait_idle(120)
    msgs = [rec.getMessage() for rec in caplog.records if "ön ısıtıldı" in rec.getMessage()]
    assert len(msgs) == 1, msgs
    m = re.search(r"\(alt süreç pid (\d+), özel bellek (\d+|\?) MB; alt süreçte 1, süreç içi 2 sembol\)$", msgs[0])
    assert m, msgs[0]
    assert msgs[0].startswith("pattern kanıtı ön ısıtıldı: sürüm 1, 3 sembol hesaplandı, 0 zaten hazırdı, ")


# ------------------------------------------------------------------ 12) anahtar: yalnız Linux, yalnız gerçek bool, görünür
def test_the_switch_is_linux_only_bool_only_and_logged_at_startup(tmp_path, monkeypatch, caplog):
    from tradingbot.core.errors import ConfigError
    with pytest.raises(ConfigError, match="evidence_subprocess"):
        load_v3({"history": {"evidence_subprocess": "false"}})              # tırnaklı YAML değeri: açık hata
    eng = TE._engine(tmp_path, monkeypatch)
    assert eng._evidence_subprocess_on() is True
    with caplog.at_level(logging.INFO, logger="tradingbot.engine_v3"):
        eng._log_evidence_subprocess_setting()
    line = [rec.getMessage() for rec in caplog.records if "pattern kanıtı sorguları" in rec.getMessage()]
    assert line and "ALT SÜREÇTE" in line[0] and "OpenBLAS iş parçacığı" in line[0], line
    eng.cfg.state_path.mkdir(parents=True, exist_ok=True)
    eng._evidence_fork_marker().write_text("1:1 1700000000\n", encoding="ascii")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="tradingbot.engine_v3"):
        eng._log_evidence_subprocess_setting()
    assert any("KAPALI kalacak" in rec.getMessage() for rec in caplog.records)
    eng.cfg.v3.history.evidence_subprocess = "false"                         # doğrulamayı atlamış değer: AÇMAZ
    assert eng._evidence_subprocess_on() is False
    eng.cfg.v3.history.evidence_subprocess = True
    monkeypatch.setattr(EC, "supported", lambda: False)
    assert eng._evidence_subprocess_on() is False
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="tradingbot.engine_v3"):
        eng._log_evidence_subprocess_setting()
    assert any("süreç içinde (platform Linux değil)" in rec.getMessage() for rec in caplog.records)
    n = EC.blas_threads()
    assert n is None or n >= 1


def test_the_publish_line_reports_the_index_build_time(tmp_path, monkeypatch, hist, caplog):
    import re
    real = _build_index(hist)
    eng = _engine(tmp_path, monkeypatch, child=False)
    r = _refresher(eng, [real])
    with caplog.at_level(logging.INFO, logger="tradingbot.patterns.refresher"):
        assert r.refresh_once().get("published")
    msgs = [rec.getMessage() for rec in caplog.records if "pattern indeksi yenilendi" in rec.getMessage()]
    assert msgs and re.fullmatch(r"pattern indeksi yenilendi: sürüm 1, \d+ olay, 3 seri, kurulum \d+\.\d sn", msgs[0])
    assert eng._evidence_cache().wait_idle(120)


# ------------------------------------------------------------------ 13) değişmez: worker'da yeni BLAS çağrısı yok
#: Worker kodundaki BLAS'a gidebilen çağrılar (AST taraması). Hepsi fork anında tehlikesiz sınıfta: vektör/matris-vektör
#: (seviye 1–2), küçük `corrcoef`/`cov`, tamsayı çarpımı (BLAS'a gitmez), pandas `rolling.cov` (BLAS'a gitmez). YENİ
#: bir satır eklemeden önce: çok iş parçacıklı seviye-3 BLAS (matris-matris, `np.linalg` ayrıştırması) başka bir iş
#: parçacığında fork'la çakışırsa worker'ı deadman sonlandırır (`evidence_child` başlığı). Bunu değerlendirip listeye ekle.
_BLAS_ALLOWED = {
    ("tradingbot/agents/analog.py", "np.dot"): 1,                    # 1-B nokta çarpımı (W noktalı)
    ("tradingbot/agents/analog.py", "np.linalg.norm"): 1,            # vektör normu
    ("tradingbot/coinhead/specialists.py", "np.corrcoef"): 1,        # 2×120
    ("tradingbot/coinhead/specialists.py", "np.cov"): 1,             # 2×120
    ("tradingbot/learn/model.py", "@"): 3,                           # LogisticModel: matris-vektör (gemv)
    ("tradingbot/learn/retrieval.py", "@"): 1,                       # matris-vektör
    ("tradingbot/learn/retrieval.py", "np.linalg.norm"): 2,          # satır normları / vektör normu
    ("tradingbot/patterns/engine.py", "np.corrcoef"): 1,             # 2×16 yol (sorgu döngüsü)
    ("tradingbot/patterns/features.py", "cov"): 1,                   # pandas rolling cov (BLAS değil)
    ("tradingbot/shared_experience/advisor_eval.py", "@"): 2,        # int64 × int64 (BLAS değil)
}
_BLAS_NAMES = {"dot", "matmul", "vdot", "inner", "outer", "tensordot", "einsum", "cov", "corrcoef", "polyfit", "lstsq",
               "solve", "inv", "pinv", "svd", "eig", "eigh", "eigvals", "qr", "cholesky", "det", "norm", "multi_dot",
               "matrix_power", "slogdet", "kron"}


def test_no_new_blas_call_site_appears_in_runtime_code_without_reviewing_the_fork_hazard():
    import ast
    from collections import Counter
    root = Path(REPO)
    found: Counter = Counter()
    for p in sorted((root / "tradingbot").rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.MatMult):
                found[(rel, "@")] += 1
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _BLAS_NAMES:
                base = ast.unparse(node.func.value)
                name = f"{base}.{node.func.attr}" if base.startswith(("np", "numpy", "scipy")) else node.func.attr
                found[(rel, name)] += 1
    extra = found - Counter(_BLAS_ALLOWED)
    assert not extra, ("worker koduna yeni BLAS çağrısı: fork asılma riskini değerlendir (evidence_child başlığı) ve "
                       f"_BLAS_ALLOWED'a gerekçesiyle ekle: {dict(extra)}")


# ------------------------------------------------------------------ 14) kapanış alt süreci beklemez (düzeltme turu 2)
_EXIT_BODY = (
    "import logging\n"
    "logging.basicConfig(level=logging.WARNING)\n"
    "from tradingbot.patterns.evidence_cache import EvidenceCache\n"
    "class Eng: pass\n"
    "class B:\n"
    "    engine = Eng(); version = 3\n"
    "def compute(eng, sym):\n"
    "    time.sleep(5.0); return {'s': sym}\n"                     # iş: 40 sembol × 5 sn = 200 sn
    "stop = threading.Event()\n"
    "signal.signal(signal.SIGTERM, lambda *a: stop.set())\n"        # `watch`'ın SIGTERM bayrağı
    "cache = EvidenceCache()\n"
    "cache.request_prewarm(B(), [(f'S{i}', 3, 1) for i in range(40)], compute, lambda b: True, use_child=True)\n"
    "end = time.monotonic() + 30\n"
    "while cache._child is None and time.monotonic() < end:\n"
    "    time.sleep(0.01)\n"
    "print('child', cache._child.pid, flush=True)\n")
_EXIT_TAILS = {
    # ana iş parçacığı hemen döner: yalnız çıkış kancası korur
    "return": "",
    # SIGTERM bayrağı → döngü biter → `stop()` ÇAĞRILMADAN çıkış (çıkış kancası korur)
    "sigterm": "while not stop.is_set():\n    time.sleep(0.05)\n",
    # `watch` kapanışı: SIGTERM → `stop()` (TradingEngineV3.stop_pattern_evidence) → sonra gelen yayım fork ETMEZ
    "stop": ("while not stop.is_set():\n    time.sleep(0.05)\n"
             "t = time.monotonic()\n"
             "cache.stop(2.0)\n"
             "print('stopped', round(time.monotonic() - t, 2), cache._child is None, flush=True)\n"
             "cache.request_prewarm(B(), [('X', 4, 1)], compute, lambda b: True, use_child=True)\n"
             "time.sleep(0.5)\n"
             "print('after', cache._thread.is_alive(), cache._job, cache.stats['child_started'], flush=True)\n"),
}


@pytest.mark.parametrize("how", sorted(_EXIT_TAILS))
def test_process_exit_never_waits_for_the_prewarm_job_and_leaves_no_child(tmp_path, how):
    """Denetim bulgusu (düzeltme turu 2): alt süreç SIGTERM'i yok sayar; `multiprocessing`'in çıkış kancası daemon alt
    sürece SIGTERM yollayıp ZAMAN AŞIMSIZ `join` ettiği için süreç çıkışı bütün ön ısıtma işini bekliyordu (VPS'te
    7–14 dk → `systemctl stop` 90 sn'yi aşıp SIGKILL). Artık ana iş parçacığı dönünce, SIGTERM'le ya da `stop()` ile süreç
    birkaç saniyede çıkar, alt süreç öldürülür ve arıza uyarısı/süreç içi yedek yol YOKTUR."""
    import signal
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", _script(_EXIT_BODY + _EXIT_TAILS[how], tmp_path / "m")],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = p.stdout.readline()
        assert line.startswith("child "), (line, p.stderr.read() if p.poll() is not None else "")
        child = int(line.split()[1])
        assert _pid_alive(child)
        t0 = time.monotonic()
        if how != "return":
            p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=120)
        took = time.monotonic() - t0
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, (p.returncode, err[-2000:])
    assert took < 10, f"çıkış {took:.1f} sn sürdü (ön ısıtma işi 200 sn)"
    assert not _pid_alive(child), "alt süreç ebeveynden sonra yaşamamalı"
    assert "arızalandı" not in err and "süreç içi hesaplanıyor" not in err, err[-2000:]
    if how == "stop":
        stopped = [ln for ln in out.splitlines() if ln.startswith("stopped ")]
        assert stopped and float(stopped[0].split()[1]) < 3.0 and stopped[0].endswith("True"), out
        assert "after False None 1" in out, f"stop() sonrası yayım işçiyi yeniden başlatmamalı / fork etmemeli: {out}"


def test_the_exit_hook_kills_live_children_and_refuses_new_forks(tmp_path):
    """Çıkış kancası (`multiprocessing.util` içe aktarıldıktan sonra, ilk fork'ta bir kez kaydedilir): canlı alt süreci
    öldürür ve biçer; ardından fork yapılmaz (`exiting()`)."""
    import subprocess
    body = (
        "from tradingbot.patterns import evidence_child as EC\n"
        "class E: pass\n"
        "e = E()\n"
        "c = EC.EvidenceChild.start(e, lambda eng, s: {'x': 1.0}, version=1)\n"
        "pid = c.pid\n"
        "EC._kill_live_children_at_exit()\n"
        "gone = not os.path.exists(f'/proc/{pid}')\n"
        "print('gone', gone, EC.exiting(), flush=True)\n"
        "try:\n"
        "    EC.EvidenceChild.start(e, lambda eng, s: {'x': 1.0}, version=2)\n"
        "    print('forked', flush=True)\n"
        "except EC.EvidenceChildError as exc:\n"
        "    print('refused', exc, flush=True)\n")
    p = subprocess.run([sys.executable, "-c", _script(body, tmp_path / "m")], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-2000:]
    assert "gone True True" in p.stdout, p.stdout
    assert "refused süreç kapanıyor" in p.stdout and "forked" not in p.stdout, p.stdout



def test_a_stop_that_lands_inside_the_fork_window_closes_the_new_child_and_computes_nothing(tmp_path, hist, monkeypatch,
                                                                                             caplog):
    """`stop()` alt süreç atanmadan (fork/el sıkışması sırasında) gelirse `kill` onu göremez: işçi atamadan SONRA
    kapanışı görür, yeni alt süreci kapatır ve sembolü süreç içi HESAPLAMAZ (kapanışı GIL için yarışarak geciktirmez);
    uyarı yazılmaz, alt süreç kalmaz."""
    real = _build_index(hist)
    idx = _Probe(real, tmp_path)
    cache = EvidenceCache()
    real_start = EC.EvidenceChild.start.__func__
    started = []

    def start(cls, *a, **k):
        cache._stop.set()                                         # `stop()` tam fork penceresinde
        c = real_start(cls, *a, **k)
        started.append(c.pid)
        return c
    monkeypatch.setattr(EC.EvidenceChild, "start", classmethod(start))
    keys = [(s, 1, int(real.candles[(s, "futures", "4h")]["timestamp"].iloc[-1])) for s in SYMS]
    with caplog.at_level(logging.INFO, logger="tradingbot.patterns.evidence_cache"):
        cache.request_prewarm(_bundle(idx), keys, TradingEngineV3._evidence_query, lambda b: True, use_child=True)
        end = time.monotonic() + 60
        while not started and time.monotonic() < end:
            time.sleep(0.01)
        cache._thread.join(60)
    assert started and not cache._thread.is_alive()
    assert not _pid_alive(started[0]) and cache._child is None and cache.stats["child_started"] == 0
    assert idx.calls() == [], "kapanışta hiçbir sembol (alt süreçte ya da süreç içi) hesaplanmamalı"
    assert not [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("kapanışta bırakıldı" in r.getMessage() for r in caplog.records)
    assert not EC.parent_gc_frozen()

def test_watch_shutdown_stops_the_pattern_evidence_before_releasing_the_lock(tmp_path, monkeypatch):
    """`watch` kapanışı izleyiciden sonra kanıt ön ısıtmasını durdurur (alt süreci öldürür) — kilit ve instance kaydı
    bırakılmadan ÖNCE. Gerçek motorda: önbellek yoksa hiçbir şey yapmaz; varsa kalıcı durdurur."""
    import argparse

    import test_protective_monitor_v1 as PM

    from tradingbot import cli
    from tradingbot.config import BotConfig
    cfg = BotConfig()
    cfg.project_root = tmp_path

    class _Eng(PM._WatchEngine):
        def stop_pattern_evidence(self):
            self.calls.append(("stop_evidence", (cfg.state_path / ".lock").exists()))

    fake = _Eng(monitor_ok=True)
    monkeypatch.setattr(cli, "_make_engine", lambda c, legacy=False: fake)
    monkeypatch.setattr(cli, "_print_tour", lambda s: None)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    args = argparse.Namespace(interval=1, scan_every=1, no_obsidian=True, no_wfo=True, exit_every=60, legacy=False,
                              families=None, no_paper=True)
    assert cli.cmd_watch(cfg, args) == 0
    assert fake.calls[-3:] == ["stop_monitor", "drain", ("stop_evidence", True)], fake.calls[-4:]
    assert not (cfg.state_path / ".lock").exists()

    eng = TE._engine(tmp_path / "real", monkeypatch)
    eng.__dict__.pop("_pattern_cache", None)
    eng.stop_pattern_evidence()                                   # önbellek yok: hata yok, kurulmaz
    assert "_pattern_cache" not in eng.__dict__
    cache = eng._evidence_cache()
    eng.stop_pattern_evidence()
    assert cache._closed and cache._stop.is_set()


def test_the_child_keeps_no_inherited_descriptor_of_the_parent(tmp_path, hist):
    """Denetim bulgusu: `fork` ebeveynin bütün tanımlayıcılarını kopyalar (HTTP soketleri, kilit dosyası, `subprocess`
    penceresindeki boru uçları). Alt süreç bunları /dev/null'a çevirir: ebeveyn soketi kapatınca karşı uç HEMEN EOF görür,
    boru yazma ucunu kapatınca okuyan EOF görür (kasa `git`'inin `communicate`'i alt süreci beklemez). Kanıt aynı."""
    import select
    import socket
    real = _build_index(hist)
    q = TradingEngineV3._evidence_query
    a, b = socket.socketpair()
    r, w = os.pipe()
    fh = open(tmp_path / "held.txt", "w", encoding="utf-8")     # noqa: SIM115
    c = EC.EvidenceChild.start(real, q, version=1)
    try:
        for fd in (a.fileno(), w, fh.fileno()):
            assert os.readlink(f"/proc/{c.pid}/fd/{fd}") == "/dev/null", fd
        targets = [os.readlink(f"/proc/{c.pid}/fd/{n}") for n in os.listdir(f"/proc/{c.pid}/fd") if int(n) > 2]
        assert sum(t.startswith("socket:") for t in targets) == 1, targets     # yalnız kendi borusu
        assert not any(t.startswith("pipe:") for t in targets), targets
        a.close()
        b.settimeout(5)
        assert b.recv(1) == b"", "ebeveynin kapattığı soket alt süreçte açık kalmamalı"
        os.close(w)
        w = None
        assert select.select([r], [], [], 5)[0] == [r] and os.read(r, 1) == b"", "boru yazma ucu alt süreçte kalmamalı"
        for s in SYMS:
            assert _ser(c.compute(s)) == _ser(q(real, s))
    finally:
        pid = c.pid
        c.close()
        for x in (b, fh):
            x.close()
        os.close(r)
        if w is not None:
            os.close(w)
        if a.fileno() >= 0:
            a.close()
    assert not _pid_alive(pid)


def test_close_is_bounded_although_the_child_dropped_the_multiprocessing_sentinel():
    """Alt süreç `multiprocessing`'in bekçi borusunu da /dev/null'a çevirir → bekçi hemen EOF verir ve `join(timeout)`
    süresiz `waitpid`'e düşerdi. `close` `waitpid` yoklamasıyla bekler: ("stop",)'u okumayan (hesapta) alt süreç
    `timeout` sonra öldürülür, `close` birkaç saniyede döner."""
    from multiprocessing.connection import wait

    class E:
        pass

    c = EC.EvidenceChild.start(E(), lambda eng, s: time.sleep(60) or {}, version=1)
    pid = c.pid
    assert wait([c._proc.sentinel], 2.0), "bekçi erken EOF verir (bu yüzden join(timeout) kullanılmaz)"
    assert c.alive
    c._conn.send(("q", "S"))                                      # alt süreç 60 sn hesapta: ("stop",) okunmaz
    time.sleep(0.2)
    t0 = time.monotonic()
    c.close(timeout=0.5)
    assert time.monotonic() - t0 < 5
    assert not _pid_alive(pid) and not EC.parent_gc_frozen()


def test_the_fork_marker_is_machine_state_not_backed_up_and_stays_on_the_machine_across_a_restore(tmp_path):
    """İşaret dosyası (deadman olayı → alt süreç bu makinede kapalı) veri değil makine durumudur: yedeğe girmez (eski
    bir yedekten dönüp alt süreci sessizce kapalı tutmaz); geri yüklemede mevcut state'teki kopya korunur."""
    import tarfile

    from tradingbot.ops import backup as BK
    assert BK.MACHINE_MARKERS == (EC.MARKER_NAME,)
    st = tmp_path / "state"
    st.mkdir()
    (st / "futures_ledger.json").write_text("{}", encoding="utf-8")
    (st / EC.MARKER_NAME).write_text("1:1 1700000000\n", encoding="ascii")
    res = BK.run_backup(st, tmp_path / "backups", "manual")
    with tarfile.open(res.archive, "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("futures_ledger.json") for n in names)
    assert not any(EC.MARKER_NAME in n for n in names), names
    BK.restore_backup(res.archive, st)
    assert (st / EC.MARKER_NAME).read_text(encoding="ascii") == "1:1 1700000000\n", "makinedeki işaret kalır"
    (st / EC.MARKER_NAME).unlink()
    time.sleep(1.05)                                              # `state.pre-restore-<ts>` saniye çözünürlüklü
    BK.restore_backup(res.archive, st)
    assert not (st / EC.MARKER_NAME).exists(), "yedekten işaret gelmez"


# ------------------------------------------------------------------ 14) bellek (düzeltme turu 3)
class _Big:
    """Eski indeks vekili: büyük, yerleşik (sayfaları dokunulmuş) bir dizi. Zayıf referans destekler."""

    def __init__(self, mb: int):
        import numpy as np
        self.arr = np.ones(mb * 131_072)                            # mb × 1 MiB float64, hepsi yazılmış


def _sleepy(seconds: float):
    def fn(eng, sym):
        time.sleep(seconds)
        return {"LONG": {"sym": sym, "n": 1}, "SHORT": {"sym": sym, "n": 2}}
    return fn


def _wait(pred, timeout=30.0, step=0.01) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def test_a_newer_publish_kills_the_old_child_at_once_not_at_the_symbol_boundary(tmp_path, monkeypatch, hist, caplog):
    """Eski sürümün alt süreci yeni yayım ANINDA öldürülür (sembol sınırı beklenmez): yazınca-kopyala sayfaları ve
    ebeveynin bıraktığı eski indeks sayfaları hemen iade edilir. Arıza sayılmaz; eski iş süreç içine DÜŞMEZ."""
    v1_real, v2_real = _build_index(hist, drop_last=1), _build_index(hist)
    v1 = _Probe(v1_real, tmp_path, gate="ETH/USDT")                # v1'in alt süreci ETH'de kapıda bekler (açılmaz)
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [v1, v2_real])
    with caplog.at_level(logging.INFO, logger="tradingbot.patterns.evidence_cache"):
        r.refresh_once()
        assert v1.wait_entered()
        cache = eng._evidence_cache()
        pid_v1 = cache._child.pid
        t0 = time.monotonic()
        r.refresh_once()                                          # sürüm 2 yayımı
        assert _wait(lambda: not _pid_alive(pid_v1), timeout=5.0), "eski alt süreç yayım anında ölmeli"
        killed_in = time.monotonic() - t0
        assert cache.wait_idle(120)
    assert killed_in < 3.0, killed_in
    assert cache.stats["child_superseded_kills"] == 1 and cache.stats["child_failures"] == 0, cache.stats
    assert not v1.parent_calls(), "eski iş süreç içi yola düşmemeli"
    assert not any(c[1] in ("SOL/USDT", "AVAX/USDT") for c in v1.calls())
    assert not any("arızalandı" in rec.getMessage() for rec in caplog.records)
    info = [rec.getMessage() for rec in caplog.records if "daha yeni yayımla hemen kapatıldı" in rec.getMessage()]
    assert info and not any(m.startswith("pattern kanıtı alt süre") for m in info), "bilgi satırı sorun önekini taşımaz"
    assert {k[1] for k in cache.keys()} == {2} and cache._child is None
    now = _now_for(v2_real)
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(v2_real, s))


def test_a_tour_in_flight_in_the_stale_child_recomputes_in_process_with_the_same_result(tmp_path, hist, caplog):
    """Tur eski motorun alt sürecinde beklerken yeni yayım alt süreci öldürür: tur o sembolü süreç içi (bugünkü yol)
    hesaplar, AYNI kanıtı alır; arıza uyarısı yok."""
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, gate="ETH/USDT")
    cache = EvidenceCache()
    with cache.compute_lock:
        assert cache._start_child(_bundle(idx), TradingEngineV3._evidence_query)
        pid = cache._child.pid
    key = ("ETH/USDT", 1, 0)
    out = {}

    def tour():
        out["ev"] = cache.get_or_compute(key, lambda: cache.compute(idx, "ETH/USDT", TradingEngineV3._evidence_query)).ev
    t = threading.Thread(target=tour, name="tour")
    with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
        t.start()
        assert idx.wait_entered(), "tur isteği alt süreçte kapıda"
        cache.note_published(object.__new__(type("E", (), {})), 2)   # başka motorun yayımı
        t.join(30)
    assert not t.is_alive(), "tur kapı açılmadan bitmeli (alt süreç öldürüldü, süreç içi hesap)"
    assert _ser(out["ev"]) == _ser(TradingEngineV3._evidence_query(real, "ETH/USDT"))
    assert {c[1] for c in idx.parent_calls()} == {"ETH/USDT"}
    assert not _pid_alive(pid) and cache._child is None
    assert cache.stats["child_failures"] == 0 and cache.stats["child_superseded_kills"] == 1
    assert not any("arızalandı" in rec.getMessage() for rec in caplog.records)
    assert not EC.parent_gc_frozen()


def test_an_old_index_dropped_after_the_fork_kills_and_reforks_the_child_same_evidence(tmp_path, hist, caplog):
    """Fork anında canlı olan eski motor (turun uçuştaki çağrısı) ebeveynde bırakılınca alt süreç HEMEN öldürülür ve
    aynı motor için yeniden kurulur: eski indeksin sayfaları alt süreçte kalmaz. Uçuştaki sembol yeni alt süreçte
    yeniden istenir; kanıt aynı, arıza yok."""
    real = _build_index(hist)
    old = _build_index(hist, drop_last=2)
    idx = _Probe(real, tmp_path, gate="SOL/USDT")
    cache = EvidenceCache()
    cache.track_engine(old)
    keys = [(s, 1, 0) for s in SYMS]
    with caplog.at_level(logging.INFO, logger="tradingbot.patterns.evidence_cache"):
        cache.request_prewarm(_bundle(idx), keys, TradingEngineV3._evidence_query, lambda b: True, use_child=True)
        assert idx.wait_entered(), "ilk alt süreç SOL'de kapıda"
        pid_a = cache._child.pid
        idx.entered.unlink()
        del old                                                   # tur çağrısı bitti: eski motor ebeveynde serbest
        gc.collect()
        assert _wait(lambda: not _pid_alive(pid_a), timeout=5.0), "eski indeksi devralan alt süreç hemen ölmeli"
        assert idx.wait_entered(), "yeni alt süreç SOL'ü yeniden istedi"
        pid_b = cache._child.pid
        idx.open_gate()
        assert cache.wait_idle(120)
    assert pid_b != pid_a
    assert cache.stats["child_reforks"] == 1 and cache.stats["child_failures"] == 0, cache.stats
    assert cache.stats["child_in_process_fallbacks"] == 0 and not idx.parent_calls()
    info = [rec.getMessage() for rec in caplog.records if "yeniden kuruldu" in rec.getMessage()]
    assert len(info) == 1 and not info[0].startswith("pattern kanıtı alt süre"), info
    assert not any("arızalandı" in rec.getMessage() for rec in caplog.records)
    line = [rec.getMessage() for rec in caplog.records if "ön ısıtıldı" in rec.getMessage()]
    assert line and line[0].endswith("; eski indeks için yeniden fork 1)"), line
    for s in SYMS:
        assert _ser(cache.get((s, 1, 0)).ev) == _ser(TradingEngineV3._evidence_query(real, s))
    assert cache._child is None and not EC.parent_gc_frozen()


@pytest.mark.parametrize("track", [False, True], ids=["eski_davranis_kanca_yok", "duzeltme"])
def test_the_child_does_not_keep_a_dropped_old_index_resident(track):
    """ÖLÇÜM (denetim bulgusu, düzeltme turu 3): fork anında ebeveynde canlı olan eski indeks sonra bırakılırsa
    sayfaları alt sürecin ÖZEL belleğine dönüşür (kanca yokken: ≈ indeks boyutu). Kanca alt süreci öldürüp yeniden
    kurar; yeni alt süreç eski indeksi içermez."""
    big_mb = 160
    old = _Big(big_mb)
    new = _Big(1)
    cache = EvidenceCache()
    if track:
        cache.track_engine(old)
    keys = [(f"S{i}", 1, 0) for i in range(200)]
    try:
        cache.request_prewarm(_bundle(new), keys, _sleepy(0.2), lambda b: True, use_child=True)
        assert _wait(lambda: cache._child is not None and cache._child.pid, timeout=30)
        pid_a = cache._child.pid
        time.sleep(0.5)
        before = EC._private_kb(pid_a)
        del old
        gc.collect()
        if track:
            assert _wait(lambda: cache._child is not None and cache._child.pid not in (None, pid_a), timeout=10)
        time.sleep(1.0)
        child = cache._child
        after = EC._private_kb(child.pid)
        assert before is not None and after is not None
        if track:
            assert child.pid != pid_a and not _pid_alive(pid_a)
            assert after < 40 * 1024, ("yeni alt süreç eski indeksi tutmamalı", before, after)
        else:
            assert child.pid == pid_a
            assert after - before > 0.8 * big_mb * 1024, ("kanca yokken eski indeks alt süreçte kalır", before, after)
    finally:
        cache.stop()
        cache._close_child()


def _headroom_seq(values):
    """`memory_headroom_mb` vekili: sırayla değer verir, sonuncuyu tekrarlar."""
    vals = list(values)

    def fn():
        return vals.pop(0) if len(vals) > 1 else vals[0]
    return fn


def test_the_memory_guard_kills_the_child_and_the_job_finishes_in_process_same_evidence(tmp_path, monkeypatch,
                                                                                         hist, caplog):
    """Alt süreç yaşarken bellek payı öldürme eşiğinin altına inerse alt süreç öldürülür (uyarı); işin kalanı süreç
    içi (bugünkü yol) — kanıt aynı. Alt süreç cgroup'u MemoryMax'a ASLA itmesin."""
    monkeypatch.setattr(EC.EvidenceChild, "MEMORY_CHECK_S", 0.01)
    monkeypatch.setattr(EC, "memory_headroom_mb", _headroom_seq([(5000.0, "test"), (100.0, "cgroup /test")]))
    real = _build_index(hist)
    idx = _Probe(real, tmp_path, gate="ETH/USDT")                 # ilk istek kapıda: pay denetimi kesin koşar
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [idx])
    with caplog.at_level(logging.INFO, logger="tradingbot.patterns.evidence_cache"):
        r.refresh_once()
        cache = eng._evidence_cache()
        assert cache.wait_idle(120)
    assert cache.stats["child_memory_kills"] == 1 and cache.stats["child_failures"] == 0, cache.stats
    warn = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert any("bellek koruması nedeniyle kapatıldı" in m and "cgroup /test payı 100 MB < 512 MB" in m for m in warn), warn
    assert {c[1] for c in idx.parent_calls()} == set(SYMS), "işin kalanı süreç içi"
    line = [rec.getMessage() for rec in caplog.records if "ön ısıtıldı" in rec.getMessage()]
    assert line and "alt süreçte 0, süreç içi 3 sembol; bellek koruması)" in line[0], line
    now = _now_for(real)
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(real, s))


def test_no_fork_when_the_memory_headroom_is_short_same_evidence(tmp_path, monkeypatch, hist, caplog):
    def boom(*a, **k):
        raise AssertionError("pay yetersizken fork yapılmamalı")
    monkeypatch.setattr(EC, "memory_headroom_mb", lambda: (1000.0, "cgroup /test"))
    monkeypatch.setattr(EC.EvidenceChild, "_pin", boom)
    real = _build_index(hist)
    idx = _Probe(real, tmp_path)
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, [idx])
    with caplog.at_level(logging.INFO, logger="tradingbot.patterns.evidence_cache"):
        r.refresh_once()
        cache = eng._evidence_cache()
        assert cache.wait_idle(120)
    assert cache.stats["child_started"] == 0 and cache.stats["child_memory_refusals"] == 1
    assert cache.stats["child_failures"] == 0
    assert any("alt süreci başlatılmadı — bellek payı yetersiz (cgroup /test: 1000 MB < 1536 MB)" in rec.getMessage()
               for rec in caplog.records)
    assert not idx.child_calls() and len(idx.parent_calls()) == 2 * len(SYMS)
    line = [rec.getMessage() for rec in caplog.records if "ön ısıtıldı" in rec.getMessage()]
    assert line and line[0].endswith("(alt süreç kurulamadı; süreç içi 3 sembol; bellek payı yetersiz)"), line
    now = _now_for(real)
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(real, s))


def test_the_headroom_reader_handles_cgroup_v2_and_v1_and_excludes_page_cache(tmp_path):
    def w(path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="ascii")
    v2 = tmp_path / "v2"
    svc = v2 / "system.slice" / "tradingbot-worker.service"
    w(svc / "memory.max", "6442450944\n")
    w(svc / "memory.current", "5000000000\n")
    w(svc / "memory.stat", "anon 3400000000\nfile 1600000000\nactive_file 1000000000\ninactive_file 500000000\n")
    w(v2 / "system.slice" / "memory.max", "max\n")
    w(v2 / "system.slice" / "memory.current", "9000000000\n")
    got = EC._cgroup_headrooms("0::/system.slice/tradingbot-worker.service\n", str(v2))
    assert got == [(6442450944 - (5000000000 - 1500000000), "cgroup /system.slice/tradingbot-worker.service")]
    v1 = tmp_path / "v1"
    w(v1 / "memory" / "a" / "b" / "memory.limit_in_bytes", "2147483648\n")
    w(v1 / "memory" / "a" / "b" / "memory.usage_in_bytes", "1500000000\n")
    w(v1 / "memory" / "a" / "b" / "memory.stat", "cache 600000000\ntotal_active_file 200000000\n"
                                                 "total_inactive_file 100000000\n")
    w(v1 / "memory" / "a" / "memory.limit_in_bytes", "9223372036854771712\n")      # sınırsız → atlanır
    w(v1 / "memory" / "a" / "memory.usage_in_bytes", "1600000000\n")
    got = EC._cgroup_headrooms("4:memory:/a/b\n3:cpuset:/\n", str(v1))
    assert got == [(2147483648 - (1500000000 - 300000000), "cgroup /a/b")]
    assert EC._cgroup_headrooms("0::/yok\n", str(tmp_path / "bos")) == []
    assert EC._cgroup_headrooms(None) == []
    head = EC.memory_headroom_mb()                                # bu Linux makinesinde en az MemAvailable okunur
    assert head is not None and head[0] > 0 and isinstance(head[1], str)


def test_a_tour_holding_the_old_index_while_the_new_child_forks_triggers_one_refork(tmp_path, monkeypatch, hist):
    """Motor düzeyinde gerçek akış: tur sürüm 1 ile kanıt çağrısının içindeyken (panel yazımı) sürüm 2 yayımlanır ve
    ön ısıtma alt süreci fork edilir — sürüm 1 o an ebeveynde canlıdır. Tur çağrısı bitip sürüm 1 serbest kalınca alt
    süreç öldürülür ve sürüm 2 için yeniden kurulur. Kanıt aynı; arıza yok; eski motor ebeveynde tutulmaz."""
    from tradingbot import engine_v3 as E3
    gate, entered = tmp_path / "tour-gate", tmp_path / "tour-entered"
    real_write = E3.atomic_write_json

    def slow_write(path, *a, **k):
        if threading.current_thread().name == "tour":
            entered.touch()
            _wait(gate.exists, timeout=60)
        return real_write(path, *a, **k)
    monkeypatch.setattr(E3, "atomic_write_json", slow_write)
    v2_real = _build_index(hist)
    v2 = _Probe(v2_real, tmp_path, gate="AVAX/USDT")
    builds = [_build_index(hist, drop_last=1), v2]
    eng = _engine(tmp_path, monkeypatch)
    r = _refresher(eng, builds)
    monkeypatch.setattr(eng, "EVIDENCE_PREWARM", False)
    r.refresh_once()                                              # sürüm 1 (ön ısıtmasız): tur kendisi hesaplar
    ref_v1 = weakref.ref(r.bundle.engine)
    monkeypatch.setattr(eng, "EVIDENCE_PREWARM", True)
    now = _now_for(v2_real)
    t = threading.Thread(target=lambda: eng._pattern_evidence("ETH/USDT", now), name="tour")
    t.start()
    assert _wait(entered.exists), "tur sürüm 1 kanıtını hesapladı, panel yazımında (motoru tutuyor)"
    r.refresh_once()                                              # sürüm 2 yayımı → ön ısıtma → fork (sürüm 1 canlı)
    cache = eng._evidence_cache()
    assert v2.wait_entered(), "sürüm 2 alt süreci AVAX'ta kapıda"
    assert ref_v1() is not None and cache.stats["child_started"] == 1
    v2.entered.unlink()
    gate.touch()                                                  # tur çağrısı biter → sürüm 1 serbest
    t.join(60)
    assert not t.is_alive()
    assert _wait(lambda: cache.stats["child_reforks"] == 1, timeout=10), cache.stats
    assert v2.wait_entered(), "yeni alt süreç AVAX'ı yeniden istedi"
    v2.open_gate()
    assert cache.wait_idle(120)
    gc.collect()
    assert ref_v1() is None, "eski motor ebeveynde tutulmamalı"
    assert cache.stats["child_started"] == 2 and cache.stats["child_failures"] == 0, cache.stats
    assert not v2.parent_calls(), "sürüm 2 hep alt süreçte"
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(v2_real, s))
    assert cache._child is None and not EC.parent_gc_frozen()


def test_a_retention_kill_during_the_fork_handshake_reforks_once_and_is_not_a_failure(tmp_path, hist, monkeypatch,
                                                                                       caplog):
    """Eski motor tam el sıkışması sırasında serbest kalırsa (alt süreç öldürülür) iş süreç içine düşmez: bir kez daha
    fork edilir (eski motor artık yok). Kanıt aynı, arıza yok."""
    real = _build_index(hist)
    idx = _Probe(real, tmp_path)
    cache = EvidenceCache()
    orig = EC.EvidenceChild._recv
    hits = []

    def recv(self, timeout, *, what, stall=None):
        if what == "hazır el sıkışması" and not hits:
            hits.append(1)
            self.retire(EC.RETIRE_RETAINED)
        return orig(self, timeout, what=what, stall=stall)
    monkeypatch.setattr(EC.EvidenceChild, "_recv", recv)
    with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
        with cache.compute_lock:
            assert cache._start_child(_bundle(idx), TradingEngineV3._evidence_query)
            ev = cache.compute(idx, "ETH/USDT", TradingEngineV3._evidence_query)
    cache._close_child()
    assert hits == [1]
    assert cache.stats["child_reforks"] == 1 and cache.stats["child_failures"] == 0, cache.stats
    assert cache.stats["child_in_process_fallbacks"] == 0 and not idx.parent_calls()
    assert _ser(ev) == _ser(TradingEngineV3._evidence_query(real, "ETH/USDT"))
    assert not [rec for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert not EC.parent_gc_frozen()
