"""ÖĞRENME MODU L1 — CONFIG (2026-09-28, öğrenme modu).

`learning_mode` bölümü: kod varsayılanı KAPALI; açıkken yalnız PAPER + paper gateway + testnet kapalı + PAPER_RESEARCH;
sınırlar, bilinmeyen anahtarlar ve ezme anahtarları ConfigError; env `TRADINGBOT_LEARNING_MODE=off` yalnız kapatır.
"""
from __future__ import annotations

import copy
import dataclasses

import pytest

from tradingbot.config_v3 import V3Config, load_v3, validate_v3
from tradingbot.core import ConfigError
from tradingbot.learning_mode import BookLearningCfg, LearningMode

#: CONTRACT.md'deki L1 değerleri (stage C config.yaml'a bunu yazar).
L1 = {
    "enabled": True,
    "risk_per_trade_pct": 0.5,
    "max_total_open_risk_pct": 100,
    "margin_reserve_pct": 5,
    "liq_buffer_mult": 2.0,
    "min_notional_bump": True,
    "counterfactual": True,
    "counterfactual_max_pending": 2000,
    "books": {
        "main": {"enabled": True, "slots": 20},
        "t2_trend_regime": {"enabled": True, "slots": 40, "leverage_max": 4},
        "m2_tsmom28": {"enabled": True, "slots": 40, "leverage_max": 4},
        "b1_box_fade": {"enabled": True, "slots": 20, "leverage_max": 4, "min_stop_pct": 0.32},
        "d4_donchian_20_10": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
        "c4_candle_variations": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
        "c4s_candle_variations_strict": {"enabled": False},
        "pattern_trader": {"enabled": True, "slots": 30, "leverage_max": 3},
    },
    "strategy_overrides": {
        "regime_gate_shadow": True,
        "candle_veto_shadow": True,
        "structures_entry_shadow": ["main", "t2_trend_regime", "m2_tsmom28"],
        "economics_exploration": True,
        "leverage_confidence_fallback": True,
    },
}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("TRADINGBOT_LEARNING_MODE", raising=False)
    monkeypatch.delenv("TRADINGBOT_LEARNING_INFLUENCE_MODE", raising=False)


def _raw(lm: dict | None = None, **top) -> dict:
    raw = {"mode": "PAPER"}
    if lm is not None:
        raw["learning_mode"] = lm
    raw.update(top)
    return raw


def _l1(**over) -> dict:
    d = copy.deepcopy(L1)
    d.update(over)
    return d


# ============================================================================ varsayılan / KAPALI
def test_code_default_is_disabled_and_section_is_registered():
    cfg = load_v3({"mode": "PAPER"})
    assert cfg.learning_mode.enabled is False
    assert cfg.learning_mode.books == {} and cfg.learning_mode.strategy_overrides == {}
    assert "learning_mode" in {f.name for f in dataclasses.fields(V3Config)}
    assert not any("learning_mode" in w for w in cfg.warnings)
    lm = LearningMode.from_config(cfg, lambda: (True, "OK"))
    assert lm.refresh() is False and lm.book("main") is None


def test_l1_contract_config_loads_in_paper():
    cfg = load_v3(_raw(_l1()))
    sec = cfg.learning_mode
    assert sec.enabled is True and sec.risk_per_trade_pct == 0.5 and sec.counterfactual_max_pending == 2000
    assert all(isinstance(v, BookLearningCfg) for v in sec.books.values())
    assert sec.books["b1_box_fade"] == BookLearningCfg(True, 20, 4, 0.32, None)
    assert sec.books["d4_donchian_20_10"].symbols == "universe"
    assert sec.books["c4s_candle_variations_strict"].enabled is False
    assert sec.strategy_overrides["structures_entry_shadow"] == ["main", "t2_trend_regime", "m2_tsmom28"]
    validate_v3(cfg)                                       # idempotent: normalize edilmiş bölüm yeniden geçer
    lm = LearningMode.from_config(cfg, lambda: (True, "OK"))
    assert lm.refresh() is True
    assert lm.book("m2_tsmom28").slots == 40 and lm.book("c4s_candle_variations_strict") is None
    assert lm.override("economics_exploration", False) is True


