"""P3b-2 — `strategy_lib` göstergeleri: TEK tanım, saf Python (G/Ç, saat, ağ, rastgelelik ve pandas YOK; AST testi).

Saf aileler (B, C, D, E, F1, H) göstergelerini yalnız buradan alır. Her gösterge depodaki BAŞVURU uygulamasının
tanımıdır ve altın testlerle ona eşitlenir (`tests/test_strategy_lib_indicators_p3b.py`):

* `ema` = `tradingbot.indicators.ema` (pandas `ewm(span, adjust=False, min_periods=span)`); `atr` =
  `tradingbot.indicators.atr` = `signal_lab.indicators()["atr"]` (Wilder, `ewm(alpha=1/n, adjust=False, min_periods=n)`,
  ilk barın gerçek aralığı yüksek − düşük); `rsi` = `tradingbot.indicators.rsi` (Wilder; ortalama kayıp 0 iken 100). Bu
  üçü pandas'ın `ewm` döngüsünün kayan nokta işlemlerini AYNI sırayla yapar (`com` → `alpha = 1 / (1 + com)`, bölmeyle
  normalleştirme, sabit seride güncelleme yok) → başvuruya bayt-aynı (test, rastgele serilerde).
* `bollinger` = `tradingbot.indicators.bollinger` (SMA20 ± 2 × popülasyon std); `bb_width` / `pct_rank` =
  `indicators_ext.bb_width_pct_rank`'in tanımı ((üst − alt) / orta; pencerede ortalama sıra / sayı); `keltner` =
  `indicators_ext.keltner(…, atr_len=14)` (orta EMA20 kapanış, ± çarpan × ATR14); `donchian_high/low` =
  `tradingbot.indicators.donchian` = `signal_lab.aux_series` `hi20/lo10` (ÖNCEKİ n barın en yükseği/düşüğü; mevcut bar
  dahil değil). Kayan toplamlar pandas'ın çevrimiçi toplamından yalnız yuvarlamada ayrılır (test: göreli ≤ 1e-9).
* `er` (Kaufman verim oranı, ER20): depoda başvurusu yok; tanım |c_t − c_{t−n}| / Σ_{k=t−n+1..t} |c_k − c_{k−1}|,
  payda 0 → None (ölçülemez; giriş yok). Elle hesaplanmış altın değerlerle sınanır.

Neden pandas çağrılmıyor: başvurular saf ama her karar barında DataFrame kurmak çağrı başına ≈ 1 ms (ATR) ister;
buradaki eşdeğerler aynı sayıları verir (testli) ve hız ölçümü (§5.3) bunlarla yapılır. Dizi girdileri değiştirilmez;
`NaN` = değer yok (ısınma).
"""
from __future__ import annotations

import math
from typing import Sequence

NAN = float("nan")


def _ok(x: float) -> bool:
    return x == x and x not in (math.inf, -math.inf)


# ============================================================================ üstel ortalamalar (pandas `ewm`, adjust=False)
def ewm_com(xs: Sequence[float], com: float, min_periods: int) -> list[float]:
    """pandas `Series.ewm(com=com, adjust=False, ignore_na=False).mean()` — aynı kayan nokta sırası (çevrimiçi ağırlık,
    `(eski_ağırlık × ağırlıklı + yeni_ağırlık × x) / (eski + yeni)`; ağırlıklı = x iken güncelleme yok); `min_periods`
    gözlem sayısıyla. NaN girdi gözlem sayılmaz (ağırlıklı ortalama NaN iken ilk gözlem onu başlatır)."""
    n = len(xs)
    out = [NAN] * n
    if n == 0:
        return out
    alpha = 1.0 / (1.0 + com)
    old_wt_factor = 1.0 - alpha
    new_wt = alpha
    weighted = float(xs[0])
    nobs = int(weighted == weighted)
    if nobs >= min_periods:
        out[0] = weighted
    old_wt = 1.0
    for i in range(1, n):
        cur = float(xs[i])
        is_obs = cur == cur
        nobs += is_obs
        if weighted == weighted:
            old_wt *= old_wt_factor
            if is_obs:
                if weighted != cur:
                    weighted = old_wt * weighted + new_wt * cur
                    weighted /= (old_wt + new_wt)
                old_wt = 1.0
        elif is_obs:
            weighted = cur
        if nobs >= min_periods:
            out[i] = weighted
    return out


def ema(xs: Sequence[float], span: int) -> list[float]:
    """`tradingbot.indicators.ema`: `ewm(span=span, adjust=False, min_periods=span)` (pandas: com = (span − 1) / 2)."""
    return ewm_com(xs, (span - 1) / 2, int(span))


def wilder(xs: Sequence[float], n: int) -> list[float]:
    """`ewm(alpha=1/n, adjust=False, min_periods=n)` (pandas: com = (1 − alpha) / alpha)."""
    a = 1.0 / n
    return ewm_com(xs, (1 - a) / a, int(n))


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    """`tradingbot.indicators.true_range`: ilk barda yüksek − düşük (önceki kapanış yok), sonra üçünün büyüğü."""
    n = len(close)
    out = [NAN] * n
    for i in range(n):
        h, lo = float(high[i]), float(low[i])
        a = h - lo
        if i == 0:
            out[i] = a
            continue
        pc = float(close[i - 1])
        out[i] = max(a, abs(h - pc), abs(lo - pc))
    return out


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> list[float]:
    """Wilder ATR (`tradingbot.indicators.atr`; `signal_lab.indicators()["atr"]` ile aynı)."""
    return wilder(true_range(high, low, close), n)


