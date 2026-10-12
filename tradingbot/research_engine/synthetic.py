"""P3b — sentetik dünya: belirlenimci bar ve fonlama serileri (gerçek veri YOK, ağ yok). Yalnız kabul testleri, parite
(a) ve hız ölçümü içindir (§2.1, §5.3; "P3b'nin hız ölçümü yalnız sentetik veriyle yapılır", §8.2); gece birimi bu
modülü import etmez.

Seriler `random.Random(tohum)` ile üretilir (tohum sabit → bayt-aynı seri; `strategy_lib` saf kalır). Rejimli rastgele yürüyüş: her
`regime_len` barda bir yön/eğim seçilir (EMA200 kesişimleri, Donchian kırılımları ve mum formasyonları çıksın diye);
ara sıra açılış boşlukları (boşluk dolumu testleri). Günlük barlar 5m barlarından toplanır (Box'un kutusu tutarlı olsun).
4h/1d dünyalarında her sinyal barının **1h alt barları** (`subdivide`; ayrı tohumlu, sinyal serisini DEĞİŞTİRMEZ) barla
tutarlı kurulur: ilk açılış = barın açılışı, son kapanış = barın kapanışı, uçların en büyüğü/küçüğü = barın yüksek/düşüğü,
her alt bar barın aralığında (çekirdek ve parite (a) başvurusu canlı defter gibi 1h tikleriyle çıkar; §2.1).
Fonlama: 00/08/16 UTC uzlaşmaları, küçük oranlar, mark = o andaki barın açılışı.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Any

from . import kernel as KN

M5, H1, H4, D1 = 300_000, 3_600_000, 14_400_000, 86_400_000
T0 = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def walk(n: int, *, seed: int, step: int, start: int = T0, p0: float = 100.0, vol: float = 0.02, trend: float = 0.004,
         regime_len: int = 60, gap_p: float = 0.02, gap_vol: float = 0.03, wick: float = 0.6,
         up_bias: float = 0.0) -> list[dict[str, Any]]:
    rnd = random.Random(seed)
    out: list[dict[str, Any]] = []
    c = p0
    mu = 0.0
    for i in range(n):
        if i % regime_len == 0:
            mu = rnd.choice((-trend, 0.0, trend, trend * up_bias if up_bias else 0.0))
        o = c * math.exp(rnd.gauss(0.0, gap_vol)) if rnd.random() < gap_p else c
        r = mu + rnd.gauss(0.0, vol)
        cl = o * math.exp(r)
        hi = max(o, cl) * (1.0 + abs(rnd.gauss(0.0, vol * wick)))
        lo = min(o, cl) * (1.0 - abs(rnd.gauss(0.0, vol * wick)))
        out.append({"timestamp": start + i * step, "open": o, "high": hi, "low": lo, "close": cl,
                    "volume": 1000.0 * (1.0 + abs(rnd.gauss(0.0, 0.5)))})
        c = cl
    return out


def aggregate(rows: list[dict[str, Any]], step: int) -> list[dict[str, Any]]:
    """Küçük dilim barlarını `step` barlarına topla (yalnız TAM barlar)."""
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    n_need = None
    cnt = 0
    for r in rows:
        k = int(r["timestamp"]) // step * step
        if cur is None or cur["timestamp"] != k:
            if cur is not None and cnt == n_need:
                out.append(cur)
            cur = {"timestamp": k, "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
                   "volume": r["volume"]}
            n_need = step // (int(rows[1]["timestamp"]) - int(rows[0]["timestamp"]))
            cnt = 1
        else:
            cur["high"] = max(cur["high"], r["high"])
            cur["low"] = min(cur["low"], r["low"])
            cur["close"] = r["close"]
            cur["volume"] += r["volume"]
            cnt += 1
    if cur is not None and cnt == n_need:
        out.append(cur)
    return out


def subdivide(rows: list[dict[str, Any]], step: int, sub_step: int, *, seed: int) -> list[dict[str, Any]]:
    """Her `step` barını `step // sub_step` TUTARLI alt bara böl (belirlenimci, `random.Random(seed)`): yol noktaları
    açılıştan kapanışa bir Brown köprüsü (barın aralığına kırpılır); alt bar k: açılış = nokta k, kapanış = nokta k+1,
    uçlar gövdeden küçük fitillerle (aralığa kırpılır); barın yükseği yolun en yüksek, düşüğü en düşük alt barına
    yazılır. Böylece max(alt yüksek) = yüksek, min(alt düşük) = düşük, ilk açılış/son kapanış = bar açılış/kapanışı."""
    rnd = random.Random(seed)
    n = step // sub_step
    out: list[dict[str, Any]] = []
    for r in rows:
        o, h, lo, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        t0 = int(r["timestamp"])
        if n <= 1:
            out.append(dict(r))
            continue
        w = [0.0]
        for _k in range(n):
            w.append(w[-1] + rnd.gauss(0.0, 1.0))
        scale = (h - lo) / (2.0 * math.sqrt(n))
        pts = [min(h, max(lo, o + (c - o) * k / n + scale * (w[k] - w[n] * k / n))) for k in range(n + 1)]
        pts[0], pts[n] = o, c
        wick = (h - lo) * 0.15 / math.sqrt(n)
        subs = []
        for k in range(n):
            a, b = pts[k], pts[k + 1]
            subs.append([a, min(h, max(a, b) + abs(rnd.gauss(0.0, wick))), max(lo, min(a, b) - abs(rnd.gauss(0.0, wick))), b])
        k_hi = max(range(n), key=lambda k: (max(subs[k][0], subs[k][3]), -k))
        k_lo = min(range(n), key=lambda k: (min(subs[k][0], subs[k][3]), k))
        subs[k_hi][1], subs[k_lo][2] = h, lo
        vols = [rnd.random() + 0.2 for _k in range(n)]
        tot = sum(vols)
        for k, (a, hh, ll, b) in enumerate(subs):
            out.append({"timestamp": t0 + k * sub_step, "open": a, "high": hh, "low": ll, "close": b,
                        "volume": float(r.get("volume") or 0.0) * vols[k] / tot})
    return out


def funding_rows(rows: list[dict[str, Any]], step: int, *, seed: int, hours=(0, 8, 16)) -> list[tuple[int, float, float]]:
    """00/08/16 UTC uzlaşmaları; mark = uzlaşma anını içeren barın açılışı (tam açılışta o barın açılışı)."""
    rnd = random.Random(seed)
    if not rows:
        return []
    start, end = int(rows[0]["timestamp"]), int(rows[-1]["timestamp"]) + step
    by_open = {int(r["timestamp"]): r for r in rows}
    out = []
    t = start // H1 * H1
    while t <= end + D1:
        if (t // H1) % 24 in hours:
            k = t // step * step
            bar = by_open.get(k) or by_open.get(k - step)
            mark = float(bar["open"]) if bar is not None else float(rows[-1]["close"])
            out.append((t, rnd.gauss(0.0001, 0.0003), mark))
        t += H1
    return out


def series(symbol: str, bars: dict[str, list[dict[str, Any]]], *, signal_tf: str, btc: list | None = None,
           seed: int = 7, tick: float | None = None) -> KN.SeriesData:
    """`tick`: verilmezse ilk fiyatın ≈ 1e-5'i (10'un kuvveti; büyük coin'lerin göreli adımı mertebesinde)."""
    step = {"5m": M5, "1h": H1, "4h": H4, "1d": D1}[signal_tf]
    if tick is None:
        tick = 10.0 ** math.floor(math.log10(float(bars[signal_tf][0]["open"]) * 1e-5))
    return KN.SeriesData(symbol=symbol, bars=bars, btc_1d=btc, funding=funding_rows(bars[signal_tf], step, seed=seed),
                         price_tick=tick)


