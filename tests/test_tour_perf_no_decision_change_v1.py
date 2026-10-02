# -*- coding: utf-8 -*-
"""TUR HIZLANDIRMALARI KARAR DEĞİŞTİRMEZ (2026-10-01).

GERÇEK `TradingEngineV3.tour` (ağsız harness; ortak deneyim altın testinin kurulumu: T2/M2/D4 kâğıt defterleri,
formasyon defteri, öğrenme modu, saat dondurulmuş, kimlikler deterministik) beş tur koşulur. Turlar arasında GERÇEK bir
`SimilarPatternEngine` indeksi iki kez yeniden YAYIMLANIR (sürüm 1 → 2 → 3), yani turlar farklı indeks sürümlerinin
kanıtını görür. Hızlandırmalar AÇIK ve KAPALI koşulur; `state/` altındaki HER dosya (defterler, planlar, risk.json,
karar günlüğü, karşı-olgusal kayıtlar, kanıt dosyaları, health.json …) BAYT BAYT aynı olmalıdır.

AÇIK koşu iki biçimde: ön ısıtma her turdan önce BİTMİŞ ve ön ısıtma tur başlarken HÂLÂ KOŞUYOR (bekleme yok).
"""
from __future__ import annotations

import random
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

time_machine = pytest.importorskip("time_machine")

BAR_4H = 14_400_000


def _index_history(symbols, now_ms: int, n: int = 420):
    from test_patterns import _candles
    start = now_ms - now_ms % BAR_4H - n * BAR_4H
    return {s: _candles(n, seed=31 + i, drift=0.0003 * (i - 1), tf_ms=BAR_4H, start=start) for i, s in enumerate(symbols)}


def _index(hist, drop_last: int):
    from tradingbot.patterns import SimilarPatternEngine
    eng = SimilarPatternEngine(min_sample=30, horizon=24)
    for s, df in hist.items():
        eng.add_series(s, "futures", "4h", df.iloc[: len(df) - drop_last].reset_index(drop=True) if drop_last else df)
    return eng


def _set_optimizations(mp, on: bool) -> None:
    """Bütün tur hızlandırmalarının geri dönüş anahtarları (AÇIK = üretim varsayılanı)."""
    import tradingbot.chart_analysis_store as CAS
    import tradingbot.learn.entry_snapshot as ES
    from tradingbot.engine_v3 import TradingEngineV3
    mp.setattr(TradingEngineV3, "EVIDENCE_PREWARM", bool(on))      # C2: kanıt ön ısıtması
    mp.setattr(CAS, "BATCH_INDEX_WRITES", bool(on))                # C3: grafik analizi indeksi tur başına bir kez
    mp.setattr(ES, "LINKS_MEMO", bool(on))                         # C4: trade_links imza memosu
    mp.setattr(TradingEngineV3, "EXIT_EVAL_MEMO", bool(on))        # C4: kapanmış işlem çıkış değerlendirmesi memosu