def test_disabled_section_is_accepted_outside_paper():
    """Bölüm KAPALIYKEN PAPER-dışı modda da config yüklenir (TESTNET profil kaldıraç tavanı 2 bile olsa)."""
    for extra in ({"mode": "TESTNET", "risk_profiles": {"profile": "TESTNET"}},
                  {"execution": {"gateway": "binance_futures_testnet", "testnet_enabled": True}},
                  {"mode": "OBSERVE"}):
        raw = _raw(_l1(enabled=False))
        raw.update(extra)
        cfg = load_v3(raw)
        assert cfg.learning_mode.enabled is False


# ============================================================================ YALNIZ PAPER
@pytest.mark.parametrize("mode", ["TESTNET", "OBSERVE", "SHADOW_LIVE", "LIVE_LIMITED", "LIVE"])
def test_enabled_outside_paper_mode_is_a_config_error(mode):
    with pytest.raises(ConfigError):
        load_v3(_raw(_l1(), mode=mode))


@pytest.mark.parametrize("extra", [
    {"execution": {"gateway": "binance_futures_testnet"}},
    {"execution": {"gateway": "binance_spot_testnet"}},
    {"execution": {"testnet_enabled": True}},
    {"risk_profiles": {"profile": "TESTNET"}},
    {"risk_profiles": {"profile": "SHADOW_LIVE"}},
])
def test_enabled_needs_paper_gateway_no_testnet_and_paper_research(extra):
    raw = _raw(_l1())
    raw.update(extra)
    with pytest.raises(ConfigError, match="LEARNING_MODE_PAPER_ONLY"):
        load_v3(raw)


# ============================================================================ bilinmeyen anahtarlar
@pytest.mark.parametrize("lm", [
    {"enabled": False, "enable": True},                                   # yazım hatası UYARI değil HATA
    {"enabled": False, "books": {"t3_nope": {"enabled": True}}},
    {"enabled": False, "books": {"main": {"enabled": True, "slot": 20}}},
    {"enabled": False, "strategy_overrides": {"regime_gate_off": True}},
    {"enabled": False, "strategy_overrides": {"structures_entry_shadow": ["main", "nope"]}},
    {"enabled": False, "strategy_overrides": {"structures_entry_shadow": ["main", "main"]}},
    {"enabled": False, "strategy_overrides": {"structures_entry_shadow": "main"}},
    {"enabled": False, "strategy_overrides": {"regime_gate_shadow": "yes"}},
    {"enabled": False, "books": ["main"]},
    {"enabled": False, "books": {"main": 20}},
])
def test_unknown_or_malformed_keys_are_config_errors(lm):
    with pytest.raises(ConfigError):
        load_v3(_raw(lm))


def test_non_dict_section_is_a_config_error():
    for bad in ("yes", True, 1, ["enabled"]):
        with pytest.raises(ConfigError):
            load_v3(_raw(bad))


# ============================================================================ sınırlar
@pytest.mark.parametrize("key,value", [
    ("risk_per_trade_pct", 0), ("risk_per_trade_pct", -0.5), ("risk_per_trade_pct", 2.01), ("risk_per_trade_pct", "0.5"),
    ("risk_per_trade_pct", float("nan")),
    ("max_total_open_risk_pct", 0), ("max_total_open_risk_pct", 101),
    ("margin_reserve_pct", -1), ("margin_reserve_pct", 60),
    ("liq_buffer_mult", 1.49), ("liq_buffer_mult", float("inf")),
    ("counterfactual_max_pending", 0), ("counterfactual_max_pending", 2001), ("counterfactual_max_pending", 10.5),
    ("enabled", "true"), ("min_notional_bump", 1), ("counterfactual", None),
])
def test_top_level_bounds(key, value):
    with pytest.raises(ConfigError):
        load_v3(_raw(_l1(**{key: value})))


