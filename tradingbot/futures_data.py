# -*- coding: utf-8 -*-
"""VADELİ VERİ (OI/FONLAMA) — sinyal laboratuvarı için arşiv G/Ç'si (SPEC fut_v1 §3, §8). Canlı kod bunu İÇE AKTARMAZ.

* Kaynak yalnız data.binance.vision arşivi (REST yedeği YOK): açık pozisyon `daily/metrics` gün dosyaları (5 dk satır,
  `sum_open_interest` BAZ birimde; `_value` fiyatla şiştiği için kullanılmaz), fonlama `monthly/fundingRate` ay
  dosyaları (yalnız BİTMİŞ aylar; içinde bulunulan ay hiç istenmez). `daily/fundingRate` yalnız yoklamada (P6).
* İndirme ana süreçte, mum döngüsünden sonra ve süreç havuzundan ÖNCE: tek `ThreadPoolExecutor` bütün eksik dosyaları
  çeker (`SIGNAL_LAB_FUTURES_THREADS`, varsayılan 8). İşçiler yalnız önbelleği okur (`read_futures_cache`, ağ YOK).
* Önbellek `signal_lab_data/futures/`: ham 5 dk OI satırları (hizalama değişirse yeniden indirme gerekmez) + gün
  dizini (ilk gün, alınan gün aralıkları, kesinleşmiş eksik günler); fonlama satırları + ay dizini. 404 gün `now − 3 g`
  öncesindeyse kesin eksik (bir daha istenmez), daha yenisi sonraki koşuda yeniden denenir; `error` biten gün/ay HİÇ
  kaydedilmez (sonraki koşuda yeniden denenir).
* Durdurma: art arda 50 istek `error` ya da koşunun isteklerinin %20'sinden fazlası `error` → `FuturesDataUnavailable`
  (laboratuvar ÇALIŞMAZ; inen veri önbellekte kalır). Sembolün pencere günlerinin %5'inden fazlası `error` →
  kapsamada `PARTIAL_ERROR`, koşu sürer (o barlar NaN olur).
* İlk gün: önce istenen başlangıç denenir (200 → tek istek), değilse [başlangıç, dün] ikili aranır (≈11 istek);
  sonuç dizine yazılır ve bir daha aranmaz.

Günlük satırları `FUT_COV` / `FUT_PROBE` (her biri ≤ 4 KB; bakımcı yalnız iş günlüğünü okur).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import signal_lab as L

_DAY = L._DAY
METRICS_URL = L.ARCHIVE_BASE + "/daily/metrics/{sym}/{sym}-metrics-{day}.zip"
FUNDING_URL = L.ARCHIVE_BASE + "/monthly/fundingRate/{sym}/{sym}-fundingRate-{month}.zip"
FUNDING_DAILY_URL = L.ARCHIVE_BASE + "/daily/fundingRate/{sym}/{sym}-fundingRate-{day}.zip"   # yalnız yoklama (P6)
SUBDIR = "futures"
WARMUP_DAYS = 22                  # = WINDOW_DAYS (20) + 2: en uzun kayan pencere + OI geriye bakışı
METRICS_FINAL_404_DAYS = 3        # bundan eski 404 gün kesin eksik; daha yenisi yayımlanmamış olabilir
FUNDING_FINAL_404_DAYS = 40       # _month_start(now − 40 g) öncesindeki 404 ay kesin eksik
ABORT_CONSECUTIVE = 50            # art arda bu kadar `error` → durdur
ABORT_ERROR_SHARE = 0.20          # koşunun isteklerinin bu payından fazlası `error` → durdur
PARTIAL_ERROR_SHARE = 0.05        # sembolün pencere günlerinin bu payından fazlası `error` → PARTIAL_ERROR
PROGRESS_EVERY = 2000
LOG_MAX_BYTES = 4000              # tek günlük satırı üst sınırı (4 KB altı)
DEFAULT_THREADS = 8

METRICS_TIME_COLS = ("create_time",)
METRICS_OI_COLS = ("sum_open_interest",)
FUNDING_TIME_COLS = ("calc_time", "funding_time", "fundingtime")
FUNDING_RATE_COLS = ("last_funding_rate", "funding_rate", "fundingrate")
FUNDING_INTERVAL_COLS = ("funding_interval_hours",)
BAD_TIME = "okunamayan zaman"     # `_get` hata metni öneki: zamanı okunamayan satırlı dosya `error` (önbelleğe girmez)
CORRUPT = "bozuk dosya"           # `_get` hata metni öneki: indirilen dosya okunamadı (ağ hatası DEĞİL)

#: son `ensure_futures` koşusunun sayaçları (P7 hız yoklaması okur)
LAST_RUN_STATS: dict[str, Any] = {}


class FuturesDataUnavailable(L.DownloadAborted):
    """Vadeli arşiv (OI/fonlama) indirilemedi: laboratuvar eksik veriyle ÇALIŞTIRILMAZ."""


# ---------------------------------------------------------------------------- yardımcılar
def _sym(symbol: str) -> str:
    return symbol.split(":")[0].replace("/", "").upper()


def _day_str(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ms) // 1000))


def _month_str(ms: int) -> str:
    return time.strftime("%Y-%m", time.gmtime(int(ms) // 1000))


def _day_ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)


def _floor_day(ms: int) -> int:
    return int(ms) - int(ms) % _DAY


def need_start(days: dict[str, int], tfs, now_ms: int) -> int:
    """İndirme penceresinin başı: now − max(gün[tf])·GÜN − WARMUP_DAYS·GÜN (gün başına yuvarlanır)."""
    return _floor_day(int(now_ms) - max(int(days[tf]) for tf in tfs) * _DAY - WARMUP_DAYS * _DAY)


def _ranges(days: set[int]) -> list[list[str]]:
    """Gün kümesi → [[ilk, son], ...] (ardışık günler tek aralık)."""
    out: list[list[int]] = []
    for d in sorted(days):
        if out and d == out[-1][1] + _DAY:
            out[-1][1] = d
        else:
            out.append([d, d])
    return [[_day_str(a), _day_str(b)] for a, b in out]


def _unranges(rs) -> set[int]:
    out: set[int] = set()
    for a, b in rs or ():
        d, e = _day_ms(a), _day_ms(b)
        while d <= e:
            out.add(d)
            d += _DAY
    return out


def log_line(tag: str, obj: dict) -> str:
    """`TAG {json}` — 4 KB'yi aşarsa en uzun alanlar kısaltılır (JSON geçerli kalır)."""
    obj = dict(obj)
    line = f"{tag} {json.dumps(obj, ensure_ascii=False, separators=(',', ':'))}"
    while len(line.encode("utf-8")) > LOG_MAX_BYTES:
        k = max(obj, key=lambda x: len(json.dumps(obj[x], ensure_ascii=False)))
        if obj[k] == "…(kısaltıldı)":
            break
        obj[k] = "…(kısaltıldı)"
        line = f"{tag} {json.dumps(obj, ensure_ascii=False, separators=(',', ':'))}"
    return line


