# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN advisor_v1 — KATLAMA, CANLI = ÇEVRİMDIŞI, YENİDEN BAŞLATMA (2026-09-29; SPEC_ADVISOR_V1 §4.4–4.6,
§6.3, §7 T-A3).

* Determinizm: aynı depo iki kez → bayt bayt aynı tavsiye; toplu yazım içinde değer satırlarının sırası toplamları
  değiştirmez; `PYTHONHASHSEED` hiçbir şeyi değiştirmez.
* Canlı = çevrimdışı: GERÇEK toplayıcı (sahte motor, gerçek defter/kayıtçı) `advisor_mode=RECORD` ile 36 tur; sonra
  `advisor_eval.verify` çekirdekleri %100 eşleşir, koşan sayaçlar çevrimdışı katlamayla AYNI.
* Yeniden başlatma: anlık görüntüyle / anlık görüntüsüz (baştan yetişme) / bozuk / başka mühür → aynı çekirdekler; yalnız
  `meta.fold_mode` farklı. Son satır arşive döndüyse yeni segmentte bulunur. Zorla ertelenen katlama içerik değiştirmez.
* 72 sa sınırı, TTL süreleri, yetim değerler, tekrarlar (yeniden yayın, çift hedef, kaybolup yeniden beliren karşı-olgusal,
  64-bit çakışma saplaması), saat gerilemesi, `advisor_born_ms` bir kez kurulur ve yeniden kurulumda taşınmaz.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import random
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import advisor_synth as X  # noqa: E402
import test_shared_experience_collector_v1 as T  # noqa: E402
from advisor_synth import A, R  # noqa: E402

from tradingbot.core import iso  # noqa: E402
from tradingbot.shared_experience import advisor_eval as AE  # noqa: E402
from tradingbot.shared_experience import advisor_live as AL  # noqa: E402

DAY, HOUR, MIN = X.DAY, X.HOUR, X.MIN


# ============================================================================ canlı dünya (gerçek toplayıcı)
class LiveWorld:
    """Sahte motor + gerçek Box kayıtçısı + gerçek ana defter; her tur: yeni karşı-olgusallar, eski olanların v3
    etiketleri, pozisyon açılış/kapanışları; `recorded_at` = turun başlangıcı. Tohum aynı → dünya aynı."""

    def __init__(self, tmp: Path, *, seed: int = 7, advisor: str = "RECORD", seed_evidence: bool = True, **xp) -> None:
        self.tmp = tmp
        self.box = T.FakeBook(tmp, "strategy_paper_box", decision_tf="5m")
        self.eng = T.fake_engine(tmp, books=[self.box], advisor_mode=advisor, **xp)
        self.rng = random.Random(seed)
        self.k = 0
        self.cfs: list = []
        self.root = self.eng.cfg.state_path / "shared_experience"
        if seed_evidence:
            # geçmiş kanıt (ana depoda zaten var): canlı hedefler yeterli seviyelerde de cevaplansın
            rec0 = X.iso_us(T.T0_MS - 2 * HOUR, 17)
            rows = X.cell_rows({d: [-1.0, 0.4, -1.0, 1.1, -0.6] * 2 for d in range(1, 7)}, rec=rec0, kind="cf",
                               setup="trend", prefix="S", base_ms=T.T0_MS - 25 * DAY, symbol="SOL/USDT")
            rows += X.cell_rows({d: [-1.0, 0.8, -0.3] * 2 for d in range(1, 7)}, rec=rec0, book="main", setup="trend",
                                prefix="M", base_ms=T.T0_MS - 25 * DAY, symbol="ETH/USDT")
            X.write_hot(self.root, rows)

    def collector(self):
        return T.collector(self.eng)

    def tour(self, xp) -> dict:
        rng, k = self.rng, self.k
        at = T.T0 + timedelta(minutes=10 * k + 1)
        for j in range(rng.randrange(1, 4)):
            sym = rng.choice(T.SYMS)
            self.cfs.append((T.record_cf(self.box, sym, sig=T.T0_MS + (k * 10 + j) * 1000, created=at,
                                         side=rng.choice(("LONG", "SHORT"))), at))
        for item in list(self.cfs):
            t, created = item
            if created < at - timedelta(minutes=15) and rng.random() < 0.35:
                r = rng.choice((-1.0, -0.7, 0.4, 1.3, -1.0))
                t.outcome = {"r_multiple": r + 0.1, "label_version": "cf_label_v3", "r_gross": r + 0.1, "r_net": r,
                             "cost_r": 0.1, "exit_reason": "target" if r > 0 else "stop"}
                t.labeled_at = iso(at - timedelta(seconds=rng.randrange(0, 300)))
                self.cfs.remove(item)
        led = self.eng.ledger2
        if led.positions and rng.random() < 0.4:
            sym = sorted(led.positions)[0]
            led.close_manual(sym, T.D(str(rng.choice(("96", "103", "108")))), "test", at)
        elif not led.positions or rng.random() < 0.3:
            free = [s for s in T.SYMS if s not in led.positions]
            if free:
                T.open_pos(led, rng.choice(free), at=at)
        T.set_tour(self.eng, T.T0_MS + (10 * k + 1) * MIN + 30_000)
        res = T.step(xp, self.eng, T.T0 + timedelta(minutes=10 * k + 2))
        self.k += 1
        return res

    def advice_rows(self) -> list[dict]:
        p = self.root / "advice" / "advice.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def cores(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in AE.iter_advice(self.root):
            out.setdefault(r["row_id"], r["core_sha"])
        return out


