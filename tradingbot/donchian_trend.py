# -*- coding: utf-8 -*-
"""4 SAATLİK TREND TAKİBİ (Donchian 20/10, yalnız LONG) — GÖZLEM DEFTERİ kuralı; canlı motor ile replay'in ORTAK tek
kaynağı (2026-09-25).

KANITLANMADI. Sinyal laboratuvarının algoritma çalıştırmasında (GitHub run 36150821072: 30 coin, 4h 4 yıl) sıkı testi
GEÇEMEDİ; en yakın aday oldu. Keşif dönemi +0,42R (%95 aralık +0,18…+0,70), doğrulama dönemi +0,05R (aralık −0,21…+0,34,
sıfırı kapsıyor). Rastgele girişli eşine (aynı çıkış) göre iki dönemde de önde. Defter, bu kuralın canlıda nasıl
davrandığını ÖLÇMEK için açılır; kâr beklentisi değildir. PAPER, gerçek para YOK.

Kural (laboratuvardaki `TREND_DONCHIAN_20_10` birebir; tanım sabit, sonuca göre AYARLANMAZ):

* Giriş: kapanmış 4h barın kapanışı ÖNCEKİ 20 barın en yükseğini İLK kez aşar (bir önceki kapanış aşmamıştı) → LONG.
  Giriş fiyatı, sinyal kapanışından sonraki ilk doğrulanmış perp fiyatıdır (laboratuvarda sonraki barın açılışı).
* İlk stop: sinyal kapanışı − 2 × ATR14(4h).
* Çıkış: kapanmış 4h barın kapanışı ÖNCEKİ 10 barın en düşüğünün altına iner → sonraki fiyattan kapat.
* Zaman sınırı: 300 bar (50 gün).
* Hedef yok; kâr, çıkış kuralı gelene kadar koşar.

Göstergeler laboratuvarın KENDİ fonksiyonlarıyla hesaplanır (`signal_lab.indicators`, `signal_lab.aux_series`); ikinci
bir formül yoktur. Parite testi: `tests/test_donchian_trend_book.py`.

Fonksiyonlar SAFTIR: dosya/ağ yok, girdiler değiştirilmez. Barlar KRONOLOJİK ve KAPANMIŞ olmalı (çağıran `closed_bars`
uygular).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .timeframes import tf_ms

VARIANTS = ("d4_donchian_20_10",)
TIMEFRAME = "4h"
STEP_MS = tf_ms(TIMEFRAME)
#: Laboratuvardaki adı (`signal_lab.ALGOS`).
LAB_ALGO = "TREND_DONCHIAN_20_10"
#: Laboratuvarla AYNI sabitler (`algo_events` / `PLACEBO_SPECS` / `simulate_rule`).
ENTRY_N, EXIT_N, STOP_ATR, MAX_BARS = 20, 10, 2.0, 300
MIN_RISK_ATR, MAX_RISK_ATR = 0.1, 10.0
#: ATR14 ısınması + 21 barlık kanal için gereken en az kapanmış bar.
MIN_BARS = 60
EVIDENCE = {
    "verdict": "KANITLANMADI (laboratuvarın sıkı testini geçemedi; gözlem defteri)",
    "lab_run": "https://github.com/CoskunerBerke/trading2/actions/runs/36150821072",
    "universe": "30 coin (signal_lab.WIDE_SYMBOLS), 4h, 4 yıl",
    "in_sample": {"n": 5728, "mean_r": 0.419, "ci95": [0.18, 0.70]},
    "out_of_sample": {"n": 2882, "mean_r": 0.046, "ci95": [-0.21, 0.34]},
    "vs_placebo": {"in_sample": 0.32, "out_of_sample": 0.23},
    "note": "2021-2025 başı işledi, son dönemde zayıfladı. Short tarafı test edildi, avantaj yok (kullanılmıyor).",
}


@dataclass(frozen=True)
class DonchianParams:
    """Yalnız UYGULAMA ayarları. Kuralın tanımı (20/10/2 ATR/300 bar) sabittir ve burada DEĞİŞTİRİLEMEZ."""

    leverage: int = 1
    #: 0 = kaldıraç yükseltilmez. Tek pozisyon tavanını aşan işlem küçültülerek açılır (bkz. `decide`).
    leverage_max: int = 0
    #: Sinyal kapanışından sonra bu kadar dakika içinde girilmezse sinyal kaçmış sayılır (kesinti sonrası geç giriş yok).
    entry_window_min: int = 60

    def validate(self) -> "DonchianParams":
        if int(self.leverage) < 1:
            raise ValueError("leverage >= 1 olmalı: %r" % (self.leverage,))
        if int(self.leverage_max) != 0 and int(self.leverage_max) < int(self.leverage):
            raise ValueError("leverage_max 0 ya da >= leverage olmalı: %r" % (self.leverage_max,))
        if not (1 <= int(self.entry_window_min) <= 240):
            raise ValueError("entry_window_min 1 ile 240 dakika arasında olmalı: %r" % (self.entry_window_min,))
        return self


DEFAULT_PARAMS = DonchianParams()


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def series(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Laboratuvarın göstergeleri, satırlardan. Okunamayan satır varsa None (fail-closed)."""
    import pandas as pd

    from . import signal_lab as L
    if len(rows or []) < MIN_BARS:
        return None
    try:
        df = pd.DataFrame([{"timestamp": int(r["timestamp"]), "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"]),
                            "volume": float(r.get("volume") or 0.0)} for r in rows])
    except (KeyError, TypeError, ValueError):
        return None
    if df["timestamp"].diff().iloc[1:].le(0).any():
        return None                                   # kronolojik değil / tekrar eden bar
    aux = L.aux_series(df)
    return {"ts": df["timestamp"].to_numpy(), "close": df["close"].to_numpy(dtype=float),
            "atr": L.indicators(df)["atr"], "hi20": aux["hi20"], "lo10": aux["lo10"]}


