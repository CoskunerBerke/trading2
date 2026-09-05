"""Pano: iki deney sürümü AYRI, dürüst ve yasak dilden arınmış gösterilir.

* Tarihsel `pfexp_v1`: SUPERSEDED_INCOMPLETE_ENTRY_INPUT, orijinal başlangıç, orijinal
  sayımlar, F00035'in gerçek P0/P2/P3 katılımı ve P1/P4 çekimserliği, kapsam kusuru,
  kârlılık sonucu YOK.
* Düzeltilmiş `pfexp_v1_1`: yeni kimlik, yeni başlangıç, kararlar, kapsam, metrikler+GA, kapılar,
  düşük örneklem, bütünlük/checksum.
* Gerekli beyanlar aynen; yasak dil (garanti/kesin kâr/en iyi strateji/al-sat talimatı) YOK.
* Eksik/bozuk dosyada 500 yok. API her iki sürümü ayrı uçtan verir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.learn.profitability_experiment import HONESTY_STATEMENTS_TR
from tradingbot.learn.profitability_store import REPORT_FILE
from tests.test_profitability_experiment_v1_1_versioning import (F35_OPENED, V1_START, V11_START,
                                                                  _engine, _pos, seed_v1, v11)
from tests.test_profitability_point_in_time_ae_v1 import snapshot

FORBIDDEN_WORDS = ("garantili", "garanti kâr", "kesin kâr", "en iyi strateji", "guaranteed",
                   "certain profit", "best strategy", "al sinyali", "sat sinyali", "şimdi al",
                   "şimdi sat", "buy now", "sell now")


def _client(tmp_path: Path):
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    sd, dd = tmp_path / "state", tmp_path / "data"
    sd.mkdir(exist_ok=True), dd.mkdir(exist_ok=True)
    (sd / "learning.json").write_text('{"n_trades":0,"lessons":[],"blacklist":[]}',
                                      encoding="utf-8")
    return TestClient(create_app(sd, dd)), sd


def _seed_both(sd: Path) -> None:
    """VPS benzeri yerleşim: v1 dosyaları (F00035) + v1.1 raporu (sıfırdan)."""
    seed_v1(sd)
    eng = _engine(sd, cfg=v11(), positions={"ZEN/USDT": _pos("F00035", F35_OPENED)},
                  history=[], snap=snapshot(symbol="ZEN/USDT"), link_tid="F00035")
    eng._run_profitability_experiment(None)
    assert (sd / "profitability_experiment_v1_1.json").exists()


def _segment(text: str) -> str:
    i = text.find("Kârlılık Deneyi")
    assert i >= 0
    return text[i:]


def test_both_versions_render_separately_with_required_content(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_both(sd)
    r = c.get("/learning")
    assert r.status_code == 200
    seg = _segment(r.text)
    # Düzeltilmiş sürüm
    assert "Düzeltilmiş sürüm — pfexp_v1_1 (pfexp_v1.1.0)" in seg
    assert V11_START[:19] in seg
    assert "ENTRY_SNAPSHOT_POINT_IN_TIME" in seg
    assert "SHADOW PAPER ONLY" in seg
    for probe in ("P0_CHAMPION_MIRROR", "P4_COMBINED", "DEĞERLENDİRİLEMEZ", "Terfi kapıları",
                  "Erken yön göstergesi", "NOT_EVALUABLE_LOW_SAMPLE", "Durum bütünlüğü",
                  "Yabancı kimlik", "checksum", "PRE_EXPERIMENT_OBSERVATION_ONLY", "F00035"):
        assert probe in seg, probe
    # Tarihsel sürüm
    assert "Tarihsel sürüm — pfexp_v1 (pfexp_v1.0.0)" in seg
    assert "SUPERSEDED_INCOMPLETE_ENTRY_INPUT" in seg
    assert V1_START[:19] in seg
    assert "Gerçek katılım (tarihsel, işlem başına)" in seg
    assert "ENTRY_FAMILY_DECISION_UNAVAILABLE" in seg
    assert "Kapsam kusuru" in seg and "P1_SELECTIVE_AE, P4_COMBINED" in seg
    assert "kârlılık sonucu" in seg.lower() and "YOKTUR" in seg
    # Düzeltilmiş blok tarihsel bloktan ÖNCE gelir (ayrı ve sıralı).
    assert seg.index("Düzeltilmiş sürüm") < seg.index("Tarihsel sürüm")


def test_required_statements_present_and_forbidden_language_absent(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_both(sd)
    seg = _segment(c.get("/learning").text)
    for s in HONESTY_STATEMENTS_TR:
        assert s in seg, s
    low = seg.lower()
    for bad in FORBIDDEN_WORDS:
        assert bad not in low, bad


def test_legacy_only_state_renders_without_500_and_marks_it_superseded(tmp_path: Path):
    """Dağıtım öncesi yerleşim: yalnız v1 dosyaları var."""
    c, sd = _client(tmp_path)
    seed_v1(sd)
    r = c.get("/learning")
    assert r.status_code == 200
    seg = _segment(r.text)
    assert "SUPERSEDED_INCOMPLETE_ENTRY_INPUT" in seg
    assert "Düzeltilmiş deney (pfexp_v1_1) raporu henüz yok" in seg
    assert "Tarihsel sürüm — pfexp_v1" in seg
    for s in HONESTY_STATEMENTS_TR:
        assert s in seg


def test_current_only_state_renders_without_500(tmp_path: Path):
    c, sd = _client(tmp_path)
    eng = _engine(sd, cfg=v11(), positions={"ZEN/USDT": _pos("F9", "2026-09-06T13:00:00+00:00")},
                  history=[], snap=snapshot(), link_tid="F9")
    eng._run_profitability_experiment(None)
    assert not (sd / REPORT_FILE).exists()
    r = c.get("/learning")
    assert r.status_code == 200
    seg = _segment(r.text)
    assert "Düzeltilmiş sürüm — pfexp_v1_1" in seg and "Tarihsel sürüm" not in seg


@pytest.mark.parametrize("bad", ["{bozuk", "[]", "null", '{"policies": "x", "policy_version": 3}'])
def test_missing_or_corrupt_files_never_500(tmp_path: Path, bad):
    c, sd = _client(tmp_path)
    assert c.get("/learning").status_code == 200                 # ikisi de YOK
    (sd / "profitability_experiment_v1_1.json").write_text(bad, encoding="utf-8")
    (sd / REPORT_FILE).write_text(bad, encoding="utf-8")
    assert c.get("/learning").status_code == 200


def test_api_serves_both_versions_on_separate_endpoints(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_both(sd)
    new = c.get("/api/state/profitability_experiment_v1_1")
    old = c.get("/api/state/profitability_experiment")
    assert new.status_code == 200 and old.status_code == 200
    assert new.json()["experiment_id"] == "pfexp_v1_1"
    assert new.json()["policy_version"] == "pfexp_v1.1.0"
    assert new.json()["superseded_versions"][0]["status"] == "SUPERSEDED_INCOMPLETE_ENTRY_INPUT"
    assert old.json()["experiment_id"] == "pfexp_v1"
    assert old.json()["policy_version"] == "pfexp_v1.0.0"
    assert old.json()["evaluation_start_at"] == V1_START
    # Eski uçtaki belge, düzeltilmiş çalışma sonrasında da AYNI (yeniden yazılmadı).
    assert json.loads((sd / REPORT_FILE).read_text(encoding="utf-8")) == old.json()


def test_secrets_never_render_in_either_block(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_both(sd)
    for fn in ("profitability_experiment_v1_1.json", REPORT_FILE):
        d = json.loads((sd / fn).read_text(encoding="utf-8"))
        d["leak_probe"] = {"api_key": "SEKRET_ANAHTAR_654321", "token": "ghp_YYYYSECRET"}
        (sd / fn).write_text(json.dumps(d, default=str), encoding="utf-8")
    t = c.get("/learning").text
    assert "SEKRET_ANAHTAR_654321" not in t and "ghp_YYYYSECRET" not in t
