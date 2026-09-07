"""Sınırlı, önbellekli piyasa geçmişi köprüsü (`research_bars_v1`) — OFFLINE araştırma için.

Neden gerekli: `position_path.jsonl` yalnız `mark` serisidir (bar içi yüksek/düşük YOKTUR) ve
ancak 2026-09-02'den sonra yazılmaya başlamıştır. Çıkış karşı-olgusu ve reddedilen fırsatların
simülasyonu için GERÇEK bar uçları gerekir.

Sözleşme:
* Yalnız PUBLIC, salt okunur market-data uçları kullanılır (anahtar yok, emir yok).
* Her istek diske önbelleklenir; aynı aralık ikinci kez indirilmez.
* Ağ yoksa/başarısızsa sonuç `None` olur — SAHTE bar üretilmez.
* Enstrüman eşlemesi DOĞRULANIR: kanonik giriş fiyatı, giriş barının [low, high] aralığına
  makul toleransla düşmüyorsa sembol `MAPPING_SUSPECT` işaretlenir ve analizden DIŞLANIR.
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "research_bars_v1"

FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"
MAX_LIMIT = 1500

INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
               "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000}

MAPPING_OK = "MAPPING_OK"
MAPPING_SUSPECT = "MAPPING_SUSPECT"
MAPPING_NO_DATA = "MAPPING_NO_DATA"


def raw_symbol(symbol: str) -> str:
    """`BTC/USDT` → `BTCUSDT` (borsanın ham sembolü)."""
    return str(symbol).replace("/", "").replace(":", "").upper()


def is_fetchable(symbol: str) -> bool:
    """Ham sembol URL'e güvenle konabilir mi?

    Evrende ASCII dışı ticker'lar var (ör. Çince karakterli meme sembolleri). Bunlar
    kaçırılıp denenmez — sessizce hata üretmek yerine AÇIKÇA dışlanır ve sayılır.
    """
    raw = raw_symbol(symbol)
    return bool(raw) and raw.isascii() and raw.isalnum()


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass
class BarCache:
    """Disk önbellekli bar sağlayıcı. `offline=True` iken hiçbir ağ isteği yapılmaz."""

    cache_dir: Path
    offline: bool = False
    request_pause_s: float = 0.12
    timeout_s: float = 25.0
    max_requests: int = 600
    stats: dict[str, int] = field(default_factory=lambda: {"requests": 0, "cache_hits": 0,
                                                           "errors": 0, "bars": 0})
    _mem: dict[str, dict[int, list[float]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ önbellek
    def _cache_path(self, sym: str, interval: str) -> Path:
        safe = "".join(ch for ch in raw_symbol(sym) if ch.isascii() and ch.isalnum()) or "UNSAFE"
        return self.cache_dir / f"{safe}_{interval}.json"

    def _load(self, sym: str, interval: str) -> dict[int, list[float]]:
        key = f"{raw_symbol(sym)}|{interval}"
        if key in self._mem:
            return self._mem[key]
        p = self._cache_path(sym, interval)
        data: dict[int, list[float]] = {}
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                data = {int(k): [float(x) for x in v] for k, v in (raw.get("bars") or {}).items()}
                self.stats["cache_hits"] += 1
            except (json.JSONDecodeError, TypeError, ValueError):
                data = {}
        self._mem[key] = data
        return data

    def flush(self) -> None:
        for key, data in self._mem.items():
            sym, interval = key.split("|", 1)
            p = self._cache_path(sym, interval)
            payload = {"schema_version": SCHEMA_VERSION, "symbol": sym, "interval": interval,
                       "source": "binance_fapi_public_klines",
                       "bars": {str(k): v for k, v in sorted(data.items())}}
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            tmp.replace(p)

    # ------------------------------------------------------------------ indirme
    def _fetch_page(self, sym: str, interval: str, start_ms: int, end_ms: int) -> list[list] | None:
        if self.offline or self.stats["requests"] >= self.max_requests:
            return None
        if not is_fetchable(sym):
            self.stats["unfetchable_symbols"] = self.stats.get("unfetchable_symbols", 0) + 1
            return None
        url = (f"{FAPI_KLINES}?symbol={raw_symbol(sym)}&interval={interval}"
               f"&startTime={int(start_ms)}&endTime={int(end_ms)}&limit={MAX_LIMIT}")
        try:
            self.stats["requests"] += 1
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:  # noqa: S310
                payload = json.load(resp)
            time.sleep(self.request_pause_s)
            return payload if isinstance(payload, list) else None
        except Exception:  # noqa: BLE001 — tek sembolün arızası bütün araştırmayı DURDURMAZ
            self.stats["errors"] += 1
            return None

    def ensure(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> bool:
        """İstenen aralığı önbelleğe getirir. Ağ yoksa mevcut önbellekle yetinir."""
        step = INTERVAL_MS.get(interval)
        if step is None:
            raise ValueError(f"desteklenmeyen aralık: {interval}")
        data = self._load(symbol, interval)
        lo = int(start_ms // step * step)
        hi = int(end_ms // step * step)
        cursor = lo
        got_any = False
        while cursor <= hi:
            window_end = min(hi, cursor + step * (MAX_LIMIT - 1))
            needed = [t for t in range(cursor, window_end + 1, step) if t not in data]
            if not needed:
                cursor = window_end + step
                continue
            page = self._fetch_page(symbol, interval, cursor, window_end + step - 1)
            if page is None:
                return got_any
            if not page:
                cursor = window_end + step
                continue
            for row in page:
                try:
                    ts = int(row[0])
                    data[ts] = [float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                                float(row[5])]
                    got_any = True
                    self.stats["bars"] += 1
                except (IndexError, TypeError, ValueError):
                    continue
            cursor = window_end + step
        return got_any

    def bars(self, symbol: str, interval: str, start_ms: float, end_ms: float,
             *, fetch: bool = True) -> list[dict[str, float]]:
        """`(start_ms, end_ms]` aralığındaki KAPANMIŞ barlar — açılış zamanına göre sıralı."""
        if fetch:
            self.ensure(symbol, interval, int(start_ms), int(end_ms))
        data = self._load(symbol, interval)
        step = INTERVAL_MS[interval]
        out: list[dict[str, float]] = []
        for ts in sorted(data):
            if ts < start_ms - step or ts > end_ms:
                continue
            o, h, low, c, v = data[ts]
            out.append({"timestamp": ts, "open": o, "high": h, "low": low, "close": c,
                        "volume": v, "close_ms": ts + step})
        return out


def verify_mapping(bar_rows: list[dict[str, float]], *, reference_price: float | None,
                   reference_ms: float | None, tolerance_pct: float = 1.5) -> dict[str, Any]:
    """Kanonik giriş fiyatı ile bar serisi aynı enstrümanı mı gösteriyor?

    Referans anını kapsayan bar bulunur; fiyat [low, high] aralığında ya da toleransa yakınsa
    eşleme DOĞRULANMIŞ sayılır. Aksi halde `MAPPING_SUSPECT` — o sembol analizden DIŞLANIR.
    """
    if reference_price is None or reference_ms is None or not bar_rows:
        return {"state": MAPPING_NO_DATA, "detail": "referans fiyat/zaman ya da bar yok"}
    bar = None
    for b in bar_rows:
        if b["timestamp"] <= reference_ms < b["close_ms"]:
            bar = b
            break
    if bar is None:
        bar = min(bar_rows, key=lambda b: abs(b["timestamp"] - reference_ms))
    lo, hi = bar["low"], bar["high"]
    if lo <= reference_price <= hi:
        return {"state": MAPPING_OK, "detail": "giriş fiyatı bar aralığında",
                "bar_ts": bar["timestamp"], "low": lo, "high": hi}
    ref = reference_price or 1.0
    dist_pct = (min(abs(reference_price - lo), abs(reference_price - hi)) / abs(ref)) * 100.0
    state = MAPPING_OK if dist_pct <= tolerance_pct else MAPPING_SUSPECT
    return {"state": state, "detail": f"bar aralığı dışında, sapma %{round(dist_pct, 4)}",
            "bar_ts": bar["timestamp"], "low": lo, "high": hi,
            "deviation_pct": round(dist_pct, 6)}


__all__ = ["BarCache", "INTERVAL_MS", "MAPPING_NO_DATA", "MAPPING_OK", "MAPPING_SUSPECT",
           "SCHEMA_VERSION", "is_fetchable", "raw_symbol", "verify_mapping"]
