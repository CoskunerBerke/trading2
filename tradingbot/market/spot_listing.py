"""Binance SPOT listeleme önbelleği — `universe.require_spot_listing` kapısının veri kaynağı.

KANIT ONARIMI V1 (2026-09-09): 239 aday kurulumu vadeli fiyatlarla etiketlendiğinde, Binance
spot'ta LİSTELİ OLMAYAN (yalnız vadelide işlem gören, tokenize hisse/emtia ağırlıklı) kesit
87 kurulumda −0,24R (%95 üst sınır −0,035) verdi; spot'ta listeli kripto LONG kesiti ise
+0,155R ile sıfır civarında kaldı. Bu modül "sembol spot'ta listeli mi?" sorusunu resmi
exchangeInfo'dan cevaplar ve cevabı diske önbellekler.

Sözleşme:
  * `is_listed(symbol)` üç değerlidir: True / False / None. None = veri YOK. Kapı açıkken None
    fail-closed'dır (yeni giriş açılmaz); bu sessiz bir "geç" DEĞİLDİR ve kod `NOT_SPOT_LISTED`
    ile günlüğe düşer.
  * Yenileme arızası ESKİ önbelleği korur (bayat ama kullanılabilir). Boş liste dönen bir
    yenileme başarısız sayılır ve önbelleği EZMEZ.
  * Yazım atomiktir (geçici dosya + `os.replace`).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .providers import to_raw

log = logging.getLogger(__name__)

SCHEMA_VERSION = "spot_listing_v1"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


class SpotListing:
    """Binance spot `exchangeInfo` içindeki TRADING sembollerin ham adları (`ETHUSDT`)."""

    def __init__(self, path: Path | str, *, ttl_minutes: int = 1440):
        self.path = Path(path)
        self.ttl_s = max(60, int(ttl_minutes) * 60)
        self.fetched_at: str | None = None
        self.source: str | None = None
        self._set: set[str] = set()
        self.load()

    # ------------------------------------------------------------------ durum
    @property
    def available(self) -> bool:
        return bool(self._set)

    @property
    def size(self) -> int:
        return len(self._set)

    def age_s(self, now: float | None = None) -> float | None:
        t = _parse_iso(self.fetched_at)
        if t is None:
            return None
        return max(0.0, (now if now is not None else time.time()) - t)

    def is_stale(self, now: float | None = None) -> bool:
        """Veri yoksa ya da TTL aşıldıysa True."""
        if not self.available:
            return True
        age = self.age_s(now)
        return age is None or age > self.ttl_s

    def is_listed(self, symbol: str) -> bool | None:
        """True/False; veri yoksa None (çağıran fail-closed davranmalı)."""
        if not self.available:
            return None
        return to_raw(symbol) in self._set

    # ------------------------------------------------------------------ kalıcılık
    def load(self) -> None:
        try:
            if not self.path.exists():
                return
            d = json.loads(self.path.read_text(encoding="utf-8"))
            syms = d.get("symbols") or []
            if not isinstance(syms, list):
                return
            self._set = {str(s).upper() for s in syms if s}
            self.fetched_at = d.get("fetched_at")
            self.source = d.get("source")
        except (OSError, ValueError) as exc:
            log.warning("spot_listing okunamadı (%s): %s — veri YOK sayılıyor", self.path, exc)
            self._set = set()
            self.fetched_at = None

    def _write(self) -> None:
        doc = {"schema_version": SCHEMA_VERSION, "fetched_at": self.fetched_at, "source": self.source,
               "n": len(self._set), "symbols": sorted(self._set)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ yenileme
    def refresh(self, provider: Any, *, now: float | None = None) -> dict:
        """`provider.exchange_info()` → TRADING sembol kümesi. Boş/hatalı sonuç önbelleği EZMEZ."""
        try:
            rows = provider.exchange_info()
        except Exception as exc:  # noqa: BLE001 — arıza: eski önbellek korunur
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "n": self.size}
        syms: set[str] = set()
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            if str(r.get("status") or "").upper() != "TRADING":
                continue
            raw = str(r.get("symbol") or "").upper()
            if raw:
                syms.add(raw)
        if not syms:
            return {"ok": False, "error": "exchangeInfo boş ya da TRADING sembol yok", "n": self.size}
        self._set = syms
        self.fetched_at = _iso(now if now is not None else time.time())
        self.source = getattr(provider, "name", type(provider).__name__)
        try:
            self._write()
        except OSError as exc:
            log.warning("spot_listing yazılamadı (%s): %s — bellekte kullanılıyor", self.path, exc)
            return {"ok": True, "n": len(syms), "persisted": False, "error": str(exc)}
        return {"ok": True, "n": len(syms), "persisted": True}


__all__ = ["SCHEMA_VERSION", "SpotListing"]
