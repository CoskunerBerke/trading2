# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — PANEL KARTI (2026-09-30; SPEC_ADVISOR_V1 §6.5, §7 T-A10). SALT OKUR, O(1): yalnız
`advice/advisor_status.json` (≤ 256 KB) ve `advice/walkforward_summary.json` (≤ 4 MB, isteğe bağlı).

* Kart fikstür JSON'dan basılır: defter başına son 24 sa tablosu (gerçek ve olsaydı kanalı ayrı), koşan walk-forward
  (N_T, N_G, U_ort, Δ), son taramanın aralığı ve bakış durumu, sabit şerit.
* Durum dosyası yoksa kart "" (sayfa bit-aynı; `/api/shared-experience-advisor` → available false).
* Bozuk / büyük / başka şemalı dosya istisna ATMAZ (kart yok ya da tarama bölümü yok); dosya KOPYALANMAZ.
* Panelin yerel sabitleri (dosya adları, şemalar, şerit, etiketler) paketinkilere EŞİT; panel paketi içe aktarmaz.
* Gerçek canlı danışmanın yazdığı durum dosyasından basılan kartın koşan sayıları durum dosyasındakiyle aynı.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from tradingbot.dashboard import state as DS  # noqa: E402
from tradingbot.dashboard.state import StateReader  # noqa: E402
from tradingbot.dashboard.templates import advisor_card  # noqa: E402

BANNER = ("yalnız KAYIT — karar değişmez; ara bakış kanıt değildir; başarı yalnız önceden kayıtlı bakışlarda "
          "(L1/L2/L3)")


def _status(**over) -> dict:
    doc = {
        "schema": "shared_experience_advisor_status_v1", "advisor_id": "advisor_v1", "advisor_sha": "abcdef0123456789",
        "banner": BANNER, "state": "OK", "mode": "live", "effect": "NONE", "last_step_at": "2026-10-05T12:00:03+00:00",
        "steps": 40, "step_ms_p50": 3.1, "step_ms_p95": 7.25, "advisor_born_ms": 1791000000000,
        "advisor_born_at": "2026-10-03T06:40:00.000+00:00", "index_mb": 1.234, "errors": 0,
        "position": {"last_row_id": "0123456789abcdef", "batch_clock_ms": 1791200000000, "lag_rows": 0},
        "fold": {"pending_contexts": 17},
        "breaker": {"tripped": False, "consecutive_errors": 0},
        "ring_24h": {"strategy_paper_box": {"real": {"GIR": 2, "GIRME": 5, "LATE": 1},
                                            "cf": {"NOTR": 9, "VERI_AZ": 31, "GIRME": 4}},
                     "main": {"real": {"VERI_AZ": 3}, "cf": {"VERI_AZ": 1}}},
        "running": {"ALL": {"N_T": 12, "N_G": 4, "U_mean": 0.416667, "U_sum100": 25.0, "delta": 0.791667},
                    "strategy_paper_box": {"N_T": 10, "N_G": 4, "U_mean": 0.5, "U_sum100": 30.0, "delta": 0.9},
                    "main": {"N_T": 2, "N_G": 0, "U_mean": 0.0, "U_sum100": 0.0, "delta": None}}}
    doc.update(over)
    return doc


def _summary() -> dict:
    return {"schema": "shared_experience_advisor_summary_v1", "wf_sha": "0011223344556677",
            "advisor_sha": "abcdef0123456789", "banner": BANNER, "generated_at": "2026-10-05T11:00:00+00:00",
            "primary": {"n_T": 12, "n_G": 4, "U_mean": 0.416667, "U_mean_ci": [-0.1, 0.9], "delta": 0.79},
            "h2": {"n_T": 12, "n_G": 1, "U_mean": 0.1},
            "by_book": {"strategy_paper_box": {"n_T": 10, "n_G": 4, "U_mean": 0.5, "U_mean_ci": [-0.25, 1.125],
                                               "delta": 0.9, "delta_ci": None}},
            "looks": {"H1": {"verdict": "PENDING", "reason": "L1_NOT_YET", "L1": None, "looks": []},
                      "H2": {"verdict": "NOT_REACHED", "reason": "NOT_REACHED", "L1": None, "looks": []}}}


def _adv_dir(state: Path) -> Path:
    d = state / "shared_experience" / "advice"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tree(p: Path) -> dict:
    return {q.relative_to(p).as_posix(): q.read_bytes() for q in sorted(p.rglob("*")) if q.is_file()}


