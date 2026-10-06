# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1b — `ResearchStore` depo düzeyi (docs/SYSTEM_LEARNING_ENGINE_V1.md §3.2; P1b depo kabul
testleri 1, 2, 3, 7, 8, 9, 10'un depo katmanı; veri birimi katmanı `test_research_engine_datastore.py`'dedir).

* 1: aynı satırlarla yeniden yazım 0 satır ekler; parça dosyaları, parça sha'ları ve mühür özdeş kalır.
* 2: parça checksum'ı ↔ yan dosya ↔ manifest ↔ `data_seal` tutarlı; mühür hesabı parça OKUMAZ (okuma sayacı + `open`
  izleme + `read_parquet` yasağı).
* 3: yalnız kapanmış bar/nokta yazılır.
* 7: öncelik (archive > seed > archive_unverified > rest), fark kaydı, yetkili arşiv aralığı.
* 8: `MANIFEST_LAG` kendiliğinden düzelir; `CORRUPT` karantinaya alınır; seri yalnız iki ARDIŞIK başarısız geceden sonra
  durur.
* 9: fonlama aralığı veriden (8h/4h/1h); boşluklar o aralıkla sayılır.
* 10: 2025+ bütün spot serilerinde µs→ms, birim yoldan ve dönemden (değerin büyüklüğünden değil).
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_data_fixtures import KHEAD, kline_row, ms, utc, zip_bytes  # noqa: E402

from tradingbot.research_engine import datastore as DS  # noqa: E402
from tradingbot.research_engine import store as S  # noqa: E402

H = 3_600_000
AUG = ms(utc(2026, 8, 1))
SEP = ms(utc(2026, 9, 1))
OCT = ms(utc(2026, 10, 1))


def bars(start: int, n: int, step: int = H, *, px0: float = 100.0, bump: dict[int, float] | None = None) -> pd.DataFrame:
    bump = bump or {}
    rows = []
    for i in range(n):
        t = start + i * step
        o = px0 + i * 0.25 + bump.get(i, 0.0)
        rows.append({"timestamp": t, "open": o, "high": o + 1, "low": o - 1, "close": o + 0.5, "volume": 2.0,
                     "close_time": t + step - 1, "quote_volume": 200.0, "trades": 7, "taker_buy_base": 1.0,
                     "taker_buy_quote": 100.0})
    return pd.DataFrame(rows)


def mkstore(tmp_path: Path, now_ms: int) -> S.ResearchStore:
    return S.ResearchStore(tmp_path / "research" / "store", clock_ms=lambda: now_ms)


def part_bytes(st: S.ResearchStore, market: str, sym: str, tf: str) -> dict[str, bytes]:
    return {ym: p.read_bytes() for ym, p in st._disk_parts(market, sym, tf).items()}


# ============================================================================ kabul 1 (depo katmanı)
def test_rewriting_the_same_rows_adds_nothing_and_keeps_files_and_seal(tmp_path):
    st = mkstore(tmp_path, OCT)
    df = bars(AUG + 600 * H, 400)                              # Ağu → Eyl'e taşan 400 bar
    r1 = st.write("futures", "BTCUSDT", "1h", df, src=S.SRC_ARCHIVE)
    assert r1["rows_new"] == 400 and r1["parts"] == ["2026/08", "2026/09"]
    before = part_bytes(st, "futures", "BTCUSDT", "1h")
    mtimes = {ym: p.stat().st_mtime_ns for ym, p in st._disk_parts("futures", "BTCUSDT", "1h").items()}
    seal1, _ = st.data_seal()
    m1 = st.manifest("futures", "BTCUSDT", "1h")
    for _ in range(2):
        r = st.write("futures", "BTCUSDT", "1h", df, src=S.SRC_ARCHIVE)
        assert r["rows_new"] == 0 and r["parts"] == [] and r["changed"] == 0
    assert part_bytes(st, "futures", "BTCUSDT", "1h") == before
    assert {ym: p.stat().st_mtime_ns for ym, p in st._disk_parts("futures", "BTCUSDT", "1h").items()} == mtimes
    m2 = st.manifest("futures", "BTCUSDT", "1h")
    assert st.data_seal()[0] == seal1 and m2.part_sha == m1.part_sha and m2.series_seal == m1.series_seal
    assert m2.row_count == 400 and m2.gap_count == 0 and m2.quality_score == 1.0


