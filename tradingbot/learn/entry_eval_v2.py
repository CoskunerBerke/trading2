"""F/G ailelerinin sonuç atfı ve terfi kapıları (`entry_eval_v2`) — SHADOW, `applied=False`.

`entry_eval` (V1) altyapısını YENİDEN YAZMAZ; istatistik, bootstrap, walk-forward ve sızıntı
denetimi oradan ÖDÜNÇ ALINIR. Buradaki fark, kararın üç durumlu olmasıdır
(`ALLOW`/`BLOCK`/`ABSTAIN`/`UNKNOWN`) ve her ailenin BİRDEN ÇOK yapılandırma varyantıyla
aynı anda ölçülmesidir.

**Kapılar V1 ile AYNI ya da DAHA SIKI.** Yeni aileler için iki ek kapı vardır:

* `WEEKLY_DATA_COVERAGE` — kapanışların yeterli bir oranında haftalık yapı GERÇEKTEN ölçülmüş
  olmalı; `UNKNOWN` üzerinden terfi edilemez.
* `ABSTAIN_RATE_ACCEPTABLE` — aile örneklemin çoğunda karar veremiyorsa, kalan azınlıktaki
  performansı terfi kanıtı sayılamaz.

Hiçbir eşik gevşetilmemiştir; `auto_promotion` hâlâ imkânsızdır.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from ..core import iso, stable_id, utc_now
from .entry_eval import (ELIGIBLE_FOR_PAPER_BOUNDED, GATE_MAX_SYMBOL_SHARE, GATE_MIN_DAYS,
                         GATE_MIN_LINKED_CLOSES, GATE_MIN_PER_STRATUM,
                         INSUFFICIENT_ENTRY_SAMPLE, NO_OUTCOME, NO_SNAPSHOT, OK,
                         _concentration, _cost_r, _observation_days, _risk_usdt, _stats,
                         bootstrap_ci, expanding_payoff, leakage_report, walk_forward_folds)
from .entry_challenger_v2 import (ABSTAIN, ALLOW, BLOCK, FAMILIES_V2, UNKNOWN,
                                  WeeklyChallengerConfig, build_variants, evaluate_v2)
from .entry_snapshot import LEGACY_MEMORY, LINKED

SCHEMA_VERSION = "entry_eval_v2"

#: Ek kapılar — V1'e GÖRE DAHA SIKI, hiçbiri gevşetme değildir.
GATE_MIN_WEEKLY_COVERAGE = 0.60      # kapanışların en az %60'ında haftalık yapı ölçülmüş olmalı
GATE_MAX_ABSTAIN_RATE = 0.50         # aile örneklemin yarısından fazlasında kararsızsa kanıt yok


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def outcome_id_v2(close_event_id: Any, candidate_id: Any, family: Any, variant: Any) -> str:
    """Deterministik değerlendirme kimliği — aynı kapanış+aday+aile+varyant DAİMA aynı."""
    return stable_id("entryeval2", str(close_event_id), str(candidate_id), str(family),
                     str(variant))


def evaluate_trade_v2(*, snapshot: dict[str, Any], close: dict[str, Any],
                      cfg: WeeklyChallengerConfig,
                      realized_payoff: float | None = None) -> dict[str, Any]:
    """Tek kapanmış işlem için F/G karşı-olgusal sonucu (tek varyant).

    `BLOCK` → işlem AÇILMAMIŞ sayılır (karşı-olgusal R = 0). `ALLOW`/`ABSTAIN`/`UNKNOWN` →
    işlem olduğu gibi kalır. Kararsızlık bir engelleme DEĞİLDİR ve öyle sayılmaz.
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    weekly = snap.get("weekly_structure") if isinstance(snap.get("weekly_structure"), dict) else None
    candle = snap.get("candle_context") if isinstance(snap.get("candle_context"), dict) else None
    cev = str(close.get("close_event_id") or "")
    cand = str(snap.get("candidate_id") or "")
    link = str(snap.get("link_status") or LINKED)
    r = _f(close.get("r_multiple"))
    pnl = _f(close.get("net_pnl"))
    risk = _risk_usdt(close)
    cr = _cost_r(close, risk)

    base = {
        "schema_version": SCHEMA_VERSION,
        "variant": cfg.variant,
        "config_id": cfg.config_id,
        "policy_version": cfg.policy_version,
        "close_event_id": cev or None,
        "trade_id": str(close.get("trade_id") or "") or None,
        "candidate_id": cand or None,
        "symbol": close.get("symbol") or snap.get("symbol"),
        "direction": snap.get("direction") or close.get("side"),
        "regime": snap.get("regime"),
        "closed_at": close.get("closed_at"),
        "opened_at": close.get("opened_at"),
        "exit_reason": close.get("exit_reason"),
        "link_status": link,
        "evidence_grade": ("PROMOTION" if link == LINKED else "OBSERVATION_ONLY"),
        "actual_r": r, "actual_net_pnl": pnl,
        "initial_risk_usdt": (round(risk, 6) if risk is not None else None),
        "cost_r": (round(cr, 6) if cr is not None else None),
        "fees": _f(close.get("fees")), "funding": _f(close.get("funding")),
        "mfe_r": _f(close.get("mfe_r")), "mae_r": _f(close.get("mae_r")),
        "weekly_available": bool((weekly or {}).get("week_available")),
        "weekly_data_quality": (weekly or {}).get("data_quality"),
        "previous_week_id": (weekly or {}).get("previous_week_id"),
        "applied": False,
    }
    if not cand:
        base.update({"status": NO_SNAPSHOT, "families": {}})
        return base
    if r is None:
        base.update({"status": NO_OUTCOME, "families": {}})
        return base

    verdicts = evaluate_v2(snap, weekly, cfg, candle=candle)
    fams: dict[str, Any] = {}
    for fam, v in verdicts.items():
        d = v.get("decision")
        blocked = (d == BLOCK)
        cf_r = 0.0 if blocked else r
        fams[fam] = {
            "decision": d,
            "reason_codes": v.get("reason_codes"),
            "blockers": v.get("blockers"),
            "confidence": v.get("confidence"),
            "evidence": v.get("evidence"),
            "blocked": blocked,
            "decided": d in (ALLOW, BLOCK),
            "abstained": d in (ABSTAIN, UNKNOWN),
            "allowed_winner": bool(d == ALLOW and r > 0),
            "allowed_loser": bool(d == ALLOW and r < 0),
            "blocked_winner": bool(blocked and r > 0),
            "blocked_loser": bool(blocked and r < 0),
            "avoided_loss_r": round(-r, 6) if (blocked and r < 0) else 0.0,
            "missed_gain_r": round(r, 6) if (blocked and r > 0) else 0.0,
            "avoided_loss_usdt": (round(-pnl, 6) if (blocked and pnl is not None and pnl < 0)
                                  else 0.0),
            "missed_gain_usdt": (round(pnl, 6) if (blocked and pnl is not None and pnl > 0)
                                 else 0.0),
            "counterfactual_r": round(cf_r, 6),
            "delta_r": round(cf_r - r, 6),
            "avoided_cost_r": (round(cr, 6) if (blocked and cr is not None) else 0.0),
            "outcome_id": outcome_id_v2(cev, cand, fam, cfg.variant),
            "applied": False,
        }
    base.update({"status": OK, "families": fams})
    return base


