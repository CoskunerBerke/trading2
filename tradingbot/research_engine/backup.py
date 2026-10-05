"""S7b — araştırma yedeği ve motor geri yüklemesi (§3.6, §6.1 S7b, §9.4; P1a kabul 15).

Saatlik yedek yalnız state ve vault'u kapsar; P1a'dan sonra `data/research/closes` ve `entries`, rotasyonla ledger'dan
düşen kayıtların TEK kopyasıdır. Bu yüzden gece biriminin son aşaması şu küçük alt ağaçların günlük, sıkıştırılmış
yedeğini alır:

* **Dahil** (`INCLUDE`): `closes/`, `entries/`, `snapshots/`, `target/`, `trials/`, `lessons/`, `library/`, `explore/`,
  `prospective/`, `proposals/`, `approvals/`, `summary/` ve son 30 `runs/<run_id>/` (+ `runs/` kökündeki küçük
  kayıt dosyaları, ör. A/B dönem kaydı `engine_epochs.jsonl`).
* **Hariç** (`EXCLUDE`, yeniden üretilebilir): `store/`, `archive_cache/`, `dukascopy/`, `backtests/`, `paths/`; ayrıca
  `backup/` (yedeğin kendisi) ve `locks/`. Listede olmayan bir üst klasör de alınmaz (bilinmeyen = dahil değil).

Biçim: `backup/research-small-YYYY-MM-DD.tar.gz` + `.sha256` (`sha256sum -c` biçimi: `<hex>  <ad>`). Arşiv önce aynı
klasörde geçici adla yazılır, `fsync` + `os.replace` ile yerine konur, sonra HEMEN yeniden okunarak doğrulanır: sha256
yan dosyayla tutmalı, gzip akışı sonuna kadar açılabilmeli (CRC32), her üye sonuna kadar okunabilmeli ve üye listesi
yazılan listeye eşit olmalı.
Saklama: **7 günlük + 4 haftalık** = en yeni 7 yedek, ve geri kalanlar arasından ISO haftası başına en yenisi, en yeni 4
hafta için. Yalnız `research-small-YYYY-MM-DD.tar.gz(.sha256)` adlı dosyalar silinir.

**Geri yükleme** (`engine-restore <arşiv>`): varsayılan KURU çalıştırmadır (doğrula + üyeleri ve kenara alınacakları
listele; hiçbir şey değişmez). `--yes` ile: komut araştırma kökünün sahibi olarak çalışmıyorsa reddedilir (VPS'te
`sudo -u tradingbot`; root ile yazılan dosyalara gece birimi yazamazdı), `analysis.lock` alınır (gece birimi
çalışıyorsa reddedilir), mevcut ağacın
içerik alt klasörleri `data/research.pre-restore-<ts>/` altına TAŞINIR (asla silinmez), arşiv önce araştırma kökünde
geçici bir klasöre açılır ve üst klasörler tek tek yerine konur. **Okuma:** belge "mevcut ağaç `research.pre-restore-<ts>`
olarak kenara alınır" der; `locks/` (geri yükleme sırasında tutulan kilidin dosyası; taşınsaydı eşzamanlı bir gece
çalıştırması yeni bir kilit dosyasıyla kilidi alabilirdi) ve `backup/` (geri yüklenen arşivin ve diğer yedeklerin
kendisi) yerinde kalır; geri kalan her şey kenara alınır. Kenara alma araştırma kökünün KARDEŞİDİR
(`state.pre-restore-*` deseniyle aynı); bu tek yazım, yalnız sahibin elle `--yes` ile çalıştırdığı komutta, yalnız
`<veri kökü>/research.pre-restore-<ts>` adına yapılır ve kod bunu denetler. Gece birimi bu yolu hiç çağırmaz.
"""
from __future__ import annotations

import gzip
import os
import re
import shutil
import tarfile
import tempfile
import zlib
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .ledgers import iso, utc_now
from .lock import analysis_lock
from .paths import EnginePaths, sha256_file

INCLUDE: tuple[str, ...] = ("closes", "entries", "snapshots", "target", "trials", "lessons", "library", "explore",
                            "prospective", "proposals", "approvals", "summary")
RUNS_DIR = "runs"
RUNS_KEEP = 30
EXCLUDE: tuple[str, ...] = ("store", "archive_cache", "dukascopy", "backtests", "paths")
#: yedeğe hiç girmeyen ve geri yüklemede yerinde kalan klasörler
NEVER: tuple[str, ...] = ("backup", "locks")
KEEP_DAILY, KEEP_WEEKLY = 7, 4
NAME_RE = re.compile(r"^research-small-(\d{4}-\d{2}-\d{2})\.tar\.gz$")
ASIDE_PREFIX = "research.pre-restore-"
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z(-\d+)?$")