# ============================================================================ kabul 2
def test_part_checksums_sidecars_manifest_and_seal_agree_and_seal_reads_no_part(tmp_path, monkeypatch):
    st = mkstore(tmp_path, OCT)
    st.write("futures", "BTCUSDT", "1h", bars(AUG, 900), src=S.SRC_ARCHIVE)
    st.write("futures", "ETHUSDT", "4h", bars(AUG, 300, 4 * H), src=S.SRC_ARCHIVE)
    st.write("spot", "BTCUSDT", "1d", bars(AUG, 40, 24 * H), src=S.SRC_REST)
    seal, listing = st.data_seal()
    assert len(listing) == 2 + 2 + 2
    for key, ym, sha in listing:
        market, sym, tf = key.split("/")
        y, mo = (int(x) for x in ym.split("/"))
        p = st.part_path(market, sym, tf, y, mo)
        assert hashlib.sha256(p.read_bytes()).hexdigest() == sha
        assert st.sidecar_path(p).read_text(encoding="ascii").split()[0] == sha
        assert st.manifest(market, sym, tf).part_sha[ym] == sha
    lines = "".join(f"{k}|{ym}|{sha}\n" for k, ym, sha in listing).encode()
    assert seal == hashlib.sha256(lines).hexdigest()
    # mühür hesabı parça OKUMAZ
    st.part_reads = st.byte_reads = 0
    opened: list[str] = []
    real_open = builtins.open

    def spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)
    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: (_ for _ in ()).throw(AssertionError("parça okundu")))
    fresh = S.ResearchStore(st.root)                           # sayaçları sıfır yeni örnek
    assert fresh.data_seal()[0] == seal
    assert fresh.part_reads == 0 and fresh.byte_reads == 0 and st.part_reads == 0
    assert opened and all(o.endswith("manifest.json") for o in opened), opened
    monkeypatch.undo()
    # bir parça değişince yalnız o satır ve mühür değişir
    st.write("spot", "BTCUSDT", "1d", bars(SEP + 9 * 24 * H, 1, 24 * H, px0=500.0), src=S.SRC_ARCHIVE)
    seal2, listing2 = st.data_seal()
    assert seal2 != seal
    assert [x for x in listing2 if x not in listing] == [x for x in listing2 if x[0] == "spot/BTCUSDT/1d" and x[1] == "2026/09"]


def test_a_write_reads_and_rewrites_only_the_months_it_touches(tmp_path):
    """§3.2 madde 1: HistoryStore._recompute her yazımda bütün seriyi okurdu (O(n²)); burada yalnız dokunulan ay."""
    st = mkstore(tmp_path, ms(utc(2027, 1, 1)))
    st.write("futures", "BTCUSDT", "1h", bars(ms(utc(2026, 1, 1)), 24 * 365), src=S.SRC_ARCHIVE)
    assert len(st._disk_parts("futures", "BTCUSDT", "1h")) == 12
    before = {ym: p.stat().st_mtime_ns for ym, p in st._disk_parts("futures", "BTCUSDT", "1h").items()}
    st.part_reads = st.byte_reads = 0
    r = st.write("futures", "BTCUSDT", "1h", bars(ms(utc(2026, 6, 10)), 3, bump={0: 1.0}), src=S.SRC_ARCHIVE)
    assert r["parts"] == ["2026/06"] and st.part_reads == 1 and st.byte_reads == 0
    after = {ym: p.stat().st_mtime_ns for ym, p in st._disk_parts("futures", "BTCUSDT", "1h").items()}
    assert [ym for ym in after if after[ym] != before[ym]] == ["2026/06"]
    m = st.manifest("futures", "BTCUSDT", "1h")
    assert m.row_count == 24 * 365 and m.first_ts_ms == ms(utc(2026, 1, 1)) and m.gap_count == 0


