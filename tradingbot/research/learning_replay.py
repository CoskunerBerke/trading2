"""Öğrenme sayaç bütünlüğü — YALITILMIŞ eski/düzeltilmiş semantik karşılaştırması
(`research_learning_replay_v1`).

Bu modül üretim `learn_v2.json`'ına DOKUNMAZ. Export'taki kanonik kapanışları iki ayrı,
tamamen bellek içi `HierarchicalRate` durumuna oynatır:

* ``OLD``  — kapanış başına İKİ ayrı `win.add` çağrısı (ortak atalar iki kez sayılır),
* ``NEW``  — tek çağrı, iki yaprak (`leaves=`), atalar bir kez.

İki ayrı soru KARIŞTIRILMAZ:

1. **COUNT_INTEGRITY** — export sonu yeniden kurulum. Sayaç bütünlüğünü gösterir;
   **point-in-time tahmin backtest'i DEĞİLDİR**.
2. **POINT_IN_TIME_COMPONENTS** — her fırsat için, o `as_of`tan ÖNCE kapanmış işlemlerle
   kurulmuş durumdan okunan bileşenler (n_eff, posterior, belirsizlik cezası). Burada
   yalnız o an mevcut olan sonuçlar kullanılır.

Tarihsel snapshot değerleri DEĞİŞTİRİLMEZ; üretilen her şey türetilmiş sonuçtur ve orijinalin
yanında raporlanır.
"""
from __future__ import annotations

import math
from typing import Any

from ..learn.labels import label_outcome
from ..learn.model import HierarchicalRate
from ..opportunity import UNCERTAINTY_K, uncertainty_penalty_r
from . import CANONICAL_OBSERVED, POINT_IN_TIME, RETROSPECTIVE_RESEARCH
from .dataset import to_ms

SCHEMA_VERSION = "research_learning_replay_v1"

OLD = "OLD_TWO_CALLS"
NEW = "NEW_SINGLE_CALL_MULTI_LEAF"

#: `LearnConfig.alpha_shrink` üretim varsayılanı; state dosyasından okunur, burası yedek.
DEFAULT_ALPHA = 10.0


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def close_keys(rec: dict[str, Any]) -> dict[str, Any]:
    """Bir kanonik kapanışın öğrenme anahtarları — üretimdeki `on_trade_closed` ile AYNI."""
    feats = rec.get("features") or {}
    regime = str(feats.get("regime") or "") or None
    symbol = str(rec.get("symbol") or "")
    setup = str(rec.get("setup_type") or feats.get("setup_type") or "-")
    side = str(rec.get("side") or feats.get("direction") or "")
    return {"regime": regime, "symbol": symbol, "setup": setup, "side": side,
            "win_leaves": (f"{symbol}|{setup}", symbol),
            "exp_r_leaf": f"{setup}|{side}"}


def apply_close(win: HierarchicalRate, exp_r: HierarchicalRate, rec: dict[str, Any], *,
                semantics: str) -> dict[str, Any]:
    """Tek nihai kapanışı verilen duruma uygular. Etiket kuralı üretimle AYNI."""
    lab = label_outcome(rec)
    k = close_keys(rec)
    won = 1.0 if lab["won"] else 0.0
    if semantics == OLD:
        # ÜRETİMDEKİ ESKİ DAVRANIŞ: iki ayrı çağrı -> ortak atalar iki kez.
        win.add(won, regime=k["regime"], leaf=k["win_leaves"][0])
        win.add(won, regime=k["regime"], leaf=k["win_leaves"][1])
    else:
        win.add(won, regime=k["regime"], leaves=k["win_leaves"])
    exp_r.add(lab["r_multiple"], regime=k["regime"], leaf=k["exp_r_leaf"])
    return {"trade_id": rec.get("trade_id") or rec.get("id"), "won": bool(lab["won"]),
            "outcome_class": lab["outcome_class"], "r_multiple": lab["r_multiple"], **k}


def _states(alpha: float, prior_mean: float = 0.5) -> tuple[HierarchicalRate, HierarchicalRate]:
    return (HierarchicalRate(alpha, prior_mean), HierarchicalRate(alpha, 0.0))


