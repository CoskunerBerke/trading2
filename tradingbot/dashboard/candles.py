"""Mum kaynağı (CSV önbellek + isteğe bağlı parquet) ve grafik yükü (overlay/panel/level/plan)."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..indicators import bollinger, ema, rsi, sma
from ..indicators_ext import macd, vwap_session

TF_ALIASES = {"1h": ("1h", "60"), "4h": ("4h", "240"), "1d": ("1d", "D", "1D"), "15m": ("15m", "15"), "1w": ("1w", "W")}


def _clean(v: Any) -> Any:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else round(f, 8)


def _series(s: pd.Series) -> list[float | None]:
    return [_clean(x) for x in s.to_numpy(dtype=float)]


class CandleSource:
    """`data_dir` altındaki `tv-binance_BTC-USDT_4h.csv` / `binance_BTC-USDT_4h.csv` (+ `candles/**/*.parquet`)."""

    def __init__(self, data_dir: Path | str, quote: str = "USDT") -> None:
        self.data_dir = Path(data_dir)
        self.quote = quote

    def candidates(self, base: str, tf: str, market: str = "spot") -> list[Path]:
        base = base.upper()
        tfs = TF_ALIASES.get(tf, (tf,))
        out: list[Path] = []
        prefixes = ["tv-binance", "binance", "binanceusdm", "tv-binanceusdm", "bybit", "okx", "kucoin"]
        if market == "futures":
            prefixes = ["binanceusdm", "tv-binanceusdm"] + prefixes
        for t in tfs:
            for pre in prefixes:
                out.append(self.data_dir / f"{pre}_{base}-{self.quote}_{t}.csv")
            for sub in (self.data_dir / "candles",):
                if sub.exists():
                    out += sorted(sub.rglob(f"*{base}*{self.quote}*{t}*.parquet"))
                    out += sorted(sub.rglob(f"{market}/*{base}*{t}*.parquet"))
        return out

    def find(self, base: str, tf: str = "4h", market: str = "spot") -> Path | None:
        for p in self.candidates(base, tf, market):
            if p.exists():
                return p
        return None

    def available_bases(self) -> list[str]:
        bases: set[str] = set()
        for p in self.data_dir.glob("*_*-*_*.csv"):
            try:
                pair = p.name.split("_", 1)[1].rsplit("_", 1)[0]
                bases.add(pair.split("-")[0].upper())
            except IndexError:
                continue
        return sorted(bases)

    def load(self, base: str, tf: str = "4h", market: str = "spot", n: int | None = None) -> pd.DataFrame | None:
        p = self.find(base, tf, market)
        if p is None:
            return None
        try:
            if p.suffix == ".parquet":
                df = pd.read_parquet(p)   # pyarrow yoksa ImportError → None
            else:
                df = pd.read_csv(p)
        except (OSError, ValueError, ImportError):
            return None
        cols = {c.lower(): c for c in df.columns}
        need = ["open", "high", "low", "close"]
        if not all(c in cols for c in need):
            return None
        ts_col = cols.get("timestamp") or cols.get("time") or cols.get("ts") or cols.get("open_time")
        out = pd.DataFrame({k: pd.to_numeric(df[cols[k]], errors="coerce") for k in need})
        out["volume"] = pd.to_numeric(df[cols["volume"]], errors="coerce") if "volume" in cols else 0.0
        if ts_col is not None:
            ts = df[ts_col]
            if np.issubdtype(ts.dtype, np.number):
                ts = pd.to_numeric(ts, errors="coerce")
                # saniye/milisaniye ayrımı
                ts = ts.where(ts > 1e11, ts * 1000)
                out["timestamp"] = ts.astype("int64")
            else:
                out["timestamp"] = (pd.to_datetime(ts, utc=True, errors="coerce").astype("int64") // 1_000_000)
        elif isinstance(df.index, pd.DatetimeIndex):
            idx = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
            out["timestamp"] = idx.asi8 // 1_000_000
        else:
            return None
        out = out.dropna(subset=["open", "high", "low", "close", "timestamp"]).sort_values("timestamp").drop_duplicates("timestamp", keep="last")
        out = out.reset_index(drop=True)
        if n:
            out = out.iloc[-int(n):].reset_index(drop=True)
        return out


def build_candle_payload(df: pd.DataFrame, *, n: int = 600, plan: dict | None = None, position: dict | None = None,
                         levels: list[dict] | dict | None = None, funding: list | None = None, oi: list | None = None,
                         base: str = "", tf: str = "", market: str = "spot", warmup: int = 250) -> dict[str, Any]:
    """Overlay'ler ısınma dahil hesaplanır (warmup ekstra bar), sonra son `n` bar döndürülür."""
    if df is None or df.empty:
        return {"base": base, "tf": tf, "market": market, "t": [], "o": [], "h": [], "l": [], "c": [], "v": [], "overlays": {},
                "levels": [], "plan": {}, "position": {}, "panels": {}, "error": "veri yok"}
    close = df["close"].astype(float)
    ov: dict[str, pd.Series] = {}
    for L in (25, 50, 99, 200):
        ov[f"sma{L}"] = sma(close, L)
        ov[f"ema{L}"] = ema(close, L)
    try:
        ov["vwap"] = vwap_session(df)
    except (ValueError, KeyError):
        ov["vwap"] = pd.Series(np.nan, index=df.index)
    lo, mid, up = bollinger(close, 20, 2.0)
    ov["bb_up"], ov["bb_mid"], ov["bb_lo"] = up, mid, lo
    r = rsi(close, 14)
    m_line, m_sig, m_hist = macd(close)
    k = min(len(df), int(n))
    sl = slice(len(df) - k, len(df))
    payload: dict[str, Any] = {
        "base": base, "tf": tf, "market": market,
        "t": [int(x) for x in df["timestamp"].iloc[sl].to_numpy()],
        "o": _series(df["open"].iloc[sl]), "h": _series(df["high"].iloc[sl]), "l": _series(df["low"].iloc[sl]),
        "c": _series(close.iloc[sl]), "v": _series(df["volume"].iloc[sl].astype(float)),
        "overlays": {kk: _series(vv.iloc[sl]) for kk, vv in ov.items()},
        "panels": {"rsi": _series(r.iloc[sl]), "macd_line": _series(m_line.iloc[sl]), "macd_signal": _series(m_sig.iloc[sl]),
                   "macd_hist": _series(m_hist.iloc[sl]), "funding": list(funding or []), "oi": list(oi or [])},
        "levels": [], "plan": {}, "position": {},
    }
    # seviyeler
    lv: list[dict] = []
    if isinstance(levels, dict):
        for name, val in levels.items():
            c = _clean(val)
            if c is not None:
                lv.append({"name": str(name), "price": c, "kind": "resistance" if str(name).lower().startswith("r") else ("support" if str(name).lower().startswith("s") else "level")})
    elif isinstance(levels, list):
        for x in levels:
            if isinstance(x, dict) and _clean(x.get("price")) is not None:
                lv.append({"name": str(x.get("name", "")), "price": _clean(x.get("price")), "kind": str(x.get("kind", "level"))})
    payload["levels"] = lv
    if plan:
        tg = plan.get("targets") or [plan.get("target1"), plan.get("target2")]
        entry = plan.get("entry")
        if entry is None and plan.get("entry_zone"):
            ez = plan["entry_zone"]
            entry = (float(ez[0]) + float(ez[1])) / 2 if len(ez) > 1 and ez[0] and ez[1] else (ez[0] if ez else None)
        payload["plan"] = {"direction": plan.get("direction"), "entry": _clean(entry), "stop": _clean(plan.get("stop")),
                           "tp1": _clean(tg[0]) if len(tg) > 0 else None, "tp2": _clean(tg[1]) if len(tg) > 1 else None,
                           "liq": _clean(plan.get("liq") or plan.get("liquidation_price")), "valid": bool(plan.get("valid", True))}
    if position:
        tg = position.get("targets") or [position.get("target1"), position.get("target2")]
        payload["position"] = {"side": position.get("side"), "entry": _clean(position.get("entry_avg") or position.get("entry")),
                               "stop": _clean(position.get("stop")), "tp1": _clean(tg[0]) if len(tg) > 0 else None,
                               "tp2": _clean(tg[1]) if len(tg) > 1 else None,
                               "liq": _clean(position.get("liquidation_price") or position.get("liq_price")),
                               "qty": _clean(position.get("qty") or position.get("units")), "leverage": position.get("leverage")}
    return payload


__all__ = ["CandleSource", "build_candle_payload", "TF_ALIASES"]