# ============================================================================ kabul 3
def test_only_closed_bars_and_points_are_written(tmp_path):
    now = AUG + 10 * H + 30 * 60_000                           # 10:30
    st = mkstore(tmp_path, now)
    r = st.write("futures", "BTCUSDT", "1h", bars(AUG, 12), src=S.SRC_REST)
    assert r["rows_new"] == 10 and r["dropped_open"] == 2      # 10:00 barı 11:00'de kapanır → yazılmaz
    assert st.manifest("futures", "BTCUSDT", "1h").last_ts_ms == AUG + 9 * H
    df = bars(AUG, 3)
    df["is_closed"] = [True, True, False]
    st.write("futures", "ETHUSDT", "1h", df, src=S.SRC_REST)
    assert st.manifest("futures", "ETHUSDT", "1h").row_count == 2
    f = pd.DataFrame({"timestamp": [AUG, AUG + 8 * H, AUG + 16 * H], "rate": [1e-4, 2e-4, 3e-4]})
    st.write("futures", "BTCUSDT", S.FUNDING, f, src=S.SRC_REST)
    assert st.manifest("futures", "BTCUSDT", S.FUNDING).row_count == 2   # 16:00 uzlaşması henüz olmadı
    with pytest.raises(ValueError, match="µs"):
        st.write("spot", "BTCUSDT", "1h", bars(AUG * 1000, 1), src=S.SRC_ARCHIVE)


# ============================================================================ kabul 7 (depo katmanı)
def test_priority_rest_never_overwrites_archive_archive_replaces_rest_and_diffs_are_recorded(tmp_path):
    st = mkstore(tmp_path, OCT)
    st.write("futures", "BTCUSDT", "1h", bars(AUG, 30), src=S.SRC_REST)
    st.drain_diffs()
    r = st.write("futures", "BTCUSDT", "1h", bars(AUG, 24, bump={5: 0.75}), src=S.SRC_ARCHIVE, authoritative=(AUG, AUG + 24 * H))
    assert r["upgraded"] == 24 and r["changed"] == 1 and r["rows_new"] == 0
    diffs, n = st.drain_diffs()
    assert n == 1 and diffs[0]["action"] == "replaced" and diffs[0]["old_src"] == "rest" and diffs[0]["new_src"] == "archive"
    assert diffs[0]["ts"] == AUG + 5 * H and set(diffs[0]["cols"]) == {"open", "high", "low", "close"}
    df = st.read_with_src("futures", "BTCUSDT", "1h")
    assert df[S.SRC_COL].tolist() == ["archive"] * 24 + ["rest"] * 6
    # REST arşivi ASLA ezmez (farklı değerle gelse bile): engellenir ve fark kaydı düşer
    r = st.write("futures", "BTCUSDT", "1h", bars(AUG, 30, px0=50.0), src=S.SRC_REST)
    assert r["blocked"] == 24 and r["blocked_diff"] == 24 and r["changed"] == 6
    after = st.read_with_src("futures", "BTCUSDT", "1h")
    assert after["open"].iloc[:24].tolist() == df["open"].iloc[:24].tolist()
    diffs, n = st.drain_diffs()
    assert n == 30 and {d["action"] for d in diffs} == {"blocked", "replaced"}
    # zincir: seed > archive_unverified > rest; archive her şeyin önünde
    st.write("spot", "PAXGUSDT", "1h", bars(AUG, 4), src=S.SRC_SEED)
    st.write("spot", "PAXGUSDT", "1h", bars(AUG, 4, px0=7.0), src=S.SRC_UNVERIFIED)
    assert st.read_with_src("spot", "PAXGUSDT", "1h")[S.SRC_COL].tolist() == ["seed"] * 4
    st.write("spot", "PAXGUSDT", "1h", bars(AUG, 6, px0=7.0), src=S.SRC_UNVERIFIED)
    st.write("spot", "PAXGUSDT", "1h", bars(AUG, 7, px0=9.0), src=S.SRC_REST)
    assert st.read_with_src("spot", "PAXGUSDT", "1h")[S.SRC_COL].tolist() == ["seed"] * 4 + ["archive_unverified"] * 2 + ["rest"]
    st.write("spot", "PAXGUSDT", "1h", bars(AUG, 7, px0=100.0), src=S.SRC_ARCHIVE)
    assert st.read_with_src("spot", "PAXGUSDT", "1h")[S.SRC_COL].tolist() == ["archive"] * 7
    # yetkili aralık: arşivin kapsadığı dönemde arşivde OLMAYAN rest satırı kaldırılır ve fark olarak kaydedilir
    st.drain_diffs()
    odd = pd.concat([bars(SEP, 5), bars(SEP + 2 * H + 1800_000, 1)])  # :30'da bir REST satırı (arşivde yok)
    st.write("futures", "SOLUSDT", "1h", odd, src=S.SRC_REST)
    st.write("futures", "SOLUSDT", "1h", bars(SEP, 5), src=S.SRC_ARCHIVE, authoritative=(SEP, SEP + 5 * H))
    got = st.read_with_src("futures", "SOLUSDT", "1h")
    assert len(got) == 5 and set(got[S.SRC_COL]) == {"archive"}
    diffs, n = st.drain_diffs()
    assert n == 1 and diffs[0]["action"] == "removed" and diffs[0]["ts"] == SEP + 2 * H + 1800_000


