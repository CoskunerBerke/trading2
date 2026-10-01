# -*- coding: utf-8 -*-
"""GRAFIK FORMASYONU ONAYI — canli motor ile replay AYNI fonksiyonu calistirir (V5).

Sabitlenenler: iki motor da `chart_confirmation` modulunu cagirir; arastirma kurali delege
eder; config mod/varyant kapali kume; ENFORCE gercek parayla (LIVE) REDDEDILIR; varsayilan OFF.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.chart_confirmation import MODES, VARIANTS, validate_settings  # noqa: E402
from tradingbot.config_v3 import ConfigError, load_v3, validate_v3  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "tradingbot"


def _calls(rel: str, func: str) -> int:
    src = (ROOT / rel).read_text(encoding="utf-8")
    return sum(1 for node in ast.walk(ast.parse(src))
               if isinstance(node, ast.Call)
               and (getattr(node.func, "id", None) == func or getattr(node.func, "attr", None) == func))


def test_both_engines_call_the_shared_module():
    assert _calls("engine_v3.py", "chart_confirmation") >= 1, "canli motor ortak modulu cagirmiyor"
    assert _calls("replay/engine.py", "chart_evaluate_variant") >= 1, "replay ortak modulu cagirmiyor"


def test_no_engine_detects_patterns_itself():
    for rel in ("engine_v3.py", "replay/engine.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "confirmed_swings(" not in src and "DOUBLE_BOTTOM" not in src, \
            "%s kendi formasyon tespitini tutuyor" % rel


def test_research_rules_delegate_to_the_shared_module():
    p = Path(r"C:/Users/berke/research/entry_v1/chart_rules.py")
    if not p.exists():
        pytest.skip("arastirma paketi bu makinede yok")
    src = p.read_text(encoding="utf-8")
    assert "from tradingbot.chart_confirmation import" in src
    assert "confirmed_swings" not in src and "def _doubles" not in src


def _cfg(mode="PAPER", ch_mode="SHADOW", variant="p2_4h_veto", policy=None):
    raw = {"mode": mode, "entry_selectivity": {"chart_confirmation_mode": ch_mode,
                                              "chart_confirmation_variant": variant,
                                              "chart_policy": policy or {}}}
    cfg = load_v3(raw)
    validate_v3(cfg)
    return cfg


def test_config_accepts_shadow_and_enforce_in_paper_and_normalizes():
    assert _cfg(ch_mode="shadow").entry_selectivity.chart_confirmation_mode == "SHADOW"
    assert _cfg(ch_mode="enforce").entry_selectivity.chart_confirmation_mode == "ENFORCE"
    assert set(MODES) == {"OFF", "SHADOW", "ENFORCE"} and len(VARIANTS) == 4


def test_config_rejects_unknown_mode_variant_or_policy():
    with pytest.raises(ConfigError):
        _cfg(ch_mode="ACTIVE")
    with pytest.raises(ConfigError):
        _cfg(variant="p9")
    with pytest.raises(ConfigError):
        _cfg(policy={"flag_max_retrace": 2.0})


@pytest.mark.parametrize("live", ["LIVE", "LIVE_LIMITED"])
def test_enforce_is_refused_with_real_money(live):
    with pytest.raises(ValueError, match="NOT_VALIDATED_FOR_LIVE"):
        validate_settings(mode="ENFORCE", variant="p2_4h_veto", app_mode=live)
    assert validate_settings(mode="shadow", variant="p2_4h_veto", app_mode=live) == "SHADOW"


def test_default_is_off_and_validate_v3_uses_the_shared_validator():
    cfg = load_v3({"mode": "PAPER"})
    validate_v3(cfg)
    assert cfg.entry_selectivity.chart_confirmation_mode == "OFF"
    src = (ROOT / "config_v3.py").read_text(encoding="utf-8")
    assert "from .chart_confirmation import validate_settings" in src
