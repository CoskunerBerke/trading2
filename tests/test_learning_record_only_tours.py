# -*- coding: utf-8 -*-
"""ÖĞRENME-EKSTRA KİPİ — ÇOK TURLU ÇEVRİMDIŞI KANIT (2026-10-03, sahip kararı).

`tests/record_only_tour_runner.py` GERÇEK `TradingEngineV3.tour`u (ağsız harness; donmuş saat, deterministik kimlikler, ortak
deneyim katmanı RECORD) beş tur, iki senaryoda ALT SÜREÇTE koşar ve `state/` altındaki her dosyanın özetini döndürür.
`tests/record_only_direct_runner.py` turların boşta kaldığı yolları (D4, Box, T2 yapı gölgesi, Formasyon; ana botun
seçicilik/kapasite/politika adayları) ağacın kendi test düzeneğiyle DOĞRUDAN koşar.

* `extra_entries` YOK (kod varsayılanı) == `extra_entries: open` == TABAN `943345c` (aynı senaryo, `git archive` ile çıkarılan
  ağaçta) — BÜTÜN dosyalar bayt bayt aynı (xp satırları dahil; yalnız süreç pid'i, RSS ve süre ölçümleri düşülür). Depo
  geçmişi yoksa (sığ klon) taban karşılaştırması atlanır; varsayılan == open yine koşar.
* `record_selectivity`: `books` senaryosunda seçicilik-ekstra yoktur → karar eserleri (defterler, karşı-olgusallar, gölge
  defter, işlem hafızası) bayt bayt aynı; `main_gates` senaryosunda `open`ın REGIME_VETO etiketiyle AÇTIĞI ana bot işlemleri
  AÇILMAZ, her biri LEARNING_RECORD_ONLY karşı-olgusalı (ve mühürlü toplayıcıda `xp_cf` satırı) olur; kâğıt defterler aynı
  kalır. Doğrudan yollarda yalnız seçicilik kodlu senaryolar değişir; kapasite, politika ve Box istisnası aynı kalır.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tradingbot.learning_mode as LM  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).resolve().parent / "record_only_tour_runner.py"
DIRECT = Path(__file__).resolve().parent / "record_only_direct_runner.py"
BASE = "943345c"
RO = "LEARNING_RECORD_ONLY"
#: Karar eserleri: kip etiketi taşıyan özet/sağlık/huni dosyaları DIŞINDA kalan, kararın kendisini tutan dosyalar.
DECISION_FILES = ("futures_ledger.json", "spot_ledger.json", "counterfactual_trades.json", "shadow_book.json", "plans.json",
                  "trade_memory.jsonl")


def _run(tree: Path, root: Path, scenario: str | None, extra: str) -> dict:
    """`scenario` None → doğrudan yol koşucusu; aksi halde çok turlu koşucu."""
    out = root.parent / (root.name + ".json")
    cmd = [sys.executable, str(RUNNER if scenario else DIRECT), "--tree", str(tree), "--root", str(root), "--extra", extra,
           "--out", str(out)] + (["--scenario", scenario] if scenario else [])
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
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
    for sc in ("books", "main_gates", None):
        tag = sc or "direct"
        for extra in ("absent", "open", "record_selectivity"):
            out[(tag, extra)] = _run(ROOT, tmp / ("%s_%s" % (tag, extra)), sc, extra)
        out[(tag, "base")] = _run(base, tmp / ("%s_base" % tag), sc, "absent") if base is not None else None
    return out


def _diff(a: dict, b: dict) -> list[str]:
    fa, fb = a["files"], b["files"]
    return sorted(set(fa) ^ set(fb)) + [k for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]]


def _decision(run: dict) -> dict:
    return {k: v for k, v in run["files"].items() if k.endswith(DECISION_FILES)}


@pytest.mark.parametrize("scenario", ["books", "main_gates", "direct"])
def test_open_is_byte_identical_to_the_code_default(runs, scenario):
    assert _diff(runs[(scenario, "open")], runs[(scenario, "absent")]) == []
    assert runs[(scenario, "open")]["facts"] == runs[(scenario, "absent")]["facts"]
    assert len(runs[(scenario, "open")]["files"]) > 50


@pytest.mark.parametrize("scenario", ["books", "main_gates", "direct"])
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
    xp = runs[("main_gates", "open")]["facts"]["xp"]["kinds"]
    assert xp.get("xp_entry", 0) > 0, "ortak deneyim katmanı RECORD: xp satırları da karşılaştırılır"
    # doğrudan yollar: her seçicilik, kapasite, politika ve Box senaryosu `open`da gerçekten açılır
    f = runs[("direct", "open")]["facts"]

    def tags(name):
        return [t for _s, t in f[name]["open"] if t is not None]
    assert ["BOOK_UNIVERSE"] in tags("d4_universe") and ["BOOK_UNIVERSE"] in tags("d4_killswitch")
    assert ["STRUCTURE_WAIT_TRIGGER"] in tags("t2_structure")
    assert all(["TOTAL_OPEN_RISK"] in tags(n) for n in ("d4_donchian_20_10_capacity", "t2_trend_regime_capacity"))
    assert tags("box_0.4_floor_0.5") == [] and all("BOX_MIN_STOP_PCT" in t for n in ("box_0.4_floor_0.32", "box_0.6_floor_0.5",
                                                                                      "box_0.6_floor_0.32") for t in tags(n))
    assert tags("pt_rr_floor") == [["RR_BELOW_MIN_AFTER_ROUNDING", "RR_BELOW_MIN_AT_ENTRY"]] and tags("pt_policy") == [[]]
    assert tags("pt_rounding") == [["RISK_ABOVE_CAP_AFTER_ROUNDING"]]
    assert sum(1 for t in tags("pt_scan") if "MAX_POSITIONS" in t) == 2
    assert all(t == ["NEGATIVE_NET_EDGE"] for n in ("main_neg", "main_killswitch") for t in tags(n)) and f["main_neg"]["opened"] == 2
    assert ["RESEARCH_SIZE_ONLY", "TOTAL_OPEN_RISK"] in tags("main_res_capacity")
    assert ["TOTAL_OPEN_RISK"] in tags("main_capacity") and tags("main_policy") == [[], []]


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
    assert {k: v for k, v in rec.items() if k not in ("main", "xp")} == {k: v for k, v in opn.items() if k not in ("main", "xp")}
    # mühürlü toplayıcı (kod değişmeden) yalnız-kayıt karşı-olgusallarını `xp_cf` satırı olarak taşır
    assert sorted([s, r] for _b, s, r in rec["xp"]["cf"] if r[0] == RO) == sorted(rec["main"]["cf"])


SELECTIVITY_DIRECT = {"d4_universe", "d4_killswitch", "t2_structure", "pt_rr_floor", "main_neg", "main_killswitch",
                      "main_res_capacity"}


def test_record_selectivity_on_the_direct_paths_changes_only_the_selectivity_scenarios(runs):
    rec, opn = runs[("direct", "record_selectivity")]["facts"], runs[("direct", "open")]["facts"]
    assert set(rec) == set(opn) and SELECTIVITY_DIRECT < set(opn)
    for name in sorted(set(opn) - SELECTIVITY_DIRECT):                 # kapasite, politika, Box istisnası: aynı
        assert rec[name] == opn[name], name
    for name in sorted(SELECTIVITY_DIRECT):
        was = sorted(s for s, t in opn[name]["open"] if t and LM.classify_unlock_codes(t) == "selectivity")
        now_open = sorted(s for s, _t in rec[name]["open"])
        assert was and not set(was) & set(now_open), name
        assert sorted(s for s, r in rec[name]["cf"] if r[0] == RO) == was, name
        kept = sorted(s for s, t in opn[name]["open"] if not (t and LM.classify_unlock_codes(t) == "selectivity"))
        assert now_open == kept, name                                   # politika/kapasite girişleri yine açık
    # kill switch kaydı (aynı sinyal) yalnız-kayda DÖNÜŞÜR: eski neden arkada kalır, ikinci kayıt yazılmaz
    assert rec["d4_killswitch"]["cf"] == [["SOL/USDT", [RO, "BOOK_UNIVERSE", "KILL_SWITCH_ACTIVE"]]]
    assert all(r == [RO, "NEGATIVE_NET_EDGE", "KILL_SWITCH_ACTIVE"] for _s, r in rec["main_killswitch"]["cf"])