# ============================================================================ sabitler
def test_local_constants_equal_the_package_constants():
    from tradingbot.ops import backup as B
    from tradingbot.shared_experience import advice_store as ADS
    from tradingbot.shared_experience import advisor as A
    from tradingbot.shared_experience import advisor_eval as AE
    from tradingbot.shared_experience import advisor_live as AL
    assert DS.XP_ADVICE_DIR == ADS.ADVICE_DIR
    assert DS.XP_ADV_STATUS_FILE == AL.STATUS_FILE and DS.XP_ADV_STATUS_SCHEMA == AL.STATUS_SCHEMA
    assert DS.XP_ADV_SUMMARY_FILE == AL.SUMMARY_FILE and DS.XP_ADV_SUMMARY_SCHEMA == AE.SUMMARY_SCHEMA
    assert DS.XP_ADV_BANNER_TR == AL.BANNER_TR == AE.BANNER_TR == BANNER
    assert DS.XP_ADV_LABELS == A.LABELS
    assert DS.XP_ADV_STATUS_MAX == 256 * 1024 and DS.XP_ADV_SUMMARY_MAX == 4 * 1024 * 1024
    assert AL.STATUS_MAX_BYTES <= DS.XP_ADV_STATUS_MAX, "yazıcının tavanı okuyucununkinin altında"
    assert B.XP_ADVICE_SEGMENTS_REL == "shared_experience/%s/archive/segments/" % ADS.ADVICE_DIR
    assert B.XP_ADVISOR_STATE_REL == "shared_experience/%s/%s" % (ADS.ADVICE_DIR, AL.STATE_FILE)


# ============================================================================ yok → kart yok
def test_absent_status_gives_no_card():
    assert advisor_card(None) == "" and advisor_card({}) == ""


