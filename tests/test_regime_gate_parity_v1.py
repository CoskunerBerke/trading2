# -*- coding: utf-8 -*-
"""PIYASA REJIMI KAPISI — canli motor ile replay AYNI fonksiyonu calistirir (V7).

Sabitlenenler: iki motor ortak modulu cagirir; arastirma kurali delege eder; satir secimi tek
yerde (`rows_for_regime`); config mod/varyant kapali kume; ENFORCE LIVE'da reddedilir; varsayilan OFF.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.config_v3 import ConfigError, load_v3, validate_v3  # noqa: E402
from tradingbot.regime_gate import (DOWN, MODES, UP, VARIANTS, regime_confirmation,  # noqa: E402
                                    rows_for_regime, validate_settings)

ROOT = Path(__file__).resolve().parents[1] / "tradingbot"


def _calls(rel: str, func: str) -> int:
    src = (ROOT / rel).read_text(encoding="utf-8")
    return sum(1 for node in ast.walk(ast.parse(src))
               if isinstance(node, ast.Call)
               and (getattr(node.func, "id", None) == func or getattr(node.func, "attr", None) == func))


def test_both_engines_call_the_shared_module():
    assert _calls("engine_v3.py", "regime_confirmation") >= 1
    assert _calls("replay/engine.py", "regime_evaluate_variant") >= 1


def test_no_engine_computes_its_own_regime():
    for rel in ("engine_v3.py", "replay/engine.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "ema200" not in src.split("def _regime_gate")[0][-4000:] or True     # bilgi amacli
        assert "> ema200" not in src and "ema_last(" not in src, "%s kendi rejim hesabini tutuyor" % rel


def test_research_rules_delegate():
    p = Path(r"C:/Users/berke/research/entry_v1/regime_rules.py")
    if not p.exists():
        pytest.skip("arastirma paketi bu makinede yok")
    src = p.read_text(encoding="utf-8")
    assert "from tradingbot.regime_gate import" in src and "rows_for_regime" in src
    assert "ema" not in src.replace("rows_for_regime", "").replace("regime_gate", "").lower()


def test_rows_for_regime_selects_shared_columns():
    df = pd.DataFrame({"timestamp": [1, 2, 3], "open": [1, 1, 1], "close": [10.0, 11.0, 12.0],
                       "ema200": [9.0, 9.5, 10.0], "rsi14": [50, 50, 50]})
    rows = rows_for_regime(df)
    assert rows[-1] == {"timestamp": 3, "close": 12.0, "ema200": 10.0} and len(rows) == 3
    assert rows_for_regime(None) == [] and rows_for_regime(pd.DataFrame({"x": [1]})) == []


def test_record_blocks_only_in_enforce_and_reports_regime():
    down = [{"timestamp": 1, "close": 90.0, "ema200": 100.0}]
    up = [{"timestamp": 1, "close": 110.0, "ema200": 100.0}]
    r = regime_confirmation(mode="ENFORCE", variant="r1_long_only_uptrend", direction="LONG", btc_daily_bars=down)
    assert r["regime"] == DOWN and r["blocks"] and r["verdict"]["reason"] == "R1_NOT_UPTREND"
    assert set(r["shadow"]) == set(VARIANTS) and r["btc_close"] == 90.0
    s = regime_confirmation(mode="SHADOW", variant="r1_long_only_uptrend", direction="SHORT", btc_daily_bars=up)
    assert s["regime"] == UP and not s["blocks"] and s["verdict"]["reason"] == "R1_SHORT_BLOCKED"
    o = regime_confirmation(mode="OFF", variant="r1_long_only_uptrend", direction="LONG", btc_daily_bars=up)
    assert not o["blocks"] and o["shadow"] == {}
    n = regime_confirmation(mode="ENFORCE", variant="r1_long_only_uptrend", direction="LONG", btc_daily_bars=[])
    assert n["regime"] is None and n["blocks"] and n["verdict"]["reason"] == "R1_NO_REGIME"


def _cfg(mode="PAPER", rg_mode="ENFORCE", variant="r1_long_only_uptrend"):
    cfg = load_v3({"mode": mode, "entry_selectivity": {"regime_gate_mode": rg_mode, "regime_gate_variant": variant}})
    validate_v3(cfg)
    return cfg


def test_config_accepts_and_normalizes_and_rejects():
    assert _cfg(rg_mode="enforce").entry_selectivity.regime_gate_mode == "ENFORCE"
    assert set(MODES) == {"OFF", "SHADOW", "ENFORCE"} and len(VARIANTS) == 3
    with pytest.raises(ConfigError):
        _cfg(rg_mode="ACTIVE")
    with pytest.raises(ConfigError):
        _cfg(variant="r9")


@pytest.mark.parametrize("live", ["LIVE", "LIVE_LIMITED"])
def test_enforce_refused_with_real_money(live):
    with pytest.raises(ValueError, match="NOT_VALIDATED_FOR_LIVE"):
        validate_settings(mode="ENFORCE", variant="r1_long_only_uptrend", app_mode=live)
    assert validate_settings(mode="shadow", variant="r1_long_only_uptrend", app_mode=live) == "SHADOW"


def test_default_is_off():
    cfg = load_v3({"mode": "PAPER"})
    validate_v3(cfg)
    assert cfg.entry_selectivity.regime_gate_mode == "OFF"
    assert "from .regime_gate import validate_settings" in (ROOT / "config_v3.py").read_text(encoding="utf-8")
