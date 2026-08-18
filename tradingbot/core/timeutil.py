"""Zaman yardımcıları — içeride her şey UTC ve timezone-aware; Europe/Istanbul yalnızca gösterim."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")
FUNDING_HOURS_UTC = (0, 8, 16)   # Binance USDⓈ-M varsayılan funding settlement saatleri


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None, *, timespec: str = "seconds") -> str:
    """UTC ISO-8601 (`+00:00` sonekli)."""
    dt = dt or utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec=timespec)


def from_iso(s: str) -> datetime:
    """ISO metni → aware UTC datetime. Naive gelirse UTC varsayılır ('Z' desteklenir)."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def istanbul(dt: datetime | None = None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Gösterim için Europe/Istanbul metni."""
    dt = dt or utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ISTANBUL).strftime(fmt)


def funding_settlements_between(start: datetime, end: datetime, hours: tuple[int, ...] = FUNDING_HOURS_UTC) -> list[datetime]:
    """(start, end] aralığındaki funding settlement zamanları (UTC). Kaçırılan bütün dönemleri toplu uygulamak için."""
    if end <= start:
        return []
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    out: list[datetime] = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        for h in hours:
            t = day.replace(hour=h)
            if start < t <= end:
                out.append(t)
        day += timedelta(days=1)
    return out