# ============================================================================ kabul 8 (depo katmanı)
class Killed(BaseException):
    """`TimeoutStartSec` öldürmesinin benzetimi (parça yazıldı, manifest yazılamadı)."""


def test_manifest_lag_heals_corrupt_is_quarantined_and_halt_needs_two_consecutive_failed_nights(tmp_path, monkeypatch):
    st = mkstore(tmp_path, OCT)
    st.write("futures", "BTCUSDT", "1h", bars(AUG, 744), src=S.SRC_ARCHIVE)
    st.write("futures", "BTCUSDT", "1h", bars(SEP, 100), src=S.SRC_ARCHIVE)
    real = S.ResearchStore._save_manifest

    def boom(self, m):
        raise Killed()
    monkeypatch.setattr(S.ResearchStore, "_save_manifest", boom)
    with pytest.raises(Killed):
        st.write("futures", "BTCUSDT", "1h", bars(SEP, 300), src=S.SRC_ARCHIVE)
    monkeypatch.setattr(S.ResearchStore, "_save_manifest", real)
    chk = st.check_series("futures", "BTCUSDT", "1h")
    assert chk["states"]["2026/09"] == S.ST_MANIFEST_LAG and chk["inflight"] == ["2026/09"] and not chk["ok"]
    assert not st.validate("futures", "BTCUSDT", "1h")["ok"]
    r = st.recover_series("futures", "BTCUSDT", "1h", night="2026-10-02")
    assert r["manifest_lag"] == ["2026/09"] and r["refetch"] == ["2026/09"] and not r["corrupt"]
    m = st.manifest("futures", "BTCUSDT", "1h")
    assert m.row_count == 744 + 300 and m.recovery["2026/09"]["state"] == S.ST_MANIFEST_LAG and m.status == S.SERIES_OK
    assert st.check_series("futures", "BTCUSDT", "1h")["ok"] and st.validate("futures", "BTCUSDT", "1h")["ok"]
    st.refetch_result("futures", "BTCUSDT", "1h", "2026/09", ok=True, night="2026-10-02")
    assert st.manifest("futures", "BTCUSDT", "1h").refetch == []
    # bozuk parça: okunamıyor → karantina, ay manifestten ve dosya defterinden çıkar, yeniden çekime girer
    st.note_files("futures", "BTCUSDT", "1h", {"2026-08": {"st": "v"}, "2026-09-01": {"st": "v"}})
    p = st.part_path("futures", "BTCUSDT", "1h", 2026, 8)
    p.write_bytes(b"bozuk" * 10)
    r = st.recover_series("futures", "BTCUSDT", "1h", night="2026-10-03")
    assert r["corrupt"] == ["2026/08"] and r["refetch"] == ["2026/08"] and len(r["quarantined"]) == 1
    q = Path(r["quarantined"][0])
    assert q.exists() and q.read_bytes() == b"bozuk" * 10 and str(st.quarantine_root()) in str(q) and not p.exists()
    m = st.manifest("futures", "BTCUSDT", "1h")
    assert "2026/08" not in m.part_sha and "2026-08" not in m.files and "2026-09-01" in m.files and m.row_count == 300
    # gece 1 başarısız → durmaz; aynı gece tekrar → yine durmaz; ARDIŞIK gece 2 → durur
    assert st.refetch_result("futures", "BTCUSDT", "1h", "2026/08", ok=False, night="2026-10-03")["status"] == "OK"
    assert st.refetch_result("futures", "BTCUSDT", "1h", "2026/08", ok=False, night="2026-10-03")["status"] == "OK"
    h = st.refetch_result("futures", "BTCUSDT", "1h", "2026/08", ok=False, night="2026-10-04", reason="404")
    assert h["status"] == S.SERIES_HALTED and "iki ardışık gece" in h["halted_reason"]
    assert st.validate("futures", "BTCUSDT", "1h")["status"] == S.SERIES_HALTED
    # başarılı yeniden çekim durmayı kaldırır
    st.write("futures", "BTCUSDT", "1h", bars(AUG, 744), src=S.SRC_ARCHIVE)
    assert st.refetch_result("futures", "BTCUSDT", "1h", "2026/08", ok=True, night="2026-10-05")["status"] == "OK"
    # ardışık OLMAYAN başarısız geceler durdurmaz
    st.write("futures", "ETHUSDT", "1h", bars(AUG, 10), src=S.SRC_ARCHIVE)
    st.part_path("futures", "ETHUSDT", "1h", 2026, 8).write_bytes(b"x")
    st.recover_series("futures", "ETHUSDT", "1h", night="2026-10-01")
    for night in ("2026-10-01", "2026-10-03", "2026-10-05"):
        assert st.refetch_result("futures", "ETHUSDT", "1h", "2026/08", ok=False, night=night)["status"] == "OK"
    # yan dosyası tutmayan (okunabilen) parça da CORRUPT'tur; yan dosya tutup manifest geride → MANIFEST_LAG
    st.write("futures", "SOLUSDT", "1h", bars(AUG, 10), src=S.SRC_ARCHIVE)
    sp = st.sidecar_path(st.part_path("futures", "SOLUSDT", "1h", 2026, 8))
    sp.write_text("0" * 64 + "  08.parquet\n", encoding="ascii")
    assert st.check_series("futures", "SOLUSDT", "1h", deep=True)["states"]["2026/08"] == S.ST_CORRUPT


