"""Doğrulama araç seti — walk-forward, purged k-fold, blok bootstrap, Monte Carlo DD, komşuluk kararlılığı,
Probabilistic/Deflated Sharpe, genişletilmiş metrikler ve champion/challenger kapısı.

Amaç (audit_agent3): tek bölme + 64 config × N coin taramasındaki çoklu-test yanlış pozitiflerini bastırmak.
`run_fn(train_df, test_df) -> dict` sözleşmesi: sözlükte 'sharpe', 'trades', 'return_pct' (varsa) beklenir;
mevcut olan sayısal anahtarlar toplanır (ortalama / toplam).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

RunFn = Callable[[pd.DataFrame, pd.DataFrame], dict[str, Any]]

_SUM_KEYS = {"trades", "n_trades", "wins", "losses", "bars"}


# --------------------------------------------------------------------------- aggregation helper
def _aggregate_folds(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold sözlüklerini toplar: sayım anahtarları toplanır, diğer sayısal anahtarlar ortalanır. Ayrıca
    `folds`, `n_folds`, `pct_positive` (return_pct veya sharpe > 0 olan fold oranı) eklenir."""
    out: dict[str, Any] = {"folds": results, "n_folds": len(results)}
    if not results:
        out["pct_positive"] = 0.0
        return out
    keys = set()
    for r in results:
        keys |= {k for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    for k in sorted(keys):
        vals = [float(r[k]) for r in results if k in r and np.isfinite(float(r[k]))]
        if not vals:
            continue
        out[k] = float(np.sum(vals)) if k in _SUM_KEYS else float(np.mean(vals))
    pos_key = "return_pct" if any("return_pct" in r for r in results) else "sharpe"
    vals = [float(r.get(pos_key, 0.0)) for r in results]
    out["pct_positive"] = float(np.mean([v > 0 for v in vals])) if vals else 0.0
    if "sharpe" in keys:
        s = [float(r["sharpe"]) for r in results if "sharpe" in r]
        out["sharpe_min"] = float(np.min(s))
        out["sharpe_std"] = float(np.std(s, ddof=1)) if len(s) > 1 else 0.0
    return out


# --------------------------------------------------------------------------- walk-forward
def anchored_wfo(df: pd.DataFrame, run_fn: RunFn, folds: int = 4, initial_train_ratio: float = 0.5,
                 purge: int = 0, embargo: int = 0) -> dict[str, Any]:
    """Sabit başlangıçlı (büyüyen eğitim penceresi) walk-forward.

    [---train---][purge][test1]  →  [------train------][purge][test2] ...
    `purge`: eğitim sonu ile test başı arasında atılan bar (gösterge/etiket sızıntısı);
    `embargo`: test bitişinden sonraki eğitime katılmayan bar (bir sonraki fold'un eğitimi test bitişi+embargo'ya kadar
    değil, test başlangıcı - purge'a kadar gider; embargo burada eğitim sonunu geri çeker).
    """
    n = len(df)
    folds = max(1, int(folds))
    first_train_end = int(n * float(initial_train_ratio))
    if first_train_end <= 0 or first_train_end >= n:
        return _aggregate_folds([])
    test_len = (n - first_train_end) // folds
    if test_len <= 0:
        return _aggregate_folds([])
    results = []
    for k in range(folds):
        test_start = first_train_end + k * test_len
        test_end = test_start + test_len if k < folds - 1 else n
        train_end = max(0, test_start - int(purge) - (int(embargo) if k > 0 else 0))
        train_df = df.iloc[:train_end]
        test_df = df.iloc[test_start:test_end]
        if len(train_df) == 0 or len(test_df) == 0:
            continue
        r = dict(run_fn(train_df, test_df))
        r.setdefault("fold", k + 1)
        r.setdefault("train_bars", len(train_df))
        r.setdefault("test_bars", len(test_df))
        results.append(r)
    return _aggregate_folds(results)


def rolling_wfo(df: pd.DataFrame, run_fn: RunFn, train_bars: int, test_bars: int, purge: int = 0,
                embargo: int = 0) -> dict[str, Any]:
    """Kayan (sabit uzunluklu) eğitim penceresi walk-forward. Her adımda pencere `test_bars` kadar ilerler;
    eğitim = [test_start - purge - train_bars, test_start - purge)."""
    n = len(df)
    train_bars, test_bars = int(train_bars), int(test_bars)
    if train_bars <= 0 or test_bars <= 0:
        return _aggregate_folds([])
    results = []
    k = 0
    test_start = train_bars + int(purge)
    while test_start + test_bars <= n:
        train_end = test_start - int(purge)
        train_start = max(0, train_end - train_bars)
        train_df = df.iloc[train_start:train_end]
        test_df = df.iloc[test_start:test_start + test_bars]
        if len(train_df) < train_bars:  # tam pencere değil
            test_start += test_bars
            continue
        r = dict(run_fn(train_df, test_df))
        k += 1
        r.setdefault("fold", k)
        r.setdefault("train_bars", len(train_df))
        r.setdefault("test_bars", len(test_df))
        results.append(r)
        test_start += test_bars + int(embargo)
    return _aggregate_folds(results)


def purged_kfold(n: int, k: int, embargo: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Zaman serisi için purged K-fold: her test bloğu ardışık; eğitimden test bloğu + her iki yanında `embargo` bar
    çıkarılır. Dönen: [(train_idx, test_idx), ...] (numpy int dizileri)."""
    n, k, embargo = int(n), int(k), max(0, int(embargo))
    if n <= 0 or k <= 1:
        idx = np.arange(n)
        return [(idx[:0], idx)] if n else []
    bounds = np.linspace(0, n, k + 1, dtype=int)
    out = []
    all_idx = np.arange(n)
    for i in range(k):
        s, e = int(bounds[i]), int(bounds[i + 1])
        if e <= s:
            continue
        test_idx = all_idx[s:e]
        lo, hi = max(0, s - embargo), min(n, e + embargo)
        mask = np.ones(n, dtype=bool)
        mask[lo:hi] = False
        out.append((all_idx[mask], test_idx))
    return out


# --------------------------------------------------------------------------- bootstrap / MC
def block_bootstrap_ci(values: Sequence[float], stat_fn: Callable[[np.ndarray], float] = np.mean, n: int = 2000,
                       block: int = 5, alpha: float = 0.05, seed: int = 0) -> dict[str, float]:
    """Dairesel blok bootstrap güven aralığı. Dönen: {stat, lo, hi, p_le_zero, n_boot}."""
    x = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if x.size == 0:
        return {"stat": float("nan"), "lo": float("nan"), "hi": float("nan"), "p_le_zero": float("nan"), "n_boot": 0}
    rng = np.random.default_rng(seed)
    block = max(1, min(int(block), x.size))
    n_blocks = int(math.ceil(x.size / block))
    stats = np.empty(int(n))
    for i in range(int(n)):
        starts = rng.integers(0, x.size, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % x.size
        stats[i] = float(stat_fn(x[idx[: x.size]]))
    lo, hi = np.quantile(stats, [alpha / 2, 1 - alpha / 2])
    return {"stat": float(stat_fn(x)), "lo": float(lo), "hi": float(hi), "p_le_zero": float(np.mean(stats <= 0)),
            "n_boot": int(n)}


def _max_dd_pct(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peaks > 0, (equity - peaks) / peaks, 0.0)
    return float(-dd.min() * 100.0)


def monte_carlo_drawdown(trade_pnls: Sequence[float], n: int = 2000, seed: int = 0,
                         starting_equity: float | None = None) -> dict[str, Any]:
    """İşlem P&L'lerini karıştırıp (yerine koymalı) max drawdown dağılımı. Dönen: p50/p95/p99 (%),
    `p_dd_gt(threshold_pct)` çağrılabilir + `p_dd_gt_table` {10,20,25,30,50: olasılık}, `worst`."""
    x = np.asarray([v for v in trade_pnls if v is not None and np.isfinite(v)], dtype=float)
    empty = {"p50": 0.0, "p95": 0.0, "p99": 0.0, "worst": 0.0, "n": 0, "p_dd_gt": (lambda t: 0.0), "p_dd_gt_table": {}}
    if x.size == 0:
        return empty
    start = float(starting_equity) if starting_equity else max(1.0, float(abs(x).sum()))
    rng = np.random.default_rng(seed)
    dds = np.empty(int(n))
    for i in range(int(n)):
        sample = rng.choice(x, size=x.size, replace=True)
        eq = start + np.cumsum(sample)
        eq = np.concatenate([[start], eq])
        dds[i] = _max_dd_pct(np.maximum(eq, 1e-12))
    p50, p95, p99 = np.percentile(dds, [50, 95, 99])

    def p_dd_gt(threshold_pct: float) -> float:
        return float(np.mean(dds > float(threshold_pct)))

    return {"p50": float(p50), "p95": float(p95), "p99": float(p99), "worst": float(dds.max()), "n": int(n),
            "p_dd_gt": p_dd_gt, "p_dd_gt_table": {t: p_dd_gt(t) for t in (10, 20, 25, 30, 50)}}


# --------------------------------------------------------------------------- neighbourhood / DSR
def neighbourhood_stability(grid: dict[tuple, float], center: tuple, radius: int = 1) -> dict[str, Any]:
    """Parametre ızgarasında merkezin `radius` Chebyshev komşuluğundaki skorların ortalaması / merkeze oranı.
    Anahtarlar sayısal parametre tuple'ları; komşuluk her eksende sıralı benzersiz değerlerin indeks mesafesiyle
    ölçülür (adım eşit olmasa da çalışır)."""
    if center not in grid:
        return {"center": None, "neighbour_mean": float("nan"), "ratio": 0.0, "n_neighbours": 0}
    dims = len(center)
    axes = [sorted({k[d] for k in grid}) for d in range(dims)]
    pos = {d: {v: i for i, v in enumerate(axes[d])} for d in range(dims)}
    c_idx = [pos[d][center[d]] for d in range(dims)]
    neigh = []
    for k, v in grid.items():
        if len(k) != dims or k == center:
            continue
        if all(abs(pos[d][k[d]] - c_idx[d]) <= radius for d in range(dims)):
            if v is not None and np.isfinite(v):
                neigh.append(float(v))
    c = float(grid[center])
    if not neigh:
        return {"center": c, "neighbour_mean": float("nan"), "ratio": 0.0, "n_neighbours": 0}
    m = float(np.mean(neigh))
    ratio = (m / c) if c > 0 else (1.0 if m >= c else 0.0)
    return {"center": c, "neighbour_mean": m, "neighbour_min": float(np.min(neigh)), "ratio": float(ratio),
            "n_neighbours": len(neigh)}


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam yaklaşımı ile ters normal (scipy'siz)."""
    if p <= 0.0:
        return -float("inf")
    if p >= 1.0:
        return float("inf")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - plow:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def probabilistic_sharpe(sr: float, sr_bench: float, T: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """PSR (Bailey & López de Prado): P[SR* > sr_bench]. `sr` gözlemlenen (dönem başına) Sharpe, T gözlem sayısı."""
    T = int(T)
    if T <= 1 or not np.isfinite(sr):
        return 0.0
    var = (1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr) / (T - 1)
    if var <= 0 or not np.isfinite(var):
        return 0.0
    return float(_norm_cdf((sr - sr_bench) / math.sqrt(var)))


def deflated_sharpe(sr: float, n_trials: int, T: int, skew: float = 0.0, kurt: float = 3.0,
                    sr_var: float | None = None) -> float:
    """DSR: çoklu deneme (n_trials) için beklenen maksimum Sharpe'ı eşik alarak PSR. `sr_var` denemeler arası
    Sharpe varyansı (bilinmiyorsa 1/(T-1) ile yaklaşık — muhafazakâr olsun diye max(.,0.01))."""
    n_trials = max(1, int(n_trials))
    T = int(T)
    if T <= 1:
        return 0.0
    if sr_var is None:
        sr_var = max(1.0 / (T - 1), 0.01)
    if n_trials == 1:
        sr0 = 0.0
    else:
        gamma = 0.5772156649015329
        e = math.e
        z1 = _norm_ppf(1.0 - 1.0 / n_trials)
        z2 = _norm_ppf(1.0 - 1.0 / (n_trials * e))
        sr0 = math.sqrt(max(sr_var, 0.0)) * ((1 - gamma) * z1 + gamma * z2)
    return probabilistic_sharpe(sr, sr0, T, skew, kurt)


# --------------------------------------------------------------------------- extended metrics
def metrics_extended(equity: Sequence[float] | pd.Series, trades_r: Sequence[float], bars_per_year: float) -> dict[str, float]:
    """Sortino, Calmar, Ulcer, expectancy_r, payoff, avg_win_r, avg_loss_r, cvar_5, max_consec_losses, win_rate,
    profit_factor, max_dd_pct, sharpe, total_return_pct, cagr_pct, trades."""
    eq = np.asarray(pd.Series(equity).astype(float).to_numpy() if not isinstance(equity, np.ndarray) else equity, dtype=float)
    eq = eq[np.isfinite(eq)]
    r = np.asarray([v for v in trades_r if v is not None and np.isfinite(v)], dtype=float)
    out: dict[str, float] = {"trades": float(r.size), "bars": float(eq.size)}
    # equity based
    if eq.size >= 2 and eq[0] > 0:
        rets = np.diff(eq) / eq[:-1]
        rets = rets[np.isfinite(rets)]
        sd = rets.std(ddof=1) if rets.size > 1 else 0.0
        out["sharpe"] = float(rets.mean() / sd * math.sqrt(bars_per_year)) if sd > 0 else 0.0
        neg = rets[rets < 0]
        dsd = math.sqrt(float(np.mean(np.square(neg)))) if neg.size else 0.0
        out["sortino"] = float(rets.mean() / dsd * math.sqrt(bars_per_year)) if dsd > 0 else (float("inf") if rets.mean() > 0 else 0.0)
        out["max_dd_pct"] = _max_dd_pct(eq)
        out["total_return_pct"] = float((eq[-1] / eq[0] - 1.0) * 100.0)
        years = eq.size / float(bars_per_year) if bars_per_year > 0 else 0.0
        out["cagr_pct"] = float(((eq[-1] / eq[0]) ** (1.0 / years) - 1.0) * 100.0) if years > 0 and eq[-1] > 0 else 0.0
        out["calmar"] = float(out["cagr_pct"] / out["max_dd_pct"]) if out["max_dd_pct"] > 0 else (float("inf") if out["cagr_pct"] > 0 else 0.0)
        peaks = np.maximum.accumulate(eq)
        dd_pct = (eq - peaks) / peaks * 100.0
        out["ulcer"] = float(math.sqrt(np.mean(np.square(dd_pct))))
    else:
        out.update({"sharpe": 0.0, "sortino": 0.0, "max_dd_pct": 0.0, "total_return_pct": 0.0, "cagr_pct": 0.0,
                    "calmar": 0.0, "ulcer": 0.0})
    # trade based (R multiples)
    if r.size:
        wins, losses = r[r > 0], r[r <= 0]
        out["win_rate"] = float(wins.size / r.size)
        out["avg_win_r"] = float(wins.mean()) if wins.size else 0.0
        out["avg_loss_r"] = float(losses.mean()) if losses.size else 0.0
        out["payoff"] = float(out["avg_win_r"] / abs(out["avg_loss_r"])) if losses.size and out["avg_loss_r"] != 0 else (float("inf") if wins.size else 0.0)
        out["expectancy_r"] = float(r.mean())
        gp, gl = float(wins.sum()), float(-losses.sum())
        out["profit_factor"] = float(gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
        k = max(1, int(math.ceil(0.05 * r.size)))
        out["cvar_5"] = float(np.sort(r)[:k].mean())
        mc, cur = 0, 0
        for v in r:
            cur = cur + 1 if v <= 0 else 0
            mc = max(mc, cur)
        out["max_consec_losses"] = float(mc)
    else:
        out.update({"win_rate": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0, "payoff": 0.0, "expectancy_r": 0.0,
                    "profit_factor": 0.0, "cvar_5": 0.0, "max_consec_losses": 0.0})
    return out


# --------------------------------------------------------------------------- report + gate
@dataclass
class ValidationReport:
    """Bir aday (challenger) için toplanmış doğrulama kanıtı — `champion_challenger_gate` girdisi."""
    label: str = ""
    dsr: float = 0.0
    psr: float = 0.0
    oos_sharpe: float = 0.0
    oos_trades: int = 0
    oos_profit_factor: float = 0.0
    is_max_dd_pct: float = 0.0
    mc_dd_p95: float = 0.0
    bootstrap_expectancy_lo: float = 0.0
    neighbourhood_ratio: float = 0.0
    folds_pct_positive: float = 0.0
    n_trials: int = 1
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


DEFAULT_GATE_THRESHOLDS: dict[str, float] = {
    "min_dsr": 0.95,
    "min_oos_trades": 30,
    "min_bootstrap_expectancy_lo": 0.0,     # 5. yüzdelik > 0
    "min_profit_factor": 1.25,
    "max_mc_dd_vs_is": 1.5,                 # MC p95 DD ≤ 1.5 × IS DD
    "max_mc_dd_pct": 25.0,
    "min_neighbourhood_ratio": 0.6,
    "min_folds_pct_positive": 0.6,
    "min_sharpe_gain_vs_champion": 0.2,
}


def champion_challenger_gate(champion: dict[str, Any] | None, challenger: dict[str, Any] | ValidationReport,
                             thresholds: dict[str, float] | None = None) -> tuple[bool, list[str]]:
    """Challenger'ın champion'u değiştirebilmesi için tüm eşikleri geçmesi gerekir. (ok, reasons)."""
    th = dict(DEFAULT_GATE_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    c = challenger.to_dict() if isinstance(challenger, ValidationReport) else dict(challenger)
    ch = champion.to_dict() if isinstance(champion, ValidationReport) else (dict(champion) if champion else None)
    reasons: list[str] = []

    def g(d: dict[str, Any], *keys: str, default: float = float("nan")) -> float:
        for k in keys:
            if k in d and d[k] is not None:
                try:
                    return float(d[k])
                except (TypeError, ValueError):
                    continue
        return default

    dsr = g(c, "dsr")
    if not (dsr > th["min_dsr"]):
        reasons.append(f"DSR {dsr:.3f} <= {th['min_dsr']}")
    trades = g(c, "oos_trades", "trades", default=0)
    if trades < th["min_oos_trades"]:
        reasons.append(f"OOS trades {trades:.0f} < {th['min_oos_trades']:.0f}")
    blo = g(c, "bootstrap_expectancy_lo", "expectancy_lo")
    if not (blo > th["min_bootstrap_expectancy_lo"]):
        reasons.append(f"bootstrap 5th-pct expectancy {blo:.4f} <= {th['min_bootstrap_expectancy_lo']}")
    pf = g(c, "oos_profit_factor", "profit_factor")
    if not (pf >= th["min_profit_factor"]):
        reasons.append(f"PF {pf:.3f} < {th['min_profit_factor']}")
    mc = g(c, "mc_dd_p95")
    isdd = g(c, "is_max_dd_pct", "max_dd_pct")
    if not np.isfinite(mc):
        reasons.append("MC p95 DD missing")
    else:
        if np.isfinite(isdd) and isdd > 0 and mc > th["max_mc_dd_vs_is"] * isdd:
            reasons.append(f"MC p95 DD {mc:.2f}% > {th['max_mc_dd_vs_is']}× IS DD {isdd:.2f}%")
        if mc > th["max_mc_dd_pct"]:
            reasons.append(f"MC p95 DD {mc:.2f}% > {th['max_mc_dd_pct']}%")
    nb = g(c, "neighbourhood_ratio")
    if not (nb >= th["min_neighbourhood_ratio"]):
        reasons.append(f"neighbourhood ratio {nb:.3f} < {th['min_neighbourhood_ratio']}")
    fp = g(c, "folds_pct_positive", "pct_positive")
    if not (fp >= th["min_folds_pct_positive"]):
        reasons.append(f"folds positive {fp:.2f} < {th['min_folds_pct_positive']}")
    if ch:
        cs = g(ch, "oos_sharpe", "sharpe", default=float("nan"))
        s = g(c, "oos_sharpe", "sharpe")
        if np.isfinite(cs) and not (s >= cs + th["min_sharpe_gain_vs_champion"]):
            reasons.append(f"sharpe {s:.3f} < champion {cs:.3f} + {th['min_sharpe_gain_vs_champion']}")
    return (len(reasons) == 0), reasons


__all__ = ["anchored_wfo", "rolling_wfo", "purged_kfold", "block_bootstrap_ci", "monte_carlo_drawdown",
           "neighbourhood_stability", "probabilistic_sharpe", "deflated_sharpe", "metrics_extended", "ValidationReport",
           "champion_challenger_gate", "DEFAULT_GATE_THRESHOLDS"]