# ---------------------------------------------------------------------------- ayrıştırma (§3.3)
def _zip_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise zipfile.BadZipFile("zip içinde dosya yok")
        return zf.read(names[0]).decode("utf-8-sig")


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_header(cell: str) -> bool:
    """Başlık = sayısal OLMAYAN ve tarih olarak da okunmayan ilk hücre (dize zamanlı başlıksız dosya başlık sanılmaz)."""
    cell = cell.strip().strip('"')
    if not cell or _is_number(cell):
        return False
    try:
        pd.Timestamp(cell)
        return False
    except (ValueError, TypeError):
        return True


def _read_rows(data: bytes) -> tuple[list[str] | None, pd.DataFrame]:
    raw = _zip_text(data)
    if not raw.strip():
        return None, pd.DataFrame()
    df = pd.read_csv(io.StringIO(raw), header=None, dtype=str, skip_blank_lines=True)
    if df.empty:
        return None, df
    first = str(df.iloc[0, 0])
    if _is_header(first):
        header = [str(x).strip().strip('"') for x in df.iloc[0].tolist()]
        return header, df.iloc[1:].reset_index(drop=True)
    return None, df


def _col(header: list[str], names: tuple[str, ...], what: str, kind: str) -> int:
    low = [h.lower() for h in header]
    for n in names:
        if n in low:
            return low.index(n)
    raise ValueError(f"{kind} dosyasında '{what}' sütunu yok — görülen başlık: {','.join(header)}")


