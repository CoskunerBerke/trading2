# -*- coding: utf-8 -*-
"""MUM ONAYI — canli motor ile replay AYNI fonksiyonu calistirir (V4, 2026-09-12).

Sabitlenenler:
  1. Iki motor da `candle_confirmation` modulunu cagirir; taraf kumesi tek kaynaktir.
  2. Varyant davranisi: hizali/karsi/yok sekil, teyit, eksik bar, veto-only fail-open.
  3. Kapanmamis bar formasyona GIRMEZ.
  4. Config: mod/varyant kapali kume; ENFORCE gercek parayla (LIVE) REDDEDILIR.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.candle_confirmation import (MODES, VARIANTS, candle_confirmation,  # noqa: E402
                                            closed_bars, evaluate_variant, validate_settings)
from tradingbot.config_v3 import ConfigError, load_v3, validate_v3  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "tradingbot"
H4 = 4 * 3_600_000


def _bar(ts, o, h, lo, c):
    return {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c}


# oncu notr bar + ayi bar + DELICI CIZGI (boga tarafli sekil)
BULL = [_bar(0, 105, 105.5, 103.8, 104), _bar(H4, 104, 104.5, 99.5, 100),
        _bar(2 * H4, 100, 103.4, 99.2, 103)]
CONFIRM = [_bar(3 * H4, 103, 104.2, 102.5, 103.8)]
# uc boga govde, kapanislar tek yonlu DEGIL, son bar topac → tarafsiz
FLAT = [_bar(0, 100.0, 100.8, 99.8, 100.5), _bar(H4, 100.0, 100.6, 99.7, 100.3),
        _bar(2 * H4, 100.0, 100.9, 99.5, 100.4)]


def _calls(rel: str, func: str) -> int:
    src = (ROOT / rel).read_text(encoding="utf-8")
    return sum(1 for node in ast.walk(ast.parse(src))
               if isinstance(node, ast.Call)
               and (getattr(node.func, "id", None) == func or getattr(node.func, "attr", None) == func))


# ------------------------------------------------------------------ 1) tek kaynak
def test_both_engines_call_the_shared_module():
    assert _calls("engine_v3.py", "candle_confirmation") >= 1, "canli motor ortak modulu cagirmiyor"
    assert _calls("replay/engine.py", "evaluate_variant") >= 1, "replay ortak modulu cagirmiyor"


def test_no_engine_defines_its_own_side_set():
    names = {"HAMMER_LIKE", "BULLISH_ENGULFING_LIKE", "BEARISH_ENGULFING_LIKE", "PIERCING_LINE_LIKE"}
    for rel in ("engine_v3.py", "replay/engine.py", "learn/entry_challenger_v2.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Set):
                ids = {getattr(e, "id", None) for e in node.elts}
                assert not (ids & names), "%s kendi taraf kumesini tutuyor" % rel


def test_research_rules_delegate_to_the_shared_module():
    p = Path(r"C:/Users/berke/research/entry_v1/candle_rules.py")
    if not p.exists():
        pytest.skip("arastirma paketi bu makinede yok")
    src = p.read_text(encoding="utf-8")
    assert "from tradingbot.candle_confirmation import" in src
    assert "def c1_pattern_bar" not in src, "arastirma kurali mantigi KOPYALIYOR"


# ------------------------------------------------------------------ 2) varyantlar
def test_c1_aligned_opposite_none_missing():
    assert evaluate_variant("c1_4h", direction="LONG", bars_4h=BULL, bars_1d=None) == (True, "")
    assert evaluate_variant("c1_4h", direction="SHORT", bars_4h=BULL, bars_1d=None) == (False, "C1_OPPOSITE_PATTERN")
    assert evaluate_variant("c1_4h", direction="LONG", bars_4h=FLAT, bars_1d=None) == (False, "C1_NO_PATTERN")
    assert evaluate_variant("c1_4h", direction="LONG", bars_4h=BULL[-2:], bars_1d=None) == (False, "C1_MISSING_BARS")
    assert evaluate_variant("c1_4h", direction="", bars_4h=BULL, bars_1d=None) == (False, "C1_SIDE")


def test_c2_needs_a_confirming_closed_bar():
    assert evaluate_variant("c2_4h_confirm", direction="LONG", bars_4h=BULL + CONFIRM, bars_1d=None) == (True, "")
    bad = BULL + [_bar(3 * H4, 103, 103.2, 101.0, 101.5)]
    assert evaluate_variant("c2_4h_confirm", direction="LONG", bars_4h=bad, bars_1d=None) == (False, "C2_NOT_CONFIRMED")
    assert evaluate_variant("c2_4h_confirm", direction="SHORT", bars_4h=BULL + CONFIRM, bars_1d=None) == (False, "C2_OPPOSITE_PATTERN")
    assert evaluate_variant("c2_4h_confirm", direction="LONG", bars_4h=BULL, bars_1d=None) == (False, "C2_MISSING_BARS")


def test_c3_only_vetoes_an_opposite_shape_and_never_blocks_without_data():
    assert evaluate_variant("c3_4h_veto", direction="LONG", bars_4h=BULL, bars_1d=None) == (True, "")
    assert evaluate_variant("c3_4h_veto", direction="SHORT", bars_4h=BULL, bars_1d=None) == (False, "C3_OPPOSITE_PATTERN")
    assert evaluate_variant("c3_4h_veto", direction="SHORT", bars_4h=FLAT, bars_1d=None) == (True, "")
    assert evaluate_variant("c3_4h_veto", direction="SHORT", bars_4h=[], bars_1d=None) == (True, "")
    assert evaluate_variant("c3_4h_veto", direction="", bars_4h=BULL, bars_1d=None) == (False, "C3_SIDE")


def test_c4_reads_only_the_daily_frame():
    assert evaluate_variant("c4_1d", direction="LONG", bars_4h=BULL, bars_1d=None) == (False, "C4_MISSING_BARS")
    assert evaluate_variant("c4_1d", direction="LONG", bars_4h=None, bars_1d=BULL) == (True, "")


def test_unknown_variant_raises():
    with pytest.raises(ValueError):
        evaluate_variant("c9", direction="LONG", bars_4h=BULL, bars_1d=None)


# ------------------------------------------------------------------ 3) karar kaydi
def test_record_blocks_only_in_enforce_and_keeps_all_shadows():
    r = candle_confirmation(mode="ENFORCE", variant="c3_4h_veto", direction="SHORT", bars_4h=BULL, bars_1d=None)
    assert r["blocks"] and r["verdict"] == {"ok": False, "reason": "C3_OPPOSITE_PATTERN"}
    assert set(r["shadow"]) == set(VARIANTS)
    s = candle_confirmation(mode="SHADOW", variant="c3_4h_veto", direction="SHORT", bars_4h=BULL, bars_1d=None)
    assert not s["blocks"] and s["verdict"]["ok"] is False
    o = candle_confirmation(mode="OFF", variant="c3_4h_veto", direction="SHORT", bars_4h=BULL, bars_1d=None)
    assert not o["blocks"] and o["shadow"] == {}


def test_unclosed_bar_is_excluded():
    now = 3 * H4          # ucuncu bar (ts=2*H4) tam simdi kapaniyor → dahil; ts=3*H4 acik
    rows = BULL + [_bar(3 * H4, 103, 110, 90, 95)]
    assert closed_bars(rows, now_ms=now, tf="4h") == BULL
    assert closed_bars(rows, now_ms=now - 1, tf="4h") == BULL[:-1]


# ------------------------------------------------------------------ 4) config
def _cfg(mode="PAPER", cc_mode="ENFORCE", variant="c3_4h_veto"):
    raw = {"mode": mode, "entry_selectivity": {"candle_confirmation_mode": cc_mode,
                                              "candle_confirmation_variant": variant}}
    cfg = load_v3(raw)
    validate_v3(cfg)
    return cfg


def test_config_accepts_enforce_in_paper_and_normalizes_mode():
    cfg = _cfg(cc_mode="enforce")
    assert cfg.entry_selectivity.candle_confirmation_mode == "ENFORCE"
    assert cfg.entry_selectivity.candle_confirmation_variant in VARIANTS
    assert set(MODES) == {"OFF", "SHADOW", "ENFORCE"}


def test_config_rejects_unknown_mode_or_variant():
    with pytest.raises(ConfigError):
        _cfg(cc_mode="ACTIVE")
    with pytest.raises(ConfigError):
        _cfg(variant="c9")


@pytest.mark.parametrize("live", ["LIVE", "LIVE_LIMITED"])
def test_config_refuses_enforce_with_real_money(live):
    # LIVE modlar bu surumde daha erken bir kapida zaten reddedilir; kural o kapidan
    # bagimsiz olarak SAF fonksiyonda sinanir (savunma derinligi).
    with pytest.raises(ConfigError):
        _cfg(mode=live, cc_mode="ENFORCE")
    with pytest.raises(ValueError, match="NOT_VALIDATED_FOR_LIVE"):
        validate_settings(mode="ENFORCE", variant="c3_4h_veto", app_mode=live)
    assert validate_settings(mode="shadow", variant="c3_4h_veto", app_mode=live) == "SHADOW"
    assert validate_settings(mode="ENFORCE", variant="c3_4h_veto", app_mode="PAPER") == "ENFORCE"


def test_validate_v3_uses_the_shared_validator():
    src = (ROOT / "config_v3.py").read_text(encoding="utf-8")
    assert "from .candle_confirmation import validate_settings" in src, \
        "config_v3 kendi mum onayi dogrulamasini tutuyor"
    assert "_CC_MODES" not in src and "NOT_VALIDATED_FOR_LIVE" not in src


def test_default_is_off_so_old_configs_behave_identically():
    cfg = load_v3({"mode": "PAPER"})
    validate_v3(cfg)
    assert cfg.entry_selectivity.candle_confirmation_mode == "OFF"
