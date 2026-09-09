"""BELLEK ONARIMI V2 — V1'de kapatilmayan iki yasam dongusu bosluğu.

V1 (`test_snapshot_memory_v1.py`) akisi, tur basina tek ayristirmayi ve tur SONUNDA birakmayi
kilitledi. Acik kalan iki soru vardi:

  A) Aday dosyasi TUR ICINDE, iki tuketicinin ARASINDA buyurse memo bayat kalir mi, yeniden
     ayristirir mi, eski grafik serbest birakilir mi? (Uretimde her tur ~20-30 yeni aday
     yazilir; iki tuketici arasinda dosyanin buyumesi normal, istisna degil.)
  B) Tur bir ISTISNA ile kesilirse memo asili kalir mi ve ard arda basarisiz turlarda birikir mi?
     `tour()` genelinde try/finally YOKTUR; savunma bir sonraki turun BASINDAKI birakmadir.

Bu dosya yalniz bu iki soruyu olcer. OOM'un kendisi tek turluk bir dususle kapanmis SAYILMAZ:
uzun sureli dogrulama VPS'te cgroup sayaclariyla yapilir (bkz. `scripts/vps_memory_probe.sh`).
"""
from __future__ import annotations

import json
import sys
import tracemalloc
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tradingbot.learn.entry_snapshot import EntrySnapshotStore  # noqa: E402


def _snap(cid: str, symbol: str = "ETH/USDT", **extra) -> dict:
    return {"schema_version": "entry_snapshot_v1", "candidate_id": cid, "symbol": symbol,
            "direction": "LONG", "ts": "2026-09-09T12:00:00+00:00", **extra}


def _fat(cid: str) -> dict:
    """Uretim satirlarina yakin agirlikta bir kayit (~1 KB)."""
    return _snap(cid, features={f"f{i}": i * 1.5 for i in range(40)},
                 provenance={"leaf": "X|pullback", "note": "x" * 200})


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


class _PathWithStat:
    """Gercek Path'e delege eder, yalniz `stat()`i degistirir.

    `Path.stat`i global yamalamak pathlib'in kendi ic islerini bozar; imza davranisini olcmek
    icin YALNIZ deponun kullandigi yolun `stat`i degistirilir.
    """

    def __init__(self, real: Path, stat_fn):
        self._real, self._stat_fn = real, stat_fn

    def stat(self, *a, **k):
        return self._stat_fn(self._real)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __fspath__(self):
        return str(self._real)

    def __truediv__(self, other):
        return self._real / other

    def __str__(self):
        return str(self._real)


class _FrozenStat:
    def __init__(self, st):
        self.st_mtime_ns, self.st_size = 1_000_000_000, st.st_size


# --------------------------------------------------------------- A) tur ici buyume
def test_01_growth_between_two_consumers_is_reparsed(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_snap("c1"), _snap("c2")])
    store = EntrySnapshotStore(p)
    first = store.by_candidate()                       # tuketici 1 (karlilik deneyi)
    assert set(first) == {"c1", "c2"}
    store.append(_snap("c3"))                          # tur ortasinda yeni aday yazildi
    second = store.by_candidate()                      # tuketici 2 (giris degerlendirmesi)
    assert set(second) == {"c1", "c2", "c3"}, "ikinci tuketici bayat memodan beslendi"
    assert store._bc_cache is not None and set(store._bc_cache) == {"c1", "c2", "c3"}


def test_02_reparsing_does_not_accumulate_graphs(tmp_path):
    """Tekrar tekrar yeniden ayristirma memoda grafik YIGMAZ: tek grafik tutulur, eski birakilir."""
    p = tmp_path / "s.jsonl"
    _write(p, [_fat(f"c{i}") for i in range(400)])
    store = EntrySnapshotStore(p)
    tracemalloc.start()
    try:
        s0 = tracemalloc.get_traced_memory()[0]
        store.by_candidate()                                   # ilk ayristirma
        s1 = tracemalloc.get_traced_memory()[0]
        one_graph = s1 - s0                                    # OLCULEN tek grafik agirligi
        for i in range(5):                                     # bes kez buyut + yeniden ayristir
            store.append(_fat(f"new{i}"))
            store.by_candidate()
        s2 = tracemalloc.get_traced_memory()[0]
        growth = s2 - s1
    finally:
        tracemalloc.stop()
    assert len(store._bc_cache) == 405
    assert one_graph > 100_000, f"olcum anlamsiz: tek grafik {one_graph} B"
    assert growth < one_graph, (
        f"bes yeniden ayristirma sonrasi artis {growth} B, tek grafik {one_graph} B — "
        "eski grafikler birakilmiyor")


