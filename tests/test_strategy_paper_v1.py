# -*- coding: utf-8 -*-
"""STRATEJI KAGIT DEFTERI (V10) — kural birim testi, tek kaynak (AST), config sozlesmesi."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.config_v3 import ConfigError, load_v3, validate_v3  # noqa: E402
from tradingbot.ema200_trend import (MIN_DAILY_BARS, VARIANTS, daily_rows_from_frame, decide,  # noqa: E402
                                     read_daily)
from tradingbot.strategy_paper import validate_settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "tradingbot"
D1 = 24 * 3_600_000


def _rows(n=260, close=100.0, ema=95.0, atr=2.0, with_cols=True):
    out = []
    for i in range(n):
        r = {"timestamp": i * D1, "open": close, "high": close + 1, "low": close - 1, "close": close}
        if with_cols:
            r.update({"ema200": ema, "atr14": atr})
        out.append(r)
    return out


def _btc(up=True):
    return [{"timestamp": 0, "close": 100.0, "ema200": 90.0 if up else 110.0}]


def test_decide_opens_long_above_ema_and_closes_below():
    a = decide("t2_trend_regime", daily_rows=_rows(), btc_daily_rows=_btc(True), position_open=False)
    assert a and a["action"] == "OPEN" and a["direction"] == "LONG" and a["stop"] == pytest.approx(100 - 3 * 2.0)
    assert a["leverage"] == 1 and a["targets"] == [] and a["regime"] == "UP"
    assert decide("t2_trend_regime", daily_rows=_rows(), btc_daily_rows=_btc(True), position_open=True) is None
    c = decide("t2_trend_regime", daily_rows=_rows(ema=105.0), btc_daily_rows=_btc(True), position_open=True)
    assert c == {"action": "CLOSE", "reason": "EMA200_CROSS_DOWN", "name": "t2_trend_regime"}
    assert decide("t2_trend_regime", daily_rows=_rows(ema=105.0), btc_daily_rows=_btc(True), position_open=False) is None


def test_t2_needs_btc_uptrend_and_t1_does_not():
    assert decide("t2_trend_regime", daily_rows=_rows(), btc_daily_rows=_btc(False), position_open=False) is None
    assert decide("t2_trend_regime", daily_rows=_rows(), btc_daily_rows=[], position_open=False) is None
    a = decide("t1_trend", daily_rows=_rows(), btc_daily_rows=None, position_open=False)
    assert a and a["action"] == "OPEN" and a["regime"] is None


def test_fail_closed_on_short_or_missing_data_and_computes_from_closes_when_columns_absent():
    assert decide("t1_trend", daily_rows=_rows(n=MIN_DAILY_BARS - 1), btc_daily_rows=None, position_open=False) is None
    assert read_daily([]) is None
    rows = _rows(with_cols=False)
    for i, r in enumerate(rows):                          # yukselen kapanislar: close > EMA200
        r["close"] = 100.0 + i * 0.5; r["high"] = r["close"] + 1; r["low"] = r["close"] - 1
    d = read_daily(rows)
    assert d is not None and d[0] > d[1] and d[2] > 0
    with pytest.raises(ValueError):
        decide("t9", daily_rows=_rows(), btc_daily_rows=None, position_open=False)


def test_daily_rows_from_frame_selects_shared_columns():
    df = pd.DataFrame({"timestamp": [1, 2], "open": [1, 1], "high": [2, 2], "low": [0, 0], "close": [1.0, 1.5],
                       "ema200": [1.0, 1.1], "atr14": [0.1, 0.1], "rsi14": [50, 50]})
    r = daily_rows_from_frame(df)
    assert len(r) == 2 and set(r[0]) == {"timestamp", "open", "high", "low", "close", "ema200", "atr14"}
    assert daily_rows_from_frame(None) == [] and daily_rows_from_frame(pd.DataFrame({"x": [1]})) == []


# ------------------------------------------------------------------ tek kaynak
def _calls(rel: str, func: str) -> int:
    src = (ROOT / rel).read_text(encoding="utf-8")
    return sum(1 for node in ast.walk(ast.parse(src))
               if isinstance(node, ast.Call)
               and (getattr(node.func, "id", None) == func or getattr(node.func, "attr", None) == func))


def test_both_engines_apply_actions_through_the_shared_executor():
    assert _calls("replay/engine.py", "apply_action") >= 1, "replay ortak uygulayiciyi cagirmiyor"
    assert _calls("strategy_paper.py", "apply_action") >= 1, "kagit defter ortak uygulayiciyi cagirmiyor"
    assert _calls("strategy_paper.py", "decide") >= 1, "kagit defter ortak kurali cagirmiyor"
    assert "_strategy_paper_tour(" in (ROOT / "engine_v3.py").read_text(encoding="utf-8")
    rp = (ROOT / "replay" / "engine.py").read_text(encoding="utf-8")
    assert "risk_per_trade_pct" not in rp.split("def _strategy_step")[1].split("def _on_closed")[0], \
        "replay boyutlandirmayi KENDI icinde tutuyor (tek kaynak ihlali)"


def test_research_rules_delegate_to_the_shared_rule():
    p = Path(r"C:/Users/berke/research/entry_v1/strategy_rules.py")
    if not p.exists():
        pytest.skip("arastirma paketi bu makinede yok")
    src = p.read_text(encoding="utf-8")
    assert "from tradingbot.ema200_trend import" in src
    assert "ema200" not in src.replace("ema200_trend", "") and "ATR_MULT" not in src


# ------------------------------------------------------------------ config
def _cfg(mode="PAPER", **sp):
    cfg = load_v3({"mode": mode, "strategy_paper": {"enabled": True, "name": "t2_trend_regime", **sp}})
    validate_v3(cfg)
    return cfg


def test_config_defaults_off_and_accepts_paper():
    cfg = load_v3({"mode": "PAPER"})
    validate_v3(cfg)
    assert cfg.strategy_paper.enabled is False and cfg.strategy_paper.name in VARIANTS
    c = _cfg()
    assert c.strategy_paper.enabled and c.strategy_paper.starting_equity_usdt == 100.0 and c.strategy_paper.atr_mult == 3.0


def test_config_rejects_bad_name_values_and_live():
    with pytest.raises(ConfigError):
        _cfg(name="t9")
    with pytest.raises(ConfigError):
        _cfg(starting_equity_usdt=0)
    with pytest.raises(ValueError, match="PAPER_ONLY"):
        validate_settings(enabled=True, name="t2_trend_regime", app_mode="LIVE", starting_equity=100, atr_mult=3.0)
    validate_settings(enabled=False, name="t2_trend_regime", app_mode="LIVE", starting_equity=100, atr_mult=3.0)
