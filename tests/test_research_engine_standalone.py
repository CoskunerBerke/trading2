# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1a — bağımsız koşucu (docs/SYSTEM_LEARNING_ENGINE_V1.md §9.3; P1a depo kabul testi 18).

`tests/standalone/run_engine_invariants.py`, depo testlerinin VPS'te koşan alt kümesini (kabul 1–8, 13, 14) pytest
OLMADAN çalıştırır. Bu test onu gerçek bir alt süreçte, servis ortamı aktarılmadan ve gerçek `pytest`/`_pytest` import'u
YASAKKEN (`--forbid-pytest`) koşar; ayrıca yerine-geçenlerin (approx, raises, monkeypatch) ve eksik test adının FAIL
sayıldığının birim testleri buradadır.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "standalone" / "run_engine_invariants.py"


def _load():
    spec = importlib.util.spec_from_file_location("engine_invariants_runner", RUNNER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_standalone_runner_passes_subset_without_pytest_in_a_clean_process(tmp_path):
    out = tmp_path / "inv.json"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path), "LANG": "C.UTF-8", "TZ": "UTC",
           "TRADINGBOT_DATA": "/opt/tradingbot/data", "ENGINE_EXPECTED_MEMORY_MAX": "1"}    # koşucu bunları siler
    cp = subprocess.run([sys.executable, "-s", str(RUNNER), "--tree", str(ROOT), "--forbid-pytest", "--json", str(out),
                         "--tmp", str(tmp_path / "work")], capture_output=True, text=True, env=env, cwd=str(tmp_path),
                        timeout=1200)
    assert cp.returncode == 0, cp.stdout[-4000:] + cp.stderr[-2000:]
    res = json.loads(out.read_text(encoding="utf-8"))
    m = _load()
    assert res["failed"] == 0 and res["skipped"] == 0 and res["passed"] == len(m.SUBSET)
    assert res["acceptance_passed"] == list(m.ACCEPTANCE) == [1, 2, 3, 4, 5, 6, 7, 8, 13, 14]
    assert "ÖZET:" in cp.stdout


def test_runner_shims_and_missing_test_is_fail(tmp_path, monkeypatch):
    m = _load()
    pt = m.make_pytest_shim()
    assert 0.1 + 0.2 == pt.approx(0.3) and 1.0 != pt.approx(1.1) and 100.0 == pt.approx(101.0, rel=0.02)
    assert [1.0, 2.0] == pt.approx([1.0, 2.0 + 1e-9])
    with pt.raises(ValueError, match="kötü"):
        raise ValueError("kötü değer")
    try:
        with pt.raises(ValueError):
            pass
    except AssertionError:
        pass
    else:
        raise AssertionError("DID NOT RAISE beklenirdi")
    mp = m.MonkeyPatch()

    class K:
        x = 1
    mp.setattr(K, "x", 2)
    mp.setenv("ENGINE_TEST_X", "1")
    assert K.x == 2 and os.environ["ENGINE_TEST_X"] == "1"
    mp.undo()
    assert K.x == 1 and "ENGINE_TEST_X" not in os.environ
    monkeypatch.setattr(m, "SUBSET", ((99, "test_research_engine_archive", "test_bu_ad_yok"),))
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        res = m.run(ROOT, tmp_path)
    finally:
        sys.path.remove(str(ROOT / "tests"))
    assert res[0]["status"] == "FAIL" and "bulunamadı" in res[0]["detail"]
