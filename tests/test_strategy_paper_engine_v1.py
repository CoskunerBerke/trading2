# -*- coding: utf-8 -*-
"""STRATEJI KAGIT DEFTERI — canli motor turunda GERCEKTEN calisiyor mu? (V10)

Gercek `TradingEngineV3.tour` surulur. BTC gunluk karesi UP, coin gunluk karesi EMA200 ustunde
→ strateji defterinde LONG acilir (ana defterden BAGIMSIZ). Sonra coin EMA200 altina duser →
`EMA200_CROSS_DOWN` ile kapanir. Kapaliyken ozet dosyasi yazilmaz ve motorda defter yoktur.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402

SYMS = ("ETH/USDT", "SOL/USDT")
BTC = "BTC/USDT"


def _install(eng, monkeypatch, *, btc_up: bool, coin_above: bool):
    """Motorun okudugu kareleri (KOPYA) ayarlar: BTC 1d ve coin 1d ema200 konumu."""
    orig = eng.runner.run_symbol

    def wrapped(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        fr = dict(eng.runner.last_frames[symbol])
        d1 = fr["1d"].copy()
        # Sentetik 1d ve 4h kareleri BAGIMSIZ uretilir; gercekte ayni fiyat serisidir. Gunluk kapanisi
        # 4h isaret fiyatina olcekle ki stop (close - 3*ATR) girisin ALTINDA kalsin (uretimdeki durum).
        scale = float(fr["4h"]["close"].iloc[-1]) / float(d1["close"].iloc[-1])
        for col in ("open", "high", "low", "close"):
            d1[col] = d1[col] * scale
        d1["ema200"] = d1["close"] * (0.95 if coin_above else 1.05)
        d1["atr14"] = d1["close"] * 0.03     # stop %9 -> notional = %2 / %9 = ozkaynagin %22'si (tek coin tavani %30 altinda)
        fr["1d"] = d1
        eng.runner.last_frames[symbol] = fr
        if BTC not in eng.runner.last_frames:
            bfr = dict(fr)
            b1 = fr["1d"].copy()
            b1["ema200"] = b1["close"] * (0.95 if btc_up else 1.05)
            bfr["1d"] = b1
            eng.runner.last_frames[BTC] = bfr
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", wrapped)


def _sp_doc(eng):
    return json.loads((eng.cfg.state_path / "strategy_paper.json").read_text(encoding="utf-8"))


def test_strategy_book_opens_longs_then_closes_on_cross_down(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime"}},
                  symbols=2, equity=EQUITY)
    _force_triggers(monkeypatch, False)                   # ana bot hicbir sey acmasin; yalniz strateji defteri
    assert eng.strategy_book is not None and eng.strategy_book.name == "t2_trend_regime"
    _install(eng, monkeypatch, btc_up=True, coin_above=True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    book = eng.strategy_book
    assert set(book.ledger.positions) == set(SYMS), (dict(book.ledger.positions), book.rejections)
    assert all(p.side.value == "LONG" and p.leverage == 1 for p in book.ledger.positions.values())
    assert not eng.ledger2.positions, "ana defter ETKILENMEMELI"
    doc = _sp_doc(eng)
    assert doc["regime"] == "UP" and set(doc["positions"]) == set(SYMS) and doc["counters"]["opened"] == 2
    assert (eng.cfg.state_path / "strategy_paper" / "futures_ledger.json").exists()
    # --- coin EMA200 altina duser: kural kapatir
    _install(eng, monkeypatch, btc_up=True, coin_above=False)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert not book.ledger.positions
    doc = _sp_doc(eng)
    assert doc["counters"]["closed"] == 2
    assert all(t["exit_reason"] == "EMA200_CROSS_DOWN" for t in doc["history_tail"])
    assert not eng.ledger2.history_dicts(), "ana defter ETKILENMEMELI"


def test_strategy_book_stays_flat_in_btc_downtrend(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _profile(6.0) | {"strategy_paper": {"enabled": True}}, symbols=2, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    _install(eng, monkeypatch, btc_up=False, coin_above=True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert not eng.strategy_book.ledger.positions
    doc = _sp_doc(eng)
    assert doc["regime"] == "DOWN" and doc["counters"]["opened"] == 0


def test_disabled_by_default_writes_nothing(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=2, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    assert eng.strategy_book is None
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert not (eng.cfg.state_path / "strategy_paper.json").exists()
