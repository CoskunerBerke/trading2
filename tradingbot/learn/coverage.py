"""Feature coverage gate — eğitimden ÖNCE hafızanın gerçekten öğrenilebilir olduğunu doğrular.

Core-4 dersi: replay hafızası pratikte yalnız `expected_r` doluydu; model "hangi koşulda zarar edildiğini"
öğrenemedi ama yine de bir model üretti. Bu modül böyle bir hafızada eğitimi **FEATURE_COVERAGE_INVALID**
ile durdurur (sessiz kötü model yerine dürüst blok).
"""
from __future__ import annotations

import math
from .snapshot import ALL_FIELDS, FIELD_NAMES, PREDICTION_FIELD_NAMES, SCHEMA_ID, SNAPSHOT_VERSION

COVERAGE_INVALID = "FEATURE_COVERAGE_INVALID"
MIN_REQUIRED_AVAILABLE = 0.90        # zorunlu alanların en az %90'ı dolu olmalı
MIN_OVERALL_AVAILABLE = 0.55         # bütün alanların en az %55'i
MAX_CONSTANT_RATIO = 0.60            # alanların en fazla %60'ı sabit/near-constant olabilir
MIN_SYMBOLS = 2
MIN_PER_SIDE = 5
_PRED = frozenset(PREDICTION_FIELD_NAMES)


def _stats(vals: list[float]) -> dict:
    n = len(vals)
    if not n:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None, "unique": 0, "constant": True}
    mean = sum(vals) / n
    std = math.sqrt(sum((x - mean) ** 2 for x in vals) / n) if n > 1 else 0.0
    uniq = len({round(x, 10) for x in vals})
    return {"n": n, "mean": round(mean, 6), "std": round(std, 6), "min": round(min(vals), 6),
            "max": round(max(vals), 6), "unique": uniq, "constant": bool(uniq <= 1 or std < 1e-12)}


