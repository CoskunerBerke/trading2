"""Kimlikler: zaman sıralı benzersiz id'ler (ULID benzeri) ve deterministik hash id'ler."""
from __future__ import annotations

import hashlib
import json
import os
import time
from decimal import Decimal
from typing import Any

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford


def _b32(n: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_B32[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str = "") -> str:
    """Zaman sıralı 26 karakterlik ULID benzeri kimlik (10 zaman + 16 rastgele). Örn: `pos_01J...`."""
    ts_ms = int(time.time() * 1000)
    rnd = int.from_bytes(os.urandom(10), "big")
    core = _b32(ts_ms, 10) + _b32(rnd, 16)
    return f"{prefix}_{core}" if prefix else core


def run_id_now() -> str:
    return new_id("run")


def _canon(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, float):
        return repr(obj)
    if isinstance(obj, dict):
        return {str(k): _canon(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_canon(x) for x in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_canon(x) for x in obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def payload_hash(obj: Any) -> str:
    """Kanonik JSON'un sha256'sı — idempotent yazım ve değişiklik tespiti için."""
    s = json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def stable_id(*parts: Any, length: int = 16) -> str:
    """Aynı girdiler için hep aynı id (ör. canvas düğüm id'si, snapshot id, clientOrderId parçası)."""
    h = hashlib.sha256("|".join(str(_canon(p)) for p in parts).encode("utf-8")).hexdigest()
    return h[:length]