def evaluate_closes_v2(*, closes: Iterable[dict[str, Any]],
                       snapshots: dict[str, dict[str, Any]], links: dict[str, str],
                       cfg: WeeklyChallengerConfig) -> list[dict[str, Any]]:
    """Kronolojik değerlendirme + tekilleştirme. Ödeme oranı GENİŞLEYEN pencereden gelir."""
    rows = [c for c in (closes or []) if isinstance(c, dict)]
    rows.sort(key=lambda c: (str(c.get("closed_at") or ""), str(c.get("trade_id") or "")))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    prior: list[float] = []
    for c in rows:
        tid = str(c.get("trade_id") or "")
        cid = links.get(tid) or ""
        snap = snapshots.get(cid) if cid else None
        ev = evaluate_trade_v2(snapshot=snap or {}, close=c, cfg=cfg,
                              realized_payoff=expanding_payoff(prior))
        key = f"{ev.get('close_event_id')}|{ev.get('candidate_id')}|{cfg.variant}"
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
        rv = _f(c.get("r_multiple"))
        if rv is not None:
            prior.append(rv)
    return out


def _family_report_v2(evs: list[dict[str, Any]], fam: str) -> dict[str, Any]:
    base_r = [_f(e.get("actual_r")) or 0.0 for e in evs]
    cf_r: list[float] = []
    n_block = n_allow = n_abstain = 0
    bl = bw = 0
    avoided = missed = avoided_usdt = missed_usdt = avoided_cost = 0.0
    kept: list[float] = []
    weekly_ok = 0
    for e in evs:
        f = (e.get("families") or {}).get(fam) or {}
        r = _f(e.get("actual_r")) or 0.0
        cf_r.append(_f(f.get("counterfactual_r")) or 0.0)
        weekly_ok += int(bool(e.get("weekly_available")))
        if f.get("blocked"):
            n_block += 1
            bl += int(bool(f.get("blocked_loser")))
            bw += int(bool(f.get("blocked_winner")))
            avoided += _f(f.get("avoided_loss_r")) or 0.0
            missed += _f(f.get("missed_gain_r")) or 0.0
            avoided_usdt += _f(f.get("avoided_loss_usdt")) or 0.0
            missed_usdt += _f(f.get("missed_gain_usdt")) or 0.0
            avoided_cost += _f(f.get("avoided_cost_r")) or 0.0
        else:
            kept.append(r)
            if f.get("decision") == ALLOW:
                n_allow += 1
            else:
                n_abstain += 1
    n = len(evs)
    n_loss = sum(1 for x in base_r if x < 0)
    n_win = sum(1 for x in base_r if x > 0)
    blr = (bl / n_loss) if n_loss else None
    mwr = (bw / n_win) if n_win else None
    j = (blr - mwr) if (blr is not None and mwr is not None) else None
    b_stats, c_stats, k_stats = _stats(base_r), _stats(cf_r), _stats(kept)
    deltas = [c - b for c, b in zip(cf_r, base_r)]
    pay = k_stats.get("payoff_ratio")
    wr = k_stats.get("win_rate")
    if pay is not None and pay > 0:
        breakeven = 1.0 / (1.0 + pay)
    elif k_stats.get("n") and k_stats.get("profit_factor_state") == "no_losses":
        breakeven = 0.0
    else:
        breakeven = None
    return {
        "family": fam, "n_evaluated": n,
        "n_allow": n_allow, "n_block": n_block, "n_abstain": n_abstain,
        "abstain_rate": (round(n_abstain / n, 6) if n else None),
        "block_rate": (round(n_block / n, 6) if n else None),
        "weekly_coverage": (round(weekly_ok / n, 6) if n else None),
        "n_blocked_loser": bl, "n_blocked_winner": bw,
        "blocked_loser_rate": (round(blr, 6) if blr is not None else None),
        "missed_winner_rate": (round(mwr, 6) if mwr is not None else None),
        "discrimination_youden_j": (round(j, 6) if j is not None else None),
        "avoided_loss_r": round(avoided, 6), "missed_gain_r": round(missed, 6),
        "avoided_loss_usdt": round(avoided_usdt, 6),
        "missed_gain_usdt": round(missed_usdt, 6),
        "avoided_cost_r": round(avoided_cost, 6),
        "baseline": b_stats, "counterfactual": c_stats, "survivors": k_stats,
        "survivor_breakeven_p": (round(breakeven, 6) if breakeven is not None else None),
        "survivors_above_breakeven": (bool(wr > breakeven)
                                      if (wr is not None and breakeven is not None) else None),
        "delta_expectancy_r": (
            round((c_stats["expectancy_r"] or 0.0) - (b_stats["expectancy_r"] or 0.0), 6)
            if (c_stats["expectancy_r"] is not None and b_stats["expectancy_r"] is not None)
            else None),
        "delta_ci": bootstrap_ci(deltas),
        "applied": False,
    }