class RestoreRefused(RuntimeError):
    """Geri yükleme güvenle yapılamaz (doğrulama, güvensiz üye, kilit)."""


def archive_name(day: str) -> str:
    return f"research-small-{day}.tar.gz"


# ============================================================================ içerik listesi
def backup_members(paths: EnginePaths) -> list[tuple[Path, str]]:
    """(kaynak dosya, arşiv adı) listesi, sıralı. Geçici dosyalar (`.*.tmp`) alınmaz; sembolik bağlar izlenmez."""
    root = paths.research
    out: list[tuple[Path, str]] = []

    def _walk(d: Path) -> None:
        for dirpath, dirnames, filenames in os.walk(d, followlinks=False):
            dirnames.sort()
            for fn in sorted(filenames):
                if fn.startswith(".") and fn.endswith(".tmp"):
                    continue
                p = Path(dirpath) / fn
                if p.is_symlink() or not p.is_file():
                    continue
                out.append((p, p.relative_to(root).as_posix()))

    for sub in INCLUDE:
        if (root / sub).is_dir():
            _walk(root / sub)
    runs = root / RUNS_DIR
    if runs.is_dir():
        for p in sorted(runs.iterdir()):
            if p.is_file() and not p.is_symlink() and not (p.name.startswith(".") and p.name.endswith(".tmp")):
                out.append((p, p.relative_to(root).as_posix()))
        keep = sorted(p for p in runs.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name))[-RUNS_KEEP:]
        for p in keep:
            _walk(p)
    return out


# ============================================================================ doğrulama
def read_sidecar(archive: Path) -> str | None:
    side = archive.with_name(archive.name + ".sha256")
    try:
        txt = side.read_text(encoding="utf-8").split()
    except OSError:
        return None
    return txt[0].lower() if txt else None


def verify_archive(archive: Path | str, *, expected_sha: str | None = None,
                   expected_members: list[str] | None = None) -> dict[str, Any]:
    """sha256 (yan dosya ya da `expected_sha`), tar bütünlüğü (her üye sonuna kadar okunur) ve güvenli üye adları.
    `expected_members` verilirse üye listesi ona eşit olmalıdır. Dönen: {ok, sha256, expected, members, error}."""
    a = Path(archive)
    out: dict[str, Any] = {"ok": False, "archive": str(a), "sha256": None, "expected": None, "members": [], "error": None}
    if not a.is_file():
        out["error"] = "arşiv yok"
        return out
    out["sha256"] = sha256_file(a)
    exp = (expected_sha or "").strip().lower() or read_sidecar(a)
    out["expected"] = exp
    if not exp:
        out["error"] = "sha256 yan dosyası yok (--sha256 ile verilebilir)"
        return out
    if exp != out["sha256"]:
        out["error"] = "sha256 uyuşmuyor"
        return out
    names: list[str] = []
    try:
        # gzip CRC32 yalnız akışın SONUNDA denetlenir; tar okuması bitiş bloğunda durur. Önce bütün akış sonuna kadar
        # açılır (bozuk bayt, sha tutsa bile — ör. yan dosya yeniden hesaplanmışsa — burada yakalanır).
        with gzip.open(a, "rb") as gz:
            while gz.read(1 << 20):
                pass
        with tarfile.open(a, "r:gz") as tar:
            for m in tar:
                why = unsafe_member(m)
                if why:
                    out["error"] = f"güvensiz üye {m.name!r}: {why}"
                    return out
                if m.isfile():
                    fh = tar.extractfile(m)
                    if fh is not None:
                        while fh.read(1 << 20):
                            pass
                names.append(m.name)
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        out["error"] = f"tar okunamadı: {type(exc).__name__}: {exc}"[:300]
        return out
    out["members"] = names
    if expected_members is not None and names != list(expected_members):
        out["error"] = f"üye listesi beklenenden farklı ({len(names)} / {len(expected_members)})"
        return out
    out["ok"] = True
    return out


