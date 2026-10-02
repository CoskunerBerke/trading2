# -*- coding: utf-8 -*-
"""ÖĞRENME-EKSTRA KİPİ — ÇOK TURLU ÇEVRİMDIŞI KANIT (2026-10-03, sahip kararı).

`tests/record_only_tour_runner.py` GERÇEK `TradingEngineV3.tour`u (ağsız harness; donmuş saat, deterministik kimlikler) beş
tur, iki senaryoda ALT SÜREÇTE koşar ve `state/` altındaki her dosyanın özetini döndürür:

* `extra_entries` YOK (kod varsayılanı) == `extra_entries: open` == TABAN `943345c` (aynı senaryo, `git archive` ile çıkarılan
  ağaçta) — BÜTÜN dosyalar bayt bayt aynı (yalnız süreç pid'i ve RSS ölçümü düşülür). Depo geçmişi yoksa (sığ klon) taban
  karşılaştırması atlanır; varsayılan == open yine koşar.
* `record_selectivity`: `books` senaryosunda seçicilik-ekstra yoktur → karar eserleri (defterler, karşı-olgusallar, gölge
  defter, işlem hafızası) bayt bayt aynı; `main_gates` senaryosunda `open`ın REGIME_VETO etiketiyle AÇTIĞI ana bot işlemleri
  AÇILMAZ, her biri LEARNING_RECORD_ONLY karşı-olgusalı olur; kâğıt defterler aynı kalır.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).resolve().parent / "record_only_tour_runner.py"
BASE = "943345c"
RO = "LEARNING_RECORD_ONLY"
#: Karar eserleri: kip etiketi taşıyan özet/sağlık/huni dosyaları DIŞINDA kalan, kararın kendisini tutan dosyalar.
DECISION_FILES = ("futures_ledger.json", "spot_ledger.json", "counterfactual_trades.json", "shadow_book.json", "plans.json",
                  "trade_memory.jsonl")


def _run(tree: Path, root: Path, scenario: str, extra: str) -> dict:
    out = root.parent / (root.name + ".json")
    p = subprocess.run([sys.executable, str(RUNNER), "--tree", str(tree), "--root", str(root), "--scenario", scenario,
                        "--extra", extra, "--out", str(out)], capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, (p.stdout[-2000:], p.stderr[-4000:])
    return json.loads(out.read_text(encoding="utf-8"))


def _base_tree(tmp: Path) -> Path | None:
    """Taban ağacı (`git archive 943345c tradingbot tests`) salt okunur çıkarır; geçmiş yoksa None."""
    try:
        blob = subprocess.run(["git", "-C", str(ROOT), "archive", BASE, "tradingbot", "tests"], capture_output=True,
                              timeout=120, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    dst = tmp / ("base_" + BASE)
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        tf.extractall(dst, filter="data")
    return dst


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("roe_tours")
    base = _base_tree(tmp)
    out = {"base_tree": base}
    for sc in ("books", "main_gates"):
        for extra in ("absent", "open", "record_selectivity"):
            out[(sc, extra)] = _run(ROOT, tmp / ("%s_%s" % (sc, extra)), sc, extra)
        out[(sc, "base")] = _run(base, tmp / ("%s_base" % sc), sc, "absent") if base is not None else None
    return out


def _diff(a: dict, b: dict) -> list[str]:
    fa, fb = a["files"], b["files"]
    return sorted(set(fa) ^ set(fb)) + [k for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]]


def _decision(run: dict) -> dict:
    return {k: v for k, v in run["files"].items() if k.endswith(DECISION_FILES)}


@pytest.mark.parametrize("scenario", ["books", "main_gates"])
def test_open_is_byte_identical_to_the_code_default(runs, scenario):
    assert _diff(runs[(scenario, "open")], runs[(scenario, "absent")]) == []
    assert runs[(scenario, "open")]["facts"] == runs[(scenario, "absent")]["facts"]
    assert len(runs[(scenario, "open")]["files"]) > 50


@pytest.mark.parametrize("scenario", ["books", "main_gates"])
def test_open_is_byte_identical_to_943345c(runs, scenario):
    if runs["base_tree"] is None:
        pytest.skip("depo geçmişi yok (sığ klon): %s çıkarılamadı" % BASE)
    assert _diff(runs[(scenario, "open")], runs[(scenario, "base")]) == []
    assert runs[(scenario, "absent")]["facts"] == runs[(scenario, "base")]["facts"]


def test_scenarios_are_meaningful(runs):
    books = runs[("books", "open")]["facts"]
    tags = [t for b in ("strategy_paper", "strategy_paper_m2") for _s, t in books[b]["open"] + books[b]["closed"]]
    assert [] in tags and ["TOTAL_OPEN_RISK"] in tags, tags            # politika + kapasite-ekstra
    gates = runs[("main_gates", "open")]["facts"]["main"]
    opened = gates["open"] + gates["closed"]
    assert len(opened) >= 2 and all(any(c.startswith("REGIME_VETO:") for c in t) for _s, t in opened), opened


def test_record_selectivity_keeps_capacity_and_policy_decisions_byte_identical(runs):
    rec, opn = runs[("books", "record_selectivity")], runs[("books", "open")]
    assert rec["facts"] == opn["facts"]
    assert _decision(rec) == _decision(opn) and len(_decision(rec)) >= 5


def test_record_selectivity_records_the_selectivity_extras_instead_of_opening_them(runs):
    rec, opn = runs[("main_gates", "record_selectivity")]["facts"], runs[("main_gates", "open")]["facts"]
    opened = sorted(s for s, _t in opn["main"]["open"] + opn["main"]["closed"])
    assert rec["main"]["open"] == [] and rec["main"]["closed"] == []
    recorded = sorted(s for s, r in rec["main"]["cf"] if r and r[0] == RO)
    assert recorded == opened, (recorded, opened)
    for _s, r in rec["main"]["cf"]:
        assert r[0] == RO and any(c.startswith("REGIME_VETO:") for c in r[1:]), r
    assert {k: v for k, v in rec.items() if k != "main"} == {k: v for k, v in opn.items() if k != "main"}
