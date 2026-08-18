"""Veri kalitesi kapısı — mum/anlık görüntü/saat tutarlılığı. HIGH şiddetli sorun → `DATA_INVALID` (işlem yok).

Kodlar
  klines   : MISSING_BARS, DUPLICATE_BARS, UNSORTED, UNCLOSED_LAST_BAR, ZERO_PRICE, STALE_CANDLE, INSUFFICIENT_BARS
  snapshot : NO_TICKER, STALE_TICKER, WIDE_SPREAD, PRICE_DIVERGENCE, MARK_LAST_DIVERGENCE
  clock    : CLOCK_DRIFT
Verdict: OK | DATA_DEGRADED (yalnız MEDIUM/LOW) | DATA_INVALID (en az bir HIGH). Kapı hiçbir zaman istisna fırlatmaz;
çağıran `report.ok`/`report.verdict`'e bakar (fail-closed karar Coin Head'in işi).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from .providers import tf_ms

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
OK, DEGRADED, INVALID = "OK", "DATA_DEGRADED", "DATA_INVALID"


@dataclass
class DataQualityConfig:
    max_candle_age_bars: float = 2.0        # son kapalı barın kapanışından bu yana en fazla N bar
    max_ticker_age_s: float = 120.0
    max_clock_drift_ms: float = 5000.0
    max_price_divergence_pct: float = 0.5   # ticker vs referans (mum kapanışı) / mark vs last
    max_spread_pct: float = 0.3
    min_bars: int = 210
    max_missing_bars: int = 3               # bundan fazla eksik bar → HIGH; azı MEDIUM
    recent_gap_window_bars: int = 50        # son N bar içinde eksik varsa HIGH
    wide_spread_severity: str = MEDIUM
    mark_divergence_severity: str = MEDIUM

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class QualityIssue:
    code: str
    severity: str
    message: str
    value: float | None = None
    limit: float | None = None

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message, "value": self.value, "limit": self.limit}


@dataclass
class QualityReport:
    ok: bool = True
    verdict: str = OK
    issues: list[QualityIssue] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def has(self, code: str) -> bool:
        return code in self.codes

    def add(self, code: str, severity: str, message: str, value: float | None = None, limit: float | None = None) -> None:
        self.issues.append(QualityIssue(code, severity, message, value, limit))
        self._finalize()

    def _finalize(self) -> None:
        sev = {i.severity for i in self.issues}
        if HIGH in sev:
            self.verdict, self.ok = INVALID, False
        elif sev:
            self.verdict, self.ok = DEGRADED, True
        else:
            self.verdict, self.ok = OK, True

    def merge(self, *others: "QualityReport") -> "QualityReport":
        for o in others:
            self.issues.extend(o.issues)
            self.details.update(o.details)
        self._finalize()
        return self

    def to_dict(self) -> dict:
        return {"ok": self.ok, "verdict": self.verdict, "issues": [i.to_dict() for i in self.issues], "details": self.details}


def merge_reports(reports: Iterable[QualityReport]) -> QualityReport:
    out = QualityReport()
    return out.merge(*reports)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


class DataQualityGate:
    def __init__(self, cfg: DataQualityConfig | None = None):
        self.cfg = cfg or DataQualityConfig()

    # ------------------------------------------------------------ mumlar
    def check_klines(self, df: pd.DataFrame, tf: str, now_ms: int) -> QualityReport:
        cfg = self.cfg
        rep = QualityReport()
        step = tf_ms(tf)
        n = 0 if df is None else len(df)
        rep.details.update({"tf": tf, "bars": n})
        if n < cfg.min_bars:
            rep.add("INSUFFICIENT_BARS", HIGH, f"{n} bar < {cfg.min_bars}", n, cfg.min_bars)
        if n == 0:
            return rep
        ts = pd.to_numeric(df["timestamp"], errors="coerce")
        if ts.isna().any():
            rep.add("ZERO_PRICE", HIGH, "timestamp NaN")
            ts = ts.dropna()
        ts = ts.astype("int64")
        if not ts.is_monotonic_increasing:
            rep.add("UNSORTED", HIGH, "timestamp sıralı değil")
        dup = int(ts.duplicated().sum())
        if dup:
            rep.add("DUPLICATE_BARS", HIGH, f"{dup} tekrar eden bar", dup, 0)
        uniq = ts.drop_duplicates().sort_values()
        if len(uniq) >= 2:
            first, last = int(uniq.iloc[0]), int(uniq.iloc[-1])
            expected = (last - first) // step + 1
            missing = int(expected - len(uniq))
            if missing > 0:
                present = set(uniq.tolist())
                gaps = [t for t in range(first, last + 1, step) if t not in present]
                recent_cut = last - cfg.recent_gap_window_bars * step
                recent = any(g >= recent_cut for g in gaps)
                sev = HIGH if (missing > cfg.max_missing_bars or recent) else MEDIUM
                rep.add("MISSING_BARS", sev, f"{missing} eksik bar (ilk: {gaps[:5]})", missing, cfg.max_missing_bars)
                rep.details["missing_ts"] = gaps[:20]
        # fiyat geçerliliği
        for c in ("open", "high", "low", "close"):
            if c in df.columns:
                col = pd.to_numeric(df[c], errors="coerce")
                bad = int((col.isna() | (col <= 0)).sum())
                if bad:
                    rep.add("ZERO_PRICE", HIGH, f"{c}: {bad} sıfır/NaN fiyat", bad, 0)
                    break
        # son bar kapalı mı, bayat mı
        last_ts = int(ts.iloc[-1])
        if "close_time" in df.columns and pd.notna(df["close_time"].iloc[-1]) and int(df["close_time"].iloc[-1]) > 0:
            last_close = int(df["close_time"].iloc[-1]) + 1
        else:
            last_close = last_ts + step
        rep.details.update({"last_ts": last_ts, "last_close": last_close, "now_ms": int(now_ms)})
        if last_close > now_ms:
            rep.add("UNCLOSED_LAST_BAR", HIGH, "son bar henüz kapanmadı", last_close, now_ms)
        else:
            age_bars = (now_ms - last_close) / step
            rep.details["candle_age_bars"] = round(age_bars, 3)
            if age_bars > cfg.max_candle_age_bars:
                rep.add("STALE_CANDLE", HIGH, f"son kapalı bar {age_bars:.1f} bar eski", age_bars, cfg.max_candle_age_bars)
        return rep

    # ------------------------------------------------------------ anlık görüntü
    def check_snapshot(self, snapshot: Any, ref_price: float | None = None) -> QualityReport:
        cfg = self.cfg
        rep = QualityReport()
        ticker = _get(snapshot, "ticker") or {}
        last = _num(_get(snapshot, "last")) or _num(_get(ticker, "lastPrice")) or _num(_get(ticker, "last"))
        rep.details["last"] = last
        if last is None:
            rep.add("NO_TICKER", HIGH, "ticker/last fiyat yok")
        fresh = _num(_get(snapshot, "freshness_seconds"))
        if fresh is not None:
            rep.details["freshness_seconds"] = fresh
            if fresh > cfg.max_ticker_age_s:
                rep.add("STALE_TICKER", HIGH, f"ticker {fresh:.0f}s eski", fresh, cfg.max_ticker_age_s)
        spread = _num(_get(snapshot, "spread_pct"))
        if spread is not None:
            rep.details["spread_pct"] = spread
            if spread > cfg.max_spread_pct:
                rep.add("WIDE_SPREAD", cfg.wide_spread_severity, f"spread %{spread:.3f}", spread, cfg.max_spread_pct)
        if last is not None and ref_price:
            div = abs(last - float(ref_price)) / float(ref_price) * 100.0
            rep.details["price_divergence_pct"] = round(div, 4)
            if div > cfg.max_price_divergence_pct:
                rep.add("PRICE_DIVERGENCE", HIGH, f"ticker/referans sapması %{div:.3f}", div, cfg.max_price_divergence_pct)
        mark = _num(_get(snapshot, "mark"))
        if last is not None and mark:
            mdiv = abs(mark - last) / last * 100.0
            rep.details["mark_last_divergence_pct"] = round(mdiv, 4)
            if mdiv > cfg.max_price_divergence_pct:
                rep.add("MARK_LAST_DIVERGENCE", cfg.mark_divergence_severity, f"mark/last sapması %{mdiv:.3f}", mdiv, cfg.max_price_divergence_pct)
        return rep

    # ------------------------------------------------------------ saat
    def check_clock(self, drift_ms: float | None) -> QualityReport:
        rep = QualityReport()
        d = _num(drift_ms)
        rep.details["clock_drift_ms"] = d
        if d is None:
            rep.add("CLOCK_DRIFT", MEDIUM, "saat kayması ölçülemedi")
        elif abs(d) > self.cfg.max_clock_drift_ms:
            rep.add("CLOCK_DRIFT", HIGH, f"saat kayması {d:.0f}ms", d, self.cfg.max_clock_drift_ms)
        return rep

    def check_all(self, df: pd.DataFrame, tf: str, now_ms: int, snapshot: Any = None, drift_ms: float | None = None) -> QualityReport:
        """Mum + (varsa) anlık görüntü (+ referans fiyat = son kapanış) + saat; tek rapor."""
        rep = self.check_klines(df, tf, now_ms)
        if snapshot is not None:
            ref = float(df["close"].iloc[-1]) if df is not None and len(df) else None
            rep.merge(self.check_snapshot(snapshot, ref))
        if drift_ms is not None:
            rep.merge(self.check_clock(drift_ms))
        return rep


__all__ = ["DataQualityConfig", "DataQualityGate", "QualityIssue", "QualityReport", "merge_reports", "HIGH", "MEDIUM", "LOW",
           "OK", "DEGRADED", "INVALID"]
