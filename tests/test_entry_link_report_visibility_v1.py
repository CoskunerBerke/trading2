"""1A bağ gözlenebilirliğinin RAPORA ulaşması (`entry_selectivity.json.snapshot_cycle`).

Doğrulanmış kusur: `_entry_flush` `links` / `link_events` / `link_health` alanlarını ölçüyor
(`_entry_cycle`), fakat `_write_entry_eval` bu üç alanı kısıtlayıcı bir beyaz listeyle
DÜŞÜRÜYORDU → raporda hiç görünmüyorlardı. Kanonik JSONL bağı (`entry_snapshot.jsonl`)
bu süre boyunca DOĞRU yazılıyordu; kusur yalnız görünürlükteydi.

Bu paket dört şeyi kanıtlar:

* başarılı bağ rapora ulaşır,
* "işlem açılmadı" (`link_not_needed`) ile "bağ yazılamadı" (`link_failed`) raporda da ayrıdır,
* arıza gerekçesi (`LINK_WRITE_FAILED`) raporda görünür kalır,
* kanonik JSONL otoritedir: rapor bağı UYDURMAZ, JSONL'de olmayan bağ raporda "yazıldı" olamaz.

Bağ üretimi, zamanlaması, kimlikleri ve snapshot şeması DEĞİŞMEDİ (regresyon: mevcut
`test_entry_link_observability_v1` paketi aynen yeşil kalır).
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from tradingbot.engine_v3 import TradingEngineV3
from tradingbot.learn.entry_challenger import EntryChallengerConfig
from tradingbot.learn.entry_snapshot import EntrySnapshotStore

REQUIRED = ("links", "link_events", "link_health")


# ------------------------------------------------------------------ saf özet

def test_summary_passes_the_three_link_fields_and_existing_keys():
    cyc = {"at": "t", "candidates": 2, "written": 2, "appended": 2, "duplicates": 0,
           "errors": 0, "mode": "SHADOW",
           "links": {"link_attempted": 1, "link_written": 1, "link_failed": 0,
                     "link_duplicate": 0, "link_not_needed": 1,
                     "link_skipped_no_snapshot": 0},
           "link_events": [{"trade_id": "F1", "candidate_id": "c1", "code": "LINK_OK"}],
           "link_health": "OK",
           "rotation": {"archived": 0}}          # bu alan raporda İSTENMİYOR (mevcut sözleşme)
    out = TradingEngineV3._entry_cycle_summary(cyc)
    for k in REQUIRED:
        assert k in out, k
    assert out["links"]["link_written"] == 1 and out["links"]["link_not_needed"] == 1
    assert out["link_events"][0]["code"] == "LINK_OK"
    assert out["link_health"] == "OK"
    for k in ("at", "candidates", "written", "appended", "duplicates", "errors", "mode"):
        assert out[k] == cyc[k]
    assert "rotation" not in out, "beyaz liste genişletildi ama kapsam DIŞI alan sızdı"


def test_summary_never_fabricates_missing_link_fields():
    """Ölçülmemiş alan rapora `null` olarak bile GİRMEZ — 'ölçülmedi' 'sıfır' değildir."""
    out = TradingEngineV3._entry_cycle_summary({"at": "t", "candidates": 0})
    for k in REQUIRED:
        assert k not in out
    assert TradingEngineV3._entry_cycle_summary(None) == {}


# ------------------------------------------------------------------ uçtan uca (flush → rapor)

class _Plan:
    valid = True
    entry = 100.0
    stop = 95.0
    targets = (110.0,)
    entry_type = "kirilim"
    expected_r = 2.0
    rr = 2.0


class _Decision:
    direction = "LONG"
    specialist_reports = ()
    opportunity: dict = {}
    score = 0.4


def _cand(symbol: str, *, trade_id: str | None) -> dict:
    return {"symbol": symbol, "direction": "LONG", "decision": _Decision(), "plan": _Plan(),
            "chief": {}, "market": "USDM_PERP", "rank": 0, "specialists": None,
            "features": {"atr_pct": 1.0}, "daily_frame": None, "intraweek_frame": None,
            "ts": None,
            "entry_log": {"risk_allowed": True, "risk_reasons": [],
                          "trade_id": trade_id, "block_code": None}}


def _engine(tmp_path: Path) -> types.SimpleNamespace:
    """`_entry_flush` + `_write_entry_eval` için asgari sahte motor. Aktif yol ÇAĞRILMAZ."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = EntrySnapshotStore(tmp_path / "entry_snapshot.jsonl", max_per_cycle=50)
    eng = types.SimpleNamespace(
        entry_snapshot_store=store, _entry_pending=[], run_id="testrun", _tour_no=1,
        _journal_cycle=1, entry_cfg=EntryChallengerConfig(), entry_mode="SHADOW",
        weekly_cfg=None, mtf_cfg=None, mtf_mode="SHADOW",
        code_sha=lambda: "deadbeef", config_hash=lambda: "cfg123",
        _attach_weekly_context=lambda snap, rec: None,
        _attach_mtf_context=lambda snap, rec: None,
        cfg=types.SimpleNamespace(
            state_path=tmp_path,
            v3=types.SimpleNamespace(entry_selectivity=types.SimpleNamespace(
                include_legacy_memory=False))),
        ledger2=types.SimpleNamespace(history=[], summary=lambda _m: {"equity": 100.0}),
        profile=types.SimpleNamespace(max_total_open_risk_pct=6.0),
        memory=types.SimpleNamespace(iter_rows=lambda: []),
        _entry_replay_audit=lambda snaps, links, closes: {"state": "stub"},
        _entry_eval_v2=lambda closes, snaps, links: {},
        _write_mtf_eval=lambda closes, snaps, links, now: {},
    )
    eng._entry_cycle_summary = TradingEngineV3._entry_cycle_summary
    return eng


