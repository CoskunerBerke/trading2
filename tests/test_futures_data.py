# -*- coding: utf-8 -*-
"""Vadeli arşiv G/Ç'si (SPEC fut_v1 §3, §8, §10 test 1–5): ağ YOK — sahte `fetch` ve sentetik zip dosyaları."""
from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile

import numpy as np
import pandas as pd
import pytest

from tradingbot import futures_data as FD
from tradingbot import signal_lab as L

DAY = 86_400_000
M_HDR = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
         "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio")
NOW = int(pd.Timestamp("2026-09-26 05:00", tz="UTC").timestamp() * 1000)
TODAY = NOW - NOW % DAY
YESTERDAY = TODAY - DAY


def ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)


def day(d: int) -> str:
    return pd.Timestamp(d, unit="ms", tz="UTC").strftime("%Y-%m-%d")


def _zip(text: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("x.csv", text)
    return out.getvalue()


def metrics_zip(d: int, sym: str = "XUSDT", oi: float = 100.0, rows: int = 2) -> bytes:
    lines = [M_HDR] + [f"{pd.Timestamp(d + (i + 1) * 300_000, unit='ms'):%Y-%m-%d %H:%M:%S},{sym},{oi + i},{(oi + i) * 50},1,1,1,1"
                       for i in range(rows)]
    return _zip("\n".join(lines) + "\n")


def funding_zip(month_ms: int, rate: float = 0.0001) -> bytes:
    rows = ["calc_time,funding_interval_hours,last_funding_rate"]
    t = month_ms
    while t < L._next_month(month_ms):
        rows.append(f"{t + 5},8,{rate}")
        t += 8 * 3_600_000
    return _zip("\n".join(rows) + "\n")


class Server:
    """data.binance.vision taklidi: gün/ay → bayt; `errors` kümesindeki anahtarlar ConnectionError; istekler kaydedilir."""

    def __init__(self, sym: str = "XUSDT"):
        self.sym, self.files, self.errors, self.asked = sym, {}, set(), []
        self.lock = threading.Lock()

    def add_days(self, a: int, b: int, skip=()):
        d = a
        while d <= b:
            if d not in skip:
                self.files[FD.METRICS_URL.format(sym=self.sym, day=day(d))] = metrics_zip(d, self.sym)
            d += DAY

    def add_months(self, a: int, b: int, skip=()):
        m = L._month_start(a)
        while m <= b:
            if m not in skip:
                self.files[FD.FUNDING_URL.format(sym=self.sym, month=pd.Timestamp(m, unit="ms", tz="UTC").strftime("%Y-%m"))] = funding_zip(m)
            m = L._next_month(m)

    def __call__(self, url: str):
        with self.lock:
            self.asked.append(url)
        if url in self.errors:
            raise ConnectionError(f"arşiv indirilemedi: {url}")
        return self.files.get(url)


def metric_days(urls) -> list[int]:
    return [ms(u[-14:-4]) for u in urls if "/metrics/" in u]


# ---------------------------------------------------------------------------- 1–2: ayrıştırma
def test_parse_metrics_header_string_time_headerless_units_zero_and_duplicates():
    t0 = ms("2026-09-01")
    df = FD.parse_metrics_zip(_zip(f"{M_HDR}\n2026-09-01 00:05:00,XUSDT,10.5,500,1,1,1,1\n"
                                   "2026-09-01 00:00:00,XUSDT,0,0,1,1,1,1\n2026-09-01 00:10:00,XUSDT,,0,1,1,1,1\n"
                                   "2026-09-01 00:05:00,XUSDT,11.0,550,1,1,1,1\n"))
    assert list(df.columns) == ["t_ms", "oi"] and df["t_ms"].dtype == np.int64
    assert df["t_ms"].tolist() == [t0 + 300_000] and df["oi"].tolist() == [11.0], "yinelenende SON satır kalır"
    assert df.attrs["zero_rows"] == 2 and not df.attrs["headerless"]
    for k in (1, 1000, 1 / 1000):                                        # ms, µs, sn (başlıksız: sütun 0 zaman, 2 OI)
        body = "".join(f"{int((t0 + i * 300_000) * k)},XUSDT,{7 + i},0,1,1,1,1\n" for i in range(3))
        df = FD.parse_metrics_zip(_zip(body))
        assert df["t_ms"].tolist() == [t0, t0 + 300_000, t0 + 600_000] and df["oi"].tolist() == [7.0, 8.0, 9.0]
        assert df.attrs["headerless"]
    df = FD.parse_metrics_zip(_zip("2026-09-01 00:05:00,XUSDT,3.0\n"))        # başlıksız dize zaman: başlık sanılmaz
    assert df["t_ms"].tolist() == [t0 + 300_000] and df["oi"].tolist() == [3.0]
    with pytest.raises(ValueError, match="open_time,symbol,oi_contracts"):
        FD.parse_metrics_zip(_zip("open_time,symbol,oi_contracts\n1,X,2\n"))
    assert FD.parse_metrics_zip(_zip(M_HDR + "\n")).empty


def test_parse_funding_with_and_without_interval_column():
    t0 = ms("2026-08-01")
    df = FD.parse_funding_zip(_zip(f"calc_time,funding_interval_hours,last_funding_rate\n{t0 * 1000 + 3000},8,0.0001\n"
                                   f"{t0 + 4 * 3_600_000},4,-0.0002\n{t0 + 8 * 3_600_000},4,\n"))
    assert list(df.columns) == ["t_ms", "rate", "interval_file"]
    assert df["t_ms"].tolist() == [t0 + 3, t0 + 4 * 3_600_000], "µs → ms; NaN oranlı satır atılır"
    assert df["rate"].tolist() == [0.0001, -0.0002] and df["interval_file"].tolist() == [8.0, 4.0]
    df = FD.parse_funding_zip(_zip(f"calc_time,last_funding_rate\n{t0},0.0003\n"))
    assert df["rate"].tolist() == [0.0003] and np.isnan(df["interval_file"]).all()
    df = FD.parse_funding_zip(_zip(f"{t0},XUSDT,0.0005\n"))                   # başlıksız: SON sütun oran
    assert df["t_ms"].tolist() == [t0] and df["rate"].tolist() == [0.0005] and df.attrs["headerless"]
    with pytest.raises(ValueError, match="calc_time,fee"):
        FD.parse_funding_zip(_zip("calc_time,fee\n1,2\n"))


# ---------------------------------------------------------------------------- 3: indirme, önbellek, yeniden deneme
def test_ensure_futures_records_404_retries_errors_skips_current_month_and_reads_cache(tmp_path):
    ns = TODAY - 60 * DAY
    first = ns + 20 * DAY
    gap = first + 5 * DAY                                                     # eski 404 → kesin eksik
    errs = [first + 10 * DAY, first + 11 * DAY, first + 12 * DAY]             # ağ hatası → kaydedilmez
    srv = Server()
    srv.add_days(first, YESTERDAY - DAY, skip={gap})                          # dün henüz yayımlanmadı (yeni 404)
    aug = ms("2026-08-01")
    srv.add_months(ns, aug - 1)                                               # Ağustos ayı dosyası henüz yok
    srv.errors = {FD.METRICS_URL.format(sym="XUSDT", day=day(d)) for d in errs}
    logs = []
    cov = FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, threads=4, log=logs.append)["X/USDT"]
    assert cov["first_day"] == day(first) and cov["n_days"] == (YESTERDAY - first) // DAY + 1
    assert cov["n_error"] == 3 and cov["status"] == "PARTIAL_ERROR", "3/40 gün hata > %5"
    assert cov["n_missing"] == 2 and cov["last_day"] == day(YESTERDAY - DAY)
    assert cov["first_funding"] == pd.Timestamp(ns, unit="ms").strftime("%Y-%m") and cov["last_funding"] == "2026-07"
    assert any(x.startswith("FUT_COV ") and json.loads(x[8:])["sym"] == "XUSDT" for x in logs)
    assert not any("2026-09" in u for u in srv.asked if "fundingRate" in u), "içinde bulunulan ay İSTENMEZ"
    assert len(srv.asked) == len(set(srv.asked)), "aynı dosya iki kez istenmez (hatalı gün de aynı koşuda yeniden istenmez)"
    before = [d for d in metric_days(srv.asked) if d < first]
    assert len(before) <= 12
    idx = json.loads((tmp_path / "futures" / "XUSDT_oi5m.json").read_text())
    assert idx["days_missing"] == [day(gap)] and idx["first_day"] == day(first)
    ok = FD._unranges(idx["days_ok"])
    assert not ok & set(errs) and YESTERDAY not in ok and gap not in ok
    fidx = json.loads((tmp_path / "futures" / "XUSDT_funding.json").read_text())
    assert "2026-08" not in fidx["months_missing"] and "2026-08" not in fidx["months_ok"], "yeni 404 ay kesinleşmez"

    # 2. koşu: hatalı günler ve yeni 404'ler yeniden istenir; kesin eksik gün ve arama bir daha YOK
    srv.errors, srv.asked = set(), []
    srv.add_days(YESTERDAY, YESTERDAY)
    srv.add_months(aug, aug)
    cov = FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, threads=4, log=lambda s: None)["X/USDT"]
    assert sorted(metric_days(srv.asked)) == sorted(errs + [YESTERDAY])
    assert [u for u in srv.asked if "fundingRate" in u] == [FD.FUNDING_URL.format(sym="XUSDT", month="2026-08")]
    assert cov["status"] == "OK" and cov["n_error"] == 0 and cov["n_missing"] == 1 and cov["last_funding"] == "2026-08"

    # 3. koşu: ağ tamamen kapalı — her şey önbellekten (istek YOK)
    def dead(url):
        raise AssertionError(f"ağa çıkıldı: {url}")
    cov3 = FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=dead, log=lambda s: None)["X/USDT"]
    assert cov3 == cov
    raw = FD.read_futures_cache(tmp_path, "X/USDT")
    assert set(raw) == {"oi_t", "oi", "f_t", "f_rate"} and raw["oi_t"].dtype == np.int64
    assert len(raw["oi_t"]) == 2 * (cov["n_ok"]) and np.all(np.diff(raw["oi_t"]) > 0)
    assert raw["f_t"][0] == L._month_start(ns) + 5 and np.all(raw["f_rate"] == 0.0001)
    assert FD.read_futures_cache(tmp_path, "Y/USDT") is None
    off = FD.ensure_futures(["X/USDT", "Y/USDT"], tmp_path, ns, NOW, fetch=dead, offline=True, log=lambda s: None)
    assert off["X/USDT"]["n_ok"] == cov["n_ok"] and off["Y/USDT"]["status"] == "NO_CACHE"


