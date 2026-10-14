"""GRAFİK ANALİZİ — TOPLU İNDEKS YAZIMI (2026-10-01): tur sonunda index.json BAYT BAYT aynı, yazım tur başına bir kez.

Eski yol her yeni `analysis_id` için bütün indeksi okuyup (imza değiştiği için yeniden ayrıştırarak), kimliği bütün
satırlarda tarayıp, bütün indeksi yeniden yazıyordu (30 MB'ta 280 kayıt ≈ 264 sn). Yeni yol: kayıt dosyaları ve budama
aynı anda; indeks bellekte, `analysis_id` sayacıyla tekilleştirilir ve TEK kez yazılır.

Karşılaştırmanın referansı "bugünkü yol"dur: her kayıttan önce indeks DİSKTEN yeniden okunur. (Not: bugünkü süreç
önbelleği dosya imzasını (mtime_ns, boyut) kullanır; aynı zaman diliminde aynı boyutta iki hızlı yazım eski içeriği
döndürebilir. Üretimde her yazım 30 MB olduğundan pratikte görülmez; referansta önbellek her kayıttan önce temizlenir
ki karşılaştırma bu zamanlama tesadüfüne bağlı olmasın.)
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import tradingbot.chart_analysis_store as CAS  # noqa: E402
from tradingbot.chart_analysis_store import INDEX_FILE, ChartAnalysisStore  # noqa: E402

H4 = 14_400_000


def _snap(i: int, *, book="main", market="USDM_PERP", sym="SOL/USDT", tf="4h", aid=None, as_of=None) -> dict:
    as_of = int(as_of if as_of is not None else 1_790_000_000_000 + i * H4)
    return {"analysis_id": aid or ("a%08d" % i), "decision_fingerprint": "fp%d" % (i % 7),
            "identity": {"book_id": book, "market_type": market, "symbol": sym, "timeframe": tf, "as_of_ms": as_of,
                         "as_of": "2026-09-%02dT%02d:00:00+00:00" % (1 + i % 28, i % 24),
                         "last_closed_bar": {"timestamp": as_of - H4}, "code_sha": "c0de"},
            "elements": [{"kind": "x", "v": i}]}


def _workload(seed: int = 3, n: int = 400) -> list[dict]:
    """Çok seri, budama (keep küçük), aynı kimliğin tekrarı, geçersiz kimlik, piyasası bilinmeyen kayıt, sıra dışı zaman."""
    rnd = random.Random(seed)
    books, syms, markets = ["main", "strategy_paper", "d4"], ["SOL/USDT", "ETH/USDT", "BTC/USDT", "ADA/USDT"], ["USDM_PERP", "SPOT"]
    out = []
    for i in range(n):
        b, s, m = rnd.choice(books), rnd.choice(syms), rnd.choice(markets)
        out.append(_snap(i, book=b, market=m, sym=s, as_of=1_790_000_000_000 + rnd.randrange(0, 50) * H4))
        if i % 17 == 0:
            out.append(_snap(i, book=b, market=m, sym=s))            # aynı analysis_id ikinci kez → yazılmaz
        if i % 41 == 0:
            out.append(_snap(i, aid="bad/../id"))                    # geçersiz kimlik
        if i % 53 == 0:
            snap = _snap(10_000 + i, book=b, sym=s)
            snap["identity"].pop("market_type")                      # piyasası bilinmeyen (`?`) kayıt
            out.append(snap)
    return out


def _save_today(state: Path, snaps, keep: int) -> list[dict]:
    """Bugünkü yol: her kayıt indeksi diskten okur, tarar, yeniden yazar."""
    st = ChartAnalysisStore(state, keep_per_series=keep)
    res = []
    for s in snaps:
        CAS._INDEX_CACHE.clear()
        res.append(st.save(s))
    return res


def _save_batched(state: Path, snaps, keep: int) -> list[dict]:
    st = ChartAnalysisStore(state, keep_per_series=keep)
    st.begin_batch()
    try:
        return [st.save(s) for s in snaps]
    finally:
        st.end_batch()


def _tree(state: Path) -> dict[str, bytes]:
    root = state / "chart_analysis"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("keep", [3, 300])
def test_batched_index_is_byte_identical_to_per_save_writes(tmp_path, keep):
    snaps = _workload()
    a = _save_today(tmp_path / "a", snaps, keep)
    b = _save_batched(tmp_path / "b", snaps, keep)
    assert a == b, "kayıt başına dönüş (written/pruned) aynı olmalı"
    ta, tb = _tree(tmp_path / "a"), _tree(tmp_path / "b")
    assert ta.keys() == tb.keys() and ta == tb
    assert sum(r["written"] for r in a) > 100 and (keep > 10 or sum(r["pruned"] for r in a) > 50)


def test_batched_tours_continue_identically_from_an_existing_and_a_v1_index(tmp_path):
    """Üç tur arka arkaya; ilk tur eski (v1, piyasasız anahtarlı) bir indeksten başlar."""
    snaps = _workload(seed=9, n=240)
    for d in ("a", "b"):
        root = tmp_path / d / "chart_analysis"
        (root / "main" / "SOL_USDT_4h").mkdir(parents=True)
        legacy = _snap(99_999, aid="legacy01")
        (root / "main" / "SOL_USDT_4h" / "1_legacy01.json").write_text(json.dumps(legacy), encoding="utf-8")
        (root / INDEX_FILE).write_text(json.dumps({"schema_version": "chart_analysis_index_v1", "series": {"main|SOL/USDT|4h": [
            {"analysis_id": "legacy01", "as_of_ms": 1, "file": "main/SOL_USDT_4h/1_legacy01.json"}]}}), encoding="utf-8")
    for k in range(3):
        part = snaps[k * 80:(k + 1) * 80]
        _save_today(tmp_path / "a", part, 5)
        CAS._INDEX_CACHE.clear()
        _save_batched(tmp_path / "b", part, 5)
        assert _tree(tmp_path / "a") == _tree(tmp_path / "b"), k


def test_index_is_written_once_per_batch_and_not_at_all_without_new_analyses(tmp_path, monkeypatch):
    writes = []
    real = CAS.atomic_write_json

    def spy(path, obj, **kw):
        if Path(path).name == INDEX_FILE:
            writes.append(path)
        return real(path, obj, **kw)
    monkeypatch.setattr(CAS, "atomic_write_json", spy)
    snaps = [_snap(i, sym=s) for i, s in enumerate(["SOL/USDT", "ETH/USDT", "BTC/USDT"] * 20)]
    _save_batched(tmp_path, snaps, 300)
    assert len(writes) == 1
    _save_batched(tmp_path, snaps, 300)                  # aynı kimlikler: yeni analiz YOK → indeks yazılmaz
    assert len(writes) == 1
    monkeypatch.setattr(CAS, "BATCH_INDEX_WRITES", False)  # geri dönüş anahtarı: bugünkü yol
    _save_batched(tmp_path, [_snap(1000 + i) for i in range(5)], 300)
    assert len(writes) == 6


def test_reads_inside_a_batch_see_the_unwritten_rows(tmp_path):
    st = ChartAnalysisStore(tmp_path)
    st.begin_batch()
    st.save(_snap(1))
    assert st.has("a00000001") and st.list("main", "USDM_PERP", "SOL/USDT", "4h")[-1]["analysis_id"] == "a00000001"
    assert st.load("a00000001")["analysis_id"] == "a00000001"
    assert not (tmp_path / "chart_analysis" / INDEX_FILE).exists()
    assert st.end_batch() is True and st.end_batch() is False      # idempotent
    assert ChartAnalysisStore(tmp_path).has("a00000001")


def test_an_unparseable_series_key_keeps_todays_semantics(tmp_path):
    """'|' içeren kimlik bugün bir sonraki okumada düşer; toplu kip bu kaydı bugünkü yoldan yazar (içerik aynı)."""
    snaps = [_snap(1), _snap(2, sym="BAD|SYM"), _snap(3), _snap(4, book="x|y"), _snap(5)]
    _save_today(tmp_path / "a", snaps, 300)
    _save_batched(tmp_path / "b", snaps, 300)
    assert _tree(tmp_path / "a") == _tree(tmp_path / "b")


def test_engine_flushes_rows_saved_before_a_mid_loop_failure(tmp_path, monkeypatch):
    """Grafik analizi döngüsü yarıda patlarsa o ana kadar kaydedilenler (bugünkü gibi) indekste kalır."""
    import test_engine_v3 as TE

    import tradingbot.chart_analysis as CA

    def run(d, batch: bool):
        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(CAS, "BATCH_INDEX_WRITES", batch)
            eng = TE._engine(d, mp, {"chart_analysis": {"enabled": True}}, symbols=3)
            eng.tour(do_scan=False, obsidian=False, charts=False)
            real, n = CA.build_snapshot, {"k": 0}

            def flaky(**kw):
                n["k"] += 1
                if n["k"] == 3:
                    raise RuntimeError("sentetik arıza")
                snap = real(**kw)
                snap["analysis_id"] = "z%07d" % n["k"]                # yeni kimlik: yazım olsun
                return snap
            mp.setattr(CA, "build_snapshot", flaky)
            eng._chart_analysis_tour(list(eng.cfg.coins), {}, __import__("tradingbot.core", fromlist=["utc_now"]).utc_now())
            idx = json.loads((eng.cfg.state_path / "chart_analysis" / INDEX_FILE).read_text(encoding="utf-8"))
            return sorted(r["analysis_id"] for rows in idx["series"].values() for r in rows if r["analysis_id"].startswith("z"))
        finally:
            mp.undo()
    assert run(tmp_path / "a", False) == run(tmp_path / "b", True) == ["z0000001", "z0000002"]
