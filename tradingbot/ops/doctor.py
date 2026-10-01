"""Doktor — çalışma ortamı sağlık kontrol listesi (`python -m tradingbot doctor [--quick]`).

Kontroller: config, state yazılabilir, kilit durumu, JSON parse + schema_version, vault yazılabilir, disk > 1GB,
saat sapması (quick'te atlanır; ağ yoksa uyarı), bağımlılık importları, sqlite bütünlüğü, yedek tazeliği,
kalp atışı yaşı, mod PAPER ve ALLOW_LIVE_TRADING ayarlı değil.
`severity="fail"` olan başarısız kontrol raporu `ok=False` yapar; `warn` yapmaz.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core import iso, read_json
from .backup import latest_backup
from .health import read_heartbeat_age
from .lock import SingletonLock

STATE_JSON_FILES = ("agents.json", "futures_ledger.json", "portfolio.json", "scan.json", "learning.json", "signals.json",
                    "coin_heads.json", "risk.json", "killswitch.json", "mode.json", "health.json", "llm_budget.json",
                    "models.json", "universe.json", "shadow_book.json", "heartbeat.json")
REQUIRED_DEPS = ("pandas", "numpy", "pydantic", "yaml")
OPTIONAL_DEPS = ("fastapi", "uvicorn", "plotly", "matplotlib", "websocket", "requests", "pyarrow")
LIVE_MODES = {"LIVE", "LIVE_LIMITED", "SHADOW_LIVE"}


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str = ""
    severity: str = "fail"      # fail | warn | info
    code: str = ""              # kararlı makine-okunur kimlik (örn. HEARTBEAT_STALE); boş = kodlanmamış

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DoctorReport:
    ok: bool
    checks: list[DoctorCheck] = field(default_factory=list)
    generated_at: str = ""
    quick: bool = False

    def to_dict(self) -> dict:
        return {"ok": self.ok, "quick": self.quick, "generated_at": self.generated_at, "checks": [c.to_dict() for c in self.checks]}

    @property
    def failures(self) -> list[DoctorCheck]:
        return [c for c in self.checks if not c.ok and c.severity == "fail"]

    @property
    def warnings(self) -> list[DoctorCheck]:
        return [c for c in self.checks if not c.ok and c.severity == "warn"]


def _get(cfg_like: Any, name: str, default: Any = None) -> Any:
    if cfg_like is None:
        return default
    if isinstance(cfg_like, dict):
        return cfg_like.get(name, default)
    return getattr(cfg_like, name, default)


def _writable(d: Path) -> tuple[bool, str]:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".doctor-probe-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, str(d)
    except OSError as exc:
        return False, f"{d}: {exc}"


def _clock_skew_seconds(timeout: float = 3.0) -> float | None:
    """Binance sunucu saatiyle fark (s). Ağ yoksa None. (quick modda çağrılmaz.)"""
    import urllib.request
    try:
        t0 = time.time()
        with urllib.request.urlopen("https://api.binance.com/api/v3/time", timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        t1 = time.time()
        server = float(data["serverTime"]) / 1000.0
        return ((t0 + t1) / 2.0) - server
    except Exception:  # noqa: BLE001 - ağ hataları çeşitli; teşhis amaçlı
        return None


def run_doctor(cfg_like: Any, state_dir: Path | str, data_dir: Path | str | None = None,
               vault_dir: Path | str | None = None, quick: bool = False, *, backups_dir: Path | str | None = None,
               heartbeat_max_age_s: float = 900.0, backup_max_age_h: float = 26.0, min_disk_gb: float = 1.0,
               max_clock_skew_s: float = 2.0) -> DoctorReport:
    state_dir = Path(state_dir)
    checks: list[DoctorCheck] = []

    def add(name: str, ok: bool, detail: Any = "", severity: str = "fail", code: str = "") -> None:
        checks.append(DoctorCheck(name=name, ok=bool(ok), detail=str(detail), severity=severity, code=code))

    # 1 config
    add("config", cfg_like is not None, "config yüklendi" if cfg_like is not None else "config yok")
    # 2 state yazılabilir
    ok, det = _writable(state_dir)
    add("state_writable", ok, det)
    # 3 kilit
    lock = SingletonLock(state_dir / "tradingbot.lock")
    if lock.is_locked_by_other():
        add("lock", True, f"kilit tutuluyor (pid={lock.read_pid() or '?'}) — motor çalışıyor", "info")
    else:
        add("lock", True, "kilit serbest", "info")
    # 4 JSON parse + schema_version
    bad: list[str] = []
    seen = 0
    for name in STATE_JSON_FILES:
        p = state_dir / name
        if not p.exists():
            continue
        seen += 1
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            bad.append(f"{name}: {exc}")
            continue
        if isinstance(d, dict) and "schema_version" in d and not isinstance(d.get("schema_version"), int):
            bad.append(f"{name}: schema_version geçersiz")
    add("state_json", not bad, f"{seen} dosya ok" if not bad else "; ".join(bad))
    # 5 vault
    if vault_dir:
        ok, det = _writable(Path(vault_dir))
        add("vault_writable", ok, det, "warn")
    else:
        add("vault_writable", True, "vault yolu verilmedi", "info")
    # 6 disk
    try:
        free_gb = shutil.disk_usage(str(state_dir if state_dir.exists() else Path.cwd())).free / 1e9
        add("disk_free", free_gb >= min_disk_gb, f"{free_gb:.1f} GB boş")
    except OSError as exc:
        add("disk_free", False, str(exc), "warn")
    # 7 saat sapması
    if quick:
        add("clock_skew", True, "quick modda atlandı", "info")
    else:
        skew = _clock_skew_seconds()
        if skew is None:
            add("clock_skew", True, "sunucu saatine ulaşılamadı (ağ yok?)", "warn")
        else:
            add("clock_skew", abs(skew) <= max_clock_skew_s, f"{skew:+.2f}s", "warn")
    # 8 bağımlılıklar
    missing = [m for m in REQUIRED_DEPS if importlib.util.find_spec(m) is None]
    add("deps_required", not missing, "hepsi var" if not missing else "eksik: " + ", ".join(missing))
    opt_missing = [m for m in OPTIONAL_DEPS if importlib.util.find_spec(m) is None]
    add("deps_optional", not opt_missing, "hepsi var" if not opt_missing else "eksik: " + ", ".join(opt_missing), "warn")
    # 9 sqlite bütünlüğü
    dbs = list(state_dir.glob("*.db")) + list(state_dir.glob("*.sqlite*")) if state_dir.exists() else []
    if not dbs:
        add("db_integrity", True, "db yok", "info")
    else:
        problems: list[str] = []
        for db in dbs:
            try:
                con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
                try:
                    res = con.execute("PRAGMA quick_check").fetchone()
                    if not res or res[0] != "ok":
                        problems.append(f"{db.name}: {res}")
                finally:
                    con.close()
            except sqlite3.Error as exc:
                problems.append(f"{db.name}: {exc}")
        add("db_integrity", not problems, "ok" if not problems else "; ".join(problems))
    # 10 yedek tazeliği
    bdir = Path(backups_dir) if backups_dir else Path(_get(cfg_like, "backups_dir", None) or state_dir.parent / "backups")
    latest = latest_backup(bdir)
    if latest is None:
        add("backup_freshness", True, f"yedek yok ({bdir})", "warn")
    else:
        age_h = (time.time() - latest.stat().st_mtime) / 3600.0
        add("backup_freshness", age_h <= backup_max_age_h, f"{latest.name} · {age_h:.1f} saat", "warn")
    # 11 kalp atışı — insan metni aynı; `code` makine-okunur ayrımı taşır (MISSING ≠ MALFORMED ≠ STALE)
    hb = read_heartbeat_age(state_dir)
    if hb is None:
        hb_file = state_dir / "heartbeat.json"
        hb_code = "HEARTBEAT_MALFORMED" if hb_file.exists() else "HEARTBEAT_MISSING"
        add("heartbeat", True, "kalp atışı yok (motor henüz başlamadı?)", "warn", code=hb_code)
    else:
        add("heartbeat", hb <= heartbeat_max_age_s, f"{hb:.0f}s (eşik {heartbeat_max_age_s:.0f}s)",
            code="HEARTBEAT_OK" if hb <= heartbeat_max_age_s else "HEARTBEAT_STALE")
    # 12 mod + ALLOW_LIVE_TRADING
    mode_d = read_json(state_dir / "mode.json", default=None)
    mode = str((mode_d or {}).get("mode", _get(cfg_like, "mode", "PAPER")) if isinstance(mode_d, dict) else _get(cfg_like, "mode", "PAPER")).upper()
    allow_live = os.environ.get("ALLOW_LIVE_TRADING", "").strip().lower() in ("1", "true", "yes")
    if mode in LIVE_MODES:
        add("mode", allow_live, f"mod {mode}" + ("" if allow_live else " ama ALLOW_LIVE_TRADING ayarlı değil"))
    else:
        add("mode", True, f"mod {mode} (kağıt)", "info")
    add("allow_live_env", not allow_live, "ALLOW_LIVE_TRADING ayarlı değil" if not allow_live else "ALLOW_LIVE_TRADING=true — DİKKAT", "warn")
    # 13 python
    add("python", sys.version_info >= (3, 11), sys.version.split()[0], "warn")

    ok_all = all(c.ok or c.severity != "fail" for c in checks)
    return DoctorReport(ok=ok_all, checks=checks, generated_at=iso(), quick=quick)


def print_report(report: DoctorReport, file=None) -> None:
    file = file or sys.stdout
    icon = {"fail": "FAIL", "warn": "WARN", "info": "info"}
    for c in report.checks:
        tag = "ok  " if c.ok else icon.get(c.severity, "FAIL")
        print(f"[{tag}] {c.name:<18} {c.detail}", file=file)
    print(f"doctor: {'OK' if report.ok else 'SORUN VAR'} · {len(report.failures)} hata · {len(report.warnings)} uyarı · {report.generated_at}", file=file)


__all__ = ["run_doctor", "print_report", "DoctorReport", "DoctorCheck", "STATE_JSON_FILES"]
