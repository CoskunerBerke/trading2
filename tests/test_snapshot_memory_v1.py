"""BELLEK ONARIMI V1 — sıcak snapshot okumasının sabit bellekli olması ve tur başına tek ayrıştırma.

Üretim kusuru (2026-09-09): worker 4 GiB cgroup sınırında OOM ile öldürüldü (status=9/KILL).
Kök neden ölçüldü: `entry_snapshot.jsonl` 93 MB'a çıkmıştı; `iter_hot_rows()` dosyanın TAMAMINI
tek string, sonra TÜM satırları ayrı liste olarak belleğe alıyordu ve `by_candidate()` motor
tarafından TUR BAŞINA İKİ KEZ çağrılıyordu. Gerçek dosyayla ölçüm: çağrı başına 631 MB tepe,
tur başına 889 MB. Akış + imza anahtarlı memo ile tur başına 258 MB.

Bu testler DAVRANIŞIN AYNI kaldığını ve onarımın gerçekten yükü kaldırdığını kanıtlar.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tradingbot.learn.entry_snapshot import EntrySnapshotStore  # noqa: E402
from tradingbot.learn.position_path import PositionPathStore  # noqa: E402


def _snap(cid: str, symbol: str = "ETH/USDT", **extra) -> dict:
    return {"schema_version": "entry_snapshot_v1", "candidate_id": cid, "symbol": symbol,
            "direction": "LONG", "ts": "2026-09-09T12:00:00+00:00", **extra}


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


# ----------------------------------------------------------------- akış: davranış aynı
def test_01_iter_hot_rows_is_streaming_and_semantics_unchanged(tmp_path):
    p = tmp_path / "entry_snapshot.jsonl"
    rows = [_snap("c1"), _snap("c2", "SOL/USDT"),
            {"kind": "link", "candidate_id": "c1", "trade_id": "F1"},   # link satırı da candidate_id taşır
            {"no_candidate": True}]                                      # candidate_id yok → atlanır
    _write(p, rows)
    p.write_text(p.read_text(encoding="utf-8") + "\n   \nBOZUK JSON\n", encoding="utf-8")
    store = EntrySnapshotStore(p)
    got = list(store.iter_hot_rows())
    assert [r.get("candidate_id") for r in got] == ["c1", "c2", "c1"]     # bozuk/boş/kimliksiz elenir
    import inspect
    assert inspect.isgeneratorfunction(EntrySnapshotStore.iter_hot_rows)


def test_01a_iter_hot_rows_peak_memory_stays_far_below_file_size(tmp_path):
    """DAVRANIŞSAL kanıt: tam gezinmede tepe bellek dosya boyutunun küçük bir kesri olmalı."""
    import gc
    import tracemalloc
    p = tmp_path / "big.jsonl"
    payload = {"features": {f"f{i}": i * 1.5 for i in range(40)}}
    _write(p, [_snap(f"c{i}", **payload) for i in range(6000)])
    size = p.stat().st_size
    assert size > 3_000_000, "test dosyası anlamlı büyüklükte olmalı"
    store = EntrySnapshotStore(p)
    gc.collect()
    tracemalloc.start()
    n = sum(1 for _ in store.iter_hot_rows())          # sonuç TUTULMAZ: saf akış
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert n == 6000
    assert peak < size * 0.10, ("akış değil: tepe=%.1f MB dosya=%.1f MB" % (peak / 1e6, size / 1e6))


def test_01b_missing_file_and_oserror_are_silent(tmp_path):
    store = EntrySnapshotStore(tmp_path / "yok.jsonl")
    assert list(store.iter_hot_rows()) == []
    assert store.by_candidate() == {}


# ----------------------------------------------------------------- memo: tur başına tek ayrıştırma
def test_02_by_candidate_parses_once_per_signature(tmp_path, monkeypatch):
    p = tmp_path / "s.jsonl"
    _write(p, [_snap("c1"), _snap("c2")])
    store = EntrySnapshotStore(p)
    calls = {"n": 0}
    orig = EntrySnapshotStore.iter_hot_rows

    def counted(self):
        calls["n"] += 1
        yield from orig(self)

    monkeypatch.setattr(EntrySnapshotStore, "iter_hot_rows", counted)
    a = store.by_candidate()
    b = store.by_candidate()
    assert set(a) == set(b) == {"c1", "c2"}
    assert calls["n"] == 1, "ikinci çağrı dosyayı yeniden ayrıştırdı — memo çalışmıyor"


def test_02b_memo_invalidates_when_file_changes(tmp_path, monkeypatch):
    p = tmp_path / "s.jsonl"
    _write(p, [_snap("c1")])
    store = EntrySnapshotStore(p)
    assert set(store.by_candidate()) == {"c1"}
    store.append(_snap("c2"))                                   # gerçek ekleme yolu
    assert set(store.by_candidate()) == {"c1", "c2"}, "memo bayat kaldı"


def test_02c_returned_dict_is_a_copy_caller_mutation_cannot_poison_memo(tmp_path):
    """Motor (`_write_entry_eval`) dönen sözlüğe eski bellek kayıtlarını EKLER."""
    p = tmp_path / "s.jsonl"
    _write(p, [_snap("c1")])
    store = EntrySnapshotStore(p)
    first = store.by_candidate()
    first["ENJEKTE"] = {"candidate_id": "ENJEKTE"}
    second = store.by_candidate()
    assert "ENJEKTE" not in second, "çağıranın mutasyonu memoya sızdı"
    assert set(second) == {"c1"}


def test_02d_drop_hot_cache_releases_the_graph(tmp_path, monkeypatch):
    p = tmp_path / "s.jsonl"
    _write(p, [_snap("c1")])
    store = EntrySnapshotStore(p)
    store.by_candidate()
    assert store._bc_cache is not None
    store.drop_hot_cache()
    assert store._bc_cache is None and store._bc_sig is None
    calls = {"n": 0}
    orig = EntrySnapshotStore.iter_hot_rows

    def counted(self):
        calls["n"] += 1
        yield from orig(self)

    monkeypatch.setattr(EntrySnapshotStore, "iter_hot_rows", counted)
    store.by_candidate()
    assert calls["n"] == 1                                       # bırakıldıktan sonra yeniden okur


def test_02e_known_ids_and_append_dedup_still_work(tmp_path):
    p = tmp_path / "s.jsonl"
    store = EntrySnapshotStore(p)
    assert store.append(_snap("c1")) is True
    assert store.append(_snap("c1")) is False and store.duplicates == 1
    assert store.known_ids() == {"c1"}
    assert store.append(_snap("c2")) is True
    assert store.known_ids() == {"c1", "c2"}


def test_02f_archive_path_is_not_memoised(tmp_path):
    """`include_archive=True` çevrimdışı yoldur; sıcak imzayla korunmaz, memolanmamalı."""
    p = tmp_path / "s.jsonl"
    _write(p, [_snap("c1")])
    store = EntrySnapshotStore(p)
    out = store.by_candidate(include_archive=True)
    assert set(out) == {"c1"}
    assert store._bc_cache is None, "arşiv yolu sıcak memoyu doldurdu"


# ----------------------------------------------------------------- position_path akışı
def test_03_position_path_iter_rows_is_streaming(tmp_path):
    p = tmp_path / "position_path.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in
                         [{"trade_id": "F1", "mfe_r": 1.0}, {"no_trade": 1}, {"trade_id": "F2"}]) +
                 "\nBOZUK\n", encoding="utf-8")
    store = PositionPathStore(p)
    assert [r["trade_id"] for r in store.iter_rows()] == ["F1", "F2"]
    import inspect
    assert inspect.isgeneratorfunction(PositionPathStore.iter_rows)
    assert list(PositionPathStore(tmp_path / "yok.jsonl").iter_rows()) == []
    # DAVRANIŞSAL: büyük dosyada tam gezinmenin tepe belleği dosyanın küçük bir kesri
    import gc
    import tracemalloc
    big = tmp_path / "big_path.jsonl"
    big.write_text("".join(json.dumps({"trade_id": f"F{i}", "mfe_r": i * 0.01,
                                       "blob": {f"k{j}": j for j in range(40)}}) + "\n"
                           for i in range(6000)), encoding="utf-8")
    size = big.stat().st_size
    s2 = PositionPathStore(big)
    gc.collect()
    tracemalloc.start()
    n = sum(1 for _ in s2.iter_rows())
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert n == 6000 and peak < size * 0.10, ("akış değil: tepe=%.1f MB dosya=%.1f MB" % (peak / 1e6, size / 1e6))


# ----------------------------------------------------------------- ölçülen kazanç
@pytest.mark.parametrize("n_rows", [4000])
def test_04_peak_memory_is_bounded_and_single_parse_wins(tmp_path, n_rows):
    """Sentetik ama gerçekçi boyutta dosyada tepe belleği ölç: akış + memo, iki tam ayrıştırmadan düşük."""
    import gc
    import tracemalloc
    p = tmp_path / "big.jsonl"
    payload = {"features": {f"f{i}": i * 1.5 for i in range(60)},
               "specialist_scores": {f"s{i}": i * 0.5 for i in range(20)}}
    _write(p, [_snap(f"c{i}", **payload) for i in range(n_rows)])
    size_mb = p.stat().st_size / 1e6
    assert size_mb > 3, "test dosyası anlamlı büyüklükte olmalı"
    store = EntrySnapshotStore(p)

    def eski_iki_ayristirma():
        """Onarim ONCESI davranis: tam okuma + iki bagimsiz ayristirma."""
        out = []
        for _ in range(2):
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = [ln for ln in text.splitlines() if ln.strip()]
            out.append({json.loads(ln)["candidate_id"]: json.loads(ln) for ln in lines})
        return out[0]

    gc.collect(); tracemalloc.start()
    eski = eski_iki_ayristirma()
    _, eski_tepe = tracemalloc.get_traced_memory()
    tracemalloc.stop(); del eski; gc.collect()

    gc.collect(); tracemalloc.start()
    a = store.by_candidate()
    b = store.by_candidate()          # memo: yeniden ayrıştırma YOK
    _, yeni_tepe = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert set(a) == set(b) and len(a) == n_rows
    del a, b; store.drop_hot_cache(); gc.collect()

    assert yeni_tepe < eski_tepe * 0.75, (
        "tepe bellek beklendiği kadar düşmedi: eski=%.1f MB yeni=%.1f MB" % (eski_tepe / 1e6, yeni_tepe / 1e6))


# ----------------------------------------------------------------- E ajanının bulduğu kusurlar
def test_05_hot_line_count_is_streaming_and_correct(tmp_path):
    """`retention_stats()` yalnız SAYIYA ihtiyaç duyar; dosyayı belleğe almamalı.

    Bağımsız doğrulamada saptandı: `len(self._hot_lines())` üretim dosyasında (93.6 MB) her turda
    374 MB tepe üretiyordu — akış onarımının kaçırdığı en büyük tek sıçrama.
    """
    import gc
    import tracemalloc
    p = tmp_path / "s.jsonl"
    payload = {"features": {f"f{i}": i * 1.5 for i in range(40)}}
    _write(p, [_snap(f"c{i}", **payload) for i in range(6000)])
    p.write_text(p.read_text(encoding="utf-8") + "\n   \n", encoding="utf-8")   # boş satırlar sayılmaz
    size = p.stat().st_size
    store = EntrySnapshotStore(p)
    assert store._hot_line_count() == 6000 == len(store._hot_lines())          # aynı sonuç
    gc.collect()
    tracemalloc.start()
    n = store._hot_line_count()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert n == 6000
    assert peak < size * 0.05, ("sayım akış değil: tepe=%.1f MB dosya=%.1f MB" % (peak / 1e6, size / 1e6))
    assert EntrySnapshotStore(tmp_path / "yok.jsonl")._hot_line_count() == 0
    st = store.retention_stats()
    assert st["hot_rows"] == 6000 and st["silent_deletion"] is False


def test_06_engine_drops_the_memo_after_the_last_consumer(tmp_path, monkeypatch):
    """Memo TUR SONUNDA, SON tüketiciden sonra bırakılmalı.

    Bağımsız doğrulamada saptandı: bırakma `_write_entry_eval` ile `_run_profitability_experiment`
    ARASINA konmuştu. Sonuç, kod yorumunun söylediğinin tam tersiydi — son tüketici yeniden
    ayrıştırıyor VE memo turlar arası ~258 MB kalıcı kalıyordu.
    """
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).parent))
    import test_engine_v3 as TE

    eng = TE._engine(tmp_path, monkeypatch, p_win=0.65)
    store = getattr(eng, "entry_snapshot_store", None)
    if store is None:
        pytest.skip("bu yapılandırmada aday snapshot deposu kurulmuyor")

    order: list[str] = []
    orig_drop = store.drop_hot_cache
    orig_by = store.by_candidate

    def spy_drop():
        order.append("drop")
        return orig_drop()

    def spy_by(*a, **k):
        order.append("by_candidate")
        return orig_by(*a, **k)

    monkeypatch.setattr(store, "drop_hot_cache", spy_drop)
    monkeypatch.setattr(store, "by_candidate", spy_by)
    eng.tour(do_scan=False, obsidian=False, charts=False)

    assert "drop" in order, "memo hiç bırakılmadı — turlar arası bellekte kalır"
    if "by_candidate" in order:
        son_tuketici = len(order) - 1 - order[::-1].index("by_candidate")
        son_drop = len(order) - 1 - order[::-1].index("drop")
        assert son_drop > son_tuketici, (
            "memo SON tüketiciden ÖNCE bırakıldı → son tüketici yeniden ayrıştırır ve memo "
            "turlar arası kalıcı olur. Sıra: %r" % order)
    assert store._bc_cache is None, "tur bitti ama ayrıştırılmış grafik hâlâ bellekte"


def test_06b_memo_is_dropped_at_tour_start_after_a_failed_tour(tmp_path, monkeypatch):
    """`tour()` genelinde try/finally YOK: istisna atan bir tur memoyu asılı bırakabilir.

    Savunma, bir sonraki turun BAŞINDA bırakmaktır; bu test onu kilitler.
    """
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).parent))
    import test_engine_v3 as TE

    eng = TE._engine(tmp_path, monkeypatch, p_win=0.65)
    store = getattr(eng, "entry_snapshot_store", None)
    if store is None:
        pytest.skip("bu yapılandırmada aday snapshot deposu kurulmuyor")
    store.append(_snap("c1"))
    store.by_candidate()
    assert store._bc_cache is not None                      # önceki turdan asılı kalmış gibi
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert store._bc_cache is None