# ---------------------------------------------------------------------------- aile dünyaları
#: alt bar serisinin tohum kayması (sinyal serisinin tohumundan bağımsız; sinyal serisi değişmez)
SUB_SEED = 5000


def _with_1h(bars: dict[str, list], tf: str, seed: int, sub: bool) -> dict[str, list]:
    """`sub` → sinyal dilimine (4h/1d) tutarlı 1h alt barlar ekle (`subdivide`); `sub=False` alt barsız dünya (çekirdeğin
    `PATH_1H_GAP` yedeği için)."""
    if sub:
        step = {"4h": H4, "1d": D1}[tf]
        bars = dict(bars, **{"1h": subdivide(bars[tf], step, H1, seed=seed + SUB_SEED)})
    return bars


def trend_world(seed: int, n: int = 900, *, sub: bool = True) -> KN.SeriesData:
    """T2/M2: 1d; BTC uzun yükseliş rejimleri (rejim UP sık); coin EMA200 çevresinde sık kesişir."""
    btc = walk(n, seed=seed + 1000, step=D1, p0=20000.0, vol=0.025, trend=0.006, regime_len=150, up_bias=1.0)
    d1 = walk(n, seed=seed, step=D1, p0=50.0, vol=0.03, trend=0.004, regime_len=15, gap_p=0.03)
    return series(f"SYN{seed}", _with_1h({"1d": d1}, "1d", seed, sub), signal_tf="1d", btc=btc, seed=seed)


