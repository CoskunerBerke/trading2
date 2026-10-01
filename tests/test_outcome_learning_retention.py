"""Outcome Learning — Lossless Retention Audit.

Tek şart burada sabitlenir: **değerlendirilen hiçbir aday ve hiçbir sonuç sessizce
kaybolamaz.** Aktif dosya performans için sınırlı kalabilir; taşan kayıtlar ÖNCE
sıkıştırılmış + checksum'lı bir segmente mühürlenir, ANCAK ondan sonra aktif dosyadan
çıkarılır. Arşiv başarısızsa budama YAPILMAZ.

Testler helper düzeyinde kalmaz: gerçek `DecisionJournal`, gerçek `ShadowBook`, gerçek
`SegmentArchive` ve gerçek `TradingEngineV3.tour()` zinciri kullanılır.
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest
from test_engine_v3 import _engine

from tradingbot.learn.decision_journal import (KIND_DECISION, KIND_OUTCOME, DecisionJournal,
                                               build_outcome_link)
from tradingbot.learn.experience import prepare_pool, query_pool
from tradingbot.learn.journal_archive import (ARCHIVE_SCHEMA_VERSION, ArchiveError, SegmentArchive)
from tradingbot.learn.shadow import ShadowBook

UTC_TS = "2026-03-0{d}T{h:02d}:00:00+00:00"


# ------------------------------------------------------------------ yardımcılar (üretim tipleri)

def _archive(tmp_path: Path, name: str = "arch", **kw) -> SegmentArchive:
    return SegmentArchive(tmp_path / name, stream_id=kw.pop("stream_id", "decision_journal"),
                          record_schema_version="decision_journal_v1", **kw)


def _journal(tmp_path: Path, *, max_lines: int, archive: SegmentArchive | None) -> DecisionJournal:
    return DecisionJournal(tmp_path / "decision_journal.jsonl", max_lines=max_lines,
                           archive=archive)


def _dec(i: int, *, run: str = "run_a") -> dict:
    return {"schema_version": "decision_journal_v1", "kind": KIND_DECISION,
            "decision_id": f"{run}_d{i:06d}", "run_id": run, "cycle_id": i,
            "symbol": f"S{i % 7}/USDT", "direction": "LONG",
            "decision_ts": UTC_TS.format(d=1 + (i // 24) % 9, h=i % 24),
            "outcome_kind": "REJECTED", "outcome_stage": "risk_engine",
            "features": {"atr_pct": 0.3 + i * 0.001, "rr": 2.0},
            "trade_id": f"t{i:06d}" if i % 5 == 0 else None}


def _out(i: int) -> dict:
    return {"schema_version": "decision_journal_v1", "kind": KIND_OUTCOME,
            "trade_id": f"t{i:06d}", "decision_id": f"run_a_d{i:06d}",
            "outcome_ts": UTC_TS.format(d=1 + (i // 24) % 9, h=i % 24),
            "r_multiple": 0.5, "net_pnl": 1.0, "provenance": "paper_ledger_close"}


def _hot_lines(j: DecisionJournal) -> list[str]:
    if not j.path.exists():
        return []
    return [x for x in j.path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ================================================================== 1) rotasyonda kayıp yok

def test_1_forced_rotation_loses_no_record(tmp_path: Path):
    """Çok küçük aktif limitle rotasyon defalarca zorlanır; TEK kayıt bile kaybolmaz."""
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=10, archive=arc)
    written = []
    for i in range(250):
        assert j.append_decision(_dec(i)) is True
        written.append(_dec(i)["decision_id"])
        if i % 7 == 0:
            j.rotate()
    j.rotate()

    assert len(_hot_lines(j)) <= 10, "aktif dosya sınırlı kalmalı"
    got = [r["decision_id"] for r in j.iter_all_rows()]
    assert got == written, "arşiv + aktif birleşimi giriş sırasıyla BİREBİR aynı olmalı"
    st = j.retention_stats()
    assert st["lifetime_records"] == 250
    assert st["silent_deletion"] is False
    assert st["deleted_segments"] == 0
    assert st["retention_policy"] == "UNLIMITED_NO_DELETION"


# ================================================================== 2) birleşim == girdi

def test_2_union_of_active_and_archive_equals_input(tmp_path: Path):
    """Aktif + BÜTÜN segmentlerin birleşimi girdi kayıtlarıyla birebir aynıdır (alan alan)."""
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=25, archive=arc)
    src = []
    for i in range(180):
        rec = _dec(i)
        src.append(rec)
        j.append_decision(rec)
        if i % 11 == 0:
            j.rotate()
    j.rotate()

    from_archive = list(arc.iter_rows())
    from_hot = [json.loads(x) for x in _hot_lines(j)]
    union = from_archive + from_hot
    assert len(union) == len(src)
    assert union == src, "hiçbir alan yolda değişmemeli"


# ================================================================== 3) retry → duplicate yok

def test_3_repeated_decision_and_outcome_never_duplicate(tmp_path: Path):
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=8, archive=arc)
    for _ in range(4):                                   # aynı kayıt 4 kez denenir
        j.append_decision(_dec(1))
        j.append_outcome(_out(1))
    for i in range(2, 40):
        j.append_decision(_dec(i))
    j.rotate()
    j.append_decision(_dec(1))                           # rotasyondan SONRA tekrar dene
    j.append_outcome(_out(1))

    rows = list(j.iter_all_rows())
    dec_ids = [r["decision_id"] for r in rows if r.get("kind") == KIND_DECISION]
    out_ids = [r["trade_id"] for r in rows if r.get("kind") == KIND_OUTCOME]
    assert len(dec_ids) == len(set(dec_ids)), "aynı decision_id iki kez okunmamalı"
    assert len(out_ids) == len(set(out_ids)), "aynı trade_id için iki outcome olmamalı"

    # Aynı bloğun yeniden mühürlenmesi AYNI segmenti verir (içerik türevli kimlik).
    block = [json.dumps(_dec(900)), json.dumps(_dec(901))]
    m1 = arc.seal(block)
    m2 = arc.seal(block)
    assert m1["segment_id"] == m2["segment_id"] and m1["sha256"] == m2["sha256"]
    arc.commit(m1)
    arc.commit(m2)
    ids = [s["segment_id"] for s in arc.manifest()["segments"]]
    assert len(ids) == len(set(ids)), "manifest aynı segmenti iki kez taşımamalı"


# ================================================================== 4) rotasyon ortasında çökme

def test_4_crash_between_seal_and_trim_is_recovered(tmp_path: Path):
    """Segment mühürlendi + manifest işlendi ama budama yapılamadan süreç öldü.

    Yeniden başlatmada `pending_trim` tamamlanır: ne kayıp ne çift kayıt olur.
    """
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=10, archive=arc)
    for i in range(45):
        j.append_decision(_dec(i))

    lines = _hot_lines(j)
    cut = len(lines) - 10
    meta = arc.seal(lines[:cut])
    arc.commit(meta, pending_trim={"segment_id": meta["segment_id"], "n_lines": cut,
                                   "block_sha256": meta["block_sha256"]})
    # ---- burada "çökme": budama YAPILMADI, aktif dosya hâlâ tam ----
    assert len(_hot_lines(j)) == 45
    assert arc.pending_trim() is not None

    j2 = _journal(tmp_path, max_lines=10, archive=_archive(tmp_path))   # yeniden başlatma
    j2.load_seen()
    j2.rotate()
    assert len(_hot_lines(j2)) <= 10
    assert arc.pending_trim() is None, "bekleyen budama temizlenmeli"
    ids = [r["decision_id"] for r in j2.iter_all_rows()]
    assert ids == [_dec(i)["decision_id"] for i in range(45)]
    assert len(ids) == len(set(ids)) == 45


def test_4b_crash_after_trim_clears_pending_without_second_deletion(tmp_path: Path):
    """Budama tamamlandı ama `pending_trim` temizlenemeden çökülürse İKİNCİ budama olmaz."""
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=10, archive=arc)
    for i in range(45):
        j.append_decision(_dec(i))
    j.rotate()
    after = _hot_lines(j)
    seg = arc.manifest()["segments"][-1]
    # pending_trim'i elle geri koy (temizleme adımından önce çökmüş gibi)
    doc = arc.manifest()
    doc["pending_trim"] = {"segment_id": seg["segment_id"], "n_lines": seg["n_records"],
                           "block_sha256": seg["block_sha256"]}
    arc._write_manifest(doc)

    j3 = _journal(tmp_path, max_lines=10, archive=_archive(tmp_path))
    j3.load_seen()
    j3.rotate()
    assert _hot_lines(j3) == after, "ikinci kez budama YAPILMAMALI"
    assert len(list(j3.iter_all_rows())) == 45


# ================================================================== 5) arşiv hatası → budama yok

def test_5_archive_failure_does_not_trim_active_journal(tmp_path: Path, monkeypatch):
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=5, archive=arc)
    for i in range(30):
        j.append_decision(_dec(i))

    def boom(_lines):
        raise ArchiveError("disk full")

    monkeypatch.setattr(arc, "seal", boom)
    res = j.rotate()
    assert res["health"] == "ARCHIVE_FAILED" and res["error"]
    assert res["archived"] == 0 and res["trimmed"] == 0
    assert len(_hot_lines(j)) == 30, "arşiv başarısızken AKTİF DOSYA BUDANMAMALI"
    assert j.retention_stats()["archive_health"] == "ARCHIVE_FAILED"


def test_5b_checksum_failure_blocks_trim(tmp_path: Path, monkeypatch):
    """Checksum doğrulaması düşerse segment yazılmaz ve aktif dosya budanmaz."""
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=5, archive=arc)
    for i in range(30):
        j.append_decision(_dec(i))
    monkeypatch.setattr(SegmentArchive, "_verify_payload", staticmethod(lambda *a, **k: False))
    res = j.rotate()
    assert res["health"] == "ARCHIVE_FAILED"
    assert len(_hot_lines(j)) == 30
    assert not list((tmp_path / "arch" / "segments").glob("seg-*.jsonl.gz")), \
        "doğrulanamayan segment diske KALICI olarak konmamalı"


# ================================================================== 6) eşzamanlı yazım

def test_6_concurrent_appends_lose_no_record(tmp_path: Path):
    """Çok iş parçacıklı append + araya giren rotasyon: tek kayıt bile düşmez."""
    import threading

    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=40, archive=arc)
    n_threads, per = 6, 60
    errors: list[BaseException] = []

    def worker(t: int):
        try:
            for i in range(per):
                j.append_decision(_dec(t * 1000 + i))
        except BaseException as exc:                      # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for _ in range(4):
        j.rotate()
        time.sleep(0.005)
    for th in threads:
        th.join()
    j.rotate()

    assert not errors
    ids = [r["decision_id"] for r in j.iter_all_rows()]
    assert len(ids) == n_threads * per == len(set(ids))


# ================================================================== 7) karışık şema

def test_7_mixed_legacy_and_new_schema_rows_are_preserved(tmp_path: Path):
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=6, archive=arc)
    legacy = {"kind": KIND_DECISION, "decision_id": "legacy_1", "symbol": "OLD/USDT"}
    j.append_decision(legacy)
    j.append_decision({"kind": KIND_DECISION, "decision_id": "legacy_2",
                       "schema_version": "decision_journal_v0", "symbol": "OLD2/USDT"})
    for i in range(20):
        j.append_decision(_dec(i))
    j.rotate()

    rows = list(j.iter_all_rows())
    assert rows[0] == legacy, "eski şema satırı OLDUĞU GİBİ korunmalı"
    assert rows[1]["schema_version"] == "decision_journal_v0"
    assert len(rows) == 22


# ================================================================== 8) bozuk segment

def test_8_corrupt_segment_is_detected_and_excluded_from_learning(tmp_path: Path):
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=5, archive=arc)
    for i in range(40):
        j.append_decision(_dec(i))
        if i % 9 == 0:
            j.rotate()
    j.rotate()
    segs = arc.manifest()["segments"]
    assert len(segs) >= 2
    assert arc.verify()["health"] == "OK"

    victim = arc.segments_dir / segs[0]["file"]
    victim.write_bytes(gzip.compress(b'{"kind": "decision", "decision_id": "TAMPERED"}\n'))

    v = arc.verify()
    assert v["health"] == "DEGRADED" and segs[0]["segment_id"] in v["corrupt"]
    ids = [r.get("decision_id") for r in j.iter_all_rows()]
    assert "TAMPERED" not in ids, "checksum'ı tutmayan segment ÖĞRENMEYE KATILMAMALI"


# ================================================================== 9) no-lookahead

def test_9_no_lookahead_holds_even_when_outcome_is_archived(tmp_path: Path):
    """Gelecekte etiketlenmiş sonuç arşivde olsa bile `as_of` filtresini geçemez."""
    arc = SegmentArchive(tmp_path / "sh", stream_id="shadow_book",
                         record_schema_version="shadow_trade_v1")
    book = ShadowBook(tmp_path / "shadow_book.json", archive=arc)
    book.MAX_TRADES = 3
    from tradingbot.learn.shadow import ShadowTrade
    for i in range(9):
        book.trades.append(ShadowTrade(
            id=f"sh{i}", plan_id=f"p{i}", symbol="BTC/USDT", market_type="futures",
            direction="LONG", created_at=f"2026-03-01T{i:02d}:00:00+00:00", entry=100.0,
            stop=95.0,
            targets=[110.0], horizon_bars=4, variant="as_planned", reason_not_opened=["X"],
            label_ts="2026-03-02T00:00:00+00:00",
            outcome={"r_multiple": 1.0, "won": True},
            labeled_at="2026-03-05T00:00:00+00:00" if i < 5 else "2026-03-01T06:00:00+00:00"))
    book.save()
    assert book.stats()["archived"] > 0, "bu senaryoda arşive kayıt taşınmış olmalı"

    all_trades = list(book.iter_all_trades())
    assert len(all_trades) == 9, "arşiv + aktif birleşimi tam olmalı"

    as_of = int(__import__("datetime").datetime(
        2026, 3, 2, tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
    prepared = prepare_pool(memory_rows=[], shadow_trades=all_trades)
    hits = query_pool(prepared, {"symbol": "BTC/USDT", "direction": "LONG"},
                      as_of_ms=as_of, top_k=50)
    assert hits, "as_of'tan ÖNCE etiketlenmiş deneyimler havuzda olmalı"
    for h in hits:
        assert h.label_ts_ms is not None and h.label_ts_ms <= as_of, "gelecek sonuç sızdı"


# ================================================================== 10) çift sayım yok

def test_10_real_and_shadow_across_active_and_archive_never_double_count(tmp_path: Path):
    """Aynı outcome hem arşivde hem aktifte görünse de TEK deneyim sayılır."""
    arc = SegmentArchive(tmp_path / "sh", stream_id="shadow_book",
                         record_schema_version="shadow_trade_v1")
    book = ShadowBook(tmp_path / "shadow_book.json", archive=arc)
    book.MAX_TRADES = 2
    from tradingbot.learn.shadow import ShadowTrade
    for i in range(6):
        book.trades.append(ShadowTrade(
            id=f"sh{i}", plan_id=f"p{i}", symbol="ETH/USDT", market_type="futures",
            direction="LONG", created_at=f"2026-03-01T0{i}:00:00+00:00", entry=100.0, stop=95.0,
            targets=[110.0], horizon_bars=4, variant="as_planned", reason_not_opened=["X"],
            label_ts="2026-03-02T00:00:00+00:00", outcome={"r_multiple": 1.0},
            labeled_at="2026-03-02T00:00:00+00:00"))
    book.save()

    merged = list(book.iter_all_trades())
    assert len(merged) == 6 and len({t["id"] for t in merged}) == 6

    # gerçek PAPER kaydı aynı adayı temsil ediyorsa gölge sürüm havuza EKLENMEZ
    mem = [{"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "as_planned",
            "opened_at": "2026-03-01T00:00:00+00:00", "recorded_at": "2026-03-01T00:00:00+00:00",
            "features": {"atr_pct": 0.3},
            "outcome": {"r_multiple": 2.0, "closed_at": "2026-03-02T00:00:00+00:00",
                        "opened_at": "2026-03-01T00:00:00+00:00"}}]
    pool = prepare_pool(memory_rows=mem, shadow_trades=merged)
    ids = [e.outcome_id for e in pool.experiences]
    assert len(ids) == len(set(ids)), "aynı kimlik iki deneyim üretmemeli"
    real = [e for e in pool.experiences if e.source == "REAL_PAPER"]
    assert len(real) == 1 and real[0].r_multiple == 2.0, "gerçek kayıt gölgeyi yenmeli"


# ================================================================== 11) SHADOW baseline

def test_11_retention_does_not_change_shadow_baseline(tmp_path: Path, monkeypatch):
    """Arşivleme açıkken üretim turu SHADOW baseline'ını AYNEN korur."""
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    assert eng.influence_cfg.mode == "SHADOW"
    eng.tour(do_scan=False, obsidian=False, charts=False)
    j = eng.decision_journal
    assert j is not None and j.archive is not None, "üretimde arşiv bağlı olmalı"
    rows = [r for r in j.iter_all_rows() if r.get("learning_influence")]
    for r in rows:
        li = r["learning_influence"]
        assert li.get("mode") == "SHADOW"
        assert li.get("applied") is not True, "SHADOW modda etki UYGULANMAMALI"