def run_world(tmp: Path, n: int, *, restart_at: tuple[int, ...] = (), before_restart=None, **kw) -> LiveWorld:
    w = LiveWorld(tmp, **kw)
    xp = w.collector()
    for i in range(n):
        if i in restart_at:
            if before_restart is not None:
                before_restart(w, xp)
            xp = w.collector()                                   # süreç yeniden başlatıldı (bellek durumu sıfır)
        w.tour(xp)
    w.xp = xp
    return w


# ============================================================================ determinizm
def test_two_folds_of_one_store_are_byte_identical():
    rows = X.random_store(11)
    _f1, a1 = X.fold_all(rows)
    _f2, a2 = X.fold_all(rows)
    assert json.dumps(a1, sort_keys=True) == json.dumps(a2, sort_keys=True) and len(a1) > 100


def _signature(f: A.AdvisorFold) -> dict:
    cells = {}
    for ck, i in f._cidx.items():
        cells[ck] = (tuple(f._col[k][i] for k in sorted(f._col)), tuple(f._tab[i]))
    tails = [sorted(t[:6] + (tuple(t[6]),) for t in f._tail_it[ch]) for ch in (0, 1)]
    return {"cells": cells, "coins": dict(f._coin), "tails": tails, "ev": list(f._ev_total), "tally": f.tallies(),
            "counters": dict(f.counters), "ctx": dict(f._ctx), "done": sorted(f._done_recent | set(map(int, f._done)))}


def test_permuting_value_rows_within_a_batch_leaves_aggregates_and_advice_identical():
    rows = X.random_store(5)
    rng = random.Random(1)
    perm: list[dict] = []
    for b in X.batches(rows):
        vals = [i for i, r in enumerate(b) if not (r["kind"] == "xp_entry" or (r["kind"] == "xp_cf" and r["rev"] == 0))]
        moved = [b[i] for i in vals]
        rng.shuffle(moved)
        bb = list(b)
        for i, r in zip(vals, moved):
            bb[i] = r
        perm.extend(bb)
    f1, a1 = X.fold_all(rows)
    f2, a2 = X.fold_all(perm)
    assert _signature(f1) == _signature(f2)
    assert sorted(json.dumps(x, sort_keys=True) for x in a1) == sorted(json.dumps(x, sort_keys=True) for x in a2)


_HASH_SCRIPT = """
import hashlib, json, sys
sys.path.insert(0, %r)
import advisor_synth as X
out = hashlib.sha256()
for seed in (3, 4):
    f, adv = X.fold_all(X.random_store(seed))
    out.update(json.dumps(adv, sort_keys=True).encode())
    out.update(f.state_bytes())
print(out.hexdigest())
"""


