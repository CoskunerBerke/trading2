# -*- coding: utf-8 -*-
"""KALABALIK VERİSİ (fut_v2) — sinyal laboratuvarı için arşiv G/Ç'si (2026-09-30). Canlı kod bunu İÇE AKTARMAZ.

* Kaynak yalnız data.binance.vision arşivi (REST yok; GitHub koşucularında fapi 451):
  - `daily/metrics` gün dosyalarının TAM ayrıştırması (`parse_metrics_full_zip`): [t_ms, oi, tals, tpls, gls, tkls].
    Başlıktan okunur; başlıksız dosyada konumdan (0, 2, 4, 5, 6, 7). Sütun başına sonlu olmayan ya da ≤ 0 değer NaN
    olur, satır ATILMAZ. Zamanı okunamayan satır dosyayı `error` yapar (fut_v1 ile aynı; önbelleğe girmez).
  - Taker mumları `{monthly|daily}/klines/{sym}/{tf}/…zip` (1h ve 4h): sütun 0 açılış, 5 hacim, 7 quote hacim, 8 işlem
    sayısı, 9 taker alış hacmi; µs → ms. Bitmiş aylar ay dosyasından; içinde bulunulan ay (ve ay dosyası henüz
    yayımlanmamış yakın ay) gün dosyalarından.
  - Fonlama fut_v1'in önbelleğinden okunur (`futures_data.ensure_futures` günceller; bu modül ona YAZMAZ).
* Önbellek `signal_lab_data/crowd/`: `{SYM}_metrics5m.csv.gz` + dizin, `{SYM}_{tf}_taker.csv.gz` + dizin. fut_v1'in
  `futures/{SYM}_oi5m` önbelleğine DOKUNULMAZ (ilk gün yalnız okunur: `oi5m.json` varsa oradan, yoksa ikili arama).
* İndirme/durdurma kuralları fut_v1 ile aynı (`futures_data` yardımcıları yeniden kullanılır): art arda 50 `error` ya
  da isteklerin %20'sinden fazlası `error` → `CrowdDataUnavailable` (laboratuvar ÇALIŞMAZ; inen veri önbellekte kalır).
* Laboratuvar birleşimi (`join_taker`): taker satırı lab'ın kendi mum önbelleğine zaman damgasıyla bağlanır; hacim
  uyuşmazsa (göreli fark > VOL_AGREE_RTOL) o barın taker'ı NaN.

Günlük satırları `CROWD_COV` / `CROWD_PROBE` (her biri ≤ 4 KB; `futures_data.log_line`).
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import crowd_features as CF
from . import futures_data as FD
from . import futures_features as FF
from . import signal_lab as L

_DAY = L._DAY
SUBDIR = "crowd"
CROWD_TFS = ("1h", "4h")
METRICS_URL = FD.METRICS_URL
KLINES_MONTHLY_URL = L.ARCHIVE_BASE + "/monthly/klines/{sym}/{tf}/{sym}-{tf}-{month}.zip"
KLINES_DAILY_URL = L.ARCHIVE_BASE + "/daily/klines/{sym}/{tf}/{sym}-{tf}-{day}.zip"
WARMUP_DAYS = FD.WARMUP_DAYS
TAKER_FINAL_404_DAYS = FD.FUNDING_FINAL_404_DAYS      # _month_start(now − 40 g) öncesindeki 404 ay kesin eksik
DAILY_FINAL_404_DAYS = FD.METRICS_FINAL_404_DAYS      # now − 3 g öncesindeki 404 gün kesin eksik
VOL_AGREE_RTOL = 1e-9                                  # taker dosyası hacmi ↔ lab mum hacmi (göreli)

METRIC_COLS = ["t_ms", *CF.METRIC_FIELDS]
#: arşiv başlığı → alan (sum_open_interest_value fiyatla şiştiği için kullanılmaz)
METRICS_HEADER = {"oi": "sum_open_interest", "tals": "count_toptrader_long_short_ratio",
                  "tpls": "sum_toptrader_long_short_ratio", "gls": "count_long_short_ratio",
                  "tkls": "sum_taker_long_short_vol_ratio"}
METRICS_POSITIONS = {"t_ms": 0, "oi": 2, "tals": 4, "tpls": 5, "gls": 6, "tkls": 7}   # başlıksız dosya
TAKER_COLS = ["t_ms", "v", "qv", "n", "tb"]
TAKER_HEADER = {"t_ms": "open_time", "v": "volume", "qv": "quote_volume", "n": "count", "tb": "taker_buy_volume"}
TAKER_POSITIONS = {"t_ms": 0, "v": 5, "qv": 7, "n": 8, "tb": 9}
COLUMN_MAP: dict[str, Any] = {
    "metrics_header": METRICS_HEADER, "metrics_time": "create_time", "metrics_positions": METRICS_POSITIONS,
    "klines_header": TAKER_HEADER, "klines_positions": TAKER_POSITIONS,
    "rest_tr": "canlı eşleme (VPS yoklamasıyla doğrulanacak): openInterestHist.sumOpenInterest = sum_open_interest; "
               "globalLongShortAccountRatio.longShortRatio = count_long_short_ratio; topLongShortPositionRatio = "
               "sum_toptrader_long_short_ratio; topLongShortAccountRatio = count_toptrader_long_short_ratio; "
               "takerlongshortRatio.buySellRatio = sum_taker_long_short_vol_ratio; klines[9] = taker_buy_base",
}

#: son `ensure_crowd` koşusunun sayaçları (P7 hız yoklaması okur)
LAST_RUN_STATS: dict[str, Any] = {}


class CrowdDataUnavailable(L.DownloadAborted):
    """Kalabalık arşivi (metrics / taker mumları) indirilemedi: laboratuvar eksik veriyle ÇALIŞTIRILMAZ."""


# ---------------------------------------------------------------------------- ayrıştırma
def _num(df: pd.DataFrame, col: int | None, n: int) -> np.ndarray:
    if col is None:
        return np.full(n, np.nan)
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float) if n else np.array([], dtype=float)


def _empty(cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: np.array([], dtype=np.int64 if c == "t_ms" else float) for c in cols})


def _dups(frame: pd.DataFrame, cols: list[str]) -> int:
    """Dosya içinde aynı t_ms'e düşen FARKLI değerli satır grubu sayısı (P6 teşhisi)."""
    d = frame[frame.duplicated("t_ms", keep=False)]
    return int((d.drop_duplicates(cols).groupby("t_ms").size() > 1).sum()) if len(d) else 0