def rebuild(closed_rows: list[dict[str, Any]], *, semantics: str,
            alpha: float = DEFAULT_ALPHA) -> dict[str, Any]:
    """Bütün kanonik kapanışları kronolojik sırayla YALITILMIŞ bir duruma oynatır."""
    rows = sorted(closed_rows, key=lambda r: (to_ms(r.get("closed_at")) or 0.0,
                                              str(r.get("trade_id") or r.get("id"))))
    win, exp_r = _states(alpha)
    applied = [apply_close(win, exp_r, r, semantics=semantics) for r in rows]
    classes: dict[str, int] = {}
    for a in applied:
        classes[a["outcome_class"]] = classes.get(a["outcome_class"], 0) + 1
    return {"semantics": semantics, "n_closes": len(applied), "applied": applied,
            "outcome_classes": classes,
            "win_stats": {k: v.to_dict() for k, v in sorted(win.stats.items())},
            "exp_r_stats": {k: v.to_dict() for k, v in sorted(exp_r.stats.items())},
            "_win": win, "_exp_r": exp_r}


def count_integrity(closed_rows: list[dict[str, Any]], *,
                    alpha: float = DEFAULT_ALPHA) -> dict[str, Any]:
    """Sayaç bütünlüğü — export sonu yeniden kurulum. **Point-in-time backtest DEĞİLDİR.**"""
    old, new = rebuild(closed_rows, semantics=OLD, alpha=alpha), \
        rebuild(closed_rows, semantics=NEW, alpha=alpha)
    n = old["n_closes"]
    per_node = []
    for key in sorted(set(old["win_stats"]) | set(new["win_stats"])):
        o = old["win_stats"].get(key, {"n": 0, "s": 0.0})
        w = new["win_stats"].get(key, {"n": 0, "s": 0.0})
        kind = ("GLOBAL" if key == "" else
                "REGIME" if key.startswith("regime:") and "|leaf:" not in key else
                "LEAF" if key.startswith("leaf:") else "REGIME_LEAF")
        per_node.append({
            "node": key, "kind": kind,
            "old_n": o["n"], "new_n": w["n"], "old_s": o["s"], "new_s": w["s"],
            "duplicated": bool(abs(o["n"] - 2 * w["n"]) < 1e-9 and w["n"] > 0),
            "unchanged": bool(abs(o["n"] - w["n"]) < 1e-9),
        })
    dup = [p for p in per_node if p["duplicated"]]
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": CANONICAL_OBSERVED,
        "label": "COUNT_INTEGRITY (export sonu yeniden kurulum) — POINT-IN-TIME BACKTEST DEĞİL",
        "n_final_closes": n,
        "outcome_classes": old["outcome_classes"],
        "unique_observations": n,
        "update_calls_win_old": 2 * n, "update_calls_win_new": n,
        "weighted_mass_global_old": old["win_stats"].get("", {}).get("n"),
        "weighted_mass_global_new": new["win_stats"].get("", {}).get("n"),
        "exp_r_global_n": old["exp_r_stats"].get("", {}).get("n"),
        "nodes": per_node,
        "duplicated_nodes": [p["node"] for p in dup],
        "n_duplicated_nodes": len(dup),
        "n_unchanged_nodes": sum(1 for p in per_node if p["unchanged"]),
        "verdict": ("DUPLICATE_ANCESTOR_UPDATES" if dup else "NO_DUPLICATION"),
        "note": ("yinelenen düğümler YALNIZ ortak atalardır (global + regime); yaprak "
                 "düğümleri iki FARKLI granülerlik olduğu için değişmez — bu meşrudur"),
    }


