"""Tarihsel veri gölü: idempotent backfill, gap/duplicate/checksum manifest, resume, arşiv checksum fail-closed, plan dry-run (indirme yok)."""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from tradingbot.history import ArchiveClient, CollectSpec, HistoryCollector, HistoryStore
from tradingbot.history.collector import month_bounds_ms
from tradingbot.history.store import canonical_checksum, cols_for
from tradingbot.market.providers import klines_frame, tf_ms

H = 3_600_000
T0 = 1_704_067_200_000            # 2024-01-01T00:00Z


def _rows(start_ms, n, step=H, base=100.0):
    out = []
    for i in range(n):
        t = start_ms + i * step
        p = base + (i % 7) * 0.5
        out.append([t, p, p + 1, p - 1, p + 0.2, 10 + i % 3, t + step - 1, 1000.0, 5, 4.0, 400.0, "0"])
    return out


class FakeProvider:
    """Deterministik sentetik kline sunucusu: [listing, now) aralığı; sayfalama gerçek gibi (limit, startTime/endTime)."""
    max_kline_limit = 1000

    def __init__(self, listing_ms=T0, now_ms=T0 + 400 * H, fail_at: int | None = None):
        self.listing, self.now, self.fail_at = listing_ms, now_ms, fail_at
        self.calls = 0

    def klines(self, symbol, interval, limit=500, start_ms=None, end_ms=None):
        self.calls += 1
        step = tf_ms(interval)
        s = max(self.listing, start_ms or self.listing)
        s = s - (s % step) if s % step else s
        e = min(end_ms if end_ms is not None else self.now, self.now)
        if self.fail_at is not None and s >= self.fail_at:
            raise ConnectionError("simulated outage")
        n = max(0, min(limit, (e - s) // step + 1))
        return klines_frame(_rows(s, int(n), step), now_ms_=self.now)

    def funding_history(self, symbol, limit=100, start_ms=None, end_ms=None):
        return [{"symbol": symbol, "funding_ts": T0 + i * 8 * H, "rate": 0.0001, "mark": 100.0} for i in range(3)]

    def open_interest_hist(self, symbol, period="1h", limit=30):
        return [{"symbol": symbol, "oi": 1.0, "oi_value": 100.0, "ts": T0 + i * H} for i in range(5)]


def _zip_bytes(rows, header=False):
    buf = io.BytesIO()
    lines = (["open_time,open,high,low,close,volume,close_time,qv,n,tb,tq,i"] if header else []) + [",".join(str(x) for x in r) for r in rows]
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def test_store_write_idempotent_gap_duplicate_checksum(tmp_path: Path):
    st = HistoryStore(tmp_path)
    df = klines_frame(_rows(T0, 10), now_ms_=T0 + 100 * H)
    r1 = st.write("spot", "BTC/USDT", "1h", df, source="t")
    r2 = st.write("spot", "BTC/USDT", "1h", df, source="t")               # aynı mumlar → yeni satır 0
    assert r1["rows_new"] == 10 and r2["rows_new"] == 0 and r2["duplicates"] == 10
    m = st.manifest("spot", "BTC/USDT", "1h")
    assert m.row_count == 10 and m.duplicate_count == 10 and m.gap_count == 0 and m.checksum == canonical_checksum(st.read("spot", "BTC/USDT", "1h"), cols_for("1h"))
    # boşluk: 3 mum atla → gap_count 3, quality < 1
    st.write("spot", "BTC/USDT", "1h", klines_frame(_rows(T0 + 13 * H, 5), now_ms_=T0 + 100 * H))
    m = st.manifest("spot", "BTC/USDT", "1h")
    assert m.gap_count == 3 and m.row_count == 15 and 0 < m.quality_score < 1
    v = st.validate("spot", "BTC/USDT", "1h")
    assert v["ok"] and v["gaps"] == 3
    # ay sınırı partition: Ocak+Şubat → iki parça, okuma sıralı ve tekilsiz
    st.write("spot", "BTC/USDT", "1h", klines_frame(_rows(month_bounds_ms(2024, 2)[0] - 2 * H, 6), now_ms_=T0 + 2000 * H))
    m = st.manifest("spot", "BTC/USDT", "1h")
    assert set(m.parts) == {"2024/01", "2024/02"}
    rd = st.read("spot", "BTC/USDT", "1h")
    assert rd["timestamp"].is_monotonic_increasing and not rd["timestamp"].duplicated().any()
    assert list(rd.columns) == cols_for("1h") and "quote_volume" in rd.columns and "trades" in rd.columns


def test_collect_rest_paginated_resume_and_no_duplicate_on_rerun(tmp_path: Path):
    st = HistoryStore(tmp_path)
    prov = FakeProvider(now_ms=T0 + 2500 * H)                              # ~104 gün 1h → 3 sayfa
    col = HistoryCollector(st, spot=prov, futures=prov, archive=None, clock_ms=lambda: prov.now)
    spec = CollectSpec(markets=("spot",), symbols=("ETH/USDT",), timeframes=("1h",), from_ms=T0, include_funding=False, include_open_interest=False, archive_first=False)
    r = col.collect(spec)["series"][0]
    assert r["rows_new"] == 2500 and r["gaps"] == 0 and r["bad_chunks"] == 0
    calls1 = prov.calls
    r2 = col.collect(spec)["series"][0]                                    # ikinci koşu: cursor'dan devam, çoğaltma yok
    assert r2["rows_new"] == 0 and r2["rows_total"] == 2500 and prov.calls - calls1 <= 2
    m = st.manifest("spot", "ETH/USDT", "1h")
    assert m.cursor_ms == T0 + 2500 * H and m.duplicate_count == 0
    # zaman ilerler → yalnız yeni mumlar eklenir (kapanmamış son mum alınmaz)
    prov.now = T0 + 2600 * H + 1000
    r3 = col.collect(spec)["series"][0]
    assert r3["rows_new"] == 100 and st.manifest("spot", "ETH/USDT", "1h").row_count == 2600


def test_collect_network_error_marks_bad_chunk_and_resumes(tmp_path: Path):
    st = HistoryStore(tmp_path)
    prov = FakeProvider(now_ms=T0 + 2500 * H, fail_at=T0 + 1000 * H)
    col = HistoryCollector(st, spot=prov, archive=None, clock_ms=lambda: prov.now)
    spec = CollectSpec(markets=("spot",), symbols=("X/USDT",), timeframes=("1h",), from_ms=T0, include_funding=False, include_open_interest=False, archive_first=False)
    r = col.collect(spec)["series"][0]
    assert r["rows_new"] == 1000 and r["bad_chunks"] == 1                # kesinti: yazılan kısım korunur, hata fail-closed işaretli
    prov.fail_at = None
    col.collect(spec)
    assert st.manifest("spot", "X/USDT", "1h").row_count == 2500          # resume kaldığı yerden tamamlar


def test_archive_first_checksum_verified_and_mismatch_fail_closed(tmp_path: Path):
    st = HistoryStore(tmp_path)
    prov = FakeProvider(listing_ms=T0, now_ms=month_bounds_ms(2024, 3)[0] + 5 * H)
    jan = _rows(month_bounds_ms(2024, 1)[0], 31 * 24, header_only := False) if False else _rows(month_bounds_ms(2024, 1)[0], 31 * 24)
    feb = _rows(month_bounds_ms(2024, 2)[0], 29 * 24)
    zj, zf = _zip_bytes(jan, header=True), _zip_bytes(feb)
    urls = {ArchiveClient.kline_url("spot", "BTC/USDT", "1h", 2024, 1): zj, ArchiveClient.kline_url("spot", "BTC/USDT", "1h", 2024, 2): zf}
    urls[ArchiveClient.kline_url("spot", "BTC/USDT", "1h", 2024, 1) + ".CHECKSUM"] = (hashlib.sha256(zj).hexdigest() + "  f.zip").encode()
    urls[ArchiveClient.kline_url("spot", "BTC/USDT", "1h", 2024, 2) + ".CHECKSUM"] = b"deadbeef  f.zip"          # bozuk checksum
    arc = ArchiveClient(fetch_bytes=lambda u: urls.get(u))
    col = HistoryCollector(st, spot=prov, archive=arc, clock_ms=lambda: prov.now)
    spec = CollectSpec(markets=("spot",), symbols=("BTC/USDT",), timeframes=("1h",), from_ms=T0, include_funding=False, include_open_interest=False)
    r = col.collect(spec)["series"][0]
    m = st.manifest("spot", "BTC/USDT", "1h")
    assert arc.stats["zip_ok"] == 1 and arc.stats["checksum_fail"] == 1
    assert m.row_count == 31 * 24 and r["bad_chunks"] == 1 and m.bad_chunks[0]["reason"] == "checksum_mismatch"   # Şubat yazılmadı, REST ile üzerine yazılmadı
    assert m.quality_score < 1
    # arşiv sağlamsa REST kalan kısmı tamamlar
    urls[ArchiveClient.kline_url("spot", "BTC/USDT", "1h", 2024, 2) + ".CHECKSUM"] = (hashlib.sha256(zf).hexdigest() + "  f.zip").encode()
    st2 = HistoryStore(tmp_path / "b")
    col2 = HistoryCollector(st2, spot=prov, archive=ArchiveClient(fetch_bytes=lambda u: urls.get(u)), clock_ms=lambda: prov.now)
    r2 = col2.collect(spec)["series"][0]
    assert r2["bad_chunks"] == 0 and r2["rows_total"] == 31 * 24 + 29 * 24 + 5 and r2["gaps"] == 0


def test_plan_is_dry_run_and_futures_extras(tmp_path: Path):
    st = HistoryStore(tmp_path)
    prov = FakeProvider(now_ms=T0 + 100 * 24 * H)
    col = HistoryCollector(st, spot=prov, futures=prov, archive=None, clock_ms=lambda: prov.now)
    spec = CollectSpec(markets=("spot", "futures"), symbols=("BTC/USDT", "ETH/USDT"), timeframes=("1h", "4h"), max_available=True)
    plan = col.plan(spec, probe_listing=True)
    assert plan["series"] == 8 and plan["expected_rows"] > 0 and plan["disk_bytes_est"] > 0 and plan["requests_est"] > 0 and plan["eta_seconds_est"] >= 0
    assert prov.calls == 4 and st.series() == []                            # yalnız listing tespiti (sembol×market başına 1), veri indirilmedi
    plan_off = col.plan(spec, probe_listing=False)
    assert prov.calls == 4 and plan_off["items"][0]["expected_rows"] > 0
    # futures: funding + OI toplanır (idempotent)
    res = col.collect(CollectSpec(markets=("futures",), symbols=("BTC/USDT",), timeframes=("4h",), days=10, archive_first=False))
    kinds = {r["timeframe"] for r in res["series"]}
    assert {"4h", "funding", "oi_1h"} <= kinds
    assert st.manifest("futures", "BTC/USDT", "funding").row_count == 3 and st.manifest("futures", "BTC/USDT", "oi_1h").row_count == 5
    col.collect(CollectSpec(markets=("futures",), symbols=("BTC/USDT",), timeframes=("4h",), days=10, archive_first=False))
    assert st.manifest("futures", "BTC/USDT", "funding").row_count == 3


def test_tier_specs_from_config():
    from tradingbot.config_v3 import HistorySection
    from tradingbot.history import build_tier_specs
    tp = build_tier_specs([f"C{i}/USDT" for i in range(100)], ["SUI/USDT"], HistorySection())
    a, b, c = tp.specs
    assert len(a.symbols) == 100 and a.timeframes == ("1h", "4h", "1d") and a.max_available
    assert len(b.symbols) == 50 and b.timeframes == ("15m",)
    assert len(c.symbols) == 21 and "SUI/USDT" in c.symbols and c.per_tf_days == {"1m": 90, "5m": 365}
