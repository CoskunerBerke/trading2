"""Belirsizlik araclari — nokta tahmin TEK BASINA rapor edilmez.

Kural: her sayinin yaninda ornek buyuklugu ve bir aralik olur. Kumeli veride (ayni sembolun
tekrar tekrar degerlendirilmesi) siradan hata payi YANILTIR; burada kume tabanli bootstrap
varsayilan yoldur.
"""
from __future__ import annotations

import math
import random
from typing import Any, Callable, Sequence

MIN_CELL = 15   #: bunun altindaki kesitler "GUCSUZ" damgasi yer; nokta tahmin bulgu sayilmaz.


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def t_ci(xs: Sequence[float], conf: float = 0.95) -> tuple[float, float, float, int]:
    """(ortalama, alt, ust, n) — BAGIMSIZ gozlem varsayimiyla normal yaklasim. Kumeli veride KULLANMA."""
    n = len(xs)
    if n < 2:
        return (mean(xs) if n else float("nan"), float("nan"), float("nan"), n)
    z = 1.959964 if conf == 0.95 else 2.575829
    se = stdev(xs) / math.sqrt(n)
    m = mean(xs)
    return m, m - z * se, m + z * se, n


def wilson(k: int, n: int, conf: float = 0.95) -> tuple[float, float, float]:
    """Oran icin Wilson skor araligi (kucuk n'de normal yaklasimdan cok daha durustur)."""
    if n == 0:
        return (float("nan"),) * 3
    z = 1.959964 if conf == 0.95 else 2.575829
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, c - h, c + h


def cluster_bootstrap(rows: Sequence[Any], value: Callable[[Any], float], cluster: Callable[[Any], str],
                      *, stat: Callable[[Sequence[float]], float] = mean, n_boot: int = 5000,
                      conf: float = 0.95, seed: int = 20260909) -> dict[str, Any]:
    """Kume bazli (blok) bootstrap: kumeler yerine koyarak cekilir, kume ICI yapi bozulmaz.

    Ayni sembolun 400 adayi 400 bagimsiz gozlem degildir; bu yordam bunu dikkate alir. Donen aralik
    yine de piyasa GENELI es-hareketi yakalamaz — o icin gun bloklu kume kullanilir.
    """
    groups: dict[str, list[float]] = {}
    for r in rows:
        v = value(r)
        if v is None:
            continue
        groups.setdefault(cluster(r), []).append(float(v))
    keys = list(groups)
    flat = [v for k in keys for v in groups[k]]
    if not flat:
        return {"n": 0, "n_clusters": 0, "point": float("nan"), "lo": float("nan"), "hi": float("nan")}
    rng = random.Random(seed)
    point = stat(flat)
    draws: list[float] = []
    nk = len(keys)
    for _ in range(n_boot):
        s: list[float] = []
        for _ in range(nk):
            s.extend(groups[keys[rng.randrange(nk)]])
        if s:
            draws.append(stat(s))
    draws.sort()
    a = (1 - conf) / 2
    lo = draws[max(0, int(a * len(draws)) - 1)] if draws else float("nan")
    hi = draws[min(len(draws) - 1, int((1 - a) * len(draws)))] if draws else float("nan")
    return {"n": len(flat), "n_clusters": nk, "point": point, "lo": lo, "hi": hi,
            "underpowered": nk < MIN_CELL or len(flat) < MIN_CELL}


def diff_cluster_bootstrap(rows_a: Sequence[Any], rows_b: Sequence[Any], value: Callable[[Any], float],
                           cluster: Callable[[Any], str], *, n_boot: int = 5000, conf: float = 0.95,
                           seed: int = 20260909) -> dict[str, Any]:
    """A − B farki icin ORTAK kume cekilisiyle bootstrap (ayni sembol iki tarafta da ayni cekiliste).

    Ortak cekilis sart: A ve B ayni adaylardan turuyorsa bagimsiz cekilis farkin varyansini SISIRIR.
    """
    ga: dict[str, list[float]] = {}
    gb: dict[str, list[float]] = {}
    for r in rows_a:
        v = value(r)
        if v is not None:
            ga.setdefault(cluster(r), []).append(float(v))
    for r in rows_b:
        v = value(r)
        if v is not None:
            gb.setdefault(cluster(r), []).append(float(v))
    keys = sorted(set(ga) | set(gb))
    fa = [v for k in keys for v in ga.get(k, [])]
    fb = [v for k in keys for v in gb.get(k, [])]
    if not fa or not fb:
        return {"n_a": len(fa), "n_b": len(fb), "point": float("nan"), "lo": float("nan"), "hi": float("nan")}
    point = mean(fa) - mean(fb)
    rng = random.Random(seed)
    draws: list[float] = []
    nk = len(keys)
    for _ in range(n_boot):
        sa: list[float] = []
        sb: list[float] = []
        for _ in range(nk):
            k = keys[rng.randrange(nk)]
            sa.extend(ga.get(k, []))
            sb.extend(gb.get(k, []))
        if sa and sb:
            draws.append(mean(sa) - mean(sb))
    draws.sort()
    a = (1 - conf) / 2
    return {"n_a": len(fa), "n_b": len(fb), "n_clusters": nk, "point": point,
            "lo": draws[max(0, int(a * len(draws)) - 1)] if draws else float("nan"),
            "hi": draws[min(len(draws) - 1, int((1 - a) * len(draws)))] if draws else float("nan"),
            "p_two_sided": _boot_p(draws, 0.0)}


def _boot_p(draws: Sequence[float], null: float = 0.0) -> float:
    """Bootstrap dagiliminin null'i kapsamasina dayali iki yonlu p (kaba ama seffaf)."""
    if not draws:
        return float("nan")
    below = sum(1 for d in draws if d <= null) / len(draws)
    return min(1.0, 2 * min(below, 1 - below))


def holm(pvals: dict[str, float], alpha: float = 0.05) -> dict[str, dict[str, float | bool]]:
    """Holm–Bonferroni: uc rakip AYNI veride test edildiginde tekil p yeterli DEGILDIR.

    Ailenin tamami icin FWER `alpha`da tutulur; her rakip icin duzeltilmis esik ve karar doner.
    """
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, dict[str, float | bool]] = {}
    rejected = True
    for i, (k, p) in enumerate(items):
        thr = alpha / (m - i)
        rejected = rejected and (p <= thr)
        out[k] = {"p": p, "holm_threshold": thr, "reject_at_fwer": bool(rejected)}
    return out


__all__ = ["MIN_CELL", "cluster_bootstrap", "diff_cluster_bootstrap", "holm", "mean", "stdev", "t_ci", "wilson"]
