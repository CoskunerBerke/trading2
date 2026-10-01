# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — inceleme bulgularının düzeltmeleri (2026-09-29, aşama FIX).

Her test düzeltmeden ÖNCEKİ kodda (bu aşamanın başındaki çalışma ağacı) düşer:
* depo: dolu sıcak dosyada her adım bir segment + adım başına manifest/segment klasörü okuması (döngü histerezisi yok);
  G/Ç hatasında diske ulaşmış baytlar kalıyor ve okuyucu eşit revizyonda İLK (bayat) kopyayı tutuyordu;
* toplayıcı: büyük imleç her adım yazılıyordu; geri doldurma kilit altında defter başına 300 kayıt kopyalıyordu; kapanmış
  işlemin bekleyen giriş taslağı yeniden başlatmada kayboluyordu; ana açılışın sinyal anahtarı açılış adımında defter
  atlanınca kayboluyordu; yeniden kaydedilen deterministik karşı-olgusal kimliği eski revizyonlarla çakışıyordu; SPOT
  açılışları sayılmıyordu;
* rapor: girişi olmayan işlem varsayılan süzgeçte kapsamda görünmüyordu; iid aralığın karneyle sayısal eşit olmadığı
  yazmıyordu;
* yedek: her saatlik yedek bütün (sınırsız) segment arşivini taşıyordu.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_shared_experience_collector_v1 as C  # noqa: E402
from test_shared_experience_store_v1 import _cf_rows, _lines  # noqa: E402
from tradingbot.learn.journal_archive import SegmentArchive  # noqa: E402
from tradingbot.learning_cf import CounterfactualRecorder  # noqa: E402
from tradingbot.shared_experience import collector as XC  # noqa: E402
from tradingbot.shared_experience import report as X  # noqa: E402
from tradingbot.shared_experience import rows as R  # noqa: E402
from tradingbot.shared_experience import store as ST  # noqa: E402
from tradingbot.shared_experience.store import ExperienceStore  # noqa: E402

UTC = timezone.utc
T0, T0_MS = C.T0, C.T0_MS


# ============================================================================ 1) depo: döngü histerezisi (YÜKSEK)
def test_a_full_hot_file_does_not_seal_a_segment_per_step_and_idle_steps_do_no_archive_io(tmp_path, monkeypatch):
    """Dolu sıcak dosyaya adım başına 1 satır, 50 adım: eskiden 50 segment (her biri 1 satır) ve adım başına ~5 manifest
    okuması + segment klasörü taraması + sıcak dosyanın yeniden yazımı/ayrıştırması. Şimdi en çok bir döngü; sınırın
    altındaki adım (döngü + disk baskısı) arşive HİÇ dokunmaz."""
    st = ExperienceStore(tmp_path / "xp", hot_max_lines=100)
    assert st.append_rows(_cf_rows(100, tag="fill"))[0] == 100
    st.rotate()                                            # süreç başı kurtarma (toplayıcı kurulurken çağırır)
    calls = {"manifest": 0, "recover": 0, "read_lines": 0}
    real_manifest, real_recover, real_read = SegmentArchive.manifest, SegmentArchive.recover, ExperienceStore._read_lines

    def _m(self):
        calls["manifest"] += 1
        return real_manifest(self)

    def _r(self):
        calls["recover"] += 1
        return real_recover(self)

    def _rl(self):
        calls["read_lines"] += 1
        return real_read(self)
    monkeypatch.setattr(SegmentArchive, "manifest", _m)
    monkeypatch.setattr(SegmentArchive, "recover", _r)
    monkeypatch.setattr(ExperienceStore, "_read_lines", _rl)
    per_step = []
    for i in range(50):
        before = dict(calls)
        assert st.append_rows(_cf_rows(1, tag="s%d_" % i))[0] == 1
        st.rotate()
        st.pressure()
        st.disk_bytes()
        per_step.append({k: calls[k] - before[k] for k in calls})
    segs = st.archive.segments()
    assert len(segs) <= 1 and st.rotations == 1, "ÖNCE: 50 segment (adım başına bir)"
    assert all(n >= 50 for n in (s["n_records"] for s in segs)), "segment ≥ hot_max_lines − keep_lines satır"
    idle = [p for p in per_step if not any(p.values())]
    assert len(idle) >= 48, per_step
    assert calls["recover"] == 0, "kurtarma süreç başına bir kez (ve arşiv hatasından sonra)"
    assert len(_lines(st.path)) <= 100 and st._line_count == len(_lines(st.path))
    # kayıpsız: her satır arşiv + sıcakta TAM bir kez
    ids = [r["row_id"] for r in st.iter_all_rows()]
    assert len(ids) == 150 and len(set(ids)) == 150
    # idempotency kümesi yalnız sıcak dosya (bellek sınırı) ve disk baskısı önbelleği doğru
    assert st._seen == {json.loads(ln)["row_id"] for ln in _lines(st.path)}
    man = real_manifest(st.archive)
    assert st.disk_bytes() == st.path.stat().st_size + man["totals"]["bytes_compressed"]


