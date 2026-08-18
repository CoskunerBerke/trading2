"""HistoryCollector — Binance public tarihsel veri: plan (dry-run) → collect (archive-first + REST, resume, idempotent, checksum) → validate.

Kaynak önceliği: (1) data.binance.vision aylık arşiv (zip + .CHECKSUM sha256 doğrulaması) (2) Binance public REST (`BinanceSpotProvider` /
`BinanceFuturesProvider`, rate budget'lı HttpClient) (3) çağıranın verdiği fallback provider. API anahtarı gerekmez.
Bozuk/doğrulanamayan parça yazılmaz, manifest'te fail-closed işaretlenir. Aynı aralık ikinci kez istenirse aynı mumlar çoğaltılmaz.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from ..core import iso, utc_now
from ..market.providers import klines_frame, tf_ms, to_raw
from .store import KLINE_COLS, HistoryStore

log = logging.getLogger(__name__)

ARCHIVE_BASE = "https://data.binance.vision/data"
MARKETS = ("spot", "futures")
MAX_AVAILABLE = "MAX_AVAILABLE"
_ROW_BYTES = {"1m": 38, "5m": 40, "15m": 42, "1h": 44, "4h": 46, "1d": 48}     # csv.gz yaklaşık bayt/satır (tahmin)


def month_starts(from_ms: int, to_ms: int) -> list[tuple[int, int]]:
    d = datetime.fromtimestamp(from_ms / 1000, tz=timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = datetime.fromtimestamp(to_ms / 1000, tz=timezone.utc)
    out = []
    while d <= end:
        out.append((d.year, d.month))
        d = d.replace(year=d.year + (d.month == 12), month=(d.month % 12) + 1)
    return out


def month_bounds_ms(year: int, month: int) -> tuple[int, int]:
    a = datetime(year, month, 1, tzinfo=timezone.utc)
    b = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


@dataclass
class CollectSpec:
    markets: tuple[str, ...] = ("spot", "futures")
    symbols: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ("1h", "4h", "1d")
    from_ms: int | None = None                  # None + max_available → listing'den
    to_ms: int | None = None                    # None → şimdi
    days: int | None = None                     # from_ms yoksa: son N gün
    max_available: bool = False
    include_funding: bool = True
    include_open_interest: bool = True
    resume: bool = True
    archive_first: bool = True
    per_tf_days: dict[str, int] = field(default_factory=dict)   # tier: {"1m": 90, "5m": 365}

    def range_for(self, tf: str, listing_ms: int | None, now_ms_: int) -> tuple[int, int]:
        end = self.to_ms or now_ms_
        if tf in self.per_tf_days:
            start = end - self.per_tf_days[tf] * 86_400_000
        elif self.from_ms is not None:
            start = self.from_ms
        elif self.max_available:
            start = listing_ms if listing_ms is not None else end - 360 * 86_400_000
        else:
            start = end - (self.days or 360) * 86_400_000
        if listing_ms is not None:
            start = max(start, listing_ms)
        return int(start), int(end)


class ArchiveClient:
    """data.binance.vision aylık kline arşivi. `fetch_bytes(url) -> bytes | None` enjekte edilebilir (test); yoksa requests kullanılır."""

    def __init__(self, fetch_bytes: Callable[[str], bytes | None] | None = None, timeout: float = 30.0):
        self._fetch = fetch_bytes
        self.timeout = timeout
        self.stats = {"zip_ok": 0, "zip_missing": 0, "checksum_fail": 0}

    def _get(self, url: str) -> bytes | None:
        if self._fetch is not None:
            return self._fetch(url)
        import requests
        r = requests.get(url, timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    @staticmethod
    def kline_url(market: str, symbol: str, tf: str, year: int, month: int) -> str:
        seg = "spot" if market == "spot" else "futures/um"
        raw = to_raw(symbol)
        return f"{ARCHIVE_BASE}/{seg}/monthly/klines/{raw}/{tf}/{raw}-{tf}-{year:04d}-{month:02d}.zip"

    def fetch_month(self, market: str, symbol: str, tf: str, year: int, month: int, *, now_ms_: int | None = None) -> tuple[pd.DataFrame | None, str]:
        """Dönen (df|None, status): 'ok' | 'missing' | 'checksum_mismatch' | 'corrupt'. checksum uymazsa veri DÖNDÜRÜLMEZ (fail-closed)."""
        url = self.kline_url(market, symbol, tf, year, month)
        blob = self._get(url)
        if blob is None:
            self.stats["zip_missing"] += 1
            return None, "missing"
        chk = self._get(url + ".CHECKSUM")
        if chk:
            expected = chk.decode("utf-8", "ignore").split()[0].strip().lower()
            if hashlib.sha256(blob).hexdigest() != expected:
                self.stats["checksum_fail"] += 1
                return None, "checksum_mismatch"
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                name = zf.namelist()[0]
                text = zf.read(name).decode("utf-8")
        except (zipfile.BadZipFile, IndexError, UnicodeDecodeError):
            return None, "corrupt"
        rows = []
        for r in csv.reader(io.StringIO(text)):
            if not r or not r[0].strip().lstrip("-").isdigit():
                continue                                    # başlık satırı (yeni arşivlerde var)
            ts = int(r[0])
            if ts > 10**14:                                  # 2025+ arşivlerde mikro-saniye
                r[0] = str(ts // 1000)
                if len(r) > 6:
                    r[6] = str(int(r[6]) // 1000)
            rows.append(r)
        df = klines_frame(rows, now_ms_=now_ms_ or int(time.time() * 1000))
        self.stats["zip_ok"] += 1
        return df, "ok"


class HistoryCollector:
    def __init__(self, store: HistoryStore, *, spot=None, futures=None, archive: ArchiveClient | None = None,
                 clock_ms: Callable[[], int] | None = None, pause_s: float = 0.0):
        self.store, self.spot, self.futures = store, spot, futures
        self.archive = archive
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.pause_s = pause_s
        self.stats = {"requests": 0, "rows_new": 0, "rows_in": 0, "duplicates": 0, "bad_chunks": 0, "archive_months": 0}

    def _prov(self, market: str):
        p = self.spot if market == "spot" else self.futures
        if p is None:
            raise ValueError(f"{market} sağlayıcısı yok")
        return p

    # ------------------------------------------------------------ listing tespiti (1 istek)
    def listing_ms(self, market: str, symbol: str) -> int | None:
        try:
            df = self._prov(market).klines(symbol, "1d", limit=1, start_ms=0)
            self.stats["requests"] += 1
            return int(df["timestamp"].iloc[0]) if len(df) else None
        except Exception as exc:  # noqa: BLE001
            log.warning("%s %s listing tespiti başarısız: %s", market, symbol, exc)
            return None

    # ------------------------------------------------------------ plan (dry-run)
    def plan(self, spec: CollectSpec, *, probe_listing: bool = True) -> dict:
        now_ = self.clock_ms()
        items, total_rows, total_bytes, total_req = [], 0, 0, 0
        for market in spec.markets:
            for sym in spec.symbols:
                lst = self.listing_ms(market, sym) if (probe_listing and (spec.max_available or spec.from_ms is None)) else None
                for tf in spec.timeframes:
                    start, end = spec.range_for(tf, lst, now_)
                    m = self.store.manifest(market, sym, tf)
                    if spec.resume and m.last_ts_ms:
                        start = max(start, m.last_ts_ms + 1)
                    if end <= start:
                        items.append({"market": market, "symbol": sym, "timeframe": tf, "from": iso(datetime.fromtimestamp(start / 1000, tz=timezone.utc)),
                                      "to": iso(datetime.fromtimestamp(end / 1000, tz=timezone.utc)), "expected_rows": 0, "requests": 0, "bytes": 0, "have_rows": m.row_count})
                        continue
                    rows = max(0, (end - start) // tf_ms(tf))
                    months = len(month_starts(start, end)) if spec.archive_first else 0
                    lim = self._prov(market).max_kline_limit if getattr(self._prov(market), "max_kline_limit", None) else 1000
                    rest_pages = (rows // lim + 1) if not spec.archive_first else 2      # arşiv sonrası ay içi tamamlama
                    req = months * 2 + rest_pages
                    b = rows * _ROW_BYTES.get(tf, 44)
                    items.append({"market": market, "symbol": sym, "timeframe": tf, "from": iso(datetime.fromtimestamp(start / 1000, tz=timezone.utc)),
                                  "to": iso(datetime.fromtimestamp(end / 1000, tz=timezone.utc)), "expected_rows": int(rows), "requests": int(req),
                                  "bytes": int(b), "have_rows": m.row_count, "listing": iso(datetime.fromtimestamp(lst / 1000, tz=timezone.utc)) if lst else None})
                    total_rows += rows; total_bytes += b; total_req += req
                if market == "futures" and spec.include_funding:
                    total_req += 8
                if market == "futures" and spec.include_open_interest:
                    total_req += 1
        est_s = total_req * 0.35 + total_rows / 200_000          # ~0.35 s/istek + işleme
        return {"generated_at": iso(utc_now()), "symbols": len(spec.symbols), "markets": list(spec.markets), "timeframes": list(spec.timeframes),
                "series": len(items), "expected_rows": int(total_rows), "disk_bytes_est": int(total_bytes), "requests_est": int(total_req),
                "eta_seconds_est": int(est_s), "items": items}

    # ------------------------------------------------------------ toplama
    def collect(self, spec: CollectSpec, *, on_progress: Callable[[dict], None] | None = None) -> dict:
        now_ = self.clock_ms()
        results = []
        for market in spec.markets:
            for sym in spec.symbols:
                lst = self.listing_ms(market, sym) if (spec.max_available or spec.from_ms is None) else None
                for tf in spec.timeframes:
                    start, end = spec.range_for(tf, lst, now_)
                    r = self.collect_series(market, sym, tf, start, end, resume=spec.resume, archive_first=spec.archive_first)
                    results.append(r)
                    if on_progress:
                        on_progress(r)
                if market == "futures" and spec.include_funding:
                    results.append(self.collect_funding(sym, spec.range_for("8h", lst, now_)[0], now_))
                if market == "futures" and spec.include_open_interest:
                    results.append(self.collect_open_interest(sym))
        return {"finished_at": iso(utc_now()), "stats": dict(self.stats), "series": results}

    def collect_series(self, market: str, symbol: str, tf: str, start_ms: int, end_ms: int, *, resume: bool = True, archive_first: bool = True) -> dict:
        m = self.store.manifest(market, symbol, tf)
        cur = start_ms
        if resume and m.cursor_ms and m.cursor_ms > cur:
            cur = m.cursor_ms
        if resume and m.last_ts_ms and m.last_ts_ms + 1 > cur and (m.first_ts_ms or 0) <= start_ms:
            cur = m.last_ts_ms + 1                            # aralığın başı zaten var → sondan devam
        step = tf_ms(tf)
        end_closed = end_ms - (end_ms % step)                 # kapanmamış son mumu alma
        rows_new = 0
        chunks_bad = 0
        # 1) arşiv: tamamı geçmişte kalan aylar
        if archive_first and self.archive is not None:
            for y, mo in month_starts(cur, end_closed):
                a, b = month_bounds_ms(y, mo)
                if b > end_closed or a < cur - step:      # ay tamamen [cur, end) içinde değilse REST'e bırak
                    if a < cur - step:
                        continue
                    break
                df, status = self.archive.fetch_month(market, symbol, tf, y, mo, now_ms_=self.clock_ms())
                self.stats["requests"] += 2
                chunk = f"archive:{y:04d}-{mo:02d}"
                if status == "ok" and df is not None and len(df):
                    df = df[df["timestamp"] < end_closed]
                    res = self.store.write(market, symbol, tf, df, source="archive", chunk_id=chunk)
                    rows_new += res["rows_new"]; self.stats["rows_new"] += res["rows_new"]; self.stats["rows_in"] += res["rows_in"]
                    self.stats["duplicates"] += res["duplicates"]; self.stats["archive_months"] += 1
                    cur = max(cur, int(df["timestamp"].iloc[-1]) + step)
                    self.store.set_cursor(market, symbol, tf, cur)
                elif status in ("checksum_mismatch", "corrupt"):
                    self.store.mark_bad_chunk(market, symbol, tf, chunk, status)
                    chunks_bad += 1; self.stats["bad_chunks"] += 1
                    break                                     # fail-closed: bu seride devam etme (REST ile üzerine yazma yok)
                else:                                         # arşivde ay yok → REST tamamlar
                    break
                if self.pause_s:
                    time.sleep(self.pause_s)
        # 2) REST: kalan aralık (sayfalı, ileri yönlü, resume cursor)
        prov = self._prov(market)
        lim = getattr(prov, "max_kline_limit", 1000)
        guard = 0
        while cur < end_closed and chunks_bad == 0 and guard < 100_000:
            guard += 1
            try:
                df = prov.klines(symbol, tf, limit=lim, start_ms=cur, end_ms=end_closed - 1)
            except Exception as exc:  # noqa: BLE001 — ağ hatası: cursor kalır, sonraki resume devam eder
                log.warning("%s %s %s REST hatası @%s: %s", market, symbol, tf, cur, exc)
                self.store.mark_bad_chunk(market, symbol, tf, f"rest:{cur}", f"error:{type(exc).__name__}")
                chunks_bad += 1; self.stats["bad_chunks"] += 1
                break
            self.stats["requests"] += 1
            if df is None or df.empty:
                break
            if "is_closed" in df.columns:
                df = df[df["is_closed"]]
            df = df[(df["timestamp"] >= cur) & (df["timestamp"] < end_closed)]
            if df.empty:
                break
            res = self.store.write(market, symbol, tf, df[KLINE_COLS], source="rest", chunk_id=f"rest:{cur}")
            rows_new += res["rows_new"]; self.stats["rows_new"] += res["rows_new"]; self.stats["rows_in"] += res["rows_in"]; self.stats["duplicates"] += res["duplicates"]
            last = int(df["timestamp"].iloc[-1])
            cur = last + step
            self.store.set_cursor(market, symbol, tf, cur)
            if len(df) < 2:
                break
            if self.pause_s:
                time.sleep(self.pause_s)
        m2 = self.store.manifest(market, symbol, tf)
        return {"market": market, "symbol": symbol, "timeframe": tf, "rows_new": int(rows_new), "rows_total": m2.row_count, "gaps": m2.gap_count,
                "duplicates_removed": m2.duplicate_count, "bad_chunks": len(m2.bad_chunks), "quality": m2.quality_score,
                "first_ts_ms": m2.first_ts_ms, "last_ts_ms": m2.last_ts_ms, "cursor_ms": m2.cursor_ms}

    def collect_funding(self, symbol: str, start_ms: int, end_ms: int) -> dict:
        prov = self._prov("futures")
        m = self.store.manifest("futures", symbol, "funding")
        cur = max(start_ms, (m.last_ts_ms or 0) + 1)
        rows_new, guard = 0, 0
        while cur < end_ms and guard < 10_000:
            guard += 1
            try:
                rows = prov.funding_history(symbol, limit=1000, start_ms=cur, end_ms=end_ms)
            except Exception as exc:  # noqa: BLE001
                log.warning("%s funding hatası: %s", symbol, exc)
                break
            self.stats["requests"] += 1
            if not rows:
                break
            df = pd.DataFrame([{"timestamp": r["funding_ts"], "rate": r["rate"], "mark": r.get("mark")} for r in rows if r.get("funding_ts")])
            if df.empty:
                break
            rows_new += self.store.write("futures", symbol, "funding", df, source="rest", chunk_id=f"funding:{cur}")["rows_new"]
            cur = int(df["timestamp"].max()) + 1
            if len(rows) < 1000:
                break
        m2 = self.store.manifest("futures", symbol, "funding")
        return {"market": "futures", "symbol": symbol, "timeframe": "funding", "rows_new": rows_new, "rows_total": m2.row_count, "gaps": m2.gap_count}

    def collect_open_interest(self, symbol: str, period: str = "1h") -> dict:
        """Binance OI geçmişi yalnız son ~30 gün; her çağrıda eklenerek birikir (idempotent)."""
        prov = self._prov("futures")
        try:
            rows = prov.open_interest_hist(symbol, period=period, limit=500)
            self.stats["requests"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("%s OI hatası: %s", symbol, exc)
            rows = []
        df = pd.DataFrame([{"timestamp": int(r.get("ts") or r.get("timestamp") or 0), "oi": r.get("oi"), "oi_usdt": r.get("oi_value")} for r in rows])
        df = df[df["timestamp"] > 0] if len(df) else df
        rn = self.store.write("futures", symbol, f"oi_{period}", df, source="rest", chunk_id="oi")["rows_new"] if len(df) else 0
        m2 = self.store.manifest("futures", symbol, f"oi_{period}")
        return {"market": "futures", "symbol": symbol, "timeframe": f"oi_{period}", "rows_new": rn, "rows_total": m2.row_count}


__all__ = ["HistoryCollector", "CollectSpec", "ArchiveClient", "month_starts", "month_bounds_ms", "MAX_AVAILABLE", "MARKETS"]
