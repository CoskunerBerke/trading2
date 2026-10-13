"""P3g — gece biriminin P3 aşamaları (docs/ENGINE_BRAIN_V1.md §5.2, §5.3, §8.1 P3g): S4x (keşif adımı), S4w
(walk-forward kuyruğu, `wfqueue`), S6 (deneme defteri zinciri + HWM kopyası, dersler, parite (b), Kapı A gecesi planı),
bellek koruması (`MEMORY_GUARD`) ve gerçek veri sayaçları. Orkestrasyon (`night.py`) bu modülün fonksiyonlarını çağırır.

Yalnız PAPER araştırma kodu: ağsız; worker/bot/panel koduna ve config'ine dokunmaz; yalnız `data/research` altına yazar
(state'teki `universe.json` ve arşivlenmiş defter kayıtları SALT-OKUNUR okunur). Kapı A GEÇİCİDİR (S2 mühürlü değil):
bu adım yalnız hesaplar/kaydeder; terfi yok, manifest yok (P4).

* **MEMORY_GUARD (§5.3):** S4w ve S6'nın işleri her birimden/alt adımdan önce (ve S4w'de sonra) sürecin şimdiki RSS'ini
  (`/proc/self/status` `VmRSS`, `gc.collect()` sonrası) 0,7 × sınırla karşılaştırır; sınır = cgroup `memory.max`, yoksa
  `ENGINE_EXPECTED_MEMORY_MAX`. Aşılırsa iş kontrol noktası yazılmış olarak durur ve `MEMORY_GUARD` bayrağı yazılır.
  S1a, S3 ve S7b ASLA korunmaz (ana ölçüm ve yedek). Tepe (`VmHWM`) özet kaynaklarına yazılır.
* **Yeniden kurulum gecesi:** S1b o gece günlüğün büyük kısmını yeniden kurduysa (ör. P3g kurulum gecesi: config
  dönemleri kuruldu; ya da fidelity/günlük sürümü değişti) S4w `S1B_REBUILD` nedeniyle atlanır: VPS'te ölçülen o gecenin
  tepesi ≈ 0,76 × MemoryMax'tı; S4w'nin yükü aynı gecede eklenmez (koruma her gece tetiklenmesin).
* **Atlama kuralları:** A/B KAPALI ve SKEW gecelerinde planda yoktur; `DATA_STALE` → S4x ve S4w atlanır (ilk AÇIK/taze
  gece birikmiş barları işler); `BACKFILL_ACTIVE` (ilk doldurma birimi çalışıyor) → S4w atlanır (bellek toplamı);
  giriş evreni dondurulmuş 40 coin'le aynı değilse ya da veri mührü yoksa S4x/S4w atlanır (fark raporlanır).
"""
from __future__ import annotations

import functools
import gc
import hashlib
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

GUARD_FRACTION = 0.7
ENV_EXPECTED_MEMORY_MAX = "ENGINE_EXPECTED_MEMORY_MAX"
#: yeniden kurulum gecesi: S1b'nin bu gece kurduğu satır ≥ max(REBUILD_ROWS_MIN, REBUILD_SHARE × satır) ya da bekleyen iş
REBUILD_ROWS_MIN = 200
REBUILD_SHARE = 0.25
#: parite (b) kayıt kesimi: arşivin gecikmesi (son 3 günde kapanan kayıt yarın karşılaştırılır)
PARITY_LAG_MS = 3 * 86_400_000
PARITY_DIR = ("parity", "b")
F_MEMORY_GUARD, F_BACKFILL_ACTIVE, F_ENTRY_UNIVERSE_DIFF = "MEMORY_GUARD", "BACKFILL_ACTIVE", "ENTRY_UNIVERSE_DIFF"
F_S4X_BACKLOG, F_S4X_ERRORS, F_WF_BACKLOG, F_WF_CACHE_MISMATCH = "S4X_BACKLOG", "S4X_ERRORS", "WF_BACKLOG", "WF_CACHE_MISMATCH"
F_WF_UNIT_ERRORS, F_TRIALS_CHAIN_BROKEN, F_LESSONS_CLOSED = "WF_UNIT_ERRORS", "TRIALS_CHAIN_BROKEN", "LESSONS_CLOSED"
F_GATE_A_NOT_COMPUTED, F_PARITY_B_FAIL, F_CONFIG_EPOCHS = "GATE_A_NOT_COMPUTED", "PARITY_B_FAIL", "CONFIG_EPOCHS_FAILED"
R_NO_SEAL, R_UNIVERSE, R_S1B_SEAL = "NO_SEAL", "ENTRY_UNIVERSE_DIFF", "S1B_SEAL"