def _gates_v2(rep: dict[str, Any], *, n_linked: int, days: float | None,
              dir_counts: dict[str, int], regime_counts: dict[str, int],
              top_symbol_share: float | None, folds: dict[str, Any],
              leak: dict[str, Any]) -> list[dict[str, Any]]:
    """V1 kapılarının TAMAMI + iki ek kapı. Hiçbiri gevşetilmemiştir."""
    b, c = rep.get("baseline") or {}, rep.get("counterfactual") or {}
    ci = rep.get("delta_ci") or {}
    cov_dirs = [k for k, v in dir_counts.items() if v >= GATE_MIN_PER_STRATUM]
    cov_reg = [k for k, v in regime_counts.items() if v >= GATE_MIN_PER_STRATUM]
    dexp = rep.get("delta_expectancy_r")
    pf_b, pf_c = b.get("profit_factor"), c.get("profit_factor")
    pf_improved = ((pf_b is not None and pf_c is not None and pf_c > pf_b)
                   or (pf_b is not None and pf_c is None
                       and c.get("profit_factor_state") == "no_losses"
                       and (c.get("total_r") or 0.0) > 0.0))
    dd_b, dd_c = b.get("max_drawdown_r"), c.get("max_drawdown_r")
    cv_b, cv_c = b.get("tail_loss_r_cvar5"), c.get("tail_loss_r_cvar5")
    jj = rep.get("discrimination_youden_j")
    cov = rep.get("weekly_coverage")
    ab = rep.get("abstain_rate")

    def g(code: str, passed: Any, detail: str) -> dict[str, Any]:
        return {"code": code, "passed": bool(passed), "detail": detail}

    return [
        g("MIN_LINKED_CLOSES", n_linked >= GATE_MIN_LINKED_CLOSES,
          f"{n_linked}/{GATE_MIN_LINKED_CLOSES} (yalnız {LINKED}; {LEGACY_MEMORY} sayılmaz)"),
        g("MIN_OBSERVATION_DAYS", days is not None and days >= GATE_MIN_DAYS,
          f"{days}/{GATE_MIN_DAYS}" if days is not None else "ölçülemedi"),
        g("DIRECTION_COVERAGE", len(cov_dirs) >= 2,
          f"≥{GATE_MIN_PER_STRATUM} kapanışlı yön: {', '.join(cov_dirs) or 'yok'}"),
        g("REGIME_COVERAGE", len(cov_reg) >= 2,
          f"≥{GATE_MIN_PER_STRATUM} kapanışlı rejim: {', '.join(cov_reg) or 'yok'}"),
        g("POSITIVE_EXPECTANCY_IMPROVEMENT", dexp is not None and dexp > 0.0,
          f"Δbeklenti {dexp}" if dexp is not None else "ölçülemedi"),
        g("WALK_FORWARD_CONSISTENCY", folds.get("all_folds_positive"),
          (f"{folds.get('n_positive')}/{folds.get('k')} kat pozitif"
           if folds.get("state") == "ok" else "yetersiz örnek")),
        g("CONFIDENCE_INTERVAL_EXCLUDES_ZERO", ci.get("excludes_zero"),
          f"[{ci.get('lo')}, {ci.get('hi')}] ({ci.get('state')})"),
        g("PROFIT_FACTOR_IMPROVEMENT", pf_improved,
          f"{b.get('profit_factor_state')} → {c.get('profit_factor_state')} (PF {pf_b} → {pf_c})"),
        g("DRAWDOWN_NOT_WORSE", dd_b is not None and dd_c is not None and dd_c >= dd_b,
          f"{dd_b} → {dd_c}" if (dd_b is not None and dd_c is not None) else "ölçülemedi"),
        g("TAIL_RISK_NOT_WORSE", cv_b is not None and cv_c is not None and cv_c >= cv_b,
          f"CVaR5 {cv_b} → {cv_c}" if (cv_b is not None and cv_c is not None) else "ölçülemedi"),
        g("DISCRIMINATION_POSITIVE", jj is not None and jj > 0.0,
          f"engellenen kaybeden − kaçırılan kazanan = {jj}" if jj is not None else "ölçülemedi"),
        g("SURVIVORS_ABOVE_BREAKEVEN", rep.get("survivors_above_breakeven"),
          f"kazanma {(rep.get('survivors') or {}).get('win_rate')} vs kırılma "
          f"{rep.get('survivor_breakeven_p')}"),
        g("SYMBOL_CONCENTRATION",
          top_symbol_share is not None and top_symbol_share <= GATE_MAX_SYMBOL_SHARE,
          (f"en yoğun sembol payı {top_symbol_share:.2f}" if top_symbol_share is not None
           else "ölçülemedi")),
        g("NO_LEAKAGE_POINT_IN_TIME", leak.get("clean"),
          f"{leak.get('state')}; denetlenen {leak.get('checked')}"),
        # --- V2'ye ÖZGÜ EK KAPILAR (daha sıkı) --------------------------------------------
        g("WEEKLY_DATA_COVERAGE", cov is not None and cov >= GATE_MIN_WEEKLY_COVERAGE,
          (f"haftalık yapı ölçülen oran {cov} (asgari {GATE_MIN_WEEKLY_COVERAGE})"
           if cov is not None else "ölçülemedi")),
        g("ABSTAIN_RATE_ACCEPTABLE", ab is not None and ab <= GATE_MAX_ABSTAIN_RATE,
          (f"kararsızlık oranı {ab} (tavan {GATE_MAX_ABSTAIN_RATE})"
           if ab is not None else "ölçülemedi")),
    ]


