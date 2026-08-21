"""Baseline ↔ candidate policy walk-forward karşılaştırması.

Mevcut `evaluate_replay` yalnız HAM OOS getirilerini ölçüyordu; bu modül gerçek politika karşılaştırması
yapar: her fold'da **yalnız geçmiş train verisiyle** iç validation üzerinde aday seçilir, seçilen aday
**sonraki test fold'unda** uygulanır ve baseline ile karşılaştırılır. Test fold'u seçim için ASLA
kullanılmaz. Sonuç en fazla RESEARCH_ONLY/SHADOW_CANDIDATE'tır; CHAMPION/LIVE terfisi bu yolda YOKTUR.
"""
from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, utc_now
from ..learn.policy import CandidatePolicy, baseline_policy, generate_candidates, validate_policy
from .research import ReplaySafetyError, _fold_rows, _r_metrics, iso_ms

POLICY_EVAL_REPORT = "policy_evaluation.json"
MIN_TEST_TRADES = 10
SELECTION_PENALTY_PER_CANDIDATE = 0.002      # çoklu test/seçim yanlılığı cezası (R cinsinden)


def _apply(policy: CandidatePolicy, rows: list[dict]) -> tuple[list[float], list[dict]]:
    """Politikayı işlem listesine uygula: izin verilenler (boyut çarpanıyla ölçekli R) + karar kayıtları."""
    rs, decisions = [], []
    for r in rows:
        out = r.get("outcome") or {}
        snap = (r.get("snapshot") or {}).get("values") or {}
        side = str(out.get("side") or snap.get("side") or ("LONG" if snap.get("is_long") else "SHORT"))
        symbol = str(out.get("symbol") or (r.get("snapshot") or {}).get("symbol") or "?")
        p_win = float(snap.get("pattern_p_win") or snap.get("p_win_prior") or 0.5)
        exp_r = float(snap.get("expected_r") or 0.0)
        d = policy.decide(snap, side=side, symbol=symbol, p_win=p_win, expected_net_r=exp_r)
        raw_r = float(out.get("r_multiple", 0) or 0)
        if d["allow"]:
            rs.append(raw_r * float(d["size_multiplier"]))
        decisions.append({"trade_id": r.get("trade_id"), "side": side, "symbol": symbol,
                          "allow": d["allow"], "size_multiplier": d["size_multiplier"], "reasons": d["reasons"]})
    return rs, decisions


def _score(rows: list[dict], policy: CandidatePolicy, *, n_candidates: int = 1) -> float:
    """İç validation skoru: beklenti − seçim yanlılığı cezası. Yalnız TRAIN/VALIDATION verisiyle çağrılır."""
    rs, _ = _apply(policy, rows)
    if len(rs) < 5:
        return -math.inf
    m = _r_metrics(rs)
    penalty = SELECTION_PENALTY_PER_CANDIDATE * max(0, n_candidates - 1)
    return float(m["expectancy_r"]) - penalty


def _bootstrap_ci(rs: list[float], *, iters: int = 500, seed: int = 7) -> tuple[float | None, float | None]:
    if len(rs) < 5:
        return None, None
    rnd = _Rng(seed)
    means = []
    n = len(rs)
    for _ in range(iters):
        means.append(sum(rs[rnd.randint(n)] for _ in range(n)) / n)
    means.sort()
    return round(means[int(0.025 * iters)], 4), round(means[int(0.975 * iters) - 1], 4)


class _Rng:
    """Deterministik LCG (numpy/random global durumuna dokunmaz)."""

    def __init__(self, seed: int):
        self.s = (int(seed) or 1) & 0xFFFFFFFF

    def randint(self, n: int) -> int:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s % max(1, n)


