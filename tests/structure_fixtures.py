# -*- coding: utf-8 -*-
"""SENTETİK (etiketli) OHLCV kurucuları — ortak yapı kataloğu testleri için.

Amaç: dedektörü TAKLİT ETMEDEN, gerçek `structures.analyze`in istenen yapıyı kendi kuralıyla bulduğu seriler. Arka plan
trendi "mum-nötr" bir iki-bar motifidir (büyük boğa + küçük ayı; gövde/fitil oranları katalog eşiklerinin DIŞINDA):
hiçbir tek/iki/üç barlı mum adı üretmez (test bunu ayrıca doğrular). Üzerine elle tanımlı yapı parçaları eklenir.
Fiyatlar göreli oranlarla kurulur; zaman damgaları bar AÇILIŞIDIR.
"""
from __future__ import annotations

from typing import Any

DAY = 86_400_000
H4 = 4 * 3_600_000
H1 = 3_600_000
M15 = 15 * 60_000
M5 = 5 * 60_000


def _bar(ts: int, o: float, h: float, lo: float, c: float, v: float = 1000.0) -> dict[str, Any]:
    assert h >= max(o, c) and lo <= min(o, c) and lo > 0, (ts, o, h, lo, c)
    return {"timestamp": int(ts), "open": float(o), "high": float(h), "low": float(lo), "close": float(c), "volume": float(v)}


def neutral_trend(n: int, *, start_ms: int, step: int, px0: float = 100.0, up: bool = True, scale: float = 1.0) -> list[dict[str, Any]]:
    """Mum-nötr trend: çift barlar büyük boğa (+2%, gövde %67 aralık, fitiller eşit), tek barlar küçük ayı (−0.6%, gövde
    %40, üst fitil %47 < 0.55). `up=False` aynası (düşüş). Kapanış serisi monoton değildir ama eğimi belirgindir."""
    rows: list[dict[str, Any]] = []
    px = px0
    for i in range(n):
        ts = start_ms + i * step
        if i % 2 == 0:
            # önceki küçük ayının kapanışının biraz üstünden açılır: gövde onu YUTMAZ (yutan/delici oluşmaz); alt fitil
            # (trend yönünün tersi) belirgin uzun: önceki barın dibiyle cımbız OLUŞMAZ (fark > 0.1 × aralık)
            o = px * (1 + 0.001 * scale) if up else px * (1 - 0.001 * scale)
            c = o * (1 + 0.02 * scale) if up else o * (1 - 0.02 * scale)
            body = abs(c - o)
            back, fwd = 0.45 * body, 0.15 * body          # geri (trend tersi) ve ileri fitil
            if up:
                hi, lo = c + fwd, o - back
            else:
                lo, hi = c - fwd, o + back
        else:
            o = px * (1 + 0.002 * scale) if up else px * (1 - 0.002 * scale)
            c = o * (1 - 0.006 * scale) if up else o * (1 + 0.006 * scale)
            body = abs(o - c)
            rng = body / 0.40
            if up:
                hi = max(o, c) + 0.47 * rng
                lo = min(o, c) - (rng - body - 0.47 * rng)
            else:
                lo = min(o, c) - 0.47 * rng
                hi = max(o, c) + (rng - body - 0.47 * rng)
        rows.append(_bar(ts, o, hi, lo, c))
        px = c
    return rows


def extend(rows: list[dict[str, Any]], ohlc: list[tuple[float, float, float, float]], *, step: int) -> list[dict[str, Any]]:
    """Göreli (son kapanışa oran) OHLC dörtlüleriyle bar ekler: (o, h, l, c) × son kapanış."""
    out = list(rows)
    base = out[-1]["close"]
    t = out[-1]["timestamp"]
    for o, h, lo, c in ohlc:
        t += step
        out.append(_bar(t, base * o, base * h, base * lo, base * c))
    return out


def pole_and_consolidation(rows: list[dict[str, Any]], *, step: int, shape: str = "parallel", n: int = 5,
                           pole_scale: float = 3.0) -> list[dict[str, Any]]:
    """Mum-nötr direk (8 bar, nötr motif `pole_scale`) + `n` DOJI konsolidasyon barı. `shape`: `parallel` (tepeler ve
    dipler aynı hızla iner: BAYRAK) | `converging` (tepeler iner, dipler yükselir: FLAMA). Dojiler tarafsızdır (taraf
    üretmez); ilk doji önceki gövdenin DIŞINDA (harami/doji yıldızı oluşmaz)."""
    out = list(rows)
    out += neutral_trend(8, start_ms=out[-1]["timestamp"] + step, step=step, px0=out[-1]["close"], up=True, scale=pole_scale)
    last = out[-1]
    h0, l0 = last["close"] * 1.004, last["close"] * 0.985
    for k in range(n):
        if shape == "parallel":
            hi, lo = h0 - k * last["close"] * 0.004, l0 - k * last["close"] * 0.004
        else:
            hi, lo = h0 - k * last["close"] * 0.0015, l0 + k * last["close"] * 0.001
        mid = (hi + lo) / 2.0
        out.append(_bar(last["timestamp"] + (k + 1) * step, mid, hi, lo, mid))
    return out


