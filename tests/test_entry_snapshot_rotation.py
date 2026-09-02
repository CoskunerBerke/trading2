"""`entry_snapshot.jsonl` arşiv-önce rotasyon regresyonları — KAYIPSIZ olmak zorunda.

Sözleşme (`DecisionJournal.rotate` ile aynı):
 * Satırlar ÖNCE sıkıştırılmış, checksum'lı, değişmez bir segmente mühürlenir; ANCAK ondan
   sonra sıcak dosyadan çıkarılır.
 * Arşiv yazılamazsa BUDAMA YAPILMAZ — sıcak dosya büyür ama hiçbir kanıt kaybolmaz.
 * Rotasyon ve kurtarma tekrar tekrar çalıştırılabilir (idempotent).
 * Mühürleme ile budama arasında çökme olursa satırlar ne kaybolur ne iki kez silinir.
 * Arşive düşmüş bir snapshot, açık pozisyonun bağını KORUR: `by_candidate()` ve
   `trade_links()` sıcak + arşiv birleşimini okur.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tradingbot.core import utc_now
from tradingbot.learn.entry_snapshot import (ENTRY_ARCHIVE_STREAM_ID, SCHEMA_VERSION,
                                             EntrySnapshotStore, build_entry_snapshot)
from tradingbot.learn.journal_archive import ArchiveError, SegmentArchive

NOW = utc_now()


def _snap(i: int, sym: str = "ETH/USDT"):
    return build_entry_snapshot(
        run_id="R", cycle_id=f"c{i}", symbol=sym, direction="LONG",
        decision={"p_win": 0.5, "regime": "TREND_UP", "consensus_score": 0.3},
        plan={"entry": 100.0 + i, "stop": 95.0, "entry_type": "breakout"},
        opportunity={"conservative_net_edge_r": 0.4, "sample_size": 20},
        baseline_accepted=False, code_sha="sha", config_hash="cfg",
        policy_version="entry_v1.0.0", now=NOW)


def _store(tmp: Path, *, max_lines: int = 5, max_segments: int = 0, archive: bool = True):
    arc = SegmentArchive(tmp / "arc", stream_id=ENTRY_ARCHIVE_STREAM_ID,
                         record_schema_version=SCHEMA_VERSION,
                         max_segments=max_segments) if archive else None
    return EntrySnapshotStore(tmp / "entry_snapshot.jsonl", archive=arc, max_lines=max_lines)


def _fill(st: EntrySnapshotStore, n: int, start: int = 0) -> list[str]:
    ids = []
    for i in range(start, start + n):
        s = _snap(i)
        assert st.append(s)
        ids.append(s["candidate_id"])
    return ids


# ------------------------------------------------------------------ temel kayıpsızlık

def test_01_rotation_archives_before_trimming_and_loses_nothing(tmp_path):
    st = _store(tmp_path, max_lines=5)
    ids = _fill(st, 12)
    res = st.rotate()
    assert res["health"] == "OK" and res["archived"] == 7 and res["trimmed"] == 7
    assert len(st._hot_lines()) == 5                                  # noqa: SLF001
    # Sıcak + arşiv birleşimi TAM olmalı.
    seen = {r["candidate_id"] for r in st.iter_all_rows() if r.get("kind") != "link"}
    assert seen == set(ids), "rotasyon sonrası kayıt KAYBOLDU"
    assert len(st.by_candidate(include_archive=True)) == 12
    assert st.retention_stats()["lifetime_rows"] == 12
    assert st.retention_stats()["silent_deletion"] is False


def test_02_archived_segment_is_readable_and_checksummed(tmp_path):
    st = _store(tmp_path, max_lines=3)
    _fill(st, 9)
    st.rotate()
    man = st.archive.manifest()
    assert man["totals"]["segments"] == 1
    seg = man["segments"][0]
    assert seg["n_records"] == 6 and seg["block_sha256"] and seg["sha256"]
    path = tmp_path / "arc" / "segments" / seg["file"]
    assert path.exists()
    rows = [json.loads(x) for x in gzip.open(path, "rt", encoding="utf-8") if x.strip()]
    assert len(rows) == 6
    assert st.archive.verify()["ok"]


def test_03_rotation_is_idempotent(tmp_path):
    st = _store(tmp_path, max_lines=4)
    ids = _fill(st, 10)
    a = st.rotate()
    before_hot = st._hot_lines()                                       # noqa: SLF001
    b = st.rotate()
    c = st.rotate()
    assert a["archived"] == 6
    assert b["archived"] == 0 and c["archived"] == 0, "ikinci rotasyon yeniden arşivledi"
    assert st._hot_lines() == before_hot                               # noqa: SLF001
    assert {r["candidate_id"] for r in st.iter_all_rows()} == set(ids)
    assert st.archive.manifest()["totals"]["segments"] == 1


def test_04_repeated_fill_and_rotate_keeps_every_row(tmp_path):
    st = _store(tmp_path, max_lines=4)
    all_ids: list[str] = []
    for cycle in range(5):
        all_ids += _fill(st, 6, start=cycle * 100)
        st.rotate()
    assert len(st.by_candidate(include_archive=True)) == len(all_ids) == 30
    assert st.retention_stats()["lifetime_rows"] == 30


# ------------------------------------------------------------------ arıza / çökme

def test_05_archive_failure_means_no_trim_at_all(tmp_path, monkeypatch):
    st = _store(tmp_path, max_lines=3)
    ids = _fill(st, 10)

    def boom(_lines):
        raise ArchiveError("disk dolu")
    monkeypatch.setattr(st.archive, "seal", boom)
    res = st.rotate()
    assert res["health"] == "ARCHIVE_FAILED" and res["archived"] == 0 and res["trimmed"] == 0
    assert len(st._hot_lines()) == 10, "arşiv düştüğü hâlde BUDANDI"   # noqa: SLF001
    assert {r["candidate_id"] for r in st.iter_all_rows()} == set(ids)
    assert st.archive_errors == 1 and st.last_archive_error


def test_06_no_archive_configured_means_no_deletion(tmp_path):
    st = _store(tmp_path, max_lines=3, archive=False)
    ids = _fill(st, 9)
    res = st.rotate()
    assert res["health"] == "NO_ARCHIVE_NO_DELETION"
    assert len(st._hot_lines()) == 9                                   # noqa: SLF001
    assert set(st.by_candidate(include_archive=True)) == set(ids)
    assert st.retention_stats()["retention_policy"] == "NO_ARCHIVE_NO_DELETION"


def test_07_max_lines_zero_disables_rotation(tmp_path):
    st = _store(tmp_path, max_lines=0)
    _fill(st, 8)
    res = st.rotate()
    assert res["health"] == "DISABLED" and res["archived"] == 0
    assert len(st._hot_lines()) == 8                                   # noqa: SLF001


def test_08_crash_between_seal_and_trim_loses_nothing(tmp_path):
    """Segment mühürlendi, manifest yazıldı, budama YAPILAMADAN çökme."""
    st = _store(tmp_path, max_lines=4)
    ids = _fill(st, 11)
    lines = st._hot_lines()                                            # noqa: SLF001
    cut = len(lines) - 4
    meta = st.archive.seal(lines[:cut])
    st.archive.commit(meta, pending_trim={"segment_id": meta["segment_id"], "n_lines": cut,
                                          "block_sha256": meta["block_sha256"]})
    # ÇÖKME: budama hiç çalışmadı. Sıcak dosya hâlâ tam.
    assert len(st._hot_lines()) == 11                                  # noqa: SLF001
    st2 = _store(tmp_path, max_lines=4)
    assert st2.archive.pending_trim() is not None
    res = st2.rotate()                          # kurtarma + bekleyen budamayı uygular
    assert res["trimmed"] >= cut
    assert st2.archive.pending_trim() is None
    assert {r["candidate_id"] for r in st2.iter_all_rows()} == set(ids), "çökme sonrası KAYIP"
    assert len(st2.by_candidate(include_archive=True)) == 11
    # Satırlar iki kez silinmedi.
    assert len(st2._hot_lines()) == 4                                  # noqa: SLF001


def test_09_double_trim_is_refused(tmp_path):
    st = _store(tmp_path, max_lines=4)
    _fill(st, 10)
    st.rotate()
    hot = st._hot_lines()                                              # noqa: SLF001
    # Aynı bekleyen budamayı elle bir kez daha uygula: baş blok artık eşleşmez → 0 satır.
    stale = {"n_lines": 6, "block_sha256": "0" * 64}
    assert st._apply_trim(stale) == 0                                  # noqa: SLF001
    assert st._hot_lines() == hot                                      # noqa: SLF001


def test_10_orphan_tmp_segment_is_cleaned_on_recover(tmp_path):
    st = _store(tmp_path, max_lines=3)
    _fill(st, 8)
    st.rotate()
    orphan = tmp_path / "arc" / "segments" / "seg-x.jsonl.gz.tmp-999"
    orphan.write_bytes(b"yarim")
    rec = st.rotate()["recovered"]
    assert rec["orphan_tmp_removed"] == 1 and not orphan.exists()


def test_11_segment_written_but_manifest_lost_is_adopted(tmp_path):
    st = _store(tmp_path, max_lines=3)
    ids = _fill(st, 9)
    st.rotate()
    man = tmp_path / "arc" / "manifest.json"
    man.unlink()                                     # manifest kayboldu, segment duruyor
    st2 = _store(tmp_path, max_lines=3)
    rec = st2.rotate()["recovered"]
    assert rec["adopted"] == 1
    assert {r["candidate_id"] for r in st2.iter_all_rows()} == set(ids), "segment sahiplenilmedi"


# ------------------------------------------------------------------ bağ dayanıklılığı

def test_12_open_position_link_survives_rotation(tmp_path):
    """Rotasyondan sonra bile AÇIK pozisyon kendi giriş snapshot'ına bağlanabilmeli."""
    st = _store(tmp_path, max_lines=3)
    first = _snap(0, "SOL/USDT")
    assert st.append(first)
    assert st.link_trade(first["candidate_id"], "F00042")
    _fill(st, 12, start=50)                      # ilk snapshot artık çok eski
    res = st.rotate()
    assert res["archived"] > 0
    assert first["candidate_id"] not in {json.loads(x)["candidate_id"]
                                         for x in st._hot_lines()      # noqa: SLF001
                                         if json.loads(x).get("candidate_id")}, \
        "test kurgusu: ilk snapshot hâlâ sıcakta, rotasyon sınanmıyor"
    links = st.trade_links(include_archive=True)
    assert links.get("F00042") == first["candidate_id"], "açık pozisyonun bağı KOPTU"
    snaps = st.by_candidate(include_archive=True)
    assert first["candidate_id"] in snaps
    assert snaps[first["candidate_id"]]["symbol"] == "SOL/USDT"
    assert snaps[first["candidate_id"]]["provenance"]["sees_outcome"] is False