def _fresh_breakout(s: dict[str, Any], i: int) -> bool:
    """Laboratuvardaki koşul: c[i] > hi20[i] ve c[i-1] <= hi20[i-1] (NaN → yok)."""
    c, hi = s["close"], s["hi20"]
    if i < 1 or hi[i] != hi[i] or hi[i - 1] != hi[i - 1]:
        return False
    return bool(c[i] > hi[i] and c[i - 1] <= hi[i - 1])


def decide(variant: str = VARIANTS[0], *, rows: list[dict[str, Any]], position: dict[str, Any] | None = None,
           now_ms: int | None = None, params: DonchianParams = DEFAULT_PARAMS) -> dict[str, Any] | None:
    """Tek karar: {"action": "OPEN"|"CLOSE", ...} ya da None. Bilinmeyen veri → None (fail-closed).

    `position`: {"opened_ts": ms}. Açık pozisyonun stop'u DEFTERDEN yürür; kuralın kapatma sebepleri kanal çıkışı ve
    zaman sınırıdır. Girişten sonra kapanmış HER bar denetlenir: bir kesintide kaçan çıkış sinyali ilk fırsatta işlenir
    (geç kalınan bar sayısı kayda yazılır)."""
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen strateji varyanti: %r" % (variant,))
    p = params.validate()
    s = series(rows)
    if s is None:
        return None
    n = len(s["ts"])
    i = n - 1
    if position:
        opened = position.get("opened_ts")
        if opened is None:
            return None
        opened = int(opened)
        for k in range(n):
            if int(s["ts"][k]) + STEP_MS <= opened:
                continue                              # giriş öncesi kapanmış bar çıkışı tetiklemez
            ref = s["lo10"][k]
            if ref == ref and s["close"][k] < ref:
                return {"action": "CLOSE", "reason": "DONCHIAN_EXIT_LOW10", "name": variant,
                        "exit_signal_ts": int(s["ts"][k]), "exit_ref_low10": float(ref), "late_bars": int(i - k)}
        entry_bar_open = opened // STEP_MS * STEP_MS
        if int(s["ts"][i]) + STEP_MS - entry_bar_open >= MAX_BARS * STEP_MS:
            return {"action": "CLOSE", "reason": "TIME_STOP_%d_BARS" % MAX_BARS, "name": variant}
        return None

    a = _f(s["atr"][i])
    if a is None or a <= 0 or not _fresh_breakout(s, i):
        return None
    signal_close_ms = int(s["ts"][i]) + STEP_MS
    if now_ms is not None and int(now_ms) - signal_close_ms > int(p.entry_window_min) * 60_000:
        return None                                   # sinyal kaçtı (kesinti): geç giriş laboratuvarda yok
    c = float(s["close"][i])
    stop = c - STOP_ATR * a
    if stop <= 0:
        return None
    return {"action": "OPEN", "direction": "LONG", "stop": float(stop), "targets": [],
            "leverage": int(p.leverage), "leverage_max": int(p.leverage_max),
            "reason": "DONCHIAN20_BREAKOUT", "name": variant, "setup_type": "trend_donchian", "lab_algo": LAB_ALGO,
            "signal_close": c, "signal_ts": int(s["ts"][i]), "signal_close_ms": signal_close_ms,
            "atr14": float(a), "channel_high20": float(s["hi20"][i]),
            # uygulayıcıya: laboratuvarın risk aralığı (girişten stop'a 0,1-10 ATR), aynı sinyalle tek giriş,
            # tavanı aşan işlem reddedilmez küçültülür (kaldıraç 1, risk bütçenin altında kalır)
            "risk_atr_bounds": [MIN_RISK_ATR, MAX_RISK_ATR], "one_entry_per_signal": True,
            "cap_notional_to_position_pct": True}


def rule_state(variant: str = VARIANTS[0], *, rows: list[dict[str, Any]],
               params: DonchianParams = DEFAULT_PARAMS) -> dict[str, Any]:
    """Kuralın karşılaştırdığı değerler — gösterim için, `decide` ile AYNI okuma."""
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen strateji varyanti: %r" % (variant,))
    params.validate()
    out: dict[str, Any] = {"variant": variant, "ok": False, "timeframe": TIMEFRAME, "close": None, "signal_ts": None,
                           "channel_high20": None, "channel_low10": None, "atr14": None, "fresh_breakout": None,
                           "stop_if_open": None, "n_bars": len(rows or []), "min_bars": MIN_BARS,
                           "evidence": EVIDENCE["verdict"], "reason": None}
    s = series(rows or [])
    if s is None:
        out["reason"] = "NOT_ENOUGH_4H_BARS" if len(rows or []) < MIN_BARS else "ROWS_UNREADABLE"
        return out
    i = len(s["ts"]) - 1
    a = _f(s["atr"][i])
    out.update({"close": float(s["close"][i]), "signal_ts": int(s["ts"][i]), "channel_high20": _f(s["hi20"][i]),
                "channel_low10": _f(s["lo10"][i]), "atr14": a, "fresh_breakout": _fresh_breakout(s, i), "ok": True})
    if a is not None and a > 0:
        out["stop_if_open"] = float(s["close"][i]) - STOP_ATR * a
    return out


__all__ = ["DEFAULT_PARAMS", "DonchianParams", "ENTRY_N", "EVIDENCE", "EXIT_N", "LAB_ALGO", "MAX_BARS", "MIN_BARS",
           "STOP_ATR", "TIMEFRAME", "VARIANTS", "decide", "rule_state", "series"]