def breakout_bar(rows: list[dict[str, Any]], *, step: int, above: float, pct: float = 0.012) -> list[dict[str, Any]]:
    """`above` seviyesinin %pct üstünde kapanan tek boğa barı (açılış önceki kapanışın hemen üstünde)."""
    lb = rows[-1]
    o = lb["close"] * 1.002
    c = above * (1 + pct)
    return list(rows) + [_bar(lb["timestamp"] + step, o, c * 1.002, o * 0.995, c)]


def bearish_engulf_confirmed(rows: list[dict[str, Any]], *, step: int) -> list[dict[str, Any]]:
    """Boğa bar + onu yutan ayı bar (BEARISH_ENGULFING) + yutanın dibinin ALTINDA kapanan teyit barı."""
    lb = rows[-1]
    p = lb["close"]
    t = lb["timestamp"]
    b1 = _bar(t + step, p * 1.001, p * 1.013, p * 0.999, p * 1.011)          # boğa
    b2 = _bar(t + 2 * step, p * 1.013, p * 1.015, p * 0.992, p * 0.994)      # ayı, b1 gövdesini yutar
    b3 = _bar(t + 3 * step, p * 0.993, p * 0.994, p * 0.982, p * 0.984)      # b2 dibinin (0.992) altında kapanış
    return list(rows) + [b1, b2, b3]


def swing_high_then_sweep(rows: list[dict[str, Any]], *, step: int) -> list[dict[str, Any]]:
    """Belirgin tepe (sağında/solunda 3 bar daha düşük tepeler) + tepeyi fitille aşıp ALTINDA kapanan süpürme barı."""
    out = list(rows)
    p = out[-1]["close"]
    t = out[-1]["timestamp"]
    seq = [(1.002, 1.008, 0.998, 1.006), (1.006, 1.014, 1.003, 1.012), (1.012, 1.030, 1.010, 1.026),   # tepe barı
           (1.024, 1.027, 1.012, 1.016), (1.016, 1.019, 1.006, 1.010), (1.010, 1.013, 1.001, 1.005),
           (1.006, 1.036, 1.004, 1.020)]                                                                   # süpürme: 1.036 > 1.030, kapanış 1.020 < 1.030
    for i, (o, h, lo, c) in enumerate(seq):
        out.append(_bar(t + (i + 1) * step, p * o, p * h, p * lo, p * c))
    return out


def box_day(*, day0: int, box=(95.0, 105.0), ending: str = "sweep") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Box senaryosu: (günlük satırlar, 5m satırlar). Dünkü günlük bar kutuyu verir (dip, tepe); bugün 5m'de fiyat
    mum-nötr motifle kutu tepesine tırmanır, sonra `ending`:
    * `sweep`    — tek bar kutu tepesini FİTİLLE aşar ve İÇERİDE kapanır (SWEEP_RECLAIM, SHORT),
    * `breakout` — kutu tepesinin ÜSTÜNDE ardışık iki kapanış (RANGE_BREAKOUT, LONG) — dönüş planını iptal eder,
    * `none`     — tepeye yakın, yapı yok."""
    lo, hi = box
    daily = []
    for i in range(5, 0, -1):
        ts = day0 - i * DAY
        if i == 1:
            daily.append(_bar(ts, 100.0, hi, lo, 101.0))
        else:
            daily.append(_bar(ts, 100.0, 102.0, 98.0, 100.5))
    m5 = neutral_trend(40, start_ms=day0, step=M5, px0=101.0, up=True, scale=0.1)
    t = m5[-1]["timestamp"]
    top_px = m5[-1]["close"]
    if ending == "sweep":
        m5.append(_bar(t + M5, top_px * 1.0005, hi * 1.023, top_px * 0.9985, hi * 0.996))
    elif ending == "breakout":
        m5.append(_bar(t + M5, top_px * 1.0005, hi * 1.008, top_px * 0.999, hi * 1.005))
        m5.append(_bar(t + 2 * M5, hi * 1.005, hi * 1.012, hi * 1.002, hi * 1.009))
    return daily, m5
