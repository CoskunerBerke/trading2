"""Küçük HTTP istemcisi (yalnızca GET, public uç noktalar) — bütçe, tekrar deneme, geri çekilme.

`requests` tembel içe aktarılır; testler `.get(url, params=, timeout=)` sunan sahte bir oturum verir.
Yanıt nesnesinden beklenenler: `status_code`, `headers`, `json()`, `text`.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable
from urllib.parse import urlparse

from ..core import TradingBotError
from .ratelimit import BannedError, RateBudget, RateLimitedError, backoff

log = logging.getLogger(__name__)


class HttpError(TradingBotError):
    """4xx (429/418 hariç) — tekrar denenmez."""

    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} {url}: {body[:200]}")
        self.status = status
        self.url = url
        self.body = body


class TransientHttpError(TradingBotError):
    """5xx / zaman aşımı / bağlantı hatası — geri çekilerek tekrar denendi, yine olmadı."""


def _is_transient_exc(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    try:  # requests kuruluysa onun istisnalarını da geçici say
        import requests  # noqa: WPS433

        return isinstance(exc, (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError))
    except ImportError:  # pragma: no cover
        return False


def _retry_after(headers: Any) -> float | None:
    if not headers:
        return None
    for k, v in headers.items():
        if str(k).lower() == "retry-after":
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


class HttpClient:
    """`get(path, params, weight)` → JSON (dict|list). 5xx/zaman aşımında `max_retries` kez üstel geri çekilme."""

    def __init__(self, base_url: str, budget: RateBudget | None = None, timeout: float = 10.0, session: Any = None,
                 sleeper: Callable[[float], None] = time.sleep, max_retries: int = 3, headers: dict | None = None,
                 backoff_base: float = 0.5, backoff_cap: float = 30.0, jitter: bool = True):
        self.base_url = base_url.rstrip("/")
        self.host = urlparse(self.base_url).netloc or self.base_url
        self.budget = budget or RateBudget(host=self.host, sleeper=sleeper)
        self.timeout = timeout
        self._session = session
        self.sleeper = sleeper
        self.max_retries = max(0, int(max_retries))
        self.headers = {"User-Agent": "tradingbot-v3/market (paper)"} | (headers or {})
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.jitter = jitter
        self.stats = {"requests": 0, "retries": 0, "errors": 0, "rate_limited": 0}

    # ---- oturum -----------------------------------------------------------
    @property
    def session(self) -> Any:
        if self._session is None:
            import requests  # tembel: testler ağ olmadan çalışsın

            s = requests.Session()
            s.headers.update(self.headers)
            self._session = s
        return self._session

    def close(self) -> None:
        if self._session is not None and hasattr(self._session, "close"):
            try:
                self._session.close()
            except Exception:  # noqa: BLE001 — kapanış hatası önemsiz
                pass

    # ---- GET ---------------------------------------------------------------
    def get(self, path: str, params: dict | None = None, weight: int = 1) -> Any:
        url = self.base_url + ("/" + path.lstrip("/"))
        params = {k: v for k, v in (params or {}).items() if v is not None}
        attempt = 0
        last_exc: BaseException | None = None
        while True:
            self.budget.acquire(weight)
            self.stats["requests"] += 1
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001 — geçici mi diye sınıflandırılır
                if not _is_transient_exc(exc):
                    raise
                last_exc = exc
                self.stats["errors"] += 1
                if attempt >= self.max_retries:
                    raise TransientHttpError(f"GET {url} başarısız ({attempt + 1} deneme): {exc}") from exc
                self._sleep_backoff(attempt)
                attempt += 1
                continue

            headers = getattr(resp, "headers", None) or {}
            self.budget.on_response(headers)
            status = int(getattr(resp, "status_code", 0))
            if 200 <= status < 300:
                return resp.json()
            if status == 429:
                self.stats["rate_limited"] += 1
                ra = self.budget.on_429(_retry_after(headers))
                log.warning("429 %s — %.0fs soğuma", url, ra)
                if attempt >= self.max_retries:
                    raise RateLimitedError(f"429 {url}", retry_after=ra)
                attempt += 1
                continue  # acquire() soğuma bitene kadar uyur
            if status == 418:
                ra = self.budget.on_418(_retry_after(headers))
                self.stats["errors"] += 1
                log.error("418 %s — IP yasağı %.0fs", url, ra)
                raise BannedError(self.budget.banned_until, f"418 {url}: {ra:.0f}s yasak")
            if status >= 500 or status == 0:
                self.stats["errors"] += 1
                last_exc = TransientHttpError(f"HTTP {status} {url}")
                if attempt >= self.max_retries:
                    raise TransientHttpError(f"GET {url} {status} ({attempt + 1} deneme)") from last_exc
                self._sleep_backoff(attempt)
                attempt += 1
                continue
            self.stats["errors"] += 1
            raise HttpError(status, url, str(getattr(resp, "text", "")))

    def _sleep_backoff(self, attempt: int) -> None:
        d = backoff(attempt, base=self.backoff_base, cap=self.backoff_cap, jitter=self.jitter)
        self.stats["retries"] += 1
        self.sleeper(d)


__all__ = ["HttpClient", "HttpError", "TransientHttpError"]