def parse_metrics_full_zip(data: bytes) -> pd.DataFrame:
    """`daily/metrics` zip → [t_ms:int64, oi, tals, tpls, gls, tkls] (float; ≤ 0 / okunamayan → NaN, satır kalır).
    Başlıkta `create_time` ve `sum_open_interest` ZORUNLU (yoksa ValueError, sessiz NaN yok); oran sütunu başlıkta yoksa
    o sütun NaN ve `attrs["missing_cols"]`'ta. Başlıksız: konum 0, 2, 4, 5, 6, 7 (sütun yoksa NaN). `attrs`: bad_time,
    headerless, missing_cols, nan_cells {alan: sayı}, dup_in_file."""
    header, df = FD._read_rows(data)
    if header is None and df.empty:
        out = _empty(METRIC_COLS)
        out.attrs.update(bad_time=0, headerless=False, missing_cols=[], nan_cells={}, dup_in_file=0)
        return out
    idx: dict[str, int | None] = {}
    if header is not None:
        low = [h.lower() for h in header]
        ti = FD._col(header, FD.METRICS_TIME_COLS, "create_time", "metrics")
        idx["oi"] = FD._col(header, FD.METRICS_OI_COLS, "sum_open_interest", "metrics")
        for f in CF.METRIC_FIELDS[1:]:
            idx[f] = low.index(METRICS_HEADER[f]) if METRICS_HEADER[f] in low else None
    else:
        if df.shape[1] < 3:
            raise ValueError(f"metrics dosyası başlıksız ve {df.shape[1]} sütunlu (en az 3 gerekir)")
        ti = METRICS_POSITIONS["t_ms"]
        idx = {f: (METRICS_POSITIONS[f] if METRICS_POSITIONS[f] < df.shape[1] else None) for f in CF.METRIC_FIELDS}
    n = len(df)
    t = FD._to_ms(df[ti]) if n else np.array([], dtype=float)
    vals, nan_cells = {}, {}
    for f in CF.METRIC_FIELDS:
        x = _num(df, idx[f], n)
        with np.errstate(invalid="ignore"):
            good = np.isfinite(x) & (x > 0)
        vals[f] = np.where(good, x, np.nan)
        nan_cells[f] = int((~good).sum())
    good_t = np.isfinite(t)
    frame = pd.DataFrame({"t_ms": t[good_t], **{f: vals[f][good_t] for f in CF.METRIC_FIELDS}})
    dup = _dups(frame, METRIC_COLS)
    out = FD._finish(frame)
    out.attrs.update(bad_time=int((~good_t).sum()), headerless=header is None,
                     missing_cols=[f for f in CF.METRIC_FIELDS if idx[f] is None], nan_cells=nan_cells, dup_in_file=dup)
    return out


def parse_taker_zip(data: bytes) -> pd.DataFrame:
    """Mum (klines) zip → [t_ms:int64, v, qv, n, tb] (float; okunamayan NaN, satır kalır). µs → ms. Başlık varsa
    `open_time`, `volume`, `quote_volume`, `count`, `taker_buy_volume` ZORUNLU; başlıksız en az 10 sütun (yoksa ValueError).
    `attrs`: bad_time, headerless, dup_in_file."""
    header, df = FD._read_rows(data)
    if header is None and df.empty:
        out = _empty(TAKER_COLS)
        out.attrs.update(bad_time=0, headerless=False, dup_in_file=0)
        return out
    if header is not None:
        idx = {c: FD._col(header, (name,), name, "klines") for c, name in TAKER_HEADER.items()}
    else:
        if df.shape[1] < 10:
            raise ValueError(f"klines dosyası başlıksız ve {df.shape[1]} sütunlu (en az 10 gerekir)")
        idx = dict(TAKER_POSITIONS)
    n = len(df)
    t = FD._to_ms(df[idx["t_ms"]]) if n else np.array([], dtype=float)
    good_t = np.isfinite(t)
    frame = pd.DataFrame({"t_ms": t[good_t], **{c: _num(df, idx[c], n)[good_t] for c in TAKER_COLS[1:]}})
    dup = _dups(frame, TAKER_COLS)
    out = FD._finish(frame)
    out.attrs.update(bad_time=int((~good_t).sum()), headerless=header is None, dup_in_file=dup)
    return out


