"""İDEMPOTENT OUTBOX — worker yeniden başlasa da aynı bildirim iki kez gönderilmez.

Neden gerekli: worker restart sonrası açık pozisyonlar yeniden okunur. Naif bir uygulama bunları
"yeni işlem açıldı" diye tekrar bildirir. Outbox her olayı `event_id` ile kalıcı olarak işaretler;
`suppress()` ise sistem ilk kez etkinleştirildiğinde mevcut açık pozisyonların AÇILIŞ olaylarını
"gönderilmiş" sayar (sahte bildirim yok) — bu pozisyonlar KAPANDIĞINDA gerçek kapanış bildirimi
yine de gönderilir.

Dosya atomik ve YEDEKLİ yazılır (`atomic_write_json(..., keep_backup=True)`): kısmen yazılmış JSON
okunmaz ve ana dosya bozulursa `read_json` otomatik olarak `.bak` kopyasından kurtarır (bozuk dosya
`<ad>.corrupt-N` olarak kenara alınır, silinmez). Her iki kopya da okunamazsa idempotency geçmişi
kaybolur (fail-open) — bu kalan risk `docs/observability-leverage-telegram.md` içinde belgelenmiştir.
Bozuk veri ASLA "gönderildi" varsayılmaz.

Telegram hatası ledger işlemini GERİ ALMAZ; outbox yalnızca `failed` işaretler ve `next_attempt_at`
zamanı geldiğinde sınırlı sayıda yeniden dener (üstel, üst sınırlı backoff).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, read_json, utc_now

MAX_BACKOFF_S = 3600.0                     # üst sınır: backoff sonsuza büyümez

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
    next_attempt_at: str = ""              # bu zamandan ÖNCE yeniden denenmez (backoff)
    payload: dict = field(default_factory=dict)   # yeniden gönderim için mesaj (token İÇERMEZ)

    def to_dict(self) -> dict:
        return asdict(self)

    def due(self, now_iso: str) -> bool:
        return (not self.next_attempt_at) or self.next_attempt_at <= now_iso


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
                            last_error=str(row.get("last_error") or ""),
                            next_attempt_at=str(row.get("next_attempt_at") or ""),
                            payload=dict(row.get("payload") or {}))
            self.entries[e.id] = e
            self._order.append(e.id)
        return self

    def save(self) -> None:
        keep_ids = self._order[-self.keep:]
        self._order = keep_ids
        self.entries = {k: v for k, v in self.entries.items() if k in set(keep_ids)}
        # `keep_backup=True`: ana dosya bozulursa `read_json` `.bak` kopyasından KURTARIR.
        atomic_write_json(self.path, {"schema": SCHEMA, "updated_at": iso(utc_now()),
                                      "entries": [self.entries[i].to_dict() for i in keep_ids]},
                          keep_backup=True)

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
        """Deneme bütçesi kalan bekleyen/başarısız olaylar (zaman filtresi UYGULANMAZ)."""
        return [self.entries[i] for i in self._order
                if self.entries[i].status in (STATUS_PENDING, STATUS_FAILED)
                and self.entries[i].attempts < self.max_attempts]

    def due(self, now: datetime | None = None) -> list[OutboxEntry]:
        """Yeniden deneme ZAMANI GELMİŞ olaylar. `sent`/`suppressed` ASLA dönmez."""
        now_iso = iso(now or utc_now())
        return [e for e in self.pending() if e.due(now_iso)]

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

    def enqueue(self, event_id: str, kind: str = "", payload: dict | None = None) -> bool:
        """Yeni olayı kuyruğa al. Zaten teslim edilmişse `False` döner (duplicate engeli).

        `payload` (başlık/metin) saklanır; böylece yeniden deneme ORİJİNAL mesajı gönderir.
        Token/chat id ASLA payload'a yazılmaz — onlar yalnız taşıma katmanında bulunur.
        """
        if self.delivered(event_id):
            return False
        e = self._touch(event_id, kind)
        if e.status == STATUS_FAILED and e.attempts >= self.max_attempts:
            return False
        e.status = STATUS_PENDING
        e.updated_at = iso(utc_now())
        if payload:
            e.payload = dict(payload)
        return True

    def mark_sent(self, event_id: str) -> None:
        e = self._touch(event_id, "")
        e.status, e.updated_at, e.last_error = STATUS_SENT, iso(utc_now()), ""

    def mark_failed(self, event_id: str, error: str = "", *, backoff_s: float = 0.0,
                    now: datetime | None = None) -> None:
        """Başarısız işaretle ve ÜSTEL, ÜST SINIRLI backoff ile bir sonraki deneme zamanını kur.

        `next_attempt_at = now + backoff_s * 2**(attempts-1)` (en çok `MAX_BACKOFF_S`). Böylece
        aynı olay her turda yeniden denenmez; worker turu bloklanmaz.
        """
        now = now or utc_now()
        e = self._touch(event_id, "")
        e.status = STATUS_FAILED
        e.attempts += 1
        e.updated_at = iso(now)
        e.last_error = str(error)[:80]      # yalnız tür adı; gizli içerik yazılmaz
        if backoff_s > 0:
            delay = min(float(backoff_s) * (2 ** max(0, e.attempts - 1)), MAX_BACKOFF_S)
            e.next_attempt_at = iso(now + timedelta(seconds=delay))

    def suppress(self, event_id: str, kind: str = "") -> None:
        """Bilinçli olarak gönderme (restart backlog'u). Kapanış bildirimi bundan ETKİLENMEZ."""
        if self.delivered(event_id):
            return
        e = self._touch(event_id, kind)
        e.status, e.updated_at = STATUS_SUPPRESSED, iso(utc_now())

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "counts": self.counts(), "size": len(self.entries)}


__all__ = ["MAX_BACKOFF_S", "NotifyOutbox", "OutboxEntry", "SCHEMA", "STATUS_FAILED",
           "STATUS_PENDING", "STATUS_SENT", "STATUS_SUPPRESSED"]
