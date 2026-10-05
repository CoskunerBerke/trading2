"""S0 — çalışma zamanı ön kontrolü ve öz-denetim (§2.3, §2.4, §2.8, §2.9, §6.1 S0, §9.2).

Gece birimi her çalıştırmada, çekirdek ayarlarına (birim dosyası) güvenmekle yetinmeyip yalıtımın GERÇEKTEN yürürlükte
olduğunu dener. Biri tutmazsa çalıştırma `ISOLATION_BROKEN` ile durur ve sıfırdan farklı kodla çıkar (uyarı birimi
tetiklenir). Denetimler (her biri saf bir fonksiyondur; testler `Probes`/`runner`/`sleep` ile yerine-geçen verir):

1. **PAPER** (`check_paper`): `state/mode.json` salt-okunur `open(..., "r")` + `json` ile okunur. `mode == "PAPER"`
   değilse, `live_order_path_enabled` doğruysa, dosya bozuksa/okunamıyorsa veya ortamda `ALLOW_LIVE_TRADING` açıksa
   çalıştırma `NOT_PAPER` ile çıkar. **Okuma:** belge "PAPER değilse veya okunamıyorsa çıkar" der; dosyanın HİÇ
   olmaması worker'ın kendi varsayılanıdır (`risk/modes.ModeState` dosya yokken PAPER'dır; f8b05fb sürüm betiğinin
   `mode_check`'i de "yok → varsayılan PAPER" sayar). Motor da aynı biçimde okur ve kaynağı `default_missing` yazar.
2. **Yalıtım** (`run_isolation`): (a) `data/state`, `data/market` ve `/opt/tradingbot/app` altında `O_CREAT|O_EXCL` ile
   benzersiz bir deneme dosyası açmak `EROFS` veya `EACCES` ile başarısız OLMALIDIR; beklenmedik biçimde açılırsa dosya
   hemen silinir ve çalıştırma durur. Klasör hiç yoksa (`ENOENT`) korunacak bir şey yoktur: `ABSENT` yazılır, bozuk
   sayılmaz. Başka her hata (ör. `ENOSPC`: yazma DENENDİ) fail-closed bozuk sayılır. (b) DNS'siz bir genel IP'ye
   (`1.1.1.1:443`) `socket.connect` 2 sn içinde başarısız OLMALIDIR; bağlanırsa veya zaman aşımına uğrarsa (paket
   gitti = ağ var) bozuktur. `PrivateNetwork=yes` altında bağlantı `ENETUNREACH` ile anında düşer. (c) Birimin kendi
   cgroup'unun `memory.max`'ı (`/proc/self/cgroup` → `/sys/fs/cgroup/<yol>/memory.max`; v1 için
   `memory.limit_in_bytes`) `ENGINE_EXPECTED_MEMORY_MAX` ortam değişkenine (bayt) eşit OLMALIDIR; değişken yoksa da
   bozuktur (birim dışında çalışılıyor demektir). (d) `/tmp` (`PrivateTmp`) ve `data/research` yazılabilir OLMALIDIR.
   Bu modül `socket`'i YALNIZ (b)'deki ret denemesi için ve fonksiyon içinde import eder (AST testi bu tek istisnayı
   bilir); motorun ağ kullanan hiçbir yolu yoktur.
3. **SKEW** (`check_skew`): çalışan app'in SHA'sı (`/opt/tradingbot/app/.git` altından saf Python ile, salt-okunur:
   `HEAD` → ref dosyası veya `packed-refs`; `git` çağrılmaz, çünkü app ağacı başka kullanıcıya ait olabilir) ve
   engine-app SHA'sı (aynı okuyucu) karşılaştırılır. App SHA'sı engine-app SHA'sının kendisi veya atası olmalıdır:
   `git -c safe.directory=<engine> -C <engine> merge-base --is-ancestor <app> <engine>`. Çıkış 0 → OK; 1 → SKEW;
   başka her şey (app SHA'sı engine klonunda bilinmiyor = 128, `git` yok, SHA okunamadı) → SKEW (fail-closed;
   `relation=UNKNOWN`). SKEW gecesinde yalnız S0, S1a (rotasyon kaybı olmasın) ve S7 çalışır.
4. **Yedek birimi çakışması** (`check_backup_overlap`): `systemctl is-active tradingbot-backup.service`. **Okuma:**
   yedek birimi `Type=oneshot`'tır; oneshot birim çalışırken `is-active` `active` değil `activating` döndürür. Bu
   yüzden `active`, `activating`, `reloading`, `deactivating` "çalışıyor" sayılır. Çalışıyorsa 30 sn aralıkla en fazla
   15 dk beklenir, sonra devam edilir ve `BACKUP_OVERLAP` yazılır. `systemctl` yoksa/cevap vermezse `UNKNOWN`
   (beklemeden devam; bayrak `BACKUP_STATE_UNKNOWN`).
5. **A/B takvimi** (`ab_decision`, §2.9): her motor sürümünden (yeni engine SHA'sı) sonraki 14 gecede, UTC tarihinin
   gün-yıl sırası çiftse AÇIK, tekse KAPALI. **Okuma (pencerenin başı):** motor bir SHA'yı ilk kez gördüğü günü
   `runs/engine_epochs.jsonl`'a yazar (yalnız eklenir). O gün sürüm günüdür ve o günün çalıştırmaları (sürüm
   betiğinin elle smoke çalıştırması dahil) A/B DIŞINDADIR — smoke çalıştırmasının beklenen dosyaları üretmesi
   gerekir (§9.3 adım 7). Pencere = ilk görülme gününden sonraki 14 UTC günü. KAPALI gecede herhangi bir defterin
   rotasyon payı 3 günden azsa S1a yine çalışır ve gece karşılaştırmadan çıkar (`AB_OFF_ZORUNLU_ARŞİV`).
6. **Disk** (`paths.disk_guard`) ve **canlı config sha'sı** (`rawconfig`; önceki çalıştırmanınkiyle karşılaştırılır,
   değiştiyse `CONFIG_CHANGED` bayrağı ve özette görünür; config dönemleri P3'te).

Kilit (`analysis.lock`) ve aşama planı `night.py`'dedir. Bu modül `config_v3`'ü, `config.load_config`'i ve
`sqlite3`'ü import ETMEZ.
"""
from __future__ import annotations