def test_an_archive_failure_retries_recovery_on_the_next_rotation(tmp_path, monkeypatch):
    st = ExperienceStore(tmp_path / "xp", hot_max_lines=10)
    assert st.append_rows(_cf_rows(12))[0] == 12
    st._recover_pending = False
    real_seal = st.archive.seal
    monkeypatch.setattr(st.archive, "seal", lambda lines: (_ for _ in ()).throw(ST.ArchiveError("disk (test)")))
    assert st.rotate()["health"] == "ARCHIVE_FAILED" and st._recover_pending is True and len(_lines(st.path)) == 12
    monkeypatch.setattr(st.archive, "seal", real_seal)
    res = st.rotate()
    assert res["recovered"] is not None and res["archived"] == 7 and len(_lines(st.path)) == 5


# ============================================================================ 2) depo: G/Ç hatası geri kesme (ORTA)
class _PrefixThenENOSPC:
    """Dosya sarmalayıcı: ilk yazım ilk satır sonuna kadar diske ulaşır, sonra ENOSPC (yarım toplu yazım)."""

    def __init__(self, fh):
        self.fh = fh

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.fh.close()

    def write(self, data):
        cut = data.index(b"\n") + 1
        self.fh.write(data[:cut])
        self.fh.flush()
        raise OSError(28, "No space left on device")

    def __getattr__(self, k):
        return getattr(self.fh, k)


@pytest.mark.parametrize("mode", ["fsync", "prefix"])
def test_a_failed_write_leaves_nothing_on_disk_and_the_retry_is_the_only_copy(tmp_path, monkeypatch, mode):
    """Probe p7: v1 etiketli (yalnız brüt) karşı-olgusal revizyonunun yazımı baytlar diske ULAŞTIKTAN sonra düşer; kaynak
    araya v3 net etiket alır; yeniden deneme AYNI `row_id`yi yeni içerikle yazar. Eskiden diskte iki kopya kalıyor ve rapor
    ilkini (brüt) tutuyordu: net etiket istatistiğe hiç girmiyordu."""
    box = C.FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = C.fake_engine(tmp_path, books=[box])
    xp = C.collector(eng)
    C.step(xp, eng, T0)
    t = C.record_cf(box, "SOL/USDT", sig=T0_MS, created=T0 + timedelta(minutes=1))
    C.set_tour(eng, T0_MS + 60_000)
    C.step(xp, eng, T0 + timedelta(minutes=2))                             # rev 0 PENDING
    size_before = xp.store.path.stat().st_size
    t.outcome = {"r_multiple": 0.7, "exit_reason": "target", "bars": 2}     # v1 (yalnız brüt)
    real_open = open
    if mode == "fsync":
        monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(5, "Input/output error")))
    else:
        monkeypatch.setattr(ST, "open", lambda p, m="r", *a, **k: _PrefixThenENOSPC(real_open(p, m, *a, **k)),
                            raising=False)
    res = C.step(xp, eng, T0 + timedelta(minutes=3))
    monkeypatch.undo()
    assert res.get("io_error") is True and xp.store.last_append["rollback"] == "OK"
    assert xp.store.path.stat().st_size == size_before, "yarım toplu yazım geri kesildi"
    t.outcome.update(label_version="cf_label_v3", r_gross=0.7, r_net=0.5, cost_r=0.2)   # tembel v1 → v3
    C.step(xp, eng, T0 + timedelta(minutes=4))
    rows = [r for r in C.rows_of(eng) if r["kind"] == "xp_cf"]
    assert [(r["rev"], r["status"], r.get("r_basis"), r.get("r_net")) for r in rows] == \
        [(0, "PENDING", None, None), (1, "LABELLED", "NET", 0.5)]
    doc = X.run(xp.root, X.Query(kind="cf", snapshot="any"))
    assert doc["coverage"]["cf_net"] == 1 and doc["coverage"]["cf_gross_legacy"] == 0


