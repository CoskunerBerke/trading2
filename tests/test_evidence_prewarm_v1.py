"""PATTERN KANITI ÖN ISITMASI (2026-10-01) — hız değişir, KANIT DEĞİŞMEZ.

Yenileyici yeni indeksi BUGÜNKÜ ANDA yayımlar; yayımdan sonra tek bir arka plan işçisi, yayımlanmış motorla ve turun
kullandığı fonksiyonla kanıtı hesaplayıp o sürümün anahtarıyla önbelleğe koyar. Bu testler kilitler:

1. Ön ısıtılmış kanıt, turun kendi hesapladığıyla BİT-AYNI (serileştirilmiş `==`), panel dosyası da aynı.
2. Ön ısıtma o an tam turun istediği sembolü hesaplıyorsa tur bekler ve AYNI sonucu alır; ikinci kez sorgulamaz.
3. Ön ısıtma sürerken yeni yayım olursa eski sürüm bırakılır; eski sürümün kanıtı yeni sürümü okuyan tura VERİLMEZ.
4. Ön ısıtma arızası yalnız loglanır; tur bugünkü gibi kendisi hesaplar. Kapalıyken davranış bugünküyle aynı.
5. Motor ve sürüm TEK paket okumasından gelir; ön ısıtma eski indekse referans tutmaz (ek indeks kopyası yok).
"""
from __future__ import annotations

import gc
import json
import logging
import sys
import threading
import time
import tracemalloc
import weakref
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402
from test_patterns import _candles  # noqa: E402

from tradingbot.engine_v3 import TradingEngineV3  # noqa: E402
from tradingbot.patterns import SimilarPatternEngine  # noqa: E402
from tradingbot.patterns.refresher import IndexRefresher  # noqa: E402

BAR_4H = 14_400_000
PREWARM_THREAD = "pattern-evidence-prewarm"


def _ser(ev) -> str:
    return json.dumps(ev, sort_keys=True, default=repr)


def _history(symbols, n=420):
    """Sembol başına 4h mumları; son bar şimdiden bir bar önce kapanmış (bayatlık kapısı açık)."""
    now_ms = int(time.time() * 1000)
    start = now_ms - now_ms % BAR_4H - n * BAR_4H
    return {s: _candles(n, seed=11 + i, drift=0.0004 * (i - 1), tf_ms=BAR_4H, start=start) for i, s in enumerate(symbols)}


def _build_index(hist, *, drop_last: int = 0):
    eng = SimilarPatternEngine(min_sample=30, horizon=24)
    for s, df in hist.items():
        d = df.iloc[: len(df) - drop_last].reset_index(drop=True) if drop_last else df
        eng.add_series(s, "futures", "4h", d)
    return eng


class _Gated:
    """Gerçek motoru saran vekil: sorguları sayar; istenirse ön ısıtma iş parçacığını bir sembolde bekletir."""

    def __init__(self, real, *, gate_symbol=None, fail_in_prewarm=False):
        self._real = real
        self.candles, self.events = real.candles, real.events
        self.gate_symbol = gate_symbol
        self.fail_in_prewarm = fail_in_prewarm
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.calls: Counter = Counter()

    def query(self, symbol, market, tf, side, k=60):
        th = threading.current_thread().name
        self.calls[(symbol, side, th == PREWARM_THREAD)] += 1
        if th == PREWARM_THREAD:
            if self.fail_in_prewarm:
                raise RuntimeError("sentetik ön ısıtma arızası")
            if symbol == self.gate_symbol:
                self.entered.set()
                assert self.gate.wait(20), "kapı açılmadı"
        return self._real.query(symbol, market, tf, side, k=k)


def _engine(tmp_path, monkeypatch, symbols):
    eng = TE._engine(tmp_path, monkeypatch, symbols=list(symbols))
    return eng


