"""Kill switch — kalıcı (state/killswitch.json), manuel reset gerektirir, koruyucu çıkışları asla engellemez."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, read_json

ARMED, HALT_ENTRIES, HALT_ALL = "ARMED", "HALT_ENTRIES", "HALT_ALL"

# tetik kodu → seviye
TRIP_LEVEL: dict[str, str] = {
    "DAILY_LOSS": HALT_ENTRIES, "WEEKLY_LOSS": HALT_ENTRIES, "MAX_DRAWDOWN": HALT_ENTRIES,
    "STALE_DATA": HALT_ENTRIES, "WS_SEQUENCE_CORRUPTION": HALT_ENTRIES, "PRICE_DIVERGENCE": HALT_ENTRIES,
    "CLOCK_DRIFT": HALT_ALL, "REPEATED_ORDER_REJECTION": HALT_ENTRIES, "RECONCILIATION_MISMATCH": HALT_ALL,
    "DB_WRITE_FAILURE": HALT_ALL, "DISK_FULL": HALT_ALL, "RATE_LIMIT_BAN": HALT_ENTRIES,
    "LLM_SCHEMA_FAILURE_STREAK": HALT_ENTRIES, "MODEL_DRIFT": HALT_ENTRIES, "WIDE_SPREAD": HALT_ENTRIES,
    "EXTREME_VOLATILITY": HALT_ENTRIES, "EXCHANGE_MAINTENANCE": HALT_ALL, "BALANCE_MISMATCH": HALT_ALL,
    "UNEXPECTED_OPEN_POSITION": HALT_ALL, "MANUAL": HALT_ALL,
}


@dataclass
class KillSwitch:
    path: Path | None = None
    state: str = ARMED
    since: str = ""
    reasons: list[dict[str, Any]] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------ kalıcılık
    @classmethod
    def load(cls, path: Path | str) -> "KillSwitch":
        path = Path(path)
        d = read_json(path, default=None)
        ks = cls(path=path)
        if isinstance(d, dict):
            ks.state = d.get("state", ARMED)
            ks.since = d.get("since", "")
            ks.reasons = list(d.get("reasons", []))
            ks.audit = list(d.get("audit", []))[-500:]
        return ks

    def save(self) -> None:
        if self.path is not None:
            atomic_write_json(self.path, self.to_dict(), keep_backup=True)

    def to_dict(self) -> dict:
        return {"state": self.state, "since": self.since, "reasons": self.reasons, "audit": self.audit[-500:]}

    # ------------------------------------------------------------ davranış
    def trip(self, code: str, detail: str = "", source: str = "risk_engine") -> str:
        level = TRIP_LEVEL.get(code, HALT_ENTRIES)
        now = iso()
        rec = {"at": now, "code": code, "detail": detail[:300], "source": source, "level": level}
        self.reasons.append(rec)
        self.audit.append({"at": now, "action": "TRIP", **rec})
        # seviye yalnızca yükselir; reset manuel
        if self.state == ARMED or (self.state == HALT_ENTRIES and level == HALT_ALL):
            self.state = level
            self.since = now
        self.save()
        return self.state

    def reset(self, operator: str, note: str) -> None:
        """Manuel reset — kim, ne zaman, neden kaydı zorunlu."""
        if not operator or not note:
            raise ValueError("reset için operator ve note zorunlu")
        self.audit.append({"at": iso(), "action": "RESET", "operator": operator, "note": note[:300],
                           "previous_state": self.state, "cleared": len(self.reasons)})
        self.state, self.since, self.reasons = ARMED, "", []
        self.save()

    def allows_entry(self) -> bool:
        return self.state == ARMED

    def allows_exit(self) -> bool:
        """Koruyucu çıkışlar (stop/TP/manuel kapatma) HER durumda çalışır."""
        return True

    @property
    def active(self) -> bool:
        return self.state != ARMED