def test_first_day_bisect_is_bounded_and_stored(tmp_path):
    ns = TODAY - 1847 * DAY                                                   # 1d=1825 + 22 ısınma
    first = ns + 1203 * DAY
    srv = Server()
    srv.add_days(first, YESTERDAY)
    cov = FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, threads=8, log=lambda s: None)["X/USDT"]
    assert cov["first_day"] == day(first) and cov["n_ok"] == (YESTERDAY - first) // DAY + 1 and cov["status"] == "OK"
    days = metric_days(srv.asked)
    assert len([d for d in days if d < first]) + 1 <= 12, "ikili arama ≤ 12 istek"
    assert len(days) == len(set(days)), "arama sırasında alınan günler yeniden istenmez"
    srv.asked = []
    FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, log=lambda s: None)
    assert srv.asked == [FD.FUNDING_URL.format(sym="XUSDT", month="2026-08")], "ilk gün dizinden: bir daha aranmaz"
    # başlangıç günü 200 → tek istek, daha eskisi hiç denenmez
    ns = TODAY - 60 * DAY
    srv2 = Server("YUSDT")
    srv2.add_days(ns - 30 * DAY, YESTERDAY)
    FD.ensure_futures(["Y/USDT"], tmp_path, ns, NOW, fetch=srv2, log=lambda s: None)
    assert min(metric_days(srv2.asked)) == ns and len(metric_days(srv2.asked)) == (YESTERDAY - ns) // DAY + 1
    # pencere geriye uzarsa (ilk gün = eski alt uç) daha eski günler aranır
    srv2.asked = []
    cov = FD.ensure_futures(["Y/USDT"], tmp_path, ns - 10 * DAY, NOW, fetch=srv2, log=lambda s: None)["Y/USDT"]
    assert cov["first_day"] == day(ns - 10 * DAY) and sorted(metric_days(srv2.asked)) == [ns - (10 - i) * DAY for i in range(10)]
    # arama sırasında hatalı orta gün: komşu gün denenir, ilk gün yine doğru
    srv3 = Server("ZUSDT")
    first3 = ns + 17 * DAY
    srv3.add_days(first3, YESTERDAY)
    mid = ns + 30 * DAY
    srv3.errors = {FD.METRICS_URL.format(sym="ZUSDT", day=day(mid))}
    cov = FD.ensure_futures(["Z/USDT"], tmp_path, ns, NOW, fetch=srv3, log=lambda s: None)["Z/USDT"]
    assert cov["first_day"] == day(first3) and cov["n_error"] == 1 and cov["status"] == "OK", "1/43 gün hata ≤ %5"
    assert metric_days(srv3.asked).count(mid) == 1, "hatalı gün aynı koşuda yeniden istenmez"