def _refresher(eng, builds):
    """`builds`: her `refresh_once` çağrısında sıradaki motoru döndüren liste (yayım sırası)."""
    r = IndexRefresher(build_fn=lambda syms: (builds.pop(0), {}), symbols_fn=lambda: list(eng.cfg.coins),
                       update_fn=lambda syms, now_ms: {"advanced": True},       # her çağrı: arşiv ilerledi → yeniden kur
                       interval_s=10 ** 6, on_publish=eng._on_pattern_index_published)
    eng._refresher = r                         # iş parçacığı BAŞLATILMAZ; yayımı test sürer
    return r


def _now_for(eng_index) -> int:
    return max(int(df["timestamp"].iloc[-1]) for df in eng_index.candles.values()) + BAR_4H


SYMS = ("ETH/USDT", "SOL/USDT", "AVAX/USDT")   # ön ısıtma sırası = evren sırası


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


# ------------------------------------------------------------------ 1) bit-aynı
def test_prewarmed_evidence_is_bit_identical_to_the_tour_computed_one(tmp_path, monkeypatch, hist):
    idx = _Gated(_build_index(hist))
    on = _engine(tmp_path / "on", monkeypatch, SYMS)
    r = _refresher(on, [idx])
    assert r.refresh_once().get("published")
    assert on._evidence_cache().wait_idle(60)
    pre = {k[0]: e for k, e in ((k, on._evidence_cache().get(k)) for k in on._evidence_cache().keys())}
    assert set(pre) == set(SYMS) and all(e.source == "prewarm" and not e.written for e in pre.values())
    n_pre = sum(idx.calls.values())
    assert n_pre == 2 * len(SYMS) and all(flag for (_s, _side, flag) in idx.calls)

    # KAPALI: aynı paket, ön ısıtma yok → tur kendisi hesaplar
    off = _engine(tmp_path / "off", monkeypatch, SYMS)
    monkeypatch.setattr(off, "EVIDENCE_PREWARM", False)
    off._refresher = type("R", (), {"bundle": r.bundle})()
    now = _now_for(idx)
    for s in SYMS:
        a = on._pattern_evidence(s, now)
        b = off._pattern_evidence(s, now)
        assert a is pre[s].ev, "tur ön ısıtılmış girdiyi kullanmalı"
        assert _ser(a) == _ser(b), s
        fa = json.loads((on.cfg.state_path / "evidence" / f"{s.replace('/', '_')}.json").read_text(encoding="utf-8"))
        fb = json.loads((off.cfg.state_path / "evidence" / f"{s.replace('/', '_')}.json").read_text(encoding="utf-8"))
        assert fa["neighbors"] == fb["neighbors"] and fa["explanation_tr"] == fb["explanation_tr"]
        for side in ("LONG", "SHORT"):
            pa, pb = dict(fa["packets"][side]), dict(fb["packets"][side])
            for k in ("timestamp", "decision_id"):        # turun kimliği/saati — kanıt değil
                pa.pop(k, None), pb.pop(k, None)
            assert pa == pb
    assert sum(idx.calls.values()) == n_pre + 2 * len(SYMS), "AÇIK motor ikinci kez sorgulamamalı (yalnız KAPALI sorguladı)"
    assert all(e.written for e in (on._evidence_cache().get(k) for k in on._evidence_cache().keys()))


def test_evidence_file_is_written_once_per_version_on_first_tour_use(tmp_path, monkeypatch, hist):
    idx = _build_index(hist)
    eng = _engine(tmp_path, monkeypatch, SYMS)
    r = _refresher(eng, [idx])
    r.refresh_once()
    assert eng._evidence_cache().wait_idle(60)
    f = eng.cfg.state_path / "evidence" / "ETH_USDT.json"
    assert not f.exists(), "ön ısıtma panel dosyası YAZMAZ (turun kimliğiyle tur yazar)"
    now = _now_for(idx)
    eng._pattern_evidence("ETH/USDT", now)
    assert json.loads(f.read_text(encoding="utf-8"))["run_id"] == eng.run_id
    stamp = f.stat().st_mtime_ns
    eng._pattern_evidence("ETH/USDT", now)
    assert f.stat().st_mtime_ns == stamp