@pytest.mark.parametrize("value", [
    {"enabled": True, "slots": 0}, {"enabled": True, "slots": 201}, {"enabled": True, "slots": "20"},
    {"enabled": True, "slots": True}, {"enabled": True, "leverage_max": 0}, {"enabled": True, "leverage_max": 6},
    {"enabled": True, "min_stop_pct": -0.1}, {"enabled": True, "symbols": "all"}, {"enabled": "yes"},
])
def test_book_bounds(value):
    lm = _l1()
    lm["books"]["t2_trend_regime"] = value
    with pytest.raises(ConfigError):
        load_v3(_raw(lm))


def test_bounds_edges_are_accepted():
    lm = _l1(risk_per_trade_pct=2.0, max_total_open_risk_pct=100, margin_reserve_pct=0, liq_buffer_mult=1.5,
             counterfactual_max_pending=1)
    lm["books"]["main"] = {"enabled": True, "slots": 200, "leverage_max": 5, "min_stop_pct": 0}
    lm["books"]["t2_trend_regime"] = {"enabled": True, "slots": 1, "leverage_max": 1}
    cfg = load_v3(_raw(lm))
    assert cfg.learning_mode.books["main"].slots == 200


def test_leverage_bound_follows_the_resolved_profile_when_enabled():
    """Açıkken `leverage_max ≤ min(5, profil futures_max_leverage)`; profil ezmesi 3 ise 4 reddedilir."""
    raw = _raw(_l1(), risk_profiles={"profile": "PAPER_RESEARCH", "overrides": {"futures_max_leverage": 3}})
    with pytest.raises(ConfigError, match="leverage_max"):
        load_v3(raw)
    raw["learning_mode"]["enabled"] = False                  # kapalıyken yalnız mutlak 5 sınırı
    assert load_v3(raw).learning_mode.enabled is False


# ============================================================================ env yalnız kapatır
@pytest.mark.parametrize("val", ["off", "OFF", "false", "0", "disabled"])
def test_env_can_turn_it_off(monkeypatch, val):
    monkeypatch.setenv("TRADINGBOT_LEARNING_MODE", val)
    cfg = load_v3(_raw(_l1()))
    assert cfg.learning_mode.enabled is False
    assert LearningMode.from_config(cfg, lambda: (True, "OK")).refresh() is False


def test_env_off_also_lets_a_non_paper_config_start(monkeypatch):
    monkeypatch.setenv("TRADINGBOT_LEARNING_MODE", "off")
    assert load_v3(_raw(_l1(), mode="TESTNET")).learning_mode.enabled is False


@pytest.mark.parametrize("val", ["on", "true", "1", "enabled", "of"])
def test_env_cannot_turn_it_on(monkeypatch, val):
    monkeypatch.setenv("TRADINGBOT_LEARNING_MODE", val)
    with pytest.raises(ConfigError, match="TRADINGBOT_LEARNING_MODE"):
        load_v3(_raw(_l1(enabled=False)))
    with pytest.raises(ConfigError):
        load_v3({"mode": "PAPER"})


def test_env_off_without_section_is_harmless(monkeypatch):
    monkeypatch.setenv("TRADINGBOT_LEARNING_MODE", "off")
    assert load_v3({"mode": "PAPER"}).learning_mode.enabled is False


# ============================================================================ KAPALI → diğer bölümler aynen
def test_other_sections_are_unchanged_by_the_new_section():
    base = load_v3({"mode": "PAPER"})
    with_off = load_v3(_raw(_l1(enabled=False)))
    a, b = dataclasses.asdict(base), dataclasses.asdict(with_off)
    a.pop("learning_mode")
    b.pop("learning_mode")
    assert a == b