def test_symbol_without_any_metrics_is_no_data(tmp_path):
    srv = Server()
    cov = FD.ensure_futures(["X/USDT"], tmp_path, TODAY - 100 * DAY, NOW, fetch=srv, log=lambda s: None)["X/USDT"]
    assert cov["status"] == "NO_DATA" and cov["first_day"] is None and cov["n_ok"] == 0
    assert len(metric_days(srv.asked)) <= 12


# ---------------------------------------------------------------------------- 4: durdurma eşikleri
def test_fifty_consecutive_errors_abort_and_keep_downloaded_data(tmp_path):
    ns = TODAY - 120 * DAY
    srv = Server()
    srv.add_days(ns, YESTERDAY)
    first_url = FD.METRICS_URL.format(sym="XUSDT", day=day(ns))

    def fetch(url):
        srv.asked.append(url)
        if url == first_url:
            return srv.files[url]
        raise ConnectionError("bağlantı yok")
    with pytest.raises(FD.FuturesDataUnavailable, match="art arda 50") as ei:
        FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=fetch, threads=1, log=lambda s: None)
    assert isinstance(ei.value, L.DownloadAborted) and "ÇALIŞTIRILMADI" in str(ei.value)
    assert len(srv.asked) >= 51
    assert not (tmp_path / "signal_lab_report.json").exists()
    idx = json.loads((tmp_path / "futures" / "XUSDT_oi5m.json").read_text())
    assert FD._unranges(idx["days_ok"]) == {ns} and idx["days_missing"] == [], "inen gün kalır; hatalı günler yazılmaz"
    assert len(FD.read_futures_cache(tmp_path, "X/USDT")["oi_t"]) == 2


