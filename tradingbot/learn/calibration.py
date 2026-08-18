"""Kalibrasyon: Platt (2 parametreli LR), izotonik (PAVA), Brier, log-loss, ECE, güvenilirlik eğrisi."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2)) if len(p) else 0.0


def log_loss(p, y, eps: float = 1e-7) -> float:
    p, y = np.clip(np.asarray(p, float), eps, 1 - eps), np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(p) else 0.0


def reliability_curve(p, y, bins: int = 10) -> list[dict]:
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if m.sum() == 0:
            continue
        out.append({"bin_lo": float(edges[i]), "bin_hi": float(edges[i + 1]), "n": int(m.sum()), "p_mean": float(p[m].mean()), "y_rate": float(y[m].mean())})
    return out


def ece(p, y, bins: int = 10) -> float:
    n = len(p)
    if not n:
        return 0.0
    return float(sum(b["n"] / n * abs(b["p_mean"] - b["y_rate"]) for b in reliability_curve(p, y, bins)))


def platt_fit(scores, y, iters: int = 300, lr: float = 0.1) -> tuple[float, float]:
    """p = σ(a·s + b) — s log-odds ya da ham skor olabilir."""
    s, y = np.asarray(scores, float), np.asarray(y, float)
    a, b = 1.0, 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(a * s + b, -30, 30)))
        g = p - y
        a -= lr * float(np.mean(g * s))
        b -= lr * float(np.mean(g))
    return float(a), float(b)


def isotonic_fit(scores, y) -> tuple[list[float], list[float]]:
    """PAVA: artan skor → monoton artmayan olmayan (non-decreasing) olasılık basamakları. → (x_knots, y_knots)"""
    s, y = np.asarray(scores, float), np.asarray(y, float)
    order = np.argsort(s, kind="stable")
    s, y = s[order], y[order]
    blocks: list[list[float]] = []   # [sum, n, x_min, x_max]
    for xi, yi in zip(s, y):
        blocks.append([float(yi), 1.0, float(xi), float(xi)])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            b2 = blocks.pop()
            b1 = blocks[-1]
            b1[0] += b2[0]; b1[1] += b2[1]; b1[3] = b2[3]
    xs = [b[2] for b in blocks]
    ys = [b[0] / b[1] for b in blocks]
    return xs, ys


def isotonic_predict(knots: tuple[list[float], list[float]], scores) -> np.ndarray:
    xs, ys = knots
    if not xs:
        return np.full(len(np.atleast_1d(scores)), 0.5)
    return np.interp(np.asarray(scores, float), xs, ys, left=ys[0], right=ys[-1])


@dataclass
class Calibrator:
    kind: str = "platt"          # platt | isotonic | none
    a: float = 1.0
    b: float = 0.0
    xs: list[float] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)
    n_fit: int = 0

    def fit(self, scores, y) -> "Calibrator":
        scores, y = np.asarray(scores, float), np.asarray(y, float)
        self.n_fit = int(len(y))
        if self.kind == "platt":
            logit = np.log(np.clip(scores, 1e-6, 1 - 1e-6) / (1 - np.clip(scores, 1e-6, 1 - 1e-6)))
            self.a, self.b = platt_fit(logit, y)
        elif self.kind == "isotonic":
            self.xs, self.ys = isotonic_fit(scores, y)
        return self

    def apply(self, scores) -> np.ndarray:
        s = np.asarray(scores, float)
        if self.kind == "platt" and self.n_fit:
            logit = np.log(np.clip(s, 1e-6, 1 - 1e-6) / (1 - np.clip(s, 1e-6, 1 - 1e-6)))
            return 1 / (1 + np.exp(-np.clip(self.a * logit + self.b, -30, 30)))
        if self.kind == "isotonic" and self.xs:
            return isotonic_predict((self.xs, self.ys), s)
        return s

    def to_dict(self) -> dict:
        return {"kind": self.kind, "a": self.a, "b": self.b, "xs": self.xs, "ys": self.ys, "n_fit": self.n_fit}

    @classmethod
    def from_dict(cls, d: dict) -> "Calibrator":
        return cls(d.get("kind", "platt"), float(d.get("a", 1.0)), float(d.get("b", 0.0)), list(d.get("xs", [])), list(d.get("ys", [])), int(d.get("n_fit", 0)))


def calibration_metrics(p, y, bins: int = 10) -> dict:
    return {"n": int(len(np.atleast_1d(y))), "brier": round(brier(p, y), 5), "log_loss": round(log_loss(p, y), 5), "ece": round(ece(p, y, bins), 5),
            "reliability": reliability_curve(p, y, bins)}
