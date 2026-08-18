"""Sağlık durumu — HEALTHY / DEGRADED / PAUSED / KILL_SWITCH / DATA_STALE / RECONCILIATION_REQUIRED.

`HealthMonitor(state_dir).evaluate(inputs)` → `HealthReport`; `.write(report)` → state/health.json.
`heartbeat(state_dir)` her tur çağrılır → state/heartbeat.json (dashboard /health/ready ve doctor bunu okur).
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, from_iso, iso, read_json, utc_now

HEARTBEAT_FILE = "heartbeat.json"
HEALTH_FILE = "health.json"


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    PAUSED = "PAUSED"
    KILL_SWITCH = "KILL_SWITCH"
    DATA_STALE = "DATA_STALE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


# HTTP hazır (ready) sayılan durumlar
READY_STATES = {HealthState.HEALTHY, HealthState.DEGRADED}


@dataclass
class HealthReport:
    state: HealthState
    checks: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["ready"] = self.state in READY_STATES
        return d

    @property
    def ready(self) -> bool:
        return self.state in READY_STATES


def heartbeat(state_dir: Path | str, run_id: str | None = None, extra: dict[str, Any] | None = None) -> Path:
    """state/heartbeat.json yazar: {ts, pid, run_id, ...extra}."""
    p = Path(state_dir) / HEARTBEAT_FILE
    d: dict[str, Any] = {"ts": iso(), "pid": os.getpid()}
    if run_id:
        d["run_id"] = run_id
    if extra:
        d.update(extra)
    atomic_write_json(p, d)
    return p


def read_heartbeat_age(state_dir: Path | str) -> float | None:
    """Saniye cinsinden kalp atışı yaşı; dosya yok/bozuksa None."""
    d = read_json(Path(state_dir) / HEARTBEAT_FILE, default=None)
    if not isinstance(d, dict) or not d.get("ts"):
        return None
    try:
        return max(0.0, (utc_now() - from_iso(str(d["ts"]))).total_seconds())
    except (ValueError, TypeError):
        return None


class HealthMonitor:
    """Girdi sözlüğünden sağlık durumu türetir. Öncelik: KILL_SWITCH > RECONCILIATION_REQUIRED > DATA_STALE > PAUSED > DEGRADED > HEALTHY.

    inputs anahtarları (hepsi isteğe bağlı):
      killswitch_state (ARMED|HALT_ENTRIES|HALT_ALL), reconciliation_required (bool), data_age_s (float),
      data_stale (bool), paused (bool), mode (str), heartbeat_age_s (float; yoksa dosyadan okunur),
      error_count (int), llm_budget_exceeded (bool), disk_free_gb (float), degraded_reasons (list[str])
    """

    def __init__(self, state_dir: Path | str, *, heartbeat_max_age_s: float = 900.0, data_max_age_s: float = 4 * 3600.0,
                 min_disk_free_gb: float = 1.0, max_errors: int = 5) -> None:
        self.state_dir = Path(state_dir)
        self.heartbeat_max_age_s = heartbeat_max_age_s
        self.data_max_age_s = data_max_age_s
        self.min_disk_free_gb = min_disk_free_gb
        self.max_errors = max_errors

    def evaluate(self, inputs: dict[str, Any] | None = None) -> HealthReport:
        inp = dict(inputs or {})
        checks: list[dict[str, Any]] = []

        def add(name: str, ok: bool, detail: Any = "", severity: str = "warn") -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})

        ks = str(inp.get("killswitch_state", "ARMED") or "ARMED")
        add("killswitch", ks == "ARMED", ks, "kill")
        recon = bool(inp.get("reconciliation_required", False))
        add("reconciliation", not recon, "uzlaştırma gerekli" if recon else "ok", "recon")
        data_age = inp.get("data_age_s")
        stale = bool(inp.get("data_stale", False)) or (data_age is not None and float(data_age) > self.data_max_age_s)
        add("data_freshness", not stale, {"age_s": data_age, "max_s": self.data_max_age_s}, "stale")
        paused = bool(inp.get("paused", False))
        add("paused", not paused, "duraklatıldı" if paused else "ok", "paused")
        hb_age = inp.get("heartbeat_age_s")
        if hb_age is None:
            hb_age = read_heartbeat_age(self.state_dir)
        hb_ok = hb_age is not None and float(hb_age) <= self.heartbeat_max_age_s
        add("heartbeat", hb_ok, {"age_s": None if hb_age is None else round(float(hb_age), 1), "max_s": self.heartbeat_max_age_s}, "warn")
        errs = int(inp.get("error_count", 0) or 0)
        add("errors", errs < self.max_errors, errs, "warn")
        add("llm_budget", not bool(inp.get("llm_budget_exceeded", False)), "aşıldı" if inp.get("llm_budget_exceeded") else "ok", "warn")
        disk = inp.get("disk_free_gb")
        add("disk", disk is None or float(disk) >= self.min_disk_free_gb, disk, "warn")
        for r in inp.get("degraded_reasons") or []:
            add("degraded", False, str(r), "warn")

        if ks != "ARMED":
            state = HealthState.KILL_SWITCH
        elif recon:
            state = HealthState.RECONCILIATION_REQUIRED
        elif stale:
            state = HealthState.DATA_STALE
        elif paused:
            state = HealthState.PAUSED
        elif any(not c["ok"] for c in checks):
            state = HealthState.DEGRADED
        else:
            state = HealthState.HEALTHY
        failed = [c["name"] for c in checks if not c["ok"]]
        summary = state.value + (" · " + ", ".join(failed) if failed else "")
        return HealthReport(state=state, checks=checks, generated_at=iso(), summary=summary)

    def write(self, report: HealthReport) -> Path:
        p = self.state_dir / HEALTH_FILE
        atomic_write_json(p, report.to_dict())
        return p

    def read(self) -> dict | None:
        d = read_json(self.state_dir / HEALTH_FILE, default=None)
        return d if isinstance(d, dict) else None


__all__ = ["HealthState", "HealthReport", "HealthMonitor", "heartbeat", "read_heartbeat_age", "READY_STATES",
           "HEARTBEAT_FILE", "HEALTH_FILE"]