# ------------------------------------------------------------------ 2) uçuştaki ön ısıtma
def test_tour_waits_for_the_in_flight_prewarm_and_gets_the_same_result(tmp_path, monkeypatch, hist):
    real = _build_index(hist)
    idx = _Gated(real, gate_symbol="ETH/USDT")
    eng = _engine(tmp_path, monkeypatch, SYMS)
    r = _refresher(eng, [idx])
    r.refresh_once()
    assert idx.entered.wait(20), "ön ısıtma ETH'de beklemeli"
    now = _now_for(idx)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ev", eng._pattern_evidence("ETH/USDT", now)), name="tour")
    t.start()
    time.sleep(0.3)
    assert t.is_alive(), "tur, ön ısıtmanın hesapladığı sembolü BEKLEMELİ (ikinci hesap yok)"
    idx.gate.set()
    t.join(30)
    assert not t.is_alive()
    assert _ser(out["ev"]) == _ser(TradingEngineV3._evidence_query(real, "ETH/USDT"))
    assert idx.calls[("ETH/USDT", "LONG", False)] == 0 and idx.calls[("ETH/USDT", "SHORT", False)] == 0
    assert idx.calls[("ETH/USDT", "LONG", True)] == 1 and idx.calls[("ETH/USDT", "SHORT", True)] == 1


def test_a_tour_that_asks_first_computes_and_the_prewarm_reuses_it(tmp_path, monkeypatch, hist):
    """Tur bir sembolü ön ısıtmadan ÖNCE hesaplarsa ön ısıtma onu yeniden hesaplamaz (tek hesap, tek sonuç)."""
    real = _build_index(hist)
    idx = _Gated(real, gate_symbol="ETH/USDT")
    eng = _engine(tmp_path, monkeypatch, SYMS)
    r = _refresher(eng, [idx])
    r.refresh_once()
    assert idx.entered.wait(20)
    now = _now_for(idx)
    # ETH ön ısıtmada bekliyor ve hesaplama kilidini tutuyor; tur AVAX'ı (sırada son) ister → kilidi bekler
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ev", eng._pattern_evidence("AVAX/USDT", now)), name="tour")
    t.start()
    time.sleep(0.2)
    idx.gate.set()
    t.join(30)
    assert eng._evidence_cache().wait_idle(60)
    assert _ser(out["ev"]) == _ser(TradingEngineV3._evidence_query(real, "AVAX/USDT"))
    for side in ("LONG", "SHORT"):
        assert idx.calls[("AVAX/USDT", side, False)] + idx.calls[("AVAX/USDT", side, True)] == 1, "AVAX tek kez sorgulanmalı"


# ------------------------------------------------------------------ 3) sürüm karışmaz
def test_a_newer_publish_drops_the_old_prewarm_and_never_serves_the_old_version(tmp_path, monkeypatch, hist):
    v1_real = _build_index(hist, drop_last=1)
    v2_real = _build_index(hist)
    v1 = _Gated(v1_real, gate_symbol="ETH/USDT")
    v2 = _Gated(v2_real)
    eng = _engine(tmp_path, monkeypatch, SYMS)
    r = _refresher(eng, [v1, v2])
    r.refresh_once()                                  # sürüm 1 → ön ısıtma ETH'de bekler
    assert v1.entered.wait(20)
    r.refresh_once()                                  # sürüm 2 yayımlandı (ön ısıtma 1 hâlâ ETH'de)
    assert r.bundle.version == 2
    now = _now_for(v2)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ev", eng._pattern_evidence("ETH/USDT", now)), name="tour")
    t.start()
    time.sleep(0.2)
    v1.gate.set()
    t.join(30)
    assert eng._evidence_cache().wait_idle(60)
    want_v2 = _ser(TradingEngineV3._evidence_query(v2_real, "ETH/USDT"))
    want_v1 = _ser(TradingEngineV3._evidence_query(v1_real, "ETH/USDT"))
    assert want_v1 != want_v2, "test anlamlı olmalı: iki sürümün kanıtı farklı"
    assert _ser(out["ev"]) == want_v2, "sürüm 2'yi okuyan tur sürüm 1 kanıtı ALAMAZ"
    keys = eng._evidence_cache().keys()
    assert keys and {k[1] for k in keys} == {2}, keys
    # sürüm 1 ön ısıtması ETH'den sonra bırakıldı: SOL/AVAX sürüm 1 için HİÇ sorgulanmadı
    assert not any(c[0] in ("SOL/USDT", "AVAX/USDT") for c in v1.calls)
    for s in SYMS:                                    # sürüm 2'nin bütün kanıtı doğru motordan
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(v2_real, s))


