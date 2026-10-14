# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — AYRI TAVSİYE DEPOSU (2026-09-29; SPEC_ADVISOR_V1 §6.2, §6.7, §7 T-A4).

* Şema/tür ayrımı: `AdviceStore` `xp_*` satırlarını, `ExperienceStore` `xp_advice` satırlarını REDDEDER; ana deponun
  sözleşmesi (`KINDS`, `ROW_SCHEMA`, akış kimliği, sıcak dosya) sınıf özniteliklerine taşındı ama değerleri AYNI.
* `row_id` ile idempotent; toplu yazım başına TEK fsync; histerezisli döngü; %100 disk baskısında seyreltme YOK →
  doğrudan DEGRADED.
* Ana depo DEĞİŞMEZ: aynı dünya danışman KAPALI / KAYIT ile koşulunca `experience.jsonl`, `archive/**`, `cursor.json`
  bayt bayt aynı; KAPALIYKEN `advice/` klasörü bile kurulmaz. `report.iter_rows` tavsiye satırı VERMEZ.
* Yedek: saatlik yedek tavsiye segmentlerini yalnız UTC 00'da, türetilmiş anlık görüntüyü HİÇ taşımaz.
"""
from __future__ import annotations

import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import advisor_synth as X  # noqa: E402
from advisor_synth import A, R, XR  # noqa: E402
from test_shared_experience_advisor_fold_v1 import LiveWorld, run_world  # noqa: E402

from tradingbot.shared_experience import advisor_eval as AE  # noqa: E402
from tradingbot.shared_experience import advisor_live as AL  # noqa: E402
from tradingbot.shared_experience import store as ST  # noqa: E402
from tradingbot.shared_experience.advice_store import (ADVICE_DIR, ADVICE_HOT_FILE, ADVICE_STREAM_ID,  # noqa: E402
                                                        AdviceStore)

UTC = timezone.utc


def _advice_rows(n: int = 3, start: int = 0) -> list[dict]:
    f = A.AdvisorFold()
    rows = [X.cf("strategy_paper_box", "a%d" % i, X.T0_MS + i * 1000, rec=X.iso_us(X.T0_MS), setup="box_fade")
            for i in range(start, start + n)]
    out = f.fold_batch(rows).advice_rows
    for r in out:
        r["recorded_at"] = X.iso_us(X.T0_MS + 5)
        r["meta"] = {"fold_mode": "live"}
    return out


# ============================================================================ sözleşme
def test_class_attribute_contract_and_main_store_values_unchanged():
    assert (ST.ExperienceStore.SCHEMA, ST.ExperienceStore.KINDS_ACCEPTED) == (R.ROW_SCHEMA, R.KINDS)
    assert (ST.ExperienceStore.STREAM_ID, ST.ExperienceStore.HOT_FILE) == ("shared_experience", "experience.jsonl")
    assert ST.ExperienceStore.THIN_ENABLED is True
    assert (AdviceStore.SCHEMA, AdviceStore.KINDS_ACCEPTED) == ("shared_experience_advice_v1", ("xp_advice",))
    assert (AdviceStore.STREAM_ID, AdviceStore.HOT_FILE, AdviceStore.THIN_ENABLED) == (
        ADVICE_STREAM_ID, ADVICE_HOT_FILE, False)
    from tradingbot.shared_experience import KINDS, ROW_SCHEMA
    assert KINDS == ("xp_entry", "xp_outcome", "xp_cf") and ROW_SCHEMA == "shared_experience_row_v1"


def test_schema_and_kind_validation_keeps_the_two_streams_apart(tmp_path):
    adv = AdviceStore(tmp_path / "advice")
    main = ST.ExperienceStore(tmp_path / "xp")
    arows = _advice_rows(2)
    xrows = [X.cf("strategy_paper_box", "c1", X.T0_MS, rec=X.iso_us(X.T0_MS), setup="box_fade")]
    assert adv.append_rows(xrows) == (0, 1) and adv.last_append["reasons"] == {"SCHEMA": 1}
    assert main.append_rows(arows) == (0, 2) and main.last_append["reasons"] == {"SCHEMA": 2}
    hybrid = dict(arows[0], schema=R.ROW_SCHEMA)
    assert main.append_rows([hybrid]) == (0, 1) and main.last_append["reasons"] == {"KIND": 1}
    assert adv.append_rows(arows) == (2, 0) and main.append_rows(xrows) == (1, 0)
    assert json.loads((tmp_path / "advice" / "advice.jsonl").read_text().splitlines()[0])["kind"] == "xp_advice"
    assert adv.summary()["schema"] == "shared_experience_advice_v1"


def test_idempotent_by_row_id_and_one_fsync_per_batch(tmp_path, monkeypatch):
    import os
    calls = []

    class _OS:
        def __getattr__(self, name):
            return getattr(os, name)

        @staticmethod
        def fsync(fd):
            calls.append(fd)
            return os.fsync(fd)
    monkeypatch.setattr(ST, "os", _OS())
    adv = AdviceStore(tmp_path / "advice")
    rows = _advice_rows(40)
    assert adv.append_rows(rows) == (40, 0) and len(calls) == 1
    assert adv.append_rows(rows) == (0, 0) and adv.last_append["duplicate"] == 40 and len(calls) == 1
    again = AdviceStore(tmp_path / "advice")
    again.load_seen()
    assert again.append_rows(rows[:5]) == (0, 0) and again.last_append["duplicate"] == 5


def test_rotation_with_hysteresis_is_lossless(tmp_path):
    adv = AdviceStore(tmp_path / "xp" / "advice", hot_max_lines=20)
    rows = _advice_rows(25)
    adv.append_rows(rows)
    res = adv.rotate()
    assert res["archived"] == 15 and adv._line_count == 10
    man = json.loads((tmp_path / "xp" / "advice" / "archive" / "manifest.json").read_text())
    assert man["stream_id"] == ADVICE_STREAM_ID and len(man["segments"]) == 1
    got = [r["row_id"] for r in AE.iter_advice(tmp_path / "xp")]
    assert got == [r["row_id"] for r in rows]


def test_disk_pressure_goes_straight_to_degraded_without_thinning(tmp_path):
    adv = AdviceStore(tmp_path / "advice", max_total_mb=1)
    main = ST.ExperienceStore(tmp_path / "xp", max_total_mb=1)
    cap = adv.max_total_bytes
    assert adv._state_for(int(cap * 0.95)) == ST.STATE_WARN
    assert adv._state_for(cap) == ST.STATE_DEGRADED and main._state_for(cap) == ST.STATE_THINNING
    assert adv._state_for(int(cap * 1.2)) == ST.STATE_DEGRADED
    (tmp_path / "advice" / "advice.jsonl").write_bytes(b"x" * cap + b"\n")
    w, rej = adv.append_rows(_advice_rows(3))
    assert (w, rej) == (0, 3) and adv.last_append["blocked_degraded"] == 3 and adv.last_append["thinned"] == 0


# ============================================================================ ana depo değişmez
def _main_store_bytes(root: Path) -> dict[str, bytes]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and (rel == "experience.jsonl" or rel == "cursor.json" or rel.startswith("archive/")):
            out[rel] = p.read_bytes()
    return out


def test_main_store_and_cursor_are_byte_identical_advisor_off_vs_record(tmp_path):
    time_machine = pytest.importorskip("time_machine")
    # arşiv manifesti duvar saatini yazar (danışmandan bağımsız): iki koşu aynı donmuş saatte
    with time_machine.travel(datetime(2026, 10, 1, 12, 0, tzinfo=UTC), tick=False):
        off = run_world(tmp_path / "off", 30, advisor="OFF", hot_max_lines=80)
    with time_machine.travel(datetime(2026, 10, 1, 12, 0, tzinfo=UTC), tick=False):
        rec = run_world(tmp_path / "rec", 30, advisor="RECORD", hot_max_lines=80)
    a, b = _main_store_bytes(off.root), _main_store_bytes(rec.root)
    assert any(k.startswith("archive/segments/") for k in a), "döngü de sınandı"
    assert a == b and len(a) >= 4
    assert not (off.root / ADVICE_DIR).exists(), "KAPALIYKEN tavsiye klasörü kurulmaz"
    assert (rec.root / ADVICE_DIR / ADVICE_HOT_FILE).exists()
    assert "advisor" not in off.xp.health() and rec.xp.health()["advisor"]["state"] == "OK"
    assert off.xp._advisor is None and "tradingbot.shared_experience.advisor_live" in sys.modules


def test_report_reader_never_yields_advice_rows(tmp_path):
    w = run_world(tmp_path, 12)
    schemas = {r.get("schema") for r in XR.iter_rows(w.root)}
    assert schemas == {R.ROW_SCHEMA}
    assert {r.get("schema") for r in AE.iter_advice(w.root)} == {A.ADVICE_SCHEMA}


def test_advisor_off_by_default_never_imports_the_advisor_modules(tmp_path):
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
            "import test_shared_experience_collector_v1 as T\n"
            "from pathlib import Path\n"
            "eng = T.fake_engine(Path(%r)); xp = T.collector(eng); T.step(xp, eng, T.T0)\n"
            "bad = [m for m in sys.modules if m.startswith('tradingbot.shared_experience.advi')]\n"
            "print(bad)\n") % (str(X.ROOT), str(HERE), str(tmp_path))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "[]"


# ============================================================================ yedek
def _state(tmp_path: Path) -> Path:
    st = tmp_path / "state"
    for rel in ("shared_experience/archive/segments/seg-0.jsonl.gz", "shared_experience/advice/archive/segments/a-0.gz",
                "shared_experience/advice/archive/segments/a-1.gz"):
        p = st / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 10)
    for rel in ("shared_experience/experience.jsonl", "shared_experience/advice/advice.jsonl",
                "shared_experience/advice/advisor_meta.json", "shared_experience/advice/advisor_status.json",
                "shared_experience/advice/archive/manifest.json", "shared_experience/advice/advisor_state.v1.gz",
                "health.json"):
        p = st / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}\n", encoding="utf-8")
    return st


@pytest.mark.parametrize("kind,hour,segs,snap", [("hourly", 5, False, False), ("hourly", 0, True, False),
                                                 ("daily", 5, True, True), ("manual", 13, True, True),
                                                 ("weekly", 3, True, True)])
def test_backup_skips_advice_segments_hourly_and_the_snapshot_always_hourly(tmp_path, kind, hour, segs, snap):
    from tradingbot.ops import backup as B
    assert B.XP_ADVICE_SEGMENTS_REL == "shared_experience/%s/%s/%s/" % (ADVICE_DIR, XR.ARCHIVE_DIR, XR.SEGMENTS_DIR)
    assert B.XP_ADVISOR_STATE_REL == "shared_experience/%s/%s" % (ADVICE_DIR, AL.STATE_FILE)
    st = _state(tmp_path)
    res = B.run_backup(st, tmp_path / "backups", kind, now=datetime(2026, 10, 1, hour, 7, tzinfo=UTC))
    with tarfile.open(res.archive, "r:gz") as tar:
        names = set(tar.getnames())
    adv_segs = {n for n in names if "/advice/archive/segments/" in n}
    main_segs = {n for n in names if n.startswith("state/shared_experience/archive/segments/")}
    assert bool(adv_segs) is segs and bool(main_segs) is segs
    assert ("state/shared_experience/advice/advisor_state.v1.gz" in names) is snap
    for must in ("advice.jsonl", "advisor_meta.json", "advisor_status.json", "archive/manifest.json"):
        assert "state/shared_experience/advice/" + must in names, must
