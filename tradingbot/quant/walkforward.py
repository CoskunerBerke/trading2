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
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..market.providers import tf_ms as _tf_ms
from ..replay.engine import WFWindow, walk_forward_windows

SCHEMA_VERSION = "quant_walkforward_v1"
DAY_MS = 86_400_000


@dataclass
class ThreeWayWindow:
    """AÇIK train → validation → test penceresi (her sınırda purge+embargo boşluğu).

    Rol ayrımı katıdır ve `run_three_way` tarafından zorlanır:
    * train      → yalnız fitting/kalibrasyon ve aday ÜRETİMİ,
    * validation → yalnız train'de üretilmiş adaylar arasından SEÇİM,
    * test       → seçilen TEK adayın dokunulmamış değerlendirmesi.
    """
    train_start: int
    train_end: int
    val_start: int
    val_end: int
    test_start: int
    test_end: int
    idx: int
    purge_bars: int = 0
    embargo_bars: int = 0
    bar_ms: int = 0

    @property
    def gap_ms(self) -> int:
        return (self.purge_bars + self.embargo_bars) * self.bar_ms

    def bounds(self) -> dict:
        purge_ms = self.purge_bars * self.bar_ms
        return {"idx": self.idx,
                "train_start_ms": self.train_start, "train_end_ms": self.train_end,
                "train_purge_start_ms": self.train_end, "train_purge_end_ms": self.train_end + purge_ms,
                "train_embargo_end_ms": self.train_end + self.gap_ms,
                "val_start_ms": self.val_start, "val_end_ms": self.val_end,
                "val_purge_start_ms": self.val_end, "val_purge_end_ms": self.val_end + purge_ms,
                "val_embargo_end_ms": self.val_end + self.gap_ms,
                "test_start_ms": self.test_start, "test_end_ms": self.test_end,
                "purge_bars": self.purge_bars, "embargo_bars": self.embargo_bars,
                "bar_ms": self.bar_ms}


def _resolve_bar_ms(tf: str | None, bar_ms: int | None) -> int:
    """`replay.engine.walk_forward_windows` ile AYNI fail-closed sözleşme (sabit 4h varsayımı yok)."""
    if bar_ms is not None:
        return int(bar_ms)
    if not tf:
        raise ValueError("make_folds: `tf` ya da `bar_ms` zorunlu (sabit bar süresi varsayımı yok)")
    return _tf_ms(tf)


def _three_way_windows(start_ms: int, end_ms: int, *, mode: str, train_days: int,
                       validation_days: int, test_days: int, purge_bars: int,
                       embargo_bars: int, bar_ms: int) -> list[ThreeWayWindow]:
    gap = (purge_bars + embargo_bars) * bar_ms
    out: list[ThreeWayWindow] = []
    idx = 0
    train_end = start_ms + train_days * DAY_MS
    while True:
        val_start = train_end + gap
        val_end = val_start + validation_days * DAY_MS
        test_start = val_end + gap
        test_end = test_start + test_days * DAY_MS
        if test_end > end_ms:
            break
        train_start = start_ms if mode == "anchored" else max(start_ms, train_end - train_days * DAY_MS)
        out.append(ThreeWayWindow(train_start, train_end, val_start, val_end, test_start, test_end,
                                  idx, purge_bars=purge_bars, embargo_bars=embargo_bars, bar_ms=bar_ms))
        idx += 1
        train_end += test_days * DAY_MS
    return out


