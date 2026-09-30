# -*- coding: utf-8 -*-
"""Demo panel durumu betiği (`scripts/demo_panel_state.py`): güvenli CLI.

* `--help` hiçbir şey yazmaz (eskiden `--help` adlı bir klasöre demo state yazıyordu).
* Bu betiğin yazmadığı dolu bir `state/` klasörüne yazmaz (işaret dosyası yoksa çıkış 2, dosyalar aynen).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_panel_state.py"
_spec = importlib.util.spec_from_file_location("demo_panel_state", SCRIPT)
DP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DP)


def _tree(p: Path) -> dict[str, bytes]:
    return {q.relative_to(p).as_posix(): q.read_bytes() for q in sorted(p.rglob("*")) if q.is_file()}


def test_help_prints_usage_and_writes_nothing(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout.lower()
    assert list(tmp_path.iterdir()) == [], "--help bir klasör/dosya yazmamalı"


def test_refuses_a_state_dir_it_did_not_write(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "futures_ledger.json").write_text('{"real": true}', encoding="utf-8")
    before = _tree(tmp_path)
    assert DP.main([str(tmp_path)]) == 2
    with pytest.raises(ValueError):
        DP.build(tmp_path)
    assert _tree(tmp_path) == before


def test_writes_marker_and_can_rerun_into_its_own_output(tmp_path):
    assert DP.main([str(tmp_path / "demo")]) == 0
    assert (tmp_path / "demo" / "state" / DP.MARKER).exists()
    assert DP.main([str(tmp_path / "demo")]) == 0
