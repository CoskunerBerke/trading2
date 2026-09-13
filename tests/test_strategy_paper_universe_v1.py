# -*- coding: utf-8 -*-
"""V13 -- strateji defterleri (1) YALNIZ olculen giris evreninde acar, (2) evren disinda kalan kendi acik
pozisyonlarini yonetmeye devam eder (kural kapanisi), (3) yeniden baslatmada sayaclari kaybetmez.

Gercek olay (VPS, 2026-09-13): ana defterin acik ZEN/USDT pozisyonu tur listesine girdi, T2 defteri de
ZEN'de LONG acti -- olculmemis evren. Ayrica restart sonrasi 'opened: 0' iken 3 acik pozisyon goruldu.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402
from test_strategy_paper_engine_v1 import SYMS, _install  # noqa: E402
from tradingbot.strategy_paper import StrategyBook, book_specs  # noqa: E402

ETH, SOL = SYMS


def _eng(tmp_path, monkeypatch, universe):
    ov = _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime"},
                          "entry_universe": {"enabled": True, "symbols": list(universe)}}
    eng = _engine(tmp_path, monkeypatch, ov, symbols=2, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    return eng


def _doc(eng):
    return json.loads((eng.cfg.state_path / "strategy_paper.json").read_text(encoding="utf-8"))


def test_book_opens_only_inside_the_measured_universe(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, [ETH])                       # evren: yalniz ETH
    _install(eng, monkeypatch, btc_up=True, coin_above=True)
    # tur listesi ana bot yuzunden SOL'u da icerir (ZEN olayinin esdegeri)
    eng.tour(do_scan=False, obsidian=False, charts=False, symbols_override=[ETH, SOL])
    book = eng.strategy_book
    assert set(book.ledger.positions) == {ETH}, (dict(book.ledger.positions), book.rejections)
    assert SOL not in book.last_actions, "evren disi sembol defter tarafindan HIC degerlendirilmemeli"
    assert _doc(eng)["counters"]["opened"] == 1


def test_book_keeps_managing_a_position_that_left_the_universe(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, [ETH, SOL])
    _install(eng, monkeypatch, btc_up=True, coin_above=True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    book = eng.strategy_book
    assert set(book.ledger.positions) == {ETH, SOL}
    # evren daralir: SOL artik giris izinli degil -- ama defterde ACIK
    eng.cfg.v3.entry_universe.symbols = [ETH]
    _install(eng, monkeypatch, btc_up=True, coin_above=False)        # her iki coin EMA200 altina duser
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert not book.ledger.positions, "evren disi acik pozisyon KURALLA kapanmali (stop beklemeden)"
    doc = _doc(eng)
    assert doc["counters"]["closed"] == 2
    assert {t["symbol"]: t["exit_reason"] for t in doc["history_tail"]} == {ETH: "EMA200_CROSS_DOWN", SOL: "EMA200_CROSS_DOWN"}
    # yeniden yukselis: yalniz ETH acilir, SOL evren disi kaldigi icin acilmaz
    _install(eng, monkeypatch, btc_up=True, coin_above=True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert set(book.ledger.positions) == {ETH}


def test_main_tour_scope_includes_book_positions_even_after_main_ledger_is_flat(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, [ETH, SOL])
    _install(eng, monkeypatch, btc_up=True, coin_above=True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    eng.cfg.v3.entry_universe.symbols = [ETH]
    assert not eng.ledger2.positions
    assert eng._strategy_open_symbols() == [ETH, SOL]
    eng.runner.last_frames.pop(SOL, None)                              # onceki turun cercevesi sayilmasin
    eng.tour(do_scan=False, obsidian=False, charts=False)
    # defterin SOL pozisyonu icin cerceve/fiyat YENIDEN alindi: last_frames'te SOL var ve tick'lendi
    assert SOL in eng.runner.last_frames
    assert eng.strategy_book.ledger.positions[SOL].last_price is not None


def test_counters_survive_a_restart(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, [ETH, SOL])
    _install(eng, monkeypatch, btc_up=True, coin_above=True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    eng.tour(do_scan=False, obsidian=False, charts=False)             # 2. tur: pozisyonlar acik, sinyal yok
    old = eng.strategy_book
    assert old.counters == {"opened": 2, "closed": 0, "rejected": 0, "tours": 2}
    # yeniden baslatma: ayni config, ayni state dizini, YENI nesne
    nb = StrategyBook(eng.cfg, profile=eng.profile, killswitch=eng.killswitch, filters_cache=eng.filters,
                      run_id="", spec=book_specs(eng.cfg.v3)[0])
    assert nb.counters == {"opened": 2, "closed": 0, "rejected": 0, "tours": 2}
    assert set(nb.ledger.positions) == {ETH, SOL}
    # bozuk ozet dosyasi: defter gercegi yine ayakta, sayaclar sifirdan (istisna YOK)
    (eng.cfg.state_path / "strategy_paper.json").write_text("{not json", encoding="utf-8")
    nb2 = StrategyBook(eng.cfg, profile=eng.profile, killswitch=eng.killswitch, filters_cache=eng.filters,
                       run_id="", spec=book_specs(eng.cfg.v3)[0])
    assert nb2.counters["opened"] == 2 and nb2.counters["closed"] == 0 and nb2.counters["tours"] == 0