def unsafe_member(m: tarfile.TarInfo) -> str | None:
    """Yalnız düz dosya ve klasör; göreli ad; `..` yok; üst klasör izin listesinde."""
    name = m.name.replace("\\", "/")
    if not (m.isfile() or m.isdir()):
        return "yalnız düz dosya/klasör"
    if name.startswith("/") or ".." in name.split("/") or not name:
        return "mutlak ya da üst klasöre çıkan ad"
    top = name.split("/", 1)[0]
    if top not in INCLUDE and top != RUNS_DIR:
        return f"izinsiz üst klasör {top!r}"
    return None


# ============================================================================ yedek (S7b)
def make_backup(paths: EnginePaths, *, now: datetime | None = None, day: str | None = None,
                keep_daily: int = KEEP_DAILY, keep_weekly: int = KEEP_WEEKLY) -> dict[str, Any]:
    """Günlük küçük yedeği yaz, hemen doğrula, saklamayı uygula. Dönen `status`: OK | VERIFY_FAILED."""
    now = now or utc_now()
    day = day or now.strftime("%Y-%m-%d")
    bdir = paths.require_research(paths.backup)
    bdir.mkdir(parents=True, exist_ok=True)
    dest = paths.require_research(bdir / archive_name(day))
    members = backup_members(paths)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=str(bdir))
    try:
        with os.fdopen(fd, "wb") as raw:
            with tarfile.open(fileobj=raw, mode="w:gz", compresslevel=6, format=tarfile.PAX_FORMAT) as tar:
                for src, arc in members:
                    tar.add(str(src), arcname=arc, recursive=False)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    digest = sha256_file(dest)
    paths.write_text(dest.with_name(dest.name + ".sha256"), f"{digest}  {dest.name}\n")
    ver = verify_archive(dest, expected_members=[arc for _, arc in members])
    included = sorted({arc.split("/", 1)[0] for _, arc in members})
    present_excluded = sorted(d for d in EXCLUDE + NEVER if (paths.research / d).exists())
    pruned = prune_backups(paths, keep_daily=keep_daily, keep_weekly=keep_weekly)
    return {"status": "OK" if ver["ok"] else "VERIFY_FAILED", "file": dest.name, "path": str(dest), "sha256": digest,
            "bytes": dest.stat().st_size, "members": len(members), "included_dirs": included,
            "excluded_present": present_excluded, "verified": bool(ver["ok"]), "error": ver.get("error"),
            "pruned": pruned, "written_at": iso(now)}


def backups(paths: EnginePaths) -> list[tuple[str, Path]]:
    """(gün, yol) listesi, eskiden yeniye."""
    if not paths.backup.is_dir():
        return []
    out = []
    for p in paths.backup.iterdir():
        m = NAME_RE.match(p.name)
        if m and p.is_file():
            out.append((m.group(1), p))
    return sorted(out)


def retention_keep(days: list[str], *, keep_daily: int = KEEP_DAILY, keep_weekly: int = KEEP_WEEKLY) -> set[str]:
    """7 günlük + 4 haftalık (modül başı): en yeni `keep_daily` gün ∪ kalanlar arasında ISO haftası başına en yenisi,
    en yeni `keep_weekly` hafta."""
    ds = sorted(set(days), reverse=True)
    keep = set(ds[:keep_daily])
    weeks: dict[tuple[int, int], str] = {}
    for d in ds[keep_daily:]:
        iy, iw, _ = date.fromisoformat(d).isocalendar()
        weeks.setdefault((iy, iw), d)                 # ds yeniden eskiye: haftanın ilk görüleni en yenisi
    for wk in sorted(weeks, reverse=True)[:keep_weekly]:
        keep.add(weeks[wk])
    return keep


def prune_backups(paths: EnginePaths, *, keep_daily: int = KEEP_DAILY, keep_weekly: int = KEEP_WEEKLY) -> list[str]:
    items = backups(paths)
    keep = retention_keep([d for d, _ in items], keep_daily=keep_daily, keep_weekly=keep_weekly)
    gone = []
    for d, p in items:
        if d in keep:
            continue
        for f in (p, p.with_name(p.name + ".sha256")):
            paths.require_research(f)
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        gone.append(p.name)
    return gone


# ============================================================================ geri yükleme
def _resolve_archive(paths: EnginePaths, archive: Path | str) -> Path:
    a = Path(archive)
    if not a.is_absolute() and not a.exists() and (paths.backup / a.name).exists():
        a = paths.backup / a.name
    return a


