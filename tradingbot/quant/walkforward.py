"""Walk-forward fold üretimi ve leakage korumaları (`quant_walkforward_v1`).

Mevcut `replay.engine.walk_forward_windows` (anchored, purge+embargo, fail-closed bar süresi)
YENİDEN KULLANILIR; bu modül üstüne şunları ekler:
* `rolling` mod (train penceresi sabit uzunlukta kayar),
* KİLİTLİ final holdout: serinin sonundan ayrılır, hiçbir fold'a girmez ve `locked=True` ile
  işaretlenir — tuning döngüsüne geri verilmez,
* kronoloji/disjointlik doğrulayıcıları (fail-closed),
* satır→fold ataması yalnız zaman ile (`t` anındaki karar yalnız `<=t` verisi görür),
* fold raporu: parametre kaydı, kararlılık, maliyet-sonrası OOS özetleri; PBO için yeterli
  aday/fold yoksa sahte sayı yerine `null` + `pbo_state`.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable

from ..replay.engine import WFWindow, walk_forward_windows

SCHEMA_VERSION = "quant_walkforward_v1"
DAY_MS = 86_400_000


def make_folds(start_ms: int, end_ms: int, *, mode: str = "anchored", train_days: int,
               test_days: int, purge_bars: int = 6, embargo_bars: int = 6,
               tf: str | None = None, bar_ms: int | None = None,
               holdout_days: int = 0) -> dict[str, Any]:
    """Fold + opsiyonel kilitli holdout üretir. `mode`: "anchored" | "rolling"."""
    if mode not in ("anchored", "rolling"):
        raise ValueError(f"walkforward mode geçersiz: {mode}")
    if holdout_days < 0:
        raise ValueError("holdout_days negatif olamaz")
    holdout = None
    fold_end = end_ms
    if holdout_days:
        h_start = end_ms - holdout_days * DAY_MS
        if h_start <= start_ms:
            raise ValueError("holdout tüm seriyi yutuyor — start/end/holdout_days tutarsız")
        holdout = {"start_ms": h_start, "end_ms": end_ms, "locked": True,
                   "note": "final holdout — tuning döngüsüne geri verilmez"}
        fold_end = h_start
    windows = walk_forward_windows(start_ms, fold_end, train_days=train_days, test_days=test_days,
                                   purge_bars=purge_bars, embargo_bars=embargo_bars,
                                   tf=tf, bar_ms=bar_ms)
    if mode == "rolling":
        windows = [WFWindow(max(start_ms, w.train_end - train_days * DAY_MS), w.train_end,
                            w.test_start, w.test_end, w.idx, purge_bars=w.purge_bars,
                            embargo_bars=w.embargo_bars, bar_ms=w.bar_ms) for w in windows]
    out = {"schema_version": SCHEMA_VERSION, "mode": mode,
           "params": {"train_days": train_days, "test_days": test_days, "purge_bars": purge_bars,
                      "embargo_bars": embargo_bars, "tf": tf, "bar_ms": bar_ms,
                      "holdout_days": holdout_days},
           "folds": windows, "holdout": holdout}
    validate_folds(out)
    return out


def validate_folds(plan: dict[str, Any]) -> None:
    """Fail-closed doğrulama: kronoloji, purge/embargo boşluğu, test disjointliği, holdout izolasyonu."""
    folds: list[WFWindow] = plan["folds"]
    prev_test_end = None
    for w in folds:
        b = w.bounds()
        if not (b["train_start_ms"] < b["train_end_ms"] <= b["embargo_end_ms"] <= b["test_start_ms"] < b["test_end_ms"]):
            raise ValueError(f"fold {w.idx}: kronoloji bozuk: {b}")
        if b["test_start_ms"] - b["train_end_ms"] < (w.purge_bars + w.embargo_bars) * w.bar_ms:
            raise ValueError(f"fold {w.idx}: purge+embargo boşluğu eksik")
        if prev_test_end is not None and b["test_start_ms"] < prev_test_end:
            raise ValueError(f"fold {w.idx}: test pencereleri örtüşüyor")
        prev_test_end = b["test_end_ms"]
    hold = plan.get("holdout")
    if hold:
        for w in folds:
            if w.test_end > hold["start_ms"] or w.train_end > hold["start_ms"]:
                raise ValueError(f"fold {w.idx}: kilitli holdout'a taşıyor")


def assign_rows(rows: Iterable[dict[str, Any]], plan: dict[str, Any], *,
                ts_key: str = "ts_ms") -> dict[str, Any]:
    """Satırları YALNIZ zaman damgasıyla train/test/holdout'a atar.

    Purge/embargo bölgesindeki satırlar HİÇBİR kümeye girmez (`purged`). Zamanı olmayan satır
    kullanılamaz (`unassigned`) — sessizce train'e düşmez. Holdout satırları `holdout` altında
    ayrı döner ve fold döngüsüne verilmez.
    """
    folds: list[WFWindow] = plan["folds"]
    hold = plan.get("holdout")
    out: dict[str, Any] = {"folds": [{"idx": w.idx, "train": [], "test": []} for w in folds],
                           "holdout": [], "purged": 0, "unassigned": 0}
    for r in rows:
        ts = r.get(ts_key)
        if not isinstance(ts, (int, float)) or not math.isfinite(float(ts)):
            out["unassigned"] += 1
            continue
        ts = int(ts)
        if hold and hold["start_ms"] <= ts < hold["end_ms"]:
            out["holdout"].append(r)
            continue
        placed = False
        for w, slot in zip(folds, out["folds"]):
            if w.train_start <= ts < w.train_end:
                slot["train"].append(r)
                placed = True
            elif w.test_start <= ts < w.test_end:
                slot["test"].append(r)
                placed = True
            elif w.train_end <= ts < w.test_start:
                placed = True                                # purge/embargo — hiçbir kümeye girmez
                out["purged"] += 1
        if not placed:
            out["unassigned"] += 1
    return out


def leakage_check(assignment: dict[str, Any], plan: dict[str, Any], *,
                  ts_key: str = "ts_ms", as_of_key: str = "as_of_ms") -> dict[str, Any]:
    """Leakage denetimi (fail-closed rapor):
    * her train satırı kendi fold'unun `train_end`'inden önce olmalı,
    * her satırın market-data `as_of` zamanı karar zamanından SONRA olamaz (gelecek verisi yok),
    * holdout satırı hiçbir fold kümesinde görünemez.
    """
    folds: list[WFWindow] = plan["folds"]
    violations: list[str] = []
    hold = plan.get("holdout")
    hold_ids = {id(r) for r in assignment.get("holdout", [])}
    for w, slot in zip(folds, assignment["folds"]):
        for r in slot["train"]:
            if int(r[ts_key]) >= w.train_end:
                violations.append(f"fold {w.idx}: train satırı train_end sonrası ({r.get(ts_key)})")
        for part in ("train", "test"):
            for r in slot[part]:
                if id(r) in hold_ids:
                    violations.append(f"fold {w.idx}: holdout satırı {part} kümesinde")
                ao = r.get(as_of_key)
                if isinstance(ao, (int, float)) and math.isfinite(float(ao)) and int(ao) > int(r[ts_key]):
                    violations.append(f"fold {w.idx}: as_of karar zamanından sonra "
                                      f"({r.get(as_of_key)} > {r.get(ts_key)}) — gelecek verisi")
    if hold:
        for r in assignment.get("holdout", []):
            if not (hold["start_ms"] <= int(r[ts_key]) < hold["end_ms"]):
                violations.append("holdout dışı satır holdout kümesinde")
    return {"passed": not violations, "n_violations": len(violations), "violations": violations[:20]}


def fold_report(assignment: dict[str, Any], plan: dict[str, Any],
                metric_fn: Callable[[list[dict]], dict[str, Any]], *,
                params_tried: int = 1, n_candidates: int = 1,
                min_pbo_folds: int = 4, min_pbo_candidates: int = 2) -> dict[str, Any]:
    """Fold bazında train/test metrikleri + kararlılık + PBO durumu.

    `metric_fn` her kümeden deterministik metrik sözlüğü üretir (ör. attribution `group_metrics`).
    PBO benzeri multiple-testing ölçüsü için yeterli fold/aday yoksa `pbo=null` +
    `pbo_state="not_computable"` raporlanır; sahte sayı üretilmez.
    """
    folds_out = []
    oos_exp: list[float] = []
    for w, slot in zip(plan["folds"], assignment["folds"]):
        fm = {"idx": w.idx, "bounds": w.bounds(),
              "n_train": len(slot["train"]), "n_test": len(slot["test"]),
              "train": metric_fn(slot["train"]), "test": metric_fn(slot["test"]),
              "symbols_test": sorted({str(r.get("symbol")) for r in slot["test"] if r.get("symbol")}),
              "regimes_test": sorted({str(r.get("regime")) for r in slot["test"] if r.get("regime")})}
        folds_out.append(fm)
        e = fm["test"].get("expectancy_r")
        if isinstance(e, (int, float)) and math.isfinite(float(e)):
            oos_exp.append(float(e))
    stability = (sum(1 for e in oos_exp if e > 0) / len(oos_exp)) if oos_exp else None
    pbo: float | None = None
    pbo_state = "not_computable"
    if len(oos_exp) >= min_pbo_folds and n_candidates >= min_pbo_candidates:
        pbo_state = "requires_candidate_matrix"             # tek politika akışında hesaplanamaz
    return {"schema_version": SCHEMA_VERSION, "mode": plan["mode"], "params": plan["params"],
            "params_tried": params_tried, "n_candidates": n_candidates,
            "folds": folds_out, "n_folds": len(folds_out),
            "purged_rows": assignment.get("purged", 0), "unassigned_rows": assignment.get("unassigned", 0),
            "holdout_rows": len(assignment.get("holdout", [])),
            "oos_expectancy_r_by_fold": [round(e, 4) for e in oos_exp],
            "oos_sign_consistency": round(stability, 4) if stability is not None else None,
            "pbo": pbo, "pbo_state": pbo_state,
            "holdout_locked": bool(plan.get("holdout"))}
