"""İstatistiksel model katmanı (numpy): train-only StandardScaler, L2 lojistik regresyon (batch GD), hiyerarşik Beta shrinkage."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core import iso, utc_now


@dataclass
class StandardScaler:
    mean: list[float] = field(default_factory=list)
    std: list[float] = field(default_factory=list)

    def fit(self, X: np.ndarray) -> "StandardScaler":
        X = np.asarray(X, float)
        self.mean = X.mean(axis=0).tolist()
        sd = X.std(axis=0)
        sd[sd < 1e-9] = 1.0
        self.std = sd.tolist()
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, float)
        return (X - np.array(self.mean)) / np.array(self.std)

    def to_dict(self) -> dict:
        return {"mean": self.mean, "std": self.std}

    @classmethod
    def from_dict(cls, d: dict) -> "StandardScaler":
        return cls(list(d.get("mean", [])), list(d.get("std", [])))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


@dataclass
class LogisticModel:
    """L2 düzenlemeli lojistik regresyon; recency ve sınıf ağırlıklarını destekler. Deterministik."""
    feature_names: list[str] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    scaler: StandardScaler = field(default_factory=StandardScaler)
    l2: float = 0.05
    n_train: int = 0
    trained_at: str = ""
    version: str = "lr-v2"

    def fit(self, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None, *, iters: int = 500, lr: float = 0.1,
            class_weight: bool = True) -> "LogisticModel":
        X = np.asarray(X, float)
        y = np.asarray(y, float)
        n, d = X.shape
        self.scaler = StandardScaler().fit(X)
        Xs = self.scaler.transform(X)
        w = np.zeros(d)
        b = 0.0
        sw = np.ones(n) if sample_weight is None else np.asarray(sample_weight, float)
        if class_weight:
            pos = max(1.0, float((y > 0.5).sum()))
            neg = max(1.0, float((y <= 0.5).sum()))
            cw = np.where(y > 0.5, n / (2 * pos), n / (2 * neg))
            sw = sw * cw
        sw = sw / sw.sum() * n
        for _ in range(iters):
            p = _sigmoid(Xs @ w + b)
            g = (sw * (p - y))
            gw = Xs.T @ g / n + self.l2 * w
            gb = g.mean()
            w -= lr * gw
            b -= lr * gb
        self.weights, self.bias, self.n_train = w.tolist(), float(b), int(n)
        self.trained_at = iso(utc_now())
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, float)
        if X.ndim == 1:
            X = X[None, :]
        if not self.weights:
            return np.full(len(X), 0.5)
        return _sigmoid(self.scaler.transform(X) @ np.array(self.weights) + self.bias)

    def to_dict(self) -> dict:
        return {"feature_names": self.feature_names, "weights": self.weights, "bias": self.bias, "scaler": self.scaler.to_dict(),
                "l2": self.l2, "n_train": self.n_train, "trained_at": self.trained_at, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> "LogisticModel":
        m = cls(list(d.get("feature_names", [])), list(d.get("weights", [])), float(d.get("bias", 0.0)),
                StandardScaler.from_dict(d.get("scaler", {})), float(d.get("l2", 0.05)), int(d.get("n_train", 0)),
                str(d.get("trained_at", "")), str(d.get("version", "lr-v2")))
        return m


def recency_weights(ages_days: np.ndarray, half_life_days: float = 60.0) -> np.ndarray:
    return 0.5 ** (np.asarray(ages_days, float) / max(half_life_days, 1e-9))


# ---------------------------------------------------------------- hiyerarşik shrinkage
@dataclass
class RateStat:
    n: int = 0
    s: float = 0.0        # toplam (kazanç sayısı ya da R toplamı)
    ss: float = 0.0       # kareler toplamı (varyans için)

    def add(self, x: float) -> None:
        self.n += 1
        self.s += x
        self.ss += x * x

    @property
    def mean(self) -> float:
        return self.s / self.n if self.n else 0.0

    @property
    def var(self) -> float:
        if self.n < 2:
            return 0.0
        m = self.mean
        return max(0.0, self.ss / self.n - m * m)

    def to_dict(self) -> dict:
        return {"n": self.n, "s": self.s, "ss": self.ss}

    @classmethod
    def from_dict(cls, d: dict) -> "RateStat":
        return cls(int(d.get("n", 0)), float(d.get("s", 0.0)), float(d.get("ss", 0.0)))


class HierarchicalRate:
    """global → regime → symbol/setup posterior ortalaması: (s + α·μ_parent) / (n + α). Az verili yaprakta ebeveyne çekilir."""

    def __init__(self, alpha: float = 10.0, prior_mean: float = 0.5):
        self.alpha = alpha
        self.prior_mean = prior_mean
        self.stats: dict[str, RateStat] = {}     # anahtar: "" (global) | "regime:X" | "regime:X|leaf:Y" | "leaf:Y"

    def add(self, x: float, *, regime: str | None = None, leaf: str | None = None) -> None:
        keys = [""]
        if regime:
            keys.append(f"regime:{regime}")
        if leaf:
            keys.append(f"leaf:{leaf}")
        if regime and leaf:
            keys.append(f"regime:{regime}|leaf:{leaf}")
        for k in keys:
            self.stats.setdefault(k, RateStat()).add(x)

    def _post(self, key: str, parent_mean: float) -> tuple[float, float]:
        st = self.stats.get(key)
        if not st or st.n == 0:
            return parent_mean, 0.0
        return (st.s + self.alpha * parent_mean) / (st.n + self.alpha), float(st.n)

    def estimate(self, *, regime: str | None = None, leaf: str | None = None) -> tuple[float, float]:
        """→ (posterior mean, n_eff). Sıra: global → regime → leaf → regime|leaf."""
        g, n = self._post("", self.prior_mean)
        m = g
        if regime:
            m, n = self._post(f"regime:{regime}", m)
        if leaf:
            m, n2 = self._post(f"leaf:{leaf}", m)
            n = n2 or n
            if regime:
                m, n3 = self._post(f"regime:{regime}|leaf:{leaf}", m)
                n = n3 or n
        return m, n

    def is_negative_with_evidence(self, *, leaf: str, threshold: float = -0.1, prob: float = 0.8, regime: str | None = None) -> bool:
        """Beklenti (R) eşiğin altında ve P(mean<0) > prob ise (normal yaklaşımı) → kara liste adayı."""
        m, n = self.estimate(regime=regime, leaf=leaf)
        st = self.stats.get(f"leaf:{leaf}")
        if not st or st.n < 5 or m >= threshold:
            return False
        se = math.sqrt(st.var / st.n) if st.n else 1.0
        if se <= 1e-9:
            return m < 0
        z = -m / se
        p_neg = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return p_neg > prob

    def to_dict(self) -> dict:
        return {"alpha": self.alpha, "prior_mean": self.prior_mean, "stats": {k: v.to_dict() for k, v in self.stats.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "HierarchicalRate":
        h = cls(float(d.get("alpha", 10.0)), float(d.get("prior_mean", 0.5)))
        h.stats = {k: RateStat.from_dict(v) for k, v in (d.get("stats") or {}).items()}
        return h