def make_folds(start_ms: int, end_ms: int, *, mode: str = "anchored", train_days: int,
               test_days: int, validation_days: int = 0, purge_bars: int = 6, embargo_bars: int = 6,
               tf: str | None = None, bar_ms: int | None = None,
               holdout_days: int = 0) -> dict[str, Any]:
    """Fold + opsiyonel kilitli holdout üretir. `mode`: "anchored" | "rolling".

    `validation_days=0` (varsayılan) → ESKİ iki yollu davranış birebir korunur (`WFWindow`,
    `replay.engine.walk_forward_windows`). `validation_days>0` → açık üç yollu `ThreeWayWindow`
    (train → purge/embargo → validation → purge/embargo → test). Plan `layout` alanıyla hangi
    şemanın kullanıldığını bildirir; çağıranlar `layout` üzerinden ayırt eder.
    """
    if mode not in ("anchored", "rolling"):
        raise ValueError(f"walkforward mode geçersiz: {mode}")
    if holdout_days < 0:
        raise ValueError("holdout_days negatif olamaz")
    if validation_days < 0:
        raise ValueError("validation_days negatif olamaz")
    holdout = None
    fold_end = end_ms
    if holdout_days:
        h_start = end_ms - holdout_days * DAY_MS
        if h_start <= start_ms:
            raise ValueError("holdout tüm seriyi yutuyor — start/end/holdout_days tutarsız")
        holdout = {"start_ms": h_start, "end_ms": end_ms, "locked": True,
                   "note": "final holdout — tuning döngüsüne geri verilmez"}
        fold_end = h_start
    layout = "three_way" if validation_days > 0 else "two_way"
    if layout == "three_way":
        windows: list = _three_way_windows(start_ms, fold_end, mode=mode, train_days=train_days,
                                           validation_days=validation_days, test_days=test_days,
                                           purge_bars=purge_bars, embargo_bars=embargo_bars,
                                           bar_ms=_resolve_bar_ms(tf, bar_ms))
        if not windows:
            # FAIL-CLOSED: yetersiz veriyle sessizce boş fold listesi döndürmek, "OOS sonucu yok"
            # durumunu "sonuç nötr" gibi gösterirdi.
            raise ValueError("üç yollu walk-forward için yetersiz veri aralığı — "
                             "train+validation+test+purge/embargo pencereleri sığmıyor")
    else:
        windows = walk_forward_windows(start_ms, fold_end, train_days=train_days, test_days=test_days,
                                       purge_bars=purge_bars, embargo_bars=embargo_bars,
                                       tf=tf, bar_ms=bar_ms)
        if mode == "rolling":
            windows = [WFWindow(max(start_ms, w.train_end - train_days * DAY_MS), w.train_end,
                                w.test_start, w.test_end, w.idx, purge_bars=w.purge_bars,
                                embargo_bars=w.embargo_bars, bar_ms=w.bar_ms) for w in windows]
    out = {"schema_version": SCHEMA_VERSION, "mode": mode, "layout": layout,
           "params": {"train_days": train_days, "validation_days": validation_days,
                      "test_days": test_days, "purge_bars": purge_bars,
                      "embargo_bars": embargo_bars, "tf": tf, "bar_ms": bar_ms,
                      "holdout_days": holdout_days},
           "folds": windows, "holdout": holdout}
    validate_folds(out)
    return out


def is_three_way(plan: dict[str, Any]) -> bool:
    return plan.get("layout") == "three_way"


def validate_folds(plan: dict[str, Any]) -> None:
    """Fail-closed doğrulama: kronoloji, HER sınırda purge+embargo boşluğu, test disjointliği,
    holdout izolasyonu. Üç yollu şemada train/validation ve validation/test sınırlarının İKİSİ de
    denetlenir."""
    folds = plan["folds"]
    three = is_three_way(plan)
    prev_test_end = None
    for w in folds:
        b = w.bounds()
        gap = (w.purge_bars + w.embargo_bars) * w.bar_ms
        if three:
            order = [b["train_start_ms"], b["train_end_ms"], b["val_start_ms"], b["val_end_ms"],
                     b["test_start_ms"], b["test_end_ms"]]
            if not all(a < c for a, c in zip(order, order[1:])):
                raise ValueError(f"fold {w.idx}: kronoloji bozuk: {b}")
            if b["val_start_ms"] - b["train_end_ms"] < gap:
                raise ValueError(f"fold {w.idx}: train→validation purge+embargo boşluğu eksik")
            if b["test_start_ms"] - b["val_end_ms"] < gap:
                raise ValueError(f"fold {w.idx}: validation→test purge+embargo boşluğu eksik")
        else:
            if not (b["train_start_ms"] < b["train_end_ms"] <= b["embargo_end_ms"] <= b["test_start_ms"] < b["test_end_ms"]):
                raise ValueError(f"fold {w.idx}: kronoloji bozuk: {b}")
            if b["test_start_ms"] - b["train_end_ms"] < gap:
                raise ValueError(f"fold {w.idx}: purge+embargo boşluğu eksik")
        if prev_test_end is not None and b["test_start_ms"] < prev_test_end:
            raise ValueError(f"fold {w.idx}: test pencereleri örtüşüyor")
        prev_test_end = b["test_end_ms"]
    hold = plan.get("holdout")
    if hold:
        for w in folds:
            latest = w.test_end
            if latest > hold["start_ms"] or w.train_end > hold["start_ms"]:
                raise ValueError(f"fold {w.idx}: kilitli holdout'a taşıyor")