# ---------------------------------------------------------------------------- önbellek
def _dir(cache_dir) -> Path:
    return Path(cache_dir) / SUBDIR


def _paths(cache_dir, sym: str) -> dict[str, Path]:
    d = _dir(cache_dir)
    return {"m": d / f"{sym}_metrics5m.csv.gz", "m_idx": d / f"{sym}_metrics5m.json"}


def _taker_paths(cache_dir, sym: str, tf: str) -> tuple[Path, Path]:
    d = _dir(cache_dir)
    return d / f"{sym}_{tf}_taker.csv.gz", d / f"{sym}_{tf}_taker.json"


def read_crowd_cache(cache_dir, symbol: str, tf: str) -> dict[str, np.ndarray] | None:
    """İşçi tarafı (ağ YOK): metrics {"m_t", "oi", "tals", "tpls", "gls", "tkls"}, taker {"k_t", "k_v", "k_tb"} (bu dilim)
    ve fonlama {"f_t", "f_rate"} (fut_v1 önbelleği). Metrics önbelleği yoksa None; taker/fonlama yoksa boş dizi."""
    sym = FD._sym(symbol)
    p = _paths(cache_dir, sym)
    if not p["m"].exists():
        return None
    m = pd.read_csv(p["m"])
    kp, _ = _taker_paths(cache_dir, sym, tf)
    k = pd.read_csv(kp) if kp.exists() else _empty(TAKER_COLS)
    fp = FD._paths(cache_dir, sym)["f"]
    f = pd.read_csv(fp) if fp.exists() else pd.DataFrame({"t_ms": [], "rate": []})
    out = {"m_t": m["t_ms"].to_numpy(dtype=np.int64)}
    for c in CF.METRIC_FIELDS:
        out[c] = m[c].to_numpy(dtype=np.float64)
    out.update(k_t=k["t_ms"].to_numpy(dtype=np.int64), k_v=k["v"].to_numpy(dtype=np.float64),
               k_tb=k["tb"].to_numpy(dtype=np.float64), f_t=f["t_ms"].to_numpy(dtype=np.int64),
               f_rate=f["rate"].to_numpy(dtype=np.float64))
    return out


def join_taker(ts: Any, v: Any, k_t: Any, k_v: Any, k_tb: Any) -> tuple[np.ndarray, dict[str, int]]:
    """Lab barlarına (ts, v) taker alış hacmi: aynı açılış zamanlı satır ve hacim uyuşuyorsa (|Δv| ≤ RTOL·max(|v|, |v_k|))
    `tb`, aksi NaN. Döner (tb, {"bars", "found", "vol_mismatch"})."""
    ts = np.asarray(ts, dtype=np.int64)
    v = np.asarray(v, dtype=float)
    kt, kv, ktb = FF._sorted(np.asarray(k_t, dtype=np.int64), np.asarray(k_v, dtype=float), np.asarray(k_tb, dtype=float))
    out = np.full(ts.size, np.nan)
    stats = {"bars": int(ts.size), "found": 0, "vol_mismatch": 0}
    if kt.size == 0 or ts.size == 0:
        return out, stats
    j = np.searchsorted(kt, ts, side="left")
    jj = np.minimum(j, kt.size - 1)
    found = (j < kt.size) & (kt[jj] == ts)
    kvv = kv[jj]
    with np.errstate(invalid="ignore"):
        agree = found & np.isfinite(kvv) & np.isfinite(v) & (np.abs(kvv - v) <= VOL_AGREE_RTOL * np.maximum(np.abs(v), np.abs(kvv)))
    out[agree] = ktb[jj][agree]
    stats.update(found=int(found.sum()), vol_mismatch=int((found & ~agree).sum()))
    return out, stats


