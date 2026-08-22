"""İDEMPOTENT OUTBOX — worker yeniden başlasa da aynı bildirim iki kez gönderilmez.

Neden gerekli: worker restart sonrası açık pozisyonlar yeniden okunur. Naif bir uygulama bunları
"yeni işlem açıldı" diye tekrar bildirir. Outbox her olayı `event_id` ile kalıcı olarak işaretler;
`suppress()` ise sistem ilk kez etkinleştirildiğinde mevcut açık pozisyonların AÇILIŞ olaylarını
"gönderilmiş" sayar (sahte bildirim yok) — bu pozisyonlar KAPANDIĞINDA gerçek kapanış bildirimi
yine de gönderilir.

Dosya atomik yazılır (`atomic_write_json`): kısmen yazılmış JSON okunmaz. Telegram hatası ledger
işlemini GERİ ALMAZ; outbox yalnızca `failed` işaretler ve sınırlı sayıda yeniden dener.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, read_json, utc_now

STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SUPPRESSED = "suppressed"          # bilinçli olarak gönderilmedi (restart backlog'u)
SCHEMA = "notify_outbox_v1"


@dataclass
class OutboxEntry:
    id: str
    kind: str
    status: str = STATUS_PENDING
    attempts: int = 0
    created_at: str = ""
    updated_at: str = ""
    last_error: str = ""                   # tür adı; ASLA token/gizli içerik değil

    def to_dict(self) -> dict:
        return asdict(self)


class NotifyOutbox:
    """Kalıcı, atomik, idempotent bildirim kuyruğu."""

    def __init__(self, path: Path | str, *, keep: int = 2000, max_attempts: int = 3) -> None:
        self.path = Path(path)
        self.keep = int(keep)
        self.max_attempts = int(max_attempts)
        self.entries: dict[str, OutboxEntry] = {}
        self._order: list[str] = []
        self.load()

    # ------------------------------------------------------------------ kalıcılık
    def load(self) -> "NotifyOutbox":
        d = read_json(self.path, default=None)
        if not isinstance(d, dict):
            return self
        for row in d.get("entries") or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            e = OutboxEntry(id=str(row["id"]), kind=str(row.get("kind") or ""),
                            status=str(row.get("status") or STATUS_PENDING),
                            attempts=int(row.get("attempts") or 0),
                            created_at=str(row.get("created_at") or ""),
                            updated_at=str(row.get("updated_at") or ""),
                            last_error=str(row.get("last_error") or ""))
            self.entries[e.id] = e
            self._order.append(e.id)
        return self

    def save(self) -> None:
        keep_ids = self._order[-self.keep:]
        self._order = keep_ids
        self.entries = {k: v for k, v in self.entries.items() if k in set(keep_ids)}
        atomic_write_json(self.path, {"schema": SCHEMA, "updated_at": iso(utc_now()),
                                      "entries": [self.entries[i].to_dict() for i in keep_ids]})

    # ------------------------------------------------------------------ sorgu
    def known(self, event_id: str) -> bool:
        return event_id in self.entries

    def delivered(self, event_id: str) -> bool:
        """Gönderildi ya da bilinçli bastırıldı → TEKRAR GÖNDERİLMEZ."""
        e = self.entries.get(event_id)
        return bool(e and e.status in (STATUS_SENT, STATUS_SUPPRESSED))

    def status_of(self, event_id: str) -> str | None:
        e = self.entries.get(event_id)
        return e.status if e else None

    def pending(self) -> list[OutboxEntry]:
        return [self.entries[i] for i in self._order
                if self.entries[i].status in (STATUS_PENDING, STATUS_FAILED)
                and self.entries[i].attempts < self.max_attempts]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries.values():
            out[e.status] = out.get(e.status, 0) + 1
        return out

    # ------------------------------------------------------------------ mutasyon
    def _touch(self, event_id: str, kind: str) -> OutboxEntry:
        e = self.entries.get(event_id)
        if e is None:
            e = OutboxEntry(id=event_id, kind=kind, created_at=iso(utc_now()))
            self.entries[event_id] = e
            self._order.append(event_id)
        return e

    def enqueue(self, event_id: str, kind: str = "") -> bool:
        """Yeni olayı kuyruğa al. Zaten teslim edilmişse `False` döner (duplicate engeli)."""
        if self.delivered(event_id):
            return False
        e = self._touch(event_id, kind)
        if e.status == STATUS_FAILED and e.attempts >= self.max_attempts:
            return False
        e.status = STATUS_PENDING
        e.updated_at = iso(utc_now())
        return True

    def mark_sent(self, event_id: str) -> None:
        e = self._touch(event_id, "")
        e.status, e.updated_at, e.last_error = STATUS_SENT, iso(utc_now()), ""

    def mark_failed(self, event_id: str, error: str = "") -> None:
        e = self._touch(event_id, "")
        e.status = STATUS_FAILED
        e.attempts += 1
        e.updated_at = iso(utc_now())
        e.last_error = str(error)[:80]      # yalnız tür adı; gizli içerik yazılmaz

    def suppress(self, event_id: str, kind: str = "") -> None:
        """Bilinçli olarak gönderme (restart backlog'u). Kapanış bildirimi bundan ETKİLENMEZ."""
        if self.delivered(event_id):
            return
        e = self._touch(event_id, kind)
        e.status, e.updated_at = STATUS_SUPPRESSED, iso(utc_now())

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "counts": self.counts(), "size": len(self.entries)}


__all__ = ["NotifyOutbox", "OutboxEntry", "SCHEMA", "STATUS_FAILED", "STATUS_PENDING",
           "STATUS_SENT", "STATUS_SUPPRESSED"]
