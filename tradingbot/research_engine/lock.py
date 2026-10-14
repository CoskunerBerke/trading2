"""Motor kilitleri (§2.4) — `data/research/locks/data.lock` ve `analysis.lock`, `SingletonLock` deseni (fcntl).

* İki AYRI kilit vardır: `data.lock` (veri birimi, ilk doldurma, elle `engine-data`; P1a'da yalnız TANIMLI) ve
  `analysis.lock` (gece birimi ve elle `engine-night`). Tek bir motor-geneli kilit olsaydı saatlerce süren ilk
  doldurma gece birimini `SKIPPED_LOCKED` yapar, kapanış arşivi durur ve rotasyon kalıcı veri kaybına yol açabilirdi.
* Kilidi alamayan birim BEKLEMEZ: `SKIPPED_LOCKED` sonucunu yazar ve 0 koduyla çıkar (çağıranın işi).
* Motor worker kilidini (`state/tradingbot.lock`) HİÇ almaz; bu modül yalnız araştırma kökündeki kilitlere dokunur.
* Kilit işletim sistemi tarafından tutulur: çöken süreçten sonra bayat kilit kalmaz (dosya kalsa da kilit serbesttir).

`tradingbot.ops.lock.SingletonLock` ile aynı desendir; burada yeniden yazılır, çünkü `tradingbot.ops` paketinin
`__init__`i motorun ihtiyacı olmayan modülleri (yedek, doctor, bildirim) de yükler — gece biriminin import grafiği
küçük tutulur.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # POSIX
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows (motor yalnız Linux VPS'te çalışır; testler için zararsız yedek)
    fcntl = None  # type: ignore

#: Kilit alınamadığında yazılan sonuç kodu (§2.4).
SKIPPED_LOCKED = "SKIPPED_LOCKED"


class LockBusy(RuntimeError):
    """Kilit başka bir süreç tarafından tutuluyor."""


class EngineLock:
    """Bloklamayan dosya kilidi. Kullanım: `if lk.try_acquire(): ... finally: lk.release()` ya da `with lk:`."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._fh = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def try_acquire(self) -> bool:
        """Kilidi almayı BİR kez dener; alınamazsa False (beklemez)."""
        if self._fh is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        try:
            fh.seek(0)
            fh.truncate(0)
            fh.write(f"{os.getpid()}\n".encode("ascii"))
            fh.flush()
        except OSError:
            pass
        self._fh = fh
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    def read_pid(self) -> int | None:
        try:
            txt = self.path.read_text(encoding="ascii", errors="ignore").strip()
            return int(txt) if txt else None
        except (OSError, ValueError):
            return None

    def __enter__(self) -> "EngineLock":
        if not self.try_acquire():
            raise LockBusy(f"kilit tutuluyor: {self.path} (pid={self.read_pid() or '?'})")
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def analysis_lock(paths) -> EngineLock:
    """Gece birimi ve elle `engine-night` kilidi."""
    return EngineLock(paths.analysis_lock)


def data_lock(paths) -> EngineLock:
    """Veri birimi kilidi (P1b). P1a'da yalnız tanımlıdır; hiçbir P1a yolu almaz."""
    return EngineLock(paths.data_lock)


__all__ = ["EngineLock", "LockBusy", "SKIPPED_LOCKED", "analysis_lock", "data_lock"]