def test_deep_validate_streams_the_canonical_checksum_and_catches_what_stat_cannot(tmp_path):
    from tradingbot.history.store import canonical_checksum
    st = mkstore(tmp_path, OCT)
    st.write("futures", "BTCUSDT", "1h", bars(AUG, 1000), src=S.SRC_ARCHIVE)
    d = st.deep_validate("futures", "BTCUSDT", "1h")
    full = st.read("futures", "BTCUSDT", "1h")
    assert d["ok"] and d["rows"] == 1000 and d["canonical"] == canonical_checksum(full, S.cols_for("1h"))
    m = st.manifest("futures", "BTCUSDT", "1h")
    assert m.checksum == d["canonical"] and m.checksum_at
    # yan dosya bozuldu (parçanın stat'ı aynı): ucuz denetim görmez, derin denetim görür
    p = st.part_path("futures", "BTCUSDT", "1h", 2026, 8)
    st.sidecar_path(p).write_text("f" * 64 + "  08.parquet\n", encoding="ascii")
    assert st.check_series("futures", "BTCUSDT", "1h")["ok"]
    d = st.deep_validate("futures", "BTCUSDT", "1h")
    assert not d["ok"] and d["states"]["2026/08"] == S.ST_CORRUPT
    r = st.recover_series("futures", "BTCUSDT", "1h", night="2026-10-04", deep=True)
    assert r["corrupt"] == ["2026/08"] and r["refetch"] == ["2026/08"]


