"""Kooperatif (uygulama seviyesi) durdurma — konsol sinyaline/shell mirasına bağımlı değil.

Mekanizma (dosya tabanlı, atomik, token doğrulamalı):
- Çalışan worker/dashboard başlangıçta `state/.<kind>_instance.json` yazar: {pid, token, started_at, kind}; çıkışta siler.
- `python -m tradingbot stop` → canlı instance'ı doğrular (pid yaşıyor mu, dosya var mı) ve `state/.stop_request.json`
  içine hedef token'ları atomik yazar. Yanlış/eski token'lı istek yok sayılır (başka instance durdurulamaz).
- Worker `StopWatcher.requested()` ile kısa aralıklarla kontrol eder → yeni giriş kabulü kapanır, mevcut tur/atomik defter
  işlemi tamamlanır, state/log flush edilir, yalnız KENDİ lock'u kaldırılır, istek tüketilir.
- Varsayılan force yok; timeout'ta dürüst rapor. `--force` yalnız kesin PID'ye normal sonlandırma uygular (graceful sayılmaz).
"""
from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, read_json

STOP_REQUEST = ".stop_request.json"
KINDS = ("worker", "dashboard")


def instance_path(state_dir: Path | str, kind: str) -> Path:
    return Path(state_dir) / f".{kind}_instance.json"


def pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if k.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == 259                       # STILL_ACTIVE
            return False
        finally:
            k.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class InstanceRecord:
    """Çalışan sürecin kimliği (pid + rastgele token). `register()` atomik yazar, `unregister()` yalnız kendi kaydını siler."""

    def __init__(self, state_dir: Path | str, kind: str, extra: dict | None = None):
        assert kind in KINDS, kind
        self.state_dir, self.kind = Path(state_dir), kind
        self.pid = os.getpid()
        self.token = secrets.token_hex(16)
        self.extra = extra or {}
        self.path = instance_path(state_dir, kind)

    def register(self) -> Path:
        atomic_write_json(self.path, {"kind": self.kind, "pid": self.pid, "token": self.token, "started_at": iso(), **self.extra}, indent=None)
        return self.path

    def unregister(self) -> bool:
        cur = read_instance(self.state_dir, self.kind)
        if cur and cur.get("token") == self.token and int(cur.get("pid", -1)) == self.pid:
            try:
                self.path.unlink()
                return True
            except OSError:
                return False
        return False


def read_instance(state_dir: Path | str, kind: str) -> dict | None:
    d = read_json(instance_path(state_dir, kind), default=None)
    return d if isinstance(d, dict) and d.get("token") and d.get("pid") else None


def instance_status(state_dir: Path | str, kind: str) -> dict:
    """{'kind','present','alive','pid','stale'}: kayıt var ama pid ölü → stale (dosya SİLİNMEZ; yalnız raporlanır)."""
    rec = read_instance(state_dir, kind)
    if not rec:
        return {"kind": kind, "present": False, "alive": False, "pid": None, "stale": False}
    alive = pid_alive(int(rec["pid"]))
    return {"kind": kind, "present": True, "alive": alive, "pid": int(rec["pid"]), "stale": not alive, "token": rec["token"]}


class StopWatcher:
    """Süreç içi: `requested()` ucuz dosya kontrolü (mtime cache), `consume()` isteği tüketir (yalnız kendi token'ı için)."""

    def __init__(self, state_dir: Path | str, token: str, min_interval_s: float = 1.0):
        self.path = Path(state_dir) / STOP_REQUEST
        self.token = token
        self.min_interval_s = min_interval_s
        self._last_check = 0.0
        self._flag = False

    def requested(self) -> bool:
        if self._flag:
            return True
        now = time.monotonic()
        if now - self._last_check < self.min_interval_s:
            return False
        self._last_check = now
        try:
            if not self.path.exists():
                return False
            d = read_json(self.path, default=None)
        except Exception:  # noqa: BLE001 — bozuk istek dosyası durdurma nedeni değildir
            return False
        if isinstance(d, dict) and self.token in (d.get("tokens") or []):
            self._flag = True
        return self._flag

    def consume(self) -> None:
        try:
            d = read_json(self.path, default=None)
        except Exception:  # noqa: BLE001
            return
        if not isinstance(d, dict):
            return
        toks = [t for t in (d.get("tokens") or []) if t != self.token]
        try:
            if toks:
                atomic_write_json(self.path, {**d, "tokens": toks}, indent=None)
            else:
                self.path.unlink()
        except OSError:
            pass


def request_stop(state_dir: Path | str, targets: tuple[str, ...] = KINDS) -> dict[str, Any]:
    """Canlı instance'lar için durdurma isteği yaz (idempotent). Dönen: {kind: status, 'requested': [...]}"""
    state_dir = Path(state_dir)
    out: dict[str, Any] = {}
    tokens: list[str] = []
    for kind in targets:
        st = instance_status(state_dir, kind)
        out[kind] = st
        if st["present"] and st["alive"]:
            tokens.append(st["token"])
    p = state_dir / STOP_REQUEST
    prev = read_json(p, default=None) if p.exists() else None
    prev_tokens = list((prev or {}).get("tokens") or []) if isinstance(prev, dict) else []
    merged = list(dict.fromkeys(prev_tokens + tokens))
    if merged and merged != prev_tokens:
        atomic_write_json(p, {"requested_at": iso(), "requested_by_pid": os.getpid(), "tokens": merged}, indent=None)
    out["requested"] = tokens
    out["already_pending"] = bool(prev_tokens) and merged == prev_tokens
    return out


def wait_stopped(state_dir: Path | str, targets: tuple[str, ...], timeout_s: float, poll_s: float = 0.5) -> dict[str, str]:
    """Her hedef için: 'stopped' (instance kaydı kalktı ya da pid öldü), 'timeout', 'absent'."""
    state_dir = Path(state_dir)
    res: dict[str, str] = {}
    initial = {k: instance_status(state_dir, k) for k in targets}
    for k, st in initial.items():
        if not (st["present"] and st["alive"]):
            res[k] = "absent" if not st["present"] else "stale"
    t0 = time.monotonic()
    pending = [k for k in targets if k not in res]
    while pending and time.monotonic() - t0 < timeout_s:
        for k in list(pending):
            st = instance_status(state_dir, k)
            if not st["present"] or not st["alive"] or st.get("token") != initial[k].get("token"):
                res[k] = "stopped"
                pending.remove(k)
        if pending:
            time.sleep(poll_s)
    for k in pending:
        res[k] = "timeout"
    return res


def terminate_pid(pid: int) -> bool:
    """Yalnız açık --force ile: kesin PID'ye normal sonlandırma (Windows TerminateProcess / POSIX SIGTERM). Graceful DEĞİLDİR."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x0001, False, int(pid))         # PROCESS_TERMINATE
            if not h:
                return False
            try:
                return bool(k.TerminateProcess(h, 1))
            finally:
                k.CloseHandle(h)
        import signal
        os.kill(int(pid), signal.SIGTERM)
        return True
    except OSError:
        return False


__all__ = ["InstanceRecord", "StopWatcher", "request_stop", "wait_stopped", "instance_status", "read_instance", "pid_alive",
           "terminate_pid", "STOP_REQUEST", "KINDS"]