def _to_ms(col: pd.Series) -> np.ndarray:
    """Zaman → ms (float; okunamayan NaN). Rakam (`123` ya da `123.0`): >10**14 µs (÷1000), <10**11 sn (×1000), aksi ms;
    dize UTC tarih. Dizeler ISO8601 ile okunur (alt biçim satırdan satıra değişebilir: `00:00:00` / `00:05:00.000`);
    kalanlar tek tek (`mixed`). pandas'ın varsayılanı biçimi ilk satırdan çıkarır ve farklı alt biçimli satırı NaT yapar."""
    s = col.astype(str).str.strip().str.strip('"')
    out = np.full(len(s), np.nan)
    dig = s.str.fullmatch(r"\d+(?:\.0*)?").fillna(False).to_numpy(dtype=bool)
    if dig.any():
        v = s[dig].str.split(".").str[0].astype("int64").to_numpy()
        v = np.where(v > 10 ** 14, v // 1000, np.where(v < 10 ** 11, v * 1000, v))
        out[dig] = v.astype(np.float64)
    if (~dig).any():
        txt = s[~dig]
        dt = pd.to_datetime(txt, utc=True, errors="coerce", format="ISO8601")
        left = dt.isna() & txt.str.len().gt(0)
        if left.any():
            dt[left] = pd.to_datetime(txt[left], utc=True, errors="coerce", format="mixed")
        ms = (dt - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)
        out[~dig] = ms.astype("Float64").to_numpy(dtype=np.float64, na_value=np.nan)
    return out


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("t_ms", kind="mergesort").drop_duplicates("t_ms", keep="last").reset_index(drop=True)
    df["t_ms"] = df["t_ms"].astype("int64")
    return df


def parse_metrics_zip(data: bytes) -> pd.DataFrame:
    """`daily/metrics` zip → [t_ms:int64, oi:float] (baz birim). `attrs`: zero_rows (oi ≤ 0/NaN atılan), bad_time,
    headerless (konumdan okundu: sütun 0 zaman, sütun 2 OI)."""
    header, df = _read_rows(data)
    if header is None and df.empty:
        out = pd.DataFrame({"t_ms": np.array([], dtype=np.int64), "oi": np.array([], dtype=float)})
        out.attrs.update(zero_rows=0, bad_time=0, headerless=False)
        return out
    if header is not None:
        ti, oi = _col(header, METRICS_TIME_COLS, "create_time", "metrics"), _col(header, METRICS_OI_COLS, "sum_open_interest", "metrics")
    else:
        if df.shape[1] < 3:
            raise ValueError(f"metrics dosyası başlıksız ve {df.shape[1]} sütunlu (en az 3 gerekir)")
        ti, oi = 0, 2
    t = _to_ms(df[ti]) if len(df) else np.array([], dtype=float)
    v = pd.to_numeric(df[oi], errors="coerce").to_numpy(dtype=float) if len(df) else np.array([], dtype=float)
    good_t = np.isfinite(t)
    good = good_t & np.isfinite(v) & (v > 0)
    out = _finish(pd.DataFrame({"t_ms": t[good], "oi": v[good]}))
    out.attrs.update(zero_rows=int((good_t & ~good).sum()), bad_time=int((~good_t).sum()), headerless=header is None)
    return out


def parse_funding_zip(data: bytes) -> pd.DataFrame:
    """`fundingRate` zip → [t_ms:int64, rate:float, interval_file:float (sütun yoksa NaN)]. NaN oranlı satır atılır.
    Başlıksız dosya: sütun 0 zaman, SON sütun oran (`attrs["headerless"]`). `attrs`: bad_rows (atılan), bad_time."""
    header, df = _read_rows(data)
    if header is None and df.empty:
        out = pd.DataFrame({"t_ms": np.array([], dtype=np.int64), "rate": np.array([], dtype=float),
                            "interval_file": np.array([], dtype=float)})
        out.attrs.update(bad_rows=0, bad_time=0, headerless=False)
        return out
    ii = None
    if header is not None:
        ti, ri = _col(header, FUNDING_TIME_COLS, "calc_time", "fundingRate"), _col(header, FUNDING_RATE_COLS, "last_funding_rate", "fundingRate")
        low = [h.lower() for h in header]
        ii = next((low.index(n) for n in FUNDING_INTERVAL_COLS if n in low), None)
    else:
        if df.shape[1] < 2:
            raise ValueError(f"fundingRate dosyası başlıksız ve {df.shape[1]} sütunlu (en az 2 gerekir)")
        ti, ri = 0, df.shape[1] - 1
    n = len(df)
    t = _to_ms(df[ti]) if n else np.array([], dtype=float)
    r = pd.to_numeric(df[ri], errors="coerce").to_numpy(dtype=float) if n else np.array([], dtype=float)
    iv = pd.to_numeric(df[ii], errors="coerce").to_numpy(dtype=float) if (n and ii is not None) else np.full(n, np.nan)
    good = np.isfinite(t) & np.isfinite(r)
    out = _finish(pd.DataFrame({"t_ms": t[good], "rate": r[good], "interval_file": iv[good]}))
    out.attrs.update(bad_rows=int((~good).sum()), bad_time=int((~np.isfinite(t)).sum()), headerless=header is None)
    return out


# ---------------------------------------------------------------------------- önbellek
def _dir(cache_dir) -> Path:
    return Path(cache_dir) / SUBDIR


def _paths(cache_dir, sym: str) -> dict[str, Path]:
    d = _dir(cache_dir)
    return {"oi": d / f"{sym}_oi5m.csv.gz", "oi_idx": d / f"{sym}_oi5m.json",
            "f": d / f"{sym}_funding.csv.gz", "f_idx": d / f"{sym}_funding.json"}


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _write_atomic(p: Path, write: Callable[[Path], None]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    write(tmp)
    os.replace(tmp, p)


def _merge_csv(p: Path, new: list[tuple[Any, pd.DataFrame]], cols: list[str], own: Callable[[np.ndarray, Any], np.ndarray] | None = None) -> int:
    """Önbellek + yeni dosyalar → tek sıralı CSV. Aynı `t_ms` birden çok kaynaktaysa kazanan iş parçacığı bitiş sırasına
    DEĞİL yalnız veriye bağlıdır: yeni dosyalar anahtarına (gün/ay) göre sıralanır; `own` verilirse zamanı dosyanın KENDİ
    gününe düşen satır önceliklidir (öncelik: kendi 2 > önbellek 1 > komşu gün 0; koşu geçmişinden bağımsız). Döner:
    değerleri FARKLI yinelenen `t_ms` sayısı (P2 teşhisi)."""
    if not new:
        return 0
    parts = [pd.read_csv(p).assign(_p=1)] if p.exists() else []
    for key, f in sorted(new, key=lambda kv: kv[0]):
        if len(f):
            t = f["t_ms"].to_numpy(dtype=np.int64)
            parts.append(f[cols].assign(_p=np.where(own(t, key), 2, 0) if own is not None else 2))
    if not parts:
        return 0
    df = pd.concat(parts, ignore_index=True)
    dup = df[df.duplicated("t_ms", keep=False)]
    conflicts = int((dup.drop_duplicates(cols).groupby("t_ms").size() > 1).sum()) if len(dup) else 0
    df = df.sort_values(["t_ms", "_p"], kind="mergesort").drop_duplicates("t_ms", keep="last").reset_index(drop=True)
    df["t_ms"] = df["t_ms"].astype("int64")
    df = df[cols]
    _write_atomic(p, lambda t: df.to_csv(t, index=False, compression="gzip"))
    return conflicts


def _own_day(t: np.ndarray, day: int) -> np.ndarray:
    return (t - t % _DAY) == int(day)


def read_futures_cache(cache_dir, symbol: str) -> dict[str, np.ndarray] | None:
    """İşçi tarafı (ağ YOK): {"oi_t","oi","f_t","f_rate"} numpy; OI önbelleği yoksa None. Fonlama yoksa boş dizi."""
    p = _paths(cache_dir, _sym(symbol))
    if not p["oi"].exists():
        return None
    oi = pd.read_csv(p["oi"])
    f = pd.read_csv(p["f"]) if p["f"].exists() else pd.DataFrame({"t_ms": [], "rate": []})
    return {"oi_t": oi["t_ms"].to_numpy(dtype=np.int64), "oi": oi["oi"].to_numpy(dtype=np.float64),
            "f_t": f["t_ms"].to_numpy(dtype=np.int64), "f_rate": f["rate"].to_numpy(dtype=np.float64)}


# ---------------------------------------------------------------------------- indirme (§3.4)
def _get(fetch: Callable[[str], bytes | None], url: str, parser: Callable[[bytes], pd.DataFrame]) -> tuple[str, Any, int]:
    """(durum, veri, bayt): "ok" + DataFrame | "404" | "error" + hata metni. Bozuk dosya (zip, boş zip, UTF-8 dışı,
    satır alan sayısı tutarsız CSV) bir kez yeniden çekilir, yine bozuksa `error` (önbelleğe girmez, sonraki koşuda
    yeniden denenir; tek bozuk dosya koşuyu düşürmez). Zamanı okunamayan satır varsa dosya `error` (`BAD_TIME`): satır
    sessizce atılıp gün alınmış sayılmaz. Başlık/şema hatası (sütun adı; ValueError) YÜKSELİR: sessiz NaN yok."""
    nbytes = 0
    for attempt in range(2):
        try:
            data = fetch(url)
        except Exception as exc:  # noqa: BLE001 — ağ hatası `error` sayılır, koşu sayaçları karar verir
            return "error", f"{type(exc).__name__}: {str(exc)[:160]}", nbytes
        if data is None:
            return "404", None, nbytes
        nbytes += len(data)
        try:
            val = parser(data)
        except (zipfile.BadZipFile, zlib.error, EOFError, UnicodeDecodeError, pd.errors.ParserError) as exc:
            if attempt:
                return "error", f"{CORRUPT}: {type(exc).__name__}: {str(exc)[:120]}", nbytes
            continue
        bad = int(val.attrs.get("bad_time", 0))
        if bad:
            return "error", f"{BAD_TIME}: {bad} satır ({url.rsplit('/', 1)[-1]})", nbytes
        return "ok", val, nbytes
    return "error", CORRUPT, nbytes      # pragma: no cover


class _Sym:
    """Bir sembolün koşu içi durumu (yalnız ana iş parçacığı yazar)."""

    def __init__(self, symbol: str, cache_dir, ns: int, yesterday: int, now_ms: int):
        self.symbol, self.sym = symbol, _sym(symbol)
        self.paths = _paths(cache_dir, self.sym)
        idx = _read_json(self.paths["oi_idx"])
        fidx = _read_json(self.paths["f_idx"])
        have_csv = self.paths["oi"].exists()
        self.first_day = _day_ms(idx["first_day"]) if idx.get("first_day") else None
        self.search_from = _day_ms(idx["search_from"]) if idx.get("search_from") else self.first_day
        self.ok = _unranges(idx.get("days_ok")) if have_csv else set()
        self.missing = {_day_ms(d) for d in idx.get("days_missing", [])}
        self.zero_rows = int(idx.get("zero_rows", 0)) if have_csv else 0
        have_f = self.paths["f"].exists()
        self.m_ok = set(fidx.get("months_ok", [])) if have_f else set()
        self.m_missing = set(fidx.get("months_missing", []))
        self.ns, self.yesterday, self.now_ms = ns, yesterday, now_ms
        self.dup_conflicts = int(idx.get("dup_conflicts", 0)) if have_csv else 0
        self.frames: list[tuple[int, pd.DataFrame]] = []           # (gün, satırlar): birleştirme gün sırasıyla
        self.f_frames: list[tuple[str, pd.DataFrame]] = []
        self.bad_time_files = 0
        self.err_days: set[int] = set()
        self.recent404: set[int] = set()
        self.f_err: set[str] = set()
        self.f_recent404: set[str] = set()
        self.pending = 0
        self.status = ""
        self.headerless = False

    def need_search(self) -> bool:
        if self.first_day is None:
            return True
        # ilk gün aramanın alt ucuyla aynıysa daha eskisi de olabilir: pencere geriye uzadıysa yeniden ara
        return self.first_day <= (self.search_from or self.first_day) and self.ns < self.first_day

    def window_days(self) -> list[int]:
        if self.first_day is None:
            return []
        d, out = max(self.first_day, self.ns), []
        while d <= self.yesterday:
            out.append(d)
            d += _DAY
        return out

    def add_day(self, day: int, status: str, val: Any) -> None:
        if status == "ok":
            self.ok.add(day)
            self.missing.discard(day)
            self.zero_rows += int(val.attrs.get("zero_rows", 0))
            self.headerless |= bool(val.attrs.get("headerless"))
            self.frames.append((day, val))
        elif status == "404":
            if day < self.now_ms - METRICS_FINAL_404_DAYS * _DAY:
                self.missing.add(day)
            else:
                self.recent404.add(day)
        else:
            self.err_days.add(day)
            self.bad_time_files += isinstance(val, str) and val.startswith(BAD_TIME)

    def add_month(self, month: str, status: str, val: Any) -> None:
        if status == "ok":
            self.m_ok.add(month)
            self.m_missing.discard(month)
            self.f_frames.append((month, val))
        elif status == "404":
            if _day_ms(month + "-01") < L._month_start(self.now_ms - FUNDING_FINAL_404_DAYS * _DAY):
                self.m_missing.add(month)
            else:
                self.f_recent404.add(month)
        else:
            self.f_err.add(month)
            self.bad_time_files += isinstance(val, str) and val.startswith(BAD_TIME)

    def flush(self) -> None:
        """Yeni satırlar + dizin diske (yalnız alınan/kesinleşen günler; `error` hiç yazılmaz)."""
        self.dup_conflicts += _merge_csv(self.paths["oi"], self.frames, ["t_ms", "oi"], _own_day)
        if self.first_day is not None:
            idx = {"first_day": _day_str(self.first_day), "search_from": _day_str(self.search_from or self.first_day),
                   "days_ok": _ranges(self.ok if self.paths["oi"].exists() else set()),
                   "days_missing": sorted(_day_str(d) for d in self.missing), "zero_rows": int(self.zero_rows),
                   "dup_conflicts": int(self.dup_conflicts), "fetched_ms": int(self.now_ms)}
            _write_atomic(self.paths["oi_idx"], lambda t: t.write_text(json.dumps(idx), encoding="utf-8"))
        _merge_csv(self.paths["f"], self.f_frames, ["t_ms", "rate", "interval_file"])
        if self.m_ok or self.m_missing:
            fidx = {"months_ok": sorted(self.m_ok if self.paths["f"].exists() else set()), "months_missing": sorted(self.m_missing)}
            _write_atomic(self.paths["f_idx"], lambda t: t.write_text(json.dumps(fidx), encoding="utf-8"))
        self.frames, self.f_frames = [], []

    def coverage(self) -> dict[str, Any]:
        win = self.window_days()
        ok = [d for d in win if d in self.ok]
        miss = [d for d in win if d in self.missing or d in self.recent404]
        err = [d for d in win if d in self.err_days]
        gaps = _ranges({d for d in win if d not in self.ok})
        status = self.status or ("NO_DATA" if self.first_day is None else
                                 "PARTIAL_ERROR" if win and len(err) > PARTIAL_ERROR_SHARE * len(win) else "OK")
        months = sorted(self.m_ok)
        return {"sym": self.sym, "first_day": _day_str(self.first_day) if self.first_day is not None else None,
                "last_day": _day_str(max(ok)) if ok else None, "n_days": len(win), "n_ok": len(ok), "n_missing": len(miss),
                "n_error": len(err), "status": status, "first_funding": months[0] if months else None,
                "last_funding": months[-1] if months else None, "n_funding_months": len(months),
                "n_funding_error": len(self.f_err), "zero_rows": int(self.zero_rows), "n_bad_time": int(self.bad_time_files),
                "dup_conflicts": int(self.dup_conflicts), "gaps": gaps[:10], "n_gaps": len(gaps)}


def _search(fetch, sym: str, lo_ms: int, hi_ms: int | None, yesterday: int) -> tuple[int | None, list[tuple[int, str, Any, int]], str]:
    """İlk 200 gün: önce `lo_ms` (200 → tek istek); değilse (lo, hi] ikili arama (hi bilinen 200 gün ya da dün+1 nöbetçi).
    Döner: (ilk gün | None, [(gün, durum, veri, bayt)], hata metni). `error` veren orta gün yerine aralıktaki komşu
    günler denenir (en çok 3 gün); hepsi `error` ise arama kesilir (ilk gün uydurulmaz)."""
    got: list[tuple[int, str, Any, int]] = []

    def probe(d: int) -> str:
        st, val, nb = _get(fetch, METRICS_URL.format(sym=sym, day=_day_str(d)), parse_metrics_zip)
        got.append((d, st, val, nb))
        return st

    st = probe(lo_ms)
    if st == "ok":
        return lo_ms, got, ""
    if st == "error":
        return None, got, str(got[-1][2])
    lo, hi = lo_ms, (hi_ms if hi_ms is not None else yesterday + _DAY)
    while hi - lo > _DAY:
        mid = lo + ((hi - lo) // _DAY // 2) * _DAY
        for d in (mid, mid + _DAY, mid - _DAY):
            if lo < d < hi and probe(d) != "error":
                break
        else:
            return None, got, str(got[-1][2])
        lo, hi = (lo, d) if got[-1][1] == "ok" else (d, hi)
    return (hi if hi <= yesterday else None), got, ""


def ensure_futures(symbols, cache_dir, need_start_ms: int, now_ms: int, *, fetch: Callable[[str], bytes | None] | None = None,
                   threads: int | None = None, log: Callable[[str], None] = print, offline: bool = False) -> dict[str, dict]:
    """Ana süreç, mum döngüsünden sonra: eksik OI günlerini ve fonlama aylarını indirir, önbelleğe yazar.
    Döner {sembol (verildiği gibi): kapsama}; her sembol için `FUT_COV` satırı yazar. `offline` → yalnız önbellek.
    Bağlantı yoksa `FuturesDataUnavailable` (inen veri yine de önbelleğe yazılır)."""
    fetch = fetch or L._http_get
    threads = int(threads or os.environ.get("SIGNAL_LAB_FUTURES_THREADS", DEFAULT_THREADS))
    now_ms = int(now_ms)
    today = _floor_day(now_ms)
    yesterday = today - _DAY
    ns = min(_floor_day(need_start_ms), yesterday)
    this_month = L._month_start(now_ms)
    states = {s: _Sym(s, cache_dir, ns, yesterday, now_ms) for s in symbols}
    stats = {"requests": 0, "errors": 0, "content_errors": 0, "consecutive": 0, "bytes": 0, "ok": 0, "404": 0, "t0": time.time()}
    LAST_RUN_STATS.clear()

    def count(st: str, nb: int, val: Any = None) -> None:
        stats["requests"] += 1
        stats["content_errors"] += st == "error" and isinstance(val, str) and val.startswith((CORRUPT, BAD_TIME))
        stats["bytes"] += nb
        stats[st if st != "error" else "errors"] += 1
        stats["consecutive"] = stats["consecutive"] + 1 if st == "error" else 0
        n = stats["requests"]
        if n % PROGRESS_EVERY == 0:
            rate = n / max(time.time() - stats["t0"], 1e-9)
            log(f"vadeli veri: {n}/{stats['total']} dosya · {rate:.0f} dosya/sn · hata {stats['errors']}")
        if stats["consecutive"] >= ABORT_CONSECUTIVE:
            raise _abort(f"art arda {stats['consecutive']} vadeli arşiv dosyası indirilemedi")

    def _abort(why: str) -> FuturesDataUnavailable:
        return FuturesDataUnavailable(
            f"Vadeli veri (OI/fonlama) indirilemedi: {why} ({stats['errors']}/{stats['requests']} istek hatalı). Bağlantı ya da "
            "Binance tarafında geçici kısıtlama olabilir; 10-15 dk sonra tekrar deneyin (inen veri önbellekte kalır). "
            "Test ÇALIŞTIRILMADI.")

    try:
        if not offline:
            with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
                # 1) ilk gün araması (sembol başına sıralı, semboller paralel)
                searching = {ex.submit(_search, fetch, st.sym, ns, st.first_day if st.first_day is not None and ns < st.first_day
                                       else None, yesterday): st for st in states.values() if st.need_search()}
                stats["total"] = len(searching)
                for fut in as_completed(searching):   # sayaçları yalnız ana iş parçacığı günceller
                    st = searching[fut]
                    first, got, err = fut.result()
                    stats["total"] += len(got) - 1
                    for d, s_, val, nb in got:
                        if first is not None and d >= first:
                            st.add_day(d, s_, val)
                        count(s_, nb, val)
                    if first is not None:
                        st.first_day, st.search_from = first, ns
                    elif err:
                        st.status = "SEARCH_ERROR" if st.first_day is None else ""
                        log(f"vadeli veri {st.sym}: ilk gün aranamadı ({err})")
                # 2) eksik günler + bitmiş aylar
                jobs: dict[Any, tuple[_Sym, str, Any]] = {}
                todo: list[tuple[_Sym, str, Any, str, Callable]] = []
                for st in states.values():
                    for d in st.window_days():
                        if d not in st.ok and d not in st.missing and d not in st.recent404 and d not in st.err_days:
                            todo.append((st, "d", d, METRICS_URL.format(sym=st.sym, day=_day_str(d)), parse_metrics_zip))
                    m = L._month_start(ns)
                    while m < this_month:
                        mo = _month_str(m)
                        if mo not in st.m_ok and mo not in st.m_missing:
                            todo.append((st, "m", mo, FUNDING_URL.format(sym=st.sym, month=mo), parse_funding_zip))
                        m = L._next_month(m)
                stats["total"] = stats["requests"] + len(todo)
                for st, kind, key, url, parser in todo:
                    st.pending += 1
                    jobs[ex.submit(_get, fetch, url, parser)] = (st, kind, key)
                try:
                    for fut in as_completed(jobs):
                        st, kind, key = jobs[fut]
                        s_, val, nb = fut.result()
                        n_bad = st.bad_time_files
                        (st.add_day if kind == "d" else st.add_month)(key, s_, val)
                        if n_bad == 0 and st.bad_time_files == 1:           # sembol başına ilk okunamayan zaman
                            log(f"vadeli veri {st.sym}: {val} — dosya `error` sayıldı, önbelleğe yazılmadı")
                        st.pending -= 1
                        count(s_, nb, val)
                        if st.pending == 0:
                            st.flush()
                except BaseException:
                    for f in jobs:
                        f.cancel()
                    raise
            n, e = stats["requests"], stats["errors"]
            # %20 kuralı en az 50 istekte (günlük artımlı koşuda tek hatalı dosya laboratuvarı durdurmasın); daha az
            # istekte hepsi hatalıysa ("ilk 50 isteğin hepsi") yine durur — ama yalnız ağ hatası da varsa: aynı gün
            # yeniden koşuda istenen tek dosya kalıcı bozuk/okunamayan dosyaysa (indirildi, ağ çalışıyor) durdurmaz
            if n and ((n >= ABORT_CONSECUTIVE and e > ABORT_ERROR_SHARE * n) or (e == n and e > stats["content_errors"])):
                raise _abort(f"isteklerin %{100 * stats['errors'] / stats['requests']:.0f}'i hatalı (sınır %{100 * ABORT_ERROR_SHARE:.0f})")
    finally:
        for st in states.values():
            if st.headerless:
                log(f"vadeli veri {st.sym}: başlıksız metrics dosyası konumdan okundu (sütun 0 zaman, sütun 2 OI)")
            st.flush()
        secs = time.time() - stats["t0"]
        LAST_RUN_STATS.update(files=stats["requests"], ok=stats["ok"], not_found=stats["404"], errors=stats["errors"],
                              mb=round(stats["bytes"] / 1e6, 2), seconds=round(secs, 1),
                              files_per_s=round(stats["requests"] / secs, 1) if secs > 0 else None, threads=threads)
    out = {}
    for s, st in states.items():
        if offline and not st.paths["oi"].exists():
            st.status = "NO_CACHE"
        out[s] = st.coverage()
        log(log_line("FUT_COV", out[s]))
    return out


# ---------------------------------------------------------------------------- doğrulama örneklemi ve yoklama (§8)
def checksum_sample(urls, fetch: Callable[[str], bytes | None], log: Callable[[str], None] = print) -> dict[str, Any]:
    """Her dosyanın sha256'sı `<url>.CHECKSUM` ile karşılaştırılır. Uyuşmazlık yalnız UYARI (koşu durmaz)."""
    ok, compared, mismatch, missing = 0, 0, [], []
    for url in urls:
        try:
            data, ck = fetch(url), fetch(url + ".CHECKSUM")
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{url.rsplit('/', 1)[-1]}: {type(exc).__name__}")
            continue
        if data is None or ck is None:
            missing.append(url.rsplit("/", 1)[-1])
            continue
        want = (ck.decode("utf-8", "replace").split() or [""])[0].lower()
        compared += 1
        if hashlib.sha256(data).hexdigest() == want:
            ok += 1
        else:
            mismatch.append(url.rsplit("/", 1)[-1])
            log(f"UYARI: checksum uyuşmazlığı {url.rsplit('/', 1)[-1]} (yalnız uyarı)")
    return {"text": f"checksum örneklem {ok}/{compared}", "ok": ok, "n": compared, "mismatch": mismatch, "missing": missing,
            "warning": bool(mismatch)}


def _raw_head(data: bytes, n: int = 3) -> list[str]:
    return _zip_text(data).splitlines()[:n]


def _time_format(cell: str) -> str:
    cell = cell.strip().strip('"')
    if re.fullmatch(r"\d+", cell):
        v = int(cell)
        return "rakam_us" if v > 10 ** 14 else "rakam_sn" if v < 10 ** 11 else "rakam_ms"
    return "dize" if not _is_header(cell) else "başlık"


def _metrics_stats(oi: pd.DataFrame) -> dict[str, Any]:
    t = oi["t_ms"].to_numpy(dtype=np.int64)
    v = oi["oi"].to_numpy(dtype=float)
    if not len(t):
        return {"rows": 0}
    per_day = pd.Series(t // _DAY).value_counts()
    first_min = pd.Series(t).groupby(t // _DAY).min() % 3_600_000 // 60_000
    step5 = np.diff(t) == 300_000
    jump = np.abs(v[1:] / v[:-1] - 1.0) > 0.5
    return {"rows": int(len(t)), "rows_per_day": [int(per_day.min()), float(per_day.median()), int(per_day.max())],
            "grid5m_share": round(float((t % 300_000 == 0).mean()), 4),
            "first_minute": {str(k): int(c) for k, c in first_min.value_counts().sort_index().head(6).items()},
            "jumps_gt50pct": int((jump & step5).sum())}


def _funding_stats(f: pd.DataFrame) -> dict[str, Any]:
    t = f["t_ms"].to_numpy(dtype=np.int64)
    if not len(t):
        return {"rows": 0}
    jit = t % 60_000
    h = np.round(np.diff(t) / 3.6e6)
    inf = np.where(np.isin(h, (1, 2, 4, 8)), h, np.nan)
    hist = {"1": 0, "2": 0, "4": 0, "8": 0, "NaN": int(np.isnan(inf).sum())}
    for k in (1, 2, 4, 8):
        hist[str(k)] = int((inf == k).sum())
    iv = f["interval_file"].to_numpy(dtype=float)[1:] if "interval_file" in f else np.full(len(inf), np.nan)
    both = np.isfinite(inf) & np.isfinite(iv)
    return {"rows": int(len(t)), "calc_mod60s": {"0": int((jit == 0).sum()), "<1s": int(((jit > 0) & (jit < 1000)).sum()),
                                                 "<10s": int(((jit >= 1000) & (jit < 10_000)).sum()), "≥10s": int((jit >= 10_000).sum())},
            "interval_h": hist, "file_interval_agree": round(float((inf[both] == iv[both]).mean()), 4) if both.any() else None,
            "file_interval_rows": int(both.sum())}


def probe(symbols, cache_dir, now_ms: int, *, fetch: Callable[[str], bytes | None] | None = None,
          log: Callable[[str], None] = print, coverage: dict[str, dict] | None = None) -> dict[str, Any]:
    """Çalışma anı kapsama yoklaması P1–P7 (§8; P8 kör sayımları laboratuvar yapar). `ensure_futures`'tan SONRA çağrılır:
    P1–P3 önbellekten, ham satırlar ve P4–P6 birkaç ek istekle. Her madde `FUT_PROBE` satırı (sembollü maddeler sembol
    başına). Sonuç kararı değiştirmez: şema çelişkisi yalnız bu dosyanın ayrıştırmasını değiştirebilir (kayıt sha'sı değil)."""
    fetch = fetch or L._http_get
    now_ms = int(now_ms)
    yesterday = _floor_day(now_ms) - _DAY
    out: dict[str, Any] = {}
    syms = [_sym(s) for s in symbols]
    idx = {s: _read_json(_paths(cache_dir, s)["oi_idx"]) for s in syms}
    fidx = {s: _read_json(_paths(cache_dir, s)["f_idx"]) for s in syms}

    def emit(item: str, obj: dict) -> None:
        log(log_line("FUT_PROBE", {"item": item, **obj}))

    def raw(url: str) -> tuple[str, list[str]]:
        try:
            data = fetch(url)
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}", []
        return ("404", []) if data is None else ("ok", _raw_head(data))

    # P1 — kapsama
    p1 = {}
    for s, orig in zip(syms, symbols):
        cov = (coverage or {}).get(orig) or (coverage or {}).get(s)
        if cov is None:
            ok = _unranges(idx[s].get("days_ok"))
            gaps = _ranges({d for d in range(min(ok), max(ok) + _DAY, _DAY) if d not in ok}) if ok else []
            cov = {"sym": s, "first_day": idx[s].get("first_day"), "last_day": _day_str(max(ok)) if ok else None, "n_ok": len(ok),
                   "n_missing": len(idx[s].get("days_missing", [])), "n_error": None, "gaps": gaps[:10],
                   "first_funding": min(fidx[s].get("months_ok") or [None]), "last_funding": max(fidx[s].get("months_ok") or [None])}
        p1[s] = {k: cov.get(k) for k in ("first_day", "last_day", "n_ok", "n_missing", "n_error", "gaps", "first_funding", "last_funding")}
        emit("P1", {"sym": s, **p1[s]})
    out["P1"] = p1

    # P2 — metrics şeması: ilk sembolün 3 döneminden ham satırlar + önbellekten satır istatistikleri
    ref = next((s for s in syms if idx[s].get("first_day")), None)
    p2: dict[str, Any] = {"raw": {}, "per_symbol": {}}
    if ref:
        eras = {"ilk_gün": idx[ref]["first_day"], "2023-06-01": "2023-06-01", "dün": _day_str(yesterday)}
        for name, day in eras.items():
            if _day_ms(day) < _day_ms(idx[ref]["first_day"]):
                continue
            st, lines = raw(METRICS_URL.format(sym=ref, day=day))
            fmt = next((_time_format(ln.split(",")[0]) for ln in lines if ln and _time_format(ln.split(",")[0]) != "başlık"), None)
            dup = len(lines) - len(set(lines)) if lines else 0
            p2["raw"][name] = {"day": day, "status": st, "lines": lines, "create_time": fmt, "dup_in_head": dup}
        emit("P2", {"sym": ref, "raw": p2["raw"]})
    for s in syms:
        p = _paths(cache_dir, s)["oi"]
        if p.exists():
            p2["per_symbol"][s] = {**_metrics_stats(pd.read_csv(p)), "zero_rows": int(idx[s].get("zero_rows", 0)),
                                   "dup_conflicts": int(idx[s].get("dup_conflicts", 0))}
            emit("P2", {"sym": s, **p2["per_symbol"][s]})
    out["P2"] = p2

    # P3 — fonlama şeması
    p3: dict[str, Any] = {"raw": {}, "per_symbol": {}}
    fref = next((s for s in syms if fidx[s].get("months_ok")), None)
    if fref:
        months = sorted(fidx[fref]["months_ok"])
        for name, m in (("ilk_ay", months[0]), ("son_ay", months[-1])):
            st, lines = raw(FUNDING_URL.format(sym=fref, month=m))
            fmt = next((_time_format(ln.split(",")[0]) for ln in lines if ln and _time_format(ln.split(",")[0]) != "başlık"), None)
            p3["raw"][name] = {"month": m, "status": st, "lines": lines, "calc_time": fmt}
        emit("P3", {"sym": fref, "raw": p3["raw"]})
    for s in syms:
        p = _paths(cache_dir, s)["f"]
        if p.exists():
            p3["per_symbol"][s] = _funding_stats(pd.read_csv(p))
            emit("P3", {"sym": s, **p3["per_symbol"][s]})
    out["P3"] = p3

    # P4 — olmayan anahtar 404 mü 403 mü? (403 `error` sayılır ve durdurmayı tetikleyebilir)
    miss_url = METRICS_URL.format(sym=ref or (syms[0] if syms else "BTCUSDT"), day="2019-01-01")
    try:
        p4 = {"url": miss_url.split("/futures/um/")[-1], "status": "404" if fetch(miss_url) is None else "200"}
    except Exception as exc:  # noqa: BLE001
        p4 = {"url": miss_url.split("/futures/um/")[-1], "status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:200]}"}
    emit("P4", p4)
    out["P4"] = p4

    # P5 — checksum örneklemi: sembol başına ilk + son metrics günü ve son fonlama ayı
    urls = []
    for s in syms:
        ok = _unranges(idx[s].get("days_ok"))
        if ok:
            urls += [METRICS_URL.format(sym=s, day=_day_str(min(ok))), METRICS_URL.format(sym=s, day=_day_str(max(ok)))]
        if fidx[s].get("months_ok"):
            urls.append(FUNDING_URL.format(sym=s, month=max(fidx[s]["months_ok"])))
    p5 = checksum_sample(urls, fetch, log)
    emit("P5", p5)
    out["P5"] = p5

    # P6 — günlük fonlama dosyası var mı? (bilgi; kullanmak fut_v2)
    s6 = ref or (syms[0] if syms else "BTCUSDT")
    st, lines = raw(FUNDING_DAILY_URL.format(sym=s6, day=_day_str(yesterday)))
    p6 = {"sym": s6, "day": _day_str(yesterday), "status": st, "lines": lines[:2]}
    emit("P6", p6)
    out["P6"] = p6

    # P7 — hız ve disk
    size = sum(p.stat().st_size for p in _dir(cache_dir).glob("*") if p.is_file()) if _dir(cache_dir).exists() else 0
    p7 = {**LAST_RUN_STATS, "cache_mb": round(size / 1e6, 2)}
    emit("P7", p7)
    out["P7"] = p7
    return out


__all__ = ["METRICS_URL", "FUNDING_URL", "FUNDING_DAILY_URL", "WARMUP_DAYS", "FuturesDataUnavailable", "parse_metrics_zip",
           "parse_funding_zip", "ensure_futures", "read_futures_cache", "probe", "checksum_sample", "need_start", "log_line",
           "LAST_RUN_STATS"]