# ================================================================== 12) risk/emir izolasyonu

def test_12_retention_layer_touches_no_risk_or_order_path(tmp_path: Path, monkeypatch):
    """Arşiv katmanı risk/emir/stop/TP/sizing yollarına DOKUNMAZ."""
    src = Path("tradingbot/learn/journal_archive.py").read_text(encoding="utf-8")
    for forbidden in ("RiskEngine", "ledger", "outbox", "gateway", "place_order", "submit",
                      "stop_loss", "take_profit", "leverage", "/opt/"):
        assert forbidden not in src, f"arşiv modülü {forbidden!r} ile ilgilenmemeli"

    eng = _engine(tmp_path, monkeypatch, symbols=3)
    before = (eng.ledger2.summary(), len(eng.ledger2.positions),
              eng.ledger2.history_dicts(), eng.spot2.history_dicts())
    eng.decision_journal.rotate()
    eng.decision_journal.rotate()
    after = (eng.ledger2.summary(), len(eng.ledger2.positions),
             eng.ledger2.history_dicts(), eng.spot2.history_dicts())
    assert before == after, "rotasyon defteri/pozisyonları DEĞİŞTİREMEZ"


def test_12b_hot_loop_never_scans_the_archive(tmp_path: Path, monkeypatch):
    """Aday başına arşiv TARANMAZ: arşiv okuması patlasa bile üretim turu tamamlanır."""
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    j = eng.decision_journal
    assert j is not None and j.archive is not None
    for i in range(60):                                  # arşivde gerçek segmentler olsun
        j.append_decision(_dec(i))
    j.max_lines = 10
    j.rotate()
    assert j.retention_stats()["n_segments"] >= 1

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("SICAK DÖNGÜ ARŞİVİ OKUMAMALI")

    monkeypatch.setattr(SegmentArchive, "iter_rows", boom)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s is not None
    assert calls["n"] == 0, "tur sırasında arşiv satırları okunmamalı"