def test_engine_and_version_come_from_a_single_bundle_read(tmp_path, monkeypatch, hist):
    """Eskiden motor ve sürüm iki ayrı okumayla alınıyordu; arada yayım olursa eski motorun kanıtı yeni sürümle önbelleğe girerdi."""
    a, b = _build_index(hist, drop_last=1), _build_index(hist)
    bundles = [type("B", (), {"engine": a, "version": 1})(), type("B", (), {"engine": b, "version": 2})()]
    reads = {"n": 0}

    class _Flip:
        @property
        def bundle(self):                              # her okumada bir SONRAKİ yayım (en kötü zamanlama)
            reads["n"] += 1
            return bundles[min(reads["n"] - 1, 1)]
    eng = _engine(tmp_path, monkeypatch, SYMS)
    monkeypatch.setattr(eng, "EVIDENCE_PREWARM", False)
    eng._refresher = _Flip()
    ev = eng._pattern_evidence("ETH/USDT", _now_for(b))
    assert reads["n"] == 1, "motor ve sürüm TEK okumayla alınmalı"
    [key] = eng._evidence_cache().keys()
    assert key[1] == 1 and _ser(ev) == _ser(TradingEngineV3._evidence_query(a, "ETH/USDT"))


# ------------------------------------------------------------------ 4) arıza ve kapalı yol
def test_a_prewarm_failure_only_logs_and_the_tour_computes_itself(tmp_path, monkeypatch, hist, caplog):
    real = _build_index(hist)
    idx = _Gated(real, fail_in_prewarm=True)
    eng = _engine(tmp_path, monkeypatch, SYMS)
    r = _refresher(eng, [idx])
    with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.evidence_cache"):
        r.refresh_once()
        assert eng._evidence_cache().wait_idle(60)
    assert any("ön ısıtılamadı" in rec.getMessage() for rec in caplog.records)
    assert len(eng._evidence_cache()) == 0
    now = _now_for(idx)
    for s in SYMS:
        assert _ser(eng._pattern_evidence(s, now)) == _ser(TradingEngineV3._evidence_query(real, s))


def test_a_broken_hook_never_breaks_the_publish(tmp_path, monkeypatch, hist, caplog):
    idx = _build_index(hist)

    def boom(bundle):
        raise RuntimeError("kanca arızası")
    r = IndexRefresher(build_fn=lambda s: (idx, {}), symbols_fn=lambda: list(SYMS), interval_s=10 ** 6, on_publish=boom)
    with caplog.at_level(logging.WARNING, logger="tradingbot.patterns.refresher"):
        out = r.refresh_once()
    assert out.get("published") and r.bundle.engine is idx and r.bundle.version == 1
    assert any("yayım sonrası kanca başarısız" in rec.getMessage() for rec in caplog.records)


def test_prewarm_off_is_todays_behaviour(tmp_path, monkeypatch, hist):
    idx = _Gated(_build_index(hist))
    eng = _engine(tmp_path, monkeypatch, SYMS)
    monkeypatch.setattr(eng, "EVIDENCE_PREWARM", False)
    r = _refresher(eng, [idx])
    r.refresh_once()
    time.sleep(0.2)
    assert len(eng._evidence_cache()) == 0 and sum(idx.calls.values()) == 0
    now = _now_for(idx)
    for s in SYMS:
        eng._pattern_evidence(s, now)
    assert sum(idx.calls.values()) == 2 * len(SYMS) and not any(flag for (_s, _side, flag) in idx.calls)