def test_13_dedup_still_holds_across_hot_and_archive(tmp_path):
    st = _store(tmp_path, max_lines=3)
    ids = _fill(st, 9)
    st.rotate()
    st2 = _store(tmp_path, max_lines=3)
    # Arşive düşmüş bir adayı yeniden yazmayı dene.
    dup = _snap(0)
    assert dup["candidate_id"] == ids[0]
    assert st2.append(dup) is True, "sıcak dedup arşivi taramaz (sözleşme)"
    # Çevrimdışı yol arşivi de görür ve duplicate'i YAKALAR:
    assert dup["candidate_id"] in st2.known_ids(include_archive=True)
    assert len(st2.by_candidate(include_archive=True)) == 9


def test_14_evaluation_reads_hot_plus_archive(tmp_path):
    """Terfi değerlendirmesi rotasyondan sonra da tam örneklemi görmeli."""
    from tradingbot.learn.entry_challenger import EntryChallengerConfig
    from tradingbot.learn.entry_eval import build_report
    st = _store(tmp_path, max_lines=2)
    ids = _fill(st, 8)
    closes, links = [], {}
    for i, cid in enumerate(ids):
        tid = f"T{i:03d}"
        st.link_trade(cid, tid)
        links[tid] = cid
        closes.append({"close_event_id": f"ce{i}", "trade_id": tid, "symbol": "ETH/USDT",
                       "side": "LONG", "closed_at": NOW.isoformat(), "exit_reason": "stop",
                       "r_multiple": (2.0 if i % 3 == 0 else -1.0), "net_pnl": 1.0,
                       "fees": 0.01, "funding": 0.0, "raw": {}})
    st.rotate()
    assert st.retention_stats()["archived_rows"] > 0, "rotasyon gerçekleşmedi"
    doc = build_report(closes=closes, snapshots=st.by_candidate(include_archive=True),
                       links=st.trade_links(include_archive=True),
                       cfg=EntryChallengerConfig())
    assert doc["n_no_snapshot"] == 0, "arşivdeki snapshot'lar değerlendirmede KAYIP"
    assert doc["n_linked"] == 8


