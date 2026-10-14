"""`position_path.jsonl` ARTIMLI DİZİN (2026-09-28, öğrenme modu; üçüncü doğrulama turu).

Öğrenmede dosya ~60 kat hızlı büyür (uçtan uca koşu: günde 3667 satır / 4,6 MB; kapalıyken 75 KB). `stats()` (turda iki kez),
çıkış değerlendirmesi (`paths_by_trade`) ve kârlılık deneyi (son 2000 satır) dosyayı her turda baştan belleğe alıyordu
(30 günlük dosyada her biri +650 MB tepe). Dizin yalnız ofset tutar; çıktılar eski okuyucularla BİREBİR aynı olmalı.
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

from tradingbot.learn.position_path import PositionPathStore


def _row(i: int, tid: str, *, sid: str | None = None, ts: int | None = None, **kw) -> dict:
    r = {"trade_id": tid, "snapshot_id": sid if sid is not None else "s%d" % i, "ts_ms": 1_000 * (ts if ts is not None else i),
         "mark": 100.0 + i, "gross_r": 0.01 * i, "tick_kind": "last_only", "note": "ş"}
    r.update(kw)
    return r


def _append(path: Path, rows: list, *, crlf: bool = False) -> None:
    with open(path, "ab") as fh:
        for r in rows:
            line = r if isinstance(r, (bytes, str)) else json.dumps(r, ensure_ascii=False)
            if isinstance(line, str):
                line = line.encode("utf-8")
            fh.write(line + (b"\r\n" if crlf else b"\n"))


def _check(store: PositionPathStore, *, k=(1, 5, 50)) -> None:
    by = store.paths_by_trade()
    tids = set(by) | {"nope", "T1", 1}
    for t in tids:
        assert store.trade_path(t) == (by.get(t) or []), t
    for kk in k:
        assert store.last_rows(kk) == [r for r in store.iter_rows()][-kk:]
    assert store._path_counts() == (len(by), sum(len(v) for v in by.values()))
    st = store.stats()
    assert st["trades_with_path"] == len(by) and st["total_snapshots"] == sum(len(v) for v in by.values())


def test_index_equals_the_full_readers_over_many_appends(tmp_path):
    rng = random.Random(11)
    p = tmp_path / "position_path.jsonl"
    store = PositionPathStore(p)
    _check(store)                                                        # dosya yok
    i = 0
    for stage in range(15):
        rows: list = []
        for _ in range(rng.randint(1, 30)):
            i += 1
            x = rng.random()
            tid = "T%d" % rng.randint(1, 6)
            if x < 0.7:
                rows.append(_row(i, tid, ts=rng.randint(1, 500)))           # sıra dışı zaman: sıralama sınanır
            elif x < 0.78:
                rows.append(_row(i, tid, sid="s%d" % rng.randint(1, max(1, i - 1))))     # kopya snapshot kimliği
            elif x < 0.83:
                rows.append(_row(i, tid, sid=""))                            # kimliksiz: her zaman sayılır
            elif x < 0.87:
                rows.append({"snapshot_id": "x%d" % i, "mark": 1})           # trade_id yok → yok sayılır
            elif x < 0.9:
                rows.append(_row(i, 7))                                      # sayısal trade_id → anahtar "7"
            elif x < 0.93:
                rows.append("")
            elif x < 0.96:
                rows.append("{bozuk")
            else:
                rows.append(b"\xff\xfe" + json.dumps(_row(i, tid)).encode())     # çözülemeyen bayt → bozuk satır
        _append(p, rows, crlf=(stage % 4 == 1))
        _check(store)


def test_rotation_truncation_and_rewrite_reindex(tmp_path):
    p = tmp_path / "position_path.jsonl"
    store = PositionPathStore(p)
    _append(p, [_row(1, "A"), _row(2, "A"), _row(3, "B")])
    _check(store)
    new = tmp_path / "n.jsonl"
    _append(new, [_row(9, "C")])
    os.replace(new, p)
    _check(store)
    p.write_text(json.dumps(_row(4, "D")) + "\n", encoding="utf-8")
    _check(store)
    body = p.read_bytes()
    with open(p, "r+b") as fh:
        fh.write(body.replace(b'"D"', b'"E"'))
    _check(store)


@pytest.mark.parametrize("tail", [b'{"trade_id": "T9", "snapshot_id": "z"}', b"{bozuk"])
def test_unterminated_last_line_and_bare_cr_use_the_old_reader(tmp_path, tail):
    p = tmp_path / "position_path.jsonl"
    store = PositionPathStore(p)
    _append(p, [_row(1, "A"), _row(2, "B")])
    _check(store)
    with open(p, "ab") as fh:
        fh.write(tail)                                                     # `iter_rows` yarım son satırı da okur
    _check(store)
    _append(p, [_row(3, "A")])
    _check(store)
    _append(p, [b'{"trade_id": "C", "snapshot_id": "c1"}\r{"trade_id": "C", "snapshot_id": "c2"}'])
    _check(store)                                                          # metin kipi \r'de böler → eski yol


def test_engine_exit_eval_and_experiment_rows_are_unchanged(tmp_path):
    """Çıkış değerlendirmesi: kapanış başına `trade_path` == eski `paths_by_trade().get(...) or []`; değerlendirme ve
    toplam rapor aynı. Kârlılık deneyi: `last_rows(2000)` == eski `[... iter_rows()][-2000:]`."""
    from tradingbot.learn.exit_eval import aggregate, evaluate_trade
    from tradingbot.learn.exit_policy import ExitPolicyConfig
    p = tmp_path / "position_path.jsonl"
    store = PositionPathStore(p)
    rows = []
    for t in range(40):
        for j in range(60):
            rows.append(_row(t * 100 + j, "T%d" % t, ts=t * 3600 + j * 60, gross_r=0.02 * j - 0.3))
    _append(p, rows)
    cfg = ExitPolicyConfig.from_dict({"policy_version": "t"})
    closes = [{"trade_id": "T%d" % t, "symbol": "X/USDT", "side": "LONG", "r_multiple": 0.5,
               "opened_at": "1970-01-01T00:00:00+00:00", "closed_at": "1970-01-02T00:00:00+00:00"} for t in range(42)]
    paths = store.paths_by_trade()
    old = [evaluate_trade(trade_id=c["trade_id"], path=paths.get(c["trade_id"]) or [], close=c, cfg=cfg) for c in closes]
    new = [evaluate_trade(trade_id=c["trade_id"], path=store.trade_path(c["trade_id"]), close=c, cfg=cfg) for c in closes]
    assert new == old
    assert json.dumps(aggregate(new, cfg=cfg, now=None), sort_keys=True, default=str) == \
        json.dumps(aggregate(old, cfg=cfg, now=None), sort_keys=True, default=str)
    assert store.last_rows(2000) == [r for r in store.iter_rows()][-2000:]
    assert len(store.last_rows(2000)) == 2000


# ============================================================================ kârlılık deneyi olay defteri (akış)
def _old_iter_events(path: Path):
    """Eski `ExperimentStore.iter_events` (read_text + splitlines) — başvuru; (olaylar, bozuk sayısı)."""
    out, bad = [], 0
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(d, dict):
            out.append(d)
        else:
            bad += 1
    return out, bad


def test_experiment_event_stream_equals_reading_the_whole_file(tmp_path):
    """Öğrenmede olay defteri ~30 kat hızlı büyür (günde 4,4 MB) ve turda en az iki kez baştan okunur; akışlı okuma dosyayı
    metin olarak belleğe almaz. Olaylar, sıraları ve bozuk satır sayacı eski okumayla AYNI (her tür satır ayırıcı dahil)."""
    from tradingbot.learn.profitability_store import ExperimentStore
    rng = random.Random(5)
    parts = [b'{"event_id": "e1", "kind": "DECISION"}', b"", b"  ", b"[1, 2]", b"{bozuk", b'{"x": "\xc5\x9f"}',
             b'{"y": "a\xe2\x80\xa8b"}', b'{"z": "\xc2\x85"}', b"\xff\xfe{}", b'{"w": 1}\x0c{"v": 2}', b'"s"']
    for trial in range(40):
        seps = [b"\n", b"\r\n", b"\r", b"\n\n", b"\x0b"]
        body = b"".join(rng.choice(parts) + rng.choice(seps) for _ in range(rng.randint(0, 50)))
        if rng.random() < 0.3:
            body = body.rstrip(b"\r\n")
        if rng.random() < 0.2:
            body = b"\xef\xbb\xbf" + body
        st = ExperimentStore(tmp_path / ("s%d" % trial), experiment_id="pfexp_test")
        st.events_path.parent.mkdir(parents=True, exist_ok=True)
        st.events_path.write_bytes(body)
        got = list(st.iter_events())
        assert (got, st.malformed) == _old_iter_events(st.events_path), trial


def test_same_size_rewrite_in_the_first_4kb_reindexes(tmp_path):
    """(bulgu R4a-2, 2026-09-28 dördüncü doğrulama turu) Aynı inode + aynı boy yerinde değişiklik dosya başında (son 4 KB
    bekçisinin çok gerisinde): baş bekçisi tam yeniden dizine düşürür."""
    p = tmp_path / "position_path.jsonl"
    store = PositionPathStore(p)
    _append(p, [_row(i, "T%d" % (i % 7), pad="x" * 300) for i in range(1, 60)])
    assert p.stat().st_size > 4 * PositionPathStore.IX_GUARD_BYTES
    _check(store)
    body = p.read_bytes()
    i = body.index(b'"trade_id": "T1"')
    assert i < PositionPathStore.IX_GUARD_BYTES
    with open(p, "r+b") as fh:
        fh.write(body[:i] + b'"trade_id": "T8"' + body[i + 16:])
    _check(store)
    assert store.trade_path("T8") and store._path_counts()[0] == 8


def test_an_error_midway_through_indexing_does_not_index_rows_twice(tmp_path, monkeypatch):
    """(bulgu R4a-3) Dizin satır satır güncellenip ofset sonda işleniyordu: okuma ortasında G/Ç hatası yarım dizin bırakır,
    yeniden deneme satırları iki kez sayardı (`_path_counts`, `trade_path`, `last_rows`). Artık dizin sıfırlanır."""
    import tradingbot.learn.position_path as PP
    p = tmp_path / "position_path.jsonl"
    store = PositionPathStore(p)
    _append(p, [_row(1, "A", sid=""), _row(2, "A", sid="")])
    _check(store)
    _append(p, [_row(3, "A", sid=""), _row(4, "A", sid="")])
    real = json.loads
    state = {"n": 0}

    class J:
        JSONDecodeError = json.JSONDecodeError

        @staticmethod
        def loads(s, *a, **k):
            d = real(s, *a, **k)
            if isinstance(d, dict) and d.get("ts_ms") == 4000 and state["n"] == 0:
                state["n"] += 1
                raise OSError(5, "EIO (enjekte)")
            return d

    monkeypatch.setattr(PP, "json", J)
    with pytest.raises(OSError):
        store._path_counts()
    monkeypatch.setattr(PP, "json", json)
    _check(store)
    assert [r["ts_ms"] for r in store.trade_path("A")] == [1000, 2000, 3000, 4000]