def d4_world(seed: int, n: int = 2200, *, sub: bool = True) -> KN.SeriesData:
    h4 = walk(n, seed=seed, step=H4, p0=30.0, vol=0.015, trend=0.004, regime_len=50, gap_p=0.02, gap_vol=0.015)
    return series(f"SYN{seed}", _with_1h({"4h": h4}, "4h", seed, sub), signal_tf="4h", seed=seed)


def c4_world(seed: int, n: int = 2600, *, sub: bool = True) -> KN.SeriesData:
    h4 = walk(n, seed=seed, step=H4, p0=30.0, vol=0.02, trend=0.003, regime_len=40, gap_p=0.01, gap_vol=0.01, wick=0.9)
    return series(f"SYN{seed}", _with_1h({"4h": h4}, "4h", seed, sub), signal_tf="4h", seed=seed)


def fm_world(seed: int, n: int = 2600, *, sub: bool = True) -> KN.SeriesData:
    h4 = walk(n, seed=seed, step=H4, p0=10.0, vol=0.015, trend=0.012, regime_len=12, gap_p=0.01, gap_vol=0.01,
              wick=0.25, up_bias=1.5)
    return series(f"SYN{seed}", _with_1h({"4h": h4}, "4h", seed, sub), signal_tf="4h", seed=seed)


def box_world(seed: int, days: int = 60, *, sub: bool = True) -> KN.SeriesData:
    """Box: sinyal dilimi 5m (≤ 1h) → çıkış tikleri 5m barlarının kendisidir; `sub` yok sayılır (dünyaların ortak imzası)."""
    del sub
    m5 = walk(days * 288, seed=seed, step=M5, p0=100.0, vol=0.0025, trend=0.0004, regime_len=36, gap_p=0.002,
              gap_vol=0.002, wick=0.8)
    d1 = aggregate(m5, D1)
    return series(f"SYN{seed}", {"1d": d1, "5m": m5}, signal_tf="5m", seed=seed)


#: K kopyası → aile birimi ve dünyası (parite (a) ve hız ölçümü)
WORLDS = {"T2": trend_world, "M2": trend_world, "BOX": box_world, "D4": d4_world, "C4": c4_world, "FM": fm_world}

__all__ = ["WORLDS", "D1", "H1", "H4", "M5", "SUB_SEED", "T0", "aggregate", "box_world", "c4_world", "d4_world", "fm_world",
           "funding_rows", "series", "subdivide", "trend_world", "walk"]
