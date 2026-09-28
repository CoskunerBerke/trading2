"""ÖĞRENME MODU — TABAN ÖĞRENİCİ GÖRÜNÜMÜ (2026-09-28, öğrenme modu; üçüncü doğrulama turu).

Sorun: ana botun ekonomi kapısı (NEGATIVE_NET_EDGE / RESEARCH_SIZE_ONLY) p_win ve gerçekleşmiş beklentiyi öğreniciden okur.
Öğrenmede öğrenme-ekstra işlemlerin sonuçları da öğreniciye girer; p_win düşer ve TABANIN (öğrenme kapalı) açacağı aday
"öğrenme-ekstra" sayılıp politika rezervinin dışında kalıyordu (uçtan uca koşu: tabanın 48 turdaki tek ana işlemi UNI
16:10, öğrenme defterinde MIN_ORDER_CONFLICT/MARGIN ile açılamadı; p_win 0,5 → 0,142).

Çözüm: `learning_unlocked_by` etiketi ve politika rezervi kararı için ekonomi kapısı bir TABAN öğrenici görünümüyle yeniden
değerlendirilir. Görünüm öğrenmenin İLK aktif olduğu anda gerçek öğreniciden kopyalanır (o ana kadar yalnız taban
kapanışları var) ve sonra yalnız öğrenme-ekstra OLMAYAN kapanışlarla güncellenir:

* `win` / `exp_r` — `LearnerV2`nin hiyerarşik kazanma oranı ve beklenti R'si (`learner_v2.outcome_keys/add_outcome`, AYNI kural);
* `v1` — eski lojistik öğrenicinin (`learning.Learner`) ağırlıkları, AYNI SGD adımı ve ısınma karışımı.

p_win: şampiyon model varsa `p = w·p_model_kalibre + (1 − w)·önsel` (model ve kalibratör elle terfi eder → ortak), yoksa
`0,5·önsel + 0,5·v1`; önsel/v1 bu görünümden. YAKLAŞIK: ajan ağırlıkları, etki katmanı (PAPER_BOUNDED) ve tabanın öğrenme
defterinde hiç açılmamış işlemleri görünüme girmez; öğrenme KAPALIYKEN (enabled: false) kapanışlar işlenmez.

Kalıcılık: `state/learning_policy_basis.json` — yalnız öğrenme etkinken yazılır (kapalı yol ve eski kod dokunmaz).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .core import atomic_write_json, iso, read_json, utc_now
from .learn.labels import label_outcome
from .learn.learner_v2 import add_outcome, outcome_keys
from .learn.model import HierarchicalRate
from .learning import FEATURES as _V1_FEATURES, _sigmoid

log = logging.getLogger(__name__)

BASIS_FILE = "learning_policy_basis.json"
SCHEMA_VERSION = "learning_policy_basis_v1"
#: `policy_basis` kaydı: ekonomi kapısı hangi öğreniciyle etiketlendi.
BASIS_POLICY = "POLICY_LEARNER"          # taban öğrenici görünümü (bu modül)
BASIS_LEARNING = "LEARNING_LEARNER"      # görünüm yok → öğrenme dünyasının kararı (eski davranış)
#: Öğreniciye bağlı ekonomi kodları (politika etiketinde taban görünümüyle yeniden hesaplanır).
ECONOMICS_CODES = ("NEGATIVE_NET_EDGE", "RESEARCH_SIZE_ONLY")


def is_learning_extra(rec: dict[str, Any] | None) -> bool:
    """Kapanan işlem öğrenme-ekstra mı (`features.learning.learning_unlocked_by` dolu)? Etiketsiz (taban/askıda) → False."""
    lr = ((rec or {}).get("features") or {}).get("learning")
    return bool(isinstance(lr, dict) and lr.get("learning_unlocked_by"))


def economics_codes(opp: dict[str, Any] | None) -> list[str]:
    """Ekonomi kapısının (sert kodsuz) hükmü → `learning_unlocked_by` kodu: işlenebilir → [], yalnız araştırma →
    RESEARCH_SIZE_ONLY, değilse NEGATIVE_NET_EDGE (motorun ekonomi dalıyla AYNI sıra)."""
    o = opp or {}
    if not o or o.get("tradeable"):
        return []
    return ["RESEARCH_SIZE_ONLY"] if o.get("research_only") else ["NEGATIVE_NET_EDGE"]


class PolicyBasis:
    """Taban öğrenici görünümü (`win`, `exp_r`, `v1`). `win`/`exp_r` özniteliği olduğu için `economics_gate.assess_one`
    (`hierarchical_expectancy`) buna doğrudan `learner` olarak verilir."""

    def __init__(self, path: Path | str, *, win: HierarchicalRate, exp_r: HierarchicalRate, v1: dict[str, Any],
                 seeded_at: str, n_policy: int = 0, n_extra: int = 0, updated_at: str | None = None):
        self.path = Path(path)
        self.win = win
        self.exp_r = exp_r
        self.v1 = v1
        self.seeded_at = seeded_at
        self.n_policy = int(n_policy)
        self.n_extra = int(n_extra)
        self.updated_at = updated_at or seeded_at

    # ------------------------------------------------------------ kuruluş
    @classmethod
    def seed(cls, path: Path | str, *, learner2: Any, learner1: Any, now: Any = None) -> "PolicyBasis":
        """Gerçek öğrenicilerin O ANKİ durumunun kopyası (öğrenmenin ilk aktif anı: yalnız taban kapanışları)."""
        st = getattr(learner1, "state", None)
        v1 = {"weights": {k: float(((st.weights or {}) if st is not None else {}).get(k, 0.0) or 0.0) for k in _V1_FEATURES},
              "bias": float(getattr(st, "bias", 0.0) or 0.0), "n_trades": int(getattr(st, "n_trades", 0) or 0),
              "lr": float(getattr(st, "lr", 0.05) or 0.05), "l2": float(getattr(st, "l2", 0.001) or 0.0),
              "min_trades": int(getattr(learner1, "min_trades", 20) or 20)}
        at = iso(now or utc_now())
        b = cls(path, win=HierarchicalRate.from_dict(learner2.win.to_dict()),
                exp_r=HierarchicalRate.from_dict(learner2.exp_r.to_dict()), v1=v1, seeded_at=at)
        b.save()
        return b

    @classmethod
    def load(cls, path: Path | str) -> "PolicyBasis | None":
        d = read_json(Path(path), default=None)
        if not isinstance(d, dict) or d.get("schema_version") != SCHEMA_VERSION:
            return None
        try:
            return cls(path, win=HierarchicalRate.from_dict(d.get("win") or {}),
                       exp_r=HierarchicalRate.from_dict(d.get("exp_r") or {}), v1=dict(d.get("v1") or {}),
                       seeded_at=str(d.get("seeded_at") or ""), n_policy=int(d.get("n_policy") or 0),
                       n_extra=int(d.get("n_extra") or 0), updated_at=d.get("updated_at"))
        except (TypeError, ValueError, AttributeError) as exc:
            log.warning("taban öğrenici görünümü okunamadı (%s): %s", path, exc)
            return None

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "seeded_at": self.seeded_at, "updated_at": self.updated_at,
                "n_policy": self.n_policy, "n_extra": self.n_extra, "win": self.win.to_dict(),
                "exp_r": self.exp_r.to_dict(), "v1": dict(self.v1)}

    def save(self) -> None:
        atomic_write_json(self.path, self.to_dict())

    def status(self) -> dict[str, Any]:
        return {"seeded_at": self.seeded_at, "updated_at": self.updated_at, "n_policy": self.n_policy,
                "n_extra": self.n_extra, "v1_n_trades": int(self.v1.get("n_trades") or 0)}

    # ------------------------------------------------------------ güncelleme
    def observe(self, rec: dict[str, Any], decision_snapshot: dict | None = None) -> bool:
        """Kapanan işlem: öğrenme-ekstra DEĞİLSE görünüme eklenir (LearnerV2 + v1 ile AYNI kural). Döner: eklendi mi."""
        if is_learning_extra(rec):
            self.n_extra += 1
            self.updated_at = iso(utc_now())
            self.save()
            return False
        lab = label_outcome(rec)
        regime, symbol, setup, side = outcome_keys(rec, decision_snapshot)
        add_outcome(self.win, self.exp_r, lab, regime=regime, symbol=symbol, setup=setup, side=side)
        self._v1_learn(rec)
        self.n_policy += 1
        self.updated_at = iso(utc_now())
        self.save()
        return True

    @staticmethod
    def _v1_x(f: dict[str, Any]) -> dict[str, float]:
        return {k: float((f or {}).get(k, 0.0) or 0.0) for k in _V1_FEATURES}      # `Learner._vec` ile aynı

    def _v1_learn(self, rec: dict[str, Any]) -> None:
        """`learning.Learner.learn`in lojistik SGD adımı (yan etkisiz; dersler/istatistikler yok)."""
        w = self.v1.setdefault("weights", {})
        x = self._v1_x(rec.get("features") or {})
        for k in x:
            w.setdefault(k, 0.0)
        y = 1.0 if float(rec.get("pnl", 0.0) or 0.0) > 0 else 0.0
        lr, l2 = float(self.v1.get("lr", 0.05)), float(self.v1.get("l2", 0.001))
        p = _sigmoid(float(self.v1.get("bias", 0.0)) + sum(float(w[k]) * v for k, v in x.items()))
        g = p - y
        for k, v in x.items():
            w[k] = float(w[k]) - lr * (g * v + l2 * float(w[k]))
        self.v1["bias"] = float(self.v1.get("bias", 0.0)) - lr * g
        self.v1["n_trades"] = int(self.v1.get("n_trades") or 0) + 1

    # ------------------------------------------------------------ tahmin
    def v1_predict(self, f: dict[str, Any]) -> float:
        """`learning.Learner.predict` ile AYNI (ısınmada 0,5'e karışım)."""
        w = self.v1.get("weights") or {}
        z = float(self.v1.get("bias", 0.0)) + sum(float(w.get(k, 0.0)) * v for k, v in self._v1_x(f).items())
        p = _sigmoid(z)
        n, mt = int(self.v1.get("n_trades") or 0), int(self.v1.get("min_trades") or 20)
        if n < mt:
            lam = n / mt
            return 0.5 * (1 - lam) + p * lam
        return p

    def p_win(self, pr: Any, f: dict[str, Any], *, regime: str | None, symbol: str | None, setup: str | None,
              prior_blend_n: float) -> float:
        """Taban p_win: motorun `baseline_p_win` formülü, önsel ve v1 bu görünümden (model/kalibratör ortak)."""
        leaf = f"{symbol}|{setup}" if symbol and setup else (symbol or setup)
        prior, _n = self.win.estimate(regime=regime, leaf=leaf)
        if pr is not None and bool(getattr(pr, "ready", False)):
            n_tr = float(getattr(pr, "n_train", 0) or 0)
            w = n_tr / (n_tr + float(prior_blend_n)) if (n_tr + float(prior_blend_n)) > 0 else 0.0
            p = float(pr.p_win_calibrated) + (1.0 - w) * (prior - float(pr.prior_used))
        else:
            p = 0.5 * prior + 0.5 * self.v1_predict(f)
        return round(max(0.0, min(1.0, p)), 3)


__all__ = ["BASIS_FILE", "BASIS_LEARNING", "BASIS_POLICY", "ECONOMICS_CODES", "PolicyBasis", "economics_codes",
           "is_learning_extra"]
