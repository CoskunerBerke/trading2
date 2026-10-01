"""Olay zarfı — journal'a yazılan her kayıt bu alanları taşır."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .ids import new_id, payload_hash
from .timeutil import iso

SCHEMA_VERSION = 1


@dataclass
class EventEnvelope:
    event_id: str
    event_type: str                 # ör. "order.filled", "coin_head.decision", "incident"
    run_id: str
    created_at_utc: str
    source: str                     # engine | ledger | learner | llm | migration | dashboard | cli
    payload: dict[str, Any] = field(default_factory=dict)
    causation_id: str | None = None      # bu olaya neden olan olay
    correlation_id: str | None = None    # işlem yaşam döngüsü (trade_id / plan_id)
    schema_version: int = SCHEMA_VERSION
    payload_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def envelope(event_type: str, payload: dict[str, Any], *, run_id: str, source: str,
             causation_id: str | None = None, correlation_id: str | None = None) -> EventEnvelope:
    return EventEnvelope(event_id=new_id("evt"), event_type=event_type, run_id=run_id, created_at_utc=iso(),
                         source=source, payload=payload, causation_id=causation_id, correlation_id=correlation_id,
                         payload_hash=payload_hash({"t": event_type, "p": payload}))
