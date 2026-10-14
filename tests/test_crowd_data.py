# -*- coding: utf-8 -*-
"""Kalabalık arşiv G/Ç'si (fut_v2, docs/CROWD_LAB_FUT_V2.md; 2026-09-30): metrics tam ayrıştırma (başlıklı, başlıksız, boş
sütun), taker mumları (µs, başlık), lab mumuyla birleşim (hacim uyuşmazlığı), indirme/önbellek/birleştirme, fut_v1
önbelleğine dokunulmaması, durdurma, çevrimdışı, yoklama ve ≤ 4 KB günlük satırları. Ağ YOK: sahte `fetch`, sentetik zip."""
from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile

import numpy as np
import pandas as pd
import pytest

from tradingbot import crowd_data as CD
from tradingbot import futures_data as FD
from tradingbot import signal_lab as L

DAY = 86_400_000
HOUR = 3_600_000
H4 = 4 * HOUR
M_HDR = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
         "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio")
K_HDR = ("open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,"
         "ignore")
NOW = int(pd.Timestamp("2026-09-26 05:00", tz="UTC").timestamp() * 1000)
TODAY = NOW - NOW % DAY
YESTERDAY = TODAY - DAY


def ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)


def day(d: int) -> str:
    return pd.Timestamp(d, unit="ms", tz="UTC").strftime("%Y-%m-%d")


def month(m: int) -> str:
    return pd.Timestamp(m, unit="ms", tz="UTC").strftime("%Y-%m")


