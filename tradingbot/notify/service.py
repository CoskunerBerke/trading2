"""Bildirim servisi — outbox + Telegram taşıması. Trade döngüsünü ASLA durdurmaz.

Güvenlik sözleşmesi:
* Telegram KAPALIYSA hiçbir ağ çağrısı yapılmaz (transport bile kurulmaz).
* Token yalnız ortam değişkeninden okunur; log'a, exception'a, outbox'a, panel API'sine YAZILMAZ.
  Dışarı sızabilecek her metin `redact()` süzgecinden geçer.
* Timeout + sınırlı retry vardır; sonsuz retry YOKTUR.
* Gönderim hatası pozisyon açma/kapatma kaydını GERİ ALMAZ — yalnız outbox `failed` işaretlenir.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

from ..core import iso, utc_now
from .events import (EVENT_CLOSED, EVENT_DAILY_SUMMARY, EVENT_HEALTH_DEGRADED, EVENT_HEALTH_RECOVERED,
                     EVENT_OPENED, EVENT_WORKER_FAILURE, EVENT_WORKER_RECOVERED, NotifyEvent, event_id)
from .outbox import NotifyOutbox

# Hangi config bayrağı hangi olay türünü kapatır (`notify_open`/`notify_close`/`notify_health`).
_GATE_OF: dict[str, str] = {
    EVENT_OPENED: "notify_open",
    EVENT_CLOSED: "notify_close",
    EVENT_HEALTH_DEGRADED: "notify_health",
    EVENT_HEALTH_RECOVERED: "notify_health",
    EVENT_WORKER_FAILURE: "notify_health",
    EVENT_WORKER_RECOVERED: "notify_health",
}

log = logging.getLogger(__name__)

HttpFn = Callable[[str, dict[str, Any], float], int]
_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{20,}\b")       # Telegram bot token biçimi


def redact(text: Any, *secrets: str) -> str:
    """Token/gizli değerleri maskele. Log ve hata mesajlarında ZORUNLU."""
    s = str(text)
    for sec in secrets:
        if sec:
            s = s.replace(sec, "***")
    return _TOKEN_RE.sub("***", s)


def _default_http(url: str, body: dict[str, Any], timeout: float) -> int:
    import json
    import urllib.request
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json", "User-Agent": "tradingbot-notify"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return int(resp.status)


class TelegramTransport:
    """`sendMessage` taşıması. `http` enjekte edilebilir → testler ağ KULLANMAZ."""
    name = "telegram"

    def __init__(self, token: str, chat_id: str, *, http: HttpFn | None = None, timeout: float = 8.0) -> None:
        self._token, self._chat_id = token, chat_id
        self._http = http or _default_http
        self._timeout = float(timeout)

    @property
    def configured(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, event: NotifyEvent) -> tuple[bool, str]:
        if not self.configured:
            return False, "NOT_CONFIGURED"           # fail-safe: eksik token/chat → ağ çağrısı yok
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        body = {"chat_id": self._chat_id, "text": f"{event.title}\n\n{event.text}"[:4000],
                "disable_web_page_preview": True}
        try:
            status = self._http(url, body, self._timeout)
        except Exception as exc:                     # noqa: BLE001 — dış servis; hata türü çeşitli
            kind = type(exc).__name__
            log.warning("telegram gönderimi başarısız: %s", redact(kind, self._token))
            return False, kind
        return (200 <= int(status) < 300), f"HTTP_{status}"


class TradeNotifier:
    """Olay → outbox → taşıma. Kapalıyken tamamen sessiz ve ağsızdır."""

    def __init__(self, *, enabled: bool = False, outbox_path: Path | str | None = None,
                 transport: Any | None = None, max_retries: int = 3, outbox_keep: int = 2000,
                 suppress_backlog_on_start: bool = True, retry_backoff_s: float = 2.0,
                 retry_batch: int = 5, notify_open: bool = True, notify_close: bool = True,
                 notify_health: bool = True, daily_summary_enabled: bool = True,
                 daily_summary_hour_utc: int = 21) -> None:
        self.enabled = bool(enabled)
        self.transport = transport
        self.suppress_backlog_on_start = bool(suppress_backlog_on_start)
        self.retry_backoff_s = max(0.0, float(retry_backoff_s))
        self.retry_batch = max(1, int(retry_batch))
        self.notify_open = bool(notify_open)
        self.notify_close = bool(notify_close)
        self.notify_health = bool(notify_health)
        self.daily_summary_enabled = bool(daily_summary_enabled)
        self.daily_summary_hour_utc = int(daily_summary_hour_utc)
        self.outbox = NotifyOutbox(outbox_path or Path("notify_outbox.json"),
                                   keep=outbox_keep, max_attempts=max_retries)
        self._bootstrapped = False
        self.last_error: str = ""

    # ------------------------------------------------------------------ kurulum
    @classmethod
    def from_config(cls, tg_cfg: Any, state_dir: Path | str, *, http: HttpFn | None = None,
                    env: dict[str, str] | None = None) -> "TradeNotifier":
        """Config + ortam değişkeninden kur. KAPALIYSA taşıma HİÇ oluşturulmaz."""
        env = os.environ if env is None else env
        enabled = bool(getattr(tg_cfg, "enabled", False))
        transport = None
        if enabled:
            token = str(env.get(getattr(tg_cfg, "bot_token_env", ""), "") or "").strip()
            chat = str(env.get(getattr(tg_cfg, "chat_id_env", ""), "") or "").strip()
            if token and chat:
                transport = TelegramTransport(token, chat, http=http,
                                              timeout=float(getattr(tg_cfg, "timeout_s", 8.0)))
            else:
                log.warning("telegram etkin fakat token/chat id ortamda yok — bildirim gönderilmeyecek "
                            "(fail-safe; ağ çağrısı yapılmaz)")
        return cls(enabled=enabled,
                   outbox_path=Path(state_dir) / str(getattr(tg_cfg, "outbox_file", "notify_outbox.json")),
                   transport=transport, max_retries=int(getattr(tg_cfg, "max_retries", 3)),
                   outbox_keep=int(getattr(tg_cfg, "outbox_keep", 2000)),
                   suppress_backlog_on_start=bool(getattr(tg_cfg, "suppress_backlog_on_start", True)),
                   retry_backoff_s=float(getattr(tg_cfg, "retry_backoff_s", 2.0)),
                   retry_batch=int(getattr(tg_cfg, "retry_batch", 5)),
                   notify_open=bool(getattr(tg_cfg, "notify_open", True)),
                   notify_close=bool(getattr(tg_cfg, "notify_close", True)),
                   notify_health=bool(getattr(tg_cfg, "notify_health", True)),
                   daily_summary_enabled=bool(getattr(tg_cfg, "daily_summary_enabled", True)),
                   daily_summary_hour_utc=int(getattr(tg_cfg, "daily_summary_hour_utc", 21)))

    def bootstrap_open_positions(self, positions: list[dict]) -> int:
        """Restart/ilk etkinleştirme: MEVCUT açık pozisyonlar için sahte "açıldı" bildirimi GÖNDERME.

        Yalnız AÇILIŞ olayı bastırılır; bu pozisyonlar kapandığında GERÇEK kapanış bildirimi
        normal biçimde gönderilir. Bir kez çalışır (idempotent).
        """
        if self._bootstrapped or not self.suppress_backlog_on_start:
            self._bootstrapped = True
            return 0
        n = 0
        for p in positions or []:
            tid = str(p.get("id") or p.get("trade_id") or "")
            if not tid:
                continue
            eid = event_id(EVENT_OPENED, tid, str(p.get("opened_at") or ""))
            if not self.outbox.delivered(eid):
                self.outbox.suppress(eid, EVENT_OPENED)
                n += 1
        self._bootstrapped = True
        if n:
            self.outbox.save()
            log.info("telegram: %d mevcut açık pozisyonun açılış bildirimi bastırıldı (restart backlog)", n)
        return n

    # ------------------------------------------------------------------ olay kapıları
    def wants(self, kind: str) -> bool:
        """Bu olay türü config'te AÇIK mı? (`notify_open`/`notify_close`/`notify_health`)

        Kapalı tür için olay ne ÜRETİLİR ne de kuyruğa alınır — outbox'ı gereksiz doldurmaz.
        """
        if not self.enabled:
            return False
        gate = _GATE_OF.get(str(kind))
        if gate is None:
            return True                                    # sınıflandırılmamış tür: varsayılan açık
        return bool(getattr(self, gate, True))

    # ------------------------------------------------------------------ kuyruğa alma (I/O YOK)
    def enqueue(self, event: NotifyEvent) -> bool:
        """Olayı YALNIZ outbox'a yaz — AĞ ÇAĞRISI YAPMAZ.

        `_entry_lock` gibi kritik bölgelerde bu kullanılır: pozisyon kalıcı olarak açıldıktan sonra
        deterministik olay kaydı hızlıca oluşur, HTTP ise kilit BIRAKILDIKTAN sonra `flush()` ile
        denenir. Gönderim başarısız olsa bile işlem geri alınmaz; olay retry için kalır.
        """
        if not self.wants(event.kind):
            return False
        try:
            if self.outbox.delivered(event.id):
                return False
            ok = self.outbox.enqueue(event.id, event.kind,
                                     payload={"title": event.title, "text": event.text,
                                              "level": event.level})
            self.outbox.save()
            return ok
        except Exception as exc:                            # noqa: BLE001 — bildirim trade'i düşürmez
            self.last_error = type(exc).__name__
            log.warning("bildirim kuyruğa alınamadı (trade döngüsü etkilenmedi): %s", redact(exc))
            return False

    def flush(self, limit: int = 20, *, now: Any = None) -> int:
        """Kuyruktaki ZAMANI GELMİŞ olayları gönder — kritik bölge DIŞINDA çağrılır.

        `sent`/`suppressed` olaylar dönmez (duplicate yok); `next_attempt_at` gelmemişse atlanır.
        """
        if not self.enabled or self.transport is None:
            return 0
        sent = 0
        try:
            for e in self.outbox.due(now)[:max(1, int(limit))]:
                ev = NotifyEvent(id=e.id, kind=e.kind, title=str(e.payload.get("title") or "PAPER"),
                                 text=str(e.payload.get("text") or ""),
                                 level=str(e.payload.get("level") or "info"))
                ok, info = self.transport.send(ev)
                if ok:
                    self.outbox.mark_sent(e.id)
                    sent += 1
                else:
                    self.outbox.mark_failed(e.id, info, backoff_s=self.retry_backoff_s)
                    self.last_error = info
            self.outbox.save()
        except Exception as exc:                            # noqa: BLE001
            self.last_error = type(exc).__name__
            log.warning("bildirim kuyruğu boşaltılamadı: %s", redact(exc))
        return sent

    # ------------------------------------------------------------------ gönderim
    def notify(self, event: NotifyEvent) -> bool:
        """Olayı gönder. `True` = bu çağrıda teslim edildi.

        Duplicate (aynı `event.id`) sessizce atlanır. Hata trade döngüsüne SIZMAZ.
        """
        if not self.wants(event.kind):
            return False
        try:
            if self.outbox.delivered(event.id):
                return False                                   # duplicate engeli
            if not self.outbox.enqueue(event.id, event.kind,
                                       payload={"title": event.title, "text": event.text,
                                                "level": event.level}):
                return False                                   # retry bütçesi bitti
            if self.transport is None:
                self.outbox.mark_failed(event.id, "NO_TRANSPORT")
                self.outbox.save()
                return False
            ok, info = self.transport.send(event)
            if ok:
                self.outbox.mark_sent(event.id)
            else:
                self.outbox.mark_failed(event.id, info, backoff_s=self.retry_backoff_s)
                self.last_error = info
            self.outbox.save()
            return ok
        except Exception as exc:                               # noqa: BLE001 — bildirim ASLA trade'i düşürmez
            self.last_error = type(exc).__name__
            log.warning("bildirim gönderilemedi (trade döngüsü etkilenmedi): %s", redact(exc))
            try:
                self.outbox.mark_failed(event.id, type(exc).__name__)
                self.outbox.save()
            except Exception:                                  # noqa: BLE001
                pass
            return False

    def notify_all(self, events: list[NotifyEvent]) -> int:
        return sum(1 for e in events if self.notify(e))

    def retry_pending(self, limit: int | None = None, *, now: Any = None) -> int:
        """Zamanı gelmiş başarısız olayları SINIRLI sayıda yeniden dener.

        Her turda en çok `retry_batch` olay işlenir; `next_attempt_at` gelmemiş olay ATLANIR; deneme
        bütçesi (`max_retries`) dolan olay bir daha denenmez. Böylece worker turu bloklanmaz ve
        sonsuz retry oluşmaz. Orijinal mesaj `payload`'dan gönderilir (uydurma metin yok).
        """
        return self.flush(limit if limit is not None else self.retry_batch, now=now)

    # ------------------------------------------------------------------ günlük özet
    def daily_summary_due(self, day: str, hour_utc: int) -> bool:
        """Bugünün özeti GÖNDERİLMELİ mi? Saat geldiyse ve o gün için henüz gönderilmediyse.

        Aynı gün worker yeniden başlasa bile ikinci kez gönderilmez (event id gün bazlıdır).
        Özet saatinde worker kapalıysa, SONRAKİ uygun turda aynı günün özeti bir kez gönderilir;
        GEÇMİŞ günler için toplu mesaj üretilmez (yalnız `day` parametresi işlenir).
        """
        if not self.enabled or not self.daily_summary_enabled:
            return False
        if int(hour_utc) < int(self.daily_summary_hour_utc):
            return False
        return not self.outbox.delivered(event_id(EVENT_DAILY_SUMMARY, "portfolio", day))

    # ------------------------------------------------------------------ worker failure/recovery
    def pending_worker_failure(self) -> str | None:
        """Kurtarma bildirimi bekleyen EN SON worker failure olayının referansı (yoksa None)."""
        for e in reversed(self.outbox.pending() + [x for x in self.outbox.entries.values()
                                                   if x.status == "sent"]):
            if e.kind != EVENT_WORKER_FAILURE:
                continue
            ref = e.id.split(":")[-1]
            if not self.outbox.known(event_id(EVENT_WORKER_RECOVERED, "worker", ref)):
                return ref
        return None

    def status(self) -> dict[str, Any]:
        """Panel/tanılama için GÜVENLİ durum — token ve chat id ASLA yer almaz."""
        return {"enabled": self.enabled, "transport": bool(self.transport),
                "gates": {"open": self.notify_open, "close": self.notify_close,
                          "health": self.notify_health, "daily_summary": self.daily_summary_enabled},
                "retry": {"backoff_s": self.retry_backoff_s, "batch": self.retry_batch,
                          "max_attempts": self.outbox.max_attempts},
                "outbox": self.outbox.to_dict(), "last_error": redact(self.last_error)}


__all__ = ["HttpFn", "TelegramTransport", "TradeNotifier", "redact"]
