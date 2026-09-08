"""Eşleşmiş olasılık kıyaslaması — hedef KAYNAKTAN izlenir (`research_prob_benchmark_v1`).

Önceki kalibrasyon iki hatayı taşıyordu ve burada DÜZELTİLİR:

1. **Farklı satır kümeleri.** Ön tahmin 39, harman 62 satırda ölçülmüştü; ön tahmin yalnız
   `features.p_win_prior` doğrudan kayıtlıyken alınıyordu. Artık kimlik tersinden
   (`DERIVED`) geri kazanılıyor ve karşılaştırma AYNI kimlik kümesinde yapılıyor
   (ID kümesi sha256'sı raporlanır).
2. **Hedef uyumsuzluğu.** Üretim etiketi `learn/labels.py::label_outcome` +
   `LearnerV2.train_challenger` ile izlendi: **SCRATCH üçüncü bir sınıf DEĞİL**, kazanç da
   sayılmaz — PAYDADA kalır. Yani `p_win` **koşulsuz** `P(r_multiple > +0.25R)`tır.
   Önceki ölçüm SCRATCH'i dışlıyordu; bu, koşullu bir olasılığı koşulsuz bir skora karşı
   ölçmek olurdu.

İki AYRI hedef asla karıştırılmaz:

* **A — YAŞAM BOYU** (belirtilen çıkış politikası altında): çözülmeyen ömür **SANSÜRLÜ**dür.
* **B — SABİT 72 SAAT**: yalnız tam gözlem ufku geçmiş adaylar; ufuk sonunda dondurulmuş
  MTM kuralı. B, A'nın kalibrasyonu DEĞİLDİR (denklik gösterilmedikçe).
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable

from . import POINT_IN_TIME, RETROSPECTIVE_RESEARCH
from .dual_edge import LABEL_RULE_ID, SCRATCH_R, recover_prior

SCHEMA_VERSION = "research_prob_benchmark_v1"

TARGET_A = "A_LIFETIME_UNDER_CHAMPION_EXIT"
TARGET_B = "B_FIXED_72H_FROZEN_MTM"

WIN, NOT_WIN, CENSORED = "WIN", "NOT_WIN", "CENSORED"
NOT_EVALUABLE = "NOT_EVALUABLE"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def id_set_hash(ids: list[str]) -> str:
    """Karşılaştırılan kimlik kümesinin parmak izi — aynı hash = aynı satırlar."""
    return hashlib.sha256(json.dumps(sorted(ids), separators=(",", ":")).encode()).hexdigest()[:16]


def label_target_contract() -> dict[str, Any]:
    """Üretim etiketinin GERÇEK sözleşmesi — kaynaktan izlendi, varsayılmadı."""
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": POINT_IN_TIME,
        "sources": ["learn/labels.py::label_outcome", "learn/learner_v2.py::on_trade_closed",
                    "learn/learner_v2.py::train_challenger"],
        "class_construction": ("|r| < 0.25R -> SCRATCH; aksi halde r > 0 -> WIN, r <= 0 -> LOSS "
                               "(`label_outcome`)"),
        "win_flag": "won = (outcome_class == 'WIN')",
        "hierarchy_update": "win.add(1.0 if won else 0.0) — SCRATCH 0.0 ekler ve n'i ARTIRIR",
        "classifier_target": "train_challenger: y = (r_multiple > 0.25)",
        "scratch_treatment": "PAYDADA — üçüncü sınıf DEĞİL, dışlanmaz, kazanç sayılmaz",
        "conditionality": "KOŞULSUZ P(r > +0.25R); 'scratch olmayan' koşuluna bağlı DEĞİL",
        "horizon": "işlem ÖMRÜ (stop/hedef/başa-baş/zaman çıkışı) — sabit ufuk DEĞİL",
        "payoff_mismatch": (
            "`avg_loss_r = -1.0` SABİT varsayımdır. Etiket 'kazanç değil' sınıfı SCRATCH'i de "
            "içerdiği için, her kazanç-olmayanı -1R saymak kayıp tarafını ABARTIR. Bu kohortta "
            "gerçekleşen SCRATCH sayısı ayrıca raporlanır."),
    }


def _lifetime_horizon_hours(ts_ms: float, cutoff_ms: float) -> float:
    return max(0.0, (cutoff_ms - ts_ms) / 3_600_000.0)


def build_targets(opps: list[dict[str, Any]], cache, *, cutoff_ms: float,
                  cost_per_fill_r: float, fixed_hours: float = 72.0,
                  fetch: bool = False) -> dict[str, Any]:
    """A (yaşam boyu, sansürlü) ve B (sabit ufuk, dondurulmuş MTM) sonuçlarını AYRI üretir."""
    from .entry_lab import FILL_NEXT_BAR_OPEN, HORIZON_CLOSE, RESOLVED, simulate_opportunity

    rows_a: dict[str, dict[str, Any]] = {}
    rows_b: dict[str, dict[str, Any]] = {}
    for o in opps:
        cid = str(o.get("candidate_id") or "")
        ts = _f(o.get("ts_ms"))
        if not cid or ts is None:
            continue
        # --- A: ömür boyu, mevcut TÜM pencere; çözülmezse SANSÜR
        h = _lifetime_horizon_hours(ts, cutoff_ms)
        sim = simulate_opportunity(o, cache, cutoff_ms=cutoff_ms, cost_per_fill_r=cost_per_fill_r,
                                   fill_mode=FILL_NEXT_BAR_OPEN, horizon_hours=h, fetch=fetch)
        if sim.get("state") == RESOLVED:
            r = _f(sim.get("net_r"))
            rows_a[cid] = {"label": (WIN if (r is not None and r > SCRATCH_R) else NOT_WIN),
                           "net_r": r, "exit_reason": sim.get("exit_reason"),
                           "observed_hours": round(h, 2)}
        else:
            rows_a[cid] = {"label": CENSORED, "reason": sim.get("state"),
                           "observed_hours": round(h, 2)}
        # --- B: SABİT ufuk; yalnız tam ufku geçmiş adaylar, ufuk sonunda dondurulmuş MTM
        simb = simulate_opportunity(o, cache, cutoff_ms=cutoff_ms,
                                    cost_per_fill_r=cost_per_fill_r,
                                    fill_mode=FILL_NEXT_BAR_OPEN, horizon_hours=fixed_hours,
                                    fetch=fetch)
        if simb.get("state") in (RESOLVED, HORIZON_CLOSE):
            r = _f(simb.get("net_r"))
            rows_b[cid] = {"label": (WIN if (r is not None and r > SCRATCH_R) else NOT_WIN),
                           "net_r": r, "resolution": simb.get("state"),
                           "exit_reason": simb.get("exit_reason")}
        else:
            rows_b[cid] = {"label": CENSORED, "reason": simb.get("state")}
    return {
        TARGET_A: {"rows": rows_a, "rule": ("şampiyon çıkış politikası altında ömür boyu; "
                                            "kural ile çözülmezse SANSÜR"),
                   "label_rule": f"{LABEL_RULE_ID} -> WIN if net_r > {SCRATCH_R} else NOT_WIN "
                                 "(SCRATCH paydada)"},
        TARGET_B: {"rows": rows_b, "fixed_hours": fixed_hours,
                   "rule": ("tam ufku geçmiş adaylar; ufuk sonunda DONDURULMUŞ MTM "
                            "(kalan pozisyon son bar kapanışıyla değerlenir)"),
                   "label_rule": f"WIN if net_r > {SCRATCH_R} else NOT_WIN"},
        "targets_are_distinct": ("B, A'nın kalibrasyonu DEĞİLDİR; denklik GÖSTERİLMEDİ"),
    }


def _reliability(pairs: list[tuple[float, float]], bins: int = 4) -> list[dict[str, Any]]:
    if len(pairs) < bins * 3:
        return []
    ordered = sorted(pairs, key=lambda t: t[0])
    per = len(ordered) // bins
    out = []
    for i in range(bins):
        chunk = ordered[i * per:(i + 1) * per] if i < bins - 1 else ordered[i * per:]
        if not chunk:
            continue
        mp = sum(p for p, _ in chunk) / len(chunk)
        my = sum(y for _, y in chunk) / len(chunk)
        out.append({"n": len(chunk), "mean_score": round(mp, 4),
                    "observed_rate": round(my, 4), "gap": round(mp - my, 4),
                    "score_range": [round(chunk[0][0], 4), round(chunk[-1][0], 4)]})
    return out


class _Lcg:
    def __init__(self, seed: int) -> None:
        self.s = int(seed) & 0xFFFFFFFF or 1

    def nxt(self, n: int) -> int:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s % max(1, n)


def _paired_delta_ci(items: list[tuple[str, float, float]], *, iters: int = 2000,
                     seed: int = 7) -> dict[str, Any]:
    """Eşleşmiş Brier farkı (A − B) için SEMBOL KÜMESİ bootstrap'ı.

    Aynı sembolün örtüşen fırsatları bağımsız DEĞİLDİR; küme bootstrap'ı bunu hesaba katar.
    """
    if len(items) < 5:
        return {"state": "INSUFFICIENT_SAMPLE", "n": len(items)}
    clusters: dict[str, list[float]] = {}
    for sym, a, b in items:
        clusters.setdefault(sym, []).append(a - b)
    keys = list(clusters)
    obs = sum(d for v in clusters.values() for d in v) / sum(len(v) for v in clusters.values())
    rng = _Lcg(seed)
    means = []
    for _ in range(iters):
        vals: list[float] = []
        for _ in range(len(keys)):
            vals.extend(clusters[keys[rng.nxt(len(keys))]])
        if vals:
            means.append(sum(vals) / len(vals))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return {"state": "RUN", "n": len(items), "n_clusters": len(keys),
            "mean_delta": round(obs, 6), "ci95": [round(lo, 6), round(hi, 6)],
            "excludes_zero": bool(lo > 0 or hi < 0)}


def score_predictor(name: str, pairs: list[tuple[float, float]]) -> dict[str, Any]:
    """Brier + log loss + reliability + (koşullu) AUC. AUC yalnız iki sınıf ve varyans varsa."""
    if len(pairs) < 10:
        return {"predictor": name, "state": NOT_EVALUABLE, "n": len(pairs),
                "reason": "n < 10"}
    n = len(pairs)
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    eps = 1e-12
    ll = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
              for p, y in pairs) / n
    pos = [p for p, y in pairs if y > 0.5]
    neg = [p for p, y in pairs if y <= 0.5]
    mean_p = sum(p for p, _ in pairs) / n
    sd = math.sqrt(sum((p - mean_p) ** 2 for p, _ in pairs) / n)
    auc: float | None = None
    auc_state = "NOT_EVALUABLE"
    if pos and neg and sd > 1e-6:
        wins = sum(1 for a in pos for b in neg if a > b) + \
            0.5 * sum(1 for a in pos for b in neg if a == b)
        auc = round(wins / (len(pos) * len(neg)), 4)
        auc_state = "RUN"
    elif sd <= 1e-6:
        auc_state = "NOT_EVALUABLE(constant_score)"
    elif not pos or not neg:
        auc_state = "NOT_EVALUABLE(single_class)"
    return {"predictor": name, "state": "RUN", "n": n, "n_pos": len(pos), "n_neg": len(neg),
            "brier": round(brier, 6), "log_loss": round(ll, 6),
            "auc": auc, "auc_state": auc_state,
            "mean_score": round(mean_p, 6), "score_sd": round(sd, 6),
            "observed_rate": round(sum(y for _, y in pairs) / n, 6),
            "calibration_gap": round(mean_p - sum(y for _, y in pairs) / n, 6),
            "reliability": _reliability(pairs)}


def run_benchmark(opps: list[dict[str, Any]], targets: dict[str, Any], *,
                  base_rate_fn: Callable[[float], float | None],
                  target: str, seed: int = 7) -> dict[str, Any]:
    """AYNI kimlik kümesinde üç tahmin ediciyi karşılaştırır."""
    rows = targets[target]["rows"]
    by_id = {str(o.get("candidate_id")): o for o in opps}
    cohort: list[dict[str, Any]] = []
    censored, missing_pred = 0, 0
    for cid, lab in rows.items():
        if lab["label"] == CENSORED:
            censored += 1
            continue
        o = by_id.get(cid)
        if o is None:
            continue
        p_md = _f(o.get("p_win_model"))
        p_pr = _f(o.get("p_win_prior"))
        prov = "recorded"
        if p_pr is None:
            rec = recover_prior(o)
            if rec.get("p_gate") is not None and rec.get("in_unit_interval"):
                p_pr, prov = rec["p_gate"], "DERIVED(gross-identity-inversion)"
        br = base_rate_fn(_f(o.get("ts_ms")) or 0.0)
        if p_md is None or p_pr is None or br is None:
            missing_pred += 1
            continue
        cohort.append({"candidate_id": cid, "symbol": o.get("symbol"), "as_of": o.get("ts"),
                       "y": 1.0 if lab["label"] == WIN else 0.0,
                       "p_prior": p_pr, "p_prior_provenance": prov,
                       "p_blend": p_md, "p_base": br, "net_r": lab.get("net_r")})
    if len(cohort) < 10:
        return {"schema_version": SCHEMA_VERSION, "target": target, "state": NOT_EVALUABLE,
                "n_cohort": len(cohort), "n_censored": censored,
                "reason": "eşleşmiş kohort < 10 satır"}
    ids = [c["candidate_id"] for c in cohort]
    preds = {"p_win_prior": [(c["p_prior"], c["y"]) for c in cohort],
             "p_win_blend": [(c["p_blend"], c["y"]) for c in cohort],
             "past_only_base_rate": [(c["p_base"], c["y"]) for c in cohort]}
    scores = {k: score_predictor(k, v) for k, v in preds.items()}
    brier = {k: (v.get("brier") if v.get("state") == "RUN" else None) for k, v in scores.items()}
    paired = {}
    for a, b in (("p_win_blend", "past_only_base_rate"), ("p_win_prior", "past_only_base_rate"),
                 ("p_win_blend", "p_win_prior")):
        items = [(c["symbol"], (c[{"p_win_prior": "p_prior", "p_win_blend": "p_blend",
                                   "past_only_base_rate": "p_base"}[a]] - c["y"]) ** 2,
                  (c[{"p_win_prior": "p_prior", "p_win_blend": "p_blend",
                      "past_only_base_rate": "p_base"}[b]] - c["y"]) ** 2) for c in cohort]
        paired[f"{a}_minus_{b}"] = _paired_delta_ci(items, seed=seed)
    n_scratch = sum(1 for c in cohort
                    if c.get("net_r") is not None and abs(c["net_r"]) < SCRATCH_R)
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": RETROSPECTIVE_RESEARCH,
        "target": target, "target_rule": targets[target]["rule"],
        "label_rule": targets[target]["label_rule"],
        "state": "RUN",
        "n_cohort": len(cohort), "n_censored": censored, "n_missing_predictor": missing_pred,
        "id_set_sha256": id_set_hash(ids),
        "identical_id_set_for_all_predictors": True,
        "n_per_predictor": {k: len(v) for k, v in preds.items()},
        "prior_provenance": {p: sum(1 for c in cohort if c["p_prior_provenance"] == p)
                             for p in sorted({c["p_prior_provenance"] for c in cohort})},
        "scores": scores,
        "brier_ranking": sorted([k for k, v in brier.items() if v is not None],
                                key=lambda k: brier[k]),
        "paired_brier_delta": paired,
        "n_scratch_outcomes_in_cohort": n_scratch,
        "limitations": [
            "Örneklem küçük ve tek bir kısa pencerede; sembol/zaman bağımlılığı yüksek.",
            "Eşleşmiş fark GA'sı SEMBOL KÜMESİ bootstrap'ıdır; zaman bağımlılığı ayrıca "
            "modellenmedi.",
            "AUC < 0.5 kârlı bir ters sinyal KANITLAMAZ; olasılık doğruluğu, sıralama ve "
            "ekonomik değer AYRI sorulardır.",
            "Taban çizgisi kanonik kapanışlardan gelir; aday evreni FARKLI bir popülasyondur.",
        ],
    }


__all__ = ["CENSORED", "NOT_EVALUABLE", "NOT_WIN", "SCHEMA_VERSION", "TARGET_A", "TARGET_B",
           "WIN", "build_targets", "id_set_hash", "label_target_contract", "run_benchmark",
           "score_predictor"]