def test_the_reader_keeps_the_last_copy_of_an_equal_revision(tmp_path):
    """İkinci kilit (geri kesme de başarısızsa ya da imleç kaybında yeniden yayın): eşit (tür, anahtar, rev) → SONUNCU."""
    a, b = ExperienceStore(tmp_path / "xp"), None
    row = _cf_rows(1, status="LABELLED", rev=1, tag="dup")[0]
    stale = dict(row, r_basis="GROSS_LEGACY", in_net_stats=False, r_net=None, label_version=None)
    fresh = dict(row, r_basis="NET", in_net_stats=True, r_net=0.5, label_version="cf_label_v3")
    base = dict(_cf_rows(1, tag="dup")[0])
    with open(a.path, "a", encoding="utf-8") as fh:
        for r in (base, stale, fresh):
            fh.write(json.dumps(r) + "\n")
    o = X.scan(tmp_path / "xp")
    assert o.counts["dup_rows"] == 1 and o.counts["dup_rows_replaced"] == 1
    doc = X.build(o, X.Query(kind="cf", snapshot="any", min_n=10))
    assert doc["coverage"]["cf_net"] == 1 and doc["coverage"]["cf_gross_legacy"] == 0, "ÖNCE: ilk (bayat) kopya kalıyordu"
    del b


# ============================================================================ 3) toplayıcı: imleç seyreltme (ORTA)
def test_a_large_cursor_is_written_at_most_every_n_steps_and_a_restart_from_it_loses_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(XC, "CURSOR_THROTTLE_BYTES", 0)                    # her imleç "büyük"
    box = C.FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = C.fake_engine(tmp_path, books=[box])
    xp = C.collector(eng)
    writes = []
    real = XC.atomic_write_text

    def spy(path, text, *a, **k):
        if Path(path).name == XC.CURSOR_FILE:
            writes.append(xp.steps)
        return real(path, text, *a, **k)
    monkeypatch.setattr(XC, "atomic_write_text", spy)
    for k in range(12):
        C.record_cf(box, "SOL/USDT", sig=T0_MS + k, created=T0 + timedelta(minutes=k))
        C.set_tour(eng, T0_MS + k * 60_000)
        C.step(xp, eng, T0 + timedelta(minutes=k, seconds=30))
    assert writes[0] == 1 and all(b - a >= XC.CURSOR_SAVE_EVERY_STEPS for a, b in zip(writes, writes[1:])), writes
    assert len(writes) <= 3 and XC.CURSOR_SAVE_EVERY_STEPS == 5, "ÖNCE: her commit'li adımda (12 kez)"
    assert C.status_of(eng)["counters"]["cursor_deferred"] >= 8
    # çökme: son kayıtlı imleçten yeniden başla — kayıp yok, çelişen row_id yok (tekrarlar aynı içerik)
    xp2 = C.collector(eng)
    for k in range(12, 14):
        C.record_cf(box, "SOL/USDT", sig=T0_MS + k, created=T0 + timedelta(minutes=k))
        C.set_tour(eng, T0_MS + k * 60_000)
        C.step(xp2, eng, T0 + timedelta(minutes=k, seconds=30))
    rows = [r for r in C.rows_of(eng) if r["kind"] == "xp_cf"]
    assert {r["cf_id"] for r in rows if r["rev"] == 0} == {t.id for t in box.cf.sb.trades}
    by_id: dict[str, dict] = {}
    for r in rows:
        prev = by_id.setdefault(r["row_id"], r)
        assert {k: v for k, v in prev.items() if k != "recorded_at"} == {k: v for k, v in r.items() if k != "recorded_at"}