def test_reader_returns_none_without_status_and_never_writes(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    rd = StateReader(state)
    assert rd.shared_experience_advisor() is None
    d = _adv_dir(state)
    (d / "walkforward_summary.json").write_text(json.dumps(_summary()), encoding="utf-8")
    assert rd.shared_experience_advisor() is None, "özet tek başına kart açmaz"
    before = _tree(state)
    rd.shared_experience_advisor()
    assert _tree(state) == before


# ============================================================================ fikstürden kart
def test_card_renders_per_book_24h_table_running_uplift_look_status_and_banner(tmp_path):
    state = tmp_path / "state"
    d = _adv_dir(state)
    (d / "advisor_status.json").write_text(json.dumps(_status(), ensure_ascii=False), encoding="utf-8")
    rd = StateReader(state)
    v = rd.shared_experience_advisor()
    assert v["banner"] == BANNER and v["state"] == "OK" and v["mode"] == "live" and v["lag_rows"] == 0
    assert v["last_sweep_at"] is None and v["looks"] is None
    books = {b["book"]: b for b in v["books"]}
    assert set(books) == {"strategy_paper_box", "main"}
    box = books["strategy_paper_box"]
    assert box["real"] == {"GIR": 2, "NOTR": 0, "GIRME": 5, "VERI_AZ": 0, "LATE": 1, "NO_GROUP": 0}
    assert box["cf"] == {"GIR": 0, "NOTR": 9, "GIRME": 4, "VERI_AZ": 31}
    assert box["running"]["N_T"] == 10 and box["running"]["U_mean"] == 0.5 and box["sweep"] is None
    assert v["running_all"]["U_mean"] == 0.416667
    html = advisor_card(v)
    assert "Gölge danışman — yalnız KAYIT" in html and BANNER in html
    assert "strategy_paper_box" in html and "main" in html
    assert "0.417" in html and "tarama yok" in html and "--summary-out" in html
    assert "GİR hiçbir zaman girişi zorlamaz" in html
    # tarama özeti gelince aralık ve bakış durumu basılır
    (d / "walkforward_summary.json").write_text(json.dumps(_summary(), ensure_ascii=False), encoding="utf-8")
    v = rd.shared_experience_advisor()
    assert v["last_sweep_at"] == "2026-10-05T11:00:00+00:00" and v["looks"]["H1"]["verdict"] == "PENDING"
    box = {b["book"]: b for b in v["books"]}["strategy_paper_box"]
    assert box["sweep"]["U_mean_ci"] == [-0.25, 1.125]
    html = advisor_card(v)
    assert "PENDING" in html and "L1_NOT_YET" in html and "[-0.250 ; +1.125]" in html and "tarama yok" not in html


@pytest.mark.parametrize("state_,badge_kind", [("OK", "ok"), ("DEGRADED", "bad"), ("DISABLED_BY_BREAKER", "bad")])
def test_state_badge_reflects_degraded_and_breaker(tmp_path, state_, badge_kind):
    state = tmp_path / "state"
    d = _adv_dir(state)
    (d / "advisor_status.json").write_text(json.dumps(_status(state=state_)), encoding="utf-8")
    html = advisor_card(StateReader(state).shared_experience_advisor())
    assert state_ in html and "b-%s" % badge_kind in html


# ============================================================================ bozuk / büyük / yabancı
@pytest.mark.parametrize("content", ["{bozuk", "[]", "null", json.dumps({"schema": "başka_v9", "state": "OK"}),
                                     json.dumps(_status(schema="shared_experience_status_v1"))])
def test_malformed_or_foreign_status_gives_no_card_and_no_exception(tmp_path, content):
    state = tmp_path / "state"
    d = _adv_dir(state)
    (d / "advisor_status.json").write_text(content, encoding="utf-8")
    assert StateReader(state).shared_experience_advisor() is None
    assert not [p for p in d.iterdir() if "corrupt" in p.name], "bozuk dosya KOPYALANMAZ"


def test_oversized_status_is_ignored_and_oversized_summary_drops_only_the_sweep(tmp_path):
    state = tmp_path / "state"
    d = _adv_dir(state)
    big = _status(pad="x" * (DS.XP_ADV_STATUS_MAX + 1))
    (d / "advisor_status.json").write_text(json.dumps(big), encoding="utf-8")
    assert StateReader(state).shared_experience_advisor() is None
    (d / "advisor_status.json").write_text(json.dumps(_status()), encoding="utf-8")
    (d / "walkforward_summary.json").write_text(json.dumps({**_summary(), "pad": "y" * DS.XP_ADV_SUMMARY_MAX}),
                                                encoding="utf-8")
    v = StateReader(state).shared_experience_advisor()
    assert v is not None and v["last_sweep_at"] is None and v["looks"] is None
    (d / "walkforward_summary.json").write_text("{bozuk", encoding="utf-8")
    v = StateReader(state).shared_experience_advisor()
    assert v is not None and v["last_sweep_at"] is None
    (d / "walkforward_summary.json").write_text(json.dumps({**_summary(), "schema": "x"}), encoding="utf-8")
    assert StateReader(state).shared_experience_advisor()["last_sweep_at"] is None


def test_junk_field_types_render_without_exception(tmp_path):
    state = tmp_path / "state"
    d = _adv_dir(state)
    junk = _status(ring_24h=["x"], running={"ALL": "x", "b": [1]}, breaker="yes", fold=3, position=None,
                   step_ms_p95={"a": 1})
    (d / "advisor_status.json").write_text(json.dumps(junk), encoding="utf-8")
    (d / "walkforward_summary.json").write_text(json.dumps({**_summary(), "by_book": "x", "looks": ["a"]}),
                                                encoding="utf-8")
    v = StateReader(state).shared_experience_advisor()
    assert v is not None
    html = advisor_card(v)
    assert "Gölge danışman" in html
    junk2 = _status(ring_24h={"strategy_paper_box": {"real": "x", "cf": None}}, running={"strategy_paper_box": 5})
    (d / "advisor_status.json").write_text(json.dumps(junk2), encoding="utf-8")
    assert "strategy_paper_box" in advisor_card(StateReader(state).shared_experience_advisor())


# ============================================================================ uygulama: sayfa ve API
def test_overview_page_is_bit_identical_without_status_and_shows_the_card_with_it(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    from tradingbot.dashboard.config import DashboardConfig
    state = tmp_path / "state"
    (state / "shared_experience").mkdir(parents=True)
    (state / "shared_experience" / "status.json").write_text(json.dumps({"state": "OK", "counters": {"rows_total": 3}}),
                                                            encoding="utf-8")
    c = TestClient(create_app(state, tmp_path / "market", None, DashboardConfig()))
    p1 = c.get("/").text
    assert "Gölge danışman" not in p1 and "Ortak deneyim — yalnız KAYIT" in p1
    assert c.get("/api/shared-experience-advisor").json() == {"available": False}
    (state / "shared_experience" / "advice").mkdir()
    p2 = c.get("/").text
    assert p2 == p1, "boş advice/ klasörü sayfayı değiştirmez"
    (state / "shared_experience" / "advice" / "advisor_status.json").write_text(json.dumps(_status()), encoding="utf-8")
    p3 = c.get("/").text
    assert "Gölge danışman — yalnız KAYIT" in p3 and BANNER in p3
    api = c.get("/api/shared-experience-advisor").json()
    assert api["available"] is True and api["state"] == "OK" and api["running_all"]["N_T"] == 12
    before = _tree(state)
    c.get("/")
    c.get("/api/shared-experience-advisor")
    assert _tree(state) == before


# ============================================================================ gerçek canlı danışmanın durumu
def test_card_from_a_real_live_advisor_status_matches_its_running_numbers(tmp_path):
    from test_shared_experience_advisor_fold_v1 import run_world

    from tradingbot.shared_experience import advisor_live as AL
    w = run_world(tmp_path, 14)
    st = json.loads((w.root / "advice" / AL.STATUS_FILE).read_text(encoding="utf-8"))
    assert len(json.dumps(st).encode()) <= AL.STATUS_MAX_BYTES
    v = StateReader(w.eng.cfg.state_path).shared_experience_advisor()
    assert v is not None and v["state"] == st["state"] == "OK" and v["advisor_sha"] == st["advisor_sha"]
    books = {b["book"]: b for b in v["books"]}
    assert "strategy_paper_box" in books
    for b, run in st["running"].items():
        if b == "ALL":
            assert v["running_all"] == run
            continue
        assert books[b]["running"] == {k: run.get(k) for k in ("N_T", "N_G", "U_mean", "U_sum100", "delta")}
    for b, ring in st["ring_24h"].items():
        assert sum(books[b]["real"].values()) == sum(ring.get("real", {}).values())
    html = advisor_card(v)
    assert "strategy_paper_box" in html and BANNER in html