def test_15_retention_stats_never_claims_silent_deletion(tmp_path):
    st = _store(tmp_path, max_lines=3)
    _fill(st, 10)
    st.rotate()
    rs = st.retention_stats()
    assert rs["silent_deletion"] is False
    assert rs["retention_policy"] == "ARCHIVE_FIRST_LOSSLESS"
    assert rs["hot_rows"] + rs["archived_rows"] == rs["lifetime_rows"] == 10
    assert rs["n_segments"] == 1
    assert st.stats()["retention"]["lifetime_rows"] == 10


@pytest.mark.parametrize("bad", [{"snapshot_max_lines": -1},
                                 {"snapshot_archive_max_segments": -1},
                                 {"snapshot_max_lines": 5, "max_snapshots_per_cycle": 200}])
def test_16_retention_config_is_fail_closed(bad):
    from tradingbot.config_v3 import load_v3, validate_v3
    from tradingbot.core import ConfigError
    with pytest.raises(ConfigError):
        validate_v3(load_v3({"entry_selectivity": bad}))


def test_17_default_config_keeps_archive_unlimited(tmp_path):
    from tradingbot.config_v3 import load_v3, validate_v3
    c = load_v3({})
    validate_v3(c)
    assert c.entry_selectivity.snapshot_archive_max_segments == 0, "segment silme AÇIK"
    assert c.entry_selectivity.snapshot_max_lines >= c.entry_selectivity.max_snapshots_per_cycle