# ============================================================================ 4) toplayıcı: geri doldurma kilit tavanı (DÜŞÜK)
def test_backfill_copies_at_most_a_hundred_records_per_book_under_the_lock(tmp_path, monkeypatch):
    eng = C.fake_engine(tmp_path)
    old = T0 - timedelta(days=3)
    for i in range(250):
        at = old + timedelta(minutes=10 * i)
        C.open_pos(eng.ledger2, "SOL/USDT", at=at)
        eng.ledger2.close_manual("SOL/USDT", D("101"), "hedef", at + timedelta(minutes=5))
    held = {"n": 0, "max": 0}
    real_rf, real_read = XC._rec_facts, XC.SharedExperienceCollector._read_ledger

    def rf(rec):
        held["n"] += 1
        return real_rf(rec)

    def read_ledger(self, ad, cur, d, ctx):
        held["n"] = 0
        out = real_read(self, ad, cur, d, ctx)
        held["max"] = max(held["max"], held["n"])
        return out
    monkeypatch.setattr(XC, "_rec_facts", rf)
    monkeypatch.setattr(XC.SharedExperienceCollector, "_read_ledger", read_ledger)
    xp = C.collector(eng)
    C.step(xp, eng, T0)
    assert 0 < held["max"] <= XC.BF_COPY_PER_BOOK == 100, "ÖNCE: kilit altında 250 kayıt kopyalanıyordu"
    for k in range(1, 5):
        C.step(xp, eng, T0 + timedelta(minutes=k))
    outs = [r for r in C.rows_of(eng) if r["kind"] == "xp_outcome"]
    assert len(outs) == 250 and len({r["trade_key"] for r in outs}) == 250, "geri doldurma birkaç adımda tamamlanır"
    assert xp._books["main"].bf_done is True


# ============================================================================ 5) toplayıcı: yeniden başlatma + bekleyen giriş (DÜŞÜK)
def test_a_trade_closed_between_steps_keeps_its_deferred_entry_across_a_restart(tmp_path):
    """Probe p5: Box işlemi 12:05'te açılıp 12:10'da kapanır; turun çerçeveleri 11:58'de alınmıştı (12:00 sınırı) → giriş
    taslağı BEKLER, sonuç yazılır ve imleç işlemi geçer. Eskiden taslak yalnız bellekteydi: yeniden başlatmada giriş
    (anlık görüntü) kalıcı olarak kayboluyordu ve varsayılan raporda bu kayıp görünmüyordu."""
    box = C.FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    t_1158 = datetime(2026, 9, 29, 11, 58, tzinfo=UTC)
    eng = C.fake_engine(tmp_path, books=[box], fetch_ms=int(t_1158.timestamp() * 1000))
    xp = C.collector(eng)
    C.step(xp, eng, t_1158)
    ev = datetime(2026, 9, 29, 12, 5, tzinfo=UTC)
    C.open_pos(box.ledger, "SOL/USDT", at=ev, meta={"signal": {"signal_ts": int(ev.timestamp() * 1000)}})
    box.ledger.close_manual("SOL/USDT", D("99"), "stop", ev + timedelta(minutes=5))
    C.step(xp, eng, ev + timedelta(minutes=6))
    assert [r["kind"] for r in C.rows_of(eng)] == ["xp_outcome"] and len(xp._drafts) == 1
    # kayıp görünür: varsayılan süzgeçte (yalnız OK) de girişi olmayan işlem sayılır
    doc = X.run(xp.root, X.Query(kind="real"))
    assert doc["coverage"]["real_entry_missing"] == 1, "ÖNCE: varsayılan süzgeç 0 gösteriyordu"
    xp2 = C.collector(eng)                                                   # yeniden başlatma (imleç kalıcı)
    assert len(xp2._drafts) == 1 and xp2.c["pending_entries_restored"] == 1, "ÖNCE: taslak yok"
    for k in range(3):
        t = datetime(2026, 9, 29, 12, 16 + 6 * k, tzinfo=UTC)
        C.set_tour(eng, int(t.timestamp() * 1000))
        C.step(xp2, eng, t)
    rows = C.rows_of(eng)
    ent = [r for r in rows if r["kind"] == "xp_entry"]
    assert len(ent) == 1 and ent[0]["snapshot_status"] == "OK" and ent[0]["origin"] == "LIVE"
    assert ent[0]["trade_key"] == [r for r in rows if r["kind"] == "xp_outcome"][0]["trade_key"]
    assert ent[0]["entry_px"] == 100.0 and ent[0]["as_of_ms"] == int(ev.timestamp() * 1000)
    assert not xp2._drafts and "pend_e" not in json.loads((xp2.root / XC.CURSOR_FILE).read_text())["books"][
        "strategy_paper_box"]
    doc = X.run(xp2.root, X.Query(kind="real"))
    assert doc["coverage"]["real_entry_missing"] == 0 and doc["coverage"]["real_closed_net"] == 1


