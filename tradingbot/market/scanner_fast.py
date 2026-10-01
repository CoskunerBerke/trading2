"""Hızlı tarayıcı (Tier-1) — ağ yok, yalnızca hesap. Ucuz özellikler (4h + 1d mumları + anlık görüntü) → 0-100 long/short skor.

Huni:  Tier-1 (bütün evren, hızlı skor)  →  Tier-2 (ilk 30: derin özellikler/kalite)  →  Tier-3 (ilk 10: LLM/Coin Head)
`fast_features()` mevcut `scanner.MarketScanner._features` mantığını ağdan ayrıştırır ve `indicators_ext` ile zenginleştirir.
Bütün fonksiyonlar deterministik ve nedenseldir (girdi DataFrame'leri kapalı barlardan oluşmalı — `MarketFeed` bunu garanti eder).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .. import indicators as ind
from .. import indicators_ext as ext

MIN_BARS_4H = 60
MIN_BARS_1D = 30


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _fl(x: Any, default: float | None = None) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return default if (v != v or math.isinf(v)) else v


def _last(s: pd.Series, default: float = float("nan")) -> float:
    if s is None or len(s) == 0:
        return default
    v = s.iloc[-1]
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- özellikler
def fast_features(df_4h: pd.DataFrame, df_1d: pd.DataFrame, snapshot: Any = None) -> dict:
    """Tier-1 ucuz özellik sözlüğü. Yetersiz veri → {'error': 'insufficient_data', ...}."""
    n4 = 0 if df_4h is None else len(df_4h)
    n1 = 0 if df_1d is None else len(df_1d)
    feats: dict[str, Any] = {"bars_4h": n4, "bars_1d": n1}
    if n4 < MIN_BARS_4H or n1 < MIN_BARS_1D:
        feats["error"] = "insufficient_data"
        return feats
    h4 = df_4h.reset_index(drop=True)
    d1 = df_1d.reset_index(drop=True)
    c4, c1 = h4["close"].astype(float), d1["close"].astype(float)
    hi4, lo4 = h4["high"].astype(float), h4["low"].astype(float)
    v4 = h4["volume"].astype(float).to_numpy()
    last = float(c4.iloc[-1])

    e20, e50, e200 = ind.ema(c4, 20), ind.ema(c4, 50), ind.ema(c4, 200)
    d_e20, d_e50 = ind.ema(c1, 20), ind.ema(c1, 50)
    atr4 = ind.atr(hi4, lo4, c4, 14)
    atr_pct = _last(atr4) / last * 100.0 if last else float("nan")
    macd_line, macd_sig, macd_hist = ext.macd(c4)
    lower, mid, upper = ind.bollinger(c4, 20, 2.0)
    bbw = ((upper - lower) / mid.replace(0.0, np.nan))
    bbw_rank = ext.bb_width_pct_rank(c4, 20, 2.0, 120)
    k_lo, k_mid, k_hi = ext.keltner(hi4, lo4, c4, 20, 1.5, 10)
    sw = ext.swing_points(hi4, lo4, 3)

    e200_last = _last(e200)
    feats.update({
        "price": last,
        "ema20_4h": _last(e20), "ema50_4h": _last(e50), "ema200_4h": e200_last,
        "above_ema200": bool(last > e200_last) if e200_last == e200_last else None,
        "dist_ema200_pct": ((last / e200_last - 1.0) * 100.0) if e200_last and e200_last == e200_last else None,
        "ema_stack_4h": 1 if _last(e20) > _last(e50) else -1,
        "ema_slope20_pct": _last(ext.ema_slope_pct(c4, 20, 3)),
        "d1_close_gt_ema20": bool(float(c1.iloc[-1]) > _last(d_e20)),
        "d1_ema20_gt_ema50": bool(_last(d_e20) > _last(d_e50)),
        "rsi_4h": _last(ind.rsi(c4, 14)), "rsi_1d": _last(ind.rsi(c1, 14)),
        "adx_4h": _last(ind.adx(hi4, lo4, c4, 14)),
        "atr_pct_4h": atr_pct,
        "atr_pct_rank": _last(ext.atr_pct_rank(hi4, lo4, c4, 14, 120)),
        "roc12_4h": (last / float(c4.iloc[-13]) - 1.0) * 100.0 if float(c4.iloc[-13]) else 0.0,
        "roc5_1d": (float(c1.iloc[-1]) / float(c1.iloc[-6]) - 1.0) * 100.0 if float(c1.iloc[-6]) else 0.0,
        "macd_hist_4h": _last(macd_hist), "macd_hist_prev_4h": float(macd_hist.iloc[-2]) if len(macd_hist) > 1 else float("nan"),
        "macd_above_signal": bool(_last(macd_line) > _last(macd_sig)),
        "vol_ratio_1": float(v4[-1] / (v4[-21:-1].mean() + 1e-9)),
        "vol_ratio_6": float(v4[-6:].mean() / (v4[-30:-6].mean() + 1e-9)),
        "bb_width_pct": _last(bbw) * 100.0, "bb_width_rank": _last(bbw_rank),
        "squeeze_release": bool(len(bbw) > 2 and float(bbw.iloc[-2]) <= float(bbw.iloc[-120:].quantile(0.15))
                                and float(bbw.iloc[-1]) > float(bbw.iloc[-2]) * 1.15),
        "hi20_prev": float(hi4.iloc[-21:-1].max()), "lo20_prev": float(lo4.iloc[-21:-1].min()),
        "above_keltner_upper": bool(last > _last(k_hi)), "below_keltner_lower": bool(last < _last(k_lo)),
        "last_swing_high": _fl(_last(sw["last_swing_high"])), "last_swing_low": _fl(_last(sw["last_swing_low"])),
        "donchian_mid_20": _fl(_last(ext.donchian_mid(hi4, lo4, 20))),
        "realized_vol_20": _fl(_last(ext.realized_vol(c4, 20))),
    })
    feats["breakout_up"] = bool(last > feats["hi20_prev"])
    feats["breakout_down"] = bool(last < feats["lo20_prev"])
    up_mask = np.diff(c4.to_numpy(float)[-21:]) > 0
    up_v, dn_v = float(v4[-20:][up_mask].sum()), float(v4[-20:][~up_mask].sum())
    feats["up_dn_vol_ratio"] = up_v / (dn_v + 1e-9)
    feats["vol_direction"] = 1 if up_v > dn_v * 1.15 else (-1 if dn_v > up_v * 1.15 else 0)

    # anlık görüntü (opsiyonel)
    ticker = _get(snapshot, "ticker") or {}
    feats.update({
        "chg24_pct": _fl(_get(ticker, "priceChangePercent"), 0.0),
        "quote_volume_24h": _fl(_get(ticker, "quoteVolume"), 0.0),
        "last_live": _fl(_get(snapshot, "last")) or _fl(_get(ticker, "lastPrice")),
        "spread_pct": _fl(_get(snapshot, "spread_pct")),
        "depth_0_5pct": _fl(_get(snapshot, "depth_0_5pct")),
        "imbalance": _fl(_get(snapshot, "imbalance")),
        "funding_pct": (lambda fr: None if fr is None else fr * 100.0)(_fl(_get(snapshot, "funding_rate"))),
        "oi": _fl(_get(snapshot, "oi")), "lsr": _fl(_get(snapshot, "lsr")), "taker_ratio": _fl(_get(snapshot, "taker_ratio")),
        "mark": _fl(_get(snapshot, "mark")),
    })
    return feats


# ---------------------------------------------------------------- skor
def tier1_score(f: dict) -> tuple[int, int, list[str]]:
    """(score_long, score_short, tags) 0-100. Trend/momentum/hacim/tetikleyici 25'er; risk çarpanı."""
    if not f or f.get("error"):
        return 0, 0, [f.get("error", "no_features")] if f else ["no_features"]
    tags: list[str] = []
    adx = _fl(f.get("adx_4h"), 0.0) or 0.0
    rsi4 = _fl(f.get("rsi_4h"), 50.0) or 50.0
    rsi1 = _fl(f.get("rsi_1d"), 50.0) or 50.0
    atr_pct = _fl(f.get("atr_pct_4h"), 3.0) or 3.0
    # trend (-1..1)
    tr = 0.0
    tr += 0.3 if f.get("above_ema200") else -0.3
    tr += 0.25 if f.get("ema_stack_4h", 0) > 0 else -0.25
    tr += 0.25 if f.get("d1_close_gt_ema20") else -0.25
    tr += 0.2 if f.get("d1_ema20_gt_ema50") else -0.2
    tr *= min(1.0, max(0.3, adx / 30.0))
    # momentum (-1..1)
    roc12 = _fl(f.get("roc12_4h"), 0.0) or 0.0
    mo = 0.4 * _clip((rsi4 - 50) / 20) + 0.3 * _clip(roc12 / (atr_pct * 3 + 1e-9)) + 0.3 * _clip((rsi1 - 50) / 20)
    mh, mhp = _fl(f.get("macd_hist_4h")), _fl(f.get("macd_hist_prev_4h"))
    if mh is not None and mhp is not None:
        if mh > 0 and mh > mhp:
            mo += 0.1
        elif mh < 0 and mh < mhp:
            mo -= 0.1
    mo = _clip(mo)
    # hacim (0..1) + yön
    vr = _fl(f.get("vol_ratio_1"), 1.0) or 1.0
    vr6 = _fl(f.get("vol_ratio_6"), 1.0) or 1.0
    vol_dir = int(f.get("vol_direction", 0) or 0)
    vol_score = min(1.0, 0.5 * min(vr, 3) / 3 * 2 + 0.5 * min(vr6, 2) / 2)
    # tetikleyici
    cat_long = cat_short = 0.0
    if f.get("breakout_up"):
        cat_long += 0.6; tags.append("20-bar kırılım ↑")
    if f.get("breakout_down"):
        cat_short += 0.6; tags.append("20-bar kırılım ↓")
    if f.get("squeeze_release"):
        tags.append("sıkışma→patlama")
        if f.get("ema_stack_4h", 0) > 0 or (f.get("macd_above_signal") and f.get("above_ema200")):
            cat_long += 0.4
        else:
            cat_short += 0.4
    if vr > 2.5:
        tags.append("hacim şoku")
        if roc12 >= 0:
            cat_long += 0.3
        else:
            cat_short += 0.3
    fp = _fl(f.get("funding_pct"))
    if fp is not None:
        if fp > 0.05:
            tags.append("funding aşırı +"); cat_short += 0.2
        elif fp < -0.03:
            tags.append("funding aşırı −"); cat_long += 0.2
    if f.get("above_keltner_upper"):
        tags.append("keltner üstü")
    if f.get("below_keltner_lower"):
        tags.append("keltner altı")
    chg = _fl(f.get("chg24_pct"), 0.0) or 0.0
    if abs(chg) > 12:
        tags.append(f"24s %{chg:+.0f}")
    # risk (0..1)
    risk = 1.0
    if atr_pct > 6:
        risk -= 0.4
    elif atr_pct > 4:
        risk -= 0.2
    if rsi4 > 78 or rsi4 < 22:
        risk -= 0.3
    if abs(chg) > 20:
        risk -= 0.3
    sp = _fl(f.get("spread_pct"))
    if sp is not None and sp > 0.2:
        risk -= 0.2; tags.append("geniş spread")
    risk = max(0.0, risk)

    def total(direction: int, cat: float) -> int:
        t_ = 25 * max(0.0, tr * direction)
        m_ = 25 * max(0.0, mo * direction)
        v_ = 25 * vol_score * (1.0 if vol_dir == direction or vol_dir == 0 else 0.4)
        c_ = 25 * min(1.0, cat)
        return int(round((t_ + m_ + v_ + c_) * (0.6 + 0.4 * risk)))

    return total(1, cat_long), total(-1, cat_short), tags