def test_error_share_above_twenty_percent_aborts(tmp_path):
    ns = TODAY - 120 * DAY
    srv = Server()
    srv.add_days(ns, YESTERDAY)
    srv.add_months(ns, ms("2026-08-01"))
    srv.errors = {FD.METRICS_URL.format(sym="XUSDT", day=day(ns + i * DAY)) for i in range(1, 120, 4)}   # %25, dağınık
    with pytest.raises(FD.FuturesDataUnavailable, match="sınır %20"):
        FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, log=lambda s: None)
    ok = FD._unranges(json.loads((tmp_path / "futures" / "XUSDT_oi5m.json").read_text())["days_ok"])
    assert len(ok) == 120 - 30
    srv2 = Server()                                                           # %20'nin altı: koşu sürer
    srv2.add_days(ns, YESTERDAY)
    srv2.add_months(ns, ms("2026-08-01"))
    srv2.errors = {FD.METRICS_URL.format(sym="XUSDT", day=day(ns + i * DAY)) for i in range(1, 120, 8)}
    cov = FD.ensure_futures(["X/USDT"], tmp_path / "b", ns, NOW, fetch=srv2, log=lambda s: None)["X/USDT"]
    assert cov["status"] == "PARTIAL_ERROR" and cov["n_error"] == 15


