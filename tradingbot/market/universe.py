"""Evren oluşturucu — Binance spot + USDⓈ-M perpetual sembolleri; likidite/yaş/kaldıraçlı-token/stable filtreleri.

Sağlayıcılar duck-typing ile kullanılır: `exchange_info()` (sembol sözlükleri), `ticker24h()` (24s istatistik),
isteğe bağlı `book_tickers()` (bütün semboller için bid/ask, tek istek) — yoksa spread ticker'daki bid/ask'ten,
o da yoksa None (filtre uygulanmaz). Derinlik filtresi ancak `depth_by_symbol` verilirse uygulanır (pahalı uç nokta).

Çıktı `UniverseSnapshot` → `state/universe.json` (atomik). Elenen semboller de listede kalır (`excluded_reason` dolu);
`eligible()` yalnızca geçerli olanları verir. Ağ dışı, deterministik: aynı girdi → aynı çıktı.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core import atomic_write_json, iso, utc_now
from .providers import FUTURES, SPOT, now_ms

log = logging.getLogger(__name__)

STABLE_BASES = ("USDC", "FDUSD", "BUSD", "TUSD", "USD1", "DAI", "USDP", "USDE", "EUR", "EURI", "GBP", "TRY", "BRL", "ARS")
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "2L", "2S", "5L", "5S")


@dataclass
class UniverseConfig:
    quote_assets: tuple[str, ...] = ("USDT",)
    allow_usdc: bool = False
    min_quote_volume_24h_spot: float = 20e6
    min_quote_volume_24h_futures: float = 20e6
    max_spread_pct: float = 0.2
    min_depth_0_5pct_usdt: float = 50_000.0
    min_listing_age_days: int = 60
    exclude_stable_bases: tuple[str, ...] = STABLE_BASES
    exclude_leveraged_suffixes: tuple[str, ...] = LEVERAGED_SUFFIXES
    exclude_symbols: tuple[str, ...] = ()
    max_symbols: int = 200

    @property
    def quotes(self) -> tuple[str, ...]:
        q = tuple(self.quote_assets)
        if self.allow_usdc and "USDC" not in q:
            q = q + ("USDC",)
        return q

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class UniverseEntry:
    symbol: str                       # 'BTC/USDT'
    raw: str                          # 'BTCUSDT'
    market_type: str                  # spot | futures
    base: str
    quote: str
    status: str = "TRADING"
    filters: dict = field(default_factory=dict)
    quote_volume_24h: float = 0.0
    spread_pct: float | None = None
    listing_age_days: float | None = None
    tags: list[str] = field(default_factory=list)
    excluded_reason: str | None = None
    last_price: float | None = None
    depth_0_5pct: float | None = None

    @property
    def perp(self) -> str:
        return f"{self.symbol}:{self.quote}"

    @property
    def eligible(self) -> bool:
        return self.excluded_reason is None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["tags"] = list(self.tags)
        d["filters"] = dict(self.filters)
        d["eligible"] = self.eligible
        return d


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _filters_dict(raw_filters: Any) -> dict:
    """Binance `filters` listesi → {filterType: {...}} + öz (tickSize/stepSize/minNotional)."""
    out: dict[str, Any] = {}
    for f in raw_filters or []:
        if isinstance(f, dict) and f.get("filterType"):
            out[f["filterType"]] = {k: v for k, v in f.items() if k != "filterType"}
    pf = out.get("PRICE_FILTER", {})
    ls = out.get("LOT_SIZE", {})
    mn = out.get("MIN_NOTIONAL", {}) or out.get("NOTIONAL", {})
    out["_tick_size"] = pf.get("tickSize")
    out["_step_size"] = ls.get("stepSize")
    out["_min_qty"] = ls.get("minQty")
    out["_min_notional"] = mn.get("minNotional") or mn.get("notional")
    return out


def _is_leveraged(base: str, suffixes: Iterable[str]) -> bool:
    for s in suffixes:
        if base.endswith(s) and len(base) > len(s):
            # 'UP'/'DOWN' yalnızca Binance kaldıraçlı token kalıbı (BTCUP); 'SUP' gibi kısa isimler hariç
            if s in ("UP", "DOWN") and len(base) - len(s) < 3:
                continue
            return True
    return False


def _spread_from(bid: Any, ask: Any) -> float | None:
    b, a = _f(bid), _f(ask)
    if b > 0 and a > 0 and a >= b:
        return (a - b) / ((a + b) / 2.0) * 100.0
    return None


def _book_map(provider: Any) -> dict[str, dict]:
    if hasattr(provider, "book_tickers"):
        try:
            rows = provider.book_tickers() or []
            return {r.get("symbol"): r for r in rows if isinstance(r, dict)}
        except Exception as exc:  # noqa: BLE001 — spread opsiyonel
            log.warning("book_tickers alınamadı: %s", exc)
    return {}


def _base_filter(e: UniverseEntry, cfg: UniverseConfig, min_vol: float) -> str | None:
    """Ortak eleme kuralları; ilk eşleşen sebep döner (None = geçerli)."""
    if e.quote not in cfg.quotes:
        return f"QUOTE_{e.quote}"
    if e.status != "TRADING":
        return f"STATUS_{e.status}"
    if e.raw in cfg.exclude_symbols or e.symbol in cfg.exclude_symbols:
        return "EXCLUDED_SYMBOL"
    if e.base in cfg.exclude_stable_bases:
        return "STABLE_BASE"
    if _is_leveraged(e.base, cfg.exclude_leveraged_suffixes):
        return "LEVERAGED_TOKEN"
    if e.quote_volume_24h < min_vol:
        return "LOW_VOLUME"
    if e.spread_pct is not None and e.spread_pct > cfg.max_spread_pct:
        return "WIDE_SPREAD"
    if e.listing_age_days is not None and e.listing_age_days < cfg.min_listing_age_days:
        return "YOUNG_LISTING"
    if e.depth_0_5pct is not None and e.depth_0_5pct < cfg.min_depth_0_5pct_usdt:
        return "THIN_DEPTH"
    return None


def _apply_cap(entries: list[UniverseEntry], cfg: UniverseConfig) -> list[UniverseEntry]:
    entries.sort(key=lambda e: (-e.quote_volume_24h, e.raw))
    n_ok = 0
    for e in entries:
        if e.excluded_reason is None:
            n_ok += 1
            if n_ok > cfg.max_symbols:
                e.excluded_reason = "MAX_SYMBOLS"
    return entries


def _ticker_map(provider: Any) -> dict[str, dict]:
    rows = provider.ticker24h() or []
    return {r.get("symbol"): r for r in rows if isinstance(r, dict) and r.get("symbol")}


def build_spot_universe(provider: Any, cfg: UniverseConfig | None = None, depth_by_symbol: dict[str, float] | None = None,
                        listing_dates_ms: dict[str, int] | None = None, now_ms_: int | None = None) -> list[UniverseEntry]:
    cfg = cfg or UniverseConfig()
    now = now_ms_ if now_ms_ is not None else now_ms()
    tickers = _ticker_map(provider)
    books = _book_map(provider)
    entries: list[UniverseEntry] = []
    for s in provider.exchange_info() or []:
        raw = s.get("symbol")
        if not raw:
            continue
        base, quote = str(s.get("baseAsset", "")), str(s.get("quoteAsset", ""))
        t = tickers.get(raw, {})
        b = books.get(raw, {})
        e = UniverseEntry(symbol=f"{base}/{quote}", raw=raw, market_type=SPOT, base=base, quote=quote,
                          status=str(s.get("status", "")), filters=_filters_dict(s.get("filters")),
                          quote_volume_24h=_f(t.get("quoteVolume")), last_price=_f(t.get("lastPrice")) or None,
                          spread_pct=_spread_from(b.get("bidPrice") or t.get("bidPrice"), b.get("askPrice") or t.get("askPrice")))
        if s.get("isSpotTradingAllowed") is False:
            e.status = "SPOT_DISABLED"
        if listing_dates_ms and raw in listing_dates_ms:
            e.listing_age_days = (now - int(listing_dates_ms[raw])) / 86_400_000
        if depth_by_symbol and raw in depth_by_symbol:
            e.depth_0_5pct = float(depth_by_symbol[raw])
        e.excluded_reason = _base_filter(e, cfg, cfg.min_quote_volume_24h_spot)
        entries.append(e)
    return _apply_cap(entries, cfg)


def build_futures_universe(provider: Any, cfg: UniverseConfig | None = None, depth_by_symbol: dict[str, float] | None = None,
                           now_ms_: int | None = None) -> list[UniverseEntry]:
    cfg = cfg or UniverseConfig()
    now = now_ms_ if now_ms_ is not None else now_ms()
    tickers = _ticker_map(provider)
    books = _book_map(provider)
    entries: list[UniverseEntry] = []
    for s in provider.exchange_info() or []:
        raw = s.get("symbol")
        if not raw:
            continue
        base, quote = str(s.get("baseAsset", "")), str(s.get("quoteAsset", ""))
        t = tickers.get(raw, {})
        b = books.get(raw, {})
        e = UniverseEntry(symbol=f"{base}/{quote}", raw=raw, market_type=FUTURES, base=base, quote=quote,
                          status=str(s.get("status", "")), filters=_filters_dict(s.get("filters")),
                          quote_volume_24h=_f(t.get("quoteVolume")), last_price=_f(t.get("lastPrice")) or None,
                          spread_pct=_spread_from(b.get("bidPrice") or t.get("bidPrice"), b.get("askPrice") or t.get("askPrice")))
        onboard = s.get("onboardDate")
        if onboard:
            e.listing_age_days = (now - int(onboard)) / 86_400_000
        if depth_by_symbol and raw in depth_by_symbol:
            e.depth_0_5pct = float(depth_by_symbol[raw])
        ct = str(s.get("contractType", "PERPETUAL"))
        if ct != "PERPETUAL":
            e.excluded_reason = f"CONTRACT_{ct}"
        else:
            e.excluded_reason = _base_filter(e, cfg, cfg.min_quote_volume_24h_futures)
        entries.append(e)
    return _apply_cap(entries, cfg)


def eligible(entries: Iterable[UniverseEntry]) -> list[UniverseEntry]:
    return [e for e in entries if e.excluded_reason is None]


def merge_universe(spot: Iterable[UniverseEntry], futures: Iterable[UniverseEntry]) -> list[UniverseEntry]:
    """Geçerli spot + futures girdilerini `symbol` üzerinden birleştir; etiket: both | spot_only | futures_only.

    Ortak sembolde futures girdisi (perp verisi daha zengin) esas alınır; spot hacmi `filters['_spot_quote_volume_24h']`
    ve etiketlere eklenir. Sonuç hacme göre azalan sıralıdır.
    """
    s_map = {e.symbol: e for e in eligible(spot)}
    f_map = {e.symbol: e for e in eligible(futures)}
    out: list[UniverseEntry] = []
    for sym in sorted(set(s_map) | set(f_map)):
        s, f = s_map.get(sym), f_map.get(sym)
        if s and f:
            m = UniverseEntry(**{k: v for k, v in f.__dict__.items()})
            m.market_type = "both"
            m.tags = sorted(set(f.tags) | set(s.tags) | {"both", "spot", "futures"})
            m.filters = dict(f.filters)
            m.filters["_spot_filters"] = dict(s.filters)
            m.filters["_spot_quote_volume_24h"] = s.quote_volume_24h
            m.filters["_futures_quote_volume_24h"] = f.quote_volume_24h
            m.quote_volume_24h = f.quote_volume_24h + s.quote_volume_24h
        elif f:
            m = UniverseEntry(**{k: v for k, v in f.__dict__.items()})
            m.tags = sorted(set(f.tags) | {"futures_only", "futures"})
        else:
            m = UniverseEntry(**{k: v for k, v in s.__dict__.items()})
            m.tags = sorted(set(s.tags) | {"spot_only", "spot"})
        out.append(m)
    out.sort(key=lambda e: (-e.quote_volume_24h, e.raw))
    return out


@dataclass
class UniverseSnapshot:
    generated_at: str
    spot: list[UniverseEntry]
    futures: list[UniverseEntry]
    merged: list[UniverseEntry]
    counts: dict[str, int] = field(default_factory=dict)
    config: dict = field(default_factory=dict)

    @classmethod
    def build(cls, spot: list[UniverseEntry], futures: list[UniverseEntry], cfg: UniverseConfig | None = None) -> "UniverseSnapshot":
        merged = merge_universe(spot, futures)
        counts = {
            "spot_total": len(spot), "spot_eligible": len(eligible(spot)),
            "futures_total": len(futures), "futures_eligible": len(eligible(futures)),
            "merged": len(merged),
            "both": sum(1 for e in merged if "both" in e.tags),
            "spot_only": sum(1 for e in merged if "spot_only" in e.tags),
            "futures_only": sum(1 for e in merged if "futures_only" in e.tags),
        }
        reasons: dict[str, int] = {}
        for e in list(spot) + list(futures):
            if e.excluded_reason:
                reasons[e.excluded_reason] = reasons.get(e.excluded_reason, 0) + 1
        counts["excluded_by_reason"] = reasons  # type: ignore[assignment]
        return cls(generated_at=iso(utc_now()), spot=spot, futures=futures, merged=merged, counts=counts,
                   config=(cfg or UniverseConfig()).to_dict())

    def symbols(self, market: str = "merged") -> list[str]:
        src = {"merged": self.merged, "spot": eligible(self.spot), "futures": eligible(self.futures)}[market]
        return [e.symbol for e in src]

    def to_dict(self) -> dict:
        return {"generated_at": self.generated_at, "counts": self.counts, "config": self.config,
                "merged": [e.to_dict() for e in self.merged],
                "spot": [e.to_dict() for e in self.spot], "futures": [e.to_dict() for e in self.futures]}

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        atomic_write_json(path, self.to_dict(), keep_backup=True)
        return path


def build_universe(spot_provider: Any | None, futures_provider: Any | None, cfg: UniverseConfig | None = None,
                   save_path: Path | str | None = None) -> UniverseSnapshot:
    """Uçtan uca: iki sağlayıcı → snapshot (→ dosya). Sağlayıcı None ise o taraf boş."""
    cfg = cfg or UniverseConfig()
    spot = build_spot_universe(spot_provider, cfg) if spot_provider is not None else []
    fut = build_futures_universe(futures_provider, cfg) if futures_provider is not None else []
    snap = UniverseSnapshot.build(spot, fut, cfg)
    if save_path:
        snap.save(save_path)
    return snap


__all__ = ["UniverseConfig", "UniverseEntry", "UniverseSnapshot", "build_spot_universe", "build_futures_universe",
           "merge_universe", "build_universe", "eligible", "STABLE_BASES", "LEVERAGED_SUFFIXES"]