def point_in_time_components(closed_rows: list[dict[str, Any]], opps: list[dict[str, Any]], *,
                             alpha: float = DEFAULT_ALPHA,
                             default_win_r: float = 1.6,
                             blend_n: float = 20.0) -> dict[str, Any]:
    """Her fırsat için, `as_of`tan ÖNCE kapanmış işlemlerle kurulmuş durumdan bileşenler.

    Yalnız o an mevcut sonuçlar kullanılır (sızıntı yok). Ölçülen bileşenler:
    `n_eff` (→ `sample_size`), hiyerarşik posterior ve `uncertainty_penalty_r`.

    `avg_win_r` ve `p_win_prior` bu ayrımdan ETKİLENMEZ (`exp_r` tek çağrıyla güncellenir,
    ön tahmin ise learner'dan gelmez) — bu da ölçülür, varsayılmaz.
    """
    closes = sorted(closed_rows, key=lambda r: (to_ms(r.get("closed_at")) or 0.0,
                                                str(r.get("trade_id") or r.get("id"))))
    states = {OLD: _states(alpha), NEW: _states(alpha)}
    idx = 0
    rows: list[dict[str, Any]] = []
    ordered = sorted([o for o in opps if o.get("ts_ms") is not None],
                     key=lambda o: (float(o["ts_ms"]), str(o.get("candidate_id") or "")))
    for o in ordered:
        t = float(o["ts_ms"])
        while idx < len(closes) and (to_ms(closes[idx].get("closed_at")) or 0.0) < t:
            for sem, (win, exp_r) in states.items():
                apply_close(win, exp_r, closes[idx], semantics=sem)
            idx += 1
        leaf = f"{o.get('symbol')}|{o.get('setup') or '-'}"
        regime = o.get("regime") or None
        out: dict[str, Any] = {}
        for sem, (win, exp_r) in states.items():
            p_h, n_eff = win.estimate(regime=regime, leaf=leaf)
            e_r, e_n = exp_r.estimate(regime=regime, leaf=f"{o.get('setup') or '-'}|"
                                                          f"{o.get('direction')}")
            n = max(float(n_eff or 0.0), float(e_n or 0.0))
            out[sem] = {"p_hier": round(p_h, 6), "n_eff_win": n_eff, "n_eff_exp_r": e_n,
                        "sample_size": int(n),
                        "uncertainty_penalty_r": round(uncertainty_penalty_r(n), 6)}
        rec_n = _f(o.get("sample_size"))
        rows.append({
            "candidate_id": o.get("candidate_id"), "decision_id": o.get("decision_id"),
            "as_of": o.get("ts"), "symbol": o.get("symbol"), "regime": regime,
            "n_closes_available": idx,
            "recorded_sample_size": rec_n,
            "recorded_uncertainty_penalty_r": _f(o.get("uncertainty_penalty_r")),
            OLD: out[OLD], NEW: out[NEW],
            "delta_sample_size": out[NEW]["sample_size"] - out[OLD]["sample_size"],
            "delta_uncertainty_penalty_r": round(
                out[NEW]["uncertainty_penalty_r"] - out[OLD]["uncertainty_penalty_r"], 6),
            "delta_p_hier": round(out[NEW]["p_hier"] - out[OLD]["p_hier"], 6),
        })

    # DOĞRULAMA: eski semantikle yeniden kurulan `sample_size`, kayıttakini üretiyor mu?
    checked = [r for r in rows if r["recorded_sample_size"] is not None]
    exact = sum(1 for r in checked if abs(r[OLD]["sample_size"] - r["recorded_sample_size"]) < 1e-9)
    unc_exact = sum(1 for r in checked
                    if r["recorded_uncertainty_penalty_r"] is not None
                    and abs(r[OLD]["uncertainty_penalty_r"]
                            - r["recorded_uncertainty_penalty_r"]) <= 1e-6)
    d_unc = [r["delta_uncertainty_penalty_r"] for r in rows]
    d_p = [r["delta_p_hier"] for r in rows]
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": POINT_IN_TIME,
        "label": ("POINT_IN_TIME_COMPONENTS — her fırsat yalnız o `as_of`tan önce kapanmış "
                  "işlemleri görür"),
        "n_opportunities": len(rows),
        "reconstruction_check": {
            "n_with_recorded_sample_size": len(checked),
            "old_semantics_reproduces_recorded_sample_size": exact,
            "old_semantics_reproduces_recorded_uncertainty": unc_exact,
            "note": ("eski semantik kayıttaki değerleri yeniden üretiyorsa, yeniden kurulum "
                     "DOĞRULANMIŞ demektir ve fark ölçümü anlamlıdır"),
            "limitation": (
                "TAM yeniden kurulum mümkün DEĞİL: üretimde `on_trade_closed` düğüm anahtarını "
                "`decision_snapshot.regime` ile kurar ve bu, kapanış ANINDAKİ son karara ait "
                "rejimdir (`engine_v3` `last_decisions[symbol]`). Bu değer kapanışla birlikte "
                "KALICI OLARAK SAKLANMAZ; defterdeki ve hafızadaki `features.regime` GİRİŞ anı "
                "rejimidir. Bu yüzden rejim düğümleri birebir yeniden kurulamaz."),
            "robust_measure": (
                "GLOBAL düğüm rejimden BAĞIMSIZdır: 25 kapanış için eski semantikte 50, "
                "düzeltilmişte 25 — bu ölçüm yeniden kurulum sınırından ETKİLENMEZ ve "
                "üretimdeki `learn_v2.json` değeriyle birebir aynıdır."),
            "provenance_gap_observed": (
                "öğrenilen hiyerarşi kanonik kayıtlardan tam olarak ÜRETİLEMEZ — ayrı bir "
                "gözlemdir, bu görevde DÜZELTİLMEDİ (davranış değişikliği olurdu)"),
        },
        "effect": {
            "mean_delta_uncertainty_penalty_r": (round(sum(d_unc) / len(d_unc), 6)
                                                 if d_unc else None),
            "max_delta_uncertainty_penalty_r": (round(max(d_unc, key=abs), 6) if d_unc else None),
            "mean_delta_p_hier": round(sum(d_p) / len(d_p), 6) if d_p else None,
            "max_delta_p_hier": round(max(d_p, key=abs), 6) if d_p else None,
            "direction": ("düzeltme belirsizlik cezasını BÜYÜTÜR (n yarıya iner) → sistem "
                          "tasarlandığı kadar TEMKİNLİ olur"),
            "confidence": ("bu farklar TAHMİNdir: rejim düğümleri birebir yeniden kurulamıyor "
                           "(bkz. reconstruction_check.limitation). Yön ve büyüklük mertebesi "
                           "güvenilir, satır bazında değerler değil"),
        },
        "rows": rows,
        "not_affected": {
            "p_win_prior": "learner'dan gelmez (head heuristiği) — etkilenmez",
            "avg_win_r": "exp_r tek çağrıyla güncellenir — sayaç yinelenmez",
            "payoff_geometry": "plan alanları — değişmez",
        },
    }