def test_03_signature_reacts_to_size_even_if_mtime_is_frozen(tmp_path):
    """Imza `(mtime_ns, size)`; kaba zaman damgali dosya sistemlerinde BOYUT tek basina yeter."""
    p = tmp_path / "s.jsonl"
    _write(p, [_snap("c1")])
    store = EntrySnapshotStore(p)
    store.path = _PathWithStat(p, lambda real: _FrozenStat(real.stat()))
    assert set(store.by_candidate()) == {"c1"}
    store.append(_snap("c2"))
    assert set(store.by_candidate()) == {"c1", "c2"}, "mtime donmusken boyut degisimi kacirildi"


def test_04_unreadable_signature_disables_the_memo_rather_than_serving_stale(tmp_path):
    """Imza cozulemezse (`stat` hatasi) memo KULLANILMAZ ve cagri COKMEZ."""
    p = tmp_path / "s.jsonl"
    _write(p, [_snap("c1")])
    store = EntrySnapshotStore(p)
    assert set(store.by_candidate()) == {"c1"}

    def _boom(_real):
        raise OSError("stat basarisiz")

    store.path = _PathWithStat(p, _boom)
    assert store._hot_signature() is None
    _write(p, [_snap("c1"), _snap("c2")])
    assert set(store.by_candidate()) == {"c1", "c2"}, "imza yokken BAYAT memo servis edildi"


# --------------------------------------------------------------- B) istisna ile kesilen tur
def test_05_repeated_failed_tours_do_not_accumulate_memos(tmp_path, monkeypatch):
    """Ard arda ISTISNA ile biten turlar memoyu ustuste yigmamali.

    `tour()` genelinde try/finally yok; savunma bir sonraki turun BASINDAKI birakmadir. Bu test
    gercekten istisna atarak calistirir, `test_06b`'deki gibi asili memoyu taklit etmez.
    """
    import test_engine_v3 as TE
    eng = TE._engine(tmp_path, monkeypatch, p_win=0.65)
    store = getattr(eng, "entry_snapshot_store", None)
    if store is None:
        pytest.skip("bu yapilandirmada aday snapshot deposu kurulmuyor")

    store.append(_snap("seed"))                         # memo yalniz sicak dosya VARSA devreye girer
    boom = {"n": 0}
    real_marks = eng._marks

    def exploding(briefs):
        store.by_candidate()                            # memo dolar
        boom["n"] += 1
        raise RuntimeError("tur ortasinda cokme")

    monkeypatch.setattr(eng, "_marks", exploding)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            eng.tour(do_scan=False, obsidian=False, charts=False)
        assert store._bc_cache is not None              # cokme aninda asili kaldi (beklenen)
    assert boom["n"] == 3

    monkeypatch.setattr(eng, "_marks", real_marks)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert store._bc_cache is None, "basarili tur asili memoyu birakmadi"


def test_06_drop_is_exception_safe(tmp_path, monkeypatch):
    """`_drop_entry_snapshot_cache()` istisna SIZDIRMAZ: bir turu bellek temizligi dusuremez."""
    import test_engine_v3 as TE
    eng = TE._engine(tmp_path, monkeypatch, p_win=0.65)
    store = getattr(eng, "entry_snapshot_store", None)
    if store is None:
        pytest.skip("bu yapilandirmada aday snapshot deposu kurulmuyor")
    monkeypatch.setattr(store, "drop_hot_cache",
                        lambda: (_ for _ in ()).throw(RuntimeError("temizlik patladi")))
    eng._drop_entry_snapshot_cache()                    # yutulmali
