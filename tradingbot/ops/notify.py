"""Bildirimler — Log / Telegram / Discord. Yalnızca ilgili ortam değişkeni ayarlıysa ve `enabled=True` ise gönderir.

HTTP çağrısı enjekte edilebilir (`http=callable(url, json_body, timeout) -> int`), testler ağ kullanmaz.
Sırlar (token, webhook URL) asla loglanmaz.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

HttpFn = Callable[[str, dict[str, Any], float], int]

TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ENV = "TELEGRAM_CHAT_ID"
DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"


def _default_http(url: str, body: dict[str, Any], timeout: float) -> int:
    import urllib.request
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "tradingbot-notify"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return int(resp.status)


@dataclass
class NotifyResult:
    channel: str
    ok: bool
    status: int | None = None
    error: str = ""

    def to_dict(self) -> dict:
        return {"channel": self.channel, "ok": self.ok, "status": self.status, "error": self.error}


class LogChannel:
    name = "log"

    def send(self, title: str, text: str, level: str) -> NotifyResult:
        fn = {"error": log.error, "warning": log.warning}.get(level, log.info)
        fn("[notify] %s — %s", title, text)
        return NotifyResult(self.name, True)


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, http: HttpFn, timeout: float = 8.0) -> None:
        self._token, self._chat_id, self._http, self._timeout = token, chat_id, http, timeout

    def send(self, title: str, text: str, level: str) -> NotifyResult:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        body = {"chat_id": self._chat_id, "text": f"{title}\n{text}"[:4000], "disable_web_page_preview": True}
        try:
            status = self._http(url, body, self._timeout)
            return NotifyResult(self.name, 200 <= status < 300, status)
        except Exception as exc:  # noqa: BLE001 - dış servis; hata türü çeşitli
            log.warning("telegram bildirimi başarısız: %s", type(exc).__name__)
            return NotifyResult(self.name, False, None, type(exc).__name__)


class DiscordChannel:
    name = "discord"

    def __init__(self, webhook_url: str, http: HttpFn, timeout: float = 8.0) -> None:
        self._url, self._http, self._timeout = webhook_url, http, timeout

    def send(self, title: str, text: str, level: str) -> NotifyResult:
        body = {"content": f"**{title}**\n{text}"[:1900]}
        try:
            status = self._http(self._url, body, self._timeout)
            return NotifyResult(self.name, 200 <= status < 300, status)
        except Exception as exc:  # noqa: BLE001
            log.warning("discord bildirimi başarısız: %s", type(exc).__name__)
            return NotifyResult(self.name, False, None, type(exc).__name__)


@dataclass
class Notifier:
    enabled: bool = True
    channels: list[Any] = field(default_factory=list)
    min_level: str = "info"          # info | warning | error

    _LEVELS = {"debug": 0, "info": 1, "warning": 2, "error": 3}

    @classmethod
    def from_env(cls, enabled: bool = True, http: HttpFn | None = None, *, env: dict[str, str] | None = None,
                 include_log: bool = True, min_level: str = "info") -> "Notifier":
        env = os.environ if env is None else env
        http = http or _default_http
        chans: list[Any] = [LogChannel()] if include_log else []
        tok, chat = env.get(TELEGRAM_TOKEN_ENV, "").strip(), env.get(TELEGRAM_CHAT_ENV, "").strip()
        if tok and chat:
            chans.append(TelegramChannel(tok, chat, http))
        hook = env.get(DISCORD_WEBHOOK_ENV, "").strip()
        if hook.startswith("https://"):
            chans.append(DiscordChannel(hook, http))
        return cls(enabled=enabled, channels=chans, min_level=min_level)

    def send(self, title: str, text: str = "", level: str = "info") -> list[NotifyResult]:
        if not self.enabled:
            return []
        if self._LEVELS.get(level, 1) < self._LEVELS.get(self.min_level, 1):
            return []
        return [c.send(title, text, level) for c in self.channels]

    def incident(self, kind: str, text: str, severity: str = "warning") -> list[NotifyResult]:
        return self.send(f"[{severity.upper()}] {kind}", text, level=severity if severity in self._LEVELS else "warning")


__all__ = ["Notifier", "NotifyResult", "LogChannel", "TelegramChannel", "DiscordChannel", "HttpFn"]
