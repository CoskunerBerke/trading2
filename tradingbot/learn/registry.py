"""Model registry — CHAMPION / CHALLENGER; terfi kapısı; PAPER dışı modlarda manuel terfi; drift kontrolü."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core import atomic_write_json, iso, new_id, read_json, utc_now


@dataclass
class PromotionThresholds:
    min_holdout: int = 30
    max_ece: float = 0.15
    expectancy_tolerance_r: float = 0.05
    require_logloss_improve: bool = True
    require_brier_improve: bool = True


def promotion_gate(champion_metrics: dict | None, challenger_metrics: dict, th: PromotionThresholds | None = None) -> tuple[bool, list[str]]:
    th = th or PromotionThresholds()
    reasons: list[str] = []
    n = int(challenger_metrics.get("n_holdout", challenger_metrics.get("n", 0)) or 0)
    if n < th.min_holdout:
        reasons.append(f"holdout {n} < {th.min_holdout}")
    if float(challenger_metrics.get("ece", 1.0)) > th.max_ece:
        reasons.append(f"ece {challenger_metrics.get('ece')} > {th.max_ece}")
    if champion_metrics:
        if th.require_logloss_improve and float(challenger_metrics.get("log_loss", 9)) >= float(champion_metrics.get("log_loss", 9)):
            reasons.append("log_loss iyileşmedi")
        if th.require_brier_improve and float(challenger_metrics.get("brier", 9)) >= float(champion_metrics.get("brier", 9)):
            reasons.append("brier iyileşmedi")
        if float(challenger_metrics.get("expectancy_r", 0)) < float(champion_metrics.get("expectancy_r", 0)) - th.expectancy_tolerance_r:
            reasons.append("beklenti (R) şampiyonun altında")
    return (not reasons), reasons


@dataclass
class DriftReport:
    drifted: bool
    signals: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def drift_check(recent: dict, baseline: dict, *, logloss_tol: float = 0.10, brier_tol: float = 0.05, hit_tol: float = 0.15, psi_tol: float = 0.25) -> DriftReport:
    sig: list[str] = []
    det: dict[str, Any] = {}
    for k, tol in (("log_loss", logloss_tol), ("brier", brier_tol)):
        if k in recent and k in baseline:
            d = float(recent[k]) - float(baseline[k])
            det[k] = round(d, 4)
            if d > tol:
                sig.append(f"{k} +{d:.3f}")
    if "hit_rate" in recent and "hit_rate" in baseline:
        d = float(baseline["hit_rate"]) - float(recent["hit_rate"])
        det["hit_rate_drop"] = round(d, 4)
        if d > hit_tol:
            sig.append(f"hit_rate −{d:.2f}")
    fm_r, fm_b = recent.get("feature_means") or {}, baseline.get("feature_means") or {}
    psi = 0.0
    for k in set(fm_r) & set(fm_b):
        b = float(fm_b[k]); r = float(fm_r[k])
        psi += abs(r - b) / (abs(b) + 1e-6) if abs(b) > 1e-6 else abs(r - b)
    if fm_r and fm_b:
        psi /= max(1, len(set(fm_r) & set(fm_b)))
        det["psi_proxy"] = round(psi, 4)
        if psi > psi_tol:
            sig.append(f"feature shift {psi:.2f}")
    return DriftReport(bool(sig), sig, det)


class ModelRegistry:
    def __init__(self, path: Path | str, db_hook: Callable[[str, dict], Any] | None = None, thresholds: PromotionThresholds | None = None):
        self.path = Path(path)
        self.db_hook = db_hook
        self.th = thresholds or PromotionThresholds()
        d = read_json(self.path, default={"models": [], "events": []})
        self.models: list[dict] = list(d.get("models", []))
        self.events: list[dict] = list(d.get("events", []))

    def save(self) -> None:
        atomic_write_json(self.path, {"models": self.models[-500:], "events": self.events[-500:]})

    def register(self, kind: str, params: dict, metrics: dict, status: str = "CANDIDATE", note: str = "") -> str:
        mid = new_id("model")
        row = {"id": mid, "kind": kind, "status": status, "params": params, "metrics": metrics, "created_at": iso(utc_now()), "note": note}
        self.models.append(row)
        if self.db_hook:
            self.db_hook("model_versions", {"id": mid, "kind": kind, "version": mid, "status": status, "n_train": int(params.get("n_train", 0) or 0), "metrics": metrics, "params": {k: v for k, v in params.items() if k != "weights"}})
        self.save()
        return mid

    def get(self, mid: str) -> dict | None:
        return next((m for m in self.models if m["id"] == mid), None)

    def champion(self, kind: str) -> dict | None:
        return next((m for m in reversed(self.models) if m["kind"] == kind and m["status"] == "CHAMPION"), None)

    def challenger(self, kind: str) -> dict | None:
        return next((m for m in reversed(self.models) if m["kind"] == kind and m["status"] == "CANDIDATE"), None)

    def promote(self, mid: str, *, operator: str, mode: str, manual: bool = False, force: bool = False) -> tuple[bool, list[str]]:
        m = self.get(mid)
        if not m:
            return False, ["model yok"]
        mode = (mode or "PAPER").upper()
        if mode != "PAPER" and not manual:
            return False, [f"{mode} modunda otomatik terfi yasak — manuel onay gerekli"]
        champ = self.champion(m["kind"])
        ok, reasons = promotion_gate(champ["metrics"] if champ else None, m["metrics"], self.th)
        if not ok and not force:
            self.events.append({"at": iso(), "action": "PROMOTE_REJECTED", "model_id": mid, "operator": operator, "mode": mode, "reasons": reasons})
            self.save()
            return False, reasons
        if champ:
            champ["status"] = "RETIRED"
        m["status"] = "CHAMPION"
        self.events.append({"at": iso(), "action": "PROMOTED", "model_id": mid, "operator": operator, "mode": mode, "manual": manual, "forced": force,
                            "previous": champ["id"] if champ else None})
        if self.db_hook:
            self.db_hook("deployment_events", {"id": new_id("dep"), "kind": "MODEL_PROMOTED", "version": mid, "host": "", "operator": operator, "mode": mode})
        self.save()
        return True, []

    def to_dict(self) -> dict:
        return {"models": [{k: v for k, v in m.items() if k != "params"} | {"n_train": (m.get("params") or {}).get("n_train")} for m in self.models],
                "events": self.events[-50:], "champion": {k: (c["id"] if (c := self.champion(k)) else None) for k in {m["kind"] for m in self.models}}}