# ============================================================================ kabul 9
def test_funding_interval_is_derived_from_data_and_gaps_use_it(tmp_path):
    st = mkstore(tmp_path, OCT)
    for sym, hours in (("BTCUSDT", 8), ("SOLUSDT", 4), ("ARBUSDT", 1)):
        iv = hours * H
        ts = [AUG + i * iv + (i % 27) for i in range(int(40 * 24 / hours))]   # Binance calc_time +0…+26 ms oynar
        del ts[11]                                                          # bir uzlaşma eksik
        st.write("futures", sym, S.FUNDING, pd.DataFrame({"timestamp": ts, "rate": [1e-4] * len(ts)}), src=S.SRC_ARCHIVE)
        m = st.manifest("futures", sym, S.FUNDING)
        assert m.interval_ms == iv, (sym, m.interval_ms)
        assert {pm["interval_ms"] for pm in m.part_meta.values()} == {iv}
        assert m.gap_count == 1, (sym, m.gap_count)                          # ay sınırı boşluk sayılmaz
        assert m.quality_score == round(len(ts) / (len(ts) + 1), 4)
    # sabit 8 saatlik varsayım (HistoryStore.step_ms_for) 4h/1h serilerde eksik uzlaşmayı göremezdi; worker'ınki aynen kalır
    from tradingbot.history.store import step_ms_for
    assert step_ms_for("funding") == 8 * H
    assert S.step_ms(S.FUNDING) is None


def test_funding_gaps_follow_a_mid_month_interval_change(tmp_path):
    """P1b kabul 9 (güçlendirilmiş; düzensiz aralık): aralık ay İÇİNDE değişirse (13'üne kadar 8h, sonra 4h) ayın tek
    medyanı 36 sahte boşluk sayıyordu (kalite 0,8). Boşluk artık yerel aralıkla (±3 komşu farkın medyanı) ve bütün
    seri üzerinde (ay sınırı dahil, parça okumadan) sayılır: değişim boşluk değildir, her rejimde bir eksik uzlaşma 1'dir."""
    def fts(*segs: tuple[int, int, int]) -> list[int]:
        out: list[int] = []
        for a, b, hours in segs:
            out += [t + (len(out) % 27) for t in range(a, b, hours * H)]   # calc_time +0…+26 ms
        return out

    def wr(st, sym, ts):
        st.write("futures", sym, S.FUNDING, pd.DataFrame({"timestamp": ts, "rate": [1e-4] * len(ts)}), src=S.SRC_ARCHIVE)
        return st.manifest("futures", sym, S.FUNDING)
    st = mkstore(tmp_path, OCT)
    mid = ms(utc(2026, 9, 13))
    ts = fts((SEP, mid, 8), (mid, OCT, 4))
    m = wr(st, "ALTUSDT", ts)
    assert m.gap_count == 0 and m.quality_score == 1.0, (m.gap_count, m.quality_score)
    assert m.interval_ms == 4 * H, "manifestin aralığı serinin EN SON yerel aralığı"
    assert m.part_meta["2026/09"]["interval_ms"] in (4 * H, 8 * H), "parçanın kendi medyanı (§3.2) kayıtlı kalır"
    # her rejimde bir eksik uzlaşma: tam 2 boşluk
    ts2 = list(ts)
    i8 = next(i for i, t in enumerate(ts2) if t >= SEP + 3 * 24 * H)
    i4 = next(i for i, t in enumerate(ts2) if t >= mid + 5 * 24 * H)
    del ts2[i4]
    del ts2[i8]
    m = wr(mkstore(tmp_path / "b", OCT), "ALTUSDT", ts2)
    assert m.gap_count == 2 and m.quality_score == round(len(ts2) / (len(ts2) + 2), 4)
    # değişim ay sınırında ve ayın İLK uzlaşmalarında (önceki ayın kuyruğu parça okumadan dikkate alınır); yazım sırası
    # (önce Eylül sonra Ağustos) sonucu değiştirmez
    for order in ((AUG, SEP), (SEP, AUG)):
        st3 = mkstore(tmp_path / f"c{order[0]}", OCT)
        sep8 = SEP + 8 * H                                         # 4h rejimi 1 Eylül 08:00'de başlar
        ts3 = fts((AUG, sep8, 8), (sep8, OCT, 4))
        for a in order:
            b = SEP if a == AUG else OCT
            m = wr(st3, "BBBUSDT", [t for t in ts3 if a <= t < b])
        assert m.gap_count == 0 and m.row_count == len(ts3), (order, m.gap_count)
    # 1h → 8h (ters yön) ve uzun bir boşluk (3 gün, 8h rejiminde 9 eksik uzlaşma)
    st4 = mkstore(tmp_path / "d", OCT)
    ts4 = fts((AUG, AUG + 10 * 24 * H, 1), (AUG + 10 * 24 * H, OCT, 8))
    hole = [t for t in ts4 if not (ms(utc(2026, 9, 10)) < t < ms(utc(2026, 9, 13)))]
    m = wr(st4, "CCCUSDT", hole)
    assert m.gap_count == len(ts4) - len(hole) and m.interval_ms == 8 * H, (m.gap_count, len(ts4) - len(hole))