# ---------------------------------------------------------------------------- indirme
class _Taker:
    """Bir sembol × dilimin taker mumu durumu (yalnız ana iş parçacığı yazar)."""

    def __init__(self, cache_dir, sym: str, tf: str, start_ms: int, now_ms: int):
        self.sym, self.tf = sym, tf
        self.path, self.idx_path = _taker_paths(cache_dir, sym, tf)
        idx = FD._read_json(self.idx_path)
        have = self.path.exists()
        self.m_ok = set(idx.get("months_ok", [])) if have else set()
        self.m_missing = set(idx.get("months_missing", []))
        self.d_ok = FD._unranges(idx.get("days_ok")) if have else set()
        self.d_missing = {FD._day_ms(d) for d in idx.get("days_missing", [])}
        self.start, self.now_ms = FD._floor_day(start_ms), int(now_ms)
        self.m_err: set[str] = set()
        self.m_recent404: set[str] = set()
        self.d_err: set[int] = set()
        self.d_recent404: set[int] = set()
        self.frames: list[tuple[str, pd.DataFrame]] = []
        self.headerless = False
        self.bad_time_files = 0

    def months(self, this_month: int) -> list[str]:
        out, m = [], L._month_start(self.start)
        while m < this_month:
            out.append(FD._month_str(m))
            m = L._next_month(m)
        return out

    def days_of(self, month_ms: int, today: int) -> list[int]:
        d, out = max(month_ms, self.start), []
        while d < min(L._next_month(month_ms), today):
            out.append(d)
            d += _DAY
        return out

    def add(self, kind: str, key: Any, status: str, val: Any) -> None:
        if status == "ok":
            self.headerless |= bool(val.attrs.get("headerless"))
            self.frames.append((key if kind == "m" else FD._day_str(key), val))
            if kind == "m":
                self.m_ok.add(key)
                self.m_missing.discard(key)
            else:
                self.d_ok.add(key)
                self.d_missing.discard(key)
        elif status == "404":
            if kind == "m":
                final = FD._day_ms(key + "-01") < L._month_start(self.now_ms - TAKER_FINAL_404_DAYS * _DAY)
                (self.m_missing if final else self.m_recent404).add(key)
            else:
                (self.d_missing if key < self.now_ms - DAILY_FINAL_404_DAYS * _DAY else self.d_recent404).add(key)
        else:
            (self.m_err if kind == "m" else self.d_err).add(key)
            self.bad_time_files += isinstance(val, str) and val.startswith(FD.BAD_TIME)

    def flush(self) -> None:
        FD._merge_csv(self.path, self.frames, TAKER_COLS)
        if self.m_ok or self.m_missing or self.d_ok or self.d_missing:
            have = self.path.exists()
            idx = {"months_ok": sorted(self.m_ok if have else set()), "months_missing": sorted(self.m_missing),
                   "days_ok": FD._ranges(self.d_ok if have else set()), "days_missing": sorted(FD._day_str(d) for d in self.d_missing)}
            FD._write_atomic(self.idx_path, lambda t: t.write_text(json.dumps(idx), encoding="utf-8"))
        self.frames = []

    def coverage(self) -> dict[str, Any]:
        months = sorted(self.m_ok)
        return {"first_month": months[0] if months else None, "last_month": months[-1] if months else None,
                "n_months": len(months), "n_months_missing": len(self.m_missing), "n_days": len(self.d_ok),
                "n_error": len(self.m_err) + len(self.d_err), "n_recent404": len(self.m_recent404) + len(self.d_recent404)}


class _Sym:
    """Bir sembolün metrics durumu + dilim başına taker durumu (yalnız ana iş parçacığı yazar)."""

    def __init__(self, symbol: str, cache_dir, ns: int, yesterday: int, now_ms: int, kstart: dict[str, int]):
        self.symbol, self.sym = symbol, FD._sym(symbol)
        self.paths = _paths(cache_dir, self.sym)
        idx = FD._read_json(self.paths["m_idx"])
        if not idx.get("first_day"):                    # fut_v1 dizininden ilk gün (yalnız OKUNUR)
            fidx = FD._read_json(FD._paths(cache_dir, self.sym)["oi_idx"])
            idx = {**idx, **{k: fidx[k] for k in ("first_day", "search_from") if fidx.get(k)}}
        have = self.paths["m"].exists()
        self.first_day = FD._day_ms(idx["first_day"]) if idx.get("first_day") else None
        self.search_from = FD._day_ms(idx["search_from"]) if idx.get("search_from") else self.first_day
        self.ok = FD._unranges(idx.get("days_ok")) if have else set()
        self.missing = {FD._day_ms(d) for d in idx.get("days_missing", [])}
        self.dup_conflicts = int(idx.get("dup_conflicts", 0)) if have else 0
        self.missing_col_days = {k: int(v) for k, v in (idx.get("missing_col_days") or {}).items()} if have else {}
        self.ns, self.yesterday, self.now_ms = ns, yesterday, now_ms
        self.frames: list[tuple[int, pd.DataFrame]] = []
        self.err_days: set[int] = set()
        self.recent404: set[int] = set()
        self.bad_time_files = 0
        self.pending = 0
        self.status = ""
        self.headerless = False
        self.taker = {tf: _Taker(cache_dir, self.sym, tf, s, now_ms) for tf, s in kstart.items()}

    def need_search(self) -> bool:
        if self.first_day is None:
            return True
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
            self.headerless |= bool(val.attrs.get("headerless"))
            for c in val.attrs.get("missing_cols") or []:
                self.missing_col_days[c] = self.missing_col_days.get(c, 0) + 1
            self.frames.append((day, val))
        elif status == "404":
            if day < self.now_ms - DAILY_FINAL_404_DAYS * _DAY:
                self.missing.add(day)
            else:
                self.recent404.add(day)
        else:
            self.err_days.add(day)
            self.bad_time_files += isinstance(val, str) and val.startswith(FD.BAD_TIME)

    def flush(self) -> None:
        self.dup_conflicts += FD._merge_csv(self.paths["m"], self.frames, METRIC_COLS, FD._own_day)
        if self.first_day is not None:
            idx = {"first_day": FD._day_str(self.first_day), "search_from": FD._day_str(self.search_from or self.first_day),
                   "days_ok": FD._ranges(self.ok if self.paths["m"].exists() else set()),
                   "days_missing": sorted(FD._day_str(d) for d in self.missing), "dup_conflicts": int(self.dup_conflicts),
                   "missing_col_days": dict(sorted(self.missing_col_days.items())), "fetched_ms": int(self.now_ms)}
            FD._write_atomic(self.paths["m_idx"], lambda t: t.write_text(json.dumps(idx), encoding="utf-8"))
        self.frames = []
        for tk in self.taker.values():
            tk.flush()

    def coverage(self) -> dict[str, Any]:
        win = self.window_days()
        ok = [d for d in win if d in self.ok]
        miss = [d for d in win if d in self.missing or d in self.recent404]
        err = [d for d in win if d in self.err_days]
        gaps = FD._ranges({d for d in win if d not in self.ok})
        t_err = sum(len(t.m_err) + len(t.d_err) for t in self.taker.values())
        t_req = sum(len(t.m_ok) + len(t.m_missing) + len(t.m_err) for t in self.taker.values())
        status = self.status or ("NO_DATA" if self.first_day is None else
                                 "PARTIAL_ERROR" if (win and len(err) > FD.PARTIAL_ERROR_SHARE * len(win))
                                 or (t_req and t_err > FD.PARTIAL_ERROR_SHARE * t_req) else "OK")
        return {"sym": self.sym, "first_day": FD._day_str(self.first_day) if self.first_day is not None else None,
                "last_day": FD._day_str(max(ok)) if ok else None, "n_days": len(win), "n_ok": len(ok), "n_missing": len(miss),
                "n_error": len(err), "status": status, "dup_conflicts": int(self.dup_conflicts),
                "n_bad_time": int(self.bad_time_files + sum(t.bad_time_files for t in self.taker.values())),
                "missing_col_days": dict(sorted(self.missing_col_days.items())), "gaps": gaps[:10], "n_gaps": len(gaps),
                "taker": {tf: t.coverage() for tf, t in self.taker.items()}}