import errno
import json
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .ledgers import add_days, iso, parse_day
from .paths import EnginePaths, read_jsonl

#: VPS app ağacı (canlı config ve app SHA'sı buradan, salt-okunur).
APP_DIR = Path("/opt/tradingbot/app")
#: Varsayılan engine-app ağacı: ÇALIŞAN kodun deposu (`tradingbot/research_engine/` → iki üst). VPS'te
#: `/opt/tradingbot/engine-app`'tir (birim `WorkingDirectory`'si); böylece SHA her zaman çalışan kodun SHA'sıdır.
ENGINE_DIR_DEFAULT = Path(__file__).resolve().parents[2]

BACKUP_UNIT = "tradingbot-backup.service"
BACKUP_WAIT_MAX_S = 15 * 60
BACKUP_POLL_S = 30
#: oneshot birim çalışırken `activating`'dir (okuma 4).
BUSY_STATES = frozenset({"active", "activating", "reloading", "deactivating"})

SOCKET_PROBE_ADDR = ("1.1.1.1", 443)
SOCKET_PROBE_TIMEOUT_S = 2.0
ENV_EXPECTED_MEMORY_MAX = "ENGINE_EXPECTED_MEMORY_MAX"
#: deneme dosyasının reddi yalnız bu hatalarla "reddedildi" sayılır (§2.8 madde 1).
DENIED_ERRNOS = frozenset({errno.EROFS, errno.EACCES})

AB_NIGHTS = 14
EPOCHS_FILE = "engine_epochs.jsonl"
EPOCH_SCHEMA = "engine_epoch_v1"

# ---- sonuç kodları
OK = "OK"
NOT_PAPER = "NOT_PAPER"
ISOLATION_BROKEN = "ISOLATION_BROKEN"
SKEW = "SKEW"
BACKUP_OVERLAP = "BACKUP_OVERLAP"
BACKUP_STATE_UNKNOWN = "BACKUP_STATE_UNKNOWN"
AB_ON, AB_OFF, AB_OFF_FORCED = "AB_ON", "AB_OFF", "AB_OFF_ZORUNLU_ARŞİV"
AB_OUTSIDE, AB_RELEASE_DAY = "AB_OUTSIDE", "AB_RELEASE_DAY"
#: deneme durumları
P_DENIED, P_ABSENT, P_WRITABLE, P_ERROR = "DENIED", "ABSENT", "WRITABLE", "ERROR"
P_CONNECTED, P_TIMEOUT = "CONNECTED", "TIMEOUT"
P_MATCH, P_MISMATCH, P_NO_ENV, P_UNREADABLE = "MATCH", "MISMATCH", "NO_ENV", "UNREADABLE"
P_WRITABLE_OK, P_NOT_WRITABLE = "WRITABLE_OK", "NOT_WRITABLE"


