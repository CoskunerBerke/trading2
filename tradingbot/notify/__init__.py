"""PAPER işlem bildirimleri — olay üretimi, idempotent outbox ve Telegram taşıması.

Token ASLA config'e, log'a, exception'a ya da panel API'sine girmez: yalnız ortam değişkeni ADI
config'te tutulur, değer çalışma anında okunur ve hiçbir yere yazılmaz.
"""
from .events import (EVENT_CLOSED, EVENT_DAILY_SUMMARY, EVENT_HEALTH_DEGRADED, EVENT_HEALTH_RECOVERED,
                     EVENT_OPENED, NotifyEvent, build_closed, build_daily_summary, build_health,
                     build_opened, event_id)
from .outbox import NotifyOutbox, OutboxEntry
from .service import TelegramTransport, TradeNotifier, redact

__all__ = ["EVENT_CLOSED", "EVENT_DAILY_SUMMARY", "EVENT_HEALTH_DEGRADED", "EVENT_HEALTH_RECOVERED",
           "EVENT_OPENED", "NotifyEvent", "NotifyOutbox", "OutboxEntry", "TelegramTransport",
           "TradeNotifier", "build_closed", "build_daily_summary", "build_health", "build_opened",
           "event_id", "redact"]
