"""SimilarPatternEngine — deterministik benzer-olay getirme + maliyet-sonrası istatistiksel kanıt (fail-closed).

- Olay: (symbol, market, tf, bar i). Vektör: normalize getiri yolu (W bar, 16 noktaya indirgenmiş, z-skor) + özellik anlık görüntüsü
  (MA mesafe/eğim, momentum, volatilite, hacim, mum anatomisi, rejim, funding) — indeks genelinde standardize.
- Sonuç: triple-barrier (ATR stop, TP 1R/2R, ufuk) LONG ve SHORT için ayrı; net R (fee/slippage/funding sonrası).
- Sorgu: yalnız cutoff_ts < query_ts - embargo olan olaylar (geçmiş; ileri bakış yok); seviyeler: aynı coin / küme / bütün evren aynı rejim.
- Tekrar sayımı önleme: aynı sembolde min_separation bar zorunlu (overlap purge), aynı (symbol, event_ts) tekilleştirme.
- İstatistik: n, win/loss/BE, posterior P(win) (Beta), Wilson CI, mean/median net R, expectancy CI, payoff, PF, maxDD, MAE/MFE, çıkış dağılımı,
  30/90/180/360 gün pencereleri, edge decay, brüt/net; kodlar: INSUFFICIENT_SAMPLE, LOW_CONFIDENCE, NEGATIVE_EXPECTANCY, EDGE_DECAY,
  REGIME_MISMATCH, COST_ERODED_EDGE, DATA_INVALID.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .features import build_feature_frame
from .outcomes import Outcome, barriers_from_atr, triple_barrier

WINDOWS = (16, 32, 64, 128)
PATH_POINTS = 16
SNAP_COLS = ["sma25_dist", "sma99_dist", "ema21_slope", "ema99_slope", "rsi14", "macd_hist", "adx14", "atr_pct", "vol_pctile", "rv_ratio",
             "vol_z", "rel_vol", "body_range", "close_loc", "consec_dir", "funding", "btc_regime", "dd50"]
DAY_MS = 86_400_000


def regime_code(row: pd.Series) -> str:
    """Basit, deterministik rejim etiketi: trend yönü (sma25 vs sma99) × volatilite (vol_pctile)."""
    tr = row.get("sma25_99_cross", 0.0)
    vp = row.get("vol_pctile", np.nan)
    trend = "UP" if tr > 0 else "DOWN" if tr < 0 else "FLAT"
    vol = "HIGHVOL" if (not np.isnan(vp) and vp >= 70) else "LOWVOL" if (not np.isnan(vp) and vp <= 30) else "MIDVOL"
    return f"{trend}_{vol}"


def _path_vec(close: np.ndarray, i: int, w: int) -> np.ndarray | None:
    if i - w < 0:
        return None
    seg = np.log(close[i - w + 1:i + 1] / close[i - w:i])
    if len(seg) < w or not np.isfinite(seg).all():
        return None
    cum = np.cumsum(seg)
    idx = np.linspace(0, len(cum) - 1, PATH_POINTS).round().astype(int)
    p = cum[idx]
    sd = p.std()
    return (p - p.mean()) / (sd if sd > 1e-12 else 1.0)


@dataclass
class PatternEvent:
    symbol: str
    market: str
    tf: str
    idx: int
    event_ts: int
    cutoff_ts: int
    regime: str
    cluster: str
    path: dict[int, np.ndarray]
    snap: np.ndarray
    outcomes: dict[str, Outcome]          # "LONG"/"SHORT" (spot'ta yalnız LONG)


@dataclass
class EvidenceStats:
    n: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    p_win_posterior: float = 0.5
    p_win_ci: tuple[float, float] = (0.0, 1.0)
    mean_net_r: float = 0.0
    median_net_r: float = 0.0
    expectancy_ci: tuple[float, float] = (0.0, 0.0)
    mean_gross_r: float = 0.0
    payoff_ratio: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_r: float = 0.0
    mae_pct_mean: float = 0.0
    mfe_pct_mean: float = 0.0
    exit_reasons: dict[str, int] = field(default_factory=dict)
    windows: dict[str, dict] = field(default_factory=dict)      # "30d"/"90d"/"180d"/"360d"/"all" → {n, mean_net_r}
    edge_decay: float = 0.0                                     # recent(90d) − all (net R)
    cost_drag_r: float = 0.0                                    # gross − net
    breakdown: dict[str, dict] = field(default_factory=dict)    # by symbol / regime / market
    codes: list[str] = field(default_factory=list)              # fail-closed nedenleri
    ok: bool = False

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["p_win_ci"] = list(self.p_win_ci); d["expectancy_ci"] = list(self.expectancy_ci)
        return d


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    cen = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, cen - half), min(1.0, cen + half)


def compute_stats(outs: list[tuple[int, Outcome, str, str, str]], *, now_ts: int, min_sample: int = 30, min_expectancy: float = 0.0,
                  query_regime: str | None = None, be_r: float = 0.1) -> EvidenceStats:
    """outs: [(cutoff_ts, Outcome, symbol, regime, market)] — istatistik + fail-closed kodlar."""
    st = EvidenceStats()
    if not outs:
        st.codes = ["INSUFFICIENT_SAMPLE"]
        return st
    outs = sorted(outs, key=lambda x: x[0])
    r = np.array([o.net_r for _, o, *_ in outs], dtype=float)
    g = np.array([o.gross_r for _, o, *_ in outs], dtype=float)
    if not np.isfinite(r).all():
        st.codes = ["DATA_INVALID"]
        return st
    st.n = len(r)
    st.wins = int((r > be_r).sum()); st.losses = int((r < -be_r).sum()); st.breakeven = st.n - st.wins - st.losses
    st.p_win_posterior = (st.wins + 1) / (st.n + 2)
    st.p_win_ci = wilson(st.wins, st.n)
    st.mean_net_r = float(r.mean()); st.median_net_r = float(np.median(r)); st.mean_gross_r = float(g.mean())
    se = float(r.std(ddof=1) / math.sqrt(st.n)) if st.n > 1 else float("inf")
    st.expectancy_ci = (st.mean_net_r - 1.96 * se, st.mean_net_r + 1.96 * se) if st.n > 1 else (-9.0, 9.0)
    pos, neg = r[r > 0], r[r < 0]
    st.payoff_ratio = float(pos.mean() / abs(neg.mean())) if len(pos) and len(neg) else (float("inf") if len(pos) and not len(neg) else 0.0)
    st.profit_factor = float(pos.sum() / abs(neg.sum())) if len(neg) else (float("inf") if len(pos) else 0.0)
    eq = np.cumsum(r); st.max_drawdown_r = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    st.mae_pct_mean = float(np.mean([o.mae_pct for _, o, *_ in outs])); st.mfe_pct_mean = float(np.mean([o.mfe_pct for _, o, *_ in outs]))
    for _, o, *_ in outs:
        st.exit_reasons[o.exit_reason] = st.exit_reasons.get(o.exit_reason, 0) + 1
    for days in (30, 90, 180, 360):
        sel = [x for x in outs if now_ts - x[0] <= days * DAY_MS]
        st.windows[f"{days}d"] = {"n": len(sel), "mean_net_r": float(np.mean([o.net_r for _, o, *_ in sel])) if sel else None}
    st.windows["all"] = {"n": st.n, "mean_net_r": st.mean_net_r}
    rec = st.windows["90d"]["mean_net_r"]
    st.edge_decay = float(rec - st.mean_net_r) if rec is not None else 0.0
    st.cost_drag_r = st.mean_gross_r - st.mean_net_r
    for key_i, name in ((2, "symbol"), (3, "regime"), (4, "market")):
        bd: dict[str, list[float]] = {}
        for x in outs:
            bd.setdefault(str(x[key_i]), []).append(x[1].net_r)
        st.breakdown[name] = {k: {"n": len(v), "mean_net_r": float(np.mean(v))} for k, v in sorted(bd.items())}
    codes = []
    if st.n < min_sample:
        codes.append("INSUFFICIENT_SAMPLE")
    if st.mean_net_r <= min_expectancy:
        codes.append("NEGATIVE_EXPECTANCY")
    if st.n >= 2 and st.expectancy_ci[0] <= 0:
        codes.append("LOW_CONFIDENCE")
    if st.mean_gross_r > 0 >= st.mean_net_r:
        codes.append("COST_ERODED_EDGE")
    if rec is not None and st.windows["90d"]["n"] >= 10 and st.mean_net_r > 0 and rec < 0.5 * st.mean_net_r:
        codes.append("EDGE_DECAY")
    if query_regime is not None:
        reg = st.breakdown.get("regime", {})
        share = reg.get(query_regime, {}).get("n", 0) / st.n if st.n else 0
        if share < 0.5:
            codes.append("REGIME_MISMATCH")
    st.codes = codes
    st.ok = not codes
    return st


class SimilarPatternEngine:
    def __init__(self, *, windows=WINDOWS, horizon: int = 24, stop_atr_mult: float = 2.5, tp1_r: float = 1.0, tp2_r: float = 2.0,
                 fee_pct: float = 0.05, slippage_pct: float = 0.03, min_separation: int | None = None, embargo_bars: int = 1,
                 min_sample: int = 30, clusters: dict[str, str] | None = None):
        self.windows = tuple(windows)
        self.horizon, self.stop_atr_mult, self.tp1_r, self.tp2_r = horizon, stop_atr_mult, tp1_r, tp2_r
        self.fee_pct, self.slippage_pct = fee_pct, slippage_pct
        self.min_separation = min_separation
        self.embargo_bars = embargo_bars
        self.min_sample = min_sample
        self.clusters = clusters or {}
        self.events: list[PatternEvent] = []
        self._mu: np.ndarray | None = None
        self._sd: np.ndarray | None = None
        self.frames: dict[tuple[str, str, str], pd.DataFrame] = {}      # (symbol, market, tf) → feature frame
        self.candles: dict[tuple[str, str, str], pd.DataFrame] = {}

    # ------------------------------------------------------------ indeks
    def add_series(self, symbol: str, market: str, tf: str, df: pd.DataFrame, *, btc_df=None, funding_df=None, stride: int = 1,
                   funding_pct_per_bar: float = 0.0) -> int:
        feats = build_feature_frame(df, tf, btc_df=btc_df, funding_df=funding_df)
        self.frames[(symbol, market, tf)] = feats
        self.candles[(symbol, market, tf)] = df.sort_values("timestamp").reset_index(drop=True)
        d = self.candles[(symbol, market, tf)]
        o, h, l, c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        atr_pct = feats["atr_pct"].to_numpy(dtype=float)
        n0 = len(self.events)
        wmax = max(self.windows)
        for i in range(wmax, len(d) - 1, max(1, stride)):
            paths = {}
            ok = True
            for w in self.windows:
                pv = _path_vec(c, i, w)
                if pv is None:
                    ok = False
                    break
                paths[w] = pv
            if not ok or not np.isfinite(atr_pct[i]) or atr_pct[i] <= 0:
                continue
            row = feats.iloc[i]
            snap = np.array([float(row.get(k_, np.nan)) for k_ in SNAP_COLS], dtype=float)
            outs = {}
            entry_ref = float(o[i + 1]); atr_abs = atr_pct[i] / 100 * c[i]
            for side in (("LONG", "SHORT") if market == "futures" else ("LONG",)):
                stp, t1, t2 = barriers_from_atr(entry_ref, atr_abs, side, stop_mult=self.stop_atr_mult, tp1_r=self.tp1_r, tp2_r=self.tp2_r)
                oc = triple_barrier(o, h, l, c, i, side, stop=stp, tp1=t1, tp2=t2, horizon=self.horizon, market=market, fee_pct=self.fee_pct,
                                    slippage_pct=self.slippage_pct, funding_pct_per_bar=funding_pct_per_bar)
                if oc is not None:
                    outs[side] = oc
            if not outs:
                continue
            self.events.append(PatternEvent(symbol, market, tf, i, int(row["event_ts"]), int(row["cutoff_ts"]), regime_code(row),
                                            self.clusters.get(symbol, "default"), paths, snap, outs))
        self._refit_scaler()
        return len(self.events) - n0

    def _refit_scaler(self) -> None:
        if not self.events:
            return
        m = np.array([e.snap for e in self.events], dtype=float)
        self._mu = np.nanmean(m, axis=0)
        sd = np.nanstd(m, axis=0)
        self._sd = np.where(sd > 1e-12, sd, 1.0)

    def _std(self, snap: np.ndarray) -> np.ndarray:
        z = (snap - self._mu) / self._sd
        return np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)

    # ------------------------------------------------------------ sorgu
    def query_vector(self, symbol: str, market: str, tf: str, idx: int | None = None) -> tuple[dict[int, np.ndarray], np.ndarray, pd.Series] | None:
        key = (symbol, market, tf)
        if key not in self.frames:
            return None
        feats, d = self.frames[key], self.candles[key]
        i = len(d) - 1 if idx is None else idx
        c = d["close"].to_numpy(dtype=float)
        paths = {}
        for w in self.windows:
            pv = _path_vec(c, i, w)
            if pv is None:
                return None
            paths[w] = pv
        row = feats.iloc[i]
        return paths, np.array([float(row.get(k_, np.nan)) for k_ in SNAP_COLS], dtype=float), row

    def query(self, symbol: str, market: str, tf: str, side: str, *, query_ts: int | None = None, idx: int | None = None, k: int = 60,
              level: str = "auto", window: int = 64, now_ts: int | None = None) -> dict:
        """level: 'same_coin' | 'cluster' | 'universe' | 'auto' (aynı coin yeterliyse onu, değilse genişlet). Dönen: evidence dict."""
        qv = self.query_vector(symbol, market, tf, idx)
        if qv is None or self._mu is None:
            return {"ok": False, "codes": ["DATA_INVALID"], "n": 0}
        paths, snap, row = qv
        d = self.candles[(symbol, market, tf)]
        i = len(d) - 1 if idx is None else idx
        qts = int(query_ts if query_ts is not None else self.frames[(symbol, market, tf)]["cutoff_ts"].iloc[i])
        step = int(d["timestamp"].iloc[1] - d["timestamp"].iloc[0]) if len(d) > 1 else 0
        min_sep = self.min_separation or max(4, window // 4)
        q_regime = regime_code(row)
        cluster = self.clusters.get(symbol, "default")
        qz = self._std(snap)
        qp = paths[window]
        cands = []
        for e in self.events:
            if window not in e.path or side not in e.outcomes:
                continue
            # geleceğe bakış YOK: komşunun sonucu (exit) da sorgu anından önce bitmiş olmalı
            exit_ts = self.candles[(e.symbol, e.market, e.tf)]["timestamp"].iloc[e.outcomes[side].exit_idx]
            if e.cutoff_ts >= qts - self.embargo_bars * step or int(exit_ts) >= qts:
                continue
            lvl = "same_coin" if e.symbol == symbol else ("cluster" if e.cluster == cluster else "universe")
            if level == "same_coin" and lvl != "same_coin":
                continue
            if level == "cluster" and lvl == "universe":
                continue
            if level in ("universe", "auto") and lvl == "universe" and e.regime != q_regime:
                continue                                              # evren seviyesi: yalnız aynı rejim
            ez = self._std(e.snap)
            d_snap = float(np.sqrt(np.mean((qz - ez) ** 2)))
            corr = float(np.corrcoef(qp, e.path[window])[0, 1]) if e.path[window].std() > 0 else 0.0
            d_path = 1.0 - (corr if np.isfinite(corr) else 0.0)
            dist = 0.5 * d_snap / (1 + d_snap) + 0.5 * d_path / 2.0
            cands.append((dist, lvl, e))
        cands.sort(key=lambda x: (x[0], x[2].symbol, x[2].event_ts))
        chosen: list[tuple[float, str, PatternEvent]] = []
        used: dict[str, list[int]] = {}
        seen: set[tuple[str, int]] = set()
        for dist, lvl, e in cands:
            if level == "auto" and lvl != "same_coin" and len([c_ for c_ in chosen if c_[1] == "same_coin"]) >= self.min_sample:
                continue                                              # auto: aynı coin yeterliyse genişletme
            key = (e.symbol, e.event_ts)
            if key in seen:
                continue
            if any(abs(e.idx - u) < min_sep for u in used.get(e.symbol, [])):
                continue                                              # overlap purge / temporal separation
            chosen.append((dist, lvl, e)); seen.add(key); used.setdefault(e.symbol, []).append(e.idx)
            if len(chosen) >= k:
                break
        outs = [(e.cutoff_ts, e.outcomes[side], e.symbol, e.regime, e.market) for _, _, e in chosen]
        st = compute_stats(outs, now_ts=int(now_ts if now_ts is not None else qts), min_sample=self.min_sample, query_regime=q_regime)
        levels = {}
        for _, lvl, _e in chosen:
            levels[lvl] = levels.get(lvl, 0) + 1
        return {"ok": st.ok, "codes": st.codes, "n": st.n, "stats": st.to_dict(), "query": {"symbol": symbol, "market": market, "tf": tf, "side": side,
                "query_ts": qts, "regime": q_regime, "window": window, "level": level}, "levels": levels,
                "neighbors": [{"symbol": e.symbol, "event_ts": e.event_ts, "regime": e.regime, "distance": round(dist, 4), "level": lvl,
                               "net_r": round(e.outcomes[side].net_r, 3), "exit": e.outcomes[side].exit_reason, "bars_held": e.outcomes[side].bars_held}
                              for dist, lvl, e in chosen[:20]]}


__all__ = ["SimilarPatternEngine", "PatternEvent", "EvidenceStats", "compute_stats", "wilson", "regime_code", "WINDOWS", "SNAP_COLS"]
