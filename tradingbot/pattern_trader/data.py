# -*- coding: utf-8 -*-
"""VERİ SERVİSİ — 15m/1h/4h kapalı mumlar (MarketFeed: artımlı indirme, kapanmamış bar düşürme, boşluk/tazelik) +
panelin okuduğu CSV önbelleği; dilim başına GERÇEK veri ihtiyacı ve hazır/yetersiz ayrımı.

Zaman sözleşmesi: `timestamp` bar açılışı; bar `açılış + dilim <= as_of` anında kapanmıştır; kapanmamış bar hiçbir
hesapta yoktur. Her (sembol, dilim) için kaynak, son kapanış, alınma zamanı, tazelik ve eksik bar durumu kaydedilir.
Veri ihtiyacı sayıları modüllerden türetilir ve açıkça yazılır (`REQUIREMENTS`); tek/keyfî/uzun bir eşik YOKTUR:
eksik üst dilim, alt dilim şeklinin GÖRÜLMESİNİ engellemez; o bağlamı şart koşan işlem kolu yetersiz veriyle çalışmaz.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ..learn.candle_context import CandleContextConfig
from ..market.providers import FUTURES, now_ms as _now_ms
from ..timeframes import tf_ms

log = logging.getLogger(__name__)

TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")
#: Dilim başına indirilecek/saklanacak azami kapalı bar (analiz penceresi). Yeni listelenen coinde daha azı olabilir.
BARS_PER_TF: dict[str, int] = {"15m": 240, "1h": 240, "4h": 240}
#: GERÇEK ihtiyaçlar (kapalı bar): şekil = son 3 bar (`_shapes` en fazla 3 bar okur); teyit = şekil barından sonra
#: `confirm_bars`=1 kapalı bar; ATR14 = 15 bar; önceki trend = `trend_lookback_bars`(10) + şekil barı; seviye/bölge =
#: fraktal pivot (k=3: her iki yanda 3 bar) ve iki dayanak (min_separation 5) → 2*3+5+2 = 13 (chart_patterns ile aynı formül);
#: sıkışma (C) = 6 aralık barı + ATR.
_CC = CandleContextConfig()
REQUIREMENTS: dict[str, int] = {"shape": 3, "confirm": _CC.confirm_bars, "atr": 15, "trend": _CC.trend_lookback_bars + 1,
                                "levels": 2 * 3 + 5 + 2, "compression": 6 + 15}
#: Bir dilimin "şekil + bağlam" için hazır sayılması: ATR, trend ve şekil birlikte.
READY_MIN_BARS = max(REQUIREMENTS["shape"], REQUIREMENTS["atr"], REQUIREMENTS["trend"]) + REQUIREMENTS["confirm"]


class CsvCandleCache:
    """MarketFeed `cache_store` sözleşmesi (`read(csym, tf, since_ms)`, `write(csym, tf, df)`) — panelin okuduğu adla
    (`binanceusdm_<BASE>-<QUOTE>_<tf>.csv`, `dashboard.candles.CandleSource.PREFIXES['futures']`) KAPALI barları saklar.
    Kaynak kimliği dosya adındadır (futures); spot dosyasına yazılmaz."""

    COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")

    def __init__(self, cache_dir: Path | str, *, prefix: str = "binanceusdm") -> None:
        self.cache_dir = Path(cache_dir)
        self.prefix = prefix
        self.writes = 0

    @staticmethod
    def _base_quote(csym: str) -> tuple[str, str]:
        core = str(csym).split(":")[0]
        if "/" in core:
            b, q = core.split("/", 1)
            return b.upper(), q.upper()
        return core.upper(), "USDT"

    def path(self, csym: str, tf: str) -> Path:
        b, q = self._base_quote(csym)
        return self.cache_dir / f"{self.prefix}_{b}-{q}_{tf}.csv"

    def read(self, csym: str, tf: str, since_ms: int | None = None) -> pd.DataFrame:
        p = self.path(csym, tf)
        if not p.exists():
            return pd.DataFrame(columns=list(self.COLUMNS))
        df = pd.read_csv(p)
        if "timestamp" not in df.columns:
            return pd.DataFrame(columns=list(self.COLUMNS))
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"])
        df["timestamp"] = df["timestamp"].astype("int64")
        if since_ms is not None:
            df = df[df["timestamp"] >= int(since_ms)]
        return df.reset_index(drop=True)

    def write(self, csym: str, tf: str, df: pd.DataFrame) -> None:
        if df is None or len(df) == 0:
            return
        p = self.path(csym, tf)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        old = self.read(csym, tf)
        new = df[[c for c in self.COLUMNS if c in df.columns]].copy()
        merged = pd.concat([old, new], ignore_index=True) if len(old) else new
        merged["timestamp"] = merged["timestamp"].astype("int64")
        merged = merged.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
        merged = merged.tail(max(BARS_PER_TF.get(tf, 240) * 3, 500))
        tmp = p.with_suffix(".csv.tmp")
        merged.to_csv(tmp, index=False)
        tmp.replace(p)
        self.writes += 1


def bars_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame → kronolojik bar sözlükleri (timestamp, open, high, low, close, volume)."""
    if df is None or len(df) == 0:
        return []
    out = []
    for t, o, h, lo, c, v in zip(df["timestamp"], df["open"], df["high"], df["low"], df["close"], df["volume"] if "volume" in df.columns else [0.0] * len(df)):
        try:
            out.append({"timestamp": int(t), "open": float(o), "high": float(h), "low": float(lo), "close": float(c), "volume": float(v) if v == v else 0.0})
        except (TypeError, ValueError):
            continue
    return out