def ensure_crowd(symbols, tfs, cache_dir, days: dict[str, int], now_ms: int, *, fetch: Callable[[str], bytes | None] | None = None,
                 threads: int | None = None, log: Callable[[str], None] = print, offline: bool = False) -> dict[str, dict]:
    """Ana süreç, mum döngüsünden sonra: eksik metrics günlerini ve taker mum dosyalarını indirir, önbelleğe yazar.
    Döner {sembol (verildiği gibi): kapsama}; her sembol için `CROWD_COV` satırı. `offline` → yalnız önbellek.
    Bağlantı yoksa `CrowdDataUnavailable` (inen veri yine de önbelleğe yazılır)."""
    fetch = fetch or L._http_get
    threads = int(threads or os.environ.get("SIGNAL_LAB_FUTURES_THREADS", FD.DEFAULT_THREADS))
    now_ms = int(now_ms)
    today = FD._floor_day(now_ms)
    yesterday = today - _DAY
    ns = min(FD._floor_day(FD.need_start(days, tfs, now_ms)), yesterday)
    this_month = L._month_start(now_ms)
    recent_month = L._month_start(now_ms - TAKER_FINAL_404_DAYS * _DAY)
    kstart = {tf: FD._floor_day(now_ms - int(days[tf]) * _DAY) for tf in tfs if tf in CROWD_TFS}
    states = {s: _Sym(s, cache_dir, ns, yesterday, now_ms, kstart) for s in symbols}
    stats = {"requests": 0, "errors": 0, "content_errors": 0, "consecutive": 0, "bytes": 0, "ok": 0, "404": 0, "t0": time.time(),
             "total": 0}
    LAST_RUN_STATS.clear()

    def _abort(why: str) -> CrowdDataUnavailable:
        return CrowdDataUnavailable(
            f"Kalabalık verisi (metrics/taker) indirilemedi: {why} ({stats['errors']}/{stats['requests']} istek hatalı). "
            "Bağlantı ya da Binance tarafında geçici kısıtlama olabilir; 10-15 dk sonra tekrar deneyin (inen veri önbellekte "
            "kalır). Test ÇALIŞTIRILMADI.")

    def count(st: str, nb: int, val: Any = None) -> None:
        stats["requests"] += 1
        stats["content_errors"] += st == "error" and isinstance(val, str) and val.startswith((FD.CORRUPT, FD.BAD_TIME))
        stats["bytes"] += nb
        stats[st if st != "error" else "errors"] += 1
        stats["consecutive"] = stats["consecutive"] + 1 if st == "error" else 0
        n = stats["requests"]
        if n % FD.PROGRESS_EVERY == 0:
            rate = n / max(time.time() - stats["t0"], 1e-9)
            log(f"kalabalık veri: {n}/{stats['total']} dosya · {rate:.0f} dosya/sn · hata {stats['errors']}")
        if stats["consecutive"] >= FD.ABORT_CONSECUTIVE:
            raise _abort(f"art arda {stats['consecutive']} kalabalık arşiv dosyası indirilemedi")

    def run_jobs(ex: ThreadPoolExecutor, todo: list[tuple[_Sym, str, Any, Any, str, Callable]]) -> None:
        jobs: dict[Any, tuple[_Sym, str, Any, Any]] = {}
        stats["total"] = stats["requests"] + len(todo)
        for st, kind, tf, key, url, parser in todo:
            st.pending += 1
            jobs[ex.submit(FD._get, fetch, url, parser)] = (st, kind, tf, key)
        try:
            for fut in as_completed(jobs):
                st, kind, tf, key = jobs[fut]
                s_, val, nb = fut.result()
                if kind == "d":
                    n_bad = st.bad_time_files
                    st.add_day(key, s_, val)
                    if n_bad == 0 and st.bad_time_files == 1:
                        log(f"kalabalık veri {st.sym}: {val} — dosya `error` sayıldı, önbelleğe yazılmadı")
                else:
                    st.taker[tf].add("m" if kind == "km" else "d", key, s_, val)
                st.pending -= 1
                count(s_, nb, val)
                if st.pending == 0:
                    st.flush()
        except BaseException:
            for f in jobs:
                f.cancel()
            raise

    try:
        if not offline:
            with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
                # 1) ilk gün (fut_v1 dizininde yoksa): fut_v1 ikili araması; bulunan günler tam ayrıştırmayla yeniden istenir
                searching = {ex.submit(FD._search, fetch, st.sym, ns, st.first_day if st.first_day is not None and ns < st.first_day
                                       else None, yesterday): st for st in states.values() if st.need_search()}
                stats["total"] = len(searching)
                for fut in as_completed(searching):
                    st = searching[fut]
                    first, got, err = fut.result()
                    stats["total"] += len(got) - 1
                    for _d, s_, val, nb in got:
                        count(s_, nb, val)
                    if first is not None:
                        st.first_day, st.search_from = first, ns
                    elif err:
                        st.status = "SEARCH_ERROR" if st.first_day is None else ""
                        log(f"kalabalık veri {st.sym}: ilk gün aranamadı ({err})")
                # 2) eksik metrics günleri + bitmiş ay taker dosyaları + içinde bulunulan ayın taker günleri
                todo: list[tuple[_Sym, str, Any, Any, str, Callable]] = []
                for st in states.values():
                    for d in st.window_days():
                        if d not in st.ok and d not in st.missing and d not in st.recent404 and d not in st.err_days:
                            todo.append((st, "d", None, d, METRICS_URL.format(sym=st.sym, day=FD._day_str(d)), parse_metrics_full_zip))
                    for tf, tk in st.taker.items():
                        for mo in tk.months(this_month):
                            if mo not in tk.m_ok and mo not in tk.m_missing:
                                todo.append((st, "km", tf, mo, KLINES_MONTHLY_URL.format(sym=st.sym, tf=tf, month=mo), parse_taker_zip))
                        for d in tk.days_of(this_month, today):
                            if d not in tk.d_ok and d not in tk.d_missing:
                                todo.append((st, "kd", tf, d, KLINES_DAILY_URL.format(sym=st.sym, tf=tf, day=FD._day_str(d)), parse_taker_zip))
                run_jobs(ex, todo)
                # 3) ay dosyası henüz yayımlanmamış yakın ay → gün dosyaları
                todo = []
                for st in states.values():
                    for tf, tk in st.taker.items():
                        for mo in sorted(tk.m_recent404):
                            m_ms = FD._day_ms(mo + "-01")
                            if m_ms < recent_month:
                                continue
                            for d in tk.days_of(m_ms, today):
                                if d not in tk.d_ok and d not in tk.d_missing:
                                    todo.append((st, "kd", tf, d, KLINES_DAILY_URL.format(sym=st.sym, tf=tf, day=FD._day_str(d)),
                                                 parse_taker_zip))
                run_jobs(ex, todo)
            n, e = stats["requests"], stats["errors"]
            if n and ((n >= FD.ABORT_CONSECUTIVE and e > FD.ABORT_ERROR_SHARE * n) or (e == n and e > stats["content_errors"])):
                raise _abort(f"isteklerin %{100 * e / n:.0f}'i hatalı (sınır %{100 * FD.ABORT_ERROR_SHARE:.0f})")
    finally:
        for st in states.values():
            if st.headerless:
                log(f"kalabalık veri {st.sym}: başlıksız metrics dosyası konumdan okundu (sütun 0, 2, 4, 5, 6, 7)")
            st.flush()
        secs = time.time() - stats["t0"]
        LAST_RUN_STATS.update(files=stats["requests"], ok=stats["ok"], not_found=stats["404"], errors=stats["errors"],
                              mb=round(stats["bytes"] / 1e6, 2), seconds=round(secs, 1),
                              files_per_s=round(stats["requests"] / secs, 1) if secs > 0 else None, threads=threads)
    out = {}
    for s, st in states.items():
        if offline and not st.paths["m"].exists():
            st.status = "NO_CACHE"
        out[s] = st.coverage()
        log(FD.log_line("CROWD_COV", out[s]))
    return out