def test_python_hash_seed_changes_nothing():
    outs = set()
    for hs in ("0", "1", "987654"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        p = subprocess.run([sys.executable, "-c", _HASH_SCRIPT % str(HERE)], env=env, capture_output=True, text=True,
                           timeout=300)
        assert p.returncode == 0, p.stderr
        outs.add(p.stdout.strip())
    assert len(outs) == 1


# ============================================================================ canlı = çevrimdışı
def test_live_equals_offline_over_36_collector_steps(tmp_path):
    w = run_world(tmp_path, 36)
    adv = w.xp._advisor
    assert adv is not None and adv.state == "OK" and adv.mode == "live" and adv.born_ms is not None
    rows = w.advice_rows()
    assert rows and all(r["effect"] == "NONE" for r in rows)
    assert {r["meta"]["fold_mode"] for r in rows} <= {"live", "catch_up"}
    assert any(r["real"] and r["real"]["level"] for r in rows if r["state"] == "OK"), "yeterli seviye de sınandı"
    v = AE.verify(w.root)
    assert v["fold_targets"] == len(rows) and v["agree"] == len(rows) and v["mismatch"] == 0
    assert v["missing_in_store"] == 0 and v["extra_in_store"] == 0 and v["agreement"] == 1.0
    # koşan sayaçlar (panel) = çevrimdışı katlamanın sayaçları (CLI)
    off = AE.fold_store(w.root, born_ms=AE.load_born(w.root), collect=False)
    assert off.tallies() == adv.fold.tallies() and off.ring_24h() == adv.fold.ring_24h()
    st = json.loads((w.root / "advice" / AL.STATUS_FILE).read_text(encoding="utf-8"))
    assert st["tallies"] == off.tallies() and st["running"] == AE.running_from_tallies(off.tallies())
    assert st["state"] == "OK" and st["counters"]["gaps"] == 0 and st["effect"] == "NONE"
    assert w.xp.health()["advisor"]["state"] == "OK"


def _restart_run(tmp: Path, *, prep=None, n: int = 30, at: int = 16, **kw) -> LiveWorld:
    return run_world(tmp, n, restart_at=(at,), before_restart=prep, advisor_snapshot_every_steps=5, **kw)


def test_restart_with_snapshot_and_without_gives_identical_cores(tmp_path):
    base = run_world(tmp_path / "base", 30, advisor_snapshot_every_steps=5)
    with_snap = _restart_run(tmp_path / "snap")
    assert (with_snap.root / "advice" / AL.STATE_FILE).exists()

    def drop_snapshot(w, xp):
        (w.root / "advice" / AL.STATE_FILE).unlink()
    no_snap = _restart_run(tmp_path / "nosnap", prep=drop_snapshot)
    ref = base.cores()
    assert ref and with_snap.cores() == ref and no_snap.cores() == ref
    a = with_snap.xp._advisor
    assert a.c["snapshot_loaded"] == 1 and a.c["rebuilds"] == 0 and a.mode == "live"
    b = no_snap.xp._advisor
    assert b.c["rebuilds"] == 1 and b.c["snapshot_loaded"] == 0
    modes = {r["meta"]["fold_mode"] for r in no_snap.advice_rows()}
    assert modes == {"live", "catch_up"} or modes == {"live"}
    for w in (with_snap, no_snap):
        v = AE.verify(w.root)
        assert v["agreement"] == 1.0 and v["missing_in_store"] == 0


@pytest.mark.parametrize("damage", ["garbage", "wrong_sha", "bad_body_sha"])
def test_corrupt_or_foreign_snapshot_triggers_a_rebuild(tmp_path, damage):
    def corrupt(w, xp):
        p = w.root / "advice" / AL.STATE_FILE
        if damage == "garbage":
            p.write_bytes(b"\x00not a snapshot")
            return
        raw = gzip.decompress(p.read_bytes())
        nl = raw.index(b"\n")
        hdr = json.loads(raw[:nl])
        if damage == "wrong_sha":
            hdr["advisor_sha"] = "0" * 16
        else:
            hdr["body_sha256"] = "0" * 64
        p.write_bytes(gzip.compress(json.dumps(hdr).encode() + raw[nl:]))
    ref = run_world(tmp_path / "base", 26, advisor_snapshot_every_steps=5).cores()
    w = _restart_run(tmp_path / damage, prep=corrupt, n=26, at=14)
    a = w.xp._advisor
    assert a.c["snapshot_rejected"] == 1 and a.c["rebuilds"] == 1
    assert w.cores() == ref and AE.verify(w.root)["agreement"] == 1.0


def test_watermark_row_rotated_into_the_archive_is_found_in_the_new_segment(tmp_path):
    snap_steps: list[int] = []

    def kill_advisor(w, xp):
        # danışman "çöktü": anlık görüntüden sonra toplayıcı yazmaya ve döndürmeye devam eder (danışmansız süreç)
        snap_steps.append(w.k)
        rot0 = xp.store.rotations
        xp._advisor = None
        while xp.store.rotations < rot0 + 2:
            w.tour(xp)
    w = run_world(tmp_path, 34, restart_at=(14,), before_restart=kill_advisor, hot_max_lines=60,
                  advisor_snapshot_every_steps=5)
    a = w.xp._advisor
    assert a.c["snapshot_loaded"] == 1 and a.c["watermark_lost"] == 0 and a.c["rebuilds"] == 0
    man = json.loads((w.root / "archive" / "manifest.json").read_text(encoding="utf-8"))
    assert len(man["segments"]) >= 2
    v = AE.verify(w.root)
    assert v["agreement"] == 1.0 and v["missing_in_store"] == 0


def test_forced_catch_up_produces_identical_content(tmp_path, monkeypatch):
    ref = run_world(tmp_path / "ref", 24).cores()
    orig = AL.LiveAdvisor.on_flush

    def slow(self, written, **kw):
        if 6 <= self.steps < 16:                                # doğumdan SONRA 10 adım: her canlı toplu yazım
            self._ms_per_row = 1e9                              # "çok pahalı" → okuyucu depodan katlar
        else:
            self._ms_per_row = 0.5
        return orig(self, written, **kw)
    monkeypatch.setattr(AL.LiveAdvisor, "on_flush", slow)
    w = run_world(tmp_path / "forced", 24)
    assert w.cores() == ref
    a = w.xp._advisor
    assert a.c["live_deferred"] >= 1 and a.c["catch_up_steps"] > 8 and a.mode == "live"
    assert {r["meta"]["fold_mode"] for r in w.advice_rows()} == {"catch_up", "live"}


def test_gap_detection_switches_to_catch_up_and_loses_nothing(tmp_path, monkeypatch):
    ref = run_world(tmp_path / "ref", 20).cores()
    calls = {"n": 0}
    orig = AL.LiveAdvisor.on_flush

    def flaky(self, written, **kw):
        calls["n"] += 1
        if calls["n"] == 9:                                     # toplayıcı yazdı ama kanca bu adım çağrılmadı
            return {"ms": 0.0}
        return orig(self, written, **kw)
    monkeypatch.setattr(AL.LiveAdvisor, "on_flush", flaky)
    w = run_world(tmp_path / "gap", 20)
    assert w.xp._advisor.c["gaps"] >= 1 and w.cores() == ref


# ============================================================================ sınırlar ve süreler
def test_tail_boundary_exactly_72h_is_ok_one_ms_older_is_late():
    clock = X.T0_MS + 1                                         # recorded_at = T0 + 1 ms (µs 0)
    as_of = X.T0_MS - A.TAIL_H_MS
    rows = [X.cf("strategy_paper_box", "a", as_of, rec=X.iso_us(X.T0_MS), setup="box_fade"),
            X.cf("strategy_paper_box", "b", as_of, rec=X.iso_us(X.T0_MS), setup="box_fade")]
    _f, adv = X.fold_all(rows[:1])
    assert adv[0]["state"] == "OK" and adv[0]["batch_clock_ms"] == X.T0_MS
    rows[1]["recorded_at"] = X.iso_us(clock)
    _f, adv = X.fold_all(rows[1:])
    assert adv[0]["batch_clock_ms"] == X.T0_MS + 1 and adv[0]["state"] == "LATE" and adv[0]["advice"] is None


def test_context_and_value_wait_ttls_and_orphans():
    f = A.AdvisorFold()
    t0 = X.T0_MS
    cfk = X.cf("strategy_paper_box", "c", t0, rec=X.iso_us(t0), setup="box_fade")
    real = X.entry("main", "E1", t0, rec=X.iso_us(t0), setup="pullback")
    orphan = X.outcome("main", "E2", t0 - HOUR, t0 - 1000, -1.0, rec=X.iso_us(t0), setup="pullback")
    f.fold_batch([cfk, real, orphan])
    assert len(f._ctx) == 2 and len(f._vw) == 1 and f.counters["orphan_values"] == 1
    f.fold_batch([X.cf("strategy_paper_box", "z1", t0 + 7 * DAY, rec=X.iso_us(t0 + 7 * DAY), setup="box_fade")])
    assert f.counters["val_wait_expired"] == 0                   # tam 7 gün: henüz değil
    f.fold_batch([X.cf("strategy_paper_box", "z2", t0 + 7 * DAY + 1, rec=X.iso_us(t0 + 7 * DAY + 1),
                       setup="box_fade")])
    assert f.counters["val_wait_expired"] == 1 and len(f._vw) == 0
    late_entry = X.entry("main", "E2", t0 - HOUR, rec=X.iso_us(t0 + 8 * DAY), setup="pullback")
    f.fold_batch([late_entry])                                  # bağlam geldi ama değer süresi dolmuştu: kanıt YOK
    assert f.counters["evidence_real"] == 0
    f.fold_batch([X.cf("strategy_paper_box", "z3", t0 + 61 * DAY, rec=X.iso_us(t0 + 61 * DAY), setup="box_fade")])
    assert f.counters["ctx_expired"] >= 1 and A.key_hash("xp_cf", "strategy_paper_box|c") not in f._ctx
    # süresi dolan bağlamın anahtarı TAMAMLANMIŞTIR: sonraki etiket yok sayılır
    lab = X.cf("strategy_paper_box", "c", t0, rec=X.iso_us(t0 + 62 * DAY), rev=1, status=R.LABELLED, r=-1.0,
               labeled_ms=t0 + 62 * DAY - 5, setup="box_fade")
    n0 = f.counters["dup_ignored"]
    f.fold_batch([lab])
    assert f.counters["dup_ignored"] == n0 + 1 and f.counters["evidence_cf"] == 0
    # gerçek bağlam 365 gün yaşar
    assert A.key_hash("xp_entry", real["trade_key"]) in f._ctx
    f.fold_batch([X.cf("strategy_paper_box", "z4", t0 + 366 * DAY, rec=X.iso_us(t0 + 366 * DAY), setup="box_fade")])
    assert A.key_hash("xp_entry", real["trade_key"]) not in f._ctx


# ============================================================================ tekrarlar
def test_duplicates_resync_reemission_duplicate_target_and_reappearance_chain():
    t0 = X.T0_MS
    rows = X.random_store(21)
    _f, adv = X.fold_all(rows)
    # yeniden eşitleme: bütün depo sonraki bir toplu yazımda aynen yeniden yayınlanır
    again = [dict(r, recorded_at=X.iso_us(t0 + 40 * X.STEP, 9)) for r in rows]
    f2, adv2 = X.fold_all(rows + again)
    assert [a["core_sha"] for a in adv2] == [a["core_sha"] for a in adv]
    assert f2.counters["dup_ignored"] >= len({a["target"]["key"] for a in adv})
    # kaybolup yeniden beliren karşı-olgusal: rev 1 DROPPED anahtarı kapatır; rev 3 LABELLED kanıt OLMAZ
    chain = [X.cf("strategy_paper_box", "rc", t0, rec=X.iso_us(t0), setup="box_fade"),
             X.cf("strategy_paper_box", "rc", t0, rec=X.iso_us(t0 + X.STEP), rev=1, status=R.DROPPED, setup="box_fade"),
             X.cf("strategy_paper_box", "rc", t0, rec=X.iso_us(t0 + 2 * X.STEP), rev=2, setup="box_fade"),
             X.cf("strategy_paper_box", "rc", t0, rec=X.iso_us(t0 + 3 * X.STEP), rev=3, status=R.LABELLED, r=-1.0,
                  labeled_ms=t0 + 3 * X.STEP, setup="box_fade")]
    f3, adv3 = X.fold_all(chain)
    assert len(adv3) == 1 and f3.counters["evidence_cf"] == 0 and f3.counters["vanished"] == 1
    assert f3.counters["dup_ignored"] == 1 and f3.counters["resolved_excluded"] == 1


def test_hash_collision_stub_is_deterministic(monkeypatch):
    rows = [X.cf("strategy_paper_box", "x1", X.T0_MS, rec=X.iso_us(X.T0_MS), setup="box_fade"),
            X.cf("strategy_paper_box", "x2", X.T0_MS + 1000, rec=X.iso_us(X.T0_MS), setup="box_fade")]
    monkeypatch.setattr(A, "key_hash", lambda kind, key: 42)
    outs = [X.fold_all(rows) for _ in range(2)]
    for f, adv in outs:
        assert len(adv) == 1 and adv[0]["target"]["key"].endswith("x1") and f.counters["dup_ignored"] == 1
    assert outs[0][1] == outs[1][1]


def test_clock_regression_is_flagged_and_counted():
    t0 = X.T0_MS
    rows = [X.cf("strategy_paper_box", "a", t0 + MIN, rec=X.iso_us(t0), setup="box_fade"),
            X.cf("strategy_paper_box", "b", t0 - 5 * MIN, rec=X.iso_us(t0 - X.STEP), setup="box_fade"),
            X.cf("strategy_paper_box", "c", t0 + X.STEP + MIN, rec=X.iso_us(t0 + X.STEP), setup="box_fade")]
    f, adv = X.fold_all(rows)
    by = {a["target"]["key"][-1]: a for a in adv}
    assert by["b"]["clock_anomaly"] is True and by["b"]["batch_clock_ms"] == t0
    assert by["a"]["clock_anomaly"] is False and by["c"]["clock_anomaly"] is False
    assert f.counters["clock_nonincreasing"] == 1


# ============================================================================ doğum
def test_advisor_born_is_set_once_and_survives_rebuild_and_meta_loss(tmp_path):
    w = run_world(tmp_path, 12, advisor_snapshot_every_steps=5)
    born = w.xp._advisor.born_ms
    meta = w.root / "advice" / AL.META_FILE
    assert born is not None and json.loads(meta.read_text())["advisors"][A.ADVISOR_SHA]["advisor_born_ms"] == born
    rows = w.advice_rows()
    pre = [r for r in rows if r["batch_clock_ms"] < born]
    post = [r for r in rows if r["batch_clock_ms"] >= born]
    assert pre and post
    assert all(r["prospective"] is False and r["advisor_born_ms"] is None for r in pre)
    assert all(r["prospective"] is True and r["advisor_born_ms"] == born for r in post)
    # anlık görüntü ve meta kayıp → yeniden kurulum doğumu TAŞIMAZ (tavsiye satırlarından geri okunur)
    (w.root / "advice" / AL.STATE_FILE).unlink()
    meta.unlink()
    xp2 = w.collector()
    for _ in range(4):
        w.tour(xp2)
    a2 = xp2._advisor
    assert a2.born_ms == born and a2.c["rebuilds"] == 1
    assert json.loads(meta.read_text())["advisors"][A.ADVISOR_SHA]["advisor_born_ms"] == born
    assert AE.verify(w.root)["agreement"] == 1.0