def coverage_report(rows: list[dict], *, source: str | None = None) -> dict:
    """`rows`: birleşik trade kayıtları (entry snapshot + outcome). Rapor + gate kararı döndürür."""
    total = len(rows)
    problems: list[str] = []
    if not total:
        return {"ok": False, "code": COVERAGE_INVALID, "problems": ["hiç kayıt yok"], "n_rows": 0,
                "schema_id": SCHEMA_ID, "feature_version": SNAPSHOT_VERSION, "fields": {}}
    snaps, sources, versions = [], set(), set()
    join_ok = join_bad = 0
    ts_bad = 0
    symbols: dict[str, int] = {}
    sides: dict[str, int] = {}
    for r in rows:
        sources.add(str(r.get("source") or ""))
        snap = r.get("snapshot") or {}
        versions.add(int(snap.get("feature_version", 0) or 0))
        out = r.get("outcome") or {}
        if out and snap:
            join_ok += 1
        else:
            join_bad += 1
        if snap:
            snaps.append(snap)
            symbols[str(snap.get("symbol") or "?")] = symbols.get(str(snap.get("symbol") or "?"), 0) + 1
            sides[str(snap.get("side") or "?")] = sides.get(str(snap.get("side") or "?"), 0) + 1
            dts, lbs = str(snap.get("decision_ts") or ""), str(snap.get("last_bar_ts") or "")
            if dts and lbs and lbs > dts:
                ts_bad += 1
    if source and (sources - {source, ""}):
        problems.append(f"source namespace karışması: {sorted(sources)}")
    if len(versions - {0}) > 1:
        problems.append(f"karışık feature_version: {sorted(versions)}")
    if not snaps:
        problems.append("hiçbir kayıtta FeatureSnapshotV3 yok (eski sparse hafıza)")
    if join_bad:
        problems.append(f"entry/outcome join bozuk: {join_bad}/{total} kayıt eksik")
    if ts_bad:
        problems.append(f"timestamp leakage: {ts_bad} kayıtta son bar karar anından sonra")
    fields: dict[str, dict] = {}
    avail_req, avail_all, avail_pred, constants = [], [], [], 0
    for name, required in ALL_FIELDS:
        present = [float(s.get("values", {}).get(name)) for s in snaps
                   if isinstance(s.get("values"), dict) and s["values"].get(name) is not None]
        ratio = len(present) / len(snaps) if snaps else 0.0
        st = _stats(present)
        fields[name] = {"required": required, "available_pct": round(100.0 * ratio, 2),
                        "missing_pct": round(100.0 * (1 - ratio), 2), **st}
        avail_all.append(ratio)
        if required:
            avail_req.append(ratio)
        if name in _PRED:
            avail_pred.append(ratio)
        if st["constant"] and present:
            constants += 1
    req_avg = sum(avail_req) / len(avail_req) if avail_req else 0.0
    all_avg = sum(avail_all) / len(avail_all) if avail_all else 0.0
    pred_avg = sum(avail_pred) / len(avail_pred) if avail_pred else 0.0
    const_ratio = constants / max(1, len(FIELD_NAMES))
    if req_avg < MIN_REQUIRED_AVAILABLE:
        problems.append(f"zorunlu alan kapsamı yetersiz: %{req_avg * 100:.1f} < %{MIN_REQUIRED_AVAILABLE * 100:.0f}")
    if all_avg < MIN_OVERALL_AVAILABLE:
        problems.append(f"genel alan kapsamı yetersiz: %{all_avg * 100:.1f} < %{MIN_OVERALL_AVAILABLE * 100:.0f}")
    if const_ratio > MAX_CONSTANT_RATIO:
        problems.append(f"alanların %{const_ratio * 100:.0f}'i sabit/near-constant "
                        f"(> %{MAX_CONSTANT_RATIO * 100:.0f}) — model bağlam öğrenemez")
    covered_symbols = [s for s, c in symbols.items() if c > 0]
    if len(covered_symbols) < MIN_SYMBOLS:
        problems.append(f"sembol çeşitliliği yetersiz: {covered_symbols}")
    weak_sides = [s for s in ("LONG", "SHORT") if sides.get(s, 0) < MIN_PER_SIDE]
    if len(weak_sides) == 2:
        problems.append(f"her iki tarafta da yetersiz örnek: {sides}")
    elif weak_sides:
        problems.append(f"tek taraf yetersiz ({weak_sides[0]}: {sides.get(weak_sides[0], 0)}) — taraf modeli kurulamaz")
    ok = not problems
    return {"ok": ok, "code": ("OK" if ok else COVERAGE_INVALID), "problems": problems, "n_rows": total,
            "schema_id": SCHEMA_ID, "feature_version": SNAPSHOT_VERSION,
            "join": {"ok": join_ok, "broken": join_bad}, "invalid_timestamps": ts_bad,
            "required_available_pct": round(100.0 * req_avg, 2), "overall_available_pct": round(100.0 * all_avg, 2),
            # p_win modeline GERCEKTEN giren alanlarin kapsami (audit-only alanlar haric) -- raporlanir,
            # mevcut esikler DEGISTIRILMEZ; yeni bir kapi eklenmez.
            "prediction_available_pct": round(100.0 * pred_avg, 2),
            "prediction_fields": len(PREDICTION_FIELD_NAMES),
            "constant_fields": constants, "constant_ratio_pct": round(100.0 * const_ratio, 2),
            "nonconstant_ratio_pct": round(100.0 * (1.0 - const_ratio), 2),
            "missing_field_rate": {n: round(f["missing_pct"] / 100.0, 4) for n, f in fields.items() if f["missing_pct"] > 0},
            "symbols": dict(sorted(symbols.items())), "sides": dict(sorted(sides.items())),
            "sources": sorted(sources), "fields": fields,
            "thresholds": {"required_available": MIN_REQUIRED_AVAILABLE, "overall_available": MIN_OVERALL_AVAILABLE,
                           "max_constant_ratio": MAX_CONSTANT_RATIO, "min_symbols": MIN_SYMBOLS,
                           "min_per_side": MIN_PER_SIDE}}


__all__ = ["COVERAGE_INVALID", "coverage_report", "MIN_OVERALL_AVAILABLE", "MIN_REQUIRED_AVAILABLE",
           "MAX_CONSTANT_RATIO", "MIN_SYMBOLS", "MIN_PER_SIDE"]
