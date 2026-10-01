"""LLM bütçesi, fiyat tablosu, devre kesici ve semantik önbellek.

* `PriceTable`     : model → USD / 1M token (input, output, cache_read) + `verified_at`. FİYATLAR GERÇEK
                     DEĞİL, YAPILANDIRMADIR: claude-api skill tablosundan (cache 2026-06-24) kopyalandı;
                     lead config.yaml'dan geçersiz kılabilir. Bilinmeyen model → en pahalı satır varsayılır
                     (bütçe muhafazakâr kalsın).
* `estimate_cost`  : token sayılarından USD tahmini.
* `LLMBudget`      : günlük USD / token tavanı, tur başına aday sınırı, max_output_tokens; `state/llm_budget.json`
                     atomik yazılır, UTC gün değişince sıfırlanır.
* `CircuitBreaker` : N ardışık hata → açık (çağrı yok) → cooldown sonrası yarı-açık deneme.
* `SemanticCache`  : `payload_hash(snapshot)` anahtarlı TTL'li JSON önbellek (aynı snapshot → aynı tavsiye,
                     sağlayıcıya gitmeden).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..core import atomic_write_json, iso, payload_hash, read_json, utc_now

# --------------------------------------------------------------------------- price table
# Kaynak: claude-api skill "Current Models" tablosu (cached 2026-06-24). Cache-read ~0.1× input, cache-write
# ~1.25× input (5 dk TTL). Bunlar YAPILANDIRMA varsayılanıdır; gerçek fatura Anthropic konsolundan doğrulanmalı.
DEFAULT_PRICES: dict[str, dict[str, Any]] = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "verified_at": "2026-06-24"},
    "claude-haiku-4-5":          {"input": 1.00, "output": 5.00, "cache_read": 0.10, "verified_at": "2026-06-24"},
    "claude-sonnet-5":           {"input": 3.00, "output": 15.00, "cache_read": 0.30, "verified_at": "2026-06-24"},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00, "cache_read": 0.30, "verified_at": "2026-06-24"},
    "claude-opus-5":             {"input": 5.00, "output": 25.00, "cache_read": 0.50, "verified_at": "2026-06-24"},
    "claude-opus-4-8":           {"input": 5.00, "output": 25.00, "cache_read": 0.50, "verified_at": "2026-06-24"},
    "claude-fable-5":            {"input": 10.00, "output": 50.00, "cache_read": 1.00, "verified_at": "2026-06-24"},
    "noop":                      {"input": 0.0, "output": 0.0, "cache_read": 0.0, "verified_at": "n/a"},
    "fake-model":                {"input": 0.0, "output": 0.0, "cache_read": 0.0, "verified_at": "n/a"},
}


@dataclass
class ModelPrice:
    model: str
    input_per_m: float
    output_per_m: float
    cache_read_per_m: float
    verified_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "input_per_m": self.input_per_m, "output_per_m": self.output_per_m,
                "cache_read_per_m": self.cache_read_per_m, "verified_at": self.verified_at}


class PriceTable:
    """Model başına fiyat (USD / 1M token). Bilinmeyen model → tablodaki en pahalı satır (muhafazakâr)."""

    def __init__(self, prices: dict[str, dict[str, Any]] | None = None) -> None:
        self._rows: dict[str, ModelPrice] = {}
        for name, row in (prices or DEFAULT_PRICES).items():
            self.set(name, row.get("input", 0.0), row.get("output", 0.0), row.get("cache_read", row.get("input", 0.0) * 0.1),
                     row.get("verified_at", ""))

    def set(self, model: str, input_per_m: float, output_per_m: float, cache_read_per_m: float | None = None,
            verified_at: str = "") -> None:
        cr = float(cache_read_per_m) if cache_read_per_m is not None else float(input_per_m) * 0.1
        self._rows[model] = ModelPrice(model, float(input_per_m), float(output_per_m), cr, verified_at)

    def get(self, model: str) -> ModelPrice:
        if model in self._rows:
            return self._rows[model]
        # önek eşleşmesi (ör. tarihli sürüm), yoksa en pahalı
        for name, row in self._rows.items():
            if model.startswith(name) or name.startswith(model):
                return row
        priced = [r for r in self._rows.values() if r.output_per_m > 0]
        if not priced:
            return ModelPrice(model, 0.0, 0.0, 0.0, "unknown")
        worst = max(priced, key=lambda r: r.output_per_m)
        return ModelPrice(model, worst.input_per_m, worst.output_per_m, worst.cache_read_per_m, "unknown-fallback")

    def estimate(self, model: str, in_tok: int, out_tok: int, cache_read: int = 0) -> float:
        p = self.get(model)
        return (max(0, in_tok) * p.input_per_m + max(0, out_tok) * p.output_per_m
                + max(0, cache_read) * p.cache_read_per_m) / 1_000_000.0

    def to_dict(self) -> dict[str, Any]:
        return {k: v.to_dict() for k, v in self._rows.items()}


_DEFAULT_TABLE = PriceTable()


def estimate_cost(model: str, in_tok: int, out_tok: int, cache_read: int = 0, table: PriceTable | None = None) -> float:
    """USD tahmini (girdi tam fiyat, cache-read indirimli, çıktı)."""
    return (table or _DEFAULT_TABLE).estimate(model, in_tok, out_tok, cache_read)


# --------------------------------------------------------------------------- budget
def _utc_day(now: datetime | None = None) -> str:
    return (now or utc_now()).astimezone(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class BudgetState:
    day: str
    spent_usd: float = 0.0
    spent_tokens: int = 0
    calls: int = 0
    tour_id: str | None = None
    tour_candidates: int = 0
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"day": self.day, "spent_usd": round(self.spent_usd, 6), "spent_tokens": self.spent_tokens,
                "calls": self.calls, "tour_id": self.tour_id, "tour_candidates": self.tour_candidates,
                "last_updated": self.last_updated}


class LLMBudget:
    """Günlük USD/token tavanı + tur başına aday sınırı. Durum `path`'e atomik yazılır (UTC gün rollover)."""

    def __init__(self, path: Path | str | None = None, daily_usd: float = 2.0, daily_tokens: int = 400_000,
                 per_tour_candidates: int = 3, max_output_tokens: int = 1024,
                 clock: Callable[[], datetime] = utc_now) -> None:
        self.path = Path(path) if path else None
        self.daily_usd = float(daily_usd)
        self.daily_tokens = int(daily_tokens)
        self.per_tour_candidates = int(per_tour_candidates)
        self.max_output_tokens = int(max_output_tokens)
        self._clock = clock
        self.state = self._load()

    # -- persistence
    def _load(self) -> BudgetState:
        today = _utc_day(self._clock())
        if self.path is not None:
            raw = read_json(self.path, default=None)
            if isinstance(raw, dict) and raw.get("day") == today:
                return BudgetState(day=today, spent_usd=float(raw.get("spent_usd", 0.0)),
                                   spent_tokens=int(raw.get("spent_tokens", 0)), calls=int(raw.get("calls", 0)),
                                   tour_id=raw.get("tour_id"), tour_candidates=int(raw.get("tour_candidates", 0)),
                                   last_updated=str(raw.get("last_updated", "")))
        return BudgetState(day=today)

    def _save(self) -> None:
        self.state.last_updated = iso(self._clock())
        if self.path is not None:
            atomic_write_json(self.path, self.state.to_dict())

    def _rollover(self) -> None:
        today = _utc_day(self._clock())
        if self.state.day != today:
            self.state = BudgetState(day=today)
            self._save()

    # -- api
    def can_spend(self, est_tokens: int, est_usd: float, tour_id: str | None = None) -> tuple[bool, str]:
        """(izin, gerekçe). Tahmin bile tavanı aşıyorsa reddet. `tour_id` verilirse tur başına aday sınırı da uygulanır."""
        self._rollover()
        s = self.state
        if est_tokens < 0 or est_usd < 0:
            return False, "negative_estimate"
        if s.spent_usd + est_usd > self.daily_usd + 1e-12:
            return False, f"daily_usd_exceeded ({s.spent_usd:.4f}+{est_usd:.4f} > {self.daily_usd})"
        if s.spent_tokens + est_tokens > self.daily_tokens:
            return False, f"daily_tokens_exceeded ({s.spent_tokens}+{est_tokens} > {self.daily_tokens})"
        if tour_id is not None:
            cand = s.tour_candidates if s.tour_id == tour_id else 0
            if cand >= self.per_tour_candidates:
                return False, f"per_tour_candidates_exceeded ({cand} >= {self.per_tour_candidates})"
        return True, "ok"

    def record(self, usage: dict[str, Any], cost: float, tour_id: str | None = None) -> None:
        """Gerçekleşen kullanım: usage = {input_tokens, output_tokens, cache_read_tokens}."""
        self._rollover()
        s = self.state
        toks = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)) + int(usage.get("cache_read_tokens", 0))
        s.spent_tokens += toks
        s.spent_usd += float(cost)
        s.calls += 1
        if tour_id is not None:
            if s.tour_id != tour_id:
                s.tour_id, s.tour_candidates = tour_id, 0
            s.tour_candidates += 1
        self._save()

    def remaining(self) -> dict[str, Any]:
        self._rollover()
        s = self.state
        return {"day": s.day, "usd": max(0.0, self.daily_usd - s.spent_usd),
                "tokens": max(0, self.daily_tokens - s.spent_tokens), "calls": s.calls,
                "tour_candidates_left": max(0, self.per_tour_candidates - s.tour_candidates)}

    def to_dict(self) -> dict[str, Any]:
        return {"daily_usd": self.daily_usd, "daily_tokens": self.daily_tokens,
                "per_tour_candidates": self.per_tour_candidates, "max_output_tokens": self.max_output_tokens,
                "state": self.state.to_dict(), "remaining": self.remaining()}