def test_funding_gap_in_the_first_settlements_of_a_new_listing_is_counted(tmp_path):
    """Yeniden doğrulama küçüğü 7 (2026-10-06): yalnız iki fark varken (yeni listelenmiş sembolün ilk üç uzlaşması) iki
    farkın ortalaması (8h + 16h → 12h) baştaki eksik uzlaşmayı 0 sayıyordu; yerel aralık artık küçük olandır. Daha uzun
    serilerde ve aralık değişiminde sonuç aynıdır (yukarıdaki test)."""
    assert S.funding_gaps([8 * H, 16 * H]) == (1, 8 * H)
    assert S.funding_gaps([16 * H, 8 * H]) == (1, 8 * H)
    assert S.funding_gaps([8 * H, 8 * H]) == (0, 8 * H) and S.funding_gaps([8 * H]) == (0, 8 * H)
    assert S.funding_gaps([8 * H, 16 * H, 8 * H]) == (1, 8 * H), "üç farkta tek medyan (değişmedi)"
    st = mkstore(tmp_path, OCT)
    t0 = ms(utc(2026, 9, 20, 8))
    ts = [t0, t0 + 8 * H, t0 + 24 * H]                              # 16:00 uzlaşması eksik
    st.write("futures", "NEWUSDT", S.FUNDING, pd.DataFrame({"timestamp": ts, "rate": [1e-4] * 3}), src=S.SRC_ARCHIVE)
    m = st.manifest("futures", "NEWUSDT", S.FUNDING)
    assert m.gap_count == 1 and m.quality_score == 0.75 and m.interval_ms == 8 * H, (m.gap_count, m.quality_score)


# ============================================================================ kabul 10
def spot_zip(sym: str, tf: str, start: int, n: int, step: int, *, us: bool) -> bytes:
    rows = [kline_row(sym, start + i * step, step, us=us) for i in range(n)]
    return zip_bytes(f"{sym}-{tf}.csv", "\n".join(",".join(r) for r in rows) + "\n")


