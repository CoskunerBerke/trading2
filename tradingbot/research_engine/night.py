"""Gece birimi orkestrasyonu (`engine-night`; §2.2, §2.4, §2.9, §6.1) — P1a İSKELETİ: S0, S1a, S3, S7, S7b.

Akış (her çalıştırma; ağsız, AI yok, karar-nötr):

1. **S0** (`selfcheck`): PAPER doğrulaması → `analysis.lock` (alınamazsa beklemeden `SKIPPED_LOCKED`, çıkış 0) → disk
   koruması (20 GB / 10 GB boş; `DISK_REFUSE` ise hiçbir aşama çalışmaz) → çalışma zamanı yalıtım öz-denetimi (tutmazsa
   `ISOLATION_BROKEN`, sıfırdan farklı çıkış = uyarı birimi) → SKEW (iki SHA + ata) → yedek birimi çakışması → canlı
   config sha'sı → A/B takvimi.
2. **Plan:** normal gece S0, S1a, S3, S7, S7b. SKEW gecesi yalnız S0, S1a, S7 (belge §2.3 ve P1a kabul 13 aynen:
   S7b de çalışmaz; arşiv bir sonraki SKEW'siz gecenin yedeğine girer). A/B KAPALI gece yalnız S0
   (`AB_OFF`); KAPALI gece ama rotasyon payı < 3 gün ise S0 + S1a (`AB_OFF_ZORUNLU_ARŞİV`, gece karşılaştırmadan çıkar).
   `run_status.json` bir AŞAMA değildir, çalıştırmanın kaydıdır; her sonuçta (kilit atlaması dahil) yazılır.
3. **Son tarih:** iç son tarih = başlangıç + 2 sa 3 dk (01:37 → 03:40; `TimeoutStartSec=2h15min` sert durması 03:52);
   başlangıç 00:00–03:40 UTC arasındaysa ayrıca o günün 03:40'ı. Her aşama başlamadan bakar; geçmişse aşama
   `SKIPPED_DEADLINE` olur. P1a aşamaları atomik ve idempotenttir: yarım kalan iş ertesi gece kendiliğinden tamamlanır
   (S1a ledger'ın elde tuttuğu her şeyi her gece yeniden karşılaştırır), ayrı bir iş kuyruğu gerekmez.
4. **Telemetri** (`run_status.json`, şema `engine_run_status_v1`): aşama başına durum, süre ve CPU; toplam CPU
   (süreç + alt süreçler), tepe bellek (cgroup `memory.peak`; yoksa `ru_maxrss`), `memory.max` ve oranı, iki SHA, ata
   ilişkisi, canlı config sha'sı (değişti mi), `data_seal` (P1b'ye kadar `null`), bayraklar. Son 30 çalıştırma
   `runs/<run_id>/run_status.json` altında tutulur; en sonuncusu `summary/run_status.json`'dadır.

Çıkış kodları: 0 = SUCCESS / SKEW / AB_OFF / AB_OFF_ZORUNLU_ARŞİV / SKIPPED_LOCKED / DEADLINE (planlı bir aşama iç son
tarih yüzünden atlandı; ertesi gece tamamlanır); 1 = FAILED (bir aşama istisna attı; uyarı birimi); 3 =
ISOLATION_BROKEN; 4 = NOT_PAPER; 5 = DISK_REFUSE. SKEW sıfırla çıkar (belge yalnız "yazılır" der; `--check` gösterir).

Bu modül ağ kullanan hiçbir modülü (P1b `datastore`, `pit_universe`) import etmez (import grafiği testi).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from . import ENGINE_VERSION
from . import selfcheck as SC
from .ledgers import iso, utc_now
from .lock import SKIPPED_LOCKED, analysis_lock
from .paths import DISK_REFUSE, DISK_WARN, EnginePaths, disk_guard

RUN_SCHEMA = "engine_run_status_v1"
STAGES = ("S0", "S1a", "S3", "S7", "S7b")
PLAN_FULL = STAGES
PLAN_SKEW = ("S0", "S1a", "S7")
PLAN_AB_OFF = ("S0",)
PLAN_AB_OFF_FORCED = ("S0", "S1a")

#: §2.2: 01:37 başlangıç, iç son tarih 03:40, sert durma 03:52 (`TimeoutStartSec=2h15min`).
DEADLINE_AFTER = timedelta(hours=2, minutes=3)
NIGHT_DEADLINE_HM = (3, 40)
KEEP_RUNS = 30
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z(-\d+)?$")

R_SUCCESS, R_SKEW, R_AB_OFF, R_AB_OFF_FORCED = "SUCCESS", "SKEW", "AB_OFF", "AB_OFF_ZORUNLU_ARŞİV"
R_SKIPPED_LOCKED, R_ISOLATION_BROKEN, R_NOT_PAPER, R_DISK_REFUSE, R_FAILED, R_RUNNING, R_DEADLINE = (
    SKIPPED_LOCKED, "ISOLATION_BROKEN", "NOT_PAPER", "DISK_REFUSE", "FAILED", "RUNNING", "DEADLINE")
EXIT_CODES = {R_SUCCESS: 0, R_SKEW: 0, R_AB_OFF: 0, R_AB_OFF_FORCED: 0, R_SKIPPED_LOCKED: 0, R_DEADLINE: 0, R_FAILED: 1,
              R_ISOLATION_BROKEN: 3, R_NOT_PAPER: 4, R_DISK_REFUSE: 5}

ST_OK, ST_FAILED, ST_NOT_PLANNED, ST_SKIPPED_DEADLINE, ST_SKIPPED = "OK", "FAILED", "NOT_PLANNED", "SKIPPED_DEADLINE", "SKIPPED"
F_DEADLINE, F_STAGE_FAILED = "DEADLINE", "STAGE_FAILED"
F_CONFIG_CHANGED, F_CONFIG_UNREADABLE = "CONFIG_CHANGED", "CONFIG_UNREADABLE"
F_BACKUP_VERIFY_FAILED, F_SUMMARY_REFRESH_FAILED = "BACKUP_VERIFY_FAILED", "SUMMARY_REFRESH_FAILED"


# ============================================================================ yardımcılar
def compute_deadline(start: datetime) -> datetime:
    """İç son tarih (modül başı madde 3)."""
    dl = start + DEADLINE_AFTER
    hh, mm = NIGHT_DEADLINE_HM
    night = start.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if start < night:
        dl = min(dl, night)
    return dl


def _cpu() -> dict[str, float]:
    try:
        import resource
    except ImportError:  # pragma: no cover - motor yalnız Linux'ta çalışır
        return {"self": 0.0, "children": 0.0, "maxrss_bytes": 0.0}
    s, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"self": s.ru_utime + s.ru_stime, "children": c.ru_utime + c.ru_stime,
            "maxrss_bytes": float(max(s.ru_maxrss, c.ru_maxrss) * (1 if sys.platform == "darwin" else 1024))}


def _cgroup_value(name: str) -> int | None:
    """Kendi cgroup'unun (v2) bir sayısal dosyası (ör. `memory.peak`); yoksa/okunamazsa None."""
    try:
        lines = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for ln in lines:
        parts = ln.split(":", 2)
        if len(parts) == 3 and parts[0] == "0" and parts[1] == "":
            f = Path("/sys/fs/cgroup") / parts[2].strip().lstrip("/") / name
            try:
                txt = f.read_text(encoding="utf-8").strip()
                return None if txt == "max" else int(txt)
            except (OSError, ValueError):
                return None
    return None


