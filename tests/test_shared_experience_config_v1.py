# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — YAPILANDIRMA + MOTOR KANCASI (2026-09-29; SPEC_V1 §9-§10, görev X3).

* Kod varsayılanı KAPALI (`enabled: false`, `mode: OFF`); `config.yaml` bölümü tam olarak `{enabled: true, mode: RECORD, advisor_mode: RECORD}` (danışman 2026-09-30).
* Mod yalnız OFF | RECORD (ADVISE/ENFORCE → ConfigError SHARED_EXPERIENCE_MODE_NOT_IMPLEMENTED); bilinmeyen anahtar,
  tip/aralık hatası ve yol kaçışı (`state_dir`) ConfigError.
* Env `TRADINGBOT_SHARED_EXPERIENCE` yalnız KAPATABİLİR; başka her değer ConfigError.
* YALNIZ PAPER: config PAPER dışındaysa uyarı; toplayıcı çalışma anında `SUSPENDED:<mod>` olur ve satır yazmaz.
* Kanca: kapalıyken paket İÇE AKTARILMAZ (alt süreçte `sys.modules` denetimi); açıkken tembel kurulum, üç başarısız
  kurulumdan sonra süreç boyunca yeniden denenmez; arıza turu durdurmaz.
"""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.config import load_config  # noqa: E402
from tradingbot.config_v3 import SharedExperienceSection, load_v3  # noqa: E402
from tradingbot.core import ConfigError  # noqa: E402

ON = {"enabled": True, "mode": "RECORD"}


def _load(sec: dict | None, **extra):
    raw = dict(extra)
    if sec is not None:
        raw["shared_experience"] = copy.deepcopy(sec)
    return load_v3(raw)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("TRADINGBOT_SHARED_EXPERIENCE", raising=False)
    monkeypatch.delenv("TRADINGBOT_LEARNING_MODE", raising=False)


# ============================================================================ varsayılan + config.yaml
def test_code_default_is_off_and_absent_section_is_inactive():
    sec = SharedExperienceSection()
    assert sec.enabled is False and sec.mode == "OFF" and sec.active is False
    assert (sec.state_dir, sec.hot_max_lines, sec.max_total_mb, sec.tour_budget_s, sec.max_rows_per_tour) == \
        ("shared_experience", 5000, 1024, 2.0, 600)
    assert (sec.backfill, sec.cache_entries, sec.pending_max_age_h, sec.lock_timeout_s, sec.lazy_fetch_max_per_tour) == \
        (True, 2048, 48.0, 0.2, 0)
    assert _load(None).shared_experience.active is False
    assert _load({"enabled": True}).shared_experience.active is False, "mod varsayılanı OFF: iki anahtar da gerekir"
    assert _load({"mode": "RECORD"}).shared_experience.active is False


def test_config_yaml_section_is_exactly_enabled_record_and_validates():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    # 2026-09-30: gölge danışman kayıtta (DANISMAN_V1 §7; yalnız KAYIT, karar değişmez)
    assert raw["shared_experience"] == {"enabled": True, "mode": "RECORD", "advisor_mode": "RECORD"}
    cfg = load_config(ROOT / "config.yaml")
    xp = cfg.v3.shared_experience
    assert xp.active and xp.mode == "RECORD" and xp.lazy_fetch_max_per_tour == 0
    assert not any("shared_experience" in w for w in cfg.v3.warnings)
    txt = (ROOT / "config.yaml").read_text(encoding="utf-8")
    block = txt[txt.index("# ---- ORTAK DENEYİM KATMANI"):txt.index("shared_experience:\n")]
    for needle in ("YALNIZ KAYIT", "YALNIZ PAPER", "KARAR DEĞİŞMEZ", "TRADINGBOT_SHARED_EXPERIENCE=off", "enabled: false",
                   "(2026-09-29)"):
        assert needle in block, needle


# ============================================================================ doğrulama
@pytest.mark.parametrize("mode", ["ADVISE", "ENFORCE", "SHADOW", "", "ON"])
def test_only_off_and_record_modes_exist(mode):
    with pytest.raises(ConfigError, match="SHARED_EXPERIENCE_MODE_NOT_IMPLEMENTED"):
        _load({"enabled": True, "mode": mode})


def test_mode_is_normalised_and_non_string_mode_is_refused():
    assert _load({"enabled": True, "mode": " record "}).shared_experience.mode == "RECORD"
    assert _load({"enabled": False, "mode": "off"}).shared_experience.mode == "OFF"
    with pytest.raises(ConfigError, match="metin"):
        _load({"enabled": True, "mode": 1})
    with pytest.raises(ConfigError, match="metin"):
        _load({"enabled": True, "mode": True})                       # YAML `mode: on` belirsiz → açılmaz


def test_yaml_bare_off_is_the_documented_off_switch_not_a_startup_crash():
    """PyYAML çıplak `OFF`/`off` yazımını bool False okur; config.yaml yorumundaki kapatma yolu (`mode: OFF`) başlatmayı
    çökertmemeli ve katmanı kapatmalı."""
    txt = (ROOT / "config.yaml").read_text(encoding="utf-8").replace("shared_experience:\n  enabled: true\n  mode: RECORD",
                                                                     "shared_experience:\n  enabled: true\n  mode: OFF")
    raw = yaml.safe_load(txt)
    assert raw["shared_experience"]["mode"] is False
    xp = load_v3(raw).shared_experience
    assert xp.mode == "OFF" and not xp.active


def test_unknown_key_and_non_mapping_section_are_config_errors():
    with pytest.raises(ConfigError, match="bilinmeyen anahtar"):
        _load({"enabled": True, "mode": "RECORD", "enable": True})
    with pytest.raises(ConfigError, match="sözlük"):
        load_v3({"shared_experience": "RECORD"})
    with pytest.raises(ConfigError, match="enabled true/false"):
        _load({"enabled": "yes", "mode": "RECORD"})
    with pytest.raises(ConfigError, match="backfill true/false"):
        _load({"enabled": True, "mode": "RECORD", "backfill": 1})


@pytest.mark.parametrize("key,bad", [
    ("hot_max_lines", 499), ("hot_max_lines", 50001), ("hot_max_lines", True), ("hot_max_lines", 5000.0),
    ("archive_max_segments", -1), ("max_total_mb", 63), ("max_total_mb", 10241), ("max_rows_per_tour", 9),
    ("max_rows_per_tour", 5001), ("cache_entries", 63), ("cache_entries", 10001), ("lazy_fetch_max_per_tour", 21),
    ("lazy_fetch_max_per_tour", -1), ("tour_budget_s", 0.05), ("tour_budget_s", 11), ("tour_budget_s", float("nan")),
    ("pending_max_age_h", 0.5), ("pending_max_age_h", 241), ("lock_timeout_s", -0.1), ("lock_timeout_s", 2.5),
    ("lock_timeout_s", False)])
def test_numeric_ranges_and_types_are_enforced(key, bad):
    with pytest.raises(ConfigError, match="shared_experience.%s" % key):
        _load(dict(ON, **{key: bad}))


def test_numeric_edges_are_accepted():
    c = _load(dict(ON, hot_max_lines=500, max_total_mb=64, tour_budget_s=0.1, max_rows_per_tour=10, cache_entries=64,
                   pending_max_age_h=1, lock_timeout_s=0, lazy_fetch_max_per_tour=20)).shared_experience
    assert c.active and c.lock_timeout_s == 0 and c.lazy_fetch_max_per_tour == 20


@pytest.mark.parametrize("bad", ["../state", "a/b", "a\\b", "..", "", " xp", "C:x", "."])
def test_state_dir_must_be_a_plain_name_under_the_state_root(bad):
    with pytest.raises(ConfigError, match="state_dir"):
        _load(dict(ON, state_dir=bad))


# ============================================================================ env: yalnız kapatır
@pytest.mark.parametrize("val", ["off", "OFF", "false", "0", "disabled", " off "])
def test_env_off_switch_disables(monkeypatch, val):
    monkeypatch.setenv("TRADINGBOT_SHARED_EXPERIENCE", val)
    xp = _load(ON).shared_experience
    assert xp.enabled is False and xp.mode == "OFF" and not xp.active
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert not load_v3(raw).shared_experience.active


@pytest.mark.parametrize("val", ["on", "1", "true", "record", "RECORD", "enabled"])
def test_env_cannot_enable_and_any_other_value_fails_closed(monkeypatch, val):
    monkeypatch.setenv("TRADINGBOT_SHARED_EXPERIENCE", val)
    with pytest.raises(ConfigError, match="TRADINGBOT_SHARED_EXPERIENCE"):
        _load(None)


# ============================================================================ yalnız PAPER
def test_non_paper_config_is_a_warning_and_the_collector_suspends_at_runtime(tmp_path):
    c = _load(ON, mode={"mode": "TESTNET"})
    assert c.shared_experience.active and any("YALNIZ PAPER" in w and "SUSPENDED:TESTNET" in w for w in c.warnings)
    assert not any("YALNIZ PAPER" in w for w in _load({"enabled": False, "mode": "RECORD"}, mode={"mode": "TESTNET"}).warnings)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_shared_experience_collector_v1 as TC
    eng = TC.fake_engine(tmp_path, mode="TESTNET")
    TC.open_pos(eng.ledger2, "SOL/USDT", at=TC.T0)
    xp = TC.collector(eng)
    assert TC.step(xp, eng, TC.T0)["state"] == "SUSPENDED:TESTNET" and TC.rows_of(eng) == []


# ============================================================================ karar kimliği (config_hash)
def test_engine_config_hash_ignores_the_record_only_section():
    """(2026-09-29) Motorun `config_hash()`ı karar günlüğü satırlarına girer. Yalnız-KAYIT bölümü özete GİRMEZ: bölüm
    YOK / OFF / RECORD / farklı bütçeler aynı özeti verir ve özet bölüm eklenmeden önceki alan kümesinin (HEAD) özetine
    eşittir — katman açılınca ya da kapanınca karar günlüğü satırları değişmez."""
    from dataclasses import asdict

    import tradingbot.engine_v3 as E
    from tradingbot.core import payload_hash

    def h(v3):
        return E.TradingEngineV3.config_hash(SimpleNamespace(cfg=SimpleNamespace(v3=v3)))
    absent, off, rec = _load(None), _load({"enabled": False, "mode": "OFF"}), _load(ON)
    other = _load(dict(ON, max_rows_per_tour=50, tour_budget_s=0.5, state_dir="xp2"))
    head_fields = {k: v for k, v in asdict(absent).items() if k != "shared_experience"}   # HEAD'in V3Config alanları
    head_fields["learning_mode"].pop("extra_entries")   # 2026-10-03: kod varsayılanı `open` karar kimliğine girmez
    assert h(absent) == h(off) == h(rec) == h(other) == payload_hash(head_fields)
    assert h(_load(None, mode={"mode": "OBSERVE"})) != h(absent), "karar ayarı değişince özet DEĞİŞİR (boş özet değil)"


# ============================================================================ motor kancası
def _hook_probe(sec_code: str) -> str:
    code = ("import sys\nfrom types import SimpleNamespace\nfrom tradingbot.config_v3 import load_v3\n"
            "import tradingbot.engine_v3 as E\n"
            "v3 = load_v3(%s)\n"
            "stub = SimpleNamespace(cfg=SimpleNamespace(v3=v3))\n"
            "E.TradingEngineV3._shared_experience_step(stub, [], {}, [], None)\n"
            "print(sorted(m for m in sys.modules if m.startswith('tradingbot.shared_experience')))\n") % sec_code
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=120,
                       env={k: v for k, v in __import__("os").environ.items() if not k.startswith("TRADINGBOT_")})
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout.strip().splitlines()[-1]


@pytest.mark.parametrize("sec", ["{}", "{'shared_experience': {'enabled': False, 'mode': 'RECORD'}}",
                                 "{'shared_experience': {'enabled': True, 'mode': 'OFF'}}"],
                         ids=["absent", "disabled", "mode_off"])
def test_off_hook_does_not_even_import_the_package(sec):
    assert _hook_probe(sec) == "[]", "kapalı yol: paket (ve numpy'lı anlık görüntü modülü) yüklenmez"


def test_hook_builds_the_collector_lazily_and_stops_after_three_failed_constructions(monkeypatch):
    import tradingbot.engine_v3 as E
    from tradingbot.shared_experience import collector as XC
    calls = {"n": 0}

    def bad(cls, eng):
        calls["n"] += 1
        raise OSError("state salt okunur")
    monkeypatch.setattr(XC.SharedExperienceCollector, "from_engine", classmethod(bad))
    stub = SimpleNamespace(cfg=SimpleNamespace(v3=_load(ON)))
    for _ in range(5):
        E.TradingEngineV3._shared_experience_step(stub, [], {}, [], None)       # istisna DIŞARI ÇIKMAZ
    assert calls["n"] == 3 and stub.__dict__["_shared_xp"] is False and stub.__dict__["_shared_xp_fail"] == 3
    good = SimpleNamespace(cfg=SimpleNamespace(v3=_load(ON)))
    seen = []

    class _X:
        def step(self, eng, **kw):
            seen.append(sorted(kw))
    monkeypatch.setattr(XC.SharedExperienceCollector, "from_engine", classmethod(lambda cls, eng: _X()))
    E.TradingEngineV3._shared_experience_step(good, [{"x": 1}], {}, [], None)
    E.TradingEngineV3._shared_experience_step(good, [], {}, [], None)
    assert isinstance(good.__dict__["_shared_xp"], _X) and seen == [["briefs", "decisions", "now", "risk_log"]] * 2