def _owner_mismatch(root: Path) -> str | None:
    """`--yes` yalnız araştırma kökünün SAHİBİ olarak uygulanır (VPS'te `sudo -u tradingbot`). root ile uygulanan bir
    geri yükleme root'a ait dosyalar bırakır; gece birimi (User=tradingbot) onlara yazamaz ve S0'da durur. Kök yoksa
    denetlenecek bir şey yoktur."""
    try:
        uid = root.stat().st_uid
    except OSError:
        return None
    me = os.geteuid() if hasattr(os, "geteuid") else uid
    if uid == me:
        return None
    try:
        import pwd
        who = pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        who = f"uid {uid}"
    return (f"geri yükleme araştırma kökünün sahibiyle uygulanmalı ({root} sahibi {who}, çalışan uid {me}): "
            f"`sudo -u {who} -H ... engine-restore <arşiv> --yes`")


def aside_dir(paths: EnginePaths, ts: str) -> Path:
    """Kenara alma yolu: YALNIZ `<veri kökü>/research.pre-restore-<ts>` (araştırma kökünün kardeşi)."""
    if not re.fullmatch(r"\d{8}T\d{6}Z", ts):
        raise RestoreRefused(f"geçersiz zaman damgası {ts!r}")
    p = paths.research.parent / f"{ASIDE_PREFIX}{ts}"
    if p.parent.resolve() != paths.research.parent.resolve() or not p.name.startswith(ASIDE_PREFIX):
        raise RestoreRefused(f"kenara alma yolu beklenen yerde değil: {p}")
    return p


def restore(paths: EnginePaths, archive: Path | str, *, apply: bool = False, expected_sha: str | None = None,
            now: datetime | None = None) -> dict[str, Any]:
    """`engine-restore`: varsayılan kuru çalıştırma; `apply=True` (`--yes`) uygular. Dönen rapor sözlüğü."""
    now = now or utc_now()
    a = _resolve_archive(paths, archive)
    ver = verify_archive(a, expected_sha=expected_sha)
    root = paths.research
    current = sorted(p.name for p in root.iterdir()) if root.is_dir() else []
    move = [n for n in current if n not in NEVER]
    tops: dict[str, int] = {}
    for n in ver.get("members") or []:
        top = n.split("/", 1)[0]
        tops[top] = tops.get(top, 0) + 1
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    rep: dict[str, Any] = {"archive": str(a), "verify": {k: ver[k] for k in ("ok", "sha256", "expected", "error")},
                           "members": len(ver.get("members") or []), "top_dirs": tops, "research": str(root),
                           "will_move_aside": move, "kept_in_place": [n for n in current if n in NEVER],
                           "aside": str(aside_dir(paths, ts)), "dry_run": not apply, "applied": False}
    if not ver["ok"]:
        rep["error"] = ver["error"]
        if apply:
            raise RestoreRefused(f"arşiv doğrulanamadı: {ver['error']}")
        return rep
    if not apply:
        return rep
    owner_problem = _owner_mismatch(root)
    if owner_problem:
        raise RestoreRefused(owner_problem)
    lk = analysis_lock(paths)
    if not lk.try_acquire():
        raise RestoreRefused("analysis.lock tutuluyor: gece birimi çalışıyor; bitince yeniden deneyin")
    try:
        aside = aside_dir(paths, ts)
        if aside.exists():
            raise RestoreRefused(f"kenara alma klasörü zaten var: {aside}")
        stage = paths.require_research(root / f".restore-staging-{ts}")
        stage.mkdir(parents=True, exist_ok=False)
        try:
            with tarfile.open(a, "r:gz") as tar:
                for m in tar.getmembers():
                    why = unsafe_member(m)
                    if why:
                        raise RestoreRefused(f"güvensiz üye {m.name!r}: {why}")
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(stage, filter="data")
                else:  # pragma: no cover - eski Python; üyeler yukarıda tek tek denetlendi
                    tar.extractall(stage)
            aside.mkdir(parents=False, exist_ok=False)
            moved = []
            for n in move:
                os.replace(root / n, aside / n)
                moved.append(n)
            placed = []
            for p in sorted(stage.iterdir()):
                os.replace(p, root / p.name)
                placed.append(p.name)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        rep.update(applied=True, moved_aside=moved, placed=placed, aside=str(aside))
        return rep
    finally:
        lk.release()


__all__ = ["ASIDE_PREFIX", "EXCLUDE", "INCLUDE", "KEEP_DAILY", "KEEP_WEEKLY", "NEVER", "RestoreRefused", "archive_name",
           "aside_dir", "backup_members", "backups", "make_backup", "prune_backups", "read_sidecar", "restore",
           "retention_keep", "unsafe_member", "verify_archive"]