def test_small_incremental_run_tolerates_one_error_but_not_a_dead_network(tmp_path):
    ns = TODAY - 30 * DAY
    srv = Server()
    srv.add_days(ns, YESTERDAY)
    srv.add_months(ns, ms("2026-08-01"))
    srv.errors = {FD.METRICS_URL.format(sym="XUSDT", day=day(YESTERDAY - i * DAY)) for i in range(4)}
    FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, log=lambda s: None)
    srv.errors = {FD.METRICS_URL.format(sym="XUSDT", day=day(YESTERDAY))}
    srv.asked = []
    cov = FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, log=lambda s: None)["X/USDT"]
    assert len(srv.asked) == 4 and cov["n_error"] == 1, "1/4 hata: %20 kuralı 50 istekten önce uygulanmaz"

    def dead(url):
        raise ConnectionError("bağlantı yok")
    with pytest.raises(FD.FuturesDataUnavailable):
        FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=dead, log=lambda s: None)


def test_corrupt_zip_is_refetched_once_then_error(tmp_path):
    ns = TODAY - 10 * DAY
    srv = Server()
    srv.add_days(ns, YESTERDAY)
    bad = FD.METRICS_URL.format(sym="XUSDT", day=day(ns + 3 * DAY))
    flaky = FD.METRICS_URL.format(sym="XUSDT", day=day(ns + 4 * DAY))
    good = srv.files[flaky]
    srv.files[bad] = b"PK\x03\x04bozuk"
    n = {"flaky": 0}

    def fetch(url):
        srv.asked.append(url)
        if url == flaky:
            n["flaky"] += 1
            return b"bozuk" if n["flaky"] == 1 else good
        return srv.files.get(url)
    cov = FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=fetch, log=lambda s: None)["X/USDT"]
    assert srv.asked.count(bad) == 2 and srv.asked.count(flaky) == 2
    assert cov["n_error"] == 1 and cov["n_ok"] == 9


def test_mixed_time_subformats_and_float_ms_are_parsed_not_dropped():
    t0 = ms("2023-01-01")
    df = FD.parse_metrics_zip(_zip(f"{M_HDR}\n2023-01-01 00:00:00,XUSDT,10,1,1,1,1,1\n2023-01-01 00:05:00.000,XUSDT,11,1,1,1,1,1\n"
                                   "2023-01-01T00:10:00Z,XUSDT,12,1,1,1,1,1\n"))
    assert df["t_ms"].tolist() == [t0, t0 + 300_000, t0 + 600_000] and df.attrs["bad_time"] == 0, "alt biçim değişse de satır kalır"
    df = FD.parse_metrics_zip(_zip(f"{t0}.0,XUSDT,10,1\n{t0 + 300_000},XUSDT,11,1\n"))
    assert df["t_ms"].tolist() == [t0, t0 + 300_000] and df.attrs["bad_time"] == 0, "`…000.0` ms rakamdır"
    df = FD.parse_funding_zip(_zip(f"calc_time,last_funding_rate\n{t0}.0,0.0001\nçöp,0.0002\n"))
    assert df["t_ms"].tolist() == [t0] and df.attrs["bad_time"] == 1


def test_unparseable_time_day_is_error_not_cached_and_reported(tmp_path):
    ns = TODAY - 10 * DAY
    srv = Server()
    srv.add_days(ns, YESTERDAY)
    srv.add_months(ns, ms("2026-08-01"))
    d3 = ns + 3 * DAY
    url = FD.METRICS_URL.format(sym="XUSDT", day=day(d3))
    srv.files[url] = _zip(f"{M_HDR}\n{day(d3)} 00:05:00,XUSDT,10,1,1,1,1,1\nçöp-zaman,XUSDT,11,1,1,1,1,1\n")
    logs = []
    cov = FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, log=logs.append)["X/USDT"]
    assert cov["n_error"] == 1 and cov["n_bad_time"] == 1 and cov["n_ok"] == 9
    assert d3 not in FD._unranges(json.loads((tmp_path / "futures" / "XUSDT_oi5m.json").read_text())["days_ok"])
    assert any(x.startswith("FUT_COV ") and json.loads(x[8:])["n_bad_time"] == 1 for x in logs)
    assert any(FD.BAD_TIME in x and "önbelleğe yazılmadı" in x for x in logs)
    srv.asked = []
    FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, log=lambda s: None)
    assert srv.asked == [url], "okunamayan gün sonraki koşuda yeniden istenir"


