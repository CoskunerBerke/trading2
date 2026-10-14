# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — YAPILANDIRMA (2026-09-30; SPEC_ADVISOR_V1 §6.6, §7 T-A9; ön kayıt DANISMAN_V1.md §1).

* Kod varsayılanı KAPALI (`advisor_mode: OFF`); bugünkü `config.yaml` DEĞİŞMEDEN ayrıştırılır ve danışman kapalıdır
  (canlıya alma ayrı bir onay/commit: bölüm o zaman `{enabled: true, mode: RECORD, advisor_mode: RECORD}` olur).
* Mod yalnız OFF | RECORD: ADVISE / ENFORCE / bilinmeyen → ConfigError `SHARED_EXPERIENCE_ADVISOR_MODE_NOT_IMPLEMENTED`;
  metin olmayan mod RED; YAML'ın çıplak `OFF`u (bool False) → OFF. Tamsayı alanları aralık + tip (bool/float RED).
* Env `TRADINGBOT_SHARED_EXPERIENCE_ADVISOR` yalnız KAPATIR; başka her değer ConfigError. Katman env'i
  (`TRADINGBOT_SHARED_EXPERIENCE=off`) danışmanı da kapatır.
* Danışman ancak katman etkinken (enabled + RECORD) ve `advisor_mode: RECORD` iken çalışır.
* Motorun `config_hash()`ı danışman alanlarından ETKİLENMEZ (bölüm özete hiç girmez).
"""
from __future__ import annotations

import copy
import logging
import sys
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.config import load_config  # noqa: E402
from tradingbot.config_v3 import SHARED_EXPERIENCE_ADVISOR_MODES, SharedExperienceSection, load_v3  # noqa: E402
from tradingbot.core import ConfigError  # noqa: E402

ON = {"enabled": True, "mode": "RECORD"}
ADV = dict(ON, advisor_mode="RECORD")
ADV_FIELDS = {"advisor_mode": "OFF", "advisor_budget_ms": 250, "advisor_catch_up_budget_ms": 1000,
              "advisor_rebuild_rows_per_step": 5000, "advisor_snapshot_every_steps": 60, "advisor_max_index_mb": 96,
              "advice_hot_max_lines": 2000, "advice_max_total_mb": 256}
ENV = "TRADINGBOT_SHARED_EXPERIENCE_ADVISOR"


def _load(sec: dict | None, **extra):
    raw = dict(extra)
    if sec is not None:
        raw["shared_experience"] = copy.deepcopy(sec)
    return load_v3(raw)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in ("TRADINGBOT_SHARED_EXPERIENCE", ENV, "TRADINGBOT_LEARNING_MODE"):
        monkeypatch.delenv(k, raising=False)


# ============================================================================ varsayılanlar + config.yaml
def test_code_defaults_are_off_and_the_documented_values():
    sec = SharedExperienceSection()
    assert {k: getattr(sec, k) for k in ADV_FIELDS} == ADV_FIELDS
    assert sec.advisor_active is False
    assert SHARED_EXPERIENCE_ADVISOR_MODES == ("OFF", "RECORD")
    # toplayıcının ayar kopyası AYNI varsayılanları taşır ve bölümden alanı alanına kopyalanır
    from tradingbot.shared_experience.collector import XpSettings
    xs = XpSettings()
    assert {k: getattr(xs, k) for k in ADV_FIELDS} == ADV_FIELDS
    sec2 = _load(dict(ADV, advisor_budget_ms=400, advisor_rebuild_rows_per_step=800, advisor_snapshot_every_steps=7,
                      advisor_max_index_mb=32, advice_hot_max_lines=600, advice_max_total_mb=64)).shared_experience
    xs2 = XpSettings.from_section(sec2)
    assert {f.name: getattr(xs2, f.name) for f in fields(XpSettings)} == {f.name: getattr(sec2, f.name)
                                                                          for f in fields(XpSettings)}
    assert xs2.advisor_mode == "RECORD" and xs2.advisor_budget_ms == 400


def test_current_config_yaml_records_advice_and_off_still_parses():
    """2026-09-30: canlıya alma (DANISMAN_V1 §7) — config.yaml danışmanı KAYITTA açar; başka advisor_* anahtarı yok
    (işletim ayarları kod varsayılanında). Kapatma yolu (`advisor_mode: OFF`) aynı bölümle doğrulanır."""
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["shared_experience"] == {"enabled": True, "mode": "RECORD", "advisor_mode": "RECORD"}
    xp = load_config(ROOT / "config.yaml").v3.shared_experience
    assert xp.active and xp.advisor_mode == "RECORD" and xp.advisor_active is True
    # geri alma: aynı bölüm advisor_mode OFF ile → danışman kurulmaz, toplayıcı aynen sürer
    xp2 = load_v3({**raw, "shared_experience": dict(raw["shared_experience"], advisor_mode="OFF")}).shared_experience
    assert xp2.active and xp2.advisor_active is False and xp2.advisor_mode == "OFF"


@pytest.mark.parametrize("sec,active", [
    (dict(ADV), True), (dict(ON), False), (dict(ON, advisor_mode="OFF"), False),
    ({"enabled": False, "mode": "RECORD", "advisor_mode": "RECORD"}, False),
    ({"enabled": True, "mode": "OFF", "advisor_mode": "RECORD"}, False)])
def test_advisor_runs_only_when_the_layer_is_active_and_advisor_mode_is_record(sec, active):
    assert _load(sec).shared_experience.advisor_active is active


# ============================================================================ doğrulama
@pytest.mark.parametrize("mode", ["ADVISE", "ENFORCE", "advise", "SHADOW", "ON", "", "LIVE"])
def test_only_off_and_record_advisor_modes_exist(mode):
    with pytest.raises(ConfigError, match="SHARED_EXPERIENCE_ADVISOR_MODE_NOT_IMPLEMENTED"):
        _load(dict(ON, advisor_mode=mode))


def test_advisor_mode_is_normalised_and_non_string_is_refused():
    assert _load(dict(ON, advisor_mode=" record ")).shared_experience.advisor_mode == "RECORD"
    assert _load(dict(ON, advisor_mode="off")).shared_experience.advisor_mode == "OFF"
    for bad in (1, True, ["RECORD"]):
        with pytest.raises(ConfigError, match="advisor_mode metin"):
            _load(dict(ON, advisor_mode=bad))


def test_yaml_bare_off_is_off_not_a_startup_crash():
    raw = yaml.safe_load("shared_experience:\n  enabled: true\n  mode: RECORD\n  advisor_mode: OFF\n")
    assert raw["shared_experience"]["advisor_mode"] is False
    xp = load_v3(raw).shared_experience
    assert xp.advisor_mode == "OFF" and xp.active and not xp.advisor_active


@pytest.mark.parametrize("key,bad", [
    ("advisor_budget_ms", 19), ("advisor_budget_ms", 2001), ("advisor_budget_ms", 250.0), ("advisor_budget_ms", True),
    ("advisor_catch_up_budget_ms", 19), ("advisor_catch_up_budget_ms", 5001), ("advisor_catch_up_budget_ms", 1e3),
    ("advisor_rebuild_rows_per_step", 499), ("advisor_rebuild_rows_per_step", 50001),
    ("advisor_snapshot_every_steps", 4), ("advisor_snapshot_every_steps", 1001),
    ("advisor_max_index_mb", 15), ("advisor_max_index_mb", 513), ("advisor_max_index_mb", "96"),
    ("advice_hot_max_lines", 499), ("advice_hot_max_lines", 50001), ("advice_max_total_mb", 31),
    ("advice_max_total_mb", 4097), ("advice_max_total_mb", None)])
def test_advisor_integer_ranges_and_types_are_enforced(key, bad):
    with pytest.raises(ConfigError, match="shared_experience.%s" % key):
        _load(dict(ADV, **{key: bad}))


def test_advisor_integer_edges_are_accepted():
    lo = _load(dict(ADV, advisor_budget_ms=20, advisor_rebuild_rows_per_step=500, advisor_snapshot_every_steps=5,
                    advisor_max_index_mb=16, advice_hot_max_lines=500, advice_max_total_mb=32,
                    advisor_catch_up_budget_ms=20)).shared_experience
    hi = _load(dict(ADV, advisor_budget_ms=2000, advisor_rebuild_rows_per_step=50000, advisor_snapshot_every_steps=1000,
                    advisor_max_index_mb=512, advice_hot_max_lines=50000, advice_max_total_mb=4096,
                    advisor_catch_up_budget_ms=5000)).shared_experience
    assert lo.advisor_active and hi.advisor_active and lo.advisor_budget_ms == 20 and hi.advice_max_total_mb == 4096
    assert lo.advisor_catch_up_budget_ms == 20 and hi.advisor_catch_up_budget_ms == 5000


def test_unknown_advisor_like_key_is_still_refused():
    with pytest.raises(ConfigError, match="bilinmeyen anahtar"):
        _load(dict(ADV, advisor_enforce=True))


# ============================================================================ env: yalnız kapatır
@pytest.mark.parametrize("val", ["off", "OFF", "false", "0", "disabled", " off "])
def test_env_off_switch_turns_only_the_advisor_off(monkeypatch, caplog, val):
    monkeypatch.setenv(ENV, val)
    with caplog.at_level(logging.WARNING):
        xp = _load(ADV).shared_experience
    assert xp.advisor_mode == "OFF" and not xp.advisor_active
    assert xp.active, "katman AÇIK kalır: env yalnız danışmanı kapatır"
    assert any(ENV in r.getMessage() for r in caplog.records), "geçersiz kılma gürültülü olmalı"
    # zaten kapalıysa uyarı gerekmez, sonuç aynı
    assert _load(ON).shared_experience.advisor_mode == "OFF"


@pytest.mark.parametrize("val", ["on", "1", "true", "record", "RECORD", "enabled", "advise"])
def test_env_cannot_enable_and_any_other_value_fails_closed(monkeypatch, val):
    monkeypatch.setenv(ENV, val)
    with pytest.raises(ConfigError, match=ENV):
        _load(None)
    with pytest.raises(ConfigError, match=ENV):
        _load(dict(ADV))


def test_layer_env_off_also_turns_the_advisor_off(monkeypatch):
    monkeypatch.setenv("TRADINGBOT_SHARED_EXPERIENCE", "off")
    xp = _load(ADV).shared_experience
    assert not xp.active and not xp.advisor_active


# ============================================================================ karar kimliği
def test_engine_config_hash_is_unchanged_by_the_advisor_fields():
    from dataclasses import asdict

    import tradingbot.engine_v3 as E
    from tradingbot.core import payload_hash

    def h(v3):
        return E.TradingEngineV3.config_hash(SimpleNamespace(cfg=SimpleNamespace(v3=v3)))
    absent, rec, adv = _load(None), _load(ON), _load(ADV)
    other = _load(dict(ADV, advisor_budget_ms=900, advisor_snapshot_every_steps=500, advice_max_total_mb=4000))
    # M2X (2026-10-05): ayna defterin bölümü de karar kimliğine GİRMEZ (bölümden önceki kodun alan kümesi)
    head_fields = {k: v for k, v in asdict(absent).items() if k not in ("shared_experience", "m2x_aggressive")}
    head_fields["learning_mode"].pop("extra_entries")   # 2026-10-03: kod varsayılanı `open` karar kimliğine girmez
    head_fields["history"].pop("evidence_subprocess")   # 2026-10-05: karar-nötr alt süreç anahtarı karar kimliğine girmez
    head_fields["history"].pop("evidence_fast_knn")     # 2026-10-06: hızlı kNN anahtarı (karar-nötr)
    assert h(absent) == h(rec) == h(adv) == h(other) == payload_hash(head_fields)


# ============================================================================ toplayıcı: OFF → hiçbir şey kurulmaz
def test_collector_builds_the_advisor_only_in_record(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_shared_experience_collector_v1 as TC
    for mode, built in (("OFF", False), ("RECORD", True)):
        eng = TC.fake_engine(tmp_path / mode, advisor_mode=mode)
        xp = TC.collector(eng)
        TC.open_pos(eng.ledger2, "SOL/USDT", at=TC.T0)
        TC.step(xp, eng, TC.T0)
        adv_dir = eng.cfg.state_path / "shared_experience" / "advice"
        assert (xp._advisor is not None) is built and adv_dir.exists() is built
        assert ("advisor" in xp.health()) is built
