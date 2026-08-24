"""Journal kapsama (completeness) ölçümü ve terfi kapıları (`quant_coverage_v1`).

Amaç: algoritma değerlendirmesini olduğundan güçlü göstermemek. Journal'da feature snapshot,
specialist skoru, rejim veya maliyet alanı yoksa attribution/champion sonuçları o ölçüde zayıftır
ve bu AÇIKÇA raporlanır.

Bu modül YALNIZ mevcut state'in offline kapsamasını ölçer; worker'a yeni instrumentation EKLEMEZ
(o ayrı ve kontrollü bir deployment konusudur).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

SCHEMA_VERSION = "quant_coverage_v1"


@dataclass
class CoverageGates:
    """Kritik kapsama eşikleri (configurable). Altına düşülürse challenger terfisi KAPANIR."""
    min_outcome_labeled: float = 0.50
    min_feature_snapshot: float = 0.30
    min_regime: float = 0.30
    min_cost_fields: float = 0.50
    min_records: int = 30
    max_age_days: float | None = None       # None → yaş kapısı uygulanmaz

    def to_dict(self) -> dict[str, Any]:
        return {"min_outcome_labeled": self.min_outcome_labeled,
                "min_feature_snapshot": self.min_feature_snapshot,
                "min_regime": self.min_regime, "min_cost_fields": self.min_cost_fields,
                "min_records": self.min_records, "max_age_days": self.max_age_days}


def _has(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return math.isfinite(float(v))
    if isinstance(v, (dict, list, tuple, set, str)):
        return len(v) > 0
    return True


def _ratio(n: int, total: int) -> float | None:
    return round(n / total, 6) if total else None


def _ts_ms(rec: dict[str, Any]) -> int | None:
    v = rec.get("event_ts_ms")
    if isinstance(v, (int, float)) and math.isfinite(float(v)):
        return int(v)
    s = rec.get("event_ts_utc")
    if not isinstance(s, str) or len(s) < 10:
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def journal_coverage(rows: Iterable[dict[str, Any]], *, gates: CoverageGates | None = None,
                     now_ms: int | None = None) -> dict[str, Any]:
    """`quant_journal_v1` kayıtları → kapsama metrikleri + kapı sonucu (deterministik)."""
    g = gates or CoverageGates()
    rows = list(rows)
    total = len(rows)
    accepted = sum(1 for r in rows if r.get("accepted"))
    rejected_shadow = sum(1 for r in rows if not r.get("accepted"))
    labeled = sum(1 for r in rows if r.get("outcome_labeled"))
    feat = sum(1 for r in rows if _has(r.get("feature_snapshot")))
    spec = sum(1 for r in rows if _has(r.get("specialist_scores")))
    regime = sum(1 for r in rows if _has(r.get("regime")))
    cost = sum(1 for r in rows if _has(r.get("fees")) or _has(r.get("funding"))
               or _has(r.get("cost_estimate")))
    maemfe = sum(1 for r in rows if _has(r.get("mae_pct")) and _has(r.get("mfe_pct")))
    policy = sum(1 for r in rows if _has(r.get("policy_id")))
    legacy = sum(1 for r in rows
                 if not _has(r.get("feature_snapshot")) and not _has(r.get("specialist_scores")))
    flagged = sum(1 for r in rows if r.get("quality_flags"))

    stamps = [t for r in rows if (t := _ts_ms(r)) is not None]
    age_days = None
    if stamps and now_ms is not None:
        age_days = round(max(0.0, (now_ms - max(stamps)) / 86_400_000), 4)
    span_days = round((max(stamps) - min(stamps)) / 86_400_000, 4) if len(stamps) >= 2 else None

    cov = {
        "outcome_labeled": _ratio(labeled, total),
        "feature_snapshot": _ratio(feat, total),
        "specialist_scores": _ratio(spec, total),
        "regime": _ratio(regime, total),
        "cost_fields": _ratio(cost, total),
        "mae_mfe": _ratio(maemfe, total),
        "policy_id": _ratio(policy, total),
        "legacy_schema": _ratio(legacy, total),
        "quality_flagged": _ratio(flagged, total),
    }

    checks: list[dict[str, Any]] = []

    def gate(code: str, value: float | None, minimum: float) -> None:
        ok = value is not None and value >= minimum
        checks.append({"code": code, "passed": bool(ok), "value": value, "min": minimum})

    if total < g.min_records:
        checks.append({"code": "MIN_RECORDS", "passed": False, "value": total,
                       "min": g.min_records})
    else:
        checks.append({"code": "MIN_RECORDS", "passed": True, "value": total, "min": g.min_records})
    gate("OUTCOME_LABELED", cov["outcome_labeled"], g.min_outcome_labeled)
    gate("FEATURE_SNAPSHOT", cov["feature_snapshot"], g.min_feature_snapshot)
    gate("REGIME", cov["regime"], g.min_regime)
    gate("COST_FIELDS", cov["cost_fields"], g.min_cost_fields)
    if g.max_age_days is not None:
        fresh = age_days is not None and age_days <= g.max_age_days
        checks.append({"code": "DATA_AGE", "passed": bool(fresh), "value": age_days,
                       "min": g.max_age_days})

    failed = [c["code"] for c in checks if not c["passed"]]
    passed = not failed
    verdict = ("kapsama kapıları geçildi" if passed
               else "düşük kapsama: " + ", ".join(failed))
    warnings: list[str] = []
    if not passed:
        warnings.append("KRİTİK KAPSAMA DÜŞÜK — challenger terfisi kapalı, champion korunur")
    if cov["legacy_schema"] is not None and cov["legacy_schema"] > 0.5:
        warnings.append("kayıtların yarısından fazlası eski şema (feature/specialist alanı yok)")
    return {"schema_version": SCHEMA_VERSION,
            "n_records": total, "n_accepted": accepted, "n_rejected_shadow": rejected_shadow,
            "n_outcome_labeled": labeled, "n_feature_snapshot": feat,
            "n_specialist_scores": spec, "n_regime": regime, "n_cost_fields": cost,
            "n_mae_mfe": maemfe, "n_policy_id": policy, "n_legacy_schema": legacy,
            "coverage": cov, "data_age_days": age_days, "span_days": span_days,
            "gates": g.to_dict(), "checks": checks,
            "gates_passed": passed, "promotion_allowed": passed,
            "verdict": verdict, "warnings": warnings}