# ============================================================================ 6) toplayıcı: birleştirme anahtarları (DÜŞÜK)
def _dec(verdict, direction="LONG", entry_type="pullback"):
    from tradingbot.coinhead.schema import Verdict
    return SimpleNamespace(active_plan=SimpleNamespace(entry_type=entry_type), verdict=Verdict(verdict), direction=direction)


def test_spot_real_opens_are_counted_not_silently_skipped(tmp_path):
    eng = C.fake_engine(tmp_path)
    xp = C.collector(eng)
    C.step(xp, eng, T0)
    d = {"AVAX/USDT": _dec("SPOT_LONG")}
    b = [SimpleNamespace(symbol="AVAX/USDT", last_bar_4h="2026-09-29T04:00")]
    eng.spot2 = SimpleNamespace(history=[object(), object()])
    risk_log = [{"symbol": "AVAX/USDT", "verdict": "SPOT_LONG", "trade_id": "spot_abc", "risk_allowed": True}]
    stats: dict = {}
    assert XC.main_signal_keys(eng, risk_log, d, b, stats=stats) == ({}, 0) and stats == {"spot": 1}
    C.set_tour(eng, T0_MS + 60_000)
    C.step(xp, eng, T0 + timedelta(minutes=2), risk_log=risk_log, decisions=d, briefs=b)
    c = C.status_of(eng)["counters"]
    assert c["spot_real_excluded"] == 1 and c["spot_real_closed_seen"] == 2 and c["rows_total"] == 0


def test_a_main_open_whose_step_skipped_the_ledger_keeps_its_signal_key_and_supersedes_the_cf(tmp_path):
    """Probe p4 B: açılış turunun adımında ana defter kilidi meşgul (koruyucu izleyici) → defter atlanır; sonraki adımın
    risk_log'unda işlem yoktur. Eskiden giriş anahtarsız yazılıyor, aynı sinyalin karşı-olgusalı DROPPED sayılıyordu."""
    eng = C.fake_engine(tmp_path, lock_timeout_s=0.0)
    xp = C.collector(eng)
    C.step(xp, eng, T0)
    sym = "SOL/USDT"
    d = {sym: _dec("FUTURES_LONG")}
    b = [SimpleNamespace(symbol=sym, last_bar_4h="2026-09-29T04:00")]
    sig = eng._signal_id(sym, "USDM_PERP", d[sym], d[sym].active_plan, b[0])
    sh = eng.shadow.add({"plan_id": sig, "symbol": sym, "market_type": "USDM_PERP", "direction": "LONG", "entry": 100,
                         "stop": 95, "targets": [110]}, ["TOTAL_OPEN_RISK"], now=T0 + timedelta(minutes=1))[0]
    sh.book, sh.signal_key, sh.label_kind, sh.features = "main", sig, "TARGET_STOP_TIME", {"setup_type": "pullback"}
    C.set_tour(eng, T0_MS + 60_000)
    C.step(xp, eng, T0 + timedelta(minutes=2))
    pos = C.open_pos(eng.ledger2, sym, at=T0 + timedelta(minutes=7), setup_type="pullback")
    eng.shadow.trades = [t for t in eng.shadow.trades if t is not sh]      # motor gerçek açılışta CF'yi siler
    eng._seen_signals = [sig]
    risk_log = [{"symbol": sym, "verdict": "LONG", "trade_id": pos.id, "risk_allowed": True}]
    held, release = threading.Event(), threading.Event()

    def holder():
        with eng._ledger_lock:
            held.set()
            release.wait(10)
    th = threading.Thread(target=holder)
    th.start()
    held.wait(5)
    try:
        C.set_tour(eng, T0_MS + 7 * 60_000)
        C.step(xp, eng, T0 + timedelta(minutes=8), risk_log=risk_log, decisions=d, briefs=b)
    finally:
        release.set()
        th.join(5)
    assert xp._books["main"].sig_tid.get(pos.id, [None])[0] == sig, "tüketilmemiş eşlem imleçte"
    C.set_tour(eng, T0_MS + 13 * 60_000)
    C.step(xp, eng, T0 + timedelta(minutes=14))                            # risk_log boş
    rows = C.rows_of(eng)
    e = [r for r in rows if r["kind"] == "xp_entry"]
    assert [(r["signal_key"], r["signal_key_src"]) for r in e] == [(sig, "ENGINE_SIGNAL_ID")] and e[0]["join_key"]
    assert [(r["rev"], r["status"]) for r in rows if r["kind"] == "xp_cf"] == [(0, "PENDING"), (1, "SUPERSEDED")], \
        "ÖNCE: (1, DROPPED)"
    assert pos.id not in xp._books["main"].sig_tid and C.status_of(eng)["counters"]["main_entry_no_sigkey"] == 0