# ============================================================================ bellek
def proc_status_bytes(field: str, *, path: str = "/proc/self/status") -> int | None:
    """`/proc/self/status` alanı (`VmRSS`, `VmHWM`; kB) → bayt; yoksa None."""
    try:
        with open(path, "r", encoding="ascii", errors="replace") as fh:
            for ln in fh:
                if ln.startswith(field + ":"):
                    parts = ln.split()
                    return int(parts[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class MemoryGuard:
    """RSS > `fraction` × sınır → ihlal. `rss`: ölçüm (test yerine-geçeni); sınır yoksa koruma pasiftir (`limit_bytes`
    None; yine de ölçülür ve raporlanır)."""

    def __init__(self, limit_bytes: int | None, *, source: str = "", rss: Callable[[], int | None] | None = None,
                 fraction: float = GUARD_FRACTION) -> None:
        self.limit = int(limit_bytes) if limit_bytes else None
        self.source = source
        self.fraction = float(fraction)
        self._rss = rss or (lambda: proc_status_bytes("VmRSS"))
        self.checks = 0
        self.max_rss: int | None = None
        self.trips: list[dict[str, Any]] = []

    @classmethod
    def for_night(cls, cgroup_max: int | None, env: Mapping[str, str], *,
                  rss: Callable[[], int | None] | None = None) -> "MemoryGuard":
        if cgroup_max:
            return cls(cgroup_max, source="cgroup memory.max", rss=rss)
        try:
            v = int(str(env.get(ENV_EXPECTED_MEMORY_MAX) or "").strip())
        except ValueError:
            v = 0
        if v > 0:
            return cls(v, source=ENV_EXPECTED_MEMORY_MAX, rss=rss)
        return cls(None, source="yok", rss=rss)

    @property
    def threshold(self) -> int | None:
        return int(self.fraction * self.limit) if self.limit else None

    def check(self, where: str) -> dict[str, Any]:
        gc.collect()
        rss = self._rss()
        self.checks += 1
        if rss is not None:
            self.max_rss = rss if self.max_rss is None else max(self.max_rss, rss)
        thr = self.threshold
        ok = not (thr is not None and rss is not None and rss > thr)
        rec = {"where": where, "ok": ok, "rss_bytes": rss, "limit_bytes": self.limit, "threshold_bytes": thr,
               "ratio": round(rss / self.limit, 4) if (rss and self.limit) else None}
        if not ok:
            self.trips.append(rec)
        return rec

    def summary(self) -> dict[str, Any]:
        return {"limit_bytes": self.limit, "source": self.source, "fraction": self.fraction, "checks": self.checks,
                "max_rss_bytes": self.max_rss, "trips": [t["where"] for t in self.trips][:10],
                "vm_hwm_bytes": proc_status_bytes("VmHWM")}


# ============================================================================ ön koşullar (salt-okunur)
def _read_json(p: Path) -> Any:
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def backfill_active(paths: Any) -> tuple[bool, str | None]:
    """İlk doldurma (`tb-engine-backfill`, `engine-data --backfill`) şu an çalışıyor mu: veri durumunun `running` kaydı
    `backfill` kipinde ve süreci yaşıyor (`data_status.json`; salt-okunur)."""
    d = _read_json(Path(paths.data_status))
    run = d.get("running") if isinstance(d, dict) else None
    if isinstance(run, dict) and run.get("mode") == "backfill" and _pid_alive(run.get("pid")):
        return True, f"ilk doldurma sürüyor (pid {run.get('pid')}, başlangıç {run.get('started_at')})"
    return False, None


def ticks_from_universe(paths: Any) -> dict[str, float]:
    """`state/universe.json` (worker'ın keşif kaydı; SALT-OKUNUR) → coin → vadeli fiyat adımı (`PRICE_FILTER.tickSize`)."""
    from .universe import coin_of
    d = _read_json(Path(paths.state) / "universe.json")
    ents = (d.get("merged") or d.get("entries") or d.get("symbols") or []) if isinstance(d, dict) else []
    out: dict[str, float] = {}
    rank: dict[str, int] = {}
    for e in ents:
        if not isinstance(e, dict) or not e.get("symbol"):
            continue
        f = e.get("filters") if isinstance(e.get("filters"), dict) else {}
        raw = f.get("_tick_size") or ((f.get("PRICE_FILTER") or {}).get("tickSize") if isinstance(f.get("PRICE_FILTER"), dict)
                                     else None)
        try:
            tick = float(raw)
        except (TypeError, ValueError):
            continue
        if not tick > 0:
            continue
        mt = str(e.get("market_type") or "")
        r = 0 if mt in ("futures", "both") else 1
        c = coin_of(e["symbol"])
        if c not in out or r < rank[c]:
            out[c], rank[c] = tick, r
    return out


def preflight(paths: Any, app_dir: Path | str) -> dict[str, Any]:
    """S4x/S4w girdileri: giriş evreni (config ya da `frame_provenance`) + S1b karşılaştırması, mühürlü okuyucu, fiyat
    adımları. `skip` doluysa S4x/S4w atlanır (neden); `reader` yine verilebilir (parite (b) evrenden bağımsızdır)."""
    from . import seal_s1b as S1B
    from . import universe as U
    from .provider import DataMoving, SealedReader
    syms, info = U.entry_universe(paths, app_dir)
    diff = U.entry_universe_diff(syms)
    out: dict[str, Any] = {"entry_symbols": syms, "entry": {"source": info.get("source"), "count": info.get("count"),
                                                            "ok": diff["ok"], "missing": diff["missing"][:40],
                                                            "extra": diff["extra"][:40],
                                                            "duplicates": diff["duplicates"][:20], "seal": diff["seal"]},
                           "reader": None, "skip": None, "ticks": {}}
    try:
        out["reader"] = SealedReader.from_status(paths)
    except DataMoving as exc:
        out["skip"] = f"{R_NO_SEAL}: {exc.reason}"[:300]
        return out
    out["ticks"] = ticks_from_universe(paths)
    out["data_seal"] = getattr(out["reader"], "seal", None)
    ok, why = S1B.check()
    if not ok:
        out["skip"] = f"{R_S1B_SEAL}: {why}"[:300]
    elif not diff["ok"]:
        out["skip"] = (f"{R_UNIVERSE}: eksik {len(diff['missing'])}, fazla {len(diff['extra'])}, yinelenen "
                       f"{len(diff['duplicates'])} (kaynak {info.get('source')})")
    return out


def is_rebuild_night(s1b_result: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    j = (s1b_result or {}).get("journal") or {}
    rows, built, pending = int(j.get("rows") or 0), int(j.get("built_rows") or 0), int(j.get("pending") or 0)
    if pending > 0:
        return True, f"S1B_REBUILD: günlükte bekleyen {pending} satır (yeniden kurulum sürüyor)"
    if built >= max(REBUILD_ROWS_MIN, REBUILD_SHARE * rows) and built > 0:
        return True, f"S1B_REBUILD: bu gece {built}/{rows} günlük satırı yeniden kuruldu (bellek tepesi; S4w ertesi gece)"
    return False, None


@functools.lru_cache(maxsize=1)
def module_src_shas() -> tuple[tuple[str, str], ...] | None:
    """Uygulanmış varyantların `module_src_sha`'ları (salt-okunur git; gece başına bir kez). Hesaplanamazsa None.
    `GIT_OPTIONAL_LOCKS=0`: `git status` salt-okunur engine-app'te (ProtectSystem=strict) dizin kilidi/yazımı denemez."""
    from . import kernel as KN
    old = os.environ.get("GIT_OPTIONAL_LOCKS")
    os.environ["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        return tuple(sorted(KN.module_src_shas().items()))
    except Exception:  # noqa: BLE001 — git yoksa/ağaç kirliyse kimlik yok sayılır (yalnız bilgi alanı)
        return None
    finally:
        if old is None:
            os.environ.pop("GIT_OPTIONAL_LOCKS", None)
        else:
            os.environ["GIT_OPTIONAL_LOCKS"] = old


def spec_of_factory() -> Callable[[str], str | None]:
    from . import registry as RG
    ms = dict(module_src_shas() or ())
    vs = RG.variants_by_id()
    cache: dict[str, str | None] = {}

    def spec_of(v: str) -> str | None:
        if v not in cache:
            base = (vs.get(v) or {}).get("alias_of") or v
            try:
                cache[v] = RG.spec_sha(RG.lib_spec(v, ms[base], RG.LIB_V1)) if base in ms else None
            except Exception:  # noqa: BLE001
                cache[v] = None
        return cache[v]
    return spec_of


# ============================================================================ S4x
INTEREST_FLAGS = ("PATH_1H_GAP", "FUNDING_GAP", "F8_UNMEASURABLE", "FUNDING_MARK_MISMATCH")


def run_s4x(paths: Any, pre: Mapping[str, Any], *, now_ms: int, budget_s: float) -> dict[str, Any]:
    from . import explore as EX
    rep = EX.run_s4x(paths, pre["reader"], entry_symbols=pre["entry_symbols"], ticks=pre["ticks"], now_ms=int(now_ms),
                     budget_s=float(budget_s))
    fi = rep.get("funding_intervals") or {}
    flags = rep.get("trade_flags") or {}
    return {"status": dict(sorted((rep.get("status") or {}).items())), "plan_total": (rep.get("plan") or {}).get("total"),
            "trades_new": rep.get("trades_new", 0), "trades_final": rep.get("trades_final", 0),
            "rows_appended": rep.get("rows_appended", 0), "bars": rep.get("bars", 0), "rebuilt": rep.get("rebuilt", 0),
            "seconds": rep.get("seconds"), "budget_s": round(float(budget_s), 1),
            "errors": len(rep.get("errors") or []), "errors_head": (rep.get("errors") or [])[:3],
            "moving": len(rep.get("moving") or {}), "refused": rep.get("refused"),
            "real_data": {"trade_flags": {k: int(flags.get(k, 0)) for k in INTEREST_FLAGS},
                          "trades_final": rep.get("trades_final", 0),
                          "funding_interval_changes": sum(int(v.get("changes") or 0) for v in fi.values()),
                          "funding_interval_coins": sorted(fi)[:12],
                          "funding_intervals_h": sorted({h for v in fi.values() for h in v.get("intervals_h") or ()})}}


# ============================================================================ S4w
def explore_digest(paths: Any) -> list[str]:
    """Özetin keşif satırları (ana belge §6.8; `explore.digest_lines`), yalnız keşif durum dosyası varsa. Gece bunu
    `run_status.p3`'e koyar; özet modülü keşfi import etmez (kapı/özet yalıtımı, P3e testi)."""
    try:
        from . import explore as EX
        root = EX.explore_root(paths)
        if not root.is_dir():
            return []
        states = EX.read_states(root)
        return list(EX.digest_lines(states)) if states else []
    except Exception as exc:  # noqa: BLE001 — keşif özeti arızası geceyi/özeti düşürmez
        return [f"(keşif özeti okunamadı: {type(exc).__name__})"]


def run_s4w(paths: Any, pre: Mapping[str, Any], *, now_ms: int, deadline_mono: float, guard: MemoryGuard,
            night_budget_s: float, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    from . import wfqueue as WQ
    rep = WQ.run_s4w(paths, pre["reader"], entry_symbols=pre["entry_symbols"], ticks=pre["ticks"], now_ms=int(now_ms),
                     deadline=float(deadline_mono), clock=clock, guard=lambda: guard.check("S4w"),
                     spec_of=spec_of_factory(), night_budget_s=night_budget_s)
    return {k: rep.get(k) for k in ("stop", "backlog", "eta_nights", "units_done", "units_error", "units_no_data",
                                    "units_killed", "units_aborted", "aborted", "verified", "mismatch", "trades", "seconds",
                                    "completed_month", "next_month_queued", "previous_night_killed_in",
                                    "discarded_stale_code")
            if rep.get(k) is not None} | {"errors_head": (rep.get("errors") or [])[:3], "moving": len(rep.get("moving") or {})}


def wf_backlog(paths: Any) -> dict[str, Any]:
    """S4w çalışmadığı gecede kuyruğun görünümü (salt-okunur)."""
    from . import wfqueue as WQ
    return WQ.backlog(WQ.read_queue(WQ.root_of(paths)))


# ============================================================================ S6
GATE_A_NIGHTS = ("gate_a", "look_nights.json")


def _gate_a_file(paths: Any) -> Path:
    return Path(paths.research).joinpath(*GATE_A_NIGHTS)


def look_months(paths: Any) -> list[str]:
    """Bakışı yapılmış aylar: defterin `LOOK_A` ayları + bu sürümün kaydettiği bakış geceleri (Kapı A satırları bu sürümde
    kurulmadığından ay `HESAPLANMADI` olarak tüketilir — ön kayıtlı tek bakış; S4w ayın geri kalanında kısılmaz)."""
    from . import stats as ST
    from . import trials as TR
    try:
        months = list(ST.ledger_looks(TR.iter_rows(paths.trials_file, lenient=True)))
    except Exception:  # noqa: BLE001 — okunamayan defter: zincir denetimi ayrıca yakalar
        months = []
    d = _read_json(_gate_a_file(paths))
    for m in sorted(((d or {}).get("months") or {}) if isinstance(d, dict) else {}):
        if m not in months:
            months.append(m)
    return months


def gate_a_night(paths: Any, night: date) -> bool:
    """Kapı A bakış gecesi mi (her ayın 5'inden sonraki ilk gece; o ay için bakış yok)."""
    from . import stats as ST
    return ST.is_look_night(night, look_months(paths))


def record_look_night(paths: Any, night: date, rec: Mapping[str, Any]) -> None:
    from . import stats as ST
    p = _gate_a_file(paths)
    d = _read_json(p)
    d = d if isinstance(d, dict) and isinstance(d.get("months"), dict) else {"schema": "gate_a_look_nights_v1", "months": {}}
    d["months"][ST.look_month(night)] = {"night": night.isoformat(), **{k: rec.get(k) for k in ("status", "reason",
                                                                                                 "wf_backlog")}}
    paths.write_json(p, d)


def trials_check(paths: Any, prev_copy: Mapping[str, Any] | None) -> dict[str, Any]:
    """Deneme defteri zinciri (HWM yan dosyasıyla) + önceki gecenin özetteki HWM kopyası (defterle birlikte eski HWM geri
    yüklenirse bunu yakalar). Dönen `ok`, `code`, `hwm_copy` (yalnız zincir sağlamken)."""
    from . import trials as TR
    v = TR.verify_ledger(paths)
    ok = v.get("code") in (TR.OK, TR.EMPTY) and (v.get("ok") or v.get("code") == TR.EMPTY)
    out: dict[str, Any] = {"ok": bool(ok), "code": v.get("code"), "rows": v.get("rows"), "head_sha": v.get("head_sha"),
                           "counters": v.get("counters"), "detail": (v.get("detail") or "")[:200],
                           "hwm_file": v.get("hwm") is not None}
    if ok and prev_copy:
        chk = TR.verify_bytes(TR.read_raw(paths.trials_file), expect=dict(prev_copy))
        out["vs_summary_copy"] = chk.get("code")
        if chk.get("code") not in (TR.OK, TR.EMPTY):
            out.update(ok=False, code="ROLLED_BACK_VS_SUMMARY", detail=str(chk.get("detail") or "")[:200])
    if out["ok"]:
        out["hwm_copy"] = {"schema": TR.HWM_SCHEMA, "rows": int(v.get("rows") or 0), "head_sha": v.get("head_sha"),
                           "counters": dict(v.get("counters") or {})}
    return out


def _compact_lessons(r: Mapping[str, Any]) -> dict[str, Any]:
    keep = ("status", "as_of", "looks", "looks_by_kind", "family_m", "overflow_carried", "validation_deferred_no_config",
            "invalidated", "verdicts", "states", "counters")
    out = {k: r.get(k) for k in keep if k in r}
    ev = r.get("evidence") or {}
    out["evidence"] = {"trades": ev.get("trades"), "cells": ev.get("cells"), "books": len(ev.get("books") or [])}
    seal = r.get("seal") or {}
    out["seal"] = {k: seal.get(k) for k in ("status", "month", "specs") if k in seal}
    d = r.get("derived") or {}
    out["derived"] = {k: d.get(k) for k in ("proposed", "status", "rows") if k in d}
    return out


def run_s6(paths: Any, *, now: datetime, app_dir: Path | str, guard: MemoryGuard, prev_status: Mapping[str, Any] | None,
           budget_s: float, look_night: bool, wf_backlog: Mapping[str, Any] | None, reader: Any = None,
           clock: Callable[[], float] = time.monotonic, parity: bool = True) -> dict[str, Any]:
    """S6: (1) deneme defteri zinciri + HWM kopyası; (2) dersler (`lessons.run_lessons`); (3) parite (b) gece bağlantısı
    (bütçeli, kaldığı yerden); (4) Kapı A gecesi planı. Zincir kırıksa (2)–(4) o gece KAPALI (`TRIALS_CHAIN_BROKEN`).
    Beklenmeyen alt adım hatası `errors`'a yazılır (gece aşamayı FAILED işaretler)."""
    from . import lessons as L
    from . import trials as TR
    from .rawconfig import read_raw_config
    t0 = clock()
    flags: list[str] = []
    errors: list[str] = []
    prev = ((((prev_status or {}).get("stages") or {}).get("S6") or {}).get("result") or {}).get("trials") or {}
    out: dict[str, Any] = {"trials": trials_check(paths, prev.get("hwm_copy"))}
    if not out["trials"]["ok"]:
        flags.append(F_TRIALS_CHAIN_BROKEN)
        out.update(lessons={"status": "CLOSED", "reason": F_TRIALS_CHAIN_BROKEN},
                   gate_a={"look_night": look_night, "status": "CLOSED", "reason": F_TRIALS_CHAIN_BROKEN})
        out["flags"] = flags
        return out
    g = guard.check("S6:lessons")
    if not g["ok"]:
        flags.append(F_MEMORY_GUARD)
        out["lessons"] = {"status": "SKIPPED", "reason": F_MEMORY_GUARD}
    else:
        rc = read_raw_config(Path(app_dir) / "config.yaml")
        ms = module_src_shas()
        try:
            r = L.run_lessons(paths, now=now, raw=rc, module_src_sha=dict(ms) if ms is not None else None)
            out["lessons"] = _compact_lessons(r)
        except (TR.TrialsChainBroken, L.LessonStoreBroken, L.LessonsRefused) as exc:
            flags.append(F_TRIALS_CHAIN_BROKEN if isinstance(exc, TR.TrialsChainBroken) else F_LESSONS_CLOSED)
            out["lessons"] = {"status": "CLOSED", "reason": f"{type(exc).__name__}: {exc}"[:300]}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"lessons: {type(exc).__name__}: {exc}"[:300])
            out["lessons"] = {"status": "FAILED"}
    # (3) parite (b)
    left = float(budget_s) - (clock() - t0) - 60.0
    if parity and reader is not None and left > 30.0:
        g = guard.check("S6:parity_b")
        if not g["ok"]:
            flags.append(F_MEMORY_GUARD)
            out["parity_b"] = {"status": "SKIPPED", "reason": F_MEMORY_GUARD}
        else:
            try:
                out["parity_b"] = run_parity_b(paths, reader, now_ms=int(now.timestamp() * 1000),
                                               deadline_mono=clock() + left, guard=guard, clock=clock)
                if out["parity_b"].get("fail"):
                    flags.append(F_PARITY_B_FAIL)
                if out["parity_b"].get("stop") == F_MEMORY_GUARD:
                    flags.append(F_MEMORY_GUARD)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"parity_b: {type(exc).__name__}: {exc}"[:300])
                out["parity_b"] = {"status": "FAILED"}
    else:
        out["parity_b"] = {"status": "SKIPPED", "reason": "veri mührü/evren yok" if reader is None else "S6 payı yok",
                           **parity_b_status(paths)}
    # (4) Kapı A gecesi planı (S2 mühürlü değil: GEÇİCİ; satır kurucuları ve LOOK_A yazımı P4a)
    ga: dict[str, Any] = {"look_night": bool(look_night), "provisional": True}
    if look_night:
        flags.append(F_GATE_A_NOT_COMPUTED)
        wb = dict(wf_backlog or {})
        ga.update(status="HESAPLANMADI", wf_backlog=wb,
                  reason=("Kapı A satır kurucuları (havuz/küme/COIN_FIT/kopya), plasebo ve LOOK_A yazımı bu sürümde yok "
                          "(P4a); WF tam kurulumu: " + (str(wb.get("complete_month")) if wb.get("complete_month")
                                                       else f"yok ({wb.get('done')}/{wb.get('total')})")))
        record_look_night(paths, now.date(), ga)
    else:
        ga["status"] = "NOT_LOOK_NIGHT"
    out["gate_a"] = ga
    out["seconds"] = round(clock() - t0, 3)
    out["flags"] = sorted(set(flags))
    if errors:
        out["errors"] = errors
    return out


# ============================================================================ parite (b) gece bağlantısı
def parity_dir(paths: Any) -> Path:
    return Path(paths.research).joinpath(*PARITY_DIR)


def parity_b_status(paths: Any) -> dict[str, Any]:
    """Kayıtlı (b) sonuçlarının küçük görünümü (salt-okunur): kopya → hüküm; Formasyon `bars["4h"]` sayacı."""
    from . import parity as PA
    d = parity_dir(paths)
    verdicts: dict[str, str] = {}
    fm = None
    for vid in sorted(PA.BOOK_DIR):
        j = _read_json(d / f"{vid}.json")
        if isinstance(j, dict):
            verdicts[vid] = str(j.get("verdict"))
            if vid == "K_FM":
                fm = j.get("data_source_key")
    return {"verdicts": verdicts, "formasyon_bars_4h": fm}


class _LazySeries(Mapping):
    """Sembol (BTCUSDT) → `KN.SeriesData`; en çok bir sembol bellekte (kayıtlar sembole göre sıralı verilir)."""

    def __init__(self, reader: Any, metas: list, *, start_ms: int, now_ms: int, ticks: Mapping[str, float],
                 symbols: list[str]) -> None:
        self.reader, self.metas, self.start, self.now = reader, metas, int(start_ms), int(now_ms)
        self.ticks, self.symbols = ticks, symbols
        self._key: str | None = None
        self._val: Any = None
        self._btc: Any = None
        self.loads = 0

    def __iter__(self):
        return iter(self.symbols)

    def __len__(self) -> int:
        return len(self.symbols)

    def __getitem__(self, sym: str) -> Any:
        v = self.get(sym)
        if v is None:
            raise KeyError(sym)
        return v

    def get(self, sym: str, default: Any = None) -> Any:
        from . import explore as EX
        if sym == self._key:
            return self._val
        self._key, self._val = sym, None
        gc.collect()
        coin = EX.coin_of(sym)
        tfs = {tf for m in self.metas for tf in m.arrays}
        cd = EX.load_coin(self.reader, coin, tick=self.ticks.get(coin, 1e-9), now_ms=self.now, t_seal_ms=self.start,
                          tfs=tfs)
        self.loads += 1
        if cd.moving:
            return default
        btc = None
        if any(m.needs_btc or m.needs_btc_4h for m in self.metas):
            if self._btc is None:
                self._btc = EX.load_coin(self.reader, "BTC", tick=1e-9, now_ms=self.now, t_seal_ms=self.start,
                                         tfs={"1d", "4h"})
            btc = self._btc
        f = EX._group_frontier(cd, btc, self.metas)
        if f is None:
            return default
        self._val = EX._group_data(cd, btc, self.metas, self.start, f)
        return self._val


def _records_digest(records: list[dict], extra: Mapping[str, Any]) -> str:
    h = hashlib.sha256()
    for r in records:
        h.update(json.dumps(r, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
    h.update(json.dumps(dict(extra), sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()


def run_parity_b(paths: Any, reader: Any, *, now_ms: int, deadline_mono: float, guard: MemoryGuard | None = None,
                 clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Parite (b) gece bağlantısı: her kopya (`parity.BOOK_DIR`) için canlı defterin ARŞİVLENMİŞ kayıtları (S1a
    `closes/`; `journal.scan_archive` + `load_records`, salt-okunur) ↔ kopya, dönem başına (`parity.compare_b`; dönemler
    kurulu `library/config_epochs.json`'dan, görünümler gece görünüm dosyasından). Kayıt kesimi: son 3 günde kapananlar
    ertesi geceye kalır (arşiv gecikmesi). Sonuç kopya başına `parity/b/<kopya>.json`; değişmeyen girdide yeniden
    hesaplanmaz; en eski sonuçtan başlayarak süre bitene kadar (kaldığı yerden)."""
    from . import epochs_night as EN
    from . import explore as EX
    from . import journal as J
    from . import kernel as KN
    from . import parity as PA
    from .closes import ST_ACTIVE
    d = parity_dir(paths)
    research = Path(paths.research) if (EN.library_dir(paths) / EN.INSTALLED_NAME).exists() else None
    views = EN.views(paths)
    ticks = ticks_from_universe(paths)
    seal = getattr(reader, "seal", None)
    order = sorted(PA.BOOK_DIR, key=lambda v: (str((_read_json(d / f"{v}.json") or {}).get("computed_at_ms") or 0), v))
    rep: dict[str, Any] = {"computed": [], "unchanged": [], "stop": "DONE", "fail": [], "loads": 0}
    cut = int(now_ms) - PARITY_LAG_MS
    for vid in order:
        if clock() > deadline_mono:
            rep["stop"] = "DEADLINE"
            break
        if guard is not None and not guard.check("S6:parity_b")["ok"]:
            rep["stop"] = F_MEMORY_GUARD
            break
        book = PA.BOOK_DIR[vid]
        locs = {k: loc for k, loc in J.scan_archive(paths, book).items() if loc.status == ST_ACTIVE}
        recs = list(J.load_records(paths, book, locs).values())
        stf = KN.make_variant(vid).signal_tf
        from .ledgers import parse_ts
        recs = [r for r in recs if (parse_ts(r.get("closed_at")) is None or
                                    parse_ts(r.get("closed_at")).timestamp() * 1000 <= cut)]
        recs.sort(key=lambda r: (PA._norm_sym(r.get("symbol")), str(r.get("opened_at"))))
        with_key = sum(1 for r in recs if PA.live_signal_key(r, stf) is not None)
        dig = _records_digest(recs, {"seal": seal, "views": sorted(views), "epochs": research is not None,
                                     "kernel": KN.KERNEL_VERSION})
        old = _read_json(d / f"{vid}.json")
        if isinstance(old, dict) and old.get("input_digest") == dig:
            rep["unchanged"].append(vid)
            continue
        opened = [int(parse_ts(r.get("opened_at")).timestamp() * 1000) for r in recs if parse_ts(r.get("opened_at"))]
        res_d: dict[str, Any]
        if not recs or not opened:
            res_d = {"verdict": PA.PARITY_UNPROVEN, "epochs": [], "cohorts": {}, "note": "kayıt yok"}
        else:
            metas = [EX.meta(vid)]
            data = _LazySeries(reader, metas, start_ms=min(opened) - 2 * 86_400_000, now_ms=now_ms, ticks=ticks,
                               symbols=sorted({PA._norm_sym(r.get("symbol")) for r in recs}))
            res = PA.compare_b(vid, recs, data=data, views=views, book=book, research=research)
            rep["loads"] += data.loads
            res_d = res.to_dict()
            for e in res_d.get("epochs") or ():
                e.pop("params", None)
        doc = {"schema": "parity_b_night_v1", "variant_id": vid, "book": book, "computed_at_ms": int(now_ms),
               "input_digest": dig, "records": len(recs), "data_source_key": {"signal_tf": stf, "records": len(recs),
                                                                              "with_key": with_key},
               "data_seal": seal, **res_d}
        paths.write_json(d / f"{vid}.json", EX._json_norm(doc))
        rep["computed"].append(vid)
        if doc.get("verdict") == PA.PARITY_FAIL:
            rep["fail"].append(vid)
    rep.update(parity_b_status(paths))
    rep["fail"] = sorted({v for v, x in rep["verdicts"].items() if x == PA.PARITY_FAIL})
    return rep


__all__ = ["F_BACKFILL_ACTIVE", "F_CONFIG_EPOCHS", "F_ENTRY_UNIVERSE_DIFF", "F_GATE_A_NOT_COMPUTED", "F_LESSONS_CLOSED",
           "F_MEMORY_GUARD", "F_PARITY_B_FAIL", "F_S4X_BACKLOG", "F_S4X_ERRORS", "F_TRIALS_CHAIN_BROKEN", "F_WF_BACKLOG",
           "F_WF_CACHE_MISMATCH", "F_WF_UNIT_ERRORS", "GUARD_FRACTION", "MemoryGuard", "backfill_active", "gate_a_night",
           "is_rebuild_night", "look_months", "module_src_shas", "record_look_night", "wf_backlog", "parity_b_status", "preflight", "proc_status_bytes",
           "run_parity_b", "run_s4w", "run_s4x", "run_s6", "spec_of_factory", "ticks_from_universe", "trials_check"]