def _run(root: Path, monkeypatch, *, optimized: bool, wait_prewarm: bool = True, tours: int = 5) -> dict:
    import test_engine_v3 as E
    import test_shared_experience_no_decision_change_v1 as G
    import test_strategy_paper_engine_v1 as SP

    import tradingbot.core.ids as ids_mod
    from tradingbot.patterns.refresher import IndexRefresher
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    with monkeypatch.context() as mp:
        mp.setattr(ids_mod, "os", G._DetOS())
        random.seed(7)
        mp.setattr("tradingbot.pattern_trader.scheduler.PatternScanner.start", lambda self: None)
        mp.setattr("tradingbot.box_timer.BoxTimer.start", lambda self: None)
        _set_optimizations(mp, optimized)
        import tradingbot.learn.exit_eval as EE
        n_eval = {"k": 0}
        _real_eval = EE.evaluate_trade

        def _counting_eval(**kw):
            n_eval["k"] += 1
            return _real_eval(**kw)
        mp.setattr(EE, "evaluate_trade", _counting_eval)
        ov = G._overrides(lm=True, xp={"enabled": False, "mode": "OFF"})
        ov["chart_analysis"] = {"enabled": True}             # gösterim katmanı da karşılaştırmaya girsin
        with time_machine.travel(G.T0, tick=False) as clock:
            import pandas as pd
            eng = E._engine(root, mp, ov, symbols=4, equity=200.0, p_win=0.64)
            for fr in eng._fake_live._frames.values():
                for tf, ms in (("4h", 14_400_000), ("1h", 3_600_000)):
                    fr[tf]["timestamp"] = fr[tf]["timestamp"] + ms
                    fr[tf].index = fr[tf].index + pd.Timedelta(milliseconds=ms)
            SP._install(eng, mp, btc_up=True, coin_above=True)
            syms = list(eng.cfg.coins)
            hist = _index_history(syms, int(G.T0.timestamp() * 1000))
            builds = [_index(hist, 2), _index(hist, 1), _index(hist, 0)]
            r = IndexRefresher(build_fn=lambda s: (builds.pop(0), {}), symbols_fn=lambda: list(syms),
                               update_fn=lambda s, n: {"advanced": True}, interval_s=10 ** 9,
                               on_publish=eng._on_pattern_index_published)
            eng._refresher = r                                # iş parçacığı başlatılmaz; yayımları test sürer
            r.refresh_once()                                  # sürüm 1 — ilk turdan önce
            px = {s: float(fr["4h"]["close"].iloc[-1]) for s, fr in eng._fake_live._frames.items()}
            versions = []
            try:
                for i in range(tours):
                    if optimized and wait_prewarm:
                        assert eng._evidence_cache().wait_idle(120)
                    if i == 0:
                        eng.killswitch.trip("MANUAL", "golden")
                    if i == 1:
                        eng.killswitch.reset("golden", "reset")
                    if i == 3:
                        for k, s in enumerate(sorted(px)):
                            eng._fake_live.price[s] = px[s] * (0.85 if k % 2 == 0 else 1.2)
                    eng._fake_live._now_s = __import__("time").time()
                    eng.tour(do_scan=False, obsidian=False, charts=False)
                    versions.append(r.bundle.version)
                    if i in (0, 2):
                        r.refresh_once()                      # yeni sürüm: sonraki tur yeni indeksin kanıtını görür
                    clock.shift(timedelta(minutes=10))
            finally:
                eng._evidence_cache().stop()
    files = {}
    rb = str(root).encode("utf-8")
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files[p.relative_to(root).as_posix()] = p.read_bytes().replace(rb, b"<ROOT>")
    # health.json: süreç RSS'i (learning_mode.memory.rss_mb/hwm_mb) ölçümdür, karar değil — altın testteki gibi düşülür
    import json
    h = json.loads(files["state/health.json"])
    mem = (h.get("learning_mode") or {}).get("memory")
    if isinstance(mem, dict):
        for k in ("rss_mb", "hwm_mb"):
            mem.pop(k, None)
    files["state/health.json"] = json.dumps(h, sort_keys=True).encode("utf-8")
    return {"files": files, "eng": eng, "versions": versions, "exit_evals": n_eval["k"],
            "closes": len(eng.ledger2.history)}


def _diff(a: dict, b: dict) -> list[str]:
    fa, fb = a["files"], b["files"]
    return sorted(set(fa) ^ set(fb)) + [k for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]]


@pytest.fixture(scope="module")
def _cache():
    return {}


def _baseline(tmp_path_factory, monkeypatch, cache) -> dict:
    """KAPALI koşu bir kez (testin içinde: conftest'in ağsız koruması etkin)."""
    if "off" not in cache:
        cache["off"] = _run(tmp_path_factory.mktemp("off") / "run", monkeypatch, optimized=False)
    return cache["off"]


def test_tours_across_index_publishes_are_byte_identical_with_optimizations_on(tmp_path, tmp_path_factory, monkeypatch, _cache):
    off = _baseline(tmp_path_factory, monkeypatch, _cache)
    on = _run(tmp_path / "run", monkeypatch, optimized=True)
    assert on["versions"] == off["versions"] == [1, 2, 2, 3, 3]
    assert _diff(on, off) == []
    # anlamlı senaryo: kanıt üretildi, defterlerde etkinlik var, grafik analizi yazıldı
    files = on["files"]
    assert any(k.startswith("state/evidence/") for k in files)
    assert "state/chart_analysis/index.json" in files
    eng = on["eng"]
    books = {b.key: (len(b.ledger.positions), len(b.ledger.history)) for b in eng.strategy_books}
    assert sum(sum(v) for v in books.values()) > 0, books
    # çıkış değerlendirmesi memosu gerçekten çalıştı: kapanmış ana defter işlemleri KAPALI'da her turda yeniden oynatıldı
    assert on["closes"] == off["closes"] > 0
    assert on["exit_evals"] < off["exit_evals"], (on["exit_evals"], off["exit_evals"])


def test_tours_are_identical_when_the_prewarm_is_still_running_at_tour_start(tmp_path, tmp_path_factory, monkeypatch, _cache):
    off = _baseline(tmp_path_factory, monkeypatch, _cache)
    on = _run(tmp_path / "run", monkeypatch, optimized=True, wait_prewarm=False)
    assert _diff(on, off) == []