def test_microseconds_to_ms_for_every_2025_plus_spot_series_by_path_and_date():
    d = 86_400_000
    dec24, jan25, aug26 = ms(utc(2024, 12, 1)), ms(utc(2025, 1, 1)), ms(utc(2026, 8, 1))
    for sym in ("BTCUSDT", "ETHUSDT", "PAXGUSDT"):
        old = DS.parse_kline_zip(spot_zip(sym, "1d", dec24, 31, d, us=False), "spot", dec24, jan25)
        assert old["timestamp"].iloc[0] == dec24 and old["close_time"].iloc[0] == dec24 + d - 1
        for a in (jan25, aug26):
            b = ms(utc(2025, 2, 1)) if a == jan25 else ms(utc(2026, 9, 1))
            new = DS.parse_kline_zip(spot_zip(sym, "1h", a, 24, H, us=True), "spot", a, b)
            assert new["timestamp"].tolist() == [a + i * H for i in range(24)], sym
            assert new["close_time"].iloc[0] == a + H - 1 and new["timestamp"].max() < 10 ** 13
    # birim DEĞERDEN değil yoldan/dönemden: 2025+ spot dosyası ms yazsaydı sessizce bölünmez, bozuk sayılır
    with pytest.raises(S.ArchiveUnitError):
        DS.parse_kline_zip(spot_zip("BTCUSDT", "1h", jan25, 24, H, us=False), "spot", jan25, ms(utc(2025, 2, 1)))
    # vadeli arşiv 2025+ da ms'dir; µs yazılmış bir vadeli dosyası bozuktur
    fz = zip_bytes("f.csv", KHEAD + "\n" + "\n".join(",".join(kline_row("BTCUSDT", jan25 + i * H, H)) for i in range(5)) + "\n")
    assert DS.parse_kline_zip(fz, "futures", jan25, ms(utc(2025, 2, 1)))["timestamp"].iloc[0] == jan25
    fu = zip_bytes("f.csv", KHEAD + "\n" + "\n".join(",".join(kline_row("BTCUSDT", jan25 + i * H, H, us=True)) for i in range(5)) + "\n")
    with pytest.raises(S.ArchiveUnitError):
        DS.parse_kline_zip(fu, "futures", jan25, ms(utc(2025, 2, 1)))
    assert S.archive_ts_divisor("spot", jan25) == 1000 and S.archive_ts_divisor("spot", jan25 - 1) == 1
    assert S.archive_ts_divisor("futures", aug26) == 1


def test_store_layout_sidecar_and_manifest_schema(tmp_path):
    st = mkstore(tmp_path, OCT)
    st.write("futures", "BTCUSDT", "metrics_5m", pd.DataFrame({"timestamp": [AUG, AUG + 300_000], "oi": [1.0, 2.0]}),
             src=S.SRC_ARCHIVE)
    st.write("dukascopy", "XAUUSD", S.DUKA_1H, pd.DataFrame({"timestamp": [AUG], "open": [1900.0], "high": [1901.0],
                                                             "low": [1899.0], "close": [1900.5], "volume_proxy": [0.0]}),
             src=S.SRC_UNVERIFIED)
    p = st.part_path("futures", "BTCUSDT", "metrics_5m", 2026, 8)
    assert p.relative_to(st.root).as_posix() == "futures/BTCUSDT/metrics_5m/2026/08.parquet"
    assert list(pd.read_parquet(p).columns) == S.METRICS_COLS + [S.SRC_COL]
    d = json.loads(st.manifest_path("futures", "BTCUSDT", "metrics_5m").read_text(encoding="utf-8"))
    assert d["store_schema"] == S.STORE_SCHEMA and d["part_sha"] and d["part_meta"]["2026/08"]["src"] == {"archive": 2}
    assert sorted(st.series()) == [("dukascopy", "XAUUSD", "duka_1h"), ("futures", "BTCUSDT", "metrics_5m")]
    assert not (st.series_dir("futures", "BTCUSDT", "metrics_5m") / S.INFLIGHT_FILE).exists()
    assert os.path.exists(st.sidecar_path(p))