def test_a_counterfactual_id_that_reappears_continues_its_revisions(tmp_path):
    """Probe p4 C: kayıtçının bellek içi `_keys/_gone`u yeniden başlatmada boşalır; aynı deterministik kimlik yeniden
    kaydedilir. Eskiden yeni rev 0/1 eski satırların row_id'siyle çakışıyor, net etiket tekrar sayılıp düşüyordu."""
    box = C.FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = C.fake_engine(tmp_path, books=[box])
    xp = C.collector(eng)
    C.step(xp, eng, T0)
    t = C.record_cf(box, "SOL/USDT", sig=T0_MS, created=T0 + timedelta(minutes=1))
    C.set_tour(eng, T0_MS + 60_000)
    C.step(xp, eng, T0 + timedelta(minutes=2))                             # rev 0 PENDING
    box.cf.sb.trades = []
    C.step(xp, eng, T0 + timedelta(minutes=3))                             # rev 1 DROPPED
    assert xp._books["strategy_paper_box"].cf_gone[t.id][0] == 1
    box.cf = CounterfactualRecorder(tmp_path / "strategy_paper_box" / "counterfactual_trades2.json", book=box.name)
    t2 = C.record_cf(box, "SOL/USDT", sig=T0_MS, created=T0 + timedelta(minutes=4))
    assert t2.id == t.id
    C.step(xp, eng, T0 + timedelta(minutes=5))
    t2.outcome = {"r_multiple": 1.0, "label_version": "cf_label_v3", "r_gross": 1.0, "r_net": 0.8, "cost_r": 0.2,
                  "exit_reason": "target", "bars": 3}
    C.step(xp, eng, T0 + timedelta(minutes=6))
    rows = [r for r in C.rows_of(eng) if r["kind"] == "xp_cf"]
    assert [(r["rev"], r["status"]) for r in rows] == [(0, "PENDING"), (1, "DROPPED"), (2, "PENDING"), (3, "LABELLED")]
    assert len({r["row_id"] for r in rows}) == 4
    c = C.status_of(eng)["counters"]
    assert c["cf_reappeared"] == 1 and c["duplicate_rows"] == 0
    assert t.id not in xp._books["strategy_paper_box"].cf_gone
    o = X.scan(xp.root)
    doc = X.build(o, X.Query(snapshot="any", kind="cf", min_n=10))
    assert doc["coverage"]["cf_net"] == 1 and doc["coverage"]["cf_dropped"] == 0, "ÖNCE: cf_dropped 1, net n 0"