def resources(cpu0: dict[str, float], mono0: float) -> dict[str, Any]:
    c = _cpu()
    peak = _cgroup_value("memory.peak")
    mmax = _cgroup_value("memory.max")
    return {"wall_s": round(time.monotonic() - mono0, 3), "cpu_self_s": round(c["self"] - cpu0["self"], 3),
            "cpu_children_s": round(c["children"] - cpu0["children"], 3), "maxrss_bytes": int(c["maxrss_bytes"]),
            "memory_peak_bytes": peak, "memory_max_bytes": mmax,
            "memory_peak_ratio": round(peak / mmax, 4) if peak and mmax else None,
            "memory_peak_source": "cgroup memory.peak" if peak is not None else "ru_maxrss (cgroup yok)"}


def _err_text(exc: BaseException) -> dict[str, Any]:
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return {"error": f"{type(exc).__name__}: {exc}"[:400], "traceback_tail": "".join(tb[-3:])[-1500:]}


def _compact_s1a(r: dict[str, Any]) -> dict[str, Any]:
    """S1a sonucunun `run_status`'a giren özeti (tam ayrıntı anlık görüntüdedir)."""
    books = {}
    for b, x in sorted((r.get("books") or {}).items()):
        rot = x.get("rotation") or {}
        books[b] = {"status": x.get("status"), "kind": x.get("kind"), "new_closes": x.get("new_closes", 0),
                    "revised": x.get("revised", 0), "new_entries": x.get("new_entries", 0), "align": x.get("align"),
                    "restored_away": x.get("restored_away", 0), "vanished": x.get("vanished", 0),
                    "recon_record": (x.get("recon_record") or {}).get("status"),
                    "recon_wallet": (x.get("recon_wallet") or {}).get("status"),
                    "rotation_days": {k: (rot.get(k) or {}).get("days") for k in ("history", "entries")},
                    "rotation_warn": any((rot.get(k) or {}).get("warn") for k in ("history", "entries")),
                    "held": {k: (rot.get(k) or {}).get("held") for k in ("history", "entries")},
                    "flags": x.get("flags") or []}
    return {"day": r.get("day"), "snapshot_written": r.get("snapshot_written"),
            "prev_snapshot_day": r.get("prev_snapshot_day"), "flags": r.get("flags") or [],
            "inconsistent": r.get("inconsistent"), "restore_events": r.get("restore_events") or [], "books": books}


