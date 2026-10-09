"""Yedekleme / geri yükleme.

`run_backup(state_dir, backups_dir, kind)`:
  1. state içindeki *.db dosyaları sqlite `backup()` API'siyle tutarlı kopyalanır
  2. *.json / *.jsonl / *.lock dışı düz dosyalar kopyalanır (atomik: önce staging dizini)
  3. staging → `backups_dir/<kind>/tradingbot-<kind>-<ts>.tar.gz` + `.sha256` yan dosyası (isteğe bağlı vault dahil)
  4. saklama: hourly 24 / daily 7 / weekly 4 (kind başına en yeni N)
`verify_backup(archive)` sha256 + tar bütünlüğü; `restore_backup(archive, state_dir, dry_run)` doğrular, geçici dizine
açar, mevcut state'i `state.pre-restore-<ts>` olarak kenara alır ve yenisini yerine koyar (asla silmez).

ORTAK DENEYİM ARŞİVİ (2026-09-29, inceleme bulgusu): `shared_experience/archive/segments/` sınırsız büyüyen, DEĞİŞMEZ
(sha256'lı, içerik adlı) segmentlerdir. Saatlik yedek onları yalnız günün İLK saatinde (UTC 00) taşır; diğer 23 saatlik yedek
manifest + sıcak dosya + imleçle yetinir (her kopya bütün arşivi taşıyordu: 35 kopya × arşiv). Elle/günlük/haftalık yedek
hep taşır. Segmentsiz bir saatlik yedekten geri yüklemede segmentler en yeni UTC-00 (ya da elle) yedekten kopyalanır —
dosyalar değişmez olduğu için daha yeni bir yedekteki segment kümesi eskisini kapsar.

GÖLGE DANIŞMAN (2026-09-29): tavsiye deposunun arşiv segmentleri (`shared_experience/advice/archive/segments/`) aynı kuralla
yalnız UTC 00 saatlik yedeğinde taşınır; türetilmiş ve yeniden kurulabilir anlık görüntü (`advice/advisor_state.v1.gz`)
saatlik yedeğe HİÇ alınmaz. Günlük/haftalık/elle yedek her şeyi taşır.

SEGMENT GERİ KOPYASI (2026-09-30, inceleme bulgusu): `restore_backup` geri yüklenen manifestlerin listelediği ama state'te
olmayan (ya da sha256'sı tutmayan) ortak deneyim / tavsiye segmentlerini, kenara alınan önceki state'ten
(`state.pre-restore-<ts>`) sha256 doğrulamasıyla geri kopyalar (segmentler değişmez ve içerik adlıdır; aynı ad + aynı sha256 =
aynı bayt). Hâlâ eksik kalanlar sonuçta `xp_segments.missing` olarak listelenir; danışman o segmentte ATLAMAZ, bekler
(`WAITING_SEGMENTS`) — eksikler en yeni UTC-00 / günlük yedekten elle kopyalanır.

MAKİNE İŞARETLERİ (2026-10-05): `MACHINE_MARKERS` (kanıt alt sürecinin fork işaret dosyası) veri değil, bu makinenin
çalışma durumudur: yedeğe alınmaz; geri yüklemede mevcut state'teki kopya yeni state'e taşınır.
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
#: MAKİNEYE ÖZGÜ çalışma işaretleri (veri değil): yedeğe GİRMEZ; geri yüklemede mevcut state'teki kopya korunur.
#: `pattern_evidence_fork.marker` (2026-10-05): kanıt alt sürecinin fork'u bu makinede asılıp worker deadman'le
#: sonlandırıldı → alt süreç bu makinede kapalı. Yedekten dönmesi alt süreci sessizce kapalı tutar; geri yüklemede
#: kaybolması asılmayı yeniden denetir. Ad `patterns.evidence_child.MARKER_NAME` ile test eşitliğine bağlı (paket içe
#: aktarılmaz).
MACHINE_MARKERS = ("pattern_evidence_fork.marker",)
#: Saatlik yedeğin yalnız UTC 00 saatinde taşıdığı değişmez segment klasörü (2026-09-29; `shared_experience.state_dir`
#: varsayılanı). Başka bir `state_dir` seçilirse klasör her yedekte taşınır (davranış eskisi gibi).
XP_SEGMENTS_REL = "shared_experience/archive/segments/"
XP_SEGMENTS_HOUR_UTC = 0
#: Gölge danışman (2026-09-29): tavsiye arşiv segmentleri (saatlikte yalnız UTC 00) ve türetilmiş anlık görüntü (saatlikte
#: HİÇ). Yol sabitleri `shared_experience.advice_store` / `advisor_live` ile test eşitliğine bağlı (paket içe aktarılmaz).
XP_ADVICE_SEGMENTS_REL = "shared_experience/advice/archive/segments/"
XP_ADVISOR_STATE_REL = "shared_experience/advice/advisor_state.v1.gz"
#: Geri yüklemede segment geri kopyası yapılan arşivler (manifest = `<kök>/manifest.json`, segmentler `<kök>/segments/`).
XP_ARCHIVE_ROOTS_REL = ("shared_experience/archive", "shared_experience/advice/archive")


@dataclass
class BackupResult:
    kind: str
    archive: str
    sha256: str
    files: int
    bytes: int
    created_at: str
    pruned: list[str] = field(default_factory=list)
    #: (2026-09-29) bu yedeğe ALINMAYAN ortak deneyim segmenti sayısı (saatlik yedek, UTC 00 dışı).
    skipped_xp_segments: int = 0

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


def _copy_tree_state(state_dir: Path, staging: Path, *, skip_dirs: tuple[str, ...] = (),
                     skipped: list[int] | None = None) -> tuple[int, int]:
    """state → staging (db'ler .backup ile, diğerleri kopya). Dönen: (dosya sayısı, bayt). `skip_dirs` (göreli, `/` ile
    biten) altındaki dosyalar alınmaz ve `skipped[0]`a sayılır."""
    n = b = 0
    for p in sorted(state_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(state_dir)
        if any(str(rel).startswith(pre) for pre in _SKIP_PREFIXES) or p.suffix in _SKIP_SUFFIXES or ".tmp-" in p.name:
            continue
        if rel.as_posix() in MACHINE_MARKERS:
            continue
        if skip_dirs and any(rel.as_posix().startswith(d) for d in skip_dirs):
            if skipped is not None:
                skipped[0] += 1
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
               include_vault: bool = False, xp_segments: bool | None = None, now: datetime | None = None) -> BackupResult:
    """`xp_segments` (2026-09-29): None → saatlik yedekte yalnız UTC `XP_SEGMENTS_HOUR_UTC` saatinde, diğer türlerde her
    zaman ortak deneyim segmentleri alınır; True/False zorlar. `now` yalnız bu kural içindir (test)."""
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
        if xp_segments is None:
            hour = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).hour
            xp_segments = kind != "hourly" or hour == XP_SEGMENTS_HOUR_UTC
        skipped = [0]
        skip_dirs: tuple[str, ...] = () if xp_segments else (XP_SEGMENTS_REL, XP_ADVICE_SEGMENTS_REL)
        if kind == "hourly":
            skip_dirs = skip_dirs + (XP_ADVISOR_STATE_REL,)     # türetilmiş; saatlikte ASLA (UTC 00 dahil)
        n, b = _copy_tree_state(state_dir, staging, skip_dirs=skip_dirs, skipped=skipped)
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
                        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), pruned=pruned,
                        skipped_xp_segments=int(skipped[0]))


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


def _xp_segments_copy_back(state_dir: Path, previous: Path | None) -> dict[str, Any]:
    """Geri yüklenen state'in ortak deneyim / tavsiye manifestlerindeki segmentler: eksik ya da sha256'sı tutmayan her
    biri `previous` state'ten (varsa, sha256 doğrulanarak) geri kopyalanır. Dönen: {needed, present, copied, missing[]}.
    Arıza ASLA geri yüklemeyi bozmaz (yalnız raporlanır)."""
    import json as _json
    out: dict[str, Any] = {"needed": 0, "present": 0, "copied": 0, "missing": []}
    for rel in XP_ARCHIVE_ROOTS_REL:
        try:
            man = _json.loads((state_dir / rel / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        segs = man.get("segments") if isinstance(man, dict) else None
        for seg in segs if isinstance(segs, list) else []:
            if not isinstance(seg, dict):
                continue
            name, sha = str(seg.get("file") or ""), str(seg.get("sha256") or "")
            if not name or "/" in name or "\\" in name or name.startswith("."):
                continue
            out["needed"] += 1
            dst = state_dir / rel / "segments" / name
            try:
                if dst.is_file() and _sha256_file(dst) == sha:
                    out["present"] += 1
                    continue
                src = (previous / rel / "segments" / name) if previous is not None else None
                if src is not None and src.is_file() and _sha256_file(src) == sha:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dst.with_name(dst.name + ".tmp-restore")
                    shutil.copy2(src, tmp)
                    if _sha256_file(tmp) != sha:
                        tmp.unlink(missing_ok=True)
                        raise OSError("kopya sha256 tutmadı")
                    os.replace(tmp, dst)
                    out["copied"] += 1
                    continue
            except OSError:
                pass
            out["missing"].append("%s/segments/%s" % (rel, name))
    return out


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
    for name in MACHINE_MARKERS:                   # makineye özgü işaret makinede kalır (yedekte yoktur)
        src = (pre / name) if pre is not None else None
        try:
            if src is not None and src.is_file() and not (state_dir / name).exists():
                shutil.copy2(src, state_dir / name)
        except OSError:
            pass
    try:
        xp_segs = _xp_segments_copy_back(state_dir, pre)
    except Exception as exc:  # noqa: BLE001 — geri yükleme tamamlandı; yalnız rapor
        xp_segs = {"error": ("%s: %s" % (type(exc).__name__, exc))[:200]}
    return {"ok": True, "dry_run": False, "state_dir": str(state_dir), "previous": str(pre) if pre else None,
            "vault_restored_to": str(vault_out) if vault_out else None, "members": names, "verify": ver,
            "xp_segments": xp_segs}


__all__ = ["run_backup", "verify_backup", "restore_backup", "latest_backup", "BackupResult", "KINDS"]
