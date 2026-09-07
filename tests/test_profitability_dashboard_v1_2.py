"""Pano: üç deney sürümü AYRI ve dürüst — v1 (tarihsel), v1.1 (kabul kapalı/drain), v1.2 (aktif).

* v1.2: yeni kimlik/başlangıç, giriş politikası entry_v1.1.0, E kapsam alanları AYRI sütunlarda
  (futures stop riski, futures bütçesi, futures ısı oranı, birleşik tanı maruziyeti, stopsuz spot
  bileşeni, futures-only ve birleşik aynı yön sayımı), beş politika metrikleri, kapılar, düşük
  örneklem, bütünlük.
* v1.1: SUPERSEDED_E_SCOPE_MISMATCH_DRAINING, birleşik pay / futures-only payda açıklaması,
  F00036'nın DEĞİŞTİRİLMEMİŞ kararları, kabul kapalı, kârlılık sonucu YOK.
* v1: SUPERSEDED_INCOMPLETE_ENTRY_INPUT.
* Birleşik tanı değeri futures limit ihlali olarak ETİKETLENMEZ. Yasak dil yok. 500 yok.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.learn import profitability_experiment as PX
from tradingbot.learn.profitability_experiment import HONESTY_STATEMENTS_TR
from tradingbot.learn.profitability_store import REPORT_FILE
from tests.test_profitability_experiment_v1_1_versioning import seed_v1
from tests.test_profitability_experiment_v1_2 import (F36_OPENED, NEW_OPENED, V12_START, _engine,
                                                      _pos, seed_v11, snap_for)

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


def _seed_all(sd: Path) -> None:
    """VPS benzeri yerleşim: v1 (F00035), v1.1 (F00036, drain) ve v1.2 (yeni F00040)."""
    seed_v1(sd)
    seed_v11(sd)
    eng = _engine(sd, positions={"ONDO/USDT": _pos("F00036", F36_OPENED, "ONDO/USDT"),
                                 "NEAR/USDT": _pos("F00040", NEW_OPENED, "NEAR/USDT")},
                  snaps=[(snap_for("ONDO/USDT", ts=F36_OPENED, cycle="85"), "F00036"),
                         (snap_for("NEAR/USDT", fut_risk=4.850588, same_fut=10), "F00040")],
                  path_rows=[{"trade_id": "F00036", "mark": 0.40, "ts": "2026-09-08T02:00:00+00:00",
                              "snapshot_id": "a"}])
    assert eng.experiment_drain is not None
    eng._run_profitability_experiment(None)
    for n in ("profitability_experiment_v1_2.json", "profitability_experiment_v1_1.json", REPORT_FILE):
        assert (sd / n).exists(), n


def _segment(text: str) -> str:
    i = text.find("Kârlılık Deneyi")
    assert i >= 0
    return text[i:]


def test_three_versions_render_separately_in_order_with_status(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_all(sd)
    r = c.get("/learning")
    assert r.status_code == 200
    seg = _segment(r.text)
    h12 = "Düzeltilmiş sürüm — pfexp_v1_2 (pfexp_v1.2.0)"
    h11 = "Kabul-kapalı sürüm — pfexp_v1_1 (pfexp_v1.1.0)"
    h1 = "Tarihsel sürüm — pfexp_v1 (pfexp_v1.0.0)"
    for h in (h12, h11, h1):
        assert h in seg, h
    assert seg.index(h12) < seg.index(h11) < seg.index(h1)
    for probe in ("ACTIVE_SHADOW", "SUPERSEDED_E_SCOPE_MISMATCH_DRAINING",
                  "SUPERSEDED_INCOMPLETE_ENTRY_INPUT", V12_START[:19], "entry_v1.1.0",
                  "FUTURES_STOP_RISK_BUCKET", "P0_CHAMPION_MIRROR", "P4_COMBINED",
                  "DEĞERLENDİRİLEMEZ", "Terfi kapıları", "Durum bütünlüğü", "Yabancı kimlik",
                  "PRE_EXPERIMENT_OBSERVATION_ONLY", "F00036", "F00040"):
        assert probe in seg, probe


def test_v12_scope_fields_displayed_separately_and_not_as_limit_violation(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_all(sd)
    seg = _segment(c.get("/learning").text)
    for col in ("Futures stop riski", "Futures risk bütçesi", "Futures ısı oranı",
                "Birleşik tanı maruziyeti", "Futures-dışı bileşen (stopsuz spot)",
                "Birleşik ısı (tanı)", "Aynı yön (futures)", "Aynı yön (birleşik)"):
        assert col in seg, col
    assert "futures limit ihlali DEĞİLDİR" in seg
    assert "diagnostic_ratio_not_enforced" in seg
    # F00040 satırı: futures 4.850588/6.0 = 0.8084 (futures kovası) ve birleşik 13.0766 AYRI.
    rep = json.loads((sd / "profitability_experiment_v1_2.json").read_text(encoding="utf-8"))
    rd = [r for r in rep["recent_entry_decisions"] if r["trade_id"] == "F00040"][0]
    f = rd["e_scope_fields"]
    assert f["scope"] == "FUTURES_STOP_RISK_BUCKET"
    assert f["futures_heat_fraction"] == pytest.approx(0.808431, abs=1e-6)
    assert f["combined_diagnostic_exposure_usdt"] == pytest.approx(13.076588)
    assert f["same_direction_open_futures"] == 10 and f["same_direction_open_combined"] == 11
    assert "0.8084" in seg and "13.0766" in seg
    # P2 notu
    assert "P2 hakkında" in seg and "izole simüle defterinde" in seg


def test_v11_block_explains_scope_mismatch_and_shows_unchanged_f00036_decisions(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_all(sd)
    seg = _segment(c.get("/learning").text)
    i = seg.index("Kabul-kapalı sürüm — pfexp_v1_1")
    j = seg.index("Tarihsel sürüm — pfexp_v1 ")
    blk = seg[i:j]
    for probe in ("birleşik tanı", "futures-only", "diagnostic_ratio_not_enforced",
                  "yeni kabul almaz", "kârlılık", "YOKTUR", "KAPALI", "F00036",
                  "Gerçek katılım", "SÜRÜYOR", "5d3549e72a2049ff"):
        assert probe in blk, probe
    # F00036: P0/P2/P3 ACCEPT, P1/P4 FILTER — değiştirilmedi.
    assert blk.count("FILTER") >= 2 and blk.count("ACCEPT") >= 3
    assert "Karar olayları değişti mi" in blk and "HAYIR" in blk
    # v1.1 için terfi kapısı/sıralama GÖSTERİLMEZ (sonuç yok).
    assert "Terfi kapıları" not in blk and "Erken yön göstergesi" not in blk


def test_required_statements_present_and_forbidden_language_absent(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_all(sd)
    seg = _segment(c.get("/learning").text)
    for s in HONESTY_STATEMENTS_TR:
        assert s in seg, s
    low = seg.lower()
    for bad in FORBIDDEN_WORDS:
        assert bad not in low, bad


def test_pre_deploy_layout_v1_and_v11_only_never_500(tmp_path: Path):
    """Dağıtım öncesi: yalnız v1 + v1.1 dosyaları (v1.1 raporu statüsüz, eski kod) → 500 yok."""
    c, sd = _client(tmp_path)
    seed_v1(sd)
    seed_v11(sd)
    old_style = json.loads((sd / "profitability_experiment_v1_1_books.json").read_text(encoding="utf-8"))
    doc = PX.compare({p: PX.PolicyBook(p) for p in PX.POLICIES},
                     PX.ExperimentConfig.from_dict(dict(experiment_id="pfexp_v1_1", policy_version="pfexp_v1.1.0",
                                                        evaluation_start_at=old_style["evaluation_start_at"])))
    (sd / "profitability_experiment_v1_1.json").write_text(json.dumps(doc, default=str), encoding="utf-8")
    r = c.get("/learning")
    assert r.status_code == 200
    seg = _segment(r.text)
    assert "pfexp_v1_1" in seg and "Tarihsel sürüm — pfexp_v1 (pfexp_v1.0.0)" in seg


@pytest.mark.parametrize("bad", ["{bozuk", "[]", "null", '{"policies": "x", "status": 7, "policy_version": 3}'])
def test_missing_or_corrupt_files_never_500(tmp_path: Path, bad):
    c, sd = _client(tmp_path)
    assert c.get("/learning").status_code == 200
    for n in ("profitability_experiment_v1_2.json", "profitability_experiment_v1_1.json", REPORT_FILE):
        (sd / n).write_text(bad, encoding="utf-8")
    assert c.get("/learning").status_code == 200


def test_api_serves_all_versions_on_separate_endpoints(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_all(sd)
    r12 = c.get("/api/state/profitability_experiment_v1_2").json()
    r11 = c.get("/api/state/profitability_experiment_v1_1").json()
    r1 = c.get("/api/state/profitability_experiment").json()
    assert (r12["experiment_id"], r12["policy_version"], r12["status"]) == ("pfexp_v1_2", "pfexp_v1.2.0", "ACTIVE_SHADOW")
    assert r12["entry_policy_version"] == "entry_v1.1.0" and r12["e_scope"] == "FUTURES_STOP_RISK_BUCKET"
    assert (r11["experiment_id"], r11["status"], r11["admissions_open"]) == ("pfexp_v1_1", "SUPERSEDED_E_SCOPE_MISMATCH_DRAINING", False)
    assert r11["admissions_closed_at"] == V12_START and r11["superseded_by"] == "pfexp_v1_2"
    assert r11["profitability_conclusion"] is None
    assert (r1["experiment_id"], r1["policy_version"]) == ("pfexp_v1", "pfexp_v1.0.0")
    sv = {s["experiment_id"]: s for s in r12["superseded_versions"]}
    assert sv["pfexp_v1"]["status"] == "SUPERSEDED_INCOMPLETE_ENTRY_INPUT"
    assert sv["pfexp_v1_1"]["status"] == "SUPERSEDED_E_SCOPE_MISMATCH_DRAINING"


def test_secrets_never_render_in_any_block(tmp_path: Path):
    c, sd = _client(tmp_path)
    _seed_all(sd)
    for fn in ("profitability_experiment_v1_2.json", "profitability_experiment_v1_1.json", REPORT_FILE):
        d = json.loads((sd / fn).read_text(encoding="utf-8"))
        d["leak_probe"] = {"api_key": "SEKRET_ANAHTAR_777", "token": "ghp_ZZZZSECRET"}
        (sd / fn).write_text(json.dumps(d, default=str), encoding="utf-8")
    t = c.get("/learning").text
    assert "SEKRET_ANAHTAR_777" not in t and "ghp_ZZZZSECRET" not in t