def _new_run_id(paths: EnginePaths, start: datetime) -> str:
    """Çalıştırma klasörünü ATOMİK olarak sahiplen (`mkdir`, var olan klasörde hata): aynı saniyede başlayan iki deneme
    (zamanlayıcı + elle smoke) aynı klasörü paylaşıp birbirinin `run_status.json`'ını ezemez."""
    base = start.strftime("%Y%m%dT%H%M%SZ")
    rid, n = base, 1
    while True:
        try:
            paths.require_research(paths.runs / rid).mkdir(parents=False, exist_ok=False)
            return rid
        except FileExistsError:
            n += 1
            rid = f"{base}-{n}"


def prune_runs(paths: EnginePaths, keep: int = KEEP_RUNS) -> list[str]:
    """`runs/` altında yalnız çalıştırma klasörleri (ad deseni) için son `keep` tanesini tut."""
    if not paths.runs.exists():
        return []
    dirs = sorted(p for p in paths.runs.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name))
    gone = []
    for p in dirs[:-keep] if keep > 0 else dirs:
        paths.require_research(p)
        shutil.rmtree(p, ignore_errors=True)
        gone.append(p.name)
    return gone


def read_last_status(paths: EnginePaths) -> dict | None:
    p = paths.summary / "run_status.json"
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ============================================================================ çalıştırma
class _Night:
    def __init__(self, paths: EnginePaths, *, now: datetime | None, app_dir: Path, engine_dir: Path,
                 env: Mapping[str, str], probes: SC.Probes | None, runner: Callable[..., Any] | None,
                 sleep: Callable[[float], None] | None, declared_instrument: str | None,
                 backup_wait_s: float | None) -> None:
        self.paths, self.fixed_now = paths, now
        self.app_dir, self.engine_dir, self.env = Path(app_dir), Path(engine_dir), env
        self.probes, self.runner, self.sleep = probes, runner, sleep
        self.declared_instrument = declared_instrument
        self.backup_wait_s = SC.BACKUP_WAIT_MAX_S if backup_wait_s is None else backup_wait_s
        self.mono0, self.cpu0 = time.monotonic(), _cpu()
        self.start = now or utc_now()
        self.deadline = compute_deadline(self.start)
        self.run_id: str | None = None
        self.flags: set[str] = set()
        self.plan: tuple[str, ...] = ("S0",)
        self.stages: dict[str, dict[str, Any]] = {}
        self.status: dict[str, Any] = {
            "schema": RUN_SCHEMA, "engine": ENGINE_VERSION, "run_id": None, "started_at": iso(self.start),
            "finished_at": None, "deadline": iso(self.deadline), "result": R_RUNNING, "exit_code": None,
            "plan": list(self.plan), "stages": self.stages, "flags": [], "selfcheck": {}, "shas": {},
            "config": {}, "data_seal": None, "resources": {}, "pid": os.getpid()}

    # ---------------------------------------------------------------- zaman
    def clock(self) -> datetime:
        return self.fixed_now if self.fixed_now is not None else utc_now()

    # ---------------------------------------------------------------- yazım
    def _write(self, *, final: bool) -> None:
        self.status["flags"] = sorted(self.flags)
        self.status["plan"] = list(self.plan)
        if self.run_id is None:
            return
        try:
            self.paths.write_json(self.paths.runs / self.run_id / "run_status.json", self.status)
            if final and self.status["result"] != R_SKIPPED_LOCKED:
                self.paths.write_json(self.paths.summary / "run_status.json", self.status)
        except OSError as exc:  # araştırma kökü yazılamıyorsa (öz-denetim zaten bozuk) yalnız stderr
            print(f"engine-night: run_status yazılamadı: {exc}", file=sys.stderr)

    def finish(self, result: str) -> dict[str, Any]:
        self.status["result"] = result
        self.status["exit_code"] = EXIT_CODES.get(result, 1)
        self.status["finished_at"] = iso(self.clock())
        self.status["resources"] = resources(self.cpu0, self.mono0)
        if (self.stages.get("S7") or {}).get("status") == ST_OK:
            # S7 özeti çalıştırma ortasında (S7b'den önce) yazar; sonuç kesinleşince digest ve engine_summary.json
            # aynı içerikle KESİN sonuca tazelenir (panel "RUNNING" görmesin). Yedekteki kopya S7 anınındır.
            self.status["flags"] = sorted(self.flags)
            try:
                from .summary import write_summary
                write_summary(self.paths, self.status, now=self.clock())
            except Exception as exc:  # noqa: BLE001 — tazeleme başarısızlığı çalıştırmanın sonucunu değiştirmez
                self.flags.add(F_SUMMARY_REFRESH_FAILED)
                self.status["summary_refresh_error"] = f"{type(exc).__name__}: {exc}"[:300]
        self._write(final=True)
        return self.status

    # ---------------------------------------------------------------- aşama
    def stage(self, name: str, fn: Callable[[], dict[str, Any] | None], *, skip_reason: str | None = None) -> dict | None:
        if name not in self.plan:
            self.stages[name] = {"status": ST_NOT_PLANNED}
            return None
        if skip_reason:
            self.stages[name] = {"status": ST_SKIPPED, "reason": skip_reason}
            return None
        if self.clock() > self.deadline:
            self.stages[name] = {"status": ST_SKIPPED_DEADLINE, "reason": f"iç son tarih {iso(self.deadline)} geçti"}
            self.flags.add(F_DEADLINE)
            return None
        t0, c0, at = time.monotonic(), _cpu(), self.clock()
        rec: dict[str, Any] = {"status": ST_OK, "started_at": iso(at)}
        out = None
        try:
            out = fn()
        except Exception as exc:  # noqa: BLE001 — aşama hatası çalıştırmayı durdurmaz; FAILED olarak kaydedilir
            rec.update(status=ST_FAILED, **_err_text(exc))
            self.flags.add(F_STAGE_FAILED)
        c1 = _cpu()
        rec.update(duration_s=round(time.monotonic() - t0, 3),
                   cpu_s=round((c1["self"] - c0["self"]) + (c1["children"] - c0["children"]), 3))
        if isinstance(out, dict):
            rec["result"] = out
        self.stages[name] = rec
        self._write(final=False)
        return out

    # ---------------------------------------------------------------- S0
    def s0(self) -> str | None:
        """S0; durdurucu bir sonuç varsa onu döndürür (çalıştırma biter), yoksa None ve `self.plan` kurulur."""
        t0, c0 = time.monotonic(), _cpu()
        sc: dict[str, Any] = self.status["selfcheck"]
        rec: dict[str, Any] = {"status": ST_OK, "started_at": iso(self.clock())}
        self.stages["S0"] = rec

        def _close(stop: str | None) -> str | None:
            c1 = _cpu()
            rec.update(duration_s=round(time.monotonic() - t0, 3),
                       cpu_s=round((c1["self"] - c0["self"]) + (c1["children"] - c0["children"]), 3))
            if stop:
                rec["status"] = stop
            return stop

        disk = disk_guard(self.paths)
        sc["disk"] = disk
        if disk["status"] == DISK_REFUSE:
            return _close(R_DISK_REFUSE)
        if disk["status"] == DISK_WARN:
            self.flags.add("DISK_WARN")
        iso_r = SC.run_isolation(self.paths, app_dir=self.app_dir, env=self.env, probes=self.probes)
        sc["isolation"] = iso_r
        if iso_r["status"] != SC.OK:
            sc["status"] = SC.ISOLATION_BROKEN
            return _close(R_ISOLATION_BROKEN)
        sc["status"] = SC.OK
        self.paths.ensure_tree()
        skew = SC.check_skew(self.app_dir, self.engine_dir, **({"runner": self.runner} if self.runner else {}))
        sc["skew"] = skew
        self.status["shas"] = {"app": skew.get("app_sha"), "engine": skew.get("engine_sha"),
                               "relation": skew.get("relation")}
        kw: dict[str, Any] = {"max_wait_s": self.backup_wait_s}
        if self.runner:
            kw["runner"] = self.runner
        if self.sleep:
            kw["sleep"] = self.sleep
        bk = SC.check_backup_overlap(**kw)
        sc["backup_unit"] = bk
        if bk["status"] in (SC.BACKUP_OVERLAP, SC.BACKUP_STATE_UNKNOWN):
            self.flags.add(bk["status"])
        from .rawconfig import read_raw_config
        rc = read_raw_config(self.app_dir / "config.yaml")
        prev = read_last_status(self.paths)
        prev_sha = ((prev or {}).get("config") or {}).get("sha256")
        changed = bool(prev_sha and rc.sha256 and prev_sha != rc.sha256)
        self.status["config"] = {"path": rc.path, "ok": rc.ok, "sha256": rc.sha256, "error": rc.error,
                                 "prev_sha256": prev_sha, "changed": changed,
                                 "extra_entries": rc.values.get("learning_mode.extra_entries")}
        if not rc.ok:
            self.flags.add(F_CONFIG_UNREADABLE)
        if changed:
            self.flags.add(F_CONFIG_CHANGED)
        day = self.start.strftime("%Y-%m-%d")
        eng = skew.get("engine_sha")
        first = SC.register_epoch(self.paths, eng, self.start, self.run_id or "") if eng else None
        ab = SC.ab_decision(day, first, rotation_min_days=None)
        if ab["status"] == SC.AB_OFF:
            # KAPALI gece: rotasyon payı (salt-okunur ledger okuması) yalnız burada gerekir (§2.9 istisnası)
            from .closes import rotation_status
            rot = rotation_status(self.paths, now=self.clock())
            sc["rotation_for_ab"] = {"min_days": rot.get("min_days"), "warn": rot.get("warn")}
            ab = SC.ab_decision(day, first, rotation_min_days=rot.get("min_days"))
        sc["ab"] = ab
        if ab["status"] == SC.AB_OFF:
            self.plan = PLAN_AB_OFF
        elif ab["status"] == SC.AB_OFF_FORCED:
            self.plan = PLAN_AB_OFF_FORCED
        elif skew["status"] != SC.OK:
            self.plan = PLAN_SKEW
        else:
            self.plan = PLAN_FULL
        if skew["status"] != SC.OK:
            self.flags.add(SC.SKEW)
        return _close(None)

    # ---------------------------------------------------------------- ana akış
    def run(self) -> dict[str, Any]:
        paper = SC.check_paper(self.paths.state, self.env)
        self.status["selfcheck"]["paper"] = paper
        try:
            self.paths.runs.mkdir(parents=True, exist_ok=True)
            self.run_id = _new_run_id(self.paths, self.start)
        except OSError as exc:
            self.status["selfcheck"]["isolation"] = {"status": SC.ISOLATION_BROKEN, "broken": ["writable_research"],
                                                     "error": str(exc)[:200]}
            return self.finish(R_ISOLATION_BROKEN)
        self.status["run_id"] = self.run_id
        if paper["status"] != SC.OK:
            self.status["selfcheck"]["status"] = SC.NOT_PAPER
            self.stages["S0"] = {"status": R_NOT_PAPER, "reason": paper.get("error")}
            return self.finish(R_NOT_PAPER)
        lk = analysis_lock(self.paths)
        try:
            got = lk.try_acquire()
        except OSError as exc:
            self.status["selfcheck"]["isolation"] = {"status": SC.ISOLATION_BROKEN, "broken": ["writable_research"],
                                                     "error": str(exc)[:200]}
            return self.finish(R_ISOLATION_BROKEN)
        if not got:
            self.stages["S0"] = {"status": R_SKIPPED_LOCKED, "reason": f"analysis.lock tutuluyor (pid {lk.read_pid() or '?'})"}
            return self.finish(R_SKIPPED_LOCKED)
        try:
            self._write(final=False)
            try:
                stop = self.s0()
            except Exception as exc:  # noqa: BLE001 — beklenmeyen S0 hatası: kayda geçer, sıfırdan farklı çıkış
                self.stages["S0"] = {**self.stages.get("S0", {}), "status": ST_FAILED, **_err_text(exc)}
                self.flags.add(F_STAGE_FAILED)
                return self.finish(R_FAILED)
            if stop:
                return self.finish(stop)
            self._write(final=False)
            now_arg = self.fixed_now
            self.stage("S1a", lambda: self._s1a(now_arg))
            s1_failed = self.stages.get("S1a", {}).get("status") == ST_FAILED
            self.stage("S3", lambda: self._s3(now_arg), skip_reason="S1a başarısız" if s1_failed else None)
            self.stage("S7", self._s7)
            self.stage("S7b", self._s7b)
            if any(s.get("status") == ST_FAILED for s in self.stages.values()):
                result = R_FAILED
            elif any(s.get("status") == ST_SKIPPED_DEADLINE for s in self.stages.values()):
                result = R_DEADLINE
            elif self.plan == PLAN_AB_OFF:
                result = R_AB_OFF
            elif self.plan == PLAN_AB_OFF_FORCED:
                result = R_AB_OFF_FORCED
            elif (self.status["selfcheck"].get("skew") or {}).get("status") != SC.OK:
                result = R_SKEW
            else:
                result = R_SUCCESS
            self.status["pruned_runs"] = prune_runs(self.paths)
            return self.finish(result)
        finally:
            lk.release()

    # ---------------------------------------------------------------- aşama gövdeleri
    def _s1a(self, now: datetime | None) -> dict[str, Any]:
        from .closes import run_s1a
        r = run_s1a(self.paths, now=now, run_id=self.run_id)
        self.flags.update(r.get("flags") or [])
        return _compact_s1a(r)

    def _s3(self, now: datetime | None) -> dict[str, Any]:
        from .daily_target import run_s3
        return run_s3(self.paths, now=now, run_id=self.run_id, declared_instrument=self.declared_instrument)

    def _s7(self) -> dict[str, Any]:
        from .summary import write_summary
        self.status["flags"] = sorted(self.flags)
        self.status["plan"] = list(self.plan)
        self.status["resources"] = resources(self.cpu0, self.mono0)      # S7 anına kadar (son değer finish'te)
        return write_summary(self.paths, self.status, now=self.clock())

    def _s7b(self) -> dict[str, Any]:
        from .backup import make_backup
        r = make_backup(self.paths, now=self.clock())
        if r.get("status") != "OK":
            self.flags.add(F_BACKUP_VERIFY_FAILED)
            raise RuntimeError(f"araştırma yedeği doğrulanamadı: {r.get('error')}")
        return r


