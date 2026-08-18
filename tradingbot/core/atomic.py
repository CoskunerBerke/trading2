"""Atomik dosya yazımı: geçici dosya + fsync + os.replace. Crash sırasında yarım JSON kalmaz.

Ek olarak `read_json` bozuk dosyada `.bak` yedeğine düşer ve asla sessizce boş state döndürmez
(çağıran `default` vermezse StorageError fırlatır).
"""
from __future__ import annotations

import json
import os
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import StorageError


def _default(o: Any):
    if isinstance(o, Decimal):
        return format(o, "f")
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "to_dict"):
        return o.to_dict()
    return str(o)


def atomic_write_bytes(path: Path | str, data: bytes, *, keep_backup: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        if keep_backup and path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        finally:
            raise StorageError(f"atomik yazım başarısız: {path}: {exc}") from exc
    return path


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8", keep_backup: bool = False,
                      skip_if_unchanged: bool = False) -> bool:
    """Metni atomik yazar. `skip_if_unchanged=True` ise içerik aynıysa dosyaya dokunmaz (Obsidian/git churn azalır).
    Dönen: yazıldı mı."""
    path = Path(path)
    if skip_if_unchanged and path.exists():
        try:
            if path.read_text(encoding=encoding) == text:
                return False
        except OSError:
            pass
    atomic_write_bytes(path, text.encode(encoding), keep_backup=keep_backup)
    return True


def atomic_write_json(path: Path | str, obj: Any, *, indent: int | None = 1, keep_backup: bool = False,
                      skip_if_unchanged: bool = False) -> bool:
    text = json.dumps(obj, indent=indent, ensure_ascii=False, default=_default, sort_keys=False)
    return atomic_write_text(path, text, keep_backup=keep_backup, skip_if_unchanged=skip_if_unchanged)


_MISSING = object()


def read_json(path: Path | str, default: Any = _MISSING) -> Any:
    """JSON oku; bozuksa `.bak` dene; o da yoksa default döndür ya da StorageError fırlat.
    Bozuk ana dosya `<name>.corrupt-<n>` olarak kenara alınır (silinmez)."""
    path = Path(path)
    if not path.exists():
        if default is _MISSING:
            raise StorageError(f"dosya yok: {path}")
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        bak = path.with_name(path.name + ".bak")
        n = 1
        while path.with_name(f"{path.name}.corrupt-{n}").exists():
            n += 1
        try:
            shutil.copy2(path, path.with_name(f"{path.name}.corrupt-{n}"))
        except OSError:
            pass
        if bak.exists():
            try:
                return json.loads(bak.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        if default is _MISSING:
            raise StorageError(f"bozuk JSON ve yedek yok: {path}: {exc}") from exc
        return default