# --------------------------------------------------------------------------- circuit breaker
class CircuitBreaker:
    """Ardışık `failures_to_open` hata → OPEN (çağrı reddedilir) → `cooldown_s` sonra HALF_OPEN (bir deneme).
    Başarı → CLOSED ve sayaç sıfır. `clock` enjekte edilebilir (test)."""

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failures_to_open: int = 3, cooldown_s: float = 900.0, clock: Callable[[], float] = time.time) -> None:
        self.failures_to_open = int(failures_to_open)
        self.cooldown_s = float(cooldown_s)
        self._clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self.state = self.CLOSED

    def allow(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if self.opened_at is not None and self._clock() - self.opened_at >= self.cooldown_s:
                self.state = self.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN → tek deneme

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.state = self.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == self.HALF_OPEN or self.failures >= self.failures_to_open:
            self.state = self.OPEN
            self.opened_at = self._clock()

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "failures": self.failures, "opened_at": self.opened_at,
                "failures_to_open": self.failures_to_open, "cooldown_s": self.cooldown_s}


# --------------------------------------------------------------------------- semantic cache
class SemanticCache:
    """`payload_hash(snapshot)` anahtarlı TTL önbellek. `path` verilirse atomik JSON'a yazılır (in-memory da olur)."""

    def __init__(self, path: Path | str | None = None, ttl_s: float = 3600.0, max_entries: int = 500,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path) if path else None
        self.ttl_s = float(ttl_s)
        self.max_entries = int(max_entries)
        self._clock = clock
        self._data: dict[str, dict[str, Any]] = {}
        if self.path is not None:
            raw = read_json(self.path, default={})
            if isinstance(raw, dict):
                self._data = {k: v for k, v in raw.items() if isinstance(v, dict) and "ts" in v}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key_for(snapshot: Any, *extra: Any) -> str:
        return payload_hash({"s": snapshot, "x": list(extra)}) if extra else payload_hash(snapshot)

    def get(self, key: str) -> Any | None:
        row = self._data.get(key)
        if row is None:
            self.misses += 1
            return None
        if self._clock() - float(row["ts"]) > self.ttl_s:
            self._data.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        return row.get("value")

    def put(self, key: str, value: Any) -> None:
        self._data[key] = {"ts": self._clock(), "value": value}
        if len(self._data) > self.max_entries:
            for k in sorted(self._data, key=lambda k: self._data[k]["ts"])[: len(self._data) - self.max_entries]:
                self._data.pop(k, None)
        self._persist()

    def purge_expired(self) -> int:
        now = self._clock()
        dead = [k for k, v in self._data.items() if now - float(v["ts"]) > self.ttl_s]
        for k in dead:
            self._data.pop(k, None)
        if dead:
            self._persist()
        return len(dead)

    def _persist(self) -> None:
        if self.path is not None:
            atomic_write_json(self.path, self._data)

    def __len__(self) -> int:
        return len(self._data)


__all__ = ["DEFAULT_PRICES", "ModelPrice", "PriceTable", "estimate_cost", "LLMBudget", "BudgetState",
           "CircuitBreaker", "SemanticCache"]
