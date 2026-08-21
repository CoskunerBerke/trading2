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

# --- CPU modeli (ÖLÇÜM tabanlı; provenance run_status/plan çıktısında taşınır) ---------------------
# Core-4 pilotu (VPS, 2026-08: 4 sembol · futures 4h · stride 4 · 2022-01→2026-08):
#   10.032 karar → 12 sa 50 dk CPU = 46.200 s  →  4.606 CPU-s / karar (CPUQuota=%60 ile ölçüldü).
# Eski model 55 karar/s (0.018 s/karar) varsayıyordu → ~250× hatalıydı. Aralık, tek nokta yerine
# ±%40 ile verilir (rejim/sembol sayısı/pattern yoğunluğu kadansı değiştirir).
CPU_SECONDS_PER_DECISION = 4.6
CPU_ESTIMATE_LOW_MULT = 0.6
CPU_ESTIMATE_HIGH_MULT = 1.4
CPU_MODEL_PROVENANCE = ("ölçüm: Core-4 pilot (run core4_4h_s4_seed7) 10.032 karar / 46.200 s CPU "
                        "= 4.6 CPU-s/karar; aralık ±%40")
LONG_RUN_WARN_HOURS = 6.0            # bu sürenin üstü MEDIUM, 24 sa üstü HIGH süre riski
LONG_RUN_HIGH_HOURS = 24.0
_LIVE_STATE_FILES = ("futures_ledger.json", "spot_ledger.json", "trade_memory.jsonl", "learn_v2.json",
                     "models.json", "risk.json", "portfolio.json", "mode.json")


class ReplaySafetyError(ValueError):
    """İzolasyon ya da kapasite sözleşmesi ihlali (fail-closed)."""


def _assert_no_symlink_component(path: Path, live: Path) -> None:
    """`path` ve canlı state köküne kadar olan bütün üst bileşenleri symlink olmamalı."""
    cur = path.absolute()
    seen = 0
    while True:
        if cur.is_symlink():
            raise ReplaySafetyError(f"replay kökü/üst dizini symlink olamaz: {cur}")
        if cur == cur.parent or cur.resolve() == live or seen > 64:
            return
        cur = cur.parent
        seen += 1


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
    raw_root = Path(state_root) if state_root else (Path(state_path) / "replay")
    # Kök ve bütün üst bileşenleri symlink OLAMAZ: `resolve()` symlink'i takip edeceği için
    # kaçış ancak takip ETMEDEN kontrol edilerek yakalanır (fail-closed).
    _assert_no_symlink_component(raw_root, live)
    root = raw_root.resolve()
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


def assert_replay_dir_still_safe(state_path: Path | str, run_id: str, state_root: Path | str | None = None) -> Path:
    """mkdir/işlem SONRASI tekrar doğrulama — dizin araya symlink ile değiştirilmediyse aynı yolu döndürür."""
    return resolve_replay_dir(state_path, run_id, state_root, must_exist=True)