# ================================================================== 13) dashboard 500 üretmez

@pytest.mark.parametrize("scenario", ["missing", "corrupt", "legacy", "empty_totals"])
def test_13_dashboard_never_500s_on_bad_archive_metadata(tmp_path: Path, scenario: str):
    from tradingbot.dashboard.state import StateReader
    st = tmp_path / "state"
    st.mkdir()
    (st / "decision_journal.jsonl").write_text(
        json.dumps(_dec(1)) + "\n" + json.dumps(_dec(2)) + "\n", encoding="utf-8")
    adir = st / "decision_archive"
    if scenario != "missing":
        adir.mkdir()
        payload = {"corrupt": "{ this is not json",
                   "legacy": json.dumps({"schema_version": "journal_archive_v0",
                                         "segments": []}),
                   "empty_totals": json.dumps({"schema_version": ARCHIVE_SCHEMA_VERSION,
                                               "segments": [], "totals": None})}[scenario]
        (adir / "manifest.json").write_text(payload, encoding="utf-8")

    view = StateReader(st).decision_retention()
    assert isinstance(view, dict)
    assert view["hot_records"] == 2
    assert view["silent_deletion"] is False
    assert isinstance(view["lifetime_records"], int)


def test_13b_learning_loop_api_survives_bad_archive(tmp_path: Path, monkeypatch):
    """`/api/learning-loop` bozuk arşiv metadata'sında 200 döner (500 YOK)."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    st = Path(eng.cfg.state_path)
    adir = st / "decision_archive"
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "manifest.json").write_text("{ bozuk", encoding="utf-8")

    client = TestClient(create_app(st, Path(eng.cfg.cache_path), None, DashboardConfig()))
    r = client.get("/api/learning-loop")
    assert r.status_code == 200
    body = r.json()
    assert body.get("retention", {}).get("silent_deletion") is False
    assert client.get("/quant").status_code == 200


def test_13c_production_tour_publishes_retention_alarm(tmp_path: Path, monkeypatch):
    """Gerçek tur `decision_funnel.json` içine saklama durumunu yazar (arşiv düşse bile görünür)."""
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    doc = json.loads((Path(eng.cfg.state_path) / "decision_funnel.json").read_text(encoding="utf-8"))
    rt = doc.get("retention") or {}
    assert rt.get("silent_deletion") is False
    assert rt.get("retention_policy") == "UNLIMITED_NO_DELETION"
    assert rt.get("lifetime_records", 0) >= 1
    assert rt.get("last_rotation_error") is None


def test_13d_preexisting_oversized_journal_is_archived_not_truncated(tmp_path: Path):
    """Yükseltme yolu: arşivden ÖNCE yazılmış büyük bir günlük ilk rotasyonda ARŞİVLENİR.

    Eski `prune()` bu satırları doğrudan atardı; yeni sözleşmede hepsi korunur.
    """
    path = tmp_path / "decision_journal.jsonl"
    legacy = [json.dumps(_dec(i)) for i in range(300)]
    path.write_text(chr(10).join(legacy) + chr(10), encoding="utf-8")

    arc = _archive(tmp_path)
    j = DecisionJournal(path, max_lines=20, archive=arc)
    j.load_seen()
    assert j.retention_stats()["hot_records"] == 300
    j.rotate()

    assert len(_hot_lines(j)) == 20
    ids = [r["decision_id"] for r in j.iter_all_rows()]
    assert ids == [_dec(i)["decision_id"] for i in range(300)], "eski kayıtların TAMAMI korunmalı"
    assert j.retention_stats()["lifetime_records"] == 300


# ================================================================== 14) 100k benchmark

@pytest.mark.slow
def test_14_hundred_thousand_record_rotation_is_bounded(tmp_path: Path):
    """100.000 kayıt: rotasyon sınırlı sürede biter ve sıcak yol arşivi TARAMAZ."""
    arc = _archive(tmp_path)
    j = _journal(tmp_path, max_lines=20_000, archive=arc)

    t0 = time.perf_counter()
    for i in range(100_000):
        j.append_decision(_dec(i))
        if i % 20_000 == 0 and i:
            j.rotate()
    append_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    j.rotate()
    rotate_s = time.perf_counter() - t1

    st = j.retention_stats()
    assert st["lifetime_records"] == 100_000, "100k kaydın tamamı korunmalı"
    assert st["hot_records"] <= 20_000

    # `retention_stats` yalnız manifest okur → segment sayısından BAĞIMSIZ (O(1)).
    t2 = time.perf_counter()
    for _ in range(50):
        j.retention_stats()
    stats_s = (time.perf_counter() - t2) / 50
    assert stats_s < 0.05, f"özet O(1) olmalı, ölçülen {stats_s:.4f}s"

    # Aday basina maliyet: append arsiv BUYUKLUGUNDEN BAGIMSIZDIR (arsiv taranmaz).
    samples = []
    for i in range(2_000):
        t3 = time.perf_counter()
        j.append_decision(_dec(500_000 + i))
        samples.append(time.perf_counter() - t3)
    samples.sort()
    p50, p95 = samples[len(samples) // 2], samples[int(len(samples) * 0.95)]
    assert p95 < 0.01, f"append p95 sinirli olmali, olculen {p95:.5f}s"
    assert st["n_segments"] >= 4, "benchmark birden cok segment uretmeli"

    print(f"\n[bench] append(100k)={append_s:.2f}s rotate={rotate_s:.2f}s "
          f"stats_p50={stats_s * 1000:.3f}ms append_p50={p50 * 1000:.3f}ms "
          f"append_p95={p95 * 1000:.3f}ms segments={st['n_segments']} "
          f"gz={st['archive_bytes_compressed'] / 1e6:.2f}MB "
          f"raw={st['archive_bytes_raw'] / 1e6:.2f}MB "
          f"ratio={st['archive_bytes_raw'] / max(1, st['archive_bytes_compressed']):.1f}x")


# ================================================================== 15) backup / restore

def test_15_backup_restore_preserves_lifetime_records_and_checksums(tmp_path: Path):
    """Yedek aktif günlük + arşiv + manifesti BİRLİKTE taşır; restore sonrası hepsi aynıdır."""
    from tradingbot.ops.backup import restore_backup, run_backup, verify_backup

    st = tmp_path / "state"
    st.mkdir()
    arc = SegmentArchive(st / "decision_archive", stream_id="decision_journal",
                         record_schema_version="decision_journal_v1")
    j = DecisionJournal(st / "decision_journal.jsonl", max_lines=15, archive=arc)
    for i in range(120):
        j.append_decision(_dec(i))
        if i % 5 == 0:
            j.append_outcome(build_outcome_link(
                trade_id=f"t{i:06d}", outcome={"closed_at": "2026-03-02T00:00:00+00:00",
                                               "r_multiple": 0.4, "net_pnl": 1.0},
                decision_id=f"run_a_d{i:06d}"))
        if i % 13 == 0:
            j.rotate()
    j.rotate()

    before_rows = list(j.iter_all_rows())
    before_stats = j.retention_stats()
    before_sha = {s["segment_id"]: s["sha256"] for s in arc.manifest()["segments"]}
    assert before_stats["n_segments"] >= 2 and before_stats["archived_records"] > 0

    res = run_backup(st, tmp_path / "backups", kind="manual")
    names = verify_backup(res.archive)
    assert names["ok"] is True

    # Yedek gerçekten arşivi ve manifesti içeriyor mu?
    import tarfile
    with tarfile.open(res.archive, "r:gz") as tar:
        members = tar.getnames()
    assert any(m.endswith("state/decision_journal.jsonl") for m in members)
    assert any("decision_archive/manifest.json" in m for m in members)
    assert sum(1 for m in members if m.endswith(".jsonl.gz")) == before_stats["n_segments"]

    restore_backup(res.archive, st)

    arc2 = SegmentArchive(st / "decision_archive", stream_id="decision_journal",
                          record_schema_version="decision_journal_v1")
    j2 = DecisionJournal(st / "decision_journal.jsonl", max_lines=15, archive=arc2)
    j2.load_seen()
    after_stats = j2.retention_stats()
    assert after_stats["lifetime_records"] == before_stats["lifetime_records"] == 120 + 24
    assert {s["segment_id"]: s["sha256"] for s in arc2.manifest()["segments"]} == before_sha
    assert arc2.verify()["health"] == "OK", "checksum'lar restore sonrası tutmalı"
    assert list(j2.iter_all_rows()) == before_rows, "kayıtlar birebir aynı olmalı"

    # outcome bağlantıları kaybolmamalı ve dedup çalışmalı
    outs = [r for r in j2.iter_all_rows() if r.get("kind") == KIND_OUTCOME]
    assert len(outs) == 24 and len({r["trade_id"] for r in outs}) == 24
    assert j2.append_outcome(_out(0)) in (False, True)   # yeniden yazım çökmez
    assert len({r["trade_id"] for r in j2.iter_all_rows()
                if r.get("kind") == KIND_OUTCOME}) == 24, "restore sonrası duplicate YOK"


def test_15b_corrupt_segment_after_restore_is_not_used_silently(tmp_path: Path):
    st = tmp_path / "state"
    st.mkdir()
    arc = SegmentArchive(st / "decision_archive", stream_id="decision_journal",
                         record_schema_version="decision_journal_v1")
    j = DecisionJournal(st / "decision_journal.jsonl", max_lines=5, archive=arc)
    for i in range(40):
        j.append_decision(_dec(i))
    j.rotate()
    seg = arc.manifest()["segments"][0]
    (arc.segments_dir / seg["file"]).write_bytes(b"not a gzip file at all")
    v = arc.verify()
    assert seg["segment_id"] in v["corrupt"] and v["health"] == "DEGRADED"
    assert all(r.get("decision_id") for r in j.iter_all_rows())