def run_night(paths: EnginePaths | None = None, *, now: datetime | None = None, app_dir: Path | str = SC.APP_DIR,
              engine_dir: Path | str | None = None, env: Mapping[str, str] | None = None, probes: SC.Probes | None = None,
              runner: Callable[..., Any] | None = None, sleep: Callable[[float], None] | None = None,
              declared_instrument: str | None = None, backup_wait_s: float | None = None) -> dict[str, Any]:
    """Bir gece çalıştırması. `now` verilirse (test/sahte VPS) bütün okumalar ve zaman damgaları o ana sabitlenir;
    `probes`/`runner`/`sleep` öz-denetim denemelerinin yerine-geçenleridir. Dönen: `run_status` sözlüğü."""
    env = os.environ if env is None else env
    paths = paths or EnginePaths.from_env(env)
    n = _Night(paths, now=now, app_dir=Path(app_dir), engine_dir=Path(engine_dir or SC.ENGINE_DIR_DEFAULT), env=env,
               probes=probes, runner=runner, sleep=sleep, declared_instrument=declared_instrument,
               backup_wait_s=backup_wait_s)
    return n.run()


__all__ = ["DEADLINE_AFTER", "EXIT_CODES", "KEEP_RUNS", "PLAN_AB_OFF", "PLAN_AB_OFF_FORCED", "PLAN_FULL", "PLAN_SKEW",
           "RUN_SCHEMA", "R_AB_OFF", "R_AB_OFF_FORCED", "R_DEADLINE", "R_DISK_REFUSE", "R_FAILED", "R_ISOLATION_BROKEN",
           "R_NOT_PAPER", "R_SKEW", "R_SKIPPED_LOCKED", "R_SUCCESS", "STAGES", "compute_deadline", "prune_runs",
           "read_last_status", "resources", "run_night"]