def _errname(code: int | None) -> str:
    return errno.errorcode.get(code or 0, str(code))


# ============================================================================ 1. PAPER
def check_paper(state: Path | str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """`state/mode.json`'u salt-okunur oku (okuma 1). Dönen `status`: OK | NOT_PAPER."""
    env = os.environ if env is None else env
    p = Path(state) / "mode.json"
    out: dict[str, Any] = {"path": str(p), "status": NOT_PAPER, "mode": None, "source": None, "error": None,
                           "allow_live_env": env.get("ALLOW_LIVE_TRADING")}
    live_env = str(env.get("ALLOW_LIVE_TRADING", "")).strip().lower() in ("1", "true", "yes", "on")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except FileNotFoundError:
        out.update(mode="PAPER", source="default_missing")
        d = None
    except (OSError, ValueError) as exc:
        out.update(source="unreadable", error=f"{type(exc).__name__}: {exc}"[:200])
        return out
    if d is not None:
        if not isinstance(d, dict):
            out.update(source="file", error="kök bir JSON nesnesi değil")
            return out
        out.update(mode=d.get("mode"), source="file", live_order_path_enabled=d.get("live_order_path_enabled"))
        if d.get("mode") != "PAPER" or d.get("live_order_path_enabled") is True:
            out["error"] = f"mode={d.get('mode')!r} live_order_path_enabled={d.get('live_order_path_enabled')!r}"
            return out
    if live_env:
        out["error"] = "ortamda ALLOW_LIVE_TRADING açık"
        return out
    out["status"] = OK
    return out


# ============================================================================ 2. yalıtım denemeleri
def probe_write_denied(directory: Path | str) -> dict[str, Any]:
    """Klasörde benzersiz bir dosyayı `O_CREAT|O_EXCL` ile açmayı dene; açılırsa HEMEN sil. Dönen `status`:
    DENIED (EROFS/EACCES) | ABSENT (klasör yok) | WRITABLE (açıldı = bozuk) | ERROR (başka hata = bozuk)."""
    d = Path(directory)
    name = d / f".engine-isolation-probe-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        fd = os.open(str(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        if exc.errno in DENIED_ERRNOS:
            return {"path": str(d), "status": P_DENIED, "errno": _errname(exc.errno)}
        if exc.errno == errno.ENOENT and not d.exists():
            return {"path": str(d), "status": P_ABSENT, "errno": _errname(exc.errno)}
        return {"path": str(d), "status": P_ERROR, "errno": _errname(exc.errno), "error": str(exc)[:200]}
    removed = True
    try:
        os.close(fd)
    finally:
        try:
            os.unlink(str(name))
        except OSError:
            removed = False
    return {"path": str(d), "status": P_WRITABLE, "errno": None, "probe_removed": removed}


def probe_socket_denied(addr: tuple[str, int] = SOCKET_PROBE_ADDR, timeout: float = SOCKET_PROBE_TIMEOUT_S) -> dict[str, Any]:
    """DNS'siz genel IP'ye bağlanmayı dene; bağlantı KURULAMAMALIDIR. Dönen `status`: DENIED | CONNECTED | TIMEOUT."""
    import socket  # yalnız bu ret denemesi için (modül başı açıklaması; AST testi bu istisnayı bilir)

    t0 = time.monotonic()
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(addr)
    except socket.timeout:
        return {"addr": f"{addr[0]}:{addr[1]}", "status": P_TIMEOUT, "elapsed_s": round(time.monotonic() - t0, 3)}
    except OSError as exc:
        return {"addr": f"{addr[0]}:{addr[1]}", "status": P_DENIED, "errno": _errname(exc.errno),
                "elapsed_s": round(time.monotonic() - t0, 3)}
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
    return {"addr": f"{addr[0]}:{addr[1]}", "status": P_CONNECTED, "elapsed_s": round(time.monotonic() - t0, 3)}


def own_cgroup_memory_max(proc_cgroup: Path | str = "/proc/self/cgroup",
                          cgroup_root: Path | str = "/sys/fs/cgroup") -> tuple[int | None, str | None, str | None]:
    """Sürecin kendi cgroup'unun bellek sınırı. Dönen: (bayt | None ("max" = sınırsız), dosya yolu, hata)."""
    try:
        lines = Path(proc_cgroup).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return None, None, f"{proc_cgroup}: {exc}"
    root = Path(cgroup_root)
    cands: list[Path] = []
    for ln in lines:
        parts = ln.split(":", 2)
        if len(parts) != 3:
            continue
        hid, ctrls, rel = parts
        rel = rel.strip().lstrip("/")
        if hid == "0" and ctrls == "":
            cands.append(root / rel / "memory.max")                       # cgroup v2 (birleşik)
        elif "memory" in ctrls.split(","):
            cands.append(root / "memory" / rel / "memory.limit_in_bytes")  # cgroup v1
    for f in cands:
        try:
            txt = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if txt == "max":
            return None, str(f), None
        try:
            return int(txt), str(f), None
        except ValueError:
            return None, str(f), f"okunamayan değer {txt!r}"
    return None, None, "cgroup bellek dosyası bulunamadı"


def probe_memory_max(env: Mapping[str, str] | None = None, **kw: Any) -> dict[str, Any]:
    """`memory.max` = `ENGINE_EXPECTED_MEMORY_MAX` mı? Dönen `status`: MATCH | MISMATCH | NO_ENV | UNREADABLE."""
    env = os.environ if env is None else env
    raw = env.get(ENV_EXPECTED_MEMORY_MAX)
    try:
        expected = int(str(raw).strip()) if raw not in (None, "") else None
    except ValueError:
        expected = None
    actual, path, err = own_cgroup_memory_max(**kw)
    out = {"expected": expected, "actual": actual, "file": path}
    if expected is None:
        return {**out, "status": P_NO_ENV, "error": f"{ENV_EXPECTED_MEMORY_MAX} yok/okunamadı ({raw!r})"}
    if err is not None or path is None:
        return {**out, "status": P_UNREADABLE, "error": err}
    return {**out, "status": P_MATCH if actual == expected else P_MISMATCH}


def probe_writable(directory: Path | str | None) -> dict[str, Any]:
    """Klasöre geçici bir dosya yazılıp silinebiliyor mu (None = `tempfile` varsayılanı, yani `/tmp`)."""
    try:
        with tempfile.NamedTemporaryFile(prefix=".engine-probe-", dir=None if directory is None else str(directory)) as fh:
            fh.write(b"x")
            fh.flush()
            where = str(Path(fh.name).parent)
    except OSError as exc:
        return {"path": str(directory or tempfile.gettempdir()), "status": P_NOT_WRITABLE, "errno": _errname(exc.errno)}
    return {"path": where, "status": P_WRITABLE_OK}


@dataclass
class Probes:
    """Yalıtım denemeleri (testler yerine-geçen verir; üretimde gerçekleri)."""

    write_denied: Callable[[Path], dict] = probe_write_denied
    socket_denied: Callable[[], dict] = probe_socket_denied
    memory_max: Callable[[Mapping[str, str]], dict] = probe_memory_max
    writable: Callable[[Path | None], dict] = probe_writable


def run_isolation(paths: EnginePaths, *, app_dir: Path | str = APP_DIR, env: Mapping[str, str] | None = None,
                  probes: Probes | None = None) -> dict[str, Any]:
    """§2.8 öz-denetiminin dört maddesi. Dönen `status`: OK | ISOLATION_BROKEN; `broken` bozuk denemelerin adları."""
    pr = probes or Probes()
    env = os.environ if env is None else env
    checks: dict[str, Any] = {}
    broken: list[str] = []
    for name, d in (("state", paths.state), ("market", paths.market), ("app", Path(app_dir))):
        r = pr.write_denied(Path(d))
        checks[f"write_denied_{name}"] = r
        if r.get("status") not in (P_DENIED, P_ABSENT):
            broken.append(f"write_denied_{name}")
    r = pr.socket_denied()
    checks["socket_denied"] = r
    if r.get("status") != P_DENIED:
        broken.append("socket_denied")
    r = pr.memory_max(env)
    checks["memory_max"] = r
    if r.get("status") != P_MATCH:
        broken.append("memory_max")
    for name, d in (("tmp", None), ("research", paths.research)):
        if d is not None:
            try:
                Path(d).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        r = pr.writable(d)
        checks[f"writable_{name}"] = r
        if r.get("status") != P_WRITABLE_OK:
            broken.append(f"writable_{name}")
    return {"status": ISOLATION_BROKEN if broken else OK, "broken": broken, "checks": checks}


# ============================================================================ 3. SKEW
def _read_text(p: Path) -> str | None:
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _git_dirs(repo: Path) -> tuple[Path | None, Path | None]:
    """(gitdir, commondir). `.git` bir klasör ya da `gitdir: …` dosyası olabilir (worktree/ayrık gitdir)."""
    dotgit = repo / ".git"
    if dotgit.is_dir():
        gitdir = dotgit
    elif dotgit.is_file():
        txt = (_read_text(dotgit) or "").strip()
        if not txt.startswith("gitdir:"):
            return None, None
        g = Path(txt.split(":", 1)[1].strip())
        gitdir = g if g.is_absolute() else (repo / g)
    else:
        return None, None
    common = gitdir
    c = _read_text(gitdir / "commondir")
    if c:
        cp = Path(c.strip())
        common = cp if cp.is_absolute() else (gitdir / cp)
    return gitdir, common


def _is_sha(s: str) -> bool:
    return len(s) == 40 and all(ch in "0123456789abcdef" for ch in s)


def read_git_head(repo: Path | str) -> tuple[str | None, str | None]:
    """Deponun HEAD SHA'sı, `git` çağırmadan, salt-okunur (HEAD → ref dosyası → `packed-refs`). Dönen: (sha, hata)."""
    gitdir, common = _git_dirs(Path(repo))
    if gitdir is None:
        return None, f"{repo}: .git yok"
    head = (_read_text(gitdir / "HEAD") or "").strip()
    if not head:
        return None, f"{gitdir}/HEAD okunamadı"
    for _ in range(5):                      # sembolik ref zinciri (sınırlı)
        if not head.startswith("ref:"):
            return (head, None) if _is_sha(head) else (None, f"HEAD çözülemedi: {head[:60]!r}")
        ref = head.split(":", 1)[1].strip()
        val = None
        for base in (gitdir, common):
            if base is None:
                continue
            t = _read_text(base / ref)
            if t:
                val = t.strip()
                break
        if val is None:
            packed = _read_text((common or gitdir) / "packed-refs") or ""
            for ln in packed.splitlines():
                ln = ln.strip()
                if not ln or ln[0] in "#^":
                    continue
                parts = ln.split(" ", 1)
                if len(parts) == 2 and parts[1] == ref:
                    val = parts[0]
                    break
        if val is None:
            return None, f"ref bulunamadı: {ref}"
        head = val
    return None, "ref zinciri çok uzun"


Runner = Callable[..., Any]


def _git_env() -> dict[str, str]:
    """`git` için en küçük ortam. HOME ve XDG_CONFIG_HOME bilerek VAR OLMAYAN bir yoldur: git küresel config'i
    okuyamadığında (`EACCES`, ör. `ProtectHome=yes` altında /home'daki bir HOME) ölür, yalnız `ENOENT`'i yok sayar;
    böyle bir ölüm her geceyi fail-closed SKEW yapardı. Sistem config'i de okunmaz; isteğe bağlı kilit yazılmaz
    (engine-app salt-okunurdur)."""
    return {"PATH": os.environ.get("PATH") or "/usr/bin:/bin", "HOME": "/nonexistent", "XDG_CONFIG_HOME": "/nonexistent",
            "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0"}


def is_ancestor(engine_dir: Path | str, app_sha: str, engine_sha: str, *, runner: Runner = subprocess.run,
                timeout: float = 60.0) -> tuple[bool | None, str]:
    """`git merge-base --is-ancestor app engine` engine klonunda. Dönen: (True/False/None(bilinmiyor), açıklama)."""
    ed = str(Path(engine_dir))
    cmd = ["git", "-c", f"safe.directory={ed}", "-C", ed, "merge-base", "--is-ancestor", app_sha, engine_sha]
    try:
        cp = runner(cmd, capture_output=True, text=True, timeout=timeout, env=_git_env(), check=False)
    except FileNotFoundError:
        return None, "git bulunamadı"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git çalıştırılamadı: {type(exc).__name__}: {exc}"[:200]
    rc = getattr(cp, "returncode", None)
    if rc == 0:
        return True, "ata"
    if rc == 1:
        return False, "ata değil"
    err = (getattr(cp, "stderr", "") or "").strip().splitlines()
    return None, f"git çıkış {rc}: {err[-1][:160] if err else ''}"


def check_skew(app_dir: Path | str = APP_DIR, engine_dir: Path | str = ENGINE_DIR_DEFAULT, *,
               runner: Runner = subprocess.run) -> dict[str, Any]:
    """İki SHA + ata denetimi (okuma 3). Dönen `status`: OK | SKEW; `relation`: SAME | ANCESTOR | NOT_ANCESTOR | UNKNOWN."""
    app_sha, app_err = read_git_head(app_dir)
    eng_sha, eng_err = read_git_head(engine_dir)
    out: dict[str, Any] = {"app_dir": str(app_dir), "engine_dir": str(engine_dir), "app_sha": app_sha,
                           "engine_sha": eng_sha, "status": SKEW, "relation": "UNKNOWN", "reason": None}
    if app_sha is None or eng_sha is None:
        out["reason"] = "; ".join(x for x in (app_err, eng_err) if x)
        return out
    if app_sha == eng_sha:
        out.update(status=OK, relation="SAME", reason="aynı SHA")
        return out
    anc, why = is_ancestor(engine_dir, app_sha, eng_sha, runner=runner)
    if anc is True:
        out.update(status=OK, relation="ANCESTOR", reason=why)
    elif anc is False:
        out.update(relation="NOT_ANCESTOR", reason="app SHA'sı engine-app SHA'sının atası değil")
    else:
        out["reason"] = why
    return out


# ============================================================================ 4. yedek birimi çakışması
def backup_unit_state(*, runner: Runner = subprocess.run, unit: str = BACKUP_UNIT) -> str | None:
    """`systemctl is-active <unit>` çıktısı (ör. `inactive`, `activating`); çalıştırılamazsa None."""
    try:
        cp = runner(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=15, check=False,
                    env={"PATH": os.environ.get("PATH") or "/usr/bin:/bin", "LC_ALL": "C", "SYSTEMD_PAGER": ""})
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    s = (getattr(cp, "stdout", "") or "").strip().splitlines()
    return s[0].strip() if s else None


def check_backup_overlap(*, runner: Runner = subprocess.run, sleep: Callable[[float], None] = time.sleep,
                         max_wait_s: float = BACKUP_WAIT_MAX_S, poll_s: float = BACKUP_POLL_S) -> dict[str, Any]:
    """Yedek birimi çalışıyorsa `poll_s` aralıkla en fazla `max_wait_s` bekle (okuma 4). Bekleme, uyku SAYISIYLA
    sınırlanır (en çok ⌈max_wait_s / poll_s⌉ yoklama; 15 dk / 30 sn = 30). Dönen `status`:
    IDLE | WAITED | BACKUP_OVERLAP | BACKUP_STATE_UNKNOWN."""
    max_polls = max(0, int(-(-max_wait_s // poll_s))) if poll_s > 0 else 0
    st = backup_unit_state(runner=runner)
    first = st
    if st is None:
        return {"status": BACKUP_STATE_UNKNOWN, "state": None, "waited_s": 0.0, "polls": 0}
    polls = 0
    while st in BUSY_STATES:
        if polls >= max_polls:
            return {"status": BACKUP_OVERLAP, "state": st, "first_state": first, "waited_s": polls * poll_s,
                    "polls": polls}
        sleep(poll_s)
        polls += 1
        st = backup_unit_state(runner=runner)
        if st is None:
            return {"status": BACKUP_STATE_UNKNOWN, "state": None, "first_state": first, "waited_s": polls * poll_s,
                    "polls": polls}
    return {"status": "WAITED" if polls else "IDLE", "state": st, "first_state": first, "waited_s": polls * poll_s,
            "polls": polls}


# ============================================================================ 5. A/B takvimi
def epochs_path(paths: EnginePaths) -> Path:
    return paths.runs / EPOCHS_FILE


def load_epochs(paths: EnginePaths) -> list[dict]:
    p = epochs_path(paths)
    try:
        return [r for r in read_jsonl(p) if r.get("schema") == EPOCH_SCHEMA]
    except ValueError:
        return []


def first_seen_day(paths: EnginePaths, engine_sha: str | None) -> str | None:
    for r in load_epochs(paths):
        if r.get("engine_sha") == engine_sha:
            return str(r.get("first_seen_day"))
    return None


def register_epoch(paths: EnginePaths, engine_sha: str, now: datetime, run_id: str) -> str:
    """Engine SHA'sını ilk görüldüğü günle kaydet (yalnız eklenir); zaten kayıtlıysa o günü döndür."""
    d = first_seen_day(paths, engine_sha)
    if d is not None:
        return d
    day = now.strftime("%Y-%m-%d")
    paths.append_jsonl(epochs_path(paths), [{"schema": EPOCH_SCHEMA, "engine_sha": engine_sha, "first_seen_day": day,
                                             "first_seen_at": iso(now), "run_id": run_id}])
    return day


def ab_parity_on(day: str | date) -> bool:
    """Mühürlü takvim: UTC tarihinin gün-yıl sırası çiftse AÇIK."""
    d = parse_day(day) if isinstance(day, str) else day
    return d.timetuple().tm_yday % 2 == 0


def ab_decision(day: str, first_day: str | None, *, rotation_min_days: float | None,
                rotation_warn_days: float = 3.0, nights: int = AB_NIGHTS) -> dict[str, Any]:
    """A/B kararı (okuma 5). Dönen `status`: AB_RELEASE_DAY | AB_ON | AB_OFF | AB_OFF_ZORUNLU_ARŞİV | AB_OUTSIDE."""
    if first_day is None:
        return {"status": AB_OUTSIDE, "day": day, "reason": "engine SHA'sı bilinmiyor; A/B penceresi kurulamaz"}
    start, end = add_days(first_day, 1), add_days(first_day, nights)
    base = {"day": day, "release_day": first_day, "window": [start, end]}
    if day == first_day:
        return {**base, "status": AB_RELEASE_DAY, "reason": "sürüm günü (smoke dahil): A/B dışında, tam çalışma"}
    if not (start <= day <= end):
        return {**base, "status": AB_OUTSIDE, "reason": "14 gecelik A/B penceresi dışında"}
    night_no = (parse_day(day) - parse_day(first_day)).days
    if ab_parity_on(day):
        return {**base, "status": AB_ON, "night": night_no, "reason": "gün-yıl sırası çift → AÇIK"}
    if rotation_min_days is not None and rotation_min_days < rotation_warn_days:
        return {**base, "status": AB_OFF_FORCED, "night": night_no, "rotation_min_days": rotation_min_days,
                "reason": f"KAPALI gece ama rotasyon payı {rotation_min_days:.2f} gün < {rotation_warn_days:g}: S1a çalışır, "
                          "gece karşılaştırmadan çıkar"}
    return {**base, "status": AB_OFF, "night": night_no, "rotation_min_days": rotation_min_days,
            "reason": "gün-yıl sırası tek → KAPALI"}


__all__ = ["AB_NIGHTS", "AB_OFF", "AB_OFF_FORCED", "AB_ON", "AB_OUTSIDE", "AB_RELEASE_DAY", "APP_DIR", "BACKUP_OVERLAP",
           "BACKUP_STATE_UNKNOWN", "BACKUP_UNIT", "BUSY_STATES", "ENGINE_DIR_DEFAULT", "ENV_EXPECTED_MEMORY_MAX",
           "ISOLATION_BROKEN", "NOT_PAPER", "OK", "Probes", "SKEW", "ab_decision", "ab_parity_on", "backup_unit_state",
           "check_backup_overlap", "check_paper", "check_skew", "first_seen_day", "is_ancestor", "load_epochs",
           "own_cgroup_memory_max", "probe_memory_max", "probe_socket_denied", "probe_writable", "probe_write_denied",
           "read_git_head", "register_epoch", "run_isolation"]
