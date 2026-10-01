"""Replay araştırma pipeline'ı — tek süreçte sıralı aşamalar + KALICI (durable) durum manifesti.

Neden: aşamaları çağıran shell'den yönetmek SSH koptuğunda zinciri kırıyordu. Bu sürücü tek bir
transient systemd service içinde çalışır; `replay → train → evaluate` sırasını kendi yürütür ve her
aşama geçişini `state/replay/<run_id>/run_status.json` dosyasına ATOMİK yazar. Böylece:
* çağıran shell/SSH ölse bile pipeline devam eder (systemd süreci yönetir),
* bir aşama non-zero ise sonraki aşamalar BAŞLAMAZ,
* unit `--collect` ile silinse bile gerçek sonuç manifestten okunur,
* hiç başlamamış aşama `NOT_STARTED` kalır (asla "başarılı" görünmez).

Manifest secret İÇERMEZ: yalnız aşama durumları, exit kodları, artifact özetleri, kaynak sınırları ve
ölçülen telemetri (wall/CPU/bellek). Telemetri determinism hash'ine GİRMEZ.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, read_json, utc_now
from .research import (EVAL_REPORT, TRAIN_MANIFEST, ReplaySafetyError, evaluate_replay, resolve_replay_dir,
                       resolve_run_seed, train_replay_challenger)

RUN_STATUS = "run_status.json"
STATUS_SCHEMA_VERSION = 1
STAGES = ("replay", "train", "evaluate")
ARTIFACTS = {"replay": "replay_result.json", "train": TRAIN_MANIFEST, "evaluate": EVAL_REPORT}
NOT_STARTED, RUNNING, SUCCESS, FAILED, BLOCKED = "NOT_STARTED", "RUNNING", "SUCCESS", "FAILED", "BLOCKED"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def artifact_summary(run_dir: Path) -> dict:
    out = {}
    for name in ("replay_result.json", TRAIN_MANIFEST, EVAL_REPORT, "trade_memory.jsonl", "models.json"):
        p = Path(run_dir) / name
        out[name] = {"exists": p.is_file(), "bytes": p.stat().st_size if p.is_file() else 0, "sha256": _sha256(p)[:16]}
    return out


def _rusage():
    try:                                       # `resource` yalnız POSIX'te var (VPS Linux)
        import resource as _r
        return _r
    except ImportError:
        return None


def _cpu_seconds() -> float:
    r = _rusage()
    if r is None:
        return round(time.process_time(), 3)   # taşınabilir yedek (yalnız bu süreç)
    a, b = r.getrusage(r.RUSAGE_SELF), r.getrusage(r.RUSAGE_CHILDREN)
    return float(a.ru_utime + a.ru_stime + b.ru_utime + b.ru_stime)


def _peak_mb() -> float:
    r = _rusage()
    if r is None:
        return 0.0                             # ölçülemiyor → 0 (uydurma değer yok)
    return round(r.getrusage(r.RUSAGE_SELF).ru_maxrss / 1024.0, 1)   # Linux: KB


def read_run_status(run_dir: Path) -> dict | None:
    d = read_json(Path(run_dir) / RUN_STATUS, default=None)
    return d if isinstance(d, dict) else None


def _write(run_dir: Path, status: dict) -> None:
    status["updated_at"] = iso(utc_now())
    status["artifacts"] = artifact_summary(run_dir)
    atomic_write_json(Path(run_dir) / RUN_STATUS, status, indent=1)


def new_status(run_id: str, run_dir: Path, stages: list[str], *, unit: str = "", limits: dict | None = None,
               fingerprint: str = "") -> dict:
    return {"schema_version": STATUS_SCHEMA_VERSION, "run_id": run_id, "run_dir": str(run_dir),
            "action": "+".join(stages), "requested_stages": list(stages), "current_stage": None,
            "stage_states": {s: {"state": NOT_STARTED, "exit_code": None, "started_at": None, "finished_at": None,
                                 "wall_seconds": None, "error_code": None} for s in stages},
            "state": NOT_STARTED, "exit_code": None, "started_at": iso(utc_now()), "finished_at": None,
            "unit": unit, "invocation_pid": os.getpid(), "input_fingerprint": fingerprint,
            "resource_limits": limits or {}, "telemetry": {}, "artifacts": {}}


def status_verdict(run_dir: Path, *, unit_active: bool = False) -> dict:
    """Kalıcı manifest + artifact'lerden GERÇEK sonucu türet. Aktif unit asla SUCCESS gösterilmez;
    manifest ile artifact çelişirse INCONSISTENT (fail-closed)."""
    run_dir = Path(run_dir)
    st = read_run_status(run_dir)
    arts = artifact_summary(run_dir)
    if st is None:
        if unit_active:
            return {"state": RUNNING, "source": "unit", "note": "unit çalışıyor, manifest henüz yok"}
        any_art = any(v["exists"] for v in arts.values())
        return {"state": (BLOCKED if any_art else NOT_STARTED), "source": "artifacts",
                "note": ("manifest yok ama artifact var — sonuç doğrulanamıyor (fail-closed)" if any_art
                         else "hiç çalıştırılmadı"), "artifacts": arts}
    state = str(st.get("state") or NOT_STARTED)
    if unit_active and state in (SUCCESS, FAILED):
        state = RUNNING                     # unit hâlâ aktifken bitmiş gösterme
    stages = st.get("stage_states") or {}
    inconsistent = []
    for stage, info in stages.items():
        art = ARTIFACTS.get(stage)
        if str(info.get("state")) == SUCCESS and art and not arts.get(art, {}).get("exists"):
            inconsistent.append(f"{stage}: SUCCESS ama {art} yok")
    if inconsistent:
        return {"state": "INCONSISTENT", "source": "manifest+artifacts", "problems": inconsistent,
                "stage_states": stages, "artifacts": arts}
    return {"state": state, "source": "manifest", "stage_states": stages, "exit_code": st.get("exit_code"),
            "current_stage": st.get("current_stage"), "unit": st.get("unit"), "telemetry": st.get("telemetry"),
            "artifacts": arts, "started_at": st.get("started_at"), "finished_at": st.get("finished_at"),
            "input_fingerprint": st.get("input_fingerprint"), "resource_limits": st.get("resource_limits")}


def _assert_paper(cfg: Any) -> None:
    """Her aşamadan ÖNCE PAPER + live-path kapalı doğrulaması (fail-closed)."""
    from ..risk import ModeState
    ms = ModeState(Path(cfg.state_path) / "mode.json")
    if str(ms.mode.value).upper() != "PAPER" or ms.is_live_order_path_enabled():
        raise ReplaySafetyError(f"mod {ms.mode.value} / live_order_path={ms.is_live_order_path_enabled()} — "
                                "araştırma hattı yalnız PAPER'da çalışır")


def run_pipeline(cfg: Any, run_id: str, stages: list[str], *, state_root: Path | None = None, unit: str = "",
                 limits: dict | None = None, replay_args: dict | None = None, seed: int | None = None,
                 min_samples: int | None = None) -> int:
    """Aşamaları sırayla yürütür; her geçişi atomik yazar. İlk hata sonraki aşamaları BAŞLATMAZ."""
    from ..cli_v3 import cmd_historical_replay          # replay aşaması mevcut CLI yolunu kullanır

    bad = [s for s in stages if s not in STAGES]
    if bad:
        raise ReplaySafetyError(f"bilinmeyen aşama: {bad}")
    run_dir = resolve_replay_dir(cfg.state_path, run_id, state_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    resolve_replay_dir(cfg.state_path, run_id, state_root, must_exist=True)      # mkdir sonrası tekrar
    fingerprint = hashlib.sha256(repr(sorted((replay_args or {}).items())).encode()).hexdigest()[:16]
    st = new_status(run_id, run_dir, stages, unit=unit, limits=limits, fingerprint=fingerprint)
    st["state"] = RUNNING
    _write(run_dir, st)
    t0, cpu0 = time.time(), _cpu_seconds()
    rc = 0
    for stage in stages:
        try:
            _assert_paper(cfg)                                                    # her aşamadan ÖNCE
        except ReplaySafetyError as exc:
            st["stage_states"][stage].update({"state": BLOCKED, "exit_code": 2, "finished_at": iso(utc_now()),
                                              "error_code": "PAPER_GUARD"})
            st.update({"state": BLOCKED, "exit_code": 2, "current_stage": stage, "finished_at": iso(utc_now())})
            _write(run_dir, st)
            print(f"BLOCK: {exc}")
            return 2
        st["current_stage"] = stage
        st["stage_states"][stage].update({"state": RUNNING, "started_at": iso(utc_now())})
        _write(run_dir, st)
        s0 = time.time()
        try:
            if stage == "replay":
                from types import SimpleNamespace
                rc = cmd_historical_replay(cfg, SimpleNamespace(**(replay_args or {})))
                if rc != 0:
                    raise ReplaySafetyError(f"historical-replay exit {rc}")
            elif stage == "train":
                train_replay_challenger(cfg, run_dir, seed=seed)
            else:
                evaluate_replay(cfg, run_dir, min_samples=min_samples)
            st["stage_states"][stage].update({"state": SUCCESS, "exit_code": 0, "finished_at": iso(utc_now()),
                                              "wall_seconds": round(time.time() - s0, 1)})
            _write(run_dir, st)
        except Exception as exc:  # noqa: BLE001 — hata kodu yazılır, DETAY/secret dump edilmez
            st["stage_states"][stage].update({"state": FAILED, "exit_code": 1, "finished_at": iso(utc_now()),
                                              "wall_seconds": round(time.time() - s0, 1),
                                              "error_code": type(exc).__name__})
            st.update({"state": FAILED, "exit_code": 1, "finished_at": iso(utc_now()),
                       "telemetry": {"wall_seconds": round(time.time() - t0, 1),
                                     "cpu_seconds": round(_cpu_seconds() - cpu0, 1), "peak_memory_mb": _peak_mb()}})
            _write(run_dir, st)
            print(f"STAGE_FAILED stage={stage} error={type(exc).__name__}: {exc}")
            return 1
    st.update({"state": SUCCESS, "exit_code": 0, "current_stage": None, "finished_at": iso(utc_now()),
               "telemetry": {"wall_seconds": round(time.time() - t0, 1),
                             "cpu_seconds": round(_cpu_seconds() - cpu0, 1), "peak_memory_mb": _peak_mb()}})
    _write(run_dir, st)
    return 0


def verify_existing_replay(cfg: Any, run_dir: Path, *, expect_seed: int | None = None) -> dict:
    """Tamamlanmış bir replay'in artifact'lerini DEĞİŞTİRMEDEN doğrula (train-only akışının ön koşulu)."""
    run_dir = Path(run_dir)
    res = read_json(run_dir / "replay_result.json", default=None)
    if not isinstance(res, dict) or not res.get("windows"):
        raise ReplaySafetyError("replay_result.json yok/geçersiz — önce `replay` aşaması gerekir")
    mem = run_dir / "trade_memory.jsonl"
    if not mem.is_file() or mem.stat().st_size == 0:
        raise ReplaySafetyError("replay trade_memory.jsonl yok/boş")
    bounds = [w.get("bounds") for w in res["windows"] if isinstance(w, dict)]
    if not all(isinstance(b, dict) for b in bounds):
        raise ReplaySafetyError("replay_result.json kesin pencere sınırları (bounds) içermiyor")
    seed, source = resolve_run_seed(run_dir, expect_seed)
    return {"run_dir": str(run_dir), "seed": seed, "seed_source": source, "windows": len(bounds),
            "memory_rows": sum(1 for _ in mem.open(encoding="utf-8")),
            "determinism_hash": res.get("determinism_hash"), "artifacts": artifact_summary(run_dir)}


__all__ = ["ARTIFACTS", "RUN_STATUS", "STAGES", "STATUS_SCHEMA_VERSION", "artifact_summary", "new_status",
           "read_run_status", "run_pipeline", "status_verdict", "verify_existing_replay",
           "NOT_STARTED", "RUNNING", "SUCCESS", "FAILED", "BLOCKED"]