def test_empty_zip_bad_encoding_and_ragged_csv_are_error_not_crash(tmp_path):
    ns = TODAY - 10 * DAY
    srv = Server()
    srv.add_days(ns, YESTERDAY)
    srv.add_months(ns, ms("2026-08-01"))
    empty = io.BytesIO()
    zipfile.ZipFile(empty, "w").close()
    latin = io.BytesIO()
    with zipfile.ZipFile(latin, "w") as zf:
        zf.writestr("x.csv", M_HDR.encode() + b"\n\xff\xfe\x80,XUSDT,1,1\n")
    ragged = _zip(f"{M_HDR}\n{day(ns)} 00:05:00,XUSDT,10,1,1,1,1,1\n{day(ns)} 00:10:00,XUSDT,10,1,1,1,1,1,9,9,9\n")
    bad = [FD.METRICS_URL.format(sym="XUSDT", day=day(ns + i * DAY)) for i in (2, 4, 6)]
    for u, data in zip(bad, (empty.getvalue(), latin.getvalue(), ragged)):
        srv.files[u] = data
    cov = FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, log=lambda s: None)["X/USDT"]
    assert cov["n_error"] == 3 and cov["n_ok"] == 7
    assert all(srv.asked.count(u) == 2 for u in bad), "bozuk dosya bir kez yeniden çekilir"
    with pytest.raises(ValueError, match="open_time"):                   # başlık/şema hatası yine YÜKSELİR
        FD._get(lambda u: _zip("open_time,symbol,oi_contracts\n1,X,2\n"), "u", FD.parse_metrics_zip)


def test_overlapping_day_files_merge_deterministically_own_day_wins(tmp_path):
    """Her gün dosyası 00:00 … ertesi gün 00:00 (289 satır); paylaşılan satırın değeri farklı. İş parçacığı bitiş sırası
    ve koşu geçmişi ne olursa olsun paylaşılan zamanda KENDİ gününün dosyası kazanır."""
    import random
    import time as _t
    ns = TODAY - 8 * DAY
    srv = Server()
    srv.add_months(ns, ms("2026-08-01"))
    d = ns
    while d <= YESTERDAY:
        rows = [f"{pd.Timestamp(d + i * 300_000, unit='ms'):%Y-%m-%d %H:%M:%S},XUSDT,{1000.0 + (d - ns) // DAY + (0.5 if i == 288 else 0)},1,1,1,1,1"
                for i in range(289)]
        srv.files[FD.METRICS_URL.format(sym="XUSDT", day=day(d))] = _zip(M_HDR + "\n" + "\n".join(rows) + "\n")
        d += DAY
    want = {ns + k * DAY: 1000.0 + k for k in range(1, 8)}               # gün sınırında kendi günün değeri (x.5 değil)
    got = []
    for seed in range(4):
        rng = random.Random(seed)

        def fetch(url, rng=rng):
            _t.sleep(rng.random() * 0.01)
            return srv(url)
        cov = FD.ensure_futures(["X/USDT"], tmp_path / str(seed), ns, NOW, fetch=fetch, threads=8, log=lambda s: None)["X/USDT"]
        raw = FD.read_futures_cache(tmp_path / str(seed), "X/USDT")
        got.append(raw["oi"].tolist())
        assert {t: raw["oi"][raw["oi_t"] == t][0] for t in want} == want and cov["dup_conflicts"] == 7
    assert all(g == got[0] for g in got)
    # koşu geçmişi: önce yalnız sonraki gün önbellekte, önceki gün sonra gelir → önbellekteki kendi-gün satırı kalır
    first = FD.METRICS_URL.format(sym="XUSDT", day=day(ns + 3 * DAY))
    hist = tmp_path / "hist"

    def err_first(url):
        if url == first:
            raise ConnectionError("geçici")
        return srv(url)
    FD.ensure_futures(["X/USDT"], hist, ns, NOW, fetch=err_first, log=lambda s: None)
    FD.ensure_futures(["X/USDT"], hist, ns, NOW, fetch=srv, log=lambda s: None)
    assert FD.read_futures_cache(hist, "X/USDT")["oi"].tolist() == got[0]