def assert_live_state_untouched(state_path: Path | str) -> dict[str, str]:
    """Canlı state dosyalarının sha256 haritası — testler ve runner öncesi/sonrası karşılaştırma için."""
    live = Path(state_path)
    out: dict[str, str] = {}
    for name in _LIVE_STATE_FILES:
        p = live / name
        if p.exists():
            out[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# --------------------------------------------------------------------------- semantik canlı doğrulama
def semantic_live_snapshot(state_path: Path | str) -> dict:
    """Canlı PAPER durumunun SEMANTİK özeti (byte hash değil): worker çalışırken MTM/heartbeat alanları
    doğal olarak değişir; korunması gereken değişmezler bunlardır.
    * mod PAPER + live_order_path_enabled=false
    * gerçek emir sayısı (PAPER'da 0)
    * açık pozisyonlar: id/symbol/side/entry/qty/stop/targets
    * fill kimlikleri (duplicate tespiti için)
    """
    st = Path(state_path)
    led = read_json(st / "futures_ledger.json", default=None) or {}
    mode_d = read_json(st / "mode.json", default=None) or {}
    positions, fills = {}, []
    for sym, p in (led.get("positions") or {}).items():
        if not isinstance(p, dict):
            continue
        positions[sym] = {"id": p.get("id"), "side": p.get("side"), "entry_avg": str(p.get("entry_avg")),
                          "qty": str(p.get("qty")), "stop": str(p.get("stop")),
                          "targets": [str(t) for t in (p.get("targets") or [])]}
        fills += [f.get("id") for f in (p.get("fills") or []) if isinstance(f, dict)]
    for h in (led.get("history") or []):
        if isinstance(h, dict):
            fills += [f.get("id") for f in (h.get("fills") or []) if isinstance(f, dict)]
    return {"mode": str(mode_d.get("mode") or "").upper() or None,
            "live_order_path_enabled": bool(mode_d.get("live_order_path_enabled", False)),
            "real_orders": int(led.get("real_orders", 0) or 0),
            "positions": positions, "closed_ids": [h.get("id") for h in (led.get("history") or []) if isinstance(h, dict)],
            "fill_ids": sorted(x for x in fills if x),
            "duplicate_fills": len(fills) != len(set(fills)),
            "duplicate_position_ids": len({v["id"] for v in positions.values()}) != len(positions)}


def compare_semantic(before: dict, after: dict, *, allow_new_closures: bool = True) -> dict:
    """Pilot öncesi/sonrası semantik karşılaştırma. Açık pozisyonların kimlik/plan alanları DEĞİŞMEMELİ.
    `allow_new_closures=True` iken worker'ın doğal PAPER kapanışları ihlal sayılmaz (pozisyon history'e geçer)."""
    diffs: list[str] = []
    if after.get("mode") != "PAPER":
        diffs.append(f"mod PAPER değil: {after.get('mode')}")
    if after.get("live_order_path_enabled"):
        diffs.append("live_order_path_enabled=true")
    if int(after.get("real_orders", 0) or 0) != 0:
        diffs.append(f"gerçek emir sayısı 0 değil: {after.get('real_orders')}")
    if after.get("duplicate_fills"):
        diffs.append("duplicate fill kimliği")
    if after.get("duplicate_position_ids"):
        diffs.append("duplicate pozisyon kimliği")
    b_pos, a_pos = before.get("positions") or {}, after.get("positions") or {}
    for sym, bp in b_pos.items():
        ap = a_pos.get(sym)
        if ap is None:
            if not (allow_new_closures and bp.get("id") in set(after.get("closed_ids") or [])):
                diffs.append(f"{sym}: pozisyon kayboldu (kapanış kaydı da yok)")
            continue
        for k in ("id", "side", "entry_avg", "qty", "stop", "targets"):
            if bp.get(k) != ap.get(k):
                diffs.append(f"{sym}.{k}: {bp.get(k)} → {ap.get(k)}")
    for fid in before.get("fill_ids") or []:
        if fid not in set(after.get("fill_ids") or []):
            diffs.append(f"fill kayboldu: {fid}")
    return {"ok": not diffs, "diffs": diffs,
            "checked": ["mode", "live_order_path_enabled", "real_orders", "position_identity", "stop_targets",
                        "fill_ids", "duplicates"]}


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
    pattern_stride: int = 1
    series: list[dict] = field(default_factory=list)
    total_rows: int = 0
    timeline_bars: int = 0
    pattern_events: int = 0
    est_memory_mb: float = 0.0
    est_decisions: int = 0
    est_cpu_minutes: float = 0.0
    est_cpu_minutes_low: float = 0.0
    est_cpu_minutes_high: float = 0.0
    cpu_model: dict = field(default_factory=dict)
    memory_risk: str = "UNKNOWN"
    duration_risk: str = "UNKNOWN"
    available_mb: float | None = None
    budget_mb: float | None = None
    runner_memory_max_mb: float | None = None
    runner_budget_mb: float | None = None
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
                patterns: bool = True, pattern_stride: int | None = None, min_bars: int = 250,
                available_mb: float | None = None, host_reserve_mb: float = DEFAULT_HOST_RESERVE_MB,
                worker_reserve_mb: float = DEFAULT_WORKER_RESERVE_MB,
                runner_memory_max_mb: float | None = None, runner_safe_pct: float = 80.0) -> ReplayPlan:
    """Veri OKUMADAN (yalnız manifest) satır/timeline/olay/bellek/CPU tahmini + kapasite sınıfı. Hiçbir şey yazmaz."""
    stride = max(1, int(stride))
    # PARİTE: `historical-replay --stride` hem karar kadansı hem pattern index stride'ı olarak kullanılır →
    # plan varsayılanı da aynı stride'dır; bilinçli override için pattern_stride açıkça verilebilir.
    pattern_stride = stride if pattern_stride is None else max(1, int(pattern_stride))
    plan = ReplayPlan(run_id=run_id, market=market, tf=tf, symbols=list(symbols), stride=stride, seed=int(seed))
    plan.pattern_stride = pattern_stride
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
    # CPU: Core-4 ölçümünden kalibre (4.6 CPU-s/karar); karar sayısı = timeline barı × sembol
    decisions = plan.timeline_bars * max(1, len(plan.series))
    plan.est_decisions = decisions
    cpu_s = decisions * CPU_SECONDS_PER_DECISION
    plan.est_cpu_minutes = round(cpu_s / 60.0, 1)
    plan.est_cpu_minutes_low = round(cpu_s * CPU_ESTIMATE_LOW_MULT / 60.0, 1)
    plan.est_cpu_minutes_high = round(cpu_s * CPU_ESTIMATE_HIGH_MULT / 60.0, 1)
    plan.cpu_model = {"seconds_per_decision": CPU_SECONDS_PER_DECISION, "provenance": CPU_MODEL_PROVENANCE,
                      "range_mult": [CPU_ESTIMATE_LOW_MULT, CPU_ESTIMATE_HIGH_MULT]}
    hours_high = plan.est_cpu_minutes_high / 60.0
    plan.duration_risk = ("LOW" if hours_high < LONG_RUN_WARN_HOURS
                          else ("MEDIUM" if hours_high < LONG_RUN_HIGH_HOURS else "HIGH"))
    if plan.duration_risk != "LOW":
        plan.warnings.append(f"tahmini süre {plan.est_cpu_minutes_low / 60:.1f}–{hours_high:.1f} CPU-saat "
                             f"({decisions:,} karar) — uzun koşu; transient service ve `status` ile izleyin")
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
            plan.memory_risk = "LOW" if ratio < 0.5 else ("MEDIUM" if ratio < 0.8 else ("HIGH" if ratio < 1.0 else "BLOCKED"))
            if ratio >= 1.0:
                plan.blockers.append(f"tahmini bellek {plan.est_memory_mb:.0f} MB > bütçe {budget:.0f} MB "
                                     f"(host {host_reserve_mb:.0f} + worker {worker_reserve_mb:.0f} rezerv) — stride artırın ya da sembol azaltın")
            elif ratio >= 0.8:
                plan.warnings.append(f"bellek bütçesinin %{ratio * 100:.0f}'i — worker ile aynı anda riskli")
    # Runner cgroup sınırıyla uyum: tahmini bellek, MemoryMax'ın güvenli yüzdesini aşmamalı (fail-closed)
    if runner_memory_max_mb:
        plan.runner_memory_max_mb = round(float(runner_memory_max_mb), 1)
        cap = float(runner_memory_max_mb) * max(1.0, float(runner_safe_pct)) / 100.0
        plan.runner_budget_mb = round(cap, 1)
        if plan.est_memory_mb > cap:
            plan.blockers.append(f"tahmini bellek {plan.est_memory_mb:.0f} MB > runner sınırı %{runner_safe_pct:.0f} "
                                 f"× {runner_memory_max_mb:.0f} MB = {cap:.0f} MB — stride artırın, sembol azaltın ya da REPLAY_MEM_MAX yükseltin")
    # Nihai sınıf: bellek VE süre riskinin en kötüsü (Core-4 gibi 12 sa'lik iş artık "LOW" görünmez)
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "BLOCKED": 3, "UNKNOWN": 3}
    plan.risk_class = max((plan.memory_risk, plan.duration_risk), key=lambda x: order.get(x, 3))
    if plan.blockers:
        plan.risk_class = "BLOCKED"
    return plan


