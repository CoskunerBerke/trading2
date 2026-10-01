"""Quant Evaluation V1 — config bölümü testleri.

Kapsam: güvenli varsayılanlar (her şey kapalı/read-only), auto_promotion fail-closed,
bilinmeyen anahtar uyarısı, mevcut config'lerin (bölümsüz) geriye uyumlu yüklenmesi.
"""
from __future__ import annotations

import pytest

from tradingbot.core import ConfigError
from tradingbot.config_v3 import QuantEvalSection, load_v3


def _base_raw(**quant):
    raw = {"mode": "PAPER"}
    if quant:
        raw["quant_eval"] = quant
    return raw


def test_safe_defaults_all_off_or_readonly():
    cfg = load_v3(_base_raw())
    q = cfg.quant_eval
    assert q.journal_enabled is False
    assert q.attribution_enabled is False
    assert q.walk_forward_enabled is False
    assert q.risk_v2_advisory is False
    assert q.challenger_shadow is False
    assert q.auto_promotion is False
    assert q.replay_cost_manifest is True      # yalnız metadata — güvenli
    assert q.dashboard_view is True            # salt okunur görünüm — güvenli


def test_auto_promotion_fails_closed():
    with pytest.raises(ConfigError, match="QUANT_AUTO_PROMOTION_FORBIDDEN"):
        load_v3(_base_raw(auto_promotion=True))


def test_unknown_key_warned_not_silent():
    cfg = load_v3(_base_raw(bilinmeyen_anahtar=1))
    assert any("quant_eval" in w and "bilinmeyen" in w for w in cfg.warnings)


def test_backward_compatible_without_section():
    cfg = load_v3({"mode": "PAPER"})           # eski config'te bölüm hiç yok
    assert isinstance(cfg.quant_eval, QuantEvalSection)
    assert cfg.quant_eval.auto_promotion is False


def test_flags_can_be_enabled_individually_in_paper():
    cfg = load_v3(_base_raw(journal_enabled=True, attribution_enabled=True,
                            walk_forward_enabled=True, risk_v2_advisory=True,
                            challenger_shadow=True))
    assert cfg.quant_eval.risk_v2_advisory is True
    assert cfg.quant_eval.auto_promotion is False
    assert cfg.mode.mode == "PAPER"