# ---------------------------------------------------------------- huni
@dataclass
class CandidateRow:
    symbol: str
    market_type: str = "futures"
    score_long: int = 0
    score_short: int = 0
    tags: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    error: str | None = None
    quality_verdict: str | None = None

    @property
    def direction(self) -> str:
        return "LONG" if self.score_long >= self.score_short else "SHORT"

    @property
    def score(self) -> int:
        return max(self.score_long, self.score_short)

    @classmethod
    def from_features(cls, symbol: str, features: dict, market_type: str = "futures") -> "CandidateRow":
        sl, ss, tags = tier1_score(features)
        return cls(symbol=symbol, market_type=market_type, score_long=sl, score_short=ss, tags=tags, features=features,
                   error=features.get("error"))

    def to_dict(self, with_features: bool = True) -> dict:
        d = {"symbol": self.symbol, "market_type": self.market_type, "score_long": self.score_long, "score_short": self.score_short,
             "direction": self.direction, "score": self.score, "tags": list(self.tags), "error": self.error,
             "quality_verdict": self.quality_verdict}
        if with_features:
            d["features"] = {k: v for k, v in self.features.items()}
        return d


@dataclass
class FunnelResult:
    tier1: list[CandidateRow]
    tier2: list[CandidateRow]
    tier3: list[CandidateRow]
    dropped: dict[str, str] = field(default_factory=dict)   # symbol → sebep

    @property
    def counts(self) -> dict[str, int]:
        return {"tier1": len(self.tier1), "tier2": len(self.tier2), "tier3": len(self.tier3), "dropped": len(self.dropped)}

    def to_dict(self) -> dict:
        return {"counts": self.counts, "tier1": [r.to_dict(False) for r in self.tier1], "tier2": [r.to_dict(False) for r in self.tier2],
                "tier3": [r.to_dict() for r in self.tier3], "dropped": dict(self.dropped)}