def _side_symbol_breakdown(rows: list[dict], policy: CandidatePolicy) -> dict:
    by_side: dict[str, list[float]] = {}
    by_symbol: dict[str, list[float]] = {}
    for r in rows:
        out = r.get("outcome") or {}
        snap = (r.get("snapshot") or {}).get("values") or {}
        side = str(out.get("side") or ("LONG" if snap.get("is_long") else "SHORT"))
        symbol = str(out.get("symbol") or (r.get("snapshot") or {}).get("symbol") or "?")
        d = policy.decide(snap, side=side, symbol=symbol,
                          p_win=float(snap.get("pattern_p_win") or snap.get("p_win_prior") or 0.5),
                          expected_net_r=float(snap.get("expected_r") or 0.0))
        if d["allow"]:
            v = float(out.get("r_multiple", 0) or 0) * float(d["size_multiplier"])
            by_side.setdefault(side, []).append(v)
            by_symbol.setdefault(symbol, []).append(v)
    return {"by_side": {k: _r_metrics(v) for k, v in sorted(by_side.items())},
            "by_symbol": {k: _r_metrics(v) for k, v in sorted(by_symbol.items())}}


def evaluate_policies(cfg: Any, replay_dir: Path, rows: list[dict], bounds: list[dict], *,
                      seed: int = 7, max_candidates: int = 24, point_in_time: bool = False,
                      survivorship_present: bool = True, min_test_trades: int = MIN_TEST_TRADES) -> dict:
    """Fold bazlı: train+validation'da aday seç → test fold'unda uygula → baseline ile karşılaştır."""
    if len(bounds) < 2:
        raise ReplaySafetyError(f"politika değerlendirmesi için en az 2 fold gerekir: {len(bounds)}")
    prof = getattr(getattr(cfg, "v3", None), "risk_profiles", None)
    max_lev = 1.0
    try:
        from ..risk import resolve_profile
        rp = resolve_profile(prof.profile, prof.overrides, i_understand=prof.i_understand)
        max_lev = float(min(1.0, rp.futures_max_leverage))
    except Exception:  # noqa: BLE001 — profil okunamazsa en muhafazakâr tavan
        max_lev = 1.0
    cands = generate_candidates(seed=seed, max_candidates=max_candidates, risk_profile_max_leverage=max_lev)
    for c in cands:
        validate_policy(c, risk_profile_max_leverage=max_lev)
    base = baseline_policy(seed)
    folds, base_all, cand_all, picks = [], [], [], []
    for b in sorted(bounds, key=lambda x: x["train_end_ms"]):
        train, test, _ex = _fold_rows(rows, b)
        if len(test) < min_test_trades or len(train) < 10:
            folds.append({"idx": b["idx"], "n_train": len(train), "n_test": len(test), "skipped": "yetersiz örnek"})
            continue
        # İç validation: train'in SON %30'u (test'e hiç bakılmaz)
        cut = max(5, int(len(train) * 0.7))
        val = train[cut:] or train[-5:]
        scored = [(c, _score(val, c, n_candidates=len(cands))) for c in cands]
        scored = [(c, s) for c, s in scored if s > -math.inf]
        if not scored:
            folds.append({"idx": b["idx"], "n_train": len(train), "n_test": len(test), "skipped": "aday seçilemedi"})
            continue
        scored.sort(key=lambda cs: (-cs[1], cs[0].policy_id))          # deterministik tie-break
        pick, pick_score = scored[0]
        picks.append(pick.policy_id)
        b_rs, _ = _apply(base, test)
        c_rs, c_dec = _apply(pick, test)
        base_all += b_rs
        cand_all += c_rs
        blocked = sum(1 for d in c_dec if not d["allow"])
        folds.append({"idx": b["idx"], "n_train": len(train), "n_test": len(test),
                      "train_range": [iso_ms(b["train_start_ms"]), iso_ms(b["train_end_ms"])],
                      "test_range": [iso_ms(b["test_start_ms"]), iso_ms(b["test_end_ms"])],
                      "selected_policy": pick.policy_id, "selection_score": round(pick_score, 6),
                      "validation_n": len(val), "blocked_in_test": blocked,
                      "baseline": _r_metrics(b_rs), "candidate": _r_metrics(c_rs)})
    scored_folds = [f for f in folds if "baseline" in f]
    if len(scored_folds) < 2:
        raise ReplaySafetyError(f"yeterli skorlanan fold yok: {len(scored_folds)} < 2")
    b_m, c_m = _r_metrics(base_all), _r_metrics(cand_all)
    b_ci, c_ci = _bootstrap_ci(base_all, seed=seed), _bootstrap_ci(cand_all, seed=seed)
    diff = [c - b for c, b in zip(cand_all, base_all)] if len(cand_all) == len(base_all) else []
    improved = sum(1 for f in scored_folds
                   if (f["candidate"]["expectancy_r"] or -9) > (f["baseline"]["expectancy_r"] or -9))
    consistency = round(improved / len(scored_folds), 4)
    gates = {
        "enough_oos": bool(c_m["n"] >= min_test_trades),
        "candidate_positive": bool(c_m["expectancy_r"] is not None and c_m["expectancy_r"] > 0),
        "beats_baseline": bool(c_m["expectancy_r"] is not None and b_m["expectancy_r"] is not None
                               and c_m["expectancy_r"] > b_m["expectancy_r"]),
        "candidate_ci_low_above_zero": bool(c_ci[0] is not None and c_ci[0] > 0),
        "profit_factor_above_one": bool(c_m.get("profit_factor") is not None and c_m["profit_factor"] > 1.0),
        "fold_consistency": bool(consistency >= 0.6),
        "enough_folds": bool(len(scored_folds) >= 2),
        "point_in_time": bool(point_in_time),
        "survivorship_clean": bool(not survivorship_present),
    }
    all_ok = all(gates.values())
    verdict = "SHADOW_CANDIDATE" if all_ok else ("RESEARCH_ONLY" if gates["candidate_positive"] and gates["beats_baseline"]
                                                 else "REJECTED")
    winner = max(set(picks), key=picks.count) if picks else None
    report = {
        "schema": "policy_walk_forward_v1", "generated_at": iso(utc_now()), "run_dir": str(replay_dir),
        "seed": seed, "n_candidates": len(cands), "selection_penalty_per_candidate": SELECTION_PENALTY_PER_CANDIDATE,
        "selected_policies": picks, "most_selected": winner,
        "folds": folds, "scored_folds": len(scored_folds),
        "baseline": {**b_m, "ci95": list(b_ci)}, "candidate": {**c_m, "ci95": list(c_ci)},
        "delta_expectancy_r": (round((c_m["expectancy_r"] or 0) - (b_m["expectancy_r"] or 0), 4)
                               if c_m["expectancy_r"] is not None and b_m["expectancy_r"] is not None else None),
        "paired_diff_mean": round(statistics.fmean(diff), 4) if diff else None,
        "fold_consistency": consistency,
        "breakdown": {"baseline": _side_symbol_breakdown(rows, base), "candidate": _side_symbol_breakdown(rows, cands[0])},
        "gates": gates, "failed_gates": sorted(k for k, v in gates.items() if not v),
        "verdict": verdict,
        "method": {"selection": "yalnız train'in son %30'u (iç validation); test fold'u seçime GİRMEZ",
                   "purge_embargo": "fold sınırları replay bounds'tan; purge/embargo kayıtları dışlanır",
                   "multiple_testing": f"{len(cands)} aday → seçim skoru {SELECTION_PENALTY_PER_CANDIDATE}×(n−1) cezalı",
                   "ci": "bootstrap (deterministik LCG, seed'e bağlı)"},
        "promotion": {"live_promotion": False, "promote_called": False,
                      "note": "bu yol CHAMPION/LIVE terfisi ÜRETMEZ; en fazla SHADOW_CANDIDATE"},
    }
    atomic_write_json(Path(replay_dir) / POLICY_EVAL_REPORT, report, indent=1)
    return report


__all__ = ["POLICY_EVAL_REPORT", "evaluate_policies"]
