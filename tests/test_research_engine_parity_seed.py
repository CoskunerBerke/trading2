# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1b — tohum ve `StoreProvider` paritesi (docs/SYSTEM_LEARNING_ENGINE_V1.md §3.2 "İlk tohum",
§3.5; P1b depo kabul testleri 11 ve 14). Ağ YOK.

* 11: eşzamanlı yazıcı benzetimiyle tohumlama ya tutarlı kopya üretir (gerekirse ikinci denemede) ya da seriyi
  arşivden yeniden indirir; asla tümden başarısız olmaz; worker deposuna HİÇ yazılmaz (bayt-özdeş kalır, yalnız
  okuma kipinde açılır); tohumla tam kapsanan aylar yeniden indirilmez.
* 14: `StoreProvider` fixture paritesi: 3 sembol × 3 tf × 6 ay, `signal_lab.ArchiveProvider` + `book_lab.load_klines`
  ile float64 ve zaman damgası BAYT eşitliği ve `book_lab.series_digest` eşitliği (REST kaynaklı kuyruk barları dahil).
"""
from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_data_fixtures import TF, DataEnv, FakeBinance, FakeClock, kline_row, ms, utc  # noqa: E402

from tradingbot import book_lab as BL  # noqa: E402
from tradingbot import signal_lab as SL  # noqa: E402
from tradingbot.history.store import KLINE_COLS, HistoryStore  # noqa: E402
from tradingbot.research_engine import datastore as DS  # noqa: E402
from tradingbot.research_engine import seed as SEED  # noqa: E402
from tradingbot.research_engine import store as S  # noqa: E402
from tradingbot.research_engine.provider import BASE_COLS, WIDE_COLS, StoreProvider  # noqa: E402

START = utc(2026, 10, 6, 0, 41)


def worker_bars(sym: str, tf: str, a: int, b: int) -> pd.DataFrame:
    """Worker'ın kendi (REST'le topladığı) satırları: sahte arşivle AYNI fiyat fonksiyonu."""
    step = TF[tf]
    rows = [kline_row(sym + "klines", t, step) for t in range(a, b, step)]
    df = pd.DataFrame([r[:11] for r in rows], columns=KLINE_COLS)
    for c in KLINE_COLS:
        df[c] = df[c].astype("int64") if c in ("timestamp", "close_time", "trades") else df[c].astype(float)
    return df


# ============================================================================ kabul 11
def test_seed_makes_a_consistent_copy_or_redownloads_and_never_writes_the_worker_store(tmp_path, monkeypatch):
    listing = {f"futures/{s}": ms(utc(2026, 8, 20)) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")}
    only = {"futures/BTCUSDT/4h", "futures/ETHUSDT/4h", "futures/SOLUSDT/4h", "futures/BTCUSDT/1h", "futures/BTCUSDT/funding"}
    e = DataEnv(tmp_path, start=START, listing=listing, only=only)
    wroot = e.paths.worker_history
    w = HistoryStore(wroot)
    a, b = ms(utc(2026, 8, 20)), ms(utc(2026, 10, 6))
    for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        w.write("futures", sym, "4h", worker_bars(sym.replace("/", ""), "4h", a, b), source="rest")
    w.write("futures", "BTC/USDT", "1h", worker_bars("BTCUSDT", "1h", a, b), source="rest")
    iv = 8 * 3_600_000
    fts = list(range(ms(utc(2026, 9, 1)), ms(utc(2026, 10, 1)), iv))   # worker REST fundingTime: tam ızgara, mark dolu
    w.write("futures", "BTC/USDT", "funding", pd.DataFrame({"timestamp": fts, "rate": [float(f"{0.0001 * ((t // iv) % 5):.8f}") for t in fts],
                                                            "mark": [100.0] * len(fts)}))
    # BTC 1h: worker parçası manifest güncellenmeden değişmiş (yarım yazım / bozulma) → kopya hiç tutmaz
    p1h = wroot / "futures" / "BTC_USDT" / "1h" / "2026" / "09.parquet"
    df = pd.read_parquet(p1h)
    df.loc[3, "close"] = df.loc[3, "close"] + 1
    df.to_parquet(p1h, index=False)
    in_hook = {"on": False}

    def concurrent_writer(series_dir: Path, attempt: int) -> None:
        """Parçalar okunduktan sonra, manifest yeniden okunmadan önce worker'ın IndexRefresher'ı yazar."""
        in_hook["on"] = True
        try:
            name = series_dir.parent.name
            nxt = ms(utc(2026, 10, 6)) + attempt * 4 * 3_600_000
            if name == "ETH_USDT" and attempt == 1:          # yalnız ilk denemede → ikinci deneme tutarlı
                w.write("futures", "ETH/USDT", "4h", worker_bars("ETHUSDT", "4h", nxt - 4 * 3_600_000, nxt), source="rest")
            if name == "SOL_USDT":                           # her denemede → yeniden indirme
                w.write("futures", "SOL/USDT", "4h", worker_bars("SOLUSDT", "4h", nxt - 4 * 3_600_000, nxt), source="rest")
        finally:
            in_hook["on"] = False
    snap_before = {p: p.read_bytes() for p in wroot.rglob("*") if p.is_file()}
    real_open, real_replace = builtins.open, os.replace
    bad: list[str] = []

    def spy_open(file, mode="r", *a, **k):
        if not in_hook["on"] and str(file).startswith(str(wroot)) and any(c in str(mode) for c in "wax+"):
            bad.append(f"open {file} {mode}")
        return real_open(file, mode, *a, **k)

    def spy_replace(src, dst, *a, **k):
        if not in_hook["on"] and str(dst).startswith(str(wroot)):
            bad.append(f"replace {dst}")
        return real_replace(src, dst, *a, **k)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "replace", spy_replace)
    st = e.run("backfill", seed_hook=concurrent_writer)
    monkeypatch.undo()
    assert not bad, bad
    assert st["result"] == DS.R_SUCCESS and st["exit_code"] == 0, st.get("error")
    seeds = st["seed"]["series"]
    assert seeds["futures/BTCUSDT/4h"]["status"] == SEED.ST_SEEDED and seeds["futures/BTCUSDT/4h"]["attempt"] == 1
    assert seeds["futures/ETHUSDT/4h"]["status"] == SEED.ST_SEEDED and seeds["futures/ETHUSDT/4h"]["attempt"] == 2
    assert "değişti" in seeds["futures/ETHUSDT/4h"]["reasons"][0]
    assert seeds["futures/SOLUSDT/4h"]["status"] == SEED.ST_REDOWNLOAD and len(seeds["futures/SOLUSDT/4h"]["reasons"]) == 2
    assert seeds["futures/BTCUSDT/1h"]["status"] == SEED.ST_REDOWNLOAD and "checksum" in seeds["futures/BTCUSDT/1h"]["reasons"][0]
    assert seeds["futures/BTCUSDT/funding"]["status"] == SEED.ST_SEEDED
    assert st["seed"]["seeded"] == 3 and st["seed"]["redownload"] == 2 and "SEED_REDOWNLOAD" in st["flags"]
    # worker deposu: yalnız benzetilen yazıcının değiştirdiği dosyalar değişti; tohumlayıcı hiçbir şey eklemedi
    after = {p: p.read_bytes() for p in wroot.rglob("*") if p.is_file()}
    changed = {p for p in after if snap_before.get(p) != after[p]}
    assert all(("ETH_USDT" in str(p) or "SOL_USDT" in str(p)) for p in changed), changed
    assert not any(p.name.endswith(".sha256") or "_src" in pd.read_parquet(p).columns for p in after if p.suffix == ".parquet")
    store = e.store()
    btc = store.read_with_src("futures", "BTCUSDT", "4h")
    sep = btc[(btc["timestamp"] >= ms(utc(2026, 9, 1))) & (btc["timestamp"] < ms(utc(2026, 10, 1)))]
    assert len(sep) == 180 and set(sep[S.SRC_COL]) == {"seed"}, "tohumla tam kapsanan ay"
    assert not e.fb.urls("BTCUSDT-4h-2026-09.zip"), "tohumla tam kapsanan ay yeniden İNDİRİLMEZ"
    aug = btc[btc["timestamp"] < ms(utc(2026, 9, 1))]
    assert set(aug[S.SRC_COL]) == {"archive"}, "eksik ay arşivden; arşiv tohumun önündedir"
    assert st["diffs"]["count"] == 0, "aynı değerler: tohum ↔ arşiv farkı 0"
    fund = store.read_with_src("futures", "BTCUSDT", "funding", ms(utc(2026, 9, 1)), ms(utc(2026, 9, 30, 23)))
    assert len(fund) == 90 and set(fund[S.SRC_COL]) == {"archive"}, "±ms oynamalı arşiv ikizi tohum satırının yerine geçer"
    for k in ("futures/SOLUSDT/4h", "futures/BTCUSDT/1h"):
        m = store.manifest(*k.split("/"))
        assert m.row_count > 0 and set(m.src_rows) <= {"archive", "rest"}, (k, m.src_rows)
    eth = store.read("futures", "ETHUSDT", "4h", ms(utc(2026, 9, 1)), ms(utc(2026, 9, 30, 20)))
    exp = HistoryStore(wroot).read("futures", "ETH/USDT", "4h", ms(utc(2026, 9, 1)), ms(utc(2026, 9, 30, 20)))
    assert eth[KLINE_COLS].reset_index(drop=True).equals(exp[KLINE_COLS].reset_index(drop=True))


def test_worker_symbol_dir_candidates():
    assert SEED.worker_symbol_dirs("BTCUSDT") == ["BTC_USDT", "BTC_USDT-USDT", "BTCUSDT"]
    assert SEED.worker_symbol_dirs("1000PEPEUSDT")[0] == "1000PEPE_USDT"


# ============================================================================ kabul 14
def test_store_provider_fixture_parity_with_archive_provider(tmp_path):
    syms, tfs = ("BTCUSDT", "ETHUSDT", "SOLUSDT"), ("15m", "1h", "4h")
    listing = {f"futures/{s}": ms(utc(2026, 4, 1)) for s in syms}
    e = DataEnv(tmp_path, start=START, listing=listing, only={f"futures/{s}/{tf}" for s in syms for tf in tfs})
    st = e.run("backfill")
    assert st["result"] == DS.R_SUCCESS, st.get("error")
    store = e.store()
    prov = StoreProvider(store, clock_ms=lambda: ms(e.clock.now()))
    later = utc(2026, 10, 8, 0, 0)                     # o gün REST'le gelen kuyruğun gün zip'i artık yayımlı
    fb2 = FakeBinance(FakeClock(later), listing=listing)

    def get(url: str):
        r = fb2(url)
        return r.body if r.status == 200 else None
    start = ms(utc(2026, 4, 1))
    rest_seen = 0
    for s in syms:
        for tf in tfs:
            m = store.manifest("futures", s, tf)
            end = int(m.last_ts_ms) + TF[tf]
            ours = BL.clip(SL.download(prov, s, tf, start, end - 1), start, end)
            theirs = BL.load_klines(s, tf, start, end, get, ms(later))
            months = (end - start) / (30 * 86_400_000)
            assert months >= 6 and len(ours) == len(theirs) == (end - start) // TF[tf], (s, tf, len(ours), len(theirs))
            for c in ("timestamp", "close_time"):
                assert ours[c].to_numpy(dtype=np.int64).tobytes() == theirs[c].to_numpy(dtype=np.int64).tobytes(), (s, tf, c)
            for c in ("open", "high", "low", "close", "volume"):
                assert ours[c].to_numpy(dtype=np.float64).tobytes() == theirs[c].to_numpy(dtype=np.float64).tobytes(), (s, tf, c)
            assert BL.series_digest(ours) == BL.series_digest(theirs)
            rest_seen += int((store.read_with_src("futures", s, tf)[S.SRC_COL] == "rest").sum())
    assert rest_seen > 0, "REST kaynaklı kuyruk barları pariteye dahil"


def test_store_provider_signature_wide_variant_and_real_funding_timestamps(tmp_path):
    e = DataEnv(tmp_path, start=START, listing={"futures/BTCUSDT": ms(utc(2026, 8, 20))},
                only={"futures/BTCUSDT/1h", "futures/BTCUSDT/funding"})
    e.fb.funding_interval_h = {"BTCUSDT": 4}
    e.run("backfill")
    prov = StoreProvider(e.store(), clock_ms=lambda: ms(e.clock.now()))
    k = prov.klines("BTC/USDT:USDT", "1h", limit=10, start_ms=ms(utc(2026, 9, 1)))
    assert list(k.columns) == BASE_COLS + ["is_closed"] and len(k) == 10 and k["is_closed"].all()
    assert k["timestamp"].iloc[0] == ms(utc(2026, 9, 1))
    wide = StoreProvider(e.store(), wide=True).klines("BTCUSDT", "1h", limit=3, start_ms=ms(utc(2026, 9, 1)))
    assert list(wide.columns) == WIDE_COLS + ["is_closed"]
    f = prov.funding_frame("BTCUSDT", ms(utc(2026, 9, 1)), ms(utc(2026, 9, 30)))
    ts = f["timestamp"].tolist()
    assert len(ts) == 29 * 6 + 1 and any(t % 3_600_000 for t in ts), "gerçek uzlaşma zamanları (±ms), ızgara değil"
    assert e.store().manifest("futures", "BTCUSDT", "funding").interval_ms == 4 * 3_600_000
    assert len(prov.klines("BTCUSDT", "1h", limit=5, start_ms=ms(utc(2030, 1, 1)))) == 0