def assign_rows(rows: Iterable[dict[str, Any]], plan: dict[str, Any], *,
                ts_key: str = "ts_ms") -> dict[str, Any]:
    """Satırları YALNIZ zaman damgasıyla train/test/holdout'a atar.

    Purge/embargo bölgesindeki satırlar HİÇBİR kümeye girmez (`purged`). Zamanı olmayan satır
    kullanılamaz (`unassigned`) — sessizce train'e düşmez. Holdout satırları `holdout` altında
    ayrı döner ve fold döngüsüne verilmez.
    """
    folds = plan["folds"]
    three = is_three_way(plan)
    hold = plan.get("holdout")
    out: dict[str, Any] = {
        "layout": plan.get("layout", "two_way"),
        "folds": [{"idx": w.idx, "train": [], "test": [], **({"validation": []} if three else {})}
                  for w in folds],
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
            elif three and w.val_start <= ts < w.val_end:
                slot["validation"].append(r)
                placed = True
            elif w.test_start <= ts < w.test_end:
                slot["test"].append(r)
                placed = True
            elif w.train_end <= ts < w.test_start:
                # purge/embargo bölgeleri (üç yolluda train→val ve val→test boşluklarının ikisi de)
                placed = True
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
    folds = plan["folds"]
    three = is_three_way(plan)
    violations: list[str] = []
    hold = plan.get("holdout")
    hold_ids = {id(r) for r in assignment.get("holdout", [])}
    parts = ("train", "validation", "test") if three else ("train", "test")
    for w, slot in zip(folds, assignment["folds"]):
        for r in slot["train"]:
            if int(r[ts_key]) >= w.train_end:
                violations.append(f"fold {w.idx}: train satırı train_end sonrası ({r.get(ts_key)})")
        if three:
            for r in slot["validation"]:
                if not (w.val_start <= int(r[ts_key]) < w.val_end):
                    violations.append(f"fold {w.idx}: validation satırı pencere dışı ({r.get(ts_key)})")
            for r in slot["test"]:
                if int(r[ts_key]) < w.val_end:
                    violations.append(f"fold {w.idx}: test satırı validation penceresine taşıyor")
        for part in parts:
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
    three = is_three_way(plan)
    folds_out = []
    oos_exp: list[float] = []
    for w, slot in zip(plan["folds"], assignment["folds"]):
        fm = {"idx": w.idx, "bounds": w.bounds(),
              "n_train": len(slot["train"]), "n_test": len(slot["test"]),
              "train": metric_fn(slot["train"]), "test": metric_fn(slot["test"]),
              "symbols_test": sorted({str(r.get("symbol")) for r in slot["test"] if r.get("symbol")}),
              "regimes_test": sorted({str(r.get("regime")) for r in slot["test"] if r.get("regime")})}
        if three:
            # Fold raporu train/validation/test tarihlerini AYRI gösterir.
            fm["n_validation"] = len(slot["validation"])
            fm["validation"] = metric_fn(slot["validation"])
            fm["windows"] = {"train": [w.train_start, w.train_end],
                             "validation": [w.val_start, w.val_end],
                             "test": [w.test_start, w.test_end]}
        folds_out.append(fm)
        e = fm["test"].get("expectancy_r")
        if isinstance(e, (int, float)) and math.isfinite(float(e)):
            oos_exp.append(float(e))
    stability = (sum(1 for e in oos_exp if e > 0) / len(oos_exp)) if oos_exp else None
    pbo: float | None = None
    pbo_state = "not_computable"
    if len(oos_exp) >= min_pbo_folds and n_candidates >= min_pbo_candidates:
        pbo_state = "requires_candidate_matrix"             # tek politika akışında hesaplanamaz
    return {"schema_version": SCHEMA_VERSION, "mode": plan["mode"],
            "layout": plan.get("layout", "two_way"), "params": plan["params"],
            "params_tried": params_tried, "n_candidates": n_candidates,
            "folds": folds_out, "n_folds": len(folds_out),
            "purged_rows": assignment.get("purged", 0), "unassigned_rows": assignment.get("unassigned", 0),
            "holdout_rows": len(assignment.get("holdout", [])),
            "oos_expectancy_r_by_fold": [round(e, 4) for e in oos_exp],
            "oos_sign_consistency": round(stability, 4) if stability is not None else None,
            "pbo": pbo, "pbo_state": pbo_state,
            "holdout_locked": bool(plan.get("holdout"))}


def run_three_way(plan: dict[str, Any], assignment: dict[str, Any], *,
                  fit_fn: Callable[[list[dict]], Any],
                  candidates_fn: Callable[[Any, list[dict]], list[Any]],
                  select_fn: Callable[[list[Any], list[dict]], Any],
                  evaluate_fn: Callable[[Any, list[dict]], dict[str, Any]],
                  min_train: int = 1, min_validation: int = 1,
                  min_test: int = 1) -> dict[str, Any]:
    """Üç yollu akışı ZORLAYARAK yürütür ve her adımın hangi veriyi gördüğünü kaydeder.

    Sözleşme (kod düzeyinde):
    * `fit_fn` YALNIZ train satırlarını alır → validation/test fitting'i değiştiremez,
    * `candidates_fn` fit parametreleri + YALNIZ train satırlarını alır → adaylar train'de doğar,
    * `select_fn` adaylar + YALNIZ validation satırlarını alır → seçim test'i göremez,
    * `evaluate_fn` seçilen TEK aday + YALNIZ test satırlarını alır → test seçimi değiştiremez.

    Yetersiz veri fail-closed: eşiklerin altındaki fold `skipped` işaretlenir ve seçim/değerlendirme
    ÇALIŞTIRILMAZ (sessizce "nötr sonuç" üretilmez).
    """
    if not is_three_way(plan):
        raise ValueError("run_three_way yalnız layout='three_way' planlarda çalışır "
                         "(make_folds(validation_days>0))")
    folds_out: list[dict[str, Any]] = []
    for w, slot in zip(plan["folds"], assignment["folds"]):
        train, val, test = slot["train"], slot["validation"], slot["test"]
        rec: dict[str, Any] = {"idx": w.idx, "windows": {"train": [w.train_start, w.train_end],
                                                         "validation": [w.val_start, w.val_end],
                                                         "test": [w.test_start, w.test_end]},
                               "n_train": len(train), "n_validation": len(val), "n_test": len(test)}
        if len(train) < min_train or len(val) < min_validation or len(test) < min_test:
            rec.update({"skipped": True, "reason": "insufficient_sample",
                        "fit": None, "n_candidates": 0, "selected": None, "test_metrics": None})
            folds_out.append(rec)
            continue
        fit = fit_fn(list(train))                                  # YALNIZ train
        cands = list(candidates_fn(fit, list(train)))              # YALNIZ train
        if not cands:
            rec.update({"skipped": True, "reason": "no_candidates", "fit": fit,
                        "n_candidates": 0, "selected": None, "test_metrics": None})
            folds_out.append(rec)
            continue
        chosen = select_fn(cands, list(val))                       # YALNIZ validation
        test_metrics = evaluate_fn(chosen, list(test))             # YALNIZ test
        rec.update({"skipped": False, "reason": None, "fit": fit, "n_candidates": len(cands),
                    "selected": chosen, "test_metrics": test_metrics})
        folds_out.append(rec)
    evaluated = [f for f in folds_out if not f.get("skipped")]
    exps = [e for f in evaluated
            if isinstance((e := (f["test_metrics"] or {}).get("expectancy_r")), (int, float))
            and math.isfinite(float(e))]
    return {"schema_version": SCHEMA_VERSION, "layout": "three_way", "mode": plan["mode"],
            "params": plan["params"], "folds": folds_out, "n_folds": len(folds_out),
            "n_evaluated": len(evaluated), "n_skipped": len(folds_out) - len(evaluated),
            "oos_expectancy_r_by_fold": [round(float(e), 4) for e in exps],
            "oos_sign_consistency": round(sum(1 for e in exps if e > 0) / len(exps), 4) if exps else None,
            "holdout_locked": bool(plan.get("holdout")),
            "holdout_rows": len(assignment.get("holdout", [])),
            "holdout_used_in_selection": False}
