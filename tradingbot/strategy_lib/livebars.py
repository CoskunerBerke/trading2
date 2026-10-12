"""P3b — kopyaların (K_*) girdisi: canlı defterin kural fonksiyonuna verdiği satırların AYNISI (SAF; G/Ç yok).

Canlı motor kuralı, perp sağlayıcısının çerçevesinden okur: `engine.TradingEngine.PERP_FRAME_LIMITS` kadar satır (oluşan
bar dahil) → oluşan bar düşer → günlük çerçeveye `indicators.ema`/`atr` ile `ema200`/`atr14` → kuralın kendi satır
okuyucusu (`paper_rules.daily_rows`/`intraday_rows`, `candle_book.window_rows`). EMA200 değeri pencerenin başına bağlıdır
(`adjust=False` tohumu), bu yüzden kopya pencereyi kısaltmaz/uzatmaz. Sayılar motorun yeniden kurma modülüyle aynıdır
(`LIVE_CLOSED_BARS`; testte `engine.TradingEngine.PERP_FRAME_LIMITS − 1`'e eşitlik). Formasyon defteri (pattern_trader)
4h analizini kendi veri servisinin penceresiyle yapar (`pattern_trader.data.BARS_PER_TF["4h"]`; testte eşitlik).

**Hızlı çerçeve (P3b-2b, §5.3).** Çerçeve her karar barında pandas'la kurulunca kopyanın süresinin çoğu DataFrame kurma
ve `to_dict`'tir (ölçüldü: Box ≈ %90, C4 ≈ %75, T2/M2 ≈ %35). `fast=True` aynı çerçeveyi `FastFrame` olarak kurar:
sütunlar düz Python listeleri, `ema200`/`atr14` `strategy_lib.indicators`'ın pandas `ewm` döngüsüyle bayt-aynı
eşdeğerleriyle (P3b-2a, testli). `FastFrame` canlı satır okuyucularının kullandığı yüzün TAMAMINI verir (`columns`,
`tail(n)`, `frame[sütunlar]`, `to_dict("records")`; pandas'ın Python türleriyle: `timestamp` int, diğerleri float, NaN
dahil) — okuyucular (`ema200_trend.daily_rows_from_frame`, `box_theory.rows_from_frame`, `candle_book.rows_from_frame`,
`paper_rules.decide_with_structures`'ın `_frames_rows`'u) DEĞİŞMEDEN çağrılır, yani kesim/sütun/kapanmış-bar kuralları
yine canlı koddur; yalnız çerçeve nesnesi farklıdır. Eşitlik testlidir: rastgele ve uç pencerelerde iki çerçevenin her
okuyucu çıktısı (değer, tür, anahtar sırası, NaN) aynı; kopyaların çekirdek koşusu iki yolda bayt-aynı
(`tests/test_strategy_lib_covers_p3b.py`). `fast=False` canlı (pandas) yoldur — başvuru ve eşitlik testleri onu kullanır.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .. import candle_book, candle_dsl, ema200_trend, indicators, paper_rules
from ..candle_confirmation import closed_bars
from . import indicators as SI

#: canlı perp çerçevesinin KAPANMIŞ bar sayısı (PERP_FRAME_LIMITS − oluşan bar)
LIVE_CLOSED_BARS: Mapping[str, int] = {"1d": 399, "4h": 699, "1h": 499, "5m": 499, "15m": 499}
#: Formasyon defterinin 4h analiz penceresi (pattern_trader veri servisi)
FM_BARS_4H = 240
_COLS = ("timestamp", "open", "high", "low", "close", "volume")
_IND = ("ema200", "atr14")


def last_closed(bars: Sequence[Mapping[str, Any]] | None, n: int) -> list[Mapping[str, Any]]:
    seq = list(bars or ())
    return seq[-int(n):] if n > 0 else []


class FastFrame:
    """Canlı çerçevenin pandas'sız eşdeğeri: yalnız canlı satır okuyucularının kullandığı yüz (modül belgesi). Sütun
    verisi paylaşılır (salt-okunur); `tail` ve sütun seçimi yeni bir görünüm döndürür, `to_dict("records")` her çağrıda
    YENİ sözlükler kurar (pandas gibi; okuyucular arası paylaşılan değişebilir nesne yok)."""

    __slots__ = ("columns", "_data", "_a", "_b")

    def __init__(self, data: Mapping[str, list], columns: Sequence[str], a: int = 0, b: int | None = None):
        self._data = data
        self.columns = list(columns)
        n = len(data[self.columns[0]]) if self.columns else 0
        self._a, self._b = int(a), (n if b is None else int(b))

    def __len__(self) -> int:
        return self._b - self._a

    def tail(self, n: int = 5) -> "FastFrame":
        """pandas `DataFrame.tail`: n ≥ 0 → son n satır (n = 0 → boş); n < 0 → ilk |n| satır hariç hepsi."""
        n = int(n)
        a = (max(self._a, self._b - n) if n > 0 else self._b) if n >= 0 else min(self._b, self._a - n)
        return FastFrame(self._data, self.columns, a, self._b)

    def __getitem__(self, cols: Sequence[str]) -> "FastFrame":
        if isinstance(cols, str) or not isinstance(cols, (list, tuple)):
            raise TypeError("FastFrame yalnız sütun listesiyle seçilir")
        missing = [c for c in cols if c not in self._data]
        if missing:
            raise KeyError(f"{missing} not in index")
        return FastFrame(self._data, list(cols), self._a, self._b)

    def to_dict(self, orient: str = "dict") -> list[dict[str, Any]]:
        if orient != "records":
            raise ValueError("FastFrame yalnız to_dict('records') verir")
        cols = self.columns
        a, b = self._a, self._b
        return [dict(zip(cols, vals)) for vals in zip(*(self._data[c][a:b] for c in cols))]


def fast_frame(bars: Sequence[Mapping[str, Any]] | None, tf: str, *, daily_indicators: bool = False) -> FastFrame | None:
    """`frame`'in pandas'sız eşdeğeri (aynı pencere, aynı dönüşümler: `int(timestamp)`, `float(OHLC)`,
    `float(volume or 0.0)`; günlükte `ema200`/`atr14` = `indicators.ema`/`atr`'nin bayt-aynı eşdeğerleri)."""
    rows = last_closed(bars, LIVE_CLOSED_BARS[tf])
    if not rows:
        return None
    data: dict[str, list] = {
        "timestamp": [int(r["timestamp"]) for r in rows], "open": [float(r["open"]) for r in rows],
        "high": [float(r["high"]) for r in rows], "low": [float(r["low"]) for r in rows],
        "close": [float(r["close"]) for r in rows], "volume": [float(r.get("volume") or 0.0) for r in rows]}
    if not all(np.isfinite(data[c]).all() for c in ("open", "high", "low", "close")):
        # NaN/inf OHLC: pandas yolu (gerçek aralıkta max(axis=1) NaN'ı atlar; saf atr NaN'ı yayar) — inceleme P3b-2b bulgu 1
        return frame(bars, tf, daily_indicators=daily_indicators)
    cols = _COLS
    if daily_indicators:
        data["ema200"] = SI.ema(data["close"], 200)
        data["atr14"] = SI.atr(data["high"], data["low"], data["close"], 14)
        cols = _COLS + _IND
    return FastFrame(data, cols)


def frame(bars: Sequence[Mapping[str, Any]] | None, tf: str, *, daily_indicators: bool = False,
          fast: bool = False) -> Any:
    """Canlı çerçevenin kapanmış kısmı (son `LIVE_CLOSED_BARS[tf]` bar) DataFrame olarak; günlükte `ema200`/`atr14`.
    `fast=True` → aynı çerçeve `FastFrame` olarak (modül belgesi)."""
    if fast:
        return fast_frame(bars, tf, daily_indicators=daily_indicators)
    rows = last_closed(bars, LIVE_CLOSED_BARS[tf])
    if not rows:
        return None
    df = pd.DataFrame([{k: (int(r[k]) if k == "timestamp" else float(r.get(k) or 0.0) if k == "volume" else float(r[k]))
                        for k in _COLS} for r in rows])
    if daily_indicators:
        df["ema200"] = indicators.ema(df["close"], 200)
        df["atr14"] = indicators.atr(df["high"], df["low"], df["close"], 14)
    return df


def daily_rows(bars_1d: Sequence[Mapping[str, Any]] | None, now_ms: int, *, fast: bool = False) -> list[dict[str, Any]]:
    """`paper_rules.daily_rows` — canlı `step` ve gösterimle AYNI okuma."""
    return paper_rules.daily_rows({"1d": frame(bars_1d, "1d", daily_indicators=True, fast=fast)}, now_ms=int(now_ms))


def intraday_rows(bars_tf: Sequence[Mapping[str, Any]] | None, tf: str, now_ms: int, *,
                  fast: bool = False) -> list[dict[str, Any]]:
    return paper_rules.intraday_rows({tf: frame(bars_tf, tf, fast=fast)}, tf=tf, now_ms=int(now_ms))


def candle_rows(bars_4h: Sequence[Mapping[str, Any]] | None, now_ms: int, *, fast: bool = False) -> list[dict[str, Any]]:
    """Mum varyasyonlarının penceresi: `candle_book.window_rows` (son 500 kapanmış 4h bar, hacim dahil)."""
    return candle_book.window_rows({candle_book.TIMEFRAME: frame(bars_4h, candle_book.TIMEFRAME, fast=fast)},
                                   now_ms=int(now_ms))


def candle_window(bars_4h: Sequence[Mapping[str, Any]] | None, now_ms: int, *, fast: bool = False) -> tuple[Any, str | None]:
    """Mum varyasyonlarının penceresi ve okunamazlık nedeni: `candle_dsl.window_from_rows(candle_rows(…), STEP_MS)`.
    `fast=True` → aynı pencere, satır sözlükleri kurulmadan: `FastFrame`'in sütunlarından aynı kesim (son 502 → kapanmış →
    son 500) ve `window_from_rows`'un aynı denetimleri aynı sırayla (uzunluk, bitişiklik, OHLC sonlu ve tutarlı, hacim
    sonlu ve ≥ 0); diziler aynı float64/int64 değerleri (testli)."""
    step = candle_book.STEP_MS
    if not fast:
        return candle_dsl.window_from_rows(candle_rows(bars_4h, now_ms), step)
    fr = fast_frame(bars_4h, candle_book.TIMEFRAME)
    if fr is None:
        return candle_dsl.window_from_rows([], step)
    if not isinstance(fr, FastFrame):                      # NaN/inf OHLC → fast_frame pandas'a döndü: yavaş yol
        return candle_dsl.window_from_rows(candle_rows(bars_4h, now_ms), step)
    d, n, w = fr._data, len(fr), candle_book.WINDOW
    a = max(0, n - (w + 2))
    now = float(now_ms)
    idx = [j for j in range(a, n) if float(d["timestamp"][j]) + step <= now][-w:]
    if len(idx) != w:
        return None, "NOT_ENOUGH_4H_BARS"
    ts = np.array([d["timestamp"][j] for j in idx], dtype=np.int64)
    if step <= 0 or not bool(np.all(np.diff(ts) == step)):
        return None, "ROWS_NOT_CONTIGUOUS"
    o, h, lo, c = (np.array([d[k][j] for j in idx], dtype=float) for k in ("open", "high", "low", "close"))
    if not (bool(np.isfinite(o).all()) and bool(np.isfinite(h).all()) and bool(np.isfinite(lo).all())
            and bool(np.isfinite(c).all())) or bool((h < np.maximum(o, c)).any()) or bool((lo > np.minimum(o, c)).any()):
        return None, "ROWS_UNREADABLE"
    v = np.array([d["volume"][j] for j in idx], dtype=float)
    if not bool(np.isfinite(v).all()) or bool((v < 0).any()):
        return None, "VOLUME_MISSING"
    return candle_dsl.Window(ts=ts, o=o, h=h, l=lo, c=c, v=v, step_ms=step), None


def btc_daily_rows(btc_1d: Sequence[Mapping[str, Any]] | None, now_ms: int, *, fast: bool = False) -> list[dict[str, Any]]:
    """BTC rejim satırları: canlı `step` ile aynı — `closed_bars(ema200_trend.daily_rows_from_frame(btc_1d))`."""
    fr = frame(btc_1d, "1d", daily_indicators=True, fast=fast)
    return closed_bars(ema200_trend.daily_rows_from_frame(fr), now_ms=int(now_ms), tf="1d")


def fm_rows(bars_4h: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Formasyon v3 penceresi: son `FM_BARS_4H` kapanmış 4h bar (sözlük; analiz ve RSI aynı satırları okur)."""
    return [{k: (int(r[k]) if k == "timestamp" else float(r.get(k) or 0.0) if k == "volume" else float(r[k])) for k in _COLS}
            for r in last_closed(bars_4h, FM_BARS_4H)]


__all__ = ["FM_BARS_4H", "FastFrame", "LIVE_CLOSED_BARS", "btc_daily_rows", "candle_rows", "candle_window", "daily_rows",
           "fast_frame",
           "fm_rows", "frame", "intraday_rows", "last_closed"]