# ---------------------------------------------------------------------------- yoklama (P1–P7; P8 laboratuvarda)
def _year_share(t: np.ndarray, bad: np.ndarray) -> dict[str, float]:
    yrs = pd.to_datetime(t, unit="ms", utc=True).year.to_numpy()
    return {str(y): round(float(bad[yrs == y].mean()), 4) for y in np.unique(yrs)}


def metrics_column_stats(m: pd.DataFrame) -> dict[str, Any]:
    """P2: sütun başına ilk dolu gün ve yıl başına boş payı (önbellekten)."""
    t = m["t_ms"].to_numpy(dtype=np.int64)
    out: dict[str, Any] = {"rows": int(len(t))}
    if not len(t):
        return out
    for c in CF.METRIC_FIELDS:
        x = m[c].to_numpy(dtype=float)
        fin = np.isfinite(x)
        out[c] = {"first_day": FD._day_str(int(t[fin][0])) if fin.any() else None, "empty_by_year": _year_share(t, ~fin)}
    return out


def taker_agreement(cache_dir, symbol: str, tf: str) -> dict[str, Any]:
    """P3: lab'ın mum önbelleği ↔ taker dosyası: satırı bulunan pay, hacim uyuşmazlığı, geçerli tb payı, ort. taker payı."""
    sym = FD._sym(symbol)
    kp, _ = _taker_paths(cache_dir, sym, tf)
    lp = L.cache_path(Path(cache_dir), symbol, tf)
    if not kp.exists() or not lp.exists():
        return {"sym": sym, "tf": tf, "status": "NO_CACHE"}
    lab = pd.read_csv(lp)
    k = pd.read_csv(kp)
    tb, st = join_taker(lab["timestamp"], lab["volume"], k["t_ms"], k["v"], k["tb"])
    v = lab["volume"].to_numpy(dtype=float)
    good = CF.clean_taker(v, tb)
    fin = np.isfinite(good)
    with np.errstate(invalid="ignore", divide="ignore"):
        share = good[fin] / v[fin]
    n = max(st["bars"], 1)
    return {"sym": sym, "tf": tf, "bars": st["bars"], "found_share": round(st["found"] / n, 4),
            "vol_mismatch": st["vol_mismatch"], "valid_share": round(float(fin.sum()) / n, 4),
            "invalid": int(st["found"] - st["vol_mismatch"] - int(fin.sum())),
            "taker_share_mean": round(float(np.nanmean(share)), 4) if fin.any() else None,
            "first_ms": int(k["t_ms"].iloc[0]) if len(k) else None}