# --------------------------------------------------------------------------- eğitim
_VOLATILE_PARAM_KEYS = ("trained_at", "created_at", "fitted_at", "updated_at")


def _stable_params(params: dict) -> dict:
    """Zaman damgalarını (duvar saati) ayıklanmış model parametreleri — determinizm karşılaştırması için."""
    out = {k: v for k, v in (params or {}).items() if k not in _VOLATILE_PARAM_KEYS}
    cal = out.get("calibrator")
    if isinstance(cal, dict):
        out["calibrator"] = {k: v for k, v in cal.items() if k not in _VOLATILE_PARAM_KEYS}
    return out


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


def resolve_run_seed(replay_dir: Path, seed: int | None = None) -> tuple[int, str]:
    """SEED SÖZLEŞMESİ: kaynak `replay_result.json`'daki seed'dir. Kullanıcı seed verirse birebir eşleşmeli;
    uyuşmazlık fail-closed. Sessiz 0 fallback YASAK — replay sonucu yoksa açık seed zorunludur."""
    res = read_json(Path(replay_dir) / "replay_result.json", default=None)
    run_seed = None
    if isinstance(res, dict) and res.get("seed") is not None:
        try:
            run_seed = int(res["seed"])
        except (TypeError, ValueError):
            raise ReplaySafetyError("replay_result.json içindeki seed sayısal değil (fail-closed)") from None
    if run_seed is None:
        if seed is None:
            raise ReplaySafetyError("seed belirlenemedi: replay_result.json yok/seed'siz ve --seed verilmedi "
                                    "(sessiz 0 fallback yasak)")
        return int(seed), "explicit"
    if seed is not None and int(seed) != run_seed:
        raise ReplaySafetyError(f"seed uyuşmazlığı: --seed {seed} ≠ replay_result seed {run_seed} (fail-closed)")
    return run_seed, "replay_result"


