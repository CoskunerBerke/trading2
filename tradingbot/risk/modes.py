"""Çalışma modları ve geçiş kapıları. Varsayılan ve zorunlu: PAPER. Geçişler asla otomatik değildir.
LIVE bu sürümde kapalıdır: emir kod yolu guard'lar tamam olsa bile çalışmaz."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..core import ExecutionDisabledError, atomic_write_json, iso, read_json, stable_id

LIVE_ORDER_PATH_ENABLED_IN_THIS_BUILD = False


class OperatingMode(str, Enum):
    OBSERVE = "OBSERVE"
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    SHADOW_LIVE = "SHADOW_LIVE"
    LIVE_LIMITED = "LIVE_LIMITED"
    LIVE = "LIVE"


@dataclass(frozen=True)
class GraduationGates:
    """SHADOW_LIVE → LIVE_LIMITED için asgari kapılar (config ile değişebilir; düşürülürse uyarı verilir)."""
    min_paper_days: int = 90
    min_closed_trades: int = 300
    min_regimes: int = 3
    require_positive_oos_expectancy_after_costs: bool = True
    require_bootstrap_ci_ok: bool = True
    max_drawdown_pct: float = 8.0
    no_critical_incident_days: int = 30
    require_testnet_lifecycle_ok: bool = True
    require_shadow_vs_paper_close: bool = True
    require_manual_confirmation: bool = True

    def warnings_vs_default(self) -> list[str]:
        d = GraduationGates()
        w = []
        for k in ("min_paper_days", "min_closed_trades", "min_regimes", "no_critical_incident_days"):
            if getattr(self, k) < getattr(d, k):
                w.append(f"{k}={getattr(self, k)} < önerilen {getattr(d, k)}")
        if self.max_drawdown_pct > d.max_drawdown_pct:
            w.append(f"max_drawdown_pct={self.max_drawdown_pct} > önerilen {d.max_drawdown_pct}")
        for k in ("require_positive_oos_expectancy_after_costs", "require_bootstrap_ci_ok", "require_testnet_lifecycle_ok",
                  "require_shadow_vs_paper_close", "require_manual_confirmation"):
            if not getattr(self, k):
                w.append(f"{k} kapatıldı (önerilmez)")
        return w


ALLOWED_TRANSITIONS: dict[OperatingMode, set[OperatingMode]] = {
    OperatingMode.OBSERVE: {OperatingMode.PAPER},
    OperatingMode.PAPER: {OperatingMode.OBSERVE, OperatingMode.TESTNET},
    OperatingMode.TESTNET: {OperatingMode.PAPER, OperatingMode.SHADOW_LIVE},
    OperatingMode.SHADOW_LIVE: {OperatingMode.PAPER, OperatingMode.TESTNET, OperatingMode.LIVE_LIMITED},
    OperatingMode.LIVE_LIMITED: {OperatingMode.PAPER, OperatingMode.SHADOW_LIVE, OperatingMode.LIVE},
    OperatingMode.LIVE: {OperatingMode.PAPER, OperatingMode.LIVE_LIMITED},
}


@dataclass
class TransitionResult:
    ok: bool
    mode: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ModeState:
    def __init__(self, path: Path | str | None = None, gates: GraduationGates | None = None):
        self.path = Path(path) if path else None
        self.gates = gates or GraduationGates()
        self.mode = OperatingMode.PAPER
        self.history: list[dict[str, Any]] = []
        if self.path:
            d = read_json(self.path, default=None)
            if isinstance(d, dict):
                try:
                    self.mode = OperatingMode(d.get("mode", "PAPER"))
                except ValueError:
                    self.mode = OperatingMode.PAPER
                self.history = list(d.get("history", []))[-200:]

    def save(self) -> None:
        if self.path:
            atomic_write_json(self.path, self.to_dict(), keep_backup=True)

    def to_dict(self) -> dict:
        return {"mode": self.mode.value, "history": self.history[-200:], "live_order_path_enabled": self.is_live_order_path_enabled(),
                "gates": asdict(self.gates)}

    @staticmethod
    def is_live_order_path_enabled() -> bool:
        return LIVE_ORDER_PATH_ENABLED_IN_THIS_BUILD

    @staticmethod
    def live_token(account_label: str) -> str:
        return stable_id("LIVE-CONFIRM", account_label)

    # ------------------------------------------------------------ geçiş
    def request_transition(self, to: OperatingMode | str, *, operator: str, checks: dict[str, Any] | None = None,
                           confirmation_token: str | None = None, account_label: str = "default",
                           config_allow_live: bool = False) -> TransitionResult:
        to = OperatingMode(to)
        checks = checks or {}
        reasons: list[str] = []
        warnings = self.gates.warnings_vs_default()
        if not operator:
            reasons.append("OPERATOR_REQUIRED")
        if to not in ALLOWED_TRANSITIONS[self.mode]:
            reasons.append(f"ILLEGAL_TRANSITION {self.mode.value}→{to.value}")
        if to == OperatingMode.TESTNET:
            for k in ("manual_config", "testnet_keys_present", "test_suite_passed", "health_ok"):
                if not checks.get(k):
                    reasons.append(f"TESTNET_GATE_{k.upper()}")
        if to == OperatingMode.SHADOW_LIVE:
            for k in ("operator_confirmed", "secrets_validated", "reconciliation_ok", "read_only_permissions"):
                if not checks.get(k):
                    reasons.append(f"SHADOW_GATE_{k.upper()}")
        if to == OperatingMode.LIVE_LIMITED:
            g = self.gates
            reqs = {"paper_days": (checks.get("paper_days", 0) >= g.min_paper_days),
                    "closed_trades": (checks.get("closed_trades", 0) >= g.min_closed_trades),
                    "regimes": (checks.get("regimes", 0) >= g.min_regimes),
                    "positive_oos_expectancy_after_costs": (checks.get("positive_oos_expectancy_after_costs") or not g.require_positive_oos_expectancy_after_costs),
                    "bootstrap_ci_ok": (checks.get("bootstrap_ci_ok") or not g.require_bootstrap_ci_ok),
                    "max_drawdown_ok": (checks.get("max_drawdown_pct", 999) <= g.max_drawdown_pct),
                    "no_critical_incident_days": (checks.get("no_critical_incident_days", 0) >= g.no_critical_incident_days),
                    "testnet_lifecycle_ok": (checks.get("testnet_lifecycle_ok") or not g.require_testnet_lifecycle_ok),
                    "shadow_vs_paper_close": (checks.get("shadow_vs_paper_close") or not g.require_shadow_vs_paper_close),
                    "manual_confirmation": (checks.get("manual_confirmation") or not g.require_manual_confirmation)}
            reasons += [f"GRADUATION_GATE_{k.upper()}" for k, ok in reqs.items() if not ok]
        if to in (OperatingMode.LIVE_LIMITED, OperatingMode.LIVE):
            if os.environ.get("ALLOW_LIVE_TRADING", "").lower() != "true":
                reasons.append("ENV_ALLOW_LIVE_TRADING_NOT_TRUE")
            if not config_allow_live:
                reasons.append("CONFIG_LIVE_FLAG_FALSE")
            if confirmation_token != self.live_token(account_label):
                reasons.append("CONFIRMATION_TOKEN_MISMATCH")
        if to == OperatingMode.LIVE:
            reasons.append("LIVE_DISABLED_IN_THIS_BUILD")
        rec = {"at": iso(), "from": self.mode.value, "to": to.value, "operator": operator, "ok": not reasons, "reasons": reasons}
        self.history.append(rec)
        if not reasons:
            self.mode = to
        self.save()
        if to == OperatingMode.LIVE and reasons:
            # LIVE talebi: açık hata — hiçbir koşulda sessiz geçmez
            self.save()
            raise ExecutionDisabledError("LIVE modu bu sürümde kapalı: " + ", ".join(reasons))
        return TransitionResult(ok=not reasons, mode=self.mode.value, reasons=reasons, warnings=warnings)