def readiness(n_closed: int) -> dict[str, Any]:
    """Bir dilimde `n_closed` kapalı barla hangi ihtiyaçlar karşılanıyor (her ihtiyaç ayrı, sahte doldurma yok)."""
    met = {k: bool(n_closed >= v) for k, v in REQUIREMENTS.items()}
    return {"n_closed": int(n_closed), "requirements": dict(REQUIREMENTS), "met": met, "ready": bool(n_closed >= READY_MIN_BARS),
            "ready_min_bars": READY_MIN_BARS, "missing_for_ready": max(0, READY_MIN_BARS - int(n_closed))}


class DataService:
    """Sembol/dilim başına kapalı barlar + durum. `feed`: `MarketFeed` (ya da aynı `get_klines` imzasını veren nesne)."""

    def __init__(self, feed: Any, *, clock_ms: Callable[[], int] = _now_ms, bars_per_tf: dict[str, int] | None = None) -> None:
        self.feed = feed
        self.clock_ms = clock_ms
        self.bars_per_tf = dict(bars_per_tf or BARS_PER_TF)
        self.stats = {"requests": 0, "errors": 0}

    def bars(self, symbol: str, tf: str, *, as_of_ms: int | None = None, n: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """`as_of` anında KAPANMIŞ barlar (kronolojik) ve durum sözlüğü. Sağlayıcı hatası → boş liste + `error` (uydurma yok)."""
        as_of = int(as_of_ms if as_of_ms is not None else self.clock_ms())
        want = int(n or self.bars_per_tf.get(tf, 240))
        step = tf_ms(tf)
        status: dict[str, Any] = {"symbol": symbol, "tf": tf, "market": "USDM_PERP", "as_of_ms": as_of, "source": None, "fetched_at_ms": None,
                                  "last_close_ms": None, "last_open_ms": None, "is_stale": None, "gaps": [], "n_closed": 0, "error": None,
                                  "from_cache": 0, "dropped_unclosed": 0}
        self.stats["requests"] += 1
        try:
            res = self.feed.get_klines(symbol, tf, FUTURES, want)
        except Exception as exc:  # noqa: BLE001 — veri yoksa bar yok; durum gerekçeli
            self.stats["errors"] += 1
            status["error"] = f"{type(exc).__name__}: {exc}"[:200]
            return [], status
        rows = [r for r in bars_from_df(res.df) if r["timestamp"] + step <= as_of]
        status.update({"source": res.source, "fetched_at_ms": int(res.fetched_at), "is_stale": bool(res.is_stale), "gaps": list(res.gaps or [])[:20],
                       "n_closed": len(rows), "from_cache": int(res.from_cache), "dropped_unclosed": int(res.dropped_unclosed),
                       "errors": list(res.errors or [])[:5]})
        if rows:
            status["last_open_ms"] = int(rows[-1]["timestamp"])
            status["last_close_ms"] = int(rows[-1]["timestamp"]) + step
            status["age_s"] = round((as_of - status["last_close_ms"]) / 1000.0, 1)
        status["readiness"] = readiness(len(rows))
        return rows, status


__all__ = ["TIMEFRAMES", "BARS_PER_TF", "REQUIREMENTS", "READY_MIN_BARS", "CsvCandleCache", "DataService", "bars_from_df", "readiness"]


class PriceService:
    """DOĞRULANMIŞ CANLI FİYAT ve TETİK ANI LİKİDİTESİ — T2/M2 ile AYNI sözleşme (`strategy_paper.verified_price`).

    * `mark(symbol)`: USDⓈ-M perp mark (`premiumIndex`, ağırlık 1) + borsanın kendi zaman damgası → `verified_price`
      hükmü (sonlu/pozitif, kaynak zamanı, yaş <= PRICE_MAX_AGE_S, gelecekte değil). Sembol başına küçük bir TTL
      önbelleği vardır (aynı turda defter + izleyici aynı isteği tekrarlamaz); TTL fiyatın YAŞINI gizlemez — hüküm her
      çağrıda kontrol anına göre yeniden verilir.
    * `liquidity(symbol)`: YALNIZ tetik anında çağrılır (pahalı: ticker + bookTicker + depth). Spread/derinlik
      ölçülemezse `None` döner ve giriş `LIQUIDITY_UNKNOWN`/`DEPTH_UNKNOWN` ile REDDEDİLİR — bilinmeyen iyi likidite
      SAYILMAZ.
    """

    def __init__(self, provider: Any, feed: Any = None, *, clock_ms: Callable[[], int] = _now_ms, ttl_s: float = 20.0) -> None:
        self.provider = provider
        self.feed = feed
        self.clock_ms = clock_ms
        self.ttl_s = float(ttl_s)
        self._cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self.stats = {"mark_calls": 0, "mark_cache_hits": 0, "liquidity_calls": 0, "errors": 0}

    def _raw_mark(self, symbol: str, now_ms: int) -> dict[str, Any]:
        hit = self._cache.get(symbol)
        if hit and (now_ms - hit[0]) < self.ttl_s * 1000:
            self.stats["mark_cache_hits"] += 1
            return hit[1]
        self.stats["mark_calls"] += 1
        try:
            m = self.provider.mark_price(symbol) or {}
            snap = {"symbol": symbol, "ts": now_ms / 1000.0, "errors": [],
                    "funding": {"mark": m.get("mark"), "rate": m.get("funding_rate"), "ts": m.get("ts") or None}}
        except Exception as exc:  # noqa: BLE001 — fiyat yoksa tick yok; uydurma YOK
            self.stats["errors"] += 1
            snap = {"symbol": symbol, "ts": now_ms / 1000.0, "errors": [f"mark_price: {type(exc).__name__}: {exc}"[:160]], "funding": {}}
        self._cache[symbol] = (now_ms, snap)
        if len(self._cache) > 4000:
            for k in list(self._cache)[:500]:
                self._cache.pop(k, None)
        return snap

    def mark(self, symbol: str, *, now_ms: int | None = None) -> dict[str, Any]:
        from ..strategy_paper import verified_price
        now = int(now_ms if now_ms is not None else self.clock_ms())
        return verified_price(self._raw_mark(symbol, now), now_ms=now)

    def marks(self, symbols: Any, *, now_ms: int | None = None) -> tuple[dict[str, Any], dict[str, float], dict[str, dict]]:
        """Çoklu sembol: (TickData sözlüğü, float fiyatlar, boşluklar) — `engine_v3._paper_marks` ile aynı biçim."""
        from decimal import Decimal

        from ..accounting import TickData
        from ..core import iso
        from .universe import iso_ms
        now = int(now_ms if now_ms is not None else self.clock_ms())
        out: dict[str, Any] = {}
        outf: dict[str, float] = {}
        gaps: dict[str, dict] = {}
        for sym in dict.fromkeys(symbols):
            v = self.mark(sym, now_ms=now)
            if not v["ok"]:
                gaps[sym] = {"reason": v["reason"], "detail": v["detail"], "at": iso(), "age_s": v["age_s"],
                             "last_seen_mark": v["mark"] if v["mark"] > 0 else None}
                continue
            px = float(v["mark"])
            out[sym] = TickData(last=Decimal(str(px)), mark=Decimal(str(px)), ts=iso_ms(v["price_ts_ms"]) or iso())
            outf[sym] = px
        return out, outf, gaps

    def liquidity(self, symbol: str) -> dict[str, Any] | None:
        if self.feed is None:
            return None
        self.stats["liquidity_calls"] += 1
        try:
            s = self.feed.snapshot(symbol, FUTURES)
        except Exception as exc:  # noqa: BLE001
            self.stats["errors"] += 1
            return {"error": f"{type(exc).__name__}: {exc}"[:160], "spread_pct": None, "depth_0_5pct": None}
        return {"spread_pct": s.spread_pct, "depth_0_5pct": s.depth_0_5pct, "depth_1pct": s.depth_1pct,
                "imbalance": s.imbalance, "last": s.last, "mark": s.mark, "ts": s.ts, "source": s.source,
                "errors": list(s.errors or [])[:3]}