def cache_schema_lines(symbols, tfs, cache_dir) -> list[str]:
    """`crowd`/`crowd-ctx` koşusunun başında önbellekten P2/P3/P6 özetleri (ağ YOK)."""
    lines = []
    for s in symbols:
        sym = FD._sym(s)
        p = _paths(cache_dir, sym)
        if p["m"].exists():
            m = pd.read_csv(p["m"])
            lines.append(FD.log_line("CROWD_PROBE", {"item": "P2", "sym": sym, **metrics_column_stats(m)}))
            lines.append(FD.log_line("CROWD_PROBE", {"item": "P6", "sym": sym, **FD._metrics_stats(m[["t_ms", "oi"]])}))
        for tf in tfs:
            lines.append(FD.log_line("CROWD_PROBE", {"item": "P3", **taker_agreement(cache_dir, s, tf)}))
    return lines


def probe(symbols, tfs, cache_dir, now_ms: int, *, fetch: Callable[[str], bytes | None] | None = None,
          log: Callable[[str], None] = print, coverage: dict[str, dict] | None = None) -> dict[str, Any]:
    """`--futures crowd-probe` P1–P7 (`ensure_crowd`'dan SONRA; P8 kör sayımları laboratuvar yapar). Her madde
    `CROWD_PROBE` satırı. Sonuç kararı değiştirmez: şema çelişkisi yalnız bu dosyanın ayrıştırmasını değiştirebilir
    (kayıt mührü değil)."""
    fetch = fetch or L._http_get
    now_ms = int(now_ms)
    yesterday = FD._floor_day(now_ms) - _DAY
    syms = [FD._sym(s) for s in symbols]
    idx = {s: FD._read_json(_paths(cache_dir, s)["m_idx"]) for s in syms}
    out: dict[str, Any] = {}

    def emit(item: str, obj: dict) -> None:
        log(FD.log_line("CROWD_PROBE", {"item": item, **obj}))

    def raw(url: str) -> tuple[str, list[str]]:
        try:
            data = fetch(url)
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}", []
        return ("404", []) if data is None else ("ok", FD._raw_head(data))

    # P1 — kapsama (metrics günleri + dilim başına taker ayları)
    p1 = {}
    for s, orig in zip(syms, symbols):
        cov = (coverage or {}).get(orig) or (coverage or {}).get(s) or {}
        p1[s] = {k: cov.get(k) for k in ("first_day", "last_day", "n_ok", "n_missing", "n_error", "status", "gaps", "taker")}
        emit("P1", {"sym": s, **p1[s]})
    out["P1"] = p1

    # P2 — metrics şeması (3 dönemden ham satırlar) + sütun başına ilk dolu gün ve yıllık boş payı
    ref = next((s for s in syms if idx[s].get("first_day")), None)
    p2: dict[str, Any] = {"raw": {}, "per_symbol": {}}
    if ref:
        eras = {"ilk_gün": idx[ref]["first_day"], "2023-06-01": "2023-06-01", "dün": FD._day_str(yesterday)}
        for name, day in eras.items():
            if FD._day_ms(day) < FD._day_ms(idx[ref]["first_day"]):
                continue
            st, lines = raw(METRICS_URL.format(sym=ref, day=day))
            p2["raw"][name] = {"day": day, "status": st, "lines": lines}
        emit("P2", {"sym": ref, "raw": p2["raw"]})
    for s in syms:
        p = _paths(cache_dir, s)["m"]
        if p.exists():
            p2["per_symbol"][s] = {**metrics_column_stats(pd.read_csv(p)), "missing_col_days": idx[s].get("missing_col_days") or {}}
            emit("P2", {"sym": s, **p2["per_symbol"][s]})
    out["P2"] = p2

    # P3 — taker şeması (ilk/son ay ham satırları) + lab mumlarıyla uyum
    p3: dict[str, Any] = {"raw": {}, "per_symbol": []}
    kref = ref or (syms[0] if syms else "BTCUSDT")
    for tf in tfs:
        kidx = FD._read_json(_taker_paths(cache_dir, kref, tf)[1])
        months = sorted(kidx.get("months_ok") or [])
        for name, mo in (("ilk_ay", months[0] if months else None), ("son_ay", months[-1] if months else None)):
            if mo is None:
                continue
            st, lines = raw(KLINES_MONTHLY_URL.format(sym=kref, tf=tf, month=mo))
            p3["raw"][f"{tf}_{name}"] = {"month": mo, "status": st, "lines": lines}
    emit("P3", {"sym": kref, "raw": p3["raw"]})
    for s in symbols:
        for tf in tfs:
            row = taker_agreement(cache_dir, s, tf)
            p3["per_symbol"].append(row)
            emit("P3", row)
    out["P3"] = p3

    # P4 — olmayan anahtar 404 mü 403 mü (metrics günü + aylık mum)
    p4 = {}
    for name, url in (("metrics", METRICS_URL.format(sym=kref, day="2019-01-01")),
                      ("klines", KLINES_MONTHLY_URL.format(sym=kref, tf=(tfs[0] if tfs else "4h"), month="2015-01"))):
        try:
            p4[name] = {"url": url.split("/futures/um/")[-1], "status": "404" if fetch(url) is None else "200"}
        except Exception as exc:  # noqa: BLE001
            p4[name] = {"url": url.split("/futures/um/")[-1], "status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:200]}"}
    emit("P4", p4)
    out["P4"] = p4

    # P5 — checksum örneklemi: sembol başına ilk + son metrics günü ve dilim başına son taker ayı
    urls = []
    for s in syms:
        ok = FD._unranges(idx[s].get("days_ok"))
        if ok:
            urls += [METRICS_URL.format(sym=s, day=FD._day_str(min(ok))), METRICS_URL.format(sym=s, day=FD._day_str(max(ok)))]
        for tf in tfs:
            months = FD._read_json(_taker_paths(cache_dir, s, tf)[1]).get("months_ok") or []
            if months:
                urls.append(KLINES_MONTHLY_URL.format(sym=s, tf=tf, month=max(months)))
    p5 = FD.checksum_sample(urls, fetch, log)
    emit("P5", p5)
    out["P5"] = p5

    # P6 — 5 dk ızgarası ve yinelenen satır çatışmaları
    p6 = {}
    for s in syms:
        p = _paths(cache_dir, s)["m"]
        if p.exists():
            m = pd.read_csv(p)
            p6[s] = {**FD._metrics_stats(m[["t_ms", "oi"]]), "dup_conflicts": int(idx[s].get("dup_conflicts", 0))}
            emit("P6", {"sym": s, **p6[s]})
    out["P6"] = p6

    # P7 — hız ve disk
    d = _dir(cache_dir)
    size = sum(p.stat().st_size for p in d.glob("*") if p.is_file()) if d.exists() else 0
    p7 = {**LAST_RUN_STATS, "cache_mb": round(size / 1e6, 2)}
    emit("P7", p7)
    out["P7"] = p7
    return out


__all__ = ["METRICS_URL", "KLINES_MONTHLY_URL", "KLINES_DAILY_URL", "CROWD_TFS", "COLUMN_MAP", "VOL_AGREE_RTOL",
           "CrowdDataUnavailable", "parse_metrics_full_zip", "parse_taker_zip", "ensure_crowd", "read_crowd_cache",
           "join_taker", "probe", "cache_schema_lines", "LAST_RUN_STATS"]
