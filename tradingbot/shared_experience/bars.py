# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — BAR KAYNAĞI (2026-09-29). Varsayılan AĞSIZ; kaynakları SALT OKUR, hiçbir şey yazmaz.

Sıra (SPEC_V1 §3.1, görev X3):

1. **Tur çerçevesi** (`runner.last_frames`) — YALNIZ bu turun provenansı `market == "USDM_PERP"` ise. Alınma kuralı
   `fetch_lb_ms = tur başlangıcı` (`engine_v3._tour_now_ms`): çerçeveler tur başladıktan SONRA alınır; tur başından önce
   kapanmış bar alındığında tamdı, sonrasında kapanan bar kısmi (oluşan) satır olabilir → dışlanır.
2. **CSV önbelleği** (`pattern_trader.data.CsvCandleCache`, Formasyon'un kapanmış-bar dosyaları) — yapısı gereği yalnız
   kapanmış bar tutar → `fetch_lb_ms = None`. Adım başına (sembol, dilim) bir kez okunur (≤ 720 satır).
3. **Tembel çekim** (isteğe bağlı, `fetch_budget` varsayılan 0 → HİÇ çağrılmaz). Verilirse adım başına en çok
   `fetch_budget` çekim; alınma kuralı çekim anının duvar saati.
4. Hiçbiri TAM pencere vermezse en iyi eksik pencere (en yeni son bar, sonra en çok bar) döner — toplayıcı satırı
   TASLAK (PENDING) tutar; yaş sınırında durum GAP / NO_BARS olarak yazılır.

Pencere ilk TAM kaynaktan gelir; aynı kapanmış barlar hangi kaynaktan gelirse gelsin `situation.normalize_rows` aynı
pencereyi üretir (T2 parite testi). `source` alanı yalnız bilgidir (satırın `snapshot_meta`sına gider).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from . import situation as S

SOURCE_TOUR = "tour_frames"
SOURCE_CSV = "csv_cache"
SOURCE_FETCH = "lazy_fetch"
SOURCE_NONE = "none"
PERP_MARKET = "USDM_PERP"
#: Tembel çekimde istenecek bar sayısı (W + kapanmamış son bar payı) (2026-09-29).
FETCH_LIMIT = S.W_MAX + 2


def symbol_aliases(symbol: str) -> tuple[str, ...]:
    """`SOL/USDT:USDT` → (`SOL/USDT:USDT`, `SOL/USDT`); sıradan sembol tek başına (2026-09-29)."""
    s = str(symbol or "")
    core = s.split(":")[0]
    return (s,) if core == s else (s, core)


def _better(w: S.Window, best: S.Window | None) -> bool:
    if best is None:
        return True
    a = -1 if w.last_open_ms is None else int(w.last_open_ms)
    b = -1 if best.last_open_ms is None else int(best.last_open_ms)
    return (a, w.n) > (b, best.n)


class BarSource:
    """Bir toplayıcı adımının bar kaynağı (adım başına YENİ örnek; bellek adım sonunda bırakılır) (2026-09-29).

    `window()` aynı (sembol, dilim, as_of, W) için adım içinde bellekten döner (BTC penceresi her satırda yeniden
    normalleştirilmez). Kaynaklar yalnız OKUNUR: çerçeve sözlüğü, provenans ve CSV dosyaları değiştirilmez."""

    def __init__(self, *, frames: Mapping[str, Any] | None, provenance: Mapping[str, Any] | None,
                 tour_now_ms: int | None, csv_dir: Path | str | None,
                 fetcher: Callable[[str, str, int], Any] | None = None, fetch_budget: int = 0,
                 wall_ms: Callable[[], int] | None = None) -> None:
        self.frames = frames if isinstance(frames, Mapping) else {}
        self.provenance = provenance if isinstance(provenance, Mapping) else {}
        self.tour_now_ms = int(tour_now_ms) if tour_now_ms else None
        self.csv_dir = Path(csv_dir) if csv_dir else None
        self.fetcher = fetcher
        self.fetch_budget = max(0, int(fetch_budget or 0))
        self._wall_ms = wall_ms
        self._csv: dict[tuple[str, str], Any] = {}
        self._fetched: dict[tuple[str, str], tuple[Any, int]] = {}
        self._win: dict[tuple[str, str, int, int], S.Window] = {}
        self._csv_cache: Any = None
        self.counters: dict[str, int] = {"windows": 0, "memo_hits": 0, "tour_frames": 0, "csv_cache": 0,
                                         "lazy_fetch": 0, "incomplete": 0, "csv_errors": 0, "fetch_errors": 0,
                                         "fetch_calls": 0, "frame_errors": 0}

    # ------------------------------------------------------------------ kaynaklar
    def _tour_frame(self, symbol: str, tf: str) -> Any:
        """Bu turun USDⓈ-M perp provenanslı çerçevesi; provenans yoksa/SPOT ise None (SPOT ikamesi ASLA kullanılmaz)."""
        if self.tour_now_ms is None:
            return None                                   # alınma sınırı bilinmiyor → tur çerçevesi kullanılamaz
        for s in symbol_aliases(symbol):
            prov = self.provenance.get(s)
            if not isinstance(prov, Mapping) or str(prov.get("market") or "") != PERP_MARKET:
                continue
            fr = self.frames.get(s)
            df = fr.get(tf) if isinstance(fr, Mapping) else None
            if df is not None:
                return df
        return None

    def _csv_rows(self, symbol: str, tf: str) -> Any:
        key = (str(symbol), str(tf))
        if key in self._csv:
            return self._csv[key]
        df = None
        if self.csv_dir is not None:
            try:
                if self._csv_cache is None:
                    from ..pattern_trader.data import CsvCandleCache       # salt okuma (write çağrılmaz)
                    self._csv_cache = CsvCandleCache(self.csv_dir)
                p = self._csv_cache.path(symbol, tf)
                if p.exists():
                    df = self._csv_cache.read(symbol, tf)
            except Exception:  # noqa: BLE001 — (2026-09-29) bozuk CSV satırı kayıt katmanını durdurmaz; sayılır
                self.counters["csv_errors"] += 1
                df = None
        self._csv[key] = df
        return df

    def _fetch_rows(self, symbol: str, tf: str) -> tuple[Any, int | None]:
        key = (str(symbol), str(tf))
        if key in self._fetched:
            return self._fetched[key]
        if self.fetcher is None or self.counters["fetch_calls"] >= self.fetch_budget:
            return None, None
        self.counters["fetch_calls"] += 1
        try:
            at = int(self._wall_ms()) if self._wall_ms is not None else None
            raw = self.fetcher(symbol, tf, FETCH_LIMIT)
        except Exception:  # noqa: BLE001 — (2026-09-29) ağ arızası satırı TASLAKTA bırakır; karar etkilenmez
            self.counters["fetch_errors"] += 1
            raw, at = None, None
        if at is None:
            raw = None                                    # alınma anı bilinmeden kısmi bar dışlanamaz → kullanılmaz
        self._fetched[key] = (raw, at)
        return raw, at

    # ------------------------------------------------------------------ pencere
    def window(self, symbol: str, tf: str, *, as_of_ms: int, W: int) -> S.Window:
        """İlk TAM kaynağın penceresi; yoksa en iyi eksik pencere; hiç bar yoksa boş pencere (source="none")."""
        key = (str(symbol), str(tf), int(as_of_ms), int(W))
        hit = self._win.get(key)
        if hit is not None:
            self.counters["memo_hits"] += 1
            return hit
        self.counters["windows"] += 1
        best: S.Window | None = None
        done = False
        df = None
        try:
            df = self._tour_frame(symbol, tf)
        except Exception:  # noqa: BLE001
            self.counters["frame_errors"] += 1
        if df is not None:
            w = S.normalize_rows(df, tf, as_of_ms, W=W, fetch_lb_ms=self.tour_now_ms, source=SOURCE_TOUR)
            if S.is_complete(w, as_of_ms):
                best, done = w, True
                self.counters["tour_frames"] += 1
            elif _better(w, best):
                best = w
        if not done:
            rows = self._csv_rows(symbol, tf)
            if rows is not None and len(rows):
                w = S.normalize_rows(rows, tf, as_of_ms, W=W, fetch_lb_ms=None, source=SOURCE_CSV)
                if S.is_complete(w, as_of_ms):
                    best, done = w, True
                    self.counters["csv_cache"] += 1
                elif _better(w, best):
                    best = w
        if not done and self.fetch_budget > 0 and self.fetcher is not None:
            raw, at = self._fetch_rows(symbol, tf)
            if raw is not None and at is not None:
                w = S.normalize_rows(raw, tf, as_of_ms, W=W, fetch_lb_ms=at, source=SOURCE_FETCH)
                if S.is_complete(w, as_of_ms):
                    best, done = w, True
                    self.counters["lazy_fetch"] += 1
                elif _better(w, best):
                    best = w
        if best is None:
            best = S.empty_window(tf, SOURCE_NONE)
        if not done:
            self.counters["incomplete"] += 1
        self._win[key] = best
        return best


__all__ = ["BarSource", "FETCH_LIMIT", "PERP_MARKET", "SOURCE_CSV", "SOURCE_FETCH", "SOURCE_NONE", "SOURCE_TOUR",
           "symbol_aliases"]