def build_report_v2(*, closes: Iterable[dict[str, Any]],
                    snapshots: dict[str, dict[str, Any]], links: dict[str, str],
                    base_policy: dict[str, Any] | None = None, now=None) -> dict[str, Any]:
    """Bütün varyantlar × iki aile için tam rapor + terfi kapıları."""
    variants = build_variants(base_policy)
    out_variants: dict[str, Any] = {}
    eligible: list[str] = []
    n_linked = 0
    days = None
    conc: dict[str, Any] = {}
    for cfg in variants:
        evs = evaluate_closes_v2(closes=closes, snapshots=snapshots, links=links, cfg=cfg)
        ok = [e for e in evs if e.get("status") == OK]
        linked = [e for e in ok if e.get("link_status") == LINKED]
        legacy = [e for e in ok if e.get("link_status") == LEGACY_MEMORY]
        n_linked = len(linked)
        days = _observation_days(linked)
        sym_c = _concentration(linked, "symbol")
        dir_c = _concentration(linked, "direction")
        reg_c = _concentration(linked, "regime")
        top = (max(sym_c.values()) / n_linked) if (n_linked and sym_c) else None
        conc = {"symbol": sym_c, "direction": dir_c, "regime": reg_c,
                "top_symbol_share": (round(top, 4) if top is not None else None)}
        leak = leakage_report(linked, snapshots)
        fam_out: dict[str, Any] = {}
        for fam in FAMILIES_V2:
            rep = _family_report_v2(linked, fam)
            rep["walk_forward_folds"] = walk_forward_folds(linked, fam) if linked else {
                "state": "insufficient_sample", "k": 3, "n": 0, "folds": [],
                "all_folds_positive": False, "n_positive": 0}
            rep["observation_only"] = (_family_report_v2(legacy, fam) if legacy else None)
            gates = _gates_v2(rep, n_linked=n_linked, days=days, dir_counts=dir_c,
                              regime_counts=reg_c, top_symbol_share=top,
                              folds=rep["walk_forward_folds"], leak=leak)
            rep["gates"] = gates
            rep["gates_passed"] = sum(1 for x in gates if x["passed"])
            rep["gates_total"] = len(gates)
            rep["verdict"] = (ELIGIBLE_FOR_PAPER_BOUNDED if all(x["passed"] for x in gates)
                              else INSUFFICIENT_ENTRY_SAMPLE)
            if rep["verdict"] == ELIGIBLE_FOR_PAPER_BOUNDED:
                eligible.append(f"{cfg.variant}:{fam}")
            fam_out[fam] = rep
        out_variants[cfg.variant] = {
            "variant": cfg.variant, "config_id": cfg.config_id,
            "policy_version": cfg.policy_version, "policy": cfg.to_dict(),
            "n_evaluated": len(evs), "n_linked": n_linked, "n_legacy_memory": len(legacy),
            "n_no_snapshot": sum(1 for e in evs if e.get("status") == NO_SNAPSHOT),
            "leakage": leak, "families": fam_out,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now or utc_now()),
        "families": list(FAMILIES_V2),
        "n_variants": len(variants),
        "n_linked": n_linked,
        "observation_days": days,
        "concentration": conc,
        "variants": out_variants,
        "eligible": sorted(eligible),
        "verdict": (ELIGIBLE_FOR_PAPER_BOUNDED if eligible else INSUFFICIENT_ENTRY_SAMPLE),
        "auto_promotion": False,
        "applied_total": 0,
        "extra_gates": {"WEEKLY_DATA_COVERAGE": GATE_MIN_WEEKLY_COVERAGE,
                        "ABSTAIN_RATE_ACCEPTABLE": GATE_MAX_ABSTAIN_RATE},
        "note_tr": ("SHADOW: F ve G aileleri aktif giriş kararını ETKİLEMEZ. Kapılar V1 ile "
                    "aynı ya da daha sıkıdır; hiçbiri gevşetilmemiştir. Varyantlar sonuçlara "
                    "bakılarak SEÇİLMEZ, hepsi ayrı ayrı raporlanır."),
    }


__all__ = ["SCHEMA_VERSION", "GATE_MIN_WEEKLY_COVERAGE", "GATE_MAX_ABSTAIN_RATE",
           "outcome_id_v2", "evaluate_trade_v2", "evaluate_closes_v2", "build_report_v2"]