def test_stale_series_are_not_prewarmed(tmp_path, monkeypatch, hist):
    """Bayat seri (son bar > 3 bar) için tur zaten kanıt vermez; ön ısıtma da harcamaz."""
    old = {s: df.assign(timestamp=df["timestamp"] - 10 * BAR_4H) for s, df in hist.items()}
    idx = _Gated(_build_index(old))
    eng = _engine(tmp_path, monkeypatch, SYMS)
    r = _refresher(eng, [idx])
    r.refresh_once()
    time.sleep(0.2)
    assert eng._evidence_cache().wait_idle(10)
    assert sum(idx.calls.values()) == 0 and len(eng._evidence_cache()) == 0


def test_production_wires_the_hook_into_the_refresher(tmp_path, monkeypatch):
    eng = TE._engine(tmp_path, monkeypatch, {"history": {"auto_refresh": True}})
    r = eng._make_refresher()
    assert r is not None and r._on_publish == eng._on_pattern_index_published
    assert TradingEngineV3.EVIDENCE_PREWARM is True


# ------------------------------------------------------------------ 5) bellek
def _kept(before, after) -> int:
    """İki tracemalloc anlık görüntüsü arasında KALAN bellek; pandas'ın kendi iç referans izleme listeleri hariç
    (sütun erişiminde büyüyen, pandas'ın tembelce budadığı zayıf referanslar — her iki yolda aynı mekanizma, gürültü)."""
    flt = [tracemalloc.Filter(False, "*/pandas/*")]
    return sum(st.size_diff for st in after.filter_traces(flt).compare_to(before.filter_traces(flt), "filename"))


def test_prewarm_keeps_no_extra_index_copy(tmp_path, monkeypatch, hist):
    """Ön ısıtmanın bellek izi = turun aynı kanıtı kendisi hesaplamasınınki (sorgu geçicileri + kanıt sözlükleri);
    indeksin kopyası YOK. Yeni yayımdan sonra eski motor serbest kalır (ön ısıtma/önbellek referans tutmaz)."""
    on = _engine(tmp_path / "on", monkeypatch, SYMS)
    off = _engine(tmp_path / "off", monkeypatch, SYMS)
    monkeypatch.setattr(off, "EVIDENCE_PREWARM", False)
    tracemalloc.start()
    try:
        base0 = tracemalloc.get_traced_memory()[0]
        v1 = _build_index(hist, drop_last=1)
        index_bytes = tracemalloc.get_traced_memory()[0] - base0
        ref_v1 = weakref.ref(v1)
        v1_off = _build_index(hist, drop_last=1)                  # aynı içerik, ayrı nesne (soğuk iç önbellekler)
        v2 = _build_index(hist)
        r = _refresher(on, [v1, v2])
        del v1, v2
        gc.collect()
        s0, before = tracemalloc.take_snapshot(), tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        r.refresh_once()                                          # yayım + ön ısıtma
        assert on._evidence_cache().wait_idle(60)
        on_peak = tracemalloc.get_traced_memory()[1] - before
        gc.collect()
        on_kept = _kept(s0, tracemalloc.take_snapshot())
        # KAPALI yol: tur aynı kanıtı kendisi hesaplar (bugünkü davranış)
        off._refresher = type("R", (), {"bundle": type("B", (), {"engine": v1_off, "version": 1})()})()
        now = _now_for(v1_off)
        gc.collect()
        s2, before = tracemalloc.take_snapshot(), tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        for s_ in SYMS:
            off._pattern_evidence(s_, now)
        off_peak = tracemalloc.get_traced_memory()[1] - before
        gc.collect()
        off_kept = _kept(s2, tracemalloc.take_snapshot())
        assert on_peak < 0.5 * index_bytes, ("ön ısıtma indeks boyutunda bellek ayırdı", on_peak, index_bytes)
        assert on_peak <= 1.25 * off_peak + 65536, (on_peak, off_peak)
        assert on_kept <= 1.25 * off_kept + 65536, (on_kept, off_kept)
        r.refresh_once()                                          # sürüm 2: sürüm 1 erişilemez olmalı
        assert on._evidence_cache().wait_idle(60)
    finally:
        tracemalloc.stop()
    gc.collect()
    assert ref_v1() is None, "eski indeks bellekte tutuluyor (ön ısıtma/önbellek referansı)"