# ---------------------------------------------------------------------------- 5: checksum örneklemi ve yoklama
def test_checksum_sample_reports_mismatch_as_warning_only():
    a, b = b"dosya-a", b"dosya-b"
    files = {"u/a.zip": a, "u/a.zip.CHECKSUM": f"{hashlib.sha256(a).hexdigest()}  a.zip\n".encode(),
             "u/b.zip": b, "u/b.zip.CHECKSUM": f"{'0' * 64}  b.zip\n".encode(), "u/c.zip": b}
    logs = []
    res = FD.checksum_sample(["u/a.zip", "u/b.zip", "u/c.zip"], files.get, logs.append)
    assert res["text"] == "checksum örneklem 1/2" and res["mismatch"] == ["b.zip"] and res["warning"]
    assert res["missing"] == ["c.zip"] and any("UYARI" in x and "b.zip" in x for x in logs)


def test_probe_reports_p1_to_p7_with_short_log_lines(tmp_path):
    ns = TODAY - 40 * DAY
    srv = Server()
    srv.add_days(ns, YESTERDAY)
    srv.add_months(ns, ms("2026-08-01"))
    for u, data in list(srv.files.items()):
        srv.files[u + ".CHECKSUM"] = f"{hashlib.sha256(data).hexdigest()}  x.zip".encode()
    cov = FD.ensure_futures(["X/USDT"], tmp_path, ns, NOW, fetch=srv, log=lambda s: None)
    logs = []
    rep = FD.probe(["X/USDT"], tmp_path, NOW, fetch=srv, log=logs.append, coverage=cov)
    assert set(rep) == {f"P{i}" for i in range(1, 8)}
    assert rep["P1"]["XUSDT"]["first_day"] == day(ns) and rep["P1"]["XUSDT"]["n_ok"] == 40
    assert rep["P2"]["raw"]["ilk_gün"]["create_time"] == "dize" and rep["P2"]["raw"]["ilk_gün"]["lines"][0] == M_HDR
    assert rep["P2"]["per_symbol"]["XUSDT"]["rows_per_day"] == [2, 2.0, 2] and rep["P2"]["per_symbol"]["XUSDT"]["grid5m_share"] == 1.0
    assert rep["P3"]["per_symbol"]["XUSDT"]["interval_h"]["8"] > 0 and rep["P3"]["per_symbol"]["XUSDT"]["file_interval_agree"] == 1.0
    assert rep["P3"]["raw"]["son_ay"]["calc_time"] == "rakam_ms"
    assert rep["P4"]["status"] == "404" and rep["P5"]["text"] == "checksum örneklem 3/3" and rep["P6"]["status"] == "404"
    assert rep["P7"]["files"] > 0 and rep["P7"]["cache_mb"] >= 0
    lines = [x for x in logs if x.startswith("FUT_PROBE ")]
    assert {json.loads(x[10:])["item"] for x in lines} == {f"P{i}" for i in range(1, 8)}
    assert all(len(x.encode()) <= 4096 for x in lines)
    rep2 = FD.probe(["X/USDT"], tmp_path, NOW, fetch=srv, log=lambda s: None)          # kapsama önbellek dizininden
    assert rep2["P1"]["XUSDT"]["first_day"] == day(ns) and rep2["P1"]["XUSDT"]["gaps"] == []


def test_log_line_stays_under_four_kilobytes():
    line = FD.log_line("FUT_COV", {"sym": "XUSDT", "gaps": [["2022-01-01", "2022-01-02"]] * 500, "n_ok": 3})
    assert len(line.encode()) <= 4096 and json.loads(line[8:])["n_ok"] == 3
