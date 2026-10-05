# -*- coding: utf-8 -*-
"""M2X SİMÜLASYONU — ön kayıt docs/M2_AGGRESSIVE_V1.md §3 (geçmiş simülasyonu ve politika-tekrar bootstrap'ı).

PAPER/geçmiş testtir; kâr garantisi değildir. Salt araştırma: canlı defterlere, state/ klasörüne ve ayarlara DOKUNMAZ;
bütün yazma `out_dir/run_<id>/` altındadır (sandbox). İçe aktarılırken ağa çıkmaz.

Bölümler:

* VERİ (§3.1): yalnız data.binance.vision USDⓈ-M arşivi — 1d/1h klines (`signal_lab.ArchiveProvider` + `load_series`
  önbelleği), aylık `fundingRate`, 1h `markPriceKlines` (funding settlement mark'ı). Fiyat verisi commit edilmez.
* KOL (a) — M2 ÜRETECİ (§3.2 E1): gerçek `StrategyBook` nesnesi (ad `m2_tsmom28`, config.yaml'daki M2 ayarları) sürülür;
  öğrenme görünümü motorun kendi kurucusuyla (`LearningMode`), yapı katmanı config'teki kipiyle. Koşum aracı yalnız
  tesisattır: çerçeve (canlı sağlayıcının 1d pencere uzunluğu, `add_snapshot_indicators`), mark, kapanmış 1h bar, saat
  ve arşiv funding'i verir. Kural/boyut/defter mantığı burada YOKTUR.
* M2X KOLLARI (§3.4): kol (a)'nın işlem akışına `m2x_policy` (tek kaynak) ile kendi `FuturesLedgerV2` defterinde boyut.
* BOOTSTRAP (§3.6): dairesel blok bootstrap; her yol `m2x_policy` + `FuturesLedgerV2` ile koşar.
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import m2x_policy as P
from . import signal_lab as L

SIM_VERSION = "m2x_sim_v1"
SIM_DOC = "docs/M2_AGGRESSIVE_V1.md"
DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def _ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)


# ---------------------------------------------------------------------------- dönem ve veri pencereleri (§3.1)
#: Kararlar 2023-01-01 → 2026-09-30 (UTC, dahil). Dönem sonu = 2026-10-01 00:00 (açık pozisyonlar bu anın mark'ıyla).
DECISION_START_MS = _ms("2023-01-01")
DECISION_END_MS = _ms("2026-10-01")
#: 1d ısınma: ön kayıt "2022-05-01'den" ve "canlı sağlayıcının 1d pencere uzunluğu kadar kuyruk" der. 400 barlık canlı
#: pencere 2023-01-01'de ancak 2021-11-27'den başlayan veriyle doludur; iki madde çelişir. Canlı eşdeğerliği (EMA200
#: tohumu) için veri 2021-11-01'den indirilir; 2022-05-01 alt sınır olarak sağlanmış olur (sonuç görülmeden seçildi).
D1_START_MS = _ms("2021-11-01")
#: 1h barlar (stop yolu) ve funding/mark: karar döneminden bir hafta önce başlar.
H1_START_MS = _ms("2022-12-25")
FUNDING_START_MS = _ms("2022-12-01")
#: Arşiv "şimdi"si: 2026-09 ayı bitmiş sayılır (ay dosyası yoksa gün dosyaları).
ARCHIVE_NOW_MS = DECISION_END_MS
MARK_URL = L.ARCHIVE_BASE + "/monthly/markPriceKlines/{sym}/1h/{sym}-1h-{month}.zip"
MARK_DAILY_URL = L.ARCHIVE_BASE + "/daily/markPriceKlines/{sym}/1h/{sym}-1h-{day}.zip"


def arch_sym(symbol: str) -> str:
    return symbol.split(":")[0].replace("/", "").upper()


def _months(start_ms: int, end_ms: int) -> list[int]:
    out, cur = [], L._month_start(start_ms)
    while cur < end_ms:
        out.append(cur)
        cur = L._next_month(cur)
    return out


def _stamp(ms: int, fmt: str) -> str:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").strftime(fmt)


def funding_cache_path(cache_dir: Path, symbol: str) -> Path:
    return Path(cache_dir) / f"{arch_sym(symbol)}_funding.csv.gz"


def mark_cache_path(cache_dir: Path, symbol: str) -> Path:
    return Path(cache_dir) / f"{arch_sym(symbol)}_mark1h.csv.gz"


def fetch_funding(symbol: str, cache_dir: Path, get: Callable[[str], bytes | None]) -> dict[str, Any]:
    """Aylık `fundingRate` (yoksa gün dosyaları) → önbellek `<SYM>_funding.csv.gz` [t_ms, rate, interval_file]."""
    from . import futures_data as FD
    p = funding_cache_path(cache_dir, symbol)
    if p.exists():
        return {"symbol": symbol, "cached": True}
    sym = arch_sym(symbol)
    frames, missing = [], []
    for mon in _months(FUNDING_START_MS, DECISION_END_MS):
        stamp = _stamp(mon, "%Y-%m")
        data = get(FD.FUNDING_URL.format(sym=sym, month=stamp))
        if data is not None:
            frames.append(FD.parse_funding_zip(data))
            continue
        got, day = 0, mon
        while day < min(L._next_month(mon), DECISION_END_MS):
            d = get(FD.FUNDING_DAILY_URL.format(sym=sym, day=_stamp(day, "%Y-%m-%d")))
            if d is not None:
                frames.append(FD.parse_funding_zip(d))
                got += 1
            day += DAY_MS
        if not got:
            missing.append(stamp)
    cols = ["t_ms", "rate", "interval_file"]
    f = pd.concat(frames)[cols] if frames else pd.DataFrame({c: [] for c in cols})
    f = f.drop_duplicates("t_ms").sort_values("t_ms")
    p.parent.mkdir(parents=True, exist_ok=True)
    f.to_csv(p, index=False, compression="gzip")
    p.with_name(p.name + ".meta.json").write_text(json.dumps({"missing_months": missing, "rows": int(len(f))}),
                                                  encoding="utf-8")
    return {"symbol": symbol, "rows": int(len(f)), "missing_months": missing}


def fetch_mark(symbol: str, cache_dir: Path, get: Callable[[str], bytes | None]) -> dict[str, Any]:
    """1h `markPriceKlines` (ay dosyası, yoksa gün dosyaları) → önbellek `<SYM>_mark1h.csv.gz` [timestamp, open, close]."""
    p = mark_cache_path(cache_dir, symbol)
    if p.exists():
        return {"symbol": symbol, "cached": True}
    sym = arch_sym(symbol)
    frames, missing = [], []
    for mon in _months(FUNDING_START_MS, DECISION_END_MS):
        data = get(MARK_URL.format(sym=sym, month=_stamp(mon, "%Y-%m")))
        if data is not None:
            frames.append(L._parse_archive_zip(data))
            continue
        got, day = 0, mon
        while day < min(L._next_month(mon), DECISION_END_MS):
            d = get(MARK_DAILY_URL.format(sym=sym, day=_stamp(day, "%Y-%m-%d")))
            if d is not None:
                frames.append(L._parse_archive_zip(d))
                got += 1
            day += DAY_MS
        if not got:
            missing.append(_stamp(mon, "%Y-%m"))
    cols = ["timestamp", "open", "close"]
    f = pd.concat(frames)[cols] if frames else pd.DataFrame({c: [] for c in cols})
    f = f.drop_duplicates("timestamp").sort_values("timestamp")
    p.parent.mkdir(parents=True, exist_ok=True)
    f.to_csv(p, index=False, compression="gzip")
    p.with_name(p.name + ".meta.json").write_text(json.dumps({"missing_months": missing, "rows": int(len(f))}),
                                                  encoding="utf-8")
    return {"symbol": symbol, "rows": int(len(f)), "missing_months": missing}


def fetch_symbol(symbol: str, cache_dir: Path, get: Callable[[str], bytes | None] | None = None) -> dict[str, Any]:
    """Bir sembolün bütün arşiv serileri (önbellekte varsa indirilmez)."""
    get = get or L._http_get
    fac = (lambda: L.ArchiveProvider(fetch=get, clock_ms=lambda: ARCHIVE_NOW_MS))
    out: dict[str, Any] = {"symbol": symbol}
    d1 = L.load_series(symbol, "1d", days=(ARCHIVE_NOW_MS - D1_START_MS) // DAY_MS, cache_dir=cache_dir,
                       provider_factory=fac, now_ms=ARCHIVE_NOW_MS)
    h1 = L.load_series(symbol, "1h", days=(ARCHIVE_NOW_MS - H1_START_MS) // DAY_MS, cache_dir=cache_dir,
                       provider_factory=fac, now_ms=ARCHIVE_NOW_MS)
    out["d1_rows"], out["h1_rows"] = int(len(d1)), int(len(h1))
    out["funding"] = fetch_funding(symbol, cache_dir, get)
    out["mark"] = fetch_mark(symbol, cache_dir, get)
    return out


def fetch_all(symbols: list[str], cache_dir: Path, *, jobs: int = 4, log: Callable[[str], None] = print) -> list[dict]:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    res: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, int(jobs))) as ex:
        for r in ex.map(lambda s: fetch_symbol(s, cache_dir), symbols):
            log(json.dumps(r, ensure_ascii=False, default=str))
            res.append(r)
    return res


# ---------------------------------------------------------------------------- arşiv verisi (bellekte)
#: Canlı sağlayıcının 1d penceresi (açık bar dahil satır sayısı) — `engine.TradingEngine.PERP_FRAME_LIMITS` koddan okunur.
def live_daily_window() -> int:
    from .engine import TradingEngine
    return int(TradingEngine.PERP_FRAME_LIMITS["1d"])


class SeriesH1:
    """Bir sembolün 1h barları (numpy) — açılış ms → indeks."""

    __slots__ = ("t", "o", "h", "l", "c", "idx")

    def __init__(self, df: pd.DataFrame):
        df = df.sort_values("timestamp").drop_duplicates("timestamp")
        self.t = df["timestamp"].to_numpy(dtype=np.int64)
        self.o = df["open"].to_numpy(dtype=float)
        self.h = df["high"].to_numpy(dtype=float)
        self.l = df["low"].to_numpy(dtype=float)
        self.c = df["close"].to_numpy(dtype=float)
        self.idx = {int(x): i for i, x in enumerate(self.t)}

    def bar(self, open_ms: int) -> tuple[float, float, float, float] | None:
        i = self.idx.get(int(open_ms))
        if i is None:
            return None
        return float(self.o[i]), float(self.h[i]), float(self.l[i]), float(self.c[i])

    def tail_rows(self, now_ms: int, n: int = 48) -> list[dict[str, Any]]:
        """`now_ms` anında kapanmış son `n` bar (açılış + 1h ≤ now) — motorun `_paper_closed_bars` satır biçimi."""
        j = int(np.searchsorted(self.t, int(now_ms) - HOUR_MS, side="right"))
        i = max(0, j - int(n))
        return [{"timestamp": int(self.t[k]), "open": float(self.o[k]), "high": float(self.h[k]), "low": float(self.l[k]),
                 "close": float(self.c[k])} for k in range(i, j)]


class ArchiveData:
    """Önbellekten okunan arşiv serileri: 1d (çerçeve), 1h (mark ve bar uçları), funding ve 1h mark kline'ları."""

    def __init__(self, symbols: list[str], cache_dir: Path, *, btc: str = "BTC/USDT"):
        self.symbols = list(dict.fromkeys(list(symbols) + [btc]))
        self.cache_dir = Path(cache_dir)
        self.d1: dict[str, pd.DataFrame] = {}
        self.d1_t: dict[str, np.ndarray] = {}
        self.h1: dict[str, SeriesH1] = {}
        self.funding_rows: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.mark_open: dict[str, dict[int, float]] = {}
        self.window = live_daily_window()
        for s in self.symbols:
            self._load(s)

    def _load(self, s: str) -> None:
        p1 = L.cache_path(self.cache_dir, s, "1d")
        ph = L.cache_path(self.cache_dir, s, "1h")
        d1 = pd.read_csv(p1) if p1.exists() else pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        d1 = d1.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        self.d1[s] = d1[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        self.d1_t[s] = self.d1[s]["timestamp"].to_numpy(dtype=np.int64)
        h1 = pd.read_csv(ph) if ph.exists() else pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
        self.h1[s] = SeriesH1(h1)
        pf = funding_cache_path(self.cache_dir, s)
        if pf.exists():
            f = pd.read_csv(pf)
            self.funding_rows[s] = (f["t_ms"].to_numpy(dtype=np.int64), f["rate"].to_numpy(dtype=float))
        else:
            self.funding_rows[s] = (np.array([], dtype=np.int64), np.array([], dtype=float))
        pm = mark_cache_path(self.cache_dir, s)
        if pm.exists():
            m = pd.read_csv(pm)
            self.mark_open[s] = {int(t): float(o) for t, o in zip(m["timestamp"], m["open"])}
        else:
            self.mark_open[s] = {}

    def daily_frame(self, s: str, now_ms: int, *, full: bool = False) -> pd.DataFrame | None:
        """Canlı çerçevenin aynısı: `now` anına kadar açılmış son `window` günlük satır (açık bar dahil) → `prepare` →
        `drop_unclosed_last_bar` → göstergeler (motor `runner._frames`). `full=True`: `add_snapshot_indicators`in
        tamamı; varsayılan yalnız kural/yapı yolunun okuduğu iki sütun (`ema200`, `atr14`), AYNI fonksiyonlarla
        (`indicators.ema`/`atr`) — değerler bit-aynıdır (test: `test_fast_daily_frame_matches_full`)."""
        from .data import drop_unclosed_last_bar, prepare
        from . import indicators as ind
        t = self.d1_t.get(s)
        if t is None or not len(t):
            return None
        j = int(np.searchsorted(t, int(now_ms), side="right"))
        if j <= 0:
            return None
        raw = self.d1[s].iloc[max(0, j - self.window):j]
        df = drop_unclosed_last_bar(prepare(raw), "1d", now_ms=int(now_ms))
        if df is None or not len(df):
            return None
        if full:
            return ind.add_snapshot_indicators(df)
        out = df.copy()
        out["ema200"] = ind.ema(out["close"], 200)
        out["atr14"] = ind.atr(out["high"], out["low"], out["close"], 14)
        return out

    def tour_mark(self, s: str, day_ms: int) -> float | None:
        """Tur mark'ı = günün 00:00 1h barının açılışı (ön kayıt §3.2)."""
        b = self.h1[s].bar(int(day_ms)) if s in self.h1 else None
        return b[0] if b else None

    def quality(self) -> dict[str, Any]:
        """Sembol başına eksik bar oranı, 24 saatten uzun boşluklar, funding kapsamı, aşırı saatlik fitiller (§3.1)."""
        out: dict[str, Any] = {}
        for s in self.symbols:
            h = self.h1[s]
            n = len(h.t)
            row: dict[str, Any] = {"h1_bars": n}
            if n:
                in_dec = (h.t >= DECISION_START_MS) & (h.t < DECISION_END_MS)
                first = max(int(h.t[0]), DECISION_START_MS)
                exp = max(0, (DECISION_END_MS - first) // HOUR_MS)
                got = int(in_dec.sum())
                row.update({"first_h1": _iso_ms(int(h.t[0])), "expected_in_period": int(exp), "got_in_period": got,
                            "missing_share": round(1.0 - got / exp, 6) if exp else None})
                dt = np.diff(h.t)
                gaps = [(int(h.t[i]), int(dt[i])) for i in np.nonzero(dt > DAY_MS)[0]]
                row["gaps_over_24h"] = [{"from": _iso_ms(a), "hours": b // HOUR_MS} for a, b in gaps]
                wick = (h.l / h.o - 1.0)
                sel = np.nonzero((wick <= -0.25) & in_dec)[0]
                row["extreme_wicks"] = [{"bar": _iso_ms(int(h.t[i])), "low_vs_open": round(float(wick[i]), 4)} for i in sel]
            ft, _fr = self.funding_rows.get(s, (np.array([]), np.array([])))
            row["funding_rows"] = int(len(ft))
            row["mark_rows"] = len(self.mark_open.get(s, {}))
            out[s] = row
        return out


def _iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)


class ArchiveFunding:
    """Gerçekleşmiş funding kaynağı (arşiv; ağ yok) — defterin `FundingSchedule.bind_source` sözleşmesi:
    `(sembol, an) → oran | None`, `settlement_mark`, `mark_basis`, `hours_for`.

    * Oran: o settlement satırı (±120 sn). Satır yoksa ve önceki/sonraki satırlar arası ≤ 8 saat ise o saat bir settlement
      DEĞİLDİR → 0 (dönem yok sayılır; sembolün aralığı zamanla değiştiyse saat kümesi birleşimdir). Daha büyük boşluk →
      None (dönem BEKLER; uydurma yok).
    * Settlement mark'ı: 1h `markPriceKlines` açılışı (o saat); yoksa perp 1h açılışı (`BAR_OPEN_PROXY`, işaretli)."""

    TOL_MS = 120_000

    def __init__(self, data: ArchiveData):
        self.data = data
        self._hours: dict[str, tuple[int, ...]] = {}
        self.hits = self.zero = self.unknown = self.proxy_marks = 0

    def hours_for(self, symbol: str) -> tuple[int, ...]:
        h = self._hours.get(symbol)
        if h is None:
            t, _r = self.data.funding_rows.get(symbol, (np.array([], dtype=np.int64), None))
            hrs = sorted({int(((int(x) + 1_800_000) % DAY_MS) // HOUR_MS) for x in t}) if len(t) else [0, 8, 16]
            h = self._hours[symbol] = tuple(hrs)
        return h

    def __call__(self, symbol: str, when: datetime):
        from decimal import Decimal
        t, r = self.data.funding_rows.get(symbol, (np.array([], dtype=np.int64), np.array([])))
        if not len(t):
            self.unknown += 1
            return None
        ms = int(when.timestamp() * 1000)
        i = int(np.searchsorted(t, ms))
        for j in (i - 1, i):
            if 0 <= j < len(t) and abs(int(t[j]) - ms) <= self.TOL_MS:
                self.hits += 1
                return Decimal(repr(float(r[j])))
        if 0 < i < len(t) and int(t[i]) - int(t[i - 1]) <= 8 * HOUR_MS + self.TOL_MS:
            self.zero += 1
            return Decimal(0)
        self.unknown += 1
        return None

    def settlement_mark(self, symbol: str, when: datetime):
        from decimal import Decimal
        ms = int(when.timestamp() * 1000)
        ms -= ms % HOUR_MS
        m = self.data.mark_open.get(symbol, {}).get(ms)
        if m is not None and m > 0:
            return Decimal(repr(float(m)))
        b = self.data.h1[symbol].bar(ms) if symbol in self.data.h1 else None
        if b is not None:
            self.proxy_marks += 1
            return Decimal(repr(float(b[0])))
        return None

    def mark_basis(self, symbol: str, when: datetime) -> str:
        ms = int(when.timestamp() * 1000)
        ms -= ms % HOUR_MS
        return "MARK_KLINE_OPEN" if ms in self.data.mark_open.get(symbol, {}) else "BAR_OPEN_PROXY"

    def stats(self) -> dict[str, int]:
        return {"rate_rows_used": self.hits, "non_settlement_hours": self.zero, "unknown": self.unknown,
                "proxy_marks": self.proxy_marks}


# ---------------------------------------------------------------------------- borsa filtreleri (VARSAYIM; §3.1)
#: VPS'teki doğrulanmış `data/symbol_filters.json`un kopyası bu çalışma alanında YOK → ön kayıtlı varsayım: her sembolde
#: min-notional 5 USDT (BTC 100, ETH 20), miktar adımı yok, varsayılan bracket'lar. Fiyat adımı da yok sayılır (varsayılan
#: 0,01 düşük fiyatlı sembollerde dolumu bozardı). Çıktı "filtre: varsayım" diye işaretlenir.
ASSUMED_MIN_NOTIONAL = {"BTC/USDT": 100.0, "ETH/USDT": 20.0}
FILTERS_ASSUMED = "ASSUMED_NO_VPS_COPY"


class SimFilters:
    """`FiltersCache.get` arayüzü (varsayım filtreleri)."""

    def __init__(self):
        self._c: dict[str, Any] = {}

    def get(self, symbol: str, market_type: Any = None):
        f = self._c.get(symbol)
        if f is None:
            from decimal import Decimal
            from .accounting import MarketType, SymbolFilters
            tiny = Decimal("1e-12")
            f = SymbolFilters(symbol=symbol, market_type=MarketType.USDM_PERP, price_tick=tiny, qty_step=tiny, min_qty=tiny,
                              max_qty=Decimal("1e15"), market_max_qty=Decimal("1e15"),
                              min_notional=Decimal(repr(ASSUMED_MIN_NOTIONAL.get(symbol, 5.0))), max_leverage=20,
                              source=FILTERS_ASSUMED)
            self._c[symbol] = f
        return f


# ---------------------------------------------------------------------------- sandbox yapı deposu (tesisat)
class LightStructureStore:
    """`structures.store.StructureStore` arayüzü, sandbox için hafif: kararlar `decisions.jsonl`a (fsync'siz) ve bellekteki
    son-hâl haritasına yazılır (aynı satır ve aynı `decision_id`); analiz görüntüleri (latest/snapshots) YAZILMAZ. Karar
    yolu depoyu OKUMAZ (`used_patterns_of` defterden okur); test `test_light_store_is_decision_neutral` aynı işlemleri
    gerçek depoyla karşılaştırır."""

    def __init__(self, state_dir: Path | str):
        from .structures.store import DIRNAME
        self.root = Path(state_dir) / DIRNAME
        self._latest: dict[str, dict[str, Any]] = {}
        self.n_rows = 0

    def save_latest(self, an: dict[str, Any]) -> bool:
        return bool(an and an.get("analysis_id"))

    def save_snapshot(self, an: dict[str, Any]) -> bool:
        return bool(an and an.get("analysis_id"))

    def record_decision(self, decision: dict[str, Any], *, book_id: str, market: str, symbol: str, at_ms: int,
                        analyses: dict[str, Any] | None = None, trade_id: str | None = None) -> dict[str, Any]:
        from .structures.store import DECISIONS_FILE, decision_id, iso_ms
        row = dict(decision)
        row.update({"book_id": book_id, "market": market, "symbol": symbol, "at_ms": int(at_ms), "at": iso_ms(at_ms)})
        if trade_id:
            row["trade_id"] = trade_id
        row["decision_id"] = decision_id(row)
        key = "%s|%s|%s" % (book_id, market, symbol)
        prev = self._latest.get(key) or {}
        changed = prev.get("decision_id") != row["decision_id"] or bool(trade_id and prev.get("trade_id") != trade_id)
        if changed:
            row["first_seen_ms"] = int(at_ms)
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self.root / DECISIONS_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({k: v for k, v in row.items() if k != "analyses"}, ensure_ascii=False, default=str) + "\n")
            self.n_rows += 1
        else:
            row["first_seen_ms"] = int(prev.get("first_seen_ms") or at_ms)
        row["last_seen_ms"] = int(at_ms)
        self._latest[key] = row
        return row


class _PatchedStore:
    """Koşu boyunca `structures.store.StructureStore` → `LightStructureStore` (çıkışta geri alınır)."""

    def __enter__(self) -> "_PatchedStore":
        from .structures import store as ST
        self._saved = ST.StructureStore
        ST.StructureStore = LightStructureStore
        return self

    def __exit__(self, *exc: Any) -> None:
        from .structures import store as ST
        ST.StructureStore = self._saved


# ---------------------------------------------------------------------------- simülasyon saati
class SimClock:
    """`strategy_paper.utc_now` (durum anlık görüntüsü, ret kaydı) simülasyon anına bağlanır — duvar saati yok."""

    def __init__(self):
        self.ms = 0

    def now(self) -> datetime:
        return _dt(self.ms)

    def __enter__(self) -> "SimClock":
        from . import strategy_paper as SP
        self._saved = SP.utc_now
        SP.utc_now = self.now
        return self

    def __exit__(self, *exc: Any) -> None:
        from . import strategy_paper as SP
        SP.utc_now = self._saved


def _snap(price: float, ts_ms: int) -> dict[str, Any]:
    """Canlı snapshot biçimi (`verified_price` girdisi): fiyat ve zamanı tur anı."""
    return {"funding": {"mark": float(price), "ts": int(ts_ms)}, "ts": int(ts_ms) / 1000.0}


def tour_marks(data: ArchiveData, symbols: list[str], day_ms: int, now_ms: int):
    """Motorun `_paper_marks` çıktısı biçiminde (tick, float, boşluk): mark = günün 00:00 1h açılışı, zaman = tur anı."""
    from .protective_monitor import live_tick
    from .strategy_paper import verified_price
    out, outf, gaps = {}, {}, {}
    for s in dict.fromkeys(symbols):
        px = data.tour_mark(s, day_ms) if s in data.h1 else None
        v = verified_price(_snap(px, now_ms) if px else {}, now_ms=int(now_ms))
        if not v["ok"]:
            gaps[s] = {"reason": v["reason"], "detail": v["detail"], "at": _iso_ms(now_ms), "price_ts": None,
                       "age_s": v["age_s"], "last_seen_mark": None}
            continue
        out[s] = live_tick(v)
        outf[s] = float(v["mark"])
    return out, outf, gaps


def close_tick(price: float, ts_ms: int):
    """Saatlik kontrol tiki: 1h kapanışı, zamanı kapanış anı (`verified_price` → `live_tick`)."""
    from .protective_monitor import live_tick
    from .strategy_paper import verified_price
    v = verified_price(_snap(price, ts_ms), now_ms=int(ts_ms))
    return live_tick(v) if v["ok"] else None


def bar_spec(rows: list[dict[str, Any]], mark: float, run_id: str) -> dict[str, Any]:
    """Motorun `_paper_closed_bars` sözlüğü."""
    return {"tf": "1h", "rows": rows, "mark": float(mark or 0.0), "market": "USDM_PERP", "source": "binance_usdm_archive",
            "tour_id": run_id, "first_bar_ms": rows[0]["timestamp"] if rows else 0}


# ---------------------------------------------------------------------------- KOL (a): M2 üreteci (§3.2 E1)
M2_BOOK = "m2_tsmom28"
BTC = "BTC/USDT"
TOUR_OFFSET_MS = 5 * 60_000            # karar turu 00:05 UTC


def load_sim_config(config_path: Path, run_dir: Path):
    """config.yaml → BotConfig; state ve önbellek SANDBOX'a (`run_dir`) taşınır — gerçek state/ asla açılmaz."""
    from .config import load_config
    cfg = load_config(config_path)
    cfg.state_dir = str(Path(run_dir).resolve() / "state")
    cfg.exchange.cache_dir = str(Path(run_dir).resolve() / "cache")
    return cfg


class M2Harness:
    """Gerçek `StrategyBook` (M2) sürücüsü — YALNIZ TESİSAT: çerçeve, mark, kapanmış 1h bar, saat ve funding verir; kural,
    yapı, öğrenme boyutu ve defter `StrategyBook`/`FuturesLedgerV2`/`paper_rules` içindedir (test: `test_harness_has_no_rule_logic`).

    Akış (gün başına): 00:05 karar turu — `lm.refresh()` + `lm.book(M2)` (motorun `_lm_refresh` yolu), `step` (kural +
    yapı + `apply_action`), `apply_closed_bars` (son 48 kapanmış 1h bar), `tick` (tur mark'ı), `reconcile_funding`; ardından
    saat başı (01:00 … 24:00) kapanan 1h bar `apply_closed_bars` + kapanış fiyatıyla `tick` (funding settlement dahil)."""

    def __init__(self, *, config_path: Path, data: ArchiveData, run_dir: Path, counterfactual: bool | None = False,
                 extra_entries: str | None = None, log: Callable[[str], None] | None = None, light_store: bool = True,
                 full_frames: bool = False):
        from dataclasses import replace as _replace
        from .learning_mode import LearningMode
        from .risk import KillSwitch, resolve_profile
        from .strategy_paper import StrategyBook, book_specs
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = load_sim_config(config_path, self.run_dir)
        v3 = self.cfg.v3
        self.data = data
        self.funding = ArchiveFunding(data)
        self.filters = SimFilters()
        self.clock = SimClock()
        self.log = log or (lambda m: None)
        self.light_store, self.full_frames = bool(light_store), bool(full_frames)
        spec = next((b for b in book_specs(v3) if b.name == M2_BOOK), None)
        if spec is None:
            raise ValueError("config'te etkin %s defteri yok" % M2_BOOK)
        self.profile = resolve_profile(v3.risk_profiles.profile, v3.risk_profiles.overrides,
                                       i_understand=v3.risk_profiles.i_understand)
        self.killswitch = KillSwitch.load(self.cfg.state_path / "killswitch.json")
        self.book = StrategyBook(self.cfg, profile=self.profile, killswitch=self.killswitch, filters_cache=self.filters,
                                 run_id="", spec=spec)
        self.book.bind_funding(self.funding)
        sec = v3.learning_mode
        kw: dict[str, Any] = {}
        if counterfactual is not None:
            kw["counterfactual"] = bool(counterfactual)
        if extra_entries is not None:
            kw["extra_entries"] = str(extra_entries)
        if kw:
            sec = _replace(sec, **kw)
        # ÖĞRENME GÖRÜNÜMÜ: motorun kurucusu (`LearningMode`), mod kapısı "aktif" ZORLANIR (ön kayıt §3.2).
        self.lm = LearningMode(sec, mode_gate=lambda: (True, "SIM_FORCED_ACTIVE"), clock=self.clock.now)
        eu = v3.entry_universe
        self.universe = list(eu.symbols) if eu.enabled else list(self.cfg.coins)
        self.events: list[dict[str, Any]] = []
        self.days: list[dict[str, Any]] = []
        self.hourly_equity: list[tuple[int, float]] = []
        self.opened: dict[str, dict[str, Any]] = {}
        self.closed_r_sum = 0.0
        self._phase = ""
        self._t = 0
        self._order = 0
        self._wrap()

    def _wrap(self) -> None:
        book = self.book
        orig_opened, orig_closed = book._on_opened, book._on_closed

        def on_opened(pos, act):
            orig_opened(pos, act)
            lr = (pos.meta or {}).get("learning") or {}
            self._order += 1
            ev = {"kind": "open", "t": self._t, "phase": self._phase, "id": str(pos.id), "symbol": pos.symbol,
                  "ref": float(pos.meta.get("ref_entry") or pos.entry_avg), "fill": float(pos.entry_avg),
                  "stop": float(pos.stop) if pos.stop is not None else None, "qty": float(pos.qty),
                  "leverage": int(pos.leverage), "liq": float(pos.liquidation_price) if pos.liquidation_price else None,
                  "order": self._order, "risk_usdt": lr.get("risk_usdt"), "size_rule": lr.get("size_rule"),
                  "unlocked_by": list(lr.get("learning_unlocked_by") or []), "signal_ts": act.get("signal_ts"),
                  "structure": (act.get("structure") or {}).get("action") if isinstance(act.get("structure"), dict) else None}
            self.events.append(ev)
            self.opened[str(pos.id)] = ev

        def on_closed(rec):
            orig_closed(rec)
            ef = (rec.features or {}).get("exit_fill") if isinstance(rec.features, dict) else None
            r = float(rec.r_multiple)
            self.closed_r_sum += r
            self.events.append({"kind": "close", "t": self._t, "phase": self._phase, "id": str(rec.id), "symbol": rec.symbol,
                                "reason": str(rec.exit_reason), "r": r, "net": float(rec.net_pnl),
                                "exit_price": float(rec.exit_price) if rec.exit_price is not None else None,
                                "closed_at": rec.closed_at, "basis": (ef or {}).get("basis"),
                                "funding": float(rec.funding), "fees": float(rec.fees)})

        book._on_opened, book._on_closed = on_opened, on_closed

    # ------------------------------------------------------------------ yardımcılar
    def _equity(self, marks_f: dict[str, float]) -> tuple[float, bool]:
        led = self.book.ledger
        e, ok = float(led.wallet_balance), True
        for s, p in led.positions.items():
            m = marks_f.get(s)
            if m is None:
                ok = False
                m = float(p.last_price or p.entry_avg)
            e += float(p.qty) * (float(m) - float(p.entry_avg))
        return e, ok

    def _u(self, marks_f: dict[str, float]) -> float | None:
        u = self.closed_r_sum
        for s, p in self.book.ledger.positions.items():
            m = marks_f.get(s)
            st = p.initial_stop if p.initial_stop is not None else p.stop
            if m is None or st is None or float(p.entry_avg) <= float(st):
                return None
            u += (float(m) - float(p.entry_avg)) / (float(p.entry_avg) - float(st))
        return u

    # ------------------------------------------------------------------ koşu
    def tour(self, day_ms: int) -> None:
        book, data = self.book, self.data
        now_ms = int(day_ms) + TOUR_OFFSET_MS
        self.clock.ms = now_ms
        now = _dt(now_ms)
        run_id = "sim-%s" % _stamp(day_ms, "%Y%m%d")
        self.lm.refresh()
        bl = self.lm.book(M2_BOOK)
        esh = bool(self.lm.structures_entry_shadow(M2_BOOK))
        scope = list(dict.fromkeys(self.universe + list(book.ledger.positions)))
        pmarks, pmarks_f, pgaps = tour_marks(data, scope, int(day_ms), now_ms)
        frames: dict[str, dict] = {}
        prov: dict[str, dict] = {}
        for s in dict.fromkeys(scope + [BTC]):
            fr = data.daily_frame(s, now_ms, full=self.full_frames) if s in data.d1 else None
            if fr is None or not len(fr):
                frames[s] = {}
                continue
            frames[s] = {"1d": fr}
            prov[s] = {"market": "USDM_PERP", "source": "binance_usdm_archive", "entry_ok": True, "tour_id": run_id,
                       "frames": {"1d": {"last_ts": int(fr["timestamp"].iloc[-1]), "n": int(len(fr))}}, "as_of_ms": now_ms}
        pbars = {s: bar_spec(data.h1[s].tail_rows(now_ms, 48), pmarks_f.get(s, 0.0), run_id)
                 for s in scope if s in data.h1 and s in prov and len(data.h1[s].t)}
        book.run_id = run_id
        self._t = now_ms
        self._phase = "step"
        book.step(symbols=list(self.universe), frames_by_symbol=frames, marks=pmarks, marks_f=pmarks_f, now=now,
                  provenance_by_symbol=prov, data_gaps=pgaps, learning=bl, structures_entry_shadow=esh)
        self._phase = "tour_bars"
        book.apply_closed_bars(pbars, now=now, funding_rate_lookup=self.funding)
        self._phase = "tour_tick"
        book.tick(pmarks, now=now, funding_rate_lookup=self.funding, bar_advance=False, apply_clock=lambda: now_ms)
        self._phase = "reconcile"
        book.reconcile_funding(now)
        e, ok = self._equity(pmarks_f)
        self.days.append({"day": int(day_ms), "t": now_ms, "equity": e, "marks_ok": ok, "u": self._u(pmarks_f),
                          "n_open": len(book.ledger.positions), "regime": book.regime,
                          "wallet": float(book.ledger.wallet_balance), "learning_on": bl is not None})

    def hour(self, t_ms: int) -> None:
        book, data = self.book, self.data
        self.clock.ms = int(t_ms)
        now = _dt(t_ms)
        held = list(book.ledger.positions)
        if not held:
            return
        bars, marks, marks_f = {}, {}, {}
        for s in held:
            b = data.h1[s].bar(int(t_ms) - HOUR_MS) if s in data.h1 else None
            if b is None:
                continue
            rows = data.h1[s].tail_rows(int(t_ms), 3)
            bars[s] = bar_spec(rows, b[3], book.run_id)
            td = close_tick(b[3], int(t_ms))
            if td is not None:
                marks[s], marks_f[s] = td, b[3]
        self._t = int(t_ms)
        self._phase = "hour_bars"
        book.apply_closed_bars(bars, now=now, funding_rate_lookup=self.funding)
        self._phase = "hour_tick"
        book.tick(marks, now=now, funding_rate_lookup=self.funding, bar_advance=False, apply_clock=lambda: int(t_ms))
        e, _ok = self._equity(marks_f)
        self.hourly_equity.append((int(t_ms), e))

    def run(self, start_ms: int = DECISION_START_MS, end_ms: int = DECISION_END_MS,
            progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        t0 = time.time()
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(self.clock)
            if self.light_store:
                stack.enter_context(_PatchedStore())
            day = int(start_ms)
            while day < int(end_ms):
                self.tour(day)
                for k in range(1, 25):
                    self.hour(day + k * HOUR_MS)
                if progress is not None and (len(self.days) % 30 == 0):
                    progress("M2 %s · açık %d · özkaynak %.2f · %.0f sn" % (_stamp(day, "%Y-%m-%d"), len(self.book.ledger.positions),
                                                                         self.days[-1]["equity"], time.time() - t0))
                day += DAY_MS
            self.final_marks = self._final_marks(int(end_ms))
        return self.stream()

    def _final_marks(self, end_ms: int) -> dict[str, float]:
        """Dönem sonu değerlemesi: son kapanmış 1h barın kapanışı (≤ end)."""
        out = {}
        for s in self.book.ledger.positions:
            rows = self.data.h1[s].tail_rows(end_ms, 1) if s in self.data.h1 else []
            if rows:
                out[s] = rows[-1]["close"]
        return out

    def stream(self) -> dict[str, Any]:
        """Kol (a)'nın işlem akışı (M2X kollarının ve bootstrap'ın girdisi) + M2'nin kendi defter kayıtları."""
        led = self.book.ledger
        trades = []
        for rec in led.history:
            trades.append({"id": str(rec.id), "symbol": rec.symbol, "opened_at": rec.opened_at, "closed_at": rec.closed_at,
                           "reason": str(rec.exit_reason), "r": float(rec.r_multiple), "net": float(rec.net_pnl),
                           "funding": float(rec.funding), "fees": float(rec.fees), "leverage": int(rec.leverage),
                           "entry": float(rec.entry), "qty": float(rec.quantity),
                           "risk_usdt": float((rec.features or {}).get("risk_usdt") or 0.0)})
        open_end = []
        for s, p in led.positions.items():
            m = getattr(self, "final_marks", {}).get(s)
            st = p.initial_stop if p.initial_stop is not None else p.stop
            open_end.append({"id": str(p.id), "symbol": s, "opened_at": p.opened_at, "entry": float(p.entry_avg),
                             "stop": float(st) if st is not None else None, "qty": float(p.qty), "mark_end": m,
                             "mark_r": ((float(m) - float(p.entry_avg)) / (float(p.entry_avg) - float(st)))
                             if (m is not None and st is not None and float(p.entry_avg) > float(st)) else None})
        return {"events": self.events, "days": self.days, "hourly_equity": self.hourly_equity, "trades": trades,
                "open_end": open_end, "final_marks": dict(getattr(self, "final_marks", {})),
                "wallet_end": float(led.wallet_balance), "starting_equity": float(led.starting_equity),
                "rejections": dict(self.book.rejections), "learning_counters": dict(self.book.learning_counters),
                "counters": dict(self.book.counters), "funding_source": self.funding.stats(),
                "total_fees": float(led.total_fees), "total_funding": float(led.total_funding),
                "universe": list(self.universe), "filters": FILTERS_ASSUMED,
                "daily_window": self.data.window,
                "learning": {"risk_pct": self.lm.risk_pct, "extra_entries": self.lm.extra_entries,
                             "counterfactual": self.lm.counterfactual,
                             "book": (self.lm.book(M2_BOOK).to_dict() if self.lm.book(M2_BOOK) else None)}}


# ---------------------------------------------------------------------------- M2X kolları (§3.4)


@dataclass(frozen=True)
class Arm:
    name: str
    knobs: P.Knobs
    role: str
    desc: str


_K = P.V1
ARMS: dict[str, Arm] = {a.name: a for a in [
    Arm("b", _K, "ÖN KAYITLI", "M2X v1: §2'nin tamamı"),
    Arm("p", P.Knobs(proportional_n=28), "ÖN KAYITLI", "orantılı kopya: risk = min(kademe riski, kademe OR tavanı / 28)"),
    Arm("c", P.Knobs(ladder=False, halt=False), "bilgi", "K1 sabit: %2, OR %20, kriz bütçesi, L_liq; kademe ve durdurma yok"),
    Arm("d", P.Knobs(ladder=False, halt=False, or_cap=False, crash_budget=False, slots=1), "bilgi",
        "%2; OR tavanı, kriz bütçesi, kademe, durdurma yok; pozisyon başı marj tavanı yok"),
    Arm("e", P.Knobs(or_cap_pct=(25.0,) + tuple(_K.or_cap_pct[1:])), "bilgi", "v1, K1 OR tavanı %25"),
    Arm("f", P.Knobs(or_measure="entry"), "bilgi", "v1, OR girişteki riskle"),
    Arm("g", P.Knobs(leverage_mode="l_need"), "bilgi", "v1, kaldıraç L_need"),
    Arm("h1", P.Knobs(halt_close=True), "bilgi", "v1 + DUR'da bütün pozisyonlar kapanır"),
    Arm("h2", P.Knobs(tier_down_shrink=True), "bilgi", "v1 + kademe düşüşünde orantılı küçültme"),
    Arm("i1", P.Knobs(peak_basis="wallet"), "bilgi", "v1, P ve Pₖ gerçekleşmiş özkaynaktan"),
    Arm("i2", P.Knobs(peak_basis="stop"), "bilgi", "v1, P ve Pₖ stop değerli özkaynaktan"),
    Arm("j", P.Knobs(key_b=False), "bilgi", "v1, yalnız A anahtarı"),
]}
BOOT_ARMS = ("b", "p", "c")


@dataclass(frozen=True)
class LedgerParams:
    """M2 ile AYNI defter parametreleri (config.yaml): ücret, kayma, likidasyon, TP1 payı."""
    maker_pct: float = 0.02
    taker_pct: float = 0.05
    fee_source: str = "config"
    slippage_bps: float = 3.0
    liq_fee_pct: float = 0.5
    tp1_fraction: float = 0.5

    @classmethod
    def from_v3(cls, v3) -> "LedgerParams":
        return cls(maker_pct=float(v3.fees.futures_maker_pct), taker_pct=float(v3.fees.futures_taker_pct),
                   fee_source=str(v3.fees.source), slippage_bps=float(v3.fees.slippage_bps),
                   liq_fee_pct=float(v3.futures_v3.liq_fee_pct), tp1_fraction=float(v3.futures_v3.tp1_fraction))

    def ledger(self, starting_equity: float, max_positions: int):
        from decimal import Decimal
        from .accounting import FeeSchedule, FuturesLedgerV2, LiquidationParams, SlippageModel, TaxPolicy, default_brackets
        return FuturesLedgerV2(starting_equity, max_positions=int(max_positions), enforce_position_cap=True,
                               fees=FeeSchedule(maker_pct=Decimal(repr(self.maker_pct)), taker_pct=Decimal(repr(self.taker_pct)),
                                                source=self.fee_source),
                               slippage=SlippageModel(fixed_bps=Decimal(repr(self.slippage_bps))), brackets=default_brackets(),
                               liq_params=LiquidationParams(liq_fee_pct=Decimal(repr(self.liq_fee_pct))),
                               tp1_fraction=Decimal(repr(self.tp1_fraction)), breakeven_at_mfe_r=Decimal(0),
                               tax_policy=TaxPolicy.disabled())


def _td(price: float, ts_ms: int):
    from decimal import Decimal
    from .accounting import TickData
    return TickData(last=Decimal(repr(float(price))), mark=Decimal(repr(float(price))), ts=_iso_ms(ts_ms))


def _filter_kw(filt) -> dict[str, float]:
    """`plan_entry`e borsa filtreleri — M2 defterinin `fit_size` çağrısıyla aynı (en küçük emir, miktar adımı, en küçük
    miktar; §2.6). 2026-10-05 düzeltmesi: adım ve en küçük miktar verilmiyordu; çıkarılmış notional defterde adıma AŞAĞI
    yuvarlanınca en küçük emrin altına (4,999… < 5) düşüyor ve her çıkarma defterce reddediliyordu."""
    return {"min_notional": float(filt.min_notional), "qty_step": float(filt.qty_step), "min_qty": float(filt.min_qty)}


def _size_spec(pl: Any):
    """Defter emri: çıkarılmış (BUMP) giriş planın adıma YUKARI yuvarlanmış MİKTARIyla açılır (§2.6; notional'dan miktarın
    yeniden türetilmesi aşağı yuvarlamayla en küçük emrin altına düşebilir). Diğer girişler notional'la (değişmedi)."""
    from decimal import Decimal
    from .accounting import AmountType, SizeSpec
    from .learning_mode import SIZE_BUMP
    if pl.size_rule == SIZE_BUMP:
        return SizeSpec(Decimal(repr(float(pl.qty))), AmountType.QUANTITY, int(pl.leverage))
    return SizeSpec(Decimal(repr(float(pl.notional))), AmountType.NOTIONAL, int(pl.leverage))


class M2xRunner:
    """M2X ayna defteri (§1.2–1.4, §2): M2'nin GERÇEKTEN açtığı işlemleri kendi `FuturesLedgerV2` defterinde `m2x_policy`
    boyutuyla kopyalar. Tur sırası §1.4: M2 kural çıkışını eşle → kapanmış 1h bar uçları → tik → yetim mutabakatı →
    gözlem (§2.7) → yeni girişler. Saat başı: bar uçları → tik → yetim mutabakatı.

    `tick_all=False` (bootstrap): turda yalnız `tick_syms` tiklenir ve saatlik olaylar yalnız M2'nin çıkış barlarıdır
    (seyrek besleme). Funding yokken PnL ve günlük özkaynak tam beslemeyle AYNIDIR (test: `test_sparse_feed_equivalence`)."""

    def __init__(self, arm: Arm, *, params: LedgerParams, funding: Any = None, filters: Any = None,
                 starting_equity: float | None = None, record_hourly: bool = True):
        self.arm, self.k = arm, arm.knobs
        se = float(starting_equity if starting_equity is not None else P.M2X_POLICY_V1["starting_equity_usdt"])
        self.ledger = params.ledger(se, int(P.M2X_POLICY_V1["max_positions"]))
        self.funding = funding
        if funding is not None:
            self.ledger.funding.bind_source(funding)
        else:
            self.ledger.funding.fallback_to_last_known = False
        self.filters = filters or SimFilters()
        self.st = P.PolicyState.start(se)
        self.parent: dict[str, str] = {}
        self.record_hourly = bool(record_hourly)
        self.snapshots: list[dict[str, Any]] = []
        self.hourly: list[tuple[int, float, float, float]] = []
        self.entries: list[dict[str, Any]] = []
        self.skips: list[dict[str, Any]] = []
        self.closes: list[dict[str, Any]] = []
        self.divergence: dict[str, int] = {}
        self.binding: dict[str, int] = {}
        self.max_or_e = self.max_cl_e = 0.0
        self.last_marks: dict[str, float] = {}

    # ------------------------------------------------------------------ ölçüler
    def _views(self, marks: dict[str, float]) -> list[P.PosView]:
        out = []
        for s, p in self.ledger.positions.items():
            m = marks.get(s, self.last_marks.get(s))
            if m is None:
                m = float(p.last_price or p.entry_avg)
            st = p.stop if p.stop is not None else p.initial_stop
            out.append(P.PosView(s, float(p.qty), float(p.entry_avg), float(st) if st is not None else None,
                                 float(p.liquidation_price) if p.liquidation_price else None, float(m)))
        return out

    def equity(self, marks: dict[str, float]) -> tuple[float, bool]:
        e, ok = float(self.ledger.wallet_balance), True
        for s, p in self.ledger.positions.items():
            m = marks.get(s)
            if m is None:
                ok = False
                m = self.last_marks.get(s, float(p.last_price or p.entry_avg))
            e += float(p.qty) * (float(m) - float(p.entry_avg))
        return e, ok

    def _x(self, e: float) -> float:
        """DD ölçü özkaynağı (`Knobs.peak_basis`)."""
        if self.k.peak_basis == "wallet":
            return float(self.ledger.wallet_balance)
        if self.k.peak_basis == "stop":
            x = float(self.ledger.wallet_balance)
            for p in self.ledger.positions.values():
                st = p.stop if p.stop is not None else p.initial_stop
                if st is not None:
                    x += float(p.qty) * (float(st) - float(p.entry_avg))
            return x
        return e

    def _closed(self, recs, t_ms: int, how: str) -> None:
        for rec in recs or []:
            mid = self.parent.pop(rec.symbol, None)
            self.closes.append({"id": str(rec.id), "m2_id": mid, "symbol": rec.symbol, "t": int(t_ms), "how": how,
                                "reason": str(rec.exit_reason), "r": float(rec.r_multiple), "net": float(rec.net_pnl),
                                "funding": float(rec.funding), "fees": float(rec.fees), "leverage": int(rec.leverage),
                                "qty": float(rec.quantity), "entry": float(rec.entry),
                                "basis": ((rec.features or {}).get("exit_fill") or {}).get("basis")})

    def _skip(self, cand: dict[str, Any], reason: str, t_ms: int, **extra: Any) -> None:
        self.skips.append({"m2_id": cand["id"], "symbol": cand["symbol"], "t": int(t_ms), "reason": reason,
                           "tier": self.st.tier_name, **extra})

    def _orphans(self, m2_open: dict[str, str], prices: dict[str, float], t_ms: int) -> None:
        for s in list(self.ledger.positions):
            if m2_open.get(s) == self.parent.get(s):
                continue
            px = prices.get(s)
            if px is None:
                continue                       # fiyat gelince kapanır (bekleyen yetim)
            rec = self.ledger.close_manual(s, px, reason="MIRROR_ORPHAN_CLOSED", now=_dt(t_ms), tick=_td(px, t_ms))
            self.divergence["orphan"] = self.divergence.get("orphan", 0) + 1
            self._closed([rec] if rec is not None else [], t_ms, "orphan")

    # ------------------------------------------------------------------ tur
    def on_tour(self, t_ms: int, *, marks: dict[str, float], rule_closes: list[tuple], opens: list[dict[str, Any]],
                m2_open: dict[str, str], u: float | None, bars: dict[str, dict] | None = None,
                tick_syms: set[str] | None = None, new_entries: bool = True) -> None:
        from .strategy_paper import apply_closed_bars_to_ledger
        now = _dt(t_ms)
        led = self.ledger
        # 1) M2'nin kural/yapı çıkışı: aynı mark'la
        for mid, sym, price, reason in rule_closes:
            if self.parent.get(sym) == mid and sym in led.positions:
                rec = led.close_manual(sym, price, reason="MIRROR_PARENT_CLOSED:%s" % reason, now=now, tick=_td(price, t_ms))
                self._closed([rec] if rec is not None else [], t_ms, "mirror")
        # 2) kapanmış 1h bar uçları
        if bars:
            recs = apply_closed_bars_to_ledger(led, {s: b for s, b in bars.items() if s in led.positions}, now=now,
                                               funding_rate_lookup=self.funding)
            self._closed(recs, t_ms, "bar")
        # 3) tik (tur mark'ı)
        tmarks = {s: _td(marks[s], t_ms) for s in list(led.positions)
                  if s in marks and (tick_syms is None or s in tick_syms)}
        if tmarks:
            self._closed(led.tick(tmarks, now_utc=now, funding_rate_lookup=self.funding, bar_advance=False), t_ms, "tick")
        self.last_marks.update({s: float(m) for s, m in marks.items()})
        # 4) yetim mutabakatı
        self._orphans(m2_open, marks, t_ms)
        # 5) gözlem (O1 = O2: tek günlük tur)
        e, ok = self.equity(marks)
        x = self._x(e)
        at = _iso_ms(t_ms)
        before_tier, before_halt = self.st.tier, self.st.halted
        evs = P.observe(self.st, x=x, kind=P.SNAPSHOT, at=at, u=u, data_gap=not ok, knobs=self.k)
        views = self._views(marks)
        if any(ev["kind"] in ("DOWN", "HALT") for ev in evs):
            if self.k.halt_close and self.st.halted and not before_halt:
                for s in list(led.positions):
                    if s in marks:
                        rec = led.close_manual(s, marks[s], reason="M2X_HALT_CLOSE", now=now, tick=_td(marks[s], t_ms))
                        self._closed([rec] if rec is not None else [], t_ms, "halt_close")
            elif self.k.tier_down_shrink and (self.st.tier > before_tier or (self.st.halted and not before_halt)):
                o, c = P.totals(views, measure=self.k.or_measure)
                cap = None if (self.st.halted or self.st.tier >= P.SOFT_HALT_TIER) else float(self.k.or_cap_pct[self.st.tier])
                f = P.shrink_fraction(equity=e, peak=self.st.peak, open_risk=o, crash_loss=c, or_cap_pct=cap, knobs=self.k)
                if f < 1.0 - 1e-9:
                    for s in list(led.positions):
                        if s in marks:
                            rec = led.close_partial(s, marks[s], 1.0 - f, reason="M2X_TIER_SHRINK", now=now)
                            self._closed([rec] if rec is not None else [], t_ms, "shrink")
                    self.divergence["tier_shrink_events"] = self.divergence.get("tier_shrink_events", 0) + 1
            e, ok = self.equity(marks)
            views = self._views(marks)
        o_now, c_now = P.totals(views, measure=self.k.or_measure)
        if e > 0:
            self.max_or_e = max(self.max_or_e, o_now / e)
            self.max_cl_e = max(self.max_cl_e, c_now / e)
        # 6) yeni girişler
        binding = "-"
        n_new = 0
        if opens:
            binding, n_new = self._entries(t_ms, marks=marks, opens=opens, m2_open=m2_open, e=e, data_gap=not ok,
                                           o_now=o_now, c_now=c_now, new_entries=new_entries)
        e2, _ = self.equity(marks)
        o2, c2 = P.totals(self._views(marks), measure=self.k.or_measure)
        self.snapshots.append({"t": int(t_ms), "equity": e, "equity_after": e2, "x": x, "wallet": float(led.wallet_balance),
                               "tier": self.st.tier_name, "peak": self.st.peak, "tier_peak": self.st.tier_peak,
                               "dd": self.st.dd(x), "ddk": self.st.ddk(x), "or": o2, "cl": c2, "n_pos": len(led.positions),
                               "gap": not ok, "binding": binding, "n_new": n_new, "streak": self.st.streak,
                               "events": [ev["kind"] for ev in evs]})

    def _entries(self, t_ms: int, *, marks, opens, m2_open, e, data_gap, o_now, c_now, new_entries) -> tuple[str, int]:
        from decimal import Decimal
        led = self.ledger
        alive = []
        for c in sorted(opens, key=lambda x: int(x.get("order") or 0)):
            if m2_open.get(c["symbol"]) != c["id"]:
                self._skip(c, P.SKIP_PARENT_CLOSED, t_ms)
            elif c["symbol"] in led.positions:
                self._skip(c, "M2X_SYMBOL_HELD", t_ms)
            else:
                alive.append(c)
        if not alive:
            return "-", 0
        gate = P.entry_gate(self.st, new_entries=new_entries)
        if gate is None and data_gap:
            gate = P.SKIP_DATA_GAP
        if gate is not None:
            for c in alive:
                self._skip(c, gate, t_ms)
            return "GATE", 0
        tier = self.st.tier
        r_pct = self.k.tier_risk_pct(tier)
        d = r_pct / 100.0 * e
        cap_pct = float(self.k.or_cap_pct[tier])
        h_or, h_cl = P.gaps(equity=e, peak=self.st.peak, open_risk=o_now, crash_loss=c_now, or_cap_pct=cap_pct, knobs=self.k)
        full: list[tuple[dict, float, Any]] = []
        for c in alive:
            filt = self.filters.get(c["symbol"])
            fill = float(led.market_fill_price(c["symbol"], "LONG", Decimal(repr(float(c["ref"]))), filters=filt))
            pl = P.plan_entry(equity=e, fill=fill, stop=float(c["stop"]), risk_usdt=d, tier_risk_pct=r_pct, available=1e18,
                              knobs=self.k, **_filter_kw(filt))
            if not pl.ok:
                self._skip(c, pl.reason or "M2X_PLAN", t_ms, stage="full")
                continue
            full.append((c, fill, pl))
        if not full:
            return "PLAN", 0
        cands = [(d, P.crash_loss_one(pl.qty, float(c["ref"]), pl.liq)) for c, _f, pl in full]
        lam, alloc = P.allocate(cands, h_or, h_cl)
        so, sc = sum(x[0] for x in cands), sum(x[1] for x in cands)
        lo = h_or / so if (so > 0 and math.isfinite(h_or)) else math.inf
        lc = h_cl / sc if (sc > 0 and math.isfinite(h_cl)) else math.inf
        binding = "-" if lam >= 1.0 - 1e-12 else ("OR" if lo <= lc else "CL")
        n_new = 0
        for (c, fill, _pl), (scale, why) in zip(full, alloc):
            if scale is None:
                self._skip(c, why or P.SKIP_OR, t_ms, lam=lam)
                continue
            filt = self.filters.get(c["symbol"])
            pl = P.plan_entry(equity=e, fill=fill, stop=float(c["stop"]), risk_usdt=scale * d, tier_risk_pct=r_pct,
                              available=float(led.available), knobs=self.k,
                              min_risk_usdt=float(P.M2X_POLICY_V1["min_scale"]) * d, **_filter_kw(filt))
            if not pl.ok:
                if pl.reason == P.SKIP_MARGIN:
                    binding = "MARGIN"
                self._skip(c, pl.reason or "M2X_PLAN", t_ms, lam=lam)
                continue
            o_new = P.open_risk_one(pl.qty, float(c["ref"]), float(c["stop"]), entry=fill, measure=self.k.or_measure)
            c_new = P.crash_loss_one(pl.qty, float(c["ref"]), pl.liq)
            if o_new > h_or + 1e-9:
                self._skip(c, P.SKIP_OR, t_ms, lam=lam)
                continue
            if c_new > h_cl + 1e-9:
                self._skip(c, P.SKIP_CL, t_ms, lam=lam)
                continue
            pos = led.open(c["symbol"], "LONG", Decimal(repr(float(c["ref"]))), _size_spec(pl),
                           stop=Decimal(repr(float(c["stop"]))), filters=filt, tick=_td(c["ref"], t_ms), now=_dt(t_ms),
                           setup_type="trend", trigger_text="M2X_MIRROR", features={"m2x": {"m2_id": c["id"]}},
                           meta={"m2x": {"m2_id": c["id"], "tier": self.st.tier_name}}, allow_shrink=True)
            if pos is None:
                self._skip(c, "M2X_LEDGER_%s" % (led.last_reject_reason or "REJECT"), t_ms, lam=lam)
                continue
            self.parent[c["symbol"]] = c["id"]
            o_act = P.open_risk_one(float(pos.qty), float(c["ref"]), float(c["stop"]), entry=float(pos.entry_avg),
                                    measure=self.k.or_measure)
            c_act = P.crash_loss_one(float(pos.qty), float(c["ref"]),
                                     float(pos.liquidation_price) if pos.liquidation_price else None)
            h_or -= o_act
            h_cl -= c_act
            n_new += 1
            self.entries.append({"m2_id": c["id"], "symbol": c["symbol"], "t": int(t_ms), "tier": self.st.tier_name,
                                 "scale": float(scale), "lam": float(lam), "d": d,
                                 "risk_usdt": float(pos.qty) * (float(pos.entry_avg) - float(c["stop"])),
                                 "full": bool(scale >= 1.0 - 1e-9), "size_rule": pl.size_rule, "leverage": int(pos.leverage),
                                 "l_need": pl.l_need, "l_liq": pl.l_liq, "lev_reduced": pl.lev_reduced,
                                 "shrunk_by_ledger": isinstance(pos.meta.get("shrunk_to_margin"), dict)})
        self.binding[binding] = self.binding.get(binding, 0) + 1
        return binding, n_new

    # ------------------------------------------------------------------ saat başı
    def on_hour(self, t_ms: int, *, bars: dict[str, dict], closes: dict[str, float], m2_open: dict[str, str]) -> None:
        from .strategy_paper import apply_closed_bars_to_ledger
        led = self.ledger
        if not led.positions:
            return
        now = _dt(t_ms)
        if bars:
            recs = apply_closed_bars_to_ledger(led, {s: b for s, b in bars.items() if s in led.positions}, now=now,
                                               funding_rate_lookup=self.funding)
            self._closed(recs, t_ms, "bar")
        tm = {s: _td(px, t_ms) for s, px in closes.items() if s in led.positions}
        if tm:
            self._closed(led.tick(tm, now_utc=now, funding_rate_lookup=self.funding, bar_advance=False), t_ms, "tick")
        self.last_marks.update({s: float(px) for s, px in closes.items()})
        self._orphans(m2_open, closes, t_ms)
        if self.record_hourly:
            views = self._views(closes)
            e = float(led.wallet_balance) + sum(v.qty * (v.mark - v.entry) for v in views)
            o, c = P.totals(views, measure=self.k.or_measure)
            self.hourly.append((int(t_ms), e, c, o))

    def summary_state(self) -> dict[str, Any]:
        return {"tier": self.st.tier_name, "halted": self.st.halted, "halted_at": self.st.halted_at,
                "peak": self.st.peak, "tier_peak": self.st.tier_peak, "events": list(self.st.events)}


# ---------------------------------------------------------------------------- tarihsel M2X koşusu (kol (a) akışı üstünde)
def stream_index(stream: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for e in stream["events"]:
        out.setdefault(int(e["t"]), []).append(e)
    return out


def _apply_m2(m2_open: dict[str, str], evs: list[dict[str, Any]]) -> None:
    for e in evs:
        if e["kind"] == "open":
            m2_open[e["symbol"]] = e["id"]
        elif e["kind"] == "close" and m2_open.get(e["symbol"]) == e["id"]:
            m2_open.pop(e["symbol"], None)


def run_historical_arm(arm: Arm, stream: dict[str, Any], data: ArchiveData, params: LedgerParams,
                       progress: Callable[[str], None] | None = None, *, with_funding: bool = True,
                       end_ms: int = DECISION_END_MS) -> dict[str, Any]:
    """Bir M2X kolunu kol (a)'nın işlem akışı üstünde, arşivin 1h barları ve funding'iyle koşar (tam besleme)."""
    funding = ArchiveFunding(data) if with_funding else None
    run = M2xRunner(arm, params=params, funding=funding, filters=SimFilters(), record_hourly=True)
    by_t = stream_index(stream)
    m2_open: dict[str, str] = {}
    t0 = time.time()
    for n, drec in enumerate(stream["days"]):
        day, T = int(drec["day"]), int(drec["t"])
        evs = by_t.get(T, [])
        rule = [(e["id"], e["symbol"], data.tour_mark(e["symbol"], day), e["reason"]) for e in evs
                if e["kind"] == "close" and e["phase"] == "step"]
        rule = [r for r in rule if r[2] is not None]
        opens = [e for e in evs if e["kind"] == "open"]
        _apply_m2(m2_open, evs)
        need = set(run.ledger.positions) | {o["symbol"] for o in opens} | {r[1] for r in rule}
        marks = {s: m for s in need if (m := data.tour_mark(s, day)) is not None}
        run_id = "sim-%s" % _stamp(day, "%Y%m%d")
        bars = {s: bar_spec(data.h1[s].tail_rows(T, 48), marks.get(s, 0.0), run_id) for s in run.ledger.positions}
        run.on_tour(T, marks=marks, rule_closes=rule, opens=opens, m2_open=dict(m2_open), u=drec.get("u"), bars=bars)
        for k in range(1, 25):
            t = day + k * HOUR_MS
            _apply_m2(m2_open, by_t.get(t, []))
            if not run.ledger.positions:
                continue
            hb, hc = {}, {}
            for s in run.ledger.positions:
                b = data.h1[s].bar(t - HOUR_MS)
                if b is None:
                    continue
                hb[s] = bar_spec(data.h1[s].tail_rows(t, 3), b[3], run_id)
                hc[s] = b[3]
            run.on_hour(t, bars=hb, closes=hc, m2_open=dict(m2_open))
        if progress is not None and n % 90 == 0:
            progress("%s %s · %s · E %.2f · açık %d · %.0f sn" % (arm.name, _stamp(day, "%Y-%m-%d"), run.st.tier_name,
                                                                run.snapshots[-1]["equity"], len(run.ledger.positions), time.time() - t0))
    final_marks = {s: data.h1[s].tail_rows(end_ms, 1)[-1]["close"] for s in run.ledger.positions
                   if data.h1[s].tail_rows(end_ms, 1)}
    e_end, _ok = run.equity(final_marks)
    return {"arm": arm.name, "role": arm.role, "desc": arm.desc, "knobs": P.knobs_dict(arm.knobs),
            "snapshots": run.snapshots, "hourly": run.hourly, "entries": run.entries, "skips": run.skips,
            "closes": run.closes, "divergence": dict(run.divergence), "binding": dict(run.binding),
            "max_or_e": run.max_or_e, "max_cl_e": run.max_cl_e, "state": run.summary_state(),
            "final": {"t": int(end_ms), "equity": e_end, "open": len(run.ledger.positions)},
            "total_fees": float(run.ledger.total_fees), "total_funding": float(run.ledger.total_funding),
            "funding_source": funding.stats() if funding is not None else None, "seconds": round(time.time() - t0, 1)}


# ---------------------------------------------------------------------------- ölçüler (§3.5)
def equity_series(points: list[tuple[int, float]]) -> tuple[np.ndarray, np.ndarray]:
    t = np.array([int(p[0]) for p in points], dtype=np.int64)
    e = np.array([float(p[1]) for p in points], dtype=float)
    return t, e


def max_drawdown(e: np.ndarray) -> float:
    if not len(e):
        return 0.0
    peak = np.maximum.accumulate(e)
    return float(np.max(1.0 - e / peak))


def underwater(t: np.ndarray, e: np.ndarray) -> dict[str, Any]:
    """Su altı: en uzun ve toplam gün (zirvenin altında geçen), tamamlanan bölümlerin ortalama toparlanma süresi."""
    if not len(e):
        return {}
    peak = np.maximum.accumulate(e)
    under = e < peak - 1e-12
    longest = total = 0
    rec: list[float] = []
    start = None
    for i in range(len(e)):
        if under[i]:
            if start is None:
                start = i - 1 if i > 0 else i
        elif start is not None:
            dur = (int(t[i]) - int(t[start])) / DAY_MS
            rec.append(dur)
            longest = max(longest, dur)
            start = None
    if start is not None:
        longest = max(longest, (int(t[-1]) - int(t[start])) / DAY_MS)
    total = float(under.sum()) * (float(np.median(np.diff(t))) / DAY_MS if len(t) > 1 else 1.0)
    return {"longest_days": round(float(longest), 1), "total_days": round(total, 1),
            "mean_recovery_days": round(float(np.mean(rec)), 1) if rec else None, "episodes_recovered": len(rec),
            "underwater_at_end": bool(under[-1])}


def month_key(ms: int) -> str:
    return _stamp(ms, "%Y-%m")


def period_returns(t: np.ndarray, e: np.ndarray, *, fmt: str) -> list[dict[str, Any]]:
    """Takvim dönemi getirileri: dönemin ilk görüntüsünden bir sonrakinin ilk görüntüsüne (son dönem: son nokta)."""
    keys = [_stamp(int(x), fmt) if fmt != "Q" else "%s-Q%d" % (_stamp(int(x), "%Y"), (int(_stamp(int(x), "%m")) - 1) // 3 + 1)
            for x in t]
    out, first = [], {}
    order: list[str] = []
    for i, k in enumerate(keys):
        if k not in first:
            first[k] = i
            order.append(k)
    for j, k in enumerate(order):
        i0 = first[k]
        i1 = first[order[j + 1]] if j + 1 < len(order) else len(e) - 1
        if i1 <= i0:
            continue
        out.append({"period": k, "return": float(e[i1] / e[i0] - 1.0), "start_equity": float(e[i0]), "end_equity": float(e[i1])})
    return out


def series_metrics(points: list[tuple[int, float]], *, hourly: list[tuple[int, float]] | None = None) -> dict[str, Any]:
    """Günlük görüntü özkaynağından (mark'a göre) bütün ön kayıtlı getiri ve düşüş ölçüleri."""
    t, e = equity_series(points)
    if len(e) < 2:
        return {}
    r = e[1:] / e[:-1] - 1.0
    months = period_returns(t, e, fmt="%Y-%m")
    mret = np.array([m["return"] for m in months])
    quarters = period_returns(t, e, fmt="Q")
    days = (int(t[-1]) - int(t[0])) / DAY_MS
    total = float(e[-1] / e[0] - 1.0)
    out: dict[str, Any] = {
        "start_equity": float(e[0]), "end_equity": float(e[-1]), "total_return": total,
        "cagr": float((e[-1] / e[0]) ** (365.25 / days) - 1.0) if days > 0 and e[-1] > 0 else None,
        "days": round(days, 1),
        "daily": {"n": int(len(r)), "share_ge_1pct": float(np.mean(r >= 0.01)), "share_le_m1pct": float(np.mean(r <= -0.01)),
                  "median": float(np.median(r)), "mean": float(np.mean(r)), "worst": float(np.min(r)),
                  "worst_at": _iso_ms(int(t[1:][int(np.argmin(r))])), "best": float(np.max(r))},
        "monthly": {"n": int(len(mret)), "mean": float(np.mean(mret)), "median": float(np.median(mret)),
                    "worst": float(np.min(mret)), "worst_month": months[int(np.argmin(mret))]["period"],
                    "best": float(np.max(mret)), "best_month": months[int(np.argmax(mret))]["period"],
                    "share_ge_1pct": float(np.mean(mret >= 0.01)), "share_ge_0": float(np.mean(mret >= 0.0)),
                    "share_le_m10pct": float(np.mean(mret <= -0.10))},
        "months": months, "quarters": quarters,
        "max_dd_daily": max_drawdown(e), "underwater": underwater(t, e)}
    if hourly:
        ht, he = equity_series(sorted(list(hourly) + list(points)))
        out["max_dd_hourly"] = max_drawdown(he)
    subs = {}
    for name, a, b in (("2023", "2023-01-01", "2024-01-01"), ("2024", "2024-01-01", "2025-01-01"),
                       ("2025", "2025-01-01", "2026-01-01"), ("2026-01..09", "2026-01-01", "2026-10-02")):
        m = (t >= _ms(a)) & (t <= _ms(b))
        if m.sum() >= 2:
            ee = e[m]
            mm = [x["return"] for x in months if _ms(a) <= _ms(x["period"] + "-01") < _ms(b)]
            subs[name] = {"return": float(ee[-1] / ee[0] - 1.0), "max_dd": max_drawdown(ee),
                          "monthly_mean": float(np.mean(mm)) if mm else None,
                          "monthly_median": float(np.median(mm)) if mm else None}
    out["subperiods"] = subs
    return out


# ---------------------------------------------------------------------------- politika-tekrar bootstrap'ı (§3.6)
BOOT_SEED = 20261005
BOOT_PATHS = 2000
BOOT_BLOCK = 20
BOOT_BLOCK_SENS = (5, 60)
BOOT_HORIZON = 365
BOOT_MONTH_DAYS = 30                   # sentetik yolda takvim ayı yok: ardışık 30 günlük dilimler (okunuş)
BOOT_BASE_MS = _ms("2001-01-01")       # sentetik zaman ekseni (gerçek takvimle karışmasın)


def _exit_spec(c: dict[str, Any] | None) -> tuple[str, int]:
    """M2 kapanış olayı → (tür, an): rule/tick → tur anı; bar → barın açılışı (kapanış anı − 1h); htick → saat."""
    if c is None:
        return "end", 0
    ph = c["phase"]
    if ph == "step":
        return "rule", int(c["t"])
    if ph == "tour_tick":
        return "tick", int(c["t"])
    if ph in ("hour_bars", "tour_bars"):
        from .strategy_paper import parse_ts_ms
        closed = parse_ts_ms(c.get("closed_at")) or int(c["t"])
        return "bar", int(closed) - HOUR_MS
    return "htick", int(c["t"])


def trade_library(stream: dict[str, Any], data: ArchiveData, *, start_ms: int = DECISION_START_MS,
                  end_ms: int = DECISION_END_MS) -> dict[str, Any]:
    """Kol (a)'nın işlem yolları (§3.6 girdi): her M2 işlemi için giriş günü, M2'deki sıra, stop, çıkış olayı, girişe
    normalize saatlik OHLC yolu (giriş gününün 00:00 barından çıkışa kadar), M2'deki net R ve günlük mark R'si."""
    closes = {e["id"]: e for e in stream["events"] if e["kind"] == "close"}
    open_end = {o["id"]: o for o in stream.get("open_end") or []}
    trades: list[dict[str, Any]] = []
    n_days = int((end_ms - start_ms) // DAY_MS)
    for e in [x for x in stream["events"] if x["kind"] == "open"]:
        sym, ref, fill, stop = e["symbol"], float(e["ref"]), float(e["fill"]), float(e["stop"])
        day0 = int(e["t"]) - TOUR_OFFSET_MS
        di = int((day0 - start_ms) // DAY_MS)
        if not (0 <= di < n_days) or not (stop < fill):
            continue
        c = closes.get(e["id"])
        kind, at = _exit_spec(c)
        h = data.h1[sym]
        i0 = int(np.searchsorted(h.t, day0))
        if kind in ("rule", "tick"):
            last_bar = at - TOUR_OFFSET_MS                         # o günün 00:00 barı (mark = açılışı)
        elif kind == "bar":
            last_bar = at
        elif kind == "htick":
            last_bar = at - HOUR_MS
        else:
            last_bar = end_ms - HOUR_MS
        i1 = int(np.searchsorted(h.t, last_bar, side="right"))
        tt = h.t[i0:i1]
        if not len(tt) or int(tt[0]) != day0:
            continue                                                # giriş gününün 00:00 barı yok → yol kurulamaz
        hrs = ((tt - day0) // HOUR_MS).astype(np.int32)
        r = float(c["r"]) if c is not None else float((open_end.get(e["id"]) or {}).get("mark_r") or 0.0)
        if kind == "end":
            # örnek sonunda açık: dönem sonunda (son kapanış) M2 çıkışı sayılır (işaretli)
            ex_day = int((end_ms - day0) // DAY_MS)
            ex_hour = None
        elif kind in ("rule", "tick"):
            ex_day, ex_hour = int((at - TOUR_OFFSET_MS - day0) // DAY_MS), None
        else:
            ex_day, ex_hour = None, int((at - day0) // HOUR_MS)
        trades.append({"sym": sym, "day": di, "order": int(e["order"]), "m2_id": e["id"], "ref": ref,
                       "stop": stop / ref, "fill": fill / ref, "r": r, "kind": kind, "ex_day": ex_day, "ex_hour": ex_hour,
                       "reason": (c or {}).get("reason") or "SAMPLE_END",
                       "hrs": hrs, "o": (h.o[i0:i1] / ref).astype(float), "h": (h.h[i0:i1] / ref).astype(float),
                       "l": (h.l[i0:i1] / ref).astype(float), "c": (h.c[i0:i1] / ref).astype(float)})
    by_day: list[list[int]] = [[] for _ in range(n_days)]
    for i, tr in enumerate(trades):
        by_day[tr["day"]].append(i)
    for lst in by_day:
        lst.sort(key=lambda i: trades[i]["order"])
    return {"trades": trades, "by_day": by_day, "n_days": n_days}


class _Inst:
    """Bootstrap yolunda bir M2 işleminin örneği (sentetik sembol)."""

    __slots__ = ("sym", "tr", "k0", "exit_t", "marks", "hour_idx")

    def __init__(self, sym: str, tr: dict[str, Any], k0: int):
        self.sym, self.tr, self.k0 = sym, tr, k0
        self.hour_idx = {int(x): i for i, x in enumerate(tr["hrs"])}
        base = BOOT_BASE_MS + k0 * DAY_MS
        if tr["kind"] in ("rule", "tick", "end"):
            self.exit_t = base + int(tr["ex_day"]) * DAY_MS + TOUR_OFFSET_MS
        elif tr["kind"] == "bar":
            self.exit_t = base + (int(tr["ex_hour"]) + 1) * HOUR_MS
        else:
            self.exit_t = base + int(tr["ex_hour"]) * HOUR_MS

    def mark(self, off_day: int) -> float | None:
        i = self.hour_idx.get(int(off_day) * 24)
        if i is not None:
            return float(self.tr["o"][i])
        if self.tr["kind"] == "end" and int(off_day) >= int(self.tr["ex_day"]):
            return float(self.tr["c"][-1])
        return None

    def mark_r(self, m: float) -> float:
        f, s = self.tr["fill"], self.tr["stop"]
        return (m - f) / (f - s)


class _Alive:
    def __init__(self):
        self.s: set[str] = set()

    def get(self, sym: str, default: Any = None) -> Any:
        return sym if sym in self.s else default


def run_boot_path(lib: dict[str, Any], starts: np.ndarray, *, block: int, horizon: int, arm: Arm, params: LedgerParams,
                  keep_series: bool = False) -> dict[str, Any]:
    """Tek bootstrap yolu: blok başlangıçlarından sentetik işlem akışı → `M2xRunner` (seyrek besleme, funding yok)."""
    trades, by_day, N = lib["trades"], lib["by_day"], int(lib["n_days"])
    starts_k: dict[int, list[_Inst]] = {}
    for j, s0 in enumerate(starts):
        for i in range(int(block)):
            k = j * int(block) + i
            if k >= horizon:
                break
            for ti in by_day[(int(s0) + i) % N]:
                starts_k.setdefault(k, []).append(_Inst("T%d_%d" % (ti, k), trades[ti], k))
    run = M2xRunner(arm, params=params, funding=None, filters=SimFilters(), record_hourly=False)
    alive = _Alive()
    active: dict[str, _Inst] = {}
    closed_sum = 0.0
    hour_ev: dict[int, list[_Inst]] = {}
    for k in range(int(horizon)):
        T = BOOT_BASE_MS + k * DAY_MS + TOUR_OFFSET_MS
        new = starts_k.get(k, [])
        for ins in new:
            active[ins.sym] = ins
            alive.s.add(ins.sym)
            if ins.tr["kind"] in ("bar", "htick") and ins.exit_t < BOOT_BASE_MS + horizon * DAY_MS:
                hour_ev.setdefault(ins.exit_t, []).append(ins)
        marks: dict[str, float] = {}
        rule, tick_syms, done = [], set(), []
        for sym, ins in active.items():
            m = ins.mark(k - ins.k0)
            if m is not None:
                marks[sym] = m
            if ins.exit_t == T:
                done.append(ins)
                if ins.tr["kind"] in ("rule", "end"):
                    rule.append((sym, sym, m if m is not None else float(ins.tr["c"][-1]), ins.tr["reason"]))
                else:
                    tick_syms.add(sym)
        for ins in done:
            alive.s.discard(ins.sym)
            closed_sum += float(ins.tr["r"])
            active.pop(ins.sym, None)
        u = closed_sum + sum(ins.mark_r(marks[s]) for s, ins in active.items() if s in marks)
        opens = [{"id": ins.sym, "symbol": ins.sym, "ref": 1.0, "stop": ins.tr["stop"], "order": n}
                 for n, ins in enumerate(new) if ins.sym in alive.s]
        # aynı turda açılıp kapanan (M2'de) örnekler: aday olarak gelir, M2X'te PARENT_ALREADY_CLOSED
        opens += [{"id": ins.sym, "symbol": ins.sym, "ref": 1.0, "stop": ins.tr["stop"], "order": 10_000 + n}
                  for n, ins in enumerate(new) if ins.sym not in alive.s]
        run.on_tour(T, marks=marks, rule_closes=rule, opens=opens, m2_open=alive, u=u, bars=None, tick_syms=tick_syms)
        for h in range(1, 25):
            t = BOOT_BASE_MS + k * DAY_MS + h * HOUR_MS
            evs = hour_ev.pop(t, None)
            if not evs:
                continue
            bars, closes = {}, {}
            for ins in evs:
                alive.s.discard(ins.sym)
                closed_sum += float(ins.tr["r"])
                active.pop(ins.sym, None)
                if ins.sym not in run.ledger.positions:
                    continue
                hi = int((t - BOOT_BASE_MS - ins.k0 * DAY_MS) // HOUR_MS) - 1
                i = ins.hour_idx.get(hi if ins.tr["kind"] == "bar" else hi)
                if i is None:
                    continue
                o, hh, lo, c = (float(ins.tr[x][i]) for x in ("o", "h", "l", "c"))
                if ins.tr["kind"] == "bar":
                    bars[ins.sym] = bar_spec([{"timestamp": t - HOUR_MS, "open": o, "high": hh, "low": lo, "close": c}], c, "boot")
                closes[ins.sym] = c
            if bars or closes:
                run.on_hour(t, bars=bars, closes=closes, m2_open=alive)
    # ufuk sonu: açık pozisyonlar ufuk gününün mark'ıyla
    fm = {}
    for sym in run.ledger.positions:
        ins = active.get(sym)
        if ins is not None:
            m = ins.mark(int(horizon) - ins.k0)
            if m is None:
                m = float(ins.tr["c"][min(len(ins.tr["c"]) - 1, max(0, (int(horizon) - ins.k0) * 24 - 1))])
            fm[sym] = m
    e_end, _ = run.equity(fm)
    eq = [s["equity"] for s in run.snapshots] + [e_end]
    out = path_summary(np.array(eq, dtype=float))
    out.update({"halted": bool(run.st.halted), "tier_end": run.st.tier_name,
                "n_entries": len(run.entries), "n_skips": len(run.skips)})
    if keep_series:
        out["equity"] = eq
        out["entries"] = run.entries
        out["closes"] = run.closes
    return out


def path_summary(eq: np.ndarray) -> dict[str, Any]:
    r = eq[1:] / eq[:-1] - 1.0
    nm = (len(eq) - 1) // BOOT_MONTH_DAYS
    mret = np.array([eq[(i + 1) * BOOT_MONTH_DAYS] / eq[i * BOOT_MONTH_DAYS] - 1.0 for i in range(nm)]) if nm else np.array([])
    return {"total_return": float(eq[-1] / eq[0] - 1.0), "max_dd": max_drawdown(eq), "days": int(len(eq) - 1),
            "day_ge_1": int(np.sum(r >= 0.01)), "day_le_m1": int(np.sum(r <= -0.01)), "n_days": int(len(r)),
            "median_day": float(np.median(r)) if len(r) else 0.0,
            "months_ge_1": int(np.sum(mret >= 0.01)), "n_months": int(len(mret)),
            "month_mean": float(np.mean(mret)) if len(mret) else None}


def bootstrap_serial(lib: dict[str, Any], *, arm: str, horizon: int, block: int, n_paths: int, params: LedgerParams,
                     seed: int = BOOT_SEED) -> list[dict[str, Any]]:
    """Tek süreçte bootstrap (testler ve küçük koşular): `run_bootstrap` ile aynı yollar ve aynı sonuç."""
    starts = boot_starts(int(lib["n_days"]), horizon=horizon, block=block, n_paths=n_paths, seed=seed)
    out = []
    for i in range(int(n_paths)):
        res = run_boot_path(lib, starts[i], block=block, horizon=horizon, arm=ARMS[arm], params=params)
        res["path"] = i
        out.append(res)
    return out


def boot_starts(n_sample_days: int, *, horizon: int, block: int, n_paths: int, seed: int = BOOT_SEED) -> np.ndarray:
    """Blok başlangıçları (n_paths × blok sayısı). Kollar AYNI yolları kullanır (ortak rastgele sayılar)."""
    nb = int(math.ceil(int(horizon) / int(block)))
    rng = np.random.default_rng([int(seed), int(block), int(horizon)])
    return rng.integers(0, int(n_sample_days), size=(int(n_paths), nb))


_BOOT_LIB: dict[str, Any] | None = None


def _boot_init(lib_path: str) -> None:
    global _BOOT_LIB
    import pickle
    with open(lib_path, "rb") as fh:
        _BOOT_LIB = pickle.load(fh)


def _boot_task(args: tuple) -> list[dict[str, Any]]:
    arm_name, horizon, block, idx, starts, params = args
    out = []
    for i, st in zip(idx, starts):
        res = run_boot_path(_BOOT_LIB, st, block=block, horizon=horizon, arm=ARMS[arm_name], params=params)
        res["path"] = int(i)
        out.append(res)
    return out


def boot_aggregate(paths: list[dict[str, Any]]) -> dict[str, Any]:
    """Yeniden örnekleme SIKLIKLARI (olasılık değil)."""
    tr = np.array([p["total_return"] for p in paths])
    dd = np.array([p["max_dd"] for p in paths])
    nd = sum(p["n_days"] for p in paths)
    nm = sum(p["n_months"] for p in paths)
    mm = [p["month_mean"] for p in paths if p["month_mean"] is not None]
    return {"n_paths": len(paths),
            "dd_ge": {k: float(np.mean(dd >= v)) for k, v in (("15", 0.15), ("25", 0.25), ("35", 0.35), ("50", 0.50))},
            "halt_freq": float(np.mean([p["halted"] for p in paths])),
            "total_return": {"median": float(np.median(tr)), "p05": float(np.quantile(tr, 0.05)), "p95": float(np.quantile(tr, 0.95)),
                             "mean": float(np.mean(tr)), "share_negative": float(np.mean(tr < 0))},
            "max_dd": {"median": float(np.median(dd)), "p95": float(np.quantile(dd, 0.95))},
            "month_ge_1pct_share": float(sum(p["months_ge_1"] for p in paths) / nm) if nm else None,
            "month_mean_median": float(np.median(mm)) if mm else None,
            "day_ge_1pct_share": float(sum(p["day_ge_1"] for p in paths) / nd) if nd else None,
            "day_le_m1pct_share": float(sum(p["day_le_m1"] for p in paths) / nd) if nd else None,
            "median_day_median": float(np.median([p["median_day"] for p in paths])),
            "tier_end": {k: int(sum(1 for p in paths if p["tier_end"] == k)) for k in (*P.TIER_NAMES, P.HALTED)}}


def run_bootstrap(lib: dict[str, Any], *, out_dir: Path, params: LedgerParams, jobs: int = 3, n_paths: int = BOOT_PATHS,
                  seed: int = BOOT_SEED, plan: list[tuple[str, int, int]] | None = None,
                  log: Callable[[str], None] = print) -> dict[str, Any]:
    """Plan: [(kol, ufuk, blok)]. Varsayılan: blok 20 → (b, p, c) × (365, örnek uzunluğu); blok 5 ve 60 → (b, 365)."""
    import pickle
    from concurrent.futures import ProcessPoolExecutor
    N = int(lib["n_days"])
    if plan is None:
        plan = [(a, h, BOOT_BLOCK) for h in (BOOT_HORIZON, N) for a in BOOT_ARMS]
        plan += [("b", BOOT_HORIZON, b) for b in BOOT_BLOCK_SENS]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lib_path = out_dir / "boot_lib.pkl"
    with open(lib_path, "wb") as fh:
        pickle.dump(lib, fh)
    results: dict[str, Any] = {}
    with ProcessPoolExecutor(max_workers=max(1, int(jobs)), initializer=_boot_init, initargs=(str(lib_path),)) as ex:
        for arm_name, horizon, block in plan:
            t0 = time.time()
            starts = boot_starts(N, horizon=horizon, block=block, n_paths=n_paths, seed=seed)
            chunk = max(1, n_paths // (int(jobs) * 8))
            tasks = [(arm_name, int(horizon), int(block), list(range(i, min(n_paths, i + chunk))),
                      starts[i:min(n_paths, i + chunk)], params) for i in range(0, n_paths, chunk)]
            paths: list[dict[str, Any]] = []
            for res in ex.map(_boot_task, tasks):
                paths.extend(res)
            paths.sort(key=lambda p: p["path"])
            key = "%s|h%d|b%d" % (arm_name, horizon, block)
            results[key] = {"arm": arm_name, "horizon": int(horizon), "block": int(block), "seed": int(seed),
                            "agg": boot_aggregate(paths), "seconds": round(time.time() - t0, 1)}
            (out_dir / ("boot_%s_h%d_b%d.json" % (arm_name, horizon, block))).write_text(
                json.dumps({"key": key, "paths": paths}, ensure_ascii=False), encoding="utf-8")
            log("bootstrap %s: %s · %.0f sn" % (key, json.dumps(results[key]["agg"]["total_return"]), time.time() - t0))
    return results


# ---------------------------------------------------------------------------- rapor ölçüleri
def m2_points(stream: dict[str, Any]) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    pts = [(int(d["t"]), float(d["equity"])) for d in stream["days"]]
    fm = stream.get("final_marks") or {}
    e_end = float(stream["wallet_end"]) + sum(float(o["qty"]) * (float(fm[o["symbol"]]) - float(o["entry"]))
                                              for o in stream.get("open_end") or [] if o["symbol"] in fm)
    pts.append((DECISION_END_MS + TOUR_OFFSET_MS, e_end))
    return pts, [(int(t), float(e)) for t, e in stream.get("hourly_equity") or []]


def stop_at_level_sensitivity(closes: list[dict[str, Any]], stops: dict[str, float], *, taker: float = 0.0005,
                              slip_bps: float = 3.0) -> dict[str, Any]:
    """§3.2: ihtiyatlı likidasyon (`INTRABAR_ORDER_UNOBSERVED`, alternatif `STOP_AT_LEVEL`) yerine stop seviyesinden
    dolum varsayılsaydı R farkı (yaklaşık: stop × (1 − kayma), giriş/çıkış ücreti %0,05, funding aynen)."""
    n, diff = 0, 0.0
    for c in closes:
        if c.get("basis") != "INTRABAR_ORDER_UNOBSERVED":
            continue
        st = stops.get(c.get("id") or "")
        q, e = float(c.get("qty") or 0.0), float(c.get("entry") or 0.0)
        if st is None or q <= 0 or e <= st:
            continue
        alt = st * (1.0 - slip_bps / 10_000.0)
        pnl = q * (alt - e) - taker * q * (e + alt) + float(c.get("funding") or 0.0)
        diff += pnl / (q * (e - st)) - float(c["r"])
        n += 1
    return {"n": n, "sum_r_gain_if_stop_at_level": round(diff, 4)}


def m2_trade_stats(stream: dict[str, Any]) -> dict[str, Any]:
    opens = {e["id"]: e for e in stream["events"] if e["kind"] == "open"}
    closes = [e for e in stream["events"] if e["kind"] == "close"]
    rs = np.array([c["r"] for c in closes], dtype=float)
    days = np.array([int(opens[c["id"]]["t"]) // DAY_MS if c["id"] in opens else 0 for c in closes])
    st = L.r_stats(rs, 2000, seed=BOOT_SEED, days=days) if len(rs) else {"n": 0}
    from collections import Counter
    out = {"closed": st, "open_at_end": len(stream.get("open_end") or []),
           "exit_reasons": dict(Counter(c["reason"] for c in closes)),
           "liquidations": int(sum(1 for c in closes if c["reason"] == "likidasyon")),
           "size_rules": dict(Counter(str(o.get("size_rule")) for o in opens.values())),
           "unlocked": dict(Counter("+".join(sorted(o.get("unlocked_by") or [])) or "POLICY" for o in opens.values())),
           "opened": len(opens)}
    # yıl başına işlem ve R (bilgi)
    by_year: dict[str, list[float]] = {}
    for c in closes:
        o = opens.get(c["id"])
        if o:
            by_year.setdefault(_stamp(int(o["t"]), "%Y"), []).append(float(c["r"]))
    out["by_year"] = {y: {"n": len(v), "mean_r": float(np.mean(v))} for y, v in sorted(by_year.items())}
    n_open = np.array([d["n_open"] for d in stream["days"]], dtype=float)
    out["open_positions"] = {"mean": float(n_open.mean()), "median": float(np.median(n_open)), "max": int(n_open.max())}
    stops = {i: float(o["stop"]) for i, o in opens.items() if o.get("stop") is not None}
    cl2 = [dict(c, qty=opens[c["id"]]["qty"], entry=opens[c["id"]]["fill"]) for c in closes if c["id"] in opens]
    out["stop_at_level_sensitivity"] = stop_at_level_sensitivity(cl2, stops)
    return out


def m2_open_risk_series(stream: dict[str, Any], data: ArchiveData) -> dict[int, float]:
    """M2'nin tur anındaki açık riski / özkaynak (mark ölçüsü) — gerçekleşen çarpanın paydası."""
    by_t = stream_index(stream)
    pos: dict[str, dict[str, Any]] = {}
    out: dict[int, float] = {}
    for d in stream["days"]:
        T, day = int(d["t"]), int(d["day"])
        for e in by_t.get(T, []):
            if e["kind"] == "open":
                pos[e["symbol"]] = e
            elif e["kind"] == "close" and (pos.get(e["symbol"]) or {}).get("id") == e["id"]:
                pos.pop(e["symbol"], None)
        o = 0.0
        for s, e in pos.items():
            m = data.tour_mark(s, day)
            if m is not None and e.get("stop") is not None:
                o += float(e["qty"]) * max(0.0, m - float(e["stop"]))
        out[T] = o / float(d["equity"]) if d["equity"] else 0.0
        for k in range(1, 25):
            for e in by_t.get(day + k * HOUR_MS, []):
                if e["kind"] == "close" and (pos.get(e["symbol"]) or {}).get("id") == e["id"]:
                    pos.pop(e["symbol"], None)
    return out


def m2x_details(res: dict[str, Any], stream: dict[str, Any], m2_or: dict[int, float]) -> dict[str, Any]:
    """§3.5'in M2X'e özgü çıktıları."""
    from collections import Counter
    snaps = res["snapshots"]
    tiers = [s["tier"] for s in snaps]
    tier_days = dict(Counter(tiers))
    # kademe kalışları (ardışık gün)
    stays: dict[str, int] = {}
    run_t, run_n = None, 0
    for t in tiers + [None]:
        if t == run_t:
            run_n += 1
            continue
        if run_t is not None:
            stays[run_t] = max(stays.get(run_t, 0), run_n)
        run_t, run_n = t, 1
    events = res["state"]["events"]
    ups = Counter(ev.get("key") for ev in events if ev["kind"] == "UP")
    downs = sum(1 for ev in events if ev["kind"] == "DOWN")
    halt = next((ev for ev in events if ev["kind"] == "HALT"), None)
    post_halt_dd = None
    if halt is not None:
        ht = _ms(halt["at"][:10]) if halt.get("at") else None
        xs = [s for s in snaps if ht is not None and s["t"] >= ht]
        if xs:
            post_halt_dd = max(s["dd"] for s in xs)
    # saatlik kriz sonrası düşüş: 1 − (E − CL) / P (P o anki gerçek zirve)
    peaks = [(s["t"], s["peak"]) for s in snaps]
    crisis = []
    pi = 0
    for t, e, c, _o in res["hourly"]:
        while pi + 1 < len(peaks) and peaks[pi + 1][0] <= t:
            pi += 1
        p = peaks[pi][1] if peaks else 0.0
        if p > 0:
            crisis.append(1.0 - (e - c) / p)
    for s in snaps:
        if s["peak"] > 0:
            crisis.append(1.0 - (s["equity_after"] - s["cl"]) / s["peak"])
    crisis_a = np.array(crisis) if crisis else np.array([0.0])
    # M2 işlemlerinin M2'deki R'si: kopyalanan (tam / küçültülmüş) ve atlanan
    r_m2 = {e["id"]: float(e["r"]) for e in stream["events"] if e["kind"] == "close"}
    for o in stream.get("open_end") or []:
        if o.get("mark_r") is not None:
            r_m2.setdefault(o["id"], float(o["mark_r"]))
    opens = {e["id"]: e for e in stream["events"] if e["kind"] == "open"}

    def grp(ids: list[str]) -> dict[str, Any]:
        ids = [i for i in ids if i in r_m2]
        if not ids:
            return {"n": 0}
        rs = np.array([r_m2[i] for i in ids])
        days = np.array([int(opens[i]["t"]) // DAY_MS for i in ids])
        st = L.r_stats(rs, 2000, seed=BOOT_SEED, days=days)
        return {"n": st["n"], "mean_r": st["mean_r"], "ci95_day_clustered": st.get("ci95")}

    full_ids = [x["m2_id"] for x in res["entries"] if x["full"]]
    scaled_ids = [x["m2_id"] for x in res["entries"] if not x["full"]]
    skip_ids = [x["m2_id"] for x in res["skips"]]
    n_cand = len(res["entries"]) + len(res["skips"])
    # eşleşen işlem başına M2X R − M2 R; likidasyon ayrışması
    diffs, liq_div, liq_cost, liqs, m2_liq_only = [], 0, [], 0, 0
    m2_close = {e["id"]: e for e in stream["events"] if e["kind"] == "close"}
    for c in res["closes"]:
        if c["reason"] == "likidasyon":
            liqs += 1
        mid = c.get("m2_id")
        if mid and mid in r_m2 and c["how"] in ("bar", "tick", "mirror"):
            diffs.append(c["r"] - r_m2[mid])
            m2c = m2_close.get(mid)
            if c["reason"] == "likidasyon" and m2c is not None and m2c["reason"] != "likidasyon":
                liq_div += 1
                liq_cost.append(c["r"] - r_m2[mid])
            if c["reason"] != "likidasyon" and m2c is not None and m2c["reason"] == "likidasyon":
                m2_liq_only += 1
    dd_a = np.array(diffs) if diffs else np.array([0.0])
    or_ratio = []
    for s in snaps:
        m = m2_or.get(int(s["t"]))
        if m and m > 0 and s["equity_after"] > 0:
            or_ratio.append((s["or"] / s["equity_after"]) / m)
    by_tier_skip: dict[str, dict[str, int]] = {}
    for x in res["skips"]:
        by_tier_skip.setdefault(x["tier"], {}).setdefault(x["reason"], 0)
        by_tier_skip[x["tier"]][x["reason"]] += 1
    return {"tier_days": tier_days, "longest_stay_days": stays, "steps_down": downs, "steps_up_by_key": dict(ups),
            "halt": halt, "max_dd_after_halt": post_halt_dd,
            "max_or_over_e": res["max_or_e"], "max_cl_over_e": res["max_cl_e"],
            "crisis_dd_max": float(crisis_a.max()), "crisis_hours_share_over_50": float(np.mean(crisis_a > 0.5)),
            "binding": res["binding"],
            "candidates": n_cand, "full": len(full_ids), "scaled": len(scaled_ids), "skipped": len(skip_ids),
            "share_full": len(full_ids) / n_cand if n_cand else None,
            "share_scaled": len(scaled_ids) / n_cand if n_cand else None,
            "share_skipped": len(skip_ids) / n_cand if n_cand else None,
            "skips_by_tier_reason": by_tier_skip,
            "entries_by_tier": dict(Counter(x["tier"] for x in res["entries"])),
            "r_in_m2": {"full": grp(full_ids), "scaled": grp(scaled_ids), "skipped": grp(skip_ids)},
            "avg_or_over_e": float(np.mean([s["or"] / s["equity_after"] for s in snaps if s["equity_after"] > 0])),
            "realised_risk_multiplier_vs_m2": float(np.mean(or_ratio)) if or_ratio else None,
            "min_notional_bumps": int(sum(1 for x in res["entries"] if x["size_rule"] == "BUMP_MIN_NOTIONAL")),
            "bracket_leverage_reductions": int(sum(1 for x in res["entries"] if x["lev_reduced"] > 0)),
            "ledger_shrunk": int(sum(1 for x in res["entries"] if x["shrunk_by_ledger"])),
            "liquidations": liqs, "liquidation_divergence": liq_div,
            "liquidation_divergence_r_cost": float(np.sum(liq_cost)) if liq_cost else 0.0,
            "divergence_other": res["divergence"],
            "matched_r_diff": {"n": len(diffs), "mean": float(dd_a.mean()), "median": float(np.median(dd_a)),
                               "p05": float(np.quantile(dd_a, 0.05)), "p95": float(np.quantile(dd_a, 0.95))},
            "m2x_closed": {"n": len(res["closes"]), "mean_r": float(np.mean([c["r"] for c in res["closes"]])) if res["closes"] else None},
            "stop_at_level_sensitivity": stop_at_level_sensitivity(
                [dict(c, id=c.get("m2_id")) for c in res["closes"]],
                {i: float(o["stop"]) for i, o in opens.items() if o.get("stop") is not None}),
            "m2_liquidated_m2x_not": m2_liq_only,
            "divergence_share_bcd": ((res["divergence"].get("orphan", 0) + m2_liq_only) / len(res["closes"]))
            if res["closes"] else None,
            "total_fees": res["total_fees"], "total_funding": res["total_funding"],
            "warmup_until": _iso_ms(DECISION_START_MS + int(P.M2X_POLICY_V1["warmup_days"]) * DAY_MS)}


def regime_count(data: ArchiveData) -> dict[str, Any]:
    """BTC günlük kapanışın EMA200 üstünde/altında geçirdiği ayrı dönemlerin sayısı (karar döneminde)."""
    from . import indicators as ind
    d = data.d1[BTC]
    ema = ind.ema(d["close"].astype(float), 200).to_numpy()
    t = d["timestamp"].to_numpy(dtype=np.int64)
    m = (t >= DECISION_START_MS - DAY_MS) & (t < DECISION_END_MS) & np.isfinite(ema)
    above = (d["close"].to_numpy(dtype=float) > ema)[m]
    runs = 1 + int(np.sum(above[1:] != above[:-1])) if len(above) else 0
    return {"periods": runs, "days_above": int(above.sum()), "days_below": int((~above).sum())}


def lab_overlap(stream: dict[str, Any], data: ArchiveData) -> dict[str, Any]:
    """E3 (bilgi): laboratuvarın TSMOM_28 LONG olaylarının kaçı bir M2 girişiyle aynı güne düşüyor."""
    lab: set[tuple[str, int]] = set()
    for s in data.symbols:
        df = data.d1[s]
        if len(df) < 230:
            continue
        df = df.reset_index(drop=True)
        ind = L.indicators(df)
        for ev in L.algo_events(df, s, "1d", ind["atr"], L.aux_series(df)):
            if ev.name == "TSMOM_28" and ev.side == L.LONG and DECISION_START_MS <= ev.t_ms < DECISION_END_MS:
                lab.add((s, int(ev.t_ms)))
    m2 = {(e["symbol"], int(e["t"]) - TOUR_OFFSET_MS) for e in stream["events"] if e["kind"] == "open"}
    both = lab & m2
    return {"lab_long_events": len(lab), "m2_entries": len(m2), "same_day": len(both),
            "share_of_lab": len(both) / len(lab) if lab else None, "share_of_m2": len(both) / len(m2) if m2 else None}


def trade_list_key(stream: dict[str, Any]) -> list[tuple]:
    """Karşı-olgusal açık/kapalı karşılaştırması: (sembol, açılış, stop, miktar, kapanış, neden)."""
    opens = {e["id"]: e for e in stream["events"] if e["kind"] == "open"}
    closes = {e["id"]: e for e in stream["events"] if e["kind"] == "close"}
    out = []
    for i, o in opens.items():
        c = closes.get(i) or {}
        out.append((o["symbol"], int(o["t"]), round(float(o["stop"]), 10), round(float(o["qty"]), 10), c.get("closed_at"),
                    c.get("reason")))
    return sorted(out)


# ---------------------------------------------------------------------------- çalışma (aşamalar)
CF_CHECK_WINDOW = (_ms("2025-09-01"), _ms("2025-11-01"))


def _pkl_dump(obj: Any, path: Path) -> None:
    import pickle
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(obj, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)


def _pkl_load(path: Path) -> Any:
    import pickle
    with open(path, "rb") as fh:
        return pickle.load(fh)


def code_identity(repo: Path) -> dict[str, Any]:
    """Simüle edilen kod: HEAD ve `1c2c6e2`ye göre `tradingbot/` farkı (yalnız m2x_* dosyaları beklenir)."""
    import subprocess
    def _git(*a: str) -> str:
        try:
            return subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    diff = [x for x in _git("diff", "--name-only", "1c2c6e2", "HEAD", "--", "tradingbot").splitlines() if x]
    dirty = [x for x in _git("status", "--porcelain", "--", "tradingbot").splitlines() if x]
    return {"head": _git("rev-parse", "HEAD"), "base_dev": "1c2c6e2", "vps": "f8b05fb",
            "tradingbot_diff_vs_1c2c6e2": diff,
            "m2_path_identical_to_1c2c6e2": all(Path(x).name.startswith("m2x_") for x in diff),
            "dirty_tradingbot": dirty}


def stage_cfcheck(*, config_path: Path, data: ArchiveData, out_dir: Path, log: Callable[[str], None]) -> dict[str, Any]:
    """§3.2: 2025-09-01 → 2025-10-31 karşı-olgusal açık ve kapalı; işlem listeleri bire bir aynı mı?"""
    lists = {}
    for cf in (True, False):
        rd = out_dir / ("cfcheck_%s" % ("on" if cf else "off"))
        import shutil
        shutil.rmtree(rd, ignore_errors=True)
        h = M2Harness(config_path=config_path, data=data, run_dir=rd, counterfactual=cf)
        st = h.run(*CF_CHECK_WINDOW)
        lists[cf] = trade_list_key(st)
        log("cf %s: %d işlem, sayaçlar %s" % ("açık" if cf else "kapalı", len(lists[cf]), st["learning_counters"]))
    same = lists[True] == lists[False]
    return {"window": [_iso_ms(CF_CHECK_WINDOW[0]), _iso_ms(CF_CHECK_WINDOW[1])], "identical": same,
            "n_on": len(lists[True]), "n_off": len(lists[False]),
            "diff_examples": [list(x) for x in sorted(set(lists[True]) ^ set(lists[False]))[:10]]}


def _arm_worker(args: tuple) -> dict[str, Any]:
    arm_name, stream_path, cache_dir, symbols, params = args
    data = ArchiveData(symbols, Path(cache_dir))
    stream = _pkl_load(Path(stream_path))
    return run_historical_arm(ARMS[arm_name], stream, data, params)


def run_study(*, config_path: Path, cache_dir: Path, out_dir: Path, stages: list[str], jobs: int = 3,
              n_paths: int = BOOT_PATHS, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Aşamalar: fetch · cfcheck · m2 · arms · boot · report (sırayla; çıktılar `out_dir`de, yeniden kullanılır)."""
    from concurrent.futures import ProcessPoolExecutor
    from .config import load_config
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg0 = load_config(config_path)
    eu = cfg0.v3.entry_universe
    universe = list(eu.symbols) if eu.enabled else list(cfg0.coins)
    symbols = list(dict.fromkeys(universe + [BTC]))
    params = LedgerParams.from_v3(cfg0.v3)
    meta_p = out_dir / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    meta.update({"sim_version": SIM_VERSION, "doc": SIM_DOC, "policy_version": P.POLICY_VERSION, "policy_sha": P.M2X_POLICY_SHA,
                 "code": code_identity(Path(__file__).resolve().parents[1]), "filters": FILTERS_ASSUMED,
                 "daily_window": live_daily_window(), "universe": universe,
                 "periods": {"decisions": [_iso_ms(DECISION_START_MS), _iso_ms(DECISION_END_MS)],
                             "d1_from": _iso_ms(D1_START_MS), "h1_from": _iso_ms(H1_START_MS)}})
    if "fetch" in stages:
        meta["fetch"] = fetch_all(symbols, cache_dir, jobs=4, log=log)
    data = None
    if any(s in stages for s in ("cfcheck", "m2", "boot", "report")):
        data = ArchiveData(symbols, cache_dir)
        meta["data_quality"] = data.quality()
    if "cfcheck" in stages:
        meta["cfcheck"] = stage_cfcheck(config_path=config_path, data=data, out_dir=out_dir, log=log)
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    stream_p = out_dir / "stream_a.pkl"
    if "m2" in stages:
        cf_on = not bool((meta.get("cfcheck") or {}).get("identical", False))
        meta["m2_counterfactual"] = cf_on
        import shutil
        shutil.rmtree(out_dir / "run_a", ignore_errors=True)
        h = M2Harness(config_path=config_path, data=data, run_dir=out_dir / "run_a", counterfactual=cf_on)
        t0 = time.time()
        st = h.run(progress=log)
        meta["m2_seconds"] = round(time.time() - t0, 1)
        _pkl_dump(st, stream_p)
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if "arms" in stages:
        tasks = [(a, str(stream_p), str(cache_dir), symbols, params) for a in ARMS]
        with ProcessPoolExecutor(max_workers=max(1, int(jobs))) as ex:
            for res in ex.map(_arm_worker, tasks):
                _pkl_dump(res, out_dir / "arms" / ("%s.pkl" % res["arm"]))
                log("kol %s bitti: E_son %.2f · %s sn" % (res["arm"], res["final"]["equity"], res["seconds"]))
    if "boot" in stages:
        stream = _pkl_load(stream_p)
        lib = trade_library(stream, data)
        meta["boot_lib"] = {"trades": len(lib["trades"]), "n_days": lib["n_days"],
                            "kinds": {k: int(sum(1 for t in lib["trades"] if t["kind"] == k))
                                      for k in ("rule", "tick", "bar", "htick", "end")}}
        meta["bootstrap"] = run_bootstrap(lib, out_dir=out_dir / "boot", params=params, jobs=jobs, n_paths=n_paths, log=log)
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if "report" in stages:
        meta["report"] = build_report(out_dir=out_dir, data=data, log=log)
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return meta


def build_report(*, out_dir: Path, data: ArchiveData, log: Callable[[str], None] = print) -> dict[str, Any]:
    stream = _pkl_load(Path(out_dir) / "stream_a.pkl")
    pts, hourly = m2_points(stream)
    rep: dict[str, Any] = {"a": {"metrics": series_metrics(pts, hourly=hourly), "trades": m2_trade_stats(stream),
                                 "rejections": stream["rejections"], "learning_counters": stream["learning_counters"],
                                 "total_fees": stream["total_fees"], "total_funding": stream["total_funding"],
                                 "funding_source": stream["funding_source"], "learning": stream["learning"]}}
    m2_or = m2_open_risk_series(stream, data)
    rep["a"]["avg_or_over_e"] = float(np.mean(list(m2_or.values()))) if m2_or else None
    for name in ARMS:
        p = Path(out_dir) / "arms" / ("%s.pkl" % name)
        if not p.exists():
            continue
        res = _pkl_load(p)
        apts = [(int(s["t"]), float(s["equity"])) for s in res["snapshots"]] + \
               [(DECISION_END_MS + TOUR_OFFSET_MS, float(res["final"]["equity"]))]
        rep[name] = {"role": res["role"], "desc": res["desc"],
                     "metrics": series_metrics(apts, hourly=[(t, e) for t, e, _c, _o in res["hourly"]]),
                     "m2x": m2x_details(res, stream, m2_or)}
    rep["regimes"] = regime_count(data)
    rep["lab_overlap_e3"] = lab_overlap(stream, data)
    q = data.quality()
    rep["extreme_wicks"] = {s: v.get("extreme_wicks") for s, v in q.items() if v.get("extreme_wicks")}
    rep["crash_events_like_2025_10_10"] = sorted({w["bar"][:10] for v in q.values() for w in (v.get("extreme_wicks") or [])})
    (Path(out_dir) / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log("rapor yazıldı: %s" % (Path(out_dir) / "report.json"))
    return {"path": str(Path(out_dir) / "report.json")}
