"""Parametrik strateji aileleri. Hepsi long-only (spot) — giriş/çıkış bool serileri üretir.

Kural: sinyaller yalnızca kapanmış bar verisini kullanır; backtester girişleri
bir SONRAKİ barın açılışında gerçekleştirir (look-ahead yok).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterator

import pandas as pd

from . import indicators as ind


@dataclass(frozen=True)
class StrategySpec:
    family: str
    params: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.family}({p})"

    def to_dict(self) -> dict:
        return {"family": self.family, "params": dict(self.params)}

    @staticmethod
    def from_dict(d: dict) -> "StrategySpec":
        return StrategySpec(d["family"], dict(d.get("params", {})))


class Signals(pd.DataFrame):
    """entries / exits bool sütunlu DataFrame (tip ipucu amaçlı)."""


def _empty(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"entries": False, "exits": False}, index=df.index)


# ---------------------------------------------------------------------------
# 1) RSI ortalamaya dönüş (mean reversion) + opsiyonel EMA trend filtresi
# ---------------------------------------------------------------------------
def rsi_mean_reversion(df: pd.DataFrame, length: int, lo: int, hi: int, trend_ema: int) -> pd.DataFrame:
    r = ind.rsi(df["close"], length)
    out = _empty(df)
    entries = (r < lo) & (r.shift(1) >= lo)          # aşağıdan eşiği kırış anı
    if trend_ema:
        e = ind.ema(df["close"], trend_ema)
        entries &= df["close"] > e
    out["entries"] = entries.fillna(False)
    out["exits"] = (r > hi).fillna(False)
    return out


# ---------------------------------------------------------------------------
# 2) EMA trend takip (kesişim) + opsiyonel ADX filtresi
# ---------------------------------------------------------------------------
def ema_trend(df: pd.DataFrame, fast: int, slow: int, adx_min: int) -> pd.DataFrame:
    f = ind.ema(df["close"], fast)
    s = ind.ema(df["close"], slow)
    out = _empty(df)
    cross_up = (f > s) & (f.shift(1) <= s.shift(1))
    cross_dn = (f < s) & (f.shift(1) >= s.shift(1))
    if adx_min:
        a = ind.adx(df["high"], df["low"], df["close"], 14)
        cross_up &= a > adx_min
    out["entries"] = cross_up.fillna(False)
    out["exits"] = cross_dn.fillna(False)
    return out


# ---------------------------------------------------------------------------
# 3) Donchian kırılım (breakout)
# ---------------------------------------------------------------------------
def donchian_breakout(df: pd.DataFrame, entry_len: int, exit_len: int) -> pd.DataFrame:
    _, upper = ind.donchian(df["high"], df["low"], entry_len)
    lower, _ = ind.donchian(df["high"], df["low"], exit_len)
    out = _empty(df)
    out["entries"] = (df["close"] > upper).fillna(False)
    out["exits"] = (df["close"] < lower).fillna(False)
    return out


# ---------------------------------------------------------------------------
# 4) Bollinger ortalamaya dönüş
# ---------------------------------------------------------------------------
def bollinger_reversion(df: pd.DataFrame, length: int, mult: float, trend_ema: int) -> pd.DataFrame:
    lower, mid, _ = ind.bollinger(df["close"], length, mult)
    out = _empty(df)
    entries = (df["close"] < lower) & (df["close"].shift(1) >= lower.shift(1))
    if trend_ema:
        entries &= df["close"] > ind.ema(df["close"], trend_ema)
    out["entries"] = entries.fillna(False)
    out["exits"] = (df["close"] > mid).fillna(False)
    return out


FAMILIES = {
    "rsi_mr": rsi_mean_reversion,
    "ema_trend": ema_trend,
    "donchian": donchian_breakout,
    "bb_mr": bollinger_reversion,
}

FAMILY_TITLES = {
    "rsi_mr": "RSI Ortalamaya Dönüş",
    "ema_trend": "EMA Trend Takibi",
    "donchian": "Donchian Kırılım",
    "bb_mr": "Bollinger Ortalamaya Dönüş",
}

# Parametre ızgaraları (tarama = slide 4'teki "sweep the variables")
GRIDS: dict[str, dict[str, list]] = {
    "rsi_mr": {"length": [7, 14], "lo": [25, 30, 35], "hi": [60, 70], "trend_ema": [0, 200]},
    "ema_trend": {"fast": [9, 20], "slow": [50, 100, 200], "adx_min": [0, 20]},
    "donchian": {"entry_len": [20, 40, 55], "exit_len": [10, 20]},
    "bb_mr": {"length": [20], "mult": [2.0, 2.5], "trend_ema": [0, 200]},
}


def iter_specs(families: list[str] | None = None) -> Iterator[StrategySpec]:
    for fam, grid in GRIDS.items():
        if families and fam not in families:
            continue
        keys = list(grid)
        for combo in itertools.product(*(grid[k] for k in keys)):
            params = dict(zip(keys, combo))
            if fam == "ema_trend" and params["fast"] >= params["slow"]:
                continue
            if fam == "rsi_mr" and params["lo"] >= params["hi"]:
                continue
            yield StrategySpec(fam, params)


def generate_signals(df: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    fn = FAMILIES[spec.family]
    return fn(df, **spec.params)
