"""policy_eval / walk-forward → normalize edilmiş kanıt paketi → champion değerlendirmesi
(`quant_evidence_v1`).

Neden: `champion.evaluate_challenger` doğru kapıları uyguluyordu ama kanıtları operatör elle
veriyordu. Bu köprü kanıtları OFFLINE ve DETERMİNİSTİK biçimde mevcut araştırma çıktılarından
(`replay.policy_eval` sonucu, `quant.walkforward` fold raporu, `quant.attribution` metrikleri,
`quant.manifest`, `quant.execution_scenarios`, `quant.coverage`) toplar.

Güvenlik sözleşmesi:
* Köprü YALNIZ rapor üretir. Config, strateji ağırlığı, ledger, outbox veya worker davranışı
  DEĞİŞTİRMEZ; otomatik terfi yolu YOKTUR.
* Kritik kanıt EKSİK ise sonuç `KEEP_CHAMPION` (fail-closed; "bilinmiyor" = "geçti" değildir).
* Leakage / izolasyon / veri kalitesi / maliyet-modeli eşitliği AÇIKÇA başarısızsa
  `REJECT_CHALLENGER`.
* Challenger daha kârlı olsa bile drawdown, tail, yoğunlaşma, senaryo dayanıklılığı veya journal
  kapsaması kapılarından biri düşerse terfi ÖNERİLMEZ.
"""
from __future__ import annotations

import math
from typing import Any

from .champion import KEEP_CHAMPION, PromotionGates, evaluate_challenger

SCHEMA_VERSION = "quant_evidence_v1"

#: Bunlar olmadan terfi değerlendirmesi anlamlı değildir (eksikse KEEP_CHAMPION).
CRITICAL_EVIDENCE = ("champion_metrics", "challenger_metrics", "leakage_passed",
                     "data_quality_passed", "isolation_verified", "same_cost_model")


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _regime_coverage(wf: dict[str, Any] | None) -> dict[str, Any]:
    """Fold'lardaki test rejimleri/sembolleri — kapsama darsa terfi kanıtı zayıftır."""
    if not wf:
        return {"regimes": [], "symbols": [], "n_regimes": 0, "n_symbols": 0, "state": "unavailable"}
    regimes: set[str] = set()
    symbols: set[str] = set()
    for f in wf.get("folds", []) or []:
        regimes.update(f.get("regimes_test") or [])
        symbols.update(f.get("symbols_test") or [])
    return {"regimes": sorted(regimes), "symbols": sorted(symbols),
            "n_regimes": len(regimes), "n_symbols": len(symbols),
            "state": "ok" if regimes or symbols else "empty"}