class CandidateFunnel:
    def __init__(self, tier2_top: int = 30, tier3_top: int = 10, min_score_tier2: int = 0, min_score_tier3: int = 0):
        self.tier2_top = int(tier2_top)
        self.tier3_top = int(tier3_top)
        self.min_score_tier2 = int(min_score_tier2)
        self.min_score_tier3 = int(min_score_tier3)

    @staticmethod
    def _to_row(r: Any) -> CandidateRow:
        if isinstance(r, CandidateRow):
            return r
        if isinstance(r, dict):
            if "score_long" in r:
                return CandidateRow(symbol=r["symbol"], market_type=r.get("market_type", "futures"), score_long=int(r.get("score_long", 0)),
                                    score_short=int(r.get("score_short", 0)), tags=list(r.get("tags", [])), features=dict(r.get("features", {})),
                                    error=r.get("error"))
            return CandidateRow.from_features(r["symbol"], r.get("features", {}), r.get("market_type", "futures"))
        raise TypeError(f"CandidateFunnel: desteklenmeyen satır tipi {type(r).__name__}")

    def select(self, rows: Iterable[Any], quality_by_symbol: dict[str, Any] | None = None) -> FunnelResult:
        """Tier-1: hatasız + kalite raporu ok (rapor yoksa geçer); skor azalan, eşitlikte sembol adı (deterministik)."""
        quality_by_symbol = quality_by_symbol or {}
        tier1: list[CandidateRow] = []
        dropped: dict[str, str] = {}
        for raw in rows:
            r = self._to_row(raw)
            if r.error:
                dropped[r.symbol] = f"error:{r.error}"
                continue
            q = quality_by_symbol.get(r.symbol)
            if q is not None:
                ok = _get(q, "ok", True)
                r.quality_verdict = _get(q, "verdict")
                if not ok:
                    codes = _get(q, "codes", None) or [i.get("code") if isinstance(i, dict) else _get(i, "code") for i in (_get(q, "issues") or [])]
                    dropped[r.symbol] = f"quality:{r.quality_verdict}:{','.join(str(c) for c in codes)}"
                    continue
            tier1.append(r)
        tier1.sort(key=lambda r: (-r.score, -max(r.score_long, r.score_short), r.symbol))
        tier2 = [r for r in tier1 if r.score >= self.min_score_tier2][: self.tier2_top]
        tier3 = [r for r in tier2 if r.score >= self.min_score_tier3][: self.tier3_top]
        return FunnelResult(tier1=tier1, tier2=tier2, tier3=tier3, dropped=dropped)


__all__ = ["fast_features", "tier1_score", "CandidateRow", "CandidateFunnel", "FunnelResult", "MIN_BARS_4H", "MIN_BARS_1D"]
