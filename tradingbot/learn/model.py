"""İstatistiksel model katmanı (numpy): train-only StandardScaler, L2 lojistik regresyon (batch GD), hiyerarşik Beta shrinkage."""
from __future__ import annotations

import math
from collections.abc import Iterable
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

    def add_weighted(self, x: float, w: float) -> None:
        """Yenilik ağırlıklı ekleme: n etkin örnek (Σw), s = Σw·x."""
        self.n += w
        self.s += w * x
        self.ss += w * x * x

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
        n = d.get("n", 0)
        return cls(int(n) if float(n) == int(float(n)) else float(n), float(d.get("s", 0.0)), float(d.get("ss", 0.0)))


class HierarchicalRate:
    """global → regime → symbol/setup posterior ortalaması: (s + α·μ_parent) / (n + α). Az verili yaprakta ebeveyne çekilir."""

    def __init__(self, alpha: float = 10.0, prior_mean: float = 0.5, half_life_days: float | None = None):
        self.alpha = alpha
        self.prior_mean = prior_mean
        self.half_life_days = half_life_days      # None → ağırlıksız; sayı → yenilik ağırlığı (w = 0.5^(yaş_gün/half_life))
        self.stats: dict[str, RateStat] = {}     # anahtar: "" | "market:M" | "cluster:C" | "regime:X" | "leaf:Y" | "regime:X|leaf:Y" (+ market/cluster kombinasyonları)

    @staticmethod
    def _keys(*, regime=None, leaf=None, market=None, cluster=None) -> list[str]:
        keys = [""]
        if market:
            keys.append(f"market:{market}")
        if cluster:
            keys.append(f"cluster:{cluster}")
        if regime:
            keys.append(f"regime:{regime}")
        if leaf:
            keys.append(f"leaf:{leaf}")
        if regime and leaf:
            keys.append(f"regime:{regime}|leaf:{leaf}")
        return keys

    @classmethod
    def _keys_multi(cls, *, regime=None, leaves=(), market=None, cluster=None) -> list[str]:
        """TEK gozlem icin anahtar kumesi — ATA dugumler BIR KEZ, her yaprak BIR KEZ.

        Bir gozlem birden cok yaprak granulerliginde (`SYM|setup` ve `SYM`) sayilabilir; bu
        MESRUdur. Fakat her `add` cagrisi ortak atalari (`""`, `regime:X`) da yazdigi icin,
        ayni gozlem icin iki ayri cagri atalari IKI KEZ sayardi. Bu yardimci, atalari tek
        sefer uretir ve anahtarlari SIRA KORUYARAK tekillestirir.
        """
        seen: dict[str, None] = {}
        for k in cls._keys(regime=regime, leaf=None, market=market, cluster=cluster):
            seen.setdefault(k, None)
        for leaf in leaves:
            if not leaf:
                continue
            seen.setdefault(f"leaf:{leaf}", None)
            if regime:
                seen.setdefault(f"regime:{regime}|leaf:{leaf}", None)
        return list(seen)

    def add(self, x: float, *, regime: str | None = None, leaf: str | None = None,
            leaves: Iterable[str] | None = None, market: str | None = None, cluster: str | None = None,
            age_days: float = 0.0) -> None:
        """Tek gozlemi hiyerarsiye ekler.

        `leaf` tek yaprak (geriye uyumlu); `leaves` AYNI gozlemin birden cok yaprak
        granulerligi — atalar yine YALNIZ BIR KEZ sayilir. Ikisi birlikte verilebilir.
        """
        w = 1.0
        if self.half_life_days and age_days > 0:
            w = 0.5 ** (float(age_days) / float(self.half_life_days))
        allleaves = ([leaf] if leaf else []) + [str(v) for v in (leaves or []) if v]
        for k in self._keys_multi(regime=regime, leaves=allleaves, market=market, cluster=cluster):
            self.stats.setdefault(k, RateStat()).add_weighted(x, w)

    def _post(self, key: str, parent_mean: float) -> tuple[float, float]:
        st = self.stats.get(key)
        if not st or st.n == 0:
            return parent_mean, 0.0
        return (st.s + self.alpha * parent_mean) / (st.n + self.alpha), float(st.n)

    def estimate(self, *, regime: str | None = None, leaf: str | None = None, market: str | None = None, cluster: str | None = None) -> tuple[float, float]:
        """→ (posterior mean, n_eff). Sıra: global → market → cluster → regime → leaf → regime|leaf (her seviye ebeveyne çekilir)."""
        g, n = self._post("", self.prior_mean)
        m = g
        if market:
            m, n = self._post(f"market:{market}", m)
        if cluster:
            m, n = self._post(f"cluster:{cluster}", m)
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
        return {"alpha": self.alpha, "prior_mean": self.prior_mean, "half_life_days": self.half_life_days, "stats": {k: v.to_dict() for k, v in self.stats.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "HierarchicalRate":
        h = cls(float(d.get("alpha", 10.0)), float(d.get("prior_mean", 0.5)), d.get("half_life_days"))
        h.stats = {k: RateStat.from_dict(v) for k, v in (d.get("stats") or {}).items()}
        return h


# ---------------------------------------------------------------- kayıp büyüklüğü (|R|) kestirimi
#: Plan geometrisi: stop TAM 1R'dir. Veri yokken kestirim BUDUR (bugünkü davranışın aynısı).
LOSS_PRIOR_R = 1.0
#: Gerçek ortalama kayıp büyüklüğünün 1.0 etrafındaki ÖNSEL standart sapması. Sapmanın kaynağı
#: yalnız uygulamadır (gap-through, stop kayması, fee/funding sürüklemesi), dolayısıyla küçüktür:
#: 2σ ≈ ±0.10R. Bu sayı ne kadar KÜÇÜKse kestirim 1.0'a o kadar yapışır (muhafazakâr yön).
LOSS_PRIOR_SD_R = 0.05
#: Gözlem başına standart sapmanın TABANI. Küçük örneklemde ölçülen s.s. tesadüfen 0'a yakın
#: çıkabilir (n=1'de tanımı gereği 0'dır); taban olmadan tek bir kapanış kestirimi ele geçirirdi.
MIN_LOSS_SD_R = 0.10
#: Bozuk/absürt duruma karşı üst sınır (fail-safe). Kestirim ASLA 1.0'ın ALTINA inmez: NET R zaten
#: fee/funding sürüklemesini içerdiği için beklenen kayıp büyüklüğü yapısal olarak ≥ 1R'dir; 1.0'ın
#: altındaki bir örneklem ortalaması küçük örneklem artefaktıdır ve edge'i ŞİŞİRİRDİ.
MAX_LOSS_R = 2.0


def loss_magnitude_estimate(n: float, mean_abs_r: float, sd_abs_r: float, *,
                            prior_r: float = LOSS_PRIOR_R, prior_sd_r: float = LOSS_PRIOR_SD_R,
                            min_sd_r: float = MIN_LOSS_SD_R, max_r: float = MAX_LOSS_R) -> tuple[float, dict]:
    """Gerçekleşmiş kayıp büyüklüklerinden belirsizlik-ayarlı |avg_loss_r| kestirimi.

    Ampirik Bayes daralması — `BLEND_N` kazanç harmanıyla AYNI biçim (`w = n / (n + k)`), ama `k`
    elle seçilmiş bir sabit değil, ÖLÇÜLEN belirsizlikten türetilir::

        k = max(sd, min_sd)² / prior_sd²          (gözlem gürültüsü / önsel yayılım)
        w = n / (n + k)                           (= prior_sd² / (prior_sd² + sd²/n))
        L = prior_r + w · max(0, mean − prior_r)

    Böylece: (a) veri yokken `n = 0 → w = 0 → L = prior_r` yani BUGÜNKÜ davranış birebir korunur;
    (b) örneklem büyüdükçe `w` tekdüze artar; (c) kayıplar gerçekten dağınıksa (`sd` büyük) `k`
    büyür ve kestirim 1.0'a daha çok yapışır — yani belirsizlik kestirimi ŞİŞİRMEZ, kırpar.

    `max(0, ...)` tek yönlüdür: kestirim 1.0'ın altına DÜŞEMEZ (bkz. `MAX_LOSS_R` notu).
    Dönüş: `(L, meta)` — `meta` denetim için ara terimleri taşır.
    """
    n = max(0.0, float(n or 0.0))
    m = float(mean_abs_r or 0.0)
    sd = max(0.0, float(sd_abs_r or 0.0))
    sd_eff = max(sd, float(min_sd_r))
    tau = max(1e-9, float(prior_sd_r))
    k = (sd_eff * sd_eff) / (tau * tau)
    w = n / (n + k) if n > 0 else 0.0
    raw = float(prior_r) + w * max(0.0, m - float(prior_r))
    est = min(float(max_r), max(float(prior_r), raw))
    return round(est, 6), {"n": round(n, 6), "mean_abs_r": round(m, 6), "sd_abs_r": round(sd, 6),
                           "sd_eff_r": round(sd_eff, 6), "shrink_k": round(k, 6),
                           "shrink_w": round(w, 6), "prior_r": float(prior_r),
                           "clamped": bool(abs(raw - est) > 1e-12)}