def train_replay_challenger(cfg: Any, replay_dir: Path, *, seed: int | None = None, force: bool = False) -> dict:
    """Replay hafızasından challenger eğitir (YALNIZ replay state'i). İdempotent: aynı girdi hash'i → yeniden eğitim yok.
    Canlı model/registry/ledger dosyalarına dokunulmaz; hiçbir terfi yapılmaz.
    Seed `replay_result.json`'dan gelir (bkz. `resolve_run_seed`)."""
    replay_dir = Path(replay_dir)
    if not replay_dir.is_dir():
        raise ReplaySafetyError(f"replay dizini yok: {replay_dir}")
    seed, seed_source = resolve_run_seed(replay_dir, seed)
    replay_res_pre = read_json(replay_dir / "replay_result.json", default=None) or {}
    mem, reg, learner = _replay_learner(cfg, replay_dir)
    rows = mem.trades(closed_only=True)
    n = len(rows)
    min_n = cfg.v3.learning_v3.min_samples_train
    stamps = [str(r.get("recorded_at") or "") for r in rows]
    inputs = {"n_closed": n, "first": stamps[0] if stamps else "", "last": stamps[-1] if stamps else "",
              "seed": int(seed), "replay_determinism_hash": str(replay_res_pre.get("determinism_hash") or ""),
              "min_samples_train": min_n, "holdout_frac": cfg.v3.learning_v3.holdout_frac,
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
        "seed_source": seed_source, "replay_determinism_hash": str(replay_res_pre.get("determinism_hash") or ""),
        "model_id": out["model_id"], "n_train": out["n_train"], "n_holdout": out["n_holdout"],
        "data_range": {"first_recorded_at": inputs["first"], "last_recorded_at": inputs["last"]},
        "reference_now": iso(ref_now),        # recency ağırlıklarının referansı (duvar saati DEĞİL)
        "metrics": out["metrics"],
        # Determinism hash yalnız AĞIRLIK+KALİBRASYON üzerinden: model id ve zaman damgaları
        # (`trained_at`, `created_at`) duvar saatine bağlıdır, karşılaştırmaya girmez.
        "params_hash": _artifact_hash(_stable_params(params)),
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



def _ms_or_none(v):
    if not v:
        return None
    try:
        return int(from_iso(str(v)).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def iso_ms(ms: int) -> str:
    import datetime as _d
    return iso(_d.datetime.fromtimestamp(int(ms) / 1000, tz=_d.timezone.utc))


def _fold_rows(rows: list[dict], b: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """(train, test, excluded) — sızıntı kuralları:
    * train: giriş >= train_start VE ÇIKIŞ (etiketin bilindiği an) < train_end
    * excluded: etiketi purge/embargo bölgesinde biten ya da pencerelere düşmeyen kayıtlar
    * test: giriş [test_start, test_end) — fold test pencereleri örtüşmez, işlem tek fold'da sayılır
    """
    tr, te, ex = [], [], []
    for r in rows:
        o, c = r.get("_open_ms"), r.get("_close_ms")
        if o is None or c is None:
            ex.append(r)
        elif b["test_start_ms"] <= o < b["test_end_ms"]:
            te.append(r)
        elif o >= b["train_start_ms"] and c < b["train_end_ms"]:
            tr.append(r)
        else:
            ex.append(r)
    return tr, te, ex


def _fit_fold(cfg, train_rows: list[dict], test_rows: list[dict], ref_ms: int):
    """Fold modeli: YALNIZ train satırlarıyla eğitilir; kalibrasyon train'in son diliminden; test'e bakılmaz."""
    import numpy as np

    from ..learn import (Calibrator, LogisticModel, build_features, calibration_metrics, feature_names,
                         recency_weights, to_vector)
    from ..learn.features import FEATURE_VERSION
    lc = cfg.v3.learning_v3
    names = feature_names(FEATURE_VERSION, True)

    def _xy(rs):
        X = np.array([to_vector(build_features(r.get("features") or r, include_time_features=True), names) for r in rs], float)
        y = np.array([1.0 if float((r.get("outcome") or {}).get("r_multiple", 0) or 0) > 0.25 else 0.0 for r in rs])
        return X, y

    Xtr, ytr = _xy(train_rows)
    ages = np.array([max(0.0, (ref_ms - float(r["_close_ms"])) / 86_400_000.0) for r in train_rows])
    w = recency_weights(ages, lc.half_life_days)
    inner = max(1, int(len(train_rows) * (1 - lc.holdout_frac)))
    model = LogisticModel(feature_names=names).fit(Xtr[:inner], ytr[:inner], w[:inner])
    cal = Calibrator(lc.calibrator)
    if len(train_rows) - inner >= 10:
        cal.fit(model.predict_proba(Xtr[inner:]), ytr[inner:])
    Xte, yte = _xy(test_rows)
    p = model.predict_proba(Xte)
    p = cal.apply(p) if cal.n_fit else p
    met = calibration_metrics(p, yte)
    met.pop("reliability", None)
    return p, yte, met


def coverage_report(rows: list[dict], replay_res: dict | None) -> dict:
    """Sembol/side kırılımı + red nedenleri. BTC gibi STEP_ZERO_QTY yüzünden işlem üretemeyen semboller
    genel ortalamaya sessizce karışmaz; raporda açıkça görünür."""
    res = replay_res or {}
    by_symbol: dict[str, dict] = {}
    for r in rows:
        out = r.get("outcome") or {}
        sym = str(out.get("symbol") or r.get("symbol") or "?")
        d = by_symbol.setdefault(sym, {"closed": 0, "r": []})
        d["closed"] += 1
        d["r"].append(float(out.get("r_multiple", 0) or 0))
    rejections = res.get("rejections") or {}
    planned = [str(x) for x in (res.get("symbols") or [])]
    covered = sorted(by_symbol)
    zero = sorted(set(planned) - set(covered))
    warnings: list[str] = []
    for sym in zero:
        why = (rejections.get("by_symbol") or {}).get(sym) or {}
        top = max(why.items(), key=lambda kv: kv[1])[0] if why else "bilinmiyor"
        warnings.append(f"{sym}: hiç kapanmış işlem yok (baskın red nedeni: {top}) — ortalamalara KATILMIYOR")
    for sym, d in by_symbol.items():
        if d["closed"] < 10:
            warnings.append(f"{sym}: yalnız {d['closed']} kapanış — sembol bazlı metrik güvenilmez")
    return {"planned_symbols": planned, "covered_symbols": covered, "zero_trade_symbols": zero,
            "by_symbol": {k: {"closed": v["closed"], **_r_metrics(v["r"])} for k, v in sorted(by_symbol.items())},
            "rejections": {"by_reason": rejections.get("by_reason") or {}, "by_symbol": rejections.get("by_symbol") or {},
                           "total": int(rejections.get("total", 0) or 0)},
            "actionable": res.get("n_actionable"), "opened": res.get("n_opened"), "closed": len(rows),
            "warnings": warnings}


def evaluate_replay(cfg, replay_dir: Path, *, min_samples: int | None = None, min_folds: int = 2,
                    max_ece: float = 0.15, max_brier: float = 0.30) -> dict:
    """GERÇEK anchored walk-forward OOS değerlendirmesi: her fold'da YALNIZ geçmiş train verisiyle challenger
    üretilir; purge/embargo kayıtları hem eğitimden hem OOS metriğinden çıkarılır; her işlem en fazla bir test
    fold'unda sayılır. Yalnız RAPOR üretir; canlı modele kopyalama/terfi YOK. Sorunlarda ReplaySafetyError."""
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
    replay_pre = read_json(replay_dir / "replay_result.json", default=None) or {}
    run_seed, _src = resolve_run_seed(replay_dir, None)
    if int(manifest.get("inputs", {}).get("seed", -1)) != run_seed:
        raise ReplaySafetyError(f"seed uyuşmazlığı: manifest {manifest.get('inputs', {}).get('seed')} ≠ "
                                f"replay_result {run_seed} — `replay-train` tekrar çalıştırın")
    if str(manifest.get("replay_determinism_hash") or "") != str(replay_pre.get("determinism_hash") or ""):
        raise ReplaySafetyError("replay determinism hash değişmiş — eğitim artifact'i bu replay'e ait değil (fail-closed)")
    stamps = [str(r.get("recorded_at") or "") for r in rows]
    if any(stamps[i] > stamps[i + 1] for i in range(len(stamps) - 1)):
        raise ReplaySafetyError("hafıza zaman sırasında değil — walk-forward sözleşmesi ihlali")
    n_train_m, n_hold_m = int(manifest.get("n_train", 0)), int(manifest.get("n_holdout", 0))
    if n_train_m <= 0 or n_hold_m <= 0 or n_train_m + n_hold_m != n:
        raise ReplaySafetyError(f"eğitim manifesti tutarsız: n_train {n_train_m} + n_holdout {n_hold_m} != {n}")
    for r in rows:
        out = r.get("outcome") or {}
        r["_open_ms"] = _ms_or_none(out.get("opened_at") or r.get("recorded_at"))
        r["_close_ms"] = _ms_or_none(out.get("closed_at") or r.get("recorded_at"))
    replay_res = read_json(replay_dir / "replay_result.json", default=None)
    windows = (replay_res or {}).get("windows") or []
    bounds = [w.get("bounds") for w in windows if isinstance(w, dict) and isinstance(w.get("bounds"), dict)]
    if not windows or len(bounds) != len(windows):
        raise ReplaySafetyError("replay_result.json içinde kesin pencere sınırları (bounds) yok — replay'i güncel kodla yeniden koşun (fail-closed)")
    bounds.sort(key=lambda b: b["train_end_ms"])
    for b in bounds:
        if not (b["train_start_ms"] < b["train_end_ms"] <= b["purge_end_ms"] <= b["embargo_end_ms"] <= b["test_start_ms"] < b["test_end_ms"]):
            raise ReplaySafetyError(f"fold {b.get('idx')}: train/purge/embargo/test sınırları geçersiz ya da kesişiyor")
        # TIMEFRAME TUTARLILIĞI: purge/embargo genişliği bar_ms × bar sayısı olmalı (4h varsayımı yok)
        bar = int(b.get("bar_ms") or 0)
        if bar <= 0:
            raise ReplaySafetyError(f"fold {b.get('idx')}: bar_ms yok — timeframe doğrulanamıyor (fail-closed)")
        if (b["purge_end_ms"] - b["purge_start_ms"]) != int(b.get("purge_bars", 0)) * bar or            (b["embargo_end_ms"] - b["embargo_start_ms"]) != int(b.get("embargo_bars", 0)) * bar:
            raise ReplaySafetyError(f"fold {b.get('idx')}: purge/embargo genişliği bar_ms ({bar}) ile tutarsız "
                                    "— replay'i doğru timeframe ile yeniden koşun")
    for a, b in zip(bounds, bounds[1:]):
        if b["test_start_ms"] < a["test_end_ms"]:
            raise ReplaySafetyError(f"fold {a.get('idx')}/{b.get('idx')}: test pencereleri örtüşüyor (çift sayım riski)")
    folds, pooled_p, pooled_y, pooled_r, seen_ids = [], [], [], [], set()
    for b in bounds:
        tr, te, ex = _fold_rows(rows, b)
        if not te:
            continue
        ids = {str(r.get("trade_id")) for r in te}
        dup = ids & seen_ids
        if dup:
            raise ReplaySafetyError(f"aynı işlem birden fazla test fold'unda sayılıyor: {sorted(dup)[:3]}")
        seen_ids |= ids
        common = {"idx": b["idx"], "n_train": len(tr), "n_test": len(te),
                  "train_range": [iso_ms(b["train_start_ms"]), iso_ms(b["train_end_ms"])],
                  "test_range": [iso_ms(b["test_start_ms"]), iso_ms(b["test_end_ms"])],
                  "purge_range": [iso_ms(b["purge_start_ms"]), iso_ms(b["purge_end_ms"])],
                  "embargo_range": [iso_ms(b["embargo_start_ms"]), iso_ms(b["embargo_end_ms"])],
                  "excluded_purge_embargo": len(ex)}
        if len(tr) < max(10, need // 4):
            folds.append(common | {"skipped": "yetersiz train örneği"})
            continue
        p, y, met = _fit_fold(cfg, tr, te, b["train_end_ms"])
        r_test = [float((r.get("outcome") or {}).get("r_multiple", 0) or 0) for r in te]
        pooled_p += [float(v) for v in p]
        pooled_y += [float(v) for v in y]
        pooled_r += r_test
        folds.append(common | _r_metrics(r_test) | {"calibration": met})
    scored = [f for f in folds if "calibration" in f]
    if len(scored) < min_folds:
        raise ReplaySafetyError(f"yeterli walk-forward fold'u yok: {len(scored)} < {min_folds} (aralığı/pencereleri büyütün)")
    agg = _r_metrics(pooled_r)
    from ..learn import calibration_metrics as _cm
    agg_cal = _cm(pooled_p, pooled_y)
    agg_cal.pop("reliability", None)
    lower_ci = None
    if agg["n"] >= 2:
        import statistics
        sd = statistics.pstdev(pooled_r) or 0.0
        lower_ci = round(agg["expectancy_r"] - 1.96 * sd / (agg["n"] ** 0.5), 4)
    pos = sum(1 for f in scored if (f.get("expectancy_r") or 0) > 0)
    consistency = round(pos / len(scored), 4)
    cov = coverage_report(rows, replay_res)
    oos_by_side: dict[str, dict] = {}
    oos_by_symbol: dict[str, dict] = {}
    for b in bounds:
        _tr, te, _ex = _fold_rows(rows, b)
        for r in te:
            out = r.get("outcome") or {}
            rr = float(out.get("r_multiple", 0) or 0)
            oos_by_side.setdefault(str(out.get("side") or "?"), {"r": []})["r"].append(rr)
            oos_by_symbol.setdefault(str(out.get("symbol") or "?"), {"r": []})["r"].append(rr)
    oos_by_side = {k: _r_metrics(v["r"]) for k, v in sorted(oos_by_side.items())}
    oos_by_symbol = {k: _r_metrics(v["r"]) for k, v in sorted(oos_by_symbol.items())}
    pf = agg.get("profit_factor")
    max_dd_limit = float(max(10.0, abs(agg["n"]) * 0.5))       # OOS örnek başına 0.5R'den fazla DD kabul edilmez
    gates = {"enough_oos": bool(agg["n"] >= need),
             "positive_expectancy": bool(agg["expectancy_r"] is not None and agg["expectancy_r"] > 0),
             "profit_factor_above_one": bool(pf is not None and pf > 1.0),
             "ci95_lower_above_zero": bool(lower_ci is not None and lower_ci > 0),
             "calibration_ok": bool(agg_cal.get("ece", 1.0) <= max_ece and agg_cal.get("brier", 1.0) <= max_brier),
             "drawdown_ok": bool((agg.get("max_dd_r") or 0.0) <= max_dd_limit),
             "fold_consistency": bool(consistency >= 0.6), "enough_folds": bool(len(scored) >= min_folds),
             "symbol_coverage": bool(not cov["zero_trade_symbols"] and len(cov["covered_symbols"]) >= 2),
             "side_coverage": bool(len([k for k, v in oos_by_side.items() if v["n"] >= 5]) >= 2),
             # Kenar iddiası için point-in-time evren şart; survivorship bias varken TERFİ ADAYI olunamaz.
             "point_in_time": bool((replay_res or {}).get("point_in_time") is True),
             "survivorship_clean": bool((replay_res or {}).get("survivorship_bias", {}).get("present") is False)}
    shadow = all(gates.values())
    failed = sorted(k for k, v in gates.items() if not v)
    report = {
        "version": RESEARCH_VERSION, "generated_at": iso(utc_now()), "run_dir": str(replay_dir),
        "source": "HISTORICAL_REPLAY", "model_id": manifest["model_id"], "model_status": model.get("status"),
        "samples": {"closed": n, "train": int(manifest.get("n_train", 0)), "holdout": int(manifest.get("n_holdout", 0)),
                    "oos_pooled": agg["n"],
                    "excluded_purge_embargo": sum(f.get("excluded_purge_embargo", 0) for f in scored)},
        "walk_forward": {"folds": len(folds), "scored_folds": len(scored), "purge_embargo_enforced": True,
                         "fold_consistency": consistency,
                         "note": "anchored-forward; purge+embargo kayıtları eğitim ve OOS dışında; test pencereleri örtüşmez"},
        "folds": folds,
        "out_of_sample": agg, "oos_expectancy_lower_ci95": lower_ci, "calibration": agg_cal, "gates": gates,
        "failed_gates": failed, "oos_by_side": oos_by_side, "oos_by_symbol": oos_by_symbol, "coverage": cov,
        "data_range": manifest.get("data_range"),
        "determinism": {"params_hash": manifest.get("params_hash"), "metrics_hash": manifest.get("metrics_hash"),
                        "replay_hash": (replay_res or {}).get("determinism_hash")},
        "survivorship_bias": {"present": True, "note": "bugün listeli evren; delisted semboller kapsam dışı — kenar tahmini yukarı yanlı olabilir"},
        "shadow_candidate": shadow,
        # Kapılardan biri bile geçmezse SHADOW_CANDIDATE ÜRETİLMEZ: en fazla RESEARCH_ONLY/REJECTED.
        # (Negatif expectancy / PF ≤ 1 / CI alt sınırı ≤ 0 / aşırı DD → REJECTED.)
        "verdict": ("SHADOW_CANDIDATE" if shadow else
                    ("REJECTED" if not (gates["positive_expectancy"] and gates["profit_factor_above_one"]
                                        and gates["ci95_lower_above_zero"] and gates["drawdown_ok"])
                     else "RESEARCH_ONLY")),
        "verdict_reason": ("bütün kapılar geçti" if shadow else f"geçmeyen kapılar: {', '.join(failed)}"),
        "promotion": {"live_promotion": False, "promote_called": False,
                      "note": "bu rapor canlı modele kopyalama/terfi İÇERMEZ; maybe_promote hiçbir araştırma yolunda çağrılmaz"},
    }
    atomic_write_json(replay_dir / EVAL_REPORT, report, indent=1)
    return report


__all__ = ["ReplayPlan", "ReplaySafetyError", "compare_semantic", "coverage_report", "iso_ms",
           "resolve_run_seed", "semantic_live_snapshot", "assert_replay_dir_still_safe", "iso_ms", "assert_live_state_untouched", "evaluate_replay", "plan_replay",
           "resolve_replay_dir", "train_replay_challenger", "EVAL_REPORT", "TRAIN_MANIFEST", "RESEARCH_VERSION"]