def _zip(text: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("x.csv", text)
    return out.getvalue()


def metrics_zip(d: int, sym: str = "XUSDT", rows: int = 3, base: float = 100.0) -> bytes:
    lines = [M_HDR] + [f"{pd.Timestamp(d + i * 300_000, unit='ms'):%Y-%m-%d %H:%M:%S},{sym},{base + i},{(base + i) * 50},"
                       f"1.{i + 1},2.{i + 1},0.{i + 5},1.0{i}" for i in range(rows)]
    return _zip("\n".join(lines) + "\n")


def kline_rows(a: int, b: int, step: int = H4) -> list[str]:
    """[a, b) aralığındaki barlar: hacim t'ye bağlı (her dosyada aynı bar aynı değer), taker = hacmin %40'ı."""
    rows, t = [], a
    while t < b:
        v = 100.0 + (t // step) % 7
        rows.append(f"{t},1,2,0.5,1.5,{v!r},{t + step - 1},{v * 1.5!r},9,{v * 0.4!r},{v * 0.6!r},0")
        t += step
    return rows


class Server:
    """data.binance.vision taklidi (metrics günleri + taker mum ayları/günleri): `files` url → bayt; `errors` →
    ConnectionError; istekler kaydedilir."""

    def __init__(self, sym: str = "XUSDT"):
        self.sym, self.files, self.errors, self.asked = sym, {}, set(), []
        self.lock = threading.Lock()

    def add_days(self, a: int, b: int, skip=()):
        d = a
        while d <= b:
            if d not in skip:
                self.files[CD.METRICS_URL.format(sym=self.sym, day=day(d))] = metrics_zip(d, self.sym)
            d += DAY

    def add_kline_month(self, m: int, tf: str = "4h", header: bool = True):
        rows = kline_rows(m, L._next_month(m))
        self.files[CD.KLINES_MONTHLY_URL.format(sym=self.sym, tf=tf, month=month(m))] = _zip(
            ("\n".join(([K_HDR] if header else []) + rows)) + "\n")

    def add_kline_days(self, a: int, b: int, tf: str = "4h"):
        d = a
        while d <= b:
            self.files[CD.KLINES_DAILY_URL.format(sym=self.sym, tf=tf, day=day(d))] = _zip("\n".join([K_HDR] + kline_rows(d, d + DAY)) + "\n")
            d += DAY

    def __call__(self, url: str):
        with self.lock:
            self.asked.append(url)
        if url in self.errors:
            raise ConnectionError(f"arşiv indirilemedi: {url}")
        return self.files.get(url)


def fut_index(cache, sym: str = "XUSDT", first: int | None = None, search_from: int | None = None) -> None:
    """fut_v1 dizini (ilk gün kalabalık tarafından YALNIZ okunur)."""
    p = FD._paths(cache, sym)
    p["oi_idx"].parent.mkdir(parents=True, exist_ok=True)
    p["oi_idx"].write_text(json.dumps({"first_day": day(first), "search_from": day(search_from or first)}), encoding="utf-8")


# ---------------------------------------------------------------------------- ayrıştırma
def test_parse_metrics_full_header_every_column_and_nan_never_zero():
    t0 = ms("2026-09-01")
    df = CD.parse_metrics_full_zip(_zip(
        f"{M_HDR}\n2026-09-01 00:05:00,XUSDT,10.5,500,1.1,2.2,0.9,1.05\n"
        "2026-09-01 00:00:00,XUSDT,0,0,-1,,abc,1.0\n"                 # ≤ 0 / boş / okunamayan → o hücre NaN, satır KALIR
        "2026-09-01 00:10:00,XUSDT,11,550,1.2,2.3,1.0,0\n"))
    assert list(df.columns) == ["t_ms", "oi", "tals", "tpls", "gls", "tkls"] and df["t_ms"].dtype == np.int64
    assert df["t_ms"].tolist() == [t0, t0 + 300_000, t0 + 600_000], "satır atılmaz, sıralanır"
    np.testing.assert_array_equal(df["oi"], [np.nan, 10.5, 11.0])
    np.testing.assert_array_equal(df["tals"], [np.nan, 1.1, 1.2])
    np.testing.assert_array_equal(df["tpls"], [np.nan, 2.2, 2.3])
    np.testing.assert_array_equal(df["gls"], [np.nan, 0.9, 1.0])
    np.testing.assert_array_equal(df["tkls"], [1.0, 1.05, np.nan])
    assert not (df[["oi", "tals", "tpls", "gls", "tkls"]].to_numpy() == 0).any(), "NaN hiçbir zaman 0 olmaz"
    assert df.attrs["nan_cells"] == {"oi": 1, "tals": 1, "tpls": 1, "gls": 1, "tkls": 1}
    assert df.attrs["missing_cols"] == [] and not df.attrs["headerless"] and df.attrs["bad_time"] == 0
    # başlıkta oran sütunu yok → o sütun NaN + missing_cols (sessiz 0 yok); OI sütunu yoksa ValueError
    df = CD.parse_metrics_full_zip(_zip("create_time,symbol,sum_open_interest,count_long_short_ratio\n2026-09-01 00:05:00,X,5,1.5\n"))
    assert df["oi"].tolist() == [5.0] and df["gls"].tolist() == [1.5] and np.isnan(df["tpls"]).all()
    assert df.attrs["missing_cols"] == ["tals", "tpls", "tkls"]
    with pytest.raises(ValueError, match="sum_open_interest"):
        CD.parse_metrics_full_zip(_zip("create_time,symbol,oi\n2026-09-01 00:05:00,X,5\n"))
    assert CD.parse_metrics_full_zip(_zip(M_HDR + "\n")).empty and CD.parse_metrics_full_zip(_zip("")).empty


def test_parse_metrics_full_headerless_positions_units_duplicates_and_bad_time():
    t0 = ms("2026-09-01")
    for k in (1, 1000):                                                   # ms / µs
        body = "".join(f"{int((t0 + i * 300_000) * k)},XUSDT,{7 + i},0,1.{i},2.{i},3.{i},4.{i}\n" for i in range(3))
        df = CD.parse_metrics_full_zip(_zip(body))
        assert df.attrs["headerless"] and df["t_ms"].tolist() == [t0, t0 + 300_000, t0 + 600_000]
        assert df["oi"].tolist() == [7.0, 8.0, 9.0] and df["tals"].tolist() == [1.0, 1.1, 1.2]
        assert df["tpls"].tolist() == [2.0, 2.1, 2.2] and df["gls"].tolist() == [3.0, 3.1, 3.2] and df["tkls"].tolist() == [4.0, 4.1, 4.2]
    short = CD.parse_metrics_full_zip(_zip(f"{t0},XUSDT,3.0\n"))           # başlıksız 3 sütun: yalnız OI
    assert short["oi"].tolist() == [3.0] and short.attrs["missing_cols"] == ["tals", "tpls", "gls", "tkls"]
    with pytest.raises(ValueError, match="en az 3"):
        CD.parse_metrics_full_zip(_zip(f"{t0},XUSDT\n"))
    dup = CD.parse_metrics_full_zip(_zip(f"{M_HDR}\n2026-09-01 00:05:00,X,1,0,1,1,1,1\n2026-09-01 00:05:00,X,2,0,1,1,1,1\n"
                                         "2026-09-01 00:10:00,X,3,0,1,1,1,1\n2026-09-01 00:10:00,X,3,0,1,1,1,1\n"))
    assert dup["oi"].tolist() == [2.0, 3.0] and dup.attrs["dup_in_file"] == 1, "yinelenende SON satır; çatışma sayılır"
    bad = CD.parse_metrics_full_zip(_zip(f"{M_HDR}\n2026-09-01 00:05:00,X,1,0,1,1,1,1\nsaat-yok,X,2,0,1,1,1,1\n"))
    assert bad.attrs["bad_time"] == 1
    st, val, _ = FD._get(lambda u: _zip(f"{M_HDR}\n2026-09-01 00:05:00,X,1,0,1,1,1,1\nsaat-yok,X,2,0,1,1,1,1\n"), "u/d.zip",
                         CD.parse_metrics_full_zip)
    assert st == "error" and val.startswith(FD.BAD_TIME), "zamanı okunamayan satırlı dosya `error` (önbelleğe girmez)"


def test_parse_taker_zip_header_headerless_microseconds_and_schema_errors():
    t0 = ms("2026-09-01")
    rows = kline_rows(t0, t0 + 3 * H4)
    df = CD.parse_taker_zip(_zip("\n".join([K_HDR] + rows) + "\n"))
    assert list(df.columns) == ["t_ms", "v", "qv", "n", "tb"] and df["t_ms"].tolist() == [t0, t0 + H4, t0 + 2 * H4]
    v = [100.0 + (t // H4) % 7 for t in (t0, t0 + H4, t0 + 2 * H4)]
    assert df["v"].tolist() == v and df["n"].tolist() == [9.0] * 3
    np.testing.assert_allclose(df["tb"], [x * 0.4 for x in v], rtol=1e-15)   # pandas ayrıştırıcısı son ULP'de oynayabilir
    assert not df.attrs["headerless"]
    micros = [f"{(t0 + i * H4) * 1000},1,2,0.5,1.5,10,{(t0 + (i + 1) * H4) * 1000 - 1},15,3,4,6,0" for i in range(2)]
    dm = CD.parse_taker_zip(_zip("\n".join(micros) + "\n"))
    assert dm.attrs["headerless"] and dm["t_ms"].tolist() == [t0, t0 + H4], "µs → ms"
    assert dm["tb"].tolist() == [4.0, 4.0] and dm["qv"].tolist() == [15.0, 15.0]
    odd = CD.parse_taker_zip(_zip(f"{t0},1,2,0.5,1.5,10,{t0 + H4 - 1},15,3,,6,0\n"))
    assert np.isnan(odd["tb"][0]) and odd["v"][0] == 10.0, "okunamayan taker hücresi NaN (0 değil), satır kalır"
    with pytest.raises(ValueError, match="en az 10"):
        CD.parse_taker_zip(_zip(f"{t0},1,2,0.5,1.5,10\n"))
    with pytest.raises(ValueError, match="taker_buy_volume"):
        CD.parse_taker_zip(_zip("open_time,open,high,low,close,volume,close_time,quote_volume,count\n1,1,1,1,1,1,1,1,1\n"))
    assert CD.parse_taker_zip(_zip(K_HDR + "\n")).empty


def test_join_taker_matches_open_time_and_volume_only():
    ts = np.array([0, H4, 2 * H4, 3 * H4, 4 * H4], dtype=np.int64)
    v = np.array([10.0, 11.0, 12.0, 0.0, 14.0])
    k_t = np.array([3 * H4, 0, H4, 2 * H4], dtype=np.int64)               # sırasız girdi savunması; 4·H4 yok
    k_v = np.array([0.0, 10.0, 11.0 * (1 + 1e-6), 12.0])
    k_tb = np.array([0.0, 4.0, 5.0, 6.0])
    tb, st = CD.join_taker(ts, v, k_t, k_v, k_tb)
    np.testing.assert_array_equal(tb, [4.0, np.nan, 6.0, 0.0, np.nan])
    assert st == {"bars": 5, "found": 4, "vol_mismatch": 1}, "hacmi uyuşmayan bar NaN; satırı olmayan bar NaN"
    tb0, st0 = CD.join_taker(ts, v, [], [], [])
    assert np.isnan(tb0).all() and st0["found"] == 0


# ---------------------------------------------------------------------------- indirme ve önbellek
def _setup(tmp_path, *, first=None):
    ns = FD.need_start({"4h": 60}, ["4h"], NOW)
    first = first if first is not None else ns
    srv = Server()
    srv.add_days(first, YESTERDAY)
    return srv, ns, first


def test_ensure_crowd_downloads_metrics_and_taker_reads_cache_and_never_touches_fut_v1(tmp_path):
    srv, ns, first = _setup(tmp_path)
    gap, err = first + 5 * DAY, first + 9 * DAY
    del srv.files[CD.METRICS_URL.format(sym="XUSDT", day=day(gap))]      # eski 404 → kesin eksik
    srv.errors.add(CD.METRICS_URL.format(sym="XUSDT", day=day(err)))     # ağ hatası → kaydedilmez
    kstart = FD._floor_day(NOW - 60 * DAY)
    jul, aug, sep = ms("2026-07-01"), ms("2026-08-01"), ms("2026-09-01")
    srv.add_kline_month(jul)                                              # bitmiş ay: ay dosyası
    srv.add_kline_days(aug, ms("2026-08-31"))                             # yakın ay dosyası yayımlanmamış → gün dosyaları
    srv.add_kline_days(sep, YESTERDAY - DAY)                              # içinde bulunulan ay: gün dosyaları (dün henüz yok)
    # fut_v1 önbelleği: ilk gün yalnız OKUNUR, dosyalar bayt bayt aynı kalır
    cache = tmp_path / "c"
    fut_index(cache, first=first, search_from=first)
    p = FD._paths(cache, "XUSDT")
    pd.DataFrame({"t_ms": [1, 2], "oi": [3.0, 4.0]}).to_csv(p["oi"], index=False, compression="gzip")
    pd.DataFrame({"t_ms": [first + HOUR, first + 9 * HOUR], "rate": [1e-4, -2e-4], "interval_file": [8.0, 8.0]}).to_csv(
        p["f"], index=False, compression="gzip")
    before = {k: v.read_bytes() for k, v in p.items() if v.exists()}
    logs: list[str] = []
    cov = CD.ensure_crowd(["X/USDT"], ["4h"], cache, {"4h": 60}, NOW, fetch=srv, log=logs.append)["X/USDT"]
    assert {k: v.read_bytes() for k, v in p.items() if v.exists()} == before, "fut_v1 önbelleğine DOKUNULMAZ"
    assert not any("/metrics/" in u and day(first - DAY) in u for u in srv.asked), "fut_v1 dizininde ilk gün var: arama yok"
    assert cov["first_day"] == day(first) and cov["n_error"] == 1 and cov["n_missing"] == 1 and cov["status"] == "OK"
    tk = cov["taker"]["4h"]
    assert (tk["first_month"], tk["last_month"], tk["n_months"]) == ("2026-07", "2026-07", 1)
    assert tk["n_recent404"] == 1 + 1 and tk["n_days"] == 31 + (YESTERDAY - DAY - sep) // DAY + 1, "Ağustos 404 → günler; dün 404"
    assert sum(1 for x in logs if x.startswith("CROWD_COV ")) == 1 and all(len(x.encode()) <= 4000 for x in logs)
    raw = CD.read_crowd_cache(cache, "X/USDT", "4h")
    assert set(raw) == {"m_t", "oi", "tals", "tpls", "gls", "tkls", "k_t", "k_v", "k_tb", "f_t", "f_rate"}
    assert raw["m_t"].dtype == np.int64 and raw["m_t"][0] == first and np.all(np.diff(raw["m_t"]) > 0)
    assert raw["tals"][0] == 1.1 and raw["tpls"][1] == 2.2 and raw["gls"][2] == 0.7 and raw["tkls"][0] == 1.0
    assert raw["f_rate"].tolist() == [1e-4, -2e-4], "fonlama fut_v1 önbelleğinden okunur"
    assert kstart > jul and raw["k_t"][0] == jul, "bitmiş ay dosyası bütünüyle önbelleğe girer"
    assert raw["k_t"][-1] == YESTERDAY - H4 and np.all(np.diff(raw["k_t"]) == H4), "Temmuz ayı + Ağustos/Eylül günleri kesintisiz"
    assert np.allclose(raw["k_tb"], raw["k_v"] * 0.4)
    # ikinci koşu: yalnız hata veren gün + yeni yayımlanan dosyalar istenir; kesin eksik gün ve bitmiş ay bir daha istenmez
    srv.errors.clear()
    srv.add_kline_month(aug)
    srv.add_kline_days(YESTERDAY, YESTERDAY)
    srv.asked.clear()
    cov2 = CD.ensure_crowd(["X/USDT"], ["4h"], cache, {"4h": 60}, NOW, fetch=srv, log=lambda s: None)["X/USDT"]
    metrics_asked = [u for u in srv.asked if "/metrics/" in u]
    assert metrics_asked == [CD.METRICS_URL.format(sym="XUSDT", day=day(err))]
    assert CD.KLINES_MONTHLY_URL.format(sym="XUSDT", tf="4h", month="2026-07") not in srv.asked
    assert CD.KLINES_MONTHLY_URL.format(sym="XUSDT", tf="4h", month="2026-08") in srv.asked
    assert cov2["n_error"] == 0 and cov2["taker"]["4h"]["n_months"] == 2
    raw2 = CD.read_crowd_cache(cache, "X/USDT", "4h")
    assert raw2["k_t"][-1] == YESTERDAY + DAY - H4 and len(np.unique(raw2["k_t"])) == len(raw2["k_t"]), "ay + gün dosyası: tekil"
    # çevrimdışı: ağ YOK, yalnız önbellek
    cov3 = CD.ensure_crowd(["X/USDT"], ["4h"], cache, {"4h": 60}, NOW, fetch=lambda u: pytest.fail(u), log=lambda s: None,
                           offline=True)["X/USDT"]
    assert cov3["status"] == "OK" and cov3["n_ok"] == cov2["n_ok"]
    none = CD.ensure_crowd(["Y/USDT"], ["4h"], tmp_path / "bos", {"4h": 60}, NOW, fetch=lambda u: pytest.fail(u),
                           log=lambda s: None, offline=True)["Y/USDT"]
    assert none["status"] == "NO_CACHE" and CD.read_crowd_cache(tmp_path / "bos", "Y/USDT", "4h") is None


def test_first_day_search_without_fut_v1_index_refetches_with_full_parse(tmp_path):
    ns = FD.need_start({"4h": 60}, ["4h"], NOW)
    first = ns + 17 * DAY
    srv = Server()
    srv.add_days(first, YESTERDAY)
    cov = CD.ensure_crowd(["X/USDT"], ["4h"], tmp_path, {"4h": 60}, NOW, fetch=srv, log=lambda s: None)["X/USDT"]
    assert cov["first_day"] == day(first) and cov["n_ok"] == (YESTERDAY - first) // DAY + 1
    assert len([u for u in srv.asked if "/metrics/" in u]) <= cov["n_ok"] + 14, "ikili arama sınırlı"
    idx = json.loads((tmp_path / "crowd" / "XUSDT_metrics5m.json").read_text(encoding="utf-8"))
    assert idx["first_day"] == day(first) and not FD._paths(tmp_path, "XUSDT")["oi_idx"].exists()
    raw = CD.read_crowd_cache(tmp_path, "X/USDT", "4h")
    assert np.isfinite(raw["gls"]).all() and raw["m_t"][0] == first, "arama günleri de tam ayrıştırmayla alınır"
    srv.asked.clear()
    CD.ensure_crowd(["X/USDT"], ["4h"], tmp_path, {"4h": 60}, NOW, fetch=srv, log=lambda s: None)
    assert not [u for u in srv.asked if "/metrics/" in u], "ilk gün dizinde: bir daha aranmaz"


def test_dead_network_aborts_and_keeps_downloaded_data(tmp_path):
    srv, ns, first = _setup(tmp_path)
    fut_index(tmp_path, first=first)
    good = sorted(u for u in srv.files)[:5]
    srv.errors.update(u for u in srv.files if u not in good)
    for d in range(60):                                                  # taker ayları da hata
        srv.errors.add(CD.KLINES_MONTHLY_URL.format(sym="XUSDT", tf="4h", month=month(ms("2026-06-01") + d * DAY)))
    with pytest.raises(CD.CrowdDataUnavailable, match="ÇALIŞTIRILMADI"):
        CD.ensure_crowd(["X/USDT"], ["4h"], tmp_path, {"4h": 60}, NOW, fetch=srv, log=lambda s: None, threads=1)
    assert issubclass(CD.CrowdDataUnavailable, L.DownloadAborted)
    raw = CD.read_crowd_cache(tmp_path, "X/USDT", "4h")
    assert raw is not None and len(raw["m_t"]) >= 3, "inen veri önbellekte kalır"


def test_overlapping_metrics_days_merge_deterministically_own_day_wins(tmp_path):
    ns = FD.need_start({"4h": 8}, ["4h"], NOW)
    srv = Server()
    d = ns
    while d <= YESTERDAY:
        k = (d - ns) // DAY
        rows = [f"{pd.Timestamp(d + i * 300_000, unit='ms'):%Y-%m-%d %H:%M:%S},XUSDT,{1000.0 + k + (0.5 if i == 288 else 0)},1,"
                f"1.{k},1,1,1" for i in range(289)]
        srv.files[CD.METRICS_URL.format(sym="XUSDT", day=day(d))] = _zip(M_HDR + "\n" + "\n".join(rows) + "\n")
        d += DAY
    fut_index(tmp_path, first=ns)
    cov = CD.ensure_crowd(["X/USDT"], ["4h"], tmp_path, {"4h": 8}, NOW, fetch=srv, log=lambda s: None, threads=8)["X/USDT"]
    raw = CD.read_crowd_cache(tmp_path, "X/USDT", "4h")
    for k in range(1, 5):
        t = ns + k * DAY
        assert raw["oi"][raw["m_t"] == t][0] == 1000.0 + k, "gün sınırında kendi günün satırı kazanır"
    assert cov["dup_conflicts"] > 0 and len(np.unique(raw["m_t"])) == len(raw["m_t"])


# ---------------------------------------------------------------------------- yoklama ve günlük
def test_probe_p1_to_p7_taker_agreement_and_short_log_lines(tmp_path):
    srv, ns, first = _setup(tmp_path)
    fut_index(tmp_path, first=first)
    for m in (ms("2026-07-01"), ms("2026-08-01")):
        srv.add_kline_month(m)
    srv.add_kline_days(ms("2026-09-01"), YESTERDAY)
    for u, data in list(srv.files.items()):
        srv.files[u + ".CHECKSUM"] = f"{hashlib.sha256(data).hexdigest()}  x.zip".encode()
    cov = CD.ensure_crowd(["X/USDT"], ["4h"], tmp_path, {"4h": 60}, NOW, fetch=srv, log=lambda s: None)
    # lab'ın kendi mum önbelleği: bir barın hacmi farklı (uyuşmazlık), bir bar taker dosyasında yok
    ts = np.arange(FD._floor_day(NOW - 60 * DAY), YESTERDAY + DAY, H4, dtype=np.int64)
    vol = 100.0 + (ts // H4) % 7
    vol[3] += 1.0
    lab = pd.DataFrame({"timestamp": np.append(ts, ts[-1] + H4), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                        "volume": np.append(vol, 50.0), "close_time": np.append(ts, ts[-1] + H4) + H4 - 1})
    lab.to_csv(L.cache_path(tmp_path, "X/USDT", "4h"), index=False, compression="gzip")
    logs: list[str] = []
    rep = CD.probe(["X/USDT"], ["4h"], tmp_path, NOW, fetch=srv, log=logs.append, coverage=cov)
    assert set(rep) == {f"P{i}" for i in range(1, 8)}
    assert rep["P1"]["XUSDT"]["first_day"] == day(first) and rep["P1"]["XUSDT"]["taker"]["4h"]["n_months"] == 2
    assert rep["P2"]["raw"]["ilk_gün"]["lines"][0] == M_HDR
    ps = rep["P2"]["per_symbol"]["XUSDT"]
    assert ps["gls"]["first_day"] == day(first) and ps["gls"]["empty_by_year"] == {"2026": 0.0}
    assert rep["P3"]["raw"]["4h_ilk_ay"]["lines"][0] == K_HDR
    agree = rep["P3"]["per_symbol"][0]
    assert agree["bars"] == len(lab) and agree["vol_mismatch"] == 1 and agree["found_share"] < 1.0
    assert agree["valid_share"] == pytest.approx((len(lab) - 2) / len(lab), abs=1e-4) and agree["taker_share_mean"] == 0.4
    assert rep["P4"]["metrics"]["status"] == "404" and rep["P4"]["klines"]["status"] == "404"
    assert rep["P5"]["n"] == 3 and rep["P5"]["ok"] == 3 and not rep["P5"]["warning"]
    assert rep["P6"]["XUSDT"]["grid5m_share"] == 1.0 and rep["P7"]["files"] > 0
    lines = [x for x in logs if x.startswith("CROWD_PROBE ")]
    assert {json.loads(x[len("CROWD_PROBE "):])["item"] for x in lines} == {f"P{i}" for i in range(1, 8)}
    assert all(len(x.encode()) <= 4000 for x in lines)
    sch = CD.cache_schema_lines(["X/USDT"], ["4h"], tmp_path)
    assert [json.loads(x[len("CROWD_PROBE "):])["item"] for x in sch] == ["P2", "P6", "P3"]
    assert CD.taker_agreement(tmp_path, "Z/USDT", "4h")["status"] == "NO_CACHE"


def test_log_lines_stay_under_four_kilobytes_and_column_map_matches_positions():
    line = FD.log_line("CROWD_COV", {"sym": "XUSDT", "gaps": [["2022-01-01", "2022-01-02"]] * 500, "n_ok": 3,
                                     "taker": {"4h": {"n_months": 3}}})
    assert len(line.encode()) <= 4000 and json.loads(line[len("CROWD_COV "):])["n_ok"] == 3
    hdr = M_HDR.split(",")
    for field, col in CD.METRICS_HEADER.items():
        assert hdr[CD.METRICS_POSITIONS[field]] == col, field
    khdr = K_HDR.split(",")
    for field, col in CD.TAKER_HEADER.items():
        assert khdr[CD.TAKER_POSITIONS[field]] == col, field
    assert CD.COLUMN_MAP["metrics_header"]["oi"] == "sum_open_interest" and "sum_open_interest_value" not in CD.METRICS_HEADER.values()
