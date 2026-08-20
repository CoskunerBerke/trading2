"""Tek yetkili worker (split-brain koruması) — aynı state'e iki makinenin worker yazmasını engeller.

`state/worker_authority.json` = {"host": <yetkili makine>, "claimed_at": iso, "note": ...}.
* Dosya yoksa: herkes başlayabilir (geriye uyumlu; SingletonLock zaten aynı makinede ikinci süreci engeller).
* Dosya varsa: yalnız `host` alanı bu makinenin hostname'iyle eşleşiyorsa worker başlar; aksi halde
  fail-closed reddedilir (VPS'e geçişte yerel PC worker'ının yanlışlıkla başlamasını önler).
Devir: yeni makinede `python -m tradingbot authority --claim` (state ile birlikte taşınan dosyayı
yeniden yazar); `--release` markörü kaldırır, `--show` durumu basar.
"""
from __future__ import annotations

import socket
from pathlib import Path

from ..core import atomic_write_json, iso, read_json

AUTHORITY_FILE = "worker_authority.json"


def current_host() -> str:
    return socket.gethostname()


def read_authority(state_dir: Path | str) -> dict | None:
    d = read_json(Path(state_dir) / AUTHORITY_FILE, default=None)
    return d if isinstance(d, dict) and d.get("host") else None


def claim(state_dir: Path | str, host: str | None = None, note: str = "") -> dict:
    d = {"host": host or current_host(), "claimed_at": iso(), "note": note}
    atomic_write_json(Path(state_dir) / AUTHORITY_FILE, d)
    return d


def release(state_dir: Path | str) -> bool:
    p = Path(state_dir) / AUTHORITY_FILE
    if p.exists():
        p.unlink()
        return True
    return False


def check(state_dir: Path | str, host: str | None = None) -> tuple[bool, str]:
    """(izin, gerekçe). Dosya yok → izin; var ve host eşleşiyor → izin; aksi halde red (fail-closed)."""
    d = read_authority(state_dir)
    me = host or current_host()
    if d is None:
        return True, "yetki markörü yok (serbest)"
    if str(d.get("host")) == me:
        return True, f"yetki bu makinede ({me}, {d.get('claimed_at', '')})"
    return False, f"yetkili worker başka makinede: {d.get('host')} (claimed_at {d.get('claimed_at', '')}); bu makine: {me}"


__all__ = ["AUTHORITY_FILE", "check", "claim", "current_host", "read_authority", "release"]
