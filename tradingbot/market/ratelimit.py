"""İstek ağırlığı bütçesi (Binance REST) — host başına token-bucket + 429/418 soğuma + üstel geri çekilme.

Binance limitleri (IP başına, dakikada): spot `api.binance.com` 6000 ağırlık (eski 1200), USDⓈ-M `fapi.binance.com`
2400 ağırlık. Muhafazakâr varsayılanlar (1200 / 2400) × `safety` (0.7) kullanılır; sunucunun döndürdüğü
`X-MBX-USED-WEIGHT-1M` başlığı ile yerel sayaç hizalanır.

* 429 → `Retry-After` kadar (yoksa 60 s) soğuma; tekrar denenebilir (`RateLimitedError`).
* 418 → IP yasağı; `Retry-After` kadar (yoksa 120 s) hiçbir istek atılmaz (`BannedError`).

Bütün zaman/uyku fonksiyonları enjekte edilebilir (testler ağ ve gerçek uyku olmadan çalışır).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

from ..core import TradingBotError

USED_WEIGHT_HEADER = "X-MBX-USED-WEIGHT-1M"
DEFAULT_WEIGHTS_PER_MINUTE: dict[str, int] = {"api.binance.com": 1200, "fapi.binance.com": 2400}


class RateLimitedError(TradingBotError):
    """429 alındı; `retry_after` saniye sonra tekrar denenebilir."""

    def __init__(self, message: str = "rate limited", retry_after: float = 60.0):
        super().__init__(message)
        self.retry_after = float(retry_after)


class BannedError(TradingBotError):
    """418 alındı: IP geçici olarak yasaklı. `until` = epoch saniye (bütçe saati)."""

    def __init__(self, until: float, message: str | None = None):
        super().__init__(message or f"IP banned until {until:.0f}")
        self.until = float(until)


def backoff(attempt: int, base: float = 0.5, cap: float = 30.0, jitter: bool = True,
            rng: Callable[[], float] = random.random) -> float:
    """Üstel geri çekilme süresi (saniye): min(cap, base * 2**attempt) ± %50 jitter ("equal jitter")."""
    attempt = max(0, int(attempt))
    raw = min(float(cap), float(base) * (2.0 ** attempt))
    if not jitter:
        return raw
    half = raw / 2.0
    return half + half * rng()


@dataclass
class RateBudget:
    """Tek host için token-bucket. `acquire(weight)` gerekli token yoksa uyur (bloklar).

    tokens: anlık kullanılabilir ağırlık; saniyede `capacity/60` yenilenir (sürekli yenileme).
    """

    weight_per_minute: int = 1200
    safety: float = 0.7
    host: str = ""
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    rng: Callable[[], float] = random.random
    max_jitter_s: float = 0.25
    tokens: float = field(init=False)
    cooldown_until: float = field(default=0.0, init=False)
    banned_until: float = field(default=0.0, init=False)
    used_weight_1m: int = field(default=0, init=False)
    total_acquired: int = field(default=0, init=False)
    total_sleep_s: float = field(default=0.0, init=False)
    _last: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.weight_per_minute <= 0:
            raise ValueError("weight_per_minute > 0 olmalı")
        self.safety = min(1.0, max(0.05, float(self.safety)))
        self.tokens = self.capacity
        self._last = self.clock()

    # ---- özellikler ------------------------------------------------------
    @property
    def capacity(self) -> float:
        return float(self.weight_per_minute) * self.safety

    @property
    def refill_per_second(self) -> float:
        return self.capacity / 60.0

    def _refill(self) -> None:
        now = self.clock()
        dt = max(0.0, now - self._last)
        self._last = now
        self.tokens = min(self.capacity, self.tokens + dt * self.refill_per_second)

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self.total_sleep_s += seconds
        self.sleeper(seconds)

    # ---- API -------------------------------------------------------------
    def wait_time(self, weight: int) -> float:
        """`weight` için beklenmesi gereken süre (uyumadan hesaplar)."""
        self._refill()
        now = self.clock()
        wait = 0.0
        if self.banned_until > now:
            wait = max(wait, self.banned_until - now)
        if self.cooldown_until > now:
            wait = max(wait, self.cooldown_until - now)
        if self.tokens < weight:
            wait = max(wait, (weight - self.tokens) / self.refill_per_second)
        return wait

    def acquire(self, weight: int = 1) -> float:
        """Token al; yoksa (jitter'lı) uyu. Dönen: toplam uyunan saniye. Yasak sürüyorsa BannedError."""
        weight = max(1, int(weight))
        if weight > self.capacity:
            raise ValueError(f"tek istek ağırlığı {weight} bütçe kapasitesini ({self.capacity:.0f}) aşıyor")
        slept = 0.0
        while True:
            now = self.clock()
            if self.banned_until > now:
                raise BannedError(self.banned_until)
            wait = self.wait_time(weight)
            if wait <= 0:
                break
            wait += self.max_jitter_s * self.rng()
            self._sleep(wait)
            slept += wait
        self.tokens -= weight
        self.total_acquired += weight
        return slept

    def on_response(self, headers: Mapping[str, str] | None) -> None:
        """Sunucu başlığındaki 1 dakikalık kullanılmış ağırlıkla yerel sayacı hizala."""
        if not headers:
            return
        used = None
        for k, v in headers.items():
            if str(k).upper() == USED_WEIGHT_HEADER:
                try:
                    used = int(v)
                except (TypeError, ValueError):
                    used = None
                break
        if used is None:
            return
        self.used_weight_1m = used
        self._refill()
        remaining_server = self.capacity - used
        if remaining_server < self.tokens:
            self.tokens = max(0.0, remaining_server)

    def on_429(self, retry_after: float | None = None) -> float:
        ra = float(retry_after) if retry_after else 60.0
        self.cooldown_until = max(self.cooldown_until, self.clock() + ra)
        self.tokens = 0.0
        return ra

    def on_418(self, retry_after: float | None = None) -> float:
        ra = float(retry_after) if retry_after else 120.0
        self.banned_until = max(self.banned_until, self.clock() + ra)
        self.tokens = 0.0
        return ra

    def to_dict(self) -> dict:
        now = self.clock()
        return {
            "host": self.host, "weight_per_minute": self.weight_per_minute, "safety": self.safety,
            "capacity": round(self.capacity, 1), "tokens": round(self.tokens, 1),
            "used_weight_1m": self.used_weight_1m, "cooldown_s": round(max(0.0, self.cooldown_until - now), 1),
            "banned_s": round(max(0.0, self.banned_until - now), 1), "total_acquired": self.total_acquired,
            "total_sleep_s": round(self.total_sleep_s, 2),
        }


class BudgetPool:
    """Host → RateBudget kayıt defteri (aynı host'u paylaşan istemciler tek bütçe kullanır)."""

    def __init__(self, safety: float = 0.7, clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep):
        self.safety = safety
        self.clock = clock
        self.sleeper = sleeper
        self._budgets: dict[str, RateBudget] = {}

    def get(self, host: str, weight_per_minute: int | None = None) -> RateBudget:
        host = host.lower()
        b = self._budgets.get(host)
        if b is None:
            wpm = weight_per_minute or DEFAULT_WEIGHTS_PER_MINUTE.get(host, 1200)
            b = RateBudget(weight_per_minute=wpm, safety=self.safety, host=host, clock=self.clock, sleeper=self.sleeper)
            self._budgets[host] = b
        return b

    def to_dict(self) -> dict:
        return {h: b.to_dict() for h, b in self._budgets.items()}


__all__ = ["RateBudget", "BudgetPool", "RateLimitedError", "BannedError", "backoff", "USED_WEIGHT_HEADER",
           "DEFAULT_WEIGHTS_PER_MINUTE"]