def _run(eng) -> dict:
    from tradingbot.core import utc_now
    now = utc_now()
    TradingEngineV3._entry_flush(eng, now)
    TradingEngineV3._write_entry_eval(eng, now)
    p = eng.cfg.state_path / "entry_selectivity.json"
    assert p.exists(), "rapor yazılmadı"
    return json.loads(p.read_text(encoding="utf-8"))


def test_successful_link_reaches_the_report(tmp_path: Path):
    eng = _engine(tmp_path)
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1")]
    doc = _run(eng)
    cyc = doc["snapshot_cycle"]
    for k in REQUIRED:
        assert k in cyc, f"{k} rapora ulaşmadı"
    assert cyc["links"]["link_written"] == 1
    assert cyc["links"]["link_failed"] == 0
    assert cyc["link_health"] == "OK"
    assert any(e["code"] == "LINK_OK" and e["trade_id"] == "F1" for e in cyc["link_events"])
    # Mevcut alanlar KORUNDU.
    for k in ("at", "candidates", "written", "appended", "duplicates", "errors", "mode"):
        assert k in cyc, k


def test_no_link_needed_and_link_failure_are_distinct_in_the_report(tmp_path: Path):
    eng = _engine(tmp_path)
    eng.entry_snapshot_store.link_trade = lambda *a, **k: False       # yazım BAŞARISIZ
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1"), _cand("ETH/USDT", trade_id=None)]
    cyc = _run(eng)["snapshot_cycle"]
    assert cyc["links"]["link_failed"] == 1
    assert cyc["links"]["link_not_needed"] == 1
    assert cyc["links"]["link_written"] == 0
    assert cyc["link_health"] == "LINK_WRITE_FAILED"


def test_failure_reason_code_stays_visible_in_the_report(tmp_path: Path):
    eng = _engine(tmp_path)

    def _boom(*a, **k):
        raise OSError("disk full")

    eng.entry_snapshot_store.link_trade = _boom
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1")]
    cyc = _run(eng)["snapshot_cycle"]
    codes = [e["code"] for e in cyc["link_events"]]
    assert "LINK_WRITE_FAILED" in codes
    assert "LINK_OK" not in codes
    assert cyc["link_health"] == "LINK_WRITE_FAILED"


def test_canonical_jsonl_remains_authoritative(tmp_path: Path):
    """Rapor bağı UYDURMAZ: JSONL'de yazılan bağ = raporun 'yazıldı' dediği bağ."""
    eng = _engine(tmp_path)
    st = eng.entry_snapshot_store
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1"), _cand("SOL/USDT", trade_id="F2"),
                          _cand("ETH/USDT", trade_id=None)]
    cyc = _run(eng)["snapshot_cycle"]
    links = st.trade_links()                     # KANONİK kaynak
    assert set(links) == {"F1", "F2"}
    assert cyc["links"]["link_written"] == len(links)
    ok = {e["trade_id"]: e["candidate_id"] for e in cyc["link_events"] if e["code"] == "LINK_OK"}
    assert ok == links, "rapor ile kanonik JSONL bağı AYRIŞTI"
    # Başarısız bağ JSONL'de YOK ve rapor da onu 'yazıldı' göstermez.
    eng2 = _engine(tmp_path / "b")
    eng2.entry_snapshot_store.link_trade = lambda *a, **k: False
    eng2._entry_pending = [_cand("BTC/USDT", trade_id="F9")]
    cyc2 = _run(eng2)["snapshot_cycle"]
    assert eng2.entry_snapshot_store.trade_links() == {}
    assert cyc2["links"]["link_written"] == 0
    assert not any(e["code"] == "LINK_OK" for e in cyc2["link_events"])


def test_link_ids_timing_and_snapshot_schema_are_unchanged(tmp_path: Path):
    """Düzeltme yalnız RAPOR beyaz listesindedir; JSONL satırları BİREBİR aynı biçimde."""
    eng = _engine(tmp_path)
    eng._entry_pending = [_cand("BTC/USDT", trade_id="F1")]
    _run(eng)
    rows = [json.loads(ln) for ln in
            (tmp_path / "entry_snapshot.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    snap = [r for r in rows if r.get("kind") != "link"]
    link = [r for r in rows if r.get("kind") == "link"]
    assert len(snap) == 1 and len(link) == 1
    assert rows.index(snap[0]) < rows.index(link[0]), "snapshot satırı bağdan ÖNCE gelir"
    assert set(link[0]) == {"schema_version", "kind", "candidate_id", "trade_id", "linked_at"}
    assert link[0]["candidate_id"] == snap[0]["candidate_id"]
    assert snap[0]["provenance"]["written_at_stage"] == "RANKING"
    assert snap[0]["provenance"]["sees_outcome"] is False
    assert "links" not in snap[0] and "link_health" not in snap[0]


@pytest.mark.parametrize("key", REQUIRED)
def test_whitelist_constant_contains_each_link_field(key):
    assert key in TradingEngineV3.ENTRY_CYCLE_REPORT_KEYS