def build_evidence(*, champion_metrics: dict[str, Any] | None = None,
                   challenger_metrics: dict[str, Any] | None = None,
                   walk_forward: dict[str, Any] | None = None,
                   policy_eval: dict[str, Any] | None = None,
                   leakage: dict[str, Any] | None = None,
                   data_quality: dict[str, Any] | None = None,
                   isolation: dict[str, Any] | None = None,
                   cost_model_equal: bool | None = None,
                   execution_quality: dict[str, Any] | None = None,
                   scenarios: dict[str, Any] | None = None,
                   coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dağınık araştırma çıktılarını TEK normalize kanıt paketine indirger (deterministik).

    Hiçbir alan uydurulmaz: hesaplanamayan kanıt `None` kalır ve `missing_critical` listesine girer.
    """
    cm = champion_metrics or {}
    ch = challenger_metrics or {}
    ch_exp, cm_exp = _f(ch.get("expectancy_r")), _f(cm.get("expectancy_r"))
    ci = ch.get("bootstrap_ci_mean_r") or {}
    conc = ch.get("concentration") or {}

    fold_consistency = None
    pbo_state = None
    if walk_forward:
        fold_consistency = _f(walk_forward.get("oos_sign_consistency"))
        pbo_state = walk_forward.get("pbo_state")
    if policy_eval:
        # policy_eval kendi fold tutarlılığını raporluyorsa o birincildir (üretim araştırma hattı).
        fold_consistency = _f(policy_eval.get("fold_consistency")) if policy_eval.get(
            "fold_consistency") is not None else fold_consistency
        pbo_state = policy_eval.get("pbo_state", pbo_state)

    ev: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "champion_metrics": cm or None,
        "challenger_metrics": ch or None,
        "champion_n": cm.get("n") if isinstance(cm.get("n"), int) else None,
        "challenger_n": ch.get("n") if isinstance(ch.get("n"), int) else None,
        "oos_net_expectancy_r": ch_exp,
        "champion_expectancy_r": cm_exp,
        "expectancy_delta_r": round(ch_exp - cm_exp, 6) if (ch_exp is not None and cm_exp is not None) else None,
        "confidence_interval": {"low": _f(ci.get("low")), "high": _f(ci.get("high")),
                                "state": ci.get("state")} if ci else None,
        "max_drawdown_r": _f(ch.get("max_drawdown_r")),
        "champion_max_drawdown_r": _f(cm.get("max_drawdown_r")),
        "tail_loss_r_cvar5": _f(ch.get("tail_loss_r_cvar5")),
        "champion_tail_loss_r_cvar5": _f(cm.get("tail_loss_r_cvar5")),
        "fold_sign_consistency": fold_consistency,
        "regime_coverage": _regime_coverage(walk_forward),
        "symbol_concentration": _f(conc.get("top_symbol_share")),
        "trade_concentration": _f(conc.get("top_trade_share")),
        "same_cost_model": cost_model_equal,
        "isolation_verified": (isolation or {}).get("passed") if isolation is not None else None,
        "isolation_detail": (isolation or {}).get("detail") if isolation else None,
        "leakage_passed": (leakage or {}).get("passed") if leakage is not None else None,
        "leakage_violations": (leakage or {}).get("n_violations") if leakage else None,
        "data_quality_passed": (data_quality or {}).get("passed") if data_quality is not None else None,
        "data_quality_verdict": (data_quality or {}).get("verdict") if data_quality else None,
        "execution_data_quality": (execution_quality or {}).get("state") if execution_quality else None,
        "execution_provenance": (execution_quality or {}).get("provenance") if execution_quality else None,
        "pbo_state": pbo_state,
        "scenarios": scenarios or None,
        "journal_coverage": coverage or None,
    }
    missing = [k for k in CRITICAL_EVIDENCE if ev.get(k) is None]
    ev["missing_critical"] = missing
    ev["complete"] = not missing
    return ev


def evaluate_with_evidence(evidence: dict[str, Any], *, gates: PromotionGates | None = None,
                           require_scenario_robustness: bool = True,
                           require_coverage: bool = True) -> dict[str, Any]:
    """Normalize kanıt paketi → `PROMOTE_CANDIDATE` / `KEEP_CHAMPION` / `REJECT_CHALLENGER`.

    Yalnız ARAŞTIRMA ÖNERİSİDİR: dönen sözlük hiçbir yerde otomatik uygulanmaz, `auto_promotion`
    her zaman `False`'tur.
    """
    hard = {k: evidence.get(k) for k in ("leakage_passed", "data_quality_passed",
                                         "isolation_verified", "same_cost_model")}
    explicit_fail = [k for k, v in hard.items() if v is False]
    if explicit_fail:
        # AÇIK başarısızlık → REJECT (eksik kanıttan farklıdır).
        return _wrap(evaluate_challenger(evidence.get("champion_metrics") or {},
                                         evidence.get("challenger_metrics") or {},
                                         gates=gates,
                                         leakage_passed=evidence.get("leakage_passed"),
                                         data_quality_passed=evidence.get("data_quality_passed"),
                                         isolation_verified=evidence.get("isolation_verified"),
                                         same_cost_model=evidence.get("same_cost_model"),
                                         fold_consistency=evidence.get("fold_sign_consistency"),
                                         pbo_state=evidence.get("pbo_state")),
                     evidence)
    if evidence.get("missing_critical"):
        return _wrap({"schema_version": "quant_champion_v1", "decision": KEEP_CHAMPION,
                      "note": "kritik kanıt eksik: " + ", ".join(evidence["missing_critical"]),
                      "auto_promotion": False,
                      "checks": [{"code": f"MISSING_{k.upper()}", "passed": False,
                                  "detail": "kanıt sağlanmadı — 'bilinmiyor' geçti sayılmaz"}
                                 for k in evidence["missing_critical"]],
                      "label": "TEST DATA / RESEARCH — kârlılık kanıtı değildir"}, evidence)

    soft: dict[str, Any] = {}
    if require_scenario_robustness:
        sc = evidence.get("scenarios") or {}
        robust = sc.get("robust_across_scenarios")
        soft["SCENARIO_ROBUSTNESS"] = (
            robust, f"base/adverse/stress dayanıklılığı: {sc.get('verdict') or 'kanıt yok'}")
    if require_coverage:
        cov = evidence.get("journal_coverage") or {}
        soft["JOURNAL_COVERAGE"] = (
            cov.get("gates_passed"),
            f"journal kapsama kapıları: {cov.get('verdict') or 'kanıt yok'}")
    rc = evidence.get("regime_coverage") or {}
    soft["REGIME_COVERAGE"] = (rc.get("n_regimes", 0) >= 2 or rc.get("n_symbols", 0) >= 2,
                               f"test kapsaması: {rc.get('n_regimes', 0)} rejim / "
                               f"{rc.get('n_symbols', 0)} sembol")
    return _wrap(evaluate_challenger(evidence["champion_metrics"], evidence["challenger_metrics"],
                                     gates=gates,
                                     leakage_passed=evidence.get("leakage_passed"),
                                     data_quality_passed=evidence.get("data_quality_passed"),
                                     isolation_verified=evidence.get("isolation_verified"),
                                     same_cost_model=evidence.get("same_cost_model"),
                                     fold_consistency=evidence.get("fold_sign_consistency"),
                                     pbo_state=evidence.get("pbo_state"),
                                     extra_soft_gates=soft), evidence)


def _wrap(result: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    out["evidence_schema"] = evidence.get("schema_version")
    out["evidence_complete"] = evidence.get("complete")
    out["missing_critical"] = evidence.get("missing_critical") or []
    out["evidence_summary"] = {
        "champion_n": evidence.get("champion_n"), "challenger_n": evidence.get("challenger_n"),
        "oos_net_expectancy_r": evidence.get("oos_net_expectancy_r"),
        "expectancy_delta_r": evidence.get("expectancy_delta_r"),
        "confidence_interval": evidence.get("confidence_interval"),
        "max_drawdown_r": evidence.get("max_drawdown_r"),
        "tail_loss_r_cvar5": evidence.get("tail_loss_r_cvar5"),
        "fold_sign_consistency": evidence.get("fold_sign_consistency"),
        "regime_coverage": evidence.get("regime_coverage"),
        "symbol_concentration": evidence.get("symbol_concentration"),
        "trade_concentration": evidence.get("trade_concentration"),
        "data_quality_verdict": evidence.get("data_quality_verdict"),
        "execution_data_quality": evidence.get("execution_data_quality"),
        "pbo_state": evidence.get("pbo_state"),
    }
    out["applies_changes"] = False          # köprü hiçbir config/ledger/worker davranışı değiştirmez
    return out
