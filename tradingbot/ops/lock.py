"""Tekil çalışma kilidi — aynı state dizininde iki motor aynı anda çalışmasın.

Windows'ta `msvcrt.locking`, POSIX'te `fcntl.flock`; kilit dosyasına PID yazılır. Kilit işletim sistemi
tarafından tutulduğu için çöken süreç sonrası bayat kilit sorunu olmaz (dosya kalsa da kilit serbesttir).
"""
from __future__ import annotations

import os
from pathlib import Path

from ..core import TradingBotError

try:  # Windows
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore
try:  # POSIX
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore


class AlreadyRunningError(TradingBotError):
    """Kilit başka bir süreç tarafından tutuluyor."""


# Windows'ta bayt aralığı kilidi: PID metninin dışında, dosya sonunun ötesinde bir bayt kilitlenir
# (böylece diğer süreçler PID'yi okuyabilir; kilitli bölge okunamaz).
_LOCK_OFFSET = 1 << 30


class SingletonLock:
    """Kullanım: `with SingletonLock(state_dir / "tradingbot.lock"): ...` ya da `acquire()/release()`."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._fh = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def acquire(self) -> "SingletonLock":
        if self._fh is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if msvcrt is not None:
                fh.seek(_LOCK_OFFSET)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            other = self.read_pid()
            fh.close()
            raise AlreadyRunningError(f"kilit tutuluyor: {self.path} (pid={other or '?'})") from exc
        # kilit alındı → PID yaz (aynı tanıtıcı üzerinden yazmak serbest)
        try:
            fh.seek(0)
            fh.truncate(0)
            fh.write(f"{os.getpid()}\n".encode("ascii"))
            fh.flush()
        except OSError:
            pass
        self._fh = fh
        return self

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if msvcrt is not None:
                fh.seek(_LOCK_OFFSET)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            elif fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    def read_pid(self) -> int | None:
        try:
            txt = self.path.read_text(encoding="ascii", errors="ignore").strip()
            return int(txt) if txt else None
        except (OSError, ValueError):
            return None

    def is_locked_by_other(self) -> bool:
        """Kilidi almadan durum sorgusu: kısa süre almayı dener, alabilirse serbest bırakır."""
        if self._fh is not None:
            return False
        probe = SingletonLock(self.path)
        try:
            probe.acquire()
        except AlreadyRunningError:
            return True
        probe.release()
        return False

    def __enter__(self) -> "SingletonLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


__all__ = ["SingletonLock", "AlreadyRunningError"]