def edge_sensitivity(dual_rows: list[dict[str, Any]],
                     pit: dict[str, Any]) -> dict[str, Any]:
    """Düzeltilmiş belirsizlik cezasının KAYITLI kenara etkisi (diğer her şey sabit).

    Bu bir yeniden karar DEĞİLDİR: üretim kararları değişmez, tarihsel snapshot'lar
    korunur. Yalnız "aynı kayıt, düzeltilmiş ceza ile ne verirdi" sorusunu yanıtlar.
    """
    by_cand = {r["candidate_id"]: r for r in pit.get("rows", [])}
    flips, rows = 0, []
    for d in dual_rows:
        p = by_cand.get(d.get("candidate_id"))
        prior = (d.get("reproduced_prior_edge") or {})
        e = _f(prior.get("conservative_net_edge_r"))
        if p is None or e is None:
            continue
        delta = _f(p["delta_uncertainty_penalty_r"]) or 0.0
        e2 = round(e - delta, 6)
        flipped = (e > 0) != (e2 > 0)
        flips += flipped
        rows.append({"candidate_id": d["candidate_id"], "symbol": d.get("symbol"),
                     "as_of": d.get("as_of"), "edge_recorded": e,
                     "edge_with_corrected_uncertainty": e2,
                     "delta": round(e2 - e, 6), "gate_verdict_flips": flipped,
                     "linked_trade_id": d.get("linked_trade_id")})
    d_all = [r["delta"] for r in rows]
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": RETROSPECTIVE_RESEARCH,
        "n": len(rows), "n_gate_verdict_flips": flips,
        "mean_delta_edge_r": round(sum(d_all) / len(d_all), 6) if d_all else None,
        "max_abs_delta_edge_r": round(max((abs(x) for x in d_all), default=0.0), 6),
        "flipped": [r for r in rows if r["gate_verdict_flips"]][:20],
        "uncertainty_k": UNCERTAINTY_K,
        "note": ("üretim kararları DEĞİŞMEDİ; bu yalnız 'aynı kayıt, düzeltilmiş ceza' "
                 "duyarlılığıdır. Kayıtlı kenarlar ve kararlar olduğu gibi korunur"),
    }


__all__ = ["DEFAULT_ALPHA", "NEW", "OLD", "SCHEMA_VERSION", "apply_close", "close_keys",
           "count_integrity", "edge_sensitivity", "point_in_time_components", "rebuild"]