def rsi(close: Sequence[float], n: int = 14) -> list[float]:
    """Wilder RSI (`tradingbot.indicators.rsi`): ortalama kayıp 0 iken 100; ısınmada NaN."""
    m = len(close)
    gain = [NAN] * m
    loss = [NAN] * m
    for i in range(1, m):
        d = float(close[i]) - float(close[i - 1])
        gain[i] = d if d > 0.0 else 0.0
        loss[i] = -(d if d < 0.0 else 0.0)
    ag, al = wilder(gain, n), wilder(loss, n)
    out = [NAN] * m
    for i in range(m):
        g, lo = ag[i], al[i]
        if lo != lo:
            continue
        if lo == 0.0:
            out[i] = 100.0
            continue
        out[i] = 100.0 - (100.0 / (1.0 + g / lo))
    return out


# ============================================================================ pencere istatistikleri
def sma_at(xs: Sequence[float], length: int, i: int) -> float:
    """i. bardaki `length` barlık basit ortalama (i dahil); yetersiz veri → NaN."""
    if i < length - 1 or i >= len(xs):
        return NAN
    return math.fsum(float(x) for x in xs[i - length + 1:i + 1]) / length


def bollinger_at(close: Sequence[float], i: int, length: int = 20, mult: float = 2.0) -> tuple[float, float, float]:
    """(alt, orta, üst) — `tradingbot.indicators.bollinger` (SMA ± mult × popülasyon std, ddof = 0) i. barda."""
    if i < length - 1 or i >= len(close):
        return NAN, NAN, NAN
    w = [float(x) for x in close[i - length + 1:i + 1]]
    mid = math.fsum(w) / length
    sd = math.sqrt(math.fsum((x - mid) ** 2 for x in w) / length)
    return mid - mult * sd, mid, mid + mult * sd


def bb_width_at(close: Sequence[float], i: int, length: int = 20, mult: float = 2.0) -> float:
    """Bant genişliği (üst − alt) / orta = 2 × mult × std / orta (`indicators_ext.bb_width_pct_rank`'in `bbw`'si)."""
    lo, mid, up = bollinger_at(close, i, length, mult)
    if mid != mid or mid == 0.0:
        return NAN
    return (up - lo) / mid


def pct_rank(window: Sequence[float], x: float) -> float:
    """`x`'in `window` (x'i İÇEREN pencere) içindeki yüzdelik sırası: pandas `rank(pct=True)` ortalama yöntemi —
    (x'ten küçüklerin sayısı + (eşitlerin sayısı + 1) / 2) / pencere boyu. NaN içeren pencere → NaN."""
    if not window or any(v != v for v in window) or x != x:
        return NAN
    less = sum(1 for v in window if v < x)
    eq = sum(1 for v in window if v == x)
    return (less + (eq + 1) / 2.0) / len(window)


def donchian_high(high: Sequence[float], n: int, i: int) -> float:
    """ÖNCEKİ n barın (i hariç) en yükseği — `tradingbot.indicators.donchian` üst kanal, `signal_lab` `hi<n>`."""
    if i < n or i >= len(high):
        return NAN
    return max(float(x) for x in high[i - n:i])


def donchian_low(low: Sequence[float], n: int, i: int) -> float:
    """ÖNCEKİ n barın (i hariç) en düşüğü — `tradingbot.indicators.donchian` alt kanal, `signal_lab` `lo<n>`."""
    if i < n or i >= len(low):
        return NAN
    return min(float(x) for x in low[i - n:i])


def keltner_at(high: Sequence[float], low: Sequence[float], close: Sequence[float], i: int, *, length: int = 20,
               mult: float = 2.0, atr_len: int = 14, ema_series: Sequence[float] | None = None,
               atr_series: Sequence[float] | None = None) -> tuple[float, float, float]:
    """(alt, orta, üst) i. barda — `indicators_ext.keltner(high, low, close, length, mult, atr_len)`: orta = EMA(length)
    kapanış, bant = mult × ATR(atr_len). Hazır seriler verilebilir (aynı pencere)."""
    e = ema_series if ema_series is not None else ema(close, length)
    a = atr_series if atr_series is not None else atr(high, low, close, atr_len)
    mid, band = e[i], a[i]
    if mid != mid or band != band:
        return NAN, NAN, NAN
    return mid - mult * band, mid, mid + mult * band


def er(close: Sequence[float], n: int, i: int) -> float | None:
    """Kaufman verim oranı i. barda: |c_i − c_{i−n}| / Σ_{k=i−n+1..i} |c_k − c_{k−1}| ∈ [0, 1]. Yetersiz veri ya da
    hareketsiz pencere (payda 0) → None (ölçülemez)."""
    if n < 1 or i < n or i >= len(close):
        return None
    c = [float(x) for x in close[i - n:i + 1]]
    den = math.fsum(abs(c[k] - c[k - 1]) for k in range(1, n + 1))
    if not den > 0.0 or not _ok(den):
        return None
    return abs(c[-1] - c[0]) / den


__all__ = ["NAN", "atr", "bb_width_at", "bollinger_at", "donchian_high", "donchian_low", "ema", "er", "ewm_com",
           "keltner_at", "pct_rank", "rsi", "sma_at", "true_range", "wilder"]