def test_tombstones_and_pending_signal_maps_are_bounded(tmp_path):
    eng = C.fake_engine(tmp_path)
    xp = C.collector(eng)
    cur = xp._book("strategy_paper_box")
    now = T0_MS
    cur.cf_gone.update({"old": [1, now - XC.CF_GONE_TTL_MS - 1], "new": [2, now]})
    cur.cf_gone.update({"n%d" % i: [0, now - i] for i in range(XC.CF_GONE_KEEP + 5)})
    xp._book("main").sig_tid.update({"t_old": ["s", now - XC.SIG_TID_TTL_MS - 1], "t_new": ["s", now]})
    xp._prune(now)
    assert "old" not in cur.cf_gone and len(cur.cf_gone) == XC.CF_GONE_KEEP and "new" in cur.cf_gone
    assert set(xp._book("main").sig_tid) == {"t_new"}


# ============================================================================ 7) rapor ve aile (DÜŞÜK)
def test_c4s_rows_stay_out_of_the_candle_family_pool():
    env = R.make_env(recorded_at=T0, app_mode="PAPER", code_sha="x", config_hash="y", learning_since=None)
    from test_shared_experience_store_v1 import _shadow
    c4 = R.cf_row(_shadow("c4_candle_variations", key="k1", variation="CV001",
                          features={"setup_type": "candle:CV001"}), book="strategy_paper_candle4h", rev=0,
                  status="PENDING", env=env)
    c4s = R.cf_row(_shadow("c4s_candle_variations_strict", key="k1", variation="CV001",
                           features={"setup_type": "candle:CV001"}), book="strategy_paper_candle4h_strict", rev=0,
                   status="PENDING", env=env)
    assert c4["family_key"] == "CANDLE_PATTERN|LONG" and c4s["family_key"] is None and c4s["family"] is None
    assert c4s["setup_key"] == "strategy_paper_candle4h_strict|candle:CV001|LONG", "kurulum düzeyinde ayrı durur"


def test_the_report_says_its_iid_ci_is_not_numerically_the_scorecards(tmp_path):
    ExperienceStore(tmp_path / "xp").append_rows(_cf_rows(3))
    doc = X.run(tmp_path / "xp", X.Query(snapshot="any"))
    note = doc["params"]["ci_iid_note"]
    assert "AYNI DEĞİL" in note and "_bootstrap_ci" in note
    assert "SAYISAL EŞİTLİK YOK" in (X.__doc__ or "")


# ============================================================================ 8) yedek (ORTA)
def _xp_state(tmp_path) -> Path:
    st = tmp_path / "state"
    seg = st / "shared_experience" / "archive" / "segments"
    seg.mkdir(parents=True)
    for i in range(3):
        (seg / ("seg-%d.jsonl.gz" % i)).write_bytes(b"x" * 10)
    (st / "shared_experience" / "archive" / "manifest.json").write_text("{}", encoding="utf-8")
    (st / "shared_experience" / "experience.jsonl").write_text("{}\n", encoding="utf-8")
    (st / "health.json").write_text("{}", encoding="utf-8")
    return st


@pytest.mark.parametrize("kind,hour,want_segments", [("hourly", 5, False), ("hourly", 0, True), ("manual", 5, True),
                                                     ("daily", 13, True)])
def test_hourly_backups_carry_the_immutable_xp_segments_only_once_a_day(tmp_path, kind, hour, want_segments):
    import tarfile

    from tradingbot.ops.backup import run_backup, verify_backup
    st = _xp_state(tmp_path)
    res = run_backup(st, tmp_path / "backups", kind, now=datetime(2026, 9, 29, hour, 3, tzinfo=UTC))
    assert verify_backup(res.archive)["ok"]
    with tarfile.open(res.archive, "r:gz") as tar:
        names = set(tar.getnames())
    segs = {n for n in names if "/archive/segments/" in n and n.endswith(".gz")}
    assert bool(segs) is want_segments and res.skipped_xp_segments == (0 if want_segments else 3)
    assert {"state/shared_experience/archive/manifest.json", "state/shared_experience/experience.jsonl",
            "state/health.json"} <= names, "manifest, sıcak dosya ve diğer durum HER yedekte"
