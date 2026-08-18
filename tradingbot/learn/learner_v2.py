"""LearnerV2 — hafıza + hiyerarşik istatistik + kalibre model + registry orkestrasyonu.

* predict: hiyerarşik önsel ile model tahminini n_eff'e göre harmanlar (ani geçiş yok).
* on_trade_closed: etiket + postmortem + hafıza + istatistik güncelle (state/learn_v2.json atomik).
* train_challenger: zaman bazlı holdout ile model eğit, kalibre et, CANDIDATE olarak kaydet.
* maybe_promote: PAPER'da kapı geçilirse otomatik; diğer modlarda manuel.
* legacy_bridge: v1 Learner durumunu kayıpsız içeri alır.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..core import atomic_write_json, from_iso, iso, read_json, utc_now
from .calibration import Calibrator, calibration_metrics
from .features import FEATURE_VERSION, build_features, feature_names, to_vector
from .labels import label_outcome
from .memory import TradeMemory
from .model import HierarchicalRate, LogisticModel, recency_weights
from .postmortem import structured_postmortem
from .registry import ModelRegistry, drift_check


@dataclass
class LearnConfig:
    min_samples_train: int = 40
    holdout_frac: float = 0.2
    half_life_days: float = 60.0
    l2: float = 0.05
    alpha_shrink: float = 10.0
    prior_blend_n: float = 30.0          # n_eff bu değere ulaşınca model ağırlığı ~0.5
    calibrator: str = "platt"
    include_time_features: bool = False


@dataclass
class PredictionResult:
    p_win: float
    p_win_calibrated: float
    model_id: str | None
    n_train: int
    ready: bool
    prior_used: float
    prior_n_eff: float

    def to_dict(self) -> dict:
        return asdict(self)


class LearnerV2:
    def __init__(self, memory: TradeMemory, registry: ModelRegistry, cfg: LearnConfig | None = None, state_path: Path | str | None = None):
        self.memory = memory
        self.registry = registry
        self.cfg = cfg or LearnConfig()
        self.state_path = Path(state_path) if state_path else None
        self.win = HierarchicalRate(self.cfg.alpha_shrink, 0.5)       # kazanma oranı
        self.exp_r = HierarchicalRate(self.cfg.alpha_shrink, 0.0)     # beklenti R
        self.agent_hit = HierarchicalRate(self.cfg.alpha_shrink, 0.5)  # leaf = ajan
        self.n_closed = 0
        self.lessons: list[dict] = []
        self.calibrator = Calibrator(self.cfg.calibrator)
        self.last_metrics: dict = {}
        self.baseline_metrics: dict = {}
        if self.state_path:
            self._load()

    # ------------------------------------------------------------ kalıcılık
    def _load(self) -> None:
        d = read_json(self.state_path, default=None)
        if not isinstance(d, dict):
            return
        self.win = HierarchicalRate.from_dict(d.get("win", {})) if d.get("win") else self.win
        self.exp_r = HierarchicalRate.from_dict(d.get("exp_r", {})) if d.get("exp_r") else self.exp_r
        self.agent_hit = HierarchicalRate.from_dict(d.get("agent_hit", {})) if d.get("agent_hit") else self.agent_hit
        self.n_closed = int(d.get("n_closed", 0))
        self.lessons = list(d.get("lessons", []))
        self.calibrator = Calibrator.from_dict(d.get("calibrator", {})) if d.get("calibrator") else self.calibrator
        self.last_metrics = dict(d.get("last_metrics", {}))
        self.baseline_metrics = dict(d.get("baseline_metrics", {}))

    def save(self) -> None:
        if self.state_path:
            atomic_write_json(self.state_path, {"schema_version": 2, "updated_at": iso(), "win": self.win.to_dict(), "exp_r": self.exp_r.to_dict(),
                                                "agent_hit": self.agent_hit.to_dict(), "n_closed": self.n_closed, "lessons": self.lessons[-500:],
                                                "calibrator": self.calibrator.to_dict(), "last_metrics": self.last_metrics,
                                                "baseline_metrics": self.baseline_metrics}, keep_backup=True)

    # ------------------------------------------------------------ tahmin
    def _champion_model(self) -> tuple[LogisticModel | None, str | None]:
        ch = self.registry.champion("p_win_lr")
        if not ch:
            return None, None
        return LogisticModel.from_dict(ch["params"]), ch["id"]

    def predict(self, features: dict[str, Any], *, regime: str | None = None, symbol: str | None = None, setup: str | None = None) -> PredictionResult:
        leaf = f"{symbol}|{setup}" if symbol and setup else (symbol or setup)
        prior, n_eff = self.win.estimate(regime=regime, leaf=leaf)
        model, mid = self._champion_model()
        if model is None:
            return PredictionResult(round(prior, 4), round(prior, 4), None, 0, False, round(prior, 4), n_eff)
        x = np.array(to_vector(build_features(features, include_time_features=self.cfg.include_time_features), model.feature_names), float)
        p_model = float(model.predict_proba(x)[0])
        p_cal = float(self.calibrator.apply([p_model])[0]) if self.calibrator.n_fit else p_model
        w = model.n_train / (model.n_train + self.cfg.prior_blend_n)   # veri arttıkça model ağırlığı artar
        p = w * p_cal + (1 - w) * prior
        return PredictionResult(round(p_model, 4), round(p, 4), mid, model.n_train, True, round(prior, 4), n_eff)

    def is_blacklisted(self, setup: str, direction: str, regime: str | None = None) -> bool:
        return self.exp_r.is_negative_with_evidence(leaf=f"{setup}|{direction}", regime=regime)

    # ------------------------------------------------------------ öğrenme
    def on_trade_closed(self, rec: dict[str, Any], decision_snapshot: dict | None = None, price_path: list[dict] | None = None) -> dict:
        lab = label_outcome(rec)
        pm = structured_postmortem(rec, decision_snapshot)
        f = rec.get("features") or {}
        regime = str((decision_snapshot or {}).get("regime") or f.get("regime") or "")
        symbol, setup, side = str(rec.get("symbol", "")), str(rec.get("setup_type", f.get("setup_type", "-"))), str(rec.get("side", f.get("direction", "")))
        won = 1.0 if lab["won"] else 0.0
        self.win.add(won, regime=regime or None, leaf=f"{symbol}|{setup}")
        self.win.add(won, regime=regime or None, leaf=symbol)
        self.exp_r.add(lab["r_multiple"], regime=regime or None, leaf=f"{setup}|{side}")
        for a in pm.agents_right:
            self.agent_hit.add(1.0, regime=regime or None, leaf=a)
        for a in pm.agents_wrong:
            self.agent_hit.add(0.0, regime=regime or None, leaf=a)
        self.n_closed += 1
        lesson = {"id": rec.get("id"), "symbol": symbol, "side": side, "r": lab["r_multiple"], "won": lab["won"], "exit": rec.get("exit_reason"),
                  "at": rec.get("closed_at"), "codes": pm.lesson_codes, "why": pm.lesson_text_tr, "setup": setup, "regime": regime}
        self.lessons.append(lesson)
        self.memory.record_exit(str(rec.get("id")), {**rec, **lab}, price_path, pm.to_dict())
        self.save()
        return lesson

    def train_challenger(self, *, now: Any | None = None) -> dict | None:
        rows = self.memory.trades(closed_only=True)
        n = len(rows)
        if n < self.cfg.min_samples_train:
            return None
        names = feature_names(FEATURE_VERSION, self.cfg.include_time_features)
        X = np.array([to_vector(build_features(r.get("features") or r, include_time_features=self.cfg.include_time_features), names) for r in rows], float)
        y = np.array([1.0 if float((r.get("outcome") or {}).get("r_multiple", 0) or 0) > 0.25 else 0.0 for r in rows])
        now = now or utc_now()
        ages = []
        for r in rows:
            try:
                ages.append((now - from_iso(str(r.get("recorded_at")))).total_seconds() / 86400)
            except (ValueError, TypeError):
                ages.append(0.0)
        w = recency_weights(np.array(ages), self.cfg.half_life_days)
        cut = max(1, int(n * (1 - self.cfg.holdout_frac)))
        model = LogisticModel(feature_names=names, l2=self.cfg.l2).fit(X[:cut], y[:cut], w[:cut])
        p_hold = model.predict_proba(X[cut:]) if cut < n else np.array([])
        cal = Calibrator(self.cfg.calibrator)
        if len(p_hold) >= 10:
            cal.fit(p_hold, y[cut:])
            p_hold_c = cal.apply(p_hold)
        else:
            p_hold_c = p_hold
        met = calibration_metrics(p_hold_c, y[cut:]) if len(p_hold) else {"n": 0, "brier": 1.0, "log_loss": 9.0, "ece": 1.0}
        r_hold = [float((r.get("outcome") or {}).get("r_multiple", 0) or 0) for r in rows[cut:]]
        gated = [rr for rr, p in zip(r_hold, p_hold_c) if p >= 0.5]
        met.update({"n_holdout": int(len(p_hold)), "expectancy_r": round(float(np.mean(gated)), 4) if gated else 0.0,
                    "hit_rate": round(float(np.mean([(p >= 0.5) == (yy > 0.5) for p, yy in zip(p_hold_c, y[cut:])])), 4) if len(p_hold) else 0.0,
                    "feature_means": {k: round(float(v), 4) for k, v in zip(names, X.mean(axis=0))}})
        params = model.to_dict() | {"calibrator": cal.to_dict()}
        mid = self.registry.register("p_win_lr", params, met, "CANDIDATE", note=f"train {cut} / holdout {n - cut}")
        self.last_metrics = met
        if not self.baseline_metrics:
            self.baseline_metrics = dict(met)
        self.save()
        return {"model_id": mid, "metrics": met, "n_train": cut, "n_holdout": n - cut}

    def maybe_promote(self, mode: str, operator: str = "auto", *, manual: bool = False) -> tuple[bool, list[str]]:
        cand = self.registry.challenger("p_win_lr")
        if not cand:
            return False, ["challenger yok"]
        ok, reasons = self.registry.promote(cand["id"], operator=operator, mode=mode, manual=manual)
        if ok:
            self.calibrator = Calibrator.from_dict((cand.get("params") or {}).get("calibrator", {}))
            self.save()
        return ok, reasons

    def drift(self) -> dict:
        if not self.last_metrics or not self.baseline_metrics:
            return {"drifted": False, "signals": [], "details": {}}
        return drift_check(self.last_metrics, self.baseline_metrics).to_dict()

    # ------------------------------------------------------------ özet / köprü
    def snapshot(self) -> dict:
        champ = self.registry.champion("p_win_lr")
        chal = self.registry.challenger("p_win_lr")
        agents = {}
        for k, st in self.agent_hit.stats.items():
            if k.startswith("leaf:") and "|" not in k:
                a = k.split(":", 1)[1]
                m, n = self.agent_hit.estimate(leaf=a)
                agents[a] = {"hit_rate_shrunk": round(m, 3), "n": int(st.n)}
        setups = {}
        for k, st in self.exp_r.stats.items():
            if k.startswith("leaf:") and "regime" not in k:
                leaf = k.split(":", 1)[1]
                m, n = self.exp_r.estimate(leaf=leaf)
                setups[leaf] = {"n": int(st.n), "exp_r_shrunk": round(m, 3), "blacklisted": self.exp_r.is_negative_with_evidence(leaf=leaf)}
        g = self.win.stats.get("")
        return {"n_closed": self.n_closed, "win_rate_global": round(g.mean, 3) if g and g.n else None, "agents": agents, "setups": setups,
                "champion": champ["id"] if champ else None, "challenger": chal["id"] if chal else None,
                "champion_metrics": (champ or {}).get("metrics"), "last_metrics": self.last_metrics, "drift": self.drift(),
                "calibrator": self.calibrator.kind if self.calibrator.n_fit else "none", "feature_version": FEATURE_VERSION,
                "recent_lessons": self.lessons[-10:]}

    def legacy_bridge(self, learner_v1: Any) -> dict:
        """tradingbot.learning.Learner durumunu içeri al: agent_hits, setup_stats, lessons, n_trades (kayıpsız, tekrar çağrılabilir değil)."""
        s = learner_v1.state
        imported = {"agents": 0, "setups": 0, "lessons": 0}
        for a, (h, n) in (s.agent_hits or {}).items():
            for _ in range(int(h)):
                self.agent_hit.add(1.0, leaf=a)
            for _ in range(int(n) - int(h)):
                self.agent_hit.add(0.0, leaf=a)
            imported["agents"] += 1
        for key, v in (s.setup_stats or {}).items():
            n, wins, sum_r = int(v.get("n", 0)), int(v.get("wins", 0)), float(v.get("sum_r", 0.0))
            avg = sum_r / n if n else 0.0
            for _ in range(n):
                self.exp_r.add(avg, leaf=key)
            for _ in range(wins):
                self.win.add(1.0, leaf=key)
            for _ in range(n - wins):
                self.win.add(0.0, leaf=key)
            imported["setups"] += 1
        for l in (s.lessons or []):
            self.lessons.append({"id": l.get("id"), "symbol": l.get("symbol"), "side": l.get("side"), "r": l.get("r"), "won": l.get("won"),
                                 "exit": l.get("exit"), "at": l.get("at"), "codes": ["LEGACY"], "why": l.get("why", []), "setup": l.get("setup"), "regime": ""})
            imported["lessons"] += 1
        self.n_closed = max(self.n_closed, int(s.n_trades or 0))
        self.save()
        return imported
