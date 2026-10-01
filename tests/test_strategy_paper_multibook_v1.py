# -*- coding: utf-8 -*-
"""V12 — coklu strateji kagit defteri + m2_tsmom28 varyanti (tek kaynak)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402
from test_strategy_paper_engine_v1 import SYMS, _install  # noqa: E402
from tradingbot.config_v3 import ConfigError, load_v3, validate_v3  # noqa: E402
from tradingbot.ema200_trend import TSMOM_LOOKBACK_DAYS, VARIANTS, decide  # noqa: E402
from tradingbot.strategy_paper import BookSpec, book_specs  # noqa: E402

D1 = 24 * 3_600_000


def _rows(n=260, close=100.0, ema=95.0, atr=2.0, ref=None):
    out = []
    for i in range(n):
        c = close
        if ref is not None and i == n - 1 - TSMOM_LOOKBACK_DAYS:
            c = ref
        out.append({"timestamp": i * D1, "open": c, "high": c + 1, "low": c - 1, "close": c, "ema200": ema, "atr14": atr})
    return out


def _btc(up=True):
    return [{"timestamp": 0, "close": 100.0, "ema200": 90.0 if up else 110.0}]


# ------------------------------------------------------------------ kural
def test_m2_uses_28_day_reference_not_the_ema():
    assert "m2_tsmom28" in VARIANTS
    # close 100 > 28 gun once 90 -> LONG (EMA200 105'in ALTINDA olsa bile: sinyal EMA degil)
    a = decide("m2_tsmom28", daily_rows=_rows(ema=105.0, ref=90.0), btc_daily_rows=_btc(True), position_open=False)
    assert a and a["action"] == "OPEN" and a["reason"] == "M2_TSMOM28" and a["stop"] == pytest.approx(94.0)
    # close 100 < 28 gun once 110 -> giris yok / acik pozisyon kapanir
    assert decide("m2_tsmom28", daily_rows=_rows(ref=110.0), btc_daily_rows=_btc(True), position_open=False) is None
    c = decide("m2_tsmom28", daily_rows=_rows(ref=110.0), btc_daily_rows=_btc(True), position_open=True)
    assert c == {"action": "CLOSE", "reason": "M2_TSMOM28_CROSS_DOWN", "name": "m2_tsmom28"}
    # BTC rejimi DOWN -> giris yok (T2 ile ayni kapi)
    assert decide("m2_tsmom28", daily_rows=_rows(ref=90.0), btc_daily_rows=_btc(False), position_open=False) is None
    # t2 davranisi degismedi
    assert decide("t2_trend_regime", daily_rows=_rows(ema=105.0, ref=90.0), btc_daily_rows=_btc(True), position_open=False) is None


# ------------------------------------------------------------------ config
def _cfg(extra, mode="PAPER"):
    cfg = load_v3({"mode": mode, "strategy_paper": {"enabled": True, "name": "t2_trend_regime", "extra": extra}})
    validate_v3(cfg)
    return cfg


def test_extra_books_validate_and_build_specs():
    cfg = _cfg([{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}])
    specs = book_specs(cfg)
    assert [(b.name, b.state_dir, b.summary_file) for b in specs] == [
        ("t2_trend_regime", "strategy_paper", "strategy_paper.json"), ("m2_tsmom28", "strategy_paper_m2", "strategy_paper_m2.json")]
    assert isinstance(specs[1], BookSpec) and specs[1].starting_equity_usdt == 100.0 and specs[1].atr_mult == 3.0
    off = load_v3({"mode": "PAPER", "strategy_paper": {"enabled": False, "extra": [{"name": "m2_tsmom28", "state_dir": "x", "enabled": False}]}})
    validate_v3(off)
    assert book_specs(off) == []


def test_extra_books_reject_duplicate_or_bad_dirs_and_bad_names():
    with pytest.raises(ConfigError):
        _cfg([{"name": "m2_tsmom28", "state_dir": "strategy_paper"}])          # ana defterle ayni dizin
    with pytest.raises(ConfigError):
        _cfg([{"name": "m2_tsmom28", "state_dir": "../x"}])
    with pytest.raises(ConfigError):
        _cfg([{"name": "m9", "state_dir": "strategy_paper_m9"}])
    with pytest.raises(ConfigError):
        _cfg([{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}], mode="LIVE")


# ------------------------------------------------------------------ motor: iki defter yan yana
def test_two_books_run_side_by_side_with_separate_ledgers(tmp_path, monkeypatch):
    ov = _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                             "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}]}}
    eng = _engine(tmp_path, monkeypatch, ov, symbols=2, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    assert [b.key for b in eng.strategy_books] == ["strategy_paper", "strategy_paper_m2"]
    assert eng.strategy_book is eng.strategy_books[0]
    _install(eng, monkeypatch, btc_up=True, coin_above=True)      # sentetik seri yukselen: close > close[-28] de saglanir
    eng.tour(do_scan=False, obsidian=False, charts=False)
    t2, m2 = eng.strategy_books
    assert set(t2.ledger.positions) == set(SYMS), t2.rejections
    assert set(m2.ledger.positions) == set(SYMS), m2.rejections
    assert t2.ledger_path != m2.ledger_path and t2.ledger_path.exists() and m2.ledger_path.exists()
    assert not eng.ledger2.positions, "ana defter ETKILENMEMELI"
    st = eng.cfg.state_path
    idx = json.loads((st / "strategy_paper_index.json").read_text(encoding="utf-8"))
    assert [b["summary_file"] for b in idx["books"]] == ["strategy_paper.json", "strategy_paper_m2.json"]
    d2 = json.loads((st / "strategy_paper_m2.json").read_text(encoding="utf-8"))
    assert d2["name"] == "m2_tsmom28" and d2["key"] == "strategy_paper_m2" and set(d2["positions"]) == set(SYMS)
    d1 = json.loads((st / "strategy_paper.json").read_text(encoding="utf-8"))
    assert d1["name"] == "t2_trend_regime" and d1["key"] == "strategy_paper"


# ------------------------------------------------------------------ dashboard
def test_dashboard_lists_every_book(tmp_path):
    from fastapi.testclient import TestClient
    from tradingbot.dashboard.app import create_app
    (tmp_path / "data").mkdir(exist_ok=True)
    base = {"schema_version": "strategy_paper_v1", "generated_at": "x", "run_id": "r", "atr_mult": 3.0, "regime": "UP",
            "starting_equity": 100.0, "summary": {"equity_mtm": 101.0}, "positions": {}, "history_tail": [],
            "last_actions": {}, "counters": {"opened": 0, "closed": 0, "rejected": 0, "tours": 1}, "rejections": {},
            "closed_recent": [], "note_tr": "KAGIT"}
    (tmp_path / "strategy_paper.json").write_text(json.dumps(base | {"name": "t2_trend_regime", "key": "strategy_paper"}), encoding="utf-8")
    (tmp_path / "strategy_paper_m2.json").write_text(json.dumps(base | {"name": "m2_tsmom28", "key": "strategy_paper_m2", "summary": {"equity_mtm": 103.5}}), encoding="utf-8")
    (tmp_path / "strategy_paper_index.json").write_text(json.dumps({"books": [
        {"key": "strategy_paper", "name": "t2_trend_regime", "summary_file": "strategy_paper.json"},
        {"key": "strategy_paper_m2", "name": "m2_tsmom28", "summary_file": "strategy_paper_m2.json"}]}), encoding="utf-8")
    c = TestClient(create_app(tmp_path, tmp_path / "data"))
    r = c.get("/")
    assert r.status_code == 200 and "t2_trend_regime" in r.text and "m2_tsmom28" in r.text and "103.5" in r.text
    r = c.get("/portfolio/strategy")
    assert r.status_code == 200 and r.text.count("defter strategy_paper") >= 2
