# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1b — veri birimi (`engine-data`; docs/SYSTEM_LEARNING_ENGINE_V1.md §2.2, §2.4, §3.2–§3.4;
P1b depo kabul testleri 1, 4, 5, 6, 7, 8, 13, 15 + Dukascopy içe alma + CLI). Ağ YOK: sahte data.binance.vision,
sahte REST, sahte saat ve sahte `journalctl` (`research_engine_data_fixtures`).

* 1: sahte sağlayıcıyla idempotent yeniden çalıştırma 0 satır ekler, checksum'lar ve mühür özdeş kalır.
* 4: bir serinin hatası diğerlerini durdurmaz (3 deneme, titreşimli bekleme).
* 5: 418/429 REST adımını durdurur; worker günlüğünde son 60 dk'da 429/418 varsa ya da günlük okunamıyorsa REST hiç
  çağrılmaz (metin kalıbı `market/http.py`'nin biçim dizgelerine karşı sabit).
* 6: motorun REST ağırlığı kayan 60 sn penceresinde 0,1 × IP limitini hiç aşmaz; `X-MBX-USED-WEIGHT-1M` %50'de bekler.
* 7: arşiv uzlaştırma REST barlarını değiştirir ve farkı `runs/data/<id>/diffs.jsonl.gz`'a yazar; REST arşiv barını ezmez;
  `.CHECKSUM`'sız zip `archive_unverified` olur, doğrulanmış sayılmaz, checksum gelince yükselir.
* 8: `MANIFEST_LAG` kendiliğinden düzelir; bozuk parça karantinaya alınıp yeniden çekilir; seri yalnız iki ardışık
  başarısız geceden sonra durur ve yeniden çekim başarılı olunca devam eder.
* 13: ilk doldurma ilerleme/ETA yazar ve 4h pencerelerinde (hh:00–hh:35) ağ isteği yapmadan bekler.
* 15: ilk doldurma `data.lock`'u tutarken gece birimi `SKIPPED_LOCKED` olmaz; okunan parça mühürden sonra değiştiyse
  `DATA_MOVING`.
"""
from __future__ import annotations

import gzip
import json
import lzma
import random
import re
import struct
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_data_fixtures import DataEnv, FakeClock, ms, utc  # noqa: E402
from research_engine_fixtures import FakeVps, at, run_engine_night, trade  # noqa: E402

from tradingbot.research_engine import datastore as DS  # noqa: E402
from tradingbot.research_engine import selfcheck as SC  # noqa: E402
from tradingbot.research_engine import store as S  # noqa: E402
from tradingbot.research_engine.lock import EngineLock  # noqa: E402
from tradingbot.research_engine.provider import DataMoving, SealedReader, StoreProvider, read_sealed  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
H = 3_600_000
START = utc(2026, 10, 6, 0, 41)
LISTING = {f"futures/{s}": ms(utc(2026, 8, 20)) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")}
LISTING.update({f"spot/{s}": ms(utc(2026, 8, 20)) for s in ("BTCUSDT", "ETHUSDT", "PAXGUSDT")})
SMALL = {"futures/BTCUSDT/1h", "futures/BTCUSDT/4h", "futures/BTCUSDT/funding", "futures/BTCUSDT/metrics_5m",
         "futures/ETHUSDT/1h", "spot/BTCUSDT/1h"}


def env(tmp_path, only=SMALL, start=START, **kw) -> DataEnv:
    return DataEnv(tmp_path, start=start, listing=kw.pop("listing", LISTING), only=only, **kw)


def all_parts(e: DataEnv) -> dict[str, bytes]:
    return {str(p.relative_to(e.paths.store)): p.read_bytes() for p in e.paths.store.rglob("*")
            if p.is_file() and p.suffix in (".parquet", ".gz", ".sha256") and "_seal" not in p.parts}


# ============================================================================ kabul 1
def test_rerun_with_fake_provider_adds_zero_rows_and_keeps_checksums_and_seal(tmp_path):
    e = env(tmp_path)
    st = e.run("backfill")
    assert st["result"] == DS.R_SUCCESS and st["exit_code"] == 0, st.get("error")
    d1 = e.status()
    assert d1["data_seal"] and d1["running"] is None and d1["totals"]["stale"] == 0
    assert {k for k, v in d1["series"].items() if v["rows"] > 0} == SMALL
    parts, seal = all_parts(e), d1["data_seal"]
    n_archive = len(e.fb.urls("data.binance.vision"))
    for mode in ("backfill", "update"):
        st = e.run(mode)
        assert st["result"] == DS.R_SUCCESS, st.get("error")
        d = e.status()
        assert all(not v.get("rows_new") for v in d["series"].values()), {k: v.get("rows_new") for k, v in d["series"].items()}
        assert d["data_seal"] == seal and all_parts(e) == parts and st["diffs"]["count"] == 0
    assert len(e.fb.urls("data.binance.vision")) == n_archive, "doğrulanmış arşiv dosyaları yeniden İNDİRİLMEZ"


# ============================================================================ kabul 4
def test_one_series_failure_does_not_stop_the_others_and_is_retried_with_jitter(tmp_path):
    e = env(tmp_path)
    e.fb.fail_always = {"/ETHUSDT/1h/"}
    e.fb.fail = {"/BTCUSDT/4h/BTCUSDT-4h-2026-09.zip": 2}          # iki kez 503, sonra düzelir
    rng = random.Random(7)
    expect = random.Random(7)
    st = e.run("backfill", rng=rng)
    assert st["result"] == DS.R_PARTIAL and st["exit_code"] == 0
    d = e.status()
    eth = d["series"]["futures/ETHUSDT/1h"]
    assert eth["stale"] and eth["status"] == DS.S_STALE and "TransientError" in eth["error"]
    for k in SMALL - {"futures/ETHUSDT/1h"}:
        assert d["series"][k]["status"] == DS.S_OK and d["series"][k]["rows"] > 0, k
    assert d["series"]["futures/BTCUSDT/4h"]["gaps"] == 0
    # 4h serisi (2 geçici hata) ETH 1h'den önce işlenir: 2 + 2 titreşimli bekleme, taban 2 s × 2^deneme × [0,5; 1,5)
    waits = [DS.RETRY_BASE_S * (2 ** a) * (0.5 + expect.random()) for a in (0, 1, 0, 1)]
    assert all(w in e.clock.slept for w in waits), (waits, e.clock.slept[:12])
    assert len({round(w / (2 ** a), 9) for w, a in zip(waits, (0, 1, 0, 1))}) == 4, "titreşim her beklemede farklı"


# ============================================================================ kabul 5
LINE_429 = "Oct 06 00:12:01 vps python[812]: 2026-10-06 00:12:01 WARNING tradingbot.market.http: 429 https://fapi.binance.com/fapi/v1/klines — 60s soğuma"
LINE_418 = "2026-10-06 00:13:00 ERROR tradingbot.market.http: 418 https://fapi.binance.com/fapi/v1/depth — IP yasağı 120s"


@pytest.mark.parametrize("journal,expect", [
    (dict(lines=["tur 1", LINE_429, "tur 2"]), DS.GUARD_SKIP),
    (dict(lines=[LINE_418]), DS.GUARD_SKIP),
    (dict(lines=["tur 1"], rc=1, err="Failed to open journal"), DS.GUARD_UNKNOWN),
    (dict(lines=[]), DS.GUARD_UNKNOWN),
    (dict(lines=["Hint: You are currently not seeing messages from other users and the system."]), DS.GUARD_UNKNOWN),
])
def test_worker_journal_guard_skips_rest_entirely(tmp_path, journal, expect):
    from research_engine_data_fixtures import FakeJournal
    e = env(tmp_path, only={"futures/BTCUSDT/1h", "futures/BTCUSDT/funding"})
    e.journal = FakeJournal(**journal)
    st = e.run("backfill")
    assert st["rest"]["guard"] == expect and expect in st["flags"]
    assert e.fb.rest_log() == [], "REST (exchangeInfo dahil) hiç çağrılmaz"
    assert e.journal.calls and e.journal.calls[0][:3] == ["journalctl", "-u", "tradingbot-worker"]
    assert "--since=-60min" in e.journal.calls[0]
    assert e.status()["series"]["futures/BTCUSDT/1h"]["rows"] > 0, "arşiv adımı yine çalışır"


@pytest.mark.parametrize("code", [429, 418])
def test_418_or_429_halts_the_rest_step(tmp_path, code):
    e = env(tmp_path, only={"futures/BTCUSDT/1h", "futures/BTCUSDT/4h", "futures/BTCUSDT/funding", "futures/ETHUSDT/1h",
                            "futures/BTCUSDT/metrics_5m"})
    e.fb.rest_status, e.fb.rest_status_after = code, 3
    st = e.run("backfill")
    assert len(e.fb.rest_log()) == 4, "ret kodundan sonra tek REST isteği bile yapılmaz"
    assert f"REST_HALTED_{code}" in st["flags"] and st["rest"]["halted"] == code
    assert st["result"] == DS.R_PARTIAL and st["exit_code"] == 0


def test_worker_rate_line_pattern_is_pinned_to_http_py_format_strings():
    src = (ROOT / "tradingbot" / "market" / "http.py").read_text(encoding="utf-8")
    fmts = re.findall(r'log\.(?:warning|error)\("((?:429|418) %s — [^"]+)"', src)
    assert len(fmts) == 2, fmts
    for f in fmts:
        line = f % ("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT", 60.0)
        assert DS.WORKER_RATE_LINE_RE.search("2026-10-06 00:12:01 WARNING tradingbot.market.http: " + line), line
    assert not DS.WORKER_RATE_LINE_RE.search("HTTP 429 sayısı 0")


# ============================================================================ kabul 6
def test_engine_rest_share_never_exceeds_a_tenth_of_the_ip_limit():
    clock = FakeClock(utc(2026, 10, 6, 0, 41))
    b = DS.EngineBudget(clock_s=lambda: clock.now().timestamp(), sleep=clock.sleep)
    assert b.pool.safety == 0.1 and b.pool.get("fapi.binance.com", 2400).capacity == 240
    for _ in range(400):
        b.acquire("fapi.binance.com", 5)
        clock.advance(0.05)
    for _ in range(60):
        b.acquire("api.binance.com", 20)
    assert b.max_window("fapi.binance.com") <= 240 and b.max_window("api.binance.com") <= 120
    assert b.stats["window_waits"] > 0 and b.stats["weight"] == 400 * 5 + 60 * 20
    # sunucu başlığı IP limitinin %50'sini gösteriyorsa bir sonraki dakika başına kadar beklenir
    b.on_response("fapi.binance.com", {"x-mbx-used-weight-1m": "1300"})
    t0 = clock.now().timestamp()
    b.acquire("fapi.binance.com", 1)
    assert b.stats["header_waits"] == 1 and clock.now().timestamp() >= (int(t0 // 60) + 1) * 60
    with pytest.raises(ValueError):
        b.acquire("api.binance.com", 121)


def test_data_run_rest_weight_stays_within_share_end_to_end(tmp_path, monkeypatch):
    keys = {f"futures/{s}/{k}" for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT") for k in ("5m", "15m", "1h", "funding", "metrics_5m",
                                                                                    "markpx_1h")} | {"spot/BTCUSDT/1h"}
    e = env(tmp_path, only=keys, monthly_lag_days=10, daily_lag_h=24 * 30)   # arşiv Ağustos'ta kalır
    monkeypatch.setattr(DS, "REST_LOOKBACK_MS", 20 * 86_400_000)   # uzun REST kuyruğu: pay sınırı gerçekten zorlanır
    e.fb.used_weight = lambda n: 1300 if n == 150 else 5        # 150. yanıtta IP limitinin %50'si aşılmış (worker + motor)
    st = e.run("backfill")
    bud = st["budget"]
    assert bud["requests"] == len(e.fb.rest_log()) > 100 and bud["weight"] > 240
    for host, mx in bud["max_window_weight"].items():
        assert mx <= bud["caps"][host] == DS.ENGINE_SAFETY * DS.IP_LIMITS[host], (host, mx)
    assert bud["window_waits"] > 0, "kayan pencere doldu, beklendi"
    assert bud["header_waits"] > 0, "%50 başlık eşiğinde beklendi"
    assert st["result"] == DS.R_SUCCESS


# ============================================================================ kabul 7
def test_archive_reconciliation_replaces_rest_bars_and_records_the_diff(tmp_path):
    e = env(tmp_path, only={"futures/BTCUSDT/1h", "futures/ETHUSDT/1h"}, daily_lag_h=30)
    e.fb.rest_value_shift = 0.5                                  # REST kapanışları arşivden 0,5 farklı (yalnız bu gece)
    e.run("backfill")
    btc = e.store().read_with_src("futures", "BTCUSDT", "1h")
    rest_rows = btc[btc[S.SRC_COL] == S.SRC_REST]
    assert len(rest_rows) == 48 and rest_rows["timestamp"].min() == ms(utc(2026, 10, 4)), "son iki günün gün zip'i yok → REST"
    e.fb.rest_value_shift = 0.0
    e.clock.t = utc(2026, 10, 7, 0, 41)
    st = e.run("update")                                         # 4 Ekim gün zip'i yayımlandı → REST barları değişir
    assert st["result"] == DS.R_SUCCESS and st["diffs"]["count"] == 2 * 24
    diffs = [json.loads(x) for x in gzip.decompress((e.paths.research / st["diffs"]["file"]).read_bytes()).decode().splitlines()]
    assert {d["series"] for d in diffs} == {"futures/BTCUSDT/1h", "futures/ETHUSDT/1h"}
    oct4 = [d for d in diffs if d["series"] == "futures/BTCUSDT/1h"]
    assert len(oct4) == 24 and all(d["at"].startswith("2026-10-04") for d in oct4)
    assert {(d["action"], d["old_src"], d["new_src"]) for d in oct4} == {("replaced", "rest", "archive")}
    assert all(set(d["cols"]) == {"close"} and abs(d["cols"]["close"][0] - d["cols"]["close"][1] - 0.5) < 1e-9 for d in oct4)
    assert "DATA_DIFF" in st["flags"] and e.status()["last_run"]["diffs"]["count"] == 48
    btc = e.store().read_with_src("futures", "BTCUSDT", "1h")
    day4 = btc[(btc["timestamp"] >= ms(utc(2026, 10, 4))) & (btc["timestamp"] < ms(utc(2026, 10, 5)))]
    assert set(day4[S.SRC_COL]) == {"archive"} and len(day4) == 24
    day5 = btc[(btc["timestamp"] >= ms(utc(2026, 10, 5))) & (btc["timestamp"] < ms(utc(2026, 10, 6)))]
    assert set(day5[S.SRC_COL]) == {"rest"}, "gün zip'i henüz yok → REST kalır"
    e.clock.t = utc(2026, 10, 8, 0, 41)                          # 5 Ekim REST'i de ilk gece (kaydırılmış) alınmıştı
    assert e.run("update")["diffs"]["count"] == 48
    # farksız uzlaştırma: 6 Ekim REST barları kaydırmasız alınmıştı → zip yayımlanınca archive'a yükselir, fark 0
    e.clock.t = utc(2026, 10, 9, 0, 41)
    st = e.run("update")
    assert st["diffs"]["count"] == 0 and st["diffs"]["file"] is None and "DATA_DIFF" not in st["flags"]
    btc = e.store().read_with_src("futures", "BTCUSDT", "1h")
    day6 = btc[(btc["timestamp"] >= ms(utc(2026, 10, 6))) & (btc["timestamp"] < ms(utc(2026, 10, 7)))]
    assert set(day6[S.SRC_COL]) == {"archive"} and len(day6) == 24
    assert e.status()["series"]["futures/BTCUSDT/1h"]["gaps"] == 0


def test_zip_without_checksum_is_unverified_not_counted_and_upgraded_when_checksum_appears(tmp_path):
    e = env(tmp_path, only={"futures/BTCUSDT/1h"})
    e.fb.no_checksum = {"BTCUSDT-1h-2026-10-02.zip"}
    st = e.run("backfill")
    assert st["archive"]["unverified"] == 1
    d = e.status()
    row = d["series"]["futures/BTCUSDT/1h"]
    assert row["unverified_rows"] == 24 and row["src"]["archive_unverified"] == 24 and d["totals"]["unverified_rows"] == 24
    m = e.store().manifest("futures", "BTCUSDT", "1h")
    assert m.files["2026-10-02"]["st"] == "u" and m.files["2026-10-01"]["st"] == "v"
    # parite/okuyucu: doğrulanmamış satırlar varsayılan olarak VERİLMEZ
    prov = StoreProvider(e.store(), clock_ms=lambda: ms(e.clock.now()))
    o2 = ms(utc(2026, 10, 2))
    k = prov.klines("BTC/USDT", "1h", limit=5000, start_ms=o2, end_ms=o2 + 86_400_000 - 1)
    assert len(k) == 0
    assert len(StoreProvider(e.store(), include_unverified=True).klines("BTCUSDT", "1h", limit=5000, start_ms=o2,
                                                                         end_ms=o2 + 86_400_000 - 1)) == 24
    # sonraki gece checksum yayımlandı → aynı satırlar archive'a yükselir (fark yok)
    e.fb.no_checksum = set()
    e.clock.t = utc(2026, 10, 7, 0, 41)
    st = e.run("update")
    row = e.status()["series"]["futures/BTCUSDT/1h"]
    assert row["unverified_rows"] == 0 and st["diffs"]["count"] == 0
    assert e.store().manifest("futures", "BTCUSDT", "1h").files["2026-10-02"]["st"] == "v"


# ============================================================================ kabul 8
class Killed(BaseException):
    pass


def test_manifest_lag_heals_corrupt_part_is_refetched_and_series_halts_only_after_two_failed_nights(tmp_path, monkeypatch):
    e = env(tmp_path, only={"futures/BTCUSDT/1h", "futures/BTCUSDT/4h"})
    e.run("backfill")
    store = e.store()
    sep = store.read("futures", "BTCUSDT", "1h", ms(utc(2026, 9, 1)), ms(utc(2026, 9, 30, 23)))
    # (a) parça yazıldı, manifest yazılamadan birim öldürüldü → sonraki gece MANIFEST_LAG kendiliğinden düzelir
    real = S.ResearchStore._save_manifest
    state = {"killed": False}

    def kill_once(self, m):
        if not state["killed"] and m.timeframe == "1h" and "2026/10" in m.part_meta and m.part_meta["2026/10"]["rows"] > 120:
            state["killed"] = True
            raise Killed()
        return real(self, m)
    monkeypatch.setattr(S.ResearchStore, "_save_manifest", kill_once)
    e.clock.t = utc(2026, 10, 7, 0, 41)
    with pytest.raises(Killed):
        e.run("update")
    monkeypatch.setattr(S.ResearchStore, "_save_manifest", real)
    assert not EngineLock(e.paths.data_lock).held                 # kilit işletim sistemindeydi; süreç bırakır
    e.clock.t = utc(2026, 10, 7, 0, 50)
    st = e.run("update")
    assert st["result"] == DS.R_SUCCESS and st["recovery"]["manifest_lag"] == ["futures/BTCUSDT/1h@2026/10"]
    assert "MANIFEST_LAG" in st["flags"] and st["diffs"]["count"] == 0
    m = e.store().manifest("futures", "BTCUSDT", "1h")
    assert m.refetch == [] and m.recovery == {} and e.store().validate("futures", "BTCUSDT", "1h")["ok"]
    # (b) bozuk parça → karantina + arşivden yeniden çekim (önbellekten), veri aynen geri gelir
    p = e.store().part_path("futures", "BTCUSDT", "1h", 2026, 9)
    p.write_bytes(b"bozuk")
    e.clock.t = utc(2026, 10, 8, 0, 41)
    st = e.run("update")
    assert st["recovery"]["corrupt"] == ["futures/BTCUSDT/1h@2026/09"] and "CORRUPT_PART" in st["flags"]
    got = e.store().read("futures", "BTCUSDT", "1h", ms(utc(2026, 9, 1)), ms(utc(2026, 9, 30, 23)))
    assert got.equals(sep) and e.status()["series"]["futures/BTCUSDT/1h"]["status"] == DS.S_OK
    assert list((e.paths.store / "_quarantine").rglob("*.parquet"))
    # (c) yine bozuk ve arşiv erişilemez: gece 1 durmaz, ARDIŞIK gece 2 durur; diğer seri etkilenmez
    url = DS.archive_url(DS.SeriesSpec("futures", "BTCUSDT", "1h", 0), "2026-09")
    cache = e.paths.archive_cache / "archive" / url.split("://", 1)[1]
    e.fb.fail_always = {"BTCUSDT-1h-2026-09.zip"}
    for night, halted in ((utc(2026, 10, 9, 0, 41), False), (utc(2026, 10, 10, 0, 41), True)):
        e.store().part_path("futures", "BTCUSDT", "1h", 2026, 9).write_bytes(b"bozuk")
        for x in (cache, cache.with_name(cache.name + ".CHECKSUM")):
            x.unlink(missing_ok=True)
        e.clock.t = night
        st = e.run("update")
        row = e.status()["series"]["futures/BTCUSDT/1h"]
        assert (row["status"] == DS.S_HALTED) is halted, (night, row)
        assert e.status()["series"]["futures/BTCUSDT/4h"]["status"] == DS.S_OK
    assert row["stale"] and "iki ardışık gece" in row["halted_reason"] and "SERIES_HALTED" in st["flags"]
    assert st["result"] == DS.R_PARTIAL and st["exit_code"] == 0
    # (d) arşiv geri geldi → yeniden çekim başarılı, seri devam eder
    e.fb.fail_always = set()
    e.clock.t = utc(2026, 10, 11, 0, 41)
    st = e.run("update")
    row = e.status()["series"]["futures/BTCUSDT/1h"]
    assert row["status"] == DS.S_OK and not row["refetch"]
    assert e.store().read("futures", "BTCUSDT", "1h", ms(utc(2026, 9, 1)), ms(utc(2026, 9, 30, 23))).equals(sep)


def test_weekly_deep_sample_finds_damage_invisible_to_stat_and_refetches_it(tmp_path):
    key = "futures/BTCUSDT/1h"
    e = env(tmp_path, only={key})
    e.run("backfill")
    sunday = next(utc(2026, 10, 11) + DS.timedelta(days=7 * k) for k in range(10)
                  if DS.stable_bucket(key) == (utc(2026, 10, 11) + DS.timedelta(days=7 * k)).isocalendar()[1] % 7)
    assert sunday.weekday() == DS.DEEP_SAMPLE_WEEKDAY
    p = e.store().part_path("futures", "BTCUSDT", "1h", 2026, 9)
    e.store().sidecar_path(p).write_text("e" * 64 + "  09.parquet\n", encoding="ascii")
    e.clock.t = sunday.replace(hour=0, minute=41)
    st = e.run("update")
    assert st["recovery"]["deep"] and st["recovery"]["deep"][0]["series"] == key and not st["recovery"]["deep"][0]["ok"]
    assert st["recovery"]["corrupt"] == [f"{key}@2026/09"]
    assert e.store().deep_validate("futures", "BTCUSDT", "1h")["ok"], "aynı gece arşivden yeniden çekildi"


# ============================================================================ kabul 13
def test_backfill_writes_progress_and_eta_and_pauses_in_4h_windows(tmp_path, monkeypatch):
    e = env(tmp_path, start=utc(2026, 10, 6, 3, 50), per_request_s=3.0)
    snaps: list[tuple[datetime, dict]] = []
    orig = DS.DataRun.write_status

    def spy(self, *, final, seal=None):
        orig(self, final=final, seal=seal)
        if not final:
            snaps.append((self.clock(), json.loads(self.paths.data_status.read_text(encoding="utf-8"))["running"]))
    monkeypatch.setattr(DS.DataRun, "write_status", spy)
    st = e.run("backfill", progress_every_s=0)
    assert st["result"] == DS.R_SUCCESS, st.get("error")
    in_win = [t for t, _u, _p in e.fb.log if DS.in_publication_window(t)]
    assert not in_win, f"4h penceresinde {len(in_win)} istek"
    assert e.fb.log[0][0] < utc(2026, 10, 6, 4, 0) and e.fb.log[-1][0] > utc(2026, 10, 6, 4, 36)
    paused = [r for _, r in snaps if r["progress"].get("paused")]
    assert paused and paused[0]["progress"]["pause_reason"] == "PAUSED_4H_WINDOW"
    assert paused[0]["progress"]["resume_at"] == "2026-10-06T04:36:00+00:00"
    assert any(s >= 30 * 60 for s in e.clock.slept)
    mid = [r["progress"] for _, r in snaps if r["progress"].get("total") and 0 < r["progress"]["done"] < r["progress"]["total"]
           and r["progress"].get("rate_per_h_1h")]
    assert mid and all(p["eta"] and p["remaining"] == p["total"] - p["done"] for p in mid)
    assert all(r["mode"] == "backfill" and r["run_id"] == st["run_id"] for _, r in snaps)
    d = e.status()
    assert d["running"] is None and d["last_run"]["result"] == DS.R_SUCCESS and "PAUSED_4H_WINDOW" in d["last_run"]["flags"]
    rec = json.loads((e.paths.data_runs / st["run_id"] / "data_run.json").read_text(encoding="utf-8"))
    assert rec["progress"]["done"] == rec["progress"]["total"] > 0


@pytest.mark.parametrize("hm,inside", [((4, 0), True), ((4, 35), True), ((4, 36), False), ((3, 59), False), ((2, 10), False),
                                       ((20, 30), True), ((0, 41), False), ((1, 37), False), ((12, 5), True)])
def test_publication_window_rule(hm, inside):
    assert DS.in_publication_window(utc(2026, 10, 6, *hm)) is inside


def test_update_deadline_is_before_the_hard_stop_and_the_night_unit():
    assert DS.compute_data_deadline(utc(2026, 10, 6, 0, 41)) == utc(2026, 10, 6, 1, 26)
    assert DS.compute_data_deadline(utc(2026, 10, 6, 0, 30)) == utc(2026, 10, 6, 1, 15)
    assert DS.compute_data_deadline(utc(2026, 10, 6, 14, 0)) == utc(2026, 10, 6, 14, 45)


def test_update_stops_starting_series_after_its_deadline(tmp_path):
    e = env(tmp_path, only={"futures/BTCUSDT/1h", "futures/ETHUSDT/1h"})
    e.run("backfill")
    e.clock.t = utc(2026, 10, 7, 0, 41)
    e.fb.per_request_s = 30 * 60.0                               # her istek yarım saat sürer
    n0 = len(e.fb.log)
    st = e.run("update")
    assert st["result"] == DS.R_DEADLINE and st["exit_code"] == 0 and "DEADLINE" in st["flags"]
    started = [t for t, _u, _p in e.fb.log[n0:]]
    assert started and all(t <= utc(2026, 10, 7, 1, 26) for t in started), "son tarihten sonra istek başlatılmaz"
    assert e.status()["data_seal"] and e.status()["last_run"]["result"] == DS.R_DEADLINE


# ============================================================================ kabul 15
def test_night_is_not_skipped_locked_during_backfill_and_reads_only_sealed_parts(tmp_path):
    e = env(tmp_path, only={"futures/BTCUSDT/1h", "futures/ETHUSDT/1h"})
    st = e.run("backfill")
    seal = e.status()["data_seal"]
    SC.register_epoch(e.paths, "b" * 40, utc(2020, 1, 1), "test", code_hash=SC.engine_code_hash())
    backfill = EngineLock(e.paths.data_lock)
    assert backfill.try_acquire()                                 # ilk doldurma sürüyor
    try:
        trade(e.v.fut(), "ETH/USDT", at("2026-10-05", 3), 100.0, 101.0)
        rs = run_engine_night(e.v, e.host, utc(2026, 10, 6, 1, 40))
        assert rs["result"] == "SUCCESS" and rs["result"] != "SKIPPED_LOCKED", rs["result"]
        assert rs["data_seal"] == seal and rs["selfcheck"]["data"]["status"] == SC.OK
        before = e.paths.data_status.read_bytes()
        st = e.run("update")
        assert st["result"] == DS.R_SKIPPED_LOCKED and st["exit_code"] == 0
        assert e.paths.data_status.read_bytes() == before, "kilitli veri çalıştırması ilerleme dosyasını ezmez"
    finally:
        backfill.release()
    night = EngineLock(e.paths.analysis_lock)
    assert night.try_acquire()
    try:
        assert e.run("update")["result"] == DS.R_SUCCESS            # gece birimi veri birimini de durdurmaz
    finally:
        night.release()
    reader = SealedReader.from_status(e.paths)
    df, info = read_sealed(reader, "futures", "BTCUSDT", "1h")
    assert info["status"] == "OK" and len(df) == e.store().manifest("futures", "BTCUSDT", "1h").row_count
    # mühürden sonra bir doldurma mühürlü parçaya yazar → o seri DATA_MOVING; diğer seri okunur
    extra = e.store().read("futures", "BTCUSDT", "1h", ms(utc(2026, 10, 2)), ms(utc(2026, 10, 2, 5)))
    extra["close"] = extra["close"] + 1
    e.store().write("futures", "BTCUSDT", "1h", extra, src=S.SRC_ARCHIVE)
    df2, info2 = read_sealed(reader, "futures", "BTCUSDT", "1h")
    assert df2 is None and info2["status"] == "DATA_MOVING" and info2["ym"] == "2026/10"
    assert read_sealed(reader, "futures", "ETHUSDT", "1h")[1]["status"] == "OK"
    assert len(StoreProvider(reader).klines("ETHUSDT", "1h", limit=50, start_ms=ms(utc(2026, 9, 1)))) == 50
    # mühür dosyası data_status'la tutmazsa okuyucu hiç kurulmaz
    sf = e.paths.research / e.status()["seal_file"]
    doc = json.loads(gzip.decompress(sf.read_bytes()))
    doc["parts"] = doc["parts"][:-1]
    sf.write_bytes(gzip.compress(json.dumps(doc).encode()))
    with pytest.raises(DataMoving):
        SealedReader.from_status(e.paths)


# ============================================================================ Dukascopy (hazır ayna içe alma)
def bi5(recs: list[tuple[int, int, int, int, int, float]]) -> bytes:
    return lzma.compress(b"".join(struct.pack(">5if", *r) for r in recs), format=lzma.FORMAT_ALONE)


def mirror(root: Path) -> Path:
    base = root / "XAUUSD" / "2019"
    hours = []
    for i in range(48):
        o = 1280_000 + i * 100
        hours.append((i * 3600, o, o + 50, o - 200, o + 300, 1.5))
    hours[5] = (5 * 3600, 1281_000, 1281_000, 1281_000, 1281_000, 0.0)      # düz + hacim 0 → kapalı piyasa (atılır)
    hours[6] = (6 * 3600, 1281_000, 1281_000, 1281_000, 1281_000, 2.0)      # düz ama hacimli → tutulur
    hours[7] = (7 * 3600, 1281_000, 1281_500, 1280_000, 1282_000, 0.0)      # hareketli, hacim 0 → tutulur (sayılır)
    hours[8] = (8 * 3600, 1281_000, 1281_500, 1281_200, 1282_000, 1.0)      # düşük > açılış → tutarsız (atılır)
    (base / "00").mkdir(parents=True)
    (base / "00" / "BID_candles_hour_1.bi5").write_bytes(bi5(hours))
    (base / "BID_candles_day_1.bi5").write_bytes(bi5([(0, 1280_000, 1281_000, 1279_000, 1285_000, 9.0),
                                                      (86_400, 1281_000, 1282_000, 1280_000, 1284_000, 8.0)]))
    (base / "00" / "02").mkdir()
    (base / "00" / "02" / "BID_candles_min_1.bi5").write_bytes(bi5([(i * 60, 1280_000, 1280_100, 1279_900, 1280_200, 0.5)
                                                                     for i in range(10)]))
    (root / "manifest.jsonl").write_text(json.dumps({"kind": "hour", "key": "2019-01", "status": 200, "bytes": 999}) + "\n"
                                         + json.dumps({"kind": "day", "key": "2019", "status": 200, "bytes": 99}) + "\n",
                                         encoding="utf-8")
    return root


def test_dukascopy_ready_mirror_is_imported_byte_identical_and_cleaned(tmp_path):
    e = env(tmp_path / "vps", only=set())
    src = mirror(tmp_path / "mirror")
    st = e.run("import_dukascopy", dukascopy_dir=src)
    assert st["result"] == DS.R_DUKA_DONE and st["exit_code"] == 0, st.get("dukascopy")
    store = e.store()
    h = store.read_with_src("dukascopy", "XAUUSD", S.DUKA_1H)
    assert len(h) == 46 and set(h[S.SRC_COL]) == {S.SRC_UNVERIFIED}
    jan1 = ms(utc(2019, 1, 1))
    assert h["timestamp"].iloc[0] == jan1 and h["open"].iloc[0] == 1280.0 and h["low"].iloc[0] == 1279.8
    assert jan1 + 5 * H not in set(h["timestamp"]) and jan1 + 8 * H not in set(h["timestamp"])
    assert jan1 + 6 * H in set(h["timestamp"]) and jan1 + 7 * H in set(h["timestamp"])
    m1 = store.read("dukascopy", "XAUUSD", S.DUKA_1M)
    assert len(m1) == 10 and m1["timestamp"].iloc[0] == ms(utc(2019, 1, 2))
    for rel in ("XAUUSD/2019/00/BID_candles_hour_1.bi5", "XAUUSD/2019/BID_candles_day_1.bi5",
                "XAUUSD/2019/00/02/BID_candles_min_1.bi5"):
        assert (e.paths.dukascopy / rel).read_bytes() == (src / rel).read_bytes()
    lines = [json.loads(x) for x in (e.paths.dukascopy / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {x["kind"] for x in lines} == {"hour", "day", "min"} and all(len(x["sha256"]) == 64 for x in lines)
    hl = next(x for x in lines if x["kind"] == "hour")
    assert hl["flat_closed"] == 1 and hl["invalid"] == 1 and hl["vol0_moving"] == 1 and hl["kept"] == 46
    status = json.loads((e.paths.dukascopy / "import_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "HAZIR" and e.status()["dukascopy"]["status"] == "HAZIR"
    assert not list(e.paths.dukascopy.glob(".staging-*"))
    st = e.run("import_dukascopy", dukascopy_dir=src)                       # yeniden içe alma idempotent
    assert st["result"] == DS.R_DUKA_DONE and st["dukascopy"]["rows_new"] == {S.DUKA_1H: 0, S.DUKA_1M: 0}


def _break_missing(src: Path) -> None:
    import shutil
    shutil.rmtree(src)


def _break_part(src: Path) -> None:
    (src / "XAUUSD" / "2019" / "01").mkdir()
    (src / "XAUUSD" / "2019" / "01" / "BID_candles_hour_1.bi5.part").write_bytes(b"...")


def _break_lzma(src: Path) -> None:
    (src / "XAUUSD" / "2019" / "00" / "BID_candles_hour_1.bi5").write_bytes(b"bu bir lzma degil")


def _break_seconds(src: Path) -> None:
    (src / "XAUUSD" / "2019" / "00" / "BID_candles_hour_1.bi5").write_bytes(bi5([(0, 1, 1, 1, 1, 1.0), (1800, 1, 1, 1, 1, 1.0)]))


@pytest.mark.parametrize("breaker,why", [(_break_missing, "yok"), (_break_part, ".part"), (_break_lzma, "kayıt denetimini"),
                                         (_break_seconds, "kayıt denetimini"), ("changed", "değişti")])
def test_dukascopy_import_says_yapilamadi_and_writes_nothing(tmp_path, monkeypatch, breaker, why):
    e = env(tmp_path / "vps", only=set())
    src = mirror(tmp_path / "mirror")
    if breaker == "changed":
        real, seen = DS._sha_file, {}

        def sha_twice(p):
            seen[p] = seen.get(p, 0) + 1
            return real(p) if seen[p] == 1 else "0" * 64        # ikinci geçişte kaynak değişmiş gibi
        monkeypatch.setattr(DS, "_sha_file", sha_twice)
    else:
        breaker(src)
    st = e.run("import_dukascopy", dukascopy_dir=src)
    assert st["result"] == DS.R_DUKA_FAILED and st["exit_code"] == 2
    assert why in st["dukascopy"]["reason"], st["dukascopy"]
    assert e.store().manifest("dukascopy", "XAUUSD", S.DUKA_1H).row_count == 0, "sahte/yarım veri yazılmaz"
    assert not (e.paths.dukascopy / "XAUUSD").exists() and not list(e.paths.dukascopy.glob(".staging-*"))
    assert e.status()["dukascopy"]["status"] == "YAPILAMADI"


def test_failed_reimport_keeps_the_previous_ready_import(tmp_path):
    e = env(tmp_path / "vps", only=set())
    src = mirror(tmp_path / "mirror")
    assert e.run("import_dukascopy", dukascopy_dir=src)["result"] == DS.R_DUKA_DONE
    _break_lzma(src)
    st = e.run("import_dukascopy", dukascopy_dir=src)
    assert st["result"] == DS.R_DUKA_FAILED
    d = e.status()["dukascopy"]
    assert d["status"] == "HAZIR" and d["last_attempt"]["status"] == "YAPILAMADI"
    assert e.store().manifest("dukascopy", "XAUUSD", S.DUKA_1H).row_count == 46


def test_dukascopy_status_without_import_says_yapilamadi(tmp_path):
    e = env(tmp_path, only={"futures/BTCUSDT/1d"})
    e.run("backfill")
    d = e.status()["dukascopy"]
    assert d["status"] == "YAPILAMADI" and "indirici yok" in d["reason"]


# ============================================================================ CLI
def test_engine_data_cli_status_is_read_only_and_update_refuses_outside_the_unit(tmp_path, monkeypatch, capsys):
    import tradingbot.cli as cli
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: (_ for _ in ()).throw(AssertionError("config yüklenmez")))
    monkeypatch.setattr(DS, "urllib_http", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ağ yok")))
    v = FakeVps(tmp_path)
    (v.data / "market").mkdir()
    monkeypatch.setenv("TRADINGBOT_DATA", str(v.data))
    monkeypatch.setenv("TRADINGBOT_STATE_DIR", str(v.state))
    monkeypatch.delenv("ENGINE_EXPECTED_MEMORY_MAX", raising=False)
    from tradingbot.research_engine import paths as P
    real_guard = P.disk_guard
    # disk: sunucunun gerçek boş alanından bağımsız (P1a CLI testiyle aynı)
    monkeypatch.setattr(DS, "disk_guard", lambda paths, **kw: real_guard(paths, **{**kw, "free_bytes": 50 * P.GB}))
    assert cli.main(["engine-data", "--status"]) == 0
    assert "data_status.json yok" in capsys.readouterr().out
    assert not (v.data / "research").exists(), "--status hiçbir şey yazmaz"
    rc = cli.main(["engine-data", "--update", "--app-dir", str(tmp_path / "app")])
    out = capsys.readouterr().out
    assert rc == 3 and "ISOLATION_BROKEN" in out
    doc = json.loads((v.data / "research" / "summary" / "data_status.json").read_text(encoding="utf-8"))
    iso = doc["last_run"]["selfcheck_status"]
    assert iso == SC.ISOLATION_BROKEN
    assert sorted(p.name for p in v.state.iterdir()) == ["mode.json"] and not list((v.data / "market").iterdir())
    assert cli.main(["engine-data", "--status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["last_run"]["result"] == "ISOLATION_BROKEN"
    with pytest.raises(SystemExit):
        cli.main(["engine-data"])
