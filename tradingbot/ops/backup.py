"""Yedekleme / geri yükleme.

`run_backup(state_dir, backups_dir, kind)`:
  1. state içindeki *.db dosyaları sqlite `backup()` API'siyle tutarlı kopyalanır
  2. *.json / *.jsonl / *.lock dışı düz dosyalar kopyalanır (atomik: önce staging dizini)
  3. staging → `backups_dir/<kind>/tradingbot-<kind>-<ts>.tar.gz` + `.sha256` yan dosyası (isteğe bağlı vault dahil)
  4. saklama: hourly 24 / daily 7 / weekly 4 (kind başına en yeni N)
`verify_backup(archive)` sha256 + tar bütünlüğü; `restore_backup(archive, state_dir, dry_run)` doğrular, geçici dizine
açar, mevcut state'i `state.pre-restore-<ts>` olarak kenara alır ve yenisini yerine koyar (asla silmez).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core import StorageError

KINDS = ("hourly", "daily", "weekly", "manual")
_SKIP_SUFFIXES = {".lock", ".tmp"}
_SKIP_PREFIXES = ("state.pre-restore-",)


@dataclass
class BackupResult:
    kind: str
    archive: str
    sha256: str
    files: int
    bytes: int
    created_at: str
    pruned: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sqlite_backup(src: Path, dst: Path) -> None:
    con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(str(dst))
        try:
            con.backup(out)
        finally:
            out.close()
    finally:
        con.close()


def _copy_tree_state(state_dir: Path, staging: Path) -> tuple[int, int]:
    """state → staging (db'ler .backup ile, diğerleri kopya). Dönen: (dosya sayısı, bayt)."""
    n = b = 0
    for p in sorted(state_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(state_dir)
        if any(str(rel).startswith(pre) for pre in _SKIP_PREFIXES) or p.suffix in _SKIP_SUFFIXES or ".tmp-" in p.name:
            continue
        if p.suffix in (".db-wal", ".db-shm", ".db-journal"):
            continue
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix in (".db", ".sqlite", ".sqlite3"):
            try:
                _sqlite_backup(p, dst)
            except sqlite3.Error as exc:
                raise StorageError(f"sqlite yedeği başarısız: {p}: {exc}") from exc
        else:
            shutil.copy2(p, dst)
        n += 1
        b += dst.stat().st_size
    return n, b


def _prune(kind_dir: Path, keep: int) -> list[str]:
    archives = sorted(kind_dir.glob("tradingbot-*.tar.gz"), key=lambda p: p.name)
    removed: list[str] = []
    for old in archives[:-keep] if keep > 0 else archives:
        for extra in (old, old.with_name(old.name + ".sha256")):
            try:
                extra.unlink()
                removed.append(str(extra))
            except FileNotFoundError:
                pass
    return removed


def run_backup(state_dir: Path | str, backups_dir: Path | str, kind: str = "hourly", *, keep_hourly: int = 24,
               keep_daily: int = 7, keep_weekly: int = 4, keep_manual: int = 10, vault_dir: Path | str | None = None,
               include_vault: bool = False) -> BackupResult:
    state_dir, backups_dir = Path(state_dir), Path(backups_dir)
    if kind not in KINDS:
        raise ValueError(f"bilinmeyen yedek türü: {kind} (geçerli: {KINDS})")
    if not state_dir.exists():
        raise StorageError(f"state dizini yok: {state_dir}")
    kind_dir = backups_dir / kind
    kind_dir.mkdir(parents=True, exist_ok=True)
    ts = _ts()
    archive = kind_dir / f"tradingbot-{kind}-{ts}.tar.gz"
    tmp_root = Path(tempfile.mkdtemp(prefix="tbbackup-", dir=str(backups_dir)))
    try:
        staging = tmp_root / "state"
        staging.mkdir()
        n, b = _copy_tree_state(state_dir, staging)
        vault_n = 0
        if include_vault and vault_dir and Path(vault_dir).exists():
            vdst = tmp_root / "vault"
            shutil.copytree(vault_dir, vdst, ignore=shutil.ignore_patterns(".git", ".obsidian", "*.png", ".trash"))
            vault_n = sum(1 for _ in vdst.rglob("*") if _.is_file())
        tmp_archive = tmp_root / "archive.tar.gz"
        with tarfile.open(tmp_archive, "w:gz") as tar:
            tar.add(staging, arcname="state")
            if vault_n:
                tar.add(tmp_root / "vault", arcname="vault")
        digest = _sha256_file(tmp_archive)
        os.replace(tmp_archive, archive)
        archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    keep = {"hourly": keep_hourly, "daily": keep_daily, "weekly": keep_weekly, "manual": keep_manual}[kind]
    pruned = _prune(kind_dir, keep)
    return BackupResult(kind=kind, archive=str(archive), sha256=digest, files=n, bytes=archive.stat().st_size,
                        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), pruned=pruned)


def verify_backup(archive: Path | str) -> dict[str, Any]:
    """sha256 yan dosyası (varsa) ve tar okunabilirliği. Dönen: {ok, sha256, expected, members, error}."""
    archive = Path(archive)
    out: dict[str, Any] = {"ok": False, "archive": str(archive), "sha256": "", "expected": "", "members": 0, "error": ""}
    if not archive.exists():
        out["error"] = "arşiv yok"
        return out
    out["sha256"] = _sha256_file(archive)
    side = archive.with_name(archive.name + ".sha256")
    if side.exists():
        try:
            out["expected"] = side.read_text(encoding="utf-8").split()[0]
        except (OSError, IndexError):
            out["expected"] = ""
        if out["expected"] and out["expected"] != out["sha256"]:
            out["error"] = "sha256 uyuşmuyor"
            return out
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for m in members:
                name = m.name.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    out["error"] = f"güvensiz üye: {m.name}"
                    return out
            out["members"] = len(members)
    except (tarfile.TarError, OSError, EOFError) as exc:
        out["error"] = f"tar okunamadı: {exc}"
        return out
    out["ok"] = True
    return out


def latest_backup(backups_dir: Path | str, kind: str | None = None) -> Path | None:
    root = Path(backups_dir)
    if not root.exists():
        return None
    pattern = f"{kind}/tradingbot-*.tar.gz" if kind else "*/tradingbot-*.tar.gz"
    cands = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def restore_backup(archive: Path | str, state_dir: Path | str, dry_run: bool = False) -> dict[str, Any]:
    """Doğrula → geçici dizine aç → `state_dir` → `state.pre-restore-<ts>` → yeni state yerine. Vault üyeleri
    (varsa) `state_dir.parent/vault.restored-<ts>` altına açılır (mevcut vault'a dokunulmaz)."""
    archive, state_dir = Path(archive), Path(state_dir)
    ver = verify_backup(archive)
    if not ver["ok"]:
        raise StorageError(f"yedek doğrulanamadı: {ver['error']}")
    with tarfile.open(archive, "r:gz") as tar:
        names = [m.name for m in tar.getmembers()]
    if dry_run:
        return {"ok": True, "dry_run": True, "members": names, "state_dir": str(state_dir), "verify": ver}
    ts = _ts()
    parent = state_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="tbrestore-", dir=str(parent)))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            try:
                tar.extractall(tmp_root, filter="data")  # py>=3.12
            except TypeError:  # pragma: no cover
                tar.extractall(tmp_root)
        new_state = tmp_root / "state"
        if not new_state.exists():
            raise StorageError("arşivde 'state/' yok")
        pre = parent / f"state.pre-restore-{ts}"
        if state_dir.exists():
            os.replace(state_dir, pre)
        else:
            pre = None
        os.replace(new_state, state_dir)
        vault_out = None
        if (tmp_root / "vault").exists():
            vault_out = parent / f"vault.restored-{ts}"
            os.replace(tmp_root / "vault", vault_out)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return {"ok": True, "dry_run": False, "state_dir": str(state_dir), "previous": str(pre) if pre else None,
            "vault_restored_to": str(vault_out) if vault_out else None, "members": names, "verify": ver}


__all__ = ["run_backup", "verify_backup", "restore_backup", "latest_backup", "BackupResult", "KINDS"]
