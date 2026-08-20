"""Replay araştırma hattı — plan (read-only) → challenger eğitimi → objektif OOS değerlendirmesi.

Güvenlik sözleşmesi (kod düzeyinde zorunlu):
* Bütün yazımlar YALNIZ `state/replay/<run_id>/` (ya da açıkça verilen replay kökü) altına yapılır.
  `resolve_replay_dir` path traversal, symlink kaçışı, boş/tehlikeli run-id ve canlı state çakışmasını
  fail-closed reddeder; canlı `state/models.json`, `learn_v2.json`, `futures_ledger.json`,
  `trade_memory.jsonl` ve açık pozisyonlar hiçbir kod yolunda AÇILMAZ.
* Eğitim yalnız `HISTORICAL_REPLAY` namespace'inden okur; canlı champion terfisi (promote) YAPILMAZ —
  üretilen model replay registry'sinde CANDIDATE olarak kalır ve rapor "shadow adayı" ile sınırlıdır.
* Tekrarlı çalıştırma deterministik ve idempotenttir: aynı girdi hash'i → yeniden eğitim yok, aynı artifact.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, from_iso, iso, read_json, utc_now

TRAIN_MANIFEST = "train_manifest.json"
EVAL_REPORT = "evaluation.json"
RESEARCH_VERSION = 1

# Hedef VPS'te ÖLÇÜLEN pattern index yoğunluğu (577.796 olay → max RSS 2.861.600 KB ≈ 5.07 KB/olay).
# Ölçümdür, garanti değildir; evren/timeframe değiştikçe değişir.
BYTES_PER_PATTERN_EVENT = 5_200
BYTES_PER_FRAME_ROW = 1_100          # replay frames (indikatörlü çoklu tf) + primary kopyası
DEFAULT_HOST_RESERVE_MB = 1024       # OS + dashboard + backup için ayrılan pay
DEFAULT_WORKER_RESERVE_MB = 900      # çalışan worker'ın 4G tavanına kalan büyüme payı
_LIVE_STATE_FILES = ("futures_ledger.json", "spot_ledger.json", "trade_memory.jsonl", "learn_v2.json",
                     "models.json", "risk.json", "portfolio.json", "mode.json")


class ReplaySafetyError(ValueError):
    """İzolasyon ya da kapasite sözleşmesi ihlali (fail-closed)."""


# --------------------------------------------------------------------------- izolasyon
def resolve_replay_dir(state_path: Path | str, run_id: str, state_root: Path | str | None = None, *, must_exist: bool = False) -> Path:
    """`<replay_root>/<run_id>` yolunu güvenle çöz. Traversal/symlink/boş id/canlı çakışma → ReplaySafetyError."""
    rid = str(run_id or "").strip()
    if not rid:
        raise ReplaySafetyError("run-id boş olamaz")
    if rid in (".", "..") or any(ch in rid for ch in ("/", "\\", "\0")) or os.path.isabs(rid):
        raise ReplaySafetyError(f"geçersiz run-id (yol ayracı/mutlak yol yasak): {rid!r}")
    if rid.startswith(("-", ".")):
        raise ReplaySafetyError(f"geçersiz run-id (nokta/tire ile başlayamaz): {rid!r}")
    live = Path(state_path).resolve()
    root = Path(state_root).resolve() if state_root else (live / "replay")
    if root == live:
        raise ReplaySafetyError("replay kökü canlı state dizini olamaz")
    target = (root / rid)
    if target.is_symlink():
        raise ReplaySafetyError(f"replay dizini symlink olamaz: {target}")
    resolved = target.resolve()
    if resolved.parent != root.resolve():          # symlink/.. ile kökten kaçış
        raise ReplaySafetyError(f"replay dizini kökün dışına çıkıyor: {resolved}")
    # Varsayılan yerleşim `state/replay/<run_id>` canlı state'in ALTINDADIR (meşru); yasak olan:
    # dizinin canlı state'in KENDİSİ ya da ATASI olması ve canlı defterlerin bulunduğu klasöre yazmak.
    if resolved == live or resolved in live.parents:
        raise ReplaySafetyError(f"replay dizini canlı state ile çakışıyor: {resolved}")
    if resolved.parent == live:
        raise ReplaySafetyError(f"replay dizini canlı state'in doğrudan altında olamaz (replay kökü kullanın): {resolved}")
    if resolved.exists() and live.exists() and os.path.samefile(resolved, live):
        raise ReplaySafetyError("replay dizini canlı state ile aynı dizin (hardlink/mount)")
    for name in _LIVE_STATE_FILES:                 # replay içindeki defter symlink'i canlıya yazmasın
        p = resolved / name
        if p.is_symlink():
            raise ReplaySafetyError(f"replay state dosyası symlink olamaz: {p}")
    if must_exist and not resolved.is_dir():
        raise ReplaySafetyError(f"replay dizini yok: {resolved}")
    return resolved


def assert_live_state_untouched(state_path: Path | str) -> dict[str, str]:
    """Canlı state dosyalarının sha256 haritası — testler ve runner öncesi/sonrası karşılaştırma için."""
    live = Path(state_path)
    out: dict[str, str] = {}
    for name in _LIVE_STATE_FILES:
        p = live / name
        if p.exists():
            out[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# --------------------------------------------------------------------------- plan (read-only)
def _available_mb() -> float | None:
    """Kullanılabilir RAM (MB). Yalnız Linux /proc/meminfo; ölçülemezse None (çağıran fail-closed davranır)."""
    if platform.system() != "Linux":
        return None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return float(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


@dataclass
class ReplayPlan:
    run_id: str
    market: str
    tf: str
    symbols: list[str]
    stride: int
    seed: int
    series: list[dict] = field(default_factory=list)
    total_rows: int = 0
    timeline_bars: int = 0
    pattern_events: int = 0
    est_memory_mb: float = 0.0
    est_cpu_minutes: float = 0.0
    available_mb: float | None = None
    budget_mb: float | None = None
    risk_class: str = "UNKNOWN"
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    point_in_time: bool = False

    @property
    def ok(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["ok"] = self.ok
        d["survivorship_bias"] = {"present": True, "note": "history yalnız bugün listeli sembolleri içerir; delisted kapsam dışı"}
        return d


def plan_replay(cfg: Any, store: Any, *, run_id: str, symbols: list[str], market: str = "futures", tf: str = "4h",
                stride: int = 1, seed: int = 0, start_ms: int | None = None, end_ms: int | None = None,
                patterns: bool = True, pattern_stride: int = 1, min_bars: int = 250,
                available_mb: float | None = None, host_reserve_mb: float = DEFAULT_HOST_RESERVE_MB,
                worker_reserve_mb: float = DEFAULT_WORKER_RESERVE_MB) -> ReplayPlan:
    """Veri OKUMADAN (yalnız manifest) satır/timeline/olay/bellek/CPU tahmini + kapasite sınıfı. Hiçbir şey yazmaz."""
    stride = max(1, int(stride))
    plan = ReplayPlan(run_id=run_id, market=market, tf=tf, symbols=list(symbols), stride=stride, seed=int(seed))
    aux_rows = 0
    for sym in symbols:
        rows = first = last = 0
        try:
            m = store.manifest(market, sym, tf)
            rows, first, last = int(m.row_count or 0), int(m.first_ts_ms or 0), int(m.last_ts_ms or 0)
            bad = len(getattr(m, "bad_chunks", []) or [])
            gaps = int(getattr(m, "gap_count", 0) or 0)
        except Exception as exc:  # noqa: BLE001 — manifest okunamıyorsa seri yok sayılmaz, bloklanır
            plan.blockers.append(f"{sym}: manifest okunamadı ({type(exc).__name__})")
            continue
        plan.series.append({"symbol": sym, "rows": rows, "first_ts_ms": first, "last_ts_ms": last, "gaps": gaps, "bad_chunks": bad})
        plan.total_rows += rows
        if rows < min_bars:
            plan.blockers.append(f"{sym}: yetersiz veri ({rows} < {min_bars} bar)")
        if bad:
            plan.blockers.append(f"{sym}: {bad} bozuk parça (fail-closed)")
        if gaps:
            plan.warnings.append(f"{sym}: {gaps} boşluk")
        for aux in ("1d", "1h"):
            try:
                aux_rows += int(store.manifest(market, sym, aux).row_count or 0)
            except Exception:  # noqa: BLE001 — yardımcı tf yoksa replay yine çalışır
                plan.warnings.append(f"{sym}: {aux} serisi yok (bağlam sınırlı)")
    if not plan.series:
        plan.blockers.append("seri bulunamadı")
    # timeline: seçilen aralıktaki birincil barlar / stride
    spans = [s for s in plan.series if s["rows"]]
    if spans:
        lo = max(start_ms or 0, min(s["first_ts_ms"] for s in spans))
        hi = min(end_ms or 2**62, max(s["last_ts_ms"] for s in spans))
        from ..market.providers import tf_ms
        step = tf_ms(tf)
        bars = max(0, (hi - lo) // step) if hi > lo else 0
        plan.timeline_bars = int(bars // stride)
        if plan.timeline_bars <= 0:
            plan.blockers.append("seçilen aralıkta karar barı yok")
    in_range_rows = sum(s["rows"] for s in plan.series)
    plan.pattern_events = int(in_range_rows / max(1, pattern_stride)) if patterns else 0
    plan.est_memory_mb = round((plan.pattern_events * BYTES_PER_PATTERN_EVENT
                                + (in_range_rows + aux_rows) * BYTES_PER_FRAME_ROW) / 1e6, 1)
    # CPU: ölçülen replay kadansı ≈ 55 karar/s (4h, pattern açık, tek çekirdek) → dakikaya çevir
    plan.est_cpu_minutes = round(plan.timeline_bars * max(1, len(plan.series)) / 55.0 / 60.0, 1)
    avail = available_mb if available_mb is not None else _available_mb()
    plan.available_mb = round(avail, 1) if avail is not None else None
    if avail is None:
        plan.blockers.append("kullanılabilir RAM ölçülemedi (Linux dışı?) — --assume-available-mb ile açıkça verin (fail-closed)")
    else:
        budget = avail - host_reserve_mb - worker_reserve_mb
        plan.budget_mb = round(budget, 1)
        if budget <= 0:
            plan.blockers.append(f"rezervler sonrası bütçe yok ({budget:.0f} MB)")
        else:
            ratio = plan.est_memory_mb / budget
            plan.risk_class = "LOW" if ratio < 0.5 else ("MEDIUM" if ratio < 0.8 else ("HIGH" if ratio < 1.0 else "BLOCKED"))
            if ratio >= 1.0:
                plan.blockers.append(f"tahmini bellek {plan.est_memory_mb:.0f} MB > bütçe {budget:.0f} MB "
                                     f"(host {host_reserve_mb:.0f} + worker {worker_reserve_mb:.0f} rezerv) — stride artırın ya da sembol azaltın")
            elif ratio >= 0.8:
                plan.warnings.append(f"bellek bütçesinin %{ratio * 100:.0f}'i — worker ile aynı anda riskli")
    if plan.blockers:
        plan.risk_class = "BLOCKED"
    return plan


# --------------------------------------------------------------------------- eğitim
def _artifact_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _replay_learner(cfg: Any, replay_dir: Path):
    from ..learn import LearnConfig, LearnerV2, ModelRegistry, TradeMemory
    mem = TradeMemory(replay_dir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    reg = ModelRegistry(replay_dir / "models.json")
    lc = cfg.v3.learning_v3
    learner = LearnerV2(mem, reg, LearnConfig(min_samples_train=lc.min_samples_train, holdout_frac=lc.holdout_frac,
                                              half_life_days=lc.half_life_days, calibrator=lc.calibrator),
                        state_path=replay_dir / "learn_v2.json")
    return mem, reg, learner


def train_replay_challenger(cfg: Any, replay_dir: Path, *, seed: int = 0, force: bool = False) -> dict:
    """Replay hafızasından challenger eğitir (YALNIZ replay state'i). İdempotent: aynı girdi hash'i → yeniden eğitim yok.
    Canlı model/registry/ledger dosyalarına dokunulmaz; hiçbir terfi yapılmaz."""
    replay_dir = Path(replay_dir)
    if not replay_dir.is_dir():
        raise ReplaySafetyError(f"replay dizini yok: {replay_dir}")
    mem, reg, learner = _replay_learner(cfg, replay_dir)
    rows = mem.trades(closed_only=True)
    n = len(rows)
    min_n = cfg.v3.learning_v3.min_samples_train
    stamps = [str(r.get("recorded_at") or "") for r in rows]
    inputs = {"n_closed": n, "first": stamps[0] if stamps else "", "last": stamps[-1] if stamps else "",
              "seed": int(seed), "min_samples_train": min_n, "holdout_frac": cfg.v3.learning_v3.holdout_frac,
              "half_life_days": cfg.v3.learning_v3.half_life_days, "calibrator": cfg.v3.learning_v3.calibrator,
              "version": RESEARCH_VERSION,
              "memory_sha256": hashlib.sha256((replay_dir / "trade_memory.jsonl").read_bytes()).hexdigest()
              if (replay_dir / "trade_memory.jsonl").exists() else ""}
    input_hash = _artifact_hash(inputs)
    prev = read_json(replay_dir / TRAIN_MANIFEST, default=None)
    if isinstance(prev, dict) and prev.get("input_hash") == input_hash and not force:
        return prev | {"idempotent_skip": True}
    if n < min_n:
        raise ReplaySafetyError(f"yetersiz kapanmış replay örneği: {n} < {min_n}")
    # DETERMİNİZM: recency ağırlıkları duvar saatine bağlı olmasın → referans an = son kaydın zamanı.
    # (Canlı LearnerV2 davranışı değişmez; bu yalnız araştırma hattının çağrı sözleşmesidir.)
    try:
        ref_now = from_iso(inputs["last"]) if inputs["last"] else None
    except (ValueError, TypeError):
        ref_now = None
    if ref_now is None:
        raise ReplaySafetyError("hafızada geçerli `recorded_at` yok — deterministik eğitim yapılamaz (fail-closed)")
    out = learner.train_challenger(now=ref_now)
    if not out:
        raise ReplaySafetyError("challenger eğitilemedi (LearnerV2 None döndürdü)")
    model = reg.get(out["model_id"]) or {}
    params = model.get("params") or {}
    manifest = {
        "version": RESEARCH_VERSION, "created_at": iso(utc_now()), "run_dir": str(replay_dir), "seed": int(seed),
        "source": "HISTORICAL_REPLAY", "input_hash": input_hash, "inputs": inputs,
        "model_id": out["model_id"], "n_train": out["n_train"], "n_holdout": out["n_holdout"],
        "data_range": {"first_recorded_at": inputs["first"], "last_recorded_at": inputs["last"]},
        "reference_now": iso(ref_now),        # recency ağırlıklarının referansı (duvar saati DEĞİL)
        "metrics": out["metrics"],
        # model kimliği/created_at zaman-bağımlı olduğundan determinism hash yalnız AĞIRLIK+KALİBRASYON üzerinden
        "params_hash": _artifact_hash({k: v for k, v in params.items() if k != "created_at"}),
        "metrics_hash": _artifact_hash(out["metrics"]),
        "promotion": {"live_promotion": False, "note": "replay registry'sinde CANDIDATE; canlı champion'a kopyalanmaz"},
        "idempotent_skip": False,
    }
    atomic_write_json(replay_dir / TRAIN_MANIFEST, manifest, indent=1)
    return manifest


# --------------------------------------------------------------------------- değerlendirme
def _r_metrics(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0, "expectancy_r": None, "win_rate": None, "max_dd_r": None, "profit_factor": None}
    eq = peak = dd = 0.0
    for v in rs:
        eq += v
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    wins = sum(v for v in rs if v > 0)
    losses = abs(sum(v for v in rs if v < 0))
    return {"n": len(rs), "expectancy_r": round(sum(rs) / len(rs), 4), "win_rate": round(sum(1 for v in rs if v > 0) / len(rs), 4),
            "max_dd_r": round(dd, 4), "profit_factor": round(wins / losses, 4) if losses > 0 else None}


def evaluate_replay(cfg: Any, replay_dir: Path, *, min_samples: int | None = None) -> dict:
    """Objektif OOS değerlendirmesi. Walk-forward/purge/embargo sözleşmesi + sızıntı kontrolü.
    Yalnız RAPOR üretir: canlı modele kopyalama/terfi YOK. Sorun varsa ReplaySafetyError (çağıran non-zero döner)."""
    replay_dir = Path(replay_dir)
    if not replay_dir.is_dir():
        raise ReplaySafetyError(f"replay dizini yok: {replay_dir}")
    manifest = read_json(replay_dir / TRAIN_MANIFEST, default=None)
    if not isinstance(manifest, dict) or not manifest.get("model_id"):
        raise ReplaySafetyError("eğitim manifesti yok/bozuk — önce `replay-train` çalıştırın")
    mem, reg, _ = _replay_learner(cfg, replay_dir)
    model = reg.get(str(manifest["model_id"]))
    if not model:
        raise ReplaySafetyError(f"model artifact bulunamadı: {manifest['model_id']}")
    if str(model.get("status", "")).upper() == "CHAMPION":
        raise ReplaySafetyError("replay modeli CHAMPION işaretli — araştırma hattı terfi üretmemeli (fail-closed)")
    rows = mem.trades(closed_only=True)
    n = len(rows)
    need = int(min_samples if min_samples is not None else cfg.v3.learning_v3.min_samples_train)
    if n < need:
        raise ReplaySafetyError(f"yetersiz örnek: {n} < {need}")
    if n != int(manifest.get("inputs", {}).get("n_closed", -1)):
        raise ReplaySafetyError("artifact bayat: hafıza eğitimden sonra değişmiş — `replay-train` tekrar çalıştırın")
    stamps = [str(r.get("recorded_at") or "") for r in rows]
    if any(stamps[i] > stamps[i + 1] for i in range(len(stamps) - 1)):
        raise ReplaySafetyError("hafıza zaman sırasında değil — walk-forward sözleşmesi ihlali")
    n_train = int(manifest.get("n_train", 0))
    n_hold = int(manifest.get("n_holdout", 0))
    if n_train <= 0 or n_hold <= 0 or n_train + n_hold != n:
        raise ReplaySafetyError(f"train/holdout bölünmesi tutarsız: {n_train}+{n_hold} != {n}")
    if stamps[n_train - 1] > stamps[n_train]:
        raise ReplaySafetyError("train/test sızıntısı: holdout örneği train penceresinden önce kaydedilmiş")
    r_all = [float((r.get("outcome") or {}).get("r_multiple", 0) or 0) for r in rows]
    oos = _r_metrics(r_all[n_train:])
    ins = _r_metrics(r_all[:n_train])
    met = dict(manifest.get("metrics") or {})
    calib = {k: met.get(k) for k in ("brier", "log_loss", "ece", "n_holdout", "hit_rate", "expectancy_r") if k in met}
    replay_res = read_json(replay_dir / "replay_result.json", default=None)
    wf = (replay_res or {}).get("windows") or []
    lower_ci = None
    if oos["n"] >= 2:                      # beklentinin normal-yaklaşık %95 alt sınırı (kenar iddiası için)
        import statistics
        sd = statistics.pstdev(r_all[n_train:]) or 0.0
        lower_ci = round(oos["expectancy_r"] - 1.96 * sd / (oos["n"] ** 0.5), 4)
    edge_claim = bool(lower_ci is not None and lower_ci > 0 and oos["n"] >= need)
    report = {
        "version": RESEARCH_VERSION, "generated_at": iso(utc_now()), "run_dir": str(replay_dir),
        "source": "HISTORICAL_REPLAY", "model_id": manifest["model_id"], "model_status": model.get("status"),
        "samples": {"closed": n, "train": n_train, "holdout": n_hold},
        "out_of_sample": oos, "in_sample": ins, "calibration": calib,
        "oos_expectancy_lower_ci95": lower_ci,
        "data_range": manifest.get("data_range"),
        "walk_forward": {"windows": len(wf), "purge_embargo_enforced": bool(wf),
                         "note": "pencereler replay_result.json'dan; anchored-forward, shuffle yok"},
        "determinism": {"params_hash": manifest.get("params_hash"), "metrics_hash": manifest.get("metrics_hash"),
                        "replay_hash": (replay_res or {}).get("determinism_hash")},
        "survivorship_bias": {"present": True, "note": "bugün listeli evren; delisted semboller kapsam dışı — kenar tahmini yukarı yanlı olabilir"},
        "shadow_candidate": edge_claim,
        "verdict": ("SHADOW ADAYI OLABİLİR" if edge_claim else "KENAR İDDİASI YOK (CI alt sınırı ≤ 0 ya da örnek az)"),
        "promotion": {"live_promotion": False, "note": "bu rapor canlı modele kopyalama/terfi İÇERMEZ"},
    }
    atomic_write_json(replay_dir / EVAL_REPORT, report, indent=1)
    return report


__all__ = ["ReplayPlan", "ReplaySafetyError", "assert_live_state_untouched", "evaluate_replay", "plan_replay",
           "resolve_replay_dir", "train_replay_challenger", "EVAL_REPORT", "TRAIN_MANIFEST", "RESEARCH_VERSION"]
